"""generative.remote_call_hook: RunPodEngine._runsync reports its wall time
to telemetry without any change to its signature or return value."""

from __future__ import annotations

import io
import json

import pytest

from reconstruction import generative
from telemetry import RunMetrics


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


@pytest.fixture()
def hook_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(generative, "remote_call_hook",
                        lambda endpoint, seconds: calls.append((endpoint, seconds)))
    return calls


def _patch_urlopen(monkeypatch, body: dict):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["timeout"] = timeout
        return _FakeResponse(json.dumps(body).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return seen


def test_runsync_calls_hook_with_endpoint_and_duration(monkeypatch, hook_calls):
    seen = _patch_urlopen(monkeypatch, {"status": "COMPLETED", "output": {"ok": 1}})
    eng = generative.RunPodEngine("key", gen_endpoint="ep123", timeout_s=5)
    out = eng._runsync("ep123", {"mode": "regenerate"})
    assert out == {"ok": 1}
    assert seen["url"].endswith("/ep123/runsync") and seen["timeout"] == 5
    assert len(hook_calls) == 1
    endpoint, seconds = hook_calls[0]
    assert endpoint == "ep123" and seconds >= 0.0


def test_runsync_calls_hook_even_when_the_job_fails(monkeypatch, hook_calls):
    _patch_urlopen(monkeypatch, {"status": "FAILED", "error": "cuda OOM"})
    eng = generative.RunPodEngine("key", timeout_s=5)
    with pytest.raises(RuntimeError, match="cuda OOM"):
        eng._runsync("ep", {})
    assert len(hook_calls) == 1 and hook_calls[0][0] == "ep"


def test_runsync_calls_hook_when_transport_raises(monkeypatch, hook_calls):
    def boom(req, timeout=None):
        raise OSError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    eng = generative.RunPodEngine("key", timeout_s=5)
    with pytest.raises(OSError):
        eng._runsync("ep", {})
    assert len(hook_calls) == 1


def test_no_hook_is_a_no_op(monkeypatch):
    monkeypatch.setattr(generative, "remote_call_hook", None)
    _patch_urlopen(monkeypatch, {"output": {}})
    assert generative.RunPodEngine("key", timeout_s=5)._runsync("ep", {}) == {}


def test_hook_feeds_run_metrics(monkeypatch):
    m = RunMetrics()
    monkeypatch.setattr(generative, "remote_call_hook",
                        lambda ep, s: m.record_remote_call(f"runpod/{ep}", s))
    _patch_urlopen(monkeypatch, {"output": {}})
    eng = generative.RunPodEngine("key", timeout_s=5)
    eng._runsync("gen", {})
    eng._runsync("gen", {})
    assert m.remote["calls"] == 2
    assert m.remote["by_kind"]["runpod/gen"]["calls"] == 2
