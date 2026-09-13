"""API description routes: the OpenAPI document and a Redoc page.

  GET /api/openapi.json   spec/openapi.yaml, loaded once, served as JSON
  GET /api/docs           static HTML that renders it with Redoc

``spec/openapi.yaml`` is hand-authored (Starlette has no generator);
``tests/api/test_openapi.py`` asserts it matches ``create_app().routes`` and
validates live responses against it. Registered in ``api.app.create_app()``
as one import plus ``docs_routes()``.

Both routes are exempt from bearer auth in keyed mode (``api.security.auth``
``PUBLIC_PATHS``): the document is in the public repository, so serving it
leaks nothing; ``SOBA_AUTH_LEGACY=1`` closes them along with everything else.

The Redoc bundle is loaded from cdn.jsdelivr.net **at view time**, so the
docs page needs network access in the browser (not on the server). No new
Python dependency: ``pyyaml`` is already a core requirement.
"""

from __future__ import annotations

import functools
from pathlib import Path

import yaml
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

OPENAPI_PATH = "/api/openapi.json"
DOCS_PATH = "/api/docs"
SPEC_FILE = Path(__file__).resolve().parents[3] / "spec" / "openapi.yaml"
REDOC_URL = "https://cdn.jsdelivr.net/npm/redoc@2/bundles/redoc.standalone.js"

_REDOC_HTML = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Soba API</title>
  <style>
    body {{ margin: 0; font-family: system-ui, sans-serif; }}
    #fallback {{ display: none; padding: 2rem; max-width: 40rem; line-height: 1.5; }}
  </style>
</head>
<body>
  <div id="fallback">
    <h1>Soba API</h1>
    <p>The interactive reference could not load: it renders with Redoc from
    <code>cdn.jsdelivr.net</code>, which needs network access in this browser.</p>
    <p>The OpenAPI document itself is served locally at
    <a href="openapi.json"><code>/api/openapi.json</code></a>; the source is
    <code>spec/openapi.yaml</code> and the prose overview is <code>docs/api.md</code>.</p>
  </div>
  <redoc spec-url="openapi.json"></redoc>
  <script src="{REDOC_URL}"
          onerror="document.getElementById('fallback').style.display='block'"></script>
</body>
</html>
"""


@functools.lru_cache(maxsize=1)
def load_spec() -> dict:
    """The parsed OpenAPI document (read once per process)."""
    with SPEC_FILE.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def docs_routes() -> list[Route]:
    async def openapi_json(request: Request):
        return JSONResponse(load_spec())

    async def docs_page(request: Request):
        return HTMLResponse(_REDOC_HTML)

    return [
        Route(OPENAPI_PATH, openapi_json, methods=["GET"]),
        Route(DOCS_PATH, docs_page, methods=["GET"]),
    ]
