#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# soba — recover the model pins behind BENCHMARK.md (docs/model-pins.md, the
# release-checklist gate G2 in docs/runbook.md).
#
# Run ONCE on the machine that produced the numbers (the RTX 4060, paths per
# CLAUDE.md: ~/soba/...), and optionally on the pod volume (/workspace/...).
# Prints a paste-ready Markdown block; it reads, never writes, never guesses:
# anything absent prints NOT FOUND so the gap stays visible.
#
#   bash deploy/runpod/recover_pins.sh                 # 4060 layout (~/soba)
#   ROOT=/workspace bash deploy/runpod/recover_pins.sh # pod volume layout
#   bash deploy/runpod/recover_pins.sh > pins.md       # then paste into docs/model-pins.md
# ---------------------------------------------------------------------------
set -uo pipefail
ROOT="${ROOT:-$HOME/soba}"
PY="${PYTHON:-python3}"

rev(){ git -C "$1" rev-parse HEAD 2>/dev/null || echo "NOT FOUND ($1)"; }
url(){ git -C "$1" remote get-url origin 2>/dev/null || echo "-"; }
sha(){ if [ -f "$1" ]; then sha256sum "$1" | cut -c1-64; else echo "NOT FOUND ($1)"; fi; }
ver(){ $PY -m pip show "$1" 2>/dev/null | awk '/^Version:/{print $2}' | grep . || echo "NOT INSTALLED"; }

echo "<!-- recovered by deploy/runpod/recover_pins.sh on $(hostname) $(date -u +%Y-%m-%dT%H:%MZ), ROOT=$ROOT -->"
echo
echo "| Model | Repo commit | Checkpoint sha256 |"
echo "|---|---|---|"
printf '| MASt3R | `%s` (%s) | `%s` |\n' "$(rev "$ROOT/mast3r")" "$(url "$ROOT/mast3r")" \
  "$(sha "$ROOT/mast3r/checkpoints/MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth")"
printf '| dust3r (submodule) | `%s` | – |\n' "$(rev "$ROOT/mast3r/dust3r")"
printf '| TripoSG | `%s` (%s) | see HF revisions below |\n' "$(rev "$ROOT/TripoSG")" "$(url "$ROOT/TripoSG")"
for f in "$ROOT"/TripoSG/pretrained_weights/TripoSG/*.safetensors "$ROOT"/TripoSG/pretrained_weights/TripoSG/*/*.safetensors; do
  [ -f "$f" ] && printf '| TripoSG weight `%s` | – | `%s` |\n' "${f#"$ROOT"/TripoSG/pretrained_weights/TripoSG/}" "$(sha "$f")"
done
printf '| Hunyuan3D 2.1 | `%s` | see HF revisions below |\n' "$(rev "$ROOT/Hunyuan3D-2.1")"
printf '| PatchComplete | `%s` (%s) | – |\n' "$(rev "$ROOT/PatchComplete")" "$(url "$ROOT/PatchComplete")"
for f in "$ROOT"/PatchComplete/trained_models/*.pt; do
  [ -f "$f" ] && printf '| PatchComplete `%s` | – | `%s` |\n' "$(basename "$f")" "$(sha "$f")"
done
printf '| PoinTr | `%s` | `%s` |\n' "$(rev "$ROOT/PoinTr")" "$(sha "$ROOT/PoinTr/pretrained/PoinTr_PCN.pth")"
printf '| SAM2 checkpoint | – | `%s` |\n' "$(sha "$ROOT/models/sam2.1_hiera_large.pt")"
YOLO=$(find "$HOME" -name 'yolo11s-seg.pt' -not -path '*/.cache/pip/*' 2>/dev/null | head -1)
printf '| YOLO `yolo11s-seg.pt` | ultralytics %s | `%s` |\n' "$(ver ultralytics)" "$( [ -n "$YOLO" ] && sha "$YOLO" || echo "NOT FOUND (yolo11s-seg.pt)")"
echo
echo "| Library | Version |"
echo "|---|---|"
for p in torch open3d numpy coacd trimesh scipy sam2 SAM-2 diffusers transformers; do
  v=$(ver "$p"); [ "$v" != "NOT INSTALLED" ] && printf '| %s | %s |\n' "$p" "$v"
done
printf '| python | %s |\n' "$($PY --version 2>&1 | awk '{print $2}')"
printf '| nvidia driver / GPU | %s |\n' "$(nvidia-smi --query-gpu=driver_version,name --format=csv,noheader 2>/dev/null | head -1 || echo "NOT FOUND")"
echo
echo "HuggingFace cache revisions (\`main\` of the download day; the row is the pin):"
echo '```'
$PY - <<'PY' 2>/dev/null || echo "huggingface_hub not importable: run \`huggingface-cli scan-cache\` by hand"
from huggingface_hub import scan_cache_dir
want = ("VAST-AI/TripoSG", "briaai/RMBG-1.4", "tencent/Hunyuan3D-2.1", "tencent/Hunyuan3D-2mini")
seen = set()
for r in scan_cache_dir().repos:
    if r.repo_id in want:
        seen.add(r.repo_id)
        print(r.repo_id, "->", ", ".join(sorted(rev.commit_hash for rev in r.revisions)),
              f"({r.size_on_disk / 1e9:.2f} GB)")
for w in want:
    if w not in seen:
        print(w, "-> NOT IN CACHE")
PY
echo '```'
