"""Generative / completion engine — Steps 6-8 (Build Order Phases 7-8).

The GPU side of the gate's three-way routing (see confidence.route). Two
capabilities behind ONE pluggable interface, one per band that needs invented
geometry:

  complete(mesh, cloud, crop_path, coco_class) -> open3d mesh | None
      MIDDLE band ("completion"): KEEP the real partial TSDF mesh but fill the
      parts the camera never saw (the back, an unseen side).

  regenerate(cloud, crop_path, coco_class) -> RegenResult | None
      BOTTOM band ("generative"): build the WHOLE mesh from the crop image and
      recover its real-world scale/pose from the observed cloud (it comes out of
      the model in a unit cube). Carries the ICP provenance for scene.json.

Engines (swap by config; nothing else in the pipeline changes):
  * LocalEngine  — NO GPU. complete() = the Poisson watertight repair we already
    use (scene.mass); regenerate() = None, so bottom-band objects are dropped —
    exactly today's "generative deferred" behaviour. This is the default, so the
    three-way gate runs end-to-end locally right now (middle band = Poisson fill).
  * RunPodEngine — calls a RunPod serverless endpoint (TripoSG / Hunyuan3D). The
    HTTP/auth/runsync plumbing is wired; the two seams that depend on the
    endpoint's own input/output contract (`_build_input`, `_decode_mesh`) raise
    until that contract is provided — fill them in when the API is handed over.

make_engine() picks RunPodEngine when RUNPOD_API_KEY + RUNPOD_ENDPOINT_ID are in
the environment, else LocalEngine — so turning the GPU path on is purely config.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class RegenResult:
    """A regenerated (bottom-band) object: the mesh plus the provenance the
    assembler must stamp into scene.json source.* (a generative object reports
    REAL alignment/scale methods, unlike a tsdf object's n/a — fix Y1)."""
    mesh: object                       # open3d.geometry.TriangleMesh, world-scaled
    alignment_method: str = "fpfh_icp"  # 'fpfh_icp' | 'coarse_aligned'
    scale_method: str = "per_axis_median"  # 'per_axis_median' | 'class_prior'


def _transfer_colors(src_mesh, dst_mesh):
    """Copy vertex colours from src_mesh onto dst_mesh by nearest neighbour.

    A completion model emits geometry only (no colour), so a freshly-meshed
    completion renders flat/dark. We paint each completed vertex with the colour
    of the closest observed (src) vertex, so it keeps the scan's appearance. Both
    meshes are in the same object-local frame. No-op if src has no colours.
    """
    import numpy as np
    import open3d as o3d

    if not src_mesh.has_vertex_colors() or len(dst_mesh.vertices) == 0:
        return dst_mesh
    src_cols = np.asarray(src_mesh.vertex_colors)
    tree = o3d.geometry.KDTreeFlann(src_mesh)
    dst_v = np.asarray(dst_mesh.vertices)
    cols = np.empty((len(dst_v), 3))
    for i, p in enumerate(dst_v):
        _, idx, _ = tree.search_knn_vector_3d(p, 1)
        cols[i] = src_cols[idx[0]]
    dst_mesh.vertex_colors = o3d.utility.Vector3dVector(cols)
    return dst_mesh


# Canonical real-world size (metres) of the LARGEST object dimension, per COCO
# class. A generated mesh arrives in a unit cube with NO real size, and every
# object in the generative band is poorly observed (that is WHY it routed here),
# so the observed cloud's extent is an unreliable partial fragment and cannot set
# the size. Instead we scale each mesh to a fixed physical size for its class so
# masses are consistent and plausible; the cloud is used only for placement.
# Value = the object's biggest side in metres (chair/table ~ height or length).
CLASS_SIZE_PRIOR_M = {
    "chair": 0.90,          # seat-back height
    "couch": 2.00,          # length
    "sofa": 2.00,
    "bench": 1.50,
    "dining table": 1.40,   # length
    "table": 1.20,
    "desk": 1.40,
    "bed": 2.00,
    "tv": 1.00,
    "laptop": 0.35,
    "bottle": 0.25,         # height
    "cup": 0.12,
    "bowl": 0.18,
    "vase": 0.30,
    "potted plant": 0.45,
    "book": 0.25,
}
DEFAULT_SIZE_PRIOR_M = 0.60  # unknown class -> a modest object


def coarse_align_to_cloud(mesh, cloud, coco_class: str | None = None):
    """Scale a unit-cube GENERATED mesh to a real size and place it on the cloud.

    A generative model returns its mesh in a unit cube with no real size. These
    objects are ALL poorly observed (that is why they routed generative), so the
    observed cloud is a partial fragment whose extent cannot be trusted for size
    (fragment-fitting gave masses of 0.1-25 kg for the same chairs). So SIZE comes
    from a per-class real-world prior (CLASS_SIZE_PRIOR_M) — a uniform scale that
    preserves the model's proportions — and the cloud sets only PLACEMENT (its
    centroid). This is the "coarse_aligned" / "class_prior" path; precise
    FPFH-rotation + ICP is Phase 8 (icp_align.py). CPU-only, runs without a GPU.
    """
    import numpy as np
    import open3d as o3d

    cloud = np.asarray(cloud, dtype=np.float64)
    if len(mesh.vertices) == 0:
        return mesh

    out = o3d.geometry.TriangleMesh(mesh)  # copy
    ab = out.get_axis_aligned_bounding_box()
    m_lo, m_hi = np.asarray(ab.min_bound), np.asarray(ab.max_bound)
    m_size, m_centre = m_hi - m_lo, (m_lo + m_hi) / 2.0
    m_max = float(m_size.max())
    if m_max <= 1e-9:
        return mesh

    # SIZE: class prior / model's largest extent -> uniform scale (keeps shape).
    target = CLASS_SIZE_PRIOR_M.get((coco_class or "").lower(), DEFAULT_SIZE_PRIOR_M)
    s = target / m_max

    # PLACEMENT: centre on the observed cloud so the object lands where it was
    # seen (horizontal); the assembler's ground pass fixes the vertical drop.
    # Fall back to the mesh's own centre if the cloud is empty/degenerate.
    if len(cloud) >= 1:
        c_centre = (cloud.min(axis=0) + cloud.max(axis=0)) / 2.0
    else:
        c_centre = m_centre

    v = (np.asarray(out.vertices) - m_centre) * s + c_centre
    out.vertices = o3d.utility.Vector3dVector(v)
    out.compute_vertex_normals()
    return out


class Engine:
    """Base interface. Both methods return None when this engine cannot produce
    geometry for that object (the caller then falls back: completion -> local
    repair; regeneration -> drop the object)."""

    def complete(self, *, mesh, cloud, crop_path, coco_class):  # -> mesh | None
        raise NotImplementedError

    def regenerate(self, *, cloud, crop_path, coco_class):  # -> RegenResult | None
        raise NotImplementedError


class LocalEngine(Engine):
    """No-GPU default. Completion = Poisson watertight repair; no regeneration."""

    def complete(self, *, mesh, cloud, crop_path, coco_class):
        # The same Step-7b repair the assembler already trusts: close the open
        # shell into a (mostly) watertight mesh. Returns None on failure so the
        # caller falls back to the raw shell.
        from scene import mass
        try:
            repaired, _vol, _wt = mass.finalize_mesh(mesh)
            return repaired
        except Exception:
            return None

    def regenerate(self, *, cloud, crop_path, coco_class):
        # No GPU -> the whole object cannot be invented. Dropped upstream.
        return None


class RunPodEngine(Engine):
    """RunPod serverless backend (TripoSG / Hunyuan3D).

    TWO models / TWO endpoints (the bands need different inputs):
      * completion_endpoint — the MIDDLE band's learned shape-completion model
        (PoinTr-family). GEOMETRY-conditioned: it is fed the PARTIAL scan (mesh /
        point cloud) and extends it; it keeps the real shape and invents less.
      * gen_endpoint — the BOTTOM band's image-to-3D model (TripoSG / Hunyuan3D).
        IMAGE-conditioned: fed the crop, it builds the whole object from scratch.
    Either may be absent: complete()/regenerate() return None when their endpoint
    is unset, so the caller falls back (completion -> local Poisson; regen -> drop).

    The transport (POST /v2/{endpoint}/runsync with a Bearer key) is generic and
    done here. The endpoint-specific seams — how an object is encoded into the
    handler's `input` and how the returned mesh is decoded — are isolated in
    `_build_input` / `_decode_mesh`; fill them in once the contract is known.
    """

    BASE_URL = "https://api.runpod.ai/v2"

    def __init__(
        self,
        api_key: str,
        *,
        gen_endpoint: str | None = None,
        completion_endpoint: str | None = None,
        gen_model: str = "triposg",
        completion_model: str = "pointr",
        timeout_s: float = 600.0,
    ):
        self.api_key = api_key
        self.gen_endpoint = gen_endpoint
        self.completion_endpoint = completion_endpoint
        self.gen_model = gen_model
        self.completion_model = completion_model
        self.timeout_s = timeout_s

    # --- transport (generic RunPod serverless runsync) ------------------
    def _runsync(self, endpoint: str, payload: dict) -> dict:
        """POST {"input": payload} to `endpoint`, return the `output` dict.

        Synchronous RunPod call: blocks until the job finishes. Raises on a
        non-200 status or a RunPod-level error field.
        """
        import json
        import urllib.request

        url = f"{self.BASE_URL}/{endpoint}/runsync"
        body = json.dumps({"input": payload}).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            data = json.loads(resp.read().decode())
        if data.get("status") == "FAILED" or "error" in data:
            raise RuntimeError(f"RunPod job failed: {data.get('error') or data}")
        return data.get("output", {})

    # --- endpoint-specific seams (FILL IN with the API contract) --------
    def _build_input(self, *, mode: str, model: str, crop_path, coco_class, cloud,
                     mesh=None) -> dict:
        """Build the handler `input` dict for this object.

        mode "complete"   -> the learned shape-completion model: send the PARTIAL
                             GEOMETRY (the observed point `cloud`, and/or `mesh`),
                             plus the class. The crop is optional context.
        mode "regenerate" -> the image-to-3D model: send the crop image (base64)
                             + class; no geometry.
        Field names are the endpoint's contract — fill them in.
        """
        raise NotImplementedError(
            "RunPod input contract not set — provide the endpoint's `input` schema"
        )

    def _decode_mesh(self, output: dict):
        """Decode the handler `output` into an open3d TriangleMesh.

        Decide the transfer format (obj/ply/glb, base64 or URL). NOTE: Open3D
        cannot read GLB back — prefer OBJ/PLY for a server round-trip (gotcha).
        """
        raise NotImplementedError(
            "RunPod output contract not set — provide the returned mesh format"
        )

    # --- capabilities ---------------------------------------------------
    def complete(self, *, mesh, cloud, crop_path, coco_class):
        # MIDDLE band: learned shape-completion (geometry-conditioned). No
        # completion endpoint configured -> None so the caller falls back to the
        # local Poisson repair.
        if not self.completion_endpoint:
            return None
        payload = self._build_input(
            mode="complete", model=self.completion_model, crop_path=crop_path,
            coco_class=coco_class, cloud=cloud, mesh=mesh)
        return self._decode_mesh(self._runsync(self.completion_endpoint, payload))

    def regenerate(self, *, cloud, crop_path, coco_class):
        # BOTTOM band: image-to-3D (image-conditioned). No gen endpoint -> None
        # so the object is dropped (as with no GPU at all).
        if not self.gen_endpoint:
            return None
        payload = self._build_input(
            mode="regenerate", model=self.gen_model, crop_path=crop_path,
            coco_class=coco_class, cloud=cloud)
        gen_mesh = self._decode_mesh(self._runsync(self.gen_endpoint, payload))
        # The model returns a unit-cube mesh; scale it to a class-size prior and
        # place it on the observed cloud (precise FPFH+ICP is Phase 8).
        aligned = coarse_align_to_cloud(gen_mesh, cloud, coco_class)
        return RegenResult(mesh=aligned, alignment_method="coarse_aligned",
                           scale_method="class_prior")


class LocalGpuEngine(Engine):
    """Run the SAME two models on the LOCAL GPU instead of RunPod — no API, no
    network, no cost. Same contract as RunPodEngine:
      complete()   -> learned shape-completion model (PoinTr-family), GPU
      regenerate() -> image-to-3D model (TripoSG / Hunyuan3D), GPU, then the
                      coarse AABB align (CPU)

    Selection (make_engine) requires torch + a visible CUDA device. The
    model-specific load+inference is isolated in `_run_completion` / `_run_gen`,
    which import the model packages on demand and raise until those packages +
    weights are installed — at which point both bands go live with NO other
    change. A raise here is caught and turned into the engine's None fallback
    (completion -> local Poisson; generative -> drop), so a missing model never
    crashes the pipeline; it just logs and degrades.

    VRAM (this box = RTX 4060, 8 GB): PoinTr fits easily; TripoSG is tight (fp16 /
    offload); Hunyuan3D 2.1 likely will NOT fit at 8 GB — prefer TripoSG locally
    and keep Hunyuan3D for a larger GPU.
    """

    def __init__(self, *, completion_model: str = "pointr", gen_model: str = "triposg"):
        self.completion_model = completion_model
        self.gen_model = gen_model
        self._triposg = None  # cached (pipe, rmbg, prepare_image) — loaded once

    @staticmethod
    def is_available() -> bool:
        """True iff torch sees a CUDA device. Never raises (torch may be absent)."""
        try:
            import torch
            return bool(torch.cuda.is_available())
        except Exception:
            return False

    def complete(self, *, mesh, cloud, crop_path, coco_class):
        try:
            return self._run_completion(mesh=mesh, cloud=cloud, coco_class=coco_class)
        except Exception as e:  # missing model / OOM -> fall back to local Poisson
            log.warning("local-GPU completion unavailable (%s) -> Poisson fallback", e)
            return None

    def regenerate(self, *, cloud, crop_path, coco_class):
        try:
            gen_mesh = self._run_gen(crop_path=crop_path, coco_class=coco_class)
            aligned = coarse_align_to_cloud(gen_mesh, cloud, coco_class)
            return RegenResult(mesh=aligned, alignment_method="coarse_aligned",
                               scale_method="class_prior")
        except Exception as e:  # missing model / OOM -> drop (as with no GPU)
            log.warning("local-GPU generation unavailable (%s) -> object dropped", e)
            return None

    # --- model adapters (FILL with the model APIs once installed) -------
    def _run_completion(self, *, mesh, cloud, coco_class):
        """PoinTr shape-completion on the PARTIAL GEOMETRY -> open3d mesh.

        Feed the recentred TSDF mesh's vertices (object-local frame) to PoinTr,
        get a dense completed cloud back in the same frame, and Poisson-mesh it
        into a single coherent surface. Geometry-only (no image). Returns an
        open3d mesh; raising here -> the engine falls back to the local Poisson.
        """
        import numpy as np
        import open3d as o3d

        pts = np.asarray(mesh.vertices)
        if len(pts) < 32:
            raise RuntimeError("too few points to complete")

        if self.completion_model == "patchcomplete":
            # PatchComplete returns a 32^3 completed TSDF -> a MESH directly
            # (no point cloud, no Poisson). Same frame as the input vertices.
            from . import patchcomplete_completion
            m = patchcomplete_completion.complete_mesh(pts)
            if len(m.triangles) == 0:
                raise RuntimeError("PatchComplete produced no triangles")
            return _transfer_colors(mesh, m)

        if self.completion_model in ("pointr", "adapointr", "pointr_sn55"):
            from . import pointr_completion
            dense = pointr_completion.complete_points(pts, model=self.completion_model)
        elif self.completion_model == "compc":
            # ComPC runs in its own quarantined env (subprocess); it preserves the
            # observed geometry and hallucinates only the unseen regions.
            from . import compc_completion
            dense = compc_completion.complete_points(pts, coco_class=coco_class)
        else:
            raise NotImplementedError(
                f"local completion model '{self.completion_model}' not wired")

        pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(dense))
        pcd.estimate_normals(o3d.geometry.KDTreeSearchParamKNN(knn=20))
        pcd.orient_normals_consistent_tangent_plane(20)
        m, dens = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=7)
        m.remove_vertices_by_mask(np.asarray(dens) < np.quantile(dens, 0.05))
        m = m.crop(pcd.get_axis_aligned_bounding_box())
        m.compute_vertex_normals()
        if len(m.triangles) == 0:
            raise RuntimeError("completion meshing produced no triangles")
        return _transfer_colors(mesh, m)  # PoinTr emits no colour -> carry it over

    def _load_triposg(self):
        """Import + load the TripoSG pipeline and BriaRMBG once, caching them.

        The repo (cloned by deploy/runpod/setup_triposg.sh) exposes the `triposg`
        package at its root and `image_process` / `briarmbg` under `scripts/`, so
        both go on sys.path. Weights live under <home>/pretrained_weights by
        default (snapshot_download targets in the setup script). Overridable via
        VID2SIM_TRIPOSG_HOME / _WEIGHTS / RMBG_WEIGHTS.
        """
        if self._triposg is not None:
            return self._triposg
        import sys

        import torch

        home = os.environ.get("VID2SIM_TRIPOSG_HOME", "/workspace/TripoSG")
        for p in (home, os.path.join(home, "scripts")):
            if p not in sys.path:
                sys.path.insert(0, p)
        # TripoSG's inference_utils imports `diso` (a CUDA ext needing nvcc) at
        # module top-level, even though it's ONLY used by the flash decoder. With
        # use_flash_decoder=False we use marching cubes, so register a stub so the
        # import succeeds without nvcc. DiffDMC is never instantiated on this path.
        if "diso" not in sys.modules:
            try:
                import diso  # noqa: F401  (real build present, e.g. on a pod)
            except Exception:
                import types
                stub = types.ModuleType("diso")
                class _NoDiso:  # noqa: N801
                    def __init__(self, *a, **k):
                        raise RuntimeError("diso not built; use VID2SIM_TRIPOSG_FLASH=0")
                stub.DiffDMC = _NoDiso
                sys.modules["diso"] = stub
        from triposg.pipelines.pipeline_triposg import TripoSGPipeline
        from image_process import prepare_image
        from briarmbg import BriaRMBG

        weights = os.path.join(home, "pretrained_weights")
        tri_dir = os.environ.get("VID2SIM_TRIPOSG_WEIGHTS", os.path.join(weights, "TripoSG"))
        rmbg_dir = os.environ.get("VID2SIM_RMBG_WEIGHTS", os.path.join(weights, "RMBG-1.4"))
        pipe = TripoSGPipeline.from_pretrained(tri_dir).to("cuda", torch.float16)
        rmbg = BriaRMBG.from_pretrained(rmbg_dir).to("cuda")
        rmbg.eval()
        self._triposg = (pipe, rmbg, prepare_image)
        return self._triposg

    def _run_gen(self, *, crop_path, coco_class):
        """Run TripoSG (image-to-3D) on the crop -> open3d unit-cube mesh.

        Removes the crop's background (BriaRMBG), samples a mesh, and returns it
        in TripoSG's unit cube; coarse_align_to_cloud (caller) recovers real-world
        scale/pose from the observed cloud. Steps/CFG/seed via
        VID2SIM_TRIPOSG_STEPS / _CFG / _SEED. A raise here -> object dropped.
        """
        if crop_path is None:
            raise RuntimeError("no crop staged for this object")
        import numpy as np
        import open3d as o3d
        import torch

        pipe, rmbg, prepare_image = self._load_triposg()
        img = prepare_image(str(crop_path), bg_color=np.array([1.0, 1.0, 1.0]),
                            rmbg_net=rmbg)
        steps = int(os.environ.get("VID2SIM_TRIPOSG_STEPS", "50"))
        cfg = float(os.environ.get("VID2SIM_TRIPOSG_CFG", "7.0"))
        seed = int(os.environ.get("VID2SIM_TRIPOSG_SEED", "42"))
        # The flash decoder needs `diso` (a CUDA ext requiring nvcc). Default OFF so
        # TripoSG uses the marching-cubes extractor instead — no nvcc/diso needed.
        # Set VID2SIM_TRIPOSG_FLASH=1 on a pod that has diso built.
        use_flash = os.environ.get("VID2SIM_TRIPOSG_FLASH", "0") == "1"
        # Mesh-extraction resolution. The default (dense 8 / hierarchical 9) decodes
        # ~16M points through the VAE at once and OOMs an 8 GB GPU; 7/8 keeps it in
        # memory and is plenty for a physics object (we decimate to 40k after).
        # Raise on a big-VRAM host via VID2SIM_TRIPOSG_DENSE / _HIER.
        dense = int(os.environ.get("VID2SIM_TRIPOSG_DENSE", "7"))
        hier = int(os.environ.get("VID2SIM_TRIPOSG_HIER", "8"))
        with torch.no_grad():
            out = pipe(
                image=img,
                generator=torch.Generator(device=pipe.device).manual_seed(seed),
                num_inference_steps=steps, guidance_scale=cfg,
                use_flash_decoder=use_flash,
                dense_octree_depth=dense, hierarchical_octree_depth=hier,
            ).samples[0]
        verts = np.asarray(out[0], dtype=np.float64)
        faces = np.ascontiguousarray(np.asarray(out[1], dtype=np.int32))
        if len(verts) == 0 or len(faces) == 0:
            raise RuntimeError("TripoSG produced an empty mesh")
        m = o3d.geometry.TriangleMesh(
            o3d.utility.Vector3dVector(verts),
            o3d.utility.Vector3iVector(faces))
        m.remove_duplicated_vertices()
        m.remove_degenerate_triangles()
        # TripoSG extracts at 505^3 -> ~1-3M triangles, which chokes the
        # downstream Poisson finalize + CoACD decomposition. Decimate to a sane
        # budget here so assembly stays fast; 0 disables.
        budget = int(os.environ.get("VID2SIM_TRIPOSG_FACES", "40000"))
        if budget > 0 and len(m.triangles) > budget:
            m = m.simplify_quadric_decimation(budget)
        m.compute_vertex_normals()
        return m


def make_engine() -> Engine:
    """Pick the geometry-invention backend, in priority order:
      1. RunPodEngine  — if RUNPOD_API_KEY + an endpoint are set.
           RUNPOD_GEN_ENDPOINT_ID (alias RUNPOD_ENDPOINT_ID) — image-to-3D.
           RUNPOD_COMPLETION_ENDPOINT_ID                     — shape-completion.
      2. LocalGpuEngine — if a CUDA GPU is visible (and VID2SIM_LOCAL_GPU != "0").
      3. LocalEngine    — no GPU: completion = Poisson, generation = drop.
    Model overrides: RUNPOD_GEN_MODEL/RUNPOD_COMPLETION_MODEL (RunPod) or
    VID2SIM_GEN_MODEL/VID2SIM_COMPLETION_MODEL (local; default triposg/pointr)."""
    key = os.environ.get("RUNPOD_API_KEY")
    gen = os.environ.get("RUNPOD_GEN_ENDPOINT_ID") or os.environ.get("RUNPOD_ENDPOINT_ID")
    comp = os.environ.get("RUNPOD_COMPLETION_ENDPOINT_ID")
    if key and (gen or comp):
        return RunPodEngine(
            key, gen_endpoint=gen, completion_endpoint=comp,
            gen_model=os.environ.get("RUNPOD_GEN_MODEL", "triposg"),
            completion_model=os.environ.get("RUNPOD_COMPLETION_MODEL", "pointr"),
        )
    if os.environ.get("VID2SIM_LOCAL_GPU", "1") != "0" and LocalGpuEngine.is_available():
        return LocalGpuEngine(
            gen_model=os.environ.get("VID2SIM_GEN_MODEL", "triposg"),
            # PatchComplete is the verdict completion pick (real-scan robust, runs
            # locally); override with VID2SIM_COMPLETION_MODEL=pointr/compc/etc.
            completion_model=os.environ.get("VID2SIM_COMPLETION_MODEL", "patchcomplete"),
        )
    return LocalEngine()
