#!/usr/bin/env python
"""End-to-end Phase 9: gate a bundle, TSDF-fuse the tsdf-routed objects, and
assemble scene.json + per-object meshes/hulls (Step 5 -> Step 4B -> Steps 8-10).

Generative-routed objects are skipped (no GPU yet). Runs the gate in the proper
Z-D order (gate first, TSDF only for survivors).

Observability (src/telemetry): every stage is timed, every gate decision is
logged as one `gate` event, and `<out>/run_metrics.json` is written at the end
of the run — also when it fails or stops early (--gate-only). Set
SOBA_LOG_JSON=1 for JSON-lines logs.

Example:
  PYTHONPATH=src python scripts/run_assemble.py \
    --bundle ~/soba/data/replica/bundles/office_3 --tier 4 \
    --out out/scene_office_3
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np

import telemetry
from perception.bundle import PerceptionBundle
from reconstruction import confidence as cf
from reconstruction import gate_cache, generative, observed_cloud, runpod_policy, tsdf
from scene import assembler, lookup
from telemetry import stage_timer

log = logging.getLogger("run_assemble")

RUN_METRICS_NAME = "run_metrics.json"


def _crop_path(bundle, track_id: int):
    """Per-object crop image for the completion/generative engine, or None.

    Stages the best-frame crop on demand (Z-B-crop): the generative band feeds
    this single image to TripoSG. Returns None when the object is never visible
    enough to crop — the engine then drops it. The LocalEngine ignores the crop;
    only the GPU/RunPod paths use it.
    """
    from perception import crop_stage
    return crop_stage.ensure_crop(bundle, track_id)


def run_eval_hook(bundle: Path, out: Path, *,
                  gt_root: Path | None = None,
                  traj_root: Path | None = None,
                  evaluator: Path | None = None) -> bool:
    """Post-assembly ground-truth evaluation (writes <out>/eval.json).

    Strictly NON-FATAL: any missing precondition (unrecognised room name, no
    GT semantic assets downloaded yet, no scene.json) or evaluator failure
    logs ONE line and returns False — a build must never fail because the
    ground truth isn't there. Runs scripts/evaluate_scene.py in a subprocess
    so evaluator crashes cannot take the build down with them.
    """
    import re
    import subprocess
    import sys as _sys

    try:
        data_root = Path.home() / "soba/data/replica"
        gt_root = gt_root or data_root / "scenes"
        traj_root = traj_root or data_root / "gt_traj"
        evaluator = evaluator or Path(__file__).resolve().parent / "evaluate_scene.py"

        m = re.search(r"(office_\d+|room_\d+)", Path(bundle).name)
        if not m:
            log.info("eval: skipped (bundle '%s' is not a recognised Replica room)",
                     Path(bundle).name)
            return False
        room = m.group(1)
        if not (Path(out) / "scene.json").is_file():
            log.info("eval: skipped (no scene.json was written)")
            return False
        gt_scene = gt_root / room
        has_gt = gt_scene.is_dir() and any(gt_scene.rglob("mesh_semantic.ply"))
        if not has_gt:
            log.info("eval: skipped (no GT semantics under %s)", gt_scene)
            return False

        cmd = [_sys.executable, str(evaluator),
               "--scene", str(out), "--room", room, "--bundle", str(bundle),
               "--gt-scene", str(gt_scene)]
        traj = traj_root / f"{room}_traj_w_c.txt"
        if traj.is_file():
            cmd += ["--gt-traj", str(traj)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip().splitlines()
            log.warning("eval: FAILED (non-fatal): %s", tail[-1] if tail else "no output")
            return False
        for line in r.stdout.strip().splitlines():
            log.info("eval: %s", line)
        return True
    except Exception as exc:  # never let evaluation break a build
        log.warning("eval: FAILED (non-fatal): %s", exc)
        return False


def _run(args, metrics: telemetry.RunMetrics) -> None:
    """The pipeline proper. `metrics` is the run's accumulator (also
    telemetry.current()); main() writes it out whatever happens here."""
    with stage_timer("bundle_open", bundle=str(args.bundle)):
        b = PerceptionBundle.open(args.bundle)
        poses = b.read_poses()
        K = b.intrinsics.matrix()
        classes = {}
        for fid in b.iter_frame_ids():
            for d in b.read_objects(fid):
                classes.setdefault(int(d["track_id"]), d.get("class", "obj"))

    with stage_timer("tier_params", tier=args.tier):
        params = cf.tier_params(args.tier)
        # Tier 1 has no TSDF/gate block: no voxel size is configured, so the cloud
        # accumulation (still needed to size/place the generated meshes) uses the
        # standard 5 mm gate-cloud voxel; every object routes to "generative".
        tier1 = not params["tsdf"]
        voxel = params["voxel_size_m"] or 0.005

    # The completion/generative backend: LocalEngine (Poisson fill, no
    # regeneration) unless RUNPOD_API_KEY + RUNPOD_ENDPOINT_ID are set. The tier
    # selects the default generative model (T1-2 TripoSG, T3-4 Hunyuan3D, fix K1).
    with stage_timer("engine_init"):
        engine = generative.make_engine(tier=args.tier)
    gen_model = getattr(engine, "gen_model", None)
    log.info("engine: %s%s", type(engine).__name__,
             f"  gen_model={gen_model}" if gen_model else "")

    # --- Step 5 gate (on the cheap observed cloud), THREE-WAY routing.
    # Accumulation + metrics are cached per (bundle, track, gate params) under
    # <bundle>/.gate_cache/ — a hit skips the per-frame accumulation entirely.
    use_cache = not args.no_gate_cache
    with stage_timer("gate_cache_key"):
        ckey = gate_cache.params_key(
            frame_count=b.manifest.frame_count, gate_stride=args.gate_stride,
            voxel_size=voxel, motion_filter=False, tier_params=params)
    routed = {}  # tid -> (strategy, cloud)
    for tid in (args.tracks or tsdf._all_track_ids(b)):
        cls = classes.get(tid, "obj")
        hit = gate_cache.load(args.bundle, tid, ckey) if use_cache else None
        if hit is not None:
            cloud, cams, res = hit
        else:
            with stage_timer("observed_cloud", track_id=tid):
                fids, frames = tsdf._object_frames(b, tid, poses)
                if args.gate_stride > 1:
                    fids, frames = fids[::args.gate_stride], frames[::args.gate_stride]
                cloud, keep = observed_cloud.accumulate_object_cloud(
                    frames, K, voxel_size=voxel, return_keep=True)
                cams = np.array([poses[fids[i]][:3, 3] for i in keep]).reshape(-1, 3)
            with stage_timer("gate", track_id=tid, points=len(cloud)):
                res = (cf.gate_object(cloud, cams, args.tier)
                       if not tier1 and len(cloud) >= 4 else None)
                if use_cache:
                    gate_cache.store(args.bundle, tid, ckey,
                                     cloud=cloud, cams=cams, metrics=res)
        if tier1:  # no gate: everything generative (still needs a usable cloud)
            if len(cloud) < 4:
                metrics.record_drop("cloud_too_small", track_id=tid, points=len(cloud))
                continue
            strat = args.force_strategy or cf.GENERATIVE
            # gate_object is never called at tier 1 -> record the unscored decision
            metrics.record_gate(tid, cls, args.tier, None, routed=strat,
                                cached=hit is not None)
            log.info("  %-13s #%-3d (tier 1: no gate) -> %s", cls, tid, strat)
            routed[tid] = (strat, cloud)
            continue
        if res is None:  # cloud too small to gate (cached too, so reruns skip fast)
            metrics.record_drop("cloud_too_small", track_id=tid, points=len(cloud))
            continue
        strat = args.force_strategy or res["strategy"]
        # one gate event per object: exactly what gate_object returned (never
        # recomputed), plus the strategy actually applied and the cache state
        metrics.record_gate(tid, cls, args.tier, res, routed=strat,
                            cached=hit is not None)
        forced = "  (forced)" if args.force_strategy else ""
        log.info("  %-13s #%-3d angle=%s compl=%s -> %s%s", cls, tid,
                 res["angular_coverage_deg"], res["completeness_ratio"], strat, forced)
        routed[tid] = (strat, cloud)

    fusable = {tid: cl for tid, (s, cl) in routed.items() if s in cf.FUSABLE}
    gen_tids = [tid for tid, (s, _) in routed.items() if s == cf.GENERATIVE]
    n_tsdf = sum(1 for s, _ in routed.values() if s == cf.TSDF)
    n_compl = sum(1 for s, _ in routed.values() if s == cf.COMPLETION)
    log.info("routing: %d keep(tsdf), %d completion, %d generative",
             n_tsdf, n_compl, len(gen_tids))
    if args.gate_only:
        return

    inputs = []

    # Bottom band: regenerate from the crop. Accepted generations are CACHED in
    # <bundle>/.gen_cache keyed on (track, model, seed, crop hash) so a rebuild
    # reuses them (assembly-only reruns take ~2 min, and a good couch is never
    # re-gambled); rejects are cached as markers for the same reason. --reroll
    # gives listed tracks a fresh per-track seed, which changes their cache key.
    import hashlib
    import json as _json
    import os as _os

    import open3d as _o3d

    gen_cache = args.bundle / ".gen_cache"
    gen_cache.mkdir(exist_ok=True)
    base_seed = int(_os.environ.get("SOBA_TRIPOSG_SEED",
                                    _os.environ.get("SOBA_HUNYUAN_SEED", "42")))
    n_dropped = 0
    with stage_timer("generation", n_objects=len(gen_tids)):
        for tid in gen_tids:
            _strat, cloud = routed[tid]
            crop = _crop_path(b, tid)
            seed = base_seed + 1000 + tid if tid in args.reroll else base_seed
            chash = (hashlib.md5(crop.read_bytes()).hexdigest()[:10]
                     if crop is not None else "nocrop")
            key = gen_cache / f"gen_{tid:03d}_{gen_model}_{seed}_{chash}"
            ply, meta = key.with_suffix(".ply"), key.with_suffix(".json")

            if meta.exists():
                m = _json.loads(meta.read_text())
                if m.get("rejected"):
                    n_dropped += 1
                    metrics.record_drop("generation_rejected_cached", track_id=tid)
                    continue
                mesh = _o3d.io.read_triangle_mesh(str(ply))
                mesh.compute_vertex_normals()
                r = generative.RegenResult(mesh=mesh,
                                           alignment_method=m["alignment_method"],
                                           scale_method=m["scale_method"])
                log.info("  #%d generation reused from cache (%s)", tid, ply.name)
            else:
                for var in ("SOBA_TRIPOSG_SEED", "SOBA_HUNYUAN_SEED"):
                    _os.environ[var] = str(seed)
                with stage_timer("generate_object", track_id=tid,
                                 cls=classes.get(tid, "obj"), seed=seed):
                    try:
                        r = engine.regenerate(cloud=cloud, crop_path=crop,
                                              coco_class=classes.get(tid, "obj"))
                    except generative.RunPodConfigError:
                        raise  # misconfiguration is loud, never a per-object drop
                    except generative.RunPodError as exc:
                        # One endpoint failure (retries exhausted, FAILED, bad
                        # reply) drops THIS object and the run goes on. Not
                        # cached as a rejection: the next run may succeed.
                        log.warning("  #%d regeneration failed on RunPod: %s", tid, exc)
                        n_dropped += 1
                        metrics.record_drop(runpod_policy.DROP_FAILED, track_id=tid,
                                            error=str(exc)[:200])
                        continue
                if r is None:
                    why = getattr(engine, "last_decline", None)
                    n_dropped += 1
                    if str(why or "").startswith("unavailable"):
                        # The model did not run (missing deps/weights, OOM): that
                        # is NOT a verdict on the object, so it is not cached as a
                        # rejection and it gets its own drop reason. A tier-2 scene
                        # must not silently lose its whole generative band.
                        metrics.record_drop("generation_unavailable", track_id=tid,
                                            engine=type(engine).__name__, why=why)
                        if _os.environ.get("SOBA_GENERATION_STRICT") == "1":
                            raise RuntimeError(
                                f"generation unavailable for track {tid} ({why}) with "
                                "SOBA_GENERATION_STRICT=1: the image-to-3D model did not run")
                        continue
                    meta.write_text(_json.dumps({"rejected": True}))
                    metrics.record_drop("engine_declined", track_id=tid,
                                        engine=type(engine).__name__, why=why)
                    continue
                _o3d.io.write_triangle_mesh(str(ply), r.mesh)
                meta.write_text(_json.dumps({
                    "rejected": False, "alignment_method": r.alignment_method,
                    "scale_method": r.scale_method}))
            inputs.append(assembler.ObjectInput(
                track_id=tid, coco_class=classes.get(tid, "obj"), mesh=r.mesh,
                cloud=cloud, strategy="generative", crop_path=crop,
                alignment_method=r.alignment_method, scale_method=r.scale_method))
    if n_dropped:
        log.info("  %d generative object(s) dropped — %s declined to regenerate "
                 "(no engine, model not installed/failed, or no crop; e.g. hunyuan3d "
                 "needs ~10 GB VRAM — SOBA_GEN_MODEL=triposg fits an 8 GB card)",
                 n_dropped, type(engine).__name__)

    # tsdf + completion bands: fuse, then assemble (completion gets gap-filled).
    if fusable:
        log.info("fusing %d tsdf/completion object(s)...", len(fusable))
        with stage_timer("tsdf_fuse", n_objects=len(fusable), voxel_size=voxel):
            meshes, vbgs = tsdf.fuse(b, track_ids=list(fusable), voxel_size=voxel,
                                     return_grids=True)
        for tid, cloud in fusable.items():
            m = meshes[tid]
            if len(m.vertices) == 0:
                metrics.record_drop("empty_tsdf_mesh", track_id=tid)
                continue
            # Hand the live VBG to "completion" objects so the assembler runs
            # Option-A fusion (keep observed geometry, graft the unobserved part).
            inputs.append(assembler.ObjectInput(
                track_id=tid, coco_class=classes.get(tid, "obj"), mesh=m,
                cloud=cloud, strategy=routed[tid][0], crop_path=_crop_path(b, tid),
                vbg=vbgs.get(tid), voxel_size=voxel))

    if not inputs:
        metrics.record_drop("nothing_to_assemble")
        log.info("nothing to assemble (everything routed to the deferred generative "
                 "path, and no GPU engine is configured).")
        return

    cfg = lookup.load_config()
    tier_cfg = cfg["tiers"][args.tier]
    tier_coacd = tier_cfg.get("coacd", {"threshold": 0.05, "max_parts": 16})
    with stage_timer("assemble", n_objects=len(inputs)):
        scene = assembler.assemble(inputs, poses, args.out, tier_coacd=tier_coacd,
                                   engine=engine, smooth_iters=args.smooth_iters,
                                   collider=tier_cfg.get("collider", "hulls"))

    log.info("scene.json written -> %s/scene.json   ground.y=%.3f",
             args.out, scene["ground"]["y"])
    for o in scene["objects"]:
        t = o["transform"]["translation"]
        col = o["collider"]
        col_desc = (f"hulls={len(col['hull_paths'])}" if col["shape"] == "hulls"
                    else f"box={col['half_extents']}")
        log.info("  %-16s mass=%7.2f kg  mat=%-8s %s  pos=(%.2f,%.2f,%.2f)",
                 o["id"], o["physics"]["mass_kg"], o["material_class"], col_desc,
                 t[0], t[1], t[2])

    if not args.no_eval:
        with stage_timer("eval"):
            run_eval_hook(args.bundle, args.out)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--tier", type=int, default=4, choices=[1, 2, 3, 4])
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--force-strategy", choices=["tsdf", "completion", "generative"],
                    default=None,
                    help="Override the gate: route EVERY object to this strategy "
                         "(e.g. 'completion' to fuse+fill all objects from their "
                         "real TSDF instead of regenerating). For A/B comparison.")
    ap.add_argument("--smooth-iters", type=int, default=assembler.RENDER_SMOOTH_ITERS,
                    help="Taubin iterations on the RENDER mesh only (0 = off, the "
                         "A/B baseline; collider/mass geometry is never smoothed)")
    ap.add_argument("--tracks", type=int, nargs="*", default=None,
                    help="only process these track ids (fast iteration on a "
                         "subset, e.g. just the chairs)")
    ap.add_argument("--gate-stride", type=int, default=1,
                    help="score the Step-5 gate on every Nth frame only. Angular "
                         "coverage/completeness change slowly with viewpoint, so "
                         "a dense (stride-1) bundle gates ~N x faster with ~the "
                         "same routing; TSDF fusion still integrates EVERY frame.")
    ap.add_argument("--no-gate-cache", action="store_true",
                    help="recompute the Step-5 gate from scratch, ignoring (and "
                         "not writing) <bundle>/.gate_cache/. The cache is keyed "
                         "on the gate params + frame_count, so it normally "
                         "invalidates itself; this flag is the manual override.")
    ap.add_argument("--gate-only", action="store_true",
                    help="stop after the Step-5 routing printout (fast gate "
                         "iteration / cache warm-up; no fusion or assembly)")
    ap.add_argument("--no-eval", action="store_true",
                    help="skip the post-assembly ground-truth evaluation hook "
                         "(it self-skips anyway when no GT assets exist for "
                         "the bundle's room)")
    ap.add_argument("--reroll", type=int, nargs="*", default=[],
                    help="track ids whose generation gets a FRESH seed (base + "
                         "1000 + tid) — re-roll a visibly wrong generation "
                         "without touching the good ones (their cached "
                         "generations are reused)")
    return ap


def main(argv: list[str] | None = None) -> None:
    # One root handler for the whole run (text, or JSON lines with
    # SOBA_LOG_JSON=1). INFO so generation-rejection reasons (debris /
    # shattered / implausible dims, logged by reconstruction.generative) are
    # surfaced — else an object silently vanishes from the scene.
    telemetry.configure_logging()
    args = build_parser().parse_args(argv)

    metrics = telemetry.start_run(
        bundle=str(args.bundle), out=str(args.out), tier=args.tier,
        force_strategy=args.force_strategy, gate_only=args.gate_only)
    # billable seconds per RunPod submission -> run_metrics.json `remote`,
    # priced with the config/pipeline.yaml `runpod.price` assumption
    generative.remote_call_hook = (
        lambda endpoint, seconds: metrics.record_remote_call(
            f"runpod/{endpoint}", seconds,
            est_usd=runpod_policy.estimate_usd(seconds, endpoint)))
    try:
        with stage_timer("run", metrics=metrics):
            _run(args, metrics)
    except BaseException as exc:
        metrics.set_status("failed", f"{type(exc).__name__}: {exc}")
        log.error("run failed: %s: %s", type(exc).__name__, exc)
        raise
    else:
        metrics.set_status("ok")
    finally:
        try:
            metrics.write(Path(args.out) / RUN_METRICS_NAME)
        except Exception as exc:  # never mask the run's own outcome
            log.error("could not write %s: %s", RUN_METRICS_NAME, exc)


if __name__ == "__main__":
    main()
