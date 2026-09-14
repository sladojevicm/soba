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

from perception.bundle import Intrinsics, Manifest, PerceptionBundle

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
    ratios = []
    for pred, sens in zip(pred_depths, sensor_depths_m):
        pred = np.asarray(pred, dtype=np.float64)
        sens = np.asarray(sens, dtype=np.float64)
        if sens.shape != pred.shape:
            import cv2  # only needed to resample; keeps the equal-shape path cv2-free

            sens = cv2.resize(sens, (pred.shape[1], pred.shape[0]),
                              interpolation=cv2.INTER_NEAREST)
        m = (pred > 1e-6) & (sens > 1e-6)
        if m.sum() >= 100:
            ratios.append(float(np.median(sens[m] / pred[m])))
    return float(np.median(ratios)) if ratios else 1.0


# --- MASt3R dense-output export (RGB-only ingest, step 1) --------------------
# MASt3R needs only RGB; the metric-scale solve is the one place that reads the
# depth sensor. When a bundle has no depth.png (a plain phone video), the scale
# comes from the metric checkpoint alone and the dense depthmaps MASt3R already
# computes are exported so the rest of the pipeline (observed cloud, gate,
# TSDF, placement, ground) runs unchanged on the anchor frames.

MAST3R_LOAD_SIZE = 512


def dust3r_crop_geometry(width: int, height: int, size: int = MAST3R_LOAD_SIZE
                         ) -> tuple[int, int, int, int, int, int]:
    """Replicates dust3r.utils.image.load_images(size=512) exactly: resize so
    the long edge is `size` (PIL rounding), then centre-crop to an even
    multiple of 16 via halfw/halfh multiples of 8.

    Returns (rw, rh, x0, y0, cw, ch): the resized image size, the crop origin
    inside it and the crop size. The MASt3R depthmap, confidence map, focal and
    principal point all live in that (ch, cw) crop frame."""
    if width <= 0 or height <= 0:
        raise ValueError(f"image size must be positive, got {width}x{height}")
    S = max(width, height)
    rw = int(round(width * size / S))
    rh = int(round(height * size / S))
    cx, cy = rw // 2, rh // 2
    halfw, halfh = ((2 * cx) // 16) * 8, ((2 * cy) // 16) * 8
    return rw, rh, cx - halfw, cy - halfh, 2 * halfw, 2 * halfh


def crop_map_to_frame(crop: np.ndarray, width: int, height: int,
                      size: int = MAST3R_LOAD_SIZE) -> np.ndarray:
    """Map a per-pixel map from MASt3R's crop frame back onto the full RGB
    frame (width x height). Pixels the crop never covered are 0 (invalid for
    depth). Nearest-neighbour throughout so depth edges are not blended."""
    import cv2

    rw, rh, x0, y0, cw, ch = dust3r_crop_geometry(width, height, size)
    crop = np.asarray(crop, dtype=np.float64)
    if crop.shape != (ch, cw):
        crop = cv2.resize(crop, (cw, ch), interpolation=cv2.INTER_NEAREST)
    canvas = np.zeros((rh, rw), dtype=np.float64)
    canvas[y0:y0 + ch, x0:x0 + cw] = crop
    if (rw, rh) == (width, height):
        return canvas
    return cv2.resize(canvas, (width, height), interpolation=cv2.INTER_NEAREST)


def conf_to_u8(conf: np.ndarray) -> np.ndarray:
    """dust3r confidence (>= 1, exp-scaled) -> the bundle's uint8 conf.png.

    u8 = 255 * (1 - 1/conf): conf 1 -> 0, conf 2 -> 127, conf 3 -> 170, ->255.
    tsdf.fuse's optional gate zeroes depth where conf < 150, i.e. below about
    conf 2.4; dust3r's own default min_conf_thr is 3, so the two agree on what
    "trust this pixel" means."""
    conf = np.asarray(conf, dtype=np.float64)
    u8 = 255.0 * (1.0 - 1.0 / np.maximum(conf, 1.0))
    return np.clip(np.round(u8), 0, 255).astype(np.uint8)


def resolve_metric_scale(bundle: PerceptionBundle, sampled: list[int],
                         pred_depths: list[np.ndarray]) -> tuple[float, str]:
    """Fix M1 with a guard: solve the residual scale against the depth sensor
    when every sampled frame has a depth.png, else keep the metric checkpoint's
    own scale (1.0) and say so. Returns (scale, "sensor" | "mast3r")."""
    import logging

    have_depth = all(bundle.depth_path(f).is_file() for f in sampled)
    if have_depth:
        sensor = [np.asarray(bundle.read_depth_mm(f), dtype=np.float64) / 1000.0
                  for f in sampled]
        return solve_metric_scale(pred_depths, sensor), "sensor"
    logging.getLogger(__name__).warning(
        "bundle has no depth.png for the sampled frames -> metric scale comes "
        "from MASt3R's metric checkpoint alone (unverified against a sensor; "
        "size sanity rests on config class_gates)")
    return 1.0, "mast3r"


def _to_np(x) -> np.ndarray:
    """torch tensor / list of tensors / array -> float64 numpy (duck-typed so
    the export path is testable without torch)."""
    if hasattr(x, "detach"):
        x = x.detach().cpu().numpy()
    return np.asarray(x, dtype=np.float64)


def scene_outputs(scene) -> dict:
    """Pull everything the export needs out of a dust3r global-alignment scene:
    cam2world (N,4,4), depthmaps [N x (h,w)], confs [N x (h,w)] or None,
    focals (N,) or None, principal points (N,2) or None. Duck-typed: any object
    with get_im_poses/get_depthmaps (and optionally im_conf, get_focals,
    get_principal_points) works, which is how the unit test drives it."""
    out = {
        "cam2world": _to_np(scene.get_im_poses()),
        "depthmaps": [_to_np(d) for d in scene.get_depthmaps()],
        "confs": None, "focals": None, "principal_points": None,
    }
    im_conf = getattr(scene, "im_conf", None)
    if im_conf is not None:
        out["confs"] = [_to_np(c) for c in im_conf]
    if hasattr(scene, "get_focals"):
        out["focals"] = _to_np(scene.get_focals()).reshape(-1)
    if hasattr(scene, "get_principal_points"):
        out["principal_points"] = _to_np(scene.get_principal_points()).reshape(-1, 2)
    return out


def export_anchor_bundle(
    src: PerceptionBundle,
    out_root: Path | str,
    sampled: list[int],
    anchors_world: np.ndarray,
    depthmaps_m: list[np.ndarray],
    confs: list[np.ndarray] | None = None,
    focals: np.ndarray | None = None,
    principal_points: np.ndarray | None = None,
    scale: float = 1.0,
) -> PerceptionBundle:
    """Write a NEW bundle holding only MASt3R's anchor frames, renumbered
    0..K-1, in the exact on-disk format every later stage reads:

      frames/k/rgb.jpg + objects.json + mask_*.png   copied from frame sampled[k]
      frames/k/depth.png    uint16 mm from depthmaps_m[k] * scale, mapped from
                            the 512-res crop frame onto the RGB frame
      frames/k/conf.png     uint8 from confs[k] (conf_to_u8), if given
      poses.json            anchors_world[k] (T_world_camera, world = anchor 0)
      intrinsics.json       fx = fy = median(focals) rescaled to the RGB size,
                            principal point mapped the same way; else the
                            source bundle's intrinsics
      manifest.json         frame_count = K, fps = effective anchor rate
      frame_times.json      the anchors' source timestamps

    Restricting to anchors is deliberate: only anchors have a MASt3R depthmap,
    and tsdf._object_frames reads depth.png for every masked frame."""
    import shutil

    if len(sampled) != len(depthmaps_m) or len(sampled) != len(anchors_world):
        raise ValueError("sampled, depthmaps and anchor poses must have the same length")
    if confs is not None and len(confs) != len(sampled):
        raise ValueError("confs must match sampled")
    if not sampled:
        raise ValueError("nothing to export: no anchor frames")

    h, w = src.read_rgb(sampled[0]).shape[:2]
    rw, rh, x0, y0, _cw, _ch = dust3r_crop_geometry(w, h)
    sx, sy = w / rw, h / rh  # resized-frame px -> RGB px (equal up to rounding)

    if focals is not None and len(focals):
        f = float(np.median(np.asarray(focals, dtype=np.float64))) * sx
        if principal_points is not None and len(principal_points):
            pp = np.median(np.asarray(principal_points, dtype=np.float64).reshape(-1, 2), axis=0)
            cx, cy = (pp[0] + x0) * sx, (pp[1] + y0) * sy
        else:
            cx, cy = w / 2.0, h / 2.0
        intr = Intrinsics(fx=f, fy=f, cx=float(cx), cy=float(cy))
    else:
        intr = src.intrinsics

    src_times = src.read_frame_times()
    times = [src_times[f] for f in sampled] if len(src_times) > max(sampled) else []
    if len(times) >= 2 and times[-1] > times[0]:
        fps = (len(times) - 1) / (times[-1] - times[0])
    else:
        n = max(1, src.manifest.frame_count)
        fps = src.manifest.fps * len(sampled) / n

    manifest = Manifest(
        session_id=f"{src.manifest.session_id}_mast3r",
        fps=float(fps),
        frame_count=len(sampled),
        timestamp_start=src.manifest.timestamp_start,
        source=src.manifest.source,
    )
    out = PerceptionBundle.create(out_root, manifest, intr)

    for k, fid in enumerate(sampled):
        dst = out.ensure_frame(k)
        for p in src.frame_dir(fid).iterdir():
            if p.is_file() and p.name not in ("depth.png", "conf.png"):
                shutil.copyfile(p, dst / p.name)
        depth_m = crop_map_to_frame(np.asarray(depthmaps_m[k]) * float(scale), w, h)
        out.write_depth_mm(k, depth_m * 1000.0)
        if confs is not None:
            out.write_conf(k, conf_to_u8(crop_map_to_frame(confs[k], w, h)))

    out.write_poses([np.asarray(T, dtype=np.float64) for T in anchors_world])
    if times:
        out.write_frame_times(times)
    return out


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

    def estimate(self, bundle: PerceptionBundle, *,
                 export_root: Path | str | None = None) -> list[np.ndarray]:
        """Poses for every frame (anchors + SE(3) interpolation), world = frame 0.

        `export_root`: also write MASt3R's dense output as a new anchor-only
        bundle there (see export_anchor_bundle) — the RGB-only ingest path,
        where the source bundle has no depth.png and the returned poses alone
        would leave every downstream stage without geometry."""
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
            scene = global_aligner(out, device=device,  # noqa: F821 (closure over `out` above)
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
        outs = scene_outputs(scene)
        del out, scene
        if dev == "cuda":
            torch.cuda.empty_cache()
        cam2world, pred_depths = outs["cam2world"], outs["depthmaps"]

        # fix M1: residual metric scale vs the sensor, applied to translations
        # (and to the exported depth). Guarded: an RGB-only bundle keeps the
        # metric checkpoint's scale.
        s, _scale_source = resolve_metric_scale(bundle, sampled, pred_depths)
        cam2world[:, :3, 3] *= s

        # world = first frame (the bundle's convention)
        T0_inv = np.linalg.inv(cam2world[0])
        anchors = np.array([T0_inv @ T for T in cam2world])

        if export_root is not None:
            export_anchor_bundle(bundle, export_root, sampled, anchors, pred_depths,
                                 confs=outs["confs"], focals=outs["focals"],
                                 principal_points=outs["principal_points"], scale=s)
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
