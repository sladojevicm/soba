"""Structured logging over the stdlib `logging` module (no new dependency).

Two formatters and one `configure_logging()` that every Python entrypoint calls
once (today: `scripts/run_assemble.py`):

  * `TextFormatter` — the console look the pipeline always had,
    `LEVEL name: message`, with structured fields appended as `key=value`.
  * `JsonFormatter` — one JSON object per line: `ts`, `level`, `logger`, `msg`,
    plus every structured field (and `exc` when there is a traceback). Selected
    by `configure_logging(json=True)` or the `SOBA_LOG_JSON=1` environment
    variable, so a worker can switch to machine-readable logs with no code change.

Structured fields travel on the log record under `record.fields`; use
`log_event(logger, level, msg, **fields)` (or `extra={"fields": {...}}`) to
attach them. Plain `log.info("...")` calls work unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime

FIELDS_ATTR = "fields"
ENV_JSON = "SOBA_LOG_JSON"
TEXT_FORMAT = "%(levelname)s %(name)s: %(message)s"
_HANDLER_TAG = "_soba_telemetry_handler"


def env_flag(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _json_default(obj):
    """JSON fallback for values that show up in pipeline fields: numpy scalars
    (`.item()`), Paths and anything else (`str`)."""
    item = getattr(obj, "item", None)
    if callable(item) and getattr(obj, "ndim", 1) == 0:  # numpy scalar
        return item()
    return str(obj)


def _fmt_value(value) -> str:
    if isinstance(value, float):
        return f"{value:.4g}" if abs(value) < 1e-3 else f"{value:.4f}".rstrip("0").rstrip(".")
    if isinstance(value, str):
        return value if value and " " not in value else json.dumps(value)
    return json.dumps(value, default=_json_default)


class JsonFormatter(logging.Formatter):
    """One JSON object per line; structured fields are merged at the top level."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, FIELDS_ATTR, None)
        if fields:
            payload.update(fields)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=_json_default, separators=(",", ":"))


class TextFormatter(logging.Formatter):
    """`LEVEL name: message key=value ...` — the pre-telemetry console format
    with the structured fields appended."""

    def __init__(self, fmt: str = TEXT_FORMAT):
        super().__init__(fmt)

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        line = self.formatMessage(record)
        fields = getattr(record, FIELDS_ATTR, None)
        if fields:
            line += " " + " ".join(f"{k}={_fmt_value(v)}" for k, v in fields.items())
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(json: bool | None = None, level: int = logging.INFO,
                      stream=None) -> logging.Handler:
    """Install ONE root handler (stdout, like the prints it replaces).

    `json=None` reads `SOBA_LOG_JSON`. Idempotent: a previous handler installed
    by this function is replaced, handlers installed by anyone else (pytest's
    caplog, a host application) are left alone. Returns the handler.
    """
    if json is None:
        json = env_flag(ENV_JSON)
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, _HANDLER_TAG, False):
            root.removeHandler(h)
            h.close()
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(JsonFormatter() if json else TextFormatter())
    setattr(handler, _HANDLER_TAG, True)
    root.addHandler(handler)
    root.setLevel(level)
    return handler


def log_event(logger: logging.Logger, level: int, msg: str, **fields) -> None:
    """Log `msg` with structured `fields` attached (see module docstring)."""
    if logger.isEnabledFor(level):
        logger.log(level, msg, extra={FIELDS_ATTR: fields})
