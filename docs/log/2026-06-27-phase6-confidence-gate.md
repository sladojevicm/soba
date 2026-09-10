_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## What an earlier session did (2026-06-27, Phase 6 / confidence gate)
Built **`src/reconstruction/confidence.py`** (Step 5) + `scripts/run_gate.py`, ran
on real room_0. Scores the RAW cloud (fix V1): **angular coverage** (largest
pairwise centroid→camera view-angle, shape-independent primary signal) AND
**surface completeness** (ball-pivoting/alpha-shape patch area ÷ oriented-bbox
area, clamped [0,1] — fix V2; empirical knob per fix Z-U). Both must clear the
per-tier bar (read from `config/pipeline.yaml`: Tier2 150°/0.65). Tier 1 = all
generative, no scoring. Output `confidence/{track_id}.json`.
- **room_0 @ Tier 2: all 16 objects → "generative".** This is CORRECT, not a bug:
  the camera's *forward* direction spans 178.8° (rotates to scan the whole room)
  but each *object's* angular coverage maxes at ~86° (couch 78.8°) — a
  center-of-room scan sees object fronts across a limited arc and never orbits
  behind any single object. Partial front-only views → generative to complete the
  unseen back is exactly the designed behavior. **Implication:** room_0's
  Phase-5 TSDF shells would all be bypassed for generative (consistent — they
  weren't watertight); the room_0 path is therefore entirely generative (needs
  RunPod, Phase 7/8). To exercise the *tsdf-accept* branch on REAL data we need
  walk-around footage; unit tests prove that branch fires.
- 12 new tests (`test_confidence.py`). **83 total pass.**
- **Run:** `PYTHONPATH=src ~/projects/vid2sim/venv/bin/python scripts/run_gate.py
  --bundle ../data/replica/bundle_room0 --tier 2`.
