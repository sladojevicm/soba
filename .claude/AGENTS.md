# AGENTS.md — Soba production hardening

Read `CLAUDE.md` first. Every agent below inherits its invariants (frozen schema, generated
BENCHMARK numbers, thresholds only in `config/pipeline.yaml`, no GPU on this box, `dist/` is
built not edited) and the deny rules in `.claude/settings.json` (no `rm -r`, no `.env` reads,
no edits to `spec/scene.schema.json` or `BENCHMARK.md`).

Decisions already made by the maintainer (2026-09-11), do not re-litigate:

1. Uploads are **PerceptionBundle or TUM archives** (zip/tar), never plain MP4. There is no
   depth-from-video path; that question belongs to the LiDAR ADR.
2. **No separate frontend container.** The committed `frontend/dist/` is served by the API
   image. Moving the bundle to a CDN is a deferred optimisation, recorded in `STATUS.md`, to
   revisit once there is real traffic.
3. **CI must prove whether Open3D imports on a plain Ubuntu runner** (not WSL) with `libgl1`
   and `libglib2.0-0` installed, before `ci.yml` is written. If it does, CI runs the full
   241-test suite. If it does not, GPU-only tests get a marker and the report says exactly
   which imports are missing.
4. `develop` is the integration branch, created from `origin/master@0c177ae`.

## Git flow

- Every agent branches from `develop` and opens a PR back into `develop`. `develop` → `master`
  only on a tagged release.
- Branch names below are fixed. One agent, one branch, one PR. Rebase on `develop` before the PR.
- `fix/phase3-pose-and-eval` is fully merged (PR #5) and deleted.

## Coordinator prerequisites (done before Wave 0)

1. `git checkout master && git pull`, `git checkout -b develop && git push -u origin develop`.
2. Flip `deploy/runpod/bootstrap.sh` `REPO_BRANCH` default to `master`.
3. Delete the merged feature branch on origin.

## Shared-file rules (merge-conflict policy)

- `src/server.py` is refactored ONCE by `job-api-agent` into a thin `create_app()` that composes
  modules under `src/api/`. Later agents add a new module plus a one-line registration; nobody
  else rewrites `server.py`. Existing routes stay byte-for-byte compatible so `verify_browser.js`
  and `tests/test_server.py` keep passing.
- `config/pipeline.yaml`: only `runpod-orchestration-agent` edits the `runpod:` block. No agent
  touches `tiers:`, `density:`, `solidity:`, `class_gates:`.
- `pyproject.toml`: each agent adds its own optional-dependency extra (`api`, `worker`,
  `telemetry`, `loadtest`) in a separate small commit to keep conflicts trivial.
- `STATUS.md`: update the current-state block only when what is runnable changes; add a dated
  file under `docs/log/` for the reasoning. Several agents below are told to add one specific
  line to STATUS.md; keep those lines short and put the reasoning in `docs/log/`.
- A GPU-dependent change is reported as untested here, with the pod command to run it
  (CLAUDE.md invariant 7). Never fabricate a GPU result.

## Agents

### 1. job-api-agent  — Wave 0
- **Responsibility.** The ingest/status service that everything else targets: upload a
  PerceptionBundle or TUM archive → job id; job state machine (queued / running:<stage> / done /
  failed); job-scoped scene serving; a `JobQueue` interface with an in-process + sqlite default.
  Ships a local worker stub that runs `scripts/run_assemble.py` as a subprocess (real on the
  pod, `--gate-only` or a mock on CPU) so the status flow is end-to-end testable without RunPod.
- **Endpoints.** `POST /api/jobs` (multipart: archive, tier), `GET /api/jobs/{id}`,
  `DELETE /api/jobs/{id}`, `GET /jobs/{id}/` (viewer), `GET /jobs/{id}/scene.json`,
  `/jobs/{id}/meshes/{id}.glb`, `/jobs/{id}/hulls/{stem}.glb`, `/jobs/{id}/events`,
  `/jobs/{id}/eval.json`. The legacy root routes remain for single-scene mode.
- **Reads.** `src/server.py`, `tests/test_server.py`, `src/perception/bundle.py`
  (`PerceptionBundle.open` validates the archive), `scripts/run_assemble.py`,
  `frontend/src/viewer/loaders.ts` + `store.ts` (how the viewer resolves URLs).
- **Modifies.** New `src/api/` (`app.py`, `routes/jobs.py`, `routes/scene.py`, `jobs/store.py`,
  `jobs/queue.py`, `jobs/worker_local.py`), `src/server.py` (thin composition only), new
  `tests/api/`, `pyproject.toml` (`api` extra: python-multipart, aiofiles),
  `frontend/src/viewer/loaders.ts` (base-path aware fetches, the ONLY context-D touch; rebuild
  and commit `dist/`; `verify_browser.js` must pass on both `/` and `/jobs/<id>/`).
- **STATUS.md.** Add one line to the current-state block: there is **no job or output retention
  policy yet** (uploads and `out/` grow unbounded). Follow-up item, not blocking.
- **Depends on.** Nothing. Everyone in Wave 1+ depends on it.
- **Bounded context.** New, orthogonal "E — Service layer". Touches D minimally.
- **Branch.** `feat/job-api`
- **Done when.** `pytest tests/api` passes; upload → status `done` with the stub worker on this
  box; `verify_browser.js --scene out/scene_test` passes via a job URL; the route list is handed
  to `api-docs-agent`.

### 2. docker-agent  — Wave 0
- **Responsibility.** Images, compose, CI.
  - `docker/api.Dockerfile`: `python:3.11-slim`, `pip install -e ".[serve,api]"`, serves the
    committed `frontend/dist/`, non-root user, `HEALTHCHECK` on `/scene.json`, uvicorn entry.
  - `docker/pipeline.Dockerfile`: CUDA + torch base mirroring `deploy/runpod/bootstrap.sh`
    (`[recon,serve,dev]`, libgl1/libglib2.0-0, Open3D CUDA check as a build-time smoke test);
    two entrypoints: worker (`src/orchestration/worker.py`, added by agent 3) and RunPod
    serverless (`deploy/runpod/generative_handler.py`). Weights are NOT baked in; they are
    mounted or pulled by the existing `setup_triposg.sh` / `setup_hunyuan3d.sh`.
  - **Pinned model versions.** Confirm and document, in the pipeline Dockerfile and in
    `docs/model-pins.md`, the exact versions and checkpoints of YOLO, SAM2, MASt3R, TripoSG and
    Hunyuan3D that produced the `BENCHMARK.md` numbers: git commit or release tag of each repo,
    checkpoint file name and sha256, HuggingFace revision where applicable, plus torch/CUDA/
    Open3D versions. Source them from `deploy/runpod/setup_*.sh`, `src/reconstruction/`
    defaults, `docs/log/`, and `BENCHMARK.md`'s header. Where a pin cannot be recovered from
    the repo, say so explicitly rather than guessing; that gap is a finding.
  - `docker/frontend-check.Dockerfile` (multi-stage, node:20): runs `npm ci && npm run build`
    and fails if the result differs from the committed `dist/` (CI guard for invariants 5/6).
    No runtime frontend container (maintainer decision 2). Add one line to the `STATUS.md`
    current-state block: frontend bundle served by the API image; CDN offload deferred until
    there is real traffic.
  - **Open3D CI probe (maintainer decision 3), before writing `ci.yml`.** Docker is not
    installed on this box, so the probe runs on GitHub Actions: push a minimal workflow on the
    feature branch (`ubuntu-latest`, Python 3.11, `apt-get install libgl1 libglib2.0-0`,
    `pip install -e ".[recon,serve,dev]"`, `python -c "import open3d, cv2, trimesh"`, then
    `pytest -q`), trigger it with `gh workflow run`, and read the log with `gh run view --log`.
    If Open3D imports and the full suite passes, `ci.yml` runs the full suite. If not, add a
    `gpu` / `open3d` pytest marker, skip those in CI, and report exactly which imports or tests
    fail and why.
  - `docker-compose.yml`: `api`, `redis`, `worker` (CPU profile using the stub worker),
    optional `gpu` profile. `.dockerignore` (out/, data/, bundles/, node_modules, .venv, .git).
  - `.github/workflows/ci.yml`: pytest per the probe result, ruff, image builds,
    frontend-check. `Makefile`: `build`, `up`, `test`, `loadtest` targets.
- **Reads.** `pyproject.toml`, `deploy/runpod/bootstrap.sh`, `deploy/runpod/setup_*.sh`,
  `deploy/runpod/README.md`, `frontend/package.json`, `scripts/verify_browser.js`,
  `.gitignore`, `src/reconstruction/slam.py`, `generative.py`, `sam2_refine.py`,
  `src/perception/detect.py`, `BENCHMARK.md` (read only), `docs/log/`.
- **Modifies.** New `docker/`, `docker-compose.yml`, `.dockerignore`, `.github/workflows/`,
  `Makefile`, `docs/model-pins.md`; `deploy/runpod/README.md` (point at the image);
  `STATUS.md` (one line, see above).
- **Depends on.** Nothing for the images. Compose wiring of `api`/`worker` services is
  finalised after `job-api-agent` merges (soft dependency; stub the services until then).
- **Bounded context.** Orthogonal (devops).
- **Branch.** `feat/docker`
- **Done when.** Images build in CI; the Open3D probe result is documented; `docs/model-pins.md`
  lists every pin or names the gap; `frontend-check` reproduces `dist/` byte-for-byte.

### 3. runpod-orchestration-agent  — Wave 1
- **Responsibility.** Queue consumer + hardened RunPod client + cost guardrails.
  - `src/orchestration/worker.py`: consumes `JobQueue`, runs the pipeline stages for a job (via
    the same code path as `scripts/run_assemble.py`), reports stage transitions to the job
    store. Redis implementation of the `job-api` queue interface.
  - `RunPodEngine` (`src/reconstruction/generative.py:578-747`): retry with exponential backoff
    + jitter on transient failures (timeouts, 429, 5xx, `IN_QUEUE` stalls); switch long jobs to
    `/run` + status polling instead of `/runsync`; honour the currently-dead
    `config/pipeline.yaml` `runpod:` block (`request_timeout_s`, `circuit_breaker`); per-job
    budget (max generative calls, max GPU-seconds, estimated USD cap) and a global kill switch
    (`SOBA_RUNPOD_DISABLED=1`); on budget exhaustion or open breaker, drop the object via the
    existing `None` path instead of raising.
  - `scripts/run_assemble.py:254`: wrap `engine.regenerate` so one RunPod failure no longer
    aborts the whole run (record the drop, continue).
  - Cost accounting record per job (calls, seconds, est. USD) handed to observability.
- **Reads.** `src/reconstruction/generative.py`, `deploy/runpod/generative_handler.py`,
  `tests/reconstruction/test_generative_handler.py`, `config/pipeline.yaml` (`runpod:` block),
  `deploy/runpod/env.example`, `src/api/jobs/queue.py` (from agent 1).
- **Modifies.** New `src/orchestration/`, `src/reconstruction/generative.py` (RunPodEngine
  only), `scripts/run_assemble.py` (error handling around the engine call only),
  `config/pipeline.yaml` (`runpod:` block only; thresholds live here, never in code),
  `deploy/runpod/env.example`, `pyproject.toml` (`worker` extra: redis), tests with a mocked
  `urllib` or fake endpoint (`SOBA_RUNPOD_URL` already supports pointing at a local server).
- **Depends on.** `job-api-agent` (queue + job store contract).
- **Bounded context.** B (Reconstruction) for the engine changes; orthogonal for the worker.
- **Branch.** `feat/runpod-orchestration`
- **Done when.** Unit tests cover retry/backoff/breaker/budget with a fake endpoint; a job
  submitted on this box reaches `done` with generative objects dropped (no key) and the run
  is NOT aborted; the real endpoint path is reported as untested here with the pod command.

### 4. observability-agent  — Wave 0 (phase A) + Wave 1 (phase B)
- **Responsibility.** Structured logging, per-stage timing, gate-decision tracking, metrics.
  - Phase A (no dependency): `src/telemetry/` — JSON log formatter over stdlib `logging`, one
    `configure_logging()` used by every entrypoint (today only `run_assemble.py:100` configures
    it); `stage_timer(name)` context manager; instrument the stage sequence in
    `scripts/run_assemble.py` (poses → engine → gate cache → observed cloud → gate → generation
    → TSDF → assemble → eval) and `src/scene/assembler.py` (`assemble`, `_assemble_object`,
    VLM, CoACD); emit one gate event per object at the `cf.gate_object` call site
    (`run_assemble.py:184`) with `strategy`, `angular_coverage_deg`, `completeness_ratio`,
    `tier`, `track_id`, `class`; write `out/<scene>/run_metrics.json` (stage durations, gate
    distribution keep/complete/regenerate, drops, RunPod calls/seconds/USD when present).
    Replace `print` with logging ONLY in `run_assemble.py` and `assembler.py`; benchmark
    scripts keep their console output.
  - Phase B (after job-api): `/metrics` (Prometheus text format, `prometheus_client` optional
    extra) on the API — request latency/count, job state gauges, gate-distribution counters
    fed from `run_metrics.json`; request-logging middleware with job id correlation; job
    state transitions logged by the worker.
- **Reads.** `scripts/run_assemble.py`, `src/scene/assembler.py`,
  `src/reconstruction/confidence.py` (gate dict shape, lines 204-266), `gate_cache.py`,
  `generative.py` (existing `log` calls), `src/api/` (phase B).
- **Modifies.** New `src/telemetry/`, `scripts/run_assemble.py`, `src/scene/assembler.py`,
  `src/api/routes/metrics.py` + middleware registration (phase B), `pyproject.toml`
  (`telemetry` extra), tests.
- **Rules.** Never recompute gate metrics; record what `gate_object` returned. Never edit
  thresholds. `BENCHMARK.md` untouched (metrics files are per-run, not benchmark tables).
- **Depends on.** Phase A: nothing. Phase B: `job-api-agent`.
- **Bounded context.** Cuts across B, C and the service layer.
- **Branch.** `feat/observability`
- **Done when.** `run_assemble.py` on a synthetic bundle emits JSON logs with stage durations
  and a gate distribution; `run_metrics.json` validates against a small schema in tests;
  `/metrics` exposes the counters.

### 5. security-agent  — Wave 0 (phase A) + Wave 1 (phase B)
- **Responsibility.**
  - Phase A (no dependency): secrets audit → `docs/security/secrets-audit.md` (inventory of
    every env var by module, which are secrets, how each is read, that none are hardcoded; do
    this from code and `.gitignore`, NEVER by reading `.env*`); add root `.env.example`
    (gitignore already whitelists it); `pip-audit` + `npm audit` report; pre-commit with
    `gitleaks` + ruff.
  - Phase B (after job-api): upload validation (size cap, MIME + magic bytes, zip-slip / path
    traversal guard on archive extraction, `manifest.json` validated through
    `PerceptionBundle.from_dict`, frame-count and depth-dtype checks, extend the existing
    `_ID_RE` allow-list style from `server.py`); API auth (bearer API keys from env,
    constant-time compare, per-key identity); rate limiting middleware (token bucket per key
    and per IP, limits configurable, a bypass key for load testing); CORS allow-list;
    security headers; uvicorn body-size limits; confirm non-root in `docker/api.Dockerfile`
    (coordinate with docker-agent).
- **Reads.** `src/server.py`, `src/api/` (phase B), `src/perception/bundle.py`, `.gitignore`,
  `deploy/runpod/env.example`, `src/reconstruction/generative.py:1189-1231` (key handling),
  `src/scene/vlm_claude.py:186-205`.
- **Modifies.** New `docs/security/`, `.env.example`, `.pre-commit-config.yaml`,
  `src/api/security/` (`auth.py`, `ratelimit.py`, `upload_validation.py`) + registration,
  tests in `tests/api/`.
- **Depends on.** Phase A: nothing. Phase B: `job-api-agent`.
- **Bounded context.** Orthogonal (cuts across all layers).
- **Branch.** `feat/security-hardening`
- **Done when.** Audit doc reviewed; malformed/oversized/traversal archives rejected with
  tests; unauthenticated requests 401; rate-limit 429 tested; `gitleaks` clean in CI.

### 6. api-docs-agent  — Wave 2
- **Responsibility.** `spec/openapi.yaml` (hand-authored; Starlette has no generator) covering
  jobs, status, job-scoped scene routes, `/events` (documented as text/event-stream),
  `/metrics`, auth scheme, error envelope; `$ref` to `spec/scene.schema.json` for the scene
  body; serve `/api/openapi.json` + a static Redoc page; `docs/api.md`; a contract test
  (`schemathesis` or a route-vs-spec assertion in `tests/api/test_openapi.py`) so the spec
  cannot drift from the routes.
- **Reads.** `src/api/` (routes, auth), `spec/scene.schema.json` (read only, deny rule),
  `docs/scene-spec.md`.
- **Modifies.** New `spec/openapi.yaml` (allowed: deny rules cover only `scene.schema.json`),
  `docs/api.md`, `src/api/routes/docs.py`, `tests/api/test_openapi.py`.
- **Depends on.** `job-api-agent` + `security-agent` phase B (auth scheme + 401/429 responses)
  + `observability-agent` phase B (`/metrics`).
- **Bounded context.** Orthogonal.
- **Branch.** `feat/api-docs`
- **Done when.** Contract test green; every route in `create_app()` appears in the spec.

### 7. load-test-agent  — Wave 2
- **Responsibility.** `loadtest/` with k6 scenarios: upload burst, status polling, scene fetch
  + mesh/hull downloads, N concurrent SSE clients, mixed; thresholds (p95 latency, error
  rate); runs against `docker compose` with the CPU stub worker and a fake RunPod endpoint
  container (`SOBA_RUNPOD_URL` override already exists); `make loadtest`; a manually
  triggered CI job; a results template in `docs/loadtest.md`.
- **Reads.** `docker-compose.yml`, `src/api/`, `spec/openapi.yaml`,
  `src/api/security/ratelimit.py` (needs the bypass key).
- **Modifies.** New `loadtest/`, `docs/loadtest.md`, `Makefile` target, compose `loadtest`
  profile, CI workflow (manual dispatch).
- **Rules.** Invariant 7: results are API-layer throughput with a mock worker on CPU. Never
  present them as pipeline or GPU throughput; say so in the report template.
- **Depends on.** `docker-agent`, `job-api-agent`, `security-agent` phase B,
  `runpod-orchestration-agent` (for the worker + fake endpoint path).
- **Bounded context.** Orthogonal.
- **Branch.** `feat/load-testing`
- **Done when.** `make loadtest` runs green against compose with documented thresholds.

### 8. runbook-agent  — Wave 3 (last)
- **Responsibility.** `docs/runbook.md`: build/push images; deploy API + worker; create the
  RunPod serverless endpoint from the pipeline image; secrets provisioning (from the security
  audit's env matrix); scaling; cost controls + kill switch; dashboards/alerts from
  `/metrics`; backup of the job store and `out/`; rollback; incident checklist; smoke test
  (`verify_browser.js` against a deployed job); release flow `develop` → `master`. Every
  command in the runbook is executed once on this box where possible. Update
  `deploy/runpod/README.md` to point at it and drop the stale branch clone instructions.
- **Reads.** Everything merged into `develop` by agents 1-7, `deploy/runpod/README.md`,
  `STATUS.md`.
- **Modifies.** New `docs/runbook.md`, `deploy/runpod/README.md`, `STATUS.md` current-state
  block, a dated `docs/log/` entry.
- **Depends on.** All of 1-7 merged.
- **Bounded context.** Orthogonal.
- **Branch.** `docs/deployment-runbook`

## Conditional track (NOT part of the committed scope; never sequenced as equal to 1-7)

### lidar-feasibility-agent  — may run any time, off the critical path
- **Only job.** Produce `docs/adr/0001-iphone-lidar-capture.md` (ADR + BDR). No code.
- **Technical questions it must answer.** ARKit `sceneDepth` (256×192 depth + confidence,
  LiDAR ~5 m) and `ARFrame.camera` intrinsics/transform vs the PerceptionBundle layout
  (`manifest/intrinsics/poses/frame_times`, `frames/NNNNN/{rgb.jpg, depth.png uint16 mm,
  conf.png}`; note `conf.png` already exists in the layout); whether RoomPlan (semantic boxes,
  no per-frame depth) fits at all or only raw ARKit frames do; coordinate conventions (ARKit
  is Y-up and gravity-aligned, which would also remove the TUM floor-snapping gotcha); which
  tier the ARKit VIO poses would replace (tier-1 odometry vs MASt3R) and what that means for
  the paper's pose claims (invariant 4: pose numbers come from TUM only); masks still need the
  YOLO+SAM2 seam server-side; an interim zero-app path via Record3D/Polycam raw exports;
  depth resolution vs the 5 mm TSDF voxel.
- **Business questions.** Addressable market, competitors (Polycam, Matterport, Scaniverse,
  Luma, RoomPlan-based apps), differentiation (per-object rigid bodies with mass/material),
  unit cost per scene (from the orchestration agent's cost record if available), risks.
- **Reads.** `src/perception/bundle.py`, `src/perception/dataset_reader.py`, `README.md`,
  `BENCHMARK.md` (§1 pose), `config/pipeline.yaml` tiers.
- **Modifies.** New `docs/adr/0001-iphone-lidar-capture.md` only.
- **Depends on.** Nothing. Blocks `lidar-capture-agent`.
- **Bounded context.** A (Perception), analysis only.
- **Branch.** `docs/adr-lidar-feasibility`

### lidar-capture-agent  — BLOCKED
- Would own an iOS capture app (likely its own repo) plus `src/perception/arkit_reader.py`
  (new reader, context A) with tests. **Does not start, and gets no branch, until the ADR/BDR
  above is explicitly approved by the maintainer in writing.** Listed here only so nobody
  invents it.

## Execution order

```
Wave 0 — parallel, no dependencies
  job-api-agent | docker-agent | observability-agent (A) | security-agent (A)
  [lidar-feasibility-agent may run here too; it is not on the critical path]

  ── gate: job-api merged into develop ──

Wave 1 — parallel
  runpod-orchestration-agent | security-agent (B) | observability-agent (B)

  ── gate: Wave 1 + docker merged ──

Wave 2 — parallel
  api-docs-agent (after security B, observability B)
  load-test-agent (after docker, security B, orchestration)

  ── gate: everything merged ──

Wave 3
  runbook-agent
```

Critical path: job-api → orchestration → load-test → runbook.
Strictly sequential: job-api before {orchestration, security B, observability B, api-docs,
load-test}; security B before api-docs and load-test; all before runbook.
