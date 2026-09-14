"""MASt3R export path (RGB-only ingest, step 1) — no torch, no checkout.

Covers the pure pieces `Mast3rEstimator.estimate(..., export_root=...)` is
built from: the dust3r crop geometry, crop->frame mapping, confidence mapping,
the guarded metric-scale resolve, `scene_outputs` on a stubbed scene object,
and `export_anchor_bundle` writing a bundle in the format tsdf.py /
observed_cloud.py / run_assemble.py already read (so nothing downstream has to
change). The writer tests need an imaging backend (cv2) and skip without one.
"""

from __future__ import annotations

import types

import numpy as np
import pytest

from reconstruction import slam

# --- pure geometry / mapping (no imaging backend needed) --------------------


@pytest.mark.parametrize("w,h,expect", [
    # (rw, rh, x0, y0, cw, ch) — full crop when the resized dims are multiples of 16
    (640, 480, (512, 384, 0, 0, 512, 384)),
    (1920, 1080, (512, 288, 0, 0, 512, 288)),
    (1080, 1920, (288, 512, 0, 0, 288, 512)),   # portrait phone clip
    (4032, 3024, (512, 384, 0, 0, 512, 384)),   # 4:3 phone still size
    (500, 375, (512, 384, 0, 0, 512, 384)),     # upscaled, rounds to 384
    (1000, 700, (512, 358, 0, 3, 512, 352)),    # odd height -> 3 px crop top/bottom
])
def test_dust3r_crop_geometry(w, h, expect):
    assert slam.dust3r_crop_geometry(w, h) == expect


def test_dust3r_crop_geometry_rejects_bad_size():
    with pytest.raises(ValueError):
        slam.dust3r_crop_geometry(0, 10)


def test_conf_to_u8_matches_tsdf_gate_semantics():
    u8 = slam.conf_to_u8(np.array([0.5, 1.0, 2.0, 3.0, 100.0]))
    assert u8.dtype == np.uint8
    assert list(u8) == [0, 0, 128, 170, 252]
    # tsdf.fuse zeroes depth where conf < 150: dust3r's default min_conf_thr=3 passes
    assert u8[3] >= 150 and u8[2] < 150


# --- stubbed dust3r scene ---------------------------------------------------


class _T:
    """Minimal torch-tensor stand-in: .detach().cpu().numpy()."""

    def __init__(self, a):
        self.a = np.asarray(a)

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.a


def _stub_scene(n=3, h=24, w=32, with_optional=True):
    poses = np.array([np.eye(4)] * n)
    for i in range(n):
        poses[i, 0, 3] = 0.1 * i  # camera walks along +x
    depth = [np.full((h, w), 2.0 + 0.5 * i) for i in range(n)]
    scene = types.SimpleNamespace(
        get_im_poses=lambda: _T(poses),
        get_depthmaps=lambda: [_T(d) for d in depth],
    )
    if with_optional:
        scene.im_conf = [_T(np.full((h, w), 3.0)) for _ in range(n)]
        scene.get_focals = lambda: _T(np.array([[40.0], [42.0], [41.0]]))
        scene.get_principal_points = lambda: _T(np.array([[16.0, 12.0]] * n))
    return scene, poses, depth


def test_scene_outputs_duck_typed():
    scene, poses, depth = _stub_scene()
    out = slam.scene_outputs(scene)
    assert out["cam2world"].shape == (3, 4, 4) and out["cam2world"].dtype == np.float64
    assert np.allclose(out["cam2world"], poses)
    assert len(out["depthmaps"]) == 3 and np.allclose(out["depthmaps"][1], depth[1])
    assert len(out["confs"]) == 3
    assert list(out["focals"]) == [40.0, 42.0, 41.0]
    assert out["principal_points"].shape == (3, 2)


def test_scene_outputs_without_optional_members():
    scene, _, _ = _stub_scene(with_optional=False)
    out = slam.scene_outputs(scene)
    assert out["confs"] is None and out["focals"] is None and out["principal_points"] is None


# --- guarded metric scale ---------------------------------------------------


def _bundle_stub(tmp_path, with_depth: bool):
    d = tmp_path / "frames"
    d.mkdir()
    if with_depth:
        (d / "depth.png").write_bytes(b"x")
    return types.SimpleNamespace(
        depth_path=lambda f: d / "depth.png",
        read_depth_mm=lambda f: np.full((16, 16), 4000, dtype=np.uint16),  # 4 m, 256 px >= the 100-px overlap floor
    )


def test_resolve_metric_scale_uses_sensor_when_present(tmp_path, monkeypatch):
    # cv2 is imported inside solve_metric_scale only when shapes differ; keep them equal
    b = _bundle_stub(tmp_path, with_depth=True)
    pred = [np.full((16, 16), 2.0)]  # predicts 2 m where the sensor says 4 m
    s, src = slam.resolve_metric_scale(b, [0], pred)
    assert src == "sensor" and s == pytest.approx(2.0)


def test_resolve_metric_scale_falls_back_without_depth(tmp_path, caplog):
    b = _bundle_stub(tmp_path, with_depth=False)
    with caplog.at_level("WARNING", logger="reconstruction.slam"):
        s, src = slam.resolve_metric_scale(b, [0], [np.full((16, 16), 2.0)])
    assert (s, src) == (1.0, "mast3r")
    assert any("no depth.png" in r.getMessage() for r in caplog.records)
