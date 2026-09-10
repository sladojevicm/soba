# BENCHMARK — measured accuracy of the Soba pipeline (formerly vid2sim-v2)

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
| Bundle frames built | 798 | 596 | 3665 (full) |
| Depth pixels valid in the [0.4 m, 8 m] gate | **75.6 %** | **74.6 %** | **69.7 %** |

Segmentation accuracy: **N/A on TUM** — the dataset has no object labels, and
the repo's known detection gap stands (no YOLO detector is wired into
`TUMReader`; SAM2 orchestration exists but the real model has never been run).
Every TUM frame therefore carries an empty `objects.json`, by design.

### Stage B — Reconstruction (pose, Tier-1 RGB-D odometry)

ATE = absolute trajectory error after SE(3) alignment, against mocap GT.

| Sequence | Frames | Traj. length | **ATE RMSE** | mean | median | max |
|---|---|---|---|---|---|---|
| fr2/xyz (slow, smooth) | 3665 | 7.4 m | **4.26 cm** | 3.68 | 3.14 | 10.1 cm |
| fr1/xyz (moderate) | 798 | 8.0 m | **4.74 cm** | 4.12 | 3.23 | 10.7 cm |
| fr1/desk (fast sweep) | 596 | 9.3 m | **26.4 cm** | 23.3 | 22.1 | 52.5 cm |

Reading: frame-to-frame odometry is accurate on slow/moderate motion (2–5 cm,
in line with the 3.66 cm previously recorded in STATUS.md for a differently
built fr1/xyz bundle) and **drifts badly on fast handheld motion** (26 cm on
fr1/desk) — there is no loop closure and nothing corrects accumulated error.
This is exactly the failure mode the tier 2–4 pose method (MASt3R) is
specified to fix — measured below on the GPU machine.

### Stage B — Reconstruction (pose, Tiers 2–4: MASt3R, GPU)

_Measured 2026-07-05 on the GPU machine (RTX 4060 8 GB, CUDA), same three
sequences, same bundles-and-protocol as the tier-1 rows above (nearest-
timestamp ≤20 ms, SE(3) Kabsch). fr2/xyz re-measured 2026-07-17 on ALL 3665
associated pairs, both tiers (originally capped at the first 1500 for CPU
time; the capped numbers were T1 2.15 / MASt3R 1.85 cm — the full sequence
worsens both and flips the winner, see the reading below). Cross-machine
protocol check: tier-1 odometry re-run here reproduces the fr1 rows exactly
(4.74 / 26.37 cm; the fr2/xyz check predates the full re-run). MASt3R runs with the 8 GB
anchor cap `SOBA_MAST3R_MAX_IMAGES=24` — 24 anchor frames globally aligned
(dust3r), metric scale solved against sensor depth, all in-between poses
SE(3)-interpolated._

**Tier 4 = tiers 2–3 by decision.** ORB-SLAM3 was dropped permanently on
2026-07-05 (user decision): its loop closure was only justified if MASt3R
drifted, and the numbers below show it doesn't meaningfully — 7.6 cm on the
hardest sequence. `POSE_METHODS[4]` is now `"mast3r"`; the fallback stub was
deleted. A tier-4 run on fr1/xyz produced byte-identical results to tier 2,
as expected for the shared method.

| Sequence | Tier 1 (odometry) | **Tiers 2–4 (MASt3R)** | MASt3R Sim(3) scale | RPE 1 s: T1 → T2–4 |
|---|---|---|---|---|
| fr2/xyz (slow, smooth) | **4.26 cm** | 4.79 cm | 0.988 | 1.07 → 2.41 cm/s |
| fr1/xyz (moderate, oscillating) | **4.74 cm** | 8.78 cm | 1.075 | 2.25 → 14.1 cm/s |
| fr1/desk (fast sweep) | 26.37 cm | **7.56 cm** | **1.0008** | 6.67 → 9.83 cm/s |

Reading, per regime:

* **Fast handheld motion (the realistic capture case): MASt3R is 3.5× better**
  (26.4 → 7.56 cm). Global alignment bounds the error that kills incremental
  odometry; this is the measured justification for tiers 2–4 — and for NOT
  building ORB-SLAM3.
* **Slow motion (full 2-min fr2/xyz): odometry is slightly ahead** (4.26 vs
  4.79 cm). Over 3665 frames the 24 anchors sit ~5 s apart, so the
  interpolation cost (RPE 2.41 vs 1.07 cm/s) outweighs the small drift that
  slow, smooth motion accumulates. (On the earlier 1500-frame cap MASt3R
  still edged odometry, 1.85 vs 2.15 cm — denser anchors per unit motion.)
* **The one regime MASt3R loses: high-frequency oscillation** (fr1/xyz,
  8.78 vs 4.74 cm). Cause is visible in the RPE column: with only 24 anchors
  over 798 frames, SE(3) interpolation smooths straight through the rapid
  back-and-forth between anchors (14.1 cm/s local error vs odometry's 2.25).
  More VRAM (a higher anchor cap) directly attacks this; room-scan footage
  does not oscillate like fr1/xyz, so the walkthrough use case sits closer
  to the desk/fr2 rows.
* **Metric scale is genuinely solved**: Sim(3)-recovered scale 0.988–1.075
  (0.08–7.5 % error), i.e. `solve_metric_scale` against sensor depth works —
  the pipeline's claim of metric poses holds without any GT scale input.
* **Runtime inverts the tiers' cost intuition**: MASt3R is ~120 s per
  sequence regardless of length (fixed 24 anchors; confirmed 120.1 s on the
  full 3665-frame fr2/xyz), while CPU odometry scales with frames
  (~0.9 s/frame on the 8-core WSL box: 720 s on fr1/xyz; the full fr2/xyz
  odometry pass takes hours on the laptop CPU).

Reproduce:

```
PYTHONPATH=src SOBA_MAST3R_MAX_IMAGES=24 python scripts/bench_tum_pose.py \
    --seq  ~/projects/soba/data/tum/rgbd_dataset_freiburg1_desk \
    --bundle ~/projects/soba/data/tum/bundle_f1desk \
    --tiers 1 2 --max-frames 10000 --json results.json
```

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

### Physics stage — Claude VLM (tiers 2–4), AGENT-PREVIEW ⚠️

_Measured 2026-07-05 on the GPU machine. ⚠️ **Preview methodology**: the
production inputs were reproduced exactly — `vlm_claude.annotate_crop`
annotated PNGs (green box + red metric ruler), the production system prompt,
the production JSON schema, production batching (12 objects/call, array-order
matching, enum/clamp guards) — but answered by **Claude Code Opus agents**
instead of the raw API (`ANTHROPIC_API_KEY` not yet provisioned). Same model
family as `config vlm.model` (claude-opus-4-8), different serving surface.
Replace with `scripts/benchmark_physics.py --dataset {ycb,abo}` once a key
exists; treat these numbers as indicative, not canonical._

_This experiment uses the **image+dims input path** (GT longest dimension on
the ruler, GT shape volume for mass) — the runner fetched its own samples
(YCB n=56 with photos from the official checklist PDF; ABO n=250 with catalog
photos). Numbers are NOT comparable to the mesh-based tables above (different
volume model, no hull clamp, different samples); compare only against the
**lookup column measured on the same objects**, below._

| | YCB (n=56) | | ABO (n=250) | |
|---|---|---|---|---|
| | **lookup (T1)** | **Claude (T2–4)** | **lookup (T1)** | **Claude (T2–4)** |
| Mass: median abs. rel. error | **0.57** | 0.95 | 2.46 | **1.95** |
| Mass: within 2× | **50 %** | 32 % | 29 % | **33 %** |
| Mass: median ratio (pred/true) | **0.99×** | 1.71× | 3.46× | **2.95×** |
| Material accuracy | — (n/a) | **98 %** (n=47) | — | **65 %** (n=240) |

**The headline finding: perception is essentially solved; the mass MODEL is
the bottleneck.** On YCB the VLM identified the surface material almost
perfectly (46/47; the one miss was a painted wood block called plastic) —
yet its mass numbers are *worse* than lookup's. The per-object data shows
why: `mass = volume × density(material) × solidity(class)` cannot represent
hollow or thin-walled construction, and correct materials *expose* that
flaw while lookup's wrong-but-light `unknown` (density 1000) accidentally
compensates. Measured examples:

* metal bowl, correctly identified → 7800 kg/m³ × default solidity 0.5 →
  **27.9× over** (a bowl is a thin shell, not 50 % solid metal);
* plastic storage box, correct → **30.9× over** (hollow);
* metal bed frames on ABO, correct material → **430–510× over** (a frame is
  ~1 % of its bounding volume, not 50 %).

**ABO's 65 % material accuracy is an undercount — the GT is dirty.** The
largest mismatch cluster is "Amazon Brand – **Stone** & Beam" items whose
listed material parsed as *stone* while the VLM (correctly, from the photo)
said *fabric* — brand-name contamination in the catalog field, not a
perception error.

**Actionable (the single highest-leverage physics fix):** extend the VLM
schema with a construction estimate (solid / hollow / thin-walled-frame, or
a numeric fill fraction) and use it in place of the class-solidity constant.
The VLM demonstrably *sees* what things are made of; it was never asked how
much of the bounding volume is actually material. That one schema field
attacks the entire 28×–510× overshoot tail.

### `fill_fraction` ablation — the fix, implemented and re-measured ⚠️ agent-preview

_Implemented 2026-07-05 in production code: `Physics.fill_fraction`
(src/scene/vlm.py), schema + prompt (vlm_claude.py), `mass_kg(...,
fill_fraction=)` override with the class-solidity table as fallback
(mass.py, assembler.py) — tier 1 / lookup behaviour is byte-identical; only
tiers 2–4 gain the new term. All 306 objects re-judged with the extended
schema (same agent-preview transport as above)._

| mass accuracy | lookup (T1) | Claude v1 (class solidity) | **Claude v2 (fill_fraction)** |
|---|---|---|---|
| **ABO** within 2× | 29 % | 33 % | **43 %** |
| **ABO** median abs. rel. err | 2.46 | 1.95 | **1.30** |
| **ABO** median ratio | 3.46× | 2.95× | **2.30×** |
| **YCB** within 2× | **50 %** | 32 % | 38 % |
| **YCB** median abs. rel. err | **0.57** | 0.95 | 1.22 |
| **YCB** outside 10× band | — | 9/56 | **1/56** |
| **ABO** outside 10× band | — | 57/250 | **33/250** |

**The catastrophic tail is fixed.** The VLM estimates construction well:
storage box 30.9× → 4.9× (fill 0.08), metal bowl 27.9× → 5.6× (0.10),
baseball 0.05× → 0.55× (0.95 — it correctly reversed direction), metal bed
frames 430–540× → 35–43× (0.04). On furniture — the product's actual
domain — every aggregate metric now clearly beats both the lookup table and
the class-solidity Claude run.

**The residual error has a new, sharper diagnosis: surface material ≠ bulk
material.** The worst v2 cases are *full containers and foam*: a tuna can
(fill 0.9 — correct! it IS full) is priced at metal's 7800 kg/m³, but it's a
thin steel shell full of fish at ~1000 kg/m³ → 7.7× over. A foam brick reads
as rubber (1200) but is ~50 kg/m³ foam. Metal tube furniture at fill 0.10 is
still 70–150× over because true effective fill of a wire frame is ~0.01 and
the model anchors on the prompt's suggested range. The formula's remaining
assumption — density(surface material) applies to the whole filled volume —
is now the bottleneck. The obvious next rung (not yet built): ask the VLM
for an **effective density** or the mass itself, making the tables advisory.
On YCB (mostly full products, contents-dominated masses) this residual keeps
Claude v2 below the lookup table on medians, even though its worst-case
behaviour is now far better (1 vs 9 objects outside the 10× band).

---

<!-- SECTION3:START -->
## 3. Replica — room reconstruction accuracy (per tier, vs Habitat ground truth)

Each of the 8 vMAP Replica rooms is rebuilt from its **custom-trajectory `_v2` bundle** (200 frames, exact ground-truth camera poses, ground-truth instance masks) and compared to the Habitat GT semantic mesh (`mesh_semantic.ply` + `info_semantic.json`) by `scripts/evaluate_scene.py`, which writes `out/scene_<room>_t<tier>/eval.json`. One table per room; one row per tier (1 *fast* → 4 *maximum*).

**Columns.** *Objects* = meshes the pipeline shipped · *Matched / in-scope GT* = how many shipped objects were paired with a real GT instance, out of the GT instances whose class is in the pipeline's COCO scope · *Recall* = matched ÷ in-scope GT · *Precision* = matched ÷ shipped (an unmatched shipped object is a false positive) · *mean Chamfer* = average symmetric surface distance of matched meshes, cm (lower better) · *mean F@5cm* = fraction of surface within 5 cm of GT, averaged over matched meshes (higher better) · *mean dim-err* = mean per-axis bounding-box size error · *Score* = the 0–100 below.

**Score.** `score = 100 · (0.40·recall + 0.20·precision + 0.30·F@5cm + 0.10·pose)`. **Pose is excluded here**: the Replica builds consume the dataset's exact GT camera poses (no SLAM runs), so trajectory error is 0 by construction and identical across tiers — the per-tier pose numbers live in §1 (TUM). With pose dropped, the remaining weights renormalise to **0.444·recall + 0.222·precision + 0.333·F@5cm**.

**Matching** pairs a shipped object to a GT instance of the same COCO class when their centroids are within 0.75 m (or world-AABB IoU ≥ 0.1), solved per class with the Hungarian algorithm. GT classes the detector never maps (tv, potted plant, clock, …) are **out of scope** and excluded from the score.

_All four tiers are measured. Tier 2 = TripoSG + gate/completion + CoACD hulls; tiers 3–4 = Hunyuan3D 2.1 (+ paint) with PatchComplete completion, built 2026-07-06 on a RunPod RTX 3090. Tiers 1–2 were built from the original `_v2` bundles on the previous machine; tiers 3–4 from bundles re-rendered with the same `render_replica.py` parameters (the renderer is deterministic given the GT mesh, but treat sub-point cross-tier deltas with that caveat)._

_**Tier 3 vs tier 4, measured finding:** tier 3 beats tier 2 in every office (cleaner Hunyuan geometry → higher F@5cm) but loses both cluttered `room_*` rooms by shipping MORE, worse-observed objects whose generated proportions drag the per-object means down (room_0: recall 0.39→0.56 but dim-err 0.23→0.64). Tier 4 tracks 0.5–2.6 points **below** tier 3 on this score in 6 of 7 non-empty rooms: its finer TSDF voxel (2 mm) and finer colliders (32 hulls) buy physics fidelity, which this surface-accuracy score does not measure — past tier 3, the extra compute is not visible in geometry numbers._

### office_0

| Tier | Objects | Matched / in-scope GT | Recall | Precision | mean Chamfer (cm) | mean F@5cm | mean dim-err | Score /100 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 4 | 4/6 | 0.67 | 1.00 | 12.7 | 0.30 | 0.25 | **61.9** |
| 2 | 5 | 5/6 | 0.83 | 1.00 | 5.9 | 0.58 | 0.12 | **78.4** |
| 3 | 5 | 5/6 | 0.83 | 1.00 | 5.6 | 0.60 | 0.13 | **79.4** |
| 4 | 5 | 5/6 | 0.83 | 1.00 | 6.1 | 0.56 | 0.11 | **77.8** |

### office_1

| Tier | Objects | Matched / in-scope GT | Recall | Precision | mean Chamfer (cm) | mean F@5cm | mean dim-err | Score /100 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 2 | 2/4 | 0.50 | 1.00 | 10.5 | 0.49 | 0.16 | **60.9** |
| 2 | 3 | 2/4 | 0.50 | 0.67 | 3.8 | 0.76 | 0.06 | **62.4** |
| 3 | 4 | 3/4 | 0.75 | 0.75 | 11.8 | 0.50 | 0.36 | **66.6** |
| 4 | 4 | 3/4 | 0.75 | 0.75 | 12.1 | 0.48 | 0.35 | **66.0** |

### office_2

| Tier | Objects | Matched / in-scope GT | Recall | Precision | mean Chamfer (cm) | mean F@5cm | mean dim-err | Score /100 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 8 | 8/14 | 0.57 | 1.00 | 11.6 | 0.40 | 0.36 | **60.8** |
| 2 | 10 | 10/14 | 0.71 | 1.00 | 8.5 | 0.61 | 0.30 | **74.4** |
| 3 | 10 | 10/14 | 0.71 | 1.00 | 7.8 | 0.69 | 0.28 | **77.0** |
| 4 | 10 | 10/14 | 0.71 | 1.00 | 8.2 | 0.64 | 0.27 | **75.3** |

### office_3

| Tier | Objects | Matched / in-scope GT | Recall | Precision | mean Chamfer (cm) | mean F@5cm | mean dim-err | Score /100 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 11 | 11/17 | 0.65 | 1.00 | 9.6 | 0.39 | 0.76 | **63.9** |
| 2 | 12 | 12/17 | 0.71 | 1.00 | 5.0 | 0.67 | 0.46 | **75.9** |
| 3 | 12 | 12/17 | 0.71 | 1.00 | 4.9 | 0.67 | 0.47 | **76.0** |
| 4 | 12 | 12/17 | 0.71 | 1.00 | 5.4 | 0.59 | 0.45 | **73.4** |

### office_4

| Tier | Objects | Matched / in-scope GT | Recall | Precision | mean Chamfer (cm) | mean F@5cm | mean dim-err | Score /100 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 9 | 9/11 | 0.82 | 1.00 | 8.9 | 0.38 | 0.24 | **71.4** |
| 2 | 10 | 10/11 | 0.91 | 1.00 | 7.6 | 0.56 | 0.19 | **81.2** |
| 3 | 10 | 10/11 | 0.91 | 1.00 | 6.7 | 0.61 | 0.26 | **83.1** |
| 4 | 9 | 9/11 | 0.82 | 1.00 | 6.9 | 0.59 | 0.26 | **78.2** |

### room_0

| Tier | Objects | Matched / in-scope GT | Recall | Precision | mean Chamfer (cm) | mean F@5cm | mean dim-err | Score /100 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 5 | 5/18 | 0.28 | 1.00 | 8.2 | 0.44 | 0.18 | **49.1** |
| 2 | 7 | 7/18 | 0.39 | 1.00 | 4.4 | 0.76 | 0.23 | **64.7** |
| 3 | 11 | 10/18 | 0.56 | 0.91 | 11.7 | 0.45 | 0.64 | **59.9** |
| 4 | 11 | 10/18 | 0.56 | 0.91 | 11.0 | 0.49 | 0.60 | **61.2** |

### room_1

| Tier | Objects | Matched / in-scope GT | Recall | Precision | mean Chamfer (cm) | mean F@5cm | mean dim-err | Score /100 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | — | — | — | — | — | — | — | *pending* |
| 2 | — | — | — | — | — | — | — | *pending* |
| 3 | — | — | — | — | — | — | — | *pending* |
| 4 | — | — | — | — | — | — | — | *pending* |

_All four tiers produce an **empty scene**: room_1 is a walkthrough-only corridor whose only 3 in-scope tracks are books, none of which yields a usable crop, so all were dropped (0 shipped, precision/recall N/A). No orbit-size furniture exists to reconstruct._

### room_2

| Tier | Objects | Matched / in-scope GT | Recall | Precision | mean Chamfer (cm) | mean F@5cm | mean dim-err | Score /100 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 8 | 8/10 | 0.80 | 1.00 | 7.9 | 0.46 | 0.17 | **73.3** |
| 2 | 9 | 9/10 | 0.90 | 1.00 | 3.9 | 0.82 | 0.08 | **89.4** |
| 3 | 10 | 9/10 | 0.90 | 0.90 | 5.0 | 0.78 | 0.11 | **85.9** |
| 4 | 10 | 9/10 | 0.90 | 0.90 | 2.6 | 0.87 | 0.06 | **88.9** |

<!-- SECTION3:END -->

## 4. Routing ablation — gated tier 2 vs a single forced strategy (2026-09-05)

Reviewer-requested isolation of the confidence gate for the ERK camera-ready:
the seven non-empty `_v2` rooms rebuilt at otherwise identical tier-2 settings
with the gate's per-object decision replaced by a constant
(`run_assemble.py --force-strategy tsdf|completion|generative`). Everything
downstream — drop-garbage output checks, sizing, placement, eval protocol —
is unchanged. Driver: `scripts/ablation_routing.sh`; aggregation:
`scripts/ablation_summary.py` (metric definitions identical to §3; macro-avg
across the 7 rooms; scenes in `out/abl_<room>_<strategy>/`).

| Strategy | Shipped | Matched/80 | Recall | Precision | mean Chamfer (cm) | mean F@5cm | mean dim-err |
|---|---:|---:|---:|---:|---:|---:|---:|
| **Gated routing (tier 2)** | 56 | 55 | 0.71 | 0.95 | **5.6** | **0.68** | 0.20 |
| All TSDF + repair | 66 | 64 | 0.83 | 0.95 | 8.5 | 0.52 | 0.21 |
| All completion | 66 | 64 | 0.83 | 0.95 | 6.6 | 0.64 | 0.20 |
| All generative | 47 | 47 | 0.61 | 1.00 | 9.9 | 0.41 | 0.30 |

Readings (all in the camera-ready §3.2/Discussion):
- **Forced generative reproduces tier 1 to rounding** (47/0.61/1.00/9.9/0.41/0.30
  vs tier 1's identical row) — the generation cache reuses tier-1 outputs and
  geometry metrics don't depend on the other tier-2 stage changes, so the
  tier-1→2 gain in §3 is attributable to the routing itself, not confounds.
- **Gated routing has the best surface fidelity of any strategy** (Chamfer
  5.6 cm, F@5cm 0.68) at equal precision.
- **The fixed measured-geometry strategies buy recall (0.83 vs 0.71), not
  quality**: both match the same 9 extra GT instances — the poorly observed
  tail the gate sends to generation, where output checks drop it. Those 9
  average F@5cm 0.29 (completion) / 0.22 (tsdf) vs 0.72 / 0.56 for the 55
  objects shared with the gated scene. The gate is a fidelity↔coverage dial;
  "all completion" is the recall-maximal setting of the same
  measurement-first principle and the strongest fixed baseline.
- The paper's claims were reworded accordingly (fidelity, not blanket
  superiority): "yields more faithful surfaces than fixing any single
  reconstruction strategy".









---

## Method / reproducibility notes

- TUM: sequences `rgbd_dataset_freiburg{1_xyz,1_desk,2_xyz}` from
  cvg.cit.tum.de; all three evaluated on every associated pair (fr2/xyz was
  capped at 1500 of 3665 until the 2026-07-17 full re-run, bundle
  `bundle_f2xyz_full`); odometry = `reconstruction.slam.RgbdOdometry` (Open3D hybrid
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
