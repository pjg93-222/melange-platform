import fs from "node:fs/promises";
import path from "node:path";

const [sourcePath, targetPath, cycleLabel] = process.argv.slice(2);
if (!sourcePath || !targetPath || !cycleLabel) {
  throw new Error(
    "usage: filter_industry_cycle_data.mjs <source.json> <target.json> <cycle-label>",
  );
}

const payload = JSON.parse(await fs.readFile(sourcePath, "utf8"));
const selectedCycle = payload.cycles.find(
  (cycle) => cycle.label === cycleLabel || String(cycle.cycle) === cycleLabel,
);
if (!selectedCycle) {
  throw new Error(`Unknown cycle: ${cycleLabel}`);
}

const cycleNumber = selectedCycle.cycle;
payload.cycles = [selectedCycle];
for (const key of [
  "raw_rows",
  "employees",
  "committee_map",
  "downstream_activity",
  "downstream_reach",
  "downstream_measurement_ledger",
  "downstream_sources",
  "beneficiary_bridge",
  "beneficiary_summary",
  "candidate_master",
  "candidate_committee_links",
  "transfer_edges",
  "transfer_paths",
  "funding_cohorts",
  "allocation_estimates",
  "allocation_coverage",
  "attribution_summary",
  "source_freshness",
  "classification_review",
  "recipient_detail",
  "contributor_detail",
]) {
  payload[key] = (payload[key] ?? []).filter(
    (row) => row.cycle === cycleNumber,
  );
}
payload.cycle_exceptions = (payload.cycle_exceptions ?? []).filter(
  (row) => row.source_cycle === cycleNumber,
);
if (payload.metadata?.expected_cycle_totals != null) {
  payload.metadata.expected_cycle_totals = {
    [selectedCycle.label]:
      payload.metadata.expected_cycle_totals[selectedCycle.label] ?? 0,
  };
}
if (payload.metadata?.omitted_non_accepted_by_cycle != null) {
  payload.metadata.omitted_non_accepted_by_cycle = {
    [String(cycleNumber)]:
      payload.metadata.omitted_non_accepted_by_cycle[String(cycleNumber)] ?? 0,
  };
}

await fs.mkdir(path.dirname(targetPath), { recursive: true });
const pendingPath = `${targetPath}.pending-${process.pid}`;
await fs.writeFile(pendingPath, JSON.stringify(payload), "utf8");
await fs.rename(pendingPath, targetPath);
console.log(
  `CYCLE CACHE ${selectedCycle.label} raw=${payload.raw_rows.length.toLocaleString("en-US")} `
  + `employees=${payload.employees.length.toLocaleString("en-US")} output=${targetPath}`,
);
