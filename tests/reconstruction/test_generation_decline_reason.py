"""A generation that could not RUN is not a verdict on the object: the engine
says which it was, so run_assemble can record `generation_unavailable` (never
cached) apart from `engine_declined` (a real rejection)."""

from __future__ import annotations

from reconstruction import generative


def _engine():
    return generative.LocalGpuEngine.__new__(generative.LocalGpuEngine)  # no model load


def test_unavailable_model_is_reported_as_unavailable(monkeypatch):
    eng = _engine()

    def boom(**_):
        raise ModuleNotFoundError("No module named 'diffusers'")

    monkeypatch.setattr(eng, "_run_gen", boom, raising=False)
    assert eng.regenerate(cloud=None, crop_path="x.png", coco_class="chair") is None
    assert eng.last_decline.startswith("unavailable: ModuleNotFoundError")


def test_debris_rejection_is_a_verdict(monkeypatch):
    eng = _engine()
    monkeypatch.setattr(eng, "_run_gen", lambda **_: object(), raising=False)
    monkeypatch.setattr(generative, "_clean_gen", lambda m: (None, 0.9))
    assert eng.regenerate(cloud=None, crop_path="x.png", coco_class="chair") is None
    assert eng.last_decline == "rejected: detached debris"


def test_split_engine_forwards_the_reason():
    inner = _engine()
    inner.last_decline = "unavailable: OOM"
    split = generative.SplitEngine(completer=_engine(), regenerator=inner)
    assert split.last_decline == "unavailable: OOM"
