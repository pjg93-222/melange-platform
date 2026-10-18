from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
import audit_collection
from collect import refresh_press_stream as press
from collect.build_press_engine_feed import ReceiptRejected, validate_body_completion
from test_press_source_freshness import SOURCES, seal, seal_historical


def pending_proof(root, receipt, rows=()):
    path = root / "press_delta_pending.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["bioguide", "url", "date", "collection_status"])
        writer.writeheader()
        writer.writerows(rows)
    receipt["pending_body_urls_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()


def fake_popen(monkeypatch, child):
    """Replace the Popen the press module launches Rscript with; ``child(command, cwd)`` returns the exit code."""
    class FakePopen:
        pid = 4242

        def __init__(self, command, cwd=None, **kwargs):
            self.command, self.cwd, self.killed = command, cwd, False

        def wait(self, *args, **kwargs):
            assert not args and not kwargs, "the collector wait must stay unbounded"
            return child(self.command, self.cwd)

        def kill(self):
            self.killed = True

    monkeypatch.setattr(press.subprocess, "Popen", FakePopen)
    return FakePopen


def official_coverage_proof(root, receipt):
    coverage_path = root / "press_official_coverage.json"
    coverage = {
        "schema": "official-current-inventory-snapshot-v1", "floor": "2026-06-25",
        "member_count": 1, "current_url_count": 1,
        "members": [{
            "schema": "official-current-inventory-proof-v1", "bioguide": "X000001",
            "source_kind": "fixture", "coverage_mode": "fully_paginated_after_floor",
            "floor": "2026-06-25", "current_count": 1,
            "current_urls": ["https://example.gov/press/current"],
            "current_url_set_sha256": "b" * 64,
            "requests": [{
                "request_index": 1, "role": "inventory_page",
                "endpoint": "https://example.gov/api?page=1",
                "final_url": "https://example.gov/api?page=1",
                "raw_response_sha256": "a" * 64, "item_count": 1,
            }],
            "completeness": {"pagination_complete": True, "date_order_verified": True,
                             "identity_complete": True, "after_floor_query_verified": True},
        }],
    }
    coverage_path.write_text(json.dumps(coverage), encoding="utf-8")
    historical_path = root / "press_historical_maintenance.csv"
    historical_path.write_text(
        "bioguide,url,official_coverage_status,collection_status\n"
        "X000001,https://example.gov/archive,"
        "historical_outside_current_inventory,retained_nonblocking_maintenance\n",
        encoding="utf-8",
    )
    receipt.update(
        schema_version=2, official_coverage_members=1, official_current_urls=1,
        official_coverage_sha256=hashlib.sha256(coverage_path.read_bytes()).hexdigest(),
        historical_maintenance_urls=1,
        historical_maintenance_sha256=hashlib.sha256(historical_path.read_bytes()).hexdigest(),
        historical_maintenance_selected=0, revisit_selected_urls=0,
    )
    return coverage_path, historical_path, coverage


def test_backlog_classification_order_and_resume_against_private_r_fixtures():
    result = subprocess.run([
        press.find_rscript(), str(Path(__file__).with_suffix(".R")),
        str(FEC / "collect/press_backlog.R"),
    ], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout


def test_cross_day_convergence_and_bounded_revisits_against_private_r_fixtures():
    result = subprocess.run([
        press.find_rscript(), str(Path(__file__).with_name("test_press_backlog_revisit.R")),
        str(FEC / "collect/press_backlog.R"),
    ], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout


def test_authoritative_current_inventory_makes_historical_cdx_nonblocking():
    result = subprocess.run([
        press.find_rscript(), str(Path(__file__).with_name("test_press_coverage_backlog.R")),
        str(FEC / "collect/press_official_coverage.R"),
        str(FEC / "collect/press_backlog.R"),
    ], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout


@pytest.mark.parametrize("count,status", [(1, "PARTIAL"), (1, "PASS"), (0, "PARTIAL"),
                                         (False, "PASS"), (-1, "PASS"), ("0", "PASS")])
def test_incomplete_or_invalid_count_cannot_reuse_receipt(tmp_path, count, status):
    seal(tmp_path, SOURCES)
    path = tmp_path / "press_network_retry_receipt.json"
    receipt = json.loads(path.read_text())
    receipt.update(status=status, pending_body_urls=count)
    path.write_text(json.dumps(receipt))
    assert press.current_source_run(tmp_path, SOURCES) is None


@pytest.mark.parametrize("failure", ["nonempty", "wrong_hash", "no_header", "missing"])
def test_zero_claim_must_bind_actual_empty_pending_artifact(tmp_path, failure):
    seal(tmp_path, SOURCES)
    receipt = json.loads((tmp_path / "press_network_retry_receipt.json").read_text())
    pending = tmp_path / "press_delta_pending.csv"
    if failure == "nonempty":
        pending_proof(tmp_path, receipt, [{"bioguide": "X000001", "url": "https://example.gov/pending"}])
    elif failure == "wrong_hash":
        pending.write_text("changed")
    elif failure == "no_header":
        pending.write_text("")
        receipt["pending_body_urls_sha256"] = hashlib.sha256(b"").hexdigest()
    else:
        pending.unlink()
    with pytest.raises(ReceiptRejected, match="PENDING_BODY_PROOF_MISMATCH"):
        validate_body_completion(tmp_path, receipt, require_complete=True)


@pytest.mark.parametrize("failure", [None, "hash", "count"])
def test_cached_date_evidence_is_hash_and_count_bound(tmp_path, failure):
    seal(tmp_path, SOURCES)
    receipt = json.loads((tmp_path / "press_network_retry_receipt.json").read_text())
    path = tmp_path / "press_delta_cached_dates.csv"
    path.write_text("bioguide,url,date_detail,evidence\nX000001,https://example.gov/old,2026-06-01,canonical_url_member_body_checkpoint\n")
    receipt.update(cached_date_rejoins=1, cached_date_rejoins_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    if failure == "hash":
        path.write_text("tampered evidence")
    if failure == "count":
        receipt["cached_date_rejoins"] = 0
    if failure:
        with pytest.raises(ReceiptRejected, match="CACHED_DATE_PROOF_MISMATCH"):
            validate_body_completion(tmp_path, receipt, require_complete=True)
    else:
        validate_body_completion(tmp_path, receipt, require_complete=True)


@pytest.mark.parametrize("failure", [None, "hash", "selected", "attempted", "recovered", "age"])
def test_due_revisits_do_not_block_pass_but_ledger_and_counts_are_bound(tmp_path, failure):
    seal(tmp_path, SOURCES)
    receipt_path = tmp_path / "press_network_retry_receipt.json"
    receipt = json.loads(receipt_path.read_text())
    ledger = tmp_path / "press_body_attempt_ledger.rds"
    ledger.write_bytes(b"immutable RDS fixture with exhaustion evidence")
    receipt.update(body_attempt_ledger_sha256=hashlib.sha256(ledger.read_bytes()).hexdigest(),
                   revisit_min_days=7, revisit_url_cap=100, revisit_due_urls=105,
                   revisit_selected_urls=100, revisit_attempted_urls=2, revisit_recovered_urls=1)
    if failure == "hash":
        ledger.write_bytes(b"changed ledger")
    if failure == "selected":
        receipt["revisit_selected_urls"] = 101
    if failure == "attempted":
        receipt["revisit_attempted_urls"] = 101
    if failure == "recovered":
        receipt["revisit_recovered_urls"] = 3
    if failure == "age":
        receipt["revisit_min_days"] = 6
    receipt_path.write_text(json.dumps(receipt))
    if failure:
        with pytest.raises(ReceiptRejected, match="BODY_ATTEMPT_LEDGER_MISMATCH"):
            validate_body_completion(tmp_path, receipt, require_complete=True)
        assert press.current_source_run(tmp_path, SOURCES) is None
    else:
        validate_body_completion(tmp_path, receipt, require_complete=True)
        assert press.current_source_run(tmp_path, SOURCES) is not None


@pytest.mark.parametrize("failure", [None, "coverage_hash", "request_hash", "pagination",
                                      "history_hash", "history_count", "history_selected"])
def test_official_inventory_and_historical_maintenance_are_hash_bound(tmp_path, failure):
    seal(tmp_path, SOURCES)
    receipt = json.loads((tmp_path / "press_network_retry_receipt.json").read_text())
    coverage_path, historical_path, coverage = official_coverage_proof(tmp_path, receipt)
    if failure == "coverage_hash":
        coverage_path.write_text("{}")
    elif failure in {"request_hash", "pagination"}:
        if failure == "request_hash":
            coverage["members"][0]["requests"][0]["raw_response_sha256"] = "bad"
        else:
            coverage["members"][0]["completeness"]["pagination_complete"] = False
        coverage_path.write_text(json.dumps(coverage))
        receipt["official_coverage_sha256"] = hashlib.sha256(coverage_path.read_bytes()).hexdigest()
    elif failure == "history_hash":
        historical_path.write_text("changed")
    elif failure == "history_count":
        receipt["historical_maintenance_urls"] = 0
    elif failure == "history_selected":
        receipt["historical_maintenance_selected"] = 1
    if failure:
        with pytest.raises(ReceiptRejected, match="OFFICIAL_COVERAGE_PROOF_MISMATCH"):
            validate_body_completion(tmp_path, receipt, require_complete=True)
    else:
        validate_body_completion(tmp_path, receipt, require_complete=True)


@pytest.mark.parametrize("failure", [None, "flag", "source", "member", "first_page",
                                      "nonempty", "filter", "redirect", "skip"])
def test_houlahan_empty_boundary_requires_contiguous_official_inventory(tmp_path, failure):
    seal(tmp_path, SOURCES)
    receipt = json.loads((tmp_path / "press_network_retry_receipt.json").read_text())
    coverage_path, _, coverage = official_coverage_proof(tmp_path, receipt)
    proof = coverage["members"][0]
    proof.update(bioguide="H001085", source_kind="houlahan_official_documentquery",
                 coverage_mode="sequential_dated_listing_terminal_empty")
    proof["completeness"].update(terminal_empty_page_verified=True, boundary_page=2)
    proof["requests"] = []
    for page in (1, 2):
        endpoint = f"https://houlahan.house.gov/press/documentquery.aspx?DocumentTypeID=27&Page={page}"
        proof["requests"].append({"request_index": page, "role": "inventory_page",
                                  "page": page, "item_count": 10 if page == 1 else 0,
                                  "endpoint": endpoint, "final_url": endpoint,
                                  "raw_response_sha256": "a" * 64})
    if failure == "flag":
        proof["completeness"]["terminal_empty_page_verified"] = False
    elif failure == "source":
        proof["source_kind"] = "unsupported_source"
    elif failure == "member":
        proof["bioguide"] = "X000001"
    elif failure == "first_page":
        proof["completeness"]["boundary_page"] = 1
    elif failure == "nonempty":
        proof["requests"][-1]["item_count"] = 10
    elif failure in {"filter", "redirect"}:
        field = "endpoint" if failure == "filter" else "final_url"
        proof["requests"][-1][field] = proof["requests"][-1][field].replace("=27", "=1951")
    elif failure == "skip":
        proof["requests"][-1]["page"] = 3
    coverage_path.write_text(json.dumps(coverage))
    receipt["official_coverage_sha256"] = hashlib.sha256(coverage_path.read_bytes()).hexdigest()
    if failure:
        with pytest.raises(ReceiptRejected, match="OFFICIAL_COVERAGE_PROOF_MISMATCH"):
            validate_body_completion(tmp_path, receipt, require_complete=True)
    else:
        validate_body_completion(tmp_path, receipt, require_complete=True)


def test_partial_run_is_not_reusable_and_next_invocation_advances(tmp_path, monkeypatch):
    seal(tmp_path, SOURCES)
    receipt_path = tmp_path / "press_network_retry_receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt.update(status="PARTIAL", pending_body_urls=2)
    pending_proof(tmp_path, receipt, [{"url": "https://example.gov/a"}, {"url": "https://example.gov/b"}])
    receipt_path.write_text(json.dumps(receipt))
    cache = tmp_path / "saved-body.rds"
    cache.write_bytes(b"verified body retained")
    monkeypatch.setattr(press, "collector_source_hashes", lambda: SOURCES)
    monkeypatch.setattr(press, "find_rscript", lambda: "fixture-rscript")
    attempts = []

    def child(command, cwd):
        attempts.append(command)
        assert cache.read_bytes() == b"verified body retained"
        seal(tmp_path, SOURCES)
        if len(attempts) == 1:
            updated = json.loads(receipt_path.read_text())
            updated.update(status="PARTIAL", pending_body_urls=1)
            pending_proof(tmp_path, updated, [{"url": "https://example.gov/b"}])
            receipt_path.write_text(json.dumps(updated))
            return 3
        return 0

    fake_popen(monkeypatch, child)
    assert press.main(["--run-root", str(tmp_path)]) == 3
    assert json.loads(receipt_path.read_text())["pending_body_urls"] == 1
    assert press.main(["--run-root", str(tmp_path)]) == 0
    assert len(attempts) == 2
    assert press.main(["--run-root", str(tmp_path)]) == 0
    assert len(attempts) == 2


def empty_audit_run(root):
    seal(root, SOURCES)
    for name in ("fulltext", "articles", "host_articles", "article_mentions", "failed_bodies",
                 "dated", "undated", "out_of_range", "scored"):
        (root / f"press_delta_{name}.csv").write_text("url,bioguide,body,date_final,collection_status\n")
    path = root / "press_network_retry_receipt.json"
    receipt = json.loads(path.read_text())
    receipt.update(fulltext_csv_sha256=hashlib.sha256((root / "press_delta_fulltext.csv").read_bytes()).hexdigest())
    for key in ("fulltext_receipts", "usable_article_member_rows", "unique_host_articles", "mention_relationships",
                "failed_bodies", "exhausted_network_body_failures", "rejected_short_mention_slices"):
        receipt[key] = 0
    path.write_text(json.dumps(receipt))
    return receipt


@pytest.mark.parametrize("kind", ["complete", "legacy", "partial", "lying_zero"])
def test_fresh_press_intake_requires_completed_coverage(tmp_path, kind):
    root = tmp_path / "press_refresh_20260913"
    receipt = empty_audit_run(root)
    if kind == "legacy":
        for key in ("status", "pending_body_urls", "pending_body_urls_sha256"):
            del receipt[key]
    elif kind in {"partial", "lying_zero"}:
        pending_proof(root, receipt, [{"bioguide": "X000001", "url": "https://example.gov/pending"}])
        if kind == "partial":
            receipt.update(status="PARTIAL", pending_body_urls=1)
    (root / "press_network_retry_receipt.json").write_text(json.dumps(receipt))
    audited = audit_collection.audit_press_delta(root, "2026-06-25", require_complete=True)
    assert bool(audited["errors"]) == (kind != "complete"), audited
    if kind == "complete":
        assert audited["coverage_complete"] is True and audited["pending_body_urls_carried"] == 0
    if kind == "legacy":
        assert audit_collection.audit_press_delta(root, "2026-06-25")["errors"] == []
    output = tmp_path / "intake.json"
    archive = tmp_path / "accepted"
    code = audit_collection.main([
        "--scope", "press", "--press-delta-root", str(root), "--output", str(output),
        "--accepted-press-receipts-dir", str(archive),
    ])
    assert code == (0 if kind == "complete" else 2)
    assert (archive / (root.name + ".json")).exists() == (kind == "complete")


@pytest.mark.parametrize("fresh", [False, True])
def test_pending_failed_bodies_equal_to_the_sealed_pending_set_are_carried(
        tmp_path, monkeypatch, fresh):
    # Owner ruling 2026-10-15: a pending row is carried to the next run whether or not the
    # deferral ledger has tombstoned it (Paul 2026-09-19); the ledger only reports.
    from collect import press_pending_tombstones as tombstones
    monkeypatch.setattr(tombstones, "DEFAULT_TOMBSTONES", str(tmp_path / "tombstones.json"))
    stuck = {"bioguide": "M000312", "url": "https://mcgovern.house.gov/news/stuck",
             "collection_status": "body_pending_network_attempt"}
    rows = [stuck] + ([{**stuck, "bioguide": "X000001", "url": "https://example.gov/new"}]
                      if fresh else [])
    outputs = tmp_path / "outputs"
    for name in ("press_refresh_20260830", "press_refresh_20260906"):
        prior = outputs / name
        prior.mkdir(parents=True)
        prior_receipt = {"status": "PARTIAL", "pending_body_urls": 1}
        pending_proof(prior, prior_receipt, [{"bioguide": stuck["bioguide"], "url": stuck["url"]}])
        (prior / "press_network_retry_receipt.json").write_text(json.dumps(prior_receipt))
    root = outputs / "press_refresh_20260913"
    receipt = empty_audit_run(root)
    for name in ("fulltext", "failed_bodies"):
        with (root / f"press_delta_{name}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "url", "bioguide", "body", "date_final", "collection_status"])
            writer.writeheader()
            writer.writerows(rows)
    pending_proof(root, receipt, [{"bioguide": row["bioguide"], "url": row["url"]} for row in rows])
    receipt.update(status="PARTIAL", pending_body_urls=len(rows), fulltext_receipts=len(rows),
                   failed_bodies=len(rows), fulltext_csv_sha256=hashlib.sha256(
                       (root / "press_delta_fulltext.csv").read_bytes()).hexdigest())
    (root / "press_network_retry_receipt.json").write_text(json.dumps(receipt))
    audited = audit_collection.audit_press_delta(root, "2026-06-25", require_complete=True)
    assert audited["errors"] == [], audited
    assert audited["pending_failed_bodies_carried"] == len(rows)
    assert audited["pending_body_urls_carried"] == len(rows)
    assert audited["pending_members"] == {bg: 1 for bg in sorted(row["bioguide"] for row in rows)}
    assert audited["coverage_complete"] is False
    # Report-only: the stuck member's URL has been deferred three runs, the new one has not.
    assert audited["pending_body_urls_tombstoned"] == 1


def carried_audit(tmp_path, monkeypatch, sealed, listed, articles=()):
    """Audit a PARTIAL run whose sealed pending artifact holds ``sealed`` while its failed bodies list ``listed``."""
    from collect import press_pending_tombstones as tombstones
    monkeypatch.setattr(tombstones, "DEFAULT_TOMBSTONES", str(tmp_path / "tombstones.json"))
    (tmp_path / "outputs").mkdir()
    root = tmp_path / "outputs" / "press_refresh_20260913"
    receipt = empty_audit_run(root)
    pending = [{"url": url, "bioguide": bg, "body": "", "date_final": "",
                "collection_status": "body_pending_network_attempt"} for bg, url in listed]
    usable = [{"url": url, "bioguide": bg, "body": " ".join(["word"] * 25), "date_final": "",
               "collection_status": ""} for bg, url in articles]
    for name, rows in (("failed_bodies", pending), ("articles", usable), ("fulltext", pending + usable)):
        with (root / f"press_delta_{name}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "url", "bioguide", "body", "date_final", "collection_status"])
            writer.writeheader()
            writer.writerows(rows)
    pending_proof(root, receipt, [{"bioguide": bg, "url": url} for bg, url in sealed])
    receipt.update(status="PARTIAL", pending_body_urls=len(sealed), failed_bodies=len(pending),
                   fulltext_receipts=len(pending + usable), usable_article_member_rows=len(usable),
                   fulltext_csv_sha256=hashlib.sha256(
                       (root / "press_delta_fulltext.csv").read_bytes()).hexdigest())
    (root / "press_network_retry_receipt.json").write_text(json.dumps(receipt))
    return audit_collection.audit_press_delta(root, "2026-06-25", require_complete=True)


A_URL, B_URL = ("X000001", "https://example.gov/a"), ("X000002", "https://example.gov/b")


def test_pending_failed_bodies_that_equal_the_sealed_set_in_any_order_are_accepted(tmp_path, monkeypatch):
    audited = carried_audit(tmp_path, monkeypatch, sealed=[A_URL, B_URL], listed=[B_URL, A_URL])
    assert audited["errors"] == [], audited
    assert audited["pending_failed_bodies_carried"] == 2
    assert audited["pending_members"] == {"X000001": 1, "X000002": 1}


@pytest.mark.parametrize("sealed,listed", [
    ([A_URL], [A_URL, B_URL]),       # an extra pending row nobody sealed
    ([A_URL, B_URL], [A_URL]),       # a sealed row missing from the failed bodies
    ([A_URL], [B_URL]),              # the same count, a different row
])
def test_pending_failed_bodies_that_differ_from_the_sealed_set_are_refused(
        tmp_path, monkeypatch, sealed, listed):
    audited = carried_audit(tmp_path, monkeypatch, sealed=sealed, listed=listed)
    assert any("do not equal the sealed pending set" in error for error in audited["errors"]), audited
    assert audited["pending_failed_bodies_carried"] == 0


def test_a_pending_body_that_is_also_a_usable_article_is_refused(tmp_path, monkeypatch):
    audited = carried_audit(tmp_path, monkeypatch, sealed=[A_URL], listed=[A_URL], articles=[A_URL])
    assert any("also usable articles" in error for error in audited["errors"]), audited
    assert audited["pending_failed_bodies_carried"] == 0


def excused_partial_receipt(root):
    """The shape valid_run hands back for a PARTIAL run whose pending rows the ledger excused."""
    seal(root, SOURCES)
    receipt = json.loads((root / "press_network_retry_receipt.json").read_text())
    receipt.update(status="PARTIAL", pending_body_urls=2)
    return receipt


def test_excused_partial_receipt_is_reused_without_a_new_walk(tmp_path, monkeypatch, capsys):
    receipt = excused_partial_receipt(tmp_path)
    monkeypatch.setattr(press, "valid_run", lambda path, *a, **k: ("completed", receipt))
    assert press.current_source_run(tmp_path, SOURCES) == ("completed", receipt)
    assert "not repeating the same-day walk" in capsys.readouterr().out
    monkeypatch.setattr(press, "collector_source_hashes", lambda: SOURCES)
    fake_popen(monkeypatch, lambda command, cwd: pytest.fail("collector launched"))
    assert press.main(["--run-root", str(tmp_path)]) == 0


@pytest.mark.parametrize("change", [
    {"collector_sources_sha256": {**SOURCES, "press_backlog.R": "old"}},
    {"pending_body_urls": 0}, {"pending_body_urls": True}, {"pending_body_urls": "2"},
    {"status": "FAILED"}, {"status": None},
])
def test_only_pass_with_nothing_pending_or_an_excused_partial_is_reused(tmp_path, monkeypatch, change):
    receipt = {**excused_partial_receipt(tmp_path), **change}
    monkeypatch.setattr(press, "valid_run", lambda path, *a, **k: ("completed", receipt))
    assert press.current_source_run(tmp_path, SOURCES) is None


def test_partial_exit_cannot_certify_a_collector_source_changed_mid_walk(tmp_path, monkeypatch):
    seal(tmp_path)
    generations = iter([SOURCES, {**SOURCES, "refresh_press_delta.R": "changed"}])
    monkeypatch.setattr(press, "collector_source_hashes", lambda: next(generations))
    monkeypatch.setattr(press, "find_rscript", lambda: "fixture-rscript")
    fake_popen(monkeypatch, lambda command, cwd: 3)
    with pytest.raises(RuntimeError, match="source"):
        press.main(["--run-root", str(tmp_path)])


def test_other_nonzero_exit_returns_unchanged_without_the_drift_check(tmp_path, monkeypatch):
    seal(tmp_path)
    generations = iter([SOURCES])     # a second hash read would raise StopIteration
    monkeypatch.setattr(press, "collector_source_hashes", lambda: next(generations))
    monkeypatch.setattr(press, "find_rscript", lambda: "fixture-rscript")
    fake_popen(monkeypatch, lambda command, cwd: 7)
    assert press.main(["--run-root", str(tmp_path)]) == 7


def test_tombstone_ledger_is_not_touched_for_a_run_root_outside_live_outputs(tmp_path, monkeypatch):
    from collect import press_pending_tombstones as tombstones
    monkeypatch.setattr(tombstones, "update", lambda *a, **k: pytest.fail("ledger update called"))
    monkeypatch.setattr(press, "collector_source_hashes", lambda: SOURCES)
    monkeypatch.setattr(press, "find_rscript", lambda: "fixture-rscript")

    def child(command, cwd):
        seal(tmp_path, SOURCES)
        return 0

    fake_popen(monkeypatch, child)
    assert press.main(["--run-root", str(tmp_path)]) == 0


@pytest.mark.parametrize("failure", [None, "boom"])
def test_tombstone_ledger_updates_once_after_a_live_walk_and_failure_is_non_fatal(
        tmp_path, monkeypatch, capsys, failure):
    from collect import press_pending_tombstones as tombstones
    (tmp_path / "outputs").mkdir()
    run_root = tmp_path / "outputs" / "press_refresh_20261013"
    calls = []

    def update(*args, **kwargs):
        calls.append((args, kwargs))
        if failure:
            raise RuntimeError(failure)
        return {"runs_seen": ["a", "b"], "records": {}, "members": {}}

    monkeypatch.setattr(press, "ROOT", str(tmp_path))
    monkeypatch.setattr(tombstones, "update", update)
    monkeypatch.setattr(press, "collector_source_hashes", lambda: SOURCES)
    monkeypatch.setattr(press, "find_rscript", lambda: "fixture-rscript")

    def child(command, cwd):
        seal(run_root, SOURCES)
        return 0

    fake_popen(monkeypatch, child)
    assert press.main(["--run-root", str(run_root)]) == 0
    assert len(calls) == 1
    out = capsys.readouterr().out
    if failure:
        assert "press tombstone ledger update skipped (non-fatal): RuntimeError: boom" in out
    else:
        assert "press tombstone ledger updated: 2 sealed run(s) counted, 0 URL(s) and 0 member(s)" in out


def test_ledger_is_not_updated_for_a_targeted_historical_recovery(tmp_path, monkeypatch):
    from collect import press_pending_tombstones as tombstones
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    source, target = outputs / "press_refresh_20261012", outputs / "press_refresh_20261013"
    seal(source, SOURCES)
    monkeypatch.setattr(press, "ROOT", str(tmp_path))
    monkeypatch.setattr(tombstones, "update", lambda *a, **k: pytest.fail("ledger update called"))
    monkeypatch.setattr(press, "collector_source_hashes", lambda: SOURCES)
    monkeypatch.setattr(press, "find_rscript", lambda: "fixture-rscript")

    def child(command, cwd):
        seal_historical(target, source)
        return 0

    fake_popen(monkeypatch, child)
    assert press.main(["--run-root", str(target), "--retry-from", str(source)]) == 0


def test_interrupt_kills_the_collector_process_tree_and_reraises(monkeypatch):
    taskkills = []

    def interrupted(command, cwd):
        raise KeyboardInterrupt

    popen = fake_popen(monkeypatch, interrupted)
    popen.kills = []
    popen.kill = lambda self: self.kills.append(self.pid)
    monkeypatch.setattr(press.subprocess, "run",
                        lambda *args, **kwargs: taskkills.append((args, kwargs)))
    with pytest.raises(KeyboardInterrupt):
        press.run_collector(["fixture-rscript"], ".")
    if press.os.name == "nt":
        assert [call[0][0] for call in taskkills] == [["taskkill", "/PID", "4242", "/T", "/F"]]
        assert taskkills[0][1]["timeout"] == 60
    else:
        assert popen.kills == [4242] and not taskkills


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", "null", '"text"'])
def test_corrupt_or_non_object_existing_receipt_is_refused_with_a_clear_message(
        tmp_path, monkeypatch, content):
    source, target = tmp_path / "prior", tmp_path / "repair"
    seal(source, SOURCES)
    target.mkdir()
    (target / "press_network_retry_receipt.json").write_text(content)
    monkeypatch.setattr(press, "collector_source_hashes", lambda: SOURCES)
    fake_popen(monkeypatch, lambda command, cwd: pytest.fail("collector launched"))
    with pytest.raises(ValueError, match="refusing historical body recovery: existing receipt"):
        press.main(["--run-root", str(target), "--retry-from", str(source)])
    assert (target / "press_network_retry_receipt.json").read_text() == content


@pytest.mark.parametrize("content", [[], "text",
                                     {"historical_inventory_source": "oops",
                                      "mode": "historical_body_recovery"},
                                     {"historical_inventory_source": ["x"]}])
def test_non_object_receipt_or_inventory_source_is_not_a_historical_recovery(tmp_path, content):
    source, target = tmp_path / "prior", tmp_path / "repair"
    seal(source, SOURCES)
    seal(target, SOURCES)
    (target / "press_network_retry_receipt.json").write_text(json.dumps(content))
    assert press.historical_recovery_run(target, SOURCES, source) is None
