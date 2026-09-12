"""RedisJobQueue under the JobQueue contract, on fakeredis (skipped without it)."""

from __future__ import annotations

import threading
import time

import pytest

fakeredis = pytest.importorskip("fakeredis", reason="fakeredis not installed")

from api.jobs.queue_redis import DEFAULT_KEY, RedisJobQueue, queue_from_env


@pytest.fixture()
def server():
    return fakeredis.FakeServer()


@pytest.fixture()
def q(server):
    return RedisJobQueue(client=fakeredis.FakeRedis(server=server, decode_responses=True))


def test_fifo_and_exactly_once(q, server):
    for i in range(3):
        q.put(f"job{i}")
    assert len(q) == 3
    other = RedisJobQueue(client=fakeredis.FakeRedis(server=server, decode_responses=True))
    assert q.get(timeout=0) == "job0"
    assert other.get(timeout=0) == "job1"  # a second consumer on the same list
    assert q.get(timeout=0) == "job2"
    assert q.get(timeout=0) is None and other.get(timeout=0) is None


def test_ids_are_str_even_for_a_bytes_client(server):
    raw = RedisJobQueue(client=fakeredis.FakeRedis(server=server))  # no decode_responses
    raw.put("abcdef0123456789")
    got = raw.get(timeout=0)
    assert got == "abcdef0123456789" and isinstance(got, str)


def test_get_times_out_with_none(q):
    t0 = time.monotonic()
    assert q.get(timeout=0.2) is None
    assert time.monotonic() - t0 < 2.5  # BRPOP blocks whole seconds, capped at one


def test_blocking_get_returns_a_late_put(q):
    got = []
    t = threading.Thread(target=lambda: got.append(q.get(timeout=5)))
    t.start()
    time.sleep(0.1)
    q.put("late")
    t.join(timeout=5)
    assert got == ["late"]


def test_close_wakes_a_blocked_consumer_and_disables_put(q):
    got = []
    t = threading.Thread(target=lambda: got.append(q.get(timeout=None)))
    t.start()
    time.sleep(0.1)
    q.close()
    t.join(timeout=5)
    assert not t.is_alive() and got == [None]
    q.put("ignored")
    assert len(q) == 0 and q.get(timeout=0) is None


def test_uses_the_documented_key_and_list_ops(server):
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    q = RedisJobQueue(client=client)
    q.put("a")
    q.put("b")
    assert client.lrange(DEFAULT_KEY, 0, -1) == ["b", "a"]  # LPUSH ... BRPOP = FIFO
    assert q.get(timeout=0) == "a"


def test_queue_from_env(monkeypatch):
    monkeypatch.delenv("SOBA_QUEUE_URL", raising=False)
    assert queue_from_env() is None
    made = {}

    class _Fake:
        @classmethod
        def from_url(cls, url, **kw):
            made["url"] = url
            return fakeredis.FakeRedis(decode_responses=True)

    import redis

    monkeypatch.setattr(redis, "Redis", _Fake)
    monkeypatch.setenv("SOBA_QUEUE_URL", "redis://user:pw@queue-host:6379/2")
    q = queue_from_env()
    assert isinstance(q, RedisJobQueue) and made["url"].endswith("queue-host:6379/2")
    monkeypatch.setenv("SOBA_QUEUE_URL", "amqp://x")
    with pytest.raises(ValueError, match="redis://"):
        queue_from_env()


def test_create_app_selects_redis_queue_from_env(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    from api.app import create_app

    class _Fake:
        @classmethod
        def from_url(cls, url, **kw):
            return fakeredis.FakeRedis(decode_responses=True)

    import redis

    monkeypatch.setattr(redis, "Redis", _Fake)
    monkeypatch.setenv("SOBA_QUEUE_URL", "redis://queue:6379/0")
    monkeypatch.setenv("SOBA_SSE_DELAY", "0")
    app = create_app(tmp_path / "scene", tmp_path / "frontend", jobs_dir=tmp_path / "jobs",
                     start_worker=False)
    assert isinstance(app.state.jobs.queue, RedisJobQueue)
    with TestClient(app):
        pass  # lifespan closes the queue cleanly
    monkeypatch.delenv("SOBA_QUEUE_URL")
    app = create_app(tmp_path / "scene", tmp_path / "frontend", jobs_dir=tmp_path / "jobs2",
                     start_worker=False)
    assert not isinstance(app.state.jobs.queue, RedisJobQueue)
