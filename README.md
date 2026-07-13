<div align="center">

# 🛋️ SOBA 🎮

### 🎥 *Soba* is Slovenian for "room." Point a depth camera at one. Get a browser physics playground back.

📄 **Headed to ERK 2026 · Portorož 🌊🇸🇮** — same pipeline, now with numbers instead of vibes.

**The grown-up rewrite of the [original VID2SIM](https://github.com/Vector-Space-Moggers/vid2sim)** 🏆 *(formerly known as vid2sim v2 — same repo, better name)*
*(winner of the Epilog Clean Code challenge at DragonHack 2026 — built in 24 h, including a nightclub pause 🪩)*

[![ERK](https://img.shields.io/badge/ERK-2026%20Portoro%C5%BE-blue?style=for-the-badge)](https://erk.fe.uni-lj.si)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org)
[![Open3D](https://img.shields.io/badge/Open3D-TSDF%20%2B%20odometry-E44D26?style=for-the-badge)](https://www.open3d.org)
[![MASt3R](https://img.shields.io/badge/MASt3R-global%20pose-8A2BE2?style=for-the-badge)](https://github.com/naver/mast3r)
[![Three.js](https://img.shields.io/badge/Three.js-Rapier%20WASM-000000?style=for-the-badge&logo=three.js)](https://threejs.org)
[![Benchmarked](https://img.shields.io/badge/BENCHMARK.md-measured%2C%20not%20marketed-2E8B57?style=for-the-badge)](BENCHMARK.md)

</div>

---

## 🚀 The pitch

Digital twins you can actually *poke* are either **💸 expensive** (enterprise RTX + Omniverse), **🛠️ manual** (Blender artisans hand-crafting URDFs), or **🧩 incomplete** (splats are gorgeous but a physics engine can't collide with a vibe).

**Soba** takes a short RGB-D video of a room and produces an interactive, mesh-based, physically-parameterised simulation that runs in a **plain browser tab with zero backend** 🌐. Click a chair. Throw a ball at it. It falls over with a plausible mass, because a VLM looked at it and said *"wooden, mostly hollow, ~6 kg"* 🪑⚖️.

The hackathon original proved it works. **Soba is the version that can defend itself at a conference**: rewritten for clarity, gated by confidence, benchmarked against TUM, Replica, YCB and ABO — and honest about every number. 📊

---

## 🧠 How it works

```
  🎥 RGB-D video (TUM / Replica / OAK capture)
        │
        ▼
  ┌──── 👁️ A · Perception ─────────────────────────┐
  │  frames + depth + masks → PerceptionBundle      │
  │  (YOLO/SAM2 seam, GT masks on datasets)         │
  └──────────────────┬──────────────────────────────┘
                     ▼
  ┌──── 🧊 B · Reconstruction ─────────────────────┐
  │  pose: RGB-D odometry (T1) / MASt3R (T2–4)     │
  │  per-object cloud → TSDF fusion                │
  │  ✨ confidence gate: keep / complete / regen    │
  │  image-to-3D: TripoSG (T2) / Hunyuan3D (T3–4)  │
  └──────────────────┬──────────────────────────────┘
                     ▼
  ┌──── 🏗️ C · Scene Assembly ─────────────────────┐
  │  physics: Claude VLM (material + fill_fraction) │
  │  mass = volume × density × how-hollow-is-it     │
  │  CoACD convex hulls → colliders                 │
  └──────────────────┬──────────────────────────────┘
                     ▼
  ┌──── 🎮 D · Presentation ───────────────────────┐
  │  scene.json v2.0 → Three.js + Rapier WASM       │
  │  60 FPS, no backend, spacebar = rubber ball 🏀  │
  └──────────────────────────────────────────────────┘
```

Four bounded contexts, one typed contract: [`spec/scene.schema.json`](spec/scene.schema.json). No stage knows how any other is implemented — swapping the image-to-3D model is a config line, not a refactor. 🧩

### 🪜 Quality tiers

| Tier | Pose | Mesh source | Physics | Colliders |
|---|---|---|---|---|
| 1 · fast ⚡ | RGB-D odometry | image-to-3D (TripoSG) | lookup table | AABB box |
| 2 · balanced ⚖️ | MASt3R | gate → TSDF / completion / TripoSG | Claude VLM | CoACD ≤8 hulls |
| 3 · quality 💎 | MASt3R | gate → TSDF / completion / Hunyuan3D 2.1 | Claude VLM | CoACD ≤16 hulls |
| 4 · maximum 🔬 | MASt3R | tier 3 + 2 mm voxels | Claude VLM | CoACD ≤32 hulls |

(There used to be an ORB-SLAM3 plan for tier 4. Then we measured MASt3R at 7.6 cm on the hardest TUM sequence and deleted the stub. 🪦 The benchmark giveth, the benchmark taketh away.)

### ✨ The confidence gate (Soba's party trick)

The camera only ever sees the *front* of your couch 🛋️. The gate scores every object's angular coverage + surface completeness and routes it three ways: **keep** the fused TSDF mesh (well observed), **complete** it (fill the unseen back, keep the real geometry), or **regenerate** it entirely from an image crop (barely seen). Real geometry when we have it, hallucination only where we must. 🎯

---

## 📊 The receipts ([BENCHMARK.md](BENCHMARK.md) — all measured, nothing vibes-based)

- **📷 Pose (TUM RGB-D):** MASt3R **1.85–8.8 cm ATE RMSE**; on fast handheld motion it beats frame-to-frame odometry **3.5×** (26.4 → 7.6 cm). Metric scale recovered to within 0.1–7.5 % from sensor depth alone.
- **🏠 Rooms (Replica, 8 scenes vs GT meshes):** scene scores up to **89/100** (room_2, tier 2) — recall, precision, Chamfer, F@5 cm per tier, per room. Tier 4's extra compute buys physics fidelity, not prettier geometry; we say so.
- **⚖️ Physics on perfect meshes (YCB + ABO):** on calibrated furniture classes, **80 % of predicted masses land within 2×** of the real weight. Claude identifies materials at **98 %** on YCB photos — and the ablation shows the new `fill_fraction` field (asking the VLM *how hollow* a thing is) cuts ABO's median mass error from 2.46 → **1.30**.
- **🔧 Colliders:** CoACD decomposed **86/87** meshes without complaint.

Every N/A in that file explains *why* it's N/A instead of inventing a proxy metric. Reviewers, we love you. 💌

---

## 📁 Repo layout

```
.
├── 🐍 src/
│   ├── perception/       # bundle I/O, TUM/Replica readers, detection seam
│   ├── reconstruction/   # odometry/MASt3R, TSDF, confidence gate, gen engines
│   ├── scene/            # VLM physics, mass, CoACD, glTF export, assembler
│   └── server.py         # Starlette server (scene.json + meshes + SSE)
├── 🎮 frontend/           # Three.js + Rapier WASM viewer (vendored, no build step)
├── 📋 spec/               # scene.json v2.0 JSON Schema — the frozen contract
├── ⚙️  config/             # pipeline.yaml: tiers, gates, densities, solidity
├── 🛠️  scripts/            # run_assemble, serve, benchmarks, Replica tooling
├── ☁️  deploy/runpod/      # GPU-side bootstrap (Hunyuan3D / TripoSG / MASt3R)
├── 🧪 tests/              # pytest — contract, readers, gate, assembly, server
└── 📊 BENCHMARK.md        # the numbers behind every claim above
```

---

## ▶️ Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[recon,serve]"

# 1️⃣ Build a perception bundle (e.g. a Replica room, GT masks included)
PYTHONPATH=src python scripts/build_replica_bundles.py --scenes office_3 --out bundles

# 2️⃣ Gate → reconstruct → physics → assemble
PYTHONPATH=src python scripts/run_assemble.py \
    --bundle bundles/office_3 --tier 2 --out out/scene_office_3

# 3️⃣ Serve + play 🎮
python scripts/serve.py --scene out/scene_office_3
# open http://127.0.0.1:8000 — click furniture, drag it, spacebar for ball
```

No GPU? Tier 1–2 completion falls back gracefully and generative objects wait politely. With a GPU (or a RunPod pod — see [`deploy/runpod/`](deploy/runpod)), the full TripoSG/Hunyuan3D path lights up with **zero code changes**. 🔌

---

## 📖 More

- 📊 [BENCHMARK.md](BENCHMARK.md) — TUM / Replica / YCB / ABO, per stage, per tier
- 📋 [Scene spec](docs/scene-spec.md) — the `scene.json` v2.0 contract
- 🗒️ [STATUS.md](STATUS.md) — running engineering log (brutally honest)
- 🐉 [The 24-hour original](https://github.com/Vector-Space-Moggers/vid2sim) — where it all began

---

<div align="center">

**🌊 See you at ERK 2026 in Portorož — bring a room, leave with a simulation. 🇸🇮**

*Built on the bones of a hackathon dragon 🐉, raised on benchmarks 📊.*

</div>
