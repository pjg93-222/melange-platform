"""Contradictions, unusually large counted receipts and clear refunds.

The published report workbook is the accounting of record. This module never
restates it: it reads a completed run's decisions and lists only the rows a
reader would want to look at by eye - refunds the engine linked to an original
gift, negative adjustments it could not link, money an accepted baseline had
counted as giving that the engine reclassified as non-giving, and unusually
large counted receipts. Every amount here describes the listed rows, never a
total of giving. Large receipts are informational only: magnitude never
changes a decision or excludes a row.

``build_digest`` is pure. It consumes ``links`` first, then ``decisions``, then
``changes``, so a caller may hand it cursors instead of lists; it retains only
the rows it can list. ``entities``, ``source_urls`` and ``descriptions`` are
looked up with ``.get`` for a bounded set of record ids, so a caller may back
them with a lazy reader rather than a dictionary of every source row.

"Large" is relative to the run, never an absolute dollar figure: the counted
receipts are split into individuals and everything else, and a row is listed
only when it is both in the top slice of its own class and a large multiple of
that class's median receipt. The percentile and the multiple are proposed
defaults and await the owner's ratification; the resulting dollar thresholds,
the medians and the class sizes are stated in the digest so no reader has to
guess them.

CLI: ``python cross_reference_digest.py <run_dir> [--percentile P]
[--median-multiple M]`` opens ``<run_dir>/ledger.sqlite`` read-only and
writes ``<run_dir>/digest.json``. It modifies nothing else.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import closing
from datetime import datetime, timezone
import argparse
import bisect
import json
from pathlib import Path
import re
import sqlite3
import sys
import time

from report_source_guard import atomic

SCHEMA_VERSION = 1
LARGE_PERCENTILE = 99.9
LARGE_MEDIAN_MULTIPLE = 20
# A class this small has no distribution worth cutting, so it lists nothing.
MINIMUM_CLASS_RECORDS = 20
INDIVIDUAL_CLASS = "individual"
OTHER_CLASS = "other"
CLASS_LABELS = {INDIVIDUAL_CLASS: "individuals", OTHER_CLASS: "other contributors"}
ROW_LIMIT = 200
NON_GIVING_PREFIX = "non_giving_receipt_role:"
# The engine files ``signed_adjustment_not_linked_refund`` in its review queue
# and writes ``negative_amount_is_not_refund_proof`` on the decision itself
# (cross_reference_engine.py:522-523). Accept either name so this digest keeps
# following the engine whichever one reaches the decision row.
UNLINKED_REFUND_REASONS = ("signed_adjustment_not_linked_refund",
                           "negative_amount_is_not_refund_proof")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def percentile_setting(value, name: str = "percentile") -> float:
    """The class cut, as a percentile between 90 and 100 (100 excluded)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number from 90 up to but not including 100")
    value = float(value)
    if not 90 <= value < 100:
        raise ValueError(f"{name} must be a number from 90 up to but not including 100")
    return value


def multiple_setting(value, name: str = "median_multiple") -> int:
    """How many times the class median a listed receipt must reach."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive whole number")
    return value


def nearest_rank(sorted_cents, percentile: float) -> int:
    """The nearest-rank percentile of an ascending sample of whole cents.

    Nearest rank: the value whose 1-based position is ``ceil(percentile/100 * n)``
    counting from the smallest, clamped into the sample. It is a value the
    sample actually contains, so the printed threshold is a real receipt size
    and no interpolation invents a figure between two rows. The rank is taken
    in whole integers from the percentile rounded to six decimals, so the same
    sample always yields the same rank on any machine.
    """
    count = len(sorted_cents)
    if not count:
        raise ValueError("A percentile needs at least one value")
    scaled = round(float(percentile) * 1_000_000)
    rank = -(-scaled * count // 100_000_000)  # ceil(percentile * count / 100)
    return sorted_cents[min(max(rank, 1), count) - 1]


def class_thresholds(sorted_cents, percentile: float, multiple: int) -> dict:
    """Median, percentile and listing threshold for one class of counted receipts.

    The median is the nearest-rank 50th percentile, so both statistics use one
    definition and both stay whole cents. A class below the minimum size gets no
    threshold at all and therefore lists nothing.
    """
    records = len(sorted_cents)
    result = {"records": records, "median_cents": 0, "percentile_cents": 0,
              "threshold_cents": None, "listed_count": 0, "listed_cents": 0}
    if records < MINIMUM_CLASS_RECORDS:
        return result
    result["median_cents"] = nearest_rank(sorted_cents, 50)
    result["percentile_cents"] = nearest_rank(sorted_cents, percentile)
    result["threshold_cents"] = max(result["percentile_cents"], multiple * result["median_cents"])
    return result


def _cents(value) -> int:
    return int(value or 0)


def _dollars(cents: int) -> float:
    return round(cents / 100, 2)


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _text(value, limit: int = 300) -> str:
    return str(value or "")[:limit]


def _identifier(value) -> str:
    return str(value or "")[:120]


def _reasons(decision: Mapping) -> tuple:
    reasons = decision.get("reasons") or ()
    return tuple(reasons) if isinstance(reasons, (list, tuple)) else ()


def _row_view(decision: Mapping, amount: int, baseline: int, counted: int, entity: str) -> dict:
    return {"company": _text(decision.get("company"), 200), "cycle": decision.get("cycle"),
            "amount_cents": amount, "baseline_cents": baseline, "counted_cents": counted,
            "receipt_role": _text(decision.get("receipt_role"), 80), "entity_type": entity}


def _hold(rows: list, amount: int, record: str, view: dict) -> None:
    """Keep only the ``ROW_LIMIT`` biggest receipts of one class, in rank order.

    The listing threshold is unknown until every decision has been read, so the
    rows that could survive it are held here. A row dropped at a trim already
    had ``ROW_LIMIT`` bigger rows ahead of it and can never be listed.
    """
    rows.append((-amount, record, view))
    if len(rows) > 2 * ROW_LIMIT:
        rows.sort(key=lambda item: item[:2])
        del rows[ROW_LIMIT:]


def _share(percentile: float) -> str:
    return f"{round(100 - percentile, 6):g}"


def _threshold_text(classes: Mapping, name: str) -> str:
    threshold = classes.get(name, {}).get("threshold_cents")
    return "no figure at all" if threshold is None else _money(threshold) + " or more"


def basis_text(percentile: float, multiple: int, classes: Mapping) -> str:
    """One sentence a reader can check the listed rows against, plus any caveat."""
    sentence = (f"A receipt is listed as large when it is in the top {_share(percentile)}% of its own class and "
                f"at least {multiple}x that class's median counted receipt, which is "
                f"{_threshold_text(classes, INDIVIDUAL_CLASS)} from an individual and "
                f"{_threshold_text(classes, OTHER_CLASS)} from any other contributor; the {_share(percentile)}% "
                f"cut and the {multiple}x multiple are a proposed default and await the owner's ratification.")
    small = [name for name in (INDIVIDUAL_CLASS, OTHER_CLASS)
             if classes.get(name, {}).get("threshold_cents") is None]
    if small:
        sentence += (" This run counted fewer than " + str(MINIMUM_CLASS_RECORDS) + " receipts sent by " +
                     " and ".join(CLASS_LABELS[name] for name in small) +
                     ", too few to cut a distribution, so no such row is listed.")
    return sentence


def build_digest(decisions: Iterable[Mapping], links: Iterable[Mapping], changes: Iterable[Mapping], *,
                 run_id: str = "", generated_at: str = "", entities: Mapping | None = None,
                 source_urls: Mapping | None = None, descriptions: Mapping | None = None,
                 percentile: float = LARGE_PERCENTILE,
                 median_multiple: int = LARGE_MEDIAN_MULTIPLE) -> dict:
    """Return clear refunds, contradictions and unusually large counted rows.

    ``decisions`` are the engine's per-row decisions, ``links`` its supported
    links and ``changes`` the runner's presentation change rows. ``entities``
    maps a record id to its FEC ``entity_type``, ``source_urls`` to its public
    filing URL and ``descriptions`` to its receipt text; all three are optional
    fallbacks, and an absent entity type puts a row in the non-individual class.

    Large is measured against this run only and is strictly a display flag:
    every counted receipt remains counted and joins the
    individual or the non-individual class, and a row is listed when it reaches
    both the class's ``percentile`` and ``median_multiple`` times the class
    median. ``entities`` is therefore asked about every counted receipt.
    """
    percentile = percentile_setting(percentile)
    median_multiple = multiple_setting(median_multiple)
    entities = {} if entities is None else entities
    source_urls = {} if source_urls is None else source_urls
    descriptions = {} if descriptions is None else descriptions

    refund_links = []
    paired = set()
    for link in links:
        if link.get("kind") != "refund":
            continue
        refund, original = _identifier(link.get("source_id")), _identifier(link.get("counterpart_id"))
        if not refund or not original:
            continue
        refund_links.append((refund, original, _cents(link.get("amount_cents"))))
        paired.update((refund, original))

    kept: dict[str, dict] = {}
    unlinked, non_giving, large = [], [], []
    # Every counted receipt joins its class sample. The cut controls only which
    # rows the digest displays; it never changes counted_cents or exclusion.
    # Only the largest few of each class can survive the display cut, so their
    # rows are the only ones held while the thresholds are still unknown.
    samples: dict[str, list] = {INDIVIDUAL_CLASS: [], OTHER_CLASS: []}
    candidates: dict[str, list] = {INDIVIDUAL_CLASS: [], OTHER_CLASS: []}
    source_records = 0
    for decision in decisions:
        source_records += 1
        record = _identifier(decision.get("source_id"))
        if not record:
            continue
        reasons = _reasons(decision)
        amount, baseline = _cents(decision.get("amount_cents")), _cents(decision.get("baseline_cents"))
        counted = _cents(decision.get("counted_cents"))
        listed, entity = record in paired, ""
        if any(reason in UNLINKED_REFUND_REASONS for reason in reasons):
            unlinked.append(record)
            listed = True
        if baseline > 0 and any(str(reason).startswith(NON_GIVING_PREFIX) for reason in reasons):
            non_giving.append(record)
            listed = True
        if counted > 0:
            entity = _text(entities.get(record), 40).strip().upper()
            name = INDIVIDUAL_CLASS if entity == "IND" else OTHER_CLASS
            samples[name].append(abs(amount))
            _hold(candidates[name], abs(amount), record, _row_view(decision, amount, baseline, counted, entity))
        if listed:
            kept[record] = _row_view(decision, amount, baseline, counted, entity)

    # A class cut can only be drawn once every counted receipt has been seen.
    # Counting and totalling the listed rows uses the whole class sample; the
    # held display rows only decide which of them this digest prints.
    classes: dict[str, dict] = {}
    for name in (INDIVIDUAL_CLASS, OTHER_CLASS):
        sample = sorted(samples[name])
        summary = class_thresholds(sample, percentile, median_multiple)
        threshold = summary["threshold_cents"]
        if threshold is not None:
            start = bisect.bisect_left(sample, threshold)
            summary["listed_count"] = len(sample) - start
            summary["listed_cents"] = sum(sample[start:])
            for ranking, record, view in candidates[name]:
                if -ranking >= threshold:
                    kept.setdefault(record, view)
                    large.append(record)
        classes[name] = summary
    large_count = sum(summary["listed_count"] for summary in classes.values())
    large_listed_cents = sum(summary["listed_cents"] for summary in classes.values())

    wanted = set(unlinked) | set(non_giving)
    context: dict[str, dict] = {}
    for change in changes:
        record = _identifier(change.get("record_id") or change.get("source_id"))
        if record in wanted and record not in context:
            context[record] = {"description": _text(change.get("source_description")),
                               "source_url": _text(change.get("source_url"), 2000)}

    def url(record: str) -> str:
        found = context.get(record, {}).get("source_url") or source_urls.get(record) or ""
        return _text(found, 2000)

    def description(record: str) -> str:
        found = context.get(record, {}).get("description") or descriptions.get(record) or ""
        return _text(found)

    def row(record: str) -> dict:
        return kept.get(record, {})

    def ranked(records, amount_of):
        return sorted(records, key=lambda record: (-abs(amount_of(record)), record))

    refund_rows = ranked(refund_links, lambda link: link[2] or _cents(row(link[0]).get("amount_cents")))
    linked = [{"refund_record_id": refund, "original_record_id": original,
               "company": row(refund).get("company", "") or row(original).get("company", ""),
               "cycle": row(refund).get("cycle") if row(refund) else row(original).get("cycle"),
               "refund_amount": _dollars(amount or _cents(row(refund).get("amount_cents"))),
               "original_amount": _dollars(_cents(row(original).get("counted_cents")
                                                  or row(original).get("amount_cents"))),
               "refund_source_url": url(refund), "original_source_url": url(original)}
              for refund, original, amount in refund_rows[:ROW_LIMIT]]

    unlinked_rows = ranked(unlinked, lambda record: _cents(row(record).get("amount_cents")))
    unlinked_listed = [{"record_id": record, "company": row(record).get("company", ""),
                        "cycle": row(record).get("cycle"),
                        "amount": _dollars(_cents(row(record).get("amount_cents"))),
                        "source_url": url(record), "description": description(record)}
                       for record in unlinked_rows[:ROW_LIMIT]]

    non_giving_rows = ranked(non_giving, lambda record: _cents(row(record).get("baseline_cents")))
    non_giving_listed = [{"record_id": record, "company": row(record).get("company", ""),
                          "cycle": row(record).get("cycle"),
                          "amount": _dollars(_cents(row(record).get("baseline_cents"))),
                          "role": row(record).get("receipt_role", ""),
                          "description": description(record), "source_url": url(record)}
                         for record in non_giving_rows[:ROW_LIMIT]]

    large_rows = ranked(large, lambda record: _cents(row(record).get("amount_cents")))
    large_listed = [{"record_id": record, "company": row(record).get("company", ""),
                     "cycle": row(record).get("cycle"),
                     "amount": _dollars(_cents(row(record).get("amount_cents"))),
                     "entity": row(record).get("entity_type", ""),
                     "role": row(record).get("receipt_role", ""), "source_url": url(record)}
                    for record in large_rows[:ROW_LIMIT]]

    def total(records, field):
        return _dollars(sum(_cents(row(record).get(field)) for record in records))

    return {
        "schema_version": SCHEMA_VERSION, "run_id": _identifier(run_id), "generated_at": _text(generated_at, 60),
        "basis": {"percentile": percentile, "median_multiple": median_multiple,
                  "minimum_class_records": MINIMUM_CLASS_RECORDS, "classes": classes,
                  "text": basis_text(percentile, median_multiple, classes)},
        "refunds": {
            "linked": linked, "linked_count": len(refund_links),
            "linked_amount": _dollars(sum(amount or _cents(row(refund).get("amount_cents"))
                                          for refund, _, amount in refund_links)),
            "unlinked": unlinked_listed, "unlinked_count": len(unlinked),
            "unlinked_amount": total(unlinked, "amount_cents"),
        },
        "outliers": {
            "non_giving": non_giving_listed, "non_giving_count": len(non_giving),
            "non_giving_amount": total(non_giving, "baseline_cents"),
            "large": large_listed, "large_count": large_count,
            "large_amount": _dollars(large_listed_cents),
        },
        "counts": {"source_records": source_records},
    }


class _SourceRows:
    """Source rows read from the ledger one record id at a time, and only when asked.

    The Banking ledger holds 849,399 source records; the digest needs a handful
    of their fields for the few hundred rows it can list, so nothing is read
    until a view asks for a specific id.
    """

    def __init__(self, db: sqlite3.Connection):
        self._db = db
        self._cache: dict[str, dict] = {}

    def read(self, record: str) -> dict:
        if record not in self._cache:
            found = self._db.execute(
                "SELECT payload_json FROM source_records WHERE source_id=? LIMIT 1", (record,)).fetchone()
            try:
                value = json.loads(found[0]) if found else {}
            except ValueError:
                value = {}
            self._cache[record] = value if isinstance(value, dict) else {}
        return self._cache[record]

    def field(self, name: str) -> "_SourceField":
        return _SourceField(self, name)

    def receipt_text(self) -> "_SourceDescription":
        return _SourceDescription(self)


class _SourceField:
    def __init__(self, rows: _SourceRows, name: str):
        self._rows, self._name = rows, name

    def get(self, record, default=None):
        value = self._rows.read(str(record)).get(self._name)
        return default if value in (None, "") else value


class _SourceDescription:
    """The receipt text the report's change table shows, from the source row.

    The ledger stores the engine's change rows, which carry cents and reasons
    but no prose, so a digest built from a saved ledger reads the same two
    fields the runner joins in ``presentation`` (run_cross_reference.py:281).
    """

    def __init__(self, rows: _SourceRows):
        self._rows = rows

    def get(self, record, default=None):
        row = self._rows.read(str(record))
        value = "; ".join(part for part in (str(row.get(field) or "").strip()
                                            for field in ("receipt_type_full", "memo_text")) if part)
        return default if not value else value


ENTITY_FIELD = re.compile(r'"entity_type":"([^"]*)"')


def _entity_types(db: sqlite3.Connection) -> dict:
    """Every stored record's FEC entity type, in one pass over the ledger.

    Classing a run's counted receipts needs the entity type of each of them, so
    the one-record-at-a-time reader used for the handful of listed rows would
    read most of the table anyway. The stored payload is canonical JSON, so the
    field is lifted from the text and only a payload that does not match is
    parsed.
    """
    found = {}
    for source_id, payload in db.execute("SELECT source_id, payload_json FROM source_records"):
        match = ENTITY_FIELD.search(payload or "")
        if match:
            value = match.group(1)
        else:
            try:
                value = (json.loads(payload) or {}).get("entity_type") or ""
            except ValueError:
                value = ""
        if value:
            found[str(source_id)] = sys.intern(str(value))
    return found


def _payloads(db: sqlite3.Connection, table: str):
    for (payload,) in db.execute(f"SELECT payload_json FROM {table}"):
        try:
            yield json.loads(payload)
        except ValueError as error:
            raise ValueError(f"The {table} ledger table holds a row that is not valid JSON") from error


def _run_identity(run_dir: Path) -> str:
    """The run id the saved report claims, or the directory's own name."""
    try:
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return run_dir.name
    identity = report.get("run_id") if isinstance(report, dict) else None
    return identity if isinstance(identity, str) and identity else run_dir.name


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build digest.json for one saved cross-reference run.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--percentile", type=float, default=LARGE_PERCENTILE)
    parser.add_argument("--median-multiple", type=int, default=LARGE_MEDIAN_MULTIPLE)
    args = parser.parse_args(argv)
    run_dir = args.run_dir.resolve()
    ledger = run_dir / "ledger.sqlite"
    if not ledger.is_file():
        parser.error(f"No decision ledger at {ledger}")
    try:
        percentile_setting(args.percentile, "--percentile")
        multiple_setting(args.median_multiple, "--median-multiple")
    except ValueError as error:
        parser.error(str(error))
    started = time.monotonic()
    with closing(sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True)) as db:
        sources = _SourceRows(db)
        digest = build_digest(
            _payloads(db, "decisions"), _payloads(db, "links"), _payloads(db, "changes"),
            run_id=_run_identity(run_dir), generated_at=_now(),
            entities=_entity_types(db), source_urls=sources.field("pdf_url"),
            descriptions=sources.receipt_text(),
            percentile=args.percentile, median_multiple=args.median_multiple)
    atomic(run_dir / "digest.json", digest)
    refunds, outliers, classes = digest["refunds"], digest["outliers"], digest["basis"]["classes"]
    print(json.dumps({"run_id": digest["run_id"], "source_records": digest["counts"]["source_records"],
                      "large_thresholds": {name: value["threshold_cents"] for name, value in classes.items()},
                      "class_records": {name: value["records"] for name, value in classes.items()},
                      "linked_refunds": refunds["linked_count"], "linked_refund_amount": refunds["linked_amount"],
                      "unlinked_adjustments": refunds["unlinked_count"], "unlinked_amount": refunds["unlinked_amount"],
                      "non_giving": outliers["non_giving_count"], "non_giving_amount": outliers["non_giving_amount"],
                      "large": outliers["large_count"], "large_amount": outliers["large_amount"],
                      "elapsed_seconds": round(time.monotonic() - started, 2)}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
