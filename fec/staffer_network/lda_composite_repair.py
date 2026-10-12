# -*- coding: utf-8 -*-
"""Build a fail-closed LDA composite from a sealed legacy sweep and repairs.

The legacy sweep is never rewritten.  Static-year pagination misses are
localized by an offline ordering proof and repaired with explicitly ordered,
date-bounded twin fetches.  The moving 2026 corpus is fetched in full through
a fixed exclusive cutoff.  Every accepted fresh payload is retained in an
immutable machine-local generation and reconciled by UUID and canonical hash.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

FEC_ROOT = Path(__file__).resolve().parents[1]
if str(FEC_ROOT) not in sys.path:
    sys.path.insert(0, str(FEC_ROOT))

from lobbying import collect_lda_filings as collector
from staffer_network.lda_contract import sweep_tree_inventory
from staffer_network.schema_validation import validate_instance
from staffer_network.source_manifest import canonical_json_bytes, sha256_file


class CompositeRepairError(RuntimeError):
    """A fail-closed composite-repair invariant was violated."""


class SharedRequestPacer:
    """Thread-safe aggregate request-start pacer for one registered run."""

    def __init__(
        self,
        rpm: int,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if rpm <= 0:
            raise CompositeRepairError("pacer rpm must be positive")
        self.interval = 60.0 / rpm
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn
        self._lock = threading.Lock()
        self._next_start = 0.0

    def wait(self) -> None:
        with self._lock:
            now = self.monotonic_fn()
            target = max(now, self._next_start)
            self._next_start = target + self.interval
        delay = target - now
        if delay > 0:
            self.sleep_fn(delay)


API_BASE = collector.API_BASE
PAGE_SIZE = collector.SWEEP_PAGE_SIZE
MAX_IN_FLIGHT_REQUESTS = 8
LEGACY_YEARS = (2021, 2022, 2023, 2024, 2025, 2026)
APPROVED_LEGACY_INVENTORY_SHA256 = (
    "d7304591ee11a5d5c58ab7ff636d429509f8332cd473faca4dd5e7a15594961e"
)
ORDERING = "dt_posted,registrant__name"
PAYLOAD_SUPERSESSION_POLICY = (
    "fresh-authoritative-with-per-record-diff-ledger"
)
DATE_FILTER_TIMEZONE = ZoneInfo("America/New_York")
HISTORICAL_DATETIME_CONTROL = {
    "filing_year": 2022,
    "first_timestamp": "2023-01-20T14:31:13Z",
    "first_uuid_hashes": {
        "445e29a7-6d97-475f-8cc0-ef7a24df27f8": (
            "fca5d1e9de62f91ddcc08bc38f34ed1d28f2d66c439ad6e6364e03110864a733"
        ),
        "c45b0db7-2988-4257-8d09-8b36c0e840be": (
            "a5cac2d6d3d34a9f68d9f2b5b8533ba92da251039de4621c7425f96f42e08572"
        ),
    },
    "next_uuid_hashes": {
        "1c376b3b-8bc2-4b48-8d97-dd075362ed2c": (
            "c7bd7100361c4de716788f83cc949acb583b37a806ba1b9dea95d27f61866696"
        ),
    },
}
REPAIR_DATES: dict[int, tuple[str, ...]] = {
    2022: ("2023-01-20",),
    2023: ("2024-01-19", "2024-01-22"),
    2024: ("2025-01-20", "2025-01-21"),
    2025: ("2026-01-20",),
}
UUID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        temporary.write_bytes(canonical_json_bytes(value))
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


TWIN_COMPARISON_POLICY = "ignore-live-registrant-dt-updated-v1"


def _twin_comparison_hash(filing: Mapping[str, Any]) -> str:
    """Hash filing evidence without one live related-entity freshness field.

    The filings endpoint embeds the registrant's current ``dt_updated`` value in
    every historical filing response. That value can change while a long twin
    acquisition is running even though the filing itself is unchanged. Raw API
    bytes and their exact hashes remain preserved; only twin equivalence ignores
    this single, documented non-filing field.
    """

    projected = dict(filing)
    registrant = filing.get("registrant")
    if isinstance(registrant, Mapping) and "dt_updated" in registrant:
        projected_registrant = dict(registrant)
        projected_registrant.pop("dt_updated", None)
        projected["registrant"] = projected_registrant
    return _canonical_hash(projected)


def _parse_timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise CompositeRepairError(f"{label}: missing dt_posted")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CompositeRepairError(f"{label}: invalid dt_posted {value!r}") from exc
    if parsed.tzinfo is None:
        raise CompositeRepairError(f"{label}: dt_posted lacks a timezone")
    return parsed


def _date_value(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise CompositeRepairError(f"invalid ISO date {value!r}") from exc
    if parsed.isoformat() != value:
        raise CompositeRepairError(f"date is not canonical YYYY-MM-DD: {value!r}")
    return parsed


def _boundary_datetime(value: str, *, label: str) -> datetime:
    """Parse an API date/datetime boundary into an aware UTC datetime."""

    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return datetime.combine(
            _date_value(value), datetime.min.time(), DATE_FILTER_TIMEZONE
        ).astimezone(timezone.utc)
    return _parse_timestamp(value, label=label).astimezone(timezone.utc)


def _timestamp_key(value: Any, *, label: str) -> str:
    parsed = _parse_timestamp(value, label=label).astimezone(timezone.utc)
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _serialize_second(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    if normalized.microsecond:
        raise CompositeRepairError("exact-second boundary has sub-second precision")
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


def _serialize_inclusive_microsecond_end(
    start: datetime, *, seconds: int = 1
) -> str:
    if seconds < 1:
        raise CompositeRepairError("inclusive span must be at least one second")
    end = start.astimezone(timezone.utc) + timedelta(
        seconds=seconds, microseconds=-1
    )
    return end.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _page_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("page_*.json"), key=lambda path: path.name)


def audit_legacy_ordering(
    sweeps_root: str | Path, *, expected_years: Iterable[int] = LEGACY_YEARS
) -> dict[str, Any]:
    """Prove monotone legacy order and localize every repeated UUID to a tie."""

    root = Path(sweeps_root)
    years = tuple(sorted(set(expected_years)))
    rows: list[dict[str, Any]] = []
    duplicate_total = 0
    same_timestamp_total = 0
    for year in years:
        directory = root / str(year)
        page_paths = _page_files(directory)
        previous_key: str | None = None
        previous_location: str | None = None
        violations: list[dict[str, str]] = []
        first: dict[str, tuple[str, str, str]] = {}
        duplicates: list[dict[str, Any]] = []
        timestamp_counts: Counter[str] = Counter()
        object_count = 0
        page_shape_violations: list[dict[str, Any]] = []
        for page_index, page_path in enumerate(page_paths, start=1):
            try:
                page = json.loads(page_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CompositeRepairError(
                    f"cannot parse legacy page {page_path}: {exc}"
                ) from exc
            results = page.get("results")
            if not isinstance(results, list):
                raise CompositeRepairError(f"{page_path}: results is not a list")
            terminal = page_index == len(page_paths)
            expected_nonterminal = PAGE_SIZE
            if not terminal and (
                len(results) != expected_nonterminal or page.get("next") is None
            ):
                page_shape_violations.append({
                    "page": page_path.name,
                    "terminal": False,
                    "results": len(results),
                    "next_is_null": page.get("next") is None,
                })
            if terminal and (
                len(results) < 1
                or len(results) > PAGE_SIZE
                or page.get("next") is not None
            ):
                page_shape_violations.append({
                    "page": page_path.name,
                    "terminal": True,
                    "results": len(results),
                    "next_is_null": page.get("next") is None,
                })
            for index, filing in enumerate(results):
                label = f"{year}/{page_path.name}:{index}"
                if not isinstance(filing, dict):
                    raise CompositeRepairError(f"{label}: filing is not an object")
                filing_uuid = filing.get("filing_uuid")
                if not isinstance(filing_uuid, str) or not filing_uuid:
                    raise CompositeRepairError(f"{label}: missing filing_uuid")
                timestamp = filing.get("dt_posted")
                _parse_timestamp(timestamp, label=label)
                assert isinstance(timestamp, str)
                if previous_key is not None and timestamp < previous_key:
                    violations.append({
                        "previous": previous_key,
                        "previous_location": previous_location or "",
                        "current": timestamp,
                        "current_location": label,
                    })
                previous_key = timestamp
                previous_location = label
                object_count += 1
                timestamp_counts[timestamp] += 1
                if filing_uuid in first:
                    first_timestamp, first_page, first_hash = first[filing_uuid]
                    duplicate = {
                        "filing_uuid": filing_uuid,
                        "first_page": first_page,
                        "repeat_page": page_path.name,
                        "dt_posted": timestamp,
                        "same_exact_timestamp": first_timestamp == timestamp,
                        "identical_payload": first_hash == _canonical_hash(filing),
                    }
                    duplicates.append(duplicate)
                else:
                    first[filing_uuid] = (
                        timestamp,
                        page_path.name,
                        _canonical_hash(filing),
                    )
        same_timestamp = sum(
            1 for duplicate in duplicates if duplicate["same_exact_timestamp"]
        )
        identical = sum(
            1 for duplicate in duplicates if duplicate["identical_payload"]
        )
        duplicate_total += len(duplicates)
        same_timestamp_total += same_timestamp
        rows.append({
            "year": year,
            "pages": len(_page_files(directory)),
            "filing_objects": object_count,
            "unique_filing_uuids": len(first),
            "ascending_monotonic": not violations,
            "monotonicity_violations": violations,
            "duplicate_occurrences": len(duplicates),
            "duplicates_same_exact_timestamp": same_timestamp,
            "duplicates_with_identical_payload": identical,
            "duplicate_dates": dict(sorted(Counter(
                duplicate["dt_posted"][:10] for duplicate in duplicates
            ).items())),
            "duplicate_timestamps": sorted({
                duplicate["dt_posted"] for duplicate in duplicates
            }),
            "objects_in_timestamp_ties": sum(
                count for count in timestamp_counts.values() if count > 1
            ),
            "page_shape_pass": not page_shape_violations,
            "page_shape_violations": page_shape_violations,
        })
    passed = (
        duplicate_total == 88
        and same_timestamp_total == duplicate_total
        and all(row["ascending_monotonic"] for row in rows)
        and all(
            row["duplicates_with_identical_payload"] == row["duplicate_occurrences"]
            for row in rows
        )
        and all(row["page_shape_pass"] for row in rows)
    )
    return {
        "ordering_key": "dt_posted",
        "direction": "ascending",
        "years": rows,
        "duplicate_occurrences": duplicate_total,
        "duplicates_same_exact_timestamp": same_timestamp_total,
        "page_shape_pass": all(row["page_shape_pass"] for row in rows),
        "pass": passed,
    }


def bounded_page_url(
    filing_year: int,
    start_date: str | None,
    end_date_exclusive: str | None,
    page_number: int,
    *,
    page_size: int = PAGE_SIZE,
    ordering: str = ORDERING,
) -> str:
    # The sealed legacy inventory remains 2021–2026. A bounded API query may
    # also backfill earlier LDA years without changing that inventory contract.
    if isinstance(filing_year, bool) or not isinstance(filing_year, int) or not 1996 <= filing_year <= date.today().year:
        raise CompositeRepairError(f"unsupported filing year {filing_year}")
    if page_number < 1 or page_size < 1 or page_size > PAGE_SIZE:
        raise CompositeRepairError("invalid page or page_size")
    query: list[tuple[str, str | int]] = [
        ("filing_year", filing_year),
        ("page", page_number),
        ("page_size", page_size),
        ("ordering", ordering),
    ]
    if start_date is not None:
        _boundary_datetime(start_date, label="start boundary")
        query.append(("filing_dt_posted_after", start_date))
    if end_date_exclusive is not None:
        _boundary_datetime(end_date_exclusive, label="end boundary")
        query.append(("filing_dt_posted_before", end_date_exclusive))
    if start_date is not None and end_date_exclusive is not None:
        if _boundary_datetime(start_date, label="start boundary") >= _boundary_datetime(
            end_date_exclusive, label="end boundary"
        ):
            raise CompositeRepairError("bounded window must be nonempty")
    return API_BASE + "?" + urlencode(query)


def _decode_page(data: bytes, *, label: str) -> dict[str, Any]:
    try:
        page = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompositeRepairError(f"{label}: invalid JSON: {exc}") from exc
    if not isinstance(page, dict):
        raise CompositeRepairError(f"{label}: page is not an object")
    if not isinstance(page.get("count"), int) or isinstance(page.get("count"), bool):
        raise CompositeRepairError(f"{label}: invalid count")
    if page["count"] < 0 or not isinstance(page.get("results"), list):
        raise CompositeRepairError(f"{label}: invalid results")
    if "next" not in page:
        raise CompositeRepairError(f"{label}: missing next")
    return page


def validate_page_shape(
    page: Mapping[str, Any],
    *,
    page_number: int,
    total_pages: int,
    advertised_count: int,
    page_size: int = PAGE_SIZE,
) -> None:
    """Prove deterministic count-derived pagination shape."""

    if total_pages < 1 or not (1 <= page_number <= total_pages):
        raise CompositeRepairError("invalid page position")
    if page.get("count") != advertised_count:
        raise CompositeRepairError(
            f"page {page_number}: source count moved within pass"
        )
    terminal = page_number == total_pages
    if advertised_count == 0:
        expected_results = 0
    elif terminal:
        expected_results = advertised_count % page_size or page_size
    else:
        expected_results = page_size
    if len(page["results"]) != expected_results:
        raise CompositeRepairError(
            f"page {page_number}: expected {expected_results} results, "
            f"observed {len(page['results'])}"
        )
    if terminal and page.get("next") is not None:
        raise CompositeRepairError(f"page {page_number}: terminal next is not null")
    if not terminal and page.get("next") is None:
        raise CompositeRepairError(f"page {page_number}: nonterminal next is null")


def _window_bounds(start_date: str, end_date_exclusive: str) -> tuple[datetime, datetime]:
    return (
        _boundary_datetime(start_date, label="start boundary"),
        _boundary_datetime(end_date_exclusive, label="end boundary"),
    )


def validate_bounded_page(
    page: Mapping[str, Any],
    *,
    filing_year: int,
    start_date: str,
    end_date_exclusive: str,
    prior_order_key: datetime | None = None,
) -> tuple[datetime | None, dict[str, str]]:
    """Validate identity, bounds, and nondecreasing timestamp order."""

    start, end = _window_bounds(start_date, end_date_exclusive)
    final_key = prior_order_key
    hashes: dict[str, str] = {}
    for index, filing in enumerate(page["results"]):
        label = f"bounded {filing_year} result {index}"
        if not isinstance(filing, dict):
            raise CompositeRepairError(f"{label}: filing is not an object")
        if filing.get("filing_year") != filing_year:
            raise CompositeRepairError(f"{label}: filing_year mismatch")
        filing_uuid = filing.get("filing_uuid")
        if not isinstance(filing_uuid, str) or UUID_RE.fullmatch(filing_uuid) is None:
            raise CompositeRepairError(f"{label}: invalid filing_uuid")
        timestamp = filing.get("dt_posted")
        parsed = _parse_timestamp(timestamp, label=label).astimezone(timezone.utc)
        if not (start <= parsed < end):
            raise CompositeRepairError(
                f"{label}: dt_posted is outside [{start_date}, {end_date_exclusive})"
            )
        if final_key is not None and parsed < final_key:
            raise CompositeRepairError(
                f"{label}: API did not honor nondecreasing dt_posted ordering"
            )
        final_key = parsed
        if filing_uuid in hashes:
            raise CompositeRepairError(f"{label}: duplicate UUID within one page")
        hashes[filing_uuid] = _canonical_hash(filing)
    return final_key, hashes


def _fetch(
    url: str,
    label: str,
    headers: Mapping[str, str],
    pacer: collector.RequestPacer,
    *,
    opener: Callable[..., Any] | None,
) -> bytes:
    return collector._fetch_url(
        url,
        label,
        dict(headers),
        pacer,
        opener=opener,
        max_tries=12,
        max_backoff_seconds=60,
    )


def _fetch_exact_second_cohort(
    directory: Path,
    *,
    window_id: str,
    twin: str,
    filing_year: int,
    timestamp_key: str,
    headers: Mapping[str, str],
    pacer: collector.RequestPacer,
    opener: Callable[..., Any] | None,
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    """Fetch one timestamp cohort without pagination and prove completeness."""

    start = _boundary_datetime(timestamp_key, label="tie timestamp")
    if start.microsecond:
        raise CompositeRepairError(
            f"{window_id}/{twin}: split timestamp has sub-second precision"
        )
    conceptual_end = start + timedelta(seconds=1)
    start_value = _serialize_second(start)
    query_end_inclusive = _serialize_inclusive_microsecond_end(start)
    conceptual_end_value = _serialize_second(conceptual_end)
    url = bounded_page_url(
        filing_year, start_value, query_end_inclusive, 1, page_size=PAGE_SIZE
    )
    label = f"{window_id} twin {twin} exact cohort {start_value}"
    data = _fetch(url, label, headers, pacer, opener=opener)
    page = _decode_page(data, label=label)
    if page["count"] > PAGE_SIZE:
        raise CompositeRepairError(
            f"{label}: exact-second cohort exceeds one page ({page['count']})"
        )
    if page.get("next") is not None:
        raise CompositeRepairError(f"{label}: exact-second cohort has next page")
    if len(page["results"]) != page["count"]:
        raise CompositeRepairError(
            f"{label}: result length {len(page['results'])} != count {page['count']}"
        )
    _, hashes = validate_bounded_page(
        page,
        filing_year=filing_year,
        start_date=start_value,
        end_date_exclusive=conceptual_end_value,
    )
    for index, filing in enumerate(page["results"]):
        observed_key = _timestamp_key(
            filing.get("dt_posted"), label=f"{label} result {index}"
        )
        if observed_key != timestamp_key:
            raise CompositeRepairError(
                f"{label}: result timestamp {observed_key} is not exact split "
                f"timestamp {timestamp_key}"
            )
    cohort_directory = directory / "tie_cohorts"
    cohort_directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(timestamp_key.encode("utf-8")).hexdigest()[:20]
    path = cohort_directory / f"cohort_{digest}.json"
    collector._write_bytes_atomic(data, str(path))
    inventory = {
        "timestamp": timestamp_key,
        "start_boundary": start_value,
        "query_before_inclusive": query_end_inclusive,
        "conceptual_end_exclusive": conceptual_end_value,
        "datetime_before_semantics": "INCLUSIVE",
        "path": path.relative_to(directory).as_posix(),
        "count": page["count"],
        "unique_filing_uuids": len(hashes),
        "next_is_null": page.get("next") is None,
        "result_length_matches_count": len(page["results"]) == page["count"],
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    return page, hashes, inventory


def fetch_window(
    root: str | Path,
    *,
    window_id: str,
    twin: str,
    filing_year: int,
    start_date: str,
    end_date_exclusive: str,
    headers: Mapping[str, str],
    pacer: Any,
    required_legacy_by_timestamp: Mapping[str, set[str]] | None = None,
    opener: Callable[..., Any] | None = None,
    page_size: int = PAGE_SIZE,
    max_in_flight_requests: int = MAX_IN_FLIGHT_REQUESTS,
    resume_existing_base_pages: bool = False,
    stop_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Fetch and repair one immutable window, returning its UUID/hash inventory."""

    if twin not in {"A", "B"}:
        raise CompositeRepairError("twin must be A or B")
    if page_size < 1 or page_size > PAGE_SIZE:
        raise CompositeRepairError("invalid base page size")
    if not (1 <= max_in_flight_requests <= MAX_IN_FLIGHT_REQUESTS):
        raise CompositeRepairError("invalid max in-flight request count")
    directory = Path(root) / "windows" / window_id / twin
    existing_page_numbers: list[int] = []
    if directory.exists() and any(directory.iterdir()):
        if not resume_existing_base_pages:
            raise CompositeRepairError(f"window directory is nonempty: {directory}")
        children = list(directory.iterdir())
        for child in children:
            if child.is_dir():
                if child.name != "tie_cohorts":
                    raise CompositeRepairError(
                        f"resume window contains unexpected directory: {child.name}"
                    )
                if any(
                    not candidate.is_file()
                    or re.fullmatch(
                        r"cohort_[0-9a-f]{20}\.json", candidate.name
                    ) is None
                    for candidate in child.iterdir()
                ):
                    raise CompositeRepairError(
                        "resume tie-cohort directory contains unexpected entries"
                    )
                continue
            if not child.is_file():
                raise CompositeRepairError(
                    f"resume window contains non-file entry: {child.name}"
                )
            match = re.fullmatch(r"page_([0-9]{5})\.json", child.name)
            if match is None:
                raise CompositeRepairError(
                    f"resume window contains unexpected file: {child.name}"
                )
            existing_page_numbers.append(int(match.group(1)))
        existing_page_numbers.sort()
        if existing_page_numbers != list(range(1, len(existing_page_numbers) + 1)):
            raise CompositeRepairError("resume window pages are not contiguous from page 1")
    directory.mkdir(parents=True, exist_ok=True)
    advertised_count = 0
    total_pages = 1
    order_key: datetime | None = None
    base_payloads: dict[str, dict[str, Any]] = {}
    base_timestamps: dict[str, str] = {}
    boundary_timestamps: set[str] = set()
    repeated_uuid_occurrences = 0
    file_inventory: list[dict[str, Any]] = []
    page_layout: list[list[str]] = []
    previous_page_last: str | None = None
    base_filing_objects = 0
    started_at = _now()

    def transport(page_number: int) -> bytes:
        if stop_event is not None and stop_event.is_set():
            raise CompositeRepairError(
                f"{window_id} twin {twin}: sibling twin stopped"
            )
        url = bounded_page_url(
            filing_year, start_date, end_date_exclusive, page_number,
            page_size=page_size,
        )
        label = f"{window_id} twin {twin} page {page_number}"
        # Production opener=None creates a fresh opener per request.  Workers
        # perform transport only and return immutable raw bytes.
        return _fetch(url, label, headers, pacer, opener=opener)

    def process_page(page_number: int, data: bytes, *, persist: bool = True) -> None:
        nonlocal order_key, previous_page_last, base_filing_objects
        nonlocal repeated_uuid_occurrences
        label = f"{window_id} twin {twin} page {page_number}"
        page = _decode_page(data, label=label)
        validate_page_shape(
            page,
            page_number=page_number,
            total_pages=total_pages,
            advertised_count=advertised_count,
            page_size=page_size,
        )
        order_key, page_hashes = validate_bounded_page(
            page,
            filing_year=filing_year,
            start_date=start_date,
            end_date_exclusive=end_date_exclusive,
            prior_order_key=order_key,
        )
        results = page["results"]
        base_filing_objects += len(results)
        row_uuids = [str(filing["filing_uuid"]) for filing in results]
        page_layout.append(row_uuids)
        current_first = (
            _timestamp_key(results[0].get("dt_posted"), label=f"{label} first")
            if results else None
        )
        current_last = (
            _timestamp_key(results[-1].get("dt_posted"), label=f"{label} last")
            if results else None
        )
        boundary_timestamp = None
        if previous_page_last is not None and current_first == previous_page_last:
            boundary_timestamp = current_first
            assert boundary_timestamp is not None
            boundary_timestamps.add(boundary_timestamp)
        overlap = set(base_payloads) & set(page_hashes)
        for filing in results:
            filing_uuid = str(filing["filing_uuid"])
            timestamp_key = _timestamp_key(
                filing.get("dt_posted"), label=f"{label}/{filing_uuid}"
            )
            if filing_uuid in overlap:
                if (
                    boundary_timestamp != timestamp_key
                    or base_timestamps[filing_uuid] != timestamp_key
                    or _canonical_hash(base_payloads[filing_uuid])
                    != page_hashes[filing_uuid]
                ):
                    raise CompositeRepairError(
                        f"{label}: repeated UUID {filing_uuid} is not an identical "
                        "same-timestamp boundary repeat"
                    )
                repeated_uuid_occurrences += 1
                continue
            base_payloads[filing_uuid] = filing
            base_timestamps[filing_uuid] = timestamp_key
        if results:
            previous_page_last = current_last
        path = directory / f"page_{page_number:05d}.json"
        if persist:
            collector._write_bytes_atomic(data, str(path))
        elif path.read_bytes() != data:
            raise CompositeRepairError(f"resume page changed during replay: {path.name}")
        file_inventory.append({
            "path": path.name,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })

    first_data = (
        (directory / "page_00001.json").read_bytes()
        if existing_page_numbers else transport(1)
    )
    first_page = _decode_page(
        first_data, label=f"{window_id} twin {twin} page 1"
    )
    advertised_count = first_page["count"]
    total_pages = max(1, (advertised_count + page_size - 1) // page_size)
    if existing_page_numbers and existing_page_numbers[-1] > total_pages:
        raise CompositeRepairError("resume window has pages beyond advertised count")
    process_page(1, first_data, persist=not existing_page_numbers)
    for page_number in existing_page_numbers[1:]:
        process_page(
            page_number,
            (directory / f"page_{page_number:05d}.json").read_bytes(),
            persist=False,
        )
    next_page = (existing_page_numbers[-1] + 1) if existing_page_numbers else 2
    if next_page <= total_pages:
        with ThreadPoolExecutor(
            max_workers=max_in_flight_requests,
            thread_name_prefix="lda-page",
        ) as executor:
            pending: dict[Any, int] = {}
            ready: dict[int, bytes] = {}
            next_to_submit = next_page
            next_to_process = next_page

            def fill_pipeline() -> None:
                nonlocal next_to_submit
                while (
                    next_to_submit <= total_pages
                    and len(pending) + len(ready) < max_in_flight_requests
                ):
                    future = executor.submit(transport, next_to_submit)
                    pending[future] = next_to_submit
                    next_to_submit += 1

            fill_pipeline()
            try:
                while pending:
                    completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in completed:
                        ready[pending.pop(future)] = future.result()
                    while next_to_process in ready:
                        process_page(next_to_process, ready.pop(next_to_process))
                        next_to_process += 1
                    fill_pipeline()
            except Exception:
                for future in pending:
                    future.cancel()
                raise
    required = {
        key: set(values)
        for key, values in (required_legacy_by_timestamp or {}).items()
    }
    repair_timestamps = sorted(boundary_timestamps | set(required))
    tie_cohorts: list[dict[str, Any]] = []
    for timestamp_key in repair_timestamps:
        page, cohort_hashes, cohort_inventory = _fetch_exact_second_cohort(
            directory,
            window_id=window_id,
            twin=twin,
            filing_year=filing_year,
            timestamp_key=timestamp_key,
            headers=headers,
            pacer=pacer,
            opener=opener,
        )
        missing_legacy = sorted(required.get(timestamp_key, set()) - set(cohort_hashes))
        if missing_legacy:
            raise CompositeRepairError(
                f"{window_id}/{twin}: legacy split cohort {timestamp_key} is "
                f"missing UUIDs {missing_legacy[:3]}"
            )
        removed = [
            filing_uuid for filing_uuid, observed in base_timestamps.items()
            if observed == timestamp_key
        ]
        for filing_uuid in removed:
            del base_payloads[filing_uuid]
            del base_timestamps[filing_uuid]
        for filing in page["results"]:
            filing_uuid = str(filing["filing_uuid"])
            if filing_uuid in base_payloads:
                raise CompositeRepairError(
                    f"{window_id}/{twin}: repaired cohort UUID {filing_uuid} "
                    "collides outside its timestamp"
                )
            base_payloads[filing_uuid] = filing
            base_timestamps[filing_uuid] = timestamp_key
        cohort_inventory["base_unique_rows_replaced"] = len(removed)
        cohort_inventory["required_legacy_uuids"] = len(required.get(timestamp_key, set()))
        tie_cohorts.append(cohort_inventory)
    expected_cohort_paths = {
        str(entry["path"]) for entry in tie_cohorts
    }
    cohort_directory = directory / "tie_cohorts"
    actual_cohort_paths = (
        {
            path.relative_to(directory).as_posix()
            for path in cohort_directory.iterdir()
            if path.is_file()
        }
        if cohort_directory.is_dir() else set()
    )
    if actual_cohort_paths != expected_cohort_paths:
        raise CompositeRepairError(
            f"{window_id}/{twin}: tie-cohort inventory is not closed after resume"
        )
    uuid_hashes = {
        filing_uuid: _canonical_hash(payload)
        for filing_uuid, payload in base_payloads.items()
    }
    if advertised_count != len(uuid_hashes):
        raise CompositeRepairError(
            f"{window_id} twin {twin}: advertised count {advertised_count} "
            f"!= repaired unique UUIDs {len(uuid_hashes)}"
        )
    manifest = {
        "schema_id": "staffer-network-lda-window-manifest-v1",
        "window_id": window_id,
        "twin": twin,
        "filing_year": filing_year,
        "start_date": start_date,
        "end_date_exclusive": end_date_exclusive,
        "ordering": ORDERING,
        "started_at": started_at,
        "completed_at": _now(),
        "page_count": len(file_inventory),
        "base_page_size": page_size,
        "max_in_flight_requests": max_in_flight_requests,
        "transport_mode": "BOUNDED_BATCH_ASCENDING_PROCESSING",
        "base_filing_objects": base_filing_objects,
        "base_unique_filing_uuids": base_filing_objects - repeated_uuid_occurrences,
        "base_repeated_uuid_occurrences": repeated_uuid_occurrences,
        "page_boundary_timestamps": sorted(boundary_timestamps),
        "page_layout_sha256": _canonical_hash(page_layout),
        "tie_cohorts": tie_cohorts,
        "advertised_count": advertised_count,
        "unique_filing_uuids": len(uuid_hashes),
        "files": file_inventory,
        "uuid_inventory_sha256": _canonical_hash(sorted(uuid_hashes)),
        "payload_inventory_sha256": _canonical_hash(sorted(uuid_hashes.items())),
        "complete": True,
    }
    _atomic_json(directory / "window_manifest.json", manifest)
    return {
        "manifest": manifest,
        "uuid_hashes": uuid_hashes,
        "directory": str(directory),
    }


def _comparison_mismatches(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    raw_payload_mismatches: list[str],
) -> list[str]:
    """Reparse only changed raw pages when applying the narrow volatility rule."""

    if not raw_payload_mismatches:
        return []
    if "comparison_hashes" in left and "comparison_hashes" in right:
        left_hashes = left["comparison_hashes"]
        right_hashes = right["comparison_hashes"]
        return sorted(
            filing_uuid
            for filing_uuid in raw_payload_mismatches
            if left_hashes.get(filing_uuid) != right_hashes.get(filing_uuid)
        )
    if not left.get("directory") or not right.get("directory"):
        return raw_payload_mismatches

    def file_hashes(result: Mapping[str, Any]) -> dict[str, str]:
        entries = [
            *result["manifest"].get("files", []),
            *result["manifest"].get("tie_cohorts", []),
        ]
        return {
            str(entry["path"]): str(entry["sha256"])
            for entry in entries
            if entry.get("path") and entry.get("sha256")
        }

    left_files = file_hashes(left)
    right_files = file_hashes(right)
    changed_paths = sorted(
        path
        for path in set(left_files) | set(right_files)
        if left_files.get(path) != right_files.get(path)
    )
    wanted = set(raw_payload_mismatches)

    def load_changed(result: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
        directory = Path(str(result["directory"]))
        found: dict[str, Mapping[str, Any]] = {}
        for relative in changed_paths:
            path = directory / relative
            if not path.is_file():
                continue
            page = _decode_page(
                path.read_bytes(), label=f"twin comparison {relative}"
            )
            for filing in page["results"]:
                filing_uuid = str(filing.get("filing_uuid"))
                if filing_uuid in wanted:
                    found[filing_uuid] = filing
        return found

    left_payloads = load_changed(left)
    right_payloads = load_changed(right)
    return sorted(
        filing_uuid
        for filing_uuid in raw_payload_mismatches
        if filing_uuid not in left_payloads
        or filing_uuid not in right_payloads
        or _twin_comparison_hash(left_payloads[filing_uuid])
        != _twin_comparison_hash(right_payloads[filing_uuid])
    )


def compare_twins(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> dict[str, Any]:
    left_manifest = left["manifest"]
    right_manifest = right["manifest"]
    left_hashes = left["uuid_hashes"]
    right_hashes = right["uuid_hashes"]
    missing_from_b = sorted(set(left_hashes) - set(right_hashes))
    missing_from_a = sorted(set(right_hashes) - set(left_hashes))
    raw_payload_mismatches = sorted(
        filing_uuid for filing_uuid in set(left_hashes) & set(right_hashes)
        if left_hashes[filing_uuid] != right_hashes[filing_uuid]
    )
    payload_mismatches = _comparison_mismatches(
        left, right, raw_payload_mismatches
    )
    ignored_live_registrant_timestamp_mismatches = sorted(
        set(raw_payload_mismatches) - set(payload_mismatches)
    )
    passed = (
        left_manifest["advertised_count"] == right_manifest["advertised_count"]
        and not missing_from_b
        and not missing_from_a
        and not payload_mismatches
    )
    return {
        "count_a": left_manifest["advertised_count"],
        "count_b": right_manifest["advertised_count"],
        "uuid_inventory_sha256_a": left_manifest["uuid_inventory_sha256"],
        "uuid_inventory_sha256_b": right_manifest["uuid_inventory_sha256"],
        "payload_inventory_sha256_a": left_manifest["payload_inventory_sha256"],
        "payload_inventory_sha256_b": right_manifest["payload_inventory_sha256"],
        "twin_comparison_policy": TWIN_COMPARISON_POLICY,
        "page_layout_sha256_a": left_manifest["page_layout_sha256"],
        "page_layout_sha256_b": right_manifest["page_layout_sha256"],
        "page_layout_equal": (
            left_manifest["page_layout_sha256"]
            == right_manifest["page_layout_sha256"]
        ),
        "base_repeated_uuid_occurrences_a": left_manifest[
            "base_repeated_uuid_occurrences"
        ],
        "base_repeated_uuid_occurrences_b": right_manifest[
            "base_repeated_uuid_occurrences"
        ],
        "page_boundary_timestamps_a": left_manifest["page_boundary_timestamps"],
        "page_boundary_timestamps_b": right_manifest["page_boundary_timestamps"],
        "tie_cohort_timestamps_a": [
            row["timestamp"] for row in left_manifest["tie_cohorts"]
        ],
        "tie_cohort_timestamps_b": [
            row["timestamp"] for row in right_manifest["tie_cohorts"]
        ],
        "missing_from_b": missing_from_b,
        "missing_from_a": missing_from_a,
        "raw_payload_mismatches": raw_payload_mismatches,
        "ignored_live_registrant_timestamp_mismatches": (
            ignored_live_registrant_timestamp_mismatches
        ),
        "payload_mismatches": payload_mismatches,
        "pass": passed,
    }


def query_count(
    filing_year: int,
    start_date: str | None,
    end_date_exclusive: str | None,
    *,
    headers: Mapping[str, str],
    pacer: collector.RequestPacer,
    opener: Callable[..., Any] | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    url = bounded_page_url(
        filing_year,
        start_date,
        end_date_exclusive,
        1,
        page_size=1,
    )
    label = f"count {filing_year} {start_date or '*'}..{end_date_exclusive or '*'}"
    page = _decode_page(_fetch(url, label, headers, pacer, opener=opener), label=label)
    return page["count"], page["results"]


def validate_historical_datetime_control(
    *,
    headers: Mapping[str, str],
    pacer: collector.RequestPacer,
    opener: Callable[..., Any] | None = None,
    control: Mapping[str, Any] = HISTORICAL_DATETIME_CONTROL,
) -> dict[str, Any]:
    """Pin a boundary-adjacent historical cohort that distinguishes <= from <."""

    year = int(control["filing_year"])
    start = _boundary_datetime(
        str(control["first_timestamp"]), label="historical control timestamp"
    )

    def load_span(label: str, span_start: datetime, seconds: int) -> dict[str, Any]:
        query_start = _serialize_second(span_start)
        query_end = _serialize_inclusive_microsecond_end(
            span_start, seconds=seconds
        )
        conceptual_end = _serialize_second(span_start + timedelta(seconds=seconds))
        url = bounded_page_url(
            year, query_start, query_end, 1, page_size=PAGE_SIZE
        )
        page = _decode_page(
            _fetch(url, label, headers, pacer, opener=opener), label=label
        )
        if (
            page["count"] > PAGE_SIZE
            or page.get("next") is not None
            or len(page["results"]) != page["count"]
        ):
            raise CompositeRepairError(
                f"{label}: historical control is not one complete page"
            )
        _, hashes = validate_bounded_page(
            page, filing_year=year, start_date=query_start,
            end_date_exclusive=conceptual_end,
        )
        return {
            "query_window": [query_start, query_end],
            "conceptual_end_exclusive": conceptual_end,
            "count": page["count"],
            "hashes": hashes,
            "timestamp_keys": sorted({
                _timestamp_key(row.get("dt_posted"), label=label)
                for row in page["results"]
            }),
        }

    first = load_span("historical exact T", start, 1)
    second = load_span("historical exact T+1", start + timedelta(seconds=1), 1)
    union = load_span("historical exact two-second union", start, 2)
    first_key = _timestamp_key(
        control["first_timestamp"], label="historical first key"
    )
    next_key = _timestamp_key(
        _serialize_second(start + timedelta(seconds=1)),
        label="historical next key",
    )
    expected_first = dict(control["first_uuid_hashes"])
    expected_second = dict(control["next_uuid_hashes"])
    expected_union = {**expected_first, **expected_second}
    passed = (
        first["count"] == 2
        and second["count"] == 1
        and union["count"] == 3
        and first["count"] + second["count"] == union["count"]
        and first["hashes"] == expected_first
        and second["hashes"] == expected_second
        and union["hashes"] == expected_union
        and first["timestamp_keys"] == [first_key]
        and second["timestamp_keys"] == [next_key]
        and union["timestamp_keys"] == [first_key, next_key]
    )
    receipt = {
        "filing_year": year,
        "datetime_before_semantics": "INCLUSIVE",
        "query_boundary_serialization": "UTC_Z_MICROSECOND_END_INCLUSIVE",
        "first": {
            **{key: value for key, value in first.items() if key != "hashes"},
            "uuid_payload_sha256": dict(sorted(first["hashes"].items())),
        },
        "second": {
            **{key: value for key, value in second.items() if key != "hashes"},
            "uuid_payload_sha256": dict(sorted(second["hashes"].items())),
        },
        "union": {
            **{key: value for key, value in union.items() if key != "hashes"},
            "uuid_payload_sha256": dict(sorted(union["hashes"].items())),
        },
        "partition_identity": first["count"] + second["count"] == union["count"],
        "pinned_payloads_match": (
            first["hashes"] == expected_first
            and second["hashes"] == expected_second
        ),
        "pass": passed,
    }
    if not passed:
        raise CompositeRepairError("historical datetime boundary control failed")
    return receipt


def validate_filter_semantics(
    *,
    headers: Mapping[str, str],
    pacer: collector.RequestPacer,
    opener: Callable[..., Any] | None = None,
    filing_year: int = 2026,
    first_date: str = "2026-08-01",
    historical_control: Mapping[str, Any] = HISTORICAL_DATETIME_CONTROL,
) -> dict[str, Any]:
    """Prove date, exact-second, empty-window, and offset filter semantics."""

    first = _date_value(first_date)
    second_date = (first + timedelta(days=1)).isoformat()
    third_date = (first + timedelta(days=2)).isoformat()
    first_count, first_rows = query_count(
        filing_year, first_date, second_date,
        headers=headers, pacer=pacer, opener=opener,
    )
    second_count, second_rows = query_count(
        filing_year, second_date, third_date,
        headers=headers, pacer=pacer, opener=opener,
    )
    union_count, union_rows = query_count(
        filing_year, first_date, third_date,
        headers=headers, pacer=pacer, opener=opener,
    )
    full_count, _ = query_count(
        filing_year, None, third_date,
        headers=headers, pacer=pacer, opener=opener,
    )
    for label, rows, start, end in (
        ("first", first_rows, first_date, second_date),
        ("second", second_rows, second_date, third_date),
        ("union", union_rows, first_date, third_date),
    ):
        if rows:
            validate_bounded_page(
                {"results": rows}, filing_year=filing_year,
                start_date=start, end_date_exclusive=end,
            )
    date_partition_pass = (
        first_count > 0
        and second_count > 0
        and first_count + second_count == union_count
        and union_count < full_count
    )
    if not first_rows:
        raise CompositeRepairError("date-filter probe returned no known timestamp")
    known_raw = first_rows[0].get("dt_posted")
    known = _parse_timestamp(known_raw, label="filter probe known timestamp")
    if known.microsecond:
        raise CompositeRepairError(
            "filter probe known timestamp has unsupported sub-second precision"
        )
    known_utc = known.astimezone(timezone.utc)
    exact_start = _serialize_second(known_utc)
    exact_middle = _serialize_second(known_utc + timedelta(seconds=1))
    exact_end = _serialize_second(known_utc + timedelta(seconds=2))
    exact_first_query_end = _serialize_inclusive_microsecond_end(known_utc)
    exact_second_query_end = _serialize_inclusive_microsecond_end(
        known_utc + timedelta(seconds=1)
    )
    exact_union_query_end = _serialize_inclusive_microsecond_end(
        known_utc, seconds=2
    )
    exact_first_count, exact_first_rows = query_count(
        filing_year, exact_start, exact_first_query_end,
        headers=headers, pacer=pacer, opener=opener,
    )
    exact_second_count, exact_second_rows = query_count(
        filing_year, exact_middle, exact_second_query_end,
        headers=headers, pacer=pacer, opener=opener,
    )
    exact_union_count, exact_union_rows = query_count(
        filing_year, exact_start, exact_union_query_end,
        headers=headers, pacer=pacer, opener=opener,
    )
    for label, rows, start, end in (
        ("exact first", exact_first_rows, exact_start, exact_middle),
        ("exact second", exact_second_rows, exact_middle, exact_end),
        ("exact union", exact_union_rows, exact_start, exact_end),
    ):
        if rows:
            validate_bounded_page(
                {"results": rows}, filing_year=filing_year,
                start_date=start, end_date_exclusive=end,
            )
    if any(
        _timestamp_key(row.get("dt_posted"), label="filter probe exact first")
        != _timestamp_key(known_raw, label="filter probe known timestamp")
        for row in exact_first_rows
    ):
        raise CompositeRepairError("exact-second probe returned a foreign timestamp")

    empty_window: tuple[str, str] | None = None
    empty_count: int | None = None
    midnight = datetime.combine(first, datetime.min.time(), timezone.utc)
    for offset in range(3):
        empty_start = _serialize_second(midnight + timedelta(seconds=offset))
        empty_end = _serialize_inclusive_microsecond_end(
            midnight + timedelta(seconds=offset)
        )
        candidate_count, _ = query_count(
            filing_year, empty_start, empty_end,
            headers=headers, pacer=pacer, opener=opener,
        )
        if candidate_count == 0:
            empty_window = (empty_start, empty_end)
            empty_count = candidate_count
            break
    if empty_window is None:
        raise CompositeRepairError(
            "datetime filter probe found no empty exact-second control"
        )

    assert isinstance(known_raw, str)
    original_end = (known + timedelta(microseconds=999999)).isoformat(
        timespec="microseconds"
    )
    offset_count, offset_rows = query_count(
        filing_year, known_raw, original_end,
        headers=headers, pacer=pacer, opener=opener,
    )
    if offset_rows:
        validate_bounded_page(
            {"results": offset_rows}, filing_year=filing_year,
            start_date=known_raw, end_date_exclusive=exact_middle,
        )
    exact_partition_pass = (
        exact_first_count > 0
        and exact_first_count + exact_second_count == exact_union_count
    )
    offset_equivalence_pass = offset_count == exact_first_count
    historical = validate_historical_datetime_control(
        headers=headers, pacer=pacer, opener=opener,
        control=historical_control,
    )
    passed = (
        date_partition_pass
        and exact_partition_pass
        and empty_count == 0
        and offset_equivalence_pass
        and historical["pass"]
    )
    receipt = {
        "filing_year": filing_year,
        "first_window": [first_date, second_date],
        "second_window": [second_date, third_date],
        "first_count": first_count,
        "second_count": second_count,
        "union_count": union_count,
        "full_bounded_count": full_count,
        "half_open_partition_identity": first_count + second_count == union_count,
        "filters_combine_with_filing_year": union_count < full_count,
        "exact_second_windows": [
            [exact_start, exact_first_query_end],
            [exact_middle, exact_second_query_end],
        ],
        "exact_second_first_count": exact_first_count,
        "exact_second_second_count": exact_second_count,
        "exact_second_union_count": exact_union_count,
        "exact_second_partition_identity": (
            exact_first_count + exact_second_count == exact_union_count
        ),
        "empty_exact_second_window": list(empty_window),
        "empty_exact_second_count": empty_count,
        "offset_equivalent_window": [known_raw, original_end],
        "offset_equivalent_count": offset_count,
        "offset_equivalence_pass": offset_equivalence_pass,
        "repair_boundary_serialization": "UTC_Z_MICROSECOND_END_INCLUSIVE",
        "historical_datetime_boundary_control": historical,
        "pass": passed,
    }
    if not passed:
        raise CompositeRepairError("date-filter semantic probe failed")
    return receipt


def _iter_unique_legacy(root: Path, year: int):
    seen: set[str] = set()
    for page_path in _page_files(root / str(year)):
        page = json.loads(page_path.read_text(encoding="utf-8"))
        for filing in page["results"]:
            filing_uuid = str(filing["filing_uuid"])
            if filing_uuid in seen:
                continue
            seen.add(filing_uuid)
            yield filing_uuid, filing


def legacy_year_inventory(root: str | Path, year: int) -> dict[str, Any]:
    hashes: dict[str, str] = {}
    timestamps: dict[str, str] = {}
    minimum: str | None = None
    maximum: str | None = None
    for filing_uuid, filing in _iter_unique_legacy(Path(root), year):
        timestamp = filing.get("dt_posted")
        _parse_timestamp(timestamp, label=f"legacy {year}/{filing_uuid}")
        assert isinstance(timestamp, str)
        hashes[filing_uuid] = _canonical_hash(filing)
        timestamps[filing_uuid] = timestamp
        minimum = timestamp if minimum is None else min(minimum, timestamp)
        maximum = timestamp if maximum is None else max(maximum, timestamp)
    return {
        "hashes": hashes,
        "timestamps": timestamps,
        "minimum_dt_posted": minimum,
        "maximum_dt_posted": maximum,
    }


def legacy_tie_cohorts(
    root: str | Path, year: int, timestamps: Iterable[str]
) -> dict[str, set[str]]:
    """Return all legacy UUIDs at each reviewer-localized split timestamp."""

    required = {
        _timestamp_key(value, label=f"legacy {year} split timestamp"): set()
        for value in timestamps
    }
    if not required:
        return required
    for filing_uuid, filing in _iter_unique_legacy(Path(root), year):
        key = _timestamp_key(
            filing.get("dt_posted"), label=f"legacy {year}/{filing_uuid}"
        )
        if key in required:
            required[key].add(filing_uuid)
    empty = sorted(key for key, filing_uuids in required.items() if not filing_uuids)
    if empty:
        raise CompositeRepairError(
            f"legacy {year}: localized split timestamps have no UUIDs: {empty[:3]}"
        )
    return required


def _json_diff_paths(left: Any, right: Any, prefix: str = "") -> list[str]:
    """Enumerate changed JSON-pointer paths without copying source values."""

    if type(left) is not type(right):
        return [prefix or "/"]
    if isinstance(left, dict):
        paths: list[str] = []
        keys = set(left) | set(right)
        for key in sorted(keys):
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            child = f"{prefix}/{escaped}"
            if key not in left or key not in right:
                paths.append(child)
            else:
                paths.extend(_json_diff_paths(left[key], right[key], child))
        return paths
    if isinstance(left, list):
        paths = []
        for index in range(max(len(left), len(right))):
            child = f"{prefix}/{index}"
            if index >= len(left) or index >= len(right):
                paths.append(child)
            else:
                paths.extend(_json_diff_paths(left[index], right[index], child))
        return paths
    return [] if left == right else [prefix or "/"]


def _load_window_payloads(root: Path, window_id: str, twin: str = "B") -> dict[str, Any]:
    payloads: dict[str, Any] = {}
    directory = root / "windows" / window_id / twin
    manifest = json.loads(
        (directory / "window_manifest.json").read_text(encoding="utf-8")
    )
    for page_path in _page_files(directory):
        page = json.loads(page_path.read_text(encoding="utf-8"))
        for filing in page["results"]:
            filing_uuid = str(filing["filing_uuid"])
            if filing_uuid in payloads:
                if _canonical_hash(payloads[filing_uuid]) != _canonical_hash(filing):
                    raise CompositeRepairError(
                        f"{window_id}/{twin}: conflicting base UUID {filing_uuid}"
                    )
                continue
            payloads[filing_uuid] = filing
    for cohort in manifest.get("tie_cohorts", []):
        timestamp_key = cohort["timestamp"]
        payloads = {
            filing_uuid: filing for filing_uuid, filing in payloads.items()
            if _timestamp_key(
                filing.get("dt_posted"),
                label=f"{window_id}/{twin}/{filing_uuid}",
            ) != timestamp_key
        }
        page = json.loads((directory / cohort["path"]).read_text(encoding="utf-8"))
        for filing in page["results"]:
            filing_uuid = str(filing["filing_uuid"])
            if filing_uuid in payloads:
                raise CompositeRepairError(
                    f"{window_id}/{twin}: repaired UUID collision {filing_uuid}"
                )
            payloads[filing_uuid] = filing
    if len(payloads) != manifest["advertised_count"]:
        raise CompositeRepairError(
            f"{window_id}/{twin}: reconstructed payload count mismatch"
        )
    return payloads


def _load_completed_window_result(
    root: Path,
    spec: Mapping[str, Any],
    twin: str,
    required_legacy_by_timestamp: Mapping[str, set[str]] | None = None,
) -> dict[str, Any]:
    """Revalidate and load one already-complete immutable twin."""

    directory = root / "windows" / str(spec["window_id"]) / twin
    manifest_path = directory / "window_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompositeRepairError(
            f"cannot load completed window manifest {manifest_path}: {exc}"
        ) from exc
    expected_fields = {
        "schema_id": "staffer-network-lda-window-manifest-v1",
        "window_id": spec["window_id"],
        "twin": twin,
        "filing_year": spec["filing_year"],
        "start_date": spec["start_date"],
        "end_date_exclusive": spec["end_date_exclusive"],
        "ordering": ORDERING,
        "base_page_size": PAGE_SIZE,
        "max_in_flight_requests": spec.get("max_in_flight_requests", MAX_IN_FLIGHT_REQUESTS),
        "transport_mode": "BOUNDED_BATCH_ASCENDING_PROCESSING",
        "complete": True,
    }
    for key, expected in expected_fields.items():
        if manifest.get(key) != expected:
            raise CompositeRepairError(
                f"completed window manifest mismatch {spec['window_id']}/{twin}/{key}"
            )
    entries = [*manifest.get("files", []), *manifest.get("tie_cohorts", [])]
    expected_paths: set[str] = set()
    for entry in entries:
        relative = Path(str(entry.get("path", "")))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise CompositeRepairError("completed window manifest has unsafe path")
        normalized = relative.as_posix()
        if normalized in expected_paths:
            raise CompositeRepairError("completed window manifest duplicates a file")
        expected_paths.add(normalized)
        target = directory / relative
        if (
            not target.is_file()
            or target.stat().st_size != entry.get("bytes")
            or sha256_file(target) != entry.get("sha256")
        ):
            raise CompositeRepairError(
                f"completed window file binding failed: {spec['window_id']}/{twin}/{normalized}"
            )
    actual_paths = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual_paths != expected_paths:
        raise CompositeRepairError(
            f"completed window inventory is not closed: {spec['window_id']}/{twin}"
        )
    page_paths = sorted(path for path in expected_paths if path.startswith("page_"))
    expected_page_paths = [
        f"page_{number:05d}.json"
        for number in range(1, int(manifest.get("page_count", -1)) + 1)
    ]
    if page_paths != expected_page_paths:
        raise CompositeRepairError(
            f"completed window base pages are not contiguous: {spec['window_id']}/{twin}"
        )
    advertised_count = manifest.get("advertised_count")
    total_pages = int(manifest.get("page_count", -1))
    order_key: datetime | None = None
    previous_page_last: str | None = None
    base_payloads: dict[str, Any] = {}
    base_timestamps: dict[str, str] = {}
    boundary_timestamps: set[str] = set()
    repeated_occurrences = 0
    base_filing_objects = 0
    page_layout: list[list[str]] = []
    for page_number, relative in enumerate(expected_page_paths, 1):
        data = (directory / relative).read_bytes()
        label = f"completed {spec['window_id']}/{twin}/page {page_number}"
        page = _decode_page(data, label=label)
        validate_page_shape(
            page,
            page_number=page_number,
            total_pages=total_pages,
            advertised_count=advertised_count,
            page_size=PAGE_SIZE,
        )
        order_key, page_hashes = validate_bounded_page(
            page,
            filing_year=int(spec["filing_year"]),
            start_date=str(spec["start_date"]),
            end_date_exclusive=str(spec["end_date_exclusive"]),
            prior_order_key=order_key,
        )
        results = page["results"]
        base_filing_objects += len(results)
        page_layout.append([str(row["filing_uuid"]) for row in results])
        current_first = (
            _timestamp_key(results[0].get("dt_posted"), label=f"{label} first")
            if results else None
        )
        current_last = (
            _timestamp_key(results[-1].get("dt_posted"), label=f"{label} last")
            if results else None
        )
        boundary_timestamp = None
        if previous_page_last is not None and current_first == previous_page_last:
            boundary_timestamp = current_first
            assert boundary_timestamp is not None
            boundary_timestamps.add(boundary_timestamp)
        overlap = set(base_payloads) & set(page_hashes)
        for filing in results:
            filing_uuid = str(filing["filing_uuid"])
            timestamp_key = _timestamp_key(
                filing.get("dt_posted"), label=f"{label}/{filing_uuid}"
            )
            if filing_uuid in overlap:
                if (
                    boundary_timestamp != timestamp_key
                    or base_timestamps[filing_uuid] != timestamp_key
                    or _canonical_hash(base_payloads[filing_uuid])
                    != page_hashes[filing_uuid]
                ):
                    raise CompositeRepairError(
                        f"completed window has invalid repeated UUID: {filing_uuid}"
                    )
                repeated_occurrences += 1
                continue
            base_payloads[filing_uuid] = filing
            base_timestamps[filing_uuid] = timestamp_key
        if results:
            previous_page_last = current_last
    replayed = {
        "base_filing_objects": base_filing_objects,
        "base_unique_filing_uuids": base_filing_objects - repeated_occurrences,
        "base_repeated_uuid_occurrences": repeated_occurrences,
        "page_boundary_timestamps": sorted(boundary_timestamps),
        "page_layout_sha256": _canonical_hash(page_layout),
    }
    for key, observed in replayed.items():
        if manifest.get(key) != observed:
            raise CompositeRepairError(
                f"completed window replay mismatch {spec['window_id']}/{twin}/{key}"
            )
    required = {
        key: set(values)
        for key, values in (required_legacy_by_timestamp or {}).items()
    }
    observed_cohort_timestamps = [
        str(cohort.get("timestamp"))
        for cohort in manifest.get("tie_cohorts", [])
    ]
    expected_cohort_timestamps = sorted(boundary_timestamps | set(required))
    if observed_cohort_timestamps != expected_cohort_timestamps:
        raise CompositeRepairError(
            f"completed tie-cohort coverage failed: {spec['window_id']}/{twin}"
        )
    for cohort in manifest.get("tie_cohorts", []):
        cohort_path = directory / str(cohort["path"])
        page = _decode_page(
            cohort_path.read_bytes(),
            label=f"completed {spec['window_id']}/{twin}/{cohort['path']}",
        )
        if (
            page.get("count") != cohort.get("count")
            or len(page.get("results", [])) != cohort.get("count")
            or page.get("next") is not None
            or any(
                _timestamp_key(
                    filing.get("dt_posted"), label="completed tie cohort"
                ) != cohort.get("timestamp")
                for filing in page.get("results", [])
            )
        ):
            raise CompositeRepairError(
                f"completed tie cohort replay failed: {spec['window_id']}/{twin}"
            )
        observed_uuids = {
            str(filing.get("filing_uuid")) for filing in page.get("results", [])
        }
        missing_required = required.get(str(cohort.get("timestamp")), set()) - observed_uuids
        if missing_required:
            raise CompositeRepairError(
                f"completed tie cohort misses required legacy UUIDs: "
                f"{spec['window_id']}/{twin}"
            )
        cohort_timestamp = str(cohort.get("timestamp"))
        replaced = [
            filing_uuid
            for filing_uuid, timestamp in base_timestamps.items()
            if timestamp == cohort_timestamp
        ]
        for filing_uuid in replaced:
            base_payloads.pop(filing_uuid, None)
            base_timestamps.pop(filing_uuid, None)
        for filing in page.get("results", []):
            filing_uuid = str(filing["filing_uuid"])
            if filing_uuid in base_payloads:
                raise CompositeRepairError(
                    f"completed repaired UUID collision: {filing_uuid}"
                )
            base_payloads[filing_uuid] = filing
            base_timestamps[filing_uuid] = cohort_timestamp
    payloads = base_payloads
    uuid_hashes = {
        filing_uuid: _canonical_hash(payload)
        for filing_uuid, payload in payloads.items()
    }
    if (
        len(uuid_hashes) != manifest.get("advertised_count")
        or _canonical_hash(sorted(uuid_hashes))
        != manifest.get("uuid_inventory_sha256")
        or _canonical_hash(sorted(uuid_hashes.items()))
        != manifest.get("payload_inventory_sha256")
    ):
        raise CompositeRepairError(
            f"completed window payload inventory failed: {spec['window_id']}/{twin}"
        )
    return {
        "manifest": manifest,
        "uuid_hashes": uuid_hashes,
        "directory": str(directory),
    }


def _legacy_payload_subset(
    root: Path, year: int, filing_uuids: set[str]
) -> dict[str, Any]:
    payloads: dict[str, Any] = {}
    if not filing_uuids:
        return payloads
    for filing_uuid, filing in _iter_unique_legacy(root, year):
        if filing_uuid in filing_uuids:
            payloads[filing_uuid] = filing
            if len(payloads) == len(filing_uuids):
                break
    missing = sorted(filing_uuids - set(payloads))
    if missing:
        raise CompositeRepairError(
            f"legacy {year}: requested UUIDs are absent: {missing[:3]}"
        )
    return payloads


def _append_ndjson(path: Path, rows: Iterable[Mapping[str, Any]]) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    count = 0
    digest = hashlib.sha256()
    try:
        with temporary.open("wb") as stream:
            for row in rows:
                body = canonical_json_bytes(row)
                stream.write(body)
                digest.update(body)
                count += 1
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return count, digest.hexdigest()


def scan_for_secret(root: str | Path, secret: str) -> dict[str, Any]:
    if not secret:
        raise CompositeRepairError("cannot scan for an empty secret")
    needle = secret.encode("utf-8")
    matches: list[str] = []
    files_scanned = 0
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        files_scanned += 1
        with path.open("rb") as stream:
            tail = b""
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                body = tail + chunk
                if needle in body:
                    matches.append(path.relative_to(root).as_posix())
                    break
                tail = body[-max(0, len(needle) - 1):]
    return {"files_scanned": files_scanned, "matches": matches, "pass": not matches}


def load_api_key(path: str | Path) -> str:
    """Load a plain token or the local keys.json LDA entry without logging it."""

    key_path = Path(path)
    text = key_path.read_text(encoding="utf-8-sig").strip()
    if not text:
        raise CompositeRepairError("API key file is empty")
    if text.startswith("{"):
        payload = json.loads(text)
        try:
            candidates = payload["lda"]["keys"]
        except (KeyError, TypeError) as exc:
            raise CompositeRepairError("keys.json lacks lda.keys") from exc
        keys = [item.strip() for item in candidates if isinstance(item, str) and item.strip()]
        if len(keys) != 1:
            raise CompositeRepairError("keys.json must contain exactly one nonblank LDA key")
        return keys[0]
    if "\n" in text or "\r" in text:
        raise CompositeRepairError("plain API key file must contain one line")
    return text


def _headers(api_key: str) -> dict[str, str]:
    environ = {"LDA_API_KEY": api_key}
    headers = collector._headers(environ)
    if headers.get("Authorization") != "Token " + api_key:
        raise CompositeRepairError("registered Authorization header was not constructed")
    return headers


def _window_specs(
    legacy: Mapping[int, Mapping[str, Any]], cutoff_exclusive: str
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for year, days in sorted(REPAIR_DATES.items()):
        for day in days:
            end = (_date_value(day) + timedelta(days=1)).isoformat()
            specs.append({
                "window_id": f"repair-{year}-{day}",
                "kind": "PAGINATION_BOUNDARY_REPAIR",
                "filing_year": year,
                "start_date": day,
                "end_date_exclusive": end,
            })
    cutoff = _date_value(cutoff_exclusive)
    for year in range(2021, 2026):
        maximum = legacy[year]["maximum_dt_posted"]
        if not isinstance(maximum, str):
            raise CompositeRepairError(f"legacy {year} lacks maximum dt_posted")
        # Include the entire final observed day so inclusive/exclusive boundaries
        # and same-timestamp arrivals cannot create a gap.
        start = maximum[:10]
        if _date_value(start) >= cutoff:
            start = (cutoff - timedelta(days=1)).isoformat()
        specs.append({
            "window_id": f"gap-{year}-{start}-to-{cutoff_exclusive}",
            "kind": "POST_SWEEP_GAP",
            "filing_year": year,
            "start_date": start,
            "end_date_exclusive": cutoff_exclusive,
        })
    specs.append({
        "window_id": f"full-2026-to-{cutoff_exclusive}",
        "kind": "FULL_MOVING_YEAR",
        "filing_year": 2026,
        "start_date": "2026-01-01",
        "end_date_exclusive": cutoff_exclusive,
    })
    return specs


def prove_split_cohort_coverage(
    specs: Iterable[Mapping[str, Any]],
    legacy_split_cohorts: Mapping[int, Mapping[str, set[str]]],
) -> tuple[dict[str, dict[str, set[str]]], dict[str, Any]]:
    """Bind every offline-localized split timestamp to one or more windows."""

    slices: dict[str, dict[str, set[str]]] = {}
    covered: dict[int, set[str]] = {year: set() for year in LEGACY_YEARS}
    window_rows: list[dict[str, Any]] = []
    for spec in specs:
        year = int(spec["filing_year"])
        start, end = _window_bounds(
            str(spec["start_date"]), str(spec["end_date_exclusive"])
        )
        window_slice = {
            key: set(filing_uuids)
            for key, filing_uuids in legacy_split_cohorts[year].items()
            if start <= _boundary_datetime(key, label="legacy split key") < end
        }
        slices[str(spec["window_id"])] = window_slice
        covered[year].update(window_slice)
        window_rows.append({
            "window_id": spec["window_id"],
            "filing_year": year,
            "localized_split_timestamps": sorted(window_slice),
            "required_legacy_uuids": sum(
                len(filing_uuids) for filing_uuids in window_slice.values()
            ),
        })
    missing = {
        str(year): sorted(set(legacy_split_cohorts[year]) - covered[year])
        for year in LEGACY_YEARS
        if set(legacy_split_cohorts[year]) - covered[year]
    }
    receipt = {
        "localized_split_timestamps": sum(
            len(values) for values in legacy_split_cohorts.values()
        ),
        "covered_split_timestamps": sum(len(values) for values in covered.values()),
        "windows": window_rows,
        "uncovered_by_year": missing,
        "pass": not missing,
    }
    if missing:
        raise CompositeRepairError(
            f"offline-localized split timestamps are not covered: {missing}"
        )
    return slices, receipt


def _status_base(
    generation_root: Path,
    cutoff_exclusive: str,
    legacy_root: Path,
    authority_fields: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "schema_id": "staffer-network-lda-composite-generation-status-v1",
        "generation_id": generation_root.name,
        "status": "RUNNING",
        "started_at": _now(),
        "completed_at": None,
        "authentication_mode": "registered",
        "rate_limit_rpm": 120,
        "base_page_size": PAGE_SIZE,
        "max_in_flight_requests": MAX_IN_FLIGHT_REQUESTS,
        "transport_mode": "BOUNDED_BATCH_ASCENDING_PROCESSING",
        "ordering": ORDERING,
        "cutoff_exclusive": cutoff_exclusive,
        "legacy_root": str(legacy_root),
        **authority_fields,
        "current_window": None,
        "windows_completed": [],
        "error": None,
    }


def _load_failed_resume_state(
    status_path: Path,
    *,
    expected_sha256: str,
    generation_id: str,
    cutoff_exclusive: str,
    legacy_root: Path,
) -> dict[str, Any]:
    """Validate the hash-pinned terminal failure before any resume mutation."""

    if not status_path.is_file():
        raise CompositeRepairError("resume status does not exist")
    if sha256_file(status_path) != expected_sha256.lower():
        raise CompositeRepairError("failed status hash does not match resume authority")
    try:
        state = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompositeRepairError(f"cannot parse resume status: {exc}") from exc
    expected_state = {
        "schema_id": "staffer-network-lda-composite-generation-status-v1",
        "generation_id": generation_id,
        "status": "FAILED",
        "authentication_mode": "registered",
        "rate_limit_rpm": 120,
        "base_page_size": PAGE_SIZE,
        "max_in_flight_requests": MAX_IN_FLIGHT_REQUESTS,
        "transport_mode": "BOUNDED_BATCH_ASCENDING_PROCESSING",
        "ordering": ORDERING,
        "cutoff_exclusive": cutoff_exclusive,
        "legacy_root": str(legacy_root),
    }
    for key, expected in expected_state.items():
        if state.get(key) != expected:
            raise CompositeRepairError(f"failed status cannot resume: {key} mismatch")
    if not isinstance(state.get("error"), str) or not state["error"]:
        raise CompositeRepairError("failed status has no recorded error")
    return state


def refuse_synced_generation_root(
    generation_root: str | Path, synced_roots: Iterable[str | Path] = ()
) -> None:
    """Refuse immutable raw generations inside known cloud-synced roots."""

    generation = Path(generation_root).resolve()
    candidates = [Path(value).resolve() for value in synced_roots]
    for variable in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        value = os.environ.get(variable)
        if value:
            candidates.append(Path(value).resolve())
    if any(
        generation == root or generation.is_relative_to(root)
        for root in candidates
    ) or any(part.casefold().startswith("onedrive") for part in generation.parts):
        raise CompositeRepairError(
            f"generation root must be outside synced storage: {generation}"
        )


def run(args: argparse.Namespace) -> int:
    generation_root = Path(args.generation_root).resolve()
    legacy_root = Path(args.legacy_sweeps_root).resolve()
    cutoff_exclusive = _date_value(args.cutoff_exclusive).isoformat()
    refuse_synced_generation_root(
        generation_root, getattr(args, "synced_root", ()) or ()
    )
    status_path = generation_root / "generation_status.json"
    resume_status_sha256 = getattr(args, "resume_failed_status_sha256", None)
    resuming = bool(resume_status_sha256)
    authority_fields = {
        "authority_basis": args.authority_basis,
        "supersedes_terminal_criterion": args.supersedes_terminal_criterion,
        "payload_supersession_policy": args.payload_supersession_policy,
        "anonymous_partial_generation": args.anonymous_partial_generation,
    }
    if resuming:
        if not generation_root.is_dir() or not status_path.is_file():
            raise CompositeRepairError("resume root/status does not exist")
        state = _load_failed_resume_state(
            status_path,
            expected_sha256=str(resume_status_sha256),
            generation_id=generation_root.name,
            cutoff_exclusive=cutoff_exclusive,
            legacy_root=legacy_root,
        )
        recorded_failures = [
            value
            for value in (
                state.get("error"),
                *(
                    entry.get("recorded_error")
                    for entry in state.get("resume_history", [])
                    if isinstance(entry, Mapping)
                ),
            )
            if isinstance(value, str) and value
        ]
        if (generation_root / "composite_reconciliation_receipt.json").exists():
            raise CompositeRepairError("failed generation already has a terminal receipt")
        resume_event = {
            "failed_status_sha256": str(resume_status_sha256).lower(),
            "failed_at": state.get("completed_at"),
            "recorded_error": state["error"],
            "resumed_at": _now(),
            "completed_windows_before_resume": list(state.get("windows_completed", [])),
            "resume_authority": authority_fields,
            "resume_module_sha256": sha256_file(Path(__file__)),
            "recovery_semantics": (
                "ADDITIVE_REPLAY_SAVED_BYTES_THROUGH_ORIGINAL_VALIDATORS_"
                "THEN_FRESH_INDEPENDENT_TWIN"
            ),
        }
        resume_authority = {
                "schema_id": "staffer-network-lda-resume-authority-receipt-v1",
                "generated_at": _now(),
                "failed_status_sha256": str(resume_status_sha256).lower(),
                "authority_act": (
                    "CURRENT_USER_DIRECTIVE_TO_CONTINUE_TO_COMPLETION_WITH_"
                    "REASONABLE_CONNECTION_ASSURANCE"
                ),
                **authority_fields,
                "module_sha256": sha256_file(Path(__file__)),
                "completed_window_reuse_policy": (
                    "REPLAY_AND_REHASH_BOTH_SAME_RUN_TWINS_WITH_FINAL_LIVE_"
                    "PER_YEAR_COUNT_RECHECK"
                ),
                "residual_same_uuid_historical_payload_update_risk": (
                    "ACCEPTED_WITHIN_MULTI_HOUR_SNAPSHOT; NOT_DETECTABLE_BY_"
                    "FINAL_COUNT_IF_UUID_AND_COUNT_ARE_UNCHANGED"
                ),
                "canonical_identity_merges": 0,
                "downstream_gates_remain_closed": True,
        }
        resume_authority_schema = json.loads((
            Path(__file__).with_name("schemas") /
            "lda_resume_authority_receipt.schema.json"
        ).read_text(encoding="utf-8"))
        validate_instance(resume_authority, resume_authority_schema)
        authority_receipt_name = (
            "resume_authority_receipt_"
            f"{str(resume_status_sha256).lower()}.json"
        )
        authority_receipt_path = generation_root / authority_receipt_name
        if authority_receipt_path.exists():
            raise CompositeRepairError(
                "resume authority receipt already exists for failed status"
            )
        _atomic_json(authority_receipt_path, resume_authority)
        resume_event["authority_receipt"] = {
            "path": authority_receipt_name,
            "bytes": authority_receipt_path.stat().st_size,
            "sha256": sha256_file(authority_receipt_path),
        }
        state.setdefault("resume_history", []).append(resume_event)
        state.update({
            "status": "RUNNING",
            "completed_at": None,
            "current_window": None,
            "error": None,
        })
        _atomic_json(status_path, state)
    else:
        if generation_root.exists() and any(generation_root.iterdir()):
            raise CompositeRepairError(f"generation root is nonempty: {generation_root}")
        generation_root.mkdir(parents=True, exist_ok=True)
        state = _status_base(
            generation_root, cutoff_exclusive, legacy_root, authority_fields
        )
        _atomic_json(status_path, state)
    try:
        if args.payload_supersession_policy != PAYLOAD_SUPERSESSION_POLICY:
            raise CompositeRepairError("unratified payload supersession policy")
        api_key = load_api_key(args.api_key_file)
        headers = _headers(api_key)
        pacer = SharedRequestPacer(120)
        legacy_before = sweep_tree_inventory(
            legacy_root, expected_years=LEGACY_YEARS
        )
        if legacy_before["inventory_sha256"] != APPROVED_LEGACY_INVENTORY_SHA256:
            raise CompositeRepairError("legacy inventory does not match approved seal")
        ordering_audit = audit_legacy_ordering(legacy_root)
        if not ordering_audit["pass"]:
            raise CompositeRepairError("offline legacy ordering/localization proof failed")
        ordering_path = generation_root / "legacy_ordering_audit.json"
        filter_path = generation_root / "filter_semantics_receipt.json"
        if resuming:
            recorded_ordering = json.loads(ordering_path.read_text(encoding="utf-8"))
            if _canonical_hash(recorded_ordering) != _canonical_hash(ordering_audit):
                raise CompositeRepairError("resume ordering audit differs from recorded proof")
            recorded_filter = json.loads(filter_path.read_text(encoding="utf-8"))
            filter_probe = validate_filter_semantics(headers=headers, pacer=pacer)
            if (
                recorded_filter.get("pass") is not True
                or _canonical_hash(recorded_filter) != _canonical_hash(filter_probe)
            ):
                raise CompositeRepairError("recorded filter-semantics proof is not PASS")
        else:
            _atomic_json(ordering_path, ordering_audit)
            filter_probe = validate_filter_semantics(headers=headers, pacer=pacer)
            _atomic_json(filter_path, filter_probe)
        legacy = {
            year: legacy_year_inventory(legacy_root, year) for year in LEGACY_YEARS
        }
        audit_by_year = {row["year"]: row for row in ordering_audit["years"]}
        legacy_split_cohorts = {
            year: legacy_tie_cohorts(
                legacy_root, year, audit_by_year[year]["duplicate_timestamps"]
            )
            for year in LEGACY_YEARS
        }
        specs = _window_specs(legacy, cutoff_exclusive)
        split_slices, split_coverage = prove_split_cohort_coverage(
            specs, legacy_split_cohorts
        )
        split_path = generation_root / "legacy_split_cohort_coverage.json"
        if resuming:
            recorded_split = json.loads(split_path.read_text(encoding="utf-8"))
            if _canonical_hash(recorded_split) != _canonical_hash(split_coverage):
                raise CompositeRepairError("resume split coverage differs from recorded proof")
        else:
            _atomic_json(split_path, split_coverage)
        twin_receipts: list[dict[str, Any]] = []
        fresh_by_year: dict[int, dict[str, Any]] = {
            year: {} for year in LEGACY_YEARS
        }
    except Exception as exc:
        state.update({
            "status": "FAILED",
            "completed_at": _now(),
            "current_window": None,
            "error": str(exc),
        })
        _atomic_json(status_path, state)
        return 2
    try:
        completed_before = list(state.get("windows_completed", []))
        expected_prefix = [
            spec["window_id"] for spec in specs[:len(completed_before)]
        ]
        if completed_before != expected_prefix:
            raise CompositeRepairError(
                "recorded completed windows are not an exact specification prefix"
            )
        for spec_index, spec in enumerate(specs):
            state["current_window"] = spec["window_id"]
            _atomic_json(status_path, state)
            required_legacy_by_timestamp = split_slices[spec["window_id"]]
            results: dict[str, dict[str, Any]] = {}
            for twin in ("A", "B"):
                twin_directory = (
                    generation_root / "windows" / spec["window_id"] / twin
                )
                manifest_path = twin_directory / "window_manifest.json"
                if manifest_path.is_file():
                    if not resuming:
                        raise CompositeRepairError(
                            f"unexpected completed window in fresh run: {spec['window_id']}/{twin}"
                        )
                    results[twin] = _load_completed_window_result(
                        generation_root, spec, twin,
                        required_legacy_by_timestamp,
                    )
                    continue
                has_partial = (
                    twin_directory.is_dir() and any(twin_directory.iterdir())
                )
                if has_partial and not resuming:
                    raise CompositeRepairError(
                        f"unexpected partial window in fresh run: {spec['window_id']}/{twin}"
                    )
                if has_partial and (
                    spec_index != len(completed_before)
                    or not any(
                        f"{spec['window_id']} twin {twin} " in failure
                        for failure in recorded_failures
                    )
                ):
                    raise CompositeRepairError(
                        f"partial window is not the exact recorded failure: "
                        f"{spec['window_id']}/{twin}"
                    )
                results[twin] = fetch_window(
                    generation_root,
                    window_id=spec["window_id"], twin=twin,
                    filing_year=spec["filing_year"],
                    start_date=spec["start_date"],
                    end_date_exclusive=spec["end_date_exclusive"],
                    headers=headers, pacer=pacer,
                    required_legacy_by_timestamp=required_legacy_by_timestamp,
                    resume_existing_base_pages=has_partial,
                )
            left, right = results["A"], results["B"]
            comparison = compare_twins(left, right)
            if not comparison["pass"]:
                raise CompositeRepairError(
                    f"twin mismatch for {spec['window_id']}"
                )
            twin_receipts.append({**spec, "comparison": comparison})
            window_payloads = _load_window_payloads(
                generation_root, spec["window_id"]
            )
            year_fresh = fresh_by_year[spec["filing_year"]]
            for filing_uuid, payload in window_payloads.items():
                prior = year_fresh.get(filing_uuid)
                if prior is not None and _canonical_hash(prior) != _canonical_hash(payload):
                    raise CompositeRepairError(
                        f"overlapping windows disagree for UUID {filing_uuid}"
                    )
                year_fresh[filing_uuid] = payload
            if spec["window_id"] not in state["windows_completed"]:
                state["windows_completed"].append(spec["window_id"])
            _atomic_json(status_path, state)

        dispositions: list[dict[str, Any]] = []
        per_year: list[dict[str, Any]] = []
        composite_global: dict[str, int] = {}
        for year in LEGACY_YEARS:
            legacy_hashes = legacy[year]["hashes"]
            legacy_timestamps = legacy[year]["timestamps"]
            fresh_payloads = fresh_by_year[year]
            composite_hashes = dict(legacy_hashes)
            overlapping_payloads = _legacy_payload_subset(
                legacy_root, year, set(fresh_payloads) & set(legacy_hashes)
            )
            for filing_uuid, fresh in sorted(fresh_payloads.items()):
                fresh_hash = _canonical_hash(fresh)
                if filing_uuid in legacy_hashes:
                    legacy_payload = overlapping_payloads[filing_uuid]
                    paths = _json_diff_paths(legacy_payload, fresh)
                    disposition = (
                        "UNCHANGED" if not paths else "SOURCE_UPDATE_OBSERVED_SUPERSEDE"
                    )
                    dispositions.append({
                        "filing_year": year,
                        "filing_uuid": filing_uuid,
                        "legacy_sha256": legacy_hashes[filing_uuid],
                        "fresh_sha256": fresh_hash,
                        "legacy_dt_posted": legacy_timestamps[filing_uuid],
                        "fresh_dt_posted": fresh.get("dt_posted"),
                        "changed_json_pointer_paths": paths,
                        "disposition": disposition,
                    })
                else:
                    dispositions.append({
                        "filing_year": year,
                        "filing_uuid": filing_uuid,
                        "legacy_sha256": None,
                        "fresh_sha256": fresh_hash,
                        "legacy_dt_posted": None,
                        "fresh_dt_posted": fresh.get("dt_posted"),
                        "changed_json_pointer_paths": [],
                        "disposition": "NEW_SOURCE_RECORD_ADD",
                    })
                composite_hashes[filing_uuid] = fresh_hash
            source_count, _ = query_count(
                year, None, cutoff_exclusive,
                headers=headers, pacer=pacer,
            )
            shortfall = source_count - len(composite_hashes)
            if shortfall != 0:
                raise CompositeRepairError(
                    f"{year}: composite count {len(composite_hashes)} does not "
                    f"equal source count {source_count} through cutoff; delta={shortfall}"
                )
            for filing_uuid in composite_hashes:
                prior = composite_global.get(filing_uuid)
                if prior is not None and prior != year:
                    raise CompositeRepairError(
                        f"cross-year UUID collision {filing_uuid}: {prior}, {year}"
                    )
                composite_global[filing_uuid] = year
            per_year.append({
                "year": year,
                "legacy_unique_filing_uuids": len(legacy_hashes),
                "fresh_window_unique_filing_uuids": len(fresh_payloads),
                "composite_unique_filing_uuids": len(composite_hashes),
                "source_count_through_cutoff": source_count,
                "shortfall": shortfall,
                "pass": True,
            })

        disposition_path = generation_root / "payload_dispositions.ndjson"
        disposition_count, disposition_sha = _append_ndjson(
            disposition_path, dispositions
        )
        legacy_after = sweep_tree_inventory(legacy_root, expected_years=LEGACY_YEARS)
        if legacy_after != legacy_before:
            raise CompositeRepairError("sealed legacy bytes changed during repair")
        token_scan = scan_for_secret(generation_root, api_key)
        if not token_scan["pass"]:
            raise CompositeRepairError("API token was found in emitted artifacts")
        resume_events = [
            {
                "failed_status_sha256": event["failed_status_sha256"],
                "recorded_error": event["recorded_error"],
                "resumed_at": event["resumed_at"],
                "resume_module_sha256": event["resume_module_sha256"],
                "recovery_semantics": event["recovery_semantics"],
                "authority_receipt": event["authority_receipt"],
            }
            for event in state.get("resume_history", [])
        ]
        resume_authority_schema = json.loads((
            Path(__file__).with_name("schemas") /
            "lda_resume_authority_receipt.schema.json"
        ).read_text(encoding="utf-8"))
        for event in resume_events:
            binding = event["authority_receipt"]
            authority_path = generation_root / binding["path"]
            if (
                not authority_path.is_file()
                or authority_path.stat().st_size != binding["bytes"]
                or sha256_file(authority_path) != binding["sha256"]
            ):
                raise CompositeRepairError(
                    "resume authority receipt binding changed before seal"
                )
            authority_value = json.loads(
                authority_path.read_text(encoding="utf-8")
            )
            validate_instance(authority_value, resume_authority_schema)
            if (
                authority_value["failed_status_sha256"]
                != event["failed_status_sha256"]
            ):
                raise CompositeRepairError(
                    "resume authority receipt failed-status binding changed"
                )
        receipt = {
            "schema_id": "staffer-network-lda-composite-reconciliation-receipt-v1",
            "generated_at": _now(),
            "status": "PASS",
            "cutoff_exclusive": cutoff_exclusive,
            "authority_basis": args.authority_basis,
            "supersedes_terminal_criterion": args.supersedes_terminal_criterion,
            "payload_supersession_policy": args.payload_supersession_policy,
            "legacy_inventory_before": legacy_before,
            "legacy_inventory_after": legacy_after,
            "legacy_ordering_audit": ordering_audit,
            "legacy_split_cohort_coverage": split_coverage,
            "filter_semantics": filter_probe,
            "ordering": ORDERING,
            "authentication_mode": "registered",
            "rate_limit_rpm": 120,
            "base_page_size": PAGE_SIZE,
            "max_in_flight_requests": MAX_IN_FLIGHT_REQUESTS,
            "transport_mode": "BOUNDED_BATCH_ASCENDING_PROCESSING",
            "windows": twin_receipts,
            "payload_dispositions": {
                "path": disposition_path.name,
                "records": disposition_count,
                "sha256": disposition_sha,
            },
            "per_year": per_year,
            "global_unique_filing_uuids": len(composite_global),
            "cross_year_duplicate_uuid_occurrences": 0,
            "token_scan": token_scan,
            "anonymous_partial_generation": args.anonymous_partial_generation,
            "resume": {
                "resumed": bool(resume_events),
                "events": resume_events,
            },
            "downstream_gates": {
                "identity_resolution": "CLOSED",
                "graph_build": "CLOSED",
                "analysis": "CLOSED",
                "publication": "CLOSED",
                "staff_net_required_state": "off",
            },
            "failures": [],
        }
        receipt_schema = json.loads((
            Path(__file__).with_name("schemas") /
            "lda_composite_reconciliation_receipt.schema.json"
        ).read_text(encoding="utf-8"))
        validate_instance(receipt, receipt_schema)
        receipt_path = generation_root / "composite_reconciliation_receipt.json"
        _atomic_json(receipt_path, receipt)
        token_scan = scan_for_secret(generation_root, api_key)
        if not token_scan["pass"]:
            try:
                receipt_path.unlink()
            except FileNotFoundError:
                pass
            raise CompositeRepairError("API token appeared after receipt sealing")
        state.update({
            "status": "PASS",
            "completed_at": _now(),
            "current_window": None,
            "receipt_sha256": sha256_file(
                receipt_path
            ),
        })
        _atomic_json(status_path, state)
        return 0
    except Exception as exc:
        state.update({
            "status": "FAILED",
            "completed_at": _now(),
            "current_window": None,
            "error": str(exc),
        })
        _atomic_json(status_path, state)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation-root", required=True)
    parser.add_argument("--legacy-sweeps-root", required=True)
    parser.add_argument("--api-key-file", required=True)
    parser.add_argument("--cutoff-exclusive", required=True)
    parser.add_argument("--authority-basis", required=True)
    parser.add_argument("--supersedes-terminal-criterion", required=True)
    parser.add_argument(
        "--payload-supersession-policy",
        required=True,
        choices=[PAYLOAD_SUPERSESSION_POLICY],
    )
    parser.add_argument("--anonymous-partial-generation", required=True)
    parser.add_argument(
        "--resume-failed-status-sha256",
        help=(
            "Explicitly resume one hash-pinned FAILED generation in place; "
            "completed and partial window bytes are revalidated before reuse."
        ),
    )
    parser.add_argument("--synced-root", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        return run(_parser().parse_args(argv))
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
