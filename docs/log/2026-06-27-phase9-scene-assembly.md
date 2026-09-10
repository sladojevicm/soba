_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## What an earlier session did (2026-06-27, Phase 9 / scene assembly)
Built **Phase 9 (Step 8-10): `scene/{lookup,mass,ground,decomp,exporter_gltf,vlm,
assembler}.py`** + `scripts/run_assemble.py`, validated end-to-end on **office_3**.
Runs the proper Z-D order: gate → TSDF only for survivors → assemble. Output is a
**schema-valid `scene.json`** + per-object `objects/{id}/mesh.glb` and CoACD
`hulls/{id}_{i}.glb`. office_3 @ Tier 4 → 3 tsdf objects (couch/table/chair),
slug ids (`couch_00`…), `source.geometry_source:"tsdf"` ⇒ `alignment/scale:"n/a"`,
lookup physics, ground-snapped placement. **94 tests pass** (9 new).
- **CoACD 1.0.11 installed** (real convex decomposition; single-hull fallback).
- **VLM deferred:** physics from the lookup table (`physics_origin:"lookup"`); the
  live Claude call (`output_config.format`) is an injectable backend, finalised
  later with the claude-api skill + key. No key needed to run.
- **Masses FIXED via Step 7b Poisson watertight repair** (`mass.watertight_repair`):
  open TSDF shells are closed by Poisson reconstruction (density-trimmed, cropped
  to the AABB), volume bounded by the convex hull, hull fallback on failure. Masses
  dropped from absurd (couch 219 kg) to believable: **couch 32.6 kg, table 45 kg,
  chair 3.6 kg** (vs real ~40/20–40/6). ⚠️ Still APPROXIMATE: the repaired mesh
  isn't perfectly watertight (the crop leaves small holes) and Poisson is mildly
  non-deterministic, so kg values can vary run-to-run by some margin. Good enough
  for plausible physics; a fully robust volume (trimesh hole-fill / deterministic
  Poisson, or solidity recalibration against known masses) is future hardening.
- **Sparse-scene caveat holds:** only tsdf objects assembled (3 of 14 in office_3);
  generative objects omitted (no GPU). Volume computed via signed-tetrahedron sum
  (robust; Open3D `get_volume()` rejects convex hulls as non-watertight — gotcha).
