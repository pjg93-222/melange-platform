# -*- coding: utf-8 -*-
"""One canonical-measure-key contract for the whole legislation lane.

Before ``fec/legislation_measure_key.py`` existed the lane held sixteen
independent definitions in three incompatible dialects, and eight of them
hardcoded ``116|117|118|119``.  These tests hold the merge in place:

* there is exactly one compiled pattern object, shared by every migrated
  module (so the definitions cannot silently drift apart again);
* the Congress window is one named constant, so seating the 120th Congress on
  2027-01-03 is a one-line edit rather than eight regexes in lockstep;
* collectors and gates now accept and reject exactly the same keys.

Run with::

    python -m pytest fec/tests/legislation/test_legislation_measure_key_contract.py -q

Set ``XCUT_FEC_ROOT`` to point the imports at a different ``fec`` directory;
that is how the pre-fix failure was demonstrated.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest


FEC = Path(os.environ.get("XCUT_FEC_ROOT") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(FEC))

import legislation_measure_key as KEY  # noqa: E402


# (module name, attribute that used to hold a private compiled pattern)
MIGRATED = (
    ("legislation.legislation_culling_integration_contract", "MEASURE_RE"),
    ("legislation.build_legislation_candidate_evidence", "CANONICAL_KEY_RE"),
    ("legislation.build_legislation_category_review_queue", "CANONICAL_KEY_RE"),
    ("legislation.build_legislation_membership_decisions", "CANONICAL_KEY_RE"),
    ("legislation.build_accepted_legislation_generation", "MEASURE_KEY_RE"),
    ("legislation.build_acu_bill_title_corroboration_query_plan", "CANONICAL_KEY_RE"),
    ("legislation.build_legislation_workbook_numeric_plan", "MEASURE_RE"),
    ("legislation.compute_legislation_policy_scenarios", "MEASURE_KEY_RE"),
    ("legislation.run_legislation_claude_ambiguity_review", "CANONICAL_KEY_RE"),
    ("legislation.collect_govinfo_bill_xml_versions", "KEY_RE"),
    ("legislation.collect_official_rollcall_events", "CANONICAL_KEY_RE"),
    ("legislation.review_legislation_candidate_memberships", "CANONICAL_KEY_RE"),
    ("legislation.assemble_approved_legislation_generation", "MEASURE_RE"),
    ("audit.audit_legfix5_coverage_reconciliation", "CANONICAL_KEY_RE"),
    ("legislation.run_reversible_legislation_culling", "CANONICAL_KEY_RE"),
)

# ``build_accepted_legislation_workbook.MEASURE_RE`` is the one remaining
# hardcoded copy and is deliberately NOT migrated.  That producer pins a
# SHA-256 of its own source into
# ``audits/legfix5/spreadsheet_engine_verification/
# accepted_workbook_producer_capability.json`` and two consumers compare the
# stored value against ``sha256_file(build_accepted_legislation_workbook.py)``:
# ``extract_legislation_workbook_audited_layout`` (raises
# ``LayoutExtractionError``) and ``build_legislation_shadow_consumer_plan``
# (reports "capability receipt is not bound to current producer source").
# Any byte change therefore requires re-running the workbook producer to
# re-mint that receipt, which is a data-writing action outside the scope of a
# regex consolidation.
DEFERRED_SELF_PINNING_PRODUCERS = (
    ("legislation.build_accepted_legislation_workbook", "MEASURE_RE"),
)

# Keys the strict gates have always rejected.  Two collectors used to accept
# every one of them; one reader used to accept the mixed-case form.
GATE_REJECTED = (
    "120:hr:1",       # a Congress the lane does not carry yet
    "1:hr:1",         # any Congress at all, under the permissive pattern
    "117:hr:0",       # zero is not a measure number
    "117:hr:007",     # leading zeros mint a duplicate key for one measure
    "117:xx:1",       # a measure type that does not exist
    "117:HR:1",       # mixed case mints a duplicate key for one measure
    "117:hr",         # structurally short
    "117:hr:1:2",     # structurally long
    "",
)


def _load(name: str):
    return __import__(name, fromlist=["*"])


class SharedDefinition(unittest.TestCase):
    def test_window_is_one_named_constant(self) -> None:
        self.assertEqual(KEY.SUPPORTED_CONGRESSES[0], KEY.FIRST_SUPPORTED_CONGRESS)
        self.assertEqual(KEY.SUPPORTED_CONGRESSES[-1], KEY.LAST_SUPPORTED_CONGRESS)
        self.assertEqual(
            KEY.SUPPORTED_CONGRESSES,
            tuple(range(KEY.FIRST_SUPPORTED_CONGRESS, KEY.LAST_SUPPORTED_CONGRESS + 1)),
        )

    def test_bumping_the_constant_seats_the_120th_congress(self) -> None:
        """The 2027-01-03 rollover must be a one-line edit, not eight."""

        self.assertIsNone(KEY.CANONICAL_KEY_RE.fullmatch("120:hr:1"))
        next_window = tuple(
            range(KEY.FIRST_SUPPORTED_CONGRESS, KEY.LAST_SUPPORTED_CONGRESS + 2)
        )
        rolled = KEY.build_canonical_key_re(next_window)
        self.assertIsNotNone(rolled.fullmatch("120:hr:1"))
        self.assertIsNotNone(rolled.fullmatch("119:samdt:12"))

    def test_strict_semantics(self) -> None:
        for congress in KEY.SUPPORTED_CONGRESSES:
            for measure_type in KEY.MEASURE_TYPES:
                key = f"{congress}:{measure_type}:1"
                self.assertIsNotNone(
                    KEY.CANONICAL_KEY_RE.fullmatch(key), f"should accept {key}"
                )
        for key in GATE_REJECTED:
            self.assertIsNone(
                KEY.CANONICAL_KEY_RE.fullmatch(key), f"should reject {key!r}"
            )

    def test_group_access_is_backwards_compatible(self) -> None:
        """Old call sites used group(1..3), groups(), named groups and groupdict."""

        match = KEY.CANONICAL_KEY_RE.fullmatch("118:sconres:42")
        assert match is not None
        self.assertEqual(match.group(1), "118")
        self.assertEqual(match.group(2), "sconres")
        self.assertEqual(match.group(3), "42")
        self.assertEqual(match.groups(), ("118", "sconres", "42"))
        self.assertEqual(match.group("congress"), "118")
        self.assertEqual(match.group("measure_type"), "sconres")
        self.assertEqual(match.group("number"), "42")
        self.assertEqual(
            match.groupdict(),
            {"congress": "118", "measure_type": "sconres", "number": "42"},
        )

    def test_prefix_measure_types_do_not_shadow_longer_ones(self) -> None:
        """``s`` must not swallow ``sres``/``sjres``/``sconres``/``samdt``."""

        for measure_type in ("s", "sres", "sjres", "sconres", "samdt"):
            match = KEY.CANONICAL_KEY_RE.fullmatch(f"119:{measure_type}:3")
            assert match is not None, measure_type
            self.assertEqual(match.group("measure_type"), measure_type)

    def test_parse_helpers(self) -> None:
        self.assertEqual(KEY.parse_canonical_key(" 117:HR:1 "), (117, "hr", 1))
        self.assertEqual(KEY.normalize_canonical_key("117:HR:1"), "117:hr:1")
        self.assertEqual(KEY.measure_kind("119:samdt:7"), "samdt")
        self.assertTrue(KEY.is_amendment_key("119:hamdt:7"))
        self.assertFalse(KEY.is_amendment_key("119:hr:7"))
        self.assertTrue(KEY.is_canonical_key("119:hr:7"))
        self.assertFalse(KEY.is_canonical_key("119:HR:7"))
        for key in GATE_REJECTED:
            if key == "117:HR:1":
                # parse_canonical_key normalizes case on purpose; that is the
                # explicit opt-in that replaced the accidental re.IGNORECASE.
                continue
            with self.assertRaises(KEY.MeasureKeyError):
                KEY.parse_canonical_key(key)


class NoDuplicateDefinitions(unittest.TestCase):
    def test_every_migrated_module_shares_one_pattern_object(self) -> None:
        for name, attribute in MIGRATED:
            module = _load(name)
            self.assertIs(
                getattr(module, attribute),
                KEY.CANONICAL_KEY_RE,
                f"{name}.{attribute} is not the shared definition",
            )

    def test_no_migrated_module_hardcodes_the_congress_window(self) -> None:
        offenders = []
        for name, _attribute in MIGRATED:
            source = (FEC / Path(*name.split("."))).with_suffix(".py").read_text(
                encoding="utf-8"
            )
            if "116|117|118|119" in source:
                offenders.append(name)
        self.assertEqual(offenders, [], f"hardcoded Congress window in {offenders}")

    def test_the_deferred_producer_is_the_only_hardcoded_copy_left(self) -> None:
        """No module outside the documented deferral may hardcode the window."""

        deferred = {name for name, _a in DEFERRED_SELF_PINNING_PRODUCERS}
        offenders = []
        for path in sorted(FEC.glob("*.py")):
            if path.stem in deferred or path.stem == "legislation_measure_key":
                continue
            if "116|117|118|119" in path.read_text(encoding="utf-8"):
                offenders.append(path.stem)
        self.assertEqual(offenders, [], f"hardcoded Congress window in {offenders}")


class DeferredSelfPinningProducers(unittest.TestCase):
    """The deferral must stay justified, not become a quiet exemption."""

    def test_deferred_producer_still_pins_a_receipt_of_its_own_source(self) -> None:
        for name, _attribute in DEFERRED_SELF_PINNING_PRODUCERS:
            # Dotted module names resolve as path segments, same as the
            # MIGRATED loop above -- FEC / f"{name}.py" broke when the
            # producer moved into the legislation package.
            source = (FEC / Path(*name.split("."))).with_suffix(".py").read_text(
                encoding="utf-8"
            )
            self.assertIn(
                "sha256_file(Path(__file__).resolve())",
                source,
                f"{name} no longer pins a hash of its own source; the regex "
                f"migration is no longer blocked and should be applied",
            )

    def test_a_consumer_still_compares_that_pin_to_the_live_source(self) -> None:
        consumer = (
            FEC / "legislation" / "extract_legislation_workbook_audited_layout.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'capability.get("producer_sha256") == sha256_file(producer)',
            consumer,
            "nothing compares the workbook producer pin any more; the regex "
            "migration is no longer blocked and should be applied",
        )

    def test_deferred_producer_is_not_also_listed_as_migrated(self) -> None:
        migrated = {name for name, _a in MIGRATED}
        deferred = {name for name, _a in DEFERRED_SELF_PINNING_PRODUCERS}
        self.assertEqual(migrated & deferred, set())


class CollectorsAgreeWithGates(unittest.TestCase):
    """Collectors used to mint keys the gates reject.  Now they cannot."""

    def _gate_rejects(self, key: str) -> None:
        contract = _load("legislation.legislation_culling_integration_contract")
        with self.assertRaises(contract.CullingContractError):
            contract._measure_kind(key)

    def test_govinfo_collector_rejects_what_the_gate_rejects(self) -> None:
        collector = _load("legislation.collect_govinfo_bill_xml_versions")
        for key in GATE_REJECTED:
            self._gate_rejects(key)
            with self.assertRaises(collector.CollectionError, msg=key):
                collector.parse_key(key)

    def test_rollcall_collector_rejects_what_the_gate_rejects(self) -> None:
        collector = _load("legislation.collect_official_rollcall_events")
        for key in GATE_REJECTED:
            if key == "117:HR:1":
                # This entry point casefolds by contract before matching.
                continue
            with self.assertRaises(collector.CollectorError, msg=key):
                collector.parse_canonical_measure_key(key)

    def test_acu_query_plan_rejects_mixed_case_duplicates(self) -> None:
        plan = _load("legislation.build_acu_bill_title_corroboration_query_plan")
        # The plan casefolds first, so mixed case still resolves; what changed
        # is that its pattern no longer carries re.IGNORECASE, so nothing else
        # in the lane can accept "117:HR:1" as a second key for one measure.
        self.assertEqual(plan.canonical_parts("117:HR:1"), (117, "hr", 1))
        for key in ("120:hr:1", "117:hr:0", "117:hr:007", "117:xx:1"):
            with self.assertRaises(plan.QueryPlanError, msg=key):
                plan.canonical_parts(key)

    def test_membership_review_rejects_out_of_window_congresses(self) -> None:
        review = _load("legislation.review_legislation_candidate_memberships")
        self.assertEqual(
            review.canonical_measure_identity("117:HR:1", "canonical_key"),
            "117:hr:1",
        )
        for key in ("120:hr:1", "1:hr:1", "117:hr:0", "117:xx:1"):
            with self.assertRaises(ValueError, msg=key):
                review.canonical_measure_identity(key, "canonical_key")

    def test_valid_keys_still_flow_through_every_entry_point(self) -> None:
        govinfo = _load("legislation.collect_govinfo_bill_xml_versions")
        rollcall = _load("legislation.collect_official_rollcall_events")
        plan = _load("legislation.build_acu_bill_title_corroboration_query_plan")
        review = _load("legislation.review_legislation_candidate_memberships")
        contract = _load("legislation.legislation_culling_integration_contract")
        for key, kind in (("119:hr:1", "hr"), ("117:samdt:9", "samdt")):
            self.assertEqual(
                govinfo.parse_key(key)["measure_type"], kind
            )
            self.assertEqual(rollcall.parse_canonical_measure_key(key).measure_type, kind)
            self.assertEqual(plan.canonical_parts(key)[1], kind)
            self.assertEqual(review.canonical_measure_identity(key, "f"), key)
            self.assertEqual(contract._measure_kind(key), kind)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
