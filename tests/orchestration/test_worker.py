"""orchestration.worker: the external queue consumer, CPU-only.

`real` mode is exercised with a stand-in `scripts/run_assemble.py` under a
temporary repo root (the worker spawns `<python> <repo_root>/scripts/run_assemble.py`),
which writes scene.json + run_metrics.json the way the real driver does. No
open3d, no Redis server (fakeredis for the CLI test).
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from api.jobs.paths import JobPaths
from api.jobs.queue import InProcessQueue
from api.jobs.store import DONE, FAILED, JobRecord, MemoryJobStore, SqliteJobStore
from orchestration import worker as ow
from reconstruction import runpod_policy

CFG = runpod_policy.load_runpod_config()


def make_bundle(root: Path, frames: int = 1) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps(
        {"session_id": "t", "fps": 30.0, "frame_count": frames, "source": "tum"}))
    (root / "intrinsics.json").write_text(json.dumps(
        {"fx": 525.0, "fy": 525.0, "cx": 319.5, "cy": 239.5}))
    for i in range(frames):
        d = root / "frames" / f"{i:05d}"
        d.mkdir(parents=True)
        (d / "rgb.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        (d / "depth.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (d / "objects.json").write_text("[]")
    return root


FAKE_RUN_ASSEMBLE = r'''
import argparse, json, os, sys
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument("--bundle"); ap.add_argument("--tier", type=int); ap.add_argument("--out")
ap.add_argument("--no-eval", action="store_true"); ap.add_argument("--gate-only", action="store_true")
a = ap.parse_args()
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
script = json.loads(os.environ.get("FAKE_RUN_ASSEMBLE", "{}"))
(out / "env.json").write_text(json.dumps({"SOBA_RUNPOD_DISABLED": os.environ.get("SOBA_RUNPOD_DISABLED")}))
if script.get("metrics") is not None:
    (out / "run_metrics.json").write_text(json.dumps(script["metrics"]))
if script.get("exit"):
    print("boom", file=sys.stderr); sys.exit(script["exit"])
(out / "scene.json").write_text(json.dumps({"version": "2.0", "objects": []}))
'''

METRICS = {
    "schema": 1, "started_at": "x", "finished_at": "y",
    "run": {"status": "ok", "error": None, "tier": 2},
    "stages": {}, "gate": {"counts": {"tsdf": 0, "completion": 0, "generative": 3},
                           "per_object": []},
    "drops": {"runpod_failed": 1, "runpod_budget_exhausted": 2, "cloud_too_small": 4},
    "remote": {"calls": 3, "seconds": 400.0, "est_usd": 0.176,
               "by_kind": {"runpod/gen": {"calls": 3, "seconds": 400.0, "est_usd": 0.176}}},
}


@pytest.fixture()
def fake_repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "run_assemble.py").write_text(FAKE_RUN_ASSEMBLE)
    return root


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """store + queue + one queued bundle job; returns a factory for the worker."""
    monkeypatch.delenv("SOBA_RUNPOD_DISABLED", raising=False)
    monkeypatch.delenv("FAKE_RUN_ASSEMBLE", raising=False)
    jobs_dir = tmp_path / "jobs"
    store, queue = MemoryJobStore(), InProcessQueue()

    def add_job(job_id="0123456789abcdef", tier=2):
        paths = JobPaths(jobs_dir, job_id)
        make_bundle(paths.extracted_dir)
        store.create(JobRecord(id=job_id, tier=tier, source_format="bundle",
                               bundle_dir=str(paths.extracted_dir)))
        queue.put(job_id)
        return job_id

    def make(**kw):
        return ow.OrchestrationWorker(store, queue, jobs_dir, **kw)

    return {"store": store, "queue": queue, "jobs_dir": jobs_dir, "add": add_job,
            "make": make}


def _cost(env, job_id) -> dict:
    return json.loads((JobPaths(env["jobs_dir"], job_id).scene_dir / "cost.json").read_text())


def test_mock_job_runs_to_done_with_a_zero_cost_record(env, tmp_path):
    jid = env["add"]()
    w = env["make"](mode="mock", fixture_scene=tmp_path / "nonexistent")
    assert w.serve(once=True, poll_s=0.1) == 1
    rec = env["store"].get(jid)
    assert rec.state == DONE
    assert [h["status"] for h in rec.history] == [
        "queued", "running:validating", "running:assembling", "done"]
    c = _cost(env, jid)
    assert c["schema"] == 1 and c["job_id"] == jid and c["tier"] == 2 and c["mode"] == "mock"
    assert c["source"] is None and c["run"]["status"] == "done" and c["run"]["error"] is None
    assert c["run"]["wall_seconds"] >= 0.0 and c["run"]["pipeline_status"] is None
    assert c["remote"] == {"calls": 0, "gpu_seconds": 0.0, "est_usd": 0.0, "by_kind": {}}
    assert c["drops"] == {"runpod_failed": 0, "runpod_budget_exhausted": 0,
                          "runpod_breaker_open": 0, "runpod_disabled": 0}
    assert c["budget"] == {**{k: CFG["budget"][k] for k in
                              ("max_calls", "max_gpu_seconds", "max_est_usd")},
                           "exhausted": None}
    assert c["price"] == {"usd_per_gpu_second": CFG["price"]["usd_per_gpu_second"]}
    assert c["runpod_disabled"] is False
    assert w.jobs_done == 1 and w.jobs_failed == 0


def test_real_mode_aggregates_run_metrics_into_the_cost_record(env, fake_repo, monkeypatch):
    monkeypatch.setenv("FAKE_RUN_ASSEMBLE", json.dumps({"metrics": METRICS}))
    jid = env["add"](tier=3)
    w = env["make"](mode="real", python=sys.executable, repo_root=fake_repo)
    assert w.serve(once=True, poll_s=0.1) == 1
    assert env["store"].get(jid).state == DONE
    c = _cost(env, jid)
    assert c["source"] == "run_metrics.json" and c["run"]["pipeline_status"] == "ok"
    assert c["remote"]["calls"] == 3 and c["remote"]["gpu_seconds"] == 400.0
    assert c["remote"]["est_usd"] == 0.176
    assert c["remote"]["by_kind"]["runpod/gen"]["calls"] == 3
    assert c["drops"] == {"runpod_failed": 1, "runpod_budget_exhausted": 2,
                          "runpod_breaker_open": 0, "runpod_disabled": 0}
    assert c["budget"]["exhausted"] is None  # 3 calls / 400 s / $0.18 under the caps
    scene = JobPaths(env["jobs_dir"], jid).scene_dir
    assert (scene / "scene.json").is_file() and (scene / "run_metrics.json").is_file()


def test_budget_exhaustion_is_derived_from_the_totals(env, fake_repo, monkeypatch):
    over = json.loads(json.dumps(METRICS))
    over["remote"]["calls"] = CFG["budget"]["max_calls"]
    monkeypatch.setenv("FAKE_RUN_ASSEMBLE", json.dumps({"metrics": over}))
    jid = env["add"]()
    w = env["make"](mode="real", python=sys.executable, repo_root=fake_repo)
    w.serve(once=True, poll_s=0.1)
    assert _cost(env, jid)["budget"]["exhausted"] == "calls"


def test_failed_run_still_gets_a_cost_record(env, fake_repo, monkeypatch):
    monkeypatch.setenv("FAKE_RUN_ASSEMBLE", json.dumps({"exit": 3, "metrics": {
        **METRICS, "run": {"status": "failed", "error": "RuntimeError: gate exploded"}}}))
    jid = env["add"]()
    w = env["make"](mode="real", python=sys.executable, repo_root=fake_repo)
    w.serve(once=True, poll_s=0.1)
    rec = env["store"].get(jid)
    assert rec.state == FAILED and "exited 3" in rec.error
    c = _cost(env, jid)
    assert c["run"]["status"] == "failed" and "exited 3" in c["run"]["error"]
    assert c["run"]["pipeline_status"] == "failed" and c["remote"]["calls"] == 3
    assert w.jobs_failed == 1


def test_kill_switch_reaches_the_subprocess_and_the_record(env, fake_repo, monkeypatch):
    monkeypatch.setenv("SOBA_RUNPOD_DISABLED", "1")
    jid = env["add"]()
    w = env["make"](mode="real", python=sys.executable, repo_root=fake_repo)
    w.serve(once=True, poll_s=0.1)
    scene = JobPaths(env["jobs_dir"], jid).scene_dir
    assert json.loads((scene / "env.json").read_text()) == {"SOBA_RUNPOD_DISABLED": "1"}
    assert _cost(env, jid)["runpod_disabled"] is True


def test_once_with_an_empty_queue_returns_promptly(env):
    w = env["make"](mode="mock")
    t0 = time.monotonic()
    assert w.serve(once=True, poll_s=0.2) == 0
    assert time.monotonic() - t0 < 2.0


def test_stop_finishes_the_current_job_then_exits(env, tmp_path):
    for i in range(3):
        env["add"](f"{i:016x}")
    w = env["make"](mode="mock", mock_delay_s=0.3, fixture_scene=tmp_path / "none")
    taken = []
    t = threading.Thread(target=lambda: taken.append(w.serve(poll_s=0.1)))
    t.start()
    time.sleep(0.1)  # inside job 1's assembling stage
    w.request_stop("SIGTERM")
    t.join(timeout=10)
    assert not t.is_alive() and taken == [1]
    states = [env["store"].get(f"{i:016x}").state for i in range(3)]
    assert states == [DONE, "queued", "queued"]  # job 1 finished, 2 and 3 untouched
    assert len(env["queue"]) == 2


def test_signal_handlers_request_stop(env, monkeypatch):
    w = env["make"](mode="mock")
    installed = {}
    monkeypatch.setattr(signal, "signal", lambda sig, h: installed.__setitem__(sig, h))
    w.install_signal_handlers()
    assert set(installed) == {signal.SIGTERM, signal.SIGINT}
    installed[signal.SIGTERM](signal.SIGTERM, None)
    assert w._stop.is_set() and w.serve(once=True, poll_s=0.1) == 0


def test_state_transitions_are_logged_as_events(env, tmp_path, caplog):
    jid = env["add"]()
    w = env["make"](mode="mock", fixture_scene=tmp_path / "none")
    with caplog.at_level("INFO"):
        w.serve(once=True, poll_s=0.1)
    fields = [getattr(r, "fields", {}) for r in caplog.records]
    # Accepted transitions are emitted once each, by JobStore (logger "api.jobs").
    events = [f for f in fields if f.get("event") == "job_state"]
    assert [e["status"] for e in events] == ["running:validating", "running:assembling", "done"]
    assert all(e["job_id"] == jid for e in events)
    assert not [f for f in fields if f.get("event") == "job_state_refused"]
    assert any(f.get("event") == "job_cost" for f in fields)


# --- CLI -------------------------------------------------------------------------
def test_main_refuses_to_run_without_a_queue(monkeypatch, tmp_path):
    monkeypatch.delenv("SOBA_QUEUE_URL", raising=False)
    assert ow.main(["--jobs-dir", str(tmp_path / "jobs"), "--once"]) == 2
    assert ow.main(["--jobs-dir", str(tmp_path / "jobs"), "--queue-url", "amqp://x"]) == 2


def test_main_consumes_a_redis_queue_once(monkeypatch, tmp_path):
    fakeredis = pytest.importorskip("fakeredis")
    import redis

    server = fakeredis.FakeServer()
    monkeypatch.setattr(redis.Redis, "from_url", classmethod(
        lambda cls, url, **kw: fakeredis.FakeRedis(server=server, decode_responses=True)))
    monkeypatch.setattr(signal, "signal", lambda sig, h: None)  # not the main thread's business
    jobs_dir = tmp_path / "jobs"
    jid = "fedcba9876543210"
    paths = JobPaths(jobs_dir, jid)
    make_bundle(paths.extracted_dir)
    store = SqliteJobStore(jobs_dir / "jobs.sqlite")
    store.create(JobRecord(id=jid, tier=1, source_format="bundle",
                           bundle_dir=str(paths.extracted_dir)))
    fakeredis.FakeRedis(server=server, decode_responses=True).lpush("soba:jobs", jid)
    monkeypatch.setenv("SOBA_FIXTURE_SCENE", str(tmp_path / "none"))
    rc = ow.main(["--jobs-dir", str(jobs_dir), "--queue-url", "redis://q:6379/0",
                  "--mode", "mock", "--once", "--poll-s", "0.2"])
    assert rc == 0
    assert store.get(jid).state == DONE
    assert (paths.scene_dir / "scene.json").is_file() and (paths.scene_dir / "cost.json").is_file()


def test_module_is_runnable(tmp_path):
    import subprocess

    env = {**os.environ, "PYTHONPATH": str(Path(ow.__file__).resolve().parents[1])}
    env.pop("SOBA_QUEUE_URL", None)
    p = subprocess.run([sys.executable, "-m", "orchestration.worker", "--help"],
                       capture_output=True, text=True, env=env, check=False)
    assert p.returncode == 0 and "--once" in p.stdout and "--queue-url" in p.stdout
