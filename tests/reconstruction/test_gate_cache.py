"""Step-5 gate cache tests: key sensitivity (auto-invalidation) + roundtrip.

The cache's correctness rests on two guarantees: (1) every parameter that
shapes the gate result changes the key — so a parameter change can never serve
a stale entry — and (2) a stored entry loads back bit-identical (cloud, cams,
metrics), including the metrics=None "too small to gate" marker.
"""

from __future__ import annotations

import numpy as np

from reconstruction import gate_cache

_TIER = {"tsdf": True, "voxel_size_m": 0.008, "angular_deg": 100.0,
         "completeness": 0.42, "keep_angular_deg": 112.0, "keep_completeness": 0.48}
_BASE = dict(frame_count=100, gate_stride=4, voxel_size=0.008,
             motion_filter=False, tier_params=_TIER)


def test_params_key_is_deterministic_and_param_sensitive():
    k = gate_cache.params_key(**_BASE)
    assert k == gate_cache.params_key(**_BASE)  # stable across calls
    # EVERY keyed parameter must invalidate: frame_count, stride, voxel,
    # motion filter, and any tier gate bar.
    for change in (dict(frame_count=2000), dict(gate_stride=10),
                   dict(voxel_size=0.01), dict(motion_filter=True),
                   dict(tier_params={**_TIER, "keep_completeness": 0.45})):
        assert gate_cache.params_key(**{**_BASE, **change}) != k, change


def test_store_load_roundtrip(tmp_path):
    key = gate_cache.params_key(**_BASE)
    cloud = np.random.default_rng(0).normal(size=(500, 3))
    cams = np.random.default_rng(1).normal(size=(25, 3))
    metrics = {"angular_coverage_deg": 120.8, "completeness_ratio": 0.758,
               "strategy": "tsdf"}
    gate_cache.store(tmp_path, 7, key, cloud=cloud, cams=cams, metrics=metrics)
    hit = gate_cache.load(tmp_path, 7, key)
    assert hit is not None
    c2, cams2, m2 = hit
    np.testing.assert_array_equal(c2, cloud)
    np.testing.assert_array_equal(cams2, cams)
    assert m2 == metrics
    # entries live under <bundle>/.gate_cache with the key in the filename
    assert (tmp_path / gate_cache.CACHE_DIR / f"gate_007_{key}.npz").exists()


def test_miss_on_other_key_track_or_corrupt_entry(tmp_path):
    key = gate_cache.params_key(**_BASE)
    gate_cache.store(tmp_path, 7, key, cloud=np.zeros((4, 3)),
                     cams=np.zeros((2, 3)), metrics=None)
    # metrics=None (too-small cloud) roundtrips as an explicit None
    cloud, cams, metrics = gate_cache.load(tmp_path, 7, key)
    assert metrics is None and len(cloud) == 4
    # different params -> different key -> miss (auto-invalidation)
    other = gate_cache.params_key(**{**_BASE, "frame_count": 2000})
    assert gate_cache.load(tmp_path, 7, other) is None
    # different track -> miss
    assert gate_cache.load(tmp_path, 8, key) is None
    # torn/corrupt file -> miss, not a crash
    p = tmp_path / gate_cache.CACHE_DIR / f"gate_007_{key}.npz"
    p.write_bytes(b"not an npz")
    assert gate_cache.load(tmp_path, 7, key) is None
