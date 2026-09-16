# 2026-09-16 — GPU validation run 2: the generative band (TripoSG) on the RunPod 4090

Same volume as run 1, new pod `4ec79bda24a5` (driver 580.126.20), `develop@ce13747`,
after `SETUP_TRIPOSG=1` bootstrap with the torch-aware HF pins (`setup_triposg.sh`)
and coacd from the `recon` extra. Summary pasted verbatim:

| step | result | detail |
|---|---|---|
| S0 | INFO | sha: ce13747 (develop); NVIDIA GeForce RTX 4090, 24564 MiB, 580.126.20 |
| S1 | PASS | open3d CUDA tensor OK on CUDA:0 (TSDF VoxelBlockGrid will run on GPU) |
| S2 | FAIL | 2 failed, 503 passed, 11 skipped, 3 warnings in 127.77s (see pytest.txt) |
| S3 | PASS | reused existing bundles/office_3 (100 frames) |
| S4 | PASS | out/scene_test written |
| S5 | PASS | GET /scene.json 200; open-mode warning: 2 |
| S6 | PASS | job 38863d0daff09b7a done; 10 objects; gate tsdf=0/completion=4/generative=10; drops {"engine_declined": 4}; run 760s |
| S7 | SKIP | WITH_REDIS=1 not set |
| S8 | INFO | 8 lines in pins.txt |

Knobs: SCENE=office_3 STRIDE=20 MAX_FRAMES=100 TIER=2, `SOBA_TRIPOSG_HOME=/workspace/TripoSG`,
ANTHROPIC_API_KEY unset. `pins.txt`: **TripoSG fc5c40990181e2a756c4e0b1c2f4d6b5202faf8c**
(https://github.com/VAST-AI-Research/TripoSG.git), coacd 1.0.14, numpy 2.4.6, open3d 0.19.0,
scipy 1.17.1, torch 2.4.1+cu124, trimesh 5.1.0.

## What it settles

- **The generative band runs on a GPU through the job API.** Of the 10 objects the gate
  routed to generative, TripoSG built 6 and the engine declined 4 (`engine_declined`:
  debris / class-size rejection, the drop-garbage policy, `docs/log/2026-07-04`). Scene:
  10 objects (4 completion + 6 generative) vs 4 in run 1. Wall time 760 s vs 225 s: the
  extra ~535 s is TripoSG at 50 steps for 10 crops on the 4090 (~50 s per object),
  consistent with the tier-2 runtime paragraph in the paper.
- **Hull colliders are real again**: coacd 1.0.14 present (run 1 had none).
- **First model pin recorded in the repo**: TripoSG commit `fc5c4099…`, cloned
  2026-09-16. It is the commit this validation used, NOT the one that produced
  `BENCHMARK.md` (that pin is still to be recovered on the 4060; `docs/model-pins.md`).

## Findings

1. **`transformers` needs torch >= 2.5**; on the template's torch 2.4.1 the unpinned
   install broke `import diffusers` ("name 'nn' is not defined"). `setup_triposg.sh`
   now pins transformers 4.46.3 / diffusers 0.32.2 / peft 0.14.0 / accelerate 1.2.1
   when torch < 2.5 (`ce13747`); that pinned set is what this run used.
2. **`gpu_validate.sh` ran pytest with `-rs`**, so `pytest.txt` has the two failures'
   tracebacks but no `FAILED` summary lines; fixed to `-rfEs`. Identified from the tracebacks: `test_mast3r_empty_bundle_returns_no_poses`
   (`ModuleNotFoundError: dust3r`, no MASt3R checkout on the pod) and
   `test_make_backend_reads_config_model` (`anthropic` SDK not installed, `make_backend`
   returns None). Both environmental, the same two CI excludes (`docs/ci-probe.md`).
   Closed in bootstrap: `anthropic` installed with the extras; `SETUP_MAST3R=1` runs the
   new `deploy/runpod/setup_mast3r.sh` (clone with submodules, metric checkpoint + sha256).
3. Redis path not repeated this run (`WITH_REDIS` unset); it passed in run 1.

## Not run

RunPod serverless endpoint; compose `gpu` profile; Hunyuan3D (tiers 3–4); any
accuracy number (invariants 3, 4).
