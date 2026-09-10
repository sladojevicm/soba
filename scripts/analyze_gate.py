"""Diagnostic: per-object angular coverage + BOTH completeness normalisers
(legacy bbox-area vs shape-fair convex-hull-area) across all bundles, so gate
thresholds can be calibrated from the real distributions. Dumps rows to JSON.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import open3d as o3d  # noqa: E402

from perception.bundle import PerceptionBundle  # noqa: E402
from reconstruction import confidence as cf  # noqa: E402
from reconstruction import observed_cloud, tsdf  # noqa: E402

bundles = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    Path.home() / "projects/soba/data/replica/bundles")
voxel = 0.004


def both_completeness(cloud):
    """(comp_hull, comp_bbox) sharing one ball-pivoting patch computation."""
    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(cloud))
    pcd.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamKNN(knn=20))
    obs = cf._observed_patch_area(pcd, voxel)
    try:
        hull, _ = pcd.compute_convex_hull()
        ha = float(hull.get_surface_area())
    except Exception:
        ha = 0.0
    e = pcd.get_oriented_bounding_box().extent
    ba = 2.0 * (e[0] * e[1] + e[1] * e[2] + e[0] * e[2])
    return (min(obs / ha, 1.0) if ha > 0 else 0.0,
            min(obs / ba, 1.0) if ba > 0 else 0.0)


rows = []
for sp in sorted(p for p in bundles.iterdir() if (p / "manifest.json").exists()):
    b = PerceptionBundle.open(sp)
    poses = b.read_poses()
    K = b.intrinsics.matrix()
    classes = {}
    for fid in b.iter_frame_ids():
        for d in b.read_objects(fid):
            classes.setdefault(int(d["track_id"]), d.get("class", "obj"))
    for tid in tsdf._all_track_ids(b):
        fids, frames = tsdf._object_frames(b, tid, poses)
        cloud, keep = observed_cloud.accumulate_object_cloud(
            frames, K, voxel_size=voxel, return_keep=True)
        if len(cloud) < 4:
            continue
        cams = np.array([poses[fids[i]][:3, 3] for i in keep])
        ang = cf.angular_coverage_deg(cloud.mean(0), cams)
        ch, cb = both_completeness(cloud)
        rows.append(dict(scene=sp.name, cls=classes.get(tid, "obj"), tid=tid,
                         frm=len(keep), pts=len(cloud),
                         angle=round(ang, 1), hull=round(ch, 3), bbox=round(cb, 3)))

out = Path(__file__).resolve().parents[1] / "out" / "gate_distribution.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(rows, indent=1))

rows.sort(key=lambda r: -r["angle"])
print(f"{'scene':9} {'class':13} {'tid':>4} {'frm':>4} {'angle':>6} {'hull':>6} {'bbox':>6}")
for r in rows:
    print(f"{r['scene']:9} {r['cls']:13} {r['tid']:>4} {r['frm']:>4} "
          f"{r['angle']:>6.1f} {r['hull']:>6.3f} {r['bbox']:>6.3f}")

ang = np.array([r["angle"] for r in rows])
hull = np.array([r["hull"] for r in rows])
bbox = np.array([r["bbox"] for r in rows])
print(f"\n{len(rows)} objects")
for name, arr in [("angular", ang), ("hull-compl", hull), ("bbox-compl", bbox)]:
    qs = np.percentile(arr, [50, 75, 90, 95])
    print(f"{name:11}: max {arr.max():.3f}  p50 {qs[0]:.3f}  p75 {qs[1]:.3f}  "
          f"p90 {qs[2]:.3f}  p95 {qs[3]:.3f}")
print(f"\nsaved {len(rows)} rows -> {out}")
