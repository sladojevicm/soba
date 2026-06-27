"""Physics inference — Step 8 (Phase 9).

Decides each object's MATERIAL + friction + restitution + is_rigid. The plan's
primary path is one BATCHED Claude call (output_config.format) over all objects;
mass is NOT taken from the model (it is computed from geometry in mass.py).

This build ships the INTERFACE plus the deterministic LOOKUP fallback (Step 8's
documented fallback, config/pipeline.yaml physics_lookup) so the assembler runs
with NO API key. The live Claude backend is injected (see `infer`) and is
finalised separately with the claude-api reference + ANTHROPIC_API_KEY — kept out
of this module so a missing key never blocks assembly.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import lookup


@dataclass
class Physics:
    material: str
    friction: float
    restitution: float
    is_rigid: bool
    origin: str            # "vlm" or "lookup"
    reasoning: str = ""


def from_lookup(coco_class: str, *, config_path: str = str(lookup._DEFAULT_CONFIG)) -> Physics:
    p = lookup.physics_lookup(coco_class, config_path)
    return Physics(
        material=p["material"], friction=float(p["friction"]),
        restitution=float(p["restitution"]), is_rigid=bool(p["is_rigid"]),
        origin="lookup", reasoning="",
    )


def infer(coco_classes: list[str], *, backend=None,
          config_path: str = str(lookup._DEFAULT_CONFIG)) -> list[Physics]:
    """Physics for a batch of objects (matched by ARRAY ORDER, fix Y5).

    backend: optional callable(list[str]) -> list[Physics] (the Claude path).
    On None / any backend failure, every object falls back to the lookup table.
    """
    if backend is not None:
        try:
            out = backend(coco_classes)
            if out and len(out) == len(coco_classes):
                return out
        except Exception:
            pass  # any VLM failure -> deterministic lookup (Step 8 fallback)
    return [from_lookup(c, config_path=config_path) for c in coco_classes]
