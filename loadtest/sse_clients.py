#!/usr/bin/env python3
"""Concurrent SSE clients against the Soba job API. Standard library only.

k6 cannot read a text/event-stream body, so this probe does what
loadtest/sse_clients.js cannot: it opens N clients on /jobs/{id}/events (or
the legacy /events), reads the events for HOLD seconds, and checks that

  * every client under the per-IP cap gets 200 + text/event-stream,
  * every such client receives one `object_added` per scene object (the
    replay) — and, when it holds long enough, a `heartbeat`,
  * with --expect-cap M, the (M+1)-th concurrent client gets 429
    `too_many_streams` while the first M are still open.

It uploads the fixture archive itself (--archive) unless --job is given, and
deletes the job afterwards. Writes a JSON report to --out and exits 0 when
every check passed, 1 otherwise.

    python loadtest/sse_clients.py --server http://127.0.0.1:8000 \\
        --archive loadtest/fixtures/bundle_small.zip --clients 8 --expect-cap 8

Environment (each overridden by the flag): BASE_URL, API_KEY, RESULTS_DIR.
"""

from __future__ import annotations

import argparse
import http.client
import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


def _headers(api_key: str, extra: dict | None = None) -> dict:
    h = dict(extra or {})
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def _request(url: str, api_key: str, *, method: str = "GET", data: bytes | None = None,
             content_type: str | None = None, timeout: float = 60.0) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=_headers(api_key))
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, {"error": {"code": "http", "message": body.decode(errors="replace")}}


def upload_fixture(server: str, api_key: str, archive: Path, tier: int = 2,
                   timeout_s: float = 90.0) -> tuple[str, list[str]]:
    boundary = "----soba" + uuid.uuid4().hex
    out = io.BytesIO()
    out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"tier\"\r\n\r\n{tier}\r\n".encode())
    out.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"archive\"; "
              f"filename=\"{archive.name}\"\r\nContent-Type: application/zip\r\n\r\n".encode())
    out.write(archive.read_bytes())
    out.write(f"\r\n--{boundary}--\r\n".encode())
    for attempt in range(20):
        status, job = _request(f"{server}/api/jobs", api_key, method="POST", data=out.getvalue(),
                               content_type=f"multipart/form-data; boundary={boundary}")
        if status == 202:
            break
        if status == 429:
            time.sleep(1.0)
            continue
        raise SystemExit(f"upload rejected ({status}): {json.dumps(job)}")
    else:
        raise SystemExit("upload kept answering 429")
    jid = job["id"]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status, job = _request(f"{server}/api/jobs/{jid}", api_key)
        if status == 200 and job.get("state") in ("done", "failed"):
            break
        time.sleep(0.25)
    if job.get("state") != "done":
        raise SystemExit(f"job {jid} did not reach done: {job.get('status')}")
    _, scene = _request(f"{server}/jobs/{jid}/scene.json", api_key)
    return jid, [o["id"] for o in scene.get("objects", [])]


class SseClient(threading.Thread):
    """One /events consumer: connects, parses events for `hold` seconds."""

    def __init__(self, idx: int, server: str, path: str, api_key: str, hold: float,
                 ready: threading.Event | None = None) -> None:
        super().__init__(daemon=True, name=f"sse-{idx}")
        self.idx, self.server, self.path, self.api_key, self.hold = idx, server, path, api_key, hold
        self.ready = ready
        self.status: int | None = None
        self.content_type = ""
        self.error: str | None = None
        self.events: dict[str, int] = {}
        self.first_event_ms: float | None = None
        self.connect_ms: float | None = None
        self.object_ids: list[str] = []
        self.body_on_error = ""

    def run(self) -> None:
        u = urllib.parse.urlsplit(self.server)
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=max(2.0, self.hold + 5))
        t0 = time.monotonic()
        try:
            conn.request("GET", self.path, headers=_headers(self.api_key, {"Accept": "text/event-stream"}))
            resp = conn.getresponse()
            self.status = resp.status
            self.content_type = resp.getheader("content-type", "")
            self.connect_ms = (time.monotonic() - t0) * 1000
            if resp.status != 200:
                self.body_on_error = resp.read(4096).decode(errors="replace")
                if self.ready:
                    self.ready.set()
                return
            if self.ready:
                self.ready.set()
            deadline = t0 + self.hold
            event, data = None, []
            while time.monotonic() < deadline:
                conn.sock.settimeout(max(0.1, deadline - time.monotonic()))
                try:
                    line = resp.fp.readline()
                except (TimeoutError, OSError):
                    break
                if not line:
                    self.error = "server closed the stream"
                    break
                line = line.decode(errors="replace").rstrip("\r\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif line == "":
                    if event is not None:
                        if self.first_event_ms is None:
                            self.first_event_ms = (time.monotonic() - t0) * 1000
                        self.events[event] = self.events.get(event, 0) + 1
                        if event == "object_added":
                            try:
                                self.object_ids.append(json.loads("".join(data))["id"])
                            except (ValueError, KeyError):
                                pass
                    event, data = None, []
        except Exception as exc:  # noqa: BLE001  the report says what happened
            self.error = f"{type(exc).__name__}: {exc}"
            if self.ready:
                self.ready.set()
        finally:
            conn.close()

    def report(self) -> dict:
        return {"client": self.idx, "status": self.status, "content_type": self.content_type,
                "connect_ms": self.connect_ms, "first_event_ms": self.first_event_ms,
                "events": self.events, "object_ids": self.object_ids, "error": self.error,
                "body_on_error": self.body_on_error or None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default=os.environ.get("BASE_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--api-key", default=os.environ.get("API_KEY", ""))
    ap.add_argument("--archive", type=Path, default=Path(__file__).parent / "fixtures" / "bundle_small.zip")
    ap.add_argument("--job", help="use this finished job instead of uploading the fixture")
    ap.add_argument("--legacy", action="store_true", help="hit /events (out/scene_test) instead of a job")
    ap.add_argument("--clients", type=int, default=8, help="concurrent streams to open (default 8)")
    ap.add_argument("--hold", type=float, default=3.0, help="seconds each client reads (default 3)")
    ap.add_argument("--expect-cap", type=int, default=None,
                    help="SOBA_SSE_MAX_PER_IP in force: open this many, then assert one more gets 429")
    ap.add_argument("--expect-heartbeat", action="store_true",
                    help="require a heartbeat per client (needs --hold > 15)")
    ap.add_argument("--out", type=Path, default=None,
                    help=f"JSON report (default $RESULTS_DIR/sse_probe.json, RESULTS_DIR="
                         f"{os.environ.get('RESULTS_DIR', '<unset>')})")
    ap.add_argument("--keep-job", action="store_true")
    args = ap.parse_args()
    server = args.server.rstrip("/")

    jid, expected_objects = None, None
    if args.legacy:
        path = "/events"
        _, scene = _request(f"{server}/scene.json", args.api_key)
        expected_objects = [o["id"] for o in scene.get("objects", [])]
    elif args.job:
        jid = args.job
        path = f"/jobs/{jid}/events"
        _, scene = _request(f"{server}/jobs/{jid}/scene.json", args.api_key)
        expected_objects = [o["id"] for o in scene.get("objects", [])]
    else:
        jid, expected_objects = upload_fixture(server, args.api_key, args.archive)
        path = f"/jobs/{jid}/events"

    n = args.expect_cap if args.expect_cap is not None else args.clients
    ready = [threading.Event() for _ in range(n)]
    clients = [SseClient(i, server, path, args.api_key, args.hold, ready[i]) for i in range(n)]
    t_start = time.monotonic()
    for c in clients:
        c.start()
    for ev in ready:
        ev.wait(timeout=args.hold + 10)
    extra = None
    if args.expect_cap is not None:
        # all N are connected (or refused) now; one more must be refused
        extra = SseClient(n, server, path, args.api_key, 1.0)
        extra.start()
        extra.join()
    for c in clients:
        c.join()
    wall_s = time.monotonic() - t_start

    failures: list[str] = []
    for c in clients:
        r = c.report()
        if r["status"] != 200:
            failures.append(f"client {c.idx}: status {r['status']} ({r['error'] or r['body_on_error']})")
            continue
        if "text/event-stream" not in r["content_type"]:
            failures.append(f"client {c.idx}: content-type {r['content_type']!r}")
        if r["error"]:
            failures.append(f"client {c.idx}: {r['error']}")
        if sorted(r["object_ids"]) != sorted(expected_objects or []):
            failures.append(f"client {c.idx}: object_added {r['object_ids']} != scene {expected_objects}")
        if args.expect_heartbeat and not r["events"].get("heartbeat"):
            failures.append(f"client {c.idx}: no heartbeat within {args.hold}s")
    cap_result = None
    if extra is not None:
        r = extra.report()
        cap_result = r
        code = None
        try:
            code = json.loads(r["body_on_error"] or "{}").get("error", {}).get("code")
        except ValueError:
            pass
        if r["status"] != 429 or code != "too_many_streams":
            failures.append(f"cap probe: expected 429 too_many_streams, got {r['status']} {code!r}")

    if jid and not args.keep_job and not args.job:
        _request(f"{server}/api/jobs/{jid}", args.api_key, method="DELETE")

    report = {
        "server": server, "path": path, "mode": "keyed" if args.api_key else "open",
        "clients": n, "hold_s": args.hold, "wall_s": round(wall_s, 3),
        "expected_objects": expected_objects, "expect_cap": args.expect_cap,
        "ok": not failures, "failures": failures,
        "connect_ms_max": max((c.connect_ms or 0) for c in clients),
        "first_event_ms_max": max((c.first_event_ms or 0) for c in clients),
        "clients_detail": [c.report() for c in clients], "cap_probe": cap_result,
    }
    out = args.out
    if out is None and os.environ.get("RESULTS_DIR"):
        out = Path(os.environ["RESULTS_DIR"]) / "sse_probe.json"
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1))
    ok_clients = sum(1 for c in clients if c.status == 200)
    print(f"sse_clients: {ok_clients}/{n} streams 200, connect max {report['connect_ms_max']:.0f} ms, "
          f"first event max {report['first_event_ms_max']:.0f} ms, hold {args.hold}s"
          + (f", cap probe -> {cap_result['status']}" if cap_result else ""))
    for f in failures:
        print("  FAIL", f)
    print("  all checks passed" if not failures else f"  {len(failures)} check(s) failed")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
