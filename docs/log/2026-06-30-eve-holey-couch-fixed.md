_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## ⬆️ EARLIER (2026-06-30 eve): holey-couch FIXED; scene served
- **office_3 couch rendered holey/broken; root-caused + FIXED.** Cause: the couch's
  real TSDF is very noisy (6969 components vs chair's 254), it fills the fusion grid to
  the edge so marching cubes left OPEN boundaries (holes) pymeshfix couldn't seal, and
  PatchComplete's couch completion is garbage (1.83m tall, L-sectional is OOD). FIX in
  `fusion.fuse_completion`: `pad=4` (empty border → marching cubes closes the surface) +
  `keep_largest=True` (`largest_component`, drops noise blobs). Rebuilt office_3 →
  `out/scene_office_3_full` (couch/table/chair), VISUALLY VERIFIED via offscreen render
  (`scratchpad/render_check.py`, Open3D EGL): couch now a SOLID recognizable L-couch (no
  holes), chair still crisp. Seat stays a bit rough = honest noisy-scan, not a bug;
  global field-smoothing was tried but hung repeatedly (abandoned). Memory `fusion-option-a`.
- **SERVED:** `scripts/serve.py --scene out/scene_office_3_full` on :8000.
- **Render-verify trick:** GLB isn't o3d-readable → assembler dumps PLY with
  `VID2SIM_DUMP_PLY=1`; render one PLY/process (~20s EGL init).
- **⚠️ 1 PRE-EXISTING TEST RED (not mine):** `tests/perception/test_crop_stage.py` from a
  collaborator's TripoSG crop-staging commit (5760c16) — background-whiten mismatch, their
  WIP, generative path (inactive). My fusion+scene tests all green.
