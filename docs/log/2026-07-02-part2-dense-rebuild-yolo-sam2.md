_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## ⬆️ EARLIER (2026-07-02, part 2): dense rebuild AUDITED, YOLO+SAM2 REAL
All committed + pushed on `fix/phase3-pose-and-eval` (through `1ae544a`).
- **Dense office_3 bundle built**: ALL 2000 frames (stride 1) at
  `~/projects/vid2sim/data/replica/bundles_dense/office_3` (1.2 GB). The old
  100-frame bundle stays at `bundles/office_3`. Disk was 96% full — deleted the
  superseded room_0 artifacts (demo zip + extracted/ + bundle_room0, ~9 GB
  reclaimed, STATUS said deletable; `bundles/room_0` is the replacement).
- **Data-loss audit (scripts/audit_data_loss.py) ANSWERED the June-28 questions:**
  (1) the depth gate [400,8000]mm loses ZERO pixels on Replica — exonerated;
  (2) the Open3D default weight threshold (~3 obs/voxel) cost 2-10% of TSDF
  vertices on 100 frames (table worst = the thin legs) and the dense rebuild
  FIXES it: w3/w1 goes to ~1.00 on all four audited objects (couch/table/
  chair25/chair9), table +14% vertices; (3) density buys OBSERVATIONS per
  voxel, not coverage — unique 5mm cells only +14-39%, dims unchanged, the
  ≤123° room-scan ceiling stands. JSON: out/audit_dense{,_tsdf}.json.
- **YOLO detection EXISTS now (`src/perception/detect.py`)**: ultralytics
  YOLO-seg (yolo11s-seg) + the plan's Step-1 IoU tracker (class-gated greedy
  match >0.4, retire after 5) as a `detector` callable for
  `TUMReader.to_bundle`. Injectable infer seam, 7 unit tests, RGB→BGR flip is
  load-bearing (ultralytics assumes BGR numpy input).
- **Real SAM2 RAN for the first time** (Phase-4 validation): fr1/xyz, 16 stable
  tracks (keyboard/tv/book/chair/cup/mouse) refined over 100 frames, ~1.5 fps
  on the 4060 (4.4 GB VRAM), crops staged. Two latent bugs found+fixed by the
  real run: Sam2VideoPredictor needs **bf16 autocast** (dtype crash without),
  and refine_masks gained a `track_ids` filter (YOLO 1-frame flicker tracks —
  32 of 48! — would each cost a SAM2 video pass). Driver script:
  `scripts/run_tum_detect.py` (weights: ~/projects/vid2sim/models/
  sam2.1_hiera_large.pt; yolo auto-downloads). Bundle:
  `data/tum/bundle_f1xyz_yolo`.
- **run_assemble gained `--gate-stride N`** (gate scores every Nth frame;
  TSDF still fuses all) — the dense bundle's gate is otherwise ~20x the
  100-frame cost (it re-reads depth per object). Default 1 = old behaviour.
- **Dense reassembly**: `out/scene_office_3_dense` (tier 2, gate-stride 10) —
  see the scene section / next-session note for the result.
- **Next**: TUM pipeline continuation (poses on bundle_f1xyz_yolo → cloud →
  gate → assemble = first REAL-SENSOR end-to-end scene); gate caching; CLI
  (Phase 12); icp_align.py (Phase 8).
