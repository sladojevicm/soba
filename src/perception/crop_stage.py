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
    """Frame id with the largest mask for track_id (closest/cleanest view), or
    None if the object is never visible enough to be worth regenerating."""
    best_fid, best_area = None, 0
    for fid in bundle.iter_frame_ids():
        if not bundle.mask_path(fid, track_id).exists():
            continue
        area = int((bundle.read_mask(fid, track_id) > 0).sum())
        if area > best_area:
            best_fid, best_area = fid, area
    return best_fid if best_area >= min_area_px else None


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
