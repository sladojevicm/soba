# Demo-grade `_v2` bundles on the pod: regenerate, do not copy

**Status 2026-09-17.** The previous version of this page copied the `_v2` orbit bundles
from "the 4060" with rsync. That machine was a pod whose volume no longer exists, so
there is nothing to copy. The bundles are regenerated instead, from the Replica dataset
meshes, with the same script and the same settings BENCHMARK.md §3 used. Everything
below is CPU-only and runs on the validation pod while the GPU is idle.

## Why `_v2` and not the vMAP room scans

`deploy/runpod/gpu_validate.sh` builds its smoke bundle from the vMAP room-scan
sequences on HuggingFace (`scripts/build_replica_bundles.py`). Those trajectories walk
the room and never orbit an object, so the gate sees at most ~123° of coverage and
routes most objects to "generative" (runs 1 and 2 on 2026-09-16: 10 of 14). The paper's
§3 numbers came from **custom orbit trajectories** rendered by
`scripts/render_replica.py` from the Replica semantic meshes: 200 frames per room,
ground-truth poses and instance masks, a walkthrough plus a slow orbit around each large
object, so most objects route to completion or keep. room_2 at tier 2 scored 89/100 that
way. That is the demo-grade input.

## What is needed

| Item | Source | Notes |
|---|---|---|
| `scenes/<room>/habitat/mesh_semantic.ply` + `info_semantic.json` for the 8 rooms | Replica dataset v1 (`facebookresearch/Replica-Dataset`), **not** the vMAP zip | the two files the renderer reads |
| Rooms | `room_0 room_1 room_2 office_0 office_1 office_2 office_3 office_4` | the same 8 the benchmark used; Replica ships 18 |
| Renderer | `scripts/render_replica.py` (open3d CPU raycasting, numpy, the repo's `perception` package; `cv2` only for `match-test`) | already installed by bootstrap |
| Licence | Replica Dataset Research Terms: non-commercial research only | same footing as the rest of Soba |

Sizes and times (estimates from the release parts and the ray count; nothing measured
on the pod yet):

| Step | Estimate |
|---|---|
| Download | 17 parts of ~2 GB from GitHub releases, ~34 GB total; the tar stream must be fetched whole |
| Kept on the volume | only the 8 `habitat/` directories, single-digit GB |
| Render, all 8 rooms at 200 frames | CPU, deterministic (no RNG in the planner); a few minutes per room |
| Output | ~150–200 MB per room, ~1.5 GB for all 8 |

## 1. Download Replica v1 to the volume (once)

```bash
cd /workspace && mkdir -p replica_dl && cd replica_dl
for p in {a..q}; do
  wget -c "https://github.com/facebookresearch/Replica-Dataset/releases/download/v1.0/replica_v1_0.tar.gz.parta$p"
done
```

`wget -c` resumes; rerun the loop if the pod's connection drops.

## 2. Extract only the 8 rooms' `habitat/` directories

```bash
mkdir -p /workspace/replica/scenes
cat replica_v1_0.tar.gz.part?? | tar -xz -C /workspace/replica/scenes --wildcards \
  'room_0/habitat/mesh_semantic.ply' 'room_0/habitat/info_semantic.json' \
  'room_1/habitat/mesh_semantic.ply' 'room_1/habitat/info_semantic.json' \
  'room_2/habitat/mesh_semantic.ply' 'room_2/habitat/info_semantic.json' \
  'office_0/habitat/mesh_semantic.ply' 'office_0/habitat/info_semantic.json' \
  'office_1/habitat/mesh_semantic.ply' 'office_1/habitat/info_semantic.json' \
  'office_2/habitat/mesh_semantic.ply' 'office_2/habitat/info_semantic.json' \
  'office_3/habitat/mesh_semantic.ply' 'office_3/habitat/info_semantic.json' \
  'office_4/habitat/mesh_semantic.ply' 'office_4/habitat/info_semantic.json'
ls -la /workspace/replica/scenes/*/habitat/
```

Then free the ~34 GB of parts: `python3 -c "import shutil; shutil.rmtree('/workspace/replica_dl')"`.
(Keep them if you expect to extract more rooms later; the download is the slow part.)

## 3. Gate: `match-test` on office_3

The smoke bundle `bundles/office_3` from `gpu_validate.sh` is an ORIGINAL vMAP render
with its ground-truth pose. Re-rendering frame 0 from that pose through our renderer
must reproduce the original depth (July: 0.0 mm median error). This proves the mesh,
the intrinsics and the Z-up→Y-up convention line up before any bundle is trusted.

```bash
cd /workspace/soba && export PYTHONPATH=src
python3 scripts/render_replica.py match-test \
  --scene-dir /workspace/replica/scenes/office_3 \
  --bundle bundles/office_3 --frame 0 \
  --out /workspace/replica/previews/match_office_3.png
```

Expect `gate: PASS (median depth diff < 30 mm)`. Do not continue on FAIL; paste the
numbers back.

## 4. Render the 8 `_v2` bundles (200 frames, as in BENCHMARK.md §3)

```bash
cd /workspace/soba && export PYTHONPATH=src
for room in room_0 room_1 room_2 office_0 office_1 office_2 office_3 office_4; do
  python3 scripts/render_replica.py render \
    --scene-dir /workspace/replica/scenes/$room \
    --out bundles/${room}_v2 --frames 200 2>&1 | tee /workspace/replica/render_${room}.log
done
```

`--frames 200` is required: the script's default is 1800. It refuses to overwrite an
existing `bundles/<room>_v2`. `room_1` is walkthrough-only (no orbit-sized furniture)
and was empty at every tier in the benchmark; render it anyway for completeness.

## 5. Run one through the job API

```bash
cd /workspace/soba
export SOBA_TRIPOSG_HOME=/workspace/TripoSG SOBA_PATCHCOMPLETE_HOME=/workspace/PatchComplete
export SOBA_COMPLETION_STRICT=1
BUNDLE_DIR=bundles/room_2_v2 KEEP=1 bash deploy/runpod/gpu_validate.sh
```

`SCENE` stays `office_3` (it is only used when a bundle has to be built); `BUNDLE_DIR`
points at the rendered bundle. Expect the gate distribution in the summary to move
from mostly `generative` to mostly `completion`/`tsdf`, and `completion.counts` in
`run_metrics.json` to show `patchcomplete`, not `poisson_*`.

## 6. Optional: the benchmark's GT evaluation

With the meshes on the volume, `scripts/evaluate_scene.py` can score a scene against
ground truth exactly as §3 did. It also wants the vMAP GT trajectory for pose columns,
which `scripts/fetch_replica_gt_traj.py` streams out of the HF zip. Do not add a
BENCHMARK.md row from this (invariants 3, 4): the model pins behind §3 are
reconstructed by date, not the originals (`docs/model-pins.md`).

```bash
python3 scripts/evaluate_scene.py --scene out/jobs/<id>/scene --room room_2 \
  --gt-scene /workspace/replica/scenes/room_2 --gt-traj /nonexistent
```

(`--gt-traj /nonexistent` forces pose = n/a, as the `_v2` bundles consume GT poses.)
