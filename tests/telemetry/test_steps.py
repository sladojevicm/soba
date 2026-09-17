"""Geometry-step visibility: which implementation ran, per object, and one strict flag."""

from __future__ import annotations

import pytest

import telemetry
from telemetry import RunMetrics, validate


def test_record_step_counts_per_step_and_validates():
    m = RunMetrics(bundle="b", out="o", tier=2)
    m.record_step("fusion", 3, "chair", "fused", completion_source="engine")
    m.record_step("fusion", 5, "couch", "plain_finalize_fallback", fallback=True,
                  reason="fusion raised ValueError: x")
    m.record_step("collider", 3, "chair", "coacd", parts=8)
    d = {**m.to_dict(), "finished_at": m.started_at}
    assert d["steps"]["fusion"]["counts"] == {"fused": 1, "plain_finalize_fallback": 1}
    assert d["steps"]["collider"]["per_object"][0]["parts"] == 8
    assert d["steps"]["fusion"]["per_object"][1]["fallback"] is True
    validate(d)


@pytest.mark.parametrize("env,kind,expected", [
    ({}, "GEOMETRY", False),
    ({"SOBA_STRICT": "1"}, "GEOMETRY", True),
    ({"SOBA_STRICT": "1"}, None, True),
    ({"SOBA_GEOMETRY_STRICT": "1"}, "GEOMETRY", True),
    ({"SOBA_GEOMETRY_STRICT": "1"}, "COMPLETION", False),
    ({"SOBA_COMPLETION_STRICT": "1"}, "COMPLETION", True),
])
def test_strict_umbrella_and_per_kind(monkeypatch, env, kind, expected):
    for k in ("SOBA_STRICT", "SOBA_GEOMETRY_STRICT", "SOBA_COMPLETION_STRICT",
              "SOBA_GENERATION_STRICT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert telemetry.strict(kind) is expected
