"""Build PerceptionBundles for Replica vMAP scenes by streaming only the strided
frames out of the remote 44.79 GB vmap.zip (HTTP range requests, no full download).

Per scene we pull ~100 frames (stride 20) of rgb/depth/semantic + the full
traj_w_c.txt + render_config.yaml into a temp dir, build the bundle via
ReplicaReader, then delete the temp dir. Peak disk per scene ~200 MB; bundles are
~64 MB each.
"""

from __future__ import annotations

import argparse
import functools
print = functools.partial(print, flush=True)
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from remote_zip import HTTPRangeFile  # noqa: E402

from perception.dataset_reader import ReplicaReader  # noqa: E402

VMAP_URL = "https://huggingface.co/datasets/kxic/vMAP/resolve/main/vmap.zip"
SCENES = ["room_0", "room_1", "room_2",
          "office_0", "office_1", "office_2", "office_3", "office_4"]
TYPES = ["depth", "rgb", "semantic_class", "semantic_instance"]


def scene_depth_indices(names: set[str], scene: str) -> list[int]:
    """Sorted rendered-frame indices available for a scene (from depth_*.png)."""
    prefix = f"vmap/{scene}/imap/00/depth/depth_"
    idx = []
    for n in names:
        if n.startswith(prefix) and n.endswith(".png"):
            try:
                idx.append(int(n[len(prefix):-4]))
            except ValueError:
                continue
    return sorted(idx)


def extract_member(zf: zipfile.ZipFile, name: str, dest: Path) -> bool:
    try:
        data = zf.read(name)
    except KeyError:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return True


def scene_members(names, scene, indices):
    """All zip member names this scene needs (small files + strided frames)."""
    base = f"vmap/{scene}/imap/00"
    members = [f"{base}/traj_w_c.txt", f"{base}/render_config.yaml"]
    for n in indices:
        for t in TYPES:
            members.append(f"{base}/{t}/{t}_{n}.png")
    return [m for m in members if m in names]


def build_scene(zf, rf, names, scene, tmp_root, out_root, stride, max_frames, fps):
    indices = scene_depth_indices(names, scene)[::stride][:max_frames]
    if not indices:
        print(f"  {scene}: NO frames found in zip, skipping")
        return None

    members = scene_members(names, scene, indices)
    # Prefetch every block these members occupy, concurrently, before reading.
    blocks: set[int] = set()
    for m in members:
        info = zf.getinfo(m)
        # local header (30 + name + extra) + compressed data; over-fetch a little
        span = 30 + len(info.filename) + 64 + info.compress_size
        blocks.update(rf.blocks_for_span(info.header_offset, span))
    print(f"  {scene}: {len(members)} members, prefetching {len(blocks)} blocks...")
    rf.prefetch_blocks(blocks)

    seq = tmp_root / scene / "imap" / "00"
    for m in members:
        rel = m.split("/imap/00/", 1)[1]
        extract_member(zf, m, seq / rel)
    return ReplicaReader(seq).to_bundle(
        out_root / scene, fps=fps, stride=1, max_frames=max_frames
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path,
                    default=Path.home() / "projects/soba/data/replica/bundles")
    ap.add_argument("--tmp", type=Path,
                    default=Path.home() / "projects/soba/data/replica/_tmp_extract")
    ap.add_argument("--scenes", nargs="*", default=SCENES)
    ap.add_argument("--stride", type=int, default=20)
    ap.add_argument("--max-frames", type=int, default=100)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--url", default=VMAP_URL)
    args = ap.parse_args()

    print(f"opening remote zip {args.url}")
    rf = HTTPRangeFile(args.url, block=1024 * 1024)
    zf = zipfile.ZipFile(rf)
    names = set(zf.namelist())
    print(f"  {rf.size/1e9:.1f} GB, {len(names)} members")

    for scene in args.scenes:
        if (args.out / scene / "manifest.json").exists():
            print(f"{scene}: bundle already exists, skipping")
            continue
        print(f"{scene}: extracting strided frames...")
        try:
            build_scene(zf, rf, names, scene, args.tmp, args.out,
                        args.stride, args.max_frames, args.fps)
            n = len(list((args.out / scene / "frames").iterdir()))
            print(f"  {scene}: bundle built ({n} frames) -> {args.out / scene}")
        finally:
            shutil.rmtree(args.tmp / scene, ignore_errors=True)
            rf.clear_cache()  # bound memory between scenes


if __name__ == "__main__":
    main()
