import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const ROOT = path.resolve(import.meta.dirname, "..");
const sourceReceipt = path.resolve(
  ROOT,
  process.argv[2] ??
    "audits/legfix5/identifier_probe_shards_20260726/shard-01/raw/failure_debug/" +
      "collector_failure_16220_1785103274922_automatic_verification.json",
);
const output = path.resolve(
  ROOT,
  process.argv[3] ??
    "audits/legfix5/cookie_replay_transport_probe_20260726.json",
);
const endpoint = process.argv[4] ?? "http://127.0.0.1:9222";

const source = JSON.parse(await fs.readFile(sourceReceipt, "utf8"));
const targetUrl =
  source.requested_url ??
  source.loaded_url ??
  source.loadedUrl ??
  source.url ??
  source.classification?.loaded_url;
if (!targetUrl?.startsWith("https://www.congress.gov/")) {
  throw new Error("failure receipt does not contain a Congress.gov URL");
}

const browser = await chromium.connectOverCDP(endpoint);
const context = browser.contexts()[0];
if (!context) throw new Error("remote Chrome has no browser context");
const pages = context.pages();
const page = pages.find((candidate) =>
  candidate.url().startsWith("https://www.congress.gov/"),
);
if (!page) throw new Error("remote Chrome has no Congress.gov page");

const userAgent = await page.evaluate(() => navigator.userAgent);
const cookies = await context.cookies("https://www.congress.gov/");
const cookieNames = [...new Set(cookies.map((cookie) => cookie.name))].sort();
const startedAt = new Date().toISOString();
const started = Date.now();
const response = await context.request.get(targetUrl, {
  headers: {
    "User-Agent": userAgent,
    Accept:
      "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    Referer: "https://www.congress.gov/",
  },
  timeout: 120_000,
});
const body = await response.text();
const parsedBodyText = await page.evaluate((html) => {
  const parsed = new DOMParser().parseFromString(html, "text/html");
  return parsed.body?.innerText ?? parsed.body?.textContent ?? "";
}, body);
const title =
  body.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1]
    ?.replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim() ?? "";
const challenge = /just a moment|checking your browser|attention required|verifying you are human|cf-browser-verification|challenge-platform/i.test(
  `${title} ${body}`,
);
const resultSurface =
  /Search Results\s+[\d,]+-[\d,]+\s+of\s+[\d,]+/i.test(body) ||
  /No results were found|0 results/i.test(body);
const singleResultSurface =
  /class=["'][^"']*\blegDetail\b/i.test(body) ||
  /id=["']summarySelector["']/i.test(body);
const status = response.status();
const passed =
  status >= 200 &&
  status < 400 &&
  !challenge &&
  (resultSurface || singleResultSurface);
const receipt = {
  schema: "acu-congress-cookie-replay-transport-probe-v1",
  status: passed ? "PASS" : "FAIL",
  started_at: startedAt,
  completed_at: new Date().toISOString(),
  duration_ms: Date.now() - started,
  source_failure_receipt: {
    path: sourceReceipt,
    sha256: crypto
      .createHash("sha256")
      .update(await fs.readFile(sourceReceipt))
      .digest("hex"),
  },
  transport: {
    bootstrap: "visible Chrome remote-debug context",
    request: "Playwright BrowserContext APIRequestContext",
    endpoint,
    cookie_values_persisted: false,
    cookie_names: cookieNames,
    cf_clearance_present: cookieNames.includes("cf_clearance"),
    user_agent: userAgent,
  },
  response: {
    requested_url: targetUrl,
    final_url: response.url(),
    http_status: status,
    title,
    bytes: Buffer.byteLength(body),
    sha256: crypto.createHash("sha256").update(body).digest("hex"),
    challenge_detected: challenge,
    result_surface_detected: resultSurface,
    single_result_surface_detected: singleResultSurface,
    single_result_proof_hints: {
      requested_query:
        new URL(targetUrl).searchParams.get("enterTerms") ?? null,
      highlighted_query:
        new URL(response.url()).searchParams.get("hl") ?? null,
      result_rank: new URL(response.url()).searchParams.get("r") ?? null,
      search_session_present: Boolean(
        new URL(response.url()).searchParams.get("s"),
      ),
      edit_search_control_present: /\bEDIT THIS\s+SEARCH\b/i.test(
        parsedBodyText,
      ),
      previous_result_disabled: /\bPREVIOUS\s*\(disabled\)/i.test(
        parsedBodyText,
      ),
      next_result_disabled: /\bNEXT\s*\(disabled\)/i.test(parsedBodyText),
      body_text_sha256: crypto
        .createHash("sha256")
        .update(parsedBodyText)
        .digest("hex"),
      relevant_public_text_lines: parsedBodyText
        .split(/\r?\n/)
        .map((line) => line.replace(/\s+/g, " ").trim())
        .filter(
          (line) =>
            line &&
            /\b(?:Sponsor|Introduced|Submitted|Offered|Proposed)\b/i.test(
              line,
            ),
        )
        .slice(0, 24)
        .map((line) => line.slice(0, 500)),
    },
  },
  acceptance: {
    used_as_collection_evidence: false,
    strict_page_parser_required_before_collection_use: true,
    exact_reconciliation_required_before_collection_use: true,
  },
};
await fs.mkdir(path.dirname(output), { recursive: true });
const temporary = `${output}.${process.pid}.tmp`;
await fs.writeFile(temporary, `${JSON.stringify(receipt, null, 2)}\n`);
await fs.rename(temporary, output);
console.log(
  JSON.stringify({
    status: receipt.status,
    http_status: status,
    title,
    cf_clearance_present: receipt.transport.cf_clearance_present,
    challenge_detected: challenge,
    result_surface_detected: resultSurface,
    single_result_surface_detected: singleResultSurface,
    output,
  }),
);
process.exit(passed ? 0 : 2);
