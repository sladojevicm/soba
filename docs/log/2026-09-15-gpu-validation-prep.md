# 2026-09-15 — Preparing the first GPU run of Waves 0–3 (RunPod pod)

**Why.** Every service-layer smoke so far (compose, load test, runbook) used the
mock worker on the WSL box. The job API in real mode, the orchestration worker,
the RunPod client, `run_metrics.json`/`cost.json` from a real build and the
Open3D CUDA wheel question (`docs/model-pins.md`) have never met a GPU.

**What changed.**
- `deploy/runpod/bootstrap.sh` installs `[recon,serve,dev,api,telemetry,worker]`
  (was `[recon,serve,dev]`: no job API, no `/metrics`, no Redis client on a pod —
  the same omission that broke the pipeline image in CI on 2026-09-13).
- `deploy/runpod/gpu_validate.sh`: one in-pod script, steps S0–S9, each recorded
  PASS/FAIL/SKIP in `/workspace/validation/<stamp>/summary.md`; no `set -e`, so a
  failing step is a finding, not an abort. Smoke-size defaults (`office_3`,
  stride 20, 100 frames, tier 2), `WITH_REDIS=1` for the external worker,
  `KEEP=1` to leave the viewer up, dense build via `STRIDE=1 MAX_FRAMES=2000`.
- `.github/workflows/publish-images.yml`: the first push job (GHCR, manual
  dispatch or `v*` tag; optional `-devel` variant). `ci.yml` still only builds.
- `docs/gpu-validation.md`: the maintainer's guide (Path A: PyTorch template +
  bootstrap on `develop`; Path B: the published pipeline image as the pod), what
  each step answers, what to paste back, what each outcome decides.

**Verified here (no GPU).** `bash -n` on the script, the workflow parses, and a
dry run of `gpu_validate.sh` on this box with a synthetic bundle: the control
flow completes and records S1 FAIL (no CUDA), S2 FAIL (missing open3d), S6 FAIL
(`run_assemble.py` cannot import open3d) exactly as expected, with `summary.md`
and the per-step logs written. Nothing GPU-side is claimed.

**Next.** The maintainer runs Path A and pastes `summary.md`, `cuda_check.txt`
and `pins.txt` back; results go into `docs/model-pins.md`, `STATUS.md` and a
dated entry here.
