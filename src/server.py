"""Step 10 — local scene server (Phase 10).

Serves the assembled scene (`out/scene_<name>/`) to the Phase-11 browser. The
plan (§15) calls for FastAPI; this is built on **Starlette** — FastAPI's own
core — because Starlette + uvicorn + sse-starlette were the installed toolchain
and FastAPI itself was not. The routes and SSE behaviour are identical.

Routes (plan §15 "LOCAL SERVER"):
  GET /                    -> frontend/index.html (the viewer)
  GET /scene.json          -> the scene contract (served as-is, partial-safe)
  GET /meshes/{id}.glb     -> remaps to  objects/{id}/mesh.glb
  GET /hulls/{id}_{i}.glb  -> remaps to  objects/{id}/hulls/{id}_{i}.glb
  GET /events              -> SSE stream of {"event":"object_added","id":...}
  /app.js, /vendor/...     -> static frontend assets (mounted catch-all)

REMAP NOTE (plan §15 "ON-DISK vs ROUTE"): both mesh and hull routes flatten the
per-object folder `objects/{id}/` into the URL space the browser requests. The
mesh keeps its plain on-disk name `mesh.glb` (the route supplies the id); the
hull files are id-prefixed on disk (`{id}_{i}.glb`) so the on-disk name already
matches the requested `/hulls/{id}_{i}.glb`.

SSE NOTE: the scene here is already fully assembled on disk (assembly happened
offline in Phase 9), so `/events` *replays* one `object_added` per object in the
current scene.json — driving the browser through the real progressive-load path
(re-GET scene.json per event, fix Z-C) — then holds the connection open with
heartbeats. It is a replay of a finished scene, not a live feed from a running
assembler.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

GLB_MEDIA_TYPE = "model/gltf-binary"

# id = slug(class)_oid, e.g. "dining_table_01" — Contract 3 regex is
# ^[a-z_]+_[0-9]{2}$, but we validate a little more loosely (allow digits in the
# slug defensively) while still blocking anything that could escape the scene
# dir (no dots, no slashes).
_ID_RE = re.compile(r"^[a-z0-9_]+$")
_HULL_RE = re.compile(r"^(?P<id>[a-z0-9_]+)_(?P<i>[0-9]+)$")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_SCENE_DIR = _REPO_ROOT / "out" / "scene_office_3"
_DEFAULT_FRONTEND_DIR = _REPO_ROOT / "frontend"

# Spacing between replayed object_added events, so the browser visibly streams
# objects in rather than popping them all at once. Overridable for tests.
_REPLAY_DELAY_S = float(os.environ.get("VID2SIM_SSE_DELAY", "0.4"))
_HEARTBEAT_S = 15.0


def create_app(
    scene_dir: Path | str | None = None,
    frontend_dir: Path | str | None = None,
) -> Starlette:
    """Build the Starlette app serving `scene_dir` and the `frontend_dir` viewer.

    Both default to the repo's `out/scene_office_3/` and `frontend/`. The scene
    dir can also be set via the `VID2SIM_SCENE_DIR` env var (the factory arg
    wins when given).
    """
    scene_dir = Path(
        scene_dir
        or os.environ.get("VID2SIM_SCENE_DIR")
        or _DEFAULT_SCENE_DIR
    ).resolve()
    frontend_dir = Path(frontend_dir or _DEFAULT_FRONTEND_DIR).resolve()

    def _read_scene() -> dict | None:
        p = scene_dir / "scene.json"
        if not p.is_file():
            return None
        with p.open() as fh:
            return json.load(fh)

    async def scene_json(request):
        scene = _read_scene()
        if scene is None:
            # Partial-safe: an empty scene is valid before assembly (plan §16
            # startup: "Fetch scene.json (may be empty — fine)").
            return JSONResponse({"version": "2.0", "objects": []})
        return JSONResponse(scene)

    async def mesh(request):
        oid = request.path_params["id"]
        if not _ID_RE.match(oid):
            return Response("bad object id", status_code=400)
        path = scene_dir / "objects" / oid / "mesh.glb"
        if not path.is_file():
            return Response("mesh not found", status_code=404)
        return FileResponse(path, media_type=GLB_MEDIA_TYPE)

    async def hull(request):
        # The URL stem is `{id}_{i}`; the id itself contains underscores, so the
        # hull index is the LAST `_N` segment (rsplit-equivalent via regex).
        stem = request.path_params["stem"]
        m = _HULL_RE.match(stem)
        if not m:
            return Response("bad hull name", status_code=400)
        oid, i = m.group("id"), m.group("i")
        path = scene_dir / "objects" / oid / "hulls" / f"{oid}_{i}.glb"
        if not path.is_file():
            return Response("hull not found", status_code=404)
        return FileResponse(path, media_type=GLB_MEDIA_TYPE)

    async def events(request):
        async def gen():
            scene = _read_scene() or {}
            for obj in scene.get("objects", []):
                if await request.is_disconnected():
                    return
                yield {"event": "object_added", "data": json.dumps({"id": obj["id"]})}
                await asyncio.sleep(_REPLAY_DELAY_S)
            # Hold the connection open with heartbeats until the client leaves.
            while not await request.is_disconnected():
                yield {"event": "heartbeat", "data": "{}"}
                await asyncio.sleep(_HEARTBEAT_S)

        return EventSourceResponse(gen())

    async def index(request):
        idx = frontend_dir / "index.html"
        if not idx.is_file():
            return Response(
                "frontend not built (frontend/index.html missing)",
                status_code=404,
            )
        return FileResponse(idx, media_type="text/html")

    routes = [
        Route("/", index),
        Route("/scene.json", scene_json),
        Route("/meshes/{id}.glb", mesh),
        Route("/hulls/{stem}.glb", hull),
        Route("/events", events),
    ]
    # Static frontend assets (app.js, vendor/*). Mounted last so the API routes
    # above take precedence. Only added when the dir exists so tests pointed at
    # a bare scene dir don't fail to construct.
    if frontend_dir.is_dir():
        routes.append(Mount("/", app=StaticFiles(directory=str(frontend_dir))))

    # No-store everything: the viewer is a live dev tool and meshes/app.js get
    # regenerated in place, so a browser MUST NOT serve a stale cached mesh (a
    # rebuilt object otherwise renders as its old geometry until a hard refresh).
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware

    async def _no_store(request, call_next):
        resp = await call_next(request)
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        return resp

    return Starlette(routes=routes,
                     middleware=[Middleware(BaseHTTPMiddleware, dispatch=_no_store)])


# Module-level app for `uvicorn server:app` (uses env/defaults).
app = create_app()


def main() -> None:
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(description="vid2sim Step-10 scene server")
    ap.add_argument("--scene", default=None, help="scene dir (default out/scene_office_3)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    if args.scene:
        os.environ["VID2SIM_SCENE_DIR"] = str(Path(args.scene).resolve())
    uvicorn.run(create_app(args.scene), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
