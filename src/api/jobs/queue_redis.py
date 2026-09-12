"""Redis implementation of the JobQueue contract (api.jobs.queue).

    put(job_id)    -> LPUSH  <key> job_id
    get(timeout)   -> BRPOP  <key> timeout     (None on timeout / after close)
    close()        -> flag; a blocked get() returns None within one poll window

Selected by ``SOBA_QUEUE_URL=redis://host:6379/0`` in ``api.app.create_app``
(and by ``orchestration.worker``); the default stays the in-process queue.
Needs the ``worker`` extra (``redis``). One Redis list is the whole queue, so
one API process can feed several external workers on other hosts. Semantics
kept from the contract: FIFO, each id delivered to exactly one consumer, no
ack (a worker that dies mid-job leaves the job ``running`` in the store).
"""

from __future__ import annotations

import logging
import threading
import time

from .queue import JobQueue

log = logging.getLogger("api.queue.redis")

DEFAULT_KEY = "soba:jobs"
_POLL_S = 1  # BRPOP block length while waiting "forever" (close() must wake us)


class RedisJobQueue(JobQueue):
    def __init__(self, url: str | None = None, *, key: str = DEFAULT_KEY,
                 client=None) -> None:
        if client is None:
            import redis  # the `worker` extra

            client = redis.Redis.from_url(url or "redis://127.0.0.1:6379/0",
                                          decode_responses=True)
        self._r = client
        self.key = key
        self._closed = threading.Event()

    def put(self, job_id: str) -> None:
        if self._closed.is_set():
            return
        self._r.lpush(self.key, job_id)

    def get(self, timeout: float | None = None) -> str | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self._closed.is_set():
            if deadline is None:
                block = _POLL_S
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    item = self._r.rpop(self.key)  # timeout=0: one non-blocking look
                    return _decode(item)
                block = max(1, min(_POLL_S, int(remaining + 0.999)))
            got = self._r.brpop([self.key], timeout=block)
            if got is not None:
                return _decode(got[1])
        return None

    def close(self) -> None:
        self._closed.set()

    def __len__(self) -> int:
        return int(self._r.llen(self.key))

    def ping(self) -> bool:
        return bool(self._r.ping())


def _decode(item) -> str | None:
    if item is None:
        return None
    return item.decode() if isinstance(item, bytes) else str(item)


def queue_from_env(url: str | None = None) -> JobQueue | None:
    """RedisJobQueue for ``url`` / ``SOBA_QUEUE_URL``; None when neither is set
    (caller keeps its default). Only ``redis://`` / ``rediss://`` URLs."""
    import os

    url = url or os.environ.get("SOBA_QUEUE_URL")
    if not url:
        return None
    if not url.startswith(("redis://", "rediss://", "unix://")):
        raise ValueError(f"SOBA_QUEUE_URL={url!r}: only redis:// / rediss:// URLs are supported")
    log.info("job queue: redis %s", url.split("@")[-1])  # never log credentials
    return RedisJobQueue(url)
