"""Trajectory evaluation metrics (TUM RGB-D protocol) — pure numpy.

Shared by scripts/eval_pose.py and scripts/bench_tum_pose.py so the ATE/RPE
math is unit-testable. Conventions follow Sturm et al., "A Benchmark for the
Evaluation of RGB-D SLAM Systems" (IROS 2012):

* association: nearest ground-truth timestamp within a tolerance;
* ATE: align estimated positions onto GT with Umeyama — SE(3) (no scale,
  because the pipeline claims METRIC poses) AND Sim(3) (with scale); the
  recovered scale factor is itself a diagnostic of slam.solve_metric_scale;
* RPE: relative pose error over a fixed time delta (default 1 s),
  E = (Q_i^-1 Q_j)^-1 (P_i^-1 P_j); we report the translational RMSE, i.e.
  drift per second.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


# ---------------------------------------------------------------- IO helpers

def read_tum_trajectory(path: Path | str) -> tuple[np.ndarray, np.ndarray]:
    """TUM-format file ("t tx ty tz qx qy qz qw") -> (t[N], T_world_cam[N,4,4])."""
    ts, Ts = [], []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = line.split()
        if len(p) < 8:
            continue
        ts.append(float(p[0]))
        T = np.eye(4)
        T[:3, :3] = quat_to_rotmat(np.array([float(x) for x in p[4:8]]))
        T[:3, 3] = [float(p[1]), float(p[2]), float(p[3])]
        Ts.append(T)
    return np.asarray(ts, dtype=np.float64), np.asarray(Ts, dtype=np.float64)


def quat_to_rotmat(q: np.ndarray) -> np.ndarray:
    """[qx, qy, qz, qw] (TUM order) -> 3x3 rotation matrix."""
    x, y, z, w = np.asarray(q, dtype=np.float64) / np.linalg.norm(q)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def associate(est_t: np.ndarray, gt_t: np.ndarray, max_dt: float = 0.02) -> list[tuple[int, int]]:
    """Nearest-timestamp association; returns (i_est, j_gt) index pairs."""
    order = np.argsort(gt_t)
    gt_sorted = gt_t[order]
    pairs = []
    for i, t in enumerate(np.asarray(est_t, dtype=np.float64)):
        k = int(np.searchsorted(gt_sorted, t))
        cands = [c for c in (k - 1, k) if 0 <= c < len(gt_sorted)]
        if not cands:
            continue
        j = min(cands, key=lambda c: abs(gt_sorted[c] - t))
        if abs(gt_sorted[j] - t) <= max_dt:
            pairs.append((i, int(order[j])))
    return pairs


# ------------------------------------------------------------------ ATE math

def umeyama(P: np.ndarray, Q: np.ndarray, with_scale: bool) -> tuple[float, np.ndarray, np.ndarray]:
    """Umeyama alignment: (s, R, t) minimising ||s R P + t - Q||^2.

    with_scale=False fixes s=1 (SE(3) / Kabsch); True solves Sim(3).
    P, Q: [N,3] corresponding points.
    """
    P = np.asarray(P, dtype=np.float64)
    Q = np.asarray(Q, dtype=np.float64)
    muP, muQ = P.mean(0), Q.mean(0)
    X, Y = P - muP, Q - muQ
    Sigma = (Y.T @ X) / len(P)
    U, D, Vt = np.linalg.svd(Sigma)
    S = np.diag([1.0, 1.0, np.sign(np.linalg.det(U) * np.linalg.det(Vt))])
    R = U @ S @ Vt
    if with_scale:
        varP = (X ** 2).sum() / len(P)
        s = float(np.trace(np.diag(D) @ S) / varP) if varP > 0 else 1.0
    else:
        s = 1.0
    t = muQ - s * (R @ muP)
    return s, R, t


def ate(P: np.ndarray, Q: np.ndarray, with_scale: bool) -> dict:
    """Absolute trajectory error of estimated positions P vs GT positions Q.

    Returns {rmse, mean, median, max, scale} in the units of the input
    (metres for TUM). `scale` is the Umeyama scale actually applied
    (1.0 when with_scale=False).
    """
    s, R, t = umeyama(P, Q, with_scale)
    err = np.linalg.norm((s * (R @ np.asarray(P).T)).T + t - Q, axis=1)
    return {
        "rmse": float(np.sqrt((err ** 2).mean())),
        "mean": float(err.mean()),
        "median": float(np.median(err)),
        "max": float(err.max()),
        "scale": s,
    }


# ------------------------------------------------------------------ RPE math

def rpe_translation(est_t: np.ndarray, est_T: np.ndarray,
                    gt_t: np.ndarray, gt_T: np.ndarray,
                    delta_s: float = 1.0, max_dt: float = 0.02,
                    delta_tol: float = 0.2) -> dict:
    """Translational RPE over a fixed time delta (TUM evaluate_rpe protocol).

    For every associated pair i (est<->gt) find the associated pair j whose
    est timestamp is closest to t_i + delta_s (within delta_s*delta_tol);
    E = (gt_i^-1 gt_j)^-1 (est_i^-1 est_j); error = ||trans(E)||.
    Returns {rmse, mean, median, max, pairs} — units/second when delta_s=1.
    """
    est_t = np.asarray(est_t, dtype=np.float64)
    pairs = associate(est_t, np.asarray(gt_t, dtype=np.float64), max_dt)
    if len(pairs) < 2:
        return {"rmse": float("nan"), "mean": float("nan"),
                "median": float("nan"), "max": float("nan"), "pairs": 0}
    idx_est = np.array([i for i, _ in pairs])
    t_assoc = est_t[idx_est]
    errs = []
    for a, (i, gi) in enumerate(pairs):
        tj = est_t[i] + delta_s
        b = int(np.argmin(np.abs(t_assoc - tj)))
        if abs(t_assoc[b] - tj) > delta_s * delta_tol or b == a:
            continue
        j, gj = pairs[b]
        rel_est = np.linalg.inv(est_T[i]) @ est_T[j]
        rel_gt = np.linalg.inv(gt_T[gi]) @ gt_T[gj]
        E = np.linalg.inv(rel_gt) @ rel_est
        errs.append(np.linalg.norm(E[:3, 3]))
    if not errs:
        return {"rmse": float("nan"), "mean": float("nan"),
                "median": float("nan"), "max": float("nan"), "pairs": 0}
    errs = np.asarray(errs)
    return {
        "rmse": float(np.sqrt((errs ** 2).mean())),
        "mean": float(errs.mean()),
        "median": float(np.median(errs)),
        "max": float(errs.max()),
        "pairs": int(len(errs)),
    }


# ------------------------------------------------------- one-call evaluation

def evaluate_trajectory(est_t: np.ndarray, est_T: np.ndarray,
                        gt_t: np.ndarray, gt_T: np.ndarray,
                        max_dt: float = 0.02, rpe_delta_s: float = 1.0) -> dict:
    """Full report: SE(3) ATE, Sim(3) ATE (+scale), translational RPE."""
    pairs = associate(np.asarray(est_t, dtype=np.float64),
                      np.asarray(gt_t, dtype=np.float64), max_dt)
    if len(pairs) < 3:
        raise ValueError(f"only {len(pairs)} associations (<3) — check timestamps")
    P = np.asarray(est_T)[[i for i, _ in pairs], :3, 3]
    Q = np.asarray(gt_T)[[j for _, j in pairs], :3, 3]
    gt_len = float(np.linalg.norm(np.diff(Q, axis=0), axis=1).sum())
    return {
        "n_est": int(len(est_t)),
        "n_pairs": int(len(pairs)),
        "gt_traj_len_m": gt_len,
        "ate_se3": ate(P, Q, with_scale=False),
        "ate_sim3": ate(P, Q, with_scale=True),
        "rpe_1s": rpe_translation(est_t, est_T, gt_t, gt_T,
                                  delta_s=rpe_delta_s, max_dt=max_dt),
    }
