// Shared helpers for the Soba k6 scenarios (loadtest/*.js).
//
// Everything is driven by environment variables so the same script serves
// the open-mode run, the keyed-mode run and the CI smoke:
//   BASE_URL      API root (default http://api:8000, the compose service name)
//   API_KEY       bearer key; empty = open mode, no Authorization header
//   RESULTS_DIR   where handleSummary writes <script>.json (default /results)
//   EXPECT_NO_429 1 = add a threshold that no request was rate limited
//   EXPECT_429    1 = add a threshold that SOME request was rate limited
//
// 429 is an *expected* status for every request (http.setResponseCallback):
// it never counts in http_req_failed, and is counted in `rate_limited`
// instead, so the rate limiter's behaviour is measured rather than hidden in
// the error rate. Everything else outside 2xx is a failure.
//
// These numbers are API-layer throughput with a mock worker on CPU. They say
// nothing about the reconstruction pipeline or a GPU (CLAUDE.md invariant 7).

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

export const BASE = (__ENV.BASE_URL || 'http://api:8000').replace(/\/$/, '');
export const API_KEY = __ENV.API_KEY || '';
export const RESULTS_DIR = __ENV.RESULTS_DIR || '/results';
export const FIXTURE = open('./fixtures/bundle_small.zip', 'b');
export const FIXTURE_NAME = 'bundle_small.zip';

http.setResponseCallback(http.expectedStatuses({ min: 200, max: 299 }, 429));

// --- custom metrics ---------------------------------------------------------
export const rateLimited = new Counter('rate_limited');       // 429 answers seen
export const uploadOk = new Rate('upload_ok');                 // POST /api/jobs -> 202 (after retries)
export const uploadRetries = new Counter('upload_retries');   // 429 -> Retry-After waits
export const uploadGaveUp = new Counter('upload_gave_up');    // still 429 after UPLOAD_RETRIES waits (limiter, not an error)
export const jobDone = new Rate('job_done');                   // polled job reached `done`
export const jobTurnaround = new Trend('job_turnaround_ms', true); // upload start -> done seen (Retry-After waits included)
export const jobWait = new Trend('job_wait_ms', true);             // 202 accepted -> done seen (queue + mock worker)
export const sceneFetchMs = new Trend('scene_fetch_ms', true); // scene.json + every mesh/hull
export const sseHeld = new Counter('sse_held');                // /events stream held past HOLD_S
export const sseRejected = new Counter('sse_rejected');        // /events answered 429 too_many_streams
export const sseEnded = new Counter('sse_ended');              // /events closed by the server (unexpected)

export function envInt(name, dflt) {
  const v = parseInt(__ENV[name], 10);
  return Number.isFinite(v) ? v : dflt;
}

export function envStr(name, dflt) {
  return __ENV[name] && __ENV[name] !== '' ? __ENV[name] : dflt;
}

export function headers(extra) {
  const h = Object.assign({}, extra || {});
  if (API_KEY) h.Authorization = `Bearer ${API_KEY}`;
  return h;
}

function noteStatus(res) {
  if (res.status === 429) rateLimited.add(1);
}

export function get(path, name, params) {
  const p = Object.assign({ headers: headers(), tags: { name } }, params || {});
  const res = http.get(BASE + path, p);
  noteStatus(res);
  return res;
}

export function del(path, name) {
  const res = http.del(BASE + path, null, { headers: headers(), tags: { name } });
  noteStatus(res);
  return res;
}

// --- job helpers ------------------------------------------------------------

// POST the fixture archive. A 429 is honoured (sleep Retry-After, retry up to
// `retries` times) because that is what a well-behaved client does against
// the 1 rps / burst 10 upload bucket in open mode. A 429 that survives every
// retry is the limiter doing its job (upload_gave_up, upload_ok=0, no failed
// check); any other non-202 answer is a failed check. Returns the job id or
// null.
export function uploadJob(tier, retries) {
  const max = retries === undefined ? envInt('UPLOAD_RETRIES', 8) : retries;
  const body = { tier: String(tier || 2), archive: http.file(FIXTURE, FIXTURE_NAME, 'application/zip') };
  for (let attempt = 0; ; attempt++) {
    const res = http.post(BASE + '/api/jobs', body, { headers: headers(), tags: { name: 'upload' } });
    noteStatus(res);
    if (res.status === 202) {
      uploadOk.add(1);
      const id = res.json('id');
      check(res, { 'upload 202 with id': (r) => typeof id === 'string' && id.length === 16 });
      return id;
    }
    if (res.status !== 429) {
      uploadOk.add(0);
      check(res, { 'upload accepted': () => false });
      return null;
    }
    if (attempt >= max) {
      uploadOk.add(0);
      uploadGaveUp.add(1);
      return null;
    }
    uploadRetries.add(1);
    const ra = parseFloat(res.headers['Retry-After'] || '1');
    sleep(Number.isFinite(ra) && ra > 0 ? ra : 1);
  }
}

// GET /api/jobs/{id} until state is done/failed or `timeoutS` elapses.
// Returns the last job JSON (or null on a non-200).
export function pollUntilDone(id, timeoutS, intervalS) {
  const deadline = Date.now() + (timeoutS || envInt('JOB_TIMEOUT_S', 90)) * 1000;
  const every = intervalS || 0.25;
  let last = null;
  while (Date.now() < deadline) {
    const res = get(`/api/jobs/${id}`, 'status');
    if (res.status === 200) {
      last = res.json();
      if (last.state === 'done' || last.state === 'failed') return last;
    } else if (res.status !== 429) {
      check(res, { 'status 200': () => false });
      return null;
    }
    sleep(every);
  }
  return last;
}

// Upload + wait; records job_done and job_turnaround_ms. Returns the id (even
// when the job failed, so the caller can still delete it) or null.
export function uploadAndWait(tier) {
  const t0 = Date.now();
  const id = uploadJob(tier);
  if (!id) return null;
  const t1 = Date.now();
  const rec = pollUntilDone(id);
  const ok = !!rec && rec.state === 'done';
  jobDone.add(ok ? 1 : 0);
  if (ok) { jobTurnaround.add(Date.now() - t0); jobWait.add(Date.now() - t1); }
  check(rec, { 'job reached done': () => ok });
  return id;
}

export function deleteJob(id) {
  const res = del(`/api/jobs/${id}`, 'delete');
  check(res, { 'delete 204': (r) => r.status === 204 });
  return res.status === 204;
}

// A finished job for the read-only scenarios (setup() helper). Throws when
// the API cannot produce one so the test aborts instead of measuring 404s.
export function setupDoneJob() {
  const id = uploadAndWait(2);
  if (!id) throw new Error('setup: could not upload the fixture job');
  const rec = pollUntilDone(id, 60);
  if (!rec || rec.state !== 'done') throw new Error(`setup: job ${id} is ${rec ? rec.status : 'unknown'}`);
  const scene = get(`/jobs/${id}/scene.json`, 'scene').json();
  return { id, objects: scene.objects || [] };
}

// GET scene.json then every mesh + hull of the job in one batch, as the
// viewer does on page load. Checks bodies are GLB (magic "glTF").
export function fetchScene(id) {
  const t0 = Date.now();
  const scene = get(`/jobs/${id}/scene.json`, 'scene');
  const okScene = check(scene, {
    'scene.json 200': (r) => r.status === 200,
    'scene.json has objects': (r) => r.status === 200 && Array.isArray(r.json('objects')),
  });
  if (!okScene) return false;
  const reqs = [];
  for (const obj of scene.json('objects')) {
    reqs.push(['GET', `${BASE}/jobs/${id}/meshes/${obj.id}.glb`, null,
      { headers: headers(), tags: { name: 'mesh' }, responseType: 'binary' }]);
    const hulls = (obj.collider && obj.collider.hull_paths) || [];
    for (const hp of hulls) {
      const stem = hp.split('/').pop();
      reqs.push(['GET', `${BASE}/jobs/${id}/hulls/${stem}`, null,
        { headers: headers(), tags: { name: 'hull' }, responseType: 'binary' }]);
    }
  }
  let all = true;
  if (reqs.length) {
    const rs = http.batch(reqs);
    for (const r of rs) {
      noteStatus(r);
      const ok = check(r, {
        'glb 200': (x) => x.status === 200,
        'glb magic': (x) => x.status === 200 && x.body && x.body.byteLength >= 12
          && String.fromCharCode(...new Uint8Array(x.body.slice(0, 4))) === 'glTF',
      });
      all = all && ok;
    }
  }
  sceneFetchMs.add(Date.now() - t0);
  return all;
}

// --- thresholds --------------------------------------------------------------
export function withRateLimitThresholds(thresholds) {
  const t = Object.assign({}, thresholds);
  if (__ENV.EXPECT_NO_429 === '1') t.rate_limited = ['count==0'];
  if (__ENV.EXPECT_429 === '1') t.rate_limited = ['count>0'];
  return t;
}

// upload_ok == 100 % is only a valid assertion when the upload bucket is
// bypassed (EXPECT_NO_429=1, a `loadtest`-flagged key). Against the 1 rps /
// burst 10 bucket the accepted share is set by the limiter and the VU count
// (every VU shares one client IP and the same Retry-After), not by the
// server's health, so there it is reported (upload_ok, upload_gave_up) and
// only required to be non-zero. Every accepted job must reach done in both
// modes.
export function uploadThresholds(thresholds) {
  const t = __ENV.EXPECT_NO_429 === '1'
    ? { upload_ok: ['rate==1'], job_done: ['rate==1'] }
    : { upload_ok: ['rate>0'], job_done: ['rate==1'] };
  return withRateLimitThresholds(Object.assign(t, thresholds));
}

// --- summary ------------------------------------------------------------------
function fmt(v) {
  if (v === undefined || v === null) return '-';
  if (Number.isInteger(v)) return String(v);
  return v.toFixed(2);
}

function metricLine(name, m) {
  const v = m.values || {};
  switch (m.type) {
    case 'counter':
      return `${name}: count=${fmt(v.count)} rate=${fmt(v.rate)}/s`;
    case 'rate':
      return `${name}: ${fmt((v.rate || 0) * 100)}% (${fmt(v.passes)} / ${fmt((v.passes || 0) + (v.fails || 0))})`;
    case 'gauge':
      return `${name}: ${fmt(v.value)} (min ${fmt(v.min)} max ${fmt(v.max)})`;
    default:
      return `${name}: avg=${fmt(v.avg)} med=${fmt(v.med)} p90=${fmt(v['p(90)'])} p95=${fmt(v['p(95)'])} max=${fmt(v.max)}`;
  }
}

// Text summary for stdout + the raw k6 summary JSON under RESULTS_DIR.
// (jslib's textSummary would need network egress from the k6 container.)
export function summarize(scriptName, data) {
  const lines = [`\n=== ${scriptName} — ${BASE} — ${API_KEY ? 'keyed' : 'open'} mode ===`];
  const names = Object.keys(data.metrics).sort();
  let failed = 0;
  for (const n of names) {
    const m = data.metrics[n];
    if (n.startsWith('data_') || n.startsWith('vus') || n === 'iteration_duration') continue;
    let flag = '';
    if (m.thresholds) {
      for (const [expr, r] of Object.entries(m.thresholds)) {
        flag += r.ok ? ` [ok ${expr}]` : ` [FAIL ${expr}]`;
        if (!r.ok) failed += 1;
      }
    }
    lines.push('  ' + metricLine(n, m) + flag);
  }
  lines.push(failed ? `  THRESHOLDS FAILED: ${failed}` : '  all thresholds passed');
  const out = { stdout: lines.join('\n') + '\n' };
  out[`${RESULTS_DIR}/${scriptName}.json`] = JSON.stringify(
    Object.assign({ script: scriptName, base_url: BASE, mode: API_KEY ? 'keyed' : 'open',
      env: pickEnv(), thresholds_failed: failed }, data), null, 1);
  return out;
}

function pickEnv() {
  const keep = {};
  for (const k of Object.keys(__ENV)) {
    if (/^(VUS|DURATION|RATE|HOLD_S|MAX_VUS|EXPECT_NO_429|EXPECT_429|JOB_TIMEOUT_S|UPLOAD_RETRIES|SCENARIO|LABEL)$/.test(k)) keep[k] = __ENV[k];
  }
  keep.API_KEY = API_KEY ? '<set>' : '';
  return keep;
}
