#!/usr/bin/env python3
"""Ground-truth evaluation of an assembled scene against Replica semantics.

Given an assembled scene (out/scene_<room>_<tier>/scene.json), the perception
bundle it was built from, and the Replica scene assets with semantics
(mesh_semantic.ply + info_semantic.json, Habitat format), writes
<scene_dir>/eval.json with four blocks:

  pose       ATE (RMSE / mean / median) of the trajectory the pipeline actually
             used (the bundle's poses.json) against the dataset ground-truth
             trajectory (traj_w_c.txt, fetched by scripts/fetch_replica_gt_traj.py
             and rotated Z-up -> Y-up exactly like perception.dataset_reader).
             NOTE (2026-07-05 investigation): for the Replica builds the
             pipeline consumed the dataset GT poses directly (bundle poses.json
             is byte-identical to traj_w_c.txt; nothing in the build path calls
             slam.estimate_poses). The ATE is then 0 BY CONSTRUCTION and is
             flagged as such via pose.source = "dataset_ground_truth".

  detection  Shipped objects vs GT instances. GT classes come from
             info_semantic.json mapped through config/replica_eval_class_map.yaml
             (eval-only map; the pipeline's own bundle-time map in
             config/coco_class_map.yaml defines which COCO classes are "in
             scope"). A match = same mapped COCO class AND (centroid distance
             <= --dist-thresh (default 0.75 m) OR world-AABB IoU >= --iou-thresh),
             solved per class with the Hungarian algorithm on centroid distance.
             Precision counts every shipped object against the pipeline
             (unmatched shipped = false positive); recall is over in-scope GT.
             Out-of-scope GT (tv, potted plant, ... — classes the pipeline's
             detector never mapped) is reported separately and does not enter
             the score.

  objects    Per matched object: symmetric Chamfer distance and F-score@5cm
             (~10k sampled points per mesh, translation-only alignment after
             one global per-room transform estimated from all matched
             centroids), per-axis bbox-extent ratios, centroid error.

  score      0-100 correctness:
                 score = 100 * (0.40 * recall_in_scope
                              + 0.20 * precision
                              + 0.30 * mean F-score@5cm over matched objects
                              + 0.10 * pose_term)
             pose_term = clamp(1 - ATE_RMSE / 0.10 m, 0, 1). If a component is
             unavailable (e.g. no pose GT) its weight is dropped and the rest
             renormalised; the formula actually used is embedded in eval.json.

Frame note: scene space equals the bundle's world space (Y-up-rotated Replica
world), so GT meshes rotated with the same Z-up->Y-up matrix should land on the
shipped objects directly. A residual systematic offset is still estimated as ONE
global transform per room (translation-only by default, --rigid for full Kabsch)
from mutually-nearest same-class centroid pairs, and reported under `alignment`.

Usage:
  python scripts/evaluate_scene.py --scene out/scene_office_3_t2 --room office_3
    [--bundle .../bundles_dense/office_3] [--gt-scene .../scenes/office_3]
    [--gt-traj .../gt_traj/office_3_traj_w_c.txt] [--pose-only] [--out ...]

Exits 2 with a clear message when required GT assets are missing.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

import numpy as np
import yaml

_REPO = Path(__file__).resolve().parents[1]
_DATA = Path.home() / "projects/soba/data/replica"

# Z-up -> Y-up, identical to perception.dataset_reader._REPLICA_ZUP_TO_YUP.
ZUP_TO_YUP = np.array(
    [[1.0, 0.0, 0.0, 0.0],
     [0.0, 0.0, 1.0, 0.0],
     [0.0, -1.0, 0.0, 0.0],
     [0.0, 0.0, 0.0, 1.0]],
    dtype=np.float64,
)

SCORE_WEIGHTS = {"recall": 0.40, "precision": 0.20, "fscore": 0.30, "pose": 0.10}
FSCORE_TAU_M = 0.05     # F-score distance threshold (5 cm)
N_SAMPLE = 10_000       # points sampled per mesh for Chamfer / F-score
ATE_FULL_MARKS_M = 0.10 # pose_term hits 0 at 10 cm ATE RMSE


# --------------------------------------------------------------------------
# geometry primitives (pure, unit-tested in tests/test_eval_scene.py)
# --------------------------------------------------------------------------
def quat_to_mat(q) -> np.ndarray:
    """[x,y,z,w] quaternion -> 3x3 rotation matrix."""
    x, y, z, w = (float(v) for v in q)
    n = x * x + y * y + z * z + w * w
    if n < 1e-12:
        return np.eye(3)
    s = 2.0 / n
    return np.array([
        [1 - s * (y * y + z * z), s * (x * y - z * w), s * (x * z + y * w)],
        [s * (x * y + z * w), 1 - s * (x * x + z * z), s * (y * z - x * w)],
        [s * (x * z - y * w), s * (y * z + x * w), 1 - s * (x * x + y * y)],
    ])


def chamfer_and_fscore(pred: np.ndarray, gt: np.ndarray,
                       tau: float = FSCORE_TAU_M) -> tuple[float, float]:
    """Symmetric Chamfer distance (mean, metres) and F-score@tau between two
    point sets. F-score: precision = fraction of pred within tau of gt,
    recall = fraction of gt within tau of pred, F = harmonic mean."""
    from scipy.spatial import cKDTree

    d_pg = cKDTree(gt).query(pred, workers=-1)[0]
    d_gp = cKDTree(pred).query(gt, workers=-1)[0]
    chamfer = float(d_pg.mean() + d_gp.mean()) / 2.0
    p = float((d_pg <= tau).mean())
    r = float((d_gp <= tau).mean())
    f = 0.0 if (p + r) == 0 else 2 * p * r / (p + r)
    return chamfer, f


def aabb_iou(min_a, max_a, min_b, max_b) -> float:
    """IoU of two axis-aligned boxes given (min,max) corners."""
    min_a, max_a = np.asarray(min_a, float), np.asarray(max_a, float)
    min_b, max_b = np.asarray(min_b, float), np.asarray(max_b, float)
    inter = np.clip(np.minimum(max_a, max_b) - np.maximum(min_a, min_b), 0, None)
    vi = float(np.prod(inter))
    va = float(np.prod(np.clip(max_a - min_a, 0, None)))
    vb = float(np.prod(np.clip(max_b - min_b, 0, None)))
    return vi / (va + vb - vi) if (va + vb - vi) > 0 else 0.0


def kabsch(P: np.ndarray, Q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rigid R, t (no scale) minimising ||R P + t - Q||."""
    muP, muQ = P.mean(0), Q.mean(0)
    H = (P - muP).T @ (Q - muQ)
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1.0, 1.0, d]) @ U.T
    return R, muQ - R @ muP


def estimate_global_transform(shipped: list[dict], gt: list[dict],
                              provisional_dist: float = 1.5,
                              rigid: bool = False) -> dict:
    """ONE per-room transform mapping GT centroids into scene space, from
    mutually-nearest same-class pairs closer than `provisional_dist`.
    Translation-only by default (scene space and GT space share the Y-up
    rotation, so rotation should be identity); `rigid` enables full Kabsch
    when >= 3 pairs exist. Returns {"R": 3x3, "t": 3, "n_pairs", "method"}."""
    pairs = []
    for i, s in enumerate(shipped):
        cands = [(np.linalg.norm(s["centroid"] - g["centroid"]), j)
                 for j, g in enumerate(gt) if g["class"] == s["class"]]
        if not cands:
            continue
        d, j = min(cands)
        # mutual nearest: shipped i must also be gt j's nearest same-class
        back = [(np.linalg.norm(gt[j]["centroid"] - s2["centroid"]), i2)
                for i2, s2 in enumerate(shipped) if s2["class"] == gt[j]["class"]]
        if back and min(back)[1] == i and d <= provisional_dist:
            pairs.append((i, j))

    R, t = np.eye(3), np.zeros(3)
    method = "identity (no provisional pairs)"
    if pairs:
        P = np.array([gt[j]["centroid"] for _, j in pairs])       # GT -> scene
        Q = np.array([shipped[i]["centroid"] for i, _ in pairs])
        if rigid and len(pairs) >= 3:
            R, t = kabsch(P, Q)
            method = f"kabsch rigid ({len(pairs)} pairs)"
        else:
            t = np.median(Q - P, axis=0)
            method = f"translation-only median offset ({len(pairs)} pairs)"
    resid = None
    if pairs:
        P = np.array([gt[j]["centroid"] for _, j in pairs])
        Q = np.array([shipped[i]["centroid"] for i, _ in pairs])
        resid = float(np.linalg.norm((P @ R.T + t) - Q, axis=1).mean())
    return {"R": R, "t": t, "n_pairs": len(pairs), "method": method,
            "mean_residual_m": resid}


def match_objects(shipped: list[dict], gt: list[dict],
                  dist_thresh: float = 0.75, iou_thresh: float = 0.10):
    """Hungarian matching per class on centroid distance.

    Each item needs: class, centroid (3,), bbox_min (3,), bbox_max (3,).
    A pairing is accepted when centroid distance <= dist_thresh OR the
    world-AABB IoU >= iou_thresh (large furniture can overlap heavily while
    its centroid is off by more than the threshold).
    Returns (matches [(i_shipped, j_gt, dist)], unmatched_shipped, unmatched_gt).
    """
    from scipy.optimize import linear_sum_assignment

    matches: list[tuple[int, int, float]] = []
    matched_s: set[int] = set()
    matched_g: set[int] = set()
    classes = sorted({s["class"] for s in shipped} | {g["class"] for g in gt})
    for cls in classes:
        si = [i for i, s in enumerate(shipped) if s["class"] == cls]
        gj = [j for j, g in enumerate(gt) if g["class"] == cls]
        if not si or not gj:
            continue
        D = np.array([[np.linalg.norm(shipped[i]["centroid"] - gt[j]["centroid"])
                       for j in gj] for i in si])
        BIG = 1e6
        cost = D.copy()
        for a, i in enumerate(si):
            for b, j in enumerate(gj):
                ok = D[a, b] <= dist_thresh or aabb_iou(
                    shipped[i]["bbox_min"], shipped[i]["bbox_max"],
                    gt[j]["bbox_min"], gt[j]["bbox_max"]) >= iou_thresh
                if not ok:
                    cost[a, b] = BIG
        rows, cols = linear_sum_assignment(cost)
        for a, b in zip(rows, cols):
            if cost[a, b] < BIG:
                matches.append((si[a], gj[b], float(D[a, b])))
                matched_s.add(si[a])
                matched_g.add(gj[b])
    unmatched_s = [i for i in range(len(shipped)) if i not in matched_s]
    unmatched_g = [j for j in range(len(gt)) if j not in matched_g]
    return matches, unmatched_s, unmatched_g


def correctness_score(recall, precision, mean_fscore, pose_term) -> dict:
    """0-100 score; None components drop out and the weights renormalise."""
    comps = {"recall": recall, "precision": precision,
             "fscore": mean_fscore, "pose": pose_term}
    used = {k: v for k, v in comps.items() if v is not None}
    wsum = sum(SCORE_WEIGHTS[k] for k in used)
    value = 100.0 * sum(SCORE_WEIGHTS[k] * v for k, v in used.items()) / wsum \
        if wsum > 0 else 0.0
    formula = " + ".join(f"{SCORE_WEIGHTS[k]/wsum:.3f}*{k}" for k in sorted(used))
    return {
        "value": round(float(value), 1),
        "formula": f"100 * ({formula})"
                   f" [pose_term = clamp(1 - ATE_RMSE/{ATE_FULL_MARKS_M}m, 0, 1);"
                   " base weights recall 0.40 / precision 0.20 / fscore 0.30 /"
                   " pose 0.10, renormalised over available components]",
        "components": {k: (round(float(v), 4) if v is not None else None)
                       for k, v in comps.items()},
    }


# --------------------------------------------------------------------------
# loaders
# --------------------------------------------------------------------------
def load_shipped_objects(scene_dir: Path) -> list[dict]:
    """scene.json objects -> world-space centroid/AABB/sampled points."""
    import trimesh

    scene = json.loads((scene_dir / "scene.json").read_text())
    out = []
    for o in scene.get("objects", []):
        mesh_path = scene_dir / "objects" / o["id"] / "mesh.glb"
        if not mesh_path.is_file():
            print(f"  WARNING: {o['id']} has no mesh.glb on disk; "
                  "counted for detection only", file=sys.stderr)
            mesh = None
        else:
            mesh = trimesh.load(mesh_path, force="mesh")
        t = o["transform"]
        R = quat_to_mat(t.get("rotation_quat", [0, 0, 0, 1]))
        s = float(t.get("scale", 1.0))
        trans = np.asarray(t["translation"], dtype=np.float64)
        if mesh is not None:
            verts = np.asarray(mesh.vertices, dtype=np.float64) * s @ R.T + trans
            world = mesh.copy()
            world.vertices = verts
            pts = np.asarray(world.sample(N_SAMPLE), dtype=np.float64) \
                if len(world.faces) else verts
            bmin, bmax = verts.min(0), verts.max(0)
            centroid = (bmin + bmax) / 2.0
        else:
            pts = None
            centroid = trans
            bmin = bmax = trans
        out.append({
            "id": o["id"], "class": o["class"], "centroid": centroid,
            "bbox_min": bmin, "bbox_max": bmax, "points": pts,
        })
    return out


def _find_semantic_assets(gt_scene_dir: Path) -> tuple[Path, Path]:
    """Locate mesh_semantic.ply + info/semantic json under a GT scene dir."""
    mesh = None
    for cand in ("habitat/mesh_semantic.ply", "mesh_semantic.ply"):
        if (gt_scene_dir / cand).is_file():
            mesh = gt_scene_dir / cand
            break
    if mesh is None:
        hits = sorted(gt_scene_dir.rglob("mesh_semantic.ply"))
        mesh = hits[0] if hits else None
    info = None
    for name in ("info_semantic.json", "semantic.json"):
        for cand in (gt_scene_dir / "habitat" / name, gt_scene_dir / name):
            if cand.is_file():
                info = cand
                break
        if info is None:
            hits = sorted(gt_scene_dir.rglob(name))
            if hits:
                info = hits[0]
        if info is not None:
            break
    if mesh is None or info is None:
        raise FileNotFoundError(
            f"GT semantic assets not found under {gt_scene_dir} "
            "(need mesh_semantic.ply + info_semantic.json/semantic.json, "
            "Habitat-format Replica)")
    return mesh, info


def load_eval_class_map(path: Path | None = None) -> dict[str, str]:
    p = path or (_REPO / "config" / "replica_eval_class_map.yaml")
    data = yaml.safe_load(p.read_text())
    return {str(k).strip().lower(): str(v) for k, v in
            (data.get("replica_to_coco") or {}).items()}


def in_scope_classes() -> set[str]:
    """COCO classes the pipeline itself could ship for Replica input — the
    non-default values of config/coco_class_map.yaml's `replica:` section
    (the map used when the bundles were built)."""
    data = yaml.safe_load((_REPO / "config" / "coco_class_map.yaml").read_text())
    return {str(v) for v in (data.get("replica") or {}).values() if v != "default"}


def load_gt_instances(gt_scene_dir: Path, class_map: dict[str, str],
                      min_diag_m: float = 0.10) -> tuple[list[dict], dict]:
    """Per-instance GT meshes (Y-up scene frame) from mesh_semantic.ply.

    Returns (instances, meta). Each instance: instance_id, replica_class,
    class (COCO), centroid, bbox_min/max, vertices, faces. `meta` records
    unmapped classes and skipped-tiny counts so gaps stay visible.
    """
    from plyfile import PlyData

    mesh_path, info_path = _find_semantic_assets(gt_scene_dir)
    info = json.loads(info_path.read_text())
    id2class: dict[int, str] = {}
    classes = {int(c["id"]): str(c["name"]) for c in info.get("classes", [])}
    for obj in info.get("objects", []):
        cid = obj.get("class_id")
        name = classes.get(int(cid), "") if cid is not None else \
            str(obj.get("class_name", ""))
        id2class[int(obj["id"])] = name

    ply = PlyData.read(str(mesh_path))
    vtx = ply["vertex"]
    V = np.column_stack([vtx["x"], vtx["y"], vtx["z"]]).astype(np.float64)
    V = V @ ZUP_TO_YUP[:3, :3].T  # Replica world (Z-up) -> scene frame (Y-up)
    face = ply["face"]
    if "object_id" not in face.data.dtype.names:
        raise ValueError(f"{mesh_path} has no per-face object_id property")
    obj_ids = np.asarray(face["object_id"], dtype=np.int64)
    idx_prop = next(n for n in ("vertex_indices", "vertex_index")
                    if n in face.data.dtype.names)
    raw_faces = face[idx_prop]

    # triangulate (Replica semantic meshes are quads; handle any n-gon)
    lens = np.array([len(f) for f in raw_faces])
    tris_list, tri_obj = [], []
    for n in np.unique(lens):
        sel = lens == n
        arr = np.vstack([np.asarray(f, dtype=np.int64) for f, m in
                         zip(raw_faces, sel) if m]) if sel.any() else None
        if arr is None or n < 3:
            continue
        oid = obj_ids[sel]
        for k in range(1, n - 1):  # fan triangulation
            tris_list.append(np.column_stack([arr[:, 0], arr[:, k], arr[:, k + 1]]))
            tri_obj.append(oid)
    F = np.vstack(tris_list)
    FO = np.concatenate(tri_obj)

    meta = {"unmapped_gt_classes": {}, "skipped_tiny": 0,
            "mesh_semantic": str(mesh_path), "info_semantic": str(info_path)}
    instances: list[dict] = []
    for oid in np.unique(FO):
        rep = id2class.get(int(oid), "").strip().lower()
        coco = class_map.get(rep)
        if coco is None:
            if rep:
                meta["unmapped_gt_classes"][rep] = \
                    meta["unmapped_gt_classes"].get(rep, 0) + 1
            continue
        if coco == "default":
            continue
        faces = F[FO == oid]
        used = np.unique(faces)
        remap = -np.ones(V.shape[0], dtype=np.int64)
        remap[used] = np.arange(len(used))
        verts = V[used]
        bmin, bmax = verts.min(0), verts.max(0)
        if float(np.linalg.norm(bmax - bmin)) < min_diag_m:
            meta["skipped_tiny"] += 1
            continue
        instances.append({
            "instance_id": int(oid), "replica_class": rep, "class": coco,
            "vertices": verts, "faces": remap[faces],
            "centroid": (bmin + bmax) / 2.0, "bbox_min": bmin, "bbox_max": bmax,
        })
    return instances, meta


# --------------------------------------------------------------------------
# pose evaluation
# --------------------------------------------------------------------------
def evaluate_pose(bundle_dir: Path, gt_traj_path: Path) -> dict:
    """ATE of the poses the pipeline used (bundle poses.json) vs dataset GT."""
    poses_p = bundle_dir / "poses.json"
    if not poses_p.is_file():
        return {"available": False, "reason": f"{poses_p} missing"}
    if not gt_traj_path.is_file():
        return {"available": False,
                "reason": f"GT trajectory {gt_traj_path} missing "
                          "(run scripts/fetch_replica_gt_traj.py)"}
    recs = json.loads(poses_p.read_text())
    recs.sort(key=lambda r: r["frame_id"])
    used = np.array([r["T_world_camera"] for r in recs], dtype=np.float64)
    gt = np.loadtxt(gt_traj_path).reshape(-1, 4, 4)
    gt = ZUP_TO_YUP[None] @ gt  # same rotation ReplicaReader applied

    # bundles were built with a uniform stride over the rendered frames
    stride = max(1, round(len(gt) / len(used)))
    gt_s = gt[::stride][: len(used)]
    if len(gt_s) != len(used):
        return {"available": False,
                "reason": f"cannot associate: {len(used)} bundle poses vs "
                          f"{len(gt)} GT poses (stride {stride})"}
    err = np.linalg.norm(used[:, :3, 3] - gt_s[:, :3, 3], axis=1)
    identical = bool(err.max() < 1e-9 and
                     np.abs(used[:, :3, :3] - gt_s[:, :3, :3]).max() < 1e-9)
    return {
        "available": True,
        "ate_rmse_m": float(np.sqrt((err ** 2).mean())),
        "ate_mean_m": float(err.mean()),
        "ate_median_m": float(np.median(err)),
        "n_frames": int(len(used)),
        "gt_stride": int(stride),
        "source": "dataset_ground_truth" if identical else "estimated",
        "note": ("bundle poses.json is byte-identical to the dataset GT "
                 "trajectory: the pipeline consumed GT poses directly (no "
                 "SLAM ran for this build), so ATE is 0 by construction")
                if identical else
                "bundle poses differ from GT: they are pipeline estimates",
    }


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def evaluate(scene_dir: Path, room: str, bundle_dir: Path, gt_scene_dir: Path,
             gt_traj: Path, dist_thresh: float, iou_thresh: float,
             rigid: bool, pose_only: bool) -> dict:
    result: dict = {
        "schema": 1,
        "room": room,
        "scene_dir": str(scene_dir),
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }

    result["pose"] = evaluate_pose(bundle_dir, gt_traj)

    if pose_only:
        result["detection"] = {"available": False, "reason": "--pose-only"}
        pose_term = _pose_term(result["pose"])
        result["score"] = correctness_score(None, None, None, pose_term)
        return result

    class_map = load_eval_class_map()
    scope = in_scope_classes()
    shipped = load_shipped_objects(scene_dir)
    gt_all, gt_meta = load_gt_instances(gt_scene_dir, class_map)

    gt_in = [g for g in gt_all if g["class"] in scope]
    gt_out = [g for g in gt_all if g["class"] not in scope]

    # ONE global room transform GT -> scene space, then bake it in
    align = estimate_global_transform(shipped, gt_in, rigid=rigid)
    R, t = align["R"], align["t"]
    for g in gt_all:
        g["vertices"] = g["vertices"] @ R.T + t
        g["bbox_min"] = g["vertices"].min(0)
        g["bbox_max"] = g["vertices"].max(0)
        g["centroid"] = (g["bbox_min"] + g["bbox_max"]) / 2.0
    result["alignment"] = {
        "method": align["method"],
        "n_pairs": align["n_pairs"],
        "mean_residual_m": align["mean_residual_m"],
        "translation_m": [round(float(v), 4) for v in t],
        "rotation_deg": round(float(np.degrees(np.arccos(
            np.clip((np.trace(R) - 1) / 2, -1, 1)))), 3),
    }

    matches, un_s, un_g = match_objects(shipped, gt_in, dist_thresh, iou_thresh)

    # ---- per-object geometry --------------------------------------------
    import trimesh

    objects_out, fscores, dim_errs = [], [], []
    for i, j, dist in matches:
        s, g = shipped[i], gt_in[j]
        entry = {"id": s["id"], "class": s["class"], "matched": True,
                 "gt_instance": g["instance_id"], "gt_class": g["replica_class"],
                 "centroid_err_m": round(dist, 4)}
        if s["points"] is not None:
            gt_mesh = trimesh.Trimesh(vertices=g["vertices"], faces=g["faces"],
                                      process=False)
            gpts = np.asarray(gt_mesh.sample(N_SAMPLE), dtype=np.float64) \
                if len(gt_mesh.faces) else g["vertices"]
            # translation-only per-object alignment (bbox-centre to bbox-centre)
            spts = s["points"] + (g["centroid"] - s["centroid"])
            cham, fsc = chamfer_and_fscore(spts, gpts)
            ext_s = s["bbox_max"] - s["bbox_min"]
            ext_g = g["bbox_max"] - g["bbox_min"]
            ratio = np.divide(ext_s, ext_g, out=np.zeros(3),
                              where=ext_g > 1e-9)
            entry.update({
                "chamfer_cm": round(cham * 100, 2),
                "fscore_5cm": round(fsc, 4),
                "dim_ratio_xyz": [round(float(r), 3) for r in ratio],
                "dim_err": round(float(np.abs(ratio - 1).mean()), 4),
                "verdict": ("good" if fsc >= 0.5 else
                            "fair" if fsc >= 0.25 else "poor"),
            })
            fscores.append(fsc)
            dim_errs.append(float(np.abs(ratio - 1).mean()))
        else:
            entry.update({"verdict": "no mesh on disk"})
        objects_out.append(entry)
    for i in un_s:
        s = shipped[i]
        objects_out.append({"id": s["id"], "class": s["class"], "matched": False,
                            "verdict": "no GT match (false positive)"})

    missed = [{"gt_instance": gt_in[j]["instance_id"],
               "class": gt_in[j]["class"],
               "replica_class": gt_in[j]["replica_class"],
               "extent_m": [round(float(v), 3) for v in
                            (gt_in[j]["bbox_max"] - gt_in[j]["bbox_min"])]}
              for j in un_g]

    per_class: dict[str, dict] = {}
    for g in gt_in:
        per_class.setdefault(g["class"], {"gt": 0, "shipped": 0, "matched": 0})
        per_class[g["class"]]["gt"] += 1
    for s in shipped:
        per_class.setdefault(s["class"], {"gt": 0, "shipped": 0, "matched": 0})
        per_class[s["class"]]["shipped"] += 1
    for i, j, _ in matches:
        per_class[shipped[i]["class"]]["matched"] += 1

    n_ship, n_gt, n_match = len(shipped), len(gt_in), len(matches)
    precision = (n_match / n_ship) if n_ship else None
    recall = (n_match / n_gt) if n_gt else None
    result["detection"] = {
        "available": True,
        "gt_in_scope": n_gt,
        "gt_out_of_scope": len(gt_out),
        "shipped": n_ship,
        "matched": n_match,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "per_class": per_class,
        "out_of_scope_gt": _count_classes(gt_out),
        "unmapped_gt_classes": gt_meta["unmapped_gt_classes"],
        "match_rule": f"same COCO class AND (centroid dist <= {dist_thresh} m "
                      f"OR AABB IoU >= {iou_thresh})",
        "gt_assets": {"mesh": gt_meta["mesh_semantic"],
                      "info": gt_meta["info_semantic"]},
    }
    result["objects"] = objects_out
    result["missed_gt"] = missed
    result["geometry"] = {
        "mean_fscore_5cm": round(float(np.mean(fscores)), 4) if fscores else None,
        "median_fscore_5cm": round(float(np.median(fscores)), 4) if fscores else None,
        "mean_dim_err": round(float(np.mean(dim_errs)), 4) if dim_errs else None,
        "n_points_per_mesh": N_SAMPLE,
        "fscore_tau_m": FSCORE_TAU_M,
    }

    mean_f = float(np.mean(fscores)) if fscores else (0.0 if matches or n_gt else None)
    result["score"] = correctness_score(recall, precision, mean_f,
                                        _pose_term(result["pose"]))
    return result


def _pose_term(pose: dict) -> float | None:
    if not pose.get("available"):
        return None
    return float(np.clip(1.0 - pose["ate_rmse_m"] / ATE_FULL_MARKS_M, 0.0, 1.0))


def _count_classes(items: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        out[it["class"]] = out.get(it["class"], 0) + 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scene", required=True, type=Path,
                    help="assembled scene dir (contains scene.json)")
    ap.add_argument("--room", required=True,
                    help="Replica room, e.g. office_3")
    ap.add_argument("--bundle", type=Path, default=None,
                    help="PerceptionBundle the build used "
                         "(default: bundles_dense/<room>, else bundles/<room>)")
    ap.add_argument("--gt-scene", type=Path, default=None,
                    help="Replica scene assets with semantics "
                         "(default: ~/projects/soba/data/replica/scenes/<room>)")
    ap.add_argument("--gt-traj", type=Path, default=None,
                    help="GT trajectory traj_w_c.txt "
                         "(default: .../gt_traj/<room>_traj_w_c.txt)")
    ap.add_argument("--dist-thresh", type=float, default=0.75)
    ap.add_argument("--iou-thresh", type=float, default=0.10)
    ap.add_argument("--rigid", action="store_true",
                    help="allow rotation in the global room alignment (Kabsch)")
    ap.add_argument("--pose-only", action="store_true",
                    help="skip detection/geometry (no semantic GT needed)")
    ap.add_argument("--out", type=Path, default=None,
                    help="output path (default <scene>/eval.json)")
    args = ap.parse_args()

    scene_dir = args.scene.resolve()
    if not (scene_dir / "scene.json").is_file():
        sys.exit(f"error: {scene_dir}/scene.json not found — not an assembled scene")
    bundle = args.bundle or (
        _DATA / "bundles_dense" / args.room
        if (_DATA / "bundles_dense" / args.room / "poses.json").is_file()
        else _DATA / "bundles" / args.room)
    gt_scene = args.gt_scene or (_DATA / "scenes" / args.room)
    gt_traj = args.gt_traj or (_DATA / "gt_traj" / f"{args.room}_traj_w_c.txt")

    if not args.pose_only:
        try:
            _find_semantic_assets(gt_scene)
        except FileNotFoundError as exc:
            sys.exit(f"error: {exc}\n"
                     "Semantic GT is required for detection/geometry eval; "
                     "re-run with --pose-only for the pose block alone.")

    result = evaluate(scene_dir, args.room, bundle, gt_scene, gt_traj,
                      args.dist_thresh, args.iou_thresh, args.rigid,
                      args.pose_only)
    out = args.out or (scene_dir / "eval.json")
    out.write_text(json.dumps(result, indent=2) + "\n")
    sc = result["score"]
    det = result["detection"]
    print(f"{args.room} {scene_dir.name}: score {sc['value']}/100")
    if det.get("available"):
        print(f"  recall {det['recall']}  precision {det['precision']}  "
              f"matched {det['matched']}/{det['gt_in_scope']} GT "
              f"({det['gt_out_of_scope']} out-of-scope)")
        g = result["geometry"]
        print(f"  mean F@5cm {g['mean_fscore_5cm']}  mean dim err {g['mean_dim_err']}")
    p = result["pose"]
    if p.get("available"):
        print(f"  ATE RMSE {p['ate_rmse_m']*100:.2f} cm ({p['source']})")
    else:
        print(f"  pose eval n/a: {p.get('reason')}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
