"""Best-frame crop staging for the generative (image-to-3D) band.

The generative engine regenerates an under-observed object from ONE image, so it
needs a single clean crop per object. Frame choice is two-stage: (1) score every
frame by the 3D EXTENT of its back-projected object points (how much of the real
structure the view reveals — mask area is the no-poses fallback), then (2) among
the frames within the top ~20% of that score, pick the best IMAGE QUALITY:
sharpness (variance of the Laplacian over the masked region) damped by an
exposure factor that penalises very dark crops. Extent stays primary — a sharp
sliver is still useless — but among equally revealing views a motion-blurred or
underexposed frame no longer wins. The chosen view is written segmented onto a
white background — the input TripoSG's RMBG step expects. The bundle works in
RGB throughout, so the staged jpg is correctly coloured. CPU-only; no model here.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

# A frame competes on image quality if its view score (3D extent, or mask area
# on the fallback path) is within this fraction of the best frame's score —
# i.e. roughly the top 20% band. 1.0 would disable the quality stage.
SHORTLIST_FRAC = 0.8

# Mean masked luminance (0-255 grayscale) below which a crop counts as
# increasingly underexposed; the quality score scales down linearly with it.
DARK_LUM = 60.0

# Depth margin (mm) for calling a pixel an OCCLUDER: it must be at least this
# much nearer than the object's near quartile. Filters out items resting ON the
# object (a book on a table sits at ~the same depth) while catching furniture
# standing between the camera and the object.
OCC_MARGIN_MM = 50.0


def _occlusion_and_dominant(mask, depth) -> tuple[float, float]:
    """(occluded fraction, dominant-component fraction) of one object view.

    Occlusion: pixels inside the mask's convex hull that are NOT the object and
    are NEARER than the object's near quartile (minus OCC_MARGIN_MM) are
    occluders — furniture in front punches white intrusions into the crop that
    mask-only metrics miss (an edge-connected bite is not a topological hole).
    The generative model then faithfully builds those intrusions as holes
    through the object (the holey-table bug).

    Dominant: largest connected component's share of the mask — an object seen
    only as disconnected slivers (through gaps) generates as a broken mesh.
    """
    import cv2
    from scipy import ndimage

    obj_depth = depth[mask & (depth > 0)]
    if obj_depth.size == 0:
        return 0.0, 1.0
    pts = cv2.findNonZero(mask.astype(np.uint8))
    hull = cv2.convexHull(pts)
    hull_mask = np.zeros(mask.shape, np.uint8)
    cv2.fillConvexPoly(hull_mask, hull, 1)
    other = hull_mask.astype(bool) & ~mask & (depth > 0)
    near = float(np.percentile(obj_depth, 25)) - OCC_MARGIN_MM
    occ = float((depth[other] < near).sum()) / max(1.0, float(hull_mask.sum()))
    lab, n = ndimage.label(mask)
    dominant = (float(np.bincount(lab.ravel())[1:].max()) / float(mask.sum())
                if n else 1.0)
    return occ, dominant


def _crop_quality(rgb, mask) -> float:
    """Image quality of the masked object region: sharpness x exposure sanity.

    Sharpness = variance of the Laplacian (grayscale) over the mask — motion
    blur / defocus flattens it. Exposure = mean masked luminance, mapped to a
    [~0, 1] damping factor so a very dark crop (which the generative model reads
    as a black blob) loses to a lit one even if noise keeps its Laplacian busy.
    """
    import cv2

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    sharpness = float(lap[mask].var())
    lum = float(gray[mask].mean())
    exposure = min(1.0, lum / DARK_LUM)
    return sharpness * exposure


def _best_frame(bundle, track_id: int, *, min_area_px: int):
    """Frame id of the BEST-LOOKING view of track_id for image-to-3D, or None if
    the object is never visible enough to be worth regenerating.

    NOT the largest mask (the old rule): a chair seen edge-on from up close has a
    huge mask yet is a useless grazing panel — TripoSG then builds a blob from it.
    The plan says "best picture in the whole sequence", i.e. the view that reveals
    the MOST of the object's real 3D structure. We score each frame by the 3D
    EXTENT (world-space bounding-box diagonal) of its back-projected object points
    x (1-occlusion)^2 x dominant-component fraction (_occlusion_and_dominant):
    a front-on view spans the whole chair (seat+back+base); a foreshortened edge-on
    view spans a thin sliver; a big view THROUGH other furniture is a hole-riddled
    mask the generative model reproduces as holes, so occluded/fragmented views
    lose to clean ones. Falls back to mask area if depth/poses are missing.
    Among the frames within SHORTLIST_FRAC of the best view score, the sharpest /
    best-exposed one wins (_crop_quality) — view score first, image quality second.

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
    ext_scores: list[tuple[int, float]] = []
    area_scores: list[tuple[int, int]] = []
    best_area = 0
    for fid in bundle.iter_frame_ids():
        if not bundle.mask_path(fid, track_id).exists():
            continue
        mask = np.asarray(bundle.read_mask(fid, track_id)) > 0
        area = int(mask.sum())
        if area == 0:
            continue
        area_scores.append((fid, area))
        best_area = max(best_area, area)
        if poses is not None and area >= min_area_px:
            try:
                depth = np.asarray(bundle.read_depth_mm(fid)).astype(np.float64)
                ys, xs = np.where(mask & (depth > 0))
                if len(ys) >= 200:
                    z = depth[ys, xs] / 1000.0
                    cam = (Kinv @ np.c_[xs, ys, np.ones(len(xs))].T).T * z[:, None]
                    world = (poses[fid][:3, :3] @ cam.T).T + poses[fid][:3, 3]
                    ext = float(np.linalg.norm(world.max(0) - world.min(0)))
                    occ, dom = _occlusion_and_dominant(mask, depth)
                    ext_scores.append((fid, ext * (1.0 - occ) ** 2 * dom))
            except Exception:
                pass
    if best_area < min_area_px:
        return None
    scores = ext_scores if ext_scores else area_scores
    # Top band by view score, best score first so a quality TIE keeps the most
    # revealing view (and a quality failure degrades to the pure-extent pick).
    ranked = sorted(scores, key=lambda s: s[1], reverse=True)
    shortlist = [fid for fid, s in ranked if s >= SHORTLIST_FRAC * ranked[0][1]]
    if len(shortlist) == 1:
        return shortlist[0]
    best_fid, best_q = shortlist[0], -1.0
    for fid in shortlist:
        try:
            rgb = np.asarray(bundle.read_rgb(fid))
            mask = np.asarray(bundle.read_mask(fid, track_id)) > 0
            q = _crop_quality(rgb, mask)
        except Exception:
            continue
        if q > best_q:
            best_fid, best_q = fid, q
    return best_fid


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
