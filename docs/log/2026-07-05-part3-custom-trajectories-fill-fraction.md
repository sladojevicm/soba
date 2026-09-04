_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## ⬆️ EARLIER (2026-07-05, part 3): custom trajectories DONE (8 rooms), fill_fraction physics fix, full GT downloaded
241 tests pass. EVERYTHING UNCOMMITTED (offer the user a commit early). Disk 93% — biggest reclaim
is SDFusion 17 GB (unused by pipeline; user approval still pending).
- **Custom camera trajectories COMPLETE for all 8 rooms** (`scripts/render_replica.py`,
  CPU raycast renderer from Replica semantic meshes; frame-match gate vs original
  bundles passes at 0.0 mm median depth error). Two hard-won fixes IN the script:
  (1) `_apply_budget` — uniform subsample on EVERY plan() return path so
  `--frames 200` yields exactly 200 (planner floors otherwise blow it up 8-16x;
  filled the disk once); (2) `Renderer.inside()` — ray up must hit ceiling AND ray
  down must hit floor, enforced at all 4 acceptance points (clearance alone is
  LARGE outside the room → cameras escaped through windows/doorways; user caught it).
  Outputs: `bundles/<room>_v2/` (200 fr, GT poses, GT-derived objects.json — pipeline-ready)
  and inspection videos `previews/200_frames/<room>.mp4` (all 8). `previews/2000_frames/`
  has long office_0/office_4 versions; the OTHER SIX 2000-frame renders run ONLY on
  explicit user command (his instruction). room_1 is walkthrough-only (no orbit-size furniture).
- **Replica GT fully on disk**: `data/replica/scenes/<room>/habitat/mesh_semantic.ply`
  + semantic.json for ALL 8 rooms (the "killed" download had finished detached).
  Room-accuracy eval vs GT is the LAST missing benchmark column: `scripts/evaluate_scene.py`
  + `config/replica_eval_class_map.yaml` + frontend GT panel + `/eval.json` route +
  run_assemble hook (`--no-eval` opt-out) are all built+tested but NEVER RUN — no
  eval.json exists anywhere yet. (Check whether the server empty-scene schema fix
  from BENCHMARK's finding actually landed in src/server.py — the agent doing it was killed.)
- **Physics fill_fraction fix IMPLEMENTED in production** (vlm.py Physics.fill_fraction,
  vlm_claude schema+prompt+parse, mass.mass_kg(fill_fraction=), assembler passes it;
  tier 1/lookup byte-identical). Measured via agent-preview (306 objects, YCB+ABO,
  results in BENCHMARK.md): furniture within-2x 29%→43%, catastrophic (>10x) errors
  9→1 (YCB) / 57→33 (ABO); residual = surface-vs-bulk density (full containers, foam).
  ⚠️ Claude columns are AGENT-PREVIEW (Claude Code opus agents on production inputs);
  canonical rerun = `scripts/benchmark_physics.py --dataset ycb|abo` once the user
  provides ANTHROPIC_API_KEY (he doesn't have Console billing yet — don't push).
- **TUM pose benchmark COMPLETE per tier** (BENCHMARK.md §1): tier-1 odometry
  reproduced exactly (2.15/4.74/26.4 cm), tiers 2-4 MASt3R measured on GPU
  (1.85/8.78/7.56 cm, scale 1.0008-1.075, 24-anchor cap). ORB-SLAM3 DROPPED
  PERMANENTLY (user decision, evidence-backed): POSE_METHODS[4]="mast3r",
  OrbSlam3Estimator deleted, config+tests updated. Never propose re-adding it.
- **Tier-2 t2b rebuilds (new 90/0.35 gates)**: office_1 (:8031, 2 obj), office_2
  (:8032, 8 obj) done+served; office_3_t2b is a KILLED PARTIAL (delete before rebuild);
  office_4 not started. NOTE: the _v2 custom-trajectory bundles may supersede these —
  ask the user whether future builds should use <room>_v2 (200 fr, ideal coverage)
  instead of bundles_dense (2000 fr, bad coverage).
- **Open queue (user-driven)**: (1) user inspects the 8 videos; (2) on his command:
  six 2000-frame renders into previews/2000_frames/; (3) run pipeline on _v2 bundles
  and/or backfill eval.json for existing scenes → "Section 3: room accuracy" in
  BENCHMARK.md + website panels; (4) canonical Claude physics run when API key exists;
  (5) commit everything; (6) paper (ERK, ~/Downloads/erkLaTeX) consumes BENCHMARK.md.
