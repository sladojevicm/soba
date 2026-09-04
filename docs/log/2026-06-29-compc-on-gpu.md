_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## ⬆️ EARLIER SESSION (2026-06-29): ComPC tested on a real GPU — verdict
Rented a **RunPod RTX 4090 (24 GB)** and made **ComPC run end-to-end** (the friend's
`deploy/runpod/` kit + a new `setup_compc.sh` that actually builds it: gcc-10,
`--no-build-isolation`, `setuptools<70`, cv2/PATH fix — all committed & pushed).
The pipeline seam is in: `src/reconstruction/compc_completion.py` + a dispatch
branch in `LocalGpuEngine._run_completion`; activate with
`VID2SIM_COMPLETION_MODEL=compc`; runs ONLY for gate "completion"-band objects.
- **RESULT on the real office_3 couch:** ComPC produces a 16k-point **blob** — both
  on the dense 256k input (out-of-regime misuse) AND on a clean **sparse 8k** input
  (in-regime). It is **~40 min/object** (default pce_num=10000 ≈ 1-2 hr).
- **Honest caveat (do not over-conclude):** the in-regime blob was NOT a fair test —
  3 fixes untried: canonical **orientation** (ComPC's Zero123/SDS assumes a canonical
  pose; we fed arbitrary yaw), **unit-normalization** (fed raw 3.6 m metric points),
  and the **L-sectional couch is OOD** for ShapeNet (try a simple chair). Rule these
  out before declaring learned completion dead.
- **Direction that converged:** dense/well-observed → **geometric (Poisson)**, settled.
  Sparse → learned completion is the *intended* band but unproven for us. To fill the
  **unobserved pocket**: for vid2sim's PHYSICS goal use **symmetry-mirror + smooth
  free-space-bounded closure** (ME-PCN's emptiness idea, which our TSDF already has);
  learned hallucination is a visual-detail fallback only. See memory
  `completion-verdict` + `compc-pod-setup`.
- **Pod + cost:** pod bills ~$0.7/hr; **STOP it from the RunPod UI when done** (SSH can't).
  `/workspace` is wiped if the pod is *replaced* (happened once). Repo is private →
  SCP files to the pod, can't `git pull` anonymously.
