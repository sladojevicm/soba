"""Tests for the Step-10 local server (Phase 10).

Uses Starlette's TestClient (httpx under the hood). The SSE route is driven
synchronously by TestClient.stream and parsed by hand — enough to assert the
object_added replay without a full async event loop.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from server import create_app


@pytest.fixture
def scene_dir():
    """The real assembled office_3 scene shipped in the repo."""
    from pathlib import Path

    d = Path(__file__).resolve().parents[1] / "out" / "scene_office_3"
    if not (d / "scene.json").is_file():
        pytest.skip("out/scene_office_3 not assembled")
    return d


@pytest.fixture
def client(scene_dir):
    # Zero replay delay so the SSE test doesn't sleep.
    import os

    os.environ["VID2SIM_SSE_DELAY"] = "0"
    return TestClient(create_app(scene_dir=scene_dir))


def test_scene_json_served(client):
    r = client.get("/scene.json")
    assert r.status_code == 200
    scene = r.json()
    assert scene["version"] == "2.0"
    assert scene["world"]["gravity"] == [0.0, -9.81, 0.0]
    assert "y" in scene["ground"]
    assert len(scene["objects"]) >= 1


def test_mesh_route_remaps_to_object_folder(client, scene_dir):
    scene = json.loads((scene_dir / "scene.json").read_text())
    oid = scene["objects"][0]["id"]
    r = client.get(f"/meshes/{oid}.glb")
    assert r.status_code == 200
    assert r.headers["content-type"] == "model/gltf-binary"
    # It really is the on-disk mesh.glb, byte-for-byte.
    on_disk = (scene_dir / "objects" / oid / "mesh.glb").read_bytes()
    assert r.content == on_disk
    assert r.content[:4] == b"glTF"  # GLB magic


def test_hull_route_remaps_and_splits_index(client, scene_dir):
    scene = json.loads((scene_dir / "scene.json").read_text())
    obj = scene["objects"][0]
    hull_url = obj["collider"]["hull_paths"][0]  # e.g. "hulls/couch_00_0.glb"
    name = hull_url.split("/")[-1]  # couch_00_0.glb
    r = client.get(f"/hulls/{name}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "model/gltf-binary"
    on_disk = (scene_dir / "objects" / obj["id"] / "hulls" / name).read_bytes()
    assert r.content == on_disk


def test_mesh_404_for_unknown_id(client):
    r = client.get("/meshes/nope_99.glb")
    assert r.status_code == 404


def test_hull_400_for_malformed_name(client):
    # No trailing _<index> -> not a valid hull stem.
    r = client.get("/hulls/couchglb.glb")
    assert r.status_code in (400, 404)


def test_path_traversal_blocked(client):
    # A dotted/sloved id must never resolve outside the scene dir.
    r = client.get("/meshes/..%2f..%2fetc.glb")
    assert r.status_code in (400, 404)


def test_events_replays_object_added_per_object(scene_dir):
    # SSE only streams faithfully against a real server (TestClient and httpx's
    # ASGI transport both buffer the infinite response). Run uvicorn in a thread
    # on an ephemeral port, collect object_added events, then shut it down.
    import os
    import socket
    import threading
    import time

    import httpx
    import uvicorn

    os.environ["VID2SIM_SSE_DELAY"] = "0"
    scene = json.loads((scene_dir / "scene.json").read_text())
    want_ids = [o["id"] for o in scene["objects"]]

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(
        create_app(scene_dir=scene_dir), host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.02)
        assert server.started, "uvicorn did not start"

        got_ids = []
        with httpx.Client(timeout=15) as c:
            with c.stream("GET", f"http://127.0.0.1:{port}/events") as r:
                assert r.status_code == 200
                assert "text/event-stream" in r.headers["content-type"]
                for line in r.iter_lines():
                    if line.startswith("data:"):
                        payload = line[len("data:"):].strip()
                        if payload and payload != "{}":
                            got_ids.append(json.loads(payload)["id"])
                    if len(got_ids) >= len(want_ids):
                        break
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    assert got_ids == want_ids


def test_empty_scene_dir_is_partial_safe(tmp_path):
    # No scene.json on disk -> server returns an empty-but-valid scene.
    app = create_app(scene_dir=tmp_path, frontend_dir=tmp_path)
    c = TestClient(app)
    r = c.get("/scene.json")
    assert r.status_code == 200
    assert r.json()["objects"] == []
