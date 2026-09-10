#!/usr/bin/env python
"""soba <-> ComPC bridge runner. RUNS INSIDE ComPC's OWN env (py3.10/cu116).

Implements the soba subprocess contract (see soba
src/reconstruction/compc_completion.py): read an (N,3) float32 partial cloud from
--input (.npy), run ComPC's per-object optimization, write the (M,3) completed
cloud to --output (.npy), in the SAME frame.

The pipeline (running in OUR venv) invokes this via:
  SOBA_COMPC_CMD="<compc_env_python> <repo>/deploy/runpod/compc_runner.py \\
                     --input {input} --output {output}"
with COMPC_HOME pointing at the ComPC clone. It stages the cloud into the
indir/<name>/indata/<file>.ply layout ComPC's test.py expects, runs it, and reads
back outdir/<name>/<file>.ply (= ComPC's 'completed.ply').
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

import numpy as np


def _compc_home() -> str:
    h = os.environ.get("COMPC_HOME")
    if not h:
        for cand in (os.path.join(os.environ.get("WORKDIR", "/workspace"), "ComPC"),
                     os.path.expanduser("~/projects/soba/ComPC")):
            if os.path.isdir(cand):
                h = cand
                break
    if not h or not os.path.isfile(os.path.join(h, "test.py")):
        raise SystemExit(
            "COMPC_HOME is not set or has no test.py — point it at the ComPC clone")
    return h


def _write_ply_xyz(path: str, pts: np.ndarray) -> None:
    pts = np.asarray(pts, dtype=np.float32)
    with open(path, "w") as f:
        f.write("ply\nformat ascii 1.0\n")
        f.write(f"element vertex {len(pts)}\n")
        f.write("property float x\nproperty float y\nproperty float z\n")
        f.write("end_header\n")
        for p in pts:
            f.write(f"{p[0]} {p[1]} {p[2]}\n")


def _read_ply_xyz(path: str) -> np.ndarray:
    with open(path) as f:
        if not f.readline().startswith("ply"):
            raise ValueError(f"{path} is not a PLY file")
        n, order = 0, []
        while True:
            line = f.readline()
            if not line:
                raise ValueError("unexpected EOF in PLY header")
            t = line.split()
            if t and t[0] == "element" and t[1] == "vertex":
                n = int(t[2])
            elif t and t[0] == "property" and t[-1] in ("x", "y", "z"):
                order.append(t[-1])
            elif t and t[0] == "end_header":
                break
        cols = {name: i for i, name in enumerate(order)}
        ix, iy, iz = cols["x"], cols["y"], cols["z"]
        out = np.empty((n, 3), dtype=np.float32)
        for i in range(n):
            v = f.readline().split()
            out[i] = (float(v[ix]), float(v[iy]), float(v[iz]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="(N,3) float32 partial cloud .npy")
    ap.add_argument("--output", required=True, help="(M,3) float32 completed cloud .npy")
    ap.add_argument("--config", default="configs/synthetic.yaml")
    a = ap.parse_args()

    compc = _compc_home()
    pts = np.load(a.input).astype(np.float32)
    if pts.ndim != 2 or pts.shape[1] != 3:
        raise ValueError(f"input must be (N,3); got {pts.shape}")

    name, fname = "job", "obj.ply"
    with tempfile.TemporaryDirectory(prefix="compc_run_") as td:
        indata = os.path.join(td, "indir", name, "indata")
        os.makedirs(indata, exist_ok=True)
        _write_ply_xyz(os.path.join(indata, fname), pts)
        outdir = os.path.join(td, "out")

        cmd = [sys.executable, "test.py", "--config", a.config,
               "--indir", os.path.join(td, "indir"), "--name", name,
               "--outdir", outdir, "--workdir", os.path.join(td, "work")]
        # ComPC's complete.py shells out via os.system('python process.py ...')
        # with a BARE `python` -> must resolve to THIS env's interpreter (which has
        # cv2/rembg), not the system python. Prepend our bin dir to PATH.
        env = os.environ.copy()
        env["PATH"] = os.path.dirname(os.path.abspath(sys.executable)) + os.pathsep + env.get("PATH", "")
        if subprocess.run(cmd, cwd=compc, env=env).returncode != 0:
            return 2

        result_ply = os.path.join(outdir, name, fname)
        if not os.path.isfile(result_ply):
            sys.stderr.write(f"ComPC produced no result at {result_ply}\n")
            return 3
        completed = _read_ply_xyz(result_ply)

    np.save(a.output, completed.astype(np.float32))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
