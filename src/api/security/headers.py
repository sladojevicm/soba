"""Response headers, CORS allow-list and the SSE connection cap (audit G7, G8).

  SecurityHeadersMiddleware   X-Content-Type-Options: nosniff,
                              Content-Security-Policy: frame-ancestors 'none',
                              X-Frame-Options: DENY, Referrer-Policy: same-origin
                              on every response (existing values are kept).
                              HSTS is left to the TLS-terminating proxy.
  cors_middleware()           Starlette CORSMiddleware from SOBA_CORS_ORIGINS
                              (comma-separated origins, or "*"); unset/empty
                              -> None, i.e. NO CORS headers at all.
  SSEConnectionCapMiddleware  at most SOBA_SSE_MAX_PER_IP (8) open /events
                              streams per client IP (root and /jobs/{id}/events);
                              0 disables; the (n+1)-th gets 429 too_many_streams.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Iterable

from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from ..errors import api_error
from .ratelimit import client_ip

ENV_CORS = "SOBA_CORS_ORIGINS"
ENV_SSE_MAX = "SOBA_SSE_MAX_PER_IP"
DEFAULT_SSE_MAX_PER_IP = 8
_SSE_PATH_RE = re.compile(r"^(?:/jobs/[^/]+)?/events$")

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
)


class SecurityHeadersMiddleware:
    def __init__(self, app, headers: Iterable[tuple[bytes, bytes]] = SECURITY_HEADERS) -> None:
        self.app = app
        self.headers = tuple(headers)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def _send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {k.lower() for k, _ in headers}
                headers.extend((k, v) for k, v in self.headers if k not in present)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, _send)


def cors_origins_from_env() -> list[str]:
    raw = os.environ.get(ENV_CORS) or ""
    return [o.strip() for o in raw.split(",") if o.strip()]


def cors_middleware(origins: list[str] | None = None) -> Middleware | None:
    origins = cors_origins_from_env() if origins is None else origins
    if not origins:
        return None
    return Middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["Retry-After", "WWW-Authenticate"],
        max_age=600,
    )


def sse_max_per_ip_from_env() -> int:
    raw = os.environ.get(ENV_SSE_MAX)
    if raw is None or raw.strip() == "":
        return DEFAULT_SSE_MAX_PER_IP
    try:
        return max(0, int(float(raw)))
    except ValueError:
        return DEFAULT_SSE_MAX_PER_IP


class SSEConnectionCapMiddleware:
    def __init__(self, app, max_per_ip: int = DEFAULT_SSE_MAX_PER_IP,
                 trust_proxy: bool = False) -> None:
        self.app = app
        self.max_per_ip = max_per_ip
        self.trust_proxy = trust_proxy
        self._open: dict[str, int] = {}
        self._lock = threading.Lock()

    def open_streams(self, ip: str) -> int:
        with self._lock:
            return self._open.get(ip, 0)

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or self.max_per_ip <= 0
                or scope.get("method") != "GET"
                or not _SSE_PATH_RE.match(scope.get("path", ""))):
            await self.app(scope, receive, send)
            return
        ip = client_ip(scope, self.trust_proxy)
        with self._lock:
            n = self._open.get(ip, 0)
            if n >= self.max_per_ip:
                allowed = False
            else:
                allowed = True
                self._open[ip] = n + 1
        if not allowed:
            resp = api_error(429, "too_many_streams",
                             f"at most {self.max_per_ip} concurrent /events streams per client")
            resp.headers["Retry-After"] = "5"
            await resp(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            with self._lock:
                left = self._open.get(ip, 1) - 1
                if left <= 0:
                    self._open.pop(ip, None)
                else:
                    self._open[ip] = left
