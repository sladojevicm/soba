"""Security phase B: response headers, CORS allow-list, the per-IP SSE cap and
the filesystem-level path guard on the scene routes."""

from __future__ import annotations

import asyncio

import pytest
from _fixtures import OBJ_ID, post_archive
from starlette.testclient import TestClient

from api.app import create_app
from api.security import security_middleware
from api.security.headers import SSEConnectionCapMiddleware, cors_middleware


@pytest.fixture
def make_app(monkeypatch, root_scene, frontend_dir, jobs_dir, fixture_scene):
    def _make(env: dict[str, str] | None = None, **kw):
        for k in ("SOBA_API_KEYS", "SOBA_AUTH_LEGACY", "SOBA_CORS_ORIGINS", "SOBA_SSE_MAX_PER_IP"):
            monkeypatch.delenv(k, raising=False)
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
        return create_app(root_scene, frontend_dir, jobs_dir=jobs_dir, worker_mode="mock",
                          fixture_scene=fixture_scene, start_worker=False, **kw)

    return _make


# --- security headers -----------------------------------------------------------------
def test_security_headers_on_every_response(make_app, bundle_zip):
    c = TestClient(make_app())
    job = post_archive(c, bundle_zip).json()
    for path, status in (("/scene.json", 200), ("/", 200), ("/assets/app.js", 200),
                         ("/api/jobs", 200), ("/api/jobs/nope", 404),
                         (f"/jobs/{job['id']}/scene.json", 200), ("/hulls/bad.glb", 400)):
        r = c.get(path)
        assert r.status_code == status, path
        assert r.headers["x-content-type-options"] == "nosniff", path
        assert r.headers["content-security-policy"] == "frame-ancestors 'none'", path
        assert r.headers["x-frame-options"] == "DENY", path
        assert r.headers["referrer-policy"] == "same-origin", path
        assert "no-store" in r.headers["cache-control"], path  # legacy middleware kept


# --- CORS ------------------------------------------------------------------------------
def test_no_cors_headers_unless_configured(make_app):
    c = TestClient(make_app())
    r = c.get("/scene.json", headers={"Origin": "https://app.example"})
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers
    r = c.options("/api/jobs", headers={"Origin": "https://app.example",
                                        "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers
    assert cors_middleware([]) is None


def test_cors_allow_list(make_app):
    c = TestClient(make_app({"SOBA_CORS_ORIGINS": "https://app.example, http://localhost:5173"}))
    r = c.get("/scene.json", headers={"Origin": "https://app.example"})
    assert r.headers["access-control-allow-origin"] == "https://app.example"
    assert "retry-after" in r.headers["access-control-expose-headers"].lower()
    r = c.get("/scene.json", headers={"Origin": "http://localhost:5173"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    r = c.get("/scene.json", headers={"Origin": "https://evil.example"})
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers
    r = c.options("/api/jobs", headers={"Origin": "https://app.example",
                                        "Access-Control-Request-Method": "POST",
                                        "Access-Control-Request-Headers": "authorization"})
    assert r.status_code == 200
    assert "POST" in r.headers["access-control-allow-methods"]
    assert "authorization" in r.headers["access-control-allow-headers"].lower()


def test_cors_headers_ride_on_401_and_429(make_app):
    c = TestClient(make_app({"SOBA_CORS_ORIGINS": "https://app.example",
                             "SOBA_API_KEYS": "a:s",
                             "SOBA_RATE_LIMIT_RPS": "0.01", "SOBA_RATE_LIMIT_BURST": "1"}))
    origin = {"Origin": "https://app.example"}
    r = c.get("/api/jobs", headers=origin)
    assert r.status_code == 401 and r.headers["access-control-allow-origin"] == "https://app.example"
    r = c.get("/api/jobs", headers=origin)
    assert r.status_code == 429 and r.headers["access-control-allow-origin"] == "https://app.example"


def test_middleware_stack_order():
    names = [m.cls.__name__ for m in security_middleware()]
    assert names == ["SecurityHeadersMiddleware", "RateLimitMiddleware", "AuthMiddleware",
                     "SSEConnectionCapMiddleware"]


def test_cors_is_outermost_when_configured(monkeypatch):
    monkeypatch.setenv("SOBA_CORS_ORIGINS", "*")
    assert security_middleware()[0].cls.__name__ == "CORSMiddleware"


# --- SSE cap ---------------------------------------------------------------------------
def _run(coro):
    return asyncio.run(coro)


async def _drive(mw, path, gate: asyncio.Event, ip="9.9.9.9", headers=()):
    """One request through the middleware; the inner app blocks on `gate`
    like a live SSE stream, then completes."""
    statuses = []

    async def send(m):
        if m["type"] == "http.response.start":
            statuses.append(m["status"])

    async def receive():
        return {"type": "http.request", "body": b""}

    await mw({"type": "http", "method": "GET", "path": path, "client": (ip, 1),
              "headers": list(headers)}, receive, send)
    return statuses[0]


def _mw(max_per_ip=2, trust_proxy=False):
    gate = asyncio.Event()

    async def inner(scope, receive, send):
        await gate.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    return SSEConnectionCapMiddleware(inner, max_per_ip=max_per_ip, trust_proxy=trust_proxy), gate


def test_sse_streams_per_ip_are_capped():
    async def full():
        mw, gate = _mw(max_per_ip=2)
        t1 = asyncio.create_task(_drive(mw, "/events", gate))
        t2 = asyncio.create_task(_drive(mw, "/jobs/0123456789abcdef/events", gate))
        await asyncio.sleep(0.01)
        assert mw.open_streams("9.9.9.9") == 2
        assert await _drive(mw, "/events", gate) == 429
        # other paths and other IPs are unaffected (they block on the gate, so
        # check the counter instead of awaiting them)
        t3 = asyncio.create_task(_drive(mw, "/events", gate, ip="8.8.8.8"))
        t4 = asyncio.create_task(_drive(mw, "/scene.json", gate))
        await asyncio.sleep(0.01)
        assert mw.open_streams("8.8.8.8") == 1 and mw.open_streams("9.9.9.9") == 2
        gate.set()
        assert await asyncio.gather(t1, t2, t3, t4) == [200, 200, 200, 200]
        assert mw.open_streams("9.9.9.9") == 0 and mw.open_streams("8.8.8.8") == 0
        gate.clear()
        t5 = asyncio.create_task(_drive(mw, "/events", gate))  # slots are free again
        await asyncio.sleep(0.01)
        assert mw.open_streams("9.9.9.9") == 1
        gate.set()
        assert await t5 == 200

    _run(full())


def test_sse_cap_counts_forwarded_ip_only_behind_a_proxy():
    async def run():
        xff = [(b"x-forwarded-for", b"1.1.1.1")]
        mw, gate = _mw(max_per_ip=1, trust_proxy=True)
        t = asyncio.create_task(_drive(mw, "/events", gate, ip="9.9.9.9", headers=xff))
        await asyncio.sleep(0.01)
        assert mw.open_streams("1.1.1.1") == 1 and mw.open_streams("9.9.9.9") == 0
        gate.set()
        await t
        mw, gate = _mw(max_per_ip=0)  # 0 disables the cap
        ts = [asyncio.create_task(_drive(mw, "/events", gate)) for _ in range(5)]
        await asyncio.sleep(0.01)
        assert mw.open_streams("9.9.9.9") == 0
        gate.set()
        assert await asyncio.gather(*ts) == [200] * 5

    _run(run())


def test_sse_cap_through_a_real_server(make_app):
    """TestClient buffers whole responses, so a live /events stream needs uvicorn
    (cf. test_job_events_replay_and_report_status)."""
    import socket
    import threading
    import time

    import httpx
    import uvicorn

    app = make_app({"SOBA_SSE_MAX_PER_IP": "1"})
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.02)
        assert server.started
        base = f"http://127.0.0.1:{port}"
        with httpx.Client(timeout=10) as c, c.stream("GET", f"{base}/events") as first:
            assert first.status_code == 200
            r = c.get(f"{base}/events")
            assert r.status_code == 429
            assert r.json()["error"]["code"] == "too_many_streams"
            assert r.headers["retry-after"] == "5"
            assert c.get(f"{base}/scene.json").status_code == 200  # only /events is counted
        # the slot is released once the first stream is closed
        deadline = time.time() + 5
        with httpx.Client(timeout=10) as c:
            while time.time() < deadline:
                with c.stream("GET", f"{base}/events") as again:
                    if again.status_code == 200:
                        break
                time.sleep(0.05)
            assert again.status_code == 200
    finally:
        server.should_exit = True
        thread.join(timeout=10)


# --- scene-route path guard -----------------------------------------------------------
def test_symlinked_files_outside_the_scene_dir_are_not_served(make_app, root_scene, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "mesh.glb").write_bytes(b"glTF-leak")
    (outside / "eval.json").write_text('{"leak": true}')
    (root_scene / "objects" / "leak_01").mkdir()
    (root_scene / "objects" / "leak_01" / "mesh.glb").symlink_to(outside / "mesh.glb")
    (root_scene / "objects" / "leak_01" / "hulls").symlink_to(outside, target_is_directory=True)
    (outside / "leak_01_0.glb").write_bytes(b"glTF-leak")
    (root_scene / "eval.json").unlink()
    (root_scene / "eval.json").symlink_to(outside / "eval.json")
    c = TestClient(make_app())
    assert c.get("/meshes/leak_01.glb").status_code == 404
    assert c.get("/hulls/leak_01_0.glb").status_code == 404
    assert c.get("/eval.json").json() == {"available": False}
    assert c.get("/meshes/root_table_01.glb").status_code == 200  # real files still served


@pytest.mark.parametrize("path", [
    "/meshes/..%2F..%2Fetc%2Fpasswd.glb", "/meshes/../scene.glb", "/hulls/..%2Fx_0.glb",
    "/meshes/%2E%2E%2F%2E%2E%2Fscene.json.glb", "/hulls/a%2Fb_0.glb", "/meshes/a.b.glb",
])
def test_traversal_shaped_urls_never_reach_the_filesystem(make_app, path):
    c = TestClient(make_app())
    r = c.get(path)
    assert r.status_code in (400, 404), path
    assert b"glTF" not in r.content and b"version" not in r.content  # no mesh, no scene.json


def test_job_scene_dir_guard_applies_too(make_app, bundle_zip, jobs_dir, tmp_path):
    app = make_app()
    c = TestClient(app)
    jid = post_archive(c, bundle_zip).json()["id"]
    assert app.state.jobs.worker.run_once(timeout=0.0)
    assert c.get(f"/jobs/{jid}/meshes/{OBJ_ID}.glb").status_code == 200
    outside = tmp_path / "o.glb"
    outside.write_bytes(b"glTF-leak")
    (jobs_dir / jid / "scene" / "objects" / "x_01").mkdir()
    (jobs_dir / jid / "scene" / "objects" / "x_01" / "mesh.glb").symlink_to(outside)
    assert c.get(f"/jobs/{jid}/meshes/x_01.glb").status_code == 404
