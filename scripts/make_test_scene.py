#!/usr/bin/env python
"""Build a tiny synthetic scene for the browser verifier (Phase-0 fixture).

The real assembled scenes live on the RunPod volume, not this machine, so this
script fabricates a minimal but fully schema-valid `out/scene_test/` from
primitive trimesh geometry:

  * table_01  — box mesh, "hulls" collider (its own convex hull, the CoACD
                density-0 code path)
  * crate_01  — box mesh, "hulls" collider, 2 hull parts
  * ball_01   — icosphere mesh, "box" collider (the tier-1 AABB code path)

Everything the viewer contract touches is exercised: both collider shapes,
per-object mass/friction/restitution, camera_pose, ground.y, SSE replay.

Usage:  .venv/bin/python scripts/make_test_scene.py  [--out out/scene_test]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import trimesh

REPO = Path(__file__).resolve().parents[1]


def export_glb(mesh: trimesh.Trimesh, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(mesh.export(file_type="glb"))


def hull_paths_for(oid: str, parts: list[trimesh.Trimesh], objdir: Path) -> list[str]:
    paths = []
    for i, part in enumerate(parts):
        export_glb(part.convex_hull, objdir / "hulls" / f"{oid}_{i}.glb")
        paths.append(f"hulls/{oid}_{i}.glb")
    return paths


def obj_entry(oid, cls, translation, collider, mass, material,
              friction=0.5, restitution=0.2):
    return {
        "id": oid,
        "class": cls,
        "mesh": f"objects/{oid}/mesh.glb",
        "transform": {"translation": translation,
                      "rotation_quat": [0.0, 0.0, 0.0, 1.0], "scale": 1.0},
        "collider": collider,
        "physics": {"mass_kg": mass, "friction": friction,
                    "restitution": restitution, "is_rigid": True},
        "material_class": material,
        "source": {"geometry_source": "tsdf", "alignment_method": "n/a",
                   "scale_method": "n/a", "physics_origin": "lookup",
                   "vlm_reasoning": ""},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/scene_test")
    args = ap.parse_args()
    out = (REPO / args.out).resolve()

    # Objects float a few mm above the ground. EXACT flush contact (y == half
    # height, identity rotation) is a degenerate coplanar-face configuration:
    # Rapier's narrow phase can then emit a manifold with a HORIZONTAL normal
    # and tens of metres of bogus "penetration" against the 100 m ground
    # cuboid, and the position solver slides the body to the ground's corner.
    # Real reconstructed scenes always carry noise, so only this synthetic
    # fixture ever hits the degenerate case.
    EPS = 0.005

    objects = []

    # table_01: one box, single-hull collider
    oid = "table_01"
    d = out / "objects" / oid
    table = trimesh.creation.box(extents=[1.2, 0.72, 0.8])
    export_glb(table, d / "mesh.glb")
    objects.append(obj_entry(
        oid, "table", [0.0, 0.36 + EPS, 0.0],
        {"shape": "hulls", "convex_decomposition": True,
         "hull_paths": hull_paths_for(oid, [table], d)},
        mass=18.5, material="wood", friction=0.6))

    # crate_01: box mesh, TWO hull parts (multi-hull path)
    oid = "crate_01"
    d = out / "objects" / oid
    crate = trimesh.creation.box(extents=[0.5, 0.5, 0.5])
    export_glb(crate, d / "mesh.glb")
    lower = trimesh.creation.box(extents=[0.5, 0.25, 0.5],
                                 transform=trimesh.transformations.translation_matrix([0, -0.125, 0]))
    upper = trimesh.creation.box(extents=[0.5, 0.25, 0.5],
                                 transform=trimesh.transformations.translation_matrix([0, 0.125, 0]))
    objects.append(obj_entry(
        oid, "crate", [1.2, 0.25 + EPS, 0.4],
        {"shape": "hulls", "convex_decomposition": True,
         "hull_paths": hull_paths_for(oid, [lower, upper], d)},
        mass=7.0, material="wood", friction=0.55))

    # ball_01: icosphere, tier-1 AABB box collider
    oid = "ball_01"
    d = out / "objects" / oid
    r = 0.18
    ball = trimesh.creation.icosphere(subdivisions=3, radius=r)
    export_glb(ball, d / "mesh.glb")
    objects.append(obj_entry(
        oid, "ball", [-0.9, r + EPS, 0.5],
        {"shape": "box", "half_extents": [r, r, r]},
        mass=0.6, material="rubber", restitution=0.6))

    scene = {
        "version": "2.0",
        "world": {"gravity": [0.0, -9.81, 0.0], "up_axis": "y", "unit": "meters"},
        "ground": {"type": "plane", "normal": [0.0, 1.0, 0.0], "y": 0.0,
                   "material": {"friction": 0.85, "restitution": 0.1}},
        "objects": objects,
        "camera_pose": {"translation": [2.6, 1.8, 2.6],
                        "rotation_quat": [0.0, 0.0, 0.0, 1.0], "scale": 1.0},
    }
    (out / "scene.json").write_text(json.dumps(scene, indent=2))

    # validate against the frozen contract before declaring success
    import jsonschema
    schema = json.loads((REPO / "spec" / "scene.schema.json").read_text())
    jsonschema.validate(scene, schema)
    print(f"wrote {out} ({len(objects)} objects), schema-valid")


if __name__ == "__main__":
    main()
