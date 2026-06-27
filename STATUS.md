# vid2sim-v2 — Build Status & Handoff

_Last updated: 2026-06-27. This is a working handoff so a fresh session can resume
without re-deriving everything. The authoritative design is `PLAN_FINAL_FINAL.txt`
(currently at `~/projects/vid2sim/PLAN_FINAL_FINAL.txt`, version 12)._

## How to resume (read these in order)
1. This file (current state + next steps).
2. `~/projects/vid2sim/PLAN_FINAL_FINAL.txt` — the full design (2265 lines, v12). The
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
- **NO GPU.** CPU only. `nvidia-smi` fails; torch would be CPU.
- **Not installed:** torch, sam2, ultralytics (needed to actually run SAM2 / YOLO).
- **open3d is the CPU wheel** but the tensor TSDF `VoxelBlockGrid` **works on `CPU:0`**
  (verified) — so Phase 5 TSDF is runnable here, just slow. room_0 is small enough.
- ~40 GB disk free.

## How to run
```bash
cd ~/projects/vid2sim/vid2sim-v2
PYTHONPATH=src ~/projects/vid2sim/venv/bin/python -m pytest -q      # 56 tests, all pass

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
| 5 Confidence gate | `confidence.py` | ✅ **built + RECALIBRATED on all 8 scenes.** Angular + shape-fair **hull** completeness (Z-U); thresholds recalibrated from real distributions (T2 110°/0.45). Routes 1–5/75 best-observed objects → tsdf, rest → generative (old bbox metric made 0.65 unreachable → 0 tsdf) |
| 6 Generative (RunPod) | `runpod_client.py` + infra | ❌ not built |
| 7 ICP align | `icp_align.py` | ❌ not built |
| 7b Mesh finalise | `decimate.py` | ❌ not built |
| 8 Physics (Claude) | `scene/vlm.py` | ❌ not built |
| 9 Convex decomp (CoACD) | `scene/decomp.py` | ❌ not built |
| 10 Scene assembly | `scene/assembler.py` (+ schema validator EXISTS) | ❌ not built |
| 11 Browser (Three.js+Rapier) | `frontend/` | ❌ not built |

**One-line summary:** the front of the pipeline works on real data — **Step 1 → Step 3
→ Step 4A → Step 4B (TSDF) → Step 5 (gate)** — i.e. we can take footage, produce per
object the real points seen + a fused mesh, and decide tsdf-vs-generative. Step 2
(SAM2) is built but unproven. Everything from Step 6 (generative/RunPod) on is unbuilt.

## What this session did (2026-06-27, gate recalibration)
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
- **Never run / doesn't exist:** real **SAM2** model; **YOLO** detection; MASt3R / ORB-SLAM3
  (stubs); everything Step 4B+ (TSDF, gate, generative, ICP, physics, assembler, browser).

## Agreed next steps (what the user wants next)
The plan: **test SAM2 on Replica → implement Phase 5 (TSDF) → load meshes into Blender to
eyeball them.** Notes:
- **SAM2 test and Phase 5 are INDEPENDENT** — TSDF uses Replica's GT masks, it does NOT
  need SAM2. So order is flexible.
- **Recommended: build Phase 5 (TSDF) first** — it needs nothing installed (open3d CPU
  works), and it's the fastest path to actual meshes you can open in Blender. Then circle
  back to the SAM2 IoU test (which needs a ~1–2 GB torch+sam2+checkpoint install, slow on CPU).
- **Phase 5 first pass can SKIP Z-T** — room_0 is a static scene, so straight TSDF
  integration is correct and will produce valid meshes. Add Z-T (the keep-frame motion
  filter, plan fix Z-T) as a follow-up for moving objects / full plan fidelity.
- **Blender is a DEV inspection tool, not the product.** The plan's real target is the
  **browser** (Three.js + Rapier, Step 11). Blender just lets us eyeball the TSDF `.ply`/`.glb`
  (does the couch look like a couch, no holes/ghosting). Not in conflict — Blender now,
  browser much later.

### Concrete Phase 5 task
Build `src/reconstruction/tsdf.py` per plan §9 Part B (lines ~984–1117): one scene-level
`VoxelBlockGrid` pass on `Device("CPU:0")`, per-object grid sized from the observed-cloud
bbox (fix M2), masked depth integration with `compute_unique_block_coordinates(...)` then
`integrate(...)` (the real tensor API, fix X1), `extract_triangle_mesh()` per object, export
a `.glb`/`.ply` for Blender. Test on `bundle_room0` (couch/table/chair are good targets).
Mind the depth validity gates in MILLIMETRES (fix G1: 400–8000 mm).

## Git state
- Branch **`fix/phase3-pose-and-eval`**. Pushed: Phase 1–3 fixes (`2b56f92`),
  Phase 4 SAM2 + ReplicaReader (`e122c37`), Phase 5 TSDF + Z-T (`14083c7`).
- **Uncommitted** on that branch: **Phase 6 (`src/reconstruction/confidence.py` +
  `tests/reconstruction/test_confidence.py` + `scripts/run_gate.py`)**, the
  `reconstruction/__init__.py` exports, and this `STATUS.md`. `master` is clean.

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
