"""Schema contract tests — G0 gate (`ajv validate` equivalent)."""

from __future__ import annotations

import copy

import pytest
from jsonschema import Draft202012Validator, ValidationError

from scene import schema


def test_example_validates(scene_example, scene_schema_dict):
    Draft202012Validator(scene_schema_dict).validate(scene_example)


def test_module_loader_validates(scene_example):
    schema.validate(scene_example)


def test_version_is_frozen(scene_example):
    wrong = copy.deepcopy(scene_example)
    wrong["version"] = "1.1"
    with pytest.raises(ValidationError):
        schema.validate(wrong)


def test_unknown_top_level_field_rejected(scene_example):
    wrong = copy.deepcopy(scene_example)
    wrong["extra"] = "nope"
    with pytest.raises(ValidationError):
        schema.validate(wrong)


def test_object_count_cap(scene_example):
    wrong = copy.deepcopy(scene_example)
    wrong["objects"] = wrong["objects"] * 4  # 12 objects, cap is 8
    with pytest.raises(ValidationError):
        schema.validate(wrong)


def test_restitution_range(scene_example):
    wrong = copy.deepcopy(scene_example)
    wrong["objects"][0]["physics"]["restitution"] = 1.5
    with pytest.raises(ValidationError):
        schema.validate(wrong)


def test_mesh_origin_enum(scene_example):
    wrong = copy.deepcopy(scene_example)
    wrong["objects"][0]["source"]["mesh_origin"] = "midjourney"
    with pytest.raises(ValidationError):
        schema.validate(wrong)
