"""Job records and their state machine.

States: ``queued`` -> ``running`` (with a ``stage`` string, shown as
``running:<stage>``) -> ``done`` | ``failed``. Terminal states are frozen;
``queued -> failed`` is allowed so a job can be failed before a worker ever
picks it up. Every accepted transition is appended to ``history`` so a client
(or a test) can see the whole path without polling fast enough to catch it.

Two implementations share one interface: ``MemoryJobStore`` (tests, throwaway
servers) and ``SqliteJobStore`` (stdlib ``sqlite3``; the default). Both are
safe to call from the request thread and the worker thread at once.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path

from telemetry import log_event

log = logging.getLogger("api.jobs")

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
STATES = (QUEUED, RUNNING, DONE, FAILED)
TERMINAL = frozenset({DONE, FAILED})

_ALLOWED = {
    QUEUED: {RUNNING, FAILED},
    RUNNING: {RUNNING, DONE, FAILED},
    DONE: set(),
    FAILED: set(),
}


class InvalidTransition(ValueError):
    """Raised when a state change is not allowed by the job state machine."""


class UnknownJob(KeyError):
    """Raised for an id the store does not hold."""


@dataclass
class JobRecord:
    id: str
    tier: int
    source_format: str  # "bundle" | "tum"
    state: str = QUEUED
    stage: str | None = None
    error: str | None = None
    upload_name: str | None = None
    bundle_dir: str | None = None  # where PerceptionBundle.open() should look
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    history: list[dict] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.state == RUNNING and self.stage:
            return f"{RUNNING}:{self.stage}"
        return self.state

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status
        d["scene_url"] = f"/jobs/{self.id}/"
        return d

    @classmethod
    def from_dict(cls, d: dict) -> JobRecord:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _apply_transition(rec: JobRecord, state: str, stage: str | None,
                      error: str | None) -> JobRecord:
    if state not in STATES:
        raise InvalidTransition(f"unknown state {state!r}")
    if state not in _ALLOWED[rec.state]:
        raise InvalidTransition(f"{rec.status} -> {state}")
    if state == RUNNING and rec.state == RUNNING and stage == rec.stage:
        return rec  # no-op: same stage twice
    prev = rec.status
    rec.state = state
    rec.stage = stage if state == RUNNING else None
    rec.error = error if state == FAILED else None
    rec.updated_at = time.time()
    rec.history.append({"status": rec.status, "at": rec.updated_at})
    # one structured event per accepted transition (job_id correlates with
    # the request log; the worker thread and an external consumer both pass
    # through here)
    log_event(log, logging.WARNING if state == FAILED else logging.INFO, "job state",
              event="job_state", job_id=rec.id, state=rec.state, stage=rec.stage,
              status=rec.status, prev=prev, error=rec.error)
    return rec


class JobStore(ABC):
    """CRUD + guarded state transitions. All methods are thread-safe."""

    @abstractmethod
    def create(self, rec: JobRecord) -> JobRecord: ...

    @abstractmethod
    def get(self, job_id: str) -> JobRecord | None: ...

    @abstractmethod
    def list(self) -> list[JobRecord]:
        """Newest first."""

    @abstractmethod
    def set_state(self, job_id: str, state: str, *, stage: str | None = None,
                  error: str | None = None) -> JobRecord:
        """Apply a transition. Raises UnknownJob / InvalidTransition."""

    @abstractmethod
    def delete(self, job_id: str) -> bool:
        """True if a record was removed."""

    def close(self) -> None:
        """Optional hook; the sqlite store opens a connection per call."""


class MemoryJobStore(JobStore):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, JobRecord] = {}

    def create(self, rec: JobRecord) -> JobRecord:
        with self._lock:
            if rec.id in self._jobs:
                raise ValueError(f"duplicate job id {rec.id}")
            if not rec.history:
                rec.history.append({"status": rec.status, "at": rec.created_at})
            self._jobs[rec.id] = rec
            return _copy(rec)

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            rec = self._jobs.get(job_id)
            return _copy(rec) if rec else None

    def list(self) -> list[JobRecord]:
        with self._lock:
            return [_copy(r) for r in sorted(self._jobs.values(),
                                             key=lambda r: r.created_at, reverse=True)]

    def set_state(self, job_id, state, *, stage=None, error=None) -> JobRecord:
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                raise UnknownJob(job_id)
            return _copy(_apply_transition(rec, state, stage, error))

    def delete(self, job_id: str) -> bool:
        with self._lock:
            return self._jobs.pop(job_id, None) is not None


def _copy(rec: JobRecord) -> JobRecord:
    return JobRecord.from_dict(json.loads(json.dumps(asdict(rec))))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    tier          INTEGER NOT NULL,
    source_format TEXT NOT NULL,
    state         TEXT NOT NULL,
    stage         TEXT,
    error         TEXT,
    upload_name   TEXT,
    bundle_dir    TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    history       TEXT NOT NULL
);
"""
_COLS = ("id", "tier", "source_format", "state", "stage", "error", "upload_name",
         "bundle_dir", "created_at", "updated_at", "history")


class SqliteJobStore(JobStore):
    """One sqlite file; a fresh connection per call under a process lock.

    Fine for one API process plus one worker thread (the in-process default).
    Several processes sharing the file also work (WAL + busy timeout), which
    is how an external worker on the same host would consume jobs.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def _row_to_rec(row: sqlite3.Row) -> JobRecord:
        d = dict(row)
        d["history"] = json.loads(d["history"])
        return JobRecord.from_dict(d)

    @staticmethod
    def _rec_to_row(rec: JobRecord) -> tuple:
        d = asdict(rec)
        d["history"] = json.dumps(d["history"])
        return tuple(d[k] for k in _COLS)

    def create(self, rec: JobRecord) -> JobRecord:
        if not rec.history:
            rec.history.append({"status": rec.status, "at": rec.created_at})
        with self._lock, self._conn() as c:
            c.execute(f"INSERT INTO jobs ({','.join(_COLS)}) VALUES "
                      f"({','.join('?' * len(_COLS))})", self._rec_to_row(rec))
        return _copy(rec)

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row_to_rec(row) if row else None

    def list(self) -> list[JobRecord]:
        with self._lock, self._conn() as c:
            rows = c.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [self._row_to_rec(r) for r in rows]

    def set_state(self, job_id, state, *, stage=None, error=None) -> JobRecord:
        with self._lock, self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                c.execute("ROLLBACK")
                raise UnknownJob(job_id)
            rec = self._row_to_rec(row)
            try:
                _apply_transition(rec, state, stage, error)
            except InvalidTransition:
                c.execute("ROLLBACK")
                raise
            c.execute(
                "UPDATE jobs SET state=?, stage=?, error=?, updated_at=?, history=? "
                "WHERE id=?",
                (rec.state, rec.stage, rec.error, rec.updated_at,
                 json.dumps(rec.history), job_id))
            c.execute("COMMIT")
        return rec

    def delete(self, job_id: str) -> bool:
        with self._lock, self._conn() as c:
            cur = c.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        return cur.rowcount > 0
