"""Completion-method visibility: run_metrics.json must say which completer ran."""

from __future__ import annotations

import logging

import pytest

import telemetry
from telemetry import RunMetrics, validate


def test_record_completion_counts_and_flags_fallback(caplog):
    m = RunMetrics(bundle="b", out="o", tier=2)
    with caplog.at_level(logging.INFO, logger="telemetry"):
        m.record_completion(3, "chair", "patchcomplete", engine="LocalGpuEngine")
        m.record_completion(5, "couch", "poisson_fallback", engine="LocalGpuEngine",
                            reason="engine_declined", fallback=True)
    d = m.to_dict()
    assert d["completion"]["counts"] == {"patchcomplete": 1, "poisson_fallback": 1}
    per = d["completion"]["per_object"]
    assert per[0]["fallback"] is False and per[1]["fallback"] is True
    assert per[1]["reason"] == "engine_declined"
    validate(d if d["finished_at"] else {**d, "finished_at": d["started_at"]})
    # the fallback is logged at WARNING, the learned completion at INFO
    assert max(r.levelno for r in caplog.records) == logging.WARNING


def test_completion_helper_records_on_active_run_and_only_logs_otherwise(caplog):
    telemetry.set_current(None)
    with caplog.at_level(logging.INFO, logger="telemetry"):
        telemetry.completion(1, "table", "poisson_local", fallback=True)
    assert any(getattr(r, "fields", {}).get("event") == "completion" for r in caplog.records)
    m = telemetry.start_run(bundle="b", out="o", tier=2)
    try:
        telemetry.completion(1, "table", "patchcomplete")
        assert m.completion["counts"] == {"patchcomplete": 1}
    finally:
        telemetry.set_current(None)


def test_schema_rejects_malformed_completion():
    m = RunMetrics(bundle="b", out="o", tier=2)
    d = {**m.to_dict(), "finished_at": m.started_at}
    d["completion"] = {"counts": {"patchcomplete": "one"}, "per_object": []}
    with pytest.raises(Exception):
        validate(d)
