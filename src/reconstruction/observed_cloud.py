"""Observed point cloud — Step 4 Part A (module observed_cloud.py).

Back-projects an object's masked depth pixels into world space using the
per-frame poses and accumulates them across frames. Needs only depth + masks +
poses, so it runs in EVERY tier (including Tier 1, which has no TSDF) — it is the
tier-independent scale/placement reference for ICP and the cloud the Step 5 gate
scores (fix G3 / V1).

Pure numpy: no Open3D needed for the back-projection itself.
"""

from __future__ import annotations

import numpy as np

DEFAULT_DEPTH_SCALE = 1000.0  # millimetres -> metres
DEFAULT_MIN_MM = 400  # 0.40 m  (fix G1: thresholds are in millimetres)
DEFAULT_MAX_MM = 8000  # 8.00 m


def backproject(
    depth_mm: np.ndarray,
    K: np.ndarray,
    *,
    mask: np.ndarray | None = None,
    min_mm: int = DEFAULT_MIN_MM,
    max_mm: int = DEFAULT_MAX_MM,
) -> np.ndarray:
    """Back-project a depth map to an (N, 3) camera-space point cloud (metres).

    Invalid depth (0, out of [min_mm, max_mm]) and masked-out pixels are dropped.
    """
    depth_mm = np.asarray(depth_mm)
    h, w = depth_mm.shape[:2]

    valid = (depth_mm >= min_mm) & (depth_mm <= max_mm)
    if mask is not None:
        valid &= np.asarray(mask) > 0
    if not valid.any():
        return np.empty((0, 3), dtype=np.float64)

    vs, us = np.nonzero(valid)
    z = depth_mm[vs, us].astype(np.float64) / DEFAULT_DEPTH_SCALE  # metres
    fx, fy = K[0, 0], K[1, 1]
    cx, cy = K[0, 2], K[1, 2]
    x = (us.astype(np.float64) - cx) / fx * z
    y = (vs.astype(np.float64) - cy) / fy * z
    return np.stack([x, y, z], axis=1)


def to_world(points_cam: np.ndarray, T_world_camera: np.ndarray) -> np.ndarray:
    """Apply a 4x4 camera->world transform to (N, 3) points."""
    if points_cam.size == 0:
        return points_cam
    T = np.asarray(T_world_camera, dtype=np.float64)
    homog = np.concatenate([points_cam, np.ones((len(points_cam), 1))], axis=1)
    return (homog @ T.T)[:, :3]


def voxel_downsample(points: np.ndarray, voxel_size: float) -> np.ndarray:
    """Keep one representative point per occupied voxel (cell centroid)."""
    if points.size == 0 or voxel_size <= 0:
        return points
    keys = np.floor(points / voxel_size).astype(np.int64)
    _, inverse = np.unique(keys, axis=0, return_inverse=True)
    # numpy 2.0 returned a 2-D `inverse` for axis-wise unique; flatten so the
    # add.at / bincount below behave identically across numpy versions.
    inverse = np.asarray(inverse).reshape(-1)
    n = int(inverse.max()) + 1
    sums = np.zeros((n, 3), dtype=np.float64)
    np.add.at(sums, inverse, points)
    counts = np.bincount(inverse, minlength=n).reshape(-1, 1)
    return sums / counts


def accumulate_object_cloud(
    frames: list[tuple[np.ndarray, np.ndarray, np.ndarray]],
    K: np.ndarray,
    *,
    voxel_size: float = 0.005,
    min_mm: int = DEFAULT_MIN_MM,
    max_mm: int = DEFAULT_MAX_MM,
) -> np.ndarray:
    """Build one object's world-space cloud from (depth_mm, mask, T_world_cam).

    Returns the voxel-downsampled (M, 3) RAW observed cloud — what the Step 5
    gate scores. The MERGED cloud (raw + MASt3R dense) is assembled separately.
    """
    chunks: list[np.ndarray] = []
    for depth_mm, mask, T in frames:
        cam = backproject(depth_mm, K, mask=mask, min_mm=min_mm, max_mm=max_mm)
        if cam.size:
            chunks.append(to_world(cam, T))
    if not chunks:
        return np.empty((0, 3), dtype=np.float64)
    return voxel_downsample(np.concatenate(chunks, axis=0), voxel_size)


def world_centroid(
    depth_mm: np.ndarray,
    mask: np.ndarray,
    K: np.ndarray,
    T_world_camera: np.ndarray,
    *,
    min_mm: int = DEFAULT_MIN_MM,
    max_mm: int = DEFAULT_MAX_MM,
) -> np.ndarray | None:
    """World-space centroid of a masked object in one frame (fix Z-A).

    Used by the moving-object check, which must compare metres-vs-metres in the
    world frame — a 2D mask-centroid shift wrongly drops static objects under a
    camera pan (parallax). Returns None if no valid masked depth this frame.
    """
    cam = backproject(depth_mm, K, mask=mask, min_mm=min_mm, max_mm=max_mm)
    if cam.size == 0:
        return None
    return to_world(cam, T_world_camera).mean(axis=0)


def provisional_ground_y(clouds: list[np.ndarray], offset_m: float = 0.02) -> float:
    """ground_y_prov = (min Y over all observed clouds) - offset (fix R4).

    Uses the gravity-up (Y) axis from Step 3. Step 7's support-height gate uses
    this; Step 10 computes the final RANSAC floor.
    """
    mins = [c[:, 1].min() for c in clouds if c.size]
    if not mins:
        raise ValueError("no non-empty clouds to estimate a provisional ground plane")
    return float(min(mins) - offset_m)
