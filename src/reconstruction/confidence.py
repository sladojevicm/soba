"""Geometry confidence gate — Step 5 (module confidence.py), Build Order Phase 6.

Decides, per object, whether the camera saw ENOUGH of it to trust a TSDF mesh
(strategy "tsdf", skip the cloud GPU) or whether it must be completed by the
generative model (strategy "generative"). The gate is BINARY on purpose (a
merged TSDF-front/generative-back mesh would need stitching that breaks volume
and CoACD).

WHAT IT SCORES (fix V1): the RAW back-projected observed cloud from Step 4 Part A
— never the TSDF-merged cloud. Scoring TSDF completeness on a cloud that already
contains the TSDF surface is circular.

TWO METRICS, both must clear the per-tier bar:
  * ANGULAR COVERAGE — the largest angle between any two view directions
    (object centroid -> camera position). Pure viewpoint geometry, so it is
    SHAPE-INDEPENDENT (fix Z-U) and is the primary signal. ~20-30 deg for a
    front-only bookshelf, up to ~180 deg for a fully walked-around object.
  * SURFACE COMPLETENESS — observed patch area / oriented-bbox surface area,
    clamped to [0,1] (fix V2). The patch area is estimated by ball-pivoting (or
    alpha-shape fallback) over the raw cloud — a SCORING ARTEFACT, thrown away,
    never the physics geometry. This metric is MONOTONIC in coverage but its
    absolute value is SHAPE-DEPENDENT (a ball tops out near ~0.5, a box near
    ~1.0), so the thresholds are EMPIRICAL knobs, not coverage percentages
    (fix Z-U). Because the gate ANDs the two, a poorly-observed flat object
    (low angular coverage) cannot pass on a deceptively high completeness alone.

Thresholds live in config/pipeline.yaml (tiers.<n>.gate); the gate voxel size is
that tier's voxel_size_m. Tier 1 is all-generative (no gate).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

TSDF = "tsdf"
GENERATIVE = "generative"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG = _REPO_ROOT / "config" / "pipeline.yaml"


# --- config -------------------------------------------------------------
@lru_cache(maxsize=4)
def _load_config(path: str) -> dict:
    import yaml

    with open(path) as fh:
        return yaml.safe_load(fh)


def tier_params(tier: int, config_path: Path | str = _DEFAULT_CONFIG) -> dict:
    """Per-tier gate params: {angular_deg, completeness, voxel_size_m, tsdf}.

    Tier 1 has tsdf False and no gate block — every object is generative.
    """
    cfg = _load_config(str(config_path))
    t = cfg["tiers"][tier]
    gate = t.get("gate") or {}
    return {
        "tsdf": bool(t.get("tsdf", False)),
        "angular_deg": gate.get("angular_deg"),
        "completeness": gate.get("completeness"),
        "voxel_size_m": t.get("voxel_size_m"),
    }


# --- metrics ------------------------------------------------------------
def angular_coverage_deg(centroid: np.ndarray, cam_positions: np.ndarray) -> float:
    """Largest angle (degrees) between any two centroid->camera view directions.

    `cam_positions` is (N, 3) camera world positions for the frames the object
    was seen. Returns 0 for fewer than two usable views.
    """
    centroid = np.asarray(centroid, dtype=np.float64).reshape(3)
    cams = np.asarray(cam_positions, dtype=np.float64).reshape(-1, 3)
    dirs = cams - centroid
    norms = np.linalg.norm(dirs, axis=1)
    dirs = dirs[norms > 1e-9]
    norms = norms[norms > 1e-9]
    if len(dirs) < 2:
        return 0.0
    units = dirs / norms[:, None]
    cos = np.clip(units @ units.T, -1.0, 1.0)
    return float(np.degrees(np.arccos(cos)).max())


def surface_completeness(
    cloud: np.ndarray, *, voxel_size: float
) -> tuple[float, float, float]:
    """(completeness_ratio, observed_area_m2, bbox_area_m2) for the raw cloud.

    observed area = summed triangle area of a ball-pivoting patch mesh over the
    raw cloud (alpha-shape fallback when ball-pivoting yields nothing); bbox area
    = surface area of the oriented bounding box. Ratio clamped to [0,1] (fix V2).
    The patch mesh is a scoring artefact only — discarded, never physics geometry.
    """
    import open3d as o3d

    cloud = np.asarray(cloud, dtype=np.float64)
    if len(cloud) < 4:
        return 0.0, 0.0, 0.0

    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(cloud))
    pcd.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamKNN(knn=20))

    radii = o3d.utility.DoubleVector([1.5 * voxel_size, 3.0 * voxel_size])
    mesh = o3d.geometry.TriangleMesh.create_from_point_cloud_ball_pivoting(pcd, radii)
    observed_area = float(mesh.get_surface_area()) if len(mesh.triangles) else 0.0
    if observed_area == 0.0:  # sparse cloud -> alpha-shape fallback
        try:
            alpha = o3d.geometry.TriangleMesh.create_from_point_cloud_alpha_shape(
                pcd, alpha=3.0 * voxel_size
            )
            observed_area = float(alpha.get_surface_area()) if len(alpha.triangles) else 0.0
        except Exception:
            observed_area = 0.0

    obb = pcd.get_oriented_bounding_box()
    dx, dy, dz = (float(e) for e in obb.extent)
    bbox_area = 2.0 * (dx * dy + dy * dz + dx * dz)
    ratio = min(observed_area / bbox_area, 1.0) if bbox_area > 0 else 0.0
    return ratio, observed_area, bbox_area


# --- the gate -----------------------------------------------------------
def gate(
    cloud: np.ndarray,
    cam_positions: np.ndarray,
    *,
    angular_deg: float,
    completeness: float,
    voxel_size: float,
) -> dict:
    """Score one object's raw cloud and route it. Both metrics must clear the bar.

    Returns the Step-5 confidence.json payload:
      {"angular_coverage_deg", "completeness_ratio", "strategy"}
    """
    cloud = np.asarray(cloud, dtype=np.float64)
    if len(cloud) == 0:
        return {
            "angular_coverage_deg": 0.0,
            "completeness_ratio": 0.0,
            "strategy": GENERATIVE,
        }
    ang = angular_coverage_deg(cloud.mean(axis=0), cam_positions)
    comp, _, _ = surface_completeness(cloud, voxel_size=voxel_size)
    strategy = TSDF if (ang > angular_deg and comp > completeness) else GENERATIVE
    return {
        "angular_coverage_deg": round(ang, 1),
        "completeness_ratio": round(comp, 3),
        "strategy": strategy,
    }


def gate_object(
    cloud: np.ndarray,
    cam_positions: np.ndarray,
    tier: int,
    *,
    config_path: Path | str = _DEFAULT_CONFIG,
) -> dict:
    """Gate one object at a given tier, reading thresholds from pipeline.yaml.

    Tier 1 (no TSDF) routes everything to the generative model without scoring.
    """
    p = tier_params(tier, config_path)
    if not p["tsdf"]:  # Tier 1: all generative (plan §10)
        return {
            "angular_coverage_deg": None,
            "completeness_ratio": None,
            "strategy": GENERATIVE,
        }
    return gate(
        cloud,
        cam_positions,
        angular_deg=p["angular_deg"],
        completeness=p["completeness"],
        voxel_size=p["voxel_size_m"],
    )
