import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { createRequire } from "node:module";

import {
  artifactIntegritySha256,
  extractTextEvidence,
  extractTextEvidenceFromHtml,
  frozenBrowserBaseline,
  officialTextContentFingerprint,
  officialTextContentProjection,
} from "../enrich_congress_text_relevance_pages.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const CHROME =
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const FIXTURE = path.join(
  import.meta.dirname,
  "fixtures",
  "congress_text_official.html",
);
const LOADED_URL =
  "https://www.congress.gov/bill/119th-congress/house-bill/42/text";
const CATEGORIES = [
  {
    query_id: "credit_union",
    label: "Credit Union",
    evidence_role: "category_membership",
    source_coverage_probe_ids: [],
    identifiers: ["Federal Credit Union Act"],
  },
];

test("HTTP-body extraction is browser-DOM equivalent on the frozen fixture", async () => {
  const html = await fs.readFile(FIXTURE, "utf8");
  const browser = await chromium.launch({
    executablePath: CHROME,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await page.setContent(html);
    const browserEvidence = await extractTextEvidence(page, CATEGORIES);
    const httpEvidence = await extractTextEvidenceFromHtml(
      page,
      CATEGORIES,
      html,
      LOADED_URL,
    );
    assert.deepEqual(
      officialTextContentProjection(httpEvidence),
      officialTextContentProjection(browserEvidence),
    );
    assert.equal(
      officialTextContentFingerprint(httpEvidence),
      officialTextContentFingerprint(browserEvidence),
    );
    assert.deepEqual(httpEvidence.categories, browserEvidence.categories);
    assert.equal(httpEvidence.loaded_url, LOADED_URL);

    const priorBrowserArtifact = {
      schema: "congress-text-relevance-measure-v2",
      ...browserEvidence,
      transport_provenance: {
        selected_transport: "browser_navigation",
      },
    };
    const baseline = frozenBrowserBaseline(
      priorBrowserArtifact,
      "a".repeat(64),
    );
    assert.ok(baseline);
    assert.equal(
      baseline.content_fingerprint_sha256,
      officialTextContentFingerprint(httpEvidence),
    );

    const changed = {
      ...httpEvidence,
      official_text_character_count:
        httpEvidence.official_text_character_count - 1,
    };
    assert.notEqual(
      baseline.content_fingerprint_sha256,
      officialTextContentFingerprint(changed),
    );
  } finally {
    await browser.close();
  }
});

test("artifact integrity binds transport provenance and evidence", () => {
  const artifact = {
    schema: "congress-text-relevance-measure-v2",
    text_available: true,
    official_text_fnv1a32: "12345678",
    transport_provenance: {
      selected_transport: "browser_context_http",
      frozen_browser_hash_equivalence: { passed: true },
    },
  };
  artifact.artifact_integrity_sha256 = artifactIntegritySha256(artifact);
  assert.equal(
    artifact.artifact_integrity_sha256,
    artifactIntegritySha256(artifact),
  );
  artifact.transport_provenance.frozen_browser_hash_equivalence.passed = false;
  assert.notEqual(
    artifact.artifact_integrity_sha256,
    artifactIntegritySha256(artifact),
  );
});

