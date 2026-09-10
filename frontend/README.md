# frontend — browser viewer

Three.js (render) + Rapier WASM (physics) viewer for the assembled scene, with
a React overlay UI. Since Phase 2 of the UI rebuild the frontend is a Vite +
React + TypeScript project — but the **built bundle is committed** under
`dist/`, so running the viewer still needs **no Node at all**:

```bash
python scripts/serve.py --scene out/scene_xxx     # serves frontend/dist/
# open http://127.0.0.1:8000/
```

`src/server.py` serves `frontend/dist/` when it exists and falls back to
`frontend/` for stale checkouts. `scripts/serve.py` puts `src/` on the path
itself, so no `PYTHONPATH` is needed.

## Architecture

```
src/
  main.tsx            # React entry
  store.ts            # zustand — the ONLY viewer<->React bridge
  viewer/             # framework-free (no React imports)
    SobaViewer.ts     # class: mount(canvas), dispose(), on(event, cb)
    physics.ts        # world/body/collider construction (correctness contract)
    loaders.ts        # GLB vertices, shadows, label sprites
    framing.ts        # whole-room camera framing math
    types.ts          # scene.json contract + event/debug types
  ui/
    App.tsx           # canvas + overlay panels
    panels/           # Hud, EvalPanel
    lib/utils.ts      # shadcn cn()
```

The viewer emits `ready`, `object-loaded`, `selection-changed`, `stats`
(~4 Hz), and `error`; the store subscribes and React reads the store. React
never touches Three.js/Rapier; the viewer never touches DOM outside its
canvas. See `CLAUDE.md` for the full rules (frozen physics semantics, pinned
`three@0.160.0` / `@dimforge/rapier3d-compat@0.13.1`, design tokens).

## Development

```bash
cd frontend
npm install
npm run dev        # HMR at :5173, proxies /scene.json /meshes /hulls /events
                   # /eval.json to the Python server at 127.0.0.1:8000
npm run build      # type-checks + writes dist/ — COMMIT the result
```

Run the Python server (`python scripts/serve.py --scene ...`) alongside
`npm run dev`; the proxy passes SSE through (verified streaming).

## Controls

- **click** — select an object (orange highlight)
- **drag** — push the selected object (velocity toward the cursor)
- **space** — drop a rubber ball at the camera (10 s TTL)
- **f** — re-frame the camera on ALL loaded objects (35° diagonal, 15% margin)
- mouse — orbit / zoom (OrbitControls)

The camera auto-frames the whole scene: `scene.json` `camera_pose` wins the
initial position (fix W5) with the OrbitControls target aimed at the bbox of
all objects; without a `camera_pose` the view is fully auto-fitted. As objects
stream in over SSE the framing follows — until you first touch the camera.

## Headless verification (`scripts/verify_browser.js`)

Launches the server + headless Chrome and asserts: zero page errors, object
count matches `scene.json`, every Rapier body's live mass equals
`physics.mass_kg`, bodies load fixed and wake on click, no explosion /
fall-through after ~3 s of simulation, `f`-framing works, and writes a
screenshot. Exit 0 iff all checks pass. The page exposes `window.__soba`
(`{objects: [{id, massKg, bodyType, position}], framedAll, screenPos(id)}`) —
live getters into Rapier, used only by the checker. Its shape is frozen.

Puppeteer is not vendored — install it OUTSIDE the repo once and point
`NODE_PATH` at it:

```bash
mkdir -p ~/tmp/pptr && cd ~/tmp/pptr && npm init -y && npm i puppeteer
# without local scene outputs, generate the fixture scene first:
.venv/bin/python scripts/make_test_scene.py
SOBA_PYTHON=.venv/bin/python NODE_PATH=~/tmp/pptr/node_modules \
  node scripts/verify_browser.js --scene out/scene_test --screenshot /tmp/soba.png
```

Options: `--python <bin>` (or `SOBA_PYTHON`), `--settle <ms>` (sim time after
the click, default 3000), `--timeout <ms>`. A swiftshader "context lost"
warning is a known headless artifact and is filtered.

## Correctness notes (plan §16)

- Mass is set on the **rigid-body descriptor before** `createRigidBody` —
  fixes the original project's P1/D5 bug where mass was computed but never
  applied.
- Hull colliders are the CoACD parts with **density 0**, so Rapier never
  re-derives mass from geometry.
- Gravity and `ground.y` are read **from `scene.json`** (Z-J / K4), not
  hardcoded.
- Each `object_added` SSE event triggers a **re-GET of `scene.json`** and a
  lookup by id (Z-C); the event carries only the id.

The scene is **sparse**: only well-observed "tsdf" objects have geometry;
generative objects are deferred until a GPU is connected. A scene showing 3 of
its 14 objects is expected, not a bug.
