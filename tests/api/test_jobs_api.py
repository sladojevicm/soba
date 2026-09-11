"""End-to-end job API: upload -> id -> worker stages -> job-scoped scene routes.

The worker is driven by hand (``run_worker``) except in the two tests that
exercise the real in-process thread through the app lifespan.
"""

from __future__ import annotations

import json
import re
import time

from _fixtures import (
    FAKE_GLB,
    OBJ_ID,
    make_bundle,
    post_archive,
    run_worker,
    tar_dir,
    zip_dir,
)
from starlette.testclient import TestClient

from api.app import create_app
from api.jobs.paths import JOB_ID_RE
from api.jobs.store import RUNNING
from api.jobs.worker_local import LocalWorker
from server import _HULL_RE, _ID_RE  # legacy re-exports must survive the split


# --- upload --------------------------------------------------------------------
def test_upload_returns_202_and_a_queued_job(client, bundle_zip, jobs_dir):
    r = post_archive(client, bundle_zip, name="office_3.zip")
    assert r.status_code == 202, r.text
    job = r.json()
    assert JOB_ID_RE.match(job["id"])
    assert job["status"] == "queued" and job["state"] == "queued"
    assert job["tier"] == 2
    assert job["source_format"] == "bundle"
    assert job["upload_name"] == "office_3.zip"
    assert job["scene_url"] == f"/jobs/{job['id']}/"
    assert [h["status"] for h in job["history"]] == ["queued"]
    # the archive was kept as received and extracted next to it
    jd = jobs_dir / job["id"]
    assert (jd / "upload.zip").read_bytes() == bundle_zip
    assert (jd / "extracted" / "manifest.json").is_file()
    assert job["bundle_dir"] == str(jd / "extracted")
    # and the job is visible in the list + status route
    assert client.get(f"/api/jobs/{job['id']}").json()["id"] == job["id"]
    assert [j["id"] for j in client.get("/api/jobs").json()["jobs"]] == [job["id"]]


def test_upload_tar_gz_with_top_level_dir_and_tier(client, tmp_path, jobs_dir):
    data = tar_dir(make_bundle(tmp_path / "b"), top="room_0")
    r = post_archive(client, data, name="room_0.tgz", tier="4", content_type="application/gzip")
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["tier"] == 4
    assert job["bundle_dir"] == str(jobs_dir / job["id"] / "extracted" / "room_0")
    assert (jobs_dir / job["id"] / "upload.tar.gz").is_file()


def test_tier_defaults_to_2(client, bundle_zip):
    assert post_archive(client, bundle_zip, tier=None).json()["tier"] == 2


# --- rejections ----------------------------------------------------------------
def _assert_error(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    assert r.json()["error"]["message"]


def test_missing_archive_field_is_400(client):
    r = client.post("/api/jobs", data={"tier": "2"})
    _assert_error(r, 400, "missing_archive")
    r = client.post("/api/jobs", files={"video": ("a.zip", b"PK\x03\x04", "application/zip")})
    _assert_error(r, 400, "missing_archive")


def test_non_multipart_body_is_400(client):
    r = client.post("/api/jobs", content=b"{}", headers={"content-type": "application/json"})
    assert r.status_code == 400


def test_bad_tier_is_422(client, bundle_zip):
    for tier in ("0", "5", "two", ""):
        _assert_error(post_archive(client, bundle_zip, tier=tier), 422, "bad_tier")


def test_video_upload_is_415(client, jobs_dir):
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100
    r = post_archive(client, mp4, name="room.mp4", content_type="video/mp4")
    _assert_error(r, 415, "unsupported_archive")
    assert not jobs_dir.exists() or not any(jobs_dir.iterdir()), "job dir must be cleaned up"


def test_archive_without_bundle_is_422(client, jobs_dir):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("notes.txt", "no bundle here")
    r = post_archive(client, buf.getvalue())
    _assert_error(r, 422, "invalid_upload")
    assert not any(p.is_dir() for p in jobs_dir.iterdir()) if jobs_dir.exists() else True


def test_zip_slip_archive_is_422(client, tmp_path):
    import io
    import zipfile

    buf = io.BytesIO(zip_dir(make_bundle(tmp_path / "b")))
    with zipfile.ZipFile(buf, "a") as zf:
        zf.writestr("../../escape.txt", "x")
    _assert_error(post_archive(client, buf.getvalue()), 422, "invalid_upload")
    assert not (tmp_path / "escape.txt").exists()


def test_upload_over_size_cap_is_413(root_scene, frontend_dir, jobs_dir, fixture_scene, bundle_zip):
    app = create_app(root_scene, frontend_dir, jobs_dir=jobs_dir, worker_mode="mock",
                     fixture_scene=fixture_scene, start_worker=False, max_upload_bytes=64)
    r = post_archive(TestClient(app), bundle_zip)
    _assert_error(r, 413, "upload_too_large")


def test_rejected_uploads_create_no_job(client, jobs_dir):
    post_archive(client, b"garbage")
    assert client.get("/api/jobs").json()["jobs"] == []


# --- status flow ---------------------------------------------------------------
def test_worker_drives_the_job_to_done_with_full_history(app, client, bundle_zip, jobs_dir):
    job = post_archive(client, bundle_zip).json()
    run_worker(app)
    got = client.get(f"/api/jobs/{job['id']}").json()
    assert got["status"] == "done"
    assert [h["status"] for h in got["history"]] == [
        "queued", "running:validating", "running:assembling", "done"]
    assert got["error"] is None
    assert (jobs_dir / job["id"] / "scene" / "scene.json").is_file()


def test_worker_failure_is_recorded_on_the_job(app, client, bundle_zip, jobs_dir):
    job = post_archive(client, bundle_zip).json()
    # sabotage the extracted bundle before the worker looks at it
    (jobs_dir / job["id"] / "extracted" / "manifest.json").unlink()
    run_worker(app)
    got = client.get(f"/api/jobs/{job['id']}").json()
    assert got["status"] == "failed"
    assert "manifest.json" in got["error"]
    assert [h["status"] for h in got["history"]] == ["queued", "running:validating", "failed"]


def test_status_is_running_stage_while_assembling(app, jobs_dir, bundle_zip, client):
    """Freeze the mock worker mid-assembly and read the status from the API."""
    job = post_archive(client, bundle_zip).json()
    w: LocalWorker = app.state.jobs.worker
    w.mock_delay_s = 30.0  # _stop.wait() releases it below
    import threading

    t = threading.Thread(target=w.run_once, kwargs={"timeout": 0.0})
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        s = client.get(f"/api/jobs/{job['id']}").json()
        if s["status"] == "running:assembling":
            break
        time.sleep(0.02)
    assert s["status"] == "running:assembling" and s["state"] == RUNNING
    w._stop.set()
    t.join(timeout=5)
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "done"


def test_in_process_worker_thread_via_lifespan(root_scene, frontend_dir, jobs_dir,
                                               fixture_scene, bundle_zip):
    app = create_app(root_scene, frontend_dir, jobs_dir=jobs_dir, worker_mode="mock",
                     fixture_scene=fixture_scene, start_worker=True)
    with TestClient(app) as c:  # lifespan starts the worker thread
        job = post_archive(c, bundle_zip).json()
        deadline = time.time() + 10
        while time.time() < deadline:
            s = c.get(f"/api/jobs/{job['id']}").json()
            if s["status"] in ("done", "failed"):
                break
            time.sleep(0.02)
        assert s["status"] == "done", s
        assert c.get(f"/jobs/{job['id']}/scene.json").json()["objects"][0]["id"] == OBJ_ID
    assert not app.state.jobs.worker.alive  # stopped on shutdown


def test_restart_re_enqueues_jobs_left_queued(root_scene, frontend_dir, jobs_dir,
                                              fixture_scene, bundle_zip):
    # first process: accept the upload, never run the worker
    app1 = create_app(root_scene, frontend_dir, jobs_dir=jobs_dir, worker_mode="mock",
                      fixture_scene=fixture_scene, start_worker=False)
    job = post_archive(TestClient(app1), bundle_zip).json()
    app1.state.jobs.store.close()
    # second process on the same jobs dir picks it up at startup
    app2 = create_app(root_scene, frontend_dir, jobs_dir=jobs_dir, worker_mode="mock",
                      fixture_scene=fixture_scene, start_worker=True)
    with TestClient(app2) as c:
        deadline = time.time() + 10
        while time.time() < deadline:
            s = c.get(f"/api/jobs/{job['id']}").json()
            if s["status"] in ("done", "failed"):
                break
            time.sleep(0.02)
        assert s["status"] == "done", s


def test_mock_worker_without_fixture_writes_a_schema_valid_empty_scene(
        root_scene, frontend_dir, jobs_dir, tmp_path, bundle_zip):
    app = create_app(root_scene, frontend_dir, jobs_dir=jobs_dir, worker_mode="mock",
                     fixture_scene=tmp_path / "nope", start_worker=False)
    c = TestClient(app)
    job = post_archive(c, bundle_zip).json()
    run_worker(app)
    scene = c.get(f"/jobs/{job['id']}/scene.json").json()
    assert scene["objects"] == [] and "world" in scene and "ground" in scene


def test_real_mode_runs_run_assemble_as_a_subprocess(root_scene, frontend_dir, jobs_dir,
                                                     tmp_path, bundle_zip):
    """`real` mode with a stub scripts/run_assemble.py (open3d is not on this box)."""
    fake_repo = tmp_path / "repo"
    (fake_repo / "scripts").mkdir(parents=True)
    (fake_repo / "scripts" / "run_assemble.py").write_text(
        "import json, sys, pathlib\n"
        "args = sys.argv[1:]\n"
        "out = pathlib.Path(args[args.index('--out') + 1])\n"
        "assert '--no-eval' in args and '--tier' in args and '--bundle' in args\n"
        "if '--gate-only' in args: sys.exit(0)\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'scene.json').write_text(json.dumps({'version': '2.0', 'objects': [],\n"
        "    'tier': int(args[args.index('--tier') + 1])}))\n"
        "print('stub assembled')\n")
    for mode in ("real", "gate-only"):
        app = create_app(root_scene, frontend_dir, jobs_dir=jobs_dir / mode, worker_mode=mode,
                         start_worker=False)
        app.state.jobs.worker.repo_root = fake_repo
        c = TestClient(app)
        job = post_archive(c, bundle_zip, tier="3").json()
        run_worker(app)
        got = c.get(f"/api/jobs/{job['id']}").json()
        assert got["status"] == "done", got
        log = (jobs_dir / mode / job["id"] / "worker.log").read_text()
        assert "run_assemble.py" in log and "--tier 3" in log
        scene = c.get(f"/jobs/{job['id']}/scene.json").json()
        if mode == "real":
            assert scene["tier"] == 3
        else:
            assert "--gate-only" in log and scene["objects"] == []


def test_real_mode_subprocess_failure_fails_the_job(root_scene, frontend_dir, jobs_dir,
                                                    tmp_path, bundle_zip):
    fake_repo = tmp_path / "repo"
    (fake_repo / "scripts").mkdir(parents=True)
    (fake_repo / "scripts" / "run_assemble.py").write_text(
        "import sys; print('no GPU here'); sys.exit(3)\n")
    app = create_app(root_scene, frontend_dir, jobs_dir=jobs_dir, worker_mode="real",
                     start_worker=False)
    app.state.jobs.worker.repo_root = fake_repo
    c = TestClient(app)
    job = post_archive(c, bundle_zip).json()
    run_worker(app)
    got = c.get(f"/api/jobs/{job['id']}").json()
    assert got["status"] == "failed"
    assert "exited 3" in got["error"] and "no GPU here" in got["error"]


# --- job-scoped scene routes ---------------------------------------------------
def _done_job(app, client, bundle_zip) -> str:
    job = post_archive(client, bundle_zip).json()
    run_worker(app)
    return job["id"]


def test_job_scoped_viewer_and_scene_routes(app, client, bundle_zip, fixture_scene):
    jid = _done_job(app, client, bundle_zip)
    r = client.get(f"/jobs/{jid}/")
    assert r.status_code == 200 and "soba viewer" in r.text
    assert client.get(f"/jobs/{jid}", follow_redirects=True).status_code == 200

    scene = client.get(f"/jobs/{jid}/scene.json").json()
    assert scene == json.loads((fixture_scene / "scene.json").read_text())
    assert scene["objects"][0]["id"] == OBJ_ID

    r = client.get(f"/jobs/{jid}/meshes/{OBJ_ID}.glb")
    assert r.status_code == 200
    assert r.headers["content-type"] == "model/gltf-binary"
    assert r.content == FAKE_GLB + b"mesh"

    r = client.get(f"/jobs/{jid}/hulls/{OBJ_ID}_0.glb")
    assert r.status_code == 200 and r.content == FAKE_GLB + b"hull0"

    r = client.get(f"/jobs/{jid}/eval.json")
    assert r.status_code == 200 and r.json()["score"]["value"] == 87.5

    assert client.get(f"/jobs/{jid}/meshes/nope_99.glb").status_code == 404
    assert client.get(f"/jobs/{jid}/hulls/badname.glb").status_code == 400
    assert client.get(f"/jobs/{jid}/meshes/..%2f..%2fetc.glb").status_code in (400, 404)
    assert "no-store" in r.headers["cache-control"]


def test_job_scene_is_partial_safe_before_the_worker_runs(client, bundle_zip):
    jid = post_archive(client, bundle_zip).json()["id"]
    r = client.get(f"/jobs/{jid}/scene.json")
    assert r.status_code == 200 and r.json()["objects"] == []
    assert client.get(f"/jobs/{jid}/eval.json").json() == {"available": False}
    assert client.get(f"/jobs/{jid}/").status_code == 200  # viewer loads, then streams


def test_unknown_or_malformed_job_ids_are_404_envelopes(client):
    for jid in ("0123456789abcdef", "nope", "0123456789ABCDEF", "x" * 16):
        for path in (f"/api/jobs/{jid}", f"/jobs/{jid}/", f"/jobs/{jid}/scene.json",
                     f"/jobs/{jid}/meshes/{OBJ_ID}.glb", f"/jobs/{jid}/hulls/{OBJ_ID}_0.glb",
                     f"/jobs/{jid}/eval.json"):
            r = client.get(path)
            assert r.status_code == 404, path
            assert r.json()["error"]["code"] == "unknown_job", path
        assert client.delete(f"/api/jobs/{jid}").status_code == 404


def test_job_events_replay_and_report_status(root_scene, frontend_dir, jobs_dir,
                                             fixture_scene, bundle_zip):
    """SSE only streams faithfully against a real server (cf. tests/test_server.py)."""
    import socket
    import threading

    import httpx
    import uvicorn

    app = create_app(root_scene, frontend_dir, jobs_dir=jobs_dir, worker_mode="mock",
                     fixture_scene=fixture_scene, start_worker=False)
    jid = _done_job(app, TestClient(app), bundle_zip)

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
        events: list[tuple[str, dict]] = []
        with httpx.Client(timeout=15) as c, \
                c.stream("GET", f"http://127.0.0.1:{port}/jobs/{jid}/events") as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers["content-type"]
            name = None
            for line in r.iter_lines():
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:") and name:
                    events.append((name, json.loads(line[5:].strip() or "{}")))
                    name = None
                if any(n == "job_status" for n, _ in events) and \
                        any(n == "object_added" for n, _ in events):
                    break
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    assert ("object_added", {"id": OBJ_ID}) in events
    statuses = [d for n, d in events if n == "job_status"]
    assert statuses and statuses[-1]["status"] == "done"


# --- delete --------------------------------------------------------------------
def test_delete_removes_record_and_files(app, client, bundle_zip, jobs_dir):
    jid = _done_job(app, client, bundle_zip)
    assert (jobs_dir / jid).is_dir()
    assert client.delete(f"/api/jobs/{jid}").status_code == 204
    assert not (jobs_dir / jid).exists()
    assert client.get(f"/api/jobs/{jid}").status_code == 404
    assert client.get(f"/jobs/{jid}/scene.json").status_code == 404
    assert client.delete(f"/api/jobs/{jid}").status_code == 404
    assert client.get("/api/jobs").json()["jobs"] == []


def test_delete_while_queued_makes_the_worker_skip_it(app, client, bundle_zip):
    jid = post_archive(client, bundle_zip).json()["id"]
    assert client.delete(f"/api/jobs/{jid}").status_code == 204
    assert app.state.jobs.worker.run_once(timeout=0.0) is True  # consumed, no crash
    assert client.get(f"/api/jobs/{jid}").status_code == 404


# --- legacy root routes untouched ---------------------------------------------
def test_legacy_root_routes_serve_the_root_scene_only(app, client, bundle_zip, root_scene):
    jid = _done_job(app, client, bundle_zip)
    root = client.get("/scene.json").json()
    assert root == json.loads((root_scene / "scene.json").read_text())
    assert root["objects"][0]["id"] == "root_table_01"  # not the job's crate_00
    assert client.get("/").status_code == 200
    assert client.get("/meshes/root_table_01.glb").status_code == 200
    assert client.get(f"/meshes/{OBJ_ID}.glb").status_code == 404  # job object is not at root
    assert client.get("/hulls/root_table_01_0.glb").status_code == 200
    assert client.get("/eval.json").json()["score"]["value"] == 87.5
    assert client.get("/assets/app.js").status_code == 200  # static mount still last
    # the plain-text legacy error bodies are unchanged
    assert client.get("/hulls/couchglb.glb").text == "bad hull name"
    assert client.get("/meshes/nope_99.glb").text == "mesh not found"
    # the job's scene is still reachable at its own prefix
    assert client.get(f"/jobs/{jid}/scene.json").json()["objects"][0]["id"] == OBJ_ID


def test_legacy_signature_and_reexports_survive(tmp_path):
    from server import create_app as legacy_create_app

    app = legacy_create_app(scene_dir=tmp_path, frontend_dir=tmp_path)
    c = TestClient(app)
    assert c.get("/scene.json").json()["objects"] == []
    assert c.get("/eval.json").json() == {"available": False}
    assert _ID_RE.match("dining_table_01") and not _ID_RE.match("../x")
    assert _HULL_RE.match("dining_table_01_3").group("i") == "3"
    assert isinstance(_ID_RE, re.Pattern)


def test_default_store_is_lazy_so_importing_server_has_no_side_effects(tmp_path):
    jobs_dir = tmp_path / "lazy_jobs"
    app = create_app(tmp_path, tmp_path, jobs_dir=jobs_dir, start_worker=False)
    assert not jobs_dir.exists()  # nothing touched until the first job call
    assert TestClient(app).get("/api/jobs").json()["jobs"] == []
    assert (jobs_dir / "jobs.sqlite").is_file()
