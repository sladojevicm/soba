# Model pins — what actually produced the `BENCHMARK.md` numbers

_Written 2026-09-11 (docker-agent) from the repo alone: `deploy/runpod/*.sh`,
`src/reconstruction/*.py`, `src/perception/detect.py`, `scripts/`,
`BENCHMARK.md`'s header and `docs/log/`. Nothing here is guessed: where the
repo does not pin something the row says **NOT PINNED IN REPO** and names the
command that would recover it on the machine that ran the benchmark._

**Headline finding.** The repo pins **no** git commit, **no** checkpoint sha256
and **no** HuggingFace revision for any model. Every `git clone` in
`deploy/runpod/setup_*.sh` and `bootstrap.sh` is `--depth 1` of the default
branch at whatever time it ran; every `snapshot_download` / `from_pretrained`
takes the HF `main` revision of the day; the only checkpoints named at all are
named by filename. The `BENCHMARK.md` numbers are therefore reproducible up to
"the upstream default branches as of 2026-07-05/06" until the gaps below are
filled in from the two machines that ran them (RTX 4060 box, RunPod RTX 3090
pod — the latter "may be gone with the old pod", `STATUS.md`).

Which measurement used which model (from `BENCHMARK.md` and `docs/log/`):

| BENCHMARK section | Models involved | Machine |
|---|---|---|
| §1 Stage B tier 1 (RGB-D odometry) | Open3D only | WSL2 CPU (commit `363dc52`) |
| §1 Stage B tiers 2–4 (MASt3R) | MASt3R metric ckpt | RTX 4060, 2026-07-05 (fr2/xyz re-run 2026-07-17) |
| §2 physics (YCB/ABO) | CoACD, lookup table; Claude column = agent preview | WSL2 CPU |
| §3 Replica tiers 1–2 | TripoSG + RMBG-1.4, PatchComplete, CoACD, Open3D TSDF | RTX 4060 |
| §3 Replica tiers 3–4 | Hunyuan3D 2.1 shape + paint, PatchComplete, CoACD | RunPod RTX 3090, 2026-07-06 |
| §4 routing ablation | same stack as §3 tier 2 (generation cache reused tier-1 outputs) | RTX 4060, 2026-09-05 |
| YOLO + SAM2 | ran once on TUM fr1/xyz (100 frames, 2026-07-02); **no benchmark number depends on them** (`BENCHMARK.md` §1 Stage A: no detector wired into `TUMReader`) | RTX 4060 |

## Per-model pins

Legend for the "benchmark?" column: **yes** = this is what produced the
numbers (as far as the repo records); **default** = what the code loads today,
with no record that the benchmark used a different one.

### YOLO (detection, context A) — `src/perception/detect.py`, `scripts/run_tum_detect.py`

| Item | Value | benchmark? |
|---|---|---|
| Package | `ultralytics` — **NOT PINNED IN REPO** (in no pyproject extra; installed ad hoc on the 4060) | not used by any benchmark number |
| Weights | `yolo11s-seg.pt`, auto-downloaded by ultralytics on first use (release asset of the installed ultralytics version) | default |
| sha256 | **NOT PINNED IN REPO** | — |
| Recover | on the 4060: `pip show ultralytics \| grep Version`; `sha256sum ~/soba/models/yolo11s-seg.pt` (or wherever ultralytics cached it: `find ~ -name yolo11s-seg.pt`) | |

### SAM2 (mask refinement, context A) — `src/reconstruction/sam2_refine.py`

| Item | Value | benchmark? |
|---|---|---|
| Repo | `https://github.com/facebookresearch/sam2` (`pip install` of the checkout; not in any extra) | not used by any benchmark number |
| Git commit | **NOT PINNED IN REPO** | — |
| Checkpoint | `sam2.1_hiera_large.pt` (default path `~/soba/models/sam2.1_hiera_large.pt`) | default |
| Config | `configs/sam2.1/sam2.1_hiera_l.yaml` (inside the sam2 package) | default |
| sha256 | **NOT PINNED IN REPO** | — |
| Recover | on the 4060: `git -C <sam2 clone> rev-parse HEAD` (or `pip show sam2` / `SAM-2`); `sha256sum ~/soba/models/sam2.1_hiera_large.pt` | |

### MASt3R (poses, tiers 2–4, context B) — `src/reconstruction/slam.py`

| Item | Value | benchmark? |
|---|---|---|
| Repo | `naver/mast3r` with its `dust3r` and `dust3r/croco` subtrees (`SOBA_MAST3R_HOME`, default `~/soba/mast3r`); repo URL is implied by the import paths, never written down | yes (§1 tiers 2–4) |
| Git commit | **NOT PINNED IN REPO** | — |
| Checkpoint | `checkpoints/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth` (`Mast3rEstimator.CKPT`) | yes |
| sha256 | **NOT PINNED IN REPO** | — |
| Run settings that ARE pinned | `SOBA_MAST3R_MAX_IMAGES=24`, stride 3 (`config/pipeline.yaml` `frame_sample_stride`), `swin-3` pairs, 512 px, global alignment `niter=300`, `lr=0.01`, `schedule=cosine` — all in `slam.py` / `BENCHMARK.md` §1 | yes |
| Recover | on the 4060: `git -C ~/soba/mast3r rev-parse HEAD; git -C ~/soba/mast3r/dust3r rev-parse HEAD; sha256sum ~/soba/mast3r/checkpoints/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth` | |

### TripoSG (+ BriaRMBG) (image-to-3D, tiers 1–2) — `src/reconstruction/generative.py`, `deploy/runpod/setup_triposg.sh`

| Item | Value | benchmark? |
|---|---|---|
| Repo | `https://github.com/VAST-AI-Research/TripoSG.git` (`--depth 1`, default branch; `SOBA_TRIPOSG_HOME`) | yes (§3 tiers 1–2, §4) |
| Git commit | **NOT PINNED IN REPO** | — |
| Weights | HF `VAST-AI/TripoSG` → `pretrained_weights/TripoSG`; HF `briaai/RMBG-1.4` → `pretrained_weights/RMBG-1.4` (license-gated) | yes |
| HF revision | **NOT PINNED IN REPO** (`snapshot_download` without `revision=`) | — |
| sha256 | **NOT PINNED IN REPO** | — |
| Run settings that ARE pinned | steps 50, cfg 7.0, seed 42, `SOBA_TRIPOSG_FACES=40000` (`env.example`, `generative.py`); the §3 tier-1/2 builds ran with `VID2SIM_TRIPOSG_FLASH=0` (marching cubes, `diso` not built — `docs/log/2026-07-06`) | yes |
| Recover | on the 4060: `git -C ~/soba/TripoSG rev-parse HEAD`; `huggingface-cli scan-cache` or `cat ~/soba/TripoSG/pretrained_weights/TripoSG/.cache/huggingface/download/*.metadata` (the `commit_hash` lines); `sha256sum ~/soba/TripoSG/pretrained_weights/TripoSG/*.safetensors` | |

### Hunyuan3D 2.1 (image-to-3D, tiers 3–4) — `generative.py`, `deploy/runpod/setup_hunyuan3d.sh`

| Item | Value | benchmark? |
|---|---|---|
| Repo | `https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1.git` (`--depth 1`; `SOBA_HUNYUAN_HOME`) | yes (§3 tiers 3–4) |
| Git commit | **NOT PINNED IN REPO** | — |
| Shape weights | HF `tencent/Hunyuan3D-2.1` via `Hunyuan3DDiTFlowMatchingPipeline.from_pretrained` (HF cache); `tencent/Hunyuan3D-2mini` is the documented 8 GB alternative, **not** what the benchmark used | yes |
| Paint weights | hy3dpaint (same repo) + `RealESRGAN_x4plus.pth` from `https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/` (URL pinned, sha256 not) | yes (tiers 3–4 "+ paint") |
| HF revision / sha256 | **NOT PINNED IN REPO** | — |
| Run settings that ARE pinned | steps 30, seed 42, `SOBA_GEN_FACES=40000` | yes |
| Recover | on the pod (if the `/workspace` volume survives): `git -C /workspace/Hunyuan3D-2.1 rev-parse HEAD`; `huggingface-cli scan-cache` (revision of `tencent/Hunyuan3D-2.1`); `sha256sum /workspace/Hunyuan3D-2.1/hy3dpaint/ckpt/RealESRGAN_x4plus.pth`. If the pod is gone these are unrecoverable and the tier 3–4 rows are reproducible only against upstream `main` as of 2026-07-06. | |

### PatchComplete (completion band) — `src/reconstruction/patchcomplete_completion.py`

| Item | Value | benchmark? |
|---|---|---|
| Repo | **NOT PINNED IN REPO** — only the local path `~/soba/PatchComplete` (`SOBA_PATCHCOMPLETE_HOME`); no clone command exists in `deploy/` | yes (§3 all tiers with completion-routed objects, §4) |
| Git commit | **NOT PINNED IN REPO** | — |
| Weights | `trained_models/multi_res.pt`, `trained_models/patch_learning_res_{32,8,4}.pt`, few-shot codebook under `priors/` | yes |
| sha256 | **NOT PINNED IN REPO** | — |
| Recover | on the 4060: `git -C ~/soba/PatchComplete remote -v; git -C ~/soba/PatchComplete rev-parse HEAD; sha256sum ~/soba/PatchComplete/trained_models/*.pt` | |

**Permanent limitation (2026-09-17).** PatchComplete is the completion band of BENCHMARK.md
§3 tier 2, §4 "forced completion" and tiers 3-4 (`config/pipeline.yaml` `completion_model:
patchcomplete`, `generative.make_engine` default). Upstream is `yuchenrao/PatchComplete`
(NeurIPS'22); its weights are the authors' `trained_models.zip` on a TUM server
(`kaldir.vc.in.tum.de/yrao/trained_models.zip`, 1.9 GB) whose README states the models were
re-run after the paper. A file on a plain web server has no revision history, so:

| Item | Status |
|---|---|
| code commit | **reconstructable by date** (`scripts/reconstruct_pins.py`): the default-branch commit as of 2026-07-05/06 |
| `trained_models/*.pt` sha256 | **unpinnable / provenance-unverifiable**: the copy behind BENCHMARK.md is gone with the volume; today's download may or may not be byte-identical |
| what runs from now on | `deploy/runpod/setup_patchcomplete.sh` records the zip's and each `.pt`'s sha256 in `trained_models/SHA256SUMS`; paste them below as the *validation* pin |

Consequence: the completion band of the benchmark is reproducible "up to the authors'
current zip", and nothing in the repo can tighten that. Also new on 2026-09-17: a scene
records which completer actually ran (`run_metrics.json` `completion.counts`); the two
2026-09-16 pod runs were Poisson fallbacks, not PatchComplete.

### PoinTr, ComPC (optional completion backends, not used by the benchmark)

| Model | Repo | Pins | benchmark? |
|---|---|---|---|
| PoinTr | `https://github.com/yuxumin/PoinTr.git` (`--depth 1`); `pretrained/PoinTr_PCN.pth` (+ `AdaPoinTr_PCN.pth`, `PoinTr_ShapeNet55.pth`) from the model zoo, manual download | commit / sha256 **NOT PINNED IN REPO**; deleted from the 4060 on 2026-07-06 | no (completion bake-off only) |
| ComPC | `https://github.com/Tianxinhuang/ComPC.git` (`--depth 1`), isolated env: Python 3.10.13, CUDA 11.6 toolkit, `torch==1.12.1+cu116`, `torchvision==0.13.1+cu116`, gcc-10; SD-2.1 + Zero123 weights auto-download from HF | those library versions ARE pinned in `setup_compc.sh`; commit / HF revisions **NOT PINNED IN REPO** | no |

## Library versions

| Library | Benchmark value (source) | Pinned in repo? |
|---|---|---|
| Python | 3.12 on the 4060 (`docs/log/2026-06-28`); 3.11 on this WSL box and in CI | `requires-python >=3.11` only |
| torch / CUDA | `torch 2.6 + cu124` on the 4060 (`docs/log/2026-06-28`); pod = RunPod "PyTorch 2.x / CUDA 12.x" template, exact version **NOT PINNED IN REPO** | no — torch is in no extra; `docker/pipeline.Dockerfile` bases on `pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime` to match the 4060 |
| Open3D | 0.19 (`BENCHMARK.md` header; CPU build on WSL, CUDA wheel on the 4060) | `open3d>=0.18`; CI resolved 0.19.0 |
| numpy | 2.5 (`BENCHMARK.md` header) | `numpy>=1.26`; CI resolved 2.4.6 |
| CoACD | "1.0" in the `BENCHMARK.md` header, `1.0.11` in `docs/log/2026-06-27` | **not in any extra** (single-hull fallback without it); installed unpinned by `docker/pipeline.Dockerfile` |
| opencv / trimesh / scipy / scikit-image / pymeshfix | not recorded for the benchmark | lower bounds only (`pyproject.toml`) |
| anthropic SDK | Claude column is agent-preview, no SDK version recorded | not in any extra |
| **TripoSG on the RunPod pod, 2026-09-16 (run 2)** | git commit `fc5c40990181e2a756c4e0b1c2f4d6b5202faf8c` of https://github.com/VAST-AI-Research/TripoSG.git, cloned `--depth 1` that day; HF weights `VAST-AI/TripoSG` + `briaai/RMBG-1.4` at that day's `main` (revisions not captured: `huggingface_hub` cache scan printed nothing); HF stack pinned transformers 4.46.3 / diffusers 0.32.2 / peft 0.14.0 / accelerate 1.2.1 for torch 2.4.1 | **validation pin, not the benchmark pin** — the commit that produced BENCHMARK.md §3 still has to be read on the 4060 (`git -C ~/soba/TripoSG rev-parse HEAD`) |
| **Observed on the RunPod pod, 2026-09-16** (`docs/log/2026-09-16-gpu-validation-run1.md`) | Python 3.11.10 · torch 2.4.1+cu124 (RunPod "PyTorch 2.4.0" template) · open3d 0.19.0 **with a working CUDA tensor backend** · numpy 2.4.6 · scipy 1.17.1 · trimesh 5.1.0 · coacd **absent** (bootstrap gap, fixed the same day) | validation run, not a benchmark; no model checkouts on the fresh volume |
| Benchmark commit | `363dc52` on `fix/phase3-pose-and-eval` (2026-07-05); §3 tiers 3–4 built 2026-07-06, §4 on 2026-09-05 | `BENCHMARK.md` header |

## Reconstructed pins (by date) — NOT the original recorded pins

Generated on this box by `scripts/reconstruct_pins.py` (network + git, no GPU): for
each upstream repo, the default-branch commit as of the day the engineering log first
records that model running on the machine that produced the benchmark rows (a
`git clone --depth 1` that day would have produced it); for each HuggingFace repo, the
revision current on that day. The originals were never recorded and the volumes that
held them are gone, so this is the tightest statement the evidence supports. It is not
proof: a clone of a non-default branch or a local edit that day would not be captured.

<!-- generated by scripts/reconstruct_pins.py on 2026-09-16T23:06:38+00:00 -->
| Model | Kind | Pin | As of | Evidence |
|---|---|---|---|---|
| mast3r | git, **reconstructed by date** | `f5209afc300cec36239a7ac992263f36847bbba0` on `origin/main` (committed 2025-06-30) | 2026-07-05 | docs/log/2026-07-05-part2-gates-90-035-mast3r-benchmark.md (TUM rows measured) |
| mast3r/dust3r | submodule gitlink at that commit | `3cc8c88c413bb9e34c41db0e0eef99c2ee010b12` | 2026-07-05 | same |
| TripoSG | git, **reconstructed by date** | `fc5c40990181e2a756c4e0b1c2f4d6b5202faf8c` on `origin/main` (committed 2025-04-18) | 2026-07-01 | docs/log/2026-07-01-fusion-option-a-d-triposg-local.md (local TripoSG live) |
| Hunyuan3D-2.1 | git, **reconstructed by date** | `82920d643c0dc2f7bfd7255f45f62d386edfe60c` on `origin/main` (committed 2025-10-17) | 2026-07-02 | docs/log/2026-07-02-part3-runpod-hunyuan-live.md (pod endpoint live) |
| PatchComplete | git, **reconstructed by date** | `938303c4499173b0c9da28c6486b7fb171f71598` on `origin/main` (committed 2023-10-16) | 2026-06-30 | docs/log/2026-06-30-am-completion-bake-off.md (verdict: PatchComplete) |
| PoinTr | git, **reconstructed by date** | `4603257ed3db9e7dad349b712e1b2fe0da207015` on `origin/master` (committed 2026-06-24) | 2026-06-28 | docs/log/2026-06-28-handoff-snapshot.md (checkpoints downloaded) |
| sam2 | git, **reconstructed by date** | `2b90b9f5ceec907a1c18123530e92e794ad901a4` on `origin/main` (committed 2024-12-15) | 2026-07-02 | docs/log/2026-07-02-part2-dense-rebuild-yolo-sam2.md (no benchmark number uses it) |
| VAST-AI/TripoSG | HF revision, **reconstructed by date** | `2c1c516d22d58db486a058d98d31bb6177344e06` (committed 2025-03-28) | 2026-07-01 | |
| briaai/RMBG-1.4 | HF revision, **reconstructed by date** | `2ceba5a5efaec153162aedea169f76caf9b46cf8` (committed 2025-07-06) | 2026-07-01 | |
| tencent/Hunyuan3D-2.1 | HF revision, **reconstructed by date** | `0b94677654c57bb9a6b6845cd7b704ccf551d327` (committed 2025-10-17) | 2026-07-02 | |
| MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth | checkpoint, **exact** | sha256 pending | n/a | exact pin; sha256 from deploy/runpod/setup_mast3r.sh on the pod or --mast3r-sha256 here |
| PatchComplete trained_models.zip | weights | **UNPINNABLE** (no history on the TUM server) | n/a | docs/model-pins.md, permanent limitation |

Reading the table:

- **TripoSG, PatchComplete, SAM2, MASt3R**: the upstream default branch has not moved
  since the benchmark dates (last commits 2025-04, 2023-10, 2024-12, 2025-06), so the
  reconstructed commit is what a fresh clone returns today. The TripoSG row matches the
  commit the pod cloned on 2026-09-16 (run 2).
- **Hunyuan3D-2.1, PoinTr**: reconstructed commits may differ from today's tip; a pod
  setup that wants the benchmark-era code must check them out explicitly.
- **HF weights**: `snapshot_download(revision=...)` with the revision above reproduces
  the benchmark-era weights for TripoSG, RMBG-1.4 and Hunyuan3D-2.1.
- **MASt3R checkpoint**: exact, not reconstructed. Observed on the pod 2026-09-17 by
  `deploy/runpod/setup_mast3r.sh`:
  `MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth` sha256
  `e28f91b488554653e2b46ddae9c78c1143e0bcb2e27d3e26cdb0b717f1568eb2`.
  The same run cloned mast3r `f5209afc300cec36239a7ac992263f36847bbba0` with dust3r
  `3cc8c88c413bb9e34c41db0e0eef99c2ee010b12`: **identical to the date-reconstructed
  rows above**, i.e. upstream has not moved since the benchmark and the reconstruction
  method agrees with a real clone.
- **PatchComplete weights**: unpinnable, see the permanent limitation above.

## Validation pins observed on the RunPod pod (2026-09-17, run 4)

What the pod actually ran, from `pins.txt` of `/workspace/validation/20260917T074432Z`
(`deploy/runpod/gpu_validate.sh` S8). These are **observed**, not reconstructed, and they
pin the validation runs from 2026-09-17 on; they are not a claim about the July benchmark.

| Item | Pin |
|---|---|
| mast3r | `f5209afc300cec36239a7ac992263f36847bbba0` (https://github.com/naver/mast3r.git); dust3r submodule `3cc8c88c413bb9e34c41db0e0eef99c2ee010b12` |
| MASt3R checkpoint `MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth` | sha256 `e28f91b488554653e2b46ddae9c78c1143e0bcb2e27d3e26cdb0b717f1568eb2` (**exact**: single release file) |
| TripoSG | `fc5c40990181e2a756c4e0b1c2f4d6b5202faf8c` (https://github.com/VAST-AI-Research/TripoSG.git) |
| PatchComplete code | `938303c4499173b0c9da28c6486b7fb171f71598` (https://github.com/yuchenrao/PatchComplete.git) |
| PatchComplete `multi_res.pt` | sha256 `63bd929da01f730b4f85a40fe40c9ae23b0c32090681e301e6ff57ccd8760f5a` |
| PatchComplete `patch_learning_res_32.pt` | sha256 `777708619d454a928297f302e40a447b449351d01f8a8508ef3c37dee478416d` |
| PatchComplete `patch_learning_res_8.pt` | sha256 `71e5068a63082d9fed43bc95aca8e8bf4e83aa5cc999e0ae8220c1c31d964b60` |
| PatchComplete `patch_learning_res_4.pt` | sha256 `678a7b9a6cef88d18a8fc434c585e74614a2135b83ac7a70a7cc63a0f052d8b2` |
| PatchComplete `fine_tune.pt` (in the zip, not loaded by the adapter) | sha256 `6b5b61d2cd86db46b3df384553ed07a69f06b8904ca067939e5883298af79624` |
| Libraries | torch 2.4.1+cu124 · open3d 0.19.0 (CUDA tensor backend OK) · numpy 2.4.6 · scipy 1.17.1 · trimesh 5.1.0 · coacd 1.0.14 |

All three code commits equal the date-reconstructed rows above (mast3r, TripoSG,
PatchComplete upstreams have not moved since the benchmark dates). The PatchComplete
weight hashes pin what runs **from now on**; whether they equal the July weights remains
unknowable (permanent limitation above). If a future download of `trained_models.zip`
yields different hashes, the authors replaced the file and these rows are the record of
what the 2026-09-17 validation used.

## How to close the gaps

Run `deploy/runpod/recover_pins.sh` once on the RTX 4060 (paths per `CLAUDE.md`,
`~/soba/...`) and paste its output verbatim into the section below. It only
reads; every absent item prints `NOT FOUND`, which is itself a recorded fact.
The same script with `ROOT=/workspace` runs on a pod volume (Hunyuan3D pins
can only come from the volume that built tiers 3–4; if that volume is gone,
say so below and treat upstream `main` of 2026-07-06 as the best reference).

```bash
# on the 4060
cd ~/soba && git pull && bash deploy/runpod/recover_pins.sh
```

## Recovered pins (paste target)

_Not yet run on the 4060. The only recovered pins so far are from the RunPod
validation pod (2026-09-16, TripoSG commit `fc5c4099…`, library versions in the
table above), which are validation pins, not the benchmark's._

<!-- paste the recover_pins.sh output below this line -->
