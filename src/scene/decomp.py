"""Convex decomposition — Step 9 (Phase 9).

Breaks an object mesh into convex parts so the physics engine can collide it
efficiently (a chair -> legs/seat/back). Uses CoACD (Collision-Aware Convex
Decomposition, CPU). The render mesh is unchanged; these hulls are the COLLISION
geometry only (fix Z-O: collider "shape" is "hulls", not the triangle mesh).

Fallback: if CoACD is unavailable or returns nothing (e.g. a degenerate open
shell), we emit the single convex hull of the mesh as one part — a coarse but
always-valid collider that satisfies the schema (hulls needs >= 1 hull_path).
"""

from __future__ import annotations

import numpy as np


def _to_o3d(verts: np.ndarray, faces: np.ndarray):
    import open3d as o3d

    m = o3d.geometry.TriangleMesh(
        o3d.utility.Vector3dVector(np.asarray(verts, dtype=np.float64)),
        o3d.utility.Vector3iVector(np.asarray(faces, dtype=np.int32)),
    )
    m.compute_vertex_normals()
    return m


def convex_hull_part(mesh):
    """The single convex hull of a mesh, as a one-element parts list (fallback)."""
    hull, _ = mesh.compute_convex_hull()
    hull.compute_vertex_normals()
    return [hull]


def decompose(mesh, *, threshold: float = 0.05, max_parts: int = 16,
              preprocess: str = "auto") -> list:
    """Decompose an Open3D legacy mesh into a list of convex Open3D meshes.

    threshold: CoACD concavity threshold (smaller -> more parts, tighter fit).
    max_parts: cap on the number of hulls (CoACD max_convex_hull).
    Returns the single convex hull if CoACD is missing or yields nothing.
    """
    try:
        import coacd
    except ImportError:
        return convex_hull_part(mesh)

    verts = np.asarray(mesh.vertices)
    tris = np.asarray(mesh.triangles)
    if len(verts) == 0 or len(tris) == 0:
        return convex_hull_part(mesh)

    try:
        cmesh = coacd.Mesh(verts, tris)
        parts = coacd.run_coacd(
            cmesh, threshold=threshold, max_convex_hull=max_parts,
            preprocess_mode=preprocess,
        )
    except Exception:
        return convex_hull_part(mesh)

    out = [_to_o3d(v, f) for v, f in parts if len(v) and len(f)]
    return out or convex_hull_part(mesh)
