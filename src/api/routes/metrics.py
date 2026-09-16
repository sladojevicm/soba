"""Observability phase B: `GET /metrics` (Prometheus text format) and the
request-logging middleware with job-id correlation.

One `ApiTelemetry` per app (its own `CollectorRegistry`, so several apps in
one process — tests — never collide). Registered in `api.app.create_app()` as
one import plus `metrics_routes(tel)` / `telemetry_middleware(tel)`.

Metrics (prefix `soba_`):

  http_requests_total{method,route,status}        counter
  http_request_duration_seconds{method,route}     histogram
  jobs{state}                                     gauge, from the JobStore at scrape time
  job_runs_total{status}                          run_metrics.json files ingested, by run.status
  gate_decisions_total{strategy}                  gate.counts (keep / complete / regenerate)
  gate_routed_total{strategy}                     per_object.routed (what was applied)
  pipeline_stage_seconds_total{stage}             stages.<name>.seconds
  pipeline_stage_runs_total{stage}                stages.<name>.count
  pipeline_drops_total{reason}                    drops
  completion_total{method}                        completion.counts (poisson_* = fallback)
  remote_calls_total{kind} / remote_call_seconds_total{kind} / remote_est_usd_total{kind}
  run_metrics_ingest_errors_total                 unreadable / invalid run_metrics.json files

Everything pipeline-side comes from each finished job's
`<jobs_dir>/<id>/scene/run_metrics.json` (the phase-A contract, validated
against src/telemetry/run_metrics.schema.json) — read once when the job is
first seen terminal, never recomputed. Ingestion runs at scrape time so an
external worker (a process that only shares the JobStore) is covered too.

`prometheus_client` is the optional `telemetry` extra: without it the route
answers 501 and everything else (logs, run_metrics.json) is unaffected.

`route` labels are path TEMPLATES (`/jobs/{id}/scene.json`), never raw paths,
so cardinality stays bounded; unknown paths are labelled `/other`.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time

from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

import telemetry
from telemetry import log_event

from ..errors import api_error
from ..jobs.store import STATES

try:
    import prometheus_client as _prom
except ImportError:  # optional `telemetry` extra
    _prom = None

log = logging.getLogger("api.http")
ingest_log = logging.getLogger("api.metrics")

MISSING_MSG = ("prometheus_client is not installed: pip install -e '.[telemetry]' "
               "to enable GET /metrics")

_JOB_RE = re.compile(r"^/(?:api/)?jobs/([^/]+)")
_SUBS = (
    (re.compile(r"^/(api/)?jobs/[^/]+"), r"/\1jobs/{id}"),
    (re.compile(r"/meshes/[^/]+\.glb$"), "/meshes/{id}.glb"),
    (re.compile(r"/hulls/[^/]+\.glb$"), "/hulls/{stem}.glb"),
    (re.compile(r"^/assets/.+$"), "/assets/*"),
)
_SCENE = ("/", "/scene.json", "/eval.json", "/meshes/{id}.glb", "/hulls/{stem}.glb",
          "/events")
KNOWN_ROUTES = frozenset(
    list(_SCENE) + [f"/jobs/{{id}}{p}" for p in _SCENE] + ["/jobs/{id}"]
    + ["/api/jobs", "/api/jobs/{id}", "/metrics", "/assets/*"])
OTHER_ROUTE = "/other"


def job_id_from_path(path: str) -> str | None:
    """`/jobs/<id>/...` or `/api/jobs/<id>` -> `<id>` (else None)."""
    m = _JOB_RE.match(path)
    return m.group(1) if m else None


def route_template(path: str) -> str:
    """Raw path -> bounded route label (see module docstring)."""
    for rx, repl in _SUBS:
        path = rx.sub(repl, path)
    return path if path in KNOWN_ROUTES else OTHER_ROUTE


def ensure_logging() -> None:
    """Install telemetry's root handler unless something already configured
    logging (uvicorn does not touch the root; pytest's caplog does)."""
    if not logging.getLogger().handlers:
        telemetry.configure_logging()


class ApiTelemetry:
    """Per-app metrics registry + run_metrics.json ingestion."""

    def __init__(self, ctx, *, registry=None):
        self.ctx = ctx
        self.enabled = _prom is not None
        self._ingested: set[str] = set()
        self._lock = threading.Lock()  # concurrent scrapes: ingest each job once
        ensure_logging()
        if not self.enabled:
            return
        r = self.registry = registry or _prom.CollectorRegistry()
        C, G, H = _prom.Counter, _prom.Gauge, _prom.Histogram
        self.http_requests = C("soba_http_requests_total", "HTTP requests",
                               ["method", "route", "status"], registry=r)
        self.http_latency = H("soba_http_request_duration_seconds", "HTTP request latency",
                              ["method", "route"], registry=r,
                              buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0,
                                       2.5, 5.0, 10.0, 30.0))
        self.jobs = G("soba_jobs", "Jobs by state, from the JobStore at scrape time",
                      ["state"], registry=r)
        self.job_runs = C("soba_job_runs_total",
                          "run_metrics.json files ingested, by run.status", ["status"],
                          registry=r)
        self.gate = C("soba_gate_decisions_total",
                      "Gate decisions (tsdf=keep, completion=complete, generative=regenerate)",
                      ["strategy"], registry=r)
        self.gate_routed = C("soba_gate_routed_total", "Strategy actually applied per object",
                             ["strategy"], registry=r)
        self.stage_seconds = C("soba_pipeline_stage_seconds_total",
                               "Pipeline stage wall time", ["stage"], registry=r)
        self.stage_runs = C("soba_pipeline_stage_runs_total", "Pipeline stage invocations",
                            ["stage"], registry=r)
        self.drops = C("soba_pipeline_drops_total", "Objects dropped, by reason", ["reason"],
                       registry=r)
        self.completion = C("soba_completion_total",
                            "Completion method per completion-band object "
                            "(poisson_* = fallback, not the configured learned completer)",
                            ["method"], registry=r)
        self.remote_calls = C("soba_remote_calls_total", "Remote (RunPod) calls", ["kind"],
                              registry=r)
        self.remote_seconds = C("soba_remote_call_seconds_total", "Remote call wall time",
                                ["kind"], registry=r)
        self.remote_usd = C("soba_remote_est_usd_total", "Estimated remote spend (USD)",
                            ["kind"], registry=r)
        self.ingest_errors = C("soba_run_metrics_ingest_errors_total",
                               "run_metrics.json files that could not be ingested",
                               registry=r)
        for s in STATES:
            self.jobs.labels(state=s).set(0)
        for s in telemetry.STRATEGIES:
            self.gate.labels(strategy=s)
            self.gate_routed.labels(strategy=s)
        for s in ("ok", "failed"):
            self.job_runs.labels(status=s)

    # --- HTTP --------------------------------------------------------------
    def observe_request(self, method: str, route: str, status: int, seconds: float) -> None:
        if not self.enabled:
            return
        self.http_requests.labels(method=method, route=route, status=str(status)).inc()
        self.http_latency.labels(method=method, route=route).observe(seconds)

    # --- jobs + run_metrics.json ------------------------------------------
    def refresh(self) -> int:
        """Scrape-time work: job-state gauges + ingest newly terminal jobs.
        Returns the number of run_metrics.json files ingested this call."""
        with self._lock:
            return self._refresh()

    def _refresh(self) -> int:
        recs = self.ctx.store.list()
        if self.enabled:
            counts = dict.fromkeys(STATES, 0)
            for rec in recs:
                counts[rec.state] = counts.get(rec.state, 0) + 1
            for state, n in counts.items():
                self.jobs.labels(state=state).set(n)
        n = 0
        for rec in recs:
            if rec.terminal and rec.id not in self._ingested:
                self._ingested.add(rec.id)
                n += int(self.ingest_job(rec.id))
        return n

    def ingest_job(self, job_id: str) -> bool:
        """Read `<job>/scene/run_metrics.json` once; True if it was ingested.
        A missing file (mock worker, failed before the run started) is not an
        error; an unreadable or contract-breaking one is counted and logged."""
        path = self.ctx.paths(job_id).scene_dir / "run_metrics.json"
        if not path.is_file():
            log_event(ingest_log, logging.DEBUG, "no run_metrics.json", event="ingest_skip",
                      job_id=job_id, path=str(path))
            return False
        try:
            data = json.loads(path.read_text())
            telemetry.validate(data)
        except Exception as exc:  # any bad file: count it, keep serving
            if self.enabled:
                self.ingest_errors.inc()
            log_event(ingest_log, logging.WARNING, "run_metrics.json rejected",
                      event="ingest_error", job_id=job_id, path=str(path),
                      error=f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
            return False
        self.ingest_run_metrics(data)
        log_event(ingest_log, logging.INFO, "run_metrics.json ingested", event="ingest",
                  job_id=job_id, status=data.get("run", {}).get("status"),
                  gate_counts=data["gate"]["counts"], drops=data["drops"])
        return True

    def ingest_run_metrics(self, data: dict) -> None:
        """Feed one validated run_metrics.json payload into the counters."""
        if not self.enabled:
            return
        self.job_runs.labels(status=data.get("run", {}).get("status") or "unknown").inc()
        for strategy, n in data["gate"]["counts"].items():
            self.gate.labels(strategy=strategy).inc(n)
        for obj in data["gate"]["per_object"]:
            self.gate_routed.labels(strategy=obj.get("routed") or obj["strategy"]).inc()
        for stage, st in data["stages"].items():
            self.stage_seconds.labels(stage=stage).inc(st["seconds"])
            self.stage_runs.labels(stage=stage).inc(st["count"])
        for reason, n in data["drops"].items():
            self.drops.labels(reason=reason).inc(n)
        for method, n in data.get("completion", {}).get("counts", {}).items():
            self.completion.labels(method=method).inc(n)
        for kind, rc in data["remote"]["by_kind"].items():
            self.remote_calls.labels(kind=kind).inc(rc["calls"])
            self.remote_seconds.labels(kind=kind).inc(rc["seconds"])
            if rc.get("est_usd"):
                self.remote_usd.labels(kind=kind).inc(rc["est_usd"])

    def render(self) -> bytes:
        self.refresh()
        return _prom.generate_latest(self.registry)

    # --- registration ------------------------------------------------------
    def routes(self) -> list[Route]:
        async def metrics(request: Request):
            if not self.enabled:
                return api_error(501, "metrics_unavailable", MISSING_MSG)
            body = await run_in_threadpool(self.render)
            return Response(body, media_type=_prom.CONTENT_TYPE_LATEST)

        return [Route("/metrics", metrics, methods=["GET"])]

    def middleware(self) -> list[Middleware]:
        return [Middleware(RequestTelemetryMiddleware, telemetry=self)]


def metrics_routes(tel: ApiTelemetry) -> list[Route]:
    return tel.routes()


def telemetry_middleware(tel: ApiTelemetry) -> list[Middleware]:
    return tel.middleware()


class RequestTelemetryMiddleware:
    """Pure-ASGI: one `http` log event per request (method, path, route
    template, status, seconds, job_id when the path carries one, client) and
    the request counter / latency histogram. The duration covers the whole
    response, so an SSE `/events` stream logs when it ends."""

    def __init__(self, app, telemetry: ApiTelemetry):
        self.app = app
        self.telemetry = telemetry

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method", "GET")
        route = route_template(path)
        job_id = job_id_from_path(path)
        status = 0
        t0 = time.perf_counter()

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            status = status or 500
            self._record(method, path, route, status, t0, job_id, scope, failed=True)
            raise
        self._record(method, path, route, status, t0, job_id, scope)

    def _record(self, method, path, route, status, t0, job_id, scope, *, failed=False):
        seconds = time.perf_counter() - t0
        self.telemetry.observe_request(method, route, status, seconds)
        client = scope.get("client")
        fields = {"event": "http", "method": method, "path": path, "route": route,
                  "status": status, "seconds": round(seconds, 4),
                  "client": client[0] if client else None}
        if job_id:
            fields["job_id"] = job_id
        if failed:
            fields["unhandled"] = True
        level = (logging.ERROR if failed else
                 logging.DEBUG if route == "/metrics" else logging.INFO)
        log_event(log, level, f"{method} {path} {status}", **fields)
