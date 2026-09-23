# 2026-09-17 — PatchComplete on the pod, and completion fallbacks made visible

**Finding.** PatchComplete is the completion band at tiers 2, 3 and 4 (`config/pipeline.yaml`
`completion_model: patchcomplete`; `generative.make_engine` default on a GPU host), i.e. in
BENCHMARK.md §3 tier 2 and §4 "forced completion", not only tiers 3-4. When its checkout or
weights are missing, `LocalGpuEngine.complete` logs a warning, returns None, and
`assembler._fuse_and_seal` repairs with Poisson; the job still reports `done`. Both
2026-09-16 pod runs ran that way: their "completion" objects were Poisson, and nothing but
a log line recorded it.

**Changes.**
- `deploy/runpod/setup_patchcomplete.sh` (+ `SETUP_PATCHCOMPLETE=1` in bootstrap): clones
  `yuchenrao/PatchComplete` (the `priors/` codebook ships in the repo), downloads the
  authors' `trained_models.zip` (1.9 GB, TUM server), places `multi_res.pt` and the three
  `patch_learning_res_*.pt` under `trained_models/`, writes `SHA256SUMS`, smoke-loads the
  model. Not executed here (no torch); the pod run is the test.
- **Fallback visibility.** `telemetry.RunMetrics.record_completion` / `telemetry.completion`
  → `run_metrics.json` gains `completion: {counts, per_object}` (method `patchcomplete` |
  `pointr` | `compc` | `poisson_fallback` (engine declined / no weights) | `poisson_local`
  (no GPU engine), with `engine`, `reason`, `fallback`); `soba_completion_total{method}` on
  `/metrics`; `GET /api/jobs/{id}` gains `run: {status, gate, completion, drops}` read from
  the job's `run_metrics.json` (`spec/openapi.yaml` Job.run). The scene contract is
  untouched: `SceneObject.source` has `additionalProperties: false` (invariant 1).
- `SOBA_COMPLETION_STRICT=1`: any completion fallback raises in `_assemble_object`, so a
  validation run fails loudly instead of producing a degraded tier-2 scene.
- `assembler._record_completion` decides the method from the engine and its result;
  `LocalEngine` (no GPU) is labelled `poisson_local` by design.

**Pins.** The PatchComplete weights are a permanent, documented gap (`docs/model-pins.md`,
`STATUS.md` known limitations): code commit reconstructable by date, weights
provenance-unverifiable.

**Tests.** `tests/telemetry/test_completion.py`, `tests/api/test_completion_visibility.py`
(fixture gains a `completion` section; ingestion and the job record checked). The assembler
path itself needs open3d and runs in CI, not on this box.

**Next (pod).** `git pull`, `SETUP_PATCHCOMPLETE=1 SETUP_TRIPOSG=1 bootstrap`, export the
two homes and `SOBA_COMPLETION_STRICT=1`, rerun `gpu_validate.sh` on `bundles/office_3`:
the summary's S6 must show `completion` counts with `patchcomplete`, no `poisson_*`.
