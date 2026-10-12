const STATE_SCHEMA = "congress-automatic-verification-cooldown-v1";
const AUTOMATIC_TIMEOUT_CODE = "CONGRESS_AUTOMATIC_VERIFICATION_TIMEOUT";

export class CongressAutomaticVerificationTimeoutError extends Error {
  constructor(observation, waitMs) {
    super(
      "Congress.gov automatic security verification did not complete " +
        `within ${Math.round(waitMs / 1000)} seconds; collection stopped ` +
        "without interacting with or bypassing the verification page.",
    );
    this.name = "CongressAutomaticVerificationTimeoutError";
    this.code = AUTOMATIC_TIMEOUT_CODE;
    this.classification = observation;
    this.wait_ms = waitMs;
  }
}

export function classifyAutomaticVerification({
  loadedUrl,
  title,
  bodyText,
}) {
  let hostname = null;
  try {
    hostname = new URL(loadedUrl).hostname.toLowerCase();
  } catch {
    return null;
  }
  if (hostname !== "www.congress.gov" && hostname !== "congress.gov") {
    return null;
  }
  const text = `${title ?? ""} ${bodyText ?? ""}`;
  const isAutomatic =
    /just a moment/i.test(title ?? "") &&
    (
      /performing security verification/i.test(text) ||
      /displayed while the website verifies you are not a bot/i.test(text)
    );
  if (!isAutomatic) return null;
  const rayId = String(bodyText ?? "").match(/\bRay ID:\s*([a-z0-9]+)/i)?.[1];
  return {
    kind: "automatic_security_verification",
    provider: /cloudflare/i.test(text) ? "cloudflare" : "unknown",
    interactive: false,
    collection_outcome: "not_a_result_page",
    loaded_url: loadedUrl,
    page_title: title,
    ray_id: rayId ?? null,
    captcha_bypass_attempted: false,
    verification_interaction_performed: false,
  };
}

export function automaticVerificationError(error) {
  let current = error;
  const seen = new Set();
  while (current && !seen.has(current)) {
    seen.add(current);
    if (
      current.code === AUTOMATIC_TIMEOUT_CODE &&
      current.classification?.kind === "automatic_security_verification"
    ) {
      return current;
    }
    current = current.cause;
  }
  return null;
}

function parseIsoMs(value) {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function validateCooldownState(value) {
  if (!value || typeof value !== "object") {
    throw new Error("Automatic-verification cooldown receipt must be an object");
  }
  if (value.schema !== STATE_SCHEMA) {
    throw new Error(
      `Unexpected automatic-verification cooldown schema: ${value.schema}`,
    );
  }
  if (value.status !== "active") {
    throw new Error(
      `Unexpected automatic-verification cooldown status: ${value.status}`,
    );
  }
  if (
    !Number.isInteger(value.consecutive_timeouts) ||
    value.consecutive_timeouts < 1
  ) {
    throw new Error(
      "Automatic-verification consecutive_timeouts must be a positive integer",
    );
  }
  if (!Number.isFinite(value.cooldown_ms) || value.cooldown_ms < 0) {
    throw new Error(
      "Automatic-verification cooldown_ms must be a non-negative number",
    );
  }
  const observedMs = parseIsoMs(value.observed_at);
  if (observedMs === null) {
    throw new Error("Automatic-verification observed_at must be an ISO timestamp");
  }
  const nextRetryMs = parseIsoMs(value.next_retry_not_before);
  if (nextRetryMs === null) {
    throw new Error(
      "Automatic-verification next_retry_not_before must be an ISO timestamp",
    );
  }
  if (nextRetryMs !== observedMs + value.cooldown_ms) {
    throw new Error(
      "Automatic-verification next retry must equal observation plus cooldown",
    );
  }
  if (
    value.policy?.kind !== "exponential_backoff" ||
    value.policy?.reset_after_validated_result_page !== true ||
    value.policy?.automatic_refresh_or_reload_during_cooldown !== false ||
    value.policy?.captcha_bypass_allowed !== false ||
    value.policy?.verification_interaction_allowed !== false
  ) {
    throw new Error(
      "Automatic-verification cooldown policy is not fail-closed/no-bypass",
    );
  }
  if (
    value.classification?.kind !== "automatic_security_verification" ||
    value.classification?.interactive !== false ||
    value.classification?.captcha_bypass_attempted !== false ||
    value.classification?.verification_interaction_performed !== false
  ) {
    throw new Error(
      "Automatic-verification receipt does not prove non-interaction/no-bypass",
    );
  }
  return value;
}

export function buildCooldownState({
  previousState = null,
  classification,
  nowMs = Date.now(),
  baseCooldownMs = 10 * 60_000,
  maxCooldownMs = 2 * 60 * 60_000,
}) {
  if (!Number.isFinite(baseCooldownMs) || baseCooldownMs < 0) {
    throw new Error("baseCooldownMs must be a non-negative number");
  }
  if (
    !Number.isFinite(maxCooldownMs) ||
    maxCooldownMs < baseCooldownMs
  ) {
    throw new Error("maxCooldownMs must be at least baseCooldownMs");
  }
  if (
    classification?.kind !== "automatic_security_verification" ||
    classification?.interactive !== false
  ) {
    throw new Error(
      "Cooldowns may only be created for classified automatic verification",
    );
  }
  let consecutiveTimeouts = 1;
  if (previousState !== null) {
    validateCooldownState(previousState);
    consecutiveTimeouts = previousState.consecutive_timeouts + 1;
  }
  const multiplier = 2 ** Math.min(consecutiveTimeouts - 1, 30);
  const cooldownMs = Math.min(
    maxCooldownMs,
    baseCooldownMs * multiplier,
  );
  const state = {
    schema: STATE_SCHEMA,
    status: "active",
    observed_at: new Date(nowMs).toISOString(),
    consecutive_timeouts: consecutiveTimeouts,
    cooldown_ms: cooldownMs,
    next_retry_not_before: new Date(nowMs + cooldownMs).toISOString(),
    policy: {
      kind: "exponential_backoff",
      base_cooldown_ms: baseCooldownMs,
      max_cooldown_ms: maxCooldownMs,
      reset_after_validated_result_page: true,
      automatic_refresh_or_reload_during_cooldown: false,
      captcha_bypass_allowed: false,
      verification_interaction_allowed: false,
    },
    classification: {
      ...classification,
      captcha_bypass_attempted: false,
      verification_interaction_performed: false,
    },
  };
  return validateCooldownState(state);
}

export function remainingCooldownMs(state, nowMs = Date.now()) {
  validateCooldownState(state);
  return Math.max(0, parseIsoMs(state.next_retry_not_before) - nowMs);
}

export const automaticVerificationConstants = Object.freeze({
  stateSchema: STATE_SCHEMA,
  timeoutCode: AUTOMATIC_TIMEOUT_CODE,
});
