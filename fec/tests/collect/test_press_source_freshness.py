from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from collect import build_press_engine_feed as feed
from collect import refresh_press_stream as press
from collect.build_press_engine_feed import REQUIRED_RUN_FILES


SOURCES = {name: str(index) * 64 for index, name in enumerate((
    "refresh_press_delta.R", "press_member_overrides.R", "press_official_coverage.R",
    "press_release_date_precedence.R", "press_wordpress_custom_posts.R",
    "press_official_dated_listings.R",
    "press_request_deadlines.R",
    "press_backlog.R", "cu_press_release_scraper.R"), 1)}


def fake_popen(monkeypatch, child):
    """Replace the Popen the press module launches Rscript with; ``child(command, cwd)`` returns the exit code."""
    class FakePopen:
        pid = 4242

        def __init__(self, command, cwd=None, **kwargs):
            self.command, self.cwd = command, cwd

        def wait(self, *args, **kwargs):
            assert not args and not kwargs, "the collector wait must stay unbounded"
            return child(self.command, self.cwd)

        def kill(self):
            pass

    monkeypatch.setattr(press.subprocess, "Popen", FakePopen)
    return FakePopen


def seal(path, sources=None):
    path.mkdir(exist_ok=True)
    for name in REQUIRED_RUN_FILES[1:]:
        (path / name).write_bytes(b"retained fixture content")
    receipt = {
        "mode": "live_network_retry", "completed_at": datetime.now(timezone.utc).isoformat(),
        "fulltext_csv_sha256": hashlib.sha256((path / "press_delta_fulltext.csv").read_bytes()).hexdigest(),
        "fulltext_rds_sha256": hashlib.sha256((path / "press_delta_fulltext.rds").read_bytes()).hexdigest(),
    }
    pending = path / "press_delta_pending.csv"
    pending.write_text("bioguide,url,date,collection_status\n")
    receipt.update(status="PASS", pending_body_urls=0,
                   pending_body_urls_sha256=hashlib.sha256(pending.read_bytes()).hexdigest())
    if sources is not None:
        receipt["collector_sources_sha256"] = sources
    (path / REQUIRED_RUN_FILES[0]).write_text(json.dumps(receipt))


@pytest.mark.parametrize("sources", [None, {}, {**SOURCES, "press_member_overrides.R": "old"}])
def test_legacy_or_different_source_receipt_is_not_reusable(tmp_path, sources):
    seal(tmp_path, sources)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert press.valid_run(tmp_path) is not None
    assert press.current_source_run(tmp_path, SOURCES) is None
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_current_source_receipt_retains_hash_validation(tmp_path):
    seal(tmp_path, SOURCES)
    assert press.current_source_run(tmp_path, SOURCES) is not None
    (tmp_path / "press_delta_fulltext.csv").write_bytes(b"unbound edit")
    assert press.current_source_run(tmp_path, SOURCES) is None


def test_matching_receipt_does_not_launch_collector(tmp_path, monkeypatch):
    seal(tmp_path, SOURCES)
    monkeypatch.setattr(press, "collector_source_hashes", lambda: SOURCES)
    fake_popen(monkeypatch, lambda *a: pytest.fail("collector launched"))
    assert press.main(["--run-root", str(tmp_path)]) == 0


def test_old_receipt_retries_with_all_caches_retained(tmp_path, monkeypatch):
    seal(tmp_path)
    cache = tmp_path / "pr_checkpoints/fulltext/member.rds"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"successful body cache")
    monkeypatch.setattr(press, "collector_source_hashes", lambda: SOURCES)
    monkeypatch.setattr(press, "find_rscript", lambda: "fixture-rscript")
    calls = []

    def child(command, cwd):
        calls.append(command)
        assert cache.read_bytes() == b"successful body cache"
        assert command[2] == str(tmp_path)
        seal(tmp_path, SOURCES)
        return 0

    fake_popen(monkeypatch, child)
    assert press.main(["--run-root", str(tmp_path)]) == 0
    assert len(calls) == 1 and cache.read_bytes() == b"successful body cache"


@pytest.mark.parametrize("failure", ["old_receipt", "changed_source", "changed_dated_adapter",
                                      "changed_coverage_contract"])
def test_child_exit_zero_cannot_certify_stale_source_receipt(tmp_path, monkeypatch, failure):
    seal(tmp_path)
    changed = {"changed_source": "refresh_press_delta.R",
               "changed_dated_adapter": "press_official_dated_listings.R",
               "changed_coverage_contract": "press_official_coverage.R"}.get(failure)
    generations = iter([SOURCES, {**SOURCES, changed: "changed"}])
    monkeypatch.setattr(press, "collector_source_hashes",
                        lambda: next(generations) if changed else SOURCES)
    monkeypatch.setattr(press, "find_rscript", lambda: "fixture-rscript")
    fake_popen(monkeypatch, lambda *a: 0)
    with pytest.raises(RuntimeError, match="source"):
        press.main(["--run-root", str(tmp_path)])


def test_source_manifest_binds_each_actual_file(tmp_path, monkeypatch):
    root = tmp_path / "Melange Systems"
    code = root / "fec/collect"
    code.mkdir(parents=True)
    upstream = tmp_path / "Draft Systems/cu_press_release_scraper.R"
    upstream.parent.mkdir()
    files = [code / "refresh_press_delta.R", code / "press_member_overrides.R",
             code / "press_official_coverage.R", code / "press_release_date_precedence.R",
             code / "press_wordpress_custom_posts.R",
             code / "press_official_dated_listings.R",
             code / "press_request_deadlines.R", code / "press_backlog.R", upstream]
    for path in files:
        path.write_text(path.name)
    monkeypatch.setattr(press, "ROOT", str(root))
    monkeypatch.setattr(press, "__file__", str(code / "refresh_press_stream.py"))
    original = press.collector_source_hashes()
    assert set(original) == set(SOURCES)
    for path in files:
        before = press.collector_source_hashes()
        path.write_text(path.name + " edited")
        after = press.collector_source_hashes()
        assert [key for key in before if before[key] != after[key]] == [path.name]


def test_override_functions_and_upstream_retry_against_private_r_fixtures():
    rscript = Path(press.find_rscript())
    upstream = Path(press.ROOT).parent / "Draft Systems/cu_press_release_scraper.R"
    result = subprocess.run([
        str(rscript), str(Path(__file__).with_name("test_press_member_overrides.R")),
        str(FEC / "collect/press_member_overrides.R"), str(upstream),
        str(FEC / "collect/refresh_press_delta.R"),
    ], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS:" in result.stdout


def seal_historical(target, source):
    seal(target, SOURCES)
    path = target / REQUIRED_RUN_FILES[0]
    receipt = json.loads(path.read_text())
    receipt.update(mode="historical_body_recovery", inventory_freshness="historical",
                   retry_source_run=source.name,
                   historical_inventory_source={
                       "run_root": str(source.resolve()),
                       "receipt_sha256": hashlib.sha256((source / REQUIRED_RUN_FILES[0]).read_bytes()).hexdigest(),
                       "fulltext_rds_sha256": hashlib.sha256((source / "press_delta_fulltext.rds").read_bytes()).hexdigest(),
                   })
    path.write_text(json.dumps(receipt))


def test_historical_recovery_never_qualifies_as_fresh_same_day_coverage(tmp_path):
    source, target = tmp_path / "prior", tmp_path / "today"
    seal(source, SOURCES)
    seal_historical(target, source)
    assert press.historical_recovery_run(target, SOURCES, source) is not None
    assert press.valid_run(target) is None
    assert press.current_source_run(target, SOURCES) is None
    (source / "press_delta_fulltext.rds").write_bytes(b"changed old source")
    assert press.historical_recovery_run(target, SOURCES, source) is None


def test_explicit_targeted_recovery_seals_only_historical_receipt(tmp_path, monkeypatch):
    source, target = tmp_path / "prior", tmp_path / "repair"
    seal(source, SOURCES)
    monkeypatch.setattr(press, "collector_source_hashes", lambda: SOURCES)
    monkeypatch.setattr(press, "find_rscript", lambda: "fixture-rscript")
    calls = []

    def child(command, cwd):
        calls.append(command)
        assert command[-2:] == ["--retry-from", str(source.resolve())]
        seal_historical(target, source)
        return 0

    fake_popen(monkeypatch, child)
    arguments = ["--run-root", str(target), "--retry-from", str(source)]
    assert press.main(arguments) == 0
    assert press.main(arguments) == 0
    assert len(calls) == 1
    assert press.current_source_run(target, SOURCES) is None


@pytest.mark.parametrize("same_path", [False, True])
def test_targeted_recovery_cannot_overwrite_source_or_fresh_inventory(tmp_path, monkeypatch, same_path):
    source, target = tmp_path / "prior", tmp_path / "fresh"
    seal(source, SOURCES)
    if same_path:
        target = source
    else:
        seal(target, SOURCES)
    before = (target / REQUIRED_RUN_FILES[0]).read_bytes()
    monkeypatch.setattr(press, "collector_source_hashes", lambda: SOURCES)
    fake_popen(monkeypatch, lambda *a: pytest.fail("collector launched"))
    with pytest.raises(ValueError, match="separate target|fresh-inventory"):
        press.main(["--run-root", str(target), "--retry-from", str(source)])
    assert (target / REQUIRED_RUN_FILES[0]).read_bytes() == before


# --- build_press_engine_feed: one malformed receipt/JSON shape must be a named refusal ---------

def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _coverage(completeness):
    return {"schema": "official-current-inventory-snapshot-v1", "member_count": 1,
            "current_url_count": 0, "members": [{
                "schema": "official-current-inventory-proof-v1", "bioguide": "A000001",
                "coverage_mode": "fully_paginated_after_floor", "current_urls": [], "current_count": 0,
                "requests": [{"request_index": 1, "item_count": 0, "endpoint": "https://example.test/list",
                              "raw_response_sha256": "a" * 64}],
                "current_url_set_sha256": "b" * 64, "completeness": completeness}]}


GOOD_COMPLETENESS = {"pagination_complete": True, "date_order_verified": True,
                     "identity_complete": True, "after_floor_query_verified": True}


def seal_v2(path, coverage):
    """A PASS/pending=0 run whose receipt claims schema 2; ``coverage`` is the snapshot JSON document."""
    seal(path)
    coverage_path = path / "press_official_coverage.json"
    coverage_path.write_text(json.dumps(coverage))
    historical = path / "press_historical_maintenance.csv"
    historical.write_text("bioguide,url,official_coverage_status,collection_status\n")
    receipt_path = path / REQUIRED_RUN_FILES[0]
    receipt = json.loads(receipt_path.read_text())
    receipt.update(schema_version=2, official_coverage_sha256=_sha(coverage_path),
                   official_coverage_members=1, official_current_urls=0,
                   historical_maintenance_sha256=_sha(historical), historical_maintenance_urls=0,
                   historical_maintenance_selected=0, revisit_selected_urls=0)
    receipt_path.write_text(json.dumps(receipt))
    return receipt


def test_well_formed_schema_2_receipt_is_still_accepted(tmp_path):
    receipt = seal_v2(tmp_path, _coverage(GOOD_COMPLETENESS))
    assert feed.validate_body_completion(tmp_path, receipt) == {}


def test_coverage_snapshot_that_is_a_list_is_a_named_refusal(tmp_path):
    receipt = seal_v2(tmp_path, [])
    with pytest.raises(feed.ReceiptRejected) as caught:
        feed.validate_body_completion(tmp_path, receipt)
    assert caught.value.reason == "OFFICIAL_COVERAGE_PROOF_MISMATCH"


@pytest.mark.parametrize("completeness", ["yes", ["pagination_complete"], 7])
def test_truthy_non_object_completeness_is_a_named_refusal(tmp_path, completeness):
    receipt = seal_v2(tmp_path, _coverage(completeness))
    with pytest.raises(feed.ReceiptRejected) as caught:
        feed.validate_body_completion(tmp_path, receipt)
    assert caught.value.reason == "OFFICIAL_COVERAGE_PROOF_MISMATCH"
    assert "completeness" in caught.value.detail


@pytest.mark.parametrize("version", ["2", None, True, 2.0])
def test_non_int_schema_version_is_a_named_refusal(tmp_path, version):
    seal(tmp_path)
    receipt = json.loads((tmp_path / REQUIRED_RUN_FILES[0]).read_text())
    receipt["schema_version"] = version
    with pytest.raises(feed.ReceiptRejected) as caught:
        feed.validate_body_completion(tmp_path, receipt)
    assert caught.value.reason == "RECEIPT_SCHEMA_VERSION_INVALID"


@pytest.mark.parametrize("version", [0, 1])
def test_old_int_schema_versions_are_still_accepted(tmp_path, version):
    seal(tmp_path)
    receipt = json.loads((tmp_path / REQUIRED_RUN_FILES[0]).read_text())
    receipt["schema_version"] = version
    assert feed.validate_body_completion(tmp_path, receipt) == {}


def test_one_malformed_run_does_not_kill_discovery(tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    good, bad = outputs / "press_refresh_20990101", outputs / "press_refresh_20990102"
    seal(good)
    seal_v2(bad, [])  # newer, so it would win if it were accepted
    path, receipt = feed.discover_run(outputs)
    assert path == good and receipt["status"] == "PASS"


def _feed_run(tmp_path):
    """A sealed run with one scored host article."""
    run = tmp_path / "press_refresh_20990101"
    seal(run)
    (run / "press_delta_scored.csv").write_text(
        "member,bioguide,url,title,polarity,score_graded\nNeutral A,N000001,https://example.test/a,T,0,0\n",
        encoding="utf-8")
    (run / "press_delta_host_articles.csv").write_text(
        "member,bioguide,url,title,body\nNeutral A,N000001,https://example.test/a,T,%s\n"
        % " ".join(["word"] * 25), encoding="utf-8")
    (run / "press_delta_article_mentions.csv").write_text(
        "member,bioguide,url,title,body,date_final\n", encoding="utf-8")
    return run


def _intake(run, **override):
    """The accepted gate document that hash-binds ``run``; ``override`` replaces top-level keys."""
    names = ("press_delta_scored.csv", "press_delta_host_articles.csv", "press_delta_article_mentions.csv")
    document = {"schema": "collection-intake-receipt-v1", "status": "ACCEPTED",
                "inputs": {"press_delta_root": str(run),
                           "press_retry_receipt_sha256": _sha(run / REQUIRED_RUN_FILES[0]),
                           "press_source_sha256": {name: _sha(run / name) for name in names}},
                "press_delta": {"live_network_retry_receipt_valid": True}}
    document.update(override)
    return document


def _build_paths(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    return [out / name for name in ("tone.csv", "fulltext.jsonl", "mentions.jsonl", "receipt.json")]


@pytest.mark.parametrize("shape,message", [
    ("intake_list", "accepted gate receipt"),
    ("inputs_list", "audited press delta root"),
    ("press_delta_list", "did not accept the press retry receipt"),
    ("retry_list", "did not accept the press retry receipt"),
])
def test_build_refuses_non_object_json_with_runtime_error(tmp_path, shape, message):
    run = _feed_run(tmp_path)
    if shape == "retry_list":
        (run / REQUIRED_RUN_FILES[0]).write_text("[]")
    if shape == "intake_list":
        document = ["x"]
    else:
        document = _intake(run, **{"inputs_list": {"inputs": ["x"]},
                                   "press_delta_list": {"press_delta": ["x"]}}.get(shape, {}))
    intake = tmp_path / "intake.json"
    intake.write_text(json.dumps(document))
    with pytest.raises(RuntimeError, match=message):
        feed.build(run, *_build_paths(tmp_path), intake)


def test_accepted_ledger_refuses_non_object_documents_with_runtime_error(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "press_refresh_20990101.json").write_text("[]")
    with pytest.raises(RuntimeError, match="not accepted"):
        feed._accepted_intake_receipts(tmp_path / "current.json", archive)
    run = _feed_run(tmp_path)
    (run / REQUIRED_RUN_FILES[0]).write_text("[]")
    current = tmp_path / "current.json"
    current.write_text(json.dumps(_intake(run)))
    with pytest.raises(RuntimeError, match="completion receipt"):
        feed._accepted_intake_receipts(current, tmp_path / "no-archive")


def test_build_publishes_all_files_with_hashes_of_the_published_bytes(tmp_path):
    run = _feed_run(tmp_path)
    intake = tmp_path / "intake.json"
    intake.write_text(json.dumps(_intake(run)))
    tone, fulltext, mentions, receipt_path = paths = _build_paths(tmp_path)
    receipt = feed.build(run, tone, fulltext, mentions, receipt_path, intake)
    assert receipt["counts"]["tone_rows"] == 1 and receipt["counts"]["fulltext_rows"] == 1
    assert receipt["outputs_sha256"] == {
        feed._rel(tone): _sha(tone), feed._rel(fulltext): _sha(fulltext), feed._rel(mentions): _sha(mentions)}
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == receipt
    assert sorted(p.name for p in paths[0].parent.iterdir()) == sorted(p.name for p in paths)


def test_a_failed_receipt_write_leaves_the_previous_generation_and_no_tmp_files(tmp_path, monkeypatch):
    run = _feed_run(tmp_path)
    intake = tmp_path / "intake.json"
    intake.write_text(json.dumps(_intake(run)))
    paths = _build_paths(tmp_path)
    for path in paths:
        path.write_text("previous generation")

    def disk_full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(feed, "atomic_json", disk_full)
    with pytest.raises(OSError, match="disk full"):
        feed.build(run, *paths, intake)
    assert [path.read_text() for path in paths] == ["previous generation"] * 4
    assert sorted(p.name for p in paths[0].parent.iterdir()) == sorted(p.name for p in paths)


def test_replace_false_stages_without_touching_the_destination(tmp_path):
    target = tmp_path / "out.jsonl"
    target.write_text("old")
    staged = feed.atomic_jsonl(target, [{"a": 1}], replace=False)
    assert target.read_text() == "old" and staged.is_file()
    os.replace(staged, target)
    assert json.loads(target.read_text()) == {"a": 1}


def test_rel_falls_back_to_the_absolute_posix_path_across_drives(tmp_path, monkeypatch):
    assert feed._rel(Path(feed.ROOT) / "data" / "x.csv") == "data/x.csv"

    def other_drive(*args, **kwargs):
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    with monkeypatch.context() as patched:
        patched.setattr(feed.os.path, "relpath", other_drive)
        assert feed._rel(tmp_path / "x.csv") == (tmp_path / "x.csv").resolve().as_posix()
