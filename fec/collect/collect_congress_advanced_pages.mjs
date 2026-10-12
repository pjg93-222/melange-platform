#!/usr/bin/env node
/**
 * Collect legislation candidates exclusively from Congress.gov Advanced Search.
 *
 * Discovery is the official Advanced Search - Legislation form with "All fields
 * including Bill Text", word variants, all legislation types, and the 116th
 * through 119th Congresses. Results are traversed as deterministic 250-record
 * pages because Congress.gov's bulk CSV generator is unreliable for large
 * result sets. Each page must reconcile exactly to the site's reported range.
 */

import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import {
  classifySingleResultRedirect,
  displayNumberForCanonical,
  parseSingleResultPageSnapshot,
} from "../lib/congress_advanced_navigation.mjs";
import {
  CongressAutomaticVerificationTimeoutError,
  automaticVerificationError,
  buildCooldownState,
  classifyAutomaticVerification,
  remainingCooldownMs,
  validateCooldownState,
} from "../lib/congress_automatic_verification.mjs";
import {
  resultSummaryIsInProgress,
  selectStrictMainText,
} from "../lib/congress_result_surface.mjs";
import {
  writeJsonAtomicWithRetry,
  writeTextAtomicWithRetry,
} from "../lib/atomic_checkpoint.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const ROOT = path.resolve(import.meta.dirname, "../..");
const DEFAULT_REGISTRY = path.join(
  ROOT,
  "source_data",
  "legislation",
  "acu_legislation_search_registry.json",
);
const ADVANCED_SEARCH =
  "https://www.congress.gov/index.php/advanced-search/legislation";
const DEFAULT_CHROME =
  process.platform === "win32"
    ? "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    : "/usr/bin/google-chrome";
const PAGE_SIZE = 250;
const PAGE_SORT = "documentNumber:asc";
const ZERO_RESULT_SURFACE_VALIDATION = "strict-main-v2";
const RESULT_SUMMARY_WAIT_MS = 90_000;
const RESULT_SUMMARY_POLL_MS = 1_000;
const AUTOMATIC_VERIFICATION_WAIT_MS = 120_000;
const AUTOMATIC_VERIFICATION_BASE_COOLDOWN_MS = 10 * 60_000;
const AUTOMATIC_VERIFICATION_MAX_COOLDOWN_MS = 2 * 60 * 60_000;
const AUTOMATIC_VERIFICATION_STATE_FILE =
  "automatic_verification_cooldown.json";
const LEGISLATION_TYPE_IDS = {
  hr: "legislationTypehr",
  hres: "legislationTypehres",
  hjres: "legislationTypehjres",
  hconres: "legislationTypehconres",
  hamdt: "legislationTypehamdt",
  s: "legislationTypes",
  sres: "legislationTypesres",
  sjres: "legislationTypesjres",
  sconres: "legislationTypesconres",
  samdt: "legislationTypesamdt",
};
const TYPE_SLUG_TO_CODE = {
  "house-bill": "hr",
  "senate-bill": "s",
  "house-resolution": "hres",
  "senate-resolution": "sres",
  "house-joint-resolution": "hjres",
  "senate-joint-resolution": "sjres",
  "house-concurrent-resolution": "hconres",
  "senate-concurrent-resolution": "sconres",
  "house-amendment": "hamdt",
  "senate-amendment": "samdt",
};

function parseArgs(argv) {
  const args = {
    registry: DEFAULT_REGISTRY,
    out: path.join(
      "audits",
      "legfix5",
      "congress_advanced_pages_20260725",
    ),
    profile: path.join(os.tmpdir(), "codex-congress-pages-profile"),
    chrome: DEFAULT_CHROME,
    headless: true,
    delayMs: 1500,
    categories: null,
    resume: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--registry") args.registry = argv[++index];
    else if (value === "--out") args.out = argv[++index];
    else if (value === "--profile") args.profile = argv[++index];
    else if (value === "--chrome") args.chrome = argv[++index];
    else if (value === "--delay-ms") args.delayMs = Number(argv[++index]);
    else if (value === "--categories") {
      args.categories = new Set(
        argv[++index]
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      );
    } else if (value === "--headless") args.headless = true;
    else if (value === "--headed") args.headless = false;
    else if (value === "--resume") args.resume = true;
    else if (value === "--help") {
      console.log(`Usage: node fec/collect/collect_congress_advanced_pages.mjs [options]
  --registry PATH     Search registry JSON
  --out PATH          Output directory within the repository
  --profile PATH      Isolated Chrome profile directory
  --chrome PATH       Chrome executable
  --categories IDS    Comma-separated category/probe IDs
  --delay-ms N        Delay between Congress.gov page loads (default 1500)
  --headless          Run headless (default)
  --headed            Run headed only when explicitly requested
  --resume            Reuse page files that pass provenance checks`);
      process.exit(0);
    } else {
      throw new Error(`Unknown argument: ${value}`);
    }
  }
  if (!Number.isFinite(args.delayMs) || args.delayMs < 0) {
    throw new Error("--delay-ms must be a non-negative number");
  }
  return args;
}

function assertWithinRoot(target) {
  const resolved = path.resolve(ROOT, target);
  const relative = path.relative(ROOT, resolved);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`Output must stay within repository root: ${resolved}`);
  }
  return resolved;
}

function stableHash(value) {
  return crypto
    .createHash("sha256")
    .update(typeof value === "string" ? value : JSON.stringify(value))
    .digest("hex");
}

function isoNow() {
  return new Date().toISOString();
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function compact(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function buildQuery(entry) {
  if (entry.advanced_search_query) return entry.advanced_search_query;
  return entry.query_terms.map((term) => `(${term.query})`).join(" OR ");
}

function parseResultRange(value) {
  const text = compact(value);
  const range = text.match(
    /Search Results\s+([\d,]+)-([\d,]+) of ([\d,]+)/i,
  );
  if (range) {
    return {
      start: Number(range[1].replaceAll(",", "")),
      end: Number(range[2].replaceAll(",", "")),
      total: Number(range[3].replaceAll(",", "")),
      header: range[0],
    };
  }
  if (
    /no results found|no search results|0 results|did not match any documents/i.test(
      text,
    )
  ) {
    return { start: 0, end: 0, total: 0, header: "Search Results 0 of 0" };
  }
  throw new Error(
    `Could not parse Congress.gov result range from: ${text.slice(0, 700)}`,
  );
}

async function strictMainText(page) {
  return selectStrictMainText(
    await page.locator("#main:visible").allInnerTexts(),
    parseResultRange,
  );
}

async function waitForSettledResultSummary({
  page,
  requestedSearchUrl,
  entry,
  contract,
}) {
  const deadline = Date.now() + RESULT_SUMMARY_WAIT_MS;
  let lastMainText = "";
  let pollCount = 0;
  while (Date.now() < deadline) {
    pollCount += 1;
    const title = await page.title().catch(() => "");
    const bodyText = await page
      .locator("body")
      .innerText()
      .catch(() => "");
    const alternate = classifySingleResultRedirect({
      requestedUrl: requestedSearchUrl,
      loadedUrl: page.url(),
      title,
      bodyText,
      query: buildQuery(entry),
      congresses: contract.congresses,
      legislationTypes: contract.legislation_types,
    });
    if (alternate) {
      return {
        kind: "single_result_redirect",
        navigation: alternate,
        poll_count: pollCount,
      };
    }
    lastMainText = await strictMainText(page);
    if (!resultSummaryIsInProgress(lastMainText)) {
      return {
        kind: "result_list",
        range: parseResultRange(lastMainText),
        poll_count: pollCount,
      };
    }
    await sleep(RESULT_SUMMARY_POLL_MS);
  }
  throw new Error(
    "Congress.gov result summary remained in the non-result " +
      `"A summary is in progress." state for ${RESULT_SUMMARY_WAIT_MS}ms; ` +
      `no result was accepted from: ${compact(lastMainText).slice(0, 700)}`,
  );
}

function parseCanonicalUrl(rawUrl) {
  const url = new URL(rawUrl);
  const match = url.pathname.match(
    /\/(?:index\.php\/)?(bill|amendment)\/(\d+)(?:st|nd|rd|th)-congress\/([^/]+)\/(\d+)\/?$/i,
  );
  if (!match) {
    throw new Error(`Unrecognized Congress.gov legislation URL: ${rawUrl}`);
  }
  const [, recordKind, congressText, typeSlug, numberText] = match;
  const type = TYPE_SLUG_TO_CODE[typeSlug.toLowerCase()];
  if (!type) throw new Error(`Unrecognized Congress.gov type slug: ${typeSlug}`);
  const congress = Number(congressText);
  const number = Number(numberText);
  return {
    record_kind: recordKind.toLowerCase(),
    congress,
    type,
    number,
    canonical_key: `${congress}:${type}:${number}`,
    canonical_url: `https://www.congress.gov/${recordKind.toLowerCase()}/${congress}th-congress/${typeSlug.toLowerCase()}/${number}`,
  };
}

function summarizeCounts(records, field) {
  const counts = {};
  for (const record of records) {
    const key = String(record[field]);
    counts[key] = (counts[key] ?? 0) + 1;
  }
  return Object.fromEntries(
    Object.entries(counts).sort(([left], [right]) =>
      left.localeCompare(right, undefined, { numeric: true }),
    ),
  );
}

async function readJson(file) {
  return JSON.parse(await fs.readFile(file, "utf8"));
}

async function writeTextWithRetry(file, textValue, attempts = 30) {
  await writeTextAtomicWithRetry(file, textValue, { attempts });
}

async function writeJson(file, value) {
  await writeJsonAtomicWithRetry(file, value);
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
  throw new Error(`Could not remove checkpoint after ${attempts} attempts: ${file}`, {
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
  const lockFile = path.join(outDir, "collector.lock.json");
  await fs.mkdir(outDir, { recursive: true });
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      const handle = await fs.open(lockFile, "wx");
      await handle.writeFile(
        `${JSON.stringify(
          {
            schema: "congress-advanced-collector-lock-v1",
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
        // An unreadable lock is not safe to overwrite automatically.
      }
      if (!prior || processIsAlive(Number(prior.pid))) {
        throw new Error(
          `Collector output is locked by a live or unreadable run: ${lockFile} ` +
            `(pid=${prior?.pid ?? "unknown"})`,
        );
      }
      await removeWithRetry(lockFile);
      if (attempt < 3) await sleep(500 * attempt);
    }
  }
  throw new Error(`Could not acquire collector output lock: ${lockFile}`);
}

async function releaseRunLock(lock) {
  if (!lock) return;
  await lock.handle.close().catch(() => {});
  await removeWithRetry(lock.lockFile);
}

async function readAutomaticVerificationState(stateFile) {
  try {
    return validateCooldownState(await readJson(stateFile));
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

async function honorAutomaticVerificationCooldown(stateFile) {
  const state = await readAutomaticVerificationState(stateFile);
  if (!state) return null;
  const waitMs = remainingCooldownMs(state);
  if (waitMs > 0) {
    console.log(
      "AUTOMATIC VERIFICATION COOLDOWN: " +
        `${state.consecutive_timeouts} consecutive timeout(s); ` +
        `waiting ${Math.ceil(waitMs / 1000)}s until ` +
        `${state.next_retry_not_before}; no verification interaction or bypass`,
    );
    await sleep(waitMs);
  }
  return state;
}

async function waitForAutomaticVerification(
  page,
  expectedSelector,
  alternateClassifier = null,
) {
  const deadline = Date.now() + AUTOMATIC_VERIFICATION_WAIT_MS;
  let automaticVerification = null;
  while (Date.now() < deadline) {
    if (await page.locator(expectedSelector).count()) {
      return { kind: "expected_selector", selector: expectedSelector };
    }
    const title = await page.title().catch(() => "");
    const body = await page
      .locator("body")
      .innerText()
      .then((value) => value.slice(0, 1200))
      .catch(() => "");
    if (alternateClassifier) {
      const alternate = alternateClassifier({
        loadedUrl: page.url(),
        title,
        bodyText: body,
      });
      if (alternate) return alternate;
    }
    automaticVerification =
      classifyAutomaticVerification({
        loadedUrl: page.url(),
        title,
        bodyText: body,
      }) ?? automaticVerification;
    if (
      /captcha|verify you are human|human verification/i.test(`${title} ${body}`) &&
      !/verifying you are human|verification successful/i.test(`${title} ${body}`)
    ) {
      throw new Error(
        "Congress.gov presented an interactive human-verification challenge; " +
          "collection stopped rather than bypassing it.",
      );
    }
    await sleep(2000);
  }
  const title = await page.title().catch(() => "<unavailable>");
  if (automaticVerification) {
    throw new CongressAutomaticVerificationTimeoutError(
      {
        ...automaticVerification,
        loaded_url: page.url(),
        page_title: title,
      },
      AUTOMATIC_VERIFICATION_WAIT_MS,
    );
  }
  throw new Error(
    `Congress.gov did not expose ${expectedSelector} after automatic verification; ` +
      `title=${JSON.stringify(title)} url=${page.url()}`,
  );
}

async function gotoWithRetry(
  page,
  url,
  expectedSelector,
  attempts = 3,
  alternateClassifier = null,
) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      await page.goto(url, {
        waitUntil: "domcontentloaded",
        timeout: 90_000,
      });
      return await waitForAutomaticVerification(
        page,
        expectedSelector,
        alternateClassifier,
      );
    } catch (error) {
      lastError = error;
      if (/interactive human-verification/i.test(error.message)) throw error;
      if (automaticVerificationError(error)) throw error;
      if (attempt < attempts) await sleep(4000 * attempt);
    }
  }
  throw new Error(`Congress.gov navigation failed after ${attempts} attempts`, {
    cause: lastError,
  });
}

async function configureAndSearch(page, entry, contract) {
  const searchUrl = new URL(ADVANCED_SEARCH);
  [...contract.congresses]
    .sort((left, right) => right - left)
    .forEach((congress, index) => {
      searchUrl.searchParams.append(`congresses[${index}]`, String(congress));
    });
  searchUrl.searchParams.set("legislationNumbers", "");
  searchUrl.searchParams.set("restrictionType", "includeBillText");
  searchUrl.searchParams.append("restrictionFields[0]", "allBillTitles");
  searchUrl.searchParams.append("restrictionFields[1]", "summary");
  searchUrl.searchParams.set("summaryField", "billSummary");
  searchUrl.searchParams.set("enterTerms", buildQuery(entry));
  searchUrl.searchParams.set("wordVariants", "true");
  contract.legislation_types.forEach((type, index) => {
    if (!Object.hasOwn(LEGISLATION_TYPE_IDS, type)) {
      throw new Error(`Unsupported legislation type in contract: ${type}`);
    }
    searchUrl.searchParams.append(`legislationTypes[${index}]`, type);
  });
  searchUrl.searchParams.set("public", "true");
  searchUrl.searchParams.set("private", "true");
  searchUrl.searchParams.set("actionTerms", "");
  searchUrl.searchParams.set("legislativeActionWordVariants", "true");
  searchUrl.searchParams.set("actionDateBoxCount", "2");
  searchUrl.searchParams.set("actionText", "");
  searchUrl.searchParams.set("actionText2", "");
  searchUrl.searchParams.set("dateOfActionToggle", "multi");
  searchUrl.searchParams.set("legislativeAction", "Any");
  searchUrl.searchParams.set("sponsorState", "One");
  searchUrl.searchParams.set("member", "");
  searchUrl.searchParams.append("sponsorTypes[0]", "sponsor");
  searchUrl.searchParams.set("sponsorTypeBool", "OR");
  searchUrl.searchParams.set("dateOfSponsorshipOperator", "equal");
  searchUrl.searchParams.set("dateOfSponsorshipStartDate", "");
  searchUrl.searchParams.set("dateOfSponsorshipEndDate", "");
  searchUrl.searchParams.set("dateOfSponsorshipIsOptions", "yesterday");
  [0, 3, 11, 23, 12, 4, 2, 5, 9].forEach((activity, index) => {
    searchUrl.searchParams.append(
      `committeeActivity[${index}]`,
      String(activity),
    );
  });
  searchUrl.searchParams.set("satellite", "[]");
  searchUrl.searchParams.set("search", "");
  searchUrl.searchParams.set("submitted", "Submitted");
  const requestedSearchUrl = searchUrl.href;
  const navigation = await gotoWithRetry(
    page,
    requestedSearchUrl,
    "#main",
    3,
    ({ loadedUrl, title, bodyText }) =>
      classifySingleResultRedirect({
        requestedUrl: requestedSearchUrl,
        loadedUrl,
        title,
        bodyText,
        query: buildQuery(entry),
        congresses: contract.congresses,
        legislationTypes: contract.legislation_types,
      }),
  );
  if (navigation.kind === "single_result_redirect") {
    const extracted = await extractSingleResultRedirect(page, navigation);
    return {
      search_url: requestedSearchUrl,
      resolved_search_url: page.url(),
      total: 1,
      result_transport: "single_result_redirect",
      single_result_redirect: {
        navigation,
        extracted,
      },
    };
  }
  const settled = await waitForSettledResultSummary({
    page,
    requestedSearchUrl,
    entry,
    contract,
  });
  if (settled.kind === "single_result_redirect") {
    const extracted = await extractSingleResultRedirect(
      page,
      settled.navigation,
    );
    return {
      search_url: requestedSearchUrl,
      resolved_search_url: page.url(),
      total: 1,
      result_transport: "single_result_redirect",
      single_result_redirect: {
        navigation: settled.navigation,
        extracted,
      },
    };
  }
  return {
    search_url: page.url(),
    resolved_search_url: page.url(),
    total: settled.range.total,
    result_transport: "result_list",
    single_result_redirect: null,
  };
}

async function extractSingleResultRedirect(page, navigation) {
  const snapshot = await page.evaluate(() => {
    const clean = (value) =>
      String(value ?? "").replace(/\s+/g, " ").trim();
    const bodyText = document.body?.innerText ?? "";
    const sponsorLabel = [...document.querySelectorAll("th,dt,strong,b,span")]
      .find((node) => /^Sponsor:\s*$/i.test(clean(node.textContent)));
    let sponsorContainer = sponsorLabel?.parentElement ?? null;
    for (
      let depth = 0;
      sponsorContainer && depth < 5;
      depth += 1, sponsorContainer = sponsorContainer.parentElement
    ) {
      if (sponsorContainer.querySelector('a[href*="/member/"]')) break;
    }
    const sponsorLink =
      sponsorContainer?.querySelector('a[href*="/member/"]') ??
      document.querySelector('a[href*="/member/"]');
    return {
      title: document.title,
      body_text: bodyText,
      sponsor_url: sponsorLink?.href ?? null,
      loaded_url: location.href,
    };
  });
  const extracted = parseSingleResultPageSnapshot({
    loadedUrl: snapshot.loaded_url,
    title: snapshot.title,
    bodyText: snapshot.body_text,
    sponsorUrl: snapshot.sponsor_url,
  });
  const expectedDisplay = displayNumberForCanonical(navigation.canonical);
  const canonical = parseCanonicalUrl(extracted.result_url);
  const validation = {
    canonical_key_matches_redirect:
      canonical.canonical_key === navigation.canonical.canonical_key,
    display_number_matches_redirect:
      compact(extracted.display_number).replace(/[^a-z0-9]/gi, "").toLowerCase() ===
      expectedDisplay.replace(/[^a-z0-9]/gi, "").toLowerCase(),
    congress_term_matches_redirect: new RegExp(
      `^${canonical.congress}(?:st|nd|rd|th) Congress\\s+\\([^)]+\\)$`,
      "i",
    ).test(extracted.congress_term ?? ""),
    title_present: Boolean(compact(extracted.title)),
    sponsor_line_present: Boolean(compact(extracted.sponsor_line)),
    initial_action_date_present: Boolean(extracted.initial_action_date),
  };
  if (Object.values(validation).some((value) => value !== true)) {
    throw new Error(
      "Congress.gov single-result redirect record extraction failed: " +
        JSON.stringify({
          canonical_key: navigation.canonical.canonical_key,
          validation,
          extracted,
        }),
    );
  }
  return {
    ...extracted,
    heading: `${expectedDisplay} — ${extracted.congress_term}`,
    extraction_validation: validation,
  };
}

async function extractPage(page) {
  return page.evaluate(() => {
    const clean = (value) =>
      String(value ?? "").replace(/\s+/g, " ").trim();
    const mainText = document.querySelector("#main")?.innerText ?? "";
    const match = clean(mainText).match(
      /Search Results\s+([\d,]+)-([\d,]+) of ([\d,]+)/i,
    );
    if (!match) {
      throw new Error(
        `Could not parse result range: ${clean(mainText).slice(0, 700)}`,
      );
    }
    const cards = [
      ...document.querySelectorAll(
        "ol.basic-search-results-lists > li.expanded",
      ),
    ];
    const records = cards.map((card) => {
      const heading = card.querySelector(".result-heading");
      const resultLink = heading?.querySelector("a");
      const itemTexts = [...card.querySelectorAll(":scope > .result-item")].map(
        (node) => clean(node.textContent),
      );
      const itemWithPrefix = (prefix) =>
        itemTexts.find((value) =>
          value.toLowerCase().startsWith(prefix.toLowerCase()),
        ) ?? null;
      const sponsorLine = itemWithPrefix("Sponsor:");
      const sponsorItem = [...card.querySelectorAll(":scope > .result-item")].find(
        (node) => clean(node.textContent).toLowerCase().startsWith("sponsor:"),
      );
      const sponsorLink = sponsorItem?.querySelector('a[href*="/member/"]');
      const actionDateMatch = sponsorLine?.match(
        /\((Introduced|Submitted|Offered|Proposed)\s+(\d{2}\/\d{2}\/\d{4})\)/i,
      );
      const latestActionLine = itemWithPrefix("Latest Action:");
      const latestActionDateMatch = latestActionLine?.match(
        /Latest Action:\s*.*?\s-\s(\d{2}\/\d{2}\/\d{4})\s/i,
      );
      const trackerText =
        card.querySelector(".result-tracker .hide_fromsighted")?.textContent ??
        "";
      return {
        indicator: clean(card.querySelector(".visualIndicator")?.textContent),
        heading: clean(heading?.textContent),
        display_number: clean(resultLink?.textContent),
        result_url: resultLink?.href ?? null,
        title: clean(card.querySelector(".result-title")?.textContent) || null,
        purpose: itemWithPrefix("Purpose:"),
        sponsor_line: sponsorLine,
        sponsor:
          sponsorLine
            ?.replace(/^Sponsor:\s*/i, "")
            .replace(/\s+\((?:Introduced|Submitted|Offered|Proposed)\s+\d{2}\/\d{2}\/\d{4}\).*$/i, "")
            .trim() || null,
        sponsor_url: sponsorLink?.href ?? null,
        initial_action: actionDateMatch?.[1] ?? null,
        initial_action_date: actionDateMatch?.[2] ?? null,
        cosponsor_count: Number(
          sponsorLine?.match(/Cosponsors:\s*\((\d+)\)/i)?.[1] ?? 0,
        ),
        committees: itemWithPrefix("Committees:"),
        latest_action: latestActionLine,
        latest_action_date: latestActionDateMatch?.[1] ?? null,
        amends: itemWithPrefix("Amends "),
        tracker_status:
          clean(trackerText).replace(/^This bill has the status\s*/i, "") ||
          null,
        raw_text: clean(card.innerText),
      };
    });
    return {
      range: {
        start: Number(match[1].replaceAll(",", "")),
        end: Number(match[2].replaceAll(",", "")),
        total: Number(match[3].replaceAll(",", "")),
        header: match[0],
      },
      loaded_url: location.href,
      page_size:
        document.querySelector(".pageSizeSelect")?.value ?? null,
      page_sort: document.querySelector("#pageSort")?.value ?? null,
      current_page:
        document.querySelector("#currentPageNumber")?.value ?? null,
      records,
    };
  });
}

function normalizeDate(value) {
  const match = String(value ?? "").match(
    /^(\d{2})\/(\d{2})\/(\d{4})$/,
  );
  return match ? `${match[3]}-${match[1]}-${match[2]}` : null;
}

function windowStatus(value, contract) {
  const isoDate = normalizeDate(value);
  if (!isoDate) return "date_unknown";
  if (isoDate < contract.required_window_start) {
    return "boundary_extension_before_window";
  }
  if (isoDate > contract.required_window_end) {
    return "after_required_window";
  }
  return "within_required_window";
}

function normalizePageRecords(rawRecords, entry, pageNumber, contract) {
  return rawRecords.map((record, index) => {
    if (!record.result_url) {
      throw new Error(
        `Missing result URL for ${entry.id} page ${pageNumber} row ${index + 1}`,
      );
    }
    const canonical = parseCanonicalUrl(record.result_url);
    const headingCongress = Number(
      record.heading.match(
        /(?:—|-)\s*(\d+)(?:st|nd|rd|th) Congress/i,
      )?.[1],
    );
    if (headingCongress !== canonical.congress) {
      throw new Error(
        `Heading/URL Congress mismatch for ${record.result_url}: ` +
          `${headingCongress} vs ${canonical.congress}`,
      );
    }
    return {
      ...canonical,
      display_number: record.display_number,
      heading: record.heading,
      title: record.title,
      purpose: record.purpose,
      sponsor: record.sponsor,
      sponsor_url: record.sponsor_url,
      sponsor_line: record.sponsor_line,
      initial_action: record.initial_action,
      initial_action_date: record.initial_action_date,
      initial_action_date_iso: normalizeDate(record.initial_action_date),
      window_status: windowStatus(record.initial_action_date, contract),
      candidate_status: "included_unreviewed_candidate",
      cosponsor_count: record.cosponsor_count,
      committees: record.committees,
      latest_action: record.latest_action,
      latest_action_date: record.latest_action_date,
      amends: record.amends,
      tracker_status: record.tracker_status,
      result_url: record.result_url,
      raw_text: record.raw_text,
      source_query_id: entry.id,
      source_page: pageNumber,
      source_rank: (pageNumber - 1) * PAGE_SIZE + index + 1,
    };
  });
}

async function collectEntry({
  page,
  entry,
  contract,
  outDir,
  registryHash,
  delayMs,
  resume,
}) {
  const query = buildQuery(entry);
  const queryHash = stableHash({
    query,
    contract,
    page_size: PAGE_SIZE,
    page_sort: PAGE_SORT,
  });
  const queryDir = path.join(outDir, "raw", "pages", entry.id);
  await fs.mkdir(queryDir, { recursive: true });

  const search = await configureAndSearch(page, entry, contract);
  const expectedPageCount = Math.ceil(search.total / PAGE_SIZE);
  console.log(
    `QUERY ${entry.id}: ${search.total} result(s), ${expectedPageCount} page(s)`,
  );
  const pages = [];
  const records = [];

  for (let pageNumber = 1; pageNumber <= expectedPageCount; pageNumber += 1) {
    const pageFile = path.join(
      queryDir,
      `page_${String(pageNumber).padStart(3, "0")}.json`,
    );
    let pageArtifact = null;
    if (resume) {
      try {
        const prior = await readJson(pageFile);
        if (
          prior.advanced_search_query === query &&
          prior.result_total === search.total &&
          prior.page_number === pageNumber &&
          prior.validation?.exact_page_count === true &&
          (prior.result_transport ?? "result_list") ===
            search.result_transport &&
          (
            search.result_transport !== "single_result_redirect" ||
            (
              Object.values(
                prior.single_result_redirect?.proof ?? {},
              ).every((value) => value === true) &&
              Object.keys(
                prior.single_result_redirect?.proof ?? {},
              ).length > 0 &&
              Object.values(
                prior.single_result_redirect?.extraction_validation ?? {},
              ).every((value) => value === true) &&
              Object.keys(
                prior.single_result_redirect?.extraction_validation ?? {},
              ).length > 0
            )
          )
        ) {
          pageArtifact = prior;
          console.log(
            `  RESUME page ${pageNumber}/${expectedPageCount}: ` +
              `${prior.records.length} record(s)`,
          );
        }
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
    }

    if (!pageArtifact) {
      if (delayMs) await sleep(delayMs);
      let requestedPageUrl;
      let extracted;
      if (search.result_transport === "single_result_redirect") {
        if (pageNumber !== 1 || expectedPageCount !== 1 || search.total !== 1) {
          throw new Error(
            `Invalid singleton redirect pagination for ${entry.id}: ` +
              JSON.stringify({
                pageNumber,
                expectedPageCount,
                resultTotal: search.total,
              }),
          );
        }
        requestedPageUrl = search.search_url;
        extracted = {
          range: {
            start: 1,
            end: 1,
            total: 1,
            header: "Congress.gov Advanced Search single-result redirect 1 of 1",
          },
          loaded_url: search.resolved_search_url,
          page_size: null,
          page_sort: null,
          current_page: null,
          records: [search.single_result_redirect.extracted],
        };
      } else {
        const pageUrl = new URL(search.search_url);
        pageUrl.searchParams.set("pageSize", String(PAGE_SIZE));
        pageUrl.searchParams.set("pageSort", PAGE_SORT);
        pageUrl.searchParams.set("page", String(pageNumber));
        requestedPageUrl = pageUrl.href;
        await gotoWithRetry(
          page,
          requestedPageUrl,
          "ol.basic-search-results-lists",
        );
        extracted = await extractPage(page);
      }
      const expectedStart = (pageNumber - 1) * PAGE_SIZE + 1;
      const expectedEnd = Math.min(pageNumber * PAGE_SIZE, search.total);
      const expectedCount = expectedEnd - expectedStart + 1;
      const normalized = normalizePageRecords(
        extracted.records,
        entry,
        pageNumber,
        contract,
      );
      const uniqueKeys = new Set(
        normalized.map((record) => record.canonical_key),
      );
      const validation = {
        total_matches_query: extracted.range.total === search.total,
        range_start_matches:
          extracted.range.start === expectedStart,
        range_end_matches: extracted.range.end === expectedEnd,
        exact_page_count: normalized.length === expectedCount,
        unique_within_page: uniqueKeys.size === normalized.length,
        page_size_matches:
          extracted.page_size === String(PAGE_SIZE) ||
          (
            expectedPageCount === 1 &&
            extracted.page_size === null &&
            normalized.length === search.total &&
            search.total <= PAGE_SIZE
          ),
        page_sort_matches:
          extracted.page_sort === PAGE_SORT ||
          (
            search.result_transport === "single_result_redirect" &&
            search.total === 1 &&
            extracted.page_sort === null
          ),
        single_result_order_trivial:
          search.result_transport !== "single_result_redirect" ||
          (
            search.total === 1 &&
            extracted.page_sort === null
          ),
        current_page_matches:
          extracted.current_page === String(pageNumber) ||
          (expectedPageCount === 1 && extracted.current_page === null),
      };
      if (Object.values(validation).some((value) => value !== true)) {
        throw new Error(
          `Page validation failed for ${entry.id} page ${pageNumber}: ` +
            JSON.stringify({
              validation,
              expected: {
                start: expectedStart,
                end: expectedEnd,
                count: expectedCount,
                total: search.total,
              },
              actual: {
                range: extracted.range,
                count: normalized.length,
                page_size: extracted.page_size,
                page_sort: extracted.page_sort,
                current_page: extracted.current_page,
              },
            }),
        );
      }
      pageArtifact = {
        schema: "congress-advanced-search-page-v1",
        collected_at: isoNow(),
        primary_instrument: "Congress.gov Advanced Search - Legislation",
        registry_sha256: registryHash,
        query_id: entry.id,
        query_sha256: queryHash,
        advanced_search_query: query,
        search_url: search.search_url,
        resolved_search_url: search.resolved_search_url,
        result_transport: search.result_transport,
        single_result_redirect:
          search.result_transport === "single_result_redirect"
            ? {
                proof: search.single_result_redirect.navigation.proof,
                canonical:
                  search.single_result_redirect.navigation.canonical,
                highlighted_query:
                  search.single_result_redirect.navigation.highlighted_query,
                result_rank:
                  search.single_result_redirect.navigation.result_rank,
                extraction_validation:
                  search.single_result_redirect.extracted.extraction_validation,
              }
            : null,
        requested_url: requestedPageUrl,
        loaded_url: extracted.loaded_url,
        page_number: pageNumber,
        page_size: PAGE_SIZE,
        page_sort: PAGE_SORT,
        result_range: extracted.range,
        result_total: search.total,
        records_sha256: stableHash(normalized),
        validation,
        records: normalized,
      };
      await writeJson(pageFile, pageArtifact);
      console.log(
        `  PAGE ${pageNumber}/${expectedPageCount}: ` +
          `${normalized.length} record(s)`,
      );
    }
    pages.push({
      file: path
        .relative(outDir, pageFile)
        .split(path.sep)
        .join("/"),
      page_number: pageArtifact.page_number,
      result_range: pageArtifact.result_range,
      record_count: pageArtifact.records.length,
      records_sha256: pageArtifact.records_sha256,
    });
    records.push(...pageArtifact.records);
  }

  const uniqueKeys = new Set(records.map((record) => record.canonical_key));
  if (records.length !== search.total || uniqueKeys.size !== search.total) {
    throw new Error(
      `Exact query reconciliation failed for ${entry.id}: site=${search.total}, ` +
        `rows=${records.length}, unique=${uniqueKeys.size}`,
    );
  }

  const queryArtifact = {
    schema: "congress-advanced-search-query-v1",
    completed_at: isoNow(),
    primary_instrument: "Congress.gov Advanced Search - Legislation",
    query_id: entry.id,
    label: entry.label,
    scoring_category: entry.scoring_category,
    record_kind: entry.record_kind ?? "category",
    parent_category_id: entry.parent_category_id ?? null,
    parent_category_label: entry.parent_category_label ?? null,
    identifier_text: entry.identifier_text ?? null,
    title_alias: entry.title_alias ?? null,
    ambiguous_short_act_alias: entry.ambiguous_short_act_alias ?? false,
    membership_non_dispositive:
      entry.membership_non_dispositive ?? false,
    source_paragraph: entry.source_paragraph ?? null,
    registry_sha256: registryHash,
    query_sha256: queryHash,
    advanced_search_query: query,
    search_url: search.search_url,
    resolved_search_url: search.resolved_search_url,
    result_transport: search.result_transport,
    single_result_redirect:
      search.result_transport === "single_result_redirect"
        ? {
            proof: search.single_result_redirect.navigation.proof,
            canonical: search.single_result_redirect.navigation.canonical,
            highlighted_query:
              search.single_result_redirect.navigation.highlighted_query,
            result_rank: search.single_result_redirect.navigation.result_rank,
            extraction_validation:
              search.single_result_redirect.extracted.extraction_validation,
          }
        : null,
    result_total: search.total,
    zero_result_surface_validation:
      search.total === 0 ? ZERO_RESULT_SURFACE_VALIDATION : null,
    page_count: expectedPageCount,
    unique_record_count: uniqueKeys.size,
    counts_by_congress: summarizeCounts(records, "congress"),
    counts_by_type: summarizeCounts(records, "type"),
    pages,
    records_sha256: stableHash(records),
    validation: {
      exact_result_total: records.length === search.total,
      unique_result_total: uniqueKeys.size === search.total,
      every_page_exact: pages.every(
        (item) => item.record_count === item.result_range.end - item.result_range.start + 1,
      ),
      single_result_redirect_proven:
        search.result_transport !== "single_result_redirect" ||
        (
          search.total === 1 &&
          Object.values(
            search.single_result_redirect.navigation.proof,
          ).every((value) => value === true) &&
          Object.values(
            search.single_result_redirect.extracted.extraction_validation,
          ).every((value) => value === true)
        ),
    },
    records,
  };
  const queryFile = path.join(outDir, "queries", `${entry.id}.json`);
  await writeJson(queryFile, queryArtifact);
  return {
    query_file: path
      .relative(outDir, queryFile)
      .split(path.sep)
      .join("/"),
    artifact: queryArtifact,
  };
}

async function loadCompletedEntry({
  entry,
  contract,
  outDir,
  registryHash,
}) {
  const queryFile = path.join(outDir, "queries", `${entry.id}.json`);
  let artifact;
  try {
    artifact = await readJson(queryFile);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
  const query = buildQuery(entry);
  const queryHash = stableHash({
    query,
    contract,
    page_size: PAGE_SIZE,
    page_sort: PAGE_SORT,
  });
  const keys = new Set(
    (artifact.records ?? []).map((record) => record.canonical_key),
  );
  const valid =
    artifact.schema === "congress-advanced-search-query-v1" &&
    artifact.query_id === entry.id &&
    artifact.advanced_search_query === query &&
    artifact.validation?.exact_result_total === true &&
    artifact.validation?.unique_result_total === true &&
    artifact.validation?.every_page_exact === true &&
    (
      artifact.result_total !== 0 ||
      artifact.zero_result_surface_validation ===
        ZERO_RESULT_SURFACE_VALIDATION
    ) &&
    (
      artifact.result_transport !== "single_result_redirect" ||
      artifact.validation?.single_result_redirect_proven === true
    ) &&
    artifact.records?.length === artifact.result_total &&
    keys.size === artifact.result_total;
  if (!valid) return null;
  const priorRegistryHash = artifact.registry_sha256;
  const desiredMetadata = {
    registry_sha256: registryHash,
    query_sha256: queryHash,
    label: entry.label,
    scoring_category: entry.scoring_category,
    record_kind: entry.record_kind ?? "category",
    parent_category_id: entry.parent_category_id ?? null,
    parent_category_label: entry.parent_category_label ?? null,
    identifier_text: entry.identifier_text ?? null,
    title_alias: entry.title_alias ?? null,
    ambiguous_short_act_alias: entry.ambiguous_short_act_alias ?? false,
    membership_non_dispositive: entry.membership_non_dispositive ?? false,
  };
  const metadataCurrent = Object.entries(desiredMetadata).every(
    ([key, value]) => artifact[key] === value,
  );
  const priorReuse = artifact.equivalent_query_reuse;
  const reuseCurrent =
    priorReuse?.basis ===
      "exact_advanced_search_query_and_complete_reconciled_query_artifact" &&
    priorReuse?.current_registry_sha256 === registryHash &&
    priorReuse?.raw_page_artifacts_may_retain_prior_equivalent_registry_hash ===
      true;
  Object.assign(artifact, desiredMetadata);
  if (!metadataCurrent || !reuseCurrent) {
    artifact.equivalent_query_reuse = {
      revalidated_at: isoNow(),
      basis:
        "exact_advanced_search_query_and_complete_reconciled_query_artifact",
      prior_registry_sha256: priorRegistryHash,
      current_registry_sha256: registryHash,
      raw_page_artifacts_may_retain_prior_equivalent_registry_hash: true,
    };
    await writeJson(queryFile, artifact);
  }
  console.log(
    `RESUME QUERY ${entry.id}: ${artifact.result_total} exact record(s), ` +
      `${artifact.page_count} page(s)`,
  );
  return {
    query_file: path
      .relative(outDir, queryFile)
      .split(path.sep)
      .join("/"),
    artifact,
  };
}

function buildUnion(queryResults) {
  const recordMap = new Map();
  const memberships = [];
  for (const { artifact } of queryResults) {
    for (const sourceRecord of artifact.records) {
      const membership = {
        query_id: artifact.query_id,
        label: artifact.label,
        scoring_category: artifact.scoring_category,
        record_kind: artifact.record_kind,
        parent_category_id: artifact.parent_category_id,
        parent_category_label: artifact.parent_category_label,
        identifier_text: artifact.identifier_text,
        title_alias: artifact.title_alias,
        ambiguous_short_act_alias: artifact.ambiguous_short_act_alias,
        membership_non_dispositive: artifact.membership_non_dispositive,
        source_paragraph: artifact.source_paragraph,
        canonical_key: sourceRecord.canonical_key,
        source_page: sourceRecord.source_page,
        source_rank: sourceRecord.source_rank,
        result_url: sourceRecord.result_url,
      };
      memberships.push(membership);
      if (!recordMap.has(sourceRecord.canonical_key)) {
        recordMap.set(sourceRecord.canonical_key, {
          ...sourceRecord,
          source_query_id: undefined,
          source_page: undefined,
          source_rank: undefined,
          matched_scoring_categories: [],
          matched_monitor_categories: [],
          matched_coverage_probes: [],
        });
      }
      const unionRecord = recordMap.get(sourceRecord.canonical_key);
      if (artifact.record_kind === "coverage_probe") {
        unionRecord.matched_coverage_probes.push(artifact.query_id);
      } else if (artifact.scoring_category) {
        unionRecord.matched_scoring_categories.push(artifact.query_id);
      } else {
        unionRecord.matched_monitor_categories.push(artifact.query_id);
      }
    }
  }
  const records = [...recordMap.values()]
    .map((record) => ({
      ...record,
      matched_scoring_categories: [
        ...new Set(record.matched_scoring_categories),
      ].sort(),
      matched_monitor_categories: [
        ...new Set(record.matched_monitor_categories),
      ].sort(),
      matched_coverage_probes: [
        ...new Set(record.matched_coverage_probes),
      ].sort(),
    }))
    .sort((left, right) =>
      left.canonical_key.localeCompare(right.canonical_key, undefined, {
        numeric: true,
      }),
    );
  memberships.sort((left, right) =>
    `${left.query_id}:${left.canonical_key}`.localeCompare(
      `${right.query_id}:${right.canonical_key}`,
      undefined,
      { numeric: true },
    ),
  );
  return { records, memberships };
}

function buildCoverageVisibility(records) {
  const gaps = [];
  for (const record of records) {
    if (
      record.matched_coverage_probes.length &&
      !record.matched_scoring_categories.length &&
      !record.matched_monitor_categories.length
    ) {
      gaps.push({
        gap_kind: "recall_only_unclassified",
        canonical_key: record.canonical_key,
        display_number: record.display_number,
        title: record.title,
        result_url: record.result_url,
        disposition: "included_unreviewed_candidate",
        explanation:
          "Recovered by one or more non-scoring recall probes but by no " +
          "outline category query. Retained for review; not excluded.",
      });
    }
    if (record.window_status === "boundary_extension_before_window") {
      gaps.push({
        gap_kind: "boundary_extension_included",
        canonical_key: record.canonical_key,
        display_number: record.display_number,
        title: record.title,
        result_url: record.result_url,
        disposition: "included_unreviewed_candidate",
        explanation:
          "Predates the exact six-year start but falls in the 116th Congress. " +
          "Retained under the boundary-extension rule.",
      });
    }
    if (record.window_status === "date_unknown") {
      gaps.push({
        gap_kind: "date_unknown_requires_review",
        canonical_key: record.canonical_key,
        display_number: record.display_number,
        title: record.title,
        result_url: record.result_url,
        disposition: "included_unreviewed_candidate",
        explanation:
          "Congress.gov result card did not expose a parseable initial-action " +
          "date. Retained rather than silently dropped.",
      });
    }
  }
  gaps.sort((left, right) =>
    `${left.gap_kind}:${left.canonical_key}`.localeCompare(
      `${right.gap_kind}:${right.canonical_key}`,
      undefined,
      { numeric: true },
    ),
  );
  return {
    gaps,
    exclusions: [],
    scope: {
      automated_semantic_exclusions: 0,
      included_congresses: [116, 117, 118, 119],
      exact_required_window: ["2020-07-25", "2026-07-25"],
      boundary_extension:
        "Pre-2020-07-25 records in the 116th Congress are included and flagged.",
      included_legislation_types: Object.keys(LEGISLATION_TYPE_IDS),
      included_public_private_scope: ["public", "private"],
      structurally_out_of_scope: [
        {
          collection: "Congress 115 and earlier",
          reason:
            "Outside the six-year collection contract; the complete 116th " +
            "Congress is retained to avoid a boundary gap.",
        },
        {
          collection: "Nominations, treaties, committee materials, and CRS products",
          reason:
            "Not legislation types in Congress.gov Advanced Search - Legislation.",
        },
      ],
      later_review_rule:
        "Any semantic exclusion must be appended to exclusion_ledger.ndjson " +
        "with reviewer, timestamp, disposition, and reason.",
    },
  };
}

async function writeNdjson(file, rows) {
  const body = rows.map((row) => JSON.stringify(row)).join("\n");
  await writeTextWithRetry(file, body ? `${body}\n` : "");
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const outDir = assertWithinRoot(args.out);
  const registryPath = path.resolve(ROOT, args.registry);
  const registryText = await fs.readFile(registryPath, "utf8");
  const registry = JSON.parse(registryText);
  const registryHash = stableHash(registryText);
  const entries = [...registry.categories, ...registry.coverage_probes].filter(
    (entry) => !args.categories || args.categories.has(entry.id),
  );
  if (!entries.length) throw new Error("No registry queries selected");
  if (args.categories) {
    const missing = [...args.categories].filter(
      (id) => !entries.some((entry) => entry.id === id),
    );
    if (missing.length) {
      throw new Error(`Unknown category/probe IDs: ${missing.join(", ")}`);
    }
  }

  await fs.mkdir(outDir, { recursive: true });
  const runLock = await acquireRunLock(outDir);
  const manifestFile = path.join(outDir, "manifest.json");
  const partialFile = path.join(
    outDir,
    `manifest.partial.${process.pid}.json`,
  );
  const automaticVerificationStateFile = path.join(
    outDir,
    AUTOMATIC_VERIFICATION_STATE_FILE,
  );
  const manifest = {
    schema: "acu-congress-advanced-pages-manifest-v1",
    status: "running",
    started_at: isoNow(),
    completed_at: null,
    primary_instrument: "Congress.gov Advanced Search - Legislation",
    quorum_used: false,
    registry_file: path
      .relative(ROOT, registryPath)
      .split(path.sep)
      .join("/"),
    registry_sha256: registryHash,
    collection_contract: registry.collection_contract,
    page_contract: {
      page_size: PAGE_SIZE,
      page_sort: PAGE_SORT,
      exact_reconciliation_required: true,
      discovery_transport: "Official Congress.gov result pages",
    },
    automatic_verification_policy: {
      state_file: AUTOMATIC_VERIFICATION_STATE_FILE,
      immutable_timeout_receipts:
        "raw/failure_debug/*_automatic_verification.json",
      automatic_wait_ms: AUTOMATIC_VERIFICATION_WAIT_MS,
      base_cooldown_ms: AUTOMATIC_VERIFICATION_BASE_COOLDOWN_MS,
      max_cooldown_ms: AUTOMATIC_VERIFICATION_MAX_COOLDOWN_MS,
      interaction_performed: false,
      captcha_bypass_attempted: false,
      result_validation_weakened: false,
    },
    selected_query_ids: entries.map((entry) => entry.id),
    queries: [],
  };
  await writeJson(partialFile, manifest);
  const honoredCooldown = await honorAutomaticVerificationCooldown(
    automaticVerificationStateFile,
  );
  if (honoredCooldown) {
    manifest.automatic_verification_policy.honored_prior_state = {
      observed_at: honoredCooldown.observed_at,
      consecutive_timeouts: honoredCooldown.consecutive_timeouts,
      next_retry_not_before: honoredCooldown.next_retry_not_before,
    };
    await writeJson(partialFile, manifest);
  }

  const context = await chromium.launchPersistentContext(args.profile, {
    executablePath: args.chrome,
    headless: args.headless,
    acceptDownloads: false,
    ignoreDefaultArgs: ["--enable-automation"],
    args: ["--disable-blink-features=AutomationControlled"],
    viewport: { width: 1400, height: 900 },
  });

  try {
    const page = context.pages()[0] || (await context.newPage());
    const queryResults = [];
    for (const entry of entries) {
      let collected = null;
      let liveCollectionPerformed = false;
      if (args.resume) {
        collected = await loadCompletedEntry({
          entry,
          contract: registry.collection_contract,
          outDir,
          registryHash,
        });
      }
      if (!collected) {
        liveCollectionPerformed = true;
        collected = await collectEntry({
          page,
          entry,
          contract: registry.collection_contract,
          outDir,
          registryHash,
          delayMs: args.delayMs,
          resume: args.resume,
        });
      }
      if (liveCollectionPerformed) {
        await removeWithRetry(automaticVerificationStateFile).catch(
          (error) => {
            if (error.code !== "ENOENT") throw error;
          },
        );
      }
      queryResults.push(collected);
      const artifact = collected.artifact;
      manifest.queries.push({
        query_id: artifact.query_id,
        label: artifact.label,
        scoring_category: artifact.scoring_category,
        record_kind: artifact.record_kind,
        parent_category_id: artifact.parent_category_id,
        parent_category_label: artifact.parent_category_label,
        identifier_text: artifact.identifier_text,
        title_alias: artifact.title_alias,
        ambiguous_short_act_alias: artifact.ambiguous_short_act_alias,
        membership_non_dispositive: artifact.membership_non_dispositive,
        query_file: collected.query_file,
        result_total: artifact.result_total,
        unique_record_count: artifact.unique_record_count,
        page_count: artifact.page_count,
        counts_by_congress: artifact.counts_by_congress,
        counts_by_type: artifact.counts_by_type,
        records_sha256: artifact.records_sha256,
        validation: artifact.validation,
      });
      if (
        liveCollectionPerformed ||
        manifest.queries.length === entries.length ||
        manifest.queries.length % 25 === 0
      ) {
        await writeJson(partialFile, manifest);
      }
    }

    const union = buildUnion(queryResults);
    const coverage = buildCoverageVisibility(union.records);
    const recordsFile = path.join(outDir, "congress_advanced_records.ndjson");
    const membershipsFile = path.join(
      outDir,
      "congress_advanced_memberships.ndjson",
    );
    const gapsFile = path.join(outDir, "coverage_gap_ledger.ndjson");
    const exclusionsFile = path.join(outDir, "exclusion_ledger.ndjson");
    const scopeFile = path.join(outDir, "scope_and_exclusion_contract.json");
    await writeNdjson(recordsFile, union.records);
    await writeNdjson(membershipsFile, union.memberships);
    await writeNdjson(gapsFile, coverage.gaps);
    await writeNdjson(exclusionsFile, coverage.exclusions);
    await writeJson(scopeFile, coverage.scope);
    manifest.status = "complete";
    manifest.completed_at = isoNow();
    manifest.union = {
      unique_record_count: union.records.length,
      membership_count: union.memberships.length,
      counts_by_congress: summarizeCounts(union.records, "congress"),
      counts_by_type: summarizeCounts(union.records, "type"),
      scoring_candidate_count: union.records.filter(
        (record) => record.matched_scoring_categories.length,
      ).length,
      monitor_candidate_count: union.records.filter(
        (record) => record.matched_monitor_categories.length,
      ).length,
      recall_probe_count: union.records.filter(
        (record) => record.matched_coverage_probes.length,
      ).length,
      records_file: path
        .relative(outDir, recordsFile)
        .split(path.sep)
        .join("/"),
      records_sha256: stableHash(union.records),
      memberships_file: path
        .relative(outDir, membershipsFile)
        .split(path.sep)
        .join("/"),
      memberships_sha256: stableHash(union.memberships),
      coverage_visibility: {
        recall_only_unclassified_count: coverage.gaps.filter(
          (item) => item.gap_kind === "recall_only_unclassified",
        ).length,
        boundary_extension_included_count: coverage.gaps.filter(
          (item) => item.gap_kind === "boundary_extension_included",
        ).length,
        date_unknown_requires_review_count: coverage.gaps.filter(
          (item) => item.gap_kind === "date_unknown_requires_review",
        ).length,
        automated_semantic_exclusion_count: coverage.exclusions.length,
        gap_ledger_file: path
          .relative(outDir, gapsFile)
          .split(path.sep)
          .join("/"),
        gap_ledger_sha256: stableHash(coverage.gaps),
        exclusion_ledger_file: path
          .relative(outDir, exclusionsFile)
          .split(path.sep)
          .join("/"),
        exclusion_ledger_sha256: stableHash(coverage.exclusions),
        scope_contract_file: path
          .relative(outDir, scopeFile)
          .split(path.sep)
          .join("/"),
      },
    };
    await writeJson(manifestFile, manifest);
    await removeWithRetry(partialFile);
    console.log(
      `COMPLETE: ${union.records.length} unique record(s), ` +
        `${union.memberships.length} query membership(s)`,
    );
  } catch (error) {
    const activePage = context.pages()[0];
    const failureDebugDir = path.join(outDir, "raw", "failure_debug");
    await fs.mkdir(failureDebugDir, { recursive: true }).catch(() => {});
    const failureStem = `collector_failure_${process.pid}_${Date.now()}`;
    const failureScreenshot = path.join(
      failureDebugDir,
      `${failureStem}.png`,
    );
    const failurePageState = path.join(
      failureDebugDir,
      `${failureStem}.json`,
    );
    if (activePage) {
      await activePage
        .screenshot({ path: failureScreenshot, fullPage: true })
        .catch(() => {});
      await writeJson(failurePageState, {
        captured_at: isoNow(),
        url: activePage.url(),
        title: await activePage.title().catch(() => null),
        visible_main_surfaces: await activePage
          .locator("#main:visible")
          .allInnerTexts()
          .catch(() => null),
        all_main_surfaces: await activePage
          .locator("#main")
          .allInnerTexts()
          .catch(() => null),
        body_excerpt: await activePage
          .locator("body")
          .innerText()
          .then((value) => value.slice(0, 5000))
          .catch(() => null),
      }).catch(() => {});
    }
    const automaticTimeout = automaticVerificationError(error);
    if (automaticTimeout) {
      const previousState = await readAutomaticVerificationState(
        automaticVerificationStateFile,
      );
      const cooldownState = buildCooldownState({
        previousState,
        classification: automaticTimeout.classification,
        baseCooldownMs: AUTOMATIC_VERIFICATION_BASE_COOLDOWN_MS,
        maxCooldownMs: AUTOMATIC_VERIFICATION_MAX_COOLDOWN_MS,
      });
      const immutableTimeoutReceipt = path.join(
        failureDebugDir,
        `${failureStem}_automatic_verification.json`,
      );
      await writeJson(immutableTimeoutReceipt, cooldownState);
      await writeJson(automaticVerificationStateFile, cooldownState);
      manifest.automatic_verification_timeout = {
        classification: "automatic_verification_timeout",
        receipt_file: path
          .relative(outDir, immutableTimeoutReceipt)
          .split(path.sep)
          .join("/"),
        receipt_sha256: stableHash(
          await fs.readFile(immutableTimeoutReceipt, "utf8"),
        ),
        state_file: AUTOMATIC_VERIFICATION_STATE_FILE,
        consecutive_timeouts: cooldownState.consecutive_timeouts,
        cooldown_ms: cooldownState.cooldown_ms,
        next_retry_not_before: cooldownState.next_retry_not_before,
        interaction_performed: false,
        captcha_bypass_attempted: false,
        collection_stopped_without_result_acceptance: true,
      };
    }
    manifest.status = "failed";
    manifest.failed_at = isoNow();
    manifest.error = {
      name: error.name,
      code: error.code ?? null,
      message: error.message,
      stack: error.stack,
      cause_name: error.cause?.name ?? null,
      cause_message: error.cause?.message ?? null,
      classification:
        automaticTimeout?.classification?.kind ?? "unclassified_failure",
      failure_screenshot: path
        .relative(outDir, failureScreenshot)
        .split(path.sep)
        .join("/"),
      failure_page_state: path
        .relative(outDir, failurePageState)
        .split(path.sep)
        .join("/"),
    };
    await writeJson(partialFile, manifest);
    throw error;
  } finally {
    await context.close();
    await releaseRunLock(runLock);
  }
}

await main();
