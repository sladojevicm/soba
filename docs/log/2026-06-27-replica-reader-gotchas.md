_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## What an earlier session did (2026-06-27)
Added **`ReplicaReader`** to `dataset_reader.py` and validated the **object/segmentation
path on real ground-truth data** (the thing TUM can't test, because TUM has no labels).
- Replica ships **perfect GT instance masks**, so YOLO+SAM2 are skipped — the GT masks are
  written straight into the bundle as `objects.json` + `mask_{track_id}.png`.
- Verified end-to-end on room_0: **16 COCO objects** (couches/chairs/dining tables/books),
  couch observed cloud = **2.34×0.90×1.06 m** (real size). 56 tests still pass.
- **Constants were verified against the real files, NOT trusted from docs** (the docs were
  wrong on two): see the Replica gotchas below.

## Replica gotchas (hard-won; all encoded in `ReplicaReader`)
1. **Depth is already MILLIMETRES** (`metres = png/1000`). The commonly-cited iMAP
   `/6553.5` is WRONG for this vMAP render (it would make the whole room < 0.77 m).
2. **`traj_w_c.txt` is camera→world** = our `T_world_camera` convention — use directly,
   NO inversion. (A doc summary claimed world→camera; it's wrong.)
3. **The Replica world is Z-UP**; our contract is Y-up (`scene.json up_axis "y"`,
   `observed_cloud` ground = min Y). `ReplicaReader` rotates Z→Y via
   `_REPLICA_ZUP_TO_YUP` (a −90° rotation about X: (x,y,z)→(x,z,−y)). Verified: scene
   height then lands on Y (~2.80 m ceiling), floor at min Y.
4. Intrinsics: 1200×680, hfov 90 → fx=fy=600, cx=599.5, cy=339.5.
5. Instance IDs are globally consistent across frames → each IS a `track_id`. Object
   class = majority `semantic_class` id over the instance's pixels → `render_config.yaml`
   name → COCO via `coco_class_map.yaml` (already has a `replica:` section). Structural
   classes (wall/floor/window…) map to "default" and are dropped.
6. `render_config.yaml` embeds numpy-pickled blobs → needs `yaml.unsafe_load` (the reader
   only consumes the plain `classes` list).
