# 2026-09-12 — Security phase B: API auth, rate limiting, upload hardening

Branch `feat/security-hardening` (PR #7 into `develop`), on top of phase A
and the merged job API + observability phase A. Changes what is runnable:
the API can now be closed with keys, uploads are validated harder, and every
response carries security headers. `STATUS.md` gained one line.

## What was done

- `src/api/security/upload_validation.py` — `UploadLimits.from_env()`
  (`SOBA_MAX_*`), a listing-time `Budget` (per-member, total, member count,
  decompression ratio: per member above 1 MiB and for the whole archive),
  nested-archive and zip-symlink rejection, `check_target_inside()` (the
  write target is `resolve()`d and must stay under the extraction dir),
  `validate_manifest()` (`manifest.json` through `Manifest.from_dict`, fps > 0,
  frame-count cap), `validate_frames()` (frame-dir cap, no symlinked frame
  dirs, a first/last-inclusive sample of `depth.png` must be a 16-bit
  greyscale PNG of bounded resolution, parsed from the IHDR chunk — no image
  decoding, so it runs without OpenCV / Pillow), `check_tum_contents()`
  (rgb.txt / depth.txt line cap), `capped_receive()` (streaming body cap).
- `src/api/jobs/upload_errors.py` — the exception hierarchy, shared by
  `archive.py` and the validator. New codes: 413 `member_too_large`; 422
  `decompression_ratio`, `nested_archive`, `bad_manifest`, `too_many_frames`,
  `bad_depth_dtype`, `frame_too_large`. Existing codes unchanged; the new
  ones subclass `InvalidUpload` / `UploadTooLarge` so old handlers still match.
- `src/api/jobs/archive.py` — same entry point; `validate_and_extract(...,
  limits=)` with the two legacy keyword caps still honoured; the copy loop
  refuses members that stream past their declared size.
- `src/api/security/auth.py`, `ratelimit.py`, `headers.py`,
  `__init__.security_middleware()` — see the env contract below.
- `src/api/app.py` — one import, one call (`*security_middleware()` first in
  the middleware list so CORS is outermost).
- `src/api/routes/jobs.py` — the multipart body goes through
  `capped_receive` (cap = `SOBA_MAX_UPLOAD_MB` + 64 KiB multipart slack),
  413 `upload_too_large` while the body is still arriving.
- `src/api/routes/scene.py` — `served_file()`: `resolve(strict=True)` +
  `is_relative_to(scene_dir)` on `scene.json`, `eval.json`, meshes, hulls.
- Tests: `tests/api/test_upload_validation.py` (35),
  `test_auth_ratelimit.py` (13), `test_headers_cors_sse.py` (17). The shared
  fixture's `depth.png` is now a real 16-bit greyscale PNG header
  (`_fixtures.png_header`); every existing test file is unchanged.
- Docs: audit doc §7 (gap table), root `env.example` (new section),
  `STATUS.md` (one line).

## Env contract

| Variable | Default | Effect |
|---|---|---|
| `SOBA_API_KEYS` | unset | `name:key[,name:key:loadtest]`. Unset = open mode + one startup WARNING. Set = `/api/*` and `/jobs/*` require `Authorization: Bearer <key>` |
| `SOBA_AUTH_LEGACY` | `0` | `1` also closes `/`, `/scene.json`, `/meshes/..`, `/hulls/..`, `/events`, `/eval.json` and the static assets (only meaningful with keys) |
| `SOBA_RATE_LIMIT_RPS` / `_BURST` | `100` / `200` | general token bucket per key (or per IP when anonymous); rps or burst <= 0 disables |
| `SOBA_RATE_LIMIT_UPLOAD_RPS` / `_BURST` | `1` / `10` | second, stricter bucket for `POST /api/jobs` |
| `SOBA_TRUST_PROXY` | `0` | `1` = identity from the rightmost `X-Forwarded-For` hop (the one the trusted proxy added) |
| `SOBA_CORS_ORIGINS` | empty | comma-separated origins or `*`; empty = no CORS headers at all |
| `SOBA_SSE_MAX_PER_IP` | `8` | concurrent `/events` streams per client IP; `0` disables |
| `SOBA_MAX_UPLOAD_MB` | `2048` | request body cap (existing, now enforced while streaming) |
| `SOBA_MAX_EXTRACTED_MB` / `SOBA_MAX_MEMBER_MB` / `SOBA_MAX_MEMBERS` | `8192` / `512` / `200000` | archive caps |
| `SOBA_MAX_DECOMPRESSION_RATIO` | `200` | uncompressed / compressed, per member above 1 MiB and in total |
| `SOBA_MAX_FRAMES` / `SOBA_MAX_FRAME_PIXELS` / `SOBA_DEPTH_SAMPLE` | `5000` / `4096*4096` / `8` | bundle content caps |

Responses: 401 `unauthorized` (+ `WWW-Authenticate: Bearer realm="soba"`,
`error="invalid_token"` when a key was sent), 429 `rate_limited` and 429
`too_many_streams` (+ `Retry-After`), all in the `{"error": {"code",
"message"}}` envelope. Every response carries `X-Content-Type-Options:
nosniff`, `Content-Security-Policy: frame-ancestors 'none'`,
`X-Frame-Options: DENY`, `Referrer-Policy: same-origin`.

## Decisions

- Open by default. Local dev, `tests/test_server.py` and
  `scripts/verify_browser.js` run with nothing configured; the price is one
  WARNING per `create_app()`.
- The rate limiter runs *before* auth (but derives its identity from the
  same key lookup), so failed key guesses burn the caller's per-IP budget
  instead of being free. CORS is outermost so 401/429 carry CORS headers.
- Generous general defaults (100 rps / burst 200): a viewer page load pulls
  `scene.json` + a mesh + hulls per object in one burst and must never trip
  in open mode. Operators lower them per deployment.
- `X-Forwarded-For`: rightmost hop, only with `SOBA_TRUST_PROXY=1`. The
  leftmost value is client-controlled.
- `bad_manifest` is checked *before* `PerceptionBundle.open`, otherwise the
  generic `invalid_upload` from the bundle reader would win.
- `/metrics` (observability phase B) is not under a protected prefix; that
  agent decides its policy. Bearer-only auth means the viewer under
  `/jobs/{id}/` needs a header-injecting proxy when keys are set; a
  cookie/session scheme is a follow-up.
- Buckets and SSE counters are per-process memory. A Redis-backed limiter is
  the follow-up once there is more than one API replica.
- `SOBA_RUNPOD_URL` https enforcement (audit G2) is runpod-orchestration-
  agent's, in `generative.py`, in parallel; not touched here.

## Verification

- `pytest -q --continue-on-collection-errors`: 294 passed, 42 failed, 7
  errors — the failures and errors are the box's missing-`open3d`/`cv2`
  baseline (229 / 42 / 7 before this branch); none in `tests/api` or
  `tests/test_server.py`.
- `SOBA_PYTHON=.venv/bin/python NODE_PATH=~/tmp/pptr/node_modules node
  scripts/verify_browser.js --scene out/scene_test`: ALL CHECKS PASSED (8/8)
  with no security env set — open mode is unchanged for the browser.
- `ruff check src/api tests/api`: clean.

## Caveats

- Starlette's `TestClient` buffers whole responses, so any test that opens a
  live `/events` stream must go through a real uvicorn server
  (`test_sse_cap_through_a_real_server`, like the existing SSE test); a
  `TestClient.get("/events")` with a valid key hangs forever.
- `zipfile.writestr` leaves the external type bits at 0; the symlink check
  only rejects an explicit non-file, non-dir type.
