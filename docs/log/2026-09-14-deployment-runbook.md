# 2026-09-14 — Deployment runbook (`docs/deployment-runbook`, Wave 3)

_runbook-agent (`.claude/AGENTS.md` §8), branch `docs/deployment-runbook` from
`develop@8dcafef` (PRs #7–#14 merged). Changes what is runnable in one place:
the compose `gpu` profile (see below). Everything else is documentation._

## What was done

- `docs/runbook.md` — the operator page, in this order: overview + ASCII
  architecture (api, redis, worker, RunPod serverless endpoint, optional pod),
  prerequisites, build & publish (tag scheme `ghcr.io/sladojevicm/soba-{api,pipeline}:<version>|<sha>`,
  `-devel` suffix for the serverless variant; CI builds with `push: false`, so
  publishing is manual until a push job exists), configure (env matrix by
  component, where each secret lives, reverse-proxy sketch for the bearer-only
  viewer problem), deploy (compose single host; the two-host split and why
  sqlite-over-NFS is not an option; GPU worker via the `gpu` profile; RunPod
  serverless endpoint; pod), three pre-deploy gates (G1 Open3D CUDA `check`,
  G2 model pins, G3 CI green), smoke test, scaling & cost controls, monitoring
  with nine Prometheus alert rules, backup & retention (cron through the API),
  rollback, five incident scenarios, release flow, known limitations.
- `deploy/runpod/README.md` — pointer to the runbook at the top; the images
  section says CI does not push and names the Open3D CUDA gate; the bootstrap
  block no longer duplicates the clone and shows `REPO_BRANCH=<tag>`; the
  serverless section points at the pipeline image + `-devel` base instead of
  an ad-hoc `pip install runpod`; a paragraph on using the pod as a queue
  worker. Pod-specific detail (GPU choice, volume, ports, ComPC) unchanged.
- `docker-compose.yml` — `worker-gpu` now mounts `./out` at `/data` with
  `SOBA_JOBS_DIR=/data/jobs`, the same container path as `api`. Before, it
  mounted `./out:/app/out`, but the job record stores the extracted bundle as
  the absolute path the API saw (`src/api/routes/jobs.py` `bundle_dir=str(res.root)`
  → `/data/jobs/<id>/extracted/...`) and the worker opens it verbatim
  (`src/api/jobs/worker_local.py`), so every real job would have failed at
  `validating`. `docker compose --profile gpu config --quiet` passes; the
  profile itself still needs a GPU host.
- `STATUS.md` (one current-state line), `README.md` (one Documentation line),
  `docs/log/README.md` (this entry), `.claude/AGENTS.md` (status note: what
  merged, #6 open, `lidar-capture-agent` blocked).

## Executed on this box (Docker Desktop 29.3.1, compose 5.1.1, no GPU)

Docker Desktop went down mid-session on 2026-09-13 (~22:48 local) and came back
on 2026-09-14 ~08:38 UTC; the api/worker containers (`restart: unless-stopped`)
came back with it and were torn down before the rerun.

| Step | Result |
|---|---|
| `docker build -f docker/api.Dockerfile -t soba-api:local .` (2026-09-13) | exit 0, image `00e7d1ce26c2`, 331 MB |
| `docker build -f docker/frontend-check.Dockerfile --target check ...` | exit 0 (the `diff -r` stage was a cached layer from the 2026-09-13 build, which a failing diff cannot produce) |
| `docker/pipeline.Dockerfile` | **not built here** (7 GB base, CI builds it; `docker image ls soba-pipeline` was empty) |
| `docker compose --profile cpu up -d` (`SOBA_QUEUE_URL=redis://redis:6379/0 SOBA_WORKER_INPROC=0`) | redis healthy, api healthy after 1 s, worker up; api log: the open-mode `SOBA_API_KEYS is not set` warning |
| `python scripts/submit_job.py --archive loadtest/fixtures/bundle_small.zip --tier 2` | job `4a301442abda3e62` queued → done in 1.0 s; `out/jobs/<id>/{upload.zip, extracted/, scene/{scene.json, objects/, cost.json}}`; `cost.json`: mode mock, source null, calls 0, est_usd 0.0, budget 40 / 3600 / 5.0 |
| `node scripts/verify_browser.js --scene out/jobs/<id>/scene --path /jobs/<id>/` | ALL CHECKS PASSED 8/8; screenshot inspected: three fixture objects rendered, not black |
| `curl /metrics` | 200 `text/plain; version=1.0.0`, 114 lines, `soba_jobs{state="done"} 1.0`, `soba_http_requests_total{method="POST",route="/api/jobs",status="202"} 1.0` |
| `docker run --rm soba-api:local python -m orchestration.worker --help` / `--once` / `id` | usage printed; without a queue: "no queue: pass --queue-url or set SOBA_QUEUE_URL", exit 2; uid 10001 |
| `SMOKE=1 NO_BUILD=1 make loadtest` (2026-09-13 22:45) | 10 of 11 scenarios ran, Docker died during `keyed_plain_upload_burst` (k6 exit 125); `keyed_loadtest_status_polling` crossed its 100 ms p95 bound (129 ms) while an image build ran alongside |
| `SMOKE=1 NO_BUILD=1 make loadtest` (2026-09-14 08:39 UTC, stamp `20260914T083908Z`) | **all 11 scenarios + SSE probe pass, `failed: []`**; stack torn down |
| `docker compose --profile gpu config --quiet` | ok (after the compose fix) |
| `gh run list --branch develop` | `8dcafef` (merge of #14) in progress at the time, `257032d` and `001d02c` success |
| `pytest -q --continue-on-collection-errors` | 401 passed, 40 failed, 14 skipped, 7 errors — the missing-`open3d` baseline, unchanged |

Rerun summary rows (API-layer, mock worker, CPU, WSL2 — never pipeline or GPU
throughput, CLAUDE.md invariant 7; `loadtest/results/` is gitignored, so the
rows are copied here):

| script | mode | knobs | requests | rps | p95 ms | 429s | p95 by route (ms) | thresholds |
|---|---|---|---|---|---|---|---|---|
| upload_burst | open | VUS=5 10s | 252 | 20.1 | 58.1 | 4 | upload 273; status 15; delete 65 | pass |
| status_polling | open | RATE=80 10s | 808 | 72.5 | 13.6 | 0 | status 13; list 14 | pass |
| scene_fetch | open | RATE=8 10s | 568 | 51.9 | 34.8 | 0 | scene 8; mesh 35; hull 36; page load 47 | pass |
| sse_clients / sse_clients_cap | open | VUS=8 / 9, HOLD_S=3 | 32 / 70 | – | – | 0 / 38 | sse_held 24; sse_rejected 38 at the cap | pass |
| sse_probe | open | 8 clients | 8/8 200 | – | – | 429 on the 9th | connect ≤ 13 ms, first event ≤ 20 ms | pass |
| mixed | open | 15s | 1026 | 53.2 | 61.5 | 0 | upload 136; status 34; scene 26; mesh 61; hull 63 | pass |
| keyed_loadtest_upload_burst | keyed | VUS=10 | 525 | 34.4 | 65.4 | 0 | upload 437; status 45; delete 81 | pass |
| keyed_loadtest_status_polling | keyed | RATE=300 | 3009 | 233.5 | 19.9 | 0 | status 18; list 52 | pass |
| keyed_plain_status_polling | keyed | RATE=300 | 3009 | 278.6 | 4.5 | 1804 | status 4; list 5 | pass |
| keyed_plain_upload_burst | keyed | VUS=10 | 399 | 21.5 | 70.6 | 57 | upload 465; upload_ok 93 % | pass |

## Not executed here (and what each needs)

- `docker run --rm --gpus all soba-pipeline:<tag> check` — GPU host. The
  wheel finding (`docs/log/2026-09-11-docker-ci.md`) makes this the first gate.
- The model-pin recovery block — the RTX 4060 machine and the pod volume.
- Registry login / push — no registry or credentials exist yet.
- `gpu` profile, real-mode worker, RunPod endpoint creation and probe, pod
  bootstrap — GPU host, RunPod account and key.
- The release flow itself — no release was cut.

## Findings (flagged in `docs/runbook.md` §14)

- `docker/pipeline.Dockerfile` has no `USER`: the GPU worker and the
  serverless handler run as root, so scene files under `out/jobs/<id>/scene`
  are root-owned and the uid-10001 API cannot `DELETE` those jobs. Not fixed:
  a non-root user needs a writable `HOME` for the HF cache and the
  `setup_*.sh` assumptions checked on a GPU host.
- `docker-compose.yml` hardcodes `image: soba-api:local` / `soba-pipeline:local`;
  a registry tag has to be re-tagged locally. Not fixed (a `SOBA_IMAGE_TAG`
  variable is a one-line change, left to the maintainer with the push job).
- The two-host split has no safe shared job store: sqlite WAL over NFS is not
  supported. Documented as a gap, not worked around.
- `.github/workflows/ci.yml` never pushes images; the runbook defines the tag
  scheme for when it does.
