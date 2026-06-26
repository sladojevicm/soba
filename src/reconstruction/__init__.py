"""Reconstruction stage — pose estimation, observed cloud, TSDF, gate, ICP.

This package currently provides the parts buildable without GPUs or large
models (Build Order Phases 3+):

* `slam` — camera poses per frame (RGB-D odometry now; MASt3R / ORB-SLAM3
  interfaces stubbed for Tiers 2-3 / 4);
* `observed_cloud` — the back-projected masked-depth point cloud (Step 4 Part A)
  that exists in every tier and is the gate's and ICP's reference.
"""

from . import observed_cloud, slam

__all__ = ["observed_cloud", "slam"]
