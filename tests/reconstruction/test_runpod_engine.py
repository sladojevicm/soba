"""RunPodEngine hardening against a fake endpoint (http.server on 127.0.0.1).

Everything the client does is exercised for real over HTTP: the /run +
/status/{id} polling transport, the single-POST transport behind
SOBA_RUNPOD_URL, retry with backoff on transient failures (socket timeout,
429, 5xx, stalls), the per-endpoint circuit breaker (open -> half-open probe
-> closed), the per-job budget, the SOBA_RUNPOD_DISABLED kill switch, the
https requirement for URL overrides, and what the remote_call_hook and the
telemetry drops see. No open3d: the mesh seams are stubbed on the engine.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

import telemetry
from reconstruction import generative, runpod_policy
from reconstruction.runpod_policy import Budget, CircuitBreaker, RetryPolicy

PRICE = runpod_policy.load_runpod_config()["price"]["usd_per_gpu_second"]


# --- scripted fake endpoint --------------------------------------------------
def reply(body: dict, code: int = 200, delay: float = 0.0) -> dict:
    return {"code": code, "body": body, "delay": delay}


COMPLETED = reply({"status": "COMPLETED", "output": {"mesh_b64": "AAA", "format": "obj"}})


class FakeEndpoint:
    """Scripted replies: `submits` answers POST .../run|runsync, `statuses`
    answers GET .../status/{id}. The last item of a queue is sticky (a stalled
    job keeps answering IN_QUEUE); an empty queue answers 500 "unscripted"."""

    def __init__(self):
        self.submits: deque = deque()
        self.statuses: deque = deque()
        self.requests: list[dict] = []
        self.cancels: list[str] = []
        self.unscripted = 0
        self.lock = threading.Lock()

    def _next(self, q: deque) -> dict:
        with self.lock:
            if not q:
                self.unscripted += 1
                return reply({"error": "unscripted request"}, 500)
            return q.popleft() if len(q) > 1 else q[0]

    def start(self):
        ep = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def _serve(self, item):
                if item["delay"]:
                    time.sleep(item["delay"])
                data = json.dumps(item["body"]).encode()
                try:
                    self.send_response(item["code"])
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the client timed out and went away

            def _record(self, body=None):
                ep.requests.append({"method": self.command, "path": self.path,
                                    "auth": self.headers.get("Authorization"),
                                    "body": body})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"null") if n else None
                self._record(body)
                if "/cancel/" in self.path:
                    ep.cancels.append(self.path.rsplit("/", 1)[-1])
                    return self._serve(reply({"status": "CANCELLED"}))
                return self._serve(ep._next(ep.submits))

            def do_GET(self):
                self._record()
                if "/status/" in self.path:
                    return self._serve(ep._next(ep.statuses))
                return self._serve(reply({"error": "no such route"}, 404))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/v2"
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    @property
    def submit_paths(self):
        return [r["path"] for r in self.requests
                if r["method"] == "POST" and "/cancel/" not in r["path"]]


@pytest.fixture()
def ep():
    e = FakeEndpoint().start()
    yield e
    e.stop()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    runpod_policy.reset_breakers()
    telemetry.set_current(None)
    for var in ("SOBA_RUNPOD_URL", "SOBA_RUNPOD_BASE_URL", "SOBA_RUNPOD_ALLOW_HTTP",
                "SOBA_RUNPOD_DISABLED", "SOBA_RUNPOD_TIMEOUT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(generative, "remote_call_hook", None)
    yield
    runpod_policy.reset_breakers()
    telemetry.set_current(None)


@pytest.fixture()
def sleeps(monkeypatch):
    """Backoff sleeps are recorded, not slept."""
    seen = []
    monkeypatch.setattr(generative, "_sleep", seen.append)
    return seen


@pytest.fixture()
def hook(monkeypatch):
    calls = []
    monkeypatch.setattr(generative, "remote_call_hook",
                        lambda endpoint, seconds: calls.append((endpoint, seconds)))
    return calls


def engine_run_mode(ep, monkeypatch, **kw) -> generative.RunPodEngine:
    """The real-API shape: POST /v2/{ep}/run, then GET /v2/{ep}/status/{id}."""
    monkeypatch.setenv("SOBA_RUNPOD_BASE_URL", ep.base)
    monkeypatch.setenv("SOBA_RUNPOD_ALLOW_HTTP", "1")
    return generative.RunPodEngine("secret-key", gen_endpoint="gen",
                                   completion_endpoint="comp", **kw)


def engine_url_mode(ep, monkeypatch, path="/gen/runsync", **kw) -> generative.RunPodEngine:
    """The SDK test-server shape: one POST to SOBA_RUNPOD_URL."""
    monkeypatch.setenv("SOBA_RUNPOD_URL", ep.base + path)
    monkeypatch.setenv("SOBA_RUNPOD_ALLOW_HTTP", "1")
    return generative.RunPodEngine("secret-key", gen_endpoint="gen", **kw)


def stub_mesh_seams(monkeypatch, eng):
    """regenerate() without open3d: decode -> a token, clean/align pass-through."""
    monkeypatch.setattr(eng, "_decode_mesh", lambda out: ("MESH", out))
    monkeypatch.setattr(generative, "_clean_gen", lambda m: (m, 0.0))
    monkeypatch.setattr(generative, "_align_and_accept",
                        lambda m, cloud, cls: (m, "fpfh_icp", "per_axis_median"))


@pytest.fixture()
def crop(tmp_path):
    p = tmp_path / "crop.jpg"
    p.write_bytes(b"\xff\xd8\xff\xd9")
    return p


def regen(eng, crop):
    return eng.regenerate(cloud=np.zeros((10, 3)), crop_path=crop, coco_class="chair")


# --- config is the single source of truth ----------------------------------
def test_engine_reads_every_knob_from_pipeline_yaml(monkeypatch):
    cfg = runpod_policy.load_runpod_config()
    for key in ("transport", "request_timeout_s", "job_timeout_s", "poll_interval_s",
                "stall_timeout_s", "retry", "circuit_breaker", "budget", "price"):
        assert key in cfg, key
    eng = generative.RunPodEngine("k", gen_endpoint="gen")
    assert eng.timeout_s == float(cfg["request_timeout_s"])
    assert eng.transport == cfg["transport"] == "run"
    assert eng.retry == RetryPolicy.from_config(cfg)
    assert eng.budget.max_calls == cfg["budget"]["max_calls"]
    assert eng.budget.max_est_usd == cfg["budget"]["max_est_usd"]
    # operator overrides for the socket timeout: env beats config, arg beats env
    monkeypatch.setenv("SOBA_RUNPOD_TIMEOUT", "12")
    assert generative.RunPodEngine("k").timeout_s == 12.0
    assert generative.RunPodEngine("k", timeout_s=3).timeout_s == 3.0


def test_backoff_is_exponential_capped_and_jittered():
    rp = RetryPolicy(max_attempts=5, backoff_base_s=2.0, backoff_max_s=5.0, jitter_s=0.0)
    assert [rp.sleep_for(a) for a in (1, 2, 3, 4)] == [2.0, 4.0, 5.0, 5.0]
    rp = RetryPolicy(max_attempts=5, backoff_base_s=1.0, backoff_max_s=60.0, jitter_s=0.5)
    for a in range(1, 4):
        base = 1.0 * 2 ** (a - 1)
        assert base <= rp.sleep_for(a) <= base + 0.5


def test_price_estimate_honours_per_endpoint_override():
    cfg = {"price": {"usd_per_gpu_second": 0.001, "by_endpoint": {"big": 0.01}}}
    assert runpod_policy.estimate_usd(10, "gen", cfg) == pytest.approx(0.01)
    assert runpod_policy.estimate_usd(10, "big", cfg) == pytest.approx(0.1)
    assert runpod_policy.estimate_usd(2.5) == pytest.approx(2.5 * PRICE)


# --- transport: /run + /status polling, /runsync override -------------------
def test_run_then_status_polling_returns_output_and_bills_execution_time(
        ep, monkeypatch, sleeps, hook):
    ep.submits.append(reply({"id": "job-1", "status": "IN_QUEUE"}))
    ep.statuses.extend([reply({"id": "job-1", "status": "IN_PROGRESS"}),
                        reply({"id": "job-1", "status": "COMPLETED", "executionTime": 1500,
                               "delayTime": 30000, "output": {"mesh_b64": "AAA"}})])
    eng = engine_run_mode(ep, monkeypatch)
    out = eng._runsync("gen", {"mode": "regenerate"})
    assert out == {"mesh_b64": "AAA"}
    assert [(r["method"], r["path"]) for r in ep.requests] == [
        ("POST", "/v2/gen/run"), ("GET", "/v2/gen/status/job-1"),
        ("GET", "/v2/gen/status/job-1")]
    assert ep.requests[0]["body"] == {"input": {"mode": "regenerate"}}
    assert all(r["auth"] == "Bearer secret-key" for r in ep.requests)
    assert sleeps == [eng.poll_interval_s] * 2  # polls wait, no retry backoff
    # billable seconds = executionTime, not the 30 s the job sat in the queue
    assert hook == [("gen", 1.5)]
    assert eng.budget.calls == 1 and eng.budget.gpu_seconds == 1.5
    assert eng.budget.est_usd == pytest.approx(1.5 * PRICE)
    assert runpod_policy.breaker_for("gen").state == "closed"


def test_url_override_is_one_blocking_post(ep, monkeypatch, sleeps, hook):
    ep.submits.append(COMPLETED)
    eng = engine_url_mode(ep, monkeypatch)
    assert eng._runsync("gen", {"x": 1}) == COMPLETED["body"]["output"]
    assert ep.submit_paths == ["/v2/gen/runsync"] and sleeps == []
    assert len(hook) == 1 and hook[0][0] == "gen" and hook[0][1] >= 0.0


def test_url_override_still_polls_when_runsync_returns_early(ep, monkeypatch, sleeps):
    # the SDK test server exposes /status next to /runsync: a runsync that
    # gave up waiting (IN_PROGRESS + id) is followed there
    ep.submits.append(reply({"id": "j9", "status": "IN_PROGRESS"}))
    ep.statuses.append(COMPLETED)
    eng = engine_url_mode(ep, monkeypatch)
    assert eng._runsync("gen", {}) == COMPLETED["body"]["output"]
    assert [r["path"] for r in ep.requests] == ["/v2/gen/runsync", "/v2/gen/status/j9"]


def test_non_terminal_reply_without_status_url_is_transient(ep, monkeypatch, sleeps):
    ep.submits.append(reply({"id": "j1", "status": "IN_QUEUE"}))
    eng = engine_url_mode(ep, monkeypatch, path="/gen/custom")
    eng.retry.max_attempts = 2
    with pytest.raises(generative.RunPodTransient, match="no status URL"):
        eng._runsync("gen", {})
    assert ep.submit_paths == ["/v2/gen/custom"] * 2


def test_runsync_transport_from_config(ep, monkeypatch, sleeps):
    ep.submits.append(COMPLETED)
    eng = engine_run_mode(ep, monkeypatch)
    eng.transport = "runsync"
    eng._runsync("gen", {})
    assert ep.submit_paths == ["/v2/gen/runsync"]


# --- retry on transient failures --------------------------------------------
@pytest.mark.parametrize("code", [429, 500, 502, 503])
def test_http_429_and_5xx_are_retried_with_backoff(ep, monkeypatch, sleeps, hook, code):
    ep.submits.extend([reply({"error": "busy"}, code), COMPLETED])
    eng = engine_run_mode(ep, monkeypatch)
    eng.retry = RetryPolicy(max_attempts=3, backoff_base_s=2.0, backoff_max_s=60, jitter_s=0)
    assert eng._runsync("gen", {}) == COMPLETED["body"]["output"]
    assert len(ep.submit_paths) == 2 and sleeps == [2.0]
    assert [h[0] for h in hook] == ["gen", "gen"]  # every submission is accounted
    assert eng.budget.calls == 2
    b = runpod_policy.breaker_for("gen")
    assert b.state == "closed" and b.failures == 0  # success resets the count


def test_socket_timeout_is_retried(ep, monkeypatch, sleeps):
    ep.submits.extend([reply({"never": "arrives"}, delay=1.0), COMPLETED])
    eng = engine_run_mode(ep, monkeypatch, timeout_s=0.2)
    assert eng._runsync("gen", {}) == COMPLETED["body"]["output"]
    assert len(sleeps) == 1 and eng.budget.calls == 2


def test_stalled_job_is_cancelled_and_retried(ep, monkeypatch):
    ep.submits.extend([reply({"id": "stuck", "status": "IN_QUEUE"}), COMPLETED])
    ep.statuses.append(reply({"id": "stuck", "status": "IN_QUEUE"}))  # sticky
    eng = engine_run_mode(ep, monkeypatch)
    eng.poll_interval_s, eng.stall_timeout_s = 0.01, 0.08
    backoffs = []
    monkeypatch.setattr(generative, "_sleep",
                        lambda s: backoffs.append(s) if s > 0.5 else time.sleep(s))
    assert eng._runsync("gen", {}) == COMPLETED["body"]["output"]
    assert ep.cancels == ["stuck"] and ep.submit_paths == ["/v2/gen/run"] * 2
    assert len(backoffs) == 1  # one retry after the stall


def test_job_timeout_is_permanent(ep, monkeypatch, sleeps):
    ep.submits.append(reply({"id": "slow", "status": "IN_PROGRESS"}))
    ep.statuses.append(reply({"id": "slow", "status": "IN_PROGRESS"}))
    eng = engine_run_mode(ep, monkeypatch)
    eng.job_timeout_s = 0.0  # already over on the first look
    with pytest.raises(generative.RunPodError, match="not finished") as ex:
        eng._runsync("gen", {})
    assert not isinstance(ex.value, generative.RunPodTransient)
    assert ep.cancels == ["slow"] and len(ep.submit_paths) == 1


def test_retries_exhausted_raise_the_last_transient_error(ep, monkeypatch, sleeps, hook):
    ep.submits.append(reply({"error": "down"}, 503))  # sticky
    eng = engine_run_mode(ep, monkeypatch)
    eng.retry = RetryPolicy(max_attempts=3, backoff_base_s=1.0, backoff_max_s=8.0, jitter_s=0)
    with pytest.raises(generative.RunPodTransient, match="HTTP 503"):
        eng._runsync("gen", {})
    assert len(ep.submit_paths) == 3 and sleeps == [1.0, 2.0] and len(hook) == 3
    assert runpod_policy.breaker_for("gen").failures == 3


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_other_4xx_is_permanent(ep, monkeypatch, sleeps, code):
    ep.submits.append(reply({"error": "nope"}, code))
    eng = engine_run_mode(ep, monkeypatch)
    with pytest.raises(generative.RunPodError, match=f"HTTP {code}") as ex:
        eng._runsync("gen", {})
    assert not isinstance(ex.value, generative.RunPodTransient)
    assert len(ep.submit_paths) == 1 and sleeps == []


def test_failed_job_is_not_retried_but_is_accounted(ep, monkeypatch, sleeps, hook):
    ep.submits.append(reply({"status": "FAILED", "error": "cuda OOM"}))
    eng = engine_run_mode(ep, monkeypatch)
    with pytest.raises(generative.RunPodJobFailed, match="cuda OOM"):
        eng._runsync("gen", {})
    assert len(ep.submit_paths) == 1 and sleeps == [] and len(hook) == 1
    assert eng.budget.calls == 1  # RunPod bills the failed execution too


# --- circuit breaker ----------------------------------------------------------
def test_breaker_state_machine(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(runpod_policy, "_now", lambda: clock[0])
    b = CircuitBreaker(consecutive_failures=2, cooldown_s=10)
    assert b.state == "closed" and b.allow()
    b.record_failure()
    assert b.state == "closed" and b.allow()
    b.record_failure()
    assert b.state == "open" and not b.allow()
    clock[0] += 9.9
    assert not b.allow()
    clock[0] += 0.2
    assert b.state == "half_open"
    assert b.allow()          # exactly one probe
    assert not b.allow()      # while it is in flight
    b.record_failure()        # probe failed -> open again, fresh cooldown
    assert b.state == "open" and not b.allow()
    clock[0] += 10.0
    assert b.allow()
    b.record_success()
    assert b.state == "closed" and b.failures == 0 and b.allow() and b.allow()


def test_regenerate_drops_while_breaker_open_then_probes(ep, monkeypatch, sleeps, crop):
    clock = [0.0]
    monkeypatch.setattr(runpod_policy, "_now", lambda: clock[0])
    ep.submits.append(reply({"error": "down"}, 503))  # sticky
    eng = engine_run_mode(ep, monkeypatch)
    stub_mesh_seams(monkeypatch, eng)
    eng.retry.max_attempts = 1
    cb = runpod_policy.breaker_for("gen")
    cb.threshold, cb.cooldown_s = 2, 60.0
    m = telemetry.start_run()

    for _ in range(2):  # two objects fail for real
        with pytest.raises(generative.RunPodTransient):
            regen(eng, crop)
    assert cb.state == "open" and len(ep.submit_paths) == 2

    assert regen(eng, crop) is None  # third: no request at all
    assert len(ep.submit_paths) == 2
    assert m.drops == {"runpod_breaker_open": 1}

    clock[0] += 61.0  # cooldown over: one half-open probe goes out and succeeds
    ep.submits.clear()
    ep.submits.append(COMPLETED)
    r = regen(eng, crop)
    assert isinstance(r, generative.RegenResult) and r.mesh[0] == "MESH"
    assert cb.state == "closed" and len(ep.submit_paths) == 3


def test_breaker_open_stops_retrying_mid_call(ep, monkeypatch, sleeps):
    ep.submits.append(reply({"error": "down"}, 503))
    eng = engine_run_mode(ep, monkeypatch)
    eng.retry = RetryPolicy(max_attempts=5, backoff_base_s=1.0, backoff_max_s=1.0, jitter_s=0)
    cb = runpod_policy.breaker_for("gen")
    cb.threshold, cb.cooldown_s = 2, 3600.0
    with pytest.raises(generative.RunPodTransient):
        eng._runsync("gen", {})
    assert len(ep.submit_paths) == 2 and sleeps == [1.0]  # not 5 attempts


# --- budget -------------------------------------------------------------------
def test_budget_caps_are_named_in_order():
    b = Budget(max_calls=2, max_gpu_seconds=100.0, max_est_usd=1.0)
    assert b.exhausted() is None
    b.charge(50.0, 0.5, "gen")
    assert b.exhausted() is None and b.by_endpoint["gen"]["calls"] == 1
    b.charge(60.0, 0.1, "gen")
    assert b.exhausted() == "calls"
    b = Budget(max_calls=9, max_gpu_seconds=100.0, max_est_usd=1.0)
    b.charge(120.0, 0.0)
    assert b.exhausted() == "gpu_seconds"
    b = Budget(max_calls=9, max_gpu_seconds=1e9, max_est_usd=1.0)
    b.charge(1.0, 1.0)
    assert b.exhausted() == "est_usd" and b.to_dict()["exhausted"] == "est_usd"


def test_budget_exhaustion_drops_without_a_call(ep, monkeypatch, sleeps, crop):
    ep.submits.append(reply({"status": "COMPLETED", "executionTime": 2000,
                             "output": {"mesh_b64": "AAA"}}))
    eng = engine_run_mode(ep, monkeypatch)
    stub_mesh_seams(monkeypatch, eng)
    eng.budget = Budget(max_calls=10, max_gpu_seconds=1e9, max_est_usd=1.5 * PRICE)
    m = telemetry.start_run()
    assert regen(eng, crop) is not None      # under the cap before the call
    assert eng.budget.est_usd == pytest.approx(2.0 * PRICE)   # 2 s billed
    assert eng.budget.exhausted() == "est_usd"
    assert regen(eng, crop) is None          # over the cap -> dropped, no request
    assert regen(eng, crop) is None and len(ep.submit_paths) == 1
    assert m.drops == {"runpod_budget_exhausted": 2}
    # the completion band declines the same way (Poisson fallback), no request
    assert eng.complete(mesh=None, cloud=np.zeros((4, 3)), crop_path=None,
                        coco_class="couch") is None
    assert len(ep.submit_paths) == 1


def test_budget_counts_failed_attempts(ep, monkeypatch, sleeps, crop):
    ep.submits.append(reply({"error": "down"}, 503))
    eng = engine_run_mode(ep, monkeypatch)
    eng.retry.max_attempts = 3
    eng.budget = Budget(max_calls=3, max_gpu_seconds=1e9, max_est_usd=1e9)
    m = telemetry.start_run()
    with pytest.raises(generative.RunPodTransient):
        regen(eng, crop)
    assert eng.budget.calls == 3 and eng.budget.exhausted() == "calls"
    assert regen(eng, crop) is None and m.drops == {"runpod_budget_exhausted": 1}


# --- kill switch, https ---------------------------------------------------------
@pytest.mark.parametrize("value", ["1", "true", "YES"])
def test_kill_switch_makes_no_network_call(ep, monkeypatch, crop, value):
    monkeypatch.setenv("SOBA_RUNPOD_DISABLED", value)
    ep.submits.append(COMPLETED)
    eng = engine_run_mode(ep, monkeypatch)
    m = telemetry.start_run()
    assert regen(eng, crop) is None
    assert eng.complete(mesh=None, cloud=np.zeros((4, 3)), crop_path=None,
                        coco_class="couch") is None
    assert ep.requests == [] and eng.budget.calls == 0
    assert m.drops == {"runpod_disabled": 1}  # completion falls back, not dropped


def test_kill_switch_off_values(monkeypatch):
    for v in ("0", "", "false", "off"):
        monkeypatch.setenv("SOBA_RUNPOD_DISABLED", v)
        assert not runpod_policy.disabled()


@pytest.mark.parametrize("var", ["SOBA_RUNPOD_URL", "SOBA_RUNPOD_BASE_URL"])
def test_plain_http_override_is_refused_before_any_request(ep, monkeypatch, hook, crop, var):
    monkeypatch.setenv(var, ep.base + ("/gen/runsync" if var == "SOBA_RUNPOD_URL" else ""))
    ep.submits.append(COMPLETED)
    eng = generative.RunPodEngine("secret-key", gen_endpoint="gen", completion_endpoint="comp")
    with pytest.raises(generative.RunPodConfigError, match="not https"):
        eng._runsync("gen", {})
    with pytest.raises(generative.RunPodConfigError):
        regen(eng, crop)  # regenerate never swallows it
    with pytest.raises(generative.RunPodConfigError):
        eng.complete(mesh=None, cloud=np.zeros((4, 3)), crop_path=None, coco_class="c")
    assert ep.requests == [] and hook == [] and eng.budget.calls == 0
    assert runpod_policy.breaker_for("gen").failures == 0
    monkeypatch.setenv("SOBA_RUNPOD_ALLOW_HTTP", "1")
    assert eng._runsync("gen", {}) == COMPLETED["body"]["output"]


def test_default_base_url_is_https():
    eng = generative.RunPodEngine("k", gen_endpoint="gen")
    submit, base = eng._urls("gen")
    assert submit == "https://api.runpod.ai/v2/gen/run" and base == "https://api.runpod.ai/v2/gen"


# --- the two bands ---------------------------------------------------------------
def test_regenerate_raises_runpod_error_for_the_caller_to_drop(ep, monkeypatch, sleeps, crop):
    ep.submits.append(reply({"status": "FAILED", "error": "bad image"}))
    eng = engine_run_mode(ep, monkeypatch)
    with pytest.raises(generative.RunPodError, match="bad image"):
        regen(eng, crop)


def test_complete_falls_back_to_none_when_the_endpoint_fails(ep, monkeypatch, sleeps):
    ep.submits.append(reply({"error": "down"}, 503))
    eng = engine_run_mode(ep, monkeypatch)
    eng.retry.max_attempts = 2
    out = eng.complete(mesh=None, cloud=np.zeros((4, 3), dtype=np.float32),
                       crop_path=None, coco_class="couch")
    assert out is None and ep.submit_paths == ["/v2/comp/run"] * 2
    assert ep.requests[0]["body"]["input"]["mode"] == "complete"


def test_garbage_reply_is_a_runpod_error(ep, monkeypatch, sleeps):
    ep.submits.append(reply({"status": "COMPLETED", "output": {}}))
    eng = engine_run_mode(ep, monkeypatch)
    with pytest.raises(generative.RunPodJobFailed, match="no mesh"):
        eng._decode_mesh(eng._runsync("gen", {}))
