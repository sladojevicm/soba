"""Physics lookup tables — Step 8 fallback + shared constants (Phase 9).

Single reader for the class-keyed tables in config/pipeline.yaml: material
densities, per-class solidity factors, the physics fallback table (material +
friction + restitution + is_rigid), the class size gates, and the ground-floor
material default. All tables are keyed on the ORIGINAL COCO class string
("dining table", "sports ball") — NOT the slug (fix R2/D1) — with a "default"/
"unknown" fallback so an unmapped class never KeyErrors.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG = _REPO_ROOT / "config" / "pipeline.yaml"


@lru_cache(maxsize=4)
def load_config(path: str = str(_DEFAULT_CONFIG)) -> dict:
    import yaml

    with open(path) as fh:
        return yaml.safe_load(fh)


def density(material: str, config_path: str = str(_DEFAULT_CONFIG)) -> float:
    d = load_config(config_path)["density"]
    return float(d.get(material, d["unknown"]))


def solidity(coco_class: str, config_path: str = str(_DEFAULT_CONFIG)) -> float:
    s = load_config(config_path)["solidity"]
    return float(s.get(coco_class, s["default"]))


def physics_lookup(coco_class: str, config_path: str = str(_DEFAULT_CONFIG)) -> dict:
    """Fallback physics for a class: {material, friction, restitution, is_rigid}.

    Used for ALL of Tier 1 and whenever the Claude (VLM) call is skipped/fails.
    """
    table = load_config(config_path)["physics_lookup"]
    return dict(table.get(coco_class, table["default"]))


def ground_material(config_path: str = str(_DEFAULT_CONFIG)) -> dict:
    """Fixed hard-floor material default (fix W1): {friction, restitution}."""
    return dict(load_config(config_path)["ground"]["material"])


def ground_offset(config_path: str = str(_DEFAULT_CONFIG)) -> float:
    return float(load_config(config_path)["ground"].get("prov_offset_m", 0.02))
