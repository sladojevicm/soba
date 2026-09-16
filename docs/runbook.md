# Soba deployment runbook

_Written 2026-09-14 from `develop@8dcafef` (everything from PRs #7–#14). Every
fact below names the file it comes from. Every command is either marked
**executed here** (this WSL box, Docker Desktop, no GPU) with what was
observed, or **not executed here** with what it needs (GPU machine, pod,
RunPod key, registry). Nothing GPU-side is inferred (CLAUDE.md invariant 7).
Numbers in `BENCHMARK.md` and `docs/loadtest.md` are generated; this page
never restates them._

Companion pages: `docs/api.md` (routes, auth, errors), `docs/loadtest.md`
(API-layer numbers), `docs/model-pins.md` (what is and is not pinned),
`docs/security/secrets-audit.md` (every env var), `docs/ci-probe.md` (what CI
proves), `deploy/runpod/README.md` (pod-specific detail), `STATUS.md`
(current state and known limitations).

## 1. Overview and architecture

Soba turns an uploaded PerceptionBundle or TUM archive into a physics-enabled
`scene.json` plus glTF meshes, served to a browser viewer. In production there
are four moving parts plus one optional one:

```
                 HTTPS (TLS + bearer header injection for the viewer)
 browser / CLI ──────────────► reverse proxy ──────────────┐
 (scripts/submit_job.py,                                   │
  curl, the viewer page)                                   ▼
                                              ┌────────────────────────┐
                                              │ api  (soba-api image)  │  uvicorn server:app :8000
                                              │  POST /api/jobs        │  src/api/app.py
                                              │  GET  /api/jobs/{id}   │  sqlite job store
                                              │  GET  /jobs/{id}/...   │  out/jobs/jobs.sqlite
                                              │  GET  /metrics         │  frontend/dist served here
                                              └───────┬────────┬───────┘
                                     LPUSH soba:jobs  │        │ bind mount  out/  (uploads,
                                                      ▼        │             extracted bundles,
                                              ┌──────────────┐ │             scene/, cost.json,
                                              │ redis :6379  │ │             run_metrics.json)
                                              └───────┬──────┘ │
                                        BRPOP         │        │
                                                      ▼        ▼
                                              ┌────────────────────────┐
                                              │ worker                 │  python -m orchestration.worker
                                              │  --mode mock (CPU,     │  src/orchestration/worker.py
                                              │    soba-api image)     │  spawns scripts/run_assemble.py
                                              │  --mode real (GPU,     │  in real / gate-only mode
                                              │    soba-pipeline image)│
                                              └───────┬────────────────┘
                                     HTTPS, bearer    │  RUNPOD_API_KEY
                                     /run + /status   ▼
                                     ┌──────────────────────────────────┐
                                     │ RunPod serverless endpoint        │  soba-pipeline image,
                                     │  generative_handler.py            │  entrypoint `serverless`
                                     │  TripoSG (tiers 1–2)              │  weights on a network
                                     │  Hunyuan3D 2.1 (tiers 3–4)        │  volume under /workspace
                                     └──────────────────────────────────┘

 optional: a RunPod *pod* (deploy/runpod/bootstrap.sh) = a GPU host that runs
 the worker in --mode real, or the whole pipeline by hand (run_pipeline.sh).
```

| Component | Image / process | Source of truth |
|---|---|---|
| api | `docker/api.Dockerfile` → `uvicorn server:app`, non-root uid 10001, `HEALTHCHECK` on `/scene.json`, serves the committed `frontend/dist/` (no frontend container, maintainer decision 2) | `src/api/app.py`, `src/server.py` |
| redis | `redis:7-alpine` (compose), one list `soba:jobs` | `src/api/jobs/queue_redis.py` |
| worker | `python -m orchestration.worker --mode mock\|gate-only\|real`; the API image in mock mode, the pipeline image in real mode | `src/orchestration/worker.py`, `src/api/jobs/worker_local.py` |
| RunPod serverless endpoint | `docker/pipeline.Dockerfile` entrypoint `serverless` → `deploy/runpod/generative_handler.py` | `src/reconstruction/generative.py` (`RunPodEngine`) |
| GPU pod (optional) | `deploy/runpod/bootstrap.sh` + `run_pipeline.sh`, or the pipeline image with `--gpus all` | `deploy/runpod/README.md` |

The job store (`out/jobs/jobs.sqlite`) is the source of truth; Redis only
carries job ids (`docs/log/2026-09-11-job-api.md`). The API and the worker
must therefore share **the same jobs directory at the same absolute path**
(the record stores the extracted bundle path as the API saw it,
`src/api/routes/jobs.py` `bundle_dir=str(res.root)`; the worker opens it
verbatim, `src/api/jobs/worker_local.py`).

## 2. Prerequisites

| Where | Needs |
|---|---|
| Build host / API host | Docker Engine 24+ with compose v2 (this box: Docker Desktop 29.3.1, compose 5.1.1 — `docs/loadtest.md`), ~1 GB for `soba-api`, network access to PyPI and (for the frontend guard) npm |
| Pipeline image build | ~15 GB free disk (`pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime` base is ~7 GB, `docker/pipeline.Dockerfile`); CI frees runner disk first (`.github/workflows/ci.yml` `pipeline-image` job) |
| GPU worker host | NVIDIA driver + `nvidia-container-toolkit`; a CUDA-capable Open3D wheel (see gate G1 in §6) |
| RunPod | an account with credit; a **network volume** mounted at `/workspace` holding the model checkouts and weights (`deploy/runpod/setup_triposg.sh`, `setup_hunyuan3d.sh`); a serverless endpoint (§5.4) |
| Secrets | `RUNPOD_API_KEY`, endpoint ids, optional `ANTHROPIC_API_KEY`, `SOBA_API_KEYS`, setup-time `HF_TOKEN` (§4.2) |
| Operator laptop | Python 3.11 venv with `pip install -e ".[serve,api,dev]"` for `scripts/submit_job.py` (stdlib only, actually needs nothing) and `pytest`; Node 20 + puppeteer outside the repo for `scripts/verify_browser.js` (`CLAUDE.md`) |

Executed here: `docker version` → server 29.3.1; `/home/mikas/soba/.venv/bin/python -m pytest -q --continue-on-collection-errors` → **401 passed, 40 failed, 14 skipped, 7 errors** (the box's missing-`open3d` baseline, unchanged; `STATUS.md`).

## 3. Build and publish images

Three Dockerfiles, all built from the repo root (`Makefile` `build-*` targets):

| Image | File | What it is |
|---|---|---|
| `soba-api` | `docker/api.Dockerfile` | `python:3.11-slim`, `pip install -e ".[serve,api,telemetry,worker]"` (`SOBA_EXTRAS` build arg), `PYTHONPATH=/app/src`, copies `spec/ config/ scripts/ frontend/dist/`, uid 10001, `HEALTHCHECK` GET `/scene.json` every 30 s, `CMD uvicorn server:app --host 0.0.0.0 --port 8000`. Also the CPU worker image (`docker-compose.yml` `worker` service). |
| `soba-pipeline` | `docker/pipeline.Dockerfile` | `ARG BASE_IMAGE=pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime`, apt `git libgl1 libglib2.0-0`, `pip install -e ".[recon,serve,dev,api,telemetry,worker]" coacd runpod`, `ENTRYPOINT pipeline-entrypoint` with modes `worker` (default) · `serverless` · `assemble <args>` · `serve` · `check` · `shell` (`docker/pipeline-entrypoint.sh`). `/workspace` is a `VOLUME` for model checkouts; weights are never baked. Build with `--build-arg BASE_IMAGE=pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel` for the serverless variant (TripoSG `diso` and Hunyuan3D `hy3dshape` compile CUDA extensions, comment block in the Dockerfile). **Runs as root** (no `USER` line — finding, §14). |
| `soba-frontend-check` | `docker/frontend-check.Dockerfile` | CI guard only: `npm ci && npm run build` in `node:20` and `diff -r` against the committed `frontend/dist/`; nothing is served from it. |

### 3.1 Build

```bash
# executed here 2026-09-13: exit 0, image soba-api:local 00e7d1ce26c2 (331 MB)
docker build -f docker/api.Dockerfile -t soba-api:local .          # = make build-api

# executed here 2026-09-14: exit 0 (the diff stage was a cached layer from the 2026-09-13 build; a failing diff cannot be cached)
docker build -f docker/frontend-check.Dockerfile --target check -t soba-frontend-check:local .   # = make frontend-check

# NOT executed here: 7 GB base; CI builds it on every push (ci.yml pipeline-image job).
docker build -f docker/pipeline.Dockerfile -t soba-pipeline:local .     # = make build-pipeline
docker build -f docker/pipeline.Dockerfile \
  --build-arg BASE_IMAGE=pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel \
  -t soba-pipeline:local-devel .                                         # serverless variant
```

### 3.2 Tag scheme and registry

**No registry push exists today**: `.github/workflows/ci.yml` builds all three
images with `push: false` (tags `soba-api:ci`, `soba-pipeline:ci`) and
`docker-compose.yml` pins `image: soba-api:local` / `soba-pipeline:local`.
CI proves the images build; it does not publish them. Publish by hand, from a
tagged commit, to GHCR (recommended: same org as the repo, free for public
images, `GITHUB_TOKEN` works from Actions when the maintainer later adds a
push job):

```
ghcr.io/sladojevicm/soba-api:<version>        e.g. v0.3.0     (release tag, §13)
ghcr.io/sladojevicm/soba-api:<git sha>        e.g. 8dcafef    (every published build)
ghcr.io/sladojevicm/soba-pipeline:<version>   runtime base
ghcr.io/sladojevicm/soba-pipeline:<version>-devel   serverless variant (nvcc)
```

Never publish `latest`; the deploy step below names an explicit tag so a
rollback is a tag change (§11).

```bash
# NOT executed here: no registry credentials on this box, and no push job exists.
VERSION=v0.3.0; SHA=$(git rev-parse --short HEAD)
echo "$GHCR_PAT" | docker login ghcr.io -u sladojevicm --password-stdin     # a PAT with write:packages
for img in soba-api soba-pipeline; do
  docker tag $img:local ghcr.io/sladojevicm/$img:$VERSION
  docker tag $img:local ghcr.io/sladojevicm/$img:$SHA
  docker push ghcr.io/sladojevicm/$img:$VERSION
  docker push ghcr.io/sladojevicm/$img:$SHA
done
```

On the deploy host, pull and re-tag to the name compose expects (compose has
no tag variable — flagged in §14):

```bash
docker pull ghcr.io/sladojevicm/soba-api:$VERSION && docker tag ghcr.io/sladojevicm/soba-api:$VERSION soba-api:local
```

## 4. Configure

### 4.1 Environment matrix by component

Defaults and the full inventory: `env.example` (developer side),
`deploy/runpod/env.example` (pod side), `docs/security/secrets-audit.md`
(every variable with file:line). Only the deployment-relevant ones are here.

| Variable | api | worker (mock / real) | RunPod endpoint | pod | Notes / source |
|---|---|---|---|---|---|
| `SOBA_JOBS_DIR` | **yes** | **yes**, same path | – | – | job store + job dirs; compose: `/data/jobs` on both (`docker-compose.yml`) |
| `SOBA_QUEUE_URL` | **yes** (`redis://…`) | **yes** | – | – | selects the Redis `JobQueue`; the worker exits 2 without it (`src/orchestration/worker.py`) |
| `SOBA_WORKER_INPROC` | **`0`** when an external worker runs | – | – | – | otherwise the API also runs its own in-process worker thread (`src/api/app.py`) |
| `SOBA_WORKER_MODE` / `--mode` | (`mock` default for the in-proc thread) | `mock` · `gate-only` · `real` | – | – | `real` spawns `scripts/run_assemble.py --no-eval` and needs the pipeline image + GPU (`worker_local.py`) |
| `SOBA_SCENE_DIR`, `SOBA_FIXTURE_SCENE` | yes | mock only | – | – | legacy root scene; the scene the mock worker copies |
| `SOBA_API_KEYS` | **yes** | – | – | – | `name:key[:loadtest],…`; unset = OPEN with one startup warning (`src/api/security/auth.py`) |
| `SOBA_AUTH_LEGACY` | optional | – | – | – | `1` also closes `/`, `/scene.json`, `/events`, static assets and `/api/docs` |
| `SOBA_RATE_LIMIT_RPS` / `_BURST`, `SOBA_RATE_LIMIT_UPLOAD_RPS` / `_BURST` | optional | – | – | – | 100/200 and 1/10 (`src/api/security/ratelimit.py`), per process |
| `SOBA_TRUST_PROXY` | **`1` behind the reverse proxy** | – | – | – | rightmost `X-Forwarded-For` hop becomes the client identity |
| `SOBA_CORS_ORIGINS` | optional | – | – | – | empty = no CORS headers (`src/api/security/headers.py`) |
| `SOBA_SSE_MAX_PER_IP` | optional | – | – | – | 8 concurrent `/events` per IP |
| `SOBA_MAX_UPLOAD_MB`, `SOBA_MAX_EXTRACTED_MB`, `SOBA_MAX_MEMBER_MB`, `SOBA_MAX_MEMBERS`, `SOBA_MAX_DECOMPRESSION_RATIO`, `SOBA_MAX_FRAMES`, `SOBA_MAX_FRAME_PIXELS`, `SOBA_DEPTH_SAMPLE` | optional | – | – | – | 2048 / 8192 / 512 / 200000 / 200 / 5000 / 4096² / 8 (`src/api/security/upload_validation.py`) |
| `SOBA_LOG_JSON` | `1` | `1` | – | optional | JSON-lines logs (`src/telemetry/jsonlog.py`) |
| `RUNPOD_API_KEY` | – | **real mode** | – | yes | secret; bearer to `https://api.runpod.ai` (`generative.py` `make_engine`) |
| `RUNPOD_GEN_ENDPOINT_ID` (alias `RUNPOD_ENDPOINT_ID`), `RUNPOD_COMPLETION_ENDPOINT_ID` | – | real mode | – | yes | endpoint ids; absent = generative objects dropped, completion local |
| `RUNPOD_GEN_MODEL`, `RUNPOD_COMPLETION_MODEL` | – | optional | – | optional | per-tier default (`triposg` tiers 1–2, `hunyuan3d` 3–4) / `pointr` |
| `SOBA_RUNPOD_DISABLED` | – | **kill switch** | – | yes | `1` = no RunPod call, objects dropped as `runpod_disabled` (`src/reconstruction/runpod_policy.py`) |
| `SOBA_RUNPOD_TIMEOUT`, `SOBA_RUNPOD_URL`, `SOBA_RUNPOD_BASE_URL`, `SOBA_RUNPOD_ALLOW_HTTP` | – | dev/test only | – | dev/test only | overrides; a non-https override is refused unless `SOBA_RUNPOD_ALLOW_HTTP=1` (`generative.py` `_check_scheme`) |
| `ANTHROPIC_API_KEY`, `SOBA_VLM`, `SOBA_VLM_MODEL` | – | real mode, optional | – | optional | Step-8 physics VLM; blank = lookup table (`src/scene/vlm_claude.py`) |
| `SOBA_MAST3R_HOME`, `SOBA_TRIPOSG_HOME`, `SOBA_HUNYUAN_HOME`, `SOBA_PATCHCOMPLETE_HOME`, `POINTR_HOME`, `SOBA_SRC` | – | real mode | **yes** | yes | baked to `/workspace/...` in `docker/pipeline.Dockerfile`; the volume must contain the checkouts |
| `SOBA_LOCAL_GPU`, `SOBA_GEN_MODEL`, `SOBA_COMPLETION_MODEL`, `SOBA_*_STEPS/_SEED/_FACES` | – | optional | optional | optional | engine knobs (`deploy/runpod/env.example`) |
| `HF_TOKEN` | – | – | setup time | setup time | only when `briaai/RMBG-1.4` or a Hunyuan repo is license-gated (`setup_triposg.sh`, `setup_hunyuan3d.sh`) |
| `SOBA_UID` / `SOBA_GID` | compose | compose | – | – | host uid/gid so the `./out` bind mount is writable (`docker-compose.yml`) |

Resilience and cost **values** live only in `config/pipeline.yaml` `runpod:`
(`transport`, `request_timeout_s 900`, `job_timeout_s 1800`,
`poll_interval_s 5`, `stall_timeout_s 600`, `retry.*`, `circuit_breaker.*`,
`budget.*`, `price.*`); env vars are switches, never values (CLAUDE.md
invariant 2). The image bakes the yaml, so a value change is a rebuild.

### 4.2 Secrets: what, where, how

| Secret | Lives on | Provisioning |
|---|---|---|
| `SOBA_API_KEYS` | API host only | generate per caller: `python -c "import secrets; print(secrets.token_urlsafe(32))"` (`env.example`); one entry per client, `name` is the audit identity; give the load-test client the `:loadtest` flag and nobody else |
| `RUNPOD_API_KEY` | worker host (real mode) / pod only; **never the API host** | RunPod console → Settings → API keys; scope it to serverless if the console offers it |
| `RUNPOD_GEN_ENDPOINT_ID`, `RUNPOD_COMPLETION_ENDPOINT_ID` | worker host / pod | from the endpoint page (§5.4); identifiers, not credentials, but keep them out of public docs (`secrets-audit.md`) |
| `ANTHROPIC_API_KEY` | worker host / pod, optional | Anthropic console; read implicitly by the SDK |
| `HF_TOKEN` | wherever `setup_*.sh` runs | HuggingFace token after accepting the gated licences; not needed at run time once weights are on the volume |
| `GHCR_PAT` | the machine that pushes images | GitHub PAT with `write:packages`; not a runtime secret |

Rules (`docs/security/secrets-audit.md`): `.env` is gitignored and was never
tracked; copy `env.example` → `.env` (`chmod 600`), `set -a; . ./.env; set +a`
or use compose's `env_file`; the serverless endpoint gets its variables from
the RunPod endpoint settings, not from a file in the image; nothing is
hardcoded, and gitleaks runs in pre-commit. The API process never needs
`RUNPOD_API_KEY` or `ANTHROPIC_API_KEY`: keep them off that host.

### 4.3 Reverse proxy (required once `SOBA_API_KEYS` is set)

Bearer-only auth means a browser cannot open `/jobs/{id}/` in keyed mode
(`STATUS.md` known limitations, `docs/api.md`). Two working options:

1. **Header injection** — the proxy authenticates the operator (SSO, basic
   auth, mTLS) and adds `Authorization: Bearer <viewer key>` on `/jobs/*`
   and `/api/*`. nginx sketch:
   ```nginx
   location / {
     proxy_pass http://127.0.0.1:8000;
     proxy_set_header Authorization "Bearer ${SOBA_VIEWER_KEY}";   # e.g. via envsubst or `set $k` in a non-committed include
     proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
     proxy_buffering off;   proxy_read_timeout 1h;                  # SSE
     client_max_body_size 2048m;                                    # = SOBA_MAX_UPLOAD_MB
   }
   location /metrics { allow 10.0.0.0/8; deny all; proxy_pass http://127.0.0.1:8000; }
   ```
2. **Keep the legacy routes open** (`SOBA_AUTH_LEGACY` unset): the root viewer
   at `/` still works without a key, but job-scoped pages do not.

`/metrics` is unauthenticated in both modes: ACL it at the proxy or bind the
API to a private interface. Set `SOBA_TRUST_PROXY=1` only when the proxy is
the sole path to the API; otherwise the client controls its own identity.

## 5. Deploy

### 5.1 Single host with compose (API + Redis + worker)

`docker-compose.yml` profiles: none = API only with the in-process mock
worker; `cpu` = api + redis + mock worker from the API image; `gpu` = adds the
pipeline image as the worker; `loadtest` = k6 + SSE probe (never started by
`up`). `./out` is bind-mounted at `/data` and the containers run as
`SOBA_UID:SOBA_GID`.

```bash
# executed here 2026-09-13 (mock worker):
SOBA_QUEUE_URL=redis://redis:6379/0 SOBA_WORKER_INPROC=0 SOBA_UID=$(id -u) SOBA_GID=$(id -g) \
  docker compose --profile cpu up -d --build            # = make up (foreground)
#   observed: redis healthy, api healthy after 1 s (curl /scene.json 200), worker up
docker compose --profile cpu ps
docker compose --profile cpu logs --no-log-prefix api | grep SOBA_API_KEYS
#   observed: "WARNING soba.api.security: SOBA_API_KEYS is not set: the API is OPEN to every caller ..."
```

Production settings on top of that (put them in `.env` next to the compose
file; compose reads it automatically): `SOBA_API_KEYS`, `SOBA_TRUST_PROXY=1`,
`SOBA_LOG_JSON=1`, and the reverse proxy from §4.3 in front of `:8000`
(`ports: "8000:8000"` binds every interface — change it to
`127.0.0.1:8000:8000` when the proxy is on the same host).

Tear down: `docker compose --profile cpu --profile gpu --profile loadtest down`
(= `make down`). Note `restart: unless-stopped` on `api` and `worker`: a
Docker daemon restart brings them back unless they were stopped (observed
here on 2026-09-14 after a Docker Desktop restart mid-run).

### 5.2 API and worker on two hosts

The queue is Redis and the store is a file, so splitting needs two things:

- **Redis reachable by both** (`SOBA_QUEUE_URL=redis://<host>:6379/0` on
  both). Compose publishes Redis on `127.0.0.1:6379` only; on a split
  deployment bind it to the private network and add `requirepass`
  (`redis://:<pw>@host:6379/0` is accepted by `redis.from_url`).
- **The same jobs directory at the same absolute path on both** (NFS or
  another shared filesystem mounted at, say, `/srv/soba/out/jobs`, with
  `SOBA_JOBS_DIR=/srv/soba/out/jobs` on both). The sqlite store uses WAL
  with a fresh connection per call under a process lock
  (`docs/log/2026-09-11-job-api.md`); it is designed for one API process
  plus one worker on one host. **sqlite over NFS is not safe** (WAL needs
  shared memory); on two hosts prefer one of: run the API on the GPU host
  too (simplest), or put `out/` on a block volume attached to the worker
  host and have the API read it over a filesystem that supports POSIX locks
  (SMB/CIFS with `nobrl` is not enough for WAL). This is a real gap: a
  shared job store (Postgres) is a follow-up, not a config change.

On the worker host (real mode, needs the pipeline image + a GPU):

```bash
# NOT executed here: needs a GPU host and the pipeline image.
docker run -d --name soba-worker --gpus all --restart unless-stopped \
  --env-file /srv/soba/.env \
  -e SOBA_QUEUE_URL=redis://<redis-host>:6379/0 -e SOBA_JOBS_DIR=/data/jobs -e SOBA_LOG_JSON=1 \
  -v /srv/soba/out:/data -v /srv/soba/workspace:/workspace \
  soba-pipeline:local worker --mode real --poll-s 5
```

The worker takes one job at a time (`orchestration.worker` `serve()` loop);
N workers = N containers on N GPUs, all on the same queue. Each worker's
breaker and budget are per process (`docs/log/2026-09-12-runpod-orchestration.md`).

### 5.3 GPU worker with the `gpu` profile

```bash
# NOT executed here: needs the NVIDIA runtime and soba-pipeline:local.
SOBA_QUEUE_URL=redis://redis:6379/0 SOBA_WORKER_INPROC=0 SOBA_UID=$(id -u) SOBA_GID=$(id -g) \
  docker compose --profile gpu up -d api redis worker-gpu
docker compose --profile gpu config --quiet && echo compose ok     # executed here: prints "compose ok"
```

`worker-gpu` mounts `./workspace:/workspace` (model checkouts + weights from
`setup_*.sh`) and `./out:/data` with `SOBA_JOBS_DIR=/data/jobs`, the same
container path the `api` service uses; it reads `.env` if present
(`env_file`, not required). The service originally mounted `./out` at
`/app/out`, which made every real job fail at `validating` because the
record's `bundle_dir` is `/data/jobs/...`; fixed on this branch. The
pipeline image runs as root, so files it writes under `out/jobs/<id>/scene`
are root-owned and `DELETE /api/jobs/{id}` from the uid-10001 API cannot
remove them (§14).

### 5.4 RunPod serverless endpoint (generative + completion bands)

Separate from any pod. Not executed here (needs a RunPod account and the
pushed `-devel` pipeline image).

1. **Network volume** (RunPod → Storage): 100 GB, same region as the
   endpoint, mounted at `/workspace`. Populate it once from a temporary pod
   that has the volume attached:
   ```bash
   bash deploy/runpod/setup_triposg.sh          # /workspace/TripoSG + HF VAST-AI/TripoSG + briaai/RMBG-1.4 (HF_TOKEN if gated)
   SETUP_HUNYUAN_PAINT=1 bash deploy/runpod/setup_hunyuan3d.sh   # /workspace/Hunyuan3D-2.1 + tencent/Hunyuan3D-2.1 (+ paint)
   ```
   Record the pins while you are there (§6 G2).
2. **Template**: container image `ghcr.io/sladojevicm/soba-pipeline:<version>-devel`,
   container start command `serverless` (the entrypoint runs
   `python deploy/runpod/generative_handler.py` → `runpod.serverless.start`),
   container disk 20 GB, volume mount path `/workspace`. Env on the
   template: `SOBA_TRIPOSG_HOME=/workspace/TripoSG`,
   `SOBA_HUNYUAN_HOME=/workspace/Hunyuan3D-2.1`, `SOBA_SRC=/app/src` (all
   already baked as defaults in the Dockerfile), plus `HF_HOME=/workspace/hf`
   so weights resolve from the volume, not the container disk.
3. **Endpoint**: GPU 24 GB (TripoSG) or 48 GB (Hunyuan3D 2.1 shape ≈ 10 GB
   + paint), min workers 0, max workers 2–3, idle timeout 60 s, execution
   timeout ≥ `job_timeout_s` (1800 s, `config/pipeline.yaml`), FlashBoot on.
   Copy the endpoint id into `RUNPOD_GEN_ENDPOINT_ID` (one endpoint serves
   both models: the client picks per tier, `deploy/runpod/README.md`); a
   second endpoint for shape completion is optional
   (`RUNPOD_COMPLETION_ENDPOINT_ID`).
4. **Contract test**: the request/response shape (`image_b64` /
   `cloud_npy_b64` in, `mesh_b64` + `format` out) is pinned by
   `tests/reconstruction/test_generative_handler.py`. Probe the live
   endpoint once with a staged crop before pointing a worker at it:
   ```bash
   curl -sS -X POST https://api.runpod.ai/v2/$RUNPOD_GEN_ENDPOINT_ID/run \
     -H "Authorization: Bearer $RUNPOD_API_KEY" -H 'Content-Type: application/json' \
     -d '{"input": {"model": "triposg", "image_b64": "'"$(base64 -w0 crop.png)"'"}}'
   # then poll https://api.runpod.ai/v2/$RUNPOD_GEN_ENDPOINT_ID/status/<id> until COMPLETED
   ```
5. **Price**: set `config/pipeline.yaml` `runpod.price.usd_per_gpu_second`
   (and `by_endpoint`) from the endpoint's GPU tier on the RunPod pricing
   page. The shipped `0.00044` is an **assumption** for a 24 GB flex worker,
   not a measurement (yaml comment); every `est_usd` in `cost.json` and
   `soba_remote_est_usd_total` scales with it.

### 5.5 GPU pod (optional, interactive)

`deploy/runpod/bootstrap.sh` clones `master` (`REPO_BRANCH` default),
installs `[recon,serve,dev]` into the template's CUDA torch and runs the
same Open3D CUDA check as the image; `run_pipeline.sh` is the one-shot
Replica driver. Pod-only detail (GPU choice, volume, ports, ComPC) stays in
`deploy/runpod/README.md`. To run the queue worker on the pod instead of the
image: `SOBA_QUEUE_URL=redis://<host>:6379/0 SOBA_JOBS_DIR=/workspace/out/jobs PYTHONPATH=src python -m orchestration.worker --mode real`
(`deploy/runpod/env.example`, last block).

## 6. Pre-deploy gates

All three are mandatory before a tag is deployed. G1 and G2 need a GPU
machine. G1 has been executed on a RunPod RTX 4090 (bootstrap path, not the
image); G2 has not.

**G1 — Open3D CUDA check (mandatory, GPU host).** **Executed 2026-09-16 on a
RunPod RTX 4090 via `bootstrap.sh` + `deploy/runpod/gpu_validate.sh` S1: PASS**
— `open3d CUDA tensor OK on CUDA:0 (TSDF VoxelBlockGrid will run on GPU)` with
the same pip-resolved `open3d==0.19.0` wheel
(`docs/log/2026-09-16-gpu-validation-run1.md`). The 2026-09-11 CI message
`Unsupported device "CUDA:0". Set BUILD_CUDA_MODULE=ON` was what Open3D prints
when no CUDA device exists on the build runner, not evidence of a CPU-only
wheel. The gate stays: run it on every new host and on the **image** itself
(the image path has not been run on a GPU yet; only bootstrap's has).

```bash
# executed 2026-09-16 (pod, bootstrap path): S1 PASS — see docs/gpu-validation.md run 1
# NOT executed yet on the image: needs a GPU host with Docker. Exit 0 = CUDA tensor backend present; exit 1 = CPU fallback.
docker run --rm --gpus all soba-pipeline:<tag> check
#   expect: "open3d CUDA tensor OK on CUDA:0 (TSDF VoxelBlockGrid will run on GPU)"
# on a pod / the 4060 without Docker, the same check is bootstrap.sh's "Verify host-pipeline stack" block.
```

If it exits 1, do **not** deploy the tag for real-mode work. Fix options,
in order of effort: (a) install a CUDA-enabled Open3D wheel for the image's
CUDA 12.4 / Python 3.11 combination (Open3D publishes CUDA wheels only for
some versions; check the release assets, then add
`pip install <wheel url>` after the extras line in `docker/pipeline.Dockerfile`
and the same line in `bootstrap.sh`); (b) build Open3D from source with
`BUILD_CUDA_MODULE=ON` in a builder stage of the `-devel` image; (c) accept
CPU TSDF for a tier-1 / small-scene deployment and say so in the release
notes. Which wheel or build is a maintainer decision, not something to bake
in silently.

**G2 — Model pins recorded.** Nothing pins the MASt3R, TripoSG, Hunyuan3D 2.1
or PatchComplete commits, checkpoint sha256s or HF revisions **that produced
BENCHMARK.md** (`docs/model-pins.md`, `STATUS.md`). The only recorded pin is a
validation pin: the TripoSG commit the 2026-09-16 pod run cloned. Before a
release tag, run the recovery script on the 4060 and, if the volume still
exists, on the pod, and paste the output into the paste target at the end of
`docs/model-pins.md`:

```bash
# NOT executed yet: needs the RTX 4060 machine (paths ~/soba/...) and the pod volume (ROOT=/workspace).
bash deploy/runpod/recover_pins.sh            # prints a paste-ready Markdown block; NOT FOUND for anything absent
```

A release whose image and volume do not match the recorded pins cannot
reproduce `BENCHMARK.md`; say so in the release notes rather than guess.

**G3 — CI green on the exact commit.** `ci.yml` runs pytest (Open3D for
real on `ubuntu-latest`, one gpu-marked test skipped, `docs/ci-probe.md`),
ruff, the three image builds and the `frontend-check` byte-for-byte guard.

```bash
# executed here 2026-09-14 (gh is authenticated; calls are rate-limited, batch them)
gh run list --branch develop --limit 3 --json databaseId,status,conclusion,headSha
#   observed: 8dcafef (the merge of PR #14) in_progress at the time of writing, 257032d success, 001d02c success
gh run watch <id> --exit-status        # blocks until the run for the release commit is green
```

## 7. Smoke test

Run after every deploy, against the deployed URL. The whole sequence was
executed here on 2026-09-13/14 against the compose `cpu` stack (mock worker,
so the scene is the `out/scene_test` fixture: three objects, not a
reconstruction).

```bash
BASE=http://127.0.0.1:8000      # the proxy URL in production; add -H "Authorization: Bearer $KEY" in keyed mode

# 1. health + open-mode warning
curl -sf $BASE/scene.json | head -c 80                    # executed: 200, {"version": "2.0", ... "objects": [...]}

# 2. upload → done (stdlib only; --bundle DIR zips a directory, --archive sends a file)
python scripts/submit_job.py --archive loadtest/fixtures/bundle_small.zip --tier 2 --server $BASE
#   executed: "job 4a301442abda3e62  status queued" → "1.0s  done" → "viewer: http://127.0.0.1:8000/jobs/4a301442abda3e62/"
#   real mode on the pod:  python scripts/submit_job.py --bundle bundles/office_3 --tier 2 --server $BASE   (NOT executed here)

# 3. the job's artefacts on disk (same jobs dir the worker wrote)
ls out/jobs/4a301442abda3e62 out/jobs/4a301442abda3e62/scene
#   executed: extracted/ scene/ upload.zip ; scene: cost.json objects/ scene.json
#   cost.json observed: mode "mock", source null, remote.calls 0, est_usd 0.0, budget {40, 3600, 5.0}, runpod_disabled false

# 4. headless browser check on the job URL (puppeteer outside the repo, CLAUDE.md)
SOBA_PYTHON=.venv/bin/python NODE_PATH=~/tmp/pptr/node_modules \
  node scripts/verify_browser.js --scene out/jobs/4a301442abda3e62/scene --path /jobs/4a301442abda3e62/ --screenshot /tmp/soba_job.png
#   executed: ALL CHECKS PASSED (8/8: object count 3/3, masses within 0.1 %, bodies fixed, framedAll, click wakes,
#   no explosion after 3000 ms, zero page errors, screenshot written); screenshot inspected, viewer rendered, not black.
#   verify_browser starts its OWN scripts/serve.py on a free port with the repo's out/jobs store, so it needs
#   the jobs dir locally; against a remote deployment run it on the host that owns out/.

# 5. metrics
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' $BASE/metrics
#   executed: 200 text/plain; version=1.0.0; charset=utf-8 (114 lines); soba_jobs{state="done"} 1.0,
#   soba_http_requests_total{method="POST",route="/api/jobs",status="202"} 1.0

# 6. the worker CLI from the image
docker run --rm soba-api:local python -m orchestration.worker --help     # executed: usage with --jobs-dir --queue-url --mode {mock,real,gate-only} --once --poll-s --python --repo-root
docker run --rm soba-api:local python -m orchestration.worker --once     # executed: "ERROR ... no queue: pass --queue-url or set SOBA_QUEUE_URL", exit 2
docker run --rm soba-api:local id                                        # executed: uid=10001(soba)

# 7. API-layer load smoke (k6 + SSE probe; brings the cpu stack up itself and tears it down)
SMOKE=1 NO_BUILD=1 make loadtest
```

Step 7 was executed here twice. 2026-09-13 22:45 local: 10 of 11 scenarios
ran, then the Docker daemon died during the last one (`keyed_plain_upload_burst`,
k6 exit 125 "unexpected EOF") and `compose down` failed; one threshold was
also crossed in that run (`keyed_loadtest_status_polling` status p95 129 ms
against the 100 ms bound at 300 rps, on a laptop that was also building an
image at the time). 2026-09-14 08:39 UTC rerun after Docker came back:
**all 11 scenarios and the SSE probe passed**, `failed: []`, results in
`loadtest/results/20260914T083908Z/` (gitignored; the summary rows are in
`docs/log/2026-09-14-deployment-runbook.md`), stack torn down by `run.sh`.
These numbers are API-layer throughput with a mock worker on CPU, never
pipeline or GPU throughput (`docs/loadtest.md`).

## 8. Scaling and cost controls

| Control | Where | Effect |
|---|---|---|
| **Kill switch** `SOBA_RUNPOD_DISABLED=1` | worker env (restart the worker) | no RunPod call is made; every generative object is dropped and recorded as `runpod_disabled` in `run_metrics.json` / `cost.json`; the run finishes (`src/reconstruction/runpod_policy.py`, `worker.py` docstring). Executed here only through the unit tests (`tests/orchestration`, `tests/reconstruction/test_runpod_engine.py`). |
| **Per-job budget** `runpod.budget` (`max_calls 40`, `max_gpu_seconds 3600`, `max_est_usd 5.0`) | `config/pipeline.yaml` (rebuild the image) | checked before each call; on exhaustion the object is dropped as `runpod_budget_exhausted`, the run continues; a call may overrun by its own retries (bounded by `retry.max_attempts 3`) |
| **Circuit breaker** `runpod.circuit_breaker` (3 consecutive failures → open 120 s → one half-open probe) | `config/pipeline.yaml` | per endpoint id, per worker process; while open objects drop as `runpod_breaker_open` |
| **Timeouts** `request_timeout_s 900`, `job_timeout_s 1800`, `stall_timeout_s 600`, `poll_interval_s 5` | `config/pipeline.yaml` | a stalled `/status` is cancelled and retried as transient; past `job_timeout_s` it is cancelled permanently |
| **Price** `runpod.price.usd_per_gpu_second` (+ `by_endpoint`) | `config/pipeline.yaml` | the multiplier behind every `est_usd`; **the maintainer must set it from the endpoint's GPU tier** (shipped value is an assumption) |
| Endpoint max workers / idle timeout | RunPod console | the hard ceiling on concurrent GPU spend; min workers 0 unless cold starts hurt |
| Worker count | one container per GPU | job throughput; queue depth = `soba_jobs{state="queued"}` |
| API replicas | one uvicorn process per host | rate limits and the SSE cap are per process: N replicas = N× the configured limits (`STATUS.md`) — run one replica, or accept N×, until the Redis-shared limiter lands |
| Upload caps | API env (§4.1) | bound disk and CPU per request |

Reading the spend: per job `out/jobs/<id>/scene/cost.json` (`remote.calls`,
`gpu_seconds`, `est_usd`, `drops`, `budget.exhausted`); fleet-wide
`soba_remote_est_usd_total{kind="runpod/<endpoint>"}` on `/metrics`
(ingested from each finished job's `run_metrics.json` at scrape time,
`docs/log/2026-09-12-observability-b.md`). Both are estimates; the invoice
is RunPod's.

## 9. Monitoring and alerts

Sources: `GET /metrics` (Prometheus text; needs the `telemetry` extra, which
the API image installs; 501 otherwise), JSON logs with `SOBA_LOG_JSON=1`
(`http` events with `job_id`, `job_state`, `job_cost`, `worker_start/stop`,
`gate`, `drop`, `remote_call`, `stage.end`), per job `run_metrics.json` +
`cost.json`, the container `HEALTHCHECK`. Metric names, all `soba_`-prefixed
(`src/api/routes/metrics.py`): `http_requests_total{method,route,status}`,
`http_request_duration_seconds{method,route}`, `jobs{state}` (gauge, from the
store at scrape time), `job_runs_total{status}`, `gate_decisions_total{strategy}`,
`gate_routed_total{strategy}`, `pipeline_stage_seconds_total{stage}`,
`pipeline_stage_runs_total{stage}`, `pipeline_drops_total{reason}`,
`remote_calls_total{kind}`, `remote_call_seconds_total{kind}`,
`remote_est_usd_total{kind}`, `run_metrics_ingest_errors_total`.

Caveats that shape the rules: counters live in the API process and reset on
restart (use `increase()`/`rate()`, never absolute values); ingestion only
happens when `/metrics` is scraped, so scrape at least every 30 s; the mock
worker writes no `run_metrics.json`, so `job_runs_total` and the gate/drop
counters stay 0 until a real worker runs; disk and process-up signals come
from `node_exporter` / the proxy, not from Soba.

Prometheus scrape (`/metrics` must be reachable from Prometheus and from
nobody else, §4.3):

```yaml
scrape_configs:
  - job_name: soba-api
    scrape_interval: 30s
    static_configs: [{targets: ["soba-api-host:8000"]}]
```

Proposed alert rules (thresholds are starting points; tune after a week of
real jobs):

```yaml
groups:
- name: soba
  rules:
  - alert: SobaApiDown
    expr: up{job="soba-api"} == 0
    for: 2m
    annotations: {summary: "API scrape failing; check `docker compose ps` (HEALTHCHECK on /scene.json) and the proxy"}
  - alert: SobaApi5xx
    expr: sum(rate(soba_http_requests_total{status=~"5.."}[5m])) / clamp_min(sum(rate(soba_http_requests_total[5m])), 0.01) > 0.01
    for: 5m
    annotations: {summary: ">1 % of requests are 5xx; §12.4"}
  - alert: SobaJobsFailing
    expr: increase(soba_job_runs_total{status="failed"}[1h]) / clamp_min(increase(soba_job_runs_total[1h]), 1) > 0.2
          or delta(soba_jobs{state="failed"}[1h]) >= 3
    for: 10m
    annotations: {summary: "job failure rate > 20 % (or 3+ new failed jobs in 1 h); read out/jobs/<id>/worker.log"}
  - alert: SobaQueueBacklog
    expr: soba_jobs{state="queued"} > 10
    for: 15m
    annotations: {summary: "queue depth > 10 for 15 min: add a worker or check the worker is alive; §12.5"}
  - alert: SobaWorkerStuck
    expr: min_over_time(soba_jobs{state="running"}[2h]) > 0 and changes(soba_jobs{state="done"}[2h]) == 0
    for: 5m
    annotations: {summary: "a job has been running for 2 h with nothing finishing (job_timeout_s is 1800 s); §12.5"}
  - alert: SobaRunPodDegraded
    expr: increase(soba_pipeline_drops_total{reason=~"runpod_(failed|breaker_open|budget_exhausted)"}[1h]) > 0
    annotations: {summary: "objects dropped because RunPod failed, the breaker opened or a budget was hit; §12.1 / §12.2"}
  - alert: SobaRunPodSpend
    expr: increase(soba_remote_est_usd_total[24h]) > 20      # daily USD cap: set from the budget
    annotations: {summary: "estimated RunPod spend over the daily cap; §12.2 (kill switch)"}
  - alert: SobaOutDiskLow
    expr: node_filesystem_avail_bytes{mountpoint="/srv/soba/out"} / node_filesystem_size_bytes{mountpoint="/srv/soba/out"} < 0.15
    for: 10m
    annotations: {summary: "out/ (uploads + jobs) under 15 % free; no retention policy exists; §12.3"}
  - alert: SobaMetricsIngestErrors
    expr: increase(soba_run_metrics_ingest_errors_total[1h]) > 0
    annotations: {summary: "a run_metrics.json failed schema validation: a worker/API version mismatch"}
```

Dashboard panels worth having: `soba_jobs` by state (stacked), `rate(soba_http_requests_total)` by route and status, p95 from `soba_http_request_duration_seconds_bucket`, `increase(soba_remote_est_usd_total[1d])`, `soba_gate_decisions_total` by strategy (the keep / complete / regenerate mix), mean stage time = `increase(soba_pipeline_stage_seconds_total[1d]) / increase(soba_pipeline_stage_runs_total[1d])`.

## 10. Backup and retention

What holds state (`docs/log/2026-09-11-job-api.md`, `docker-compose.yml`):

| Data | Path | Backup |
|---|---|---|
| Job store | `out/jobs/jobs.sqlite` (+ `-wal`, `-shm`) | `sqlite3 out/jobs/jobs.sqlite ".backup '/backup/jobs-$(date +%F).sqlite'"` — a consistent copy under WAL; never `cp` the three files while the API runs |
| Job directories | `out/jobs/<id>/{upload.*, extracted/, scene/}` | rsync to object storage; `scene/` is the deliverable (`scene.json`, `objects/*.glb`, `run_metrics.json`, `cost.json`), `upload.*` + `extracted/` are re-derivable from the upload |
| Legacy root scene | `out/<scene>` (`SOBA_SCENE_DIR`) | with the job dirs |
| Redis | list `soba:jobs` | not backed up: the API re-enqueues jobs still `queued` in the store at startup (in-process queue); with Redis, a lost list means re-`LPUSH` the ids of `queued` records (`sqlite3 out/jobs/jobs.sqlite "select id from jobs where state='queued'"`) |
| Model volume | `/workspace` on the pod / RunPod network volume | snapshot in RunPod, or re-run `setup_*.sh` — but only the recorded pins (§6 G2) make that reproducible |
| Config | `config/pipeline.yaml`, `.env` (per host, 0600) | git for the yaml; the secret store for `.env` |

**Retention: none exists** (`STATUS.md`): uploads and `out/jobs/` grow
unbounded; `DELETE /api/jobs/{id}` is the only cleanup and it removes the
record and the whole directory. Until a policy lands, run a cron on the API
host that deletes terminal jobs older than N days **through the API** (so
the store stays consistent), and keep the disk alert from §9:

```bash
# /etc/cron.daily/soba-retention  (executed here against the local stack in mock mode: not run; the command shape is from docs/api.md)
BASE=http://127.0.0.1:8000; DAYS=14; AUTH=(-H "Authorization: Bearer $SOBA_ADMIN_KEY")
curl -sS "${AUTH[@]}" $BASE/api/jobs | python -c '
import json,sys,time; cutoff=time.time()-'"$DAYS"'*86400
print("\n".join(j["id"] for j in json.load(sys.stdin) if j["state"] in ("done","failed") and j["updated_at"]<cutoff))' \
| while read id; do curl -sS "${AUTH[@]}" -X DELETE -o /dev/null -w "$id %{http_code}\n" $BASE/api/jobs/$id; done
```

Jobs left `running` by a worker that died stay `running` forever (no
requeue, `docs/log/2026-09-12-runpod-orchestration.md`); §12.5 covers them.

## 11. Rollback

Images are immutable and the job store schema has not changed since PR #9
(`CREATE TABLE IF NOT EXISTS jobs`, `src/api/jobs/store.py`; no migration
mechanism exists, so a rollback across a future store change is untested).

```bash
# 1. re-tag the previous version to the name compose expects, recreate only what changed
docker pull ghcr.io/sladojevicm/soba-api:$PREV && docker tag ghcr.io/sladojevicm/soba-api:$PREV soba-api:local
docker compose --profile cpu up -d --no-build api worker      # NOT executed here with a registry image; the same command with the local image was (§5.1)
# 2. GPU worker
docker pull ghcr.io/sladojevicm/soba-pipeline:$PREV && docker tag ghcr.io/sladojevicm/soba-pipeline:$PREV soba-pipeline:local
docker compose --profile gpu up -d --no-build worker-gpu      # NOT executed here
# 3. RunPod endpoint: edit the template's image tag in the console (or `runpodctl`), the next cold start uses it
# 4. code on a pod: git -C /workspace/soba checkout <tag> && bash deploy/runpod/bootstrap.sh   (bootstrap resets to origin/$REPO_BRANCH: set REPO_BRANCH=<tag>)
# 5. re-run §7 steps 1, 2, 5
```

The worker finishes its current job on SIGTERM (`install_signal_handlers`,
`worker.py`), so `docker compose up -d` recreating it is safe; give it
`stop_grace_period` ≥ the longest job if the default 10 s is too short
(add it to `docker-compose.yml` per deployment; not set today).

## 12. Incident checklist

Each scenario: detect → contain → diagnose → recover → follow up. Commands
are the ones from §7–§10; none of the GPU/RunPod ones were executed here.

### 12.1 RunPod outage (endpoint down, 5xx, IN_QUEUE forever)
- **Detect**: `SobaRunPodDegraded` (`runpod_failed` / `runpod_breaker_open` drops), worker log `remote_call` errors, RunPod status page.
- **Contain**: nothing to do for correctness — a failure drops one object, never the run, and the breaker opens after 3 consecutive failures for 120 s (`config/pipeline.yaml`). To stop paying for retries: `SOBA_RUNPOD_DISABLED=1` on the worker and restart it (§8).
- **Diagnose**: `grep -h remote_call out/jobs/*/worker.log | tail`; `curl -H "Authorization: Bearer $RUNPOD_API_KEY" https://api.runpod.ai/v2/$RUNPOD_GEN_ENDPOINT_ID/health` (workers ready / throttled).
- **Recover**: clear the switch, restart the worker, re-submit the affected jobs (their generative objects were dropped; nothing re-runs automatically). Identify them with `python -c 'import json,glob; [print(p) for p in glob.glob("out/jobs/*/scene/cost.json") if sum(json.load(open(p))["drops"].values())]'`.
- **Follow up**: if the endpoint stalled rather than failed, check `stall_timeout_s` / `job_timeout_s` against the observed generation time (shape ~2.5 min + paint ~2 min + cold load, yaml comment).

### 12.2 Runaway cost
- **Detect**: `SobaRunPodSpend`, RunPod billing page, `cost.json` `budget.exhausted != null` on many jobs, `soba_remote_calls_total` climbing with no jobs finishing.
- **Contain (60 s)**: `SOBA_RUNPOD_DISABLED=1` + worker restart; in the RunPod console set the endpoint's **max workers to 0**. Both, in that order.
- **Diagnose**: per-job `cost.json` (`remote.calls`, `gpu_seconds`, `by_kind`); a retry loop shows as `calls` near `max_calls 40` with `est_usd` at `max_est_usd`; a price mismatch shows as RunPod's invoice ≫ `est_usd` (fix `runpod.price`).
- **Recover**: lower `budget.max_est_usd` / `max_calls` in `config/pipeline.yaml`, rebuild + redeploy the worker image, restore max workers, clear the switch.
- **Follow up**: the budget is per job and per worker process; a daily fleet cap only exists as the alert. Consider an endpoint-side spend limit in the RunPod console.

### 12.3 Disk full on `out/`
- **Detect**: `SobaOutDiskLow`; uploads answer 500 (`_stream_to_disk` fails) or jobs fail at `validating`; the API logs `unhandled: true`.
- **Contain**: stop accepting uploads at the proxy (`return 503` on `POST /api/jobs`) rather than stopping the API — status and viewer keep working.
- **Diagnose**: `du -sh out/jobs/* | sort -h | tail`; the biggest entries are `extracted/` trees of large bundles and `.gen_cache` / `.gate_cache` under bundle dirs if bundles live on the same volume.
- **Recover**: run the retention script from §10 with a smaller `DAYS`; delete failed jobs first (`state == "failed"`); never `rm` inside `out/jobs/<id>` by hand while the API runs (the store would keep pointing at it) — use `DELETE /api/jobs/{id}`.
- **Follow up**: retention policy (open item in `STATUS.md`), a separate volume for `out/`, `SOBA_MAX_EXTRACTED_MB` lower than 8192.

### 12.4 API 5xx
- **Detect**: `SobaApi5xx`, proxy error log, `soba_http_requests_total{status=~"5.."}` by route.
- **Contain**: if the route is `POST /api/jobs` only, block uploads at the proxy (as in 12.3); if everything 5xxs, restart the container (`docker compose --profile cpu restart api`, HEALTHCHECK confirms) and if that does not hold, roll back (§11).
- **Diagnose**: `docker compose logs --no-log-prefix api | grep -E 'unhandled|ERROR' | tail -20` (JSON lines carry `route`, `job_id`, `exc`); `500 multipart_unavailable` means the image was built without the `api` extra; `501 metrics_unavailable` without `telemetry` (`docs/api.md`); sqlite `database is locked` points at a second process opening the store without WAL or an NFS mount (§5.2).
- **Recover**: fix the config/image, redeploy, re-run §7 steps 1, 2, 5.
- **Follow up**: the known defect list in `STATUS.md` (empty-scene fallback fails the schema) is a 200, not a 5xx — do not chase it here.

### 12.5 Worker stuck (queue grows, jobs stay `running:<stage>`)
- **Detect**: `SobaQueueBacklog`, `SobaWorkerStuck`; `GET /api/jobs` shows the same `running:assembling` for hours; no `job_state` events in the worker log.
- **Contain**: nothing is lost — the store is the truth and Redis holds the remaining ids. Do not start a second worker on the same GPU (OOM, `STATUS.md` durable gotchas).
- **Diagnose**: `docker compose --profile cpu ps` (restart loop?); `docker compose logs --no-log-prefix worker | tail -50`; `tail out/jobs/<id>/worker.log` (the `run_assemble.py` subprocess log); `nvidia-smi` on a GPU worker (a zombie `run_assemble.py` holding VRAM); `redis-cli -h <host> llen soba:jobs` for the backlog.
- **Recover**: `docker compose --profile cpu restart worker` (SIGTERM lets the current job finish; if it is truly hung, `docker kill` it). The job it was on stays `running` forever (no requeue): mark it by hand — `sqlite3 out/jobs/jobs.sqlite "update jobs set state='failed', stage=null, error='worker restarted', updated_at=strftime('%s','now') where id='<id>' and state='running'"` — then ask the user to re-submit, or `DELETE` it. Jobs still `queued` in the store but missing from Redis: `redis-cli lpush soba:jobs <id>`.
- **Follow up**: requeue-on-death and a per-job wall-clock limit are open follow-ups (`docs/log/2026-09-12-runpod-orchestration.md`); `job_timeout_s` bounds only the RunPod call, not the local stages.

## 13. Release flow (`develop` → `master`)

`develop` is the integration branch; `master` moves only on a tagged release
(`.claude/AGENTS.md`). The pod bootstrap clones `master` by default.

1. `develop` is green: `gh run list --branch develop --limit 1` shows `success` for the head commit (G3).
2. Gates G1 and G2 done on the GPU machine; the outputs are pasted into `docs/model-pins.md` in a commit on `develop`.
3. `BENCHMARK.md` untouched by hand (invariant 3); `spec/scene.schema.json` unchanged (invariant 1) — `git diff master..develop --stat -- BENCHMARK.md spec/scene.schema.json` shows only generated or approved changes.
4. `STATUS.md` current-state block reflects the release; `docs/log/` has the dated entries.
5. Open the PR: `gh pr create --base master --head develop --title "Release vX.Y.Z"`; merge after CI on the PR is green.
6. Tag: `git checkout master && git pull && git tag -a vX.Y.Z -m "Soba vX.Y.Z" && git push origin vX.Y.Z`.
7. Build and push both images from the tag (§3.2) with `<version>` = the tag and `<sha>` = `git rev-parse --short vX.Y.Z`.
8. Deploy (§5), smoke (§7), watch the alerts (§9) for a day.
9. Back-merge if anything was fixed on `master`: `git checkout develop && git merge master`.

Executed here: none of the release steps (no release was cut); `gh` calls in
G3 were.

## 14. Known limitations

The authoritative list is `STATUS.md` → "Known limitations". Operationally:

- **Bearer-only auth blocks the browser viewer** in keyed mode → header-injecting proxy or leave legacy routes open (§4.3).
- **`/metrics` is unauthenticated** → network ACL at the proxy (§4.3, §9).
- **Rate limiting and the SSE cap are per process** → one API replica, or accept N× (§8).
- **No retention policy** → cron through the API + disk alert (§10).
- **No model pins** → G2 before every release (§6).
- **Open3D wheel without CUDA in the pipeline image** → G1 before every GPU deploy (§6).
- **`coacd` missing from the pyproject extras** (installed explicitly by the pipeline image only; a pod bootstrapped with `bootstrap.sh` falls back to a single hull per object — `docs/model-pins.md`).

Found while writing this page (flagged, not fixed unless stated):

- `docker-compose.yml` `worker-gpu` mounted `./out` at `/app/out` while `api` stores absolute `/data/jobs/...` paths in the job record → every real job would fail at `validating`. **Fixed on this branch** (`./out:/data`, `SOBA_JOBS_DIR=/data/jobs`); the `gpu` profile still cannot be run here.
- `docker/pipeline.Dockerfile` has no `USER`: the GPU worker and the serverless handler run as root, so scene files it writes are root-owned and the uid-10001 API cannot `DELETE` those jobs. Flagged; the fix (a non-root user with a writable `HOME` for the HF cache) touches the setup scripts' assumptions and needs a GPU host to verify.
- `docker-compose.yml` hardcodes `image: soba-api:local` / `soba-pipeline:local` with no tag variable; deploying a registry tag means re-tagging locally (§3.2, §11). Flagged.
- `docs/api.md`'s example record shows `bundle_dir` as a host path (`/srv/soba/out/...`); in compose it is the container path (`/data/jobs/...`), which is why the mount rule in §1 matters. Documentation only.
- `.github/workflows/ci.yml` never pushes an image (by design so far); §3.2 defines the tag scheme for when a push job is added.
