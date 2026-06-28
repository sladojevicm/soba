"""PoinTr learned shape-completion backend — middle band, local GPU.

Loads the pretrained PoinTr (PCN) model WITHOUT its custom CUDA ops — pure-torch
furthest-point-sample + gather shims, plus permissive stubs for the unused
extension modules (chamfer/emd/gridding...), so NO nvcc/CUDA-toolkit build is
needed. Completes a partial object point cloud (N,3) into a dense one (~16k pts),
which the caller meshes.

The PoinTr repo lives OUTSIDE this repo (clone of github.com/yuxumin/PoinTr) at
POINTR_HOME (default ~/projects/vid2sim/PoinTr), with the PCN checkpoint at
pretrained/PoinTr_PCN.pth. Verified on an RTX 4060: loads in ~30 s, < 250 MB VRAM.

PCN was trained on chair/sofa/table (among others) — in-domain for our furniture
— but on canonically-oriented synthetic partials, so quality on real room-scan
clouds is best-effort (orientation/noise differ). This is the learned mid-band
backend; the local Poisson repair remains the fallback.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import numpy as np

_POINTR_HOME = Path(os.environ.get(
    "POINTR_HOME", str(Path.home() / "projects" / "vid2sim" / "PoinTr")))

# model name -> (config relative to POINTR_HOME, checkpoint relative to POINTR_HOME)
# NOTE the PCN models expect PCN's own pre-normalised convention (their inference
# does NO re-normalisation); the ShapeNet-55 model expects centroid+unit-radius
# normalisation (ShapeNet55Dataset.pc_norm) — which is what complete_points does,
# so "pointr_sn55" is the convention-matched choice for arbitrary real input.
_MODELS = {
    "pointr":      ("cfgs/PCN_models/PoinTr.yaml",        "pretrained/PoinTr_PCN.pth"),
    "adapointr":   ("cfgs/PCN_models/AdaPoinTr.yaml",     "pretrained/AdaPoinTr_PCN.pth"),
    "pointr_sn55": ("cfgs/ShapeNet55_models/PoinTr.yaml", "pretrained/PoinTr_ShapeNet55.pth"),
}

_loaded: dict = {}  # name -> model (lazy, cached; loading is slow)


# --- pure-torch replacements for the two pointnet2 ops in the forward path ----
def _furthest_point_sample(xyz, npoint):
    import torch
    B, N, _ = xyz.shape
    dev = xyz.device
    centroids = torch.zeros(B, npoint, dtype=torch.long, device=dev)
    distance = torch.full((B, N), 1e10, device=dev)
    farthest = torch.zeros(B, dtype=torch.long, device=dev)
    bidx = torch.arange(B, dtype=torch.long, device=dev)
    for i in range(npoint):
        centroids[:, i] = farthest
        centroid = xyz[bidx, farthest, :].view(B, 1, 3)
        dist = torch.sum((xyz - centroid) ** 2, -1)
        mask = dist < distance
        distance[mask] = dist[mask]
        farthest = torch.max(distance, -1)[1]
    return centroids.int()


def _gather_operation(features, idx):  # (B,C,N), (B,npoint) -> (B,C,npoint)
    import torch
    B, C, _ = features.shape
    idx_l = idx.long().unsqueeze(1).expand(B, C, idx.shape[1])
    return torch.gather(features, 2, idx_l)


class _AnyT:
    """Permissive stand-in for any symbol the unused extensions expose:
    callable and self-returning on attribute access, so import-time chained calls
    like emd.emdModule() never break."""
    def __call__(self, *a, **k):
        return _ANY

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return _ANY


_ANY = _AnyT()


class _Stub(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):     # dunders raise -> inspect/import work
            raise AttributeError(name)
        return _ANY


def _install_shims():
    pu = _Stub("pointnet2_ops.pointnet2_utils")
    pu.furthest_point_sample = _furthest_point_sample
    pu.gather_operation = _gather_operation
    po = types.ModuleType("pointnet2_ops")
    po.pointnet2_utils = pu
    sys.modules["pointnet2_ops"] = po
    sys.modules["pointnet2_ops.pointnet2_utils"] = pu
    ext = types.ModuleType("extensions")
    ext.__path__ = []
    sys.modules["extensions"] = ext
    for sub in ["chamfer_dist", "gridding", "gridding_loss",
                "cubic_feature_sampling", "emd"]:
        m = _Stub(f"extensions.{sub}")
        setattr(ext, sub, m)
        sys.modules[f"extensions.{sub}"] = m


def load_model(name: str = "pointr"):
    """Build the named model (pointr|adapointr) + load its PCN checkpoint onto
    CUDA (cached per name). Raises a clear error if repo/checkpoint is missing so
    the engine can fall back."""
    if name in _loaded:
        return _loaded[name]
    if name not in _MODELS:
        raise ValueError(f"unknown completion model {name!r}")
    cfg_rel, ckpt_rel = _MODELS[name]
    ckpt = _POINTR_HOME / ckpt_rel
    if not ckpt.is_file():
        raise FileNotFoundError(
            f"{name} checkpoint missing: {ckpt} (set POINTR_HOME / download it)")

    _install_shims()
    if str(_POINTR_HOME) not in sys.path:
        sys.path.insert(0, str(_POINTR_HOME))
    cwd = os.getcwd()
    try:
        os.chdir(_POINTR_HOME)  # config _base_ paths are repo-relative
        from tools import builder
        from utils.config import cfg_from_yaml_file
        cfg = cfg_from_yaml_file(cfg_rel)
        model = builder.model_builder(cfg.model)
        builder.load_model(model, str(ckpt))
    finally:
        os.chdir(cwd)
    _loaded[name] = model.cuda().eval()
    return _loaded[name]


def _yaw_canonicalize(pts: np.ndarray) -> np.ndarray:
    """Rotation about Y (gravity-up, preserved) that sends the object's dominant
    HORIZONTAL axis to +X. The PCN-trained model is orientation-sensitive but our
    objects face arbitrary directions, so we canonicalise the yaw before feeding
    it (and undo the rotation on the output). Returns Ry(phi) as a 3x3 (apply to
    row-vector points as pts @ R.T; invert with pts @ R)."""
    xz = pts[:, [0, 2]]
    cov = (xz.T @ xz) / max(len(xz), 1)
    _, vecs = np.linalg.eigh(cov)            # ascending; last col = principal dir
    major = vecs[:, -1]                       # [dx, dz]
    phi = float(np.arctan2(major[1], major[0]))
    c, s = np.cos(phi), np.sin(phi)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float32)


def complete_points(partial: np.ndarray, *, model: str = "pointr",
                    n_in: int = 2048, pca_align: bool | None = None) -> np.ndarray:
    """Complete a partial cloud -> dense cloud, in the SAME frame as the input
    (PoinTr normalises to a unit sphere internally; we denormalise back). The
    input should be object-local (recentred); output is too.

    pca_align (default on; env VID2SIM_PCA_ALIGN=0 to disable): yaw-canonicalise
    the object before completion to reduce the arbitrary-orientation variance the
    model is sensitive to, then rotate the result back."""
    import torch
    if pca_align is None:
        pca_align = os.environ.get("VID2SIM_PCA_ALIGN", "1") != "0"
    net = load_model(model)
    pts = np.asarray(partial, dtype=np.float32)
    centroid = pts.mean(axis=0)
    pts = pts - centroid
    R = _yaw_canonicalize(pts) if pca_align else np.eye(3, dtype=np.float32)
    pts = pts @ R.T                           # to canonical yaw
    scale = float(np.max(np.sqrt((pts ** 2).sum(axis=1)))) or 1.0
    pts = pts / scale
    # resample to the model's input size WITHOUT replacement when possible (the
    # randint approach duplicated points, which hurts the completion)
    rng = np.random.default_rng(0)
    if len(pts) >= n_in:
        idx = rng.choice(len(pts), n_in, replace=False)
    else:
        idx = rng.choice(len(pts), n_in, replace=True)
    inp = torch.from_numpy(pts[idx]).unsqueeze(0).cuda()
    with torch.no_grad():
        dense = net(inp)[-1].squeeze(0).cpu().numpy()
    dense = dense * scale @ R                  # undo yaw, back to input frame
    return dense + centroid
