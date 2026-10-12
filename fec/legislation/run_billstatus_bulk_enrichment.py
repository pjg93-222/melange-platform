#!/usr/bin/env python3
"""Resumable, bounded-concurrency GovInfo BILLSTATUS staging worker."""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import concurrent.futures
import hashlib
import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from legislation import stage_billstatus_bulk_enrichment as BILLSTATUS

try:
    from lane_file_io import atomic_write_bytes
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import atomic_write_bytes


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "govinfo-billstatus-enrichment-run-v1"
RECORD_RECEIPT_SCHEMA = "govinfo-billstatus-record-receipt-v1"
SITEMAP_RECEIPT_SCHEMA = "govinfo-billstatus-sitemap-receipt-v1"
MAX_SITEMAP_BYTES = 64 * 1024 * 1024
MAX_WORKERS = 8


class WorkerError(RuntimeError):
    """Raised when official staging evidence fails closed."""


@dataclass(frozen=True)
class FetchResponse:
    body: bytes
    status: int
    final_url: str
    headers: dict[str, str]


Fetcher = Callable[[str], FetchResponse]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkerError(message)


def atomic_write(path: Path, body: bytes, *, immutable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable:
        if path.exists():
            require(path.read_bytes() == body, f"immutable content drift: {path}")
            return
        with path.open("xb") as handle:
            handle.write(body)
        return
    # Was ``path.with_name(path.name + ".tmp")``: a fixed scratch name shared by
    # every concurrent worker writing the same output, and exactly the kind of
    # stable name OneDrive syncs and conflict-copies.  ``atomic_write_bytes`` is
    # unique per process and per call, fsync'd before publication, and retries
    # the publish through the OneDrive/AV lock window.
    atomic_write_bytes(path, body)


def json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    )


def official_url(url: str) -> None:
    parsed = urlparse(url)
    require(
        parsed.scheme == "https"
        and parsed.hostname == "www.govinfo.gov"
        and parsed.username is None
        and parsed.password is None
        and parsed.port in (None, 443),
        f"non-official URL refused: {url}",
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise WorkerError(f"redirect refused: {req.full_url} -> {newurl}")


def network_fetcher(timeout_seconds: float) -> Fetcher:
    context = ssl.create_default_context()
    opener = urllib.request.build_opener(
        _NoRedirect(),
        urllib.request.HTTPSHandler(context=context),
    )

    def fetch(url: str) -> FetchResponse:
        official_url(url)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/xml,text/xml;q=0.9",
                "User-Agent": (
                    "MelangeSystems-BILLSTATUS-Staging/1.0 "
                    "(official GovInfo audit enrichment)"
                ),
            },
        )
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                final_url = response.geturl()
                official_url(final_url)
                require(final_url == url, f"redirect/final URL drift: {url}")
                status = int(getattr(response, "status", 0))
                require(status == 200, f"unexpected HTTP status {status}: {url}")
                limit = (
                    BILLSTATUS.MAX_XML_BYTES
                    if "/BILLSTATUS-" in url
                    else MAX_SITEMAP_BYTES
                )
                body = response.read(limit + 1)
                require(len(body) <= limit, f"oversize official XML: {url}")
                return FetchResponse(
                    body=body,
                    status=status,
                    final_url=final_url,
                    headers={
                        key.casefold(): value
                        for key, value in response.headers.items()
                    },
                )
        except urllib.error.URLError as error:
            raise WorkerError(f"official fetch failed: {url}: {error}") from error

    return fetch


def parse_sitemap(raw: bytes, *, expected_url: str) -> dict[str, str]:
    require(len(raw) <= MAX_SITEMAP_BYTES, "oversize sitemap")
    upper = raw.upper()
    require(b"<!DOCTYPE" not in upper, "DOCTYPE is forbidden in sitemap")
    require(b"<!ENTITY" not in upper, "ENTITY is forbidden in sitemap")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise WorkerError(f"malformed sitemap: {expected_url}") from error
    require(root.tag.endswith("urlset"), f"unexpected sitemap root: {root.tag}")
    records: dict[str, str] = {}
    for item in list(root):
        loc: str | None = None
        lastmod: str | None = None
        for child in list(item):
            if child.tag.endswith("loc"):
                loc = (child.text or "").strip()
            elif child.tag.endswith("lastmod"):
                lastmod = (child.text or "").strip()
        require(loc is not None and lastmod is not None, "incomplete sitemap row")
        official_url(loc)
        require(
            loc.startswith(BILLSTATUS.OFFICIAL_BASE + "/"),
            f"out-of-scope sitemap location: {loc}",
        )
        BILLSTATUS._iso8601(lastmod, "sitemap.lastmod")
        require(loc not in records, f"duplicate sitemap location: {loc}")
        records[loc] = lastmod
    require(records, f"empty sitemap: {expected_url}")
    return records


def _url_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def ensure_sitemap(
    run_dir: Path,
    row: dict[str, Any],
    fetch: Fetcher,
) -> tuple[dict[str, str], dict[str, Any], bool]:
    url = str(row["sitemap_url"])
    official_url(url)
    identity = _url_id(url)
    receipt_path = run_dir / "sitemaps" / f"{identity}.json"
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        raw_path = Path(str(receipt.get("raw_path") or ""))
        require(
            receipt.get("schema") == SITEMAP_RECEIPT_SCHEMA
            and receipt.get("url") == url
            and raw_path.is_file()
            and receipt.get("raw_sha256") == BILLSTATUS.sha256_file(raw_path)
            and receipt.get("raw_bytes") == raw_path.stat().st_size,
            f"stale/tampered sitemap receipt: {url}",
        )
        records = parse_sitemap(raw_path.read_bytes(), expected_url=url)
        require(
            receipt.get("record_count") == len(records),
            f"sitemap receipt count drift: {url}",
        )
        return records, receipt, True
    response = fetch(url)
    require(response.status == 200 and response.final_url == url, "sitemap fetch drift")
    records = parse_sitemap(response.body, expected_url=url)
    digest = BILLSTATUS.sha256_bytes(response.body)
    raw_path = run_dir / "raw" / "sha256" / f"{digest}.xml"
    atomic_write(raw_path, response.body, immutable=True)
    receipt = {
        "schema": SITEMAP_RECEIPT_SCHEMA,
        "url": url,
        "status": response.status,
        "response_headers": response.headers,
        "raw_path": str(raw_path.resolve()),
        "raw_sha256": digest,
        "raw_bytes": len(response.body),
        "record_count": len(records),
        "network_executed": True,
    }
    atomic_write(receipt_path, json_bytes(receipt), immutable=True)
    return records, receipt, False


def validate_record_receipt(
    receipt_path: Path,
    task: dict[str, Any],
    *,
    sitemap_lastmod: str,
) -> dict[str, Any]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    raw_path = Path(str(receipt.get("raw_path") or ""))
    parsed_path = Path(str(receipt.get("parsed_path") or ""))
    require(
        receipt.get("schema") == RECORD_RECEIPT_SCHEMA
        and receipt.get("canonical_key") == task["canonical_key"]
        and receipt.get("source_url") == task["url"]
        and receipt.get("sitemap_lastmod") == sitemap_lastmod
        and raw_path.is_file()
        and parsed_path.is_file()
        and receipt.get("raw_sha256") == BILLSTATUS.sha256_file(raw_path)
        and receipt.get("raw_bytes") == raw_path.stat().st_size
        and receipt.get("parsed_sha256") == BILLSTATUS.sha256_file(parsed_path)
        and receipt.get("parsed_bytes") == parsed_path.stat().st_size,
        f"stale/tampered record receipt: {task['canonical_key']}",
    )
    parsed = BILLSTATUS.parse_billstatus_xml(
        raw_path,
        source_url=task["url"],
        sitemap_lastmod=sitemap_lastmod,
        expected_sha256=receipt["raw_sha256"],
    )
    stored = json.loads(parsed_path.read_text(encoding="utf-8"))
    require(
        parsed == stored
        and parsed.get("canonical_key") == task["canonical_key"],
        f"parsed record drift: {task['canonical_key']}",
    )
    return receipt


def fetch_record(
    run_dir: Path,
    task: dict[str, Any],
    *,
    sitemap_lastmod: str,
    fetch: Fetcher,
) -> tuple[dict[str, Any], bool]:
    key = str(task["canonical_key"])
    receipt_path = run_dir / "receipts" / f"{key.replace(':', '_')}.json"
    if receipt_path.is_file():
        return (
            validate_record_receipt(
                receipt_path, task, sitemap_lastmod=sitemap_lastmod
            ),
            True,
        )
    url = str(task["url"])
    official_url(url)
    response = fetch(url)
    require(response.status == 200 and response.final_url == url, "record fetch drift")
    raw_digest = BILLSTATUS.sha256_bytes(response.body)
    raw_path = run_dir / "raw" / "sha256" / f"{raw_digest}.xml"
    atomic_write(raw_path, response.body, immutable=True)
    parsed = BILLSTATUS.parse_billstatus_xml(
        raw_path,
        source_url=url,
        sitemap_lastmod=sitemap_lastmod,
        expected_sha256=raw_digest,
    )
    require(parsed["canonical_key"] == key, f"task/XML identity drift: {key}")
    parsed_body = json_bytes(parsed)
    parsed_digest = BILLSTATUS.sha256_bytes(parsed_body)
    parsed_path = run_dir / "parsed" / "sha256" / f"{parsed_digest}.json"
    atomic_write(parsed_path, parsed_body, immutable=True)
    receipt = {
        "schema": RECORD_RECEIPT_SCHEMA,
        "canonical_key": key,
        "source_url": url,
        "sitemap_lastmod": sitemap_lastmod,
        "status": response.status,
        "response_headers": response.headers,
        "raw_path": str(raw_path.resolve()),
        "raw_sha256": raw_digest,
        "raw_bytes": len(response.body),
        "parsed_path": str(parsed_path.resolve()),
        "parsed_sha256": parsed_digest,
        "parsed_bytes": len(parsed_body),
        "advanced_search_line": task["advanced_search_line"],
        "advanced_search_line_sha256": task[
            "advanced_search_line_sha256"
        ],
        "candidate_creation_performed": False,
        "semantic_exclusion_performed": False,
    }
    atomic_write(receipt_path, json_bytes(receipt), immutable=True)
    return receipt, False


def run(
    *,
    plan_path: Path,
    out_root: Path,
    workers: int,
    fetch: Fetcher,
    max_records: int | None = None,
    enforce_workspace_audits: bool = True,
) -> dict[str, Any]:
    require(1 <= workers <= MAX_WORKERS, f"workers must be 1..{MAX_WORKERS}")
    plan_path = plan_path.resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    strict_receipt = (plan.get("snapshot_contract") or {}).get(
        "strict_manifest"
    )
    require(isinstance(strict_receipt, dict), "plan strict receipt missing")
    strict_path = Path(str(strict_receipt.get("path") or ""))
    BILLSTATUS.validate_snapshot_plan(
        plan, expected_strict_manifest=strict_path
    )
    root = out_root.resolve()
    if enforce_workspace_audits:
        audit_root = (ROOT / "audits").resolve()
        require(
            root != audit_root and root.is_relative_to(audit_root),
            "fetch output root must be a child of workspace audits",
        )
    producer_path = Path(__file__).resolve()
    run_id = BILLSTATUS.stable_json_sha256(
        {
            "plan_sha256": BILLSTATUS.sha256_file(plan_path),
            "producer_sha256": BILLSTATUS.sha256_file(producer_path),
        }
    )
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    sitemap_map: dict[str, str] = {}
    sitemap_receipts: list[dict[str, Any]] = []
    sitemap_reused = 0
    for sitemap in plan["type_sitemaps"]:
        records, receipt, reused = ensure_sitemap(run_dir, sitemap, fetch)
        overlap = set(sitemap_map) & set(records)
        require(not overlap, "record URL appears in multiple type sitemaps")
        sitemap_map.update(records)
        sitemap_receipts.append(receipt)
        sitemap_reused += int(reused)

    tasks = list(plan["record_tasks"])
    missing = [task["canonical_key"] for task in tasks if task["url"] not in sitemap_map]
    require(not missing, f"planned records absent from exact official sitemaps: {missing[:5]}")
    selected_tasks = tasks[:max_records] if max_records is not None else tasks
    require(
        max_records is None or max_records >= 0,
        "max_records cannot be negative",
    )
    completed: list[dict[str, Any]] = []
    reused_count = 0
    failures: list[dict[str, str]] = []

    def one(task: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        return fetch_record(
            run_dir,
            task,
            sitemap_lastmod=sitemap_map[task["url"]],
            fetch=fetch,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_task = {
            pool.submit(one, task): task for task in selected_tasks
        }
        for future in concurrent.futures.as_completed(future_to_task):
            task = future_to_task[future]
            try:
                receipt, reused = future.result()
                completed.append(receipt)
                reused_count += int(reused)
            except Exception as error:
                failures.append(
                    {
                        "canonical_key": str(task["canonical_key"]),
                        "error": str(error),
                    }
                )

    unsupported_path = run_dir / "unsupported_nested_amendments.ndjson"
    unsupported_body = b"".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        + b"\n"
        for row in plan["unsupported_candidates"]
    )
    atomic_write(unsupported_path, unsupported_body, immutable=True)
    completed.sort(key=lambda row: row["canonical_key"])
    failures.sort(key=lambda row: row["canonical_key"])
    status = (
        "COMPLETE"
        if not failures and len(selected_tasks) == len(tasks)
        else ("BOUNDED_PARTIAL" if not failures else "FAILED_CLOSED")
    )
    manifest = {
        "schema": SCHEMA,
        "status": status,
        "snapshot_only": True,
        "official_source_only": True,
        "network_executed": bool(
            len(sitemap_receipts) - sitemap_reused
            + len(selected_tasks) - reused_count
        ),
        "production_write_performed": False,
        "sync_or_promotion_performed": False,
        "candidate_creation_performed": False,
        "semantic_exclusion_performed": False,
        "plan": BILLSTATUS.file_receipt(plan_path),
        "producer": BILLSTATUS.file_receipt(producer_path),
        "run_id": run_id,
        "workers": workers,
        "partition": {
            "all_candidates": plan["candidate_partition"]["all"],
            "top_level_planned": len(tasks),
            "top_level_selected_this_run": len(selected_tasks),
            "top_level_completed": len(completed),
            "top_level_failed": len(failures),
            "nested_only_amendments_retained_unresolved": len(
                plan["unsupported_candidates"]
            ),
            "remaining_top_level": len(tasks) - len(completed),
        },
        "sitemaps": {
            "required": len(plan["type_sitemaps"]),
            "validated": len(sitemap_receipts),
            "reused": sitemap_reused,
        },
        "unsupported_amendments": {
            **BILLSTATUS.file_receipt(unsupported_path),
            "record_count": len(plan["unsupported_candidates"]),
            "disposition": "retained_unresolved_no_standalone_top_level_package",
        },
        "record_receipts": completed,
        "failures": failures,
    }
    manifest_path = run_dir / "run_manifest.json"
    atomic_write(manifest_path, json_bytes(manifest))
    return {**manifest, "manifest_path": str(manifest_path.resolve())}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--max-records", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run(
        plan_path=args.plan,
        out_root=args.out_root,
        workers=args.workers,
        fetch=network_fetcher(args.timeout_seconds),
        max_records=args.max_records,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "manifest": result["manifest_path"],
                "partition": result["partition"],
                "network_executed": result["network_executed"],
                "sync_or_promotion_performed": False,
            },
            indent=2,
        )
    )
    return 0 if result["status"] in {"COMPLETE", "BOUNDED_PARTIAL"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
