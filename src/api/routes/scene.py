"""Scene-serving routes, parameterised by "which scene dir".

The handlers are the Phase-10 server's, unchanged in behaviour (see the
route table in server.py's history):

  GET {prefix}/                 -> frontend index.html (the viewer)
  GET {prefix}/scene.json       -> the scene contract (partial-safe: empty
                                   scene when none is on disk yet)
  GET {prefix}/eval.json        -> evaluate_scene.py report, or {"available": false}
  GET {prefix}/meshes/{id}.glb  -> objects/{id}/mesh.glb
  GET {prefix}/hulls/{stem}.glb -> objects/{id}/hulls/{id}_{i}.glb
  GET {prefix}/events           -> SSE replay of object_added, then heartbeats

`resolve(request)` returns the scene dir for this request, or a Response
(a 404 envelope for an unknown job) that is sent instead. The root app
passes a constant; the job routes look the id up in the store.

REMAP NOTE: both mesh and hull routes flatten `objects/{id}/` into the URL
space the browser requests; the hull files are id-prefixed on disk so the
on-disk name already matches `/hulls/{id}_{i}.glb`.

SSE NOTE: for a finished scene `/events` *replays* one object_added per
object then holds the connection with heartbeats. With a `watch` callable
(job routes) it additionally follows a running job: new objects written by
the worker are announced as they appear and a `job_status` event is sent on
every status change until the job is terminal.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

GLB_MEDIA_TYPE = "model/gltf-binary"

# id = slug(class)_oid, e.g. "dining_table_01" — Contract 3 regex is
# ^[a-z_]+_[0-9]{2}$, but we validate a little more loosely (allow digits in the
# slug defensively) while still blocking anything that could escape the scene
# dir (no dots, no slashes).
_ID_RE = re.compile(r"^[a-z0-9_]+$")
_HULL_RE = re.compile(r"^(?P<id>[a-z0-9_]+)_(?P<i>[0-9]+)$")

_HEARTBEAT_S = 15.0
_WATCH_POLL_S = 0.5

Resolver = Callable[[Request], "Path | Response"]
Watcher = Callable[[Request], "Any | None"]  # -> object with .status/.terminal


def served_file(scene_dir: Path, *parts: str) -> Path | None:
    """``scene_dir/parts`` if it is a regular file that really lives under
    ``scene_dir`` once symlinks are resolved, else None.

    The ``_ID_RE`` / ``_HULL_RE`` allow-lists already keep dots and slashes
    out of the URL; this is the second, filesystem-level check the audit
    asked for (G5), so a symlink dropped into a scene dir cannot serve a
    file from outside it.
    """
    base = scene_dir.resolve()
    path = scene_dir.joinpath(*parts)
    try:
        real = path.resolve(strict=True)
    except OSError:
        return None
    if not real.is_relative_to(base) or not real.is_file():
        return None
    return path


def replay_delay_s() -> float:
    """Spacing between replayed object_added events (SOBA_SSE_DELAY, s).

    Read per request so tests can zero it after import.
    """
    return float(os.environ.get("SOBA_SSE_DELAY", "0.4"))


def read_scene(scene_dir: Path) -> dict | None:
    p = served_file(scene_dir, "scene.json")
    if p is None:
        return None
    with p.open() as fh:
        return json.load(fh)


def scene_routes(resolve: Resolver, frontend_dir: Path, *, prefix: str = "",
                 watch: Watcher | None = None) -> list[Route]:
    async def index(request: Request):
        d = resolve(request)
        if isinstance(d, Response):
            return d
        idx = frontend_dir / "index.html"
        if not idx.is_file():
            return Response("frontend not built (frontend/index.html missing)",
                            status_code=404)
        return FileResponse(idx, media_type="text/html")

    async def scene_json(request: Request):
        d = resolve(request)
        if isinstance(d, Response):
            return d
        scene = read_scene(d)
        if scene is None:
            # Partial-safe: an empty scene is valid before assembly (plan §16
            # startup: "Fetch scene.json (may be empty — fine)").
            return JSONResponse({"version": "2.0", "objects": []})
        return JSONResponse(scene)

    async def mesh(request: Request):
        d = resolve(request)
        if isinstance(d, Response):
            return d
        oid = request.path_params["id"]
        if not _ID_RE.match(oid):
            return Response("bad object id", status_code=400)
        path = served_file(d, "objects", oid, "mesh.glb")
        if path is None:
            return Response("mesh not found", status_code=404)
        return FileResponse(path, media_type=GLB_MEDIA_TYPE)

    async def hull(request: Request):
        d = resolve(request)
        if isinstance(d, Response):
            return d
        # The URL stem is `{id}_{i}`; the id itself contains underscores, so the
        # hull index is the LAST `_N` segment (rsplit-equivalent via regex).
        stem = request.path_params["stem"]
        m = _HULL_RE.match(stem)
        if not m:
            return Response("bad hull name", status_code=400)
        oid, i = m.group("id"), m.group("i")
        path = served_file(d, "objects", oid, "hulls", f"{oid}_{i}.glb")
        if path is None:
            return Response("hull not found", status_code=404)
        return FileResponse(path, media_type=GLB_MEDIA_TYPE)

    async def eval_json(request: Request):
        d = resolve(request)
        if isinstance(d, Response):
            return d
        # Scenes without a report are the NORMAL case, so answer 200 with a
        # "not available" marker instead of a 404 — the browser logs every 404
        # as a console error, which would fail the headless check.
        p = served_file(d, "eval.json")
        if p is None:
            return JSONResponse({"available": False})
        return FileResponse(p, media_type="application/json")

    async def events(request: Request):
        d = resolve(request)
        if isinstance(d, Response):
            return d

        async def gen():
            announced: set[str] = set()
            delay = replay_delay_s()

            async def announce_new() -> bool:
                scene = read_scene(d) or {}
                for obj in scene.get("objects", []):
                    if obj["id"] in announced:
                        continue
                    if await request.is_disconnected():
                        return False
                    announced.add(obj["id"])
                    yield_queue.append({"event": "object_added",
                                        "data": json.dumps({"id": obj["id"]})})
                return True

            yield_queue: list[dict] = []
            if not await announce_new():
                return
            for ev in yield_queue:
                yield ev
                await asyncio.sleep(delay)
            yield_queue.clear()

            # Follow a running job: status changes + objects as they land.
            last_status = None
            while watch is not None:
                rec = watch(request)
                if rec is None:
                    break
                if rec.status != last_status:
                    last_status = rec.status
                    yield {"event": "job_status",
                           "data": json.dumps({"status": rec.status, "state": rec.state,
                                               "stage": rec.stage, "error": rec.error})}
                if not await announce_new():
                    return
                for ev in yield_queue:
                    yield ev
                    await asyncio.sleep(delay)
                yield_queue.clear()
                if rec.terminal:
                    break
                await asyncio.sleep(_WATCH_POLL_S)
                if await request.is_disconnected():
                    return

            # Hold the connection open with heartbeats until the client leaves.
            while not await request.is_disconnected():
                yield {"event": "heartbeat", "data": "{}"}
                await asyncio.sleep(_HEARTBEAT_S)

        return EventSourceResponse(gen())

    async def index_redirect(request: Request):
        # `/jobs/{id}` -> `/jobs/{id}/`: the static catch-all mount at `/`
        # would otherwise swallow the slash-less URL before Starlette's own
        # redirect_slashes gets a chance.
        d = resolve(request)
        if isinstance(d, Response):
            return d
        return RedirectResponse(request.url.path + "/", status_code=307)

    routes = [Route(prefix, index_redirect)] if prefix else []
    return routes + [
        Route(f"{prefix}/", index),
        Route(f"{prefix}/scene.json", scene_json),
        Route(f"{prefix}/eval.json", eval_json),
        Route(f"{prefix}/meshes/{{id}}.glb", mesh),
        Route(f"{prefix}/hulls/{{stem}}.glb", hull),
        Route(f"{prefix}/events", events),
    ]
