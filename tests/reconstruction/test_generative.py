"""Engine tests (generative.py, Build Order Phases 7-8).

The LOCAL default is exercised end-to-end (Poisson completion needs Open3D); the
RunPod backend is tested only for construction/seams (no network).
"""

from __future__ import annotations

import numpy as np
import pytest

from reconstruction import generative


def test_make_engine_defaults_to_local(monkeypatch):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.delenv("RUNPOD_ENDPOINT_ID", raising=False)
    # force the no-GPU path so this is deterministic on CUDA + non-CUDA boxes
    monkeypatch.setattr(generative.LocalGpuEngine, "is_available", staticmethod(lambda: False))
    assert isinstance(generative.make_engine(), generative.LocalEngine)


def test_make_engine_picks_runpod_when_configured(monkeypatch):
    monkeypatch.delenv("RUNPOD_GEN_ENDPOINT_ID", raising=False)
    monkeypatch.delenv("RUNPOD_COMPLETION_ENDPOINT_ID", raising=False)
    monkeypatch.setenv("RUNPOD_API_KEY", "k")
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "gen")  # back-compat alias for gen
    # no local GPU -> plain RunPodEngine (no SplitEngine composition)
    monkeypatch.setattr(generative.LocalGpuEngine, "is_available", staticmethod(lambda: False))
    eng = generative.make_engine()
    assert isinstance(eng, generative.RunPodEngine)
    assert eng.gen_endpoint == "gen" and eng.api_key == "k"


def test_make_engine_splits_gen_remote_completion_local(monkeypatch):
    # RunPod gen endpoint + NO completion endpoint + local CUDA -> SplitEngine:
    # the completion band keeps PatchComplete instead of degrading to Poisson.
    monkeypatch.setenv("RUNPOD_API_KEY", "k")
    monkeypatch.setenv("RUNPOD_GEN_ENDPOINT_ID", "gen")
    monkeypatch.delenv("RUNPOD_ENDPOINT_ID", raising=False)
    monkeypatch.delenv("RUNPOD_COMPLETION_ENDPOINT_ID", raising=False)
    monkeypatch.setattr(generative.LocalGpuEngine, "is_available", staticmethod(lambda: True))
    eng = generative.make_engine()
    assert isinstance(eng, generative.SplitEngine)
    assert isinstance(eng._regenerator, generative.RunPodEngine)
    assert isinstance(eng._completer, generative.LocalGpuEngine)
    assert eng._completer.completion_model == "patchcomplete"


def test_split_engine_delegates_per_band():
    class FakeCompleter:
        completion_model = "c"
        def complete(self, **kw):
            return ("completed", kw["cloud"])
    class FakeRegen:
        gen_model = "g"
        def regenerate(self, **kw):
            return ("regenerated", kw["coco_class"])
    eng = generative.SplitEngine(completer=FakeCompleter(), regenerator=FakeRegen())
    assert eng.complete(mesh=None, cloud=7, crop_path=None, coco_class="x") == ("completed", 7)
    assert eng.regenerate(cloud=None, crop_path=None, coco_class="chair") == ("regenerated", "chair")
    assert eng.gen_model == "g" and eng.completion_model == "c"


def test_make_engine_reads_both_endpoints(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "k")
    monkeypatch.delenv("RUNPOD_ENDPOINT_ID", raising=False)
    monkeypatch.setenv("RUNPOD_GEN_ENDPOINT_ID", "gen")
    monkeypatch.setenv("RUNPOD_COMPLETION_ENDPOINT_ID", "comp")
    eng = generative.make_engine()
    assert eng.gen_endpoint == "gen" and eng.completion_endpoint == "comp"


def test_runpod_returns_none_when_endpoint_unset():
    # completion with no completion endpoint -> None (caller falls back to Poisson)
    eng = generative.RunPodEngine("k", gen_endpoint="gen")
    assert eng.complete(mesh=None, cloud=None, crop_path=None, coco_class="chair") is None
    # regenerate with no gen endpoint -> None (object dropped)
    eng2 = generative.RunPodEngine("k", completion_endpoint="comp")
    assert eng2.regenerate(cloud=None, crop_path=None, coco_class="chair") is None


def test_local_engine_regenerate_returns_none():
    # No GPU -> the bottom band can't be invented locally (object is dropped).
    out = generative.LocalEngine().regenerate(
        cloud=np.zeros((10, 3)), crop_path=None, coco_class="chair")
    assert out is None


def test_local_engine_complete_closes_an_open_shell():
    o3d = pytest.importorskip("open3d")
    # an open box (5 of 6 faces) -> Poisson completion should produce geometry
    box = o3d.geometry.TriangleMesh.create_box(1, 1, 1)
    box.compute_vertex_normals()
    tris = np.asarray(box.triangles)
    box.triangles = o3d.utility.Vector3iVector(tris[:-2])  # remove the top -> open
    box.compute_vertex_normals()
    out = generative.LocalEngine().complete(
        mesh=box, cloud=None, crop_path=None, coco_class="box")
    assert out is not None and len(out.vertices) > 0


def test_runpod_build_input_regenerate_encodes_the_crop(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    import base64
    crop = tmp_path / "crop.jpg"
    Image.new("RGB", (32, 32), (10, 20, 30)).save(crop)
    eng = generative.RunPodEngine("k", gen_endpoint="gen")
    payload = eng._build_input(mode="regenerate", model="hunyuan3d",
                               crop_path=str(crop), coco_class="chair", cloud=None)
    assert payload["mode"] == "regenerate" and payload["model"] == "hunyuan3d"
    assert payload["coco_class"] == "chair"
    assert len(base64.b64decode(payload["image_b64"])) > 100


def test_runpod_build_input_complete_encodes_the_cloud():
    import base64
    import io
    eng = generative.RunPodEngine("k", completion_endpoint="comp")
    cloud = np.arange(30, dtype=np.float32).reshape(10, 3)
    payload = eng._build_input(mode="complete", model="pointr", crop_path=None,
                               coco_class="couch", cloud=cloud, mesh=None)
    back = np.load(io.BytesIO(base64.b64decode(payload["cloud_npy_b64"])))
    assert back.shape == (10, 3) and np.allclose(back, cloud)


def test_runpod_decode_mesh_round_trips_obj():
    o3d = pytest.importorskip("open3d")
    import base64
    import tempfile
    box = o3d.geometry.TriangleMesh.create_box(0.5, 0.5, 0.5)
    with tempfile.NamedTemporaryFile(suffix=".obj", delete=False) as tf:
        path = tf.name
    o3d.io.write_triangle_mesh(path, box)
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    eng = generative.RunPodEngine("k", gen_endpoint="gen")
    rec = eng._decode_mesh({"mesh_b64": b64, "format": "obj"})
    assert len(rec.vertices) > 0


def test_runpod_decode_mesh_rejects_empty_output():
    eng = generative.RunPodEngine("k", gen_endpoint="gen")
    with pytest.raises(RuntimeError):
        eng._decode_mesh({})


def test_gen_model_selected_by_tier():
    # fix K1: tiers 1-2 -> TripoSG, tiers 3-4 -> Hunyuan3D
    assert generative.gen_model_for_tier(1) == "triposg"
    assert generative.gen_model_for_tier(2) == "triposg"
    assert generative.gen_model_for_tier(3) == "hunyuan3d"
    assert generative.gen_model_for_tier(4) == "hunyuan3d"
    assert generative.gen_model_for_tier(None) == "triposg"  # safe default


def test_make_engine_tier_sets_runpod_gen_model(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "k")
    monkeypatch.setenv("RUNPOD_GEN_ENDPOINT_ID", "gen")
    monkeypatch.delenv("RUNPOD_GEN_MODEL", raising=False)
    # no local GPU: model selection is what's under test, not Split composition
    monkeypatch.setattr(generative.LocalGpuEngine, "is_available", staticmethod(lambda: False))
    eng = generative.make_engine(tier=4)
    assert isinstance(eng, generative.RunPodEngine) and eng.gen_model == "hunyuan3d"
    # explicit override still wins
    monkeypatch.setenv("RUNPOD_GEN_MODEL", "triposg")
    assert generative.make_engine(tier=4).gen_model == "triposg"


def _box(cx, cy, cz, sx, sy, sz):
    import open3d as o3d
    b = o3d.geometry.TriangleMesh.create_box(sx, sy, sz)
    b.translate((cx - sx / 2, cy - sy / 2, cz - sz / 2))
    return b


def test_strip_removes_hallucinated_mat():
    # a SMALL box standing on a thin full-footprint mat (the Hunyuan
    # display-base artifact): the mat must go, the object must stay.
    chair = _box(0, 0.15, 0, 0.2, 0.3, 0.2)   # miniature on the mat
    mat = _box(0, 0.005, 0, 1.6, 0.01, 1.6)
    cleaned = generative._strip_base_and_fragments(chair + mat)
    import numpy as np
    ext = np.asarray(cleaned.get_max_bound()) - np.asarray(cleaned.get_min_bound())
    assert ext[0] < 0.6 and ext[2] < 0.6      # mat footprint (1.6 m) gone
    assert ext[1] > 0.25                       # object height kept


def test_strip_removes_tilted_mat():
    # the mesh comes out VIEW-ALIGNED (tilted) — the mat is not axis-aligned,
    # which defeated the first (axis-band) detector. Plane RANSAC must not care.
    import numpy as np
    import open3d as o3d
    chair = _box(0, 0.15, 0, 0.2, 0.3, 0.2)
    mat = _box(0, 0.005, 0, 1.6, 0.01, 1.6)
    m = chair + mat
    R = m.get_rotation_matrix_from_xyz((0.5, 0.2, 0.3))   # arbitrary tilt
    m.rotate(R, center=(0, 0, 0))
    cleaned = generative._strip_base_and_fragments(m)
    diag0 = np.linalg.norm(np.asarray(m.get_max_bound()) - np.asarray(m.get_min_bound()))
    diag1 = np.linalg.norm(np.asarray(cleaned.get_max_bound()) - np.asarray(cleaned.get_min_bound()))
    assert diag1 < 0.5 * diag0                 # mat (the dominant extent) gone


def test_strip_keeps_table_with_legs():
    # a table = dominant flat top + legs spanning the same footprint: the
    # rest-footprint test must keep it whole (legs cover the top's footprint).
    table = _box(0, 0.72, 0, 1.6, 0.06, 0.9)
    for x in (-0.7, 0.7):
        for z in (-0.35, 0.35):
            table = table + _box(x, 0.35, z, 0.08, 0.7, 0.08)
    n_before = len(table.triangles)
    cleaned = generative._strip_base_and_fragments(table)
    assert len(cleaned.triangles) == n_before


def test_shattered_generation_is_rejected():
    # three similar-size disconnected pieces = debris, not an object
    debris = _box(0, 0, 0, 0.3, 0.3, 0.3) + _box(1, 0, 0, 0.3, 0.3, 0.3) \
        + _box(2, 0, 0, 0.28, 0.28, 0.28)
    assert generative._looks_shattered(debris)
    assert not generative._looks_shattered(_box(0, 0, 0, 0.5, 0.5, 0.5))


def test_class_dims_gate_rejects_slab_chair():
    # a 'chair' that is a 0.24 m-tall slab (thick fused mat) is nonsense;
    # a 0.9 m one is fine
    assert not generative._class_dims_ok(_box(0, 0.12, 0, 1.2, 0.24, 1.2), "chair")
    assert generative._class_dims_ok(_box(0, 0.45, 0, 0.5, 0.9, 0.5), "chair")


def test_strip_keeps_solid_objects_untouched():
    # a couch-like solid is fat all the way up -> the mat rule must NOT fire
    couch = _box(0, 0.45, 0, 2.0, 0.9, 0.9)
    n_before = len(couch.triangles)
    cleaned = generative._strip_base_and_fragments(couch)
    assert len(cleaned.triangles) == n_before


def test_strip_drops_floating_fragments():
    body = _box(0, 0.5, 0, 0.5, 1.0, 0.5)
    crumb = _box(2.0, 2.0, 2.0, 0.05, 0.05, 0.05)   # tiny far-away fragment
    cleaned = generative._strip_base_and_fragments(body + crumb)
    import numpy as np
    hi = np.asarray(cleaned.get_max_bound())
    assert hi[0] < 1.0 and hi[1] < 1.5          # crumb (at ~2.0) gone


def test_runpod_regenerate_declines_without_crop():
    # image-conditioned band, no crop -> decline (None), never raise (a raise
    # here killed a full assembly run: the caller must be able to drop and go on)
    eng = generative.RunPodEngine("k", gen_endpoint="gen")
    assert eng.regenerate(cloud=None, crop_path=None, coco_class="chair") is None


def test_regen_result_defaults_are_generative_provenance():
    r = generative.RegenResult(mesh=object())
    assert r.alignment_method in ("fpfh_icp", "coarse_aligned")
    assert r.scale_method in ("per_axis_median", "class_prior")


# --- local-GPU backend (selected when a CUDA device is present) ---------
def test_make_engine_prefers_local_gpu_when_cuda_available(monkeypatch):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.delenv("RUNPOD_ENDPOINT_ID", raising=False)
    monkeypatch.delenv("RUNPOD_GEN_ENDPOINT_ID", raising=False)
    monkeypatch.setattr(generative.LocalGpuEngine, "is_available", staticmethod(lambda: True))
    assert isinstance(generative.make_engine(), generative.LocalGpuEngine)


def test_make_engine_local_gpu_can_be_disabled(monkeypatch):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.setattr(generative.LocalGpuEngine, "is_available", staticmethod(lambda: True))
    monkeypatch.setenv("VID2SIM_LOCAL_GPU", "0")
    assert isinstance(generative.make_engine(), generative.LocalEngine)


def test_local_gpu_falls_back_gracefully_when_models_missing():
    # models not installed -> _run_* raise -> complete()/regenerate() return None
    eng = generative.LocalGpuEngine()
    assert eng.complete(mesh=None, cloud=None, crop_path=None, coco_class="chair") is None
    assert eng.regenerate(cloud=None, crop_path=None, coco_class="chair") is None


def test_coarse_align_uses_class_prior_for_size_not_the_cloud():
    o3d = pytest.importorskip("open3d")
    box = o3d.geometry.TriangleMesh.create_box(1, 1, 1)  # unit cube
    box.compute_vertex_normals()
    # The cloud is a PARTIAL fragment (0.2 x 0.05 x 0.4) — its extent must NOT set
    # the size (that gave 0.1-25 kg chairs). Size comes from the class prior: a
    # chair's largest side is 0.90 m, so the unit cube (max extent 1) -> 0.90 cube,
    # proportions intact, and it is CENTRED on the cloud (placement only).
    lo = np.array([9.0, 0.975, -5.2]); hi = np.array([9.2, 1.025, -4.8])
    cloud = np.array([lo, hi, (lo + hi) / 2])
    out = generative.coarse_align_to_cloud(box, cloud, "chair")
    ab = out.get_axis_aligned_bounding_box()
    size = np.asarray(ab.max_bound) - np.asarray(ab.min_bound)
    centre = (np.asarray(ab.max_bound) + np.asarray(ab.min_bound)) / 2
    assert np.allclose(size, 0.90, atol=1e-6)                 # class prior, not cloud
    assert np.allclose(centre, [9.1, 1.0, -5.0], atol=1e-6)   # placed on the cloud


def test_coarse_align_unknown_class_uses_default_size():
    o3d = pytest.importorskip("open3d")
    box = o3d.geometry.TriangleMesh.create_box(2, 1, 1)  # max extent 2
    box.compute_vertex_normals()
    cloud = np.array([[0.0, 0.0, 0.0], [0.1, 0.1, 0.1]])
    out = generative.coarse_align_to_cloud(box, cloud, "unicorn")
    ab = out.get_axis_aligned_bounding_box()
    size = np.asarray(ab.max_bound) - np.asarray(ab.min_bound)
    # default prior 0.60 on the largest side; proportions kept (2:1:1 -> 0.6:0.3:0.3)
    assert np.allclose(size, [0.60, 0.30, 0.30], atol=1e-6)
