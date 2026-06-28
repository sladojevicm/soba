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
_CKPT = _POINTR_HOME / "pretrained" / "PoinTr_PCN.pth"
_CONFIG = "cfgs/PCN_models/PoinTr.yaml"  # relative to POINTR_HOME

_model = None  # lazy singleton (loading is slow; do it once)


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


def load_model():
    """Build PoinTr + load the PCN checkpoint onto CUDA (cached). Raises a clear
    error if the repo/checkpoint isn't present so the engine can fall back."""
    global _model
    if _model is not None:
        return _model
    import torch
    if not _CKPT.is_file():
        raise FileNotFoundError(
            f"PoinTr checkpoint missing: {_CKPT} (set POINTR_HOME / download it)")

    _install_shims()
    if str(_POINTR_HOME) not in sys.path:
        sys.path.insert(0, str(_POINTR_HOME))
    cwd = os.getcwd()
    try:
        os.chdir(_POINTR_HOME)  # config _base_ paths are repo-relative
        from tools import builder
        from utils.config import cfg_from_yaml_file
        cfg = cfg_from_yaml_file(_CONFIG)
        model = builder.model_builder(cfg.model)
        builder.load_model(model, str(_CKPT))
    finally:
        os.chdir(cwd)
    _model = model.cuda().eval()
    return _model


def complete_points(partial: np.ndarray, *, n_in: int = 2048) -> np.ndarray:
    """Complete a partial cloud -> dense cloud, in the SAME frame as the input
    (PoinTr normalises to a unit sphere internally; we denormalise back). The
    input should be object-local (recentred); output is too."""
    import torch
    model = load_model()
    pts = np.asarray(partial, dtype=np.float32)
    centroid = pts.mean(axis=0)
    pts = pts - centroid
    scale = float(np.max(np.sqrt((pts ** 2).sum(axis=1)))) or 1.0
    pts = pts / scale
    # resample to the model's input size (pad-by-repeat if sparse)
    rng = np.random.default_rng(0)
    idx = rng.integers(0, len(pts), n_in)
    inp = torch.from_numpy(pts[idx]).unsqueeze(0).cuda()
    with torch.no_grad():
        dense = model(inp)[-1].squeeze(0).cpu().numpy()
    return dense * scale + centroid  # back to the input frame
