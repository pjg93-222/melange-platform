from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest


FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))

from engine import responsiveness_layers as RL  # noqa: E402


def registry_row(
    vehicle="ASK",
    congress="117",
    status="active",
    scope=None,
    industry="credit_union",
    **extra,
):
    row = {
        "vehicle": vehicle,
        "congress": congress,
        "industry": industry,
        "ask_type": "bill",
        "scope": {"chamber": "House"} if scope is None else scope,
        "status": status,
    }
    row.update(extra)
    return row


def write_ndjson(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, separators=(",", ":")), encoding="utf-8"
    )


class RegistryLoaderTests(unittest.TestCase):
    def load(self, rows, tag_keys=None):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "registry.ndjson"
            write_ndjson(path, rows)
            return RL.load_registry(
                path, tag_keys or {("ASK", "117")}
            )

    def assert_row_error(self, rows, pattern, tag_keys=None):
        with self.assertRaisesRegex(ValueError, pattern):
            self.load(rows, tag_keys)

    def test_unknown_vehicle_vs_tags_guard_names_row(self):
        self.assert_row_error(
            [registry_row(vehicle="UNKNOWN")],
            r"registry row 1 \(UNKNOWN\|117\).*absent",
        )

    def test_duplicate_row_guard_names_second_row(self):
        self.assert_row_error(
            [registry_row(), registry_row()],
            r"registry row 2 \(ASK\|117\).*duplicate",
        )

    def test_bad_scope_key_guard_names_row(self):
        self.assert_row_error(
            [registry_row(scope={"district": "KY-06"})],
            r"registry row 1 \(ASK\|117\).*scope key",
        )

    def test_bad_status_guard_names_row(self):
        self.assert_row_error(
            [registry_row(status="pending")],
            r"registry row 1 \(ASK\|117\).*status",
        )

    def test_bad_chamber_and_party_values_guard_names_row(self):
        cases = [
            ({"chamber": "Assembly"}, "chamber"),
            ({"party": "Independent"}, "party"),
        ]
        for scope, field in cases:
            with self.subTest(field=field):
                self.assert_row_error(
                    [registry_row(scope=scope)],
                    r"registry row 1 \(ASK\|117\).*%s" % field,
                )

    def test_non_cu_industry_guard_names_row(self):
        self.assert_row_error(
            [registry_row(industry="bank")],
            r"registry row 1 \(ASK\|117\).*industry",
        )

    def test_registry_validation_precedes_other_input_loading(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_ndjson(
                root / "bill_beneficiary_tags.ndjson",
                [{"bill": "ASK", "congress": "117"}],
            )
            write_ndjson(
                root
                / "data"
                / "lobbying"
                / "industry_ask_registry.ndjson",
                [registry_row(vehicle="UNKNOWN")],
            )
            with self.assertRaisesRegex(
                ValueError, r"registry row 1 .*absent"
            ):
                RL.main(root, root / "unused.json")


class ServiceLoaderTests(unittest.TestCase):
    def test_voteview_service_is_bioguide_matched_and_congress_scoped(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "HSall_members.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["bioguide_id", "congress", "chamber"],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "bioguide_id": "BIO-A",
                            "congress": "117",
                            "chamber": "House",
                        },
                        {
                            "bioguide_id": "BIO-A",
                            "congress": "116",
                            "chamber": "House",
                        },
                        {
                            "bioguide_id": "BIO-B",
                            "congress": "118",
                            "chamber": "Assembly",
                        },
                    ]
                )
            service = RL.load_service(path)
            bundle = {
                "members": [
                    {
                        "name": "Alice",
                        "bio": {"bioguide": "BIO-A"},
                    },
                    {
                        "name": "Bob",
                        "bio": {"bioguide": "BIO-B"},
                    },
                ]
            }
            self.assertEqual(service, {"BIO-A": {117: "House"}})
            self.assertEqual(
                RL.derive_member_service(bundle, service),
                {"Alice": {"117": "House"}, "Bob": {}},
            )


class EligibilityAndAnsweredTests(unittest.TestCase):
    def setUp(self):
        self.bundle = {
            "members": [
                {
                    "name": "Alice",
                    "chamber": "House",
                    "party": "Democrat",
                    "bio": {"bioguide": "BIO-A"},
                    "bills": [
                        {
                            "bill": "ANY",
                            "congress": "117",
                            "role": "signer",
                        },
                        {
                            "bill": "INIT",
                            "congress": "117",
                            "role": "sponsor",
                        },
                        {
                            "bill": "UNSCORED",
                            "congress": "117",
                            "role": "cosponsor",
                        },
                        {
                            "bill": "INACTIVE",
                            "congress": "117",
                            "role": "original",
                        },
                    ],
                }
            ]
        }
        self.registry = [
            registry_row(vehicle="ANY"),
            registry_row(
                vehicle="INIT", initiator="Alice", ask_type="letter"
            ),
            registry_row(
                vehicle="UNSCORED",
                status="unscored",
                scope={"committee": "Ways and Means"},
            ),
            registry_row(vehicle="INACTIVE", status="inactive"),
            registry_row(vehicle="SENATE", scope={"chamber": "Senate"}),
            registry_row(vehicle="PARTY", scope={"party": "Republican"}),
            registry_row(vehicle="FUTURE", congress="118"),
        ]
        self.result = RL.compute_take_up(
            self.bundle,
            self.registry,
            {"Alice": {"117": "House"}},
        )[0]
        self.vehicles = {
            row["vehicle"]: row
            for row in self.result["take_up"]["vehicles"]
        }

    def test_unscored_is_excluded_from_denominator(self):
        self.assertEqual(self.result["take_up"]["eligible"], 2)
        self.assertEqual(self.result["take_up"]["unscored"], 1)
        self.assertFalse(self.vehicles["UNSCORED"]["eligible"])

    def test_active_committee_scope_is_always_unscored(self):
        result = RL.compute_take_up(
            self.bundle,
            [
                registry_row(
                    vehicle="COMMITTEE",
                    status="active",
                    scope={"committee": "Ways and Means"},
                )
            ],
            {"Alice": {"117": "House"}},
        )[0]["take_up"]
        self.assertEqual(result["eligible"], 0)
        self.assertEqual(result["answered"], 0)
        self.assertEqual(result["unscored"], 1)
        self.assertFalse(result["vehicles"][0]["eligible"])

    def test_inactive_is_fully_ignored(self):
        self.assertNotIn("INACTIVE", self.vehicles)
        self.assertEqual(self.result["take_up"]["unscored"], 1)

    def test_wrong_chamber_is_ineligible(self):
        self.assertFalse(self.vehicles["SENATE"]["eligible"])

    def test_party_scope_excludes_other_party(self):
        self.assertFalse(self.vehicles["PARTY"]["eligible"])

    def test_non_served_congress_is_ineligible(self):
        self.assertFalse(self.vehicles["FUTURE"]["eligible"])

    def test_chamber_switcher_uses_congress_time_chamber(self):
        bundle = {
            "members": [
                {
                    "name": "Switcher",
                    "chamber": "Senate",
                    "party": "Democrat",
                    "bills": [],
                }
            ]
        }
        result = RL.compute_take_up(
            bundle,
            [
                registry_row(
                    vehicle="HOUSE-117",
                    scope={"chamber": "House"},
                ),
                registry_row(
                    vehicle="SENATE-117",
                    scope={"chamber": "Senate"},
                ),
            ],
            {"Switcher": {"117": "House"}},
        )[0]
        vehicles = {
            row["vehicle"]: row
            for row in result["take_up"]["vehicles"]
        }
        self.assertEqual(result["chamber"], "Senate")
        self.assertTrue(vehicles["HOUSE-117"]["eligible"])
        self.assertFalse(vehicles["SENATE-117"]["eligible"])
        self.assertEqual(result["take_up"]["eligible"], 1)

    def test_any_role_answers_and_initiator_sponsor_counts(self):
        self.assertEqual(self.result["take_up"]["answered"], 2)
        self.assertTrue(self.vehicles["ANY"]["answered"])
        self.assertEqual(self.vehicles["ANY"]["role"], "signer")
        self.assertTrue(self.vehicles["INIT"]["answered"])
        self.assertEqual(self.vehicles["INIT"]["role"], "sponsor")


def capacity_fixture():
    names = ["M0", "M1", "M2", "M3", "M4", "M5"]
    chambers = ["House", "House", "House", "House", "House", "Senate"]
    parties = [
        "Democrat",
        "Democrat",
        "Democrat",
        "Democrat",
        "Democrat",
        "Republican",
    ]
    actuals = [2.0, 5.0, 10.0, 12.0, 13.0, 15.0]
    agendas = [0.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    committees = [
        {},
        {"on": "House Financial Services", "lead": 0, "mem": 0},
        {"on": "House Financial Services", "lead": 1, "mem": 0},
        {"on": "Senate Banking", "lead": 0, "mem": 1},
        {},
        {},
    ]
    bundle = {
        "members": [
            {
                "name": name,
                "chamber": chambers[index],
                "party": parties[index],
                "bio": {"bioguide": "BIO-%d" % index},
                "bills": (
                    [
                        {
                            "bill": "ASK",
                            "congress": "117",
                            "role": "signer",
                        }
                    ]
                    if index == 0
                    else []
                ),
            }
            for index, name in enumerate(names)
        ]
    }
    primacy = {
        "members": {
            name: {
                "mix": {"cu_primary": actuals[index]},
                "agenda_credit": agendas[index],
            }
            for index, name in enumerate(names)
        }
    }
    member_desk_src = {
        "members": [
            {"name": name, "committee": committees[index]}
            for index, name in enumerate(names)
        ]
    }
    return bundle, primacy, member_desk_src


def non_saturated_capacity_fixture():
    base_bundle, _, base_member_desk_src = capacity_fixture()
    predictions = [2.0, 5.0, 10.0, 12.0, 13.0, 15.0]
    agendas = [0.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    members = []
    primacy_members = {}
    source_members = []
    for index, prediction in enumerate(predictions):
        base_member = base_bundle["members"][index]
        base_source = base_member_desk_src["members"][index]
        for suffix, residual in (("a", -1.0), ("b", 1.0)):
            name = "P%d%s" % (index, suffix)
            members.append(
                {
                    "name": name,
                    "chamber": base_member["chamber"],
                    "party": base_member["party"],
                    "bio": {"bioguide": "BIO-%s" % name},
                    "bills": [],
                }
            )
            primacy_members[name] = {
                "mix": {"cu_primary": prediction + residual},
                "agenda_credit": agendas[index],
            }
            source_members.append(
                {
                    "name": name,
                    "committee": dict(base_source["committee"]),
                }
            )
    return (
        {"members": members},
        {"members": primacy_members},
        {"members": source_members},
    )


class PercentileTests(unittest.TestCase):
    def test_midrank_percentiles_cover_ties_and_two_member_edges(self):
        self.assertEqual(
            RL._midrank_percentiles([0.0, 2.0, 2.0, 5.0]),
            [0.0, 0.5, 0.5, 1.0],
        )
        self.assertEqual(
            RL._midrank_percentiles([7.0, 3.0]), [1.0, 0.0]
        )
        self.assertEqual(
            RL._midrank_percentiles([4.0, 4.0]), [0.5, 0.5]
        )


class CapacityTests(unittest.TestCase):
    def test_capacity_ols_exact_interpolation_has_hand_computed_betas(self):
        bundle, primacy, member_desk_src = capacity_fixture()
        capacity, betas = RL.compute_capacity(
            bundle, primacy, member_desk_src
        )

        # Hand calculation.  The six linear rows are
        #   [1,0,0,0,0,0] -> 2
        #   [1,1,0,0,0,0] -> 5
        #   [1,1,1,0,0,0] -> 10
        #   [1,1,0,1,0,0] -> 12
        #   [1,0,0,0,1,0] -> 13
        #   [1,0,0,0,0,1] -> 15.
        # Therefore b0=2; b_fin=5-2=3; b_lead=10-5=5;
        # b_mem=12-5=7; b_agenda=13-2=11; b_senate=15-2=13.
        # In the log model only row five changes: log1p(1)=ln(2), so
        # b_log_agenda * ln(2)=11 and b_log_agenda=11/ln(2).
        self.assertEqual(
            betas["linear"],
            {
                "intercept": 2.0,
                "fin_member": 3.0,
                "fin_lead_grade": 5.0,
                "fin_mem_grade": 7.0,
                "agenda_credit": 11.0,
                "senate": 13.0,
            },
        )
        self.assertEqual(betas["log"]["intercept"], 2.0)
        self.assertEqual(betas["log"]["fin_member"], 3.0)
        self.assertEqual(betas["log"]["fin_lead_grade"], 5.0)
        self.assertEqual(betas["log"]["fin_mem_grade"], 7.0)
        self.assertAlmostEqual(
            betas["log"]["log1p_agenda_credit"],
            11.0 / math.log(2.0),
            places=6,
        )
        self.assertEqual(betas["log"]["senate"], 13.0)
        for index, name in enumerate(
            ["M0", "M1", "M2", "M3", "M4", "M5"]
        ):
            self.assertEqual(
                capacity[name]["actual_cu_primary"],
                [2.0, 5.0, 10.0, 12.0, 13.0, 15.0][index],
            )
            self.assertEqual(capacity[name]["resid_linear"], 0.0)
            self.assertEqual(capacity[name]["resid_log"], 0.0)

    def test_capacity_ols_non_saturated_pairs_recover_hand_computed_betas(
        self,
    ):
        bundle, primacy, member_desk_src = non_saturated_capacity_fixture()
        capacity, betas = RL.compute_capacity(
            bundle, primacy, member_desk_src
        )

        # Non-saturated hand calculation: use the same six independent rows
        # x0..x5 documented in the exact-interpolation test, but repeat each
        # row twice with y = x*beta - 1 and y = x*beta + 1.  Thus n=12 and
        # k=6, with beta=(2,3,5,7,11,13).  Inside every repeated-x pair the
        # residuals sum to -1+1=0, so X'e=sum(x_i*(-1+1))=0.  Therefore
        # X'X*beta=X'y exactly; SSE=12 and residual df=12-6=6, proving this
        # is a genuine non-saturated fit rather than interpolation.  For the
        # log model, the fifth coordinate is ln(2), so its beta is 11/ln(2).
        self.assertEqual(
            betas["linear"],
            {
                "intercept": 2.0,
                "fin_member": 3.0,
                "fin_lead_grade": 5.0,
                "fin_mem_grade": 7.0,
                "agenda_credit": 11.0,
                "senate": 13.0,
            },
        )
        self.assertEqual(betas["log"]["intercept"], 2.0)
        self.assertEqual(betas["log"]["fin_member"], 3.0)
        self.assertEqual(betas["log"]["fin_lead_grade"], 5.0)
        self.assertEqual(betas["log"]["fin_mem_grade"], 7.0)
        self.assertAlmostEqual(
            betas["log"]["log1p_agenda_credit"],
            11.0 / math.log(2.0),
            places=6,
        )
        self.assertEqual(betas["log"]["senate"], 13.0)
        self.assertEqual(
            sorted(
                row["resid_linear"] for row in capacity.values()
            ),
            [-1.0] * 6 + [1.0] * 6,
        )


class ReceiptAndInGroupTests(unittest.TestCase):
    def test_receipts_disclose_unmatched_service_and_capacity_population(self):
        bundle, primacy, member_desk_src = capacity_fixture()
        document = RL.build_document(
            bundle,
            [registry_row()],
            {"BIO-0": {117: "House"}},
            primacy,
            member_desk_src,
            "fixture-digest",
        )
        receipts = document["receipts"]
        self.assertEqual(
            receipts["service_unmatched_members"],
            ["M1", "M2", "M3", "M4", "M5"],
        )
        self.assertEqual(receipts["service_unmatched_count"], 5)
        self.assertEqual(receipts["capacity_n"], 6)
        self.assertEqual(
            receipts["percentile_definition"],
            "midrank residual percentile over all capacity_n members "
            "(roster-wide)",
        )
        m1 = next(
            row for row in document["members"] if row["member"] == "M1"
        )
        self.assertIsNone(m1["take_up"]["rate"])

    def test_in_group_n_is_measurable_rate_denominator(self):
        bundle, primacy, member_desk_src = capacity_fixture()
        service = {
            member["bio"]["bioguide"]: {
                117: member["chamber"]
            }
            for member in bundle["members"]
            if member["name"] != "M2"
        }
        document = RL.build_document(
            bundle,
            [registry_row()],
            service,
            primacy,
            member_desk_src,
            "fixture-digest",
        )
        m1 = next(
            row for row in document["members"] if row["member"] == "M1"
        )
        self.assertEqual(m1["in_group"]["n"], 2)
        self.assertEqual(m1["in_group"]["mean_rate"], 0.0)
        self.assertEqual(
            m1["in_group"]["definition"], RL.IN_GROUP_DEFINITION
        )


class DeterminismTests(unittest.TestCase):
    def write_fixture_root(self, root):
        bundle, primacy, member_desk_src = capacity_fixture()
        write_json(root / "member_desk_bundle.json", bundle)
        write_ndjson(
            root / "bill_beneficiary_tags.ndjson",
            [{"bill": "ASK", "congress": "117"}],
        )
        write_ndjson(
            root / "data" / "lobbying" / "industry_ask_registry.ndjson",
            [registry_row()],
        )
        write_json(root / "data" / "engine" / "primacy_factors.json", primacy)
        write_json(
            root / "data" / "members" / "member_desk_src.json",
            member_desk_src,
        )
        service_path = root / "votes" / "HSall_members.csv"
        service_path.parent.mkdir(parents=True, exist_ok=True)
        with service_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["bioguide_id", "congress", "chamber"],
            )
            writer.writeheader()
            for index, member in enumerate(bundle["members"]):
                writer.writerow(
                    {
                        "bioguide_id": "BIO-%d" % index,
                        "congress": "117",
                        "chamber": member["chamber"],
                    }
                )

    def test_two_fixture_runs_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fixture_root(root)
            first = root / "first.json"
            second = root / "second.json"
            one = RL.main(root, first)
            two = RL.main(root, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(one, two)
            self.assertTrue(one["receipts"]["display_only"])
            self.assertEqual(
                one["receipts"]["service_source"], RL.SERVICE_SOURCE
            )
            payload = first.read_text(encoding="utf-8")
            self.assertNotIn("timestamp", payload)
            self.assertNotIn("generated_at", payload)


if __name__ == "__main__":
    unittest.main()
