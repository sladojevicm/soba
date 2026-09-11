# 2026-09-11 — Observability phase A: structured logs, stage timing, `run_metrics.json`

_Written on branch `feat/observability` (AGENTS.md agent 4, phase A). Phase B (`/metrics` on the API, request logging with job-id correlation) waits for `job-api-agent` and is not in this entry._

## What changed

- New package `src/telemetry/` (stdlib `logging` + the `jsonschema` the contract layer already
  depends on; no new extra):
  - `jsonlog.py` — `configure_logging(json=None, level=INFO, stream=stdout)` installs one root
    handler. Text mode keeps the old console look (`LEVEL name: message`) and appends structured
    fields as `key=value`; JSON mode (`SOBA_LOG_JSON=1` or `json=True`) emits one object per line:
    `ts`, `level`, `logger`, `msg`, the fields, and `exc` on a traceback. Idempotent; leaves
    foreign handlers (pytest `caplog`, a host app) alone. Fields ride on `record.fields`, attached
    with `log_event(logger, level, msg, **fields)` or `extra={"fields": {...}}`.
  - `metrics.py` — `RunMetrics` (`record_stage`, `record_gate`, `record_drop`,
    `record_remote_call`, `set_status`, `write`), `stage_timer(name, **fields)` (DEBUG
    `stage.start`, INFO `stage.end` with `seconds`; ERROR with `ok=false` + `error` when the body
    raised, exception re-raised), and the active run: `start_run(**run_fields)` makes a
    `RunMetrics` reachable through `current()` (a `contextvars` variable, so a future multi-job
    worker gets one per job); `drop(reason, **fields)` records on it or only logs when no run is
    active.
  - `run_metrics.schema.json` — the contract below; `RunMetrics.write()` validates against it
    before the atomic write, the way `assembler.assemble()` validates `scene.json`.
- `scripts/run_assemble.py` — `telemetry.configure_logging()` replaces `logging.basicConfig`.
  The body moved into `_run(args, metrics)`; `main()` wraps it in `try/except/finally` so
  `<out>/run_metrics.json` is written on success, on `--gate-only` / "nothing to assemble"
  early returns, and on failure (`run.status = "failed"`, `run.error = "Type: message"`, the
  exception re-raised so the exit code is unchanged). Stages: `run`, `bundle_open`,
  `tier_params`, `engine_init`, `gate_cache_key`, `observed_cloud` (per track, cache miss),
  `gate` (per track), `generation`, `generate_object` (per regenerated track), `tsdf_fuse`,
  `assemble`, `eval`. One `gate` event per routed object. All 16 `print()` calls are `log.info`
  with the same text (the `routing:` line and the final per-object table included); the eval
  hook's failures are `log.warning`. Flags and exit codes unchanged.
- `src/scene/assembler.py` — stages `assemble`, `ground`, `vlm_infer`, `assemble_object` (per
  object, with id/track/class/strategy), `completion` (the engine call), `coacd`, `deoverlap`;
  one `object` event per assembled object (id, mass, material, physics origin, collider, hull
  count, translation) and a `scene_written` event. The two silent filters — empty cloud/mesh
  inputs and the 12-object cap — now record drops `empty_input` / `over_cap`. No behaviour
  change; `assemble()` keeps its signature.
- `src/reconstruction/generative.py` — module-level `remote_call_hook(endpoint, seconds)` and a
  `perf_counter` wrap around the `urlopen` in `RunPodEngine._runsync` (called in a `finally`, so
  failed calls are accounted too). Six lines; signature and return value untouched, the class
  stays with `runpod-orchestration-agent`. `run_assemble.py` points the hook at
  `metrics.record_remote_call(f"runpod/{endpoint}", seconds)`.
- `pyproject.toml` — `[tool.setuptools.package-data] telemetry = ["run_metrics.schema.json"]`.

## The gate rule

`record_gate()` stores the dict `confidence.gate_object` returned — `angular_coverage_deg`,
`completeness_ratio`, `strategy` — verbatim; nothing is recomputed from the cloud. A gate-cache
hit stores the cached dict (`cached: true`). `--force-strategy` does not touch `strategy`: the
applied strategy goes into `routed`, and `gate.counts` aggregates the gate's own decision. At
tier 1 `run_assemble.py` never calls `gate_object` (there is no TSDF block); the event is recorded
with both metrics `null` and `strategy: "generative"`, which is exactly what `gate_object`
returns for tier 1.

## `run_metrics.json` contract (schema 1)

`out/<scene>/run_metrics.json`, validated by `src/telemetry/run_metrics.schema.json`.

| key | type | meaning |
| --- | --- | --- |
| `schema` | const `1` | contract version |
| `started_at`, `finished_at` | ISO-8601 UTC | wall clock of the run |
| `run` | object | `bundle`, `out`, `tier`, `force_strategy` (or null), `gate_only`, `status` (`running`/`ok`/`failed`), `error` (string or null) |
| `stages` | `{name: {seconds, count}}` | wall time summed over invocations, and how many |
| `gate.counts` | `{tsdf, completion, generative}` | the gate's decisions: keep / complete / regenerate |
| `gate.per_object[]` | object | `track_id`, `class`, `tier`, `strategy`, `angular_coverage_deg` (number or null), `completeness_ratio` (number or null), `routed`, `cached` |
| `drops` | `{reason: count}` | `cloud_too_small`, `generation_rejected_cached`, `engine_declined`, `empty_tsdf_mesh`, `nothing_to_assemble`, `empty_input`, `over_cap` |
| `remote` | object | `calls`, `seconds`, `est_usd` (null until the orchestration agent's cost record lands), `by_kind` (`runpod/<endpoint>` → `{calls, seconds, est_usd}`) |

Example (a `--gate-only` run at tier 2):

```json
{
  "schema": 1,
  "started_at": "2026-09-11T14:40:02.113+00:00",
  "finished_at": "2026-09-11T14:40:09.870+00:00",
  "run": {"status": "ok", "error": null, "bundle": "bundles/office_3", "out": "out/scene_office_3",
          "tier": 2, "force_strategy": null, "gate_only": true},
  "stages": {"run": {"seconds": 7.75, "count": 1}, "bundle_open": {"seconds": 0.41, "count": 1},
             "observed_cloud": {"seconds": 6.9, "count": 9}, "gate": {"seconds": 0.32, "count": 9}},
  "gate": {"counts": {"tsdf": 0, "completion": 5, "generative": 3},
           "per_object": [{"track_id": 3, "class": "chair", "tier": 2, "strategy": "completion",
                           "angular_coverage_deg": 121.3, "completeness_ratio": 0.52,
                           "routed": "completion", "cached": false}]},
  "drops": {"cloud_too_small": 1},
  "remote": {"calls": 0, "seconds": 0.0, "est_usd": null, "by_kind": {}}
}
```

Log events (field `event`): `run_start`, `stage.start`, `stage.end`, `gate`, `drop`,
`remote_call`, `object`, `scene_written`, `run_metrics`.

## Verified here / only on the GPU machine

- Here (no open3d): `tests/telemetry/` — 25 tests: formatter output, `stage_timer`, `RunMetrics`
  aggregation incl. the tier-1 `None` case, schema validation, the RunPod hook with a fake
  `urlopen`, and a smoke test that stubs `open3d` in `sys.modules`, loads
  `scripts/run_assemble.py` and fakes the pipeline calls (gate-only run, JSON logs, forced
  strategy + cache hit, tier 1, failure path writing `run.status = failed`). Full suite: the
  baseline 42 failures / 7 collection errors (all missing `open3d`) plus 150 passes.
- GPU machine / pod only: the assembler stages on real meshes (`pytest tests/scene`), a real
  `run_metrics.json` from a bundle, and the RunPod hook against the endpoint:
  `PYTHONPATH=src SOBA_LOG_JSON=1 python scripts/run_assemble.py --bundle ~/soba/data/replica/bundles/office_3_v2 --tier 2 --out out/scene_office_3_t2 && python -c "import json; print(json.load(open('out/scene_office_3_t2/run_metrics.json'))['gate']['counts'])"`.
