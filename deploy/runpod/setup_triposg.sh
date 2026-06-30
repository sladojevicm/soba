#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# vid2sim-v2 — local TripoSG (image-to-3D) setup for the generative band.
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
python3 -m pip install -q \
  "diffusers>=0.30" transformers accelerate huggingface_hub safetensors \
  einops omegaconf peft trimesh pymeshlab scikit-image opencv-python-headless \
  diso   # differentiable iso-surface extraction — TripoSG's final mesh step
         # (builds a CUDA ext at install; needs the template's nvcc)

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
VID2SIM_TRIPOSG_HOME="$TRIPOSG_HOME" python3 - <<'PY'
import os, sys
home = os.environ["VID2SIM_TRIPOSG_HOME"]
for p in (home, os.path.join(home, "scripts")):
    sys.path.insert(0, p)
from triposg.pipelines.pipeline_triposg import TripoSGPipeline  # noqa
from image_process import prepare_image  # noqa
from briarmbg import BriaRMBG  # noqa
print("  ok  triposg + image_process + briarmbg import")
PY

cat <<EOF

TripoSG ready. Tell the pipeline where it lives, then run the gate:
    export VID2SIM_TRIPOSG_HOME=$TRIPOSG_HOME
    PYTHONPATH=src python3 scripts/run_assemble.py --bundle bundles/office_3 --tier 2 --out out/scene_office_3
Generative-routed objects now regenerate from their staged crops instead of being
dropped. Tunables: VID2SIM_TRIPOSG_STEPS (default 50), _CFG (7.0), _SEED (42).
EOF
