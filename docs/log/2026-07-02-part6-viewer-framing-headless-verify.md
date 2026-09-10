_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## ⬆️ LATEST SESSION (2026-07-02, part 6): viewer debts (framing, headless verification)
Two viewer debts paid, one commit each; JS only (src/ untouched), 184 tests pass.
- **Camera frames the WHOLE ROOM** (`frontend/app.js`): the old running-centroid
  target made users think the scene was missing. Now the view fits the bbox of
  ALL loaded meshes (35°-elevation diagonal, bounding-sphere fit vs. the
  narrower FOV axis, 15% margin). `camera_pose` still wins the INITIAL position
  (W5) — then only the OrbitControls target is aimed at the bbox centre;
  without a camera_pose the view fully auto-fits. Re-frames as objects stream
  in over SSE ONLY until the first user interaction (controls `start` /
  canvas pointerdown); **`f`** re-frames on demand any time.
- **Headless verification harness** (`scripts/verify_browser.js`, run recipe in
  `frontend/README.md`): spawns serve.py on a free port, drives headless Chrome
  (puppeteer via `NODE_PATH` — install it OUTSIDE the repo; swiftshader GL).
  Asserts: zero page errors; object count == scene.json; every LIVE Rapier body
  mass == `physics.mass_kg` (regression guard for the founding desc-mass fix);
  bodies load FIXED and wake on a REAL click (`__vid2sim.screenPos` + mouse);
  no NaN / |p|>50 m after ~3 s of sim; `f`-framing sets `framedAll`; screenshot.
  Exit 0 iff all pass. `window.__vid2sim` debug handle in app.js is additive
  (live getters into Rapier), unused by the viewer itself.
  `NODE_PATH=<pptr>/node_modules node scripts/verify_browser.js --scene out/scene_chairs`
- **Verified**: `out/scene_chairs` (6/6) and `out/scene_office_3_hy4` (11/11)
  — all 8 checks PASS on both; masses match within float32 (e.g. dining_table_02
  71.8074, bottle_08 0.1072); max |p| after click+3 s ≈ 4.1 m; screenshots show
  every object in frame. Only real defect found: `/favicon.ico` 404 console
  error on every load → inline `data:,` favicon in `index.html`.
- **Open (ranked)**: TUM real-sensor end-to-end; MASt3R; Phase-12 CLI.
