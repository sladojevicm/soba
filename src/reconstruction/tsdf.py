"""TSDF fusion — Step 4 Part B (module tsdf.py), Build Order Phase 5.

Fuses each object's masked depth across all frames into a per-object
``VoxelBlockGrid`` (Open3D tensor API) and extracts a watertight triangle mesh
via Marching Cubes. This is the geometry path for objects the Step-5 gate routes
to strategy "tsdf" (well-observed objects the camera saw most of).

WHAT IS / ISN'T HERE
  * SINGLE SCENE-LEVEL PASS: one loop over the frames; each frame's depth + RGB
    is read ONCE and applied to every tsdf-bound object's grid (fix Z-N). Masks
    are still read per object per frame.
  * Per-object grid sizing from the observed-cloud bbox (fix M2) — never a flat
    40000-block grid.
  * Real Open3D tensor call (fix X1): compute_unique_block_coordinates(...) FIRST,
    then integrate(coords, depth, colour, intr, intr, extr, ...). The legacy
    vbg.integrate(rgbd, ...) form does not exist in the tensor API.
  * Depth gates are in MILLIMETRES (fix G1): valid range 400..8000 mm.
  * DEVICE: the plan recommends "CUDA:0"; this build defaults to "CPU:0" because
    the dev box has no GPU. Open3D's tensor VoxelBlockGrid runs on CPU:0 (just
    slower) — "Metal:0" is invalid and raises (fix Z-I).

DEVIATIONS FROM THE PLAN'S CODE LISTING (deliberate, documented):
  * CONFIDENCE GATE is OPTIONAL. The plan zeros depth where conf < 150, but
    dataset bundles (Replica here) ship no conf.png. When a bundle has no conf
    map the conf gate is simply skipped; on live OAK capture pass conf_min to
    enable it (and verify the sensor's polarity — fix's caveat).
  * Z-T MOTION FILTER is NOT applied here by default. room_0 is a static scene,
    so straight integration of every visible frame is correct. The hook is the
    `keep_frames` argument: pass the Part-A motion-consistent keep-frame set to
    honour fix Z-T for moving objects. When None, all visible frames integrate.

This validates TSDF geometry on PERFECT synthetic depth + GT masks (Replica).
Real-sensor noise is a later (ScanNet) concern.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from perception.bundle import PerceptionBundle
from reconstruction import observed_cloud

DEFAULT_VOXEL_SIZE = 0.004  # 4 mm (Tier 2). Tier 3: 3mm, Tier 4: 2mm.
BLOCK_RESOLUTION = 16  # one block spans 16 voxels per side
DEPTH_SCALE = 1000.0  # millimetres -> metres (integrate expects raw mm)
DEPTH_MAX_M = 8.0  # metres, post-scale (matches 8000 mm gate)


def block_count_for_extent(
    extent_m: tuple[float, float, float],
    voxel_size: float,
    *,
    block_resolution: int = BLOCK_RESOLUTION,
    safety: float = 2.0,
    lo: int = 1000,
    hi: int = 20000,
) -> int:
    """Per-object VoxelBlockGrid block_count from its bbox extent (fix M2).

    TSDF only allocates blocks in a thin shell around the surface, so size the
    grid from the object's surface area, not a flat 40000 (~3.2 GB each). A
    couple-thousand blocks for a chair -> a few hundred MB.
    """
    dx, dy, dz = (max(float(e), 0.0) for e in extent_m)
    block_size_m = block_resolution * voxel_size
    surf_area = 2.0 * (dx * dy + dy * dz + dx * dz)
    est_blocks = (surf_area / (block_size_m**2)) * safety
    return int(min(max(est_blocks, lo), hi))


def _visible_frames(bundle: PerceptionBundle, track_id: int) -> list[int]:
    """Frame ids whose objects.json lists this track_id (no image reads)."""
    out = []
    for fid in bundle.iter_frame_ids():
        if any(d.get("track_id") == track_id for d in bundle.read_objects(fid)):
            out.append(fid)
    return out


def _all_track_ids(bundle: PerceptionBundle) -> list[int]:
    """Union of track_ids across every frame's objects.json, sorted."""
    ids: set[int] = set()
    for fid in bundle.iter_frame_ids():
        for d in bundle.read_objects(fid):
            ids.add(int(d["track_id"]))
    return sorted(ids)


def _object_frames(
    bundle: PerceptionBundle, track_id: int, poses: list[np.ndarray]
) -> tuple[list[int], list[tuple[np.ndarray, np.ndarray, np.ndarray]]]:
    """(frame_ids, [(depth_mm, mask, T)...]) for every frame the object has a mask."""
    fids: list[int] = []
    frames: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for fid in _visible_frames(bundle, track_id):
        if not bundle.mask_path(fid, track_id).exists():
            continue
        fids.append(fid)
        frames.append(
            (bundle.read_depth_mm(fid), bundle.read_mask(fid, track_id), poses[fid])
        )
    return fids, frames


def analyze_objects(
    bundle: PerceptionBundle,
    track_ids: list[int],
    poses: list[np.ndarray],
    K: np.ndarray,
    *,
    voxel_size: float,
    min_mm: int,
    max_mm: int,
    motion_filter: bool = False,
    move_thresh_frac: float = 0.3,
) -> tuple[dict[int, tuple[float, float, float]], dict[int, set[int]]]:
    """Per-object Step-4A analysis: bbox extent (for grid sizing, fix M2) AND the
    motion-consistent keep-frame set (fix Z-T), from a single back-projection pass.

    Returns (extents, keep_frames). `extents[tid]` is the (dx,dy,dz) of the KEPT
    observed cloud (m); objects with no valid masked depth get a zero extent (they
    fall to the minimum block_count clamp). `keep_frames[tid]` is the set of
    FRAME IDS to integrate — every visible frame when motion_filter is off, the
    Z-T motion-consistent subset when on. This keep set is the single source of
    truth the TSDF integration reuses.
    """
    extents: dict[int, tuple[float, float, float]] = {}
    keep_frames: dict[int, set[int]] = {}
    for tid in track_ids:
        fids, frames = _object_frames(bundle, tid, poses)
        cloud, keep_idx = observed_cloud.accumulate_object_cloud(
            frames, K,
            voxel_size=voxel_size, min_mm=min_mm, max_mm=max_mm,
            motion_filter=motion_filter, move_thresh_frac=move_thresh_frac,
            return_keep=True,
        )
        if cloud.size:
            ext = cloud.max(axis=0) - cloud.min(axis=0)
            extents[tid] = (float(ext[0]), float(ext[1]), float(ext[2]))
        else:
            extents[tid] = (0.0, 0.0, 0.0)
        keep_frames[tid] = {fids[i] for i in keep_idx}
    return extents, keep_frames


def fuse(
    bundle: PerceptionBundle,
    track_ids: list[int] | None = None,
    *,
    voxel_size: float = DEFAULT_VOXEL_SIZE,
    device: str = "CPU:0",
    min_mm: int = observed_cloud.DEFAULT_MIN_MM,
    max_mm: int = observed_cloud.DEFAULT_MAX_MM,
    conf_min: int | None = None,
    keep_frames: dict[int, set[int]] | None = None,
    motion_filter: bool = False,
    move_thresh_frac: float = 0.3,
    block_resolution: int = BLOCK_RESOLUTION,
    progress: bool = False,
):
    """Scene-level TSDF fusion. Returns {track_id: legacy o3d TriangleMesh}.

    Args:
      track_ids: objects to fuse. None -> every object in the bundle.
      voxel_size: TSDF voxel edge in metres (tier-dependent).
      device: Open3D device string. "CPU:0" here (no GPU); "CUDA:0" on an
        NVIDIA host.
      conf_min: if set AND the bundle has conf maps, zero depth where conf <
        conf_min. None (default) skips the conf gate (datasets ship no conf).
      motion_filter: apply the Z-T motion-consistent keep-frame filter (fix Z-T).
        Default False — straight integration of every visible frame, correct for
        a static scene like room_0. When True, the keep-frame set is computed in
        the sizing pass (the single source of truth) and reused for integration.
      move_thresh_frac: keep-frame threshold as a fraction of the object's full
        bbox diagonal (default 0.3); only used when motion_filter is True.
      keep_frames: an explicit {track_id: {frame_id,...}} keep set. Overrides
        motion_filter (use a precomputed Part-A set). None lets motion_filter
        decide.
    """
    import open3d as o3d  # lazy: keeps the rest of the package import-light

    dev = o3d.core.Device(device)
    K = bundle.intrinsics.matrix()
    poses = bundle.read_poses()

    if track_ids is None:
        track_ids = _all_track_ids(bundle)

    intrinsic = o3d.core.Tensor(K, dtype=o3d.core.float64)

    # --- size every grid before the pass (fix M2) + compute the Z-T keep-frame
    #     set ONCE (fix Z-T: single source of truth reused for integration) -----
    extents, auto_keep = analyze_objects(
        bundle, track_ids, poses, K,
        voxel_size=voxel_size, min_mm=min_mm, max_mm=max_mm,
        motion_filter=motion_filter, move_thresh_frac=move_thresh_frac,
    )
    if keep_frames is None and motion_filter:
        keep_frames = auto_keep
    block_size_m = block_resolution * voxel_size
    per_object_vbg = {}
    for tid in track_ids:
        bc = block_count_for_extent(
            extents[tid], voxel_size, block_resolution=block_resolution
        )
        per_object_vbg[tid] = o3d.t.geometry.VoxelBlockGrid(
            attr_names=("tsdf", "weight", "color"),
            attr_dtypes=(o3d.core.float32, o3d.core.float32, o3d.core.float32),
            attr_channels=((1,), (1,), (3,)),
            voxel_size=voxel_size,
            block_resolution=block_resolution,
            block_count=bc,
            device=dev,
        )
        if progress:
            d = extents[tid]
            print(
                f"  grid track {tid}: extent {d[0]:.2f}x{d[1]:.2f}x{d[2]:.2f} m "
                f"-> {bc} blocks ({block_size_m * 1000:.0f} mm/block)"
            )

    # --- single scene-level pass: depth/rgb read ONCE per frame ---------
    for fid in bundle.iter_frame_ids():
        depth_mm = bundle.read_depth_mm(fid).astype(np.uint16, copy=True)
        rgb = bundle.read_rgb(fid)

        # Depth cleaning, IN MILLIMETRES (fix G1).
        if conf_min is not None and bundle.conf_path(fid).exists():
            depth_mm[bundle.read_conf(fid) < conf_min] = 0
        depth_mm[depth_mm < min_mm] = 0
        depth_mm[depth_mm > max_mm] = 0

        rgb_t = o3d.core.Tensor(np.ascontiguousarray(rgb), device=dev)
        pose = o3d.core.Tensor(poses[fid], dtype=o3d.core.float64)
        # world->camera (fix Z-Q: .inv(), not .inverse()). .contiguous() because
        # Open3D's integrate requires a contiguous extrinsic tensor.
        extrinsic = pose.inv().contiguous()

        for tid, vbg in per_object_vbg.items():
            if keep_frames is not None and fid not in keep_frames.get(tid, set()):
                continue
            mp = bundle.mask_path(fid, tid)
            if not mp.exists():
                continue
            mask = bundle.read_mask(fid, tid)
            if not np.any(mask):
                continue

            masked = depth_mm.copy()
            masked[mask == 0] = 0
            if not np.any(masked):
                continue

            depth_img = o3d.t.geometry.Image(
                o3d.core.Tensor(masked, device=dev)
            )
            color_img = o3d.t.geometry.Image(rgb_t)
            coords = vbg.compute_unique_block_coordinates(
                depth_img, intrinsic, extrinsic,
                depth_scale=DEPTH_SCALE, depth_max=DEPTH_MAX_M,
            )
            vbg.integrate(
                coords, depth_img, color_img,
                intrinsic, intrinsic, extrinsic,
                depth_scale=DEPTH_SCALE, depth_max=DEPTH_MAX_M,
                trunc_voxel_multiplier=4.0,
            )
        if progress:
            print(f"  integrated frame {fid}")

    # --- extract one mesh per object ------------------------------------
    meshes = {}
    for tid, vbg in per_object_vbg.items():
        mesh = vbg.extract_triangle_mesh().to_legacy()
        mesh.compute_vertex_normals()
        meshes[tid] = mesh
    return meshes


def export_meshes(
    meshes: dict,
    out_dir: Path | str,
    *,
    classes: dict[int, str] | None = None,
    fmt: str = "ply",
) -> dict[int, Path]:
    """Write each mesh to out_dir as ``tsdf_{class}_{track_id}.{fmt}``.

    .ply (with vertex colours) imports natively into Blender. Empty meshes (no
    geometry fused) are skipped and reported by absence in the returned dict.
    """
    import open3d as o3d

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[int, Path] = {}
    for tid, mesh in meshes.items():
        if len(mesh.vertices) == 0:
            continue
        label = (classes or {}).get(tid, "obj")
        slug = str(label).replace(" ", "_")
        path = out_dir / f"tsdf_{slug}_{tid}.{fmt}"
        o3d.io.write_triangle_mesh(str(path), mesh)
        written[tid] = path
    return written
