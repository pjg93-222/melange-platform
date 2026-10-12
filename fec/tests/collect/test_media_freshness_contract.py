import datetime as dt
import json
from pathlib import Path
import sqlite3
import sys

import pytest

FEC = Path(__file__).resolve().parents[2]
ROOT = FEC.parent
sys.path.insert(0, str(FEC))

from audit_collection import audit_db, interview_source_label
from collect.refresh_cspan_wj import load_receipt
from members import wikipedia_biographies


def test_interview_source_labels_include_bias_relevant_outlets():
    assert interview_source_label("https://www.foxnews.com/transcript/example") == "Fox News"
    assert interview_source_label("https://www.ms.now/transcript/example") == "MSNBC / MS NOW"
    assert interview_source_label(
        "https://www.c-span.org/program/washington-journal/example/123"
    ) == "Washington Journal"
    assert interview_source_label("https://transcripts.cnn.com/show/x") == "CNN"


def test_archive_terminal_date_is_distinct_from_collector_freshness(tmp_path):
    database = tmp_path / "media.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE speeches(member TEXT,date TEXT,source TEXT,chamber TEXT,"
        "package TEXT,nwords INTEGER,text TEXT)"
    )
    connection.execute(
        "CREATE TABLE iv_fetch_log(key TEXT PRIMARY KEY,status TEXT,ts TEXT)"
    )
    connection.executemany(
        "INSERT INTO speeches VALUES (?,?,?,?,?,?,?)",
        [
            ("Member A", "2024-09-15", "INTERVIEW", "fox", "https://www.foxnews.com/transcript/a", 10, "one two three four five six seven eight nine ten"),
            ("Member A", "2026-08-31", "INTERVIEW", "cnn", "https://transcripts.cnn.com/show/a", 10, "one two three four five six seven eight nine ten"),
            ("Member B", "2025-12-19", "INTERVIEW", "wj", "https://www.c-span.org/program/washington-journal/x/1", 10, "one two three four five six seven eight nine ten"),
        ],
    )
    yesterday = dt.date.today() - dt.timedelta(days=1)
    connection.executemany(
        "INSERT INTO iv_fetch_log VALUES (?,?,?)",
        [
            (f"cnn:{yesterday.isoformat()}", "ok", dt.datetime.now().isoformat()),
            (f"pbs:{yesterday.isoformat()}", "noep", dt.datetime.now().isoformat()),
            (f"npr:morning-edition:{yesterday.isoformat()}", "ok", dt.datetime.now().isoformat()),
        ],
    )
    connection.commit()
    connection.close()

    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "fox.json").write_text(json.dumps({
        "schema": "interview-source-receipt-v1",
        "source": "fox",
        "status": "COMPLETE",
        "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "freshness_mode": "historical_archive",
        "source_terminal_date": "2024-09-15",
    }), encoding="utf-8")
    (receipts / "washington_journal.json").write_text(json.dumps({
        "schema": "washington-journal-collection-v1",
        "status": "COMPLETE",
        "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "collector_horizon": yesterday.isoformat(),
        "source_latest_program_date": yesterday.isoformat(),
    }), encoding="utf-8")

    result = audit_db(database, receipts)
    fox = result["source_coverage"]["Fox News"]
    assert fox["source_terminal_date"] == "2024-09-15"
    assert fox["max_accepted_date"] == "2024-09-15"
    assert fox["collector_status"] == "COMPLETE"
    assert fox["appearances"] == 1
    assert result["collection_checked_through_yesterday"] is True
    assert result["errors"] == []


def test_cspan_receipt_must_match_requested_window(tmp_path):
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps({
        "schema": "cspan-wj-search-v1",
        "status": "COMPLETE",
        "since": "2026-08-01",
        "until": "2026-08-31",
    }), encoding="utf-8")
    assert load_receipt(path, "cspan-wj-search-v1", "2026-08-01", "2026-08-31")
    with pytest.raises(RuntimeError, match="does not match"):
        load_receipt(path, "cspan-wj-search-v1", "2026-08-02", "2026-08-31")


def test_weekly_refresh_schedules_full_media_and_biography_sources():
    source = (ROOT / "fec" / "weekly_refresh.py").read_text(encoding="utf-8")
    assert "interviews: Fox archive audit" in source
    assert "interviews: Washington Journal (21d)" in source
    assert "Wikipedia biographies (full roster)" in source
    assert source.index("Wikipedia biographies (full roster)") < source.index(
        "FULL PIPELINE + dashboards"
    )


def test_cspan_search_is_current_bounded_and_cumulative():
    source = (ROOT / "cspan" / "cspan_scrape.R").read_text(encoding="utf-8")
    assert "12%2F31%2F2025" not in source
    assert 'flag("since"' in source
    assert 'flag("until"' in source
    assert "rbind(prior, df)" in source
    assert 'has_flag("retry-empty")' in source


def test_press_refresh_classifies_nonarticles_before_body_fetch():
    source = (ROOT / "fec" / "collect" / "refresh_press_delta.R").read_text(
        encoding="utf-8"
    )
    assert "terminal_nonarticle_reason" in source
    assert "nested_press_release_media_asset" in source
    assert "press_delta_terminal_nonarticles.csv" in source
    assert "--retry-from" in source
    assert 'materialize_press_outputs(full, "after_network_retry")' in source
    assert "after_targeted_network_retry" not in source
    assert "prior_recovered" in source
    assert "Targeted retry resume: preserved" in source


def test_wikipedia_strict_mode_rejects_missing_member(monkeypatch):
    monkeypatch.setattr(wikipedia_biographies, "build", lambda _member: {
        "coverage": {"rows": 1, "roster_members": 1, "missing": ["Member A"]}
    })
    monkeypatch.setattr(sys, "argv", ["wikipedia_biographies.py", "--strict"])
    with pytest.raises(RuntimeError, match="left 1 member"):
        wikipedia_biographies.main()
