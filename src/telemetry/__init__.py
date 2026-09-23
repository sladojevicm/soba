"""Observability (phase A): structured logging, stage timing, per-run metrics.

    from telemetry import configure_logging, start_run, stage_timer

    configure_logging()                       # text, or JSON with SOBA_LOG_JSON=1
    metrics = start_run(bundle=..., tier=2)   # becomes telemetry.current()
    with stage_timer("gate", track_id=3):
        res = cf.gate_object(...)
    metrics.record_gate(3, "chair", 2, res, routed=res["strategy"])
    metrics.write(out / "run_metrics.json")   # validated against run_metrics.schema.json

Stdlib only (plus the `jsonschema` the contract layer already depends on).
"""

from .jsonlog import (
                      ENV_JSON,
                      FIELDS_ATTR,
                      JsonFormatter,
                      TextFormatter,
                      configure_logging,
                      log_event,
)
from .metrics import (
                      SCHEMA_PATH,
                      SCHEMA_VERSION,
                      STRATEGIES,
                      RunMetrics,
                      completion,
                      current,
                      drop,
                      set_current,
                      stage_timer,
                      start_run,
                      step,
                      strict,
                      validate,
)

__all__ = [
                      "ENV_JSON",
                      "FIELDS_ATTR",
                      "SCHEMA_PATH",
                      "SCHEMA_VERSION",
                      "STRATEGIES",
                      "JsonFormatter",
                      "RunMetrics",
                      "completion",
                      "TextFormatter",
                      "configure_logging",
                      "current",
                      "drop",
                      "log_event",
                      "set_current",
                      "stage_timer",
                      "step",
                      "strict",
                      "start_run",
                      "validate",
]
