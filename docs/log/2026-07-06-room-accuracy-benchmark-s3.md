_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## ⬆️ LATEST (2026-07-06): TIER-1 + TIER-2 ROOM ACCURACY DONE (all 8 rooms) — BENCHMARK §3 written
241 tests pass. This commit bundles all prior uncommitted work + the room-accuracy runs.
- **Tier 1 AND tier 2 built for all 8 `_v2` rooms**, GT-evaluated (`scripts/evaluate_scene.py`),
  browser-verified (`scripts/verify_browser.js`, all pass), and served. Ports: tier 1 :8001–8008,
  tier 2 :8011–8018 (room_1 is empty in both — walkthrough corridor, only uncroppable books).
- **BENCHMARK.md §3 "Replica room accuracy"** now written (between `<!-- SECTION3 -->` markers):
  one table per room, a row per tier. Regenerate with `scratchpad/write_section3.py` (reads each
  `out/scene_<room>_t<tier>/eval.json`). Pose is EXCLUDED from the score for `_v2` (they consume
  exact GT poses → ATE 0 by construction; passing the ORIGINAL dataset traj gives a bogus ~115 cm,
  so run evaluate_scene with `--gt-traj /nonexistent` to force pose n/a and renormalise the score
  to 0.444·recall + 0.222·precision + 0.333·F@5cm).
- **Result: tier 2 beat tier 1 in every non-empty room.** Mean score 63.0 → 75.2 (+12.2), mean
  F@5cm 0.41 → 0.68. Precision 1.00 everywhere (drop-garbage shipped zero hallucinations, both tiers).
  Driver of the gain: on the `_v2` orbit trajectories most objects route to COMPLETION (real geometry),
  NOT generative — the opposite of the old room-scan bundles. This is the coverage-gated-tier story.
- **Local tier build recipe (4060):** `scratchpad/tier_build.sh <room> <tier> <port>` needs
  `VID2SIM_TRIPOSG_HOME=~/projects/vid2sim/TripoSG VID2SIM_TRIPOSG_FLASH=0` (diso/nvcc not built →
  marching cubes) + `VID2SIM_PATCHCOMPLETE_HOME=~/projects/vid2sim/PatchComplete`. Never run two GPU
  builds at once. `scratchpad/tier{1,2}_all.sh` are the sequential drivers.
- **Disk reclaimed:** deleted SDFusion (17 GB) + PoinTr (1.3 GB) + caches → root 93% → 74% (25 GB free).
- **Open:** tiers 3–4 need the pod (Hunyuan3D, stopped); commit; then paper (ERK) consumes BENCHMARK §3.
