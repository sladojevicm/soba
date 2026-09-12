"""telemetry.jsonlog: JSON / text formatters and configure_logging()."""

from __future__ import annotations

import io
import json
import logging

import pytest

from telemetry import (
    ENV_JSON,
    JsonFormatter,
    TextFormatter,
    configure_logging,
    log_event,
)


def _record(msg="hello", fields=None, level=logging.INFO, exc=None):
    rec = logging.LogRecord("soba.test", level, __file__, 1, msg, None, exc)
    if fields is not None:
        rec.fields = fields
    return rec


def test_json_formatter_emits_one_valid_object_with_expected_keys():
    line = JsonFormatter().format(_record("gate", {"event": "gate", "track_id": 3,
                                                  "completeness_ratio": 0.42}))
    obj = json.loads(line)
    assert set(obj) >= {"ts", "level", "logger", "msg"}
    assert obj["level"] == "INFO" and obj["logger"] == "soba.test"
    assert obj["msg"] == "gate"
    assert obj["event"] == "gate" and obj["track_id"] == 3
    assert obj["completeness_ratio"] == 0.42
    assert obj["ts"].endswith("+00:00") and "T" in obj["ts"]
    assert "\n" not in line


def test_json_formatter_serialises_numpy_and_paths_and_exceptions():
    np = pytest.importorskip("numpy")
    from pathlib import Path

    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        exc = sys.exc_info()
    line = JsonFormatter().format(_record("x", {"angle": np.float32(12.5),
                                               "n": np.int64(4), "p": Path("/a/b")},
                                          exc=exc))
    obj = json.loads(line)
    assert obj["angle"] == 12.5 and obj["n"] == 4 and obj["p"] == "/a/b"
    assert "ValueError: boom" in obj["exc"]


def test_text_formatter_keeps_legacy_prefix_and_appends_fields():
    line = TextFormatter().format(_record("routing done", {"event": "stage.end",
                                                          "seconds": 1.25,
                                                          "cls": "dining table"}))
    assert line.startswith("INFO soba.test: routing done ")
    assert "event=stage.end" in line and "seconds=1.25" in line
    assert 'cls="dining table"' in line


def test_configure_logging_text_and_json_and_env(monkeypatch):
    stream = io.StringIO()
    monkeypatch.delenv(ENV_JSON, raising=False)
    h1 = configure_logging(stream=stream)
    log = logging.getLogger("soba.cfg")
    log_event(log, logging.INFO, "hi", k="v")
    assert stream.getvalue().strip() == "INFO soba.cfg: hi k=v"

    # env flag selects JSON; reconfiguring replaces (not duplicates) our handler
    monkeypatch.setenv(ENV_JSON, "1")
    stream2 = io.StringIO()
    h2 = configure_logging(stream=stream2)
    root = logging.getLogger()
    assert h1 not in root.handlers and h2 in root.handlers
    assert sum(1 for h in root.handlers if getattr(h, "_soba_telemetry_handler", False)) == 1
    log_event(log, logging.INFO, "hi", k="v")
    assert json.loads(stream2.getvalue())["k"] == "v"
    assert stream.getvalue().count("\n") == 1  # old handler is detached

    # explicit argument beats the env
    stream3 = io.StringIO()
    configure_logging(json=False, stream=stream3)
    log.info("plain")
    assert stream3.getvalue().strip() == "INFO soba.cfg: plain"


def test_configure_logging_does_not_remove_foreign_handlers():
    root = logging.getLogger()
    foreign = logging.NullHandler()
    root.addHandler(foreign)
    try:
        configure_logging(stream=io.StringIO())
        assert foreign in root.handlers
    finally:
        root.removeHandler(foreign)
