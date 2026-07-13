# Soba — Build Status & Handoff

_Renamed from "vid2sim-v2" to "Soba" on 2026-07-13 (branding + code identifiers,
e.g. `VID2SIM_*` env vars → `SOBA_*`; see the rename commit). The log entries
below predate the rename and still say "vid2sim-v2" / `VID2SIM_*` — left as
written since they're a historical record of what was actually run, not
retroactively edited. Substitute `soba` / `SOBA_*` for any command you copy
from below into a post-rename checkout._

_Last updated: 2026-07-02. This is a working handoff so a fresh session can resume
without re-deriving everything. The authoritative design is `PLAN_FINAL_FINAL.txt`
(currently at `~/projects/vid2sim/PLAN_FINAL_FINAL.txt`, version 14)._

## ⬆️ LATEST (2026-07-06): TIER-1 + TIER-2 ROOM ACCURACY DONE (all 8 rooms) — BENCHMARK §3 written
241 tests pass. This commit bundles all prior uncommitted work + the room-accuracy runs.
- **Tier 1 AND tier 2 built for all 8 `_v2` rooms**, GT-evaluated (`scripts/evaluate_scene.py`),
  browser-verified (`scripts/verify_browser.js`, all pass), and served. Ports: tier 1 :8001–8008,
  tier 2 :8011–8018 (room_1 is empty in both — walkthrough corridor, only uncroppable books).
- **BENCHMARK.md §3 "Replica room accuracy"** now written (between `<!-- SECTION3 -->` markers):
  one table per room, a row per tier. Regenerate with `scratchpad/write_section3.py` (reads each
  `out/scene_<room>_t<tier>/eval.json`). Pose is EXCLUDED from the score for `_v2` (they consume
  exact GT poses → ATE 0 by construction; passing the ORIGINAL dataset traj gives a bogus ~115 cm,
  so run evaluate_scene with `--gt-traj /nonexistent` to force pose n/a and renormalise the score
  to 0.444·recall + 0.222·precision + 0.333·F@5cm).
- **Result: tier 2 beat tier 1 in every non-empty room.** Mean score 63.0 → 75.2 (+12.2), mean
  F@5cm 0.41 → 0.68. Precision 1.00 everywhere (drop-garbage shipped zero hallucinations, both tiers).
  Driver of the gain: on the `_v2` orbit trajectories most objects route to COMPLETION (real geometry),
  NOT generative — the opposite of the old room-scan bundles. This is the coverage-gated-tier story.
- **Local tier build recipe (4060):** `scratchpad/tier_build.sh <room> <tier> <port>` needs
  `VID2SIM_TRIPOSG_HOME=~/projects/vid2sim/TripoSG VID2SIM_TRIPOSG_FLASH=0` (diso/nvcc not built →
  marching cubes) + `VID2SIM_PATCHCOMPLETE_HOME=~/projects/vid2sim/PatchComplete`. Never run two GPU
  builds at once. `scratchpad/tier{1,2}_all.sh` are the sequential drivers.
- **Disk reclaimed:** deleted SDFusion (17 GB) + PoinTr (1.3 GB) + caches → root 93% → 74% (25 GB free).
- **Open:** tiers 3–4 need the pod (Hunyuan3D, stopped); commit; then paper (ERK) consumes BENCHMARK §3.

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

## ⬆️ EARLIER (2026-07-05, part 2): gates 90/0.35, ORB-SLAM3 dropped, MASt3R benchmarked, BENCHMARK.md
238 tests pass. Working tree carries in-progress agent work (eval/renderer/frontend) — see below.
- **Routing thresholds LOWERED to 90°/0.35 (all tiers)** — user: real geometry
  first, image-to-3D last resort. test_confidence updated. Tier-2 rebuilds with
  the new gate: office_1_t2b (:8031, 2 obj — no routing change, coverage 26–42°),
  office_2_t2b (:8032, 8 obj); office_3/4_t2b NOT built (office_3_t2b dir is a
  KILLED PARTIAL — delete before rebuilding). Old sites untouched (:8001-:8024).
- **ORB-SLAM3 permanently dropped (user decision)**: POSE_METHODS[4]="mast3r",
  OrbSlam3Estimator deleted, config tier-4 pose_method=mast3r, tests locked.
  Justified by measurement (below): MASt3R 7.6 cm on the hardest TUM sequence.
- **BENCHMARK.md (repo root, committed base from user's WSL machine + new GPU
  section)**: TUM pose per tier now COMPLETE — tier-1 odometry (2.15/4.74/26.4 cm,
  reproduced here exactly) vs tiers 2–4 MASt3R (1.85/8.78/7.56 cm; scale
  1.0008–1.075; 24-anchor cap). MASt3R 3.5× better on fast motion, loses only on
  oscillating fr1/xyz (interpolation between anchors — RPE column shows it).
  Runner: scripts/bench_tum_pose.py + src/reconstruction/traj_eval.py (tested).
  YCB/ABO physics = lookup backend measured (WSL); **Claude column still missing**.
- **Uncommitted agent work in tree** (from stopped agents, all tests green):
  evaluate_scene.py + replica_eval_class_map.yaml + eval hook in run_assemble
  (--no-eval; quiet-skips w/o GT), frontend GT panel, server /eval.json route +
  empty-scene schema fix pending, render_replica.py (custom trajectories; user
  wants --frames default cut to 200), fetch_replica_gt_traj.py (GT trajs in
  data/replica/gt_traj). Replica semantic-mesh download INCOMPLETE (room_0/1/2
  only; offices never arrived). TUM data: f1xyz/f1desk/f2xyz bundles + poses per
  tier cached in data/tum (fr2 raw frames deleted, groundtruth.txt kept).
- Disk ~97% full — biggest reclaim: SDFusion 17 GB (unused; user approval pending).

## ⬆️ EARLIER (2026-07-05): tier-1+2 across rooms 1-4, gen cache, de-overlap, drop policy
Ports: room3 t1 :8001 / t2 :8002 / t3(stale) :8003; rooms office_1/2/4 t1
:8011/:8012/:8014, t2 :8021/:8022/:8024. Walkthrough mp4s (what the camera saw):
~/projects/vid2sim/data/replica/previews/. All builds headless-verified.
- Dense bundles now exist for office_1/2/4 (+office_3). Gen cache
  (<bundle>/.gen_cache) + --reroll make per-object regeneration ~3 min and
  rebuilds assembly-only; tier 2 reused tier 1's generations byte-identical.
- Drop-garbage policy live (user-directed): thin-shell + width gates; class
  gates recalibrated for COCO umbrella classes (coffee tables ARE
  "dining table"); alpha-mask crops stop generators re-segmenting (the
  white-tabletop eraser bug); sizing retry before drop; placement de-overlap.
- Known limit demonstrated by office_1 (2 objects) and office_4 (panel/ring
  "chairs"): distant/edge-on glimpses generate confident junk that passes
  ARITHMETIC gates. Agreed next candidates, NOT built: input-side minimum
  revealed-structure bar (drop before generating) and output-side CLIP
  class-resemblance gate; watertight gate parked on user request.

## ⬆️ LATEST SESSION (2026-07-04): tier 1 wired, Steps 3/7/8 REAL, textures, drop-garbage
All committed on `fix/phase3-pose-and-eval`. 210 tests pass. Room-3 (office_3)
four-tier build + websites :8001-:8004 (serve.py per tier).
- **Tier 1 runnable** (`--tier 1`): no gate, everything generative, BOX colliders
  (AABB half_extents, no CoACD) — assembler `collider="box"` path, frontend already had it.
- **Crop quality overhaul** (`crop_stage`): occlusion-aware best-frame scoring
  (depth-based cover detection), soft world-height factor (top-down views generate
  slabs), Telea INPAINTING of occluder pixels (cover = hull pixels NOT clearly
  behind the local surface; genuine see-through openings stay), tiny mask scraps
  dropped. Fixed the holey-table + floating-fragment artifacts the user flagged.
- **Step 8 LIVE (`scene/vlm_claude.py`)**: batched Claude physics call
  (output_config.format), annotated crops w/ metric ruler, refusal/max_tokens
  guards, clamps. Activates on ANTHROPIC_API_KEY; lookup fallback otherwise.
- **Phase 8 (`reconstruction/icp_align.py`)**: FPFH rotation-first + per-axis
  metric scale (<30%-coverage axes dropped) + point-to-plane refine + quality
  gates (incl. Z-H support-height). Wired into both regenerate() paths; on
  room-scan slivers the gates correctly refuse -> class-prior coarse fallback
  (honest provenance fpfh_icp/coarse_aligned).
- **Step 3 MASt3R REAL (`slam.py`)**: metric ckpt, swin pairs, dust3r global
  alignment, metric-scale solve vs sensor (M1), SE(3) interpolation. TUM fr1/xyz
  ATE 4.55 cm (24-anchor cap on 8 GB; odometry baseline 3.66 cm). Tier 4 falls
  back to MASt3R with a log (ORB-SLAM3 stays Phase-14-only-if-needed).
- **Hunyuan TEXTURE stage works on the pod** (VID2SIM_HUNYUAN_PAINT=1): paint ->
  bake UV texture to VERTEX COLORS -> flows through the whole pipeline; browser
  shows a purple fabric couch. Pod fixes recorded in setup_hunyuan3d.sh
  (python3-dev, trust_remote_code, xatlas/pytorch_lightning/realesrgan, bpy stub,
  cfg paths pinned absolute). **CRITICAL pod fix: model caches moved to
  /workspace/cache with symlinks from /root/.cache — container disk was 91% full
  and weights now SURVIVE pod stops.** Paint remesh leaves seams unwelded ->
  weld in _paint_hunyuan (else _looks_shattered falsely rejects ~600-component
  meshes — cost tier 3 its couch+tables once).
- **DROP-GARBAGE POLICY (user directive: "don't create magic")**: _too_thin
  (enc/hull < 3% = bent-sheet 'furniture'; real worst-case chair 4.1%) and
  _class_dims_ok now checks WIDTH (panel 'chairs' out). Expect fewer, better
  objects. Bottles: #94 never croppable, #92 blob rejected by dims — honest drops.
- **Scenes**: t1/t2 local TripoSG; t3/t4 pod Hunyuan+paint (11 objects each,
  couch+main table+bottle back, textured). Pod: slow_tomato_gull now at
  root@213.192.2.110 -p 40066 (STOP IT when done — bills hourly); handler via
  /workspace/start_handler.sh + local tunnel `ssh -N -L 8777:127.0.0.1:8777`;
  engine env: RUNPOD_API_KEY=dummy RUNPOD_GEN_ENDPOINT_ID=dummy
  VID2SIM_RUNPOD_URL=http://127.0.0.1:8777/runsync VID2SIM_RUNPOD_TIMEOUT=1200.
- **GOTCHA: do NOT run two GPU builds concurrently on the 4060** — T2 TripoSG
  died to OOM against T3's local PatchComplete; rerun solo.
- **Open**: user reviews the 4 room-3 sites, then the other 7 rooms (need dense
  re-streams); de-overlap pass (objects interpenetrate in the desk cluster);
  TUM end-to-end; CLI (Phase 12); serverless deploy; capture.py.

## ⬆️ LATEST SESSION (2026-07-02, part 6): viewer debts (framing, headless verification)
Two viewer debts paid, one commit each; JS only (src/ untouched), 184 tests pass.
- **Camera frames the WHOLE ROOM** (`frontend/app.js`): the old running-centroid
  target made users think the scene was missing. Now the view fits the bbox of
  ALL loaded meshes (35°-elevation diagonal, bounding-sphere fit vs. the
  narrower FOV axis, 15% margin). `camera_pose` still wins the INITIAL position
  (W5) — then only the OrbitControls target is aimed at the bbox centre;
  without a camera_pose the view fully auto-fits. Re-frames as objects stream
  in over SSE ONLY until the first user interaction (controls `start` /
  canvas pointerdown); **`f`** re-frames on demand any time.
- **Headless verification harness** (`scripts/verify_browser.js`, run recipe in
  `frontend/README.md`): spawns serve.py on a free port, drives headless Chrome
  (puppeteer via `NODE_PATH` — install it OUTSIDE the repo; swiftshader GL).
  Asserts: zero page errors; object count == scene.json; every LIVE Rapier body
  mass == `physics.mass_kg` (regression guard for the founding desc-mass fix);
  bodies load FIXED and wake on a REAL click (`__vid2sim.screenPos` + mouse);
  no NaN / |p|>50 m after ~3 s of sim; `f`-framing sets `framedAll`; screenshot.
  Exit 0 iff all pass. `window.__vid2sim` debug handle in app.js is additive
  (live getters into Rapier), unused by the viewer itself.
  `NODE_PATH=<pptr>/node_modules node scripts/verify_browser.js --scene out/scene_chairs`
- **Verified**: `out/scene_chairs` (6/6) and `out/scene_office_3_hy4` (11/11)
  — all 8 checks PASS on both; masses match within float32 (e.g. dining_table_02
  71.8074, bottle_08 0.1072); max |p| after click+3 s ≈ 4.1 m; screenshots show
  every object in frame. Only real defect found: `/favicon.ico` 404 console
  error on every load → inline `data:,` favicon in `index.html`.
- **Open (ranked)**: TUM real-sensor end-to-end; MASt3R; Phase-12 CLI.

## ⬆️ EARLIER (2026-07-02, part 5): quality debts (masses, crop scoring, gate cache)
Three ranked debts from part 4 paid, one commit each; 184 tests pass. CPU-only
(pod stopped) — verified against the on-disk scenes, no regeneration.
- **Generated masses fixed** (`mass.generative_volume`, config `generative_mass`):
  the hull rule (1dde0fa) predated the cleanup's watertight guarantee; a table's
  hull fills the air under the top. Now: ENCLOSED signed-tet volume when sane,
  clamped into a hull-ratio band [0.15, 0.35] (generation style swings enc/hull
  3%–63% on identical furniture; real furniture is a ~constant hull fraction),
  hull only as non-watertight fallback. Generative band ONLY; tsdf/completion
  untouched. Recomputed from on-disk glbs (scene.jsons refreshed in place):
  dining tables 205.8→71.8 kg / 101.7→15.2 kg, full-size chairs 28-55→4.5-8.1 kg,
  couch 82→28.7 kg, small chairs 5.4/19.4→0.8/2.9 kg (genuinely small objects).
- **Crop best-frame now quality-aware** (`crop_stage`): world extent stays the
  primary signal; among the top ~20% band the sharpest / best-exposed frame wins
  (Laplacian variance over the mask × dark-luminance damping). Sharp sliver
  still loses; no-poses mask-area fallback keeps the same two-stage rule.
- **Step-5 gate cache** (`reconstruction/gate_cache.py`): per (bundle, track,
  params) npz under `<bundle>/.gate_cache/` holding cloud+cams+metrics; params
  (tier bars, voxel, stride, motion filter, frame_count) are hashed into the
  filename so any change auto-invalidates. run_assemble gained `--no-gate-cache`
  and `--gate-only`. Proof (small office_3, tier 4, stride 4): cold 212.9 s →
  warm 5.3 s (**40x**), routing printout byte-identical; cache 57 MB / 14 objects.
- **Open (ranked)**: TUM real-sensor end-to-end; MASt3R; Phase-12 CLI.

## ⬆️ EARLIER (2026-07-02, part 4): generation-artifact defenses, user-tuned gate
Iterated with the user LOOKING AT the browser scenes; pod now STOPPED.
- **Gate re-tuned per user preference (image-to-3D FIRST):** complete bar
  RAISED to T2 112/0.48, T3 105/0.45, T4 100/0.42 — completion is reserved
  for truly well-observed objects (only chair#25 qualifies on office_3);
  everything else generates. (An earlier same-day widening was my misread of
  the user's intent — reverted.)
- **Hunyuan artifact defenses in `generative.py`** (each found by LOOKING at
  real output, all test-pinned, 175 pass):
  1. **Display-mat cut** (plane-RANSAC, orientation-free): Hunyuan reads the
     object-on-white crop as a product shot and adds a base mat; the uniform
     class-prior scale then shrinks the object to a miniature on a platform.
     >50% of samples on one plane + rest footprint <50% of it -> cut the mat
     (tables/couches never match). Runs BEFORE scaling.
  2. **Detached-fragment drop by CONNECTIVITY, not size** — a 17%-area armrest
     floating 65 cm away passed a <15% size rule; now ANY component >3% extent
     from the dominant component's SURFACE (raycast distance — vertex distance
     lies on sparse meshes) is dropped; touching parts of any size stay (legs).
  3. **Broken-generation rejection**: >30% detached (_clean_gen), no dominant
     component (_looks_shattered), or implausible class height (_class_dims_ok,
     e.g. a 0.24 m slab "chair") -> the OBJECT IS DROPPED, not shipped.
     VID2SIM_GEN_STRICT=0 / VID2SIM_GEN_CLEAN=0 disable.
- **`run_assemble --tracks`** = fast subset iteration (chairs-only rebuild
  ~13 min at gate-stride 20). Scenes: `out/scene_chairs` (6 single-component
  full-size chairs; 3 broken generations rejected), `out/scene_tables`,
  `out/scene_office_3_hy4` (full room). Serve any with scripts/serve.py.
- **Not-a-bug**: "things on the table tops" = REAL tabletop items faithfully
  generated from the crop (Replica's GT table mask includes them; the TSDF
  has the same bumps). Accepted by the user... pending.
- **Open (ranked)**: generated masses (solid-volume overshoot: 206 kg table —
  solidity recalibration for generated meshes); crop quality for weak views;
  gate caching; TUM real-sensor end-to-end; MASt3R; Phase-12 CLI.

## ⬆️ EARLIER (2026-07-02, part 3): RUNPOD POD LIVE — Hunyuan3D generative band REAL
User provided a RunPod pod (RTX 3090 24 GB, `slow_tomato_gull`, SSH
`root@213.192.2.110 -p 40028`; **REMIND USER TO STOP IT when done — bills
hourly**). First-ever live run of the plan's cloud-GPU generative band:
- **Hunyuan3D 2.1 installed on the pod** via `deploy/runpod/setup_hunyuan3d.sh`
  (first real execution; it works — one missing dep `timm` found+added). Weights
  (14 GB) in `/root/.cache` (NOT /workspace → gone if pod is REPLACED; script
  restores in ~5 min). Pod pip needs `PIP_BREAK_SYSTEM_PACKAGES=1` (PEP 668).
  Code shipped by `git archive HEAD | ssh ... tar -x` into /workspace/vid2sim-v2.
- **The serverless handler runs on the pod as a plain HTTP server**
  (`python3 generative_handler.py --rp_serve_api --rp_api_port 8777`, log
  /workspace/handler.log) and the local pipeline drives it through an SSH
  tunnel via the new **`VID2SIM_RUNPOD_URL`** override — the REAL
  RunPodEngine transport (`_build_input`/`_decode_mesh`) validated live:
  watertight chair mesh back on first post-timm attempt. No serverless
  deployment needed for a pod; for real serverless later just set
  RUNPOD_API_KEY + RUNPOD_GEN_ENDPOINT_ID and unset VID2SIM_RUNPOD_URL.
- **`SplitEngine` added** (make_engine composes it: RunPod gen endpoint set,
  no completion endpoint, local CUDA present) → generative band on the pod,
  completion band stays local PatchComplete instead of degrading to Poisson.
- **RunPodEngine contract fix:** regenerate() now DECLINES (None) on a missing
  crop instead of raising — a raise killed a full 35-min tier-4 assembly at the
  first uncroppable object (chair#9). Test pins it. 166 tests pass.
- **Scenes from the dense (2000-frame) bundle, tier 4, gate-stride 10:**
  - `out/scene_office_3_dense` — local: 3 fused (PatchComplete+fusion+seal) +
    9 TripoSG, 12 objects (12-cap), **server smoke-tested: schema-valid, 12/12
    meshes+hulls fetchable**. Generative masses still rough (125 kg table).
  - `out/scene_office_3_hy` — same but generative band = **Hunyuan3D 2.1 on
    the pod** (~40 min wall; 9 generated + 3 fused = 12 objects, 1 uncroppable
    chair declined gracefully). Schema-valid, 12/12 meshes served. Masses far
    saner than TripoSG's (chairs 2-20 kg vs 0.4-39 kg; tables still ~100 kg
    high → solidity recalibration is future work). Serve either scene with
    `scripts/serve.py --scene out/scene_office_3_hy`.
- Gate scores on dense data ≈ identical to 100-frame (chair#25 120.8°/0.758 vs
  121°/0.74) — coverage really is trajectory-bound; `--gate-stride` validated.

## ⬆️ EARLIER (2026-07-02, part 2): dense rebuild AUDITED, YOLO+SAM2 REAL
All committed + pushed on `fix/phase3-pose-and-eval` (through `1ae544a`).
- **Dense office_3 bundle built**: ALL 2000 frames (stride 1) at
  `~/projects/vid2sim/data/replica/bundles_dense/office_3` (1.2 GB). The old
  100-frame bundle stays at `bundles/office_3`. Disk was 96% full — deleted the
  superseded room_0 artifacts (demo zip + extracted/ + bundle_room0, ~9 GB
  reclaimed, STATUS said deletable; `bundles/room_0` is the replacement).
- **Data-loss audit (scripts/audit_data_loss.py) ANSWERED the June-28 questions:**
  (1) the depth gate [400,8000]mm loses ZERO pixels on Replica — exonerated;
  (2) the Open3D default weight threshold (~3 obs/voxel) cost 2-10% of TSDF
  vertices on 100 frames (table worst = the thin legs) and the dense rebuild
  FIXES it: w3/w1 goes to ~1.00 on all four audited objects (couch/table/
  chair25/chair9), table +14% vertices; (3) density buys OBSERVATIONS per
  voxel, not coverage — unique 5mm cells only +14-39%, dims unchanged, the
  ≤123° room-scan ceiling stands. JSON: out/audit_dense{,_tsdf}.json.
- **YOLO detection EXISTS now (`src/perception/detect.py`)**: ultralytics
  YOLO-seg (yolo11s-seg) + the plan's Step-1 IoU tracker (class-gated greedy
  match >0.4, retire after 5) as a `detector` callable for
  `TUMReader.to_bundle`. Injectable infer seam, 7 unit tests, RGB→BGR flip is
  load-bearing (ultralytics assumes BGR numpy input).
- **Real SAM2 RAN for the first time** (Phase-4 validation): fr1/xyz, 16 stable
  tracks (keyboard/tv/book/chair/cup/mouse) refined over 100 frames, ~1.5 fps
  on the 4060 (4.4 GB VRAM), crops staged. Two latent bugs found+fixed by the
  real run: Sam2VideoPredictor needs **bf16 autocast** (dtype crash without),
  and refine_masks gained a `track_ids` filter (YOLO 1-frame flicker tracks —
  32 of 48! — would each cost a SAM2 video pass). Driver script:
  `scripts/run_tum_detect.py` (weights: ~/projects/vid2sim/models/
  sam2.1_hiera_large.pt; yolo auto-downloads). Bundle:
  `data/tum/bundle_f1xyz_yolo`.
- **run_assemble gained `--gate-stride N`** (gate scores every Nth frame;
  TSDF still fuses all) — the dense bundle's gate is otherwise ~20x the
  100-frame cost (it re-reads depth per object). Default 1 = old behaviour.
- **Dense reassembly**: `out/scene_office_3_dense` (tier 2, gate-stride 10) —
  see the scene section / next-session note for the result.
- **Next**: TUM pipeline continuation (poses on bundle_f1xyz_yolo → cloud →
  gate → assemble = first REAL-SENSOR end-to-end scene); gate caching; CLI
  (Phase 12); icp_align.py (Phase 8).

## ⬆️ EARLIER (2026-07-02, part 1): full-project sanity check, red test FIXED
- **Test suite: 156 pass, 0 fail** (was 154+1 red). The red
  `tests/perception/test_crop_stage.py` was the TEST's fault, not the code:
  it drew a FULL-SQUARE mask then asserted the tight crop's corners are
  whitened background — but a square's bbox corners are INSIDE the mask.
  Fixed the fixture to a diamond mask (corners really are background,
  JPEG-tolerant ≥240 check) and ADDED a test for the uncommitted `_best_frame`
  rework (with depth+poses it picks the frame with the largest back-projected
  WORLD extent — the revealing view — not the biggest mask; falls back to mask
  area without poses).
- **Uncommitted WIP reviewed (coherent, tests green, still UNCOMMITTED):**
  Hunyuan3D generative band (tier-selected model T1-2 TripoSG / T3-4 Hunyuan3D,
  fix K1; `make_engine(tier=)`), `deploy/runpod/generative_handler.py`
  serverless worker (one endpoint, modes regenerate+complete, contract pinned
  by `test_generative_handler.py`), yaw-ICP + full FPFH registration in
  `coarse_align_to_cloud` (two pose candidates scored by cloud→mesh RMSD),
  crop-stage 3D-extent best-frame, frontend legend removal. Worth committing.
- **NOT DONE (still open from the 2026-06-28 top-priority list): the office_3
  bundle is STILL 100/2000 frames (stride 20)** — `scene_office_3_full` means
  "full room" (all 12 objects), NOT full frames. The stride-1 re-stream +
  per-object data-loss audit (weight threshold, voxel sizes) never ran.
- Pipeline gap list vs the plan re-derived this session — see "Pipeline
  progress" table + "Remaining build phases" below (unchanged conclusions:
  icp_align.py, real SAM2/YOLO, MASt3R/ORB-SLAM3, live VLM call, CLI/tiers,
  integration pass, capture.py all missing; gate caching + generative crop
  quality + mass sanity still open).

## ⬆️ EARLIER (2026-07-01): Options A+D shipped, TripoSG LOCAL, full room
Big session. All on branch `fix/phase3-pose-and-eval` (mine + collaborator commits,
latest `e9c6295`). Memory to read FIRST: `fusion-option-a`, `geometric-repair-option-d`,
`triposg-local-and-verify` (NEW), plus the older completion memories.

**BUILT + SHIPPED this session:**
- **Option D** (`src/scene/geometric_repair.py`): pymeshfix watertight collider for the
  dense band. Wired to the "tsdf" keep band.
- **Option A = FUSION** (`src/reconstruction/fusion.py`): keep real observed geometry,
  graft ONLY the unobserved part. Wired: `tsdf.fuse(return_grids=True)` → assembler
  `_fuse_and_seal` (fusion→D-seal) for the "completion" band. Key params (all validated):
  grid-based `keep_largest_interior` (o3d MESH cluster is pathologically slow — use
  ndimage.label on the GRID), adaptive `denoise_sigma` (noisy objects >150 interior blobs
  get global-smoothed → solid closed body), `pad` (marching-cubes closes the surface),
  per-class `max_fill_dist_m` (`assembler.FILL_DIST_BY_CLASS`: table 0.06 kills the
  under-table blob, couch/default 0.25 keeps it solid), `max_voxels` cap (couch@2mm OOMs).
- **PatchComplete = in-engine completion source** (`src/reconstruction/patchcomplete_completion.py`,
  loaded in-process; `make_engine()` default `completion_model=patchcomplete`).
- **TripoSG image-to-3D RUNS LOCALLY** on the 8 GB RTX 4060 (the generative band now
  REGENERATES poorly-observed objects instead of dropping them). Install at
  `~/projects/vid2sim/TripoSG` (weights present). See `triposg-local-and-verify` memory.
- **Render fixes** (all verified in a REAL browser via headless Chrome, NOT Open3D which
  is culling-blind): frontend `DoubleSide`; `exporter_gltf` decimate-BEFORE-smooth +
  discard watertight-breaking Taubin; `smooth_taubin` weld-only (collaborator); server
  `no-store` headers (browser was caching stale meshes → "it's the same every time").
- **Physics**: frontend objects start FIXED, turn dynamic on click (fixes the load
  EXPLOSION from overlapping class-prior-sized objects). Collaborator: drop-to-ground,
  class-prior sizing, generative mass by hull volume.

**SCENES + SERVERS (live now, may need restart):**
- `out/scene_office_3_full` — MIXED (tier 4): 3 fused (couch/table/chair) + 9 TripoSG. Served :8001.
- `out/scene_generated` — ALL 12 TripoSG (tier 2, `--force-strategy generative`). Served :8002.

**OPEN / NEXT:** (1) Poorly-observed generative objects are BAD (torn/blobby) — DATA limit
(bad crops from a center-of-room scan); options: filter them out, improve crops
(brighten/pick-sharpest), or accept. (2) Gate is SLOW + recomputed every run (uncache) —
cache it. (3) Generative masses still high (blobby volume). (4) Browser default camera
centers on ONE object → should auto-frame the room. (5) 1 red test
`tests/perception/test_crop_stage.py` = collaborator WIP, NOT ours.

## ⬆️ EARLIER (2026-06-30 eve): holey-couch FIXED; scene served
- **office_3 couch rendered holey/broken; root-caused + FIXED.** Cause: the couch's
  real TSDF is very noisy (6969 components vs chair's 254), it fills the fusion grid to
  the edge so marching cubes left OPEN boundaries (holes) pymeshfix couldn't seal, and
  PatchComplete's couch completion is garbage (1.83m tall, L-sectional is OOD). FIX in
  `fusion.fuse_completion`: `pad=4` (empty border → marching cubes closes the surface) +
  `keep_largest=True` (`largest_component`, drops noise blobs). Rebuilt office_3 →
  `out/scene_office_3_full` (couch/table/chair), VISUALLY VERIFIED via offscreen render
  (`scratchpad/render_check.py`, Open3D EGL): couch now a SOLID recognizable L-couch (no
  holes), chair still crisp. Seat stays a bit rough = honest noisy-scan, not a bug;
  global field-smoothing was tried but hung repeatedly (abandoned). Memory `fusion-option-a`.
- **SERVED:** `scripts/serve.py --scene out/scene_office_3_full` on :8000.
- **Render-verify trick:** GLB isn't o3d-readable → assembler dumps PLY with
  `VID2SIM_DUMP_PLY=1`; render one PLY/process (~20s EGL init).
- **⚠️ 1 PRE-EXISTING TEST RED (not mine):** `tests/perception/test_crop_stage.py` from a
  collaborator's TripoSG crop-staging commit (5760c16) — background-whiten mismatch, their
  WIP, generative path (inactive). My fusion+scene tests all green.

## ⬆️ EARLIER (2026-06-30 pm): Option D BUILT+WIRED, Option A (fusion) STARTED
- **Best-observed object across ALL 8 scenes = office_3 chair#25** (hull 0.754, ang 120.6°).
  Ranked from `out/gate_distribution.json`. **NOTHING exceeds 0.90** (room-scan ceiling;
  only 1 object >0.70; median 0.298).
- **Option D (geometric watertight repair) DONE.** `src/scene/geometric_repair.py`:
  `declutter` (drop TSDF fragments) → quadric-decimate → **pymeshfix** → watertight
  2-manifold collider + colour transfer. `watertight_collider(mesh, target_tris=80000)`.
  On chair#25: watertight=TRUE, 1 comp, fidelity 2.0mm (matches Poisson) BUT actually
  sealed (Poisson isn't — its crop reopens it). 40k too coarse for thin chair; 80k = sweet
  spot; ~76s. CGAL alpha-wrap unavailable (no py binding); pymeshfix is the stand-in.
  - **WIRED:** assembler `strategy=="tsdf"` → `_tsdf_watertight_finalize` → watertight_collider
    (Poisson fallback). `config/pipeline.yaml` `keep_completeness=0.85` all tiers.
  - **END-TO-END demo** (`scratchpad/d_pipeline_demo.py`, forces chair#25 to tsdf):
    `chair_00 mass=10.9kg hulls=16`, CoACD logged **Mesh Manifoldness: true** (the payoff).
    Scene `out/scene_d_demo`. 4 tests; **full suite 132 pass.**
  - **CAVEAT:** keep bar ANDs angular (150-160°, max real 123°) + 0.85>0.754 ceiling → never
    auto-fires on Replica; correct for future walk-around footage. User accepted.
  - **Browser comparison** `out/scene_d_repair/` (raw|pymeshfix|alpha|poisson side by side);
    serve `scripts/serve.py --scene out/scene_d_repair`. Memory: `geometric-repair-option-d`.
- **Option A = FUSION — BUILT + WIRED + VALIDATED.** `src/reconstruction/fusion.py`:
  `grid_from_vbg` (sparse VBG → dense T_real,W), `mesh_to_grid_sdf` (completion mesh →
  signed-dist on same grid via o3d RaycastingScene), `fuse_fields` (`observed?real:comp`,
  EDT blend over a 3-voxel seam), `grid_to_mesh` (skimage marching_cubes → world),
  `fuse_completion` (end-to-end). 5 tests; **full suite 137 pass.**
  - **Validated on chair#25:** fused mesh hugs OBSERVED points to **0.0mm** (Poisson alone
    1.9mm) → real geometry preserved exactly, only the unseen back grafted. ~24s, no GPU.
  - **Canonical combo proven:** real TSDF + **PatchComplete** completion → fusion → D-seal
    → watertight 1-component collider. PatchComplete pred reused from
    `PatchComplete/output_ours/.../chair25/input_0_pred.npz`, placed in world by inverting
    the `chair_to_patchcomplete.py` normalisation. Browser `out/scene_a_patchcomplete`
    (real|patchcomplete|fused|fused+D); also `out/scene_a_fusion` (real|poisson|fused).
    Scripts: `scratchpad/a_fusion_{probe,validate,patchcomplete}.py`.
  - **WIRED:** `tsdf.fuse(return_grids=True)` → `(meshes, {tid:VBG})`. `ObjectInput` gains
    `vbg`+`voxel_size`; `run_assemble.py` passes them for fusable objects. Assembler
    `_fuse_and_seal`: a "completion" object with a VBG KEEPS observed geometry + grafts the
    engine's completion only where unobserved, then D-seals (all WORLD coords, recentred at
    end). Backward-compatible (no VBG → old behaviour). This fires on the COMPLETION band,
    which IS reachable on real Replica (unlike D's keep band).
  - **KEY PROPERTY:** fused mesh inherits the real shell's openness (non-watertight) BY
    DESIGN — fusion preserves real geometry; D seals it. So the pipeline is fusion→D.
  - **PatchComplete-in-engine DONE:** `src/reconstruction/patchcomplete_completion.py`
    loads multi_res in-process (reproduces the CLI prediction BIT-FOR-BIT) →
    `LocalGpuEngine._run_completion` `patchcomplete` branch returns the mesh directly →
    `make_engine()` default completion_model = **patchcomplete**. Validated end-to-end:
    `engine.complete`→6970 tris/10.7s, assemble→fusion→D-seal→CoACD→`out/scene_a_engine`
    (chair_00, 16 hulls, 17.5kg). GOTCHA: model reads its codebook from the RELATIVE
    `priors/` dir → construct with cwd=PatchComplete repo (module handles it).
  - **NOTE:** full `run_assemble` on office_3 is SLOW in the GATE phase (per-object cloud
    accumulation; tier 4 @2mm timed out at 560s, tier 2 @4mm ~3-4min — PRE-EXISTING, not a
    fusion cost). The completion→fusion→seal stage itself is ~1-2 min/object.

## ⬆️ EARLIER SESSION (2026-06-30 am): full completion-model bake-off, ALL validated on own data
Tested **4 completion models** for filling partial furniture, each FIRST validated on its
OWN paper/demo data (with a reference output) BEFORE our objects — the discipline the user
insisted on. It caught real bugs and closed the ComPC question. Memory: `completion-verdict`,
`validate-and-run-completion-models`, `completion-tools-survey`, `compc-pod-setup`.
- **VERDICT — PatchComplete wins.** Real-ScanNet-trained, clean + CORRECT-SIZE output, instant
  (110ms), runs LOCALLY in the venv (pure torch, no env build). Couch reconstructed well;
  table → filled solid block not thin legs (32³ too coarse). Validated on its ShapeNet lamp+GT.
- **ComPC: env VALIDATED (not botched), but slow + coarse.** Rebuilt on a fresh RTX 6000 Ada pod;
  validated on ComPC's OWN redwood REAL-SCAN eval data + GT (sym chamfer ~0.055, shape matches).
  The "blob" is CORRECT ComPC behaviour — it outputs dense FILLED SOLIDS (right size, smoothed
  structure). ~20 min/object. This finally closes the user's "did we botch the setup?" question.
- **SDFusion: crisp on synthetic, BLOBS on our real scans** (domain gap). Validation caught a
  feeding bug (SDF must clamp to ±0.2). Runs locally in venv (pytorch3d stubbed).
- **PoinTr/AdaPoinTr: FAIL on real** (synthetic domain gap) — PROVEN: clean on PoinTr's own demo
  sofa, scatter/collapse on our real chair. Not a usage bug (feeding matches official inference).
- **BROWSER COMPARISON:** `out/scene_pointr_compare/` — grid (columns=model, rows=chair/couch/table),
  colors+labels+legend in `frontend/app.js`. Serve: `scripts/serve.py --scene out/scene_pointr_compare`.
  All scratchpad scripts in `scratchpad/` (+ `scratchpad/compc_fair/` artifacts).
- **HIGHEST-VALUE NEXT STEP (conceptual, NOT built): FUSION** — keep REAL observed geometry +
  graft ONLY the missing part. Fuse both as TSDF grids (`if observed→real else→completion`),
  using our `tsdf.py` per-voxel WEIGHTS as the free observed/unobserved mask; marching-cubes.
  Medium difficulty (~1-2 days), infra exists. Biggest win for the regenerate-everything models.
- **⚠️ STOP THE RTX 6000 Ada POD** from the RunPod UI (bills ~$0.5/hr; SSH can't). `/workspace`
  is a network volume → the ComPC env persists for next time.
- **NOTE:** PatchComplete/SDFusion need NO pod (local RTX 4060). Only ComPC needs the pod.
  Image-to-3D (TRELLIS/Hunyuan3D) is the user's chosen LAST RESORT — not yet tried.

## ⬆️ EARLIER SESSION (2026-06-29): ComPC tested on a real GPU — verdict
Rented a **RunPod RTX 4090 (24 GB)** and made **ComPC run end-to-end** (the friend's
`deploy/runpod/` kit + a new `setup_compc.sh` that actually builds it: gcc-10,
`--no-build-isolation`, `setuptools<70`, cv2/PATH fix — all committed & pushed).
The pipeline seam is in: `src/reconstruction/compc_completion.py` + a dispatch
branch in `LocalGpuEngine._run_completion`; activate with
`VID2SIM_COMPLETION_MODEL=compc`; runs ONLY for gate "completion"-band objects.
- **RESULT on the real office_3 couch:** ComPC produces a 16k-point **blob** — both
  on the dense 256k input (out-of-regime misuse) AND on a clean **sparse 8k** input
  (in-regime). It is **~40 min/object** (default pce_num=10000 ≈ 1-2 hr).
- **Honest caveat (do not over-conclude):** the in-regime blob was NOT a fair test —
  3 fixes untried: canonical **orientation** (ComPC's Zero123/SDS assumes a canonical
  pose; we fed arbitrary yaw), **unit-normalization** (fed raw 3.6 m metric points),
  and the **L-sectional couch is OOD** for ShapeNet (try a simple chair). Rule these
  out before declaring learned completion dead.
- **Direction that converged:** dense/well-observed → **geometric (Poisson)**, settled.
  Sparse → learned completion is the *intended* band but unproven for us. To fill the
  **unobserved pocket**: for vid2sim's PHYSICS goal use **symmetry-mirror + smooth
  free-space-bounded closure** (ME-PCN's emptiness idea, which our TSDF already has);
  learned hallucination is a visual-detail fallback only. See memory
  `completion-verdict` + `compc-pod-setup`.
- **Pod + cost:** pod bills ~$0.7/hr; **STOP it from the RunPod UI when done** (SSH can't).
  `/workspace` is wiped if the pod is *replaced* (happened once). Repo is private →
  SCP files to the pod, can't `git pull` anonymously.

## ⬆️ TOP PRIORITY FOR THE NEXT INSTANCE (user-directed 2026-06-28)
The user wants the FRONT of the pipeline (input → observed cloud → TSDF → gate)
audited and maximised BEFORE judging any completion model. Garbage-in is partly
on us: office_3 currently uses only **100 of the scene's 2000 frames (stride 20)**
— a disk/streaming-budget choice, NOT quality. Do this, in order:

1. **Rebuild office_3 with ALL 2000 frames (stride 1).** Re-stream via
   `scripts/build_replica_bundles.py --stride 1 --max_frames 2000` (only office_3),
   then reassemble. Denser footage → denser clouds → far more complete TSDF (thin
   legs survive the weight threshold). Est. **30–60 min** (~15–30 re-stream, 5–10
   denser TSDF/gate, ~6 CoACD). This is the single biggest quality lever.
2. **Per-object audit + data-loss check.** For EACH of the 14 objects: how many
   frames see it, point count at each stage (raw backproject → masked → voxel-
   downsampled → TSDF), and where points are lost. Suspects to check:
   - `observed_cloud`: depth gate `[400, 8000] mm`, voxel downsample `0.005 m`
     (the gate cloud) — is 5 mm too coarse? is 0.4 m near-clip dropping close points?
   - `tsdf.extract_triangle_mesh()` uses the Open3D **default weight threshold
     (~3 frames)** with NO explicit arg — drops voxels seen <3× (kills legs). Try
     lowering it; with 2000 frames more will clear it anyway.
3. **Audit the whole front-end so it WORKS end-to-end** on the dense rebuild:
   confirm each object's cloud dims are physically right (couch was validated at
   2.34×0.90×1.06 m on the 100-frame build — re-validate on 2000), no silent drops,
   gate metrics recomputed. NOTE for Replica the inputs are GT (masks/poses/depth)
   so there is NO segmentation/pose/sensor error — the only quality lever is
   frames + the weight threshold + voxel sizes. The hard limit is COVERAGE
   (≤123°/object, center-of-room scan) — no handling fixes the unseen back.
4. THEN re-test completion on the DENSE clouds (see completion section below).

## COMPLETION-MODEL STATE (where we are, what was wrong)
The user wants **point/mesh completion** (keep real geometry, fill gaps); image-to-3D
is the LAST resort. PoinTr/AdaPoinTr (PCN ckpt) gave **blobby** results — but the
PoinTr paper shows clean completions, so it was a **usage error**, root cause found:
- **BUG (fixed):** used the **PCN checkpoint** with **ShapeNet-55-style normalisation**
  (centroid+unit-radius). Verified in the repo: `ShapeNet55Dataset.pc_norm` normalises
  that way, `PCNDataset` does NOT. Added `pointr_sn55` (ShapeNet-55 model, matched
  convention, 55 categories) + fixed sampling (was `randint` → duplicates; now
  without-replacement). Checkpoints downloaded to `~/projects/vid2sim/PoinTr/pretrained/`.
- **DIAGNOSTIC RESULT (the key finding):** rendered the RAW completion point clouds
  (input vs PCN vs ShapeNet-55) for couch+table. **The input partials are DENSE and
  RECOGNISABLE (couch 2.36M pts, table 580k pts) — both completion models make them
  WORSE**, outputting only 8–16k SCATTERED points (their fixed output size). Point-
  completion nets are built to DENSIFY a sparse sliver, NOT to fill holes in an
  already-dense scan — so on our well-observed objects they throw away ~99% of the
  real resolution. The "blob" was that low-res scattered output + Poisson meshing it.
  **CONCLUSION:** for well-observed objects KEEP the real geometry and repair holes
  GEOMETRICALLY (Poisson / pymeshfix / alpha-wrap on the REAL mesh — pymeshfix was
  pip-installed, test was interrupted). Learned completion/generation belongs ONLY
  on the genuinely SPARSE objects (where 8–16k output is an upgrade). This VALIDATES
  the user's "keep real geometry" instinct but shows point-completion is the wrong
  mechanism for it. Diagnostic image: `scratchpad/diag.png` (regenerate via
  `scratchpad/diag.py` — needs the `.ptp()`→`np.ptp()` numpy-2 fix already applied).
- Infra is DONE + model-agnostic (`LocalGpuEngine`, `pointr_completion.py`): swapping
  the model is one config line. PCA yaw-align (`VID2SIM_PCA_ALIGN`) + colour transfer
  are in. Deep-research on SOTA completion was launched but FAILED at synthesis
  (partial agent transcripts in the workflow dir if useful).

## GPU / CLOUD (corrected facts)
- **GPU NOW WORKS**: RTX 4060 8 GB, torch 2.6+cu124, `make_engine()` auto-selects
  `LocalGpuEngine`. The driver was the blocker (user fixed it: dkms + reboot).
- **Project is CLOUD-GPU-first by design** (RunPodEngine is make_engine's 1st choice;
  config targets Hunyuan3D for top tiers). The 8 GB only limited LOCAL TESTING, never
  the design. Compute is NOT a constraint — target the best models on cloud.
- PoinTr's blob was MODEL/USAGE, not VRAM (it used 234 MB). Unlimited GPU unlocks the
  strong models, doesn't fix point-completion misuse.

## ⚠️ CURRENT OPERATING CONSTRAINT — generative path deferred (no GPU)
Decided 2026-06-27: the generative path (Step 6 / Phases 7–8, RunPod) is **on hold**
— no GPU yet (FRI/SLING/Vega or RunPod credits TBD; RunPod costs ~$20–30, faculty
GPU likely free). **CONSEQUENCE:** every object the Step-5 gate routes to
**"generative"** has NO mesh and is **DROPPED — not rendered, not simulated.** Only
**"tsdf"-routed (well-observed) objects** yield usable geometry locally. On the
recalibrated thresholds that is ~1–5 objects/scene on Replica; the rest are
**invisible until a GPU is connected.** Downstream Phases 9–11 (assembly/browser)
must treat generative objects as **pending/omitted, not an error**, and the scene
will be sparse (only the best-observed objects) until the generative stage exists.

## How to resume (read these in order)
1. This file (current state + next steps).
2. `~/projects/vid2sim/PLAN_FINAL_FINAL.txt` — the full design (v13). The
   15-phase Build Order is §24; the pipeline Steps overview is §5.
3. The user's auto-memory `vid2sim-v2-project.md` (locations + working style).
The user is **non-expert on the internals** — explain in plain language and be
**honest about what is validated on real data vs only synthetic/fake-input tested.**

## IMPORTANT: two numbering systems (don't confuse them)
- **Pipeline "Steps"** = the runtime data-flow order (§5). SAM2 is **Step 2**.
- **Build Order "Phases"** = the order you build the code (§24). SAM2 is **Phase 4**.
  So "Phase 4" and "Step 2 (SAM2)" are the SAME module. Phase 5 = the TSDF half of
  Step 4. When in doubt, talk in pipeline Steps.

## Where everything lives
- **Repo:** `~/projects/vid2sim/vid2sim-v2/` (git, remote `git@github.com:sladojevicm/vid2sim-v2.git`)
- **Plan:** `~/projects/vid2sim/PLAN_FINAL_FINAL.txt` (v12)
- **TUM data:** `~/projects/vid2sim/data/tum/rgbd_dataset_freiburg1_xyz/` (fr1/xyz + `groundtruth.txt`)
- **Replica data:** `~/projects/vid2sim/data/replica/`
  - **ALL 8 vMAP scenes built as bundles: `bundles/{room_0,room_1,room_2,office_0..4}/`**
    (100 frames each, stride 20; 445 MB total). Built by **streaming only the strided
    frames out of the remote 44.79 GB `vmap.zip`** via HTTP range requests +
    parallel block prefetch (`scripts/remote_zip.py` + `scripts/build_replica_bundles.py`)
    — NO full download (the 44.79 GB zip never fits the 39 GB free disk). ~5 min total.
  - `demo_replica_room_0.zip` (6 GB) + `extracted/room_0/` + `bundle_room0/` — the
    older single-room artefacts (superseded by `bundles/room_0`, which is identical:
    couch 2.34×0.90×1.06). Can be deleted to reclaim ~9 GB.
  - HF repo `kxic/vMAP` has only: `demo_replica_room_0.zip` (6.48 GB),
    `vMAP_Replica_Results.zip` (10.59 GB), `vmap.zip` (44.79 GB, all 8 scenes,
    layout `vmap/<scene>/imap/00/{depth,rgb,semantic_class,semantic_instance}/`).
- **venv:** `~/projects/vid2sim/venv/` (python 3.12, numpy 2.5, **open3d 0.19 CPU build**, opencv, pytest)

## Environment reality (matters for what's runnable here)
- **GPU PRESENT but NOT USABLE yet (corrected 2026-06-28):** the box has an
  **NVIDIA RTX 4060 Laptop (8 GB)** (`lspci` confirms; the earlier "no GPU" note
  was WRONG). BUT the kernel module isn't built for the running kernel
  (6.17.0-35): `dkms` isn't installed, `modinfo nvidia` → not found, no
  `/dev/nvidia*`, so `nvidia-smi` fails and `torch.cuda` is unavailable. **Fix is
  user-side (sudo + reboot):** install dkms + rebuild the `nvidia-driver-580-open`
  module, e.g. `sudo apt install dkms nvidia-dkms-580-open && sudo reboot` (or
  reinstall the driver), then `nvidia-smi` should show the 4060. Until then all
  GPU models fall back (completion → Poisson, generative → dropped).
- VRAM ceiling once live: 8 GB → PoinTr/SAM2/MASt3R fit; TripoSG tight (fp16);
  Hunyuan3D 2.1 likely won't fit (use TripoSG locally).
- **Not installed:** torch, sam2, ultralytics (needed to actually run SAM2 / YOLO).
- **open3d is the CPU wheel** but the tensor TSDF `VoxelBlockGrid` **works on `CPU:0`**
  (verified) — so Phase 5 TSDF is runnable here, just slow. room_0 is small enough.
- ~40 GB disk free.

## How to run
```bash
cd ~/projects/vid2sim/vid2sim-v2
PYTHONPATH=src ~/projects/vid2sim/venv/bin/python -m pytest -q      # 102 tests, all pass

# SEE the scene (Phase 10+11): local server + browser viewer, no GPU:
~/projects/vid2sim/venv/bin/python scripts/serve.py                # serves out/scene_office_3
#   then open http://127.0.0.1:8000/  (--scene DIR for another scene, --port N)
#   (serve.py adds src/ to the path itself — no PYTHONPATH needed. The
#    `PYTHONPATH=src python -m server` form also works but only as ONE shell line.)

# Build a Replica bundle from the rendered room_0 sequence (GT masks, no YOLO/SAM2):
PYTHONPATH=src ~/projects/vid2sim/venv/bin/python -c "
from perception.dataset_reader import ReplicaReader
ReplicaReader('$HOME/projects/vid2sim/data/replica/extracted/room_0/imap/00').to_bundle(
    '$HOME/projects/vid2sim/data/replica/bundle_room0', fps=30.0, stride=20, max_frames=100)"

# Phase-3 pose validation on real TUM vs ground truth (ATE 3.66 cm):
~/projects/vid2sim/venv/bin/python scripts/eval_pose.py \
  --bundle ~/projects/vid2sim/data/tum/bundle_f1xyz \
  --gt ~/projects/vid2sim/data/tum/rgbd_dataset_freiburg1_xyz/groundtruth.txt
```

## Pipeline progress (by Step — the data-flow view)
| Step | What | State |
|---|---|---|
| 1 Input | `bundle.py` + `dataset_reader.py` (TUM + **Replica**) | ✅ done, exercised on real data |
| 2 SAM2 masks | `sam2_refine.py` | ⚠️ built; real backend **never run** (fake-backend tested). Skipped on Replica (GT masks) |
| 3 Pose | `slam.py` (Tier-1 RGB-D odometry) | ✅ **validated on real TUM (ATE 3.66 cm)**. MASt3R/ORB-SLAM3 are stubs |
| 4A Observed cloud | `observed_cloud.py` | ✅ **validated on real Replica** (couch 2.34×0.90×1.06 m). **Z-T motion filter wired** (`motion_filter=`, default off; no-op on static room_0, drops jumped frames on synthetic moving objects) |
| 4B TSDF fusion | `tsdf.py` | ✅ **built + validated on real Replica room_0** (couch/chair/table meshes, dims match Step 4A). CPU:0. **Z-T keep-frame set shared from Part A** (single source of truth) |
| 5 Confidence gate | `confidence.py` | ✅ **built + RECALIBRATED + now THREE-WAY.** Angular + shape-fair **hull** completeness (Z-U). Two cutoffs on those metrics: `keep` bar → **tsdf** (keep as-is), `complete` bar → **completion** (fill the gaps), below → **generative**. Collapses to binary by setting keep==complete (or omitting keep_*). On room-scan data the keep bar is unreachable, so survivors route to **completion** |
| 6 Generative/completion engine | `reconstruction/generative.py` | ✅ **interface + LocalEngine built** (completion = Poisson; regenerate = None/deferred). **RunPodEngine skeleton** (transport wired; `_build_input`/`_decode_mesh` seams await the API). `make_engine()` env-switched |
| 7 ICP align | `icp_align.py` | ❌ not built |
| 7b Mesh finalise | `scene/exporter_gltf.py` + `mass.watertight_repair` | ✅ **decimation + .glb export + Poisson watertight repair built** (repair feeds mass volume; masses now realistic, see below) |
| 8 Physics (Claude) | `scene/vlm.py` | ⚠️ **interface + lookup fallback built** (`physics_origin:"lookup"`); live Claude `output_config.format` call deferred (needs claude-api skill + key) |
| 9 Convex decomp (CoACD) | `scene/decomp.py` | ✅ **built** (CoACD 1.0.11 installed; single-hull fallback) |
| 10 Scene assembly | `scene/assembler.py` (+ `lookup`/`mass`/`ground`) | ✅ **built + validated on office_3** → schema-valid `scene.json` |
| 10 Local server | `src/server.py` | ✅ **built + tested** (Starlette; routes+SSE; 8 tests) |
| 11 Browser (Three.js+Rapier) | `frontend/` | ✅ **built + rendered** — office_3 couch/table/chair visible, physics steps (headless-Chrome verified) |

**One-line summary:** the front of the pipeline works on real data — **Step 1 → Step 3
→ Step 4A → Step 4B (TSDF) → Step 5 (gate)** — i.e. we can take footage, produce per
object the real points seen + a fused mesh, and decide tsdf-vs-generative. Step 2
(SAM2) is built but unproven. Everything from Step 6 (generative/RunPod) on is unbuilt.

## What this session did (2026-06-28, THREE-WAY routing + pluggable engine)
Reworked the gate from BINARY (tsdf | generative) to **THREE-WAY** so the
generative GPU work drops in cleanly later, and built the pluggable engine seam.
- **Gate (`confidence.py`) is now three-way** via `route(ang, comp, ...)`: two
  cutoffs on the SAME two metrics. `keep` bar → **"tsdf"** (keep the TSDF mesh
  as-is), `complete` bar → **"completion"** (keep real geometry, FILL the unseen
  parts), below → **"generative"** (regenerate whole from the crop). **Collapses
  to the old binary gate** by setting keep==complete or omitting `keep_*` (the
  toggle the user asked for — config only, no code change). `tier_params`/`gate`/
  `gate_object` carry the keep bars; `FUSABLE=(tsdf,completion)`.
- **Pluggable engine (`reconstruction/generative.py`, NEW)** — one interface for
  the two bands that need invented geometry: `complete(mesh,cloud,crop,class)`
  (mid) and `regenerate(cloud,crop,class)->RegenResult` (bottom). **Decision
  2026-06-28: the mid band uses a LEARNED, geometry-conditioned shape-completion
  model (PoinTr-family, GPU)**, NOT image-gen — so there are now **two GPU models
  / two RunPod endpoints**: completion (`pointr`, fed the partial scan) and
  generation (`triposg`/`hunyuan3d`, fed the crop). **LocalEngine** (default, no
  GPU): complete = Poisson repair (the no-GPU fallback for the mid band);
  regenerate = None (bottom dropped). **RunPodEngine**: transport (POST
  /v2/{endpoint}/runsync, Bearer key) wired; per-band endpoints; the contract
  seams `_build_input`/`_decode_mesh` raise until the API is provided; an absent
  endpoint → that band falls back (completion→Poisson, generative→drop). `make_
  engine()` reads `RUNPOD_API_KEY` + `RUNPOD_GEN_ENDPOINT_ID` (alias
  `RUNPOD_ENDPOINT_ID`) and/or `RUNPOD_COMPLETION_ENDPOINT_ID`. **Awaiting the
  RunPod API.** `pipeline.yaml` tiers 2-4 carry `completion_model: pointr` +
  `generative_model`.
- **Local-GPU backend added (`LocalGpuEngine`)** so the models can run on THIS
  box's GPU with RunPod left blank. `make_engine()` priority: RunPod (if endpoints
  set) → **LocalGpuEngine (if a CUDA device is visible, toggle `VID2SIM_LOCAL_GPU`)**
  → LocalEngine. `complete()` = the shape-completion model, `regenerate()` = the
  image-to-3D model, then `coarse_align_to_cloud()` (REAL, CPU/Open3D — scales the
  unit-cube mesh to the observed AABB; replaces the old NotImplemented stub). The
  per-model load+inference (`_run_completion`/`_run_gen`) are honest seams that
  raise until the model packages+weights are installed; a raise is caught →
  graceful fallback (completion→Poisson, generative→drop), so a missing model
  never crashes the pipeline. **BLOCKED on the driver** (see Environment reality):
  CUDA is not visible, so right now `make_engine()` returns LocalEngine and
  office_3 runs in fallback (3 completion via Poisson, 11 dropped) — same scene as
  before. The GPU path goes live with NO code change once `nvidia-smi` works and
  the models are installed.
- **PLAN_FINAL_FINAL updated to v14 (Z-W/Z-X)** — the gate section rewritten from
  binary to three-way (the old "WHY BINARY" objection is *resolved*: each band
  emits ONE coherent mesh, no naive stitch), Step 6 split into completion-model +
  generation-model, overview/TOC/run-order/folder/removed-features/limitations
  all reconciled. Quadruple-checked: grep-swept for lingering "binary"/two-way
  contradictions; remaining "binary" mentions are all historical/explanatory.
- **Assembler is strategy-aware**: `ObjectInput.strategy` drives the mesh
  finalize (completion → `engine.complete`, Poisson fallback; tsdf/generative →
  light repair) and `source.*` (generative → real ICP provenance; tsdf/completion
  → geometry_source "tsdf" + n/a, per the frozen schema's enum + Y1 rule).
  `mass.closed_mesh_volume` measures an already-completed mesh without re-repair.
- **`run_assemble.py` routes three-way**, regenerates the generative band via the
  engine (dropped when Local), fuses tsdf+completion, prints the breakdown.
- **Config (`pipeline.yaml`)**: tiers 2-4 gained `keep_angular_deg`/
  `keep_completeness` (160/0.85, 155/0.82, 150/0.80) — PROVISIONAL, set near
  walk-around quality so room-scan survivors route to completion (the `complete`
  bar is unchanged: the recalibrated 110/0.45, 100/0.42, 90/0.38).
- **Verified:** 115 tests pass (+13: routing bands, collapse-to-binary toggle,
  engine defaults/seams). On **real office_3** the gate routes **3 → completion**
  (couch 110/0.40, table 94/0.46, chair 121/0.74), **11 → generative (dropped)**,
  **0 → tsdf** (nothing clears the keep bar — correct for room-scan). Synthetic
  completion object assembles to a schema-valid entry (geometry_source "tsdf",
  hulls + mass). The visible result is unchanged (completion via Poisson == the
  repair), but the architecture is now three-way and GPU-ready.
- **What the RunPod handover needs:** (1) endpoint id + API key (env vars);
  (2) the handler `input` JSON shape (fill `_build_input`); (3) the returned mesh
  format — prefer OBJ/PLY, Open3D can't read GLB back (fill `_decode_mesh`);
  (4) Phase-8 ICP for `regenerate`'s `_align_to_cloud` (scale/place the unit-cube
  mesh); (5) crop staging (`_crop_path` / Z-B-crop) so the engine gets images.

## What an earlier session did (2026-06-28, Phase 10 + 11 / server + browser — SEE the scene)
Built the **local server** and the **browser viewer**, then verified the whole
chain in a headless Chrome — **office_3's couch, dining table and chair render
in Three.js and step in Rapier physics**, no JS errors. This is the payoff: the
assembled scene is now visible and interactive, fully local, no GPU.
- **Phase 10 — `src/server.py`** (plan §15). Built on **Starlette**, not FastAPI:
  FastAPI itself wasn't installed but Starlette + uvicorn + sse-starlette + httpx
  + httpx-sse were — clearly the pre-staged toolchain (httpx-sse is an SSE *test*
  client). Same routes, fewer deps. Routes: `GET /scene.json` (partial-safe —
  empty-but-valid if no file), `GET /meshes/{id}.glb` → `objects/{id}/mesh.glb`,
  `GET /hulls/{id}_{i}.glb` → `objects/{id}/hulls/{id}_{i}.glb` (hull index split
  off the LAST `_N`), `GET /events` SSE, `/` + static `/app.js` + `/vendor/...`.
  Path-traversal guarded (id regex; 400/404 on junk). `create_app(scene_dir)`
  factory + `python -m server [--scene DIR] [--port N]`. Scene dir via
  `VID2SIM_SCENE_DIR` env or `--scene` (default `out/scene_office_3`).
  - **SSE is a REPLAY, not a live feed:** the scene is already fully assembled on
    disk (Phase 9 ran offline), so `/events` emits one `object_added` per object
    in the current scene.json (0.4 s apart), then heartbeats. It drives the
    browser through the real progressive-load + re-GET path (Z-C); it is not a
    live stream from a running assembler (there's nothing assembling live here).
- **Phase 11 — `frontend/`** (plan §16): `index.html` (import map, no bundler) +
  `app.js` + **vendored** `vendor/three` (0.160 ESM + GLTFLoader/OrbitControls/
  BufferGeometryUtils) and `vendor/rapier/rapier.es.js` (rapier3d-**compat** 0.13,
  WASM inlined as base64 → no separate fetch, no build step). All the §16
  load-bearing fixes are implemented and present in the code:
  - **mass on the rigid-body DESC before `createRigidBody`** (`setAdditionalMass`)
    — the P1/D5 fix (original project's computed-but-never-applied-mass bug).
  - **hull colliders = CoACD parts with density 0** so Rapier never re-derives
    mass; Tier-1 path also handled (`collider.shape:"box"` → `cuboid(hx,hy,hz)`).
  - **gravity + ground.y read FROM scene.json** (Z-J / K4); ground cuboid uses
    HALF-extents with centre.y = ground.y − 0.1 (Z6); up_axis asserted.
  - **re-GET /scene.json on each `object_added`**, look up by id (Z-C).
  - camera from `camera_pose` when present (W5); OrbitControls recenters on the
    object centroid so the start view always frames the scene.
  - interactions: click-select (orange emissive), drag-push (velocity to cursor),
    spacebar rubber ball (10 s TTL). Hull verts baked through `matrixWorld` so the
    collider matches the render mesh; mesh+hulls share the assembler's recentred
    object frame (verified: assembler recenters the mesh BEFORE decomposing).
- **Verification (honest):** 8 new server tests (route remaps, byte-equality vs
  on-disk, 404/400, traversal, partial-safe empty scene, **real-server SSE replay
  via a threaded uvicorn** — TestClient/ASGI both deadlock on an infinite SSE
  stream, so the test runs a real port). **102 tests pass** (was 94). Then drove
  the live page with **headless Chrome (Puppeteer + swiftshader, software WebGL)**:
  all 3 objects load, **zero page errors**, WebGL context active, physics stepping
  — screenshot shows the recognizable couch/table/chair on a shadowed floor. The
  software-WebGL "context lost→restored" warning is a swiftshader headless
  artefact, not a code bug. The meshes are the partial-view, non-watertight TSDF
  shells (open backs), exactly as upstream caveats say — they render fine.
- **Sparse-scene constraint holds:** office_3 shows **3 of 14** objects; the
  generative ones are omitted until a GPU exists. The HUD says so on screen.
- **Run:** `PYTHONPATH=src ~/projects/vid2sim/venv/bin/python -m server` then open
  `http://127.0.0.1:8000/`. Deps already in the venv; recorded as the `serve`
  extra in `pyproject.toml`.

## What an earlier session did (2026-06-27, Phase 9 / scene assembly)
Built **Phase 9 (Step 8-10): `scene/{lookup,mass,ground,decomp,exporter_gltf,vlm,
assembler}.py`** + `scripts/run_assemble.py`, validated end-to-end on **office_3**.
Runs the proper Z-D order: gate → TSDF only for survivors → assemble. Output is a
**schema-valid `scene.json`** + per-object `objects/{id}/mesh.glb` and CoACD
`hulls/{id}_{i}.glb`. office_3 @ Tier 4 → 3 tsdf objects (couch/table/chair),
slug ids (`couch_00`…), `source.geometry_source:"tsdf"` ⇒ `alignment/scale:"n/a"`,
lookup physics, ground-snapped placement. **94 tests pass** (9 new).
- **CoACD 1.0.11 installed** (real convex decomposition; single-hull fallback).
- **VLM deferred:** physics from the lookup table (`physics_origin:"lookup"`); the
  live Claude call (`output_config.format`) is an injectable backend, finalised
  later with the claude-api skill + key. No key needed to run.
- **Masses FIXED via Step 7b Poisson watertight repair** (`mass.watertight_repair`):
  open TSDF shells are closed by Poisson reconstruction (density-trimmed, cropped
  to the AABB), volume bounded by the convex hull, hull fallback on failure. Masses
  dropped from absurd (couch 219 kg) to believable: **couch 32.6 kg, table 45 kg,
  chair 3.6 kg** (vs real ~40/20–40/6). ⚠️ Still APPROXIMATE: the repaired mesh
  isn't perfectly watertight (the crop leaves small holes) and Poisson is mildly
  non-deterministic, so kg values can vary run-to-run by some margin. Good enough
  for plausible physics; a fully robust volume (trimesh hole-fill / deterministic
  Poisson, or solidity recalibration against known masses) is future hardening.
- **Sparse-scene caveat holds:** only tsdf objects assembled (3 of 14 in office_3);
  generative objects omitted (no GPU). Volume computed via signed-tetrahedron sum
  (robust; Open3D `get_volume()` rejects convex hulls as non-watertight — gotcha).

## What an earlier session did (2026-06-27, gate recalibration)
Investigated "0/75 → tsdf" and found it was PARTLY a real data property and PARTLY
a **miscalibrated, unreachable threshold**. Two findings, both fixed:
- **The completeness metric was broken.** Old metric = observed_area / **bbox** area,
  which (per the plan's own fix Z-U) tops out ~0.37 on real data — so the 0.55–0.65
  thresholds were UNREACHABLE by any object, ever. Replaced with the shape-fair
  **convex-hull** normaliser (`surface_completeness(denom="hull")`, the Z-U
  improvement path): observed_area / convex-hull area. Measured max rose 0.37→0.75.
- **Thresholds were never calibrated.** Recalibrated from the real 8-scene
  distributions (75 objects; `scripts/analyze_gate.py` dumps
  `out/gate_distribution.json`). New per-tier gates in `config/pipeline.yaml`:
  T2 110°/0.45, T3 100°/0.42, T4 90°/0.38 (~p90→p72 of the distribution).
- **Result (live gate verified == distribution):** T2 routes **1/75** to tsdf,
  T3 **2/75**, T4 **5/75** — the genuinely best-observed objects (office_3 chair#25
  at 121°/0.75 hull is #1; fuses to 0.88×0.67×0.86 m). The partial majority still
  (correctly) goes generative. So the gate now DISCRIMINATES instead of rejecting
  everything via a broken threshold.
- **Honest caveats:** (1) selected objects are STILL partial-view (chair#25 misses
  ~⅓ back) so their TSDF is non-watertight — they lean on Step 7b repair, not magic.
  (2) Thresholds are PROVISIONAL: grounded in percentiles, NOT in ground-truth
  "fully-vs-partially observed" labels (we have none). Proper calibration needs
  walk-around captures. (3) This is a deliberate DEVIATION from the plan
  (150/0.65 bbox → 110/0.45 hull); the plan's numbers were never data-checked.
- 85 tests pass (added hull-metric + denom tests). `pipeline.yaml` documents the change.

## What an earlier session did (2026-06-27, all-8-scenes TSDF + gate test)
Built bundles for **all 8 Replica vMAP scenes** (streaming, see Replica data above)
and ran TSDF fusion + the Step-5 gate across all of them (`scripts/report_scenes.py`).
**Result: TSDF fuses correctly on every scene (real-world dims), and the gate routes
ALL 75 objects to "generative" (0 tsdf) at Tier 2.** Per-scene best-object TSDF dims:
room_0 couch 2.31×0.92×1.02, room_2 dining-table 2.12×0.64×1.35, office_2 table
1.58×0.50×1.58, office_3 chair 0.88×0.67×0.86 — all sane, all watertight=False.
- **Why 0 tsdf everywhere (honest, important finding):** max per-object angular
  coverage across all 8 scenes is **123.3°** (office_4), below even the loosest
  Tier-4 bar (130°); completeness is also low (~0.04–0.33). vMAP trajectories are
  center-of-room exploration scans — the camera rotates a lot (forward-dir spans
  ~179°) but never ORBITS a single object past ~123°, so nothing clears the
  walk-around gate. This is the DESIGNED behavior (partial views → generative), and
  it means **the tsdf-accept branch is unreachable on this dataset** — to exercise
  it on real data we need genuine walk-around footage, OR the thresholds need
  revisiting for room-scan captures (a real open question for the project).
- **Net:** both modules are now validated on 8 diverse real scenes. The room-scan
  conclusion is consistent, not a one-off room_0 artefact.

## What an earlier session did (2026-06-27, Phase 6 / confidence gate)
Built **`src/reconstruction/confidence.py`** (Step 5) + `scripts/run_gate.py`, ran
on real room_0. Scores the RAW cloud (fix V1): **angular coverage** (largest
pairwise centroid→camera view-angle, shape-independent primary signal) AND
**surface completeness** (ball-pivoting/alpha-shape patch area ÷ oriented-bbox
area, clamped [0,1] — fix V2; empirical knob per fix Z-U). Both must clear the
per-tier bar (read from `config/pipeline.yaml`: Tier2 150°/0.65). Tier 1 = all
generative, no scoring. Output `confidence/{track_id}.json`.
- **room_0 @ Tier 2: all 16 objects → "generative".** This is CORRECT, not a bug:
  the camera's *forward* direction spans 178.8° (rotates to scan the whole room)
  but each *object's* angular coverage maxes at ~86° (couch 78.8°) — a
  center-of-room scan sees object fronts across a limited arc and never orbits
  behind any single object. Partial front-only views → generative to complete the
  unseen back is exactly the designed behavior. **Implication:** room_0's
  Phase-5 TSDF shells would all be bypassed for generative (consistent — they
  weren't watertight); the room_0 path is therefore entirely generative (needs
  RunPod, Phase 7/8). To exercise the *tsdf-accept* branch on REAL data we need
  walk-around footage; unit tests prove that branch fires.
- 12 new tests (`test_confidence.py`). **83 total pass.**
- **Run:** `PYTHONPATH=src ~/projects/vid2sim/venv/bin/python scripts/run_gate.py
  --bundle ../data/replica/bundle_room0 --tier 2`.

## What an earlier session did (2026-06-27, Phase 5 / TSDF)
Built **`src/reconstruction/tsdf.py`** (Step 4 Part B) and validated it on **real
Replica room_0** with GT masks + poses. Scene-level `VoxelBlockGrid` pass on
`CPU:0`, per-object grid sized from the Step-4A observed-cloud bbox (fix M2), real
tensor API (`compute_unique_block_coordinates` → `integrate`, fix X1), depth gates
in mm (fix G1). `extract_triangle_mesh` per object → `.ply`/`.glb` export.
- Ran on couch(9)/chair(73)/dining-table(11) at 4 mm in **14 s on CPU**. Mesh
  bboxes match Step 4A: couch **2.32×0.89×1.03 m**, chair 0.83×1.08×0.82 m, table
  1.43×0.47×0.83 m. Exports in `out/tsdf_room0/`.
- **Honest quality:** meshes are **edge-manifold but NOT watertight** (open backs/
  undersides + floating fragments, e.g. couch 575 components). Expected for
  single-pass TSDF on partial views — Step 5 gate + Step 7b repair handle this
  downstream. Good enough to eyeball in Blender; not yet physics-ready.
- Then **wired the Z-T motion filter** (see gap #2 below): two-pass keep-frame
  walk in `observed_cloud`, shared into `tsdf.fuse` as the single source of truth.
- Tests: 6 for tsdf + 9 for Z-T/observed_cloud. **71 total pass.**
- **Run:** `PYTHONPATH=src ~/projects/vid2sim/venv/bin/python scripts/run_tsdf.py
  --bundle ../data/replica/bundle_room0 --out out/tsdf_room0 --voxel 0.004
  --tracks 9 73 11` (omit `--tracks` for all; `--fmt glb` for Blender).

### Phase-5 gotchas (hard-won)
1. **`pose.inv()` is non-contiguous** → Open3D `integrate` rejects it. Must call
   `.inv().contiguous()` on the extrinsic.
2. **No `conf.png` in the Replica bundle** — the plan's `depth[conf<150]=0` line
   would crash. Conf gate is now OPTIONAL (`conf_min=None` default; only applied
   when both `conf_min` set AND conf maps exist).
3. **`extract_triangle_mesh` default weight threshold (~3)** drops voxels seen by
   <3 frames — synthetic 2-frame tests came out empty; real objects (50–100
   frames) are fine.
4. **CPU works** (open3d 0.19 tensor VBG on `CPU:0`), ~5 s/object at 4 mm; the
   plan recommends CUDA but never required it here.

## What an earlier session did (2026-06-27)
Added **`ReplicaReader`** to `dataset_reader.py` and validated the **object/segmentation
path on real ground-truth data** (the thing TUM can't test, because TUM has no labels).
- Replica ships **perfect GT instance masks**, so YOLO+SAM2 are skipped — the GT masks are
  written straight into the bundle as `objects.json` + `mask_{track_id}.png`.
- Verified end-to-end on room_0: **16 COCO objects** (couches/chairs/dining tables/books),
  couch observed cloud = **2.34×0.90×1.06 m** (real size). 56 tests still pass.
- **Constants were verified against the real files, NOT trusted from docs** (the docs were
  wrong on two): see the Replica gotchas below.

## Replica gotchas (hard-won; all encoded in `ReplicaReader`)
1. **Depth is already MILLIMETRES** (`metres = png/1000`). The commonly-cited iMAP
   `/6553.5` is WRONG for this vMAP render (it would make the whole room < 0.77 m).
2. **`traj_w_c.txt` is camera→world** = our `T_world_camera` convention — use directly,
   NO inversion. (A doc summary claimed world→camera; it's wrong.)
3. **The Replica world is Z-UP**; our contract is Y-up (`scene.json up_axis "y"`,
   `observed_cloud` ground = min Y). `ReplicaReader` rotates Z→Y via
   `_REPLICA_ZUP_TO_YUP` (a −90° rotation about X: (x,y,z)→(x,z,−y)). Verified: scene
   height then lands on Y (~2.80 m ceiling), floor at min Y.
4. Intrinsics: 1200×680, hfov 90 → fx=fy=600, cx=599.5, cy=339.5.
5. Instance IDs are globally consistent across frames → each IS a `track_id`. Object
   class = majority `semantic_class` id over the instance's pixels → `render_config.yaml`
   name → COCO via `coco_class_map.yaml` (already has a `replica:` section). Structural
   classes (wall/floor/window…) map to "default" and are dropped.
6. `render_config.yaml` embeds numpy-pickled blobs → needs `yaml.unsafe_load` (the reader
   only consumes the plain `classes` list).

## What is validated vs not (read before trusting anything)
- **Validated on REAL data:** camera trajectory (Step 3, TUM, 3.66 cm ATE) and, now, the
  **per-object observed cloud** (Step 4A) on real Replica frames with GT masks.
- **Logic-tested with synthetic/fake inputs only:** schema, bundle I/O, TUM/Replica parsing
  detail, pose-composition math, and **all of SAM2's orchestration** (fake backend).
- **Validated locally end-to-end (this session):** assembled office_3 served by
  `src/server.py` and **rendered + simulated in a real browser engine** (headless
  Chrome) — 3 objects load, physics steps, no JS errors. Software WebGL, not a GPU.
- **Never run / doesn't exist:** real **SAM2** model; **YOLO** detection; MASt3R /
  ORB-SLAM3 (stubs); the **generative path** (Steps 6–8, RunPod/GPU). The browser
  exists and runs; its physics is functional but not tuned/stress-tested.

## Agreed next steps (for the next instance)
Phases 1–6 + 9 + **10 + 11** are built. The pipeline now runs end-to-end **locally
with no GPU**: assemble → serve → render+simulate in the browser. office_3's
couch/table/chair are visible and interactive.
- **The viewer works now** — `python -m server`, open `http://127.0.0.1:8000/`.
  See "What this session did (Phase 10+11)" above and `frontend/README.md`.
- **Remember the sparse-scene constraint** (top of this file): only tsdf objects
  exist; generative ones are omitted until a GPU is connected. office_3 @ Tier 4
  shows 3 of 14 objects — that's expected, not a bug (the HUD says so on screen).
- **Honest residual caveats / future work:**
  - SSE is a *replay* of a finished on-disk scene, not a live assembler feed. To
    make it live, the assembler (Phase 9) would write objects incrementally and
    the server would watch the dir / receive emits instead of replaying.
  - Physics verified to *run* (objects load, step, no errors) but not tuned —
    starting heights are the observed heights, so objects settle onto the floor
    on first frames; the §16 INERTIA CAVEAT (density-0 + scalar mass can give a
    degenerate inertia tensor → odd spin) was NOT hit in the static settle test
    but isn't stress-tested. If spin looks wrong, switch to
    `setAdditionalMassProperties` (a bbox-box inertia approx).
  - Verified with **software WebGL** (swiftshader, headless). Real GPU browsers
    will look better; the context-lost warning is swiftshader-only.
  - Meshes are partial-view non-watertight TSDF shells (open backs) — render
    fine, but colliders are only as good as those shells.
- **Remaining build phases:** 12 (CLI + tier wiring), 13 (integration), 14
  (ORB-SLAM3, optional), 15 (live OAK capture, needs hardware), plus the deferred
  generative path (Steps 6–8, RunPod/GPU). Optional hardening: deterministic mass
  volume (Step 7b approximate); real SAM2; live Claude physics call (vlm.py + key).
- **A ready test scene exists:** `out/scene_office_3/` (regenerate with
  `scripts/run_assemble.py --bundle .../bundles/office_3 --tier 4 --out ...`).
  Open3D CANNOT read its own .glb back (writes fine for three.js); don't QA in o3d.

## Git state
- Branch **`fix/phase3-pose-and-eval`**. Pushed: Phase 1–3 (`2b56f92`), Phase 4
  (`e122c37`), Phase 5 (`14083c7`), Phase 6 (`956ec7a`), 8-scene tooling
  (`eaf71ab`), gate recalibration (`c396850`), generative-deferral note
  (`fe6d4d9`), Phase 9 assembly (`15408b7`), mass fix (`0f6d7be`), STATUS refresh
  (`fb31168`).
- **This session (Phase 10+11) — to be committed:** `src/server.py`,
  `tests/test_server.py`, `frontend/` (index.html, app.js, README.md, vendored
  `vendor/three` + `vendor/rapier`), `pyproject.toml` (serve/httpx extras), this
  `STATUS.md`. `master` stays clean.

## Carried-forward gaps & gotchas
1. **Detection gap (unchanged):** the pipeline assumes per-frame `objects.json`+masks. TUM
   has none (needs host-side YOLO behind `TUMReader(detector=)`); Replica sidesteps it with
   GT masks. Live OAK on-NPU YOLO is Phase 15 (`capture.py`, not built).
2. ~~Z-T motion filter not wired~~ **DONE** — implemented as the two-pass keep-frame
   walk in `observed_cloud` (`bbox_diagonal`, `motion_keep_indices`,
   `accumulate_object_cloud(motion_filter=, return_keep=)`) and shared into
   `tsdf.fuse(motion_filter=)` via `tsdf.analyze_objects` (the single source of
   truth keyed by frame_id, fix Z-T). Default OFF. Verified: no-op on static
   room_0 (all frames kept, extents unchanged), drops jumped frames on synthetic
   moving objects. `obj_size_m` uses the FULL multi-view diagonal so the camera
   orbit doesn't false-reject static frames (the Z-A/parallax property).
3. **Confidence completeness metric is shape-dependent** (fix Z-U): treat thresholds as
   empirical; angular coverage is the shape-robust signal. Matters at Phase 6.
4. **Crop staging seam (Z-B-crop):** crops staged at `crops/crop_{track_id}.jpg`; Step 10
   must relocate to `objects/{id}/crop.jpg`.
5. Low-risk audit items: `bundle.write_poses` labels frames by enumerate index; TUM
   `associate()` is many-to-one; `schema.load_schema(path)` custom path ignored by the
   cached validator.
