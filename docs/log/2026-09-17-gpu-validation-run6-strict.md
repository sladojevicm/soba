# 2026-09-17 — GPU validation runs 5-6: full tier 2 verified under SOBA_STRICT; orbit bundles regenerated

Same pod (`e516016796a6`, RTX 4090), `bundles/office_3` (100 frames), tier 2. From this
session on the coordinator drives the pod over SSH with a dedicated key (maintainer's
decision); the maintainer still starts/stops the pod and judges the viewer.

## Run 5 (maintainer, `70f1fb5`, non-strict, after `setup_triposg.sh`)
S6 PASS: job 7087e8cb592a84bc, **11 objects** (4 PatchComplete + 7 TripoSG, 3 declined),
795 s, S2 510 passed. First complete tier-2 configuration on a GPU, but on the commit
before the step records, so it could not show whether the completions were used.

## Run 6a (`1a7acb2`, `SOBA_STRICT=1`) — FAILED, correctly
`generation unavailable for track 94 (unavailable: RuntimeError: no crop staged for this
object)`. Strict mode exposed a flaw in PR #22: every exception from `_run_gen` was
classified `unavailable`, including a missing crop (crop staging found no usable view),
which is a verdict on the object. Fixed in PR #24: `rejected: no crop staged`, recorded
as `engine_declined`, cacheable, not strict.

## Run 6b (`d310414` = PR #24, `SOBA_STRICT=1`) — PASS, no fallback of any kind
| step | result | detail |
|---|---|---|
| S2 | PASS | 530 passed, 11 skipped |
| S6 | PASS | job 0d26a00e4ff86776 done; 10 objects; gate tsdf=0/completion=4/generative=10; drops {"engine_declined": 4}; run 880s; completion {"patchcomplete": 4}; steps {"collider": {"coacd": 10}, "fusion": {"fused": 4}, "watertight_repair": {"pymeshfix": 4}} |

Fusion per object (all `fused`, `completion_source: engine`, `fallback: false`):
couch_02 449212 fused triangles (completion 3328), dining_table_03 307526 (6086),
chair_04 98044 (6970), dining_table_06 498356 (4616).

**What it settles.** The completion band's meshes reach the final scene: PatchComplete's
output is grafted by `fusion.fuse_completion`, sealed by pymeshfix, and every collider is
a real CoACD decomposition. With strict on, any completion, generation or geometry
fallback would have failed the run. This is the first run where "tier 2" is verified end
to end rather than inferred.

## Orbit (`_v2`) bundles regenerated on the pod (`deploy/runpod/sync_bundles.md`)
- Replica v1: 17 parts, 32 GB, downloaded on the pod; the 8 rooms' `habitat/
  mesh_semantic.ply` + `info_semantic.json` extracted (19-55 MB per mesh). `tar` exits 2
  on the network volume only because it cannot chown; the files are complete (use
  `--no-same-owner`).
- `render_replica.py match-test` on office_3: **depth |diff| median 0.0 mm, p90 0.0,
  100 % valid overlap: PASS** (same as the 2026-07-05 log).
- `render --frames 200` for all 8 rooms: 45 s - 2 min 15 s per room on 16 vCPU, 298-322 MB
  each, 2.5 GB total, 12 min 20 s in all (concurrently with a GPU job).

## Next
`BUNDLE_DIR=bundles/room_2_v2` through the job API under strict (running).
