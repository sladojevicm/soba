#!/usr/bin/env python3
"""Aggregate the routing ablation (out/abl_<room>_<strategy>/eval.json) into
the macro-averaged columns used by the ERK paper's Table 3: gated tier-2 vs
routing forced to a single strategy for every object. Same metric definitions
as write_section3.py / the paper: recall, precision, mean Chamfer over matched
objects, geometry.mean_fscore_5cm, geometry.mean_dim_err — each macro-averaged
across the 7 rooms with reconstructable furniture (room_1 excluded)."""
import json, statistics as st
from pathlib import Path

REPO = Path.home()/"projects/vid2sim/vid2sim-v2"
OUT = REPO/"out"
ROOMS = ["office_0","office_1","office_2","office_3","office_4","room_0","room_2"]
COLS = {
    "gated": "scene_{room}_t2",
    "tsdf": "abl_{room}_tsdf",
    "completion": "abl_{room}_completion",
    "generative": "abl_{room}_generative",
}

def room_metrics(p):
    d = json.load(open(p)); det = d["detection"]
    objs = [o for o in d.get("objects", []) if o.get("matched")]
    ch = [o["chamfer_cm"] for o in objs if o.get("chamfer_cm") is not None]
    return dict(shipped=det["shipped"], in_scope=det["gt_in_scope"],
                matched=det["matched"],
                recall=det["recall"], precision=det["precision"],
                chamfer=st.mean(ch) if ch else None,
                fscore=d["geometry"]["mean_fscore_5cm"],
                dim_err=d["geometry"]["mean_dim_err"])

agg = {}
for col, pat in COLS.items():
    per_room, missing = {}, []
    for room in ROOMS:
        p = OUT/pat.format(room=room)/"eval.json"
        if not p.is_file():
            missing.append(room); continue
        per_room[room] = room_metrics(p)
    rs = list(per_room.values())
    # precision/recall of an empty scene (0 shipped) is None in eval.json:
    # count recall as 0 (objects exist but none shipped); skip precision
    # (no shipped objects to be wrong about) — mirrors the paper's handling.
    def mavg(key, none_as=None):
        vals = [(r[key] if r[key] is not None else none_as) for r in rs]
        vals = [v for v in vals if v is not None]
        return st.mean(vals) if vals else None
    agg[col] = dict(
        rooms=len(rs), missing=missing,
        shipped=sum(r["shipped"] for r in rs),
        matched=sum(r["matched"] for r in rs),
        in_scope=sum(r["in_scope"] for r in rs),
        recall=mavg("recall", none_as=0.0),
        precision=mavg("precision"),
        chamfer=mavg("chamfer"),
        fscore=mavg("fscore"),
        dim_err=mavg("dim_err"),
        per_room=per_room,
    )

def f(x, nd=2):
    return "  — " if x is None else f"{x:.{nd}f}"

print(f"{'':14s} {'ship':>5s} {'match':>5s} {'recall':>6s} {'prec':>6s} "
      f"{'chamf':>6s} {'F@5':>6s} {'dim':>6s}")
for col, a in agg.items():
    print(f"{col:14s} {a['shipped']:5d} {a['matched']:5d} {f(a['recall']):>6s} "
          f"{f(a['precision']):>6s} {f(a['chamfer'],1):>6s} {f(a['fscore']):>6s} "
          f"{f(a['dim_err']):>6s}"
          + (f"   MISSING: {','.join(a['missing'])}" if a['missing'] else ""))

print("\nLaTeX rows (gated, tsdf, completion, generative):")
names = {"gated": r"Gated routing (tier~2)", "tsdf": r"All TSDF + repair",
         "completion": r"All completion", "generative": r"All generative"}
for col, a in agg.items():
    print(f"{names[col]:26s} & {a['shipped']} & {f(a['recall'])} & "
          f"{f(a['precision'])} & {f(a['chamfer'],1)} & {f(a['fscore'])} & "
          f"{f(a['dim_err'])} \\\\")

json.dump({k: {kk: vv for kk, vv in v.items() if kk != "per_room"}
           for k, v in agg.items()},
          open(REPO/"scratchpad/ablation_summary.json", "w"), indent=1)
print("\nwrote scratchpad/ablation_summary.json")
