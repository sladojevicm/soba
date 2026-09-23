#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# soba — local TripoSG (image-to-3D) setup for the generative band.
#
# Clones VAST-AI-Research/TripoSG, installs its python deps INTO the pod's
# existing CUDA torch (we do NOT touch torch — TripoSG's requirements pin one,
# so we install only the pieces inference needs), and downloads the TripoSG +
# BriaRMBG weights. After this, LocalGpuEngine._run_gen goes live with no other
# change and the gate's "generative" band builds objects instead of dropping them.
#
# Usage on the pod:
#     bash deploy/runpod/setup_triposg.sh
# Override: TRIPOSG_HOME (default /workspace/TripoSG). RMBG-1.4 is license-gated
# on HuggingFace — if the download 401s, `export HF_TOKEN=...` first.
# ---------------------------------------------------------------------------
set -euo pipefail

TRIPOSG_HOME="${TRIPOSG_HOME:-/workspace/TripoSG}"
REPO_URL="${TRIPOSG_REPO_URL:-https://github.com/VAST-AI-Research/TripoSG.git}"

log(){ printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

log "Clone / update TripoSG -> $TRIPOSG_HOME"
if [ -d "$TRIPOSG_HOME/.git" ]; then
  git -C "$TRIPOSG_HOME" pull --ff-only || true
else
  git clone --depth 1 "$REPO_URL" "$TRIPOSG_HOME"
fi

log "Install TripoSG inference deps (NOT torch — keep the pod's CUDA build)"
# Only what scripts/inference_triposg.py + the pipeline import. torch/torchvision
# stay as the template shipped them.
#
# The HF stack is pinned to the pod's torch: current transformers requires
# torch >= 2.5 and, on an older torch, disables itself ("PyTorch was not
# found"), which makes `import diffusers` die with "name 'nn' is not defined"
# (observed on the RunPod "PyTorch 2.4.0" template, 2026-09-16). Override with
# TRIPOSG_HF_PINS="..." if you know better.
TORCH_VER=$(python3 -c "import torch; v = torch.__version__.split('+')[0].split('.'); print(int(v[0]) * 100 + int(v[1]))")
if [ -z "${TRIPOSG_HF_PINS:-}" ]; then
  if [ "$TORCH_VER" -lt 205 ]; then
    # last HF releases built and tested against torch 2.4 (Dec 2024 line)
    TRIPOSG_HF_PINS="transformers==4.46.3 diffusers==0.32.2 peft==0.14.0 accelerate==1.2.1"
    echo "torch $(python3 -c 'import torch; print(torch.__version__)') < 2.5 -> pinning $TRIPOSG_HF_PINS"
  else
    TRIPOSG_HF_PINS="diffusers>=0.30 transformers accelerate peft"
  fi
fi
# shellcheck disable=SC2086
python3 -m pip install -q $TRIPOSG_HF_PINS huggingface_hub safetensors \
  einops omegaconf trimesh pymeshlab scikit-image opencv-python-headless \
  jaxtyping typeguard   # TripoSG type-annotation deps (pure python)

# diso = TripoSG's differentiable iso-surface extraction (its final mesh step).
# Its setup.py imports torch at build time, so it MUST skip build isolation to
# see the pod's torch; it then compiles a CUDA ext (needs the template's nvcc).
python3 -m pip install -q --no-build-isolation diso

log "Download weights (TripoSG + BriaRMBG) -> $TRIPOSG_HOME/pretrained_weights"
python3 - "$TRIPOSG_HOME" <<'PY'
import os, sys
from huggingface_hub import snapshot_download
home = sys.argv[1]
out = os.path.join(home, "pretrained_weights")
snapshot_download("VAST-AI/TripoSG", local_dir=os.path.join(out, "TripoSG"))
try:
    snapshot_download("briaai/RMBG-1.4", local_dir=os.path.join(out, "RMBG-1.4"))
except Exception as e:
    print(f"WARNING: RMBG-1.4 download failed ({e}).")
    print("  briaai/RMBG-1.4 is license-gated — accept the license on HuggingFace")
    print("  and `export HF_TOKEN=...`, then re-run this script.")
print("weights under:", out)
PY

log "Smoke-import (no inference)"
SOBA_TRIPOSG_HOME="$TRIPOSG_HOME" python3 - <<'PY'
import os, sys
home = os.environ["SOBA_TRIPOSG_HOME"]
for p in (home, os.path.join(home, "scripts")):
    sys.path.insert(0, p)
from triposg.pipelines.pipeline_triposg import TripoSGPipeline  # noqa
from image_process import prepare_image  # noqa
from briarmbg import BriaRMBG  # noqa
print("  ok  triposg + image_process + briarmbg import")
PY

cat <<EOF

TripoSG ready. Tell the pipeline where it lives, then run the gate:
    export SOBA_TRIPOSG_HOME=$TRIPOSG_HOME
    PYTHONPATH=src python3 scripts/run_assemble.py --bundle bundles/office_3 --tier 2 --out out/scene_office_3
Generative-routed objects now regenerate from their staged crops instead of being
dropped. Tunables: SOBA_TRIPOSG_STEPS (default 50), _CFG (7.0), _SEED (42).
EOF
