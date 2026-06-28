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
    assert isinstance(generative.make_engine(), generative.LocalEngine)


def test_make_engine_picks_runpod_when_configured(monkeypatch):
    monkeypatch.delenv("RUNPOD_GEN_ENDPOINT_ID", raising=False)
    monkeypatch.delenv("RUNPOD_COMPLETION_ENDPOINT_ID", raising=False)
    monkeypatch.setenv("RUNPOD_API_KEY", "k")
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "gen")  # back-compat alias for gen
    eng = generative.make_engine()
    assert isinstance(eng, generative.RunPodEngine)
    assert eng.gen_endpoint == "gen" and eng.api_key == "k"


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


def test_runpod_seams_raise_until_contract_provided():
    eng = generative.RunPodEngine("k", gen_endpoint="gen", completion_endpoint="comp")
    with pytest.raises(NotImplementedError):
        eng._build_input(mode="complete", model="pointr", crop_path=None,
                         coco_class="chair", cloud=None, mesh=None)
    with pytest.raises(NotImplementedError):
        eng._decode_mesh({})


def test_regen_result_defaults_are_generative_provenance():
    r = generative.RegenResult(mesh=object())
    assert r.alignment_method in ("fpfh_icp", "coarse_aligned")
    assert r.scale_method in ("per_axis_median", "class_prior")
