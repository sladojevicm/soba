"""RunPod serverless handler (deploy/runpod/generative_handler.py) — dispatch
and error-wrapping, exercised WITHOUT a GPU or model weights. The model paths
are covered by the local-GPU engine tests; here we only pin the request/response
contract and the failure envelope RunPodEngine relies on."""
import os
import sys

import pytest

# the handler lives outside the src/ package tree
_HANDLER_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "deploy", "runpod")
sys.path.insert(0, os.path.abspath(_HANDLER_DIR))

H = pytest.importorskip("generative_handler")


def test_unknown_mode_raises_in_run():
    with pytest.raises(ValueError):
        H.run({"mode": "nonsense"})


def test_regenerate_without_image_raises():
    with pytest.raises(ValueError):
        H.run({"mode": "regenerate", "model": "triposg"})


def test_complete_without_cloud_raises():
    with pytest.raises(ValueError):
        H.run({"mode": "complete", "model": "pointr"})


def test_handler_wraps_errors_as_job_error():
    # handler() must never raise — RunPodEngine._runsync keys off "error"
    out = H.handler({"input": {"mode": "nonsense"}})
    assert "error" in out and "ValueError" in out["error"]
