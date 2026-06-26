"""Schema contract tests for scene.json v2.0 (Contract 3, freeze gate)."""

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
    wrong["version"] = "2.1"
    with pytest.raises(ValidationError):
        schema.validate(wrong)


def test_unknown_top_level_field_rejected(scene_example):
    wrong = copy.deepcopy(scene_example)
    wrong["extra"] = "nope"
    with pytest.raises(ValidationError):
        schema.validate(wrong)


def test_object_count_cap_is_twelve(scene_example):
    base = scene_example["objects"][0]
    ok = copy.deepcopy(scene_example)
    ok["objects"] = [
        {**copy.deepcopy(base), "id": f"chair_{i:02d}"} for i in range(12)
    ]
    schema.validate(ok)  # exactly 12 is allowed

    wrong = copy.deepcopy(ok)
    wrong["objects"].append({**copy.deepcopy(base), "id": "chair_99"})  # 13
    with pytest.raises(ValidationError):
        schema.validate(wrong)


@pytest.mark.parametrize("bad_id", ["chair_1", "chair_147", "Chair_01", "chair01", "chair_01x"])
def test_id_regex_rejects_bad_ids(scene_example, bad_id):
    wrong = copy.deepcopy(scene_example)
    wrong["objects"][0]["id"] = bad_id
    with pytest.raises(ValidationError):
        schema.validate(wrong)


@pytest.mark.parametrize("good_id", ["chair_01", "dining_table_11", "sports_ball_00"])
def test_id_regex_accepts_slug_oid(scene_example, good_id):
    ok = copy.deepcopy(scene_example)
    ok["objects"][0]["id"] = good_id
    schema.validate(ok)


def test_friction_upper_bound_is_two(scene_example):
    ok = copy.deepcopy(scene_example)
    ok["objects"][0]["physics"]["friction"] = 2.0
    schema.validate(ok)

    wrong = copy.deepcopy(scene_example)
    wrong["objects"][0]["physics"]["friction"] = 2.1
    with pytest.raises(ValidationError):
        schema.validate(wrong)


def test_restitution_range(scene_example):
    wrong = copy.deepcopy(scene_example)
    wrong["objects"][0]["physics"]["restitution"] = 1.5
    with pytest.raises(ValidationError):
        schema.validate(wrong)


def test_mass_must_be_positive(scene_example):
    wrong = copy.deepcopy(scene_example)
    wrong["objects"][0]["physics"]["mass_kg"] = 0
    with pytest.raises(ValidationError):
        schema.validate(wrong)


def test_geometry_source_enum(scene_example):
    wrong = copy.deepcopy(scene_example)
    wrong["objects"][0]["source"]["geometry_source"] = "midjourney"
    with pytest.raises(ValidationError):
        schema.validate(wrong)


def test_tsdf_object_must_report_na_provenance(scene_example):
    # fix Y1: a tsdf object skips Step 7 and cannot claim ICP provenance.
    wrong = copy.deepcopy(scene_example)
    obj = wrong["objects"][0]
    obj["source"]["geometry_source"] = "tsdf"
    obj["source"]["alignment_method"] = "fpfh_icp"  # invalid for tsdf
    obj["source"]["scale_method"] = "n/a"
    with pytest.raises(ValidationError):
        schema.validate(wrong)


def test_hulls_collider_requires_nonempty_hull_paths(scene_example):
    wrong = copy.deepcopy(scene_example)
    wrong["objects"][0]["collider"] = {
        "shape": "hulls",
        "convex_decomposition": True,
        "hull_paths": [],
    }
    with pytest.raises(ValidationError):
        schema.validate(wrong)


def test_box_collider_requires_half_extents_and_forbids_hulls(scene_example):
    ok = copy.deepcopy(scene_example)
    ok["objects"][0]["collider"] = {"shape": "box", "half_extents": [0.2, 0.4, 0.2]}
    schema.validate(ok)

    wrong = copy.deepcopy(ok)
    wrong["objects"][0]["collider"]["hull_paths"] = ["hulls/chair_01_0.glb"]
    with pytest.raises(ValidationError):
        schema.validate(wrong)
