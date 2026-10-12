import crypto from "node:crypto";

const CHALLENGE_PATTERN =
  /just a moment|checking your browser|attention required|verify you are human|verifying you are human|human verification|cf-browser-verification|challenge-platform|cf-challenge/i;
const NOT_FOUND_PATTERN =
  /page not found|requested page could not be found|error 404/i;

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function compact(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function headerValue(headers, name) {
  const target = name.toLowerCase();
  for (const [key, value] of Object.entries(headers ?? {})) {
    if (key.toLowerCase() === target) return String(value);
  }
  return null;
}

function canonicalMeasureIdentity(rawUrl) {
  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    return null;
  }
  const match = url.pathname.match(
    /^\/(bill|amendment)\/(\d+)(?:st|nd|rd|th)-congress\/([^/]+)\/(\d+)(?:\/text)?\/?$/i,
  );
  if (!match) return null;
  return {
    origin: url.origin.toLowerCase(),
    kind: match[1].toLowerCase(),
    congress: Number.parseInt(match[2], 10),
    type: match[3].toLowerCase(),
    number: Number.parseInt(match[4], 10),
    has_text_suffix: /\/text\/?$/i.test(url.pathname),
  };
}

function sameMeasure(left, right) {
  return Boolean(
    left &&
      right &&
      left.origin === right.origin &&
      left.kind === right.kind &&
      left.congress === right.congress &&
      left.type === right.type &&
      left.number === right.number,
  );
}

function canonicalLinks(html) {
  const links = String(html ?? "").match(/<link\b[^>]*>/gi) ?? [];
  const hrefs = [];
  for (const link of links) {
    if (!/\brel\s*=\s*["'][^"']*\bcanonical\b[^"']*["']/i.test(link)) {
      continue;
    }
    const href = link.match(/\bhref\s*=\s*["']([^"']+)["']/i)?.[1];
    if (href) hrefs.push(href);
  }
  return hrefs;
}

function mainSurfaceText(html) {
  const source = String(html ?? "");
  const opening = source.match(
    /<main\b[^>]*>|<[a-z][\w:-]*\b[^>]*\bid\s*=\s*["']main["'][^>]*>/i,
  );
  if (!opening || opening.index === undefined) return "";
  const tagName = opening[0].match(/^<([a-z][\w:-]*)/i)?.[1];
  if (!tagName) return "";
  const tail = source.slice(opening.index + opening[0].length);
  const closing = new RegExp("</" + tagName + "\\s*>", "i").exec(tail);
  const region = closing ? tail.slice(0, closing.index) : tail;
  return compact(
    region
      .replace(/<script\b[^>]*>[\s\S]*?<\/script\s*>/gi, " ")
      .replace(/<style\b[^>]*>[\s\S]*?<\/style\s*>/gi, " ")
      .replace(/<[^>]+>/g, " "),
  );
}

/**
 * Validate an official Congress.gov text response without treating absence or
 * ambiguity as evidence. This is intentionally stricter than the browser
 * fallback: only a positive, identity-bound bill-text surface is HTTP-eligible.
 */
export function validateOfficialTextHttpBody({
  requestedUrl,
  loadedUrl,
  status,
  headers,
  body,
}) {
  const envelope = validateCongressHtmlEnvelope({
    requestedUrl,
    loadedUrl,
    status,
    headers,
    body,
  });
  const requested = canonicalMeasureIdentity(requestedUrl);
  const loaded = canonicalMeasureIdentity(loadedUrl);
  const text = String(body ?? "");
  const expectedMarker =
    requested?.kind === "amendment" ? "Amendment Text:" : "Shown Here:";
  const expectedMarkerPattern =
    expectedMarker === "Amendment Text:"
      ? /\bAmendment\s+Text\s*:/i
      : /\bShown\s+Here\s*:/i;
  const mainText = mainSurfaceText(text);
  const shownHereIndex = mainText.search(/\bShown\s+Here\s*:/i);
  const amendmentTextIndex = mainText.search(/\bAmendment\s+Text\s*:/i);
  const expectedMarkerIndex =
    expectedMarker === "Amendment Text:"
      ? amendmentTextIndex
      : shownHereIndex;
  const supportedMarkerIndexes = [shownHereIndex, amendmentTextIndex].filter(
    (index) => index >= 0,
  );
  const validation = {
    ...envelope.validation,
    no_not_found_surface: !NOT_FOUND_PATTERN.test(
      compact(text).slice(0, 20_000),
    ),
    requested_identity_valid: Boolean(requested?.has_text_suffix),
    loaded_identity_matches: sameMeasure(requested, loaded),
    loaded_text_path: Boolean(loaded?.has_text_suffix),
    main_region_present:
      /<main\b[^>]*>|<[a-z][\w:-]*\b[^>]*\bid\s*=\s*["']main["'][^>]*>/i.test(text),
    shown_here_marker_present:
      requested?.kind !== "bill" || /\bShown\s+Here\s*:/i.test(text),
    amendment_text_marker_present:
      requested?.kind !== "amendment" ||
      /\bAmendment\s+Text\s*:/i.test(text),
    expected_typed_marker_present: expectedMarkerPattern.test(text),
    expected_typed_marker_in_main: expectedMarkerIndex >= 0,
    expected_typed_marker_is_first_supported_surface:
      expectedMarkerIndex >= 0 &&
      expectedMarkerIndex === Math.min(...supportedMarkerIndexes),
    expected_typed_surface_nonempty:
      expectedMarkerIndex >= 0 &&
      mainText
        .slice(expectedMarkerIndex)
        .replace(expectedMarkerPattern, "")
        .trim().length > 0,
    canonical_identity_matches: false,
  };
  if (requested) {
    validation.canonical_identity_matches = canonicalLinks(text).some((href) => {
      try {
        return sameMeasure(requested, canonicalMeasureIdentity(new URL(href, loadedUrl).href));
      } catch {
        return false;
      }
    });
  }
  const failed = Object.entries(validation)
    .filter(([, passed]) => passed !== true)
    .map(([name]) => name);
  return {
    accepted: failed.length === 0,
    failed,
    validation,
    body_sha256: envelope.body_sha256,
    body_byte_length: envelope.body_byte_length,
    content_type: envelope.content_type,
    expected_marker: expectedMarker,
  };
}

export function validateCongressHtmlEnvelope({
  requestedUrl,
  loadedUrl,
  status,
  headers,
  body,
}) {
  const text = String(body ?? "");
  const compactText = compact(text);
  const contentType = headerValue(headers, "content-type");
  let requested;
  let loaded;
  try {
    requested = new URL(requestedUrl);
    loaded = new URL(loadedUrl);
  } catch {
    requested = null;
    loaded = null;
  }
  const validation = {
    status_2xx: Number.isInteger(status) && status >= 200 && status < 300,
    html_content_type: Boolean(
      contentType && /(?:text\/html|application\/xhtml\+xml)/i.test(contentType),
    ),
    nonempty_body: compactText.length >= 200,
    no_challenge_surface: !CHALLENGE_PATTERN.test(
      compactText.slice(0, 50_000),
    ),
    requested_congress_origin:
      requested?.origin.toLowerCase() === "https://www.congress.gov",
    loaded_congress_origin:
      loaded?.origin.toLowerCase() === "https://www.congress.gov",
  };
  const failed = Object.entries(validation)
    .filter(([, passed]) => passed !== true)
    .map(([name]) => name);
  return {
    accepted: failed.length === 0,
    failed,
    validation,
    body_sha256: sha256(text),
    body_byte_length: Buffer.byteLength(text, "utf8"),
    content_type: contentType,
  };
}

export function parseRetryAfterMilliseconds(
  value,
  { now = () => Date.now(), maximumMs = 60_000 } = {},
) {
  if (value === null || value === undefined || value === "") return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) {
    return Math.min(maximumMs, Math.ceil(seconds * 1000));
  }
  const timestamp = Date.parse(String(value));
  if (!Number.isFinite(timestamp)) return null;
  return Math.min(maximumMs, Math.max(0, timestamp - now()));
}

export function createCongressCookieReplayRequestContext({
  cookieHeader,
  defaultHeaders = {},
  fetchImpl = globalThis.fetch,
}) {
  if (typeof cookieHeader !== "string" || !cookieHeader.trim()) {
    throw new Error("A nonempty in-memory Congress cookie header is required");
  }
  if (typeof fetchImpl !== "function") {
    throw new Error("A fetch implementation is required");
  }
  return {
    async get(url, { headers = {}, timeout = 45_000 } = {}) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeout);
      let response;
      try {
        response = await fetchImpl(url, {
          method: "GET",
          headers: {
            ...defaultHeaders,
            ...headers,
            Cookie: cookieHeader,
          },
          redirect: "follow",
          signal: controller.signal,
        });
      } catch (error) {
        const reason =
          error?.name === "AbortError"
            ? "timed out"
            : "failed before a response";
        throw new Error(`Congress cookie-replay request ${reason}`);
      } finally {
        clearTimeout(timer);
      }
      let bodyPromise = null;
      return {
        status: () => response.status,
        url: () => response.url,
        headers: () => Object.fromEntries(response.headers.entries()),
        text: () => {
          bodyPromise ??= response.text();
          return bodyPromise;
        },
      };
    },
  };
}

function responseReceipt({
  attempt,
  status = null,
  loadedUrl = null,
  outcome,
  retryDelayMs = null,
  validation = null,
  error = null,
}) {
  return {
    attempt,
    status,
    loaded_url: loadedUrl,
    outcome,
    retry_delay_ms: retryDelayMs,
    validation,
    error:
      error === null
        ? null
        : {
            name: error.name ?? "Error",
            message: error.message ?? String(error),
          },
  };
}

/**
 * Create a bounded, browser-context-seeded HTTP transport.
 *
 * `requestContext` must be the `BrowserContext.request` instance. No cookies or
 * headers are exported. `scheduleRequest` is shared with browser navigation so
 * all logical workers preserve one global request-start cadence.
 */
export function createCongressContextHttpFastPath({
  requestContext,
  scheduleRequest,
  maxAttempts = 1,
  retryBaseMs = 0,
  maximumRetryMs = 60_000,
  requestTimeoutMs = 45_000,
  sleeper = (milliseconds) =>
    new Promise((resolve) => setTimeout(resolve, milliseconds)),
  now = () => Date.now(),
  dedupeEntries = 16,
  requestHeaders = {},
  validateResponse = validateOfficialTextHttpBody,
}) {
  if (!requestContext || typeof requestContext.get !== "function") {
    throw new Error("requestContext.get is required");
  }
  if (typeof scheduleRequest !== "function") {
    throw new Error("scheduleRequest must be a function");
  }
  if (maxAttempts !== 1) {
    throw new Error("Congress HTTP fast path is strictly one-shot");
  }
  if (!Number.isFinite(retryBaseMs) || retryBaseMs < 0) {
    throw new Error("retryBaseMs must be a non-negative finite number");
  }
  if (!Number.isInteger(dedupeEntries) || dedupeEntries < 1) {
    throw new Error("dedupeEntries must be a positive integer");
  }
  if (typeof validateResponse !== "function") {
    throw new Error("validateResponse must be a function");
  }
  if (!requestHeaders || typeof requestHeaders !== "object") {
    throw new Error("requestHeaders must be an object");
  }

  const cache = new Map();
  const stats = {
    logical_fetches: 0,
    distinct_request_urls: 0,
    deduplicated_fetches: 0,
    accepted_http_responses: 0,
    quarantined_for_browser: 0,
    request_attempts: 0,
  };

  const remember = (url, promise) => {
    cache.delete(url);
    cache.set(url, promise);
    while (cache.size > dedupeEntries) {
      cache.delete(cache.keys().next().value);
    }
  };

  const fetchDistinct = async (url) => {
    stats.distinct_request_urls += 1;
    const attempts = [];
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      await scheduleRequest();
      stats.request_attempts += 1;
      let response;
      try {
        response = await requestContext.get(url, {
          failOnStatusCode: false,
          timeout: requestTimeoutMs,
          headers: { ...requestHeaders },
        });
      } catch (error) {
        if (attempt < maxAttempts) {
          const retryDelayMs = Math.min(
            maximumRetryMs,
            retryBaseMs * 2 ** (attempt - 1),
          );
          attempts.push(
            responseReceipt({
              attempt,
              outcome: "transport_error_retry",
              retryDelayMs,
              error,
            }),
          );
          await sleeper(retryDelayMs);
          continue;
        }
        attempts.push(
          responseReceipt({
            attempt,
            outcome: "transport_error_quarantined",
            error,
          }),
        );
        stats.quarantined_for_browser += 1;
        return {
          kind: "browser_fallback",
          reason: "http_transport_error",
          attempts,
        };
      }

      const status = response.status();
      const loadedUrl = response.url();
      const headers = response.headers();
      if (status === 429 || status >= 500) {
        if (attempt < maxAttempts) {
          const retryAfterMs = parseRetryAfterMilliseconds(
            headerValue(headers, "retry-after"),
            { now, maximumMs: maximumRetryMs },
          );
          const retryDelayMs =
            retryAfterMs ??
            Math.min(maximumRetryMs, retryBaseMs * 2 ** (attempt - 1));
          attempts.push(
            responseReceipt({
              attempt,
              status,
              loadedUrl,
              outcome: "retryable_status_retry",
              retryDelayMs,
            }),
          );
          await sleeper(retryDelayMs);
          continue;
        }
        attempts.push(
          responseReceipt({
            attempt,
            status,
            loadedUrl,
            outcome: "retryable_status_quarantined",
          }),
        );
        stats.quarantined_for_browser += 1;
        return {
          kind: "browser_fallback",
          reason: "http_retry_exhausted",
          attempts,
        };
      }

      if (status < 200 || status >= 300) {
        attempts.push(
          responseReceipt({
            attempt,
            status,
            loadedUrl,
            outcome: "non_2xx_quarantined",
          }),
        );
        stats.quarantined_for_browser += 1;
        return {
          kind: "browser_fallback",
          reason: "http_non_2xx",
          attempts,
        };
      }

      let body;
      try {
        body = await response.text();
      } catch (error) {
        attempts.push(
          responseReceipt({
            attempt,
            status,
            loadedUrl,
            outcome: "body_read_error_quarantined",
            error,
          }),
        );
        stats.quarantined_for_browser += 1;
        return {
          kind: "browser_fallback",
          reason: "http_body_read_error",
          attempts,
        };
      }
      const checked = await validateResponse({
        requestedUrl: url,
        loadedUrl,
        status,
        headers,
        body,
      });
      attempts.push(
        responseReceipt({
          attempt,
          status,
          loadedUrl,
          outcome: checked.accepted
            ? "accepted"
            : "unsafe_surface_quarantined",
          validation: checked,
        }),
      );
      if (!checked.accepted) {
        stats.quarantined_for_browser += 1;
        return {
          kind: "browser_fallback",
          reason: "http_surface_validation_failed",
          attempts,
        };
      }
      stats.accepted_http_responses += 1;
      return {
        kind: "accepted",
        status,
        loaded_url: loadedUrl,
        body,
        body_sha256: checked.body_sha256,
        body_byte_length: checked.body_byte_length,
        content_type: checked.content_type,
        validation: checked.validation,
        attempts,
      };
    }
    throw new Error("unreachable HTTP retry state");
  };

  return {
    async fetch(url) {
      stats.logical_fetches += 1;
      const key = new URL(url).href;
      if (cache.has(key)) {
        stats.deduplicated_fetches += 1;
        const prior = cache.get(key);
        remember(key, prior);
        return prior;
      }
      const promise = fetchDistinct(key);
      remember(key, promise);
      return promise;
    },
    snapshot() {
      return { ...stats, dedupe_cache_entries: cache.size };
    },
  };
}
