"""Mass computation — Step 8 (Phase 9).

mass_kg = mesh_volume_m3 * density[material] * solidity[class]   (fix P2).
An APPROXIMATION, not physically exact: the solidity factor accounts for hollow
objects (a chair is mostly air). Mass is computed from GEOMETRY, never taken from
the VLM (which only supplies material + friction + restitution).

VOLUME caveat (this build): the plan assumes Step 7b has produced a WATERTIGHT
mesh. Our TSDF meshes are open shells (no back — the camera never saw it), so
`get_volume()` is invalid. We fall back to the CONVEX-HULL volume, which is a
reasonable proxy for a solid-ish object and is exactly the kind of approximation
the solidity factor already assumes. Flagged so it isn't mistaken for exact.
"""

from __future__ import annotations

import numpy as np

from . import lookup


def _signed_volume(mesh) -> float:
    """Enclosed volume of a CLOSED triangle mesh via the divergence theorem
    (sum of signed tetrahedron volumes). Works on any sealed mesh without
    Open3D's get_volume() watertight assertion (which rejects even convex hulls).
    """
    v = np.asarray(mesh.vertices)
    t = np.asarray(mesh.triangles)
    if len(v) == 0 or len(t) == 0:
        return 0.0
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    return abs(float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0)


def watertight_repair(mesh, *, depth: int = 8, density_quantile: float = 0.1):
    """Step 7b: close an open TSDF shell into an (approximately) watertight mesh
    via Poisson reconstruction, trimmed of low-density over-extension and cropped
    to the object's bounding box. Returns the repaired Open3D mesh.

    Poisson INTERPOLATES the unseen back, so the enclosed volume is far closer to
    the object's true displaced volume than the convex hull (which fills every
    concavity — under a table, between chair legs). Needs vertex normals; the
    fresh TSDF mesh has them, otherwise they are estimated.
    """
    import open3d as o3d

    pcd = o3d.geometry.PointCloud(mesh.vertices)
    # Carry vertex colours into the point cloud so Poisson interpolates them onto
    # the closed surface — otherwise the repaired mesh renders flat grey instead
    # of keeping the TSDF appearance.
    if mesh.has_vertex_colors():
        pcd.colors = mesh.vertex_colors
    if mesh.has_vertex_normals():
        pcd.normals = mesh.vertex_normals
    else:
        pcd.estimate_normals(o3d.geometry.KDTreeSearchParamKNN(knn=30))
        pcd.orient_normals_consistent_tangent_plane(30)
    pm, dens = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=depth)
    import numpy as _np

    pm.remove_vertices_by_mask(_np.asarray(dens) < _np.quantile(dens, density_quantile))
    pm = pm.crop(mesh.get_axis_aligned_bounding_box())
    pm.compute_vertex_normals()
    return pm


def finalize_mesh(mesh):
    """Step 7b finalisation — repair ONCE and return the mesh to actually use.

    Returns ``(finalized_mesh, volume_m3, watertight)``. The finalized mesh is
    the SAME geometry that gets rendered, collided, and measured for mass, so the
    physics matches what you see. An already-watertight mesh is returned as-is.
    An open TSDF shell is closed by Poisson repair when that yields a
    tighter-than-hull, non-empty mesh; otherwise we keep the RAW shell (and use
    the convex-hull volume), i.e. the pre-repair behaviour — repair never makes
    things worse, it only upgrades when it clearly worked.
    """
    if len(mesh.vertices) == 0:
        return mesh, 0.0, False
    if mesh.is_watertight():
        return mesh, _signed_volume(mesh), True
    hull, _ = mesh.compute_convex_hull()
    hull_v = _signed_volume(hull)
    try:
        repaired = watertight_repair(mesh)
        if len(repaired.triangles):
            v = _signed_volume(repaired)
            if 0.0 < v <= hull_v:  # repaired must be tighter than the hull
                return repaired, v, bool(repaired.is_watertight())
    except Exception:
        pass
    return mesh, hull_v, False


def volume_m3(mesh) -> tuple[float, bool]:
    """(volume in m^3, watertight?) for an Open3D legacy TriangleMesh.

    Already watertight -> true enclosed volume. Open TSDF shell -> Poisson
    watertight repair (Step 7b) when that yields a tighter-than-hull volume,
    else the convex-hull volume. The repaired estimate is bounded by the hull
    (never larger) and falls back safely on any failure. Volume is the
    signed-tetrahedron sum (robust where Open3D's get_volume() refuses).

    HONEST: even the repaired volume is APPROXIMATE — Poisson invents the unseen
    back and the crop can leave small holes, so masses derived from it are a
    best-effort estimate (typically still somewhat high), not ground truth.

    Thin wrapper over finalize_mesh() (which also returns the closed mesh) — kept
    for callers that only want the volume.
    """
    _, vol, watertight = finalize_mesh(mesh)
    return vol, watertight


def closed_mesh_volume(mesh) -> float:
    """Volume of an ALREADY-finalized mesh (e.g. a completion engine's output) —
    no further repair. Signed-tetrahedron volume, bounded above by the convex
    hull so a mesh with small residual seams can't report a runaway volume."""
    if len(mesh.vertices) == 0:
        return 0.0
    v = _signed_volume(mesh)
    try:
        hull, _ = mesh.compute_convex_hull()
        hv = _signed_volume(hull)
    except Exception:
        hv = v
    return min(v, hv) if v > 0 else hv


def mass_kg(
    volume: float,
    material: str,
    coco_class: str,
    *,
    config_path: str = str(lookup._DEFAULT_CONFIG),
) -> float:
    """vol * density[material] * solidity[class]. Always > 0 (clamped tiny)."""
    rho = lookup.density(material, config_path)
    sol = lookup.solidity(coco_class, config_path)
    return max(volume * rho * sol, 1e-3)
