# -*- coding: utf-8 -*-
"""Flag-gated BICAM snapshot acquisition and deterministic arrival auditing."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Iterable, Iterator, Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit
import urllib.request

from fec.bill_rules import bill_type_key
from fec.paths import ROOT


csv.field_size_limit(1 << 30)


COMPONENTS = (
    "bills", "bills_texts", "bills_sponsored", "members", "committees",
    "hearings", "committee_reports",
)
AMENDMENTS_COMPONENTS = ("amendments", "amendments_texts")
V2_COMPONENTS = COMPONENTS + AMENDMENTS_COMPONENTS
SOURCE_SCHEMA = "bicam-fetch-map-v1"
SOURCE_SCHEMA_V2 = "bicam-fetch-map-v2"
SOURCE_COMPONENTS = {
    SOURCE_SCHEMA: COMPONENTS,
    SOURCE_SCHEMA_V2: V2_COMPONENTS,
}
ARRIVAL_SCHEMA = "bicam-arrival-manifest-v1"
AUDIT_SCHEMA = "bicam-arrival-audit-v1"
ARRIVAL_FILENAME = "bicam_arrival_manifest.json"
AUDIT_FILENAME = "bicam_arrival_audit.json"
FORMATS = {"csv", "csv.gz"}
REQUIRED_LOGICAL_COLUMNS = {
    "bills": ("bill_id", "congress", "bill_type", "bill_number"),
    "bills_texts": ("bill_id",),
    "bills_sponsored": ("bill_id", "member_id", "relationship"),
    "members": ("member_id", "bioguide_id"),
    "committees": (),
    "hearings": (),
    "committee_reports": (),
    "amendments": (
        "amendment_id", "congress", "amendment_type", "amendment_number",
    ),
    "amendments_texts": ("amendment_id",),
}
SECRET_QUERY_FRAGMENTS = (
    "api_key", "apikey", "key", "token", "secret", "password", "passwd",
    "credential", "auth", "signature", "sig",
)
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
HEADER_PATTERN = re.compile(r"^[^\x00-\x1f\x7f]+$")
AMENDMENT_ACT_PATTERN = re.compile(
    r"([HS])\.\s*(?:AMDT|AMENDMENT)\.?\s*(\d+)", re.IGNORECASE
)
EXPECTED_ROSTER = 533
EXPECTED_ACTS = 648
ACT_PASS_MINIMUM = 640
PRE_119_ACT_HIT_RATE_MINIMUM = 0.987
ARMED_119_ROW_MINIMUM = 5000
ARMED_STRATUM_HIT_RATE_MINIMUM = 0.95
EXPECTED_DISCOVERY = 18557
TEXT_AGREEMENT_MINIMUM = 0.95
COSponsor_SAMPLE_SIZE = 100
COSponsor_MAX_RATIO = 0.02


class UsageError(ValueError):
    """A CLI, source-map, or output-gate validation error."""


class IntegrityError(RuntimeError):
    """A transport, file-integrity, or required-input failure."""


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, separators=(",", ": "))
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_path(path: Path) -> str:
    if path.is_file():
        return sha256_file(path)
    if not path.is_dir():
        raise IntegrityError(f"required input does not exist: {path}")
    digest = hashlib.sha256()
    for child in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        relative = child.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(child)))
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"invalid JSON input: {path}") from exc


def _valid_filename(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise UsageError("component filename must be nonblank")
    candidate = Path(value)
    if candidate.is_absolute() or candidate.name != value or value in {".", ".."}:
        raise UsageError("component filename must not contain traversal or directories")
    return value


def _secret_query_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    return any(
        normalized == fragment
        or normalized.startswith(fragment + "_")
        or normalized.endswith("_" + fragment)
        for fragment in SECRET_QUERY_FRAGMENTS
    )


def validate_source_url(url: Any, allowed_hosts: set[str] | None = None) -> str:
    if not isinstance(url, str) or not url:
        raise UsageError("component URL must be nonblank")
    parsed = urlsplit(url)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise UsageError("component URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise UsageError("component URL must not contain userinfo")
    if any(_secret_query_key(key) for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        raise UsageError("component URL contains a secret-like query key")
    if allowed_hosts is not None and parsed.hostname.casefold() not in allowed_hosts:
        raise UsageError("component URL host is not explicitly allowlisted")
    return parsed.hostname.casefold()


def validate_source_manifest(
    path: Path,
    *,
    fetch: bool = False,
    allowed_hosts: Iterable[str] = (),
) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise UsageError("--source-manifest must be an existing file")
    value = _load_json(path)
    if not isinstance(value, dict) or set(value) != {"schema", "snapshot_id", "components"}:
        raise UsageError("source manifest has invalid top-level fields")
    schema = value["schema"]
    component_order = SOURCE_COMPONENTS.get(schema) if isinstance(schema, str) else None
    if component_order is None:
        raise UsageError("source manifest schema is invalid")
    if not isinstance(value["snapshot_id"], str) or not value["snapshot_id"].strip():
        raise UsageError("source manifest snapshot_id must be nonblank")
    components = value["components"]
    if not isinstance(components, dict) or tuple(components) != component_order:
        raise UsageError("source manifest components do not match schema in exact order")
    allowlist = {str(host).casefold() for host in allowed_hosts}
    filenames: set[str] = set()
    for component in component_order:
        specification = components[component]
        if not isinstance(specification, dict):
            raise UsageError(f"{component} specification must be an object")
        required_fields = {"url", "filename", "format", "columns"}
        permitted_fields = required_fields | {"expected_sha256"}
        if component == "bills_sponsored":
            required_fields.add("cosponsor_values")
            permitted_fields.add("cosponsor_values")
        if not required_fields.issubset(specification) or not set(specification).issubset(
            permitted_fields
        ):
            raise UsageError(f"{component} specification fields are invalid")
        validate_source_url(
            specification["url"], allowlist if fetch else None
        )
        filename = _valid_filename(specification["filename"])
        if filename in filenames:
            raise UsageError("component filenames must be unique")
        filenames.add(filename)
        format_name = specification["format"]
        if format_name not in FORMATS:
            raise UsageError("only csv and csv.gz component formats are supported")
        if (format_name == "csv" and not filename.casefold().endswith(".csv")) or (
            format_name == "csv.gz" and not filename.casefold().endswith(".csv.gz")
        ):
            raise UsageError("component filename does not match its declared format")
        expected_sha = specification.get("expected_sha256", "")
        if expected_sha and (
            not isinstance(expected_sha, str) or SHA256_PATTERN.fullmatch(expected_sha) is None
        ):
            raise UsageError("expected_sha256 must be blank or a SHA-256 hex digest")
        columns = specification["columns"]
        if not isinstance(columns, dict):
            raise UsageError(f"{component} columns must be an object")
        if any(
            not isinstance(logical, str)
            or not logical
            or not isinstance(physical, str)
            or not physical
            for logical, physical in columns.items()
        ):
            raise UsageError(f"{component} column mappings must be nonblank strings")
        if len(set(columns.values())) != len(columns):
            raise UsageError(f"{component} physical column mappings must be unique")
        missing = set(REQUIRED_LOGICAL_COLUMNS[component]) - set(columns)
        if missing:
            raise UsageError(
                f"{component} is missing logical mappings: {', '.join(sorted(missing))}"
            )
        if component == "bills_sponsored":
            values = specification["cosponsor_values"]
            if (
                not isinstance(values, list)
                or not values
                or any(not isinstance(item, str) or not item.strip() for item in values)
                or len({item.casefold() for item in values}) != len(values)
            ):
                raise UsageError("bills_sponsored cosponsor_values must be unique strings")
    return value, sha256_file(path)


def inspect_component(
    path: Path,
    format_name: str,
    columns: Mapping[str, str],
) -> dict[str, Any]:
    if not path.is_file():
        raise IntegrityError(f"missing BICAM component: {path.name}")
    byte_count = path.stat().st_size
    digest = sha256_file(path)
    try:
        if format_name == "csv.gz":
            binary: Any = gzip.open(path, "rb")
        elif format_name == "csv":
            binary = path.open("rb")
        else:
            raise IntegrityError("unsupported component format")
        with binary:
            with io.TextIOWrapper(binary, encoding="utf-8-sig", newline="") as stream:
                reader = csv.reader(stream)
                try:
                    headers = next(reader)
                except StopIteration as exc:
                    raise IntegrityError(f"component has no header: {path.name}") from exc
                if (
                    not headers
                    or any(not header or HEADER_PATTERN.fullmatch(header) is None for header in headers)
                    or len(set(headers)) != len(headers)
                ):
                    raise IntegrityError(f"component headers are blank, invalid, or duplicated: {path.name}")
                missing = set(columns.values()) - set(headers)
                if missing:
                    raise IntegrityError(
                        f"component {path.name} lacks mapped headers: {', '.join(sorted(missing))}"
                    )
                row_count = sum(1 for _ in reader)
    except (OSError, UnicodeError, csv.Error, gzip.BadGzipFile) as exc:
        if isinstance(exc, IntegrityError):
            raise
        raise IntegrityError(f"component cannot be read: {path.name}") from exc
    return {
        "bytes": byte_count,
        "sha256": digest,
        "row_count": row_count,
        "headers": headers,
    }


def build_arrival_manifest(
    snapshot_dir: Path,
    source_manifest: Mapping[str, Any],
    source_manifest_sha256: str,
) -> dict[str, Any]:
    components: dict[str, Any] = {}
    component_order = SOURCE_COMPONENTS[source_manifest["schema"]]
    for name in component_order:
        specification = source_manifest["components"][name]
        path = snapshot_dir / specification["filename"]
        receipt = inspect_component(path, specification["format"], specification["columns"])
        expected_sha = specification.get("expected_sha256", "")
        if expected_sha and receipt["sha256"].casefold() != expected_sha.casefold():
            raise IntegrityError(f"component SHA-256 mismatch: {name}")
        components[name] = {
            "relative_path": path.relative_to(snapshot_dir).as_posix(),
            "format": specification["format"],
            "bytes": receipt["bytes"],
            "sha256": receipt["sha256"],
            "row_count": receipt["row_count"],
            "columns": dict(specification["columns"]),
        }
    return {
        "schema": ARRIVAL_SCHEMA,
        "snapshot_id": source_manifest["snapshot_id"],
        "source_manifest_sha256": source_manifest_sha256,
        "component_order": list(component_order),
        "components": components,
    }


class _AllowedHostRedirect(urllib.request.HTTPRedirectHandler):
    """Apply the caller-approved host policy to every redirect hop."""
    def __init__(self, url, allowed_hosts=None):
        self.allowed_hosts = None if allowed_hosts is None else {str(host).casefold() for host in allowed_hosts}
        validate_source_url(url, self.allowed_hosts)
        parsed = urlsplit(url)
        self.origin = (parsed.hostname.casefold(), parsed.port or 443)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_source_url(newurl, self.allowed_hosts)
        parsed = urlsplit(newurl)
        if self.allowed_hosts is None and (parsed.hostname.casefold(), parsed.port or 443) != self.origin:
            raise UsageError("component redirect escaped its approved origin")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def atomic_download(
    url: str,
    target: Path,
    expected_sha256: str,
    format_name: str,
    columns: Mapping[str, str],
    *,
    allowed_hosts: Iterable[str] | None = None
) -> dict[str, Any]:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            try:
                opener = urllib.request.build_opener(_AllowedHostRedirect(url, allowed_hosts))
                with opener.open(url, timeout=120) as response:
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        stream.write(block)
            except Exception as exc:
                raise IntegrityError("BICAM transport failed") from exc
            stream.flush()
            os.fsync(stream.fileno())
        receipt = inspect_component(temporary_path, format_name, columns)
        if expected_sha256 and receipt["sha256"].casefold() != expected_sha256.casefold():
            raise IntegrityError("downloaded component SHA-256 mismatch")
        os.replace(temporary_path, target)
        temporary_path = None
        return receipt
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def fetch_snapshot(
    snapshot_dir: Path,
    source_manifest: Mapping[str, Any],
    source_manifest_sha256: str,
    *,
    allowed_hosts: Iterable[str] | None = None
) -> dict[str, Any]:
    approved_hosts = None if allowed_hosts is None else frozenset(allowed_hosts)
    if snapshot_dir.exists():
        if not snapshot_dir.is_dir() or any(snapshot_dir.iterdir()):
            raise UsageError("--fetch requires an empty or nonexistent snapshot directory")
    else:
        snapshot_dir.mkdir(parents=True)
    for name in SOURCE_COMPONENTS[source_manifest["schema"]]:
        specification = source_manifest["components"][name]
        atomic_download(
            specification["url"],
            snapshot_dir / specification["filename"],
            specification.get("expected_sha256", ""),
            specification["format"],
            specification["columns"],
            allowed_hosts=approved_hosts,
        )
    return build_arrival_manifest(
        snapshot_dir, source_manifest, source_manifest_sha256
    )


def load_component_rows(
    snapshot_dir: Path,
    specification: Mapping[str, Any],
) -> list[dict[str, str]]:
    path = snapshot_dir / specification["filename"]
    try:
        if specification["format"] == "csv.gz":
            binary: Any = gzip.open(path, "rb")
        else:
            binary = path.open("rb")
        with binary:
            with io.TextIOWrapper(binary, encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                rows = []
                for raw in reader:
                    rows.append({
                        logical: str(raw.get(physical) or "").strip()
                        for logical, physical in specification["columns"].items()
                    })
                return rows
    except (OSError, UnicodeError, csv.Error, gzip.BadGzipFile) as exc:
        raise IntegrityError(f"cannot load component rows: {path.name}") from exc


def _count_skipped_nonrecord(stats: dict[str, int] | None) -> None:
    if stats is not None:
        stats["skipped_nonrecord"] = stats.get("skipped_nonrecord", 0) + 1


def iter_json_records(
    path: Path,
    stats: dict[str, int] | None = None,
) -> Iterator[dict[str, Any]]:
    if stats is not None:
        stats.setdefault("skipped_nonrecord", 0)
    if path.is_dir():
        for child in sorted(
            candidate
            for candidate in path.rglob("*")
            if candidate.is_file() and candidate.suffix.casefold() in {".json", ".ndjson"}
        ):
            yield from iter_json_records(child, stats)
        return
    if not path.is_file():
        raise IntegrityError(f"required record input does not exist: {path}")
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            if path.suffix.casefold() == ".ndjson":
                for line_number, line in enumerate(stream, start=1):
                    if not line.strip():
                        continue
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        _count_skipped_nonrecord(stats)
                        continue
                    yield value
            else:
                value = json.load(stream)
                if isinstance(value, list):
                    values = value
                elif isinstance(value, dict):
                    nested = next(
                        (
                            value[key]
                            for key in ("records", "bills", "items")
                            if isinstance(value.get(key), list)
                        ),
                        None,
                    )
                    values = nested if nested is not None else [value]
                else:
                    values = [value]
                for record in values:
                    if not isinstance(record, dict):
                        _count_skipped_nonrecord(stats)
                        continue
                    yield record
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, IntegrityError):
            raise
        raise IntegrityError(f"invalid record input: {path}") from exc


def _collect_bioguides(value: Any, output: list[str], blanks: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in {"bioguide", "bioguide_id", "bioguideid"}:
                identifier = str(child or "").strip()
                if identifier:
                    output.append(identifier)
                else:
                    blanks.append(str(key))
            else:
                _collect_bioguides(child, output, blanks)
    elif isinstance(value, list):
        for child in value:
            _collect_bioguides(child, output, blanks)


def read_csv_identifiers(path: Path) -> set[str]:
    if not path.is_file():
        raise IntegrityError(f"identifier CSV does not exist: {path}")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fields = reader.fieldnames or []
            by_normalized = {
                re.sub(r"[^a-z0-9]", "", field.casefold()): field for field in fields
            }
            physical = next(
                (
                    by_normalized[name]
                    for name in ("bioguideid", "bioguide")
                    if name in by_normalized
                ),
                None,
            )
            if physical is None:
                raise IntegrityError(f"identifier CSV lacks a bioguide column: {path.name}")
            return {
                str(row.get(physical) or "").strip()
                for row in reader
                if str(row.get(physical) or "").strip()
            }
    except (OSError, UnicodeError, csv.Error) as exc:
        if isinstance(exc, IntegrityError):
            raise
        raise IntegrityError(f"cannot read identifier CSV: {path}") from exc


def audit_roster(
    roster_path: Path,
    legislators_path: Path,
    voteview_path: Path,
    member_rows: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    roster_values: list[str] = []
    blank_fields: list[str] = []
    _collect_bioguides(_load_json(roster_path), roster_values, blank_fields)
    roster_counts = Counter(roster_values)
    roster_ids = set(roster_counts)
    legislators = read_csv_identifiers(legislators_path)
    voteview = read_csv_identifiers(voteview_path)
    bicam_values = [str(row.get("bioguide_id") or "").strip() for row in member_rows]
    bicam_counts = Counter(value for value in bicam_values if value)
    bicam_ids = set(bicam_counts)
    missing_legislators = sorted(roster_ids - legislators)
    missing_voteview = sorted(roster_ids - voteview)
    missing_bicam = sorted(roster_ids - bicam_ids)
    duplicates = sorted(identifier for identifier, count in roster_counts.items() if count > 1)
    bicam_duplicates = sorted(
        identifier for identifier, count in bicam_counts.items() if count > 1
    )
    joined = len(roster_ids & legislators & voteview & bicam_ids)
    passed = (
        len(roster_ids) == EXPECTED_ROSTER
        and not blank_fields
        and not duplicates
        and not bicam_duplicates
        and joined == EXPECTED_ROSTER
    )
    return {
        "pass": passed,
        "expected": EXPECTED_ROSTER,
        "roster_distinct": len(roster_ids),
        "joined": joined,
        "blank_bioguide_fields": len(blank_fields),
        "missing_from_legislators": missing_legislators,
        "missing_from_voteview": missing_voteview,
        "missing_from_bicam": missing_bicam,
        "roster_duplicates": duplicates,
        "bicam_duplicates": bicam_duplicates,
    }


def _parse_congress(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed_integer = int(text)
        return parsed_integer if parsed_integer > 0 else None
    except ValueError:
        pass
    try:
        parsed = float(text)
    except ValueError:
        return None
    if parsed.is_integer() and parsed > 0:
        return int(parsed)
    return None


def normalize_bicam_bill_type(value: Any, number: Any) -> tuple[str, str, int] | None:
    token = re.sub(r"[^a-z]", "", str(value or "").casefold())
    mappings = {
        "hr": ("H", "R"), "housebill": ("H", "R"),
        "s": ("S", "R"), "senatebill": ("S", "R"),
        "hres": ("H", "RES"), "sres": ("S", "RES"),
        "hjres": ("H", "JRES"), "sjres": ("S", "JRES"),
        "hconres": ("H", "CONRES"), "sconres": ("S", "CONRES"),
    }
    if token not in mappings:
        return None
    try:
        parsed_number = int(str(number).strip())
    except (TypeError, ValueError):
        return None
    if parsed_number < 1:
        return None
    chamber, type_token = mappings[token]
    return chamber, type_token, parsed_number


def normalize_repo_act(record: Mapping[str, Any]) -> tuple[int, str, str, int] | None:
    congress_value = next(
        (record.get(field) for field in ("congress", "congress_number") if record.get(field) not in (None, "")),
        None,
    )
    bill_value = next(
        (
            record.get(field)
            for field in ("bill", "bill_name", "bill_id", "act", "name")
            if record.get(field) not in (None, "")
        ),
        None,
    )
    congress = _parse_congress(congress_value)
    if congress is None:
        return None
    typed = bill_type_key(bill_value)
    if typed is None:
        return None
    return congress, typed[0], typed[1], typed[2]


def canonical_act_key(key: tuple[int, str, str, int]) -> str:
    return f"{key[0]}|{key[1]}|{key[2]}|{key[3]}"


def _is_non_bill_act(value: Any) -> bool:
    text = str(value or "").strip()
    return text.upper().startswith("LTR-") or AMENDMENT_ACT_PATTERN.fullmatch(
        text
    ) is not None


def _bicam_bill_index(
    bill_rows: Sequence[Mapping[str, str]],
) -> dict[tuple[int, str, str, int], list[str]]:
    index: dict[tuple[int, str, str, int], list[str]] = defaultdict(list)
    for row in bill_rows:
        congress = _parse_congress(row.get("congress"))
        if congress is None:
            continue
        typed = normalize_bicam_bill_type(row.get("bill_type"), row.get("bill_number"))
        bill_id = str(row.get("bill_id") or "").strip()
        if typed is not None and bill_id:
            index[(congress, typed[0], typed[1], typed[2])].append(bill_id)
    return index


def audit_act_universe(
    tag_records: Sequence[Mapping[str, Any]],
    bill_rows: Sequence[Mapping[str, str]],
    skipped_nonrecord: int = 0,
    *,
    amendment_rows: Sequence[Mapping[str, str]] = (),
    amendments_texts_row_count: int = 0,
    amendments_declared: bool = False,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    raw_counts: Counter[str] = Counter()
    for record in tag_records:
        bill_value = next(
            (
                record.get(field)
                for field in ("bill", "bill_name", "bill_id", "act", "name")
                if record.get(field) not in (None, "")
            ),
            "",
        )
        congress = next(
            (
                record.get(field)
                for field in ("congress", "congress_number")
                if record.get(field) not in (None, "")
            ),
            "",
        )
        raw_counts[f"{bill_value}|{congress}"] += 1
    bicam_index = _bicam_bill_index(bill_rows)
    misses: list[dict[str, Any]] = []
    joinable: dict[str, dict[str, Any]] = {}
    canonical_counts: Counter[str] = Counter()
    strata: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "hit": 0})
    bill_exclusions_by_congress: Counter[str] = Counter()
    amendments_armed = (
        amendments_declared
        and len(amendment_rows) >= 1
        and amendments_texts_row_count >= 1
    )
    amendment_ids = {
        str(row.get("amendment_id") or "").strip()
        for row in amendment_rows
        if row.get("amendment_id")
    }
    amendment_denominator = 0
    amendment_hit = 0
    for record in tag_records:
        bill_value = next(
            (
                record.get(field)
                for field in ("bill", "bill_name", "bill_id", "act", "name")
                if record.get(field) not in (None, "")
            ),
            "",
        )
        congress_value = next(
            (
                record.get(field)
                for field in ("congress", "congress_number")
                if record.get(field) not in (None, "")
            ),
            "",
        )
        congress = _parse_congress(congress_value)
        if congress is not None:
            strata[str(congress)]["n"] += 1
        amendment_match = AMENDMENT_ACT_PATTERN.fullmatch(
            str(bill_value or "").strip()
        )
        if amendments_armed and amendment_match is not None:
            amendment_denominator += 1
            if congress is None:
                misses.append({"raw": dict(record), "reason": "MALFORMED"})
                continue
            bill_exclusions_by_congress[str(congress)] += 1
            amendment_prefix = "samdt" if amendment_match.group(1).upper() == "S" else "hamdt"
            amendment_id = (
                f"{amendment_prefix}{int(amendment_match.group(2))}-{congress}"
            )
            if amendment_id in amendment_ids:
                amendment_hit += 1
            else:
                misses.append({"raw": dict(record), "reason": "NO_BICAM_MATCH"})
            continue
        key = normalize_repo_act(record)
        if key is None:
            reason = "NON_BILL" if _is_non_bill_act(bill_value) else "MALFORMED"
            misses.append({"raw": dict(record), "reason": reason})
            if reason == "NON_BILL" and congress is not None:
                bill_exclusions_by_congress[str(congress)] += 1
            continue
        canonical = canonical_act_key(key)
        canonical_counts[canonical] += 1
        bill_ids = sorted(set(bicam_index.get(key, [])))
        if not bill_ids:
            misses.append({"raw": dict(record), "reason": "NO_BICAM_MATCH"})
            continue
        if len(bill_ids) > 1:
            misses.append({"raw": dict(record), "reason": "AMBIGUOUS_BICAM_MATCH"})
            continue
        strata[str(key[0])]["hit"] += 1
        if canonical not in joinable:
            joinable[canonical] = {
                "bill_id": bill_ids[0],
                "raw": dict(record),
            }
    duplicates = sorted(key for key, count in raw_counts.items() if count > 1)
    canonical_duplicates = sorted(
        key for key, count in canonical_counts.items() if count > 1
    )
    unique_rows = len(raw_counts)
    joined = len(joinable)
    bill_strata = {}
    for congress_key, counts in sorted(strata.items(), key=lambda item: int(item[0])):
        denominator = counts["n"] - bill_exclusions_by_congress[congress_key]
        hit_rate = counts["hit"] / denominator if denominator else 0.0
        bill_strata[congress_key] = {
            "denominator": denominator,
            "hit": counts["hit"],
            "hit_rate": hit_rate,
        }
    pre_119_denominator = sum(
        row["denominator"]
        for congress_key, row in bill_strata.items()
        if int(congress_key) < 119
    )
    pre_119_hit = sum(
        row["hit"]
        for congress_key, row in bill_strata.items()
        if int(congress_key) < 119
    )
    pre_119_hit_rate = (
        pre_119_hit / pre_119_denominator if pre_119_denominator else 0.0
    )
    congress_119 = bill_strata.get(
        "119", {"denominator": 0, "hit": 0, "hit_rate": 0.0}
    )
    congress_119_snapshot_rows = sum(
        1 for row in bill_rows if _parse_congress(row.get("congress")) == 119
    )
    congress_119_armed = congress_119_snapshot_rows >= ARMED_119_ROW_MINIMUM
    amendment_hit_rate = (
        amendment_hit / amendment_denominator if amendment_denominator else 0.0
    )
    amendment_bar_pass = (
        not amendments_declared
        or (
            amendments_armed
            and amendment_denominator > 0
            and amendment_hit_rate >= ARMED_STRATUM_HIT_RATE_MINIMUM
        )
    )
    malformed_misses = sum(miss["reason"] == "MALFORMED" for miss in misses)
    result = {
        "pass": (
            len(tag_records) == EXPECTED_ACTS
            and unique_rows == EXPECTED_ACTS
            and not duplicates
            and not canonical_duplicates
            and pre_119_denominator > 0
            and pre_119_hit_rate >= PRE_119_ACT_HIT_RATE_MINIMUM
            and malformed_misses == 0
            and (
                not congress_119_armed
                or (
                    congress_119["denominator"] > 0
                    and congress_119["hit_rate"] >= ARMED_STRATUM_HIT_RATE_MINIMUM
                )
            )
            and amendment_bar_pass
            and skipped_nonrecord == 0
        ),
        "expected": EXPECTED_ACTS,
        "minimum_joined": ACT_PASS_MINIMUM,
        "tag_rows": len(tag_records),
        "unique_bill_congress_rows": unique_rows,
        "joined": joined,
        "duplicate_bill_congress_rows": duplicates,
        "duplicate_canonical_keys": canonical_duplicates,
        "strata": dict(sorted(strata.items(), key=lambda item: int(item[0]))),
        "bill_strata": bill_strata,
        "pre_119_bill_stratum": {
            "denominator": pre_119_denominator,
            "hit": pre_119_hit,
            "hit_rate": pre_119_hit_rate,
        },
        "armed_strata": {
            "119": {
                "armed": congress_119_armed,
                "condition": "bills.csv contains >= 5000 congress-119 rows",
                "hit_rate": congress_119["hit_rate"],
                "congress_119_snapshot_rows": congress_119_snapshot_rows,
            },
            "amendments": {
                "armed": False,
                "condition": "amendments component not declared in manifest",
                "hit_rate": None,
            } if not amendments_declared else {
                "armed": amendments_armed,
                "condition": (
                    "amendments and amendments_texts components each contain >= 1 row"
                    if amendments_armed
                    else "amendments component declared but empty"
                ),
                "hit_rate": amendment_hit_rate if amendments_armed else None,
                "denominator": amendment_denominator,
                "hit": amendment_hit,
                "amendments_snapshot_rows": len(amendment_rows),
                "amendments_texts_snapshot_rows": amendments_texts_row_count,
            },
        },
        "malformed_misses": malformed_misses,
        "legacy_composite": {
            "minimum_joined": ACT_PASS_MINIMUM,
            "joined": joined,
            "pass": joined >= ACT_PASS_MINIMUM,
        },
        "skipped_nonrecord": skipped_nonrecord,
        "misses": misses,
    }
    return result, joinable


def _receipt_key_from_path(path: Path, receipts_root: Path) -> str:
    try:
        parts = path.relative_to(receipts_root).parts
    except ValueError:
        parts = ()
    if len(parts) >= 4:
        return f"{parts[0]}:{parts[1]}:{parts[2]}"
    return path.relative_to(receipts_root).as_posix()


def load_legfix5_presence(
    run_path: Path,
    stats: dict[str, int] | None = None,
) -> tuple[set[str], list[dict[str, str]], int]:
    receipts_root = run_path / "receipts"
    accepted_root_path = run_path / "accepted_html"
    missing = [
        name
        for name, path in (
            ("receipts/", receipts_root),
            ("accepted_html/", accepted_root_path),
        )
        if not path.is_dir()
    ]
    if missing:
        raise IntegrityError(
            "legfix5 run path must contain expected children receipts/ and "
            f"accepted_html/; missing: {', '.join(missing)}"
        )
    if stats is not None:
        stats.setdefault("skipped_nonrecord", 0)
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    receipt_paths = sorted(
        candidate
        for candidate in receipts_root.rglob("*")
        if candidate.is_file()
        and candidate.suffix.casefold() in {".json", ".ndjson"}
    )
    for receipt_path in receipt_paths:
        try:
            records.extend(iter_json_records(receipt_path, stats))
        except IntegrityError:
            errors.append({
                "canonical_key": _receipt_key_from_path(
                    receipt_path, receipts_root
                ),
                "reason": "UNREADABLE_OR_UNPARSEABLE_RECEIPT",
            })
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        canonical = str(record.get("canonical_key") or "").strip()
        if canonical:
            grouped[canonical].append(record)
    presence: set[str] = set()
    body_cache: dict[Path, bool] = {}
    accepted_root = accepted_root_path.resolve()
    for canonical in sorted(grouped):
        receipts = grouped[canonical]
        flags = []
        body_values = []
        for receipt in receipts:
            status = str(receipt.get("status") or receipt.get("result") or "").casefold()
            flags.append(receipt.get("accepted") is True or status == "accepted")
            body_values.append(
                receipt.get("accepted_html")
                or receipt.get("accepted_html_path")
                or receipt.get("body_path")
                or receipt.get("html_file")
            )
        if not flags or not all(flags) or any(not value for value in body_values):
            errors.append({"canonical_key": canonical, "reason": "MISSING_OR_INCONSISTENT_RECEIPT"})
            continue
        valid = True
        for body_value in body_values:
            body_path = Path(str(body_value))
            body = (
                body_path.resolve()
                if body_path.is_absolute()
                else (run_path / body_path).resolve()
            )
            try:
                body.relative_to(accepted_root)
            except ValueError:
                valid = False
                break
            if body.name != "page.html.gz":
                valid = False
                break
            if body not in body_cache:
                try:
                    with gzip.open(body, "rb") as stream:
                        body_cache[body] = bool(stream.read(1))
                except (OSError, gzip.BadGzipFile):
                    body_cache[body] = False
            if not body_cache[body]:
                valid = False
                break
        if valid:
            presence.add(canonical)
        else:
            errors.append({"canonical_key": canonical, "reason": "MISSING_OR_UNREADABLE_BODY"})
    return presence, errors, len(records)


def _text_discovery_stratum(canonical: str) -> str:
    parts = canonical.split(":")
    if len(parts) != 3:
        return "other"
    congress = _parse_congress(parts[0])
    if congress is None:
        return "other"
    if parts[1].casefold() in {"hamdt", "samdt"}:
        return "amendments"
    if congress < 119:
        return "pre_119_bills"
    if congress == 119:
        return "119"
    return "other"


def _text_amendment_id(canonical: str) -> str | None:
    parts = canonical.split(":")
    if len(parts) != 3:
        return None
    amendment_type = parts[1].casefold()
    if amendment_type not in {"hamdt", "samdt"}:
        return None
    congress = _parse_congress(parts[0])
    amendment_number = parts[2].strip()
    if congress is None or re.fullmatch(r"\d+", amendment_number) is None:
        return None
    return f"{amendment_type}{int(amendment_number)}-{congress}"


def audit_text_presence(
    discovery_records: Sequence[Mapping[str, Any]],
    bicam_bill_ids: set[str],
    legfix5_presence: set[str],
    legfix5_errors: Sequence[Mapping[str, str]] = (),
    skipped_nonrecord: int = 0,
    legfix5_skipped_nonrecord: int = 0,
    *,
    bicam_amendment_ids: set[str] | None = None,
    amendments_armed: bool = False,
    amendments_declared: bool = False,
) -> dict[str, Any]:
    amendment_ids = bicam_amendment_ids or set()
    keyed: dict[str, Mapping[str, Any]] = {}
    duplicate_keys: list[str] = []
    for record in discovery_records:
        canonical = str(record.get("canonical_key") or "").strip()
        if not canonical:
            continue
        if canonical in keyed:
            duplicate_keys.append(canonical)
        else:
            keyed[canonical] = record
    matrix = {
        "BICAM_AND_LEGFIX5": 0,
        "BICAM_ONLY": 0,
        "LEGFIX5_ONLY": 0,
        "NEITHER": 0,
    }
    stratum_matrices = {
        name: {
            "BICAM_AND_LEGFIX5": 0,
            "BICAM_ONLY": 0,
            "LEGFIX5_ONLY": 0,
            "NEITHER": 0,
        }
        for name in ("pre_119_bills", "119", "amendments", "other")
    }
    disagreements: list[dict[str, str]] = []
    for canonical, record in keyed.items():
        stratum = _text_discovery_stratum(canonical)
        if amendments_armed and stratum == "amendments":
            amendment_id = _text_amendment_id(canonical)
            bicam = amendment_id is not None and amendment_id in amendment_ids
        else:
            bill_id = str(record.get("bill_id") or "").strip()
            if not bill_id:
                parts = canonical.split(":")
                bill_id = (
                    parts[1] + parts[2] + "-" + parts[0]
                    if len(parts) == 3 else canonical
                )
            bicam = bill_id in bicam_bill_ids
        legfix = canonical in legfix5_presence
        if bicam and legfix:
            classification = "BICAM_AND_LEGFIX5"
        elif bicam:
            classification = "BICAM_ONLY"
            disagreements.append({"canonical_key": canonical, "classification": "BICAM_ONLY"})
        elif legfix:
            classification = "LEGFIX5_ONLY"
            disagreements.append({"canonical_key": canonical, "classification": "LEGFIX5_ONLY"})
        else:
            classification = "NEITHER"
        matrix[classification] += 1
        stratum_matrices[stratum][classification] += 1
    denominator = len(keyed)
    agreement = matrix["BICAM_AND_LEGFIX5"] + matrix["NEITHER"]
    ratio = agreement / denominator if denominator else 1.0
    pre_119_matrix = stratum_matrices["pre_119_bills"]
    pre_119_size = sum(pre_119_matrix.values())
    pre_119_agreement = (
        pre_119_matrix["BICAM_AND_LEGFIX5"] + pre_119_matrix["NEITHER"]
    )
    pre_119_ratio = (
        pre_119_agreement / pre_119_size if pre_119_size else 0.0
    )
    amendments_matrix = stratum_matrices["amendments"]
    amendments_size = sum(amendments_matrix.values())
    amendments_agreement = (
        amendments_matrix["BICAM_AND_LEGFIX5"] + amendments_matrix["NEITHER"]
    )
    amendments_ratio = (
        amendments_agreement / amendments_size if amendments_size else 0.0
    )
    amendments_bar_pass = (
        not amendments_declared
        or (
            amendments_armed
            and amendments_size > 0
            and amendments_ratio >= TEXT_AGREEMENT_MINIMUM
        )
    )
    disagreements.sort(
        key=lambda row: (
            hashlib.sha256(row["canonical_key"].encode("utf-8")).hexdigest(),
            row["canonical_key"],
        )
    )
    selected = disagreements[:50] if len(disagreements) >= 50 else disagreements
    return {
        "pass": (
            len(discovery_records) == EXPECTED_DISCOVERY
            and denominator == EXPECTED_DISCOVERY
            and not duplicate_keys
            and pre_119_size > 0
            and pre_119_ratio >= TEXT_AGREEMENT_MINIMUM
            and amendments_bar_pass
            and not legfix5_errors
            and skipped_nonrecord == 0
            and legfix5_skipped_nonrecord == 0
        ),
        "expected_denominator": EXPECTED_DISCOVERY,
        "denominator": denominator,
        "agreement": agreement,
        "agreement_ratio": ratio,
        "matrix": matrix,
        "strata": {
            "pre_119_bills": {
                "size": pre_119_size,
                "agreement": pre_119_agreement,
                "agreement_ratio": pre_119_ratio,
                "matrix": pre_119_matrix,
            },
            "119": {
                "label": "report_only",
                "size": sum(stratum_matrices["119"].values()),
                "LEGFIX5_ONLY": stratum_matrices["119"]["LEGFIX5_ONLY"],
            },
            "amendments": {
                "label": "report_only",
                "size": sum(stratum_matrices["amendments"].values()),
                "LEGFIX5_ONLY": stratum_matrices["amendments"]["LEGFIX5_ONLY"],
            } if not amendments_armed else {
                "label": "armed",
                "size": amendments_size,
                "LEGFIX5_ONLY": amendments_matrix["LEGFIX5_ONLY"],
                "agreement": amendments_agreement,
                "agreement_ratio": amendments_ratio,
                "matrix": amendments_matrix,
            },
        },
        "legacy_overall": {
            "agreement": agreement,
            "agreement_ratio": ratio,
        },
        "duplicate_canonical_keys": sorted(set(duplicate_keys)),
        "disagreement_count": len(disagreements),
        "selected_disagreements": selected,
        "legfix5_errors": list(legfix5_errors),
        "skipped_nonrecord": skipped_nonrecord,
        "legfix5_skipped_nonrecord": legfix5_skipped_nonrecord,
    }


def _bioguides_from_cosponsors(value: Any) -> set[str]:
    output: set[str] = set()
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                output.add(item.strip())
            elif isinstance(item, dict):
                candidate = next(
                    (
                        item.get(field)
                        for field in ("bioguide", "bioguide_id", "bioguideId")
                        if item.get(field)
                    ),
                    None,
                )
                if candidate:
                    output.add(str(candidate).strip())
    return output


def load_billstatus_cosponsors(
    path: Path,
    stats: dict[str, int] | None = None,
) -> tuple[dict[str, set[str]], int]:
    records = list(iter_json_records(path, stats))
    output: dict[str, set[str]] = {}
    for record in records:
        canonical = str(record.get("canonical_act_key") or "").strip()
        if not canonical:
            key = normalize_repo_act(record)
            if key is None:
                continue
            canonical = canonical_act_key(key)
        cosponsors = _bioguides_from_cosponsors(record.get("cosponsors", []))
        output.setdefault(canonical, set()).update(cosponsors)
    return output, len(records)


def build_bicam_cosponsors(
    joinable: Mapping[str, Mapping[str, Any]],
    member_rows: Sequence[Mapping[str, str]],
    sponsored_rows: Sequence[Mapping[str, str]],
    cosponsor_values: Sequence[str],
) -> dict[str, set[str]]:
    member_index: dict[str, set[str]] = defaultdict(set)
    for row in member_rows:
        member_id = str(row.get("member_id") or "").strip()
        bioguide = str(row.get("bioguide_id") or "").strip()
        if member_id and bioguide:
            member_index[member_id].add(bioguide)
    bill_to_act = {
        str(value["bill_id"]): canonical for canonical, value in joinable.items()
    }
    accepted = {value.casefold() for value in cosponsor_values}
    output: dict[str, set[str]] = {canonical: set() for canonical in joinable}
    for row in sponsored_rows:
        if str(row.get("relationship") or "").strip().casefold() not in accepted:
            continue
        canonical = bill_to_act.get(str(row.get("bill_id") or "").strip())
        member_id = str(row.get("member_id") or "").strip()
        if canonical is None or len(member_index.get(member_id, set())) != 1:
            continue
        output[canonical].update(member_index[member_id])
    return output


def _sample_sha256(sample: Sequence[str]) -> str:
    return hashlib.sha256("".join(f"{key}\n" for key in sample).encode("utf-8")).hexdigest()


def audit_cosponsor_agreement(
    joinable_keys: Iterable[str],
    repository: Mapping[str, set[str]],
    bicam: Mapping[str, set[str]],
    sample_size: int = COSponsor_SAMPLE_SIZE,
    skipped_nonrecord: int = 0,
) -> dict[str, Any]:
    both = sorted(
        set(joinable_keys) & set(repository) & set(bicam),
        key=lambda key: (hashlib.sha256(key.encode("utf-8")).hexdigest(), key),
    )
    sample = both[:sample_size]
    repository_total = sum(len(repository[key]) for key in sample)
    bicam_total = sum(len(bicam[key]) for key in sample)
    symmetric_difference_total = sum(
        len(repository[key] ^ bicam[key]) for key in sample
    )
    if repository_total:
        ratio = symmetric_difference_total / repository_total
    else:
        ratio = 0.0 if symmetric_difference_total == 0 else math.inf
    differences = []
    per_act_symmetric_differences = []
    for key in sample:
        missing_from_bicam = sorted(repository[key] - bicam[key])
        extra_in_bicam = sorted(bicam[key] - repository[key])
        symmetric_difference_count = len(missing_from_bicam) + len(extra_in_bicam)
        per_act_symmetric_differences.append({
            "canonical_act_key": key,
            "symmetric_difference_count": symmetric_difference_count,
        })
        if missing_from_bicam or extra_in_bicam:
            differences.append({
                "canonical_act_key": key,
                "repository_count": len(repository[key]),
                "bicam_count": len(bicam[key]),
                "symmetric_difference_count": symmetric_difference_count,
                "missing_from_bicam": missing_from_bicam,
                "extra_in_bicam": extra_in_bicam,
            })
    return {
        "pass": (
            len(sample) == sample_size
            and ratio <= COSponsor_MAX_RATIO
            and skipped_nonrecord == 0
        ),
        "required_sample_size": sample_size,
        "both_sources_count": len(both),
        "sample": sample,
        "sample_sha256": _sample_sha256(sample),
        "repository_total": repository_total,
        "bicam_total": bicam_total,
        "symmetric_difference_total": symmetric_difference_total,
        "identity_mismatch_ratio": ratio,
        "absolute_aggregate_ratio": ratio,
        "per_act_symmetric_differences": per_act_symmetric_differences,
        "per_act_differences": differences,
        "skipped_nonrecord": skipped_nonrecord,
    }


def _relative_path(path: Path, base: Path) -> str:
    return Path(os.path.relpath(path.resolve(), base.resolve())).as_posix()


def input_receipt(
    path: Path,
    base: Path,
    row_count: int,
) -> dict[str, Any]:
    return {
        "relative_path": _relative_path(path, base),
        "sha256": sha256_path(path),
        "row_count": row_count,
    }


def run_audits(
    args: argparse.Namespace,
    source_manifest: Mapping[str, Any],
    source_manifest_sha256: str,
    arrival_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    component_order = SOURCE_COMPONENTS[source_manifest["schema"]]
    component_rows = {
        name: load_component_rows(args.snapshot_dir, source_manifest["components"][name])
        for name in component_order
    }
    amendments_declared = source_manifest["schema"] == SOURCE_SCHEMA_V2
    amendment_rows = component_rows.get("amendments", [])
    amendments_texts_rows = component_rows.get("amendments_texts", [])
    amendments_armed = (
        amendments_declared
        and len(amendment_rows) >= 1
        and len(amendments_texts_rows) >= 1
    )
    roster = audit_roster(
        args.roster, args.legislators, args.voteview, component_rows["members"]
    )
    tag_stats: dict[str, int] = {}
    tag_records = list(iter_json_records(args.tags, tag_stats))
    acts, joinable = audit_act_universe(
        tag_records,
        component_rows["bills"],
        tag_stats["skipped_nonrecord"],
        amendment_rows=amendment_rows,
        amendments_texts_row_count=len(amendments_texts_rows),
        amendments_declared=amendments_declared,
    )
    discovery_stats: dict[str, int] = {}
    discovery_records = list(
        iter_json_records(args.legfix5_fulltext, discovery_stats)
    )
    legfix5_stats: dict[str, int] = {}
    legfix_presence, legfix_errors, receipt_count = load_legfix5_presence(
        args.legfix5_run, legfix5_stats
    )
    text = audit_text_presence(
        discovery_records,
        {row["bill_id"] for row in component_rows["bills_texts"] if row.get("bill_id")},
        legfix_presence,
        legfix_errors,
        discovery_stats["skipped_nonrecord"],
        legfix5_stats["skipped_nonrecord"],
        bicam_amendment_ids={
            row["amendment_id"]
            for row in amendments_texts_rows
            if row.get("amendment_id")
        },
        amendments_armed=amendments_armed,
        amendments_declared=amendments_declared,
    )
    billstatus_stats: dict[str, int] = {}
    repository_cosponsors, billstatus_count = load_billstatus_cosponsors(
        args.billstatus_parsed, billstatus_stats
    )
    bicam_cosponsors = build_bicam_cosponsors(
        joinable,
        component_rows["members"],
        component_rows["bills_sponsored"],
        source_manifest["components"]["bills_sponsored"]["cosponsor_values"],
    )
    cosponsors = audit_cosponsor_agreement(
        joinable,
        repository_cosponsors,
        bicam_cosponsors,
        skipped_nonrecord=billstatus_stats["skipped_nonrecord"],
    )
    bars = {
        "roster_join": {"pass": roster["pass"], "bar": "533/533"},
        "act_universe_join": {
            "pass": acts["pass"],
            "bar": (
                ">=0.987 pre-119th bill-stratum hit rate; armed 119 >=0.95"
                + ("; armed amendments >=0.95" if amendments_declared else "")
            ),
        },
        "text_presence": {
            "pass": text["pass"],
            "bar": (
                ">=0.95 pre-119th bill discovery-key agreement/"
                f"{text['strata']['pre_119_bills']['size']}"
                + ("; armed amendments >=0.95" if amendments_declared else "")
            ),
        },
        "cosponsor_agreement": {
            "pass": cosponsors["pass"],
            "bar": "<=0.02 identity symmetric-difference/repository entries",
        },
    }
    passed = all(value["pass"] for value in bars.values())
    errors = [name for name, value in bars.items() if not value["pass"]]
    inputs = {
        "source_manifest": input_receipt(args.source_manifest, args.repo_root, 1),
        "components": {
            name: {
                "relative_path": arrival_manifest["components"][name]["relative_path"],
                "sha256": arrival_manifest["components"][name]["sha256"],
                "row_count": arrival_manifest["components"][name]["row_count"],
            }
            for name in component_order
        },
        "roster": input_receipt(args.roster, args.repo_root, roster["roster_distinct"]),
        "legislators": input_receipt(args.legislators, args.repo_root, len(read_csv_identifiers(args.legislators))),
        "voteview": input_receipt(args.voteview, args.repo_root, len(read_csv_identifiers(args.voteview))),
        "tags": input_receipt(args.tags, args.repo_root, len(tag_records)),
        "legfix5_run": input_receipt(args.legfix5_run, args.repo_root, receipt_count),
        "legfix5_fulltext": input_receipt(args.legfix5_fulltext, args.repo_root, len(discovery_records)),
        "billstatus_parsed": input_receipt(args.billstatus_parsed, args.repo_root, billstatus_count),
    }
    return {
        "schema": AUDIT_SCHEMA,
        "bars_schema": "d011-stratum-v1",
        "status": "PASS" if passed else "FAIL",
        "arrival_manifest_sha256": sha256_bytes(canonical_json_bytes(arrival_manifest)),
        "inputs": inputs,
        "bars": bars,
        "roster_join": roster,
        "act_universe_join": acts,
        "text_presence": text,
        "cosponsor_agreement": cosponsors,
        "errors": errors,
    }


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(canonical_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def output_paths(out_dir: Path) -> tuple[Path, Path]:
    return out_dir / ARRIVAL_FILENAME, out_dir / AUDIT_FILENAME


def validate_output_gate(out_dir: Path, replace: bool) -> tuple[Path, Path]:
    arrival_path, audit_path = output_paths(out_dir)
    if not replace and (arrival_path.exists() or audit_path.exists()):
        raise UsageError("existing audit output requires --replace-audit")
    return arrival_path, audit_path


def write_audit_outputs(
    out_dir: Path,
    arrival_manifest: Mapping[str, Any],
    audit_report: Mapping[str, Any],
    replace: bool,
) -> None:
    arrival_path, audit_path = validate_output_gate(out_dir, replace)
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(arrival_path, arrival_manifest)
    atomic_write_json(audit_path, audit_report)


def build_parser() -> argparse.ArgumentParser:
    root = Path(ROOT)
    default_legfix_parent = (
        root / "audits" / "legfix5" / "congress_advanced_pages_20260725"
    )
    default_legfix_run = (
        default_legfix_parent / "text_relevance_httr_full_20260727z"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--fetch", action="store_true")
    action.add_argument("--audit", action="store_true")
    parser.add_argument("--allow-host", action="append", default=[])
    parser.add_argument("--repo-root", type=Path, default=root)
    parser.add_argument("--roster", type=Path, default=root / "data" / "engine" / "congestion.json")
    parser.add_argument("--legislators", type=Path, default=root / "fec" / "legislators.csv")
    parser.add_argument("--voteview", type=Path, default=root / "votes" / "HS119_members.csv")
    parser.add_argument("--tags", type=Path, default=root / "bill_beneficiary_tags.ndjson")
    parser.add_argument("--legfix5-run", type=Path, default=default_legfix_run)
    parser.add_argument(
        "--legfix5-fulltext",
        type=Path,
        default=default_legfix_parent / "congress_advanced_records.ndjson",
    )
    parser.add_argument("--billstatus-parsed", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--replace-audit", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not args.fetch and not args.audit:
            print("dry-run: no network and no writes")
            return 0
        if args.source_manifest is None:
            raise UsageError("--source-manifest is required for --fetch or --audit")
        if args.billstatus_parsed is None:
            raise UsageError("--billstatus-parsed is required for --fetch or --audit")
        args.out_dir = args.out_dir or args.snapshot_dir / "audit"
        source_manifest, source_hash = validate_source_manifest(
            args.source_manifest,
            fetch=args.fetch,
            allowed_hosts=args.allow_host,
        )
        validate_output_gate(args.out_dir, args.replace_audit)
        if args.fetch:
            if os.environ.get("BICAM_FETCH_OK") != "1":
                raise UsageError("--fetch requires BICAM_FETCH_OK=1")
            arrival = fetch_snapshot(
                args.snapshot_dir, source_manifest, source_hash,
                allowed_hosts=args.allow_host,
            )
        else:
            arrival = build_arrival_manifest(
                args.snapshot_dir, source_manifest, source_hash
            )
        report = run_audits(args, source_manifest, source_hash, arrival)
        write_audit_outputs(
            args.out_dir, arrival, report, args.replace_audit
        )
        return 0 if report["status"] == "PASS" else 3
    except UsageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except (IntegrityError, OSError, csv.Error, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
