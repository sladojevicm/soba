"""Step-8 live Claude physics backend — transport faked, contract pinned.

The real API is never called: a fake client returns canned responses, so these
pin the guards (refusal -> lookup, max_tokens -> one doubled retry), the
array-order matching, the clamping, and the key-gated activation.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from scene import vlm, vlm_claude


def _resp(stop_reason="end_turn", objects=None):
    text = json.dumps({"objects": objects or []})
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
    )


class FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def _est(material="wood", friction=0.5, restitution=0.2, rigid=True, why="w"):
    return {"material": material, "friction": friction,
            "restitution": restitution, "is_rigid": rigid, "reasoning": why}


def _backend(responses):
    return vlm_claude.ClaudeBackend(model="claude-opus-4-8", max_tokens=4096,
                                    client=FakeClient(responses))


def test_estimates_matched_by_order_and_clamped():
    be = _backend([_resp(objects=[
        _est("fabric", friction=9.0, restitution=-2.0),   # out of range -> clamped
        _est("glass", friction=0.3, restitution=0.4),
    ])])
    out = be(["couch", "bottle"])
    assert [p.material for p in out] == ["fabric", "glass"]
    assert out[0].friction == 2.0 and out[0].restitution == 0.0
    assert all(p.origin == "vlm" for p in out)


def test_refusal_falls_back_to_lookup():
    be = _backend([_resp(stop_reason="refusal")])
    out = vlm.infer(["chair"], backend=be)
    assert out[0].origin == "lookup"


def test_max_tokens_retries_once_with_doubled_cap():
    be = _backend([_resp(stop_reason="max_tokens"),
                   _resp(objects=[_est("metal")])])
    out = be(["chair"])
    assert out[0].material == "metal"
    caps = [c["max_tokens"] for c in be.client.calls]
    assert caps == [4096, 8192]


def test_max_tokens_twice_gives_up():
    be = _backend([_resp(stop_reason="max_tokens")] * 2)
    assert be(["chair"]) is None
    out = vlm.infer(["chair"], backend=_backend([_resp(stop_reason="max_tokens")] * 2))
    assert out[0].origin == "lookup"


def test_count_mismatch_falls_back():
    be = _backend([_resp(objects=[_est()])])  # 1 estimate for 2 objects
    assert be(["chair", "couch"]) is None


def test_request_shape_matches_plan(tmp_path):
    """output_config.format + system cache_control + one text label per object
    (no crop staged -> no image block, label says so)."""
    be = _backend([_resp(objects=[_est()])])
    be(["chair"], crops=[None], dims_m=[0.8])
    (call,) = be.client.calls
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    content = call["messages"][0]["content"]
    assert [b["type"] for b in content] == ["text"]
    assert "no image available" in content[0]["text"]


def test_annotated_crop_becomes_image_block(tmp_path):
    import cv2

    crop = tmp_path / "crop.jpg"
    cv2.imwrite(str(crop), np.full((60, 80, 3), 200, np.uint8))
    be = _backend([_resp(objects=[_est()])])
    be(["chair"], crops=[crop], dims_m=[0.8])
    content = be.client.calls[0]["messages"][0]["content"]
    assert [b["type"] for b in content] == ["image", "text"]
    assert content[0]["source"]["media_type"] == "image/png"


def test_annotate_crop_draws_ruler_and_box(tmp_path):
    import cv2

    crop = tmp_path / "crop.jpg"
    cv2.imwrite(str(crop), np.full((100, 100, 3), 255, np.uint8))
    png = vlm_claude.annotate_crop(crop, 0.82)
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    b, g, r = img[..., 0].astype(int), img[..., 1].astype(int), img[..., 2].astype(int)
    assert ((g > 200) & (r < 80) & (b < 80)).any(), "no green box/dot"
    assert ((r > 200) & (g < 80) & (b < 80)).any(), "no red ruler"


def test_make_backend_requires_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert vlm_claude.make_backend() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("VID2SIM_VLM", "0")  # explicit off-switch wins
    assert vlm_claude.make_backend() is None


def test_make_backend_reads_config_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("VID2SIM_VLM", raising=False)
    monkeypatch.delenv("VID2SIM_VLM_MODEL", raising=False)
    be = vlm_claude.make_backend()
    assert be is not None and be.model == "claude-opus-4-8"
    assert be.max_tokens == 4096
    monkeypatch.setenv("VID2SIM_VLM_MODEL", "claude-haiku-4-5")
    assert vlm_claude.make_backend().model == "claude-haiku-4-5"


def test_fill_fraction_parsed_clamped_and_optional():
    est_full = _est("metal"); est_full["fill_fraction"] = 0.03      # thin bowl
    est_wild = _est("wood"); est_wild["fill_fraction"] = 7.0        # clamp to 1
    est_none = _est("plastic")                                      # absent -> None
    be = _backend([_resp(objects=[est_full, est_wild, est_none])])
    out = be(["bowl", "dining table", "bottle"])
    assert out[0].fill_fraction == 0.03
    assert out[1].fill_fraction == 1.0
    assert out[2].fill_fraction is None


def test_schema_requires_fill_fraction():
    item = vlm_claude._SCHEMA["properties"]["objects"]["items"]
    assert "fill_fraction" in item["required"]
    assert "fill_fraction" in item["properties"]
