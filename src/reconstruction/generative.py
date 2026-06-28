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

import os
from dataclasses import dataclass


@dataclass
class RegenResult:
    """A regenerated (bottom-band) object: the mesh plus the provenance the
    assembler must stamp into scene.json source.* (a generative object reports
    REAL alignment/scale methods, unlike a tsdf object's n/a — fix Y1)."""
    mesh: object                       # open3d.geometry.TriangleMesh, world-scaled
    alignment_method: str = "fpfh_icp"  # 'fpfh_icp' | 'coarse_aligned'
    scale_method: str = "per_axis_median"  # 'per_axis_median' | 'class_prior'


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

    The transport (POST /v2/{endpoint}/runsync with a Bearer key) is generic and
    done here. The two endpoint-specific seams — how the object is encoded into
    the handler's `input` and how the returned mesh is decoded — are isolated in
    `_build_input` / `_decode_mesh`; fill them in once the endpoint contract is
    known. Until then they raise, and make_engine() only returns this when the
    env is configured, so the local path is unaffected.
    """

    BASE_URL = "https://api.runpod.ai/v2"

    def __init__(
        self,
        api_key: str,
        endpoint_id: str,
        *,
        model: str = "triposg",
        timeout_s: float = 600.0,
    ):
        self.api_key = api_key
        self.endpoint_id = endpoint_id
        self.model = model
        self.timeout_s = timeout_s

    # --- transport (generic RunPod serverless runsync) ------------------
    def _runsync(self, payload: dict) -> dict:
        """POST {"input": payload} to the endpoint, return the `output` dict.

        Synchronous RunPod call: blocks until the job finishes. Raises on a
        non-200 status or a RunPod-level error field.
        """
        import json
        import urllib.request

        url = f"{self.BASE_URL}/{self.endpoint_id}/runsync"
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
    def _build_input(self, *, mode: str, crop_path, coco_class, cloud) -> dict:
        """Build the handler `input` dict for this object.

        mode is "complete" or "regenerate". Will likely include the crop image
        (base64), the class name, the chosen model, and — for completion — the
        partial geometry. The exact field names are the endpoint's contract.
        """
        raise NotImplementedError(
            "RunPod input contract not set — provide the endpoint's `input` schema"
        )

    def _decode_mesh(self, output: dict):
        """Decode the handler `output` into an open3d TriangleMesh.

        The model returns a mesh (TripoSG/Hunyuan3D output); decide the transfer
        format (glb/obj/ply, base64 or URL). NOTE: Open3D cannot read GLB back —
        prefer OBJ/PLY for a server round-trip (see project gotchas).
        """
        raise NotImplementedError(
            "RunPod output contract not set — provide the returned mesh format"
        )

    # --- capabilities ---------------------------------------------------
    def complete(self, *, mesh, cloud, crop_path, coco_class):
        payload = self._build_input(
            mode="complete", crop_path=crop_path, coco_class=coco_class, cloud=cloud)
        return self._decode_mesh(self._runsync(payload))

    def regenerate(self, *, cloud, crop_path, coco_class):
        payload = self._build_input(
            mode="regenerate", crop_path=crop_path, coco_class=coco_class, cloud=cloud)
        gen_mesh = self._decode_mesh(self._runsync(payload))
        # Scale/place the unit-cube mesh against the observed cloud. The real
        # FPFH+ICP per-axis fit is Phase 8 (icp_align.py); until then a generative
        # object needs that step to become world-correct.
        aligned = self._align_to_cloud(gen_mesh, cloud)
        return RegenResult(mesh=aligned, alignment_method="coarse_aligned",
                           scale_method="class_prior")

    def _align_to_cloud(self, mesh, cloud):
        """Placeholder coarse alignment (AABB fit). Replaced by Phase-8 ICP."""
        raise NotImplementedError("ICP alignment is Phase 8 (icp_align.py)")


def make_engine() -> Engine:
    """LocalEngine, or RunPodEngine when RUNPOD_API_KEY + RUNPOD_ENDPOINT_ID are
    set. RUNPOD_MODEL overrides the model (default triposg)."""
    key = os.environ.get("RUNPOD_API_KEY")
    endpoint = os.environ.get("RUNPOD_ENDPOINT_ID")
    if key and endpoint:
        return RunPodEngine(key, endpoint, model=os.environ.get("RUNPOD_MODEL", "triposg"))
    return LocalEngine()
