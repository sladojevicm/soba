"""IoU tracker + TrackingDetector contract tests (no ultralytics needed)."""

from __future__ import annotations

import numpy as np

from perception.detect import IouTracker, TrackingDetector, bbox_iou


def _det(bbox, cls="chair", conf=0.9, mask=None):
    return {"class": cls, "bbox": bbox, "confidence": conf, "mask": mask}


def test_bbox_iou_basics():
    assert bbox_iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert bbox_iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
    assert 0.14 < bbox_iou([0, 0, 10, 10], [5, 5, 15, 15]) < 0.15


def test_same_object_keeps_its_track_id():
    t = IouTracker()
    a = t.update([_det([0, 0, 100, 100])])
    b = t.update([_det([5, 5, 105, 105])])  # drifted slightly, IoU >> 0.4
    assert a == b == [0]


def test_different_class_never_links():
    t = IouTracker()
    t.update([_det([0, 0, 100, 100], cls="chair")])
    ids = t.update([_det([0, 0, 100, 100], cls="couch")])  # same spot, new class
    assert ids == [1]


def test_new_object_gets_new_id():
    t = IouTracker()
    t.update([_det([0, 0, 100, 100])])
    ids = t.update([_det([2, 2, 102, 102]), _det([300, 300, 400, 400])])
    assert ids == [0, 1]


def test_retired_track_id_is_not_reused():
    t = IouTracker(retire_after=5)
    t.update([_det([0, 0, 100, 100])])
    for _ in range(5):                     # absent 5 frames -> retired
        t.update([])
    ids = t.update([_det([0, 0, 100, 100])])
    assert ids == [1]                      # reappearance = NEW id, not 0


def test_brief_absence_relinks():
    t = IouTracker(retire_after=5)
    t.update([_det([0, 0, 100, 100])])
    t.update([])                           # missed once (< retire_after)
    ids = t.update([_det([3, 3, 103, 103])])
    assert ids == [0]


def test_tracking_detector_emits_bundle_contract():
    mask = np.zeros((8, 8), bool)
    mask[2:6, 2:6] = True
    frames = iter([[_det([0, 0, 4, 4], conf=0.8, mask=mask)]])
    det = TrackingDetector(lambda rgb: next(frames))
    recs = det(np.zeros((8, 8, 3), np.uint8))
    assert recs == [{
        "track_id": 0, "class": "chair", "bbox": [0.0, 0.0, 4.0, 4.0],
        "confidence": 0.8, "mask": mask,
    }]
