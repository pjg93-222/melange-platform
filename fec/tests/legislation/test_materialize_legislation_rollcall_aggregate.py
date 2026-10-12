# -*- coding: utf-8 -*-
"""Focused stdlib tests for the staged official roll-call materializer."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from legislation import materialize_legislation_rollcall_aggregate as MAT  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_ndjson(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def read_ndjson(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def parts(measure: str) -> tuple[int, str, int]:
    congress, measure_type, number = measure.split(":")
    return int(congress), measure_type, int(number)


def measure_dict(measure: str) -> dict:
    congress, measure_type, number = parts(measure)
    return {
        "congress": congress,
        "measure_type": measure_type,
        "number": number,
        "canonical_key": measure,
    }


def event_row(
    event_id: str,
    measure: str,
    directions: dict[str, int],
    *,
    disposition: str = "included",
) -> dict:
    congress_text, chamber, session_text, roll_text = event_id.split(":")
    return {
        "schema": "official-rollcall-staging-v1",
        "event_id": event_id,
        "event_key": {
            "congress": int(congress_text),
            "chamber": chamber.title(),
            "session": int(session_text),
            "roll": int(roll_text),
            "event_id": event_id,
        },
        "measure_key": measure_dict(measure),
        "categories": sorted(directions),
        "category_directions": [
            {
                "category": category,
                "disposition": "included",
                "reason": "explicit_direction_with_provenance",
                "direction": {"sign": sign},
            }
            for category, sign in sorted(directions.items())
        ],
        "congress_action": {
            "date": "2026-01-01",
            "text": "Passed House by the Yeas and Nays.",
            "type": "Floor",
        },
        "official_vote_url": "https://clerk.house.gov/Votes/fixture",
        "disposition": disposition,
        "reason": (
            "official_final_disposition_exact_measure_match"
            if disposition == "included"
            else "official_vote_not_final_measure_disposition"
        ),
        "aggregation_ready": disposition == "included",
    }


def member_row(
    event_id: str,
    measure: str,
    directions: dict[str, int],
    *,
    bioguide: str | None,
    name: str,
    position: str,
    event_included: bool = True,
) -> dict:
    vote_value = (
        1
        if position.casefold() in {"yea", "yes", "aye"}
        else -1
        if position.casefold() in {"nay", "no"}
        else None
    )
    contributions = []
    if event_included and vote_value is not None and bioguide:
        for category, sign in sorted(directions.items()):
            contributions.append(
                {
                    "category": category,
                    "disposition": "included" if sign else "excluded",
                    "reason": (
                        "recorded_vote_with_explicit_direction"
                        if sign
                        else "explicit_neutral_direction"
                    ),
                    "aligned_value": vote_value * sign if sign else None,
                    "direction": {"sign": sign},
                }
            )
    if not event_included:
        disposition, reason = "excluded", "event_not_included"
    elif not bioguide:
        disposition, reason = "unmatched", "member_bioguide_not_uniquely_resolved"
    elif vote_value is None:
        disposition, reason = "excluded", "non_directional_vote_position"
    elif any(sign for sign in directions.values()):
        disposition, reason = (
            "included",
            "at_least_one_category_contribution_included",
        )
    else:
        disposition, reason = "excluded", "no_scored_category_contribution"
    return {
        "schema": "official-rollcall-staging-v1",
        "event_id": event_id,
        "measure_key": measure_dict(measure),
        "vote_row": 1,
        "member_name": name,
        "position": position,
        "bioguide_id": bioguide,
        "bioguide_resolution": {
            "status": "included" if bioguide else "unmatched",
            "bioguide_id": bioguide,
        },
        "category_contributions": contributions,
        "disposition": disposition,
        "reason": reason,
    }


class Fixture:
    def __init__(
        self,
        root: Path,
        *,
        direction_map: dict[str, dict[str, int]],
        events: list[dict],
        members: list[dict],
    ):
        self.root = root
        self.direction_map = direction_map
        self.events = events
        self.members = members
        self.accepted = root / "accepted"
        self.direction = root / "direction"
        self.rollcall = root / "rollcall"
        self._write()

    @staticmethod
    def receipt(path: Path, base: Path) -> dict:
        return {
            "path": str(path.relative_to(base)),
            "size": path.stat().st_size,
            "sha256": MAT.sha256_file(path),
        }

    def _write(self) -> None:
        accepted_rows = []
        for measure, categories in sorted(self.direction_map.items()):
            congress, measure_type, number = parts(measure)
            for category in sorted(categories):
                accepted_rows.append(
                    {
                        "schema": "accepted-legislation-relationship-v1",
                        "measure_key": measure,
                        "congress": congress,
                        "measure_type": measure_type,
                        "measure_number": number,
                        "category_label": category,
                        "membership_status": "included",
                        "scoring_category": True,
                        "measure_weight": MAT.measure_weight(measure),
                        "effective_weight": round(
                            MAT.DECAY[congress] * MAT.measure_weight(measure),
                            12,
                        ),
                    }
                )
        accepted_ledger = self.accepted / "accepted_legislation_relationships.ndjson"
        write_ndjson(accepted_ledger, accepted_rows)
        write_json(
            self.accepted / "manifest.json",
            {
                "schema": "accepted-legislation-generation-manifest-v1",
                "status": "complete",
                "generation_complete": True,
                "upstream_acceptance_passed": True,
                "staged_only": True,
                "production_artifacts_written": False,
                "measure_weight_policy": MAT.measure_weight_policy(),
                "canonical_ledger_file": accepted_ledger.name,
                "canonical_ledger_sha256": MAT.sha256_file(accepted_ledger),
            },
        )

        scoring_rows = []
        for measure, categories in sorted(self.direction_map.items()):
            congress, measure_type, number = parts(measure)
            for category, sign in sorted(categories.items()):
                scoring_rows.append(
                    {
                        "schema": "legislation-direction-decision-v1",
                        "canonical_key": measure,
                        "congress": congress,
                        "measure_type": measure_type,
                        "number": number,
                        "category": category,
                        "direction": sign,
                        "direction_sign": sign,
                        "direction_meaning": (
                            "yea_is_industry_aligned"
                            if sign == 1
                            else "nay_is_industry_aligned"
                            if sign == -1
                            else "neutral_unscored"
                        ),
                        "disposition": "included",
                    }
                )
        direction_paths = {}
        for name, rows in {
            "direction_candidates.ndjson": [],
            "edge_direction_audit.ndjson": [],
            "scoring_directions.ndjson": scoring_rows,
            "unmatched_direction_evidence.ndjson": [],
        }.items():
            path = self.direction / name
            write_ndjson(path, rows)
            direction_paths[name] = {
                "path": str(path.resolve()),
                "size": path.stat().st_size,
                "sha256": MAT.sha256_file(path),
            }
        write_json(
            self.direction / "manifest.json",
            {
                "schema": "legislation-direction-ledger-manifest-v1",
                "status": "PASS",
                "acceptance_passed": True,
                "production_write_prohibited": True,
                "inputs": {
                    "accepted": self.receipt(accepted_ledger, self.accepted)
                },
                "outputs": direction_paths,
            },
        )

        measure_rows = []
        for measure, categories in sorted(self.direction_map.items()):
            congress, measure_type, number = parts(measure)
            measure_events = [
                row
                for row in self.events
                if row["measure_key"]["canonical_key"] == measure
            ]
            dispositions = Counter(row["disposition"] for row in measure_events)
            measure_rows.append(
                {
                    "schema": "official-rollcall-staging-v1",
                    "canonical_key": measure,
                    "congress": congress,
                    "measure_type": measure_type,
                    "number": number,
                    "categories": sorted(categories),
                    "event_disposition_counts": dict(sorted(dispositions.items())),
                    "event_ids": sorted(row["event_id"] for row in measure_events),
                    "disposition": (
                        "included" if dispositions["included"] else "excluded"
                    ),
                    "reason": (
                        "official_final_rollcall_included"
                        if dispositions["included"]
                        else "no_official_final_passage_rollcall"
                    ),
                }
            )

        source_cells = defaultdict(lambda: [0, 0])
        for row in self.members:
            if row["disposition"] != "included":
                continue
            for contribution in row["category_contributions"]:
                if contribution["disposition"] != "included":
                    continue
                key = (row["bioguide_id"], contribution["category"])
                source_cells[key][0] += contribution["aligned_value"]
                source_cells[key][1] += 1
        source_aggregate = {
            "schema": "official-rollcall-staging-v1",
            "members": {},
            "cells": [
                {
                    "bioguide_id": key[0],
                    "category": key[1],
                    "aligned_sum": values[0],
                    "recorded_vote_count": values[1],
                    "lean": values[0] / values[1],
                }
                for key, values in sorted(source_cells.items())
            ],
        }
        rollcall_values = {
            "official_rollcall_event_ledger.ndjson": self.events,
            "rollcall_measure_match_audit.ndjson": measure_rows,
            "official_rollcall_member_votes.ndjson": self.members,
            "rollcall_direction_audit.ndjson": [],
            "source_snapshots.ndjson": [],
        }
        roll_outputs = {}
        for name, rows in rollcall_values.items():
            path = self.rollcall / name
            write_ndjson(path, rows)
            roll_outputs[name] = self.receipt(path, self.rollcall)
        aggregate_path = self.rollcall / "official_rollcall_aggregate.json"
        write_json(aggregate_path, source_aggregate)
        roll_outputs[aggregate_path.name] = self.receipt(
            aggregate_path, self.rollcall
        )
        write_json(
            self.rollcall / "manifest.json",
            {
                "schema": "official-rollcall-staging-v1",
                "staging_only": True,
                "production_vote_category_lean_written": False,
                "acceptance_passed": True,
                "blocking_issue_count": 0,
                "blocking_issues": [],
                "event_disposition_counts": dict(
                    sorted(Counter(row["disposition"] for row in self.events).items())
                ),
                "member_vote_disposition_counts": dict(
                    sorted(Counter(row["disposition"] for row in self.members).items())
                ),
                "inputs": [
                    self.receipt(accepted_ledger, self.accepted),
                    self.receipt(
                        self.direction / "scoring_directions.ndjson",
                        self.direction,
                    ),
                ],
                "outputs": roll_outputs,
            },
        )


def main_fixture(root: Path) -> Fixture:
    directions = {
        "119:hr:1": {"Payments": 1, "CU Tax Status": 0},
        "118:hres:1": {"Payments": -1},
        "117:s:2": {"CFPB": 1},
        "116:sjres:3": {"CFPB": 1},
    }
    events = [
        event_row("119:house:1:1", "119:hr:1", directions["119:hr:1"]),
        event_row("118:house:1:2", "118:hres:1", directions["118:hres:1"]),
        event_row("117:senate:1:3", "117:s:2", directions["117:s:2"]),
        event_row("116:senate:1:4", "116:sjres:3", directions["116:sjres:3"]),
        event_row(
            "119:house:1:99",
            "119:hr:1",
            directions["119:hr:1"],
            disposition="excluded",
        ),
    ]
    members = [
        member_row(
            "119:house:1:1",
            "119:hr:1",
            directions["119:hr:1"],
            bioguide="A000001",
            name="Alex Member",
            position="Yea",
        ),
        # Exact duplicate event/member vote: visible but never double-counted.
        member_row(
            "119:house:1:1",
            "119:hr:1",
            directions["119:hr:1"],
            bioguide="A000001",
            name="Alex Member",
            position="Yea",
        ),
        member_row(
            "118:house:1:2",
            "118:hres:1",
            directions["118:hres:1"],
            bioguide="A000001",
            name="Alex Member",
            position="Yea",
        ),
        member_row(
            "117:senate:1:3",
            "117:s:2",
            directions["117:s:2"],
            bioguide="A000001",
            name="Member, Alex",
            position="Nay",
        ),
        member_row(
            "116:senate:1:4",
            "116:sjres:3",
            directions["116:sjres:3"],
            bioguide="A000001",
            name="Alex Member",
            position="Yea",
        ),
        member_row(
            "119:house:1:1",
            "119:hr:1",
            directions["119:hr:1"],
            bioguide="B000002",
            name="Blair Present",
            position="Present",
        ),
        member_row(
            "119:house:1:99",
            "119:hr:1",
            directions["119:hr:1"],
            bioguide="C000003",
            name="Casey Excluded",
            position="Yea",
            event_included=False,
        ),
        member_row(
            "119:house:1:99",
            "119:hr:1",
            directions["119:hr:1"],
            bioguide=None,
            name="Unresolved Excluded Member",
            position="Yea",
            event_included=False,
        ),
    ]
    return Fixture(
        root, direction_map=directions, events=events, members=members
    )


class MaterializerTests(unittest.TestCase):
    def test_legacy_semantics_type_aware_dedup_and_decay_diagnostic(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fixture = main_fixture(root)
            out = root / "audit-output"
            manifest = MAT.materialize(
                rollcall=fixture.rollcall,
                accepted_generation=fixture.accepted,
                direction=fixture.direction,
                out=out,
            )

            self.assertEqual("PASS", manifest["status"])
            legacy = json.loads(
                (out / "vote_category_lean.json").read_text(encoding="utf-8")
            )
            # With no amendments in this fixture, measure-weighted lean equals
            # the historical signed mean while n remains a raw vote count.
            self.assertEqual(
                {"lean": 0.0, "n": 2},
                legacy["votes"]["A000001"]["Payments"],
            )
            self.assertEqual(
                {"lean": 0.0, "n": 2},
                legacy["votes"]["A000001"]["CFPB"],
            )
            self.assertNotIn("CU Tax Status", legacy["votes"]["A000001"])
            self.assertNotIn("B000002", legacy["votes"])

            cells = {
                (row["bioguide_id"], row["category"]): row
                for row in read_ndjson(
                    out / "official_rollcall_category_cells.ndjson"
                )
            }
            payment = cells[("A000001", "Payments")]
            self.assertEqual(
                ["118:hres:1", "119:hr:1"], payment["measure_keys"]
            )
            self.assertEqual(0.0, payment["lean"])
            self.assertAlmostEqual(
                (1.0 - 0.66) / (1.0 + 0.66),
                payment["decayed_lean_diagnostic"],
                places=11,
            )
            cfpb = cells[("A000001", "CFPB")]
            self.assertEqual(0.0, cfpb["lean"])
            self.assertAlmostEqual(
                (-0.33 + 0.16) / (0.33 + 0.16),
                cfpb["decayed_lean_diagnostic"],
                places=11,
            )
            self.assertEqual(
                1,
                manifest["counts"][
                    "exact_member_event_vote_duplicates_excluded"
                ],
            )
            self.assertEqual(
                2,
                manifest["counts"][
                    "abstain_or_missing_contributions_excluded"
                ],
            )
            self.assertGreater(
                manifest["counts"]["excluded_event_contributions_preserved"],
                0,
            )
            self.assertEqual(
                1,
                manifest["counts"][
                    "unmatched_or_ambiguous_identity_rows_preserved"
                ],
            )
            self.assertEqual(
                1, manifest["counts"]["member_name_variant_conflicts"]
            )
            self.assertTrue(
                manifest["decay_policy"][
                    "diagnostic_is_not_consumed_downstream"
                ]
            )
            compatibility = json.loads(
                (out / "rollcall_consumer_compatibility.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn(
                "contains only 17 categories and omits CU Tax Status",
                compatibility["incompatibilities"]["vote_by_category.py"],
            )

    def test_amendment_vote_is_half_weighted_in_lean_and_decay(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            directions = {
                "119:hr:1": {"Payments": 1},
                "119:hamdt:2": {"Payments": 1},
            }
            events = [
                event_row(
                    "119:house:1:1", "119:hr:1", directions["119:hr:1"]
                ),
                event_row(
                    "119:house:1:2",
                    "119:hamdt:2",
                    directions["119:hamdt:2"],
                ),
            ]
            members = [
                member_row(
                    "119:house:1:1",
                    "119:hr:1",
                    directions["119:hr:1"],
                    bioguide="A000001",
                    name="Alex Member",
                    position="Yea",
                ),
                member_row(
                    "119:house:1:2",
                    "119:hamdt:2",
                    directions["119:hamdt:2"],
                    bioguide="A000001",
                    name="Alex Member",
                    position="Nay",
                ),
            ]
            fixture = Fixture(
                root,
                direction_map=directions,
                events=events,
                members=members,
            )
            out = root / "audit-output"
            manifest = MAT.materialize(
                rollcall=fixture.rollcall,
                accepted_generation=fixture.accepted,
                direction=fixture.direction,
                out=out,
            )
            self.assertEqual("PASS", manifest["status"])
            cells = read_ndjson(
                out / "official_rollcall_category_cells.ndjson"
            )
            self.assertEqual(1, len(cells))
            cell = cells[0]
            self.assertEqual(2, cell["n"])
            self.assertEqual(1.5, cell["measure_weight_sum"])
            self.assertEqual(0.5, cell["measure_weighted_aligned_sum"])
            self.assertAlmostEqual(1.0 / 3.0, cell["lean"], places=11)
            self.assertAlmostEqual(
                1.0 / 3.0, cell["decayed_lean_diagnostic"], places=11
            )
            contributions = {
                row["measure_key"]: row
                for row in read_ndjson(
                    out / "official_rollcall_category_contributions.ndjson"
                )
            }
            self.assertEqual(1.0, contributions["119:hr:1"]["measure_weight"])
            self.assertEqual(
                0.5, contributions["119:hamdt:2"]["measure_weight"]
            )

    def test_measure_weight_policy_and_rows_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fixture = main_fixture(root)
            manifest_path = fixture.accepted / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.pop("measure_weight_policy")
            write_json(manifest_path, manifest)
            with self.assertRaisesRegex(
                MAT.MaterializerError, "measure-weight policy"
            ):
                MAT.materialize(
                    rollcall=fixture.rollcall,
                    accepted_generation=fixture.accepted,
                    direction=fixture.direction,
                    out=root / "audit-output",
                )

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fixture = main_fixture(root)
            ledger = (
                fixture.accepted / "accepted_legislation_relationships.ndjson"
            )
            rows = read_ndjson(ledger)
            rows[0]["measure_weight"] = 0.5
            write_ndjson(ledger, rows)
            manifest_path = fixture.accepted / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["canonical_ledger_sha256"] = MAT.sha256_file(ledger)
            write_json(manifest_path, manifest)
            # Rebind the direction and roll-call input receipts so the test
            # reaches row-level validation instead of failing at hash binding.
            for producer in (fixture.direction, fixture.rollcall):
                producer_manifest_path = producer / "manifest.json"
                producer_manifest = json.loads(
                    producer_manifest_path.read_text(encoding="utf-8")
                )
                bindings = (
                    producer_manifest["inputs"]
                    if isinstance(producer_manifest["inputs"], list)
                    else [producer_manifest["inputs"]["accepted"]]
                )
                for binding in bindings:
                    if str(binding.get("path") or "").endswith(ledger.name):
                        binding["size"] = ledger.stat().st_size
                        binding["sha256"] = MAT.sha256_file(ledger)
                write_json(producer_manifest_path, producer_manifest)
            with self.assertRaisesRegex(
                MAT.MaterializerError, "measure_weight"
            ):
                MAT.materialize(
                    rollcall=fixture.rollcall,
                    accepted_generation=fixture.accepted,
                    direction=fixture.direction,
                    out=root / "audit-output",
                )

    def test_conflicting_duplicate_member_vote_is_visible_and_blocks(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            directions = {"119:hr:1": {"Payments": 1}}
            event = event_row(
                "119:house:1:1", "119:hr:1", directions["119:hr:1"]
            )
            members = [
                member_row(
                    event["event_id"],
                    "119:hr:1",
                    directions["119:hr:1"],
                    bioguide="A000001",
                    name="Alex Member",
                    position=position,
                )
                for position in ("Yea", "Nay")
            ]
            fixture = Fixture(
                root,
                direction_map=directions,
                events=[event],
                members=members,
            )
            out = root / "audit-output"
            manifest = MAT.materialize(
                rollcall=fixture.rollcall,
                accepted_generation=fixture.accepted,
                direction=fixture.direction,
                out=out,
            )

            self.assertEqual("FAIL", manifest["status"])
            self.assertFalse(manifest["acceptance_passed"])
            self.assertTrue(
                any(
                    issue.startswith("conflicting_duplicate_member_vote:")
                    for issue in manifest["blocking_issues"]
                )
            )
            rows = read_ndjson(
                out / "official_rollcall_category_contributions.ndjson"
            )
            self.assertEqual({"ambiguous"}, {row["disposition"] for row in rows})
            legacy = json.loads(
                (out / "vote_category_lean.json").read_text(encoding="utf-8")
            )
            self.assertEqual({}, legacy["votes"])

    def test_hash_binding_and_output_receipts_fail_before_output(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fixture = main_fixture(root)
            manifest_path = fixture.rollcall / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["inputs"] = manifest["inputs"][:1]
            write_json(manifest_path, manifest)
            out = root / "audit-output"
            with self.assertRaisesRegex(
                MAT.MaterializerError, "scoring directions"
            ):
                MAT.materialize(
                    rollcall=fixture.rollcall,
                    accepted_generation=fixture.accepted,
                    direction=fixture.direction,
                    out=out,
                )
            self.assertFalse(out.exists())

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fixture = main_fixture(root)
            with (
                fixture.rollcall / "official_rollcall_member_votes.ndjson"
            ).open("a", encoding="utf-8") as handle:
                handle.write("{}\n")
            out = root / "audit-output"
            with self.assertRaisesRegex(MAT.MaterializerError, "hash mismatch"):
                MAT.materialize(
                    rollcall=fixture.rollcall,
                    accepted_generation=fixture.accepted,
                    direction=fixture.direction,
                    out=out,
                )
            self.assertFalse(out.exists())

    def test_idempotence_and_deterministic_generation(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fixture = main_fixture(root)
            out_one = root / "audit-one"
            out_two = root / "audit-two"
            first = MAT.materialize(
                rollcall=fixture.rollcall,
                accepted_generation=fixture.accepted,
                direction=fixture.direction,
                out=out_one,
            )
            first_manifest_bytes = (out_one / MAT.MANIFEST_NAME).read_bytes()
            second = MAT.materialize(
                rollcall=fixture.rollcall,
                accepted_generation=fixture.accepted,
                direction=fixture.direction,
                out=out_one,
            )
            third = MAT.materialize(
                rollcall=fixture.rollcall,
                accepted_generation=fixture.accepted,
                direction=fixture.direction,
                out=out_two,
            )
            self.assertFalse(first["_idempotent_reuse"])
            self.assertTrue(second["_idempotent_reuse"])
            self.assertEqual(
                first_manifest_bytes, (out_one / MAT.MANIFEST_NAME).read_bytes()
            )
            self.assertEqual(
                first_manifest_bytes, (out_two / MAT.MANIFEST_NAME).read_bytes()
            )
            self.assertEqual(first["generation_id"], third["generation_id"])
            for name in (*MAT.OUTPUT_NAMES, MAT.MANIFEST_NAME):
                self.assertEqual(
                    (out_one / name).read_bytes(), (out_two / name).read_bytes()
                )

    def test_isolated_copies_override_original_absolute_receipt_paths(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fixture = main_fixture(root / "producer")
            isolated = root / "isolated" / "inputs"
            for name, source in {
                "accepted_generation": fixture.accepted,
                "direction": fixture.direction,
                "rollcall": fixture.rollcall,
            }.items():
                shutil.copytree(source, isolated / name)

            # The copied direction manifest still carries the producer's
            # original absolute receipt. Corrupting that original must not
            # cause the isolated materializer to escape its copied inputs.
            (fixture.direction / "scoring_directions.ndjson").write_text(
                "{}\n", encoding="utf-8"
            )
            out = root / "isolated" / "audit-output"
            manifest = MAT.materialize(
                rollcall=isolated / "rollcall",
                accepted_generation=isolated / "accepted_generation",
                direction=isolated / "direction",
                out=out,
            )
            self.assertEqual("PASS", manifest["status"])
            self.assertEqual(
                MAT.sha256_file(
                    isolated / "direction" / "scoring_directions.ndjson"
                ),
                manifest["inputs"]["direction_outputs"][
                    "scoring_directions.ndjson"
                ]["sha256"],
            )

    def test_production_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = main_fixture(Path(raw))
            unsafe = MAT.CODE_ROOT / "not-a-staged-rollcall-output"
            with self.assertRaisesRegex(
                MAT.MaterializerError, "audits/legfix5"
            ):
                MAT.materialize(
                    rollcall=fixture.rollcall,
                    accepted_generation=fixture.accepted,
                    direction=fixture.direction,
                    out=unsafe,
                )
            self.assertFalse(unsafe.exists())


if __name__ == "__main__":
    unittest.main()
