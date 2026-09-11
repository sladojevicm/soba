# ADR 0001: iPhone LiDAR capture as a Soba input source (ADR + BDR)

| | |
|---|---|
| **Status** | PROPOSED — decision required from the maintainer |
| **Date** | 2026-09-11 |
| **Author** | lidar-feasibility-agent (analysis only; no code) |
| **Deciders** | maintainer |
| **Bounded context** | A — Perception (input side only) |
| **Blocks** | `lidar-capture-agent` (see §1) |

**Evidence tags used throughout.** `[repo]` verified in this checkout of `develop`
(`9cbd012`) with a file:line reference · `[apple]` Apple developer documentation ·
`[3p]` third-party source · `[est]` the author's estimate, not a measurement. Every
number from `BENCHMARK.md` is quoted, never derived. All URLs were accessed on
2026-09-11 and are listed in §9.

---

## 1. Status

**PROPOSED.** Nothing in this document is a decision. The maintainer decides by
filling in §7.

**`lidar-capture-agent` is blocked.** Per `.claude/AGENTS.md` ("Conditional track"),
that agent gets no branch and writes no code — no iOS app, no `arkit_reader.py`, no
stub, no fixture — until this ADR is explicitly approved by the maintainer **in
writing**. This document proposes what its first slice would be (§8) precisely so
that nobody has to invent it, but proposing is not authorising.

## 2. Context and the question

Soba turns a short RGB-D video of a room into a physics-enabled browser scene. Its
only input contract is the on-disk `PerceptionBundle` (`src/perception/bundle.py`),
which is written today by three sources: the TUM reader, the Replica reader, and a
live OAK capture path `[repo: src/perception/dataset_reader.py:111, :212;
bundle.py:12-13]`. Depth is mandatory (`depth.png`, uint16 millimetres, "always"
`[repo: bundle.py:26]`); there is no monocular-video path, and the maintainer has
already decided there will not be one: uploads are PerceptionBundle or TUM archives,
never plain MP4, and depth-from-video "belongs to the LiDAR ADR"
`[repo: .claude/AGENTS.md, decision 1]`.

Two facts make a phone source relevant now:

1. **The paper.** Review 2 of the ERK 2026 submission asked for an evaluation "off the
   orbit trajectories on a real handheld capture" `[repo: STATUS.md:10-13]`. STATUS.md
   also records that "a real-sensor scene end to end (TUM through detector, SAM2,
   MASt3R, assembly)" has **never been run** `[repo: STATUS.md:39-41]`.
2. **The product.** The production-hardening track (job API, Docker, RunPod
   orchestration) builds an upload → job → viewer service. Its only realistic
   non-dataset input is a phone with a depth sensor, and on phones that means Apple's
   LiDAR.

**The question being decided:** should Soba accept iPhone LiDAR captures as an input
source, and if so through which path — a native ARKit capture app, third-party raw
exports (Polycam, Record3D), or neither?

## 3. Technical feasibility

### 3.1 What ARKit provides per frame

| Item | Fact | Tag |
|---|---|---|
| `ARFrame.sceneDepth` | "Data on the distance between a device's rear camera and real-world objects"; `nil` unless the `sceneDepth` frame semantic is added; populated "with `ARDepthData` captured by the LiDAR scanner"; iOS 14+. | [apple] |
| `ARFrame.smoothedSceneDepth` | "similar to `sceneDepth` except that the framework smoothes the depth data over time to lessen its frame-to-frame delta". | [apple] |
| `ARDepthData.depthMap` | "estimated distance from the device to its environment, in meters"; "Every pixel in the depthMap maps to a region of the visible scene (capturedImage), where the pixel value defines that region's distance from the plane of the camera in meters". | [apple] |
| `ARDepthData.confidenceMap` | "The framework's confidence in the accuracy of the depth-map data ... useful in filtering out lower-accuracy depth values". Three `ARConfidenceLevel` values: low, medium, high (Apple's sample shader maps 0/1/2). | [apple] |
| Depth resolution / rate / types | 256 × 192, 60 Hz, `Float32` depth, `UInt8` confidence. Apple's own docs only say "precise, low-resolution"; the numbers come from WWDC20 "Explore ARKit 4" and independent measurements ("sceneDepth (256 × 192, 60FPS)"). | [3p] |
| RGB `capturedImage` | 1920 × 1440 (4:3) on LiDAR iPhones — same aspect ratio as the depth map, factor 7.5. Not found stated in the Apple pages fetched; widely reported. | [3p] |
| LiDAR range | "measures the distance to surrounding objects up to 5 meters away" (Apple newsroom, iPad Pro 2020). | [apple] |
| Depth accuracy | Centre-of-image error under ±2.5 cm at 2–3 m in a controlled study; periphery markedly worse. Field guides quote ~1–3 cm under 3 m and 5–10 cm near 5 m. Comparable to the Kinect-class depth in TUM. | [3p] |
| `ARCamera.intrinsics` | 3 × 3 pinhole `K` with `fx, fy, ox, oy` "expressed in pixels", tied to `ARCamera.imageResolution` ("The width and height, in pixels, of the captured camera image"). | [apple] |
| `ARCamera.transform` | "The position and orientation of the camera in world coordinate space" — i.e. **camera → world**. Camera-space axes: X along the device's long axis, Y up, "Z-axis points away from the device on the screen side" → the camera looks down **−Z** (OpenGL convention). | [apple] |
| World frame (`WorldAlignment.gravity`, the default) | "The y-axis matches the direction of gravity as detected by the device's motion sensing hardware; that is, the vector (0,-1,0) points downward"; origin = "the initial position of the device"; −Z = direction the camera initially faced; right-handed. | [apple] |
| Timestamps | `ARFrame.timestamp` is a `TimeInterval` in seconds; a float per frame, which is exactly what `frame_times.json` stores `[repo: bundle.py:199-211]`. | [apple]/[repo] |
| Raw IMU | Not exposed on `ARFrame` (CoreMotion is a separate API). Irrelevant: gravity is already baked into the pose, and nothing downstream reads `imu.jsonl` `[repo: grep — only bundle.py read/write helpers]`. | [repo] |

**Which depth to use.** `sceneDepth`, not `smoothedSceneDepth`. TSDF fusion is itself a
temporal average over every frame `[repo: src/reconstruction/tsdf.py:190 and docstring]`;
temporally pre-smoothed depth adds lag and edge ghosting on a moving camera without
adding information `[est]`.

### 3.2 RoomPlan versus raw ARKit frames

RoomPlan returns a `CapturedRoom`: walls, doors, windows, and objects as
**category-labelled bounding boxes**, exported to USDZ. It does not expose per-frame
depth or camera frames `[apple]`. The Soba pipeline needs per-frame depth plus
per-frame instance masks to build a per-object TSDF and to score the confidence gate
(angular coverage and completeness, `config/pipeline.yaml:116-126`). A box with a
category cannot enter that pipeline anywhere; at most it could later seed class
priors or an AABB sanity check. **Only raw ARKit frames fit the bundle.**

### 3.3 Coordinate conventions — and a gotcha that disappears

The bundle's pose contract: `T_world_camera`, 4 × 4, `P_world = T_world_camera @
P_camera`, "world = first-frame camera, Y up, metres, right-handed"
`[repo: src/reconstruction/slam.py:13-15; bundle.py:182-197]`. TSDF integrates with
Open3D, whose camera looks down **+Z** with **+Y down** (OpenCV convention), using the
inverted pose as extrinsic `[repo: STATUS.md:56 "the extrinsic must be
.inv().contiguous()"]`. Replica's `traj_w_c.txt` is consumed in that convention after a
Z-up → Y-up world rotation `[repo: dataset_reader.py:189-209]`.

ARKit differs in exactly one place: the **camera** axes. Its world is already
right-handed and Y-up. So a reader must right-multiply every ARKit pose by the
OpenGL → OpenCV flip:

```
T_bundle = T_arkit @ diag(1, -1, -1, 1)      # flip camera Y and Z; world untouched
```

and must **not** apply any world rotation. Verification is cheap: back-project one
depth frame with `T_bundle`, and the floor must land at minimum Y.

Two consequences worth stating plainly:

- **The floor-snapping gotcha goes away for this source.** CLAUDE.md and STATUS.md
  both record that TUM/odometry/MASt3R worlds are the first camera frame, never
  gravity-aligned, so `ground.py` (which assumes a Y-up world and takes the floor as
  the 1st percentile of observed Y minus `ground.prov_offset_m` `[repo:
  src/scene/ground.py:3-8, :20-33; config/pipeline.yaml:24]`) is **wrong on real-sensor
  runs**. An ARKit world is gravity-aligned by construction (§3.1). A phone bundle would
  be the first real-sensor input on which the existing floor logic is correct without
  any change to `ground.py`. Nothing in `ground.py` needs to be — or should be —
  touched.
- The ARKit world origin is the device's *initial position*, not the first camera pose
  (they coincide in position but the orientation is gravity/heading, not the camera's).
  This deviates from the `slam.py` docstring wording "world = first-frame camera" but
  is harmless: nothing downstream assumes the first pose is identity (Replica poses are
  not identity either `[repo: dataset_reader.py:247-252]`).

### 3.4 Field-by-field mapping, ARKit → PerceptionBundle

| Bundle file `[repo: bundle.py]` | Source in ARKit | Transformation | Notes |
|---|---|---|---|
| `manifest.json` (session_id, fps, frame_count, timestamp_start, source) L70-90 | app session id; capture fps; count; ISO start time | `source = "arkit"` (or `"polycam"` / `"record3d"` for §3.9 exports) | `Manifest.source` is a free string; readers already use `"tum"`, `"replica"`, `"live"`. |
| `intrinsics.json` (fx, fy, cx, cy, distortion[], baseline) L37-66 | `ARCamera.intrinsics`, `imageResolution` | scale `fx, fy, cx, cy` by (written RGB width / 1920) — see §3.5 | One `K` is shared by depth and colour ("depth registered to RGB" L38); `distortion = []` (ARKit's `capturedImage` is delivered rectified for the intrinsics given — [3p], treat as unverified until checked on-device); `baseline = null`. |
| `frames/NNNNN/rgb.jpg` L124, L224-226 | `ARFrame.capturedImage` (YCbCr) | convert to RGB, resize to the chosen grid, JPEG q90 | Landscape orientation must be locked or the image rotated consistently with `K`. |
| `frames/NNNNN/depth.png` (uint16 mm) L127, L214-218 | `sceneDepth.depthMap` (Float32 m) | `round(m × 1000)`, clip to 65535, 0 = invalid; nearest-neighbour resample to the grid | `write_depth_mm` already does the dtype clamp; never bilinear (§3.5). |
| `frames/NNNNN/conf.png` (uint8) L130, L231-236 | `sceneDepth.confidenceMap` (0/1/2) | map to **0 / 127 / 255** (low / medium / high); nearest resample | Same encoding Polycam uses `[3p]`. Polarity is ascending, so the "VERIFY SENSOR POLARITY" note on `pipeline.yaml:160` is satisfied; with `conf_threshold: 150` only *high* survives. |
| `poses.json` (`T_world_camera`) L182-197 | `ARCamera.transform` | right-multiply `diag(1,-1,-1,1)` (§3.3) | Written by the reader → **trusted poses** (§3.7). |
| `frame_times.json` (seconds) L203-211 | `ARFrame.timestamp` | as-is | Monotonic device clock; only differences matter. |
| `frames/NNNNN/objects.json`, `mask_<track>.png` L133-138, L159-166, L239-246 | — | empty at capture | Filled server-side by YOLO + SAM2 (§3.8). |
| `frames/NNNNN/imu.jsonl` L140, L168-179 | — | omit | Optional; no consumer. |
| `crops/` L251-268 | — | — | Staged server-side by `crop_stage.py`. |

### 3.5 Resolution: the one design choice the reader has to make

Because the bundle carries a single `K` for depth and colour, `depth.png` and
`rgb.jpg` must share a pixel grid `[repo: bundle.py:38 "depth registered to RGB"]`;
`crop_stage.py` combines `depth > 0` with RGB-resolution masks, and the observed-cloud
back-projection uses the same `K` for depth pixels `[repo: CLAUDE.md gotcha list;
src/perception/crop_stage.py]`. Three options:

| Option | Grid | Pros | Cons |
|---|---|---|---|
| (a) upsample depth + conf ×7.5 to 1920 × 1440 | full RGB | best crops for image-to-3D and the VLM | largest archive; fabricates 56 depth pixels per real one |
| **(b) upsample depth + conf ×4 to 1024 × 768, resize RGB down to 1024 × 768** | 1024 × 768 | integer factor; YOLO11-seg and SAM2 work well at 1024 px; ~3.5× smaller than (a); crops of a couch at 2 m are still ~300 px | slightly softer crops than (a) |
| (c) keep depth at 256 × 192, downsample RGB | 256 × 192 | tiny | far too small for detection, masks and crops |

**Recommendation `[est]`: (b).** Use nearest-neighbour for depth and confidence in
every option: bilinear interpolation across a depth discontinuity invents surfaces
halfway between a chair and the wall behind it, which TSDF fusion will faithfully
integrate.

### 3.6 Depth resolution versus the TSDF voxel and the depth gates

**Repo-verified correction to the brief.** The task brief describes the TSDF voxel as
"5 mm (tier 4: 2 mm)". `config/pipeline.yaml` actually sets `voxel_size_m` to
**0.004 / 0.003 / 0.002** for tiers 2 / 3 / 4 (tier 1 has no TSDF, `null`)
`[repo: config/pipeline.yaml:106, :114, :136, :147]`. The 5 mm figure is only the
**gate-cloud** default: `voxel = params["voxel_size_m"] or 0.005` in
`scripts/run_assemble.py:150`, used for the tier-1 cloud and the gate accumulation.
This ADR uses the yaml values.

Footprint of one LiDAR depth pixel `[est]`: with RGB `fx ≈ 1600 px` at 1920 wide
(typical for the wide camera, [3p]), the depth-grid focal length is ≈ 1600 / 7.5 ≈
213 px, so one depth pixel covers ≈ 4.7 mm at 1 m, ≈ 9.4 mm at 2 m, ≈ 14 mm at 3 m.

- Below ~1 m the tier-2 4 mm voxel matches the sensor; at typical furniture distances
  (1.5–3 m) each depth pixel spans two to three voxels. TSDF fusion over many frames
  with sub-voxel camera motion recovers some of that (this is how Kinect-class sensors
  work in KinectFusion-style pipelines), but thin structures — chair and table legs —
  will suffer exactly the drop-out already documented for the current pipeline
  `[repo: deploy/runpod/README.md:92-99]`. Tiers 3–4 (3 mm, 2 mm) buy nothing from a
  256 × 192 sensor beyond ~0.7 m `[est]`; that is consistent with BENCHMARK §3's
  finding that tier 4's finer voxel is "not visible in geometry numbers".
- **Near gate 400 mm** `[repo: src/reconstruction/observed_cloud.py:17;
  tsdf.py:17]`: Apple publishes no minimum range. Field reports put usable LiDAR depth
  from roughly 0.2–0.3 m `[3p, unverified]`; the 400 mm gate is therefore not the
  binding constraint. Note the gate lives as constants in two modules and the
  `depth:` block in `pipeline.yaml` is read by nothing `[repo: CLAUDE.md gotcha]` —
  a phone source does not require changing either, and this ADR does not propose to.
- **Far gate 8000 mm** versus the **5 m** LiDAR range: ARKit still returns values past
  5 m (fused from RGB/ML), flagged with lower confidence. The pipeline already has the
  right knob: `tsdf.fuse(conf_min=...)` zeroes depth where `conf < conf_min` and is
  skipped only when a bundle has no `conf.png` `[repo: tsdf.py:23-26, :174-175]`.
  **A phone bundle would be the first input that exercises `conf.png`**; the reader
  should write it and the driver should pass `conf_min` (e.g. 255 = high only, or 127
  to keep medium) for `source in {"arkit", "polycam"}`. The polarity check that
  `pipeline.yaml:160` asks for is answered in §3.4.

### 3.7 Which tier the ARKit poses replace, and what the paper may claim

**Mechanism `[repo]`.** `scripts/run_assemble.py:143` does `poses = b.read_poses()`
and never calls `slam.estimate_poses` (`src/reconstruction/slam.py:293-298`, which is
only invoked by the pose benchmark scripts). Whatever writes `poses.json` is trusted.
That is exactly how the `_v2` Replica bundles feed ground-truth poses
`[repo: dataset_reader.py:346]`. An ARKit reader would use the same door: ARKit VIO
poses go into `poses.json` and **replace both the tier-1 RGB-D odometry and the
tier-2–4 MASt3R path for that source**, at every tier. `pipeline.yaml`'s `pose_method`
would simply not be consulted for such a bundle; no new `pose_method` value is needed
or proposed.

**Why that is attractive `[repo BENCHMARK §1]`.** The measured pose paths cost 4.26 /
4.74 / 26.37 cm ATE (tier-1 odometry on fr2/xyz, fr1/xyz, fr1/desk) and 4.79 / 8.78 /
7.56 cm (MASt3R), with MASt3R fixed at ~120 s per sequence and CPU odometry at
~0.9 s/frame. ARKit's VIO is free at capture time, metric, gravity-aligned, and — on
a static room walkthrough — routinely at the few-centimetre level `[3p, est]`. It
also removes MASt3R from this source's dependency chain, which matters for licensing
(§4.4).

**What it costs the paper — invariant 4.** CLAUDE.md invariant 4: pose numbers come
from TUM only; `_v2` rooms consume GT poses and "must never be reported as a pose
result". A phone bundle is in the same position as a `_v2` bundle: its poses are an
input, not an output, and there is **no ground truth** to score them against. So:

- No ATE may be claimed for a phone scene. The pose section of the paper stays TUM-only.
- If a phone-pose number is ever wanted, it needs its own evaluation, and the honest
  options are limited: (i) report **geometry only** — reconstruction against a
  reference mesh (Polycam's own `raw.glb`, or a laser scan if one exists), never
  trajectory; (ii) an **agreement** check — run MASt3R on the same bundle and report
  the ARKit-vs-MASt3R discrepancy (this is consistency, not accuracy, and must be
  labelled as such); (iii) use Apple's **ARKitScenes** dataset, which ships ARKit
  trajectories with Faro laser-scan ground-truth *geometry* — again a geometry
  evaluation; (iv) a metric-scale sanity check with an object of known dimensions.
  None yields a defensible ATE. `evaluate_scene.py` already supports "pose n/a" via
  `--gt-traj /nonexistent` `[repo: STATUS.md:63-64]`, so a phone scene can be scored
  on recall/precision/F@5cm with the renormalised weights exactly like Replica.

### 3.8 Masks still come from the server

The YOLO11-seg + IoU tracker (`src/perception/detect.py:115-158`) and the SAM2 seam
(`src/reconstruction/sam2_refine.py`) run server-side on bundle frames; the pattern
is `scripts/run_tum_detect.py` `[repo]`. A phone bundle changes nothing here: it
arrives with empty `objects.json` and the detector fills it. The risk is not in the
seam but in its track record: YOLO + SAM2 has been run once, on 100 TUM frames
`[repo: STATUS.md:21-22]`, and the full real-sensor chain has never been run
`[repo: STATUS.md:39-41]`. The first phone bundle would therefore be the first
end-to-end real-sensor run, with all the unknowns that implies (mask temporal
consistency across 600 frames, tracker id switches, crops of partially seen
furniture).

### 3.9 Interim zero-app path: Polycam and Record3D raw exports

**Polycam "raw data" export `[3p: PolyCam/polyform README; Polycam help centre]`.**
Enabled by *Developer mode* in the app; available only on LiDAR iOS devices and only
for LiDAR/ROOM-mode captures. Layout:

```
capture/  raw.glb  thumbnail.jpg  polycam.mp4  mesh_info.json
  keyframes/ images/ corrected_images/ cameras/ corrected_cameras/ depth/ confidence/
```

- `depth/*.png`: "lossless .png format as a single channel image where depth is encoded
  as a 16-bit integer with units of millimeters" — **already the bundle's exact
  encoding**; lower resolution than the images, so `K` must be scaled (§3.5); "maximum
  sensor range is 5 meters".
- `confidence/*.png`: "single-channel 8-bit integer map, where 0 = low, 127 = medium,
  and 255 = high" — the encoding adopted in §3.4.
- `cameras/*.json`: `fx, fy, cx, cy, width, height`, `t_00 … t_23` (3 × 4 row-major
  camera → world), `blur_score`, `timestamp`; "follows the ARKit gravity aligned
  convention": +Y up, −Z initial camera direction. Files are named by the timestamp
  in microseconds → `frame_times.json` = µs / 1e6.
- `corrected_images/` are undistorted; `images/` are the originals.

This is a near one-to-one PerceptionBundle: the reader is a file-renaming,
`K`-scaling, axis-flipping, resampling job. **Gaps, all unverified:** Polycam exports
*keyframes*, not every frame — the density (frames per second of capture) is not
documented and decides whether the gate's angular-coverage bar (90°,
`pipeline.yaml:126`) can be cleared on furniture the way Replica's 200-frame orbits do;
whether Developer mode requires a paid Polycam Pro tier (~$27/month per third-party
2026 comparisons) for each end user; and format drift, since the layout is documented
by a community tool, not a versioned spec.

**Record3D `[3p]`.** `.r3d` archives store per-frame RGB plus depth as LZFSE-compressed
Float32 at 192 × 256 (GitHub issue #7 confirms `192 × 256 × 4` bytes after
decompression), with per-frame intrinsics in the metadata (`perFrameIntrinsicCoeffs`);
the streaming API carries 6-DoF poses. Whether `.r3d` files include poses and
confidence maps was not verifiable from public sources. Reading it needs an LZFSE
decoder in Python (`pyliblzfse`). Record3D is the fallback if Polycam keyframe density
proves too low: it records every frame.

**Verdict.** Polycam raw export is sufficient to prove every technical claim in this
section — gravity-aligned trusted poses, the `conf.png` gate, the resampling choice,
the MASt3R-free pose path, and the first real-sensor end-to-end run — with **zero iOS
code**.

### 3.10 Bandwidth and the job API's archive assumption

Per frame at option (b), 1024 × 768 `[est]`: `rgb.jpg` q90 ≈ 200–300 KB, `depth.png`
(uint16, nearest-upsampled, PNG-compressed) ≈ 100–200 KB, `conf.png` ≈ 5–10 KB → ≈
0.4–0.5 MB per frame.

| Capture | Frames | Archive `[est]` |
|---|---|---|
| 10 fps × 60 s | 600 | ≈ 250–300 MB |
| 30 fps × 60 s | 1 800 | ≈ 0.8–1 GB |
| 60 fps × 60 s (ARKit native) | 3 600 | ≈ 1.6–2 GB |

For scale: the Replica `_v2` orbits are 200 frames at 1200 × 680; TUM fr1 sequences
are 596–798 frames at 640 × 480; fr2/xyz is 3 665 frames `[repo: BENCHMARK §1, §3]`.
Recommendation `[est]`: capture at 10 fps (decimate the 60 Hz stream on-device) and cap
at ~600 frames per upload; the gate metrics "change slowly with viewpoint"
`[repo: scripts/run_assemble.py:120-122 help text]`, so 10 fps loses nothing the gate
can measure.

The job API (`job-api-agent`, not yet merged) takes "multipart: archive, tier"
`[repo: .claude/AGENTS.md §1]`. A phone path therefore needs that endpoint to accept
archives of a few hundred MB, and `security-agent` phase B's size cap and upload
validation must be set with these numbers in mind. A background upload of 300 MB over
Wi-Fi is routine on iOS; over cellular it is not.

### 3.11 iOS platform effort and ownership `[est]`

A minimal capture app is: Swift, `ARWorldTrackingConfiguration` with
`frameSemantics = [.sceneDepth]`, a per-frame writer (JPEG + 16-bit PNG + 8-bit PNG +
one JSON line), a zip step, and a background `URLSession` upload to `POST /api/jobs`
with a status screen polling `GET /api/jobs/{id}` and opening `/jobs/{id}/`. No UI
beyond start/stop/upload. Constraints: an Apple Developer Program membership
(US$99/year), TestFlight for testers, App Store review for the public; LiDAR Pro
devices only (§4.4); landscape lock or orientation handling; thermal throttling on
long captures.

Effort: 3–6 person-weeks for a v1 by someone who already writes Swift; more for
anyone learning ARKit on the job. **Nobody on the current agent roster owns Swift**,
and `.claude/AGENTS.md` already expects the app to live in "likely its own repo".
The app also cannot be finished before the job API exists.

## 4. Business feasibility

### 4.1 Who wants a physics-enabled digital twin of a room

Top-down market figures exist but are not addressable-market figures: 3D scanning
≈ US$4.3 B (2024) → US$7.5 B (2030) `[3p: Grand View Research]`; "digital twin" ≈
US$21 B (2025) → US$150 B (2030) `[3p: MarketsandMarkets]` — the latter is dominated by
industrial asset twins that have nothing to do with living rooms. The segments where
*per-object rigid bodies with mass and material* — Soba's actual output — is the
thing being bought `[est]`:

1. **Robotics / embodied-AI simulation.** Teams building sim-to-real pipelines need
   interiors where objects are separate, collidable, and have plausible mass and
   friction. Today they hand-author these or use synthetic asset libraries. This is
   the clearest fit for the output and for the phone as the capture device (a lab
   engineer walks a room once). Pays per scene or per seat.
2. **Games / XR content.** Interactive replicas of real places for VR, location-based
   entertainment, indie games. Physics is required, browser delivery is a bonus.
3. **Interior / real-estate "interactive staging".** Large audience, but here physics
   is a demo feature; the buyer wants the tour and the floor plan, which Polycam and
   Matterport already sell. Weak fit.
4. **Insurance and facilities inventory.** Object-level inventory of a room with
   dimensions and material; mass is incidental. Possible but crowded.
5. **Education and research.** The paper's own audience; small, zero revenue, high
   citation value.

### 4.2 Competitors — one line each, and what they do not do

| Product | What it does `[3p]` | What it does not do |
|---|---|---|
| **Polycam** | LiDAR, photogrammetry and Gaussian-splat capture; mesh/floor-plan/raw-data export; API and team tools; Pro ≈ US$27/month. | No per-object segmentation into rigid bodies, no mass/material, no physics. Exports one fused mesh (and a floor plan). |
| **Matterport** | Pro3 camera (US$5,999) plus phone capture; hosted "digital twin" tours, dollhouse view, measurements; plans free → US$309/month. | No object-level geometry, no physics, no offline/browser-only viewer; a hosted SaaS around tours. |
| **Scaniverse** (Niantic) | Free, on-device LiDAR/photogrammetry/splat scanning, fast. | Single fused mesh/splat; no objects, no physics, no pipeline hooks. |
| **Luma AI** | Free radiance-field/splat capture; company has pivoted to generative video (Genie sunset 2026-01-01); capture app effectively unmaintained. | Splats only; no mesh per object, no physics. |
| **Kiri Engine** | Cloud photogrammetry, clean print-ready object meshes. | Object scanning, not rooms; no physics. |
| **RoomPlan-based apps** (magicplan, Canvas, …) | Semantic room layouts and floor plans from Apple's RoomPlan. | Boxes and walls, no surface geometry of objects, no physics. |
| **Meta Hyperscape** (Quest) | Room capture into Gaussian splats for VR viewing. | Headset-only, splat-only, no objects, no physics `[est]`. |

Nobody in this list emits what `scene.json` v2.0 contains: N rigid bodies, each with
a mesh, convex colliders, mass, material, friction and restitution, in a file a browser
tab simulates with no backend `[repo: spec/scene.schema.json; README.md]`.

### 4.3 Soba's differentiation

- **Per-object rigid bodies** with estimated mass and material — measured: on
  calibrated furniture classes 80 % of predicted masses fall within 2× of the real
  weight; VLM material accuracy 98 % on YCB `[repo: BENCHMARK §2]`.
- **Measurement-first geometry**: the confidence gate keeps real TSDF surfaces where
  the camera saw them and regenerates only the unseen tail — gated routing gives the
  best surface fidelity of any strategy (Chamfer 5.6 cm, F@5cm 0.68) `[repo: BENCHMARK §4]`.
- **No backend at view time**: Three.js + Rapier WASM from a static `dist/` `[repo]`.
- A phone source adds **gravity-aligned, metric, free poses** and removes the
  non-commercial MASt3R dependency from the path (§4.4).

### 4.4 Unit economics per scene `[est — no cost record exists yet]`

`runpod-orchestration-agent` is chartered to produce a per-job cost record; it has not
merged. The figures below combine repo-measured runtimes with public RunPod pricing
`[3p: runpod.io/pricing and 2026 comparisons — pod RTX 4090 US$0.69/h, A100 80 GB
US$1.39/h; serverless RTX 4090 ≈ US$1.10/h, A100 ≈ US$2.72/h, billed per second
active]` and Claude list prices `[3p: claude-opus-4-8 US$5 / US$25 per MTok in/out;
claude-haiku-4-5 US$1 / US$5]`.

Repo-measured runtimes `[repo]`: MASt3R ≈ 120 s per sequence, fixed (BENCHMARK §1);
CoACD median 26 s (YCB) / 55 s (ABO) per object on CPU, worst 6–9 min (§2); a tier-4
assembly ≈ 35–40 min wall on the pod for 9 generated + 3 fused objects
(`docs/log/2026-07-02-part3-runpod-hunyuan-live.md`); per-object regeneration ≈ 3 min
(`docs/log/2026-07-05-part1-tiers-rooms-1-4-gen-cache.md`); Hunyuan3D 2.1 needs ≥ 16 GB
GPU, TripoSG runs on 8 GB (`deploy/runpod/README.md`).

| Tier | Host GPU time | Generative calls | VLM call | Total per scene `[est]` |
|---|---|---|---|---|
| 1 | ≈ 10 min on 4090 pod ≈ US$0.12 (odometry replaced by ARKit → mostly generation + boxes) | TripoSG ≈ 1–2 min/object on serverless 4090 ≈ US$0.02–0.04 × ~10 | none (lookup) | **≈ US$0.4–0.6** |
| 2 | ≈ 15 min on 4090 pod ≈ US$0.17 (no MASt3R on a phone bundle) | TripoSG, only the generative band (≈ 3–6 objects) | 12 crops/call ≈ 20 k input + 2 k output tokens ≈ US$0.15 (Opus 4.8) or ≈ US$0.03 (Haiku 4.5) | **≈ US$0.5–1** |
| 3 / 4 | ≈ 40 min on 4090 pod ≈ US$0.46 | Hunyuan3D 2.1 on serverless A100 ≈ 3 min/object ≈ US$0.14 × ~9 ≈ US$1.2 | as tier 2 | **≈ US$2–2.5** |

Hidden costs: an on-demand host pod bills while idle (`deploy/runpod/README.md`
"Cost hygiene"); a serverless cold start loads ~10 GB of Hunyuan3D weights; the
CoACD long tail (6–9 min for wire-frame furniture) is CPU time on the host pod; and
storage, since there is "no job or output retention policy yet"
`[repo: .claude/AGENTS.md §1]`. At these numbers a phone-captured tier-2 scene costs
about a dollar of compute — cheap enough for a per-scene price and far below Matterport's
hardware-plus-subscription model, but only if the pod is not left running.

### 4.5 Risks

| Risk | Detail | Severity `[est]` |
|---|---|---|
| **Apple platform dependence** | ARKit APIs, App Store review, yearly developer fee, iOS version drift. No Android equivalent with a comparable depth sensor in the mainstream. | medium |
| **LiDAR only on Pro devices** | iPhone 12 Pro → 17 Pro (Pro/Pro Max only) and iPad Pro 2020+; no base, Plus, Air, mini or SE model has LiDAR `[3p]`. The addressable device base is the premium tier only. | medium |
| **Privacy of room scans** | Uploads are photographs and geometry of people's homes. GDPR applies to an EU operator; the repo has no retention policy `[repo: AGENTS.md]`; `security-agent` is chartered for upload validation and auth but not for a data-protection policy. Must exist before any public upload path. | high |
| **Hunyuan3D 2.1 licence — territory** | "Tencent Hunyuan 3D 2.1 Community License Agreement": Territory is "the worldwide territory, excluding the territory of the European Union, United Kingdom and South Korea"; separate licence above "1 million monthly active users"; attribution notice required `[3p: LICENSE file]`. **Soba is developed in Slovenia (EU): tiers 3–4 as configured cannot be offered commercially from the EU under this licence.** | high for tiers 3–4 |
| **MASt3R licence** | CC BY-NC-SA 4.0, non-commercial, plus the training-dataset licences `[3p: naver/mast3r]`. Tiers 2–4 pose on dataset/OAK sources is non-commercial. **An ARKit-pose source bypasses MASt3R entirely** (§3.7) — a concrete business argument for this ADR. | high today; removed by this path |
| **YOLO11 licence** | Ultralytics: AGPL-3.0 by default, "Enterprise License" required to use it "without open-sourcing your entire project" `[3p: ultralytics.com/license]`. Applies to every source, phone or not. | medium |
| **TripoSG** | MIT (`Copyright (c) 2025 VAST-AI-Research and contributors`) `[3p: LICENSE file]`. No restriction. | none |
| **SAM 2** | Apache-2.0 `[3p: facebookresearch/sam2 LICENSE]`. No restriction. | none |
| **PatchComplete, CoACD, MASt3R checkpoints' dataset terms** | Not verified in this ADR. | unknown |
| **Claude API dependency** | Tiers 2–4 physics need `ANTHROPIC_API_KEY`; lookup fallback exists `[repo: pipeline.yaml:80-91]`. | low |
| **Third-party export drift** (option B) | Polycam's raw layout is documented by a community tool, not a versioned contract; Developer-mode availability could change. | medium |
| **First real-sensor run** | §3.8: the mask seam has never run end to end on real footage. Any of tracker, SAM2, crop staging or ICP placement may need real-data fixes before a phone scene looks good. | medium |

## 5. Options considered

### A. Build a native ARKit capture app now

- **Pros.** Every frame at 60 Hz with depth, confidence and timestamps; control over
  fps, resolution and the upload UX; direct `POST /api/jobs`; no third-party paywall;
  the product story is complete.
- **Cons.** 3–6 person-weeks of Swift by someone not on the roster, in a separate
  repo, plus App Store overhead; blocked on the job API and on the security phase B
  upload limits; builds the most expensive component before the cheapest experiment
  (§3.9) has shown that the pipeline copes with real handheld footage at all.

### B. Accept Polycam / Record3D raw exports first; app later, if justified

- **Pros.** Zero iOS code; a Python reader of a few hundred lines with unit tests;
  proves in days the things this ADR can only argue: gravity-aligned trusted poses,
  the `conf.png` gate, the resampling choice, MASt3R-free poses, and the first
  end-to-end real-sensor run; answers the reviewer's handheld-capture request; the
  same reader becomes the reference implementation the app must match byte-for-byte.
- **Cons.** Keyframe density unknown; a possible Polycam Pro paywall for end users;
  a manual export-then-upload step; dependency on an undocumented layout.

### C. Do not pursue phone capture

- **Pros.** No new surface area; the roster stays on the hardening track.
- **Cons.** No real handheld evaluation for the paper; the hosted service has no
  input other than research datasets; the MASt3R non-commercial problem stays on the
  only pose path; competitors keep the capture side entirely.

## 6. Recommendation

**Option B, gated.** Approve a Polycam-raw reader plus fixture as the first and only
slice (§8); run one real room through tier 2 on the pod; evaluate geometry only
(against Polycam's `raw.glb`, or a laser mesh if one is available) with pose marked
n/a; and decide on the native app (A) only after that run, on evidence.

Evidence that would change the recommendation:

- Polycam keyframe density too low to clear the 90° coverage bar on ordinary furniture
  → switch the reader to Record3D (every frame) or go to A directly.
- Developer-mode raw export locked behind a per-user paid tier → weakens B as a
  product path (still fine for the paper) and strengthens A.
- ARKit poses disagreeing with MASt3R by more than ~10 cm on a static room → the
  "free, good poses" premise is wrong and pose evaluation becomes a real problem.
- A decision to target non-Apple devices → C, or a different ADR.
- A resolution of the Hunyuan3D territory problem (a different tier-3/4 model or a
  negotiated licence) does not change B but decides whether tiers 3–4 are part of the
  offer at all.

## 7. Decision record

| | |
|---|---|
| **Decision** | ☐ A ☐ B ☐ C ☐ other: ______________________ |
| **Approved by** | ______________________ |
| **Date** | ______________________ |
| **Conditions / notes** | ______________________ |

Until this table is filled in, `lidar-capture-agent` remains blocked (§1).

## 8. Consequences if approved (option B)

The smallest first slice for `lidar-capture-agent`, on its own branch from `develop`:

**Must do.**
1. `src/perception/polycam_reader.py` — `PolycamRawReader(capture_dir).to_bundle(out,
   grid=(1024, 768))`: parse `keyframes/cameras/*.json`; scale `fx, fy, cx, cy` to the
   grid; build `T_world_camera` from `t_00…t_23` and right-multiply `diag(1,-1,-1,1)`;
   nearest-resample `depth/*.png` (already uint16 mm) and `confidence/*.png` (0/127/255)
   to the grid; resize `corrected_images/*` to the grid, JPEG q90; `frame_times.json`
   from the microsecond filenames; `manifest.source = "polycam"`; empty `objects.json`.
   Pure-logic parts (json → K, transform assembly and flip, conf mapping, filename →
   seconds) import-light and testable without images, mirroring `dataset_reader.py`'s
   split `[repo: dataset_reader.py:7-10]`.
2. `tests/perception/test_polycam_reader.py` with a **synthetic fixture generated in
   the test** (no binary blobs in git): a floor plane and a box rendered into a 256 × 192
   depth map with a known ARKit-convention pose; assert the back-projected floor lands at
   minimum Y in the bundle world (this is the test that proves §3.3), the intrinsics
   scaling, the conf mapping, and that `PerceptionBundle.open` round-trips.
3. `scripts/build_polycam_bundle.py` driver (`PYTHONPATH=src`), and a
   `--conf-min` pass-through in the assembly driver for `source == "polycam"` only if it
   can be done without touching the gate or TSDF constants; otherwise leave `conf_min`
   for a second slice.
4. A dated `docs/log/` entry and one line in `STATUS.md` ("phone-capture reader:
   Polycam raw, untested on real data on this box") per CLAUDE.md.

**Must NOT do.**
- Touch `spec/scene.schema.json` (invariant 1), any threshold, density, solidity or
  tier block in `config/pipeline.yaml` (invariant 2), `src/scene/ground.py`, the depth
  constants in `observed_cloud.py` / `tsdf.py`, or the `bundle.py` layout.
- Add a new `pose_method` to the tiers; trusted poses go through `poses.json` exactly
  as Replica's do.
- Report any pose number for a phone bundle (invariant 4), or any GPU result not
  actually run on the pod (invariant 7).
- Start the iOS app, a monocular-depth path, or a Record3D reader in the same slice.
- Change `src/server.py` or the job API (owned by `job-api-agent`).

## 9. Sources (all accessed 2026-09-11)

Repository (`develop` @ `9cbd012`): `CLAUDE.md`, `.claude/AGENTS.md`, `README.md`,
`STATUS.md`, `BENCHMARK.md` (§1, §2, §3, §4), `config/pipeline.yaml`,
`src/perception/bundle.py`, `src/perception/dataset_reader.py`,
`src/perception/detect.py`, `src/reconstruction/slam.py`, `src/reconstruction/tsdf.py`,
`src/reconstruction/observed_cloud.py`, `src/scene/ground.py`, `scripts/run_assemble.py`,
`deploy/runpod/README.md`, `docs/log/2026-07-02-part3-runpod-hunyuan-live.md`,
`docs/log/2026-07-05-part1-tiers-rooms-1-4-gen-cache.md`.

Apple documentation:
- ARFrame.sceneDepth — https://developer.apple.com/documentation/arkit/arframe/scenedepth
- ARFrame.smoothedSceneDepth — https://developer.apple.com/documentation/arkit/arframe/smoothedscenedepth
- ARDepthData — https://developer.apple.com/documentation/arkit/ardepthdata
- ARConfidenceLevel — https://developer.apple.com/documentation/arkit/arconfidencelevel
- ARCamera.transform — https://developer.apple.com/documentation/arkit/arcamera/transform
- ARCamera.intrinsics — https://developer.apple.com/documentation/arkit/arcamera/intrinsics
- ARConfiguration.WorldAlignment.gravity — https://developer.apple.com/documentation/arkit/arconfiguration/worldalignment-swift.enum/gravity
- Displaying a point cloud using scene depth (sample) — https://developer.apple.com/documentation/arkit/displaying-a-point-cloud-using-scene-depth
- RoomPlan — https://developer.apple.com/documentation/roomplan
- Apple Newsroom, iPad Pro with LiDAR (2020-03-18) — https://www.apple.com/newsroom/2020/03/apple-unveils-new-ipad-pro-with-lidar-scanner-and-trackpad-support-in-ipados/
- WWDC20 "Explore ARKit 4" — https://developer.apple.com/videos/play/wwdc2020/10611/

Third-party, technical:
- it-jim, "iPhone 12 Pro LiDAR: how to get and interpret data" — https://www.it-jim.com/blog/iphones-12-pro-lidar-how-to-get-and-interpret-data/
- PMC, smartphone distance-estimation accuracy study (sceneDepth 256 × 192 @ 60 fps; error figures) — https://pmc.ncbi.nlm.nih.gov/articles/PMC10939328/
- SimplyWise, iPhone LiDAR accuracy field guide — https://www.simplywise.com/blog/iphone-lidar-construction-accuracy/
- LiDAR device list — https://caseadri.com/roomkit/guides/which-iphones-have-lidar/ and https://www.simplywise.com/blog/which-iphones-have-lidar/
- PolyCam/polyform README (raw data layout) — https://github.com/PolyCam/polyform
- Polycam help centre, "How to extract raw data and what is included" — https://learn.poly.cam/hc/en-us/articles/38276871185044-How-to-Extract-Raw-Data-and-What-Is-Included (returned HTTP 403 to automated fetch; content confirmed via search excerpt)
- Record3D depth format (issue #7) — https://github.com/marek-simonik/record3d/issues/7 ; features — https://record3d.app/features
- Apple ARKitScenes, depth question (issue #14) — https://github.com/apple/ARKitScenes/issues/14

Licences:
- TripoSG LICENSE (MIT) — https://github.com/VAST-AI-Research/TripoSG/blob/main/LICENSE
- Hunyuan3D 2.1 LICENSE (Tencent Hunyuan 3D 2.1 Community License Agreement) — https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1/blob/main/LICENSE
- MASt3R (CC BY-NC-SA 4.0) — https://github.com/naver/mast3r
- Ultralytics licensing (AGPL-3.0 / Enterprise) — https://www.ultralytics.com/license
- SAM 2 LICENSE (Apache-2.0) — https://github.com/facebookresearch/sam2/blob/main/LICENSE

Business:
- RunPod pricing — https://www.runpod.io/pricing ; 2026 comparisons — https://www.thundercompute.com/blog/runpod-pricing-vs-thunder-compute , https://hackceleration.com/labs/runpod-pricing
- Claude API list prices — Anthropic pricing table as cached in the `claude-api` reference (2026-06-24)
- Matterport plans and Pro3 — https://matterport.com/plans , https://www.thefuture3d.com/blog/matterport-pricing-guide-2026/
- Luma AI status — https://radiancefields.com/platforms/luma-ai
- App comparisons 2026 — https://swiftwand.com/en/smartphone-3d-scanning-app-comparison-2026-en/ , https://www.kiriengine.app/blog/best-lidar-3d-scanner-apps-iphone-2026 , https://www.skyebrowse.com/news/posts/polycam-vs-scaniverse
- 3D scanning market — https://www.grandviewresearch.com/industry-analysis/3d-scanning-industry ; digital twin market — https://www.marketsandmarkets.com/Market-Reports/digital-twin-market-225269522.html
