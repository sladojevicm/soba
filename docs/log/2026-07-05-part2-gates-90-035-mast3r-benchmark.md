_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## ⬆️ EARLIER (2026-07-05, part 2): gates 90/0.35, ORB-SLAM3 dropped, MASt3R benchmarked, BENCHMARK.md
238 tests pass. Working tree carries in-progress agent work (eval/renderer/frontend) — see below.
- **Routing thresholds LOWERED to 90°/0.35 (all tiers)** — user: real geometry
  first, image-to-3D last resort. test_confidence updated. Tier-2 rebuilds with
  the new gate: office_1_t2b (:8031, 2 obj — no routing change, coverage 26–42°),
  office_2_t2b (:8032, 8 obj); office_3/4_t2b NOT built (office_3_t2b dir is a
  KILLED PARTIAL — delete before rebuilding). Old sites untouched (:8001-:8024).
- **ORB-SLAM3 permanently dropped (user decision)**: POSE_METHODS[4]="mast3r",
  OrbSlam3Estimator deleted, config tier-4 pose_method=mast3r, tests locked.
  Justified by measurement (below): MASt3R 7.6 cm on the hardest TUM sequence.
- **BENCHMARK.md (repo root, committed base from user's WSL machine + new GPU
  section)**: TUM pose per tier now COMPLETE — tier-1 odometry (2.15/4.74/26.4 cm,
  reproduced here exactly) vs tiers 2–4 MASt3R (1.85/8.78/7.56 cm; scale
  1.0008–1.075; 24-anchor cap). MASt3R 3.5× better on fast motion, loses only on
  oscillating fr1/xyz (interpolation between anchors — RPE column shows it).
  Runner: scripts/bench_tum_pose.py + src/reconstruction/traj_eval.py (tested).
  YCB/ABO physics = lookup backend measured (WSL); **Claude column still missing**.
- **Uncommitted agent work in tree** (from stopped agents, all tests green):
  evaluate_scene.py + replica_eval_class_map.yaml + eval hook in run_assemble
  (--no-eval; quiet-skips w/o GT), frontend GT panel, server /eval.json route +
  empty-scene schema fix pending, render_replica.py (custom trajectories; user
  wants --frames default cut to 200), fetch_replica_gt_traj.py (GT trajs in
  data/replica/gt_traj). Replica semantic-mesh download INCOMPLETE (room_0/1/2
  only; offices never arrived). TUM data: f1xyz/f1desk/f2xyz bundles + poses per
  tier cached in data/tum (fr2 raw frames deleted, groundtruth.txt kept).
- Disk ~97% full — biggest reclaim: SDFusion 17 GB (unused; user approval pending).
