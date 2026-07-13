"""ComPC learned shape-completion backend — middle band, isolated subprocess.

ComPC (Tianxinhuang/ComPC, ICLR 2025) is *training-free*: it builds 3D Gaussians
from the partial cloud (observed geometry kept via a Preservation Constraint) and
hallucinates ONLY the unseen regions with a 2D-diffusion (Zero123/SDS) prior. It
beat the fixed-output transformers (PoinTr/AdaPoinTr/SeedFormer) on real Redwood
scans. See the impl-readiness research: weights are public (auto-downloaded), but
the runtime is a brittle CUDA 11.6 / torch 1.12 / py3.10 env — INCOMPATIBLE with
our venv (torch 2.6 / py3.12). So we never import ComPC; we run it as a quarantined
SUBPROCESS in its own environment and exchange point clouds via .npy files.

THE RUNNER CONTRACT (what `SOBA_COMPC_CMD` must satisfy)
----------------------------------------------------------
`SOBA_COMPC_CMD` is a command *template* containing the literal tokens
`{input}` and `{output}`. We substitute temp file paths and run it. The command
must, in ComPC's own env:
  1. load an (N,3) float32 array of object-local partial points from `{input}` (np.load),
  2. run ComPC completion,
  3. save the (M,3) float32 completed cloud to `{output}` (np.save), SAME frame as input.
Example:
  SOBA_COMPC_CMD="/opt/compc-env/bin/python ~/projects/soba/ComPC/soba_runner.py \\
                     --input {input} --output {output}"
If the var is unset or the runner fails, complete_points raises -> the engine
falls back to local Poisson (never crashes the pipeline). Output is a dense cloud
which the caller (LocalGpuEngine._run_completion) Poisson-meshes, identical to the
PoinTr path.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

import numpy as np

# ComPC's SDS optimisation is per-object and slow (minutes); be generous but
# bounded so a hung runner can't wedge the pipeline.
_DEFAULT_TIMEOUT_S = int(os.environ.get("SOBA_COMPC_TIMEOUT", "1800"))


def _build_command(input_path: Path, output_path: Path) -> list[str]:
    """Resolve `SOBA_COMPC_CMD` into an argv list with {input}/{output} filled.

    Raised errors here are caught upstream and turned into the Poisson fallback,
    so a missing/blank runner degrades gracefully rather than crashing."""
    tmpl = os.environ.get("SOBA_COMPC_CMD", "").strip()
    if not tmpl:
        raise RuntimeError(
            "SOBA_COMPC_CMD is not set — point it at a ComPC runner "
            "(see compc_completion docstring for the contract)")
    if "{input}" not in tmpl or "{output}" not in tmpl:
        raise RuntimeError(
            "SOBA_COMPC_CMD must contain the {input} and {output} tokens")
    filled = tmpl.replace("{input}", str(input_path)).replace("{output}", str(output_path))
    return shlex.split(os.path.expanduser(filled))


def complete_points(partial: np.ndarray, *, coco_class: str | None = None,
                    timeout_s: int | None = None) -> np.ndarray:
    """Complete a partial cloud -> dense cloud, in the SAME frame as the input.

    We recenter + unit-scale the input (ComPC is scale-sensitive), hand the
    normalised object-local points to the quarantined ComPC runner, and undo the
    normalisation on the result — mirroring the PoinTr path so the two backends
    are interchangeable behind `LocalGpuEngine._run_completion`."""
    pts = np.asarray(partial, dtype=np.float32)
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise ValueError(f"partial must be (N,3); got {pts.shape}")
    if len(pts) < 32:
        raise RuntimeError("too few points to complete")

    centroid = pts.mean(axis=0)
    pts = pts - centroid
    scale = float(np.max(np.sqrt((pts ** 2).sum(axis=1)))) or 1.0
    pts = pts / scale

    timeout_s = _DEFAULT_TIMEOUT_S if timeout_s is None else timeout_s
    with tempfile.TemporaryDirectory(prefix="compc_") as td:
        in_p = Path(td) / "partial.npy"
        out_p = Path(td) / "complete.npy"
        np.save(in_p, pts.astype(np.float32))
        cmd = _build_command(in_p, out_p)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"ComPC runner timed out after {timeout_s}s") from e
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-600:]
            raise RuntimeError(f"ComPC runner failed (exit {proc.returncode}): {tail}")
        if not out_p.is_file():
            raise RuntimeError("ComPC runner produced no output file")
        dense = np.load(out_p)

    dense = np.asarray(dense, dtype=np.float32)
    if dense.ndim != 2 or dense.shape[1] != 3 or len(dense) == 0:
        raise RuntimeError(f"ComPC runner returned a bad cloud: shape {dense.shape}")
    return dense * scale + centroid          # denormalise back to the input frame
