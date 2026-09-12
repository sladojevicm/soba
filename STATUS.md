# Soba — Status

_Current state as of 2026-09-11. Measured numbers live in `BENCHMARK.md`; the
scene contract lives in `spec/scene.schema.json`; those two are authoritative.
The reasoning behind past decisions lives in dated files under `docs/log/`._

## Current state

- **Paper.** "Room Video to Interactive Physics Simulation" is accepted at ERK 2026
  (Portorož), section SM. Review 1 scored 0 with nine items; review 2 scored +2 with
  three items: define the gate precisely (formula, thresholds, sensitivity), evaluate
  off the orbit trajectories on a real handheld capture, and explain how generated
  objects are placed back with orientation, position and metric scale. Camera-ready
  PDF is due **2026-09-07**. The LaTeX source is not in this repo and was last seen
  at `~/Downloads/erkLaTeX` on the GPU machine.
- **Code.** `master` at the 2026-09-04 commits. The `fix/phase3-pose-and-eval` work
  merged on 2026-07-12; the project was renamed from vid2sim-v2 to Soba on
  2026-07-13; the frontend was rebuilt to Vite + React + TS + Tailwind v4 on
  2026-08-18 with `dist/` committed and `verify_browser.js` passing 8/8.
- **Pipeline.** Every step is built and has run at least once: TUM and Replica
  readers, YOLO + SAM2 (run on TUM fr1/xyz, 100 frames), RGB-D odometry (tier 1) and
  MASt3R (tiers 2–4), observed cloud, TSDF, three-way gate, PatchComplete completion
  with fusion and pymeshfix sealing, TripoSG (local 4060) and Hunyuan3D 2.1 with
  paint (pod) generation, FPFH + ICP placement with class-prior fallback, Claude
  physics with `fill_fraction` (needs `ANTHROPIC_API_KEY`, lookup otherwise), CoACD,
  `scene.json` v2.0, Starlette server, browser viewer.
- **Gate.** Complete bar 90° / 0.35 at all tiers. Keep bar 160/155/150° and 0.85 has
  never been reached on any Replica bundle, so reported routing is completion versus
  generative in practice. No sensitivity study exists yet.
- **Benchmarks.** TUM pose per tier; YCB and ABO physics (lookup measured, Claude
  columns are agent-preview until an API key exists); Replica room accuracy for all
  four tiers on all eight rooms using the `_v2` orbit bundles with ground-truth poses
  and masks. Mean score tier 1 63.0, tier 2 74.9, tier 3 75.4, tier 4 74.1; room_1
  is empty at every tier. Routing ablation (BENCHMARK §4, 2026-09-05): 7 rooms ×
  forced tsdf / completion / generative at tier-2 settings via
  `scripts/ablation_routing.sh` and `scripts/ablation_summary.py`; gated routing
  gives the best surface fidelity, forced generative reproduces tier 1. fr2/xyz
  pose re-measured on all 3665 pairs (BENCHMARK §1).
- **Job API (2026-09-11, `feat/job-api`).** `POST /api/jobs` takes a PerceptionBundle or
  TUM archive and returns a job id; `GET /api/jobs/{id}` reports queued / running:<stage> /
  done / failed; the viewer and scene routes are mirrored under `/jobs/{id}/`. On this box
  the in-process worker runs in `mock` mode (copies `out/scene_test`); `real` and
  `gate-only` spawn `scripts/run_assemble.py` and need the pod. There is **no job or
  output retention policy yet**: uploads and `out/jobs/` grow unbounded (follow-up, not
  blocking).
- **API security (2026-09-12, `feat/security-hardening`).** Bearer-key auth, per-key/per-IP
  rate limiting, a CORS allow-list, security headers, an SSE connection cap and upload
  validation (decompression-bomb, size, manifest, frame-count and depth-dtype checks) live
  in `src/api/security/`; the API stays open by default until `SOBA_API_KEYS` is set (one
  startup warning). Rate-limit buckets are per-process (Redis-backed limiting is a follow-up).
- **Observability.** `scripts/run_assemble.py` emits structured logs (text, or JSON lines
  with `SOBA_LOG_JSON=1`) with per-stage timings and one gate event per object, and writes
  `out/<scene>/run_metrics.json` (schema in `src/telemetry/`) on every run, failures
  included. No `/metrics` endpoint yet (phase B, after the job API).
- **Never built or never run.** A real-sensor scene end to end (TUM through detector,
  SAM2, MASt3R, assembly); live OAK capture; a phone-capture reader; the ScanNet
  reader (stub); a Phase-12 CLI (`scripts/run_assemble.py` is the driver).
- **Known defects.** The server's empty-scene fallback (`src/api/routes/scene.py`, `scene_json`) omits
  `world`, `ground` and `camera_pose` and fails the frozen schema. The eight
  cross-module disagreements are listed in `CLAUDE.md`. TUM world frames are not
  gravity-aligned, so floor snapping is wrong on real-sensor runs.
- **Machines.** This WSL box has no GPU and no data, and its `.venv` lacks the
  `recon` extra: `pytest` here gives 125 pass and 49 fail or error, every one a
  missing-`open3d` import (last full run: 241 pass, 2026-07-06, GPU machine). Data and builds: `~/soba/data` on the
  RTX 4060 machine; `/workspace` on the RunPod pod holds tier 3/4 outputs, and the
  tier 1/2 output folders may be gone with the old pod. The pod bills hourly.

## Durable gotchas (still true)

- Replica: depth PNG is already millimetres; `traj_w_c.txt` is camera-to-world; the
  world is Z-up and is rotated to Y-up on read; intrinsics 1200×680, fx = fy = 600;
  instance id is the track id; `render_config.yaml` needs `yaml.unsafe_load`.
- TSDF: the extrinsic must be `.inv().contiguous()`; the confidence gate is optional;
  `extract_triangle_mesh` drops voxels seen fewer than ~3 times; `CPU:0` works.
- Two numbering systems: pipeline Steps (data flow, SAM2 is Step 2) versus build
  Phases (SAM2 is Phase 4). Talk in Steps.
- Open3D cannot read back its own `.glb`; set `SOBA_DUMP_PLY=1` to inspect a mesh.
- Never run two GPU builds at once on the 4060; the second one dies of OOM.
- `_v2` bundles consume ground-truth poses, so ATE is 0 by construction. Evaluate with
  `--gt-traj /nonexistent` so pose is n/a and the score renormalises.
- Caches: gate results under `<bundle>/.gate_cache`, accepted generations under
  `<bundle>/.gen_cache` keyed by track, model, seed and crop hash; `--reroll` changes
  one track's seed.
- Everything in `docs/log/` predates the rename: read `VID2SIM_*` as `SOBA_*`.

## Log

Dated entries, newest first, are indexed in [`docs/log/README.md`](docs/log/README.md).
Add a new dated file there for anything that changes what is runnable, and update
the current-state block above only when the state itself changes.
