"""Tests for the ground-truth evaluation logic (scripts/evaluate_scene.py).

Pure synthetic-geometry tests: box point clouds and hand-built object dicts,
no Replica download and no scene on disk. Locks the matching rules, the
Chamfer/F-score math, the global-alignment estimate, and the correctness-score
formula (weights + renormalisation).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "evaluate_scene",
    Path(__file__).resolve().parents[1] / "scripts" / "evaluate_scene.py",
)
ev = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ev)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def box_points(center, extents, n=6000, seed=0):
    """Points uniformly on the surface of an axis-aligned box."""
    rng = np.random.default_rng(seed)
    c = np.asarray(center, float)
    e = np.asarray(extents, float) / 2.0
    pts = rng.uniform(-1, 1, size=(n, 3))
    # project each point to a random face
    face = rng.integers(0, 3, size=n)
    sign = rng.choice([-1.0, 1.0], size=n)
    pts[np.arange(n), face] = sign
    return c + pts * e


def obj(cls, center, extents, points=None):
    c = np.asarray(center, float)
    e = np.asarray(extents, float)
    return {
        "id": f"{cls}_x", "class": cls, "centroid": c,
        "bbox_min": c - e / 2, "bbox_max": c + e / 2,
        "points": points,
    }


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def test_quat_identity_and_yaw():
    assert np.allclose(ev.quat_to_mat([0, 0, 0, 1]), np.eye(3))
    # 90 deg about Y: [0, sin45, 0, cos45]
    R = ev.quat_to_mat([0, np.sin(np.pi / 4), 0, np.cos(np.pi / 4)])
    assert np.allclose(R @ np.array([1, 0, 0]), [0, 0, -1], atol=1e-9)


def test_chamfer_fscore_identical_points():
    pts = box_points([0, 0, 0], [1, 1, 1])
    cham, f = ev.chamfer_and_fscore(pts, pts.copy())
    assert cham == 0.0
    assert f == 1.0


def test_fscore_degrades_with_offset():
    a = box_points([0, 0, 0], [1, 1, 1])
    cham0, f0 = ev.chamfer_and_fscore(a, box_points([0, 0, 0], [1, 1, 1], seed=1))
    # 3 cm shift: still mostly within the 5 cm tau
    cham3, f3 = ev.chamfer_and_fscore(a + [0.03, 0, 0],
                                      box_points([0, 0, 0], [1, 1, 1], seed=1))
    # 30 cm shift: parallel faces still slide along themselves, so F only sags
    cham30, f30 = ev.chamfer_and_fscore(a + [0.30, 0, 0],
                                        box_points([0, 0, 0], [1, 1, 1], seed=1))
    # fully separated boxes: no point anywhere near the other set
    cham_far, f_far = ev.chamfer_and_fscore(a + 1.5,
                                            box_points([0, 0, 0], [1, 1, 1], seed=1))
    assert f0 > 0.9
    assert f3 > 0.5
    assert f3 > f30 > f_far
    assert f_far < 0.01
    assert cham0 < cham3 < cham30 < cham_far


def test_fscore_penalises_wrong_size():
    gt = box_points([0, 0, 0], [1.0, 1.0, 1.0])
    half = box_points([0, 0, 0], [0.5, 0.5, 0.5])
    _, f_same = ev.chamfer_and_fscore(box_points([0, 0, 0], [1, 1, 1], seed=2), gt)
    _, f_half = ev.chamfer_and_fscore(half, gt)
    assert f_half < f_same
    assert f_half < 0.5


def test_aabb_iou():
    assert ev.aabb_iou([0, 0, 0], [1, 1, 1], [0, 0, 0], [1, 1, 1]) == pytest.approx(1.0)
    assert ev.aabb_iou([0, 0, 0], [1, 1, 1], [2, 2, 2], [3, 3, 3]) == 0.0
    # half-overlap in x only: inter 0.5, union 1.5
    assert ev.aabb_iou([0, 0, 0], [1, 1, 1], [0.5, 0, 0], [1.5, 1, 1]) == \
        pytest.approx(0.5 / 1.5)


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------
def test_match_same_class_within_threshold():
    shipped = [obj("chair", [0.3, 0, 0], [0.5, 0.9, 0.5])]
    gt = [obj("chair", [0, 0, 0], [0.5, 0.9, 0.5])]
    m, un_s, un_g = ev.match_objects(shipped, gt)
    assert len(m) == 1 and not un_s and not un_g
    assert m[0][2] == pytest.approx(0.3)


def test_no_match_across_classes_or_far():
    shipped = [obj("chair", [0, 0, 0], [0.5, 0.9, 0.5]),
               obj("couch", [5, 0, 0], [2, 0.8, 1])]
    gt = [obj("dining table", [0, 0, 0], [1.5, 0.7, 0.9]),   # same spot, wrong class
          obj("couch", [0, 0, 0], [2, 0.8, 1])]              # right class, 5 m away
    m, un_s, un_g = ev.match_objects(shipped, gt)
    assert m == []
    assert un_s == [0, 1] and un_g == [0, 1]


def test_iou_rescues_large_offset_couch():
    # centroid 0.9 m apart (> 0.75) but boxes overlap heavily
    shipped = [obj("couch", [0.9, 0, 0], [2.5, 0.8, 1.0])]
    gt = [obj("couch", [0, 0, 0], [2.5, 0.8, 1.0])]
    m, _, _ = ev.match_objects(shipped, gt)
    assert len(m) == 1


def test_hungarian_prefers_nearest_assignment():
    shipped = [obj("chair", [0.0, 0, 0], [0.5, 0.9, 0.5]),
               obj("chair", [1.0, 0, 0], [0.5, 0.9, 0.5])]
    gt = [obj("chair", [0.1, 0, 0], [0.5, 0.9, 0.5]),
          obj("chair", [1.1, 0, 0], [0.5, 0.9, 0.5])]
    m, un_s, un_g = ev.match_objects(shipped, gt)
    assert sorted((i, j) for i, j, _ in m) == [(0, 0), (1, 1)]


def test_extra_shipped_hits_precision_not_recall():
    shipped = [obj("chair", [0, 0, 0], [0.5, 0.9, 0.5]),
               obj("chair", [3, 0, 0], [0.5, 0.9, 0.5])]  # hallucinated
    gt = [obj("chair", [0, 0, 0], [0.5, 0.9, 0.5])]
    m, un_s, un_g = ev.match_objects(shipped, gt)
    assert len(m) == 1 and un_s == [1] and un_g == []
    precision = len(m) / len(shipped)
    recall = len(m) / len(gt)
    assert precision == 0.5 and recall == 1.0


# --------------------------------------------------------------------------
# global alignment
# --------------------------------------------------------------------------
def test_global_translation_recovered():
    offset = np.array([0.2, -0.05, 0.1])
    gt = [obj("chair", [0, 0, 0], [0.5, 0.9, 0.5]),
          obj("couch", [2, 0, 1], [2, 0.8, 1]),
          obj("dining table", [-1, 0, 2], [1.5, 0.7, 0.9])]
    shipped = [obj(g["class"], g["centroid"] + offset,
                   g["bbox_max"] - g["bbox_min"]) for g in gt]
    a = ev.estimate_global_transform(shipped, gt)
    assert a["n_pairs"] == 3
    assert np.allclose(a["t"], offset, atol=1e-9)
    assert np.allclose(a["R"], np.eye(3))
    assert a["mean_residual_m"] == pytest.approx(0.0, abs=1e-9)


def test_global_alignment_identity_without_pairs():
    a = ev.estimate_global_transform(
        [obj("chair", [0, 0, 0], [1, 1, 1])],
        [obj("couch", [0, 0, 0], [1, 1, 1])])
    assert a["n_pairs"] == 0
    assert np.allclose(a["t"], 0) and np.allclose(a["R"], np.eye(3))


# --------------------------------------------------------------------------
# score formula
# --------------------------------------------------------------------------
def test_score_full_marks():
    s = ev.correctness_score(1.0, 1.0, 1.0, 1.0)
    assert s["value"] == 100.0


def test_score_weights():
    # recall alone at weight 0.40
    s = ev.correctness_score(1.0, 0.0, 0.0, 0.0)
    assert s["value"] == pytest.approx(40.0)
    s = ev.correctness_score(0.0, 1.0, 0.0, 0.0)
    assert s["value"] == pytest.approx(20.0)
    s = ev.correctness_score(0.0, 0.0, 1.0, 0.0)
    assert s["value"] == pytest.approx(30.0)
    s = ev.correctness_score(0.0, 0.0, 0.0, 1.0)
    assert s["value"] == pytest.approx(10.0)


def test_score_renormalises_missing_components():
    # no pose term: weights renormalise over 0.9
    s = ev.correctness_score(1.0, 1.0, 1.0, None)
    assert s["value"] == pytest.approx(100.0)
    assert s["components"]["pose"] is None
    s2 = ev.correctness_score(1.0, 0.0, 0.0, None)
    assert s2["value"] == pytest.approx(100 * 0.40 / 0.90, abs=0.1)


def test_pose_term_clamps():
    assert ev._pose_term({"available": True, "ate_rmse_m": 0.0}) == 1.0
    assert ev._pose_term({"available": True, "ate_rmse_m": 0.05}) == pytest.approx(0.5)
    assert ev._pose_term({"available": True, "ate_rmse_m": 0.5}) == 0.0
    assert ev._pose_term({"available": False}) is None


# --------------------------------------------------------------------------
# end-to-end on a synthetic GT scene (tiny quad-faced semantic PLY, one box)
# --------------------------------------------------------------------------
def _write_box_glb(path, extents):
    trimesh = pytest.importorskip("trimesh")
    trimesh.creation.box(extents=extents).export(path)


def _write_semantic_ply(path, boxes):
    """boxes: [(object_id, center_ZUP, extents)] -> quad-faced mesh_semantic.ply
    with a per-face object_id, in Replica's Z-up frame."""
    trimesh = pytest.importorskip("trimesh")
    plyfile = pytest.importorskip("plyfile")
    import numpy as _np

    verts, quads, oids = [], [], []
    for oid, center, extents in boxes:
        b = trimesh.creation.box(extents=extents)
        base = len(verts)
        verts.extend((_np.asarray(b.vertices) + center).tolist())
        # emit each triangle as a degenerate quad (v0,v1,v2,v2) to exercise
        # the n-gon fan triangulation path exactly like Replica's quads
        for f in b.faces:
            quads.append([base + f[0], base + f[1], base + f[2], base + f[2]])
            oids.append(oid)
    v = _np.array([tuple(p) for p in verts],
                  dtype=[("x", "f4"), ("y", "f4"), ("z", "f4")])
    f = _np.empty(len(quads),
                  dtype=[("vertex_indices", "O"), ("object_id", "i4")])
    f["vertex_indices"] = [_np.array(q, dtype=_np.int32) for q in quads]
    f["object_id"] = oids
    plyfile.PlyData([
        plyfile.PlyElement.describe(v, "vertex"),
        plyfile.PlyElement.describe(f, "face"),
    ], text=False).write(str(path))


def test_end_to_end_synthetic_scene(tmp_path):
    pytest.importorskip("trimesh")
    pytest.importorskip("plyfile")
    import json

    # --- GT: a chair and a table, Replica Z-up world -----------------------
    # scene frame is Y-up: (x, y, z)_yup = (x, z, -y)_zup
    gt_dir = tmp_path / "gt"
    (gt_dir / "habitat").mkdir(parents=True)
    _write_semantic_ply(gt_dir / "habitat" / "mesh_semantic.ply", [
        (7, [1.0, 2.0, 0.5], [0.5, 0.5, 0.9]),    # chair @ yup (1.0, 0.5, -2.0)
        (8, [-1.0, 0.0, 0.35], [1.4, 0.8, 0.7]),  # table @ yup (-1.0, 0.35, 0.0)
    ])
    (gt_dir / "habitat" / "info_semantic.json").write_text(json.dumps({
        "classes": [{"id": 20, "name": "chair"}, {"id": 80, "name": "table"}],
        "objects": [{"id": 7, "class_id": 20}, {"id": 8, "class_id": 80}],
    }))

    # --- shipped scene: the chair only, 10 cm off --------------------------
    scene_dir = tmp_path / "scene"
    (scene_dir / "objects" / "chair_00").mkdir(parents=True)
    _write_box_glb(scene_dir / "objects" / "chair_00" / "mesh.glb",
                   [0.5, 0.9, 0.5])  # Y-up extents (x, z-height, -y-depth)
    (scene_dir / "scene.json").write_text(json.dumps({
        "version": "2.0",
        "objects": [{
            "id": "chair_00", "class": "chair",
            "transform": {"translation": [1.1, 0.5, -2.0],
                          "rotation_quat": [0, 0, 0, 1], "scale": 1.0},
        }],
    }))

    result = ev.evaluate(
        scene_dir=scene_dir, room="synthetic", bundle_dir=tmp_path / "nobundle",
        gt_scene_dir=gt_dir, gt_traj=tmp_path / "notraj",
        dist_thresh=0.75, iou_thresh=0.10, rigid=False, pose_only=False)

    det = result["detection"]
    assert det["gt_in_scope"] == 2 and det["shipped"] == 1
    assert det["matched"] == 1
    assert det["recall"] == pytest.approx(0.5)
    assert det["precision"] == pytest.approx(1.0)
    assert result["pose"]["available"] is False  # no bundle/traj given

    chair = next(o for o in result["objects"] if o["id"] == "chair_00")
    assert chair["matched"] and chair["gt_class"] == "chair"
    # same box, translation-only alignment -> near-perfect geometry
    assert chair["fscore_5cm"] > 0.9
    assert all(abs(r - 1) < 0.05 for r in chair["dim_ratio_xyz"])
    assert [m["class"] for m in result["missed_gt"]] == ["dining table"]

    # global alignment recovered the systematic 10 cm x-offset
    assert abs(result["alignment"]["translation_m"][0] - 0.1) < 0.02
    # score components present and sane
    assert 0 < result["score"]["value"] <= 100
    assert result["score"]["components"]["pose"] is None
