# 2026-09-17 — Prep while the pod runs: demo-grade input, benchmark pin recovery

Both 4060-side, neither needs the pod, neither touches `slam.py` or
`deploy/runpod/setup_mast3r.sh` (in flight on the pod).

- **`deploy/runpod/sync_bundles.md`.** The pod's `office_3` is the vMAP room
  scan (10 of 14 objects → generative); the paper's §3 numbers came from the
  `_v2` orbit bundles that exist only on the 4060. Size estimate ~1 GB per room
  (1800 frames at 1200×680; the 2000-frame dense office_3 was 1.2 GB), rsync
  over the pod's exposed SSH with resume, scp and JupyterLab-upload fallbacks,
  verification (`poses.json` must be present: `_v2` = GT poses, invariant 4),
  and the `BUNDLE_DIR=bundles/room_2_v2 … gpu_validate.sh` run. The copy is the
  maintainer's manual step.
- **`deploy/runpod/recover_pins.sh`.** The recovery commands from
  `docs/model-pins.md` as one read-only script printing a paste-ready Markdown
  block (repo commits, checkpoint sha256s, library versions, HF cache
  revisions); absent items print `NOT FOUND`. Dry-run here with
  `ROOT=/nonexistent`: every row `NOT FOUND`, as expected on a box with no
  checkouts. `docs/model-pins.md` now ends with the run command and a paste
  target. This is runbook gate G2.
