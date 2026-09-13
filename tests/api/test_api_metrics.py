"""Observability phase B: GET /metrics, the request-log middleware with job-id
correlation, run_metrics.json ingestion, and job-state transition logging.

CPU-only, no open3d: the mock worker copies `fixture_scene`, so dropping a
run_metrics.json into that directory is how a "finished job" gets one.
"""

from __future__ import annotations

import io
import json
import logging
import shutil
from pathlib import Path

import pytest
from _fixtures import post_archive, run_worker
prometheus_client = pytest.importorskip(
    "prometheus_client", reason="telemetry extra not installed (pip install -e '.[telemetry]')")
from prometheus_client.parser import text_string_to_metric_families  # noqa: E402
from starlette.testclient import TestClient

import telemetry
from api.app import create_app
from api.jobs.store import FAILED, RUNNING, JobRecord, MemoryJobStore
from api.routes import metrics as metrics_mod
from api.routes.metrics import job_id_from_path, route_template

FIXTURE = Path(__file__).with_name("run_metrics_fixture.json")


def samples(text: str, name: str) -> dict[tuple, float]:
    """{(sorted label items): value} for every sample of metric `name`."""
    out = {}
    for fam in text_string_to_metric_families(text):
        for s in fam.samples:
            if s.name == name:
                out[tuple(sorted(s.labels.items()))] = s.value
    return out


def value(text: str, name: str, **labels) -> float | None:
    return samples(text, name).get(tuple(sorted(labels.items())))


# --- path helpers -----------------------------------------------------------
@pytest.mark.parametrize("path,route,job", [
    ("/", "/", None),
    ("/scene.json", "/scene.json", None),
    ("/meshes/chair_00.glb", "/meshes/{id}.glb", None),
    ("/hulls/chair_00_3.glb", "/hulls/{stem}.glb", None),
    ("/api/jobs", "/api/jobs", None),
    ("/api/jobs/0123456789abcdef", "/api/jobs/{id}", "0123456789abcdef"),
    ("/jobs/0123456789abcdef", "/jobs/{id}", "0123456789abcdef"),
    ("/jobs/0123456789abcdef/", "/jobs/{id}/", "0123456789abcdef"),
    ("/jobs/0123456789abcdef/scene.json", "/jobs/{id}/scene.json", "0123456789abcdef"),
    ("/jobs/0123456789abcdef/hulls/x_0.glb", "/jobs/{id}/hulls/{stem}.glb", "0123456789abcdef"),
    ("/jobs/0123456789abcdef/events", "/jobs/{id}/events", "0123456789abcdef"),
    ("/assets/index-abc123.js", "/assets/*", None),
    ("/metrics", "/metrics", None),
    ("/etc/passwd", "/other", None),
    ("/api/jobs/x/y/z", "/other", "x"),
])
def test_route_template_and_job_id(path, route, job):
    assert route_template(path) == route
    assert job_id_from_path(path) == job


# --- /metrics ----------------------------------------------------------------
def test_metrics_endpoint_serves_prometheus_text_with_http_counters(client):
    client.get("/scene.json")
    client.get("/api/jobs/0123456789abcdef")  # 404
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "version=" in r.headers["content-type"]  # 0.0.4 or 1.0.0 text format
    text = r.text
    assert value(text, "soba_http_requests_total", method="GET", route="/scene.json",
                 status="200") == 1.0
    assert value(text, "soba_http_requests_total", method="GET", route="/api/jobs/{id}",
                 status="404") == 1.0
    assert value(text, "soba_http_request_duration_seconds_count", method="GET",
                 route="/scene.json") == 1.0
    # fixed label sets are pre-registered so dashboards see zeros, not gaps
    for state in ("queued", "running", "done", "failed"):
        assert value(text, "soba_jobs", state=state) == 0.0
    for strategy in ("tsdf", "completion", "generative"):
        assert value(text, "soba_gate_decisions_total", strategy=strategy) == 0.0
    assert value(text, "soba_run_metrics_ingest_errors_total") == 0.0


def test_job_state_gauges_follow_the_store(client, app, bundle_zip):
    job = post_archive(client, bundle_zip).json()
    text = client.get("/metrics").text
    assert value(text, "soba_jobs", state="queued") == 1.0
    run_worker(app)
    assert client.get(f"/api/jobs/{job['id']}").json()["state"] == "done"
    text = client.get("/metrics").text
    assert value(text, "soba_jobs", state="queued") == 0.0
    assert value(text, "soba_jobs", state="done") == 1.0
    # mock worker writes no run_metrics.json: nothing ingested, no error
    assert value(text, "soba_job_runs_total", status="ok") == 0.0
    assert value(text, "soba_run_metrics_ingest_errors_total") == 0.0


def test_run_metrics_ingested_once_per_finished_job(client, app, bundle_zip, fixture_scene):
    shutil.copy(FIXTURE, fixture_scene / "run_metrics.json")
    post_archive(client, bundle_zip)
    run_worker(app)
    text = client.get("/metrics").text
    assert value(text, "soba_job_runs_total", status="ok") == 1.0
    assert value(text, "soba_gate_decisions_total", strategy="completion") == 2.0
    assert value(text, "soba_gate_decisions_total", strategy="generative") == 1.0
    assert value(text, "soba_gate_decisions_total", strategy="tsdf") == 0.0
    assert value(text, "soba_gate_routed_total", strategy="completion") == 2.0
    assert value(text, "soba_pipeline_stage_seconds_total", stage="gate") == 0.32
    assert value(text, "soba_pipeline_stage_runs_total", stage="gate") == 4.0
    assert value(text, "soba_pipeline_stage_seconds_total", stage="run") == 67.5
    assert value(text, "soba_pipeline_drops_total", reason="cloud_too_small") == 1.0
    assert value(text, "soba_remote_calls_total", kind="runpod/gen") == 1.0
    assert value(text, "soba_remote_call_seconds_total", kind="runpod/gen") == 38.0
    assert value(text, "soba_remote_est_usd_total", kind="runpod/gen") == 0.03
    assert value(text, "soba_remote_est_usd_total", kind="runpod/complete") in (None, 0.0)
    # a second scrape does not double count; a second job does add up
    text2 = client.get("/metrics").text
    assert value(text2, "soba_gate_decisions_total", strategy="completion") == 2.0
    assert app.state.telemetry.refresh() == 0
    post_archive(client, bundle_zip)
    run_worker(app)
    text3 = client.get("/metrics").text
    assert value(text3, "soba_gate_decisions_total", strategy="completion") == 4.0
    assert value(text3, "soba_job_runs_total", status="ok") == 2.0


def test_ingestion_never_recomputes_and_uses_the_contract(app):
    """The counters are fed straight from the file's own numbers."""
    tel = app.state.telemetry
    data = json.loads(FIXTURE.read_text())
    telemetry.validate(data)
    data["gate"]["counts"]["completion"] = 7      # deliberately != len(per_object)
    tel.ingest_run_metrics(data)
    text = client_text(app)
    assert value(text, "soba_gate_decisions_total", strategy="completion") == 7.0


def client_text(app) -> str:
    return TestClient(app).get("/metrics").text


def test_bad_run_metrics_file_is_counted_not_fatal(client, app, bundle_zip, fixture_scene,
                                                   caplog):
    (fixture_scene / "run_metrics.json").write_text("{not json")
    post_archive(client, bundle_zip)
    run_worker(app)
    with caplog.at_level(logging.WARNING, logger="api.metrics"):
        r = client.get("/metrics")
    assert r.status_code == 200
    assert value(r.text, "soba_run_metrics_ingest_errors_total") == 1.0
    assert value(r.text, "soba_job_runs_total", status="ok") == 0.0
    rec = next(x for x in caplog.records if getattr(x, "fields", {}).get("event") == "ingest_error")
    assert rec.fields["job_id"] and "JSONDecodeError" in rec.fields["error"]
    # schema-breaking content is rejected the same way
    (fixture_scene / "run_metrics.json").write_text(json.dumps({"schema": 2}))
    post_archive(client, bundle_zip)
    run_worker(app)
    assert value(client.get("/metrics").text, "soba_run_metrics_ingest_errors_total") == 2.0


def test_failed_job_without_a_scene_is_gauged_not_ingested(client, app):
    store = app.state.jobs.store
    store.create(JobRecord(id="feedfacefeedface", tier=2, source_format="bundle"))
    store.set_state("feedfacefeedface", RUNNING, stage="validating")
    store.set_state("feedfacefeedface", FAILED, error="ValueError: bad manifest")
    text = client.get("/metrics").text
    assert value(text, "soba_jobs", state="failed") == 1.0
    assert value(text, "soba_run_metrics_ingest_errors_total") == 0.0
    assert value(text, "soba_job_runs_total", status="failed") == 0.0


def test_metrics_501_without_prometheus_client(monkeypatch, root_scene, frontend_dir,
                                               jobs_dir, fixture_scene, caplog):
    monkeypatch.setattr(metrics_mod, "_prom", None)
    app = create_app(root_scene, frontend_dir, jobs_dir=jobs_dir, worker_mode="mock",
                     fixture_scene=fixture_scene, start_worker=False)
    c = TestClient(app)
    r = c.get("/metrics")
    assert r.status_code == 501
    assert r.json()["error"]["code"] == "metrics_unavailable"
    assert "pip install -e '.[telemetry]'" in r.json()["error"]["message"]
    # everything else keeps working, including the request log
    with caplog.at_level(logging.INFO, logger="api.http"):
        assert c.get("/scene.json").status_code == 200
    assert any(getattr(x, "fields", {}).get("route") == "/scene.json" for x in caplog.records)
    assert app.state.telemetry.refresh() == 0  # no registry, no crash


# --- request log middleware -------------------------------------------------
def test_request_log_has_job_id_route_status_and_duration(client, caplog):
    with caplog.at_level(logging.INFO, logger="api.http"):
        client.get("/api/jobs/0123456789abcdef")
        client.get("/jobs/0123456789abcdef/scene.json")
        client.get("/scene.json")
    recs = [x for x in caplog.records if getattr(x, "fields", {}).get("event") == "http"]
    assert [r.fields["route"] for r in recs] == ["/api/jobs/{id}", "/jobs/{id}/scene.json",
                                                "/scene.json"]
    assert recs[0].fields["job_id"] == "0123456789abcdef" == recs[1].fields["job_id"]
    assert "job_id" not in recs[2].fields
    assert recs[0].fields["status"] == 404 and recs[2].fields["status"] == 200
    assert recs[0].fields["method"] == "GET" and recs[0].fields["path"] == "/api/jobs/0123456789abcdef"
    assert recs[0].fields["seconds"] >= 0.0 and recs[0].levelno == logging.INFO
    assert recs[0].getMessage() == "GET /api/jobs/0123456789abcdef 404"


def test_metrics_scrapes_are_logged_at_debug_only(client, caplog):
    with caplog.at_level(logging.DEBUG, logger="api.http"):
        client.get("/metrics")
    recs = [x for x in caplog.records if getattr(x, "fields", {}).get("route") == "/metrics"]
    assert recs and all(r.levelno == logging.DEBUG for r in recs)


def test_request_log_is_json_with_soba_log_json(client, monkeypatch):
    stream = io.StringIO()
    monkeypatch.setenv(telemetry.ENV_JSON, "1")
    telemetry.configure_logging(stream=stream)
    try:
        client.get("/jobs/0123456789abcdef/eval.json")
    finally:
        logging.getLogger().handlers[:] = [
            h for h in logging.getLogger().handlers
            if not getattr(h, "_soba_telemetry_handler", False)]
    lines = [json.loads(l) for l in stream.getvalue().splitlines() if l.strip()]
    http = [l for l in lines if l.get("event") == "http"]
    assert len(http) == 1
    assert http[0]["job_id"] == "0123456789abcdef"
    assert http[0]["route"] == "/jobs/{id}/eval.json" and http[0]["status"] == 404
    assert {"ts", "level", "logger", "msg", "seconds", "method"} <= set(http[0])
    assert http[0]["logger"] == "api.http"


# --- job state transitions --------------------------------------------------
def test_job_state_transitions_are_logged(caplog):
    store = MemoryJobStore()
    with caplog.at_level(logging.INFO, logger="api.jobs"):
        store.create(JobRecord(id="0123456789abcdef", tier=2, source_format="bundle"))
        store.set_state("0123456789abcdef", RUNNING, stage="validating")
        store.set_state("0123456789abcdef", RUNNING, stage="validating")  # no-op
        store.set_state("0123456789abcdef", RUNNING, stage="assembling")
        store.set_state("0123456789abcdef", FAILED, error="RuntimeError: boom")
    recs = [x for x in caplog.records if getattr(x, "fields", {}).get("event") == "job_state"]
    assert [(r.fields["state"], r.fields["stage"]) for r in recs] == [
        ("running", "validating"), ("running", "assembling"), ("failed", None)]
    assert all(r.fields["job_id"] == "0123456789abcdef" for r in recs)
    assert recs[0].fields["prev"] == "queued" and recs[1].fields["prev"] == "running:validating"
    assert recs[2].fields["error"] == "RuntimeError: boom" and recs[2].levelno == logging.WARNING
    assert recs[2].fields["status"] == "failed"
