# 2026-09-17 — Prep while the pod runs: VLM-key plumbing, docs reconciled with the pod runs

- **`docs/gpu-validation.md`**: the physics path is now explicit. Both 2026-09-16
  pod runs were `physics: lookup` (no `ANTHROPIC_API_KEY`), which is the fallback,
  not the paper's headline VLM physics (BENCHMARK.md §2). The guide says how to set
  the key on the pod (shell export, or a `chmod 600 /workspace/.env` on the volume;
  never a committed file), and how to confirm which path ran: `summary.md`'s
  `Knobs:` line already prints `ANTHROPIC_API_KEY=set|unset`
  (`deploy/runpod/gpu_validate.sh`), and every object's `source.physics_origin`
  in `scene.json` (`src/scene/assembler.py`) is `vlm` or `lookup`, shown as the
  inspector's "physics" row. No script change was needed. No key value is
  written anywhere.
- **`STATUS.md` "Never built or never run"** now separates what the two pod runs
  proved (job API real mode, Redis worker, TripoSG through the job API, Open3D
  CUDA TSDF, coacd) from what is still unrun on a GPU (MASt3R via the job path,
  serverless, compose `gpu` profile, Hunyuan3D, VLM physics through the job API).
- **`docs/runbook.md` §6**: G1 marked executed 2026-09-16 (bootstrap path, PASS)
  with the CI-message explanation; the image-path check is still open. G2 points
  at `deploy/runpod/recover_pins.sh` (prep A, PR #18) and states the one recorded
  pin is a validation pin, not the benchmark's.

Out of scope, untouched: the RunPod serverless endpoint, phone-video Step 2,
`slam.py`, `deploy/runpod/setup_mast3r.sh`.
