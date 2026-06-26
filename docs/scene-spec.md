# `scene.json` — the scene contract (v2.0)

The frozen `scene.json` contract (Contract 3 of `PLAN_FINAL_FINAL`) is the single
typed source of truth that every stage produces or consumes: the pipeline emits
it, the browser viewer (Three.js + Rapier) consumes it. It is the one
cross-context boundary that stays stable, and per Build Order Phase 1 it is
locked first because everything downstream depends on it.

- Schema: [`spec/scene.schema.json`](../spec/scene.schema.json) (JSON Schema, draft 2020-12)
- Example / fixture: [`spec/scene.example.json`](../spec/scene.example.json)
- Validator: [`src/scene/schema.py`](../src/scene/schema.py)

## Schema semantics (v2.0)

| Field | Meaning |
|---|---|
| `version` | Frozen at `"2.0"`. Any breaking change is a new major version. |
| `world.gravity` | 3-vector, m/s². The browser reads this (does not hardcode it, fix Z-J). |
| `world.up_axis` | `"y"` (asserted against the renderer's up). |
| `world.unit` | Always `"meters"`. |
| `ground.type` | `"plane"`. |
| `ground.normal` | Unit vector, world frame. |
| `ground.y` | **Computed** floor height (Step 10 RANSAC / IMU), not a constant (fix W3). |
| `ground.material` | `{friction, restitution}` — fixed hard-floor default (fix W1). |
| `objects[]` | Up to **12** scene objects (fix Z8). |
| `objects[].id` | `^[a-z_]+_[0-9]{2}$` — `slug(class)_oid`, a compact 00..11 index, **not** the raw track_id (fix T1/D1). |
| `objects[].class` | Exact COCO label (may contain spaces, e.g. `dining table`). |
| `objects[].mesh` | Relative path to a glTF under the scene directory. |
| `objects[].transform` | World-space `{translation, rotation_quat (xyzw), scale}`. `scale` is always `1.0` (real scale baked into the mesh vertices). |
| `objects[].collider` | `"hulls"` (Tiers 2-4: CoACD convex hulls + `hull_paths`) or `"box"` (Tier 1: AABB `half_extents`), fix Z-O/Z-F. |
| `objects[].physics` | `{mass_kg>0, friction∈[0,2], restitution∈[0,1], is_rigid}`. Mass is the computed `volume×density×solidity`. |
| `objects[].material_class` | Material label (renamed from per-object `physics.material`, fix X5). |
| `objects[].source` | Provenance: `geometry_source` (`tsdf`\|`generative`), `alignment_method` (`fpfh_icp`\|`coarse_aligned`\|`n/a`), `scale_method` (`per_axis_median`\|`class_prior`\|`n/a`), `physics_origin` (`vlm`\|`lookup`), optional `vlm_reasoning`. |
| `camera_pose` | Initial viewer pose (consumed by the browser, fix W5). |

**Conditional rules the validator enforces**

- A `tsdf` object skips Step 7 ICP, so it **must** report `alignment_method:"n/a"` and `scale_method:"n/a"` (fix Y1).
- A `hulls` collider **requires** `convex_decomposition:true` and a non-empty `hull_paths`.
- A `box` collider **requires** `half_extents` and **must not** carry `hull_paths`.

## Validate

```python
from scene import schema

schema.validate(scene_dict)            # raises jsonschema.ValidationError on first bad field
list(schema.iter_errors(scene_dict))   # all errors, for reporting
```

CLI equivalent (the example must always validate against the schema):

```bash
python -m jsonschema -i spec/scene.example.json spec/scene.schema.json
```

## Test

```bash
uv pip install -e .[dev]   # or: pip install -e .[dev]
pytest
```
