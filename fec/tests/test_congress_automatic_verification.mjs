import assert from "node:assert/strict";
import test from "node:test";

import {
  CongressAutomaticVerificationTimeoutError,
  automaticVerificationError,
  buildCooldownState,
  classifyAutomaticVerification,
  remainingCooldownMs,
  validateCooldownState,
} from "../lib/congress_automatic_verification.mjs";

const observed = {
  loadedUrl:
    "https://www.congress.gov/index.php/advanced-search/legislation?enterTerms=x",
  title: "Just a moment...",
  bodyText: `
    www.congress.gov
    Performing security verification
    This page is displayed while the website verifies you are not a bot.
    Ray ID: a21123a3ce70fa80
    Performance and Security by Cloudflare
  `,
};

test("classifies the observed automatic Cloudflare interstitial as non-result", () => {
  const value = classifyAutomaticVerification(observed);
  assert.equal(value.kind, "automatic_security_verification");
  assert.equal(value.provider, "cloudflare");
  assert.equal(value.interactive, false);
  assert.equal(value.collection_outcome, "not_a_result_page");
  assert.equal(value.ray_id, "a21123a3ce70fa80");
  assert.equal(value.captcha_bypass_attempted, false);
  assert.equal(value.verification_interaction_performed, false);
});

test("does not misclassify a validated Congress.gov result page", () => {
  assert.equal(
    classifyAutomaticVerification({
      loadedUrl: "https://www.congress.gov/bill/119th-congress/house-bill/940",
      title: "H.R.940 - FAIR Exams Act | Congress.gov",
      bodyText: "H.R.940 - FAIR Exams Act Sponsor: Rep. Hill",
    }),
    null,
  );
});

test("does not classify the same text on a non-Congress host", () => {
  assert.equal(
    classifyAutomaticVerification({
      ...observed,
      loadedUrl: "https://example.com/advanced-search/legislation",
    }),
    null,
  );
});

test("builds deterministic exponential cooldown receipts capped at maximum", () => {
  const classification = classifyAutomaticVerification(observed);
  const first = buildCooldownState({
    classification,
    nowMs: Date.parse("2026-07-26T06:00:00Z"),
    baseCooldownMs: 600_000,
    maxCooldownMs: 2_400_000,
  });
  const second = buildCooldownState({
    previousState: first,
    classification,
    nowMs: Date.parse("2026-07-26T06:10:00Z"),
    baseCooldownMs: 600_000,
    maxCooldownMs: 2_400_000,
  });
  const third = buildCooldownState({
    previousState: second,
    classification,
    nowMs: Date.parse("2026-07-26T06:30:00Z"),
    baseCooldownMs: 600_000,
    maxCooldownMs: 2_400_000,
  });
  const fourth = buildCooldownState({
    previousState: third,
    classification,
    nowMs: Date.parse("2026-07-26T07:10:00Z"),
    baseCooldownMs: 600_000,
    maxCooldownMs: 2_400_000,
  });
  assert.deepEqual(
    [first.cooldown_ms, second.cooldown_ms, third.cooldown_ms, fourth.cooldown_ms],
    [600_000, 1_200_000, 2_400_000, 2_400_000],
  );
  assert.equal(first.policy.captcha_bypass_allowed, false);
  assert.equal(first.policy.verification_interaction_allowed, false);
  assert.equal(
    remainingCooldownMs(first, Date.parse("2026-07-26T06:05:00Z")),
    300_000,
  );
  validateCooldownState(fourth);
});

test("rejects receipts that do not prove no interaction and no bypass", () => {
  const state = buildCooldownState({
    classification: classifyAutomaticVerification(observed),
    nowMs: Date.parse("2026-07-26T06:00:00Z"),
  });
  state.classification.captcha_bypass_attempted = true;
  assert.throws(
    () => validateCooldownState(state),
    /non-interaction\/no-bypass/,
  );
});

test("rejects a shortened next-retry timestamp", () => {
  const state = buildCooldownState({
    classification: classifyAutomaticVerification(observed),
    nowMs: Date.parse("2026-07-26T06:00:00Z"),
  });
  state.next_retry_not_before = "2026-07-26T06:00:01.000Z";
  assert.throws(
    () => validateCooldownState(state),
    /observation plus cooldown/,
  );
});

test("finds typed automatic timeout errors through an error cause chain", () => {
  const typed = new CongressAutomaticVerificationTimeoutError(
    classifyAutomaticVerification(observed),
    120_000,
  );
  const wrapped = new Error("navigation failed", { cause: typed });
  assert.equal(automaticVerificationError(wrapped), typed);
  assert.equal(typed.code, "CONGRESS_AUTOMATIC_VERIFICATION_TIMEOUT");
});
