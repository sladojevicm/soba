#!/usr/bin/env python
"""Run the Step-5 confidence gate (Phase 6) over a PerceptionBundle.

For each object, builds the RAW observed cloud (Step 4 Part A) and the per-frame
camera positions, scores angular coverage + surface completeness, and routes it
to "tsdf" or "generative" at the chosen tier. Writes confidence/{track_id}.json
and prints a summary.

Example:
  PYTHONPATH=src python scripts/run_gate.py \
    --bundle ~/projects/vid2sim/data/replica/bundle_room0 --tier 2
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from perception.bundle import PerceptionBundle
from reconstruction import confidence as cf
from reconstruction import observed_cloud
from reconstruction import tsdf  # _object_frames / _all_track_ids helpers


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--tier", type=int, default=2, choices=[1, 2, 3, 4])
    ap.add_argument("--motion-filter", action="store_true",
                    help="apply the Z-T keep-frame filter when building clouds")
    ap.add_argument("--out", type=Path, default=None,
                    help="output dir for confidence/{track_id}.json "
                         "(default: <bundle>/confidence)")
    args = ap.parse_args()

    bundle = PerceptionBundle.open(args.bundle)
    poses = bundle.read_poses()
    K = bundle.intrinsics.matrix()
    params = cf.tier_params(args.tier)
    out_dir = args.out or (args.bundle / "confidence")
    out_dir.mkdir(parents=True, exist_ok=True)

    track_ids = tsdf._all_track_ids(bundle)
    classes: dict[int, str] = {}
    for fid in bundle.iter_frame_ids():
        for d in bundle.read_objects(fid):
            classes.setdefault(int(d["track_id"]), d.get("class", "obj"))

    print(f"bundle: {args.bundle}   tier {args.tier} "
          f"(angular>{params['angular_deg']} AND completeness>{params['completeness']})")
    print(f"{'track':>5} {'class':14} {'frames':>6} {'angle°':>7} {'compl':>6}  strategy")

    counts = {cf.TSDF: 0, cf.GENERATIVE: 0}
    for tid in track_ids:
        fids, frames = tsdf._object_frames(bundle, tid, poses)
        cloud, keep = observed_cloud.accumulate_object_cloud(
            frames, K, voxel_size=params["voxel_size_m"] or 0.005,
            motion_filter=args.motion_filter, return_keep=True,
        )
        kept_fids = [fids[i] for i in keep]
        cam_positions = np.array([poses[f][:3, 3] for f in kept_fids])
        result = cf.gate_object(cloud, cam_positions, args.tier)
        result["track_id"] = tid
        result["class"] = classes.get(tid, "obj")
        result["n_frames"] = len(kept_fids)
        with (out_dir / f"{tid}.json").open("w") as fh:
            json.dump(result, fh, indent=2)
        counts[result["strategy"]] += 1
        a = result["angular_coverage_deg"]
        c = result["completeness_ratio"]
        print(f"{tid:>5} {classes.get(tid, 'obj'):14} {len(kept_fids):>6} "
              f"{(a if a is not None else 0):>7.1f} {(c if c is not None else 0):>6.3f}  "
              f"{result['strategy']}")

    print(f"\n-> {counts[cf.TSDF]} tsdf, {counts[cf.GENERATIVE]} generative "
          f"(confidence/*.json in {out_dir})")


if __name__ == "__main__":
    main()
