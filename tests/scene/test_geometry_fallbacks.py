"""No geometry step may turn a failure into a valid-looking result silently:
fusion, keep-band repair, CoACD and the mass volume each record which
implementation ran, and SOBA_STRICT=1 fails the run on a fallback."""

from __future__ import annotations

import builtins
import types

import numpy as np
import pytest

o3d = pytest.importorskip("open3d")

import telemetry  # noqa: E402
from scene import assembler, decomp, mass  # noqa: E402


def _box(w=0.4, h=0.3, d=0.2):
    m = o3d.geometry.TriangleMesh.create_box(w, h, d)
    m.compute_vertex_normals()
    return m


def _open_shell():
    m = _box()
    tris = np.asarray(m.triangles)[:-2]  # drop one face: no longer watertight
    m.triangles = o3d.utility.Vector3iVector(tris)
    return m


@pytest.fixture
def run(monkeypatch):
    for k in ("SOBA_STRICT", "SOBA_GEOMETRY_STRICT"):
        monkeypatch.delenv(k, raising=False)
    m = telemetry.start_run(bundle="b", out="o", tier=2)
    yield m
    telemetry.set_current(None)


def _obj():
    return types.SimpleNamespace(track_id=7, coco_class="chair", mesh=_open_shell(),
                                 vbg=object(), voxel_size=0.004)


# --- item 3: CoACD -----------------------------------------------------------
def test_decompose_info_reports_coacd_when_it_ran():
    pytest.importorskip("coacd")
    parts, info = decomp.decompose_info(_box(), max_parts=4)
    assert info["method"] == "coacd" and info["parts"] == len(parts) >= 1


def test_decompose_info_reports_missing_coacd(monkeypatch):
    real_import = builtins.__import__

    def no_coacd(name, *a, **k):
        if name == "coacd":
            raise ImportError("No module named 'coacd'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_coacd)
    parts, info = decomp.decompose_info(_box())
    assert len(parts) == 1
    assert info == {"method": "single_hull_fallback", "reason": "coacd not installed", "parts": 1}
    assert decomp.decompose(_box())  # the old signature still works


def test_decompose_info_reports_a_coacd_crash(monkeypatch):
    coacd = pytest.importorskip("coacd")
    monkeypatch.setattr(coacd, "run_coacd", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    _parts, info = decomp.decompose_info(_box())
    assert info["method"] == "single_hull_fallback" and "coacd raised ValueError" in info["reason"]


# --- item 5: mass volume -----------------------------------------------------
def test_finalize_mesh_info_methods(monkeypatch):
    _m, vol, wt, info = mass.finalize_mesh_info(_box())
    assert info["method"] == "already_watertight" and wt and vol == pytest.approx(0.4 * 0.3 * 0.2)

    monkeypatch.setattr(mass, "watertight_repair",
                        lambda m: (_ for _ in ()).throw(RuntimeError("poisson died")))
    out, vol, wt, info = mass.finalize_mesh_info(_open_shell())
    assert info["method"] == "hull_volume_fallback" and "repair raised RuntimeError" in info["reason"]
    assert wt is False and vol > 0
    assert mass.finalize_mesh(_open_shell())[2] is False  # 3-tuple API unchanged


# --- item 1: fusion ----------------------------------------------------------
def test_fusion_failure_is_recorded_not_swallowed(run, monkeypatch):
    monkeypatch.setattr(assembler.fusion, "fuse_completion",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("vbg mismatch")))
    mesh, vol = assembler._fuse_and_seal(_obj(), np.zeros(3), _box(), "chair_07")
    assert len(mesh.triangles) and vol > 0            # the run still completes...
    f = run.steps["fusion"]
    assert f["counts"] == {"plain_finalize_fallback": 1}  # ...but says the completion was NOT used
    e = f["per_object"][0]
    assert e["fallback"] is True and "fusion raised ValueError" in e["reason"]
    assert e["completion_source"] == "engine" and e["id"] == "chair_07"
    assert "volume" in run.steps


def test_fusion_success_records_that_the_completion_reached_the_mesh(run, monkeypatch):
    monkeypatch.setattr(assembler.fusion, "fuse_completion", lambda *a, **k: (_box(), None))
    assembler._fuse_and_seal(_obj(), np.zeros(3), _box(), "chair_07")
    e = run.steps["fusion"]["per_object"][0]
    assert e["method"] == "fused" and e["fallback"] is False
    assert e["completion_source"] == "engine" and e["fused_triangles"] == 12
    # no engine mesh -> fused with the local Poisson completion, and it says so
    assembler._fuse_and_seal(_obj(), np.zeros(3), None, "chair_08")
    assert run.steps["fusion"]["per_object"][1]["completion_source"] == "poisson"


def test_strict_mode_fails_the_run_on_a_fusion_fallback(run, monkeypatch):
    monkeypatch.setenv("SOBA_STRICT", "1")
    monkeypatch.setattr(assembler.fusion, "fuse_completion",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("vbg mismatch")))
    with pytest.raises(RuntimeError, match="fusion fallback for chair_07"):
        assembler._fuse_and_seal(_obj(), np.zeros(3), _box(), "chair_07")


# --- item 4: keep-band repair -------------------------------------------------
def test_keep_band_repair_fallback_is_recorded(run, monkeypatch):
    monkeypatch.setattr(assembler.geometric_repair, "watertight_collider",
                        lambda m: (_ for _ in ()).throw(ImportError("no pymeshfix")))
    mesh, vol = assembler._tsdf_watertight_finalize(_open_shell(), obj=_obj(),
                                                    oid_str="table_01", context="keep")
    assert len(mesh.triangles) and vol > 0
    e = run.steps["watertight_repair"]["per_object"][0]
    assert e["method"] == "poisson_fallback" and e["fallback"] is True
    assert "pymeshfix raised ImportError" in e["reason"] and e["context"] == "keep"
    monkeypatch.setenv("SOBA_GEOMETRY_STRICT", "1")
    with pytest.raises(RuntimeError, match="watertight_repair fallback"):
        assembler._tsdf_watertight_finalize(_open_shell(), obj=_obj(), oid_str="table_01")


def test_keep_band_repair_success_is_recorded(run):
    pytest.importorskip("pymeshfix")
    assembler._tsdf_watertight_finalize(_open_shell(), obj=_obj(), oid_str="table_01")
    assert run.steps["watertight_repair"]["counts"].get("pymeshfix") == 1
