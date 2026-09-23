"""API hardening (security phase B): auth, rate limiting, headers, upload checks.

``create_app`` adds ``*security_middleware()`` to its middleware list; the
upload route wraps its body in ``capped_receive``; ``api.jobs.archive``
runs the ``upload_validation`` checks. Everything is configured from the
environment, read when the app is built:

  SOBA_API_KEYS                 name:key[,name:key:loadtest]   unset = open mode
  SOBA_AUTH_LEGACY              1 also closes the legacy root routes
  SOBA_RATE_LIMIT_RPS / _BURST  general token bucket (100 / 200)
  SOBA_RATE_LIMIT_UPLOAD_RPS / _BURST   POST /api/jobs bucket (1 / 10)
  SOBA_TRUST_PROXY              1 = honour X-Forwarded-For (rightmost hop)
  SOBA_CORS_ORIGINS             comma-separated origins; empty = no CORS headers
  SOBA_SSE_MAX_PER_IP           concurrent /events streams per IP (8, 0 = off)
  SOBA_MAX_UPLOAD_MB            request body cap (app.py, 2048)
  SOBA_MAX_EXTRACTED_MB, SOBA_MAX_MEMBER_MB, SOBA_MAX_MEMBERS,
  SOBA_MAX_DECOMPRESSION_RATIO, SOBA_MAX_FRAMES, SOBA_MAX_FRAME_PIXELS,
  SOBA_DEPTH_SAMPLE             archive / bundle limits (upload_validation.py)
"""

from __future__ import annotations

from starlette.middleware import Middleware

from .auth import AuthConfig, AuthMiddleware, log_auth_mode
from .headers import (
    SecurityHeadersMiddleware,
    SSEConnectionCapMiddleware,
    cors_middleware,
    sse_max_per_ip_from_env,
)
from .ratelimit import RateLimitConfig, RateLimiter, RateLimitMiddleware

__all__ = ["security_middleware"]


def security_middleware() -> list[Middleware]:
    """The middleware stack, outermost first: CORS, headers, rate limit, auth, SSE cap.

    CORS is outermost so 401/429 answers carry the CORS headers too; the
    rate limiter runs BEFORE auth so failed key guesses burn the caller's
    per-IP budget instead of being free.
    """
    auth = AuthConfig.from_env()
    rl = RateLimitConfig.from_env()
    log_auth_mode(auth)
    stack: list[Middleware] = []
    cors = cors_middleware()
    if cors is not None:
        stack.append(cors)
    stack += [
        Middleware(SecurityHeadersMiddleware),
        Middleware(RateLimitMiddleware, limiter=RateLimiter(rl), auth=auth),
        Middleware(AuthMiddleware, config=auth),
        Middleware(SSEConnectionCapMiddleware, max_per_ip=sse_max_per_ip_from_env(),
                   trust_proxy=rl.trust_proxy),
    ]
    return stack
