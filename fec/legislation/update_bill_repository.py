# -*- coding: utf-8 -*-
"""Refresh the dashboard's Congress.gov bill cache from official sources.

GovInfo BILLSTATUS is the primary transport, with persistent Playwright/Chrome
as the Congress.gov fallback. A failed bill keeps its last-good record and is
marked stale. Optional publication runs the complete verifier-to-Excel-to-
dashboard pipeline under the same writer lease as collection.

Examples:
  python fec/legislation/update_bill_repository.py
  python fec/legislation/update_bill_repository.py --watch --rebuild-dashboard
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import concurrent.futures
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from xml.etree import ElementTree as ET

from paths import ROOT
from legislation.congress_bill_repository import (
    REFRESH_RECEIPT_SCHEMA,
    merge_refresh,
    seed_fingerprint,
    seed_identity_fingerprint,
    seeds_from_member_bundle,
    sha256_file,
    utc_now,
    validate_repository,
    write_repository_atomic,
)
from legislation import stage_billstatus_bulk_enrichment as BILLSTATUS
from legislation.run_billstatus_bulk_enrichment import (
    network_fetcher as govinfo_network_fetcher,
    parse_sitemap as parse_govinfo_sitemap,
)

BROWSER_SCRIPT = (
    Path(ROOT) / "fec" / "refresh_bill_repository_headless.mjs"
).resolve()
BROWSER_SCHEMA = "congress-bill-browser-refresh-v1"
DEFAULT_RECEIPT = (
    Path(ROOT) / "data" / "engine" / "congress_bill_refresh_receipt.json"
).resolve()


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()


def _govinfo_person(row: dict) -> dict:
    return {
        "name": str(row.get("full_name") or "").strip(),
        "bioguide": str(row.get("bioguide_id") or "").strip(),
        "party": str(row.get("party") or "").strip(),
        "state": str(row.get("state") or "").strip(),
        "district": row.get("district") or "",
        "date": str(row.get("sponsorship_date") or "").strip(),
        "withdrawn_date": str(row.get("withdrawn_date") or "").strip(),
    }


def _govinfo_amendments(raw: bytes) -> tuple[list[dict], str, str]:
    root = ET.fromstring(raw)
    bill = root.find("bill")
    if bill is None:
        raise RuntimeError("GovInfo BILLSTATUS record has no bill element")
    policy_area = (bill.findtext("./policyArea/name") or "").strip()
    introduced_date = (bill.findtext("introducedDate") or "").strip()
    rows = []
    amendments = list(bill.findall("./amendments/amendment"))
    amendments.extend(bill.findall("./amendments/item"))
    for item in amendments:
        amendment_type = (item.findtext("type") or "").strip().lower()
        number = (item.findtext("number") or "").strip()
        congress = (item.findtext("congress") or "").strip()
        if amendment_type not in {"hamdt", "samdt"} or not number.isdigit() or not congress.isdigit():
            continue
        latest = item.find("latestAction")
        latest_date = (latest.findtext("actionDate") if latest is not None else "") or ""
        latest_text = (latest.findtext("text") if latest is not None else "") or ""
        chamber = "house" if amendment_type == "hamdt" else "senate"
        rows.append({
            "display": ("H.Amdt." if amendment_type == "hamdt" else "S.Amdt.") + number,
            "congress": int(congress),
            "type": amendment_type,
            "number": number,
            "description": _clean_text(
                item.findtext("purpose") or item.findtext("description") or ""
            ),
            "latest_action": {
                "date": str(latest_date).strip()[:10],
                "text": _clean_text(latest_text),
            },
            "url": (
                f"https://www.congress.gov/amendment/{congress}th-congress/"
                f"{chamber}-amendment/{number}"
            ),
        })
    unique = {row["url"]: row for row in rows}
    return [unique[key] for key in sorted(unique)], policy_area, introduced_date


def _govinfo_snapshot(seed: dict, parsed: dict, raw: bytes, *, updated_at: str) -> dict:
    identity = parsed.get("identity") or {}
    bill_identity = BILLSTATUS.canonical_key(
        int(identity.get("congress") or 0),
        str(identity.get("type") or ""),
        int(identity.get("number") or 0),
    )
    expected = BILLSTATUS.canonical_key(
        int(seed["congress"]),
        re.sub(r"[^a-z]", "", str(seed["bill"]).lower()),
        int(re.sub(r"\D", "", str(seed["bill"]))),
    )
    if bill_identity != expected:
        raise RuntimeError(f"GovInfo BILLSTATUS identity mismatch: {bill_identity} != {expected}")
    sponsors = parsed.get("sponsors") or []
    if not sponsors or not str(sponsors[0].get("full_name") or "").strip():
        raise RuntimeError(f"GovInfo BILLSTATUS sponsor missing: {expected}")
    cosponsors = parsed.get("cosponsors") or []
    original = [_govinfo_person(row) for row in cosponsors if row.get("original_cosponsor") is True]
    later = [_govinfo_person(row) for row in cosponsors if row.get("original_cosponsor") is not True]
    actions = [row for row in (parsed.get("actions") or []) if row.get("action_date")]
    latest = max(
        actions,
        key=lambda row: (str(row.get("action_date") or ""), str(row.get("action_time") or "")),
        default={},
    )
    summaries = [row for row in (parsed.get("summaries") or []) if row.get("text")]
    latest_summary = max(
        summaries,
        key=lambda row: str(row.get("update_date") or row.get("action_date") or ""),
        default={},
    )
    titles = [str(row.get("title") or "").strip() for row in (parsed.get("titles") or [])]
    titles = [title for title in titles if title]
    amendments, policy_area, introduced_date = _govinfo_amendments(raw)
    display = str(seed["bill"])
    normalized = re.sub(r"[^A-Z0-9]", "", display.upper())
    type_match = re.match(r"(HCONRES|SCONRES|HJRES|SJRES|HRES|SRES|HR|S)(\d+)$", normalized)
    if not type_match:
        raise RuntimeError(f"unsupported seeded bill identity: {display}")
    type_code, number = type_match.groups()
    page_slugs = {
        "HR": "house-bill", "S": "senate-bill", "HRES": "house-resolution",
        "SRES": "senate-resolution", "HJRES": "house-joint-resolution",
        "SJRES": "senate-joint-resolution", "HCONRES": "house-concurrent-resolution",
        "SCONRES": "senate-concurrent-resolution",
    }
    return {
        "key": expected,
        "congress": int(seed["congress"]),
        "type": type_code.lower(),
        "number": int(number),
        "display": display,
        "url": (
            f"https://www.congress.gov/bill/{int(seed['congress'])}th-congress/"
            f"{page_slugs[type_code]}/{int(number)}"
        ),
        "title": str(seed.get("title") or (titles[-1] if titles else display)).strip(),
        "introduced_date": introduced_date,
        "update_date": str(parsed.get("record_update_date_including_text") or parsed.get("record_update_date") or ""),
        "latest_action": {
            "date": str(latest.get("action_date") or "").strip(),
            "text": _clean_text(latest.get("text") or seed.get("action") or ""),
        },
        "sponsor": _govinfo_person(sponsors[0]),
        "original_cosponsors": original,
        "cosponsors": later,
        "cosponsor_count": len(original) + len(later),
        "amendments": amendments,
        "summary": _clean_text(latest_summary.get("text") or seed.get("summary") or ""),
        "policy_area": policy_area,
        "areas": sorted({str(area).strip() for area in (seed.get("areas") or []) if str(area).strip()}),
        "updated_at": updated_at,
        "stale": False,
        "source": "U.S. Government Publishing Office BILLSTATUS / Congress.gov",
    }


def _govinfo_refresh(seeds: list[dict], *, workers: int) -> tuple[dict, dict, dict]:
    """Refresh the seeded universe from official GovInfo BILLSTATUS bulk XML.

    This is the non-interactive official transport.  It validates every requested record against
    the exact GovInfo type sitemap and the existing strict BILLSTATUS parser before promotion.
    """
    fetch = govinfo_network_fetcher(120.0)
    identities = []
    combinations = set()
    for seed in seeds:
        normalized = re.sub(r"[^A-Z0-9]", "", str(seed["bill"]).upper())
        match = re.match(r"(HCONRES|SCONRES|HJRES|SJRES|HRES|SRES|HR|S)(\d+)$", normalized)
        if not match:
            raise RuntimeError(f"unsupported seeded bill identity: {seed['bill']}")
        measure_type, number = match.groups()
        congress = int(seed["congress"])
        measure_type = measure_type.lower()
        url = BILLSTATUS.expected_record_url(congress, measure_type, int(number))
        identities.append((seed, congress, measure_type, int(number), url))
        combinations.add((congress, measure_type))

    sitemap_records: dict[str, str] = {}
    for congress, measure_type in sorted(combinations):
        sitemap_url = BILLSTATUS.expected_sitemap_url(congress, measure_type)
        response = fetch(sitemap_url)
        records = parse_govinfo_sitemap(response.body, expected_url=sitemap_url)
        overlap = set(sitemap_records) & set(records)
        if overlap:
            raise RuntimeError("GovInfo record appeared in multiple exact type sitemaps")
        sitemap_records.update(records)
    absent = [url for *_rest, url in identities if url not in sitemap_records]
    if absent:
        raise RuntimeError(f"seeded records absent from exact GovInfo sitemaps: {absent[:5]}")

    refreshed: dict[str, dict] = {}
    failures: dict[str, str] = {}
    stamp = utc_now()

    def one(item):
        seed, congress, measure_type, number, url = item
        response = fetch(url)
        digest = hashlib.sha256(response.body).hexdigest()
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False, dir=Path(ROOT)) as handle:
            handle.write(response.body)
            raw_path = Path(handle.name)
        try:
            parsed = BILLSTATUS.parse_billstatus_xml(
                raw_path,
                source_url=url,
                sitemap_lastmod=sitemap_records[url],
                expected_sha256=digest,
            )
            return parsed["canonical_key"], _govinfo_snapshot(
                seed, parsed, response.body, updated_at=stamp
            )
        finally:
            raw_path.unlink(missing_ok=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(8, workers))) as pool:
        future_rows = {pool.submit(one, item): item for item in identities}
        for future in concurrent.futures.as_completed(future_rows):
            item = future_rows[future]
            key = BILLSTATUS.canonical_key(item[1], item[2], item[3])
            try:
                actual_key, row = future.result()
                if actual_key != key:
                    raise RuntimeError(f"GovInfo result key drift: {actual_key} != {key}")
                refreshed[key] = row
            except Exception as exc:
                failures[key] = f"{type(exc).__name__}: {exc}"
    return refreshed, failures, {
        "browser_status": "govinfo-complete" if not failures else "govinfo-partial",
        "browser_output": (
            f"official GovInfo BILLSTATUS: {len(refreshed)} refreshed, {len(failures)} failed, "
            f"{len(combinations)} exact type sitemaps validated"
        ),
        "browser_errors": "",
    }


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def read_repository(path: Path) -> tuple[dict, bool]:
    backup = path.with_suffix(path.suffix + ".lastgood")
    primary_error = None
    if path.exists():
        try:
            repository = read_json(path)
            validate_repository(repository)
            return repository, False
        except Exception as exc:
            primary_error = exc
    if backup.exists():
        repository = read_json(backup)
        validate_repository(repository)
        return repository, True
    if primary_error is not None:
        raise primary_error
    return {}, False


def refresh_status(refreshed: int, failed: int) -> str:
    if refreshed and not failed:
        return "PASS"
    return "PARTIAL" if refreshed else "STALE"


def _browser_runtime() -> tuple[str, dict[str, str]]:
    environment = os.environ.copy()
    bundled = (
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "node"
    )
    configured = environment.get("CONGRESS_NODE", "").strip()
    candidates = [
        Path(configured) if configured else None,
        bundled / "bin" / ("node.exe" if os.name == "nt" else "node"),
    ]
    node = next(
        (str(candidate) for candidate in candidates if candidate and candidate.is_file()),
        None,
    )
    if node is None:
        node = shutil.which("node")
    if not node:
        raise RuntimeError(
            "Node.js was not found; set CONGRESS_NODE or install the "
            "workspace runtime"
        )
    node_modules = bundled / "node_modules"
    if node_modules.is_dir() and not environment.get("NODE_PATH"):
        environment["NODE_PATH"] = str(node_modules)
    return node, environment


def _browser_refresh(
    seeds: list[dict],
    *,
    workers: int,
    delay_ms: int,
    profile: Path | None,
    chrome: Path | None,
    limit: int | None,
) -> tuple[dict, dict, dict]:
    node, environment = _browser_runtime()
    repo_root = Path(ROOT).resolve()
    with tempfile.TemporaryDirectory(
        prefix=".bill-browser-refresh-",
        dir=repo_root,
    ) as temporary:
        stage = Path(temporary)
        seeds_path = stage / "seeds.json"
        result_path = stage / "result.json"
        seeds_path.write_text(
            json.dumps(seeds, ensure_ascii=False),
            encoding="utf-8", newline='\r\n',
        )
        command = [
            node,
            str(BROWSER_SCRIPT),
            "--seeds",
            str(seeds_path),
            "--output",
            str(result_path),
            "--headless",
            "--workers",
            str(max(1, min(4, workers))),
            "--delay-ms",
            str(max(0, delay_ms)),
        ]
        if profile is not None:
            command.extend(["--profile", str(profile)])
        if chrome is not None:
            command.extend(["--chrome", str(chrome)])
        if limit is not None:
            command.extend(["--limit", str(limit)])
        completed = subprocess.run(
            command,
            cwd=repo_root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if not result_path.is_file():
            diagnostic = (
                completed.stderr.strip()
                or completed.stdout.strip()
                or f"browser exited {completed.returncode}"
            )
            raise RuntimeError(
                "headless Congress.gov refresh did not produce a checkpoint: "
                + diagnostic[-1200:]
            )
        result = read_json(result_path)
        if result.get("schema") != BROWSER_SCHEMA:
            raise RuntimeError("headless browser checkpoint schema mismatch")
        if result.get("status") not in {"complete", "partial", "stale"}:
            diagnostic = (
                completed.stderr.strip()
                or completed.stdout.strip()
                or f"browser exited {completed.returncode}"
            )
            raise RuntimeError(
                "headless browser exited before finalizing its checkpoint: "
                + diagnostic[-1200:]
            )
        refreshed = result.get("refreshed")
        failures = result.get("failures")
        if not isinstance(refreshed, dict) or not isinstance(failures, dict):
            raise RuntimeError("headless browser checkpoint is malformed")
        if completed.returncode:
            diagnostic = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(
                "headless Congress.gov refresh failed: " + diagnostic[-1200:]
            )
        metadata = {
            "browser_status": result.get("status"),
            "browser_output": completed.stdout.strip()[-1200:],
            "browser_errors": completed.stderr.strip()[-1200:],
        }
        return refreshed, failures, metadata


def refresh_once(
    *,
    bundle_path: Path,
    output_path: Path,
    workers: int,
    delay_ms: int = 750,
    profile: Path | None = None,
    chrome: Path | None = None,
    limit: int | None = None,
) -> dict:
    from legislation import sentinel_admissions as SA
    bundle = SA.augment_bundle(read_json(bundle_path), SA.load(bundle_path.parent))
    seeds = seeds_from_member_bundle(bundle)
    if not seeds:
        raise RuntimeError("member desk bundle contains no supported bills")
    selected = seeds[:limit] if limit is not None else seeds
    existing, recovered_from_lastgood = read_repository(output_path)
    stamp = utc_now()
    try:
        refreshed, failures, browser_metadata = _govinfo_refresh(
            selected,
            workers=workers,
        )
    except Exception as exc:
        refreshed = {}
        failures = {
            BILLSTATUS.canonical_key(
                int(seed["congress"]),
                re.sub(r"[^a-z]", "", str(seed["bill"]).lower()),
                int(re.sub(r"\D", "", str(seed["bill"]))),
            ): f"GovInfoTransportError: {type(exc).__name__}: {exc}"
            for seed in selected
        }
        browser_metadata = {
            "browser_status": "govinfo-failed",
            "browser_output": "",
            "browser_errors": str(exc)[-1200:],
        }

    # Exhaust the second official transport only for rows the bulk feed could not close.
    # It remains fail-closed on Congress.gov verification; a challenged page is never bypassed.
    if failures:
        failed_keys = set(failures)
        retry_seeds = []
        for seed in selected:
            normalized = re.sub(r"[^A-Z0-9]", "", str(seed["bill"]).upper())
            match = re.match(r"(HCONRES|SCONRES|HJRES|SJRES|HRES|SRES|HR|S)(\d+)$", normalized)
            if match:
                key = BILLSTATUS.canonical_key(
                    int(seed["congress"]), match.group(1).lower(), int(match.group(2))
                )
                if key in failed_keys:
                    retry_seeds.append(seed)
        try:
            page_rows, page_failures, page_metadata = _browser_refresh(
                retry_seeds,
                workers=workers,
                delay_ms=delay_ms,
                profile=profile,
                chrome=chrome,
                limit=None,
            )
            refreshed.update(page_rows)
            for key in page_rows:
                failures.pop(key, None)
            for key, message in page_failures.items():
                failures[key] = message
            browser_metadata = {
                "browser_status": (
                    f"{browser_metadata.get('browser_status')}+congress-{page_metadata.get('browser_status')}"
                ),
                "browser_output": (
                    str(browser_metadata.get("browser_output") or "") + "; "
                    + str(page_metadata.get("browser_output") or "")
                )[-1200:],
                "browser_errors": (
                    str(browser_metadata.get("browser_errors") or "") + "; "
                    + str(page_metadata.get("browser_errors") or "")
                )[-1200:],
            }
        except Exception as exc:
            browser_metadata["browser_errors"] = (
                str(browser_metadata.get("browser_errors") or "")
                + f"; CongressPageFallbackError: {type(exc).__name__}: {exc}"
            )[-1200:]
    repository = merge_refresh(
        existing,
        refreshed,
        failures,
        updated_at=stamp,
    )
    write_repository_atomic(output_path, repository)
    status = refresh_status(len(refreshed), len(failures))
    if limit is not None and len(selected) < len(seeds) and status == "PASS":
        status = "PARTIAL"
    return {
        "status": status,
        "updated_at": stamp,
        "seeded": len(selected),
        "seed_sha256": seed_fingerprint(bundle)["sha256"],
        "refreshed": len(refreshed),
        "failed": len(failures),
        "last_good_retained": repository["self_repair"][
            "failed_bills_retained"
        ],
        "recovered_from_lastgood": recovered_from_lastgood,
        "output": str(output_path),
        "transport": "GovInfo BILLSTATUS bulk XML + Congress.gov headless fallback",
        **browser_metadata,
    }


def _file_receipt(path: Path) -> dict:
    return {
        "path": str(path.resolve()),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, candidate = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".candidate",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(candidate, path)
    finally:
        if os.path.exists(candidate):
            os.unlink(candidate)


def write_refresh_receipt(
    receipt_path: Path,
    *,
    result: dict,
    bundle_path: Path,
    output_path: Path,
) -> dict:
    from legislation import sentinel_admissions as SA
    bundle = SA.augment_bundle(read_json(bundle_path), SA.load(bundle_path.parent)) if bundle_path.is_file() else {}
    seeds = seed_fingerprint(bundle) if bundle else {"count": 0, "sha256": ""}
    document = {
        "schema": REFRESH_RECEIPT_SCHEMA,
        "status": str(result.get("status") or "FAIL"),
        "completed_at": utc_now(),
        "seeded": int(result.get("seeded") or seeds["count"]),
        "refreshed": int(result.get("refreshed") or 0),
        "failed": int(result.get("failed") or 0),
        "seeds": seeds,
        "seed_identities": seed_identity_fingerprint(bundle),
        "bundle": _file_receipt(bundle_path) if bundle_path.is_file() else None,
        "repository": _file_receipt(output_path) if output_path.is_file() else None,
        "transport": result.get("transport"),
        "browser_status": result.get("browser_status"),
        "error": result.get("error"),
        "publication": result.get("publication"),
        "contract": {
            "complete_pass_required_for_publication": True,
            "partial_or_stale_refresh_authorizes_publication": False,
            "repository_and_seed_universe_hash_bound": True,
        },
    }
    _write_json_atomic(receipt_path, document)
    return document


def rebuild_dashboard(lease: dict) -> dict:
    """Run the canonical Excel/rank/surface chain under the collector's lease.

    The chain owns both "make_member_desk.py" and "make_combined.py", plus
    verified-history persistence, the Excel ledger, rank/honors propagation,
    aliases, and final audits. Rebuilding just those two pages loses the
    collector-to-workbook link and leaves ranks from the previous cycle.
    """
    from pipeline_lock import child_lock_environment

    if lease.get("adopted"):
        raise RuntimeError(
            "--rebuild-dashboard cannot run under an inherited writer lease; "
            "the lease owner must invoke run_pipeline.py --from "
            "verify_collector_roster_updates.py as its own direct child"
        )
    repo_root = Path(ROOT).resolve()
    environment = os.environ.copy()
    environment.pop("DESK_OUT", None)
    environment.pop("DASH_OUT", None)
    environment.update(child_lock_environment(lease))
    subprocess.run(
        [sys.executable, str(repo_root / "fec" / "run_pipeline.py"),
         "--from", "verify_collector_roster_updates.py"],
        cwd=repo_root, env=environment, check=True,
    )
    # The general pipeline permits an environment-only workbook skip. This
    # explicit end-to-end command may report success only for a fresh ledger.
    workbook_receipt = read_json(
        repo_root / "data" / "engine" / "sentinel_bill_workbook_receipt.json"
    )
    workbook = repo_root / "Sentinel Bill Ledger.xlsx"
    live = workbook_receipt.get("live_ledger_workbook") or {}
    if (
        workbook_receipt.get("status") != "PASS"
        or workbook_receipt.get("installed") is not True
        or workbook_receipt.get("repository_sha256")
        != sha256_file(repo_root / "bill_repository_live.json")
        or not workbook.is_file()
        or live.get("sha256") != sha256_file(workbook)
    ):
        raise RuntimeError("canonical pipeline did not publish the refreshed Excel bill ledger")
    return {
        "workbook": str(workbook),
        "member_desk": str(repo_root / "cu-member-desk.html"),
        "combined_dashboard": str(repo_root / "Moneyball.html"),
    }


def publication_preflight(lease: dict) -> None:
    """Refuse an unsafe or incomplete publication before collecting new bytes."""
    if lease.get("adopted"):
        raise RuntimeError(
            "--rebuild-dashboard cannot run under an inherited writer lease; "
            "the lease owner must invoke run_pipeline.py --from "
            "verify_collector_roster_updates.py as its own direct child"
        )
    from pipeline_lock import child_lock_environment
    from run_pipeline import preflight_dependencies
    from legislation.sync_sentinel_bill_ledger import runtime

    preflight_dependencies()
    if runtime() is None:
        raise RuntimeError("--rebuild-dashboard requires the Excel artifact-tool runtime")
    subprocess.run(
        [sys.executable, str(Path(ROOT) / "fec" / "calibration_guard.py")],
        cwd=Path(ROOT),
        env={**os.environ, **child_lock_environment(lease)},
        check=True,
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Refresh the self-repairing Congress.gov bill cache."
    )
    result.add_argument(
        "--bundle",
        type=Path,
        default=Path(ROOT) / "member_desk_bundle.json",
    )
    result.add_argument(
        "--output",
        type=Path,
        default=Path(ROOT) / "bill_repository_live.json",
    )
    result.add_argument("--workers", type=int, default=4)
    result.add_argument("--delay-ms", type=int, default=750)
    result.add_argument(
        "--profile",
        type=Path,
        help=(
            "Persistent Chrome profile (defaults to the legislation "
            "harvester profile)."
        ),
    )
    result.add_argument(
        "--chrome",
        type=Path,
        help="Chrome executable (defaults to the legislation harvester path).",
    )
    result.add_argument(
        "--limit",
        type=int,
        help="Canary: refresh only the first N bills.",
    )
    result.add_argument("--watch", action="store_true")
    result.add_argument(
        "--rebuild-dashboard",
        action="store_true",
        help=(
            "After each complete PASS refresh, verify memberships, update "
            "Excel, and run the canonical rank/dashboard publication chain."
        ),
    )
    result.add_argument("--interval-minutes", type=float, default=60.0)
    result.add_argument(
        "--receipt",
        type=Path,
        default=DEFAULT_RECEIPT,
        help="Atomic terminal refresh receipt used by production consumers.",
    )
    return result


def _rooted_path(value: Path) -> Path:
    """Resolve CLI artifact paths against the project root, never the caller's cwd."""
    return value.resolve() if value.is_absolute() else (Path(ROOT) / value).resolve()


def refresh_cycle(args: argparse.Namespace) -> tuple[dict, int]:
    """Serialize one refresh and its optional publication; never hold during sleep."""
    from pipeline_lock import pipeline_run_lock

    with pipeline_run_lock(owner_kind="bill_repository_refresh") as lease:
        if args.rebuild_dashboard:
            publication_preflight(lease)
        result = {"status": "FAIL"}
        receipt_written = False
        try:
            result = refresh_once(
                bundle_path=_rooted_path(args.bundle),
                output_path=_rooted_path(args.output),
                workers=max(1, args.workers),
                delay_ms=max(0, args.delay_ms),
                profile=args.profile.resolve() if args.profile else None,
                chrome=args.chrome.resolve() if args.chrome else None,
                limit=max(1, args.limit) if args.limit is not None else None,
            )
            # Consumers verify these exact repository bytes at import. The
            # receipt must therefore precede every downstream producer.
            if args.rebuild_dashboard and result["status"] == "PASS":
                result["publication"] = {"status": "PENDING"}
            write_refresh_receipt(
                _rooted_path(args.receipt), result=result,
                bundle_path=_rooted_path(args.bundle),
                output_path=_rooted_path(args.output),
            )
            receipt_written = True
            if args.rebuild_dashboard and result["status"] == "PASS":
                result["rebuilt"] = rebuild_dashboard(lease)
                result["publication"] = {"status": "PASS", **result["rebuilt"]}
                document = read_json(_rooted_path(args.receipt))
                document["publication"] = result["publication"]
                _write_json_atomic(_rooted_path(args.receipt), document)
            return result, 0 if result["status"] == "PASS" else 1
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if receipt_written:
                # A downstream failure is not a failed official scrape. Keep
                # its authentic PASS usable for a retry, and expose the failed
                # publication separately instead of authorizing silent success.
                result["publication"] = {"status": "FAIL", "error": error}
            else:
                result = {"status": "FAIL", "error": error}
            try:
                if receipt_written:
                    document = read_json(_rooted_path(args.receipt))
                    document["publication"] = result["publication"]
                    _write_json_atomic(_rooted_path(args.receipt), document)
                else:
                    write_refresh_receipt(
                        _rooted_path(args.receipt), result=result,
                        bundle_path=_rooted_path(args.bundle),
                        output_path=_rooted_path(args.output),
                    )
            except Exception as receipt_exc:
                result["receipt_error"] = (
                    f"{type(receipt_exc).__name__}: {receipt_exc}"
                )
            return result, 1


def main() -> int:
    argument_parser = parser()
    args = argument_parser.parse_args()
    default_bundle = (Path(ROOT) / "member_desk_bundle.json").resolve()
    default_output = (Path(ROOT) / "bill_repository_live.json").resolve()
    if args.rebuild_dashboard and (
        _rooted_path(args.output) != default_output
        or _rooted_path(args.bundle) != default_bundle
        or _rooted_path(args.receipt) != DEFAULT_RECEIPT
        or args.limit is not None
    ):
        argument_parser.error(
            "--rebuild-dashboard requires the default member bundle and "
            "bill repository output and refresh receipt so the canonical "
            "pipeline consumes the dataset that was refreshed"
        )
    while True:
        try:
            result, exit_code = refresh_cycle(args)
        except Exception as exc:
            # Lock and preflight refusals happen before this attempt owns or
            # changes collection evidence; do not overwrite another run's receipt.
            result = {
                "status": "FAIL",
                "error": f"{type(exc).__name__}: {exc}",
            }
            exit_code = 1
        print(json.dumps(result, sort_keys=True))
        if not args.watch:
            return exit_code
        time.sleep(max(60.0, args.interval_minutes * 60.0))


if __name__ == "__main__":
    raise SystemExit(main())
