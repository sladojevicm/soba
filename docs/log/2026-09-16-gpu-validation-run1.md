# 2026-09-16 — GPU validation run 1: the service layer on a RunPod RTX 4090

First time anything from Waves 0–3 ran on a GPU. Pod `48a8f5d7762c`, RunPod
"PyTorch 2.4.0" template (Python 3.11.10, torch 2.4.1+cu124, driver 570.195.03),
100 GB network volume at `/workspace` (EU-RO-1), `develop@bb99956`. Script:
`deploy/runpod/gpu_validate.sh`, smoke size. Summary pasted verbatim:

| step | result | detail |
|---|---|---|
| S0 | INFO | sha: bb99956 (develop); NVIDIA GeForce RTX 4090, 24564 MiB, 570.195.03 |
| S1 | PASS | open3d CUDA tensor OK on CUDA:0 (TSDF VoxelBlockGrid will run on GPU) |
| S2 | FAIL | 2 failed, 503 passed, 11 skipped, 3 warnings in 78.15s (see pytest.txt) |
| S3 | PASS | built (100 frames) |
| S4 | PASS | out/scene_test written |
| S5 | PASS | GET /scene.json 200; open-mode warning: 2 |
| S6 | PASS | job 6dc66dba242a06c0 done; 4 objects; gate tsdf=0/completion=4/generative=10; drops {"engine_declined": 10}; run 225s |
| S7 | PASS | job b1630caf073d9cf1 done via redis worker; 4 objects; gate tsdf=0/completion=4/generative=10; drops {"engine_declined": 10}; run 227s; remote calls=0 est_usd=0.0 |
| S8 | INFO | 6 lines in pins.txt |

Knobs: SCENE=office_3 STRIDE=20 MAX_FRAMES=100 TIER=2 WITH_REDIS=1, TripoSG unset,
ANTHROPIC_API_KEY unset. `cuda_check.txt`: `torch.cuda True NVIDIA GeForce RTX 4090` /
`open3d CUDA tensor OK on CUDA:0`. `pins.txt`: numpy 2.4.6, open3d 0.19.0, scipy 1.17.1,
torch 2.4.1+cu124, trimesh 5.1.0 (no model checkouts on the fresh volume).

## What it settles

- **Open3D CUDA.** The pip `open3d==0.19.0` wheel DOES have the CUDA tensor backend on
  a real GPU. The 2026-09-11 CI finding ("Unsupported device CUDA:0, set
  BUILD_CUDA_MODULE=ON") was the same wheel on a runner with no GPU: that message is
  what Open3D prints when no CUDA device exists, not proof of a CPU-only wheel. TSDF
  runs on the GPU as designed; the "pre-deploy gate" in `docs/runbook.md` §6 stays
  (it is still the right check on any new host) but the limitation is closed.
- **Job API in real mode, on a GPU.** Upload → validate → `run_assemble.py` → done,
  `run_metrics.json` + `cost.json` written, `/metrics` ingested them (S6). The same
  through Redis + the external `orchestration.worker` (S7). Both 225–227 s for a
  100-frame tier-2 build with the generative band dropped.
- **Gate on the room-scan bundle.** completion=4, generative=10, tsdf=0 — consistent
  with the ≤123° coverage ceiling of vMAP room scans (`docs/log/2026-06-27`); not a
  regression.

## Correction (2026-09-17)

"TSDF runs on the GPU as designed" above was wrong. S1 proves the CUDA tensor backend
EXISTS on the pod; it does not prove fusion used it. `tsdf.fuse` defaults to `CPU:0`
and `scripts/run_assemble.py` passes no device, so every pod run fused on the CPU.
The S1 message printed by the scripts said "TSDF VoxelBlockGrid will run on GPU",
which invited the mistake; it now says the backend is available and fusion is on CPU.

## Findings

1. **`coacd` was not installed on the pod** (absent from `pins.txt`): bootstrap installs
   the extras and coacd was in none, so the two scenes shipped single-hull colliders
   (`scene/decomp.py` fallback). Fixed the same day: `coacd>=1.0` is in the `recon`
   extra now (STATUS.md known-limitation closed).
2. **pip on the template's system Python failed on `blinker 1.4`** (distutils install
   that open3d→flask needs replaced). Fixed in `bootstrap.sh` before this run
   (`bb99956`): `pip install --ignore-installed blinker` first.
3. **2 pytest failures on the pod** — under review; FAILED lines requested from the
   maintainer (likely the gpu-marked MASt3R test without a checkout and the VLM
   backend test without the `anthropic` SDK, as in CI, but not assumed).
4. **The generative band has not run on a GPU yet**: all 10 generative-routed objects
   were `engine_declined` (no `SOBA_TRIPOSG_HOME`). Next run: `SETUP_TRIPOSG=1`.

## Not run (needs its own session)

RunPod serverless endpoint + hardened client against it; compose `gpu` profile
(no Docker inside a pod); any accuracy number (invariants 3, 4).
