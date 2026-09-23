// status_polling: a constant arrival rate of GET /api/jobs/{id} (with one
// GET /api/jobs list per ten polls) against one finished job, the pattern
// of many browser tabs polling a job.
//
// Open mode allows 100 rps / burst 200 per IP, so RATE=80 stays under the
// general bucket; RATE=300 with a plain key shows the limiter (EXPECT_429=1)
// and with a `loadtest` key proves the bypass (EXPECT_NO_429=1).
//
//   RATE (80 rps)  DURATION (30s)  MAX_VUS (200)

import { check } from 'k6';
import {
  deleteJob, envInt, envStr, get, setupDoneJob, summarize, withRateLimitThresholds,
} from './lib.js';

const NAME = envStr('LABEL', 'status_polling');
const RATE = envInt('RATE', 80);

export const options = {
  scenarios: {
    polling: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: envStr('DURATION', '30s'),
      preAllocatedVUs: Math.min(50, Math.max(10, Math.ceil(RATE / 4))),
      maxVUs: envInt('MAX_VUS', 200),
    },
  },
  thresholds: withRateLimitThresholds({
    http_req_failed: ['rate<0.01'],
    'http_req_duration{name:status}': ['p(95)<100'],
    'http_req_duration{name:list}': ['p(95)<250'],
    checks: ['rate>0.99'],
  }),
};

export function setup() {
  return setupDoneJob();
}

export default function (data) {
  if (__ITER % 10 === 9) {
    const res = get('/api/jobs', 'list');
    check(res, { 'list 200|429': (r) => r.status === 200 || r.status === 429,
      'list has jobs': (r) => r.status !== 200 || Array.isArray(r.json('jobs')) });
    return;
  }
  const res = get(`/api/jobs/${data.id}`, 'status');
  check(res, {
    'status 200|429': (r) => r.status === 200 || r.status === 429,
    'status is done': (r) => r.status !== 200 || r.json('status') === 'done',
  });
}

export function teardown(data) {
  if (data && data.id) deleteJob(data.id);
}

export function handleSummary(data) {
  return summarize(NAME, data);
}
