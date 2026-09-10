_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

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
