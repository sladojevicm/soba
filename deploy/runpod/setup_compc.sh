#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# soba — ComPC middle-band setup (RunPod, big-GPU host).
#
# ComPC (Tianxinhuang/ComPC, ICLR'25) is training-free and PRESERVES observed
# geometry, but needs its OWN brittle env: python 3.10.13 + CUDA 11.6 toolkit +
# torch 1.12.1+cu116 + a compiled diff-gaussian-rasterizer. That is incompatible
# with the host pipeline's torch 2.x, so we build it in an ISOLATED micromamba
# env and the pipeline calls it as a subprocess (SOBA_COMPC_CMD).
#
# Diffusion weights (Stable-Diffusion-2.1 + Zero123) AUTO-DOWNLOAD from
# HuggingFace on first run — no manual download here.
#
# Run AFTER bootstrap.sh, on a >=16GB (ideally 24GB) GPU pod:
#     COMPC_HOME=/workspace/ComPC bash deploy/runpod/setup_compc.sh
#
# This is the brittle step. It is verbose and stops on first error so you can
# see exactly where it fails; paste the failure back and we iterate.
# ---------------------------------------------------------------------------
set -euo pipefail

WORKDIR="${WORKDIR:-/workspace}"
COMPC_HOME="${COMPC_HOME:-$WORKDIR/ComPC}"
COMPC_ENV="${COMPC_ENV:-$WORKDIR/compc-env}"
MAMBA_ROOT="${MAMBA_ROOT:-$WORKDIR/micromamba}"
REPO_DIR="${REPO_DIR:-$WORKDIR/soba}"

log(){ printf '\n\033[1;35m== %s ==\033[0m\n' "$*"; }

log "GPU + nvcc-host check"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || {
  echo "no GPU — ComPC needs CUDA"; exit 1; }

log "System libs for nvdiffrast / OpenGL (best-effort)"
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq || true
  # gcc-10/g++-10: CUDA 11.6's nvcc cannot parse GCC 11 libstdc++ headers
  # ("error: parameter packs not expanded with '...'") — host compiler must be <=10.
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    git build-essential ninja-build gcc-10 g++-10 libgl1 libglib2.0-0 \
    libegl1 libgles2 libglvnd-dev pkg-config >/dev/null 2>&1 || true
fi

log "Clone ComPC -> $COMPC_HOME"
if [ ! -d "$COMPC_HOME/.git" ]; then
  git clone --depth 1 https://github.com/Tianxinhuang/ComPC.git "$COMPC_HOME"
fi

log "micromamba + isolated py3.10.13 env"
if [ ! -x "$MAMBA_ROOT/bin/micromamba" ]; then
  mkdir -p "$MAMBA_ROOT/bin"
  curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest \
    | tar -xj -C "$MAMBA_ROOT" bin/micromamba
fi
export MAMBA_ROOT_PREFIX="$MAMBA_ROOT"
MM="$MAMBA_ROOT/bin/micromamba"
if [ ! -d "$COMPC_ENV" ]; then
  "$MM" create -y -p "$COMPC_ENV" -c conda-forge python=3.10.13
fi
PY="$COMPC_ENV/bin/python"

log "CUDA 11.6 toolkit (nvcc) into the env — needed to compile the rasterizer"
"$MM" install -y -p "$COMPC_ENV" -c "nvidia/label/cuda-11.6.0" cuda-toolkit || \
  "$MM" install -y -p "$COMPC_ENV" -c "nvidia/label/cuda-11.6.0" cuda-nvcc cuda-cudart-dev libcusparse-dev
export CUDA_HOME="$COMPC_ENV"
export PATH="$COMPC_ENV/bin:$PATH"
# Force nvcc's host compiler to gcc-10 (CUDA 11.6 rejects gcc-11 headers).
export CC="${CC:-/usr/bin/gcc-10}"
export CXX="${CXX:-/usr/bin/g++-10}"

log "torch 1.12.1+cu116"
"$PY" -m pip install --upgrade pip wheel -q
"$PY" -m pip install -q \
  torch==1.12.1+cu116 torchvision==0.13.1+cu116 torchaudio==0.12.1+cu116 \
  --extra-index-url https://download.pytorch.org/whl/cu116

log "ComPC requirements (compiles diff-gaussian-rasterization, pulls git deps)"
# ComPC's CUDA extensions (Chamfer3D, diff-gaussian-rasterization, nvdiffrast)
# `import torch` in their setup.py, so pip's ISOLATED build env (which lacks torch)
# fails with ModuleNotFoundError. --no-build-isolation makes the build see the
# env's torch; ninja is the compiler driver those extensions need.
# setuptools>=70 dropped pkg_resources, which torch 1.12's cpp_extension build
# still imports -> pin an older setuptools (and ninja/wheel) into the env first.
"$PY" -m pip install -q ninja wheel "setuptools<70"
( cd "$COMPC_HOME" && "$PY" -m pip install -q --no-build-isolation -r requirements.txt \
    --extra-index-url https://download.pytorch.org/whl/cu116 )

log "Smoke test: import torch + build the rasterizer"
"$PY" - <<'PY'
import torch
print("  torch", torch.__version__, "| cuda", torch.version.cuda, "| avail", torch.cuda.is_available())
import diff_gaussian_rasterization  # noqa: F401
print("  diff_gaussian_rasterization import OK")
PY

cat <<EOF

\033[1;32mComPC env ready.\033[0m To use it from the pipeline (in the soba venv):

  export COMPC_HOME="$COMPC_HOME"
  export SOBA_COMPLETION_MODEL=compc
  export SOBA_COMPC_CMD="$PY $REPO_DIR/deploy/runpod/compc_runner.py --input {input} --output {output}"

Quick standalone check (one object) BEFORE the full pipeline:
  $PY -c "import numpy as np; np.save('/tmp/p.npy', (np.random.rand(2048,3)-0.5).astype('float32'))"
  COMPC_HOME="$COMPC_HOME" $PY $REPO_DIR/deploy/runpod/compc_runner.py --input /tmp/p.npy --output /tmp/c.npy
  $PY -c "import numpy as np; print('completed', np.load('/tmp/c.npy').shape)"
EOF
