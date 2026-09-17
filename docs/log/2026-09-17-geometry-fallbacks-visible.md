# 2026-09-17 — Geometry fallbacks made visible (fusion, keep-band repair, CoACD, mass volume); TSDF-on-GPU claim retracted

**Why.** A repo-wide sweep of every exception handler around an ML/geometry call
(56 handlers, 15 files) after two silent failures in a row (Poisson instead of
PatchComplete; TripoSG absent recorded as a decline) found the same pattern in four
places on the demo's critical path. The maintainer scoped the fix to those four; the
rest of the sweep (no-GPU engine labels, VLM failure vs no key, ICP crash vs quality
fallback, Hunyuan paint, class-size gate, crop staging, metric scale, the `mock` worker
default, TUM uploads without a detector) is deferred until after the conference.

**What changed.**
- `telemetry.RunMetrics.record_step` / `telemetry.step`: `run_metrics.json` gains
  `steps: {<step>: {counts, per_object}}`, each entry `{track_id, class, method,
  fallback, reason, id, ...}`; `soba_pipeline_step_total{step,method}` on `/metrics`;
  `run.steps` (counts) on `GET /api/jobs/{id}`. `scene.json` untouched (invariant 1).
- **fusion** (`assembler._fuse_and_seal`): the bare `except: pass` is gone. `fused`
  (with `completion_source: engine|poisson`, `fused_triangles`) means the completion's
  geometry is in the final mesh; `plain_finalize_fallback` (with the exception text)
  means it is NOT, whatever `completion.counts` says. This is what run 4 could not show:
  `patchcomplete: 4` proved the model returned four meshes, not that they were used.
- **keep-band repair** (`_tsdf_watertight_finalize`): `pymeshfix` vs `poisson_fallback`
  with the reason, for both the keep band and the sealing of a fused mesh (`context`).
- **CoACD** (`decomp.decompose_info`): `coacd` (parts=n) vs `single_hull_fallback`
  (`coacd not installed` | `coacd raised …` | `coacd returned no usable parts`).
- **mass volume** (`mass.finalize_mesh_info`): `already_watertight` | `poisson_repair` |
  `hull_volume_fallback` (the hull OVERESTIMATES mass) with the reason.
- **One strict flag**: `SOBA_STRICT=1` fails the run on any completion, generation or
  geometry fallback (`telemetry.strict`; per-kind `SOBA_{COMPLETION,GENERATION,GEOMETRY}_STRICT`
  still work). A strict failure is re-raised through the old handlers, never swallowed.
- `gpu_validate.sh` prints completion and step counts in the S6 detail.

**TSDF claim retracted.** STATUS.md, `docs/runbook.md` §6, `docs/gpu-validation.md` and
the run-1 log said or implied "TSDF runs on the GPU". Only the capability was checked:
`tsdf.fuse` defaults to `CPU:0` and `run_assemble.py` passes no device, so fusion has
executed on CPU in every run. The three scripts' S1 message was reworded; the device
itself is NOT changed here (not in scope).

**Tests.** `tests/scene/test_geometry_fallbacks.py` (fusion failure/success/strict, keep
repair failure/success/strict, CoACD missing/crash/ok, volume methods),
`tests/telemetry/test_steps.py`, job-record and `/metrics` assertions. Run here in an
ephemeral `uv run --with "open3d<0.20" --with coacd --with pymeshfix` environment:
103 passed (the one failure is the absent `anthropic` SDK in that env).
