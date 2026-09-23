"""Option D — geometric watertight repair for the DENSE / well-observed band.

Turns an open, fragmented TSDF shell into a watertight, single-component,
2-manifold mesh suitable as a physics collider, while PRESERVING the real
observed surface (only the genuinely-unseen back is invented as a seal).

This is the no-GPU path for objects the gate marks well-observed (high angular
coverage + hull completeness, e.g. office_3 chair#25). It is NOT a substitute
for learned completion on heavily-partial objects: geometric repair can only
close holes / shrink-wrap; it cannot reconstruct a large never-seen surface
faithfully (it fills a plausible-but-fake envelope — fine for a collider, not
for true geometry). See memory `completion-tools-survey`.

Pipeline: declutter (drop tiny TSDF fragments) -> decimate (collider-grade
resolution; also what lets pymeshfix run in seconds not minutes) -> pymeshfix
(fill_holes + remove self-intersections -> watertight manifold).

pymeshfix is the installed stand-in for CGAL 3D Alpha Wrapping (the research
top-pick, whose Python bindings are not installed). Both convert defective ->
watertight while preserving defect-free regions.
"""
from __future__ import annotations

import numpy as np
import open3d as o3d


def declutter(mesh, *, min_frac: float = 0.02):
    """Drop connected components smaller than ``min_frac`` of the largest one
    (TSDF leaves hundreds of tiny floating fragments). Returns a new mesh.
    A no-op-safe copy; never mutates the input."""
    out = o3d.geometry.TriangleMesh(mesh.vertices, mesh.triangles)
    if mesh.has_vertex_colors():
        out.vertex_colors = mesh.vertex_colors
    labels, counts, _ = out.cluster_connected_triangles()
    labels = np.asarray(labels)
    counts = np.asarray(counts)
    if len(counts) == 0:
        return out
    keep = counts[labels] >= max(1, int(min_frac * counts.max()))
    out.remove_triangles_by_mask(~keep)
    out.remove_unreferenced_vertices()
    return out


def watertight_collider(mesh, *, target_tris: int = 80000, min_frac: float = 0.02):
    """Declutter -> decimate -> pymeshfix into a watertight 2-manifold collider.

    Returns ``(repaired_mesh, info)`` where info has watertight/n_components/
    n_triangles. Raises if pymeshfix is unavailable; callers that need a
    never-fail path should catch and fall back to Poisson (``mass.watertight_repair``).
    """
    import pymeshfix

    clean = declutter(mesh, min_frac=min_frac)
    if target_tris and len(clean.triangles) > target_tris:
        clean = clean.simplify_quadric_decimation(target_tris)
        clean.remove_unreferenced_vertices()

    mf = pymeshfix.MeshFix(np.asarray(clean.vertices), np.asarray(clean.triangles))
    mf.repair(joincomp=False, remove_smallest_components=True)
    rep = o3d.geometry.TriangleMesh(
        o3d.utility.Vector3dVector(mf.points),
        o3d.utility.Vector3iVector(mf.faces),
    )
    rep.compute_vertex_normals()
    _transfer_colors(mesh, rep)
    info = {
        "watertight": bool(rep.is_watertight()),
        "n_components": len(rep.cluster_connected_triangles()[1]),
        "n_triangles": len(rep.triangles),
    }
    return rep, info


def _transfer_colors(src, dst):
    """Nearest-neighbour colour transfer from the original observed mesh onto the
    repaired surface, so the collider keeps the TSDF appearance instead of flat
    grey. The invented back inherits the nearest observed colour (acceptable —
    it was never seen)."""
    if not src.has_vertex_colors() or len(src.vertices) == 0:
        return
    sc = np.asarray(src.vertex_colors)
    kd = o3d.geometry.KDTreeFlann(o3d.geometry.PointCloud(src.vertices))
    dv = np.asarray(dst.vertices)
    out = np.empty((len(dv), 3))
    for i, p in enumerate(dv):
        _, idx, _ = kd.search_knn_vector_3d(p, 1)
        out[i] = sc[idx[0]]
    dst.vertex_colors = o3d.utility.Vector3dVector(out)
