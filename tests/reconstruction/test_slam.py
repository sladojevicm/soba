"""Pose-estimation logic tests (method selection + pose composition)."""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from reconstruction import slam


def _translation(dx=0.0, dy=0.0, dz=0.0):
    T = np.eye(4)
    T[:3, 3] = [dx, dy, dz]
    return T


def test_method_for_tier():
    assert slam.method_for_tier(1) == "odometry"
    assert slam.method_for_tier(2) == "mast3r"
    assert slam.method_for_tier(3) == "mast3r"
    assert slam.method_for_tier(4) == "orbslam3"
    with pytest.raises(ValueError):
        slam.method_for_tier(9)


def test_compose_poses_identity_chain():
    poses = slam.compose_poses([np.eye(4), np.eye(4)])
    assert len(poses) == 3
    for p in poses:
        np.testing.assert_allclose(p, np.eye(4))


def test_compose_poses_accumulates_translation():
    step = np.eye(4)
    step[:3, 3] = [0.1, 0.0, 0.0]
    poses = slam.compose_poses([step, step, step])
    # drift accumulates: x advances 0.1 each frame
    assert [round(p[0, 3], 3) for p in poses] == [0.0, 0.1, 0.2, 0.3]


def test_odometry_convention_forward_motion():
    # Locks the sign convention against re-introducing an inverse in
    # RgbdOdometry. If the camera steps +0.1 m in world X between prev and cur,
    # a static point's coords in cur shift -0.1 X vs prev, so the source->target
    # transform Open3D returns is T_{prev<-cur} = +0.1 X translation. Fed to
    # compose_poses as-is, the world pose must advance +0.1 (forward), not -0.1.
    T_prev_cur = np.eye(4)
    T_prev_cur[:3, 3] = [0.1, 0.0, 0.0]
    poses = slam.compose_poses([T_prev_cur, T_prev_cur])
    assert [round(p[0, 3], 3) for p in poses] == [0.0, 0.1, 0.2]


def test_compose_poses_rejects_bad_shape():
    with pytest.raises(ValueError):
        slam.compose_poses([np.eye(3)])


def test_make_estimator_types():
    assert isinstance(slam.make_estimator(1), slam.RgbdOdometry)
    assert isinstance(slam.make_estimator(2), slam.Mast3rEstimator)
    assert isinstance(slam.make_estimator(4), slam.OrbSlam3Estimator)


def test_unbuilt_estimators_raise():
    with pytest.raises(NotImplementedError):
        slam.Mast3rEstimator().estimate(None)
    with pytest.raises(NotImplementedError):
        slam.OrbSlam3Estimator().estimate(None)


def test_compose_recovers_known_world_poses():
    # The relative compose_poses consumes is inv(T_i) @ T_{i+1} (the pose of
    # camera i+1 expressed in camera i). Feeding those back must recover the
    # absolute poses — this pins the @-convention the odometry path depends on.
    world = [_translation(), _translation(0.1, 0.0, 0.0), _translation(0.1, 0.3, 0.0)]
    relatives = [np.linalg.inv(world[i]) @ world[i + 1] for i in range(len(world) - 1)]
    recovered = slam.compose_poses(relatives)
    for got, want in zip(recovered, world):
        np.testing.assert_allclose(got, want, atol=1e-9)


def test_rgbd_odometry_does_not_invert_relative(monkeypatch):
    # Regression for the mirrored-trajectory bug. Open3D's compute_rgbd_odometry
    # returns the cur->prev (source->target) point transform, which compose_poses
    # consumes DIRECTLY; if estimate() re-introduces an inverse the camera path
    # mirrors through the origin. Stub Open3D to return a fixed +x step and assert
    # the trajectory advances +x (not -x).
    step = _translation(0.1, 0.0, 0.0)

    o3d = types.ModuleType("open3d")
    o3d.camera = types.SimpleNamespace(PinholeCameraIntrinsic=lambda *a, **k: object())
    o3d.geometry = types.SimpleNamespace(
        Image=lambda *a, **k: object(),
        RGBDImage=types.SimpleNamespace(
            create_from_color_and_depth=lambda *a, **k: object()
        ),
    )
    o3d.pipelines = types.SimpleNamespace(
        odometry=types.SimpleNamespace(
            compute_rgbd_odometry=lambda *a, **k: (True, np.array(step), np.eye(6)),
            OdometryOption=lambda: object(),
            RGBDOdometryJacobianFromHybridTerm=lambda: object(),
        )
    )
    monkeypatch.setitem(sys.modules, "open3d", o3d)

    bundle = types.SimpleNamespace(
        intrinsics=types.SimpleNamespace(fx=100.0, fy=100.0, cx=2.0, cy=2.0),
        iter_frame_ids=lambda: iter([0, 1, 2]),
        read_depth_mm=lambda fid: np.zeros((4, 5), dtype=np.uint16),
        read_rgb=lambda fid: np.zeros((4, 5, 3), dtype=np.uint8),
    )

    poses = slam.RgbdOdometry().estimate(bundle)
    assert [round(p[0, 3], 3) for p in poses] == [0.0, 0.1, 0.2]
