# -*- coding: utf-8 -*-
"""Focused, dependency-free tests for the staged direction ledger."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path


FEC = Path(__file__).resolve().parents[2]
ROOT = FEC.parent
sys.path.insert(0, str(FEC))

from legislation import build_legislation_direction_ledger as DIR  # noqa: E402
from legislation import collect_official_rollcall_events as ROLL  # noqa: E402


class raises:
    def __init__(self, exception: type[BaseException], match: str):
        self.exception = exception
        self.pattern = re.compile(match)

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        assert kind is not None, f"expected {self.exception.__name__}"
        assert issubclass(kind, self.exception), value
        assert self.pattern.search(str(value)), (
            f"{value!s} does not match {self.pattern.pattern!r}"
        )
        return True


def accepted_row(
    *,
    canonical_key: str,
    category: str,
    direction: int | None = None,
    cfpb_subtype: str | None = None,
    complete: bool = True,
) -> dict:
    congress_text, measure_type, number_text = canonical_key.split(":")
    row = {
        "canonical_key": canonical_key,
        "congress": int(congress_text),
        "measure_type": measure_type,
        "number": int(number_text),
        "category": category,
        "disposition": "included",
        "included_for_scoring": True,
    }
    if direction is not None:
        row["direction"] = direction
        row["cfpb_subtype"] = cfpb_subtype
        row["direction_provenance"] = {
            "source_url": (
                f"https://www.congress.gov/bill/{congress_text}th-congress/"
                f"{measure_type}/{number_text}"
            ),
            "source_reference": "official title and operative provisions",
            "rationale": (
                f"Official provisions establish the {category} industry effect."
                if complete
                else None
            ),
        }
    return row


def write_ndjson(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_ndjson(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def args_for(
    accepted: Path,
    out: Path,
    *,
    bill_directions: Path | None = None,
    archive: Path | None = None,
    press: Path | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        accepted=accepted,
        out=out,
        bill_directions=bill_directions,
        archive_stance_evidence=archive,
        press_cross_reference=press,
    )


def test_complete_directions_cfpb_semantics_collector_contract_and_idempotence():
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        accepted = temp / "accepted.ndjson"
        registry = temp / "directions.csv"
        press = temp / "press.ndjson"
        out = temp / "stage"
        write_ndjson(
            accepted,
            [
                accepted_row(
                    canonical_key="119:hr:1",
                    category="Payments",
                    direction=1,
                ),
                accepted_row(
                    canonical_key="119:hr:2",
                    category="CFPB",
                ),
            ],
        )
        with registry.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "Bill",
                    "Congress",
                    "Category",
                    "Direction",
                    "direction_source_url",
                    "direction_source_reference",
                    "direction_rationale",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "Bill": "H.R.2",
                    "Congress": "119",
                    "Category": "CFPB:Reduce",
                    "Direction": "1",
                    "direction_source_url": (
                        "https://www.congress.gov/bill/119th-congress/"
                        "house-bill/2"
                    ),
                    "direction_source_reference": "official provisions",
                    "direction_rationale": (
                        "The operative provisions reduce CFPB authority."
                    ),
                }
            )
        # This is positive corroboration only.  It has no direction and does
        # not become a guessed sign.
        write_ndjson(
            press,
            [
                {
                    "canonical_key": "119:hr:2",
                    "category": "CFPB",
                    "article_url": "https://example.test/article",
                    "title": "Industry article references H.R. 2",
                }
            ],
        )
        run_args = args_for(
            accepted,
            out,
            bill_directions=registry,
            press=press,
        )
        first = DIR.run(run_args)
        paths = [
            out / "direction_candidates.ndjson",
            out / "edge_direction_audit.ndjson",
            out / "scoring_directions.ndjson",
            out / "unmatched_direction_evidence.ndjson",
            out / "manifest.json",
        ]
        first_hashes = [digest(path) for path in paths]
        second = DIR.run(run_args)
        second_hashes = [digest(path) for path in paths]
        assert first_hashes == second_hashes
        assert first["counts"] == second["counts"]
        assert first["acceptance_passed"] is True

        scoring = read_ndjson(out / "scoring_directions.ndjson")
        assert len(scoring) == 2
        by_edge = {
            (row["canonical_key"], row["category"]): row for row in scoring
        }
        cfpb = by_edge[("119:hr:2", "CFPB")]
        assert cfpb["direction"] == 1
        assert cfpb["cfpb_subtype"] == "reduce_authority"
        assert cfpb["direction_meaning"] == "yea_is_industry_aligned"
        assert cfpb["direction_provenance"]["rationale"]

        candidates = read_ndjson(out / "direction_candidates.ndjson")
        press_candidate = next(
            row
            for row in candidates
            if row["source_kind"] == "trusted_press_cross_reference"
        )
        assert press_candidate["status"] == "corroboration_only"
        assert press_candidate["direction_sign"] is None

        # The emitted rows are directly consumable by the official roll-call
        # collector's accepted direction loader.
        measures, direction_audit = ROLL.load_accepted_measures(
            accepted, out / "scoring_directions.ndjson"
        )
        assert len(measures) == 2
        assert all(row["disposition"] == "included" for row in direction_audit)


def test_missing_conflict_and_weak_provenance_are_all_visible_and_blocking():
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        accepted = temp / "accepted.ndjson"
        archive = temp / "archive.json"
        weak = temp / "weak.csv"
        write_ndjson(
            accepted,
            [
                accepted_row(
                    canonical_key="119:hr:10",
                    category="Payments",
                    direction=1,
                ),
                accepted_row(
                    canonical_key="119:hr:11",
                    category="Interchange",
                ),
                accepted_row(
                    canonical_key="119:hr:12",
                    category="Fraud Prevention",
                ),
            ],
        )
        archive.write_text(
            json.dumps(
                {
                    "schema": "legislation-routing-audit-v1",
                    "rows": [
                {
                    "bill": "H.R.10",
                    "congress": 119,
                    "effective_category": "Payments",
                    "sign": -1,
                    "direction_source_url": (
                        "https://www.congress.gov/bill/119th-congress/"
                        "house-bill/10"
                    ),
                    "direction_source_reference": "archive official review",
                    "direction_rationale": (
                        "The archived official review says the opposite."
                    ),
                }
                    ],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        with weak.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["Bill", "Congress", "Category", "Direction", "Note"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "Bill": "H.R.11",
                    "Congress": "119",
                    "Category": "Interchange",
                    "Direction": "1",
                    "Note": "migrated from old registry",
                }
            )
        out = temp / "stage"
        manifest = DIR.run(
            args_for(
                accepted,
                out,
                bill_directions=weak,
                archive=archive,
            )
        )
        assert manifest["acceptance_passed"] is False
        audit = {
            row["canonical_key"]: row
            for row in read_ndjson(out / "edge_direction_audit.ndjson")
        }
        assert audit["119:hr:10"]["reason"] == "conflicting_category_directions"
        assert audit["119:hr:11"]["disposition"] == "weak_provenance"
        assert audit["119:hr:12"]["disposition"] == "missing"
        assert read_ndjson(out / "scoring_directions.ndjson") == []


def test_cfpb_expand_requires_negative_sign_and_preserves_subtype():
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        accepted = temp / "accepted.ndjson"
        bad = temp / "bad.ndjson"
        good = temp / "good.ndjson"
        write_ndjson(
            accepted,
            [accepted_row(canonical_key="119:s:9", category="CFPB")],
        )
        base = {
            "canonical_key": "119:s:9",
            "category": "CFPB:Expand",
            "direction_source_url": (
                "https://www.congress.gov/bill/119th-congress/senate-bill/9"
            ),
            "direction_source_reference": "official provisions",
            "direction_rationale": "The provisions expand CFPB authority.",
        }
        write_ndjson(bad, [{**base, "direction": 1}])
        bad_out = temp / "bad_stage"
        bad_manifest = DIR.run(
            args_for(accepted, bad_out, archive=bad)
        )
        assert bad_manifest["acceptance_passed"] is False
        bad_audit = read_ndjson(bad_out / "edge_direction_audit.ndjson")[0]
        assert bad_audit["reason"] == "invalid_direction_semantics"
        assert read_ndjson(bad_out / "scoring_directions.ndjson") == []

        write_ndjson(good, [{**base, "direction": -1}])
        good_out = temp / "good_stage"
        good_manifest = DIR.run(
            args_for(accepted, good_out, archive=good)
        )
        assert good_manifest["acceptance_passed"] is True
        decision = read_ndjson(good_out / "scoring_directions.ndjson")[0]
        assert decision["direction"] == -1
        assert decision["cfpb_subtype"] == "expand_authority"
        assert decision["direction_meaning"] == "nay_is_industry_aligned"


def test_type_aware_edges_do_not_collide_and_article_absence_is_not_negative():
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        accepted = temp / "accepted.ndjson"
        out = temp / "stage"
        write_ndjson(
            accepted,
            [
                accepted_row(
                    canonical_key="119:hr:1",
                    category="Payments",
                    direction=1,
                ),
                accepted_row(
                    canonical_key="119:hres:1",
                    category="Payments",
                    direction=-1,
                ),
            ],
        )
        manifest = DIR.run(args_for(accepted, out))
        assert manifest["acceptance_passed"] is True
        assert manifest["policy"]["article_absence_is_evidence"] is False
        rows = read_ndjson(out / "scoring_directions.ndjson")
        assert {
            (row["canonical_key"], row["direction"]) for row in rows
        } == {("119:hr:1", 1), ("119:hres:1", -1)}


def test_excluded_and_non_scoring_memberships_do_not_require_direction():
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        accepted = temp / "accepted.ndjson"
        out = temp / "stage"
        included = accepted_row(
            canonical_key="119:hr:1",
            category="Payments",
            direction=1,
        )
        included.update(
            {
                "membership_status": "included",
                "scoring_category": True,
            }
        )
        excluded = accepted_row(
            canonical_key="119:hr:2",
            category="Interchange",
        )
        excluded.update(
            {
                "membership_status": "excluded",
                "scoring_category": True,
            }
        )
        monitor = accepted_row(
            canonical_key="119:hr:3",
            category="Payments",
        )
        monitor.update(
            {
                "membership_status": "included",
                "scoring_category": False,
            }
        )
        write_ndjson(accepted, [included, excluded, monitor])
        manifest = DIR.run(args_for(accepted, out))
        assert manifest["acceptance_passed"] is True
        audit = read_ndjson(out / "edge_direction_audit.ndjson")
        assert [(row["canonical_key"], row["category"]) for row in audit] == [
            ("119:hr:1", "Payments")
        ]


def test_unmatched_passed_evidence_and_production_output_are_rejected():
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        accepted = temp / "accepted.ndjson"
        extra = temp / "extra.ndjson"
        out = temp / "stage"
        write_ndjson(
            accepted,
            [
                accepted_row(
                    canonical_key="119:hr:1",
                    category="Payments",
                    direction=1,
                )
            ],
        )
        write_ndjson(
            extra,
            [
                {
                    "canonical_key": "119:hres:1",
                    "category": "Payments",
                    "direction": 1,
                    "direction_source_reference": "official review",
                    "direction_rationale": "Explicit but not an accepted edge.",
                }
            ],
        )
        manifest = DIR.run(args_for(accepted, out, archive=extra))
        assert manifest["acceptance_passed"] is False
        unmatched = read_ndjson(out / "unmatched_direction_evidence.ndjson")
        assert len(unmatched) == 1
        assert (
            unmatched[0]["reason"]
            == "evidence_references_unaccepted_measure_category_edge"
        )
        with raises(DIR.DirectionError, "must resolve under"):
            DIR.validate_out(ROOT / "direction_stage")
        with raises(DIR.DirectionError, "must be a directory"):
            DIR.validate_out(temp / "data" / "legislation" / "bill_directions.csv")


if __name__ == "__main__":
    for name, value in sorted(globals().copy().items()):
        if name.startswith("test_") and callable(value):
            value()
            print(f"PASS {name}")
