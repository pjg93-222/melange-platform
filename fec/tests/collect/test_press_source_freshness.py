from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from collect import refresh_press_stream as press
from collect.build_press_engine_feed import REQUIRED_RUN_FILES


SOURCES = {name: str(index) * 64 for index, name in enumerate((
    "refresh_press_delta.R", "press_member_overrides.R", "press_official_coverage.R",
    "press_release_date_precedence.R", "press_wordpress_custom_posts.R",
    "press_official_dated_listings.R",
    "press_request_deadlines.R",
    "press_backlog.R", "cu_press_release_scraper.R"), 1)}


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
    monkeypatch.setattr(press.subprocess, "run", lambda *a, **k: pytest.fail("collector launched"))
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
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(press.subprocess, "run", child)
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
    monkeypatch.setattr(press.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
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
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(press.subprocess, "run", child)
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
    monkeypatch.setattr(press.subprocess, "run", lambda *a, **k: pytest.fail("collector launched"))
    with pytest.raises(ValueError, match="separate target|fresh-inventory"):
        press.main(["--run-root", str(target), "--retry-from", str(source)])
    assert (target / REQUIRED_RUN_FILES[0]).read_bytes() == before
