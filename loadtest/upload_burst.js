// upload_burst: VUS virtual users each upload the fixture archive, poll the
// job to `done`, then delete it, for DURATION.
//
// Open mode: POST /api/jobs is limited to 1 rps / burst 10 per IP, and every
// VU shares the k6 container's IP, so after the first ten uploads the
// clients see 429 + Retry-After and wait (counted in rate_limited /
// upload_retries). Every waiting VU gets the same Retry-After, they retry
// together and one wins per second, so with VUS well above 1 some give up
// after UPLOAD_RETRIES waits (upload_gave_up): that is the limiter working,
// and upload_ok is reported, not required to be 100 %. With a `loadtest`
// key (API_KEY, EXPECT_NO_429=1) the bucket is bypassed, rate_limited must
// stay at 0 and upload_ok must be 100 %. Accepted jobs must always reach
// done.
//
//   VUS (10)  DURATION (30s)  JOB_TIMEOUT_S (90)  UPLOAD_RETRIES (8)  KEEP_JOBS (0)

import { sleep } from 'k6';
import {
  deleteJob, envInt, envStr, summarize, uploadAndWait, uploadThresholds,
} from './lib.js';

const NAME = envStr('LABEL', 'upload_burst');

export const options = {
  scenarios: {
    upload: {
      executor: 'constant-vus',
      vus: envInt('VUS', 10),
      duration: envStr('DURATION', '30s'),
      gracefulStop: '60s',
    },
  },
  thresholds: uploadThresholds({                   // upload_ok / job_done, see lib.js
    http_req_failed: ['rate<0.01'],                // 429 excluded (expected status)
    'http_req_duration{name:upload}': ['p(95)<2000'],
    'http_req_duration{name:status}': ['p(95)<250'],
    'http_req_duration{name:delete}': ['p(95)<500'],
    checks: ['rate>0.99'],
  }),
};

export default function () {
  const id = uploadAndWait(2);
  if (id && __ENV.KEEP_JOBS !== '1') deleteJob(id);
  sleep(0.1);
}

export function handleSummary(data) {
  return summarize(NAME, data);
}
