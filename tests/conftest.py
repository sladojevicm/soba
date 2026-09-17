"""Repo-wide pytest hooks.

`gpu`-marked tests need torch and/or a model checkout that only the GPU machine
has (see the marker text in pyproject.toml). CI (`.github/workflows/ci.yml`)
sets SOBA_SKIP_GPU_TESTS=1 to skip exactly those; the finding behind this is
docs/ci-probe.md. Unset locally, nothing is skipped.
"""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("SOBA_SKIP_GPU_TESTS") != "1":
        return
    skip = pytest.mark.skip(reason="gpu-marked test skipped (SOBA_SKIP_GPU_TESTS=1)")
    for item in items:
        if "gpu" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _no_live_anthropic_key(monkeypatch):
    """The suite never calls the live Claude API.

    vlm.infer() uses the Claude backend whenever ANTHROPIC_API_KEY is set, so a
    key in the shell (the pod, 2026-09-17 run 8) turned assemble()/infer() tests
    into real, billed API calls and broke the lookup-fallback assertion. Tests
    that need a key set their own fake one with monkeypatch.setenv.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
