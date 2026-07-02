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
from reconstruction import generative, observed_cloud, tsdf
from scene import assembler, lookup


def _crop_path(bundle, track_id: int):
    """Per-object crop image for the completion/generative engine, or None.

    Stages the best-frame crop on demand (Z-B-crop): the generative band feeds
    this single image to TripoSG. Returns None when the object is never visible
    enough to crop — the engine then drops it. The LocalEngine ignores the crop;
    only the GPU/RunPod paths use it.
    """
    from perception import crop_stage
    return crop_stage.ensure_crop(bundle, track_id)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--tier", type=int, default=4, choices=[2, 3, 4])
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--force-strategy", choices=["tsdf", "completion", "generative"],
                    default=None,
                    help="Override the gate: route EVERY object to this strategy "
                         "(e.g. 'completion' to fuse+fill all objects from their "
                         "real TSDF instead of regenerating). For A/B comparison.")
    ap.add_argument("--smooth-iters", type=int, default=assembler.RENDER_SMOOTH_ITERS,
                    help="Taubin iterations on the RENDER mesh only (0 = off, the "
                         "A/B baseline; collider/mass geometry is never smoothed)")
    ap.add_argument("--gate-stride", type=int, default=1,
                    help="score the Step-5 gate on every Nth frame only. Angular "
                         "coverage/completeness change slowly with viewpoint, so "
                         "a dense (stride-1) bundle gates ~N x faster with ~the "
                         "same routing; TSDF fusion still integrates EVERY frame.")
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

    # The completion/generative backend: LocalEngine (Poisson fill, no
    # regeneration) unless RUNPOD_API_KEY + RUNPOD_ENDPOINT_ID are set. The tier
    # selects the default generative model (T1-2 TripoSG, T3-4 Hunyuan3D, fix K1).
    engine = generative.make_engine(tier=args.tier)
    gen_model = getattr(engine, "gen_model", None)
    print(f"engine: {type(engine).__name__}"
          + (f"  gen_model={gen_model}" if gen_model else ""))

    # --- Step 5 gate (on the cheap observed cloud), THREE-WAY routing
    routed = {}  # tid -> (strategy, cloud)
    for tid in tsdf._all_track_ids(b):
        fids, frames = tsdf._object_frames(b, tid, poses)
        if args.gate_stride > 1:
            fids, frames = fids[::args.gate_stride], frames[::args.gate_stride]
        cloud, keep = observed_cloud.accumulate_object_cloud(
            frames, K, voxel_size=voxel, return_keep=True)
        if len(cloud) < 4:
            continue
        cams = np.array([poses[fids[i]][:3, 3] for i in keep])
        res = cf.gate_object(cloud, cams, args.tier)
        strat = args.force_strategy or res["strategy"]
        forced = "  (forced)" if args.force_strategy else ""
        print(f"  {classes.get(tid,'obj'):13} #{tid:<3} angle={res['angular_coverage_deg']} "
              f"compl={res['completeness_ratio']} -> {strat}{forced}")
        routed[tid] = (strat, cloud)

    fusable = {tid: cl for tid, (s, cl) in routed.items() if s in cf.FUSABLE}
    gen_tids = [tid for tid, (s, _) in routed.items() if s == cf.GENERATIVE]
    n_tsdf = sum(1 for s, _ in routed.values() if s == cf.TSDF)
    n_compl = sum(1 for s, _ in routed.values() if s == cf.COMPLETION)
    print(f"\nrouting: {n_tsdf} keep(tsdf), {n_compl} completion, "
          f"{len(gen_tids)} generative")

    inputs = []

    # Bottom band: regenerate from the crop. The LocalEngine declines (no GPU) so
    # these are DROPPED for now; a RunPodEngine returns a regenerated+aligned mesh.
    n_dropped = 0
    for tid in gen_tids:
        _strat, cloud = routed[tid]
        r = engine.regenerate(cloud=cloud, crop_path=_crop_path(b, tid),
                              coco_class=classes.get(tid, "obj"))
        if r is None:
            n_dropped += 1
            continue
        inputs.append(assembler.ObjectInput(
            track_id=tid, coco_class=classes.get(tid, "obj"), mesh=r.mesh,
            cloud=cloud, strategy="generative",
            alignment_method=r.alignment_method, scale_method=r.scale_method))
    if n_dropped:
        print(f"  {n_dropped} generative object(s) dropped — "
              f"{type(engine).__name__} declined to regenerate (no engine, model "
              f"not installed/failed, or no crop; e.g. hunyuan3d needs ~10 GB "
              f"VRAM — VID2SIM_GEN_MODEL=triposg fits an 8 GB card)")

    # tsdf + completion bands: fuse, then assemble (completion gets gap-filled).
    if fusable:
        print(f"fusing {len(fusable)} tsdf/completion object(s)...")
        meshes, vbgs = tsdf.fuse(b, track_ids=list(fusable), voxel_size=voxel,
                                 return_grids=True)
        for tid, cloud in fusable.items():
            m = meshes[tid]
            if len(m.vertices) == 0:
                continue
            # Hand the live VBG to "completion" objects so the assembler runs
            # Option-A fusion (keep observed geometry, graft the unobserved part).
            inputs.append(assembler.ObjectInput(
                track_id=tid, coco_class=classes.get(tid, "obj"), mesh=m,
                cloud=cloud, strategy=routed[tid][0], crop_path=_crop_path(b, tid),
                vbg=vbgs.get(tid), voxel_size=voxel))

    if not inputs:
        print("nothing to assemble (everything routed to the deferred generative "
              "path, and no GPU engine is configured).")
        return

    cfg = lookup.load_config()
    tier_coacd = cfg["tiers"][args.tier].get("coacd", {"threshold": 0.05, "max_parts": 16})
    scene = assembler.assemble(inputs, poses, args.out, tier_coacd=tier_coacd,
                               engine=engine, smooth_iters=args.smooth_iters)

    print(f"\nscene.json written -> {args.out}/scene.json   ground.y={scene['ground']['y']:.3f}")
    for o in scene["objects"]:
        t = o["transform"]["translation"]
        print(f"  {o['id']:16} mass={o['physics']['mass_kg']:7.2f} kg  "
              f"mat={o['material_class']:8} hulls={len(o['collider']['hull_paths'])}  "
              f"pos=({t[0]:.2f},{t[1]:.2f},{t[2]:.2f})")


if __name__ == "__main__":
    main()
