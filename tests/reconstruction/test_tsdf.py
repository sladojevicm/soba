"""TSDF fusion tests (Step 4 Part B, Build Order Phase 5).

block_count sizing is pure math (fast). The fusion test builds a tiny synthetic
bundle (a flat fronto-parallel patch) and runs the real Open3D tensor pass so the
compute_unique_block_coordinates -> integrate -> extract path is exercised, not
just mocked.
"""

from __future__ import annotations

import numpy as np

from perception.bundle import Intrinsics, Manifest, PerceptionBundle
from reconstruction import tsdf


def test_block_count_scales_with_surface_area():
    # bigger object -> more blocks, holding voxel size fixed
    small = tsdf.block_count_for_extent((0.3, 0.3, 0.3), 0.004)
    big = tsdf.block_count_for_extent((1.5, 1.5, 1.5), 0.004)
    assert big > small


def test_block_count_clamped_to_range():
    # a degenerate (zero) extent floors at the lower clamp, never below
    assert tsdf.block_count_for_extent((0.0, 0.0, 0.0), 0.004) == 1000
    # an enormous object saturates the upper clamp
    assert tsdf.block_count_for_extent((50.0, 50.0, 50.0), 0.002) == 20000


def test_block_count_finer_voxels_need_more_blocks():
    # same object, smaller voxels -> smaller blocks -> more of them
    coarse = tsdf.block_count_for_extent((1.0, 1.0, 1.0), 0.008)
    fine = tsdf.block_count_for_extent((1.0, 1.0, 1.0), 0.002)
    assert fine > coarse


def _synthetic_plane_bundle(root) -> PerceptionBundle:
    """A few-frame bundle: one object = a flat patch 1.5 m in front of the camera.

    Camera pose is identity for every frame (static, fronto-parallel), so the
    fused TSDF should yield a small surface mesh for track_id 1. Uses several
    identical frames so voxel weight clears extract_triangle_mesh's default
    weight threshold (~3) — real sequences have ~100 frames per object.
    """
    n = 5
    h, w = 60, 80
    fx = fy = 80.0
    cx, cy = w / 2.0, h / 2.0
    intr = Intrinsics(fx=fx, fy=fy, cx=cx, cy=cy)
    man = Manifest(session_id="synth", fps=30.0, frame_count=n, source="test")
    b = PerceptionBundle.create(root, man, intr)

    depth = np.zeros((h, w), dtype=np.uint16)
    mask = np.zeros((h, w), dtype=np.uint8)
    depth[20:40, 30:50] = 1500  # 1.5 m patch
    mask[20:40, 30:50] = 255
    rgb = np.full((h, w, 3), 128, dtype=np.uint8)

    for fid in range(n):
        b.write_depth_mm(fid, depth)
        b.write_rgb(fid, rgb)
        b.write_mask(fid, 1, mask)
        b.write_objects(fid, [{"track_id": 1, "class": "book", "bbox": [30, 20, 50, 40]}])
    b.write_poses([np.eye(4) for _ in range(n)])
    return b


def test_fuse_produces_a_mesh_for_a_visible_object(tmp_path):
    b = _synthetic_plane_bundle(tmp_path / "synth")
    meshes = tsdf.fuse(b, track_ids=[1], voxel_size=0.01)
    assert 1 in meshes
    mesh = meshes[1]
    assert len(mesh.vertices) > 0
    assert len(mesh.triangles) > 0
    # the fused surface should sit ~1.5 m out in Z (camera looks down +Z)
    z = np.asarray(mesh.vertices)[:, 2]
    assert 1.3 < float(z.mean()) < 1.7


def test_fuse_auto_discovers_track_ids(tmp_path):
    b = _synthetic_plane_bundle(tmp_path / "synth2")
    meshes = tsdf.fuse(b, voxel_size=0.01)  # track_ids=None -> discover from objects.json
    assert set(meshes) == {1}


def _moving_object_bundle(root):
    """A bundle where one object sits at 1.5 m for 6 frames, then jumps to 3.0 m
    for 4 frames (identity camera pose, fronto-parallel). The Z-T filter should
    keep only the first static run, so the fused mesh sits at ~1.5 m, not smeared
    across 1.5..3.0 m.
    """
    h, w = 60, 80
    intr = Intrinsics(fx=80.0, fy=80.0, cx=w / 2.0, cy=h / 2.0)
    n = 10
    man = Manifest(session_id="moving", fps=30.0, frame_count=n, source="test")
    b = PerceptionBundle.create(root, man, intr)
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[20:40, 30:50] = 255
    rgb = np.full((h, w, 3), 128, dtype=np.uint8)
    for fid in range(n):
        depth = np.zeros((h, w), dtype=np.uint16)
        depth[20:40, 30:50] = 1500 if fid < 6 else 3000
        b.write_depth_mm(fid, depth)
        b.write_rgb(fid, rgb)
        b.write_mask(fid, 1, mask)
        b.write_objects(fid, [{"track_id": 1, "class": "book", "bbox": [30, 20, 50, 40]}])
    b.write_poses([np.eye(4) for _ in range(n)])
    return b


def test_fuse_motion_filter_keeps_first_static_run(tmp_path):
    b = _moving_object_bundle(tmp_path / "moving")

    # without the filter: both positions integrate -> mesh spans toward 3.0 m
    naive = tsdf.fuse(b, track_ids=[1], voxel_size=0.01)
    znaive = np.asarray(naive[1].vertices)[:, 2]
    assert znaive.max() > 2.5

    # with the filter: only the 1.5 m run survives -> mesh stays near 1.5 m
    filtered = tsdf.fuse(b, track_ids=[1], voxel_size=0.01, motion_filter=True)
    zf = np.asarray(filtered[1].vertices)[:, 2]
    assert len(zf) > 0
    assert zf.max() < 2.0


def test_analyze_objects_returns_extent_and_keep(tmp_path):
    b = _moving_object_bundle(tmp_path / "moving2")
    poses = b.read_poses()
    K = b.intrinsics.matrix()
    extents, keep = tsdf.analyze_objects(
        b, [1], poses, K, voxel_size=0.01, min_mm=400, max_mm=8000,
        motion_filter=True,
    )
    # 6 static frames (ids 0..5) kept, the 4 jumped frames (6..9) dropped
    assert keep[1] == {0, 1, 2, 3, 4, 5}
    assert 1 in extents


def test_export_meshes_writes_named_ply(tmp_path):
    b = _synthetic_plane_bundle(tmp_path / "synth3")
    meshes = tsdf.fuse(b, track_ids=[1], voxel_size=0.01)
    out = tsdf.export_meshes(meshes, tmp_path / "out", classes={1: "book"})
    assert 1 in out
    p = out[1]
    assert p.name == "tsdf_book_1.ply"
    assert p.exists() and p.stat().st_size > 0
