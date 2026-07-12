"""PerceptionBundle I/O — the Contract-1 on-disk format (Build Order Phase 2).

This module is the single reader/writer for a capture session. It is split into
two layers:

* metadata + layout (manifest, intrinsics, poses, objects.json, IMU, path
  helpers) — pure stdlib + numpy, so it imports and tests with no camera and no
  imaging libraries;
* pixel I/O (rgb/depth/conf images) — lazy-imports an imaging backend (OpenCV,
  falling back to imageio) only when actually called.

Both `capture.py` (live OAK) and `dataset_reader.py` (TUM/ScanNet/Replica) write
this exact format, so everything downstream is source-agnostic.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np

FRAME_ID_WIDTH = 5  # frames/00001, frames/00150, ...
DEPTH_DTYPE = np.uint16  # depth is millimetres, always


def frame_name(frame_id: int) -> str:
    """Zero-padded frame directory name, e.g. 1 -> '00001'."""
    if frame_id < 0:
        raise ValueError(f"frame_id must be >= 0, got {frame_id}")
    return str(frame_id).zfill(FRAME_ID_WIDTH)


@dataclass(frozen=True)
class Intrinsics:
    """Pinhole intrinsics shared by depth and colour (depth registered to RGB)."""

    fx: float
    fy: float
    cx: float
    cy: float
    distortion: list[float] = field(default_factory=list)
    baseline: float | None = None  # stereo baseline in metres, if known

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Intrinsics":
        return cls(
            fx=float(d["fx"]),
            fy=float(d["fy"]),
            cx=float(d["cx"]),
            cy=float(d["cy"]),
            distortion=list(d.get("distortion") or []),
            baseline=(None if d.get("baseline") is None else float(d["baseline"])),
        )

    def matrix(self) -> np.ndarray:
        """3x3 camera matrix K (float64)."""
        return np.array(
            [[self.fx, 0.0, self.cx], [0.0, self.fy, self.cy], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )


@dataclass(frozen=True)
class Manifest:
    """Session manifest — unified field set (fix Z-M)."""

    session_id: str
    fps: float
    frame_count: int
    timestamp_start: str = ""  # ISO time for live capture; "" for datasets
    source: str = "live"  # "live" or the dataset name ("tum", "scannet", ...)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Manifest":
        return cls(
            session_id=str(d["session_id"]),
            fps=float(d["fps"]),
            frame_count=int(d["frame_count"]),
            timestamp_start=str(d.get("timestamp_start", "")),
            source=str(d.get("source", "live")),
        )


class PerceptionBundle:
    """A capture session on disk. Construct via `create()` or `open()`."""

    def __init__(self, root: Path | str, manifest: Manifest, intrinsics: Intrinsics):
        self.root = Path(root)
        self.manifest = manifest
        self.intrinsics = intrinsics

    # -- lifecycle ---------------------------------------------------------
    @classmethod
    def create(
        cls, root: Path | str, manifest: Manifest, intrinsics: Intrinsics
    ) -> "PerceptionBundle":
        root = Path(root)
        (root / "frames").mkdir(parents=True, exist_ok=True)
        bundle = cls(root, manifest, intrinsics)
        bundle._write_json("manifest.json", manifest.to_dict())
        bundle._write_json("intrinsics.json", intrinsics.to_dict())
        return bundle

    @classmethod
    def open(cls, root: Path | str) -> "PerceptionBundle":
        root = Path(root)
        manifest = Manifest.from_dict(_read_json(root / "manifest.json"))
        intrinsics = Intrinsics.from_dict(_read_json(root / "intrinsics.json"))
        return cls(root, manifest, intrinsics)

    # -- paths -------------------------------------------------------------
    def frame_dir(self, frame_id: int) -> Path:
        return self.root / "frames" / frame_name(frame_id)

    def rgb_path(self, frame_id: int) -> Path:
        return self.frame_dir(frame_id) / "rgb.jpg"

    def depth_path(self, frame_id: int) -> Path:
        return self.frame_dir(frame_id) / "depth.png"

    def conf_path(self, frame_id: int) -> Path:
        return self.frame_dir(frame_id) / "conf.png"

    def objects_path(self, frame_id: int) -> Path:
        return self.frame_dir(frame_id) / "objects.json"

    def mask_path(self, frame_id: int, track_id: int) -> Path:
        # masks are keyed by the RAW track_id (fix Z7), not the final scene id.
        return self.frame_dir(frame_id) / f"mask_{track_id}.png"

    def imu_path(self, frame_id: int) -> Path:
        return self.frame_dir(frame_id) / "imu.jsonl"

    def iter_frame_ids(self) -> Iterator[int]:
        frames = self.root / "frames"
        if not frames.is_dir():
            return
        for d in sorted(p for p in frames.iterdir() if p.is_dir()):
            try:
                yield int(d.name)
            except ValueError:
                continue

    # -- per-frame metadata ------------------------------------------------
    def ensure_frame(self, frame_id: int) -> Path:
        d = self.frame_dir(frame_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_objects(self, frame_id: int, detections: list[dict]) -> None:
        """objects.json: [{track_id, class, bbox, confidence}, ...]."""
        self.ensure_frame(frame_id)
        _write_json_path(self.objects_path(frame_id), detections)

    def read_objects(self, frame_id: int) -> list[dict]:
        p = self.objects_path(frame_id)
        return _read_json(p) if p.exists() else []

    def write_imu(self, frame_id: int, samples: Iterable[dict]) -> None:
        self.ensure_frame(frame_id)
        with self.imu_path(frame_id).open("w") as fh:
            for s in samples:
                fh.write(json.dumps(s) + "\n")

    def read_imu(self, frame_id: int) -> list[dict]:
        p = self.imu_path(frame_id)
        if not p.exists():
            return []
        with p.open() as fh:
            return [json.loads(line) for line in fh if line.strip()]

    # -- poses (written by Step 3) ----------------------------------------
    def write_poses(self, poses: list[np.ndarray] | np.ndarray) -> None:
        """poses.json: [{frame_id, T_world_camera: 4x4 nested lists}]."""
        records = []
        for i, T in enumerate(poses):
            T = np.asarray(T, dtype=np.float64)
            if T.shape != (4, 4):
                raise ValueError(f"pose {i} must be 4x4, got {T.shape}")
            records.append({"frame_id": i, "T_world_camera": T.tolist()})
        _write_json_path(self.root / "poses.json", records)

    def read_poses(self) -> list[np.ndarray]:
        records = _read_json(self.root / "poses.json")
        out: list[np.ndarray] = []
        for rec in sorted(records, key=lambda r: r["frame_id"]):
            out.append(np.array(rec["T_world_camera"], dtype=np.float64))
        return out

    # -- frame timestamps (source clock, e.g. TUM stamps) -----------------
    # frame_times[i] is the source timestamp for frame i (seconds). Needed to
    # associate estimated poses with a ground-truth trajectory; written by the
    # dataset reader / live capture so consumers don't have to re-derive it.
    def times_path(self) -> Path:
        return self.root / "frame_times.json"

    def write_frame_times(self, times: Iterable[float]) -> None:
        _write_json_path(self.times_path(), [float(t) for t in times])

    def read_frame_times(self) -> list[float]:
        p = self.times_path()
        return [float(t) for t in _read_json(p)] if p.exists() else []

    # -- pixel I/O (lazy imaging backend) ---------------------------------
    def write_depth_mm(self, frame_id: int, depth_mm: np.ndarray) -> None:
        if depth_mm.dtype != DEPTH_DTYPE:
            depth_mm = np.clip(np.round(depth_mm), 0, 65535).astype(DEPTH_DTYPE)
        self.ensure_frame(frame_id)
        _imwrite(self.depth_path(frame_id), depth_mm)

    def read_depth_mm(self, frame_id: int) -> np.ndarray:
        depth = _imread(self.depth_path(frame_id), unchanged=True)
        return depth.astype(DEPTH_DTYPE, copy=False)

    def write_rgb(self, frame_id: int, rgb: np.ndarray) -> None:
        self.ensure_frame(frame_id)
        _imwrite(self.rgb_path(frame_id), rgb, quality=90)

    def read_rgb(self, frame_id: int) -> np.ndarray:
        return _imread(self.rgb_path(frame_id))

    def write_conf(self, frame_id: int, conf: np.ndarray) -> None:
        self.ensure_frame(frame_id)
        _imwrite(self.conf_path(frame_id), conf.astype(np.uint8, copy=False))

    def read_conf(self, frame_id: int) -> np.ndarray:
        return _imread(self.conf_path(frame_id), unchanged=True).astype(np.uint8)

    # -- masks (refined by Step 2 / SAM2; keyed by RAW track_id, fix Z7) ---
    def write_mask(self, frame_id: int, track_id: int, mask: np.ndarray) -> None:
        """Write a binary object mask (any truthy array -> 0/255 uint8 PNG)."""
        self.ensure_frame(frame_id)
        binary = (np.asarray(mask) > 0).astype(np.uint8) * 255
        _imwrite(self.mask_path(frame_id, track_id), binary)

    def read_mask(self, frame_id: int, track_id: int) -> np.ndarray:
        return _imread(self.mask_path(frame_id, track_id), unchanged=True)

    # -- shared best-frame crops (fix Z-B-crop) ---------------------------
    # Staged by track_id here; Step 10 maps them into objects/{id}/crop.jpg
    # once the slug(class)_oid id exists.
    def crops_dir(self) -> Path:
        return self.root / "crops"

    def crop_path(self, track_id: int) -> Path:
        # PNG since 2026-07-04: the crop carries the OBJECT MASK as its ALPHA
        # channel so image-to-3D models skip their own background removal.
        # TripoSG's BriaRMBG deleted a WHITE tabletop as "background" and then
        # faithfully generated the leftover wooden rim — we hold the exact
        # mask, so the generator must never re-guess the segmentation.
        return self.crops_dir() / f"crop_{track_id}.png"

    def write_crop(self, track_id: int, rgb: np.ndarray) -> None:
        """rgb: HxWx3 (legacy) or HxWx4 RGBA with the mask as alpha."""
        self.crops_dir().mkdir(parents=True, exist_ok=True)
        _imwrite(self.crop_path(track_id), rgb)

    def read_crop(self, track_id: int) -> np.ndarray:
        return _imread(self.crop_path(track_id))

    # -- internals ---------------------------------------------------------
    def _write_json(self, name: str, data) -> None:
        _write_json_path(self.root / name, data)


# --- module-level helpers -------------------------------------------------
def _read_json(path: Path) -> dict | list:
    with Path(path).open() as fh:
        return json.load(fh)


def _write_json_path(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        json.dump(data, fh, indent=2)


def _imaging_backend():
    """Return a thin (imwrite, imread) pair, preferring OpenCV then imageio."""
    try:
        import cv2  # type: ignore

        def _w(path, arr, quality=None):
            params = []
            if quality is not None:
                params = [cv2.IMWRITE_JPEG_QUALITY, int(quality)]
            img = arr
            if arr.ndim == 3 and arr.shape[2] == 3:  # RGB -> BGR for cv2
                img = arr[:, :, ::-1]
            elif arr.ndim == 3 and arr.shape[2] == 4:  # RGBA -> BGRA
                img = arr[:, :, [2, 1, 0, 3]]
            if not cv2.imwrite(str(path), img, params):
                raise OSError(f"cv2 failed to write {path}")

        def _r(path, unchanged=False):
            flag = cv2.IMREAD_UNCHANGED if unchanged else cv2.IMREAD_COLOR
            img = cv2.imread(str(path), flag)
            if img is None:
                raise FileNotFoundError(path)
            if img.ndim == 3 and img.shape[2] == 3:  # BGR -> RGB
                img = img[:, :, ::-1]
            elif img.ndim == 3 and img.shape[2] == 4:  # BGRA -> RGBA
                img = img[:, :, [2, 1, 0, 3]]
            return img

        return _w, _r
    except ImportError:
        pass

    try:
        import imageio.v3 as iio  # type: ignore

        def _w(path, arr, quality=None):
            iio.imwrite(str(path), arr)

        def _r(path, unchanged=False):
            return iio.imread(str(path))

        return _w, _r
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "No imaging backend available. Install the 'recon' extra "
            "(opencv-python) or imageio to read/write frame images."
        ) from exc


def _imwrite(path: Path, arr: np.ndarray, quality: int | None = None) -> None:
    w, _ = _imaging_backend()
    w(path, arr, quality=quality)


def _imread(path: Path, unchanged: bool = False) -> np.ndarray:
    _, r = _imaging_backend()
    return r(path, unchanged=unchanged)
