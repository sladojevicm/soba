"""Reconstruction stage — pose estimation, observed cloud, TSDF, gate, ICP.

This package currently provides the parts buildable without GPUs or large
models (Build Order Phases 3+):

* `slam` — camera poses per frame (RGB-D odometry now; MASt3R / ORB-SLAM3
  interfaces stubbed for Tiers 2-3 / 4);
* `observed_cloud` — the back-projected masked-depth point cloud (Step 4 Part A)
  that exists in every tier and is the gate's and ICP's reference;
* `sam2_refine` — SAM2 mask refinement + shared best-frame crop (Step 2;
  orchestration is pure, the SAM2 model sits behind an injectable backend);
* `tsdf` — TSDF fusion (Step 4 Part B) via Open3D's tensor VoxelBlockGrid
  (open3d imported lazily inside the fusion functions, so this stays light).
"""

from . import observed_cloud, sam2_refine, slam, tsdf

__all__ = ["observed_cloud", "sam2_refine", "slam", "tsdf"]
