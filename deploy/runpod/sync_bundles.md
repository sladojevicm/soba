# Copying the demo-grade `_v2` bundles from the 4060 to the pod

The pod's `office_3` bundle is the vMAP room scan: the coverage ceiling of
about 123° (`docs/log/2026-06-27-all-8-scenes-tsdf-gate.md`) routes 10 of 14
objects to generative. The paper's numbers (BENCHMARK.md §3) came from the
`_v2` orbit bundles rendered by `scripts/render_replica.py` on the 4060 —
custom trajectories that circle each object, so most objects route to
completion. `room_2` at tier 2 scored 89/100. Those bundles live only at
`~/soba/data/replica/bundles/<room>_v2` on the 4060. This page moves one to
the pod. **The copy is a manual step; nothing here runs it.**

## Size estimate

A `_v2` bundle is 1800 frames (`render_replica.py --frames 1800`, default) of
1200×680 RGB JPEG + uint16 depth PNG + per-object masks. Measured reference:
the 2000-frame dense `office_3` bundle was 1.2 GB
(`docs/log/2026-07-02-part2-dense-rebuild-yolo-sam2.md`). Expect **~1 GB per
`_v2` room**, ~0.7 GB as a zip (JPEGs do not compress; PNG depth does a
little). Check before copying:

```bash
# on the 4060
du -sh ~/soba/data/replica/bundles/*_v2
```

## Copy: 4060 → pod

The pod exposes SSH (RunPod → pod → Connect → "SSH over exposed TCP" gives the
host, port and key). Two options; `rsync` resumes if the link drops, which
matters for a 1 GB transfer over a home uplink.

```bash
# on the 4060 — fill in from the pod's Connect panel
POD_HOST=<ip>; POD_PORT=<port>; ROOM=room_2
rsync -avz --partial --progress -e "ssh -p $POD_PORT -i ~/.ssh/id_ed25519" \
  ~/soba/data/replica/bundles/${ROOM}_v2/ \
  root@$POD_HOST:/workspace/bundles/${ROOM}_v2/
# scp fallback (no resume):
# scp -P $POD_PORT -r ~/soba/data/replica/bundles/${ROOM}_v2 root@$POD_HOST:/workspace/bundles/
```

Alternative with no SSH from the 4060: `zip -r ${ROOM}_v2.zip ${ROOM}_v2` on
the 4060, upload the zip through the pod's JupyterLab (port 8888, the upload
arrow in the file browser, into `/workspace/bundles/`), then
`cd /workspace/bundles && unzip ${ROOM}_v2.zip` on the pod.

Verify on the pod:

```bash
ls /workspace/bundles/room_2_v2/            # manifest.json intrinsics.json poses.json frames/ frame_times.json
ls /workspace/bundles/room_2_v2/frames | wc -l   # 1800
```

`poses.json` must be present: `_v2` bundles consume ground-truth poses and
never run MASt3R (CLAUDE.md invariant 4 — their ATE is 0 by construction and
is never a pose result).

## Run it through the job API on the pod

```bash
cd /workspace/soba && export SOBA_TRIPOSG_HOME=/workspace/TripoSG   # + ANTHROPIC_API_KEY, see docs/gpu-validation.md
pkill -f '[s]cripts/serve.py'                                        # if a KEEP=1 API is still up
BUNDLE_DIR=bundles/room_2_v2 KEEP=1 bash deploy/runpod/gpu_validate.sh
```

`BUNDLE_DIR` pointing at an existing bundle makes S3 reuse it (no HuggingFace
download); `SCENE` is irrelevant then. Expect the gate distribution in the S6
detail to be mostly `completion`, the opposite of the room-scan bundle, and a
scene comparable to BENCHMARK.md §3 room_2 tier 2 — a plumbing check, not a
new benchmark number (invariant 3).

To compare against the paper's own evaluation on the pod, the GT meshes are
needed too (`~/soba/data/replica/scenes/<room>` on the 4060, several GB per
room); that is optional and separate.
