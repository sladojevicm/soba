"""Security phase B: bearer API keys and the token-bucket rate limiter.

Every app here is built AFTER monkeypatching the environment, because
``security_middleware()`` reads its configuration when ``create_app`` runs.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from _fixtures import OBJ_ID, post_archive
from starlette.testclient import TestClient

from api.app import create_app
from api.security.auth import AuthConfig, AuthMiddleware, parse_api_keys
from api.security.ratelimit import (
    Limit,
    RateLimitConfig,
    RateLimiter,
    TokenBucket,
    client_ip,
)

KEYS = "alice:sekrit-a,bob:sekrit-b,k6:sekrit-lt:loadtest"
ALICE = {"Authorization": "Bearer sekrit-a"}
BOB = {"Authorization": "Bearer sekrit-b"}
LOADTEST = {"Authorization": "Bearer sekrit-lt"}


@pytest.fixture
def make_app(monkeypatch, root_scene, frontend_dir, jobs_dir, fixture_scene):
    def _make(env: dict[str, str] | None = None, **kw):
        for k in ("SOBA_API_KEYS", "SOBA_AUTH_LEGACY", "SOBA_RATE_LIMIT_RPS",
                  "SOBA_RATE_LIMIT_BURST", "SOBA_RATE_LIMIT_UPLOAD_RPS",
                  "SOBA_RATE_LIMIT_UPLOAD_BURST", "SOBA_TRUST_PROXY", "SOBA_CORS_ORIGINS"):
            monkeypatch.delenv(k, raising=False)
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
        return create_app(root_scene, frontend_dir, jobs_dir=jobs_dir, worker_mode="mock",
                          fixture_scene=fixture_scene, start_worker=False, **kw)

    return _make


# --- key parsing -------------------------------------------------------------------
def test_parse_api_keys():
    keys = parse_api_keys(" alice:sekrit-a , bob:b:LOADTEST,,")
    assert [(k.name, k.secret, k.loadtest) for k in keys] == [
        ("alice", "sekrit-a", False), ("bob", "b", True)]
    assert parse_api_keys(None) == () and parse_api_keys("  ") == ()
    assert "sekrit" not in repr(keys[0])
    for bad in ("nocolon", ":key", "name:", "a:b:c:wat", "a:x,a:y"):
        with pytest.raises(ValueError) as ei:
            parse_api_keys(bad)
        assert "SOBA_API_KEYS" in str(ei.value)
    with pytest.raises(ValueError) as ei:
        parse_api_keys("svc:hunter2:bogus")
    assert "hunter2" not in str(ei.value)  # never echo key material


def test_lookup_is_by_secret_not_by_name():
    cfg = AuthConfig(parse_api_keys(KEYS))
    assert cfg.lookup("sekrit-b").name == "bob"
    assert cfg.lookup("bob") is None and cfg.lookup("") is None
    assert cfg.requires_auth("/api/jobs") and cfg.requires_auth("/jobs/abc/scene.json")
    assert cfg.requires_auth("/jobs") and not cfg.requires_auth("/jobsx")
    assert not cfg.requires_auth("/scene.json") and not cfg.requires_auth("/")
    assert AuthConfig(cfg.keys, protect_legacy=True).requires_auth("/scene.json")
    assert not AuthConfig().requires_auth("/api/jobs")  # open mode


# --- open mode -----------------------------------------------------------------------
def test_open_mode_by_default_with_one_warning(make_app, caplog, bundle_zip):
    with caplog.at_level(logging.WARNING, logger="soba.api.security"):
        app = make_app()
    warnings = [r for r in caplog.records if "SOBA_API_KEYS" in r.getMessage()]
    assert len(warnings) == 1 and warnings[0].levelno == logging.WARNING
    c = TestClient(app)
    assert c.get("/api/jobs").status_code == 200
    assert c.get("/scene.json").status_code == 200
    job = post_archive(c, bundle_zip).json()
    assert c.get(f"/jobs/{job['id']}/scene.json").status_code == 200
    assert "www-authenticate" not in c.get("/api/jobs").headers


# --- keys set --------------------------------------------------------------------------
def test_api_and_job_routes_require_a_key(make_app, bundle_zip, caplog):
    with caplog.at_level(logging.INFO, logger="soba.api.security"):
        app = make_app({"SOBA_API_KEYS": KEYS})
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert "sekrit" not in caplog.text
    c = TestClient(app)

    r = c.get("/api/jobs")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    assert r.headers["www-authenticate"] == 'Bearer realm="soba"'

    r = c.get("/api/jobs", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert 'error="invalid_token"' in r.headers["www-authenticate"]
    assert c.get("/api/jobs", headers={"Authorization": "Basic abc"}).status_code == 401
    assert c.get("/api/jobs", headers={"Authorization": "bearer sekrit-a"}).status_code == 200

    assert post_archive(c, bundle_zip).status_code == 401
    job = post_archive(TestClient(app, headers=ALICE), bundle_zip).json()
    for path in (f"/jobs/{job['id']}/", f"/jobs/{job['id']}/scene.json",
                 f"/api/jobs/{job['id']}"):
        assert c.get(path).status_code == 401, path
        assert c.get(path, headers=BOB).status_code == 200, path
    # /events streams forever under TestClient, so only the closed side is checked
    assert c.get(f"/jobs/{job['id']}/events").status_code == 401
    assert c.delete(f"/api/jobs/{job['id']}").status_code == 401
    assert c.delete(f"/api/jobs/{job['id']}", headers=BOB).status_code == 204


def test_legacy_root_routes_stay_open_unless_flagged(make_app):
    c = TestClient(make_app({"SOBA_API_KEYS": KEYS}))
    for path in ("/", "/scene.json", "/eval.json", "/meshes/root_table_01.glb",
                 "/hulls/root_table_01_0.glb", "/assets/app.js"):
        assert c.get(path).status_code == 200, path

    c = TestClient(make_app({"SOBA_API_KEYS": KEYS, "SOBA_AUTH_LEGACY": "1"}))
    for path in ("/", "/scene.json", "/meshes/root_table_01.glb", "/assets/app.js"):
        assert c.get(path).status_code == 401, path
    assert c.get("/scene.json", headers=ALICE).status_code == 200
    # the flag alone, without keys, changes nothing
    c = TestClient(make_app({"SOBA_AUTH_LEGACY": "1"}))
    assert c.get("/api/jobs").status_code == 200


def test_middleware_exposes_the_key_name_on_request_state():
    seen = {}

    async def inner(scope, receive, send):
        seen.update(scope["state"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = AuthMiddleware(inner, AuthConfig(parse_api_keys(KEYS)))

    async def call(headers):
        sent = []

        async def send(m):
            sent.append(m)

        async def receive():
            return {"type": "http.request", "body": b""}

        await mw({"type": "http", "method": "GET", "path": "/api/jobs",
                  "headers": headers}, receive, send)
        return sent[0]["status"]

    assert asyncio.run(call([(b"authorization", b"Bearer sekrit-lt")])) == 204
    assert seen["api_key_name"] == "k6" and seen["api_key_loadtest"] is True
    assert asyncio.run(call([])) == 401
    assert seen["api_key_name"] == "k6"  # untouched by the rejected call


# --- rate limiting ---------------------------------------------------------------------
def test_token_bucket_refills_over_time():
    clock = [100.0]
    rl = RateLimiter(RateLimitConfig(), clock=lambda: clock[0])
    lim = Limit(rps=2.0, burst=2)
    assert rl.check("g", "ip:1", lim) == 0.0
    assert rl.check("g", "ip:1", lim) == 0.0
    wait = rl.check("g", "ip:1", lim)
    assert wait == pytest.approx(0.5)
    assert rl.check("g", "ip:2", lim) == 0.0  # other identity, own bucket
    clock[0] += 0.5
    assert rl.check("g", "ip:1", lim) == 0.0
    assert rl.check("g", "ip:1", Limit(0, 0)) == 0.0  # disabled limit
    b = TokenBucket(Limit(1.0, 1), now=0.0)
    assert b.take(0.0) == 0.0 and b.take(0.0) == pytest.approx(1.0)


def test_client_ip_honours_forwarded_for_only_behind_a_trusted_proxy():
    scope = {"client": ("10.0.0.9", 1234),
             "headers": [(b"x-forwarded-for", b"1.2.3.4, 5.6.7.8")]}
    assert client_ip(scope, trust_proxy=False) == "10.0.0.9"
    assert client_ip(scope, trust_proxy=True) == "5.6.7.8"  # rightmost hop
    assert client_ip({"client": None, "headers": []}, False) == "unknown"


def test_config_from_env(monkeypatch):
    for k in ("SOBA_RATE_LIMIT_RPS", "SOBA_RATE_LIMIT_BURST", "SOBA_TRUST_PROXY"):
        monkeypatch.delenv(k, raising=False)
    d = RateLimitConfig.from_env()
    assert d.general == Limit(100.0, 200) and d.upload == Limit(1.0, 10) and not d.trust_proxy
    monkeypatch.setenv("SOBA_RATE_LIMIT_RPS", "0.5")
    monkeypatch.setenv("SOBA_RATE_LIMIT_BURST", "3")
    monkeypatch.setenv("SOBA_RATE_LIMIT_UPLOAD_RPS", "0")
    monkeypatch.setenv("SOBA_TRUST_PROXY", "1")
    cfg = RateLimitConfig.from_env()
    assert cfg.general == Limit(0.5, 3) and not cfg.upload.enabled and cfg.trust_proxy


def test_429_with_retry_after_per_ip(make_app):
    c = TestClient(make_app({"SOBA_RATE_LIMIT_RPS": "0.01", "SOBA_RATE_LIMIT_BURST": "3"}))
    for _ in range(3):
        assert c.get("/scene.json").status_code == 200
    r = c.get("/scene.json")
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    assert int(r.headers["retry-after"]) >= 1
    assert c.get("/api/jobs").status_code == 429  # same bucket for every route


def test_buckets_are_per_key_and_loadtest_keys_bypass(make_app):
    c = TestClient(make_app({"SOBA_API_KEYS": KEYS, "SOBA_RATE_LIMIT_RPS": "0.01",
                             "SOBA_RATE_LIMIT_BURST": "2"}))
    assert [c.get("/api/jobs", headers=ALICE).status_code for _ in range(3)] == [200, 200, 429]
    assert c.get("/api/jobs", headers=BOB).status_code == 200  # bob has his own bucket
    assert all(c.get("/api/jobs", headers=LOADTEST).status_code == 200 for _ in range(20))
    # a bad guess costs the caller's per-IP budget (limiter runs before auth)
    bad = {"Authorization": "Bearer nope"}
    assert c.get("/api/jobs", headers=bad).status_code == 401
    assert c.get("/api/jobs", headers=bad).status_code == 401
    assert c.get("/api/jobs", headers=bad).status_code == 429
    assert c.get("/scene.json").status_code == 429  # ...and the anonymous bucket is shared


def test_upload_route_has_its_own_stricter_bucket(make_app, bundle_zip):
    c = TestClient(make_app({"SOBA_RATE_LIMIT_UPLOAD_RPS": "0.01",
                             "SOBA_RATE_LIMIT_UPLOAD_BURST": "1"}))
    assert post_archive(c, bundle_zip).status_code == 202
    r = post_archive(c, bundle_zip)
    assert r.status_code == 429 and "retry-after" in r.headers
    assert c.get("/api/jobs").status_code == 200  # the general bucket is untouched
    assert len(c.get("/api/jobs").json()["jobs"]) == 1


def test_default_limits_do_not_trip_a_viewer_page_load(make_app):
    c = TestClient(make_app())
    for _ in range(150):
        assert c.get("/scene.json").status_code == 200
    assert c.get(f"/meshes/{OBJ_ID}.glb").status_code == 404  # not 429
