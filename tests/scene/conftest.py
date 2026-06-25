"""Shared fixtures for scene-contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SPEC_DIR = Path(__file__).resolve().parents[2] / "spec"


@pytest.fixture()
def scene_example() -> dict:
    with (SPEC_DIR / "scene.example.json").open() as fh:
        return json.load(fh)


@pytest.fixture()
def scene_schema_dict() -> dict:
    with (SPEC_DIR / "scene.schema.json").open() as fh:
        return json.load(fh)
