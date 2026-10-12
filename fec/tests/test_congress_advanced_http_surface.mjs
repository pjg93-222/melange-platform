import assert from "node:assert/strict";
import test, { after, before } from "node:test";
import { createRequire } from "node:module";

import {
  buildAdvancedSearchPageUrl,
  parseAdvancedSearchHttpBody,
} from "../lib/congress_advanced_http_surface.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const CHROME =
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";

const entry = {
  id: "fair_exams",
  advanced_search_query: '("FAIR Exams Act")',
  query_terms: [{ query: '"FAIR Exams Act"' }],
};
const contract = {
  congresses: [116, 117, 118, 119],
  legislation_types: [
    "hr",
    "hres",
    "hjres",
    "hconres",
    "hamdt",
    "s",
    "sres",
    "sjres",
    "sconres",
    "samdt",
  ],
  required_window_start: "2019-07-25",
  required_window_end: "2026-07-25",
};

let browser;
let page;

before(async () => {
  browser = await chromium.launch({
    executablePath: CHROME,
    headless: true,
  });
  page = await browser.newPage();
});

after(async () => {
  await browser?.close();
});

function resultCard({
  number = 940,
  title = "FAIR Exams Act",
  actionDate = "02/04/2025",
} = {}) {
  return `
    <li class="expanded">
      <span class="visualIndicator">Bill</span>
      <h2 class="result-heading">
        <a href="/bill/119th-congress/house-bill/${number}">H.R. ${number}</a>
        - 119th Congress
      </h2>
      <span class="result-title">${title}</span>
      <div class="result-item">
        Sponsor:
        <a href="/member/j-french-hill/H001072">
          Rep. Hill, J. French [R-AR-2]
        </a>
        (Introduced ${actionDate}) Cosponsors: (10)
      </div>
      <div class="result-item">Committees: House - Financial Services</div>
      <div class="result-item">
        Latest Action: House - 07/25/2025 Placed on the Union Calendar.
      </div>
      <div class="result-tracker">
        <span class="hide_fromsighted">This bill has the status Introduced</span>
      </div>
    </li>`;
}

function listHtml({ range, cards = [] }) {
  return `<!doctype html>
    <html>
      <head><title>Advanced Search | Congress.gov</title></head>
      <body>
        <main id="main"><h1>${range}</h1>
          <ol class="basic-search-results-lists">${cards.join("")}</ol>
        </main>
      </body>
    </html>`;
}

async function parse({
  html,
  pageNumber = 1,
  expectedTotal = null,
  requestedUrl = buildAdvancedSearchPageUrl(entry, contract, pageNumber),
  loadedUrl = requestedUrl,
  dualTransportRedirectMatch = false,
}) {
  return parseAdvancedSearchHttpBody(page, {
    requestedUrl,
    loadedUrl,
    html,
    entry,
    contract,
    pageNumber,
    expectedTotal,
    dualTransportRedirectMatch,
  });
}

test("accepts a strictly bound nonzero final pagination page", async () => {
  const result = await parse({
    pageNumber: 2,
    expectedTotal: 251,
    html: listHtml({
      range: "Search Results 251-251 of 251",
      cards: [resultCard()],
    }),
  });
  assert.equal(result.result_transport, "result_list");
  assert.deepEqual(result.range, {
    start: 251,
    end: 251,
    total: 251,
    header: "Search Results 251-251 of 251",
  });
  assert.equal(result.records.length, 1);
  assert.equal(result.records[0].canonical_key, "119:hr:940");
  assert.equal(result.records[0].source_page, 2);
  assert.equal(result.records[0].source_rank, 251);
  assert.ok(Object.values(result.surface_validation).every(Boolean));
});

test("accepts strict page-one zero only with no result rows", async () => {
  const html = `<!doctype html><html><body>
    <main id="main">No results found</main>
    <main id="main">No results found Back to previous page</main>
  </body></html>`;
  const result = await parse({ html, expectedTotal: 0 });
  assert.equal(result.range.total, 0);
  assert.deepEqual(result.records, []);
  assert.equal(result.surface_validation.strict_zero_surface, true);
});

test("rejects summary-in-progress even beside a stale parseable result", async () => {
  const html = `<!doctype html><html><body>
    <main id="main">Search Results 1-1 of 1</main>
    <main id="main">A summary is in progress.</main>
    <ol class="basic-search-results-lists">${resultCard()}</ol>
  </body></html>`;
  await assert.rejects(
    parse({ html, expectedTotal: 1 }),
    /result summary is still in progress/i,
  );
});

test("rejects a challenge or other non-result main surface", async () => {
  const html = `<!doctype html><html><body>
    <main id="main">
      Performing security verification. Verify you are human.
    </main>
  </body></html>`;
  await assert.rejects(
    parse({ html }),
    /invalid #main result surface|could not parse congress\.gov result range/i,
  );
});

test("rejects requested-versus-loaded query, page, size, and sort drift", async () => {
  const requestedUrl = buildAdvancedSearchPageUrl(entry, contract, 2);
  const html = listHtml({
    range: "Search Results 251-251 of 251",
    cards: [resultCard()],
  });
  for (const [label, mutate] of [
    ["query", (url) => url.searchParams.set("enterTerms", '("OTHER")')],
    ["page", (url) => url.searchParams.set("page", "1")],
    ["page size", (url) => url.searchParams.set("pageSize", "100")],
    ["sort", (url) => url.searchParams.set("pageSort", "relevance")],
  ]) {
    const loaded = new URL(requestedUrl);
    mutate(loaded);
    await assert.rejects(
      parse({
        html,
        pageNumber: 2,
        expectedTotal: 251,
        requestedUrl,
        loadedUrl: loaded.href,
      }),
      /HTTP surface validation failed/i,
      label,
    );
  }
});

test("rejects duplicate canonical rows and missing rows", async () => {
  await assert.rejects(
    parse({
      expectedTotal: 2,
      html: listHtml({
        range: "Search Results 1-2 of 2",
        cards: [resultCard(), resultCard()],
      }),
    }),
    /"unique_within_page":false/i,
  );
  await assert.rejects(
    parse({
      expectedTotal: 2,
      html: listHtml({
        range: "Search Results 1-2 of 2",
        cards: [resultCard()],
      }),
    }),
    /"exact_page_count":false/i,
  );
});

test("accepts a singleton redirect only with full search-session proof", async () => {
  const requestedUrl = buildAdvancedSearchPageUrl(entry, contract, 1);
  const loaded = new URL(
    "https://www.congress.gov/index.php/bill/119th-congress/house-bill/940",
  );
  loaded.searchParams.set("hl", entry.advanced_search_query);
  loaded.searchParams.set("s", "9");
  loaded.searchParams.set("r", "1");
  const html = `<!doctype html><html>
    <head>
      <title>H.R.940 - 119th Congress (2025-2026): FAIR Exams Act | Congress.gov | Library of Congress</title>
    </head>
    <body>
      <p>EDIT THIS SEARCH</p>
      <p>PREVIOUS (disabled)</p>
      <p>NEXT (disabled)</p>
      <p>H.R.940 - FAIR Exams Act</p>
      <p>119th Congress (2025-2026)</p>
      <p>Sponsor: <a href="/member/j-french-hill/H001072">Rep. Hill, J. French [R-AR-2]</a> (Introduced 02/04/2025)</p>
      <p>Cosponsors (10)</p>
      <p>Committees: House - Financial Services</p>
      <p>Latest Action: House - 07/25/2025 Placed on the Union Calendar.</p>
      <p>This bill has the status Introduced Here are the steps for Status of Legislation:</p>
    </body>
  </html>`;
  const result = await parse({
    html,
    requestedUrl,
    loadedUrl: loaded.href,
    expectedTotal: 1,
  });
  assert.equal(result.result_transport, "single_result_redirect");
  assert.equal(result.records.length, 1);
  assert.equal(result.records[0].canonical_key, "119:hr:940");
  assert.ok(
    Object.values(
      result.single_result_redirect.extraction_validation,
    ).every(Boolean),
  );

  const unproven = new URL(loaded);
  unproven.searchParams.delete("s");
  await assert.rejects(
    parse({
      html,
      requestedUrl,
      loadedUrl: unproven.href,
      expectedTotal: 1,
    }),
    /invalid #main result surface|could not parse congress\.gov result range/i,
  );
});

test("accepts current control-free singleton only with dual-transport agreement", async () => {
  const requestedUrl = buildAdvancedSearchPageUrl(entry, contract, 1);
  const loaded = new URL(
    "https://www.congress.gov/index.php/bill/119th-congress/house-bill/940",
  );
  loaded.searchParams.set("hl", entry.advanced_search_query);
  loaded.searchParams.set("s", "9");
  loaded.searchParams.set("r", "1");
  const html = `<!doctype html><html>
    <head>
      <title>H.R.940 - 119th Congress (2025-2026): FAIR Exams Act | Congress.gov | Library of Congress</title>
    </head>
    <body>
      <p>H.R.940 - FAIR Exams Act</p>
      <p>119th Congress (2025-2026)</p>
      <p>Sponsor: <a href="/member/j-french-hill/H001072">Rep. Hill, J. French [R-AR-2]</a> (Introduced 02/04/2025)</p>
      <p>Cosponsors (10)</p>
    </body>
  </html>`;
  await assert.rejects(
    parse({
      html,
      requestedUrl,
      loadedUrl: loaded.href,
      expectedTotal: 1,
    }),
    /invalid #main result surface|could not parse congress\.gov result range/i,
  );
  const result = await parse({
    html,
    requestedUrl,
    loadedUrl: loaded.href,
    expectedTotal: 1,
    dualTransportRedirectMatch: true,
  });
  assert.equal(result.result_transport, "single_result_redirect");
  assert.equal(result.records[0].canonical_key, "119:hr:940");
  assert.equal(
    result.single_result_redirect.cardinality_proof,
    "dual-transport-session-redirect-v2",
  );
  assert.ok(Object.values(result.single_result_redirect.proof).every(Boolean));
});
