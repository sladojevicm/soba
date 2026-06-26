"""PerceptionBundle metadata + layout tests (no imaging backend needed)."""

from __future__ import annotations

import numpy as np
import pytest

from perception.bundle import Intrinsics, Manifest, PerceptionBundle, frame_name


def test_frame_name_padding():
    assert frame_name(1) == "00001"
    assert frame_name(150) == "00150"
    with pytest.raises(ValueError):
        frame_name(-1)


def test_intrinsics_roundtrip_and_matrix():
    intr = Intrinsics(fx=500.0, fy=510.0, cx=320.0, cy=240.0, baseline=0.075)
    again = Intrinsics.from_dict(intr.to_dict())
    assert again == intr
    K = intr.matrix()
    assert K.shape == (3, 3)
    assert K[0, 0] == 500.0 and K[1, 2] == 240.0 and K[2, 2] == 1.0


def test_manifest_roundtrip_defaults():
    m = Manifest(session_id="rgbd_dataset_freiburg1_xyz", fps=30.0, frame_count=42)
    assert m.timestamp_start == "" and m.source == "live"
    assert Manifest.from_dict(m.to_dict()) == m


def test_create_open_roundtrip(tmp_path):
    m = Manifest(session_id="s1", fps=15.0, frame_count=2, source="tum")
    intr = Intrinsics(fx=1.0, fy=1.0, cx=0.0, cy=0.0)
    PerceptionBundle.create(tmp_path / "sess", m, intr)
    reopened = PerceptionBundle.open(tmp_path / "sess")
    assert reopened.manifest == m
    assert reopened.intrinsics == intr
    assert (tmp_path / "sess" / "frames").is_dir()


def test_objects_and_paths(tmp_path):
    b = PerceptionBundle.create(
        tmp_path, Manifest("s", 15.0, 1), Intrinsics(1, 1, 0, 0)
    )
    dets = [{"track_id": 147, "class": "chair", "bbox": [0, 0, 10, 10], "confidence": 0.9}]
    b.write_objects(0, dets)
    assert b.read_objects(0) == dets
    # masks are keyed by the RAW track_id (fix Z7)
    assert b.mask_path(0, 147).name == "mask_147.png"
    assert b.depth_path(0).name == "depth.png"


def test_imu_jsonl_roundtrip(tmp_path):
    b = PerceptionBundle.create(tmp_path, Manifest("s", 15.0, 1), Intrinsics(1, 1, 0, 0))
    samples = [{"t": 0.0, "ax": 0, "ay": -9.81, "az": 0, "gx": 0, "gy": 0, "gz": 0}]
    b.write_imu(0, samples)
    assert b.read_imu(0) == samples


def test_poses_roundtrip_and_validation(tmp_path):
    b = PerceptionBundle.create(tmp_path, Manifest("s", 15.0, 2), Intrinsics(1, 1, 0, 0))
    poses = [np.eye(4), np.diag([1.0, 1.0, 1.0, 1.0])]
    poses[1][:3, 3] = [0.1, 0.0, 0.2]
    b.write_poses(poses)
    back = b.read_poses()
    assert len(back) == 2
    np.testing.assert_allclose(back[1], poses[1])

    with pytest.raises(ValueError):
        b.write_poses([np.eye(3)])


def test_iter_frame_ids_sorted(tmp_path):
    b = PerceptionBundle.create(tmp_path, Manifest("s", 15.0, 3), Intrinsics(1, 1, 0, 0))
    for fid in (2, 0, 1):
        b.ensure_frame(fid)
    assert list(b.iter_frame_ids()) == [0, 1, 2]
