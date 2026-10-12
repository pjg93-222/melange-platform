#!/usr/bin/env node
/**
 * Refresh dashboard bill records from live Congress.gov pages.
 *
 * This is the bill-repository adapter for the same persistent, headless
 * Playwright/Chrome transport used by the legislation coverage harvesters.
 * It checkpoints after every bill, fails closed on interactive verification,
 * and never treats a partially parsed relationship page as authoritative.
 */

import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { writeJsonAtomicWithRetry } from "./lib/atomic_checkpoint.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const ROOT = path.resolve(import.meta.dirname, "..");
const DEFAULT_CHROME =
  process.platform === "win32"
    ? "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    : "/usr/bin/google-chrome";
const DEFAULT_PROFILE =
  process.platform === "win32"
    ? "C:\\tmp\\congress-pages-interchange-profile-2"
    : path.join(os.tmpdir(), "congress-pages-interchange-profile-2");
const BILL_TYPES = new Map([
  ["HR", ["hr", "H.R.", "house-bill"]],
  ["S", ["s", "S.", "senate-bill"]],
  ["HRES", ["hres", "H.Res.", "house-resolution"]],
  ["SRES", ["sres", "S.Res.", "senate-resolution"]],
  ["HJRES", ["hjres", "H.J.Res.", "house-joint-resolution"]],
  ["SJRES", ["sjres", "S.J.Res.", "senate-joint-resolution"]],
  ["HCONRES", ["hconres", "H.Con.Res.", "house-concurrent-resolution"]],
  ["SCONRES", ["sconres", "S.Con.Res.", "senate-concurrent-resolution"]],
]);

function parseArgs(argv) {
  const args = {
    seeds: null,
    output: null,
    profile: DEFAULT_PROFILE,
    chrome: DEFAULT_CHROME,
    headless: true,
    delayMs: 750,
    workers: 2,
    limit: null,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--seeds") args.seeds = argv[++index];
    else if (value === "--output") args.output = argv[++index];
    else if (value === "--profile") args.profile = argv[++index];
    else if (value === "--chrome") args.chrome = argv[++index];
    else if (value === "--delay-ms") args.delayMs = Number(argv[++index]);
    else if (value === "--workers") args.workers = Number(argv[++index]);
    else if (value === "--limit") args.limit = Number(argv[++index]);
    else if (value === "--headless") args.headless = true;
    else if (value === "--headed") args.headless = false;
    else if (value === "--help") {
      console.log(`Usage: node fec/refresh_bill_repository_headless.mjs [options]
  --seeds PATH      JSON array of dashboard bill seeds
  --output PATH     Atomic browser-refresh checkpoint JSON
  --profile PATH    Persistent Chrome profile
  --chrome PATH     Chrome executable
  --delay-ms N      Delay before each Congress.gov navigation (default 750)
  --workers N       Concurrent browser tabs, capped at 4 (default 2)
  --limit N         Process only the first N seeds
  --headless        Run Chrome headless (default)
  --headed          Explicit diagnostic fallback`);
      process.exit(0);
    } else {
      throw new Error(`Unknown argument: ${value}`);
    }
  }
  if (!args.seeds || !args.output) {
    throw new Error("--seeds and --output are required");
  }
  if (!Number.isFinite(args.delayMs) || args.delayMs < 0) {
    throw new Error("--delay-ms must be a non-negative number");
  }
  if (!Number.isInteger(args.workers) || args.workers < 1) {
    throw new Error("--workers must be a positive integer");
  }
  args.workers = Math.min(4, args.workers);
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

function isoNow() {
  return new Date().toISOString();
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function compact(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

export function parseBillSeed(seed) {
  const normalized = compact(seed?.bill).toUpperCase().replace(/[^A-Z0-9]/g, "");
  const match = normalized.match(
    /^(HCONRES|SCONRES|HJRES|SJRES|HRES|SRES|HR|S)(\d+)$/,
  );
  if (!match) throw new Error(`Unsupported bill identity: ${seed?.bill}`);
  const congress = Number(seed?.congress);
  if (!Number.isInteger(congress) || congress < 1) {
    throw new Error(`Invalid Congress number: ${seed?.congress}`);
  }
  const [type, prefix, slug] = BILL_TYPES.get(match[1]);
  const number = Number(match[2]);
  return {
    key: `${congress}:${type}:${number}`,
    congress,
    type,
    number,
    display: `${prefix}${number}`,
    url:
      `https://www.congress.gov/bill/${congress}th-congress/` +
      `${slug}/${number}`,
  };
}

function normalizeDate(value) {
  const text = compact(value);
  let match = text.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (match) return text;
  match = text.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (!match) return text;
  return `${match[3]}-${match[1].padStart(2, "0")}-${match[2].padStart(2, "0")}`;
}

function normalizeMemberUrl(rawUrl) {
  if (!rawUrl) return null;
  const url = new URL(rawUrl);
  if (url.protocol !== "https:" || url.hostname !== "www.congress.gov") {
    throw new Error(`Refusing non-Congress.gov member URL: ${url.href}`);
  }
  url.search = "";
  url.hash = "";
  return url.href.replace(/\/$/, "");
}

export function parseMember(name, rawUrl, date = "") {
  const memberUrl = normalizeMemberUrl(rawUrl);
  const bioguide =
    memberUrl?.match(/\/member\/[^/]+\/([A-Z]\d{6})$/i)?.[1]?.toUpperCase() ??
    "";
  const display = compact(name).replace(/\*$/, "");
  const bracket = display.match(/\[([^\]]+)\]$/)?.[1] ?? "";
  const parts = bracket.split("-").filter(Boolean);
  const firstIsParty = /^(D|R|I|L)$/i.test(parts[0] ?? "");
  const party = firstIsParty ? parts[0] : parts[1] ?? "";
  const state = firstIsParty ? parts[1] ?? "" : parts[2] ?? "";
  const districtParts = firstIsParty ? parts.slice(2) : parts.slice(3);
  return {
    name: display,
    bioguide,
    party,
    state,
    district: districtParts.join("-"),
    date: normalizeDate(date),
    withdrawn_date: "",
  };
}

async function waitForCongressPage(page, selector) {
  const deadline = Date.now() + 120_000;
  let lastTitle = "";
  let lastBody = "";
  while (Date.now() < deadline) {
    if (await page.locator(selector).count()) return;
    lastTitle = await page.title().catch(() => "");
    lastBody = await page
      .locator("body")
      .innerText()
      .then((value) => value.slice(0, 1200))
      .catch(() => "");
    if (
      /captcha|verify you are human|human verification/i.test(
        `${lastTitle} ${lastBody}`,
      ) &&
      !/verifying you are human|verification successful/i.test(
        `${lastTitle} ${lastBody}`,
      )
    ) {
      throw new Error(
        "Congress.gov presented an interactive human-verification challenge; " +
          "progress is checkpointed and no challenge was bypassed.",
      );
    }
    await sleep(2000);
  }
  if (
    /verifying you are human|verification|just a moment/i.test(
      `${lastTitle} ${lastBody} ${page.url()}`,
    )
  ) {
    throw new Error(
      "Congress.gov automatic verification did not complete; progress is " +
        "checkpointed and the refresh stopped without replacing last-good data.",
    );
  }
  throw new Error(
    `Congress.gov did not expose ${selector}; title=${JSON.stringify(
      await page.title().catch(() => "<unavailable>"),
    )} url=${page.url()}`,
  );
}

async function gotoWithRetry(page, url, delayMs, attempts = 3) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      if (delayMs) await sleep(delayMs);
      const response = await page.goto(url, {
        waitUntil: "domcontentloaded",
        timeout: 90_000,
      });
      await waitForCongressPage(page, "#main");
      const status = response?.status() ?? null;
      if (status !== null && status >= 400) {
        throw new Error(`Congress.gov returned HTTP ${status}`);
      }
      return;
    } catch (error) {
      lastError = error;
      if (
        /interactive human-verification|automatic verification/i.test(
          error.message,
        )
      ) {
        throw error;
      }
      if (attempt < attempts) await sleep(4000 * attempt);
    }
  }
  throw new Error(
    `Congress.gov navigation failed after ${attempts} attempts: ` +
      `${lastError?.message ?? "unknown navigation error"}`,
    { cause: lastError },
  );
}

async function extractBillPage(page) {
  return page.evaluate(() => {
    const clean = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
    const main = document.querySelector("#main");
    const tables = [...main.querySelectorAll("table")];
    const allRows = tables.flatMap((table) => [...table.querySelectorAll("tr")]);
    const labeledRow = (label) =>
      allRows.find((row) =>
        new RegExp(`^${label}:?\\s*$`, "i").test(
          clean(row.querySelector("th")?.textContent),
        ),
      );
    const sponsorRow = labeledRow("Sponsor");
    const sponsorLink = sponsorRow?.querySelector('a[href*="/member/"]');
    const sponsorDate = clean(sponsorRow?.textContent).match(
      /\((?:Introduced|Submitted|Offered|Proposed)\s+(\d{2}\/\d{2}\/\d{4})\)/i,
    )?.[1];
    const relationshipTables = tables
      .map((table) => {
        const headers = [...table.querySelectorAll("th")].map((node) =>
          clean(node.textContent),
        );
        if (
          headers.some((header) => /Cosponsors Who Withdrew/i.test(header)) ||
          !headers.some((header) => /^Cosponsor$/i.test(header))
        ) {
          return null;
        }
        return [...table.querySelectorAll("tr")]
          .map((row) => {
            const cells = [...row.querySelectorAll("td")];
            const link = row.querySelector('a[href*="/member/"]');
            if (!link || cells.length < 2) return null;
            const member = clean(link.textContent);
            return {
              member: member.replace(/\*$/, ""),
              member_url: link.href,
              original: /\*$/.test(member),
              date: clean(cells[1]?.textContent),
            };
          })
          .filter(Boolean);
      })
      .filter(Boolean)
      .flat();
    const statisticsNode = [...main.querySelectorAll("p")].find((node) =>
      /Cosponsor statistics:/i.test(clean(node.textContent)),
    );
    const countFromOption = (endpoint) => {
      const option = [...document.querySelectorAll("option")].find(
        (node) =>
          new RegExp(`/${endpoint}(?:$|\\?)`, "i").test(node.value) &&
          new RegExp(`^${endpoint}`, "i").test(clean(node.textContent)),
      );
      const value = clean(option?.textContent).match(/\(([\d,]+)\)/)?.[1];
      return value ? Number(value.replaceAll(",", "")) : null;
    };
    const summaryHeading = [...main.querySelectorAll("h2, h3, h4")].find(
      (heading) => /^Summary(?:\s|$)/i.test(clean(heading.textContent)),
    );
    const summaryParts = [];
    let node = summaryHeading?.nextElementSibling ?? null;
    while (
      node &&
      !/^H[234]$/.test(node.tagName) &&
      summaryParts.join(" ").length < 20_000
    ) {
      summaryParts.push(clean(node.textContent));
      node = node.nextElementSibling;
    }
    const latestText = clean(
      labeledRow("Latest Action")?.querySelector("td")?.textContent,
    );
    const latestMatch = latestText.match(
      /^(\d{2}\/\d{2}\/\d{4})\s+(.*?)(?:\s+\(All Actions\))?$/,
    );
    const heading = clean(main.querySelector("h1")?.textContent);
    const title =
      heading.replace(
        /^(?:H\.?\s*R\.?|S\.?|H\.?\s*(?:J\.?|Con\.?)?\s*Res\.?|S\.?\s*(?:J\.?|Con\.?)?\s*Res\.?)\s*\d+\s*[-–—:]\s*/i,
        "",
      ) || null;
    const policyRow = labeledRow("Policy Area");
    return {
      loaded_url: location.href,
      title,
      page_heading: heading,
      sponsor: clean(sponsorLink?.textContent) || null,
      sponsor_url: sponsorLink?.href ?? null,
      sponsor_date: sponsorDate ?? "",
      summary: clean(summaryParts.join(" ")) || null,
      policy_area: clean(policyRow?.querySelector("td")?.textContent) || null,
      latest_action: {
        date: latestMatch?.[1] ?? "",
        text: latestMatch?.[2] ?? latestText,
      },
      cosponsors: relationshipTables,
      statistics: clean(statisticsNode?.textContent),
      cosponsor_count: countFromOption("cosponsors"),
      amendment_count: countFromOption("amendments"),
      not_found: /page not found|requested page could not be found|error 404/i.test(
        `${document.title} ${clean(main.innerText).slice(0, 1200)}`,
      ),
      main_text: clean(main.innerText).slice(0, 2000),
      next_url: document.querySelector(".pagination a.next")?.href ?? null,
    };
  });
}

function reportedCosponsorCount(pageData) {
  const value = pageData.statistics.match(
    /Cosponsor statistics:\s*([\d,]+) current/i,
  )?.[1];
  return value ? Number(value.replaceAll(",", "")) : pageData.cosponsor_count;
}

function validateCongressUrl(rawUrl, kind) {
  const url = new URL(rawUrl);
  if (
    url.protocol !== "https:" ||
    url.hostname !== "www.congress.gov" ||
    !url.pathname.includes(`/${kind}/`)
  ) {
    throw new Error(`Refusing invalid Congress.gov ${kind} URL: ${url.href}`);
  }
  url.search = "";
  url.hash = "";
  return url.href.replace(/\/$/, "");
}

async function collectCosponsors(page, identity, delayMs) {
  const firstUrl = new URL(`${identity.url}/cosponsors`);
  firstUrl.searchParams.set("pageSize", "250");
  firstUrl.searchParams.set("pageSort", "firstToLast");
  firstUrl.searchParams.set("page", "1");
  await gotoWithRetry(page, firstUrl.href, delayMs);
  const pages = [await extractBillPage(page)];
  const visited = new Set([pages[0].loaded_url]);
  while (pages.at(-1).next_url) {
    if (pages.length >= 10) {
      throw new Error(`Cosponsor pagination exceeded 10 pages for ${identity.key}`);
    }
    const nextUrl = pages.at(-1).next_url;
    if (visited.has(nextUrl)) {
      throw new Error(`Cosponsor pagination loop for ${identity.key}`);
    }
    await gotoWithRetry(page, nextUrl, delayMs);
    const next = await extractBillPage(page);
    pages.push(next);
    visited.add(next.loaded_url);
  }
  const deduped = new Map();
  for (const item of pages.flatMap((value) => value.cosponsors)) {
    const member = parseMember(item.member, item.member_url, item.date);
    deduped.set(`${member.bioguide || member.name}:${member.date}`, {
      ...member,
      original: item.original,
    });
  }
  const rows = [...deduped.values()];
  const expected = reportedCosponsorCount(pages[0]);
  if (expected === null && rows.length === 0) {
    throw new Error(`Cosponsor endpoint was not observable for ${identity.key}`);
  }
  if (expected !== null && rows.length !== expected) {
    throw new Error(
      `Cosponsor count mismatch for ${identity.key}: expected ${expected}, parsed ${rows.length}`,
    );
  }
  return { rows, expected };
}

async function extractAmendmentPage(page) {
  return page.evaluate(() => {
    const clean = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
    const main = document.querySelector("#main");
    const links = [...main.querySelectorAll('a[href*="/amendment/"]')];
    const rows = links.map((link) => {
      const container =
        link.closest("li, article, tr, .expanded, .compact, .result-item") ??
        link.parentElement;
      const body = clean(container?.innerText);
      const latest = body.match(
        /Latest Action:\s*(\d{2}\/\d{2}\/\d{4})\s*(.*?)(?=\s+(?:Tracker:|Amends Bill:|Amendment is:|$))/i,
      );
      return {
        display: clean(link.textContent).replace(/\s+/g, ""),
        url: link.href,
        description: body.slice(0, 2000),
        latest_date: latest?.[1] ?? "",
        latest_text: latest?.[2] ?? "",
      };
    });
    return {
      loaded_url: location.href,
      rows,
      main_text: clean(main.innerText).slice(0, 2000),
      next_url: document.querySelector(".pagination a.next")?.href ?? null,
    };
  });
}

async function collectAmendments(page, identity, expected, delayMs) {
  if (expected === 0) return [];
  const firstUrl = new URL(`${identity.url}/amendments`);
  firstUrl.searchParams.set("pageSize", "250");
  firstUrl.searchParams.set("pageSort", "latestAction:desc");
  firstUrl.searchParams.set("page", "1");
  await gotoWithRetry(page, firstUrl.href, delayMs);
  const pages = [await extractAmendmentPage(page)];
  const visited = new Set([pages[0].loaded_url]);
  while (pages.at(-1).next_url) {
    if (pages.length >= 20) {
      throw new Error(`Amendment pagination exceeded 20 pages for ${identity.key}`);
    }
    const nextUrl = pages.at(-1).next_url;
    if (visited.has(nextUrl)) {
      throw new Error(`Amendment pagination loop for ${identity.key}`);
    }
    await gotoWithRetry(page, nextUrl, delayMs);
    const next = await extractAmendmentPage(page);
    pages.push(next);
    visited.add(next.loaded_url);
  }
  const deduped = new Map();
  for (const raw of pages.flatMap((value) => value.rows)) {
    const url = validateCongressUrl(raw.url, "amendment");
    const match = url.match(
      /\/amendment\/(\d+)th-congress\/(house|senate)-amendment\/(\d+)$/i,
    );
    if (!match) continue;
    const chamber = match[2].toLowerCase();
    const number = match[3];
    deduped.set(url, {
      display:
        raw.display.match(/[HS]\.?Amdt\.?\d+/i)?.[0] ??
        `${chamber === "house" ? "H" : "S"}.Amdt.${number}`,
      congress: Number(match[1]),
      type: chamber === "house" ? "hamdt" : "samdt",
      number,
      description: raw.description,
      latest_action: {
        date: normalizeDate(raw.latest_date),
        text: compact(raw.latest_text),
      },
      url,
    });
  }
  const rows = [...deduped.values()];
  const observable =
    rows.length > 0 ||
    /no amendments|zero amendments/i.test(pages[0].main_text);
  if (expected === null && !observable) {
    throw new Error(`Amendment endpoint was not observable for ${identity.key}`);
  }
  if (expected !== null && rows.length !== expected) {
    throw new Error(
      `Amendment count mismatch for ${identity.key}: expected ${expected}, parsed ${rows.length}`,
    );
  }
  return rows;
}

async function collectBill(page, seed, delayMs, updatedAt) {
  const identity = parseBillSeed(seed);
  await gotoWithRetry(page, identity.url, delayMs);
  const detail = await extractBillPage(page);
  if (detail.not_found) throw new Error(`Congress.gov did not find ${identity.key}`);
  const sponsor = detail.sponsor
    ? parseMember(detail.sponsor, detail.sponsor_url, detail.sponsor_date)
    : {
        name: compact(seed.sponsor),
        bioguide: "",
        party: "",
        state: "",
        district: "",
        date: "",
        withdrawn_date: "",
      };
  if (!sponsor.name) {
    throw new Error(`Sponsor was not observable for ${identity.key}`);
  }
  const relationships = await collectCosponsors(page, identity, delayMs);
  const amendments = await collectAmendments(
    page,
    identity,
    detail.amendment_count,
    delayMs,
  );
  const originals = relationships.rows
    .filter((row) => row.original)
    .map(({ original: _original, ...row }) => row);
  const later = relationships.rows
    .filter((row) => !row.original)
    .map(({ original: _original, ...row }) => row);
  return {
    ...identity,
    title: detail.title || compact(seed.title) || identity.display,
    introduced_date: normalizeDate(detail.sponsor_date),
    update_date: "",
    latest_action: {
      date: normalizeDate(detail.latest_action.date),
      text: compact(detail.latest_action.text) || compact(seed.action),
    },
    sponsor,
    original_cosponsors: originals,
    cosponsors: later,
    cosponsor_count: originals.length + later.length,
    amendments,
    summary: detail.summary || compact(seed.summary),
    policy_area: detail.policy_area || "",
    areas: [...new Set((seed.areas ?? []).map(compact).filter(Boolean))].sort(),
    updated_at: updatedAt,
    stale: false,
    source: "Congress.gov headless Chrome/Playwright",
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const seedsPath = assertWithinRoot(args.seeds);
  const outputPath = assertWithinRoot(args.output);
  const rawSeeds = JSON.parse(await fs.readFile(seedsPath, "utf8"));
  if (!Array.isArray(rawSeeds)) throw new Error("--seeds must contain a JSON array");
  let seeds = rawSeeds;
  if (args.limit !== null) seeds = seeds.slice(0, args.limit);
  for (const seed of seeds) parseBillSeed(seed);

  const startedAt = isoNow();
  const state = {
    schema: "congress-bill-browser-refresh-v1",
    status: "running",
    source: "Congress.gov headless Chrome/Playwright",
    started_at: startedAt,
    updated_at: startedAt,
    selected_count: seeds.length,
    refreshed: {},
    failures: {},
  };
  await writeJsonAtomicWithRetry(outputPath, state);

  const context = await chromium.launchPersistentContext(args.profile, {
    executablePath: args.chrome,
    headless: args.headless,
    ignoreDefaultArgs: ["--enable-automation"],
    args: ["--disable-blink-features=AutomationControlled"],
    viewport: { width: 1400, height: 900 },
  });
  let cursor = 0;
  let fatalError = null;
  let checkpoint = Promise.resolve();
  const save = () => {
    state.updated_at = isoNow();
    const snapshot = JSON.parse(JSON.stringify(state));
    checkpoint = checkpoint.then(() =>
      writeJsonAtomicWithRetry(outputPath, snapshot),
    );
    return checkpoint;
  };
  const worker = async () => {
    const page = await context.newPage();
    try {
      while (!fatalError) {
        const index = cursor;
        cursor += 1;
        if (index >= seeds.length) break;
        const seed = seeds[index];
        const identity = parseBillSeed(seed);
        try {
          state.refreshed[identity.key] = await collectBill(
            page,
            seed,
            args.delayMs,
            state.started_at,
          );
        } catch (error) {
          state.failures[identity.key] = `${error.name}: ${error.message}`;
          if (
            /interactive human-verification|automatic verification/i.test(
              error.message,
            )
          ) {
            fatalError = error;
          }
        }
        await save();
      }
    } finally {
      await page.close().catch(() => {});
    }
  };
  try {
    await Promise.all(
      Array.from(
        { length: Math.min(args.workers, Math.max(1, seeds.length)) },
        () => worker(),
      ),
    );
  } finally {
    await context.close().catch(() => {});
  }
  if (fatalError) {
    for (let index = cursor; index < seeds.length; index += 1) {
      const identity = parseBillSeed(seeds[index]);
      state.failures[identity.key] =
        "BrowserPaused: not attempted after Congress.gov verification";
    }
  }
  state.status =
    Object.keys(state.failures).length === 0
      ? "complete"
      : Object.keys(state.refreshed).length
        ? "partial"
        : "stale";
  await save();
  await checkpoint;
  console.log(
    JSON.stringify({
      status: state.status,
      selected: state.selected_count,
      refreshed: Object.keys(state.refreshed).length,
      failed: Object.keys(state.failures).length,
      output: outputPath,
    }),
  );
}

const invokedPath = process.argv[1]
  ? pathToFileURL(path.resolve(process.argv[1])).href
  : "";
if (import.meta.url === invokedPath) {
  main().catch((error) => {
    console.error(`${error.name}: ${error.message}`);
    process.exitCode = 1;
  });
}
