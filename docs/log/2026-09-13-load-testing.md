# 2026-09-13 — Load testing: k6 scenarios, SSE probe, `make loadtest`, manual CI job

Branch `feat/load-testing` (PR into `develop`), after docker (PR #10), the
job API (PR #9), security phase B (PR #7) and RunPod orchestration.
Changes what is runnable: `make loadtest` now runs a suite against the
compose stack and writes `loadtest/results/<stamp>/`; the recorded run
(2026-09-12 on this box) and its disclaimer live in `docs/loadtest.md`.
`STATUS.md` gained one line.

**Every number this produces is API-layer throughput with a mock worker on
CPU under Docker Desktop on WSL2. It is not pipeline or GPU throughput and
must never be cited as such (CLAUDE.md invariant 7).**

## What was done

- `loadtest/lib.js` — shared k6 helpers: base URL / bearer key from env,
  the fixture archive via `open()`, `uploadJob()` (honours 429 +
  `Retry-After`, up to `UPLOAD_RETRIES`), `pollUntilDone()`, `fetchScene()`
  (scene.json + every mesh and hull in one `http.batch`, GLB magic checked),
  custom metrics (`rate_limited`, `upload_ok`, `upload_retries`, `job_done`,
  `job_wait_ms` = 202→done, `job_turnaround_ms` = upload start→done,
  `scene_fetch_ms`, `sse_held` / `sse_rejected` / `sse_ended`), and a
  `handleSummary` that prints a text summary and writes the raw k6 summary
  JSON under `RESULTS_DIR` (jslib's `textSummary` would need network egress
  from the k6 container). 429 is declared an *expected* status
  (`http.setResponseCallback`) so it is measured in `rate_limited` instead of
  hiding in `http_req_failed`.
- Five scenarios, each with k6 `thresholds` (p95 per route, error rate
  < 1 %, `upload_ok` and `job_done` == 100 %, checks > 99 %):
  `upload_burst.js` (constant VUs upload → poll → delete),
  `status_polling.js` (constant arrival rate, 1 list per 10 polls),
  `scene_fetch.js` (viewer page loads at a constant rate),
  `sse_clients.js` (see below), `mixed.js` (all four at once, sized under
  the open-mode general bucket). `EXPECT_NO_429=1` / `EXPECT_429=1` add a
  threshold on `rate_limited` for the keyed-mode runs.
- **SSE.** k6 has no SSE support and buffers whole bodies, so
  `sse_clients.js` gives `/jobs/{id}/events` a `HOLD_S` timeout and treats
  the timeout as "the server held the stream" (`sse_held`), 429 as the
  per-IP cap (`sse_rejected`), 200 as an unexpected early close
  (`sse_ended`). It cannot see events. `loadtest/sse_clients.py` (standard
  library, runs in the `sse-probe` service from `python:3.11-slim`) opens N
  concurrent streams, parses the events, requires one `object_added` per
  scene object on every client, and with `--expect-cap M` opens an
  (M+1)-th client and requires 429 `too_many_streams`. The suite stays at
  the default cap `SOBA_SSE_MAX_PER_IP=8` (8 clients pass, the 9th is
  refused); raise the env to test more clients per IP. xk6-sse was not
  used: it needs a custom k6 image built with Go, which is more machinery
  than a 200-line stdlib script.
- `loadtest/run.sh` (`make loadtest`; `KEEP=1`, `SMOKE=1`, `NO_BUILD=1`):
  builds `out/scene_test` if missing, `compose --profile cpu up -d
  --build`, waits for `/scene.json`, runs the **open-mode** pass (every
  scenario + the SSE probe), then recreates only the `api` container with
  `SOBA_API_KEYS=loadtest:<k>:loadtest,plain:<k>` for the **keyed-mode**
  pass: a `loadtest` key must see zero 429s at 20 upload VUs and 300 rps
  polling (the bypass proof), a plain key at the same rates must see 429s
  (the observation), and an unauthenticated request must get 401. Writes
  `run.json` (k6/docker/compose versions, host CPU/RAM, compose env,
  durations) and `summary.md` (`loadtest/summarize.py`). Tears the stack
  down unless `KEEP=1` (then restores open mode and leaves it up).
- `docker-compose.yml` — `loadtest` profile: `k6` (`grafana/k6:2.2.0`,
  pinned, host uid so results are host-owned, `./loadtest` read-only,
  `./loadtest/results` writable) and `sse-probe`; `SOBA_MOCK_DELAY_S`
  passed through to `api` and `worker` so mock jobs can take time (the
  suite uses 0.5 s to build queue depth). `Makefile` `loadtest` target;
  `make down` also takes the loadtest profile down. `.gitignore`:
  `loadtest/results/`.
- `loadtest/fixtures/bundle_small.zip` — 2.3 KB synthetic PerceptionBundle
  (4 frames, `tests/api/_fixtures.py make_bundle`), committed.
- `.github/workflows/loadtest.yml` — `workflow_dispatch` only, never on
  push: `make loadtest SMOKE=1` on `ubuntu-latest` with the results as an
  artifact and `summary.md` in the step summary.
- `docs/loadtest.md` — the results template with the recorded run.

## Decisions

- **Single client IP.** All k6 VUs share the k6 container's IP, so in open
  mode the whole suite is one identity to the limiter: 100 rps / burst 200
  in general and 1 rps / burst 10 on `POST /api/jobs`. Open-mode rates are
  sized under those (80 rps polling, 8 page loads/s = 56 rps, mixed ≈ 65
  rps + uploads) and uploads are allowed to wait on `Retry-After`; the
  numbers above the limits come from the keyed pass with the `loadtest`
  flag. This is the honest shape of the API as configured, not a
  workaround.
- **No fake RunPod endpoint container.** The mock worker copies
  `out/scene_test` and never calls RunPod, so `SOBA_RUNPOD_URL` has no
  effect on this suite; the `runpod_policy` fake-endpoint tests already
  cover that client. A real-mode worker needs the pipeline image and a
  GPU (pod), out of scope for an API-layer test.
- **No `loadtest` extra in `pyproject.toml`.** k6 is a container and the
  probe is standard library; an empty extra would be noise.
- **No Python tests added.** The suite is exercised by `make loadtest`; the
  pytest baseline is unchanged.
- **`upload_ok == 100 %` is asserted only under the bypass key.** The
  recorded run failed `upload_burst` (10 VUs, open) and
  `keyed_plain_upload_burst` (20 VUs, plain key) on that threshold: both
  deliberately exceed the 1 rps / burst 10 upload bucket from one IP, and
  the server admitted exactly burst + rps × duration (45 in 30 s). The
  give-ups are the thundering-herd effect of every VU receiving the same
  `Retry-After` (≤ 1 s) and retrying together, so after `UPLOAD_RETRIES`
  waits some VUs stop. Asserting 100 % there asserts something the limiter
  is meant to refuse. `lib.js uploadThresholds()` now requires
  `upload_ok == 100 %` and `rate_limited == 0` only with `EXPECT_NO_429=1`;
  otherwise `upload_ok` is reported (plus a new `upload_gave_up` counter)
  and required to be non-zero, `job_done == 100 %` holds in both modes, and
  a 429 that survives the retries is not a failed check (any other non-202
  still is). The alternative, raising `UPLOAD_RETRIES` until nobody gives
  up, would only hide the herd behind a longer wait.

## Observations from the recorded run

The numbers and the full reading are in `docs/loadtest.md`; in short:

- Open mode admits what it is configured to admit: uploads at burst 10 +
  1 rps, polling at 100 rps + burst 200 (the plain key at 300 rps was
  refused 5804 of 9009 times). A refused request costs 2–4 ms.
- The `loadtest` key bypass is clean: 0 429s at 20 upload VUs and 300 rps
  polling, status p95 11 ms.
- The compose worker is one job at a time, so 20 uploaders queue ≈ 20 ×
  `SOBA_MOCK_DELAY_S` (202→done p95 11.6 s at 0.5 s jobs). Queue depth, not
  API latency, and nothing to do with pipeline throughput.
- Under the bypass at 20 concurrent accepted uploads the upload route's p95
  went to 1.5 s (median 11 ms); consistent with `store.create()` and
  `get()` serialising on the sqlite store's process-wide lock, not profiled.
- SSE: 8 streams held for free (connect ≤ 33 ms, first event ≤ 45 ms, all
  `object_added` replayed to every client); the 9th client got 429
  `too_many_streams` in 4.5 ms and the cap held for the whole 20 s.
- Static serving of the fixture scene: page load p95 52 ms at 8 loads/s.
- Docker Desktop was down on 2026-09-13 when the page was written, so the
  suite could not be rerun with the corrected thresholds; the recorded
  numbers predate the threshold change and are unaffected by it.
