_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## ⬆️ EARLIER (2026-07-02, part 3): RUNPOD POD LIVE — Hunyuan3D generative band REAL
User provided a RunPod pod (RTX 3090 24 GB, `slow_tomato_gull`, SSH
`root@213.192.2.110 -p 40028`; **REMIND USER TO STOP IT when done — bills
hourly**). First-ever live run of the plan's cloud-GPU generative band:
- **Hunyuan3D 2.1 installed on the pod** via `deploy/runpod/setup_hunyuan3d.sh`
  (first real execution; it works — one missing dep `timm` found+added). Weights
  (14 GB) in `/root/.cache` (NOT /workspace → gone if pod is REPLACED; script
  restores in ~5 min). Pod pip needs `PIP_BREAK_SYSTEM_PACKAGES=1` (PEP 668).
  Code shipped by `git archive HEAD | ssh ... tar -x` into /workspace/vid2sim-v2.
- **The serverless handler runs on the pod as a plain HTTP server**
  (`python3 generative_handler.py --rp_serve_api --rp_api_port 8777`, log
  /workspace/handler.log) and the local pipeline drives it through an SSH
  tunnel via the new **`VID2SIM_RUNPOD_URL`** override — the REAL
  RunPodEngine transport (`_build_input`/`_decode_mesh`) validated live:
  watertight chair mesh back on first post-timm attempt. No serverless
  deployment needed for a pod; for real serverless later just set
  RUNPOD_API_KEY + RUNPOD_GEN_ENDPOINT_ID and unset VID2SIM_RUNPOD_URL.
- **`SplitEngine` added** (make_engine composes it: RunPod gen endpoint set,
  no completion endpoint, local CUDA present) → generative band on the pod,
  completion band stays local PatchComplete instead of degrading to Poisson.
- **RunPodEngine contract fix:** regenerate() now DECLINES (None) on a missing
  crop instead of raising — a raise killed a full 35-min tier-4 assembly at the
  first uncroppable object (chair#9). Test pins it. 166 tests pass.
- **Scenes from the dense (2000-frame) bundle, tier 4, gate-stride 10:**
  - `out/scene_office_3_dense` — local: 3 fused (PatchComplete+fusion+seal) +
    9 TripoSG, 12 objects (12-cap), **server smoke-tested: schema-valid, 12/12
    meshes+hulls fetchable**. Generative masses still rough (125 kg table).
  - `out/scene_office_3_hy` — same but generative band = **Hunyuan3D 2.1 on
    the pod** (~40 min wall; 9 generated + 3 fused = 12 objects, 1 uncroppable
    chair declined gracefully). Schema-valid, 12/12 meshes served. Masses far
    saner than TripoSG's (chairs 2-20 kg vs 0.4-39 kg; tables still ~100 kg
    high → solidity recalibration is future work). Serve either scene with
    `scripts/serve.py --scene out/scene_office_3_hy`.
- Gate scores on dense data ≈ identical to 100-frame (chair#25 120.8°/0.758 vs
  121°/0.74) — coverage really is trajectory-bound; `--gate-stride` validated.
