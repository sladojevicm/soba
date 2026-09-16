#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# soba — PatchComplete (completion band, tiers 2-4) setup on a GPU host.
#
# PatchComplete (Rao et al., NeurIPS'22) is the learned shape completer the
# gate's "complete" band uses at tiers 2, 3 and 4 (config/pipeline.yaml
# `completion_model: patchcomplete`, `generative.make_engine` default). Without
# it the engine silently falls back to Poisson repair — a different
# configuration from the one BENCHMARK.md §3/§4 measured.
#
# What it does:
#   1. clone https://github.com/yuchenrao/PatchComplete (the priors/ codebook
#      the model loads at construction time ships in the repo)
#   2. download the authors' trained_models.zip (1.9 GB, TUM server) and place
#      trained_models/{multi_res.pt, patch_learning_res_32.pt,
#      patch_learning_res_8.pt, patch_learning_res_4.pt} where
#      src/reconstruction/patchcomplete_completion.py loads them
#   3. record sha256 of the zip and of every .pt (docs/model-pins.md)
#   4. smoke-load the model in-process (no inference)
#
# Provenance caveat (docs/model-pins.md): the README says these models were
# re-run after the paper and "have different numbers compared with the paper",
# and a zip on a university server has no revision history. Whether today's
# zip is byte-identical to the one behind BENCHMARK.md cannot be established;
# the sha256 recorded here pins what THIS host uses from now on.
#
# Usage on the pod:
#     bash deploy/runpod/setup_patchcomplete.sh
#     export SOBA_PATCHCOMPLETE_HOME=/workspace/PatchComplete
# Override: PATCHCOMPLETE_HOME (default /workspace/PatchComplete),
#           PATCHCOMPLETE_WEIGHTS_URL.
# Not executed on the WSL box (no torch); the first pod run is the test.
# ---------------------------------------------------------------------------
set -euo pipefail
PATCHCOMPLETE_HOME="${PATCHCOMPLETE_HOME:-/workspace/PatchComplete}"
REPO_URL="${PATCHCOMPLETE_REPO_URL:-https://github.com/yuchenrao/PatchComplete.git}"
WEIGHTS_URL="${PATCHCOMPLETE_WEIGHTS_URL:-https://kaldir.vc.in.tum.de/yrao/trained_models.zip}"
NEEDED="multi_res.pt patch_learning_res_32.pt patch_learning_res_8.pt patch_learning_res_4.pt"
log(){ printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }

log "Clone / update PatchComplete -> $PATCHCOMPLETE_HOME"
if [ -d "$PATCHCOMPLETE_HOME/.git" ]; then
  git -C "$PATCHCOMPLETE_HOME" pull --ff-only || true
else
  git clone "$REPO_URL" "$PATCHCOMPLETE_HOME"
fi
[ -d "$PATCHCOMPLETE_HOME/priors" ] || { echo "priors/ missing in the checkout" >&2; exit 1; }
[ -f "$PATCHCOMPLETE_HOME/model/patch_learning_models.py" ] || { echo "model/ missing" >&2; exit 1; }

log "Python deps (the adapter needs only torch + numpy from the checkout; marching cubes is scikit-image, already in the recon extra)"
export PIP_ROOT_USER_ACTION=ignore
python3 -c "import torch, numpy, skimage; print('torch', torch.__version__, '| cuda', torch.cuda.is_available())"

log "Trained models -> $PATCHCOMPLETE_HOME/trained_models"
MODELS="$PATCHCOMPLETE_HOME/trained_models"
mkdir -p "$MODELS"
missing=0
for f in $NEEDED; do [ -s "$MODELS/$f" ] || missing=1; done
if [ "$missing" = 1 ]; then
  ZIP="$PATCHCOMPLETE_HOME/trained_models.zip"
  if [ ! -s "$ZIP" ]; then
    (curl -fL --retry 3 -C - -o "$ZIP" "$WEIGHTS_URL" || wget -c -O "$ZIP" "$WEIGHTS_URL")
  fi
  sha256sum "$ZIP" | tee "$ZIP.sha256"
  # The zip may hold the files at its root or under trained_models/; extract
  # into a scratch dir and move whatever we need into place.
  STAGE="$(mktemp -d "$PATCHCOMPLETE_HOME/.unzip_XXXX")"
  python3 - "$ZIP" "$STAGE" <<'PY'
import sys, zipfile
zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])
PY
  for f in $NEEDED; do
    src="$(find "$STAGE" -type f -name "$f" | head -1)"
    [ -n "$src" ] || { echo "ERROR: $f not in $ZIP (contents: $(find "$STAGE" -type f -name '*.pt' -printf '%f '))" >&2; exit 1; }
    mv -f "$src" "$MODELS/$f"
  done
  # keep any extra checkpoints the zip ships (fine_tune.pt etc.) next to them
  find "$STAGE" -type f -name '*.pt' -exec mv -f {} "$MODELS/" \; 2>/dev/null || true
  python3 - "$STAGE" <<'PY'
import shutil, sys; shutil.rmtree(sys.argv[1], ignore_errors=True)
PY
fi
( cd "$MODELS" && sha256sum $NEEDED | tee SHA256SUMS )

log "Smoke-load (constructs the multi_res model, no inference)"
SOBA_PATCHCOMPLETE_HOME="$PATCHCOMPLETE_HOME" PYTHONPATH="${SOBA_SRC:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/src}" python3 - <<'PY'
from reconstruction import patchcomplete_completion as pc
model, device = pc._load_model()
n = sum(p.numel() for p in model.parameters())
print(f"PatchComplete multi_res loaded on {device}: {n/1e6:.1f} M parameters")
PY

echo
echo "PatchComplete ready. export SOBA_PATCHCOMPLETE_HOME=$PATCHCOMPLETE_HOME"
echo "Pins (paste into docs/model-pins.md):"
echo "  patchcomplete code   $(git -C "$PATCHCOMPLETE_HOME" rev-parse HEAD) ($REPO_URL)"
echo "  trained_models.zip   $(cut -d' ' -f1 "$PATCHCOMPLETE_HOME/trained_models.zip.sha256" 2>/dev/null || echo 'zip not downloaded this run')"
sed 's/^/  /' "$MODELS/SHA256SUMS"
