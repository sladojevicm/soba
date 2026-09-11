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

from pathlib import Path
from typing import Protocol

import numpy as np

from perception.bundle import PerceptionBundle

# Tier 4 uses MASt3R by DECISION (user, 2026-07-05): ORB-SLAM3 will not be
# integrated — its loop closure was only ever justified if MASt3R drifted,
# and the heavy C++ build (Pangolin/DBoW2/g2o, no Python bindings) isn't
# worth it. The old plan's "Phase 14" is closed.
POSE_METHODS = {1: "odometry", 2: "mast3r", 3: "mast3r", 4: "mast3r"}


def method_for_tier(tier: int) -> str:
    try:
        return POSE_METHODS[tier]
    except KeyError as exc:
        raise ValueError(f"unknown tier {tier!r}; expected 1-4") from exc


def compose_poses(relatives: list[np.ndarray], T0: np.ndarray | None = None) -> list[np.ndarray]:
    """Chain frame-to-frame transforms into absolute world poses.

    T_world[0]   = T0 (identity by default — world is the first camera)
    T_world[i+1] = T_world[i] @ relatives[i]

    With the T_world_camera convention (P_world = T_world_camera @ P_camera),
    `relatives[i]` must be the point transform that maps frame i+1's camera
    coords into frame i's camera coords (P_i = relatives[i] @ P_{i+1}) — i.e. the
    pose of camera i+1 expressed in camera i. This is exactly what Open3D's
    compute_rgbd_odometry(source=i+1, target=i) returns, so RgbdOdometry feeds
    that output in DIRECTLY (no inverse). There are len(relatives)+1 poses.
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
            ok, T_prev_cur, _ = o3d.pipelines.odometry.compute_rgbd_odometry(
                cur, prev, pinhole, np.eye(4),
                o3d.pipelines.odometry.RGBDOdometryJacobianFromHybridTerm(),
                option,
            )
            # Open3D returns the point transform source->target: it maps `cur`
            # (source) camera coords into `prev` (target) coords, i.e.
            #   P_prev = T_prev_cur @ P_cur
            # (verified by the Open3D tutorial's source.transform(T) aligning the
            # source cloud onto the target). The world chain is therefore
            #   T_world[cur] = T_world[prev] @ T_prev_cur,
            # so compose_poses consumes this DIRECTLY — inverting it here mirrors
            # the whole trajectory through the origin (the bug this replaces).
            relatives.append(T_prev_cur if ok else np.eye(4))
            prev = cur

        return compose_poses(relatives)


def interpolate_poses(all_fids: list[int], anchor_fids: list[int],
                      anchor_poses: np.ndarray) -> list[np.ndarray]:
    """SE(3)-interpolate anchor poses onto every frame id.

    MASt3R runs on every Nth frame (Method B); the in-between frames get slerp
    rotation + lerp translation between their two anchors. Frames outside the
    anchor range clamp to the nearest anchor.
    """
    from scipy.spatial.transform import Rotation, Slerp

    anchor_fids = list(anchor_fids)
    Rs = Rotation.from_matrix(anchor_poses[:, :3, :3])
    slerp = Slerp(np.asarray(anchor_fids, dtype=float), Rs) if len(anchor_fids) > 1 else None
    ts = anchor_poses[:, :3, 3]
    out = []
    lo, hi = anchor_fids[0], anchor_fids[-1]
    for fid in all_fids:
        f = float(np.clip(fid, lo, hi))
        T = np.eye(4)
        if slerp is None:
            T[:3, :3] = anchor_poses[0][:3, :3]
            T[:3, 3] = ts[0]
        else:
            T[:3, :3] = slerp(f).as_matrix()
            T[:3, 3] = np.array([np.interp(f, anchor_fids, ts[:, k]) for k in range(3)])
        out.append(T)
    return out


def solve_metric_scale(pred_depths, sensor_depths_m) -> float:
    """Fix M1: one global scale from predicted vs metric sensor depth.

    MASt3R's metric checkpoint is roughly metric already, but 'roughly' is not
    good enough to fuse against a real depth sensor — solve the residual scale
    as the median over frames of the median per-pixel sensor/pred ratio.
    Returns 1.0 when nothing valid overlaps (degenerate input)."""
    import cv2

    ratios = []
    for pred, sens in zip(pred_depths, sensor_depths_m):
        pred = np.asarray(pred, dtype=np.float64)
        sens = np.asarray(sens, dtype=np.float64)
        if sens.shape != pred.shape:
            sens = cv2.resize(sens, (pred.shape[1], pred.shape[0]),
                              interpolation=cv2.INTER_NEAREST)
        m = (pred > 1e-6) & (sens > 1e-6)
        if m.sum() >= 100:
            ratios.append(float(np.median(sens[m] / pred[m])))
    return float(np.median(ratios)) if ratios else 1.0


class Mast3rEstimator:
    """Tiers 2-3 primary. Globally consistent poses + dense cloud in one pass.

    Runs MASt3R (metric checkpoint) on every Nth frame (Method B, default
    stride 3), sliding-window pairs -> dust3r global alignment -> cam2world
    poses; the residual metric scale is solved against the depth sensor
    (fix M1) and skipped frames are SE(3)-interpolated. World = first frame.

    Env knobs: SOBA_MAST3R_HOME (repo, default ~/soba/mast3r),
    SOBA_MAST3R_STRIDE (default config frame_sample_stride = 3),
    SOBA_MAST3R_MAX_IMAGES (memory guard, default 24 — the stride grows to
    fit; global alignment holds every pairwise pointmap in memory, and ~34
    images / ~130 pairs at 512 res already exhausts an 8 GB GPU AND a
    similarly-sized host RAM on the CPU fallback).
    """

    CKPT = "MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth"

    def __init__(self, stride: int | None = None, device: str | None = None):
        import os
        self.home = Path(os.environ.get(
            "SOBA_MAST3R_HOME",
            str(Path.home() / "soba/mast3r")))
        self.stride = stride or int(os.environ.get("SOBA_MAST3R_STRIDE", "3"))
        self.max_images = int(os.environ.get("SOBA_MAST3R_MAX_IMAGES", "24"))
        self.device = device

    def _add_paths(self):
        import sys
        for p in (self.home, self.home / "dust3r", self.home / "dust3r" / "croco"):
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))

    def _load(self):
        import torch
        from mast3r.model import AsymmetricMASt3R

        dev = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
        ckpt = self.home / "checkpoints" / self.CKPT
        model = AsymmetricMASt3R.from_pretrained(str(ckpt)).to(dev).eval()
        return model, dev

    def estimate(self, bundle: PerceptionBundle) -> list[np.ndarray]:
        self._add_paths()  # the repo isn't a package; imports need its dirs
        import torch
        from dust3r.cloud_opt import GlobalAlignerMode, global_aligner
        from dust3r.image_pairs import make_pairs
        from dust3r.inference import inference
        from dust3r.utils.image import load_images

        all_fids = list(bundle.iter_frame_ids())
        if not all_fids:
            return []
        stride = self.stride
        while len(all_fids[::stride]) > self.max_images:
            stride += 1  # VRAM guard: global alignment holds all pairs on GPU
        sampled = all_fids[::stride]
        if sampled[-1] != all_fids[-1]:
            sampled.append(all_fids[-1])

        model, dev = self._load()
        imgs = load_images([str(bundle.rgb_path(f)) for f in sampled],
                           size=512, verbose=False)
        pairs = make_pairs(imgs, scene_graph="swin-3", prefilter=None,
                           symmetrize=True)
        out = inference(pairs, model, dev, batch_size=1, verbose=False)
        # free the 2.6 GB network BEFORE global alignment — the optimizer holds
        # every pairwise pointmap on the GPU and the two together OOM an 8 GB
        # card; alignment needs the predictions, not the network.
        del model
        if dev == "cuda":
            torch.cuda.empty_cache()

        def _align(device):
            scene = global_aligner(out, device=device,
                                   mode=GlobalAlignerMode.PointCloudOptimizer,
                                   verbose=False)
            scene.compute_global_alignment(init="mst", niter=300,
                                           schedule="cosine", lr=0.01)
            return scene

        try:
            scene = _align(dev)
        except torch.OutOfMemoryError:
            torch.cuda.empty_cache()
            import logging
            logging.getLogger(__name__).warning(
                "MASt3R global alignment OOM on %s -> retrying on CPU "
                "(slower, same result)", dev)
            scene = _align("cpu")
        cam2world = scene.get_im_poses().detach().cpu().numpy().astype(np.float64)
        pred_depths = [d.detach().cpu().numpy() for d in scene.get_depthmaps()]
        del out, scene
        if dev == "cuda":
            torch.cuda.empty_cache()

        # fix M1: residual metric scale vs the sensor, applied to translations
        sensor = [np.asarray(bundle.read_depth_mm(f), dtype=np.float64) / 1000.0
                  for f in sampled]
        s = solve_metric_scale(pred_depths, sensor)
        cam2world[:, :3, 3] *= s

        # world = first frame (the bundle's convention)
        T0_inv = np.linalg.inv(cam2world[0])
        anchors = np.array([T0_inv @ T for T in cam2world])
        return interpolate_poses(all_fids, sampled, anchors)


def make_estimator(tier: int) -> PoseEstimator:
    return {
        "odometry": RgbdOdometry,
        "mast3r": Mast3rEstimator,
    }[method_for_tier(tier)]()


def estimate_poses(bundle: PerceptionBundle, tier: int, *, write: bool = True) -> list[np.ndarray]:
    """Estimate per-frame poses for a tier and (optionally) write poses.json."""
    poses = make_estimator(tier).estimate(bundle)
    if write:
        bundle.write_poses(poses)
    return poses
