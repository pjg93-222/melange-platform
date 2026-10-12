from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "audits" / "staffer_network" / "filter_senate_payroll_employment_candidates.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("filter_senate_payroll_employment_candidates", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _row(payee: str | None, title: str | None) -> dict:
    return {
        "date_intervals": [{"start": "2011-04-01", "end": "2011-09-30", "basis": "REPORT_PERIOD_ONLY"}],
        "payee_name_raw": payee,
        "report_period_end": "2011-09-30",
        "report_period_start": "2011-04-01",
        "title_candidate": title,
        "title_raw": title,
    }


def test_accepts_person_shape_and_role_title() -> None:
    assert MODULE.rejection_reason(_row("SANCHEZ, JOSHUA S", "CONSTITUENT SERVICE REPRESENTATIVE")) is None


def test_rejects_historical_route_false_positive() -> None:
    assert MODULE.rejection_reason(
        _row("SANTA FE TO GALLUP, ZUNI PUEBLO AND RETURN", "STAFF TRANSPORTATION")
    ) == "ROUTE_TEXT_AS_PAYEE"


def test_rejects_non_employment_expense_title() -> None:
    assert MODULE.rejection_reason(
        _row("EXAMPLE, COMPANY", "OTHER CONTRACTUAL SERVICES")
    ) == "NON_EMPLOYMENT_EXPENSE_CATEGORY"


def test_rejects_reversed_interval_even_for_valid_role() -> None:
    row = _row("PUBLIC, JANE Q", "LEGISLATIVE ASSISTANT")
    row["date_intervals"] = [{"start": "2011-09-30", "end": "2011-04-01", "basis": "BAD"}]
    assert MODULE.rejection_reason(row) == "INVALID_OR_OUT_OF_PERIOD_INTERVAL"


def test_repairs_historical_title_surname_join_from_raw_evidence() -> None:
    row = _row("INEZ, RANDOLPH A", "PRINTING & REPROGRAPHICS GENERALISTMART")
    row.update({
        "evidence_quote": "38,906.92PRINTING & REPROGRAPHICS GENERALISTMARTINEZ, RANDOLPH A",
        "parse_status": "HISTORICAL_REVERSE_LAYOUT_HEURISTIC_SPLIT",
    })
    assert MODULE.repair_historical_boundary(row) is True
    assert row["payee_name_raw"] == "MARTINEZ, RANDOLPH A"
    assert row["title_candidate"] == "PRINTING & REPROGRAPHICS GENERALIST"
    assert row["boundary_repair_status"] == "RAW_EVIDENCE_HEURISTIC_REPAIR_REVIEW_REQUIRED"
