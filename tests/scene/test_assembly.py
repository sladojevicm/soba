"""Phase 9 scene-assembly tests (Step 8-10).

Config tables and mass math are pure; the assembler is exercised on a small
watertight box object and the result is validated against the frozen schema.
"""

from __future__ import annotations

import numpy as np
import pytest

from scene import assembler, ground, lookup, mass, schema, vlm


# --- lookup tables ------------------------------------------------------
def test_lookup_density_and_solidity_fall_back():
    assert lookup.density("wood") == 700
    assert lookup.density("nonsense") == lookup.density("unknown")  # fallback
    assert lookup.solidity("couch") == 0.20
    assert lookup.solidity("nonsense") == lookup.solidity("default")


def test_physics_lookup_known_and_default():
    p = lookup.physics_lookup("chair")
    assert p["material"] == "wood" and p["is_rigid"] is True
    d = lookup.physics_lookup("flux capacitor")  # unknown -> default
    assert d["material"] == "unknown"


# --- mass ---------------------------------------------------------------
def test_mass_formula_uses_density_and_solidity():
    # 1 m^3 of wood (700) at chair solidity (0.25) -> 175 kg
    m = mass.mass_kg(1.0, "wood", "chair")
    assert abs(m - 700 * 0.25) < 1e-6


def test_volume_watertight_vs_hull_fallback():
    import open3d as o3d

    box = o3d.geometry.TriangleMesh.create_box(1.0, 1.0, 1.0)
    box.compute_vertex_normals()
    vol, watertight = mass.volume_m3(box)
    assert watertight and abs(vol - 1.0) < 1e-6
    # remove a face -> open shell -> hull fallback still returns a positive volume
    tris = np.asarray(box.triangles)[:-2]
    box.triangles = o3d.utility.Vector3iVector(tris)
    vol2, wt2 = mass.volume_m3(box)
    assert not wt2 and vol2 > 0


def test_generative_volume_uses_enclosed_not_hull():
    """A generated "table" (top + one leg) must be massed by its ENCLOSED volume,
    not its convex hull (which fills all the air under the top — the 206 kg bug)."""
    import open3d as o3d

    top = o3d.geometry.TriangleMesh.create_box(1.6, 0.05, 0.9)
    top.translate((0.0, 0.70, 0.0))
    leg = o3d.geometry.TriangleMesh.create_box(0.08, 0.70, 0.08)
    table = top + leg  # two watertight components; signed volume is still sane
    enc = 1.6 * 0.05 * 0.9 + 0.08 * 0.70 * 0.08
    hull = mass.hull_volume(table)
    vol, used_enclosed = mass.generative_volume(table)
    assert used_enclosed
    assert vol < hull / 2  # nowhere near the hull
    # enclosed (~0.076) is below min_hull_ratio * hull -> clamped UP to the floor
    assert vol == pytest.approx(max(enc, 0.15 * hull), rel=1e-3)


def test_generative_volume_clamps_solid_blob_to_hull_band():
    """A solid-blob generation (enclosed == hull) is clamped DOWN to
    max_hull_ratio so class solidity doesn't compound into an overshoot."""
    import open3d as o3d

    box = o3d.geometry.TriangleMesh.create_box(1.0, 1.0, 1.0)
    vol, used_enclosed = mass.generative_volume(box)
    assert used_enclosed
    assert vol == pytest.approx(0.35, rel=1e-3)  # max_hull_ratio * 1 m^3


def test_generative_volume_hull_fallback_when_not_sane():
    """No sane enclosed volume (open shell with a garbage signed volume) ->
    hull volume, the pre-fix behaviour."""
    import open3d as o3d

    box = o3d.geometry.TriangleMesh.create_box(1.0, 1.0, 1.0)
    # keep only 2 faces -> wildly non-watertight; far from the origin the
    # signed-tetrahedron sum of an open patch is garbage (way beyond the hull)
    box.triangles = o3d.utility.Vector3iVector(np.asarray(box.triangles)[:2])
    box.translate((100.0, 100.0, 100.0))
    vol, used_enclosed = mass.generative_volume(box)
    assert not used_enclosed
    assert vol == pytest.approx(mass.hull_volume(box), rel=1e-6)


# --- ground -------------------------------------------------------------
def test_ground_y_from_clouds():
    a = np.array([[0, 0.50, 0], [0, 1.0, 0]], dtype=float)
    b = np.array([[0, 0.10, 0]] * 50, dtype=float)
    gy = ground.ground_y([a, b], offset_m=0.02)
    assert abs(gy - 0.08) < 1e-6  # ~min(0.10) - 0.02


# --- slug + id ----------------------------------------------------------
def test_slug_multiword_class():
    assert assembler.slug("dining table") == "dining_table"
    assert assembler.slug("sports ball") == "sports_ball"


# --- full assemble validates against the schema -------------------------
def _box_object(track_id, coco_class, at):
    import open3d as o3d

    box = o3d.geometry.TriangleMesh.create_box(0.5, 0.8, 0.5)
    box.translate(at)
    box.compute_vertex_normals()
    cloud = np.asarray(box.vertices, dtype=float)
    return assembler.ObjectInput(track_id, coco_class, box, cloud)


def test_assemble_produces_schema_valid_scene(tmp_path):
    objs = [
        _box_object(7, "dining table", (1.0, 0.0, 2.0)),
        _box_object(3, "chair", (-1.0, 0.0, 0.5)),
    ]
    scene = assembler.assemble(objs, [np.eye(4)], tmp_path / "scene")
    schema.validate(scene)  # raises if invalid
    assert {o["id"] for o in scene["objects"]} == {"chair_00", "dining_table_01"}
    for o in scene["objects"]:
        assert o["source"]["geometry_source"] == "tsdf"
        assert o["source"]["alignment_method"] == "n/a"  # Y1: tsdf -> n/a
        assert o["physics"]["mass_kg"] > 0
        assert o["collider"]["shape"] == "hulls"
        assert len(o["collider"]["hull_paths"]) >= 1
    assert (tmp_path / "scene" / "scene.json").exists()
    # ids assigned by sorted track_id: 3->chair_00, 7->dining_table_01
    by_id = {o["id"]: o for o in scene["objects"]}
    assert by_id["chair_00"]["class"] == "chair"


def test_assemble_box_collider_tier1(tmp_path):
    """Tier 1 (collider="box"): AABB half_extents, no CoACD, schema-valid."""
    objs = [_box_object(3, "chair", (-1.0, 0.0, 0.5))]
    scene = assembler.assemble(objs, [np.eye(4)], tmp_path / "scene",
                               collider="box")
    schema.validate(scene)
    (o,) = scene["objects"]
    col = o["collider"]
    assert col["shape"] == "box" and "hull_paths" not in col
    hx, hy, hz = col["half_extents"]
    # source mesh is 0.5 x 0.8 x 0.5; repair keeps the AABB (loose bounds)
    assert 0.2 < hx < 0.3 and 0.35 < hy < 0.45 and 0.2 < hz < 0.3
    # no hull GLBs written for a box collider
    assert not list((tmp_path / "scene" / "objects" / o["id"] / "hulls").glob("*"))


def test_assemble_caps_at_12_objects(tmp_path):
    objs = [_box_object(i, "chair", (i * 0.6, 0.0, 0.0)) for i in range(15)]
    scene = assembler.assemble(objs, [np.eye(4)], tmp_path / "scene")
    assert len(scene["objects"]) == 12  # Contract 3 cap (Z8)


def test_vlm_falls_back_to_lookup_without_backend():
    out = vlm.infer(["chair", "couch"])
    assert [p.origin for p in out] == ["lookup", "lookup"]
    assert out[0].material == "wood" and out[1].material == "fabric"


def _entry(oid, x, z, hx, hz, mass_kg):
    return {"id": oid, "_half_extents": [hx, 0.4, hz], "physics": {"mass_kg": mass_kg},
            "transform": {"translation": [x, 0.0, z]}}


def test_deoverlap_pushes_lighter_object_apart():
    heavy = _entry("desk", 0.0, 0.0, 0.8, 0.5, 40.0)
    light = _entry("chair", 0.3, 0.1, 0.4, 0.4, 5.0)   # deep inside the desk
    n = assembler.deoverlap([heavy, light], max_shift=1.0)
    assert n > 0
    assert heavy["transform"]["translation"] == [0.0, 0.0, 0.0]  # anchor stays
    dx = abs(light["transform"]["translation"][0] - 0.0)
    dz = abs(light["transform"]["translation"][2] - 0.0)
    # separated on at least one axis (to within the 5 cm tuck tolerance)
    assert dx >= 0.8 + 0.4 - 0.05 - 1e-6 or dz >= 0.5 + 0.4 - 0.05 - 1e-6


def test_deoverlap_leaves_separated_and_tucked_objects_alone():
    a = _entry("a", 0.0, 0.0, 0.5, 0.5, 10.0)
    b = _entry("b", 2.0, 0.0, 0.5, 0.5, 10.0)          # clearly apart
    c = _entry("c", 0.0, 0.97, 0.5, 0.5, 1.0)          # grazing within tol
    assert assembler.deoverlap([a, b, c]) == 0
    assert c["transform"]["translation"] == [0.0, 0.0, 0.97]


def test_deoverlap_caps_displacement():
    heavy = _entry("desk", 0.0, 0.0, 2.0, 2.0, 40.0)
    light = _entry("chair", 0.0, 0.0, 2.0, 2.0, 5.0)   # hopeless full overlap
    assembler.deoverlap([heavy, light], max_shift=0.5)
    t = light["transform"]["translation"]
    assert abs(t[0]) + abs(t[2]) <= 0.5 + 1e-6          # nothing teleports


def test_mass_fill_fraction_overrides_class_solidity():
    # 1 m^3 metal "bowl": class default solidity 0.5 -> 3900 kg; the VLM's
    # per-object fill_fraction 0.03 -> 234 kg (the BENCHMARK bowl fix).
    heavy = mass.mass_kg(1.0, "metal", "bowl")
    light = mass.mass_kg(1.0, "metal", "bowl", fill_fraction=0.03)
    assert abs(heavy - 7800 * 0.5) < 1e-6
    assert abs(light - 7800 * 0.03) < 1e-6
    # None -> unchanged legacy behaviour; silly values clamp to [0.005, 1]
    assert mass.mass_kg(1.0, "metal", "bowl", fill_fraction=None) == heavy
    assert mass.mass_kg(1.0, "metal", "bowl", fill_fraction=99.0) == pytest.approx(7800.0)
