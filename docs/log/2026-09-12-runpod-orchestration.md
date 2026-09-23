# 2026-09-12 — RunPod orchestration: hardened client, cost guardrails, external worker

_Branch `feat/runpod-orchestration` (AGENTS.md agent 3, Wave 1). Depends on the job API
(PR #9) and observability phase A (PR #8). Nothing here was run against a real RunPod
endpoint: this box has no GPU and no key (CLAUDE.md invariant 7); see "Untested here"._

## What is runnable now

```bash
# API enqueues to Redis; an external worker consumes (same jobs dir on both)
SOBA_QUEUE_URL=redis://127.0.0.1:6379/0 SOBA_WORKER_INPROC=0 python scripts/serve.py --scene out/scene_test
SOBA_QUEUE_URL=redis://127.0.0.1:6379/0 PYTHONPATH=src python -m orchestration.worker --jobs-dir out/jobs --mode mock
PYTHONPATH=src python -m orchestration.worker --once --mode gate-only     # one job, then exit
SOBA_RUNPOD_DISABLED=1 PYTHONPATH=src python -m orchestration.worker ...  # kill switch: no RunPod call, objects dropped
pytest tests/orchestration tests/reconstruction/test_runpod_engine.py    # 62 tests, no open3d / GPU / network
```

## What changed

- **`config/pipeline.yaml` `runpod:`** is now read (it was dead) and is the only place any
  resilience or cost number lives: `transport` (`run` = `/run` + `/status/{id}` polling,
  `runsync`), `request_timeout_s`, `job_timeout_s`, `poll_interval_s`, `stall_timeout_s`,
  `retry.{max_attempts,backoff_base_s,backoff_max_s,jitter_s}`,
  `circuit_breaker.{consecutive_failures,cooldown_s}`,
  `budget.{max_calls,max_gpu_seconds,max_est_usd}` (per job),
  `price.{usd_per_gpu_second,by_endpoint}` (an **assumption**, 24 GB flex worker; set it from
  the endpoint's GPU tier). The old `pod_url` key is gone: that role is `SOBA_RUNPOD_URL`.
  The breaker moved from 2 failures / 30 s to 3 / 120 s; the old values were never applied.
- **`src/reconstruction/runpod_policy.py`** — `load_runpod_config()` (its own cached yaml read:
  importing `scene.lookup` pulls in open3d, which the API/worker host may lack), `RetryPolicy`,
  `CircuitBreaker` (closed → open after N consecutive failures → one half-open probe after the
  cooldown → closed on success; process-wide per endpoint id via `breaker_for()`), `Budget`
  (calls, billable GPU seconds, est. USD; `exhausted()` names the cap), `estimate_usd()`,
  `disabled()` (the kill switch), and the four drop reasons.
- **`RunPodEngine`** (`generative.py`), signature unchanged:
  - `_runsync()` submits `POST /v2/{endpoint}/run` and polls `GET /status/{id}` every
    `poll_interval_s`. RunPod's `/runsync` stops waiting well before a 2.5–4.5 min
    generation finishes and answers `IN_PROGRESS` + an id, which the old code read as
    "no mesh"; the July generation runs never hit this because they went through
    `SOBA_RUNPOD_URL` to the SDK's local test server, not a serverless endpoint. With
    `SOBA_RUNPOD_URL` it stays one blocking POST, and still follows an early
    `IN_PROGRESS` reply to `/status` next to it.
  - Retries with exponential backoff + jitter on **transient** failures: socket/network
    errors, HTTP 429 and 5xx, a job stuck in one non-terminal state for `stall_timeout_s`
    (cancelled first), a non-terminal reply with nowhere to poll. **Permanent** (no retry):
    handler `FAILED` / `error`, other 4xx, `job_timeout_s` exceeded (cancelled), a reply
    without a mesh. Errors are `RunPodError` (a `RuntimeError`) with `RunPodTransient`,
    `RunPodJobFailed`, `RunPodConfigError` subclasses.
  - Every submission, retries and failures included, is charged to the engine's per-job
    `Budget`, reported to the endpoint's breaker, and passed to `remote_call_hook` as
    **billable** seconds (`executionTime` when the reply has it, else wall time).
  - `regenerate()` makes no call and returns `None` — the assembler's existing "dropped"
    path — when `SOBA_RUNPOD_DISABLED=1`, the budget is spent, or the breaker is open,
    recording `runpod_disabled` / `runpod_budget_exhausted` / `runpod_breaker_open` on the
    run through `telemetry.drop()`. `complete()` declines the same way and falls back to the
    local Poisson repair on any `RunPodError` (never aborts an assembly).
  - Security audit gap G2: `SOBA_RUNPOD_URL` and `SOBA_RUNPOD_BASE_URL` must be `https://`
    unless `SOBA_RUNPOD_ALLOW_HTTP=1`, checked before any request, so the bearer key can no
    longer follow an override to a plain-http host.
- **`scripts/run_assemble.py`** — `engine.regenerate()` is wrapped: a `RunPodError` records
  `runpod_failed` for that track (not cached as a rejection) and the run continues;
  `RunPodConfigError` still aborts. The remote-call hook now prices each call with
  `runpod.price`, so `run_metrics.json` `remote.est_usd` is filled.
- **`src/api/jobs/queue_redis.py`** — `RedisJobQueue` (`LPUSH soba:jobs` / `BRPOP`, the
  contract documented in `queue.py`), chosen by `create_app` when `SOBA_QUEUE_URL` is set
  (explicit `None` checks: an empty queue is falsy). `worker` extra = `redis`.
- **`src/orchestration/worker.py`** (`python -m orchestration.worker`) — subclasses
  `LocalWorker` and reuses its `process()`, so the stage path is the API thread's; adds a
  `job_state` log event per transition, SIGTERM/SIGINT that finish the current job, `--once`,
  and the per-job cost record.

## Cost record — `<jobs_dir>/<id>/scene/cost.json`

```json
{"schema": 1, "job_id": "…", "tier": 2, "mode": "real", "written_at": "…",
 "source": "run_metrics.json",
 "run": {"status": "done", "error": null, "wall_seconds": 812.4, "pipeline_status": "ok"},
 "remote": {"calls": 3, "gpu_seconds": 400.0, "est_usd": 0.176, "by_kind": {"runpod/gen": {…}}},
 "drops": {"runpod_failed": 1, "runpod_budget_exhausted": 0, "runpod_breaker_open": 0, "runpod_disabled": 0},
 "budget": {"max_calls": 40, "max_gpu_seconds": 3600, "max_est_usd": 5.0, "exhausted": null},
 "price": {"usd_per_gpu_second": 0.00044},
 "runpod_disabled": false}
```

`source` is `null` (all zeros) in mock mode or when the run died before writing
`run_metrics.json`. The observability layer reads this file; `run_metrics.json` is unchanged.

## Untested here (needs the pod + a serverless endpoint)

```bash
# on the pod, with RUNPOD_API_KEY + RUNPOD_GEN_ENDPOINT_ID exported (deploy/runpod/env.example)
PYTHONPATH=src SOBA_LOG_JSON=1 python scripts/run_assemble.py --bundle bundles/office_3 --tier 2 --out out/scene_office_3_rp
#   expect: /run + /status polling in the log, remote.est_usd in out/scene_office_3_rp/run_metrics.json
SOBA_QUEUE_URL=redis://127.0.0.1:6379/0 PYTHONPATH=src python -m orchestration.worker --jobs-dir out/jobs --mode real --once
#   expect: out/jobs/<id>/scene/cost.json with calls/gpu_seconds/est_usd > 0
```

What the fake endpoint cannot prove: RunPod's real `executionTime` semantics per GPU tier, the
price assumption, cancel behaviour on a live queue, and the SDK test server's `/status` route
next to `/runsync`.

## Follow-ups

- No job retry / requeue: a worker killed mid-job leaves the job `running` (unchanged from
  the job API; retention policy is still open).
- The breaker is per process; several workers discover an outage independently.
- `budget` is checked before a call, not mid-retry: a call may overrun a cap by its own
  retries (bounded by `retry.max_attempts`).
