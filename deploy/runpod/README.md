# Running the vid2sim-v2 host pipeline on RunPod

This pod runs the **host (NVIDIA) side** of the pipeline: SAM2, RGB-D pose,
Open3D **TSDF fusion (CUDA-only)**, the confidence gate, scene assembly, and the
scene server/viewer. It does **not** need to be the big generative GPU — that's a
separate, optional RunPod *serverless* endpoint for TripoSG/Hunyuan3D.

> The artifacts here (`bootstrap.sh`, `env.example`) make pod setup a single
> command. Renting the pod and pasting your API keys is the part only you can do.

---

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
git clone --depth 1 --branch fix/phase3-pose-and-eval \
  https://github.com/sladojevicm/vid2sim-v2.git
bash vid2sim-v2/deploy/runpod/bootstrap.sh
# optional learned completion as well:  SETUP_POINTR=1 bash .../bootstrap.sh
```

`bootstrap.sh` is idempotent. It clones/updates the repo, installs the
`recon,serve,dev` extras into the template's CUDA torch, and **verifies the Open3D
CUDA tensor backend** (the thing TSDF needs). If that verification prints a
warning, TSDF will silently fall back to CPU — fix the GPU/template before running
the tiers.

## 3. Configure (optional keys)

```bash
cp vid2sim-v2/deploy/runpod/env.example /workspace/.env
# edit /workspace/.env — ANTHROPIC_API_KEY only if you want the Step-8 physics
# VLM; the front-end needs no keys.
set -a; . /workspace/.env; set +a
```

## 4. Run the pipeline

```bash
cd /workspace/vid2sim-v2
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
python3 scripts/serve.py --scene out/scene_office_3 --port 8000
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

## Cost hygiene

- **Stop the pod when idle** (on-demand bills while running).
- Keep the repo/datasets/checkpoints on the **`/workspace` volume** so restarts are
  cheap.
- The host pod and the (optional) generative serverless endpoint are **billed
  separately** — you don't need both running at once for front-end work.
