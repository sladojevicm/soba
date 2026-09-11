#!/usr/bin/env bash
# Routing ablation for the ERK camera-ready (reviewer request):
# tier-2 settings, but routing FORCED to one strategy for every object,
# vs the gated tier-2 scenes already on disk.
# Sequential — never two GPU builds at once (4060 OOM gotcha).
set -uo pipefail
REPO="$HOME/soba"
PY="$HOME/soba/.venv/bin/python"
cd "$REPO" || exit 2
export PYTHONPATH=src
export SOBA_TRIPOSG_HOME="$HOME/soba/TripoSG"
export SOBA_TRIPOSG_FLASH=0
export SOBA_GEN_MODEL=triposg
export SOBA_PATCHCOMPLETE_HOME="$HOME/soba/PatchComplete"

ROOMS="office_0 office_1 office_2 office_3 office_4 room_0 room_2"
STRATS="tsdf completion generative"

for ROOM in $ROOMS; do
  BUNDLE="$HOME/soba/data/replica/bundles/${ROOM}_v2"
  for S in $STRATS; do
    OUT="$REPO/out/abl_${ROOM}_${S}"
    LOG="$REPO/scratchpad/abl_${ROOM}_${S}.log"
    if [ -f "$OUT/eval.json" ]; then
      echo "SKIP $ROOM $S (eval.json exists)"
      continue
    fi
    echo "=== ABL BUILD $ROOM $S $(date -Iseconds) ==="
    $PY -u scripts/run_assemble.py \
      --bundle "$BUNDLE" --tier 2 --force-strategy "$S" \
      --out "$OUT" --no-eval > "$LOG" 2>&1
    RC=$?
    echo "=== ABL BUILD END $ROOM $S rc=$RC $(date -Iseconds) ==="
    if [ $RC -ne 0 ]; then
      echo "BUILD FAILED $ROOM $S (see $LOG)"
      continue
    fi
    $PY -u scripts/evaluate_scene.py \
      --scene "$OUT" --room "$ROOM" --bundle "$BUNDLE" \
      --gt-traj /nonexistent.txt --out "$OUT/eval.json" >> "$LOG" 2>&1
    ERC=$?
    echo "=== ABL EVAL END $ROOM $S rc=$ERC ==="
  done
done
echo "=== ABLATION DONE $(date -Iseconds) ==="
