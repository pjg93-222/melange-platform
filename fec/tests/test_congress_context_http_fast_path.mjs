import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";

import {
  createCongressContextHttpFastPath,
  parseRetryAfterMilliseconds,
  validateOfficialTextHttpBody,
} from "../lib/congress_context_http_fast_path.mjs";

const FIXTURES = path.join(import.meta.dirname, "fixtures");
const REQUESTED =
  "https://www.congress.gov/bill/119th-congress/house-bill/42/text";
const AMENDMENT_REQUESTED =
  "https://www.congress.gov/amendment/116th-congress/house-amendment/3/text";
const AMENDMENT_HTML = `<!doctype html>
<html><head><title>H.Amdt. 3 Amendment Text</title>
<link rel="canonical" href="https://www.congress.gov/amendment/116th-congress/house-amendment/3" />
</head><body><main id="main"><h1>H.Amdt. 3</h1><p>Amendment Text:</p>
<pre>Strike all after the enacting clause and insert the following complete amendment text.</pre>
</main></body></html>`;

class FakeResponse {
  constructor({
    status = 200,
    url = REQUESTED,
    headers = { "content-type": "text/html; charset=utf-8" },
    body = "",
  }) {
    this.value = { status, url, headers, body };
  }
  status() {
    return this.value.status;
  }
  url() {
    return this.value.url;
  }
  headers() {
    return this.value.headers;
  }
  async text() {
    return this.value.body;
  }
}

async function fixture(name) {
  return fs.readFile(path.join(FIXTURES, name), "utf8");
}

test("accepts only identity-bound positive official-text HTML", async () => {
  const official = await fixture("congress_text_official.html");
  const accepted = validateOfficialTextHttpBody({
    requestedUrl: REQUESTED,
    loadedUrl: REQUESTED,
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8" },
    body: official,
  });
  assert.equal(accepted.accepted, true);
  assert.match(accepted.body_sha256, /^[0-9a-f]{64}$/);

  const wrongMeasure = validateOfficialTextHttpBody({
    requestedUrl: REQUESTED,
    loadedUrl:
      "https://www.congress.gov/bill/119th-congress/house-bill/43/text",
    status: 200,
    headers: { "content-type": "text/html" },
    body: official,
  });
  assert.equal(wrongMeasure.accepted, false);
  assert.ok(wrongMeasure.failed.includes("loaded_identity_matches"));
});

test("accepts typed amendment text and rejects bill/amendment identity crossover", () => {
  const accepted = validateOfficialTextHttpBody({
    requestedUrl: AMENDMENT_REQUESTED,
    loadedUrl: AMENDMENT_REQUESTED,
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8" },
    body: AMENDMENT_HTML,
  });
  assert.equal(accepted.accepted, true);
  assert.equal(accepted.expected_marker, "Amendment Text:");
  assert.equal(accepted.validation.amendment_text_marker_present, true);

  const wrongKind = validateOfficialTextHttpBody({
    requestedUrl: REQUESTED,
    loadedUrl: AMENDMENT_REQUESTED,
    status: 200,
    headers: { "content-type": "text/html" },
    body: AMENDMENT_HTML,
  });
  assert.equal(wrongKind.accepted, false);
  assert.ok(wrongKind.failed.includes("loaded_identity_matches"));
  assert.ok(wrongKind.failed.includes("shown_here_marker_present"));
});

test("quarantines challenge, ambiguous, empty, and non-HTML surfaces", async () => {
  for (const [name, expectedFailure] of [
    ["congress_text_challenge.html", "no_challenge_surface"],
    ["congress_text_ambiguous.html", "shown_here_marker_present"],
  ]) {
    const checked = validateOfficialTextHttpBody({
      requestedUrl: REQUESTED,
      loadedUrl: REQUESTED,
      status: 200,
      headers: { "content-type": "text/html" },
      body: await fixture(name),
    });
    assert.equal(checked.accepted, false);
    assert.ok(checked.failed.includes(expectedFailure));
  }
  const empty = validateOfficialTextHttpBody({
    requestedUrl: REQUESTED,
    loadedUrl: REQUESTED,
    status: 200,
    headers: { "content-type": "text/html" },
    body: "",
  });
  assert.equal(empty.accepted, false);
  assert.ok(empty.failed.includes("nonempty_body"));

  const nonHtml = validateOfficialTextHttpBody({
    requestedUrl: REQUESTED,
    loadedUrl: REQUESTED,
    status: 200,
    headers: { "content-type": "application/json" },
    body: await fixture("congress_text_official.html"),
  });
  assert.equal(nonHtml.accepted, false);
  assert.ok(nonHtml.failed.includes("html_content_type"));
});

test("quarantines Retry-After immediately without retry sleeps", async () => {
  const responses = [
    new FakeResponse({
      status: 429,
      headers: { "retry-after": "2", "content-type": "text/html" },
    }),
  ];
  const requestStarts = [];
  const sleeps = [];
  let gets = 0;
  const fastPath = createCongressContextHttpFastPath({
    requestContext: {
      async get() {
        const response = responses[gets];
        gets += 1;
        return response;
      },
    },
    scheduleRequest: async () => {
      requestStarts.push(gets);
    },
    sleeper: async (milliseconds) => {
      sleeps.push(milliseconds);
    },
  });
  const result = await fastPath.fetch(REQUESTED);
  assert.equal(result.kind, "browser_fallback");
  assert.equal(result.reason, "http_retry_exhausted");
  assert.equal(gets, 1);
  assert.equal(requestStarts.length, 1);
  assert.deepEqual(sleeps, []);
  assert.deepEqual(
    result.attempts.map((attempt) => attempt.outcome),
    ["retryable_status_quarantined"],
  );
  assert.throws(
    () =>
      createCongressContextHttpFastPath({
        requestContext: { get: async () => responses[0] },
        scheduleRequest: async () => {},
        maxAttempts: 2,
      }),
    /strictly one-shot/,
  );
});

test("deduplicates concurrent logical fetches without extra request starts", async () => {
  const official = await fixture("congress_text_official.html");
  let gets = 0;
  let starts = 0;
  const fastPath = createCongressContextHttpFastPath({
    requestContext: {
      async get() {
        gets += 1;
        return new FakeResponse({ body: official });
      },
    },
    scheduleRequest: async () => {
      starts += 1;
    },
  });
  const [left, right, third] = await Promise.all([
    fastPath.fetch(REQUESTED),
    fastPath.fetch(REQUESTED),
    fastPath.fetch(REQUESTED),
  ]);
  assert.equal(left.kind, "accepted");
  assert.equal(right.body_sha256, left.body_sha256);
  assert.equal(third.body_sha256, left.body_sha256);
  assert.equal(gets, 1);
  assert.equal(starts, 1);
  assert.equal(fastPath.snapshot().deduplicated_fetches, 2);
});

test("forwards the live browser headers and performs one shot with no sleep", async () => {
  let observedOptions = null;
  let sleeps = 0;
  const fastPath = createCongressContextHttpFastPath({
    requestContext: {
      async get(_url, options) {
        observedOptions = options;
        return new FakeResponse({ status: 429 });
      },
    },
    scheduleRequest: async () => {},
    maxAttempts: 1,
    retryBaseMs: 0,
    requestHeaders: {
      "user-agent": "live-browser-user-agent",
      "accept-language": "en-US,en;q=0.9",
      referer: "https://www.congress.gov/",
    },
    sleeper: async () => {
      sleeps += 1;
    },
  });
  const result = await fastPath.fetch(REQUESTED);
  assert.equal(result.kind, "browser_fallback");
  assert.equal(result.attempts.length, 1);
  assert.equal(sleeps, 0);
  assert.equal(
    observedOptions.headers["user-agent"],
    "live-browser-user-agent",
  );
});

test("non-2xx and unsafe 2xx responses select browser fallback", async () => {
  for (const response of [
    new FakeResponse({ status: 404 }),
    new FakeResponse({
      body: await fixture("congress_text_challenge.html"),
    }),
  ]) {
    const fastPath = createCongressContextHttpFastPath({
      requestContext: { get: async () => response },
      scheduleRequest: async () => {},
    });
    const result = await fastPath.fetch(REQUESTED);
    assert.equal(result.kind, "browser_fallback");
    assert.equal(result.attempts.length, 1);
  }
});

test("parses seconds and HTTP-date Retry-After values with a hard cap", () => {
  assert.equal(parseRetryAfterMilliseconds("1.5"), 1500);
  assert.equal(parseRetryAfterMilliseconds("999"), 60_000);
  assert.equal(
    parseRetryAfterMilliseconds("Thu, 01 Jan 1970 00:00:05 GMT", {
      now: () => 1000,
    }),
    4000,
  );
  assert.equal(parseRetryAfterMilliseconds("invalid"), null);
});
