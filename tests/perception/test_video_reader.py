"""VideoReader: plain RGB video -> frames + manifest bundle (no depth, no poses).

Needs an OpenCV that can encode a tiny clip (mp4v, falling back to MJPG/AVI),
so the whole module skips where `cv2` is absent (this WSL box); CI installs
the recon extra and runs it.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from perception.bundle import Intrinsics, PerceptionBundle  # noqa: E402
from perception.video_reader import VideoReader, default_intrinsics  # noqa: E402

W, H, FPS = 64, 48, 10.0


def _frame(i: int) -> np.ndarray:
    """A flat colour per frame so frames are distinguishable after JPEG."""
    bgr = np.zeros((H, W, 3), dtype=np.uint8)
    bgr[:, :, i % 3] = 200
    return bgr


def _write_clip(tmp_path, n_frames: int):
    for suffix, fourcc in ((".mp4", "mp4v"), (".avi", "MJPG")):
        path = tmp_path / f"clip{suffix}"
        wr = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fourcc), FPS, (W, H))
        if not wr.isOpened():
            continue
        for i in range(n_frames):
            wr.write(_frame(i))
        wr.release()
        if path.stat().st_size > 0:
            return path
    pytest.skip("this OpenCV build cannot encode a test clip")


def test_default_intrinsics_from_hfov():
    intr = default_intrinsics(640, 480, hfov_deg=90.0)
    assert intr.fx == pytest.approx(320.0)  # (w/2) / tan(45 deg)
    assert intr.fy == intr.fx and intr.cx == 320.0 and intr.cy == 240.0
    with pytest.raises(ValueError):
        default_intrinsics(0, 480)
    with pytest.raises(ValueError):
        default_intrinsics(640, 480, hfov_deg=180.0)


def test_to_bundle_writes_frames_manifest_times_and_placeholder_intrinsics(tmp_path):
    clip = _write_clip(tmp_path, 3)
    b = VideoReader(clip).to_bundle(tmp_path / "bundle")

    # manifest: source "video", effective fps, frame_count known after decode
    m = json.loads((b.root / "manifest.json").read_text())
    assert m["source"] == "video"
    assert m["frame_count"] == 3
    assert m["fps"] == pytest.approx(FPS)
    assert m["session_id"] == clip.stem

    # frames 0..2, rgb only, no depth/conf/poses written
    assert list(b.iter_frame_ids()) == [0, 1, 2]
    for fid in range(3):
        assert b.rgb_path(fid).is_file()
        assert not b.depth_path(fid).exists()
        assert not b.conf_path(fid).exists()
    assert not (b.root / "poses.json").exists()

    # RGB round-trips at the right size and in RGB (not BGR) order: frame 1
    # was painted in the green channel (bgr[:, :, 1]).
    rgb = b.read_rgb(1)
    assert rgb.shape == (H, W, 3)
    assert rgb[:, :, 1].mean() > 150 and rgb[:, :, 0].mean() < 60

    # timestamps are decode index / source fps
    assert b.read_frame_times() == pytest.approx([0.0, 0.1, 0.2])

    # placeholder intrinsics: centred principal point, fx=fy from the HFOV
    assert b.intrinsics == default_intrinsics(W, H)
    # ... and the bundle re-opens through the normal path
    assert PerceptionBundle.open(b.root).manifest.frame_count == 3


def test_to_bundle_stride_and_max_frames(tmp_path):
    clip = _write_clip(tmp_path, 6)
    b = VideoReader(clip).to_bundle(tmp_path / "b2", stride=2)
    assert list(b.iter_frame_ids()) == [0, 1, 2]
    assert b.manifest.fps == pytest.approx(FPS / 2)
    assert b.read_frame_times() == pytest.approx([0.0, 0.2, 0.4])

    b3 = VideoReader(clip).to_bundle(tmp_path / "b3", max_frames=2)
    assert b3.manifest.frame_count == 2 and list(b3.iter_frame_ids()) == [0, 1]


def test_explicit_intrinsics_and_bad_args(tmp_path):
    clip = _write_clip(tmp_path, 2)
    intr = Intrinsics(fx=100.0, fy=101.0, cx=32.0, cy=24.0)
    b = VideoReader(clip, intrinsics=intr).to_bundle(tmp_path / "b4")
    assert b.intrinsics == intr
    with pytest.raises(ValueError):
        VideoReader(clip).to_bundle(tmp_path / "b5", stride=0)
    with pytest.raises(FileNotFoundError):
        VideoReader(tmp_path / "missing.mp4")
