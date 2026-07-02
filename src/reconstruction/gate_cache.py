"""Step-5 gate cache — skip per-object cloud accumulation on reruns.

The gate scores each object on a cloud accumulated over every visible frame
(a depth read + back-projection per frame) — ~20 minutes on the dense
2000-frame bundle even at --gate-stride 10 — yet the result is a pure function
of the bundle contents and the gate parameters. So run_assemble caches, per
(bundle, track_id, params), the accumulated cloud, the gating camera positions,
and the gate metrics in one small .npz (~1-5 MB/object) under
<bundle>/.gate_cache/. On a hit the accumulation is skipped entirely.

INVALIDATION is by construction: every parameter that shapes the result —
the tier gate bars, voxel size, gate stride, motion filter, and the bundle's
frame_count — is hashed INTO the filename (params_key), so any change is
automatically a miss. Stale entries are simply never read again (delete
.gate_cache/ to reclaim the few MB). --no-gate-cache bypasses reads and writes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

CACHE_DIR = ".gate_cache"

# Bump when the stored npz layout changes; keyed into the filename so old
# entries become misses instead of load errors.
_FORMAT = 1


def params_key(*, frame_count: int, gate_stride: int, voxel_size: float,
               motion_filter: bool, tier_params: dict) -> str:
    """Deterministic 12-hex digest of everything the gate result depends on.

    ``tier_params`` is cf.tier_params(tier) — the tier's gate bars (complete +
    keep thresholds, tsdf on/off, voxel), so re-tuning config/pipeline.yaml
    invalidates the cache without any manual step. frame_count catches a
    rebuilt/extended bundle that keeps the same path.
    """
    payload = {
        "format": _FORMAT,
        "frame_count": int(frame_count),
        "gate_stride": int(gate_stride),
        "voxel_size": float(voxel_size),
        "motion_filter": bool(motion_filter),
        "tier_params": {k: tier_params[k] for k in sorted(tier_params)},
    }
    blob = json.dumps(payload, sort_keys=True, default=float)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def _path(bundle_dir: Path | str, track_id: int, key: str) -> Path:
    return Path(bundle_dir) / CACHE_DIR / f"gate_{int(track_id):03d}_{key}.npz"


def store(bundle_dir: Path | str, track_id: int, key: str, *,
          cloud: np.ndarray, cams: np.ndarray, metrics: dict | None) -> Path:
    """Write one object's gate result. ``metrics`` may be None (an object whose
    cloud was too small to gate — cached so the rerun skips it just as fast)."""
    p = _path(bundle_dir, track_id, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp.npz")  # atomic-ish: never leave a torn npz behind
    np.savez_compressed(
        tmp,
        cloud=np.asarray(cloud, dtype=np.float64).reshape(-1, 3),
        cams=np.asarray(cams, dtype=np.float64).reshape(-1, 3),
        metrics=np.bytes_(json.dumps(metrics).encode()),
    )
    tmp.replace(p)
    return p


def load(bundle_dir: Path | str, track_id: int, key: str):
    """(cloud, cams, metrics|None) for a hit, or None for a miss/corrupt entry."""
    p = _path(bundle_dir, track_id, key)
    if not p.exists():
        return None
    try:
        with np.load(p) as z:
            metrics = json.loads(z["metrics"].item().decode())
            return z["cloud"], z["cams"], metrics
    except Exception:
        return None  # unreadable/torn entry -> recompute (and overwrite)
