#!/usr/bin/env python3
"""Turn one loadtest/results/<stamp>/ directory into the Markdown table that
docs/loadtest.md records. Standard library only.

    python loadtest/summarize.py loadtest/results/20260912T120000Z

Reads every k6 summary (`*.json` written by lib.js handleSummary), the SSE
probe report (`sse_probe.json`) and `run.json` (environment written by
run.sh), prints Markdown to stdout and also writes `summary.md` next to them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DISCLAIMER = ("**API-layer throughput with a mock worker on CPU under Docker Desktop on "
              "WSL2; not pipeline or GPU throughput; never cite as such** (CLAUDE.md invariant 7).")


def _p(values: dict, key: str) -> str:
    v = values.get(key)
    return "-" if v is None else f"{v:.1f}"


def k6_row(doc: dict) -> list[str]:
    m = doc["metrics"]
    env = doc.get("env", {})
    reqs = m.get("http_reqs", {}).get("values", {})
    failed = m.get("http_req_failed", {}).get("values", {}).get("rate", 0.0)
    rl = m.get("rate_limited", {}).get("values", {}).get("count", 0)
    dur = m.get("http_req_duration", {}).get("values", {})
    per_name = []
    for name in ("upload", "status", "list", "scene", "mesh", "hull", "delete"):
        k = f"http_req_duration{{name:{name}}}"
        if k in m:
            v = m[k]["values"]
            per_name.append(f"{name} {v.get('p(95)', 0):.0f}")
    extras = []
    for key, label in (("job_wait_ms", "202→done"), ("job_turnaround_ms", "upload→done"),
                       ("scene_fetch_ms", "page load")):
        if key in m:
            extras.append(f"{label} p95 {m[key]['values'].get('p(95)', 0):.0f} ms")
    for key in ("upload_ok", "job_done"):
        if key in m:
            extras.append(f"{key} {m[key]['values'].get('rate', 0) * 100:.0f}%")
    for key in ("sse_held", "sse_rejected", "sse_ended", "upload_retries", "upload_gave_up"):
        if key in m and m[key]["values"].get("count"):
            extras.append(f"{key} {int(m[key]['values']['count'])}")
    knobs = " ".join(f"{k}={v}" for k, v in sorted(env.items()) if k not in ("API_KEY", "LABEL") and v)
    thr = "pass" if not doc.get("thresholds_failed") else f"**{doc['thresholds_failed']} FAILED**"
    return [
        doc.get("script", "?"), doc.get("mode", "?"), knobs or "-",
        f"{int(reqs.get('count', 0))}", f"{reqs.get('rate', 0):.1f}",
        _p(dur, "med"), _p(dur, "p(95)"), _p(dur, "max"),
        f"{failed * 100:.2f}%", str(int(rl)),
        "; ".join(per_name) or "-", "; ".join(extras) or "-", thr,
    ]


HEAD = ["script", "mode", "knobs", "requests", "rps", "med ms", "p95 ms", "max ms",
        "failed", "429s", "p95 by route (ms)", "custom", "thresholds"]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    d = Path(argv[1])
    run = json.loads((d / "run.json").read_text()) if (d / "run.json").is_file() else {}
    rows = []
    for p in sorted(d.glob("*.json")):
        if p.name in ("run.json", "sse_probe.json") or p.name.startswith("sse_probe"):
            continue
        try:
            doc = json.loads(p.read_text())
        except ValueError:
            continue
        if "metrics" in doc:
            rows.append(k6_row(doc))
    lines = [f"## Load-test run {d.name}", "", DISCLAIMER, ""]
    if run:
        lines.append("| setting | value |")
        lines.append("|---|---|")
        for k, v in run.items():
            if isinstance(v, (dict, list)):
                v = json.dumps(v)
            lines.append(f"| {k} | {v} |")
        lines.append("")
    lines.append("| " + " | ".join(HEAD) + " |")
    lines.append("|" + "---|" * len(HEAD))
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    probes = sorted(d.glob("sse_probe*.json"))
    if probes:
        lines += ["", "| SSE probe | mode | clients | hold s | 200s | connect max ms | first event max ms | cap probe | result |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for p in probes:
            s = json.loads(p.read_text())
            ok = sum(1 for c in s["clients_detail"] if c["status"] == 200)
            cap = s.get("cap_probe")
            cap_s = f"{cap['status']}" if cap else "-"
            res = "pass" if s["ok"] else "**FAIL** " + "; ".join(s["failures"])
            lines.append(f"| {p.stem} | {s['mode']} | {s['clients']} | {s['hold_s']} | {ok}/{s['clients']} | "
                         f"{s['connect_ms_max']:.0f} | {s['first_event_ms_max']:.0f} | {cap_s} | {res} |")
    text = "\n".join(lines) + "\n"
    (d / "summary.md").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
