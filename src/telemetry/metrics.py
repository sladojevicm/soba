"""Per-run metrics: stage timings, gate decisions, drops, remote calls.

One `RunMetrics` per pipeline run. `scripts/run_assemble.py` creates it with
`start_run()`, code anywhere in the run reaches it through `current()` (a
context variable, so a future multi-job worker gets one per job), and
`RunMetrics.write()` emits `out/<scene>/run_metrics.json` — validated against
`run_metrics.schema.json` in this package, the contract phase B (`/metrics`)
will read.

Rule (AGENTS.md, observability-agent): gate metrics are never recomputed here.
`record_gate()` stores exactly the dict `confidence.gate_object` returned.
"""

from __future__ import annotations

import contextvars
import json
import logging
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

from .jsonlog import log_event

log = logging.getLogger("telemetry")

SCHEMA_PATH = Path(__file__).with_name("run_metrics.schema.json")
SCHEMA_VERSION = 1
STRATEGIES = ("tsdf", "completion", "generative")
GENERATIVE = "generative"  # gate_object's tier-1 answer (confidence.GENERATIVE)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _num(v):
    return None if v is None else float(v)


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    with SCHEMA_PATH.open() as fh:
        return Draft202012Validator(json.load(fh))


def validate(payload: dict) -> None:
    """Raise `jsonschema.ValidationError` if `payload` breaks the contract."""
    _validator().validate(payload)


class RunMetrics:
    """Accumulator for one run. All `record_*` methods also log an event."""

    def __init__(self, **run_fields):
        self.started_at: str = _now()
        self.finished_at: str | None = None
        self.run: dict = {"status": "running", "error": None, **run_fields}
        self.stages: dict[str, dict] = {}
        self.gate: dict = {"counts": {s: 0 for s in STRATEGIES}, "per_object": []}
        self.drops: dict[str, int] = {}
        self.completion: dict = {"counts": {}, "per_object": []}
        self.remote: dict = {"calls": 0, "seconds": 0.0, "est_usd": None, "by_kind": {}}

    # --- recording -------------------------------------------------------
    def record_stage(self, name: str, seconds: float) -> None:
        st = self.stages.setdefault(name, {"seconds": 0.0, "count": 0})
        st["seconds"] += float(seconds)
        st["count"] += 1

    def record_gate(self, track_id: int, cls: str, tier: int, metrics: dict | None,
                    *, routed: str | None = None, cached: bool = False) -> dict:
        """Record one gate decision exactly as `confidence.gate_object` returned it.

        `metrics` is the gate dict `{angular_coverage_deg, completeness_ratio,
        strategy}` (values may be None at tier 1) or None when the gate was not
        run at all (tier 1 in run_assemble.py): both mean "generative, unscored".
        `routed` is the strategy actually applied (differs from `strategy` only
        under --force-strategy); `cached` marks a gate-cache hit.
        """
        if metrics is None:
            strategy, ang, comp = GENERATIVE, None, None
        else:
            strategy = str(metrics["strategy"])
            ang = _num(metrics.get("angular_coverage_deg"))
            comp = _num(metrics.get("completeness_ratio"))
        entry = {
            "track_id": int(track_id),
            "class": str(cls),
            "tier": int(tier),
            "strategy": strategy,
            "angular_coverage_deg": ang,
            "completeness_ratio": comp,
            "routed": str(routed or strategy),
            "cached": bool(cached),
        }
        counts = self.gate["counts"]
        counts[strategy] = counts.get(strategy, 0) + 1
        self.gate["per_object"].append(entry)
        log_event(log, logging.INFO, "gate", event="gate", **entry)
        return entry

    def record_drop(self, reason: str, **fields) -> None:
        self.drops[reason] = self.drops.get(reason, 0) + 1
        log_event(log, logging.INFO, "drop", event="drop", reason=reason, **fields)

    def record_completion(self, track_id: int, cls: str, method: str, *,
                          engine: str | None = None, reason: str | None = None,
                          fallback: bool = False, **fields) -> dict:
        """Record which completion actually ran for one completion-band object.

        `method` is the learned completer that produced the mesh
        ("patchcomplete", "pointr", "compc", ...) or a Poisson fallback
        ("poisson_fallback" when the configured engine declined / had no
        weights, "poisson_local" when no GPU engine exists at all). `fallback`
        marks anything that is NOT the configured learned completer, so a
        scene can never claim tier 2 while silently running Poisson.
        """
        entry = {"track_id": int(track_id), "class": str(cls), "method": str(method),
                 "engine": engine, "reason": reason, "fallback": bool(fallback), **fields}
        counts = self.completion["counts"]
        counts[method] = counts.get(method, 0) + 1
        self.completion["per_object"].append(entry)
        log_event(log, logging.WARNING if fallback else logging.INFO,
                  "completion", event="completion", **entry)
        return entry

    def record_remote_call(self, kind: str, seconds: float,
                           est_usd: float | None = None) -> None:
        seconds = float(seconds)
        self.remote["calls"] += 1
        self.remote["seconds"] += seconds
        bk = self.remote["by_kind"].setdefault(
            kind, {"calls": 0, "seconds": 0.0, "est_usd": None})
        bk["calls"] += 1
        bk["seconds"] += seconds
        if est_usd is not None:
            self.remote["est_usd"] = (self.remote["est_usd"] or 0.0) + float(est_usd)
            bk["est_usd"] = (bk["est_usd"] or 0.0) + float(est_usd)
        log_event(log, logging.INFO, "remote call", event="remote_call", kind=kind,
                  seconds=round(seconds, 4), est_usd=est_usd)

    def set_status(self, status: str, error: str | None = None) -> None:
        self.run["status"] = status
        self.run["error"] = error

    # --- output ----------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "run": dict(self.run),
            "stages": {k: {"seconds": round(v["seconds"], 6), "count": v["count"]}
                       for k, v in self.stages.items()},
            "gate": {"counts": dict(self.gate["counts"]),
                     "per_object": list(self.gate["per_object"])},
            "drops": dict(self.drops),
            "completion": {"counts": dict(self.completion["counts"]),
                           "per_object": list(self.completion["per_object"])},
            "remote": {**self.remote,
                       "seconds": round(self.remote["seconds"], 6),
                       "by_kind": {k: dict(v) for k, v in self.remote["by_kind"].items()}},
        }

    def write(self, path: Path | str) -> dict:
        """Stamp `finished_at`, validate, and atomically write `path`.

        Creates missing parent directories (the run may fail before the
        assembler creates the output folder). Returns the written dict.
        """
        path = Path(path)
        self.finished_at = _now()
        payload = self.to_dict()
        validate(payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str))
        tmp.replace(path)
        log_event(log, logging.INFO, "run metrics written", event="run_metrics",
                  path=str(path), status=self.run.get("status"),
                  gate_counts=payload["gate"]["counts"], drops=payload["drops"])
        return payload


# --- the active run -----------------------------------------------------
_current: contextvars.ContextVar[RunMetrics | None] = contextvars.ContextVar(
    "soba_run_metrics", default=None)


def start_run(**run_fields) -> RunMetrics:
    """Create the run's `RunMetrics` and make it `current()`."""
    m = RunMetrics(**{k: (str(v) if isinstance(v, Path) else v)
                      for k, v in run_fields.items()})
    _current.set(m)
    log_event(log, logging.INFO, "run started", event="run_start", **m.run)
    return m


def set_current(metrics: RunMetrics | None) -> None:
    _current.set(metrics)


def current() -> RunMetrics | None:
    return _current.get()


def drop(reason: str, **fields) -> None:
    """Record a drop on the active run; only logs when no run is active."""
    m = current()
    if m is not None:
        m.record_drop(reason, **fields)
    else:
        log_event(log, logging.INFO, "drop", event="drop", reason=reason, **fields)


def completion(track_id: int, cls: str, method: str, **fields) -> None:
    """Record a completion method on the active run; only logs when none is active."""
    m = current()
    if m is not None:
        m.record_completion(track_id, cls, method, **fields)
    else:
        log_event(log, logging.INFO, "completion", event="completion",
                  track_id=track_id, cls=cls, method=method, **fields)


@contextmanager
def stage_timer(name: str, *, metrics: RunMetrics | None = None,
                logger: logging.Logger | None = None, **fields):
    """Time a stage: DEBUG `stage.start`, INFO `stage.end` with `seconds`
    (ERROR with `ok=false`/`error` if the body raised — the exception is
    re-raised), and `record_stage()` on `metrics` or the active run."""
    lg = logger or log
    m = metrics if metrics is not None else current()
    log_event(lg, logging.DEBUG, f"{name} start", event="stage.start", stage=name,
              **fields)
    t0 = time.perf_counter()
    ok, err = True, None
    try:
        yield m
    except BaseException as exc:
        ok, err = False, f"{type(exc).__name__}: {exc}"
        raise
    finally:
        dt = time.perf_counter() - t0
        if m is not None:
            m.record_stage(name, dt)
        end = {"event": "stage.end", "stage": name, "seconds": round(dt, 4), **fields,
               "ok": ok}
        if err:
            end["error"] = err
        log_event(lg, logging.INFO if ok else logging.ERROR, f"{name} done", **end)
