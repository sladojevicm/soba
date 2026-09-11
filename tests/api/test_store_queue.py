"""Job store state machine (memory + sqlite) and the in-process queue."""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from api.jobs.queue import InProcessQueue
from api.jobs.store import (
    DONE,
    FAILED,
    QUEUED,
    RUNNING,
    InvalidTransition,
    JobRecord,
    MemoryJobStore,
    SqliteJobStore,
    UnknownJob,
)


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemoryJobStore()
    return SqliteJobStore(tmp_path / "jobs.sqlite")


def _rec(i: int = 0, **kw) -> JobRecord:
    return JobRecord(id=f"{i:016x}", tier=2, source_format="bundle", **kw)


def test_create_get_roundtrip_and_initial_history(store):
    created = store.create(_rec(1, upload_name="x.zip", bundle_dir="/tmp/b"))
    got = store.get(created.id)
    assert got is not None
    assert got.state == QUEUED and got.status == "queued"
    assert got.upload_name == "x.zip" and got.bundle_dir == "/tmp/b"
    assert [h["status"] for h in got.history] == ["queued"]
    assert store.get("0" * 16) is None


def test_duplicate_id_rejected(store):
    store.create(_rec(1))
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        store.create(_rec(1))


def test_list_is_newest_first(store):
    store.create(_rec(1, created_at=10.0))
    store.create(_rec(2, created_at=30.0))
    store.create(_rec(3, created_at=20.0))
    assert [r.id for r in store.list()] == [_rec(2).id, _rec(3).id, _rec(1).id]


def test_happy_path_transitions_and_history(store):
    jid = store.create(_rec(1)).id
    r = store.set_state(jid, RUNNING, stage="validating")
    assert r.status == "running:validating"
    r = store.set_state(jid, RUNNING, stage="assembling")
    assert r.status == "running:assembling"
    r = store.set_state(jid, DONE)
    assert r.status == "done" and r.terminal and r.stage is None and r.error is None
    assert [h["status"] for h in store.get(jid).history] == [
        "queued", "running:validating", "running:assembling", "done"]


def test_same_stage_twice_is_a_noop(store):
    jid = store.create(_rec(1)).id
    store.set_state(jid, RUNNING, stage="assembling")
    store.set_state(jid, RUNNING, stage="assembling")
    assert [h["status"] for h in store.get(jid).history] == ["queued", "running:assembling"]


def test_failed_keeps_error_and_queued_may_fail_directly(store):
    jid = store.create(_rec(1)).id
    r = store.set_state(jid, FAILED, error="boom")
    assert r.status == "failed" and r.error == "boom" and r.terminal


@pytest.mark.parametrize("terminal", [DONE, FAILED])
def test_terminal_states_are_frozen(store, terminal):
    jid = store.create(_rec(1)).id
    store.set_state(jid, RUNNING, stage="x")
    store.set_state(jid, terminal, error="e" if terminal == FAILED else None)
    for nxt in (QUEUED, RUNNING, DONE, FAILED):
        with pytest.raises(InvalidTransition):
            store.set_state(jid, nxt, stage="y")
    assert store.get(jid).state == terminal  # unchanged after refused writes


def test_queued_to_done_is_not_allowed(store):
    jid = store.create(_rec(1)).id
    with pytest.raises(InvalidTransition):
        store.set_state(jid, DONE)
    with pytest.raises(InvalidTransition):
        store.set_state(jid, "bogus")


def test_unknown_job_raises(store):
    with pytest.raises(UnknownJob):
        store.set_state("f" * 16, RUNNING, stage="x")


def test_delete(store):
    jid = store.create(_rec(1)).id
    assert store.delete(jid) is True
    assert store.get(jid) is None
    assert store.delete(jid) is False


def test_returned_records_are_copies(store):
    rec = store.create(_rec(1))
    rec.state = "corrupted"
    assert store.get(rec.id).state == QUEUED


def test_to_dict_carries_status_and_scene_url():
    r = _rec(7, state=RUNNING, stage="assembling")
    d = r.to_dict()
    assert d["status"] == "running:assembling"
    assert d["scene_url"] == f"/jobs/{r.id}/"
    assert JobRecord.from_dict(d).id == r.id  # extra keys are ignored


def test_sqlite_persists_across_instances(tmp_path):
    path = tmp_path / "jobs.sqlite"
    a = SqliteJobStore(path)
    jid = a.create(_rec(1)).id
    a.set_state(jid, RUNNING, stage="validating")
    a.close()
    b = SqliteJobStore(path)
    got = b.get(jid)
    assert got is not None and got.status == "running:validating"
    assert [h["status"] for h in got.history] == ["queued", "running:validating"]


def test_sqlite_concurrent_writers_do_not_lose_transitions(tmp_path):
    store = SqliteJobStore(tmp_path / "jobs.sqlite")
    ids = [store.create(_rec(i)).id for i in range(20)]

    def work(jid):
        store.set_state(jid, RUNNING, stage="a")
        store.set_state(jid, DONE)

    threads = [threading.Thread(target=work, args=(j,)) for j in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(store.get(j).state == DONE for j in ids)


# --- queue --------------------------------------------------------------------
def test_queue_fifo_and_timeout():
    q = InProcessQueue()
    q.put("a")
    q.put("b")
    assert q.get(timeout=0) == "a"
    assert q.get(timeout=0) == "b"
    t0 = time.monotonic()
    assert q.get(timeout=0.05) is None
    assert time.monotonic() - t0 < 1.0


def test_queue_close_wakes_blocked_consumer_and_drops_later_puts():
    q = InProcessQueue()
    got = []

    def consume():
        got.append(q.get(timeout=5.0))

    t = threading.Thread(target=consume)
    t.start()
    time.sleep(0.05)
    q.close()
    t.join(timeout=2.0)
    assert not t.is_alive()
    assert got == [None]
    q.put("late")
    assert q.get(timeout=0) is None
    assert len(q) == 0
