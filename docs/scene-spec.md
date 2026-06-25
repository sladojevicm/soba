# `scene.json` — the scene contract

The frozen `scene.json` contract (ADR-001 in the original repo) is the single
typed source of truth that every stage produces or consumes: the perception →
reconstruction → scene-assembly pipeline emits it, and the browser viewer,
MuJoCo, and any robotics/sim pipeline consume it. It is the one cross-context
boundary that should stay stable through the rewrite.

- Schema: [`spec/scene.schema.json`](../spec/scene.schema.json) (JSON Schema, draft 2020-12)
- Example / fixture: [`spec/scene.example.json`](../spec/scene.example.json)
- Validator: [`src/scene/schema.py`](../src/scene/schema.py)

## Schema semantics (v1.0)

| Field | Meaning |
|---|---|
| `version` | Frozen at `"1.0"`. Any breaking change is a new major version. |
| `world.gravity` | 3-vector, m/s². |
| `world.up_axis` | `"y"` (default) or `"z"`. |
| `world.unit` | Always `"meters"`. |
| `ground.type` | `"plane"` only in v1.0. |
| `ground.normal` | Unit vector, world frame. |
| `ground.material` | `{friction, restitution}`. |
| `objects[]` | Up to 8 scene objects. |
| `objects[].id` | Unique across the scene. |
| `objects[].class` | Semantic label (e.g. `chair`). Used to look up physics when the VLM falls back. |
| `objects[].mesh` | Relative path to a glTF under the scene directory. |
| `objects[].transform` | World-space `{translation, rotation_quat, scale}`. Quaternion is `xyzw`. |
| `objects[].collider` | `{shape, convex_decomposition?, hull_paths?, radius?, half_extents?}`. `shape` ∈ `mesh`, `sphere`, `box`, `capsule`. |
| `objects[].physics` | `{mass_kg, friction, restitution, is_rigid}`. `restitution ∈ [0, 1]`. |
| `objects[].material_class` | Free-form material label. |
| `objects[].source` | Provenance: `mesh_origin` (`hunyuan3d_2.1` \| `triposg_1.5b` \| `sf3d` \| `identity`), `physics_origin` (`vlm` \| `lookup` \| `manual`), optional `vlm_reasoning`. |
| `camera_pose` | Reference camera pose (default viewer pose). |

## Validate

```python
from scene import schema

schema.validate(scene_dict)        # raises jsonschema.ValidationError on first bad field
list(schema.iter_errors(scene_dict))  # all errors, for reporting
```

CLI equivalent (G0 gate — the example must always validate against the schema):

```bash
python -m jsonschema -i spec/scene.example.json spec/scene.schema.json
```

## Test

```bash
pip install -e .[dev]
pytest
```
