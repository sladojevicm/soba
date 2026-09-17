# 2026-09-17 — GPU validation run 9: the other seven `_v2` rooms (tier 2, strict, VLM physics)

Pod e516016796a6 (RTX 4090), `develop@7819260`. The seven rooms were submitted one after
another with `scripts/submit_job.py` to the API that run 8 left running (`KEEP=1`), so they
share its environment: `SOBA_STRICT=1`, `SOBA_WORKER_MODE=real`, the TripoSG / PatchComplete /
MASt3R homes, and the Anthropic key (pod shell only). Driver: `/workspace/run_seven.sh`;
artefacts: `/workspace/validation/seven_rooms_20260917T120959Z/` (`summary.txt` holds the
per-object table). room_2 is run 8, repeated here for comparison. No accuracy number is
claimed and no BENCHMARK.md row follows from this (invariants 3, 4: GT poses, reconstructed pins).

## Result: 7/7 jobs done, no fallback anywhere

| room | job | objects | gate tsdf / completion / generative | drops | PatchComplete fused | CoACD | physics | run |
|---|---|---|---|---|---|---|---|---|
| room_0 | 03e69abb2560327d | 7 | 0 / 7 / 9 | engine_declined 9 | 7/7 | 7/7 | 7 vlm | 800 s |
| room_1 | dfbf512e21e9b6ef | 0 | 0 / 0 / 3 | engine_declined 3, nothing_to_assemble | n/a | n/a | n/a | 58 s |
| room_2 (run 8) | 6d29b517d8e12cd7 | 9 | 0 / 8 / 2 | engine_declined 1 | 8/8 | 9/9 | 9 vlm | 710 s |
| office_0 | d570c182b39b85bf | 5 | 0 / 4 / 2 | engine_declined 1 | 4/4 | 5/5 | 5 vlm | 514 s |
| office_1 | 85505fced89b7cb1 | 4 | 0 / 3 / 1 | none | 3/3 | 4/4 | 4 vlm | 287 s |
| office_2 | d66f8c79ca13cc32 | 9 | 0 / 7 / 4 | engine_declined 2 | 7/7 | 9/9 | 9 vlm | 879 s |
| office_3 | 84c6d3ed343c1dc7 | 12 | 0 / 12 / 2 | engine_declined 1, over_cap 1 | 11/11 | 12/12 | 12 vlm | 1055 s |
| office_4 | 908fa8bd20ddcbaf | 10 | 0 / 7 / 4 | engine_declined 1 | 7/7 | 10/10 | 10 vlm | 816 s |

Every completion-band object was completed by PatchComplete, fused (`fusion: fused`), sealed
by pymeshfix and decomposed by CoACD; under `SOBA_STRICT=1` any fallback would have failed the
job. All 56 objects carry `physics_origin: vlm`. room_1 is empty, as at every tier in the
benchmark (walkthrough only). office_3 hit the 12-object cap (one `over_cap`). The keep band
(`tsdf` routing) is still never reached.

## Mass plausibility (the physics demo cares about this)

Mass = mesh volume x density x solidity, so it is only as good as the volume. Two systematic
geometry effects show up across rooms; neither is a physics-source problem:

1. **Generated (TripoSG) meshes are thin shells.** Volume / AABB-volume is 0.00-0.02 for most
   of them, so they come out far too light: office_4 `dining_table_08` **0.54 kg**, office_3
   `chair_09` 3.2 kg, office_4 `chair_01`/`chair_04` 3.7 / 2.8 kg, office_2 `couch_03` 25 kg at
   2.6 m long. A 0.5 kg table will be thrown around by anything that touches it.
2. **Some completed meshes are sealed into solid blocks** (fill 0.24-0.45 where sibling chairs
   are 0.05-0.10): room_2 `chair_07` 34 kg, office_4 `chair_06` 26 kg and `chair_05`, office_3
   `chair_11` 17 kg and `chair_05` 24 kg, office_0 `chair_00` 14 kg. Couches are solid by nature
   but heavy: 86-160 kg, and office_3's 3.6 x 2.7 m sectional is **262 kg**.

Within one room, chairs of the same model differ by up to 10x in mass (office_3: 1.2-24 kg)
because of effect 2. Not investigated further and not fixed here.

## Demo-room reading (numbers only; the visual call is the maintainer's)

* **room_2**: most uniform scene: eight near-identical chairs at 6.2-7.2 kg, one blob outlier
  (`chair_07`), one generated table. 8 of 9 objects measured + completed.
* **room_0**: the only room with **zero generated objects**: 2 couches, 2 armchairs, table,
  2 books, all measured + completed. 9 small generative-routed objects were declined (dropped),
  couches heavy (126 / 160 kg) but nothing absurdly light.
* **office_3**: largest scene (12), but widest mass spread and a 262 kg couch.
* office_4 / office_2: carry the lightest generated objects (0.54 kg table).

The seven non-empty scenes were copied to this WSL box under `out/pod_20260917/<room>/`
(505 files, verified equal to the pod's count); `python scripts/serve.py --scene
out/pod_20260917/room_2` serves scene.json, meshes and hulls locally (HTTP 200 checked), so
the viewer can be shown without the pod.
