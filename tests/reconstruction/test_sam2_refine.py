"""SAM2 refinement tests — pure orchestration with a fake backend (Phase 4)."""

from __future__ import annotations

import numpy as np

from perception.bundle import Intrinsics, Manifest, PerceptionBundle
from reconstruction import sam2_refine as sr

H, W = 10, 12
YOLO_BBOX = [2.0, 2.0, 8.0, 8.0]  # xyxy, area 36


def _make_bundle(root, source="tum") -> PerceptionBundle:
    m = Manifest(session_id="t", fps=30.0, frame_count=3, source=source)
    b = PerceptionBundle.create(root, m, Intrinsics(fx=100, fy=100, cx=2, cy=2))
    for fid in range(3):
        b.write_rgb(fid, np.full((H, W, 3), fid * 30 + 10, np.uint8))
    b.write_objects(0, [])  # object not yet visible
    b.write_objects(1, [{"track_id": 7, "class": "chair", "bbox": YOLO_BBOX, "confidence": 0.9}])
    b.write_objects(2, [{"track_id": 7, "class": "chair", "bbox": [3, 2, 9, 8], "confidence": 0.8}])
    return b


class FakeBackend:
    """Returns masks TIGHTER than the YOLO bbox; frame 2 is the larger view."""

    def refine(self, bundle, prompts):
        out = {}
        for p in prompts:
            f1 = np.zeros((H, W), bool)
            f1[3:7, 3:7] = True  # 16 px
            f2 = np.zeros((H, W), bool)
            f2[3:8, 3:8] = True  # 25 px
            out[p.track_id] = [sr.FrameMask(1, f1, 0.9), sr.FrameMask(2, f2, 0.8)]
        return out


# -- pure helpers (no imaging backend) ------------------------------------
def test_track_first_frames(tmp_path):
    b = _make_bundle(tmp_path)
    prompts = sr.track_first_frames(b)
    assert set(prompts) == {7}
    assert prompts[7].frame_id == 1  # first sighting, not frame 0 or 2
    assert prompts[7].bbox == (2.0, 2.0, 8.0, 8.0)
    assert prompts[7].class_name == "chair"


def test_bbox_from_mask():
    mask = np.zeros((10, 10), bool)
    mask[2:5, 3:7] = True
    assert sr.bbox_from_mask(mask) == (3.0, 2.0, 7.0, 5.0)
    assert sr.bbox_from_mask(np.zeros((4, 4), bool)) is None


def test_crop_with_margin_clamps_to_image():
    img = np.zeros((10, 12, 3), np.uint8)
    crop = sr.crop_with_margin(img, (0.0, 0.0, 4.0, 4.0), margin=0.5)
    assert crop.shape[0] <= 10 and crop.shape[1] <= 12  # clamped, never OOB
    assert crop.size > 0


def test_select_best_mask_uses_score_times_area():
    f1 = sr.FrameMask(1, np.ones((4, 4), bool), 0.9)  # 16 * 0.9 = 14.4
    f2 = sr.FrameMask(2, np.ones((5, 5), bool), 0.8)  # 25 * 0.8 = 20.0
    assert sr.select_best_mask([f1, f2]).frame_id == 2


# -- full orchestration (writes images; needs an imaging backend) ----------
def test_refine_writes_tight_masks_and_crop(tmp_path):
    b = _make_bundle(tmp_path)
    summary = sr.refine_masks(b, FakeBackend())

    assert summary[7]["frames"] == 2
    assert summary[7]["best_frame"] == 2  # larger view wins

    # masks were written for the frames the object appears in
    assert b.mask_path(1, 7).exists() and b.mask_path(2, 7).exists()
    # ...and they are TIGHTER than the YOLO bbox (the whole point of SAM2)
    refined_area = int(np.count_nonzero(b.read_mask(1, 7)))
    yolo_area = (YOLO_BBOX[2] - YOLO_BBOX[0]) * (YOLO_BBOX[3] - YOLO_BBOX[1])
    assert 0 < refined_area < yolo_area

    # a crop was staged for the object, keyed by track_id
    assert b.crop_path(7).exists()
    crop = b.read_crop(7)
    assert crop.ndim == 3 and crop.size > 0


def test_refine_skips_ground_truth_datasets(tmp_path):
    b = _make_bundle(tmp_path, source="scannet")
    assert sr.refine_masks(b, FakeBackend()) == {}
    assert not b.mask_path(1, 7).exists()
    # ...but can be forced if you really want SAM2 to run anyway
    assert sr.refine_masks(b, FakeBackend(), force=True)[7]["frames"] == 2


def test_refine_no_detections_is_noop(tmp_path):
    m = Manifest(session_id="t", fps=30.0, frame_count=1, source="tum")
    b = PerceptionBundle.create(tmp_path, m, Intrinsics(fx=100, fy=100, cx=2, cy=2))
    b.write_rgb(0, np.zeros((H, W, 3), np.uint8))
    b.write_objects(0, [])
    assert sr.refine_masks(b, FakeBackend()) == {}
