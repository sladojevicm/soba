#!/usr/bin/env python3
"""Physics-accuracy benchmark — isolates the physics-estimation stage (Step 8).

Premise: assume the 3D reconstruction was PERFECT. Feed real objects with
KNOWN mass/material/dimensions (image + ground-truth metric dims, exactly the
inputs the assembler hands to ``vlm.infer``) into the production physics stage
and score the predictions against ground truth. This measures our physics
stage (material inference + the mass_kg = volume * density * solidity model),
not the reconstruction.

Backends compared (both are the REAL production code paths):
  lookup — src/scene/lookup.py class-keyed table (Tier 1, and the fallback
           whenever the Claude call is unavailable). Via vlm.from_lookup.
  claude — src/scene/vlm_claude.py ClaudeBackend (Tiers 2/3/4), exercised
           through vlm.infer with the annotated crop + metric-dim ruler,
           batched the way the assembler batches (objects_cap=12 per call).
           Requires ANTHROPIC_API_KEY; every raw response is cached to disk
           keyed by object id + model, so reruns are free.

Datasets:
  ycb — YCB Object and Model Set (Calli et al., 2015). Ground-truth masses
        (digital scale, ±1 g) and principal dimensions (calipers, ±0.1 mm)
        from the official object checklist PDF (ycbbenchmarks.com); one
        studio photo per object extracted from that same PDF. 56 of the 72
        catalogue rows are usable (see YCB_EXCLUDED for the documented
        drop reasons: multi-object rows, deformables, erroneous dims).
  abo — Amazon Berkeley Objects (listings metadata, CC BY 4.0). Catalog
        item_weight + item_dimensions + material for product types that map
        to our COCO classes; one catalog photo per item (abo images-small).

Reproduce:
  # one-time dataset fetch (small: ~90 MB network for ABO, ~8 MB for YCB)
  PYTHONPATH=src python scripts/benchmark_physics.py --dataset ycb --fetch
  PYTHONPATH=src python scripts/benchmark_physics.py --dataset abo --fetch
  # run (lookup always; claude only when ANTHROPIC_API_KEY is set)
  PYTHONPATH=src python scripts/benchmark_physics.py --dataset ycb
  PYTHONPATH=src python scripts/benchmark_physics.py --dataset abo

Outputs (under --data-root, default ~/projects/vid2sim/data/benchmarks):
  {ycb,abo}/…             fetched ground truth + images (manifest *_gt.json)
  predictions/…           per-object cached Claude responses (rerun-safe)
  results/{ds}_results.json   per-object rows + metrics summary
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import random
import sys
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "src") not in sys.path:
    sys.path.insert(0, str(_REPO / "src"))

DEFAULT_DATA_ROOT = Path(
    os.environ.get(
        "VID2SIM_BENCH_DATA",
        str(Path.home() / "projects" / "vid2sim" / "data" / "benchmarks"),
    )
)

def _download(url: str, dst: Path) -> None:
    """urlretrieve with a browser UA (ycbbenchmarks.com 406s python-urllib)."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp, open(dst, "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)


# $/MTok (input, output) — for the cost report only. claude-api reference 2026-06.
_PRICES_PER_MTOK = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

# ---------------------------------------------------------------------------
# Metrics (pure functions — unit-tested in tests/test_benchmark_metrics.py)
# ---------------------------------------------------------------------------

WITHIN_2X_LOG10 = 0.3  # |log10(pred/true)| <= 0.3  (~2.0x either way)


def mass_metrics(pairs: list[tuple[float, float]]) -> dict:
    """Mass accuracy for (pred_kg, true_kg) pairs (all must be > 0).

    - med_are / mean_are: median/mean of |pred - true| / true
    - frac_within_2x: fraction with |log10(pred/true)| <= 0.3 (~within 2x)
    - med_log10_ratio: median of log10(pred/true) (signed bias; + = too heavy)
    """
    if not pairs:
        return {"n": 0, "med_are": None, "mean_are": None,
                "frac_within_2x": None, "med_log10_ratio": None}
    ares = sorted(abs(p - t) / t for p, t in pairs)
    logs = sorted(math.log10(p / t) for p, t in pairs)
    n = len(pairs)

    def _median(xs):
        return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])

    return {
        "n": n,
        "med_are": _median(ares),
        "mean_are": sum(ares) / n,
        "frac_within_2x": sum(abs(v) <= WITHIN_2X_LOG10 for v in logs) / n,
        "med_log10_ratio": _median(logs),
    }


def material_accuracy(pairs: list[tuple[str, str]]) -> dict:
    """Accuracy for (pred_material, gt_material); caller filters gt=None out."""
    if not pairs:
        return {"n": 0, "accuracy": None}
    return {"n": len(pairs),
            "accuracy": sum(p == t for p, t in pairs) / len(pairs)}


def per_class_metrics(rows: list[dict], backend: str) -> dict:
    """Group per-object rows by class -> mass metrics + material accuracy."""
    by_cls: dict[str, list[dict]] = {}
    for r in rows:
        by_cls.setdefault(r["cls"], []).append(r)
    out = {}
    for cls, rs in sorted(by_cls.items()):
        mp = [(r[f"mass_{backend}"], r["mass_gt_kg"]) for r in rs
              if r.get(f"mass_{backend}")]
        mat = [(r[f"material_{backend}"], r["material_gt"]) for r in rs
               if r.get(f"material_{backend}") and r.get("material_gt")]
        out[cls] = {"mass": mass_metrics(mp), "material": material_accuracy(mat)}
    return out


def shape_volume_m3(shape: str, dims_mm: list[float]) -> float:
    """Displaced-volume estimate from ground-truth principal dimensions.

    The production stage measures volume from the (assumed-perfect) mesh; we
    reconstruct that volume analytically from the catalogued dims:
      box       (l, w, h)   -> l*w*h
      cylinder  (d, h)      -> pi (d/2)^2 h
      sphere    (d,)        -> pi d^3 / 6
      ellipsoid (d, h)      -> pi/6 d^2 h   (fruit-like solids of revolution)
    ABO items only carry 3 box dims, so ABO is all 'box' (an upper bound on
    the true hull volume — documented caveat in BENCHMARK.md).
    """
    d = [x / 1000.0 for x in dims_mm]
    if shape == "box":
        assert len(d) == 3
        return d[0] * d[1] * d[2]
    if shape == "cylinder":
        assert len(d) == 2
        return math.pi * (d[0] / 2.0) ** 2 * d[1]
    if shape == "sphere":
        assert len(d) == 1
        return math.pi * d[0] ** 3 / 6.0
    if shape == "ellipsoid":
        assert len(d) == 2
        return math.pi / 6.0 * d[0] * d[0] * d[1]
    raise ValueError(f"unknown shape {shape!r}")


# ---------------------------------------------------------------------------
# YCB ground truth (curated from the official object checklist PDF:
# http://www.ycbbenchmarks.com/wp-content/uploads/2015/09/object-list-Sheet1.pdf
# mass in grams as printed; dims in mm as printed unless noted).
# cls = the class string the physics stage receives (COCO class where one
# exists — what the detector would say — else the object's plain name).
# material = ground-truth material in OUR vocabulary (vlm_claude.MATERIALS);
# None = genuinely mixed/uncertain, excluded from material scoring.
# ---------------------------------------------------------------------------

YCB_PDF_URL = "http://www.ycbbenchmarks.com/wp-content/uploads/2015/09/object-list-Sheet1.pdf"

# (id, name, cls, material, mass_g, dims_mm, shape, note)
YCB_GT = [
    (1, "cracker box", "cracker box", "paper", 411, [60, 158, 210], "box", ""),
    (2, "sugar box", "sugar box", "paper", 514, [38, 89, 175], "box", ""),
    (3, "pudding box", "pudding box", "paper", 187, [35, 110, 89], "box", ""),
    (4, "gelatin box", "gelatin box", "paper", 97, [28, 85, 73], "box", ""),
    (5, "potted meat can", "potted meat can", "metal", 370, [50, 97, 82], "box", "filled"),
    (6, "coffee can", "coffee can", "metal", 414, [102, 139], "cylinder", "filled; plastic lid"),
    (7, "tuna fish can", "tuna fish can", "metal", 171, [85, 33], "cylinder", "filled"),
    (8, "chips can", "chips can", "paper", 205, [75, 250], "cylinder", "cardboard tube, metal base"),
    (9, "mustard bottle", "bottle", "plastic", 603, [50, 85, 175], "box", "filled"),
    (10, "tomato soup can", "tomato soup can", "metal", 349, [66, 101], "cylinder", "filled"),
    (11, "plastic banana", "banana", "plastic", 66, [36, 190], "cylinder", "toy fruit"),
    (12, "plastic strawberry", "strawberry", "plastic", 18, [43.8, 55], "ellipsoid", "toy fruit"),
    (13, "plastic apple", "apple", "plastic", 68, [75], "sphere", "toy fruit"),
    (14, "plastic lemon", "lemon", "plastic", 29, [54, 68], "ellipsoid", "toy fruit"),
    (15, "plastic peach", "peach", "plastic", 33, [59], "sphere", "toy fruit"),
    (16, "plastic pear", "pear", "plastic", 49, [66.2, 100], "ellipsoid", "toy fruit"),
    (17, "plastic orange", "orange", "plastic", 47, [73], "sphere", "toy fruit"),
    (18, "plastic plum", "plum", "plastic", 25, [52], "sphere", "toy fruit"),
    (19, "windex spray bottle", "bottle", "plastic", 1022, [80, 105, 270], "box", "filled with liquid"),
    (20, "cleanser bottle", "bottle", "plastic", 1131, [250, 98, 65], "box", "filled with liquid"),
    (21, "sponge", "sponge", None, 6.2, [72, 114, 14], "box", "cellulose foam - no vocab match"),
    (22, "pitcher base", "pitcher", "plastic", 178, [108, 235], "cylinder", ""),
    (23, "pitcher lid", "pitcher lid", "plastic", 66, [123, 48], "cylinder", ""),
    (24, "plate", "plate", None, 279, [258, 24], "cylinder", "material not stated in checklist"),
    (25, "bowl", "bowl", None, 147, [159, 53], "cylinder", "material not stated in checklist"),
    (26, "fork", "fork", "metal", 34, [14, 20, 198], "box", "plastic handle"),
    (27, "spoon", "spoon", "metal", 30, [14, 20, 195], "box", "plastic handle"),
    (28, "knife", "knife", "metal", 31, [14, 20, 215], "box", "plastic handle"),
    (29, "spatula", "spatula", "plastic", 51.5, [83, 35, 350], "box", ""),
    (30, "wine glass", "wine glass", None, 133, [89, 137], "cylinder", "material not stated"),
    (31, "mug", "cup", "metal", 118, [80, 82], "cylinder", "enamel-coated metal"),
    (35, "scissors", "scissors", "metal", 82, [87, 200, 14], "box", "metal blades, plastic handles"),
    (36, "large marker", "large marker", "plastic", 15.8, [18, 121], "cylinder", ""),
    (37, "small marker", "small marker", "plastic", 8.2, [8, 135], "cylinder", ""),
    (38, "padlock", "padlock", "metal", 208, [24, 47, 65], "box", "keys excluded"),
    (42, "phillips screwdriver", "phillips screwdriver", None, 97, [31, 215], "cylinder", "plastic handle + steel shaft"),
    (43, "flat screwdriver", "flat screwdriver", None, 98.4, [31, 215], "cylinder", "plastic handle + steel shaft"),
    (44, "adjustable wrench", "adjustable wrench", "metal", 252, [5, 55, 205], "box", ""),
    (45, "wood block", "wood block", "wood", 729, [85, 85, 200], "box", ""),
    (48, "credit card blank", "credit card", "plastic", 5.2, [54, 85, 1], "box", ""),
    (49, "mini soccer ball", "sports ball", "rubber", 123, [140], "sphere", "inflated"),
    (50, "softball", "sports ball", None, 191, [96], "sphere", "solid core + leather"),
    (51, "baseball", "sports ball", None, 138, [80], "sphere", "solid core + leather"),
    (52, "tennis ball", "sports ball", "rubber", 58, [64.7], "sphere", "hollow rubber + felt"),
    (53, "racquetball", "sports ball", "rubber", 41, [55.3], "sphere", "hollow rubber"),
    (54, "golf ball", "sports ball", "plastic", 46, [42.7], "sphere", "urethane shell, solid"),
    (57, "foam brick", "foam brick", None, 28, [50, 75, 50], "box", "foam - no vocab match"),
    (58, "dice", "dice", "plastic", 5.2, [16.2, 16.2, 16.2], "box", ""),
    (62, "rubiks cube", "rubiks cube", "plastic", 94, [57, 57, 57], "box", ""),
    (63, "clear storage box", "clear storage box", "plastic", 302, [292, 429, 149], "box", "hollow container"),
    (64, "storage box lid", "box lid", "plastic", 159, [292, 429, 20], "box", ""),
    (65, "colored wood block", "wood block", "wood", 10.8, [26, 26, 26], "box", "single 26mm cube"),
    (67, "toy airplane", "toy airplane", "plastic", 570, [171, 266, 280], "box", "assembled toy; bbox mostly air"),
    (69, "magazine", "book", "paper", 73, [256, 200, 1.6], "box", ""),
    (71, "timer", "clock", "plastic", 101, [80, 85, 45], "box", "checklist dims in cm, converted"),
    (72, "footlocker", "suitcase", "plastic", 3700, [790.7, 444.5, 352.6], "box", "checklist dims in inches, converted; hollow container"),
]

# Catalogue rows NOT benchmarked, with the reason (documented in BENCHMARK.md).
YCB_EXCLUDED = {
    32: "skillet — checklist dims (270x25x30) ambiguous (handle/body unclear)",
    33: "skillet lid — checklist dims (270x10x22) ambiguous",
    34: "table cloth — deformable, folded 2D dims, no defined volume",
    39: "hammer — checklist dims (34x32x135) implausible for a 665 g hammer",
    40: "nails — multi-object row, per-item masses bracketed",
    41: "bolt & nut — multi-object row",
    46: "clamps — multi-object row (4 sizes)",
    47: "power drill — checklist dims (35x46x184) cannot bound a drill body",
    55: "marbles — mass/dims N/A in checklist",
    56: "cups — 10-piece nesting set, bracketed masses",
    59: "washers — multi-object row (7 sizes)",
    60: "rope — deformable, degenerate dims",
    61: "chain — deformable, per-link dims",
    66: "9-peg-hole test — checklist dims (1150x1200x1200) clearly erroneous",
    68: "lego duplo — set mass vs per-brick dims, ambiguous",
    70: "t-shirt — deformable, 2D dims",
}


def fetch_ycb(root: Path) -> Path:
    """Download the YCB checklist PDF, extract the per-object studio photos
    (image placements mapped to table rows by page position via PyMuPDF),
    and write ycb_gt.json. Total network: one 7.9 MB PDF."""
    import fitz  # PyMuPDF

    ycb = root / "ycb"
    img_dir = ycb / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = ycb / "object-list-Sheet1.pdf"
    if not pdf_path.exists():
        print(f"[ycb] downloading {YCB_PDF_URL}")
        _download(YCB_PDF_URL, pdf_path)

    doc = fitz.open(pdf_path)
    # First (page, y0) occurrence of each image xref = its table-row position.
    first_pos: dict[int, tuple[int, float]] = {}
    for pno in range(len(doc)):
        for img in doc[pno].get_images(full=True):
            xref = img[0]
            for rect in doc[pno].get_image_rects(xref):
                key = (pno, rect.y0)
                if xref not in first_pos or key < first_pos[xref]:
                    first_pos[xref] = key
    ordered = [x for x, _ in sorted(first_pos.items(), key=lambda kv: kv[1])]
    if len(ordered) != 72:
        raise RuntimeError(f"expected 72 object photos in the PDF, got {len(ordered)}")

    id_to_img = {}
    for obj_id, xref in enumerate(ordered, start=1):
        info = doc.extract_image(xref)
        out = img_dir / f"{obj_id:03d}.{info['ext']}"
        if not out.exists():
            out.write_bytes(info["image"])
        id_to_img[obj_id] = str(out)

    objects = []
    for (oid, name, cls, mat, mass_g, dims, shape, note) in YCB_GT:
        objects.append({
            "id": f"ycb_{oid:03d}",
            "name": name,
            "cls": cls,
            "material_gt": mat,
            "mass_gt_kg": mass_g / 1000.0,
            "dims_mm": dims,
            "shape": shape,
            "volume_m3": shape_volume_m3(shape, dims),
            "dim_longest_m": max(dims) / 1000.0,
            "image": id_to_img[oid],
            "note": note,
        })
    manifest = {"dataset": "ycb", "source": YCB_PDF_URL,
                "excluded": {str(k): v for k, v in sorted(YCB_EXCLUDED.items())},
                "objects": objects}
    out = ycb / "ycb_gt.json"
    out.write_text(json.dumps(manifest, indent=1))
    print(f"[ycb] wrote {out} ({len(objects)} objects, {len(YCB_EXCLUDED)} excluded)")
    return out


# ---------------------------------------------------------------------------
# ABO (Amazon Berkeley Objects) — listings metadata + small images.
# License: CC BY 4.0 (Amazon.com). https://amazon-berkeley-objects.s3.amazonaws.com
# ---------------------------------------------------------------------------

ABO_BASE = "https://amazon-berkeley-objects.s3.amazonaws.com"

# ABO product_type -> the COCO-ish class string our detector would emit.
ABO_TYPE_TO_CLASS = {
    "CHAIR": "chair",
    "STOOL_SEATING": "chair",
    "SOFA": "couch",
    "TABLE": "dining table",
    "DESK": "dining table",
    "BED": "bed",
    "BENCH": "bench",
    "PLANTER": "potted plant",
    "VASE": "vase",
    "DRINKING_CUP": "cup",
    "SUITCASE": "suitcase",
    "WALL_CLOCK": "clock",
    "CLOCK": "clock",
    "WATER_BOTTLE": "bottle",
    "BOTTLE": "bottle",
    "BOOK": "book",
    "TELEVISION": "tv",
}

# Per-class plausible catalog-weight range (kg) — outside -> discarded.
ABO_WEIGHT_RANGE = {
    "chair": (1.0, 80), "couch": (5.0, 250), "dining table": (2.0, 250),
    "bed": (5.0, 300), "bench": (2.0, 150), "potted plant": (0.05, 60),
    "vase": (0.05, 40), "cup": (0.03, 3), "suitcase": (0.5, 30),
    "clock": (0.05, 25), "bottle": (0.02, 5), "book": (0.05, 10),
    "tv": (1.0, 80),
}

# ABO free-text material -> our material vocabulary (lowercased contains-match,
# first hit wins; None = unmappable, excluded from material scoring).
ABO_MATERIAL_MAP = [
    ("engineered wood", "wood"), ("wood", "wood"), ("bamboo", "wood"),
    ("mdf", "wood"), ("oak", "wood"), ("pine", "wood"), ("walnut", "wood"),
    ("stainless", "metal"), ("steel", "metal"), ("iron", "metal"),
    ("aluminum", "metal"), ("aluminium", "metal"), ("metal", "metal"),
    ("brass", "metal"), ("chrome", "metal"), ("alloy", "metal"),
    ("polypropylene", "plastic"), ("plastic", "plastic"), ("abs", "plastic"),
    ("polycarbonate", "plastic"), ("acrylic", "plastic"), ("resin", "plastic"),
    ("pvc", "plastic"), ("polyester", "fabric"), ("cotton", "fabric"),
    ("linen", "fabric"), ("velvet", "fabric"), ("fabric", "fabric"),
    ("leather", "fabric"), ("upholster", "fabric"), ("foam", "fabric"),
    ("microfiber", "fabric"), ("wool", "fabric"), ("rattan", "wood"),
    ("wicker", "wood"), ("glass", "glass"), ("ceramic", "ceramic"),
    ("porcelain", "ceramic"), ("stoneware", "ceramic"), ("terracotta", "ceramic"),
    ("marble", "stone"), ("granite", "stone"), ("stone", "stone"),
    ("concrete", "stone"), ("cement", "stone"), ("rubber", "rubber"),
    ("paper", "paper"), ("cardboard", "paper"),
]


def _map_abo_material(values: list[str]) -> str | None:
    for v in values:
        lv = v.lower()
        for needle, mat in ABO_MATERIAL_MAP:
            if needle in lv:
                return mat
    return None


def _norm(entry: dict) -> tuple[float, str]:
    nv = entry.get("normalized_value", entry)
    return float(nv["value"]), nv["unit"]


_TO_KG = {"pounds": 0.45359237, "kilograms": 1.0, "grams": 0.001,
          "ounces": 0.028349523, "milligrams": 1e-6}
_TO_M = {"inches": 0.0254, "centimeters": 0.01, "meters": 1.0,
         "millimeters": 0.001, "feet": 0.3048}


def fetch_abo(root: Path, per_class: int = 30, total_cap: int = 250,
              seed: int = 0) -> Path:
    """Scan the 16 ABO listings shards (downloaded one at a time, ~5 MB each,
    deleted after parsing), filter to mapped product types with catalog weight
    + dimensions + a main image, sample per class, then download only the
    sampled items' small catalog images."""
    abo = root / "abo"
    img_dir = abo / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    tmp = abo / "_tmp"
    tmp.mkdir(exist_ok=True)

    candidates: dict[str, list[dict]] = {}
    n_scanned = 0
    for shard in "0123456789abcdef":
        gz = tmp / f"listings_{shard}.json.gz"
        if not gz.exists():
            url = f"{ABO_BASE}/listings/metadata/listings_{shard}.json.gz"
            print(f"[abo] downloading shard {shard}")
            _download(url, gz)
        with gzip.open(gz, "rt") as fh:
            for line in fh:
                n_scanned += 1
                it = json.loads(line)
                pts = it.get("product_type") or []
                cls = ABO_TYPE_TO_CLASS.get(pts[0].get("value") if pts else None)
                if cls is None:
                    continue
                if not (it.get("item_weight") and it.get("item_dimensions")
                        and it.get("main_image_id")):
                    continue
                try:
                    wv, wu = _norm(it["item_weight"][0])
                    weight_kg = wv * _TO_KG[wu]
                    dims_m = []
                    for k in ("length", "width", "height"):
                        dv, du = _norm(it["item_dimensions"][k])
                        dims_m.append(dv * _TO_M[du])
                except (KeyError, ValueError, TypeError):
                    continue
                lo, hi = ABO_WEIGHT_RANGE[cls]
                if not (lo <= weight_kg <= hi):
                    continue
                if not all(0.01 <= d <= 3.5 for d in dims_m):
                    continue
                mats = [m["value"] for m in it.get("material", [])
                        if m.get("language_tag", "en").startswith("en")]
                names = [n["value"] for n in it.get("item_name", [])
                         if n.get("language_tag", "").startswith("en")]
                candidates.setdefault(cls, []).append({
                    "id": f"abo_{it['item_id']}",
                    "name": (names[0] if names else it["item_id"])[:120],
                    "cls": cls,
                    "product_type": pts[0]["value"],
                    "material_gt": _map_abo_material(mats),
                    "material_raw": mats[:3],
                    "mass_gt_kg": round(weight_kg, 4),
                    "dims_mm": [round(d * 1000, 1) for d in dims_m],
                    "shape": "box",
                    "volume_m3": dims_m[0] * dims_m[1] * dims_m[2],
                    "dim_longest_m": max(dims_m),
                    "main_image_id": it["main_image_id"],
                })
        gz.unlink()  # keep disk footprint ~one shard

    rng = random.Random(seed)
    sampled: list[dict] = []
    for cls, items in sorted(candidates.items()):
        # Dedup by item id, prefer items WITH a mapped material (better GT).
        uniq = {c["id"]: c for c in items}
        items = sorted(uniq.values(), key=lambda c: (c["material_gt"] is None, c["id"]))
        with_mat = [c for c in items if c["material_gt"]]
        without = [c for c in items if not c["material_gt"]]
        rng.shuffle(with_mat), rng.shuffle(without)
        take = (with_mat + without)[:per_class]
        sampled.extend(take)
        print(f"[abo] {cls}: {len(items)} candidates -> {len(take)} sampled "
              f"({sum(1 for c in take if c['material_gt'])} with material GT)")
    sampled = sampled[:total_cap]

    # image_id -> path lookup, then fetch just the sampled small images.
    csv_gz = tmp / "images.csv.gz"
    if not csv_gz.exists():
        print("[abo] downloading images.csv.gz")
        _download(f"{ABO_BASE}/images/metadata/images.csv.gz", csv_gz)
    wanted = {c["main_image_id"] for c in sampled}
    paths: dict[str, str] = {}
    with gzip.open(csv_gz, "rt") as fh:
        next(fh)  # header: image_id,height,width,path
        for line in fh:
            image_id, _, _, path = line.rstrip("\n").split(",")
            if image_id in wanted:
                paths[image_id] = path
    kept = []
    for c in sampled:
        rel = paths.get(c["main_image_id"])
        if rel is None:
            continue
        dst = img_dir / f"{c['id']}{Path(rel).suffix}"
        if not dst.exists():
            try:
                _download(f"{ABO_BASE}/images/small/{rel}", dst)
            except Exception as e:  # noqa: BLE001 - drop items w/ missing images
                print(f"[abo] image fetch failed for {c['id']}: {e}")
                continue
        c["image"] = str(dst)
        c.pop("main_image_id", None)
        kept.append(c)
    csv_gz.unlink()

    manifest = {"dataset": "abo", "source": ABO_BASE, "license": "CC BY 4.0",
                "seed": seed, "per_class": per_class,
                "scanned_listings": n_scanned, "objects": kept}
    out = abo / "abo_gt.json"
    out.write_text(json.dumps(manifest, indent=1))
    print(f"[abo] wrote {out} ({len(kept)} objects with images)")
    return out


# ---------------------------------------------------------------------------
# Prediction runners — the REAL production code paths.
# ---------------------------------------------------------------------------

BATCH = 12  # production objects_cap per scene == per Claude call (pipeline.yaml)


def run_lookup(objs: list[dict]) -> list[dict]:
    """Tier-1 path: the class-keyed lookup table (vlm.from_lookup)."""
    from scene import vlm

    out = []
    for o in objs:
        p = vlm.from_lookup(o["cls"])
        out.append({"material": p.material, "friction": p.friction,
                    "restitution": p.restitution, "is_rigid": p.is_rigid,
                    "origin": p.origin})
    return out


class _UsageMessages:
    def __init__(self, inner, log):
        self._inner, self._log = inner, log

    def create(self, **kw):
        resp = self._inner.messages.create(**kw)
        u = getattr(resp, "usage", None)
        if u is not None:
            self._log.append({
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
                "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
            })
        return resp


class UsageClient:
    """Transparent wrapper so we can report tokens/cost without touching the
    production ClaudeBackend code."""

    def __init__(self, inner):
        self.log: list[dict] = []
        self.messages = _UsageMessages(inner, self.log)


def run_claude(objs: list[dict], pred_root: Path, cap: int = 300):
    """Tier-2/3/4 path: vlm.infer with the live ClaudeBackend (annotated crop
    + metric ruler, schema-constrained output, batched at the production cap).

    Returns (preds_by_id, model, usage_summary) or (None, model, reason) when
    no ANTHROPIC_API_KEY is available. Every per-object prediction is cached
    at predictions/{dataset}/{model}/{id}.json so reruns cost nothing.
    """
    from scene import vlm, vlm_claude

    backend = vlm_claude.make_backend()
    if backend is None:
        return None, None, "no ANTHROPIC_API_KEY (or VID2SIM_VLM=0)"
    backend.client = UsageClient(backend.client)  # cost accounting only
    model = backend.model

    cache_dir = pred_root / model
    cache_dir.mkdir(parents=True, exist_ok=True)
    preds: dict[str, dict] = {}
    todo = []
    for o in objs:
        c = cache_dir / f"{o['id']}.json"
        if c.exists():
            preds[o["id"]] = json.loads(c.read_text())
        else:
            todo.append(o)
    todo = todo[: max(0, cap - len(preds))]
    print(f"[claude] {len(preds)} cached, {len(todo)} to query (model {model})")

    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        classes = [o["cls"] for o in batch]
        crops = [o.get("image") for o in batch]
        dims = [o["dim_longest_m"] for o in batch]
        results = vlm.infer(classes, backend=backend, crops=crops, dims_m=dims)
        for o, p in zip(batch, results):
            rec = {"material": p.material, "friction": p.friction,
                   "restitution": p.restitution, "is_rigid": p.is_rigid,
                   "origin": p.origin, "reasoning": p.reasoning, "model": model}
            if p.origin == "vlm":  # cache only real Claude answers, not fallbacks
                (cache_dir / f"{o['id']}.json").write_text(json.dumps(rec))
                preds[o["id"]] = rec
            else:
                print(f"[claude] batch {i // BATCH}: fell back to lookup "
                      f"(refusal/mismatch) — {o['id']} not cached")

    usage = {"calls": len(backend.client.log),
             "input_tokens": sum(u["input_tokens"] for u in backend.client.log),
             "output_tokens": sum(u["output_tokens"] for u in backend.client.log)}
    price = _PRICES_PER_MTOK.get(model)
    if price:
        usage["est_cost_usd"] = round(
            usage["input_tokens"] * price[0] / 1e6
            + usage["output_tokens"] * price[1] / 1e6, 4)
    return preds, model, usage


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run(dataset: str, data_root: Path, backend_sel: str, cap: int,
        limit: int | None) -> dict:
    from scene import mass

    manifest_path = data_root / dataset / f"{dataset}_gt.json"
    if not manifest_path.exists():
        raise SystemExit(f"{manifest_path} missing — run with --fetch first")
    manifest = json.loads(manifest_path.read_text())
    objs = manifest["objects"][:limit] if limit else manifest["objects"]

    rows = []
    for o in objs:
        rows.append({"id": o["id"], "name": o["name"], "cls": o["cls"],
                     "mass_gt_kg": o["mass_gt_kg"], "material_gt": o["material_gt"],
                     "volume_m3": o["volume_m3"], "shape": o["shape"]})

    backends_out: dict[str, dict] = {}

    if backend_sel in ("both", "lookup"):
        lk = run_lookup(objs)
        for r, o, p in zip(rows, objs, lk):
            r["material_lookup"] = p["material"]
            r["mass_lookup"] = mass.mass_kg(o["volume_m3"], p["material"], o["cls"])
        backends_out["lookup"] = _summarize(rows, "lookup")

    if backend_sel in ("both", "claude"):
        preds, model, usage = run_claude(objs, data_root / "predictions" / dataset, cap)
        if preds is None:
            backends_out["claude"] = {"status": "pending", "reason": usage,
                                      "note": "set ANTHROPIC_API_KEY and rerun "
                                              "scripts/benchmark_physics.py"}
        else:
            for r, o in zip(rows, objs):
                p = preds.get(o["id"])
                if p is None:
                    continue
                r["material_claude"] = p["material"]
                r["mass_claude"] = mass.mass_kg(o["volume_m3"], p["material"], o["cls"])
                r["is_rigid_claude"] = p["is_rigid"]
                r["friction_claude"] = p["friction"]
                r["restitution_claude"] = p["restitution"]
            backends_out["claude"] = _summarize(rows, "claude")
            backends_out["claude"]["model"] = model
            backends_out["claude"]["usage"] = usage

    results = {"dataset": dataset, "n_objects": len(objs),
               "backends": backends_out, "per_object": rows}
    out_dir = data_root / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{dataset}_results.json"
    out.write_text(json.dumps(results, indent=1))
    print(f"\nwrote {out}")
    _print_summary(results)
    return results


def _summarize(rows: list[dict], backend: str) -> dict:
    mp = [(r[f"mass_{backend}"], r["mass_gt_kg"]) for r in rows
          if r.get(f"mass_{backend}")]
    mat = [(r[f"material_{backend}"], r["material_gt"]) for r in rows
           if r.get(f"material_{backend}") and r.get("material_gt")]
    return {"mass": mass_metrics(mp), "material": material_accuracy(mat),
            "per_class": per_class_metrics(rows, backend)}


def _print_summary(results: dict) -> None:
    print(f"\n=== {results['dataset']} — {results['n_objects']} objects ===")
    for name, b in results["backends"].items():
        if b.get("status") == "pending":
            print(f"  {name:8s}: PENDING — {b['reason']}")
            continue
        m, mt = b["mass"], b["material"]
        print(f"  {name:8s}: mass medARE {m['med_are']:.2f}  meanARE {m['mean_are']:.2f}  "
              f"within2x {m['frac_within_2x']:.0%}  bias(log10) {m['med_log10_ratio']:+.2f}  "
              f"(n={m['n']})  | material acc "
              f"{mt['accuracy']:.0%} (n={mt['n']})" if mt["accuracy"] is not None
              else f"  {name:8s}: mass medARE {m['med_are']:.2f} (n={m['n']}) | no material GT")
        if "usage" in b:
            print(f"           model {b['model']}  usage {b['usage']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", choices=["ycb", "abo"], required=True)
    ap.add_argument("--fetch", action="store_true",
                    help="download/build the ground-truth manifest + images")
    ap.add_argument("--backend", choices=["both", "lookup", "claude"], default="both")
    ap.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    ap.add_argument("--cap", type=int, default=300,
                    help="max NEW Claude-scored objects per run (spend bound)")
    ap.add_argument("--limit", type=int, default=None,
                    help="only benchmark the first N objects (debug)")
    args = ap.parse_args()

    if args.fetch:
        (fetch_ycb if args.dataset == "ycb" else fetch_abo)(args.data_root)
    run(args.dataset, args.data_root, args.backend, args.cap, args.limit)


if __name__ == "__main__":
    main()
