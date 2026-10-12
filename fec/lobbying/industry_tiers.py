#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Publish one canonical CU primacy board and four industry evidence boards.

2026-09-08 user decision: CU uses the full desk primacy authority everywhere,
including adjudicated credits, evidence floor and conduct adjustments.
"""
from __future__ import annotations

import os as _o, sys as _s; _FEC = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))); _s.path.insert(0, _FEC)  # noqa: E501,E702

import contextlib
import hashlib
import json
import math
import os
import sys
from datetime import datetime, timezone

from paths import ROOT as MB_ROOT
from engine import primacy_factors
from moneyball_core import write_text_atomic
import tier_util

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


SCHEMA = "industry-tiers-v1"
INDUSTRIES = ("credit_union", "bank", "crypto", "fintech", "telecom")
PRIMARY_CLASS = {
    "credit_union": "cu_primary",
    "bank": "bank_primary",
    "crypto": "crypto_primary",
    "fintech": "fintech",
    "telecom": "telecom_primary",
}

BUNDLE_PATH = "member_desk_bundle.json"
RANKS_PATH = "ranks_python.json"
TAGS_PATH = "bill_beneficiary_tags.ndjson"
FACTOR_PATH = tier_util.PRIMACY_PATH
ADJUDICATION_PATH = primacy_factors.ADJUDICATION_LEDGER
GRIEVANCE_PATH = tier_util.LEDGER_PATH
DELIVERY_PATH = tier_util.DELIVERY_PATH
DEFAULT_OUTPUT_PATH = os.path.join("data", "lobbying", "industry_tiers.json")
VALENCE_PATH = os.path.join(
    "data", "lobbying", "industry_valence_adjudication.ndjson"
)
AXES_PATH = os.path.join("data", "lobbying", "industry_axes.ndjson")
VALENCE_INDUSTRIES = ("bank", "crypto", "fintech", "telecom")
VALENCE_VALUES = ("pro", "anti", "neutral")


class IndustryTiersError(RuntimeError):
    """An input or parity contract failed; no artifact may be written."""


def _root_path(relative_path):
    return os.path.join(MB_ROOT, relative_path)


def _valence_classes(industry):
    return ("%s_anti" % industry, "%s_neutral" % industry)


def _load_json(path, label):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise IndustryTiersError(
            "cannot read %s %s: %s" % (label, path, exc)
        ) from exc


def _sha12(path):
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise IndustryTiersError("cannot hash input %s: %s" % (path, exc)) from exc
    return digest.hexdigest()[:12]


def _valence_path():
    override = os.environ.get("INDUSTRY_VALENCE_PATH")
    if override:
        if not os.path.isabs(override):
            raise IndustryTiersError(
                "INDUSTRY_VALENCE_PATH must be a full file path; got %r"
                % override
            )
        return os.path.abspath(override)
    return _root_path(VALENCE_PATH)


def _input_files():
    files = {
        BUNDLE_PATH: _root_path(BUNDLE_PATH),
        FACTOR_PATH: _root_path(FACTOR_PATH),
        TAGS_PATH: _root_path(TAGS_PATH),
        ADJUDICATION_PATH: _root_path(ADJUDICATION_PATH),
        RANKS_PATH: _root_path(RANKS_PATH),
        "industry_valence_adjudication.ndjson": _valence_path(),
        "industry_axes.ndjson": _root_path(AXES_PATH),
    }
    admission_path=_root_path('data/legislation/sentinel_admissions.json')
    if os.path.isfile(admission_path):
        files['data/legislation/sentinel_admissions.json']=admission_path
    for relative in (GRIEVANCE_PATH,DELIVERY_PATH,'tier_overrides.json'):
        if os.path.isfile(_root_path(relative)):
            files[relative]=_root_path(relative)
    return files


def _load_valence_ledger(path, tags):
    rows_by_industry = {industry: {} for industry in VALENCE_INDUSTRIES}
    total_active = 0
    seen = set()
    required = (
        "bill", "congress", "industry", "valence", "basis", "rationale",
        "evidence", "adjudicated_by", "adjudicated", "status",
    )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError as exc:
                    raise IndustryTiersError(
                        "invalid valence ledger JSON at line %d: %s"
                        % (line_number, exc)
                    ) from exc
                if not isinstance(row, dict):
                    raise IndustryTiersError(
                        "valence ledger line %d must be an object" % line_number
                    )
                normalized = {}
                for field in required:
                    value = str(row.get(field) or "").strip()
                    if not value:
                        raise IndustryTiersError(
                            "valence ledger line %d field %s must be non-empty"
                            % (line_number, field)
                        )
                    normalized[field] = value
                status = normalized["status"]
                if status not in ("active", "inactive"):
                    raise IndustryTiersError(
                        "status must be active or inactive; got %r" % status
                    )
                industry = normalized["industry"]
                if industry not in VALENCE_INDUSTRIES:
                    raise IndustryTiersError(
                        "valence ledger line %d industry must be one of %s; got %r"
                        % (line_number, VALENCE_INDUSTRIES, industry)
                    )
                valence = normalized["valence"]
                if valence not in VALENCE_VALUES:
                    raise IndustryTiersError(
                        "valence ledger line %d valence must be one of %s; got %r"
                        % (line_number, VALENCE_VALUES, valence)
                    )
                confidence = _number(
                    row.get("confidence"),
                    "valence ledger line %d confidence" % line_number,
                )
                if not 0 < confidence <= 1:
                    raise IndustryTiersError(
                        "valence ledger line %d confidence must be in (0, 1]; got %r"
                        % (line_number, confidence)
                    )
                bill = normalized["bill"]
                congress = normalized["congress"]
                if bill.startswith("LTR-"):
                    raise IndustryTiersError(
                        "signed-letter valence is undefined; letters score "
                        "LETTER_W and collapse to mix['letter']"
                    )
                if "|" in bill or "|" in congress:
                    raise IndustryTiersError(
                        "bill/congress may not contain '|'"
                    )
                key = "%s|%s" % (bill, congress)
                if key not in tags:
                    raise IndustryTiersError(
                        "valence ledger line %d act key %s is not in beneficiary tags"
                        % (line_number, key)
                    )
                tag_class = tags[key].get("class")
                primary_class = PRIMARY_CLASS[industry]
                if tag_class not in (primary_class, "oppose"):
                    raise IndustryTiersError(
                        "valence ledger line %d act key %s class must be "
                        "%s or oppose; got %r"
                        % (line_number, key, primary_class, tag_class)
                    )
                duplicate_key = (bill, congress, industry)
                if duplicate_key in seen:
                    raise IndustryTiersError(
                        "duplicate valence ledger row at line %d: %r"
                        % (line_number, duplicate_key)
                    )
                seen.add(duplicate_key)
                if normalized["status"] != "active":
                    continue
                active_row = dict(row)
                active_row.update(normalized)
                active_row["confidence"] = confidence
                rows_by_industry[industry][key] = active_row
                total_active += 1
    except OSError as exc:
        raise IndustryTiersError(
            "cannot read valence ledger %s: %s" % (path, exc)
        ) from exc
    return rows_by_industry, total_active


def _load_industry_axes(path, tags):
    rows_by_industry = {industry: {} for industry in INDUSTRIES}
    total_active = 0
    seen = set()
    required = (
        "bill", "congress", "industry", "basis", "rationale",
        "adjudicated_by", "adjudicated", "status",
    )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError as exc:
                    raise IndustryTiersError(
                        "invalid industry axes JSON at line %d: %s"
                        % (line_number, exc)
                    ) from exc
                if not isinstance(row, dict):
                    raise IndustryTiersError(
                        "industry axes line %d must be an object" % line_number
                    )

                normalized = {}
                for field in required:
                    value = str(row.get(field) or "").strip()
                    if not value:
                        raise IndustryTiersError(
                            "industry axes line %d field %s must be non-empty"
                            % (line_number, field)
                        )
                    normalized[field] = value

                status = normalized["status"]
                if status not in ("active", "inactive"):
                    raise IndustryTiersError(
                        "industry axes line %d status must be active or "
                        "inactive; got %r" % (line_number, status)
                    )

                industry = normalized["industry"]
                if industry not in INDUSTRIES:
                    raise IndustryTiersError(
                        "industry axes line %d industry must be one of %s; "
                        "got %r" % (line_number, INDUSTRIES, industry)
                    )

                relevance = _number(
                    row.get("relevance"),
                    "industry axes line %d relevance" % line_number,
                )
                if not 0.0 <= relevance <= 1.0:
                    raise IndustryTiersError(
                        "industry axes line %d relevance must be in [0, 1]; "
                        "got %r" % (line_number, relevance)
                    )

                valence = _number(
                    row.get("valence"),
                    "industry axes line %d valence" % line_number,
                )
                if not -1.0 <= valence <= 1.0:
                    raise IndustryTiersError(
                        "industry axes line %d valence must be in [-1, 1]; "
                        "got %r" % (line_number, valence)
                    )

                confidence = _number(
                    row.get("confidence"),
                    "industry axes line %d confidence" % line_number,
                )
                if not 0.0 < confidence <= 1.0:
                    raise IndustryTiersError(
                        "industry axes line %d confidence must be in "
                        "(0, 1]; got %r" % (line_number, confidence)
                    )

                bill = normalized["bill"]
                congress = normalized["congress"]
                if bill.startswith("LTR-"):
                    raise IndustryTiersError(
                        "industry axes line %d signed-letter cells are "
                        "undefined; letters score LETTER_W and collapse to "
                        "mix['letter']" % line_number
                    )
                if "|" in bill or "|" in congress:
                    raise IndustryTiersError(
                        "industry axes line %d bill/congress may not contain "
                        "'|'" % line_number
                    )

                key = "%s|%s" % (bill, congress)
                if key not in tags:
                    raise IndustryTiersError(
                        "industry axes line %d act key %s is not in "
                        "beneficiary tags" % (line_number, key)
                    )

                duplicate_key = (bill, congress, industry)
                if duplicate_key in seen:
                    raise IndustryTiersError(
                        "duplicate industry axes cell at line %d: %r"
                        % (line_number, duplicate_key)
                    )
                seen.add(duplicate_key)

                if status != "active":
                    continue

                active_row = dict(row)
                active_row.update(normalized)
                active_row.update({
                    "relevance": relevance,
                    "valence": valence,
                    "confidence": confidence,
                })
                rows_by_industry[industry][key] = active_row
                total_active += 1
    except OSError as exc:
        raise IndustryTiersError(
            "cannot read industry axes %s: %s" % (path, exc)
        ) from exc
    return rows_by_industry, total_active


def _industry_tags_view(
    tags, valence_rows, axes_rows, industry,
):
    conflicts = sorted(set(valence_rows) & set(axes_rows))
    if conflicts:
        raise IndustryTiersError(
            "industry axes conflict for %s %s: the valence ledger and "
            "tensor both own the cell" % (industry, conflicts[0])
        )

    view = {key: dict(value) for key, value in tags.items()}
    counts = {"anti": 0, "neutral": 0, "pro": 0, "cells": 0}
    cell_weights = {}
    anti_class, neutral_class = _valence_classes(industry)

    for key, row in valence_rows.items():
        valence = row["valence"]
        counts[valence] += 1
        if valence == "anti":
            view[key]["class"] = anti_class
        elif valence == "neutral":
            view[key]["class"] = neutral_class

    for index, key in enumerate(sorted(axes_rows), 1):
        row = axes_rows[key]
        cell_class = "%s_cell_%d" % (industry, index)
        view[key]["class"] = cell_class
        if industry == "credit_union":
            # The adjudicated cell owns CU physics; the older raw position
            # overlay must not replace its exact scoped tensor weight.
            view[key].pop("acu_position", None)
        cell_weights[cell_class] = round(
            row["relevance"] * row["valence"], 6
        )
        counts["cells"] += 1

    return view, counts, cell_weights


def _number(value, label):
    if isinstance(value, bool):
        raise IndustryTiersError("%s must be numeric" % label)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise IndustryTiersError("%s must be numeric" % label) from exc
    if not math.isfinite(number):
        raise IndustryTiersError("%s must be finite" % label)
    return number


def _assert_close(label, got, want, tolerance=1e-12):
    got = _number(got, "%s (runtime)" % label)
    want = _number(want, "%s (artifact)" % label)
    if abs(got - want) > tolerance:
        raise IndustryTiersError(
            "%s mismatch: runtime=%r artifact=%r" % (label, got, want)
        )


def _numeric_map(value, label):
    if not isinstance(value, dict):
        raise IndustryTiersError("%s must be an object" % label)
    return {
        str(key): _number(number, "%s.%s" % (label, key))
        for key, number in value.items()
    }


def _validate_constants(factor_artifact, tags):
    constants = factor_artifact.get("_constants")
    if not isinstance(constants, dict):
        raise IndustryTiersError("primacy_factors.json lacks _constants")

    runtime_role_weights = {
        str(key): float(value)
        for key, value in primacy_factors.ROLE_W.items()
    }
    artifact_role_weights = _numeric_map(
        constants.get("role_weights"), "_constants.role_weights"
    )
    if runtime_role_weights != artifact_role_weights:
        raise IndustryTiersError(
            "role_weights mismatch: runtime=%r artifact=%r"
            % (runtime_role_weights, artifact_role_weights)
        )

    runtime_act_weights = {
        str(key): float(value)
        for key, value in primacy_factors.ACT_W.items()
    }
    artifact_act_weights = _numeric_map(
        constants.get("act_weights"), "_constants.act_weights"
    )
    if runtime_act_weights != artifact_act_weights:
        raise IndustryTiersError(
            "act_weights mismatch: runtime=%r artifact=%r"
            % (runtime_act_weights, artifact_act_weights)
        )

    runtime_acu_position_relevance = {
        str(key): float(value)
        for key, value in primacy_factors.ACU_RELEVANCE.items()
    }
    artifact_acu_position_relevance = _numeric_map(
        constants.get("acu_position_relevance"),
        "_constants.acu_position_relevance",
    )
    if runtime_acu_position_relevance != artifact_acu_position_relevance:
        raise IndustryTiersError(
            "acu_position_relevance mismatch: runtime=%r artifact=%r"
            % (runtime_acu_position_relevance, artifact_acu_position_relevance)
        )

    _assert_close(
        "letter_weight", primacy_factors.LETTER_W,
        constants.get("letter_weight"),
    )
    _assert_close(
        "uncertainty_penalty_k", primacy_factors.UNCERT_K,
        constants.get("uncertainty_penalty_k"),
    )
    _assert_close(
        "amendment_measure_weight", primacy_factors.AMENDMENT_W,
        constants.get("amendment_measure_weight"),
    )
    _assert_close(
        "shrink_anchor", primacy_factors.SHRINK_ANCHOR,
        constants.get("shrink_anchor"),
    )
    _assert_close("default_role_weight", primacy_factors.DEFAULT_ROLE_W, 0.25)
    _assert_close("pinned letter_weight", constants.get("letter_weight"), 0.60)
    _assert_close(
        "pinned uncertainty_penalty_k",
        constants.get("uncertainty_penalty_k"),
        0.0 if primacy_factors.DIAGNOSTIC else 0.25,
    )
    _assert_close(
        "pinned amendment_measure_weight",
        constants.get("amendment_measure_weight"), 0.50,
    )
    _assert_close("pinned shrink_anchor", constants.get("shrink_anchor"), 15.0)

    if primacy_factors.MK.measure_weight_policy() != constants.get(
        "measure_weight_policy"
    ):
        raise IndustryTiersError(
            "measure_weight_policy mismatch between runtime and artifact"
        )
    _assert_close(
        "legislation_measure_key amendment weight",
        primacy_factors.MK.AMENDMENT_MEASURE_WEIGHT,
        constants.get("amendment_measure_weight"),
    )

    letter_overrides = {
        key: _number(
            tag[primacy_factors.LETTER_OVERRIDE_FIELD],
            "%s.%s" % (key, primacy_factors.LETTER_OVERRIDE_FIELD),
        )
        for key, tag in sorted(tags.items())
        if tag.get(primacy_factors.LETTER_OVERRIDE_FIELD) is not None
    }
    artifact_letter_overrides = _numeric_map(
        constants.get("letter_weight_overrides") or {},
        "_constants.letter_weight_overrides",
    )
    if letter_overrides != artifact_letter_overrides:
        raise IndustryTiersError(
            "letter_weight_overrides mismatch: runtime=%r artifact=%r"
            % (letter_overrides, artifact_letter_overrides)
        )
    try:
        classified_count = int(constants.get("classified_act_count"))
    except (TypeError, ValueError) as exc:
        raise IndustryTiersError(
            "_constants.classified_act_count must be an integer"
        ) from exc
    if classified_count != len(tags):
        raise IndustryTiersError(
            "classified_act_count mismatch: tags=%d artifact=%d"
            % (len(tags), classified_count)
        )
    return constants


def _validate_bundle(bundle, tags):
    records = bundle.get("members")
    if not isinstance(records, list):
        raise IndustryTiersError("member_desk_bundle.json members must be a list")
    if len(records) != 533:
        raise IndustryTiersError(
            "member_desk_bundle.json must contain 533 members; got %d"
            % len(records)
        )

    names = []
    bundle_keys = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise IndustryTiersError("bundle member %d must be an object" % index)
        member = str(record.get("name") or "").strip()
        if not member:
            raise IndustryTiersError("bundle member %d has no name" % index)
        names.append(member)
        if not str(record.get("chamber") or "").strip():
            raise IndustryTiersError("bundle member %r has no chamber" % member)
        for bill in record.get("bills") or []:
            display_id = str((bill or {}).get("bill") or "").strip()
            congress = str((bill or {}).get("congress") or "").strip()
            bundle_keys.add("%s|%s" % (display_id, congress))

    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise IndustryTiersError("duplicate bundle members: %s" % duplicates[:10])
    tag_keys = set(tags)
    if bundle_keys != tag_keys:
        missing = sorted(bundle_keys - tag_keys)
        extra = sorted(tag_keys - bundle_keys)
        raise IndustryTiersError(
            "act universe and beneficiary tags are not closed: missing=%s extra=%s"
            % (missing[:10], extra[:10])
        )
    unknown_classes = sorted(
        {
            str(tag.get("class") or "")
            for tag in tags.values()
        }
        - set(primacy_factors.ACT_W)
    )
    if unknown_classes:
        raise IndustryTiersError(
            "unknown beneficiary classes: %s" % unknown_classes
        )
    return records, names


def _validate_adjudication_scope(path):
    active_classes = set()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if str(row.get("status") or "active").strip() != "active":
                    continue
                active_classes.add(
                    str(row.get("beneficiary_class") or "").strip()
                )
    except (OSError, ValueError) as exc:
        raise IndustryTiersError(
            "cannot validate adjudication ledger %s: %s" % (path, exc)
        ) from exc
    if active_classes - {"cu_primary"}:
        raise IndustryTiersError(
            "non-CU adjudicated valence is not defined; active classes=%s"
            % sorted(active_classes)
        )


def _act_weight_map(industry):
    if industry == "credit_union":
        return {
            key: float(value)
            for key, value in primacy_factors.ACT_W.items()
        }
    own_class = PRIMARY_CLASS[industry]
    industry_classes = set(PRIMARY_CLASS.values())
    weights = {}
    for beneficiary_class in primacy_factors.ACT_W:
        if beneficiary_class == own_class:
            weight = 1.0
        elif beneficiary_class == "shared":
            weight = 0.5
        elif beneficiary_class in industry_classes:
            weight = 0.15
        elif beneficiary_class in ("offtopic", "oppose"):
            weight = 0.0
        else:
            raise IndustryTiersError(
                "no %s act weight for class %r"
                % (industry, beneficiary_class)
            )
        weights[beneficiary_class] = weight
    return weights


@contextlib.contextmanager
def _scoped_act_weights(act_weights, acu_relevance, uncertainty_k=None):
    """Bind the engine's module-global map only for one synchronous compute."""
    original_act_weights = primacy_factors.ACT_W
    original_acu_relevance = primacy_factors.ACU_RELEVANCE_ACTIVE
    original_uncertainty_k = primacy_factors.UNCERT_K
    primacy_factors.ACT_W = dict(act_weights)
    primacy_factors.ACU_RELEVANCE_ACTIVE = acu_relevance
    if uncertainty_k is not None:
        primacy_factors.UNCERT_K = uncertainty_k
    try:
        yield
    finally:
        primacy_factors.ACT_W = original_act_weights
        primacy_factors.ACU_RELEVANCE_ACTIVE = original_acu_relevance
        primacy_factors.UNCERT_K = original_uncertainty_k


def _compute_generalized(
    bundle, tags, act_weights, acu_relevance, include_adjudicated=True,
    uncertainty_k=None,
):
    with _scoped_act_weights(act_weights, acu_relevance, uncertainty_k):
        adjudicated = None
        if include_adjudicated:
            adjudicated = primacy_factors.load_adjudicated_credits(
                _root_path(ADJUDICATION_PATH)
            )
        return primacy_factors.compute(bundle, tags, adjudicated)


def _floor_minimum(industry):
    try:
        minimum = float(
            os.environ.get("INDUSTRY_TIERS_MIN_PRIMARY") or "0.75"
        )
    except ValueError as exc:
        raise IndustryTiersError(
            "INDUSTRY_TIERS_MIN_PRIMARY must be numeric"
        ) from exc
    if not math.isfinite(minimum):
        raise IndustryTiersError(
            "INDUSTRY_TIERS_MIN_PRIMARY must be finite"
        )
    if minimum < 0:
        raise IndustryTiersError(
            "INDUSTRY_TIERS_MIN_PRIMARY must be >= 0; got %r" % minimum
        )
    return minimum


def _apply_tier1_floor(tier_by_member, floor_qualified, industry):
    if os.environ.get("INDUSTRY_TIERS_T1_FLOOR") == "0":
        return 0
    demotions = 0
    for member, tier in tier_by_member.items():
        if tier == 1 and not floor_qualified[member]:
            tier_by_member[member] = 2
            demotions += 1
    return demotions


def _prioritize_non_cu_evidence(order, primary_credit, minimum, buckets):
    """Preserve the industry boards' evidence priority, not a hard seat floor.

    Up to the total tier capacity, qualified members take their original score
    order first. If there are fewer qualifiers, the remaining members retain
    score order; after that capacity, qualification gives no further priority.
    Tier 1 qualification is enforced separately by ``_apply_tier1_floor``.

    This is the historical non-CU behavior made explicit. CU eligibility uses
    tier_util's strict joint constraints and must never use this soft priority.
    """
    order = list(order)
    if minimum <= 0 or not primary_credit:
        return order
    preferred = [member for member in order
                 if float(primary_credit.get(member, 0.0)) >= minimum]
    preferred = preferred[:sum(buckets)]
    selected = set(preferred)
    return preferred + [member for member in order if member not in selected]


def _bioguide(record):
    candidates = (
        record.get("bioguide"),
        record.get("bioguide_id"),
        (record.get("bio") or {}).get("bioguide")
        if isinstance(record.get("bio"), dict) else None,
    )
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value:
            return value
    return None


def _build_industry(
    industry, bundle, records, member_names, pcts, tags, valence_rows,
    axes_rows,
):
    act_weights = _act_weight_map(industry)
    if industry == "credit_union" and valence_rows:
        raise IndustryTiersError(
            "credit_union cannot carry industry valence rows"
        )

    compute_tags, applied, cell_weights = _industry_tags_view(
        tags, valence_rows, axes_rows, industry
    )
    act_weights.update(cell_weights)

    if industry == "credit_union":
        generalized_members, baseline = _compute_generalized(
            bundle, compute_tags, act_weights, acu_relevance=True,
            include_adjudicated=False,
        )
    else:
        anti_class, neutral_class = _valence_classes(industry)
        act_weights.update({anti_class: -1.0, neutral_class: 0.0})
        generalized_members, baseline = _compute_generalized(
            bundle, compute_tags, act_weights, acu_relevance=False,
            include_adjudicated=False,
            # The ratified diagnostic changes the CU board. Bank/crypto/fintech/telecom
            # retain their prior factor-only method and its .25 uncertainty dock.
            uncertainty_k=0.25,
        )
    if set(generalized_members) != set(member_names):
        raise IndustryTiersError(
            "%s generalized factor roster does not match the bundle" % industry
        )

    scores = {
        member: round(
            _number(
                values["factor_ranked"],
                "%s.%s.factor_ranked" % (industry, member),
            ),
            6,
        )
        for member, values in generalized_members.items()
    }
    primary_class = PRIMARY_CLASS[industry]
    primary_credit = {
        member: _number(
            (values.get("mix") or {}).get(primary_class, 0.0),
            "%s.%s.mix.%s" % (industry, member, primary_class),
        )
        for member, values in generalized_members.items()
    }
    industry_overrides = {}
    industry_positions = {}
    breaches = {}
    delivered = {}

    all_scores = tier_util.primacy_scores(pcts, scores, mode="factor")
    rankable = [member for member in member_names if member in all_scores]
    score_order = sorted(
        rankable, key=lambda member: (-all_scores[member], member)
    )
    order = tier_util.apply_tier_overrides(
        score_order,
        industry_overrides,
        buckets=tier_util.BUCKETS,
        positions=industry_positions,
    )
    minimum = _floor_minimum(industry)
    if industry == "credit_union":
        # Private compatibility path only; production uses _canonical_cu_board.
        # It still honors the same strict CU eligibility/constraint contract.
        minimum_slots = tier_util.evidence_slot_floors(
            order,
            primary_credit,
            minimum=minimum,
            buckets=tier_util.BUCKETS,
            exempt=set(industry_overrides),
        )
        final, _detail = tier_util.apply_conduct_ladder(
            order, breaches, delivered, buckets=tier_util.BUCKETS,
            minimum_slots=minimum_slots,
        )
    else:
        # Non-CU boards carry no conduct ledger or placement overrides. Their
        # mandatory qualification rule is Tier 1 only, applied below.
        final = _prioritize_non_cu_evidence(
            order, primary_credit, minimum, tier_util.BUCKETS,
        )

    floor_qualified = {
        member: (
            minimum <= 0
            or member in industry_overrides
            or float(primary_credit.get(member, 0.0)) >= minimum
        )
        for member in final
    }
    first, second, third = tier_util._cutoffs(tier_util.BUCKETS)
    tier_by_member = {}
    rank_by_member = {}
    for index, member in enumerate(final):
        rank_by_member[member] = index + 1
        tier_by_member[member] = (
            1 if index < first else
            2 if index < second else
            3 if index < third else 0
        )
    tier1_floor_demotions = _apply_tier1_floor(
        tier_by_member, floor_qualified, industry
    )

    records_by_name = {record["name"]: record for record in records}
    rows = [
        {
            "member": member,
            "bioguide": _bioguide(records_by_name[member]),
            "chamber": records_by_name[member]["chamber"],
            "tier": tier_by_member[member],
            "tierrank": rank_by_member[member],
            "score": scores[member],
            "floor_qualified": floor_qualified[member],
        }
        for member in sorted(final, key=rank_by_member.__getitem__)
    ]
    primary_class = PRIMARY_CLASS[industry]
    receipts = {
        "scope": (
            "sponsored legislation only (no rhetoric, hearings, or votes); "
            "ranks below tier 3 order a near-tied score plateau - scores "
            "carry the signal"
            + (
                "; CU tier-0 rank = least CU-focused, not anti-CU - "
                "hostility is carried by the oppose class and negative cells"
                if industry == "credit_union" else ""
            )
        ),
        "primary_act_count": sum(
            1 for tag in tags.values() if tag.get("class") == primary_class
        ),
        "floor_qualified_count": sum(floor_qualified.values()),
        "tier1_floor": (
            "cu (desk semantics unchanged)"
            if industry == "credit_union"
            else (
                "non-CU tier 1 requires evidence-floor qualification; "
                "demoted %d unqualified slot-holders to tier 2 "
                "(INDUSTRY_TIERS_T1_FLOOR=0 reverts)"
                % tier1_floor_demotions
            )
        ),
        "conduct_ledger": (
            "none (desk-only refinement)"
            if industry == "credit_union" else "none"
        ),
        "overrides": (
            "none (desk-only refinement)"
            if industry == "credit_union" else "none"
        ),
        "oppose_handling": (
            "CU physics: oppose -1.0, letters 0.6, acu_position axis "
            "(SUPPORT>=0.5/OPPOSE=-1.0); adjudicated credits excluded "
            "(desk-only)"
            if industry == "credit_union"
            else (
                "valence-adjudicated (2026-08-10): own-industry acts signed "
                "from operative text; anti weight -1.0, neutral weight 0.0, "
                "unadjudicated primary weight 1.0; applied: %d anti, %d "
                "neutral, %d pro; CU-relative 'oppose' class stays weight 0.0"
                % (applied["anti"], applied["neutral"], applied["pro"])
            )
        ),
        "baseline": round(float(baseline), 6),
        "act_weight_map": act_weights,
        "tensor_cells": {
            "applied": applied["cells"],
            "source": "industry_axes.ndjson",
        },
    }
    if industry != "credit_union":
        receipts["valence_adjudication"] = {
            "applied_anti": applied["anti"],
            "applied_neutral": applied["neutral"],
            "applied_pro": applied["pro"],
            "ledger": "industry_valence_adjudication.ndjson",
        }
    return {
        "rows": rows,
        "receipts": receipts,
    }


def _canonical_cu_board(records, member_names, factor_artifact, tags, *,
                        board_mode=None, effectiveness_path=None, shadow_root=None):
    """Read the same factor and tier authorities as the desk and engines."""
    selected_mode = board_mode or tier_util.PRIMACY_BOARD
    if selected_mode == 'realized_results_v1':
        from engine.effectiveness_integration import load_shadow_comparison
        comparison = load_shadow_comparison(
            effectiveness_path or os.environ.get('EFFECTIVENESS_COMPARISON_PATH'),
            shadow_root=shadow_root or os.environ.get('EFFECTIVENESS_SHADOW_ROOT'))
        tier,rank,detail = tier_util.realized_results_tiers(member_names, comparison,
                                                         root=MB_ROOT, with_detail=True)
        records_by_name = {row['name']:row for row in records}
        rows = [{'member':name, 'bioguide':_bioguide(records_by_name[name]),
                 'chamber':records_by_name[name]['chamber'], 'tier':tier[name],
                 'tierrank':rank[name], 'score':detail[name]['realized_results'],
                 'orientation_factor':detail[name]['orientation_factor'],
                 'floor_qualified':detail[name]['results_gate_qualified'],
                 'coverage':detail[name]['coverage']} for name in sorted(member_names,key=lambda name:(rank[name] or 10**9,name))]
        return {'rows':rows, 'receipts':{
            'scope':'Shadow realized CU results; distinct-measure evidence gate and canonical conduct rules',
            'board_mode':selected_mode, 'status':'shadow_candidate',
            'evidence_gate':comparison['results_evidence_gate'],
            'source_sha256':comparison['source_sha256'], 'as_of':comparison.get('as_of'),
            'floor_qualified_count':sum(row['floor_qualified'] for row in rows)}}
    tier, rank = tier_util.legislative_press_tiers(member_names, root=MB_ROOT, mode=selected_mode)
    factors = factor_artifact['members']
    if set(factors) != set(member_names) or set(tier) != set(member_names):
        raise IndustryTiersError('Canonical CU board roster differs from its factor authority')
    records_by_name = {row['name']:row for row in records}
    minimum = tier_util.PRIMACY_MIN_CUPRIM
    rows = [{
        'member':name, 'bioguide':_bioguide(records_by_name[name]),
        'chamber':records_by_name[name]['chamber'], 'tier':tier[name], 'tierrank':rank[name],
        'score':factors[name]['factor_ranked'],
        'floor_qualified':float(factors[name].get('mix',{}).get('cu_primary',0))>=minimum,
    } for name in sorted(rank,key=rank.get)]
    return {'rows':rows,'receipts':{
        'scope':'Canonical CU primacy board shared with the member desk and allocation engines; classified acts, adjudicated credits, evidence floor and conduct adjustments.',
        'primary_act_count':sum(tag.get('class')=='cu_primary' for tag in tags.values()),
        'floor_qualified_count':sum(row['floor_qualified'] for row in rows),
        'tier1_floor':'canonical tier_util evidence floor',
        'conduct_ledger':GRIEVANCE_PATH+' + '+DELIVERY_PATH,
        'overrides':'canonical tier_util policy',
        'oppose_handling':'Canonical signed CU act weights and active adjudicated credits; identical to the member desk.',
        'baseline':factor_artifact['_constants']['roster_baseline'],
        'authority':FACTOR_PATH,
    }}


def _sorted_for_json(value, preserve_order=False):
    """Sort object keys recursively, preserving the required industry order."""
    if isinstance(value, dict):
        keys = list(value) if preserve_order else sorted(value)
        return {
            key: _sorted_for_json(
                value[key], preserve_order=(key == "industries")
            )
            for key in keys
        }
    if isinstance(value, list):
        return [_sorted_for_json(item) for item in value]
    return value


def _generated_utc():
    override = os.environ.get("GENERATED_UTC")
    if override is not None:
        return override
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _output_path():
    override = os.environ.get("INDUSTRY_TIERS_OUT")
    if override:
        if not os.path.isabs(override):
            raise IndustryTiersError(
                "INDUSTRY_TIERS_OUT must be a full file path; got %r" % override
            )
        return os.path.abspath(override)
    return _root_path(DEFAULT_OUTPUT_PATH)


def _print_summary(industry, rows):
    counts = {
        tier: sum(1 for row in rows if row["tier"] == tier)
        for tier in (1, 2, 3, 0)
    }
    top = [row["member"] for row in rows if row["tier"] == 1]
    print(
        "%s: Tier 1=%d | Tier 2=%d | Tier 3=%d | Tier 0=%d"
        % (
            industry, counts[1], counts[2], counts[3], counts[0],
        )
    )
    print("%s top-13: %s" % (industry, "; ".join(top)))


def main():
    files = _input_files()
    input_hashes = {
        path: _sha12(resolved)
        for path, resolved in sorted(files.items())
    }
    bundle = _load_json(files[BUNDLE_PATH], "member desk bundle")
    ranks = _load_json(files[RANKS_PATH], "rank authority")
    factor_artifact = _load_json(files[FACTOR_PATH], "primacy factor artifact")
    try:
        tags = primacy_factors.load_tags(files[TAGS_PATH])
        from legislation import sentinel_admissions as SA
        tags = SA.effective_tags(tags, SA.load(MB_ROOT))
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise IndustryTiersError(
            "cannot load beneficiary tags %s: %s" % (files[TAGS_PATH], exc)
        ) from exc

    records, member_names = _validate_bundle(bundle, tags)
    _validate_constants(factor_artifact, tags)
    _validate_adjudication_scope(files[ADJUDICATION_PATH])
    valence_rows_by_industry, _ = (
        _load_valence_ledger(
            files["industry_valence_adjudication.ndjson"], tags
        )
    )
    axes_rows_by_industry, _ = _load_industry_axes(
        files["industry_axes.ndjson"], tags
    )
    if not isinstance(ranks.get("pcts"), dict):
        raise IndustryTiersError("ranks_python.json pcts must be an object")

    industries = {}
    for industry in INDUSTRIES:
        if industry == 'credit_union':
            industries[industry] = _canonical_cu_board(records,member_names,factor_artifact,tags)
            continue
        industries[industry] = _build_industry(
            industry=industry,
            bundle=bundle,
            records=records,
            member_names=member_names,
            pcts=ranks["pcts"],
            tags=tags,
            valence_rows=valence_rows_by_industry.get(industry, {}),
            axes_rows=axes_rows_by_industry.get(industry, {}),
        )

    payload = {
        "schema": SCHEMA,
        "generated_utc": _generated_utc(),
        "industries": industries,
        "validation": {
            "method": "canonical-cu-primacy-v2",
            "bundle_sha12": input_hashes[BUNDLE_PATH],
            "adjudicated_credits": "included in canonical CU board; CU-specific credits do not enter other industries",
            "tensor": (
                "industry-axes-v1 "
                "(empty-tensor byte-identity gated)"
            ),
        },
        "inputs": input_hashes,
    }
    body = json.dumps(
        _sorted_for_json(payload), ensure_ascii=False, indent=2
    ) + "\n"
    output_path = _output_path()
    if tier_util.PRIMACY_BOARD == 'realized_results_v1':
        from engine.effectiveness_integration import require_shadow_path
        require_shadow_path(output_path, os.environ.get('EFFECTIVENESS_SHADOW_ROOT'))
    parent = os.path.dirname(output_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    write_text_atomic(body, output_path)

    for industry in INDUSTRIES:
        _print_summary(industry, industries[industry]["rows"])
    print(
        "industry tiers: canonical CU primacy + industry evidence boards | %d bytes -> %s"
        % (len(body.encode("utf-8")), output_path)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
