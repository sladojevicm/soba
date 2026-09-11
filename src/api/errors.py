"""One JSON error envelope for every API error: {"error": {"code", "message"}}.

Legacy scene routes keep their plain-text 400/404 bodies (byte-for-byte
compatibility with the pre-job server); everything under /api and every
job-scoped 404 uses this envelope.
"""

from __future__ import annotations

from starlette.responses import JSONResponse


def api_error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)
