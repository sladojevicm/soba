# Soba — Status

_Current state as of 2026-09-14. Measured numbers live in `BENCHMARK.md`; the
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
- **RunPod orchestration (2026-09-12, `feat/runpod-orchestration`).** `RunPodEngine` retries
  transient failures with backoff, polls `/run` + `/status/{id}`, honours a per-endpoint circuit
  breaker and a per-job budget from the now-live `config/pipeline.yaml` `runpod:` block, refuses
  non-https URL overrides, and `SOBA_RUNPOD_DISABLED=1` drops every generative object unsent; a
  RunPod failure drops one object, never the run. `SOBA_QUEUE_URL=redis://` swaps in a Redis
  `JobQueue`; `python -m orchestration.worker` consumes it and writes `out/jobs/<id>/scene/cost.json`.
  Verified only against a fake endpoint on this box; the real endpoint path needs the pod.
- **Observability.** `scripts/run_assemble.py` emits structured logs (text, or JSON lines
  with `SOBA_LOG_JSON=1`) with per-stage timings and one gate event per object, and writes
  `out/<scene>/run_metrics.json` (schema in `src/telemetry/`) on every run, failures
  included. The API serves `GET /metrics` (Prometheus text, optional `telemetry` extra,
  501 without it): request count/latency by route, job-state gauges, and gate / stage /
  drop / RunPod counters ingested from each finished job's `run_metrics.json`; every
  request and job-state transition is logged with the job id.
- **Load testing (2026-09-13, `feat/load-testing`).** `make loadtest` runs k6 scenarios
  (upload burst, status polling, scene fetch, SSE, mixed) plus a stdlib SSE probe against the
  compose stack in open and keyed mode and writes `loadtest/results/<stamp>/`; the recorded
  run is in `docs/loadtest.md`. Those numbers are API-layer throughput with the mock worker
  on CPU, never pipeline or GPU throughput.
- **API docs (2026-09-13, `feat/api-docs`).** `spec/openapi.yaml` (OpenAPI 3.1, scene body `$ref`s
  the frozen schema) is served at `GET /api/openapi.json` and rendered at `GET /api/docs` (Redoc,
  loads its bundle from a CDN in the browser); `tests/api/test_openapi.py` fails when a route and
  the spec disagree. Prose in `docs/api.md`.
- **Never built or never run.** A real-sensor scene end to end (TUM through detector,
  SAM2, MASt3R, assembly); live OAK capture; a phone-capture reader (Step 1 building
  blocks exist on `feat/rgb-video-step1`, PR #16, unmerged and parked); the ScanNet
  reader (stub); a Phase-12 CLI (`scripts/run_assemble.py` is the driver). **Now run at
  least once on a GPU (2026-09-16, RunPod 4090):** the job API in real mode, the Redis
  queue + external worker, TripoSG generation through the job API, Open3D CUDA TSDF,
  coacd hulls, all on a Replica GT-pose bundle. Still never run on a GPU: MASt3R via
  the job path (setup script written, unexercised), the RunPod serverless endpoint,
  the compose `gpu` profile, Hunyuan3D tiers 3–4, VLM physics through the job API
  (both pod runs were lookup-only: no `ANTHROPIC_API_KEY` set).
- **Known defects.** The server's empty-scene fallback (`src/api/routes/scene.py`, `scene_json`) omits
  `world`, `ground` and `camera_pose` and fails the frozen schema. The eight
  cross-module disagreements are listed in `CLAUDE.md`. TUM world frames are not
  gravity-aligned, so floor snapping is wrong on real-sensor runs.
- **Deployment runbook (2026-09-14, `docs/deployment-runbook`).** `docs/runbook.md` is the
  operator page: images and tag scheme (no registry push exists yet), env matrix and
  secrets, compose / two-host / GPU-worker deploy, RunPod serverless endpoint, the three
  pre-deploy gates (Open3D CUDA `check`, model pins, CI), smoke test, cost controls, alert
  rules, backup, rollback, incident checklist, release flow. Executed here: API image build,
  compose `cpu` smoke (upload → done → `verify_browser.js` 8/8 on the job URL), `/metrics`,
  `SMOKE=1 make loadtest` (all pass); every GPU / RunPod / registry step is marked not
  executed here. Compose `worker-gpu` now mounts `./out` at `/data` like `api` (real jobs
  failed at `validating` before); the pipeline image still runs as root (flagged).
- **Packaging.** `docker/` (API, pipeline, frontend-check images), compose and
  `.github/workflows/ci.yml` exist (2026-09-11); the frontend bundle is served by
  the API image, CDN offload is deferred until there is real traffic.
- **GPU validation run 1 (2026-09-16, RunPod RTX 4090, `docs/gpu-validation.md`).** First real-GPU
  pass over the service layer: Open3D CUDA tensor backend **PASS** on the pod's pip wheel
  (0.19.0), so TSDF runs on the GPU; a Replica `office_3` smoke bundle (100 frames) went
  through `POST /api/jobs` → real worker → done in 225 s, and again through Redis +
  `python -m orchestration.worker` in 227 s (4 objects; gate completion=4 / generative=10,
  all 10 generative dropped as `engine_declined` because TripoSG was not set up); 503 of
  505 tests pass on the pod (2 failures under review). Full record:
  `docs/log/2026-09-16-gpu-validation-run1.md`. **Run 2** the same day with TripoSG set up
  (`docs/log/2026-09-16-gpu-validation-run2.md`): 10 objects (4 completion + 6 generative,
  4 declined by the engine) in 760 s, coacd hull colliders, TripoSG commit recorded in
  `docs/model-pins.md`. Not yet run on a GPU: the RunPod serverless endpoint, the compose
  `gpu` profile, Hunyuan3D (tiers 3–4). Two pytest failures on the pod still unidentified.
- **Machines.** This WSL box has no GPU and no data, and its `.venv` lacks the
  `recon` extra: `pytest` here gives 125 pass and 49 fail or error, every one a
  missing-`open3d` import (last full run: 241 pass, 2026-07-06, GPU machine). Data and builds: `~/soba/data` on the
  RTX 4060 machine; `/workspace` on the RunPod pod holds tier 3/4 outputs, and the
  tier 1/2 output folders may be gone with the old pod. The pod bills hourly.

## Known limitations (2026-09-12, updated 2026-09-16)

- **Bearer-only auth blocks the browser viewer once `SOBA_API_KEYS` is set.** A browser
  cannot attach an `Authorization` header to `/jobs/{id}/`, so the viewer needs a
  header-injecting proxy in front of it. A cookie/session scheme is a future follow-up.
- **`/metrics` sits outside the protected prefixes** (`/api/*`, `/jobs/*`); put it behind
  the reverse proxy or a network ACL until it is gated.
- **Rate limiting is per-process, not Redis-shared.** Several API replicas each keep
  their own buckets, so the effective limit scales with the replica count.
- **No model version pins exist for MASt3R, TripoSG, Hunyuan3D or PatchComplete.**
  No git commit, checkpoint sha256 or HuggingFace revision is recorded anywhere in the
  repo, so BENCHMARK.md numbers are not reproducible from the repo alone. Recovery
  commands for the GPU machine and the pod volume are in `docs/model-pins.md`. The
  2026-09-16 pod run recorded library versions only (fresh volume, no model checkouts).
- ~~`coacd` missing from the extras~~ — fixed 2026-09-16: it is in the `recon` extra now.
  Before that the first pod run had no coacd (bootstrap installs extras only), so its
  scenes shipped single-hull colliders.

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
