"""External job worker: consumes the JobQueue (Redis) and runs each job.

    python -m orchestration.worker --jobs-dir out/jobs            # SOBA_QUEUE_URL=redis://...
    python -m orchestration.worker --once --mode gate-only        # one job, then exit

Same code path as the in-process thread (api.jobs.worker_local.LocalWorker:
validating -> converting -> assembling -> done | failed, `scripts/run_assemble.py`
as a subprocess in `real` / `gate-only` mode), so the API's thread and this
process are interchangeable. Run the API with SOBA_WORKER_INPROC=0 when this
worker owns the queue. What this adds on top of LocalWorker:

* every job state transition is logged as a `job_state` event (JSON lines
  with SOBA_LOG_JSON=1) for the observability layer;
* a per-job cost record, `<jobs_dir>/<id>/scene/cost.json`, next to
  `run_metrics.json`: RunPod calls, billable seconds and estimated USD, the
  RunPod drop reasons, the configured budget and price (config/pipeline.yaml
  `runpod:`), and whether the kill switch was on;
* graceful stop: SIGTERM / SIGINT finish the job in progress (the
  run_assemble subprocess is never killed mid-write) and then exit;
* `--once`: take at most one job and exit (batch runners, tests).

Kill switch: SOBA_RUNPOD_DISABLED=1 in this process's environment reaches
the subprocess unchanged; RunPodEngine then drops every generative object
without a network call (recorded as `runpod_disabled` drops).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from api.jobs import store as _store
from api.jobs.paths import JobPaths
from api.jobs.queue import JobQueue
from api.jobs.queue_redis import queue_from_env
from api.jobs.store import JobStore, SqliteJobStore
from api.jobs.worker_local import MODES, REPO_ROOT, LocalWorker
from reconstruction import runpod_policy
from reconstruction.runpod_policy import Budget
from telemetry.jsonlog import log_event

log = logging.getLogger("orchestration.worker")

COST_RECORD_NAME = "cost.json"
COST_SCHEMA_VERSION = 1
RUN_METRICS_NAME = "run_metrics.json"
RUNPOD_DROPS = (runpod_policy.DROP_FAILED, runpod_policy.DROP_BUDGET,
                runpod_policy.DROP_BREAKER_OPEN, runpod_policy.DROP_DISABLED)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def cost_record(job_id: str, rec: _store.JobRecord | None, scene_dir: Path, *,
                mode: str, wall_seconds: float, cfg: dict | None = None) -> dict:
    """Build the per-job cost record from `<scene_dir>/run_metrics.json`
    (absent in mock mode or when the run died early -> zeros, source null)."""
    cfg = cfg or runpod_policy.load_runpod_config()
    metrics = None
    mpath = scene_dir / RUN_METRICS_NAME
    if mpath.is_file():
        try:
            metrics = json.loads(mpath.read_text())
        except (OSError, ValueError) as exc:
            log.warning("job %s: unreadable %s: %s", job_id, mpath, exc)
    remote = (metrics or {}).get("remote") or {}
    drops = (metrics or {}).get("drops") or {}
    budget = Budget.from_config(cfg)
    budget.calls = int(remote.get("calls") or 0)
    budget.gpu_seconds = float(remote.get("seconds") or 0.0)
    budget.est_usd = float(remote.get("est_usd") or 0.0)
    return {
        "schema": COST_SCHEMA_VERSION,
        "job_id": job_id,
        "tier": rec.tier if rec else None,
        "mode": mode,
        "written_at": _now_iso(),
        "source": RUN_METRICS_NAME if metrics else None,
        "run": {
            "status": rec.state if rec else None,       # done | failed
            "error": rec.error if rec else None,
            "wall_seconds": round(float(wall_seconds), 3),
            "pipeline_status": (metrics or {}).get("run", {}).get("status"),
        },
        "remote": {
            "calls": budget.calls,
            "gpu_seconds": round(budget.gpu_seconds, 3),
            "est_usd": round(budget.est_usd, 6),
            "by_kind": {k: dict(v) for k, v in (remote.get("by_kind") or {}).items()},
        },
        "drops": {k: int(drops.get(k, 0)) for k in RUNPOD_DROPS},
        "budget": {"max_calls": budget.max_calls,
                   "max_gpu_seconds": budget.max_gpu_seconds,
                   "max_est_usd": budget.max_est_usd,
                   "exhausted": budget.exhausted()},
        "price": {"usd_per_gpu_second": runpod_policy.usd_per_gpu_second(cfg)},
        "runpod_disabled": runpod_policy.disabled(),
    }


class OrchestrationWorker(LocalWorker):
    """LocalWorker run as the main loop of its own process."""

    def __init__(self, store: JobStore, queue: JobQueue, jobs_dir: Path | str, **kw) -> None:
        super().__init__(store, queue, jobs_dir, **kw)
        self.jobs_done = 0
        self.jobs_failed = 0

    # -- state transitions -> log events --------------------------------------
    def _set(self, job_id, state, stage=None, error=None) -> bool:
        ok = super()._set(job_id, state, stage=stage, error=error)
        status = f"{state}:{stage}" if state == _store.RUNNING and stage else state
        log_event(log, logging.INFO if ok else logging.WARNING, f"job {job_id} {status}",
                  event="job_state", job_id=job_id, status=status, error=error, ok=ok)
        return ok

    # -- one job + its cost record --------------------------------------------
    def process(self, job_id: str) -> None:
        t0 = time.perf_counter()
        super().process(job_id)
        rec = self.store.get(job_id)
        if rec is None:
            return  # deleted meanwhile; nothing to record against
        if rec.state == _store.DONE:
            self.jobs_done += 1
        elif rec.state == _store.FAILED:
            self.jobs_failed += 1
        try:
            self.write_cost_record(job_id, rec, time.perf_counter() - t0)
        except Exception:  # accounting never fails the worker
            log.exception("job %s: could not write %s", job_id, COST_RECORD_NAME)

    def write_cost_record(self, job_id: str, rec: _store.JobRecord,
                          wall_seconds: float) -> dict:
        scene_dir = JobPaths(self.jobs_dir, job_id).scene_dir
        record = cost_record(job_id, rec, scene_dir, mode=self.mode,
                             wall_seconds=wall_seconds)
        scene_dir.mkdir(parents=True, exist_ok=True)
        path = scene_dir / COST_RECORD_NAME
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(record, indent=2))
        tmp.replace(path)
        log_event(log, logging.INFO, f"job {job_id} cost record", event="job_cost",
                  job_id=job_id, path=str(path), **record["remote"],
                  drops=record["drops"], exhausted=record["budget"]["exhausted"])
        return record

    # -- main loop --------------------------------------------------------------
    def request_stop(self, reason: str = "signal") -> None:
        """Finish the job in progress, then let serve() return."""
        if not self._stop.is_set():
            log.info("stop requested (%s): finishing the current job, then exiting", reason)
        self._stop.set()

    def install_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda signum, frame: self.request_stop(
                signal.Signals(signum).name))

    def serve(self, *, once: bool = False, poll_s: float = 5.0) -> int:
        """Consume until stopped (or one job with `once`); jobs taken."""
        taken = 0
        while not self._stop.is_set():
            if self.run_once(timeout=poll_s):
                taken += 1
                if once:
                    break
            elif once:
                break  # nothing queued within poll_s
        return taken


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m orchestration.worker",
        description="Consume the Soba job queue and run jobs (external worker).")
    ap.add_argument("--jobs-dir", type=Path,
                    default=Path(os.environ.get("SOBA_JOBS_DIR") or REPO_ROOT / "out" / "jobs"),
                    help="job store + job dirs shared with the API (SOBA_JOBS_DIR)")
    ap.add_argument("--queue-url", default=None,
                    help="redis://... (default: SOBA_QUEUE_URL); required")
    ap.add_argument("--mode", choices=MODES,
                    default=os.environ.get("SOBA_WORKER_MODE") or "real",
                    help="real (pod), gate-only, or mock (SOBA_WORKER_MODE; default real)")
    ap.add_argument("--once", action="store_true",
                    help="take at most one job (waiting up to --poll-s for it), then exit")
    ap.add_argument("--poll-s", type=float, default=5.0,
                    help="queue wait per loop; the stop signal is honoured between waits")
    ap.add_argument("--python", default=None,
                    help="interpreter for scripts/run_assemble.py (default: this one)")
    ap.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    return ap


def main(argv: list[str] | None = None) -> int:
    import telemetry

    telemetry.configure_logging()
    args = build_parser().parse_args(argv)
    try:
        queue = queue_from_env(args.queue_url)
    except ValueError as exc:
        log.error("%s", exc)
        return 2
    if queue is None:
        log.error("no queue: pass --queue-url or set SOBA_QUEUE_URL=redis://host:6379/0 "
                  "(the in-process queue lives inside the API; this worker needs Redis)")
        return 2
    jobs_dir = args.jobs_dir.resolve()
    store = SqliteJobStore(jobs_dir / "jobs.sqlite")
    worker = OrchestrationWorker(
        store, queue, jobs_dir, mode=args.mode, python=args.python,
        repo_root=args.repo_root,
        fixture_scene=os.environ.get("SOBA_FIXTURE_SCENE") or None,
        mock_delay_s=float(os.environ.get("SOBA_MOCK_DELAY_S", "0")))
    worker.install_signal_handlers()
    log_event(log, logging.INFO, "worker started", event="worker_start",
              jobs_dir=str(jobs_dir), mode=args.mode, once=args.once,
              runpod_disabled=runpod_policy.disabled(),
              budget=runpod_policy.load_runpod_config()["budget"])
    try:
        taken = worker.serve(once=args.once, poll_s=args.poll_s)
    finally:
        queue.close()
        store.close()
    log_event(log, logging.INFO, "worker stopped", event="worker_stop", jobs_taken=taken,
              jobs_done=worker.jobs_done, jobs_failed=worker.jobs_failed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
