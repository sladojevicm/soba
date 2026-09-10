"""PatchComplete shape-completion adapter (in-process, local GPU/CPU).

PatchComplete (NeurIPS'22) is a VOXEL completer: a 32^3 partial TSDF in, a 32^3
completed TSDF out. Unlike the point-completion models it returns a full implicit
volume, so we marching-cubes it directly to a mesh (no Poisson). It is the chosen
completion source for Option-A fusion — real-ScanNet-trained, robust on real scans,
correct size, instant. See memory `completion-verdict` / `fusion-option-a`.

This module loads the multi_res model in-process (verified to reproduce the CLI
`generation.py` prediction bit-for-bit) and exposes `complete_mesh(pts)`:
partial object-local points -> completed mesh in the SAME frame.

Repo + weights via SOBA_PATCHCOMPLETE_HOME (default ~/projects/soba/
PatchComplete). The model's few-shot codebook is read from the repo's RELATIVE
`priors/` dir, so model construction is done with cwd set to the repo.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

RES = 32
TRUNC = 2.5         # voxel-unit truncation (matches the validated chair25 run)
FILL = 30.0         # object max-extent -> ~30 voxels inside the 32^3 grid
CHANNELS = 128

_HOME = Path(os.environ.get("SOBA_PATCHCOMPLETE_HOME",
                            str(Path("~/projects/soba/PatchComplete").expanduser())))
_MODEL = None       # cached (model, device)


def _load_model():
    """Construct + load the multi_res model once; cache it. cwd is set to the
    PatchComplete repo during construction so the codebook loads from priors/."""
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    import sys
    import torch

    if str(_HOME) not in sys.path:
        sys.path.insert(0, str(_HOME))
    import model.patch_learning_models as plm  # noqa: E402  (repo module)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    models_dir = _HOME / "trained_models"
    prev = Path.cwd()
    try:
        os.chdir(_HOME)  # codebook is read from the relative path 'priors/'

        def sub(res):
            m = plm.PatchLearningModel_fewshot_priors_3_encoder(
                True, device, CHANNELS, res, truncation=TRUNC)
            m.load_state_dict(torch.load(models_dir / f"patch_learning_res_{res}.pt",
                                         map_location=device))
            return m.to(device)

        m32, m8, m4 = sub(32), sub(8), sub(4)
        model = plm.ShapeLearningModel_codebook_learning_end_to_end_flatten(
            False, device, CHANNELS * 2, m32, m8, m4)
        model.load_state_dict(torch.load(models_dir / "multi_res.pt", map_location=device))
        model = model.to(device).eval()
    finally:
        os.chdir(prev)
    _MODEL = (model, device)
    return _MODEL


def _partial_tsdf_32(pts):
    """Object-local points -> (signed 32^3 TSDF in voxel units, cen, scale).

    Sign comes from oriented normals (voxel outside surface = +); voxels far from
    any observed point land at +trunc = free/unseen (the missing back). Returns the
    normalisation so the prediction can be mapped back to the input frame.
    """
    import open3d as o3d
    from scipy.spatial import cKDTree

    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.asarray(pts, np.float64)))
    pcd.estimate_normals(o3d.geometry.KDTreeSearchParamKNN(knn=30))
    pcd.orient_normals_consistent_tangent_plane(30)
    p = np.asarray(pcd.points)
    n = np.asarray(pcd.normals)

    mn, mx = p.min(0), p.max(0)
    cen = 0.5 * (mn + mx)
    scale = FILL / (mx - mn).max()
    gp = (p - cen) * scale + RES / 2.0

    tree = cKDTree(gp)
    gx, gy, gz = np.meshgrid(np.arange(RES), np.arange(RES), np.arange(RES), indexing="ij")
    vox = np.stack([gx, gy, gz], -1).reshape(-1, 3) + 0.5
    dist, idx = tree.query(vox, k=1)
    sign = np.sign(np.sum((vox - gp[idx]) * n[idx], axis=1))
    sign[sign == 0] = 1.0
    sdf = np.clip((sign * dist).reshape(RES, RES, RES), -TRUNC, TRUNC).astype(np.float32)
    return sdf, cen, scale


def complete_mesh(pts):
    """Partial object-local points (N,3) -> PatchComplete completed mesh in the
    SAME frame. Raises on failure so the engine can fall back to Poisson."""
    import open3d as o3d
    import torch
    from skimage import measure

    model, device = _load_model()
    sdf, cen, scale = _partial_tsdf_32(pts)
    x = torch.from_numpy(sdf).unsqueeze(0).unsqueeze(0).float().to(device)
    with torch.no_grad():
        pred, *_ = model(x)
    pv = pred.cpu().numpy()[0, 0]               # (32,32,32) completed TSDF
    if not (pv.min() < 0 < pv.max()):
        raise RuntimeError("PatchComplete output has no zero-crossing")
    verts, faces, normals, _ = measure.marching_cubes(pv, level=0.0)
    world = (verts - RES / 2.0) / scale + cen   # invert the normalisation -> input frame
    m = o3d.geometry.TriangleMesh(
        o3d.utility.Vector3dVector(world),
        o3d.utility.Vector3iVector(faces.astype(np.int32)))
    m.vertex_normals = o3d.utility.Vector3dVector(normals.astype(np.float64))
    m.compute_vertex_normals()
    return m
