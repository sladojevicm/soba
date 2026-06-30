"""Option A — FUSION: keep the REAL observed geometry, graft ONLY the unobserved
part from a completion.

The pipeline's regenerate-everything completers (PatchComplete, SDFusion) throw
away our real scan resolution — they output a whole new low-res object even where
the camera saw the surface perfectly. Fusion fixes that: it keeps the real TSDF
exactly where we OBSERVED it and substitutes the completion's shape ONLY where we
did not.

The observed/unobserved mask is FREE: our `tsdf.py` VoxelBlockGrid stores a
per-voxel `weight` (frames that hit the voxel). weight>0 == observed, weight==0
== never seen. Alignment is FREE too: the completion's input is built from our own
cloud, so it already lives in the same world frame as the grid.

Method (all on a dense voxel grid over the object AABB):
  1. rasterise the sparse VBG -> dense (T_real, W).            [grid_from_vbg]
  2. completion mesh -> signed-distance field on the same grid. [mesh_to_grid_sdf]
  3. fused = observed ? T_real : T_comp, blended over a few-voxel
     band across the boundary so the seam is smooth.            [fuse_fields]
  4. marching cubes the fused field back to a world-space mesh. [grid_to_mesh]

Both fields use Open3D's TSDF sign convention (negative = inside the surface,
positive = outside); the completion SDF is normalised to the same truncation so
the zero-crossings line up.
"""
from __future__ import annotations

import numpy as np


def grid_from_vbg(vbg, voxel, *, max_voxels: int = 12_000_000, default_tsdf: float = 1.0):
    """Rasterise a sparse VoxelBlockGrid into dense (T_real, W, lo, grid_voxel).

    T_real: normalised truncated SDF in [-1, 1] (Open3D convention, negative
    inside), default +1 (empty) where no voxel was active. W: per-voxel weight,
    0 where unobserved. lo: world coord of grid index (0,0,0). `voxel` (the grid
    edge in metres) is passed in — the VBG does not expose it in this build.

    To bound memory, the dense grid is CAPPED at ``max_voxels``: for a large object
    at a fine voxel (e.g. a couch at 2 mm = hundreds of millions of cells) the grid
    is coarsened to ``grid_voxel = voxel * factor`` by binning. Per coarse cell the
    HIGHEST-WEIGHT fine sample wins, so the observed/unobserved mask is preserved
    (any observed fine voxel -> observed coarse cell). The completion is coarse
    (~3 cm) anyway, so this costs no real fusion fidelity. Returns the effective
    grid_voxel so the caller samples the completion + marching-cubes at that step.
    """
    coords, indices = vbg.voxel_coordinates_and_flattened_indices()
    coords = coords.numpy()
    indices = indices.numpy()
    tsdf = vbg.attribute("tsdf").reshape((-1, 1)).numpy()[indices, 0]
    weight = vbg.attribute("weight").reshape((-1, 1)).numpy()[indices, 0]
    voxel = float(voxel)
    lo = coords.min(0)
    hi = coords.max(0)

    base = np.ceil((hi - lo) / voxel).astype(np.int64) + 1
    n = int(np.prod(base))
    factor = 1 if n <= max_voxels else int(np.ceil((n / max_voxels) ** (1.0 / 3.0)))
    grid_voxel = voxel * factor

    dims = (np.ceil((hi - lo) / grid_voxel).astype(int) + 1)
    ijk = np.round((coords - lo) / grid_voxel).astype(int)
    np.clip(ijk, 0, dims - 1, out=ijk)
    # highest-weight fine sample wins per (coarse) cell -> stable-sort ascending so
    # the last write into each cell is the max-weight one.
    order = np.argsort(weight, kind="stable")
    ijk, tsdf, weight = ijk[order], tsdf[order], weight[order]
    T = np.full(tuple(dims), default_tsdf, np.float32)
    W = np.zeros(tuple(dims), np.float32)
    T[ijk[:, 0], ijk[:, 1], ijk[:, 2]] = tsdf
    W[ijk[:, 0], ijk[:, 1], ijk[:, 2]] = weight
    return T, W, lo, grid_voxel


def mesh_to_grid_sdf(mesh, lo, voxel, dims, *, trunc_voxels: float = 4.0):
    """Signed distance from the completion mesh sampled on the dense grid, in the
    SAME normalised-truncated convention as the VBG tsdf (negative inside, clamped
    to [-1, 1] over trunc = trunc_voxels*voxel). Uses Open3D raycasting (robust to
    an imperfect mesh; sign from winding)."""
    import open3d as o3d

    dims = tuple(int(d) for d in dims)
    gx, gy, gz = np.meshgrid(np.arange(dims[0]), np.arange(dims[1]),
                             np.arange(dims[2]), indexing="ij")
    pts = (np.stack([gx, gy, gz], -1).reshape(-1, 3).astype(np.float32) * voxel
           + lo.astype(np.float32))
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(mesh))
    q = o3d.core.Tensor(pts, dtype=o3d.core.float32)
    sd = scene.compute_signed_distance(q).numpy()      # metres, negative inside
    trunc = trunc_voxels * voxel
    return np.clip(sd / trunc, -1.0, 1.0).reshape(dims).astype(np.float32)


def fuse_fields(T_real, W, T_comp, *, blend_voxels: float = 3.0, w_min: float = 0.0,
                smooth_sigma: float = 0.0):
    """fused = observed ? T_real : T_comp, with a linear blend over `blend_voxels`
    on the unobserved side of the boundary so the graft seam is smooth.

    alpha = 1 deep in the observed region, ramps to 0 over blend_voxels into the
    unobserved region; fused = alpha*T_real + (1-alpha)*T_comp.

    smooth_sigma (voxels) > 0 ADDITIONALLY Gaussian-smooths the field and blends
    the smoothed version in by (1-alpha) — so the OBSERVED surface stays exact
    (alpha=1) while the coarse completion (e.g. PatchComplete's blocky 32^3 back)
    and the seam get rounded. The smoothing acts on the SDF, so marching cubes
    yields a smooth surface there without touching the real geometry.
    """
    from scipy import ndimage as ndi

    observed = W > w_min
    if blend_voxels > 0:
        dist_out = ndi.distance_transform_edt(~observed)   # 0 on observed, grows outward
        alpha = np.clip(1.0 - dist_out / blend_voxels, 0.0, 1.0).astype(np.float32)
    else:
        alpha = observed.astype(np.float32)
    fused = (alpha * T_real + (1.0 - alpha) * T_comp).astype(np.float32)
    if smooth_sigma > 0:
        smoothed = ndi.gaussian_filter(fused, smooth_sigma)
        fused = (alpha * fused + (1.0 - alpha) * smoothed).astype(np.float32)
    return fused


def grid_to_mesh(field, lo, voxel, *, level: float = 0.0):
    """Marching-cubes the fused field back to a world-space Open3D mesh.
    Returns an empty mesh if the field has no zero-crossing."""
    import open3d as o3d
    from skimage import measure

    fmin, fmax = float(field.min()), float(field.max())
    if not (fmin < level < fmax):
        return o3d.geometry.TriangleMesh()
    verts, faces, normals, _ = measure.marching_cubes(field, level=level)
    verts = verts * voxel + lo
    m = o3d.geometry.TriangleMesh(
        o3d.utility.Vector3dVector(verts.astype(np.float64)),
        o3d.utility.Vector3iVector(faces.astype(np.int32)))
    m.vertex_normals = o3d.utility.Vector3dVector(normals.astype(np.float64))
    m.compute_vertex_normals()
    return m


def fuse_completion(vbg, completion_mesh, voxel, *, blend_voxels: float = 3.0,
                    trunc_voxels: float = 4.0, smooth_sigma: float = 0.0):
    """End-to-end: real VBG + a completion mesh -> a fused mesh that keeps the
    observed geometry and grafts the completion only where unobserved.

    `voxel` is the grid edge in metres (the VBG it was built with). `smooth_sigma`
    (voxels) rounds the patched/unobserved surface only (observed stays exact).
    Returns (fused_mesh, info) with observed/unobserved voxel counts.
    """
    T_real, W, lo, gv = grid_from_vbg(vbg, voxel)
    T_comp = mesh_to_grid_sdf(completion_mesh, lo, gv, T_real.shape,
                              trunc_voxels=trunc_voxels)
    fused = fuse_fields(T_real, W, T_comp, blend_voxels=blend_voxels,
                        smooth_sigma=smooth_sigma)
    mesh = grid_to_mesh(fused, lo, gv)
    info = {
        "dims": list(T_real.shape),
        "grid_voxel": gv,
        "observed_voxels": int((W > 0).sum()),
        "unobserved_voxels": int((W == 0).sum()),
        "fused_tris": len(mesh.triangles),
    }
    return mesh, info
