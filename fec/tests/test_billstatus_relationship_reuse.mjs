import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { buildBillstatusRelationshipReuse } from "../lib/billstatus_relationship_reuse.mjs";

async function receipt(file) {
  const body = await fs.readFile(file);
  return {
    path: path.resolve(file),
    bytes: body.length,
    sha256: crypto.createHash("sha256").update(body).digest("hex"),
  };
}

async function fixture() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "billstatus-reuse-"));
  const sidecar = path.join(root, "sidecar");
  await fs.mkdir(sidecar);
  const rows = {
    edges: [{ canonical_key: "118:hr:1", member: "A" }],
    verification_ledger: [
      {
        canonical_key: "118:hamdt:2",
        disposition: "retained_unresolved_no_standalone_top_level_package",
        reason: "nested amendment",
        plan_sha256: "plan-hash",
      },
      {
        canonical_key: "118:hamdt:185",
        disposition: "retained_unresolved_no_standalone_top_level_package",
        reason: "nested amendment",
        plan_sha256: "plan-hash",
      },
    ],
    measure_metadata: [{ canonical_key: "118:hr:1" }],
    source_gap_ledger: [
      { canonical_key: "118:hamdt:2", gap: "nested" },
      { canonical_key: "118:hamdt:185", gap: "nested" },
    ],
  };
  const artifacts = {};
  for (const [name, values] of Object.entries(rows)) {
    const file = path.join(sidecar, `${name}.ndjson`);
    await fs.writeFile(
      file,
      `${values.map((value) => JSON.stringify(value)).join("\n")}\n`,
    );
    artifacts[name] = await receipt(file);
  }
  const manifest = {
    schema: "congress-sponsorship-enrichment-manifest-v1",
    status: "complete",
    official_source_only: true,
    top_level_relationship_evidence_complete: true,
    candidate_creation_performed: false,
    semantic_exclusion_performed: false,
    ratified_decision_performed: false,
    automatic_scoring_performed: false,
    sync_or_promotion_performed: false,
    production_write_performed: false,
    membership_disposition_authority: false,
    ratification_authority: false,
    scoring_authority: false,
    acceptance_passed: false,
    selected_record_count: 3,
    top_level_metadata_count: 1,
    unresolved_measure_count: 2,
    edge_count: 1,
    edge_counts: { sponsors: 1 },
    edges_file: path.basename(artifacts.edges.path),
    edges_sha256: artifacts.edges.sha256,
    verification_ledger_file: path.basename(
      artifacts.verification_ledger.path,
    ),
    verification_ledger_sha256: artifacts.verification_ledger.sha256,
    measure_metadata_file: path.basename(artifacts.measure_metadata.path),
    measure_metadata_sha256: artifacts.measure_metadata.sha256,
    source_gap_ledger_file: path.basename(artifacts.source_gap_ledger.path),
    source_gap_ledger_sha256: artifacts.source_gap_ledger.sha256,
    outputs: artifacts,
  };
  const manifestPath = path.join(sidecar, "manifest.json");
  await fs.writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
  const pointer = {
    schema: "govinfo-billstatus-review-sidecar-pointer-v1",
    manifest: await receipt(manifestPath),
    sync_or_promotion_performed: false,
  };
  const pointerPath = path.join(root, "current.json");
  await fs.writeFile(pointerPath, `${JSON.stringify(pointer, null, 2)}\n`);
  return { root, pointerPath, artifacts };
}

function discoveryRecords() {
  return [
    {
      canonical_key: "118:hr:1",
      record_kind: "bill",
    },
    {
      canonical_key: "118:hamdt:2",
      record_kind: "amendment",
      congress: 118,
      type: "hamdt",
      number: 2,
      display_number: "H.Amdt.2",
      canonical_url:
        "https://www.congress.gov/amendment/118th-congress/house-amendment/2",
      result_url:
        "https://www.congress.gov/amendment/118th-congress/house-amendment/2?s=1",
      sponsor: "Example, Alice [Rep.-D-CA-1]",
      sponsor_url:
        "https://www.congress.gov/member/alice-example/E000001?search=x",
      sponsor_line: "Sponsor: Example, Alice [Rep.-D-CA-1]",
      initial_action_date_iso: "2023-01-02",
    },
    {
      canonical_key: "118:hamdt:185",
      record_kind: "amendment",
      congress: 118,
      type: "hamdt",
      number: 185,
      display_number: "H.Amdt.185",
      canonical_url:
        "https://www.congress.gov/amendment/118th-congress/house-amendment/185",
      result_url:
        "https://www.congress.gov/amendment/118th-congress/house-amendment/185?s=1",
      sponsor: "House Committee on Rules",
      sponsor_url: null,
      sponsor_line: "Sponsor: House Committee on Rules (Offered 05/31/2023)",
      initial_action_date_iso: "2023-05-31",
    },
  ];
}

function discoveryHash(records) {
  return crypto
    .createHash("sha256")
    .update(JSON.stringify(records))
    .digest("hex");
}

async function readNdjson(file) {
  return (await fs.readFile(file, "utf8"))
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

test("reuses official relationship data only for an exact candidate partition", async () => {
  const value = await fixture();
  try {
    const outputDir = path.join(value.root, "output");
    const records = discoveryRecords();
    const manifest = await buildBillstatusRelationshipReuse({
      pointerPath: value.pointerPath,
      discoveryRecords: records,
      discoveryManifestPath: path.join(value.root, "discovery.json"),
      discoveryRecordsSha256: discoveryHash(records),
      outputDir,
    });
    assert.equal(manifest.status, "complete");
    assert.equal(manifest.edge_count, 2);
    assert.equal(manifest.resolved_amendment_count, 2);
    assert.equal(manifest.amendment_member_sponsor_count, 1);
    assert.equal(manifest.non_member_sponsor_disposition_count, 1);
    assert.equal(manifest.unresolved_measure_count, 0);
    assert.equal(manifest.candidate_partition.missing_count, 0);
    assert.equal(manifest.candidate_partition.extra_count, 0);
    assert.equal(manifest.candidate_partition.unresolved_amendment_count, 0);
    assert.equal(
      manifest.billstatus_relationship_reuse.browser_navigation_performed,
      false,
    );
    const edges = await readNdjson(path.join(outputDir, manifest.edges_file));
    assert.equal(edges.length, 2);
    assert.deepEqual(
      edges.find((row) => row.canonical_key === "118:hamdt:2"),
      {
        canonical_key: "118:hamdt:2",
        congress: 118,
        type: "hamdt",
        number: 2,
        display_number: "H.Amdt.2",
        title: null,
        role: "Sponsor",
        explicit_role: "Sponsor",
        source_relationship_role: "Sponsor",
        member: "Example, Alice [Rep.-D-CA-1]",
        member_url:
          "https://www.congress.gov/member/alice-example/E000001",
        bioguide_id: "E000001",
        chamber_label: "Rep",
        party: "D",
        state: "CA",
        district: "1",
        sponsorship_date: "2023-01-02",
        original_cosponsor: null,
        relationship_status: "current",
        withdrawn_date: null,
        withdrawal_explanation: null,
        role_provenance:
          "Congress.gov Advanced Search result card sponsor line and official member URL",
        role_provenance_complete: true,
        source_url:
          "https://www.congress.gov/amendment/118th-congress/house-amendment/2",
        discovery_result_url:
          "https://www.congress.gov/amendment/118th-congress/house-amendment/2?s=1",
        discovery_record_sha256:
          "1dc345fb789b10893b93fd16b38393ef781ea3991ba27efccc0054ac51213582",
        evidence_scope: "review_prefill_only",
        membership_disposition_authority: false,
        ratification_authority: false,
        scoring_authority: false,
      },
    );
    assert.deepEqual(
      await readNdjson(
        path.join(outputDir, manifest.verification_ledger_file),
      ),
      [],
    );
    const resolutions = await readNdjson(
      path.join(outputDir, manifest.amendment_resolution_ledger_file),
    );
    assert.equal(resolutions.length, 2);
    assert.equal(
      resolutions.find((row) => row.canonical_key === "118:hamdt:185")
        .disposition,
      "resolved_explicit_non_member_sponsor",
    );
    assert.equal(
      manifest.billstatus_relationship_reuse.source_verification_ledger
        .sha256,
      value.artifacts.verification_ledger.sha256,
    );
  } finally {
    await fs.rm(value.root, { recursive: true, force: true });
  }
});

test("rejects a sidecar whose candidate partition is incomplete", async () => {
  const value = await fixture();
  try {
    const records = [
      ...discoveryRecords(),
      { canonical_key: "118:s:3", record_kind: "bill" },
    ];
    await assert.rejects(
      buildBillstatusRelationshipReuse({
        pointerPath: value.pointerPath,
        discoveryRecords: records,
        discoveryManifestPath: path.join(value.root, "discovery.json"),
        discoveryRecordsSha256: discoveryHash(records),
        outputDir: path.join(value.root, "output"),
      }),
      /partition differs/,
    );
  } finally {
    await fs.rm(value.root, { recursive: true, force: true });
  }
});

test("rejects a sidecar artifact changed after its receipt", async () => {
  const value = await fixture();
  try {
    await fs.appendFile(value.artifacts.measure_metadata.path, "{}\n");
    const records = discoveryRecords();
    await assert.rejects(
      buildBillstatusRelationshipReuse({
        pointerPath: value.pointerPath,
        discoveryRecords: records,
        discoveryManifestPath: path.join(value.root, "discovery.json"),
        discoveryRecordsSha256: discoveryHash(records),
        outputDir: path.join(value.root, "output"),
      }),
      /does not match/,
    );
  } finally {
    await fs.rm(value.root, { recursive: true, force: true });
  }
});

test("fails closed for an unexpected amendment without a member sponsor", async () => {
  const value = await fixture();
  try {
    const records = discoveryRecords();
    records[1] = {
      ...records[1],
      sponsor: "Unknown Committee",
      sponsor_url: null,
    };
    await assert.rejects(
      buildBillstatusRelationshipReuse({
        pointerPath: value.pointerPath,
        discoveryRecords: records,
        discoveryManifestPath: path.join(value.root, "discovery.json"),
        discoveryRecordsSha256: discoveryHash(records),
        outputDir: path.join(value.root, "output"),
      }),
      /unexpected amendment without a member sponsor/,
    );
  } finally {
    await fs.rm(value.root, { recursive: true, force: true });
  }
});

test("rejects discovery rows that are stale against the manifest hash", async () => {
  const value = await fixture();
  try {
    await assert.rejects(
      buildBillstatusRelationshipReuse({
        pointerPath: value.pointerPath,
        discoveryRecords: discoveryRecords(),
        discoveryManifestPath: path.join(value.root, "discovery.json"),
        discoveryRecordsSha256: "stale-hash",
        outputDir: path.join(value.root, "output"),
      }),
      /do not match the hash-bound Advanced Search manifest/,
    );
  } finally {
    await fs.rm(value.root, { recursive: true, force: true });
  }
});
