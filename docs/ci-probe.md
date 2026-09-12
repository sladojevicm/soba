# CI probe — does the `recon` extra import on a plain Ubuntu runner?

Maintainer decision 3 (`.claude/AGENTS.md`): before `ci.yml` was written, a
temporary workflow (`.github/workflows/probe-open3d.yml`, branch `feat/docker`)
had to prove whether Open3D imports on `ubuntu-latest` (not WSL) with only
`libgl1` and `libglib2.0-0` installed, and what the full test suite does there.

Run: <https://github.com/sladojevicm/soba/actions/runs/34600770203>
(2026-09-11, commit `315fd8c`, `ubuntu-latest`, Python 3.11.16).

## Verdict

**Open3D imports.** `pip install -e ".[recon,serve,dev]"` after
`apt-get install libgl1 libglib2.0-0` gives a working `open3d 0.19.0`, and the
tensor backend runs on `CPU:0` (`o3d.core.Tensor.zeros((2,2), CPU:0)` — the
TSDF fallback path). `cv2 5.0.0`, `trimesh 5.1.0`, `scipy 1.17.1`,
`skimage 0.26.0`, `pymeshfix 0.18.1`, `numpy 2.4.6` all import.

**Full suite: 238 passed, 2 failed, 8 skipped in 44 s** (248 collected; the
GPU machine's last full run was 241 pass on 2026-07-06, before the §4 ablation
tests were added).

## The 8 skips — all environment, all expected

| Test | Reason |
|---|---|
| `tests/test_server.py` × 7 | `out/scene_office_3` is a gitignored build output; the fixture skips when it is absent |
| `tests/test_eval_scene.py:253` | `plyfile` is not in any pyproject extra (`importorskip`) |

## The 2 failures — both environment, neither a code defect

| Test | Missing | Why it is environment | CI handling |
|---|---|---|---|
| `tests/reconstruction/test_slam.py::test_mast3r_empty_bundle_returns_no_poses` | `torch`, then `dust3r` | `Mast3rEstimator.estimate` imports `torch` and `dust3r.*` from the MASt3R checkout (`SOBA_MAST3R_HOME`) *before* the empty-bundle early return. Neither torch nor the checkout is in any extra; only the GPU machine has both. | marked `@pytest.mark.gpu`; `tests/conftest.py` skips `gpu` tests when `SOBA_SKIP_GPU_TESTS=1`, which `ci.yml` sets. Runs unchanged on the GPU machine. |
| `tests/scene/test_vlm_claude.py::test_make_backend_reads_config_model` | `anthropic` | `make_backend()` returns `None` with a warning when the SDK is missing (never a crash by design). `anthropic` is not in any pyproject extra. Pure Python, no GPU. | CI installs `anthropic` next to the extras so the test really runs (no skip). |

Packages the probe found absent on the runner (none is in an extra):
`coacd`, `plyfile`, `torch`, `ultralytics`, `sam2` (and `anthropic`, from the
failure above). `coacd` matters for reproducibility — without it
`scene/decomp.py` falls back to a single hull — so `docker/pipeline.Dockerfile`
installs it explicitly; adding it to the `recon` extra is left to the
maintainer (finding, not fixed here).

## What `ci.yml` does with this

- Same setup as the probe (`ubuntu-latest`, Python 3.11, the two apt libs,
  `[recon,serve,dev]`) plus `anthropic`.
- `pytest -q` with `SOBA_SKIP_GPU_TESTS=1`: **one** test excluded
  (`test_mast3r_empty_bundle_returns_no_poses`), everything else runs,
  Open3D-dependent tests included. Expected: 239 pass, 1 skipped (gpu),
  8 skipped (environment).
- `ruff check .` (E9 + F only; see `[tool.ruff]` in `pyproject.toml`).

Runner `pip freeze` excerpt (for the record; the GPU machine and the
`BENCHMARK.md` measurement ran Open3D 0.19 / numpy 2.5 / Python 3.12):
`open3d==0.19.0 numpy==2.4.6 opencv-python==5.0.0.93 trimesh==5.1.0
scipy==1.17.1 scikit-image==0.26.0 pymeshfix==0.18.1 pillow==12.3.0
starlette==1.6.0 uvicorn==0.52.4 sse-starlette==3.4.11 httpx==0.28.1
pytest==9.1.1 ruff==0.16.7 jsonschema==4.26.0 PyYAML==6.0.3`.
