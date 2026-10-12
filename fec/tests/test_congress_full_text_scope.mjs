import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { gunzipSync } from "node:zlib";

import {
  buildOfficialTextArchive,
  buildRecordCategorySpecs,
} from "../enrich_congress_text_relevance_pages.mjs";

const REGISTRY = path.join(
  import.meta.dirname,
  "..",
  "..",
  "source_data",
  "legislation",
  "acu_legislation_search_registry.json",
);

test("every record expands to all 19 categories and all 210 identifiers", async () => {
  const registry = JSON.parse(await fs.readFile(REGISTRY, "utf8"));
  const identifierCount = registry.categories.reduce(
    (count, category) => count + category.identifiers.length,
    0,
  );
  assert.equal(registry.categories.length, 19);
  assert.equal(identifierCount, 210);

  const directId = registry.categories[0].id;
  const specs = buildRecordCategorySpecs({
    registryCategories: registry.categories,
    directMembershipIds: [directId],
    coverageProbeIds: ["coverage-probe-example"],
  });
  assert.equal(specs.length, 19);
  assert.equal(
    specs.reduce((count, category) => count + category.identifiers.length, 0),
    210,
  );
  assert.equal(
    specs.filter((category) => category.direct_category_membership).length,
    1,
  );
  assert.equal(
    specs.filter((category) => category.cross_category_full_text_audit).length,
    18,
  );
  assert.ok(
    specs.every(
      (category) =>
        category.record_direct_membership_query_ids[0] === directId &&
        category.source_coverage_probe_ids[0] === "coverage-probe-example",
    ),
  );
});

test("complete visible official text archive is exact and hash bound", () => {
  const content =
    "Shown Here:\nThis section amends the Federal Credit Union Act.\n";
  const archive = buildOfficialTextArchive(content);
  assert.equal(gunzipSync(archive.compressed).toString("utf8"), content);
  assert.equal(
    archive.content_sha256,
    crypto.createHash("sha256").update(content).digest("hex"),
  );
  assert.equal(
    archive.gzip_sha256,
    crypto.createHash("sha256").update(archive.compressed).digest("hex"),
  );
  assert.equal(archive.uncompressed_bytes, Buffer.byteLength(content, "utf8"));
});
