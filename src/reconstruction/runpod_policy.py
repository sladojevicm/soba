"""Resilience and cost policy for the RunPod client (config/pipeline.yaml `runpod:`).

`RunPodEngine` (generative.py) composes four small objects from here:

* `RetryPolicy`    — attempts + exponential backoff with jitter (`sleep_for(attempt)`).
* `CircuitBreaker` — per endpoint id, process-wide (`breaker_for(endpoint)`):
                     closed -> open after N consecutive failures -> half-open after
                     the cooldown (one probe) -> closed on success / open on failure.
* `Budget`         — per job: calls, billable GPU seconds and estimated USD, each
                     capped; `exhausted()` names the first cap that was hit.
* `estimate_usd()` — the USD-per-GPU-second assumption, optionally per endpoint.

Every number comes from `load_runpod_config()`; nothing here has a default value
(CLAUDE.md invariant 2). A missing key is a `KeyError` naming the block, on purpose.
Env switches are documented in the yaml block: SOBA_RUNPOD_DISABLED (kill switch)
is read by `disabled()`.
"""

from __future__ import annotations

import os
import random
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ENV_DISABLED = "SOBA_RUNPOD_DISABLED"

# drop reasons recorded on the run (telemetry.drop) when no call is made / kept
DROP_DISABLED = "runpod_disabled"
DROP_BREAKER_OPEN = "runpod_breaker_open"
DROP_BUDGET = "runpod_budget_exhausted"
DROP_FAILED = "runpod_failed"  # recorded by scripts/run_assemble.py after a raise


def _now() -> float:  # monkeypatchable clock for the breaker tests
    return time.monotonic()


_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = _REPO_ROOT / "config" / "pipeline.yaml"


@lru_cache(maxsize=4)
def _load_yaml(path: str) -> dict:
    # Same file scene.lookup.load_config reads; read here directly because
    # importing `scene` pulls in open3d, which the API/worker box may lack.
    import yaml

    with open(path) as fh:
        return yaml.safe_load(fh)


def load_runpod_config(path: str | Path | None = None) -> dict:
    """The `runpod:` block of config/pipeline.yaml."""
    cfg = _load_yaml(str(path or DEFAULT_CONFIG))
    try:
        return cfg["runpod"]
    except (KeyError, TypeError) as exc:
        raise KeyError(f"{path or DEFAULT_CONFIG} has no `runpod:` block") from exc


def disabled() -> bool:
    """The global kill switch: SOBA_RUNPOD_DISABLED=1 (also true/yes/on)."""
    return os.environ.get(ENV_DISABLED, "").strip().lower() in ("1", "true", "yes", "on")


# --- retry ------------------------------------------------------------------
@dataclass
class RetryPolicy:
    max_attempts: int
    backoff_base_s: float
    backoff_max_s: float
    jitter_s: float

    @classmethod
    def from_config(cls, cfg: dict) -> RetryPolicy:
        r = cfg["retry"]
        return cls(int(r["max_attempts"]), float(r["backoff_base_s"]),
                   float(r["backoff_max_s"]), float(r["jitter_s"]))

    def sleep_for(self, attempt: int) -> float:
        """Seconds to wait AFTER the given failed attempt (1-based)."""
        base = min(self.backoff_base_s * (2 ** (attempt - 1)), self.backoff_max_s)
        return base + (random.uniform(0.0, self.jitter_s) if self.jitter_s > 0 else 0.0)


# --- circuit breaker --------------------------------------------------------
CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"


class CircuitBreaker:
    """Consecutive-failure breaker with a cooldown and a single half-open probe."""

    def __init__(self, consecutive_failures: int, cooldown_s: float, *, name: str = ""):
        self.threshold = int(consecutive_failures)
        self.cooldown_s = float(cooldown_s)
        self.name = name
        self.failures = 0
        self.opened_at: float | None = None
        self._probing = False
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, cfg: dict, *, name: str = "") -> CircuitBreaker:
        cb = cfg["circuit_breaker"]
        return cls(cb["consecutive_failures"], cb["cooldown_s"], name=name)

    @property
    def state(self) -> str:
        with self._lock:
            return self._state_locked()

    def _state_locked(self) -> str:
        if self.opened_at is None:
            return CLOSED
        if self._probing or _now() - self.opened_at >= self.cooldown_s:
            return HALF_OPEN
        return OPEN

    def allow(self) -> bool:
        """May a call go out now? Half-open admits exactly one probe."""
        with self._lock:
            st = self._state_locked()
            if st == CLOSED:
                return True
            if st == OPEN:
                return False
            if self._probing:
                return False  # a probe is already in flight
            self._probing = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.opened_at = None
            self._probing = False

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self._probing or self.failures >= self.threshold:
                self.opened_at = _now()
                self._probing = False

    def to_dict(self) -> dict:
        return {"state": self.state, "failures": self.failures,
                "threshold": self.threshold, "cooldown_s": self.cooldown_s}


_BREAKERS: dict[str, CircuitBreaker] = {}
_BREAKERS_LOCK = threading.Lock()


def breaker_for(endpoint: str, cfg: dict | None = None) -> CircuitBreaker:
    """The process-wide breaker for `endpoint` (created from config on first use).

    Process-wide on purpose: an endpoint that is down stays open across the jobs
    a worker runs during the cooldown, instead of every job re-discovering it.
    """
    with _BREAKERS_LOCK:
        b = _BREAKERS.get(endpoint)
        if b is None:
            b = CircuitBreaker.from_config(cfg or load_runpod_config(), name=endpoint)
            _BREAKERS[endpoint] = b
        return b


def reset_breakers() -> None:
    with _BREAKERS_LOCK:
        _BREAKERS.clear()


# --- price + budget ---------------------------------------------------------
def usd_per_gpu_second(cfg: dict, endpoint: str | None = None) -> float:
    price = cfg["price"]
    by_ep = price.get("by_endpoint") or {}
    if endpoint and endpoint in by_ep:
        return float(by_ep[endpoint])
    return float(price["usd_per_gpu_second"])


def estimate_usd(seconds: float, endpoint: str | None = None,
                 cfg: dict | None = None) -> float:
    return float(seconds) * usd_per_gpu_second(cfg or load_runpod_config(), endpoint)


@dataclass
class Budget:
    """Per-job caps on RunPod usage. `charge()` after every HTTP submission,
    success or failure (RunPod bills failed executions too)."""

    max_calls: int
    max_gpu_seconds: float
    max_est_usd: float
    calls: int = 0
    gpu_seconds: float = 0.0
    est_usd: float = 0.0
    by_endpoint: dict = field(default_factory=dict)

    @classmethod
    def from_config(cls, cfg: dict) -> Budget:
        b = cfg["budget"]
        return cls(int(b["max_calls"]), float(b["max_gpu_seconds"]),
                   float(b["max_est_usd"]))

    def charge(self, seconds: float, est_usd: float, endpoint: str = "") -> None:
        self.calls += 1
        self.gpu_seconds += float(seconds)
        self.est_usd += float(est_usd)
        ep = self.by_endpoint.setdefault(endpoint, {"calls": 0, "gpu_seconds": 0.0,
                                                    "est_usd": 0.0})
        ep["calls"] += 1
        ep["gpu_seconds"] += float(seconds)
        ep["est_usd"] += float(est_usd)

    def exhausted(self) -> str | None:
        """The first cap that is hit (`calls` / `gpu_seconds` / `est_usd`), else None."""
        if self.calls >= self.max_calls:
            return "calls"
        if self.gpu_seconds >= self.max_gpu_seconds:
            return "gpu_seconds"
        if self.est_usd >= self.max_est_usd:
            return "est_usd"
        return None

    def to_dict(self) -> dict:
        return {"max_calls": self.max_calls, "max_gpu_seconds": self.max_gpu_seconds,
                "max_est_usd": self.max_est_usd, "calls": self.calls,
                "gpu_seconds": round(self.gpu_seconds, 3),
                "est_usd": round(self.est_usd, 6), "exhausted": self.exhausted(),
                "by_endpoint": {k: dict(v) for k, v in self.by_endpoint.items()}}
