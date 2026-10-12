#!/usr/bin/env node
/**
 * Enrich an Advanced Search candidate corpus with official Congress.gov
 * sponsor/cosponsor relationships.
 *
 * This script never discovers measures. It only visits Congress.gov detail and
 * cosponsor pages for the Congress-qualified records already recovered by
 * collect_congress_advanced_pages.mjs. Current, original, and withdrawn
 * cosponsorships remain separately visible.
 */

import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { writeTextAtomicWithRetry } from "./lib/atomic_checkpoint.mjs";
import { buildBillstatusRelationshipReuse } from "./lib/billstatus_relationship_reuse.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const ROOT = path.resolve(import.meta.dirname, "..");
const DEFAULT_RUN = path.join(
  ROOT,
  "audits",
  "legfix5",
  "congress_advanced_pages_20260725",
);
const DEFAULT_CHROME =
  process.platform === "win32"
    ? "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    : "/usr/bin/google-chrome";

function parseArgs(argv) {
  const args = {
    run: DEFAULT_RUN,
    out: null,
    profile: path.join(os.tmpdir(), "codex-congress-relationships-profile"),
    chrome: DEFAULT_CHROME,
    headless: true,
    delayMs: 750,
    resume: false,
    billstatusSidecar: path.join(
      ROOT,
      "audits",
      "legfix5",
      "billstatus_bulk_enrichment",
      "review_sidecar_current.json",
    ),
    billstatusReuseRequired: false,
    keys: null,
    limit: null,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--run") args.run = argv[++index];
    else if (value === "--out") args.out = argv[++index];
    else if (value === "--profile") args.profile = argv[++index];
    else if (value === "--chrome") args.chrome = argv[++index];
    else if (value === "--delay-ms") args.delayMs = Number(argv[++index]);
    else if (value === "--billstatus-sidecar") {
      args.billstatusSidecar = path.resolve(ROOT, argv[++index]);
    } else if (value === "--no-billstatus-sidecar-reuse") {
      args.billstatusSidecar = null;
    } else if (value === "--require-billstatus-sidecar-reuse") {
      args.billstatusReuseRequired = true;
    }
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
      console.log(`Usage: node fec/enrich_congress_sponsorship_pages.mjs [options]
  --run PATH        Completed Advanced Search run directory
  --out PATH        Output directory (default RUN/relationships)
  --profile PATH    Isolated Chrome profile directory
  --chrome PATH     Chrome executable
  --delay-ms N      Delay between Congress.gov detail pages (default 750)
  --billstatus-sidecar PATH
                      Reuse exact hash-bound official bulk relationships
  --no-billstatus-sidecar-reuse
                      Force live detail-page enrichment
  --require-billstatus-sidecar-reuse
                      Fail closed instead of launching a browser if reuse rejects
  --keys IDS        Comma-separated Congress-qualified keys
  --limit N         Process only the first N selected records
  --headless        Run Chrome headless (default)
  --headed          Run headed only when explicitly requested
  --resume          Reuse measure artifacts that pass provenance checks`);
      process.exit(0);
    } else {
      throw new Error(`Unknown argument: ${value}`);
    }
  }
  if (!Number.isFinite(args.delayMs) || args.delayMs < 0) {
    throw new Error("--delay-ms must be a non-negative number");
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

function isoNow() {
  return new Date().toISOString();
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function compact(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

async function readJson(file) {
  return JSON.parse(await fs.readFile(file, "utf8"));
}

async function writeTextWithRetry(file, textValue, attempts = 30) {
  await writeTextAtomicWithRetry(file, textValue, { attempts });
}

async function writeJson(file, value) {
  await writeTextWithRetry(file, `${JSON.stringify(value, null, 2)}\n`);
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

async function readNdjson(file) {
  const text = await fs.readFile(file, "utf8");
  return text
    .split(/\r?\n/)
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line));
}

async function writeNdjson(file, rows) {
  const body = rows.map((row) => JSON.stringify(row)).join("\n");
  await writeTextWithRetry(file, body ? `${body}\n` : "");
}

async function waitForCongressPage(page, selector) {
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    if (await page.locator(selector).count()) return;
    const title = await page.title().catch(() => "");
    const body = await page
      .locator("body")
      .innerText()
      .then((value) => value.slice(0, 1200))
      .catch(() => "");
    if (
      /captcha|verify you are human|human verification/i.test(`${title} ${body}`) &&
      !/verifying you are human|verification successful/i.test(`${title} ${body}`)
    ) {
      throw new Error(
        "Congress.gov presented an interactive human-verification challenge; " +
          "progress is checkpointed and no challenge was bypassed.",
      );
    }
    await sleep(2000);
  }
  throw new Error(
    `Congress.gov did not expose ${selector}; title=${JSON.stringify(
      await page.title().catch(() => "<unavailable>"),
    )} url=${page.url()}`,
  );
}

async function gotoWithRetry(page, url, selector = "#main", attempts = 3) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const response = await page.goto(url, {
        waitUntil: "domcontentloaded",
        timeout: 90_000,
      });
      await waitForCongressPage(page, selector);
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

function normalizeMemberUrl(rawUrl) {
  if (!rawUrl) return null;
  const url = new URL(rawUrl);
  url.search = "";
  url.hash = "";
  return url.href.replace(/\/$/, "");
}

function parseMember(name, rawUrl) {
  const memberUrl = normalizeMemberUrl(rawUrl);
  const bioguideId =
    memberUrl?.match(/\/member\/[^/]+\/([A-Z]\d{6})$/i)?.[1]?.toUpperCase() ??
    null;
  const bracket = compact(name).match(/\[([^\]]+)\]\*?$/)?.[1] ?? null;
  const parts = bracket?.split("-") ?? [];
  const chamberLabel = parts[0]?.replace(/\.$/, "") ?? null;
  const party = parts.length >= 2 ? parts[1] : null;
  const state = parts.length >= 3 ? parts[2] : null;
  const district = parts.length >= 4 ? parts.slice(3).join("-") : null;
  return {
    member: compact(name).replace(/\*$/, ""),
    member_url: memberUrl,
    bioguide_id: bioguideId,
    chamber_label: chamberLabel,
    party,
    state,
    district,
  };
}

function parseStatistics(text) {
  const value = compact(text);
  const current = value.match(/Cosponsor statistics:\s*([\d,]+) current/i);
  const original = value.match(/includes\s*([\d,]+) original/i);
  const withdrawn = value.match(/excludes\s*([\d,]+) withdrawn/i);
  return {
    current: current ? Number(current[1].replaceAll(",", "")) : null,
    original: original ? Number(original[1].replaceAll(",", "")) : null,
    withdrawn: withdrawn
      ? Number(withdrawn[1].replaceAll(",", ""))
      : null,
    raw: value || null,
  };
}

async function extractRelationshipPage(page) {
  return page.evaluate(() => {
    const clean = (value) =>
      String(value ?? "").replace(/\s+/g, " ").trim();
    const main = document.querySelector("#main");
    const tables = [...main.querySelectorAll("table")];
    const sponsorRow = tables
      .flatMap((table) => [...table.querySelectorAll("tr")])
      .find((row) =>
        /^Sponsor:\s*/i.test(clean(row.querySelector("th")?.textContent)),
      );
    const sponsorLink = sponsorRow?.querySelector('a[href*="/member/"]');
    const sponsorText = clean(sponsorLink?.textContent);
    const sponsorDate = clean(sponsorRow?.textContent).match(
      /\((?:Introduced|Submitted|Offered|Proposed)\s+(\d{2}\/\d{2}\/\d{4})\)/i,
    )?.[1];
    const relationshipTables = tables
      .map((table) => {
        const headers = [...table.querySelectorAll("th")].map((node) =>
          clean(node.textContent),
        );
        const kind = headers.some((header) =>
          /Cosponsors Who Withdrew/i.test(header),
        )
          ? "withdrawn"
          : headers.some((header) => /^Cosponsor$/i.test(header))
            ? "current"
            : null;
        if (!kind) return null;
        const rows = [...table.querySelectorAll("tr")]
          .map((row) => {
            const cells = [...row.querySelectorAll("td")];
            const memberLink = row.querySelector('a[href*="/member/"]');
            if (!memberLink || cells.length < 2) return null;
            const memberDisplay = clean(memberLink.textContent);
            return {
              member: memberDisplay.replace(/\*$/, ""),
              member_url: memberLink.href,
              original_cosponsor: /\*$/.test(memberDisplay),
              sponsorship_date: clean(cells[1]?.textContent) || null,
              withdrawn_date:
                kind === "withdrawn"
                  ? clean(cells[2]?.textContent) || null
                  : null,
              withdrawal_explanation:
                kind === "withdrawn"
                  ? clean(cells.slice(3).map((cell) => cell.textContent).join(" ")) ||
                    null
                  : null,
            };
          })
          .filter(Boolean);
        return { kind, headers, rows };
      })
      .filter(Boolean);
    const statisticsNode = [...main.querySelectorAll("p")].find((node) =>
      /Cosponsor statistics:/i.test(clean(node.textContent)),
    );
    const cosponsorTab = [...document.querySelectorAll("option")].find(
      (option) =>
        /\/cosponsors(?:$|\?)/i.test(option.value) &&
        /^Cosponsors\s*\(/i.test(clean(option.textContent)),
    );
    const summaryHeading = [...main.querySelectorAll("h2, h3, h4")].find(
      (heading) => /^Summary(?:\s|$)/i.test(clean(heading.textContent)),
    );
    const summaryParts = [];
    let summaryNode = summaryHeading?.nextElementSibling ?? null;
    while (
      summaryNode &&
      !/^H[234]$/.test(summaryNode.tagName) &&
      summaryParts.join(" ").length < 20_000
    ) {
      summaryParts.push(clean(summaryNode.textContent));
      summaryNode = summaryNode.nextElementSibling;
    }
    const labeledRow = (label) =>
      tables
        .flatMap((table) => [...table.querySelectorAll("tr")])
        .find((row) =>
          new RegExp(`^${label}:?\\s*$`, "i").test(
            clean(row.querySelector("th")?.textContent),
          ),
        );
    const policyRow = labeledRow("Policy Area");
    const officialTitle =
      clean(
        document.querySelector(
          'meta[property="og:title"]',
        )?.getAttribute("content"),
      ) ||
      clean(main.querySelector("h1")?.textContent) ||
      null;
    return {
      loaded_url: location.href,
      title: document.title,
      main_text: clean(main.innerText).slice(0, 2000),
      not_found:
        /page not found|requested page could not be found|error 404/i.test(
          `${document.title} ${clean(main.innerText).slice(0, 1200)}`,
        ),
      sponsor: sponsorText || null,
      sponsor_url: sponsorLink?.href ?? null,
      sponsor_date: sponsorDate ?? null,
      official_title: officialTitle,
      policy_area:
        clean(policyRow?.querySelector("td")?.textContent) || null,
      summary: clean(summaryParts.join(" ")) || null,
      meta_description:
        clean(
          document
            .querySelector('meta[name="description"]')
            ?.getAttribute("content"),
        ) || null,
      statistics_text: clean(statisticsNode?.textContent) || null,
      tab_cosponsor_count: (() => {
        const value = clean(cosponsorTab?.textContent).match(
          /\(([\d,]+)\)/,
        )?.[1];
        return value ? Number(value.replaceAll(",", "")) : null;
      })(),
      relationship_tables: relationshipTables,
      next_url:
        document.querySelector(".pagination a.next")?.href ?? null,
    };
  });
}

function edgeKey(edge) {
  return [
    edge.canonical_key,
    edge.role,
    edge.bioguide_id ?? edge.member,
    edge.relationship_status,
    edge.sponsorship_date ?? "",
  ].join(":");
}

function makeSponsorEdge(record, detail) {
  const member = parseMember(
    detail?.sponsor ?? record.sponsor,
    detail?.sponsor_url ?? record.sponsor_url,
  );
  return {
    canonical_key: record.canonical_key,
    congress: record.congress,
    type: record.type,
    number: record.number,
    display_number: record.display_number,
    title: record.title ?? record.purpose,
    role: "Sponsor",
    ...member,
    sponsorship_date:
      detail?.sponsor_date ?? record.initial_action_date ?? null,
    original_cosponsor: null,
    relationship_status: "current",
    withdrawn_date: null,
    withdrawal_explanation: null,
    source_url: detail?.loaded_url ?? record.result_url,
  };
}

function makeCosponsorEdge(record, raw, status, sourceUrl) {
  return {
    canonical_key: record.canonical_key,
    congress: record.congress,
    type: record.type,
    number: record.number,
    display_number: record.display_number,
    title: record.title ?? record.purpose,
    role: "Cosponsor",
    ...parseMember(raw.member, raw.member_url),
    sponsorship_date: raw.sponsorship_date,
    original_cosponsor: raw.original_cosponsor,
    relationship_status: status,
    withdrawn_date: raw.withdrawn_date,
    withdrawal_explanation: raw.withdrawal_explanation,
    source_url: sourceUrl,
  };
}

async function collectMeasure(page, record, delayMs) {
  const cosponsorUrl = new URL(`${record.canonical_url}/cosponsors`);
  cosponsorUrl.searchParams.set("pageSize", "250");
  cosponsorUrl.searchParams.set("pageSort", "firstToLast");
  cosponsorUrl.searchParams.set("page", "1");
  if (delayMs) await sleep(delayMs);
  const httpStatus = await gotoWithRetry(page, cosponsorUrl.href);
  let extracted = await extractRelationshipPage(page);
  let endpoint = "cosponsors";
  if (extracted.not_found || (httpStatus !== null && httpStatus >= 400)) {
    if (delayMs) await sleep(delayMs);
    await gotoWithRetry(page, record.canonical_url);
    extracted = await extractRelationshipPage(page);
    endpoint = "detail_fallback";
  }

  const pages = [extracted];
  const visited = new Set([extracted.loaded_url]);
  while (pages.at(-1).next_url) {
    if (pages.length >= 10) {
      throw new Error(
        `Cosponsor pagination exceeded 10 pages for ${record.canonical_key}`,
      );
    }
    const nextUrl = pages.at(-1).next_url;
    if (visited.has(nextUrl)) {
      throw new Error(
        `Cosponsor pagination loop for ${record.canonical_key}: ${nextUrl}`,
      );
    }
    if (delayMs) await sleep(delayMs);
    await gotoWithRetry(page, nextUrl);
    const nextPage = await extractRelationshipPage(page);
    visited.add(nextPage.loaded_url);
    pages.push(nextPage);
  }

  let detailPage = pages[0];
  if (endpoint === "cosponsors") {
    if (delayMs) await sleep(delayMs);
    await gotoWithRetry(page, record.canonical_url);
    detailPage = await extractRelationshipPage(page);
  }

  const current = [];
  const withdrawn = [];
  for (const relationshipPage of pages) {
    for (const table of relationshipPage.relationship_tables) {
      if (table.kind === "current") current.push(...table.rows);
      else if (table.kind === "withdrawn") withdrawn.push(...table.rows);
    }
  }
  const dedupe = (rows, status) => {
    const map = new Map();
    for (const raw of rows) {
      const edge = makeCosponsorEdge(
        record,
        raw,
        status,
        pages[0].loaded_url,
      );
      map.set(edgeKey(edge), edge);
    }
    return [...map.values()];
  };
  const currentEdges = dedupe(current, "current");
  const withdrawnEdges = dedupe(withdrawn, "withdrawn");
  const statistics = parseStatistics(pages[0].statistics_text);
  const observable =
    endpoint === "cosponsors" &&
    !pages[0].not_found &&
    (statistics.current !== null ||
      pages[0].tab_cosponsor_count === 0 ||
      currentEdges.length > 0);
  const expectedCurrent =
    statistics.current ?? pages[0].tab_cosponsor_count ?? null;
  const expectedOriginal = statistics.original;
  const expectedWithdrawn = statistics.withdrawn;
  const originalCount = currentEdges.filter(
    (edge) => edge.original_cosponsor,
  ).length;
  const validation = {
    cosponsor_endpoint_observable: observable,
    current_count_matches:
      expectedCurrent === null ? null : currentEdges.length === expectedCurrent,
    original_count_matches:
      expectedOriginal === null
        ? null
        : originalCount === expectedOriginal,
    withdrawn_count_matches:
      expectedWithdrawn === null
        ? null
        : withdrawnEdges.length === expectedWithdrawn,
    search_card_current_count_matches:
      record.record_kind !== "bill"
        ? null
        : currentEdges.length === record.cosponsor_count,
  };
  const sponsorEdge = makeSponsorEdge(record, detailPage);
  validation.sponsor_observable = Boolean(sponsorEdge.member);
  return {
    schema: "congress-sponsorship-measure-v1",
    collected_at: isoNow(),
    primary_source: "Congress.gov measure and cosponsor pages",
    discovery_canonical_key: record.canonical_key,
    discovery_record_sha256: stableHash(record),
    endpoint,
    pages: pages.map((item) => ({
      loaded_url: item.loaded_url,
      title: item.title,
      next_url: item.next_url,
    })),
    detail_metadata: {
      loaded_url: detailPage.loaded_url,
      official_title: detailPage.official_title,
      policy_area: detailPage.policy_area,
      summary: detailPage.summary,
      meta_description: detailPage.meta_description,
      main_text_excerpt: detailPage.main_text,
    },
    reported_statistics: statistics,
    reported_tab_cosponsor_count: pages[0].tab_cosponsor_count,
    validation,
    edges: [sponsorEdge, ...currentEdges, ...withdrawnEdges],
  };
}

function summarizeEdgeCounts(edges) {
  const counts = {};
  for (const edge of edges) {
    const key =
      edge.role === "Sponsor"
        ? "sponsors"
        : edge.relationship_status === "withdrawn"
          ? "withdrawn_cosponsors"
          : edge.original_cosponsor
            ? "original_cosponsors"
            : "later_cosponsors";
    counts[key] = (counts[key] ?? 0) + 1;
  }
  return counts;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const runDir = assertWithinRoot(args.run);
  const outDir = assertWithinRoot(args.out ?? path.join(runDir, "relationships"));
  const discoveryManifest = await readJson(path.join(runDir, "manifest.json"));
  if (discoveryManifest.status !== "complete") {
    throw new Error("Advanced Search run manifest is not complete");
  }
  let records = await readNdjson(
    path.join(runDir, discoveryManifest.union.records_file),
  );
  if (args.keys) {
    records = records.filter((record) => args.keys.has(record.canonical_key));
    const missing = [...args.keys].filter(
      (key) => !records.some((record) => record.canonical_key === key),
    );
    if (missing.length) throw new Error(`Unknown canonical keys: ${missing.join(", ")}`);
  }
  if (args.limit !== null) records = records.slice(0, args.limit);
  if (!records.length) throw new Error("No discovery records selected");

  await fs.mkdir(outDir, { recursive: true });
  const partialFile = path.join(outDir, "manifest.partial.json");
  const manifestFile = path.join(outDir, "manifest.json");
  if (args.billstatusSidecar && !args.keys && args.limit === null) {
    try {
      const reused = await buildBillstatusRelationshipReuse({
        pointerPath: args.billstatusSidecar,
        discoveryRecords: records,
        discoveryManifestPath: path.join(runDir, "manifest.json"),
        discoveryRecordsSha256: discoveryManifest.union.records_sha256,
        outputDir: outDir,
      });
      await writeJson(manifestFile, reused);
      await removeWithRetry(partialFile);
      console.log(
        `COMPLETE BILLSTATUS HYBRID REUSE: ${reused.edge_count} edge(s), ` +
          `${reused.resolved_amendment_count} amendment(s) resolved, ` +
          `${reused.non_member_sponsor_disposition_count} explicit non-member ` +
          "sponsor disposition(s), " +
          "0 browser navigation(s)",
      );
      return;
    } catch (error) {
      if (args.billstatusReuseRequired) {
        throw new Error(
          `Required BillStatus relationship reuse rejected: ${error.message}`,
          { cause: error },
        );
      }
      console.log(
        `BILLSTATUS REUSE NOT APPLICABLE: ${error.message}; ` +
          "falling back to live Congress.gov pages",
      );
    }
  }
  const manifest = {
    schema: "congress-sponsorship-enrichment-manifest-v1",
    status: "running",
    started_at: isoNow(),
    completed_at: null,
    primary_source: "Congress.gov measure and cosponsor pages",
    discovery_manifest: path
      .relative(ROOT, path.join(runDir, "manifest.json"))
      .split(path.sep)
      .join("/"),
    discovery_records_sha256: discoveryManifest.union.records_sha256,
    selected_record_count: records.length,
    completed_records: [],
  };
  await writeJson(partialFile, manifest);

  const context = await chromium.launchPersistentContext(args.profile, {
    executablePath: args.chrome,
    headless: args.headless,
    ignoreDefaultArgs: ["--enable-automation"],
    args: ["--disable-blink-features=AutomationControlled"],
    viewport: { width: 1400, height: 900 },
  });
  try {
    const page = context.pages()[0] || (await context.newPage());
    const artifacts = [];
    let resumedCount = 0;
    let collectedCount = 0;
    for (let index = 0; index < records.length; index += 1) {
      const record = records[index];
      const measureFile = path.join(
        outDir,
        "raw",
        String(record.congress),
        record.type,
        `${record.number}.json`,
      );
      let artifact = null;
      if (args.resume) {
        try {
          const prior = await readJson(measureFile);
          if (
            prior.discovery_canonical_key === record.canonical_key &&
            prior.discovery_record_sha256 === stableHash(record)
          ) {
            artifact = prior;
            resumedCount += 1;
          }
        } catch (error) {
          if (error.code !== "ENOENT") throw error;
        }
      }
      if (!artifact) {
        artifact = await collectMeasure(page, record, args.delayMs);
        await writeJson(measureFile, artifact);
        collectedCount += 1;
      }
      artifacts.push(artifact);
      manifest.completed_records.push({
        canonical_key: record.canonical_key,
        file: path
          .relative(outDir, measureFile)
          .split(path.sep)
          .join("/"),
        edge_count: artifact.edges.length,
        validation: artifact.validation,
      });
      if ((index + 1) % 100 === 0 || index + 1 === records.length) {
        await writeJson(partialFile, manifest);
        console.log(
          `REL PROGRESS ${index + 1}/${records.length}: ` +
            `${collectedCount} collected, ${resumedCount} resumed`,
        );
      }
    }

    const edgeMap = new Map();
    const unresolved = [];
    const metadata = [];
    for (const artifact of artifacts) {
      for (const edge of artifact.edges) edgeMap.set(edgeKey(edge), edge);
      metadata.push({
        canonical_key: artifact.discovery_canonical_key,
        ...artifact.detail_metadata,
      });
      const failed = Object.entries(artifact.validation).filter(
        ([, value]) => value === false,
      );
      const unknown = Object.entries(artifact.validation).filter(
        ([, value]) => value === null,
      );
      if (failed.length || unknown.length) {
        unresolved.push({
          canonical_key: artifact.discovery_canonical_key,
          endpoint: artifact.endpoint,
          failed_checks: failed.map(([key]) => key),
          unobservable_checks: unknown.map(([key]) => key),
          disposition: "included_relationship_data_requires_review",
          source_urls: artifact.pages.map((item) => item.loaded_url),
        });
      }
    }
    const edges = [...edgeMap.values()].sort((left, right) =>
      edgeKey(left).localeCompare(edgeKey(right), undefined, { numeric: true }),
    );
    unresolved.sort((left, right) =>
      left.canonical_key.localeCompare(right.canonical_key, undefined, {
        numeric: true,
      }),
    );
    const edgesFile = path.join(outDir, "member_measure_edges.ndjson");
    const unresolvedFile = path.join(
      outDir,
      "relationship_verification_ledger.ndjson",
    );
    const metadataFile = path.join(outDir, "measure_metadata.ndjson");
    await writeNdjson(edgesFile, edges);
    await writeNdjson(unresolvedFile, unresolved);
    await writeNdjson(metadataFile, metadata);
    manifest.status = "complete";
    manifest.completed_at = isoNow();
    manifest.edge_count = edges.length;
    manifest.edge_counts = summarizeEdgeCounts(edges);
    manifest.unresolved_measure_count = unresolved.length;
    manifest.edges_file = path
      .relative(outDir, edgesFile)
      .split(path.sep)
      .join("/");
    manifest.edges_sha256 = stableHash(edges);
    manifest.verification_ledger_file = path
      .relative(outDir, unresolvedFile)
      .split(path.sep)
      .join("/");
    manifest.verification_ledger_sha256 = stableHash(unresolved);
    manifest.measure_metadata_file = path
      .relative(outDir, metadataFile)
      .split(path.sep)
      .join("/");
    manifest.measure_metadata_sha256 = stableHash(metadata);
    await writeJson(manifestFile, manifest);
    await removeWithRetry(partialFile);
    console.log(
      `COMPLETE: ${edges.length} member-measure edge(s), ` +
        `${unresolved.length} measure(s) require review`,
    );
  } catch (error) {
    manifest.status = "failed";
    manifest.failed_at = isoNow();
    manifest.error = {
      name: error.name,
      message: error.message,
      stack: error.stack,
    };
    await writeJson(partialFile, manifest);
    throw error;
  } finally {
    await context.close();
  }
}

await main();
