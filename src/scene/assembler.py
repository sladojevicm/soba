"""Scene assembly — Step 10 (Phase 9).

Turns finished per-object geometry into the frozen `scene.json` contract and the
on-disk object folders the browser will fetch. Validates the result against the
Phase-1 schema (scene/schema.py) before writing — a scene that doesn't match the
contract is never emitted.

SCOPE (current build): assembles "tsdf"-routed objects only. Generative objects
are deferred (no GPU yet) — they have no mesh and are simply omitted, so scenes
are sparse until the generative stage exists (see STATUS "operating constraint").

Per object (fix Z1 DERIVED/RENAMED, Z-H placement, Z-F/Z-O collider):
  * id   = slug(class)_oid, oid a dense 2-digit index over surviving objects (T1/D1)
  * mesh = recentred to its own AABB centre so the rigid body rotates about its
           centre; transform.translation carries the world position, with the
           bottom snapped to ground_y only when the object actually sits on the
           floor (within 0.15 m, fix Z-H). rotation_quat is identity (a TSDF mesh
           is already world-axis-aligned), scale 1.0 (baked in).
  * collider = CoACD convex hulls (fix Z-O)
  * physics  = mass from geometry (mass.py) + material/friction/restitution from
               the lookup table (vlm.py fallback; physics_origin "lookup")
  * source.geometry_source = "tsdf" -> alignment_method/scale_method = "n/a" (Y1)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import decomp, exporter_gltf, ground, lookup, mass, schema, vlm

WORLD_GRAVITY = [0.0, -9.81, 0.0]
GROUND_SNAP_BAND_M = 0.15


@dataclass
class ObjectInput:
    track_id: int
    coco_class: str
    mesh: object          # Open3D legacy TriangleMesh (world-placed, from TSDF)
    cloud: np.ndarray     # (N,3) observed world points
    # Three-way routing (confidence.route). "tsdf" keeps the mesh as-is (light
    # repair); "completion" fills the gaps via the engine; "generative" objects
    # arrive with the mesh ALREADY regenerated+aligned (mesh = RegenResult.mesh).
    strategy: str = "tsdf"
    crop_path: object = None   # objects/{id}/crop.jpg — the completion/regen image
    # Provenance for scene.json source.* (only meaningful for "generative").
    alignment_method: str = "n/a"
    scale_method: str = "n/a"


def slug(coco_class: str) -> str:
    return coco_class.lower().replace(" ", "_")


def _rot_matrix_to_quat_xyzw(R: np.ndarray) -> list[float]:
    """3x3 rotation matrix -> quaternion [x,y,z,w] (schema order)."""
    R = np.asarray(R, dtype=np.float64)
    t = np.trace(R)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        w = 0.25 * s
        x = (R[2, 1] - R[1, 2]) / s
        y = (R[0, 2] - R[2, 0]) / s
        z = (R[1, 0] - R[0, 1]) / s
    else:
        i = int(np.argmax([R[0, 0], R[1, 1], R[2, 2]]))
        if i == 0:
            s = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
            w = (R[2, 1] - R[1, 2]) / s; x = 0.25 * s
            y = (R[0, 1] + R[1, 0]) / s; z = (R[0, 2] + R[2, 0]) / s
        elif i == 1:
            s = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
            w = (R[0, 2] - R[2, 0]) / s; x = (R[0, 1] + R[1, 0]) / s
            y = 0.25 * s; z = (R[1, 2] + R[2, 1]) / s
        else:
            s = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
            w = (R[1, 0] - R[0, 1]) / s; x = (R[0, 2] + R[2, 0]) / s
            y = (R[1, 2] + R[2, 1]) / s; z = 0.25 * s
    q = np.array([x, y, z, w], dtype=np.float64)
    return (q / np.linalg.norm(q)).tolist()


def _camera_pose(poses: list[np.ndarray]) -> dict:
    """Initial camera placement from the first frame pose (fix W5)."""
    if not poses:
        return {"translation": [0.0, 0.0, 0.0], "rotation_quat": [0.0, 0.0, 0.0, 1.0]}
    T = np.asarray(poses[0], dtype=np.float64)
    return {
        "translation": [float(v) for v in T[:3, 3]],
        "rotation_quat": _rot_matrix_to_quat_xyzw(T[:3, :3]),
    }


def _assemble_object(obj: ObjectInput, oid: int, ground_y: float, out_dir: Path,
                     phys: vlm.Physics, tier_coacd: dict, decimate_to: int,
                     config_path: str, engine=None) -> dict:
    import open3d as o3d

    oid_str = f"{slug(obj.coco_class)}_{oid:02d}"
    mesh = o3d.geometry.TriangleMesh(obj.mesh)  # copy

    aabb = mesh.get_axis_aligned_bounding_box()
    lo, hi = np.asarray(aabb.min_bound), np.asarray(aabb.max_bound)
    center = (lo + hi) / 2.0
    half_h = float(hi[1] - lo[1]) / 2.0
    world_bottom = float(lo[1])

    # placement (fix Z-H): keep observed height; snap to floor only if on it
    if abs(world_bottom - ground_y) <= GROUND_SNAP_BAND_M:
        ty = ground_y + half_h
    else:
        ty = float(center[1])
    translation = [float(center[0]), ty, float(center[2])]

    # recentre mesh to its AABB centre so the body rotates about its centre
    mesh.translate((-center).tolist())

    # Finalize the render/collide/measure mesh per ROUTING STRATEGY (one mesh for
    # all three, so what you see matches the colliders and the mass). Placement
    # (translation above) stays keyed to the RAW AABB so finalize can't shift it.
    #   * completion: the engine FILLS the unseen parts (Poisson locally; an
    #     image+geometry model on GPU). Falls back to the local repair if the
    #     engine declines (returns None).
    #   * tsdf / generative: light Step-7b repair — closes the open shell (tsdf)
    #     or is a no-op on an already-watertight generated mesh (generative).
    completed = None
    if obj.strategy == "completion" and engine is not None:
        completed = engine.complete(
            mesh=mesh, cloud=obj.cloud, crop_path=obj.crop_path,
            coco_class=obj.coco_class)
    if completed is not None and len(completed.vertices):
        final_mesh = completed
        vol = mass.closed_mesh_volume(final_mesh)
    else:
        final_mesh, vol, _watertight = mass.finalize_mesh(mesh)

    obj_dir = out_dir / "objects" / oid_str
    (obj_dir / "hulls").mkdir(parents=True, exist_ok=True)
    exporter_gltf.write_glb(final_mesh, obj_dir / "mesh.glb", decimate_to=decimate_to)

    parts = decomp.decompose(
        final_mesh, threshold=float(tier_coacd.get("threshold", 0.05)),
        max_parts=int(tier_coacd.get("max_parts", 16)),
    )
    hull_route_paths = []
    for i, part in enumerate(parts):
        exporter_gltf.write_glb(part, obj_dir / "hulls" / f"{oid_str}_{i}.glb")
        hull_route_paths.append(f"hulls/{oid_str}_{i}.glb")
    mass_kg = mass.mass_kg(vol, phys.material, obj.coco_class, config_path=config_path)

    return {
        "id": oid_str,
        "class": obj.coco_class,
        "mesh": f"meshes/{oid_str}.glb",
        "transform": {
            "translation": translation,
            "rotation_quat": [0.0, 0.0, 0.0, 1.0],
            "scale": 1.0,
        },
        "collider": {
            "shape": "hulls",
            "convex_decomposition": True,
            "hull_paths": hull_route_paths,
        },
        "physics": {
            "mass_kg": round(mass_kg, 4),
            "friction": phys.friction,
            "restitution": phys.restitution,
            "is_rigid": phys.is_rigid,
        },
        "material_class": phys.material,
        "source": _source(obj, phys),
    }


def _source(obj: ObjectInput, phys: vlm.Physics) -> dict:
    """scene.json source.* per strategy. "tsdf" and "completion" are both real
    TSDF geometry (completion just fills gaps) -> geometry_source "tsdf" with n/a
    provenance (fix Y1). "generative" reports the regen mesh's ICP provenance."""
    if obj.strategy == "generative":
        geom = {
            "geometry_source": "generative",
            "alignment_method": obj.alignment_method,
            "scale_method": obj.scale_method,
        }
    else:
        geom = {
            "geometry_source": "tsdf",
            "alignment_method": "n/a",   # tsdf/completion skip Step 7 ICP (fix Y1)
            "scale_method": "n/a",
        }
    return {
        **geom,
        "physics_origin": phys.origin,
        "vlm_reasoning": phys.reasoning,
    }


def assemble(objects: list[ObjectInput], poses: list[np.ndarray], out_dir: Path | str,
             *, tier_coacd: dict | None = None, decimate_to: int = 20000,
             vlm_backend=None, engine=None,
             config_path: str = str(lookup._DEFAULT_CONFIG)) -> dict:
    """Build + validate + write scene.json for the given objects.

    `engine` is the completion/generative backend (generative.Engine). It is
    consulted only for objects with strategy "completion" (to fill gaps);
    defaults to the local Poisson repair when None. Returns the scene dict. Caps
    at the 12 best-observed objects (Contract 3 / Z8) by observed point count.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tier_coacd = tier_coacd or {"threshold": 0.05, "max_parts": 16}
    if engine is None:
        from reconstruction.generative import LocalEngine
        engine = LocalEngine()

    objects = [o for o in objects if len(o.cloud) and len(o.mesh.vertices)]
    if len(objects) > 12:  # over-cap: keep best-observed (Z8)
        objects = sorted(objects, key=lambda o: -len(o.cloud))[:12]
    objects = sorted(objects, key=lambda o: o.track_id)

    g_y = ground.ground_y([o.cloud for o in objects], config_path=config_path)
    phys_list = vlm.infer([o.coco_class for o in objects],
                          backend=vlm_backend, config_path=config_path)

    entries = [
        _assemble_object(o, oid, g_y, out_dir, ph, tier_coacd, decimate_to,
                         config_path, engine=engine)
        for oid, (o, ph) in enumerate(zip(objects, phys_list))
    ]

    scene = {
        "version": "2.0",
        "world": {"gravity": list(WORLD_GRAVITY), "up_axis": "y", "unit": "meters"},
        "ground": {
            "type": "plane", "normal": list(ground.GROUND_NORMAL), "y": g_y,
            "material": lookup.ground_material(config_path),
        },
        "objects": entries,
        "camera_pose": _camera_pose(poses),
    }
    schema.validate(scene)  # never emit a scene that breaks the contract

    tmp = out_dir / "scene.json.tmp"
    tmp.write_text(json.dumps(scene, indent=2))
    tmp.rename(out_dir / "scene.json")  # atomic write (fix Z-C)
    return scene
