"""Live Claude physics backend — Step 8 (plan §13).

One BATCHED Messages call over all objects: each object contributes its
ANNOTATED crop (green box 4% inside the bbox, green centroid dot, red ruler
labelled with the real-world longest dimension — fix Y4) plus a one-line label;
the response is schema-constrained via output_config.format (fix C1) and
matched back to the inputs by ARRAY ORDER (fix Y5). Mass is NOT asked from the
model — material class only; mass comes from geometry (mass.py).

Activation: `make_backend()` returns None unless ANTHROPIC_API_KEY is set, so
assembly always works key-less on the lookup table (vlm.infer's fallback).
Guards (fix C2/C4): stop_reason "refusal" -> None (lookup); "max_tokens" ->
ONE retry at a doubled cap, then None. friction/restitution are clamped in
Python (structured-output schema can't carry numeric bounds).
"""

from __future__ import annotations

import base64
import json
import logging
import os

from . import lookup, vlm

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a physics property estimator for a rigid-body simulator. "
    "You receive images of real objects with a green bounding box, a centroid "
    "dot, and a red ruler showing the object's real-world longest dimension. "
    "For each object, estimate its surface material and physics properties. "
    "friction is 0.0-2.0; restitution is 0.0-1.0. fill_fraction (0.0-1.0) is "
    "the fraction of the object's overall volume that is solid material, "
    "judged from its visible construction: a solid block ~1.0, packed/full "
    "containers ~0.7-1.0, upholstered furniture ~0.3-0.6, hollow shells and "
    "empty containers ~0.03-0.15, thin frames (wire racks, bed frames, tube "
    "chairs) ~0.01-0.05. Return estimates for ALL objects, in the order "
    "shown. Keep each 'reasoning' to ONE short clause."
)

MATERIALS = ["wood", "metal", "plastic", "rubber", "glass",
             "ceramic", "fabric", "paper", "stone", "unknown"]

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["objects"],
    "properties": {
        "objects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["material", "friction", "restitution",
                             "is_rigid", "fill_fraction", "reasoning"],
                "properties": {
                    "material": {"type": "string", "enum": MATERIALS},
                    "friction": {"type": "number"},
                    "restitution": {"type": "number"},
                    "is_rigid": {"type": "boolean"},
                    "fill_fraction": {"type": "number"},
                    "reasoning": {"type": "string"},
                },
            },
        }
    },
}


def annotate_crop(crop_path, longest_dim_m: float | None) -> bytes | None:
    """objects/{id}/crop.jpg -> annotated PNG bytes (fix Y4), or None.

    Green rectangle 4% inside the image bounds, green dot at the centre, red
    ruler along the bottom labelled with the metric longest dimension — the
    scale cue is what lets the model tell a toy car from a real one.
    """
    import cv2
    import numpy as np

    if crop_path is None:
        return None
    img = cv2.imread(str(crop_path))
    if img is None:
        return None
    h, w = img.shape[:2]
    mx, my = max(1, int(w * 0.04)), max(1, int(h * 0.04))
    cv2.rectangle(img, (mx, my), (w - mx - 1, h - my - 1), (0, 255, 0), 2)
    cv2.circle(img, (w // 2, h // 2), max(2, min(h, w) // 60), (0, 255, 0), -1)
    if longest_dim_m:
        y = h - max(4, my // 2)
        cv2.line(img, (mx, y), (w - mx - 1, y), (0, 0, 255), 2)
        cv2.putText(img, f"{longest_dim_m:.2f} m", (mx, y - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, min(h, w) / 500.0, (0, 0, 255), 1,
                    cv2.LINE_AA)
    ok, png = cv2.imencode(".png", img)
    return png.tobytes() if ok else None


def _clamp(v, lo, hi):
    return max(lo, min(hi, float(v)))


class ClaudeBackend:
    """vlm.infer backend: callable(classes, crops, dims_m) -> list[Physics].

    `wants_context = True` tells vlm.infer to pass the crops + metric dims
    (older backends take just the class list).
    """

    wants_context = True

    def __init__(self, *, model: str, max_tokens: int, client=None):
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.max_tokens = max_tokens

    def __call__(self, coco_classes, crops=None, dims_m=None):
        crops = crops or [None] * len(coco_classes)
        dims_m = dims_m or [None] * len(coco_classes)
        content = []
        for i, (cls, crop, dim) in enumerate(zip(coco_classes, crops, dims_m), 1):
            png = annotate_crop(crop, dim)
            if png is not None:
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": "image/png",
                    "data": base64.standard_b64encode(png).decode()}})
            label = f"Object {i}: {cls}"
            if png is None:  # no crop staged -> the model works from class+dims
                label += " (no image available"
                label += f", longest dimension {dim:.2f} m)" if dim else ")"
            content.append({"type": "text", "text": label})

        data = self._call(content)
        if data is None:
            return None  # vlm.infer falls back to the lookup table
        ests = data["objects"]
        if len(ests) != len(coco_classes):
            log.warning("Claude returned %d estimates for %d objects -> lookup",
                        len(ests), len(coco_classes))
            return None
        out = []
        for est in ests:  # matched by ARRAY ORDER (fix Y5)
            mat = est["material"] if est["material"] in MATERIALS else "unknown"
            out.append(vlm.Physics(
                material=mat,
                friction=_clamp(est["friction"], 0.0, 2.0),
                restitution=_clamp(est["restitution"], 0.0, 1.0),
                is_rigid=bool(est["is_rigid"]),
                origin="vlm",
                reasoning=str(est.get("reasoning", ""))[:300],
                fill_fraction=_clamp(est["fill_fraction"], 0.005, 1.0)
                if est.get("fill_fraction") is not None else None,
            ))
        return out

    def _call(self, content, *, _retried: bool = False):
        """The guarded Messages call (fix C2/C4): refusal -> None; max_tokens ->
        one retry at a doubled cap; anything else parses the schema'd JSON."""
        max_tokens = self.max_tokens * (2 if _retried else 1)
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],  # fix C5 (no-op while short)
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        )
        if response.stop_reason == "refusal":
            log.warning("Claude physics call refused -> lookup fallback")
            return None
        if response.stop_reason == "max_tokens":
            if _retried:
                log.warning("Claude physics call truncated twice -> lookup")
                return None
            log.info("Claude physics call truncated at %d tokens -> retrying",
                     max_tokens)
            return self._call(content, _retried=True)
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)  # schema-valid by construction


def make_backend(config_path: str = str(lookup._DEFAULT_CONFIG)):
    """The Step-8 backend for vlm.infer, or None when it can't run.

    Requires ANTHROPIC_API_KEY (never blocks key-less assembly). Model comes
    from config vlm.model (claude-opus-4-8); VID2SIM_VLM_MODEL overrides (e.g.
    the config's cost_option claude-haiku-4-5 for cheap runs). VID2SIM_VLM=0
    disables even with a key.
    """
    if os.environ.get("VID2SIM_VLM", "1") == "0":
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        cfg = lookup.load_config(config_path).get("vlm", {})
        model = os.environ.get("VID2SIM_VLM_MODEL") or cfg.get("model", "claude-opus-4-8")
        max_tokens = int(cfg.get("max_tokens", 4096))
        return ClaudeBackend(model=model, max_tokens=max_tokens)
    except Exception as e:  # SDK missing/broken -> lookup, never a crash
        log.warning("Claude physics backend unavailable (%s) -> lookup", e)
        return None
