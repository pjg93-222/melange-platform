#!/usr/bin/env python3
"""Run a standalone, evidence-bound Claude review of ambiguous bill memberships.

This program is deliberately independent of Codex.  It can use either the
Anthropic API directly or the installed ``claude`` CLI, and it writes only an
immutable review run under ``--out``.  Its results are proposals for later
ratification; it never edits the discovery corpus, the junk ledger, rankings,
or any production/mainframe input.

Safety properties:

* only ``flagged`` or ``ambiguous`` membership rows are accepted;
* every prompt contains the complete supplied evidence projection, without
  silent clipping;
* missing or incomplete official evidence is terminally kept ``flagged`` and
  is never sent to Claude;
* a Claude ``junk`` proposal is downgraded to ``flagged`` unless it is
  high-confidence, cites official full text, rules out every text version, and
  explicitly says that absence was not used as exclusion evidence;
* dispatch, prompt, call-start, response, and terminal-receipt artifacts are
  immutable and content-hash bound;
* a call-start marker without a terminal receipt is not retried on resume,
  because the prior provider call may already have incurred cost;
* a conservative reservation plus an aggregate run cap bounds paid work; and
* API keys are read only from the process environment and are never persisted
  or placed on a command line.
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
import tempfile
from typing import Any, Iterable, Mapping, Protocol, Sequence

try:
    from legislation_measure_key import CANONICAL_KEY_RE
except ImportError:  # pragma: no cover - package import path
    from fec.legislation_measure_key import CANONICAL_KEY_RE


INPUT_SCHEMA = "legislation-claude-ambiguity-input-v1"
RUN_SCHEMA = "legislation-claude-ambiguity-run-v1"
DISPATCH_SCHEMA = "legislation-claude-ambiguity-dispatch-v1"
PROMPT_SCHEMA = "legislation-claude-ambiguity-prompt-v1"
CALL_SCHEMA = "legislation-claude-ambiguity-call-start-v1"
RESPONSE_SCHEMA = "legislation-claude-ambiguity-response-v1"
RECEIPT_SCHEMA = "legislation-claude-ambiguity-terminal-receipt-v1"
SUMMARY_SCHEMA = "legislation-claude-ambiguity-summary-v1"
PROMPT_VERSION = "legislation-membership-culling-v1"
ALLOWED_TRIAGE_STATES = {"flagged", "ambiguous"}
ALLOWED_DISPOSITIONS = {"keep", "junk", "flagged"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SECRET_RE = re.compile(
    r"(?i)(?:sk-ant-[a-z0-9_-]{8,}|ANTHROPIC_API_KEY\s*[=:]\s*\S+)"
)

SYSTEM_PROMPT = """You are reviewing one query/category membership for a preserved
U.S. congressional legislation candidate. Decide whether this membership
belongs in the retained coverage set.

Use ONLY the official evidence in the supplied envelope. Review the title, the
official CRS summary or its explicit official-unavailability receipt, EVERY
supplied full-text version excerpt, EVERY identifier excerpt, stage and
actions, and sponsor/original-cosponsor/cosponsor roles. Do not infer exclusion
from missing press coverage, a missing keyword, or any other absence.

Return:
- keep: official evidence affirmatively supports this query/category
  membership, including cross-category measures;
- junk: official evidence affirmatively rules this membership out as a false
  positive; or
- flagged: evidence is incomplete, mixed, uncertain, or requires human review.

Junk requires high confidence, affirmative official-text evidence, review of
all expected text versions, and full_text_rule_out_complete=true. If any
required evidence is missing, or if evidence absence is doing work, return
flagged. Cite the supplied evidence IDs that actually support the decision.
The result is a review proposal only and never a human-ratified final decision."""

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "disposition": {
            "type": "string",
            "enum": ["keep", "junk", "flagged"],
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
        "reason": {"type": "string", "minLength": 12},
        "reason_code": {"type": "string", "minLength": 3},
        "evidence_ids": {
            "type": "array",
            "items": {"type": "string"},
            "uniqueItems": True,
        },
        "full_text_rule_out_complete": {"type": "boolean"},
        "absence_used_as_exclusion_evidence": {"type": "boolean"},
        "uncertainty": {"type": "string"},
        "matched_category_basis": {"type": "string"},
    },
    "required": [
        "disposition",
        "confidence",
        "reason",
        "reason_code",
        "evidence_ids",
        "full_text_rule_out_complete",
        "absence_used_as_exclusion_evidence",
        "uncertainty",
        "matched_category_basis",
    ],
    "additionalProperties": False,
}


class ReviewError(RuntimeError):
    """A fail-closed queue, artifact, provider, or policy error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
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
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewError(f"cannot read JSON {path}: {exc}") from exc


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ReviewError(f"input queue does not exist: {path}")
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReviewError(
                    f"invalid queue JSON at {path}:{line_number}: {exc}"
                ) from exc
            if not isinstance(value, dict):
                raise ReviewError(f"queue row {line_number} is not an object")
            rows.append(dict(value, _input_line=line_number))
    if not rows:
        raise ReviewError("input queue is empty")
    return rows


def exclusive_write(path: Path, value: Any) -> str:
    """Create one immutable artifact, or verify an identical existing one."""

    data = value if isinstance(value, bytes) else canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        existing = path.read_bytes()
        if existing != data:
            raise ReviewError(f"immutable artifact collision: {path}")
    return sha256_bytes(data)


def atomic_write_current(path: Path, value: Any) -> None:
    """Write a mutable progress pointer; immutable evidence remains elsewhere."""

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


def require_object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReviewError(f"{label} must be an object")
    return value


def require_list(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ReviewError(f"{label} must be an array")
    return value


def member_name(value: Any) -> str:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, Mapping):
        return clean_text(value.get("name") or value.get("full_name"))
    return ""


def evidence_projection(row: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return the exact prompt projection and explicit missing-evidence reasons."""

    canonical_key = clean_text(row.get("canonical_key")).casefold()
    query_id = clean_text(row.get("query_id"))
    category = require_object(row.get("category"), "category")
    title = require_object(row.get("title"), "title")
    summary = require_object(row.get("official_summary"), "official_summary")
    full_text = require_object(row.get("full_text"), "full_text")
    inventory = require_object(full_text.get("inventory"), "full_text.inventory")
    versions = require_list(full_text.get("versions"), "full_text.versions")
    identifier_excerpts = require_list(
        row.get("identifier_excerpts"), "identifier_excerpts"
    )
    stage = require_object(row.get("stage"), "stage")
    actions = require_list(stage.get("actions"), "stage.actions")
    relationships = require_object(row.get("relationships"), "relationships")
    sponsors = require_list(relationships.get("sponsors"), "relationships.sponsors")
    original_cosponsors = require_list(
        relationships.get("original_cosponsors"),
        "relationships.original_cosponsors",
    )
    cosponsors = require_list(
        relationships.get("cosponsors"), "relationships.cosponsors"
    )

    missing: list[str] = []
    if not clean_text(category.get("category_id") or category.get("name")):
        missing.append("category identity absent")
    if title.get("status") != "available" or not clean_text(title.get("text")):
        missing.append("official title unavailable")
    if not SHA256_RE.fullmatch(str(title.get("text_sha256") or "")):
        missing.append("official title hash absent or invalid")

    summary_status = clean_text(summary.get("status"))
    if summary_status == "available":
        if not clean_text(summary.get("text")):
            missing.append("official summary text absent")
        if not SHA256_RE.fullmatch(str(summary.get("text_sha256") or "")):
            missing.append("official summary hash absent or invalid")
    elif summary_status == "officially_unavailable":
        if not clean_text(summary.get("unavailability_reason")):
            missing.append("official summary unavailability reason absent")
        if not SHA256_RE.fullmatch(
            str(summary.get("availability_receipt_sha256") or "")
        ):
            missing.append("official summary unavailability receipt absent or invalid")
    else:
        missing.append("official summary status is unresolved")

    try:
        expected = int(inventory.get("expected_version_count"))
        reviewed = int(inventory.get("reviewed_version_count"))
    except (TypeError, ValueError):
        expected = -1
        reviewed = -2
        missing.append("full-text version counts absent")
    if expected <= 0:
        missing.append("no expected official full-text versions")
    if reviewed != expected:
        missing.append("not every official full-text version was reviewed")
    if inventory.get("all_versions_reviewed") is not True:
        missing.append("full-text inventory is not marked completely reviewed")
    if len(versions) != expected:
        missing.append("full-text version inventory and supplied versions differ")

    evidence_ids: list[str] = ["official_title"]
    if summary_status == "available":
        evidence_ids.append("official_summary")
    elif summary_status == "officially_unavailable":
        evidence_ids.append("official_summary_unavailable")

    normalized_versions: list[dict[str, Any]] = []
    seen_versions: set[str] = set()
    for version_index, raw_version in enumerate(versions):
        version = require_object(
            raw_version, f"full_text.versions[{version_index}]"
        )
        code = clean_text(version.get("version_code"))
        if not code:
            missing.append(f"full-text version {version_index} has no version_code")
            code = f"index-{version_index}"
        if code in seen_versions:
            missing.append(f"duplicate full-text version_code {code}")
        seen_versions.add(code)
        if not SHA256_RE.fullmatch(str(version.get("body_sha256") or "")):
            missing.append(f"full-text version {code} has no valid body hash")
        excerpts = require_list(
            version.get("excerpts"),
            f"full_text.versions[{version_index}].excerpts",
        )
        explicit_no_match = version.get("no_relevant_excerpt") is True
        if not excerpts and not explicit_no_match:
            missing.append(
                f"full-text version {code} has neither excerpts nor explicit no-match"
            )
        normalized_excerpts: list[dict[str, Any]] = []
        for excerpt_index, raw_excerpt in enumerate(excerpts):
            excerpt = require_object(
                raw_excerpt,
                f"full_text.versions[{version_index}].excerpts[{excerpt_index}]",
            )
            text = clean_text(excerpt.get("text"))
            if not text:
                missing.append(
                    f"full-text version {code} excerpt {excerpt_index} is blank"
                )
            evidence_id = f"full_text:{code}:{excerpt_index}"
            evidence_ids.append(evidence_id)
            normalized_excerpts.append(
                {
                    "evidence_id": evidence_id,
                    **{key: value for key, value in excerpt.items()},
                }
            )
        normalized_versions.append(
            {
                **{key: value for key, value in version.items() if key != "excerpts"},
                "excerpts": normalized_excerpts,
            }
        )

    normalized_identifiers: list[dict[str, Any]] = []
    for index, raw_excerpt in enumerate(identifier_excerpts):
        excerpt = require_object(raw_excerpt, f"identifier_excerpts[{index}]")
        if not clean_text(excerpt.get("text") or excerpt.get("context")):
            missing.append(f"identifier excerpt {index} is blank")
        evidence_id = f"identifier:{index}"
        evidence_ids.append(evidence_id)
        normalized_identifiers.append(
            {"evidence_id": evidence_id, **dict(excerpt)}
        )

    if not clean_text(stage.get("value")):
        missing.append("bill stage absent")
    if not actions:
        missing.append("official action history absent")
    for index, action in enumerate(actions):
        if not isinstance(action, (Mapping, str)):
            missing.append(f"action {index} has invalid structure")
        evidence_ids.append(f"action:{index}")

    if relationships.get("role_provenance_complete") is not True:
        missing.append("relationship role provenance incomplete")
    measure_kind = canonical_key.split(":")[1] if canonical_key.count(":") == 2 else ""
    if measure_kind not in {"hamdt", "samdt"} and not sponsors:
        missing.append("sponsor role absent")
    for role, values in (
        ("sponsor", sponsors),
        ("original_cosponsor", original_cosponsors),
        ("cosponsor", cosponsors),
    ):
        for index, person in enumerate(values):
            if not member_name(person):
                missing.append(f"{role} {index} has no name")
            evidence_ids.append(f"role:{role}:{index}")

    projection = {
        "canonical_key": canonical_key,
        "query_id": query_id,
        "category": dict(category),
        "official_title": dict(title),
        "official_summary": dict(summary),
        "full_text": {
            "inventory": dict(inventory),
            "versions": normalized_versions,
        },
        "identifier_excerpts": normalized_identifiers,
        "stage_and_actions": {
            **{key: value for key, value in stage.items() if key != "actions"},
            "actions": [
                {"evidence_id": f"action:{index}", "value": action}
                for index, action in enumerate(actions)
            ],
        },
        "relationship_roles": {
            "role_provenance_complete": relationships.get(
                "role_provenance_complete"
            ),
            "sponsors": [
                {"evidence_id": f"role:sponsor:{index}", "member": value}
                for index, value in enumerate(sponsors)
            ],
            "original_cosponsors": [
                {
                    "evidence_id": f"role:original_cosponsor:{index}",
                    "member": value,
                }
                for index, value in enumerate(original_cosponsors)
            ],
            "cosponsors": [
                {"evidence_id": f"role:cosponsor:{index}", "member": value}
                for index, value in enumerate(cosponsors)
            ],
        },
        "allowed_evidence_ids": evidence_ids,
        "all_supplied_evidence_included": True,
        "candidate_preserved": row.get("candidate_preserved") is True,
        "triage_status": clean_text(row.get("triage_status")).casefold(),
    }
    if row.get("candidate_preserved") is not True:
        missing.append("candidate preservation assertion absent")
    return projection, sorted(set(missing))


def validate_queue(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate every queue row before any provider dispatch is possible."""

    prepared: list[dict[str, Any]] = []
    natural_keys: set[tuple[str, str]] = set()
    for input_index, source in enumerate(rows):
        row = {key: value for key, value in source.items() if key != "_input_line"}
        if row.get("schema") != INPUT_SCHEMA:
            raise ReviewError(f"queue row {input_index} has unexpected schema")
        key = clean_text(row.get("canonical_key")).casefold()
        if not CANONICAL_KEY_RE.fullmatch(key):
            raise ReviewError(f"queue row {input_index} has invalid canonical_key")
        query_id = clean_text(row.get("query_id"))
        if not query_id:
            raise ReviewError(f"queue row {input_index} has blank query_id")
        triage_status = clean_text(row.get("triage_status")).casefold()
        if triage_status not in ALLOWED_TRIAGE_STATES:
            raise ReviewError(
                f"queue row {input_index} is not flagged/ambiguous: {triage_status!r}"
            )
        natural_key = (key, query_id)
        if natural_key in natural_keys:
            raise ReviewError(f"duplicate membership row: {natural_key}")
        natural_keys.add(natural_key)
        projection, missing = evidence_projection(row)
        evidence_sha256 = sha256_bytes(canonical_bytes(projection))
        prepared.append(
            {
                "input_index": input_index,
                "canonical_key": key,
                "query_id": query_id,
                "projection": projection,
                "evidence_sha256": evidence_sha256,
                "missing_evidence": missing,
            }
        )
    return prepared


def prompt_document(prepared: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    document = {
        "schema": PROMPT_SCHEMA,
        "prompt_version": PROMPT_VERSION,
        "policy": {
            "proposal_only": True,
            "candidate_deletion_allowed": False,
            "absence_is_neutral": True,
            "missing_evidence_must_remain_flagged": True,
            "main_corpus_write_authorized": False,
        },
        "system_prompt": SYSTEM_PROMPT,
        "output_schema": OUTPUT_SCHEMA,
        "evidence_envelope": prepared["projection"],
        "evidence_sha256": prepared["evidence_sha256"],
    }
    user_prompt = (
        "Review this exact evidence envelope. No supplied excerpt may be ignored "
        "or treated as absent. Return only the schema-conforming JSON object.\n\n"
        + json.dumps(
            prepared["projection"],
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    return document, user_prompt


def validate_model_output(value: Any, allowed_evidence_ids: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReviewError("Claude output is not an object")
    expected = set(OUTPUT_SCHEMA["required"])
    observed = set(value)
    if observed != expected:
        raise ReviewError(
            f"Claude output fields differ: missing={sorted(expected - observed)} "
            f"extra={sorted(observed - expected)}"
        )
    if value.get("disposition") not in ALLOWED_DISPOSITIONS:
        raise ReviewError("Claude output disposition is invalid")
    if value.get("confidence") not in ALLOWED_CONFIDENCE:
        raise ReviewError("Claude output confidence is invalid")
    if len(clean_text(value.get("reason"))) < 12:
        raise ReviewError("Claude output reason is too short")
    if len(clean_text(value.get("reason_code"))) < 3:
        raise ReviewError("Claude output reason_code is too short")
    evidence_ids = value.get("evidence_ids")
    if (
        not isinstance(evidence_ids, list)
        or any(not isinstance(item, str) or not item for item in evidence_ids)
        or len(evidence_ids) != len(set(evidence_ids))
    ):
        raise ReviewError("Claude output evidence_ids is not a unique string array")
    unknown = set(evidence_ids) - allowed_evidence_ids
    if unknown:
        raise ReviewError(f"Claude cited unknown evidence IDs: {sorted(unknown)}")
    for name in (
        "full_text_rule_out_complete",
        "absence_used_as_exclusion_evidence",
    ):
        if not isinstance(value.get(name), bool):
            raise ReviewError(f"Claude output {name} is not boolean")
    for name in ("uncertainty", "matched_category_basis"):
        if not isinstance(value.get(name), str):
            raise ReviewError(f"Claude output {name} is not a string")
    return dict(value)


def policy_effective_decision(
    model_decision: Mapping[str, Any],
    *,
    missing_evidence: Sequence[str],
) -> dict[str, Any]:
    """Enforce the non-destructive post-model culling contract."""

    decision = dict(model_decision)
    downgrades: list[str] = []
    if missing_evidence:
        downgrades.append("required_evidence_missing")
    if decision.get("disposition") == "junk":
        evidence_ids = set(decision.get("evidence_ids") or [])
        if decision.get("confidence") != "high":
            downgrades.append("junk_requires_high_confidence")
        if decision.get("full_text_rule_out_complete") is not True:
            downgrades.append("junk_requires_complete_full_text_rule_out")
        if decision.get("absence_used_as_exclusion_evidence") is not False:
            downgrades.append("absence_cannot_support_exclusion")
        if not any(item.startswith("full_text:") for item in evidence_ids):
            downgrades.append("junk_requires_official_full_text_citation")
    if downgrades:
        decision["disposition"] = "flagged"
        decision["confidence"] = "low"
        decision["reason_code"] = "policy_downgraded_to_flagged"
        decision["uncertainty"] = "; ".join(sorted(set(downgrades)))
    return decision


def missing_evidence_decision(missing: Sequence[str]) -> dict[str, Any]:
    reason = "Required official evidence is incomplete; membership remains flagged."
    return {
        "disposition": "flagged",
        "confidence": "low",
        "reason": reason,
        "reason_code": "required_evidence_incomplete",
        "evidence_ids": [],
        "full_text_rule_out_complete": False,
        "absence_used_as_exclusion_evidence": False,
        "uncertainty": "; ".join(missing),
        "matched_category_basis": "",
    }


@dataclass(frozen=True)
class ProviderResult:
    output: dict[str, Any]
    response_metadata: dict[str, Any]
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None


class ClaudeTransport(Protocol):
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
            raise ReviewError("ANTHROPIC_API_KEY is required for anthropic-api")
        try:
            import anthropic  # type: ignore
        except ImportError as exc:
            raise ReviewError("the anthropic Python package is not installed") from exc
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
            raise ReviewError("Anthropic response did not contain exactly one text block")
        try:
            output = json.loads(text_blocks[0])
        except json.JSONDecodeError as exc:
            raise ReviewError("Anthropic structured output was not JSON") from exc
        usage = response.usage
        metadata = {
            "provider_response_id": clean_text(getattr(response, "id", "")),
            "provider_model": clean_text(getattr(response, "model", model)),
            "stop_reason": clean_text(getattr(response, "stop_reason", "")),
        }
        return ProviderResult(
            output=output,
            response_metadata=metadata,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cost_usd=None,
        )


class ClaudeCliTransport:
    """Claude CLI transport, locked down like fec/classifier_runtime.py.

    The evidence-bearing user prompt goes over stdin. Only the fixed SYSTEM_PROMPT policy text is
    on the command line (no file/stdin form for it is used anywhere in this repo); no evidence or
    secret is. Tools, MCP servers, slash commands and session persistence are off, and the child runs in
    the temp directory so it cannot pick up the repo's CLAUDE.md, settings or files.
    """

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
                cwd=tempfile.gettempdir(),
            )
        except OSError as exc:
            raise ReviewError(f"cannot execute Claude CLI: {redact_secrets(exc)}") from exc
        if completed.returncode != 0:
            raise ReviewError(
                f"Claude CLI exited {completed.returncode}: "
                f"{redact_secrets(completed.stderr)}"
            )
        try:
            envelope = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ReviewError("Claude CLI output envelope was not JSON") from exc
        if not isinstance(envelope, Mapping):
            raise ReviewError("Claude CLI output envelope is not an object")
        output: Any = envelope.get("structured_output")
        if output is None:
            output = envelope.get("result")
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except json.JSONDecodeError as exc:
                raise ReviewError("Claude CLI result was not structured JSON") from exc
        usage = envelope.get("usage") or {}
        if not isinstance(usage, Mapping):
            usage = {}
        cost_value = envelope.get("total_cost_usd")
        cost: Decimal | None = None
        if cost_value is not None:
            try:
                cost = Decimal(str(cost_value))
            except InvalidOperation as exc:
                raise ReviewError("Claude CLI returned invalid total_cost_usd") from exc
        return ProviderResult(
            output=output,
            response_metadata={
                "provider_response_id": clean_text(envelope.get("session_id")),
                "provider_model": model,
                "stop_reason": clean_text(envelope.get("subtype")),
                "cli_exit_code": completed.returncode,
                "stderr_sha256": sha256_bytes(completed.stderr.encode("utf-8")),
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
) -> Decimal:
    # One input token per UTF-8 byte is a deliberately conservative upper bound
    # for ordinary text and prevents parallel workers from oversubscribing cap.
    input_upper = len(SYSTEM_PROMPT.encode("utf-8")) + len(user_prompt.encode("utf-8"))
    return (
        Decimal(input_upper) * input_cost_per_million
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
    cost = (
        Decimal(result.input_tokens) * input_cost_per_million
        + Decimal(result.output_tokens) * output_cost_per_million
    ) / Decimal(1_000_000)
    if result.input_tokens <= 0 and result.output_tokens <= 0:
        return reservation
    return cost


@dataclass(frozen=True)
class Job:
    prepared: dict[str, Any]
    dispatch_id: str
    prompt_document: dict[str, Any]
    user_prompt: str
    prompt_sha256: str
    reservation: Decimal


def make_job(
    prepared: dict[str, Any],
    *,
    model: str,
    provider: str,
    effort: str,
    max_output_tokens: int,
    input_cost_per_million: Decimal,
    output_cost_per_million: Decimal,
) -> Job:
    document, user_prompt = prompt_document(prepared)
    prompt_sha256 = sha256_bytes(canonical_bytes(document))
    identity = {
        "canonical_key": prepared["canonical_key"],
        "query_id": prepared["query_id"],
        "evidence_sha256": prepared["evidence_sha256"],
        "prompt_sha256": prompt_sha256,
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "provider": provider,
        "effort": effort,
    }
    dispatch_id = sha256_bytes(canonical_bytes(identity))
    reservation = estimated_reservation(
        user_prompt=user_prompt,
        max_output_tokens=max_output_tokens,
        input_cost_per_million=input_cost_per_million,
        output_cost_per_million=output_cost_per_million,
    )
    return Job(
        prepared=prepared,
        dispatch_id=dispatch_id,
        prompt_document=document,
        user_prompt=user_prompt,
        prompt_sha256=prompt_sha256,
        reservation=reservation,
    )


def artifact_paths(run_dir: Path, dispatch_id: str) -> dict[str, Path]:
    return {
        "dispatch": run_dir / "dispatches" / f"{dispatch_id}.json",
        "prompt": run_dir / "prompts" / f"{dispatch_id}.json",
        "call": run_dir / "call_starts" / f"{dispatch_id}.json",
        "response": run_dir / "responses" / f"{dispatch_id}.json",
        "receipt": run_dir / "receipts" / f"{dispatch_id}.json",
    }


def dispatch_record(job: Job, args: argparse.Namespace) -> dict[str, Any]:
    return {
        "schema": DISPATCH_SCHEMA,
        "dispatch_id": job.dispatch_id,
        "canonical_key": job.prepared["canonical_key"],
        "query_id": job.prepared["query_id"],
        "input_index": job.prepared["input_index"],
        "triage_status": job.prepared["projection"]["triage_status"],
        "provider": args.provider,
        "model": args.model,
        "effort": args.effort,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": job.prompt_sha256,
        "evidence_sha256": job.prepared["evidence_sha256"],
        "missing_evidence": job.prepared["missing_evidence"],
        "reserved_cost_usd": decimal_string(job.reservation),
        "candidate_deletion_allowed": False,
        "main_corpus_write_authorized": False,
    }


def terminal_receipt(
    job: Job,
    *,
    status: str,
    model_decision: Mapping[str, Any],
    effective_decision: Mapping[str, Any],
    provider_call_performed: bool,
    accounted_cost: Decimal,
    response_path: Path | None = None,
    response_sha256: str | None = None,
    error: str = "",
) -> dict[str, Any]:
    return {
        "schema": RECEIPT_SCHEMA,
        "dispatch_id": job.dispatch_id,
        "status": status,
        "terminal": True,
        "canonical_key": job.prepared["canonical_key"],
        "query_id": job.prepared["query_id"],
        "evidence_sha256": job.prepared["evidence_sha256"],
        "prompt_sha256": job.prompt_sha256,
        "provider_call_performed": provider_call_performed,
        "response_path": str(response_path.resolve()) if response_path else None,
        "response_sha256": response_sha256,
        "model_decision": dict(model_decision),
        "effective_decision": dict(effective_decision),
        "missing_evidence": list(job.prepared["missing_evidence"]),
        "error": redact_secrets(error),
        "accounted_cost_usd": decimal_string(accounted_cost),
        "automated_final_decision": False,
        "human_ratification_required": True,
        "candidate_deletion_performed": False,
        "absence_is_neutral": True,
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
        raise ReviewError(f"terminal receipt is not current for dispatch: {path}")
    effective = require_object(receipt.get("effective_decision"), "effective_decision")
    if job.prepared["missing_evidence"] and effective.get("disposition") == "junk":
        raise ReviewError("existing receipt violates missing-evidence junk prohibition")
    response_path = receipt.get("response_path")
    response_sha = receipt.get("response_sha256")
    if response_path:
        candidate = Path(str(response_path))
        if not candidate.is_file() or sha256_file(candidate) != response_sha:
            raise ReviewError(f"response artifact hash mismatch for {path}")
    return dict(receipt)


def persist_dispatch_and_prompt(
    run_dir: Path, job: Job, args: argparse.Namespace
) -> dict[str, Path]:
    paths = artifact_paths(run_dir, job.dispatch_id)
    exclusive_write(paths["prompt"], job.prompt_document)
    exclusive_write(paths["dispatch"], dispatch_record(job, args))
    if sha256_file(paths["prompt"]) != job.prompt_sha256:
        raise ReviewError(f"prompt artifact hash mismatch: {paths['prompt']}")
    return paths


def execute_provider_job(
    *,
    run_dir: Path,
    job: Job,
    args: argparse.Namespace,
    transport: ClaudeTransport,
    remaining_budget: Decimal,
    input_cost_per_million: Decimal,
    output_cost_per_million: Decimal,
) -> dict[str, Any]:
    paths = artifact_paths(run_dir, job.dispatch_id)
    exclusive_write(
        paths["call"],
        {
            "schema": CALL_SCHEMA,
            "dispatch_id": job.dispatch_id,
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
            schema=OUTPUT_SCHEMA,
            model=args.model,
            effort=args.effort,
            max_output_tokens=args.max_output_tokens,
            remaining_budget_usd=remaining_budget,
        )
        model_decision = validate_model_output(
            result.output,
            set(job.prepared["projection"]["allowed_evidence_ids"]),
        )
        effective = policy_effective_decision(
            model_decision,
            missing_evidence=job.prepared["missing_evidence"],
        )
        accounted = calculated_cost(
            result,
            input_cost_per_million=input_cost_per_million,
            output_cost_per_million=output_cost_per_million,
            reservation=job.reservation,
        )
        if accounted > job.reservation:
            raise ReviewError(
                "provider cost exceeded conservative reservation; run is fail-closed"
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
            "structured_output": model_decision,
            "structured_output_sha256": sha256_bytes(canonical_bytes(model_decision)),
            "secret_material_persisted": False,
            "received_at": utc_now(),
        }
        response_sha = exclusive_write(paths["response"], response)
        receipt = terminal_receipt(
            job,
            status="accepted",
            model_decision=model_decision,
            effective_decision=effective,
            provider_call_performed=True,
            accounted_cost=accounted,
            response_path=paths["response"],
            response_sha256=response_sha,
        )
    except Exception as exc:  # terminalize every attempted provider dispatch
        fallback = missing_evidence_decision(
            ["provider call or structured-output validation failed"]
        )
        receipt = terminal_receipt(
            job,
            status="provider_failed_flagged",
            model_decision={},
            effective_decision=fallback,
            provider_call_performed=True,
            accounted_cost=job.reservation,
            error=f"{type(exc).__name__}: {redact_secrets(exc)}",
        )
    exclusive_write(paths["receipt"], receipt)
    return receipt


def parse_decimal(value: str, label: str, *, positive: bool = False) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"{label} must be a decimal") from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed <= 0):
        relation = "positive" if positive else "nonnegative"
        raise argparse.ArgumentTypeError(f"{label} must be finite and {relation}")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--provider",
        choices=("anthropic-api", "claude-cli"),
        default="anthropic-api",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("CLAUDE_CULL_MODEL", "claude-sonnet-5"),
    )
    parser.add_argument(
        "--effort",
        choices=("low", "medium", "high", "xhigh", "max"),
        default=os.environ.get("CLAUDE_CULL_EFFORT", "medium"),
        help=(
            "Claude reasoning effort. Use max for the Opus 5 Ultracode "
            "adjudication route."
        ),
    )
    parser.add_argument("--claude-command", default="claude")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--max-output-tokens", type=int, default=1800)
    parser.add_argument("--max-prompt-bytes", type=int, default=1_500_000)
    parser.add_argument("--max-cost-usd", required=True)
    parser.add_argument("--input-cost-per-million", default="3")
    parser.add_argument("--output-cost-per-million", default="15")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.run_id):
        parser.error("--run-id must be a filesystem-safe identifier")
    if args.workers < 1 or args.workers > 32:
        parser.error("--workers must be from 1 through 32")
    if args.max_output_tokens < 128:
        parser.error("--max-output-tokens must be at least 128")
    if args.max_prompt_bytes < 1024:
        parser.error("--max-prompt-bytes must be at least 1024")
    try:
        args.max_cost = parse_decimal(args.max_cost_usd, "--max-cost-usd")
        args.input_rate = parse_decimal(
            args.input_cost_per_million,
            "--input-cost-per-million",
            positive=True,
        )
        args.output_rate = parse_decimal(
            args.output_cost_per_million,
            "--output-cost-per-million",
            positive=True,
        )
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    return args


def build_transport(args: argparse.Namespace) -> ClaudeTransport:
    if args.provider == "anthropic-api":
        return AnthropicApiTransport()
    return ClaudeCliTransport(args.claude_command)


def run(
    args: argparse.Namespace,
    *,
    transport: ClaudeTransport | None = None,
) -> dict[str, Any]:
    source_rows = read_ndjson(args.input)
    prepared = validate_queue(source_rows)
    input_sha = sha256_file(args.input)
    jobs = [
        make_job(
            row,
            model=args.model,
            provider=args.provider,
            effort=args.effort,
            max_output_tokens=args.max_output_tokens,
            input_cost_per_million=args.input_rate,
            output_cost_per_million=args.output_rate,
        )
        for row in prepared
    ]
    oversized = [
        job
        for job in jobs
        if len(job.user_prompt.encode("utf-8")) > args.max_prompt_bytes
    ]

    plan = {
        "schema": RUN_SCHEMA,
        "run_id": args.run_id,
        "input_path": str(args.input.resolve()),
        "input_sha256": input_sha,
        "membership_count": len(jobs),
        "missing_evidence_count": sum(
            bool(job.prepared["missing_evidence"]) for job in jobs
        ),
        "oversized_prompt_count": len(oversized),
        "provider": args.provider,
        "model": args.model,
        "effort": args.effort,
        "workers": args.workers,
        "max_output_tokens": args.max_output_tokens,
        "max_prompt_bytes": args.max_prompt_bytes,
        "max_cost_usd": decimal_string(args.max_cost),
        "input_cost_per_million": decimal_string(args.input_rate),
        "output_cost_per_million": decimal_string(args.output_rate),
        "prompt_version": PROMPT_VERSION,
        "prompt_schema_sha256": sha256_bytes(canonical_bytes(OUTPUT_SCHEMA)),
        "candidate_deletion_allowed": False,
        "main_corpus_write_authorized": False,
        "codex_dependency": False,
    }
    plan["binding_sha256"] = sha256_bytes(canonical_bytes(plan))
    if args.plan_only:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return plan

    run_dir = args.out.resolve() / "runs" / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "run_manifest.json"
    exclusive_write(manifest_path, plan)

    completed: dict[str, dict[str, Any]] = {}
    pending: list[Job] = []
    for job in jobs:
        paths = persist_dispatch_and_prompt(run_dir, job, args)
        if paths["receipt"].exists():
            completed[job.dispatch_id] = verify_existing_receipt(
                job, paths["receipt"]
            )
            continue
        if job.prepared["missing_evidence"]:
            decision = missing_evidence_decision(job.prepared["missing_evidence"])
            receipt = terminal_receipt(
                job,
                status="missing_evidence_flagged",
                model_decision={},
                effective_decision=decision,
                provider_call_performed=False,
                accounted_cost=Decimal("0"),
            )
            exclusive_write(paths["receipt"], receipt)
            completed[job.dispatch_id] = receipt
            continue
        if len(job.user_prompt.encode("utf-8")) > args.max_prompt_bytes:
            decision = missing_evidence_decision(
                ["complete prompt exceeds configured byte ceiling; no clipping allowed"]
            )
            receipt = terminal_receipt(
                job,
                status="oversized_prompt_flagged",
                model_decision={},
                effective_decision=decision,
                provider_call_performed=False,
                accounted_cost=Decimal("0"),
            )
            exclusive_write(paths["receipt"], receipt)
            completed[job.dispatch_id] = receipt
            continue
        if paths["call"].exists():
            decision = missing_evidence_decision(
                ["prior provider call may have occurred; automatic replay prohibited"]
            )
            receipt = terminal_receipt(
                job,
                status="indeterminate_prior_call_flagged",
                model_decision={},
                effective_decision=decision,
                provider_call_performed=True,
                accounted_cost=job.reservation,
            )
            exclusive_write(paths["receipt"], receipt)
            completed[job.dispatch_id] = receipt
            continue
        pending.append(job)

    spent = sum(
        (Decimal(str(row.get("accounted_cost_usd") or "0")) for row in completed.values()),
        Decimal("0"),
    )
    if spent > args.max_cost:
        raise ReviewError("resumed receipts already exceed the configured cost cap")

    if pending and transport is None:
        transport = build_transport(args)

    active: dict[Future[dict[str, Any]], tuple[Job, Decimal]] = {}
    reserved = Decimal("0")
    waiting = list(pending)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        while waiting or active:
            while waiting and len(active) < args.workers:
                job = waiting[0]
                if spent + reserved + job.reservation > args.max_cost:
                    if active:
                        break
                    waiting.pop(0)
                    decision = missing_evidence_decision(
                        ["aggregate Claude cost cap reached before dispatch"]
                    )
                    receipt = terminal_receipt(
                        job,
                        status="cost_cap_flagged",
                        model_decision={},
                        effective_decision=decision,
                        provider_call_performed=False,
                        accounted_cost=Decimal("0"),
                    )
                    exclusive_write(
                        artifact_paths(run_dir, job.dispatch_id)["receipt"], receipt
                    )
                    completed[job.dispatch_id] = receipt
                    continue
                waiting.pop(0)
                remaining = args.max_cost - spent - reserved
                assert transport is not None
                future = executor.submit(
                    execute_provider_job,
                    run_dir=run_dir,
                    job=job,
                    args=args,
                    transport=transport,
                    remaining_budget=remaining,
                    input_cost_per_million=args.input_rate,
                    output_cost_per_million=args.output_rate,
                )
                active[future] = (job, job.reservation)
                reserved += job.reservation
            if active:
                done, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in done:
                    job, reservation = active.pop(future)
                    reserved -= reservation
                    receipt = future.result()
                    completed[job.dispatch_id] = receipt
                    spent += Decimal(str(receipt["accounted_cost_usd"]))
                    if spent > args.max_cost:
                        raise ReviewError("accounted provider cost exceeded run cap")
                    print(
                        f"{len(completed)}/{len(jobs)} "
                        f"{job.prepared['canonical_key']} {job.prepared['query_id']} "
                        f"{receipt['status']} "
                        f"cost=${receipt['accounted_cost_usd']}",
                        flush=True,
                    )

    dispatch_files = list((run_dir / "dispatches").glob("*.json"))
    receipt_files = list((run_dir / "receipts").glob("*.json"))
    dispatch_ids = {path.stem for path in dispatch_files}
    receipt_ids = {path.stem for path in receipt_files}
    if dispatch_ids != receipt_ids or dispatch_ids != {job.dispatch_id for job in jobs}:
        raise ReviewError("dispatch/terminal-receipt reconciliation is not exact")

    ordered_receipts = [
        verify_existing_receipt(job, artifact_paths(run_dir, job.dispatch_id)["receipt"])
        for job in jobs
    ]
    proposal_rows = [
        {
            "canonical_key": row["canonical_key"],
            "query_id": row["query_id"],
            "model_decision": row["model_decision"],
            "effective_decision": row["effective_decision"],
            "terminal_receipt": str(
                artifact_paths(run_dir, row["dispatch_id"])["receipt"].resolve()
            ),
            "terminal_receipt_sha256": sha256_file(
                artifact_paths(run_dir, row["dispatch_id"])["receipt"]
            ),
            "automated_final_decision": False,
            "human_ratification_required": True,
        }
        for row in ordered_receipts
    ]
    proposal_data = b"".join(canonical_bytes(row) for row in proposal_rows)
    generation_sha = sha256_bytes(proposal_data)
    proposal_path = run_dir / "generations" / f"proposals-{generation_sha}.ndjson"
    exclusive_write(proposal_path, proposal_data)

    disposition_counts = {name: 0 for name in sorted(ALLOWED_DISPOSITIONS)}
    status_counts: dict[str, int] = {}
    for row in ordered_receipts:
        disposition_counts[row["effective_decision"]["disposition"]] += 1
        status = str(row["status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    summary = {
        "schema": SUMMARY_SCHEMA,
        "run_id": args.run_id,
        "status": "complete_review_proposals_only",
        "membership_count": len(jobs),
        "dispatch_count": len(dispatch_ids),
        "terminal_receipt_count": len(receipt_ids),
        "one_terminal_receipt_per_dispatch": True,
        "effective_dispositions": disposition_counts,
        "terminal_statuses": status_counts,
        "accounted_cost_usd": decimal_string(
            sum(
                (
                    Decimal(str(row.get("accounted_cost_usd") or "0"))
                    for row in ordered_receipts
                ),
                Decimal("0"),
            )
        ),
        "max_cost_usd": decimal_string(args.max_cost),
        "proposal_generation_path": str(proposal_path.resolve()),
        "proposal_generation_sha256": generation_sha,
        "candidate_deletion_performed": False,
        "junk_ledger_write_performed": False,
        "main_corpus_write_performed": False,
        "production_write_performed": False,
        "human_ratification_required": True,
        "completed_at": utc_now(),
    }
    summary_sha = exclusive_write(
        run_dir / "generations" / f"summary-{sha256_bytes(canonical_bytes(summary))}.json",
        summary,
    )
    atomic_write_current(
        run_dir / "current_generation.json",
        {
            "schema": "legislation-claude-ambiguity-current-generation-v1",
            "proposal_generation_path": str(proposal_path.resolve()),
            "proposal_generation_sha256": generation_sha,
            "summary_sha256": summary_sha,
            "updated_at": utc_now(),
        },
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        run(args)
        return 0
    except ReviewError as exc:
        print(f"ERROR: {redact_secrets(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
