"""Step 7 — ICP alignment of GENERATED meshes (plan §12, Phase 8).

A generative mesh is a unit cube: it knows neither where, how big, nor which
way up the object is. This recovers (R, s, t) from the observed cloud, fixing
the two v4 bugs the plan calls out:
  * scale must NOT come from a bbox diagonal of an incomplete cloud — the
    diagonal is exactly the metric most distorted by missing surfaces;
  * per-axis scale needs the mesh already ROTATED to world axes (rotation
    FIRST on scale-normalised clouds, THEN per-axis metric scale — fix G2).

Pipeline (plan A-E):
  A. source = 3000 pts sampled from the mesh; target = the observed cloud.
  B. rotation-first: FPFH + RANSAC on RMS-radius-normalised clouds (RMS radius
     is robust to missing surfaces, unlike a bbox diagonal).
  C. per-axis scale on the UN-normalised clouds (fix Z-B), axes with < 30%
     coverage (gap analysis) dropped as truncated; s = median of the rest.
  D. class-specific physical gates (config class_gates, fix R2); out of range
     -> RANSAC retry at 10x iterations -> class-prior scale.
  E. point-to-plane ICP refinement with s BAKED INTO the init (the estimator
     is rigid; it only nudges R, t).
Quality gates: fitness > 0.5, RMSE < 0.03 m, dims in class range, and the
SUPPORT-HEIGHT gate (fix Z-H): the aligned mesh bottom must sit within 0.15 m
of the object's OWN observed bottom — a cup on a table must pass, a mesh
floating away from where the object was seen must not.

Any failure falls back to the proven class-prior coarse alignment
(generative.coarse_align_to_cloud) with honest provenance:
  alignment_method "fpfh_icp" vs "coarse_aligned" (fix K3),
  scale_method "per_axis_median" vs "class_prior" (fix R5).
On the poorly-observed fragments that route generative, the gates OFTEN fail —
that is by design; the ICP path upgrades placement exactly when the
observation is good enough to trust.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from scene import lookup

log = logging.getLogger(__name__)

N_SOURCE_PTS = 3000
NORMAL_K = 20
RANSAC_CORR_DIST = 0.05        # normalised units (plan B)
RANSAC_ITERS = 4_000_000       # with early stopping
ICP_CORR_DIST_M = 0.02         # metres (plan E)
MIN_AXIS_COVERAGE = 0.30       # plan C axis-reliability bar
GATE_FITNESS = 0.5
GATE_RMSE_M = 0.03
GATE_SUPPORT_M = 0.15


@dataclass
class AlignResult:
    mesh: object                # aligned open3d TriangleMesh
    alignment_method: str       # "fpfh_icp" | "coarse_aligned"
    scale_method: str           # "per_axis_median" | "class_prior"
    fitness: float = 0.0
    rmse: float = float("inf")


# --- small geometry helpers ------------------------------------------------
def _rms_normalise(pts: np.ndarray):
    c = pts.mean(axis=0)
    r = float(np.sqrt(np.mean(np.sum((pts - c) ** 2, axis=1))))
    r = r if r > 1e-12 else 1.0
    return (pts - c) / r, c, r


def _pcd(pts: np.ndarray, *, normals: bool):
    import open3d as o3d

    p = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(pts))
    if normals:
        p.estimate_normals(o3d.geometry.KDTreeSearchParamKNN(NORMAL_K))
    return p


def axis_coverage(pts: np.ndarray, axis: int, bins: int = 20) -> float:
    """Occupied fraction of the cloud's own span along `axis` (gap analysis).

    A truncated axis (half a table seen) shows as a low occupied fraction —
    its extent is unreliable for scale (plan C)."""
    v = pts[:, axis]
    lo, hi = float(v.min()), float(v.max())
    if hi - lo < 1e-9:
        return 0.0
    hist, _ = np.histogram(v, bins=bins, range=(lo, hi))
    return float((hist > 0).sum()) / bins


def _fpfh_rotation(src_n: np.ndarray, tgt_n: np.ndarray, iters: int):
    """RANSAC global registration on normalised clouds -> 3x3 rotation R."""
    import open3d as o3d

    reg = o3d.pipelines.registration
    voxel = 0.05
    s = _pcd(src_n, normals=True)
    t = _pcd(tgt_n, normals=True)
    fs = reg.compute_fpfh_feature(s, o3d.geometry.KDTreeSearchParamHybrid(radius=0.25, max_nn=100))
    ft = reg.compute_fpfh_feature(t, o3d.geometry.KDTreeSearchParamHybrid(radius=0.25, max_nn=100))
    res = reg.registration_ransac_based_on_feature_matching(
        s, t, fs, ft, True, RANSAC_CORR_DIST,
        reg.TransformationEstimationPointToPoint(False), 3,
        [reg.CorrespondenceCheckerBasedOnEdgeLength(0.9),
         reg.CorrespondenceCheckerBasedOnDistance(RANSAC_CORR_DIST)],
        reg.RANSACConvergenceCriteria(iters, 0.999))
    R = np.asarray(res.transformation)[:3, :3].copy()
    # guard against a degenerate (reflective / near-singular) estimate
    if abs(np.linalg.det(R) - 1.0) > 0.1:
        return None
    return R


def _dims_ok(extents_world: np.ndarray, coco_class: str | None,
             config_path: str) -> bool:
    """Plan D/class gates: height = world-Y extent, width = max horizontal."""
    gates = lookup.load_config(config_path).get("class_gates", {})
    g = gates.get((coco_class or "").lower()) or gates.get(coco_class or "") \
        or gates.get("default", {"height": [0.05, 2.5], "width": [0.05, 2.5]})
    h = float(extents_world[1])
    w = float(max(extents_world[0], extents_world[2]))
    if "diameter" in g:
        lo, hi = g["diameter"]
        return lo <= max(h, w) <= hi
    ok = True
    if "height" in g:
        ok &= g["height"][0] <= h <= g["height"][1]
    if "width" in g:
        ok &= g["width"][0] <= w <= g["width"][1]
    return bool(ok)


def _coarse_fallback(mesh, cloud, coco_class) -> AlignResult:
    from reconstruction import generative  # lazy: generative also imports us

    aligned = generative.coarse_align_to_cloud(mesh, cloud, coco_class, clean=False)
    return AlignResult(mesh=aligned, alignment_method="coarse_aligned",
                       scale_method="class_prior")


# --- the Step-7 pipeline -----------------------------------------------------
def _attempt(mesh, cloud: np.ndarray, coco_class, config_path, iters):
    """One A-E pass. Returns (AlignResult, gates_passed) or None on hard fail."""
    import open3d as o3d

    reg = o3d.pipelines.registration

    # A. the two clouds
    src_pcd = mesh.sample_points_uniformly(N_SOURCE_PTS)
    src = np.asarray(src_pcd.points)
    tgt = np.asarray(cloud, dtype=np.float64)

    # B. rotation on RMS-normalised clouds (scale-free)
    src_n, src_c, _ = _rms_normalise(src)
    tgt_n, tgt_c, _ = _rms_normalise(tgt)
    R = _fpfh_rotation(src_n, tgt_n, iters)
    if R is None:
        return None

    # C. per-axis metric scale on the UN-normalised clouds (fix Z-B)
    src_rot = (src - src_c) @ R.T
    src_ext = src_rot.max(axis=0) - src_rot.min(axis=0)
    tgt_ext = tgt.max(axis=0) - tgt.min(axis=0)
    scales, used = [], []
    for ax in range(3):
        if src_ext[ax] < 1e-9:
            continue
        if axis_coverage(tgt, ax) < MIN_AXIS_COVERAGE:
            continue  # truncated axis -> unreliable extent
        scales.append(float(tgt_ext[ax] / src_ext[ax]))
        used.append(ax)
    if not scales:
        return None
    s = float(np.median(scales))
    scale_method = "per_axis_median"

    # D. class gates on the scaled world dims
    world_ext = src_ext * s
    if not _dims_ok(world_ext, coco_class, config_path):
        return None  # caller retries at 10x, then class-prior

    # E. rigid point-to-plane refinement, s baked into the init
    T = np.eye(4)
    T[:3, :3] = R * s
    T[:3, 3] = tgt_c - (R * s) @ src_c
    src_scaled = _pcd(src, normals=True)
    tgt_pcd = _pcd(tgt, normals=True)
    icp = reg.registration_icp(
        src_scaled, tgt_pcd, ICP_CORR_DIST_M, T,
        reg.TransformationEstimationPointToPlane(),
        reg.ICPConvergenceCriteria(max_iteration=50, relative_fitness=1e-6))
    T_final = np.asarray(icp.transformation)

    aligned = o3d.geometry.TriangleMesh(mesh)
    aligned.transform(T_final)
    aligned.compute_vertex_normals()

    # quality + support-height gates (fix Z-H: vs the object's OWN bottom)
    ok = (icp.fitness > GATE_FITNESS and icp.inlier_rmse < GATE_RMSE_M)
    bottom = float(np.asarray(aligned.vertices)[:, 1].min())
    ok &= abs(bottom - float(tgt[:, 1].min())) < GATE_SUPPORT_M
    a_ext = (np.asarray(aligned.get_axis_aligned_bounding_box().max_bound)
             - np.asarray(aligned.get_axis_aligned_bounding_box().min_bound))
    ok &= _dims_ok(a_ext, coco_class, config_path)

    return AlignResult(mesh=aligned, alignment_method="fpfh_icp",
                       scale_method=scale_method,
                       fitness=float(icp.fitness),
                       rmse=float(icp.inlier_rmse)), bool(ok)


def align(mesh, cloud, coco_class: str | None = None, *,
          config_path: str = str(lookup._DEFAULT_CONFIG)) -> AlignResult:
    """Align a GENERATED (unit-cube) mesh to the observed cloud (plan §12).

    Never raises: any failure lands on the class-prior coarse alignment with
    provenance ("coarse_aligned"/"class_prior"), so a bad registration can
    only ever cost quality, not the object.
    """
    cloud = np.asarray(cloud, dtype=np.float64).reshape(-1, 3)
    if len(mesh.vertices) == 0 or len(cloud) < 50:
        return _coarse_fallback(mesh, cloud, coco_class)
    try:
        got = _attempt(mesh, cloud, coco_class, config_path, RANSAC_ITERS)
        if got is not None:
            res, ok = got
            if ok:
                return res
        # plan D/gates: one retry at 10x iterations
        got = _attempt(mesh, cloud, coco_class, config_path, RANSAC_ITERS * 10)
        if got is not None:
            res, ok = got
            if ok:
                return res
    except Exception:
        log.warning("icp_align failed -> coarse fallback", exc_info=True)
    return _coarse_fallback(mesh, cloud, coco_class)
