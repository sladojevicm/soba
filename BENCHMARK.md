# BENCHMARK — measured accuracy of the vid2sim-v2 pipeline

_Measured 2026-07-05 on branch `fix/phase3-pose-and-eval` (commit `363dc52`),
CPU-only (WSL2, 8 cores, no CUDA), Open3D 0.19 / numpy 2.5 / CoACD 1.0.
Everything below was **run and measured for this file** — nothing is copied
from earlier sessions. Where a dataset provides no ground truth for a stage,
the entry says N/A and explains why, rather than inventing a proxy number._

The pipeline's four stages (README):

| Stage | Context | Responsibility |
|---|---|---|
| A | Perception | Camera capture, depth fusion, segmentation |
| B | Reconstruction | Pose, TSDF, mesh completion (image-to-3D), ICP |
| C | Scene Assembly | Physics inference, convex decomposition, exporters |
| D | Presentation | Browser viewer (Three.js + Rapier WASM) |

---

## 1. TUM RGB-D — accuracy per stage

Three sequences from the TUM RGB-D benchmark (freiburg1_xyz, freiburg1_desk,
freiburg2_xyz), full-resolution, processed end-to-end through
`TUMReader → PerceptionBundle → RgbdOdometry` (Tier-1 pose path), evaluated
against the dataset's motion-capture ground truth with the repo's
`scripts/eval_pose.py` procedure (nearest-timestamp association ≤20 ms,
SE(3) Kabsch alignment, no scale).

**What TUM can and cannot measure:** TUM provides ground truth for **camera
trajectory only**. It has no object/instance labels, no GT meshes, and no
physics annotations — so it can score Stage B's pose accuracy, and only
functional (pass/fail) checks are possible for stages A, C, D.

### Stage A — Perception

| Metric | fr1/xyz | fr1/desk | fr2/xyz |
|---|---|---|---|
| RGB frames | 798 | 613 | 3669 |
| RGB↔depth association rate (≤20 ms) | **100 %** | **97.2 %** | **99.9 %** |
| Bundle frames built | 798 | 596 | 1500 (capped) |
| Depth pixels valid in the [0.4 m, 8 m] gate | **75.6 %** | **74.6 %** | **68.9 %** |

Segmentation accuracy: **N/A on TUM** — the dataset has no object labels, and
the repo's known detection gap stands (no YOLO detector is wired into
`TUMReader`; SAM2 orchestration exists but the real model has never been run).
Every TUM frame therefore carries an empty `objects.json`, by design.

### Stage B — Reconstruction (pose, Tier-1 RGB-D odometry)

ATE = absolute trajectory error after SE(3) alignment, against mocap GT.

| Sequence | Frames | Traj. length | **ATE RMSE** | mean | median | max |
|---|---|---|---|---|---|---|
| fr2/xyz (slow, smooth) | 1500 | 2.9 m | **2.15 cm** | 1.97 | 1.87 | 4.4 cm |
| fr1/xyz (moderate) | 798 | 8.0 m | **4.74 cm** | 4.12 | 3.23 | 10.7 cm |
| fr1/desk (fast sweep) | 596 | 9.3 m | **26.4 cm** | 23.3 | 22.1 | 52.5 cm |

Reading: frame-to-frame odometry is accurate on slow/moderate motion (2–5 cm,
in line with the 3.66 cm previously recorded in STATUS.md for a differently
built fr1/xyz bundle) and **drifts badly on fast handheld motion** (26 cm on
fr1/desk) — there is no loop closure and nothing corrects accumulated error.
This is exactly the failure mode the plan's tier 2–4 pose methods
(MASt3R / ORB-SLAM3, currently stubs) are specified to fix.

TSDF / mesh-completion / ICP accuracy: **N/A on TUM** — no GT geometry to
score against (mesh quality is exercised on Replica in STATUS.md, and the
physics stage is scored on YCB/ABO below).

### Stage C — Scene Assembly

**Accuracy N/A on TUM** (no objects can enter the scene: no labels, no
detector). Functional result, measured: `run_assemble.py --tier 2` on the
fr1/xyz bundle runs the gate over zero objects and exits gracefully —
`routing: 0 keep(tsdf), 0 completion, 0 generative — nothing to assemble`,
no crash. The physics half of this stage is what the YCB/ABO section below
actually scores.

### Stage D — Presentation

**Accuracy N/A on TUM** (nothing renderable comes out of a label-less
sequence, and the dataset defines no presentation metric). Functional smoke
test, measured: `python -m server` on the (empty) TUM scene dir serves
`/` and `/scene.json` with HTTP 200.

⚠️ **Finding:** the server's "partial-safe" empty response
`{"version":"2.0","objects":[]}` **fails the frozen v2.0 schema** — the schema
requires `world`, `ground`, and `camera_pose`. The empty-scene fallback in
`src/server.py` should emit a minimal schema-valid document instead.

---

## 2. YCB + ABO — physics stage in isolation (perfect input geometry)

**Setup.** The 3D objects are assumed perfectly generated: ground-truth scans
(YCB `google_16k`) and artist-made retail models (ABO GLBs) are fed directly
into the physics part of Stage C, using the assembler's own generative-band
call path, unmodified:

```
phys = lookup.physics_lookup(coco_class)        # material/friction/restitution
vol  = mass.generative_volume(mesh)             # enclosed vol, clamped to 15–35 % of hull
m    = mass.mass_kg(vol, material, coco_class)  # vol × density[material] × solidity[class]
decomp.decompose(mesh, threshold=0.07, max_parts=8)   # tier-2 CoACD colliders
```

Predicted mass is compared to each object's real mass (YCB: masses from the
official object-list PDF; ABO: Amazon `item_weight` from the listings
metadata). Friction/restitution have **no dataset ground truth** — they can't
be scored, only their assignment coverage reported. "ratio" below =
predicted / true mass; "within k×" counts objects with ratio in [1/k, k].

### YCB (27 objects, real scanned household items)

| Aggregate | Value |
|---|---|
| Objects scored | 27/27 |
| Geometric-mean ratio (pred/true) | **0.30× (≈3.4× under-prediction)** |
| Median ratio | 0.27× |
| Within 2× | **18.5 %** |
| Within 3× | 37 % |
| Within 10× | 81.5 % |
| CoACD decomposition success | **27/27**, mean 4.3 hulls |
| CoACD time (tier-2, CPU) | median 26 s, worst 376 s (mug, scissors ~6 min) |

Per lookup-table row (the classes the config actually knows):

| Lookup row hit | n | Geomean ratio | Note |
|---|---|---|---|
| `sports ball` | 6 | **0.08×** | golf ball predicted 1.4 g vs real 46 g |
| `bottle` | 2 | **0.14×** | bleach bottle 140 g vs real 1131 g |
| `cup` | 1 | 0.47× | |
| `default` (unmapped classes) | 18 | 0.49× | |

**Diagnosis (visible in the per-object data).** Two compounding causes:

1. `mass.generative_volume` clamps the enclosed volume to **≤35 % of the
   convex hull** (`generative_mass.max_hull_ratio`). That band was tuned to
   normalise hollow/solid *generated* furniture — but a genuinely solid convex
   object (ball, full can, box) really occupies ~90–100 % of its hull, so the
   clamp alone under-reports volume ~3× on exactly the meshes this benchmark
   assumes ("perfectly generated").
2. The calibrated class rows stack on top: `sports ball` gets solidity 0.08 ×
   rubber 1200 kg/m³ = effective 96 kg/m³ — an order of magnitude below a
   baseball; `bottle` gets solidity 0.15 tuned for *empty* bottles while real
   household bottles are full (the YCB masses include contents).

Best case: cracker box 0.98× (411 g). Typical outliers: pitcher 3.1× over
(lookup can't know it's empty), sponge 3.7× over (foam ≪ 1000 kg/m³ default).

### ABO (60 objects, Amazon retail furniture with listed weights)

| Aggregate | Value |
|---|---|
| Objects scored | 59/60 (1 GLB failed to parse: truncated chunk) |
| Geometric-mean ratio | **1.23×** |
| Median ratio | **0.90×** |
| Within 2× | **59.3 %** |
| Within 3× | 76.3 % |
| Within 10× | 94.9 % |
| CoACD decomposition success | **59/60**, mean 7.3 hulls |
| CoACD time (tier-2, CPU) | median 55 s, worst 519 s |

Split by whether the class hits a calibrated table row:

| Group | n | Geomean ratio | Within 2× |
|---|---|---|---|
| **Calibrated rows** (chair, couch, dining table) | 35 | **0.83×** | **80 %** |
| `default` row (bed, lamp, pillow, rug, …) | 24 | 2.17× | 29 % |

Per class: chair 1.06×, couch 0.92×, cabinet 0.83×, lamp 1.17×, rug 1.12×,
dining table 0.52×, ottoman 2.9×, pillow 6.8×, bed 9.4×.

**Reading.** The physics tables were calibrated on furniture and it shows:
on the classes the config actually models, mass comes out within 2× for 80 %
of objects — genuinely good for a lookup+solidity heuristic. Errors
concentrate where the `default` row (solidity 0.5, density 1000) meets
soft/airy objects: beds and pillows over-predict ~7–24× (a mattress is mostly
air at 1000 kg/m³ × 0.5), and `dining table` under-predicts ~2× (tabletop
solidity 0.35 is low for solid-wood retail tables).

### Physics-stage takeaways (actionable)

1. **Bypass the 35 % hull clamp for meshes known to be watertight-solid** —
   `generative_volume` should trust the enclosed volume when
   `is_watertight()` (it already verifies sanity); the clamp exists for
   flaky generations, not perfect ones. This alone would move most of YCB
   from ~0.3× to ~0.9×.
2. **`sports ball` solidity 0.08 is ~5–10× too low** for real balls
   (measured effective density of a baseball ≈ 660 kg/m³ vs the config's 96).
3. **Add rows for soft furniture** (bed, pillow, ottoman) with low effective
   density — they are the entire ABO over-prediction tail.
4. **CoACD is fully reliable but has a long tail on CPU**: 86/87 meshes
   decomposed, but thin/concave meshes (mug, scissors, wire-frame furniture)
   take 6–9 min at tier-2 settings — worth a face-count guard before decomp.

---

## Method / reproducibility notes

- TUM: sequences `rgbd_dataset_freiburg{1_xyz,1_desk,2_xyz}` from
  cvg.cit.tum.de; fr2/xyz capped at the first 1500 associated pairs (of 3665)
  for CPU time; odometry = `reconstruction.slam.RgbdOdometry` (Open3D hybrid
  RGB-D odometry), evaluation identical to `scripts/eval_pose.py`.
- YCB: `google_16k/nontextured.ply` scans from ycb-benchmarks S3; ground-truth
  masses from the official YCB object-list PDF. Objects with no COCO
  equivalent keep a descriptive class name and hit the tables' `default` row —
  the same thing the live pipeline would do.
- ABO: 60 GLBs sampled (seed 42) across product types from the 7,119 ABO
  models that have both a 3D model and a listed `item_weight`; weights
  converted to kg. **Caveat:** Amazon `item_weight` is sometimes shipping
  weight (packaging included), so ABO "ground truth" carries some upward
  noise; YCB masses are measured on the physical objects and are exact.
- All timings are CPU-only (8-core WSL2); GPU timings will differ.
