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


def write_glb(mesh, path: Path | str, *, decimate_to: int | None = None) -> Path:
    """Write a mesh to .glb (optionally decimated). Returns the path."""
    import open3d as o3d

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    m = decimate(mesh, decimate_to) if decimate_to else mesh
    if not m.has_vertex_normals():
        m.compute_vertex_normals()
    ok = o3d.io.write_triangle_mesh(str(path), m)
    if not ok:
        raise OSError(f"failed to write {path}")
    return path
