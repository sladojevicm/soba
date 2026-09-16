#!/usr/bin/env python
"""Reconstruct model pins BY DATE — not the original recorded pins.

The volumes that ran BENCHMARK.md are gone and the repo never recorded a
commit, checkpoint hash or HuggingFace revision for any model
(docs/model-pins.md). Every model was fetched as `git clone --depth 1` of the
upstream default branch, or `snapshot_download` of an HF repo's `main`, on a
day the engineering log records. This script answers "what would that clone
have produced on that day": the default-branch commit as of the date, and
the HF revision current on the date. It needs the network and git; no GPU.

The result is labelled RECONSTRUCTED (BY DATE) everywhere it is pasted. It is
the tightest statement the evidence supports, and it is NOT proof: a clone on
that day of a non-default branch, or a local edit, would not be captured.

What is exact instead of reconstructed:
  * the MASt3R metric checkpoint is a single fixed release file: its sha256 is
    recorded by deploy/runpod/setup_mast3r.sh on the pod (or --mast3r-sha256
    here, 2.6 GB download).
What cannot be reconstructed at all:
  * PatchComplete's trained_models.zip (a file on a university server, no
    history) — see docs/model-pins.md, permanent limitation.

Usage:
  python scripts/reconstruct_pins.py                 # Markdown table to stdout
  python scripts/reconstruct_pins.py --json pins.json
  python scripts/reconstruct_pins.py --mast3r-sha256 # also download + hash the ckpt
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

# "Cloned no later than" dates, each traced to the log entry that first records
# the model RUNNING on the machine that produced the benchmark rows.
GIT_REPOS = {
    "mast3r": ("https://github.com/naver/mast3r.git", "2026-07-05",
               "docs/log/2026-07-05-part2-gates-90-035-mast3r-benchmark.md (TUM rows measured)"),
    "TripoSG": ("https://github.com/VAST-AI-Research/TripoSG.git", "2026-07-01",
                "docs/log/2026-07-01-fusion-option-a-d-triposg-local.md (local TripoSG live)"),
    "Hunyuan3D-2.1": ("https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1.git", "2026-07-02",
                      "docs/log/2026-07-02-part3-runpod-hunyuan-live.md (pod endpoint live)"),
    "PatchComplete": ("https://github.com/yuchenrao/PatchComplete.git", "2026-06-30",
                      "docs/log/2026-06-30-am-completion-bake-off.md (verdict: PatchComplete)"),
    "PoinTr": ("https://github.com/yuxumin/PoinTr.git", "2026-06-28",
               "docs/log/2026-06-28-handoff-snapshot.md (checkpoints downloaded)"),
    "sam2": ("https://github.com/facebookresearch/sam2.git", "2026-07-02",
             "docs/log/2026-07-02-part2-dense-rebuild-yolo-sam2.md (no benchmark number uses it)"),
}
# dust3r is a submodule of mast3r: its pin is the gitlink inside the mast3r commit.
HF_REPOS = {
    "VAST-AI/TripoSG": "2026-07-01",
    "briaai/RMBG-1.4": "2026-07-01",
    "tencent/Hunyuan3D-2.1": "2026-07-02",
}
MAST3R_CKPT = "MASt3R_ViTLarge_BaseDecoder_512_catmlpdpt_metric.pth"
MAST3R_CKPT_URL = f"https://download.europe.naverlabs.com/ComputerVision/MASt3R/{MAST3R_CKPT}"


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True,
                                   stderr=subprocess.STDOUT).strip()


def commit_as_of(url: str, date: str, workdir: Path) -> dict:
    """Default-branch commit as of end of `date` (UTC) in a blobless clone."""
    name = url.rstrip("/").split("/")[-1].removesuffix(".git")
    repo = workdir / name
    _git("clone", "--quiet", "--filter=blob:none", "--no-checkout", url, str(repo))
    head_branch = _git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", cwd=repo)
    sha = _git("rev-list", "-1", f"--before={date}T23:59:59Z", head_branch, cwd=repo)
    if not sha:
        return {"error": f"no commit on {head_branch} before {date}"}
    when = _git("show", "-s", "--format=%cI", sha, cwd=repo)
    subject = _git("show", "-s", "--format=%s", sha, cwd=repo)
    out = {"repo": url, "branch": head_branch, "as_of": date, "commit": sha,
           "committed": when, "subject": subject[:80]}
    # submodule gitlinks recorded in that commit (dust3r, croco for mast3r)
    try:
        tree = _git("ls-tree", "-r", sha, cwd=repo)
        links = {line.split()[3]: line.split()[2] for line in tree.splitlines()
                 if line.split()[1] == "commit"}
        if links:
            out["submodules"] = links
    except subprocess.CalledProcessError:
        pass
    return out


def hf_revision_as_of(repo_id: str, date: str) -> dict:
    try:
        from huggingface_hub import HfApi
    except ImportError:
        return {"error": "huggingface_hub not installed (pip install huggingface_hub)"}
    api = HfApi()
    try:
        commits = api.list_repo_commits(repo_id)
    except Exception as exc:  # gated repos need a token; say so, do not guess
        return {"error": f"{type(exc).__name__}: {str(exc)[:120]} (gated? set HF_TOKEN)"}
    cutoff = dt.datetime.fromisoformat(date + "T23:59:59+00:00")
    before = [c for c in commits if c.created_at <= cutoff]
    if not before:
        return {"error": f"no revision before {date}"}
    c = max(before, key=lambda c: c.created_at)
    return {"repo": repo_id, "as_of": date, "revision": c.commit_id,
            "committed": c.created_at.isoformat(), "title": (c.title or "")[:80]}


def sha256_of_url(url: str, dest: Path) -> str:
    if not dest.is_file():
        urllib.request.urlretrieve(url, dest)
    h = hashlib.sha256()
    with dest.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", type=Path, help="also write the raw result here")
    ap.add_argument("--mast3r-sha256", action="store_true",
                    help="download the 2.6 GB MASt3R checkpoint and hash it (exact pin)")
    ap.add_argument("--keep", type=Path, help="keep the blobless clones here (default: temp)")
    args = ap.parse_args()

    workdir = args.keep or Path(tempfile.mkdtemp(prefix="soba-pins-"))
    workdir.mkdir(parents=True, exist_ok=True)
    result = {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "method": "reconstructed by date (default-branch commit / HF revision as of the "
                        "date the log first records the model running); NOT the original pins",
              "git": {}, "hf": {}, "exact": {}}
    try:
        for name, (url, date, why) in GIT_REPOS.items():
            print(f"git  {name:14s} as of {date} ...", file=sys.stderr)
            try:
                result["git"][name] = {**commit_as_of(url, date, workdir), "why": why}
            except subprocess.CalledProcessError as exc:
                result["git"][name] = {"error": exc.output.strip()[:200], "why": why}
        for repo_id, date in HF_REPOS.items():
            print(f"hf   {repo_id:22s} as of {date} ...", file=sys.stderr)
            result["hf"][repo_id] = hf_revision_as_of(repo_id, date)
        if args.mast3r_sha256:
            print("mast3r checkpoint: downloading + hashing (2.6 GB) ...", file=sys.stderr)
            result["exact"][MAST3R_CKPT] = {
                "sha256": sha256_of_url(MAST3R_CKPT_URL, workdir / MAST3R_CKPT),
                "url": MAST3R_CKPT_URL, "note": "single fixed release file: exact pin"}
        else:
            result["exact"][MAST3R_CKPT] = {
                "sha256": None, "url": MAST3R_CKPT_URL,
                "note": "exact pin; sha256 from deploy/runpod/setup_mast3r.sh on the pod "
                        "or --mast3r-sha256 here"}
    finally:
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)

    if args.json:
        args.json.write_text(json.dumps(result, indent=2))

    print(f"<!-- generated by scripts/reconstruct_pins.py on {result['generated']} -->")
    print("| Model | Kind | Pin | As of | Evidence |")
    print("|---|---|---|---|---|")
    for name, r in result["git"].items():
        if "error" in r:
            print(f"| {name} | git | ERROR: {r['error']} | {GIT_REPOS[name][1]} | {r['why']} |")
            continue
        print(f"| {name} | git, **reconstructed by date** | `{r['commit']}` on `{r['branch']}` "
              f"(committed {r['committed'][:10]}) | {r['as_of']} | {r['why']} |")
        for path, sha in (r.get("submodules") or {}).items():
            print(f"| {name}/{path} | submodule gitlink at that commit | `{sha}` | {r['as_of']} | same |")
    for repo_id, r in result["hf"].items():
        if "error" in r:
            print(f"| {repo_id} | HF | ERROR: {r['error']} | {HF_REPOS[repo_id]} | |")
        else:
            print(f"| {repo_id} | HF revision, **reconstructed by date** | `{r['revision']}` "
                  f"(committed {r['committed'][:10]}) | {r['as_of']} | |")
    for ck, r in result["exact"].items():
        print(f"| {ck} | checkpoint, **exact** | {('`' + r['sha256'] + '`') if r['sha256'] else 'sha256 pending'} | n/a | {r['note']} |")
    print("| PatchComplete trained_models.zip | weights | **UNPINNABLE** (no history on the "
          "TUM server) | n/a | docs/model-pins.md, permanent limitation |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
