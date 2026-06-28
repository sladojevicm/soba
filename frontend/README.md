# frontend — Step 11 browser viewer (Phase 11)

Three.js (render) + Rapier WASM (physics) viewer for the assembled scene. No
build step / bundler: `index.html` uses an **import map** and the libraries are
**vendored** under `vendor/`, served by the Step-10 server (`src/server.py`).

## Run

```bash
cd ~/projects/vid2sim/vid2sim-v2
PYTHONPATH=src ~/projects/vid2sim/venv/bin/python -m server          # serves out/scene_office_3
# then open http://127.0.0.1:8000/
# point at another scene:  python -m server --scene out/scene_xxx
```

## Controls
- **click** — select an object (orange highlight)
- **drag** — push the selected object (velocity toward the cursor)
- **space** — drop a rubber ball at the camera (10 s TTL)
- mouse — orbit / zoom (OrbitControls)

## Vendored libraries (`vendor/`)
- `three/` — Three.js 0.160 ESM (`three.module.js`) + addons GLTFLoader,
  OrbitControls, BufferGeometryUtils.
- `rapier/rapier.es.js` — `@dimforge/rapier3d-compat` 0.13 (WASM inlined as
  base64, so no separate `.wasm` fetch and no bundler needed).

These are committed deliberately so the viewer works offline; regenerate with
`npm pack three@0.160.0 @dimforge/rapier3d-compat@0.13.1` and copy the files.

## Correctness notes (plan §16)
- Mass is set on the **rigid-body descriptor before** `createRigidBody`
  (`setAdditionalMass`) — fixes the original project's P1/D5 bug where mass was
  computed but never applied.
- Hull colliders are the CoACD parts with **density 0**, so Rapier never
  re-derives mass from geometry.
- Gravity and `ground.y` are read **from `scene.json`** (Z-J / K4), not hardcoded.
- Each `object_added` SSE event triggers a **re-GET of `scene.json`** and a
  lookup by id (Z-C); the event carries only the id.

The scene is **sparse**: only well-observed "tsdf" objects have geometry;
generative objects are deferred until a GPU is connected, so office_3 shows 3 of
its 14 objects. That is expected, not a bug.
