# -*- coding: utf-8 -*-
"""Every NDJSON reader in the lane must tolerate a leading UTF-8 BOM.

PowerShell 5.1's ``Out-File -Encoding utf8`` prepends ``EF BB BF``, and on
2026-07-27 that already broke a git pathspec file and an R script on this
workstation.  A reader opened with ``utf-8`` raises on the first line of a
BOM-prefixed file -- ``json.decoder.JSONDecodeError: Expecting value`` -- so a
single accidental PowerShell redirect could poison a whole lane run.  Reading
with ``utf-8-sig`` is strictly more permissive: it strips a leading BOM if
present and is otherwise identical, so it costs nothing.

Byte-oriented readers that hash raw lines into receipts strip the BOM before
hashing, because the BOM is an encoding marker and not payload: a receipt must
be the same for a BOM-prefixed and a BOM-free copy of the same records.

Two readers are exempt on purpose -- see ``DEFERRED_SELF_PINNING_READERS``
below, and the test class that keeps that exemption justified.

Run with::

    python -m pytest fec/tests/infra/test_ndjson_bom_tolerance.py -q

Set ``XCUT_FEC_ROOT`` to point the imports at a different ``fec`` directory;
that is how the pre-fix failure was demonstrated.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


FEC = Path(os.environ.get("XCUT_FEC_ROOT") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(FEC))


ROWS = [
    {"canonical_key": "119:hr:1", "query_id": "q1", "title": "Ünïcode title"},
    {"canonical_key": "118:samdt:42", "query_id": "q2", "title": "second"},
]
BODY = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in ROWS)
UTF8_BOM = "﻿"


def _write(directory: Path, name: str, *, bom: bool) -> Path:
    path = directory / name
    path.write_bytes(((UTF8_BOM if bom else "") + BODY).encode("utf-8"))
    return path


def _consume(value):
    """Force generators so a lazy reader actually touches the file."""

    return list(value) if hasattr(value, "__iter__") and not isinstance(
        value, (list, tuple, dict, str, bytes)
    ) else value


# (module, function name, how to call it, how many rows the result implies)
READERS = (
    ("audit.audit_billstatus_snapshot_divergence", "iter_ndjson",
     lambda fn, p: len(list(fn(p)))),
    ("audit.audit_billstatus_snapshot_divergence", "ndjson_logical_array_receipt",
     lambda fn, p: fn(p)[0]),
    ("audit.audit_candidate_membership_review_partition", "_read_ndjson",
     lambda fn, p: fn(p, on_row=lambda *_a: None)[0]),
    ("audit.audit_named_outline_recall_misses", "iter_ndjson_with_receipts",
     lambda fn, p: len(list(fn(p)))),
    ("legislation.augment_partial_review_prefill_structured_relevance", "iter_ndjson",
     lambda fn, p: len(list(fn(p)))),
    ("legislation.build_accepted_legislation_generation", "read_ndjson",
     lambda fn, p: len(fn(p))),
    ("legislation.build_billstatus_relevance_sidecar", "count_ndjson",
     lambda fn, p: fn(p)),
    ("legislation.build_billstatus_review_sidecar", "iter_ndjson",
     lambda fn, p: len(list(fn(p)))),
    ("legislation.build_legislation_candidate_evidence", "read_ndjson",
     lambda fn, p: len(list(fn(p)))),
    ("legislation.build_legislation_category_review_queue", "read_ndjson",
     lambda fn, p: len(list(fn(p, label="fixture")))),
    ("legislation.build_legislation_membership_decisions", "read_ndjson",
     lambda fn, p: len(list(fn(p)))),
    ("legislation.build_partial_legislation_review_prefill", "iter_ndjson",
     lambda fn, p: len(list(fn(p)))),
    ("legislation.build_partial_legislation_review_queue", "iter_ndjson",
     lambda fn, p: len(list(fn(p)))),
    ("legislation.compute_legislation_policy_scenarios", "read_ndjson",
     lambda fn, p: len(fn(p))),
    ("legislation.cross_reference_legislation_press_articles", "read_ndjson",
     lambda fn, p: len(fn(p))),
    ("legislation.merge_congress_advanced_coverage_runs", "read_ndjson",
     lambda fn, p: len(fn(p))),
    ("legislation.reconcile_congress_advanced_pages", "read_ndjson",
     lambda fn, p: len(fn(p))),
    ("legislation.review_legislation_candidate_memberships", "read_ndjson",
     lambda fn, p: len(fn(p))),
    ("legislation.stage_billstatus_bulk_enrichment", "read_ndjson_with_provenance",
     lambda fn, p: len(fn(p))),
    ("legislation.stage_billstatus_bulk_enrichment", "ndjson_logical_array_receipt",
     lambda fn, p: fn(p)[0]),
    ("audit.audit_legfix5_coverage_reconciliation", "read_ndjson",
     lambda fn, p: len(list(fn(p)))),
    ("legislation.build_legislation_staged_integration_gate", "_iter_ndjson",
     lambda fn, p: len(list(fn(p)))),
    ("legislation.run_reversible_legislation_culling", "read_ndjson",
     lambda fn, p: len(fn(p, label="fixture"))),
)

# Two readers are deliberately NOT migrated.  Both live in producers that pin
# a receipt of their own source file: ``validate_evidence`` compares the
# recorded ``producer`` receipt against ``sha256(Path(__file__))``, so *any*
# byte change to them -- including a pure BOM-tolerance fix -- flips the
# corresponding informational readiness check to ``STALE_OR_INVALID`` until the
# producer is re-run to re-mint its pointer.  Re-running a producer is a
# data-writing action outside the scope of an encoding fix, and both readers
# only ever consume snapshot artifacts the producer itself wrote (which cannot
# carry a PowerShell BOM), so the fix is deferred rather than forced.
#
# Follow-up: migrate these two together with a coordinated re-mint of
# ``audits/.../post_weekly_refresh_*/current.json``.
DEFERRED_SELF_PINNING_READERS = (
    ("audit.audit_postrefresh_behavior_stance_semantics", "read_ndjson"),
    ("audit.audit_postrefresh_planning_dashboard_semantics", "read_ndjson"),
)

# Readers whose result carries a per-row or whole-file receipt.  The receipt
# must not change when a BOM is bolted onto the front of the same records.
RECEIPT_READERS = (
    ("audit.audit_billstatus_snapshot_divergence", "ndjson_logical_array_receipt",
     lambda fn, p: fn(p)[1]),
    ("legislation.stage_billstatus_bulk_enrichment", "ndjson_logical_array_receipt",
     lambda fn, p: fn(p)[1]),
    ("audit.audit_billstatus_snapshot_divergence", "iter_ndjson",
     lambda fn, p: [receipt for _n, receipt, _row in fn(p)]),
    ("audit.audit_named_outline_recall_misses", "iter_ndjson_with_receipts",
     lambda fn, p: [receipt for _n, _row, receipt in fn(p)]),
    ("legislation.build_legislation_category_review_queue", "read_ndjson",
     lambda fn, p: [receipt for _row, receipt in fn(p, label="fixture")]),
    ("legislation.stage_billstatus_bulk_enrichment", "read_ndjson_with_provenance",
     lambda fn, p: [row["_input_line_sha256"] for row in fn(p)]),
    # The culling engine hashes every line it reads into a per-row digest, so
    # the BOM must be stripped *before* hashing or the digests move.
    ("legislation.run_reversible_legislation_culling", "read_ndjson",
     lambda fn, p: [sha for _row, sha in fn(p, label="fixture")]),
)


class BomTolerance(unittest.TestCase):
    def test_every_reader_parses_a_bom_prefixed_file(self) -> None:
        failures = []
        with tempfile.TemporaryDirectory(prefix="xcut-bom-") as raw:
            directory = Path(raw)
            for index, (module_name, attribute, invoke) in enumerate(READERS):
                module = __import__(module_name, fromlist=["*"])
                function = getattr(module, attribute)
                path = _write(directory, f"bom_{index}.ndjson", bom=True)
                try:
                    observed = invoke(function, path)
                except Exception as error:  # noqa: BLE001 - report every reader
                    failures.append(
                        f"{module_name}.{attribute}: "
                        f"{type(error).__name__}: {error}"
                    )
                    continue
                if observed != len(ROWS):
                    failures.append(
                        f"{module_name}.{attribute}: read {observed} rows, "
                        f"expected {len(ROWS)}"
                    )
        self.assertEqual(
            failures, [], "readers that still choke on a UTF-8 BOM:\n" + "\n".join(failures)
        )

    def test_bom_free_files_are_unaffected(self) -> None:
        failures = []
        with tempfile.TemporaryDirectory(prefix="xcut-nobom-") as raw:
            directory = Path(raw)
            for index, (module_name, attribute, invoke) in enumerate(READERS):
                module = __import__(module_name, fromlist=["*"])
                function = getattr(module, attribute)
                path = _write(directory, f"clean_{index}.ndjson", bom=False)
                observed = invoke(function, path)
                if observed != len(ROWS):
                    failures.append(f"{module_name}.{attribute}: {observed}")
        self.assertEqual(failures, [], f"BOM-free regression: {failures}")

    def test_receipts_ignore_the_encoding_marker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="xcut-receipt-") as raw:
            directory = Path(raw)
            clean = _write(directory, "clean.ndjson", bom=False)
            bommed = _write(directory, "bommed.ndjson", bom=True)
            for module_name, attribute, invoke in RECEIPT_READERS:
                module = __import__(module_name, fromlist=["*"])
                function = getattr(module, attribute)
                self.assertEqual(
                    invoke(function, bommed),
                    invoke(function, clean),
                    f"{module_name}.{attribute}: BOM changed the receipt",
                )

    def test_no_touched_reader_opens_ndjson_as_plain_utf8(self) -> None:
        offenders = []
        for module_name in sorted({name for name, _a, _i in READERS}):
            source = (FEC / Path(*module_name.split("."))).with_suffix(".py").read_text(
                encoding="utf-8"
            )
            code = "\n".join(
                line
                for line in source.splitlines()
                if not line.lstrip().startswith("#")
            )
            for marker in ('path.open("r", encoding="utf-8")',
                           'path.read_text(encoding="utf-8").splitlines()'):
                if marker in code:
                    offenders.append(f"{module_name}: {marker}")
        self.assertEqual(offenders, [], f"BOM-intolerant reads remain: {offenders}")


class DeferredSelfPinningProducers(unittest.TestCase):
    """The deferral must stay justified, not become a quiet exemption."""

    def test_deferred_readers_really_do_pin_their_own_source(self) -> None:
        import re as _re

        pin = _re.compile(r"producer_path\s*=\s*\(?.*Path\(__file__\)")
        for module_name, _attribute in DEFERRED_SELF_PINNING_READERS:
            source = (FEC / Path(*module_name.split("."))).with_suffix(".py").read_text(
                encoding="utf-8"
            )
            self.assertIsNotNone(
                pin.search(source),
                f"{module_name} no longer derives a producer receipt from its "
                f"own source; the BOM fix is no longer blocked and should be "
                f"applied",
            )
            self.assertIn(
                '"producer"',
                source,
                f"{module_name} no longer records a producer receipt; the BOM "
                f"fix is no longer blocked and should be applied",
            )

    def test_deferred_readers_are_still_the_only_exemption(self) -> None:
        migrated = {name for name, _a, _i in READERS}
        deferred = {name for name, _a in DEFERRED_SELF_PINNING_READERS}
        self.assertEqual(migrated & deferred, set())


class SharedHelper(unittest.TestCase):
    def test_helper_strips_only_a_leading_bom(self) -> None:
        lane_io = __import__("lane_file_io")
        self.assertEqual(lane_io.strip_utf8_bom(b"\xef\xbb\xbf{}"), b"{}")
        self.assertEqual(lane_io.strip_utf8_bom(b"{}"), b"{}")
        self.assertEqual(
            lane_io.strip_utf8_bom(b'{"a":"\xef\xbb\xbf"}'), b'{"a":"\xef\xbb\xbf"}'
        )
        self.assertEqual(lane_io.strip_utf8_bom_text("﻿{}"), "{}")
        self.assertEqual(lane_io.strip_utf8_bom_text("{}"), "{}")

    def test_raw_line_iterator_strips_the_bom_only_on_line_one(self) -> None:
        lane_io = __import__("lane_file_io")
        with tempfile.TemporaryDirectory(prefix="xcut-rawline-") as raw:
            path = Path(raw) / "rows.ndjson"
            path.write_bytes(b"\xef\xbb\xbf" + BODY.encode("utf-8"))
            lines = list(lane_io.iter_ndjson_raw_lines(path))
            self.assertEqual([number for number, _raw in lines], [1, 2])
            self.assertFalse(lines[0][1].startswith(b"\xef\xbb\xbf"))
            self.assertEqual(
                json.loads(lines[0][1])["canonical_key"], "119:hr:1"
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
