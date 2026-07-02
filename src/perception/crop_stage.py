"""Best-frame crop staging for the generative (image-to-3D) band.

The generative engine regenerates an under-observed object from ONE image, so it
needs a single clean crop per object. We pick the frame where the object is
best-observed (largest refined mask = closest / least-occluded view) and write it
segmented onto a white background — the input TripoSG's RMBG step expects. The
bundle works in RGB throughout, so the staged jpg is correctly coloured. CPU-only;
no model here.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np


def _best_frame(bundle, track_id: int, *, min_area_px: int):
    """Frame id of the BEST-LOOKING view of track_id for image-to-3D, or None if
    the object is never visible enough to be worth regenerating.

    NOT the largest mask (the old rule): a chair seen edge-on from up close has a
    huge mask yet is a useless grazing panel — TripoSG then builds a blob from it.
    The plan says "best picture in the whole sequence", i.e. the view that reveals
    the MOST of the object's real 3D structure. We score each frame by the 3D
    EXTENT (world-space bounding-box diagonal) of its back-projected object points:
    a front-on view spans the whole chair (seat+back+base); a foreshortened edge-on
    view spans a thin sliver. Falls back to mask area if depth/poses are missing.

    The min_area_px gate (drop objects too small to regenerate usefully) is kept,
    judged on the object's LARGEST mask across the sequence.
    """
    try:
        poses = bundle.read_poses()
        Kinv = np.linalg.inv(bundle.intrinsics.matrix())
    except Exception:
        poses, Kinv = None, None

    # Score by 3D extent when poses/depth are available, else by mask area. The
    # two metrics never mix within one pass (different scales), so pick the mode up
    # front from whether any usable 3D score was produced.
    best_ext_fid, best_ext = None, -1.0
    best_area_fid, best_area = None, 0
    for fid in bundle.iter_frame_ids():
        if not bundle.mask_path(fid, track_id).exists():
            continue
        mask = np.asarray(bundle.read_mask(fid, track_id)) > 0
        area = int(mask.sum())
        if area == 0:
            continue
        if area > best_area:
            best_area_fid, best_area = fid, area
        if poses is not None and area >= min_area_px:
            try:
                depth = np.asarray(bundle.read_depth_mm(fid)).astype(np.float64)
                ys, xs = np.where(mask & (depth > 0))
                if len(ys) >= 200:
                    z = depth[ys, xs] / 1000.0
                    cam = (Kinv @ np.c_[xs, ys, np.ones(len(xs))].T).T * z[:, None]
                    world = (poses[fid][:3, :3] @ cam.T).T + poses[fid][:3, 3]
                    ext = float(np.linalg.norm(world.max(0) - world.min(0)))
                    if ext > best_ext:
                        best_ext_fid, best_ext = fid, ext
            except Exception:
                pass
    if best_area < min_area_px:
        return None
    return best_ext_fid if best_ext_fid is not None else best_area_fid


def stage_crop(bundle, track_id: int, *, pad_frac: float = 0.12,
               min_area_px: int = 1024) -> Path | None:
    """Write crops/crop_{track_id}.jpg (object on white) and return its path.

    Returns None when the object's biggest view is < min_area_px (or it has no
    mask) — too small to regenerate usefully, so the caller drops it.
    """
    fid = _best_frame(bundle, track_id, min_area_px=min_area_px)
    if fid is None:
        return None

    rgb = np.asarray(bundle.read_rgb(fid))                 # HxWx3, RGB
    mask = np.asarray(bundle.read_mask(fid, track_id)) > 0
    ys, xs = np.where(mask)
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())

    # pad the tight mask bbox so the object isn't flush against the crop edge
    h, w = mask.shape[:2]
    py = int(round((y1 - y0 + 1) * pad_frac))
    px = int(round((x1 - x0 + 1) * pad_frac))
    y0, y1 = max(0, y0 - py), min(h - 1, y1 + py)
    x0, x1 = max(0, x0 - px), min(w - 1, x1 + px)

    crop = rgb[y0:y1 + 1, x0:x1 + 1].copy()
    keep = mask[y0:y1 + 1, x0:x1 + 1]
    crop[~keep] = 255                                      # white background
    bundle.write_crop(track_id, crop)
    return bundle.crop_path(track_id)


def ensure_crop(bundle, track_id: int, **kw) -> Path | None:
    """stage_crop, but reuse an already-staged crop if present (idempotent)."""
    p = bundle.crop_path(track_id)
    if p.exists():
        return p
    return stage_crop(bundle, track_id, **kw)
