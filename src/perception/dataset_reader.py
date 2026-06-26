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
        return bundle


# --- placeholders for the other two datasets ------------------------------
class ScanNetReader:
    """ScanNet .sens reader (ground-truth labels + poses).

    Skips YOLO/SAM2 (has instance labels) and optionally Step 3 (has poses);
    maps NYU40 labels to COCO via coco_class_map.yaml. Implemented after TUM.
    """

    def __init__(self, *_args, **_kwargs):
        raise NotImplementedError("ScanNetReader lands after the TUM path is validated (Phase 2).")


class ReplicaReader:
    """Replica reader (perfect poses + segmentation). Implemented after TUM."""

    def __init__(self, *_args, **_kwargs):
        raise NotImplementedError("ReplicaReader lands after the TUM path is validated (Phase 2).")
