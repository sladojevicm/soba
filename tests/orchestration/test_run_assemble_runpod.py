"""scripts/run_assemble.py survives RunPod failures (no open3d, no data).

Same harness as tests/telemetry/test_run_assemble_smoke.py: open3d stubbed in
sys.modules, the pipeline calls replaced on the loaded module. Tier 1 routes
every object to the generative band, so the engine's regenerate() is reached
for every usable track and what it raises / returns decides the run.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import telemetry
from reconstruction import runpod_policy

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "run_assemble.py"


@pytest.fixture(scope="module")
def ra():
    before = set(sys.modules)
    if "open3d" not in sys.modules:
        try:
            import open3d  # noqa: F401
        except ImportError:
            sys.modules["open3d"] = types.ModuleType("open3d")
    spec = importlib.util.spec_from_file_location("run_assemble_runpod_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)


@pytest.fixture(autouse=True)
def _reset():
    telemetry.set_current(None)
    runpod_policy.reset_breakers()
    yield
    telemetry.set_current(None)
    runpod_policy.reset_breakers()


CLOUDS = {1: np.zeros((40, 3)), 2: np.zeros((10, 3)), 3: np.zeros((2, 3))}


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
def pipeline(ra, monkeypatch, tmp_path):
    """Everything up to the engine call faked; `engine` is what make_engine returns."""
    monkeypatch.setattr(ra.PerceptionBundle, "open", staticmethod(lambda p: _fake_bundle()))
    monkeypatch.setattr(ra.tsdf, "_all_track_ids", lambda b: [1, 2, 3])
    monkeypatch.setattr(ra.tsdf, "_object_frames", lambda b, tid, poses: ([0], [tid]))
    monkeypatch.setattr(ra.observed_cloud, "accumulate_object_cloud",
                        lambda frames, K, *, voxel_size, return_keep: (CLOUDS[frames[0]], [0]))
    monkeypatch.setattr(ra.gate_cache, "load", lambda *a, **k: None)
    monkeypatch.setattr(ra.gate_cache, "store", lambda *a, **k: None)
    crop = tmp_path / "crop.jpg"
    crop.write_bytes(b"\xff\xd8\xff\xd9")
    monkeypatch.setattr(ra, "_crop_path", lambda b, tid: crop)
    holder = {}
    monkeypatch.setattr(ra.generative, "make_engine", lambda tier: holder["engine"])
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    holder["bundle"] = bundle
    return holder


def _run(ra, pipeline, tmp_path, engine):
    pipeline["engine"] = engine
    out = tmp_path / "scene"
    ra.main(["--bundle", str(pipeline["bundle"]), "--tier", "1", "--out", str(out),
             "--no-gate-cache", "--no-eval"])
    d = json.loads((out / "run_metrics.json").read_text())
    telemetry.validate(d)
    return d


class _Raising:
    gen_model = "triposg"

    def __init__(self, exc):
        self.exc, self.calls = exc, 0

    def regenerate(self, **kw):
        self.calls += 1
        raise self.exc


def test_runpod_failure_drops_the_object_and_the_run_continues(ra, pipeline, tmp_path):
    engine = _Raising(ra.generative.RunPodTransient("HTTP 503 after 3 attempts"))
    d = _run(ra, pipeline, tmp_path, engine)
    assert d["run"]["status"] == "ok" and d["run"]["error"] is None
    assert engine.calls == 2  # tracks 1 and 2; track 3's cloud is too small
    assert d["drops"] == {"cloud_too_small": 1, "runpod_failed": 2,
                          "nothing_to_assemble": 1}
    assert d["stages"]["generate_object"]["count"] == 2
    # a transient failure is never cached as a rejection
    assert list((pipeline["bundle"] / ".gen_cache").glob("*.json")) == []


def test_failed_job_reply_is_also_a_per_object_drop(ra, pipeline, tmp_path):
    engine = _Raising(ra.generative.RunPodJobFailed("RunPod job failed: cuda OOM"))
    d = _run(ra, pipeline, tmp_path, engine)
    assert d["run"]["status"] == "ok" and d["drops"]["runpod_failed"] == 2


def test_config_error_still_aborts_the_run(ra, pipeline, tmp_path):
    engine = _Raising(ra.generative.RunPodConfigError("SOBA_RUNPOD_URL is not https"))
    with pytest.raises(ra.generative.RunPodConfigError):
        _run(ra, pipeline, tmp_path, engine)
    d = json.loads((tmp_path / "scene" / "run_metrics.json").read_text())
    assert d["run"]["status"] == "failed" and "not https" in d["run"]["error"]
    assert engine.calls == 1 and "runpod_failed" not in d["drops"]


def test_engine_side_drops_reach_run_metrics(ra, pipeline, tmp_path):
    """Breaker / budget / kill switch: the engine returns None and records the
    reason through telemetry.drop; run_assemble adds its own engine_declined."""

    class _Declining:
        gen_model = "triposg"

        def regenerate(self, **kw):
            telemetry.drop(runpod_policy.DROP_BREAKER_OPEN, band="generative")

    d = _run(ra, pipeline, tmp_path, _Declining())
    assert d["run"]["status"] == "ok"
    assert d["drops"]["runpod_breaker_open"] == 2 and d["drops"]["engine_declined"] == 2


def test_remote_calls_are_priced_from_config(ra, pipeline, tmp_path):
    price = runpod_policy.load_runpod_config()["price"]["usd_per_gpu_second"]

    class _Reporting:
        gen_model = "triposg"

        def regenerate(self, **kw):
            ra.generative.remote_call_hook("gen", 10.0)
            raise ra.generative.RunPodJobFailed("no mesh")

    d = _run(ra, pipeline, tmp_path, _Reporting())
    assert d["remote"]["calls"] == 2 and d["remote"]["seconds"] == 20.0
    assert d["remote"]["est_usd"] == pytest.approx(20.0 * price)
    assert d["remote"]["by_kind"]["runpod/gen"]["est_usd"] == pytest.approx(20.0 * price)
