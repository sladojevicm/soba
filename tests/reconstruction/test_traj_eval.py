"""traj_eval metric math on synthetic trajectories (no IO, fast)."""

from __future__ import annotations

import numpy as np
import pytest

from reconstruction import traj_eval


def _rot_z(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def _traj(n=50, dt=0.1):
    """Helix positions + tangent-ish rotations as [n,4,4] world poses."""
    t = np.arange(n) * dt
    T = np.tile(np.eye(4), (n, 1, 1))
    for i, ti in enumerate(t):
        T[i, :3, :3] = _rot_z(0.3 * ti)
        T[i, :3, 3] = [np.cos(ti), np.sin(ti), 0.1 * ti]
    return t, T


# ------------------------------------------------------------------ umeyama

def test_umeyama_recovers_known_sim3():
    rng = np.random.default_rng(0)
    P = rng.normal(size=(40, 3))
    R_true = _rot_z(0.7)
    s_true, t_true = 2.5, np.array([1.0, -2.0, 0.5])
    Q = (s_true * (R_true @ P.T)).T + t_true
    s, R, t = traj_eval.umeyama(P, Q, with_scale=True)
    assert s == pytest.approx(s_true, abs=1e-9)
    np.testing.assert_allclose(R, R_true, atol=1e-9)
    np.testing.assert_allclose(t, t_true, atol=1e-9)


def test_umeyama_no_scale_fixes_s_to_one():
    rng = np.random.default_rng(1)
    P = rng.normal(size=(30, 3))
    Q = (2.0 * P) @ _rot_z(0.2).T  # scaled data, but SE(3) mode must keep s=1
    s, R, t = traj_eval.umeyama(P, Q, with_scale=False)
    assert s == 1.0
    # rotation must still be a proper rotation
    assert np.linalg.det(R) == pytest.approx(1.0, abs=1e-9)
    np.testing.assert_allclose(R @ R.T, np.eye(3), atol=1e-9)


# ---------------------------------------------------------------------- ATE

def test_ate_zero_for_rigidly_moved_trajectory():
    _, T = _traj()
    P = T[:, :3, 3]
    Q = (P @ _rot_z(1.1).T) + np.array([3.0, 0.0, -1.0])
    res = traj_eval.ate(P, Q, with_scale=False)
    assert res["rmse"] == pytest.approx(0.0, abs=1e-9)
    assert res["scale"] == 1.0


def test_ate_sim3_absorbs_scale_and_reports_it():
    _, T = _traj()
    P = T[:, :3, 3]
    Q = 1.3 * P  # pure scale error
    se3 = traj_eval.ate(P, Q, with_scale=False)
    sim3 = traj_eval.ate(P, Q, with_scale=True)
    assert sim3["scale"] == pytest.approx(1.3, abs=1e-9)
    assert sim3["rmse"] == pytest.approx(0.0, abs=1e-9)
    assert se3["rmse"] > 0.05  # SE(3) cannot absorb the scale error


# ---------------------------------------------------------------- associate

def test_associate_nearest_within_tolerance():
    est_t = np.array([0.0, 1.0, 2.0, 10.0])
    gt_t = np.array([0.005, 1.5, 1.995])
    pairs = traj_eval.associate(est_t, gt_t, max_dt=0.02)
    assert pairs == [(0, 0), (2, 2)]  # 1.0 and 10.0 have no GT within 20 ms


# ---------------------------------------------------------------------- RPE

def test_rpe_zero_for_perfect_trajectory():
    t, T = _traj(n=60, dt=0.1)
    res = traj_eval.rpe_translation(t, T, t, T, delta_s=1.0)
    assert res["pairs"] > 0
    assert res["rmse"] == pytest.approx(0.0, abs=1e-12)


def test_rpe_measures_constant_drift_per_second():
    t, T = _traj(n=60, dt=0.1)
    drift = T.copy()
    drift[:, :3, 3] += np.outer(t, [0.02, 0.0, 0.0])  # 2 cm/s along world x
    res = traj_eval.rpe_translation(t, drift, t, T, delta_s=1.0)
    # relative translation error per 1 s pair should be ~2 cm
    assert res["rmse"] == pytest.approx(0.02, rel=0.05)


# --------------------------------------------------------------- end-to-end

def test_evaluate_trajectory_full_report():
    t, T = _traj(n=50, dt=0.1)
    est = T.copy()
    est[:, :3, 3] = 0.5 * est[:, :3, 3] @ _rot_z(0.4).T + np.array([1.0, 2.0, 3.0])
    res = traj_eval.evaluate_trajectory(t, est, t, T)
    assert res["n_pairs"] == 50
    assert res["ate_sim3"]["scale"] == pytest.approx(2.0, rel=1e-6)
    assert res["ate_sim3"]["rmse"] == pytest.approx(0.0, abs=1e-9)
    assert res["ate_se3"]["rmse"] > res["ate_sim3"]["rmse"]


def test_evaluate_trajectory_needs_three_pairs():
    t = np.array([0.0, 1.0])
    T = np.tile(np.eye(4), (2, 1, 1))
    with pytest.raises(ValueError, match="associations"):
        traj_eval.evaluate_trajectory(t, T, t + 100.0, T)


def test_read_tum_trajectory_and_quat(tmp_path):
    gt = tmp_path / "gt.txt"
    gt.write_text("# comment\n1.5 1 2 3 0 0 0 1\n2.5 4 5 6 0 0 0.7071068 0.7071068\n")
    ts, Ts = traj_eval.read_tum_trajectory(gt)
    np.testing.assert_allclose(ts, [1.5, 2.5])
    np.testing.assert_allclose(Ts[0], np.array(
        [[1, 0, 0, 1], [0, 1, 0, 2], [0, 0, 1, 3], [0, 0, 0, 1]]), atol=1e-12)
    # 90 deg about z
    np.testing.assert_allclose(Ts[1, :3, :3], _rot_z(np.pi / 2), atol=1e-6)
