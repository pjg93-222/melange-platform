"""A scheduled discovery pass must contact the source again after completion."""
import json
import hashlib
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legislation import refresh_legislation_discovery as REFRESH


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def checkpoint(tmp_path):
    out = tmp_path / "current"
    registry = tmp_path / "registry.json"
    write(registry, {"categories": [{"id": "example"}]})
    write(out / "manifest.json", {"status": "complete", "completed_at": "previous"})
    write(out / "queries" / "example.json", {"records": ["old"]})
    write(out / "raw" / "pages" / "example" / "page_001.json", {"records": ["old"]})
    return out, registry


def test_new_cycle_archives_stale_browser_cache_and_preserves_cumulative_fallback(checkpoint):
    out, registry = checkpoint
    preserved = ["manifest.json", "congress_advanced_records.ndjson",
                 "congress_advanced_memberships.ndjson", "coverage_gap_ledger.ndjson",
                 "exclusion_ledger.ndjson", "automatic_verification_cooldown.json",
                 ".govinfo_fallback_cache/raw/version.xml"]
    for name in preserved[1:]:
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"preserved evidence\n")
    before = {name: (out / name).read_bytes() for name in preserved}
    cycle = REFRESH.prepare_discovery_cycle(out, registry, None)
    assert cycle["status"] == "running"
    assert not (out / "queries").exists()
    assert not (out / "raw/pages").exists()
    archive = out / ".refresh_cycles" / cycle["id"]
    assert (archive / "queries/example.json").is_file()
    assert (archive / "raw/pages/example/page_001.json").is_file()
    assert {name: (out / name).read_bytes() for name in preserved} == before


def test_interrupted_cycle_reuses_only_its_new_queries(checkpoint):
    out, registry = checkpoint
    first = REFRESH.prepare_discovery_cycle(out, registry, None)
    write(out / "queries/example.json", {"records": ["fresh partial cycle"]})
    resumed = REFRESH.prepare_discovery_cycle(out, registry, None)
    assert resumed["id"] == first["id"]
    assert json.loads((out / "queries/example.json").read_text())["records"] == ["fresh partial cycle"]


def test_successful_cycle_restarts_fresh_on_next_scheduled_call(checkpoint):
    out, registry = checkpoint
    first = REFRESH.prepare_discovery_cycle(out, registry, None)
    write(out / "queries/example.json", {"records": ["cycle one"]})
    write(out / "manifest.json", {"status": "complete", "completed_at": "cycle one"})
    REFRESH.complete_discovery_cycle(out, first)
    second = REFRESH.prepare_discovery_cycle(out, registry, None)
    assert second["id"] != first["id"]
    assert not (out / "queries").exists()
    assert (out / ".refresh_cycles" / second["id"] / "queries/example.json").is_file()


def test_crash_after_collector_success_does_not_freeze_future_cycles(checkpoint):
    out, registry = checkpoint
    first = REFRESH.prepare_discovery_cycle(out, registry, None)
    write(out / "queries/example.json", {"records": ["new"]})
    write(out / "manifest.json", {"status": "complete", "completed_at": "new"})
    second = REFRESH.prepare_discovery_cycle(out, registry, None)
    assert second["id"] != first["id"]
    assert not (out / "queries").exists()


def test_interrupted_archive_preparation_finishes_without_discarding_evidence(checkpoint, monkeypatch):
    out, registry = checkpoint
    original_replace = REFRESH.os.replace

    def interrupt_pages(source, destination):
        if Path(source) == out / "raw/pages":
            raise OSError("interrupted cache archival")
        return original_replace(source, destination)

    monkeypatch.setattr(REFRESH.os, "replace", interrupt_pages)
    with pytest.raises(OSError, match="interrupted"):
        REFRESH.prepare_discovery_cycle(out, registry, None)
    preparing = json.loads((out / REFRESH.CYCLE_FILE).read_text())
    assert preparing["status"] == "preparing"
    monkeypatch.setattr(REFRESH.os, "replace", original_replace)
    resumed = REFRESH.prepare_discovery_cycle(out, registry, None)
    assert resumed["id"] == preparing["id"]
    assert resumed["status"] == "running"
    assert (out / ".refresh_cycles" / resumed["id"] / "queries/example.json").is_file()
    assert not (out / "raw/pages").exists()


def test_old_complete_manifest_cannot_authorize_cycle_completion(checkpoint):
    out, registry = checkpoint
    cycle = REFRESH.prepare_discovery_cycle(out, registry, None)
    with pytest.raises(RuntimeError, match="new complete manifest"):
        REFRESH.complete_discovery_cycle(out, cycle)
    assert json.loads((out / REFRESH.CYCLE_FILE).read_text())["status"] == "running"


def test_registry_or_category_change_cannot_resume_previous_scope(checkpoint):
    out, registry = checkpoint
    first = REFRESH.prepare_discovery_cycle(out, registry, "example")
    same = REFRESH.prepare_discovery_cycle(out, registry, " example,example ")
    assert same["id"] == first["id"]
    changed = REFRESH.prepare_discovery_cycle(out, registry, None)
    assert changed["id"] != first["id"]
    write(registry, {"categories": [{"id": "other"}]})
    assert REFRESH.prepare_discovery_cycle(out, registry, None)["id"] != changed["id"]


def cooldown_evidence(out):
    now = time.time()
    stamp = lambda seconds: datetime.fromtimestamp(seconds, timezone.utc).isoformat()
    state = {
        "schema": "congress-automatic-verification-cooldown-v1", "status": "active",
        "observed_at": stamp(now - 60), "next_retry_not_before": stamp(now + 540),
        "consecutive_timeouts": 1, "cooldown_ms": 600000,
        "policy": {"kind": "exponential_backoff", "reset_after_validated_result_page": True,
                   "automatic_refresh_or_reload_during_cooldown": False,
                   "captcha_bypass_allowed": False, "verification_interaction_allowed": False},
        "classification": {"kind": "automatic_security_verification", "interactive": False,
                           "captcha_bypass_attempted": False, "verification_interaction_performed": False},
    }
    write(out / "automatic_verification_cooldown.json", state)
    receipt = out / "raw/failure_debug/timeout.json"
    write(receipt, state)
    manifest = {
        "schema": "acu-congress-advanced-pages-manifest-v1", "status": "failed",
        "error": {"code": "CONGRESS_AUTOMATIC_VERIFICATION_TIMEOUT"},
        "automatic_verification_timeout": {
            "classification": "automatic_verification_timeout",
            "collection_stopped_without_result_acceptance": True,
            "interaction_performed": False, "captcha_bypass_attempted": False,
            "state_file": "automatic_verification_cooldown.json",
            "receipt_file": "raw/failure_debug/timeout.json",
            "receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
            **{key: state[key] for key in ("consecutive_timeouts", "cooldown_ms", "next_retry_not_before")},
        },
    }
    failure = out / "manifest.partial.123.json"
    write(failure, manifest)
    return now, state, manifest, failure, receipt


def test_active_cooldown_selects_bound_failure_without_mutating_security_state(checkpoint):
    out, registry = checkpoint
    now, state, manifest, failure, receipt = cooldown_evidence(out)
    before = (out / "automatic_verification_cooldown.json").read_bytes()
    assert REFRESH.active_cooldown_failure(out, now=now) == failure
    assert REFRESH.active_cooldown_failure(out, now=now + 600) is None
    assert (out / "automatic_verification_cooldown.json").read_bytes() == before


@pytest.mark.parametrize("broken", ["code", "schema", "hash", "state", "interaction", "receipt_escape"])
def test_cooldown_fallback_refuses_unproven_or_mismatched_failure(checkpoint, broken):
    out, registry = checkpoint
    now, state, manifest, failure, receipt = cooldown_evidence(out)
    if broken == "code":
        manifest["error"]["code"] = "NETWORK_ERROR"
    elif broken == "schema":
        manifest["schema"] = "other"
    elif broken == "hash":
        receipt.write_text("{}", encoding="utf-8")
    elif broken == "state":
        state["classification"]["provider"] = "mismatched"
        write(out / "automatic_verification_cooldown.json", state)
    elif broken == "interaction":
        manifest["automatic_verification_timeout"]["interaction_performed"] = True
    elif broken == "receipt_escape":
        manifest["automatic_verification_timeout"]["receipt_file"] = "../outside.json"
    write(failure, manifest)
    assert REFRESH.active_cooldown_failure(out, now=now) is None


def test_main_runs_only_official_fallback_during_active_cooldown(checkpoint, monkeypatch):
    out, registry = checkpoint
    now, state, manifest, failure, receipt = cooldown_evidence(out)
    monkeypatch.setattr(REFRESH, "REGISTRY", registry)
    monkeypatch.setattr(sys, "argv", ["discovery", "--out", str(out)])
    monkeypatch.setattr(REFRESH, "node_runtime", lambda: pytest.fail("must not launch Congress during cooldown"))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command[1] == str(REFRESH.GOVINFO_FALLBACK)
        assert command[command.index("--browser-failure") + 1] == str(failure)
        write(out / "manifest.json", {"status": "complete", "transport": "official fallback"})
        (out / "congress_advanced_records.ndjson").write_text('{"fresh":true}\n', encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(REFRESH.subprocess, "run", run)
    assert REFRESH.main() == 0
    assert len(calls) == 1
    assert json.loads((out / "automatic_verification_cooldown.json").read_text()) == state
