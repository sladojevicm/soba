"""On-disk layout of one job under the jobs dir.

    <jobs_dir>/jobs.sqlite              the SqliteJobStore
    <jobs_dir>/<id>/upload.<ext>        the archive as received
    <jobs_dir>/<id>/extracted/          the archive unpacked (bundle or TUM root inside)
    <jobs_dir>/<id>/bundle/             TUM -> PerceptionBundle conversion output
    <jobs_dir>/<id>/scene/              run_assemble.py output (scene.json, objects/)
    <jobs_dir>/<id>/worker.log          subprocess log (real / gate-only modes)

There is no retention policy yet: nothing under <jobs_dir> is ever removed
except by DELETE /api/jobs/{id} (STATUS.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

JOB_ID_RE = re.compile(r"^[0-9a-f]{16}$")


@dataclass(frozen=True)
class JobPaths:
    jobs_dir: Path
    job_id: str

    @property
    def root(self) -> Path:
        return self.jobs_dir / self.job_id

    def upload_path(self, ext: str) -> Path:
        return self.root / f"upload.{ext}"

    @property
    def extracted_dir(self) -> Path:
        return self.root / "extracted"

    @property
    def bundle_dir(self) -> Path:
        return self.root / "bundle"

    @property
    def scene_dir(self) -> Path:
        return self.root / "scene"

    @property
    def worker_log(self) -> Path:
        return self.root / "worker.log"
