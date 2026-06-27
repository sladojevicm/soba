#!/usr/bin/env python
"""Run TSDF fusion (Step 4 Part B / Phase 5) on a PerceptionBundle and export
per-object meshes for inspection in Blender.

Example:
  PYTHONPATH=src python scripts/run_tsdf.py \
    --bundle ~/projects/vid2sim/data/replica/bundle_room0 \
    --out    out/tsdf_room0 \
    --voxel  0.004 \
    --tracks 9 73 11

With no --tracks, every object in the bundle is fused. Meshes are written as
.ply (vertex colours, native Blender import). This is dev inspection only — the
real target is the browser (Step 11), much later.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

from perception.bundle import PerceptionBundle
from reconstruction import tsdf


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--voxel", type=float, default=tsdf.DEFAULT_VOXEL_SIZE,
                    help="voxel edge in metres (Tier2 .004, Tier3 .003, Tier4 .002)")
    ap.add_argument("--tracks", type=int, nargs="*", default=None,
                    help="track_ids to fuse (default: all objects in the bundle)")
    ap.add_argument("--device", default="CPU:0")
    ap.add_argument("--fmt", default="ply", choices=["ply", "glb", "obj"],
                    help="export format (ply/glb both import natively into Blender)")
    ap.add_argument("--conf-min", type=int, default=None,
                    help="zero depth where conf < this (skipped if no conf maps)")
    ap.add_argument("--motion-filter", action="store_true",
                    help="apply the Z-T keep-frame motion filter (for moving "
                         "objects; a no-op on a static scene like room_0)")
    args = ap.parse_args()

    bundle = PerceptionBundle.open(args.bundle)

    # track_id -> class label, for naming the exported files
    classes: dict[int, str] = {}
    for fid in bundle.iter_frame_ids():
        for d in bundle.read_objects(fid):
            classes.setdefault(int(d["track_id"]), d.get("class", "obj"))

    print(f"bundle: {args.bundle}  ({bundle.manifest.frame_count} frames, "
          f"source={bundle.manifest.source})")
    print(f"voxel: {args.voxel * 1000:.0f} mm   device: {args.device}")

    t0 = time.time()
    meshes = tsdf.fuse(
        bundle,
        track_ids=args.tracks,
        voxel_size=args.voxel,
        device=args.device,
        conf_min=args.conf_min,
        motion_filter=args.motion_filter,
        progress=True,
    )
    dt = time.time() - t0
    print(f"fused {len(meshes)} object(s) in {dt:.1f}s")

    written = tsdf.export_meshes(meshes, args.out, classes=classes, fmt=args.fmt)
    print(f"\nwrote {len(written)} mesh(es) to {args.out}:")
    for tid, mesh in meshes.items():
        v = np.asarray(mesh.vertices)
        label = classes.get(tid, "obj")
        if len(v) == 0:
            print(f"  track {tid:3d} {label:14s}  EMPTY (no geometry fused)")
            continue
        ext = v.max(axis=0) - v.min(axis=0)
        path = written.get(tid)
        print(f"  track {tid:3d} {label:14s}  {len(v):6d} verts "
              f"{len(mesh.triangles):6d} tris  "
              f"bbox {ext[0]:.2f}x{ext[1]:.2f}x{ext[2]:.2f} m  -> {path.name if path else '-'}")


if __name__ == "__main__":
    main()
