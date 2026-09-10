_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## ⬆️ EARLIER (2026-06-30 pm): Option D BUILT+WIRED, Option A (fusion) STARTED
- **Best-observed object across ALL 8 scenes = office_3 chair#25** (hull 0.754, ang 120.6°).
  Ranked from `out/gate_distribution.json`. **NOTHING exceeds 0.90** (room-scan ceiling;
  only 1 object >0.70; median 0.298).
- **Option D (geometric watertight repair) DONE.** `src/scene/geometric_repair.py`:
  `declutter` (drop TSDF fragments) → quadric-decimate → **pymeshfix** → watertight
  2-manifold collider + colour transfer. `watertight_collider(mesh, target_tris=80000)`.
  On chair#25: watertight=TRUE, 1 comp, fidelity 2.0mm (matches Poisson) BUT actually
  sealed (Poisson isn't — its crop reopens it). 40k too coarse for thin chair; 80k = sweet
  spot; ~76s. CGAL alpha-wrap unavailable (no py binding); pymeshfix is the stand-in.
  - **WIRED:** assembler `strategy=="tsdf"` → `_tsdf_watertight_finalize` → watertight_collider
    (Poisson fallback). `config/pipeline.yaml` `keep_completeness=0.85` all tiers.
  - **END-TO-END demo** (`scratchpad/d_pipeline_demo.py`, forces chair#25 to tsdf):
    `chair_00 mass=10.9kg hulls=16`, CoACD logged **Mesh Manifoldness: true** (the payoff).
    Scene `out/scene_d_demo`. 4 tests; **full suite 132 pass.**
  - **CAVEAT:** keep bar ANDs angular (150-160°, max real 123°) + 0.85>0.754 ceiling → never
    auto-fires on Replica; correct for future walk-around footage. User accepted.
  - **Browser comparison** `out/scene_d_repair/` (raw|pymeshfix|alpha|poisson side by side);
    serve `scripts/serve.py --scene out/scene_d_repair`. Memory: `geometric-repair-option-d`.
- **Option A = FUSION — BUILT + WIRED + VALIDATED.** `src/reconstruction/fusion.py`:
  `grid_from_vbg` (sparse VBG → dense T_real,W), `mesh_to_grid_sdf` (completion mesh →
  signed-dist on same grid via o3d RaycastingScene), `fuse_fields` (`observed?real:comp`,
  EDT blend over a 3-voxel seam), `grid_to_mesh` (skimage marching_cubes → world),
  `fuse_completion` (end-to-end). 5 tests; **full suite 137 pass.**
  - **Validated on chair#25:** fused mesh hugs OBSERVED points to **0.0mm** (Poisson alone
    1.9mm) → real geometry preserved exactly, only the unseen back grafted. ~24s, no GPU.
  - **Canonical combo proven:** real TSDF + **PatchComplete** completion → fusion → D-seal
    → watertight 1-component collider. PatchComplete pred reused from
    `PatchComplete/output_ours/.../chair25/input_0_pred.npz`, placed in world by inverting
    the `chair_to_patchcomplete.py` normalisation. Browser `out/scene_a_patchcomplete`
    (real|patchcomplete|fused|fused+D); also `out/scene_a_fusion` (real|poisson|fused).
    Scripts: `scratchpad/a_fusion_{probe,validate,patchcomplete}.py`.
  - **WIRED:** `tsdf.fuse(return_grids=True)` → `(meshes, {tid:VBG})`. `ObjectInput` gains
    `vbg`+`voxel_size`; `run_assemble.py` passes them for fusable objects. Assembler
    `_fuse_and_seal`: a "completion" object with a VBG KEEPS observed geometry + grafts the
    engine's completion only where unobserved, then D-seals (all WORLD coords, recentred at
    end). Backward-compatible (no VBG → old behaviour). This fires on the COMPLETION band,
    which IS reachable on real Replica (unlike D's keep band).
  - **KEY PROPERTY:** fused mesh inherits the real shell's openness (non-watertight) BY
    DESIGN — fusion preserves real geometry; D seals it. So the pipeline is fusion→D.
  - **PatchComplete-in-engine DONE:** `src/reconstruction/patchcomplete_completion.py`
    loads multi_res in-process (reproduces the CLI prediction BIT-FOR-BIT) →
    `LocalGpuEngine._run_completion` `patchcomplete` branch returns the mesh directly →
    `make_engine()` default completion_model = **patchcomplete**. Validated end-to-end:
    `engine.complete`→6970 tris/10.7s, assemble→fusion→D-seal→CoACD→`out/scene_a_engine`
    (chair_00, 16 hulls, 17.5kg). GOTCHA: model reads its codebook from the RELATIVE
    `priors/` dir → construct with cwd=PatchComplete repo (module handles it).
  - **NOTE:** full `run_assemble` on office_3 is SLOW in the GATE phase (per-object cloud
    accumulation; tier 4 @2mm timed out at 560s, tier 2 @4mm ~3-4min — PRE-EXISTING, not a
    fusion cost). The completion→fusion→seal stage itself is ~1-2 min/object.
