_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## What an earlier session did (2026-06-27, gate recalibration)
Investigated "0/75 → tsdf" and found it was PARTLY a real data property and PARTLY
a **miscalibrated, unreachable threshold**. Two findings, both fixed:
- **The completeness metric was broken.** Old metric = observed_area / **bbox** area,
  which (per the plan's own fix Z-U) tops out ~0.37 on real data — so the 0.55–0.65
  thresholds were UNREACHABLE by any object, ever. Replaced with the shape-fair
  **convex-hull** normaliser (`surface_completeness(denom="hull")`, the Z-U
  improvement path): observed_area / convex-hull area. Measured max rose 0.37→0.75.
- **Thresholds were never calibrated.** Recalibrated from the real 8-scene
  distributions (75 objects; `scripts/analyze_gate.py` dumps
  `out/gate_distribution.json`). New per-tier gates in `config/pipeline.yaml`:
  T2 110°/0.45, T3 100°/0.42, T4 90°/0.38 (~p90→p72 of the distribution).
- **Result (live gate verified == distribution):** T2 routes **1/75** to tsdf,
  T3 **2/75**, T4 **5/75** — the genuinely best-observed objects (office_3 chair#25
  at 121°/0.75 hull is #1; fuses to 0.88×0.67×0.86 m). The partial majority still
  (correctly) goes generative. So the gate now DISCRIMINATES instead of rejecting
  everything via a broken threshold.
- **Honest caveats:** (1) selected objects are STILL partial-view (chair#25 misses
  ~⅓ back) so their TSDF is non-watertight — they lean on Step 7b repair, not magic.
  (2) Thresholds are PROVISIONAL: grounded in percentiles, NOT in ground-truth
  "fully-vs-partially observed" labels (we have none). Proper calibration needs
  walk-around captures. (3) This is a deliberate DEVIATION from the plan
  (150/0.65 bbox → 110/0.45 hull); the plan's numbers were never data-checked.
- 85 tests pass (added hull-metric + denom tests). `pipeline.yaml` documents the change.
