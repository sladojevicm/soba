# CLAUDE.md — Soba

Soba turns a short RGB-D video of a room into a physics-enabled 3D scene that
runs in a plain browser tab with no backend. It is the code behind an ERK 2026
paper; every number in the paper is generated from this repo. Four bounded
contexts talk only through one typed contract, `spec/scene.schema.json`:
**A Perception** (frames, depth, masks → PerceptionBundle), **B Reconstruction**
(poses, per-object TSDF, the confidence gate that routes each object to
keep / complete / regenerate, image-to-3D), **C Scene Assembly** (VLM physics,
mass, CoACD hulls → `scene.json`), **D Presentation** (Three.js + Rapier viewer).
Swapping a model inside a context is a config change, never a refactor.

## Commands — Python (contexts A–C, server, scripts)

Activate the venv first. Outside it there is no `python`, only `python3`;
inside it `python` is Python 3.11. Scripts under `scripts/` import from `src/`
and need `PYTHONPATH=src`, except `serve.py`, `evaluate_scene.py`,
`write_section3.py`, `make_test_scene.py` and `fetch_replica_gt_traj.py`,
which put `src/` on the path themselves.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[recon,serve,dev]"

pytest                                   # pyproject sets pythonpath=src, testpaths=tests

# Replica room, GT masks: bundle -> gate/reconstruct/physics/assemble -> serve
PYTHONPATH=src python scripts/build_replica_bundles.py --scenes office_3 --out bundles
PYTHONPATH=src python scripts/run_assemble.py --bundle bundles/office_3 --tier 2 --out out/scene_office_3
python scripts/serve.py --scene out/scene_office_3          # http://127.0.0.1:8000

PYTHONPATH=src python scripts/run_gate.py --bundle bundles/office_3 --tier 2      # routing only
PYTHONPATH=src python scripts/run_assemble.py --bundle B --tier 2 --out O --gate-only
PYTHONPATH=src python scripts/run_assemble.py --bundle B --tier 2 --out O --force-strategy completion

# custom Replica trajectories, GT evaluation, BENCHMARK §3 tables
PYTHONPATH=src python scripts/render_replica.py render --scene-dir S --out bundles/office_4_v2 --frames 200
python scripts/evaluate_scene.py --scene out/scene_office_3_t2 --room office_3
python scripts/write_section3.py --out-root out

# real Kinect sequence with YOLO + SAM2; TUM pose benchmark
PYTHONPATH=src python scripts/run_tum_detect.py --seq SEQ --out bundles/f1xyz_yolo --max-frames 100
PYTHONPATH=src SOBA_MAST3R_MAX_IMAGES=24 python scripts/bench_tum_pose.py --seq SEQ --bundle B --tiers 1 2

python scripts/make_test_scene.py        # writes out/scene_test, the only scene on this box
```

This WSL box has no GPU and no data. Bundles, GT and real builds live under
`~/projects/soba/data` on the GPU machine or `/workspace` on the RunPod pod
(`deploy/runpod/README.md`). Commands copied from STATUS.md predate the rename:
substitute `SOBA_*` for `VID2SIM_*`.

## Commands — frontend (context D)

```bash
cd frontend
npm install
npm run dev              # HMR at :5173, proxies /scene.json /meshes /hulls /events /eval.json to :8000
                         # run `python scripts/serve.py --scene ...` alongside
                         # http://localhost:5173/kitchen-sink = design-system reference (dev only)
npm run build            # tsc -b && vite build -> dist/  (COMMIT dist/)
npm run preview

# headless regression harness (puppeteer installed OUTSIDE the repo, once)
mkdir -p ~/tmp/pptr && cd ~/tmp/pptr && npm init -y && npm i puppeteer
SOBA_PYTHON=.venv/bin/python NODE_PATH=~/tmp/pptr/node_modules \
  node scripts/verify_browser.js --scene out/scene_test --screenshot /tmp/soba.png
```

## Who owns what

- `src/perception/` — Python — bundle I/O, TUM/Replica readers, YOLO + IoU tracker, crop staging
- `src/reconstruction/` — Python — odometry/MASt3R poses, observed cloud, TSDF, `confidence.py` gate, fusion, generative engines, `icp_align.py`
- `src/scene/` — Python — VLM physics, mass, CoACD, glTF export, ground, `assembler.py` (validates against the schema before writing)
- `src/server.py` — Python — Starlette server: `scene.json`, meshes, hulls, SSE, `eval.json`
- `spec/` — contract — `scene.schema.json` v2.0 + example; `docs/scene-spec.md` is its prose
- `config/` — Python — `pipeline.yaml` (tiers, gates, densities, solidity), COCO class maps
- `scripts/` — Python, plus `verify_browser.js` (Node) — pipeline drivers, benchmarks, Replica tooling
- `tests/` — Python — pytest for contract, readers, gate, assembly, server
- `deploy/runpod/` — shell/Python — pod bootstrap, model setup, serverless handler
- `frontend/src/viewer/` — TypeScript, framework-free — Three.js + Rapier (`SobaViewer`, `physics.ts`)
- `frontend/src/ui/` — React + Tailwind v4 + shadcn — panels, components, `dev/KitchenSink.tsx`
- `frontend/src/store.ts` — Zustand — the only bridge between viewer and React
- `frontend/src/index.css` — the design tokens (`@theme`), the only source of visual values
- `frontend/dist/` — built bundle, committed so the viewer runs with no Node
- `out/` — local outputs; only the `scene_test` fixture exists here
- `BENCHMARK.md` measured results · `STATUS.md` engineering log · `README.md` overview

## Invariants — never break these

1. `spec/scene.schema.json` is a frozen contract. Any change needs the
   maintainer's explicit approval first.
2. `config/pipeline.yaml` is the single source of truth for gate thresholds,
   material densities and solidity factors. Never duplicate a value in code.
3. `BENCHMARK.md` numbers are generated (`scripts/write_section3.py` from
   `eval.json`, benchmark scripts for the rest). Never hand-edit a number.
4. The `_v2` Replica rooms consume ground-truth poses. Their ATE is 0 by
   construction and must never be reported as a pose result; pose numbers
   come from TUM only.
5. Frontend visual values come from the tokens in `frontend/src/index.css`.
   No ad-hoc colours or spacing, no arbitrary Tailwind values, no
   `tailwind.config.js`. Check `/kitchen-sink` before building a component.
   `frontend/dist/` is built output: rebuild it, never edit it.
6. `frontend/CLAUDE.md` holds the frozen physics semantics, the pinned
   `three@0.160.0` / `rapier3d-compat@0.13.1` versions, the "React never
   touches Three/Rapier, viewer never touches DOM" rule and the frozen
   `window.__soba` shape. Read it before touching `frontend/`.
7. This box has no GPU. Never fake a GPU result; say the run needs the pod.

Known places where modules can disagree (do not "fix" one side alone):
- Up axis: Replica poses are rotated to Y-up on read; TUM/odometry/MASt3R world = first camera frame, never re-oriented. `ground.py` and floor snapping assume Y-up.
- Placement uses the RAW TSDF mesh AABB (`assembler.py`); fusion/pymeshfix/Poisson can move the final mesh's centre and bottom. De-overlap and box colliders use the final mesh.
- Two raw clouds: `run_assemble` gates on a `--gate-stride` cloud (also the ICP target and ground input); `tsdf.fuse` re-accumulates every frame for grid sizing.
- `config/pipeline.yaml` `depth:` block is read by nothing; 400/8000 mm live as constants in `observed_cloud.py` and `tsdf.py`; `crop_stage.py` uses depth > 0.
- Class size gates: `icp_align._dims_ok` is strict, `generative._class_dims_ok` allows 0.7x–1.5x.
- `transform.scale` is allowed by the schema, always written 1.0, never read by the viewer.
- PatchComplete runs in the assembler's recentred frame; `_fuse_and_seal` restores world with `+center`.
- `evaluate_scene.py` ignores `source.alignment_method` / `scale_method` and aligns per object translation-only.

## Before you finish — Python

- `source .venv/bin/activate && pytest` passes.
- No edit to `spec/scene.schema.json` without approval; assembled scenes still validate.
- Thresholds, densities, solidity changed only in `config/pipeline.yaml`.
- Benchmark tables regenerated from `eval.json`, not typed in.
- A GPU-dependent change is reported as untested here, with the pod command to run.
- Anything that changes what is runnable gets a dated entry at the top of `STATUS.md`.

## Before you finish — frontend

- `npm run build` succeeds (type-check included) and the new `dist/` is committed.
- `scripts/verify_browser.js` exits 0 and the screenshot is not a black canvas.
- No arbitrary Tailwind values; new values were added to the `@theme` tokens.
- No `three` import in a `.tsx` file; no DOM access inside `viewer/`.
- `window.__soba` shape and the pinned library versions are unchanged.
- Orbiting the camera causes zero React renders.
