from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "audits" / "staffer_network" / "build_official_senate_payroll_lane.py"
SPEC = importlib.util.spec_from_file_location("build_official_senate_payroll_lane", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_resolve_qualifier_date_is_period_bounded_and_calendar_valid() -> None:
    assert MODULE.resolve_qualifier_date("APR", 1, "2011-04-01", "2011-09-30") == "2011-04-01"
    assert MODULE.resolve_qualifier_date("OCT", 1, "2011-04-01", "2011-09-30") is None
    assert MODULE.resolve_qualifier_date("FEB", 30, "2011-04-01", "2011-09-30") is None


def test_parse_qualifiers_fails_closed_on_out_of_period_date() -> None:
    qualifiers, intervals, title = MODULE.parse_qualifiers(
        "LEGISLATIVE AIDE FROM OCT. 1", "2011-04-01", "2011-09-30"
    )
    assert qualifiers[0]["resolved_date"] is None
    assert intervals == [{
        "start": "2011-04-01",
        "end": "2011-09-30",
        "basis": "INVALID_OR_OUT_OF_PERIOD_QUALIFIER_REVIEW_REQUIRED",
    }]
    assert title == "LEGISLATIVE AIDE"


def test_parse_qualifiers_fails_closed_on_reversed_range() -> None:
    qualifiers, intervals, title = MODULE.parse_qualifiers(
        "COUNSEL FROM SEP. 20 TO APR. 2", "2011-04-01", "2011-09-30"
    )
    assert all(item["resolved_date"] for item in qualifiers)
    assert intervals == [{
        "start": "2011-04-01",
        "end": "2011-09-30",
        "basis": "INVALID_OR_REVERSED_QUALIFIER_RANGE_REVIEW_REQUIRED",
    }]
    assert title == "COUNSEL"


def test_historical_reverse_layout_line_is_split_without_identity_promotion() -> None:
    rows = MODULE.extract_old_layout_rows(
        "11,510.37CONSTITUENT SERVICE REPRESENTATIVE FROM JUN. 7SANCHEZ, JOSHUA S\n"
    )
    assert rows == [{
        "amount": "11,510.37",
        "payee": "SANCHEZ, JOSHUA S",
        "raw": "11,510.37CONSTITUENT SERVICE REPRESENTATIVE FROM JUN. 7SANCHEZ, JOSHUA S",
        "status": "HISTORICAL_REVERSE_LAYOUT_HEURISTIC_SPLIT",
        "title": "CONSTITUENT SERVICE REPRESENTATIVE FROM JUN. 7",
    }]


def test_historical_reverse_layout_handles_title_commas_and_single_initials() -> None:
    rows = MODULE.extract_old_layout_rows(
        "23,122.92COORDINATOR, INTERNET STRATEGY AND COMMUNICATIONS OUTREACHFOX, LEANNE A\n"
        "16,500.00STAFF ASSISTANTROUSSEAU, M\n"
    )
    assert [row["payee"] for row in rows] == ["FOX, LEANNE A", "ROUSSEAU, M"]
    assert [row["title"] for row in rows] == [
        "COORDINATOR, INTERNET STRATEGY AND COMMUNICATIONS OUTREACH",
        "STAFF ASSISTANT",
    ]


def test_historical_reverse_layout_rejects_net_payroll_summary() -> None:
    assert MODULE.extract_old_layout_rows("0.00 -1,542,574.59Net Payroll Expenses\n") == []


def test_historical_boundary_does_not_match_role_token_inside_surname() -> None:
    payee, title, status = MODULE.split_old_inline_title_payee(
        "PRINTING & REPROGRAPHICS GENERALISTMARTINEZ, RANDOLPH A"
    )
    assert payee == "MARTINEZ, RANDOLPH A"
    assert title == "PRINTING & REPROGRAPHICS GENERALIST"
    assert status == "HISTORICAL_REVERSE_LAYOUT_HEURISTIC_SPLIT"
    payee, title, _ = MODULE.split_old_inline_title_payee("ASSISTANT TO THE CHAIRMANPAGAN, FRED W")
    assert payee == "PAGAN, FRED W"
    assert title == "ASSISTANT TO THE CHAIRMAN"
