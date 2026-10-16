#!/usr/bin/env python3
"""Load and validate the frozen evidence used by the original review method.

The bundle is deliberately read-only.  It supplies category boundaries and
candidate-specific amendment context to the proposal lane, while keeping each
source's evidentiary role explicit:

* operative amendment text is ``official_dispositive``;
* attributed floor remarks and parent identity are ``official_context_only``;
* trusted press is ``corroborative_only`` (with institutional direction usable
  only when an America's Credit Unions article is explicit);
* letters are a separate preservation track and never bill-role evidence.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

try:
    from legislation_measure_key import (
        AMENDMENT_MEASURE_TYPES,
        CANONICAL_KEY_RE,
        parse_canonical_key,
    )
except ImportError:  # pragma: no cover - package import path
    from fec.legislation_measure_key import (
        AMENDMENT_MEASURE_TYPES,
        CANONICAL_KEY_RE,
        parse_canonical_key,
    )


BUNDLE_SCHEMA = "acu-legislation-original-method-evidence-bundle-v1"
METHOD_REGISTRY_SCHEMA = "acu-legislation-original-method-category-registry-v1"
SUPPLEMENTAL_SCHEMA = "acu-legislation-original-method-candidate-evidence-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PARENT_RE = re.compile(
    r"\b(?:Amends\s+Bill:\s*)?"
    r"(?P<type>H\.?\s*R\.?|H\.?\s*J\.?\s*Res\.?|H\.?\s*Con\.?\s*Res\.?|"
    r"H\.?\s*Res\.?|S\.?|S\.?\s*J\.?\s*Res\.?|S\.?\s*Con\.?\s*Res\.?|"
    r"S\.?\s*Res\.?)\s*(?P<number>[1-9]\d*)\b",
    re.I,
)
TYPE_ALIASES = {
    "hr": "hr",
    "hjr": "hjres",
    "hjres": "hjres",
    "hconr": "hconres",
    "hconres": "hconres",
    "hres": "hres",
    "s": "s",
    "sjr": "sjres",
    "sjres": "sjres",
    "sconr": "sconres",
    "sconres": "sconres",
    "sres": "sres",
}
CATEGORY_ID_ALIASES = {
    "ncua_budget_share_insurance_fund": "ncua_budget_share_insurance",
    "telephone_consumer_protection_act_issues": "telephone_consumer_protection_act",
}
# Every bundle file this class reads must be listed (and so hash-verified) in
# the manifest; a file the manifest omits would otherwise be consumed unchecked.
REQUIRED_BUNDLE_FILES = (
    "query_guard_registry_v2.json",
    "acu_category_registry.json",
    "amendment_content_v2.ndjson",
    "amendment_review_queue.ndjson",
    "floor_speech_rescue_310.json",
    "parent_titles.json",
    "press_text_cache.jsonl",
    "press_stance_final.csv",
    "press_amendment_pairs.json",
    "manual_bill_evidence.pre_adjudication.csv",
    "acu_letters_snapshot_20260725.json",
)


class OriginalMethodEvidenceError(RuntimeError):
    """The frozen method bundle is missing, corrupt, or semantically invalid."""


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OriginalMethodEvidenceError(f"cannot read JSON {path}: {exc}") from exc


def iter_ndjson(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise OriginalMethodEvidenceError(
                        f"{path}:{line_number} is not an object"
                    )
                yield value
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OriginalMethodEvidenceError(f"cannot read NDJSON {path}: {exc}") from exc


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def text_block(value: Any, *, ceiling: int = 60_000) -> dict[str, Any]:
    full = clean_text(value)
    encoded = full.encode("utf-8")
    clipped = full
    truncated = False
    if len(encoded) > ceiling:
        clipped = encoded[:ceiling].decode("utf-8", errors="ignore")
        truncated = True
    return {
        "text": clipped,
        "full_text_sha256": sha256_bytes(encoded),
        "full_text_bytes": len(encoded),
        "excerpt_truncated": truncated,
    }


def normalize_parent_key(congress: int, value: Any) -> str:
    text = clean_text(value)
    if CANONICAL_KEY_RE.fullmatch(text.casefold()):
        return text.casefold()
    match = PARENT_RE.search(text)
    if not match:
        return ""
    compact = re.sub(r"[^a-z]", "", match.group("type").casefold())
    measure_type = TYPE_ALIASES.get(compact)
    if not measure_type:
        return ""
    key = f"{congress}:{measure_type}:{int(match.group('number'))}"
    return key if CANONICAL_KEY_RE.fullmatch(key) else ""


class OriginalMethodEvidence:
    """Hash-verified view over one frozen original-method bundle."""

    def __init__(self, root: Path, *, category_ids: Sequence[str]) -> None:
        self.root = root.resolve()
        self.manifest_path = self.root / "manifest.json"
        self.manifest = read_json(self.manifest_path)
        if self.manifest.get("schema") != BUNDLE_SCHEMA:
            raise OriginalMethodEvidenceError("unexpected original-method bundle schema")
        self.manifest_sha256 = sha256_file(self.manifest_path)
        self.file_receipts: dict[str, dict[str, Any]] = {}
        for name, declared in (self.manifest.get("files") or {}).items():
            if not isinstance(declared, Mapping):
                raise OriginalMethodEvidenceError(f"invalid manifest entry for {name}")
            path = self.root / str(name)
            if not path.is_file():
                raise OriginalMethodEvidenceError(f"bundle file absent: {path}")
            expected_bytes = int(declared.get("bytes") or -1)
            expected_sha = str(declared.get("sha256") or "")
            actual_bytes = path.stat().st_size
            actual_sha = sha256_file(path)
            if actual_bytes != expected_bytes or actual_sha != expected_sha:
                raise OriginalMethodEvidenceError(
                    f"bundle file drift: {name} bytes={actual_bytes} sha256={actual_sha}"
                )
            self.file_receipts[name] = {
                "path": str(path),
                "bytes": actual_bytes,
                "sha256": actual_sha,
                "role": declared.get("role"),
                "records": declared.get("records"),
            }
        missing = [n for n in REQUIRED_BUNDLE_FILES if n not in self.file_receipts]
        if missing:
            raise OriginalMethodEvidenceError(
                f"manifest omits required bundle files: {', '.join(missing)}"
            )

        guards = read_json(self.root / "query_guard_registry_v2.json")
        north_star = read_json(self.root / "acu_category_registry.json")
        self.method_registry = self._build_method_registry(
            guards, north_star, category_ids
        )
        self.method_registry_sha256 = sha256_bytes(
            canonical_bytes(self.method_registry)
        )

        self.amendments = self._index_by_key(
            iter_ndjson(self.root / "amendment_content_v2.ndjson"),
            "amendment_content_v2.ndjson",
        )
        self.review_queue = self._index_by_key(
            iter_ndjson(self.root / "amendment_review_queue.ndjson"),
            "amendment_review_queue.ndjson",
        )
        self.floor_rescue = self._index_by_key(
            read_json(self.root / "floor_speech_rescue_310.json"),
            "floor_speech_rescue_310.json",
        )
        self.parent_titles = read_json(self.root / "parent_titles.json")
        if not isinstance(self.parent_titles, dict):
            raise OriginalMethodEvidenceError("parent_titles.json is not an object")

        self.press_text = self._index_press_text()
        self.press_stance = self._index_press_stance()
        self.press_by_key = self._index_press_pairs()
        self.preservation = self._build_preservation_receipt()

    @staticmethod
    def _index_by_key(
        rows: Iterable[Mapping[str, Any]], source: str
    ) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = clean_text(row.get("canonical_key")).casefold()
            if not CANONICAL_KEY_RE.fullmatch(key):
                raise OriginalMethodEvidenceError(
                    f"{source} has invalid canonical_key {key!r}"
                )
            if key in out:
                raise OriginalMethodEvidenceError(f"{source} duplicates {key}")
            out[key] = dict(row)
        return out

    def _build_method_registry(
        self,
        guards: Mapping[str, Any],
        north_star: Mapping[str, Any],
        category_ids: Sequence[str],
    ) -> dict[str, Any]:
        guard_rows = guards.get("categories") or []
        if not isinstance(guard_rows, list):
            raise OriginalMethodEvidenceError("guard categories are not an array")
        by_id: dict[str, dict[str, Any]] = {}
        for row in guard_rows:
            if not isinstance(row, Mapping):
                continue
            source_id = clean_text(row.get("category_id"))
            category_id = CATEGORY_ID_ALIASES.get(source_id, source_id)
            item = dict(row)
            item["source_category_id"] = source_id
            item["category_id"] = category_id
            by_id[category_id] = item
        north_by_name: dict[str, dict[str, Any]] = {}
        for section in north_star.get("sections") or []:
            for category in (section or {}).get("categories") or []:
                if isinstance(category, Mapping):
                    north_by_name[clean_text(category.get("category"))] = dict(category)
        expected = list(category_ids)
        if len(expected) != 19 or len(set(expected)) != 19:
            raise OriginalMethodEvidenceError("method registry requires nineteen categories")
        if set(expected) != set(by_id):
            missing = sorted(set(expected) - set(by_id))
            extra = sorted(set(by_id) - set(expected))
            raise OriginalMethodEvidenceError(
                f"method/category mismatch missing={missing} extra={extra}"
            )

        categories: list[dict[str, Any]] = []
        for category_id in expected:
            guard = by_id[category_id]
            north = north_by_name.get(clean_text(guard.get("category"))) or {}
            named_bills = []
            for bill in north.get("bills") or []:
                if not isinstance(bill, Mapping):
                    continue
                named_bills.append(
                    {
                        "title": bill.get("title"),
                        "bill_numbers": bill.get("bill_numbers") or [],
                        "positions": bill.get("positions") or [],
                        "raw": bill.get("raw"),
                    }
                )
            categories.append(
                {
                    "category_id": category_id,
                    "label": guard.get("category"),
                    "section": guard.get("section"),
                    "terms": guard.get("terms") or [],
                    "exclusions": guard.get("exclusions") or [],
                    "named_bills": named_bills,
                    "flags": north.get("flags") or {},
                    "boundary_rule": (
                        "Terms and named bills define the review boundary and "
                        "recall probes; a match is not itself an include decision."
                    ),
                }
            )
        return {
            "schema": METHOD_REGISTRY_SCHEMA,
            "source_bundle_manifest_sha256": self.manifest_sha256,
            "source_guard_registry_sha256": self.file_receipts[
                "query_guard_registry_v2.json"
            ]["sha256"],
            "source_north_star_registry_sha256": self.file_receipts[
                "acu_category_registry.json"
            ]["sha256"],
            "category_count": len(categories),
            "review_order": [
                "official_title",
                "official_summary_including_govinfo",
                "official_bill_or_amendment_text",
            ],
            "categories": categories,
        }

    def _index_press_text(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for row in iter_ndjson(self.root / "press_text_cache.jsonl"):
            url = clean_text(row.get("url"))
            if not url:
                continue
            body = clean_text(row.get("text"))
            out[url.rstrip("/")] = {
                **text_block(body, ceiling=30_000),
                "declared_characters": row.get("n"),
            }
        return out

    def _index_press_stance(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        path = self.root / "press_stance_final.csv"
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                url = clean_text(row.get("url"))
                if url:
                    out[url.rstrip("/")] = dict(row)
        return out

    def _index_press_pairs(self) -> dict[str, list[dict[str, Any]]]:
        pairs = read_json(self.root / "press_amendment_pairs.json")
        if not isinstance(pairs, list):
            raise OriginalMethodEvidenceError("press amendment pairs are not an array")
        out: dict[str, list[dict[str, Any]]] = {}
        for index, row in enumerate(pairs):
            if not isinstance(row, list) or len(row) < 7:
                raise OriginalMethodEvidenceError(f"invalid press pair at index {index}")
            key = clean_text(row[0]).casefold()
            if not CANONICAL_KEY_RE.fullmatch(key):
                raise OriginalMethodEvidenceError(f"press pair has invalid key {key!r}")
            url = clean_text(row[2])
            stance = self.press_stance.get(url.rstrip("/"), {})
            body = self.press_text.get(url.rstrip("/"), {})
            domain = urlparse(url).netloc.casefold()
            institutional = domain.endswith("americascreditunions.org")
            out.setdefault(key, []).append(
                {
                    "article_index": index,
                    "parent_display": row[1],
                    "url": url,
                    "title": row[3],
                    "article_date": row[4],
                    "amendment_date": row[5],
                    "linker_label": row[6],
                    "publisher_kind": (
                        "americas_credit_unions" if institutional else "member_office"
                    ),
                    "evidence_class": "corroborative_only",
                    "permitted_use": (
                        "institutional direction when the article explicitly "
                        "names this amendment"
                        if institutional
                        else "member intent only when speaker and amendment are explicit"
                    ),
                    "computed_stack_label": stance.get("stack_label"),
                    "computed_debate_stance": stance.get("debate_stance"),
                    "body": body,
                }
            )
        return out

    def _build_preservation_receipt(self) -> dict[str, Any]:
        path = self.root / "manual_bill_evidence.pre_adjudication.csv"
        rows: list[dict[str, str]] = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        letter_rows = [row for row in rows if clean_text(row.get("Bill")).startswith("LTR-")]
        amendment_rows = [
            row
            for row in rows
            if clean_text(row.get("Bill")).startswith(("H.Amdt.", "S.Amdt."))
        ]
        letter_ids = {clean_text(row.get("Bill")) for row in letter_rows}
        return {
            "manual_evidence_control_sha256": self.file_receipts[
                "manual_bill_evidence.pre_adjudication.csv"
            ]["sha256"],
            "manual_evidence_row_count": len(rows),
            "letter_row_count": len(letter_rows),
            "letter_id_count": len(letter_ids),
            "letter_member_count": len(
                {clean_text(row.get("Member")) for row in letter_rows}
            ),
            "amendment_row_count": len(amendment_rows),
            "amendment_blank_direction_count": sum(
                not clean_text(row.get("Direction")) for row in amendment_rows
            ),
            "acu_letter_metadata_sha256": self.file_receipts[
                "acu_letters_snapshot_20260725.json"
            ]["sha256"],
            "acu_letter_metadata_row_count": int(
                self.manifest["files"]["acu_letters_snapshot_20260725.json"].get(
                    "records"
                )
                or 0
            ),
            "role_rule": (
                "Congressional-letter lead/signatory and ACU-letter metadata "
                "never become bill Sponsor or Original Cosponsor roles."
            ),
        }

    def candidate_evidence(self, canonical_key: str) -> dict[str, Any] | None:
        key = clean_text(canonical_key).casefold()
        congress, measure_type, _ = parse_canonical_key(key)
        if measure_type not in AMENDMENT_MEASURE_TYPES:
            return None
        row = self.amendments.get(key)
        review = self.review_queue.get(key) or {}
        rescue = self.floor_rescue.get(key)
        press = self.press_by_key.get(key, [])
        if row is None:
            return {
                "schema": SUPPLEMENTAL_SCHEMA,
                "canonical_key": key,
                "measure_kind": "amendment",
                "source_bundle_manifest_sha256": self.manifest_sha256,
                "official_amendment_text": {
                    "status": "missing_from_frozen_recovery",
                    "evidence_class": "official_dispositive",
                    "absence_is_neutral": True,
                },
                "parent_context": None,
                "sponsor_floor_remarks": None,
                "floor_speech_rescue": rescue,
                "press": press,
            }

        declared_sha = clean_text(row.get("amendment_text_sha256"))
        body_raw = str(row.get("amendment_text") or "")
        body = clean_text(body_raw)
        # The frozen recovery explicitly hashes an unavailable body as the empty
        # UTF-8 payload.  Verify that declaration too; an empty body is still
        # unavailable evidence, but it must not be mistaken for hash drift.
        actual_sha = sha256_bytes(body_raw.encode("utf-8"))
        if declared_sha and actual_sha != declared_sha:
            raise OriginalMethodEvidenceError(
                f"amendment text hash drift for {key}: {actual_sha} != {declared_sha}"
            )
        parent_key = normalize_parent_key(
            congress, row.get("amends") or review.get("parent_bill")
        )
        remarks = clean_text(row.get("floor_remarks"))
        official_text: dict[str, Any] = {
            "status": "available" if body else "unavailable",
            "evidence_class": "official_dispositive",
            "absence_is_neutral": True,
            "source_kind": "congressional_record_from_archived_congress_gov_amendment_page",
            "declared_text_sha256": declared_sha or None,
            "text_unresolved_reason": row.get("text_unresolved_reason"),
        }
        if body:
            official_text.update(text_block(body, ceiling=240_000))
        floor: dict[str, Any] | None = None
        if remarks:
            floor = {
                "evidence_class": "official_context_only",
                "permitted_use": (
                    "attributed sponsor intent; never a substitute for operative "
                    "text and never parent-role inheritance"
                ),
                **text_block(remarks, ceiling=60_000),
            }
        parent = None
        if parent_key:
            parent = {
                "canonical_key": parent_key,
                "title": self.parent_titles.get(parent_key),
                "evidence_class": "official_context_only",
                "direction_inheritance_permitted": False,
                "parent_vehicle_is_never_disqualifying": True,
            }
        offerer = {
            "display": row.get("sponsor"),
            "evidence_class": "role_authority_only",
            "role": "amendment_offerer",
            "parent_roles_inherited": False,
        }
        return {
            "schema": SUPPLEMENTAL_SCHEMA,
            "canonical_key": key,
            "measure_kind": "amendment",
            "source_bundle_manifest_sha256": self.manifest_sha256,
            "official_amendment_text": official_text,
            "parent_context": parent,
            "amendment_offerer": offerer,
            "latest_action": row.get("latest_action"),
            "sponsor_floor_remarks": floor,
            "floor_speech_rescue": (
                {
                    **dict(rescue),
                    "evidence_class": "official_context_only",
                    "permitted_use": "attributed sponsor intent only",
                }
                if rescue
                else None
            ),
            "press": press,
            "review_provenance": {
                "queued": bool(review),
                "review_tier": review.get("review_tier"),
                "why_queued": review.get("why_queued"),
                "guarded_categories": review.get("guarded_categories") or [],
                "guarded_terms": review.get("guarded_terms") or {},
                "disposition": review.get("disposition"),
            },
            "fixed_downstream_weight": 0.5,
        }

    def receipt(self) -> dict[str, Any]:
        amendment_status = Counter()
        for row in self.amendments.values():
            amendment_status[
                "text_available" if clean_text(row.get("amendment_text")) else "text_unavailable"
            ] += 1
            if clean_text(row.get("floor_remarks")):
                amendment_status["floor_remarks_available"] += 1
        return {
            "schema": BUNDLE_SCHEMA,
            "root": str(self.root),
            "manifest_path": str(self.manifest_path),
            "manifest_sha256": self.manifest_sha256,
            "method_registry_sha256": self.method_registry_sha256,
            "category_count": self.method_registry["category_count"],
            "amendment_count": len(self.amendments),
            "review_queue_count": len(self.review_queue),
            "floor_rescue_count": len(self.floor_rescue),
            "press_pair_count": sum(len(rows) for rows in self.press_by_key.values()),
            "press_key_count": len(self.press_by_key),
            "amendment_status": dict(sorted(amendment_status.items())),
            "preservation": self.preservation,
            "files": self.file_receipts,
        }
