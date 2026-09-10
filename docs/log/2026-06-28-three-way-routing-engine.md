_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## What this session did (2026-06-28, THREE-WAY routing + pluggable engine)
Reworked the gate from BINARY (tsdf | generative) to **THREE-WAY** so the
generative GPU work drops in cleanly later, and built the pluggable engine seam.
- **Gate (`confidence.py`) is now three-way** via `route(ang, comp, ...)`: two
  cutoffs on the SAME two metrics. `keep` bar → **"tsdf"** (keep the TSDF mesh
  as-is), `complete` bar → **"completion"** (keep real geometry, FILL the unseen
  parts), below → **"generative"** (regenerate whole from the crop). **Collapses
  to the old binary gate** by setting keep==complete or omitting `keep_*` (the
  toggle the user asked for — config only, no code change). `tier_params`/`gate`/
  `gate_object` carry the keep bars; `FUSABLE=(tsdf,completion)`.
- **Pluggable engine (`reconstruction/generative.py`, NEW)** — one interface for
  the two bands that need invented geometry: `complete(mesh,cloud,crop,class)`
  (mid) and `regenerate(cloud,crop,class)->RegenResult` (bottom). **Decision
  2026-06-28: the mid band uses a LEARNED, geometry-conditioned shape-completion
  model (PoinTr-family, GPU)**, NOT image-gen — so there are now **two GPU models
  / two RunPod endpoints**: completion (`pointr`, fed the partial scan) and
  generation (`triposg`/`hunyuan3d`, fed the crop). **LocalEngine** (default, no
  GPU): complete = Poisson repair (the no-GPU fallback for the mid band);
  regenerate = None (bottom dropped). **RunPodEngine**: transport (POST
  /v2/{endpoint}/runsync, Bearer key) wired; per-band endpoints; the contract
  seams `_build_input`/`_decode_mesh` raise until the API is provided; an absent
  endpoint → that band falls back (completion→Poisson, generative→drop). `make_
  engine()` reads `RUNPOD_API_KEY` + `RUNPOD_GEN_ENDPOINT_ID` (alias
  `RUNPOD_ENDPOINT_ID`) and/or `RUNPOD_COMPLETION_ENDPOINT_ID`. **Awaiting the
  RunPod API.** `pipeline.yaml` tiers 2-4 carry `completion_model: pointr` +
  `generative_model`.
- **Local-GPU backend added (`LocalGpuEngine`)** so the models can run on THIS
  box's GPU with RunPod left blank. `make_engine()` priority: RunPod (if endpoints
  set) → **LocalGpuEngine (if a CUDA device is visible, toggle `VID2SIM_LOCAL_GPU`)**
  → LocalEngine. `complete()` = the shape-completion model, `regenerate()` = the
  image-to-3D model, then `coarse_align_to_cloud()` (REAL, CPU/Open3D — scales the
  unit-cube mesh to the observed AABB; replaces the old NotImplemented stub). The
  per-model load+inference (`_run_completion`/`_run_gen`) are honest seams that
  raise until the model packages+weights are installed; a raise is caught →
  graceful fallback (completion→Poisson, generative→drop), so a missing model
  never crashes the pipeline. **BLOCKED on the driver** (see Environment reality):
  CUDA is not visible, so right now `make_engine()` returns LocalEngine and
  office_3 runs in fallback (3 completion via Poisson, 11 dropped) — same scene as
  before. The GPU path goes live with NO code change once `nvidia-smi` works and
  the models are installed.
- **PLAN_FINAL_FINAL updated to v14 (Z-W/Z-X)** — the gate section rewritten from
  binary to three-way (the old "WHY BINARY" objection is *resolved*: each band
  emits ONE coherent mesh, no naive stitch), Step 6 split into completion-model +
  generation-model, overview/TOC/run-order/folder/removed-features/limitations
  all reconciled. Quadruple-checked: grep-swept for lingering "binary"/two-way
  contradictions; remaining "binary" mentions are all historical/explanatory.
- **Assembler is strategy-aware**: `ObjectInput.strategy` drives the mesh
  finalize (completion → `engine.complete`, Poisson fallback; tsdf/generative →
  light repair) and `source.*` (generative → real ICP provenance; tsdf/completion
  → geometry_source "tsdf" + n/a, per the frozen schema's enum + Y1 rule).
  `mass.closed_mesh_volume` measures an already-completed mesh without re-repair.
- **`run_assemble.py` routes three-way**, regenerates the generative band via the
  engine (dropped when Local), fuses tsdf+completion, prints the breakdown.
- **Config (`pipeline.yaml`)**: tiers 2-4 gained `keep_angular_deg`/
  `keep_completeness` (160/0.85, 155/0.82, 150/0.80) — PROVISIONAL, set near
  walk-around quality so room-scan survivors route to completion (the `complete`
  bar is unchanged: the recalibrated 110/0.45, 100/0.42, 90/0.38).
- **Verified:** 115 tests pass (+13: routing bands, collapse-to-binary toggle,
  engine defaults/seams). On **real office_3** the gate routes **3 → completion**
  (couch 110/0.40, table 94/0.46, chair 121/0.74), **11 → generative (dropped)**,
  **0 → tsdf** (nothing clears the keep bar — correct for room-scan). Synthetic
  completion object assembles to a schema-valid entry (geometry_source "tsdf",
  hulls + mass). The visible result is unchanged (completion via Poisson == the
  repair), but the architecture is now three-way and GPU-ready.
- **What the RunPod handover needs:** (1) endpoint id + API key (env vars);
  (2) the handler `input` JSON shape (fill `_build_input`); (3) the returned mesh
  format — prefer OBJ/PLY, Open3D can't read GLB back (fill `_decode_mesh`);
  (4) Phase-8 ICP for `regenerate`'s `_align_to_cloud` (scale/place the unit-cube
  mesh); (5) crop staging (`_crop_path` / Z-B-crop) so the engine gets images.
