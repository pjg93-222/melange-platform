# -*- coding: utf-8 -*-
"""Standalone synthetic tests for the LobbyView network repository."""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))); from paths import ROOT as MB_ROOT

import csv
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from lobbying import collect_lobbyview as CL
from surfaces import make_lobby_network_html as MLH


P = F = 0


def ok(name, cond, detail=""):
    global P, F
    P, F = (P + 1, F) if cond else (P, F + 1)
    print(
        ("  OK   " if cond else "FAIL  ")
        + name
        + (("  :: " + str(detail)) if (detail and not cond) else "")
    )


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def network_raises(path, client_rows, rows, expected_rows=None):
    write_csv(path, CL.NETWORK_INPUT_FIELDS, rows)
    try:
        CL._process_network(path, client_rows, expected_rows=expected_rows)
        return False
    except CL.IngestError:
        return True


def main():
    fixture = tempfile.mkdtemp()
    clients_path = os.path.join(fixture, "clients.csv")
    network_path = os.path.join(fixture, "network.csv")
    write_csv(
        clients_path,
        CL.CLIENT_INPUT_FIELDS,
        [
            ["cu-1", "", "", "522130", "TEST CREDIT UNION"],
            ["cuso-1", "", "", "", "STATEWIDE CUSO SERVICES"],
            ["bank-1", "", "", "522110", "TEST NATIONAL BANK"],
            ["fin-1", "", "", "522320", "PAYMENT SERVICES LLC"],
            ["other-1", "", "", "", "UNMATCHED ORGANIZATION"],
        ],
    )
    client_rows, _counts, _adversaries, _confidence, _info = CL._load_clients(
        clients_path
    )
    by_lob_id = {row["lob_id"]: row for row in client_rows}
    ok(
        "CUSO name rule classifies as credit_union",
        by_lob_id["cuso-1"]["side"] == "credit_union"
        and by_lob_id["cuso-1"]["match_rule"] == "name:credit-union",
        by_lob_id["cuso-1"],
    )
    ok(
        "unmatched client name remains classified nonfinancial",
        by_lob_id["other-1"]["side"] == "other",
        by_lob_id["other-1"],
    )
    ok(
        "financial_other classification remains distinct",
        by_lob_id["fin-1"]["side"] == "financial_other",
        by_lob_id["fin-1"],
    )

    network_rows = [
        ["cu-1", "A000001", "2021", "3"],
        ["cuso-1", "A000001", "2021", "2"],
        ["cu-1", "A000001", "2023", "4"],
        ["bank-1", "B000002", "2021", "1"],
        ["bank-1", "B000002", "2022", "4"],
        ["other-1", "C000003", "2022", "2"],
        ["not-in-clients", "D000004", "2022", "5"],
        ["cuso-1", "E000005", "2022", "6"],
        ["fin-1", "F000006", "2022", "7"],
        ["cu-1", "   ", "2005", "11"],
    ]
    write_csv(network_path, CL.NETWORK_INPUT_FIELDS, network_rows)
    result = CL._process_network(
        network_path, client_rows, expected_rows=len(network_rows)
    )
    member_by_key = {
        (row["industry_set"], row["bioguide"], row["year"]): row
        for row in result["member_rows"]
    }
    cu_2021 = member_by_key[("credit_union", "A000001", 2021)]
    ok(
        "network aggregation sums bills and distinct clients exactly",
        cu_2021["n_bills_sum"] == 5 and cu_2021["n_clients"] == 2,
        cu_2021,
    )
    cu_top = {
        row["bioguide"]: row
        for row in result["per_industry"]["credit_union"][
            "top_members_2021_2022"
        ]
    }
    ok(
        "absent 2022 dyad zero-fills YoY exactly",
        cu_top["A000001"]["n_bills_2021"] == 5
        and cu_top["A000001"]["n_bills_2022"] == 0
        and cu_top["A000001"]["yoy_change"] == -5,
        cu_top["A000001"],
    )
    coverage = result["coverage"]
    baseline_path = os.path.join(fixture, "baseline_network.csv")
    write_csv(baseline_path, CL.NETWORK_INPUT_FIELDS, network_rows[:-1])
    baseline_result = CL._process_network(
        baseline_path, client_rows, expected_rows=len(network_rows) - 1
    )
    ok(
        "blank bioguide is receipted and excluded from every aggregate",
        coverage["n_blank_bioguide_rows"] == 1
        and coverage["blank_bioguide_n_bills"] == 11
        and baseline_result["coverage"]["n_blank_bioguide_rows"] == 0
        and baseline_result["coverage"]["blank_bioguide_n_bills"] == 0
        and sum(coverage["rows_by_industry_set"].values())
        + coverage["n_blank_bioguide_rows"]
        == coverage["rows_read"]
        and result["member_rows"] == baseline_result["member_rows"]
        and result["per_industry"] == baseline_result["per_industry"]
        and result["review_rows"] == baseline_result["review_rows"]
        and coverage["rows_by_industry_set"]
        == baseline_result["coverage"]["rows_by_industry_set"],
        coverage,
    )
    ok(
        "unclassified row receipt is exact and no row is dropped",
        coverage["rows_read"] == 10
        and coverage["rows_matched_classified"] == 9
        and coverage["unclassified_row_share"] == 1 / 10
        and member_by_key[("unclassified", "D000004", 2022)][
            "n_bills_sum"
        ]
        == 5,
        coverage,
    )
    ok(
        "coverage receipts carry distinct members and year range",
        coverage["distinct_bioguides"] == 6
        and coverage["year_min"] == 2021
        and coverage["year_max"] == 2023,
        coverage,
    )
    derived_caveats = CL._network_caveats(coverage["year_max"])
    ok(
        "network coverage caveat derives the observed terminal year",
        "coverage ends 2023 (same cliff family as issue codes); the FEC lane is the only fresh member-level money signal after"
        in derived_caveats,
        derived_caveats,
    )
    ok(
        "all five industry sets survive aggregation and coverage accounting",
        set(result["per_industry"]) == set(CL.NETWORK_INDUSTRY_SETS)
        and {row["industry_set"] for row in result["member_rows"]}
        == set(CL.NETWORK_INDUSTRY_SETS)
        and coverage["rows_by_industry_set"]
        == {
            "credit_union": 4,
            "bank": 2,
            "financial_other": 1,
            "nonfinancial": 1,
            "unclassified": 1,
        }
        and member_by_key[("financial_other", "F000006", 2022)][
            "n_bills_sum"
        ]
        == 7
        and member_by_key[("nonfinancial", "C000003", 2022)][
            "n_bills_sum"
        ]
        == 2,
        coverage["rows_by_industry_set"],
    )
    review_by_lob_id = {
        row["lob_id"]: row for row in result["review_rows"]
    }
    ok(
        "industry review is finance-only and carries exact attention",
        review_by_lob_id["cuso-1"]["set"] == "credit_union"
        and review_by_lob_id["cuso-1"]["n_bills_2021_2022"] == 8
        and review_by_lob_id["fin-1"]["set"] == "financial_other"
        and "other-1" not in review_by_lob_id
        and {row["set"] for row in result["review_rows"]}
        == CL.NETWORK_REVIEW_SETS
        and result["review_rows"]
        == sorted(
            result["review_rows"],
            key=lambda row: (
                row["set"],
                -row["n_bills_2021_2022"],
                row["client_name"],
                row["lob_id"],
            ),
        ),
        review_by_lob_id["cuso-1"],
    )

    bad_path = os.path.join(fixture, "bad_network.csv")
    ok(
        "non-blank malformed bioguide still fails loudly",
        network_raises(
            bad_path, client_rows, [["cu-1", "bad", "2021", "1"]]
        ),
    )
    ok(
        "year 1999 fails loudly",
        network_raises(
            bad_path, client_rows, [["cu-1", "A000001", "1999", "1"]]
        ),
    )
    ok(
        "n_bills zero fails loudly",
        network_raises(
            bad_path, client_rows, [["cu-1", "A000001", "2021", "0"]]
        ),
    )
    ok(
        "truncated network row fails loudly",
        network_raises(
            bad_path, client_rows, [["cu-1", "A000001", "2021"]]
        ),
    )
    ok(
        "inventory versus processing row-count mismatch fails loudly",
        network_raises(
            bad_path,
            client_rows,
            [["cu-1", "A000001", "2021", "1"]],
            expected_rows=2,
        ),
    )
    ok(
        "header-only network fails loudly",
        network_raises(bad_path, client_rows, []),
    )
    ok(
        "all-unmatched lob_ids fail loudly",
        network_raises(
            bad_path,
            client_rows,
            [["absent-client", "A000001", "2021", "1"]],
        ),
    )

    duplicate_path = os.path.join(fixture, "duplicate_network.csv")
    duplicate_rows = [
        ["cu-1", "A000001", "2021", "1"],
        ["cu-1", "A000001", "2021", "2"],
    ]
    write_csv(duplicate_path, CL.NETWORK_INPUT_FIELDS, duplicate_rows)
    duplicate_result = CL._process_network(
        duplicate_path, client_rows, expected_rows=2
    )
    ok(
        "duplicate raw dyad increments receipt without dropping attention",
        duplicate_result["coverage"]["duplicate_dyad_rows"] == 1
        and duplicate_result["member_rows"][0]["n_bills_sum"] == 3,
        duplicate_result["coverage"],
    )
    ok(
        "skip-issues disables network processing but presence otherwise enables it",
        not CL._network_processing_enabled(True, network_path)
        and CL._network_processing_enabled(False, network_path)
        and not CL._network_processing_enabled(False, None),
    )

    # The end-to-end emission check moved to the Phase 0 content-freshness audit,
    # which asserts the builder's honesty strings are present in the deployed
    # lobby-network.html. This is a stronger check than a synthetic fixture because
    # the failure mode it guards against is a deployed surface carrying a newer mtime
    # than its builder while missing every feature the builder emits.

    template_path = os.path.join(
        MB_ROOT, "fec", "surfaces", "templates", "make_dashboard_html_00.tpl"
    )
    with open(template_path, "r", encoding="utf-8") as handle:
        template = handle.read()
    desk_anchor = '<a id=desklink class=desklink href="cu-member-desk.html">'
    industry_anchor = (
        '<a id=lobnetlink class=desklink href="lobby-network.html">'
    )
    desk_index = template.find(desk_anchor)
    industry_index = template.find(industry_anchor)
    header_end = template.find("</header>")
    desk_close = template.find("</a>", desk_index + len(desk_anchor))
    industry_close = template.find("</a>", industry_index + len(industry_anchor))
    industry_label = (
        template[industry_index + len(industry_anchor) : industry_close]
        if industry_index >= 0 and industry_close >= 0
        else ""
    )
    # The label is deliberately not pinned after "Lobbying network" -> "Industry";
    # its position, id, and href remain pinned.
    ok(
        "template lobbying-network anchor is inside header",
        desk_index >= 0
        and industry_index >= 0
        and header_end >= 0
        and desk_index < industry_index < header_end
        and desk_close + len("</a>") == industry_index
        and industry_close < header_end
        and bool(industry_label.strip()),
    )

    print(f"\nLOBBYVIEW NETWORK TESTS — {P} passed, {F} FAILED")
    return 1 if F else 0


if __name__ == "__main__":
    sys.exit(main())
