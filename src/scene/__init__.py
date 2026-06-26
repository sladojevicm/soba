"""VID2SIM scene contract (scene.json) — schema loading + validation.

This package owns the frozen `scene.json` contract (Contract 3, v2.0), the
single typed boundary between the pipeline and the browser viewer. Locked
first (Build Order Phase 1) because everything downstream depends on it.
"""

from . import schema

__all__ = ["schema"]
