#!/usr/bin/env node
/**
 * Collect ACU legislation from the Congress.gov Advanced Search web workflow.
 *
 * Congress.gov's public API has no keyword-search endpoint.  This collector
 * drives the official Advanced Search - Legislation form and downloads the
 * site's own CSV exports.  It never substitutes title matching or another
 * legislative corpus.
 */

import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";

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
const DOWNLOAD_FIELD_IDS = [
  "latestTitle",
  "sponsor",
  "sponsorParty",
  "dateOfIntroduction",
  "committees",
  "latestAction",
  "latestActionDate",
  "billStatusTracker",
  "numberOfCosponsors",
  "billPolicyArea",
  "dateOffered",
  "dateSubmitted",
  "dateProposed",
  "amendsAmendment",
];

function parseArgs(argv) {
  const args = {
    registry: DEFAULT_REGISTRY,
    out: path.join("audits", "legfix5", "congress_advanced_run_20260725"),
    profile: path.join(os.tmpdir(), "codex-congress-advanced-profile"),
    chrome: DEFAULT_CHROME,
    headless: false,
    delayMs: 750,
    maxExportRows: 500,
    categories: null,
    resume: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const value = argv[i];
    if (value === "--registry") args.registry = argv[++i];
    else if (value === "--out") args.out = argv[++i];
    else if (value === "--profile") args.profile = argv[++i];
    else if (value === "--chrome") args.chrome = argv[++i];
    else if (value === "--delay-ms") args.delayMs = Number(argv[++i]);
    else if (value === "--max-export-rows") {
      args.maxExportRows = Number(argv[++i]);
    }
    else if (value === "--categories") {
      args.categories = new Set(
        argv[++i]
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      );
    } else if (value === "--headless") args.headless = true;
    else if (value === "--resume") args.resume = true;
    else if (value === "--help") {
      console.log(`Usage: node fec/collect/collect_congress_advanced.mjs [options]
  --registry PATH       Search registry JSON
  --out PATH            Output directory within the repository
  --profile PATH        Isolated Chrome profile directory
  --chrome PATH         Chrome executable
  --categories IDS      Comma-separated category ids
  --delay-ms N          Delay between live searches (default 750)
  --max-export-rows N   Proactively split large all-fields CSVs (default 500)
  --headless            Run headless (headed is more reliable with Congress.gov)
  --resume              Reuse completed chunks in the partial manifest`);
      process.exit(0);
    } else {
      throw new Error(`Unknown argument: ${value}`);
    }
  }
  if (!Number.isFinite(args.delayMs) || args.delayMs < 0) {
    throw new Error("--delay-ms must be a non-negative number");
  }
  if (
    !Number.isInteger(args.maxExportRows) ||
    args.maxExportRows < 1 ||
    args.maxExportRows > 2500
  ) {
    throw new Error("--max-export-rows must be an integer from 1 to 2500");
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
    .update(JSON.stringify(value))
    .digest("hex");
}

function isoNow() {
  return new Date().toISOString();
}

function buildQuery(terms) {
  return terms.map((term) => `(${term.query})`).join(" OR ");
}

function parseResultTotal(mainText) {
  const range = mainText.match(/\b\d[\d,]*-\d[\d,]* of ([\d,]+)\b/);
  if (range) return Number(range[1].replaceAll(",", ""));
  const single = mainText.match(/\b1 of 1\b/);
  if (single) return 1;
  if (
    /no results found|no search results|0 results|did not match any documents/i.test(
      mainText,
    )
  ) {
    return 0;
  }
  throw new Error(
    `Could not parse Congress.gov result total from: ${mainText.slice(0, 500)}`,
  );
}

async function waitForAdvancedSearch(page) {
  try {
    await page.waitForFunction(
      () =>
        document.title.includes("Advanced Search") &&
        Boolean(document.querySelector("#advancedSearchLegislation")),
      undefined,
      { timeout: 120_000 },
    );
  } catch (error) {
    const title = await page.title().catch(() => "<unavailable>");
    const url = page.url();
    const body = await page
      .locator("body")
      .innerText()
      .then((value) => value.slice(0, 800))
      .catch(() => "<unavailable>");
    throw new Error(
      `Congress.gov Advanced Search form unavailable after wait; ` +
        `title=${JSON.stringify(title)} url=${url} body=${JSON.stringify(body)}`,
      { cause: error },
    );
  }
}

async function configureAndSearch(page, chunk) {
  await page.goto(ADVANCED_SEARCH, {
    waitUntil: "domcontentloaded",
    timeout: 60_000,
  });
  await waitForAdvancedSearch(page);

  for (const congress of [116, 117, 118, 119]) {
    await page
      .locator(`#congresses${congress}`)
      .setChecked(chunk.congresses.includes(congress));
  }
  await page.locator("#restrictionTypeIncludeBillText").check();
  await page.locator("#enterTerms").fill(buildQuery(chunk.terms));

  for (const [type, id] of Object.entries(LEGISLATION_TYPE_IDS)) {
    await page.locator(`#${id}`).setChecked(chunk.legislationTypes.includes(type));
  }
  await page.locator("#legislationScopePublic").check();
  await page.locator("#legislationScopePrivate").check();

  await Promise.all([
    page.waitForNavigation({
      waitUntil: "domcontentloaded",
      timeout: 90_000,
    }),
    page
      .locator(
        "#advancedSearchLegislation button[type=submit][name=search]",
      )
      .click(),
  ]);
  await page.waitForLoadState("domcontentloaded");

  const mainText = await page.locator("main").innerText();
  return {
    total: parseResultTotal(mainText),
    searchUrl: page.url(),
  };
}

async function downloadResults(page, destination) {
  const link = page.locator("a").filter({ hasText: "Download Results" });
  if ((await link.count()) !== 1) {
    throw new Error(`Expected one Download Results link; found ${await link.count()}`);
  }
  await link.click();

  const dialog = page.locator(".congress-default-modal:visible");
  await dialog.waitFor({ state: "visible", timeout: 15_000 });
  await dialog
    .locator('input[name="fieldListOption"]')
    .evaluateAll(
      (inputs, selectedIds) => {
        for (const input of inputs) {
          input.checked = selectedIds.includes(input.id);
          input.dispatchEvent(new Event("change", { bubbles: true }));
        }
      },
      DOWNLOAD_FIELD_IDS,
    );

  const downloadPromise = page.waitForEvent("download", { timeout: 300_000 });
  await dialog
    .locator("button")
    .filter({ hasText: "Download Results" })
    .click();
  const download = await downloadPromise;
  const failure = await download.failure();
  if (failure) throw new Error(`Congress.gov CSV download failed: ${failure}`);
  await download.saveAs(destination);
}

function splitInHalf(values) {
  const middle = Math.ceil(values.length / 2);
  return [values.slice(0, middle), values.slice(middle)];
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const outDir = assertWithinRoot(args.out);
  const rawDir = path.join(outDir, "raw");
  const partialPath = path.join(outDir, "run_manifest.partial.json");
  const finalPath = path.join(outDir, "run_manifest.json");
  await fs.mkdir(rawDir, { recursive: true });

  const registryPath = path.resolve(ROOT, args.registry);
  const registryText = await fs.readFile(registryPath, "utf8");
  const registry = JSON.parse(registryText);
  const registryHash = crypto
    .createHash("sha256")
    .update(registryText)
    .digest("hex");

  let manifest = {
    schema: "acu-congress-advanced-search-run-v1",
    status: "running",
    started_at: isoNow(),
    registry_path: path
      .relative(ROOT, registryPath)
      .replaceAll(path.sep, "/"),
    registry_sha256: registryHash,
    source_outline_sha256: registry.source.sha256,
    congresses: registry.collection_contract.congresses,
    search_scope: registry.collection_contract.search_scope,
    download_fields: DOWNLOAD_FIELD_IDS,
    output_directory: path.relative(ROOT, outDir).replaceAll(path.sep, "/"),
    chunks: [],
    split_searches: [],
    failures: [],
  };
  if (args.resume) {
    try {
      const prior = JSON.parse(await fs.readFile(partialPath, "utf8"));
      if (prior.registry_sha256 !== registryHash) {
        throw new Error("Cannot resume: registry SHA-256 changed");
      }
      manifest = prior;
      manifest.status = "running";
      manifest.recovered_failures = [
        ...(manifest.recovered_failures || []),
        ...(manifest.failures || []),
      ];
      manifest.failures = [];
      manifest.split_searches = manifest.split_searches || [];
      manifest.download_fields = DOWNLOAD_FIELD_IDS;
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }

  const completedByKey = new Map(
    manifest.chunks.map((chunk) => [chunk.chunk_key, chunk]),
  );
  const splitByKey = new Map(
    manifest.split_searches.map((split) => [split.chunk_key, split]),
  );
  const searchableSections = [
    ...registry.categories.map((category) => ({
      record_kind: "outline_category",
      ...category,
    })),
    ...(registry.coverage_probes || []).map((probe) => ({
      scoring_category: false,
      ...probe,
    })),
  ];
  const categories = searchableSections.filter(
    (category) =>
      !args.categories || args.categories.has(category.id),
  );
  if (args.categories) {
    const found = new Set(categories.map((category) => category.id));
    const missing = [...args.categories].filter((id) => !found.has(id));
    if (missing.length) {
      throw new Error(`Unknown category ids: ${missing.join(", ")}`);
    }
  }

  const context = await chromium.launchPersistentContext(args.profile, {
    executablePath: args.chrome,
    headless: args.headless,
    ignoreDefaultArgs: ["--enable-automation"],
    args: ["--disable-blink-features=AutomationControlled"],
    viewport: { width: 1400, height: 900 },
    acceptDownloads: true,
  });

  const page = context.pages()[0] || (await context.newPage());
  const downloadLimit = registry.collection_contract.download_limit;

  async function savePartial() {
    await fs.writeFile(
      partialPath,
      JSON.stringify(manifest, null, 2) + "\n",
      "utf8",
    );
  }

  async function collectChunk(category, chunk, lineage) {
    const descriptor = {
      category_id: category.id,
      congresses: [...chunk.congresses].sort(),
      legislation_types: [...chunk.legislationTypes].sort(),
      terms: chunk.terms.map((term) => term.query),
    };
    const chunkKey = stableHash(descriptor);
    const prior = completedByKey.get(chunkKey);
    if (prior) {
      const fileExists =
        !prior.raw_csv ||
        (await fs
          .access(path.join(ROOT, prior.raw_csv))
          .then(() => true)
          .catch(() => false));
      if (fileExists) {
        console.log(
          `RESUME ${category.id} ${chunkKey.slice(0, 10)} total=${prior.result_total}`,
        );
        return;
      }
    }

    if (args.delayMs) await page.waitForTimeout(args.delayMs);
    const result = await configureAndSearch(page, chunk);
    console.log(
      `SEARCH ${category.id} congress=${chunk.congresses.join(",")} ` +
        `types=${chunk.legislationTypes.length} terms=${chunk.terms.length} ` +
        `total=${result.total}`,
    );

    if (result.total > args.maxExportRows) {
      let splitDimension = null;
      if (chunk.congresses.length > 1) {
        splitDimension = "congress";
      } else if (chunk.terms.length > 1) {
        splitDimension = "query_terms";
      } else if (chunk.legislationTypes.length > 1) {
        splitDimension = "legislation_types";
      }
      if (splitDimension) {
        if (!splitByKey.has(chunkKey)) {
          const splitRecord = {
            chunk_key: chunkKey,
            category_id: category.id,
            category_label: category.label,
            record_kind: category.record_kind,
            congresses: chunk.congresses,
            legislation_types: chunk.legislationTypes,
            term_count: chunk.terms.length,
            terms: chunk.terms,
            advanced_search_query: buildQuery(chunk.terms),
            result_total: result.total,
            search_url: result.searchUrl,
            split_dimension: splitDimension,
            collected_at: isoNow(),
          };
          manifest.split_searches.push(splitRecord);
          splitByKey.set(chunkKey, splitRecord);
          await savePartial();
        }
      }
      if (chunk.congresses.length > 1) {
        for (const congress of chunk.congresses) {
          await collectChunk(
            category,
            { ...chunk, congresses: [congress] },
            [...lineage, `congress-${congress}`],
          );
        }
        return;
      }
      if (chunk.terms.length > 1) {
        for (const [index, terms] of splitInHalf(chunk.terms).entries()) {
          await collectChunk(
            category,
            { ...chunk, terms },
            [...lineage, `terms-${index + 1}`],
          );
        }
        return;
      }
      if (chunk.legislationTypes.length > 1) {
        for (const [index, legislationTypes] of splitInHalf(
          chunk.legislationTypes,
        ).entries()) {
          await collectChunk(
            category,
            { ...chunk, legislationTypes },
            [...lineage, `types-${index + 1}`],
          );
        }
        return;
      }
    }
    if (result.total > downloadLimit) {
      throw new Error(
        `Unsplittable result set exceeds ${downloadLimit}: ${JSON.stringify(
          descriptor,
        )}`,
      );
    }

    const record = {
      chunk_key: chunkKey,
      category_id: category.id,
      category_label: category.label,
      scoring_category: category.scoring_category,
      record_kind: category.record_kind,
      lineage,
      congresses: chunk.congresses,
      legislation_types: chunk.legislationTypes,
      term_count: chunk.terms.length,
      terms: chunk.terms,
      advanced_search_query: buildQuery(chunk.terms),
      result_total: result.total,
      search_url: result.searchUrl,
      collected_at: isoNow(),
      raw_csv: null,
      raw_csv_sha256: null,
    };

    if (result.total > 0) {
      const fileName = `${category.id}__${chunkKey.slice(0, 12)}.csv`;
      const destination = path.join(rawDir, fileName);
      await downloadResults(page, destination);
      const bytes = await fs.readFile(destination);
      record.raw_csv = path
        .relative(ROOT, destination)
        .replaceAll(path.sep, "/");
      record.raw_csv_sha256 = crypto
        .createHash("sha256")
        .update(bytes)
        .digest("hex");
    }

    manifest.chunks.push(record);
    completedByKey.set(chunkKey, record);
    await savePartial();
  }

  try {
    for (const category of categories) {
      await collectChunk(
        category,
        {
          congresses: [...registry.collection_contract.congresses],
          legislationTypes: [...registry.collection_contract.legislation_types],
          terms: [...category.query_terms],
        },
        ["root"],
      );
    }
    manifest.status = "complete";
    manifest.completed_at = isoNow();
    manifest.chunk_count = manifest.chunks.length;
    manifest.downloaded_result_rows = manifest.chunks.reduce(
      (sum, chunk) => sum + chunk.result_total,
      0,
    );
    await fs.writeFile(
      finalPath,
      JSON.stringify(manifest, null, 2) + "\n",
      "utf8",
    );
    console.log(
      `COMPLETE chunks=${manifest.chunk_count} rows=${manifest.downloaded_result_rows}`,
    );
  } catch (error) {
    manifest.failures.push({
      at: isoNow(),
      message: error.stack || String(error),
    });
    await savePartial();
    throw error;
  } finally {
    await context.close();
  }
}

await main();
