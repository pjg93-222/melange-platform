#!/usr/bin/env python3
"""Candidate-level Opus adjudication of preserved legislation candidates.

The decision unit here is the **candidate**, not the membership.  One measure
is one proposal unit: every one of its nineteen category slots and every one of
its discovered memberships are adjudicated in a single call against a single
evidence envelope, so a measure is never split across batches and its bill text
is never re-sent once per membership.

Everything this program emits is a **proposal**.  ``automated_final_decision``
is always false, the adjudication state is always non-final, and the strings
``human_ratified`` and ``reverified_preexisting_project_decision`` are refused
on output.  It writes only into its own hash-addressed run directory: never the
corpus, scoring files, the junk ledger, rank outputs, integration state, sync
state, or production.

Safety properties carried over from ``run_legislation_claude_ambiguity_review``:

* a candidate whose official evidence is incomplete is terminally flagged and
  is never sent to a provider;
* every prompt carries the complete supplied evidence, never a silent clip;
* dispatch, prompt, call-start, response, and terminal-receipt artifacts are
  immutable and content-hash bound;
* a call-start marker without a terminal receipt is never replayed on resume,
  because the prior provider call may already have incurred cost;
* a conservative per-call reservation, an aggregate cost cap, and a hard
  provider-call ceiling bound paid work; and
* API keys are read only from the process environment.

Additional policy this program enforces:

* an ``include`` proposal requires affirmative official material-nexus evidence
  citing at least one official source;
* an ``exclude_false_positive`` proposal requires affirmative false-positive
  evidence and complete official operative-text review: every official bill
  version for bills, or the exact verified amendment text for amendments;
* search membership and term absence can never support either direction.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_UP
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterator, Mapping, Protocol, Sequence

try:
    from legislation_measure_key import CANONICAL_KEY_RE, measure_weight
except ImportError:  # pragma: no cover - package import path
    from fec.legislation_measure_key import CANONICAL_KEY_RE, measure_weight


PACKET_SCHEMA = "acu-legislation-opus-adjudication-packet-v2"
RUN_SCHEMA = "acu-legislation-opus-adjudication-run-v2"
DISPATCH_SCHEMA = "acu-legislation-opus-adjudication-dispatch-v2"
PROMPT_SCHEMA = "acu-legislation-opus-adjudication-prompt-v2"
CALL_SCHEMA = "acu-legislation-opus-adjudication-call-start-v2"
RESPONSE_SCHEMA = "acu-legislation-opus-adjudication-response-v2"
RECEIPT_SCHEMA = "acu-legislation-opus-adjudication-terminal-receipt-v2"
SUMMARY_SCHEMA = "acu-legislation-opus-adjudication-summary-v2"
CANARY_SCHEMA = "acu-legislation-opus-adjudication-canary-selection-v2"
CATEGORY_PROPOSAL_SCHEMA = "acu-legislation-category-evidence-proposal-v2"
MEMBERSHIP_PROPOSAL_SCHEMA = "acu-legislation-membership-adjudication-proposal-v2"
METHOD_REGISTRY_SCHEMA = "acu-legislation-original-method-category-registry-v1"
PROMPT_VERSION = "legislation-candidate-adjudication-original-method-v2"
TRANSPORT_VERSION = "claude-cli-tool-free-v1"

EXPECTED_CATEGORY_COUNT = 19
PROPOSED_STATE = "model_proposed_pending_human_ratification"
FORBIDDEN_STATES = {"human_ratified", "reverified_preexisting_project_decision"}
RETRYABLE_NO_CALL_STATUSES = {
    "cost_cap_flagged",
    "provider_call_ceiling_flagged",
}
ALLOWED_DISPOSITIONS = {"include", "exclude_false_positive", "flagged"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}
ALLOWED_NEXUS = {
    "affirmative_material_nexus",
    "affirmative_false_positive",
    "no_affirmative_evidence",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SECRET_RE = re.compile(r"(?i)(?:sk-ant-[a-z0-9_-]{8,}|ANTHROPIC_API_KEY\s*[=:]\s*\S+)")

SYSTEM_PROMPT = """You are adjudicating ONE preserved U.S. congressional measure
against the nineteen credit-union policy categories of the ACU search outline.

Use ONLY the hash-bound evidence and category method in the supplied envelope.
Review in this order: official title, official CRS or GovInfo BillStatus summary,
then operative bill or amendment text.  The category guards and named measures
define boundaries and recall probes; they do not decide inclusion by themselves.

Hard rules.

1. Search-query membership is provenance, never evidence. The fact that a query
   returned this measure can neither include nor exclude it.
2. Absence is neutral. A missing summary, a missing text version, a term that
   does not appear, or the absence of press coverage can never support an
   exclusion and can never support an inclusion.
3. The bare word "Act" is never evidence. A short acronym alias such as SMART
   Act, CLARITY Act, GENIUS Act, or TABS Act is a recall probe only; it needs
   Congress-qualified identity plus the complete official title or an operative
   provision. A base statute name such as "Federal Credit Union Act" must appear
   complete, ordered, and token-bounded, and a mere mention is unresolved until
   a localized operative action shows what the measure does to it.
4. A multi-topic measure may belong to several categories. Do not discard a
   credit-union nexus because the measure's headline subject is broader.
5. For an amendment, its own operative text controls. The parent vehicle is
   context only, is never disqualifying, and its direction and sponsor roles
   never inherit. The amendment offerer is a distinct role.
6. Attributed sponsor floor remarks may support amendment intent, but never
   establish policy domain without dispositive text. Trusted press corroborates;
   explicit America's Credit Unions language may establish institutional
   direction, but press presence alone cannot include or exclude a candidate.
   C-SPAN, interviews, and social retrieval results are admitted only when an
   exact citation, speaker, date, and measure link are supplied. Their absence
   is neutral.
7. Congressional-letter leads/signatories and ACU-letter metadata are preserved
   in a separate track. They never become bill Sponsor or Original Cosponsor
   roles. Amendment scoring weight is fixed downstream at 0.5; do not alter it.

For EVERY ONE of the nineteen category slots, including categories this measure
was never returned for, record that you reviewed the title, the summary status,
and the full text, that absence was treated as neutral, and one finding:

- affirmative_material_nexus: an official source affirmatively shows a material
  industry connection -- a title specifically about the relevant policy, a
  summary describing a material effect on credit unions, NCUA, credit-union
  members, covered institutions that include credit unions, or the category's
  regulated activity, or an operative provision that creates or changes a duty,
  authority, exemption, cost, benefit, eligibility rule, market structure,
  funding stream, or enforcement exposure material to the industry;
- affirmative_false_positive: official evidence affirmatively shows the apparent
  connection is unrelated -- a different measure sharing a short title, a
  Congress/number collision, a lexical collision such as transportation
  "interchange", a named statute appearing only in a definition, citation, or
  conforming reference, or operative provisions regulating a different industry
  that do not reach the category;
- no_affirmative_evidence: neither of the above is affirmatively shown.

For EVERY discovered membership, propose exactly one disposition:

- include: requires affirmative_material_nexus for that category;
- exclude_false_positive: requires affirmative_false_positive for that category,
  high confidence, and a complete review of every official text version;
- flagged: anything else. Uncertainty is always flagged, never an exclusion.

Everything you return is a proposal for human ratification. You are not
authorised to finalise a decision, and you must never claim one is final."""


def output_schema(category_ids: Sequence[str], query_ids: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "category_slots": {
                "type": "array",
                "minItems": EXPECTED_CATEGORY_COUNT,
                "maxItems": EXPECTED_CATEGORY_COUNT,
                "items": {
                    "type": "object",
                    "properties": {
                        "category_id": {"type": "string", "enum": list(category_ids)},
                        "title_reviewed": {"type": "boolean"},
                        "summary_status_reviewed": {"type": "boolean"},
                        "full_text_reviewed": {"type": "boolean"},
                        "absence_is_neutral": {"type": "boolean"},
                        "nexus_finding": {"type": "string", "enum": sorted(ALLOWED_NEXUS)},
                        "basis": {"type": "string"},
                        "evidence_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "uniqueItems": True,
                        },
                    },
                    "required": [
                        "category_id",
                        "title_reviewed",
                        "summary_status_reviewed",
                        "full_text_reviewed",
                        "absence_is_neutral",
                        "nexus_finding",
                        "basis",
                        "evidence_ids",
                    ],
                    "additionalProperties": False,
                },
            },
            "memberships": {
                "type": "array",
                "minItems": len(query_ids),
                "maxItems": len(query_ids),
                "items": {
                    "type": "object",
                    "properties": {
                        "query_id": {"type": "string", "enum": list(query_ids)},
                        "proposed_disposition": {
                            "type": "string",
                            "enum": sorted(ALLOWED_DISPOSITIONS),
                        },
                        "confidence": {"type": "string", "enum": sorted(ALLOWED_CONFIDENCE)},
                        "reason": {"type": "string"},
                        "reason_code": {"type": "string"},
                        "evidence_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "uniqueItems": True,
                        },
                        "full_text_rule_out_complete": {"type": "boolean"},
                        "absence_used_as_exclusion_evidence": {"type": "boolean"},
                        "matched_category_basis": {"type": "string"},
                    },
                    "required": [
                        "query_id",
                        "proposed_disposition",
                        "confidence",
                        "reason",
                        "reason_code",
                        "evidence_ids",
                        "full_text_rule_out_complete",
                        "absence_used_as_exclusion_evidence",
                        "matched_category_basis",
                    ],
                    "additionalProperties": False,
                },
            },
            "uncertainty": {"type": "string"},
        },
        "required": ["category_slots", "memberships", "uncertainty"],
        "additionalProperties": False,
    }


class AdjudicationError(RuntimeError):
    """A fail-closed packet, artifact, provider, or policy error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decimal_string(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000001"), rounding=ROUND_UP), "f")


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def redact_secrets(value: Any) -> str:
    return SECRET_RE.sub("[REDACTED]", str(value or ""))[:2000]


def read_json(path: Path) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise AdjudicationError(f"cannot read JSON {path}: {exc}") from exc
    # every caller does .get(...) on the result
    if not isinstance(value, dict):
        raise AdjudicationError(f"JSON is not an object: {path}")
    return value


def iter_packets(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        raise AdjudicationError(f"packet file does not exist: {path}")
    try:
        with path.open(encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise AdjudicationError(
                        f"invalid packet JSON at {path}:{line_number}"
                    ) from exc
                if not isinstance(value, dict):
                    raise AdjudicationError(f"packet {line_number} is not an object")
                yield value
    except (OSError, ValueError) as exc:
        raise AdjudicationError(f"cannot read packets {path}: {exc}") from exc


def retryable_no_call_keys(run_dir: Path) -> set[str]:
    receipts = run_dir.resolve() / "receipts"
    if not receipts.is_dir():
        raise AdjudicationError(f"retry source has no receipts directory: {receipts}")
    keys: set[str] = set()
    for path in sorted(receipts.glob("*.json")):
        receipt = read_json(path)
        if (
            receipt.get("status") not in RETRYABLE_NO_CALL_STATUSES
            or receipt.get("provider_call_performed") is not False
        ):
            continue
        key = clean_text(receipt.get("canonical_key")).casefold()
        if not CANONICAL_KEY_RE.fullmatch(key):
            raise AdjudicationError(f"retryable receipt has invalid key: {path}")
        if key in keys:
            raise AdjudicationError(f"retry source duplicates candidate: {key}")
        keys.add(key)
    if not keys:
        raise AdjudicationError("retry source has no safe no-call terminal receipts")
    return keys


def prior_call_keys(run_dir: Path, *, packets_sha256: str) -> set[str]:
    source = run_dir.resolve()
    manifest_path = source / "run_manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("packets_sha256") != packets_sha256:
        raise AdjudicationError(
            f"prior-call source is bound to a different packet corpus: {manifest_path}"
        )
    calls = source / "call_starts"
    if not calls.is_dir():
        raise AdjudicationError(f"prior-call source has no call_starts directory: {calls}")
    keys: set[str] = set()
    for path in sorted(calls.glob("*.json")):
        call = read_json(path)
        key = clean_text(call.get("canonical_key")).casefold()
        if not CANONICAL_KEY_RE.fullmatch(key):
            raise AdjudicationError(f"prior call start has invalid key: {path}")
        if key in keys:
            raise AdjudicationError(f"prior-call source duplicates candidate: {key}")
        keys.add(key)
    if not keys:
        raise AdjudicationError("prior-call source has no provider call starts")
    return keys


def exclusive_write(path: Path, value: Any) -> str:
    data = value if isinstance(value, bytes) else canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    # write a temp file, then hard-link it into place: link fails if path exists,
    # so a crash never leaves a truncated immutable artifact at the final path
    temp = path.with_name(f".{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise AdjudicationError(f"immutable artifact collision: {path}")
    finally:
        temp.unlink(missing_ok=True)
    return sha256_bytes(data)


def atomic_write_current(path: Path, value: Any) -> None:
    data = canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def evidence_catalog_for(packet: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Enumerate citable identifiers and their machine-enforced evidence class."""

    catalog: dict[str, dict[str, str]] = {}

    def add(evidence_id: str, evidence_class: str, permitted_use: str) -> None:
        catalog[evidence_id] = {
            "evidence_class": evidence_class,
            "permitted_use": permitted_use,
        }

    official = packet.get("official_evidence") or {}
    add("official_title", "official_dispositive", "domain and material nexus")
    summary = official.get("official_summary") or {}
    if summary.get("status") == "available":
        add(
            "official_summary",
            "official_dispositive",
            "domain and material nexus; Congress.gov CRS or GovInfo BillStatus",
        )
    elif summary.get("status") == "officially_unavailable":
        add(
            "official_summary_unavailable",
            "official_context_only",
            "neutral availability receipt; never affirmative evidence",
        )
    if (official.get("congress_gov_full_text") or {}).get("text_verified") is True:
        add(
            "congress_gov_full_text",
            "official_dispositive",
            "operative text and affirmative false-positive review",
        )
    versions = (official.get("official_text_versions") or {}).get("versions") or []
    for index, version in enumerate(versions):
        if version.get("text"):
            add(
                f"official_text_version:{version.get('version_code') or index}",
                "official_dispositive",
                "operative text and affirmative false-positive review",
            )
    for index, _ in enumerate((official.get("stage_and_actions") or {}).get("actions") or []):
        add(f"action:{index}", "official_context_only", "stage and disposition context")
    roles = official.get("relationship_roles") or {}
    for role in ("sponsors", "original_cosponsors", "cosponsors"):
        for index, _ in enumerate(roles.get(role) or []):
            add(
                f"role:{role}:{index}",
                "role_authority_only",
                f"bill {role} role only",
            )

    contextual = packet.get("contextual_and_corroborative_evidence") or {}
    amendment_text = contextual.get("official_amendment_text") or {}
    if amendment_text.get("status") == "available" and amendment_text.get("text"):
        add(
            "official_amendment_text",
            "official_dispositive",
            "operative amendment text; parent identity does not control",
        )
    if contextual.get("parent_context"):
        add(
            "amendment_parent_context",
            "official_context_only",
            "parent identity/title only; no role or direction inheritance",
        )
    if contextual.get("amendment_offerer"):
        add(
            "amendment_offerer",
            "role_authority_only",
            "amendment offerer role only",
        )
    if contextual.get("sponsor_floor_remarks"):
        add(
            "sponsor_floor_remarks",
            "official_context_only",
            "attributed amendment intent; never domain alone",
        )
    if contextual.get("floor_speech_rescue"):
        add(
            "floor_speech_rescue",
            "official_context_only",
            "attributed amendment intent; never domain alone",
        )
    for index, item in enumerate(contextual.get("press") or []):
        publisher = str(item.get("publisher_kind") or "")
        add(
            f"trusted_press:{index}",
            "corroborative_only",
            (
                "explicit institutional direction when exact amendment is named"
                if publisher == "americas_credit_unions"
                else "member intent when exact speaker and amendment are named"
            ),
        )
    return catalog


def evidence_ids_for(packet: Mapping[str, Any]) -> list[str]:
    return list(evidence_catalog_for(packet))


def load_method_registry(packets_path: Path) -> dict[str, Any] | None:
    path = packets_path.parent / "category_method_registry.json"
    if not path.is_file():
        return None
    value = read_json(path)
    if value.get("schema") != METHOD_REGISTRY_SCHEMA:
        raise AdjudicationError("category method registry has unexpected schema")
    categories = value.get("categories")
    if not isinstance(categories, list) or len(categories) != EXPECTED_CATEGORY_COUNT:
        raise AdjudicationError("category method registry is not exactly nineteen")
    ids = [str(row.get("category_id") or "") for row in categories]
    if len(set(ids)) != EXPECTED_CATEGORY_COUNT or not all(ids):
        raise AdjudicationError("category method registry ids are invalid")
    return value


def prepare_packet(
    packet: Mapping[str, Any],
    method_registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    key = clean_text(packet.get("canonical_key")).casefold()
    if packet.get("schema") != PACKET_SCHEMA:
        raise AdjudicationError(f"packet {key!r} has unexpected schema")
    if not CANONICAL_KEY_RE.fullmatch(key):
        raise AdjudicationError(f"packet has invalid canonical_key: {key!r}")
    slots = packet.get("category_slots") or []
    if len(slots) != EXPECTED_CATEGORY_COUNT:
        raise AdjudicationError(f"packet {key} does not carry nineteen category slots")
    category_ids = [str(row.get("category_id") or "") for row in slots]
    if len(set(category_ids)) != EXPECTED_CATEGORY_COUNT or not all(category_ids):
        raise AdjudicationError(f"packet {key} has duplicate or blank category ids")
    memberships = packet.get("memberships") or []
    query_ids = [str(row.get("query_id") or "") for row in memberships]
    if not query_ids or len(set(query_ids)) != len(query_ids):
        raise AdjudicationError(f"packet {key} has missing or duplicate memberships")
    eligibility = packet.get("eligibility") or {}
    expected_measure_weight = measure_weight(key)
    candidate = packet.get("candidate") or {}
    packet_measure_weight = candidate.get("fixed_downstream_weight")
    try:
        packet_measure_weight = float(packet_measure_weight)
    except (TypeError, ValueError):
        raise AdjudicationError(
            f"packet {key} has no valid fixed downstream measure weight"
        )
    if packet_measure_weight != expected_measure_weight:
        raise AdjudicationError(
            f"packet {key} measure weight {packet_measure_weight} differs from "
            f"the canonical contract {expected_measure_weight}"
        )
    method_binding = packet.get("original_method") or {}
    bound_method_sha = str(method_binding.get("category_method_registry_sha256") or "")
    if bound_method_sha:
        if method_registry is None:
            raise AdjudicationError(
                f"packet {key} requires the bound category method registry"
            )
        actual_method_sha = sha256_bytes(canonical_bytes(method_registry))
        if actual_method_sha != bound_method_sha:
            raise AdjudicationError(
                f"packet {key} category method registry hash differs"
            )
        method_ids = [
            str(row.get("category_id") or "")
            for row in method_registry.get("categories") or []
        ]
        if method_ids != category_ids:
            raise AdjudicationError(
                f"packet {key} category method order differs from its slots"
            )
    else:
        # Compatibility for unit fixtures only. Production v2 packets always
        # carry a hash binding and fail closed without the sidecar.
        method_registry = {
            "schema": METHOD_REGISTRY_SCHEMA,
            "category_count": EXPECTED_CATEGORY_COUNT,
            "review_order": [
                "official_title",
                "official_summary_including_govinfo",
                "official_bill_or_amendment_text",
            ],
            "categories": [
                {
                    "category_id": row.get("category_id"),
                    "label": row.get("label"),
                    "terms": [],
                    "exclusions": [],
                    "named_bills": [],
                }
                for row in slots
            ],
        }
    evidence_catalog = evidence_catalog_for(packet)
    envelope = {
        "canonical_key": key,
        "candidate": candidate,
        "fixed_measure_weight": packet_measure_weight,
        "eligibility_constraints": {
            "include_evidence_complete": bool(
                eligibility.get("include_evidence_complete")
            ),
            "exclude_official_text_review_complete": (
                eligibility.get("exclude_official_text_review_complete")
            ),
            "exclude_all_version_review_complete": bool(
                eligibility.get("exclude_all_version_review_complete")
            ),
            "provider_dispatch_eligible": bool(
                eligibility.get("provider_dispatch_eligible")
            ),
            "blockers": sorted(
                str(item) for item in eligibility.get("blockers") or []
            ),
            "warnings": sorted(
                str(item) for item in eligibility.get("warnings") or []
            ),
        },
        "official_evidence": packet.get("official_evidence"),
        "contextual_and_corroborative_evidence": packet.get(
            "contextual_and_corroborative_evidence"
        ),
        "category_method_registry": method_registry,
        "category_slots_to_adjudicate": [
            {
                "category_id": row.get("category_id"),
                "label": row.get("label"),
                "scoring_category": row.get("scoring_category"),
                "is_discovered_membership": row.get("is_discovered_membership"),
            }
            for row in slots
        ],
        "memberships_to_adjudicate": memberships,
        "all_version_review_complete": bool(
            eligibility.get("exclude_all_version_review_complete")
        ),
        "evidence_catalog": evidence_catalog,
        "allowed_evidence_ids": list(evidence_catalog),
        "all_supplied_evidence_included": True,
        "absence_is_neutral": True,
        "search_membership_is_not_decision_evidence": True,
    }
    return {
        "canonical_key": key,
        "packet_row_id": packet.get("packet_row_id"),
        "category_ids": category_ids,
        "query_ids": query_ids,
        "envelope": envelope,
        "evidence_sha256": sha256_bytes(canonical_bytes(envelope)),
        "eligibility": dict(eligibility),
        "blockers": list(eligibility.get("blockers") or []),
        "warnings": list(eligibility.get("warnings") or []),
        "membership_count": len(query_ids),
        "expected_version_count": int(
            ((packet.get("official_evidence") or {}).get("official_text_versions") or {}).get(
                "expected_version_count"
            )
            or 0
        ),
        "measure_weight": packet_measure_weight,
        "is_amendment": packet_measure_weight != 1.0,
    }


def prompt_document(prepared: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    schema = output_schema(prepared["category_ids"], prepared["query_ids"])
    document = {
        "schema": PROMPT_SCHEMA,
        "prompt_version": PROMPT_VERSION,
        "policy": {
            "proposal_only": True,
            "automated_final_decision_permitted": False,
            "candidate_deletion_allowed": False,
            "absence_is_neutral": True,
            "search_membership_is_not_decision_evidence": True,
            "missing_evidence_must_remain_flagged": True,
            "main_corpus_write_authorized": False,
        },
        "system_prompt": SYSTEM_PROMPT,
        "output_schema": schema,
        "evidence_envelope": prepared["envelope"],
        "evidence_sha256": prepared["evidence_sha256"],
    }
    user_prompt = (
        "Adjudicate this exact evidence envelope. Return one entry for each of "
        "the nineteen category slots and exactly one entry for each discovered "
        "membership. No supplied excerpt may be ignored or treated as absent.\n\n"
        + json.dumps(prepared["envelope"], ensure_ascii=False, sort_keys=True, indent=2)
    )
    return document, user_prompt


def validate_model_output(
    value: Any, prepared: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AdjudicationError("model output is not an object")
    if set(value) != {"category_slots", "memberships", "uncertainty"}:
        raise AdjudicationError(f"model output fields differ: {sorted(value)}")
    allowed = set(prepared["envelope"]["allowed_evidence_ids"])

    slots = value.get("category_slots")
    if not isinstance(slots, list) or len(slots) != EXPECTED_CATEGORY_COUNT:
        raise AdjudicationError("model output does not carry nineteen category slots")
    seen_categories: list[str] = []
    for slot in slots:
        if not isinstance(slot, Mapping):
            raise AdjudicationError("category slot is not an object")
        category_id = str(slot.get("category_id") or "")
        if category_id not in prepared["category_ids"]:
            raise AdjudicationError(f"unknown category_id in output: {category_id!r}")
        seen_categories.append(category_id)
        if slot.get("nexus_finding") not in ALLOWED_NEXUS:
            raise AdjudicationError("category slot nexus_finding is invalid")
        for flag in (
            "title_reviewed",
            "summary_status_reviewed",
            "full_text_reviewed",
            "absence_is_neutral",
        ):
            if not isinstance(slot.get(flag), bool):
                raise AdjudicationError(f"category slot {flag} is not boolean")
        unknown = set(slot.get("evidence_ids") or []) - allowed
        if unknown:
            raise AdjudicationError(f"category slot cites unknown evidence: {sorted(unknown)}")
    if sorted(seen_categories) != sorted(prepared["category_ids"]):
        raise AdjudicationError("model output category slots are not the exact nineteen")

    memberships = value.get("memberships")
    if not isinstance(memberships, list) or len(memberships) != prepared["membership_count"]:
        raise AdjudicationError("model output membership count differs from the packet")
    seen_queries: list[str] = []
    for row in memberships:
        if not isinstance(row, Mapping):
            raise AdjudicationError("membership row is not an object")
        query_id = str(row.get("query_id") or "")
        if query_id not in prepared["query_ids"]:
            raise AdjudicationError(f"unknown query_id in output: {query_id!r}")
        seen_queries.append(query_id)
        if row.get("proposed_disposition") not in ALLOWED_DISPOSITIONS:
            raise AdjudicationError("membership proposed_disposition is invalid")
        if row.get("confidence") not in ALLOWED_CONFIDENCE:
            raise AdjudicationError("membership confidence is invalid")
        if len(clean_text(row.get("reason"))) < 12:
            raise AdjudicationError("membership reason is too short")
        if len(clean_text(row.get("reason_code"))) < 3:
            raise AdjudicationError("membership reason_code is too short")
        for flag in ("full_text_rule_out_complete", "absence_used_as_exclusion_evidence"):
            if not isinstance(row.get(flag), bool):
                raise AdjudicationError(f"membership {flag} is not boolean")
        ids = row.get("evidence_ids")
        if not isinstance(ids, list) or len(ids) != len(set(ids)):
            raise AdjudicationError("membership evidence_ids is not a unique array")
        unknown = set(ids) - allowed
        if unknown:
            raise AdjudicationError(f"membership cites unknown evidence: {sorted(unknown)}")
    if sorted(seen_queries) != sorted(prepared["query_ids"]):
        raise AdjudicationError("model output memberships are not the exact discovered set")
    return json.loads(json.dumps(value))


def policy_effective_memberships(
    model_output: Mapping[str, Any], prepared: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Downgrade any proposal the culling contract would not support."""

    slots_by_category = {
        str(slot.get("category_id")): slot
        for slot in model_output.get("category_slots") or []
    }
    evidence_catalog = prepared["envelope"].get("evidence_catalog") or {}
    if (
        prepared["eligibility"].get(
            "exclude_official_text_review_complete"
        )
        is not None
    ):
        exclude_text_review_complete = bool(
            prepared["eligibility"].get(
                "exclude_official_text_review_complete"
            )
        )
    elif prepared.get("is_amendment"):
        # Frozen v2 compatibility: a uniquely numbered amendment's verified
        # operative text is its complete official text surface. Parent-bill
        # versions never transfer to the amendment.
        exclude_text_review_complete = bool(
            prepared["eligibility"].get("include_evidence_complete")
            and "official_amendment_text" in evidence_catalog
        )
    else:
        exclude_text_review_complete = bool(
            prepared["eligibility"].get(
                "exclude_all_version_review_complete"
            )
        )

    def affirmative_official_citation(item: str) -> bool:
        return (
            (evidence_catalog.get(item) or {}).get("evidence_class")
            == "official_dispositive"
        )

    rows: list[dict[str, Any]] = []
    downgrade_counts: dict[str, int] = {}
    for row in model_output.get("memberships") or []:
        effective = dict(row)
        downgrades: list[str] = []
        disposition = str(row.get("proposed_disposition"))
        ids = set(row.get("evidence_ids") or [])
        cites_official = any(affirmative_official_citation(item) for item in ids)
        slot = slots_by_category.get(str(row.get("query_id"))) or {}
        nexus = str(slot.get("nexus_finding") or "")
        slot_ids = set(slot.get("evidence_ids") or [])
        slot_cites_official = any(
            affirmative_official_citation(item) for item in slot_ids
        )
        review_complete = all(
            slot.get(flag) is True
            for flag in (
                "title_reviewed",
                "summary_status_reviewed",
                "full_text_reviewed",
                "absence_is_neutral",
            )
        )
        if disposition == "include":
            if not prepared["eligibility"].get("include_evidence_complete"):
                downgrades.append(
                    "include_requires_complete_official_dispositive_evidence"
                )
            if not review_complete:
                downgrades.append("include_requires_complete_review_attestations")
            if nexus != "affirmative_material_nexus":
                downgrades.append("include_requires_affirmative_material_nexus")
            if not cites_official or not slot_cites_official:
                downgrades.append("include_requires_an_official_source_citation")
        elif disposition == "exclude_false_positive":
            if not review_complete:
                downgrades.append("exclude_requires_complete_review_attestations")
            if nexus != "affirmative_false_positive":
                downgrades.append("exclude_requires_affirmative_false_positive_evidence")
            if row.get("confidence") != "high":
                downgrades.append("exclude_requires_high_confidence")
            if row.get("full_text_rule_out_complete") is not True:
                downgrades.append("exclude_requires_complete_full_text_rule_out")
            if row.get("absence_used_as_exclusion_evidence") is not False:
                downgrades.append("absence_can_never_support_an_exclusion")
            if not any(
                item
                in {
                    "congress_gov_full_text",
                    "official_amendment_text",
                }
                or item.startswith("official_text_version:")
                for item in ids
            ):
                downgrades.append("exclude_requires_an_official_full_text_citation")
            if not exclude_text_review_complete:
                downgrades.append(
                    "exclude_requires_complete_official_operative_text_review"
                )
        if downgrades:
            effective["proposed_disposition"] = "flagged"
            effective["confidence"] = "low"
            effective["reason_code"] = "policy_downgraded_to_flagged"
            effective["policy_downgrades"] = sorted(set(downgrades))
            for name in set(downgrades):
                downgrade_counts[name] = downgrade_counts.get(name, 0) + 1
        else:
            effective["policy_downgrades"] = []
        rows.append(effective)
    return rows, downgrade_counts


def flagged_output(prepared: Mapping[str, Any], reasons: Sequence[str]) -> dict[str, Any]:
    reason = "Required official evidence is incomplete; candidate remains flagged."
    return {
        "category_slots": [
            {
                "category_id": category_id,
                "title_reviewed": False,
                "summary_status_reviewed": False,
                "full_text_reviewed": False,
                "absence_is_neutral": True,
                "nexus_finding": "no_affirmative_evidence",
                "basis": reason,
                "evidence_ids": [],
            }
            for category_id in prepared["category_ids"]
        ],
        "memberships": [
            {
                "query_id": query_id,
                "proposed_disposition": "flagged",
                "confidence": "low",
                "reason": reason,
                "reason_code": "required_evidence_incomplete",
                "evidence_ids": [],
                "full_text_rule_out_complete": False,
                "absence_used_as_exclusion_evidence": False,
                "matched_category_basis": "",
                "policy_downgrades": [],
            }
            for query_id in prepared["query_ids"]
        ],
        "uncertainty": "; ".join(reasons),
    }


def proposal_rows(
    prepared: Mapping[str, Any],
    *,
    effective_slots: Sequence[Mapping[str, Any]],
    effective_memberships: Sequence[Mapping[str, Any]],
    run_id: str,
    model: str,
    effort: str,
    decision_source: str,
    proposed_at: str | None = None,
) -> list[dict[str, Any]]:
    """Exactly nineteen category proposals plus one per discovered membership."""

    stamp = proposed_at or utc_now()
    proposal_measure_weight = float(prepared["measure_weight"])
    amendment_weight = (
        proposal_measure_weight if proposal_measure_weight != 1.0 else None
    )
    rows: list[dict[str, Any]] = []
    for slot in effective_slots:
        rows.append(
            {
                "schema": CATEGORY_PROPOSAL_SCHEMA,
                "canonical_key": prepared["canonical_key"],
                "category_id": slot.get("category_id"),
                "title_reviewed": slot.get("title_reviewed"),
                "summary_status_reviewed": slot.get("summary_status_reviewed"),
                "full_text_reviewed": slot.get("full_text_reviewed"),
                "absence_is_neutral": True,
                "nexus_finding": slot.get("nexus_finding"),
                "basis": slot.get("basis"),
                "evidence_ids": slot.get("evidence_ids") or [],
                "evidence_sha256": prepared["evidence_sha256"],
                "measure_weight": proposal_measure_weight,
                "amendment_weight": amendment_weight,
                "adjudication_state": PROPOSED_STATE,
                "automated_final_decision": False,
                "human_ratification_required": True,
                "decision_source": decision_source,
                "run_id": run_id,
                "model": model,
                "effort": effort,
                "proposed_at": stamp,
            }
        )
    for row in effective_memberships:
        rows.append(
            {
                "schema": MEMBERSHIP_PROPOSAL_SCHEMA,
                "canonical_key": prepared["canonical_key"],
                "query_id": row.get("query_id"),
                "proposed_disposition": row.get("proposed_disposition"),
                "confidence": row.get("confidence"),
                "reason": row.get("reason"),
                "reason_code": row.get("reason_code"),
                "evidence_ids": row.get("evidence_ids") or [],
                "full_text_rule_out_complete": row.get("full_text_rule_out_complete"),
                "absence_used_as_exclusion_evidence": row.get(
                    "absence_used_as_exclusion_evidence"
                ),
                "matched_category_basis": row.get("matched_category_basis"),
                "policy_downgrades": row.get("policy_downgrades") or [],
                "evidence_sha256": prepared["evidence_sha256"],
                "measure_weight": proposal_measure_weight,
                "amendment_weight": amendment_weight,
                "adjudication_state": PROPOSED_STATE,
                "automated_final_decision": False,
                "human_ratification_required": True,
                "decision_source": decision_source,
                "run_id": run_id,
                "model": model,
                "effort": effort,
                "proposed_at": stamp,
            }
        )
    for row in rows:
        if row["adjudication_state"] in FORBIDDEN_STATES:
            raise AdjudicationError("a proposal attempted to claim a final state")
        if row["automated_final_decision"] is not False:
            raise AdjudicationError("a proposal attempted to claim finality")
    expected = EXPECTED_CATEGORY_COUNT + prepared["membership_count"]
    if len(rows) != expected:
        raise AdjudicationError(
            f"proposal row count {len(rows)} != nineteen plus {prepared['membership_count']}"
        )
    return rows


@dataclass(frozen=True)
class ProviderResult:
    output: dict[str, Any]
    response_metadata: dict[str, Any]
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None


class Transport(Protocol):
    def review(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: Mapping[str, Any],
        model: str,
        effort: str,
        max_output_tokens: int,
        remaining_budget_usd: Decimal,
    ) -> ProviderResult:
        ...


class AnthropicApiTransport:
    """Direct Anthropic API transport; the key remains environment-only."""

    def __init__(self) -> None:
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise AdjudicationError("ANTHROPIC_API_KEY is required for anthropic-api")
        try:
            import anthropic  # type: ignore
        except ImportError as exc:
            raise AdjudicationError("the anthropic Python package is not installed") from exc
        self._client = anthropic.Anthropic(api_key=key)

    def review(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: Mapping[str, Any],
        model: str,
        effort: str,
        max_output_tokens: int,
        remaining_budget_usd: Decimal,
    ) -> ProviderResult:
        del remaining_budget_usd
        response = self._client.messages.create(
            model=model,
            max_tokens=max_output_tokens,
            thinking={"type": "adaptive"},
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": dict(schema)},
            },
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text_blocks = [
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ]
        if len(text_blocks) != 1:
            raise AdjudicationError("Anthropic response did not contain exactly one text block")
        try:
            output = json.loads(text_blocks[0])
        except json.JSONDecodeError as exc:
            raise AdjudicationError("Anthropic structured output was not JSON") from exc
        usage = response.usage
        return ProviderResult(
            output=output,
            response_metadata={
                "provider_response_id": clean_text(getattr(response, "id", "")),
                "provider_model": clean_text(getattr(response, "model", model)),
                "stop_reason": clean_text(getattr(response, "stop_reason", "")),
            },
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cost_usd=None,
        )


class ClaudeCliTransport:
    """Claude CLI transport using stdin; no prompt or secret on the command line."""

    def __init__(self, command: str) -> None:
        self.command = command

    def review(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: Mapping[str, Any],
        model: str,
        effort: str,
        max_output_tokens: int,
        remaining_budget_usd: Decimal,
    ) -> ProviderResult:
        del max_output_tokens
        command = [
            self.command,
            "-p",
            "--safe-mode",
            "--disable-slash-commands",
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--no-session-persistence",
            "--no-chrome",
            "--permission-mode",
            "dontAsk",
            "--prompt-suggestions",
            "false",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
            "--model",
            model,
            "--effort",
            effort,
            "--max-budget-usd",
            decimal_string(remaining_budget_usd),
            "--system-prompt",
            system_prompt,
        ]
        try:
            completed = subprocess.run(
                command,
                input=user_prompt,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                shell=False,
            )
        except OSError as exc:
            raise AdjudicationError(f"cannot execute Claude CLI: {redact_secrets(exc)}") from exc
        if completed.returncode != 0:
            raise AdjudicationError(
                f"Claude CLI exited {completed.returncode}: {redact_secrets(completed.stderr)}"
            )
        try:
            envelope = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise AdjudicationError("Claude CLI output envelope was not JSON") from exc
        output: Any = envelope.get("structured_output")
        if output is None:
            output = envelope.get("result")
        if isinstance(output, str):
            output = json.loads(output)
        usage = envelope.get("usage") or {}
        cost_value = envelope.get("total_cost_usd")
        cost: Decimal | None = None
        if cost_value is not None:
            try:
                cost = Decimal(str(cost_value))
            except InvalidOperation as exc:
                raise AdjudicationError("Claude CLI returned invalid total_cost_usd") from exc
        return ProviderResult(
            output=output,
            response_metadata={
                "provider_response_id": clean_text(envelope.get("session_id")),
                "provider_model": model,
                "stop_reason": clean_text(envelope.get("subtype")),
                "cli_exit_code": completed.returncode,
            },
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cost_usd=cost,
        )


def estimated_reservation(
    *,
    user_prompt: str,
    max_output_tokens: int,
    input_cost_per_million: Decimal,
    output_cost_per_million: Decimal,
    bytes_per_token: Decimal,
) -> Decimal:
    prompt_bytes = len(SYSTEM_PROMPT.encode("utf-8")) + len(user_prompt.encode("utf-8"))
    input_upper = (Decimal(prompt_bytes) / bytes_per_token).quantize(
        Decimal("1"), rounding=ROUND_UP
    )
    return (
        input_upper * input_cost_per_million
        + Decimal(max_output_tokens) * output_cost_per_million
    ) / Decimal(1_000_000)


def calculated_cost(
    result: ProviderResult,
    *,
    input_cost_per_million: Decimal,
    output_cost_per_million: Decimal,
    reservation: Decimal,
) -> Decimal:
    if result.cost_usd is not None:
        return max(Decimal("0"), result.cost_usd)
    if result.input_tokens <= 0 and result.output_tokens <= 0:
        return reservation
    return (
        Decimal(result.input_tokens) * input_cost_per_million
        + Decimal(result.output_tokens) * output_cost_per_million
    ) / Decimal(1_000_000)


@dataclass(frozen=True)
class Job:
    prepared: dict[str, Any]
    dispatch_id: str
    prompt_document: dict[str, Any]
    user_prompt: str
    prompt_sha256: str
    prompt_bytes: int
    reservation: Decimal
    run_id: str
    model: str
    effort: str


def make_job(prepared: dict[str, Any], args: argparse.Namespace) -> Job:
    document, user_prompt = prompt_document(prepared)
    prompt_sha256 = sha256_bytes(canonical_bytes(document))
    dispatch_id = sha256_bytes(
        canonical_bytes(
            {
                "canonical_key": prepared["canonical_key"],
                "evidence_sha256": prepared["evidence_sha256"],
                "prompt_sha256": prompt_sha256,
                "prompt_version": PROMPT_VERSION,
                "model": args.model,
                "provider": args.provider,
                "effort": args.effort,
                "transport_version": TRANSPORT_VERSION,
            }
        )
    )
    return Job(
        prepared=prepared,
        dispatch_id=dispatch_id,
        prompt_document=document,
        user_prompt=user_prompt,
        prompt_sha256=prompt_sha256,
        prompt_bytes=len(user_prompt.encode("utf-8")),
        reservation=estimated_reservation(
            user_prompt=user_prompt,
            max_output_tokens=args.max_output_tokens,
            input_cost_per_million=args.input_rate,
            output_cost_per_million=args.output_rate,
            bytes_per_token=args.bytes_per_token,
        ),
        run_id=args.run_id,
        model=args.model,
        effort=args.effort,
    )


def artifact_paths(run_dir: Path, dispatch_id: str) -> dict[str, Path]:
    return {
        "dispatch": run_dir / "dispatches" / f"{dispatch_id}.json",
        "prompt": run_dir / "prompts" / f"{dispatch_id}.json",
        "call": run_dir / "call_starts" / f"{dispatch_id}.json",
        "response": run_dir / "responses" / f"{dispatch_id}.json",
        "receipt": run_dir / "receipts" / f"{dispatch_id}.json",
    }


def terminal_receipt(
    job: Job,
    *,
    status: str,
    model_output: Mapping[str, Any],
    effective_slots: Sequence[Mapping[str, Any]],
    effective_memberships: Sequence[Mapping[str, Any]],
    proposals: Sequence[Mapping[str, Any]],
    provider_call_performed: bool,
    accounted_cost: Decimal,
    response_path: Path | None = None,
    response_sha256: str | None = None,
    error: str = "",
    policy_downgrades: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    proposal_copy = json.loads(json.dumps(list(proposals)))
    proposal_payload = b"".join(canonical_bytes(row) for row in proposal_copy)
    return {
        "schema": RECEIPT_SCHEMA,
        "dispatch_id": job.dispatch_id,
        "status": status,
        "terminal": True,
        "canonical_key": job.prepared["canonical_key"],
        "packet_row_id": job.prepared["packet_row_id"],
        "evidence_sha256": job.prepared["evidence_sha256"],
        "prompt_sha256": job.prompt_sha256,
        "prompt_bytes": job.prompt_bytes,
        "provider_call_performed": provider_call_performed,
        "response_path": str(response_path.resolve()) if response_path else None,
        "response_sha256": response_sha256,
        "model_output": json.loads(json.dumps(model_output)),
        "effective_category_slots": json.loads(json.dumps(list(effective_slots))),
        "effective_memberships": json.loads(json.dumps(list(effective_memberships))),
        "proposal_count": len(proposals),
        "proposal_rows": proposal_copy,
        "proposal_rows_sha256": sha256_bytes(proposal_payload),
        "category_proposal_count": sum(
            1 for row in proposals if row.get("schema") == CATEGORY_PROPOSAL_SCHEMA
        ),
        "membership_proposal_count": sum(
            1 for row in proposals if row.get("schema") == MEMBERSHIP_PROPOSAL_SCHEMA
        ),
        "policy_downgrades": dict(sorted((policy_downgrades or {}).items())),
        "blockers": list(job.prepared["blockers"]),
        "error": redact_secrets(error),
        "accounted_cost_usd": decimal_string(accounted_cost),
        "adjudication_state": PROPOSED_STATE,
        "automated_final_decision": False,
        "human_ratification_required": True,
        "candidate_deletion_performed": False,
        "absence_is_neutral": True,
        "junk_ledger_write_performed": False,
        "main_corpus_write_performed": False,
        "production_write_performed": False,
        "completed_at": utc_now(),
    }


def verify_existing_receipt(job: Job, path: Path) -> dict[str, Any]:
    receipt = read_json(path)
    if (
        receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("dispatch_id") != job.dispatch_id
        or receipt.get("terminal") is not True
        or receipt.get("evidence_sha256") != job.prepared["evidence_sha256"]
        or receipt.get("prompt_sha256") != job.prompt_sha256
    ):
        raise AdjudicationError(f"terminal receipt is not current for dispatch: {path}")
    if receipt.get("automated_final_decision") is not False:
        raise AdjudicationError(f"stored receipt claims finality: {path}")
    try:
        stored_model_output = validate_model_output(
            receipt.get("model_output"), job.prepared
        )
        expected_memberships, expected_downgrades = policy_effective_memberships(
            stored_model_output, job.prepared
        )
    except AdjudicationError as exc:
        raise AdjudicationError(
            f"stored receipt model output is not current: {path}: {exc}"
        ) from exc
    expected_slots = stored_model_output["category_slots"]
    if canonical_bytes(receipt.get("effective_category_slots")) != canonical_bytes(
        expected_slots
    ):
        raise AdjudicationError(
            f"stored receipt category results are not policy-current: {path}"
        )
    if canonical_bytes(receipt.get("effective_memberships")) != canonical_bytes(
        expected_memberships
    ):
        raise AdjudicationError(
            f"stored receipt membership results are not policy-current: {path}"
        )
    if dict(receipt.get("policy_downgrades") or {}) != dict(
        sorted(expected_downgrades.items())
    ):
        raise AdjudicationError(
            f"stored receipt policy downgrade counts are not current: {path}"
        )
    proposal_rows_value = receipt.get("proposal_rows")
    if proposal_rows_value is not None:
        if (
            not isinstance(proposal_rows_value, list)
            or len(proposal_rows_value) != receipt.get("proposal_count")
            or sha256_bytes(
                b"".join(canonical_bytes(row) for row in proposal_rows_value)
            )
            != receipt.get("proposal_rows_sha256")
        ):
            raise AdjudicationError(f"stored proposal rows are not hash-valid: {path}")
        stamps = {
            str(row.get("proposed_at") or "") for row in proposal_rows_value
        }
        expected_source = (
            "automated_opus_candidate_adjudication_proposal"
            if receipt.get("status") == "accepted"
            else (
                "automated_provider_failure_flagged"
                if receipt.get("status") == "provider_failed_flagged"
                else "automated_evidence_gate_no_provider_call"
            )
        )
        if len(stamps) != 1 or "" in stamps:
            raise AdjudicationError(
                f"stored proposal timestamps are not coherent: {path}"
            )
        expected_rows = proposal_rows(
            job.prepared,
            effective_slots=expected_slots,
            effective_memberships=expected_memberships,
            run_id=job.run_id,
            model=job.model,
            effort=job.effort,
            decision_source=expected_source,
            proposed_at=next(iter(stamps)),
        )
        if canonical_bytes(proposal_rows_value) != canonical_bytes(expected_rows):
            raise AdjudicationError(
                f"stored proposal rows are not semantically current: {path}"
            )
    response_path = receipt.get("response_path")
    if response_path:
        candidate = Path(str(response_path))
        if not candidate.is_file() or sha256_file(candidate) != receipt.get("response_sha256"):
            raise AdjudicationError(f"response artifact hash mismatch for {path}")
    return dict(receipt)


def flagged_terminal(
    job: Job, args: argparse.Namespace, *, status: str, reasons: Sequence[str]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    output = flagged_output(job.prepared, reasons)
    proposals = proposal_rows(
        job.prepared,
        effective_slots=output["category_slots"],
        effective_memberships=output["memberships"],
        run_id=args.run_id,
        model=args.model,
        effort=args.effort,
        decision_source="automated_evidence_gate_no_provider_call",
    )
    receipt = terminal_receipt(
        job,
        status=status,
        model_output=output,
        effective_slots=output["category_slots"],
        effective_memberships=output["memberships"],
        proposals=proposals,
        provider_call_performed=False,
        accounted_cost=Decimal("0"),
    )
    return receipt, proposals


def execute_provider_job(
    *,
    run_dir: Path,
    job: Job,
    args: argparse.Namespace,
    transport: Transport,
    remaining_budget: Decimal,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    paths = artifact_paths(run_dir, job.dispatch_id)
    exclusive_write(
        paths["call"],
        {
            "schema": CALL_SCHEMA,
            "dispatch_id": job.dispatch_id,
            "canonical_key": job.prepared["canonical_key"],
            "provider": args.provider,
            "model": args.model,
            "effort": args.effort,
            "prompt_sha256": job.prompt_sha256,
            "remaining_budget_usd": decimal_string(remaining_budget),
            "started_at": utc_now(),
            "secret_material_persisted": False,
        },
    )
    try:
        result = transport.review(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=job.user_prompt,
            schema=job.prompt_document["output_schema"],
            model=args.model,
            effort=args.effort,
            max_output_tokens=args.max_output_tokens,
            remaining_budget_usd=remaining_budget,
        )
        model_output = validate_model_output(result.output, job.prepared)
        memberships, downgrades = policy_effective_memberships(model_output, job.prepared)
        accounted = calculated_cost(
            result,
            input_cost_per_million=args.input_rate,
            output_cost_per_million=args.output_rate,
            reservation=job.reservation,
        )
        if accounted > remaining_budget:
            raise AdjudicationError(
                "provider-reported cost exceeded the reserved per-call budget"
            )
        proposals = proposal_rows(
            job.prepared,
            effective_slots=model_output["category_slots"],
            effective_memberships=memberships,
            run_id=args.run_id,
            model=args.model,
            effort=args.effort,
            decision_source="automated_opus_candidate_adjudication_proposal",
        )
        response = {
            "schema": RESPONSE_SCHEMA,
            "dispatch_id": job.dispatch_id,
            "provider": args.provider,
            "model": args.model,
            "effort": args.effort,
            "provider_metadata": result.response_metadata,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "calculated_cost_usd": decimal_string(accounted),
            "structured_output": model_output,
            "structured_output_sha256": sha256_bytes(canonical_bytes(model_output)),
            "secret_material_persisted": False,
            "received_at": utc_now(),
        }
        response_sha = exclusive_write(paths["response"], response)
        receipt = terminal_receipt(
            job,
            status="accepted",
            model_output=model_output,
            effective_slots=model_output["category_slots"],
            effective_memberships=memberships,
            proposals=proposals,
            provider_call_performed=True,
            accounted_cost=accounted,
            response_path=paths["response"],
            response_sha256=response_sha,
            policy_downgrades=downgrades,
        )
    except Exception as exc:  # terminalise every attempted provider dispatch
        output = flagged_output(
            job.prepared, ["provider call or structured-output validation failed"]
        )
        proposals = proposal_rows(
            job.prepared,
            effective_slots=output["category_slots"],
            effective_memberships=output["memberships"],
            run_id=args.run_id,
            model=args.model,
            effort=args.effort,
            decision_source="automated_provider_failure_flagged",
        )
        receipt = terminal_receipt(
            job,
            status="provider_failed_flagged",
            model_output=output,
            effective_slots=output["category_slots"],
            effective_memberships=output["memberships"],
            proposals=proposals,
            provider_call_performed=True,
            accounted_cost=remaining_budget,
            error=f"{type(exc).__name__}: {redact_secrets(exc)}",
        )
    exclusive_write(paths["receipt"], receipt)
    return receipt, proposals


def select_canary(
    jobs: Sequence[Job],
    *,
    size: int,
    max_prompt_bytes: int,
    eligible_pool_size: int | None = None,
) -> dict[str, Any]:
    """Deterministic canary selection: quotas first, smallest prompt first."""

    pool = [
        job
        for job in jobs
        if not job.prepared["blockers"] and job.prompt_bytes <= max_prompt_bytes
    ]
    pool.sort(key=lambda job: (job.prompt_bytes, job.prepared["canonical_key"]))
    chosen: list[Job] = []
    taken: set[str] = set()

    def take(predicate, quota: int, label: str) -> list[str]:
        picked: list[str] = []
        for job in pool:
            if len(picked) >= quota:
                break
            key = job.prepared["canonical_key"]
            if key in taken or not predicate(job):
                continue
            taken.add(key)
            chosen.append(job)
            picked.append(key)
        if len(picked) < quota:
            raise AdjudicationError(
                f"canary quota {label} needs {quota} candidates, found {len(picked)}"
            )
        return picked

    multi_version = take(lambda job: job.prepared["expected_version_count"] > 1, 5, "multi_version")
    multi_membership = take(lambda job: job.prepared["membership_count"] > 1, 5, "multi_membership")
    for job in pool:
        if len(chosen) >= size:
            break
        key = job.prepared["canonical_key"]
        if key in taken:
            continue
        taken.add(key)
        chosen.append(job)
    if len(chosen) != size:
        raise AdjudicationError(f"canary selection produced {len(chosen)} of {size}")
    chosen.sort(key=lambda job: job.prepared["canonical_key"])
    return {
        "schema": CANARY_SCHEMA,
        "size": size,
        "max_prompt_bytes": max_prompt_bytes,
        "eligible_pool_size": (
            eligible_pool_size if eligible_pool_size is not None else len(pool)
        ),
        "rule": (
            "eligible packets only, sorted by (prompt_bytes, canonical_key); "
            "five multi-version, then five multi-membership, then smallest first"
        ),
        "quota_multi_version": multi_version,
        "quota_multi_membership": multi_membership,
        "canonical_keys": [job.prepared["canonical_key"] for job in chosen],
        "jobs": chosen,
    }


def retain_smallest(
    pool: list[Job], job: Job, *, limit: int
) -> None:
    """Retain a deterministic bounded prefix by prompt size and key."""

    pool.append(job)
    pool.sort(key=lambda row: (row.prompt_bytes, row.prepared["canonical_key"]))
    if len(pool) > limit:
        del pool[limit:]


def parse_decimal(value: str, label: str, *, positive: bool = False) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"{label} must be a decimal") from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed <= 0):
        raise argparse.ArgumentTypeError(f"{label} must be finite and in range")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--provider", choices=("anthropic-api", "claude-cli"), default="anthropic-api")
    parser.add_argument("--model", default=os.environ.get("CLAUDE_CULL_MODEL", "claude-opus-5"))
    parser.add_argument(
        "--effort",
        choices=("low", "medium", "high", "xhigh", "max"),
        default=os.environ.get("CLAUDE_CULL_EFFORT", "max"),
    )
    parser.add_argument("--claude-command", default="claude")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max-output-tokens", type=int, default=6000)
    parser.add_argument("--max-prompt-bytes", type=int, default=600_000)
    parser.add_argument("--max-cost-usd", required=True)
    parser.add_argument("--max-provider-calls", type=int, default=0)
    parser.add_argument("--input-cost-per-million", default="15")
    parser.add_argument("--output-cost-per-million", default="75")
    parser.add_argument("--bytes-per-token", default="3.5")
    parser.add_argument("--canary-size", type=int, default=0)
    parser.add_argument("--retry-no-call-run", type=Path)
    parser.add_argument(
        "--exclude-prior-call-run",
        action="append",
        type=Path,
        default=[],
        help=(
            "exclude every candidate with a call-start receipt in a prior run "
            "bound to the same packet corpus; may be repeated"
        ),
    )
    parser.add_argument(
        "--minimum-claude-call-budget-usd",
        help=(
            "for claude-cli only, reserve at least this much of the aggregate "
            "cap for each dispatched call"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.run_id):
        parser.error("--run-id must be a filesystem-safe identifier")
    if args.workers < 1 or args.workers > 32:
        parser.error("--workers must be from 1 through 32")
    if args.max_output_tokens < 512:
        parser.error("--max-output-tokens must be at least 512")
    if args.max_prompt_bytes < 1024:
        parser.error("--max-prompt-bytes must be at least 1024")
    if args.max_provider_calls < 0:
        parser.error("--max-provider-calls must be nonnegative")
    if args.canary_size and args.retry_no_call_run:
        parser.error("--canary-size and --retry-no-call-run are mutually exclusive")
    try:
        args.max_cost = parse_decimal(args.max_cost_usd, "--max-cost-usd")
        args.input_rate = parse_decimal(args.input_cost_per_million, "--input-cost-per-million", positive=True)
        args.output_rate = parse_decimal(args.output_cost_per_million, "--output-cost-per-million", positive=True)
        args.bytes_per_token = parse_decimal(args.bytes_per_token, "--bytes-per-token", positive=True)
        args.minimum_claude_call_budget = (
            parse_decimal(
                args.minimum_claude_call_budget_usd,
                "--minimum-claude-call-budget-usd",
                positive=True,
            )
            if args.minimum_claude_call_budget_usd is not None
            else None
        )
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    if args.minimum_claude_call_budget is not None and args.provider != "claude-cli":
        parser.error("--minimum-claude-call-budget-usd requires --provider claude-cli")
    return args


def build_transport(args: argparse.Namespace) -> Transport:
    if args.provider == "anthropic-api":
        return AnthropicApiTransport()
    return ClaudeCliTransport(args.claude_command)


def run(args: argparse.Namespace, *, transport: Transport | None = None) -> dict[str, Any]:
    packets_path = args.packets.resolve()
    if packets_path.is_dir():
        packets_path = packets_path / "packets.ndjson"
    packets_sha = sha256_file(packets_path)
    method_registry = load_method_registry(packets_path)
    method_registry_sha = (
        sha256_bytes(canonical_bytes(method_registry))
        if method_registry is not None
        else None
    )
    excluded_prior_call_keys: set[str] = set()
    prior_call_sources: list[str] = []
    for source in args.exclude_prior_call_run:
        resolved = source.resolve()
        excluded_prior_call_keys.update(
            prior_call_keys(resolved, packets_sha256=packets_sha)
        )
        prior_call_sources.append(str(resolved))
    retry_keys = (
        retryable_no_call_keys(args.retry_no_call_run)
        if args.retry_no_call_run
        else None
    )
    jobs: list[Job] = []
    canary_smallest: list[Job] = []
    canary_multi_version: list[Job] = []
    canary_multi_membership: list[Job] = []
    canary_eligible_pool_size = 0
    keys: set[str] = set()
    candidate_count = 0
    for packet in iter_packets(packets_path):
        prepared = prepare_packet(packet, method_registry)
        key = prepared["canonical_key"]
        if key in keys:
            raise AdjudicationError("packet file carries duplicate candidates")
        keys.add(key)
        candidate_count += 1
        if key in excluded_prior_call_keys:
            continue
        if retry_keys is not None and key not in retry_keys:
            continue
        # A canary selects only dispatch-eligible candidates. Avoid retaining
        # the much larger auto-flagged population and its prompt copies.
        if args.canary_size and prepared["blockers"]:
            continue
        job = make_job(prepared, args)
        if args.canary_size and job.prompt_bytes > args.max_prompt_bytes:
            continue
        if args.canary_size:
            canary_eligible_pool_size += 1
            # The selector takes five multi-version rows, then five
            # multi-membership rows excluding those already taken, then fills
            # from the global smallest prefix.  These bounded prefixes are
            # sufficient to reproduce that result without retaining every
            # complete evidence prompt in memory.
            retain_smallest(
                canary_smallest,
                job,
                limit=args.canary_size + 10,
            )
            if job.prepared["expected_version_count"] > 1:
                retain_smallest(
                    canary_multi_version,
                    job,
                    limit=5,
                )
            if job.prepared["membership_count"] > 1:
                retain_smallest(
                    canary_multi_membership,
                    job,
                    limit=10,
                )
        else:
            jobs.append(job)
    if retry_keys is not None:
        found = {job.prepared["canonical_key"] for job in jobs}
        missing = sorted(retry_keys - found)
        if missing:
            raise AdjudicationError(
                f"retry source keys are absent or no longer dispatchable: {missing[:10]}"
            )

    canary: dict[str, Any] | None = None
    selected = jobs
    if args.canary_size:
        bounded_by_key: dict[str, Job] = {}
        for job in (
            canary_smallest + canary_multi_version + canary_multi_membership
        ):
            bounded_by_key[job.prepared["canonical_key"]] = job
        jobs = list(bounded_by_key.values())
        canary = select_canary(
            jobs,
            size=args.canary_size,
            max_prompt_bytes=args.max_prompt_bytes,
            eligible_pool_size=canary_eligible_pool_size,
        )
        canary_keys = set(canary["canonical_keys"])
        selected = [job for job in jobs if job.prepared["canonical_key"] in canary_keys]

    eligible = [
        job
        for job in selected
        if not job.prepared["blockers"] and job.prompt_bytes <= args.max_prompt_bytes
    ]
    plan = {
        "schema": RUN_SCHEMA,
        "run_id": args.run_id,
        "packets_path": str(packets_path),
        "packets_sha256": packets_sha,
        "category_method_registry_sha256": method_registry_sha,
        "candidate_count": candidate_count,
        "selected_count": len(selected),
        "provider_eligible_count": len(eligible),
        "auto_flagged_count": len(selected) - len(eligible),
        "membership_count": sum(job.prepared["membership_count"] for job in selected),
        "category_slot_count": EXPECTED_CATEGORY_COUNT * len(selected),
        "provider": args.provider,
        "model": args.model,
        "effort": args.effort,
        "workers": args.workers,
        "dry_run": bool(args.dry_run),
        "max_output_tokens": args.max_output_tokens,
        "max_prompt_bytes": args.max_prompt_bytes,
        "max_cost_usd": decimal_string(args.max_cost),
        "max_provider_calls": args.max_provider_calls or None,
        "input_cost_per_million": decimal_string(args.input_rate),
        "output_cost_per_million": decimal_string(args.output_rate),
        "bytes_per_token": decimal_string(args.bytes_per_token),
        "rate_provenance": (
            "operator-supplied planning rates; the provider remains the billing "
            "authority and the aggregate cap is the hard control"
        ),
        "prompt_version": PROMPT_VERSION,
        "transport_version": TRANSPORT_VERSION,
        "canary_keys": canary["canonical_keys"] if canary else None,
        "retry_no_call_run": (
            str(args.retry_no_call_run.resolve()) if args.retry_no_call_run else None
        ),
        "retryable_no_call_statuses": (
            sorted(RETRYABLE_NO_CALL_STATUSES) if args.retry_no_call_run else None
        ),
        "excluded_prior_call_runs": prior_call_sources or None,
        "excluded_prior_call_key_count": len(excluded_prior_call_keys),
        "excluded_prior_call_keys_sha256": (
            sha256_bytes(canonical_bytes(sorted(excluded_prior_call_keys)))
            if excluded_prior_call_keys
            else None
        ),
        "minimum_claude_call_budget_usd": (
            decimal_string(args.minimum_claude_call_budget)
            if args.minimum_claude_call_budget is not None
            else None
        ),
        "automated_final_decision_permitted": False,
        "main_corpus_write_authorized": False,
    }
    plan["binding_sha256"] = sha256_bytes(canonical_bytes(plan))
    if args.plan_only:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return plan

    run_dir = args.out.resolve() / "runs" / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    exclusive_write(run_dir / "run_manifest.json", plan)
    if canary:
        exclusive_write(
            run_dir / "canary_selection.json",
            {key: value for key, value in canary.items() if key != "jobs"},
        )

    completed: dict[str, dict[str, Any]] = {}
    proposals_by_dispatch: dict[str, list[dict[str, Any]]] = {}
    pending: list[Job] = []
    for job in selected:
        paths = artifact_paths(run_dir, job.dispatch_id)
        exclusive_write(paths["prompt"], job.prompt_document)
        exclusive_write(
            paths["dispatch"],
            {
                "schema": DISPATCH_SCHEMA,
                "dispatch_id": job.dispatch_id,
                "canonical_key": job.prepared["canonical_key"],
                "packet_row_id": job.prepared["packet_row_id"],
                "membership_count": job.prepared["membership_count"],
                "category_slot_count": EXPECTED_CATEGORY_COUNT,
                "provider": args.provider,
                "model": args.model,
                "effort": args.effort,
                "prompt_version": PROMPT_VERSION,
                "transport_version": TRANSPORT_VERSION,
                "prompt_sha256": job.prompt_sha256,
                "prompt_bytes": job.prompt_bytes,
                "evidence_sha256": job.prepared["evidence_sha256"],
                "blockers": job.prepared["blockers"],
                "warnings": job.prepared["warnings"],
                "reserved_cost_usd": decimal_string(job.reservation),
                "automated_final_decision_permitted": False,
                "main_corpus_write_authorized": False,
            },
        )
        if paths["receipt"].exists():
            completed[job.dispatch_id] = verify_existing_receipt(job, paths["receipt"])
            continue
        if job.prepared["blockers"]:
            receipt, rows = flagged_terminal(
                job, args, status="evidence_incomplete_flagged",
                reasons=job.prepared["blockers"],
            )
        elif job.prompt_bytes > args.max_prompt_bytes:
            receipt, rows = flagged_terminal(
                job, args, status="oversized_prompt_flagged",
                reasons=["complete prompt exceeds the configured ceiling; no clipping allowed"],
            )
        elif paths["call"].exists():
            receipt, rows = flagged_terminal(
                job, args, status="indeterminate_prior_call_flagged",
                reasons=["a prior provider call may have occurred; automatic replay prohibited"],
            )
            receipt["provider_call_performed"] = True
        else:
            pending.append(job)
            continue
        exclusive_write(paths["receipt"], receipt)
        completed[job.dispatch_id] = receipt
        proposals_by_dispatch[job.dispatch_id] = rows

    dispatched = 0
    if not args.dry_run and pending:
        spent = sum(
            (Decimal(str(row.get("accounted_cost_usd") or "0")) for row in completed.values()),
            Decimal("0"),
        )
        if transport is None:
            transport = build_transport(args)
        ceiling = args.max_provider_calls or len(pending)
        active: dict[Future, tuple[Job, Decimal]] = {}
        reserved = Decimal("0")
        waiting = list(pending)
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            while waiting or active:
                while waiting and len(active) < args.workers and dispatched < ceiling:
                    job = waiting[0]
                    call_reservation = (
                        max(job.reservation, args.minimum_claude_call_budget)
                        if args.provider == "claude-cli"
                        and args.minimum_claude_call_budget is not None
                        else job.reservation
                    )
                    if spent + reserved + call_reservation > args.max_cost:
                        if active:
                            break
                        waiting.pop(0)
                        receipt, rows = flagged_terminal(
                            job, args, status="cost_cap_flagged",
                            reasons=["aggregate provider cost cap reached before dispatch"],
                        )
                        exclusive_write(
                            artifact_paths(run_dir, job.dispatch_id)["receipt"], receipt
                        )
                        completed[job.dispatch_id] = receipt
                        proposals_by_dispatch[job.dispatch_id] = rows
                        continue
                    waiting.pop(0)
                    dispatched += 1
                    future = executor.submit(
                        execute_provider_job,
                        run_dir=run_dir,
                        job=job,
                        args=args,
                        transport=transport,
                        remaining_budget=min(
                            call_reservation,
                            args.max_cost - spent - reserved,
                        ),
                    )
                    active[future] = (job, call_reservation)
                    reserved += call_reservation
                if active:
                    done, _ = wait(active, return_when=FIRST_COMPLETED)
                    for future in done:
                        job, reservation = active.pop(future)
                        reserved -= reservation
                        receipt, rows = future.result()
                        completed[job.dispatch_id] = receipt
                        proposals_by_dispatch[job.dispatch_id] = rows
                        spent += Decimal(str(receipt["accounted_cost_usd"]))
                        if spent > args.max_cost:
                            raise AdjudicationError("accounted provider cost exceeded run cap")
                        print(
                            f"{len(completed)}/{len(selected)} "
                            f"{job.prepared['canonical_key']} {receipt['status']} "
                            f"cost=${receipt['accounted_cost_usd']}",
                            flush=True,
                        )
                elif waiting and dispatched >= ceiling:
                    break
                elif not waiting:
                    break
        for job in waiting:
            receipt, rows = flagged_terminal(
                job, args, status="provider_call_ceiling_flagged",
                reasons=[f"provider call ceiling of {ceiling} reached before dispatch"],
            )
            exclusive_write(artifact_paths(run_dir, job.dispatch_id)["receipt"], receipt)
            completed[job.dispatch_id] = receipt
            proposals_by_dispatch[job.dispatch_id] = rows

    ordered: list[dict[str, Any]] = []
    all_proposals: list[dict[str, Any]] = []
    dry_pending: list[str] = []
    for job in selected:
        paths = artifact_paths(run_dir, job.dispatch_id)
        if not paths["receipt"].exists():
            dry_pending.append(job.prepared["canonical_key"])
            continue
        ordered.append(verify_existing_receipt(job, paths["receipt"]))
        rows = proposals_by_dispatch.get(job.dispatch_id)
        if rows is None:
            receipt = ordered[-1]
            rows = receipt.get("proposal_rows")
            if rows is None:  # legacy v1 receipt: deterministic one-time fallback
                rows = proposal_rows(
                    job.prepared,
                    effective_slots=receipt["effective_category_slots"],
                    effective_memberships=receipt["effective_memberships"],
                    run_id=args.run_id,
                    model=args.model,
                    effort=args.effort,
                    decision_source="automated_replayed_from_legacy_terminal_receipt",
                    proposed_at=receipt["completed_at"],
                )
        all_proposals.extend(rows)

    payload = b"".join(canonical_bytes(row) for row in all_proposals)
    generation_sha = sha256_bytes(payload)
    proposal_path = run_dir / "generations" / f"proposals-{generation_sha}.ndjson"
    exclusive_write(proposal_path, payload)

    dispositions = {name: 0 for name in sorted(ALLOWED_DISPOSITIONS)}
    nexus = {name: 0 for name in sorted(ALLOWED_NEXUS)}
    statuses: dict[str, int] = {}
    downgrades: dict[str, int] = {}
    for receipt in ordered:
        statuses[receipt["status"]] = statuses.get(receipt["status"], 0) + 1
        for row in receipt["effective_memberships"]:
            dispositions[row["proposed_disposition"]] += 1
        for row in receipt["effective_category_slots"]:
            nexus[row["nexus_finding"]] += 1
        for name, count in (receipt.get("policy_downgrades") or {}).items():
            downgrades[name] = downgrades.get(name, 0) + count

    summary = {
        "schema": SUMMARY_SCHEMA,
        "run_id": args.run_id,
        "status": "complete_proposals_only",
        "dry_run": bool(args.dry_run),
        "packets_sha256": packets_sha,
        "candidate_count": candidate_count,
        "selected_count": len(selected),
        "terminal_receipt_count": len(ordered),
        "dry_run_eligible_not_dispatched": dry_pending,
        "provider_calls_performed": sum(
            1 for row in ordered if row.get("provider_call_performed") is True
        ),
        "provider_calls_dispatched_this_run": dispatched,
        "max_provider_calls": args.max_provider_calls or None,
        "excluded_prior_call_key_count": len(excluded_prior_call_keys),
        "minimum_claude_call_budget_usd": (
            decimal_string(args.minimum_claude_call_budget)
            if args.minimum_claude_call_budget is not None
            else None
        ),
        "category_proposal_count": sum(
            1 for row in all_proposals if row["schema"] == CATEGORY_PROPOSAL_SCHEMA
        ),
        "membership_proposal_count": sum(
            1 for row in all_proposals if row["schema"] == MEMBERSHIP_PROPOSAL_SCHEMA
        ),
        "effective_dispositions": dispositions,
        "category_nexus_findings": nexus,
        "terminal_statuses": statuses,
        "policy_downgrades": dict(sorted(downgrades.items())),
        "accounted_cost_usd": decimal_string(
            sum(
                (Decimal(str(row.get("accounted_cost_usd") or "0")) for row in ordered),
                Decimal("0"),
            )
        ),
        "max_cost_usd": decimal_string(args.max_cost),
        "proposal_generation_path": str(proposal_path.resolve()),
        "proposal_generation_sha256": generation_sha,
        "adjudication_state": PROPOSED_STATE,
        "automated_final_decision": False,
        "human_ratification_required": True,
        "candidate_deletion_performed": False,
        "category_evidence_written": False,
        "junk_ledger_write_performed": False,
        "culling_performed": False,
        "scoring_performed": False,
        "rank_write_performed": False,
        "integration_write_performed": False,
        "sync_or_promotion_performed": False,
        "main_corpus_write_performed": False,
        "production_write_performed": False,
        "completed_at": utc_now(),
    }
    exclusive_write(
        run_dir / "generations" / f"summary-{sha256_bytes(canonical_bytes(summary))}.json",
        summary,
    )
    atomic_write_current(
        run_dir / "current_generation.json",
        {
            "schema": "acu-legislation-opus-adjudication-current-generation-v1",
            "proposal_generation_path": str(proposal_path.resolve()),
            "proposal_generation_sha256": generation_sha,
            "updated_at": utc_now(),
        },
    )
    if sha256_file(packets_path) != packets_sha:
        raise AdjudicationError("packet file changed during the run")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    try:
        run(parse_args(argv))
        return 0
    except (AdjudicationError, AttributeError, TypeError, ValueError, OSError) as exc:
        print(f"ERROR: {redact_secrets(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
