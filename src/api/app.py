"""Composition root: legacy single-scene routes + job API + job-scoped viewer.

Route order (first match wins):
  legacy root      GET /  /scene.json  /eval.json  /meshes/{id}.glb  /hulls/{stem}.glb  /events
  jobs API         POST|GET /api/jobs   GET|DELETE /api/jobs/{id}
  metrics          GET /metrics  (Prometheus text; 501 without the `telemetry` extra)
  job-scoped       GET /jobs/{id}/  + the same six scene routes under that prefix
  static mount     everything else from frontend/dist (index assets)

Later agents add a module under src/api/ and one registration line here.

Environment (factory args win):
  SOBA_SCENE_DIR      legacy root scene dir           (default out/scene_office_3)
  SOBA_JOBS_DIR       job store + job dirs            (default out/jobs)
  SOBA_WORKER_MODE    mock | real | gate-only         (default mock)
  SOBA_WORKER_INPROC  "0" disables the in-process worker thread (default on)
  SOBA_FIXTURE_SCENE  scene dir the mock worker copies (default out/scene_test)
  SOBA_MAX_UPLOAD_MB  upload size cap                 (default 2048)
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from .jobs.queue import InProcessQueue, JobQueue
from .jobs.store import QUEUED, JobStore, SqliteJobStore
from .jobs.worker_local import LocalWorker
from .routes.jobs import (
    DEFAULT_MAX_UPLOAD_BYTES,
    JobsContext,
    job_scene_resolver,
    job_watcher,
    jobs_routes,
)
from .routes.metrics import ApiTelemetry, metrics_routes, telemetry_middleware
from .routes.scene import scene_routes

REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SCENE_DIR = REPO_ROOT / "out" / "scene_office_3"
_DEFAULT_FRONTEND_DIR = REPO_ROOT / "frontend"
_DEFAULT_JOBS_DIR = REPO_ROOT / "out" / "jobs"


def _env_flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")


def create_app(
    scene_dir: Path | str | None = None,
    frontend_dir: Path | str | None = None,
    *,
    jobs_dir: Path | str | None = None,
    worker_mode: str | None = None,
    fixture_scene: Path | str | None = None,
    start_worker: bool | None = None,
    max_upload_bytes: int | None = None,
    store: JobStore | None = None,
    queue: JobQueue | None = None,
    worker_python: str | None = None,
) -> Starlette:
    """Build the Starlette app serving `scene_dir` at `/`, the viewer from
    `frontend_dir`, and the job API rooted at `jobs_dir`.

    `scene_dir` / `frontend_dir` keep the Phase-10 defaults (repo
    `out/scene_office_3/` and the committed `frontend/dist/`); the two
    positional args are the whole legacy signature.
    """
    scene_dir = Path(scene_dir or os.environ.get("SOBA_SCENE_DIR") or _DEFAULT_SCENE_DIR).resolve()
    if frontend_dir is None:
        # Prefer the committed Vite bundle (frontend/dist/) so `python
        # scripts/serve.py` needs no Node; fall back to frontend/ itself so a
        # stale checkout (pre-bundle, vendored libs) still runs.
        dist = _DEFAULT_FRONTEND_DIR / "dist"
        frontend_dir = dist if (dist / "index.html").is_file() else _DEFAULT_FRONTEND_DIR
    frontend_dir = Path(frontend_dir).resolve()

    jobs_dir = Path(jobs_dir or os.environ.get("SOBA_JOBS_DIR") or _DEFAULT_JOBS_DIR).resolve()
    worker_mode = worker_mode or os.environ.get("SOBA_WORKER_MODE") or "mock"
    if start_worker is None:
        start_worker = _env_flag("SOBA_WORKER_INPROC", True)
    if max_upload_bytes is None:
        max_upload_bytes = int(
            float(os.environ.get("SOBA_MAX_UPLOAD_MB", "0")) * 1024 ** 2
        ) or DEFAULT_MAX_UPLOAD_BYTES

    ctx = JobsContext(
        jobs_dir=jobs_dir,
        store=store or _LazySqliteStore(jobs_dir / "jobs.sqlite"),
        queue=queue or InProcessQueue(),
        max_upload_bytes=max_upload_bytes,
    )
    ctx.worker = LocalWorker(
        ctx.store, ctx.queue, jobs_dir, mode=worker_mode,
        fixture_scene=fixture_scene or os.environ.get("SOBA_FIXTURE_SCENE") or None,
        python=worker_python, repo_root=REPO_ROOT,
        mock_delay_s=float(os.environ.get("SOBA_MOCK_DELAY_S", "0")),
    )

    tel = ApiTelemetry(ctx)  # GET /metrics + request log (src/api/routes/metrics.py)
    routes = []
    routes += scene_routes(lambda request: scene_dir, frontend_dir)
    routes += jobs_routes(ctx)
    routes += metrics_routes(tel)
    routes += scene_routes(job_scene_resolver(ctx), frontend_dir,
                           prefix="/jobs/{job_id}", watch=job_watcher(ctx))
    # Static frontend assets (index-*.js/css). Mounted last so the routes
    # above take precedence. Only added when the dir exists so tests pointed
    # at a bare scene dir don't fail to construct.
    if frontend_dir.is_dir():
        routes.append(Mount("/", app=StaticFiles(directory=str(frontend_dir))))

    # No-store everything: the viewer is a live dev tool and meshes/app.js get
    # regenerated in place, so a browser MUST NOT serve a stale cached mesh (a
    # rebuilt object otherwise renders as its old geometry until a hard refresh).
    async def _no_store(request, call_next):
        resp = await call_next(request)
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        return resp

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        if start_worker:
            # The in-process queue is not persisted: jobs still `queued` in
            # the store from a previous process would otherwise never be
            # picked up. Only the in-process worker does this; an external
            # consumer (Redis queue) owns that concern itself.
            for rec in ctx.store.list():
                if rec.state == QUEUED:
                    ctx.queue.put(rec.id)
            ctx.worker.start()
        try:
            yield
        finally:
            ctx.worker.stop()
            ctx.queue.close()
            ctx.store.close()

    app = Starlette(routes=routes,
                    middleware=telemetry_middleware(tel)
                    + [Middleware(BaseHTTPMiddleware, dispatch=_no_store)],
                    lifespan=lifespan)
    app.state.jobs = ctx
    app.state.telemetry = tel
    app.state.scene_dir = scene_dir
    app.state.frontend_dir = frontend_dir
    return app


class _LazySqliteStore(JobStore):
    """SqliteJobStore that creates its file on first use, not at create_app().

    `server.py` builds a module-level app on import (for `uvicorn server:app`);
    importing it must not create out/jobs/ as a side effect.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._inner: SqliteJobStore | None = None

    def _s(self) -> SqliteJobStore:
        if self._inner is None:
            self._inner = SqliteJobStore(self._path)
        return self._inner

    def create(self, rec):
        return self._s().create(rec)

    def get(self, job_id):
        return self._s().get(job_id)

    def list(self):
        return self._s().list()

    def set_state(self, job_id, state, *, stage=None, error=None):
        return self._s().set_state(job_id, state, stage=stage, error=error)

    def delete(self, job_id):
        return self._s().delete(job_id)

    def close(self) -> None:
        if self._inner is not None:
            self._inner.close()
