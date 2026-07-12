"""Render-only Taubin smoothing tests (cosmetic polish, exporter_gltf).

Asserts the guarantees the render smoother MUST keep so it can complement fusion
without undoing it: the caller's mesh is never mutated (the collider/mass path
stays exact), topology is preserved (Taubin moves vertices, never re-meshes), a
watertight mesh stays watertight, and the enclosed volume barely changes (Taubin's
lambda/mu counteract Laplacian shrink). iters<=0 is a strict no-op.
"""

from __future__ import annotations

import numpy as np
import open3d as o3d

from scene import exporter_gltf as eg


def _noisy_sphere():
    """A clean (manifold, watertight) sphere with per-vertex positional noise —
    the kind of staircased-but-closed surface the render smoother polishes."""
    m = o3d.geometry.TriangleMesh.create_sphere(radius=0.3, resolution=20)
    v = np.asarray(m.vertices)
    rng = np.random.default_rng(0)
    # 0.002 keeps the noisy sphere genuinely watertight (0.004 self-intersects the
    # thin pole triangles under Open3D 0.19, breaking the watertight precondition).
    m.vertices = o3d.utility.Vector3dVector(v + rng.normal(0, 0.002, v.shape))
    m.compute_vertex_normals()
    return m


def test_iters_zero_is_noop():
    m = _noisy_sphere()
    assert eg.smooth_taubin(m, 0) is m  # identity: no copy, no work


def test_input_not_mutated():
    m = _noisy_sphere()
    before = np.asarray(m.vertices).copy()
    eg.smooth_taubin(m, 10)
    # the collider/mass path reuses this mesh — it must be byte-for-byte intact
    assert np.array_equal(np.asarray(m.vertices), before)


def test_topology_preserved():
    m = _noisy_sphere()
    out = eg.smooth_taubin(m, 10)
    assert len(out.vertices) == len(m.vertices)
    assert len(out.triangles) == len(m.triangles)


def test_watertight_and_volume_preserved():
    m = _noisy_sphere()
    assert m.is_watertight()
    out = eg.smooth_taubin(m, 10)
    assert out.is_watertight()
    v0, v1 = m.get_volume(), out.get_volume()
    assert abs(v1 - v0) / v0 < 0.05  # Taubin preserves volume within a few %
