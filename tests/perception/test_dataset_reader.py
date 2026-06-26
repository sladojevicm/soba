"""TUM reader pure-logic tests (association, depth conversion, label mapping)."""

from __future__ import annotations

import numpy as np

from perception import dataset_reader as dr


def test_parse_tum_index_skips_comments():
    text = "# color images\n# timestamp filename\n1305031910.765238 rgb/1.png\n1305031910.797230 rgb/2.png\n"
    rows = dr.parse_tum_index(text)
    assert [r.path for r in rows] == ["rgb/1.png", "rgb/2.png"]
    assert rows[0].timestamp == 1305031910.765238


def test_associate_nearest_within_tolerance():
    rgb = [dr.StampedPath(100.00, "rgb/a.png"), dr.StampedPath(100.10, "rgb/b.png")]
    depth = [dr.StampedPath(100.005, "d/a.png"), dr.StampedPath(100.50, "d/b.png")]
    pairs = dr.associate(rgb, depth, max_diff_s=0.02)
    # only the first rgb has a depth within 20 ms
    assert len(pairs) == 1
    assert pairs[0][0].path == "rgb/a.png" and pairs[0][1].path == "d/a.png"


def test_tum_depth_png_to_mm():
    # TUM value 5000 == 1.0 m == 1000 mm
    png = np.array([[0, 5000, 2500]], dtype=np.uint16)
    mm = dr.tum_depth_png_to_mm(png)
    assert mm.dtype == np.uint16
    assert list(mm[0]) == [0, 1000, 500]


def test_intrinsics_for_sequence():
    fr1 = dr.intrinsics_for_sequence("rgbd_dataset_freiburg1_xyz")
    assert abs(fr1.fx - 517.306408) < 1e-6
    # unknown sequence falls back to freiburg1
    assert dr.intrinsics_for_sequence("mystery").fx == fr1.fx


def test_map_label_to_coco():
    cmap = {"scannet": {"sofa": "couch", "table": "dining table"}}
    assert dr.map_label_to_coco("scannet", "Sofa", cmap) == "couch"
    assert dr.map_label_to_coco("scannet", "table", cmap) == "dining table"
    # unmapped -> default
    assert dr.map_label_to_coco("scannet", "lamp", cmap) == "default"
    # TUM passes through unchanged (YOLO already emits COCO names)
    assert dr.map_label_to_coco("tum", "chair", cmap) == "chair"


def test_real_coco_map_loads_and_has_spaced_keys():
    cmap = dr.load_coco_class_map()
    assert "scannet" in cmap and "replica" in cmap
    assert dr.map_label_to_coco("scannet", "table", cmap) == "dining table"
