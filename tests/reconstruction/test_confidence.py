"""Confidence-gate tests (Step 5, Build Order Phase 6).

Angular coverage and the routing logic are pure and tested directly. Surface
completeness needs Open3D (real point clouds); routing tests isolate the
threshold decision by monkeypatching the completeness estimate.
"""

from __future__ import annotations

import numpy as np
import pytest

from reconstruction import confidence as cf


# --- angular coverage ---------------------------------------------------
def test_angular_coverage_opposite_cameras_is_180():
    centroid = np.zeros(3)
    cams = np.array([[1.0, 0, 0], [-1.0, 0, 0]])
    assert abs(cf.angular_coverage_deg(centroid, cams) - 180.0) < 1e-6


def test_angular_coverage_orthogonal_cameras_is_90():
    centroid = np.zeros(3)
    cams = np.array([[1.0, 0, 0], [0, 1.0, 0]])
    assert abs(cf.angular_coverage_deg(centroid, cams) - 90.0) < 1e-6


def test_angular_coverage_takes_the_largest_pair():
    centroid = np.zeros(3)
    # three views spanning ~135 deg; the max pair, not the average, is reported
    cams = np.array([[1.0, 0, 0], [1.0, 1.0, 0], [-1.0, 1.0, 0]])
    assert abs(cf.angular_coverage_deg(centroid, cams) - 135.0) < 1e-6


def test_angular_coverage_single_view_is_zero():
    assert cf.angular_coverage_deg(np.zeros(3), np.array([[1.0, 0, 0]])) == 0.0


# --- surface completeness (real Open3D) ---------------------------------
def test_surface_completeness_box_shell_in_unit_range():
    # a dense point cloud on the surface of a 1 m cube -> ratio in (0, 1]
    rng = np.random.default_rng(0)
    faces = []
    for axis in range(3):
        for val in (0.0, 1.0):
            p = rng.random((400, 3))
            p[:, axis] = val
            faces.append(p)
    cloud = np.concatenate(faces, axis=0)
    ratio, observed, bbox = cf.surface_completeness(cloud, voxel_size=0.03)
    assert 0.0 < ratio <= 1.0
    assert observed > 0.0 and bbox > 0.0


def test_surface_completeness_empty_cloud_is_zero():
    assert cf.surface_completeness(np.empty((0, 3)), voxel_size=0.01) == (0.0, 0.0, 0.0)


def test_hull_completeness_higher_than_bbox_for_compact_object():
    # the shape-fair hull normaliser gives a higher (more reachable) ratio than
    # the bbox normaliser, which over-counts surface for compact shapes (fix Z-U)
    rng = np.random.default_rng(1)
    faces = []
    for axis in range(3):
        for val in (0.0, 1.0):
            p = rng.random((400, 3))
            p[:, axis] = val
            faces.append(p)
    cloud = np.concatenate(faces, axis=0)
    r_hull, _, hull_area = cf.surface_completeness(cloud, voxel_size=0.03, denom="hull")
    r_bbox, _, bbox_area = cf.surface_completeness(cloud, voxel_size=0.03, denom="bbox")
    assert 0.0 < r_bbox <= 1.0 and 0.0 < r_hull <= 1.0
    assert hull_area <= bbox_area  # hull never larger than its bbox -> higher ratio
    assert r_hull >= r_bbox


def test_unknown_denom_raises():
    with pytest.raises(ValueError):
        cf.surface_completeness(np.random.default_rng(0).random((50, 3)),
                                voxel_size=0.05, denom="nope")


# --- routing logic (isolated from Open3D) -------------------------------
def test_gate_routes_high_coverage_to_tsdf(monkeypatch):
    monkeypatch.setattr(cf, "surface_completeness", lambda c, *, voxel_size: (0.8, 1.0, 1.0))
    centroid_cams = np.array([[1.0, 0, 0], [-1.0, 0, 0]])  # 180 deg
    cloud = np.array([[0.0, 0, 0], [0.1, 0, 0]])
    out = cf.gate(cloud, centroid_cams, angular_deg=150, completeness=0.65, voxel_size=0.004)
    assert out["strategy"] == cf.TSDF
    assert out["completeness_ratio"] == 0.8


def test_gate_low_completeness_routes_to_generative(monkeypatch):
    monkeypatch.setattr(cf, "surface_completeness", lambda c, *, voxel_size: (0.3, 1.0, 1.0))
    cams = np.array([[1.0, 0, 0], [-1.0, 0, 0]])  # 180 deg (passes angular)
    cloud = np.array([[0.0, 0, 0], [0.1, 0, 0]])
    out = cf.gate(cloud, cams, angular_deg=150, completeness=0.65, voxel_size=0.004)
    assert out["strategy"] == cf.GENERATIVE  # high angle but completeness fails


def test_gate_low_angle_routes_to_generative(monkeypatch):
    monkeypatch.setattr(cf, "surface_completeness", lambda c, *, voxel_size: (0.9, 1.0, 1.0))
    cams = np.array([[1.0, 0, 0], [1.0, 0.1, 0]])  # ~6 deg spread (fails angular)
    cloud = np.array([[0.0, 0, 0], [0.1, 0, 0]])
    out = cf.gate(cloud, cams, angular_deg=150, completeness=0.65, voxel_size=0.004)
    assert out["strategy"] == cf.GENERATIVE  # high completeness but too few angles


def test_gate_empty_cloud_is_generative():
    out = cf.gate(np.empty((0, 3)), np.empty((0, 3)),
                  angular_deg=150, completeness=0.65, voxel_size=0.004)
    assert out["strategy"] == cf.GENERATIVE


# --- three-way routing (route() is pure; no Open3D) ---------------------
_HIGH = dict(complete_angular=90, complete_completeness=0.4,
             keep_angular=150, keep_completeness=0.8)


def test_route_below_complete_bar_is_generative():
    assert cf.route(80, 0.3, **_HIGH) == cf.GENERATIVE      # both below
    assert cf.route(120, 0.3, **_HIGH) == cf.GENERATIVE     # completeness below
    assert cf.route(80, 0.9, **_HIGH) == cf.GENERATIVE      # angular below


def test_route_between_bars_is_completion():
    # clears the complete bar, below the keep bar -> fill the gaps
    assert cf.route(121, 0.75, **_HIGH) == cf.COMPLETION
    assert cf.route(149, 0.79, **_HIGH) == cf.COMPLETION


def test_route_at_or_above_keep_bar_is_tsdf():
    assert cf.route(150, 0.8, **_HIGH) == cf.TSDF
    assert cf.route(175, 0.95, **_HIGH) == cf.TSDF


def test_route_collapses_to_binary_without_keep_bar():
    # no keep_* -> legacy binary: clearing the single bar == tsdf
    assert cf.route(121, 0.75, complete_angular=90, complete_completeness=0.4) == cf.TSDF
    assert cf.route(80, 0.3, complete_angular=90, complete_completeness=0.4) == cf.GENERATIVE


def test_route_collapses_to_binary_when_keep_equals_complete():
    # keep bar == complete bar -> middle band has zero width (the toggle)
    eq = dict(complete_angular=90, complete_completeness=0.4,
              keep_angular=90, keep_completeness=0.4)
    assert cf.route(121, 0.75, **eq) == cf.TSDF      # clearing the bar clears both
    assert cf.route(80, 0.3, **eq) == cf.GENERATIVE


def test_gate_object_three_way_reads_keep_bars(monkeypatch):
    # tier 4 config carries keep_* -> a mid-quality object routes to completion
    monkeypatch.setattr(cf, "surface_completeness",
                        lambda c, *, voxel_size: (0.75, 1.0, 1.0))
    cams = np.array([[1.0, 0, 0], [-1.0, 0, 0]])  # 180 deg (clears 150 keep-angular)
    cloud = np.array([[0.0, 0, 0], [0.1, 0, 0]])
    out = cf.gate_object(cloud, cams, tier=4)
    # 180deg >= keep_angular 150 but completeness 0.75 < keep_completeness 0.80
    assert out["strategy"] == cf.COMPLETION


# --- tier config wiring -------------------------------------------------
def test_tier_params_reads_pipeline_yaml():
    p = cf.tier_params(2)
    assert p["tsdf"] is True
    # recalibrated 2026-06-27 against real Replica distributions (was 150/0.65)
    assert p["angular_deg"] == 112   # raised 2026-07-02 (user: image-to-3D first)
    assert p["completeness"] == 0.48
    assert p["voxel_size_m"] == 0.004


def test_tier_params_exposes_keep_bars():
    p = cf.tier_params(4)
    assert p["angular_deg"] == 100 and p["completeness"] == 0.42  # raised 2026-07-02
    assert p["keep_angular_deg"] == 150 and p["keep_completeness"] == 0.85


def test_tier1_is_all_generative_without_scoring():
    p = cf.tier_params(1)
    assert p["tsdf"] is False
    out = cf.gate_object(np.array([[0.0, 0, 0]]), np.array([[1.0, 0, 0]]), tier=1)
    assert out["strategy"] == cf.GENERATIVE
    assert out["angular_coverage_deg"] is None  # not scored
