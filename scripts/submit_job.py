#!/usr/bin/env python
"""Submit a PerceptionBundle (or TUM sequence) directory to a running Soba
server as a job and poll it to a terminal state. Standard library only.

    python scripts/serve.py --scene out/scene_test            # in one shell
    python scripts/submit_job.py --bundle bundles/office_3 --tier 2
    python scripts/submit_job.py --archive office_3.zip --server http://127.0.0.1:8000

Prints the job id, then one line per status change, and finally the viewer
URL (`<server>/jobs/<id>/`). Exit 0 on `done`, 1 on `failed`, 2 on a
rejected upload (the server's JSON error envelope is printed).

`--bundle DIR` is zipped in memory (a directory named after DIR at the
archive root); `--archive FILE` sends an existing zip / tar.gz / tar as-is.
The worker mode is the server's (mock on a CPU box, real on the pod); this
script only drives the API.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path


def zip_dir(src: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if p.is_file() and not p.name.startswith("."):
                rel = p.relative_to(src).as_posix()
                if rel.split("/", 1)[0] in (".gate_cache", ".gen_cache"):
                    continue
                zf.write(p, f"{src.name}/{rel}")
    return buf.getvalue()


def multipart(fields: dict[str, str], file_field: str, filename: str, data: bytes,
              content_type: str) -> tuple[bytes, str]:
    boundary = "----soba" + uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
                  .encode())
    out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
              f"filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n".encode())
    out.write(data)
    out.write(f"\r\n--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def request(url: str, *, data: bytes | None = None, content_type: str | None = None,
            method: str = "GET") -> tuple[int, dict]:
    req = urllib.request.Request(url, data=data, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, {"error": {"code": "http", "message": body.decode(errors="replace")}}


def main() -> int:
    ap = argparse.ArgumentParser(description="submit a bundle as a Soba job and wait for it")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--bundle", type=Path, help="PerceptionBundle (or TUM sequence) directory")
    src.add_argument("--archive", type=Path, help="existing zip / tar.gz / tar archive")
    ap.add_argument("--tier", type=int, default=2, choices=[1, 2, 3, 4])
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--poll", type=float, default=1.0, help="status poll interval (s)")
    ap.add_argument("--timeout", type=float, default=6 * 3600, help="give up after (s)")
    ap.add_argument("--no-wait", action="store_true", help="print the job id and exit")
    args = ap.parse_args()
    server = args.server.rstrip("/")

    if args.bundle:
        data = zip_dir(args.bundle.resolve())
        filename, ctype = args.bundle.resolve().name + ".zip", "application/zip"
    else:
        data = args.archive.read_bytes()
        filename = args.archive.name
        ctype = {"zip": "application/zip", "gz": "application/gzip", "tgz": "application/gzip",
                 "tar": "application/x-tar"}.get(filename.rsplit(".", 1)[-1], "application/octet-stream")

    body, ct = multipart({"tier": str(args.tier)}, "archive", filename, data, ctype)
    print(f"uploading {filename} ({len(data) / 1e6:.1f} MB, tier {args.tier}) to {server}/api/jobs")
    status, job = request(f"{server}/api/jobs", data=body, content_type=ct, method="POST")
    if status != 202:
        print(f"rejected ({status}): {json.dumps(job)}", file=sys.stderr)
        return 2
    jid = job["id"]
    print(f"job {jid}  status {job['status']}")
    if args.no_wait:
        print(f"{server}/api/jobs/{jid}")
        return 0

    last = job["status"]
    t0 = time.time()
    while time.time() - t0 < args.timeout:
        time.sleep(args.poll)
        status, job = request(f"{server}/api/jobs/{jid}")
        if status != 200:
            print(f"status poll failed ({status}): {json.dumps(job)}", file=sys.stderr)
            return 1
        if job["status"] != last:
            last = job["status"]
            print(f"  {time.time() - t0:7.1f}s  {last}" + (f"  {job['error']}" if job.get("error") else ""))
        if job["state"] in ("done", "failed"):
            break
    else:
        print("timed out waiting for the job", file=sys.stderr)
        return 1

    print(f"viewer: {server}/jobs/{jid}/")
    return 0 if job["state"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
