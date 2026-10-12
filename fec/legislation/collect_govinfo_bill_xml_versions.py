#!/usr/bin/env python3
"""Immutable, fail-closed collector for official GovInfo bill-version XML.

The input is the existing parsed BillStatus directory.  The collector never
uses a browser, Congress.gov, cookies, Cloudflare state, or retry sleeps.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import re
import ssl
import sys
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from legislation_measure_key import CANONICAL_KEY_RE
except ImportError:  # pragma: no cover - package import path
    from fec.legislation_measure_key import CANONICAL_KEY_RE


SCHEMA = "govinfo-bill-version-collector-v1"
DEFAULT_WORKERS = 16
MAX_XML_BYTES = 64 * 1024 * 1024
EXPECTED_RECORDS = 14_854
EXPECTED_JOBS = 18_440
EXPECTED_MISSING_KEYS = frozenset(
    {
        "117:hr:5677",
        "117:hr:5695",
        "118:hr:3496",
        "118:hr:3571",
        "118:hr:7324",
        "119:hr:4584",
        "119:hr:5185",
        "119:hr:5210",
        "119:hr:9821",
        "119:hr:9900",
    }
)

# This collector used to accept ``^\d{1,3}:[a-z]+:\d+$`` -- any Congress, any
# lowercase token as a measure type, and the numbers ``0``/``007`` -- while
# every downstream gate rejected all of those.  It now shares the lane's one
# strict definition, so a malformed key fails at ingestion instead of surviving
# to the gate.  ``groupdict()`` still returns ``congress``/``measure_type``/
# ``number``.
KEY_RE = CANONICAL_KEY_RE
BILLS_URL_RE = re.compile(
    r"^/content/pkg/"
    r"(?P<package>BILLS-(?P<congress>\d+)(?P<measure_type>[a-z]+)"
    r"(?P<number>\d+)(?P<version>[a-z][a-z0-9]*))"
    r"/(?P<format>xml|uslm)/(?P=package)\.xml$"
)
PLAW_URL_RE = re.compile(
    r"^/content/pkg/"
    r"(?P<package>PLAW-(?P<congress>\d+)(?P<law_type>publ|priv)"
    r"(?P<law_number>\d+))"
    r"/(?P<format>xml|uslm)/(?P=package)\.xml$"
)
LEGACY_CONGRESS_DOCTYPE_RE = re.compile(
    rb'<!DOCTYPE\s+(?:'
    rb'bill\s+PUBLIC\s+"-//US Congress//DTDs/bill\.dtd//EN"\s+"bill\.dtd"|'
    rb'resolution\s+PUBLIC\s+"-//US Congress//DTDs/res\.dtd//EN"\s+"res\.dtd"|'
    rb'amendment-doc\s+PUBLIC\s+"-//US Congress//DTDs/amend\.dtd//EN"\s+"amend\.dtd"|'
    rb'(?:bill|law)\s+PUBLIC\s+'
    rb'"-//US Congress//DTD [^"]+//EN"\s+'
    rb'"https?://xml\.house\.gov/schemas/[^"]+\.dtd")\s*>',
    re.IGNORECASE,
)
DC_TITLE_TAG = "{http://purl.org/dc/elements/1.1/}title"
# ``amend.dtd`` carries no <legis-num>.  The Dublin Core title is the only
# official identity surface in an engrossed-amendment document, and it states
# congress, measure, and version together, e.g. "116 S178 EAH: ..." or
# "117 HR 1437 EAH: ...".  Requiring all three to match is strictly stronger
# than the <legis-num> check used for bills and resolutions, which pins the
# measure alone.
AMENDMENT_DC_TITLE_RE = re.compile(
    r"^\s*(?P<identity>\d{1,3}\s+[A-Za-z][A-Za-z.\s]*\d+\s+[A-Za-z][A-Za-z0-9]*)"
    r"\s*:"
)
CONGRESS_ORDINAL_NAMES = {
    "116": "One Hundred Sixteenth",
    "117": "One Hundred Seventeenth",
    "118": "One Hundred Eighteenth",
    "119": "One Hundred Nineteenth",
}
BILLSTATUS_PACKAGE_RE = re.compile(
    r"(?:^|/)(?P<package>BILLSTATUS-(?P<congress>\d+)"
    r"(?P<measure_type>[a-z]+)(?P<number>\d+))(?:/|\.|$)"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class CollectionError(RuntimeError):
    """Raised when evidence cannot be accepted without ambiguity."""


@dataclass(frozen=True)
class FetchResponse:
    body: bytes
    status: int
    final_url: str
    headers: Mapping[str, str]


Fetcher = Callable[[str], FetchResponse]


@dataclass(frozen=True)
class SourceRecord:
    canonical_key: str
    record: Mapping[str, Any]
    parsed_path: str
    parsed_sha256: str
    source_url: str
    source_package: str
    source_sha256: str


@dataclass(frozen=True)
class Plan:
    records: tuple[SourceRecord, ...]
    jobs: tuple[Mapping[str, Any], ...]
    missing: tuple[Mapping[str, Any], ...]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CollectionError(message)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def immutable_write(path: Path, body: bytes) -> bool:
    """Create exclusively; accept identical prior bytes, never overwrite."""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o644,
        )
    except FileExistsError:
        require(path.read_bytes() == body, f"immutable content drift: {path}")
        return False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    return True


def immutable_json(path: Path, value: Any) -> bool:
    return immutable_write(path, json_bytes(value))


def official_govinfo_url(url: str) -> Any:
    parsed = urlparse(url)
    require(
        parsed.scheme == "https"
        and parsed.hostname == "www.govinfo.gov"
        and parsed.username is None
        and parsed.password is None
        and parsed.port in (None, 443)
        and not parsed.query
        and not parsed.fragment,
        f"non-canonical official URL refused: {url}",
    )
    return parsed


def parse_key(canonical_key: str) -> Mapping[str, str]:
    match = KEY_RE.fullmatch(canonical_key)
    require(match is not None, f"invalid canonical_key: {canonical_key!r}")
    assert match is not None
    return match.groupdict()


def record_key(record: Mapping[str, Any]) -> str:
    value = record.get("canonical_key")
    require(isinstance(value, str), "parsed record missing canonical_key")
    canonical_key = value.strip().lower()
    parse_key(canonical_key)
    return canonical_key


def source_provenance(
    record: Mapping[str, Any], canonical_key: str
) -> tuple[str, str, str]:
    provenance = record.get("provenance")
    require(
        isinstance(provenance, Mapping),
        f"{canonical_key}: missing provenance",
    )
    assert isinstance(provenance, Mapping)
    source_url = provenance.get("source_url")
    source_sha256 = provenance.get("source_sha256")
    require(
        isinstance(source_url, str) and bool(source_url.strip()),
        f"{canonical_key}: missing provenance.source_url",
    )
    require(
        isinstance(source_sha256, str)
        and SHA256_RE.fullmatch(source_sha256.strip().lower()) is not None,
        f"{canonical_key}: invalid provenance.source_sha256",
    )
    assert isinstance(source_url, str) and isinstance(source_sha256, str)
    official_govinfo_url(source_url)
    package_match = BILLSTATUS_PACKAGE_RE.search(urlparse(source_url).path)
    require(
        package_match is not None,
        f"{canonical_key}: source is not a BillStatus package",
    )
    assert package_match is not None
    identity = parse_key(canonical_key)
    require(
        all(
            package_match.group(field).lower() == identity[field].lower()
            for field in ("congress", "measure_type", "number")
        ),
        f"{canonical_key}: BillStatus source-package identity mismatch",
    )
    return (
        source_url,
        package_match.group("package"),
        source_sha256.strip().lower(),
    )


def input_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        return (path,)
    require(path.is_dir(), f"input not found: {path}")
    return sorted(path.rglob("*.json"))


def parsed_records(path: Path) -> Iterator[tuple[Path, Mapping[str, Any], bytes]]:
    for candidate in input_files(path):
        raw = candidate.read_bytes()
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if (
            isinstance(value, Mapping)
            and "canonical_key" in value
            and "provenance" in value
            and "text_versions" in value
        ):
            yield candidate, value, raw


def version_urls(version: Mapping[str, Any]) -> tuple[str, ...]:
    values = version.get("urls")
    if values is None:
        return ()
    if isinstance(values, str):
        values = [values]
    require(
        isinstance(values, Sequence)
        and not isinstance(values, (bytes, bytearray)),
        "text_versions.urls must be a list",
    )
    urls: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            value = value.get("url")
        require(
            isinstance(value, str) and bool(value.strip()),
            "blank/non-string text version URL",
        )
        urls.append(value.strip())
    return tuple(urls)


def make_job(
    source: SourceRecord,
    version: Mapping[str, Any],
    version_index: int,
    url_index: int,
    url: str,
) -> Mapping[str, Any]:
    parsed_url = official_govinfo_url(url)
    identity = parse_key(source.canonical_key)
    bills_match = BILLS_URL_RE.fullmatch(parsed_url.path)
    law_match = PLAW_URL_RE.fullmatch(parsed_url.path)
    require(
        bills_match is not None or law_match is not None,
        f"{source.canonical_key}: non-canonical GovInfo XML/USLM URL: {url}",
    )
    expected_originating_bill_ref = None
    law_type = None
    law_number = None
    if bills_match is not None:
        require(
            all(
                bills_match.group(field).lower() == identity[field].lower()
                for field in ("congress", "measure_type", "number")
            ),
            f"{source.canonical_key}: version URL identity mismatch: {url}",
        )
        package = bills_match.group("package")
        version_code = bills_match.group("version")
        document_format = bills_match.group("format")
        surface = "BILLS"
    else:
        assert law_match is not None
        require(
            law_match.group("congress") == identity["congress"],
            f"{source.canonical_key}: public-law Congress mismatch: {url}",
        )
        version_type = str(version.get("type") or "").strip().lower()
        expected_type = "public law" if law_match.group("law_type") == "publ" else "private law"
        require(
            version_type == expected_type,
            f"{source.canonical_key}: public/private-law version type mismatch: {version.get('type')!r}",
        )
        package = law_match.group("package")
        law_type = law_match.group("law_type")
        law_number = law_match.group("law_number")
        version_code = law_type + law_number
        document_format = law_match.group("format")
        surface = "PLAW"
        expected_originating_bill_ref = (
            f"/us/bill/{identity['congress']}/{identity['measure_type']}/{identity['number']}"
        )
    binding = {
        "schema": SCHEMA,
        "canonical_key": source.canonical_key,
        "version_index": version_index,
        "url_index": url_index,
        "version_date": version.get("date"),
        "version_type": version.get("type"),
        "version_code": version_code,
        "url": url,
        "govinfo_package": package,
        "govinfo_surface": surface,
        "document_format": document_format,
        "law_type": law_type,
        "law_number": law_number,
        "expected_originating_bill_ref": expected_originating_bill_ref,
        "billstatus_source_url": source.source_url,
        "billstatus_source_package": source.source_package,
        "billstatus_source_sha256": source.source_sha256,
        "parsed_path": source.parsed_path,
        "parsed_sha256": source.parsed_sha256,
    }
    return {**binding, "job_id": sha256_bytes(json_bytes(binding))}


def build_plan(path: Path) -> Plan:
    sources: dict[str, SourceRecord] = {}
    for parsed_path, record, raw in parsed_records(path):
        canonical_key = record_key(record)
        require(
            canonical_key not in sources,
            f"duplicate parsed record: {canonical_key}",
        )
        source_url, source_package, source_sha256 = source_provenance(
            record, canonical_key
        )
        sources[canonical_key] = SourceRecord(
            canonical_key=canonical_key,
            record=record,
            parsed_path=str(parsed_path.resolve()),
            parsed_sha256=sha256_bytes(raw),
            source_url=source_url,
            source_package=source_package,
            source_sha256=source_sha256,
        )
    require(bool(sources), f"no parsed BillStatus records under {path}")

    jobs: list[Mapping[str, Any]] = []
    missing: list[Mapping[str, Any]] = []
    seen_urls: dict[str, str] = {}
    seen_ids: set[str] = set()
    for canonical_key in sorted(sources):
        source = sources[canonical_key]
        versions = source.record.get("text_versions")
        require(
            isinstance(versions, Sequence)
            and not isinstance(versions, (str, bytes, bytearray)),
            f"{canonical_key}: text_versions must be a list",
        )
        found = 0
        for version_index, version in enumerate(versions):
            require(
                isinstance(version, Mapping),
                f"{canonical_key}: invalid text_versions[{version_index}]",
            )
            assert isinstance(version, Mapping)
            for url_index, url in enumerate(version_urls(version)):
                job = make_job(
                    source, version, version_index, url_index, url
                )
                require(
                    url not in seen_urls,
                    f"duplicate version URL for {seen_urls.get(url)} and "
                    f"{canonical_key}: {url}",
                )
                require(
                    job["job_id"] not in seen_ids,
                    f"duplicate job_id: {job['job_id']}",
                )
                seen_urls[url] = canonical_key
                seen_ids.add(str(job["job_id"]))
                jobs.append(job)
                found += 1
        if found == 0:
            missing.append(
                {
                    "schema": SCHEMA,
                    "kind": "missing-official-version-url",
                    "canonical_key": canonical_key,
                    "reason": (
                        "no_text_versions"
                        if len(versions) == 0
                        else "text_versions_have_no_url"
                    ),
                    "text_version_count": len(versions),
                    "billstatus_source_url": source.source_url,
                    "billstatus_source_package": source.source_package,
                    "billstatus_source_sha256": source.source_sha256,
                    "parsed_path": source.parsed_path,
                    "parsed_sha256": source.parsed_sha256,
                }
            )
    jobs.sort(
        key=lambda row: (
            row["canonical_key"],
            row["version_index"],
            row["url_index"],
            row["url"],
        )
    )
    return Plan(
        records=tuple(sources[key] for key in sorted(sources)),
        jobs=tuple(jobs),
        missing=tuple(missing),
    )


def assert_corpus(plan: Plan, *, enforce_expected: bool = True) -> None:
    if not enforce_expected:
        return
    missing_keys = {str(row["canonical_key"]) for row in plan.missing}
    require(
        len(plan.records) == EXPECTED_RECORDS,
        f"record count drift: {len(plan.records)} != {EXPECTED_RECORDS}",
    )
    require(
        len(plan.jobs) == EXPECTED_JOBS,
        f"version URL count drift: {len(plan.jobs)} != {EXPECTED_JOBS}",
    )
    require(
        missing_keys == EXPECTED_MISSING_KEYS,
        "missing-URL exception drift: "
        f"missing={sorted(missing_keys - EXPECTED_MISSING_KEYS)}, "
        f"unexpectedly_resolved={sorted(EXPECTED_MISSING_KEYS - missing_keys)}",
    )


def local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower()


def normalized_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def congress_identity_matches(value: str, expected: str) -> bool:
    numeric = re.compile(
        rf"(?<!\d){re.escape(expected)}(?:st|nd|rd|th)?(?!\d)",
        re.IGNORECASE,
    )
    if numeric.search(value):
        return True
    ordinal = CONGRESS_ORDINAL_NAMES.get(expected)
    return bool(
        ordinal
        and normalized_label(ordinal) in normalized_label(value)
    )


def validate_xml(raw: bytes, job: Mapping[str, Any]) -> Mapping[str, Any]:
    require(64 <= len(raw) <= MAX_XML_BYTES, "implausible XML byte length")
    upper = raw.upper()
    require(b"<HTML" not in upper[:4096], "HTML/challenge body refused")
    require(b"<!ENTITY" not in upper, "ENTITY is forbidden")
    sanitized = raw
    if b"<!DOCTYPE" in upper:
        require(upper.count(b"<!DOCTYPE") == 1, "multiple DOCTYPE declarations refused")
        doctype = LEGACY_CONGRESS_DOCTYPE_RE.search(raw[:16384])
        require(doctype is not None, "unapproved DOCTYPE refused")
        assert doctype is not None
        sanitized = raw[: doctype.start()] + raw[doctype.end() :]
    try:
        root = ET.fromstring(sanitized)
    except ET.ParseError as error:
        raise CollectionError(f"malformed XML: {error}") from error
    require(local_name(root.tag) != "html", "HTML root refused")

    congress_names = {"congress", "congress-number", "congressnumber"}
    measure_names = {
        "legis-num",
        "legisnum",
        "legislation-number",
        "measure-number",
        "citableas",
    }
    public_private_names = {"publicprivate", "public-private", "lawtype"}
    law_number_names = {"docnumber", "lawnumber", "law-number"}
    congress_values: list[str] = []
    measure_values: list[str] = []
    public_private_values: list[str] = []
    law_number_values: list[str] = []
    href_values: list[str] = []
    for element in root.iter():
        name = local_name(element.tag)
        text = " ".join(
            piece.strip() for piece in element.itertext() if piece.strip()
        )
        if name in congress_names and text:
            congress_values.append(text)
        if name in measure_names and text:
            measure_values.append(text)
        if name in public_private_names and text:
            public_private_values.append(text)
        if name in law_number_names and text:
            law_number_values.append(text)
        for attribute, value in element.attrib.items():
            attribute_name = local_name(attribute)
            if attribute_name in congress_names:
                congress_values.append(value)
            if attribute_name in measure_names:
                measure_values.append(value)
            if attribute_name in public_private_names:
                public_private_values.append(value)
            if attribute_name in law_number_names:
                law_number_values.append(value)
            if attribute_name == "href":
                href_values.append(value)

    identity = parse_key(str(job["canonical_key"]))
    require(
        any(
            congress_identity_matches(value, identity["congress"])
            for value in congress_values
        ),
        f"XML Congress identity mismatch: observed={congress_values[:5]}",
    )
    surface = str(job.get("govinfo_surface") or "BILLS")
    if surface == "PLAW":
        expected_law_type = "public" if job.get("law_type") == "publ" else "private"
        expected_law_number = str(job.get("law_number") or "")
        expected_ref = str(job.get("expected_originating_bill_ref") or "").lower()
        require(
            any(expected_law_type in value.lower() for value in public_private_values),
            f"XML public/private-law identity mismatch: observed={public_private_values[:5]}",
        )
        number_pattern = re.compile(rf"(?<!\d){re.escape(expected_law_number)}(?!\d)")
        require(
            expected_law_number
            and any(number_pattern.search(value) for value in law_number_values),
            f"XML law-number identity mismatch: observed={law_number_values[:5]}",
        )
        require(
            expected_ref and expected_ref in {value.lower() for value in href_values},
            f"XML originating-bill reference mismatch: expected={expected_ref!r}",
        )
        return {
            "xml_root": local_name(root.tag),
            "surface": surface,
            "matched_congress": identity["congress"],
            "matched_law_type": expected_law_type,
            "matched_law_number": expected_law_number,
            "matched_originating_bill_ref": expected_ref,
            "observed_congress_fields": congress_values[:10],
            "observed_public_private_fields": public_private_values[:10],
            "observed_law_number_fields": law_number_values[:10],
        }

    require(surface == "BILLS", f"unsupported GovInfo surface: {surface}")

    # Only fills the gap: an amendment document that does carry a measure
    # element is still validated by the ordinary <legis-num> path below.
    if local_name(root.tag) == "amendment-doc" and not measure_values:
        title_text = ""
        for element in root.iter():
            if element.tag == DC_TITLE_TAG:
                title_text = " ".join(
                    piece.strip()
                    for piece in element.itertext()
                    if piece.strip()
                )
                break
        require(
            bool(title_text),
            "amendment-doc is missing its Dublin Core title identity",
        )
        title_match = AMENDMENT_DC_TITLE_RE.match(title_text)
        require(
            title_match is not None,
            f"amendment-doc title identity unparsable: {title_text[:80]!r}",
        )
        assert title_match is not None
        version_code = str(job.get("version_code") or "")
        require(
            bool(version_code),
            "amendment-doc identity requires a planned version code",
        )
        expected_identity = normalized_label(
            identity["congress"]
            + identity["measure_type"]
            + identity["number"]
            + version_code
        )
        observed_identity = normalized_label(title_match.group("identity"))
        require(
            observed_identity == expected_identity,
            "amendment-doc identity mismatch: "
            f"observed={observed_identity!r} expected={expected_identity!r}",
        )
        return {
            "xml_root": local_name(root.tag),
            "surface": surface,
            "matched_congress": identity["congress"],
            "matched_measure": normalized_label(
                identity["measure_type"] + identity["number"]
            ),
            "matched_amendment_identity": observed_identity,
            "identity_source": "dublin_core_title",
            "observed_congress_fields": congress_values[:10],
            "observed_measure_fields": measure_values[:10],
        }

    expected_measure = normalized_label(
        identity["measure_type"] + identity["number"]
    )
    require(
        any(normalized_label(value) == expected_measure for value in measure_values),
        f"XML measure identity mismatch: observed={measure_values[:5]}",
    )
    return {
        "xml_root": local_name(root.tag),
        "surface": surface,
        "matched_congress": identity["congress"],
        "matched_measure": expected_measure,
        "observed_congress_fields": congress_values[:10],
        "observed_measure_fields": measure_values[:10],
    }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectionError(f"redirect refused: {req.full_url} -> {newurl}")


def network_fetcher(timeout_seconds: float) -> Fetcher:
    context = ssl.create_default_context()
    opener = urllib.request.build_opener(
        _NoRedirect(), urllib.request.HTTPSHandler(context=context)
    )

    def fetch(url: str) -> FetchResponse:
        official_govinfo_url(url)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/xml,text/xml;q=0.9",
                "User-Agent": (
                    "MelangeSystems-GovInfo-Version-Evidence/1.0 "
                    "(official static XML collection)"
                ),
            },
        )
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                status = int(getattr(response, "status", 0))
                final_url = response.geturl()
                headers = {
                    key.casefold(): value for key, value in response.headers.items()
                }
                body = response.read(MAX_XML_BYTES + 1)
        except urllib.error.URLError as error:
            raise CollectionError(f"official fetch failed: {url}: {error}") from error
        require(status == 200, f"unexpected HTTP status {status}: {url}")
        require(final_url == url, f"final URL drift: {url} -> {final_url}")
        official_govinfo_url(final_url)
        require(len(body) <= MAX_XML_BYTES, f"oversize XML: {url}")
        content_type = headers.get("content-type", "").casefold()
        require("xml" in content_type, f"non-XML Content-Type: {content_type!r}")
        return FetchResponse(body, status, final_url, headers)

    return fetch


def accept_job(
    job: Mapping[str, Any],
    objects_root: Path,
    fetch: Fetcher,
) -> Mapping[str, Any]:
    response = fetch(str(job["url"]))  # exactly one call; no retry wrapper
    require(response.final_url == job["url"], "fetcher final URL mismatch")
    identity = validate_xml(response.body, job)
    content_sha256 = sha256_bytes(response.body)
    object_path = (
        objects_root
        / "sha256"
        / content_sha256[:2]
        / f"{content_sha256}.xml"
    )
    object_created = immutable_write(object_path, response.body)
    return {
        "schema": SCHEMA,
        "kind": "accepted",
        "job_id": job["job_id"],
        "canonical_key": job["canonical_key"],
        "version_index": job["version_index"],
        "url_index": job["url_index"],
        "version_date": job["version_date"],
        "version_type": job["version_type"],
        "version_code": job["version_code"],
        "url": job["url"],
        "final_url": response.final_url,
        "govinfo_package": job["govinfo_package"],
        "billstatus_source_package": job["billstatus_source_package"],
        "billstatus_source_sha256": job["billstatus_source_sha256"],
        "parsed_sha256": job["parsed_sha256"],
        "http_status": response.status,
        "content_type": response.headers.get("content-type"),
        "content_bytes": len(response.body),
        "content_sha256": content_sha256,
        "object_path": str(object_path.resolve()),
        "object_created": object_created,
        "identity_validation": identity,
        "attempts": 1,
        "retry_waits": 0,
        "accepted_at": utc_now(),
    }


def failure_receipt(
    job: Mapping[str, Any], error: BaseException
) -> Mapping[str, Any]:
    return {
        "schema": SCHEMA,
        "kind": "failure",
        "job_id": job["job_id"],
        "canonical_key": job["canonical_key"],
        "version_index": job["version_index"],
        "url_index": job["url_index"],
        "url": job["url"],
        "govinfo_package": job["govinfo_package"],
        "billstatus_source_package": job["billstatus_source_package"],
        "billstatus_source_sha256": job["billstatus_source_sha256"],
        "parsed_sha256": job["parsed_sha256"],
        "error_type": type(error).__name__,
        "error": str(error),
        "attempts": 1,
        "retry_waits": 0,
        "failed_at": utc_now(),
    }


def plan_sha256(plan: Plan) -> str:
    return sha256_bytes(
        json_bytes(
            {
                "record_count": len(plan.records),
                "job_ids": [row["job_id"] for row in plan.jobs],
                "missing": list(plan.missing),
            }
        )
    )


def select_jobs(
    jobs: Sequence[Mapping[str, Any]], mode: str, canary: int | None
) -> tuple[Mapping[str, Any], ...]:
    if mode == "plan":
        return ()
    if mode == "full":
        return tuple(jobs)
    require(mode == "canary" and canary is not None, "invalid collection mode")
    assert canary is not None
    require(0 < canary <= len(jobs), "invalid canary size")
    if canary == 1:
        return (jobs[0],)
    positions = {
        round(index * (len(jobs) - 1) / (canary - 1))
        for index in range(canary)
    }
    require(len(positions) == canary, "canary selection collision")
    return tuple(jobs[position] for position in sorted(positions))


def run(args: argparse.Namespace, *, fetch: Fetcher | None = None) -> Mapping[str, Any]:
    require(RUN_ID_RE.fullmatch(args.run_id) is not None, "invalid --run-id")
    require(args.workers > 0, "--workers must be positive")
    require(args.timeout_seconds > 0, "--timeout-seconds must be positive")
    plan = build_plan(Path(args.input).resolve())
    assert_corpus(plan, enforce_expected=not args.allow_test_corpus)
    selected = select_jobs(plan.jobs, args.mode, args.canary)

    output_root = Path(args.out).resolve()
    run_root = output_root / "runs" / args.run_id
    try:
        run_root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise CollectionError(
            f"immutable run already exists; choose a new --run-id: {run_root}"
        ) from error

    immutable_write(
        run_root / "all_jobs.ndjson",
        b"".join(json_bytes(row) for row in plan.jobs),
    )
    immutable_write(
        run_root / "selected_jobs.ndjson",
        b"".join(json_bytes(row) for row in selected),
    )
    immutable_write(
        run_root / "missing_url_exceptions.ndjson",
        b"".join(json_bytes(row) for row in plan.missing),
    )
    plan_receipt = {
        "schema": SCHEMA,
        "kind": "plan",
        "run_id": args.run_id,
        "created_at": utc_now(),
        "mode": args.mode,
        "input": str(Path(args.input).resolve()),
        "output": str(output_root),
        "plan_sha256": plan_sha256(plan),
        "record_count": len(plan.records),
        "job_count": len(plan.jobs),
        "selected_count": len(selected),
        "missing_url_count": len(plan.missing),
        "missing_url_keys": sorted(
            str(row["canonical_key"]) for row in plan.missing
        ),
        "workers": args.workers,
        "attempts_per_job": 1,
        "retry_waits": 0,
        "browser_used": False,
        "cookies_used": False,
        "cloudflare_instrument_used": False,
    }
    immutable_json(run_root / "plan.json", plan_receipt)
    if args.mode == "plan":
        completed = {
            **plan_receipt,
            "kind": "completed",
            "status": "planned",
            "accepted_count": 0,
            "failure_count": 0,
            "completed_at": utc_now(),
        }
        immutable_json(run_root / "completed.json", completed)
        return completed

    dispatch_root = run_root / "receipts" / "dispatch"
    accepted_root = run_root / "receipts" / "accepted"
    failure_root = run_root / "receipts" / "failure"
    for job in selected:
        immutable_json(
            dispatch_root / f"{job['job_id']}.json",
            {
                "schema": SCHEMA,
                "kind": "dispatch",
                "run_id": args.run_id,
                **job,
            },
        )

    actual_fetch = fetch or network_fetcher(args.timeout_seconds)
    counters = {"accepted": 0, "failure": 0}
    lock = threading.Lock()

    def worker(job: Mapping[str, Any]) -> None:
        try:
            receipt = accept_job(job, output_root / "objects", actual_fetch)
            immutable_json(
                accepted_root / f"{job['job_id']}.json", receipt
            )
            outcome = "accepted"
        except BaseException as error:
            receipt = failure_receipt(job, error)
            immutable_json(
                failure_root / f"{job['job_id']}.json", receipt
            )
            outcome = "failure"
        with lock:
            counters[outcome] += 1
            done = counters["accepted"] + counters["failure"]
            print(
                f"[{done}/{len(selected)}] {outcome} "
                f"{job['canonical_key']} {job['version_code']}",
                flush=True,
            )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=args.workers,
        thread_name_prefix="govinfo-xml",
    ) as executor:
        futures = [executor.submit(worker, job) for job in selected]
        for future in concurrent.futures.as_completed(futures):
            future.result()

    dispatch_count = sum(1 for _ in dispatch_root.glob("*.json"))
    accepted_count = sum(1 for _ in accepted_root.glob("*.json"))
    failure_count = sum(1 for _ in failure_root.glob("*.json"))
    require(dispatch_count == len(selected), "dispatch receipt mismatch")
    require(accepted_count == counters["accepted"], "accepted receipt mismatch")
    require(failure_count == counters["failure"], "failure receipt mismatch")
    require(
        accepted_count + failure_count == dispatch_count,
        "terminal receipt reconciliation mismatch",
    )
    completed = {
        **plan_receipt,
        "kind": "completed",
        "status": (
            "accepted" if failure_count == 0 else "failures-quarantined"
        ),
        "dispatch_count": dispatch_count,
        "accepted_count": accepted_count,
        "failure_count": failure_count,
        "completed_at": utc_now(),
    }
    immutable_json(run_root / "completed.json", completed)
    return completed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--run-id", required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--plan-only", dest="mode", action="store_const", const="plan"
    )
    modes.add_argument("--canary", type=int, metavar="N")
    modes.add_argument(
        "--full", dest="mode", action="store_const", const="full"
    )
    parser.set_defaults(mode=None)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    parser.add_argument(
        "--allow-test-corpus",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    if args.canary is not None:
        args.mode = "canary"
    return args


def main(argv: Sequence[str] | None = None) -> int:
    try:
        completed = run(parse_args(argv))
    except (CollectionError, OSError, ValueError) as error:
        print(f"FAIL CLOSED: {error}", file=sys.stderr)
        return 2
    print(json.dumps(completed, sort_keys=True))
    return 0 if completed["failure_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
