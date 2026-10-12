import assert from "node:assert/strict";
import test from "node:test";

import {
  classifySingleResultRedirect,
  displayNumberForCanonical,
  parseSingleResultPageSnapshot,
} from "../lib/congress_advanced_navigation.mjs";

const query = '("FAIR Exams Act")';
const requestedUrl =
  "https://www.congress.gov/index.php/advanced-search/legislation?" +
  "congresses%5B0%5D=119&enterTerms=%28%22FAIR+Exams+Act%22%29";
const loadedUrl =
  "https://www.congress.gov/index.php/bill/119th-congress/house-bill/940?" +
  "hl=%28%22FAIR+Exams+Act%22%29&s=9&r=1";
const title =
  "H.R.940 - 119th Congress (2025-2026): FAIR Exams Act | Congress.gov | " +
  "Library of Congress";
const bodyText = `
  Home > Legislation > 119th Congress > H.R.940
  EDIT THIS
  SEARCH
  PREVIOUS (disabled)
  NEXT (disabled)
  H.R.940 - FAIR Exams Act
  119th Congress (2025-2026)
  Sponsor: Rep. Hill, J. French [R-AR-2] (Introduced 02/04/2025)
`;
const contract = {
  congresses: [116, 117, 118, 119],
  legislationTypes: [
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
};

test("accepts the observed Congress.gov one-result Advanced Search redirect", () => {
  const result = classifySingleResultRedirect({
    requestedUrl,
    loadedUrl,
    title,
    bodyText,
    query,
    ...contract,
  });
  assert.equal(result?.kind, "single_result_redirect");
  assert.equal(result?.total, 1);
  assert.equal(result?.canonical.canonical_key, "119:hr:940");
  assert.equal(displayNumberForCanonical(result.canonical), "H.R.940");
  assert.ok(Object.values(result.proof).every(Boolean));
});

test("rejects a stale or unrelated bill page", () => {
  assert.equal(
    classifySingleResultRedirect({
      requestedUrl,
      loadedUrl: loadedUrl.replace("FAIR+Exams+Act", "TAILOR+Act"),
      title,
      bodyText,
      query,
      ...contract,
    }),
    null,
  );
});

test("rejects a result navigation context that does not prove cardinality one", () => {
  assert.equal(
    classifySingleResultRedirect({
      requestedUrl,
      loadedUrl,
      title,
      bodyText: bodyText.replace("NEXT (disabled)", "NEXT"),
      query,
      ...contract,
    }),
    null,
  );
});

test("accepts control-free current surface only after dual-transport redirect agreement", () => {
  const result = classifySingleResultRedirect({
    requestedUrl,
    loadedUrl,
    title,
    bodyText: bodyText
      .replace("EDIT THIS\n  SEARCH", "")
      .replace("PREVIOUS (disabled)", "")
      .replace("NEXT (disabled)", ""),
    query,
    ...contract,
    dualTransportRedirectMatch: true,
  });
  assert.equal(result?.kind, "single_result_redirect");
  assert.equal(result?.canonical.canonical_key, "119:hr:940");
  assert.equal(
    result?.cardinality_proof,
    "dual-transport-session-redirect-v2",
  );
  assert.equal(result?.surface_observations.edit_search_control_present, false);
  assert.equal(result?.surface_observations.previous_result_disabled, false);
  assert.equal(result?.surface_observations.next_result_disabled, false);
  assert.equal(result?.surface_observations.dual_transport_redirect_match, true);
  assert.ok(Object.values(result.proof).every(Boolean));
});

test("rejects a measure outside the collection contract", () => {
  assert.equal(
    classifySingleResultRedirect({
      requestedUrl,
      loadedUrl: loadedUrl.replace("119th-congress", "115th-congress"),
      title: title.replace("119th Congress", "115th Congress"),
      bodyText: bodyText.replaceAll("119th Congress", "115th Congress"),
      query,
      ...contract,
    }),
    null,
  );
});

test("extracts the official candidate fields from a singleton detail page", () => {
  const record = parseSingleResultPageSnapshot({
    loadedUrl,
    title,
    bodyText: `${bodyText}
      Committees: House - Financial Services
      Latest Action: House - 07/25/2025 Placed on the Union Calendar. (All Actions)
      This bill has the status Introduced
      Here are the steps for Status of Legislation:
      Cosponsors (10)
    `,
    sponsorUrl:
      "https://www.congress.gov/member/j-french-hill/H001072",
  });
  assert.equal(record.display_number, "H.R.940");
  assert.equal(record.title, "FAIR Exams Act");
  assert.equal(record.sponsor, "Rep. Hill, J. French [R-AR-2]");
  assert.equal(record.initial_action, "Introduced");
  assert.equal(record.initial_action_date, "02/04/2025");
  assert.equal(record.cosponsor_count, 10);
  assert.equal(record.committees, "Committees: House - Financial Services");
  assert.equal(record.latest_action_date, "07/25/2025");
  assert.equal(record.tracker_status, "Introduced");
  assert.equal(record.result_url, loadedUrl);
});

test("extracts sponsor and action date when the current page splits the label", () => {
  const record = parseSingleResultPageSnapshot({
    loadedUrl,
    title,
    bodyText: `
      Sponsor:
      Rep. Hill, J. French [R-AR-2] (Introduced 02/04/2025)
      Introduced in House (02/04/2025)
    `,
    sponsorUrl:
      "https://www.congress.gov/member/j-french-hill/H001072",
  });
  assert.equal(record.sponsor, "Rep. Hill, J. French [R-AR-2]");
  assert.equal(record.initial_action, "Introduced");
  assert.equal(record.initial_action_date, "02/04/2025");
});
