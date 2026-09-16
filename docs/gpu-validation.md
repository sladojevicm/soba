# First GPU validation of the service layer (Waves 0–3) on RunPod

Nothing from the production-hardening waves (job API, real worker, Redis queue,
RunPod client, metrics, images) has run on a GPU yet: every smoke test so far
used the mock worker on the WSL box. This page runs the real thing once on a
RunPod pod and brings the results back into the repo. It is split into **what
is already done in the repo** and **what only you can do** (rent the pod, paste
keys, look at the viewer, paste results back).

Ground rules: nothing here claims a GPU result that was not observed
(CLAUDE.md invariant 7). `deploy/runpod/gpu_validate.sh` records PASS / FAIL /
SKIP per step and writes `summary.md`; a FAIL is a finding, not something to
paper over.

## What this validates, and what it does not

| Step | Wave / component | Question answered |
|---|---|---|
| S1 cuda_check | docker-agent finding (`docs/model-pins.md`) | Does the pip `open3d` wheel have the CUDA tensor backend on a real GPU, or does TSDF silently run on CPU? |
| S2 pytest | everything | Do the 2 gpu-marked tests and the full suite pass with torch and CUDA present? |
| S3 bundle | perception (unchanged) | Replica bundle from HuggingFace via `build_replica_bundles.py` (the pod README's proven path) |
| S5 api_real | job-api (Wave 0) | API + in-process worker in **real** mode start on a GPU host |
| S6 job_real | job-api + observability + orchestration | Upload → `run_assemble.py` on the GPU → done; first real `run_metrics.json`, `cost.json`, `/metrics` gate counters |
| S7 redis_worker | orchestration (Wave 1) | `python -m orchestration.worker` consuming a Redis queue, second job through that path |
| S8 pins | docker-agent finding | Commits / checkpoint hashes of whatever model checkouts the pod volume holds |

Not validated by this page (each needs its own session): the RunPod
**serverless** endpoint and the hardened client against it (needs an endpoint
built from the `-devel` image with weights; `docs/runbook.md` §5.4), the
`gpu` compose profile (RunPod pods cannot run Docker inside), the OAK/phone
capture gate (parked), and any accuracy number (invariants 3 and 4: this is a
plumbing check, not a benchmark).

## Already done in the repo (my part)

- `deploy/runpod/bootstrap.sh` now installs `[recon,serve,dev,api,telemetry,worker]`.
  Before this it installed only `[recon,serve,dev]`, so a pod would have had no
  job API, no `/metrics` and no Redis client — the same omission that broke the
  pipeline image in CI on 2026-09-13.
- `deploy/runpod/gpu_validate.sh`: the in-pod script described above. Knobs
  are env vars; defaults give a **smoke-size** run (Replica `office_3`, stride
  20, 100 frames, tier 2), which is what `run_pipeline.sh` also defaults to.
- `.github/workflows/publish-images.yml`: pushes `soba-api` and `soba-pipeline`
  to GHCR on manual dispatch or a `v*` tag (Path B below). CI still only builds.
- This page, and a pointer at the top of `deploy/runpod/README.md`.

## Your part — Path A (recommended first run: PyTorch template + bootstrap)

No registry, no image publishing. The pod clones `develop` and pip-installs,
exactly the way every earlier pod run worked. The Open3D CUDA question is
answered identically, because bootstrap and the image share the pip line.

### A1. Rent the pod (RunPod console)

1. **Deploy → Pods → GPU Cloud.** GPU: **RTX 4090 (24 GB)** recommended
   (`deploy/runpod/README.md`); an RTX 3090 / A5000 also works. On-demand, not
   spot, for this interactive session.
2. **Template:** an official **PyTorch 2.x / CUDA 12.x** template (torch and CUDA
   preinstalled; bootstrap installs into that interpreter and never touches torch).
3. **Volume:** a persistent volume mounted at **`/workspace`**, 100 GB. It keeps
   the repo, the Replica bundle and any model checkouts across pod restarts. If
   the old pod's volume still exists (`STATUS.md` says it may be gone), reuse it:
   `/workspace/soba`, `/workspace/TripoSG` etc. are picked up automatically.
4. **Expose HTTP port 8000** (the viewer). Deploy, then open the **web terminal**.

Cost reference: RunPod listed an RTX 4090 pod at about $0.69/h when the ADR
was written (2026-09-11). The smoke-size run below takes well under an hour
including bootstrap; the dense build adds 30–60 min.

### A2. Bootstrap on `develop` (web terminal)

```bash
cd /workspace
# fresh volume:
git clone --branch develop https://github.com/sladojevicm/soba.git
# existing volume: cd soba && git fetch && git checkout develop && git pull
REPO_BRANCH=develop bash soba/deploy/runpod/bootstrap.sh
```

`REPO_BRANCH=develop` matters: `master` has none of the Waves 0–3 code.
Bootstrap ends with the same Open3D CUDA check the script repeats as S1; if it
prints the CUDA warning here, you already have the answer to the wheel question.

**Tier-2-capable pod = TripoSG AND PatchComplete.** The gate's "complete" band at
tiers 2-4 runs PatchComplete (the paper's configuration); without it the engine falls
back to Poisson repair, which since 2026-09-17 is recorded per object in
`run_metrics.json` (`completion.counts`), in `/metrics` (`soba_completion_total`) and
on `GET /api/jobs/{id}` (`run.completion`). Runs 1 and 2 on 2026-09-16 had no
PatchComplete, so their "completion" objects were Poisson. For validation set
`SOBA_COMPLETION_STRICT=1` so a fallback fails the run instead of degrading it.

```bash
SETUP_TRIPOSG=1 SETUP_PATCHCOMPLETE=1 REPO_BRANCH=develop bash soba/deploy/runpod/bootstrap.sh
export SOBA_TRIPOSG_HOME=/workspace/TripoSG SOBA_PATCHCOMPLETE_HOME=/workspace/PatchComplete
export SOBA_COMPLETION_STRICT=1
```

Optional, adds ~10 min and ~10 GB: the generative band. Optional too, needed only for real
sensor bundles (TUM / OAK / phone) and the 2 gpu-marked tests: `SETUP_MAST3R=1` (clones
naver/mast3r with submodules and fetches the 2.6 GB metric checkpoint; then
`export SOBA_MAST3R_HOME=/workspace/mast3r`).

```bash
SETUP_TRIPOSG=1 REPO_BRANCH=develop bash soba/deploy/runpod/bootstrap.sh   # TripoSG + RMBG weights
export SOBA_TRIPOSG_HOME=/workspace/TripoSG
```

Without it, objects the gate routes to "generative" are **dropped** and the job
still completes; the summary says which happened. RMBG-1.4 is licence-gated on
HuggingFace: `export HF_TOKEN=...` first if the download 401s.

**Physics: VLM or lookup.** Without `ANTHROPIC_API_KEY` every object gets
`physics: lookup` (the density table), which is the fallback the paper reports
on, not its headline physics path (BENCHMARK.md §2: the VLM's material /
`fill_fraction` estimates). The two 2026-09-16 pod runs were lookup-only. To
run the VLM, paste the key on the pod, in the shell only, never in a file that
could be committed:

```bash
export ANTHROPIC_API_KEY=sk-ant-...        # pod shell only; never in the repo
# or, kept across pod restarts on the volume (chmod 600, gitignored path):
# cp soba/deploy/runpod/env.example /workspace/.env && chmod 600 /workspace/.env
# edit /workspace/.env, then before each run:  set -a; . /workspace/.env; set +a
```

The validation script confirms which path ran: the `Knobs:` line of
`summary.md` prints `ANTHROPIC_API_KEY=set` or `unset`, and every object in
the job's `scene.json` carries `source.physics_origin` (`vlm` or `lookup`,
also shown as the inspector's "physics" row in the viewer). Cost: one batched
Claude call per scene (all crops in one request), cents per scene. Leave
`RUNPOD_API_KEY` / endpoint ids **unset** for this session; the serverless
path is out of scope.

### A3. Run the validation

```bash
cd /workspace/soba
bash deploy/runpod/gpu_validate.sh                         # smoke size, ~15-30 min after bootstrap
# variants:
WITH_REDIS=1 bash deploy/runpod/gpu_validate.sh            # + the external queue worker (S7)
KEEP=1 bash deploy/runpod/gpu_validate.sh                  # leave the API up for the viewer
BUNDLE_DIR=bundles/office_3_dense STRIDE=1 MAX_FRAMES=2000 bash deploy/runpod/gpu_validate.sh   # dense build, own dir (30-60 min more)
```

Everything lands in `/workspace/validation/<stamp>/`. The script prints the
summary table at the end. `SCENE` is the Replica scene name (the HuggingFace download);
an existing bundle dir is reused, so a second density needs its own `BUNDLE_DIR`. A
`KEEP=1` API must be stopped before the next run (`pkill -f '[s]cripts/serve.py'`), or
S5 refuses to start and says so.

### A4. Look at the viewer

With `KEEP=1`: RunPod → your pod → **Connect → HTTP Service [Port 8000]**. Open
`/jobs/<job id>/` (the id is in the summary). Click an object, drag it, press
space to drop a ball. This is the first scene the job API ever served from a
real GPU build; a screenshot is worth keeping.

### A5. Paste back

Paste into the chat, or commit under `docs/validation/<stamp>/`:

- `/workspace/validation/<stamp>/summary.md` (the table; its `Knobs:` line says
  whether physics ran with the VLM or the lookup table)
- `cuda_check.txt` (one line decides the TSDF question)
- `pins.txt` (goes into `docs/model-pins.md`)
- if S6 failed: `job_real.json` and the tail of `api.log`
- if S2 failed: the FAILED lines of `pytest.txt`

I turn those into: `docs/model-pins.md` rows, a `STATUS.md` current-state
update, a dated `docs/log/` entry, and fixes for whatever failed.

### A6. Stop the pod

**Stop it** (not just close the terminal): on-demand pods bill while running.
The volume keeps everything for the next session.

## Your part — Path B (optional: the pipeline image itself on RunPod)

Path A validates the code; Path B additionally validates the **image**
(`docker/pipeline.Dockerfile`), which is what the serverless endpoint and the
`gpu` compose profile will run.

1. GitHub → Actions → **publish-images** → Run workflow on `develop`
   (tick `devel` only if you also want the nvcc variant). About 20 min.
2. GitHub → your profile → Packages → `soba-pipeline` → Package settings →
   **Change visibility → Public** (or add the GHCR credentials to RunPod under
   Settings → Container Registry Auth).
3. RunPod → Deploy → Pods → **Custom image**: `ghcr.io/sladojevicm/soba-pipeline:develop`,
   **Container start command**: `shell -c "sleep infinity"` (the default `worker`
   mode exits 2 without Redis), volume at `/workspace`, expose 8000.
4. In the web terminal: `cd /app && pipeline-entrypoint check` — this is the
   pre-deploy gate from `docs/runbook.md` §6 — then
   `bash deploy/runpod/gpu_validate.sh` exactly as in A3 (the repo lives at
   `/app` inside the image; the script finds its own root).

## What the results decide

| Observation | Consequence |
|---|---|
| S1 PASS | TSDF on GPU as designed; no action |
| S1 FAIL | The pip wheel has no CUDA module. Jobs still complete, TSDF on CPU. Decide: accept for the demo (dense builds take longer) or build Open3D with CUDA / find a CUDA wheel for the image and bootstrap. This becomes a `STATUS.md` known limitation either way. |
| S6 PASS, generative dropped | Plumbing works; rerun with TripoSG for the full tier-2 scene |
| S6 FAIL | Read `job_real.json` `error` (the worker stores the subprocess log tail). Most likely candidates: a missing model checkout path, or the `run_assemble.py` CLI drifting from `worker_local.py`'s call. Fix on `develop`, rerun. |
| S7 PASS | The Redis queue + external worker path is proven end to end; the compose `gpu` profile only adds Docker on top |
| S8 non-empty | Paste into `docs/model-pins.md`; for MASt3R / TripoSG / Hunyuan3D these are the first pins the repo has ever had |

## Run 1 — 2026-09-16 (RTX 4090, EU-RO-1)

S1 PASS (Open3D CUDA on the pip wheel), S3–S7 PASS (job API real mode 225 s, Redis
worker 227 s, 4 objects, generative band dropped: TripoSG not set up), S2 two failures
under review, S8 library versions only. Findings and interpretation:
`docs/log/2026-09-16-gpu-validation-run1.md`. Fixed from it: `blinker` pre-install in
bootstrap, `coacd` added to the `recon` extra. Next run: `SETUP_TRIPOSG=1` bootstrap,
then the same script.

## Run 2 — 2026-09-16 (same volume, TripoSG set up)

S6 PASS with the generative band: 10 objects (4 completion + 6 TripoSG, 4 declined by the
engine), 760 s; coacd hulls; TripoSG commit recorded (`docs/model-pins.md`). Fixed from it:
`setup_triposg.sh` pins the HF stack when torch < 2.5; `gpu_validate.sh` now writes the
`FAILED` summary lines (`-rfEs`). Details: `docs/log/2026-09-16-gpu-validation-run2.md`.

