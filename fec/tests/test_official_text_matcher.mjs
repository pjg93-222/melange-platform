import assert from "node:assert/strict";
import { createRequire } from "node:module";

import {
  extractTextEvidence,
  TEXT_MATCH_POLICY_VERSION,
} from "../enrich_congress_text_relevance_pages.mjs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const CHROME =
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";

function category(identifier) {
  return [
    {
      query_id: "test",
      label: "Test",
      evidence_role: "category_membership",
      source_coverage_probe_ids: [],
      identifiers: [identifier],
    },
  ];
}

async function evidence(
  page,
  identifier,
  billText,
  withMarker = true,
  markerText = "Shown Here:",
) {
  const marker = withMarker ? `<div>${markerText}</div>` : "";
  await page.setContent(
    `<main id="main">${marker}<div>${billText}</div></main>`,
  );
  const result = await extractTextEvidence(page, category(identifier));
  return {
    result,
    item: result.categories[0].identifiers[0],
    category: result.categories[0],
  };
}

const browser = await chromium.launch({
  executablePath: CHROME,
  headless: true,
});

try {
  const page = await browser.newPage();

  {
    const { item } = await evidence(
      page,
      "Act",
      "This Act amends a federal program.",
    );
    assert.equal(item.specificity_valid, false);
    assert.equal(item.localized_match, false);
  }

  {
    const { item } = await evidence(
      page,
      "SMART Act",
      "The SMART Act amends a transportation program.",
    );
    assert.equal(item.specificity_valid, false);
    assert.equal(item.localized_match, false);
  }

  {
    const { item } = await evidence(
      page,
      "Federal Credit Union Act",
      "Federal credit rules apply to each union. This Act amends another law.",
    );
    assert.equal(item.complete_act_name_found, false);
    assert.equal(item.localized_match, false);
  }

  {
    const { item } = await evidence(
      page,
      "Federal Credit Union Act",
      "The term is defined in the Federal Credit Union Act.",
    );
    assert.equal(item.complete_act_name_found, true);
    assert.equal(item.act_reference_only, true);
    assert.equal(item.localized_match, false);
  }

  {
    const { item } = await evidence(
      page,
      "Federal Credit Union Act",
      "This section amends the Federal Credit Union Act to require notice.",
    );
    assert.equal(item.localized_legislative_action_found, true);
    assert.equal(item.localized_match, true);
  }

  {
    const { item } = await evidence(
      page,
      '"credit card routing"~5',
      "The credit card network must modernize its routing requirements.",
    );
    assert.equal(item.evidence_identifier, "credit card routing");
    assert.equal(item.localized_match, true);
    assert.equal(item.identifier_tokens.includes("5"), false);
  }

  {
    const { item } = await evidence(
      page,
      '"credit card routing"~5',
      "The credit card network is regulated. Routing rules are separate.",
    );
    assert.equal(item.localized_match, false);
  }

  {
    const { item } = await evidence(
      page,
      "MDI",
      "This section modifies Medicaid eligibility.",
    );
    assert.equal(item.localized_match, false);
  }

  {
    const { result, item } = await evidence(
      page,
      "credit union",
      "Navigation Bill Text credit union",
      false,
    );
    assert.equal(result.text_available, false);
    assert.equal(item.localized_match, false);
  }

  {
    const { result, item } = await evidence(
      page,
      "credit union",
      "This amendment changes credit union reporting requirements.",
      true,
      "Amendment Text: H.Amdt.3 — 116th Congress",
    );
    assert.equal(result.text_available, true);
    assert.equal(result.official_text_surface_marker, "Amendment Text:");
    assert.equal(result.shown_here_marker_found, false);
    assert.equal(result.amendment_text_marker_found, true);
    assert.equal(item.localized_match, true);
  }

  {
    await page.setContent(
      "<main><h1>Just a moment...</h1><p>Cloudflare verifying you are human</p></main>",
    );
    const result = await extractTextEvidence(page, category("credit union"));
    assert.equal(result.text_available, false);
    assert.equal(result.official_text_surface_marker, null);
    assert.equal(result.shown_here_marker_found, false);
    assert.equal(result.amendment_text_marker_found, false);
    assert.equal(result.categories[0].identifiers[0].localized_match, false);
  }

  assert.equal(
    TEXT_MATCH_POLICY_VERSION,
    "ordered-token-bounded-act-action-required-complete-surface-v3",
  );
  console.log("official-text matcher behavior: 11 PASS");
} finally {
  await browser.close();
}
