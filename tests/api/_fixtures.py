"""Shared fixtures for the job API tests.

Everything here is synthetic and CPU-only: a fake PerceptionBundle (manifest +
intrinsics + frame dirs with placeholder bytes; nothing decodes them), a fake
one-object scene in the on-disk layout the routes remap, and an app built
with `create_app(..., start_worker=False)` so tests drive the worker by hand
(`app.state.jobs.worker.run_once()`) instead of racing a thread. No open3d,
no cv2.
"""

from __future__ import annotations

import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from api.app import create_app

# 12-byte GLB header (magic, version 2, length) — enough for the byte checks.
FAKE_GLB = b"glTF" + (2).to_bytes(4, "little") + (12).to_bytes(4, "little")
OBJ_ID = "crate_00"


@pytest.fixture(autouse=True)
def _no_sse_delay(monkeypatch):
    monkeypatch.setenv("SOBA_SSE_DELAY", "0")


# --- synthetic inputs ---------------------------------------------------------
def make_bundle(root: Path, frames: int = 2) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps(
        {"session_id": "test", "fps": 30.0, "frame_count": frames, "source": "tum"}))
    (root / "intrinsics.json").write_text(json.dumps(
        {"fx": 525.0, "fy": 525.0, "cx": 319.5, "cy": 239.5}))
    for i in range(frames):
        d = root / "frames" / f"{i:05d}"
        d.mkdir(parents=True)
        (d / "rgb.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        (d / "depth.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (d / "objects.json").write_text("[]")
    return root


def make_tum_sequence(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "rgb.txt").write_text("# rgb\n1.0 rgb/1.png\n")
    (root / "depth.txt").write_text("# depth\n1.0 depth/1.png\n")
    (root / "rgb").mkdir()
    (root / "depth").mkdir()
    (root / "rgb" / "1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (root / "depth" / "1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    return root


def zip_dir(src: Path, *, top: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                rel = p.relative_to(src).as_posix()
                zf.write(p, f"{top}/{rel}" if top else rel)
    return buf.getvalue()


def tar_dir(src: Path, *, gz: bool = True, top: str | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz" if gz else "w") as tf:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                rel = p.relative_to(src).as_posix()
                tf.add(p, arcname=f"{top}/{rel}" if top else rel)
    return buf.getvalue()


def make_fixture_scene(root: Path, oid: str = OBJ_ID) -> dict:
    """One object with a mesh and one hull, in the layout the routes remap."""
    scene = {
        "version": "2.0",
        "world": {"gravity": [0.0, -9.81, 0.0], "up_axis": "y", "unit": "meters"},
        "ground": {"type": "plane", "normal": [0.0, 1.0, 0.0], "y": 0.0,
                   "material": {"friction": 0.85, "restitution": 0.1}},
        "camera_pose": {"translation": [0.0, 1.2, 2.0],
                        "rotation_quat": [0.0, 0.0, 0.0, 1.0]},
        "objects": [{
            "id": oid, "class": "crate",
            "transform": {"translation": [0.0, 0.205, 0.0],
                          "rotation_quat": [0.0, 0.0, 0.0, 1.0], "scale": 1.0},
            "mesh": "meshes/crate_00.glb",
            "collider": {"shape": "convex_hulls", "hull_paths": [f"hulls/{oid}_0.glb"]},
            "physics": {"mass_kg": 3.5, "material": "wood", "friction": 0.6,
                        "restitution": 0.2},
        }],
    }
    (root / "objects" / oid / "hulls").mkdir(parents=True, exist_ok=True)
    (root / "scene.json").write_text(json.dumps(scene, indent=2))
    (root / "objects" / oid / "mesh.glb").write_bytes(FAKE_GLB + b"mesh")
    (root / "objects" / oid / "hulls" / f"{oid}_0.glb").write_bytes(FAKE_GLB + b"hull0")
    (root / "eval.json").write_text(json.dumps({"schema": 1, "room": "synthetic",
                                                 "score": {"value": 87.5}}))
    return scene


# --- app fixtures -------------------------------------------------------------
@pytest.fixture
def bundle_zip(tmp_path) -> bytes:
    return zip_dir(make_bundle(tmp_path / "bundle_src"))


@pytest.fixture
def fixture_scene(tmp_path) -> Path:
    d = tmp_path / "fixture_scene"
    d.mkdir()
    make_fixture_scene(d)
    return d


@pytest.fixture
def root_scene(tmp_path) -> Path:
    """The legacy single-scene dir served at `/` (distinct object id)."""
    d = tmp_path / "root_scene"
    d.mkdir()
    make_fixture_scene(d, oid="root_table_01")
    return d


@pytest.fixture
def frontend_dir(tmp_path) -> Path:
    d = tmp_path / "frontend"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>soba viewer</title>")
    (d / "assets" / "app.js").write_text("// bundle")
    return d


@pytest.fixture
def jobs_dir(tmp_path) -> Path:
    return tmp_path / "jobs"


@pytest.fixture
def app(root_scene, frontend_dir, jobs_dir, fixture_scene):
    """Worker NOT started: tests call app.state.jobs.worker.run_once()."""
    return create_app(root_scene, frontend_dir, jobs_dir=jobs_dir, worker_mode="mock",
                      fixture_scene=fixture_scene, start_worker=False)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


def post_archive(client: TestClient, data: bytes, *, name: str = "bundle.zip",
                 tier: str | None = "2", content_type: str = "application/zip"):
    form = {} if tier is None else {"tier": tier}
    return client.post("/api/jobs", files={"archive": (name, data, content_type)}, data=form)


def run_worker(app, n: int = 1) -> None:
    for _ in range(n):
        assert app.state.jobs.worker.run_once(timeout=0.0), "no job was queued"
