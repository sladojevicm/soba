# Secrets and configuration audit — 2026-09-11

Phase A of the security-hardening track (`.claude/AGENTS.md` §5). Scope: every
environment variable read anywhere in `src/`, `scripts/`, `deploy/` and
`frontend/src/`, how the two real secrets flow, what is missing today, and a
threat-model sketch for the upload API that phase B will add.

**Method.** Grep over the tree for `os.environ`, `os.getenv`, `${VAR:-…}`,
`import.meta.env` and `process.env`; manual read of the key-handling code
(`src/reconstruction/generative.py` `RunPodEngine` / `make_engine`,
`src/scene/vlm_claude.py` `make_backend`); `.gitignore` and `git ls-files` for
what is and is not tracked; gitleaks over the full history. No `.env*` file was
opened at any point — the audit is from code and the ignore rules only.

## 1. Environment-variable inventory

"How read" is `os.environ.get(name, default)` unless stated. Line numbers are
at `develop@9cbd012`.

### Secrets

| Variable | Where | Purpose | Secret | Default | How read |
|---|---|---|---|---|---|
| `RUNPOD_API_KEY` | `src/reconstruction/generative.py:1207` | RunPod serverless auth | **yes** | none (engine skipped) | `environ.get`, passed to `RunPodEngine(api_key)` |
| `ANTHROPIC_API_KEY` | `src/scene/vlm_claude.py:196` (presence check); `scripts/benchmark_physics.py:578` (message text) | Claude VLM physics backend | **yes** | none (lookup fallback) | presence-checked only; the `anthropic` SDK reads it itself (`vlm_claude.py:116`, `anthropic.Anthropic()` with no argument) |

### Semi-secret identifiers (not credentials, but not for public docs)

| Variable | Where | Purpose | Default | How read |
|---|---|---|---|---|
| `RUNPOD_GEN_ENDPOINT_ID` (alias `RUNPOD_ENDPOINT_ID`) | `generative.py:1208` | image-to-3D endpoint | none | `environ.get` |
| `RUNPOD_COMPLETION_ENDPOINT_ID` | `generative.py:1209` | shape-completion endpoint | none | `environ.get` |

### Non-secret configuration — `src/`

| Variable | Where | Purpose | Default |
|---|---|---|---|
| `RUNPOD_GEN_MODEL` | `generative.py:1213` | model on the RunPod gen endpoint | per-tier (`gen_model_for_tier`) |
| `RUNPOD_COMPLETION_MODEL` | `generative.py:1214` | model on the RunPod completion endpoint | `pointr` |
| `SOBA_RUNPOD_TIMEOUT` | `generative.py:616` | runsync timeout (s) | `900` |
| `SOBA_RUNPOD_URL` | `generative.py:632` | **full URL override** for runsync (dev tunnel) | unset → `https://api.runpod.ai/v2/{endpoint}/runsync` |
| `SOBA_LOCAL_GPU` | `generative.py:1216,1224` | `0` disables local CUDA engines | `1` |
| `SOBA_GEN_MODEL` | `generative.py:1226` | local generative model | per-tier |
| `SOBA_COMPLETION_MODEL` | `generative.py:1221,1229` | local completion model | `patchcomplete` |
| `SOBA_GEN_MIN_SOLID` | `generative.py:391` | min solidity for generated meshes | config value |
| `SOBA_GEN_CLEAN` | `generative.py:414,497` | `0` skips generated-mesh cleaning | `1` |
| `SOBA_GEN_STRICT` | `generative.py:426` | `0` keeps every generation | `1` |
| `SOBA_GEN_FACES` / `SOBA_TRIPOSG_FACES` | `generative.py:1052-1053` | decimation cap | `40000` |
| `SOBA_TRIPOSG_HOME` / `_WEIGHTS` / `SOBA_RMBG_WEIGHTS` | `generative.py:873,897,898` | TripoSG checkout + weights | `~/soba/TripoSG`, derived |
| `SOBA_TRIPOSG_STEPS` / `_CFG` / `_SEED` | `generative.py:1069-1071` | sampler knobs | `50` / `7.0` / `42` |
| `SOBA_TRIPOSG_FLASH` / `_DENSE` / `_HIER` | `generative.py:1075,1084,1085` | diso + resolution knobs | `0` / model defaults |
| `SOBA_HUNYUAN_HOME` | `generative.py:980,1114` | Hunyuan3D checkout | `~/soba/Hunyuan3D-2.1` |
| `SOBA_HUNYUAN_MODEL` / `_STEPS` / `_SEED` | `generative.py:1120,1148,1149` | variant + sampler knobs | `tencent/Hunyuan3D-2.1` / `30` / `42` |
| `SOBA_HUNYUAN_PAINT` / `SOBA_PAINT_VIEWS` / `SOBA_PAINT_RES` | `generative.py:920,1002,1003` | texture-paint stage | off / model defaults |
| `SOBA_MAST3R_HOME` / `_STRIDE` / `_MAX_IMAGES` | `src/reconstruction/slam.py:198-202` | MASt3R checkout, stride, memory cap | `~/soba/mast3r` / `3` / `24` |
| `SOBA_PATCHCOMPLETE_HOME` | `src/reconstruction/patchcomplete_completion.py:29` | PatchComplete checkout | `~/soba/PatchComplete` |
| `POINTR_HOME` | `src/reconstruction/pointr_completion.py:28` | PoinTr checkout | `~/projects/soba/PoinTr` |
| `SOBA_PCA_ALIGN` | `pointr_completion.py:169` | `0` disables yaw canonicalisation | `1` |
| `SOBA_COMPC_TIMEOUT` | `src/reconstruction/compc_completion.py:41` | subprocess timeout | module default |
| `SOBA_COMPC_CMD` | `compc_completion.py:49` | **command template executed as a subprocess** (`{input}` / `{output}` tokens) | none (required for ComPC) |
| `SOBA_DUMP_PLY` | `src/scene/assembler.py:252` | dump per-object PLY | off |
| `SOBA_DEOVERLAP` | `assembler.py:430` | `0` disables placement de-overlap | `1` |
| `SOBA_VLM` | `src/scene/vlm_claude.py:194` | `0` disables Claude even with a key | `1` |
| `SOBA_VLM_MODEL` | `vlm_claude.py:200` | model override | `config/pipeline.yaml` `vlm.model` |
| `SOBA_SCENE_DIR` | `src/server.py:75` (read), `:203` (set by `__main__` from `--scene`) | scene directory for `create_app()` | `out/scene_test` fallback |
| `SOBA_SSE_DELAY` | `server.py:59` | demo SSE cadence | `0` |

### Non-secret configuration — `scripts/` and `deploy/`

| Variable | Where | Purpose | Default |
|---|---|---|---|
| `SOBA_TRIPOSG_SEED`, `SOBA_HUNYUAN_SEED` | `scripts/run_assemble.py:228-229` (read), `:252-253` (set from `--seed`) | reproducible generation | `42` |
| `SOBA_BENCH_DATA` | `scripts/benchmark_physics.py:63` | benchmark data root | `~/projects/soba/data/benchmarks` |
| `SOBA_TRIPOSG_HOME`, `SOBA_TRIPOSG_FLASH`, `SOBA_GEN_MODEL`, `SOBA_PATCHCOMPLETE_HOME` | `scripts/ablation_routing.sh:11-14` | exported for the ablation run | fixed paths |
| `SOBA_SRC` | `deploy/runpod/generative_handler.py:37` | repo `src/` on the serverless worker | `../../src` relative to the handler |
| `COMPC_HOME`, `WORKDIR` | `deploy/runpod/compc_runner.py:28,30` | ComPC checkout lookup | `/workspace/ComPC`, `~/soba/ComPC` |
| `REPO_URL`, `REPO_BRANCH`, `WORKDIR`, `REPO_DIR`, `POINTR_HOME`, `SETUP_POINTR`, `SETUP_COMPC`, `SETUP_TRIPOSG` | `deploy/runpod/bootstrap.sh:20-28` | pod bootstrap (`${VAR:-default}`) | public GitHub URL, `master`, `/workspace`, `0` |
| `WORKDIR`, `COMPC_HOME`, `COMPC_ENV`, `MAMBA_ROOT`, `REPO_DIR` | `deploy/runpod/setup_compc.sh:22-26` | ComPC env build | under `/workspace` |
| `HUNYUAN_HOME`, `HUNYUAN_MODEL`, `HUNYUAN_REPO_URL`, `SETUP_HUNYUAN_PAINT` | `deploy/runpod/setup_hunyuan3d.sh:22-24,50` | Hunyuan setup | public repo, `/workspace` |
| `TRIPOSG_HOME`, `TRIPOSG_REPO_URL` | `deploy/runpod/setup_triposg.sh:18-19` | TripoSG setup | public repo, `/workspace` |
| `SCENE`, `STRIDE`, `MAX_FRAMES`, `TIER`, `REBUILD` | `deploy/runpod/run_pipeline.sh:32-36` | pipeline driver | `office_3`, `20`, `100`, `2`, `0` |
| `PYTHONPATH` | `deploy/runpod/env.example:8`, `run_pipeline.sh:30` | import path | `src` |

### Frontend

`frontend/src/main.tsx:10` and `frontend/src/store.ts:40` read only
`import.meta.env.DEV` (Vite build-time flag; gates the kitchen-sink route and
the `window.__soba` debug bridge). Nothing else from the environment is
inlined into the bundle, and no `VITE_*` variable exists. `frontend/dist/` is
built from that source and contains no key material (gitleaks over the tree
confirms it).

## 2. Secret handling

**Nothing is hardcoded.** Every credential is read from the process
environment at call time with no fallback literal. gitleaks (v8.30.1, default
rules) over the full history: `122 commits scanned … no leaks found`; over the
working tree (including `frontend/dist/`): `no leaks found`. The `dummy`
placeholders in `docs/log/` did not even trigger a finding.

**Where the RunPod key flows.** `make_engine()` (`generative.py:1206-1231`)
reads `RUNPOD_API_KEY`; if set together with an endpoint id it constructs
`RunPodEngine(key, …)`, which stores it on `self.api_key`. The only use is
`_runsync()` (`generative.py:619-640`): a `urllib.request.Request` to
`https://api.runpod.ai/v2/{endpoint}/runsync` with header
`Authorization: Bearer {api_key}` and a JSON body. The key is never logged,
never written to the bundle or the scene, and never returned in a response.
HTTPS is guaranteed by the hardcoded `BASE_URL` — except when
`SOBA_RUNPOD_URL` overrides the whole URL (see gap G2).

**Where the Anthropic key flows.** `make_backend()` (`vlm_claude.py:186-205`)
only checks that `ANTHROPIC_API_KEY` is non-empty; it never reads the value.
`ClaudeBackend` calls `anthropic.Anthropic()` with no arguments
(`vlm_claude.py:116`), so the SDK picks the key up from the environment and
sends it over HTTPS to the Anthropic API. The key is not passed through any
Soba function signature, config file, log line or bundle.

**Keys never reach the browser.** The viewer is static and talks only to
`src/server.py`, whose routes are `/`, `/scene.json`, `/eval.json`,
`/meshes/{id}.glb`, `/hulls/{stem}.glb`, `/events` and the static
`frontend/dist/`. None of them reads a secret; the server process does not need
either key. The generation and VLM steps run offline in `run_assemble.py`,
before anything is served.

**Pod side.** `deploy/runpod/env.example` is the pod template; its secret
lines are blank (`export ANTHROPIC_API_KEY=` etc.). Copies go to `.env`, which
`.gitignore` excludes (`.env`, `.env.*`, `*.local`, with `!.env.example`
re-included). `git ls-files` shows only `deploy/runpod/env.example`; no `.env*`
has ever been tracked (gitleaks history scan covers that too). The new root
`env.example` is the developer-side template and cross-references the pod one.
It uses the undotted name already used by `deploy/runpod/env.example` because
the project's `.claude/settings.json` denies every tool access to `.env.*`,
which includes `.env.example`; `git mv env.example .env.example` is a
maintainer decision (`.gitignore` already whitelists that name).

## 3. Gaps and recommendations (phase B input)

Ordered by how directly they matter once the upload / job API exists.

- **G1 — No authentication anywhere.** `src/server.py` accepts every request;
  it was designed as a localhost demo server. Phase B: bearer API keys from
  env (`SOBA_API_KEYS`, comma-separated), `hmac.compare_digest` for the
  comparison, a per-key identity attached to the request for rate limiting
  and audit logs. Unauthenticated requests → 401.
- **G2 — `SOBA_RUNPOD_URL` sends the bearer token to any host.**
  `generative.py:632` substitutes the full URL with no scheme or host check,
  so a stray value (or, in a hosted setting, an env var an attacker can
  influence) exfiltrates `RUNPOD_API_KEY`. Recommendation: require
  `https://` unless the host is `localhost`/`127.0.0.1`, or replace the
  override with a `SOBA_RUNPOD_BASE_URL` allow-list; never expose it through
  any API. Same class: the `*_REPO_URL` and `REPO_URL` variables in
  `deploy/` clone whatever they name — fine for an operator shell, not for
  anything user-facing.
- **G3 — `SOBA_COMPC_CMD` is an arbitrary command template** executed by
  `compc_completion.py`. It is a trusted-operator knob and must stay one: the
  job API must not let request data reach it, and the worker should run with
  the ComPC path fixed by deployment config rather than by a per-job env.
- **G4 — No request body size limit.** Starlette/uvicorn impose none by
  default and the current routes are GET-only, so today it is moot; the upload
  route changes that. Set `uvicorn --limit-max-request` equivalents at the
  proxy *and* enforce a streaming cap in the handler (count bytes as they are
  read, abort past `SOBA_MAX_UPLOAD_BYTES`), because a proxy-only limit is
  bypassed by direct access.
- **G5 — Path safety rests on two regexes.** `_ID_RE = ^[a-z0-9_]+$` and
  `_HULL_RE` (`server.py:50-51`) are the only thing keeping `/meshes/{id}` and
  `/hulls/{stem}` inside the scene directory. They are correct (no dots, no
  slashes), but they are the whole defence. Phase B should keep the allow-list
  style, add `Path.resolve()` + `is_relative_to(scene_dir)` as a second check
  on every filesystem read, and apply the same to job ids.
- **G6 — `SOBA_SCENE_DIR` is trusted as-is.** Fine for the CLI; in the API,
  scene directories must be derived from a job id under a fixed root, never
  from a client-supplied path.
- **G7 — No CORS policy, no security headers.** Only a `Cache-Control:
  no-store` middleware exists (`server.py:178`). Add an explicit CORS
  allow-list (default: same-origin only), `X-Content-Type-Options: nosniff`,
  `Content-Security-Policy` for the static viewer, `Referrer-Policy`.
- **G8 — SSE has no client cap.** `/events` holds a connection open with a
  15 s heartbeat; a per-IP connection cap belongs with the rate limiter.
- **G9 — Secrets in logs / errors.** `_runsync` raises on non-200 with the
  RunPod error body; nothing prints the header, but phase B should add a
  redaction filter to the logging config before requests carry API keys.
- **G10 — Dependency hygiene.** All audits are clean today (see
  `dependency-audit-2026-09-11.md`); wire `pip-audit` and `npm audit` into CI
  next to gitleaks so that stays true.

## 4. Threat-model sketch — upload API (phase B)

Assets: the operator's `RUNPOD_API_KEY` / `ANTHROPIC_API_KEY`, GPU time (cost),
the worker filesystem, other users' bundles and scenes.

| Threat | Vector | Control |
|---|---|---|
| Zip-slip | archive member named `../../etc/cron.d/x` or an absolute path | extract with a member-by-member allow-list: reject absolute paths, `..`, symlinks/hardlinks, device entries; resolve every target and require `is_relative_to(job_dir)`; never call `extractall` |
| Decompression bomb | small zip inflating to tens of GB, or nested archives | cap total uncompressed size (sum of `ZipInfo.file_size`) and per-member size *before* extracting, cap compression ratio, cap member count, no nested archives, stream to disk with a running byte counter |
| Path traversal in `manifest.json` | frame paths like `../../x.png` | validate through `PerceptionBundle.from_dict`; require frame filenames to match an `_ID_RE`-style pattern and to exist inside the extracted dir |
| Oversized / malformed frames | 100 MP PNGs, wrong depth dtype, float depth, mismatched RGB/depth counts | check magic bytes + MIME, decode with a pixel-count cap (`PIL.Image.MAX_IMAGE_PIXELS`), require depth `uint16`, cap frame count and resolution to what the pipeline is benchmarked on, reject mismatched frame lists |
| Oversized request | multi-GB upload | streaming size cap in the handler (G4) plus proxy limit |
| SSRF / credential exfil via override vars | any path where job input could set `SOBA_RUNPOD_URL`, `*_HOME`, `SOBA_COMPC_CMD`, `REPO_URL` | never map request fields to env; workers get a fixed environment; G2/G3 hardening |
| Resource exhaustion | many concurrent jobs, huge TSDF grids | job queue with a concurrency limit, per-key quotas, timeouts on every subprocess/engine call |
| Cross-job access | reading another job's scene by guessing ids | unguessable job ids (`secrets.token_urlsafe`), per-key ownership check on every job route |
| Malicious mesh output | CoACD / glTF export on adversarial geometry | already sandboxed per object with size gates in `assembler.py`; add wall-clock limits |

## 5. Pre-commit

`.pre-commit-config.yaml` runs `gitleaks` (v8.30.1) and `ruff` (v0.16.3,
matching the venv; no `--fix`, no `ruff-format` because 64 files are not
format-clean yet). One-time setup per clone:

```bash
source .venv/bin/activate
uv pip install --python .venv/bin/python pre-commit   # or: pip install pre-commit
pre-commit install
pre-commit run gitleaks --all-files                    # should print "Passed"
```

Caveat: `ruff check` reports 127 findings on `develop` today, so
`pre-commit run --all-files` is red on the ruff hook until a separate lint
clean-up lands; on ordinary commits the hook only sees staged files.

## 6. Secret-scan record

```
$ gitleaks git --no-banner --redact .
INF 122 commits scanned.
INF scanned ~13516290 bytes (13.52 MB) in 3.32s
INF no leaks found

$ gitleaks dir --no-banner --redact .
INF scanned ~20678485 bytes (20.68 MB) in 1.2s
INF no leaks found
```
