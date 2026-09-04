_Moved verbatim from the root STATUS.md on 2026-09-04. Written before the 2026-07-13 rename from vid2sim-v2 to Soba: substitute `soba` / `SOBA_*` for `vid2sim-v2` / `VID2SIM_*` in any command you copy. Paths under `~/projects/vid2sim/` refer to the GPU machine of that time._

## ⬆️ LATEST SESSION (2026-07-04): tier 1 wired, Steps 3/7/8 REAL, textures, drop-garbage
All committed on `fix/phase3-pose-and-eval`. 210 tests pass. Room-3 (office_3)
four-tier build + websites :8001-:8004 (serve.py per tier).
- **Tier 1 runnable** (`--tier 1`): no gate, everything generative, BOX colliders
  (AABB half_extents, no CoACD) — assembler `collider="box"` path, frontend already had it.
- **Crop quality overhaul** (`crop_stage`): occlusion-aware best-frame scoring
  (depth-based cover detection), soft world-height factor (top-down views generate
  slabs), Telea INPAINTING of occluder pixels (cover = hull pixels NOT clearly
  behind the local surface; genuine see-through openings stay), tiny mask scraps
  dropped. Fixed the holey-table + floating-fragment artifacts the user flagged.
- **Step 8 LIVE (`scene/vlm_claude.py`)**: batched Claude physics call
  (output_config.format), annotated crops w/ metric ruler, refusal/max_tokens
  guards, clamps. Activates on ANTHROPIC_API_KEY; lookup fallback otherwise.
- **Phase 8 (`reconstruction/icp_align.py`)**: FPFH rotation-first + per-axis
  metric scale (<30%-coverage axes dropped) + point-to-plane refine + quality
  gates (incl. Z-H support-height). Wired into both regenerate() paths; on
  room-scan slivers the gates correctly refuse -> class-prior coarse fallback
  (honest provenance fpfh_icp/coarse_aligned).
- **Step 3 MASt3R REAL (`slam.py`)**: metric ckpt, swin pairs, dust3r global
  alignment, metric-scale solve vs sensor (M1), SE(3) interpolation. TUM fr1/xyz
  ATE 4.55 cm (24-anchor cap on 8 GB; odometry baseline 3.66 cm). Tier 4 falls
  back to MASt3R with a log (ORB-SLAM3 stays Phase-14-only-if-needed).
- **Hunyuan TEXTURE stage works on the pod** (VID2SIM_HUNYUAN_PAINT=1): paint ->
  bake UV texture to VERTEX COLORS -> flows through the whole pipeline; browser
  shows a purple fabric couch. Pod fixes recorded in setup_hunyuan3d.sh
  (python3-dev, trust_remote_code, xatlas/pytorch_lightning/realesrgan, bpy stub,
  cfg paths pinned absolute). **CRITICAL pod fix: model caches moved to
  /workspace/cache with symlinks from /root/.cache — container disk was 91% full
  and weights now SURVIVE pod stops.** Paint remesh leaves seams unwelded ->
  weld in _paint_hunyuan (else _looks_shattered falsely rejects ~600-component
  meshes — cost tier 3 its couch+tables once).
- **DROP-GARBAGE POLICY (user directive: "don't create magic")**: _too_thin
  (enc/hull < 3% = bent-sheet 'furniture'; real worst-case chair 4.1%) and
  _class_dims_ok now checks WIDTH (panel 'chairs' out). Expect fewer, better
  objects. Bottles: #94 never croppable, #92 blob rejected by dims — honest drops.
- **Scenes**: t1/t2 local TripoSG; t3/t4 pod Hunyuan+paint (11 objects each,
  couch+main table+bottle back, textured). Pod: slow_tomato_gull now at
  root@213.192.2.110 -p 40066 (STOP IT when done — bills hourly); handler via
  /workspace/start_handler.sh + local tunnel `ssh -N -L 8777:127.0.0.1:8777`;
  engine env: RUNPOD_API_KEY=dummy RUNPOD_GEN_ENDPOINT_ID=dummy
  VID2SIM_RUNPOD_URL=http://127.0.0.1:8777/runsync VID2SIM_RUNPOD_TIMEOUT=1200.
- **GOTCHA: do NOT run two GPU builds concurrently on the 4060** — T2 TripoSG
  died to OOM against T3's local PatchComplete; rerun solo.
- **Open**: user reviews the 4 room-3 sites, then the other 7 rooms (need dense
  re-streams); de-overlap pass (objects interpenetrate in the desk cluster);
  TUM end-to-end; CLI (Phase 12); serverless deploy; capture.py.
