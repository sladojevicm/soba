"""Pose-estimation logic tests (method selection + pose composition)."""

from __future__ import annotations

import numpy as np
import pytest

from reconstruction import slam


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
