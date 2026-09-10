_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## What an earlier session did (2026-06-28, Phase 10 + 11 / server + browser — SEE the scene)
Built the **local server** and the **browser viewer**, then verified the whole
chain in a headless Chrome — **office_3's couch, dining table and chair render
in Three.js and step in Rapier physics**, no JS errors. This is the payoff: the
assembled scene is now visible and interactive, fully local, no GPU.
- **Phase 10 — `src/server.py`** (plan §15). Built on **Starlette**, not FastAPI:
  FastAPI itself wasn't installed but Starlette + uvicorn + sse-starlette + httpx
  + httpx-sse were — clearly the pre-staged toolchain (httpx-sse is an SSE *test*
  client). Same routes, fewer deps. Routes: `GET /scene.json` (partial-safe —
  empty-but-valid if no file), `GET /meshes/{id}.glb` → `objects/{id}/mesh.glb`,
  `GET /hulls/{id}_{i}.glb` → `objects/{id}/hulls/{id}_{i}.glb` (hull index split
  off the LAST `_N`), `GET /events` SSE, `/` + static `/app.js` + `/vendor/...`.
  Path-traversal guarded (id regex; 400/404 on junk). `create_app(scene_dir)`
  factory + `python -m server [--scene DIR] [--port N]`. Scene dir via
  `VID2SIM_SCENE_DIR` env or `--scene` (default `out/scene_office_3`).
  - **SSE is a REPLAY, not a live feed:** the scene is already fully assembled on
    disk (Phase 9 ran offline), so `/events` emits one `object_added` per object
    in the current scene.json (0.4 s apart), then heartbeats. It drives the
    browser through the real progressive-load + re-GET path (Z-C); it is not a
    live stream from a running assembler (there's nothing assembling live here).
- **Phase 11 — `frontend/`** (plan §16): `index.html` (import map, no bundler) +
  `app.js` + **vendored** `vendor/three` (0.160 ESM + GLTFLoader/OrbitControls/
  BufferGeometryUtils) and `vendor/rapier/rapier.es.js` (rapier3d-**compat** 0.13,
  WASM inlined as base64 → no separate fetch, no build step). All the §16
  load-bearing fixes are implemented and present in the code:
  - **mass on the rigid-body DESC before `createRigidBody`** (`setAdditionalMass`)
    — the P1/D5 fix (original project's computed-but-never-applied-mass bug).
  - **hull colliders = CoACD parts with density 0** so Rapier never re-derives
    mass; Tier-1 path also handled (`collider.shape:"box"` → `cuboid(hx,hy,hz)`).
  - **gravity + ground.y read FROM scene.json** (Z-J / K4); ground cuboid uses
    HALF-extents with centre.y = ground.y − 0.1 (Z6); up_axis asserted.
  - **re-GET /scene.json on each `object_added`**, look up by id (Z-C).
  - camera from `camera_pose` when present (W5); OrbitControls recenters on the
    object centroid so the start view always frames the scene.
  - interactions: click-select (orange emissive), drag-push (velocity to cursor),
    spacebar rubber ball (10 s TTL). Hull verts baked through `matrixWorld` so the
    collider matches the render mesh; mesh+hulls share the assembler's recentred
    object frame (verified: assembler recenters the mesh BEFORE decomposing).
- **Verification (honest):** 8 new server tests (route remaps, byte-equality vs
  on-disk, 404/400, traversal, partial-safe empty scene, **real-server SSE replay
  via a threaded uvicorn** — TestClient/ASGI both deadlock on an infinite SSE
  stream, so the test runs a real port). **102 tests pass** (was 94). Then drove
  the live page with **headless Chrome (Puppeteer + swiftshader, software WebGL)**:
  all 3 objects load, **zero page errors**, WebGL context active, physics stepping
  — screenshot shows the recognizable couch/table/chair on a shadowed floor. The
  software-WebGL "context lost→restored" warning is a swiftshader headless
  artefact, not a code bug. The meshes are the partial-view, non-watertight TSDF
  shells (open backs), exactly as upstream caveats say — they render fine.
- **Sparse-scene constraint holds:** office_3 shows **3 of 14** objects; the
  generative ones are omitted until a GPU exists. The HUD says so on screen.
- **Run:** `PYTHONPATH=src ~/projects/vid2sim/venv/bin/python -m server` then open
  `http://127.0.0.1:8000/`. Deps already in the venv; recorded as the `serve`
  extra in `pyproject.toml`.
