// mixed: the four traffic types at once, sized to stay under the open-mode
// general bucket (100 rps / burst 200 per IP) from one client IP:
//   uploads   UPLOAD_VUS (2)  users uploading, polling to done, deleting
//             (two users at ~1.2 s per cycle exceed the 1 rps upload bucket
//             slightly, so a few Retry-After waits are expected)
//   polling   POLL_RATE (30 rps) status polls of one finished job
//   pageloads SCENE_RATE (4/s) viewer page loads (7 requests each = 28 rps)
//   sse       SSE_VUS (2) clients holding /jobs/{id}/events for HOLD_S (5 s)
//
//   DURATION (45s)

import http from 'k6/http';
import { check, sleep } from 'k6';
import {
  BASE, deleteJob, envInt, envStr, fetchScene, get, headers, rateLimited, setupDoneJob,
  sseEnded, sseHeld, sseRejected, summarize, uploadAndWait, uploadThresholds,
} from './lib.js';

const NAME = envStr('LABEL', 'mixed');
const DURATION = envStr('DURATION', '45s');
const HOLD_S = envInt('HOLD_S', 5);

export const options = {
  scenarios: {
    uploads: {
      executor: 'constant-vus', exec: 'uploads',
      vus: envInt('UPLOAD_VUS', 2), duration: DURATION, gracefulStop: '60s',
    },
    polling: {
      executor: 'constant-arrival-rate', exec: 'polling',
      rate: envInt('POLL_RATE', 30), timeUnit: '1s', duration: DURATION,
      preAllocatedVUs: 10, maxVUs: 100,
    },
    pageloads: {
      executor: 'constant-arrival-rate', exec: 'pageloads',
      rate: envInt('SCENE_RATE', 4), timeUnit: '1s', duration: DURATION,
      preAllocatedVUs: 5, maxVUs: 50,
    },
    sse: {
      executor: 'constant-vus', exec: 'sse',
      vus: envInt('SSE_VUS', 2), duration: DURATION, gracefulStop: `${HOLD_S + 5}s`,
    },
  },
  thresholds: uploadThresholds({                   // upload_ok / job_done, see lib.js
    'http_req_failed{name:upload}': ['rate<0.01'],
    'http_req_failed{name:status}': ['rate<0.01'],
    'http_req_failed{name:scene}': ['rate<0.01'],
    'http_req_failed{name:mesh}': ['rate<0.01'],
    'http_req_failed{name:hull}': ['rate<0.01'],
    'http_req_duration{name:upload}': ['p(95)<2000'],
    'http_req_duration{name:status}': ['p(95)<250'],
    'http_req_duration{name:scene}': ['p(95)<250'],
    'http_req_duration{name:mesh}': ['p(95)<250'],
    'http_req_duration{name:hull}': ['p(95)<250'],
    sse_rejected: ['count==0'],
    sse_ended: ['count==0'],
    checks: ['rate>0.99'],
  }),
};

export function setup() {
  return setupDoneJob();
}

export function uploads() {
  const id = uploadAndWait(2);
  if (id) deleteJob(id);
  sleep(0.5);
}

export function polling(data) {
  const res = get(`/api/jobs/${data.id}`, 'status');
  check(res, { 'status 200|429': (r) => r.status === 200 || r.status === 429 });
}

export function pageloads(data) {
  check(fetchScene(data.id), { 'page load complete': (ok) => ok });
}

export function sse(data) {
  const res = http.get(`${BASE}/jobs/${data.id}/events`, {
    headers: headers({ Accept: 'text/event-stream' }),
    tags: { name: 'sse' },
    timeout: `${HOLD_S}s`,
  });
  const timedOut = res.status === 0 && /timeout/i.test(res.error || '');
  if (timedOut) sseHeld.add(1);
  else if (res.status === 429) { sseRejected.add(1); rateLimited.add(1); }
  else sseEnded.add(1);
  check(res, { 'stream held': () => timedOut });
  sleep(0.5);
}

export function teardown(data) {
  if (data && data.id) deleteJob(data.id);
}

export function handleSummary(data) {
  return summarize(NAME, data);
}
