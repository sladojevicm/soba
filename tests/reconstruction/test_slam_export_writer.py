"""export_anchor_bundle: the on-disk contract the downstream stages read.

Needs an imaging backend (cv2) for the bundle writers, so this module skips
without one (this WSL box); CI installs the recon extra and runs it. The pure
export helpers are covered in test_slam_export.py, which runs everywhere.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")  # bundle writers need an imaging backend

from perception.bundle import Intrinsics, Manifest, PerceptionBundle  # noqa: E402
from reconstruction import slam  # noqa: E402

W, H = 64, 48  # RGB size; MASt3R crop frame for 64x48 is 512x384 (upscaled)


def _source_bundle(tmp_path, n=4):
    manifest = Manifest(session_id="clip", fps=10.0, frame_count=n, source="video")
    src = PerceptionBundle.create(tmp_path / "src", manifest, Intrinsics(50, 50, 32, 24))
    for fid in range(n):
        rgb = np.zeros((H, W, 3), dtype=np.uint8)
        rgb[:, :, fid % 3] = 180
        src.write_rgb(fid, rgb)
        src.write_objects(fid, [{"track_id": 7, "class": "chair", "bbox": [1, 2, 3, 4]}])
        src.write_mask(fid, 7, np.ones((H, W), dtype=np.uint8))
    src.write_frame_times([0.0, 0.1, 0.2, 0.3])
    return src


def test_export_anchor_bundle_writes_the_downstream_format(tmp_path):
    src = _source_bundle(tmp_path)
    sampled = [0, 2, 3]
    anchors = np.array([np.eye(4)] * 3)
    anchors[1, 0, 3], anchors[2, 0, 3] = 0.2, 0.3
    rw, rh, x0, y0, cw, ch = slam.dust3r_crop_geometry(W, H)
    depth = [np.full((ch, cw), 1.5), np.full((ch, cw), 2.0), np.full((ch, cw), 2.5)]
    confs = [np.full((ch, cw), 3.0)] * 3
    focals = np.array([400.0, 410.0, 405.0])          # crop-frame px
    pps = np.array([[cw / 2, ch / 2]] * 3)

    out = slam.export_anchor_bundle(src, tmp_path / "out", sampled, anchors, depth,
                                    confs=confs, focals=focals, principal_points=pps,
                                    scale=2.0)

    # --- manifest / frame ids: anchors renumbered 0..K-1
    m = json.loads((out.root / "manifest.json").read_text())
    assert m["frame_count"] == 3 and m["source"] == "video"
    assert m["session_id"] == "clip_mast3r"
    assert m["fps"] == pytest.approx(2 / 0.3)  # 3 anchors over 0.3 s
    assert list(out.iter_frame_ids()) == [0, 1, 2]
    assert out.read_frame_times() == pytest.approx([0.0, 0.2, 0.3])

    # --- depth: uint16 mm at the RGB size, scaled by `scale`, readable the
    #     way tsdf._object_frames / observed_cloud read it
    for k, metres in enumerate([1.5, 2.0, 2.5]):
        d = out.read_depth_mm(k)
        assert d.dtype == np.uint16 and d.shape == (H, W)
        assert int(np.median(d)) == int(round(metres * 2.0 * 1000))
        # inside the 400-8000 mm gate observed_cloud applies
        assert 400 <= np.median(d) <= 8000

    # --- conf: uint8, above the tsdf gate threshold for dust3r conf 3
    c = out.read_conf(1)
    assert c.dtype == np.uint8 and c.shape == (H, W) and int(np.median(c)) == 170

    # --- poses: one per anchor, T_world_camera, world = anchor 0
    poses = out.read_poses()
    assert len(poses) == 3 and np.allclose(poses[0], np.eye(4))
    assert poses[2][0, 3] == pytest.approx(0.3)

    # --- intrinsics from the solved focal, rescaled crop px -> RGB px
    intr = PerceptionBundle.open(out.root).intrinsics
    assert intr.fx == pytest.approx(405.0 * W / rw) and intr.fy == intr.fx
    assert intr.cx == pytest.approx(W / 2) and intr.cy == pytest.approx(H / 2)

    # --- rgb, objects.json and masks copied verbatim from the sampled frames
    assert out.rgb_path(1).read_bytes() == src.rgb_path(2).read_bytes()
    assert out.read_objects(2)[0]["track_id"] == 7
    assert out.read_mask(0, 7).max() == 255
    assert not out.rgb_path(3).exists()  # frame 1 of the source was not an anchor


def test_export_without_focals_keeps_source_intrinsics_and_skips_conf(tmp_path):
    src = _source_bundle(tmp_path, n=2)
    _, _, _, _, cw, ch = slam.dust3r_crop_geometry(W, H)
    out = slam.export_anchor_bundle(src, tmp_path / "out2", [0, 1],
                                    np.array([np.eye(4)] * 2),
                                    [np.full((ch, cw), 1.0)] * 2)
    assert PerceptionBundle.open(out.root).intrinsics == src.intrinsics
    assert not out.conf_path(0).exists()
    assert out.read_depth_mm(0).shape == (H, W)


def test_export_maps_depth_from_a_smaller_prediction_grid(tmp_path):
    # MASt3R depthmaps arrive at the crop size; a coarser grid is resized nearest
    src = _source_bundle(tmp_path, n=1)
    out = slam.export_anchor_bundle(src, tmp_path / "out3", [0], np.array([np.eye(4)]),
                                    [np.full((12, 16), 3.0)])
    d = out.read_depth_mm(0)
    assert d.shape == (H, W) and int(np.median(d)) == 3000


def test_export_rejects_mismatched_lengths(tmp_path):
    src = _source_bundle(tmp_path, n=2)
    with pytest.raises(ValueError):
        slam.export_anchor_bundle(src, tmp_path / "bad", [0, 1], np.array([np.eye(4)]),
                                  [np.zeros((4, 4))])
