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
