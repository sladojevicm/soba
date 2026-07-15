# Soba

*Soba* (Slovenian for "room") is a pipeline that converts a short RGB-D video of a room into an interactive, physics-enabled 3D scene that runs in a plain browser tab with no backend.

It is a ground-up rewrite of [vid2sim](https://github.com/Vector-Space-Moggers/vid2sim), which was built in 24 hours at DragonHack 2026 and won the Epilog Clean Code challenge there. This repository accompanies a paper submitted to ERK 2026 (Portorož); the evaluation behind every claim below is in [BENCHMARK.md](BENCHMARK.md).

## Motivation

Existing routes to an interactive replica of a real room are either expensive (enterprise GPU + simulation platforms), manual (hand-modelled assets and physics authoring), or incomplete (radiance-field and splat reconstructions render well but provide no per-object collision geometry or physical parameters).

Soba takes a short RGB-D video and produces a mesh-based, physically parameterised simulation: each object is a separate rigid body with an estimated mass and material, assigned by a vision-language model from an image crop, and convex collision hulls. The scene runs at interactive rates in the browser; objects can be selected, dragged, and knocked over.

## How it works

```
  RGB-D video (TUM / Replica / OAK capture)
        |
        v
  +---- A - Perception -----------------------------+
  |  frames + depth + masks -> PerceptionBundle     |
  |  (YOLO/SAM2 seam, GT masks on datasets)         |
  +------------------+------------------------------+
                     v
  +---- B - Reconstruction -------------------------+
  |  pose: RGB-D odometry (T1) / MASt3R (T2-4)      |
  |  per-object cloud -> TSDF fusion                |
  |  confidence gate: keep / complete / regenerate  |
  |  image-to-3D: TripoSG (T2) / Hunyuan3D (T3-4)   |
  +------------------+------------------------------+
                     v
  +---- C - Scene Assembly -------------------------+
  |  physics: Claude VLM (material + fill_fraction) |
  |  mass = volume x density x fill fraction        |
  |  CoACD convex hulls -> colliders                |
  +------------------+------------------------------+
                     v
  +---- D - Presentation ---------------------------+
  |  scene.json v2.0 -> Three.js + Rapier WASM      |
  |  real-time, no backend                          |
  +-------------------------------------------------+
```

Four bounded contexts communicate through one typed contract, [`spec/scene.schema.json`](spec/scene.schema.json). No stage depends on how any other is implemented — swapping the image-to-3D model is a configuration change, not a refactor.

### Quality tiers

| Tier | Pose | Mesh source | Physics | Colliders |
|---|---|---|---|---|
| 1 · fast | RGB-D odometry | image-to-3D (TripoSG) | lookup table | AABB box |
| 2 · balanced | MASt3R | gate → TSDF / completion / TripoSG | Claude VLM | CoACD ≤8 hulls |
| 3 · quality | MASt3R | gate → TSDF / completion / Hunyuan3D 2.1 | Claude VLM | CoACD ≤16 hulls |
| 4 · maximum | MASt3R | tier 3 + 2 mm voxels | Claude VLM | CoACD ≤32 hulls |

An ORB-SLAM3 pose path was originally planned for tier 4 and dropped after measurement: MASt3R already reaches 7.6 cm ATE on the hardest TUM sequence, leaving loop closure without a demonstrated benefit.

### The confidence gate

A camera typically sees only part of each object. The gate scores every object's angular coverage and surface completeness and routes it one of three ways: **keep** the fused TSDF mesh (well observed), **complete** it (fill the unseen regions while keeping the measured geometry), or **regenerate** it from an image crop (poorly observed). Measured geometry is used wherever it is trustworthy; generative models step in only where observation ends.

## Evaluation

All numbers below are measured; the full methodology, per-tier and per-room tables, and known limitations are in [BENCHMARK.md](BENCHMARK.md). Where a metric is not applicable, that file states why rather than substituting a proxy.

- **Pose (TUM RGB-D):** MASt3R reaches 1.85–8.8 cm ATE RMSE; on fast handheld motion it improves on frame-to-frame odometry by 3.5× (26.4 → 7.6 cm). Metric scale is recovered to within 0.1–7.5 % from sensor depth alone.
- **Scene reconstruction (Replica, 8 rooms vs. ground-truth meshes):** recall, precision, Chamfer distance, and F-score at 5 cm per tier and per room; scene scores up to 89/100 (room_2, tier 2). Tier 4's extra compute improves physics fidelity but not the surface-accuracy metrics.
- **Physical properties on ground-truth meshes (YCB + ABO):** on calibrated furniture classes, 80 % of predicted masses fall within 2× of the real weight. The VLM identifies materials at 98 % accuracy on YCB, and the `fill_fraction` ablation (asking the model how hollow an object is) reduces ABO's median mass error from 2.46 to 1.30.
- **Colliders:** CoACD successfully decomposed 86 of 87 meshes.

## Repository layout

```
.
├── src/
│   ├── perception/       # bundle I/O, TUM/Replica readers, detection seam
│   ├── reconstruction/   # odometry/MASt3R, TSDF, confidence gate, gen engines
│   ├── scene/            # VLM physics, mass, CoACD, glTF export, assembler
│   └── server.py         # Starlette server (scene.json + meshes + SSE)
├── frontend/             # Three.js + Rapier WASM viewer (vendored, no build step)
├── spec/                 # scene.json v2.0 JSON Schema — the frozen contract
├── config/               # pipeline.yaml: tiers, gates, densities, solidity
├── scripts/              # run_assemble, serve, benchmarks, Replica tooling
├── deploy/runpod/        # GPU-side bootstrap (Hunyuan3D / TripoSG / MASt3R)
├── tests/                # pytest — contract, readers, gate, assembly, server
└── BENCHMARK.md          # measured results behind the claims above
```

## Running

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[recon,serve]"

# 1. Build a perception bundle (e.g. a Replica room, GT masks included)
PYTHONPATH=src python scripts/build_replica_bundles.py --scenes office_3 --out bundles

# 2. Gate -> reconstruct -> physics -> assemble
PYTHONPATH=src python scripts/run_assemble.py \
    --bundle bundles/office_3 --tier 2 --out out/scene_office_3

# 3. Serve and interact
python scripts/serve.py --scene out/scene_office_3
# open http://127.0.0.1:8000 — click furniture to select, drag to push,
# spacebar drops a ball
```

Without a GPU, tier 1–2 completion falls back to geometric repair and generative objects are deferred. With a local GPU or a RunPod instance (see [`deploy/runpod/`](deploy/runpod)), the full TripoSG/Hunyuan3D path is enabled with no code changes.

## Documentation

- [BENCHMARK.md](BENCHMARK.md) — TUM / Replica / YCB / ABO results, per stage and per tier
- [Scene spec](docs/scene-spec.md) — the `scene.json` v2.0 contract
- [STATUS.md](STATUS.md) — running engineering log
- [Original vid2sim](https://github.com/Vector-Space-Moggers/vid2sim) — the hackathon predecessor
