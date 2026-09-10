"""ComPC subprocess-seam tests (compc_completion.py).

ComPC's real runtime (CUDA 11.6 / torch 1.12 / Zero123 weights) can't run here,
so we drive the seam with a STUB runner: a tiny python script that reads the
.npy partial and writes a denser .npy cloud. This proves the contract end-to-end
(normalise -> subprocess -> denormalise, same frame) without ComPC installed.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

from reconstruction import compc_completion


def _write_stub_runner(tmp_path: Path) -> Path:
    """A fake ComPC runner: load (N,3), append a tiny jittered copy, save (M,3)."""
    p = tmp_path / "stub_runner.py"
    p.write_text(textwrap.dedent("""
        import argparse, numpy as np
        ap = argparse.ArgumentParser()
        ap.add_argument("--input", required=True)
        ap.add_argument("--output", required=True)
        a = ap.parse_args()
        pts = np.load(a.input).astype(np.float32)
        extra = pts + np.float32(0.001)          # pretend-completed extra points
        np.save(a.output, np.concatenate([pts, extra], axis=0).astype(np.float32))
    """))
    return p


def _partial_cloud(n=256, shift=(2.0, -1.0, 0.5)) -> np.ndarray:
    rng = np.random.default_rng(0)
    return (rng.standard_normal((n, 3)).astype(np.float32) * 0.3) + np.float32(shift)


def test_complete_points_roundtrips_through_subprocess(tmp_path, monkeypatch):
    runner = _write_stub_runner(tmp_path)
    monkeypatch.setenv(
        "SOBA_COMPC_CMD",
        f"{sys.executable} {runner} --input {{input}} --output {{output}}")
    partial = _partial_cloud()
    dense = compc_completion.complete_points(partial)

    assert dense.ndim == 2 and dense.shape[1] == 3
    assert len(dense) > len(partial)                       # got "completed" points
    # frame preserved: denormalised output centroid ~ input centroid
    np.testing.assert_allclose(dense.mean(0), partial.mean(0), atol=0.05)
    # and the output stays in the input's spatial range (not unit-sphere)
    np.testing.assert_allclose(dense.min(0), partial.min(0), atol=0.2)


def test_unset_command_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("SOBA_COMPC_CMD", raising=False)
    with pytest.raises(RuntimeError, match="SOBA_COMPC_CMD"):
        compc_completion.complete_points(_partial_cloud())


def test_missing_tokens_raises(monkeypatch):
    monkeypatch.setenv("SOBA_COMPC_CMD", "echo no-tokens-here")
    with pytest.raises(RuntimeError, match="must contain"):
        compc_completion.complete_points(_partial_cloud())


def test_runner_failure_propagates(tmp_path, monkeypatch):
    monkeypatch.setenv("SOBA_COMPC_CMD", "false {input} {output}")
    with pytest.raises(RuntimeError, match="failed|no output"):
        compc_completion.complete_points(_partial_cloud())


def test_too_few_points_raises(monkeypatch):
    monkeypatch.setenv("SOBA_COMPC_CMD", "true {input} {output}")
    with pytest.raises(RuntimeError, match="too few"):
        compc_completion.complete_points(np.zeros((4, 3), dtype=np.float32))


def test_bad_shape_raises(monkeypatch):
    with pytest.raises(ValueError, match="N,3"):
        compc_completion.complete_points(np.zeros((10, 2), dtype=np.float32))
