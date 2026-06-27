"""Ground plane — Step 10 final floor (Phase 9).

Y-up world (our contract): the floor is a horizontal plane at ground_y. With IMU
this equals the provisional ground_y from Step 4 Part A (gravity -> min observed
Y - offset). Without IMU (datasets), the plan fits a RANSAC plane to the lowest
points; here we use a robust low percentile of all observed Y (outlier-safe) as
the floor, which is equivalent for clean synthetic depth. Material is the fixed
hard-floor default (fix W1), independent of any object.
"""

from __future__ import annotations

import numpy as np

from . import lookup

GROUND_NORMAL = [0.0, 1.0, 0.0]


def ground_y(clouds: list[np.ndarray], *, offset_m: float | None = None,
             config_path: str = str(lookup._DEFAULT_CONFIG)) -> float:
    """Floor Y = (robust-low observed Y) - offset, over all object clouds.

    Uses the 1st percentile of pooled Y rather than the strict min so a single
    stray low point can't drop the floor. Matches ground_y_prov when the lowest
    points are clean.
    """
    if offset_m is None:
        offset_m = lookup.ground_offset(config_path)
    ys = np.concatenate([c[:, 1] for c in clouds if len(c)]) if clouds else np.array([])
    if ys.size == 0:
        raise ValueError("no observed points to estimate a ground plane")
    return float(np.percentile(ys, 1.0) - offset_m)


def ground_dict(clouds: list[np.ndarray], *,
                config_path: str = str(lookup._DEFAULT_CONFIG)) -> dict:
    """The scene.json `ground` block."""
    return {
        "type": "plane",
        "normal": list(GROUND_NORMAL),
        "y": ground_y(clouds, config_path=config_path),
        "material": lookup.ground_material(config_path),
    }
