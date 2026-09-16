# Engineering log (dated entries moved out of STATUS.md)

Read these only when you need the reasoning behind a decision. The current state lives in the root `STATUS.md`; measured numbers live in `BENCHMARK.md`; the contract lives in `spec/scene.schema.json`.

All entries predate the 2026-07-13 rename from vid2sim-v2 to Soba and were moved verbatim on 2026-09-04: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Newest first.

- [2026-09-17](2026-09-17-demo-input-and-pins-prep.md) — Prep while the pod runs: `deploy/runpod/sync_bundles.md` (`_v2` orbit bundles 4060 → pod, BUNDLE_DIR run), `deploy/runpod/recover_pins.sh` (one-shot benchmark pin recovery, paste target in docs/model-pins.md)
- [2026-09-16](2026-09-16-gpu-validation-run2.md) — GPU validation run 2: TripoSG generative band on the 4090 through the job API (10 objects, 760 s), first model pin (TripoSG commit), HF-stack pins for torch < 2.5
- [2026-09-16](2026-09-16-gpu-validation-run1.md) — GPU validation run 1 (RunPod 4090): Open3D CUDA PASS, job API real mode + Redis worker done in ~225 s, coacd missing on pod (fixed), 2 pytest failures under review
- [2026-09-15](2026-09-15-gpu-validation-prep.md) — First GPU run of Waves 0–3 prepared: bootstrap installs every extra, `deploy/runpod/gpu_validate.sh` (S0–S9, recorded outcomes), `publish-images.yml` (GHCR), `docs/gpu-validation.md` guide
- [2026-09-14](2026-09-14-deployment-runbook.md) — Deployment runbook: `docs/runbook.md` (images + tag scheme, env matrix, compose / two-host / GPU / RunPod serverless deploy, Open3D CUDA + model-pin + CI gates, smoke, cost controls, alerts, backup, rollback, incidents, release flow); compose `worker-gpu` jobs-dir mount fixed; what was executed here vs needs a GPU / pod / key
- [2026-09-13](2026-09-13-load-testing.md) — Load testing: k6 scenarios (upload burst, polling, scene fetch, SSE, mixed) + stdlib SSE probe against compose, `make loadtest`, open vs keyed (rate-limit bypass) passes, manual CI job; numbers in `docs/loadtest.md` are API-layer with a mock worker, never pipeline/GPU
- [2026-09-13](2026-09-13-api-docs.md) — API docs: hand-authored `spec/openapi.yaml` (OpenAPI 3.1, `$ref` to the scene schema), `GET /api/openapi.json` + Redoc `/api/docs`, route-vs-spec + live-response contract test, `docs/api.md`
- [2026-09-12](2026-09-12-runpod-orchestration.md) — RunPod orchestration: `runpod:` config live, RunPodEngine retry/backoff, `/run`+`/status` polling, breaker, per-job budget, kill switch, https rule; Redis `JobQueue`; `python -m orchestration.worker` + per-job `cost.json`
- [2026-09-12](2026-09-12-observability-b.md) — Observability phase B: `GET /metrics` (Prometheus, `telemetry` extra), request log with job-id correlation, job-state events, `run_metrics.json` ingestion
- [2026-09-12](2026-09-12-security-b.md) — Security phase B: bearer API keys (open until `SOBA_API_KEYS`), per-key/per-IP rate limiting, CORS allow-list, security headers, SSE cap, upload hardening (bomb/size/manifest/depth checks)
- [2026-09-11](2026-09-11-security-audit.md) — Security phase A: secrets + env-var audit, gitleaks/pip-audit/npm audit all clean, root `env.example`, pre-commit (gitleaks + ruff), phase-B threat model
- [2026-09-11](2026-09-11-job-api.md) — Job API: upload a bundle/TUM archive → job id → status → job-scoped viewer; `src/api/` composition, sqlite store, `JobQueue` contract, mock/real worker
- [2026-09-11](2026-09-11-observability-a.md) — Observability phase A: `src/telemetry/`, JSON logs (`SOBA_LOG_JSON=1`), stage timers, gate events, `run_metrics.json` contract
- [2026-09-11](2026-09-11-docker-ci.md) — Docker images, compose, CI; Open3D CI probe verdict; ruff baseline; model-pin audit (nothing pinned in repo)
- [2026-09-05](2026-09-05-routing-ablation-benchmark-s4.md) — Routing ablation, gated tier 2 vs forced tsdf/completion/generative on 7 rooms; BENCHMARK §4 written; camera-ready at 4 pp
- [2026-07-06](2026-07-06-room-accuracy-benchmark-s3.md) — Tier 1 + tier 2 room accuracy on all 8 Replica rooms; BENCHMARK §3 written
- [2026-07-05](2026-07-05-part3-custom-trajectories-fill-fraction.md) — Custom `_v2` trajectories for 8 rooms; fill_fraction physics fix; full Replica GT on disk
- [2026-07-05](2026-07-05-part2-gates-90-035-mast3r-benchmark.md) — Gate lowered to 90°/0.35; ORB-SLAM3 dropped; MASt3R benchmarked on TUM; BENCHMARK.md started
- [2026-07-05](2026-07-05-part1-tiers-rooms-1-4-gen-cache.md) — Tiers 1+2 across rooms 1–4; generation cache; de-overlap; drop-garbage limits
- [2026-07-04](2026-07-04-tier1-mast3r-icp-vlm-drop-garbage.md) — Tier 1 wired; MASt3R, ICP align and Claude physics real; Hunyuan paint; drop-garbage policy
- [2026-07-02](2026-07-02-part6-viewer-framing-headless-verify.md) — Viewer frames the whole room; headless verification harness
- [2026-07-02](2026-07-02-part5-masses-crop-gate-cache.md) — Generated masses fixed; quality-aware crop frame; gate cache (40× warm)
- [2026-07-02](2026-07-02-part4-generation-defenses-gate.md) — Hunyuan artifact defenses (mat cut, fragment drop, rejection); gate re-tuned
- [2026-07-02](2026-07-02-part3-runpod-hunyuan-live.md) — RunPod pod live; Hunyuan3D generative band real; SplitEngine
- [2026-07-02](2026-07-02-part2-dense-rebuild-yolo-sam2.md) — Dense office_3 rebuild audited; YOLO detection + real SAM2 run on TUM
- [2026-07-02](2026-07-02-part1-sanity-check.md) — Full-project sanity check; red test fixed; WIP reviewed
- [2026-07-01](2026-07-01-fusion-option-a-d-triposg-local.md) — Options A (fusion) + D (pymeshfix) shipped; TripoSG runs locally; full room assembled
- [2026-06-30](2026-06-30-eve-holey-couch-fixed.md) — Holey couch root-caused and fixed (pad + keep_largest); scene served
- [2026-06-30](2026-06-30-pm-option-d-fusion-built.md) — Option D built and wired; Option A fusion built, wired, validated; PatchComplete in-engine
- [2026-06-30](2026-06-30-am-completion-bake-off.md) — Four completion models validated on their own data; PatchComplete wins
- [2026-06-29](2026-06-29-compc-on-gpu.md) — ComPC tested on a real GPU; verdict and caveats
- [2026-06-28](2026-06-28-handoff-snapshot.md) — Handoff snapshot: top priority, completion-model state, GPU facts, operating constraint, how to resume, numbering systems, where everything lives, environment, how to run, pipeline progress table (all as of 2026-06-28)
- [2026-06-28](2026-06-28-three-way-routing-engine.md) — Gate reworked to three-way routing; pluggable completion/generative engine
- [2026-06-28](2026-06-28-phase10-11-server-browser.md) — Phase 10 + 11: Starlette server and Three.js/Rapier browser viewer, headless-verified
- [2026-06-27](2026-06-27-phase9-scene-assembly.md) — Phase 9 scene assembly; CoACD; Poisson watertight repair fixes masses
- [2026-06-27](2026-06-27-gate-recalibration.md) — Gate recalibration: hull completeness replaces bbox; thresholds from real distributions
- [2026-06-27](2026-06-27-all-8-scenes-tsdf-gate.md) — TSDF + gate across all 8 Replica scenes; the ≤123° room-scan coverage ceiling
- [2026-06-27](2026-06-27-phase6-confidence-gate.md) — Phase 6 confidence gate built and run on room_0
- [2026-06-27](2026-06-27-phase5-tsdf.md) — Phase 5 TSDF fusion built and validated; Phase-5 gotchas
- [2026-06-27](2026-06-27-replica-reader-gotchas.md) — ReplicaReader added; Replica gotchas (depth mm, camera→world, Z-up→Y-up)
- [2026-06-28](2026-06-28-handoff-validated-next-steps-gaps.md) — Handoff snapshot: validated vs not, agreed next steps, git state, carried-forward gaps
