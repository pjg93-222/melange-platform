#!/usr/bin/env python3
"""Official, pending-only discovery fallback for Congress.gov verification stops.

The primary discovery instrument remains Congress.gov Advanced Search.  When
that collector stops at its automatic security-verification page, this module
uses the unauthenticated GovInfo BILLSTATUS sitemaps and the BILLS XML links
declared by those records to find *new measure identifiers* since the last
good Advanced Search checkpoint.  It never attempts to solve or bypass the
verification page, and it never ratifies, scores, or promotes a candidate.

The fallback is deliberately cumulative.  Every pre-existing discovery row
and membership edge is carried forward unchanged; only previously unseen,
registry-matching measure IDs are appended as unreviewed candidates.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import ssl
import tempfile
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import os as _bootstrap_os, sys as _bootstrap_sys
_bootstrap_sys.path.insert(
    0,
    _bootstrap_os.path.dirname(
        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))
    ),
)

from legislation import stage_billstatus_bulk_enrichment as BILLSTATUS
from paths import ROOT as ROOT_TEXT


ROOT = Path(ROOT_TEXT)
DEFAULT_REGISTRY = (
    ROOT / "source_data" / "legislation" / "acu_legislation_search_registry.json"
)
DEFAULT_BASELINE = (
    ROOT / "audits" / "legfix5" / "congress_advanced_pages_20260725"
)
DEFAULT_OUT = ROOT / "data" / "legislation" / "congress_advanced_current"
MANIFEST_SCHEMA = "acu-govinfo-discovery-fallback-manifest-v1"
RECEIPT_SCHEMA = "acu-govinfo-discovery-fallback-receipt-v1"
CACHE_SCHEMA = "acu-govinfo-official-xml-cache-v1"
OFFICIAL_HOST = "www.govinfo.gov"
MAX_SITEMAP_BYTES = 64 * 1024 * 1024
MAX_BILL_TEXT_BYTES = 64 * 1024 * 1024
SUPPORTED_TYPES = BILLSTATUS.TOP_LEVEL_TYPES
TYPE_SLUG = {
    "hr": "house-bill",
    "hres": "house-resolution",
    "hjres": "house-joint-resolution",
    "hconres": "house-concurrent-resolution",
    "s": "senate-bill",
    "sres": "senate-resolution",
    "sjres": "senate-joint-resolution",
    "sconres": "senate-concurrent-resolution",
}
DISPLAY_PREFIX = {
    "hr": "H.R.",
    "hres": "H.Res.",
    "hjres": "H.J.Res.",
    "hconres": "H.Con.Res.",
    "s": "S.",
    "sres": "S.Res.",
    "sjres": "S.J.Res.",
    "sconres": "S.Con.Res.",
}
STATUS_URL_RE = re.compile(
    r"^https://www\.govinfo\.gov/bulkdata/BILLSTATUS/"
    r"(?P<congress>\d+)/(?P<type>hconres|hjres|hres|hr|sconres|sjres|sres|s)/"
    r"BILLSTATUS-(?P=congress)(?P=type)(?P<number>\d+)\.xml$",
    re.IGNORECASE,
)
BILLS_XML_URL_RE = re.compile(
    r"^https://www\.govinfo\.gov/content/pkg/BILLS-"
    r"(?P<congress>\d+)(?P<type>hconres|hjres|hres|hr|sconres|sjres|sres|s)"
    r"(?P<number>\d+)(?P<version>[a-z0-9-]+)/(?P<xml_kind>xml|uslm)/BILLS-"
    r"(?P=congress)(?P=type)(?P=number)(?P=version)\.xml$",
    re.IGNORECASE,
)
KNOWN_CITE_RE = re.compile(
    r"congressId\s*:\s*(?P<congress>\d+).*?cite\s*:\s*\"?"
    r"(?P<type>hconres|hjres|hres|hr|sconres|sjres|sres|s)"
    r"(?P<number>\d+)\"?",
    re.IGNORECASE,
)


class FallbackError(RuntimeError):
    """Raised when an official source or local checkpoint fails closed."""


Fetcher = Callable[[str, int], bytes]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def stable_hash(value: Any) -> str:
    return sha256_bytes(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def file_receipt(path: Path) -> dict[str, Any]:
    body = path.read_bytes()
    try:
        display_path = str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        display_path = str(path.resolve())
    return {
        "path": display_path,
        "bytes": len(body),
        "sha256": sha256_bytes(body),
    }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FallbackError(message)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FallbackError(f"invalid JSON artifact: {path}: {error}") from error


def _read_ndjson(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                _require(isinstance(row, dict), f"non-object row {path}:{line_number}")
                rows.append(row)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FallbackError(f"invalid NDJSON artifact: {path}: {error}") from error
    return rows


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json(path: Path, value: Any) -> None:
    _atomic_write(
        path,
        (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
    )


def _write_ndjson(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    body = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows
    )
    _atomic_write(path, body.encode("utf-8"))


def _official_url(url: str) -> None:
    parsed = urlparse(url)
    _require(
        parsed.scheme == "https"
        and parsed.hostname == OFFICIAL_HOST
        and parsed.username is None
        and parsed.password is None
        and parsed.port in (None, 443)
        and not parsed.query
        and not parsed.fragment,
        f"non-canonical official URL refused: {url}",
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise FallbackError(f"redirect refused: {req.full_url} -> {newurl}")


def network_fetcher(timeout_seconds: float) -> Fetcher:
    context = ssl.create_default_context()
    opener = urllib.request.build_opener(
        _NoRedirect(), urllib.request.HTTPSHandler(context=context)
    )

    def fetch(url: str, max_bytes: int) -> bytes:
        _official_url(url)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/xml,text/xml;q=0.9",
                "User-Agent": (
                    "MelangeSystems-GovInfo-Discovery-Fallback/1.0 "
                    "(official pending-only candidate audit)"
                ),
            },
        )
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                final_url = response.geturl()
                _official_url(final_url)
                _require(final_url == url, f"final URL drift: {url} -> {final_url}")
                _require(int(getattr(response, "status", 0)) == 200, f"HTTP failure: {url}")
                body = response.read(max_bytes + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise FallbackError(f"official fetch failed: {url}: {error}") from error
        _require(len(body) <= max_bytes, f"oversize official XML: {url}")
        return body

    return fetch


def parse_sitemap(
    raw: bytes, *, expected_url: str, congress: int, measure_type: str
) -> dict[str, str]:
    _require(len(raw) <= MAX_SITEMAP_BYTES, f"oversize sitemap: {expected_url}")
    upper = raw.upper()
    _require(b"<!DOCTYPE" not in upper, f"DOCTYPE forbidden in sitemap: {expected_url}")
    _require(b"<!ENTITY" not in upper, f"ENTITY forbidden in sitemap: {expected_url}")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise FallbackError(f"malformed sitemap: {expected_url}") from error
    _require(root.tag.endswith("urlset"), f"unexpected sitemap root: {expected_url}")
    records: dict[str, str] = {}
    for item in list(root):
        _require(item.tag.endswith("url"), f"unexpected sitemap row: {expected_url}")
        loc = next(
            ((node.text or "").strip() for node in item if node.tag.endswith("loc")),
            "",
        )
        lastmod = next(
            ((node.text or "").strip() for node in item if node.tag.endswith("lastmod")),
            "",
        )
        match = STATUS_URL_RE.fullmatch(loc)
        _require(match is not None, f"out-of-contract sitemap location: {loc}")
        assert match is not None
        _require(
            int(match.group("congress")) == congress
            and match.group("type").casefold() == measure_type.casefold(),
            f"sitemap identity drift: {loc}",
        )
        _parse_timestamp(lastmod, "sitemap.lastmod")
        _require(loc not in records, f"duplicate sitemap location: {loc}")
        records[loc] = lastmod
    _require(bool(records), f"empty sitemap: {expected_url}")
    return records


def _parse_timestamp(value: str, field: str) -> datetime:
    text = str(value or "").strip()
    _require(bool(text), f"missing {field}")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise FallbackError(f"invalid {field}: {text}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _cache_fetch(
    cache_dir: Path,
    *,
    url: str,
    version_token: str,
    suffix: str,
    max_bytes: int,
    fetch: Fetcher,
) -> tuple[Path, bytes, bool]:
    identity = sha256_bytes(f"{url}\n{version_token}".encode("utf-8"))
    raw_path = cache_dir / "raw" / f"{identity}{suffix}"
    receipt_path = cache_dir / "receipts" / f"{identity}.json"
    if raw_path.is_file() and receipt_path.is_file():
        receipt = _read_json(receipt_path)
        body = raw_path.read_bytes()
        _require(
            isinstance(receipt, dict)
            and receipt.get("schema") == CACHE_SCHEMA
            and receipt.get("url") == url
            and receipt.get("version_token") == version_token
            and receipt.get("bytes") == len(body)
            and receipt.get("sha256") == sha256_bytes(body),
            f"stale or tampered GovInfo cache entry: {url}",
        )
        return raw_path, body, True
    body = fetch(url, max_bytes)
    _require(len(body) <= max_bytes, f"oversize fetched XML: {url}")
    _atomic_write(raw_path, body)
    _write_json(
        receipt_path,
        {
            "schema": CACHE_SCHEMA,
            "url": url,
            "version_token": version_token,
            "bytes": len(body),
            "sha256": sha256_bytes(body),
        },
    )
    return raw_path, body, False


def _parse_bill_text(raw: bytes, *, url: str, canonical_key: str) -> str:
    _require(len(raw) <= MAX_BILL_TEXT_BYTES, f"oversize BILLS XML: {url}")
    upper = raw.upper()
    _require(b"<!ENTITY" not in upper, f"ENTITY forbidden in BILLS XML: {url}")
    _require(
        re.search(br"<!DOCTYPE[^>]*\[", raw, re.IGNORECASE) is None,
        f"internal DTD subset forbidden in BILLS XML: {url}",
    )
    match = BILLS_XML_URL_RE.fullmatch(url)
    _require(match is not None, f"non-canonical BILLS XML URL: {url}")
    assert match is not None
    url_key = BILLSTATUS.canonical_key(
        int(match.group("congress")), match.group("type"), int(match.group("number"))
    )
    _require(url_key == canonical_key, f"BILLS XML URL identity drift: {url}")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise FallbackError(f"malformed BILLS XML: {url}") from error
    _require(
        root.tag.split("}")[-1] in {"bill", "resolution", "amendment-doc"},
        f"bad BILLS root: {url}",
    )
    return " ".join(part.strip() for part in root.itertext() if part.strip())


def _normalize(value: str) -> str:
    folded = unicodedata.normalize("NFKC", str(value or "")).casefold()
    folded = re.sub(r"[^a-z0-9]+", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def _phrase_from_query(query: str) -> str | None:
    text = str(query or "").strip()
    if "congressid" in text.casefold() and "cite" in text.casefold():
        return None
    while len(text) >= 2 and text[0] == "(" and text[-1] == ")":
        text = text[1:-1].strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    normalized = _normalize(text)
    return normalized or None


def _known_cite(query: str) -> str | None:
    match = KNOWN_CITE_RE.search(str(query or ""))
    if not match:
        return None
    return BILLSTATUS.canonical_key(
        int(match.group("congress")), match.group("type"), int(match.group("number"))
    )


def _entry_matches(
    entry: dict[str, Any],
    *,
    canonical_key: str,
    evidence: list[tuple[str, str]],
    allow_known_cite: bool = True,
) -> tuple[list[str], list[str]]:
    normalized_evidence = [(source, f" {_normalize(text)} ") for source, text in evidence]
    matched_terms: set[str] = set()
    matched_sources: set[str] = set()
    for term in entry.get("query_terms") or []:
        _require(isinstance(term, dict), f"malformed query term: {entry.get('id')}")
        query = str(term.get("query") or "").strip()
        _require(bool(query), f"empty query term: {entry.get('id')}")
        cite = _known_cite(query)
        if cite:
            if allow_known_cite and cite == canonical_key:
                matched_terms.add(query)
                matched_sources.add("registry_known_bill_cite")
            continue
        phrase = _phrase_from_query(query)
        _require(bool(phrase), f"unsupported query term: {entry.get('id')}: {query}")
        needle = f" {phrase} "
        for source, haystack in normalized_evidence:
            if needle in haystack:
                matched_terms.add(query)
                matched_sources.add(source)
    return sorted(matched_terms), sorted(matched_sources)


def _status_evidence(parsed: dict[str, Any]) -> list[tuple[str, str]]:
    evidence: list[tuple[str, str]] = []
    for title in parsed.get("titles") or []:
        if title.get("title"):
            evidence.append(("billstatus_title", str(title["title"])))
    for summary in parsed.get("summaries") or []:
        if summary.get("text"):
            evidence.append(("billstatus_summary", str(summary["text"])))
    for action in parsed.get("actions") or []:
        if action.get("text"):
            evidence.append(("billstatus_action", str(action["text"])))
    return evidence


def _bill_text_urls(parsed: dict[str, Any]) -> tuple[list[str], list[str]]:
    urls: set[str] = set()
    ignored_official_xml: set[str] = set()
    canonical_key = str(parsed["canonical_key"])
    for version in parsed.get("text_versions") or []:
        for raw_url in version.get("urls") or []:
            url = str(raw_url).strip()
            if not url.casefold().endswith(".xml"):
                continue
            _official_url(url)
            match = BILLS_XML_URL_RE.fullmatch(url)
            if match is None and "/content/pkg/BILLS-" not in url:
                ignored_official_xml.add(url)
                continue
            _require(match is not None, f"malformed official BILLS XML URL: {url}")
            assert match is not None
            key = BILLSTATUS.canonical_key(
                int(match.group("congress")),
                match.group("type"),
                int(match.group("number")),
            )
            _require(key == canonical_key, f"bill-version identity drift: {url}")
            urls.add(url)
    return sorted(urls), sorted(ignored_official_xml)


def _direct_text(node: ET.Element, path: str) -> str | None:
    child = node.find(path)
    if child is None or child.text is None or not child.text.strip():
        return None
    return child.text.strip()


def _parse_nested_amendments(
    raw: bytes,
    *,
    parent: dict[str, Any],
    parent_status_url: str,
    parent_sitemap_lastmod: str,
) -> list[dict[str, Any]]:
    """Parse direct amendment objects without inheriting parent-bill text.

    Only the amendment's own direct ``purpose`` and ``description`` nodes are
    exposed as matching evidence.  In particular, the title under
    ``amendedBill`` and the parent measure's title/summary/text are excluded.
    """
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise FallbackError(f"malformed BILLSTATUS XML: {parent_status_url}") from error
    bill = root.find("bill")
    _require(bill is not None, f"missing bill element: {parent_status_url}")
    parent_key = str(parent["canonical_key"])
    amendments: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in bill.findall("./amendments/amendment"):
        congress_text = _direct_text(node, "congress")
        type_text = str(_direct_text(node, "type") or "").casefold()
        number_text = _direct_text(node, "number")
        _require(
            congress_text is not None
            and congress_text.isdigit()
            and number_text is not None
            and number_text.isdigit()
            and type_text in BILLSTATUS.NESTED_ONLY_TYPES,
            f"invalid nested amendment identity in {parent_status_url}",
        )
        key = BILLSTATUS.canonical_key(int(congress_text), type_text, int(number_text))
        _require(key not in seen, f"duplicate nested amendment in one status record: {key}")
        seen.add(key)
        _require(
            int(congress_text) == int(parent["identity"]["congress"]),
            f"nested amendment Congress drift: {key}",
        )
        amended_congress = _direct_text(node, "amendedBill/congress")
        amended_type = _direct_text(node, "amendedBill/type")
        amended_number = _direct_text(node, "amendedBill/number")
        if amended_congress and amended_type and amended_number:
            _require(
                amended_congress.isdigit() and amended_number.isdigit(),
                f"bad amended-bill identity: {key}",
            )
            amended_key = BILLSTATUS.canonical_key(
                int(amended_congress), amended_type, int(amended_number)
            )
            _require(amended_key == parent_key, f"nested amendment parent drift: {key}")
        update_date = _direct_text(node, "updateDate")
        _require(bool(update_date), f"nested amendment update date missing: {key}")
        _parse_timestamp(str(update_date), "amendment.updateDate")
        purpose = _direct_text(node, "purpose")
        description = _direct_text(node, "description")
        sponsors = []
        for item in node.findall("./sponsors/item"):
            sponsors.append(
                {
                    "bioguide_id": _direct_text(item, "bioguideId"),
                    "full_name": _direct_text(item, "fullName"),
                }
            )
        action_rows: list[dict[str, Any]] = []
        for item in node.findall("./actions/actions/item"):
            action_rows.append(
                {
                    "action_date": _direct_text(item, "actionDate"),
                    "text": _direct_text(item, "text"),
                }
            )
        latest_action = {
            "action_date": _direct_text(node, "latestAction/actionDate"),
            "text": _direct_text(node, "latestAction/text"),
        }
        if not latest_action["action_date"] and action_rows:
            latest_action = max(
                (row for row in action_rows if row.get("action_date")),
                key=lambda row: str(row["action_date"]),
                default=latest_action,
            )
        submitted = _direct_text(node, "proposedDate") or _direct_text(node, "submittedDate")
        submitted_iso = submitted[:10] if submitted else None
        amendments.append(
            {
                "canonical_key": key,
                "congress": int(congress_text),
                "type": type_text,
                "number": int(number_text),
                "update_date": update_date,
                "purpose": purpose,
                "description": description,
                "sponsors": sponsors,
                "cosponsor_count": len(node.findall("./cosponsors/item")),
                "submitted_date": submitted_iso,
                "latest_action": latest_action,
                "parent_key": parent_key,
                "parent_status_url": parent_status_url,
                "parent_sitemap_lastmod": parent_sitemap_lastmod,
                "evidence": [
                    ("amendment_direct_purpose", purpose),
                    ("amendment_direct_description", description),
                ],
            }
        )
    return amendments


def _ordinal(congress: int) -> str:
    if 10 <= congress % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(congress % 10, "th")
    return f"{congress}{suffix}"


def _date_display(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%m/%d/%Y")
    except ValueError:
        return None


def _select_title(parsed: dict[str, Any]) -> str | None:
    titles = [row for row in parsed.get("titles") or [] if row.get("title")]
    if not titles:
        return None
    preferences = ("official title as introduced", "short title as introduced")
    for preferred in preferences:
        for row in titles:
            if str(row.get("title_type") or "").casefold() == preferred:
                return str(row["title"])
    return str(titles[0]["title"])


def _window_status(value: str | None, contract: dict[str, Any]) -> str:
    if not value:
        return "date_unknown"
    start = str(contract.get("required_window_start") or "")
    end = str(contract.get("required_window_end") or "")
    if start and value < start:
        return "boundary_extension_before_window"
    if end and value > end:
        return "after_required_window"
    return "within_required_window"


def _build_record(
    parsed: dict[str, Any],
    *,
    category_ids: list[str],
    monitor_ids: list[str],
    probe_ids: list[str],
    match_receipts: list[dict[str, Any]],
    bill_text_receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    identity = parsed["identity"]
    congress = int(identity["congress"])
    measure_type = str(identity["type"])
    number = int(identity["number"])
    canonical_key = str(parsed["canonical_key"])
    slug = TYPE_SLUG[measure_type]
    display = f"{DISPLAY_PREFIX[measure_type]}{number}"
    canonical_url = f"https://www.congress.gov/bill/{_ordinal(congress)}-congress/{slug}/{number}"
    sponsors = parsed.get("sponsors") or []
    sponsor = sponsors[0] if sponsors else {}
    initial_dates = sorted(
        str(row.get("action_date"))
        for row in parsed.get("actions") or []
        if row.get("action_date")
    )
    initial_iso = initial_dates[0] if initial_dates else None
    latest = max(
        (row for row in parsed.get("actions") or [] if row.get("action_date")),
        key=lambda row: (str(row.get("action_date")), int(row.get("action_index") or 0)),
        default={},
    )
    title = _select_title(parsed)
    sponsor_name = sponsor.get("full_name")
    initial_display = _date_display(initial_iso)
    sponsor_line = (
        f"Sponsor: {sponsor_name} (Introduced {initial_display}) "
        f"Cosponsors: ({len(parsed.get('cosponsors') or [])})"
        if sponsor_name and initial_display
        else (f"Sponsor: {sponsor_name}" if sponsor_name else None)
    )
    latest_text = latest.get("text")
    latest_display = _date_display(latest.get("action_date"))
    concise_parts = [part for part in (display, title, sponsor_line, latest_text) if part]
    return {
        "record_kind": "bill",
        "congress": congress,
        "type": measure_type,
        "number": number,
        "canonical_key": canonical_key,
        "canonical_url": canonical_url,
        "display_number": display,
        "heading": (
            f"{display} — {_ordinal(congress)} Congress "
            f"({1789 + (congress - 1) * 2}-{1790 + (congress - 1) * 2})"
        ),
        "title": title,
        "purpose": None,
        "sponsor": sponsor_name,
        "sponsor_url": (
            f"https://www.congress.gov/member/{sponsor.get('bioguide_id')}"
            if sponsor.get("bioguide_id")
            else None
        ),
        "sponsor_line": sponsor_line,
        "initial_action": "Introduced",
        "initial_action_date": initial_display,
        "initial_action_date_iso": initial_iso,
        "window_status": _window_status(initial_iso, {}),
        "candidate_status": "included_unreviewed_candidate",
        "cosponsor_count": len(parsed.get("cosponsors") or []),
        "committees": None,
        "latest_action": (
            f"Latest Action: {latest_display} {latest_text}"
            if latest_display and latest_text
            else latest_text
        ),
        "latest_action_date": latest_display,
        "amends": None,
        "tracker_status": None,
        "result_url": canonical_url,
        "raw_text": " ".join(str(part) for part in concise_parts),
        "matched_scoring_categories": sorted(category_ids),
        "matched_monitor_categories": sorted(monitor_ids),
        "matched_coverage_probes": sorted(probe_ids),
        "fallback_discovery": {
            "transport": "official_govinfo_billstatus_and_bills_xml",
            "candidate_only": True,
            "ratified": False,
            "scoring_eligible": False,
            "billstatus": parsed["provenance"],
            "bill_text_versions": bill_text_receipts,
            "matches": match_receipts,
        },
    }


def _build_amendment_record(
    amendment: dict[str, Any],
    *,
    contract: dict[str, Any],
    category_ids: list[str],
    monitor_ids: list[str],
    probe_ids: list[str],
    match_receipts: list[dict[str, Any]],
    source_receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    congress = int(amendment["congress"])
    measure_type = str(amendment["type"])
    number = int(amendment["number"])
    key = str(amendment["canonical_key"])
    chamber_slug = "house-amendment" if measure_type == "hamdt" else "senate-amendment"
    display = f"{'H.Amdt.' if measure_type == 'hamdt' else 'S.Amdt.'}{number}"
    canonical_url = (
        f"https://www.congress.gov/amendment/{_ordinal(congress)}-congress/"
        f"{chamber_slug}/{number}"
    )
    sponsors = amendment.get("sponsors") or []
    sponsor = sponsors[0] if sponsors else {}
    submitted_iso = amendment.get("submitted_date")
    submitted_display = _date_display(submitted_iso)
    purpose = amendment.get("purpose")
    description = amendment.get("description")
    latest = amendment.get("latest_action") or {}
    latest_display = _date_display(latest.get("action_date"))
    parent_parts = str(amendment["parent_key"]).split(":")
    parent_display = f"{DISPLAY_PREFIX[parent_parts[1]]}{parent_parts[2]}"
    own_text = " ".join(
        str(value) for value in (purpose, description) if value
    )
    return {
        "record_kind": "amendment",
        "congress": congress,
        "type": measure_type,
        "number": number,
        "canonical_key": key,
        "canonical_url": canonical_url,
        "display_number": display,
        "heading": (
            f"{display} — {_ordinal(congress)} Congress "
            f"({1789 + (congress - 1) * 2}-{1790 + (congress - 1) * 2})"
        ),
        "title": None,
        "purpose": f"Purpose: {purpose}" if purpose else (
            f"Description: {description}" if description else None
        ),
        "sponsor": sponsor.get("full_name"),
        "sponsor_url": (
            f"https://www.congress.gov/member/{sponsor.get('bioguide_id')}"
            if sponsor.get("bioguide_id")
            else None
        ),
        "sponsor_line": (
            f"Sponsor: {sponsor.get('full_name')} (Offered {submitted_display})"
            if sponsor.get("full_name") and submitted_display
            else (f"Sponsor: {sponsor.get('full_name')}" if sponsor.get("full_name") else None)
        ),
        "initial_action": "Offered",
        "initial_action_date": submitted_display,
        "initial_action_date_iso": submitted_iso,
        "window_status": _window_status(submitted_iso, contract),
        "candidate_status": "included_unreviewed_candidate",
        "cosponsor_count": int(amendment.get("cosponsor_count") or 0),
        "committees": None,
        "latest_action": (
            f"Latest Action: {latest_display} {latest.get('text')}"
            if latest_display and latest.get("text")
            else latest.get("text")
        ),
        "latest_action_date": latest_display,
        "amends": f"Amends Bill: {parent_display}",
        "tracker_status": None,
        "result_url": canonical_url,
        "raw_text": own_text,
        "matched_scoring_categories": sorted(category_ids),
        "matched_monitor_categories": sorted(monitor_ids),
        "matched_coverage_probes": sorted(probe_ids),
        "fallback_discovery": {
            "transport": "official_govinfo_billstatus_nested_amendment",
            "candidate_only": True,
            "ratified": False,
            "scoring_eligible": False,
            "matching_evidence_scope": (
                "direct_amendment_purpose_and_description_only; "
                "parent_bill_title_summary_actions_and_text_excluded"
            ),
            "parent_billstatus_sources": source_receipts,
            "matches": match_receipts,
        },
    }


def _validate_existing_records(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("canonical_key") or "")
        _require(bool(key), "existing discovery row has no canonical key")
        _require(key not in by_key, f"duplicate existing discovery key: {key}")
        parts = key.split(":")
        _require(len(parts) == 3 and parts[0].isdigit() and parts[2].isdigit(), f"bad key: {key}")
        _require(
            int(row.get("congress")) == int(parts[0])
            and str(row.get("type")).casefold() == parts[1]
            and int(row.get("number")) == int(parts[2]),
            f"existing discovery identity drift: {key}",
        )
        by_key[key] = row
    return by_key


def _validate_memberships(rows: list[dict[str, Any]], valid_keys: set[str]) -> set[tuple[str, str]]:
    identities: set[tuple[str, str]] = set()
    for row in rows:
        identity = (str(row.get("query_id") or ""), str(row.get("canonical_key") or ""))
        _require(all(identity), "existing membership identity missing")
        _require(identity[1] in valid_keys, f"orphan existing membership: {identity}")
        _require(identity not in identities, f"duplicate existing membership: {identity}")
        identities.add(identity)
    return identities


def _load_cumulative_base(
    baseline_dir: Path, out_dir: Path, registry_sha256: str, selected_ids: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Path, dict[str, Any]]:
    baseline_manifest_path = baseline_dir / "manifest.json"
    baseline_manifest = _read_json(baseline_manifest_path)
    _require(baseline_manifest.get("status") == "complete", "baseline manifest is not complete")
    _require(
        baseline_manifest.get("registry_sha256") == registry_sha256,
        "baseline registry hash differs from current registry",
    )
    union = baseline_manifest.get("union") or {}
    records_path = baseline_dir / str(union.get("records_file") or "")
    memberships_path = baseline_dir / str(union.get("memberships_file") or "")
    _require(records_path.is_file() and memberships_path.is_file(), "baseline union files missing")
    baseline_records = _read_ndjson(records_path)
    baseline_memberships = _read_ndjson(memberships_path)
    _require(
        union.get("unique_record_count") == len(baseline_records)
        and union.get("records_sha256") == stable_hash(baseline_records),
        "baseline record receipt drift",
    )
    _require(
        union.get("membership_count") == len(baseline_memberships)
        and union.get("memberships_sha256") == stable_hash(baseline_memberships),
        "baseline membership receipt drift",
    )

    current_manifest_path = out_dir / "manifest.json"
    if current_manifest_path.is_file():
        current = _read_json(current_manifest_path)
        if (
            current.get("schema") == MANIFEST_SCHEMA
            and current.get("status") == "complete"
            and (current.get("registry") or {}).get("sha256") == registry_sha256
            and (current.get("immutable_baseline") or {}).get("manifest", {}).get("sha256")
            == file_receipt(baseline_manifest_path)["sha256"]
        ):
            # Records a prior run added were matched only against its own selection;
            # reusing them under a wider selection would silently skip the new entries.
            _require(
                selected_ids.issubset(set(current.get("selected_query_ids") or [])),
                "prior fallback selection does not cover the current selection; "
                "rerun with a covering selection or rebuild the fallback output",
            )
            current_union = current.get("union") or {}
            current_records_path = out_dir / str(current_union.get("records_file") or "")
            current_memberships_path = out_dir / str(current_union.get("memberships_file") or "")
            _require(
                current_records_path.is_file() and current_memberships_path.is_file(),
                "prior fallback union files missing",
            )
            current_records = _read_ndjson(current_records_path)
            current_memberships = _read_ndjson(current_memberships_path)
            _require(
                current_union.get("unique_record_count") == len(current_records)
                and current_union.get("records_sha256") == stable_hash(current_records),
                "prior fallback record receipt drift",
            )
            _require(
                current_union.get("membership_count") == len(current_memberships)
                and current_union.get("memberships_sha256") == stable_hash(current_memberships),
                "prior fallback membership receipt drift",
            )
            base_map = _validate_existing_records(baseline_records)
            current_map = _validate_existing_records(current_records)
            _require(set(base_map).issubset(current_map), "prior fallback lost baseline records")
            _require(
                all(current_map[key] == row for key, row in base_map.items()),
                "prior fallback mutated a baseline record",
            )
            return current_records, current_memberships, baseline_manifest_path, baseline_manifest
    return baseline_records, baseline_memberships, baseline_manifest_path, baseline_manifest


def _counts(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row[field]) for row in rows).items()))


def _prepare_support_files(
    *,
    baseline_dir: Path,
    out_dir: Path,
    new_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Read and validate every support input so nothing is written before they all load."""
    baseline_gaps_path = baseline_dir / "coverage_gap_ledger.ndjson"
    prior_gaps_path = out_dir / "coverage_gap_ledger.ndjson"
    gaps = _read_ndjson(prior_gaps_path if prior_gaps_path.is_file() else baseline_gaps_path)
    gap_keys = {(row.get("gap_kind"), row.get("canonical_key")) for row in gaps}
    for row in new_records:
        if (
            row.get("matched_coverage_probes")
            and not row.get("matched_scoring_categories")
            and not row.get("matched_monitor_categories")
            and ("recall_only_unclassified", row["canonical_key"]) not in gap_keys
        ):
            gaps.append(
                {
                    "gap_kind": "recall_only_unclassified",
                    "canonical_key": row["canonical_key"],
                    "display_number": row.get("display_number"),
                    "title": row.get("title"),
                    "result_url": row.get("result_url"),
                    "disposition": "included_unreviewed_candidate",
                    "discovery_transport": "official_govinfo_fallback",
                }
            )
    exclusion_path = out_dir / "exclusion_ledger.ndjson"
    exclusion_body = None
    if not exclusion_path.is_file():
        try:
            exclusion_body = (baseline_dir / "exclusion_ledger.ndjson").read_bytes()
        except OSError as error:
            raise FallbackError(f"baseline exclusion ledger unreadable: {error}") from error
    scope = _read_json(baseline_dir / "scope_and_exclusion_contract.json")
    scope["govinfo_security_verification_fallback"] = {
        "purpose": "Fresh bill-ID discovery while Congress.gov Advanced Search is unavailable",
        "candidate_disposition": "pending_human_review_only",
        "semantic_exclusion_automatic": False,
        "ratification_automatic": False,
        "scoring_automatic": False,
    }
    return {"gaps": gaps, "exclusion_body": exclusion_body, "scope": scope}


def _copy_or_extend_support_files(*, out_dir: Path, prepared: dict[str, Any]) -> dict[str, Any]:
    gaps_path = out_dir / "coverage_gap_ledger.ndjson"
    _write_ndjson(gaps_path, prepared["gaps"])
    exclusion_path = out_dir / "exclusion_ledger.ndjson"
    if prepared["exclusion_body"] is not None:
        _atomic_write(exclusion_path, prepared["exclusion_body"])
    scope_path = out_dir / "scope_and_exclusion_contract.json"
    _write_json(scope_path, prepared["scope"])
    return {
        "coverage_gap_ledger": file_receipt(gaps_path),
        "exclusion_ledger": file_receipt(exclusion_path),
        "scope_contract": file_receipt(scope_path),
    }


def run_fallback(
    *,
    registry_path: Path,
    baseline_dir: Path,
    out_dir: Path,
    browser_failure_path: Path,
    workers: int,
    fetch: Fetcher,
    selected_categories: set[str] | None = None,
    changed_since: str | None = None,
) -> dict[str, Any]:
    registry_raw = registry_path.read_bytes()
    registry_sha256 = sha256_bytes(registry_raw)
    registry = _read_json(registry_path)
    entries = list(registry.get("categories") or []) + list(registry.get("coverage_probes") or [])
    if selected_categories is not None:
        entries = [entry for entry in entries if entry.get("id") in selected_categories]
        found = {str(entry.get("id")) for entry in entries}
        _require(found == selected_categories, f"unknown registry IDs: {sorted(selected_categories - found)}")
    _require(bool(entries), "registry has no selected entries")
    _require(
        all(isinstance(entry, dict) and entry.get("id") for entry in entries),
        "registry entries are malformed",
    )
    entry_ids = [str(entry["id"]) for entry in entries]
    _require(len(entry_ids) == len(set(entry_ids)), "duplicate registry entry ID")

    existing_records, existing_memberships, baseline_manifest_path, baseline_manifest = (
        _load_cumulative_base(baseline_dir, out_dir, registry_sha256, set(entry_ids))
    )
    existing_by_key = _validate_existing_records(existing_records)
    membership_ids = _validate_memberships(existing_memberships, set(existing_by_key))
    failure = _read_json(browser_failure_path)
    _require(
        (failure.get("error") or {}).get("code") == "CONGRESS_AUTOMATIC_VERIFICATION_TIMEOUT",
        "fallback requires a Congress automatic-verification timeout receipt",
    )

    contract = registry.get("collection_contract") or {}
    congresses = [int(value) for value in contract.get("congresses") or []]
    configured_types = [str(value).casefold() for value in contract.get("legislation_types") or []]
    _require(bool(congresses) and bool(configured_types), "registry collection contract missing")
    types = [value for value in configured_types if value in SUPPORTED_TYPES]
    unsupported_types = [value for value in configured_types if value not in SUPPORTED_TYPES]
    checkpoint_text = changed_since or str(baseline_manifest.get("completed_at") or "")
    checkpoint = _parse_timestamp(checkpoint_text, "discovery checkpoint")
    checkpoint_date: date = checkpoint.date()

    sitemap_receipts: list[dict[str, Any]] = []
    changed: dict[str, str] = {}
    for congress in congresses:
        for measure_type in types:
            sitemap_url = BILLSTATUS.expected_sitemap_url(congress, measure_type)
            raw = fetch(sitemap_url, MAX_SITEMAP_BYTES)
            records = parse_sitemap(
                raw,
                expected_url=sitemap_url,
                congress=congress,
                measure_type=measure_type,
            )
            selected = {
                url: lastmod
                for url, lastmod in records.items()
                if _parse_timestamp(lastmod, "sitemap.lastmod").date() >= checkpoint_date
            }
            changed.update(selected)
            sitemap_receipts.append(
                {
                    "url": sitemap_url,
                    "sha256": sha256_bytes(raw),
                    "bytes": len(raw),
                    "entry_count": len(records),
                    "changed_since_checkpoint_inclusive_count": len(selected),
                }
            )
    _require(len(changed) == sum(row["changed_since_checkpoint_inclusive_count"] for row in sitemap_receipts), "duplicate status URLs across sitemaps")

    known_changed_count = 0
    for url, lastmod in sorted(changed.items()):
        match = STATUS_URL_RE.fullmatch(url)
        assert match is not None
        key = BILLSTATUS.canonical_key(
            int(match.group("congress")), match.group("type"), int(match.group("number"))
        )
        if key in existing_by_key:
            known_changed_count += 1

    status_rows = sorted(changed.items())

    cache_dir = out_dir / ".govinfo_fallback_cache"

    def evaluate(source: tuple[str, str]) -> dict[str, Any]:
        url, lastmod = source
        raw_path, status_raw, status_cache_hit = _cache_fetch(
            cache_dir,
            url=url,
            version_token=lastmod,
            suffix=".billstatus.xml",
            max_bytes=BILLSTATUS.MAX_XML_BYTES,
            fetch=fetch,
        )
        parsed = BILLSTATUS.parse_billstatus_xml(
            raw_path,
            source_url=url,
            sitemap_lastmod=lastmod,
            expected_sha256=sha256_bytes(status_raw),
        )
        key = str(parsed["canonical_key"])
        nested_amendments = _parse_nested_amendments(
            status_raw,
            parent=parsed,
            parent_status_url=url,
            parent_sitemap_lastmod=lastmod,
        )
        version_receipts: list[dict[str, Any]] = []
        ignored_official_xml: list[str] = []
        text_cache_hits = 0
        evidence: list[tuple[str, str]] = []
        if key not in existing_by_key:
            evidence = _status_evidence(parsed)
            bill_text_urls, ignored_official_xml = _bill_text_urls(parsed)
            for text_url in bill_text_urls:
                text_path, text_raw, cache_hit = _cache_fetch(
                    cache_dir,
                    url=text_url,
                    version_token=text_url,
                    suffix=".bill.xml",
                    max_bytes=MAX_BILL_TEXT_BYTES,
                    fetch=fetch,
                )
                del text_path
                text = _parse_bill_text(text_raw, url=text_url, canonical_key=key)
                evidence.append(("official_bill_text", text))
                text_cache_hits += int(cache_hit)
                version_receipts.append(
                    {
                        "url": text_url,
                        "bytes": len(text_raw),
                        "sha256": sha256_bytes(text_raw),
                        "cache_hit": cache_hit,
                    }
                )
        scoring: list[str] = []
        monitors: list[str] = []
        probes: list[str] = []
        matches: list[dict[str, Any]] = []
        matched_entries: list[dict[str, Any]] = []
        if key not in existing_by_key:
            for entry in entries:
                terms, sources = _entry_matches(entry, canonical_key=key, evidence=evidence)
                if not terms:
                    continue
                entry_id = str(entry["id"])
                matched_entries.append(entry)
                if entry.get("record_kind") == "coverage_probe":
                    probes.append(entry_id)
                elif entry.get("scoring_category") is True:
                    scoring.append(entry_id)
                else:
                    monitors.append(entry_id)
                matches.append(
                    {
                        "query_id": entry_id,
                        "terms": terms,
                        "evidence_sources": sources,
                        "matching_method": "normalized_contiguous_phrase_or_exact_known_cite",
                    }
                )
        measure_record = None
        if scoring or monitors or probes:
            measure_record = _build_record(
                parsed,
                category_ids=scoring,
                monitor_ids=monitors,
                probe_ids=probes,
                match_receipts=matches,
                bill_text_receipts=version_receipts,
            )
            measure_record["window_status"] = _window_status(
                measure_record.get("initial_action_date_iso"), contract
            )
        return {
            "canonical_key": key,
            "measure_record": measure_record,
            "matched_entries": matched_entries,
            "matches": matches,
            "scoring": scoring,
            "monitors": monitors,
            "probes": probes,
            "version_receipts": version_receipts,
            "status_cache_hit": status_cache_hit,
            "text_cache_hits": text_cache_hits,
            "nested_amendments": nested_amendments,
            "ignored_non_bills_official_xml": ignored_official_xml,
        }

    evaluated: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(workers, 16))) as pool:
        futures = {pool.submit(evaluate, row): row for row in status_rows}
        for future in concurrent.futures.as_completed(futures):
            source = futures[future]
            try:
                evaluated.append(future.result())
            except Exception as error:
                for pending in futures:
                    pending.cancel()
                if isinstance(error, FallbackError):
                    raise
                raise FallbackError(f"failed to evaluate official record {source[0]}: {error}") from error

    new_records: list[dict[str, Any]] = []
    new_memberships: list[dict[str, Any]] = []
    unmatched_keys: list[str] = []
    status_cache_hits = 0
    text_cache_hits = 0
    text_version_count = 0
    no_bill_text_count = 0
    ignored_non_bills_official_xml_count = 0
    amendment_observation_count = 0
    amendment_known_count = 0
    amendment_without_direct_evidence_count = 0
    entry_by_id = {str(entry["id"]): entry for entry in entries}
    amendments_by_key: dict[str, list[dict[str, Any]]] = {}
    for result in sorted(evaluated, key=lambda row: row["canonical_key"]):
        key = str(result["canonical_key"])
        status_cache_hits += int(result["status_cache_hit"])
        text_cache_hits += int(result["text_cache_hits"])
        text_version_count += len(result["version_receipts"])
        ignored_non_bills_official_xml_count += len(
            result["ignored_non_bills_official_xml"]
        )
        if key not in existing_by_key:
            no_bill_text_count += int(not result["version_receipts"])
        for amendment in result["nested_amendments"]:
            amendment_observation_count += 1
            amendment_key = str(amendment["canonical_key"])
            if amendment_key in existing_by_key:
                amendment_known_count += 1
                continue
            amendments_by_key.setdefault(amendment_key, []).append(amendment)
        if key in existing_by_key:
            continue
        all_ids = [*result["scoring"], *result["monitors"], *result["probes"]]
        if not all_ids:
            unmatched_keys.append(key)
            continue
        record = result["measure_record"]
        _require(record is not None, f"missing matched measure record: {key}")
        new_records.append(record)
        for query_id in sorted(all_ids):
            entry = entry_by_id[query_id]
            identity = (query_id, key)
            _require(identity not in membership_ids, f"duplicate fallback membership: {identity}")
            membership_ids.add(identity)
            match_receipt = next(row for row in result["matches"] if row["query_id"] == query_id)
            new_memberships.append(
                {
                    "query_id": query_id,
                    "label": entry.get("label"),
                    "scoring_category": entry.get("scoring_category") is True,
                    "record_kind": entry.get("record_kind") or "category",
                    "parent_category_id": entry.get("parent_category_id"),
                    "parent_category_label": entry.get("parent_category_label"),
                    "identifier_text": entry.get("identifier_text"),
                    "title_alias": entry.get("title_alias"),
                    "ambiguous_short_act_alias": entry.get("ambiguous_short_act_alias"),
                    "membership_non_dispositive": entry.get("membership_non_dispositive"),
                    "source_paragraph": None,
                    "canonical_key": key,
                    "source_page": None,
                    "source_rank": None,
                    "result_url": record["result_url"],
                    "fallback_match": match_receipt,
                    "candidate_status": "pending_human_review",
                    "scoring_eligible": False,
                }
            )

    unmatched_amendment_keys: list[str] = []
    matched_amendment_count = 0
    for amendment_key, observations in sorted(amendments_by_key.items()):
        evidence: list[tuple[str, str]] = []
        for observation in observations:
            evidence.extend(
                (source, text)
                for source, text in observation["evidence"]
                if text
            )
        if not evidence:
            amendment_without_direct_evidence_count += 1
            unmatched_amendment_keys.append(amendment_key)
            continue
        scoring: list[str] = []
        monitors: list[str] = []
        probes: list[str] = []
        matches: list[dict[str, Any]] = []
        for entry in entries:
            terms, sources = _entry_matches(
                entry,
                canonical_key=amendment_key,
                evidence=evidence,
                allow_known_cite=False,
            )
            if not terms:
                continue
            entry_id = str(entry["id"])
            if entry.get("record_kind") == "coverage_probe":
                probes.append(entry_id)
            elif entry.get("scoring_category") is True:
                scoring.append(entry_id)
            else:
                monitors.append(entry_id)
            matches.append(
                {
                    "query_id": entry_id,
                    "terms": terms,
                    "evidence_sources": sources,
                    "matching_method": (
                        "normalized_contiguous_phrase_in_direct_amendment_"
                        "purpose_or_description_only"
                    ),
                }
            )
        all_ids = [*scoring, *monitors, *probes]
        if not all_ids:
            unmatched_amendment_keys.append(amendment_key)
            continue
        representative = max(
            observations,
            key=lambda row: str(row.get("update_date") or ""),
        )
        source_receipts = [
            {
                "parent_canonical_key": row["parent_key"],
                "billstatus_url": row["parent_status_url"],
                "sitemap_lastmod": row["parent_sitemap_lastmod"],
                "amendment_update_date": row["update_date"],
            }
            for row in sorted(
                observations,
                key=lambda item: (item["parent_key"], item["parent_status_url"]),
            )
        ]
        record = _build_amendment_record(
            representative,
            contract=contract,
            category_ids=scoring,
            monitor_ids=monitors,
            probe_ids=probes,
            match_receipts=matches,
            source_receipts=source_receipts,
        )
        new_records.append(record)
        matched_amendment_count += 1
        for query_id in sorted(all_ids):
            entry = entry_by_id[query_id]
            identity = (query_id, amendment_key)
            _require(identity not in membership_ids, f"duplicate fallback membership: {identity}")
            membership_ids.add(identity)
            match_receipt = next(row for row in matches if row["query_id"] == query_id)
            new_memberships.append(
                {
                    "query_id": query_id,
                    "label": entry.get("label"),
                    "scoring_category": entry.get("scoring_category") is True,
                    "record_kind": entry.get("record_kind") or "category",
                    "parent_category_id": entry.get("parent_category_id"),
                    "parent_category_label": entry.get("parent_category_label"),
                    "identifier_text": entry.get("identifier_text"),
                    "title_alias": entry.get("title_alias"),
                    "ambiguous_short_act_alias": entry.get("ambiguous_short_act_alias"),
                    "membership_non_dispositive": entry.get("membership_non_dispositive"),
                    "source_paragraph": None,
                    "canonical_key": amendment_key,
                    "source_page": None,
                    "source_rank": None,
                    "result_url": record["result_url"],
                    "fallback_match": match_receipt,
                    "candidate_status": "pending_human_review",
                    "scoring_eligible": False,
                }
            )

    output_records = [*existing_records, *new_records]
    output_memberships = [*existing_memberships, *new_memberships]
    output_map = _validate_existing_records(output_records)
    _require(len(output_map) == len(existing_by_key) + len(new_records), "record union did not grow exactly")
    _require(
        all(output_map[key] == row for key, row in existing_by_key.items()),
        "fallback mutated an existing record",
    )
    _validate_memberships(output_memberships, set(output_map))

    prepared_support = _prepare_support_files(
        baseline_dir=baseline_dir, out_dir=out_dir, new_records=new_records
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    records_path = out_dir / "congress_advanced_records.ndjson"
    memberships_path = out_dir / "congress_advanced_memberships.ndjson"
    # The prior manifest's union hashes stop matching once the union files are replaced, so
    # demote it first: an interrupted run leaves a non-complete manifest, not a drifted one.
    manifest_path = out_dir / "manifest.json"
    prior_manifest = _read_json(manifest_path) if manifest_path.is_file() else {"schema": MANIFEST_SCHEMA}
    _write_json(manifest_path, {**prior_manifest, "status": "in_progress"})
    _write_ndjson(records_path, output_records)
    _write_ndjson(memberships_path, output_memberships)
    support_receipts = _copy_or_extend_support_files(out_dir=out_dir, prepared=prepared_support)
    browser_failure_receipt = file_receipt(browser_failure_path)
    immutable_manifest_receipt = file_receipt(baseline_manifest_path)
    union = {
        "unique_record_count": len(output_records),
        "membership_count": len(output_memberships),
        "counts_by_congress": _counts(output_records, "congress"),
        "counts_by_type": _counts(output_records, "type"),
        "scoring_candidate_count": sum(bool(row.get("matched_scoring_categories")) for row in output_records),
        "monitor_candidate_count": sum(bool(row.get("matched_monitor_categories")) for row in output_records),
        "recall_probe_count": sum(bool(row.get("matched_coverage_probes")) for row in output_records),
        "records_file": records_path.name,
        "records_sha256": stable_hash(output_records),
        "memberships_file": memberships_path.name,
        "memberships_sha256": stable_hash(output_memberships),
    }
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS",
        "transport": "official_unauthenticated_govinfo_bulk_xml",
        "security_verification_bypassed": False,
        "browser_failure": browser_failure_receipt,
        "registry": {**file_receipt(registry_path), "sha256": registry_sha256},
        "immutable_baseline": {"manifest": immutable_manifest_receipt},
        "checkpoint": {
            "timestamp": checkpoint.isoformat(),
            "inclusive_calendar_date": checkpoint_date.isoformat(),
            "same_day_overlap_prevents_sitemap_date_granularity_gap": True,
        },
        "sitemaps": sitemap_receipts,
        "scan": {
            "changed_status_entries_seen": len(changed),
            "previously_known_changed_measures_preserved_without_mutation": known_changed_count,
            "previously_unseen_measure_ids_evaluated": len(status_rows) - known_changed_count,
            "new_matching_pending_candidates": len(new_records),
            "new_matching_pending_measure_candidates": len(new_records) - matched_amendment_count,
            "nested_amendment_observations": amendment_observation_count,
            "previously_known_amendment_observations_preserved": amendment_known_count,
            "previously_unseen_nested_amendment_ids_evaluated": len(amendments_by_key),
            "new_matching_pending_amendment_candidates": matched_amendment_count,
            "unmatched_nested_amendment_ids": len(unmatched_amendment_keys),
            "nested_amendment_ids_without_direct_purpose_or_description": amendment_without_direct_evidence_count,
            "new_memberships": len(new_memberships),
            "unmatched_new_measure_ids": len(unmatched_keys),
            "unmatched_new_measure_canonical_keys": unmatched_keys,
            "bill_text_xml_versions_evaluated": text_version_count,
            "non_bills_official_xml_formats_ignored": ignored_non_bills_official_xml_count,
            "new_measure_ids_without_declared_bill_text_xml": no_bill_text_count,
            "status_cache_hits": status_cache_hits,
            "bill_text_cache_hits": text_cache_hits,
        },
        "candidate_policy": {
            "candidate_status": "pending_human_review",
            "automatic_ratification": False,
            "automatic_scoring": False,
            "automatic_promotion": False,
            "existing_rows_mutated": False,
        },
        "limitations": [
            "GovInfo BILLSTATUS has no top-level House/Senate amendment sitemap. The fallback inspects direct nested amendments on every changed parent measure and admits a new amendment only when its own purpose or description matches; amendments omitted from or delayed in those parent records still wait for the primary Congress.gov search.",
            "Nested amendment matching deliberately excludes parent-bill titles, summaries, actions, and bill text and does not claim full amendment-text or Congress.gov word-variant parity.",
            "Fallback matching uses normalized contiguous phrases and exact Congress-qualified known-bill citations; it does not claim word-variant or ranking parity with Congress.gov Advanced Search.",
            "Only records whose official sitemap lastmod date is on or after the last-good checkpoint date are evaluated; the inclusive day is intentionally replayed.",
            "A measure without declared official BILLS XML is evaluated from BILLSTATUS titles, summaries, and actions and remains subject to human review.",
            "Official XML derivatives that are not BILLS text packages (for example, enrolled public-law XML) are not used as bill-text discovery evidence and are counted in the receipt.",
        ],
        "types_without_top_level_sitemaps": unsupported_types,
        "supporting_files": support_receipts,
        "union": union,
    }
    receipt_path = out_dir / "govinfo_fallback_receipt.json"
    _write_json(receipt_path, receipt)
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "status": "complete",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "primary_instrument": "GovInfo BILLSTATUS/BILLS official bulk fallback",
        "primary_instrument_temporarily_unavailable": "Congress.gov Advanced Search - Legislation",
        "fallback_reason": "CONGRESS_AUTOMATIC_VERIFICATION_TIMEOUT",
        "quorum_used": False,
        "captcha_interaction_performed": False,
        "captcha_bypass_attempted": False,
        "registry": {"path": file_receipt(registry_path)["path"], "sha256": registry_sha256},
        "immutable_baseline": {
            "manifest": immutable_manifest_receipt,
            "baseline_record_count": int((baseline_manifest.get("union") or {}).get("unique_record_count", 0)),
            "preexisting_cumulative_record_count": len(existing_records),
            "preexisting_records_preserved_losslessly": True,
        },
        "selected_query_ids": entry_ids,
        "collection_contract": contract,
        "fallback_receipt": file_receipt(receipt_path),
        "union": union,
        "candidate_policy": receipt["candidate_policy"],
        "limitations": receipt["limitations"],
    }
    _write_json(manifest_path, manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--browser-failure", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--categories")
    parser.add_argument("--changed-since")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = args.out.resolve()
    try:
        out_dir.relative_to(ROOT.resolve())
    except ValueError as error:
        raise SystemExit(f"output must remain inside the repository: {out_dir}") from error
    selected = (
        {value.strip() for value in args.categories.split(",") if value.strip()}
        if args.categories
        else None
    )
    manifest = run_fallback(
        registry_path=args.registry.resolve(),
        baseline_dir=args.baseline.resolve(),
        out_dir=out_dir,
        browser_failure_path=args.browser_failure.resolve(),
        workers=max(1, args.workers),
        fetch=network_fetcher(args.timeout_seconds),
        selected_categories=selected,
        changed_since=args.changed_since,
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "transport": manifest["primary_instrument"],
                "records": manifest["union"]["unique_record_count"],
                "memberships": manifest["union"]["membership_count"],
                "manifest": str(out_dir / "manifest.json"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
