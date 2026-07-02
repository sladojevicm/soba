"""Host-side object detection + IoU tracking (plan Step 1, dataset path).

The live OAK runs YOLO on its NPU; datasets have no detections, so this module
provides the host-side equivalent: an ultralytics YOLO segmentation model per
frame, linked across frames by the plan's IoU tracker (IoU > 0.4 with the
previous frame -> same track id; unseen 5+ frames -> retired, a reappearing
object gets a NEW id). The result is a `detector` callable for
`TUMReader.to_bundle(detector=...)` emitting the bundle contract:
    {track_id, class, bbox: [x0, y0, x1, y1], confidence, mask}
with COCO class names exactly as the detector emits them (fix R2) and masks at
full frame resolution (SAM2 refines them in Step 2).

The YOLO dependency is lazy and injectable: `TrackingDetector` takes any
`infer(rgb) -> [{class, bbox, confidence, mask}]` callable, so tests run
without ultralytics and another model can be swapped in.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

CONF_THRESHOLD = 0.4   # plan Step 1: discard detections below
IOU_SAME_TRACK = 0.4   # plan Step 1: IoU > 0.4 between consecutive frames
RETIRE_AFTER = 5       # plan Step 1: absent 5+ frames -> retired


def bbox_iou(a, b) -> float:
    """IoU of two [x0, y0, x1, y1] boxes."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    iw = min(ax1, bx1) - max(ax0, bx0)
    ih = min(ay1, by1) - max(ay0, by0)
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return float(inter / union) if union > 0 else 0.0


class IouTracker:
    """Greedy IoU tracker: stable track ids across consecutive frames."""

    def __init__(self, iou_thresh: float = IOU_SAME_TRACK,
                 retire_after: int = RETIRE_AFTER):
        self.iou_thresh = iou_thresh
        self.retire_after = retire_after
        self._next_id = 0
        self._live: dict[int, dict] = {}  # tid -> {bbox, cls, missed}

    def update(self, detections: list[dict]) -> list[int]:
        """Assign a track id per detection (same order). Call once per frame."""
        # Greedy best-IoU matching against last-seen boxes of the SAME class:
        # highest IoU pair first, each track and detection used at most once.
        cand = []
        for di, det in enumerate(detections):
            for tid, tr in self._live.items():
                if tr["cls"] != det["class"]:
                    continue
                iou = bbox_iou(det["bbox"], tr["bbox"])
                if iou > self.iou_thresh:
                    cand.append((iou, di, tid))
        cand.sort(reverse=True)

        ids: list[int | None] = [None] * len(detections)
        taken: set[int] = set()
        for _, di, tid in cand:
            if ids[di] is not None or tid in taken:
                continue
            ids[di] = tid
            taken.add(tid)

        for di, det in enumerate(detections):
            if ids[di] is None:
                ids[di] = self._next_id
                self._next_id += 1
            self._live[ids[di]] = {"bbox": det["bbox"], "cls": det["class"],
                                   "missed": 0}

        # age out tracks not matched this frame
        for tid in list(self._live):
            if tid in taken or tid in ids:
                continue
            self._live[tid]["missed"] += 1
            if self._live[tid]["missed"] >= self.retire_after:
                del self._live[tid]
        return ids  # type: ignore[return-value]


class TrackingDetector:
    """`detector` callable for `to_bundle(detector=...)`: infer + track.

    `infer(rgb)` returns per-frame [{class, bbox, confidence, mask}] (no ids);
    this wrapper threads the IoU tracker through consecutive calls and emits
    the full bundle contract dicts.
    """

    def __init__(self, infer: Callable[[np.ndarray], list[dict]],
                 tracker: IouTracker | None = None):
        self._infer = infer
        self._tracker = tracker or IouTracker()

    def __call__(self, rgb: np.ndarray) -> list[dict]:
        dets = self._infer(rgb)
        ids = self._tracker.update(dets)
        return [
            {"track_id": tid, "class": det["class"],
             "bbox": [float(v) for v in det["bbox"]],
             "confidence": float(det["confidence"]),
             "mask": det.get("mask")}
            for tid, det in zip(ids, dets)
        ]


def yolo_infer(model: str = "yolo11s-seg.pt", conf: float = CONF_THRESHOLD,
               device: str | None = None) -> Callable[[np.ndarray], list[dict]]:
    """An `infer` callable backed by an ultralytics YOLO segmentation model.

    Lazy-imports ultralytics; weights auto-download on first use. Masks are
    returned at FULL frame resolution (retina_masks) as bool arrays. NOTE:
    ultralytics treats a raw numpy array as BGR (the cv2 convention) — the
    bundle is RGB throughout, so the channel flip here is load-bearing.
    """
    from ultralytics import YOLO  # lazy

    net = YOLO(model)

    def infer(rgb: np.ndarray) -> list[dict]:
        res = net.predict(
            np.ascontiguousarray(rgb[:, :, ::-1]),  # RGB -> BGR
            conf=conf, retina_masks=True, verbose=False, device=device,
        )[0]
        out: list[dict] = []
        if res.boxes is None or len(res.boxes) == 0:
            return out
        boxes = res.boxes.xyxy.cpu().numpy()
        confs = res.boxes.conf.cpu().numpy()
        clses = res.boxes.cls.cpu().numpy().astype(int)
        masks = res.masks.data.cpu().numpy() if res.masks is not None else None
        for i in range(len(boxes)):
            mask = None
            if masks is not None and i < len(masks):
                mask = masks[i] > 0.5
                if mask.shape != rgb.shape[:2]:  # safety: retina should match
                    continue
            out.append({"class": res.names[clses[i]],
                        "bbox": boxes[i].tolist(),
                        "confidence": float(confs[i]),
                        "mask": mask})
        return out

    return infer


def yolo_detector(model: str = "yolo11s-seg.pt", conf: float = CONF_THRESHOLD,
                  device: str | None = None) -> TrackingDetector:
    """The one-liner: a tracked YOLO `detector` for `to_bundle(detector=...)`."""
    return TrackingDetector(yolo_infer(model, conf, device))
