#!/usr/bin/env python
"""Build a TUM bundle with REAL YOLO detections, then REAL SAM2 refinement.

This is the Build-Order Phase-4 validation the fake backend couldn't give us:
  1. TUMReader + perception.detect.yolo_detector -> bundle with per-frame
     objects.json + mask_{track_id}.png (host-side YOLO + IoU tracker).
  2. sam2_refine.refine_masks with the real Sam2VideoPredictor -> pixel-perfect
     masks + best-frame crops. force=True because TUM is not a GT-mask dataset
     but ALSO not in GROUND_TRUTH_SOURCES — the flag documents intent.

Run (weights: yolo auto-downloads; sam2 checkpoint from --sam2-ckpt):
  PYTHONPATH=src python scripts/run_tum_detect.py \
      --seq ~/projects/vid2sim/data/tum/rgbd_dataset_freiburg1_xyz \
      --out ~/projects/vid2sim/data/tum/bundle_f1xyz_yolo --max-frames 100
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from perception.dataset_reader import TUMReader  # noqa: E402
from perception.detect import yolo_detector  # noqa: E402
from reconstruction import sam2_refine  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seq", type=Path, required=True, help="TUM sequence dir")
    ap.add_argument("--out", type=Path, required=True, help="bundle output dir")
    ap.add_argument("--max-frames", type=int, default=100)
    ap.add_argument("--model", default="yolo11s-seg.pt")
    ap.add_argument("--conf", type=float, default=0.4)
    ap.add_argument("--sam2-ckpt",
                    default=str(Path.home() / "projects/vid2sim/models/sam2.1_hiera_large.pt"))
    ap.add_argument("--sam2-cfg", default="configs/sam2.1/sam2.1_hiera_l.yaml")
    ap.add_argument("--skip-sam2", action="store_true",
                    help="stop after YOLO (inspect raw detections first)")
    ap.add_argument("--min-track-frames", type=int, default=10,
                    help="only SAM2-refine tracks detected in >= this many "
                         "frames (drops 1-frame YOLO flicker phantoms)")
    a = ap.parse_args()

    print(f"YOLO ({a.model}) over {a.seq.name}, first {a.max_frames} frames ...")
    bundle = TUMReader(a.seq).to_bundle(
        a.out, max_frames=a.max_frames, detector=yolo_detector(a.model, a.conf))

    # Detection summary: what YOLO found and how stable the tracks are.
    per_track: dict[int, dict] = {}
    for fid in bundle.iter_frame_ids():
        for d in bundle.read_objects(fid):
            t = per_track.setdefault(int(d["track_id"]),
                                     {"class": d["class"], "frames": 0, "conf": 0.0})
            t["frames"] += 1
            t["conf"] = max(t["conf"], float(d["confidence"]))
    print(f"tracks: {len(per_track)}")
    for tid, t in sorted(per_track.items()):
        print(f"  track {tid:>3} {t['class']:<14} {t['frames']:>4} frames "
              f"max_conf {t['conf']:.2f}")

    if a.skip_sam2:
        return
    stable = {tid for tid, t in per_track.items()
              if t["frames"] >= a.min_track_frames}
    print(f"SAM2 ({Path(a.sam2_ckpt).name}) refining {len(stable)} stable "
          f"track(s) (>= {a.min_track_frames} frames; "
          f"{len(per_track) - len(stable)} flicker tracks skipped) ...")
    summary = sam2_refine.refine_masks(
        bundle,
        sam2_refine.Sam2VideoPredictor(a.sam2_ckpt, model_cfg=a.sam2_cfg),
        force=True,
        track_ids=stable,
    )
    for tid, s in sorted(summary.items()):
        print(f"  track {tid:>3}: {s['frames']} refined frames, "
              f"best_frame {s['best_frame']}")
    print(f"bundle ready: {bundle.root}")


if __name__ == "__main__":
    main()
