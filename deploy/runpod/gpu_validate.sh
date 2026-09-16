#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# soba — first GPU validation of the service layer (Waves 0-3) on a RunPod pod.
#
# Runs, records and never fakes (CLAUDE.md invariant 7):
#   S0  environment      git sha, GPU, torch / open3d versions
#   S1  cuda_check       Open3D CUDA tensor backend (the docs/model-pins.md finding)
#   S2  pytest           full suite INCLUDING gpu-marked tests
#   S3  bundle           Replica bundle (reused if present, else built from HF)
#   S4  fixture_scene    out/scene_test for the legacy root routes
#   S5  api_real         scripts/serve.py with the in-process worker in REAL mode
#   S6  job_real         POST /api/jobs -> run_assemble.py on the GPU -> done,
#                        run_metrics.json, cost.json, /metrics gate counters
#   S7  redis_worker     (WITH_REDIS=1) redis-server + python -m orchestration.worker
#                        consuming the queue, second job through that path
#   S8  pins             docs/model-pins.md recovery commands on this pod
#   S9  summary          <OUT>/<stamp>/summary.md — paste it back
#
# Usage on the pod, after bootstrap.sh (REPO_BRANCH=develop!):
#   cd /workspace/soba && bash deploy/runpod/gpu_validate.sh
# Knobs (env):
#   SCENE=office_3  STRIDE=20  MAX_FRAMES=100   smoke-size Replica bundle (as
#                                               run_pipeline.sh); STRIDE=1
#                                               MAX_FRAMES=2000 = the dense build
#   TIER=2          gate tier for the job
#   WITH_REDIS=1    also validate the external queue worker (apt installs redis)
#   KEEP=1          leave the API (and worker) running for the browser viewer
#   PORT=8000       API port (expose it on the pod for the viewer)
#   OUT=/workspace/validation   where <stamp>/ lands
#   JOB_TIMEOUT=7200            seconds to wait for a job
# Generative band: with SOBA_TRIPOSG_HOME set to a setup_triposg.sh checkout the
# gate's "generative" objects are built; without it they are DROPPED (LocalEngine)
# and the run still completes — both are valid outcomes, the summary says which.
# ---------------------------------------------------------------------------
set -uo pipefail   # deliberately no -e: every step records its own result

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO"
export PYTHONPATH="$REPO/src"
PY="${PYTHON:-python3}"
SCENE="${SCENE:-office_3}"; STRIDE="${STRIDE:-20}"; MAX_FRAMES="${MAX_FRAMES:-100}"
TIER="${TIER:-2}"; WITH_REDIS="${WITH_REDIS:-0}"; KEEP="${KEEP:-0}"; PORT="${PORT:-8000}"
JOB_TIMEOUT="${JOB_TIMEOUT:-7200}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN="${OUT:-/workspace/validation}/$STAMP"; mkdir -p "$RUN"
JOBS_DIR="$REPO/out/jobs"; mkdir -p "$JOBS_DIR"
API_URL="http://127.0.0.1:$PORT"
SUMMARY="$RUN/summary.md"

log(){ printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
RESULTS=()
record(){ RESULTS+=("| $1 | $2 | $3 |"); printf '   -> %s: %s — %s\n' "$1" "$2" "$3"; }
API_PID=""; WORKER_PID=""
cleanup(){
  if [ "$KEEP" != 1 ]; then
    [ -n "$WORKER_PID" ] && { kill "$WORKER_PID" 2>/dev/null; wait "$WORKER_PID" 2>/dev/null; }
    [ -n "$API_PID" ] && { kill "$API_PID" 2>/dev/null; wait "$API_PID" 2>/dev/null; }
  fi
}
trap cleanup EXIT

wait_http(){ # url seconds
  for _ in $(seq 1 "$2"); do curl -sf "$1" >/dev/null 2>&1 && return 0; sleep 1; done; return 1
}
start_api(){ # extra env via caller's exports
  $PY scripts/serve.py --scene out/scene_test --host 0.0.0.0 --port "$PORT" \
      --worker-mode real --jobs-dir "$JOBS_DIR" > "$RUN/api.log" 2>&1 &
  API_PID=$!
  wait_http "$API_URL/scene.json" 60
}
stop_api(){ [ -n "$API_PID" ] && { kill "$API_PID" 2>/dev/null; wait "$API_PID" 2>/dev/null; API_PID=""; }; }

submit_job(){ # label -> sets JID, JOB_RC
  local label="$1"
  $PY scripts/submit_job.py --bundle "bundles/$SCENE" --tier "$TIER" \
      --server "$API_URL" --timeout "$JOB_TIMEOUT" > "$RUN/job_$label.log" 2>&1
  JOB_RC=$?
  JID="$(grep -oE '\b[0-9a-f]{16}\b' "$RUN/job_$label.log" | head -1)"
  cat "$RUN/job_$label.log" | tail -4
  if [ -n "$JID" ]; then
    curl -s "$API_URL/api/jobs/$JID" > "$RUN/job_$label.json"
    for f in run_metrics.json cost.json scene.json; do
      [ -f "$JOBS_DIR/$JID/scene/$f" ] && cp "$JOBS_DIR/$JID/scene/$f" "$RUN/${label}_$f"
    done
    curl -s "$API_URL/metrics" | grep -E '^soba_(jobs|job_runs_total|gate_(decisions|routed)_total|pipeline_drops_total|remote_(calls|est_usd)_total|pipeline_stage_seconds_total)' > "$RUN/metrics_$label.txt"
  fi
}
job_detail(){ # label -> one-line detail from the artefacts
  local label="$1" d=""
  if [ -f "$RUN/${label}_scene.json" ]; then
    d="$($PY - "$RUN/${label}_scene.json" "$RUN/${label}_run_metrics.json" "$RUN/${label}_cost.json" <<'PY'
import json, sys, os
s = json.load(open(sys.argv[1])); n = len(s.get("objects", []))
parts = [f"{n} objects"]
if os.path.isfile(sys.argv[2]):
    m = json.load(open(sys.argv[2])); g = m.get("gate", {}).get("counts", {})
    st = m.get("stages", {})
    parts.append("gate " + "/".join(f"{k}={g.get(k, 0)}" for k in ("tsdf", "completion", "generative")))
    parts.append("drops " + json.dumps(m.get("drops", {})))
    if "run" in st: parts.append(f"run {st['run']['seconds']:.0f}s")
if os.path.isfile(sys.argv[3]):
    c = json.load(open(sys.argv[3])); r = c.get("remote", {})
    parts.append(f"remote calls={r.get('calls', 0)} est_usd={r.get('est_usd', 0)}")
print("; ".join(parts))
PY
)"
  fi
  echo "$d"
}

# --- S0 environment --------------------------------------------------------
log "S0 environment"
{
  echo "sha: $(git rev-parse --short HEAD) ($(git branch --show-current))"
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>&1
  $PY - <<'PY'
import torch, open3d as o3d, numpy, platform
print("python", platform.python_version(), "| torch", torch.__version__, "| cuda", torch.cuda.is_available(),
      "| open3d", o3d.__version__, "| numpy", numpy.__version__)
PY
} 2>&1 | tee "$RUN/env.txt"
record S0 INFO "$(head -1 "$RUN/env.txt"); $(sed -n 2p "$RUN/env.txt")"

# --- S1 Open3D CUDA tensor backend ---------------------------------------
log "S1 Open3D CUDA tensor check"
if $PY - > "$RUN/cuda_check.txt" 2>&1 <<'PY'
import sys, open3d as o3d, torch
print("torch.cuda", torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else "-")
try:
    dev = o3d.core.Device("CUDA:0"); o3d.core.Tensor.zeros((2, 2), device=dev)
    print("open3d CUDA tensor OK on", dev, "(TSDF VoxelBlockGrid will run on GPU)")
except Exception as e:
    print("FAIL open3d CUDA tensor:", e); sys.exit(1)
PY
then record S1 PASS "$(tail -1 "$RUN/cuda_check.txt")"
else record S1 FAIL "$(tail -1 "$RUN/cuda_check.txt" | cut -c1-140) — TSDF runs on CPU (docs/model-pins.md finding)"; fi
cat "$RUN/cuda_check.txt"

# --- S2 pytest (gpu-marked tests included) --------------------------------
log "S2 pytest (full suite, gpu tests included)"
$PY -m pytest -q -rfEs --color=no -p no:cacheprovider > "$RUN/pytest.txt" 2>&1; PT_RC=$?
PT_LINE="$(grep -E 'passed|failed|error' "$RUN/pytest.txt" | tail -1)"
[ "$PT_RC" = 0 ] && record S2 PASS "$PT_LINE" || record S2 FAIL "$PT_LINE (see pytest.txt)"
echo "$PT_LINE"

# --- S3 Replica bundle -----------------------------------------------------
log "S3 bundle bundles/$SCENE (stride=$STRIDE, max-frames=$MAX_FRAMES)"
if [ -f "bundles/$SCENE/manifest.json" ]; then
  record S3 PASS "reused existing bundles/$SCENE ($(ls "bundles/$SCENE/frames" | wc -l) frames)"
else
  if $PY scripts/build_replica_bundles.py --scenes "$SCENE" --stride "$STRIDE" \
        --max-frames "$MAX_FRAMES" --out bundles > "$RUN/bundle.log" 2>&1; then
    record S3 PASS "built ($(ls "bundles/$SCENE/frames" | wc -l) frames)"
  else record S3 FAIL "build_replica_bundles.py exit $? (see bundle.log)"; fi
fi

# --- S4 fixture scene for the legacy root routes -------------------------
log "S4 fixture scene"
if $PY scripts/make_test_scene.py --out out/scene_test > "$RUN/fixture.log" 2>&1; then
  record S4 PASS "out/scene_test written"; else record S4 FAIL "make_test_scene.py failed"; fi

# --- S5 API in real mode ---------------------------------------------------
log "S5 API (in-process worker, REAL mode) on :$PORT"
export SOBA_WORKER_MODE=real SOBA_JOBS_DIR="$JOBS_DIR"
unset SOBA_QUEUE_URL; export SOBA_WORKER_INPROC=1
if start_api; then record S5 PASS "GET /scene.json 200; open-mode warning: $(grep -c 'SOBA_API_KEYS is not set' "$RUN/api.log")"
else record S5 FAIL "API did not answer within 60 s (see api.log)"; tail -20 "$RUN/api.log"; fi

# --- S6 real job through the job API --------------------------------------
log "S6 job: bundles/$SCENE tier $TIER -> run_assemble.py on this GPU"
if [ -f "bundles/$SCENE/manifest.json" ] && [ -n "$API_PID" ]; then
  submit_job real
  if [ "$JOB_RC" = 0 ]; then record S6 PASS "job $JID done; $(job_detail real)"
  else record S6 FAIL "job ${JID:-?} exit $JOB_RC; error: $($PY -c "import json,sys;print(json.load(open(sys.argv[1])).get('error'))" "$RUN/job_real.json" 2>/dev/null | cut -c1-200)"; fi
else record S6 SKIP "no bundle or no API"; fi

# --- S7 Redis + external orchestration worker -----------------------------
if [ "$WITH_REDIS" = 1 ]; then
  log "S7 redis-server + python -m orchestration.worker"
  if ! command -v redis-server >/dev/null 2>&1; then
    (apt-get update -qq && apt-get install -y -qq redis-server) > "$RUN/redis_install.log" 2>&1 || true
  fi
  if command -v redis-server >/dev/null 2>&1; then
    redis-server --daemonize yes --port 6379 > "$RUN/redis.log" 2>&1
    stop_api
    export SOBA_QUEUE_URL=redis://127.0.0.1:6379/0 SOBA_WORKER_INPROC=0
    if start_api; then
      $PY -m orchestration.worker --jobs-dir "$JOBS_DIR" --mode real \
          --queue-url redis://127.0.0.1:6379/0 --poll-s 1 > "$RUN/worker.log" 2>&1 &
      WORKER_PID=$!
      sleep 2
      submit_job redis
      if [ "$JOB_RC" = 0 ]; then record S7 PASS "job $JID done via redis worker; $(job_detail redis)"
      else record S7 FAIL "job ${JID:-?} exit $JOB_RC (see worker.log / api.log)"; fi
    else record S7 FAIL "API with SOBA_QUEUE_URL did not start"; fi
  else record S7 FAIL "redis-server not installable here (see redis_install.log)"; fi
else
  record S7 SKIP "WITH_REDIS=1 not set"
fi

# --- S8 model pins on this pod (docs/model-pins.md recovery) --------------
log "S8 model pins"
{
  echo "# pins recovered on $(hostname) $STAMP (see docs/model-pins.md)"
  for d in mast3r mast3r/dust3r TripoSG Hunyuan3D-2.1 PatchComplete PoinTr ComPC; do
    p="/workspace/$d"; [ -d "$p/.git" ] && echo "$d: $(git -C "$p" rev-parse HEAD) ($(git -C "$p" remote get-url origin 2>/dev/null))"
  done
  for f in /workspace/mast3r/checkpoints/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth \
           /workspace/PatchComplete/trained_models/*.pt /workspace/PoinTr/pretrained/*.pth \
           /workspace/models/sam2.1_hiera_large.pt; do
    [ -f "$f" ] && echo "sha256 $(sha256sum "$f")"
  done
  for hf in VAST-AI/TripoSG briaai/RMBG-1.4 tencent/Hunyuan3D-2.1; do
    $PY - "$hf" <<'PY' 2>/dev/null
import sys
from huggingface_hub import scan_cache_dir
for r in scan_cache_dir().repos:
    if r.repo_id == sys.argv[1]:
        print(sys.argv[1], "revisions:", ", ".join(rev.commit_hash[:12] for rev in r.revisions))
PY
  done
  $PY -m pip list 2>/dev/null | grep -iE '^(torch|open3d|numpy|coacd|ultralytics|sam-?2|trimesh|scipy) ' | tr -s ' '
} > "$RUN/pins.txt" 2>&1
record S8 INFO "$(grep -c . "$RUN/pins.txt") lines in pins.txt (paste into docs/model-pins.md)"
cat "$RUN/pins.txt"

# --- S9 summary ------------------------------------------------------------
log "S9 summary -> $SUMMARY"
{
  echo "# GPU validation $STAMP"
  echo
  echo "Pod: $(hostname); $(head -1 "$RUN/env.txt"); $(sed -n 2p "$RUN/env.txt")"
  echo "Knobs: SCENE=$SCENE STRIDE=$STRIDE MAX_FRAMES=$MAX_FRAMES TIER=$TIER WITH_REDIS=$WITH_REDIS TRIPOSG=${SOBA_TRIPOSG_HOME:-unset} ANTHROPIC_API_KEY=$([ -n "${ANTHROPIC_API_KEY:-}" ] && echo set || echo unset)"
  echo
  echo "| step | result | detail |"; echo "|---|---|---|"
  printf '%s\n' "${RESULTS[@]}"
  echo
  echo "Artefacts: $RUN (env.txt cuda_check.txt pytest.txt api.log job_*.log job_*.json *_run_metrics.json *_cost.json metrics_*.txt pins.txt)"
} | tee "$SUMMARY"
if [ "$KEEP" = 1 ]; then
  echo; echo "KEEP=1: API left running on :$PORT — open the pod's HTTP port $PORT proxy in a browser: /jobs/${JID:-<id>}/"
fi
