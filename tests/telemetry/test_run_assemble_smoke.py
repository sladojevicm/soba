"""scripts/run_assemble.py instrumentation, without open3d or data.

The script imports open3d-dependent modules at load time, so `open3d` is
stubbed in sys.modules (only when it is genuinely missing) and every pipeline
call the driver makes is replaced by a fake on the loaded module. What is
exercised for real: telemetry.configure_logging, start_run, the stage timers,
the gate events (routed / cached / tier-1 None), the drops, and the
try/except/finally that writes run_metrics.json on success AND failure.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import numpy as np
import pytest

import telemetry

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "run_assemble.py"


@pytest.fixture(scope="module")
def ra():
    before = set(sys.modules)
    if "open3d" not in sys.modules:
        try:
            import open3d  # noqa: F401
        except ImportError:
            sys.modules["open3d"] = types.ModuleType("open3d")  # import-time stub
    spec = importlib.util.spec_from_file_location("run_assemble_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    # drop the stub and everything imported against it, so later tests see
    # the box as it is (open3d missing -> ImportError / importorskip)
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)


@pytest.fixture(autouse=True)
def _reset_run():
    telemetry.set_current(None)
    yield
    telemetry.set_current(None)


# cloud sizes per track: 1 -> well observed, 2 -> poorly observed, 3 -> too small
CLOUDS = {1: np.zeros((40, 3)), 2: np.zeros((10, 3)), 3: np.zeros((2, 3))}
GATE = {
    40: {"angular_coverage_deg": 121.3, "completeness_ratio": 0.52,
         "strategy": "completion"},
    10: {"angular_coverage_deg": 33.0, "completeness_ratio": 0.11,
         "strategy": "generative"},
}


def _fake_bundle():
    return SimpleNamespace(
        read_poses=lambda: [np.eye(4)],
        intrinsics=SimpleNamespace(matrix=lambda: np.eye(3)),
        iter_frame_ids=lambda: [0],
        read_objects=lambda fid: [{"track_id": 1, "class": "chair"},
                                  {"track_id": 2, "class": "couch"},
                                  {"track_id": 3, "class": "tv"}],
        manifest=SimpleNamespace(frame_count=1),
    )


@pytest.fixture()
def fakes(ra, monkeypatch):
    calls = {"gate_object": [], "store": [], "accumulate": []}
    monkeypatch.setattr(ra.PerceptionBundle, "open", staticmethod(lambda p: _fake_bundle()))
    monkeypatch.setattr(ra.tsdf, "_all_track_ids", lambda b: [1, 2, 3])
    monkeypatch.setattr(ra.tsdf, "_object_frames", lambda b, tid, poses: ([0], [tid]))

    def accumulate(frames, K, *, voxel_size, return_keep):
        calls["accumulate"].append(frames[0])
        return CLOUDS[frames[0]], [0]

    monkeypatch.setattr(ra.observed_cloud, "accumulate_object_cloud", accumulate)

    def gate_object(cloud, cams, tier):
        calls["gate_object"].append((len(cloud), tier))
        return dict(GATE[len(cloud)])

    monkeypatch.setattr(ra.cf, "gate_object", gate_object)
    monkeypatch.setattr(ra.gate_cache, "load", lambda *a, **k: None)
    monkeypatch.setattr(ra.gate_cache, "store",
                        lambda *a, **k: calls["store"].append(k["metrics"]))
    monkeypatch.setattr(ra.generative, "make_engine",
                        lambda tier: SimpleNamespace(gen_model=None))
    return calls


def _metrics(out: Path) -> dict:
    d = json.loads((out / "run_metrics.json").read_text())
    telemetry.validate(d)
    jsonschema.Draft202012Validator(
        json.loads(telemetry.SCHEMA_PATH.read_text())).validate(d)
    return d


def test_gate_only_run_writes_validated_metrics(ra, fakes, tmp_path, capsys):
    out = tmp_path / "scene"
    ra.main(["--bundle", str(tmp_path / "office_3"), "--tier", "2",
             "--out", str(out), "--gate-only"])
    d = _metrics(out)

    assert d["run"]["status"] == "ok" and d["run"]["error"] is None
    assert d["run"]["tier"] == 2 and d["run"]["gate_only"] is True
    assert d["gate"]["counts"] == {"tsdf": 0, "completion": 1, "generative": 1}
    per = {e["track_id"]: e for e in d["gate"]["per_object"]}
    assert per[1] == {"track_id": 1, "class": "chair", "tier": 2,
                      "strategy": "completion", "angular_coverage_deg": 121.3,
                      "completeness_ratio": 0.52, "routed": "completion",
                      "cached": False}
    assert per[2]["strategy"] == "generative" and per[2]["class"] == "couch"
    assert 3 not in per and d["drops"] == {"cloud_too_small": 1}
    # the gate ran exactly twice (track 3 too small), stores got the same dicts
    assert fakes["gate_object"] == [(40, 2), (10, 2)]
    assert fakes["store"][:2] == [GATE[40], GATE[10]] and fakes["store"][2] is None
    for stage in ("run", "bundle_open", "tier_params", "engine_init",
                  "gate_cache_key", "observed_cloud", "gate"):
        assert d["stages"][stage]["count"] >= 1, stage
    assert d["stages"]["gate"]["count"] == 3 and d["stages"]["observed_cloud"]["count"] == 3
    assert d["stages"]["run"]["seconds"] >= d["stages"]["gate"]["seconds"]
    assert d["remote"] == {"calls": 0, "seconds": 0.0, "est_usd": None, "by_kind": {}}

    text = capsys.readouterr().out
    assert "routing: 0 keep(tsdf), 1 completion, 1 generative" in text
    assert "event=gate" in text and "event=stage.end" in text


def test_json_logs_carry_gate_events(ra, fakes, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv(telemetry.ENV_JSON, "1")
    out = tmp_path / "scene"
    ra.main(["--bundle", str(tmp_path / "b"), "--tier", "2", "--out", str(out),
             "--gate-only", "--no-gate-cache"])
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert all({"ts", "level", "logger", "msg"} <= set(l) for l in lines)
    gates = [l for l in lines if l.get("event") == "gate"]
    assert [(g["track_id"], g["strategy"], g["completeness_ratio"]) for g in gates] == \
        [(1, "completion", 0.52), (2, "generative", 0.11)]
    ends = {l["stage"] for l in lines if l.get("event") == "stage.end"}
    assert {"run", "gate", "bundle_open"} <= ends
    assert any(l.get("event") == "run_metrics" for l in lines)
    assert fakes["store"] == []  # --no-gate-cache


def test_forced_strategy_and_cache_hit_are_recorded(ra, fakes, tmp_path, monkeypatch):
    hits = {1: (CLOUDS[40 // 40], np.zeros((1, 3)), dict(GATE[40]))}
    monkeypatch.setattr(ra.gate_cache, "load",
                        lambda bundle, tid, key: hits.get(tid))
    out = tmp_path / "scene"
    ra.main(["--bundle", str(tmp_path / "b"), "--tier", "2", "--out", str(out),
             "--gate-only", "--force-strategy", "tsdf"])
    d = _metrics(out)
    per = {e["track_id"]: e for e in d["gate"]["per_object"]}
    assert per[1]["cached"] is True and per[2]["cached"] is False
    # the gate's own decision is kept; `routed` carries the forced one
    assert per[1]["strategy"] == "completion" and per[1]["routed"] == "tsdf"
    assert per[2]["strategy"] == "generative" and per[2]["routed"] == "tsdf"
    assert d["gate"]["counts"] == {"tsdf": 0, "completion": 1, "generative": 1}
    assert d["run"]["force_strategy"] == "tsdf"
    assert fakes["gate_object"] == [(10, 2)]           # track 1 came from the cache
    assert d["stages"]["observed_cloud"]["count"] == 2  # tracks 2 and 3 only


def test_tier1_records_unscored_generative_decisions(ra, fakes, tmp_path):
    out = tmp_path / "scene"
    ra.main(["--bundle", str(tmp_path / "b"), "--tier", "1", "--out", str(out),
             "--gate-only"])
    d = _metrics(out)
    assert fakes["gate_object"] == []  # tier 1 never scores
    assert d["gate"]["counts"] == {"tsdf": 0, "completion": 0, "generative": 2}
    for e in d["gate"]["per_object"]:
        assert e["strategy"] == "generative" and e["routed"] == "generative"
        assert e["angular_coverage_deg"] is None and e["completeness_ratio"] is None
        assert e["tier"] == 1
    assert d["drops"] == {"cloud_too_small": 1}


def test_failure_still_writes_run_metrics_and_reraises(ra, fakes, tmp_path, monkeypatch):
    def boom(cloud, cams, tier):
        raise RuntimeError("gate exploded")

    monkeypatch.setattr(ra.cf, "gate_object", boom)
    out = tmp_path / "never_created_by_assembler" / "scene"
    with pytest.raises(RuntimeError, match="gate exploded"):
        ra.main(["--bundle", str(tmp_path / "b"), "--tier", "2", "--out", str(out)])
    d = _metrics(out)
    assert d["run"]["status"] == "failed"
    assert d["run"]["error"] == "RuntimeError: gate exploded"
    assert d["stages"]["gate"]["count"] == 1 and d["stages"]["run"]["count"] == 1
    assert d["gate"]["per_object"] == []


def test_remote_call_hook_is_installed_for_the_run(ra, fakes, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(ra.generative, "remote_call_hook", None)
    orig = ra.telemetry.RunMetrics.record_remote_call
    monkeypatch.setattr(ra.telemetry.RunMetrics, "record_remote_call",
                        lambda self, kind, s, est_usd=None: (seen.append(kind),
                                                             orig(self, kind, s)))
    ra.main(["--bundle", str(tmp_path / "b"), "--tier", "2",
             "--out", str(tmp_path / "scene"), "--gate-only"])
    assert ra.generative.remote_call_hook is not None
    ra.generative.remote_call_hook("ep42", 1.5)
    assert seen == ["runpod/ep42"]


def test_help_and_parser_unchanged(ra, capsys):
    with pytest.raises(SystemExit) as ex:
        ra.build_parser().parse_args(["--help"])
    assert ex.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--bundle", "--tier", "--out", "--force-strategy", "--smooth-iters",
                 "--tracks", "--gate-stride", "--no-gate-cache", "--gate-only",
                 "--no-eval", "--reroll"):
        assert flag in out
