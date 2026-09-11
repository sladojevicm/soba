"""Fetch the ground-truth camera trajectories (traj_w_c.txt) for Replica vMAP
scenes out of the remote 44.79 GB vmap.zip via HTTP range requests.

The bundles were built from these files (ReplicaReader rotates them Z-up->Y-up
and writes poses.json), but the raw traj_w_c.txt was never kept locally.
Re-fetching it gives an authoritative, un-clobberable ground truth for pose
evaluation (~250 KB per scene; nothing else is downloaded).

Usage:
  python scripts/fetch_replica_gt_traj.py [--scenes office_1 office_2 ...]
Writes ~/soba/data/replica/gt_traj/<scene>_traj_w_c.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from remote_zip import HTTPRangeFile  # noqa: E402

VMAP_URL = "https://huggingface.co/datasets/kxic/vMAP/resolve/main/vmap.zip"
DEFAULT_SCENES = ["office_1", "office_2", "office_3", "office_4"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenes", nargs="*", default=DEFAULT_SCENES)
    ap.add_argument("--out", type=Path,
                    default=Path.home() / "soba/data/replica/gt_traj")
    ap.add_argument("--url", default=VMAP_URL)
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    todo = [s for s in args.scenes
            if not (args.out / f"{s}_traj_w_c.txt").exists()]
    if not todo:
        print("all trajectories already fetched")
        return

    import zipfile
    print(f"opening remote zip {args.url}")
    rf = HTTPRangeFile(args.url, block=1024 * 1024)
    zf = zipfile.ZipFile(rf)
    for scene in todo:
        member = f"vmap/{scene}/imap/00/traj_w_c.txt"
        data = zf.read(member)
        dest = args.out / f"{scene}_traj_w_c.txt"
        dest.write_bytes(data)
        print(f"  {scene}: {len(data)} bytes -> {dest}")
    rf.clear_cache()


if __name__ == "__main__":
    main()
