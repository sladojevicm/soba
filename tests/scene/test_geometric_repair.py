"""Option D — geometric watertight-repair tests (Step 7b alternative).

Exercised on a small synthetic open shell + a tiny floating fragment (fast;
pymeshfix on a full TSDF mesh is seconds-to-minutes, too slow for CI). Asserts
the two guarantees D exists to provide: fragments are dropped, and the result
is a watertight single-component manifold.
"""

from __future__ import annotations

import numpy as np
import open3d as o3d
import pytest

from scene import geometric_repair as gr


def _open_shell_with_fragment():
    """A sphere with a cap of triangles removed (an open hole) plus a tiny
    detached cube far away (a TSDF-style floating fragment)."""
    sphere = o3d.geometry.TriangleMesh.create_sphere(radius=0.3, resolution=20)
    sphere.compute_vertex_normals()
    tri = np.asarray(sphere.triangles)
    v = np.asarray(sphere.vertices)
    cz = v[tri].mean(axis=1)[:, 2]
    sphere.remove_triangles_by_mask(cz > 0.2)  # cut an open cap
    sphere.remove_unreferenced_vertices()
    frag = o3d.geometry.TriangleMesh.create_box(0.02, 0.02, 0.02)
    frag.translate([1.0, 0.0, 0.0])
    return sphere + frag


def test_declutter_drops_floating_fragment():
    mesh = _open_shell_with_fragment()
    n_before = len(mesh.cluster_connected_triangles()[1])
    out = gr.declutter(mesh, min_frac=0.02)
    n_after = len(out.cluster_connected_triangles()[1])
    assert n_before == 2 and n_after == 1  # fragment gone, shell kept


def test_watertight_collider_seals_open_shell():
    mesh = _open_shell_with_fragment()
    assert not mesh.is_watertight()
    rep, info = gr.watertight_collider(mesh, target_tris=0)  # tiny mesh: skip decimate
    assert info["watertight"] is True
    assert info["n_components"] == 1
    assert rep.is_watertight()
    assert len(rep.triangles) > 0


def test_watertight_collider_preserves_colors():
    mesh = _open_shell_with_fragment()
    mesh.paint_uniform_color([0.2, 0.6, 0.9])
    rep, _ = gr.watertight_collider(mesh, target_tris=0)
    assert rep.has_vertex_colors()
    # the transferred colour is the (single) source colour
    c = np.asarray(rep.vertex_colors)
    assert np.allclose(c[0], [0.2, 0.6, 0.9], atol=1e-6)


def test_watertight_collider_without_pymeshfix_raises(monkeypatch):
    """The contract: no pymeshfix -> raise (caller falls back to Poisson),
    never silently returns an open mesh."""
    import builtins

    real_import = builtins.__import__

    def fake(name, *a, **k):
        if name == "pymeshfix":
            raise ImportError("simulated missing pymeshfix")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(ImportError):
        gr.watertight_collider(_open_shell_with_fragment(), target_tris=0)
