# Soba API

The HTTP service in front of the pipeline: upload a capture archive, poll (or
stream) the job, open the result in the browser viewer. The authoritative
description is `spec/openapi.yaml` (OpenAPI 3.1), which the running server
serves at **`GET /api/openapi.json`** and renders at **`GET /api/docs`**
(Redoc; the Redoc bundle loads from `cdn.jsdelivr.net` *in the browser*, so
the page needs network access at view time — the JSON document itself does
not). `tests/api/test_openapi.py` keeps the document and
`api.app.create_app()` in lock-step: every route must be in the spec and vice
versa, and live responses are validated against the response schemas.

The scene body is not described twice: the spec `$ref`s the frozen
`spec/scene.schema.json` (prose in `docs/scene-spec.md`).

Start a server:

```bash
python scripts/serve.py --scene out/scene_test          # root scene + job API on :8000
# or: docker compose up api                              # docker-compose.yml, the `api` service
open http://127.0.0.1:8000/api/docs
```

## Routes

| Group | Method / path | Purpose |
|---|---|---|
| Jobs | `POST /api/jobs` | multipart upload (`archive`, `tier`) → `202` + job record |
| | `GET /api/jobs` | every job, newest first |
| | `GET /api/jobs/{job_id}` | poll one job |
| | `DELETE /api/jobs/{job_id}` | remove the job and its directory → `204` |
| Job-scoped viewer | `GET /jobs/{job_id}` | `307` → `/jobs/{job_id}/` |
| | `GET /jobs/{job_id}/` | the viewer page for that job |
| | `GET /jobs/{job_id}/scene.json` · `/eval.json` | scene contract, evaluation report |
| | `GET /jobs/{job_id}/meshes/{id}.glb` · `/hulls/{stem}.glb` | glTF binary assets |
| | `GET /jobs/{job_id}/events` | SSE: `object_added`, `job_status`, `heartbeat` |
| Legacy root | `GET /` `/scene.json` `/eval.json` `/meshes/{id}.glb` `/hulls/{stem}.glb` `/events` | single-scene mode (`--scene` / `SOBA_SCENE_DIR`) |
| Ops | `GET /metrics` | Prometheus text (`501` without the `telemetry` extra) |
| | `GET /api/openapi.json` · `GET /api/docs` | this API's description |

Everything else is served from the committed `frontend/dist/` by a static
mount that runs last.

## Authentication

Two modes, chosen by `SOBA_API_KEYS` at startup.

| | `SOBA_API_KEYS` unset (default) | `SOBA_API_KEYS=name:key[,name:key:loadtest]` |
|---|---|---|
| `/api/*`, `/jobs/*` | open (one startup warning) | `Authorization: Bearer <key>` required |
| legacy root routes, static assets | open | open, unless `SOBA_AUTH_LEGACY=1` |
| `/api/openapi.json`, `/api/docs` | open | open, unless `SOBA_AUTH_LEGACY=1` |
| `/metrics` | open | **open** (known limitation, `STATUS.md`) |

A missing or wrong key answers `401 unauthorized` with
`WWW-Authenticate: Bearer realm="soba"` (`, error="invalid_token"` appended
when a token was sent). The `name` half is the caller's identity in logs and
rate-limit buckets; a key flagged `:loadtest` is exempt from rate limiting.
Keys are compared in constant time.

A browser cannot attach a bearer header to a page load, so in keyed mode the
viewer under `/jobs/{job_id}/` needs a header-injecting proxy in front of it.

## Upload contract

`POST /api/jobs`, `multipart/form-data`:

| part | required | value |
|---|---|---|
| `archive` | yes | a **zip**, **tar.gz** or plain **tar** file |
| `tier` | no (default `2`) | `1`–`4`, the pipeline tier from `config/pipeline.yaml` |

The archive holds either a **PerceptionBundle** (`manifest.json` +
`intrinsics.json` + `frames/NNNNN/{rgb.jpg, depth.png}`) or a **TUM RGB-D**
sequence (`rgb.txt` + `depth.txt` + `rgb/` + `depth/`), at the root or inside a
single top-level directory. The format is sniffed from magic bytes, never from
the file name or the part's `Content-Type`; video files are refused with `415`
(maintainer decision: uploads are archives, not MP4). Validation and extraction
run inside the request, so a bad archive is answered with a `4xx` rather than a
job that fails later. A TUM sequence needs an imaging backend (OpenCV /
imageio) on the server; otherwise `422 invalid_upload` says so.

Caps (all environment variables, defaults in `src/api/security/upload_validation.py`):
`SOBA_MAX_UPLOAD_MB` (2048, checked against `Content-Length` before the body is
read, and enforced on a chunked body as it arrives), `SOBA_MAX_EXTRACTED_MB`,
`SOBA_MAX_MEMBER_MB`, `SOBA_MAX_MEMBERS`, `SOBA_MAX_DECOMPRESSION_RATIO`,
`SOBA_MAX_FRAMES`, `SOBA_MAX_FRAME_PIXELS`.

## Job lifecycle

```
queued ──► running:<stage> ──► done
   │              │
   └──────────────┴──────────► failed
```

`GET /api/jobs/{job_id}` returns the record (`api.jobs.store.JobRecord`):

```json
{
  "id": "3f9c2a7b81d04e5c",
  "tier": 2,
  "source_format": "bundle",
  "state": "running",
  "stage": "assembling",
  "error": null,
  "upload_name": "office_3.zip",
  "bundle_dir": "/srv/soba/out/jobs/3f9c2a7b81d04e5c/extracted",
  "created_at": 1789200000.12,
  "updated_at": 1789200004.5,
  "history": [
    {"status": "queued", "at": 1789200000.12},
    {"status": "running:validating", "at": 1789200001.0},
    {"status": "running:assembling", "at": 1789200004.5}
  ],
  "status": "running:assembling",
  "scene_url": "/jobs/3f9c2a7b81d04e5c/"
}
```

`status` is `queued`, `running:<stage>` (`running` alone if the worker set no
stage), `done` or `failed`; `state` is the same without the stage. Terminal
states are frozen; every accepted transition is appended to `history`, so a
slow poller still sees the whole path. Stage names come from the worker (the
mock worker reports `validating` and `assembling`; the real pipeline worker
reports its own). When `status` is `done`, open `scene_url` in a browser or
fetch `/jobs/{job_id}/scene.json`. The job-scoped scene routes are
*partial-safe*: before the worker has written `scene.json` they answer `200`
with an empty scene, so the viewer can open early and fill in from `/events`.

`DELETE` removes the record and the whole job directory (upload, extracted
bundle, scene output). There is no other retention policy yet.

## Server-sent events

`GET /events` (root scene) and `GET /jobs/{job_id}/events` are
`text/event-stream`; each message is `event: <name>` + `data: <json>`.

| event | data | root stream | job stream |
|---|---|---|---|
| `object_added` | `{"id": "<objects[].id>"}` | one per object in `scene.json`, replayed on connect | replayed on connect, then one per object the worker writes (polled every 0.5 s) |
| `job_status` | `{"status", "state", "stage", "error"}` | — | the current status on connect, then on every change until the job is terminal |
| `heartbeat` | `{}` | every 15 s while connected | every 15 s once the job is terminal |

The replay is spaced by `SOBA_SSE_DELAY` (default 0.4 s). At most
`SOBA_SSE_MAX_PER_IP` (default 8) streams may be open per client IP; the next
one gets `429 too_many_streams` with `Retry-After: 5`.

## Rate limiting

A general token bucket (`SOBA_RATE_LIMIT_RPS` / `SOBA_RATE_LIMIT_BURST`,
default 100 / 200) applies to every request, keyed by API-key name when a
valid bearer key is present, else by client IP (`X-Forwarded-For` is honoured
only with `SOBA_TRUST_PROXY=1`). `POST /api/jobs` has a second, stricter
bucket (`SOBA_RATE_LIMIT_UPLOAD_RPS` / `_BURST`, default 1 / 10). Exceeding
either answers `429 rate_limited` with a whole-seconds `Retry-After`. Buckets
are per process (known limitation).

## Errors

Every error under `/api/*`, every `401` / `429` / `501`, and every job-scoped
`404` for an unknown job use one envelope:

```json
{"error": {"code": "unknown_job", "message": "no job '0123456789abcdef'"}}
```

| Status | `code` | When |
|---|---|---|
| 400 | `missing_archive` | multipart body without an `archive` file part |
| 400 | `bad_multipart` | body is not parseable multipart |
| 401 | `unauthorized` | keyed mode, bearer missing or wrong |
| 404 | `unknown_job` | `job_id` malformed or not in the store (also after `DELETE`) |
| 413 | `upload_too_large` | body over `SOBA_MAX_UPLOAD_MB`, archive expands past `SOBA_MAX_EXTRACTED_MB`, or more than `SOBA_MAX_MEMBERS` members |
| 413 | `member_too_large` | one archive member over `SOBA_MAX_MEMBER_MB` |
| 415 | `unsupported_archive` | not zip / tar.gz / tar by magic bytes (video lands here) |
| 422 | `bad_tier` | `tier` not one of 1–4 |
| 422 | `invalid_upload` | zip-slip / absolute path, link or special member, corrupt archive, no bundle or TUM layout, TUM without an imaging backend, bundle without frames |
| 422 | `decompression_ratio` | inflates past `SOBA_MAX_DECOMPRESSION_RATIO` |
| 422 | `nested_archive` | an archive inside the archive |
| 422 | `bad_manifest` | `manifest.json` rejected (or fps <= 0) |
| 422 | `too_many_frames` | frame count over `SOBA_MAX_FRAMES` |
| 422 | `bad_depth_dtype` | a sampled `depth.png` is missing or not a 16-bit greyscale PNG |
| 422 | `frame_too_large` | a sampled `depth.png` exceeds `SOBA_MAX_FRAME_PIXELS` |
| 429 | `rate_limited` | general or upload bucket empty (`Retry-After`) |
| 429 | `too_many_streams` | more than `SOBA_SSE_MAX_PER_IP` open `/events` streams from one IP |
| 500 | `multipart_unavailable` | `python-multipart` not installed (`pip install -e '.[api]'`) |
| 501 | `metrics_unavailable` | `prometheus_client` not installed (`pip install -e '.[telemetry]'`) |

The legacy scene routes (and their job-scoped mirrors once the job exists)
keep their pre-job-API **plain-text** bodies for byte-for-byte compatibility
with the viewer and `scripts/verify_browser.js`: `400 bad object id` /
`bad hull name`, `404 mesh not found` / `hull not found` /
`frontend not built (...)`. Those are sent without a `Content-Type` header.

Every response also carries `Cache-Control: no-store, must-revalidate`,
`X-Content-Type-Options: nosniff`, `Content-Security-Policy: frame-ancestors
'none'`, `X-Frame-Options: DENY` and `Referrer-Policy: same-origin`; CORS
headers appear only when `SOBA_CORS_ORIGINS` is set.

## curl walk-through

```bash
BASE=http://127.0.0.1:8000
AUTH=()                                   # open mode
# AUTH=(-H "Authorization: Bearer $KEY")  # keyed mode

# 1. upload (zip of a PerceptionBundle or a TUM sequence), tier 2
JOB=$(curl -sS "${AUTH[@]}" -F archive=@bundles/office_3.zip -F tier=2 \
      $BASE/api/jobs | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')

# 2. poll
curl -sS "${AUTH[@]}" $BASE/api/jobs/$JOB | python -m json.tool

# 3. or stream status + objects until the job is terminal
curl -sS -N "${AUTH[@]}" $BASE/jobs/$JOB/events

# 4. the result
curl -sS "${AUTH[@]}" $BASE/jobs/$JOB/scene.json > scene.json
curl -sS "${AUTH[@]}" -o dining_table_01.glb $BASE/jobs/$JOB/meshes/dining_table_01.glb
xdg-open $BASE/jobs/$JOB/                 # the viewer (keyed mode: via a header-injecting proxy)

# 5. clean up
curl -sS "${AUTH[@]}" -X DELETE -o /dev/null -w '%{http_code}\n' $BASE/api/jobs/$JOB   # 204

# errors come back as one envelope
curl -sS -F archive=@room.mp4 $BASE/api/jobs
# {"error":{"code":"unsupported_archive","message":"upload must be a zip, tar.gz or tar archive ..."}}

# the description itself
curl -sS $BASE/api/openapi.json | python -m json.tool | head
curl -sS $BASE/metrics | head
```

## Keeping the spec honest

`spec/openapi.yaml` is hand-authored (Starlette has no generator). When you
add or change a route in `src/api/`, edit the spec in the same change;
`tests/api/test_openapi.py` fails otherwise:

- the document must validate as OpenAPI 3.1 (`openapi-spec-validator`, in the
  `dev` extra) and every `$ref` must resolve, `./scene.schema.json` included;
- `create_app().routes` (recursively, mounts included) and `paths` must list
  exactly the same path + method pairs; the only undocumented mount is the
  static `frontend/dist` catch-all;
- live responses for the job lifecycle, the upload rejections, keyed-mode
  `401`, `429`, the redirect, `/metrics` and the scene / eval bodies must fit
  the declared status codes, media types, headers and JSON schemas;
- the `ErrorCode` enum must equal the set of codes `src/api` can raise.

If `schemathesis` is installed by hand (it is in no extra) the same file also
fuzzes every documented `GET` route with a handful of examples; the test is
skipped otherwise.
