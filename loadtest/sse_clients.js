// sse_clients: VUS concurrent clients on GET /jobs/{id}/events.
//
// k6 has no SSE support: http.get buffers the whole body and /events never
// ends (heartbeats every 15 s), so the request is given a HOLD_S timeout and
// a timeout is the *expected* outcome — it means the server held the stream
// open for HOLD_S (counted in sse_held). A 429 is the SSE cap
// (SOBA_SSE_MAX_PER_IP, 8 by default; every VU shares the container's IP)
// and lands in sse_rejected; a 200 means the server closed the stream early
// (sse_ended). Event *content* is not visible to k6; loadtest/sse_clients.py
// reads the events and checks them.
//
// Default VUS=8 sits exactly at the cap and must see no 429; EXPECT_CAP=1
// with VUS=9 asserts the cap fires. The http_req_failed threshold is not
// used here because k6 counts the deliberate timeouts as failures.
//
//   VUS (8)  DURATION (20s)  HOLD_S (3)  EXPECT_CAP (0)

import http from 'k6/http';
import { check, sleep } from 'k6';
import {
  BASE, deleteJob, envInt, envStr, headers, rateLimited, setupDoneJob, sseEnded, sseHeld,
  sseRejected, summarize,
} from './lib.js';

const NAME = envStr('LABEL', 'sse_clients');
const HOLD_S = envInt('HOLD_S', 3);

const thresholds = {
  sse_ended: ['count==0'],
  checks: ['rate>0.99'],
};
if (__ENV.EXPECT_CAP === '1') thresholds.sse_rejected = ['count>0'];
else thresholds.sse_rejected = ['count==0'];
thresholds.sse_held = ['count>0'];

export const options = {
  scenarios: {
    sse: {
      executor: 'constant-vus',
      vus: envInt('VUS', 8),
      duration: envStr('DURATION', '20s'),
      gracefulStop: `${HOLD_S + 5}s`,
    },
  },
  thresholds,
};

export function setup() {
  return setupDoneJob();
}

export default function (data) {
  const res = http.get(`${BASE}/jobs/${data.id}/events`, {
    headers: headers({ Accept: 'text/event-stream' }),
    tags: { name: 'sse' },
    timeout: `${HOLD_S}s`,
  });
  const timedOut = res.status === 0 && /timeout/i.test(res.error || '');
  if (timedOut) sseHeld.add(1);
  else if (res.status === 429) { sseRejected.add(1); rateLimited.add(1); }
  else sseEnded.add(1);
  check(res, {
    'stream held or capped': () => timedOut || res.status === 429,
    'cap answer is too_many_streams': (r) => r.status !== 429 || r.json('error.code') === 'too_many_streams',
  });
  sleep(0.2);
}

export function teardown(data) {
  if (data && data.id) deleteJob(data.id);
}

export function handleSummary(data) {
  return summarize(NAME, data);
}
