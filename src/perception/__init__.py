"""Perception stage — capture / dataset input (Contract 1, PerceptionBundle).

Owns the on-disk PerceptionBundle format every later stage reads:

    session_{id}/
      manifest.json      {session_id, fps, frame_count, timestamp_start, source}
      intrinsics.json    {fx, fy, cx, cy, distortion, baseline}
      poses.json         written by Step 3 (slam)
      frames/00001/      rgb.jpg, depth.png (uint16 mm), conf.png,
                         objects.json, mask_{track_id}.png, imu.jsonl

Depth is ALWAYS stored in millimetres (uint16); see KEY DEFINITIONS in the plan.
"""

from .bundle import Intrinsics, Manifest, PerceptionBundle

__all__ = ["Intrinsics", "Manifest", "PerceptionBundle"]
