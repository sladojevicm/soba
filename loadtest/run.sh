#!/usr/bin/env bash
# Soba load-test suite: brings up the compose cpu profile (api + redis + mock
# worker), runs the k6 scenarios and the SSE probe from the loadtest profile,
# writes loadtest/results/<UTC stamp>/ and prints the Markdown summary.
#
#   make loadtest                # full suite, stack torn down afterwards
#   KEEP=1 make loadtest         # leave the stack up (open mode) afterwards
#   SMOKE=1 make loadtest        # short CI variant (10 s per scenario)
#   NO_BUILD=1 make loadtest     # reuse the existing soba-api:local image
#
# Two passes: OPEN mode (no SOBA_API_KEYS, what compose runs by default) for
# every scenario, then KEYED mode (the api container recreated with a
# `loadtest`-flagged key and a plain key) for the rate-limit bypass proof and
# the 429 observation. SOBA_MOCK_DELAY_S (default 0.5) makes every mock job
# take half a second so uploads build queue depth.
#
# The numbers this produces are API-layer throughput with a mock worker on
# CPU. They are not pipeline or GPU throughput (CLAUDE.md invariant 7).
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

COMPOSE=${COMPOSE:-docker compose}
PYTHON=${PYTHON:-python3}
KEEP=${KEEP:-0}
SMOKE=${SMOKE:-0}
NO_BUILD=${NO_BUILD:-0}
API_URL=${API_URL:-http://127.0.0.1:8000}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
RES_HOST="loadtest/results/$STAMP"
export RESULTS_DIR="/results/$STAMP"
export SOBA_UID=${SOBA_UID:-$(id -u)}
export SOBA_GID=${SOBA_GID:-$(id -g)}
export SOBA_QUEUE_URL=${SOBA_QUEUE_URL:-redis://redis:6379/0}
export SOBA_WORKER_INPROC=0
export SOBA_MOCK_DELAY_S=${SOBA_MOCK_DELAY_S:-0.5}
export API_KEY=""

if [ "$SMOKE" = 1 ]; then
  D_UPLOAD=10s; D_POLL=10s; D_SCENE=10s; D_SSE=8s; D_MIXED=15s
  UPLOAD_VUS=5; KEYED_UPLOAD_VUS=10
else
  D_UPLOAD=${D_UPLOAD:-30s}; D_POLL=${D_POLL:-30s}; D_SCENE=${D_SCENE:-30s}
  D_SSE=${D_SSE:-20s}; D_MIXED=${D_MIXED:-45s}
  UPLOAD_VUS=${UPLOAD_VUS:-10}; KEYED_UPLOAD_VUS=${KEYED_UPLOAD_VUS:-20}
fi
SSE_CAP=${SOBA_SSE_MAX_PER_IP:-8}

mkdir -p "$RES_HOST"
LOG="$RES_HOST/run.log"
FAILED=()

say() { printf '\n== %s ==\n' "$*" | tee -a "$LOG"; }

wait_api() {
  for _ in $(seq 1 60); do
    if curl -sf -o /dev/null "$API_URL/scene.json"; then return 0; fi
    sleep 1
  done
  echo "api did not answer at $API_URL" >&2
  $COMPOSE --profile cpu logs --tail 50 api >&2 || true
  return 1
}

# k6 <label> <script> [ENV=VAL ...]   (env pairs reach the k6 container)
k6() {
  local label=$1 script=$2; shift 2
  local envs=(-e "LABEL=$label" -e "API_KEY=${API_KEY}" -e "RESULTS_DIR=$RESULTS_DIR")
  for kv in "$@"; do envs+=(-e "$kv"); done
  say "k6 $label ($script $*)"
  local rc=0
  $COMPOSE --profile loadtest run --rm --no-deps -T "${envs[@]}" k6 run -q "$script" 2>&1 | tee -a "$LOG" || rc=${PIPESTATUS[0]}
  if [ "$rc" != 0 ]; then
    FAILED+=("$label")
    echo "  -> $label FAILED (k6 exit $rc)" | tee -a "$LOG"
  fi
}

# sse_probe <label> [args...]
sse_probe() {
  local label=$1; shift
  say "sse probe $label ($*)"
  if $COMPOSE --profile loadtest run --rm --no-deps -T -e "API_KEY=${API_KEY}" -e "RESULTS_DIR=$RESULTS_DIR" \
       sse-probe --out "$RESULTS_DIR/$label.json" "$@" 2>&1 | tee -a "$LOG"; then
    :
  else
    FAILED+=("$label")
    echo "  -> $label FAILED" | tee -a "$LOG"
  fi
}

cleanup() {
  if [ "$KEEP" = 1 ]; then
    say "KEEP=1: restoring open mode and leaving the stack up"
    SOBA_API_KEYS="" $COMPOSE --profile cpu up -d --no-build api >>"$LOG" 2>&1 || true
  else
    say "compose down"
    $COMPOSE --profile cpu --profile loadtest down >>"$LOG" 2>&1 || true
  fi
}
trap cleanup EXIT

# ---- 0. stack ----------------------------------------------------------------
say "load test $STAMP -> $RES_HOST (SMOKE=$SMOKE KEEP=$KEEP)"
if [ ! -f out/scene_test/scene.json ]; then
  say "out/scene_test missing: python scripts/make_test_scene.py"
  $PYTHON scripts/make_test_scene.py | tee -a "$LOG"
fi
BUILD=(--build); [ "$NO_BUILD" = 1 ] && BUILD=(--no-build)
SOBA_API_KEYS="" $COMPOSE --profile cpu up -d "${BUILD[@]}" 2>&1 | tail -5 | tee -a "$LOG"
wait_api
K6_VERSION=$($COMPOSE --profile loadtest run --rm --no-deps -T k6 version 2>/dev/null | head -1 || echo unknown)

# ---- 1. open mode ------------------------------------------------------------
say "OPEN mode (no SOBA_API_KEYS): general 100 rps / burst 200, upload 1 rps / burst 10 per IP"
k6 upload_burst    upload_burst.js   "VUS=$UPLOAD_VUS" "DURATION=$D_UPLOAD"
k6 status_polling  status_polling.js "RATE=80" "DURATION=$D_POLL" "EXPECT_NO_429=1"
k6 scene_fetch     scene_fetch.js    "RATE=8" "DURATION=$D_SCENE"
k6 sse_clients     sse_clients.js    "VUS=$SSE_CAP" "DURATION=$D_SSE" "HOLD_S=3"
k6 sse_clients_cap sse_clients.js    "VUS=$((SSE_CAP + 1))" "DURATION=$D_SSE" "HOLD_S=3" "EXPECT_CAP=1"
sse_probe sse_probe --clients "$SSE_CAP" --hold 3 --expect-cap "$SSE_CAP"
k6 mixed           mixed.js          "DURATION=$D_MIXED"

# ---- 2. keyed mode -----------------------------------------------------------
rand() { od -An -N16 -tx1 /dev/urandom | tr -d ' \n'; }
LT_KEY=$(rand); PLAIN_KEY=$(rand)
say "KEYED mode: SOBA_API_KEYS=loadtest:<k>:loadtest,plain:<k> (api recreated)"
SOBA_API_KEYS="loadtest:${LT_KEY}:loadtest,plain:${PLAIN_KEY}" $COMPOSE --profile cpu up -d --no-build api 2>&1 | tail -2 | tee -a "$LOG"
wait_api
code=$(curl -s -o /dev/null -w '%{http_code}' "$API_URL/api/jobs")
echo "unauthenticated GET /api/jobs -> $code (expect 401)" | tee -a "$LOG"
[ "$code" = 401 ] || FAILED+=("keyed_mode_401")

API_KEY=$LT_KEY
k6 keyed_loadtest_upload_burst   upload_burst.js   "VUS=$KEYED_UPLOAD_VUS" "DURATION=$D_UPLOAD" "EXPECT_NO_429=1"
k6 keyed_loadtest_status_polling status_polling.js "RATE=300" "DURATION=$D_POLL" "EXPECT_NO_429=1" "MAX_VUS=300"
API_KEY=$PLAIN_KEY
k6 keyed_plain_status_polling    status_polling.js "RATE=300" "DURATION=$D_POLL" "EXPECT_429=1" "MAX_VUS=300"
k6 keyed_plain_upload_burst      upload_burst.js   "VUS=$KEYED_UPLOAD_VUS" "DURATION=$D_UPLOAD" "EXPECT_429=1"
API_KEY=""

# ---- 3. record ---------------------------------------------------------------
say "summary"
{
  printf '{'
  printf '"stamp": "%s", "smoke": %s, "k6": "%s", ' "$STAMP" "$([ "$SMOKE" = 1 ] && echo true || echo false)" "$K6_VERSION"
  printf '"docker": "%s", "compose": "%s", ' "$(docker version --format '{{.Server.Version}}' 2>/dev/null)" "$($COMPOSE version --short 2>/dev/null)"
  printf '"host": "%s", "cpus": %s, "mem": "%s", ' "$(uname -srm)" "$(nproc)" "$(free -h 2>/dev/null | awk '/^Mem:/{print $2}')"
  printf '"cpu_model": "%s", ' "$(lscpu 2>/dev/null | awk -F: '/Model name/{gsub(/^ +/,"",$2); print $2}')"
  printf '"compose_env": {"SOBA_QUEUE_URL": "%s", "SOBA_WORKER_INPROC": "0", "SOBA_MOCK_DELAY_S": "%s", "SOBA_SSE_MAX_PER_IP": "%s", "rate_limits": "defaults (100/200, upload 1/10)"}, ' "$SOBA_QUEUE_URL" "$SOBA_MOCK_DELAY_S" "$SSE_CAP"
  printf '"durations": {"upload": "%s", "poll": "%s", "scene": "%s", "sse": "%s", "mixed": "%s"}, ' "$D_UPLOAD" "$D_POLL" "$D_SCENE" "$D_SSE" "$D_MIXED"
  printf '"failed": ['
  first=1; for f in "${FAILED[@]:-}"; do [ -n "$f" ] || continue; [ $first = 1 ] || printf ', '; printf '"%s"' "$f"; first=0; done
  printf ']}\n'
} > "$RES_HOST/run.json"
$PYTHON loadtest/summarize.py "$RES_HOST" | tee -a "$LOG"

if [ ${#FAILED[@]} -gt 0 ]; then
  echo "FAILED scenarios: ${FAILED[*]}" | tee -a "$LOG"
  exit 1
fi
echo "all scenarios passed -> $RES_HOST" | tee -a "$LOG"
