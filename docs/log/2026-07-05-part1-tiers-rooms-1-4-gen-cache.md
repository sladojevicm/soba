_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## ⬆️ EARLIER (2026-07-05): tier-1+2 across rooms 1-4, gen cache, de-overlap, drop policy
Ports: room3 t1 :8001 / t2 :8002 / t3(stale) :8003; rooms office_1/2/4 t1
:8011/:8012/:8014, t2 :8021/:8022/:8024. Walkthrough mp4s (what the camera saw):
~/projects/vid2sim/data/replica/previews/. All builds headless-verified.
- Dense bundles now exist for office_1/2/4 (+office_3). Gen cache
  (<bundle>/.gen_cache) + --reroll make per-object regeneration ~3 min and
  rebuilds assembly-only; tier 2 reused tier 1's generations byte-identical.
- Drop-garbage policy live (user-directed): thin-shell + width gates; class
  gates recalibrated for COCO umbrella classes (coffee tables ARE
  "dining table"); alpha-mask crops stop generators re-segmenting (the
  white-tabletop eraser bug); sizing retry before drop; placement de-overlap.
- Known limit demonstrated by office_1 (2 objects) and office_4 (panel/ring
  "chairs"): distant/edge-on glimpses generate confident junk that passes
  ARITHMETIC gates. Agreed next candidates, NOT built: input-side minimum
  revealed-structure bar (drop before generating) and output-side CLIP
  class-resemblance gate; watertight gate parked on user request.
