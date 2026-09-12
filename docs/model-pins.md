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
| Benchmark commit | `363dc52` on `fix/phase3-pose-and-eval` (2026-07-05); §3 tiers 3–4 built 2026-07-06, §4 on 2026-09-05 | `BENCHMARK.md` header |

## How to close the gaps

Run on the RTX 4060 machine (paths per `CLAUDE.md`: `~/soba/...`) and paste the
output into this file:

```bash
for r in mast3r mast3r/dust3r TripoSG PatchComplete; do
  printf '%-22s %s\n' "$r" "$(git -C ~/soba/$r rev-parse HEAD 2>/dev/null || echo MISSING)"; done
sha256sum ~/soba/mast3r/checkpoints/*.pth ~/soba/PatchComplete/trained_models/*.pt \
          ~/soba/models/sam2.1_hiera_large.pt ~/soba/TripoSG/pretrained_weights/TripoSG/*.safetensors
pip show ultralytics sam2 torch open3d coacd numpy 2>/dev/null | grep -E '^(Name|Version)'
huggingface-cli scan-cache
```

The Hunyuan3D pins can only come from the RunPod volume that built tiers 3–4;
if it no longer exists, record that here and treat the upstream `main` of
2026-07-06 as the best available reference.
