# 2026-09-12 — Observability phase B: `GET /metrics`, request log with job-id correlation, job-state events

_Written on branch `feat/observability` (AGENTS.md agent 4, phase B), after PR #8 (phase A) and PR #9 (job API) merged into `develop`. Phase A's `run_metrics.json` contract is in [2026-09-11-observability-a.md](2026-09-11-observability-a.md)._

## What changed

- New `src/api/routes/metrics.py`, registered in `api.app.create_app()` as one import plus
  `ApiTelemetry(ctx)`, `metrics_routes(tel)`, `telemetry_middleware(tel)` and
  `app.state.telemetry = tel` (kept to five lines so the security and orchestration agents'
  edits to `app.py` merge cleanly). One `ApiTelemetry` per app with its own
  `prometheus_client.CollectorRegistry`, so several apps in one process (tests) never collide.
- `GET /metrics` — Prometheus text format (`prometheus_client.generate_latest`; content type
  `text/plain; version=1.0.0` with prometheus-client 0.26, `0.0.4` on older releases).
  `prometheus_client` is the optional **`telemetry` extra** (`pip install -e ".[telemetry]"`);
  without it the route answers **501** `{"error": {"code": "metrics_unavailable", ...}}` and the
  request log, `run_metrics.json` and everything else are unaffected.
- **Request-logging middleware** (`RequestTelemetryMiddleware`, pure ASGI, outermost): one
  `http` event per request with `method`, `path`, `route` (a bounded path template such as
  `/jobs/{id}/scene.json`, unknown paths → `/other`), `status`, `seconds`, `client`, and
  `job_id` parsed from `/jobs/{id}/...` and `/api/jobs/{id}`; INFO normally, DEBUG for
  `/metrics` scrapes, ERROR with `unhandled: true` when the app raised. JSON when
  `SOBA_LOG_JSON=1`: the middleware calls `telemetry.configure_logging()` if nothing has
  configured the root logger yet (uvicorn does not; pytest's caplog does). The duration covers
  the whole response, so an SSE `/events` stream logs when it ends.
- **Job state transitions** are logged from `src/api/jobs/store.py` `_apply_transition`, the one
  code path both stores and any worker (in-process thread or an external queue consumer) go
  through: event `job_state` with `job_id`, `state`, `stage`, `status`, `prev`, `error`; WARNING
  for `failed`, INFO otherwise. The no-op "same stage twice" path logs nothing.
- **Ingestion of `run_metrics.json`**: at scrape time `ApiTelemetry.refresh()` lists the
  JobStore, sets the job-state gauges, and for every job seen terminal (`done`/`failed`) for
  the first time reads `<jobs_dir>/<id>/scene/run_metrics.json`, validates it against
  `src/telemetry/run_metrics.schema.json` and feeds the counters straight from the file's own
  numbers — nothing is recomputed (AGENTS.md rule). Each job is ingested once. A missing file
  (mock worker, job failed before the run started) is a DEBUG skip; an unreadable or
  contract-breaking one increments `soba_run_metrics_ingest_errors_total` and logs
  `ingest_error` at WARNING. Scrape-time ingestion (rather than a hook in the worker) is what
  keeps an external worker covered: it only has to share the JobStore and the jobs dir.

## Metrics exposed (all prefixed `soba_`)

| metric | type | labels | fed from |
| --- | --- | --- | --- |
| `http_requests_total` | counter | `method`, `route`, `status` | middleware |
| `http_request_duration_seconds` | histogram | `method`, `route` | middleware (buckets 5 ms … 30 s) |
| `jobs` | gauge | `state` ∈ queued/running/done/failed | JobStore at scrape time |
| `job_runs_total` | counter | `status` ∈ ok/failed | `run.status` of each ingested file |
| `gate_decisions_total` | counter | `strategy` ∈ tsdf/completion/generative | `gate.counts` (keep / complete / regenerate) |
| `gate_routed_total` | counter | `strategy` | `gate.per_object[].routed` (what was applied under `--force-strategy`) |
| `pipeline_stage_seconds_total` | counter | `stage` | `stages.<name>.seconds` |
| `pipeline_stage_runs_total` | counter | `stage` | `stages.<name>.count` |
| `pipeline_drops_total` | counter | `reason` | `drops` |
| `remote_calls_total`, `remote_call_seconds_total`, `remote_est_usd_total` | counter | `kind` (`runpod/<endpoint>`) | `remote.by_kind` |
| `run_metrics_ingest_errors_total` | counter | — | ingestion failures |

Fixed label sets (job states, the three strategies, `ok`/`failed`) are pre-registered so a
dashboard sees zeros rather than gaps. Stage durations are exported as `_seconds_total` +
`_runs_total` counters (a Prometheus summary cannot be fed pre-aggregated sums); divide for the
mean, rate both for throughput.

## Verified here

`tests/api/test_api_metrics.py` (26 tests, no open3d): route templating and job-id parsing; the
`/metrics` body and content type; job-state gauges following the store; ingestion from the
fixture `tests/api/run_metrics_fixture.json` (values land verbatim, once per job, add up across
jobs); a malformed and a schema-breaking file counted as ingest errors; a failed job with no
scene gauged but not ingested; the 501 path with `prometheus_client` absent; the request log
record (route, status, job_id, duration, DEBUG for scrapes); JSON request lines under
`SOBA_LOG_JSON=1`; job-state transition events. Full suite on this box: the baseline 42
failures / 7 collection errors (missing `open3d`) plus every other test passing.

Not verified here: a real `run_metrics.json` produced by the `real` worker mode on the pod
flowing into `/metrics` (needs the GPU machine: start `python scripts/serve.py --worker-mode
real`, upload a bundle, then `curl -s localhost:8000/metrics | grep soba_gate`).
