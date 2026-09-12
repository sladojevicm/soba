#!/usr/bin/env python
"""Per-object data-loss audit (the STATUS top-priority item #2).

Follows every tracked object's pixels through each lossy stage of the front
end and reports where points die, so "the couch has no legs" can be blamed on
a specific knob instead of guessed at:

  frames    frames in which the object has a non-empty mask
  raw_px    masked pixels with ANY depth (>0), summed over frames
  near/far  raw_px lost to the depth gate (< --min-mm / > --max-mm)
  gated     pixels surviving the depth gate = back-projected world points
  cells     unique occupied voxel cells at --voxel (== the gate-cloud size the
            confidence gate scores; the downsample keeps one point per cell)
  tsdf_w3   TSDF mesh vertices at Open3D's DEFAULT weight_threshold (~3 —
            voxels observed <3x are DROPPED; the thin-legs suspect)
  tsdf_w1   the SAME grid extracted at weight_threshold=1 (every observed
            voxel counts) — the w1/w3 gap is geometry the default throws away
  dims      TSDF mesh bbox at w3 (sanity: couch ~2.34x0.90x1.06 on office_3)

TSDF stages need --tsdf (a full fusion pass; minutes on big bundles).

Run:
  PYTHONPATH=src python scripts/audit_data_loss.py \
      --bundle ~/soba/data/replica/bundles/office_3 [--tsdf]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from perception.bundle import PerceptionBundle  # noqa: E402
from reconstruction import observed_cloud, tsdf  # noqa: E402

# Pack a voxel key (kx,ky,kz) into one int64 so per-object "unique occupied
# cells" is a set of ints, not tuples. 8192 cells/axis @5mm = +/-20m — plenty.
_OFF, _MOD = 4096, 8192


def _cell_keys(world: np.ndarray, voxel: float) -> np.ndarray:
    k = np.floor(world / voxel).astype(np.int64) + _OFF
    return (k[:, 0] + k[:, 1] * _MOD + k[:, 2] * _MOD * _MOD)


def audit(bundle_dir: Path, *, voxel: float, min_mm: int, max_mm: int,
          tracks: list[int] | None, run_tsdf: bool, tsdf_voxel: float) -> dict:
    b = PerceptionBundle.open(bundle_dir)
    K = b.intrinsics.matrix()
    poses = b.read_poses()

    # track_id -> class name (first detection wins)
    classes: dict[int, str] = {}
    for fid in b.iter_frame_ids():
        for d in b.read_objects(fid):
            classes.setdefault(int(d["track_id"]), d.get("class", "obj"))
    tids = tracks if tracks else sorted(classes)

    stats = {
        tid: {"class": classes.get(tid, "?"), "frames": 0, "raw_px": 0,
              "lost_near": 0, "lost_far": 0, "gated": 0,
              "cells": set(), "lo": None, "hi": None}
        for tid in tids
    }

    # One pass over the bundle: depth read once per frame (the Step-4 discipline).
    for fid in b.iter_frame_ids():
        depth = None
        for tid in tids:
            if not b.mask_path(fid, tid).exists():
                continue
            mask = np.asarray(b.read_mask(fid, tid)) > 0
            if not mask.any():
                continue
            if depth is None:
                depth = np.asarray(b.read_depth_mm(fid))
            s = stats[tid]
            d = depth[mask]
            has = d > 0
            if not has.any():
                continue
            s["frames"] += 1
            s["raw_px"] += int(has.sum())
            s["lost_near"] += int((has & (d < min_mm)).sum())
            s["lost_far"] += int((d > max_mm).sum())

            cam = observed_cloud.backproject(depth, K, mask=mask,
                                             min_mm=min_mm, max_mm=max_mm)
            if not cam.size:
                continue
            world = observed_cloud.to_world(cam, poses[fid])
            s["gated"] += len(world)
            s["cells"].update(np.unique(_cell_keys(world, voxel)).tolist())
            lo, hi = world.min(axis=0), world.max(axis=0)
            s["lo"] = lo if s["lo"] is None else np.minimum(s["lo"], lo)
            s["hi"] = hi if s["hi"] is None else np.maximum(s["hi"], hi)

    for s in stats.values():
        s["cells"] = len(s["cells"])
        ext = (s["hi"] - s["lo"]) if s["lo"] is not None else np.zeros(3)
        s["cloud_dims"] = [round(float(x), 3) for x in ext]
        del s["lo"], s["hi"]

    if run_tsdf:
        # One fusion, two extractions: the w1-vs-w3 delta is pure weight-threshold
        # loss, measured on the SAME grid.
        alive = [t for t in tids if stats[t]["gated"] > 0]
        _, grids = tsdf.fuse(b, alive, voxel_size=tsdf_voxel, min_mm=min_mm,
                             max_mm=max_mm, return_grids=True)
        for tid, vbg in grids.items():
            m3 = vbg.extract_triangle_mesh().to_legacy()          # default ~3
            m1 = vbg.extract_triangle_mesh(weight_threshold=1.0).to_legacy()
            s = stats[tid]
            s["tsdf_w3"], s["tsdf_w1"] = len(m3.vertices), len(m1.vertices)
            if len(m3.vertices):
                ext = m3.get_max_bound() - m3.get_min_bound()
                s["tsdf_dims"] = [round(float(x), 3) for x in ext]

    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--voxel", type=float, default=0.005,
                    help="gate-cloud downsample cell (m), default 0.005")
    ap.add_argument("--min-mm", type=int, default=observed_cloud.DEFAULT_MIN_MM)
    ap.add_argument("--max-mm", type=int, default=observed_cloud.DEFAULT_MAX_MM)
    ap.add_argument("--tracks", type=int, nargs="*", default=None)
    ap.add_argument("--tsdf", action="store_true",
                    help="also fuse TSDF and report w1-vs-w3 vertex loss (slow)")
    ap.add_argument("--tsdf-voxel", type=float, default=0.004)
    ap.add_argument("--json", type=Path, default=None,
                    help="also dump the numbers to this file")
    a = ap.parse_args()

    stats = audit(a.bundle, voxel=a.voxel, min_mm=a.min_mm, max_mm=a.max_mm,
                  tracks=a.tracks, run_tsdf=a.tsdf, tsdf_voxel=a.tsdf_voxel)

    hdr = (f"{'tid':>4} {'class':<14} {'frames':>6} {'raw_px':>12} {'near':>9} "
           f"{'far':>7} {'gated':>12} {'cells':>9}")
    tail = f" {'tsdf_w3':>9} {'tsdf_w1':>9} {'w3/w1':>6}  dims(w3)" if a.tsdf else ""
    print(hdr + tail)
    for tid, s in sorted(stats.items()):
        line = (f"{tid:>4} {s['class']:<14} {s['frames']:>6} {s['raw_px']:>12,} "
                f"{s['lost_near']:>9,} {s['lost_far']:>7,} {s['gated']:>12,} "
                f"{s['cells']:>9,}")
        if a.tsdf and "tsdf_w3" in s:
            frac = s["tsdf_w3"] / s["tsdf_w1"] if s["tsdf_w1"] else 0.0
            line += (f" {s['tsdf_w3']:>9,} {s['tsdf_w1']:>9,} {frac:>6.2f}"
                     f"  {s.get('tsdf_dims', '-')}")
        print(line)
    print("\ncloud dims (m): " + ", ".join(
        f"{tid}:{s['class']}={s['cloud_dims']}" for tid, s in sorted(stats.items())))

    if a.json:
        a.json.write_text(json.dumps(stats, indent=2, default=str))
        print(f"wrote {a.json}")


if __name__ == "__main__":
    main()
