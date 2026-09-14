"""Plain RGB video -> PerceptionBundle (frames + manifest, no depth, no poses).

Step 1 of the RGB-only ingest path (docs/log/2026-09-14-rgb-video-step1.md).
A stock phone camera app records an MP4/MOV with no depth channel; this reader
decodes it into the bundle layout every later stage reads, writing ONLY what a
video can supply:

    frames/NNNNN/rgb.jpg     every `stride`-th decoded frame
    frame_times.json         decode index / source fps, seconds
    manifest.json            source="video", fps = source fps / stride
    intrinsics.json          a PLACEHOLDER pinhole from a nominal horizontal
                             FOV (default 70 deg); replaced by the focal MASt3R
                             solves when `slam.Mast3rEstimator.estimate(...,
                             export_root=...)` exports the anchor bundle.

It writes no depth.png, no conf.png, no poses.json and no objects/masks: those
come from the MASt3R export (depth, conf, poses, intrinsics) and from the
YOLO+SAM2 detection seam (objects.json, masks) in the "preparing" stage. A
bundle straight out of this reader therefore fails today's upload validation
and `scripts/run_assemble.py` by design; it is an intermediate artefact.

Decoding uses OpenCV (the `recon` extra). OpenCV >= 4.5 applies the container's
rotation tag automatically (CAP_PROP_ORIENTATION_AUTO), so a portrait phone
clip decodes upright; we do not re-rotate.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np

from .bundle import Intrinsics, Manifest, PerceptionBundle

log = logging.getLogger(__name__)

DEFAULT_HFOV_DEG = 70.0  # nominal phone main camera; only a placeholder


def default_intrinsics(width: int, height: int, hfov_deg: float = DEFAULT_HFOV_DEG) -> Intrinsics:
    """Pinhole intrinsics from a nominal horizontal field of view.

    fx = fy = (w/2) / tan(hfov/2), principal point at the image centre. Marked
    as a placeholder in the manifest consumer's eyes by `source == "video"`;
    the MASt3R export overwrites intrinsics.json with the solved focal."""
    if width <= 0 or height <= 0:
        raise ValueError(f"image size must be positive, got {width}x{height}")
    if not 0.0 < hfov_deg < 180.0:
        raise ValueError(f"hfov_deg must be in (0, 180), got {hfov_deg}")
    f = (width / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
    return Intrinsics(fx=f, fy=f, cx=width / 2.0, cy=height / 2.0)


class VideoReader:
    """Decode a plain RGB video into a PerceptionBundle (frames + manifest only)."""

    def __init__(self, path: Path | str, intrinsics: Intrinsics | None = None,
                 hfov_deg: float = DEFAULT_HFOV_DEG):
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        self.intrinsics = intrinsics
        self.hfov_deg = hfov_deg

    def probe(self) -> tuple[float, int, int, int]:
        """(source fps, reported frame count, width, height). Frame count is
        the container's estimate and may be off by a few frames."""
        import cv2

        cap = cv2.VideoCapture(str(self.path))
        try:
            if not cap.isOpened():
                raise OSError(f"cannot open video {self.path}")
            fps = float(cap.get(cv2.CAP_PROP_FPS)) or 0.0
            n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        finally:
            cap.release()
        return fps, n, w, h

    def to_bundle(
        self,
        out_root: Path | str,
        *,
        stride: int = 1,
        max_frames: int | None = None,
        session_id: str | None = None,
        fallback_fps: float = 30.0,
    ) -> PerceptionBundle:
        """Write every `stride`-th frame as frames/NNNNN/rgb.jpg (ids 0..K-1).

        `fps` in the manifest is the EFFECTIVE rate of the written frames
        (source fps / stride); frame_times.json keeps the source timestamps so
        nothing downstream has to know the stride. Containers that report no
        fps get `fallback_fps`."""
        import cv2

        if stride < 1:
            raise ValueError(f"stride must be >= 1, got {stride}")
        if max_frames is not None and max_frames < 1:
            raise ValueError(f"max_frames must be >= 1, got {max_frames}")

        cap = cv2.VideoCapture(str(self.path))
        if not cap.isOpened():
            raise OSError(f"cannot open video {self.path}")
        try:
            src_fps = float(cap.get(cv2.CAP_PROP_FPS)) or 0.0
            if src_fps <= 0.0:
                log.warning("video %s reports no fps; assuming %.1f", self.path.name, fallback_fps)
                src_fps = fallback_fps

            bundle: PerceptionBundle | None = None
            times: list[float] = []
            frame_id = 0
            idx = 0
            while True:
                ok, bgr = cap.read()
                if not ok or bgr is None:
                    break
                if idx % stride == 0:
                    if bundle is None:
                        h, w = bgr.shape[:2]
                        intr = self.intrinsics or default_intrinsics(w, h, self.hfov_deg)
                        manifest = Manifest(
                            session_id=session_id or self.path.stem,
                            fps=src_fps / stride,
                            frame_count=0,  # patched below once we know
                            timestamp_start="",
                            source="video",
                        )
                        bundle = PerceptionBundle.create(out_root, manifest, intr)
                        if self.intrinsics is None:
                            log.info("video %s: placeholder intrinsics fx=fy=%.1f from %.0f deg HFOV "
                                     "(replaced by the MASt3R export)", self.path.name, intr.fx,
                                     self.hfov_deg)
                    rgb = np.ascontiguousarray(bgr[:, :, ::-1])
                    bundle.write_rgb(frame_id, rgb)
                    times.append(idx / src_fps)
                    frame_id += 1
                    if max_frames is not None and frame_id >= max_frames:
                        break
                idx += 1
        finally:
            cap.release()

        if bundle is None:
            raise ValueError(f"video {self.path} yielded no decodable frames")

        # frame_count is only known after decoding: rewrite the manifest.
        bundle.manifest = Manifest(
            session_id=bundle.manifest.session_id,
            fps=bundle.manifest.fps,
            frame_count=frame_id,
            timestamp_start=bundle.manifest.timestamp_start,
            source=bundle.manifest.source,
        )
        bundle._write_json("manifest.json", bundle.manifest.to_dict())
        bundle.write_frame_times(times)
        return bundle
