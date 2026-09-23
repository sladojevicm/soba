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
import time
from dataclasses import dataclass

from reconstruction import runpod_policy

log = logging.getLogger(__name__)

# Observability hook (src/telemetry): run_assemble.py sets this to record the
# wall time of every RunPod call as `hook(endpoint_id, seconds)`. Called on
# success AND failure; None = no accounting.
remote_call_hook = None

# The seconds reported to the hook are BILLABLE seconds: RunPod's
# `executionTime` (ms) when the response carries it, else the request's wall
# time. Reported once per HTTP submission, retries included.

_sleep = time.sleep  # retry backoff; tests replace it

# Terminal RunPod job states (the rest — IN_QUEUE, IN_PROGRESS — are polled).
_TERMINAL_STATUS = frozenset({"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"})


class RunPodError(RuntimeError):
    """A RunPod call failed for good: a permanent failure, or retries exhausted.
    scripts/run_assemble.py turns this into a `runpod_failed` drop of the one
    object and carries on."""


class RunPodTransient(RunPodError):
    """Retryable: network error / socket timeout, HTTP 429 or 5xx, an
    IN_QUEUE / IN_PROGRESS stall, a non-terminal reply that cannot be polled."""


class RunPodJobFailed(RunPodError):
    """The handler ran and reported FAILED / an `error` field. Not retried:
    the input is most likely the problem, and a retry costs GPU time."""


class RunPodConfigError(RunPodError):
    """Misconfiguration (a plain-http URL override without
    SOBA_RUNPOD_ALLOW_HTTP=1). Raised before any request; never swallowed."""


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _billable_seconds(data, wall: float) -> float:
    if isinstance(data, dict):
        et = data.get("executionTime")
        if isinstance(et, (int, float)) and not isinstance(et, bool) and et >= 0:
            return float(et) / 1000.0
    return float(wall)


def _error_body(exc) -> str:
    try:
        return exc.read().decode(errors="replace")[:200]
    except (OSError, ValueError, AttributeError):
        return ""


def _record_drop(reason: str, **fields) -> None:
    """Record a no-call drop on the active run (telemetry) — or only log it."""
    try:
        from telemetry import drop
    except ImportError:  # the pod-side handler imports this module without telemetry
        log.info("drop: %s %s", reason, fields)
        return
    drop(reason, **fields)


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


def _fit_pose_to_cloud(verts, cloud, n_coarse: int = 24, icp_iters: int = 8):
    """Yaw-constrained ICP: find the rigid pose (rotation about vertical Y +
    translation) that seats the generated mesh in the observed cloud.

    Objects rest upright on the ground, so the rotation is a single yaw — a 6-DoF
    fit would let a partial cloud tip the object off-vertical. Translation is
    solved TOGETHER with yaw because the cloud is a partial fragment: its extent
    (hence its bbox-centre placement) is offset from the true object centre, and
    that offset must be absorbed or it swamps the rotation signal.

    For each of n_coarse yaw seeds it runs a few translation-only ICP steps
    (scored cloud->mesh: every partial-cloud point should land on the full mesh,
    not vice-versa) and keeps the lowest-residual seed. Returns (R, offset) such
    that new_vertices = verts @ R.T + offset. CPU-only.
    """
    import numpy as np
    import open3d as o3d

    V = np.asarray(verts, dtype=np.float64)
    C = np.asarray(cloud, dtype=np.float64)
    if len(V) < 8 or len(C) < 8:
        return np.eye(3), np.zeros(3)

    rng = np.random.default_rng(0)
    def _sub(a, n):
        return a if len(a) <= n else a[rng.choice(len(a), n, replace=False)]
    Vs = _sub(V, 6000)
    Cs = _sub(C, 2000)
    cM = Vs.mean(axis=0)  # yaw pivot

    def _tree(P):
        pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.ascontiguousarray(P)))
        return o3d.geometry.KDTreeFlann(pc)

    def _nn(P, kdt):
        idx = np.empty(len(P), dtype=np.int64)
        d2 = np.empty(len(P))
        for i, p in enumerate(P):
            _, j, dd = kdt.search_knn_vector_3d(np.ascontiguousarray(p, dtype=np.float64), 1)
            idx[i] = j[0]; d2[i] = dd[0]
        return idx, d2

    best = (np.inf, np.eye(3), np.zeros(3))
    for k in range(n_coarse):
        phi = 2.0 * np.pi * k / n_coarse
        c, s = np.cos(phi), np.sin(phi)
        R = np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])
        Vt = (Vs - cM) @ R.T + cM      # yaw-rotated mesh points
        t = np.zeros(3)
        for _ in range(icp_iters):     # translation-only refine at this yaw
            kdt = _tree(Vt + t)
            idx, _ = _nn(Cs, kdt)
            t = t + (Cs - (Vt + t)[idx]).mean(axis=0)
        kdt = _tree(Vt + t)
        _, d2 = _nn(Cs, kdt)
        score = float(np.mean(d2))
        if score < best[0]:
            best = (score, R, cM - R @ cM + t)
    return best[1], best[2]


def _cloud_fit_rmsd(verts, cloud, n_mesh: int = 8000, n_cloud: int = 2000) -> float:
    """Mean nearest-neighbour distance from the observed CLOUD to the mesh surface
    (cloud->mesh: every partial-cloud point should land on the full mesh). The
    single fair metric used to choose between candidate poses — lower is better."""
    import numpy as np
    import open3d as o3d

    V = np.asarray(verts, dtype=np.float64)
    C = np.asarray(cloud, dtype=np.float64)
    if len(V) < 8 or len(C) < 8:
        return float("inf")
    rng = np.random.default_rng(0)
    def _sub(a, n):
        return a if len(a) <= n else a[rng.choice(len(a), n, replace=False)]
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.ascontiguousarray(_sub(V, n_mesh))))
    kdt = o3d.geometry.KDTreeFlann(pc)
    tot = 0.0
    Cs = _sub(C, n_cloud)
    for p in Cs:
        _, _, d2 = kdt.search_knn_vector_3d(np.ascontiguousarray(p, dtype=np.float64), 1)
        tot += d2[0]
    return float(tot / len(Cs))


def _register_full_to_cloud(verts, cloud, voxel: float = 0.03):
    """FULL 3-DoF rotation alignment (the plan's Step-7 FPFH+ICP) of an already
    scaled+placed generative mesh to the observed cloud. Needed because the
    image-to-3D model emits the object VIEW-ALIGNED (in the crop's camera frame),
    so a table shot from an oblique angle comes out tilted ~50deg — a yaw-only fit
    cannot stand it up. FPFH+RANSAC global registration then point-to-plane ICP
    recovers the full rotation; the observed cloud (world frame) is the truth.

    Registers cloud->mesh (source=cloud: every partial point has a real match on
    the full mesh) and applies the INVERSE to the mesh. Returns transformed verts,
    or None if registration is unusable (caller then keeps the yaw-only result).
    """
    import numpy as np
    import open3d as o3d

    V = np.asarray(verts, dtype=np.float64)
    C = np.asarray(cloud, dtype=np.float64)
    if len(V) < 32 or len(C) < 32:
        return None
    def _pcd(x):
        return o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.ascontiguousarray(x)))
    try:
        src = _pcd(C).voxel_down_sample(voxel)   # observed (world frame)
        tgt = _pcd(V).voxel_down_sample(voxel)   # full mesh (view-tilted)
        if len(src.points) < 8 or len(tgt.points) < 8:
            return None
        nrm = o3d.geometry.KDTreeSearchParamHybrid(radius=voxel * 2, max_nn=30)
        src.estimate_normals(nrm); tgt.estimate_normals(nrm)
        fpar = o3d.geometry.KDTreeSearchParamHybrid(radius=voxel * 5, max_nn=100)
        fs = o3d.pipelines.registration.compute_fpfh_feature(src, fpar)
        ft = o3d.pipelines.registration.compute_fpfh_feature(tgt, fpar)
        res = o3d.pipelines.registration.registration_ransac_based_on_feature_matching(
            src, tgt, fs, ft, True, voxel * 1.5,
            o3d.pipelines.registration.TransformationEstimationPointToPoint(False), 3,
            [o3d.pipelines.registration.CorrespondenceCheckerBasedOnDistance(voxel * 1.5)],
            o3d.pipelines.registration.RANSACConvergenceCriteria(200000, 0.999))
        icp = o3d.pipelines.registration.registration_icp(
            src, tgt, voxel * 2, res.transformation,
            o3d.pipelines.registration.TransformationEstimationPointToPlane())
        T_mc = np.linalg.inv(icp.transformation)  # mesh -> cloud frame
    except Exception:
        return None
    return (np.c_[V, np.ones(len(V))] @ T_mc.T)[:, :3]


def _strip_base_and_fragments(mesh, return_stats: bool = False):
    """Clean an image-to-3D output BEFORE scaling/placement.

    Two failure modes seen on real Hunyuan3D output (2026-07-02, office_3):
      * a hallucinated DISPLAY MAT under the object — the model reads the
        object-on-white crop as a product shot on a base. The mat spans the
        whole footprint, inflates the bbox, and the uniform class-prior scale
        then shrinks the real object into a miniature standing on a platform.
      * loose FRAGMENTS — a weak crop yields disconnected pieces that float
        once the object is simulated as one rigid body.

    MAT rule (plane-based, so a VIEW-TILTED mesh is caught too — the first
    axis-band version missed tilted mats, seen live in scene hy3): RANSAC the
    dominant plane of the sampled surface. If it holds >50% of the samples AND
    the remaining points' footprint (projected onto that plane) is <50% of the
    plane's own footprint, it is a mat with a miniature standing on it — cut
    it. A real table/couch never matches: its top/bottom plane holds <40% of
    samples and its legs/body span the whole footprint. FRAGMENT rule: drop
    components spatially DETACHED from the dominant one (any size — the
    detached fraction is returned with return_stats=True so the caller can
    reject a generation that lost >30% of itself as structurally broken).
    Disable with SOBA_GEN_CLEAN=0.
    """
    import numpy as np
    import open3d as o3d

    verts = np.asarray(mesh.vertices)
    tris = np.asarray(mesh.triangles)
    if len(verts) == 0 or len(tris) == 0:
        return (mesh, 0.0) if return_stats else mesh

    out = o3d.geometry.TriangleMesh(mesh)

    # --- mat cut (dominant-plane test on sampled surface points) ----------
    pc = mesh.sample_points_uniformly(4000)
    pts = np.asarray(pc.points)
    ext = pts.max(axis=0) - pts.min(axis=0)
    thr = 0.012 * float(ext.max())

    def _footprint(p, n):
        """2D bbox area of points projected onto the plane with normal n."""
        if len(p) < 8:
            return 0.0
        d = p - p.mean(axis=0)
        d = d - np.outer(d @ n, n)
        basis = np.linalg.svd(d, full_matrices=False)[2][:2]
        q = d @ basis.T
        return float((q[:, 0].max() - q[:, 0].min()) * (q[:, 1].max() - q[:, 1].min()))

    try:
        plane, inliers = pc.segment_plane(
            distance_threshold=thr, ransac_n=3, num_iterations=500)
    except Exception:
        plane, inliers = None, []
    if plane is not None and len(inliers) > 0.5 * len(pts):
        n = np.asarray(plane[:3], dtype=np.float64)
        n /= max(np.linalg.norm(n), 1e-12)
        rest = np.delete(pts, inliers, axis=0)
        foot_plane = _footprint(pts[inliers], n)
        foot_rest = _footprint(rest, n)
        if foot_plane > 1e-9 and foot_rest < 0.5 * foot_plane:
            vd = np.abs(verts @ n + plane[3])
            cut = (vd < 1.5 * thr)[tris].all(axis=1)   # faces lying in the mat
            if cut.any() and not cut.all():
                out.remove_triangles_by_mask(cut)
                out.remove_unreferenced_vertices()

    # --- fragment filter: connectivity, not size ---------------------------
    # ANY component spatially detached from the dominant one (>3% of the
    # object extent from its SURFACE) is dropped — a 17%-area armrest floating
    # 65 cm from its chair passed the old <15% size rule (seen live, hy4).
    # Touching components of any size stay (a table's separate-component legs).
    # Vertex-to-vertex distance misjudges touching parts on vertex-sparse
    # meshes, hence the raycast surface distance.
    detached_frac = 0.0
    if len(out.triangles) == 0:
        return (mesh, detached_frac) if return_stats else mesh
    cluster, _, areas = out.cluster_connected_triangles()
    cluster = np.asarray(cluster)
    areas = np.asarray(areas)
    if len(areas) > 1:
        o_verts = np.asarray(out.vertices)
        o_tris = np.asarray(out.triangles)
        dom = int(areas.argmax())
        gap = 0.03 * float((o_verts.max(axis=0) - o_verts.min(axis=0)).max())
        dom_mesh = o3d.geometry.TriangleMesh(out)
        dom_mesh.remove_triangles_by_mask(cluster != dom)
        dom_mesh.remove_unreferenced_vertices()
        scene_rc = o3d.t.geometry.RaycastingScene()
        scene_rc.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(dom_mesh))
        keep = np.ones(len(areas), dtype=bool)
        for ci in range(len(areas)):
            if ci == dom:
                continue
            cp = o_verts[np.unique(o_tris[cluster == ci])].astype(np.float32)
            dmin = float(scene_rc.compute_distance(
                o3d.core.Tensor(cp)).numpy().min())
            keep[ci] = dmin < gap
        detached_frac = float(areas[~keep].sum() / max(areas.sum(), 1e-12))
        if not keep.all():
            out.remove_triangles_by_mask(~keep[cluster])
            out.remove_unreferenced_vertices()
    result = out if len(out.vertices) else mesh
    return (result, detached_frac) if return_stats else result


def _looks_shattered(mesh, min_dominant: float = 0.6) -> bool:
    """True when no single connected component holds >=min_dominant of the
    surface area — the generation is loose debris (floating pieces once
    simulated as one rigid body), not an object. Seen live: a 'chair' of four
    similar-size fragments (armrest, star-base, ...)."""
    import numpy as np

    if len(mesh.triangles) == 0:
        return True
    _, _, areas = mesh.cluster_connected_triangles()
    areas = np.asarray(areas)
    return bool(areas.max() < min_dominant * areas.sum())


def _class_dims_ok(mesh, coco_class: str | None) -> bool:
    """Reject a generated object whose ALIGNED dimensions are wildly outside
    its class's physical range (config class_gates, fix R2) — e.g. a 'chair'
    that is a 0.24 m-tall slab, or a 0.25 m-wide PANEL 'chair' (a flat sheet
    the height gate alone let through: seen live as floating boards in the
    office_3 desk cluster). Wide tolerance (0.7x low, 1.5x high): this only
    catches nonsense, not honest variance."""
    try:
        from scene import lookup  # lazy: avoid import cycles at module load
        gates = lookup.load_config().get("class_gates", {})
    except Exception:
        return True
    g = gates.get((coco_class or "").lower(), gates.get("default"))
    if not g:
        return True
    ext = mesh.get_max_bound() - mesh.get_min_bound()
    if "diameter" in g:
        lo, hi = g["diameter"]
        return 0.7 * lo <= float(max(ext)) <= 1.5 * hi
    if "height" in g:
        h = float(ext[1])
        if not (0.7 * g["height"][0] <= h <= 1.5 * g["height"][1]):
            return False
    if "width" in g:
        w = float(max(ext[0], ext[2]))
        if not (0.7 * g["width"][0] <= w <= 1.5 * g["width"][1]):
            return False
    return True


def _too_thin(mesh) -> bool:
    """A generation that is a SHEET pretending to be an object (user policy:
    don't create magic — drop garbage).

    Measured as enclosed volume / convex-hull volume: real furniture occupies
    >= ~4% of its hull even when spindly (worst honest office_3 chair: 4.1%),
    while the failure mode — a bent L-shell 'table' spanning a table-sized
    hull with paper-thin walls — measures 1-2.5%. Bar: 3%
    (SOBA_GEN_MIN_SOLID overrides; 0 disables). Non-watertight meshes are
    skipped (enclosed volume means nothing there)."""
    bar = float(os.environ.get("SOBA_GEN_MIN_SOLID", "0.03"))
    if bar <= 0:
        return False
    try:
        if not mesh.is_watertight():
            return False
        enc = abs(mesh.get_volume())
        hull, _ = mesh.compute_convex_hull()
        hv = hull.get_volume()
        return hv > 1e-9 and (enc / hv) < bar
    except Exception:
        return False


def _clean_gen(mesh, max_detached: float = 0.3):
    """Clean a raw generation and decide whether it is structurally usable.

    Returns (cleaned_mesh, detached_frac); cleaned_mesh is None when more than
    max_detached of the surface was floating debris — a generation that lost a
    third of itself detached is broken (its 'missing part' was the floater),
    so ship nothing rather than an amputated object + hovering pieces.
    Respects SOBA_GEN_CLEAN=0 (no cleaning, never rejects here).
    """
    if os.environ.get("SOBA_GEN_CLEAN", "1") == "0":
        return mesh, 0.0
    cleaned, detached = _strip_base_and_fragments(mesh, return_stats=True)
    if detached > max_detached:
        return None, detached
    return cleaned, detached


def _accept_regen(mesh, coco_class) -> bool:
    """Post-alignment quality gate for the generative band: drop debris and
    dimensionally-absurd generations instead of shipping them into the scene.
    SOBA_GEN_STRICT=0 disables (every generation is kept)."""
    if os.environ.get("SOBA_GEN_STRICT", "1") == "0":
        return True
    if _looks_shattered(mesh):
        log.info("generated mesh rejected: shattered (no dominant component)")
        return False
    if not _class_dims_ok(mesh, coco_class):
        log.info("generated mesh rejected: implausible %s dimensions", coco_class)
        return False
    if _too_thin(mesh):
        log.info("generated mesh rejected: paper-thin shell (not real %s "
                 "geometry) — dropped per drop-garbage policy", coco_class)
        return False
    return True


def _align_and_accept(gen_mesh, cloud, coco_class):
    """Align a cleaned generation and gate it; returns (mesh, alignment_method,
    scale_method) or None.

    Two sizing candidates for the SAME generated mesh: the ICP path sizes from
    the observed cloud (best when the observation is good), but a PARTIAL cloud
    can scale a perfectly fine generation outside its class's plausible range —
    seen live: tier 3 lost the couch and the main table to 'implausible
    dimensions' when fpfh_icp sized them from fragments. The mesh is not the
    problem there, the sizing is, so the class-prior coarse alignment gets a
    second try before the object is dropped. Only a generation that is
    implausible under BOTH sizings is garbage (drop policy)."""
    from reconstruction import icp_align  # lazy: it imports us back

    res = icp_align.align(gen_mesh, cloud, coco_class)
    if _accept_regen(res.mesh, coco_class):
        return res.mesh, res.alignment_method, res.scale_method
    if res.alignment_method == "fpfh_icp":
        log.info("ICP-sized %s failed the class gate -> retrying with the "
                 "class-prior size", coco_class)
        aligned = coarse_align_to_cloud(gen_mesh, cloud, coco_class, clean=False)
        if _accept_regen(aligned, coco_class):
            return aligned, "coarse_aligned", "class_prior"
    return None


def coarse_align_to_cloud(mesh, cloud, coco_class: str | None = None,
                          clean: bool = True):
    """Scale a unit-cube GENERATED mesh to a real size, YAW-align it to the
    observed cloud, and place it where the object was seen.

    A generative model returns its mesh in a unit cube with no real size and in
    its OWN canonical pose (no relation to how the object sits in the room). These
    objects are ALL poorly observed (that is why they routed generative), so the
    observed cloud is a partial fragment whose extent cannot be trusted for size
    (fragment-fitting gave masses of 0.1-25 kg for the same chairs). So:
      * SIZE   — a per-class real-world prior (CLASS_SIZE_PRIOR_M): a uniform
                 scale that preserves the model's proportions.
      * YAW    — the rotation about vertical Y that best seats the mesh in the
                 cloud (objects rest upright, so yaw is the only free rotation).
      * PLACE  — the cloud's centroid.
    CPU-only, runs without a GPU. (Full FPFH+ICP would add pitch/roll, but upright
    furniture on a floor needs only yaw, and the partial cloud makes a 6-DoF fit
    less stable than this constrained one.)
    """
    import numpy as np
    import open3d as o3d

    cloud = np.asarray(cloud, dtype=np.float64)
    if len(mesh.vertices) == 0:
        return mesh

    # Strip the hallucinated display mat + loose fragments FIRST, so the
    # class-prior scale sizes the actual object, not object+mat. (clean=False
    # when the caller already cleaned — e.g. regenerate(), which needs the
    # detached-fraction stats to reject broken generations.)
    if clean and os.environ.get("SOBA_GEN_CLEAN", "1") != "0":
        mesh = _strip_base_and_fragments(mesh)

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

    # Scale + place first (into world metres, roughly centred on the cloud).
    v = (np.asarray(out.vertices) - m_centre) * s + c_centre

    # POSE: two candidates, keep whichever seats the mesh best in the cloud.
    #   * yaw-only   — rotation about vertical Y + translation (safe for objects
    #                  the model already emits ~upright, e.g. a front-on couch).
    #   * full 3-DoF — FPFH+ICP, recovers pitch/roll too (needed because the
    #                  image-to-3D model is VIEW-ALIGNED: an obliquely-shot table
    #                  comes out tilted and yaw-only cannot stand it up).
    # Scored by cloud->mesh RMSD so a wrong full-registration never beats a good
    # yaw-only fit (that would otherwise tip an already-correct object over).
    if len(cloud) >= 8:
        R, offset = _fit_pose_to_cloud(v, cloud)
        v_yaw = v @ R.T + offset
        best_v, best_score = v_yaw, _cloud_fit_rmsd(v_yaw, cloud)
        v_full = _register_full_to_cloud(v, cloud)
        if v_full is not None:
            score_full = _cloud_fit_rmsd(v_full, cloud)
            if score_full < best_score:
                best_v, best_score = v_full, score_full
        v = best_v

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
        timeout_s: float | None = None,
    ):
        self.api_key = api_key
        self.gen_endpoint = gen_endpoint
        self.completion_endpoint = completion_endpoint
        self.gen_model = gen_model
        self.completion_model = completion_model
        # Every resilience / cost number comes from config/pipeline.yaml
        # `runpod:` (reconstruction.runpod_policy); the attributes below are
        # the per-engine (= per job) copies a caller or a test may replace.
        cfg = runpod_policy.load_runpod_config()
        self.config = cfg
        # one request's socket timeout: arg > SOBA_RUNPOD_TIMEOUT > config. In
        # runsync mode (the SDK test server) this is the whole job — shape
        # ~2.5 min + paint ~2 min per object plus cold model loads.
        env_timeout = os.environ.get("SOBA_RUNPOD_TIMEOUT")
        self.timeout_s = float(
            timeout_s if timeout_s is not None
            else env_timeout if env_timeout else cfg["request_timeout_s"])
        self.transport = str(cfg["transport"])
        self.job_timeout_s = float(cfg["job_timeout_s"])
        self.poll_interval_s = float(cfg["poll_interval_s"])
        self.stall_timeout_s = float(cfg["stall_timeout_s"])
        self.retry = runpod_policy.RetryPolicy.from_config(cfg)
        self.budget = runpod_policy.Budget.from_config(cfg)

    # --- transport (RunPod serverless: /run + /status polling, or /runsync) --
    def _check_scheme(self, url: str, var: str) -> None:
        if url.lower().startswith("https://") or _env_flag("SOBA_RUNPOD_ALLOW_HTTP"):
            return
        raise RunPodConfigError(
            f"{var}={url!r} is not https://; the bearer key would travel in clear. "
            "Set SOBA_RUNPOD_ALLOW_HTTP=1 only for a local tunnel / test server.")

    def _urls(self, endpoint: str) -> tuple[str, str | None]:
        """(submit URL, base URL for /status + /cancel, or None if unknown)."""
        override = os.environ.get("SOBA_RUNPOD_URL")
        if override:
            # The SDK's local test server (`python generative_handler.py
            # --rp_serve_api`) through a tunnel: one blocking POST to the
            # given URL; polling only if we can see where /status lives.
            self._check_scheme(override, "SOBA_RUNPOD_URL")
            trimmed = override.rstrip("/")
            for suffix in ("/runsync", "/run"):
                if trimmed.endswith(suffix):
                    return override, trimmed[: -len(suffix)]
            return override, None
        root = os.environ.get("SOBA_RUNPOD_BASE_URL") or self.BASE_URL
        self._check_scheme(root, "SOBA_RUNPOD_BASE_URL")
        base = f"{root.rstrip('/')}/{endpoint}"
        op = "runsync" if self.transport == "runsync" else "run"
        return f"{base}/{op}", base

    def _http(self, method: str, url: str, body: bytes | None = None) -> dict:
        """One authenticated request -> parsed JSON. Classifies failures."""
        import http.client
        import json
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            url, data=body, method=method,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as exc:  # before URLError: it is a subclass
            detail = _error_body(exc)
            if exc.code == 429 or exc.code >= 500:
                raise RunPodTransient(f"HTTP {exc.code} from {url}: {detail}") from exc
            raise RunPodError(f"HTTP {exc.code} from {url}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError,
                http.client.HTTPException) as exc:
            raise RunPodTransient(f"{type(exc).__name__}: {exc}") from exc
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise RunPodError(f"non-JSON reply from {url}: {raw[:120]!r}") from exc
        if not isinstance(data, dict):
            raise RunPodError(f"unexpected reply from {url}: {raw[:120]!r}")
        return data

    def _cancel(self, base: str, job_id: str) -> None:
        try:
            self._http("POST", f"{base}/cancel/{job_id}")
        except RunPodError as exc:  # best effort; the job may already be gone
            log.info("runpod cancel %s: %s", job_id, exc)

    def _poll(self, base: str, job_id: str, status: str | None) -> dict:
        """GET /status/{id} until terminal. A job stuck in one non-terminal
        state past `stall_timeout_s` is cancelled and retried (transient); one
        still running past `job_timeout_s` is cancelled for good."""
        started = last_change = runpod_policy._now()
        while True:
            now = runpod_policy._now()
            if now - started > self.job_timeout_s:
                self._cancel(base, job_id)
                raise RunPodError(f"job {job_id} not finished after "
                                  f"{self.job_timeout_s:.0f}s (last {status}); cancelled")
            if now - last_change > self.stall_timeout_s:
                self._cancel(base, job_id)
                raise RunPodTransient(f"job {job_id} stalled in {status} for "
                                      f"{self.stall_timeout_s:.0f}s; cancelled")
            _sleep(self.poll_interval_s)
            data = self._http("GET", f"{base}/status/{job_id}")
            new_status = data.get("status")
            if new_status in _TERMINAL_STATUS:
                return data
            if new_status != status:
                status, last_change = new_status, runpod_policy._now()

    def _submit_and_wait(self, endpoint: str, body: bytes) -> dict:
        submit_url, base = self._urls(endpoint)
        data = self._http("POST", submit_url, body)
        status = data.get("status")
        if status in _TERMINAL_STATUS or (status is None and "id" not in data):
            return data  # runsync finished in one go (or a bare {output} reply)
        job_id = data.get("id")
        if not base or not job_id:
            raise RunPodTransient(f"non-terminal reply {status!r} from {submit_url} "
                                  "and no status URL to poll")
        return self._poll(base, str(job_id), status)

    def _runsync(self, endpoint: str, payload: dict) -> dict:
        """POST {"input": payload} to `endpoint`, return the `output` dict.

        Blocks until the job finishes: `/run` then `/status/{id}` polling (or a
        single `/runsync` when configured / when SOBA_RUNPOD_URL is set).
        Transient failures are retried with exponential backoff + jitter; every
        submission is charged to the per-job budget, reported to the breaker
        and to `remote_call_hook`. Raises `RunPodError` (a RuntimeError) when
        the call is given up: FAILED / `error` from the handler, a non-retryable
        HTTP status, or the retry policy exhausted.
        """
        import json

        body = json.dumps({"input": payload}).encode()
        breaker = runpod_policy.breaker_for(endpoint, self.config)
        n = max(1, self.retry.max_attempts)
        last: RunPodError | None = None
        for attempt in range(1, n + 1):
            t0 = time.perf_counter()
            data, exc = None, None
            try:
                data = self._submit_and_wait(endpoint, body)
            except RunPodConfigError:
                raise  # nothing was sent: not a call, not a failure
            except RunPodError as e:
                exc = e
            billable = _billable_seconds(data, time.perf_counter() - t0)
            self.budget.charge(
                billable, runpod_policy.estimate_usd(billable, endpoint, self.config),
                endpoint)
            if remote_call_hook is not None:
                remote_call_hook(endpoint, billable)
            if exc is None:
                status = data.get("status")
                if status == "FAILED" or "error" in data:
                    exc = RunPodJobFailed(f"RunPod job failed: {data.get('error') or data}")
                elif status in ("CANCELLED", "TIMED_OUT"):
                    exc = RunPodError(f"RunPod job {status}: {data}")
                else:
                    breaker.record_success()
                    return data.get("output", {})
            breaker.record_failure()
            last = exc
            log.warning("runpod %s attempt %d/%d failed: %s", endpoint, attempt, n, exc)
            if not isinstance(exc, RunPodTransient) or attempt == n:
                break
            if not breaker.allow():
                log.warning("runpod %s: circuit breaker open, no more retries", endpoint)
                break
            _sleep(self.retry.sleep_for(attempt))
        assert last is not None
        raise last

    def _guard(self, endpoint: str) -> str | None:
        """Why NOT to call `endpoint` now (a drop reason), or None to proceed.
        Order matters: the breaker's half-open probe slot is only taken when
        the kill switch and the budget already allow the call."""
        if runpod_policy.disabled():
            return runpod_policy.DROP_DISABLED
        if self.budget.exhausted():
            return runpod_policy.DROP_BUDGET
        if not runpod_policy.breaker_for(endpoint, self.config).allow():
            return runpod_policy.DROP_BREAKER_OPEN
        return None

    # --- endpoint-specific seams (contract shared with the serverless handler,
    #     deploy/runpod/generative_handler.py) ------------------------------
    def _build_input(self, *, mode: str, model: str, crop_path, coco_class, cloud,
                     mesh=None) -> dict:
        """Build the handler `input` dict for this object.

        mode "regenerate" -> the image-to-3D model: send the crop image (base64
                             JPEG/PNG) + class; no geometry.
        mode "complete"   -> the learned shape-completion model: send the PARTIAL
                             GEOMETRY (the observed `cloud` as a base64 .npy) +
                             class. The crop is optional context.
        Mirrors generative_handler.py's expected `input` schema exactly.
        """
        import base64

        payload = {"mode": mode, "model": model, "coco_class": coco_class or "obj"}
        if mode == "regenerate":
            if crop_path is None:
                raise RuntimeError("no crop staged for this object")
            with open(crop_path, "rb") as f:
                payload["image_b64"] = base64.b64encode(f.read()).decode()
        elif mode == "complete":
            import io

            import numpy as np
            buf = io.BytesIO()
            np.save(buf, np.asarray(cloud, dtype=np.float32))
            payload["cloud_npy_b64"] = base64.b64encode(buf.getvalue()).decode()
            if crop_path is not None:
                with open(crop_path, "rb") as f:
                    payload["image_b64"] = base64.b64encode(f.read()).decode()
        else:
            raise ValueError(f"unknown mode {mode!r}")
        return payload

    def _decode_mesh(self, output: dict):
        """Decode the handler `output` into an open3d TriangleMesh.

        Contract: {"mesh_b64": <base64>, "format": "obj"|"ply"}. OBJ/PLY on
        purpose — Open3D cannot read GLB back (that gotcha is why the handler
        never returns GLB for the round-trip).
        """
        import base64
        import tempfile

        fmt = (output or {}).get("format", "obj").lower()
        data = (output or {}).get("mesh_b64")
        if not data:
            raise RunPodJobFailed(
                f"RunPod returned no mesh (output keys: {list((output or {}).keys())})")

        import open3d as o3d

        raw = base64.b64decode(data)
        with tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=True) as tf:
            tf.write(raw)
            tf.flush()
            m = o3d.io.read_triangle_mesh(tf.name)
        if len(m.vertices) == 0:
            raise RunPodJobFailed("RunPod mesh decoded to zero vertices")
        m.compute_vertex_normals()
        return m

    # --- capabilities ---------------------------------------------------
    def complete(self, *, mesh, cloud, crop_path, coco_class):
        # MIDDLE band: learned shape-completion (geometry-conditioned). No
        # completion endpoint configured -> None so the caller falls back to the
        # local Poisson repair.
        if not self.completion_endpoint:
            return None
        reason = self._guard(self.completion_endpoint)
        if reason:
            log.info("completion via RunPod skipped (%s); local Poisson fallback", reason)
            return None
        payload = self._build_input(
            mode="complete", model=self.completion_model, crop_path=crop_path,
            coco_class=coco_class, cloud=cloud, mesh=mesh)
        try:
            return self._decode_mesh(self._runsync(self.completion_endpoint, payload))
        except RunPodConfigError:
            raise
        except RunPodError as exc:
            # The object is NOT dropped: the assembler seals the partial mesh
            # locally. One endpoint outage must not abort the assembly.
            log.warning("completion via RunPod failed (%s); local Poisson fallback", exc)
            return None

    def regenerate(self, *, cloud, crop_path, coco_class):
        # BOTTOM band: image-to-3D (image-conditioned). No gen endpoint -> None
        # so the object is dropped (as with no GPU at all). Same for no crop:
        # regeneration is image-conditioned, so an object too small to crop is
        # DECLINED per the Engine contract (LocalGpuEngine already does), not a
        # raise that kills the whole assembly.
        if not self.gen_endpoint or crop_path is None:
            return None
        # Kill switch / budget / open breaker: no request, the object is
        # dropped and the reason recorded on the run (telemetry drops).
        reason = self._guard(self.gen_endpoint)
        if reason:
            fields = {"band": "generative", "endpoint": self.gen_endpoint,
                      "coco_class": coco_class}
            if reason == runpod_policy.DROP_BUDGET:
                fields["limit"] = self.budget.exhausted()
            log.info("regeneration via RunPod skipped: %s", reason)
            _record_drop(reason, **fields)
            return None
        payload = self._build_input(
            mode="regenerate", model=self.gen_model, crop_path=crop_path,
            coco_class=coco_class, cloud=cloud)
        # A RunPodError from here propagates: scripts/run_assemble.py records
        # the `runpod_failed` drop for this one object and continues.
        gen_mesh = self._decode_mesh(self._runsync(self.gen_endpoint, payload))
        # The model returns a unit-cube mesh; scale it to a class-size prior and
        # place it on the observed cloud (precise FPFH+ICP is Phase 8).
        gen_mesh, detached = _clean_gen(gen_mesh)
        if gen_mesh is None:
            log.info("generation rejected: %.0f%% of it was detached debris",
                     detached * 100)
            return None
        got = _align_and_accept(gen_mesh, cloud, coco_class)
        if got is None:
            return None    # implausible under BOTH sizings -> drop the object
        mesh, align_m, scale_m = got
        return RegenResult(mesh=mesh, alignment_method=align_m,
                           scale_method=scale_m)


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
        self._triposg = None   # cached (pipe, rmbg, prepare_image) — loaded once
        self._hunyuan = None   # cached (pipe, rembg) — loaded once

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
        # `last_decline` tells the caller WHY None came back: a verdict on the
        # object ("rejected: ...", cacheable) or the model not running at all
        # ("unavailable: ...", never a verdict, never cached).
        self.last_decline = None
        if crop_path is None:
            # Crop staging found no usable view (mask under min_area_px): a verdict
            # on the OBJECT, not a model failure. It must not trip strict mode, and
            # it is cacheable like any other rejection.
            self.last_decline = "rejected: no crop staged (no usable view of the object)"
            return None
        try:
            gen_mesh = self._run_gen(crop_path=crop_path, coco_class=coco_class)
            gen_mesh, detached = _clean_gen(gen_mesh)
            if gen_mesh is None:
                log.info("generation rejected: %.0f%% of it was detached debris",
                         detached * 100)
                self.last_decline = "rejected: detached debris"
                return None
            got = _align_and_accept(gen_mesh, cloud, coco_class)
            if got is None:
                self.last_decline = "rejected: implausible size under both sizings"
                return None    # implausible under BOTH sizings -> drop
            mesh, align_m, scale_m = got
            return RegenResult(mesh=mesh, alignment_method=align_m,
                               scale_method=scale_m)
        except Exception as e:  # missing model / OOM -> drop (as with no GPU)
            log.warning("local-GPU generation unavailable (%s) -> object dropped", e)
            self.last_decline = f"unavailable: {type(e).__name__}: {str(e)[:160]}"
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
        SOBA_TRIPOSG_HOME / _WEIGHTS / RMBG_WEIGHTS.
        """
        if self._triposg is not None:
            return self._triposg
        import sys

        import torch

        home = os.environ.get("SOBA_TRIPOSG_HOME", "/workspace/TripoSG")
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
                        raise RuntimeError("diso not built; use SOBA_TRIPOSG_FLASH=0")
                stub.DiffDMC = _NoDiso
                sys.modules["diso"] = stub
        from triposg.pipelines.pipeline_triposg import TripoSGPipeline
        from image_process import prepare_image
        from briarmbg import BriaRMBG

        weights = os.path.join(home, "pretrained_weights")
        tri_dir = os.environ.get("SOBA_TRIPOSG_WEIGHTS", os.path.join(weights, "TripoSG"))
        rmbg_dir = os.environ.get("SOBA_RMBG_WEIGHTS", os.path.join(weights, "RMBG-1.4"))
        pipe = TripoSGPipeline.from_pretrained(tri_dir).to("cuda", torch.float16)
        rmbg = BriaRMBG.from_pretrained(rmbg_dir).to("cuda")
        rmbg.eval()
        self._triposg = (pipe, rmbg, prepare_image)
        return self._triposg

    def _run_gen(self, *, crop_path, coco_class):
        """Image-to-3D on the crop -> open3d mesh (model's own frame; the caller's
        coarse_align_to_cloud recovers real-world scale/pose from the observed
        cloud). Dispatches on self.gen_model (fix K1): "triposg" (Tiers 1-2) or
        "hunyuan3d" (Tiers 3-4). A raise here -> object dropped."""
        if crop_path is None:
            raise RuntimeError("no crop staged for this object")
        if self.gen_model == "triposg":
            verts, faces = self._run_triposg(crop_path)
        elif self.gen_model in ("hunyuan3d", "hunyuan"):
            verts, faces = self._run_hunyuan(crop_path)
        else:
            raise NotImplementedError(f"local gen model '{self.gen_model}' not wired")
        mesh = self._finalize_gen_mesh(verts, faces)
        if (self.gen_model in ("hunyuan3d", "hunyuan")
                and os.environ.get("SOBA_HUNYUAN_PAINT", "0") == "1"):
            mesh = self._paint_hunyuan(mesh, crop_path)
        return mesh

    def _paint_hunyuan(self, mesh, crop_path):
        """Hunyuan3D-Paint texture stage -> the SAME object with vertex colors.

        Runs the PBR texture pipeline (~21 GB VRAM — pod-only, which is why the
        default is off) on the decimated shape mesh, then BAKES the produced UV
        texture down to per-vertex colors: the whole downstream pipeline (clean,
        align, GLB export, browser) speaks vertex colors, so a baked mesh flows
        through untouched while a textured GLB would be lost at the first
        open3d round-trip. Paint remeshes, so the painted topology REPLACES the
        input's. Best-effort: any failure returns the untextured mesh.
        """
        import tempfile

        import numpy as np
        import open3d as o3d

        try:
            pipe = self._load_hunyuan_paint()
            import trimesh
            with tempfile.TemporaryDirectory() as td:
                src = os.path.join(td, "shape.glb")
                trimesh.Trimesh(np.asarray(mesh.vertices),
                                np.asarray(mesh.triangles)).export(src)
                # save_glb=False keeps the output a textured OBJ+MTL and skips
                # the bpy (Blender) OBJ->GLB convert — bpy has no py3.12 wheel
                # on the pod and is stubbed for import only.
                out_path = pipe(mesh_path=src, image_path=str(crop_path),
                                output_mesh_path=os.path.join(td, "painted.obj"),
                                save_glb=False)
                painted = trimesh.load(out_path, force="mesh")
                colors = np.asarray(painted.visual.to_color().vertex_colors)
            m = o3d.geometry.TriangleMesh(
                o3d.utility.Vector3dVector(np.asarray(painted.vertices, np.float64)),
                o3d.utility.Vector3iVector(np.ascontiguousarray(painted.faces, np.int32)))
            m.vertex_colors = o3d.utility.Vector3dVector(
                colors[:, :3].astype(np.float64) / 255.0)
            # WELD: the paint remesh leaves seams unmerged — a visually solid
            # table reads as ~600 touching "components" (22% dominant), which
            # the client's _looks_shattered then falsely rejects. One
            # duplicate-vertex weld reconnects it to a single component.
            m.remove_duplicated_vertices()
            m.remove_degenerate_triangles()
            m.compute_vertex_normals()
            return m
        except Exception as e:
            log.warning("Hunyuan paint stage failed (%s) -> untextured mesh", e)
            return mesh

    def _load_hunyuan_paint(self):
        """Import + build the Hunyuan3D-Paint pipeline once, caching it. The
        repo's cfg paths are relative, so construction happens with cwd at
        hy3dpaint (same gotcha as PatchComplete's priors/)."""
        if getattr(self, "_hunyuan_paint", None) is not None:
            return self._hunyuan_paint
        import sys

        home = os.environ.get("SOBA_HUNYUAN_HOME", "/workspace/Hunyuan3D-2.1")
        paint_dir = os.path.join(home, "hy3dpaint")
        for p in (home, paint_dir):
            if os.path.isdir(p) and p not in sys.path:
                sys.path.insert(0, p)
        try:
            from utils.torchvision_fix import apply_fix
            apply_fix()
        except Exception:
            pass
        # hy3dpaint's mesh_utils imports bpy (Blender) at module top-level, but
        # bpy is only USED by the save_glb=True OBJ->GLB convert we never call
        # (no py3.12 wheel exists). Stub it so the import succeeds.
        if "bpy" not in sys.modules:
            try:
                import bpy  # noqa: F401
            except ImportError:
                import types
                sys.modules["bpy"] = types.ModuleType("bpy")
        from textureGenPipeline import (Hunyuan3DPaintConfig,
                                        Hunyuan3DPaintPipeline)

        views = int(os.environ.get("SOBA_PAINT_VIEWS", "6"))
        res = int(os.environ.get("SOBA_PAINT_RES", "512"))
        conf = Hunyuan3DPaintConfig(views, res)
        # the repo's defaults mix two bases (cfg is repo-root-relative, the
        # RealESRGAN ckpt is hy3dpaint-relative) — pin both absolutely
        conf.multiview_cfg_path = os.path.join(paint_dir, "cfgs",
                                               "hunyuan-paint-pbr.yaml")
        conf.realesrgan_ckpt_path = os.path.join(paint_dir, "ckpt",
                                                 "RealESRGAN_x4plus.pth")
        cwd = os.getcwd()
        try:
            os.chdir(paint_dir)  # any remaining relative refs
            self._hunyuan_paint = Hunyuan3DPaintPipeline(conf)
        finally:
            os.chdir(cwd)
        return self._hunyuan_paint

    @staticmethod
    def _finalize_gen_mesh(verts, faces):
        """Shared post-processing for any image-to-3D output: build the open3d
        mesh, weld/clean, and decimate to a physics-sane budget. Generative
        extractors emit 1-3M triangles (TripoSG at 505^3, Hunyuan3D's marching
        cubes) which choke the downstream Poisson finalize + CoACD; SOBA_GEN_FACES
        caps it (0 disables)."""
        import numpy as np
        import open3d as o3d

        if len(verts) == 0 or len(faces) == 0:
            raise RuntimeError("generative model produced an empty mesh")
        m = o3d.geometry.TriangleMesh(
            o3d.utility.Vector3dVector(np.asarray(verts, dtype=np.float64)),
            o3d.utility.Vector3iVector(np.ascontiguousarray(faces, dtype=np.int32)))
        m.remove_duplicated_vertices()
        m.remove_degenerate_triangles()
        # Keep the dominant connected surface: a high-res marching-cubes decode
        # leaves a cloud of tiny floating shells around the real object (a full-
        # octree TripoSG chair had ~927 floaters around one 2.7M-face surface).
        # Drop clusters below 2% of the face count — cheap, and always correct for
        # a single-object generation.
        try:
            labels, counts, _ = m.cluster_connected_triangles()
            labels = np.asarray(labels)
            counts = np.asarray(counts)
            if len(counts) > 1:
                big = set(np.where(counts > 0.02 * counts.sum())[0].tolist())
                m.remove_triangles_by_mask(np.array([l not in big for l in labels]))
                m.remove_unreferenced_vertices()
        except Exception:
            pass
        # accept the legacy TripoSG knob as a fallback so existing runs are unchanged
        budget = int(os.environ.get("SOBA_GEN_FACES",
                                    os.environ.get("SOBA_TRIPOSG_FACES", "40000")))
        if budget > 0 and len(m.triangles) > budget:
            m = m.simplify_quadric_decimation(budget)
        m.compute_vertex_normals()
        return m

    def _run_triposg(self, crop_path):
        """TripoSG (image-to-3D) inference -> (verts, faces). Removes the crop's
        background (BriaRMBG) and samples a mesh in TripoSG's unit cube.
        Steps/CFG/seed via SOBA_TRIPOSG_STEPS / _CFG / _SEED."""
        import numpy as np
        import torch

        pipe, rmbg, prepare_image = self._load_triposg()
        img = prepare_image(str(crop_path), bg_color=np.array([1.0, 1.0, 1.0]),
                            rmbg_net=rmbg)
        steps = int(os.environ.get("SOBA_TRIPOSG_STEPS", "50"))
        cfg = float(os.environ.get("SOBA_TRIPOSG_CFG", "7.0"))
        seed = int(os.environ.get("SOBA_TRIPOSG_SEED", "42"))
        # The flash decoder needs `diso` (a CUDA ext requiring nvcc). Default OFF so
        # TripoSG uses the marching-cubes extractor instead — no nvcc/diso needed.
        # Set SOBA_TRIPOSG_FLASH=1 on a pod that has diso built.
        use_flash = os.environ.get("SOBA_TRIPOSG_FLASH", "0") == "1"
        # Mesh-extraction resolution. TripoSG's default (dense 8 / hierarchical 9,
        # a 512^3 grid) is REQUIRED for correct geometry: the reduced 7/8 (256^3)
        # under-resolves the marching-cubes iso-surface and turns thin/concave
        # objects (chairs) into a holey genus-~3000 sponge, while bulky objects
        # (couch) survive. 8/9 decodes ~16M points and is tight on an 8 GB GPU; it
        # fits when the card is otherwise free (serial per-object regen), and is a
        # non-issue on RunPod. Dial DOWN via SOBA_TRIPOSG_DENSE/_HIER only if it
        # OOMs — accepting the sponge — but prefer RunPod for 8 GB quality builds.
        dense = int(os.environ.get("SOBA_TRIPOSG_DENSE", "8"))
        hier = int(os.environ.get("SOBA_TRIPOSG_HIER", "9"))
        if torch.cuda.is_available():
            torch.cuda.empty_cache()  # free the prior object's decode before this one
        with torch.no_grad():
            out = pipe(
                image=img,
                generator=torch.Generator(device=pipe.device).manual_seed(seed),
                num_inference_steps=steps, guidance_scale=cfg,
                use_flash_decoder=use_flash,
                dense_octree_depth=dense, hierarchical_octree_depth=hier,
            ).samples[0]
        return (np.asarray(out[0], dtype=np.float64),
                np.ascontiguousarray(np.asarray(out[1], dtype=np.int32)))

    def _load_hunyuan(self):
        """Import + load the Hunyuan3D 2.1 SHAPE pipeline once, caching it.

        The repo (cloned by deploy/runpod/setup_hunyuan3d.sh) exposes the shape
        package under hy3dshape/ (and hy3dpaint/ for the optional PBR texture
        model). Model id defaults to the full 2.1 checkpoint; override with
        SOBA_HUNYUAN_MODEL=tencent/Hunyuan3D-2mini for the 0.6B variant that
        fits an 8 GB GPU (the local cost-saver path). Shape gen needs ~10 GB, so
        the full 2.1 OOMs an 8 GB box -> caught -> object dropped (RunPod carries
        it); texture generation (21 GB) is RunPod-only and not run here.
        """
        if self._hunyuan is not None:
            return self._hunyuan
        import sys

        home = os.environ.get("SOBA_HUNYUAN_HOME", "/workspace/Hunyuan3D-2.1")
        for p in (home, os.path.join(home, "hy3dshape"), os.path.join(home, "hy3dpaint")):
            if os.path.isdir(p) and p not in sys.path:
                sys.path.insert(0, p)
        from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline

        model_id = os.environ.get("SOBA_HUNYUAN_MODEL", "tencent/Hunyuan3D-2.1")
        pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(model_id)
        # Hunyuan ships its own background remover; if unavailable, we pass the raw
        # crop (the shape model tolerates a light background but prefers a clean one).
        try:
            from hy3dshape.rembg import BackgroundRemover
            rembg = BackgroundRemover()
        except Exception:
            rembg = None
        self._hunyuan = (pipe, rembg)
        return self._hunyuan

    def _run_hunyuan(self, crop_path):
        """Hunyuan3D 2.1 SHAPE inference -> (verts, faces). Background-removes the
        crop, runs the flow-matching shape pipeline, and returns the untextured
        mesh in the model's own frame. Steps/seed via SOBA_HUNYUAN_STEPS/_SEED."""
        import numpy as np
        import torch
        from PIL import Image

        pipe, rembg = self._load_hunyuan()
        img = Image.open(str(crop_path))
        if img.mode == "RGBA" and img.getextrema()[3][0] < 255:
            pass  # crop carries OUR ground-truth mask as alpha — never re-guess
        else:
            img = img.convert("RGB")
            if rembg is not None:
                img = rembg(img)  # -> RGBA with background stripped
        steps = int(os.environ.get("SOBA_HUNYUAN_STEPS", "30"))
        seed = int(os.environ.get("SOBA_HUNYUAN_SEED", "42"))
        with torch.no_grad():
            mesh = pipe(image=img, num_inference_steps=steps,
                        generator=torch.Generator().manual_seed(seed))[0]
        # Hunyuan returns a trimesh.Trimesh (shape only, no texture on this path).
        return (np.asarray(mesh.vertices, dtype=np.float64),
                np.ascontiguousarray(np.asarray(mesh.faces, dtype=np.int32)))


def gen_model_for_tier(tier) -> str:
    """The generative (image-to-3D) model for a tier — the plan's single source
    of truth (fix K1): Tiers 1-2 -> TripoSG (fast, good), Tiers 3-4 -> Hunyuan3D
    2.1 (highest quality, PBR). Unknown/None tier -> TripoSG (the safe default)."""
    try:
        return "triposg" if int(tier) <= 2 else "hunyuan3d"
    except (TypeError, ValueError):
        return "triposg"


class SplitEngine(Engine):
    """A different backend per band. The natural split: the image-to-3D model
    outgrows the local card first (Hunyuan3D ~10 GB), so the GENERATIVE band
    goes to RunPod while the COMPLETION band (PatchComplete, ~small) stays on
    the local GPU. Composed automatically by make_engine when RunPod has a gen
    endpoint but no completion endpoint and a local CUDA device exists."""

    def __init__(self, completer: Engine, regenerator: Engine):
        self._completer = completer
        self._regenerator = regenerator
        # surfaced for run_assemble's "engine: ..." print
        self.gen_model = getattr(regenerator, "gen_model", None)
        self.completion_model = getattr(completer, "completion_model", None)

    def complete(self, **kw):
        return self._completer.complete(**kw)

    def regenerate(self, **kw):
        return self._regenerator.regenerate(**kw)

    @property
    def last_decline(self):
        return getattr(self._regenerator, "last_decline", None)


def make_engine(tier=None) -> Engine:
    """Pick the geometry-invention backend, in priority order:
      1. RunPodEngine  — if RUNPOD_API_KEY + an endpoint are set.
           RUNPOD_GEN_ENDPOINT_ID (alias RUNPOD_ENDPOINT_ID) — image-to-3D.
           RUNPOD_COMPLETION_ENDPOINT_ID                     — shape-completion.
           If only the GEN endpoint is set and a local CUDA GPU exists, the
           completion band stays local (SplitEngine) instead of degrading from
           PatchComplete to the Poisson fallback.
      2. LocalGpuEngine — if a CUDA GPU is visible (and SOBA_LOCAL_GPU != "0").
      3. LocalEngine    — no GPU: completion = Poisson, generation = drop.

    The generative model defaults to the per-tier pick (gen_model_for_tier: T1-2
    TripoSG, T3-4 Hunyuan3D). An explicit env override always wins:
    RUNPOD_GEN_MODEL/RUNPOD_COMPLETION_MODEL (RunPod) or SOBA_GEN_MODEL/
    SOBA_COMPLETION_MODEL (local). This is why heavy Hunyuan3D (~10 GB, tiers
    3-4) lands on RunPod by default while the local 8 GB box keeps TripoSG — and
    a small local scene can still force Hunyuan-2mini via SOBA_GEN_MODEL."""
    tier_gen = gen_model_for_tier(tier)
    key = os.environ.get("RUNPOD_API_KEY")
    gen = os.environ.get("RUNPOD_GEN_ENDPOINT_ID") or os.environ.get("RUNPOD_ENDPOINT_ID")
    comp = os.environ.get("RUNPOD_COMPLETION_ENDPOINT_ID")
    if key and (gen or comp):
        runpod = RunPodEngine(
            key, gen_endpoint=gen, completion_endpoint=comp,
            gen_model=os.environ.get("RUNPOD_GEN_MODEL", tier_gen),
            completion_model=os.environ.get("RUNPOD_COMPLETION_MODEL", "pointr"),
        )
        local_ok = (os.environ.get("SOBA_LOCAL_GPU", "1") != "0"
                    and LocalGpuEngine.is_available())
        if gen and not comp and local_ok:
            local = LocalGpuEngine(
                completion_model=os.environ.get(
                    "SOBA_COMPLETION_MODEL", "patchcomplete"))
            return SplitEngine(completer=local, regenerator=runpod)
        return runpod
    if os.environ.get("SOBA_LOCAL_GPU", "1") != "0" and LocalGpuEngine.is_available():
        return LocalGpuEngine(
            gen_model=os.environ.get("SOBA_GEN_MODEL", tier_gen),
            # PatchComplete is the verdict completion pick (real-scan robust, runs
            # locally); override with SOBA_COMPLETION_MODEL=pointr/compc/etc.
            completion_model=os.environ.get("SOBA_COMPLETION_MODEL", "patchcomplete"),
        )
    return LocalEngine()
