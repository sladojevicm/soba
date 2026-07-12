"""Step-7 ICP alignment tests (plan §12) — synthetic, CPU, no models.

The well-observed case must take the full FPFH+ICP path and recover metric
size/pose; degenerate observations must land on the class-prior coarse
fallback with honest provenance.
"""

from __future__ import annotations

import numpy as np
import pytest

from reconstruction import icp_align


def _l_mesh():
    """A unit-scale L-shaped mesh (two boxes) — asymmetric on every axis, so
    registration has no rotational ambiguity to get lucky/unlucky with."""
    import open3d as o3d

    a = o3d.geometry.TriangleMesh.create_box(1.0, 0.25, 0.4)
    b = o3d.geometry.TriangleMesh.create_box(0.3, 0.8, 0.4)
    m = a + b
    m.compute_vertex_normals()
    return m


def _observed_cloud(mesh, scale, yaw_deg, offset, n=4000, keep=1.0, seed=7):
    """Sample the mesh surface, apply scale/yaw/offset -> a synthetic observed
    cloud in metres. `keep` < 1 drops a contiguous chunk (partial observation)."""
    import open3d as o3d

    del seed  # o3d's sampler has no seed arg; determinism isn't needed here
    pts = np.asarray(mesh.sample_points_uniformly(n).points)
    th = np.deg2rad(yaw_deg)
    R = np.array([[np.cos(th), 0, np.sin(th)], [0, 1, 0], [-np.sin(th), 0, np.cos(th)]])
    pts = (pts - pts.mean(axis=0)) @ R.T * scale + np.asarray(offset)
    if keep < 1.0:
        cut = np.quantile(pts[:, 0], keep)
        pts = pts[pts[:, 0] <= cut]
    return pts


def test_well_observed_object_takes_fpfh_path_and_recovers_size():
    mesh = _l_mesh()
    cloud = _observed_cloud(mesh, scale=0.8, yaw_deg=30.0, offset=(2.0, 0.5, -1.0))
    res = icp_align.align(mesh, cloud, None)
    assert res.alignment_method == "fpfh_icp"
    assert res.scale_method == "per_axis_median"
    # recovered size: the aligned mesh's max extent ~ 0.8 * 1.0 (the L's long side)
    aabb = res.mesh.get_axis_aligned_bounding_box()
    ext = np.asarray(aabb.max_bound) - np.asarray(aabb.min_bound)
    assert max(ext) == pytest.approx(0.8, rel=0.15)
    # placed on the cloud: AABB centres agree to ~10 cm (the point MEAN of an
    # L-shape is not its box centre, so compare box centre to box centre)
    centre = (np.asarray(aabb.max_bound) + np.asarray(aabb.min_bound)) / 2
    cloud_centre = (cloud.max(axis=0) + cloud.min(axis=0)) / 2
    assert np.linalg.norm(centre - cloud_centre) < 0.1


def test_sliver_observation_falls_back_to_coarse():
    """A tiny fragment (the generative band's normal diet) must not pretend to
    be an ICP fit — it lands on the class-prior coarse path."""
    mesh = _l_mesh()
    cloud = _observed_cloud(mesh, scale=0.8, yaw_deg=0.0, offset=(0, 0, 0),
                            keep=0.15)  # 15% sliver
    res = icp_align.align(mesh, cloud, "chair")
    assert res.alignment_method == "coarse_aligned"
    assert res.scale_method == "class_prior"


def test_too_few_points_falls_back():
    mesh = _l_mesh()
    res = icp_align.align(mesh, np.random.rand(10, 3), "chair")
    assert res.alignment_method == "coarse_aligned"


def test_axis_coverage_flags_gappy_axis():
    rng = np.random.default_rng(0)
    solid = rng.uniform(0, 1, (2000, 3))
    assert icp_align.axis_coverage(solid, 0) > 0.9
    # two clusters at the ends of X -> most bins in between are empty
    gappy = solid.copy()
    gappy[:, 0] = np.where(gappy[:, 0] < 0.5, gappy[:, 0] * 0.1, 0.9 + gappy[:, 0] * 0.1)
    assert icp_align.axis_coverage(gappy, 0) < 0.5


def test_class_gate_rejects_absurd_dims():
    # a "bottle" the size of a couch must not pass the class gate
    assert not icp_align._dims_ok(np.array([2.0, 1.0, 1.0]), "bottle",
                                  str(icp_align.lookup._DEFAULT_CONFIG))
    assert icp_align._dims_ok(np.array([0.08, 0.3, 0.08]), "bottle",
                              str(icp_align.lookup._DEFAULT_CONFIG))
