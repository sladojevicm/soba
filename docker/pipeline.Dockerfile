# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# soba pipeline image: the GPU side of the pipeline (contexts A-C) — poses,
# observed cloud, TSDF (Open3D CUDA tensor backend), the confidence gate,
# completion / generation adapters, scene assembly — plus the scene server.
# Mirrors deploy/runpod/bootstrap.sh: a CUDA torch base, apt git + libgl1 +
# libglib2.0-0, `pip install -e ".[recon,serve,dev]"`, an Open3D CUDA tensor
# check. Weights are NOT baked in (see "Model pins" below).
#
# Build (from the repo root; needs a lot of disk — the base is ~7 GB):
#   docker build -f docker/pipeline.Dockerfile -t soba-pipeline .
#
# Two entrypoints (docker/pipeline-entrypoint.sh):
#   worker      python -m orchestration.worker          (default)
#               The queue consumer from feat/runpod-orchestration
#               (src/orchestration/worker.py). Until that lands the entrypoint
#               reports the missing module and exits 3.
#   serverless  python deploy/runpod/generative_handler.py
#               RunPod serverless worker for the generative + completion bands
#               (needs the `runpod` SDK, installed below, and the TripoSG /
#               Hunyuan3D checkouts + weights mounted under /workspace).
#   assemble    scripts/run_assemble.py <args>   (one-shot, the same driver
#               deploy/runpod/run_pipeline.sh uses)
#   shell       bash
#
# Base image: `pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime` = torch 2.6 +
# CUDA 12.4, the stack recorded on the RTX 4060 (docs/log/2026-06-28) and the
# RunPod "PyTorch 2.x / CUDA 12.x" template bootstrap.sh targets. The runtime
# image has NO nvcc: TripoSG's `diso` and Hunyuan3D's hy3dshape custom ops
# (setup_triposg.sh / setup_hunyuan3d.sh) compile CUDA extensions, so build
# the serverless variant on the devel base:
#   docker build --build-arg BASE_IMAGE=pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel ...
#
# ---- Model pins (docs/model-pins.md is the authoritative table) ------------
# Nothing in this repo pins a git commit, checkpoint sha256 or HuggingFace
# revision for any model; the defaults below are what the code loads, NOT a
# reproducibility guarantee for BENCHMARK.md. Recovery commands are in
# docs/model-pins.md.
#   YOLO        ultralytics (unpinned)      yolo11s-seg.pt (auto-download)
#   SAM2        facebookresearch/sam2       sam2.1_hiera_large.pt
#                                           configs/sam2.1/sam2.1_hiera_l.yaml
#   MASt3R      naver/mast3r (+dust3r)      MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth
#   TripoSG     VAST-AI-Research/TripoSG    HF VAST-AI/TripoSG + briaai/RMBG-1.4 (no revision)
#   Hunyuan3D   Tencent-Hunyuan/Hunyuan3D-2.1  HF tencent/Hunyuan3D-2.1 (no revision)
#   PatchComplete (~/soba/PatchComplete)    trained_models/{multi_res,patch_learning_res_{32,8,4}}.pt
#   PoinTr      yuxumin/PoinTr              pretrained/PoinTr_PCN.pth
#   ComPC       Tianxinhuang/ComPC          isolated env: py 3.10.13, torch 1.12.1+cu116
#   Libraries   torch 2.6+cu124 · Open3D 0.19 · numpy 2.5 · CoACD 1.0.11 (BENCHMARK header: 1.0)
# The checkouts live on a volume and are found through the SOBA_*_HOME env
# vars set below (deploy/runpod/env.example lists every knob).
# ---------------------------------------------------------------------------
ARG BASE_IMAGE=pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    # src/ on the path (server.py and the scripts/ drivers expect PYTHONPATH=src)
    PYTHONPATH=/app/src \
    # Model checkouts + weights: mounted, never baked (deploy/runpod/env.example).
    WORKDIR=/workspace \
    SOBA_MAST3R_HOME=/workspace/mast3r \
    SOBA_TRIPOSG_HOME=/workspace/TripoSG \
    SOBA_HUNYUAN_HOME=/workspace/Hunyuan3D-2.1 \
    SOBA_PATCHCOMPLETE_HOME=/workspace/PatchComplete \
    POINTR_HOME=/workspace/PoinTr \
    # The serverless handler locates the repo's src/ through this.
    SOBA_SRC=/app/src

# git: the setup_*.sh scripts clone model repos. libgl1 + libglib2.0-0: open3d
# and opencv import them (same list as bootstrap.sh). Versions follow the base
# image's Ubuntu release, so they are not pinned here (hadolint DL3008).
# hadolint ignore=DL3008
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends git libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/
# Not in any pyproject extra, installed here on purpose:
#   coacd  — the convex decomposition behind every hull collider in
#            BENCHMARK.md (scene/decomp.py falls back to ONE hull without it).
#            Finding for the maintainer: it is absent from the `recon` extra.
#   runpod — the serverless SDK generative_handler.py starts under.
# torch is NOT reinstalled: it comes from the base image (bootstrap.sh does
# the same — "we do NOT touch torch"). Versions come from pyproject.toml's
# lower bounds; docs/model-pins.md records what actually produced the numbers.
# hadolint ignore=DL3013
RUN pip install -e ".[recon,serve,dev]" coacd runpod

COPY spec/ ./spec/
COPY config/ ./config/
COPY scripts/ ./scripts/
COPY deploy/ ./deploy/
COPY frontend/dist/ ./frontend/dist/
COPY docker/pipeline-entrypoint.sh /usr/local/bin/pipeline-entrypoint
RUN chmod +x /usr/local/bin/pipeline-entrypoint

# Build-time smoke test, mirroring bootstrap.sh "Verify host-pipeline stack".
# The Open3D CUDA tensor check WARNS instead of failing: the build runs on
# GitHub Actions with no GPU, where it can only ever warn. On a GPU host run
# the same check for real:   docker run --rm --gpus all soba-pipeline check
RUN python - <<'PY'
import importlib
for m in ("numpy", "yaml", "jsonschema", "open3d", "cv2", "trimesh", "PIL",
          "scipy", "skimage", "pymeshfix", "coacd", "starlette", "uvicorn", "runpod"):
    importlib.import_module(m); print(f"  ok  {m}")
import open3d as o3d, torch
print("  open3d", o3d.__version__, "| torch", torch.__version__,
      "| torch.cuda available", torch.cuda.is_available())
try:
    dev = o3d.core.Device("CUDA:0")
    o3d.core.Tensor.zeros((2, 2), device=dev)
    print("  open3d CUDA tensor OK on", dev)
except Exception as e:  # no GPU at build time is the normal case
    print("  WARNING open3d CUDA tensor unavailable at build time "
          "(expected without a GPU; TSDF falls back to CPU):", e)
PY

VOLUME ["/workspace"]
EXPOSE 8000

ENTRYPOINT ["pipeline-entrypoint"]
CMD ["worker"]
