"""Local worker: consumes the JobQueue and drives one job through its stages.

    queued -> running:validating  PerceptionBundle.open on the extracted root
           -> running:converting  (TUM uploads only) TUMReader.to_bundle
           -> running:assembling  see modes below
           -> done | failed

Modes (``SOBA_WORKER_MODE``):

* ``mock`` (default, the only mode that completes on a CPU-only box): copies
  ``fixture_scene`` (``SOBA_FIXTURE_SCENE``, else ``out/scene_test`` when it
  exists) into the job's scene dir, or writes a schema-valid empty scene.
  ``SOBA_MOCK_DELAY_S`` slows the assembling stage for demos.
* ``real``: ``python scripts/run_assemble.py --bundle B --tier T --out S
  --no-eval`` as a subprocess (needs the recon extra + a GPU for tiers >= 2;
  the pod). Non-zero exit -> failed with the log tail.
* ``gate-only``: same subprocess with ``--gate-only`` (routing printout, no
  meshes); the job ends ``done`` with an empty scene.

The worker runs in a daemon thread inside the API process by default
(``SOBA_WORKER_INPROC=1``). Run the API with ONE uvicorn worker in that
configuration; with several, each process would start its own consumer. An
external consumer (runpod-orchestration-agent's ``src/orchestration/worker.py``)
replaces this thread by taking the same JobQueue.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from perception.bundle import PerceptionBundle

from . import store as _store
from .paths import JobPaths
from .queue import JobQueue
from .store import JobStore

log = logging.getLogger("api.worker")

REPO_ROOT = Path(__file__).resolve().parents[3]
MODES = ("mock", "real", "gate-only")

EMPTY_SCENE = {
    "version": "2.0",
    "world": {"gravity": [0.0, -9.81, 0.0], "up_axis": "y", "unit": "meters"},
    "ground": {"type": "plane", "normal": [0.0, 1.0, 0.0], "y": 0.0,
               "material": {"friction": 0.85, "restitution": 0.1}},
    "camera_pose": {"translation": [0.0, 1.2, 0.0], "rotation_quat": [0.0, 0.0, 0.0, 1.0]},
    "objects": [],
}


def default_fixture_scene() -> Path | None:
    env = os.environ.get("SOBA_FIXTURE_SCENE")
    if env:
        return Path(env)
    cand = REPO_ROOT / "out" / "scene_test"
    return cand if (cand / "scene.json").is_file() else None


def write_empty_scene(scene_dir: Path) -> None:
    # Validated straight against spec/scene.schema.json: importing
    # scene.schema would pull in the whole `scene` package (open3d).
    from jsonschema import Draft202012Validator

    schema = json.loads((REPO_ROOT / "spec" / "scene.schema.json").read_text())
    Draft202012Validator(schema).validate(EMPTY_SCENE)  # never break the contract
    scene_dir.mkdir(parents=True, exist_ok=True)
    (scene_dir / "scene.json").write_text(json.dumps(EMPTY_SCENE, indent=2))


class LocalWorker:
    def __init__(
        self,
        store: JobStore,
        queue: JobQueue,
        jobs_dir: Path | str,
        *,
        mode: str = "mock",
        fixture_scene: Path | str | None = None,
        python: str | None = None,
        repo_root: Path | str = REPO_ROOT,
        mock_delay_s: float = 0.0,
        subprocess_timeout_s: float = 4 * 3600,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"worker mode must be one of {MODES}, got {mode!r}")
        self.store, self.queue = store, queue
        self.jobs_dir = Path(jobs_dir)
        self.mode = mode
        self.fixture_scene = Path(fixture_scene) if fixture_scene else None
        self.python = python or sys.executable
        self.repo_root = Path(repo_root)
        self.mock_delay_s = mock_delay_s
        self.subprocess_timeout_s = subprocess_timeout_s
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="soba-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_once(timeout=0.5)

    def run_once(self, timeout: float | None = 0.0) -> bool:
        """Process at most one queued job; True if one was taken."""
        job_id = self.queue.get(timeout=timeout)
        if job_id is None:
            return False
        self.process(job_id)
        return True

    # -- one job ------------------------------------------------------------
    def _set(self, job_id: str, state: str, stage: str | None = None,
             error: str | None = None) -> bool:
        try:
            self.store.set_state(job_id, state, stage=stage, error=error)
            return True
        except _store.UnknownJob:
            log.info("job %s vanished (deleted?) while %s", job_id, state)
        except _store.InvalidTransition as exc:
            log.warning("job %s: refused transition %s", job_id, exc)
        return False

    def process(self, job_id: str) -> None:
        rec = self.store.get(job_id)
        if rec is None or rec.terminal:
            return
        paths = JobPaths(self.jobs_dir, job_id)
        try:
            if not self._set(job_id, _store.RUNNING, "validating"):
                return
            root = Path(rec.bundle_dir) if rec.bundle_dir else paths.extracted_dir
            if rec.source_format == "tum":
                self._set(job_id, _store.RUNNING, "converting")
                root = self._convert_tum(root, paths.bundle_dir)
            PerceptionBundle.open(root)
            self._set(job_id, _store.RUNNING, "assembling")
            self._assemble(rec, root, paths)
            self._set(job_id, _store.DONE)
        except Exception as exc:  # any failure is the job's, never the worker's
            log.exception("job %s failed", job_id)
            self._set(job_id, _store.FAILED, error=f"{type(exc).__name__}: {exc}"[:2000])

    @staticmethod
    def _convert_tum(seq_dir: Path, bundle_dir: Path) -> Path:
        from perception.dataset_reader import TUMReader

        max_frames = os.environ.get("SOBA_TUM_MAX_FRAMES")
        TUMReader(seq_dir).to_bundle(
            bundle_dir, max_frames=int(max_frames) if max_frames else None)
        return bundle_dir

    def _assemble(self, rec: _store.JobRecord, bundle_root: Path, paths: JobPaths) -> None:
        scene_dir = paths.scene_dir
        if self.mode == "mock":
            if self.mock_delay_s:
                self._stop.wait(self.mock_delay_s)
            fixture = self.fixture_scene or default_fixture_scene()
            if fixture and (fixture / "scene.json").is_file():
                shutil.copytree(fixture, scene_dir, dirs_exist_ok=True)
            else:
                write_empty_scene(scene_dir)
            return

        cmd = [self.python, str(self.repo_root / "scripts" / "run_assemble.py"),
               "--bundle", str(bundle_root), "--tier", str(rec.tier),
               "--out", str(scene_dir), "--no-eval"]
        if self.mode == "gate-only":
            cmd.append("--gate-only")
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (str(self.repo_root / "src"), env.get("PYTHONPATH")) if p)
        scene_dir.mkdir(parents=True, exist_ok=True)
        with paths.worker_log.open("ab") as logfh:
            logfh.write(("$ " + " ".join(cmd) + "\n").encode())
            logfh.flush()
            proc = subprocess.run(cmd, cwd=self.repo_root, env=env, stdout=logfh,
                                  stderr=subprocess.STDOUT, check=False,
                                  timeout=self.subprocess_timeout_s)
        if proc.returncode != 0:
            tail = _tail(paths.worker_log)
            raise RuntimeError(f"run_assemble.py exited {proc.returncode}: {tail}")
        if not (scene_dir / "scene.json").is_file():
            # --gate-only, or "nothing to assemble": still hand the viewer a
            # valid (empty) scene rather than the partial-safe fallback.
            write_empty_scene(scene_dir)


def _tail(path: Path, n: int = 5) -> str:
    try:
        lines = path.read_text(errors="replace").strip().splitlines()
    except OSError:
        return ""
    return " | ".join(lines[-n:])
