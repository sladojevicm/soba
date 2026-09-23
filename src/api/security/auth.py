"""Bearer API keys for the job API (secrets-audit gap G1).

Policy
  ``SOBA_API_KEYS`` unset or empty  -> OPEN mode. Every route is reachable and
      ``security_middleware()`` logs ONE warning when the app is built. This
      is the local-dev / ``scripts/verify_browser.js`` path and it must keep
      working with nothing configured.
  ``SOBA_API_KEYS`` set             -> ``/api/*`` and ``/jobs/*`` require
      ``Authorization: Bearer <key>``. The legacy root routes (``/``,
      ``/scene.json``, ``/meshes/..``, ``/hulls/..``, ``/events``,
      ``/eval.json`` and the static assets) stay open unless
      ``SOBA_AUTH_LEGACY=1`` closes them too. ``/api/openapi.json`` and
      ``/api/docs`` (``PUBLIC_PATHS``) stay open the same way.

Key format: comma-separated ``name:key`` or ``name:key:loadtest``. ``name``
is the caller's identity (``request.state.api_key_name``, the rate-limit
bucket, log lines); the ``loadtest`` flag exempts the key from rate limiting
(for ``load-test-agent``). Comparison is ``hmac.compare_digest`` against
every configured key, so neither the name nor the key length leaks through
timing. A failed check answers 401 in the JSON envelope with
``WWW-Authenticate: Bearer``.

Known limit: a browser cannot attach a bearer header to a page load, so with
keys set the viewer under ``/jobs/{id}/`` needs a proxy that injects the
header (or a future cookie/session scheme, follow-up in the audit doc).
"""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

from starlette.responses import Response

from ..errors import api_error

log = logging.getLogger("soba.api.security")

ENV_KEYS = "SOBA_API_KEYS"
ENV_LEGACY = "SOBA_AUTH_LEGACY"
_PROTECTED_PREFIXES = ("/api", "/jobs")
# Under /api but open in keyed mode: the OpenAPI document and the Redoc page
# contain nothing that is not in the public repository. SOBA_AUTH_LEGACY=1
# (close everything) still covers them.
PUBLIC_PATHS = frozenset({"/api/openapi.json", "/api/docs"})
_REALM = 'Bearer realm="soba"'


def env_flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class ApiKey:
    name: str
    secret: str
    loadtest: bool = False

    def __repr__(self) -> str:  # never print the secret
        return f"ApiKey(name={self.name!r}, loadtest={self.loadtest})"


def parse_api_keys(raw: str | None) -> tuple[ApiKey, ...]:
    """``name:key[:loadtest]`` entries, comma-separated; whitespace ignored.

    Raises ValueError with a message that names the entry position, never
    the key material.
    """
    keys: list[ApiKey] = []
    seen: set[str] = set()
    for i, item in enumerate((raw or "").split(",")):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":")
        if len(parts) < 2 or not parts[0].strip() or not parts[1]:
            raise ValueError(
                f"{ENV_KEYS} entry #{i + 1} must be name:key or name:key:loadtest")
        name, secret, *flags = parts
        name = name.strip()
        flagset = {f.strip().lower() for f in flags if f.strip()}
        unknown = flagset - {"loadtest"}
        if unknown:
            raise ValueError(
                f"{ENV_KEYS} entry #{i + 1} ({name}): unknown flag(s) {sorted(unknown)}")
        if name in seen:
            raise ValueError(f"{ENV_KEYS}: duplicate key name {name!r}")
        seen.add(name)
        keys.append(ApiKey(name=name, secret=secret, loadtest="loadtest" in flagset))
    return tuple(keys)


@dataclass(frozen=True)
class AuthConfig:
    keys: tuple[ApiKey, ...] = ()
    protect_legacy: bool = False

    @property
    def enabled(self) -> bool:
        return bool(self.keys)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AuthConfig:
        env = os.environ if environ is None else environ
        keys = parse_api_keys(env.get(ENV_KEYS))
        legacy = (env.get(ENV_LEGACY) or "").strip().lower() not in ("", "0", "false", "no", "off")
        return cls(keys=keys, protect_legacy=legacy)

    def lookup(self, token: str) -> ApiKey | None:
        """Constant-time over the whole key list (no early return)."""
        found: ApiKey | None = None
        tok = token.encode("utf-8", "surrogateescape")
        for key in self.keys:
            if hmac.compare_digest(key.secret.encode("utf-8"), tok):
                found = key
        return found

    def identify(self, scope: Mapping) -> ApiKey | None:
        """The ApiKey named by the request's bearer token, or None."""
        token = bearer_token(scope)
        if token is None or not self.keys:
            return None
        return self.lookup(token)

    def requires_auth(self, path: str) -> bool:
        if not self.enabled:
            return False
        if path in PUBLIC_PATHS:
            return self.protect_legacy
        for prefix in _PROTECTED_PREFIXES:
            if path == prefix or path.startswith(prefix + "/"):
                return True
        return self.protect_legacy


def bearer_token(scope: Mapping) -> str | None:
    """The token from ``Authorization: Bearer <token>`` (scheme case-insensitive)."""
    for name, value in scope.get("headers") or ():
        if name == b"authorization":
            raw = value.decode("latin-1").strip()
            scheme, _, token = raw.partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
            return None
    return None


def unauthorized(*, token_present: bool) -> Response:
    resp = api_error(401, "unauthorized",
                     "invalid API key" if token_present
                     else "missing Authorization: Bearer <key> header")
    resp.headers["WWW-Authenticate"] = (
        _REALM + ', error="invalid_token"' if token_present else _REALM)
    return resp


class AuthMiddleware:
    """Pure ASGI: sets ``request.state.api_key_name`` and enforces the policy."""

    def __init__(self, app, config: AuthConfig) -> None:
        self.app = app
        self.config = config

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        key = self.config.identify(scope)
        state = scope.setdefault("state", {})
        state["api_key_name"] = key.name if key else None
        state["api_key_loadtest"] = bool(key and key.loadtest)
        if key is None and self.config.requires_auth(scope.get("path", "")):
            resp = unauthorized(token_present=bearer_token(scope) is not None)
            await resp(scope, receive, send)
            return
        await self.app(scope, receive, send)


def log_auth_mode(config: AuthConfig) -> None:
    if config.enabled:
        names = ", ".join(k.name + ("(loadtest)" if k.loadtest else "") for k in config.keys)
        log.info("API auth enabled for /api/* and /jobs/* (%d key(s): %s); legacy root routes %s",
                 len(config.keys), names, "closed" if config.protect_legacy else "open")
    else:
        log.warning("%s is not set: the API is OPEN to every caller (fine for local dev; "
                    "set name:key[,name:key:loadtest] before exposing this server)", ENV_KEYS)
