"""Best-frame crop staging tests (CPU, no model).

Builds a tiny real bundle with the SAME object visible at two sizes across two
frames, and asserts the guarantees the generative band relies on: the frame with
the LARGEST mask is chosen, the crop is whitened outside the mask, and an object
that is never visible enough yields None so the caller drops it.
"""

from __future__ import annotations

import numpy as np

from perception import crop_stage
from perception.bundle import Intrinsics, Manifest, PerceptionBundle


def _bundle(tmp_path):
    b = PerceptionBundle.create(
        tmp_path / "b",
        Manifest(session_id="t", fps=30.0, frame_count=2, source="test"),
        Intrinsics(fx=100.0, fy=100.0, cx=32.0, cy=32.0),
    )
    # frame 0: small mask (16x16 red square). frame 1: large mask (40x40).
    for fid, side, color in ((0, 16, (255, 0, 0)), (1, 40, (0, 255, 0))):
        rgb = np.zeros((64, 64, 3), np.uint8)
        mask = np.zeros((64, 64), np.uint8)
        rgb[5:5 + side, 5:5 + side] = color
        mask[5:5 + side, 5:5 + side] = 255
        b.write_rgb(fid, rgb)
        b.write_mask(fid, 7, mask)
    return b


def test_picks_largest_mask_frame_and_whitens_background(tmp_path):
    b = _bundle(tmp_path)
    p = crop_stage.stage_crop(b, 7, pad_frac=0.0, min_area_px=16)
    assert p is not None and p.exists()
    crop = b.read_crop(7)
    # frame 1 (the 40x40 green view) wins -> crop ~40px, not ~16px
    assert min(crop.shape[:2]) >= 36
    # corners are outside the object's footprint after tight crop -> white
    assert tuple(int(c) for c in crop[0, 0]) == (255, 255, 255)
    # the object's green survives somewhere in the crop
    assert (crop[:, :, 1] > 200).any()


def test_absent_object_returns_none(tmp_path):
    b = _bundle(tmp_path)
    assert crop_stage.stage_crop(b, 999, min_area_px=16) is None  # no mask -> drop


def test_too_small_returns_none(tmp_path):
    b = _bundle(tmp_path)
    # both views are < a huge threshold -> not worth regenerating
    assert crop_stage.stage_crop(b, 7, min_area_px=10_000) is None
