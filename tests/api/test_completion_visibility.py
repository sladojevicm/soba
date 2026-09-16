"""A job cannot claim tier 2 while silently running Poisson: the completion
method reaches /metrics and GET /api/jobs/{id}."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from _fixtures import post_archive, run_worker

pytest.importorskip("prometheus_client")
FIXTURE = Path(__file__).with_name("run_metrics_fixture.json")


def _value(text: str, name: str, **labels) -> float | None:
    from prometheus_client.parser import text_string_to_metric_families
    for fam in text_string_to_metric_families(text):
        for s in fam.samples:
            if s.name == name and all(s.labels.get(k) == v for k, v in labels.items()):
                return s.value
    return None


def test_completion_counts_reach_metrics_and_job_record(client, app, bundle_zip, fixture_scene):
    shutil.copy(FIXTURE, fixture_scene / "run_metrics.json")
    r = post_archive(client, bundle_zip)
    jid = r.json()["id"]
    run_worker(app)
    text = client.get("/metrics").text
    assert _value(text, "soba_completion_total", method="patchcomplete") == 1.0
    assert _value(text, "soba_completion_total", method="poisson_fallback") == 1.0
    job = client.get(f"/api/jobs/{jid}").json()
    assert job["run"]["completion"] == {"patchcomplete": 1, "poisson_fallback": 1}
    assert job["run"]["gate"] == {"tsdf": 0, "completion": 2, "generative": 1}
    assert job["run"]["drops"] == {"cloud_too_small": 1}
    assert job["run"]["status"] == "ok"


def test_job_without_run_metrics_has_no_run_field(client, app, bundle_zip):
    r = post_archive(client, bundle_zip)
    jid = r.json()["id"]
    run_worker(app)
    job = client.get(f"/api/jobs/{jid}").json()
    assert "run" not in job
