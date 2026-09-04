_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## ⬆️ EARLIER (2026-07-02, part 1): full-project sanity check, red test FIXED
- **Test suite: 156 pass, 0 fail** (was 154+1 red). The red
  `tests/perception/test_crop_stage.py` was the TEST's fault, not the code:
  it drew a FULL-SQUARE mask then asserted the tight crop's corners are
  whitened background — but a square's bbox corners are INSIDE the mask.
  Fixed the fixture to a diamond mask (corners really are background,
  JPEG-tolerant ≥240 check) and ADDED a test for the uncommitted `_best_frame`
  rework (with depth+poses it picks the frame with the largest back-projected
  WORLD extent — the revealing view — not the biggest mask; falls back to mask
  area without poses).
- **Uncommitted WIP reviewed (coherent, tests green, still UNCOMMITTED):**
  Hunyuan3D generative band (tier-selected model T1-2 TripoSG / T3-4 Hunyuan3D,
  fix K1; `make_engine(tier=)`), `deploy/runpod/generative_handler.py`
  serverless worker (one endpoint, modes regenerate+complete, contract pinned
  by `test_generative_handler.py`), yaw-ICP + full FPFH registration in
  `coarse_align_to_cloud` (two pose candidates scored by cloud→mesh RMSD),
  crop-stage 3D-extent best-frame, frontend legend removal. Worth committing.
- **NOT DONE (still open from the 2026-06-28 top-priority list): the office_3
  bundle is STILL 100/2000 frames (stride 20)** — `scene_office_3_full` means
  "full room" (all 12 objects), NOT full frames. The stride-1 re-stream +
  per-object data-loss audit (weight threshold, voxel sizes) never ran.
- Pipeline gap list vs the plan re-derived this session — see "Pipeline
  progress" table + "Remaining build phases" below (unchanged conclusions:
  icp_align.py, real SAM2/YOLO, MASt3R/ORB-SLAM3, live VLM call, CLI/tiers,
  integration pass, capture.py all missing; gate caching + generative crop
  quality + mass sanity still open).
