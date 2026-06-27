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


def volume_m3(mesh) -> tuple[float, bool]:
    """(volume in m^3, watertight?) for an Open3D legacy TriangleMesh.

    Watertight -> the true enclosed volume. Otherwise -> the convex-hull volume
    (open TSDF shell), reported via the bool so callers can record the fallback.
    Volume is computed from the triangles directly (robust on convex hulls, which
    Open3D's get_volume() wrongly rejects as non-watertight).
    """
    if len(mesh.vertices) == 0:
        return 0.0, False
    if mesh.is_watertight():
        return _signed_volume(mesh), True
    hull, _ = mesh.compute_convex_hull()
    return _signed_volume(hull), False


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
