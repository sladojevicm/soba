#!/usr/bin/env python
"""Regenerate BENCHMARK.md §3 (Replica room-accuracy tables) from eval.json files.

Rewrites ONLY the per-room tables between the <!-- SECTION3:START/END --> markers;
the prose between the START marker and the first `### <room>` heading is kept
verbatim. For every (room, tier) cell the row comes from
<out-root>/scene_<room>_t<tier>/eval.json when that file exists, otherwise the
row already in BENCHMARK.md is kept (so tiers built on hardware that no longer
exists — e.g. the tier-1/2 runs whose out/ dirs died with the old pod — survive
a rerun), and finally `*pending*` when neither source has it.

Usage (repo root):
    python scripts/write_section3.py [--out-root out] [--benchmark BENCHMARK.md]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOMS = ["office_0", "office_1", "office_2", "office_3", "office_4",
         "room_0", "room_1", "room_2"]
TIERS = [1, 2, 3, 4]
START, END = "<!-- SECTION3:START -->", "<!-- SECTION3:END -->"
HEADER = ("| Tier | Objects | Matched / in-scope GT | Recall | Precision | "
          "mean Chamfer (cm) | mean F@5cm | mean dim-err | Score /100 |\n"
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|")
PENDING = "| {tier} | — | — | — | — | — | — | — | *pending* |"


def row_from_eval(tier: int, path: Path) -> str:
    e = json.loads(path.read_text())
    det, geo = e["detection"], e["geometry"]
    if not det.get("available"):
        return PENDING.format(tier=tier)
    chamfers = [o["chamfer_cm"] for o in e.get("objects", [])
                if o.get("matched") and "chamfer_cm" in o]
    mean_ch = f"{sum(chamfers) / len(chamfers):.1f}" if chamfers else "—"
    fmt = lambda v, spec=".2f": ("—" if v is None else f"{v:{spec}}")
    return (f"| {tier} | {det['shipped']} | {det['matched']}/{det['gt_in_scope']} "
            f"| {fmt(det['recall'])} | {fmt(det['precision'])} | {mean_ch} "
            f"| {fmt(geo['mean_fscore_5cm'])} | {fmt(geo['mean_dim_err'])} "
            f"| **{e['score']['value']}** |")


def existing_rows(section: str) -> tuple[dict[tuple[str, int], str],
                                         dict[str, list[str]]]:
    """Parse the current section tables. Returns (rows, notes):
    rows[(room, tier)] -> row line; notes[room] -> prose lines that follow a
    room's table (e.g. room_1's empty-scene explanation), preserved verbatim."""
    rows: dict[tuple[str, int], str] = {}
    notes: dict[str, list[str]] = {}
    room = None
    for line in section.splitlines():
        m = re.match(r"### (\w+)", line)
        if m:
            room = m.group(1)
            continue
        if not room:
            continue
        if re.match(r"\| (\d) \|", line):
            rows[(room, int(line[2]))] = line.rstrip()
        elif line.strip() and not line.startswith("|"):
            notes.setdefault(room, []).append(line.rstrip())
    return rows, notes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-root", type=Path, default=Path("out"))
    ap.add_argument("--benchmark", type=Path, default=Path("BENCHMARK.md"))
    args = ap.parse_args()

    text = args.benchmark.read_text()
    s, e = text.index(START), text.index(END)
    section = text[s + len(START):e]
    prose_end = section.index("### ")
    prose = section[:prose_end]
    old, notes = existing_rows(section)

    parts = [prose.rstrip()]
    n_new = 0
    for room in ROOMS:
        parts.append(f"\n### {room}\n\n{HEADER}")
        for tier in TIERS:
            ev = args.out_root / f"scene_{room}_t{tier}" / "eval.json"
            if ev.is_file():
                parts.append(row_from_eval(tier, ev))
                n_new += 1
            else:
                parts.append(old.get((room, tier), PENDING.format(tier=tier)))
        for note in notes.get(room, []):
            parts.append(f"\n{note}")
    new = text[:s + len(START)] + "\n".join(parts) + "\n\n" + text[e:]
    args.benchmark.write_text(new)
    print(f"§3 rewritten: {n_new} rows from eval.json, "
          f"{len(ROOMS) * len(TIERS) - n_new} kept/pending")


if __name__ == "__main__":
    main()
