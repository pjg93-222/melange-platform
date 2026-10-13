# -*- coding: utf-8 -*-
"""Build direction-aware organization priors and signed lobbying attention.

Historical priors are computed from ``label_3_class`` only.  The historical
five-class field is checked as part of the input schema but its values are
never read or used.  Current classifications are similarly signed from
``label_3_class`` only, after the benchmarked prompt variant has been pinned by
the position-join receipt.

This module is an artifact lane.  It does not wire the artifacts into a public
surface.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT, LOCAL_MELANGE  # noqa: E501,E702

import argparse
import collections
import copy
import csv
import itertools
import json
import os
import re
import sys


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


PRIORS_SCHEMA = "org-stance-priors-v1"
RECOMPUTE_SCHEMA = "alliance-conflict-recompute-v1"
YEARS = tuple(range(2021, 2027))
STRICT_CHANNELS = frozenset({"formal_strict", "both_strict", "title_strict"})
LABELS = ("Support", "Oppose", "Engage")
SECTORS = ("bank", "credit_union", "crypto", "fintech", "telecom", "financial_other")
# Explicit, not a positional slice of SECTORS: telecom is directional exactly like
# fintech, and financial_other (the catch-all) is the only non-directional sector.
DIRECTIONAL_SECTORS = ("bank", "credit_union", "crypto", "fintech", "telecom")
SECTOR_PAIRS = tuple(itertools.combinations(DIRECTIONAL_SECTORS, 2))
CCCA_BILLS = ("118:s:1838", "118:hr:3881", "117:s:4674", "117:hr:8874")
SIGNED_ATTENTION_FIELDS = (
    "lob_id",
    "client_name",
    "bill_key",
    "org_sector",
    "sign_source",
    "p_support",
    "label",
)
DYAD_SIGN_FIELDS = ("lob_id", "bioguide_id", "support_n", "oppose_n")
BILL_MEMBER_FIELDS = ("bill_key", "bioguide_id", "role", "sponsorship_date")
HISTORICAL_FIELDS = (
    "lob_id",
    "client_name",
    "bill_id",
    "label_3_class",
    "label_5_class",
    "label",
    "annotation_source",
)
INDEX_FIELDS = ("channel", "lob_id", "bill_key", "client_name", "org_sector")
CURRENT_FIELDS = (
    "lob_id",
    "bill_key",
    "label_3_class",
    "model",
    "prompt_variant",
    "classification_status",
    "report_uuid",
    "filing_year",
    "quarter",
)
THREE_CLASS_DISCIPLINE = (
    "Historical priors and current signs use label_3_class ONLY; "
    "label_5_class is never read for computation."
)
PRIOR_DECLARATION = (
    "support_share_directional uses declared Laplace smoothing: "
    "(support_n + 1) / (support_n + oppose_n + 2)."
)
INFERENCE_NOTE = (
    "signed flows are INFERRED and coverage-limited; historical priors end "
    "at the dataset's last congress"
)
_BILL_KEY_RE = re.compile(r"^[1-9][0-9]*:[a-z][a-z0-9]*:[1-9][0-9]*$")
_HISTORICAL_BILL_RE = re.compile(r"^.+-([1-9][0-9]*)$")


class OrgStancePriorsError(RuntimeError):
    """An input or validation contract failed; publish no artifacts."""


class PromptLaneError(OrgStancePriorsError):
    """The benchmarked current-classification lane is not determinable."""


class CCCAValidationError(OrgStancePriorsError):
    """A determinable CCCA bank-by-credit-union row was opposed."""

    def __init__(self, failures):
        self.failures = failures
        super().__init__(
            "CCCA validation anchor failed; determinable bank x credit_union "
            "rows classified OPPOSED: "
            + json.dumps(failures, ensure_ascii=False, sort_keys=True)
        )


def _derived_dir():
    return os.path.join(LOCAL_MELANGE, "lda_rebuild", "derived")


def _default_historical_path(derived):
    return os.path.join(
        os.path.dirname(derived),
        "raw",
        "positions",
        "bill_position_dataset_input",
        "bill_position_dataset.csv",
    )


def _modal(counter):
    if not counter:
        return ""
    largest = max(counter.values())
    return min(
        (value for value, count in counter.items() if count == largest),
        key=lambda value: (value.casefold(), value),
    )


def _open_csv(path, label, required_fields):
    if not os.path.isfile(path):
        raise OrgStancePriorsError(f"{label} not found: {path}")
    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise OrgStancePriorsError(f"{label} unreadable: {exc}") from exc
    reader = csv.DictReader(handle)
    fields = set(reader.fieldnames or ())
    missing = sorted(set(required_fields) - fields)
    if missing:
        handle.close()
        raise OrgStancePriorsError(
            f"{label} lacks required columns: {', '.join(missing)}"
        )
    return handle, reader


def _canonical_bill_key(value, label):
    key = str(value or "").strip()
    if not _BILL_KEY_RE.fullmatch(key):
        raise OrgStancePriorsError(f"{label} is not a canonical bill key: {value!r}")
    return key


def _bill_key_from_object(value, label):
    if not isinstance(value, dict):
        raise OrgStancePriorsError(f"{label} must be an object")
    try:
        congress = int(value.get("congress"))
        number = int(value.get("number"))
    except (TypeError, ValueError) as exc:
        raise OrgStancePriorsError(
            f"{label} congress and number must be integers"
        ) from exc
    bill_type = str(value.get("type") or "").strip().lower()
    return _canonical_bill_key(
        f"{congress}:{bill_type}:{number}", label
    )


def build_historical_priors(path):
    """Return ``lob_id -> prior`` and a receipt, using three-class labels only."""
    handle, reader = _open_csv(
        os.path.abspath(path), "historical position dataset", HISTORICAL_FIELDS
    )
    grouped = {}
    seen = {}
    input_rows = 0
    duplicate_rows = 0
    label_counts = collections.Counter()
    annotation_counts = collections.Counter()
    congress_counts = collections.Counter()
    try:
        for row_number, row in enumerate(reader, start=2):
            input_rows += 1
            lob_id = str(row.get("lob_id") or "").strip()
            client_name = str(row.get("client_name") or "").strip()
            bill_id = str(row.get("bill_id") or "").strip()
            label = str(row.get("label_3_class") or "").strip()
            annotation_source = str(row.get("annotation_source") or "").strip()
            if not lob_id or not client_name or not bill_id or not annotation_source:
                raise OrgStancePriorsError(
                    f"historical position row {row_number} has a blank required value"
                )
            if label not in LABELS:
                raise OrgStancePriorsError(
                    f"historical position row {row_number} has invalid "
                    f"label_3_class {label!r}"
                )
            match = _HISTORICAL_BILL_RE.fullmatch(bill_id)
            if match is None:
                raise OrgStancePriorsError(
                    f"historical position row {row_number} has invalid bill_id "
                    f"{bill_id!r}"
                )
            congress = int(match.group(1))
            pair = (lob_id, bill_id)
            previous = seen.get(pair)
            if previous is not None:
                duplicate_rows += 1
                if previous != label:
                    raise OrgStancePriorsError(
                        f"historical pair {pair!r} has conflicting label_3_class "
                        f"values {previous!r} and {label!r}"
                    )
                continue
            seen[pair] = label
            state = grouped.setdefault(
                lob_id,
                {
                    "names": collections.Counter(),
                    "labels": collections.Counter(),
                    "sources": collections.Counter(),
                    "congresses": [],
                },
            )
            state["names"][client_name] += 1
            state["labels"][label] += 1
            state["sources"][annotation_source] += 1
            state["congresses"].append(congress)
            label_counts[label] += 1
            annotation_counts[annotation_source] += 1
            congress_counts[congress] += 1
    except (OSError, csv.Error) as exc:
        raise OrgStancePriorsError(
            f"historical position dataset unreadable: {exc}"
        ) from exc
    finally:
        handle.close()

    priors = {}
    for lob_id in sorted(grouped):
        state = grouped[lob_id]
        support_n = state["labels"]["Support"]
        oppose_n = state["labels"]["Oppose"]
        engage_n = state["labels"]["Engage"]
        n_bills = support_n + oppose_n + engage_n
        priors[lob_id] = {
            "client_name": _modal(state["names"]),
            "n_bills": n_bills,
            "support_n": support_n,
            "oppose_n": oppose_n,
            "engage_n": engage_n,
            "directional_rate": (support_n + oppose_n) / n_bills,
            "support_share_directional": (
                (support_n + 1) / (support_n + oppose_n + 2)
            ),
            "first_congress_seen": min(state["congresses"]),
            "last_congress_seen": max(state["congresses"]),
            "annotation_source_mix": dict(sorted(state["sources"].items())),
        }
    receipt = {
        "input_path": os.path.abspath(path),
        "input_rows": input_rows,
        "distinct_org_bill_rows": len(seen),
        "duplicate_rows_ignored": duplicate_rows,
        "organizations": len(priors),
        "label_3_class_counts": {
            label: label_counts[label] for label in LABELS
        },
        "annotation_source_counts": dict(sorted(annotation_counts.items())),
        "rows_per_congress": {
            str(congress): congress_counts[congress]
            for congress in sorted(congress_counts)
        },
        "first_congress": min(congress_counts) if congress_counts else None,
        "last_congress": max(congress_counts) if congress_counts else None,
        "three_class_discipline": THREE_CLASS_DISCIPLINE,
        "prior_calculation": PRIOR_DECLARATION,
    }
    return priors, receipt


def _pinned_prompt_variant(join_receipt):
    pins = set()
    classification_input = join_receipt.get("classification_input")
    if isinstance(classification_input, dict) and classification_input.get("variant"):
        pins.add(str(classification_input["variant"]).strip())
    validation = join_receipt.get("validation")
    if isinstance(validation, dict) and validation.get("variant"):
        pins.add(str(validation["variant"]).strip())
    basis = str(join_receipt.get("position_basis") or "").strip()
    if basis.startswith("inferred_") and len(basis) > len("inferred_"):
        pins.add(basis[len("inferred_"):])
    pins.discard("")
    if len(pins) > 1:
        raise PromptLaneError(
            "position_join_receipt.json contains conflicting benchmarked "
            f"prompt variants: {sorted(pins)!r}"
        )
    return next(iter(pins)) if pins else None


def load_join_receipt(path):
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise OrgStancePriorsError(f"position join receipt not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            receipt = json.load(handle)
    except (OSError, ValueError) as exc:
        raise OrgStancePriorsError(f"position join receipt unreadable: {exc}") from exc
    if not isinstance(receipt, dict):
        raise OrgStancePriorsError("position join receipt must be a JSON object")
    accuracy_note = receipt.get("accuracy_note")
    if not isinstance(accuracy_note, str) or not accuracy_note:
        raise OrgStancePriorsError("position join receipt lacks accuracy_note")
    return receipt


def majority_label(labels):
    """Return the three-class majority; every tie resolves to Engage."""
    counts = collections.Counter(labels)
    invalid = sorted(set(counts) - set(LABELS))
    if invalid:
        raise OrgStancePriorsError(
            f"invalid label_3_class values in majority: {invalid!r}"
        )
    if not counts:
        return None
    largest = max(counts.values())
    winners = [label for label, count in counts.items() if count == largest]
    return winners[0] if len(winners) == 1 else "Engage"


def load_kim_majorities(path, join_receipt):
    """Enumerate current lanes, select the receipt-pinned lane, and vote pairs."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise OrgStancePriorsError(f"current position classifications not found: {path}")
    lane_counts = collections.Counter()
    records = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for row_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError as exc:
                    raise OrgStancePriorsError(
                        f"current classification line {row_number} is invalid JSON"
                    ) from exc
                if not isinstance(row, dict):
                    raise OrgStancePriorsError(
                        f"current classification line {row_number} is not an object"
                    )
                missing = sorted(field for field in CURRENT_FIELDS if field not in row)
                if missing:
                    raise OrgStancePriorsError(
                        f"current classification line {row_number} lacks fields: "
                        + ", ".join(missing)
                    )
                model = str(row.get("model") or "").strip()
                variant = str(row.get("prompt_variant") or "").strip()
                status = str(row.get("classification_status") or "").strip()
                lob_id = str(row.get("lob_id") or "").strip()
                label = str(row.get("label_3_class") or "").strip()
                if not model or not variant or not status or not lob_id:
                    raise OrgStancePriorsError(
                        f"current classification line {row_number} has a blank lane/org value"
                    )
                if label not in LABELS:
                    raise OrgStancePriorsError(
                        f"current classification line {row_number} has invalid "
                        f"label_3_class {label!r}"
                    )
                bill_key = _bill_key_from_object(
                    row.get("bill_key"),
                    f"current classification line {row_number} bill_key",
                )
                lane = (model, variant, status)
                lane_counts[lane] += 1
                records.append((lane, lob_id, bill_key, label))
    except OSError as exc:
        raise OrgStancePriorsError(
            f"current position classifications unreadable: {exc}"
        ) from exc

    ok_variants = sorted({lane[1] for lane in lane_counts if lane[2] == "ok"})
    pinned = _pinned_prompt_variant(join_receipt)
    if pinned is None:
        if len(ok_variants) != 1:
            raise PromptLaneError(
                "position join receipt does not pin the benchmarked prompt "
                f"variant; current ok variants are {ok_variants!r}"
            )
        pinned = ok_variants[0]
    if pinned not in ok_variants:
        raise PromptLaneError(
            f"receipt pins prompt variant {pinned!r}, absent from current ok "
            f"variants {ok_variants!r}"
        )

    pair_labels = collections.defaultdict(list)
    selected_rows = 0
    selected_models = set()
    selected_label_counts = collections.Counter()
    for lane, lob_id, bill_key, label in records:
        model, variant, status = lane
        if variant != pinned or status != "ok":
            continue
        selected_rows += 1
        selected_models.add(model)
        selected_label_counts[label] += 1
        pair_labels[(lob_id, bill_key)].append(label)
    majorities = {}
    tie_pairs = 0
    for pair in sorted(pair_labels):
        counts = collections.Counter(pair_labels[pair])
        largest = max(counts.values())
        if sum(count == largest for count in counts.values()) > 1:
            tie_pairs += 1
        majorities[pair] = majority_label(pair_labels[pair])
    lane_rows = [
        {
            "model": model,
            "prompt_variant": variant,
            "classification_status": status,
            "rows": lane_counts[(model, variant, status)],
        }
        for model, variant, status in sorted(lane_counts)
    ]
    receipt = {
        "input_path": path,
        "enumerated_lanes": lane_rows,
        "benchmarked_prompt_variant": pinned,
        "selected_models": sorted(selected_models),
        "selected_classification_status": "ok",
        "selected_rows": selected_rows,
        "selected_label_3_class_counts": {
            label: selected_label_counts[label] for label in LABELS
        },
        "distinct_org_bill_pairs": len(majorities),
        "tie_to_engage_pairs": tie_pairs,
        "three_class_discipline": THREE_CLASS_DISCIPLINE,
    }
    return majorities, receipt


def load_strict_universe(derived):
    """Load and deduplicate the six annual strict org-by-bill indexes."""
    derived = os.path.abspath(derived)
    pairs = {}
    input_rows = 0
    strict_rows = 0
    channel_counts = collections.Counter()
    for year in YEARS:
        path = os.path.join(derived, f"our_bill_lobby_index_{year}.csv")
        handle, reader = _open_csv(path, "bill-lobby index", INDEX_FIELDS)
        try:
            for row_number, row in enumerate(reader, start=2):
                input_rows += 1
                channel = str(row.get("channel") or "").strip()
                channel_counts[channel] += 1
                if channel not in STRICT_CHANNELS:
                    continue
                strict_rows += 1
                lob_id = str(row.get("lob_id") or "").strip()
                client_name = str(row.get("client_name") or "").strip()
                if not lob_id or not client_name:
                    raise OrgStancePriorsError(
                        f"bill-lobby index row {row_number} in {path} has blank org/name"
                    )
                bill_key = _canonical_bill_key(
                    row.get("bill_key"),
                    f"bill-lobby index row {row_number} in {path} bill_key",
                )
                sectors = {
                    token.strip()
                    for token in str(row.get("org_sector") or "").split("|")
                    if token.strip()
                }
                unknown = sorted(sectors - set(SECTORS))
                if unknown:
                    raise OrgStancePriorsError(
                        f"bill-lobby index row {row_number} in {path} has "
                        f"unknown sectors: {unknown!r}"
                    )
                state = pairs.setdefault(
                    (lob_id, bill_key),
                    {"names": collections.Counter(), "sectors": set()},
                )
                state["names"][client_name] += 1
                state["sectors"].update(sectors)
        except (OSError, csv.Error) as exc:
            raise OrgStancePriorsError(f"bill-lobby index unreadable: {exc}") from exc
        finally:
            handle.close()
    universe = []
    for (lob_id, bill_key), state in sorted(pairs.items()):
        universe.append(
            {
                "lob_id": lob_id,
                "client_name": _modal(state["names"]),
                "bill_key": bill_key,
                "org_sector": "|".join(
                    sector for sector in SECTORS if sector in state["sectors"]
                ),
            }
        )
    receipt = {
        "input_paths": [
            os.path.join(derived, f"our_bill_lobby_index_{year}.csv")
            for year in YEARS
        ],
        "input_rows": input_rows,
        "channel_counts": dict(sorted(channel_counts.items())),
        "strict_rows": strict_rows,
        "distinct_strict_org_bill_pairs": len(universe),
        "duplicate_strict_rows_collapsed": strict_rows - len(universe),
        "strict_channels": sorted(STRICT_CHANNELS),
        "org_sector_pipe_rule": (
            "split pipe-delimited sector tokens, union them per org-bill pair, "
            "and emit them in the declared sector order"
        ),
    }
    return universe, receipt


def assign_sign(kim_label, prior):
    """Apply the binding kim -> eligible prior -> none sign ladder."""
    if kim_label is not None:
        if kim_label not in LABELS:
            raise OrgStancePriorsError(f"invalid kim majority label {kim_label!r}")
        p_support = {"Support": 1.0, "Oppose": 0.0, "Engage": ""}[kim_label]
        return {
            "sign_source": "kim",
            "p_support": p_support,
            "label": kim_label,
        }
    if prior is not None:
        n_bills = int(prior.get("n_bills", 0))
        directional_rate = float(prior.get("directional_rate", 0.0))
        if n_bills >= 5 and directional_rate >= 0.25:
            return {
                "sign_source": "prior",
                "p_support": float(prior["support_share_directional"]),
                "label": "prior-imputed",
            }
    return {"sign_source": "none", "p_support": "", "label": ""}


def load_canonical_cu_directions(path):
    """Paul's canonical bill-direction marks -> {bill_key: +1|-1}.

    2026-08-06 ladder fix: the canonical file is the TOP sign authority for
    credit-union-sector rows.  The 2026-08-06 champions incident proved the
    model lane mis-signs CU filings on CFPB-reduction bills that the
    canonical file marks pro-CU; authority order is now
    canonical > kim > prior.  Direction 0 rows and rows without a canonical
    congress fall through to the model ladder.
    """
    directions = {}
    display_re = re.compile(r"^([A-Za-z.]+?)\s*\.?\s*(\d+)$")
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            congress = (row.get("Congress") or "").strip()
            raw_bill = (row.get("Bill") or "").strip()
            raw_direction = (row.get("Direction") or "").strip()
            if not congress or not raw_bill or raw_direction not in ("1", "-1"):
                continue
            compact = raw_bill.replace(".", "").replace(" ", "").upper()
            match = re.match(r"^([A-Z]+)(\d+)$", compact)
            if not match:
                continue
            bill_type = match.group(1).lower()
            bill_key = f"{int(congress)}:{bill_type}:{int(match.group(2))}"
            directions[bill_key] = int(raw_direction)
    return directions


def build_signed_attention(universe, kim_majorities, priors, canonical_cu=None):
    canonical_cu = canonical_cu or {}
    rows = []
    source_counts = collections.Counter()
    label_counts = collections.Counter()
    for source in universe:
        key = (source["lob_id"], source["bill_key"])
        canonical_direction = (
            canonical_cu.get(source["bill_key"])
            if "credit_union" in (source.get("org_sector") or "")
            else None
        )
        if canonical_direction is not None:
            sign = {
                "sign_source": "canonical",
                "p_support": 1.0 if canonical_direction > 0 else 0.0,
                "label": "Support" if canonical_direction > 0 else "Oppose",
            }
        else:
            sign = assign_sign(kim_majorities.get(key), priors.get(source["lob_id"]))
        row = dict(source)
        row.update(sign)
        rows.append(row)
        source_counts[row["sign_source"]] += 1
        if row["label"]:
            label_counts[row["label"]] += 1
    rows.sort(key=lambda row: (row["lob_id"], row["bill_key"]))
    receipt = {
        "rows": len(rows),
        "sign_source_counts": {
            source: source_counts[source]
            for source in ("canonical", "kim", "prior", "none")
        },
        "label_counts": dict(sorted(label_counts.items())),
        "ladder": "canonical (CU-sector rows, Paul's marks) > kim > eligible prior > none",
        "prior_eligibility": {"minimum_n_bills": 5, "minimum_directional_rate": 0.25},
        "side_thresholds": {"support_at_or_above": 0.7, "oppose_at_or_below": 0.3},
    }
    return rows, receipt


def build_dyad_signs(signed_rows, bill_members_path):
    """Join signed org-bill rows to sponsor/cosponsor rosters by canonical bill."""
    bill_members_path = os.path.abspath(bill_members_path)
    handle, reader = _open_csv(
        bill_members_path, "interaction bill members", BILL_MEMBER_FIELDS
    )
    members_by_bill = collections.defaultdict(set)
    membership_rows = 0
    try:
        for row_number, row in enumerate(reader, start=2):
            membership_rows += 1
            bill_key = _canonical_bill_key(
                row.get("bill_key"),
                f"interaction bill members row {row_number} bill_key",
            )
            bioguide_id = str(row.get("bioguide_id") or "").strip()
            if not bioguide_id:
                raise OrgStancePriorsError(
                    f"interaction bill members row {row_number} has blank bioguide_id"
                )
            members_by_bill[bill_key].add(bioguide_id)
    except (OSError, csv.Error) as exc:
        raise OrgStancePriorsError(
            f"interaction bill members unreadable: {exc}"
        ) from exc
    finally:
        handle.close()

    counts = {}
    for row in signed_rows:
        raw_probability = row.get("p_support")
        if raw_probability == "" or raw_probability is None:
            continue
        try:
            probability = float(raw_probability)
        except (TypeError, ValueError) as exc:
            raise OrgStancePriorsError(
                f"signed attention row has invalid p_support {raw_probability!r}"
            ) from exc
        if not 0.0 <= probability <= 1.0:
            raise OrgStancePriorsError(
                f"signed attention row has out-of-range p_support {raw_probability!r}"
            )
        lob_id = str(row.get("lob_id") or "").strip()
        if not lob_id:
            raise OrgStancePriorsError("signed attention row has blank lob_id")
        bill_key = _canonical_bill_key(
            row.get("bill_key"), "signed attention bill_key"
        )
        for bioguide_id in members_by_bill.get(bill_key, ()):
            pair = (lob_id, bioguide_id)
            pair_counts = counts.setdefault(pair, [0, 0])
            if probability >= 0.7:
                pair_counts[0] += 1
            elif probability <= 0.3:
                pair_counts[1] += 1

    rows = [
        {
            "lob_id": lob_id,
            "bioguide_id": bioguide_id,
            "support_n": pair_counts[0],
            "oppose_n": pair_counts[1],
        }
        for (lob_id, bioguide_id), pair_counts in sorted(counts.items())
    ]
    receipt = {
        "rows": len(rows),
        "orgs": len({row["lob_id"] for row in rows}),
        "members": len({row["bioguide_id"] for row in rows}),
        "input_path": bill_members_path,
        "input_rows": membership_rows,
        "distinct_bill_member_rows": sum(
            len(members) for members in members_by_bill.values()
        ),
        "side_thresholds": {
            "support_at_or_above": 0.7,
            "oppose_at_or_below": 0.3,
        },
    }
    return rows, receipt


def _sector_side(rows, sector):
    support_orgs = set()
    oppose_orgs = set()
    carrying_orgs = set()
    for row in rows:
        sectors = set(str(row.get("org_sector") or "").split("|"))
        if sector not in sectors:
            continue
        lob_id = str(row.get("lob_id") or "")
        carrying_orgs.add(lob_id)
        p_support = row.get("p_support")
        if p_support == "" or p_support is None:
            continue
        probability = float(p_support)
        if probability >= 0.7:
            support_orgs.add(lob_id)
        elif probability <= 0.3:
            oppose_orgs.add(lob_id)
    signed_n = len(support_orgs) + len(oppose_orgs)
    if signed_n < 2 or len(support_orgs) == len(oppose_orgs):
        side = "UNKNOWN"
    elif len(support_orgs) > len(oppose_orgs):
        side = "Support"
    else:
        side = "Oppose"
    return {
        "side": side,
        "carrying_orgs": len(carrying_orgs),
        "signed_orgs": signed_n,
        "support_orgs": len(support_orgs),
        "oppose_orgs": len(oppose_orgs),
    }


def classify_pair_on_bill(rows, sector_a, sector_b):
    """Classify one sector pair on one bill, including the two-org floor."""
    side_a = _sector_side(rows, sector_a)
    side_b = _sector_side(rows, sector_b)
    known = side_a["side"] != "UNKNOWN" and side_b["side"] != "UNKNOWN"
    if not known:
        label = "UNKNOWN"
    elif side_a["side"] == side_b["side"]:
        label = "ALIGNED"
    else:
        label = "OPPOSED"
    relevant_orgs = {
        str(row.get("lob_id") or "")
        for row in rows
        if sector_a in set(str(row.get("org_sector") or "").split("|"))
        or sector_b in set(str(row.get("org_sector") or "").split("|"))
    }
    return {
        "label": label,
        "org_count": len(relevant_orgs),
        "sector_sides": {sector_a: side_a, sector_b: side_b},
    }


def _signed_rows_for_anchor(rows, bill_key):
    result = []
    for row in rows:
        if row["bill_key"] != bill_key:
            continue
        sectors = set(str(row.get("org_sector") or "").split("|"))
        if sectors.intersection({"bank", "credit_union"}):
            result.append({field: row.get(field, "") for field in SIGNED_ATTENTION_FIELDS})
    return result


def recompute_alliance_conflict(signed_rows):
    """Recompute the directional-sector pair tables and enforce the CCCA validation anchor."""
    by_bill = collections.defaultdict(list)
    for row in signed_rows:
        by_bill[row["bill_key"]].append(row)
    pair_documents = []
    pair_lookup = {}
    overall = collections.Counter()
    for sector_a, sector_b in SECTOR_PAIRS:
        bills = []
        label_counts = collections.Counter()
        for bill_key in sorted(by_bill):
            bill_rows = by_bill[bill_key]
            present = set()
            for row in bill_rows:
                present.update(
                    token
                    for token in str(row.get("org_sector") or "").split("|")
                    if token
                )
            if sector_a not in present or sector_b not in present:
                continue
            classified = classify_pair_on_bill(bill_rows, sector_a, sector_b)
            item = {"bill_key": bill_key, **classified}
            bills.append(item)
            label_counts[item["label"]] += 1
            overall[item["label"]] += 1
        top_bills = sorted(
            bills, key=lambda item: (-item["org_count"], item["bill_key"])
        )[:15]
        pair_document = {
            "sector_a": sector_a,
            "sector_b": sector_b,
            "pair_key": f"{sector_a}|{sector_b}",
            "totals": {
                "bills": len(bills),
                "ALIGNED": label_counts["ALIGNED"],
                "OPPOSED": label_counts["OPPOSED"],
                "UNKNOWN": label_counts["UNKNOWN"],
            },
            "top_15_bills_by_org_count": top_bills,
        }
        pair_documents.append(pair_document)
        pair_lookup[(sector_a, sector_b)] = {item["bill_key"]: item for item in bills}

    anchor_rows = []
    failures = []
    bank_cu = pair_lookup[("bank", "credit_union")]
    for bill_key in CCCA_BILLS:
        pair_row = bank_cu.get(bill_key)
        if pair_row is None:
            anchor = {"bill_key": bill_key, "label": "NOT_IN_PAIR_UNIVERSE"}
        else:
            anchor = {
                "bill_key": bill_key,
                "label": pair_row["label"],
                "org_count": pair_row["org_count"],
                "sector_sides": pair_row["sector_sides"],
            }
        anchor_rows.append(anchor)
        if anchor["label"] == "OPPOSED":
            failures.append(
                {
                    **anchor,
                    "underlying_signed_rows": _signed_rows_for_anchor(
                        signed_rows, bill_key
                    ),
                }
            )
    if failures:
        raise CCCAValidationError(failures)

    pair_bill_rows = sum(overall.values())
    known = overall["ALIGNED"] + overall["OPPOSED"]
    document = {
        "schema": RECOMPUTE_SCHEMA,
        "sector_pairs": pair_documents,
        "overall_coverage": {
            "pair_bill_rows": pair_bill_rows,
            "known_pair_bill_rows": known,
            "unknown_pair_bill_rows": overall["UNKNOWN"],
            "coverage_rate": known / pair_bill_rows if pair_bill_rows else 0.0,
            "ALIGNED": overall["ALIGNED"],
            "OPPOSED": overall["OPPOSED"],
            "UNKNOWN": overall["UNKNOWN"],
        },
        "validation_anchor": {
            "name": "Credit Card Competition Act bank x credit_union",
            "rule": (
                "Every determinable CCCA bank x credit_union row must be ALIGNED; "
                "an OPPOSED row halts publication."
            ),
            "passed": True,
            "rows": anchor_rows,
        },
    }
    return document


def _honesty_receipt(join_receipt):
    receipt = {
        "accuracy_note": join_receipt["accuracy_note"],
        "inference_note": INFERENCE_NOTE,
    }
    if "validation" in join_receipt:
        receipt["accuracy_validation"] = copy.deepcopy(join_receipt["validation"])
    return receipt


def build_artifacts(
    derived=None,
    historical_path=None,
    kim_path=None,
    join_receipt_path=None,
    bill_members_path=None,
):
    """Load real inputs and return all four in-memory artifact payloads."""
    derived = os.path.abspath(derived or _derived_dir())
    historical_path = os.path.abspath(
        historical_path or _default_historical_path(derived)
    )
    interactions = os.path.join(derived, "interactions")
    kim_path = os.path.abspath(
        kim_path or os.path.join(interactions, "bill_positions_kim.jsonl")
    )
    join_receipt_path = os.path.abspath(
        join_receipt_path or os.path.join(interactions, "position_join_receipt.json")
    )
    bill_members_path = os.path.abspath(
        bill_members_path
        or os.path.join(interactions, "interaction_bill_members.csv")
    )

    join_receipt = load_join_receipt(join_receipt_path)
    priors, historical_receipt = build_historical_priors(historical_path)
    kim_majorities, kim_receipt = load_kim_majorities(kim_path, join_receipt)
    universe, universe_receipt = load_strict_universe(derived)
    canonical_cu = load_canonical_cu_directions(
        os.path.join(MB_ROOT, "data", "legislation", "bill_directions.csv")
    )
    signed_rows, signed_receipt = build_signed_attention(
        universe, kim_majorities, priors, canonical_cu=canonical_cu
    )
    dyad_rows, dyad_receipt = build_dyad_signs(signed_rows, bill_members_path)
    alliance_document = recompute_alliance_conflict(signed_rows)

    honesty = _honesty_receipt(join_receipt)
    priors_document = {
        "schema": PRIORS_SCHEMA,
        "priors": priors,
        "receipt": {
            **historical_receipt,
            **honesty,
            "artifact": "org_directional_priors.json",
        },
    }
    alliance_document["receipt"] = {
        **honesty,
        "artifact": "alliance_conflict_recompute.json",
        "three_class_discipline": THREE_CLASS_DISCIPLINE,
        "current_classifications": kim_receipt,
        "strict_universe": universe_receipt,
        "signed_attention": {
            **signed_receipt,
            "artifact": "signed_attention.csv",
            **honesty,
        },
        "dyad_signs": {
            **dyad_receipt,
            "artifact": "dyad_signs.csv",
            **honesty,
        },
        "input_paths": {
            "historical": historical_path,
            "current_classifications": kim_path,
            "position_join_receipt": join_receipt_path,
            "interaction_bill_members": bill_members_path,
        },
    }
    return priors_document, signed_rows, dyad_rows, alliance_document


def _validate_document_anchor(alliance_document):
    anchor = alliance_document.get("validation_anchor") or {}
    failures = [
        row for row in anchor.get("rows", ()) if row.get("label") == "OPPOSED"
    ]
    if failures or not anchor.get("passed"):
        raise CCCAValidationError(failures or [{"validation_anchor": anchor}])


def write_outputs(
    out_dir, priors_document, signed_rows, dyad_rows, alliance_document
):
    """Atomically publish the four artifacts only after the CCCA gate passes."""
    _validate_document_anchor(alliance_document)
    out_dir = os.path.abspath(out_dir)
    paths = {
        "priors": os.path.join(out_dir, "org_directional_priors.json"),
        "signed_attention": os.path.join(out_dir, "signed_attention.csv"),
        "dyad_signs": os.path.join(out_dir, "dyad_signs.csv"),
        "alliance_conflict": os.path.join(
            out_dir, "alliance_conflict_recompute.json"
        ),
    }
    os.makedirs(out_dir, exist_ok=True)
    temporary_paths = {key: f"{path}.tmp.{os.getpid()}" for key, path in paths.items()}
    try:
        with open(
            temporary_paths["priors"], "w", encoding="utf-8", newline="\n"
        ) as handle:
            json.dump(
                priors_document,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            handle.write("\n")
        with open(
            temporary_paths["signed_attention"],
            "w",
            encoding="utf-8",
            newline="",
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=SIGNED_ATTENTION_FIELDS,
                extrasaction="raise",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(signed_rows)
        with open(
            temporary_paths["dyad_signs"],
            "w",
            encoding="utf-8",
            newline="",
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=DYAD_SIGN_FIELDS,
                extrasaction="raise",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(dyad_rows)
        with open(
            temporary_paths["alliance_conflict"],
            "w",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            json.dump(
                alliance_document,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            handle.write("\n")
        for key in (
            "priors",
            "signed_attention",
            "dyad_signs",
            "alliance_conflict",
        ):
            os.replace(temporary_paths[key], paths[key])
    except BaseException:
        for temporary in temporary_paths.values():
            try:
                os.remove(temporary)
            except OSError:
                pass
        raise
    return paths


def _parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--derived", default=None)
    parser.add_argument("--historical", default=None)
    parser.add_argument("--kim", default=None)
    parser.add_argument("--join-receipt", default=None)
    parser.add_argument("--bill-members", default=None)
    parser.add_argument("--out-dir", default=None)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        derived = os.path.abspath(args.derived or _derived_dir())
        out_dir = os.path.abspath(
            args.out_dir or os.path.join(derived, "stance_priors")
        )
        priors, signed_rows, dyad_rows, alliance = build_artifacts(
            derived=derived,
            historical_path=args.historical,
            kim_path=args.kim,
            join_receipt_path=args.join_receipt,
            bill_members_path=args.bill_members,
        )
        paths = write_outputs(out_dir, priors, signed_rows, dyad_rows, alliance)
    except OrgStancePriorsError as exc:
        print(f"ORG STANCE PRIORS ERROR: {exc}", file=sys.stderr)
        return 2
    source_counts = alliance["receipt"]["signed_attention"]["sign_source_counts"]
    coverage = alliance["overall_coverage"]
    print(
        "org stance priors: "
        f"{len(priors['priors']):,} historical orgs; "
        f"{len(signed_rows):,} strict org-bill rows "
        f"(kim={source_counts['kim']:,}, prior={source_counts['prior']:,}, "
        f"none={source_counts['none']:,}); "
        f"{len(dyad_rows):,} signed org-member dyads; "
        f"pair coverage={coverage['known_pair_bill_rows']:,}/"
        f"{coverage['pair_bill_rows']:,} -> {paths['alliance_conflict']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
