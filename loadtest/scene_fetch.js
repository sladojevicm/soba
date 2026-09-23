// scene_fetch: what the viewer does on page load for a finished job —
// GET /jobs/{id}/scene.json, then every mesh and hull in one batch — at a
// constant arrival rate of RATE page loads per second. With the three-object
// out/scene_test fixture one page load is 7 requests (1 + 3 meshes + 3
// hulls), so RATE=8 is ~56 rps, under the open-mode general bucket.
//
//   RATE (8 loads/s)  DURATION (30s)  MAX_VUS (100)

import { check } from 'k6';
import {
  deleteJob, envInt, envStr, fetchScene, setupDoneJob, summarize, withRateLimitThresholds,
} from './lib.js';

const NAME = envStr('LABEL', 'scene_fetch');
const RATE = envInt('RATE', 8);

export const options = {
  scenarios: {
    pageloads: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: envStr('DURATION', '30s'),
      preAllocatedVUs: Math.max(5, Math.ceil(RATE)),
      maxVUs: envInt('MAX_VUS', 100),
    },
  },
  thresholds: withRateLimitThresholds({
    http_req_failed: ['rate<0.01'],
    'http_req_duration{name:scene}': ['p(95)<100'],
    'http_req_duration{name:mesh}': ['p(95)<150'],
    'http_req_duration{name:hull}': ['p(95)<150'],
    scene_fetch_ms: ['p(95)<500'],
    checks: ['rate>0.99'],
  }),
};

export function setup() {
  const s = setupDoneJob();
  if (!s.objects.length) throw new Error('setup: the fixture scene has no objects');
  return s;
}

export default function (data) {
  check(fetchScene(data.id), { 'page load complete': (ok) => ok });
}

export function teardown(data) {
  if (data && data.id) deleteJob(data.id);
}

export function handleSummary(data) {
  return summarize(NAME, data);
}
