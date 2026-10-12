"""snapshot_registry against contracts/snapshot_registry.json.

The producer under test is snapshot_registry.registry(), driven by synthetic
contribution_data._sources() items, because the real reader needs a published
workbook with a pinned payload and manifest. The synthetic items carry the
contract's own synthetic hashes -- sha256("example:<industry>:<field>") -- so
the entries this suite compares are the contract's entries, not a paraphrase.

Only workbook/companion `bytes` and `modified_at` are excluded from the
comparison: those come from the fixture files on disk, not from _sources().
"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / "app"
CONTRACTS = Path(__file__).resolve().parents[2] / "audits" / "mentat_failings_20260915" / "contracts"


def load_contract(name):
    return json.loads((CONTRACTS / f"{name}.json").read_text(encoding="utf-8"))


# One module object for both, so patching contribution_data._sources here is
# the same object snapshot_registry reads through.
sys.path.insert(0, str(APP))
import contribution_data
import snapshot_registry

CONTRACT = load_contract("snapshot_registry")
EXAMPLE = CONTRACT["examples"][0]
FLOATING = (("workbook", "bytes"), ("workbook", "modified_at"),
            ("companion", "bytes"), ("companion", "modified_at"))


def synthetic(industry, field):
    """The contract's own synthetic hash for one field of one industry."""
    return hashlib.sha256(f"example:{industry}:{field}".encode()).hexdigest()


def comparable(entry):
    """A copy without the two fixture-dependent fields on each workbook."""
    value = json.loads(json.dumps(entry))
    for owner, key in FLOATING:
        if isinstance(value.get(owner), dict):
            value[owner].pop(key, None)
    return value


class SnapshotRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.reports = Path(cls.temp.name)
        cls.sources = []
        for industry in ("Fintech", "Credit Union", "Telecom", "Banking"):
            workbook = cls.reports / (industry + " Industry Spending Overview.xlsx")
            workbook.write_bytes(b"overview " + industry.encode())
            item = {"id": industry, "industry": industry, "label": industry,
                    "workbook": workbook, "workbook_sha256": synthetic(industry, "workbook")}
            if industry == "Telecom":
                # The shape _sources() now produces for a workbook it cannot open:
                # no hash at all, so no build stamp either (contract example 4).
                item.update(workbook_sha256=None, available=False,
                            message="Report workbook is unreadable.")
            elif industry == "Fintech":
                # Unavailable snapshots stay listed with their own message.
                item.update(available=False, message="Pinned report source evidence is unavailable.")
            else:
                cache = (".cache/receipt-metadata-repair-20260910/credit_union" if industry == "Credit Union"
                         else ".cache/industry-report/banking")
                item.update(available=True,
                            database=snapshot_registry.PROJECT / cache / "mentat.db",
                            database_sha256=synthetic(industry, "database"),
                            original_database_sha256=synthetic(industry, "database"),
                            payload_sha256=synthetic(industry, "payload"),
                            destination_schema=1,
                            hydration_manifest=(snapshot_registry.PROJECT / cache / "mentat.db.hydration.json"
                                                if industry == "Credit Union" else None))
            if industry == "Credit Union":
                companion = cls.reports / (industry + " Industry Spending Data.xlsx")
                companion.write_bytes(b"companion " + industry.encode())
                item.update(data_workbook=companion,
                            data_workbook_sha256=synthetic(industry, "companion"),
                            data_lineage_matches=True)
            cls.sources.append(item)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def registry(self):
        with patch.object(contribution_data, "_sources", return_value=self.sources):
            return snapshot_registry.registry(self.reports)

    def test_the_registry_validates_itself_and_lists_snapshots_sorted_by_label(self):
        value = self.registry()
        self.assertEqual(value["schema_version"], snapshot_registry.SCHEMA_VERSION)
        self.assertEqual(value["report_directory"], str(self.reports))
        self.assertRegex(value["generated_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        labels = [item["industry"] for item in value["snapshots"]]
        self.assertEqual(labels, ["Banking", "Credit Union", "Fintech", "Telecom"])
        self.assertEqual(labels, sorted(labels))
        # The producer validates its own output; validate() must agree.
        self.assertIs(snapshot_registry.validate(value), value)

    def test_every_contract_example_entry_is_what_the_producer_emits(self):
        produced = {item["industry"]: item for item in self.registry()["snapshots"]}
        self.assertEqual(sorted(produced), sorted(item["industry"] for item in EXAMPLE["snapshots"]))
        for example in EXAMPLE["snapshots"]:
            with self.subTest(industry=example["industry"]):
                self.assertEqual(comparable(produced[example["industry"]]), comparable(example))

    def test_the_fixture_files_supply_the_two_fields_the_comparison_excludes(self):
        # The excluded fields are excluded because they are measured, not because
        # they are absent: assert the producer really emits them.
        for item in self.registry()["snapshots"]:
            with self.subTest(industry=item["industry"]):
                self.assertGreater(item["workbook"]["bytes"], 0)
                self.assertRegex(item["workbook"]["modified_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        companion = next(item for item in self.registry()["snapshots"] if item["companion"])
        self.assertGreater(companion["companion"]["bytes"], 0)
        self.assertRegex(companion["companion"]["modified_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_the_build_stamp_recomputes_from_the_four_source_hashes(self):
        for item in self.registry()["snapshots"]:
            industry = item["industry"]
            with self.subTest(industry=industry):
                if item["workbook"]["sha256"] is None:
                    # No hash means no identity: none is invented (contract v2).
                    self.assertIsNone(item["build_stamp"])
                    self.assertIs(item["available"], False)
                    continue
                recomputed = snapshot_registry.build_stamp(
                    synthetic(industry, "workbook"),
                    synthetic(industry, "companion") if item["companion"] else None,
                    synthetic(industry, "database") if item["database"] else None,
                    synthetic(industry, "payload") if item["payload_sha256"] else None)
                self.assertEqual(item["build_stamp"], recomputed)
                self.assertRegex(item["build_stamp"], r"^[0-9a-f]{64}$")

    def test_the_build_stamp_changes_when_any_one_input_changes(self):
        base = ["a" * 64, "b" * 64, "c" * 64, "d" * 64]
        stamp = snapshot_registry.build_stamp(*base)
        seen = {stamp}
        for index in range(4):
            changed = list(base)
            changed[index] = "e" * 64
            seen.add(snapshot_registry.build_stamp(*changed))
        self.assertEqual(len(seen), 5)
        # An absent companion and an empty one are the same absence, by design.
        self.assertEqual(snapshot_registry.build_stamp("a" * 64, None, None, None),
                         snapshot_registry.build_stamp("a" * 64, "", "", ""))

    def test_no_entry_carries_an_absolute_path(self):
        for item in self.registry()["snapshots"]:
            with self.subTest(industry=item["industry"]):
                flat = json.dumps(item)
                self.assertNotIn(str(self.reports), flat)
                self.assertNotIn(str(snapshot_registry.PROJECT), flat)
                if item["database"]:
                    self.assertFalse(Path(item["database"]["path"]).is_absolute())
                    self.assertNotIn(":", item["database"]["path"])

    def test_hydration_and_companion_are_reported_not_inferred(self):
        produced = {item["industry"]: item for item in self.registry()["snapshots"]}
        self.assertIs(produced["Credit Union"]["database"]["hydrated"], True)
        self.assertIs(produced["Banking"]["database"]["hydrated"], False)
        self.assertIsNone(produced["Banking"]["companion"])
        self.assertIs(produced["Credit Union"]["companion"]["lineage_matches"], True)
        self.assertIs(produced["Fintech"]["available"], False)
        self.assertEqual(produced["Fintech"]["message"], "Pinned report source evidence is unavailable.")
        self.assertIsNone(produced["Fintech"]["database"])
        self.assertIsNone(produced["Fintech"]["payload_sha256"])
        self.assertIsNone(produced["Fintech"]["destination_schema"])
        # Two different unavailabilities, told apart by their own messages:
        # Fintech was read and has no pinned evidence; Telecom could not be read
        # at all, so it has neither a workbook hash nor a build stamp.
        self.assertEqual(produced["Telecom"]["message"], "Report workbook is unreadable.")
        self.assertRegex(produced["Fintech"]["workbook"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertIsNone(produced["Telecom"]["workbook"]["sha256"])
        self.assertIsNone(produced["Telecom"]["build_stamp"])
        self.assertRegex(produced["Fintech"]["build_stamp"], r"^[0-9a-f]{64}$")

    def test_find_matches_a_label_or_its_slug_and_nothing_else(self):
        value = self.registry()
        for wanted in ("Banking", "banking", "Credit Union", "credit_union", "Fintech", "fintech",
                       "Telecom", "telecom"):
            with self.subTest(value=wanted):
                self.assertIsNotNone(snapshot_registry.find(value, wanted))
        self.assertEqual(snapshot_registry.find(value, "credit_union")["industry"], "Credit Union")
        self.assertEqual(snapshot_registry.find(value, "Credit Union")["slug"], "credit_union")
        # 'credit-unions' is the R collector's slug, never a published label.
        for wrong in ("credit-unions", "credit-union", "CREDIT UNION", "Credit  Union", "", None, 12):
            with self.subTest(value=wrong):
                self.assertIsNone(snapshot_registry.find(value, wrong))

    def test_a_lock_file_snapshot_never_steals_its_workbook_s_slug(self):
        """Defence in depth: any producer that emits a '~$' entry, from anywhere.

        Measured on the real report directory on 2026-09-16, when
        contribution_data._sources() still globbed '* Industry Spending
        Overview.xlsx' without skipping Excel's lock file, so '~$Credit Union
        Industry Spending Overview.xlsx' arrived as a ninth snapshot whose slug
        was 'credit_union' -- the same slug as the book it locks.
        _sources() skips '~$' names now (contribution_data.py:184-186), so this
        no longer reproduces through that path. The entry shape is still
        reachable: registry() and find() accept any source list handed to them,
        including one from a future producer, a fixture, or a cached scan taken
        before that skip existed. What this pins is that addressing stays
        deterministic whenever such an entry appears: a label wins outright and
        a slug resolves to the readable snapshot, rather than to whichever of
        the two the sort happened to put first.
        """
        phantom = dict(self.sources[1])  # Credit Union
        phantom["workbook"] = self.reports / "~$Credit Union Industry Spending Overview.xlsx"
        phantom["workbook"].write_bytes(b"lock")
        phantom.update(id="~$Credit Union", industry="~$Credit Union", label="~$Credit Union",
                       available=False, message="Pinned report source evidence is unavailable.")
        for key in ("database", "database_sha256", "payload_sha256", "destination_schema",
                    "hydration_manifest", "data_workbook", "data_workbook_sha256",
                    "data_lineage_matches", "original_database_sha256"):
            phantom.pop(key, None)
        with patch.object(contribution_data, "_sources", return_value=self.sources + [phantom]):
            value = snapshot_registry.registry(self.reports)
        self.assertEqual(snapshot_registry.slug("~$Credit Union"), "credit_union")
        self.assertIn("~$Credit Union", [item["industry"] for item in value["snapshots"]])
        self.assertEqual(snapshot_registry.find(value, "credit_union")["industry"], "Credit Union")
        self.assertIs(snapshot_registry.find(value, "credit_union")["available"], True)
        self.assertEqual(snapshot_registry.find(value, "Credit Union")["industry"], "Credit Union")
        self.assertEqual(snapshot_registry.find(value, "~$Credit Union")["industry"], "~$Credit Union")

    def test_find_accepts_a_report_directory_as_well_as_a_built_registry(self):
        with patch.object(contribution_data, "_sources", return_value=self.sources):
            self.assertEqual(snapshot_registry.find(self.reports, "banking")["industry"], "Banking")

    def test_every_contract_negative_control_is_rejected(self):
        controls = CONTRACT["negative_controls"]
        self.assertTrue(controls, "a contract with no negative control is not finished")
        for control in controls:
            with self.subTest(why=control["why"][:60]):
                with self.assertRaises(ValueError):
                    snapshot_registry.validate_entry(control["payload"])

    def test_the_negative_control_assertion_has_teeth(self):
        # The same call that rejects every control above accepts every example.
        for example in EXAMPLE["snapshots"]:
            with self.subTest(industry=example["industry"]):
                self.assertIs(snapshot_registry.validate_entry(example), example)

    def test_the_envelope_is_validated_too(self):
        good = self.registry()
        for label, broken in (("schema_version", {**good, "schema_version": 2}),
                              ("report_directory", {**good, "report_directory": ""}),
                              ("generated_at", {**good, "generated_at": None}),
                              ("snapshots", {**good, "snapshots": "Banking"}),
                              ("not an object", "Banking")):
            with self.subTest(broken=label):
                with self.assertRaises(ValueError):
                    snapshot_registry.validate(broken)


class UnreadableWorkbookTests(unittest.TestCase):
    """One book this process cannot open must not take the whole list down.

    contribution_data._sources() hashed each workbook outside its try block, so a
    single PermissionError -- Excel holding the file, a scanner mid-pass --
    propagated out of the scan. Every route that lists snapshots then answered
    400: session, snapshots, graph and comparison, none of which had touched
    contribution data before S4. The real _sources() runs here, not a double.
    """

    UNREADABLE = "Report workbook is unreadable."

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.reports = Path(self.temp.name)
        self.books = []
        for industry in ("Alpha", "Beta"):
            path = self.reports / (industry + " Industry Spending Overview.xlsx")
            path.write_bytes(b"")
            self.books.append(path)

    def breaking_hash(self):
        """The real _hash, except that opening the first workbook fails."""
        real = contribution_data._hash.__wrapped__ if hasattr(contribution_data._hash, "__wrapped__") \
            else contribution_data._hash
        first = self.books[0].resolve()

        def _hash(path):
            if Path(path).resolve() == first:
                raise PermissionError(13, "The process cannot access the file")
            return real(path)
        return _hash

    def test_one_unreadable_workbook_leaves_the_other_snapshots_listed(self):
        with patch.object(contribution_data, "_hash", self.breaking_hash()):
            sources = contribution_data._sources(self.reports)
        self.assertEqual([item["industry"] for item in sources], ["Alpha", "Beta"])
        self.assertIs(sources[0]["available"], False)
        self.assertEqual(sources[0]["message"], self.UNREADABLE)
        self.assertIsNone(sources[0]["workbook_sha256"])
        # The message names no file: the convention everywhere in this reader.
        self.assertNotIn("Alpha", sources[0]["message"])
        self.assertNotIn(".xlsx", sources[0]["message"])
        # The second book was read; it is unavailable for its own, different reason.
        self.assertRegex(sources[1]["workbook_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(sources[1].get("message"), self.UNREADABLE)

    def test_the_registry_still_lists_both_and_validates(self):
        with patch.object(contribution_data, "_hash", self.breaking_hash()):
            value = snapshot_registry.registry(self.reports)
        self.assertEqual([item["industry"] for item in value["snapshots"]], ["Alpha", "Beta"])
        self.assertIs(snapshot_registry.validate(value), value)
        unreadable, readable = value["snapshots"]
        self.assertIs(unreadable["available"], False)
        self.assertEqual(unreadable["message"], self.UNREADABLE)
        # No hash means no identity, so no stamp is invented for it.
        self.assertIsNone(unreadable["workbook"]["sha256"])
        self.assertIsNone(unreadable["build_stamp"])
        self.assertRegex(readable["build_stamp"], r"^[0-9a-f]{64}$")

    def test_without_the_broken_hash_neither_entry_is_unreadable(self):
        # The negative control on the fixture: the failure above is the patched
        # OSError, not something these two empty files do on their own.
        sources = contribution_data._sources(self.reports)
        self.assertEqual(len(sources), 2)
        for item in sources:
            with self.subTest(industry=item["industry"]):
                self.assertNotEqual(item.get("message"), self.UNREADABLE)
                self.assertRegex(item["workbook_sha256"], r"^[0-9a-f]{64}$")
        value = snapshot_registry.registry(self.reports)
        for item in value["snapshots"]:
            self.assertRegex(item["build_stamp"], r"^[0-9a-f]{64}$")

    def test_a_workbook_that_vanishes_mid_request_never_leaks_its_path(self):
        """The report folder is live: a book can go between the scan and the entry.

        _file() stat'd it unguarded, so the FileNotFoundError reached the
        route's generic handler and str(error) put the ABSOLUTE PATH into the
        JSON error body - contradicting the no-filename rule the same reader
        states everywhere else. The entry now reports the same way an
        unreadable one does, and nothing raises.
        """
        missing = self.reports / "Gamma Industry Spending Overview.xlsx"
        self.assertFalse(missing.exists())
        source = {"id": "Gamma", "industry": "Gamma", "label": "Gamma",
                  "workbook": missing, "workbook_sha256": synthetic("Gamma", "workbook"),
                  "available": True, "destination_schema": 1,
                  "payload_sha256": synthetic("Gamma", "payload")}
        with patch.object(contribution_data, "_sources", return_value=[source]):
            value = snapshot_registry.registry(self.reports)
        self.assertIs(snapshot_registry.validate(value), value)
        gone = value["snapshots"][0]
        self.assertIs(gone["available"], False)
        self.assertEqual(gone["message"], "Report workbook is unreadable.")
        self.assertIsNone(gone["workbook"]["bytes"])
        self.assertIsNone(gone["workbook"]["modified_at"])
        # The hash measured before the file went away describes bytes nobody
        # can produce any more, so it is dropped with the stamp.
        self.assertIsNone(gone["workbook"]["sha256"])
        self.assertIsNone(gone["build_stamp"])
        # Only the file NAME is published, never the path it was read from.
        flat = json.dumps(value["snapshots"])
        self.assertIn("Gamma Industry Spending Overview.xlsx", flat)
        self.assertNotIn(str(self.reports), flat)

    def test_the_vanished_file_control_is_the_missing_file_and_nothing_else(self):
        # Negative control: the very same source, with the file present, keeps
        # its size, its time, its hash and its stamp, and stays available.
        present = self.reports / "Gamma Industry Spending Overview.xlsx"
        present.write_bytes(b"overview Gamma")
        self.addCleanup(present.unlink)
        source = {"id": "Gamma", "industry": "Gamma", "label": "Gamma",
                  "workbook": present, "workbook_sha256": synthetic("Gamma", "workbook"),
                  "available": True, "destination_schema": 1,
                  "payload_sha256": synthetic("Gamma", "payload")}
        with patch.object(contribution_data, "_sources", return_value=[source]):
            value = snapshot_registry.registry(self.reports)
        here = value["snapshots"][0]
        self.assertIs(here["available"], True)
        self.assertIsNone(here["message"])
        self.assertGreater(here["workbook"]["bytes"], 0)
        self.assertRegex(here["workbook"]["modified_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(here["workbook"]["sha256"], synthetic("Gamma", "workbook"))
        self.assertRegex(here["build_stamp"], r"^[0-9a-f]{64}$")
        # And an AVAILABLE entry may not report None for either measured field.
        for field in ("bytes", "modified_at"):
            with self.subTest(field=field):
                broken = json.loads(json.dumps(here))
                broken["workbook"][field] = None
                with self.assertRaises(ValueError):
                    snapshot_registry.validate_entry(broken)

    def test_an_available_entry_may_never_drop_its_hash_or_its_stamp(self):
        # The relaxation is scoped to available:false. An available entry missing
        # either field is still the defect negative control 1 describes.
        with patch.object(contribution_data, "_hash", self.breaking_hash()):
            value = snapshot_registry.registry(self.reports)
        entry = dict(value["snapshots"][0])
        entry["available"] = True
        with self.assertRaises(ValueError):
            snapshot_registry.validate_entry(entry)
        entry = dict(value["snapshots"][1])
        entry["build_stamp"] = None
        with self.assertRaises(ValueError):
            snapshot_registry.validate_entry(entry)


if __name__ == "__main__":
    unittest.main()
