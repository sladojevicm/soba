#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# vid2sim-v2 — RunPod host-pipeline bootstrap.
#
# Idempotent: safe to re-run. Targets a RunPod "PyTorch 2.x / CUDA 12.x" pod
# template (torch + CUDA already installed). It clones the repo, installs the
# recon + serve extras, verifies the GPU stack (including Open3D's CUDA tensor
# backend that TSDF needs), and optionally sets up the PoinTr middle band.
#
# Usage on the pod:
#     export SETUP_POINTR=0          # 1 to also clone PoinTr (optional)
#     bash bootstrap.sh
#
# Override any of these via env before running:
#     REPO_URL REPO_BRANCH WORKDIR REPO_DIR POINTR_HOME SETUP_POINTR
# See deploy/runpod/README.md for the full rental + run guide.
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/sladojevicm/vid2sim-v2.git}"
REPO_BRANCH="${REPO_BRANCH:-fix/phase3-pose-and-eval}"
WORKDIR="${WORKDIR:-/workspace}"
REPO_DIR="${REPO_DIR:-$WORKDIR/vid2sim-v2}"
POINTR_HOME="${POINTR_HOME:-$WORKDIR/PoinTr}"
SETUP_POINTR="${SETUP_POINTR:-0}"   # 1 = also clone PoinTr (optional middle band)
SETUP_COMPC="${SETUP_COMPC:-0}"     # 1 = also build ComPC (training-free, preserves
                                    #     observed geometry; isolated env, >=16GB GPU)

log(){ printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

log "GPU check"
if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "WARNING: nvidia-smi not found — this pod has no GPU. TSDF/SAM2/PoinTr need CUDA." >&2
else
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
fi

log "System packages (git + libGL/glib for open3d & opencv)"
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq || true
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git libgl1 libglib2.0-0 >/dev/null || true
fi

log "Clone / update repo ($REPO_BRANCH)"
mkdir -p "$WORKDIR"
if [ -d "$REPO_DIR/.git" ]; then
  git -C "$REPO_DIR" fetch --depth 1 origin "$REPO_BRANCH"
  git -C "$REPO_DIR" checkout "$REPO_BRANCH"
  git -C "$REPO_DIR" reset --hard "origin/$REPO_BRANCH"
else
  git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$REPO_DIR"
fi

log "Python / torch"
python3 --version
if python3 -c "import torch; assert torch.cuda.is_available()" 2>/dev/null; then
  echo "torch + CUDA present on the system interpreter — installing into it."
else
  echo "WARNING: torch+CUDA not importable on this interpreter." >&2
  echo "         On a RunPod *PyTorch* template it is preinstalled. If you booted a" >&2
  echo "         bare CUDA image, install a CUDA torch build first, e.g.:" >&2
  echo "           pip install torch --index-url https://download.pytorch.org/whl/cu121" >&2
fi

log "Install vid2sim-v2 (recon + serve + dev extras)"
python3 -m pip install --upgrade pip -q
python3 -m pip install -e "${REPO_DIR}[recon,serve,dev]" -q

log "Verify host-pipeline stack"
python3 - <<'PY'
import importlib
for m in ("numpy","yaml","jsonschema","open3d","cv2","trimesh","PIL","starlette","uvicorn"):
    importlib.import_module(m); print(f"  ok  {m}")
import open3d as o3d, torch
print("  open3d", o3d.__version__, "| torch", torch.__version__, "| cuda", torch.cuda.is_available())
try:
    dev = o3d.core.Device("CUDA:0")
    o3d.core.Tensor.zeros((2, 2), device=dev)
    print("  open3d CUDA tensor OK on", dev, "(TSDF VoxelBlockGrid will run on GPU)")
except Exception as e:
    print("  WARNING open3d CUDA tensor FAILED — TSDF will fall back to CPU:", e)
PY

if [ "$SETUP_POINTR" = "1" ]; then
  log "Optional: PoinTr middle-band completion"
  if [ ! -d "$POINTR_HOME/.git" ]; then
    git clone --depth 1 https://github.com/yuxumin/PoinTr.git "$POINTR_HOME"
  fi
  # pointr_completion.py SHIMS the custom CUDA ops in pure torch, so we do NOT
  # build pointnet2/extensions (no nvcc). Only PoinTr's plain-python deps:
  python3 -m pip install -q easydict timm h5py termcolor || true
  mkdir -p "$POINTR_HOME/pretrained"
  echo "PoinTr cloned to $POINTR_HOME."
  echo "Checkpoints are NOT auto-downloaded (Google-Drive + license). Place:"
  echo "    pretrained/PoinTr_PCN.pth        and/or"
  echo "    pretrained/PoinTr_ShapeNet55.pth (convention-matched — see STATUS.md)"
  echo "from the PoinTr model zoo: https://github.com/yuxumin/PoinTr"
  echo "Then: export POINTR_HOME=$POINTR_HOME"
  echo "NOTE: STATUS.md found point-completion makes well-observed objects WORSE."
  echo "      The proven host path is input -> cloud -> TSDF -> gate -> assemble -> serve."
fi

if [ "$SETUP_COMPC" = "1" ]; then
  log "Optional: ComPC middle-band completion (isolated env — brittle, may need iteration)"
  # Run non-fatally: a ComPC build hiccup must NOT break the working host pipeline.
  if COMPC_HOME="${COMPC_HOME:-$WORKDIR/ComPC}" REPO_DIR="$REPO_DIR" WORKDIR="$WORKDIR" \
       bash "$REPO_DIR/deploy/runpod/setup_compc.sh"; then
    echo "ComPC env built. Export VID2SIM_COMPC_CMD (printed above) to activate."
  else
    echo "WARNING: ComPC setup failed — host pipeline is unaffected. See output above." >&2
  fi
fi

log "Done — next steps"
cat <<EOF
  cd $REPO_DIR
  export PYTHONPATH=src
  # 1) sanity (no GPU): contract + pose unit tests
  python3 -m pytest -q tests/scene/test_schema.py tests/reconstruction/test_slam.py
  # 2) build a Replica bundle (GT masks/poses/depth — no camera, no SAM2/pose error)
  python3 scripts/build_replica_bundles.py --scenes office_3 --stride 1 --max-frames 2000 --out bundles
  # 3) front-end stages
  python3 scripts/run_tsdf.py     --bundle bundles/office_3 --out out/office_3.ply
  python3 scripts/run_gate.py     --bundle bundles/office_3 --tier 2
  python3 scripts/run_assemble.py --bundle bundles/office_3 --tier 2 --out out/scene_office_3
  # 4) view it (expose this TCP port on the pod; open the proxied URL in a browser)
  python3 scripts/serve.py --scene out/scene_office_3 --port 8000
EOF
