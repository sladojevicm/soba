_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## What is validated vs not (read before trusting anything)
- **Validated on REAL data:** camera trajectory (Step 3, TUM, 3.66 cm ATE) and, now, the
  **per-object observed cloud** (Step 4A) on real Replica frames with GT masks.
- **Logic-tested with synthetic/fake inputs only:** schema, bundle I/O, TUM/Replica parsing
  detail, pose-composition math, and **all of SAM2's orchestration** (fake backend).
- **Validated locally end-to-end (this session):** assembled office_3 served by
  `src/server.py` and **rendered + simulated in a real browser engine** (headless
  Chrome) — 3 objects load, physics steps, no JS errors. Software WebGL, not a GPU.
- **Never run / doesn't exist:** real **SAM2** model; **YOLO** detection; MASt3R /
  ORB-SLAM3 (stubs); the **generative path** (Steps 6–8, RunPod/GPU). The browser
  exists and runs; its physics is functional but not tuned/stress-tested.

## Agreed next steps (for the next instance)
Phases 1–6 + 9 + **10 + 11** are built. The pipeline now runs end-to-end **locally
with no GPU**: assemble → serve → render+simulate in the browser. office_3's
couch/table/chair are visible and interactive.
- **The viewer works now** — `python -m server`, open `http://127.0.0.1:8000/`.
  See "What this session did (Phase 10+11)" above and `frontend/README.md`.
- **Remember the sparse-scene constraint** (top of this file): only tsdf objects
  exist; generative ones are omitted until a GPU is connected. office_3 @ Tier 4
  shows 3 of 14 objects — that's expected, not a bug (the HUD says so on screen).
- **Honest residual caveats / future work:**
  - SSE is a *replay* of a finished on-disk scene, not a live assembler feed. To
    make it live, the assembler (Phase 9) would write objects incrementally and
    the server would watch the dir / receive emits instead of replaying.
  - Physics verified to *run* (objects load, step, no errors) but not tuned —
    starting heights are the observed heights, so objects settle onto the floor
    on first frames; the §16 INERTIA CAVEAT (density-0 + scalar mass can give a
    degenerate inertia tensor → odd spin) was NOT hit in the static settle test
    but isn't stress-tested. If spin looks wrong, switch to
    `setAdditionalMassProperties` (a bbox-box inertia approx).
  - Verified with **software WebGL** (swiftshader, headless). Real GPU browsers
    will look better; the context-lost warning is swiftshader-only.
  - Meshes are partial-view non-watertight TSDF shells (open backs) — render
    fine, but colliders are only as good as those shells.
- **Remaining build phases:** 12 (CLI + tier wiring), 13 (integration), 14
  (ORB-SLAM3, optional), 15 (live OAK capture, needs hardware), plus the deferred
  generative path (Steps 6–8, RunPod/GPU). Optional hardening: deterministic mass
  volume (Step 7b approximate); real SAM2; live Claude physics call (vlm.py + key).
- **A ready test scene exists:** `out/scene_office_3/` (regenerate with
  `scripts/run_assemble.py --bundle .../bundles/office_3 --tier 4 --out ...`).
  Open3D CANNOT read its own .glb back (writes fine for three.js); don't QA in o3d.

## Git state
- Branch **`fix/phase3-pose-and-eval`**. Pushed: Phase 1–3 (`2b56f92`), Phase 4
  (`e122c37`), Phase 5 (`14083c7`), Phase 6 (`956ec7a`), 8-scene tooling
  (`eaf71ab`), gate recalibration (`c396850`), generative-deferral note
  (`fe6d4d9`), Phase 9 assembly (`15408b7`), mass fix (`0f6d7be`), STATUS refresh
  (`fb31168`).
- **This session (Phase 10+11) — to be committed:** `src/server.py`,
  `tests/test_server.py`, `frontend/` (index.html, app.js, README.md, vendored
  `vendor/three` + `vendor/rapier`), `pyproject.toml` (serve/httpx extras), this
  `STATUS.md`. `master` stays clean.

## Carried-forward gaps & gotchas
1. **Detection gap (unchanged):** the pipeline assumes per-frame `objects.json`+masks. TUM
   has none (needs host-side YOLO behind `TUMReader(detector=)`); Replica sidesteps it with
   GT masks. Live OAK on-NPU YOLO is Phase 15 (`capture.py`, not built).
2. ~~Z-T motion filter not wired~~ **DONE** — implemented as the two-pass keep-frame
   walk in `observed_cloud` (`bbox_diagonal`, `motion_keep_indices`,
   `accumulate_object_cloud(motion_filter=, return_keep=)`) and shared into
   `tsdf.fuse(motion_filter=)` via `tsdf.analyze_objects` (the single source of
   truth keyed by frame_id, fix Z-T). Default OFF. Verified: no-op on static
   room_0 (all frames kept, extents unchanged), drops jumped frames on synthetic
   moving objects. `obj_size_m` uses the FULL multi-view diagonal so the camera
   orbit doesn't false-reject static frames (the Z-A/parallax property).
3. **Confidence completeness metric is shape-dependent** (fix Z-U): treat thresholds as
   empirical; angular coverage is the shape-robust signal. Matters at Phase 6.
4. **Crop staging seam (Z-B-crop):** crops staged at `crops/crop_{track_id}.jpg`; Step 10
   must relocate to `objects/{id}/crop.jpg`.
5. Low-risk audit items: `bundle.write_poses` labels frames by enumerate index; TUM
   `associate()` is many-to-one; `schema.load_schema(path)` custom path ignored by the
   cached validator.
