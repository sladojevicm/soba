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
           bottom dropped onto the ground plane (x,z keep the observed position).
           rotation_quat is identity (a TSDF mesh is already world-axis-aligned),
           scale 1.0 (baked in).
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

from . import decomp, exporter_gltf, geometric_repair, ground, lookup, mass, schema, vlm
from reconstruction import fusion

WORLD_GRAVITY = [0.0, -9.81, 0.0]
# Gaussian smoothing (in voxels) applied to the PATCHED/unobserved surface only
# during Option-A fusion; observed geometry is left exact. 3.0 rounds the coarse
# completion back well while keeping the real surface crisp.
FUSION_SMOOTH_SIGMA = 3.0
# Taubin smoothing iterations for the RENDER mesh ONLY — a cosmetic de-facet of
# the marching-cubes/voxel staircasing. Complements FUSION_SMOOTH_SIGMA (which
# rounds only the unobserved back): this lightly polishes the WHOLE rendered
# surface without touching the collider/mass mesh (those keep exact geometry).
# 0 disables it (the A/B baseline).
RENDER_SMOOTH_ITERS = 10


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
    # Option-A fusion (completion band): the live VoxelBlockGrid + its voxel size
    # carry the per-voxel observed/unobserved mask. When present, a "completion"
    # object KEEPS its observed geometry and grafts the engine's completion only
    # where unobserved (fuse -> seal), instead of replacing the whole mesh.
    vbg: object = None
    voxel_size: float | None = None
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


# How far (metres) the completion may INVENT geometry away from observed surface,
# per class. Flat objects with a large empty underside (a table's leg room) must
# fill only a thin band under the observed top — otherwise the completion fills the
# whole void as a solid blob. Bulky/solid objects (couch) fill generously so their
# unseen back closes. See fusion.fuse_completion(max_fill_dist_m).
FILL_DIST_BY_CLASS = {
    "dining table": 0.06, "table": 0.06, "desk": 0.06, "bench": 0.08,
    "tv": 0.06, "laptop": 0.06,
}
FILL_DIST_DEFAULT = 0.25


def _fill_dist(coco_class: str) -> float:
    return FILL_DIST_BY_CLASS.get((coco_class or "").lower(), FILL_DIST_DEFAULT)


def _fuse_and_seal(obj, center, completed):
    """Option-A finalize for a "completion" object that carries its VBG: KEEP the
    real observed geometry and graft the engine's completion only where unobserved
    (fusion), then seal into a watertight collider (Option D). All in WORLD coords
    (the VBG's frame); the result is recentred to the AABB centre like every other
    branch. `completed` is the engine's completion in the RECENTRED frame (or None).
    Returns (recentred_mesh, volume); falls back to a plain finalize on any failure.
    """
    import open3d as o3d

    if completed is not None and len(completed.vertices):
        comp = o3d.geometry.TriangleMesh(completed)
        comp.translate(center.tolist())          # recentred -> world
    else:
        comp = mass.watertight_repair(obj.mesh)  # local Poisson completion (world)
    try:
        # smooth_sigma rounds the patched (unobserved) surface only — the coarse
        # completion back (e.g. PatchComplete's 32^3) — leaving observed exact.
        fused, _ = fusion.fuse_completion(obj.vbg, comp, obj.voxel_size,
                                          smooth_sigma=FUSION_SMOOTH_SIGMA,
                                          max_fill_dist_m=_fill_dist(obj.coco_class))
        if len(fused.triangles):
            sealed, vol = _tsdf_watertight_finalize(fused)
            sealed.translate((-center).tolist())
            return sealed, vol
    except Exception:
        pass
    fm, vol, _ = mass.finalize_mesh(o3d.geometry.TriangleMesh(obj.mesh))
    fm.translate((-center).tolist())
    return fm, vol


def _tsdf_watertight_finalize(mesh):
    """Finalize a well-observed ("tsdf"/keep band) mesh into a watertight collider
    (Option D). pymeshfix preserves the real observed surface and seals only the
    unseen back; on any failure (pymeshfix missing, degenerate input) fall back to
    the Poisson Step-7b repair so the pipeline never crashes. Returns (mesh, volume).
    """
    try:
        rep, info = geometric_repair.watertight_collider(mesh)
        if len(rep.triangles):
            return rep, mass.closed_mesh_volume(rep)
    except Exception:
        pass
    final_mesh, vol, _watertight = mass.finalize_mesh(mesh)
    return final_mesh, vol


def _assemble_object(obj: ObjectInput, oid: int, ground_y: float, out_dir: Path,
                     phys: vlm.Physics, tier_coacd: dict, decimate_to: int,
                     config_path: str, engine=None, smooth_iters: int = 0,
                     collider: str = "hulls") -> dict:
    import open3d as o3d

    oid_str = f"{slug(obj.coco_class)}_{oid:02d}"
    mesh = o3d.geometry.TriangleMesh(obj.mesh)  # copy

    aabb = mesh.get_axis_aligned_bounding_box()
    lo, hi = np.asarray(aabb.min_bound), np.asarray(aabb.max_bound)
    center = (lo + hi) / 2.0
    half_h = float(hi[1] - lo[1]) / 2.0

    # placement: DROP every object so its bottom rests on the ground plane. The
    # observed scan height is unreliable — partial/holey meshes and a noisy ground
    # estimate leave objects floating — and this scene is furniture standing on the
    # floor, so ground-snapping is both more correct and removes the "floating"
    # cue. (Earlier the snap only fired when the bottom was already near the floor,
    # which is exactly why some objects floated.) Horizontal (x, z) keeps the real
    # observed position. Objects that genuinely rest ON another object (a bottle on
    # a table) would need stacking logic — not in this scene; revisit with the
    # de-overlap pass.
    ty = ground_y + half_h
    translation = [float(center[0]), ty, float(center[2])]

    # recentre mesh to its AABB centre so the body rotates about its centre
    mesh.translate((-center).tolist())

    # Finalize the render/collide/measure mesh per ROUTING STRATEGY (one mesh for
    # all three, so what you see matches the colliders and the mass). Placement
    # (translation above) stays keyed to the RAW AABB so finalize can't shift it.
    #   * completion: the engine FILLS the unseen parts (Poisson locally; an
    #     image+geometry model on GPU). Falls back to the local repair if the
    #     engine declines (returns None).
    #   * tsdf: well-observed "keep" band (cleared the keep_completeness bar) ->
    #     geometric watertight repair (Option D): pymeshfix preserves the real
    #     observed surface and seals only the unseen back into a watertight
    #     2-manifold collider. Falls back to the Poisson repair if pymeshfix is
    #     unavailable or fails.
    #   * generative: light Step-7b repair — a no-op on an already-watertight
    #     generated mesh.
    completed = None
    if obj.strategy == "completion" and engine is not None:
        completed = engine.complete(
            mesh=mesh, cloud=obj.cloud, crop_path=obj.crop_path,
            coco_class=obj.coco_class)
    if obj.strategy == "completion" and obj.vbg is not None and obj.voxel_size:
        # Option A: keep real geometry, graft only the unobserved part, then seal.
        final_mesh, vol = _fuse_and_seal(obj, center, completed)
    elif completed is not None and len(completed.vertices):
        final_mesh = completed
        vol = mass.closed_mesh_volume(final_mesh)
    elif obj.strategy == "tsdf":
        final_mesh, vol = _tsdf_watertight_finalize(mesh)
    elif obj.strategy == "generative":
        # Generated mesh: RENDER it as-is (a real thin chair, not a Poisson blob)
        # and measure mass from the ENCLOSED volume, clamped into a plausible
        # fraction of the hull (mass.generative_volume). Cleanup guarantees the
        # generated mesh is a watertight single component, so the enclosed volume
        # is trustworthy — the earlier hull-only rule overshot mass badly (a
        # table's hull fills all the air under the top: 206 kg dining tables).
        # Hull volume remains the fallback for a non-watertight generation.
        final_mesh, vol = mesh, mass.generative_volume(mesh, config_path=config_path)[0]
    else:
        final_mesh, vol, _watertight = mass.finalize_mesh(mesh)

    obj_dir = out_dir / "objects" / oid_str
    (obj_dir / "hulls").mkdir(parents=True, exist_ok=True)
    # RENDER mesh gets the cosmetic Taubin polish; hulls below never do (they
    # are colliders — their geometry must stay exact).
    exporter_gltf.write_glb(final_mesh, obj_dir / "mesh.glb",
                            decimate_to=decimate_to, smooth_iters=smooth_iters)
    import os as _os
    if _os.environ.get("SOBA_DUMP_PLY"):  # debug: a readable copy for inspection
        import open3d as _o3d
        _o3d.io.write_triangle_mesh(str(obj_dir / "mesh.ply"), final_mesh)

    if collider == "box":
        # Tier 1 (fix Z-F): AABB half_extents of the final recentred mesh — no
        # CoACD. The schema forbids hull_paths on a box collider.
        fa = final_mesh.get_axis_aligned_bounding_box()
        ext = np.asarray(fa.max_bound) - np.asarray(fa.min_bound)
        collider_entry = {"shape": "box",
                          "half_extents": [round(float(e) / 2.0, 4) for e in ext]}
    else:
        parts = decomp.decompose(
            final_mesh, threshold=float(tier_coacd.get("threshold", 0.05)),
            max_parts=int(tier_coacd.get("max_parts", 16)),
        )
        hull_route_paths = []
        for i, part in enumerate(parts):
            exporter_gltf.write_glb(part, obj_dir / "hulls" / f"{oid_str}_{i}.glb")
            hull_route_paths.append(f"hulls/{oid_str}_{i}.glb")
        collider_entry = {
            "shape": "hulls",
            "convex_decomposition": True,
            "hull_paths": hull_route_paths,
        }
    mass_kg = mass.mass_kg(vol, phys.material, obj.coco_class,
                           fill_fraction=getattr(phys, "fill_fraction", None),
                           config_path=config_path)

    fa = final_mesh.get_axis_aligned_bounding_box()
    fhalf = ((np.asarray(fa.max_bound) - np.asarray(fa.min_bound)) / 2.0).tolist()

    return {
        "_half_extents": fhalf,  # internal: consumed by the de-overlap pass
        "id": oid_str,
        "class": obj.coco_class,
        "mesh": f"meshes/{oid_str}.glb",
        "transform": {
            "translation": translation,
            "rotation_quat": [0.0, 0.0, 0.0, 1.0],
            "scale": 1.0,
        },
        "collider": collider_entry,
        "physics": {
            "mass_kg": round(mass_kg, 4),
            "friction": phys.friction,
            "restitution": phys.restitution,
            "is_rigid": phys.is_rigid,
        },
        "material_class": phys.material,
        "source": _source(obj, phys),
    }


def deoverlap(entries: list[dict], *, tol: float = 0.05, max_shift: float = 0.5,
              iters: int = 60) -> int:
    """Separate interpenetrating objects on the ground plane (fix: the desk
    cluster renders as furniture clipping through furniture).

    Objects are placed at their OBSERVED positions with generated/prior sizes,
    so footprints overlap. For each overlapping pair (XZ AABBs, minus `tol` —
    a chair tucked at a desk legitimately grazes it), the LIGHTER object is
    pushed along the axis of least overlap. Displacement per object is capped
    at `max_shift` so nothing teleports; residual overlap is accepted rather
    than rearranging the room. Mutates entry translations; returns the number
    of moves. Needs the assembler's internal _half_extents on each entry.
    """
    import numpy as np

    moved = {e["id"]: 0.0 for e in entries}
    n_moves = 0
    for _ in range(iters):
        clean = True
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                a, b = entries[i], entries[j]
                ta, tb = a["transform"]["translation"], b["transform"]["translation"]
                ha, hb = a["_half_extents"], b["_half_extents"]
                ox = (ha[0] + hb[0]) - abs(ta[0] - tb[0]) - tol
                oz = (ha[2] + hb[2]) - abs(ta[2] - tb[2]) - tol
                if ox <= 0 or oz <= 0:
                    continue
                # lighter object moves, along the axis of least overlap; when
                # its budget is spent the residual overlap is ACCEPTED — never
                # start shoving the anchor (a desk must not dodge its chair)
                m = a if a["physics"]["mass_kg"] <= b["physics"]["mass_kg"] else b
                other = b if m is a else a
                if moved[m["id"]] >= max_shift - 1e-9:
                    continue
                axis = 0 if ox <= oz else 2
                amt = min((ox if axis == 0 else oz), max_shift - moved[m["id"]])
                sign = np.sign(m["transform"]["translation"][axis]
                               - other["transform"]["translation"][axis]) or 1.0
                m["transform"]["translation"][axis] += float(sign * amt)
                moved[m["id"]] += amt
                n_moves += 1
                clean = False
        if clean:
            break
    return n_moves


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
             vlm_backend=None, engine=None, smooth_iters: int = RENDER_SMOOTH_ITERS,
             config_path: str = str(lookup._DEFAULT_CONFIG),
             collider: str = "hulls") -> dict:
    """Build + validate + write scene.json for the given objects.

    `engine` is the completion/generative backend (generative.Engine). It is
    consulted only for objects with strategy "completion" (to fill gaps);
    defaults to the local Poisson repair when None. Returns the scene dict. Caps
    at the 12 best-observed objects (Contract 3 / Z8) by observed point count.

    `collider` is the tier's collider type: "hulls" (CoACD, default) or "box"
    (Tier 1: AABB half_extents, no decomposition).

    `smooth_iters` Taubin-smooths the RENDER mesh only (cosmetic); 0 disables it.
    The collider/mass geometry is never smoothed, so fusion's exact observed
    surface is preserved where it counts.
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

    def _longest_dim(m):
        ext = (np.asarray(m.get_axis_aligned_bounding_box().max_bound)
               - np.asarray(m.get_axis_aligned_bounding_box().min_bound))
        return float(np.max(ext))

    phys_list = vlm.infer([o.coco_class for o in objects],
                          backend=vlm_backend, config_path=config_path,
                          crops=[o.crop_path for o in objects],
                          dims_m=[_longest_dim(o.mesh) for o in objects])

    entries = [
        _assemble_object(o, oid, g_y, out_dir, ph, tier_coacd, decimate_to,
                         config_path, engine=engine, smooth_iters=smooth_iters,
                         collider=collider)
        for oid, (o, ph) in enumerate(zip(objects, phys_list))
    ]

    # placement de-overlap (SOBA_DEOVERLAP=0 disables), then strip the
    # internal half-extents field before schema validation
    import os as _os
    if _os.environ.get("SOBA_DEOVERLAP", "1") != "0":
        deoverlap(entries)
    for e in entries:
        e.pop("_half_extents", None)

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
