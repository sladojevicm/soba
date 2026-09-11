#!/usr/bin/env bash
# Entrypoint of docker/pipeline.Dockerfile. First argument selects the mode;
# everything after it is passed through.
#
#   worker      python -m orchestration.worker        (default; feat/runpod-orchestration)
#   serverless  python deploy/runpod/generative_handler.py   (RunPod serverless)
#   assemble    python scripts/run_assemble.py "$@"   (one-shot pipeline run)
#   serve       python scripts/serve.py "$@"          (scene server on :8000)
#   check       Open3D CUDA tensor check on THIS host (fails if no CUDA device)
#   shell       bash "$@"
#   <anything else>  executed verbatim
set -euo pipefail

cd /app
mode="${1:-worker}"
[ "$#" -gt 0 ] && shift

case "$mode" in
  worker)
    if ! python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('orchestration.worker') else 1)"; then
      echo "pipeline-entrypoint: src/orchestration/worker.py is not in this build." >&2
      echo "  It lands with feat/runpod-orchestration (.claude/AGENTS.md agent 3)." >&2
      echo "  Other modes: serverless | assemble <args> | serve <args> | check | shell" >&2
      exit 3
    fi
    exec python -m orchestration.worker "$@"
    ;;
  serverless)
    exec python deploy/runpod/generative_handler.py "$@"
    ;;
  assemble)
    exec python scripts/run_assemble.py "$@"
    ;;
  serve)
    exec python scripts/serve.py --host 0.0.0.0 --port 8000 "$@"
    ;;
  check)
    exec python - <<'PY'
import sys
import open3d as o3d, torch
print("open3d", o3d.__version__, "| torch", torch.__version__,
      "| torch.cuda available", torch.cuda.is_available())
try:
    dev = o3d.core.Device("CUDA:0")
    o3d.core.Tensor.zeros((2, 2), device=dev)
    print("open3d CUDA tensor OK on", dev, "(TSDF VoxelBlockGrid will run on GPU)")
except Exception as e:
    print("FAIL open3d CUDA tensor:", e)
    sys.exit(1)
PY
    ;;
  shell)
    exec bash "$@"
    ;;
  *)
    exec "$mode" "$@"
    ;;
esac
