# Load testing — API layer, mock worker, CPU

> **Every number on this page is API-layer throughput with a mock worker on
> CPU under Docker Desktop on WSL2. It is not pipeline throughput and not GPU
> throughput, and must never be cited as either** (CLAUDE.md invariant 7).
> The mock worker copies `out/scene_test` after `SOBA_MOCK_DELAY_S` seconds; no
> frame is perceived, no TSDF is fused, no RunPod call is made. What is
> measured is the Starlette API (`src/api/`), its sqlite job store, the Redis
> queue hand-off, the rate limiter, the SSE fan-out and static mesh/hull
> serving.

## What the suite is

`loadtest/` holds five [k6](https://k6.io) scenarios over a shared `lib.js`,
a standard-library SSE probe, `run.sh` (the driver behind `make loadtest`)
and `summarize.py` (renders `loadtest/results/<stamp>/summary.md`, which the
table below is copied from verbatim; numbers are never typed in by hand).

| script | traffic | executor | thresholds |
|---|---|---|---|
| `upload_burst.js` | `VUS` users: `POST /api/jobs` (2.3 KB synthetic PerceptionBundle, `fixtures/bundle_small.zip`) → poll `GET /api/jobs/{id}` to `done` → `DELETE` | constant VUs | `job_done == 100 %`; `upload_ok == 100 %` only with the bypass key, otherwise `> 0` (see *Rate limiter*); `http_req_failed < 1 %`; p95 upload < 2 s, status < 250 ms, delete < 500 ms; checks > 99 % |
| `status_polling.js` | `RATE` rps of `GET /api/jobs/{id}` on one finished job, one `GET /api/jobs` per ten polls | constant arrival rate | p95 status < 100 ms, list < 250 ms; `http_req_failed < 1 %`; checks > 99 % |
| `scene_fetch.js` | `RATE` viewer page loads/s: `GET /jobs/{id}/scene.json` then every mesh and hull in one `http.batch` (7 requests per load with the 3-object fixture), GLB magic checked | constant arrival rate | p95 scene < 100 ms, mesh/hull < 150 ms, page load < 500 ms; `http_req_failed < 1 %` |
| `sse_clients.js` | `VUS` clients holding `GET /jobs/{id}/events` for `HOLD_S` | constant VUs | `sse_held > 0`, `sse_ended == 0`; `sse_rejected == 0` at the cap, `> 0` with `EXPECT_CAP=1` one above it |
| `sse_clients.py` (probe) | N concurrent `/events` clients, events parsed | stdlib `http.client`, threads | every client gets one `object_added` per scene object; with `--expect-cap M` the (M+1)-th client gets 429 `too_many_streams` |
| `mixed.js` | 2 uploaders + 30 rps polling + 4 page loads/s (28 rps) + 2 SSE holders at once, sized under the open-mode general bucket | four scenarios in one run | the union of the above at p95 < 250 ms per route |

`429` is declared an *expected* status for every request
(`http.setResponseCallback`), so it never hides in `http_req_failed`; it is
counted in `rate_limited` (and `upload_retries` / `upload_gave_up` for the
upload bucket). `EXPECT_NO_429=1` adds `rate_limited == 0`, `EXPECT_429=1`
adds `rate_limited > 0`.

k6 cannot read event streams (it buffers whole bodies), so `sse_clients.js`
only proves that the server *holds* a stream for `HOLD_S` (a client timeout
is the expected outcome and is counted in `sse_held`; k6 also counts it in
`http_req_failed`, which is why the SSE rows show a high "failed" share) and
that the per-IP cap answers 429. The Python probe reads the events.

## How to run

```bash
make loadtest                 # full suite, ~7 min, stack torn down afterwards
KEEP=1 make loadtest          # leave the stack up (open mode) afterwards
SMOKE=1 make loadtest         # 10 s scenarios, what the manual CI job runs
NO_BUILD=1 make loadtest      # reuse the soba-api:local image
D_UPLOAD=60s UPLOAD_VUS=20 SOBA_MOCK_DELAY_S=2 make loadtest   # knobs
```

`loadtest/run.sh` builds `out/scene_test` if missing, starts the compose
`cpu` profile (`api` + `redis` + `worker --mode mock`, `SOBA_QUEUE_URL`
set, `SOBA_WORKER_INPROC=0`, `SOBA_MOCK_DELAY_S=0.5` so jobs take long enough
to build queue depth), waits for `/scene.json`, then runs two passes:

1. **Open mode** (no `SOBA_API_KEYS`, what compose runs by default): every
   scenario plus the SSE probe. All k6 VUs share the k6 container's IP, so
   the whole suite is one identity to the limiter: 100 rps / burst 200 in
   general and 1 rps / burst 10 on `POST /api/jobs`. Rates are sized under
   the general bucket (80 rps polling, 8 page loads/s = 56 rps, mixed ≈ 65
   rps); uploads wait on `Retry-After`.
2. **Keyed mode**: the `api` container is recreated with
   `SOBA_API_KEYS=loadtest:<k>:loadtest,plain:<k>`. An unauthenticated
   request must get 401. The `loadtest`-flagged key bypasses both buckets and
   must see zero 429s at 20 upload VUs and 300 rps polling (the bypass
   proof; these are the only numbers above the open-mode limits). The plain
   key at the same rates must see 429s (the observation).

Every scenario writes its raw k6 summary to `loadtest/results/<stamp>/`
(gitignored); `run.json` records the k6/docker/compose versions, host CPU
and RAM and the compose env; `summary.md` is the rendered table. The stack
is taken down at the end (`docker compose --profile cpu --profile loadtest
down`) unless `KEEP=1`. `run.sh` exits 1 when any threshold failed.

The k6 image is pinned (`grafana/k6:2.2.0`, compose `loadtest` profile) and
runs as the host uid so the results are host-owned. The SSE probe runs from
`python:3.11-slim` with no dependencies.

`.github/workflows/loadtest.yml` is `workflow_dispatch` only, never on push:
it runs `SMOKE=1 make loadtest` on `ubuntu-latest` and uploads
`loadtest/results/` as an artifact. Its numbers come from a shared 2-vCPU
runner and are not comparable with the run below.

## Recorded run

Run on this box (the development laptop, see the host rows) on 2026-09-12,
full durations, from the worktree of `feat/load-testing` at `cecd2bf`
(scenarios) + `f0cbdbb` (compose profile). The scenario code has not changed
since except the threshold rule described under *Threshold failures*;
no request or timing path was touched, so the numbers stand. 

> **RERUN PENDING: run `make loadtest` with Docker Desktop running.** Docker
> Desktop was not running on 2026-09-13 when this page was written, so the
> suite could not be repeated with the corrected thresholds. The numbers
> below are measured, not typed in, but the "thresholds" column reflects the
> old rule (two scenarios marked FAILED). The first rerun should replace
> everything from here to *How to read the table* with the new
> `loadtest/results/<stamp>/summary.md` and is expected to pass every
> scenario on numbers like these.

**API-layer throughput with a mock worker on CPU under Docker Desktop on WSL2; not pipeline or GPU throughput; never cite as such** (CLAUDE.md invariant 7).

| setting | value |
|---|---|
| stamp | 20260912T101612Z |
| smoke | False |
| k6 | k6 v2.2.0 (commit/00a9a1b7f5, go1.26.5, linux/amd64) |
| docker | 29.3.1 |
| compose | 5.1.1 |
| host | Linux 6.6.87.2-microsoft-standard-WSL2 x86_64 |
| cpus | 8 |
| mem | 7.6Gi |
| cpu_model | 11th Gen Intel(R) Core(TM) i7-1165G7 @ 2.80GHz |
| compose_env | {"SOBA_QUEUE_URL": "redis://redis:6379/0", "SOBA_WORKER_INPROC": "0", "SOBA_MOCK_DELAY_S": "0.5", "SOBA_SSE_MAX_PER_IP": "8", "rate_limits": "defaults (100/200, upload 1/10)"} |
| durations | {"upload": "30s", "poll": "30s", "scene": "30s", "sse": "20s", "mixed": "45s"} |
| failed | ["upload_burst", "keyed_plain_upload_burst"] |

| script | mode | knobs | requests | rps | med ms | p95 ms | max ms | failed | 429s | p95 by route (ms) | custom | thresholds |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| keyed_loadtest_status_polling | keyed | DURATION=30s EXPECT_NO_429=1 MAX_VUS=300 RATE=300 | 9009 | 291.8 | 2.3 | 11.9 | 66.0 | 0.00% | 0 | status 11; list 18 | 202→done p95 768 ms; upload→done p95 801 ms; upload_ok 100%; job_done 100% | pass |
| keyed_loadtest_upload_burst | keyed | DURATION=30s EXPECT_NO_429=1 VUS=20 | 2699 | 65.7 | 11.1 | 67.4 | 1520.7 | 0.00% | 0 | upload 1517; status 55; delete 90 | 202→done p95 11610 ms; upload→done p95 11806 ms; upload_ok 100%; job_done 100% | pass |
| keyed_plain_status_polling | keyed | DURATION=30s EXPECT_429=1 MAX_VUS=300 RATE=300 | 9009 | 292.4 | 1.6 | 3.7 | 100.1 | 0.00% | 5804 | status 4; list 3 | 202→done p95 768 ms; upload→done p95 800 ms; upload_ok 100%; job_done 100% | pass |
| keyed_plain_upload_burst | keyed | DURATION=30s EXPECT_429=1 VUS=20 | 1124 | 28.8 | 3.4 | 62.3 | 788.5 | 0.00% | 615 | upload 62; status 31; delete 83 | 202→done p95 5586 ms; upload→done p95 10051 ms; upload_ok 46%; job_done 100%; upload_retries 559 | **2 FAILED** |
| mixed | open | DURATION=45s | 2991 | 59.4 | 12.7 | 65.8 | 5001.1 | 0.60% | 12 | upload 139; status 40; scene 41; mesh 59; hull 60 | 202→done p95 1049 ms; upload→done p95 2832 ms; page load p95 108 ms; upload_ok 100%; job_done 100%; sse_held 18; upload_retries 12 | pass |
| scene_fetch | open | DURATION=30s RATE=8 | 1695 | 54.9 | 32.0 | 40.9 | 71.5 | 0.00% | 0 | scene 6; mesh 41; hull 42 | 202→done p95 771 ms; upload→done p95 813 ms; page load p95 52 ms; upload_ok 100%; job_done 100% | pass |
| sse_clients | open | DURATION=20s HOLD_S=3 VUS=8 | 64 | 2.7 | 2999.8 | 3001.4 | 3002.4 | 87.50% | 0 | - | 202→done p95 769 ms; upload→done p95 799 ms; upload_ok 100%; job_done 100%; sse_held 56 | pass |
| sse_clients_cap | open | DURATION=20s HOLD_S=3 VUS=9 | 155 | 6.7 | 3.3 | 3000.8 | 3001.4 | 36.13% | 91 | - | 202→done p95 768 ms; upload→done p95 806 ms; upload_ok 100%; job_done 100%; sse_held 56; sse_rejected 91 | pass |
| status_polling | open | DURATION=30s EXPECT_NO_429=1 RATE=80 | 2408 | 78.1 | 3.5 | 5.1 | 31.8 | 0.00% | 0 | status 5; list 5 | 202→done p95 767 ms; upload→done p95 801 ms; upload_ok 100%; job_done 100% | pass |
| upload_burst | open | DURATION=30s VUS=10 | 723 | 19.5 | 4.1 | 67.1 | 860.6 | 0.00% | 244 | upload 83; status 25; delete 69 | 202→done p95 5439 ms; upload→done p95 6886 ms; upload_ok 67%; job_done 100%; upload_retries 222 | **2 FAILED** |

| SSE probe | mode | clients | hold s | 200s | connect max ms | first event max ms | cap probe | result |
|---|---|---|---|---|---|---|---|---|
| sse_probe | open | 8 | 3.0 | 8/8 | 33 | 45 | 429 | pass |

### How to read the table

- **requests / rps** are every HTTP request k6 made in the scenario,
  including the 429s and, for `upload_burst`, the polls and deletes.
- **med / p95 / max** are `http_req_duration` over all routes; the per-route
  p95 column is the one to quote.
- **failed** is k6's `http_req_failed` (anything outside 2xx and 429). For
  the SSE scenarios it is the share of deliberate `HOLD_S` timeouts, not
  errors; for `mixed` the 0.60 % (18 of 2991) is the same thing (`sse_held 18`).
- **202→done** is the queue wait plus the mock job (0.5 s);
  **upload→done** adds the `Retry-After` waits before the upload was accepted.
- **failed** in the settings block lists the scenarios whose k6 exit code was
  non-zero (thresholds crossed), see below.

### Observations

**Rate limiter (open mode, 1 rps / burst 10 on `POST /api/jobs`).**
`upload_burst` at 10 VUs got 45 of 67 uploads accepted in 30 s: burst 10 +
1 rps × 30 s is 40, so the server admitted what it is configured to admit
and refused the rest with 429 + `Retry-After` (244 429s, 222 waits). The
22 that never got through are a client-side effect: `Retry-After` is
`(1 − tokens) / rps ≤ 1 s` and every waiting VU receives the same value, so
they all retry together, one wins per second and the others burn a retry
(thundering herd); after `UPLOAD_RETRIES=8` waits (~8 s) a VU gives up.
The plain key at 20 VUs shows the same shape (47 of 103 accepted, 615 429s).
Latency is unaffected: a refused upload costs 2–4 ms. The general bucket
never tripped at 80 rps polling or 56 rps page loads (0 429s), and the
plain key at 300 rps polling was refused 5804 of 9009 times, i.e. it
admitted 3205 ≈ 100 rps × 30 s + burst 200. `Retry-After` is per
identity, so a real deployment behind one NAT sees one shared upload
bucket per office; `SOBA_TRUST_PROXY` + per-key limits are the answer, not
a larger bucket.

**Bypass key.** With the `loadtest` flag the same 20 VUs got 100 % of 2699
requests through with zero 429s and 300 rps polling ran at p95 11.9 ms
(status 11 ms, list 18 ms) — the limiter costs nothing measurable when it
lets a request through.

**Queue and worker.** The compose worker takes one job at a time
(`orchestration.worker` `serve()` loop), so with 0.5 s mock jobs the
ceiling is 2 jobs/s. 20 keyed VUs uploading as fast as they can produced a
202→done p95 of 11.6 s: that is ≈ 20 queued jobs × 0.5 s, queue depth by
construction, not API latency. Two uploaders (`mixed`) saw 1.0 s; a single
uploader sees 0.77 s (0.5 s job + Redis hand-off + the 0.25 s poll
interval). Job throughput with the real pipeline is a GPU question and is
not measured here.

**Upload tail under the bypass.** At 20 concurrent accepted uploads the
upload route's p95 rose to 1517 ms (max 1521 ms) while the median stayed at
11 ms; the plain-key run, where most uploads were refused before touching
disk, had p95 62 ms. The accepted path streams the archive to disk,
validates and extracts it in the threadpool, then calls `store.create()` —
a sqlite insert behind one process-wide `threading.Lock` with a fresh
connection per call — on the event loop, and `GET /api/jobs/{id}` takes the
same lock. The tail is consistent with those calls serialising behind each
other under 20 writers plus 20 pollers; this was not profiled, and at the
one-worker job rate it is invisible to users (the queue wait is 10× larger).
Status p95 rose from 5 ms (open, 80 rps) to 55 ms in the same run.

**SSE cap (`SOBA_SSE_MAX_PER_IP=8`).** 8 held streams cost nothing visible
(k6: 56 holds, 0 early closes; probe: 8 of 8 clients connected in ≤ 33 ms,
first event in ≤ 45 ms, all 3 `object_added` replayed to every client); the
9th client was refused with 429 `too_many_streams` in 4.5 ms and the cap
held for the whole 20 s (91 rejections while 8 streams were up). Note the
cap is per client IP: 8 browser tabs behind one NAT is the limit as shipped.

**Static serving.** 8 page loads/s (scene.json + 3 GLB meshes + 3 GLB hulls)
ran at 55 rps with page-load p95 52 ms and mesh/hull p95 41–42 ms; under
`mixed` the same requests went to 59–60 ms. The fixture meshes are small
(a few KB); real scenes with multi-MB GLBs will be bandwidth-bound, which
this suite does not exercise.

**Threshold failures.** Two scenarios failed in the recorded run,
`upload_burst` (open) and `keyed_plain_upload_burst`, both on
`upload_ok == 100 %` and `checks > 99 %`. Both deliberately exceed the
upload bucket from one IP, so the threshold asserted something the server
is meant to refuse. The rule was changed after the run (commit
"assert upload_ok == 100 % only under the bypass key"): `upload_ok == 100 %`
and `rate_limited == 0` are required only with `EXPECT_NO_429=1`, otherwise
`upload_ok` is reported (with the new `upload_gave_up` counter) and only
required to be non-zero; `job_done == 100 %` holds in both modes, and a 429
that survives the retries is no longer a failed check. Under that rule every
scenario of the recorded run passes on its numbers; the rerun will show it.
No other threshold was crossed: every p95 was under its bound with margin
except the keyed upload p95 (1517 ms of 2000 ms).

## Not measured

- Pipeline or GPU throughput, RunPod latency, cost per scene (invariant 7).
  A real-mode worker needs the pipeline image and a pod.
- Bandwidth on real meshes; TLS; anything behind a reverse proxy
  (`SOBA_TRUST_PROXY`).
- More than one API process: the rate limiter and the SSE cap are
  per-process (STATUS.md known limitations).
- Sustained runs longer than 45 s; sqlite growth with thousands of jobs.
