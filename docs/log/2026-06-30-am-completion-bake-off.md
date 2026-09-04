_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## ⬆️ EARLIER SESSION (2026-06-30 am): full completion-model bake-off, ALL validated on own data
Tested **4 completion models** for filling partial furniture, each FIRST validated on its
OWN paper/demo data (with a reference output) BEFORE our objects — the discipline the user
insisted on. It caught real bugs and closed the ComPC question. Memory: `completion-verdict`,
`validate-and-run-completion-models`, `completion-tools-survey`, `compc-pod-setup`.
- **VERDICT — PatchComplete wins.** Real-ScanNet-trained, clean + CORRECT-SIZE output, instant
  (110ms), runs LOCALLY in the venv (pure torch, no env build). Couch reconstructed well;
  table → filled solid block not thin legs (32³ too coarse). Validated on its ShapeNet lamp+GT.
- **ComPC: env VALIDATED (not botched), but slow + coarse.** Rebuilt on a fresh RTX 6000 Ada pod;
  validated on ComPC's OWN redwood REAL-SCAN eval data + GT (sym chamfer ~0.055, shape matches).
  The "blob" is CORRECT ComPC behaviour — it outputs dense FILLED SOLIDS (right size, smoothed
  structure). ~20 min/object. This finally closes the user's "did we botch the setup?" question.
- **SDFusion: crisp on synthetic, BLOBS on our real scans** (domain gap). Validation caught a
  feeding bug (SDF must clamp to ±0.2). Runs locally in venv (pytorch3d stubbed).
- **PoinTr/AdaPoinTr: FAIL on real** (synthetic domain gap) — PROVEN: clean on PoinTr's own demo
  sofa, scatter/collapse on our real chair. Not a usage bug (feeding matches official inference).
- **BROWSER COMPARISON:** `out/scene_pointr_compare/` — grid (columns=model, rows=chair/couch/table),
  colors+labels+legend in `frontend/app.js`. Serve: `scripts/serve.py --scene out/scene_pointr_compare`.
  All scratchpad scripts in `scratchpad/` (+ `scratchpad/compc_fair/` artifacts).
- **HIGHEST-VALUE NEXT STEP (conceptual, NOT built): FUSION** — keep REAL observed geometry +
  graft ONLY the missing part. Fuse both as TSDF grids (`if observed→real else→completion`),
  using our `tsdf.py` per-voxel WEIGHTS as the free observed/unobserved mask; marching-cubes.
  Medium difficulty (~1-2 days), infra exists. Biggest win for the regenerate-everything models.
- **⚠️ STOP THE RTX 6000 Ada POD** from the RunPod UI (bills ~$0.5/hr; SSH can't). `/workspace`
  is a network volume → the ComPC env persists for next time.
- **NOTE:** PatchComplete/SDFusion need NO pod (local RTX 4060). Only ComPC needs the pod.
  Image-to-3D (TRELLIS/Hunyuan3D) is the user's chosen LAST RESORT — not yet tried.
