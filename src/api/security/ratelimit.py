"""Token-bucket rate limiting, per API key or per client IP (audit G4/G8 class).

Two limits:
  general   every request          SOBA_RATE_LIMIT_RPS (100) / SOBA_RATE_LIMIT_BURST (200)
  upload    POST /api/jobs only    SOBA_RATE_LIMIT_UPLOAD_RPS (1) / SOBA_RATE_LIMIT_UPLOAD_BURST (10)
A limit with rps or burst <= 0 is disabled. The defaults are generous on
purpose: a viewer page load fetches scene.json + one mesh + hulls per object
in a burst and must never trip in open mode.

Identity: the key name when the request carries a valid bearer key, else the
client IP. ``X-Forwarded-For`` is honoured only with ``SOBA_TRUST_PROXY=1``
(rightmost entry, i.e. the address the trusted proxy in front of us saw;
the leftmost is client-controlled). Keys flagged ``loadtest`` bypass both
limits. 429 answers carry the JSON envelope and ``Retry-After`` (seconds).

Buckets are in-process memory: with several API replicas each one limits
independently. A shared (Redis) bucket is the follow-up noted in
docs/security/secrets-audit.md.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ..errors import api_error
from .auth import AuthConfig, env_flag

ENV_TRUST_PROXY = "SOBA_TRUST_PROXY"
_PRUNE_ABOVE = 10_000        # buckets kept before idle ones are dropped
_IDLE_S = 600.0


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Limit:
    rps: float
    burst: int

    @property
    def enabled(self) -> bool:
        return self.rps > 0 and self.burst > 0


@dataclass(frozen=True)
class RateLimitConfig:
    general: Limit = Limit(100.0, 200)
    upload: Limit = Limit(1.0, 10)
    trust_proxy: bool = False

    @classmethod
    def from_env(cls) -> RateLimitConfig:
        d = cls()
        return cls(
            general=Limit(_env_float("SOBA_RATE_LIMIT_RPS", d.general.rps),
                          int(_env_float("SOBA_RATE_LIMIT_BURST", d.general.burst))),
            upload=Limit(_env_float("SOBA_RATE_LIMIT_UPLOAD_RPS", d.upload.rps),
                         int(_env_float("SOBA_RATE_LIMIT_UPLOAD_BURST", d.upload.burst))),
            trust_proxy=env_flag(ENV_TRUST_PROXY, False),
        )


class TokenBucket:
    __slots__ = ("burst", "rate", "tokens", "updated")

    def __init__(self, limit: Limit, now: float) -> None:
        self.rate = limit.rps
        self.burst = float(limit.burst)
        self.tokens = self.burst
        self.updated = now

    def take(self, now: float) -> float:
        """0.0 when a token was taken, else seconds until one is available."""
        self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return 0.0
        return (1.0 - self.tokens) / self.rate


class RateLimiter:
    """Buckets keyed by (limit name, identity); thread-safe; prunes idle buckets."""

    def __init__(self, config: RateLimitConfig | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config or RateLimitConfig()
        self._clock = clock
        self._buckets: dict[tuple[str, str], TokenBucket] = {}
        self._lock = threading.Lock()

    def check(self, name: str, identity: str, limit: Limit) -> float:
        """Seconds to wait (0.0 = allowed) for one request under ``limit``."""
        if not limit.enabled:
            return 0.0
        now = self._clock()
        with self._lock:
            b = self._buckets.get((name, identity))
            if b is None:
                if len(self._buckets) >= _PRUNE_ABOVE:
                    self._prune(now)
                b = self._buckets[(name, identity)] = TokenBucket(limit, now)
            return b.take(now)

    def _prune(self, now: float) -> None:
        stale = [k for k, b in self._buckets.items() if now - b.updated > _IDLE_S]
        for k in stale:
            del self._buckets[k]


def client_ip(scope: Mapping, trust_proxy: bool) -> str:
    if trust_proxy:
        for name, value in scope.get("headers") or ():
            if name == b"x-forwarded-for":
                hops = [h.strip() for h in value.decode("latin-1").split(",") if h.strip()]
                if hops:
                    return hops[-1]
    client = scope.get("client")
    return client[0] if client else "unknown"


def retry_after_header(seconds: float) -> str:
    return str(max(1, math.ceil(seconds)))


class RateLimitMiddleware:
    def __init__(self, app, limiter: RateLimiter, auth: AuthConfig) -> None:
        self.app = app
        self.limiter = limiter
        self.auth = auth

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        key = self.auth.identify(scope)
        if key is not None and key.loadtest:
            await self.app(scope, receive, send)
            return
        cfg = self.limiter.config
        identity = f"key:{key.name}" if key else f"ip:{client_ip(scope, cfg.trust_proxy)}"
        wait = self.limiter.check("general", identity, cfg.general)
        if wait == 0.0 and scope.get("method") == "POST" and scope.get("path") == "/api/jobs":
            wait = self.limiter.check("upload", identity, cfg.upload)
        if wait > 0.0:
            resp = api_error(429, "rate_limited",
                             f"rate limit exceeded; retry in {retry_after_header(wait)} s")
            resp.headers["Retry-After"] = retry_after_header(wait)
            await resp(scope, receive, send)
            return
        await self.app(scope, receive, send)
