"""SOBA scene contract (scene.json) — schema loading + validation.

This package owns the frozen `scene.json` contract (Contract 3, v2.0), the
single typed boundary between the pipeline and the browser viewer. Locked
first (Build Order Phase 1) because everything downstream depends on it.
"""

from . import schema

# Phase-9 assembly modules. Imported lazily-friendly: these pull numpy but defer
# open3d to inside their functions, so importing the package stays light.
from . import assembler, decomp, exporter_gltf, ground, lookup, mass, vlm

__all__ = [
    "schema", "assembler", "decomp", "exporter_gltf", "ground", "lookup",
    "mass", "vlm",
]
