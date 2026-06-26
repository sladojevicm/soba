# vid2sim-v2

Rewrite of [vid2sim](https://github.com/Vector-Space-Moggers/vid2sim) — a pipeline that turns a short depth-camera video of a room into an interactive, browser-based physics simulation.

> **Status:** Build in progress against `PLAN_FINAL_FINAL` (v11). The plan's
> Build Order is followed phase by phase.
>
> **Done so far (first three build steps):**
> - ✅ **Step 1 — Foundation:** `scene.json` **v2.0** contract (Contract 3) — schema,
>   example, validator, tests (see [`docs/scene-spec.md`](docs/scene-spec.md)); plus
>   [`config/pipeline.yaml`](config/pipeline.yaml) (tier params, class gates, solidity,
>   density, physics lookup, ground default) and
>   [`config/coco_class_map.yaml`](config/coco_class_map.yaml) (dataset→COCO, fix T2).
> - ✅ **Step 2 — Data input:** `perception/bundle.py` (Contract-1 PerceptionBundle I/O)
>   and `perception/dataset_reader.py` (TUM RGB-D reader + COCO label mapping).
> - ✅ **Step 3 — Pose estimation:** `reconstruction/slam.py` (RGB-D odometry; MASt3R /
>   ORB-SLAM3 interfaces) and `reconstruction/observed_cloud.py` (Step 4 Part A
>   back-projection — the tier-independent ICP/gate reference).
>
> Heavy host-side deps (Open3D, OpenCV, trimesh) live behind the `recon` extra; the
> contract and data-input layers install and test without them.

## Goal

Preserve what works in the original pipeline while rewriting the implementation for clarity and maintainability.

The original is organized as four bounded contexts communicating through a single typed `scene.json` contract:

| Stage | Context | Responsibility |
|---|---|---|
| A | Perception | Camera capture, depth fusion, segmentation |
| B | Reconstruction | Mesh completion (image-to-3D), ICP alignment |
| C | Scene Assembly | Physics inference (VLM), convex decomposition, exporters |
| D | Presentation | Browser viewer (Three.js + Rapier WASM) |

The `scene.json` schema is the cross-context contract and should remain the stable boundary through the rewrite.

## Migration notes

- Reference implementation: `../vid2sim` (local clone) / [upstream](https://github.com/Vector-Space-Moggers/vid2sim).
- Stage B fallback chain in the original: Hunyuan3D 2.1 (primary, RunPod) → TripoSG 1.5B → SF3D (local emergency) → stub. (Note: the upstream README markets SF3D as primary, but the code/ADRs treat it as the last-resort fallback.)

## License

TBD.
