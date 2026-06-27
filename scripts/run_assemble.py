#!/usr/bin/env python
"""End-to-end Phase 9: gate a bundle, TSDF-fuse the tsdf-routed objects, and
assemble scene.json + per-object meshes/hulls (Step 5 -> Step 4B -> Steps 8-10).

Generative-routed objects are skipped (no GPU yet). Runs the gate in the proper
Z-D order (gate first, TSDF only for survivors).

Example:
  PYTHONPATH=src python scripts/run_assemble.py \
    --bundle ~/projects/vid2sim/data/replica/bundles/office_3 --tier 4 \
    --out out/scene_office_3
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from perception.bundle import PerceptionBundle
from reconstruction import confidence as cf
from reconstruction import observed_cloud, tsdf
from scene import assembler, lookup


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--tier", type=int, default=4, choices=[2, 3, 4])
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    b = PerceptionBundle.open(args.bundle)
    poses = b.read_poses()
    K = b.intrinsics.matrix()
    params = cf.tier_params(args.tier)
    voxel = params["voxel_size_m"]

    classes = {}
    for fid in b.iter_frame_ids():
        for d in b.read_objects(fid):
            classes.setdefault(int(d["track_id"]), d.get("class", "obj"))

    # --- Step 5 gate (on the cheap observed cloud), then TSDF only for survivors
    tsdf_objs = []
    for tid in tsdf._all_track_ids(b):
        fids, frames = tsdf._object_frames(b, tid, poses)
        cloud, keep = observed_cloud.accumulate_object_cloud(
            frames, K, voxel_size=voxel, return_keep=True)
        if len(cloud) < 4:
            continue
        cams = np.array([poses[fids[i]][:3, 3] for i in keep])
        res = cf.gate_object(cloud, cams, args.tier)
        flag = res["strategy"]
        print(f"  {classes.get(tid,'obj'):13} #{tid:<3} angle={res['angular_coverage_deg']} "
              f"compl={res['completeness_ratio']} -> {flag}")
        if flag == "tsdf":
            tsdf_objs.append((tid, cloud))

    if not tsdf_objs:
        print("no tsdf-routed objects at this tier — nothing to assemble "
              "(everything needs the generative path).")
        return

    print(f"\nfusing {len(tsdf_objs)} tsdf object(s)...")
    inputs = []
    tids = [t for t, _ in tsdf_objs]
    meshes = tsdf.fuse(b, track_ids=tids, voxel_size=voxel)
    for tid, cloud in tsdf_objs:
        m = meshes[tid]
        if len(m.vertices) == 0:
            continue
        inputs.append(assembler.ObjectInput(
            track_id=tid, coco_class=classes.get(tid, "obj"), mesh=m, cloud=cloud))

    cfg = lookup.load_config()
    tier_coacd = cfg["tiers"][args.tier].get("coacd", {"threshold": 0.05, "max_parts": 16})
    scene = assembler.assemble(inputs, poses, args.out, tier_coacd=tier_coacd)

    print(f"\nscene.json written -> {args.out}/scene.json   ground.y={scene['ground']['y']:.3f}")
    for o in scene["objects"]:
        t = o["transform"]["translation"]
        print(f"  {o['id']:16} mass={o['physics']['mass_kg']:7.2f} kg  "
              f"mat={o['material_class']:8} hulls={len(o['collider']['hull_paths'])}  "
              f"pos=({t[0]:.2f},{t[1]:.2f},{t[2]:.2f})")


if __name__ == "__main__":
    main()
