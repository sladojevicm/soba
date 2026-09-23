# 2026-09-17 — `_v2` bundles must be regenerated; model pins reconstructed by date

**Bundles.** `deploy/runpod/sync_bundles.md` (PR #18) assumed copying the `_v2` orbit
bundles from "the 4060" over rsync. That machine was a pod whose volume is gone, so the
page now regenerates them: Replica v1 download (17 × ~2 GB release parts), selective
extraction of the 8 rooms' `habitat/mesh_semantic.ply` + `info_semantic.json`,
`render_replica.py match-test` on `office_3` as the gate (the original vMAP bundle is
already on the pod's volume), then `render --frames 200` for the 8 rooms. The planner
has no randomness, so the trajectories are the same function of the same meshes as in
July; identity cannot be proven because the July `trajectory_plan.json` files are gone.

**Pins.** No `.gitmodules`, no tracked checkouts: every model was a depth-1 clone of the
upstream default branch or an HF `main` snapshot on a logged date. `scripts/reconstruct_pins.py`
reconstructs the default-branch commit / HF revision as of those dates (blobless clones +
`HfApi.list_repo_commits`), prints a Markdown table labelled **reconstructed by date**, and
keeps the MASt3R checkpoint as the one **exact** pin (sha256, single release file) and
PatchComplete's weights as **unpinnable**. Run on the WSL box and pasted into
`docs/model-pins.md`. Observation: TripoSG, PatchComplete, SAM2 and MASt3R upstreams have
not moved since the benchmark dates, so their reconstructed commits equal today's tips.
