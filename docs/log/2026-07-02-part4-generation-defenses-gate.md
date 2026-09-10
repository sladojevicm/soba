_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## ⬆️ EARLIER (2026-07-02, part 4): generation-artifact defenses, user-tuned gate
Iterated with the user LOOKING AT the browser scenes; pod now STOPPED.
- **Gate re-tuned per user preference (image-to-3D FIRST):** complete bar
  RAISED to T2 112/0.48, T3 105/0.45, T4 100/0.42 — completion is reserved
  for truly well-observed objects (only chair#25 qualifies on office_3);
  everything else generates. (An earlier same-day widening was my misread of
  the user's intent — reverted.)
- **Hunyuan artifact defenses in `generative.py`** (each found by LOOKING at
  real output, all test-pinned, 175 pass):
  1. **Display-mat cut** (plane-RANSAC, orientation-free): Hunyuan reads the
     object-on-white crop as a product shot and adds a base mat; the uniform
     class-prior scale then shrinks the object to a miniature on a platform.
     >50% of samples on one plane + rest footprint <50% of it -> cut the mat
     (tables/couches never match). Runs BEFORE scaling.
  2. **Detached-fragment drop by CONNECTIVITY, not size** — a 17%-area armrest
     floating 65 cm away passed a <15% size rule; now ANY component >3% extent
     from the dominant component's SURFACE (raycast distance — vertex distance
     lies on sparse meshes) is dropped; touching parts of any size stay (legs).
  3. **Broken-generation rejection**: >30% detached (_clean_gen), no dominant
     component (_looks_shattered), or implausible class height (_class_dims_ok,
     e.g. a 0.24 m slab "chair") -> the OBJECT IS DROPPED, not shipped.
     VID2SIM_GEN_STRICT=0 / VID2SIM_GEN_CLEAN=0 disable.
- **`run_assemble --tracks`** = fast subset iteration (chairs-only rebuild
  ~13 min at gate-stride 20). Scenes: `out/scene_chairs` (6 single-component
  full-size chairs; 3 broken generations rejected), `out/scene_tables`,
  `out/scene_office_3_hy4` (full room). Serve any with scripts/serve.py.
- **Not-a-bug**: "things on the table tops" = REAL tabletop items faithfully
  generated from the crop (Replica's GT table mask includes them; the TSDF
  has the same bumps). Accepted by the user... pending.
- **Open (ranked)**: generated masses (solid-volume overshoot: 206 kg table —
  solidity recalibration for generated meshes); crop quality for weak views;
  gate caching; TUM real-sensor end-to-end; MASt3R; Phase-12 CLI.
