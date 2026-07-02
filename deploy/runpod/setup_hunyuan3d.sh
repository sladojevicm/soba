#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# vid2sim-v2 — Hunyuan3D 2.1 (image-to-3D) setup for the generative band.
#
# Clones Tencent-Hunyuan/Hunyuan3D-2.1, installs its shape-model deps INTO the
# pod's existing CUDA torch (we do NOT touch torch), and downloads the weights.
# After this, LocalGpuEngine._run_hunyuan (and the serverless generative_handler)
# go live and the tier 3-4 generative band builds objects with Hunyuan3D.
#
# Usage on the pod:
#     bash deploy/runpod/setup_hunyuan3d.sh
# Override: HUNYUAN_HOME (default /workspace/Hunyuan3D-2.1),
#           HUNYUAN_MODEL (default tencent/Hunyuan3D-2.1; set tencent/Hunyuan3D-2mini
#           for the 0.6B variant that fits an 8 GB GPU).
#
# VRAM: shape generation needs ~10 GB (full 2.1) — it will OOM an 8 GB card, so
# run full 2.1 on a >=16 GB GPU (RunPod). The optional TEXTURE pass needs ~21 GB
# and is NOT installed by default (SETUP_HUNYUAN_PAINT=1 to add hy3dpaint).
# ---------------------------------------------------------------------------
set -euo pipefail

HUNYUAN_HOME="${HUNYUAN_HOME:-/workspace/Hunyuan3D-2.1}"
HUNYUAN_MODEL="${HUNYUAN_MODEL:-tencent/Hunyuan3D-2.1}"
REPO_URL="${HUNYUAN_REPO_URL:-https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1.git}"

log(){ printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

log "Clone / update Hunyuan3D 2.1 -> $HUNYUAN_HOME"
if [ -d "$HUNYUAN_HOME/.git" ]; then
  git -C "$HUNYUAN_HOME" pull --ff-only || true
else
  git clone --depth 1 "$REPO_URL" "$HUNYUAN_HOME"
fi

log "Install Hunyuan3D shape-model deps (NOT torch — keep the pod's CUDA build)"
# The shape pipeline (hy3dshape) needs these; torch/torchvision stay as shipped.
python3 -m pip install -q \
  "diffusers>=0.30" transformers accelerate huggingface_hub safetensors \
  einops omegaconf trimesh pymeshlab scikit-image opencv-python-headless \
  ninja pybind11 rembg onnxruntime timm

# hy3dshape ships a custom CUDA rasteriser / mesh-processor built from source;
# --no-build-isolation lets it see the pod's torch + nvcc (as with TripoSG's diso).
if [ -f "$HUNYUAN_HOME/hy3dshape/setup.py" ]; then
  log "Build hy3dshape custom ops (needs nvcc)"
  python3 -m pip install -q --no-build-isolation -e "$HUNYUAN_HOME/hy3dshape" || \
    echo "WARNING: hy3dshape build failed — check nvcc/CUDA toolkit on the pod."
fi

if [ "${SETUP_HUNYUAN_PAINT:-0}" = "1" ] && [ -d "$HUNYUAN_HOME/hy3dpaint" ]; then
  log "Install optional PBR texture model (hy3dpaint, ~21 GB VRAM at run time)"
  python3 -m pip install -q --no-build-isolation -e "$HUNYUAN_HOME/hy3dpaint" || \
    echo "WARNING: hy3dpaint build failed."
fi

log "Download weights ($HUNYUAN_MODEL) into the HuggingFace cache"
python3 - "$HUNYUAN_MODEL" <<'PY'
import sys
from huggingface_hub import snapshot_download
model = sys.argv[1]
try:
    p = snapshot_download(model)
    print("weights cached at:", p)
except Exception as e:
    print(f"WARNING: {model} download failed ({e}).")
    print("  If it is license-gated, accept the license on HuggingFace and")
    print("  `export HF_TOKEN=...`, then re-run this script.")
PY

log "Smoke-import (no inference)"
HUNYUAN_HOME="$HUNYUAN_HOME" python3 - <<'PY'
import os, sys
home = os.environ["HUNYUAN_HOME"]
for p in (home, os.path.join(home, "hy3dshape")):
    if os.path.isdir(p):
        sys.path.insert(0, p)
from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline  # noqa
print("  ok  hy3dshape import")
PY

cat <<EOF

Hunyuan3D 2.1 ready. Point the pipeline at it, then run a tier-3/4 build:
    export VID2SIM_HUNYUAN_HOME=$HUNYUAN_HOME
    export VID2SIM_GEN_MODEL=hunyuan3d          # or rely on the tier default (3/4)
    PYTHONPATH=src python3 scripts/run_assemble.py --bundle bundles/office_3 --tier 3 --out out/scene_office_3
Tiers 3-4 default to Hunyuan3D automatically (fix K1). Tunables:
VID2SIM_HUNYUAN_STEPS (default 30), _SEED (42), _MODEL (tencent/Hunyuan3D-2mini for 8 GB).
EOF
