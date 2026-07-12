#!/usr/bin/env python3
"""Evaluate estimated camera poses against a ground-truth trajectory (Phase 3).

Reads a PerceptionBundle's poses.json + frame_times.json (or an explicit
--poses file), associates each estimated pose with the nearest ground-truth
pose by timestamp (TUM protocol), and reports:

* ATE after SE(3) Umeyama alignment (no scale — the pipeline claims METRIC
  poses, so this is the honest headline number);
* ATE after Sim(3) alignment, WITH the recovered scale factor printed — the
  scale is a diagnostic of slam.solve_metric_scale (1.00 = truly metric);
* translational RPE over 1 s (drift per second).

Ground truth is a TUM-format file: "t tx ty tz qx qy qz qw" per line.
Metric math lives in src/reconstruction/traj_eval.py (unit-tested); this
script is the CLI. Also writes the estimated trajectory in TUM format for
optional cross-checking with `evo_ape`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reconstruction import traj_eval  # noqa: E402


def read_estimated(bundle_root: Path, poses_file: Path | None = None
                   ) -> tuple[np.ndarray, np.ndarray]:
    """Return (timestamps[N], T_world_camera[N,4,4]) ordered by frame_id."""
    records = json.loads((poses_file or bundle_root / "poses.json").read_text())
    records.sort(key=lambda r: r["frame_id"])
    times = json.loads((bundle_root / "frame_times.json").read_text())
    if len(times) != len(records):
        raise SystemExit(
            f"poses ({len(records)}) and frame_times ({len(times)}) length mismatch"
        )
    Ts = np.array([r["T_world_camera"] for r in records], dtype=np.float64)
    return np.asarray(times, dtype=np.float64), Ts


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


def report(res: dict, max_dt: float) -> None:
    print(f"frames estimated:   {res['n_est']}")
    print(f"associated pairs:   {res['n_pairs']}  (|dt| <= {max_dt}s)")
    print(f"trajectory length:  {res['gt_traj_len_m']:.3f} m")
    a = res["ate_se3"]
    print("--- ATE, SE(3)-aligned (no scale — metric claim) ---")
    print(f"  RMSE:   {a['rmse'] * 100:.2f} cm")
    print(f"  mean:   {a['mean'] * 100:.2f} cm")
    print(f"  median: {a['median'] * 100:.2f} cm")
    print(f"  max:    {a['max'] * 100:.2f} cm")
    a = res["ate_sim3"]
    print(f"--- ATE, Sim(3)-aligned (recovered scale s = {a['scale']:.4f}) ---")
    print(f"  RMSE:   {a['rmse'] * 100:.2f} cm")
    r = res["rpe_1s"]
    print(f"--- RPE, translational drift over 1 s ({r['pairs']} pairs) ---")
    print(f"  RMSE:   {r['rmse'] * 100:.2f} cm/s")
    print(f"  median: {r['median'] * 100:.2f} cm/s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--gt", required=True, type=Path)
    ap.add_argument("--poses", type=Path, default=None,
                    help="poses json (default: <bundle>/poses.json); lets one "
                         "bundle carry poses_tier1.json / poses_mast3r.json etc.")
    ap.add_argument("--max-dt", type=float, default=0.02, help="assoc tolerance (s)")
    ap.add_argument("--out", type=Path, default=Path("estimated.tum"))
    args = ap.parse_args()

    est_t, est_T = read_estimated(args.bundle, args.poses)
    gt_t, gt_T = traj_eval.read_tum_trajectory(args.gt)
    res = traj_eval.evaluate_trajectory(est_t, est_T, gt_t, gt_T, max_dt=args.max_dt)

    # estimated.tum for optional `evo_ape tum gt.txt estimated.tum -a`
    with args.out.open("w") as f:
        for ti, Ti in zip(est_t, est_T):
            x, y, z = Ti[:3, 3]
            qx, qy, qz, qw = rotmat_to_quat(Ti[:3, :3])
            f.write(f"{ti:.6f} {x} {y} {z} {qx} {qy} {qz} {qw}\n")

    report(res, args.max_dt)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
