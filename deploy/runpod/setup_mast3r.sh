#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# soba — MASt3R (poses, tiers 2-4) setup on a GPU host.
#
# Clones naver/mast3r WITH its dust3r and croco submodules (slam.py puts all
# three on sys.path), installs the light inference deps into the host's
# existing CUDA torch (never torch itself), and downloads the metric
# checkpoint slam.Mast3rEstimator.CKPT expects under <home>/checkpoints/.
# Replica `_v2` bundles do not need this (they consume GT poses); any real
# sensor bundle (TUM, OAK, the phone path) does.
#
# Usage on the pod:
#     bash deploy/runpod/setup_mast3r.sh
#     export SOBA_MAST3R_HOME=/workspace/mast3r
# Override: MAST3R_HOME (default /workspace/mast3r), MAST3R_CKPT_URL.
# Not executed on the WSL box (no torch); first run on the pod is the test.
# ---------------------------------------------------------------------------
set -euo pipefail
MAST3R_HOME="${MAST3R_HOME:-/workspace/mast3r}"
REPO_URL="${MAST3R_REPO_URL:-https://github.com/naver/mast3r.git}"
CKPT="MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth"
CKPT_URL="${MAST3R_CKPT_URL:-https://download.europe.naverlabs.com/ComputerVision/MASt3R/$CKPT}"
log(){ printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

log "Clone / update mast3r (+ dust3r, croco submodules) -> $MAST3R_HOME"
if [ -d "$MAST3R_HOME/.git" ]; then
  git -C "$MAST3R_HOME" pull --ff-only || true
  git -C "$MAST3R_HOME" submodule update --init --recursive
else
  git clone --recursive "$REPO_URL" "$MAST3R_HOME"
fi
for d in "$MAST3R_HOME/dust3r" "$MAST3R_HOME/dust3r/croco"; do
  [ -f "$d/__init__.py" ] || [ -d "$d" ] || { echo "missing submodule $d" >&2; exit 1; }
done

log "Install MASt3R inference deps (NOT torch — keep the host's CUDA build)"
export PIP_ROOT_USER_ACTION=ignore
# dust3r/requirements.txt minus torch/torchvision/gradio; roma + einops are the
# real runtime needs, the rest is what load_images / global alignment import.
python3 -m pip install -q roma einops tqdm matplotlib pillow-heif "huggingface_hub>=0.22" scipy

log "Checkpoint -> $MAST3R_HOME/checkpoints/$CKPT"
mkdir -p "$MAST3R_HOME/checkpoints"
if [ ! -s "$MAST3R_HOME/checkpoints/$CKPT" ]; then
  (cd "$MAST3R_HOME/checkpoints" && (curl -fL --retry 3 -o "$CKPT" "$CKPT_URL" || wget -q -O "$CKPT" "$CKPT_URL"))
fi
ls -la "$MAST3R_HOME/checkpoints/$CKPT"
sha256sum "$MAST3R_HOME/checkpoints/$CKPT" | tee "$MAST3R_HOME/checkpoints/$CKPT.sha256"

log "Smoke-import (no inference)"
SOBA_MAST3R_HOME="$MAST3R_HOME" python3 - <<'PY'
import os, sys
home = os.environ["SOBA_MAST3R_HOME"]
for p in (home, os.path.join(home, "dust3r"), os.path.join(home, "dust3r", "croco")):
    sys.path.insert(0, p)
from mast3r.model import AsymmetricMASt3R  # noqa: F401
from dust3r.cloud_opt import global_aligner  # noqa: F401
from dust3r.utils.image import load_images  # noqa: F401
import torch
print("mast3r + dust3r import OK | torch", torch.__version__, "| cuda", torch.cuda.is_available())
PY

echo
echo "MASt3R ready. export SOBA_MAST3R_HOME=$MAST3R_HOME"
echo "Pins (paste into docs/model-pins.md):"
echo "  mast3r $(git -C "$MAST3R_HOME" rev-parse HEAD)"
echo "  dust3r $(git -C "$MAST3R_HOME/dust3r" rev-parse HEAD)"
echo "  ckpt   $(cut -d' ' -f1 "$MAST3R_HOME/checkpoints/$CKPT.sha256")"
