import assert from "node:assert/strict";
import test from "node:test";

import { createGlobalRateLimiter } from "../lib/global_rate_limiter.mjs";

test("serializes concurrent request starts at the configured interval", async () => {
  let clock = 1000;
  const sleeps = [];
  const limiter = createGlobalRateLimiter(750, {
    now: () => clock,
    sleeper: async (milliseconds) => {
      sleeps.push(milliseconds);
      clock += milliseconds;
    },
  });
  const starts = await Promise.all([limiter(), limiter(), limiter()]);
  assert.deepEqual(starts, [1000, 1750, 2500]);
  assert.deepEqual(sleeps, [750, 750]);
});

test("does not add delay after an already-long request gap", async () => {
  let clock = 1000;
  const limiter = createGlobalRateLimiter(750, {
    now: () => clock,
    sleeper: async (milliseconds) => {
      clock += milliseconds;
    },
  });
  assert.equal(await limiter(), 1000);
  clock = 5000;
  assert.equal(await limiter(), 5000);
});
