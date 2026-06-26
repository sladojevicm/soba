"""Camera pose estimation — Step 3 (module slam.py).

One interface, three tier-selected methods (the plan's METHOD HIERARCHY):

* Tier 1   — RGB-D odometry (Open3D, frame-to-frame; drifts, acceptable for
             Tier-1 placement). IMPLEMENTED here.
* Tier 2-3 — MASt3R (globally consistent, no drift; requires a metric-scale
             solve against the depth sensor, fix M1). Interface defined; the
             network call is wired in when the model weights are available.
* Tier 4   — ORB-SLAM3 (visual-inertial, loop closure). Built last (Phase 14),
             only if MASt3R shows unacceptable drift.

Pose convention: T_world_camera[N], 4x4, world = first-frame camera, Y up,
metres, right-handed. P_world = T_world_camera @ P_camera.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np

from perception.bundle import PerceptionBundle

POSE_METHODS = {1: "odometry", 2: "mast3r", 3: "mast3r", 4: "orbslam3"}


def method_for_tier(tier: int) -> str:
    try:
        return POSE_METHODS[tier]
    except KeyError as exc:
        raise ValueError(f"unknown tier {tier!r}; expected 1-4") from exc


def compose_poses(relatives: list[np.ndarray], T0: np.ndarray | None = None) -> list[np.ndarray]:
    """Chain frame-to-frame transforms into absolute world poses.

    T_world[0]   = T0 (identity by default — world is the first camera)
    T_world[N+1] = T_world[N] @ T_relative[N->N+1]

    `relatives[i]` is the relative transform mapping frame i into frame i+1's
    camera, so there are len(relatives)+1 absolute poses.
    """
    T = np.eye(4) if T0 is None else np.asarray(T0, dtype=np.float64)
    poses = [T]
    for i, rel in enumerate(relatives):
        rel = np.asarray(rel, dtype=np.float64)
        if rel.shape != (4, 4):
            raise ValueError(f"relative {i} must be 4x4, got {rel.shape}")
        T = T @ rel
        poses.append(T)
    return poses


class PoseEstimator(Protocol):
    def estimate(self, bundle: PerceptionBundle) -> list[np.ndarray]: ...


class RgbdOdometry:
    """Tier-1 RGB-D odometry via Open3D (lazy import).

    Frame-to-frame only: error accumulates with nothing to correct it. Fine for
    Tier 1, which places generative meshes from per-object depth clouds and never
    fuses a TSDF, so coarse poses are acceptable.
    """

    def __init__(self, depth_scale: float = 1000.0, depth_max_m: float = 8.0):
        self.depth_scale = depth_scale
        self.depth_max_m = depth_max_m

    def estimate(self, bundle: PerceptionBundle) -> list[np.ndarray]:
        import open3d as o3d  # lazy: only Tier 1 at runtime needs it

        K = bundle.intrinsics
        frame_ids = list(bundle.iter_frame_ids())
        if not frame_ids:
            return []

        h, w = bundle.read_depth_mm(frame_ids[0]).shape[:2]
        pinhole = o3d.camera.PinholeCameraIntrinsic(
            w, h, K.fx, K.fy, K.cx, K.cy
        )

        def rgbd(fid: int):
            color = o3d.geometry.Image(np.ascontiguousarray(bundle.read_rgb(fid)))
            depth = o3d.geometry.Image(bundle.read_depth_mm(fid))
            return o3d.geometry.RGBDImage.create_from_color_and_depth(
                color, depth,
                depth_scale=self.depth_scale,
                depth_trunc=self.depth_max_m,
                convert_rgb_to_intensity=False,
            )

        relatives: list[np.ndarray] = []
        prev = rgbd(frame_ids[0])
        option = o3d.pipelines.odometry.OdometryOption()
        for fid in frame_ids[1:]:
            cur = rgbd(fid)
            ok, T_cur_prev, _ = o3d.pipelines.odometry.compute_rgbd_odometry(
                cur, prev, pinhole, np.eye(4),
                o3d.pipelines.odometry.RGBDOdometryJacobianFromHybridTerm(),
                option,
            )
            # compute_rgbd_odometry returns the transform from `cur` to `prev`;
            # the relative prev->cur world step is its inverse.
            relatives.append(np.linalg.inv(T_cur_prev) if ok else np.eye(4))
            prev = cur

        return compose_poses(relatives)


class Mast3rEstimator:
    """Tiers 2-3 primary. Globally consistent poses + dense cloud in one pass.

    Requires the metric-scale solve (fix M1): MASt3R is scale-ambiguous, so a
    single global scale is solved against the metric depth sensor and applied to
    every pose translation and dense-cloud point before any fusion. Wired in when
    weights are available; the scale solve lives in this estimator.
    """

    def estimate(self, bundle: PerceptionBundle) -> list[np.ndarray]:
        raise NotImplementedError(
            "MASt3R estimator requires model weights; build/test it on TUM "
            "against ground-truth poses (Build Order Phase 3)."
        )


class OrbSlam3Estimator:
    """Tier 4. Visual-inertial SLAM, loop closure + bundle adjustment.

    SPARSE (feature-based): poses + a sparse landmark map, NOT a dense cloud
    (fix S1). Complex C++ build; constructed last (Phase 14), only if proven
    necessary by MASt3R drift on Tier-3 data.
    """

    def estimate(self, bundle: PerceptionBundle) -> list[np.ndarray]:
        raise NotImplementedError(
            "ORB-SLAM3 is built last (Phase 14), only if MASt3R drifts."
        )


def make_estimator(tier: int) -> PoseEstimator:
    return {
        "odometry": RgbdOdometry,
        "mast3r": Mast3rEstimator,
        "orbslam3": OrbSlam3Estimator,
    }[method_for_tier(tier)]()


def estimate_poses(bundle: PerceptionBundle, tier: int, *, write: bool = True) -> list[np.ndarray]:
    """Estimate per-frame poses for a tier and (optionally) write poses.json."""
    poses = make_estimator(tier).estimate(bundle)
    if write:
        bundle.write_poses(poses)
    return poses
