# 2026-09-17 — GPU validation run 4: PatchComplete verified on the pod; TripoSG silently absent

Pod `e516016796a6` (fresh container after an overnight stop; volume kept), `develop@70f1fb5`,
`SOBA_COMPLETION_STRICT=1`, `bundles/office_3` (100 frames), tier 2. Summary verbatim:

| step | result | detail |
|---|---|---|
| S1 | PASS | open3d CUDA tensor OK on CUDA:0 |
| S2 | PASS | 510 passed, 11 skipped, 3 warnings in 84.41s |
| S6 | PASS | job 8997f9d6d2522c50 done; 4 objects; gate tsdf=0/completion=4/generative=10; drops {"engine_declined": 10}; run 288s |

`run_metrics.json` completion: `{'patchcomplete': 4}`; per object couch_00, dining_table_01,
chair_02, dining_table_03, all `method: patchcomplete`, `engine: LocalGpuEngine`,
`fallback: False`.

## What it settles

- **The completion band of tier 2 runs PatchComplete on a GPU through the job API**, under
  strict mode, with no fallback. First run where the scene's completion objects are the
  paper's configuration rather than Poisson (runs 1-3).
- **The pod suite is fully green**: 510 passed, 0 failed (the `anthropic` SDK and the
  MASt3R checkout closed the two environmental failures).
- **MASt3R pins observed**: mast3r `f5209afc…`, dust3r `3cc8c88c…`, checkpoint sha256
  `e28f91b4…`; identical to the date-reconstructed rows (`docs/model-pins.md`).
- The TUM server delivers PatchComplete's 1.8 GB at ~350 KB/s per connection;
  `setup_patchcomplete.sh` now resumes to `.part` and uses `aria2c` when present.

## Finding: the generative band did not run, and nothing said why

All 10 generative-routed objects were dropped as `engine_declined` (run 2: 6 built, 4
declined) and the run took 288 s instead of ~760 s. Cause: the container disk is wiped
when a pod stops, bootstrap ran TripoSG's setup AFTER PatchComplete, and the maintainer
(on the coordinator's advice) interrupted bootstrap during the slow PatchComplete
download and ran only `setup_patchcomplete.sh`; TripoSG's Python deps were never
reinstalled, `LocalGpuEngine.regenerate` caught the import error and returned None. The
record could not tell "model unavailable" from "object rejected", and `run_assemble.py`
would have CACHED it as a rejection on a persistent bundle.

Fixed the same day: `LocalGpuEngine.last_decline` (`rejected: …` / `unavailable: …`,
forwarded by `SplitEngine`); `run_assemble.py` records `generation_unavailable` (not
cached) apart from `engine_declined`; `SOBA_GENERATION_STRICT=1` fails the run;
bootstrap now sets up TripoSG before PatchComplete.

## Next

`bash deploy/runpod/setup_triposg.sh`, then the same validation with both strict flags:
expected ~10 objects (4 PatchComplete + TripoSG's accepted generations), no
`generation_unavailable`.
