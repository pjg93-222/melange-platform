#!/usr/bin/env node
/**
 * Collect official Congress.gov text-context evidence for category relevance.
 *
 * Discovery remains exclusively the completed Advanced Search corpus. This
 * enrichment visits the official /text page for each recovered measure and
 * records exact/proximate identifier contexts without storing entire bill
 * texts. It never deletes candidates and never makes an exclusion decision.
 */

import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { gunzipSync, gzipSync } from "node:zlib";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import {
  writeTextAtomicWithRetry,
} from "./lib/atomic_checkpoint.mjs";
import { createGlobalRateLimiter } from "./lib/global_rate_limiter.mjs";
import { createCongressContextHttpFastPath } from "./lib/congress_context_http_fast_path.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const ROOT = path.resolve(import.meta.dirname, "..");
const DEFAULT_RUN = path.join(
  ROOT,
  "audits",
  "legfix5",
  "congress_advanced_final_20260725",
);
const DEFAULT_REGISTRY = path.join(
  ROOT,
  "source_data",
  "legislation",
  "acu_legislation_search_registry.json",
);
const DEFAULT_CHROME =
  process.platform === "win32"
    ? "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    : "/usr/bin/google-chrome";

function parseArgs(argv) {
  const args = {
    run: DEFAULT_RUN,
    registry: DEFAULT_REGISTRY,
    out: null,
    profile: path.join(os.tmpdir(), "codex-congress-text-relevance-profile"),
    endpoint: null,
    chrome: DEFAULT_CHROME,
    headless: true,
    delayMs: 750,
    workers: 3,
    browserMaxAttempts: 3,
    pageTimeoutMs: 30_000,
    httpFastPath: false,
    httpWorkers: 6,
    httpMaxAttempts: 1,
    httpRetryBaseMs: 0,
    freshHttpCanaryManifest: null,
    httpParityInterval: 100,
    billstatusManifest: null,
    canary: false,
    resume: false,
    keys: null,
    limit: null,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--run") args.run = argv[++index];
    else if (value === "--registry") args.registry = argv[++index];
    else if (value === "--out") args.out = argv[++index];
    else if (value === "--profile") args.profile = argv[++index];
    else if (value === "--endpoint") args.endpoint = argv[++index];
    else if (value === "--chrome") args.chrome = argv[++index];
    else if (value === "--delay-ms") args.delayMs = Number(argv[++index]);
    else if (value === "--workers") args.workers = Number(argv[++index]);
    else if (value === "--browser-max-attempts") {
      args.browserMaxAttempts = Number(argv[++index]);
    }
    else if (value === "--page-timeout-ms") {
      args.pageTimeoutMs = Number(argv[++index]);
    }
    else if (value === "--http-fast-path") args.httpFastPath = true;
    else if (value === "--http-workers") {
      args.httpWorkers = Number(argv[++index]);
    } else if (value === "--http-max-attempts") {
      args.httpMaxAttempts = Number(argv[++index]);
    } else if (value === "--http-retry-base-ms") {
      args.httpRetryBaseMs = Number(argv[++index]);
    } else if (value === "--fresh-http-canary-manifest") {
      args.freshHttpCanaryManifest = argv[++index];
    } else if (value === "--http-parity-interval") {
      args.httpParityInterval = Number(argv[++index]);
    } else if (value === "--billstatus-manifest") {
      args.billstatusManifest = argv[++index];
    }
    else if (value === "--canary") args.canary = true;
    else if (value === "--keys") {
      args.keys = new Set(
        argv[++index]
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      );
    } else if (value === "--limit") args.limit = Number(argv[++index]);
    else if (value === "--headless") args.headless = true;
    else if (value === "--headed") args.headless = false;
    else if (value === "--resume") args.resume = true;
    else if (value === "--help") {
      console.log(`Usage: node fec/enrich_congress_text_relevance_pages.mjs [options]
  --run PATH        Completed Advanced Search run directory
  --registry PATH   Search-outline registry
  --out PATH        Output directory (default RUN/text_relevance)
  --profile PATH    Isolated Chrome profile directory
  --endpoint URL    Attach to an already-visible Chrome remote-debug context
  --chrome PATH     Chrome executable
  --delay-ms N      Delay between Congress.gov pages (default 750)
  --workers N       Concurrent tabs sharing the global delay (default 3)
  --browser-max-attempts N  Bounded browser attempts, 1-3 (default 3)
  --http-fast-path  Opt in to context-seeded HTTP with strict browser fallback
  --http-workers N  Logical HTTP workers sharing the global delay (default 6)
  --http-max-attempts N  Bounded HTTP attempts, 1-5 (default 1)
  --http-retry-base-ms N  HTTP exponential-backoff base (default 0)
  --fresh-http-canary-manifest FILE  Browser canary that must pass live bill+amendment HTTP parity
  --http-parity-interval N  Browser parity audit interval for fresh HTTP accepts (default 100)
  --keys IDS        Comma-separated Congress-qualified keys
  --limit N         Process only the first N selected records
  --headless        Run Chrome headless (default)
  --headed          Run headed only when explicitly requested
  --resume          Reuse evidence artifacts that pass provenance checks`);
      process.exit(0);
    } else {
      throw new Error(`Unknown argument: ${value}`);
    }
  }
  if (!Number.isFinite(args.delayMs) || args.delayMs < 0) {
    throw new Error("--delay-ms must be a non-negative number");
  }
  if (
    !Number.isInteger(args.workers) ||
    args.workers < 1 ||
    args.workers > 8
  ) {
    throw new Error("--workers must be an integer between 1 and 8");
  }
  if (
    !Number.isInteger(args.browserMaxAttempts) ||
    args.browserMaxAttempts < 1 ||
    args.browserMaxAttempts > 3
  ) {
    throw new Error("--browser-max-attempts must be an integer between 1 and 3");
  }
  if (
    !Number.isInteger(args.httpWorkers) ||
    args.httpWorkers < 1 ||
    args.httpWorkers > 8
  ) {
    throw new Error("--http-workers must be an integer between 1 and 8");
  }
  if (
    !Number.isInteger(args.httpMaxAttempts) ||
    args.httpMaxAttempts < 1 ||
    args.httpMaxAttempts > 5
  ) {
    throw new Error("--http-max-attempts must be an integer between 1 and 5");
  }
  if (
    !Number.isFinite(args.httpRetryBaseMs) ||
    args.httpRetryBaseMs < 0
  ) {
    throw new Error("--http-retry-base-ms must be a non-negative number");
  }
  if (
    !Number.isInteger(args.httpParityInterval) ||
    args.httpParityInterval < 1
  ) {
    throw new Error("--http-parity-interval must be a positive integer");
  }
  if (args.freshHttpCanaryManifest && !args.httpFastPath) {
    throw new Error("--fresh-http-canary-manifest requires --http-fast-path");
  }
  if (
    args.freshHttpCanaryManifest &&
    (args.httpMaxAttempts !== 1 || args.httpRetryBaseMs !== 0)
  ) {
    throw new Error(
      "Fresh HTTP collection requires --http-max-attempts 1 and --http-retry-base-ms 0",
    );
  }
  if (args.freshHttpCanaryManifest && args.headless) {
    throw new Error("Fresh HTTP collection requires a headed clearance browser");
  }
  if (args.freshHttpCanaryManifest && !args.endpoint) {
    throw new Error(
      "Fresh HTTP collection requires --endpoint for a user-visible cleared Chrome",
    );
  }
  if (args.httpFastPath && args.delayMs !== 0) {
    throw new Error("HTTP fast-path collection requires --delay-ms 0");
  }
  if (
    args.httpFastPath &&
    (args.httpMaxAttempts !== 1 || args.httpRetryBaseMs !== 0)
  ) {
    throw new Error(
      "HTTP fast-path collection is one-shot: attempts must be 1 and retry delay 0",
    );
  }
  if (
    !Number.isInteger(args.pageTimeoutMs) ||
    args.pageTimeoutMs < 5_000 ||
    args.pageTimeoutMs > 120_000
  ) {
    throw new Error("--page-timeout-ms must be 5000 through 120000");
  }
  if ((args.keys || args.limit !== null) && !args.canary) {
    throw new Error("--keys and --limit require --canary");
  }
  if (
    args.limit !== null &&
    (!Number.isInteger(args.limit) || args.limit < 1)
  ) {
    throw new Error("--limit must be a positive integer");
  }
  return args;
}

function assertWithinRoot(target) {
  const resolved = path.resolve(ROOT, target);
  const relative = path.relative(ROOT, resolved);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`Path must stay within repository root: ${resolved}`);
  }
  return resolved;
}

function stableHash(value) {
  return crypto
    .createHash("sha256")
    .update(typeof value === "string" ? value : JSON.stringify(value))
    .digest("hex");
}

function sha256Bytes(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function isoNow() {
  return new Date().toISOString();
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function readJson(file) {
  return JSON.parse(await fs.readFile(file, "utf8"));
}

async function readNdjsonWithHashes(file) {
  const text = await fs.readFile(file, "utf8");
  const rows = text
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line));
  return {
    rows,
    file_sha256: stableHash(text),
    rows_sha256: stableHash(rows),
  };
}

async function writeTextWithRetry(file, value, attempts = 30) {
  await writeTextAtomicWithRetry(file, value, { attempts });
}

async function writeJson(file, value) {
  await writeTextWithRetry(file, `${JSON.stringify(value, null, 2)}\n`);
}

async function writeNdjson(file, rows) {
  const body = rows.map((row) => JSON.stringify(row)).join("\n");
  await writeTextWithRetry(file, body ? `${body}\n` : "");
}

function safeEventSlug(value) {
  return String(value ?? "unknown")
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 120) || "unknown";
}

async function writeGlassboxEvent(eventsDir, sequence, canonicalKey, payload) {
  const file = path.join(
    eventsDir,
    `${String(sequence).padStart(8, "0")}-${safeEventSlug(canonicalKey)}-${crypto.randomUUID()}.json`,
  );
  await writeJson(file, {
    schema_version: 1,
    timestamp_utc: isoNow(),
    lane: "codex",
    source: "congress-full-text",
    run_id: "legislation-full-text-20260726",
    ...payload,
  });
  return file;
}

async function removeWithRetry(file, attempts = 30) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      await fs.rm(file, { force: true });
      return;
    } catch (error) {
      lastError = error;
      if (!["EBUSY", "EPERM", "EACCES"].includes(error.code)) throw error;
      if (attempt < attempts) await sleep(250 * attempt);
    }
  }
  throw new Error(`Could not remove checkpoint: ${file}`, {
    cause: lastError,
  });
}

function processIsAlive(processId) {
  if (!Number.isInteger(processId) || processId < 1) return false;
  try {
    process.kill(processId, 0);
    return true;
  } catch (error) {
    return error.code === "EPERM";
  }
}

async function acquireRunLock(outDir) {
  const lockFile = path.join(outDir, "text-relevance.lock.json");
  await fs.mkdir(outDir, { recursive: true });
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      const handle = await fs.open(lockFile, "wx");
      await handle.writeFile(
        `${JSON.stringify(
          {
            schema: "congress-text-relevance-lock-v1",
            pid: process.pid,
            started_at: isoNow(),
          },
          null,
          2,
        )}\n`,
        "utf8",
      );
      return { handle, lockFile };
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
      let prior = null;
      try {
        prior = await readJson(lockFile);
      } catch {
        // An unreadable lock is not safe to overwrite.
      }
      if (!prior || processIsAlive(Number(prior.pid))) {
        throw new Error(
          `Text relevance output is locked by a live or unreadable run: ` +
            `${lockFile} (pid=${prior?.pid ?? "unknown"})`,
        );
      }
      await removeWithRetry(lockFile);
      if (attempt < 3) await sleep(500 * attempt);
    }
  }
  throw new Error(`Could not acquire text relevance lock: ${lockFile}`);
}

async function releaseRunLock(lock) {
  if (!lock) return;
  await lock.handle.close().catch(() => {});
  await removeWithRetry(lock.lockFile);
}

async function waitForCongressPage(
  page,
  selector,
  requireOfficialTextSurface = false,
  timeoutMs = 30_000,
) {
  const deadline = Date.now() + timeoutMs;
  let previousSignature = null;
  let stableObservations = 0;
  while (Date.now() < deadline) {
    const title = await page.title().catch(() => "");
    const body = await page
      .locator("body")
      .innerText()
      .then((value) => value.slice(0, 1200))
      .catch(() => "");
    const challengeText = `${title} ${body}`;
    if (
      /captcha|verify you are human|human verification/i.test(challengeText) &&
      !/verifying you are human|verification successful/i.test(challengeText)
    ) {
      throw new Error(
        "Congress.gov presented an interactive human-verification challenge; " +
          "progress is checkpointed and no challenge was bypassed.",
      );
    }
    if (/just a moment|verifying you are human|cloudflare/i.test(challengeText)) {
      previousSignature = null;
      stableObservations = 0;
      await sleep(400);
      continue;
    }
    if (!requireOfficialTextSurface) {
      if (await page.locator(selector).count()) return;
      await sleep(400);
      continue;
    }
    const state = await page
      .evaluate((surfaceSelector) => {
        const marker =
          /(?:^|\n)\s*(Shown Here:|Amendment Text:)(?=\s|$)/i;
        for (const surface of document.querySelectorAll(surfaceSelector)) {
          const rawText = surface.innerText ?? "";
          const match = marker.exec(rawText);
          if (!match) continue;
          const content = rawText.slice(match.index ?? 0);
          const remainder = content.replace(marker, "").trim();
          if (!remainder) continue;
          let hash = 2166136261;
          for (let index = 0; index < content.length; index += 1) {
            hash ^= content.charCodeAt(index);
            hash = Math.imul(hash, 16777619);
          }
          return {
            ready: true,
            signature: `${location.href}|${match[1]}|${content.length}|${(
              hash >>> 0
            ).toString(16)}`,
          };
        }
        return { ready: false, signature: null };
      }, selector)
      .catch(() => ({ ready: false, signature: null }));
    if (state.ready) {
      if (state.signature === previousSignature) {
        stableObservations += 1;
      } else {
        previousSignature = state.signature;
        stableObservations = 1;
      }
      if (stableObservations >= 2) return;
    } else {
      previousSignature = null;
      stableObservations = 0;
    }
    await sleep(400);
  }
  throw new Error(
    `Congress.gov did not expose ${selector}; title=${JSON.stringify(
      await page.title().catch(() => "<unavailable>"),
    )} url=${page.url()}`,
  );
}

async function gotoWithRetry(
  page,
  url,
  selector = "#main",
  attempts = 3,
  scheduleRequest = async () => {},
  requireOfficialTextSurface = false,
  pageTimeoutMs = 30_000,
) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      await scheduleRequest();
      const response = await page.goto(url, {
        waitUntil: "domcontentloaded",
        timeout: pageTimeoutMs,
      });
      await waitForCongressPage(
        page,
        selector,
        requireOfficialTextSurface,
        pageTimeoutMs,
      );
      return response?.status() ?? null;
    } catch (error) {
      lastError = error;
      if (/interactive human-verification/i.test(error.message)) throw error;
      if (attempt < attempts) await sleep(4000 * attempt);
    }
  }
  throw new Error(`Congress.gov navigation failed after ${attempts} attempts`, {
    cause: lastError,
  });
}

function canonicalKeySet(value) {
  return [...new Set(value)].sort();
}

function buildRecordCategorySpecs({
  registryCategories,
  directMembershipIds = [],
  coverageProbeIds = [],
}) {
  const directIds = canonicalKeySet(directMembershipIds);
  const directIdSet = new Set(directIds);
  const probeIds = canonicalKeySet(coverageProbeIds);
  return registryCategories.map((category) => {
    const direct = directIdSet.has(category.id);
    return {
      query_id: category.id,
      label: category.label,
      evidence_role: direct
        ? "category_membership"
        : "cross_category_full_text_audit",
      direct_category_membership: direct,
      cross_category_full_text_audit: !direct,
      record_direct_membership_query_ids: directIds,
      source_coverage_probe_ids: probeIds,
      coverage_probe_candidate: probeIds.length > 0,
      identifiers: category.identifiers.map((item) => item.text),
    };
  });
}

const TEXT_MATCH_POLICY_VERSION =
  "ordered-token-bounded-act-action-required-complete-surface-v3";

function textEvidenceEvaluator({ categories, html = null, loadedUrl = null }) {
    const compactBrowser = (value) =>
      String(value ?? "").replace(/\s+/g, " ").trim();
    const normalize = (value) =>
      String(value ?? "")
        .normalize("NFKD")
        .replace(/[\u0300-\u036f]/g, "")
        .toLowerCase()
        .replace(/[’']/g, "")
        .replace(/[^\p{L}\p{N}]+/gu, " ")
        .replace(/\s+/g, " ")
        .trim();
    const stem = (value) => {
      let token = normalize(value);
      if (token.length > 5 && token.endsWith("ies")) {
        token = `${token.slice(0, -3)}y`;
      } else if (
        token.length > 5 &&
        token.endsWith("es") &&
        !token.endsWith("ses")
      ) {
        token = token.slice(0, -2);
      } else if (token.length > 4 && token.endsWith("s")) {
        token = token.slice(0, -1);
      }
      return token;
    };
    const sourceDocument =
      html === null
        ? document
        : new DOMParser().parseFromString(html, "text/html");
    const surfaces = [...sourceDocument.querySelectorAll("#main, main")];
    const verifiedSurfaces = surfaces
      .map((surface) => {
        const rawText = surface.innerText ?? "";
        const markers = [
          ...rawText.matchAll(
            /(?:^|\n)\s*(Shown Here:|Amendment Text:)(?=\s|$)/gi,
          ),
        ].map((match) => ({
          index: match.index ?? -1,
          marker: /^Shown Here:$/i.test(match[1])
            ? "Shown Here:"
            : "Amendment Text:",
        }));
        return { surface, rawText, markers };
      })
      .filter((candidate) => candidate.markers.length > 0)
      .sort(
        (left, right) =>
          left.markers[0].index - right.markers[0].index,
      );
    const verifiedSurface = verifiedSurfaces[0] ?? null;
    const main = verifiedSurface?.surface ?? null;
    const rawMain = verifiedSurface?.rawText ?? "";
    const selectedMarker = verifiedSurface?.markers[0] ?? null;
    const officialTextSurfaceMarker = selectedMarker?.marker ?? null;
    const shownHereMarkerFound =
      verifiedSurface?.markers.some(
        (marker) => marker.marker === "Shown Here:",
      ) ?? false;
    const amendmentTextMarkerFound =
      verifiedSurface?.markers.some(
        (marker) => marker.marker === "Amendment Text:",
      ) ?? false;
    // Fail closed when Congress.gov does not expose the verified bill-text
    // or amendment-text container. Page chrome, Cloudflare challenge text,
    // related-measure titles, and navigation are not official-text evidence.
    const content =
      selectedMarker?.index >= 0 ? rawMain.slice(selectedMarker.index) : "";
    const normalizedContent = normalize(content);
    const tokens = [];
    const tokenPattern = /[\p{L}\p{N}]+/gu;
    let tokenMatch;
    let segmentId = 0;
    let priorTokenEnd = 0;
    while ((tokenMatch = tokenPattern.exec(content)) !== null) {
      if (/[.!?;\n]/.test(content.slice(priorTokenEnd, tokenMatch.index))) {
        segmentId += 1;
      }
      tokens.push({
        stem: stem(tokenMatch[0]),
        start: tokenMatch.index,
        end: tokenPattern.lastIndex,
        segment_id: segmentId,
      });
      priorTokenEnd = tokenPattern.lastIndex;
    }
    const contextForRange = (start, end) =>
      compactBrowser(
        content.slice(
          Math.max(0, start - 320),
          Math.min(content.length, end + 320),
        ),
      );
    const orderedTokenSpans = (required, maximumGap = 0) => {
      if (!required.length) return [];
      const spans = [];
      for (let startIndex = 0; startIndex < tokens.length; startIndex += 1) {
        if (tokens[startIndex].stem !== required[0]) continue;
        let priorIndex = startIndex;
        let matched = true;
        for (let requiredIndex = 1; requiredIndex < required.length; requiredIndex += 1) {
          let foundIndex = -1;
          const lastIndex = Math.min(
            tokens.length - 1,
            priorIndex + maximumGap + 1,
          );
          for (let index = priorIndex + 1; index <= lastIndex; index += 1) {
            if (tokens[index].segment_id !== tokens[startIndex].segment_id) break;
            if (tokens[index].stem === required[requiredIndex]) {
              foundIndex = index;
              break;
            }
          }
          if (foundIndex < 0) {
            matched = false;
            break;
          }
          priorIndex = foundIndex;
        }
        if (matched) {
          spans.push({
            startToken: startIndex,
            endToken: priorIndex,
            spanTokens: priorIndex - startIndex + 1,
            segmentId: tokens[startIndex].segment_id,
          });
        }
      }
      return spans;
    };
    const actionPattern =
      /\b(?:amend(?:s|ed|ing|ment)?|repeal(?:s|ed|ing)?|exempt(?:s|ed|ing|ion)?|prohibit(?:s|ed|ing|ion)?|require(?:s|d|ing|ment)?|authoriz(?:e|es|ed|ing|ation)|reauthoriz(?:e|es|ed|ing|ation)|direct(?:s|ed|ing|ion)?|modif(?:y|ies|ied|ication)|revis(?:e|es|ed|ing|ion)|strike|strikes|struck|insert(?:s|ed|ing)?|extend(?:s|ed|ing)?|apply|applies|applied)\b/i;
    const segmentContext = (span) => {
      let start = tokens[span.startToken].start;
      let end = tokens[span.endToken].end;
      while (start > 0 && !/[.!?;\n]/.test(content[start - 1])) start -= 1;
      while (end < content.length && !/[.!?;\n]/.test(content[end])) end += 1;
      return compactBrowser(content.slice(start, end));
    };
    const evidenceForIdentifier = (identifier) => {
      const rawIdentifier = String(identifier ?? "").trim();
      const proximityExpression = rawIdentifier.match(
        /^\s*"([^"]+)"\s*~\s*(\d+)\s*$/,
      );
      const evidenceIdentifier = (
        proximityExpression ? proximityExpression[1] : rawIdentifier
      )
        .replace(/"([^"]+)"\s*~\s*\d+/g, "$1")
        .replace(/[()"]/g, " ")
        .replace(/\b(?:AND|OR|NOT)\b/gi, " ");
      const normalizedIdentifier = normalize(evidenceIdentifier);
      const identifierTokens = normalizedIdentifier
        .split(" ")
        .map(stem)
        .filter(Boolean);
      const genericEvidenceTokens = new Set([
        "a",
        "an",
        "the",
        "this",
        "that",
        "of",
        "and",
        "or",
        "for",
        "to",
        "in",
        "on",
        "act",
        "bill",
        "law",
        "resolution",
        "legislation",
      ]);
      const containsAct = identifierTokens.includes("act");
      const actIndex = identifierTokens.indexOf("act");
      const distinctiveTokens = [
        ...new Set(
          identifierTokens.filter(
            (token) => !genericEvidenceTokens.has(token),
          ),
        ),
      ];
      const specificityValid =
        identifierTokens.length > 0 &&
        !(containsAct && distinctiveTokens.length < 2) &&
        !identifierTokens.every((token) =>
          genericEvidenceTokens.has(token),
        );
      const specificityReason = specificityValid
        ? containsAct
          ? "complete_ordered_act_name_and_local_legislative_action_required"
          : "non_generic_identifier"
        : containsAct
          ? "rejected_non_specific_act_identifier"
          : "rejected_all_generic_identifier";
      const actTitleTokens = containsAct
        ? identifierTokens.slice(0, actIndex + 1)
        : [];
      const trailingContextTokens = containsAct
        ? identifierTokens.slice(actIndex + 1)
        : [];
      const maximumGap = proximityExpression
        ? Math.max(0, Number.parseInt(proximityExpression[2], 10))
        : 0;
      const fullOrderedSpans = specificityValid
        ? orderedTokenSpans(identifierTokens, maximumGap)
        : [];
      const exactPhraseSpans = specificityValid
        ? orderedTokenSpans(identifierTokens, 0)
        : [];
      const actTitleSpans =
        specificityValid && containsAct
          ? orderedTokenSpans(actTitleTokens, 0)
          : [];
      const actSpansWithContext = actTitleSpans.filter((titleSpan) => {
        if (!trailingContextTokens.length) return true;
        const tailTokens = tokens.slice(
          titleSpan.endToken + 1,
          Math.min(
            tokens.length,
            titleSpan.endToken + trailingContextTokens.length + 9,
          ),
        );
        if (
          tailTokens.some(
            (token) => token.segment_id !== titleSpan.segmentId,
          )
        ) {
          const firstDifferent = tailTokens.findIndex(
            (token) => token.segment_id !== titleSpan.segmentId,
          );
          tailTokens.splice(Math.max(0, firstDifferent));
        }
        let prior = -1;
        return trailingContextTokens.every((requiredToken) => {
          const found = tailTokens.findIndex(
            (token, index) =>
              index > prior && token.stem === requiredToken,
          );
          if (found < 0) return false;
          prior = found;
          return true;
        });
      });
      const actActionSpans = actSpansWithContext.filter((span) =>
        actionPattern.test(segmentContext(span)),
      );
      const exactPhrase = exactPhraseSpans.length > 0;
      const orderedProximityMatch = Boolean(
        proximityExpression && fullOrderedSpans.length,
      );
      const best = containsAct
        ? actActionSpans[0] ?? actSpansWithContext[0] ?? actTitleSpans[0] ?? null
        : exactPhraseSpans[0] ?? fullOrderedSpans[0] ?? null;
      const proximityMatch = containsAct
        ? actActionSpans.length > 0
        : orderedProximityMatch;
      const localizedMatch = containsAct
        ? specificityValid && actActionSpans.length > 0
        : specificityValid && (exactPhrase || orderedProximityMatch);
      const context = best
        ? contextForRange(
            tokens[best.startToken].start,
            tokens[best.endToken].end,
          )
        : null;
      return {
        identifier,
        evidence_identifier: normalizedIdentifier,
        match_mode: containsAct
          ? "ordered_complete_act_name_plus_same_segment_legislative_action"
          : proximityExpression
            ? "ordered_same_segment_proximity"
            : "ordered_exact_token_phrase",
        maximum_adjacent_gap: maximumGap,
        normalized_identifier: normalizedIdentifier,
        identifier_tokens: identifierTokens,
        distinctive_identifier_tokens: distinctiveTokens,
        act_title_tokens: actTitleTokens,
        trailing_context_tokens: trailingContextTokens,
        specificity_valid: specificityValid,
        specificity_reason: specificityReason,
        exact_phrase: exactPhrase,
        minimum_token_span: best?.spanTokens ?? null,
        proximity_threshold: proximityExpression ? maximumGap : 0,
        proximity_match: proximityMatch,
        complete_act_name_found: actTitleSpans.length > 0,
        act_context_found: actSpansWithContext.length > 0,
        localized_legislative_action_found:
          containsAct && actActionSpans.length > 0,
        act_reference_only:
          containsAct &&
          actSpansWithContext.length > 0 &&
          actActionSpans.length === 0,
        localized_match: localizedMatch,
        context,
      };
    };
    const categoryEvidence = categories.map((category) => {
      const identifiers = category.identifiers.map(evidenceForIdentifier);
      const localized = identifiers.filter((item) => item.localized_match);
      const separated = identifiers.filter(
        (item) =>
          !item.localized_match && item.minimum_token_span !== null,
      );
      return {
        query_id: category.query_id,
        label: category.label,
        evidence_role: category.evidence_role,
        direct_category_membership:
          category.direct_category_membership === true,
        cross_category_full_text_audit:
          category.cross_category_full_text_audit === true,
        record_direct_membership_query_ids:
          category.record_direct_membership_query_ids ?? [],
        source_coverage_probe_ids:
          category.source_coverage_probe_ids ?? [],
        coverage_probe_candidate:
          category.coverage_probe_candidate === true,
        identifiers,
        localized_identifier_count: localized.length,
        localized_identifiers: localized.map((item) => item.identifier),
        separated_identifier_count: separated.length,
        separated_identifiers: separated.map((item) => ({
          identifier: item.identifier,
          minimum_token_span: item.minimum_token_span,
        })),
        evidence_state: localized.length
          ? "localized_official_text_evidence"
          : "no_localized_identifier_evidence",
      };
    });
    const title = sourceDocument.title;
    const notFound =
      /page not found|requested page could not be found|error 404/i.test(
        `${title} ${compactBrowser(rawMain).slice(0, 1500)}`,
      );
    const textAvailable =
      !notFound &&
      selectedMarker?.index >= 0 &&
      content
        .replace(
          /^(?:\s*)(?:Shown Here:|Amendment Text:)(?=\s|$)/i,
          "",
        )
        .trim().length > 0;
    const markerLine = compactBrowser(content.split(/\r?\n/, 1)[0]);
    const shownHereVersion =
      officialTextSurfaceMarker === "Shown Here:"
        ? markerLine.replace(/^Shown Here:\s*/i, "").trim() || null
        : null;
    const amendmentTextLabel =
      officialTextSurfaceMarker === "Amendment Text:"
        ? markerLine.replace(/^Amendment Text:\s*/i, "").trim() || null
        : null;
    const amendmentHeading =
      officialTextSurfaceMarker === "Amendment Text:"
        ? compactBrowser(
            [...(main?.querySelectorAll("h1, h2, h3") ?? [])].find((node) =>
              /(?:H|S)\.\s*Amdt\.?\s*\d+/i.test(
                compactBrowser(node.textContent),
              ),
            )?.textContent,
          ) || null
        : null;
    const versionLabel = shownHereVersion || null;
    let hash = 2166136261;
    for (let index = 0; index < content.length; index += 1) {
      hash ^= content.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return {
      loaded_url: loadedUrl ?? location.href,
      page_title: title,
      not_found: notFound,
      text_available: textAvailable,
      official_text_surface_marker: officialTextSurfaceMarker,
      shown_here_marker_found: shownHereMarkerFound,
      amendment_text_marker_found: amendmentTextMarkerFound,
      version_label: versionLabel,
      amendment_text_label:
        amendmentTextLabel || amendmentHeading || null,
      official_text_character_count: content.length,
      official_text_token_count: tokens.length,
      official_text_fnv1a32: (hash >>> 0).toString(16).padStart(8, "0"),
      _official_text_for_archive: content,
      categories: categoryEvidence,
    };
}

function finalizeExtractedTextEvidence(value) {
  const officialText = String(value?._official_text_for_archive ?? "");
  const result = {
    ...value,
    official_text_sha256: stableHash(officialText),
    _official_text_for_archive: officialText,
  };
  return result;
}

async function extractTextEvidence(page, categorySpecs) {
  return finalizeExtractedTextEvidence(
    await page.evaluate(textEvidenceEvaluator, { categories: categorySpecs }),
  );
}

async function extractStableTextEvidence(
  page,
  categorySpecs,
  { observations = 2, intervalMs = 250, timeoutMs = 5_000 } = {},
) {
  const deadline = Date.now() + timeoutMs;
  let previousSignature = null;
  let stableObservations = 0;
  let latest = null;
  while (Date.now() < deadline) {
    latest = await extractTextEvidence(page, categorySpecs);
    const signature = stableHash({
      content: officialTextContentProjection(latest),
      categories: latest.categories,
    });
    if (signature === previousSignature) {
      stableObservations += 1;
    } else {
      previousSignature = signature;
      stableObservations = 1;
    }
    if (stableObservations >= observations) return latest;
    await sleep(intervalMs);
  }
  throw new Error(
    "Congress.gov official-text surface did not stabilize before parity audit",
  );
}

async function extractTextEvidenceFromHtml(
  page,
  categorySpecs,
  html,
  loadedUrl,
) {
  // Render the already-fetched body in the worker tab so `innerText` has the
  // same layout/whitespace semantics as the frozen browser-navigation
  // baseline. Strip scripts first: the HTTP body is evidence to parse, not
  // authority to execute a second navigation or analytics workload.
  const inertHtml = String(html ?? "").replace(
    /<script\b[^>]*>[\s\S]*?<\/script\s*>/gi,
    "",
  );
  await page.setContent(inertHtml, {
    waitUntil: "domcontentloaded",
    timeout: 30_000,
  });
  return finalizeExtractedTextEvidence(
    await page.evaluate(textEvidenceEvaluator, {
      categories: categorySpecs,
      loadedUrl,
    }),
  );
}

function officialTextContentProjection(value) {
  return {
    text_available: value?.text_available === true,
    official_text_surface_marker:
      value?.official_text_surface_marker ?? null,
    shown_here_marker_found: value?.shown_here_marker_found === true,
    amendment_text_marker_found:
      value?.amendment_text_marker_found === true,
    official_text_character_count:
      value?.official_text_character_count ?? null,
    official_text_token_count: value?.official_text_token_count ?? null,
    official_text_fnv1a32: value?.official_text_fnv1a32 ?? null,
    official_text_sha256: value?.official_text_sha256 ?? null,
  };
}

function officialTextContentFingerprint(value) {
  return stableHash(officialTextContentProjection(value));
}

function artifactIntegrityProjection(artifact) {
  const copy = { ...artifact };
  delete copy.artifact_integrity_sha256;
  return copy;
}

function artifactIntegritySha256(artifact) {
  return stableHash(artifactIntegrityProjection(artifact));
}

function frozenBrowserBaseline(prior, sourceFileSha256) {
  if (!prior || prior.text_available !== true) return null;
  const selected =
    prior.transport_provenance?.selected_transport ?? "browser_navigation";
  if (selected !== "browser_navigation") return null;
  const projection = officialTextContentProjection(prior);
  const verifiedSurface =
    (projection.official_text_surface_marker === "Shown Here:" &&
      projection.shown_here_marker_found === true) ||
    (projection.official_text_surface_marker === "Amendment Text:" &&
      projection.amendment_text_marker_found === true);
  if (
    !verifiedSurface ||
    !Number.isInteger(projection.official_text_character_count) ||
    projection.official_text_character_count < 1 ||
    !Number.isInteger(projection.official_text_token_count) ||
    projection.official_text_token_count < 1 ||
    !/^[0-9a-f]{8}$/i.test(projection.official_text_fnv1a32 ?? "") ||
    !/^[0-9a-f]{64}$/i.test(projection.official_text_sha256 ?? "")
  ) {
    return null;
  }
  return {
    schema: "congress-text-browser-equivalence-baseline-v1",
    source_artifact_file_sha256: sourceFileSha256,
    source_artifact_integrity_sha256:
      prior.artifact_integrity_sha256 ?? null,
    source_transport: selected,
    content_projection: projection,
    content_fingerprint_sha256: stableHash(projection),
  };
}

async function readJsonWithFileHash(file) {
  const text = await fs.readFile(file, "utf8");
  return {
    artifact: JSON.parse(text),
    file_sha256: stableHash(text),
  };
}

async function loadBillstatusSummaryIndex(manifestFile) {
  if (!manifestFile) {
    throw new Error(
      "--billstatus-manifest is required so official summaries and their absences remain explicit",
    );
  }
  const resolved = assertWithinRoot(manifestFile);
  const loaded = await readJsonWithFileHash(resolved);
  const manifest = loaded.artifact;
  const partition = manifest.partition ?? {};
  if (
    String(manifest.status ?? "").toUpperCase() !== "COMPLETE" ||
    manifest.official_source_only !== true ||
    !Array.isArray(manifest.record_receipts) ||
    !Array.isArray(manifest.failures) ||
    manifest.failures.length !== 0 ||
    partition.top_level_completed !== partition.top_level_planned ||
    partition.top_level_failed !== 0 ||
    partition.remaining_top_level !== 0 ||
    manifest.record_receipts.length !== partition.top_level_planned
  ) {
    throw new Error("BillStatus summary manifest is not complete and official-only");
  }
  const byCanonicalKey = new Map();
  for (const receipt of manifest.record_receipts) {
    if (!receipt?.canonical_key || byCanonicalKey.has(receipt.canonical_key)) {
      throw new Error(
        `Invalid or duplicate BillStatus receipt: ${receipt?.canonical_key}`,
      );
    }
    byCanonicalKey.set(receipt.canonical_key, receipt);
  }
  return {
    file: resolved,
    file_sha256: loaded.file_sha256,
    manifest,
    by_canonical_key: byCanonicalKey,
  };
}

async function officialSummaryEvidence(record, summaryIndex) {
  const receipt = summaryIndex.by_canonical_key.get(record.canonical_key);
  if (!receipt) {
    return {
      schema: "official-billstatus-summary-evidence-v1",
      available: false,
      absence_reason:
        record.record_kind === "amendment"
          ? "nested_amendment_no_standalone_billstatus_package"
          : "official_billstatus_receipt_missing",
      items: [],
      titles: [],
      text_versions: [],
      identity: null,
      sponsors: [],
      cosponsors: [],
      actions: [],
      recorded_votes: [],
      provenance: {
        manifest_file: path
          .relative(ROOT, summaryIndex.file)
          .split(path.sep)
          .join("/"),
        manifest_file_sha256: summaryIndex.file_sha256,
        parsed_file: null,
        parsed_file_sha256: null,
        raw_file: null,
        source_url: null,
        source_file_sha256: null,
      },
    };
  }
  const parsedFile = assertWithinRoot(receipt.parsed_path);
  const parsedText = await fs.readFile(parsedFile, "utf8");
  if (stableHash(parsedText) !== receipt.parsed_sha256) {
    throw new Error(
      `BillStatus parsed receipt hash mismatch: ${record.canonical_key}`,
    );
  }
  const rawFile = assertWithinRoot(receipt.raw_path);
  const rawBytes = await fs.readFile(rawFile);
  if (
    sha256Bytes(rawBytes) !== receipt.raw_sha256 ||
    rawBytes.byteLength !== receipt.raw_bytes
  ) {
    throw new Error(
      `BillStatus raw official-source hash mismatch: ${record.canonical_key}`,
    );
  }
  const parsed = JSON.parse(parsedText);
  if (parsed.canonical_key !== record.canonical_key) {
    throw new Error(
      `BillStatus parsed identity mismatch: ${record.canonical_key}`,
    );
  }
  const items = Array.isArray(parsed.summaries) ? parsed.summaries : [];
  const summaryAvailable = items.some((item) =>
    String(item?.text ?? "").trim(),
  );
  return {
    schema: "official-billstatus-summary-evidence-v1",
    available: summaryAvailable,
    absence_reason: summaryAvailable
      ? null
      : items.length
        ? "official_crs_summary_text_missing"
        : "official_crs_summary_absent",
    items,
    titles: Array.isArray(parsed.titles) ? parsed.titles : [],
    text_versions: Array.isArray(parsed.text_versions)
      ? parsed.text_versions
      : [],
    identity: parsed.identity ?? null,
    sponsors: Array.isArray(parsed.sponsors) ? parsed.sponsors : [],
    cosponsors: Array.isArray(parsed.cosponsors) ? parsed.cosponsors : [],
    actions: Array.isArray(parsed.actions) ? parsed.actions : [],
    recorded_votes: Array.isArray(parsed.recorded_votes)
      ? parsed.recorded_votes
      : [],
    provenance: {
      manifest_file: path
        .relative(ROOT, summaryIndex.file)
        .split(path.sep)
        .join("/"),
      manifest_file_sha256: summaryIndex.file_sha256,
      parsed_file: path.relative(ROOT, parsedFile).split(path.sep).join("/"),
      parsed_file_sha256: receipt.parsed_sha256,
      raw_file: path.relative(ROOT, rawFile).split(path.sep).join("/"),
      source_url: receipt.source_url,
      source_file_sha256: receipt.raw_sha256,
      source_file_bytes: receipt.raw_bytes,
    },
  };
}

function buildOfficialTextArchive(content) {
  const text = String(content ?? "");
  const uncompressed = Buffer.from(text, "utf8");
  const compressed = gzipSync(uncompressed);
  return {
    compressed,
    content_sha256: stableHash(text),
    gzip_sha256: sha256Bytes(compressed),
    uncompressed_bytes: uncompressed.byteLength,
    compressed_bytes: compressed.byteLength,
  };
}

async function writeImmutableBuffer(file, expected) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  try {
    const existing = await fs.readFile(file);
    if (!existing.equals(expected)) {
      throw new Error(`Immutable archive collision: ${file}`);
    }
    return;
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  const temporary = path.join(
    path.dirname(file),
    `.${path.basename(file)}.${process.pid}.${crypto.randomUUID()}.tmp`,
  );
  let handle;
  try {
    handle = await fs.open(temporary, "wx");
    await handle.writeFile(expected);
    await handle.sync();
    await handle.close();
    handle = null;
    try {
      await fs.link(temporary, file);
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
    }
    const observed = await fs.readFile(file);
    if (!observed.equals(expected)) {
      throw new Error(`Immutable archive readback mismatch: ${file}`);
    }
  } finally {
    await handle?.close().catch(() => {});
    await fs.rm(temporary, { force: true }).catch(() => {});
  }
}

async function validateOfficialTextArchive(outDir, artifact) {
  if (artifact.text_available !== true) {
    return artifact.official_text_archive == null;
  }
  const archive = artifact.official_text_archive;
  if (!archive?.file || archive.compression !== "gzip") return false;
  const file = path.resolve(outDir, archive.file);
  const relative = path.relative(outDir, file);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`Official-text archive escaped output directory: ${file}`);
  }
  let compressed;
  try {
    compressed = await fs.readFile(file);
  } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
  if (
    compressed.byteLength !== archive.compressed_bytes ||
    sha256Bytes(compressed) !== archive.gzip_sha256
  ) {
    throw new Error(`Official-text gzip integrity mismatch: ${file}`);
  }
  const uncompressed = gunzipSync(compressed);
  const text = uncompressed.toString("utf8");
  if (
    uncompressed.byteLength !== archive.uncompressed_bytes ||
    stableHash(text) !== archive.content_sha256 ||
    archive.content_sha256 !== artifact.official_text_sha256
  ) {
    throw new Error(`Official-text content integrity mismatch: ${file}`);
  }
  return true;
}

async function persistOfficialTextArchive(outDir, record, artifact) {
  const content = String(artifact._official_text_for_archive ?? "");
  delete artifact._official_text_for_archive;
  if (artifact.text_available !== true) {
    artifact.official_text_archive = null;
    return;
  }
  if (!content || stableHash(content) !== artifact.official_text_sha256) {
    throw new Error(
      `Complete official text was not retained for ${record.canonical_key}`,
    );
  }
  const archive = buildOfficialTextArchive(content);
  const file = path.join(
    outDir,
    "official_text",
    "sha256",
    `${archive.content_sha256}.txt.gz`,
  );
  await writeImmutableBuffer(file, archive.compressed);
  artifact.official_text_archive = {
    schema: "congress-official-text-archive-v1",
    file: path.relative(outDir, file).split(path.sep).join("/"),
    encoding: "utf-8",
    compression: "gzip",
    content_sha256: archive.content_sha256,
    gzip_sha256: archive.gzip_sha256,
    uncompressed_bytes: archive.uncompressed_bytes,
    compressed_bytes: archive.compressed_bytes,
  };
  if (!(await validateOfficialTextArchive(outDir, artifact))) {
    throw new Error(`Official-text archive validation failed: ${file}`);
  }
}

async function writeImmutableTransportReceipt(outDir, record, payload) {
  const receiptBase = {
    schema: "congress-text-transport-receipt-v1",
    created_at: isoNow(),
    discovery_canonical_key: record.canonical_key,
    ...payload,
  };
  const receipt = {
    ...receiptBase,
    receipt_payload_sha256: stableHash(receiptBase),
  };
  const text = `${JSON.stringify(receipt, null, 2)}\n`;
  const fileSha256 = stableHash(text);
  const directory = path.join(
    outDir,
    "transport_receipts",
    String(record.congress),
    record.type,
    String(record.number),
  );
  const file = path.join(directory, `${receipt.receipt_payload_sha256}.json`);
  await fs.mkdir(directory, { recursive: true });
  try {
    const existing = await fs.readFile(file, "utf8");
    if (existing !== text) {
      throw new Error(`Immutable transport receipt collision: ${file}`);
    }
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
    const temporary = path.join(
      directory,
      `.${receipt.receipt_payload_sha256}.${process.pid}.${crypto.randomUUID()}.tmp`,
    );
    let handle;
    try {
      handle = await fs.open(temporary, "wx");
      await handle.writeFile(text, "utf8");
      await handle.sync();
      await handle.close();
      handle = null;
      try {
        await fs.link(temporary, file);
      } catch (linkError) {
        if (linkError.code !== "EEXIST") throw linkError;
        const existing = await fs.readFile(file, "utf8");
        if (existing !== text) {
          throw new Error(`Immutable transport receipt collision: ${file}`);
        }
      }
    } finally {
      await handle?.close().catch(() => {});
      await fs.rm(temporary, { force: true }).catch(() => {});
    }
  }
  return {
    file: path.relative(outDir, file).split(path.sep).join("/"),
    receipt_payload_sha256: receipt.receipt_payload_sha256,
    file_sha256: fileSha256,
  };
}

async function writeImmutableHttpEpochReceipt(outDir, payload) {
  const base = {
    schema: "congress-http-acceptance-epoch-v1",
    created_at: isoNow(),
    ...payload,
  };
  const receipt = { ...base, receipt_payload_sha256: stableHash(base) };
  const text = `${JSON.stringify(receipt, null, 2)}\n`;
  const file = path.join(
    outDir,
    "http_epochs",
    `${receipt.receipt_payload_sha256}.json`,
  );
  await fs.mkdir(path.dirname(file), { recursive: true });
  try {
    const handle = await fs.open(file, "wx");
    try {
      await handle.writeFile(text, "utf8");
      await handle.sync();
    } finally {
      await handle.close();
    }
  } catch (error) {
    if (error.code !== "EEXIST") throw error;
    const existing = await fs.readFile(file, "utf8");
    if (existing !== text) {
      throw new Error(`Immutable HTTP epoch receipt collision: ${file}`);
    }
  }
  return {
    file: path.relative(outDir, file).split(path.sep).join("/"),
    receipt_payload_sha256: receipt.receipt_payload_sha256,
    file_sha256: stableHash(text),
  };
}

async function validateCommittedHttpEpoch(
  outDir,
  artifact,
  freshHttpAuthorization,
) {
  if (
    artifact.transport_provenance?.selected_transport !==
    "browser_context_http"
  ) {
    return true;
  }
  const binding = artifact.transport_provenance?.committed_http_epoch;
  if (
    !binding ||
    binding.passed !== true ||
    binding.authorization_sha256 !==
      freshHttpAuthorization?.authorization_sha256 ||
    !/^[0-9a-f]{64}$/i.test(binding.receipt_payload_sha256 ?? "") ||
    !/^[0-9a-f]{64}$/i.test(binding.receipt_file_sha256 ?? "")
  ) {
    return false;
  }
  const file = path.resolve(outDir, binding.receipt_file);
  const relative = path.relative(outDir, file);
  if (relative.startsWith("..") || path.isAbsolute(relative)) return false;
  let text;
  try {
    text = await fs.readFile(file, "utf8");
  } catch {
    return false;
  }
  if (stableHash(text) !== binding.receipt_file_sha256) return false;
  let receipt;
  try {
    receipt = JSON.parse(text);
  } catch {
    return false;
  }
  if (
    receipt.schema !== "congress-http-acceptance-epoch-v1" ||
    receipt.passed !== true ||
    receipt.authorization_sha256 !==
      freshHttpAuthorization.authorization_sha256 ||
    receipt.receipt_payload_sha256 !== binding.receipt_payload_sha256 ||
    stableHash(
      Object.fromEntries(
        Object.entries(receipt).filter(
          ([name]) => name !== "receipt_payload_sha256",
        ),
      ),
    ) !== receipt.receipt_payload_sha256 ||
    !Array.isArray(receipt.http_records)
  ) {
    return false;
  }
  const entry = receipt.http_records.find(
    (item) => item.canonical_key === artifact.discovery_canonical_key,
  );
  return Boolean(
    entry &&
      entry.content_fingerprint_sha256 ===
        officialTextContentFingerprint(artifact) &&
      entry.category_evidence_sha256 === stableHash(artifact.categories),
  );
}

function normalizedCongressPath(urlValue) {
  const parsed = new URL(urlValue);
  if (parsed.origin.toLowerCase() !== "https://www.congress.gov") {
    throw new Error(`Official-text URL is not Congress.gov: ${urlValue}`);
  }
  if (parsed.search || parsed.hash) {
    throw new Error(
      "Official-text URL contains unexpected query or fragment: " + urlValue,
    );
  }
  return decodeURIComponent(parsed.pathname)
    .replace(/^\/index\.php/i, "")
    .replace(/\/+$/, "")
    .toLowerCase();
}

function validateOfficialTextIdentity(record, extracted) {
  const expectedPath = `${normalizedCongressPath(record.canonical_url)}/text`;
  const loadedPath = normalizedCongressPath(extracted.loaded_url);
  if (loadedPath !== expectedPath) {
    throw new Error(
      `Official-text identity mismatch for ${record.canonical_key}: ${extracted.loaded_url}`,
    );
  }
  const normalizeDisplay = (value) =>
    String(value ?? "").toLowerCase().replace(/[^a-z0-9]+/g, "");
  const display = normalizeDisplay(record.display_number);
  const title = normalizeDisplay(extracted.page_title);
  const titleContainsDisplay = Boolean(display) && title.includes(display);
  if (display && !titleContainsDisplay) {
    throw new Error(
      `Official-text page-title identity mismatch for ${record.canonical_key}: ${extracted.page_title}`,
    );
  }
  return {
    schema: "congress-official-text-identity-v1",
    verified: true,
    canonical_key: record.canonical_key,
    expected_path: expectedPath,
    loaded_path: loadedPath,
    display_number: record.display_number ?? null,
    page_title_contains_display_number: titleContainsDisplay,
  };
}

function expectedTextSurfaceMarker(record) {
  return record.type === "hamdt" || record.type === "samdt"
    ? "Amendment Text:"
    : "Shown Here:";
}

function assertExpectedTextSurface(record, extracted) {
  const expected = expectedTextSurfaceMarker(record);
  const correctFlag =
    expected === "Amendment Text:"
      ? extracted.amendment_text_marker_found === true
      : extracted.shown_here_marker_found === true;
  if (
    extracted.official_text_surface_marker !== expected ||
    !correctFlag ||
    extracted.text_available !== true ||
    !Number.isInteger(extracted.official_text_character_count) ||
    extracted.official_text_character_count < 1
  ) {
    throw new Error(
      "Official-text surface type mismatch for " +
        record.canonical_key +
        ": expected " +
        expected,
    );
  }
  return expected;
}

async function loadFreshHttpCanaryBundle(
  manifestFile,
  {
    discoveryManifest,
    recordsInput,
    membershipsInput,
    registryHash,
    summaryIndex,
    recordsByKey,
    categorySpecsForRecord,
  },
) {
  if (!manifestFile) return null;
  const resolved = assertWithinRoot(manifestFile);
  const loaded = await readJsonWithFileHash(resolved);
  const manifest = loaded.artifact;
  const expected = {
    discovery_records_sha256: discoveryManifest.union.records_sha256,
    discovery_records_file_sha256: recordsInput.file_sha256,
    discovery_memberships_sha256: discoveryManifest.union.memberships_sha256,
    discovery_memberships_file_sha256: membershipsInput.file_sha256,
    registry_sha256: registryHash,
    billstatus_summary_manifest_sha256: summaryIndex.file_sha256,
    text_match_policy_version: TEXT_MATCH_POLICY_VERSION,
  };
  if (
    manifest.status !== "complete" ||
    manifest.run_scope !== "canary" ||
    manifest.corpus_acceptance_eligible !== false ||
    manifest.transport_policy?.default_transport !== "browser_navigation" ||
    manifest.transport_policy?.http_fast_path_opt_in !== false ||
    !Array.isArray(manifest.completed_records)
  ) {
    throw new Error("Fresh HTTP canary manifest is not an isolated browser canary");
  }
  for (const [name, value] of Object.entries(expected)) {
    if (manifest[name] !== value) {
      throw new Error(
        "Fresh HTTP canary input mismatch for " +
          name +
          ": " +
          manifest[name] +
          " !== " +
          value,
      );
    }
  }
  const directory = path.dirname(resolved);
  const entries = [];
  const kindCoverage = new Set();
  for (const completed of manifest.completed_records) {
    const record = recordsByKey.get(completed.canonical_key);
    if (!record) {
      throw new Error(
        "Fresh HTTP canary record is not in current discovery: " +
          completed.canonical_key,
      );
    }
    const artifactFile = path.resolve(directory, completed.file);
    const artifactRelative = path.relative(directory, artifactFile);
    if (artifactRelative.startsWith("..") || path.isAbsolute(artifactRelative)) {
      throw new Error(
        "Fresh HTTP canary artifact escaped its directory: " + artifactFile,
      );
    }
    const priorRead = await readJsonWithFileHash(artifactFile);
    const artifact = priorRead.artifact;
    if (
      !/^[0-9a-f]{64}$/i.test(artifact.artifact_integrity_sha256 ?? "") ||
      artifact.artifact_integrity_sha256 !== artifactIntegritySha256(artifact) ||
      artifact.discovery_canonical_key !== record.canonical_key ||
      artifact.discovery_record_sha256 !== stableHash(record) ||
      artifact.registry_sha256 !== registryHash ||
      artifact.text_match_policy_version !== TEXT_MATCH_POLICY_VERSION ||
      artifact.transport_provenance?.selected_transport !== "browser_navigation" ||
      artifact.text_available !== true ||
      artifact.official_text_identity?.verified !== true ||
      !(await validateOfficialTextArchive(directory, artifact))
    ) {
      throw new Error(
        "Fresh HTTP canary artifact is not current and browser-verified: " +
          artifactFile,
      );
    }
    const categorySpecs = categorySpecsForRecord(record);
    if (artifact.membership_sha256 !== stableHash(categorySpecs)) {
      throw new Error(
        "Fresh HTTP canary membership projection drifted: " +
          record.canonical_key,
      );
    }
    const baseline = frozenBrowserBaseline(artifact, priorRead.file_sha256);
    if (!baseline) {
      throw new Error(
        "Fresh HTTP canary lacks a valid frozen browser baseline: " +
          record.canonical_key,
      );
    }
    const kind = record.type === "hamdt" || record.type === "samdt"
      ? "amendment"
      : "bill";
    kindCoverage.add(kind);
    entries.push({
      record,
      categorySpecs,
      artifact,
      baseline,
      artifact_file: artifactRelative.split(path.sep).join("/"),
      artifact_file_sha256: priorRead.file_sha256,
      category_evidence_sha256: stableHash(artifact.categories),
      kind,
    });
  }
  if (
    entries.length !== 2 ||
    !kindCoverage.has("bill") ||
    !kindCoverage.has("amendment")
  ) {
    throw new Error("Fresh HTTP canary must contain exactly one bill and one amendment");
  }
  return {
    schema: "congress-fresh-http-canary-bundle-v1",
    manifest_file: path.relative(ROOT, resolved).split(path.sep).join("/"),
    manifest_file_sha256: loaded.file_sha256,
    entries,
  };
}

async function warmBrowserContextFromCanary(
  page,
  bundle,
  scheduleRequest,
  pageTimeoutMs,
  { nativeContext = null } = {},
) {
  const records = [];
  for (const entry of bundle.entries) {
    let extracted = null;
    if (nativeContext) {
      const candidates = nativeContext
        .pages()
        .filter((candidate) => candidate.url() === entry.artifact.requested_url);
      for (const candidate of candidates) {
        try {
          const observed = await extractStableTextEvidence(
            candidate,
            entry.categorySpecs,
          );
          validateOfficialTextIdentity(entry.record, observed);
          assertExpectedTextSurface(entry.record, observed);
          extracted = observed;
          break;
        } catch {
          // Keep looking: a native Chrome profile may also contain a stale
          // Cloudflare tab. Only a fully verified exact canary is accepted.
        }
      }
      if (!extracted) {
        throw new Error(
          "Visible native Chrome does not contain a verified exact " +
            entry.kind +
            " canary tab",
        );
      }
    } else {
      await gotoWithRetry(
        page,
        entry.artifact.requested_url,
        "#main, main",
        1,
        scheduleRequest,
        true,
        pageTimeoutMs,
      );
      extracted = await extractStableTextEvidence(page, entry.categorySpecs);
    }
    validateOfficialTextIdentity(entry.record, extracted);
    assertExpectedTextSurface(entry.record, extracted);
    const contentFingerprint = officialTextContentFingerprint(extracted);
    const categoryEvidenceSha256 = stableHash(extracted.categories);
    if (
      contentFingerprint !== entry.baseline.content_fingerprint_sha256 ||
      categoryEvidenceSha256 !== entry.category_evidence_sha256
    ) {
      throw new Error(
        "Headed clearance browser did not reproduce the frozen " +
          entry.kind +
          " canary",
      );
    }
    records.push({
      canonical_key: entry.record.canonical_key,
      kind: entry.kind,
      loaded_url: extracted.loaded_url,
      content_fingerprint_sha256: contentFingerprint,
      category_evidence_sha256: categoryEvidenceSha256,
    });
  }
  return { records };
}

async function runFreshHttpTransportCanary(page, httpFastPath, bundle, browserWarmup) {
  const records = [];
  for (const entry of bundle.entries) {
    const response = await httpFastPath.fetch(entry.artifact.requested_url);
    if (response.kind !== "accepted") {
      const failedGuards =
        response.attempts?.at(-1)?.validation?.failed ?? [];
      throw new Error(
        "Fresh HTTP " +
          entry.kind +
          " canary was not accepted: " +
          response.reason +
          (failedGuards.length
            ? " [" + failedGuards.join(",") + "]"
            : ""),
      );
    }
    const extracted = await extractTextEvidenceFromHtml(
      page,
      entry.categorySpecs,
      response.body,
      response.loaded_url,
    );
    validateOfficialTextIdentity(entry.record, extracted);
    assertExpectedTextSurface(entry.record, extracted);
    const contentFingerprint = officialTextContentFingerprint(extracted);
    const categoryEvidenceSha256 = stableHash(extracted.categories);
    if (
      extracted.text_available !== true ||
      contentFingerprint !== entry.baseline.content_fingerprint_sha256 ||
      categoryEvidenceSha256 !== entry.category_evidence_sha256
    ) {
      throw new Error(
        "Fresh HTTP " +
          entry.kind +
          " canary diverged from frozen browser evidence",
      );
    }
    records.push({
      canonical_key: entry.record.canonical_key,
      kind: entry.kind,
      requested_url: entry.artifact.requested_url,
      loaded_url: response.loaded_url,
      http_status: response.status,
      http_body_sha256: response.body_sha256,
      http_body_byte_length: response.body_byte_length,
      content_fingerprint_sha256: contentFingerprint,
      category_evidence_sha256: categoryEvidenceSha256,
      frozen_browser_artifact_file: entry.artifact_file,
      frozen_browser_artifact_file_sha256: entry.artifact_file_sha256,
      browser_equivalence_passed: true,
    });
  }
  const base = {
    schema: "congress-fresh-http-authorization-v1",
    passed: true,
    created_at: isoNow(),
    canary_manifest_file: bundle.manifest_file,
    canary_manifest_file_sha256: bundle.manifest_file_sha256,
    cookie_export_performed: false,
    cookie_logging_performed: false,
    headed_browser_warmup: browserWarmup,
    records,
  };
  return { ...base, authorization_sha256: stableHash(base) };
}

async function collectMeasure(
  page,
  record,
  categorySpecs,
  scheduleRequest,
  {
    httpFastPath = null,
    frozenBaseline = null,
    freshHttpAuthorization = null,
    browserParityAuditRequired = false,
    browserMaxAttempts = 3,
    pageTimeoutMs = 30_000,
  } = {},
) {
  const textUrl = `${record.canonical_url}/text`;
  let httpStatus = null;
  let extracted = null;
  let selectedTransport = "browser_navigation";
  let browserFallbackReason = null;
  let httpResult = null;
  let httpExtractedCandidate = null;
  let browserParityAudit = {
    required: Boolean(browserParityAuditRequired),
    passed: false,
    browser_http_status: null,
    browser_content_fingerprint_sha256: null,
    browser_category_evidence_sha256: null,
    http_content_fingerprint_sha256: null,
    http_category_evidence_sha256: null,
  };
  let equivalence = {
    required: Boolean(httpFastPath),
    baseline_available: Boolean(frozenBaseline),
    fresh_http_canary_authorized: freshHttpAuthorization?.passed === true,
    fresh_http_authorization_sha256:
      freshHttpAuthorization?.authorization_sha256 ?? null,
    authorization_mode: null,
    passed: false,
    baseline: frozenBaseline,
    observed_content_fingerprint_sha256: null,
  };

  if (httpFastPath) {
    httpResult = await httpFastPath.fetch(textUrl);
    if (httpResult.kind === "accepted") {
      const httpExtracted = await extractTextEvidenceFromHtml(
        page,
        categorySpecs,
        httpResult.body,
        httpResult.loaded_url,
      );
      validateOfficialTextIdentity(record, httpExtracted);
      assertExpectedTextSurface(record, httpExtracted);
      equivalence.observed_content_fingerprint_sha256 =
        officialTextContentFingerprint(httpExtracted);
      const frozenBaselinePassed = Boolean(
        frozenBaseline &&
          httpExtracted.text_available === true &&
          equivalence.observed_content_fingerprint_sha256 ===
            frozenBaseline.content_fingerprint_sha256,
      );
      const freshCanaryPassed = Boolean(
        !frozenBaseline &&
          freshHttpAuthorization?.passed === true &&
          httpExtracted.text_available === true,
      );
      equivalence.authorization_mode = frozenBaselinePassed
        ? "frozen_record_browser_hash"
        : freshCanaryPassed
          ? "fresh_bill_amendment_canary"
          : null;
      equivalence.passed = frozenBaselinePassed || freshCanaryPassed;
      if (frozenBaselinePassed || (freshCanaryPassed && !browserParityAuditRequired)) {
        extracted = httpExtracted;
        httpStatus = httpResult.status;
        selectedTransport = "browser_context_http";
      } else if (freshCanaryPassed && browserParityAuditRequired) {
        httpExtractedCandidate = httpExtracted;
        browserParityAudit.http_content_fingerprint_sha256 =
          officialTextContentFingerprint(httpExtracted);
        browserParityAudit.http_category_evidence_sha256 = stableHash(
          httpExtracted.categories,
        );
      } else {
        browserFallbackReason = frozenBaseline
          ? "http_content_missed_frozen_browser_hash"
          : "missing_fresh_http_canary_authorization";
      }
    } else {
      browserFallbackReason = httpResult.reason;
    }
  }

  if (!extracted) {
    const browserHttpStatus = await gotoWithRetry(
      page,
      textUrl,
      "#main, main",
      browserMaxAttempts,
      scheduleRequest,
      true,
      pageTimeoutMs,
    );
    const browserExtracted = httpExtractedCandidate
      ? await extractStableTextEvidence(page, categorySpecs)
      : await extractTextEvidence(page, categorySpecs);
    validateOfficialTextIdentity(record, browserExtracted);
    if (browserExtracted.text_available === true) {
      assertExpectedTextSurface(record, browserExtracted);
    }
    if (httpExtractedCandidate) {
      validateOfficialTextIdentity(record, browserExtracted);
      browserParityAudit.browser_http_status = browserHttpStatus;
      browserParityAudit.browser_content_fingerprint_sha256 =
        officialTextContentFingerprint(browserExtracted);
      browserParityAudit.browser_category_evidence_sha256 = stableHash(
        browserExtracted.categories,
      );
      browserParityAudit.passed = Boolean(
        browserExtracted.text_available === true &&
          browserParityAudit.browser_content_fingerprint_sha256 ===
            browserParityAudit.http_content_fingerprint_sha256 &&
          browserParityAudit.browser_category_evidence_sha256 ===
            browserParityAudit.http_category_evidence_sha256,
      );
      if (!browserParityAudit.passed) {
        throw new Error(
          "Fresh HTTP browser parity audit failed for " +
            record.canonical_key +
            ": " +
            JSON.stringify(browserParityAudit),
        );
      }
      extracted = httpExtractedCandidate;
      httpStatus = httpResult.status;
      selectedTransport = "browser_context_http";
    } else {
      extracted = browserExtracted;
      httpStatus = browserHttpStatus;
    }
  }
  const officialTextIdentity = validateOfficialTextIdentity(record, extracted);
  const textUnavailable =
    extracted.not_found || !extracted.text_available;
  const httpStatusSurfaceDisagreement =
    httpStatus !== null &&
    httpStatus >= 400 &&
    extracted.text_available === true &&
    officialTextIdentity.verified === true;
  const categories = extracted.categories.map((category) => ({
    ...category,
    evidence_state: textUnavailable
      ? "official_text_unavailable"
      : category.evidence_state,
  }));
  return {
    schema: "congress-text-relevance-measure-v2",
    text_match_policy_version: TEXT_MATCH_POLICY_VERSION,
    collected_at: isoNow(),
    primary_source: "Congress.gov official measure text page",
    discovery_canonical_key: record.canonical_key,
    discovery_record_sha256: stableHash(record),
    requested_url: textUrl,
    http_status: httpStatus,
    http_status_surface_disagreement: httpStatusSurfaceDisagreement,
    loaded_url: extracted.loaded_url,
    page_title: extracted.page_title,
    text_available: !textUnavailable,
    not_found: extracted.not_found,
    official_text_identity: officialTextIdentity,
    official_text_surface_marker: extracted.official_text_surface_marker,
    shown_here_marker_found: extracted.shown_here_marker_found,
    amendment_text_marker_found: extracted.amendment_text_marker_found,
    version_label: extracted.version_label,
    amendment_text_label: extracted.amendment_text_label,
    official_text_character_count: extracted.official_text_character_count,
    official_text_token_count: extracted.official_text_token_count,
    official_text_fnv1a32: extracted.official_text_fnv1a32,
    official_text_sha256: extracted.official_text_sha256,
    _official_text_for_archive: extracted._official_text_for_archive,
    transport_provenance: {
      schema: "congress-text-transport-provenance-v1",
      selected_transport: selectedTransport,
      http_fast_path_opt_in: Boolean(httpFastPath),
      browser_context_seeded_http: Boolean(httpFastPath),
      cookie_export_performed: false,
      manual_challenge_interaction_performed: false,
      stealth_escalation_performed: false,
      shared_global_request_start_limiter: true,
      browser_fallback_performed:
        Boolean(httpFastPath) && selectedTransport === "browser_navigation",
      browser_fallback_reason: browserFallbackReason,
      frozen_browser_hash_equivalence: equivalence,
      browser_parity_audit: browserParityAudit,
      http_response:
        httpResult?.kind === "accepted"
          ? {
              status: httpResult.status,
              loaded_url: httpResult.loaded_url,
              body_sha256: httpResult.body_sha256,
              body_byte_length: httpResult.body_byte_length,
              content_type: httpResult.content_type,
              validation: httpResult.validation,
            }
          : null,
      http_attempts: httpResult?.attempts ?? [],
    },
    categories,
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const runDir = assertWithinRoot(args.run);
  const registryPath = path.resolve(ROOT, args.registry);
  const productionOutDir = assertWithinRoot(
    path.join(runDir, "text_relevance"),
  );
  if (args.canary && !args.out) {
    throw new Error("--canary requires an explicit isolated --out directory");
  }
  const outDir = assertWithinRoot(args.out ?? productionOutDir);
  const relativeToProduction = path.relative(productionOutDir, outDir);
  if (
    args.canary &&
    (relativeToProduction === "" ||
      (!relativeToProduction.startsWith("..") &&
        !path.isAbsolute(relativeToProduction)))
  ) {
    throw new Error(
      `Canary output must not equal or be nested within ${productionOutDir}`,
    );
  }
  const discoveryManifest = await readJson(path.join(runDir, "manifest.json"));
  if (discoveryManifest.status !== "complete") {
    throw new Error("Advanced Search discovery manifest is not complete");
  }
  const registryText = await fs.readFile(registryPath, "utf8");
  const registry = JSON.parse(registryText);
  const registryHash = stableHash(registryText);
  const summaryIndex = await loadBillstatusSummaryIndex(
    args.billstatusManifest,
  );
  const recordsInput = await readNdjsonWithHashes(
    path.join(runDir, discoveryManifest.union.records_file),
  );
  const membershipsInput = await readNdjsonWithHashes(
    path.join(runDir, discoveryManifest.union.memberships_file),
  );
  let records = recordsInput.rows;
  const memberships = membershipsInput.rows;
  if (
    recordsInput.rows_sha256 !== discoveryManifest.union.records_sha256 ||
    membershipsInput.rows_sha256 !==
      discoveryManifest.union.memberships_sha256 ||
    records.length !== discoveryManifest.union.unique_record_count ||
    memberships.length !== discoveryManifest.union.membership_count
  ) {
    throw new Error("Advanced Search union hashes or counts do not reconcile");
  }
  const recordKeys = new Set(records.map((record) => record.canonical_key));
  if (recordKeys.size !== records.length) {
    throw new Error("Advanced Search union contains duplicate canonical records");
  }
  const categoryById = new Map(
    registry.categories.map((category) => [category.id, category]),
  );
  const queryMetaById = new Map(
    discoveryManifest.queries.map((query) => [query.query_id, query]),
  );
  const membershipIdsByRecord = new Map();
  const coverageIdsByRecord = new Map();
  const unknownMemberships = [];
  for (const membership of memberships) {
    if (!recordKeys.has(membership.canonical_key)) {
      throw new Error(
        `Membership references an unknown record: ${membership.canonical_key}`,
      );
    }
    if (categoryById.has(membership.query_id)) {
      if (!membershipIdsByRecord.has(membership.canonical_key)) {
        membershipIdsByRecord.set(membership.canonical_key, []);
      }
      membershipIdsByRecord.get(membership.canonical_key).push(
        membership.query_id,
      );
    } else if (
      queryMetaById.get(membership.query_id)?.record_kind === "coverage_probe"
    ) {
      if (!coverageIdsByRecord.has(membership.canonical_key)) {
        coverageIdsByRecord.set(membership.canonical_key, []);
      }
      coverageIdsByRecord.get(membership.canonical_key).push(
        membership.query_id,
      );
    } else {
      unknownMemberships.push(membership);
    }
  }
  if (unknownMemberships.length) {
    throw new Error(
      `Unclassified discovery memberships: ${unknownMemberships.length}`,
    );
  }
  const recordsWithoutMembership = records.filter(
    (record) =>
      !membershipIdsByRecord.has(record.canonical_key) &&
      !coverageIdsByRecord.has(record.canonical_key),
  );
  if (recordsWithoutMembership.length) {
    throw new Error(
      `Discovery records without membership provenance: ${recordsWithoutMembership.length}`,
    );
  }
  const registryIdentifierCount = registry.categories.reduce(
    (count, category) => count + category.identifiers.length,
    0,
  );
  if (registry.categories.length !== 19 || registryIdentifierCount !== 210) {
    throw new Error(
      `Registry closure mismatch: ${registry.categories.length} categories / ${registryIdentifierCount} identifiers`,
    );
  }
  const recordsByKey = new Map(
    records.map((record) => [record.canonical_key, record]),
  );
  const categorySpecsForRecord = (record) =>
    buildRecordCategorySpecs({
      registryCategories: registry.categories,
      directMembershipIds: canonicalKeySet(
        membershipIdsByRecord.get(record.canonical_key) ?? [],
      ),
      coverageProbeIds: canonicalKeySet(
        coverageIdsByRecord.get(record.canonical_key) ?? [],
      ),
    });
  const freshHttpCanaryBundle = await loadFreshHttpCanaryBundle(
    args.freshHttpCanaryManifest,
    {
      discoveryManifest,
      recordsInput,
      membershipsInput,
      registryHash,
      summaryIndex,
      recordsByKey,
      categorySpecsForRecord,
    },
  );
  if (args.keys) {
    records = records.filter((record) => args.keys.has(record.canonical_key));
    const missing = [...args.keys].filter(
      (key) => !records.some((record) => record.canonical_key === key),
    );
    if (missing.length) {
      throw new Error(`Unknown selected canonical keys: ${missing.join(", ")}`);
    }
  }
  if (args.limit !== null) records = records.slice(0, args.limit);
  if (!records.length) throw new Error("No category candidates selected");
  const corpusAcceptanceEligible =
    !args.canary &&
    records.length === discoveryManifest.union.unique_record_count;
  const selectedRecordKeys = new Set(
    records.map((record) => record.canonical_key),
  );
  const expectedDirectMembershipCount = memberships.filter(
    (membership) =>
      selectedRecordKeys.has(membership.canonical_key) &&
      categoryById.has(membership.query_id),
  ).length;
  const expectedCoverageProbeMembershipCount = memberships.filter(
    (membership) =>
      selectedRecordKeys.has(membership.canonical_key) &&
      queryMetaById.get(membership.query_id)?.record_kind === "coverage_probe",
  ).length;
  const expectedCategoryAuditEvidenceCount =
    records.length * registry.categories.length;
  const logicalWorkerCount = Math.min(
    args.httpFastPath ? args.httpWorkers : args.workers,
    records.length,
  );

  await fs.mkdir(outDir, { recursive: true });
  const progressFile = path.join(outDir, "progress.json");
  const eventsDir = path.join(outDir, "events");
  await fs.mkdir(eventsDir, { recursive: true });
  const runLock = await acquireRunLock(outDir);
  const partialFile = path.join(outDir, "manifest.partial.json");
  const manifestFile = path.join(outDir, "manifest.json");
  const commandLine = [
    process.execPath,
    path.relative(ROOT, fileURLToPath(import.meta.url)),
    ...process.argv.slice(2),
  ].join(" ");
  const startedMilliseconds = Date.now();
  let progressWriteQueue = Promise.resolve();
  const writeProgress = (payload) => {
    const operation = progressWriteQueue.then(async () => {
      const {
        status,
        completed,
        collected,
        resumed,
        preserved,
        failureCount = 0,
        detail = "",
      } = payload;
    const elapsedSeconds = Math.max(
      0.001,
      (Date.now() - startedMilliseconds) / 1000,
    );
    const recordsPerMinute =
      completed > 0 ? completed / (elapsedSeconds / 60) : 0;
    const etaSeconds =
      recordsPerMinute > 0
        ? Math.max(0, ((records.length - completed) / recordsPerMinute) * 60)
        : null;
    await writeJson(progressFile, {
      schema_version: 1,
      updated_at: isoNow(),
      lane: "codex",
      source: "congress-full-text",
      run_id: "legislation-full-text-20260726",
      kind: "collector.progress",
      status,
      stage: "official-full-text",
      current: completed,
      total: records.length,
      summary: `${completed}/${records.length} full-text records`,
      detail:
        detail ||
        "Complete official text is checked against every assigned category identifier; absence remains neutral.",
      workers: logicalWorkerCount,
      active_workers:
        status === "running"
          ? Math.min(logicalWorkerCount, records.length - completed)
          : 0,
      collected_records: collected,
      resumed_records: resumed,
      preserved_prior_records: preserved,
      failure_count: failureCount,
      records_per_minute: Number(recordsPerMinute.toFixed(3)),
      rate: Number(recordsPerMinute.toFixed(3)),
      eta_seconds:
        etaSeconds === null ? null : Number(etaSeconds.toFixed(1)),
      command: commandLine,
      output_directory: outDir,
      candidate_deletion_allowed: false,
      absence_is_neutral: true,
    });
    });
    progressWriteQueue = operation.catch(() => {});
    return operation;
  };
  const manifest = {
    schema: "congress-text-relevance-enrichment-manifest-v2",
    text_match_policy_version: TEXT_MATCH_POLICY_VERSION,
    status: "running",
    started_at: isoNow(),
    completed_at: null,
    primary_source: "Congress.gov official measure text pages",
    discovery_manifest: path
      .relative(ROOT, path.join(runDir, "manifest.json"))
      .split(path.sep)
      .join("/"),
    discovery_records_sha256: discoveryManifest.union.records_sha256,
    discovery_records_file_sha256: recordsInput.file_sha256,
    discovery_memberships_sha256:
      discoveryManifest.union.memberships_sha256,
    discovery_memberships_file_sha256: membershipsInput.file_sha256,
    registry_file: path.relative(ROOT, registryPath).split(path.sep).join("/"),
    registry_sha256: registryHash,
    billstatus_summary_manifest_file: path
      .relative(ROOT, summaryIndex.file)
      .split(path.sep)
      .join("/"),
    billstatus_summary_manifest_sha256: summaryIndex.file_sha256,
    billstatus_summary_receipt_count:
      summaryIndex.by_canonical_key.size,
    selected_record_count: records.length,
    run_scope: args.canary ? "canary" : "full_corpus",
    corpus_acceptance_eligible: corpusAcceptanceEligible,
    expected_direct_category_membership_count:
      expectedDirectMembershipCount,
    expected_coverage_probe_membership_count:
      expectedCoverageProbeMembershipCount,
    expected_category_audit_evidence_count:
      expectedCategoryAuditEvidenceCount,
    category_audit_scope: {
      every_record_checked_against_every_category: true,
      registry_category_count: registry.categories.length,
      registry_identifier_count: registryIdentifierCount,
      official_text_archive_required_when_text_available: true,
      official_summary_absence_is_neutral: true,
    },
    completed_records: [],
    worker_count: logicalWorkerCount,
    global_request_start_delay_ms: args.delayMs,
    minimum_request_start_interval_preserved: true,
    transport_policy: {
      default_transport: "browser_navigation",
      http_fast_path_opt_in: args.httpFastPath,
      browser_context_seeded_http: args.httpFastPath,
      logical_http_worker_count: args.httpFastPath
        ? logicalWorkerCount
        : 0,
      http_max_attempts: args.httpFastPath ? args.httpMaxAttempts : 0,
      http_retry_base_ms: args.httpFastPath ? args.httpRetryBaseMs : null,
      fresh_http_canary_manifest:
        freshHttpCanaryBundle?.manifest_file ?? null,
      fresh_http_canary_manifest_sha256:
        freshHttpCanaryBundle?.manifest_file_sha256 ?? null,
      fresh_http_authorization: null,
      browser_parity_audit_interval: args.httpFastPath
        ? args.httpParityInterval
        : null,
      browser_max_attempts: args.browserMaxAttempts,
      browser_page_timeout_ms: args.pageTimeoutMs,
      frozen_browser_hash_equivalence_required: args.httpFastPath,
      fresh_http_bill_amendment_canary_required:
        Boolean(freshHttpCanaryBundle),
      fresh_http_periodic_browser_parity_required:
        Boolean(freshHttpCanaryBundle),
      unsafe_http_response_browser_fallback_required: args.httpFastPath,
      cookie_export_performed: false,
      manual_challenge_interaction_performed: false,
      stealth_escalation_performed: false,
    },
    destructive_filtering: false,
    exclusion_decisions_made: 0,
  };
  await writeJson(partialFile, manifest);

  const remoteBrowser = args.endpoint
    ? await chromium.connectOverCDP(args.endpoint)
    : null;
  const context = remoteBrowser
    ? remoteBrowser.contexts()[0]
    : await chromium.launchPersistentContext(args.profile, {
        executablePath: args.chrome,
        headless: args.headless,
        ignoreDefaultArgs: ["--enable-automation"],
        args: ["--disable-blink-features=AutomationControlled"],
        viewport: { width: 1400, height: 900 },
      });
  if (!context) {
    throw new Error("Visible Chrome endpoint did not expose a browser context");
  }
  const artifacts = new Array(records.length);
  const completedEntries = new Array(records.length);
  let resumedCount = 0;
  let collectedCount = 0;
  let noRegressionPreservedCount = 0;
  let completedCount = 0;
  let pages = [];
  try {
    pages = remoteBrowser
      ? []
      : [context.pages()[0] || (await context.newPage())];
    while (
      pages.length < logicalWorkerCount
    ) {
      pages.push(await context.newPage());
    }
    const scheduleRequest = createGlobalRateLimiter(args.delayMs);
    const browserWarmup = freshHttpCanaryBundle
      ? await warmBrowserContextFromCanary(
          pages[0],
          freshHttpCanaryBundle,
          scheduleRequest,
          args.pageTimeoutMs,
          { nativeContext: remoteBrowser ? context : null },
        )
      : null;
    const liveUserAgent = await pages[0].evaluate(() => navigator.userAgent);
    const httpFastPath = args.httpFastPath
      ? createCongressContextHttpFastPath({
          requestContext: context.request,
          scheduleRequest,
          maxAttempts: args.httpMaxAttempts,
          retryBaseMs: args.httpRetryBaseMs,
          requestHeaders: {
            "user-agent": liveUserAgent,
            accept:
              "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "accept-language": "en-US,en;q=0.9",
            referer: "https://www.congress.gov/",
          },
        })
      : null;
    const freshHttpAuthorization = freshHttpCanaryBundle
      ? await runFreshHttpTransportCanary(
          pages[0],
          httpFastPath,
          freshHttpCanaryBundle,
          browserWarmup,
        )
      : null;
    manifest.transport_policy.fresh_http_authorization =
      freshHttpAuthorization;
    await writeJson(partialFile, manifest);
    const parityAuditIndexes = new Set();
    if (freshHttpAuthorization?.passed === true) {
      for (
        let index = args.httpParityInterval - 1;
        index < records.length;
        index += args.httpParityInterval
      ) {
        parityAuditIndexes.add(index);
      }
    }
    let nextIndex = 0;
    let fatalError = null;
    let checkpointQueue = Promise.resolve();
    await writeProgress({
      status: "running",
      completed: 0,
      collected: 0,
      resumed: 0,
      preserved: 0,
    });
    await writeGlassboxEvent(eventsDir, 0, "run", {
      kind: "collector.started",
      status: "running",
      stage: "official-full-text",
      summary: `0/${records.length} full-text records | ${logicalWorkerCount} workers`,
      detail:
        "All harvested candidates are retained while complete official text is checked against the 210 category identifiers.",
      command: commandLine,
      input: {
        records: records.length,
        discovery_memberships: memberships.length,
        category_identifiers: registry.categories.reduce(
          (count, category) => count + category.identifiers.length,
          0,
        ),
      },
      output: outDir,
      current: 0,
      total: records.length,
    });

    const processRecord = async (
      page,
      index,
      workerId,
      { deferCommit = false } = {},
    ) => {
      const record = records[index];
      const queryIds = canonicalKeySet(
        membershipIdsByRecord.get(record.canonical_key) ?? [],
      );
      const coverageIds = canonicalKeySet(
        coverageIdsByRecord.get(record.canonical_key) ?? [],
      );
      const categorySpecs = categorySpecsForRecord(record);
      const membershipHash = stableHash(categorySpecs);
      const summaryEvidence = await officialSummaryEvidence(
        record,
        summaryIndex,
      );
      const summaryEvidenceHash = stableHash(summaryEvidence);
      const measureFile = path.join(
        outDir,
        "raw",
        String(record.congress),
        record.type,
        `${record.number}.json`,
      );
      let artifact = null;
      let candidate = null;
      let priorRead = null;
      try {
        priorRead = await readJsonWithFileHash(measureFile);
        const priorIntegrity =
          priorRead.artifact.artifact_integrity_sha256;
        if (
          !/^[0-9a-f]{64}$/i.test(priorIntegrity ?? "") ||
          priorIntegrity !== artifactIntegritySha256(priorRead.artifact)
        ) {
          throw new Error(
            `Existing text artifact failed integrity validation: ${measureFile}`,
          );
        }
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
      const recordHash = stableHash(record);
      const priorArchiveCurrent = priorRead
        ? await validateOfficialTextArchive(outDir, priorRead.artifact)
        : false;
      const priorTransportCurrent = priorRead
        ? args.canary ||
          (await validateCommittedHttpEpoch(
            outDir,
            priorRead.artifact,
            freshHttpAuthorization,
          ))
        : false;
      const priorInputsCurrent = Boolean(
        priorRead &&
          priorArchiveCurrent &&
          priorTransportCurrent &&
          priorRead.artifact.discovery_canonical_key === record.canonical_key &&
          priorRead.artifact.discovery_record_sha256 === recordHash &&
          priorRead.artifact.registry_sha256 === registryHash &&
          priorRead.artifact.membership_sha256 === membershipHash &&
          priorRead.artifact.official_summary_evidence_sha256 ===
            summaryEvidenceHash &&
          priorRead.artifact.text_match_policy_version ===
            TEXT_MATCH_POLICY_VERSION,
      );
      if (args.resume) {
        if (priorInputsCurrent) {
          artifact = priorRead.artifact;
          if (!deferCommit) resumedCount += 1;
        }
      }
      const preparedState = () => ({
        page,
        index,
        workerId,
        record,
        queryIds,
        coverageIds,
        categorySpecs,
        membershipHash,
        summaryEvidence,
        summaryEvidenceHash,
        measureFile,
        priorRead,
        priorInputsCurrent,
        artifact,
        candidate,
        resumed: Boolean(artifact),
      });
      if (!artifact) {
        candidate = await collectMeasure(
          page,
          record,
          categorySpecs,
          scheduleRequest,
          {
            httpFastPath,
            freshHttpAuthorization,
            browserParityAuditRequired:
              !deferCommit && parityAuditIndexes.has(index),
            browserMaxAttempts: args.browserMaxAttempts,
            pageTimeoutMs: args.pageTimeoutMs,
            frozenBaseline: frozenBrowserBaseline(
              priorRead?.artifact,
              priorRead?.file_sha256,
            ),
          },
        );
        candidate.registry_sha256 = registryHash;
        candidate.membership_sha256 = membershipHash;
        candidate.official_summary = summaryEvidence;
        candidate.official_summary_evidence_sha256 = summaryEvidenceHash;
        candidate.discovery_stage_context = {
          record_kind: record.record_kind,
          title: record.title ?? null,
          purpose: record.purpose ?? null,
          sponsor: record.sponsor ?? null,
          sponsor_url: record.sponsor_url ?? null,
          sponsor_line: record.sponsor_line ?? null,
          initial_action: record.initial_action ?? null,
          initial_action_date_iso: record.initial_action_date_iso ?? null,
          latest_action: record.latest_action ?? null,
          latest_action_date: record.latest_action_date ?? null,
          tracker_status: record.tracker_status ?? null,
          amends: record.amends ?? null,
          cosponsor_count: record.cosponsor_count ?? null,
          committees: record.committees ?? null,
        };
        candidate.category_audit_scope = {
          registry_category_count: registry.categories.length,
          registry_identifier_count: registry.categories.reduce(
            (count, category) => count + category.identifiers.length,
            0,
          ),
          every_registry_category_checked: true,
        };
        if (deferCommit) return preparedState();
        await persistOfficialTextArchive(outDir, record, candidate);
        candidate.artifact_integrity_sha256 =
          artifactIntegritySha256(candidate);

        if (
          priorInputsCurrent &&
          priorRead.artifact.text_available === true &&
          candidate.text_available !== true
        ) {
          await writeImmutableTransportReceipt(outDir, record, {
            decision: "preserve_prior_no_regression",
            reason: "candidate_lost_previously_verified_official_text",
            preserved_artifact_file: path
              .relative(outDir, measureFile)
              .split(path.sep)
              .join("/"),
            preserved_artifact_file_sha256: priorRead.file_sha256,
            preserved_artifact_integrity_sha256:
              priorRead.artifact.artifact_integrity_sha256 ?? null,
            candidate_artifact_integrity_sha256:
              candidate.artifact_integrity_sha256,
            candidate_transport_provenance:
              candidate.transport_provenance,
            candidate_content_projection:
              officialTextContentProjection(candidate),
          });
          artifact = priorRead.artifact;
          noRegressionPreservedCount += 1;
        } else {
          const receipt = await writeImmutableTransportReceipt(
            outDir,
            record,
            {
              decision: "accept_collected_artifact",
              candidate_artifact_integrity_sha256:
                candidate.artifact_integrity_sha256,
              selected_transport:
                candidate.transport_provenance.selected_transport,
              transport_provenance: candidate.transport_provenance,
              content_projection:
                officialTextContentProjection(candidate),
            },
          );
          candidate.transport_receipt = receipt;
          candidate.artifact_integrity_sha256 =
            artifactIntegritySha256(candidate);
          await writeJson(measureFile, candidate);
          artifact = candidate;
        }
        collectedCount += 1;
      }
      if (deferCommit) return preparedState();
      artifacts[index] = artifact;
      completedEntries[index] = {
        canonical_key: record.canonical_key,
        file: path.relative(outDir, measureFile).split(path.sep).join("/"),
        text_available: artifact.text_available,
        official_summary_available:
          artifact.official_summary?.available === true,
        official_text_archive_file:
          artifact.official_text_archive?.file ?? null,
        membership_count: artifact.categories.filter(
          (item) => item.direct_category_membership,
        ).length,
        category_audit_count: artifact.categories.length,
        cross_category_full_text_audit_count: artifact.categories.filter(
          (item) => item.cross_category_full_text_audit,
        ).length,
        localized_membership_count: artifact.categories.filter(
          (item) =>
            item.direct_category_membership &&
            item.localized_identifier_count,
        ).length,
        localized_category_audit_count: artifact.categories.filter(
          (item) => item.localized_identifier_count,
        ).length,
      };
      completedCount += 1;
      const localizedCategories = artifact.categories
        .filter((item) => item.localized_identifier_count)
        .map((item) => ({
          query_id: item.query_id,
          identifiers: item.localized_identifiers,
        }));
      await writeGlassboxEvent(
        eventsDir,
        completedCount,
        record.canonical_key,
        {
          kind: "collector.record.completed",
          status: artifact.text_available ? "completed" : "needs-review",
          stage: `worker-${workerId}`,
          summary:
            `${completedCount}/${records.length} ${record.canonical_key} | ` +
            `${artifact.text_available ? "text verified" : "text unavailable"} | ` +
            `${localizedCategories.length} category signal(s)`,
          detail:
            "Identifier absence is neutral; this record remains preserved unless a separate provenance-complete review decision is made.",
          command: `GET ${artifact.requested_url}`,
          input: {
            canonical_key: record.canonical_key,
            direct_membership_query_ids: queryIds,
            coverage_probe_query_ids: coverageIds,
            audited_categories: categorySpecs.map((category) => ({
              query_id: category.query_id,
              evidence_role: category.evidence_role,
              identifiers: category.identifiers,
            })),
          },
          output: {
            loaded_url: artifact.loaded_url,
            text_available: artifact.text_available,
            version_label: artifact.version_label,
            official_text_character_count:
              artifact.official_text_character_count,
            official_text_token_count: artifact.official_text_token_count,
            official_text_sha256: artifact.official_text_sha256,
            official_text_archive_file:
              artifact.official_text_archive?.file ?? null,
            official_summary_available:
              artifact.official_summary?.available === true,
            official_summary_item_count:
              artifact.official_summary?.items?.length ?? 0,
            localized_categories: localizedCategories,
            selected_transport:
              artifact.transport_provenance?.selected_transport ?? null,
            artifact_integrity_sha256:
              artifact.artifact_integrity_sha256 ?? null,
          },
          artifact: path.relative(ROOT, measureFile).split(path.sep).join("/"),
          current: completedCount,
          total: records.length,
        },
      );
      if (
        completedCount === records.length ||
        completedCount % 10 === 0
      ) {
        await writeProgress({
          status: "running",
          completed: completedCount,
          collected: collectedCount,
          resumed: resumedCount,
          preserved: noRegressionPreservedCount,
        });
      }
      if (completedCount % 100 === 0 || completedCount === records.length) {
        const snapshot = {
          ...manifest,
          completed_records: completedEntries.filter(Boolean),
        };
        checkpointQueue = checkpointQueue.then(() =>
          writeJson(partialFile, snapshot),
        );
        console.log(
          `TEXT PROGRESS ${completedCount}/${records.length}: ` +
            `${collectedCount} collected, ${resumedCount} resumed, ` +
            `${noRegressionPreservedCount} prior artifact(s) preserved`,
        );
      }
    };

    const auditAndCommitHttpEpoch = async (
      epochNumber,
      preparedRecords,
      auditPage,
    ) => {
      const httpPrepared = preparedRecords.filter(
        (prepared) =>
          prepared.candidate?.transport_provenance?.selected_transport ===
          "browser_context_http",
      );
      if (!httpPrepared.length) return null;
      const audited = httpPrepared[0];
      const browserHttpStatus = await gotoWithRetry(
        auditPage,
        audited.candidate.requested_url,
        "#main, main",
        1,
        scheduleRequest,
        true,
        args.pageTimeoutMs,
      );
      const browserExtracted = await extractStableTextEvidence(
        auditPage,
        audited.categorySpecs,
      );
      validateOfficialTextIdentity(audited.record, browserExtracted);
      assertExpectedTextSurface(audited.record, browserExtracted);
      const browserContentFingerprint =
        officialTextContentFingerprint(browserExtracted);
      const browserCategoryEvidenceSha256 = stableHash(
        browserExtracted.categories,
      );
      const httpContentFingerprint = officialTextContentFingerprint(
        audited.candidate,
      );
      const httpCategoryEvidenceSha256 = stableHash(
        audited.candidate.categories,
      );
      if (
        browserContentFingerprint !== httpContentFingerprint ||
        browserCategoryEvidenceSha256 !== httpCategoryEvidenceSha256
      ) {
        throw new Error(
          "HTTP epoch browser parity mismatch before commit for " +
            audited.record.canonical_key +
            ": " +
            JSON.stringify({
              browser_content_fingerprint_sha256: browserContentFingerprint,
              browser_category_evidence_sha256: browserCategoryEvidenceSha256,
              http_content_fingerprint_sha256: httpContentFingerprint,
              http_category_evidence_sha256: httpCategoryEvidenceSha256,
            }),
        );
      }
      const httpRecords = httpPrepared
        .slice()
        .sort((left, right) => left.index - right.index)
        .map((prepared) => ({
          canonical_key: prepared.record.canonical_key,
          source_index: prepared.index,
          content_fingerprint_sha256: officialTextContentFingerprint(
            prepared.candidate,
          ),
          category_evidence_sha256: stableHash(prepared.candidate.categories),
        }));
      const receipt = await writeImmutableHttpEpochReceipt(outDir, {
        passed: true,
        epoch_number: epochNumber,
        authorization_sha256:
          freshHttpAuthorization.authorization_sha256,
        canary_manifest_file_sha256:
          freshHttpAuthorization.canary_manifest_file_sha256,
        fresh_http_accept_count: httpPrepared.length,
        prepared_record_count: preparedRecords.length,
        source_index_start: Math.min(
          ...preparedRecords.map((prepared) => prepared.index),
        ),
        source_index_end: Math.max(
          ...preparedRecords.map((prepared) => prepared.index),
        ),
        http_records: httpRecords,
        browser_audit: {
          canonical_key: audited.record.canonical_key,
          browser_http_status: browserHttpStatus,
          browser_content_fingerprint_sha256: browserContentFingerprint,
          browser_category_evidence_sha256:
            browserCategoryEvidenceSha256,
          http_content_fingerprint_sha256: httpContentFingerprint,
          http_category_evidence_sha256: httpCategoryEvidenceSha256,
          passed: true,
        },
      });
      const binding = {
        schema: "congress-http-acceptance-epoch-binding-v1",
        passed: true,
        epoch_number: epochNumber,
        authorization_sha256:
          freshHttpAuthorization.authorization_sha256,
        receipt_file: receipt.file,
        receipt_payload_sha256: receipt.receipt_payload_sha256,
        receipt_file_sha256: receipt.file_sha256,
        audited_canonical_key: audited.record.canonical_key,
      };
      for (const prepared of httpPrepared) {
        prepared.candidate.transport_provenance.browser_parity_audit = {
          required: true,
          passed: true,
          scope: "committed_http_acceptance_epoch",
          epoch_number: epochNumber,
          audited_canonical_key: audited.record.canonical_key,
          receipt_payload_sha256: receipt.receipt_payload_sha256,
        };
      }
      return binding;
    };

    const commitPreparedRecord = async (prepared, committedHttpEpoch = null) => {
      const {
        index,
        workerId,
        record,
        queryIds,
        coverageIds,
        categorySpecs,
        measureFile,
        priorRead,
        priorInputsCurrent,
      } = prepared;
      let artifact = prepared.artifact;
      const candidate = prepared.candidate;
      if (artifact) {
        resumedCount += 1;
      } else {
        if (
          candidate.transport_provenance?.selected_transport ===
          "browser_context_http"
        ) {
          if (!args.canary && !committedHttpEpoch) {
            throw new Error(
              "Refusing to persist HTTP artifact without a passed committed epoch",
            );
          }
          if (committedHttpEpoch) {
            candidate.transport_provenance.committed_http_epoch =
              committedHttpEpoch;
          }
        }
        await persistOfficialTextArchive(outDir, record, candidate);
        candidate.artifact_integrity_sha256 = artifactIntegritySha256(candidate);
        if (
          priorInputsCurrent &&
          priorRead.artifact.text_available === true &&
          candidate.text_available !== true
        ) {
          await writeImmutableTransportReceipt(outDir, record, {
            decision: "preserve_prior_no_regression",
            reason: "candidate_lost_previously_verified_official_text",
            preserved_artifact_file: path
              .relative(outDir, measureFile)
              .split(path.sep)
              .join("/"),
            preserved_artifact_file_sha256: priorRead.file_sha256,
            preserved_artifact_integrity_sha256:
              priorRead.artifact.artifact_integrity_sha256 ?? null,
            candidate_artifact_integrity_sha256:
              candidate.artifact_integrity_sha256,
            candidate_transport_provenance: candidate.transport_provenance,
            candidate_content_projection:
              officialTextContentProjection(candidate),
          });
          artifact = priorRead.artifact;
          noRegressionPreservedCount += 1;
        } else {
          const receipt = await writeImmutableTransportReceipt(
            outDir,
            record,
            {
              decision: "accept_collected_artifact",
              candidate_artifact_integrity_sha256:
                candidate.artifact_integrity_sha256,
              selected_transport:
                candidate.transport_provenance.selected_transport,
              transport_provenance: candidate.transport_provenance,
              content_projection: officialTextContentProjection(candidate),
            },
          );
          candidate.transport_receipt = receipt;
          candidate.artifact_integrity_sha256 = artifactIntegritySha256(candidate);
          await writeJson(measureFile, candidate);
          artifact = candidate;
        }
        collectedCount += 1;
      }
      artifacts[index] = artifact;
      completedEntries[index] = {
        canonical_key: record.canonical_key,
        file: path.relative(outDir, measureFile).split(path.sep).join("/"),
        text_available: artifact.text_available,
        official_summary_available:
          artifact.official_summary?.available === true,
        official_text_archive_file:
          artifact.official_text_archive?.file ?? null,
        membership_count: artifact.categories.filter(
          (item) => item.direct_category_membership,
        ).length,
        category_audit_count: artifact.categories.length,
        cross_category_full_text_audit_count: artifact.categories.filter(
          (item) => item.cross_category_full_text_audit,
        ).length,
        localized_membership_count: artifact.categories.filter(
          (item) =>
            item.direct_category_membership && item.localized_identifier_count,
        ).length,
        localized_category_audit_count: artifact.categories.filter(
          (item) => item.localized_identifier_count,
        ).length,
      };
      completedCount += 1;
      const localizedCategories = artifact.categories
        .filter((item) => item.localized_identifier_count)
        .map((item) => ({
          query_id: item.query_id,
          identifiers: item.localized_identifiers,
        }));
      await writeGlassboxEvent(
        eventsDir,
        completedCount,
        record.canonical_key,
        {
          kind: "collector.record.completed",
          status: artifact.text_available ? "completed" : "needs-review",
          stage: `worker-${workerId}`,
          summary:
            `${completedCount}/${records.length} ${record.canonical_key} | ` +
            `${artifact.text_available ? "text verified" : "text unavailable"} | ` +
            `${localizedCategories.length} category signal(s)`,
          detail:
            "Identifier absence is neutral; this record remains preserved unless a separate provenance-complete review decision is made.",
          command: `GET ${artifact.requested_url}`,
          input: {
            canonical_key: record.canonical_key,
            direct_membership_query_ids: queryIds,
            coverage_probe_query_ids: coverageIds,
            audited_category_count: categorySpecs.length,
          },
          output: {
            loaded_url: artifact.loaded_url,
            text_available: artifact.text_available,
            official_text_character_count:
              artifact.official_text_character_count,
            official_text_token_count: artifact.official_text_token_count,
            official_text_sha256: artifact.official_text_sha256,
            official_text_archive_file:
              artifact.official_text_archive?.file ?? null,
            official_summary_available:
              artifact.official_summary?.available === true,
            localized_categories: localizedCategories,
            selected_transport:
              artifact.transport_provenance?.selected_transport ?? null,
            committed_http_epoch:
              artifact.transport_provenance?.committed_http_epoch ?? null,
            artifact_integrity_sha256:
              artifact.artifact_integrity_sha256 ?? null,
          },
          artifact: path.relative(ROOT, measureFile).split(path.sep).join("/"),
          current: completedCount,
          total: records.length,
        },
      );
      if (completedCount === records.length || completedCount % 10 === 0) {
        await writeProgress({
          status: "running",
          completed: completedCount,
          collected: collectedCount,
          resumed: resumedCount,
          preserved: noRegressionPreservedCount,
        });
      }
      if (completedCount % 100 === 0 || completedCount === records.length) {
        const snapshot = {
          ...manifest,
          completed_records: completedEntries.filter(Boolean),
        };
        checkpointQueue = checkpointQueue.then(() => writeJson(partialFile, snapshot));
        console.log(
          `TEXT PROGRESS ${completedCount}/${records.length}: ` +
            `${collectedCount} collected, ${resumedCount} resumed, ` +
            `${noRegressionPreservedCount} prior artifact(s) preserved`,
        );
      }
      return artifact;
    };

    const worker = async (page, workerId) => {
      while (!fatalError) {
        const index = nextIndex;
        nextIndex += 1;
        if (index >= records.length) return;
        try {
          await processRecord(page, index, workerId);
        } catch (error) {
          fatalError ??= error;
          return;
        }
      }
    };

    await Promise.all(pages.map((page, index) => worker(page, index + 1)));
    await checkpointQueue;
    if (fatalError) throw fatalError;
    if (artifacts.filter(Boolean).length !== records.length) {
      throw new Error("Concurrent text collection left an incomplete artifact set");
    }
    manifest.completed_records = completedEntries;

    const ledger = [];
    for (const artifact of artifacts) {
      for (const category of artifact.categories) {
        ledger.push({
          canonical_key: artifact.discovery_canonical_key,
          query_id: category.query_id,
          category_label: category.label,
          evidence_role: category.evidence_role,
          direct_category_membership:
            category.direct_category_membership === true,
          cross_category_full_text_audit:
            category.cross_category_full_text_audit === true,
          record_direct_membership_query_ids:
            category.record_direct_membership_query_ids ?? [],
          source_coverage_probe_ids:
            category.source_coverage_probe_ids ?? [],
          coverage_probe_candidate:
            category.coverage_probe_candidate === true,
          evidence_state: category.evidence_state,
          text_available: artifact.text_available,
          version_label: artifact.version_label,
          official_text_character_count:
            artifact.official_text_character_count,
          official_text_token_count: artifact.official_text_token_count,
          official_text_sha256: artifact.official_text_sha256,
          official_text_archive_file:
            artifact.official_text_archive?.file ?? null,
          official_text_archive_gzip_sha256:
            artifact.official_text_archive?.gzip_sha256 ?? null,
          official_summary_available:
            artifact.official_summary?.available === true,
          official_summary_item_count:
            artifact.official_summary?.items?.length ?? 0,
          official_summary_evidence_sha256:
            artifact.official_summary_evidence_sha256,
          localized_identifier_count: category.localized_identifier_count,
          localized_identifiers: category.localized_identifiers,
          separated_identifier_count: category.separated_identifier_count,
          separated_identifiers: category.separated_identifiers,
          identifier_evidence: category.identifiers,
          source_url: artifact.loaded_url,
          candidate_retained_in_discovery_corpus: true,
          exclusion_decision: null,
        });
      }
    }
    ledger.sort((left, right) =>
      `${left.query_id}:${left.canonical_key}`.localeCompare(
        `${right.query_id}:${right.canonical_key}`,
        undefined,
        { numeric: true },
      ),
    );
    const observedDirectMembershipCount = ledger.filter(
      (row) => row.direct_category_membership,
    ).length;
    if (
      ledger.length !== expectedCategoryAuditEvidenceCount ||
      observedDirectMembershipCount !== expectedDirectMembershipCount
    ) {
      throw new Error(
        `Category-audit reconciliation failed: ${ledger.length}/${expectedCategoryAuditEvidenceCount} rows, ` +
          `${observedDirectMembershipCount}/${expectedDirectMembershipCount} direct memberships`,
      );
    }
    const unresolved = ledger.filter(
      (row) => row.evidence_state !== "localized_official_text_evidence",
    );
    const ledgerFile = path.join(outDir, "text_relevance_evidence.ndjson");
    const unresolvedFile = path.join(
      outDir,
      "text_relevance_unresolved.ndjson",
    );
    await writeNdjson(ledgerFile, ledger);
    await writeNdjson(unresolvedFile, unresolved);
    manifest.status = "complete";
    manifest.completed_at = isoNow();
    manifest.membership_count = observedDirectMembershipCount;
    manifest.direct_category_membership_count =
      observedDirectMembershipCount;
    manifest.category_audit_evidence_count = ledger.length;
    manifest.actual_category_membership_evidence_count = ledger.filter(
      (row) => row.evidence_role === "category_membership",
    ).length;
    manifest.cross_category_full_text_audit_evidence_count = ledger.filter(
      (row) => row.evidence_role === "cross_category_full_text_audit",
    ).length;
    manifest.localized_official_text_membership_count = ledger.filter(
      (row) =>
        row.direct_category_membership &&
        row.evidence_state === "localized_official_text_evidence",
    ).length;
    manifest.localized_official_text_category_audit_count = ledger.filter(
      (row) => row.evidence_state === "localized_official_text_evidence",
    ).length;
    manifest.unresolved_membership_count = unresolved.filter(
      (row) => row.direct_category_membership,
    ).length;
    manifest.unresolved_category_audit_count = unresolved.length;
    manifest.text_unavailable_record_count = artifacts.filter(
      (artifact) => !artifact.text_available,
    ).length;
    manifest.official_text_archived_record_count = artifacts.filter(
      (artifact) => artifact.official_text_archive?.file,
    ).length;
    manifest.official_summary_available_record_count = artifacts.filter(
      (artifact) => artifact.official_summary?.available === true,
    ).length;
    manifest.transport_summary = {
      collected_record_count: collectedCount,
      resumed_record_count: resumedCount,
      no_regression_preserved_record_count:
        noRegressionPreservedCount,
      browser_navigation_record_count: artifacts.filter(
        (artifact) =>
          (artifact.transport_provenance?.selected_transport ??
            "browser_navigation") === "browser_navigation",
      ).length,
      browser_context_http_record_count: artifacts.filter(
        (artifact) =>
          artifact.transport_provenance?.selected_transport ===
          "browser_context_http",
      ).length,
      http_fast_path: httpFastPath?.snapshot() ?? null,
    };
    manifest.ledger_file = path
      .relative(outDir, ledgerFile)
      .split(path.sep)
      .join("/");
    manifest.ledger_sha256 = stableHash(ledger);
    manifest.unresolved_file = path
      .relative(outDir, unresolvedFile)
      .split(path.sep)
      .join("/");
    manifest.unresolved_sha256 = stableHash(unresolved);
    await writeJson(manifestFile, manifest);
    await removeWithRetry(partialFile);
    await writeProgress({
      status: "complete",
      completed: records.length,
      collected: collectedCount,
      resumed: resumedCount,
      preserved: noRegressionPreservedCount,
      detail:
        "Official-text evidence collection completed; no exclusion or production write was performed.",
    });
    await writeGlassboxEvent(eventsDir, records.length + 1, "run-complete", {
      kind: "collector.completed",
      status: "completed",
      stage: "official-full-text",
      summary: `${records.length}/${records.length} full-text records complete`,
      detail:
        `${ledger.length} category-audit evidence rows emitted; ${unresolved.length} audit rows remain without a localized official-text signal.`,
      command: commandLine,
      output: {
        manifest: path.relative(ROOT, manifestFile).split(path.sep).join("/"),
        ledger: path.relative(ROOT, ledgerFile).split(path.sep).join("/"),
        unresolved: path.relative(ROOT, unresolvedFile).split(path.sep).join("/"),
      },
      current: records.length,
      total: records.length,
    });
    console.log(
      `COMPLETE: ${ledger.length} category-audit evidence row(s), ` +
        `${observedDirectMembershipCount} direct membership(s), ` +
        `${unresolved.length} audit row(s) without localized official-text evidence`,
    );
  } catch (error) {
    manifest.status = "failed";
    manifest.failed_at = isoNow();
    manifest.completed_records = completedEntries.filter(Boolean);
    manifest.error = {
      name: error.name,
      message: error.message,
      stack: error.stack,
    };
    await writeJson(partialFile, manifest);
    await writeProgress({
      status: "failed",
      completed: manifest.completed_records.length,
      collected: collectedCount,
      resumed: resumedCount,
      preserved: noRegressionPreservedCount,
      failureCount: 1,
      detail: error.message,
    }).catch(() => {});
    await writeGlassboxEvent(
      eventsDir,
      manifest.completed_records.length + 1,
      "run-failed",
      {
        kind: "collector.failed",
        status: "failed",
        stage: "official-full-text",
        summary:
          `${manifest.completed_records.length}/${records.length} before failure`,
        detail:
          "Failure is quarantined; preserved discovery and completed evidence artifacts are not deleted.",
        command: commandLine,
        failure: {
          name: error.name,
          message: error.message,
        },
        current: manifest.completed_records.length,
        total: records.length,
      },
    ).catch(() => {});
    throw error;
  } finally {
    if (remoteBrowser) {
      await Promise.all(pages.map((page) => page.close().catch(() => {})));
    } else {
      await context.close();
    }
    await releaseRunLock(runLock);
  }
}

const isDirectExecution =
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isDirectExecution) {
  await main();
  process.exit(0);
}

export {
  artifactIntegritySha256,
  buildOfficialTextArchive,
  buildRecordCategorySpecs,
  extractTextEvidence,
  extractTextEvidenceFromHtml,
  frozenBrowserBaseline,
  officialTextContentFingerprint,
  officialTextContentProjection,
  officialSummaryEvidence,
  loadBillstatusSummaryIndex,
  TEXT_MATCH_POLICY_VERSION,
  validateOfficialTextIdentity,
  waitForCongressPage,
};
