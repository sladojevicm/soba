"""Schema loading and validation for `scene.json`.

Spec lives at `spec/scene.schema.json` (frozen v2.0, Contract 3 of
PLAN_FINAL_FINAL). This module is the VALIDATOR ONLY (Build Order Phase 1,
fix Z-L): it loads the schema and validates a scene dict against it. Phase 9
(the assembler) imports this same module — it is not re-created there.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

SPEC_PATH = Path(__file__).resolve().parents[2] / "spec" / "scene.schema.json"


@lru_cache(maxsize=1)
def load_schema(path: Path | None = None) -> dict:
    target = Path(path) if path else SPEC_PATH
    with target.open() as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(load_schema())


def validate(scene: dict) -> None:
    """Raise jsonschema.ValidationError on first invalid field."""
    _validator().validate(scene)


def iter_errors(scene: dict):
    return _validator().iter_errors(scene)
