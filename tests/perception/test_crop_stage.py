"""Best-frame crop staging tests (CPU, no model).

Builds a tiny real bundle with the SAME object visible at two sizes across two
frames, and asserts the guarantees the generative band relies on: the best view
is chosen (largest 3D extent when depth+poses exist, else largest mask), among
equally revealing views the sharpest / best-exposed frame wins (but a sharp
foreshortened sliver never beats a revealing view), the crop is whitened
outside the mask, and an object that is never visible enough yields None so
the caller drops it.
"""

from __future__ import annotations

import numpy as np

from perception import crop_stage
from perception.bundle import Intrinsics, Manifest, PerceptionBundle


def _diamond_mask(r: int, centre: int = 25) -> np.ndarray:
    """A diamond (not a full square): the tight crop's CORNERS lie outside the
    mask, so background whitening is observable at the corners."""
    yy, xx = np.mgrid[0:64, 0:64]
    return ((np.abs(yy - centre) + np.abs(xx - centre)) <= r).astype(np.uint8) * 255


def _bundle(tmp_path):
    b = PerceptionBundle.create(
        tmp_path / "b",
        Manifest(session_id="t", fps=30.0, frame_count=2, source="test"),
        Intrinsics(fx=100.0, fy=100.0, cx=32.0, cy=32.0),
    )
    # frame 0: small mask (r=8 red diamond). frame 1: large mask (r=20 green).
    for fid, r, color in ((0, 8, (255, 0, 0)), (1, 20, (0, 255, 0))):
        mask = _diamond_mask(r)
        rgb = np.zeros((64, 64, 3), np.uint8)
        rgb[mask > 0] = color
        b.write_rgb(fid, rgb)
        b.write_mask(fid, 7, mask)
    return b


def test_picks_largest_mask_frame_and_whitens_background(tmp_path):
    # No poses/depth in this bundle -> falls back to the largest-mask rule.
    b = _bundle(tmp_path)
    p = crop_stage.stage_crop(b, 7, pad_frac=0.0, min_area_px=16)
    assert p is not None and p.exists()
    crop = b.read_crop(7)
    # frame 1 (the r=20 green view) wins -> crop ~41px, not ~17px
    assert min(crop.shape[:2]) >= 36
    # corners are outside the diamond after tight crop -> whitened (JPEG-tolerant)
    assert all(int(c) >= 240 for c in crop[0, 0])
    # the object's green survives somewhere in the crop
    assert (crop[:, :, 1] > 200).any()


def test_prefers_frame_revealing_most_3d_structure(tmp_path):
    """With depth + poses available, the best frame is the one whose
    back-projected points span the largest WORLD extent (a front-on revealing
    view), even if another frame has a bigger mask (a close-up grazing view)."""
    b = PerceptionBundle.create(
        tmp_path / "b3d",
        Manifest(session_id="t", fps=30.0, frame_count=2, source="test"),
        Intrinsics(fx=100.0, fy=100.0, cx=32.0, cy=32.0),
    )
    # frame 0: HUGE mask (40x40 red) but very close (0.1 m) -> tiny 3D extent.
    # frame 1: smaller mask (24x24 green) but far (2.0 m) -> big 3D extent.
    for fid, side, color, depth_mm in ((0, 40, (255, 0, 0), 100),
                                       (1, 24, (0, 255, 0), 2000)):
        rgb = np.zeros((64, 64, 3), np.uint8)
        mask = np.zeros((64, 64), np.uint8)
        depth = np.zeros((64, 64), np.uint16)
        rgb[5:5 + side, 5:5 + side] = color
        mask[5:5 + side, 5:5 + side] = 255
        depth[5:5 + side, 5:5 + side] = depth_mm
        b.write_rgb(fid, rgb)
        b.write_mask(fid, 7, mask)
        b.write_depth_mm(fid, depth)
    b.write_poses([np.eye(4), np.eye(4)])

    p = crop_stage.stage_crop(b, 7, pad_frac=0.0, min_area_px=16)
    assert p is not None
    crop = b.read_crop(7)
    # frame 1's 24px view chosen (not frame 0's 40px close-up)
    assert max(crop.shape[:2]) <= 30
    assert (crop[:, :, 1] > 200).any()      # green view
    assert not (crop[:, :, 0] > 200).any()  # not the red close-up


def _quality_bundle(tmp_path, name, frames):
    """Two-frame bundle where BOTH frames back-project to the SAME world extent
    (side_px / depth balanced), so the image-quality stage must break the tie.
    `frames` = [(side_px, depth_mm, texture_hi, blur_sigma), ...]; the object is
    an 8px checkerboard of 0/texture_hi values, optionally Gaussian-blurred.
    Frame identity is observable through the staged crop's size (40px vs 24px).
    """
    import cv2

    b = PerceptionBundle.create(
        tmp_path / name,
        Manifest(session_id="t", fps=30.0, frame_count=len(frames), source="test"),
        Intrinsics(fx=100.0, fy=100.0, cx=32.0, cy=32.0),
    )
    for fid, (side, depth_mm, hi, sigma) in enumerate(frames):
        yy, xx = np.mgrid[0:side, 0:side]
        checker = (((yy // 8 + xx // 8) % 2) * hi).astype(np.uint8)
        rgb = np.zeros((64, 64, 3), np.uint8)
        rgb[5:5 + side, 5:5 + side] = checker[..., None]
        if sigma:
            rgb = cv2.GaussianBlur(rgb, (0, 0), sigma)
        mask = np.zeros((64, 64), np.uint8)
        mask[5:5 + side, 5:5 + side] = 255
        depth = np.zeros((64, 64), np.uint16)
        depth[5:5 + side, 5:5 + side] = depth_mm
        b.write_rgb(fid, rgb)
        b.write_mask(fid, 7, mask)
        b.write_depth_mm(fid, depth)
    b.write_poses([np.eye(4)] * len(frames))
    return b


def test_sharp_frame_beats_blurred_at_equal_extent(tmp_path):
    """Same world extent both frames (40px@1m vs 24px@1.667m) -> the quality
    stage decides: the SHARP 24px view wins over the blurred 40px close-up."""
    b = _quality_bundle(tmp_path, "blur", [(40, 1000, 200, 8.0),
                                           (24, 1667, 200, 0.0)])
    assert crop_stage.stage_crop(b, 7, pad_frac=0.0, min_area_px=16) is not None
    assert max(b.read_crop(7).shape[:2]) <= 30  # the sharp 24px frame

    # and the mirror image: sharp 40px vs blurred 24px -> the 40px frame,
    # proving the pick follows SHARPNESS, not crop size.
    b2 = _quality_bundle(tmp_path, "blur2", [(40, 1000, 200, 0.0),
                                             (24, 1667, 200, 8.0)])
    assert crop_stage.stage_crop(b2, 7, pad_frac=0.0, min_area_px=16) is not None
    assert min(b2.read_crop(7).shape[:2]) >= 36


def test_lit_frame_beats_dark_at_equal_extent(tmp_path):
    """Same extent, both sharp; one crop nearly black (checker 0/40, mean
    luminance ~20) -> the well-exposed view wins, in either size order."""
    b = _quality_bundle(tmp_path, "dark", [(40, 1000, 40, 0.0),
                                           (24, 1667, 200, 0.0)])
    assert crop_stage.stage_crop(b, 7, pad_frac=0.0, min_area_px=16) is not None
    assert max(b.read_crop(7).shape[:2]) <= 30  # the lit 24px frame

    b2 = _quality_bundle(tmp_path, "dark2", [(40, 1000, 200, 0.0),
                                             (24, 1667, 40, 0.0)])
    assert crop_stage.stage_crop(b2, 7, pad_frac=0.0, min_area_px=16) is not None
    assert min(b2.read_crop(7).shape[:2]) >= 36  # the lit 40px frame


def test_extent_stays_primary_over_sharpness(tmp_path):
    """A sharp but FORESHORTENED view (extent below the top band) must not win:
    the blurred frame that reveals far more 3D structure is kept."""
    # frame 0: blurred, 40px@1m -> ext ~0.57 m; frame 1: sharp, 24px@0.5m ->
    # ext ~0.17 m (< 0.8 * best) -> excluded from the quality shortlist.
    b = _quality_bundle(tmp_path, "sliver", [(40, 1000, 200, 8.0),
                                             (24, 500, 200, 0.0)])
    assert crop_stage.stage_crop(b, 7, pad_frac=0.0, min_area_px=16) is not None
    assert min(b.read_crop(7).shape[:2]) >= 36  # the revealing 40px frame


def test_absent_object_returns_none(tmp_path):
    b = _bundle(tmp_path)
    assert crop_stage.stage_crop(b, 999, min_area_px=16) is None  # no mask -> drop


def test_too_small_returns_none(tmp_path):
    b = _bundle(tmp_path)
    # both views are < a huge threshold -> not worth regenerating
    assert crop_stage.stage_crop(b, 7, min_area_px=10_000) is None
