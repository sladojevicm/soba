"""Observed-cloud back-projection tests (Step 4 Part A, pure numpy)."""

from __future__ import annotations

import numpy as np

from reconstruction import observed_cloud as oc


def _K(fx=100.0, fy=100.0, cx=2.0, cy=2.0):
    return np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float64)


def test_backproject_center_pixel():
    # a single valid pixel exactly at the principal point projects to (0,0,z)
    depth = np.zeros((5, 5), dtype=np.uint16)
    depth[2, 2] = 1000  # 1000 mm = 1.0 m
    pts = oc.backproject(depth, _K())
    assert pts.shape == (1, 3)
    np.testing.assert_allclose(pts[0], [0.0, 0.0, 1.0], atol=1e-9)


def test_backproject_offset_pixel_uses_intrinsics():
    depth = np.zeros((5, 5), dtype=np.uint16)
    depth[2, 4] = 1000  # u=4, cx=2, fx=100 -> x = (4-2)/100 * 1.0 = 0.02
    pts = oc.backproject(depth, _K())
    np.testing.assert_allclose(pts[0], [0.02, 0.0, 1.0], atol=1e-9)


def test_backproject_rejects_out_of_range_and_mask():
    depth = np.full((3, 3), 1000, dtype=np.uint16)
    depth[0, 0] = 100  # below min_mm (400) -> dropped
    depth[0, 1] = 9000  # above max_mm (8000) -> dropped
    mask = np.zeros((3, 3), dtype=np.uint8)
    mask[1, 1] = 1
    pts = oc.backproject(depth, _K(), mask=mask)
    assert pts.shape == (1, 3)  # only the single masked, in-range pixel


def test_to_world_applies_translation():
    pts = np.array([[0.0, 0.0, 1.0]])
    T = np.eye(4)
    T[:3, 3] = [1.0, 2.0, 3.0]
    np.testing.assert_allclose(oc.to_world(pts, T)[0], [1.0, 2.0, 4.0])


def test_voxel_downsample_merges_close_points():
    pts = np.array([[0.0, 0.0, 0.0], [0.001, 0.0, 0.0], [1.0, 1.0, 1.0]])
    out = oc.voxel_downsample(pts, voxel_size=0.01)
    assert out.shape[0] == 2  # first two collapse into one voxel


def test_world_centroid_and_none():
    depth = np.zeros((5, 5), dtype=np.uint16)
    depth[2, 2] = 1000
    mask = np.zeros((5, 5), dtype=np.uint8)
    mask[2, 2] = 1
    c = oc.world_centroid(depth, mask, _K(), np.eye(4))
    np.testing.assert_allclose(c, [0.0, 0.0, 1.0], atol=1e-9)

    empty_mask = np.zeros((5, 5), dtype=np.uint8)
    assert oc.world_centroid(depth, empty_mask, _K(), np.eye(4)) is None


def test_provisional_ground_y():
    a = np.array([[0.0, 0.5, 0.0], [0.0, 1.0, 0.0]])
    b = np.array([[0.0, 0.2, 0.0]])
    assert abs(oc.provisional_ground_y([a, b], offset_m=0.02) - 0.18) < 1e-9
