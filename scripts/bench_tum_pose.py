#!/usr/bin/env python3
"""Benchmark per-tier camera pose accuracy on TUM RGB-D sequences.

For each requested tier this runs the tier's pose method (slam.make_estimator:
tier 1 = RGB-D odometry, tiers 2/3 = MASt3R, tier 4 = ORB-SLAM3 which — not
being integrated — falls back to MASt3R with a warning) on a PerceptionBundle
built from a TUM sequence, saves the poses to <bundle>/poses_tier{N}.json,
and evaluates against groundtruth.txt with the TUM protocol (traj_eval):
SE(3)-aligned ATE, Sim(3)-aligned ATE + recovered scale, 1-s translational RPE.

GPU note: MASt3R needs ~6 GB VRAM — do not run tiers 2/3/4 while another GPU
job is active. SOBA_MAST3R_MAX_IMAGES (default 24) caps the anchor count
on 8 GB cards.

Example:
  PYTHONPATH=src ~/projects/soba/venv/bin/python scripts/bench_tum_pose.py \
      --seq ~/projects/soba/data/tum/rgbd_dataset_freiburg1_xyz \
      --bundle ~/projects/soba/data/tum/bundle_f1xyz \
      --tiers 1 2 --max-frames 500 --json results.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from perception.bundle import PerceptionBundle  # noqa: E402
from perception.dataset_reader import TUMReader  # noqa: E402
from reconstruction import slam, traj_eval  # noqa: E402


def ensure_bundle(seq: Path, bundle_dir: Path, max_frames: int) -> PerceptionBundle:
    if (bundle_dir / "manifest.json").exists():
        print(f"reusing bundle {bundle_dir}")
        return PerceptionBundle.open(bundle_dir)
    print(f"building bundle {bundle_dir} from {seq} (max {max_frames} frames) ...")
    return TUMReader(seq).to_bundle(bundle_dir, fps=30.0, max_frames=max_frames)


def main() -> None:
    logging.basicConfig(level=logging.INFO)  # surface the tier-4 fallback warning
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seq", type=Path, required=True, help="TUM sequence dir")
    ap.add_argument("--bundle", type=Path, required=True, help="bundle dir (built if missing)")
    ap.add_argument("--gt", type=Path, default=None,
                    help="groundtruth.txt (default: <seq>/groundtruth.txt)")
    ap.add_argument("--tiers", type=int, nargs="+", default=[1, 2],
                    help="tiers to run (2 and 3 are the same method; 4 falls back to MASt3R)")
    ap.add_argument("--max-frames", type=int, default=500)
    ap.add_argument("--max-dt", type=float, default=0.02)
    ap.add_argument("--json", type=Path, default=None, help="append results to this JSON file")
    args = ap.parse_args()

    bundle = ensure_bundle(args.seq, args.bundle, args.max_frames)
    gt_path = args.gt or args.seq / "groundtruth.txt"
    gt_t, gt_T = traj_eval.read_tum_trajectory(gt_path)
    est_t = np.asarray(json.loads((bundle.root / "frame_times.json").read_text()),
                       dtype=np.float64)

    all_results = []
    for tier in args.tiers:
        method = slam.method_for_tier(tier)
        poses_file = bundle.root / f"poses_tier{tier}.json"
        if poses_file.exists():
            print(f"\n=== tier {tier} ({method}): reusing {poses_file.name} ===")
            records = json.loads(poses_file.read_text())
            records.sort(key=lambda r: r["frame_id"])
            poses = [np.asarray(r["T_world_camera"]) for r in records]
            wall = None
        else:
            print(f"\n=== tier {tier} ({method}): estimating "
                  f"{bundle.manifest.frame_count} frames ===")
            t0 = time.time()
            poses = slam.make_estimator(tier).estimate(bundle)
            wall = time.time() - t0
            poses_file.write_text(json.dumps(
                [{"frame_id": i, "T_world_camera": np.asarray(T).tolist()}
                 for i, T in enumerate(poses)]))
            print(f"estimated in {wall:.1f}s -> {poses_file.name}")

        res = traj_eval.evaluate_trajectory(
            est_t, np.asarray(poses), gt_t, gt_T, max_dt=args.max_dt)
        res.update(tier=tier, method=method, wall_s=wall,
                   sequence=args.seq.name, bundle=str(bundle.root))
        all_results.append(res)
        print(f"pairs {res['n_pairs']}/{res['n_est']}, "
              f"gt length {res['gt_traj_len_m']:.2f} m")
        print(f"ATE SE(3)  RMSE {res['ate_se3']['rmse'] * 100:6.2f} cm  "
              f"(median {res['ate_se3']['median'] * 100:.2f})")
        print(f"ATE Sim(3) RMSE {res['ate_sim3']['rmse'] * 100:6.2f} cm  "
              f"(scale {res['ate_sim3']['scale']:.4f})")
        print(f"RPE 1s     RMSE {res['rpe_1s']['rmse'] * 100:6.2f} cm/s "
              f"({res['rpe_1s']['pairs']} pairs)")

    if args.json:
        existing = json.loads(args.json.read_text()) if args.json.exists() else []
        existing.extend(all_results)
        args.json.write_text(json.dumps(existing, indent=1))
        print(f"\nappended {len(all_results)} result(s) to {args.json}")


if __name__ == "__main__":
    main()
