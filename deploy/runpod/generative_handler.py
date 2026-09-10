#!/usr/bin/env python
"""RunPod SERVERLESS handler for the generative + completion bands.

This is the pod-side counterpart of reconstruction.generative.RunPodEngine. The
README's "separate, optional RunPod *serverless* endpoint for TripoSG/Hunyuan3D"
IS this file. One endpoint serves BOTH image-to-3D models (TripoSG for tiers 1-2,
Hunyuan3D 2.1 for tiers 3-4, fix K1) and the learned completion model, selected
per request by `input.model` — so there is no second deployment to manage.

It REUSES the exact model code the local path uses (LocalGpuEngine._run_gen /
_run_completion): the pod is just "LocalGpuEngine on a big GPU behind HTTP", so
Hunyuan3D 2.1 (~10 GB shape) and its 24 GB+ texture pass run here where the VRAM
exists, while an 8 GB host keeps TripoSG. No model logic is duplicated.

CONTRACT (mirrors RunPodEngine._build_input / _decode_mesh exactly):
  input (regenerate): {mode:"regenerate", model:"triposg"|"hunyuan3d",
                       image_b64:<b64 jpg/png>, coco_class:str}
  input (complete):   {mode:"complete", model:"pointr"|..., cloud_npy_b64:<b64 .npy>,
                       coco_class:str, image_b64?:<b64>}
  output:             {mesh_b64:<b64>, format:"obj"}
OBJ on purpose: Open3D cannot read GLB back, so the round-trip never uses GLB.

Deploy: package this with the repo on a RunPod serverless worker whose Docker
image has the model weights (setup_triposg.sh / setup_hunyuan3d.sh). Entry point:
`python generative_handler.py` -> runpod.serverless.start.
"""
from __future__ import annotations

import base64
import io
import os
import sys
import tempfile

# The repo's src/ must be importable so we can reuse the model adapters.
_REPO_SRC = os.environ.get(
    "SOBA_SRC",
    os.path.join(os.path.dirname(__file__), "..", "..", "src"),
)
if _REPO_SRC not in sys.path:
    sys.path.insert(0, os.path.abspath(_REPO_SRC))

# One cached engine per worker process (weights load once, on first request).
_ENGINE = {}


def _engine(gen_model: str, completion_model: str):
    """A cached LocalGpuEngine keyed by (gen_model, completion_model). A worker
    typically serves one model, so this is a 1-entry cache in practice."""
    from reconstruction.generative import LocalGpuEngine

    key = (gen_model, completion_model)
    if key not in _ENGINE:
        _ENGINE[key] = LocalGpuEngine(
            gen_model=gen_model, completion_model=completion_model)
    return _ENGINE[key]


def _mesh_to_obj_b64(mesh) -> str:
    """Serialise an open3d TriangleMesh to base64 OBJ text."""
    import open3d as o3d

    with tempfile.NamedTemporaryFile(suffix=".obj", delete=False) as tf:
        path = tf.name
    try:
        o3d.io.write_triangle_mesh(path, mesh, write_vertex_colors=True)
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    finally:
        os.unlink(path)


def run(job_input: dict) -> dict:
    """Core dispatch — pure function of the `input` dict, unit-testable off-pod."""
    mode = job_input.get("mode", "regenerate")
    model = job_input.get("model", "triposg")
    coco_class = job_input.get("coco_class", "obj")

    if mode == "regenerate":
        img_b64 = job_input.get("image_b64")
        if not img_b64:
            raise ValueError("regenerate requires image_b64")
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            tf.write(base64.b64decode(img_b64))
            crop_path = tf.name
        try:
            eng = _engine(model, "pointr")
            mesh = eng._run_gen(crop_path=crop_path, coco_class=coco_class)
        finally:
            os.unlink(crop_path)
        return {"mesh_b64": _mesh_to_obj_b64(mesh), "format": "obj"}

    if mode == "complete":
        import numpy as np
        import open3d as o3d

        cloud_b64 = job_input.get("cloud_npy_b64")
        if not cloud_b64:
            raise ValueError("complete requires cloud_npy_b64")
        cloud = np.load(io.BytesIO(base64.b64decode(cloud_b64)))
        partial = o3d.geometry.TriangleMesh()
        partial.vertices = o3d.utility.Vector3dVector(np.asarray(cloud, dtype=np.float64))
        eng = _engine("triposg", model)
        mesh = eng._run_completion(mesh=partial, cloud=cloud, coco_class=coco_class)
        return {"mesh_b64": _mesh_to_obj_b64(mesh), "format": "obj"}

    raise ValueError(f"unknown mode {mode!r}")


def handler(job: dict) -> dict:
    """RunPod serverless entry: job -> {"output": ...} or {"error": ...}."""
    try:
        return run(job.get("input", {}))
    except Exception as e:  # surface the failure as a RunPod job error
        return {"error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    try:
        import runpod

        runpod.serverless.start({"handler": handler})
    except ImportError:
        sys.stderr.write("runpod SDK not installed; this entrypoint runs on a "
                         "RunPod serverless worker (pip install runpod).\n")
        raise
