"""telemetry.metrics: RunMetrics, stage_timer, the run_metrics.json contract."""

from __future__ import annotations

import json
import time

import jsonschema
import pytest

import telemetry
from telemetry import SCHEMA_PATH, RunMetrics, stage_timer, start_run


@pytest.fixture(autouse=True)
def _no_active_run():
    telemetry.set_current(None)
    yield
    telemetry.set_current(None)


def _schema():
    return json.loads(SCHEMA_PATH.read_text())


def test_empty_run_validates_against_schema(tmp_path):
    m = RunMetrics()
    out = m.write(tmp_path / "nested" / "dir" / "run_metrics.json")
    jsonschema.Draft202012Validator(_schema()).validate(out)
    on_disk = json.loads((tmp_path / "nested/dir/run_metrics.json").read_text())
    assert on_disk == out
    assert on_disk["schema"] == 1
    assert set(on_disk) == {"schema", "started_at", "finished_at", "run", "stages",
                            "gate", "drops", "remote", "completion", "steps"}
    assert on_disk["gate"]["counts"] == {"tsdf": 0, "completion": 0, "generative": 0}
    assert on_disk["finished_at"] >= on_disk["started_at"]
    assert not (tmp_path / "nested/dir/run_metrics.json.tmp").exists()


def test_stage_timer_records_duration_count_and_nesting():
    m = RunMetrics()
    with stage_timer("outer", metrics=m):
        for _ in range(3):
            with stage_timer("inner", metrics=m, track_id=1):
                time.sleep(0.002)
    assert m.stages["inner"]["count"] == 3
    assert m.stages["outer"]["count"] == 1
    assert m.stages["inner"]["seconds"] >= 0.006
    assert m.stages["outer"]["seconds"] >= m.stages["inner"]["seconds"]


def test_stage_timer_uses_active_run_and_records_on_exception():
    m = start_run(bundle="b", tier=2)
    assert telemetry.current() is m
    with pytest.raises(RuntimeError), stage_timer("boom"):
        raise RuntimeError("x")
    assert m.stages["boom"]["count"] == 1
    # without any run it still works, just records nowhere
    telemetry.set_current(None)
    with stage_timer("orphan") as got:
        assert got is None


def test_record_gate_stores_gate_dict_verbatim_and_counts():
    m = RunMetrics()
    m.record_gate(7, "chair", 2, {"angular_coverage_deg": 95.5,
                                  "completeness_ratio": 0.41, "strategy": "completion"})
    m.record_gate(8, "couch", 2, {"angular_coverage_deg": 40.0,
                                  "completeness_ratio": 0.10, "strategy": "generative"},
                  routed="completion", cached=True)  # --force-strategy completion
    m.record_gate(9, "tv", 2, {"angular_coverage_deg": 170.0,
                               "completeness_ratio": 0.9, "strategy": "tsdf"})
    # tier 1: gate_object's own answer carries None metrics ...
    m.record_gate(10, "bed", 1, {"angular_coverage_deg": None,
                                 "completeness_ratio": None, "strategy": "generative"})
    # ... and run_assemble.py never even calls it at tier 1 (res is None)
    m.record_gate(11, "chair", 1, None)

    assert m.gate["counts"] == {"tsdf": 1, "completion": 1, "generative": 3}
    po = {e["track_id"]: e for e in m.gate["per_object"]}
    assert po[7]["strategy"] == "completion" and po[7]["routed"] == "completion"
    assert po[7]["angular_coverage_deg"] == 95.5 and po[7]["completeness_ratio"] == 0.41
    assert po[8]["strategy"] == "generative" and po[8]["routed"] == "completion"
    assert po[8]["cached"] is True and po[7]["cached"] is False
    assert po[10]["angular_coverage_deg"] is None and po[10]["completeness_ratio"] is None
    assert po[11] == {"track_id": 11, "class": "chair", "tier": 1,
                      "strategy": "generative", "angular_coverage_deg": None,
                      "completeness_ratio": None, "routed": "generative",
                      "cached": False}
    jsonschema.Draft202012Validator(_schema()).validate(m.to_dict() | {"finished_at": m.started_at})


def test_record_gate_accepts_numpy_scalars():
    np = pytest.importorskip("numpy")
    m = RunMetrics()
    e = m.record_gate(np.int64(3), "chair", 2, {"angular_coverage_deg": np.float32(91.0),
                                                "completeness_ratio": np.float64(0.5),
                                                "strategy": "completion"})
    assert type(e["track_id"]) is int and type(e["angular_coverage_deg"]) is float
    json.dumps(m.to_dict())


def test_drops_and_remote_calls_aggregate(tmp_path):
    m = start_run(bundle="b", out=str(tmp_path), tier=3, force_strategy=None,
                  gate_only=False)
    m.record_drop("cloud_too_small", track_id=1)
    telemetry.drop("cloud_too_small", track_id=2)     # via the active run
    telemetry.drop("engine_declined")
    m.record_remote_call("runpod/gen", 12.5)
    m.record_remote_call("runpod/gen", 7.5, est_usd=0.02)
    m.record_remote_call("runpod/complete", 1.0)
    m.set_status("ok")
    out = m.write(tmp_path / "run_metrics.json")
    jsonschema.Draft202012Validator(_schema()).validate(out)
    assert out["drops"] == {"cloud_too_small": 2, "engine_declined": 1}
    assert out["remote"]["calls"] == 3 and out["remote"]["seconds"] == 21.0
    assert out["remote"]["est_usd"] == 0.02
    assert out["remote"]["by_kind"]["runpod/gen"] == {"calls": 2, "seconds": 20.0,
                                                      "est_usd": 0.02}
    assert out["remote"]["by_kind"]["runpod/complete"]["est_usd"] is None
    assert out["run"] == {"status": "ok", "error": None, "bundle": "b",
                          "out": str(tmp_path), "tier": 3, "force_strategy": None,
                          "gate_only": False}


def test_drop_without_active_run_only_logs():
    telemetry.drop("nothing_to_assemble")  # must not raise


def test_failed_status_is_written_and_schema_rejects_bad_payloads(tmp_path):
    m = start_run(bundle="b", tier=2)
    m.set_status("failed", "RuntimeError: boom")
    out = m.write(tmp_path / "run_metrics.json")
    assert out["run"]["status"] == "failed" and "boom" in out["run"]["error"]

    v = jsonschema.Draft202012Validator(_schema())
    bad = json.loads(json.dumps(out))
    bad["gate"]["counts"].pop("tsdf")
    with pytest.raises(jsonschema.ValidationError):
        v.validate(bad)
    bad = json.loads(json.dumps(out))
    bad["gate"]["per_object"].append({"track_id": 1, "class": "x", "tier": 2,
                                      "strategy": "invented"})
    with pytest.raises(jsonschema.ValidationError):
        v.validate(bad)
    bad = json.loads(json.dumps(out))
    bad["unexpected"] = 1
    with pytest.raises(jsonschema.ValidationError):
        v.validate(bad)
    with pytest.raises(jsonschema.ValidationError):
        telemetry.validate({"schema": 2})
