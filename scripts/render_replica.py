#!/usr/bin/env python
"""Render CUSTOM camera trajectories from Replica semantic meshes into
PerceptionBundles that scripts/run_assemble.py consumes unchanged.

Why: the pre-rendered vMAP trajectories never frame some objects properly
(office_4's table gets only distant/edge-on glimpses), capping reconstruction
quality. Here we render OUR OWN trajectories — a walkthrough loop at eye
height plus a slow orbit around each large object — from the Habitat-format
semantic mesh (mesh_semantic.ply, vertex colors + per-face object_id).

Everything is CPU-only (open3d RaycastingScene, embree): safe to run while the
GPU is busy with tier-2 builds.

Conventions (all VERIFIED against the existing bundles via `match-test`):
  * intrinsics 1200x680, fx=fy=600, cx=599.5, cy=339.5 (REPLICA_INTRINSICS)
  * poses.json holds CAMERA->WORLD in a Y-UP world; the raw Replica mesh is
    Z-up, so the mesh is rotated by the same Z-up->Y-up map the reader uses.
  * camera is OpenCV convention: x right, y down, z forward.
  * depth.png is uint16 millimetres (z-depth, not ray length); 0 = no hit.

Usage:
  # correctness gate — render one frame from an ORIGINAL bundle's pose and
  # compare with the original rgb/depth:
  PYTHONPATH=src python scripts/render_replica.py match-test \
      --scene-dir ~/projects/soba/data/replica/scenes/office_4 \
      --bundle ~/projects/soba/data/replica/bundles/office_4 \
      --frame 0 --out ~/projects/soba/data/replica/previews/frame_match_office_4.png

  # full custom-trajectory bundle:
  PYTHONPATH=src python scripts/render_replica.py render \
      --scene-dir ~/projects/soba/data/replica/scenes/office_4 \
      --out ~/projects/soba/data/replica/bundles/office_4_v2 --frames 1800
"""

from __future__ import annotations

import argparse
import functools
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from perception.bundle import Intrinsics, Manifest, PerceptionBundle  # noqa: E402
from perception.dataset_reader import (  # noqa: E402
    REPLICA_INTRINSICS,
    _REPLICA_ZUP_TO_YUP,
    load_coco_class_map,
    map_label_to_coco,
)

print = functools.partial(print, flush=True)

WIDTH, HEIGHT = 1200, 680
FPS = 30.0
# ~20-40 cm/s at 30 fps so tracking-based stages see small pose deltas.
STEP_M = 0.0125          # metres between consecutive camera positions (37.5 cm/s)
EYE_HEIGHT = 1.5         # walkthrough camera height above floor (m)
CLEARANCE = 0.28         # min camera distance to any geometry (m)
MIN_MASK_AREA = 200      # px, same as ReplicaReader.detections
# classes worth a dedicated orbit (COCO keys after mapping)
ORBIT_CLASSES = {"chair", "dining table", "couch"}
MIN_ORBIT_DIAG = 0.45    # skip tiny instances (m, xz bbox diagonal)


# --------------------------------------------------------------------------
# Fast binary-PLY loader for Habitat mesh_semantic.ply
# --------------------------------------------------------------------------
_PLY_TYPES = {
    "char": "i1", "uchar": "u1", "int8": "i1", "uint8": "u1",
    "short": "i2", "ushort": "u2", "int16": "i2", "uint16": "u2",
    "int": "i4", "uint": "u4", "int32": "i4", "uint32": "u4",
    "float": "f4", "float32": "f4", "double": "f8", "float64": "f8",
}


@dataclass
class SemanticMesh:
    vertices: np.ndarray       # (N,3) float32, Y-UP world
    colors: np.ndarray         # (N,3) float32 in [0,1]
    triangles: np.ndarray      # (M,3) uint32
    tri_object_id: np.ndarray  # (M,) int32
    labels: dict[int, str] = field(default_factory=dict)  # object_id -> replica label


def _parse_ply_header(fh) -> tuple[list[tuple[str, int, list]], int]:
    """Returns ([(element, count, [props...])], header_end). prop is either
    ('scalar', name, dtype) or ('list', name, count_dtype, item_dtype)."""
    magic = fh.readline().strip()
    if magic != b"ply":
        raise ValueError("not a PLY file")
    elements: list[tuple[str, int, list]] = []
    fmt = None
    while True:
        line = fh.readline().decode("ascii").strip()
        if line == "end_header":
            break
        parts = line.split()
        if not parts or parts[0] == "comment":
            continue
        if parts[0] == "format":
            fmt = parts[1]
        elif parts[0] == "element":
            elements.append((parts[1], int(parts[2]), []))
        elif parts[0] == "property":
            if parts[1] == "list":
                elements[-1][2].append(("list", parts[4], _PLY_TYPES[parts[2]], _PLY_TYPES[parts[3]]))
            else:
                elements[-1][2].append(("scalar", parts[2], _PLY_TYPES[parts[1]]))
    if fmt != "binary_little_endian":
        raise ValueError(f"unsupported PLY format {fmt} (expected binary_little_endian)")
    return elements, fh.tell()


def load_semantic_mesh(ply_path: Path, info_json: Path | None = None,
                       zup_to_yup: bool = True) -> SemanticMesh:
    """Load mesh_semantic.ply (vertex colors + per-face object_id).

    Assumes uniform face arity (Habitat's semantic mesh is a quad mesh; some
    exports are tris) so faces parse as one fixed-stride numpy view. A cached
    .npz sits next to the ply after the first load (~10x faster reopen).
    """
    cache = ply_path.with_suffix(".cache.npz")
    if cache.exists() and cache.stat().st_mtime >= ply_path.stat().st_mtime:
        z = np.load(cache)
        mesh = SemanticMesh(z["v"], z["c"], z["t"], z["o"])
    else:
        with ply_path.open("rb") as fh:
            elements, offset = _parse_ply_header(fh)
            data = fh.read()
        pos = 0
        verts = cols = tris = tri_obj = None
        for name, count, props in elements:
            if name == "vertex":
                dtype = np.dtype([(p[1], "<" + p[2]) for p in props])
                arr = np.frombuffer(data, dtype=dtype, count=count, offset=pos)
                pos += dtype.itemsize * count
                verts = np.stack([arr["x"], arr["y"], arr["z"]], axis=1).astype(np.float32)
                if all(k in dtype.names for k in ("red", "green", "blue")):
                    cols = np.stack([arr["red"], arr["green"], arr["blue"]],
                                    axis=1).astype(np.float32) / 255.0
                else:
                    cols = np.full((count, 3), 0.7, np.float32)
            elif name == "face":
                # peek arity, then parse the whole element as fixed stride
                arity = int(np.frombuffer(data, dtype="<u1", count=1, offset=pos)[0])
                fields = [("n", "<u1"), ("idx", "<i4", (arity,))]
                for p in props:
                    if p[0] == "scalar":
                        fields.append((p[1], "<" + p[2]))
                dtype = np.dtype(fields)
                arr = np.frombuffer(data, dtype=dtype, count=count, offset=pos)
                if not (arr["n"] == arity).all():
                    raise ValueError("mixed face arity PLY — extend loader")
                pos += dtype.itemsize * count
                idx = arr["idx"].astype(np.uint32)
                obj = (arr["object_id"].astype(np.int32)
                       if "object_id" in dtype.names else np.zeros(count, np.int32))
                if arity == 3:
                    tris, tri_obj = idx, obj
                elif arity == 4:  # quad -> two tris, object_id duplicated
                    tris = np.concatenate([idx[:, [0, 1, 2]], idx[:, [0, 2, 3]]])
                    tri_obj = np.concatenate([obj, obj])
                else:
                    raise ValueError(f"unsupported face arity {arity}")
            else:
                raise ValueError(f"cannot skip unknown element {name} — extend loader")
        mesh = SemanticMesh(verts, cols, tris, tri_obj)
        np.savez(cache, v=mesh.vertices, c=mesh.colors, t=mesh.triangles, o=mesh.tri_object_id)

    if zup_to_yup:
        R = _REPLICA_ZUP_TO_YUP[:3, :3].astype(np.float32)
        mesh.vertices = np.ascontiguousarray(mesh.vertices @ R.T)

    if info_json and info_json.exists():
        info = json.loads(info_json.read_text())
        cls_names = {c["id"]: c["name"] for c in info.get("classes", [])}
        for o in info.get("objects", []):
            label = o.get("class_name") or cls_names.get(o.get("class_id"), "")
            mesh.labels[int(o["id"])] = str(label)
        # id_to_label maps instance -> class id directly (covers ids missing
        # from the objects list; negative = unlabeled)
        for inst, cid in enumerate(info.get("id_to_label", [])):
            if inst not in mesh.labels and cid >= 0:
                mesh.labels[inst] = cls_names.get(cid, "")
    return mesh


# --------------------------------------------------------------------------
# CPU renderer
# --------------------------------------------------------------------------
class Renderer:
    def __init__(self, mesh: SemanticMesh, intr: Intrinsics = REPLICA_INTRINSICS,
                 width: int = WIDTH, height: int = HEIGHT):
        import open3d as o3d
        self.o3d = o3d
        self.mesh = mesh
        self.intr = intr
        self.w, self.h = width, height
        self.scene = o3d.t.geometry.RaycastingScene()
        self.scene.add_triangles(
            o3d.core.Tensor(mesh.vertices), o3d.core.Tensor(mesh.triangles))
        # camera-frame ray directions with z=1 so t_hit IS the z-depth
        u, v = np.meshgrid(np.arange(self.w, dtype=np.float32),
                           np.arange(self.h, dtype=np.float32))
        self.dirs_cam = np.stack(
            [(u - intr.cx) / intr.fx, (v - intr.cy) / intr.fy, np.ones_like(u)],
            axis=-1).reshape(-1, 3)
        self.invalid = self.scene.INVALID_ID

    def render(self, T_wc: np.ndarray):
        """-> (rgb uint8 HxWx3, depth_mm uint16 HxW, inst int32 HxW [-1=none])."""
        R, t = T_wc[:3, :3].astype(np.float32), T_wc[:3, 3].astype(np.float32)
        dirs = self.dirs_cam @ R.T
        rays = np.concatenate(
            [np.broadcast_to(t, dirs.shape), dirs], axis=1).astype(np.float32)
        ans = self.scene.cast_rays(self.o3d.core.Tensor(rays))
        t_hit = ans["t_hit"].numpy()
        prim = ans["primitive_ids"].numpy()
        uv = ans["primitive_uvs"].numpy()
        hit = prim != self.invalid
        prim_safe = np.where(hit, prim, 0).astype(np.int64)

        tri = self.mesh.triangles[prim_safe].astype(np.int64)     # (P,3)
        w0 = (1.0 - uv[:, 0] - uv[:, 1])[:, None]
        c = (self.mesh.colors[tri[:, 0]] * w0
             + self.mesh.colors[tri[:, 1]] * uv[:, 0:1]
             + self.mesh.colors[tri[:, 2]] * uv[:, 1:2])
        rgb = np.clip(c * 255.0 + 0.5, 0, 255).astype(np.uint8)
        rgb[~hit] = 0
        depth_mm = np.where(hit, np.clip(t_hit * 1000.0, 0, 65535), 0.0)
        inst = np.where(hit, self.mesh.tri_object_id[prim_safe], -1)
        return (rgb.reshape(self.h, self.w, 3),
                depth_mm.reshape(self.h, self.w).astype(np.uint16),
                inst.reshape(self.h, self.w).astype(np.int32))

    def clearance(self, points: np.ndarray) -> np.ndarray:
        q = self.o3d.core.Tensor(np.asarray(points, np.float32).reshape(-1, 3))
        return self.scene.compute_distance(q).numpy()

    def inside(self, points: np.ndarray, max_reach: float = 8.0) -> np.ndarray:
        """True where a point is INSIDE the room (bool per point).

        Clearance alone cannot catch escapes: beyond a wall / through a window
        or scan hole there is nothing nearby, so clearance is LARGE precisely
        when the camera has left the room. Test containment instead: a ray
        straight up must hit the ceiling AND a ray straight down must hit the
        floor within max_reach — outside the building one of them hits void.
        """
        p = np.asarray(points, np.float64).reshape(-1, 3)
        rays = np.zeros((2 * len(p), 6), np.float32)
        rays[: len(p), :3] = rays[len(p):, :3] = p
        rays[: len(p), 4] = 1.0    # up
        rays[len(p):, 4] = -1.0    # down
        hit = self.scene.cast_rays(
            self.o3d.core.Tensor(rays))["t_hit"].numpy()
        up, down = hit[: len(p)], hit[len(p):]
        return (np.isfinite(up) & (up < max_reach)
                & np.isfinite(down) & (down < max_reach))


# --------------------------------------------------------------------------
# Trajectory authoring (Y-up world, OpenCV camera)
# --------------------------------------------------------------------------
def look_at(pos: np.ndarray, target: np.ndarray) -> np.ndarray:
    """T_wc with +z forward (to target), image up = world +Y."""
    z = target - pos
    n = np.linalg.norm(z)
    z = z / n if n > 1e-9 else np.array([0.0, 0.0, 1.0])
    up = np.array([0.0, 1.0, 0.0])
    if abs(z @ up) > 0.999:  # looking straight up/down
        up = np.array([0.0, 0.0, 1.0])
    x = np.cross(z, up)  # right-handed with y_cam = z×x pointing image-down
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    T = np.eye(4)
    T[:3, 0], T[:3, 1], T[:3, 2], T[:3, 3] = x, y, z, pos
    return T


@dataclass
class Instance:
    object_id: int
    label: str
    coco: str
    centroid: np.ndarray
    bbox_min: np.ndarray
    bbox_max: np.ndarray

    @property
    def xz_radius(self) -> float:
        e = (self.bbox_max - self.bbox_min) / 2
        return float(np.hypot(e[0], e[2]))


def scene_instances(mesh: SemanticMesh, class_map) -> list[Instance]:
    out = []
    for oid in np.unique(mesh.tri_object_id):
        label = mesh.labels.get(int(oid), "")
        coco = map_label_to_coco("replica", label, class_map)
        vid = np.unique(mesh.triangles[mesh.tri_object_id == oid])
        pts = mesh.vertices[vid]
        out.append(Instance(int(oid), label, coco, pts.mean(axis=0),
                            pts.min(axis=0), pts.max(axis=0)))
    return out


def _resample_polyline(pts: np.ndarray, step: float, closed: bool = False) -> np.ndarray:
    """Resample a polyline at constant arc-length step (constant camera speed)."""
    if closed:
        pts = np.vstack([pts, pts[:1]])
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    n = max(int(s[-1] / step), 2)
    si = np.linspace(0.0, s[-1], n, endpoint=not closed)
    return np.stack([np.interp(si, s, pts[:, k]) for k in range(3)], axis=1)


def _slerp_dir(d0: np.ndarray, d1: np.ndarray, e: float) -> np.ndarray:
    """Spherical interpolation between two unit directions (stable look easing)."""
    dot = float(np.clip(d0 @ d1, -1.0, 1.0))
    ang = np.arccos(dot)
    if ang < 1e-4:
        return d1
    if ang > np.pi - 1e-3:  # antipodal: rotate via the world-up plane
        mid = np.cross(d0, [0.0, 1.0, 0.0])
        mid /= np.linalg.norm(mid)
        return _slerp_dir(mid, d1, e) if e > 0.5 else _slerp_dir(d0, mid, 1 - e)
    return (np.sin((1 - e) * ang) * d0 + np.sin(e * ang) * d1) / np.sin(ang)


def _smooth(pts: np.ndarray, it: int = 8) -> np.ndarray:
    p = pts.copy()
    for _ in range(it):
        p[1:-1] = 0.25 * p[:-2] + 0.5 * p[1:-1] + 0.25 * p[2:]
    return p


class TrajectoryPlanner:
    def __init__(self, renderer: Renderer, mesh: SemanticMesh, class_map):
        self.r = renderer
        self.mesh = mesh
        # room extent from the walkable band (ignore ceiling clutter)
        v = mesh.vertices
        self.floor_y = float(np.percentile(v[:, 1], 0.5))
        self.ceil_y = float(np.percentile(v[:, 1], 99.5))
        band = v[(v[:, 1] > self.floor_y + 0.1) & (v[:, 1] < self.ceil_y - 0.1)]
        self.bb_min = band.min(axis=0)
        self.bb_max = band.max(axis=0)
        self.center = (self.bb_min + self.bb_max) / 2
        self.instances = scene_instances(mesh, class_map)

    # -- free-space helpers -------------------------------------------------
    def _pull_free(self, p: np.ndarray, min_clear: float = CLEARANCE,
                   max_iter: int = 25) -> np.ndarray | None:
        """Pull a camera position toward the room centre until it has clearance."""
        p = p.copy()
        anchor = self.center.copy()
        anchor[1] = p[1]
        for _ in range(max_iter):
            # inside() first: an escaped point has LARGE clearance, so testing
            # clearance alone would accept it immediately (the out-of-room bug)
            if self.r.inside(p)[0] and self.r.clearance(p)[0] >= min_clear:
                return p
            p = p + (anchor - p) * 0.12
        return None

    def _repair(self, pts: np.ndarray, min_clear: float = 0.18,
                rounds: int = 3) -> np.ndarray:
        """Post-pass on a smoothed path: nudge any nearly-colliding sample
        toward the room centre (in xz), then locally re-smooth. Catches spots
        the coarse waypoint checks missed (between-waypoint grazes, smoothing
        drift, orbit radius-ring switches)."""
        pts = pts.copy()
        for _ in range(rounds):
            cl = self.r.clearance(pts)
            bad = np.nonzero((cl < min_clear) | ~self.r.inside(pts))[0]
            if bad.size == 0:
                break
            for k in bad:
                fixed = self._pull_free(pts[k], min_clear=min_clear + 0.05)
                if fixed is not None:
                    pts[k] = fixed
                else:
                    pts[k, 1] = min(pts[k, 1] + 0.5, self.ceil_y - 0.4)
            pts = _smooth(pts, it=2)
        return pts

    def orbit_targets(self) -> list[Instance]:
        """Large furniture instances worth a dedicated orbit, biggest first."""
        cands = [i for i in self.instances
                 if i.coco in ORBIT_CLASSES and 2 * i.xz_radius >= MIN_ORBIT_DIAG]
        cands.sort(key=lambda i: -i.xz_radius)
        # drop near-duplicate centroids (e.g. sectional couch split in two ids)
        kept: list[Instance] = []
        for c in cands:
            if all(np.linalg.norm((c.centroid - k.centroid)[[0, 2]]) > 0.6 for k in kept):
                kept.append(c)
        return kept[:6]

    # -- segments ------------------------------------------------------------
    def walkthrough(self) -> list[np.ndarray]:
        """Eye-height loop around the room, camera sweeping the interior."""
        y = self.floor_y + EYE_HEIGHT
        cx, cz = self.center[0], self.center[2]
        rx = (self.bb_max[0] - self.bb_min[0]) / 2 - 0.55
        rz = (self.bb_max[2] - self.bb_min[2]) / 2 - 0.55
        rx, rz = max(rx, 0.4), max(rz, 0.4)
        raw = []
        for a in np.linspace(0, 2 * np.pi, 240, endpoint=False):
            # superellipse hugs the walls better than an ellipse
            ce, se = np.cos(a), np.sin(a)
            f = (abs(ce) ** 2.5 + abs(se) ** 2.5) ** (-1 / 2.5)
            p = np.array([cx + rx * f * ce, y, cz + rz * f * se])
            p = self._pull_free(p)
            if p is not None:
                raw.append(p)
        pts = _smooth(_resample_polyline(np.array(raw), STEP_M, closed=True))
        pts = self._repair(pts)
        # n_frames is a soft budget: the full loop always survives (cutting it
        # would leave a wall uncovered); the orbit budget absorbs the excess.
        look = np.array([cx, self.floor_y + 1.0, cz])
        return [look_at(p, look) for p in pts]

    def orbit_arc(self, inst: Instance) -> tuple[np.ndarray, np.ndarray]:
        """Full reachable arc around an object at constant STEP_M spacing.

        Returns (positions (N,3), aim point (3,)); positions may be empty.
        The arc is the longest contiguous run of collision-free circle samples
        (i.e. the open side of the object)."""
        target = inst.centroid.copy()
        top = inst.bbox_max[1]
        cam_y = np.clip(top + 0.45, self.floor_y + 0.9, self.ceil_y - 0.4)
        radius = np.clip(inst.xz_radius * 1.35 + 0.55, 0.9, 2.4)
        angs = np.linspace(0, 2 * np.pi, 180, endpoint=False)
        ok: list[tuple[float, np.ndarray]] = []
        for a in angs:
            for r in (radius, radius * 1.25, radius * 1.5):
                p = np.array([target[0] + r * np.cos(a), cam_y,
                              target[2] + r * np.sin(a)])
                if (self.bb_min[0] + 0.25 < p[0] < self.bb_max[0] - 0.25
                        and self.bb_min[2] + 0.25 < p[2] < self.bb_max[2] - 0.25
                        and self.r.inside(p)[0]
                        and self.r.clearance(p)[0] >= CLEARANCE):
                    ok.append((a, p))
                    break
        aim = target.copy()
        aim[1] = min(target[1] + 0.15, cam_y - 0.2)
        if len(ok) < 12:
            return np.empty((0, 3)), aim
        # rotate the sample ring so it starts right after the biggest angular
        # gap -> a contiguous sweep of the object's open side
        angs_ok = np.array([a for a, _ in ok])
        gaps = np.diff(np.concatenate([angs_ok, [angs_ok[0] + 2 * np.pi]]))
        cut = int(np.argmax(gaps)) + 1
        order = np.roll(np.arange(len(ok)), -cut)
        pts = np.array([ok[i][1] for i in order])
        # drop everything past any residual >40cm jump (non-contiguous ring)
        jump = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        bad = np.nonzero(jump > 0.4)[0]
        if bad.size:
            pts = pts[: bad[0] + 1]
        if len(pts) < 8:
            return np.empty((0, 3)), aim
        return self._repair(_smooth(_resample_polyline(pts, STEP_M))), aim

    def transition(self, p_from: np.ndarray, look_from: np.ndarray,
                   p_to: np.ndarray, look_to: np.ndarray) -> list[np.ndarray]:
        """Constant-speed link between segments.

        Clearance is fixed on coarse waypoints (pull toward room centre, else
        lift over furniture) BEFORE smoothing+resampling, so consecutive frames
        can't flip between lifted/unlifted (that caused 70 cm jumps)."""
        d = float(np.linalg.norm(p_to - p_from))
        if d < 1e-6:
            return []
        n_way = max(int(d / 0.20), 2)
        way = [p_from + (p_to - p_from) * s for s in np.linspace(0, 1, n_way + 1)]
        for k in range(1, len(way) - 1):
            p = way[k]
            if self.r.inside(p)[0] and self.r.clearance(p)[0] >= CLEARANCE:
                continue
            free = self._pull_free(p)
            if free is not None:
                way[k] = free
                continue
            lift = p.copy()
            lift[1] = min(lift[1] + 0.8, self.ceil_y - 0.4)
            way[k] = lift
        pts = self._repair(_smooth(_resample_polyline(np.array(way), STEP_M), it=4))
        # ease the LOOK DIRECTION (slerp), not a look point — interpolating a
        # point the camera can pass through causes 180-degree view flips
        d0 = look_from - p_from
        d0 /= np.linalg.norm(d0)
        out = []
        n = len(pts)
        for k in range(1, n - 1):  # endpoints belong to the adjacent segments
            s = k / (n - 1)
            e = s * s * (3 - 2 * s)
            d1 = look_to - pts[k]
            d1 /= np.linalg.norm(d1)
            d = _slerp_dir(d0, d1, e)
            out.append(look_at(pts[k], pts[k] + d))
        return out

    def plan(self, total_frames: int) -> tuple[list[np.ndarray], list[dict]]:
        """Walkthrough + orbit-per-large-object, budgeted to ~total_frames."""
        targets = self.orbit_targets()
        poses = self.walkthrough()
        log = [{"segment": "walkthrough", "frames": len(poses),
                "frame_range": [0, len(poses) - 1]}]
        if not targets:
            return self._apply_budget(poses, log, total_frames)
        # visit order: greedy nearest-neighbour from the walkthrough end
        cur = poses[-1][:3, 3]
        ordered: list[Instance] = []
        pool = list(targets)
        while pool:
            nxt = min(pool, key=lambda i: np.linalg.norm((i.centroid - cur)[[0, 2]]))
            ordered.append(nxt)
            pool.remove(nxt)
            cur = nxt.centroid
        per_target = max((total_frames - len(poses)) // len(ordered), 120)
        for inst in ordered:
            pts, aim = self.orbit_arc(inst)
            if len(pts) == 0:
                log.append({"segment": f"orbit {inst.label}#{inst.object_id}",
                            "frames": 0, "note": "no reachable arc"})
                continue
            # enter the arc from whichever end is closer to the current camera
            cur = poses[-1][:3, 3]
            if (np.linalg.norm(pts[-1] - cur) < np.linalg.norm(pts[0] - cur)):
                pts = pts[::-1]
            look_from = poses[-1][:3, 3] + poses[-1][:3, 2]  # 1m ahead
            link = self.transition(cur, look_from, pts[0], aim)
            # orbit gets what's left of this target's budget after the link,
            # with a floor of 20 frames of actual coverage. 20 views across the
            # arc (~8 deg apart) is plenty for TSDF/gate/crop coverage — what
            # the pipeline consumes — and keeps the TOTAL near the requested
            # budget (the old floor of 100 blew a 200-frame request up to
            # 2000-3300 frames and filled the disk; smooth video playback is
            # not a requirement, viewpoint coverage is).
            n_orbit = int(np.clip(per_target - len(link), 20, len(pts)))
            pts = pts[:n_orbit]
            poses.extend(link)
            start = len(poses)
            poses.extend(look_at(p, aim) for p in pts)
            log.append({"segment": f"orbit {inst.label}#{inst.object_id} ({inst.coco})",
                        "frames": len(pts), "transition": len(link),
                        "frame_range": [start, len(poses) - 1],
                        "centroid": [round(float(x), 2) for x in inst.centroid],
                        "xz_radius_m": round(inst.xz_radius, 2)})
        return self._apply_budget(poses, log, total_frames)

    @staticmethod
    def _apply_budget(poses: list[np.ndarray], log: list[dict],
                      total_frames: int) -> tuple[list[np.ndarray], list[dict]]:
        """HARD budget: the segment planner optimises for smooth motion and
        (via the per-target/transition floors) can produce 8-16x the asked
        frame count. Uniformly subsampling the finished trajectory keeps the
        path shape and the walkthrough/orbit coverage proportions while
        honouring total_frames EXACTLY — inter-frame motion gets larger, but
        the pipeline consumes viewpoint coverage (GT poses ship with the
        bundle), not video smoothness. frame_range entries in the log refer
        to the pre-subsample trajectory. Applied on EVERY return path (the
        walkthrough-only early return once skipped it: room_1, 1526 frames)."""
        if len(poses) > total_frames:
            idx = np.linspace(0, len(poses) - 1, total_frames).round().astype(int)
            log.append({"segment": "subsample",
                        "kept": total_frames, "planned": len(poses)})
            poses = [poses[i] for i in idx]
        return poses, log


# --------------------------------------------------------------------------
# Bundle writing
# --------------------------------------------------------------------------
def detections_from_instances(inst_img: np.ndarray, labels: dict[int, str],
                              class_map) -> list[dict]:
    out = []
    for oid in np.unique(inst_img):
        if oid < 0:
            continue
        coco = map_label_to_coco("replica", labels.get(int(oid), ""), class_map)
        if coco == "default":
            continue
        mask = inst_img == oid
        if int(mask.sum()) < MIN_MASK_AREA:
            continue
        ys, xs = np.nonzero(mask)
        out.append({"track_id": int(oid), "class": coco,
                    "bbox": [float(xs.min()), float(ys.min()),
                             float(xs.max() + 1), float(ys.max() + 1)],
                    "mask": mask})
    return out


def render_bundle(scene_dir: Path, out_root: Path, total_frames: int,
                  fps: float = FPS) -> None:
    class_map = load_coco_class_map()
    mesh = load_semantic_mesh(scene_dir / "habitat" / "mesh_semantic.ply",
                              scene_dir / "habitat" / "info_semantic.json")
    print(f"mesh: {len(mesh.vertices):,} verts, {len(mesh.triangles):,} tris, "
          f"{len(mesh.labels)} labelled instances")
    r = Renderer(mesh)
    planner = TrajectoryPlanner(r, mesh, class_map)
    poses, log = planner.plan(total_frames)
    print(f"trajectory: {len(poses)} frames")
    for entry in log:
        print(f"  {entry}")

    manifest = Manifest(session_id=out_root.name, fps=fps,
                        frame_count=len(poses), timestamp_start="", source="replica")
    bundle = PerceptionBundle.create(out_root, manifest, REPLICA_INTRINSICS)
    for i, T in enumerate(poses):
        rgb, depth_mm, inst = r.render(T)
        bundle.write_rgb(i, rgb)
        bundle.write_depth_mm(i, depth_mm)
        records = []
        for det in detections_from_instances(inst, mesh.labels, class_map):
            mask = det.pop("mask")
            bundle.write_mask(i, det["track_id"], mask)
            records.append(det)
        bundle.write_objects(i, records)
        if i % 100 == 0:
            print(f"  frame {i}/{len(poses)}")
    bundle.write_poses(poses)
    bundle.write_frame_times([i / fps for i in range(len(poses))])
    (out_root / "trajectory_plan.json").write_text(json.dumps(log, indent=2))
    print(f"bundle written -> {out_root} ({len(poses)} frames)")


# --------------------------------------------------------------------------
# Frame-match correctness gate
# --------------------------------------------------------------------------
def match_test(scene_dir: Path, bundle_dir: Path, frame: int, out_png: Path) -> None:
    import cv2

    bundle = PerceptionBundle.open(bundle_dir)
    T = bundle.read_poses()[frame]
    rgb_orig = bundle.read_rgb(frame)
    depth_orig = bundle.read_depth_mm(frame)

    mesh = load_semantic_mesh(scene_dir / "habitat" / "mesh_semantic.ply",
                              scene_dir / "habitat" / "info_semantic.json")
    rgb_ren, depth_ren, _ = Renderer(mesh).render(T)

    both = (depth_orig > 0) & (depth_ren > 0)
    dd = np.abs(depth_orig.astype(np.int32) - depth_ren.astype(np.int32))[both]
    err_rgb = np.abs(rgb_orig.astype(np.int16) - rgb_ren.astype(np.int16)).mean()
    print(f"depth |diff| (mm): median {np.median(dd):.1f}, p90 {np.percentile(dd, 90):.1f}, "
          f"mean {dd.mean():.1f}  (valid overlap {both.mean() * 100:.1f}%)")
    print(f"RGB mean |diff|: {err_rgb:.1f} / 255")

    gap = np.full((rgb_orig.shape[0], 8, 3), 255, np.uint8)
    side = np.concatenate([rgb_orig, gap, rgb_ren], axis=1)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_png), cv2.cvtColor(side, cv2.COLOR_RGB2BGR))
    print(f"side-by-side (left=original, right=render) -> {out_png}")
    verdict = "PASS" if np.median(dd) < 30 else "FAIL"
    print(f"gate: {verdict} (median depth diff {'<' if verdict == 'PASS' else '>='} 30 mm)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    mt = sub.add_parser("match-test", help="render one original-bundle pose and compare")
    mt.add_argument("--scene-dir", type=Path, required=True)
    mt.add_argument("--bundle", type=Path, required=True)
    mt.add_argument("--frame", type=int, default=0)
    mt.add_argument("--out", type=Path, required=True)

    rd = sub.add_parser("render", help="render a full custom-trajectory bundle")
    rd.add_argument("--scene-dir", type=Path, required=True)
    rd.add_argument("--out", type=Path, required=True)
    rd.add_argument("--frames", type=int, default=1800)
    rd.add_argument("--fps", type=float, default=FPS)

    args = ap.parse_args()
    if args.cmd == "match-test":
        match_test(args.scene_dir, args.bundle, args.frame, args.out)
    else:
        if (args.out / "manifest.json").exists():
            ap.error(f"{args.out} already exists — refusing to overwrite")
        render_bundle(args.scene_dir, args.out, args.frames, args.fps)


if __name__ == "__main__":
    main()
