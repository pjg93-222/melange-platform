# -*- coding: utf-8 -*-
"""audit_collection.audit_press_delta: which scored rows the intake accepts (owner ruling D-219, 2026-10-15).

The refresh scorer (refresh_press_delta.R score_scorable_press_rows) scores the dated articles, the UNDATED
articles, and an exhausted body failure on its title (``body_fallback = "title"``).  The intake audit used to
demand ``scored`` be a subset of ``dated``, which rejects every such run.  It now accepts scored rows in
dated + undated + the title-tagged exhausted failures, and still refuses a row in no partition, a pending row
and a mention slice.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import sys

import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
import audit_collection
from test_press_backlog import pending_proof
from test_press_source_freshness import SOURCES, seal

FIELDS = ["url", "bioguide", "title", "body", "source", "date_final", "collection_status", "body_fallback"]
BODY = " ".join(["word"] * 25)
EXHAUSTED = "body_shell_under_20_words_after_network_retry"
PENDING = "body_pending_network_attempt"
MENTION = "mention_slice_under_20_words"
STRAY_MESSAGE = "are not dated or undated articles or exhausted title-fallback failures"


def row(name, bioguide="X000001", **fields):
    return {"url": "https://example.gov/" + name, "bioguide": bioguide, "title": "Title " + name, "body": "",
            "source": "", "date_final": "", "collection_status": "", "body_fallback": "", **fields}


def dated(name, **fields):
    return row(name, body=BODY, date_final="2026-07-01", collection_status="usable_article", **fields)


def undated(name, **fields):
    return row(name, body=BODY, collection_status="usable_article", **fields)


def failed(name, status=EXHAUSTED, **fields):
    return row(name, collection_status=status, **fields)


def audit(tmp_path, monkeypatch, *, dated_rows=(), undated_rows=(), failed_rows=(), scored_rows=(),
          scored_fields=FIELDS):
    from collect import press_pending_tombstones as tombstones
    monkeypatch.setattr(tombstones, "DEFAULT_TOMBSTONES", str(tmp_path / "tombstones.json"))
    (tmp_path / "outputs").mkdir()
    root = tmp_path / "outputs" / "press_refresh_20260913"
    seal(root, SOURCES)
    articles = list(dated_rows) + list(undated_rows)
    pending = [r for r in failed_rows if r["collection_status"] == PENDING]
    frames = {"fulltext": articles + list(failed_rows), "articles": articles, "host_articles": articles,
              "article_mentions": [], "failed_bodies": list(failed_rows), "dated": list(dated_rows),
              "undated": list(undated_rows), "out_of_range": [], "scored": list(scored_rows)}
    for name, rows in frames.items():
        fields = scored_fields if name == "scored" else FIELDS
        with (root / ("press_delta_%s.csv" % name)).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    path = root / "press_network_retry_receipt.json"
    receipt = json.loads(path.read_text())
    pending_proof(root, receipt, [{"bioguide": r["bioguide"], "url": r["url"]} for r in pending])
    receipt.update(
        fulltext_csv_sha256=hashlib.sha256((root / "press_delta_fulltext.csv").read_bytes()).hexdigest(),
        fulltext_receipts=len(frames["fulltext"]), usable_article_member_rows=len(articles),
        unique_host_articles=len({r["url"] for r in articles}), mention_relationships=0,
        failed_bodies=len(failed_rows),
        exhausted_network_body_failures=sum(r["collection_status"].endswith("after_network_retry") for r in failed_rows),
        rejected_short_mention_slices=sum(r["collection_status"] == MENTION for r in failed_rows))
    if pending:
        receipt.update(status="PARTIAL", pending_body_urls=len(pending))
    path.write_text(json.dumps(receipt))
    return audit_collection.audit_press_delta(root, "2026-06-25", require_complete=True)


def tagged(base, tag="title"):
    return {**base, "body_fallback": tag}


def test_dated_only_scoring_is_still_accepted(tmp_path, monkeypatch):
    first = dated("a")
    audited = audit(tmp_path, monkeypatch, dated_rows=[first], scored_rows=[first])
    assert audited["errors"] == [], audited


def test_a_legacy_scored_artifact_without_the_tag_column_is_still_accepted(tmp_path, monkeypatch):
    first = dated("a")
    audited = audit(tmp_path, monkeypatch, dated_rows=[first], scored_rows=[first],
                    scored_fields=[name for name in FIELDS if name != "body_fallback"])
    assert audited["errors"] == [], audited


def test_scored_undated_and_exhausted_title_fallback_rows_are_accepted(tmp_path, monkeypatch):
    first, second = dated("a"), undated("b", bioguide="X000002")
    shell = failed("c", bioguide="X000003")
    audited = audit(tmp_path, monkeypatch, dated_rows=[first], undated_rows=[second], failed_rows=[shell],
                    scored_rows=[first, second, tagged(shell)])
    assert audited["errors"] == [], audited
    assert (audited["dated"], audited["undated"], audited["scored"]) == (1, 1, 3)
    assert audited["exhausted_network_body_failures"] == 1


def test_scored_undated_rows_alone_are_accepted(tmp_path, monkeypatch):
    only = undated("b")
    audited = audit(tmp_path, monkeypatch, undated_rows=[only], scored_rows=[only])
    assert audited["errors"] == [], audited


def test_a_carried_pending_row_that_is_not_scored_leaves_the_run_accepted(tmp_path, monkeypatch):
    first, held = dated("a"), failed("p", PENDING, bioguide="X000009")
    audited = audit(tmp_path, monkeypatch, dated_rows=[first], failed_rows=[held], scored_rows=[first])
    assert audited["errors"] == [], audited
    assert audited["pending_failed_bodies_carried"] == 1 and audited["coverage_complete"] is False


def test_a_scored_row_in_no_partition_is_refused(tmp_path, monkeypatch):
    first, ghost = dated("a"), row("ghost", bioguide="X000004")
    audited = audit(tmp_path, monkeypatch, dated_rows=[first], scored_rows=[first, ghost])
    assert any(STRAY_MESSAGE in error and error.startswith("1 scored URL") for error in audited["errors"]), audited


def test_a_title_tag_cannot_launder_a_row_that_is_in_no_partition(tmp_path, monkeypatch):
    first, ghost = dated("a"), tagged(row("ghost", bioguide="X000004"))
    audited = audit(tmp_path, monkeypatch, dated_rows=[first], scored_rows=[first, ghost])
    assert any(STRAY_MESSAGE in error for error in audited["errors"]), audited


def test_a_scored_pending_row_is_refused_even_when_tagged_as_a_title_fallback(tmp_path, monkeypatch):
    first, held = dated("a"), failed("p", PENDING, bioguide="X000009")
    audited = audit(tmp_path, monkeypatch, dated_rows=[first], failed_rows=[held],
                    scored_rows=[first, tagged(held)])
    assert any(STRAY_MESSAGE in error and "(1 of them pending)" in error for error in audited["errors"]), audited


def test_a_scored_mention_slice_is_refused(tmp_path, monkeypatch):
    first = dated("a")
    slice_ = failed("m", MENTION, bioguide="X000005", source="OFFICIAL_MENTION")
    audited = audit(tmp_path, monkeypatch, dated_rows=[first], failed_rows=[slice_],
                    scored_rows=[first, tagged(slice_)])
    assert any(STRAY_MESSAGE in error for error in audited["errors"]), audited
    assert any("mention relationships leaked" in error for error in audited["errors"]), audited


def test_a_scored_mention_slice_with_the_source_column_dropped_is_still_refused(tmp_path, monkeypatch):
    first = dated("a")
    slice_ = failed("m", MENTION, bioguide="X000005", source="OFFICIAL_MENTION")
    sourceless = {**tagged(slice_), "source": ""}
    audited = audit(tmp_path, monkeypatch, dated_rows=[first], failed_rows=[slice_],
                    scored_rows=[first, sourceless])
    assert any(STRAY_MESSAGE in error for error in audited["errors"]), audited


def test_an_exhausted_failure_scored_without_the_title_tag_is_refused(tmp_path, monkeypatch):
    first, shell = dated("a"), failed("c", bioguide="X000003")
    audited = audit(tmp_path, monkeypatch, dated_rows=[first], failed_rows=[shell], scored_rows=[first, shell])
    assert any(STRAY_MESSAGE in error for error in audited["errors"]), audited


@pytest.mark.parametrize("status", ["body_unavailable_from_existing_receipt", "body_unavailable_"])
def test_a_title_tagged_failure_that_was_not_exhausted_by_the_network_walk_is_refused(tmp_path, monkeypatch, status):
    first, shell = dated("a"), failed("c", status, bioguide="X000003")
    audited = audit(tmp_path, monkeypatch, dated_rows=[first], failed_rows=[shell],
                    scored_rows=[first, tagged(shell)])
    assert any(STRAY_MESSAGE in error for error in audited["errors"]), audited


def test_the_title_tag_must_belong_to_the_same_member_turn(tmp_path, monkeypatch):
    # The same URL failing for one member does not authorize scoring it for another member.
    first, shell = dated("a"), failed("c", bioguide="X000003")
    other_member = tagged({**shell, "bioguide": "X000007"})
    audited = audit(tmp_path, monkeypatch, dated_rows=[first], failed_rows=[shell],
                    scored_rows=[first, other_member])
    assert any(STRAY_MESSAGE in error for error in audited["errors"]), audited
