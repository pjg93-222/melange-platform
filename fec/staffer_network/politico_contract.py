# -*- coding: utf-8 -*-
"""Re-audit an authorized Politico directory snapshot without publishing rows."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import heapq
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping

from .source_manifest import sha256_file, validate_manifest_files


SCHEMA_ID = "politico-directory-crawl-receipt-v1"
SOURCE_SYSTEM_ID = "politico-pro-staffer-directory"
PARTITIONS = ("Democratic", "Republican", "Unknown", "Independent")
RETAINED_FIELDS = (
    "issue_areas",
    "name",
    "partition",
    "partition_ordinal",
    "profile_uuid",
    "roles",
)
_FILE_NAMES = {
    "directory_rows.ndjson",
    "request_log.ndjson",
    "partition_inventory.json",
}
_REQUEST_FIELDS = {
    "requested_at", "party", "offset", "limit", "count", "duration_ms", "status"
}
_PROHIBITED_KEY_FRAGMENTS = {
    "address", "contact", "email", "fax", "mobile", "phone", "telephone"
}
_EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_PHONE = re.compile(
    r"(?<!\d)(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]\d{3}[ .-]\d{4}(?!\d)"
)


class PoliticoCrawlError(ValueError):
    """Raised when a crawl cannot be deterministically audited."""


def _timestamp_ms(value: Any, *, context: str) -> int:
    if not isinstance(value, str):
        raise PoliticoCrawlError(f"{context}: timestamp must be a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PoliticoCrawlError(f"{context}: invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise PoliticoCrawlError(f"{context}: timestamp lacks timezone")
    return int(parsed.timestamp() * 1000)


def _ndjson(path: Path):
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise PoliticoCrawlError(f"{path.name}:{line_number}: blank NDJSON line")
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PoliticoCrawlError(
                    f"{path.name}:{line_number}: invalid JSON"
                ) from exc
            if not isinstance(value, dict):
                raise PoliticoCrawlError(
                    f"{path.name}:{line_number}: record must be an object"
                )
            yield line_number, value


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for nested in value.values():
            yield from _strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _strings(nested)


def _mapping_keys(value: Any):
    if isinstance(value, Mapping):
        for key, nested in value.items():
            yield str(key)
            yield from _mapping_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _mapping_keys(nested)


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def politico_directory_receipt(
    run_root: str | Path,
    manifest: Mapping[str, Any],
    *,
    generated_at: str,
) -> dict[str, Any]:
    """Return a reproducible, contact-free receipt for one sealed crawl.

    Source rows are streamed and never copied into the returned object.  Access,
    internal analysis, and redistribution remain distinct manifest dimensions.
    """

    root = Path(run_root)
    validate_manifest_files(manifest, root)
    if manifest.get("source_system_id") != SOURCE_SYSTEM_ID:
        raise PoliticoCrawlError("unexpected source_system_id")
    if manifest.get("authorization_status") != "AUTHORIZED":
        raise PoliticoCrawlError("Politico snapshot is not authorized")
    if manifest.get("redistribution_class") == "prohibited":
        raise PoliticoCrawlError("Politico source has prohibited redistribution status")
    advertised = {entry["path"] for entry in manifest["files"]}
    if advertised != _FILE_NAMES:
        raise PoliticoCrawlError("Politico manifest file inventory is not closed")

    inventory_path = root / "partition_inventory.json"
    rows_path = root / "directory_rows.ndjson"
    requests_path = root / "request_log.ndjson"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if not isinstance(inventory, dict) or inventory.get("schema_id") != (
        "politico-directory-partition-inventory-v1"
    ):
        raise PoliticoCrawlError("partition inventory schema mismatch")
    partition_rows = inventory.get("partitions")
    if not isinstance(partition_rows, list) or len(partition_rows) != len(PARTITIONS):
        raise PoliticoCrawlError("partition inventory is incomplete")
    expected: dict[str, int] = {}
    for index, item in enumerate(partition_rows):
        if not isinstance(item, dict) or set(item) != {"party", "total"}:
            raise PoliticoCrawlError("partition inventory entry shape mismatch")
        party, total = item["party"], item["total"]
        if party != PARTITIONS[index]:
            raise PoliticoCrawlError("partition inventory order mismatch")
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            raise PoliticoCrawlError("partition total must be nonnegative")
        expected[party] = total
    source_census = inventory.get("total")
    pages_expected = inventory.get("task_pages")
    if not isinstance(source_census, int) or source_census <= 0:
        raise PoliticoCrawlError("partition census must be positive")
    if source_census != sum(expected.values()):
        raise PoliticoCrawlError("partition census does not conserve totals")
    if not isinstance(pages_expected, int) or pages_expected <= 0:
        raise PoliticoCrawlError("task_pages must be positive")

    observed: Counter[str] = Counter()
    ordinal_sets: dict[str, set[int]] = {party: set() for party in PARTITIONS}
    profile_uuids: set[str] = set()
    duplicate_uuids = 0
    retained_fields: set[str] = set()
    prohibited_keys: set[str] = set()
    role_assertions = 0
    issue_rows = 0
    contact_pattern_rows = 0
    failures: list[str] = []
    observed_rows = 0
    for line_number, row in _ndjson(rows_path):
        observed_rows += 1
        keys = set(row)
        retained_fields.update(keys)
        if keys != set(RETAINED_FIELDS):
            failures.append(f"row {line_number}: retained-field allowlist mismatch")
        for key in _mapping_keys(row):
            normalized = re.sub(r"[^a-z]", "", key.lower())
            if any(fragment in normalized for fragment in _PROHIBITED_KEY_FRAGMENTS):
                prohibited_keys.add(key)

        party = row.get("partition")
        ordinal = row.get("partition_ordinal")
        profile_uuid = row.get("profile_uuid")
        name = row.get("name")
        roles = row.get("roles")
        issue_areas = row.get("issue_areas")
        if party not in expected:
            raise PoliticoCrawlError(f"row {line_number}: unknown partition")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
            raise PoliticoCrawlError(f"row {line_number}: invalid partition ordinal")
        if ordinal in ordinal_sets[party]:
            failures.append(f"partition {party}: duplicate ordinal")
        ordinal_sets[party].add(ordinal)
        if not isinstance(profile_uuid, str) or not profile_uuid.strip():
            raise PoliticoCrawlError(f"row {line_number}: missing profile UUID")
        if profile_uuid in profile_uuids:
            duplicate_uuids += 1
        profile_uuids.add(profile_uuid)
        if not isinstance(name, str) or not name.strip():
            raise PoliticoCrawlError(f"row {line_number}: missing name")
        if not isinstance(roles, list) or not all(isinstance(value, str) for value in roles):
            raise PoliticoCrawlError(f"row {line_number}: roles must be strings")
        if not isinstance(issue_areas, list) or not all(
            isinstance(value, str) for value in issue_areas
        ):
            raise PoliticoCrawlError(f"row {line_number}: issue areas must be strings")
        observed[party] += 1
        role_assertions += len(roles)
        issue_rows += bool(issue_areas)
        if any(_EMAIL.search(value) or _PHONE.search(value) for value in _strings(row)):
            contact_pattern_rows += 1

    for party in PARTITIONS:
        if ordinal_sets[party] != set(range(1, observed[party] + 1)):
            failures.append(f"partition {party}: ordinals are not contiguous")

    request_starts: list[int] = []
    request_durations: list[int] = []
    request_failures = 0
    request_partition_counts: Counter[str] = Counter()
    request_offsets: dict[str, list[int]] = {party: [] for party in PARTITIONS}
    for line_number, request in _ndjson(requests_path):
        if set(request) != _REQUEST_FIELDS:
            raise PoliticoCrawlError(
                f"request_log.ndjson:{line_number}: request shape mismatch"
            )
        party = request["party"]
        if party not in expected:
            raise PoliticoCrawlError(
                f"request_log.ndjson:{line_number}: unknown partition"
            )
        offset, limit, count, duration = (
            request["offset"], request["limit"], request["count"], request["duration_ms"]
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in (offset, limit, count, duration)
        ) or limit <= 0 or count > limit:
            raise PoliticoCrawlError(
                f"request_log.ndjson:{line_number}: invalid request counts"
            )
        started = _timestamp_ms(
            request["requested_at"], context=f"request_log.ndjson:{line_number}"
        )
        request_starts.append(started)
        request_durations.append(duration)
        request_partition_counts[party] += 1
        request_offsets[party].append(offset)
        request_failures += request["status"] != "OK"

    pages_observed = len(request_starts)
    for party in PARTITIONS:
        expected_pages = math.ceil(expected[party] / 100)
        if request_partition_counts[party] != expected_pages:
            failures.append(f"partition {party}: request-page count mismatch")
        if sorted(request_offsets[party]) != list(range(0, expected[party], 100)):
            failures.append(f"partition {party}: request offsets are not contiguous")

    ordered_starts = sorted(request_starts)
    left = 0
    max_rolling = 0
    for right, started in enumerate(ordered_starts):
        while started - ordered_starts[left] >= 60_000:
            left += 1
        max_rolling = max(max_rolling, right - left + 1)

    active_ends: list[int] = []
    max_in_flight = 0
    for started, duration in sorted(zip(request_starts, request_durations)):
        while active_ends and active_ends[0] <= started:
            heapq.heappop(active_ends)
        heapq.heappush(active_ends, started + duration)
        max_in_flight = max(max_in_flight, len(active_ends))

    if dict(observed) != expected:
        failures.append("observed partition rows do not reconcile")
    if observed_rows != source_census:
        failures.append("observed rows do not reconcile to census")
    if duplicate_uuids:
        failures.append("duplicate profile UUIDs observed")
    if pages_observed != pages_expected:
        failures.append("request pages do not reconcile")
    if request_failures:
        failures.append("request failures observed")
    if max_rolling > 60:
        failures.append("rolling-minute request ceiling exceeded")
    if max_in_flight > 4:
        failures.append("four-request concurrency ceiling exceeded")
    if retained_fields != set(RETAINED_FIELDS):
        failures.append("retained-field inventory is not exactly allowlisted")
    if prohibited_keys:
        failures.append("prohibited contact-like keys observed")
    if contact_pattern_rows:
        failures.append("email or phone patterns observed")

    return {
        "schema_id": SCHEMA_ID,
        "generated_at": generated_at,
        "status": "PASS" if not failures else "FAIL",
        "source_census": source_census,
        "observed_rows": observed_rows,
        "unique_profile_uuids": len(profile_uuids),
        "duplicate_uuid_occurrences": duplicate_uuids,
        "partition_expected": expected,
        "partition_observed": {party: observed[party] for party in PARTITIONS},
        "pages_expected": pages_expected,
        "pages_observed": pages_observed,
        "request_failures": request_failures,
        "max_requests_in_any_rolling_minute": max_rolling,
        "max_in_flight_requests": max_in_flight,
        "request_duration_p95_ms": _p95(request_durations),
        "role_assertions": role_assertions,
        "rows_with_issue_areas": issue_rows,
        "prohibited_contact_keys": sorted(prohibited_keys),
        "rows_matching_email_or_phone_patterns": contact_pattern_rows,
        "retained_fields": sorted(retained_fields),
        "rows_sha256": sha256_file(rows_path),
        "request_log_sha256": sha256_file(requests_path),
        "partition_inventory_sha256": sha256_file(inventory_path),
        "failures": failures,
    }
