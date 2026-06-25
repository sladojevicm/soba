# vid2sim-v2

Rewrite of [vid2sim](https://github.com/Vector-Space-Moggers/vid2sim) — a pipeline that turns a short depth-camera video of a room into an interactive, browser-based physics simulation.

> **Status:** Empty scaffold. Working functionality from the original is being migrated and rewritten here incrementally. Until then, the original repo remains the source of truth for behavior.

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
