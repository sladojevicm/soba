# 2026-09-14 — RGB-only ingest, step 1: MASt3R export + VideoReader (no API/worker changes)

**Why.** A plain phone video (no depth channel) cannot enter the pipeline at any
tier: upload validation requires `depth.png`, every tier back-projects depth
into the observed cloud, the gate never runs without a cloud (objects are dropped
as `cloud_too_small`), and `ground.ground_y` raises on zero points. Yet MASt3R
needs only RGB and already computes dense per-frame depth that `Mast3rEstimator`
discarded after the metric-scale solve. Step 1 keeps everything downstream
unchanged and instead produces the bundle the pipeline already expects.

**What changed (`src/reconstruction/slam.py`).**
- `resolve_metric_scale(bundle, sampled, pred_depths)`: the fix-M1 sensor scale is
  solved only when every sampled frame has a `depth.png`; otherwise scale 1.0 from
  the metric checkpoint, with a WARNING. `estimate()` no longer crashes on a
  depth-less bundle.
- `Mast3rEstimator.estimate(bundle, export_root=None)`: with `export_root`, the
  dense output is written as a NEW anchor-only bundle via `export_anchor_bundle`:
  `frames/k/depth.png` (uint16 mm, scaled by the same factor as the poses),
  `conf.png` (dust3r confidence mapped by `conf_to_u8`, so conf 3 = 170 clears the
  tsdf gate at 150), `poses.json` (world = anchor 0), `intrinsics.json` from the
  solved focal rescaled to the RGB size, rgb/objects/masks copied verbatim,
  manifest `frame_count = K`, `frame_times.json` subset. Anchor-only is deliberate:
  only anchors have a depthmap and `tsdf._object_frames` reads depth for every
  masked frame.
- `dust3r_crop_geometry` / `crop_map_to_frame` replicate `load_images(size=512)`
  (long edge 512, centre crop to multiples of 16) so the 512-res depthmap lands on
  the right RGB pixels; uncovered border pixels are 0 (invalid).
- `scene_outputs(scene)` pulls poses/depthmaps/conf/focals/principal points out of
  the dust3r scene duck-typed, which is what the unit test stubs.
- `solve_metric_scale` imports cv2 only when it must resample.

**New `src/perception/video_reader.py`.** `VideoReader(path).to_bundle(out,
stride, max_frames)`: OpenCV decode → `frames/NNNNN/rgb.jpg`, `frame_times.json`,
manifest `source="video"` with the effective fps, and PLACEHOLDER intrinsics from a
nominal 70° HFOV (replaced by the MASt3R export). No depth, poses, objects or masks:
this bundle is an intermediate artefact and still fails upload validation and
`run_assemble.py` by design until step 2 wires the "preparing" stage.

**Tests.** `tests/reconstruction/test_slam_export.py` (pure: crop geometry incl.
portrait/odd sizes, conf mapping vs the tsdf gate, stubbed scene, scale guard both
ways), `tests/reconstruction/test_slam_export_writer.py` and
`tests/perception/test_video_reader.py` (need cv2: skip on this box, run in CI;
verified locally in an ephemeral `uv run --with opencv-python-headless` env,
32 pass). Full suite here: 414 pass / 39 fail / 7 errors (baseline 401 / 40 / 7;
the -1 failure is `test_solve_metric_scale_recovers_ratio`, which no longer needs
cv2).

**Not verified here (invariant 7).** The real `estimate(..., export_root=)` call
needs torch + the MASt3R checkout: the dust3r attribute names (`im_conf`,
`get_focals`, `get_principal_points`) are read defensively and must be confirmed
on the GPU machine with
`PYTHONPATH=src python -c "from reconstruction.slam import Mast3rEstimator; from perception.bundle import PerceptionBundle; Mast3rEstimator().estimate(PerceptionBundle.open('bundles/<rgb_only>'), export_root='bundles/<rgb_only>_mast3r')"`.

**Step 2 (separate PR).** GPU-only worker stage "preparing" (YOLO+SAM2 →
objects/masks, then the MASt3R export), the "video" upload layout that skips the
depth/intrinsics checks, OpenAPI/docs, the STATUS.md notes (GPU end-to-end; the
RGB-only path rests on MASt3R for pose AND depth, CC BY-NC-SA, non-commercial),
and the hard gate before any demo: OAK vs phone capture of the same room at tier 2,
object count / class-size plausibility only, no BENCHMARK.md row (invariants 3, 4).
