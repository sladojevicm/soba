"""Option A (Fusion) — unit tests for the core fields logic.

The end-to-end fuse_completion needs a real VoxelBlockGrid (seconds to build, too
heavy for CI), so we test the three pure pieces it composes: mesh->SDF, the
observed/unobserved blend, and marching cubes back to world. Together they pin
the behaviour fusion depends on: keep real where observed, take completion where
not, smooth seam, correct world placement.
"""
from __future__ import annotations

import numpy as np
import open3d as o3d

from reconstruction import fusion


def test_mesh_to_grid_sdf_sign_and_scale():
    """A sphere -> negative inside, positive outside, clamped to [-1, 1]."""
    sphere = o3d.geometry.TriangleMesh.create_sphere(radius=0.1, resolution=20)
    sphere.compute_vertex_normals()
    voxel = 0.01
    lo = np.array([-0.2, -0.2, -0.2])
    dims = (40, 40, 40)
    sdf = fusion.mesh_to_grid_sdf(sphere, lo, voxel, dims, trunc_voxels=4.0)
    assert sdf.shape == dims
    assert sdf.min() >= -1.0 and sdf.max() <= 1.0
    centre_idx = tuple(int(round((0 - lo[k]) / voxel)) for k in range(3))
    assert sdf[centre_idx] < 0          # centre is inside the sphere
    assert sdf[0, 0, 0] > 0             # a corner is outside


def test_fuse_fields_keeps_real_where_observed():
    real = np.full((10, 10, 10), -1.0, np.float32)   # "real" says inside
    comp = np.full((10, 10, 10), 1.0, np.float32)    # "comp" says outside
    W = np.zeros((10, 10, 10), np.float32)
    W[2:5, 2:5, 2:5] = 5.0                            # an observed block
    fused = fusion.fuse_fields(real, W, comp, blend_voxels=0.0)
    assert np.allclose(fused[3, 3, 3], -1.0)          # deep observed -> real
    assert np.allclose(fused[8, 8, 8], 1.0)           # unobserved -> comp


def test_fuse_fields_blends_the_seam():
    real = np.full((20, 1, 1), -1.0, np.float32)
    comp = np.full((20, 1, 1), 1.0, np.float32)
    W = np.zeros((20, 1, 1), np.float32)
    W[:10] = 3.0                                       # left half observed
    fused = fusion.fuse_fields(real, W, comp, blend_voxels=4.0)
    assert np.allclose(fused[2, 0, 0], -1.0)           # deep observed -> real
    assert np.allclose(fused[18, 0, 0], 1.0)           # deep unobserved -> comp
    band = fused[10:13, 0, 0]                          # interior of the ramp (dist 1..3)
    assert np.all(np.diff(band) > 0)                   # monotone real->comp
    assert band.min() > -1.0 and band.max() < 1.0      # strictly between the extremes


def test_smooth_sigma_keeps_observed_smooths_patch():
    """smooth_sigma rounds the unobserved patch but leaves observed voxels exact."""
    rng = np.random.default_rng(0)
    real = rng.standard_normal((24, 24, 24)).astype(np.float32)   # arbitrary observed field
    comp = rng.standard_normal((24, 24, 24)).astype(np.float32)   # noisy completion
    W = np.zeros((24, 24, 24), np.float32)
    W[:12] = 4.0                                                  # left half observed
    plain = fusion.fuse_fields(real, W, comp, blend_voxels=0.0, smooth_sigma=0.0)
    sm = fusion.fuse_fields(real, W, comp, blend_voxels=0.0, smooth_sigma=2.0)
    # observed half is byte-for-byte unchanged (alpha=1 there)
    assert np.allclose(plain[:12], sm[:12])
    # unobserved half is smoother: lower variance of the local gradient
    g_plain = np.abs(np.diff(plain[13:], axis=0)).mean()
    g_sm = np.abs(np.diff(sm[13:], axis=0)).mean()
    assert g_sm < g_plain


def test_grid_to_mesh_places_in_world():
    field = np.ones((20, 20, 20), np.float32)
    field[5:15, 5:15, 5:15] = -1.0                     # a negative cube -> closed surface
    voxel = 0.05
    lo = np.array([1.0, 2.0, 3.0])
    m = fusion.grid_to_mesh(field, lo, voxel, level=0.0)
    assert len(m.triangles) > 0
    c = m.get_axis_aligned_bounding_box().get_center()
    # cube centre index ~10 -> world = 10*voxel + lo
    assert np.allclose(c, 10 * voxel + lo, atol=2 * voxel)


def test_grid_to_mesh_empty_when_no_crossing():
    field = np.full((8, 8, 8), 1.0, np.float32)        # all positive, no zero level
    m = fusion.grid_to_mesh(field, np.zeros(3), 0.05, level=0.0)
    assert len(m.triangles) == 0
