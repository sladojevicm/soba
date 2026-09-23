# 2026-09-17 — GPU validation run 8: `room_2_v2` with Claude VLM physics

Pod e516016796a6 (RTX 4090), `develop@7819260`, tier 2, `SOBA_STRICT=1`,
`BUNDLE_DIR=bundles/room_2_v2`, `ANTHROPIC_API_KEY=set` (pod shell only; never in the repo).
Artefacts: `/workspace/validation/20260917T104631Z/`. Same bundle and commit as run 7
(`2026-09-17-gpu-validation-run6-strict.md`); the only change is the key.

## Result

S6 PASS: job 6d29b517d8e12cd7, 9 objects, gate tsdf=0 / completion=8 / generative=2, drops
{"engine_declined": 1}, 710 s, completion {"patchcomplete": 8}, steps {"fusion": {"fused": 8},
"watertight_repair": {"pymeshfix": 8}, "collider": {"coacd": 9}}. No fallback, no job error.
Geometry and routing identical to run 7.

Physics: **9/9 objects `source.physics_origin: vlm`** (run 7: 9/9 lookup), each with a
`vlm_reasoning` string ("upholstered chair back with legs"). A failed API call would have
fallen back to lookup silently (sweep item 6, deferred), so the per-object origin was
checked in `real_scene.json`, not assumed from the key being set.

| object | run 7 (lookup) | run 8 (vlm) | final mesh volume |
|---|---|---|---|
| 7 chairs | 9.0-10.5 kg | 6.2-7.2 kg, fabric, friction 0.7 | 0.051-0.060 m3 |
| chair_07 | 49.8 kg | **34.1 kg** | **0.294 m3** |
| dining_table_01 | 13.8 kg | 9.9 kg | 0.011 m3 |

## The chair outlier is geometry, not physics

chair_07's watertight mesh encloses 0.294 m3, five times its siblings (extents
0.74 x 1.14 x 0.94 m vs ~0.72 x 1.10 x 0.76 m: similar box, far more of it filled). Mass is
volume x density x solidity, so any physics source inherits the error; the VLM only moved
it from 49.8 to 34.1 kg by choosing fabric. The likely cause is the completion/seal step
closing the under-seat space into a solid block for this one object. Not investigated
further and not fixed here. The dining table is the opposite case: a thin generated shell
(0.011 m3), so a 1.5 m table weighs 9.9 kg.

## Test-suite finding (fixed in this change)

S2 on the pod: 529 passed, **1 failed**:
`tests/scene/test_assembly.py::test_vlm_falls_back_to_lookup_without_backend` got
`['vlm', 'vlm']`. With a real key in the shell, `vlm.infer()` builds the live Claude backend,
so that test, and every `assembler.assemble()` test that passes no backend, made real billed
API calls during pytest. `tests/conftest.py` now has an autouse fixture that removes
`ANTHROPIC_API_KEY` for every test; tests that need one set a fake with `monkeypatch.setenv`
(as `test_vlm_claude.py` already did). Checked on the WSL box in an ephemeral env with a
fake key exported: `tests/scene` 66 passed. Not re-run on the pod.

## Next
Pick the demo room (the other seven `_v2` rooms have not been run); look at chair_07's
completion mesh if room_2 is the demo room.
