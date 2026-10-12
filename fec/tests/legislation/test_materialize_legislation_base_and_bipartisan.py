# -*- coding: utf-8 -*-
"""Focused tests for staged base-network/bipartisan materialization."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile

from openpyxl import Workbook


FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from legislation import materialize_legislation_base_and_bipartisan as BB  # noqa: E402


def party_rows(roster: dict[str, str], parties: dict[str, str | None]):
    return [
        {
            "schema": BB.SCHEMA,
            "bioguide_id": bioguide,
            "member_name": name,
            "selected_party": parties[bioguide],
            "selected_congress": 119,
            "party_status": "selected",
            "party_history": [],
            "network_evidence_eligible": parties[bioguide] in {"D", "R"},
        }
        for bioguide, name in roster.items()
    ]


def test_exact_weighted_bipartisan_formula():
    roster = {
        "A000001": "Member A",
        "B000002": "Member B",
        "C000003": "Member C",
    }
    parties = {"A000001": "D", "B000002": "R", "C000003": "D"}
    edges = [
        {
            "source": "A000001",
            "target": "B000002",
            "weight": 2.0,
            "source_row": 2,
            "from_label": "Member A",
            "to_label": "Member B",
        },
        {
            "source": "A000001",
            "target": "C000003",
            "weight": 1.0,
            "source_row": 3,
            "from_label": "Member A",
            "to_label": "Member C",
        },
    ]
    by_name, by_bio, audit, exclusions, summary, blockers = (
        BB.compute_bipartisan(
            edges=edges,
            centrality=roster,
            roster=roster,
            party=parties,
            party_audit=party_rows(roster, parties),
        )
    )
    assert not blockers
    assert not exclusions
    assert summary["global_same_party_weight"] == 2.0
    assert summary["global_cross_party_weight"] == 4.0
    assert summary["global_cross_party_prior"] == round(4 / 6, 12)
    assert by_bio == {"A000001": 67, "B000002": 75, "C000003": 57}
    assert by_name["Member A"] == 67
    assert len(audit) == 3


def test_reverse_directed_edges_are_mean_symmetrized():
    roster = {"A000001": "Member A", "B000002": "Member B"}
    parties = {"A000001": "D", "B000002": "R"}
    edges = [
        {
            "source": "A000001",
            "target": "B000002",
            "weight": 2.0,
            "source_row": 2,
            "from_label": "Member A",
            "to_label": "Member B",
        },
        {
            "source": "B000002",
            "target": "A000001",
            "weight": 4.0,
            "source_row": 3,
            "from_label": "Member B",
            "to_label": "Member A",
        },
    ]
    _, _, audit, exclusions, summary, blockers = BB.compute_bipartisan(
        edges=edges,
        centrality=roster,
        roster=roster,
        party=parties,
        party_audit=party_rows(roster, parties),
    )
    assert not blockers
    assert not exclusions
    assert summary["major_party_eligible_pairs"] == 1
    assert all(row["cross_party_weight"] == 3.0 for row in audit)


def test_unknown_party_edge_is_visible_and_zero_weighted():
    roster = {"A000001": "Member A", "B000002": "Member B"}
    parties = {"A000001": "D", "B000002": None}
    edges = [
        {
            "source": "A000001",
            "target": "B000002",
            "weight": 2.0,
            "source_row": 2,
            "from_label": "Member A",
            "to_label": "Member B",
        }
    ]
    _, by_bio, _, exclusions, summary, blockers = BB.compute_bipartisan(
        edges=edges,
        centrality=roster,
        roster=roster,
        party=parties,
        party_audit=party_rows(roster, parties),
    )
    assert not blockers
    assert summary["major_party_eligible_pairs"] == 0
    assert summary["fallback_prior_used"] is True
    assert by_bio == {"A000001": 12, "B000002": 12}
    assert exclusions[0]["reason"] == "nonmajor_or_unknown_party_endpoint"


def test_duplicate_directed_workbook_edge_fails_closed():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "base.xlsx"
        workbook = Workbook()
        edges = workbook.active
        edges.title = "Edges"
        edges.append(BB.EDGE_HEADERS)
        row = [
            "A000001",
            "B000002",
            1.0,
            1,
            0,
            1,
            "Member A",
            "Member B",
        ]
        edges.append(row)
        edges.append(row)
        centrality = workbook.create_sheet("Centrality")
        centrality.append(BB.CENTRALITY_HEADERS)
        centrality.append(
            ["A000001", "Member A", 1, 1, 0, 1, 0, 0, 0, 0]
        )
        centrality.append(
            ["B000002", "Member B", 1, 0, 1, 0, 1, 0, 0, 0]
        )
        workbook.save(path)
        try:
            BB.read_base_workbook(path)
        except BB.AdapterError as exc:
            assert "duplicate directed base edge" in str(exc)
        else:
            raise AssertionError("duplicate directed edge unexpectedly passed")


def test_idempotent_install_reuses_only_byte_identical_generation():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        temp = root / "temp"
        out = root / "out"
        temp.mkdir()
        (temp / "one.json").write_text('{"a":1}\n', encoding="utf-8")
        assert BB.install_idempotently(temp, out, ["one.json"]) is False
        shutil.copyfile(out / "one.json", temp / "one.json")
        assert BB.install_idempotently(temp, out, ["one.json"]) is True
        (temp / "one.json").write_text('{"a":2}\n', encoding="utf-8")
        try:
            BB.install_idempotently(temp, out, ["one.json"])
        except BB.AdapterError as exc:
            assert "immutable staged output differs" in str(exc)
        else:
            raise AssertionError("different staged output unexpectedly reused")


if __name__ == "__main__":
    for name, value in sorted(globals().copy().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"PASS {name}")
