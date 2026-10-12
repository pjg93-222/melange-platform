import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";

import {
  loadBillstatusSummaryIndex,
  officialSummaryEvidence,
} from "../enrich_congress_text_relevance_pages.mjs";

const MANIFEST = path.join(
  import.meta.dirname,
  "..",
  "..",
  "audits",
  "legfix5",
  "billstatus_bulk_enrichment",
  "fetch_runs",
  "c7ca822fd5951478a33a814d228b71ffdf69745e6d823001fefaba620282c435",
  "run_manifest.json",
);

test("BillStatus evidence verifies raw+parsed sources and preserves roles", async () => {
  const index = await loadBillstatusSummaryIndex(MANIFEST);
  const evidence = await officialSummaryEvidence(
    { canonical_key: "117:hr:3664", record_kind: "legislation" },
    index,
  );
  assert.equal(evidence.available, true);
  assert.ok(evidence.items.some((item) => item.text));
  assert.ok(evidence.titles.length > 0);
  assert.equal(evidence.sponsors[0].relationship_role, "Sponsor");
  assert.ok(
    evidence.cosponsors.some(
      (item) =>
        item.original_cosponsor === true &&
        item.relationship_role === "Original Cosponsor" &&
        item.role_provenance_complete === true,
    ),
  );
  assert.ok(evidence.actions.length > 0);
  assert.match(evidence.provenance.parsed_file_sha256, /^[0-9a-f]{64}$/);
  assert.match(evidence.provenance.source_file_sha256, /^[0-9a-f]{64}$/);
});

test("nested amendment summary absence remains explicit and neutral", async () => {
  const index = await loadBillstatusSummaryIndex(MANIFEST);
  const evidence = await officialSummaryEvidence(
    { canonical_key: "116:hamdt:3", record_kind: "amendment" },
    index,
  );
  assert.equal(evidence.available, false);
  assert.equal(
    evidence.absence_reason,
    "nested_amendment_no_standalone_billstatus_package",
  );
  assert.deepEqual(evidence.items, []);
});
