# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# soba API image: the Starlette scene server (src/server.py) + the COMMITTED
# viewer bundle (frontend/dist/). No GPU, no reconstruction deps.
#
# Build (from the repo root):
#   docker build -f docker/api.Dockerfile -t soba-api .
# Run:
#   docker run --rm -p 8000:8000 -v $PWD/out/scene_test:/data/scene:ro soba-api
#
# Maintainer decision 2 (.claude/AGENTS.md): there is NO separate frontend
# container; this image serves frontend/dist itself. CDN offload is deferred
# until there is real traffic (STATUS.md).
#
# SOBA_EXTRAS: the pyproject extras to install. `serve` today; switch the
# default to `serve,api` once the `api` extra (python-multipart, aiofiles)
# lands on develop from feat/job-api. Overridable at build time:
#   docker build --build-arg SOBA_EXTRAS=serve,api ...
# ---------------------------------------------------------------------------
FROM python:3.11-slim

ARG SOBA_EXTRAS=serve

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # server.py is a top-level module under src/ (not a package), so the
    # editable install alone does not make `import server` work: put src/ on
    # the path explicitly. Same form as the repo's `PYTHONPATH=src`.
    PYTHONPATH=/app/src \
    # Scene directory served at /scene.json, /meshes, /hulls, /events, /eval.json.
    # Mount an assembled scene here; an empty dir serves an empty scene (200).
    SOBA_SCENE_DIR=/data/scene

WORKDIR /app

# Dependency layer first so source edits do not reinstall the wheels.
COPY pyproject.toml ./
# The editable install needs the package tree present; copy src/ before pip.
COPY src/ ./src/
RUN pip install -e ".[${SOBA_EXTRAS}]"

# The rest of what the server (and, later, the job API's local worker stub)
# reads at runtime: the schema, pipeline config, driver scripts, viewer bundle.
COPY spec/ ./spec/
COPY config/ ./config/
COPY scripts/ ./scripts/
COPY frontend/dist/ ./frontend/dist/

# Non-root (security-agent phase B confirms this). /data is the scene mount.
RUN groupadd -r -g 10001 soba \
    && useradd -r -u 10001 -g soba -d /app -s /usr/sbin/nologin soba \
    && mkdir -p /data/scene \
    && chown -R soba:soba /app /data
USER 10001:10001

EXPOSE 8000

# python:3.11-slim ships no curl/wget; probe with the stdlib. /scene.json
# answers 200 even before a scene is mounted (server.py partial-safe fallback).
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/scene.json', timeout=2).status == 200 else 1)"]

# `server:app` is the module-level app built from env/defaults (src/server.py).
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
