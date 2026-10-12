# -*- coding: utf-8 -*-
"""Audit whether a Greenlit staged integration can enter the canonical shadow."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from openpyxl import load_workbook


SCHEMA = "legislation-greenlit-shadow-readiness-audit-v2"
INTEGRATION_SCHEMA = "legislation-greenlit-culling-integration-receipt-v1"
PAIRED_SHADOW_SCHEMA = "legislation-greenlit-paired-rank-shadow-v2"


class ReadinessError(ValueError):
    """The staged integration or its bound outputs have drifted."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ReadinessError(f"JSON root is not an object: {path}")
    return value


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        if not line:
            raise ReadinessError(f"{path}:{number}: blank NDJSON line")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ReadinessError(f"{path}:{number}: row is not an object")
        rows.append(value)
    return rows


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\n"
    )
    os.replace(temporary, path)


def receipt(path: Path, records: int | None = None) -> dict[str, Any]:
    result = {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if records is not None:
        result["records"] = records
    return result


def verify_output(
    root: Path,
    name: str,
    declared: Mapping[str, Any],
) -> tuple[Path, int]:
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        raise ReadinessError(f"integration output is absent: {name}")
    records = (
        sum(1 for _ in path.open(encoding="utf-8-sig"))
        if path.suffix == ".ndjson"
        else 1
    )
    actual = receipt(path, records)
    if any(
        actual[field] != declared.get(field)
        for field in ("bytes", "sha256", "records")
    ):
        raise ReadinessError(f"integration output drifted: {name}")
    return path, records


def audit(
    integration_receipt: Path,
    out: Path,
    *,
    baseline_workbook: Path | None = None,
    paired_shadow_receipt: Path | None = None,
) -> dict[str, Any]:
    integration_receipt = integration_receipt.resolve()
    root = integration_receipt.parent
    value = read_object(integration_receipt)
    if not (
        value.get("schema") == INTEGRATION_SCHEMA
        and value.get("status")
        == "PASS_STAGED_INTEGRATION_WITH_HELD_NONSCORING"
    ):
        raise ReadinessError("integration receipt is not a staged PASS")
    unsealed = dict(value)
    declared_seal = unsealed.pop("record_content_sha256", None)
    if declared_seal != hashlib.sha256(
        canonical_bytes(unsealed)
    ).hexdigest():
        raise ReadinessError("integration receipt seal drifted")
    outputs = value.get("outputs")
    if not isinstance(outputs, dict):
        raise ReadinessError("integration receipt has no outputs")
    edges_path, edge_count = verify_output(
        root,
        "integrated_membership_edges.ndjson",
        outputs.get("integrated_membership_edges.ndjson") or {},
    )
    held_path, held_count = verify_output(
        root,
        "held_visible_non_scoring.ndjson",
        outputs.get("held_visible_non_scoring.ndjson") or {},
    )
    edges = read_ndjson(edges_path)
    held = read_ndjson(held_path)
    if edge_count != 93 or held_count != 1184:
        raise ReadinessError("integrated edge/hold counts drifted")

    scoring = [row for row in edges if row.get("scoring_eligible") is True]
    membership_only = [
        row for row in edges if row.get("scoring_eligible") is not True
    ]
    cfpb_sponsor_edges = [
        row
        for row in scoring
        if row.get("category_id") == "cfpb_issues_structural_reform"
    ]
    if len(scoring) != 74 or len(membership_only) != 19:
        raise ReadinessError("scoring/member-only edge partition drifted")
    if len(cfpb_sponsor_edges) != 9:
        raise ReadinessError("expected nine CFPB amendment scoring edges")

    repo = Path(__file__).resolve().parents[2]
    if baseline_workbook is None:
        baseline_workbook = (
            repo
            / "audits"
            / "legfix5"
            / "production_baseline_full_downstream"
            / "files"
            / "Moneyball Main Version - Blended.xlsx"
        )
    baseline_workbook = baseline_workbook.resolve()
    if not baseline_workbook.is_file():
        raise ReadinessError("frozen baseline workbook is absent")
    workbook = load_workbook(
        baseline_workbook,
        read_only=True,
        data_only=False,
    )
    try:
        raw = workbook["Legislative Score - Raw"]
        rank_formula = str(workbook["Rank & Allocation"]["D3"].value or "")
        route_evidence = {
            "restrain_header": raw["CU1"].value,
            "restrain_range": "CU:DB",
            "restrain_rank_multiplier": 0.5,
            "reach_header": raw["EO1"].value,
            "reach_range": "EO:EV",
            "reach_rank_multiplier": -1.0,
            "rank_formula_uses_restrain_activity": (
                "$CU$3:$CY$535" in rank_formula
            ),
            "rank_formula_uses_reach_activity": (
                "$EO$3:$ES$535" in rank_formula
                and "*(-1)" in rank_formula
            ),
        }
    finally:
        workbook.close()
    route_checks = {
        "restrain_block_exists": (
            route_evidence["restrain_header"] == "CFPB Restrain - Sponsor"
        ),
        "reach_block_exists": (
            route_evidence["reach_header"] == "CFPB Reach (-) - Sponsor"
        ),
        "rank_formula_scores_restrain_positive": (
            route_evidence["rank_formula_uses_restrain_activity"]
        ),
        "rank_formula_scores_reach_negative": (
            route_evidence["rank_formula_uses_reach_activity"]
        ),
        "greenlit_cfpb_signs_are_routable": all(
            row.get("ultimate_direction") in {-1, 1}
            for row in cfpb_sponsor_edges
        ),
    }
    if not all(route_checks.values()):
        raise ReadinessError("existing CFPB Restrain/Reach route drifted")

    source_contracts = {
        "accepted_generation": (
            repo / "fec" / "build_accepted_legislation_generation.py"
        ),
        "shadow_runner": (
            repo / "fec" / "legislation" / "run_greenlit_legislation_rank_shadow.py"
        ),
    }
    for label, path in source_contracts.items():
        if not path.is_file():
            raise ReadinessError(f"source contract is absent: {label}")
    accepted_source = source_contracts["accepted_generation"].read_text(
        encoding="utf-8-sig"
    )
    shadow_source = source_contracts["shadow_runner"].read_text(
        encoding="utf-8-sig"
    )
    contract_checks = {
        "accepted_generation_rejects_unresolved_memberships": (
            "reconciliation still has unresolved memberships"
            in accepted_source
            and "membership review manifest is unresolved" in accepted_source
        ),
        "isolated_shadow_uses_existing_cfpb_blocks": (
            '"CFPB Restrain"' in shadow_source
            and '"CFPB Reach (-)"' in shadow_source
            and "production_hashes_unchanged" in shadow_source
        ),
    }
    if not all(contract_checks.values()):
        raise ReadinessError("one or more shadow contracts drifted")

    paired: dict[str, Any] | None = None
    if paired_shadow_receipt is not None:
        paired_shadow_receipt = paired_shadow_receipt.resolve()
        paired = read_object(paired_shadow_receipt)
        if not (
            paired.get("schema") == PAIRED_SHADOW_SCHEMA
            and paired.get("status") == "PASS_ISOLATED_PAIRED_RANK_SHADOW"
            and paired.get("integration_generation_id")
            == value.get("generation_id")
            and all((paired.get("coverage") or {}).values())
            and (paired.get("safety") or {}).get(
                "production_hashes_unchanged"
            )
            is True
        ):
            raise ReadinessError("paired shadow receipt is not a bound PASS")

    rank_delta = (
        {
            "status": "COMPUTED_PAIRED_SHADOW",
            **(paired.get("rank_delta_summary") or {}),
            "control_replay": paired.get("control_replay"),
            "receipt": receipt(paired_shadow_receipt),
        }
        if paired is not None
        else {
            "status": "READY_NOT_YET_COMPUTED",
            "members_with_rank_change": None,
            "max_abs_rank_delta": None,
        }
    )
    result = {
        "schema": SCHEMA,
        "status": (
            "PASS_ISOLATED_PAIRED_SHADOW_COMPLETE"
            if paired is not None
            else "PASS_READY_FOR_ISOLATED_PAIRED_SHADOW"
        ),
        "integration_generation_id": value.get("generation_id"),
        "integration_receipt": receipt(integration_receipt, 1),
        "counts": {
            "integrated_category_edges": len(edges),
            "scoring_ready_category_edges": len(scoring),
            "membership_only_non_scoring_category_edges": len(membership_only),
            "held_decision_units": len(held),
            "cfpb_scoring_edges_routed": len(cfpb_sponsor_edges),
        },
        "contract_checks": contract_checks,
        "cfpb_route_checks": route_checks,
        "cfpb_route_evidence": route_evidence,
        "source_contracts": {
            label: receipt(path, 1)
            for label, path in source_contracts.items()
        },
        "blocking_issues": [],
        "rank_delta": rank_delta,
        "remaining_promotion_gates": [
            {
                "code": "held_visible_non_scoring_not_canonicalized",
                "detail": (
                    "The 1,184 Greenlit holds are preserved and excluded from "
                    "scoring, but production promotion still requires an "
                    "explicit canonical hold representation."
                ),
            },
            {
                "code": "production_promotion_not_authorized",
                "detail": (
                    "The Greenlight authorized integration and an isolated "
                    "shadow, not a production workbook/rank promotion."
                ),
            },
        ],
        "safety": {
            "shadow_execution_started": paired is not None,
            "shadow_execution_passed": paired is not None,
            "production_written": False,
            "rank_written": False,
            "promotion_performed": False,
            "external_sync_performed": False,
        },
    }
    result["record_content_sha256"] = hashlib.sha256(
        canonical_bytes(result)
    ).hexdigest()
    atomic_json(out.resolve(), result)
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--integration-receipt", type=Path, required=True)
    result.add_argument("--baseline-workbook", type=Path)
    result.add_argument("--paired-shadow-receipt", type=Path)
    result.add_argument("--out", type=Path, required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = audit(
            args.integration_receipt,
            args.out,
            baseline_workbook=args.baseline_workbook,
            paired_shadow_receipt=args.paired_shadow_receipt,
        )
    except (ReadinessError, OSError, json.JSONDecodeError) as exc:
        print(f"Greenlit shadow readiness: FAIL CLOSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if result["status"].startswith("PASS") else 3


if __name__ == "__main__":
    raise SystemExit(main())
