#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# soba — one-shot host front-end run on a RunPod pod.
#
# Runs, in order: sanity tests -> Replica bundle -> scene assembly
# (run_assemble.py does the gate + TSDF + assembly end-to-end, Z-D order,
# skipping generative-routed objects). Then it PRINTS the serve command
# (serve.py blocks the terminal, so you start it yourself).
#
# Usage on the pod:
#     cd /workspace/soba && git pull && bash deploy/runpod/run_pipeline.sh
#
# Tunables (env) — defaults are a FAST smoke run; override for the dense build:
#     SCENE       scene name              (default office_3)
#     STRIDE      frame stride            (default 20  ; set 1 for dense)
#     MAX_FRAMES  max frames              (default 100 ; set 2000 for dense)
#     TIER        gate tier 2|3|4         (default 2)
#     REBUILD     1 = delete an existing bundle and re-stream it (default 0).
#                 build_replica_bundles.py SKIPS a scene whose bundle already
#                 exists, so changing STRIDE/MAX_FRAMES has NO effect unless you
#                 rebuild — set REBUILD=1 when you change frame density.
#
#   Dense quality build (STATUS.md's biggest lever, ~30-60 min):
#     REBUILD=1 STRIDE=1 MAX_FRAMES=2000 bash deploy/runpod/run_pipeline.sh
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
export PYTHONPATH=src

SCENE="${SCENE:-office_3}"
STRIDE="${STRIDE:-20}"
MAX_FRAMES="${MAX_FRAMES:-100}"
TIER="${TIER:-2}"
REBUILD="${REBUILD:-0}"

log(){ printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

log "1/3 Sanity tests (no GPU)"
python3 -m pytest -q tests/scene/test_schema.py tests/reconstruction/test_slam.py

if [ "$REBUILD" = "1" ] && [ -d "bundles/$SCENE" ]; then
  log "REBUILD=1 -> removing stale bundles/$SCENE so it re-streams at this density"
  rm -rf "bundles/$SCENE"
fi

log "2/3 Build Replica bundle: $SCENE (stride=$STRIDE, max-frames=$MAX_FRAMES)"
python3 scripts/build_replica_bundles.py \
  --scenes "$SCENE" --stride "$STRIDE" --max-frames "$MAX_FRAMES" --out bundles

log "3/3 Assemble scene (gate + TSDF + assembly, tier $TIER) -> out/scene_${SCENE}"
python3 scripts/run_assemble.py --bundle "bundles/$SCENE" --tier "$TIER" --out "out/scene_${SCENE}"

log "DONE"
cat <<EOF
Scene written to: out/scene_${SCENE}/scene.json

To VIEW it (this command blocks the terminal — run it in a new Jupyter terminal):
    cd $REPO_ROOT && PYTHONPATH=src python3 scripts/serve.py --scene out/scene_${SCENE} --host 0.0.0.0 --port 8000
Then in RunPod: Connect tab -> Port 8000 -> HTTP Service (waits, then opens in browser).

Optional inspection (NOT needed for the viewer):
    python3 scripts/run_tsdf.py --bundle bundles/$SCENE --out out/${SCENE}.ply   # mesh .ply for Blender
    python3 scripts/run_gate.py --bundle bundles/$SCENE --tier $TIER             # per-object routing json

Once this smoke run looks right, do the DENSE quality build:
    STRIDE=1 MAX_FRAMES=2000 bash deploy/runpod/run_pipeline.sh
EOF
