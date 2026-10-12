# -*- coding: utf-8 -*-
"""Congress.gov bill-cache helpers with fail-safe last-good semantics."""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Mapping


SCHEMA = "congress-bill-repository-v1"
REFRESH_RECEIPT_SCHEMA = "congress-bill-refresh-receipt-v1"
API_ROOT = "https://api.congress.gov/v3"
SOURCE_PAGE = "https://www.congress.gov/"

_BILL_TYPES = {
    "HR": ("H.R.", "house-bill"),
    "S": ("S.", "senate-bill"),
    "HRES": ("H.Res.", "house-resolution"),
    "SRES": ("S.Res.", "senate-resolution"),
    "HJRES": ("H.J.Res.", "house-joint-resolution"),
    "SJRES": ("S.J.Res.", "senate-joint-resolution"),
    "HCONRES": ("H.Con.Res.", "house-concurrent-resolution"),
    "SCONRES": ("S.Con.Res.", "senate-concurrent-resolution"),
}
_AMENDMENT_TYPES = {
    "HAMDT": "H.Amdt.",
    "SAMDT": "S.Amdt.",
}


class RepositoryError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json_sha256(value: object) -> str:
    body = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def seed_fingerprint(bundle: Mapping[str, Any]) -> dict[str, Any]:
    seeds = seeds_from_member_bundle(bundle)
    return {
        "count": len(seeds),
        "sha256": stable_json_sha256(seeds),
    }


def seed_identity_keys(bundle: Mapping[str, Any]) -> list[str]:
    """The refresh universe is bill identity, independent of display metadata."""
    return sorted({parse_bill_identity(row['bill'], row['congress'])['key']
                   for row in seeds_from_member_bundle(bundle)})


def seed_identity_fingerprint(bundle: Mapping[str, Any]) -> dict[str, Any]:
    keys = seed_identity_keys(bundle)
    return {'count': len(keys), 'sha256': stable_json_sha256(keys)}


def validate_live_repository(
    bundle_path: Path,
    repository_path: Path,
    receipt_path: Path,
    *,
    max_age_seconds: int = 8 * 24 * 60 * 60,
) -> dict[str, Any]:
    """Load a live bill repository only when its weekly receipt is current.

    The logical seed fingerprint remains stable after the member desk embeds
    the refreshed repository, unlike the whole bundle file hash.  This lets a
    receipt authorize the same bill universe throughout the weekly cycle while
    still refusing a newly changed legislative universe until it is scraped.
    """
    for label, path in (
        ("member bundle", bundle_path),
        ("bill repository", repository_path),
        ("bill refresh receipt", receipt_path),
    ):
        if not path.is_file():
            raise RepositoryError(f"{label} is missing: {path}")
    with bundle_path.open(encoding="utf-8") as stream:
        bundle = json.load(stream)
    from legislation import sentinel_admissions as SA
    bundle = SA.augment_bundle(bundle, SA.load(bundle_path.parent))
    with repository_path.open(encoding="utf-8") as stream:
        repository = json.load(stream)
    with receipt_path.open(encoding="utf-8") as stream:
        receipt = json.load(stream)
    validate_repository(repository)
    if not isinstance(receipt, Mapping):
        raise RepositoryError("bill refresh receipt must be an object")
    if receipt.get("schema") != REFRESH_RECEIPT_SCHEMA:
        raise RepositoryError("bill refresh receipt schema mismatch")
    if receipt.get("status") != "PASS" or int(receipt.get("failed") or 0):
        raise RepositoryError("bill refresh receipt is not a complete PASS")
    completed_at = str(receipt.get("completed_at") or "")
    try:
        completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RepositoryError("bill refresh receipt timestamp is invalid") from exc
    age = (datetime.now(timezone.utc) - completed.astimezone(timezone.utc)).total_seconds()
    if age < -300 or age > max_age_seconds:
        raise RepositoryError(
            f"bill refresh receipt is outside the weekly freshness window: {age:.0f}s"
        )
    repository_receipt = receipt.get("repository") or {}
    if (
        repository_receipt.get("sha256") != sha256_file(repository_path)
        or int(repository_receipt.get("size") or -1) != repository_path.stat().st_size
    ):
        raise RepositoryError("bill repository is not hash-bound to its refresh receipt")
    seeds = seed_fingerprint(bundle)
    receipt_seeds = receipt.get("seeds") or {}
    if 'seed_identities' in receipt:
        identities = receipt['seed_identities']
        if (not isinstance(identities, Mapping)
                or type(identities.get('count')) is not int
                or identities['count'] < 0
                or not isinstance(identities.get('sha256'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', identities['sha256'])):
            raise RepositoryError('bill refresh identity binding is malformed')
        if dict(identities) != seed_identity_fingerprint(bundle):
            raise RepositoryError('bill refresh receipt is bound to a different bill universe')
    elif seeds != {
            "count": int(receipt_seeds.get("count") or -1),
            "sha256": str(receipt_seeds.get("sha256") or ""),
        }:
        # Legacy receipts remain strict until an exact-source migration or a
        # new official refresh supplies an explicit identity binding.
        raise RepositoryError("bill refresh receipt is bound to a different bill universe")
    if not set(seed_identity_keys(bundle)).issubset(repository.get('bills') or {}):
        raise RepositoryError("bill repository does not cover the complete seeded bill universe")
    return repository


def _clean_measure(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def parse_bill_identity(name: object, congress: object) -> dict[str, Any]:
    normalized = _clean_measure(name)
    match = re.fullmatch(
        r"(HCONRES|SCONRES|HJRES|SJRES|HRES|SRES|HR|S)(\d+)",
        normalized,
    )
    if not match:
        raise ValueError(f"unsupported bill identity: {name!r}")
    try:
        congress_number = int(congress)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid congress number: {congress!r}") from exc
    if congress_number <= 0:
        raise ValueError(f"invalid congress number: {congress!r}")
    bill_type, number = match.groups()
    api_type = bill_type.casefold()
    display_prefix, page_slug = _BILL_TYPES[bill_type]
    number_value = int(number)
    return {
        "key": f"{congress_number}:{api_type}:{number_value}",
        "congress": congress_number,
        "type": api_type,
        "number": number_value,
        "display": f"{display_prefix}{number_value}",
        "url": (
            f"https://www.congress.gov/bill/{congress_number}th-congress/"
            f"{page_slug}/{number_value}"
        ),
        "api_path": (
            f"/bill/{congress_number}/{api_type}/{number_value}"
        ),
    }


def _strip_html(value: object) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _person(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": str(row.get("fullName") or row.get("name") or "").strip(),
        "bioguide": str(row.get("bioguideId") or "").strip(),
        "party": str(row.get("party") or "").strip(),
        "state": str(row.get("state") or "").strip(),
        "district": row.get("district"),
        "date": str(row.get("sponsorshipDate") or "").strip(),
        "withdrawn_date": str(
            row.get("sponsorshipWithdrawnDate") or ""
        ).strip(),
    }


def _amendment(row: Mapping[str, Any], congress: int) -> dict[str, Any]:
    amendment_type = str(row.get("type") or "").upper()
    number = str(row.get("number") or "").strip()
    prefix = _AMENDMENT_TYPES.get(amendment_type, amendment_type + ".")
    action = row.get("latestAction") or {}
    if not isinstance(action, Mapping):
        action = {}
    amendment_congress = int(row.get("congress") or congress)
    api_type = amendment_type.casefold()
    url = str(row.get("url") or "").strip()
    if url.startswith(API_ROOT):
        url = (
            f"https://www.congress.gov/amendment/"
            f"{amendment_congress}th-congress/"
            f"{'house' if amendment_type.startswith('H') else 'senate'}-"
            f"amendment/{number}"
        )
    return {
        "display": f"{prefix}{number}",
        "congress": amendment_congress,
        "type": api_type,
        "number": number,
        "description": _strip_html(
            row.get("description")
            or row.get("purpose")
            or row.get("text")
        ),
        "latest_action": {
            "date": str(action.get("actionDate") or "").strip(),
            "text": _strip_html(action.get("text")),
        },
        "url": url,
    }


def normalize_bill_snapshot(
    seed: Mapping[str, Any],
    detail_payload: Mapping[str, Any],
    cosponsor_payload: Mapping[str, Any],
    amendment_payload: Mapping[str, Any],
    summary_payload: Mapping[str, Any] | None = None,
    *,
    updated_at: str | None = None,
) -> dict[str, Any]:
    identity = parse_bill_identity(seed.get("bill"), seed.get("congress"))
    bill = detail_payload.get("bill") or {}
    if not isinstance(bill, Mapping):
        raise RepositoryError(f"{identity['key']}: missing bill detail")
    if (
        int(bill.get("congress") or identity["congress"])
        != identity["congress"]
        or str(bill.get("type") or identity["type"]).casefold()
        != identity["type"]
        or int(bill.get("number") or identity["number"])
        != identity["number"]
    ):
        raise RepositoryError(
            f"{identity['key']}: Congress.gov identity mismatch"
        )
    latest_action = bill.get("latestAction") or {}
    if not isinstance(latest_action, Mapping):
        latest_action = {}
    sponsor_rows = bill.get("sponsors") or []
    sponsor = (
        _person(sponsor_rows[0])
        if sponsor_rows and isinstance(sponsor_rows[0], Mapping)
        else {"name": str(seed.get("sponsor") or "").strip()}
    )
    cosponsor_rows = cosponsor_payload.get("cosponsors") or []
    originals = []
    later = []
    for row in cosponsor_rows:
        if not isinstance(row, Mapping):
            continue
        packed = _person(row)
        if not packed["name"]:
            continue
        if bool(row.get("isOriginalCosponsor")):
            originals.append(packed)
        else:
            later.append(packed)
    amendment_rows = amendment_payload.get("amendments") or []
    amendments = [
        _amendment(row, identity["congress"])
        for row in amendment_rows
        if isinstance(row, Mapping)
    ]
    summaries = (summary_payload or {}).get("summaries") or []
    summaries = [row for row in summaries if isinstance(row, Mapping)]
    summaries.sort(
        key=lambda row: str(
            row.get("updateDate") or row.get("actionDate") or ""
        ),
        reverse=True,
    )
    summary = (
        _strip_html(summaries[0].get("text"))
        if summaries
        else str(seed.get("summary") or "").strip()
    )
    return {
        **identity,
        "title": str(
            bill.get("title") or seed.get("title") or identity["display"]
        ).strip(),
        "introduced_date": str(bill.get("introducedDate") or "").strip(),
        "update_date": str(
            bill.get("updateDateIncludingText")
            or bill.get("updateDate")
            or ""
        ).strip(),
        "latest_action": {
            "date": str(latest_action.get("actionDate") or "").strip(),
            "text": _strip_html(latest_action.get("text"))
            or str(seed.get("action") or "").strip(),
        },
        "sponsor": sponsor,
        "original_cosponsors": originals,
        "cosponsors": later,
        "cosponsor_count": len(originals) + len(later),
        "amendments": amendments,
        "summary": summary,
        "policy_area": str(
            (bill.get("policyArea") or {}).get("name")
            if isinstance(bill.get("policyArea"), Mapping)
            else ""
        ).strip(),
        "areas": sorted(
            {
                str(area).strip()
                for area in (seed.get("areas") or ())
                if str(area).strip()
            }
        ),
        "updated_at": updated_at or utc_now(),
        "stale": False,
        "source": "Congress.gov",
    }


def merge_refresh(
    existing: Mapping[str, Any] | None,
    refreshed: Mapping[str, Mapping[str, Any]],
    failures: Mapping[str, str],
    *,
    updated_at: str | None = None,
) -> dict[str, Any]:
    old_bills = dict((existing or {}).get("bills") or {})
    bills = {**old_bills, **dict(refreshed)}
    for key, message in failures.items():
        if key in bills:
            bills[key] = {
                **bills[key],
                "stale": True,
                "last_error": str(message),
            }
    return {
        "schema": SCHEMA,
        "updated_at": updated_at or utc_now(),
        "source": SOURCE_PAGE,
        "bills": dict(sorted(bills.items())),
        "failures": dict(sorted((str(k), str(v)) for k, v in failures.items())),
        "self_repair": {
            "last_good_preserved": True,
            "failed_bills_retained": sum(key in old_bills for key in failures),
        },
    }


def seeds_from_member_bundle(bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for member in bundle.get("members") or []:
        if not isinstance(member, Mapping):
            continue
        for bill in member.get("bills") or []:
            if not isinstance(bill, Mapping):
                continue
            try:
                identity = parse_bill_identity(
                    bill.get("bill"),
                    bill.get("congress"),
                )
            except ValueError:
                continue
            row = merged.setdefault(
                identity["key"],
                {
                    "bill": identity["display"],
                    "congress": identity["congress"],
                    "title": str(bill.get("title") or ""),
                    "sponsor": str(bill.get("sponsor") or ""),
                    "summary": str(bill.get("summary") or ""),
                    "action": str(bill.get("action") or ""),
                    "areas": [],
                },
            )
            for area in bill.get("areas") or ():
                if area and area not in row["areas"]:
                    row["areas"].append(area)
    for row in merged.values():
        row["areas"].sort()
    return [merged[key] for key in sorted(merged)]


def validate_repository(repository: Mapping[str, Any]) -> None:
    if repository.get("schema") != SCHEMA:
        raise RepositoryError("repository schema mismatch")
    bills = repository.get("bills")
    if not isinstance(bills, Mapping):
        raise RepositoryError("repository bills must be an object")
    for key, bill in bills.items():
        if not isinstance(bill, Mapping) or bill.get("key") != key:
            raise RepositoryError(f"invalid bill row: {key}")
        parse_bill_identity(bill.get("display"), bill.get("congress"))


def write_repository_atomic(
    path: Path,
    repository: Mapping[str, Any],
) -> None:
    validate_repository(repository)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            with path.open(encoding="utf-8") as stream:
                prior = json.load(stream)
            validate_repository(prior)
        except Exception:
            # Never replace a valid recovery point with a corrupt primary.
            pass
        else:
            shutil.copy2(path, path.with_suffix(path.suffix + ".lastgood"))
    body = json.dumps(
        repository,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    ) + "\n"
    handle, temporary = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".candidate",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
