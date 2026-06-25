"""VID2SIM scene contract (scene.json) — schema loading + validation.

This package owns the frozen `scene.json` contract (ADR-001), the single
typed boundary between the four pipeline stages. Migrated from the original
vid2sim repo as the stable foundation for the rewrite.
"""

from . import schema

__all__ = ["schema"]
