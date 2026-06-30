"""glTF/GLB export — Step 7b/10 (Phase 9).

Writes Open3D meshes to .glb, the web-3D standard the browser (Three.js) loads.
Optional quadric decimation keeps render meshes light for the browser; collider
hulls are exported as-is (already low-poly). Vertex colours from the TSDF carry
through.
"""

from __future__ import annotations

from pathlib import Path


def decimate(mesh, target_triangles: int):
    """Quadric-decimate to ~target_triangles (no-op if already smaller)."""
    if target_triangles <= 0 or len(mesh.triangles) <= target_triangles:
        return mesh
    out = mesh.simplify_quadric_decimation(int(target_triangles))
    out.compute_vertex_normals()
    return out


def smooth_taubin(mesh, iterations: int):
    """Volume-preserving Taubin smoothing for the RENDER mesh ONLY (cosmetic).

    De-facets the marching-cubes / voxel staircasing that fusion's `smooth_sigma`
    leaves on the OBSERVED surface (smooth_sigma rounds only the unobserved back,
    and the "tsdf" keep-band gets no smoothing at all). Returns a smoothed COPY —
    the caller's mesh is untouched, so the collider/mass path keeps the exact
    observed geometry. No-op (returns the same object) if iterations<=0 or empty.

    Taubin's lambda/mu pair (Open3D defaults 0.5 / -0.53) counteracts the shrink
    of plain Laplacian, so a watertight mesh stays watertight and the enclosed
    volume is preserved. Topology is cleaned first (Taubin needs manifold input).
    """
    if iterations <= 0 or len(mesh.vertices) == 0:
        return mesh
    import open3d as o3d

    m = o3d.geometry.TriangleMesh(mesh)  # copy — never mutate the caller's mesh
    m.remove_duplicated_vertices()
    m.remove_duplicated_triangles()
    m.remove_degenerate_triangles()
    m.remove_non_manifold_edges()
    m = m.filter_smooth_taubin(number_of_iterations=int(iterations))
    m.compute_vertex_normals()
    return m


def write_glb(mesh, path: Path | str, *, decimate_to: int | None = None,
              smooth_iters: int = 0) -> Path:
    """Write a mesh to .glb (optionally Taubin-smoothed, then decimated). Returns
    the path. `smooth_iters` is a RENDER-only cosmetic polish (see smooth_taubin);
    leave it 0 for collider hulls so their geometry stays exact."""
    import open3d as o3d

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    m = smooth_taubin(mesh, smooth_iters) if smooth_iters else mesh
    m = decimate(m, decimate_to) if decimate_to else m
    if not m.has_vertex_normals():
        m.compute_vertex_normals()
    ok = o3d.io.write_triangle_mesh(str(path), m)
    if not ok:
        raise OSError(f"failed to write {path}")
    return path
