"""SAM2 mask refinement + shared best-frame crop — Step 2 (Build Order Phase 4).

YOLO masks (512x288, upsampled to full resolution) are blocky, so background
depth leaks through their rough edges and corrupts an object's geometry. SAM2,
prompted with the YOLO bounding box on each object's FIRST frame and propagated
through the sequence in video mode, produces pixel-perfect full-resolution masks.

This module also runs the SHARED best-frame crop (fix Z-B-crop): for EVERY
tracked object it picks the frame maximising (SAM2 confidence x masked area),
crops the RGB to the mask bbox + a margin, and writes crops/crop_{track_id}.jpg.
That crop is consumed later by the generative model (Step 6) and the Claude
physics call (Step 8), so it must be produced here for ALL objects — not inside
the generative-only path.

Design mirrors the rest of the package: the heavy SAM2 model lives behind an
injectable `Sam2Backend` (Protocol), so the orchestration and geometry here are
pure numpy and unit-testable without torch, a checkpoint, or a GPU.
`Sam2VideoPredictor` is the real backend (lazy-imports `sam2`).

Masks stay keyed by the RAW track_id (fix Z7); the final slug(class)_oid id is
assigned at Step 10, which also relocates the staged crops into objects/{id}/.
Datasets that ship instance masks (ScanNet, Replica) skip this step entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from perception.bundle import PerceptionBundle

# Sources that already provide ground-truth instance masks -> no SAM2 needed.
GROUND_TRUTH_SOURCES = {"scannet", "replica"}

BBox = tuple[float, float, float, float]  # x0, y0, x1, y1 (pixels, full-res)


@dataclass(frozen=True)
class Prompt:
    """The SAM2 prompt for one object: its first sighting and YOLO bbox."""

    track_id: int
    frame_id: int
    bbox: BBox
    class_name: str = ""


@dataclass(frozen=True)
class FrameMask:
    """A refined mask for one (object, frame), with a confidence score."""

    frame_id: int
    mask: np.ndarray  # 2D, any truthy dtype
    score: float = 1.0


class Sam2Backend(Protocol):
    """Produces refined per-frame masks for each prompted object."""

    def refine(
        self, bundle: PerceptionBundle, prompts: list[Prompt]
    ) -> dict[int, list[FrameMask]]: ...


# --- pure geometry / selection helpers (no imaging backend needed) --------
def track_first_frames(bundle: PerceptionBundle) -> dict[int, Prompt]:
    """First frame each track_id is detected, with its bbox (the SAM2 prompt).

    A detection without a bbox can't prompt SAM2 and is skipped.
    """
    seen: dict[int, Prompt] = {}
    for fid in bundle.iter_frame_ids():
        for det in bundle.read_objects(fid):
            tid = det.get("track_id")
            if tid is None or tid in seen or "bbox" not in det:
                continue
            x0, y0, x1, y1 = det["bbox"]
            seen[tid] = Prompt(
                track_id=int(tid),
                frame_id=int(fid),
                bbox=(float(x0), float(y0), float(x1), float(y1)),
                class_name=str(det.get("class", "")),
            )
    return seen


def bbox_from_mask(mask: np.ndarray) -> BBox | None:
    """Tight xyxy bbox of a mask's truthy pixels, or None if empty."""
    ys, xs = np.nonzero(np.asarray(mask) > 0)
    if xs.size == 0:
        return None
    return (float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1))


def crop_with_margin(image: np.ndarray, bbox: BBox, margin: float = 0.10) -> np.ndarray:
    """Crop `image` to `bbox` grown by `margin` (fraction of side), clamped."""
    h, w = image.shape[:2]
    x0, y0, x1, y1 = bbox
    mx, my = (x1 - x0) * margin, (y1 - y0) * margin
    cx0 = int(max(0, np.floor(x0 - mx)))
    cy0 = int(max(0, np.floor(y0 - my)))
    cx1 = int(min(w, np.ceil(x1 + mx)))
    cy1 = int(min(h, np.ceil(y1 + my)))
    cx1 = max(cx1, cx0 + 1)  # never empty
    cy1 = max(cy1, cy0 + 1)
    return image[cy0:cy1, cx0:cx1]


def select_best_mask(frames: list[FrameMask]) -> FrameMask:
    """Best crop source = argmax(confidence x masked pixel area) (fix Z-B-crop)."""
    return max(frames, key=lambda fm: float(fm.score) * int(np.count_nonzero(fm.mask)))


# --- orchestration --------------------------------------------------------
def refine_masks(
    bundle: PerceptionBundle,
    backend: Sam2Backend,
    *,
    write_crops: bool = True,
    margin: float = 0.10,
    force: bool = False,
    track_ids: set[int] | None = None,
) -> dict[int, dict]:
    """Refine every tracked object's masks and stage its best-frame crop.

    Returns a per-track summary {track_id: {"frames": n, "best_frame": fid|None}}.
    No-ops (returns {}) for ground-truth-mask datasets unless `force=True`.
    `track_ids` restricts refinement to those tracks (detector flicker — 1-frame
    phantom tracks — is real and SAM2 video is expensive; the caller filters).
    """
    if not force and bundle.manifest.source in GROUND_TRUTH_SOURCES:
        return {}

    prompts = [
        p for p in track_first_frames(bundle).values()
        if track_ids is None or p.track_id in track_ids
    ]
    if not prompts:
        return {}

    results = backend.refine(bundle, prompts)

    summary: dict[int, dict] = {}
    for track_id, frames in results.items():
        frames = sorted(frames, key=lambda fm: fm.frame_id)
        for fm in frames:
            bundle.write_mask(fm.frame_id, track_id, fm.mask)

        best_frame: int | None = None
        if write_crops and frames:
            best = select_best_mask(frames)
            box = bbox_from_mask(best.mask)
            if box is not None:
                bundle.write_crop(
                    track_id, crop_with_margin(bundle.read_rgb(best.frame_id), box, margin)
                )
                best_frame = best.frame_id
        summary[track_id] = {"frames": len(frames), "best_frame": best_frame}
    return summary


# --- real backend (lazy import; requires sam2 + checkpoint + GPU) ----------
class Sam2VideoPredictor:
    """Real SAM2 video-mode backend.

    Requires the `sam2` package (facebookresearch/sam2) + a checkpoint; a CUDA
    GPU is strongly recommended. Lazy-imported so the rest of this module (and
    its tests) run without torch/sam2 installed. The orchestration above is
    covered by fake-backend tests; this wrapper is exercised end-to-end only
    when weights are available (Build Order Phase 4 validation on TUM).
    """

    def __init__(
        self,
        checkpoint: str,
        model_cfg: str = "configs/sam2.1/sam2.1_hiera_l.yaml",
        device: str | None = None,
    ):
        self.checkpoint = checkpoint
        self.model_cfg = model_cfg
        self.device = device

    def refine(
        self, bundle: PerceptionBundle, prompts: list[Prompt]
    ) -> dict[int, list[FrameMask]]:
        import contextlib
        import shutil
        import tempfile

        import torch
        from sam2.build_sam import build_sam2_video_predictor

        device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
        predictor = build_sam2_video_predictor(self.model_cfg, self.checkpoint, device=device)

        # SAM2's video predictor is written for bf16 autocast (the official
        # examples wrap ALL inference in it); without it the memory-attention
        # matmul crashes on a dtype mismatch (BFloat16 vs Float).
        amp = (
            torch.autocast("cuda", dtype=torch.bfloat16)
            if device == "cuda"
            else contextlib.nullcontext()
        )

        frame_ids = sorted(bundle.iter_frame_ids())
        idx_of = {fid: i for i, fid in enumerate(frame_ids)}

        # SAM2's video predictor wants a directory of JPEG frames named <i>.jpg.
        staging = Path(tempfile.mkdtemp(prefix="sam2_frames_"))
        try:
            for i, fid in enumerate(frame_ids):
                dst = staging / f"{i}.jpg"
                src = bundle.rgb_path(fid).resolve()
                try:
                    dst.symlink_to(src)
                except OSError:
                    shutil.copyfile(src, dst)

            with amp:
                state = predictor.init_state(video_path=str(staging))
                for p in prompts:
                    x0, y0, x1, y1 = p.bbox
                    predictor.add_new_points_or_box(
                        inference_state=state,
                        frame_idx=idx_of[p.frame_id],
                        obj_id=p.track_id,
                        box=np.array([x0, y0, x1, y1], dtype=np.float32),
                    )

                out: dict[int, list[FrameMask]] = {p.track_id: [] for p in prompts}
                for sam_idx, obj_ids, mask_logits in predictor.propagate_in_video(state):
                    fid = frame_ids[sam_idx]
                    for k, oid in enumerate(obj_ids):
                        logit = mask_logits[k].squeeze()
                        mask = (logit > 0.0).detach().cpu().numpy().astype(bool)
                        score = float(torch.sigmoid(logit.max()).item())
                        out[int(oid)].append(FrameMask(frame_id=fid, mask=mask, score=score))
            return out
        finally:
            shutil.rmtree(staging, ignore_errors=True)
