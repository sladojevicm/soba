"""Contract test for spec/openapi.yaml: the hand-authored OpenAPI document must
match ``create_app().routes`` exactly, and live responses must fit its schemas.

Three layers:
  (a) the document parses and is a valid OpenAPI 3.1 spec (openapi-spec-validator
      when installed, always a structural + $ref-resolution check);
  (b) every route the app registers has a path + method in the spec and vice
      versa; the only undocumented mount is the static frontend/dist catch-all;
  (c) live responses (job create/get/list/delete, the 4xx envelopes, keyed-mode
      401, rate-limit 429, redirect, metrics, scene/eval bodies, the docs routes)
      validate against the response schemas with jsonschema — including the
      ``./scene.schema.json`` reference, which is resolved, never copied.

An optional schemathesis run (marker ``schemathesis``) fuzzes the GET routes
when the package is installed; it is skipped otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import jsonschema
import pytest
import yaml
from _fixtures import OBJ_ID, post_archive, run_worker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient

from api.app import create_app
from api.routes.docs import DOCS_PATH, OPENAPI_PATH, REDOC_URL, SPEC_FILE, load_spec

SPEC_URI = SPEC_FILE.as_uri()
SCENE_SCHEMA_FILE = SPEC_FILE.parent / "scene.schema.json"
SCENE_EXAMPLE_FILE = SPEC_FILE.parent / "scene.example.json"

# Mounts that are legitimately absent from the spec: the static frontend/dist
# catch-all at "/" (index-*.js / index-*.css). Anything else undocumented fails.
STATIC_MOUNT_ALLOW_LIST = {("", StaticFiles)}


# --- helpers -------------------------------------------------------------------
@pytest.fixture(scope="module")
def spec() -> dict:
    return yaml.safe_load(SPEC_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def registry(spec) -> Registry:
    scene = json.loads(SCENE_SCHEMA_FILE.read_text())
    return Registry().with_resources([
        (SPEC_URI, Resource.from_contents(spec, default_specification=DRAFT202012)),
        (SCENE_SCHEMA_FILE.as_uri(), Resource.from_contents(scene)),
        (scene["$id"], Resource.from_contents(scene)),
    ])


def _pointer(*segments: str) -> str:
    return "".join("/" + s.replace("~", "~0").replace("/", "~1") for s in segments)


def _validate_at(registry: Registry, pointer: str, instance) -> None:
    """Validate ``instance`` against the schema at ``pointer`` inside the spec,
    resolving every $ref (local components and ./scene.schema.json)."""
    ref = {"$ref": f"{SPEC_URI}#{quote(pointer, safe='/~')}"}
    jsonschema.Draft202012Validator(ref, registry=registry).validate(instance)


def _response_pointer(spec: dict, path: str, method: str, status: int) -> tuple[dict, str]:
    op = spec["paths"][path][method.lower()]
    assert str(status) in op["responses"], \
        f"{method} {path} answered {status}, which the spec does not document"
    resp = op["responses"][str(status)]
    if "$ref" in resp:
        assert resp["$ref"].startswith("#/"), resp["$ref"]
        pointer = resp["$ref"][1:]
        node = spec
        for seg in pointer.split("/")[1:]:
            node = node[seg.replace("~1", "/").replace("~0", "~")]
        return node, pointer
    return resp, _pointer("paths", path, method.lower(), "responses", str(status))


def assert_matches_spec(spec: dict, registry: Registry, r, path: str, method: str = "GET"):
    """The live response's status, content type, body and headers fit the spec."""
    resp, pointer = _response_pointer(spec, path, method, r.status_code)
    ctype = r.headers.get("content-type", "")
    if "content" in resp:
        # The legacy plain-text 400/404 bodies are sent WITHOUT a Content-Type
        # header (starlette.Response with no media_type); the spec documents
        # them as text/plain, which is the only media type that may match an
        # absent header.
        media = next((m for m in resp["content"]
                      if ctype.startswith(m) or (not ctype and m == "text/plain")), None)
        assert media, f"{method} {path} {r.status_code}: {ctype!r} is not a declared media type"
        if media == "application/json":
            _validate_at(registry, pointer + _pointer("content", media, "schema"), r.json())
    else:
        assert not r.content, f"{method} {path} {r.status_code} must have no body"
    for name, hdr in resp.get("headers", {}).items():
        assert name in r.headers, f"{method} {path} {r.status_code}: header {name} missing"
        jsonschema.Draft202012Validator(hdr["schema"]).validate(r.headers[name])


def route_table(routes: list[BaseRoute], prefix: str = "") -> tuple[dict[str, set[str]], list]:
    """{path: methods} for every Route, recursing into mounted sub-apps; the
    leaf mounts (StaticFiles) come back separately as (path, app type)."""
    table: dict[str, set[str]] = {}
    leaves: list[tuple[str, type]] = []
    for r in routes:
        if isinstance(r, Route):
            methods = {m for m in (r.methods or set()) if m != "HEAD"}  # HEAD rides on GET
            table.setdefault(prefix + r.path, set()).update(methods)
        elif isinstance(r, Mount):
            sub = getattr(r.app, "routes", None)
            if sub:
                t, lv = route_table(sub, prefix + r.path)
                for k, v in t.items():
                    table.setdefault(k, set()).update(v)
                leaves += lv
            else:
                leaves.append((prefix + r.path, type(r.app)))
        else:  # pragma: no cover - nothing else is registered today
            raise AssertionError(f"unexpected route type {type(r).__name__}: {r!r}")
    return table, leaves


def spec_table(spec: dict) -> dict[str, set[str]]:
    http = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
    return {path: {m.upper() for m in item if m in http}
            for path, item in spec["paths"].items()}


def _walk_refs(node, out: list[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                out.append(v)
            else:
                _walk_refs(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_refs(v, out)


# --- (a) the document ------------------------------------------------------------
def test_spec_is_valid_openapi_31(spec):
    assert spec["openapi"].startswith("3.1."), spec["openapi"]
    assert spec["info"]["title"] and spec["info"]["version"]
    assert "bearerAuth" in spec["components"]["securitySchemes"]
    assert spec["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
    try:
        from openapi_spec_validator import OpenAPIV31SpecValidator, validate
    except ImportError:  # structural check only; the dev extra installs it
        pytest.skip("openapi-spec-validator not installed: structural check only")
    validate(spec, base_uri=SPEC_URI, cls=OpenAPIV31SpecValidator)


def test_every_ref_resolves_including_the_scene_schema(spec, registry):
    refs: list[str] = []
    _walk_refs(spec, refs)
    assert "./scene.schema.json" in refs, "the scene body must $ref the frozen schema, not copy it"
    resolver = registry.resolver(base_uri=SPEC_URI)
    for ref in set(refs):
        resolver.lookup(ref)  # raises Unresolvable on a dangling reference
    # the frozen schema is referenced, never inlined
    assert "SceneObject" not in json.dumps(spec["components"]["schemas"])


def test_error_code_enum_covers_every_documented_response(spec):
    """Every per-response code enum is a subset of ErrorCode, and every
    ErrorCode is used by at least one response."""
    codes = set(spec["components"]["schemas"]["ErrorCode"]["enum"])
    used: set[str] = set()
    for resp in spec["components"]["responses"].values():
        schema = resp.get("content", {}).get("application/json", {}).get("schema", {})
        for part in schema.get("allOf", []):
            enum = part.get("properties", {}).get("error", {}).get("properties", {}) \
                .get("code", {}).get("enum")
            if enum:
                used.update(enum)
    assert used <= codes, used - codes
    assert codes <= used, codes - used


def test_error_codes_match_the_code(spec):
    """The enum lists exactly the codes src/api can emit."""
    from api.jobs import upload_errors

    from_code = {"missing_archive", "bad_multipart", "bad_tier", "unknown_job",
                 "unauthorized", "rate_limited", "too_many_streams",
                 "multipart_unavailable", "metrics_unavailable"}
    for name in upload_errors.__all__:
        cls = getattr(upload_errors, name)
        if cls is not upload_errors.UploadError:  # base class, never raised as such
            from_code.add(cls.code)
    assert set(spec["components"]["schemas"]["ErrorCode"]["enum"]) == from_code


def test_scene_example_and_empty_scene_fit_the_scene_body(spec, registry):
    example = json.loads(SCENE_EXAMPLE_FILE.read_text())
    body = _pointer("components", "responses", "SceneBody", "content", "application/json",
                    "schema")
    _validate_at(registry, body, example)
    _validate_at(registry, body, {"version": "2.0", "objects": []})
    with pytest.raises(jsonschema.ValidationError):
        _validate_at(registry, body, {"version": "2.0", "objects": [{"id": "x"}]})
    with pytest.raises(jsonschema.ValidationError):  # the frozen schema is really enforced
        _validate_at(registry, _pointer("components", "schemas", "Scene"),
                     {"version": "2.0", "objects": []})


# --- (b) routes vs spec ------------------------------------------------------------
def test_every_route_is_documented_and_every_documented_route_exists(app, spec):
    have, leaves = route_table(app.routes)
    want = spec_table(spec)
    assert set(have) == set(want), {
        "routes missing from spec/openapi.yaml": sorted(set(have) - set(want)),
        "spec paths with no route": sorted(set(want) - set(have)),
    }
    for path in have:
        assert have[path] == want[path], f"{path}: app {have[path]} vs spec {want[path]}"
    assert {(p, t) for p, t in leaves} == STATIC_MOUNT_ALLOW_LIST, leaves


def test_static_mount_is_last_so_documented_routes_win(app):
    assert isinstance(app.routes[-1], Mount) and isinstance(app.routes[-1].app, StaticFiles)


def test_route_table_is_the_same_without_a_frontend_dir(root_scene, jobs_dir, tmp_path, spec):
    """The only thing a missing frontend/dist changes is the static mount."""
    app = create_app(root_scene, tmp_path / "no-frontend", jobs_dir=jobs_dir,
                     worker_mode="mock", start_worker=False)
    have, leaves = route_table(app.routes)
    assert set(have) == set(spec_table(spec)) and leaves == []


# --- (c) live responses --------------------------------------------------------------
def test_job_lifecycle_responses_match_the_spec(app, client, bundle_zip, spec, registry):
    r = post_archive(client, bundle_zip, name="office_3.zip")
    assert_matches_spec(spec, registry, r, "/api/jobs", "POST")
    assert r.status_code == 202
    jid = r.json()["id"]

    assert_matches_spec(spec, registry, client.get("/api/jobs"), "/api/jobs")
    r = client.get(f"/api/jobs/{jid}")
    assert_matches_spec(spec, registry, r, "/api/jobs/{job_id}")
    assert r.json()["status"] == "queued"

    # before the worker: partial-safe bodies and the viewer
    assert_matches_spec(spec, registry, client.get(f"/jobs/{jid}/scene.json"),
                        "/jobs/{job_id}/scene.json")
    assert_matches_spec(spec, registry, client.get(f"/jobs/{jid}/eval.json"),
                        "/jobs/{job_id}/eval.json")
    assert_matches_spec(spec, registry, client.get(f"/jobs/{jid}/"), "/jobs/{job_id}/")
    r = client.get(f"/jobs/{jid}", follow_redirects=False)
    assert_matches_spec(spec, registry, r, "/jobs/{job_id}")
    assert r.status_code == 307 and r.headers["location"] == f"/jobs/{jid}/"

    run_worker(app)
    r = client.get(f"/api/jobs/{jid}")
    assert_matches_spec(spec, registry, r, "/api/jobs/{job_id}")
    assert r.json()["status"] == "done"
    assert [h["status"] for h in r.json()["history"]] == [
        "queued", "running:validating", "running:assembling", "done"]

    r = client.get(f"/jobs/{jid}/meshes/{OBJ_ID}.glb")
    assert_matches_spec(spec, registry, r, "/jobs/{job_id}/meshes/{id}.glb")
    r = client.get(f"/jobs/{jid}/hulls/{OBJ_ID}_0.glb")
    assert_matches_spec(spec, registry, r, "/jobs/{job_id}/hulls/{stem}.glb")
    assert r.status_code == 200
    assert_matches_spec(spec, registry, client.get(f"/jobs/{jid}/meshes/nope_99.glb"),
                        "/jobs/{job_id}/meshes/{id}.glb")
    assert_matches_spec(spec, registry, client.get(f"/jobs/{jid}/hulls/badname.glb"),
                        "/jobs/{job_id}/hulls/{stem}.glb")

    r = client.delete(f"/api/jobs/{jid}")
    assert_matches_spec(spec, registry, r, "/api/jobs/{job_id}", "DELETE")
    assert r.status_code == 204
    r = client.get(f"/api/jobs/{jid}")
    assert_matches_spec(spec, registry, r, "/api/jobs/{job_id}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "unknown_job"


def test_scene_and_eval_bodies_match_both_branches(root_scene, frontend_dir, jobs_dir,
                                                  tmp_path, bundle_zip, spec, registry):
    """Root: a real eval.json + a scene dir without scene.json (EmptyScene).
    Job: the mock worker's schema-valid empty scene (Scene) + no eval."""
    empty_root = tmp_path / "empty_root"
    empty_root.mkdir()
    app = create_app(empty_root, frontend_dir, jobs_dir=jobs_dir, worker_mode="mock",
                     fixture_scene=tmp_path / "nope", start_worker=False)
    c = TestClient(app)
    r = c.get("/scene.json")
    assert_matches_spec(spec, registry, r, "/scene.json")
    assert r.json() == {"version": "2.0", "objects": []}
    assert_matches_spec(spec, registry, c.get("/eval.json"), "/eval.json")
    assert_matches_spec(spec, registry, c.get("/"), "/")

    jid = post_archive(c, bundle_zip).json()["id"]
    run_worker(app)
    r = c.get(f"/jobs/{jid}/scene.json")
    assert_matches_spec(spec, registry, r, "/jobs/{job_id}/scene.json")
    assert "world" in r.json() and r.json()["objects"] == []
    _validate_at(registry, _pointer("components", "schemas", "Scene"), r.json())

    app2 = create_app(root_scene, frontend_dir, jobs_dir=tmp_path / "jobs2",
                      worker_mode="mock", start_worker=False)
    c2 = TestClient(app2)
    r = c2.get("/eval.json")
    assert_matches_spec(spec, registry, r, "/eval.json")
    assert r.json()["score"]["value"] == 87.5
    assert_matches_spec(spec, registry, c2.get("/meshes/root_table_01.glb"), "/meshes/{id}.glb")
    assert_matches_spec(spec, registry, c2.get("/hulls/root_table_01_0.glb"),
                        "/hulls/{stem}.glb")
    assert_matches_spec(spec, registry, c2.get("/meshes/nope.glb"), "/meshes/{id}.glb")
    assert_matches_spec(spec, registry, c2.get("/hulls/bad.glb"), "/hulls/{stem}.glb")


def test_upload_rejections_match_the_spec(client, bundle_zip, spec, registry):
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100
    r = post_archive(client, mp4, name="room.mp4", content_type="video/mp4")
    assert_matches_spec(spec, registry, r, "/api/jobs", "POST")
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_archive"

    r = client.post("/api/jobs", data={"tier": "2"})
    assert_matches_spec(spec, registry, r, "/api/jobs", "POST")
    assert r.status_code == 400 and r.json()["error"]["code"] == "missing_archive"

    r = post_archive(client, bundle_zip, tier="9")
    assert_matches_spec(spec, registry, r, "/api/jobs", "POST")
    assert r.status_code == 422 and r.json()["error"]["code"] == "bad_tier"

    r = client.post("/api/jobs", content=b"{}", headers={"content-type": "application/json"})
    assert_matches_spec(spec, registry, r, "/api/jobs", "POST")
    assert r.status_code == 400


def test_unknown_job_envelopes_match_the_spec(client, spec, registry):
    jid = "0123456789abcdef"
    for path, url in (("/api/jobs/{job_id}", f"/api/jobs/{jid}"),
                      ("/jobs/{job_id}", f"/jobs/{jid}"),
                      ("/jobs/{job_id}/", f"/jobs/{jid}/"),
                      ("/jobs/{job_id}/scene.json", f"/jobs/{jid}/scene.json"),
                      ("/jobs/{job_id}/eval.json", f"/jobs/{jid}/eval.json"),
                      ("/jobs/{job_id}/meshes/{id}.glb", f"/jobs/{jid}/meshes/{OBJ_ID}.glb"),
                      ("/jobs/{job_id}/hulls/{stem}.glb", f"/jobs/{jid}/hulls/{OBJ_ID}_0.glb"),
                      ("/jobs/{job_id}/events", f"/jobs/{jid}/events")):
        r = client.get(url, follow_redirects=False)
        assert_matches_spec(spec, registry, r, path)
        assert r.status_code == 404 and r.json()["error"]["code"] == "unknown_job", url
    r = client.delete(f"/api/jobs/{jid}")
    assert_matches_spec(spec, registry, r, "/api/jobs/{job_id}", "DELETE")
    assert r.status_code == 404


@pytest.fixture
def make_app(monkeypatch, root_scene, frontend_dir, jobs_dir, fixture_scene):
    def _make(env: dict[str, str] | None = None):
        for k in ("SOBA_API_KEYS", "SOBA_AUTH_LEGACY", "SOBA_RATE_LIMIT_RPS",
                  "SOBA_RATE_LIMIT_BURST", "SOBA_RATE_LIMIT_UPLOAD_RPS",
                  "SOBA_RATE_LIMIT_UPLOAD_BURST", "SOBA_CORS_ORIGINS"):
            monkeypatch.delenv(k, raising=False)
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
        return create_app(root_scene, frontend_dir, jobs_dir=jobs_dir, worker_mode="mock",
                          fixture_scene=fixture_scene, start_worker=False)

    return _make


def test_keyed_mode_401_matches_the_spec_and_docs_stay_open(make_app, spec, registry):
    c = TestClient(make_app({"SOBA_API_KEYS": "alice:sekrit"}))
    for path, url, method in (("/api/jobs", "/api/jobs", "GET"),
                              ("/api/jobs", "/api/jobs", "POST"),
                              ("/api/jobs/{job_id}", "/api/jobs/0123456789abcdef", "GET"),
                              ("/api/jobs/{job_id}", "/api/jobs/0123456789abcdef", "DELETE"),
                              ("/jobs/{job_id}/scene.json",
                               "/jobs/0123456789abcdef/scene.json", "GET")):
        r = c.request(method, url)
        assert_matches_spec(spec, registry, r, path, method)
        assert r.status_code == 401, url
        assert r.headers["www-authenticate"] == 'Bearer realm="soba"'
    r = c.get("/api/jobs", headers={"Authorization": "Bearer wrong"})
    assert_matches_spec(spec, registry, r, "/api/jobs")
    assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"]
    r = c.get("/api/jobs", headers={"Authorization": "Bearer sekrit"})
    assert_matches_spec(spec, registry, r, "/api/jobs")
    assert r.status_code == 200
    # the description routes are public in keyed mode; legacy root stays open
    assert c.get(OPENAPI_PATH).status_code == 200
    assert c.get(DOCS_PATH).status_code == 200
    assert c.get("/scene.json").status_code == 200
    assert c.get("/metrics").status_code in (200, 501)  # known limitation: never gated

    # SOBA_AUTH_LEGACY=1 closes everything, the docs included
    c = TestClient(make_app({"SOBA_API_KEYS": "alice:sekrit", "SOBA_AUTH_LEGACY": "1"}))
    for path, url in ((OPENAPI_PATH, OPENAPI_PATH), (DOCS_PATH, DOCS_PATH),
                      ("/scene.json", "/scene.json"), ("/", "/")):
        r = c.get(url)
        assert_matches_spec(spec, registry, r, path)
        assert r.status_code == 401, url
    assert c.get(OPENAPI_PATH, headers={"Authorization": "Bearer sekrit"}).status_code == 200


def test_rate_limit_429_matches_the_spec(make_app, spec, registry):
    c = TestClient(make_app({"SOBA_RATE_LIMIT_RPS": "0.001", "SOBA_RATE_LIMIT_BURST": "1"}))
    assert c.get("/api/jobs").status_code == 200
    r = c.get("/api/jobs")
    assert_matches_spec(spec, registry, r, "/api/jobs")
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert r.headers["retry-after"].isdigit()
    for path in ("/scene.json", "/eval.json", "/metrics", OPENAPI_PATH, DOCS_PATH, "/events",
                 "/meshes/{id}.glb", "/hulls/{stem}.glb", "/"):
        url = path.replace("{id}", OBJ_ID).replace("{stem}", f"{OBJ_ID}_0")
        assert_matches_spec(spec, registry, c.get(url), path)


def test_metrics_response_matches_the_spec(client, spec, registry):
    r = client.get("/metrics")
    assert_matches_spec(spec, registry, r, "/metrics")
    assert r.status_code in (200, 501)
    if r.status_code == 200:
        assert r.headers["content-type"].startswith("text/plain")
        assert "soba_http_requests_total" in r.text


def test_openapi_json_and_docs_routes(client, spec, registry):
    r = client.get(OPENAPI_PATH)
    assert_matches_spec(spec, registry, r, OPENAPI_PATH)
    assert r.status_code == 200 and r.json() == spec == load_spec()
    assert "no-store" in r.headers["cache-control"]
    assert r.headers["x-content-type-options"] == "nosniff"

    r = client.get(DOCS_PATH)
    assert_matches_spec(spec, registry, r, DOCS_PATH)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert 'spec-url="openapi.json"' in r.text  # relative: resolves to /api/openapi.json
    assert REDOC_URL in r.text and "cdn.jsdelivr.net" in r.text  # network at view time
    assert "openapi.json" in r.text and "fallback" in r.text


def test_spec_file_is_the_one_in_spec_dir():
    assert SPEC_FILE == Path(__file__).resolve().parents[2] / "spec" / "openapi.yaml"
    assert SPEC_FILE.is_file() and SCENE_SCHEMA_FILE.is_file()


# --- optional: schemathesis over the GET routes ----------------------------------------
# Not a dependency of any extra (it pulls in hypothesis and friends); the test
# exists only when `pip install schemathesis` was done by hand.
try:
    import hypothesis
    import schemathesis
except ImportError:  # pragma: no cover - depends on the box
    @pytest.mark.schemathesis
    @pytest.mark.skip(reason="optional: pip install schemathesis")
    def test_schemathesis_get_routes():
        pass
else:
    from schemathesis.specs.openapi.checks import content_type_conformance, unsupported_method

    @pytest.fixture
    def api_schema(app):
        return schemathesis.openapi.from_asgi(OPENAPI_PATH, app)

    # Filters go on the lazy schema (filters set on the fixture's return value
    # are not carried over). /events streams forever (heartbeats), so it cannot
    # be fuzzed with a blocking client; every other GET is fair game.
    lazy_schema = (schemathesis.pytest.from_fixture("api_schema")
                   .include(method="GET").exclude(path_regex=r"/events$"))

    @pytest.mark.schemathesis
    @lazy_schema.parametrize()
    @hypothesis.settings(max_examples=5, deadline=None,
                         suppress_health_check=list(hypothesis.HealthCheck))
    def test_schemathesis_get_routes(case):
        # `unsupported_method` sends TRACE/PUT/... and wants an `Allow` header
        # on the 405; those land on the static frontend/dist catch-all mount,
        # whose 405 has none. That is Starlette's StaticFiles, not the API.
        response = case.call()
        excluded = [unsupported_method]
        # The legacy plain-text 400/404 bodies (`bad object id`, `mesh not
        # found`, ...) are sent without a Content-Type header, byte-for-byte as
        # before the job API; the spec documents them as text/plain. Only that
        # check is waived, and only for those responses.
        has_ctype = any(k.lower() == "content-type" for k in response.headers)
        if response.status_code in (400, 404) and not has_ctype:
            excluded.append(content_type_conformance)
        case.validate_response(response, excluded_checks=excluded)
