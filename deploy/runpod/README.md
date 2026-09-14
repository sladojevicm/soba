# Running the soba host pipeline on RunPod

> **Deploying Soba as a service?** Start with [`docs/runbook.md`](../../docs/runbook.md):
> images and tags, the env matrix and secrets, compose / two-host / GPU-worker
> deployment, the RunPod **serverless** endpoint, the mandatory pre-deploy gates
> (Open3D CUDA check, model pins, CI), smoke test, alerts, backup, rollback,
> incident checklist and the release flow. This page keeps only what is specific
> to renting and using an interactive GPU **pod**.

This pod runs the **host (NVIDIA) side** of the pipeline: SAM2, RGB-D pose,
Open3D **TSDF fusion (CUDA-only)**, the confidence gate, scene assembly, and the
scene server/viewer. It does **not** need to be the big generative GPU — that's a
separate, optional RunPod *serverless* endpoint for TripoSG/Hunyuan3D.

> The artifacts here (`bootstrap.sh`, `env.example`) make pod setup a single
> command. Renting the pod and pasting your API keys is the part only you can do.

---

## 0. Container images and model pins

The same stack is packaged as images under `docker/` (built in CI by
`.github/workflows/ci.yml`, which does not push them; `docs/runbook.md` §3 has
the tag scheme and push commands):

- `docker/pipeline.Dockerfile` — this host pipeline on a `pytorch/pytorch:2.6.0-cuda12.4`
  base (`[recon,serve,dev,api,telemetry,worker]` + coacd + runpod). Entrypoints:
  `worker` (`python -m orchestration.worker`, the Redis queue consumer),
  `serverless` (`python deploy/runpod/generative_handler.py`, the endpoint in the
  last section), `assemble <args>`, `serve`, `check` (Open3D CUDA tensor check on
  a real GPU), `shell`. Build with `--build-arg BASE_IMAGE=...-devel` when the
  TripoSG / Hunyuan3D setup scripts must compile CUDA extensions. Weights and
  model checkouts are mounted under `/workspace`, never baked.
- `docker/api.Dockerfile` — the job API + scene server + committed viewer bundle (no GPU).
- `docs/model-pins.md` — which model versions produced `BENCHMARK.md`, and every
  pin the repo does not record (git commits, checkpoint sha256, HF revisions).
  Recording them is a release gate (`docs/runbook.md` §6).

**Open3D CUDA gate.** The `open3d` wheel pip resolves has no CUDA module
(`docs/log/2026-09-11-docker-ci.md`), so on any GPU host, image or pod, run
the check before trusting a tier ≥ 2 build: `docker run --rm --gpus all
soba-pipeline:<tag> check` (exit 1 = TSDF would silently run on CPU), or the
"Verify host-pipeline stack" block that `bootstrap.sh` prints. What to do
when it fails is in the runbook.

## 1. Rent the pod

1. Create an account at <https://runpod.io> and add credit/billing.
2. **Deploy → Pods → GPU Cloud.** Pick a GPU:
   - **RTX 4090 (24 GB)** — recommended. Runs TSDF + SAM2 + PoinTr comfortably and
     is the cheapest 24 GB card. (An A100/H100 is overkill for the *host* side —
     save those for the generative endpoint.)
   - 16 GB (e.g. A4000/4080) works for Replica/TUM testing and small scenes.
3. **Template:** choose an official **PyTorch 2.x / CUDA 12.x** template so
   `torch` + CUDA are preinstalled (the bootstrap installs into that interpreter).
4. **Volume:** attach a **persistent volume mounted at `/workspace`** (≈50–100 GB).
   This keeps the repo, datasets, and any PoinTr checkpoints across pod
   stop/restart so you don't re-download on every cold start.
5. **Expose a port:** add **HTTP port 8000** (for the scene viewer in step 4 below).
6. **On-demand** for interactive work; use **Spot/Community** only for long batch
   reconstruction you can restart. Deploy, then open the pod's **web terminal**
   (or SSH).

## 2. Bootstrap (one command on the pod)

```bash
cd /workspace
git clone --depth 1 --branch master https://github.com/sladojevicm/soba.git   # bootstrap.sh also does this (REPO_BRANCH=master)
bash soba/deploy/runpod/bootstrap.sh          # REPO_BRANCH=<tag> to pin a release
# optional learned completion as well:  SETUP_POINTR=1 bash .../bootstrap.sh
# OR ComPC (training-free, preserves observed geometry — needs >=16GB GPU,
# isolated env; brittle, may need iteration):  SETUP_COMPC=1 bash .../bootstrap.sh
```

### ComPC middle band (optional, `SETUP_COMPC=1`)

ComPC runs in an **isolated micromamba env** (py3.10.13 / CUDA 11.6 / torch
1.12.1) built by `deploy/runpod/setup_compc.sh`; the host pipeline (torch 2.x)
calls it as a subprocess. Diffusion weights (SD-2.1 + Zero123) auto-download from
HuggingFace on first run. After it builds, activate it from the host venv with the
three exports the script prints (`COMPC_HOME`, `SOBA_COMPLETION_MODEL=compc`,
`SOBA_COMPC_CMD=...`), then run the gate at a tier where survivors route to the
`completion` band. It is **per-object SDS (minutes each)** and emits ~10–16k
points that the pipeline Poisson-meshes. This is the brittle step — run it
explicitly and iterate on any build error.

`bootstrap.sh` is idempotent. It clones/updates the repo, installs the
`recon,serve,dev` extras into the template's CUDA torch, and **verifies the Open3D
CUDA tensor backend** (the thing TSDF needs). If that verification prints a
warning, TSDF will silently fall back to CPU — fix the GPU/template before running
the tiers.

## 3. Configure (optional keys)

```bash
cp soba/deploy/runpod/env.example /workspace/.env && chmod 600 /workspace/.env
# edit /workspace/.env — ANTHROPIC_API_KEY only if you want the Step-8 physics
# VLM; the front-end needs no keys. Never commit it (docs/security/secrets-audit.md).
set -a; . /workspace/.env; set +a
```

To make the pod a queue worker for a deployed API instead of running scenes by
hand, use the last block of `env.example` (`SOBA_QUEUE_URL`, `SOBA_JOBS_DIR`,
`SOBA_WORKER_MODE=real`) and `python -m orchestration.worker`; the wiring and
the cost controls (`SOBA_RUNPOD_DISABLED`, budget, breaker) are in
`docs/runbook.md` §5 and §8.

## 4. Run the pipeline

```bash
cd /workspace/soba
export PYTHONPATH=src

# sanity (no GPU needed)
python3 -m pytest -q tests/scene/test_schema.py tests/reconstruction/test_slam.py

# Replica: ground-truth masks/poses/depth -> no camera, no SAM2/pose error.
# Best first target; coverage is the only quality lever.
python3 scripts/build_replica_bundles.py --scenes office_3 --stride 1 --max-frames 2000 --out bundles
python3 scripts/run_tsdf.py     --bundle bundles/office_3 --out out/office_3.ply
python3 scripts/run_gate.py     --bundle bundles/office_3 --tier 2
python3 scripts/run_assemble.py --bundle bundles/office_3 --tier 2 --out out/scene_office_3

# serve scene.json + the Three.js/Rapier viewer; open the pod's proxied :8000 URL
python3 scripts/serve.py --scene out/scene_office_3 --host 0.0.0.0 --port 8000
```

## 5. What to do next (from `STATUS.md`, the authoritative handoff)

1. **Rebuild a scene with all frames (`--stride 1 --max-frames 2000`).** The
   current bundles use only ~100 of 2000 frames — denser footage is the single
   biggest quality lever (thin chair/table legs survive the TSDF weight
   threshold).
2. **Per-object front-end audit:** for each object track the point count at every
   stage (raw backproject → masked → voxel-downsample → TSDF) and find where
   points are lost. Suspects: the `[400, 8000] mm` depth gate, the `0.005 m`
   voxel size, and `tsdf.extract_triangle_mesh()` using Open3D's default ~3-frame
   weight threshold (drops thin geometry).
3. **Completion is a known dead-end for well-observed objects** — point-completion
   nets output only 8–16k scattered points and *discard* dense real geometry. Keep
   the real mesh and repair holes geometrically (Poisson / pymeshfix); reserve
   learned completion/generation for genuinely sparse objects only.

## Generative serverless endpoint (image-to-3D — TripoSG / Hunyuan3D 2.1)

Separate from the host pod above. This is the "big generative GPU" — a RunPod
**serverless** worker that turns an object crop into a mesh. One endpoint serves
both models; the client (`reconstruction.generative.RunPodEngine`) picks per tier
(fix K1): **tiers 1-2 → TripoSG**, **tiers 3-4 → Hunyuan3D 2.1**.

1. Build a serverless worker: the `docker/pipeline.Dockerfile` image built on
   the `-devel` base (it already installs `runpod`), start command
   `serverless` (= `python deploy/runpod/generative_handler.py`, which reuses
   the exact `LocalGpuEngine` model code), and a network volume at
   `/workspace` populated once with:
   ```bash
   bash deploy/runpod/setup_triposg.sh      # tiers 1-2
   bash deploy/runpod/setup_hunyuan3d.sh    # tiers 3-4 (~10 GB shape; >=16 GB GPU)
   ```
   Endpoint sizing, template env and the live probe are in `docs/runbook.md` §5.4.
2. Point the **host** pipeline at it (in `.env`, see `env.example`):
   ```bash
   export RUNPOD_API_KEY=...  RUNPOD_GEN_ENDPOINT_ID=...
   # RUNPOD_GEN_MODEL is optional — the tier default already selects the model.
   ```
   Absent → the generative band drops (host still runs TSDF/gate). The request/
   response contract (`image_b64`/`cloud_npy_b64` in, `mesh_b64`+`format` out) is
   pinned by `tests/reconstruction/test_generative_handler.py`.

Local cost-saver: on the 8 GB host, TripoSG runs in-process for small scenes; the
full Hunyuan3D 2.1 needs ~10 GB so it belongs on the endpoint (or use the 8 GB
`tencent/Hunyuan3D-2mini` via `SOBA_HUNYUAN_MODEL` locally).

## Cost hygiene

- **Stop the pod when idle** (on-demand bills while running).
- Keep the repo/datasets/checkpoints on the **`/workspace` volume** so restarts are
  cheap.
- The host pod and the (optional) generative serverless endpoint are **billed
  separately** — you don't need both running at once for front-end work.
