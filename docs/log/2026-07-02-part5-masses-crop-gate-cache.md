_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## ⬆️ EARLIER (2026-07-02, part 5): quality debts (masses, crop scoring, gate cache)
Three ranked debts from part 4 paid, one commit each; 184 tests pass. CPU-only
(pod stopped) — verified against the on-disk scenes, no regeneration.
- **Generated masses fixed** (`mass.generative_volume`, config `generative_mass`):
  the hull rule (1dde0fa) predated the cleanup's watertight guarantee; a table's
  hull fills the air under the top. Now: ENCLOSED signed-tet volume when sane,
  clamped into a hull-ratio band [0.15, 0.35] (generation style swings enc/hull
  3%–63% on identical furniture; real furniture is a ~constant hull fraction),
  hull only as non-watertight fallback. Generative band ONLY; tsdf/completion
  untouched. Recomputed from on-disk glbs (scene.jsons refreshed in place):
  dining tables 205.8→71.8 kg / 101.7→15.2 kg, full-size chairs 28-55→4.5-8.1 kg,
  couch 82→28.7 kg, small chairs 5.4/19.4→0.8/2.9 kg (genuinely small objects).
- **Crop best-frame now quality-aware** (`crop_stage`): world extent stays the
  primary signal; among the top ~20% band the sharpest / best-exposed frame wins
  (Laplacian variance over the mask × dark-luminance damping). Sharp sliver
  still loses; no-poses mask-area fallback keeps the same two-stage rule.
- **Step-5 gate cache** (`reconstruction/gate_cache.py`): per (bundle, track,
  params) npz under `<bundle>/.gate_cache/` holding cloud+cams+metrics; params
  (tier bars, voxel, stride, motion filter, frame_count) are hashed into the
  filename so any change auto-invalidates. run_assemble gained `--no-gate-cache`
  and `--gate-only`. Proof (small office_3, tier 4, stride 4): cold 212.9 s →
  warm 5.3 s (**40x**), routing printout byte-identical; cache 57 MB / 14 objects.
- **Open (ranked)**: TUM real-sensor end-to-end; MASt3R; Phase-12 CLI.
