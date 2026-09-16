# 2026-09-16 — GPU validation run 3: the dense office_3 build (2000 frames)

Same pod and volume as run 2, `develop@45d38bc` (before the PatchComplete PR #20,
so no learned completion and no completion-method record yet), `bundles/office_3_dense`
built with `STRIDE=1 MAX_FRAMES=2000`, TripoSG set up, no Anthropic key. Summary verbatim:

| step | result | detail |
|---|---|---|
| S1 | PASS | open3d CUDA tensor OK on CUDA:0 |
| S2 | FAIL | 2 failed, 503 passed (the same two environmental failures, fixed in bootstrap afterwards) |
| S3 | PASS | built bundles/office_3_dense (2000 frames) |
| S6 | PASS | job 73ea7f13014a973e done; 9 objects; gate tsdf=0/completion=5/generative=9; drops {"engine_declined": 5}; run 2912s |

Per-object gate values for the five completion-routed objects: angular coverage
93-121°, completeness 0.36-0.76. The `completion` stage totalled **0.035 s for 5
objects**, i.e. the engine declined instantly (no PatchComplete on the volume) and the
assembler repaired with Poisson: this run, like runs 1 and 2, has no learned completion.

## What it settles

- **Frame density does not move the gate.** 2000 frames vs 100 gave the same routing
  (tsdf 0, completion 5 vs 4, generative 9 vs 10): the vMAP room-scan trajectory caps
  angular coverage at about 120° whatever the stride (`docs/log/2026-06-27-all-8-scenes-tsdf-gate.md`).
  Density only densifies the TSDF and multiplies the fusion time: 2912 s vs 760 s.
- Therefore the demo-grade scene needs a different **trajectory**, not more frames: the
  `_v2` orbit bundles (`deploy/runpod/sync_bundles.md`).
- The viewer result looks like run 2 for that reason; masses are lookup-table values
  (no VLM key) with class-prior sizes, hence 0.9 kg chairs and 87 kg tables.

## Not yet validated on a GPU

PatchComplete (PR #20 landed after this run), VLM physics (no key), `_v2` orbit input,
MASt3R on the pod (setup script landed after this run).
