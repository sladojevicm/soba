"""Cross-scene TSDF + confidence-gate report over a directory of bundles.

For each bundle: lists objects, runs the Step-5 gate (tier-configurable) on every
object's raw cloud, counts tsdf-vs-generative routing, and (optionally) TSDF-fuses
the single best-observed object to confirm a mesh comes out with sane dimensions.

Example:
  PYTHONPATH=src python scripts/report_scenes.py \
    --bundles ~/soba/data/replica/bundles --tier 2 --fuse-best
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from perception.bundle import PerceptionBundle
from reconstruction import confidence as cf
from reconstruction import observed_cloud, tsdf


def gate_scene(bundle, tier, params):
    poses = bundle.read_poses()
    K = bundle.intrinsics.matrix()
    classes = {}
    for fid in bundle.iter_frame_ids():
        for d in bundle.read_objects(fid):
            classes.setdefault(int(d["track_id"]), d.get("class", "obj"))
    rows = []
    for tid in tsdf._all_track_ids(bundle):
        fids, frames = tsdf._object_frames(bundle, tid, poses)
        cloud, keep = observed_cloud.accumulate_object_cloud(
            frames, K, voxel_size=params["voxel_size_m"] or 0.005, return_keep=True
        )
        cams = np.array([poses[fids[i]][:3, 3] for i in keep]) if keep else np.empty((0, 3))
        res = cf.gate_object(cloud, cams, tier)
        res.update(track_id=tid, cls=classes.get(tid, "obj"),
                   n=len(keep), cloud=cloud)
        rows.append(res)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundles", required=True, type=Path)
    ap.add_argument("--tier", type=int, default=2, choices=[1, 2, 3, 4])
    ap.add_argument("--fuse-best", action="store_true",
                    help="TSDF-fuse the best-observed object per scene as a smoke test")
    args = ap.parse_args()

    params = cf.tier_params(args.tier)
    scenes = sorted(p for p in args.bundles.iterdir()
                    if (p / "manifest.json").exists())
    print(f"{len(scenes)} scenes, tier {args.tier} "
          f"(angular>{params['angular_deg']} AND completeness>{params['completeness']})\n")

    grand = {"tsdf": 0, "generative": 0, "objects": 0}
    for sp in scenes:
        b = PerceptionBundle.open(sp)
        rows = gate_scene(b, args.tier, params)
        n_tsdf = sum(r["strategy"] == "tsdf" for r in rows)
        best = max(rows, key=lambda r: (r["angular_coverage_deg"] or 0), default=None)
        grand["tsdf"] += n_tsdf
        grand["generative"] += len(rows) - n_tsdf
        grand["objects"] += len(rows)
        classes = sorted({r["cls"] for r in rows})
        bang = best["angular_coverage_deg"] if best else 0
        print(f"{sp.name:10} {len(rows):2d} objs  {n_tsdf:2d} tsdf / {len(rows)-n_tsdf:2d} gen  "
              f"max-angle {bang:5.1f}°  best={best['cls'] if best else '-'}  "
              f"classes={classes}")

        if args.fuse_best and best is not None and len(best["cloud"]):
            meshes = tsdf.fuse(b, track_ids=[best["track_id"]],
                               voxel_size=params["voxel_size_m"] or 0.004)
            m = meshes[best["track_id"]]
            v = np.asarray(m.vertices)
            if len(v):
                ext = v.max(0) - v.min(0)
                print(f"           └ TSDF {best['cls']}#{best['track_id']}: "
                      f"{len(v)} verts, bbox {ext[0]:.2f}x{ext[1]:.2f}x{ext[2]:.2f} m, "
                      f"watertight={m.is_watertight()}")

    print(f"\nTOTAL: {grand['objects']} objects across {len(scenes)} scenes -> "
          f"{grand['tsdf']} tsdf, {grand['generative']} generative")


if __name__ == "__main__":
    main()
