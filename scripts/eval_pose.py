#!/usr/bin/env python3
"""Evaluate estimated camera poses against a ground-truth trajectory (Phase 3).

Reads a PerceptionBundle's poses.json + frame_times.json, associates each
estimated pose with the nearest ground-truth pose by timestamp, rigidly aligns
the two trajectories (SE(3) Umeyama / Kabsch — no scale, since RGB-D is metric),
and reports the Absolute Trajectory Error (ATE). Ground truth is a TUM-format
file: "timestamp tx ty tz qx qy qz qw" per line.

Pure numpy + stdlib, so it runs without open3d/opencv/evo. Also writes the
estimated trajectory in TUM format for optional cross-checking with `evo_ape`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def read_estimated(bundle_root: Path) -> tuple[np.ndarray, np.ndarray]:
    """Return (timestamps[N], T_world_camera[N,4,4]) ordered by frame_id."""
    records = json.loads((bundle_root / "poses.json").read_text())
    records.sort(key=lambda r: r["frame_id"])
    times = json.loads((bundle_root / "frame_times.json").read_text())
    if len(times) != len(records):
        raise SystemExit(
            f"poses ({len(records)}) and frame_times ({len(times)}) length mismatch"
        )
    Ts = np.array([r["T_world_camera"] for r in records], dtype=np.float64)
    return np.asarray(times, dtype=np.float64), Ts


def read_groundtruth(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Return (timestamps[M], positions[M,3]) from a TUM groundtruth.txt."""
    ts, xyz = [], []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = line.split()
        if len(p) < 8:
            continue
        ts.append(float(p[0]))
        xyz.append([float(p[1]), float(p[2]), float(p[3])])
    return np.asarray(ts, dtype=np.float64), np.asarray(xyz, dtype=np.float64)


def associate(est_t: np.ndarray, gt_t: np.ndarray, max_dt: float):
    """Nearest-timestamp association; returns index pairs (i_est, j_gt)."""
    order = np.argsort(gt_t)
    gt_sorted = gt_t[order]
    pairs = []
    for i, t in enumerate(est_t):
        k = int(np.searchsorted(gt_sorted, t))
        cands = [c for c in (k - 1, k) if 0 <= c < len(gt_sorted)]
        j = min(cands, key=lambda c: abs(gt_sorted[c] - t))
        if abs(gt_sorted[j] - t) <= max_dt:
            pairs.append((i, int(order[j])))
    return pairs


def kabsch(P: np.ndarray, Q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rigid R,t (no scale) mapping P onto Q, minimising ||R P + t - Q||."""
    muP, muQ = P.mean(0), Q.mean(0)
    H = (P - muP).T @ (Q - muQ)
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1.0, 1.0, d]) @ U.T
    return R, muQ - R @ muP


def rotmat_to_quat(R: np.ndarray) -> np.ndarray:
    """3x3 rotation -> [qx, qy, qz, qw] (TUM order)."""
    m = R
    tr = np.trace(m)
    if tr > 0:
        s = np.sqrt(tr + 1.0) * 2
        qw = 0.25 * s
        qx = (m[2, 1] - m[1, 2]) / s
        qy = (m[0, 2] - m[2, 0]) / s
        qz = (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        qw = (m[2, 1] - m[1, 2]) / s
        qx = 0.25 * s
        qy = (m[0, 1] + m[1, 0]) / s
        qz = (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        qw = (m[0, 2] - m[2, 0]) / s
        qx = (m[0, 1] + m[1, 0]) / s
        qy = 0.25 * s
        qz = (m[1, 2] + m[2, 1]) / s
    else:
        s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        qw = (m[1, 0] - m[0, 1]) / s
        qx = (m[0, 2] + m[2, 0]) / s
        qy = (m[1, 2] + m[2, 1]) / s
        qz = 0.25 * s
    return np.array([qx, qy, qz, qw])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--gt", required=True, type=Path)
    ap.add_argument("--max-dt", type=float, default=0.02, help="assoc tolerance (s)")
    ap.add_argument("--out", type=Path, default=Path("estimated.tum"))
    args = ap.parse_args()

    est_t, est_T = read_estimated(args.bundle)
    gt_t, gt_xyz = read_groundtruth(args.gt)
    est_xyz = est_T[:, :3, 3]

    pairs = associate(est_t, gt_t, args.max_dt)
    if len(pairs) < 3:
        raise SystemExit(f"only {len(pairs)} associations (<3) — check timestamps/tol")

    P = np.array([est_xyz[i] for i, _ in pairs])
    Q = np.array([gt_xyz[j] for _, j in pairs])
    R, t = kabsch(P, Q)
    aligned = (R @ P.T).T + t
    err = np.linalg.norm(aligned - Q, axis=1)

    # estimated.tum for optional `evo_ape tum gt.txt estimated.tum -a`
    with args.out.open("w") as f:
        for ti, Ti in zip(est_t, est_T):
            x, y, z = Ti[:3, 3]
            qx, qy, qz, qw = rotmat_to_quat(Ti[:3, :3])
            f.write(f"{ti:.6f} {x} {y} {z} {qx} {qy} {qz} {qw}\n")

    print(f"frames estimated:   {len(est_t)}")
    print(f"gt poses:           {len(gt_t)}")
    print(f"associated pairs:   {len(pairs)}  (|dt| <= {args.max_dt}s)")
    print(f"trajectory length:  {np.linalg.norm(np.diff(Q, axis=0), axis=1).sum():.3f} m")
    print("--- ATE (SE(3)-aligned position error) ---")
    print(f"  RMSE:   {np.sqrt((err**2).mean())*100:.2f} cm")
    print(f"  mean:   {err.mean()*100:.2f} cm")
    print(f"  median: {np.median(err)*100:.2f} cm")
    print(f"  max:    {err.max()*100:.2f} cm")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
