"""Job ingest + status routes, and the resolver for job-scoped scene routes.

  POST   /api/jobs        multipart: `archive` (zip | tar.gz | tar of a
                          PerceptionBundle or TUM sequence), `tier` (1-4, default 2)
                          -> 202 job JSON. 400 no archive / 413 too large /
                          415 not an archive / 422 invalid layout or tier.
  GET    /api/jobs        list, newest first
  GET    /api/jobs/{id}   job JSON (status: queued | running:<stage> | done | failed)
  DELETE /api/jobs/{id}   204; removes the record and the whole job dir

Validation + extraction happen synchronously in the upload request (in a
thread) so a bad archive is answered with a 4xx instead of a failed job.
"""

from __future__ import annotations

import secrets
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..errors import api_error
from ..jobs.archive import (
    DEFAULT_MAX_EXTRACTED_BYTES,
    UploadError,
    UploadTooLarge,
    ext_for,
    validate_and_extract,
)
from ..jobs.paths import JOB_ID_RE, JobPaths
from ..jobs.queue import JobQueue
from ..jobs.store import JobRecord, JobStore
from ..jobs.worker_local import LocalWorker
from ..security.upload_validation import capped_receive

DEFAULT_MAX_UPLOAD_BYTES = 2 * 1024 ** 3  # 2 GiB
_CHUNK = 1 << 20
_MULTIPART_SLACK = 64 * 1024  # boundaries + the tier field on top of the archive
TIERS = (1, 2, 3, 4)


@dataclass
class JobsContext:
    """Everything the job routes and the worker share; exposed as app.state.jobs."""

    jobs_dir: Path
    store: JobStore
    queue: JobQueue
    worker: LocalWorker | None = None
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    max_extracted_bytes: int = DEFAULT_MAX_EXTRACTED_BYTES
    extra: dict = field(default_factory=dict)

    def paths(self, job_id: str) -> JobPaths:
        return JobPaths(self.jobs_dir, job_id)

    def get(self, job_id: str) -> JobRecord | None:
        if not JOB_ID_RE.match(job_id or ""):
            return None
        return self.store.get(job_id)


def _unknown(job_id: str) -> JSONResponse:
    return api_error(404, "unknown_job", f"no job {job_id!r}")


def _rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def job_scene_resolver(ctx: JobsContext):
    """resolve(request) for api.routes.scene: the job's scene dir or a 404."""

    def resolve(request: Request):
        jid = request.path_params["job_id"]
        rec = ctx.get(jid)
        if rec is None:
            return _unknown(jid)
        return ctx.paths(jid).scene_dir

    return resolve


def job_watcher(ctx: JobsContext):
    def watch(request: Request):
        return ctx.get(request.path_params["job_id"])

    return watch


def jobs_routes(ctx: JobsContext) -> list[Route]:
    async def create_job(request: Request):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > ctx.max_upload_bytes:
            return api_error(413, "upload_too_large",
                             f"upload exceeds {ctx.max_upload_bytes // 1024 ** 2} MiB")
        # Streaming cap: the multipart parser pulls the body through this
        # receive, so an oversized (or chunked, Content-Length-less) upload is
        # cut off as it arrives, not after it was spooled to disk (audit G4).
        request = Request(request.scope,
                          capped_receive(request.receive,
                                         ctx.max_upload_bytes + _MULTIPART_SLACK))
        try:
            form = await request.form()
        except UploadTooLarge as exc:
            return api_error(exc.status, exc.code, exc.message)
        except AssertionError as exc:  # starlette: python-multipart missing
            return api_error(500, "multipart_unavailable",
                             f"server cannot parse multipart uploads: {exc}")
        except Exception as exc:  # noqa: BLE001  any parser error = malformed body
            return api_error(400, "bad_multipart", f"could not parse multipart body: {exc}")

        upload = form.get("archive")
        if not isinstance(upload, UploadFile):
            return api_error(400, "missing_archive",
                             "multipart field 'archive' (a zip/tar.gz file) is required")
        tier_raw = form.get("tier", "2")
        try:
            tier = int(str(tier_raw))
            if tier not in TIERS:
                raise ValueError
        except ValueError:
            return api_error(422, "bad_tier", f"tier must be one of {list(TIERS)}, got {tier_raw!r}")

        job_id = secrets.token_hex(8)
        paths = ctx.paths(job_id)
        paths.root.mkdir(parents=True, exist_ok=False)
        tmp = paths.root / "upload.bin"
        try:
            await _stream_to_disk(upload, tmp, ctx.max_upload_bytes)
            res = await run_in_threadpool(
                validate_and_extract, tmp, paths.extracted_dir,
                max_extracted_bytes=ctx.max_extracted_bytes)
            tmp.rename(paths.upload_path(ext_for(res.archive_format)))
        except UploadError as exc:
            await run_in_threadpool(_rmtree, paths.root)
            return api_error(exc.status, exc.code, exc.message)
        finally:
            await upload.close()

        rec = ctx.store.create(JobRecord(
            id=job_id, tier=tier, source_format=res.format,
            upload_name=upload.filename, bundle_dir=str(res.root)))
        ctx.queue.put(job_id)
        return JSONResponse(rec.to_dict(), status_code=202)

    async def list_jobs(request: Request):
        return JSONResponse({"jobs": [r.to_dict() for r in ctx.store.list()]})

    async def get_job(request: Request):
        jid = request.path_params["job_id"]
        rec = ctx.get(jid)
        return JSONResponse(rec.to_dict()) if rec else _unknown(jid)

    async def delete_job(request: Request):
        jid = request.path_params["job_id"]
        rec = ctx.get(jid)
        if rec is None:
            return _unknown(jid)
        ctx.store.delete(jid)
        await run_in_threadpool(_rmtree, ctx.paths(jid).root)
        return Response(status_code=204)

    return [
        Route("/api/jobs", create_job, methods=["POST"]),
        Route("/api/jobs", list_jobs, methods=["GET"]),
        Route("/api/jobs/{job_id}", get_job, methods=["GET"]),
        Route("/api/jobs/{job_id}", delete_job, methods=["DELETE"]),
    ]


async def _stream_to_disk(upload: UploadFile, target: Path, max_bytes: int) -> int:
    import aiofiles

    size = 0
    async with aiofiles.open(target, "wb") as fh:
        while True:
            chunk = await upload.read(_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise UploadTooLarge(f"upload exceeds {max_bytes // 1024 ** 2} MiB")
            await fh.write(chunk)
    return size
