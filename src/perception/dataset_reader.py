"""Dataset input — replaces capture.py for public RGB-D datasets (Phase 2).

TUM RGB-D is implemented first (the plan's recommended starting point: free, no
signup, metric depth + ground-truth poses). ScanNet/Replica are sketched as
readers that produce the same PerceptionBundle.

The pure logic here — timestamp association, TUM depth -> millimetre conversion,
and dataset-label -> COCO-key mapping (fix T2) — is import-light and unit-tested
without any dataset on disk. Writing the bundle (`to_bundle`) lazily uses the
imaging backend in bundle.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import yaml

from .bundle import Intrinsics, Manifest, PerceptionBundle

# TUM depth PNGs are uint16 scaled by 5000 per metre. We store millimetres
# (uint16) everywhere, so mm = png * 1000 / 5000 = png / 5.
TUM_DEPTH_SCALE = 5000.0
TUM_PNG_TO_MM = 1000.0 / TUM_DEPTH_SCALE  # 0.2

# Standard TUM Freiburg intrinsics (full resolution 640x480), by sequence prefix.
TUM_INTRINSICS = {
    "freiburg1": Intrinsics(fx=517.306408, fy=516.469215, cx=318.643040, cy=255.313989),
    "freiburg2": Intrinsics(fx=520.908620, fy=521.007327, cx=325.141442, cy=249.701764),
    "freiburg3": Intrinsics(fx=535.4, fy=539.2, cx=320.1, cy=247.6),
}

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


# --- label mapping (fix T2) ----------------------------------------------
def load_coco_class_map(path: Path | None = None) -> dict[str, dict[str, str]]:
    target = Path(path) if path else _CONFIG_DIR / "coco_class_map.yaml"
    with target.open() as fh:
        return yaml.safe_load(fh) or {}


def map_label_to_coco(
    dataset: str, label: str, class_map: dict[str, dict[str, str]] | None = None
) -> str:
    """Map a raw dataset label to a COCO key, or 'default' if unmapped.

    TUM has no native labels (YOLO already emits COCO names there), so a TUM
    label is returned unchanged.
    """
    if dataset == "tum":
        return label
    class_map = class_map if class_map is not None else load_coco_class_map()
    table = class_map.get(dataset, {})
    return table.get(label.strip().lower(), "default")


# --- TUM RGB-D ------------------------------------------------------------
@dataclass(frozen=True)
class StampedPath:
    timestamp: float
    path: str


def parse_tum_index(text: str) -> list[StampedPath]:
    """Parse a TUM 'rgb.txt'/'depth.txt' index ('# comments' + '<ts> <path>')."""
    out: list[StampedPath] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        out.append(StampedPath(timestamp=float(parts[0]), path=parts[1]))
    return out


def associate(
    rgb: list[StampedPath], depth: list[StampedPath], max_diff_s: float = 0.02
) -> list[tuple[StampedPath, StampedPath]]:
    """Greedy nearest-timestamp RGB<->depth association (TUM convention)."""
    pairs: list[tuple[StampedPath, StampedPath]] = []
    depth_sorted = sorted(depth, key=lambda d: d.timestamp)
    dts = np.array([d.timestamp for d in depth_sorted]) if depth_sorted else np.array([])
    for r in sorted(rgb, key=lambda x: x.timestamp):
        if dts.size == 0:
            break
        j = int(np.argmin(np.abs(dts - r.timestamp)))
        if abs(depth_sorted[j].timestamp - r.timestamp) <= max_diff_s:
            pairs.append((r, depth_sorted[j]))
    return pairs


def tum_depth_png_to_mm(depth_png: np.ndarray) -> np.ndarray:
    """Convert a raw TUM depth PNG (uint16, /5000 m) to uint16 millimetres."""
    mm = depth_png.astype(np.float64) * TUM_PNG_TO_MM
    return np.clip(np.round(mm), 0, 65535).astype(np.uint16)


def intrinsics_for_sequence(name: str) -> Intrinsics:
    for prefix, intr in TUM_INTRINSICS.items():
        if prefix in name:
            return intr
    return TUM_INTRINSICS["freiburg1"]


class TUMReader:
    """Reads a TUM RGB-D sequence directory into a PerceptionBundle.

    Object detection (YOLO) is injected as a `detector` callable so this reader
    stays dependency-light; without one, frames carry empty objects.json and the
    masks/objects are filled in by a later detection pass.
    """

    def __init__(self, sequence_dir: Path | str, intrinsics: Intrinsics | None = None):
        self.dir = Path(sequence_dir)
        self.intrinsics = intrinsics or intrinsics_for_sequence(self.dir.name)

    def pairs(self, max_diff_s: float = 0.02) -> list[tuple[StampedPath, StampedPath]]:
        rgb = parse_tum_index((self.dir / "rgb.txt").read_text())
        depth = parse_tum_index((self.dir / "depth.txt").read_text())
        return associate(rgb, depth, max_diff_s=max_diff_s)

    def to_bundle(
        self,
        out_root: Path | str,
        fps: float = 15.0,
        max_frames: int | None = None,
        detector: Callable[[np.ndarray], list[dict]] | None = None,
    ) -> PerceptionBundle:
        pairs = self.pairs()
        if max_frames is not None:
            pairs = pairs[:max_frames]

        manifest = Manifest(
            session_id=self.dir.name,
            fps=fps,
            frame_count=len(pairs),
            timestamp_start="",  # datasets have no live start (fix Z-M)
            source="tum",
        )
        bundle = PerceptionBundle.create(out_root, manifest, self.intrinsics)

        from .bundle import _imread, _imwrite  # lazy: only used when writing

        for frame_id, (r, d) in enumerate(pairs):
            rgb = _imread(self.dir / r.path)
            depth_png = _imread(self.dir / d.path, unchanged=True)
            bundle.write_rgb(frame_id, rgb)
            bundle.write_depth_mm(frame_id, tum_depth_png_to_mm(depth_png))

            detections = detector(rgb) if detector else []
            # objects.json carries only the JSON-safe detection fields; any mask
            # array is pulled out and written to its own mask_{track_id}.png.
            records = []
            for det in detections:
                mask = det.get("mask")
                if mask is not None and "track_id" in det:
                    _imwrite(
                        bundle.mask_path(frame_id, det["track_id"]),
                        (np.asarray(mask) > 0).astype(np.uint8) * 255,
                    )
                records.append({k: v for k, v in det.items() if k != "mask"})
            bundle.write_objects(frame_id, records)

        # Persist the RGB source timestamps so a later pose evaluation can
        # associate estimated poses with the dataset's ground-truth trajectory
        # without re-running association.
        bundle.write_frame_times([r.timestamp for r, _ in pairs])
        return bundle


# --- placeholders for the other two datasets ------------------------------
class ScanNetReader:
    """ScanNet .sens reader (ground-truth labels + poses).

    Skips YOLO/SAM2 (has instance labels) and optionally Step 3 (has poses);
    maps NYU40 labels to COCO via coco_class_map.yaml. Implemented after TUM.
    """

    def __init__(self, *_args, **_kwargs):
        raise NotImplementedError("ScanNetReader lands after the TUM path is validated (Phase 2).")


# --- Replica (vMAP rendering) --------------------------------------------
# Constants below were VERIFIED against the kxic/vMAP room_0 demo on disk, not
# taken from docs (the docs' iMAP /6553.5 depth scale is WRONG for this render):
#   * depth PNG is already millimetres (metres = png / 1000).
#   * traj_w_c.txt holds CAMERA->WORLD 4x4 poses (our T_world_camera), one
#     flattened row of 16 values per frame.
#   * the rendered world is Z-UP; our contract (scene.json up_axis "y",
#     observed_cloud ground = min Y) is Y-up, so poses are rotated on read.
#   * intrinsics: 1200x680, hfov 90 deg -> fx=fy=600, cx=599.5, cy=339.5.
REPLICA_INTRINSICS = Intrinsics(fx=600.0, fy=600.0, cx=599.5, cy=339.5)

# Z-up -> Y-up: (x, y, z) -> (x, z, -y); a -90 deg rotation about X (det +1, so
# floor stays at min Y and the world stays right-handed). Left-multiplied onto
# every camera->world pose, so back-projected world clouds come out Y-up.
_REPLICA_ZUP_TO_YUP = np.array(
    [[1.0, 0.0, 0.0, 0.0],
     [0.0, 0.0, 1.0, 0.0],
     [0.0, -1.0, 0.0, 0.0],
     [0.0, 0.0, 0.0, 1.0]],
    dtype=np.float64,
)


class ReplicaReader:
    """Reads a vMAP-rendered Replica sequence into a PerceptionBundle.

    Replica ships PERFECT depth, poses, and per-frame INSTANCE masks, so the
    YOLO+SAM2 path is skipped (fix T2 / plan Step 1): the ground-truth instance
    segmentation is written straight into objects.json + mask_{track_id}.png.
    Instance ids are globally consistent across frames, so each id IS a track_id.

    Points at the rendered-sequence dir (e.g. .../room_0/imap/00) holding rgb/,
    depth/, semantic_class/, semantic_instance/, traj_w_c.txt, render_config.yaml.
    """

    def __init__(
        self,
        sequence_dir: Path | str,
        intrinsics: Intrinsics | None = None,
        *,
        zup_to_yup: bool = True,
    ):
        self.dir = Path(sequence_dir)
        self.intrinsics = intrinsics or REPLICA_INTRINSICS
        self.zup_to_yup = zup_to_yup
        self._id2name: dict[int, str] | None = None

    def class_names(self) -> dict[int, str]:
        """class_id -> Replica label, from render_config.yaml (cached).

        render_config.yaml embeds numpy-pickled camera blobs, so it needs the
        full (unsafe) loader; only the plain `classes` list is consumed here.
        """
        if self._id2name is None:
            cfg = yaml.unsafe_load((self.dir / "render_config.yaml").read_text())
            self._id2name = {int(c["id"]): str(c["name"]) for c in cfg["classes"]}
        return self._id2name

    def poses(self) -> np.ndarray:
        """(N, 4, 4) camera->world poses, rotated into the Y-up contract frame."""
        T = np.loadtxt(self.dir / "traj_w_c.txt").reshape(-1, 4, 4)
        if self.zup_to_yup:
            T = _REPLICA_ZUP_TO_YUP[None, :, :] @ T
        return T

    def frame_indices(self) -> list[int]:
        """Sorted rendered-frame indices present on disk (from depth/*.png)."""
        idx = []
        for p in (self.dir / "depth").glob("depth_*.png"):
            try:
                idx.append(int(p.stem.split("_")[1]))
            except (IndexError, ValueError):
                continue
        return sorted(idx)

    def detections(
        self,
        instance_png: np.ndarray,
        class_png: np.ndarray,
        class_map: dict[str, dict[str, str]],
        *,
        keep_default: bool = False,
        min_area: int = 200,
    ) -> list[dict]:
        """GT instances in one frame -> [{track_id, class, bbox, mask}, ...].

        Each instance's class is the majority semantic_class id over its pixels,
        mapped to a COCO key. Structural classes (wall/floor/window/...) map to
        'default' and are dropped unless keep_default=True.
        """
        id2name = self.class_names()
        out: list[dict] = []
        for inst in np.unique(instance_png):
            if inst == 0:  # 0 = void / unlabelled
                continue
            mask = instance_png == inst
            area = int(mask.sum())
            if area < min_area:
                continue
            cid = int(np.bincount(class_png[mask].ravel()).argmax())
            coco = map_label_to_coco("replica", id2name.get(cid, ""), class_map)
            if coco == "default" and not keep_default:
                continue
            ys, xs = np.nonzero(mask)
            out.append({
                "track_id": int(inst),
                "class": coco,
                "bbox": [float(xs.min()), float(ys.min()),
                         float(xs.max() + 1), float(ys.max() + 1)],
                "mask": mask,
            })
        return out

    def to_bundle(
        self,
        out_root: Path | str,
        fps: float = 30.0,
        max_frames: int | None = None,
        stride: int = 1,
        *,
        keep_default: bool = False,
    ) -> PerceptionBundle:
        from .bundle import _imread  # lazy: only used when writing

        class_map = load_coco_class_map()
        all_poses = self.poses()
        indices = self.frame_indices()[:: max(1, stride)]
        if max_frames is not None:
            indices = indices[:max_frames]

        manifest = Manifest(
            session_id=self.dir.parts[-3] if len(self.dir.parts) >= 3 else self.dir.name,
            fps=fps,
            frame_count=len(indices),
            timestamp_start="",
            source="replica",
        )
        bundle = PerceptionBundle.create(out_root, manifest, self.intrinsics)

        poses_out: list[np.ndarray] = []
        for frame_id, src_idx in enumerate(indices):
            rgb = _imread(self.dir / "rgb" / f"rgb_{src_idx}.png")
            depth_mm = _imread(self.dir / "depth" / f"depth_{src_idx}.png", unchanged=True)
            inst = _imread(self.dir / "semantic_instance" / f"semantic_instance_{src_idx}.png", unchanged=True)
            cls = _imread(self.dir / "semantic_class" / f"semantic_class_{src_idx}.png", unchanged=True)

            bundle.write_rgb(frame_id, rgb)
            bundle.write_depth_mm(frame_id, depth_mm)  # already millimetres

            records = []
            for det in self.detections(inst, cls, class_map, keep_default=keep_default):
                mask = det.pop("mask")
                bundle.write_mask(frame_id, det["track_id"], mask)
                records.append(det)
            bundle.write_objects(frame_id, records)
            poses_out.append(all_poses[src_idx])

        bundle.write_poses(poses_out)
        # Replica has no real clock; synthesise frame times from the fps so a
        # later pose evaluation can still associate frames.
        bundle.write_frame_times([i / fps for i in range(len(indices))])
        return bundle
