#!/usr/bin/env node
/**
 * Strict six-worker Congress.gov identifier-probe collector.
 *
 * This executable attaches to an explicitly launched visible Chrome CDP
 * session and uses that BrowserContext's ephemeral APIRequestContext. Cookie
 * values are never read or persisted. HTML is parsed in isolated local DOMs;
 * the parser pages never navigate. Every accepted page is range-, query-,
 * identity-, count-, and hash-reconciled. Unsafe surfaces fail closed while
 * the other logical workers continue.
 */

import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

import {
  writeJsonAtomicWithRetry,
  writeTextAtomicWithRetry,
} from "../lib/atomic_checkpoint.mjs";
import {
  createCongressContextHttpFastPath,
  validateCongressHtmlEnvelope,
} from "../lib/congress_context_http_fast_path.mjs";
import {
  buildAdvancedSearchPageUrl,
  buildAdvancedSearchQuery,
  CONGRESS_PAGE_SIZE,
  CONGRESS_PAGE_SORT,
  parseAdvancedSearchHttpBody,
  STRICT_ZERO_SURFACE,
} from "../lib/congress_advanced_http_surface.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const ROOT = path.resolve(import.meta.dirname, "../..");
const DEFAULT_PLAN = path.join(
  ROOT,
  "audits",
  "legfix5",
  "identifier_probe_parallel_20260726",
  "shard_plan.json",
);
const DEFAULT_OUT = path.join(
  ROOT,
  "audits",
  "legfix5",
  "identifier_probe_fast_20260726",
);
const MANIFEST_SCHEMA = "acu-congress-advanced-pages-manifest-v1";
const QUERY_SCHEMA = "congress-advanced-search-query-v1";
const PAGE_SCHEMA = "congress-advanced-search-page-v1";

function sha256(value) {
  return crypto
    .createHash("sha256")
    .update(
      typeof value === "string" || Buffer.isBuffer(value)
        ? value
        : JSON.stringify(value),
    )
    .digest("hex");
}

async function sha256File(file) {
  return sha256(await fs.readFile(file));
}

function isoNow() {
  return new Date().toISOString();
}

function sameSessionBoundSingletonRedirect(leftUrl, rightUrl) {
  let left;
  let right;
  try {
    left = new URL(leftUrl);
    right = new URL(rightUrl);
  } catch {
    return false;
  }
  const legislationPath =
    /\/(?:index\.php\/)?(?:bill|amendment)\/\d+(?:st|nd|rd|th)-congress\/[^/]+\/\d+\/?$/i;
  return Boolean(
    left.origin === "https://www.congress.gov" &&
      right.origin === left.origin &&
      legislationPath.test(left.pathname) &&
      right.pathname.toLowerCase() === left.pathname.toLowerCase() &&
      left.searchParams.get("hl") &&
      right.searchParams.get("hl") === left.searchParams.get("hl") &&
      left.searchParams.get("r") === "1" &&
      right.searchParams.get("r") === "1" &&
      left.searchParams.get("s") &&
      right.searchParams.get("s") === left.searchParams.get("s"),
  );
}

function assertWithinRoot(target) {
  const resolved = path.resolve(ROOT, target);
  const relative = path.relative(ROOT, resolved);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`Path must stay within repository root: ${resolved}`);
  }
  return resolved;
}

function resolvePlanPath(planDir, value) {
  const candidate = path.isAbsolute(value)
    ? path.resolve(value)
    : path.resolve(ROOT, value);
  const relative = path.relative(ROOT, candidate);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`Plan path escapes repository: ${value}`);
  }
  return candidate;
}

function parseArgs(argv) {
  const args = {
    plan: DEFAULT_PLAN,
    out: DEFAULT_OUT,
    endpoint: "http://127.0.0.1:9222",
    workers: 6,
    resume: true,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--plan") args.plan = argv[++index];
    else if (value === "--out") args.out = argv[++index];
    else if (value === "--endpoint") args.endpoint = argv[++index];
    else if (value === "--workers") args.workers = Number(argv[++index]);
    else if (value === "--resume") args.resume = true;
    else if (value === "--no-resume") args.resume = false;
    else if (value === "--help") {
      console.log(`Usage: node fec/collect/collect_congress_identifier_fast.mjs [options]
  --plan PATH       Existing exact 55+155 identifier shard plan
  --out PATH        New/resumable output directory
  --endpoint URL    Existing visible Chrome CDP endpoint (default :9222)
  --workers N       Concurrent logical HTTP workers, 1-6 (default 6)
  --resume          Reuse exact hash-valid query/page artifacts (default)
  --no-resume       Refuse completed-query reuse`);
      process.exit(0);
    } else {
      throw new Error(`Unknown argument: ${value}`);
    }
  }
  if (!Number.isInteger(args.workers) || args.workers < 1 || args.workers > 6) {
    throw new Error("--workers must be an integer between 1 and 6");
  }
  return args;
}

async function readJson(file) {
  return JSON.parse(await fs.readFile(file, "utf8"));
}

async function writeNdjson(file, rows) {
  const body = rows.map((row) => JSON.stringify(row)).join("\n");
  await writeTextAtomicWithRetry(file, body ? `${body}\n` : "");
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

function queryMetadata(entry) {
  return {
    label: entry.label,
    scoring_category: entry.scoring_category,
    record_kind: entry.record_kind ?? "identifier_probe",
    parent_category_id: entry.parent_category_id ?? null,
    parent_category_label: entry.parent_category_label ?? null,
    identifier_text: entry.identifier_text ?? null,
    title_alias: entry.title_alias ?? null,
    ambiguous_short_act_alias: entry.ambiguous_short_act_alias ?? false,
    membership_non_dispositive:
      entry.membership_non_dispositive ?? false,
    source_paragraph: entry.source_paragraph ?? null,
  };
}

function queryManifestEntry(artifact, queryFile) {
  return {
    query_id: artifact.query_id,
    ...queryMetadata(artifact),
    query_file: queryFile,
    result_total: artifact.result_total,
    unique_record_count: artifact.unique_record_count,
    page_count: artifact.page_count,
    counts_by_congress: artifact.counts_by_congress,
    counts_by_type: artifact.counts_by_type,
    records_sha256: artifact.records_sha256,
    validation: artifact.validation,
  };
}

async function copyImmutable(source, destination, expectedHash = null) {
  const sourceHash = await sha256File(source);
  if (expectedHash && sourceHash !== expectedHash) {
    throw new Error(`Preserved source hash mismatch: ${source}`);
  }
  try {
    const destinationHash = await sha256File(destination);
    if (destinationHash !== sourceHash) {
      throw new Error(`Immutable destination differs: ${destination}`);
    }
    return sourceHash;
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  await fs.mkdir(path.dirname(destination), { recursive: true });
  const temporary = `${destination}.${process.pid}.${crypto.randomUUID()}.tmp`;
  try {
    await fs.copyFile(source, temporary);
    if ((await sha256File(temporary)) !== sourceHash) {
      throw new Error(`Temporary copy hash mismatch: ${temporary}`);
    }
    try {
      await fs.link(temporary, destination);
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
      if ((await sha256File(destination)) !== sourceHash) {
        throw new Error(`Concurrent immutable copy differs: ${destination}`);
      }
    }
  } finally {
    await fs.rm(temporary, { force: true }).catch(() => {});
  }
  return sourceHash;
}

async function seedPreserved(plan, outDir) {
  const sourceRun = resolvePlanPath(
    path.dirname(plan.__file),
    plan.preserved.source_run,
  );
  const seeded = [];
  for (const receipt of plan.preserved.queries) {
    const sourceQuery = path.join(sourceRun, receipt.query_file);
    const destinationQuery = path.join(outDir, receipt.query_file);
    const artifact = await readJson(sourceQuery);
    if (artifact.query_id !== receipt.query_id) {
      throw new Error(`Preserved query ID mismatch: ${receipt.query_id}`);
    }
    const pageFileBindings = [];
    for (const page of artifact.pages ?? []) {
      const sourcePage = path.join(sourceRun, page.file);
      const pageFileHash = await copyImmutable(
        sourcePage,
        path.join(outDir, page.file),
      );
      pageFileBindings.push([page.file, pageFileHash]);
    }
    if (
      sha256(pageFileBindings) !==
      receipt.page_artifact_file_hash_binding_sha256
    ) {
      throw new Error(
        `Preserved page-file hash binding mismatch: ${receipt.query_id}`,
      );
    }
    await copyImmutable(
      sourceQuery,
      destinationQuery,
      receipt.query_file_sha256,
    );
    seeded.push({
      artifact: await readJson(destinationQuery),
      query_file: receipt.query_file,
      provenance: {
        source_run: plan.preserved.source_run,
        source_query_file_sha256: receipt.query_file_sha256,
      },
    });
  }
  return seeded;
}

function queryHash(entry, contract) {
  return sha256({
    query: buildAdvancedSearchQuery(entry),
    contract,
    page_size: CONGRESS_PAGE_SIZE,
    page_sort: CONGRESS_PAGE_SORT,
  });
}

async function validateCompletedQuery(
  outDir,
  entry,
  contract,
  registryHash,
) {
  const queryFile = path.join(outDir, "queries", `${entry.id}.json`);
  let artifact;
  try {
    artifact = await readJson(queryFile);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
  const records = artifact.records ?? [];
  const pages = artifact.pages ?? [];
  const keys = new Set(records.map((record) => record.canonical_key));
  const valid =
    artifact.schema === QUERY_SCHEMA &&
    artifact.query_id === entry.id &&
    artifact.registry_sha256 === registryHash &&
    artifact.query_sha256 === queryHash(entry, contract) &&
    artifact.advanced_search_query === buildAdvancedSearchQuery(entry) &&
    artifact.records_sha256 === sha256(records) &&
    artifact.result_total === records.length &&
    artifact.unique_record_count === keys.size &&
    keys.size === records.length &&
    artifact.page_count === pages.length &&
    Object.values(artifact.validation ?? {}).every((value) => value === true) &&
    (artifact.result_total !== 0 ||
      artifact.zero_result_surface_validation === STRICT_ZERO_SURFACE);
  if (!valid) return null;
  const concatenated = [];
  for (const [index, reference] of pages.entries()) {
    let page;
    try {
      page = await readJson(path.join(outDir, reference.file));
    } catch {
      return null;
    }
    if (
      page.schema !== PAGE_SCHEMA ||
      page.query_id !== entry.id ||
      page.page_number !== index + 1 ||
      page.records_sha256 !== sha256(page.records ?? []) ||
      page.records_sha256 !== reference.records_sha256 ||
      page.records?.length !== reference.record_count ||
      JSON.stringify(page.result_range) !==
        JSON.stringify(reference.result_range) ||
      Object.values(page.validation ?? {}).some((value) => value !== true)
    ) {
      return null;
    }
    concatenated.push(...page.records);
  }
  if (sha256(concatenated) !== artifact.records_sha256) return null;
  return {
    artifact,
    query_file: path
      .relative(outDir, queryFile)
      .split(path.sep)
      .join("/"),
    provenance: { resume_validated: true },
  };
}

async function writeNewEvidence(file, value, equivalent) {
  try {
    const prior = await readJson(file);
    if (!equivalent(prior, value)) {
      throw new Error(`Existing evidence differs; refusing overwrite: ${file}`);
    }
    return prior;
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  await writeJsonAtomicWithRetry(file, value);
  return value;
}

function pageEquivalent(left, right) {
  return Boolean(
    left?.schema === PAGE_SCHEMA &&
      left.query_id === right.query_id &&
      left.page_number === right.page_number &&
      left.result_total === right.result_total &&
      left.records_sha256 === right.records_sha256 &&
      JSON.stringify(left.result_range) === JSON.stringify(right.result_range) &&
      Object.values(left.validation ?? {}).every((value) => value === true),
  );
}

function queryEquivalent(left, right) {
  return Boolean(
    left?.schema === QUERY_SCHEMA &&
      left.query_id === right.query_id &&
      left.query_sha256 === right.query_sha256 &&
      left.records_sha256 === right.records_sha256 &&
      left.result_total === right.result_total &&
      JSON.stringify(left.pages) === JSON.stringify(right.pages) &&
      Object.values(left.validation ?? {}).every((value) => value === true),
  );
}

async function writeFailureReceipt(outDir, value) {
  const receipt = {
    schema: "congress-identifier-fast-failure-v1",
    failed_at: isoNow(),
    ...value,
  };
  const hash = sha256(receipt);
  const file = path.join(outDir, "failure_receipts", `${hash}.json`);
  await fs.mkdir(path.dirname(file), { recursive: true });
  return writeNewEvidence(
    file,
    { ...receipt, receipt_sha256: hash },
    (left, right) => left.receipt_sha256 === right.receipt_sha256,
  );
}

function buildUnion(queryResults) {
  const recordMap = new Map();
  const memberships = [];
  for (const { artifact } of queryResults) {
    for (const sourceRecord of artifact.records) {
      memberships.push({
        query_id: artifact.query_id,
        ...queryMetadata(artifact),
        canonical_key: sourceRecord.canonical_key,
        source_page: sourceRecord.source_page,
        source_rank: sourceRecord.source_rank,
        result_url: sourceRecord.result_url,
      });
      if (!recordMap.has(sourceRecord.canonical_key)) {
        const unionRecord = { ...sourceRecord };
        delete unionRecord.source_query_id;
        delete unionRecord.source_page;
        delete unionRecord.source_rank;
        unionRecord.matched_scoring_categories = [];
        unionRecord.matched_monitor_categories = [];
        unionRecord.matched_coverage_probes = [];
        recordMap.set(sourceRecord.canonical_key, unionRecord);
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

async function collectQuery({
  parserPage,
  fastPath,
  entry,
  contract,
  registryHash,
  outDir,
  onAcceptedPage,
}) {
  const query = buildAdvancedSearchQuery(entry);
  const boundQueryHash = queryHash(entry, contract);
  const records = [];
  const pages = [];
  let expectedTotal = null;
  let expectedPageCount = null;
  let singleResult = null;
  let firstLoadedUrl = null;
  for (let pageNumber = 1; ; pageNumber += 1) {
    if (expectedPageCount !== null && pageNumber > expectedPageCount) break;
    const requestedUrl = buildAdvancedSearchPageUrl(
      entry,
      contract,
      pageNumber,
    );
    const fetched = await fastPath.fetch(requestedUrl);
    let extracted;
    let selectedTransport = "browser_context_http";
    let selectedResponse = fetched;
    try {
      if (fetched.kind !== "accepted") {
        throw new Error(`HTTP surface quarantined: ${fetched.reason}`);
      }
      extracted = await parseAdvancedSearchHttpBody(parserPage, {
        requestedUrl,
        loadedUrl: fetched.loaded_url,
        html: fetched.body,
        entry,
        contract,
        pageNumber,
        expectedTotal,
      });
    } catch (httpError) {
      let browserResponse;
      let browserBody;
      let browserLoadedUrl;
      try {
        browserResponse = await parserPage.goto(requestedUrl, {
          waitUntil: "domcontentloaded",
          timeout: 90_000,
        });
        browserLoadedUrl = parserPage.url();
        browserBody = await parserPage.content();
        const browserEnvelope = validateCongressHtmlEnvelope({
          requestedUrl,
          loadedUrl: browserLoadedUrl,
          status: browserResponse?.status() ?? null,
          headers: browserResponse?.headers() ?? {},
          body: browserBody,
        });
        if (!browserEnvelope.accepted) {
          throw new Error(
            `Browser fallback envelope rejected: ${browserEnvelope.failed.join(",")}`,
          );
        }
        extracted = await parseAdvancedSearchHttpBody(parserPage, {
          requestedUrl,
          loadedUrl: browserLoadedUrl,
          html: browserBody,
          entry,
          contract,
          pageNumber,
          expectedTotal,
          dualTransportRedirectMatch:
            fetched.kind === "accepted" &&
            fetched.status >= 200 &&
            fetched.status < 300 &&
            (browserResponse?.status() ?? 0) >= 200 &&
            (browserResponse?.status() ?? 0) < 300 &&
            sameSessionBoundSingletonRedirect(
              fetched.loaded_url,
              browserLoadedUrl,
            ),
        });
        selectedTransport = "browser_context_navigation_fallback";
        selectedResponse = {
          kind: "accepted",
          status: browserResponse?.status() ?? null,
          loaded_url: browserLoadedUrl,
          body_sha256: sha256(browserBody),
          body_byte_length: Buffer.byteLength(browserBody, "utf8"),
          validation: browserEnvelope.validation,
          attempts: [],
          browser_fallback_from: {
            http_error: httpError.message,
            http_transport:
              fetched.kind === "accepted"
                ? {
                    status: fetched.status,
                    loaded_url: fetched.loaded_url,
                    body_sha256: fetched.body_sha256,
                    body_byte_length: fetched.body_byte_length,
                    validation: fetched.validation,
                  }
                : fetched,
          },
        };
      } catch (browserError) {
        const error = new Error(
          `HTTP and immediate browser fallback both failed: ` +
            `http=${httpError.message}; browser=${browserError.message}`,
        );
        error.transport = {
          http:
            fetched.kind === "accepted"
              ? {
                  status: fetched.status,
                  loaded_url: fetched.loaded_url,
                  body_sha256: fetched.body_sha256,
                  body_byte_length: fetched.body_byte_length,
                  validation: fetched.validation,
                  attempts: fetched.attempts,
                }
              : fetched,
          browser: {
            status: browserResponse?.status() ?? null,
            loaded_url: browserLoadedUrl ?? null,
            body_sha256:
              browserBody === undefined ? null : sha256(browserBody),
            error: browserError.message,
          },
        };
        error.requested_url = requestedUrl;
        throw error;
      }
    }
    const response = selectedResponse;
    if (pageNumber === 1) firstLoadedUrl = response.loaded_url;
    if (pageNumber === 1) {
      expectedTotal = extracted.range.total;
      expectedPageCount =
        extracted.result_transport === "single_result_redirect"
          ? 1
          : Math.max(1, Math.ceil(expectedTotal / CONGRESS_PAGE_SIZE));
      singleResult = extracted.single_result_redirect;
    }
    const normalized = extracted.records;
    const validation = {
      ...extracted.surface_validation,
      total_matches_query: extracted.range.total === expectedTotal,
      exact_page_count:
        normalized.length ===
        Math.min(
          CONGRESS_PAGE_SIZE,
          expectedTotal - (pageNumber - 1) * CONGRESS_PAGE_SIZE,
        ),
      unique_within_page:
        new Set(normalized.map((record) => record.canonical_key)).size ===
        normalized.length,
    };
    if (Object.values(validation).some((value) => value !== true)) {
      throw new Error(
        `Final page validation failed: ${JSON.stringify(validation)}`,
      );
    }
    const pageFile = path.join(
      outDir,
      "raw",
      "pages",
      entry.id,
      `page_${String(pageNumber).padStart(3, "0")}.json`,
    );
    const pageArtifact = {
      schema: PAGE_SCHEMA,
      collected_at: isoNow(),
      primary_instrument: "Congress.gov Advanced Search - Legislation",
      registry_sha256: registryHash,
      query_id: entry.id,
      query_sha256: boundQueryHash,
      advanced_search_query: query,
      search_url: buildAdvancedSearchPageUrl(entry, contract, 1),
      resolved_search_url: response.loaded_url,
      result_transport: extracted.result_transport,
      single_result_redirect: extracted.single_result_redirect,
      requested_url: requestedUrl,
      loaded_url: response.loaded_url,
      page_number: pageNumber,
      page_size: CONGRESS_PAGE_SIZE,
      page_sort: CONGRESS_PAGE_SORT,
      result_range: extracted.range,
      result_total: expectedTotal,
      records_sha256: sha256(normalized),
      validation,
      transport_provenance: {
        schema: "congress-advanced-context-http-v1",
        selected_transport: selectedTransport,
        cdp_context_reused: true,
        cookie_values_read: false,
        cookie_values_persisted: false,
        fixed_retry_waits: false,
        http_attempt_count: fetched.attempts?.length ?? 0,
        response_body_sha256: response.body_sha256,
        response_body_byte_length: response.body_byte_length,
        envelope_validation: response.validation,
        browser_fallback_from: response.browser_fallback_from ?? null,
      },
      records: normalized,
    };
    const stored = await writeNewEvidence(
      pageFile,
      pageArtifact,
      pageEquivalent,
    );
    pages.push({
      file: path.relative(outDir, pageFile).split(path.sep).join("/"),
      page_number: stored.page_number,
      result_range: stored.result_range,
      record_count: stored.records.length,
      records_sha256: stored.records_sha256,
    });
    records.push(...stored.records);
    await onAcceptedPage({
      query_id: entry.id,
      page_number: pageNumber,
      page_count: expectedPageCount,
      record_count: stored.records.length,
    });
  }
  const uniqueKeys = new Set(records.map((record) => record.canonical_key));
  if (records.length !== expectedTotal || uniqueKeys.size !== expectedTotal) {
    throw new Error(
      `Exact query reconciliation failed for ${entry.id}: ` +
        `site=${expectedTotal}, rows=${records.length}, unique=${uniqueKeys.size}`,
    );
  }
  const artifact = {
    schema: QUERY_SCHEMA,
    completed_at: isoNow(),
    primary_instrument: "Congress.gov Advanced Search - Legislation",
    query_id: entry.id,
    ...queryMetadata(entry),
    registry_sha256: registryHash,
    query_sha256: boundQueryHash,
    advanced_search_query: query,
    result_transport:
      singleResult ? "single_result_redirect" : "result_list",
    single_result_redirect: singleResult,
    zero_result_surface_validation:
      expectedTotal === 0 ? STRICT_ZERO_SURFACE : null,
    search_url: buildAdvancedSearchPageUrl(entry, contract, 1),
    resolved_search_url:
      firstLoadedUrl ?? buildAdvancedSearchPageUrl(entry, contract, 1),
    result_total: expectedTotal,
    unique_record_count: uniqueKeys.size,
    page_count: pages.length,
    counts_by_congress: summarizeCounts(records, "congress"),
    counts_by_type: summarizeCounts(records, "type"),
    records_sha256: sha256(records),
    pages,
    validation: {
      exact_result_total: records.length === expectedTotal,
      unique_result_total: uniqueKeys.size === expectedTotal,
      every_page_exact: true,
      single_result_redirect_proven:
        !singleResult ||
        Object.values(singleResult.proof ?? {}).every((value) => value === true),
    },
    records,
  };
  const queryFile = path.join(outDir, "queries", `${entry.id}.json`);
  const stored = await writeNewEvidence(
    queryFile,
    artifact,
    queryEquivalent,
  );
  return {
    artifact: stored,
    query_file: path
      .relative(outDir, queryFile)
      .split(path.sep)
      .join("/"),
    provenance: {
      transport: "visible_chrome_cdp_browser_context_request",
      cookie_values_persisted: false,
    },
  };
}

async function acquireLock(outDir) {
  await fs.mkdir(outDir, { recursive: true });
  const file = path.join(outDir, "identifier-fast.lock.json");
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      const handle = await fs.open(file, "wx");
      await handle.writeFile(
        `${JSON.stringify({ pid: process.pid, started_at: isoNow() }, null, 2)}\n`,
      );
      return { file, handle };
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
      let prior;
      try {
        prior = await readJson(file);
      } catch {
        throw new Error(`Identifier fast output has an unreadable lock: ${file}`);
      }
      let alive = false;
      if (Number.isInteger(prior.pid) && prior.pid > 0) {
        try {
          process.kill(prior.pid, 0);
          alive = true;
        } catch (processError) {
          alive = processError.code === "EPERM";
        }
      }
      if (alive) {
        throw new Error(
          `Identifier fast output is locked by live PID ${prior.pid}`,
        );
      }
      await fs.rm(file, { force: true });
    }
  }
  throw new Error(`Could not acquire identifier fast output lock: ${file}`);
}

async function releaseLock(lock) {
  if (!lock) return;
  await lock.handle.close().catch(() => {});
  await fs.rm(lock.file, { force: true }).catch(() => {});
}

export async function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  const planPath = assertWithinRoot(args.plan);
  const outDir = assertWithinRoot(args.out);
  const planText = await fs.readFile(planPath, "utf8");
  const plan = JSON.parse(planText);
  plan.__file = planPath;
  const sidecar = `${planPath}.sha256`;
  const sidecarHash = (await fs.readFile(sidecar, "ascii")).trim().split(/\s+/)[0];
  if (
    plan.schema !== "acu-legislation-identifier-shard-plan-v1" ||
    sidecarHash !== sha256(planText) ||
    plan.registry.query_count !== 210 ||
    plan.preserved.query_count !== 55 ||
    plan.partition.collection_query_count !== 155 ||
    plan.partition.disjoint !== true ||
    plan.partition.complete !== true
  ) {
    throw new Error("Identifier shard plan failed exact 55+155 validation");
  }
  const registryPath = resolvePlanPath(
    path.dirname(planPath),
    plan.registry.path,
  );
  const registryText = await fs.readFile(registryPath, "utf8");
  const registry = JSON.parse(registryText);
  const registryHash = sha256(registryText);
  const entries = registry.categories ?? [];
  if (
    registryHash !== plan.registry.sha256 ||
    entries.length !== 210 ||
    new Set(entries.map((entry) => entry.id)).size !== 210
  ) {
    throw new Error("Identifier registry does not match the exact plan");
  }
  const registryIds = entries.map((entry) => entry.id);
  const plannedIds = [
    ...plan.preserved.queries.map((query) => query.query_id),
    ...plan.shards.flatMap((shard) => shard.query_ids),
  ];
  if (
    plannedIds.length !== 210 ||
    new Set(plannedIds).size !== 210 ||
    registryIds.some((queryId) => !plannedIds.includes(queryId)) ||
    plannedIds.some((queryId) => !registryIds.includes(queryId))
  ) {
    throw new Error("Shard plan is not the exact 210-query registry partition");
  }

  const lock = await acquireLock(outDir);
  const progressFile = path.join(outDir, "progress.json");
  const partialManifestFile = path.join(outDir, "manifest.partial.json");
  const startedMs = Date.now();
  const active = new Map();
  const results = new Map();
  const failures = [];
  let acceptedLiveQueries = 0;
  let acceptedPages = 0;
  let progressQueue = Promise.resolve();
  const writeProgress = (status) => {
    progressQueue = progressQueue.then(async () => {
      const elapsedMinutes = Math.max(
        (Date.now() - startedMs) / 60_000,
        1 / 60,
      );
      const queriesPerMinute = acceptedLiveQueries / elapsedMinutes;
      const remaining = 210 - results.size;
      await writeJsonAtomicWithRetry(progressFile, {
        schema: "congress-identifier-fast-progress-v1",
        status,
        completed_queries: results.size,
        total_queries: 210,
        active_workers: active.size,
        active_worker_ids: [...active.entries()].map(([worker, queryId]) => ({
          worker,
          query_id: queryId,
        })),
        accepted_pages: acceptedPages,
        queries_per_minute: Number(queriesPerMinute.toFixed(3)),
        failure_count: failures.length,
        eta_seconds:
          queriesPerMinute > 0
            ? Math.ceil((remaining / queriesPerMinute) * 60)
            : null,
        updated_utc: isoNow(),
        cookie_values_persisted: false,
        fixed_retry_waits: false,
      });
    });
    return progressQueue;
  };
  let manifestQueue = Promise.resolve();
  const writePartialManifest = (status) => {
    manifestQueue = manifestQueue.then(async () => {
      const queryEntries = entries
        .filter((entry) => results.has(entry.id))
        .map((entry) => {
          const result = results.get(entry.id);
          return queryManifestEntry(result.artifact, result.query_file);
        });
      await writeJsonAtomicWithRetry(partialManifestFile, {
        schema: MANIFEST_SCHEMA,
        status,
        started_at: new Date(startedMs).toISOString(),
        primary_instrument: "Congress.gov Advanced Search - Legislation",
        quorum_used: false,
        registry_file: path
          .relative(ROOT, registryPath)
          .split(path.sep)
          .join("/"),
        registry_sha256: registryHash,
        collection_contract: registry.collection_contract,
        selected_query_ids: entries.map((entry) => entry.id),
        queries: queryEntries,
        failure_count: failures.length,
      });
    });
    return manifestQueue;
  };

  let browser;
  const parserPages = [];
  try {
    const preserved = await seedPreserved(plan, outDir);
    const entryById = new Map(entries.map((entry) => [entry.id, entry]));
    for (const seeded of preserved) {
      const entry = entryById.get(seeded.artifact.query_id);
      if (!entry) {
        throw new Error(
          `Preserved query is absent from registry: ${seeded.artifact.query_id}`,
        );
      }
      const validated = await validateCompletedQuery(
        outDir,
        entry,
        registry.collection_contract,
        registryHash,
      );
      if (!validated) {
        throw new Error(
          `Preserved query/page evidence failed validation: ${entry.id}`,
        );
      }
      validated.provenance = {
        ...seeded.provenance,
        copied_page_and_query_artifacts_revalidated: true,
      };
      results.set(entry.id, validated);
    }
    await writeProgress("running");
    await writePartialManifest("running");

    if (args.resume) {
      for (const entry of entries) {
        if (results.has(entry.id)) continue;
        const resumed = await validateCompletedQuery(
          outDir,
          entry,
          registry.collection_contract,
          registryHash,
        );
        if (resumed) results.set(entry.id, resumed);
      }
      await writeProgress("running");
      await writePartialManifest("running");
    }

    const pending = entries.filter((entry) => !results.has(entry.id));
    if (pending.length) {
      browser = await chromium.connectOverCDP(args.endpoint);
      const context = browser.contexts()[0];
      if (!context) throw new Error("Visible Chrome CDP has no BrowserContext");
      for (let index = 0; index < Math.min(args.workers, pending.length); index += 1) {
        parserPages.push(await context.newPage());
      }
      const fastPath = createCongressContextHttpFastPath({
        requestContext: context.request,
        scheduleRequest: async () => {},
        maxAttempts: 1,
        retryBaseMs: 0,
        dedupeEntries: 6,
        validateResponse: validateCongressHtmlEnvelope,
      });
      let cursor = 0;
      const worker = async (parserPage, workerNumber) => {
        while (true) {
          const index = cursor;
          cursor += 1;
          if (index >= pending.length) return;
          const entry = pending[index];
          active.set(workerNumber, entry.id);
          await writeProgress("running");
          try {
            const result = await collectQuery({
              parserPage,
              fastPath,
              entry,
              contract: registry.collection_contract,
              registryHash,
              outDir,
              onAcceptedPage: async () => {
                acceptedPages += 1;
                await writeProgress("running");
              },
            });
            results.set(entry.id, result);
            acceptedLiveQueries += 1;
            await writePartialManifest("running");
          } catch (error) {
            const failure = {
              query_id: entry.id,
              worker: workerNumber,
              name: error.name ?? "Error",
              message: error.message ?? String(error),
              requested_url: error.requested_url ?? null,
              transport: error.transport ?? null,
            };
            failures.push(failure);
            await writeFailureReceipt(outDir, failure);
          } finally {
            active.delete(workerNumber);
            await writeProgress("running");
          }
        }
      };
      await Promise.all(
        parserPages.map((page, index) => worker(page, index + 1)),
      );
    }

    if (failures.length || results.size !== 210) {
      await writePartialManifest("failed");
      await writeProgress("failed");
      throw new Error(
        `Fast identifier collection incomplete: ${results.size}/210; ` +
          `${failures.length} failure(s)`,
      );
    }

    const orderedResults = entries.map((entry) => results.get(entry.id));
    const union = buildUnion(orderedResults);
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
    await writeNdjson(gapsFile, []);
    await writeNdjson(exclusionsFile, []);
    await writeJsonAtomicWithRetry(scopeFile, {
      automated_semantic_exclusions: 0,
      candidate_only: true,
      identifier_membership_non_dispositive: true,
      later_review_required: true,
    });
    const manifest = {
      schema: MANIFEST_SCHEMA,
      status: "complete",
      started_at: new Date(startedMs).toISOString(),
      completed_at: isoNow(),
      primary_instrument: "Congress.gov Advanced Search - Legislation",
      quorum_used: false,
      registry_file: path
        .relative(ROOT, registryPath)
        .split(path.sep)
        .join("/"),
      registry_sha256: registryHash,
      collection_contract: registry.collection_contract,
      page_contract: {
        page_size: CONGRESS_PAGE_SIZE,
        page_sort: CONGRESS_PAGE_SORT,
        exact_reconciliation_required: true,
        discovery_transport:
          "Visible Chrome CDP BrowserContext APIRequestContext",
      },
      transport_policy: {
        worker_count: args.workers,
        cookie_values_read: false,
        cookie_values_persisted: false,
        fixed_retry_waits: false,
        one_http_attempt_per_page: true,
        strict_unsafe_surface_failure: true,
      },
      selected_query_ids: entries.map((entry) => entry.id),
      queries: orderedResults.map((result) =>
        queryManifestEntry(result.artifact, result.query_file),
      ),
      union: {
        unique_record_count: union.records.length,
        membership_count: union.memberships.length,
        counts_by_congress: summarizeCounts(union.records, "congress"),
        counts_by_type: summarizeCounts(union.records, "type"),
        scoring_candidate_count: 0,
        monitor_candidate_count: union.records.length,
        recall_probe_count: 0,
        records_file: path
          .relative(outDir, recordsFile)
          .split(path.sep)
          .join("/"),
        records_sha256: sha256(union.records),
        memberships_file: path
          .relative(outDir, membershipsFile)
          .split(path.sep)
          .join("/"),
        memberships_sha256: sha256(union.memberships),
      },
      visibility: {
        coverage_gap_ledger: path
          .relative(outDir, gapsFile)
          .split(path.sep)
          .join("/"),
        gap_count: 0,
        gap_ledger_sha256: sha256([]),
        exclusion_ledger: path
          .relative(outDir, exclusionsFile)
          .split(path.sep)
          .join("/"),
        exclusion_count: 0,
        exclusion_ledger_sha256: sha256([]),
        scope_contract: path
          .relative(outDir, scopeFile)
          .split(path.sep)
          .join("/"),
      },
    };
    await writeNewEvidence(
      path.join(outDir, "manifest.json"),
      manifest,
      (left, right) =>
        left.schema === right.schema &&
        left.status === "complete" &&
        left.registry_sha256 === right.registry_sha256 &&
        left.union?.records_sha256 === right.union.records_sha256 &&
        left.union?.memberships_sha256 === right.union.memberships_sha256 &&
        JSON.stringify(left.selected_query_ids) ===
          JSON.stringify(right.selected_query_ids) &&
        JSON.stringify(left.queries) === JSON.stringify(right.queries),
    );
    await writeProgress("complete");
    return manifest;
  } finally {
    await Promise.all(parserPages.map((page) => page.close().catch(() => {})));
    // Do not call browser.close(): this process does not own the visible Chrome.
    await releaseLock(lock);
  }
}

const isDirectExecution =
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isDirectExecution) {
  await main();
  // The CDP connection is borrowed from a user-owned visible Chrome. Do not
  // close that browser, but do terminate this process after all atomic
  // evidence and progress receipts have been written.
  process.exit(0);
}

export {
  buildUnion,
  collectQuery,
  parseArgs,
  queryEquivalent,
  queryManifestEntry,
  validateCompletedQuery,
};
