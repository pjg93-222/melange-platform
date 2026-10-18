import crypto from "node:crypto";
import { createReadStream } from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";
import readline from "node:readline";

const RETRYABLE_RENAME_CODES = new Set([
  "EBUSY",
  "EPERM",
  "EACCES",
  "EEXIST",
]);

function canonicalHash(values) {
  return crypto
    .createHash("sha256")
    .update(JSON.stringify([...values].sort()))
    .digest("hex");
}

async function sha256File(file) {
  const hash = crypto.createHash("sha256");
  const handle = await fs.open(file, "r");
  try {
    for await (const chunk of handle.readableWebStream()) {
      hash.update(Buffer.from(chunk));
    }
  } finally {
    await handle.close();
  }
  return hash.digest("hex");
}

async function fileReceipt(file) {
  const resolved = path.resolve(file);
  const stat = await fs.stat(resolved);
  if (!stat.isFile()) throw new Error(`not a file: ${resolved}`);
  return {
    path: resolved,
    bytes: stat.size,
    sha256: await sha256File(resolved),
  };
}

function resolveManifestFile(manifestDir, value) {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error("sidecar manifest has an invalid artifact path");
  }
  return path.isAbsolute(value)
    ? path.resolve(value)
    : path.resolve(manifestDir, value);
}

async function readCanonicalKeys(file) {
  const keys = new Set();
  const text = await fs.readFile(file, "utf8");
  let rowCount = 0;
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) continue;
    rowCount += 1;
    const row = JSON.parse(line);
    if (
      typeof row?.canonical_key !== "string" ||
      !row.canonical_key.trim()
    ) {
      throw new Error(`missing canonical_key in ${file}`);
    }
    if (keys.has(row.canonical_key)) {
      throw new Error(`duplicate canonical_key in ${file}: ${row.canonical_key}`);
    }
    keys.add(row.canonical_key);
  }
  return { keys, rowCount };
}

async function readNdjson(file) {
  const rows = [];
  const text = await fs.readFile(file, "utf8");
  for (const line of text.split(/\r?\n/)) {
    if (line.trim()) rows.push(JSON.parse(line));
  }
  return rows;
}

function stableHash(value) {
  return crypto
    .createHash("sha256")
    .update(JSON.stringify(value))
    .digest("hex");
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function replaceWithRetry(temporary, destination, attempts = 30) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      await fs.rename(temporary, destination);
      return;
    } catch (error) {
      lastError = error;
      if (!RETRYABLE_RENAME_CODES.has(error.code)) throw error;
      if (attempt < attempts) await sleep(250 * attempt);
    }
  }
  throw new Error(
    `Could not atomically publish relationship artifact: ${destination}`,
    { cause: lastError },
  );
}

/**
 * Copy an optional NDJSON base and append rows while computing both the exact
 * file receipt and the JSON-array hash used by downstream relationship gates.
 * Publication is fsync + same-directory atomic rename so a crash cannot expose
 * a truncated accepted artifact.
 */
async function writeHybridNdjson({ sourceFile = null, additionalRows, outputFile }) {
  const resolved = path.resolve(outputFile);
  await fs.mkdir(path.dirname(resolved), { recursive: true });
  const temporary = path.join(
    path.dirname(resolved),
    `.${path.basename(resolved)}.tmp-${process.pid}-${crypto.randomUUID()}`,
  );
  const fileHash = crypto.createHash("sha256");
  const contentHash = crypto.createHash("sha256");
  contentHash.update("[");
  let rowCount = 0;
  let bytes = 0;
  let handle;
  let buffer = "";
  let bufferBytes = 0;

  const flush = async () => {
    if (!buffer) return;
    const pending = Buffer.from(buffer, "utf8");
    let offset = 0;
    while (offset < pending.length) {
      const { bytesWritten } = await handle.write(
        pending, offset, pending.length - offset, null,
      );
      if (!Number.isInteger(bytesWritten) || bytesWritten <= 0 ||
          bytesWritten > pending.length - offset) {
        throw new Error("Relationship artifact write made invalid progress");
      }
      offset += bytesWritten;
    }
    buffer = "";
    bufferBytes = 0;
  };
  const addRow = async (row) => {
    const serialized = JSON.stringify(row);
    contentHash.update(rowCount ? `,${serialized}` : serialized);
    const line = `${serialized}\n`;
    fileHash.update(line);
    const lineBytes = Buffer.byteLength(line);
    bytes += lineBytes;
    buffer += line;
    bufferBytes += lineBytes;
    rowCount += 1;
    if (bufferBytes >= 1024 * 1024) await flush();
  };

  try {
    handle = await fs.open(temporary, "wx");
    if (sourceFile) {
      const input = readline.createInterface({
        input: createReadStream(sourceFile, { encoding: "utf8" }),
        crlfDelay: Infinity,
      });
      for await (const line of input) {
        if (line.trim()) await addRow(JSON.parse(line));
      }
    }
    for (const row of additionalRows) await addRow(row);
    await flush();
    contentHash.update("]");
    await handle.sync();
    await handle.close();
    handle = null;
    await replaceWithRetry(temporary, resolved);
    return {
      path: resolved,
      bytes,
      sha256: fileHash.digest("hex"),
      content_sha256: contentHash.digest("hex"),
      record_count: rowCount,
    };
  } finally {
    if (handle) await handle.close().catch(() => {});
    await fs.rm(temporary, { force: true }).catch(() => {});
  }
}

function compact(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function normalizedMember(record) {
  if (typeof record.sponsor_url !== "string" || !record.sponsor_url.trim()) {
    return null;
  }
  const memberUrl = new URL(record.sponsor_url);
  if (
    memberUrl.protocol !== "https:" ||
    memberUrl.hostname.toLowerCase() !== "www.congress.gov"
  ) {
    throw new Error(
      `amendment sponsor URL is not official Congress.gov evidence: ${record.canonical_key}`,
    );
  }
  memberUrl.search = "";
  memberUrl.hash = "";
  const bioguideId = memberUrl.pathname
    .match(/^\/member\/[^/]+\/([A-Z]\d{6})\/?$/i)?.[1]
    ?.toUpperCase();
  if (!bioguideId) {
    throw new Error(
      `amendment sponsor URL lacks a Bioguide ID: ${record.canonical_key}`,
    );
  }
  const bracket = compact(record.sponsor).match(/\[([^\]]+)\]\*?$/)?.[1] ?? null;
  const parts = bracket?.split("-") ?? [];
  return {
    member: compact(record.sponsor).replace(/\*$/, ""),
    member_url: memberUrl.href.replace(/\/$/, ""),
    bioguide_id: bioguideId,
    chamber_label: parts[0]?.replace(/\.$/, "") ?? null,
    party: parts.length >= 2 ? parts[1] : null,
    state: parts.length >= 3 ? parts[2] : null,
    district: parts.length >= 4 ? parts.slice(3).join("-") : null,
  };
}

function amendmentResolution(record, priorGap) {
  const recordSha256 = stableHash(record);
  const member = normalizedMember(record);
  const shared = {
    canonical_key: record.canonical_key,
    congress: record.congress,
    type: record.type,
    number: record.number,
    display_number: record.display_number,
    source_gap_disposition: priorGap.disposition,
    source_gap_reason: priorGap.reason,
    source_gap_plan_sha256: priorGap.plan_sha256,
    discovery_record_sha256: recordSha256,
    discovery_evidence: {
      record_kind: record.record_kind,
      sponsor: record.sponsor,
      sponsor_url: record.sponsor_url,
      sponsor_line: record.sponsor_line,
      initial_action_date_iso: record.initial_action_date_iso,
      canonical_url: record.canonical_url,
      result_url: record.result_url,
    },
    candidate_retained: true,
    absence_is_neutral: true,
    automatic_exclusion_authorized: false,
    automatic_scoring_authorized: false,
    membership_disposition_authority: false,
    ratification_authority: false,
    scoring_authority: false,
  };
  if (!member) {
    if (
      record.canonical_key !== "118:hamdt:185" ||
      compact(record.sponsor) !== "House Committee on Rules"
    ) {
      throw new Error(
        `unexpected amendment without a member sponsor: ${record.canonical_key}`,
      );
    }
    return {
      resolution: {
        ...shared,
        disposition: "resolved_explicit_non_member_sponsor",
        member_edge_emitted: false,
        non_member_sponsor: "House Committee on Rules",
      },
      edge: null,
      metadata: {
        canonical_key: record.canonical_key,
        canonical_url: record.canonical_url,
        discovery_title: record.title ?? record.purpose ?? null,
        relationship_evidence_available: true,
        relationship_evidence_kind: "official_non_member_sponsor",
        official_non_member_sponsor: "House Committee on Rules",
        discovery_record_sha256: recordSha256,
        membership_disposition_authority: false,
        ratification_authority: false,
        scoring_authority: false,
      },
    };
  }
  return {
    resolution: {
      ...shared,
      disposition: "resolved_from_congress_advanced_search_sponsor_evidence",
      member_edge_emitted: true,
      bioguide_id: member.bioguide_id,
    },
    edge: {
      canonical_key: record.canonical_key,
      congress: record.congress,
      type: record.type,
      number: record.number,
      display_number: record.display_number,
      title: record.title ?? record.purpose ?? null,
      role: "Sponsor",
      explicit_role: "Sponsor",
      source_relationship_role: "Sponsor",
      ...member,
      sponsorship_date: record.initial_action_date_iso ?? null,
      original_cosponsor: null,
      relationship_status: "current",
      withdrawn_date: null,
      withdrawal_explanation: null,
      role_provenance:
        "Congress.gov Advanced Search result card sponsor line and official member URL",
      role_provenance_complete: true,
      source_url: record.canonical_url,
      discovery_result_url: record.result_url,
      discovery_record_sha256: recordSha256,
      evidence_scope: "review_prefill_only",
      membership_disposition_authority: false,
      ratification_authority: false,
      scoring_authority: false,
    },
    metadata: {
      canonical_key: record.canonical_key,
      canonical_url: record.canonical_url,
      discovery_title: record.title ?? record.purpose ?? null,
      relationship_evidence_available: true,
      relationship_evidence_kind:
        "official_advanced_search_member_sponsor",
      official_sponsors: [
        {
          bioguide_id: member.bioguide_id,
          full_name: member.member,
          relationship_role: "Sponsor",
          relationship_status: "current",
          role_provenance:
            "Congress.gov Advanced Search result card sponsor line and official member URL",
          source_url: record.canonical_url,
        },
      ],
      discovery_record_sha256: recordSha256,
      membership_disposition_authority: false,
      ratification_authority: false,
      scoring_authority: false,
    },
  };
}

function assertBoolean(value, expected, label) {
  if (value !== expected) {
    throw new Error(`${label} must be ${expected}`);
  }
}

async function validatedArtifact(
  source,
  manifestDir,
  fileField,
  hashField,
  outputField,
) {
  const file = resolveManifestFile(manifestDir, source[fileField]);
  const receipt = await fileReceipt(file);
  if (source[hashField] !== receipt.sha256) {
    throw new Error(`${hashField} does not match ${fileField}`);
  }
  const output = source.outputs?.[outputField];
  if (
    !output ||
    path.resolve(output.path) !== receipt.path ||
    output.bytes !== receipt.bytes ||
    output.sha256 !== receipt.sha256
  ) {
    throw new Error(`outputs.${outputField} is not file-bound`);
  }
  return receipt;
}

/**
 * Return an exact, hash-validated adapter manifest or throw. The caller may
 * safely fall back to live Congress.gov detail pages on any rejection.
 */
export async function buildBillstatusRelationshipReuse({
  pointerPath,
  discoveryRecords,
  discoveryManifestPath,
  discoveryRecordsSha256,
  outputDir,
}) {
  if (
    typeof discoveryRecordsSha256 !== "string" ||
    stableHash(discoveryRecords) !== discoveryRecordsSha256
  ) {
    throw new Error(
      "discovery records do not match the hash-bound Advanced Search manifest",
    );
  }
  if (typeof outputDir !== "string" || !outputDir.trim()) {
    throw new Error("outputDir is required for hybrid relationship materialization");
  }
  const pointerFile = path.resolve(pointerPath);
  const pointer = JSON.parse(await fs.readFile(pointerFile, "utf8"));
  if (pointer.schema !== "govinfo-billstatus-review-sidecar-pointer-v1") {
    throw new Error("unexpected BillStatus relationship pointer schema");
  }
  assertBoolean(
    pointer.sync_or_promotion_performed,
    false,
    "pointer.sync_or_promotion_performed",
  );
  const sourceManifestPath = path.resolve(pointer.manifest?.path ?? "");
  const sourceManifestReceipt = await fileReceipt(sourceManifestPath);
  if (
    pointer.manifest?.bytes !== sourceManifestReceipt.bytes ||
    pointer.manifest?.sha256 !== sourceManifestReceipt.sha256
  ) {
    throw new Error("BillStatus relationship pointer manifest receipt is stale");
  }
  const source = JSON.parse(await fs.readFile(sourceManifestPath, "utf8"));
  if (
    source.schema !== "congress-sponsorship-enrichment-manifest-v1" ||
    source.status !== "complete"
  ) {
    throw new Error("BillStatus relationship sidecar is not complete");
  }
  assertBoolean(source.official_source_only, true, "official_source_only");
  assertBoolean(
    source.top_level_relationship_evidence_complete,
    true,
    "top_level_relationship_evidence_complete",
  );
  for (const field of [
    "candidate_creation_performed",
    "semantic_exclusion_performed",
    "ratified_decision_performed",
    "automatic_scoring_performed",
    "sync_or_promotion_performed",
    "production_write_performed",
    "membership_disposition_authority",
    "ratification_authority",
    "scoring_authority",
    "acceptance_passed",
  ]) {
    assertBoolean(source[field], false, field);
  }

  const manifestDir = path.dirname(sourceManifestPath);
  const edges = await validatedArtifact(
    source,
    manifestDir,
    "edges_file",
    "edges_sha256",
    "edges",
  );
  const verification = await validatedArtifact(
    source,
    manifestDir,
    "verification_ledger_file",
    "verification_ledger_sha256",
    "verification_ledger",
  );
  const metadata = await validatedArtifact(
    source,
    manifestDir,
    "measure_metadata_file",
    "measure_metadata_sha256",
    "measure_metadata",
  );
  const gaps = await validatedArtifact(
    source,
    manifestDir,
    "source_gap_ledger_file",
    "source_gap_ledger_sha256",
    "source_gap_ledger",
  );

  const discoveryKeys = new Set();
  for (const record of discoveryRecords) {
    if (
      typeof record?.canonical_key !== "string" ||
      !record.canonical_key.trim() ||
      discoveryKeys.has(record.canonical_key)
    ) {
      throw new Error("discovery corpus has an invalid canonical-key partition");
    }
    discoveryKeys.add(record.canonical_key);
  }
  const metadataKeys = await readCanonicalKeys(metadata.path);
  const unresolvedRows = await readNdjson(verification.path);
  if (
    unresolvedRows.some(
      (row) =>
        typeof row.canonical_key !== "string" || !row.canonical_key.trim(),
    )
  ) {
    throw new Error(
      "BillStatus relationship verification ledger has an invalid canonical key",
    );
  }
  const unresolvedKeys = {
    keys: new Set(unresolvedRows.map((row) => row.canonical_key)),
    rowCount: unresolvedRows.length,
  };
  if (unresolvedKeys.keys.size !== unresolvedKeys.rowCount) {
    throw new Error("BillStatus relationship verification ledger has duplicate keys");
  }
  for (const key of unresolvedKeys.keys) {
    if (metadataKeys.keys.has(key)) {
      throw new Error(`BillStatus relationship partition overlaps at ${key}`);
    }
    metadataKeys.keys.add(key);
  }
  const discoveryKeyHash = canonicalHash(discoveryKeys);
  const sidecarKeyHash = canonicalHash(metadataKeys.keys);
  if (
    discoveryKeys.size !== metadataKeys.keys.size ||
    discoveryKeyHash !== sidecarKeyHash
  ) {
    throw new Error(
      `BillStatus relationship partition differs: discovery=${discoveryKeys.size}, ` +
        `sidecar=${metadataKeys.keys.size}`,
    );
  }
  if (
    source.selected_record_count !== discoveryKeys.size ||
    source.top_level_metadata_count !== metadataKeys.rowCount ||
    source.unresolved_measure_count !== unresolvedKeys.rowCount
  ) {
    throw new Error("BillStatus relationship sidecar counts are inconsistent");
  }

  const amendments = discoveryRecords
    .filter((record) => record.record_kind === "amendment")
    .sort((left, right) =>
      left.canonical_key.localeCompare(right.canonical_key, undefined, {
        numeric: true,
      }),
    );
  if (
    amendments.length !== unresolvedKeys.rowCount ||
    amendments.some((record) => !unresolvedKeys.keys.has(record.canonical_key))
  ) {
    throw new Error(
      "BillStatus unresolved partition is not exactly the discovery amendment partition",
    );
  }
  const gapByKey = new Map(
    unresolvedRows.map((row) => [row.canonical_key, row]),
  );
  const resolved = amendments.map((record) =>
    amendmentResolution(record, gapByKey.get(record.canonical_key)),
  );
  const amendmentEdges = resolved
    .map((item) => item.edge)
    .filter(Boolean);
  const amendmentMetadata = resolved.map((item) => item.metadata);
  const resolutionRows = resolved.map((item) => item.resolution);
  const nonMemberRows = resolutionRows.filter(
    (row) => row.disposition === "resolved_explicit_non_member_sponsor",
  );
  if (amendmentEdges.length + nonMemberRows.length !== amendments.length) {
    throw new Error("amendment relationship resolution does not reconcile");
  }

  const targetDir = path.resolve(outputDir);
  const outputEdges = await writeHybridNdjson({
    sourceFile: edges.path,
    additionalRows: amendmentEdges,
    outputFile: path.join(targetDir, "member_measure_edges.ndjson"),
  });
  const outputVerification = await writeHybridNdjson({
    additionalRows: [],
    outputFile: path.join(
      targetDir,
      "relationship_verification_ledger.ndjson",
    ),
  });
  const outputMetadata = await writeHybridNdjson({
    sourceFile: metadata.path,
    additionalRows: amendmentMetadata,
    outputFile: path.join(targetDir, "measure_metadata.ndjson"),
  });
  const outputResolution = await writeHybridNdjson({
    additionalRows: resolutionRows,
    outputFile: path.join(targetDir, "amendment_resolution_ledger.ndjson"),
  });
  const outputNonMember = await writeHybridNdjson({
    additionalRows: nonMemberRows,
    outputFile: path.join(targetDir, "non_member_sponsor_ledger.ndjson"),
  });
  if (
    outputEdges.record_count !== source.edge_count + amendmentEdges.length ||
    outputMetadata.record_count !== discoveryKeys.size ||
    outputResolution.record_count !== amendments.length ||
    outputNonMember.record_count !== nonMemberRows.length
  ) {
    throw new Error("hybrid relationship artifact counts do not reconcile");
  }

  const completedAt = new Date().toISOString();
  return {
    schema: "congress-sponsorship-enrichment-manifest-v1",
    status: "complete",
    started_at: completedAt,
    completed_at: completedAt,
    primary_source:
      "Official GovInfo BILLSTATUS bulk data for Congress.gov measures",
    discovery_manifest: path.resolve(discoveryManifestPath),
    discovery_records_sha256: discoveryRecordsSha256,
    selected_record_count: discoveryKeys.size,
    completed_records: [],
    edge_count: outputEdges.record_count,
    edge_counts: {
      ...source.edge_counts,
      sponsors: (source.edge_counts?.sponsors ?? 0) + amendmentEdges.length,
    },
    unresolved_measure_count: 0,
    resolved_amendment_count: amendments.length,
    amendment_member_sponsor_count: amendmentEdges.length,
    non_member_sponsor_disposition_count: nonMemberRows.length,
    edges_file: path.basename(outputEdges.path),
    edges_sha256: outputEdges.content_sha256,
    edges_file_sha256: outputEdges.sha256,
    verification_ledger_file: path.basename(outputVerification.path),
    verification_ledger_sha256: outputVerification.content_sha256,
    verification_ledger_file_sha256: outputVerification.sha256,
    measure_metadata_file: path.basename(outputMetadata.path),
    measure_metadata_sha256: outputMetadata.content_sha256,
    measure_metadata_file_sha256: outputMetadata.sha256,
    source_gap_ledger_file: gaps.path,
    source_gap_ledger_sha256: gaps.sha256,
    amendment_resolution_ledger_file: path.basename(outputResolution.path),
    amendment_resolution_ledger_sha256: outputResolution.sha256,
    non_member_sponsor_ledger_file: path.basename(outputNonMember.path),
    non_member_sponsor_ledger_sha256: outputNonMember.sha256,
    destructive_filtering: false,
    exclusion_decisions_made: 0,
    candidate_creation_performed: false,
    semantic_exclusion_performed: false,
    automatic_scoring_performed: false,
    production_write_performed: false,
    sync_or_promotion_performed: false,
    membership_disposition_authority: false,
    scoring_authority: false,
    candidate_partition: {
      discovery_count: discoveryKeys.size,
      billstatus_top_level_count: metadataKeys.rowCount,
      resolved_amendment_count: amendments.length,
      unresolved_amendment_count: 0,
      member_sponsored_amendment_count: amendmentEdges.length,
      explicit_non_member_sponsor_count: nonMemberRows.length,
      missing_count: 0,
      extra_count: 0,
      canonical_keys_sha256: discoveryKeyHash,
    },
    billstatus_relationship_reuse: {
      schema: "congress-billstatus-relationship-reuse-v1",
      pointer: await fileReceipt(pointerFile),
      source_manifest: sourceManifestReceipt,
      source_edges: edges,
      source_verification_ledger: verification,
      source_measure_metadata: metadata,
      source_gap_ledger: gaps,
      official_source_only: true,
      exact_candidate_partition: true,
      browser_navigation_performed: false,
      hybrid_resolution: {
        schema: "congress-amendment-sponsor-resolution-v1",
        source:
          "Validated Congress.gov Advanced Search discovery result-card evidence",
        amendment_count: amendments.length,
        member_sponsor_edge_count: amendmentEdges.length,
        explicit_non_member_sponsor_count: nonMemberRows.length,
        original_billstatus_gaps_preserved: true,
        outputs: {
          edges: outputEdges,
          verification_ledger: outputVerification,
          measure_metadata: outputMetadata,
          amendment_resolution_ledger: outputResolution,
          non_member_sponsor_ledger: outputNonMember,
        },
      },
    },
  };
}
