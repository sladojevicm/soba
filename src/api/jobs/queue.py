"""The job queue interface and its in-process implementation.

The interface is deliberately three methods so a Redis implementation
(runpod-orchestration-agent) is a drop-in::

    put(job_id)      -> LPUSH  soba:jobs job_id
    get(timeout)     -> BRPOP  soba:jobs timeout   (None on timeout / after close)
    close()          -> set a flag so blocked get() callers return None

Semantics every implementation must keep:

* FIFO per producer; ids are opaque strings (see api.jobs.paths.JOB_ID_RE).
* Each id is delivered to exactly one ``get()`` caller. There is no ack /
  redelivery: a worker that dies mid-job leaves the job ``running`` in the
  store, which is the retention/cleanup policy's problem, not the queue's.
* ``get()`` never raises on timeout; it returns ``None``.
* ``put()`` after ``close()`` is a no-op.

The store (api.jobs.store) is the source of truth for job state; the queue
only says "someone should look at this id".
"""

from __future__ import annotations

import queue as _queue
import threading
import time
from abc import ABC, abstractmethod


class JobQueue(ABC):
    @abstractmethod
    def put(self, job_id: str) -> None:
        """Enqueue an id. Never blocks."""

    @abstractmethod
    def get(self, timeout: float | None = None) -> str | None:
        """Next id, blocking up to ``timeout`` s (None = forever).

        Returns None on timeout or once the queue is closed.
        """

    @abstractmethod
    def close(self) -> None:
        """Wake blocked consumers; later get() calls return None immediately."""


class InProcessQueue(JobQueue):
    """``queue.Queue`` under the JobQueue contract (single API process)."""

    _POLL_S = 0.2

    def __init__(self) -> None:
        self._q: _queue.Queue[str] = _queue.Queue()
        self._closed = threading.Event()

    def put(self, job_id: str) -> None:
        if not self._closed.is_set():
            self._q.put(job_id)

    def get(self, timeout: float | None = None) -> str | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self._closed.is_set():
            wait = self._POLL_S
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # one last non-blocking look so timeout=0 still drains
                    try:
                        return self._q.get_nowait()
                    except _queue.Empty:
                        return None
                wait = min(wait, remaining)
            try:
                return self._q.get(timeout=wait)
            except _queue.Empty:
                continue
        return None

    def close(self) -> None:
        self._closed.set()

    def __len__(self) -> int:
        return self._q.qsize()
