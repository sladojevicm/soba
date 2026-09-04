_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## What an earlier session did (2026-06-27, all-8-scenes TSDF + gate test)
Built bundles for **all 8 Replica vMAP scenes** (streaming, see Replica data above)
and ran TSDF fusion + the Step-5 gate across all of them (`scripts/report_scenes.py`).
**Result: TSDF fuses correctly on every scene (real-world dims), and the gate routes
ALL 75 objects to "generative" (0 tsdf) at Tier 2.** Per-scene best-object TSDF dims:
room_0 couch 2.31×0.92×1.02, room_2 dining-table 2.12×0.64×1.35, office_2 table
1.58×0.50×1.58, office_3 chair 0.88×0.67×0.86 — all sane, all watertight=False.
- **Why 0 tsdf everywhere (honest, important finding):** max per-object angular
  coverage across all 8 scenes is **123.3°** (office_4), below even the loosest
  Tier-4 bar (130°); completeness is also low (~0.04–0.33). vMAP trajectories are
  center-of-room exploration scans — the camera rotates a lot (forward-dir spans
  ~179°) but never ORBITS a single object past ~123°, so nothing clears the
  walk-around gate. This is the DESIGNED behavior (partial views → generative), and
  it means **the tsdf-accept branch is unreachable on this dataset** — to exercise
  it on real data we need genuine walk-around footage, OR the thresholds need
  revisiting for room-scan captures (a real open question for the project).
- **Net:** both modules are now validated on 8 diverse real scenes. The room-scan
  conclusion is consistent, not a one-off room_0 artefact.
