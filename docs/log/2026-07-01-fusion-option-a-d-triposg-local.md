_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## ⬆️ EARLIER (2026-07-01): Options A+D shipped, TripoSG LOCAL, full room
Big session. All on branch `fix/phase3-pose-and-eval` (mine + collaborator commits,
latest `e9c6295`). Memory to read FIRST: `fusion-option-a`, `geometric-repair-option-d`,
`triposg-local-and-verify` (NEW), plus the older completion memories.

**BUILT + SHIPPED this session:**
- **Option D** (`src/scene/geometric_repair.py`): pymeshfix watertight collider for the
  dense band. Wired to the "tsdf" keep band.
- **Option A = FUSION** (`src/reconstruction/fusion.py`): keep real observed geometry,
  graft ONLY the unobserved part. Wired: `tsdf.fuse(return_grids=True)` → assembler
  `_fuse_and_seal` (fusion→D-seal) for the "completion" band. Key params (all validated):
  grid-based `keep_largest_interior` (o3d MESH cluster is pathologically slow — use
  ndimage.label on the GRID), adaptive `denoise_sigma` (noisy objects >150 interior blobs
  get global-smoothed → solid closed body), `pad` (marching-cubes closes the surface),
  per-class `max_fill_dist_m` (`assembler.FILL_DIST_BY_CLASS`: table 0.06 kills the
  under-table blob, couch/default 0.25 keeps it solid), `max_voxels` cap (couch@2mm OOMs).
- **PatchComplete = in-engine completion source** (`src/reconstruction/patchcomplete_completion.py`,
  loaded in-process; `make_engine()` default `completion_model=patchcomplete`).
- **TripoSG image-to-3D RUNS LOCALLY** on the 8 GB RTX 4060 (the generative band now
  REGENERATES poorly-observed objects instead of dropping them). Install at
  `~/projects/vid2sim/TripoSG` (weights present). See `triposg-local-and-verify` memory.
- **Render fixes** (all verified in a REAL browser via headless Chrome, NOT Open3D which
  is culling-blind): frontend `DoubleSide`; `exporter_gltf` decimate-BEFORE-smooth +
  discard watertight-breaking Taubin; `smooth_taubin` weld-only (collaborator); server
  `no-store` headers (browser was caching stale meshes → "it's the same every time").
- **Physics**: frontend objects start FIXED, turn dynamic on click (fixes the load
  EXPLOSION from overlapping class-prior-sized objects). Collaborator: drop-to-ground,
  class-prior sizing, generative mass by hull volume.

**SCENES + SERVERS (live now, may need restart):**
- `out/scene_office_3_full` — MIXED (tier 4): 3 fused (couch/table/chair) + 9 TripoSG. Served :8001.
- `out/scene_generated` — ALL 12 TripoSG (tier 2, `--force-strategy generative`). Served :8002.

**OPEN / NEXT:** (1) Poorly-observed generative objects are BAD (torn/blobby) — DATA limit
(bad crops from a center-of-room scan); options: filter them out, improve crops
(brighten/pick-sharpest), or accept. (2) Gate is SLOW + recomputed every run (uncache) —
cache it. (3) Generative masses still high (blobby volume). (4) Browser default camera
centers on ONE object → should auto-frame the room. (5) 1 red test
`tests/perception/test_crop_stage.py` = collaborator WIP, NOT ours.
