import json
import sqlite3

from fec.staffer_network.complete_existing_career_histories import (
    classify_directory_claims, classify_historical_clauses, classify_release_clauses,
)


def claim(db, ident, person, employer, title, status, start=None, end=None,
          observed="2026-09-05T00:00:00Z"):
    payload = json.dumps({"title": title, "source_observed_at": observed})
    db.execute("INSERT INTO claim VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
        ident, "directory", person, employer, title, start, end, start, end, status, payload))


def database():
    db = sqlite3.connect(":memory:")
    db.execute("""CREATE TABLE claim(id TEXT PRIMARY KEY,source TEXT,person TEXT,
      employer TEXT,title TEXT,raw_start TEXT,raw_end TEXT,start TEXT,
      end_exclusive TEXT,status TEXT,payload TEXT)""")
    return db


def test_exact_politico_duplicate_resolves_and_preserves_references():
    db = database()
    claim(db, "missing", "politico:1", "office:1", "Chief of Staff", "MISSING_DATE_BOUND")
    claim(db, "dated", "politico:1", "office:1", "Chief of Staff",
          "BOUNDED_SOURCE_INTERVAL", "2020-01-01", "2021-01-01")
    decisions, gaps = classify_directory_claims(db)
    assert gaps == []
    assert decisions[0]["interval"] == ["2020-01-01", "2021-01-01"]
    assert decisions[0]["missing_claim_id"] == "missing"
    assert decisions[0]["bounded_references"][0]["claim_id"] == "dated"


def test_multiple_real_intervals_are_held_and_interns_never_resolve():
    db = database()
    claim(db, "missing", "politico:1", "office:1", "Director", "MISSING_DATE_BOUND")
    claim(db, "dated-a", "politico:1", "office:1", "Director",
          "BOUNDED_SOURCE_INTERVAL", "2010-01-01", "2011-01-01")
    claim(db, "dated-b", "politico:1", "office:1", "Director",
          "BOUNDED_SOURCE_INTERVAL", "2020-01-01", "2021-01-01")
    claim(db, "intern-missing", "politico:2", "office:2", "Intern", "MISSING_DATE_BOUND")
    claim(db, "intern-dated", "politico:2", "office:2", "Intern",
          "BOUNDED_SOURCE_INTERVAL", "2020-01-01", "2021-01-01")
    decisions, gaps = classify_directory_claims(db)
    assert decisions == []
    assert {row["status"] for row in gaps} == {
        "AMBIGUOUS_REPEATED_ROLE_INTERVALS", "ROLE_NOT_SUBSTANTIVE"}


def test_person_office_title_and_provenance_must_all_match():
    db = database()
    claim(db, "missing", "politico:1", "office:1", "Counsel", "MISSING_DATE_BOUND")
    claim(db, "different-person", "politico:2", "office:1", "Counsel",
          "BOUNDED_SOURCE_INTERVAL", "2020-01-01", "2021-01-01")
    claim(db, "different-office", "politico:1", "office:2", "Counsel",
          "BOUNDED_SOURCE_INTERVAL", "2020-01-01", "2021-01-01")
    decisions, gaps = classify_directory_claims(db)
    assert decisions == []
    assert gaps[0]["status"] == "NO_EXACT_HELD_DATE_EVIDENCE"


def test_accepted_lda_binding_requires_one_supported_interval():
    release = {"identity_bindings": [{
        "kind": "covered_politico", "source": "person:lda:1", "target": "politico:1",
        "decision_id": "binding", "evidence": {"support": [
            {"clause_id": "one", "directory_role_id": "role:1",
             "covered_dates": {"status": "MISSING_DATE_BOUND"},
             "dates_from_directory_only": True,
             "directory_dates": ["2020-01-01", "2021-01-01"]},
            {"clause_id": "many", "directory_role_id": "role:2",
             "covered_dates": {"status": "MISSING_DATE_BOUND"},
             "dates_from_directory_only": True,
             "directory_dates": ["2018-01-01", "2019-01-01"]},
            {"clause_id": "many", "directory_role_id": "role:3",
             "covered_dates": {"status": "MISSING_DATE_BOUND"},
             "dates_from_directory_only": True,
             "directory_dates": ["2020-01-01", "2021-01-01"]},
        ]}
    }]}
    decisions, gaps = classify_release_clauses(release)
    assert [row["clause_id"] for row in decisions] == ["one"]
    assert gaps[0]["status"] == "AMBIGUOUS_COMPOUND_CAREER_CLAUSE"


def test_bounded_historical_clause_structures_only_exact_office_and_title():
    ledger = database()
    claim(ledger, "role", "politico:1", "office:1", "Senior Counsel",
          "BOUNDED_SOURCE_INTERVAL", "2020-01-01", "2021-01-01")
    ledger.execute("UPDATE claim SET payload=? WHERE id='role'", (
        json.dumps({"title": "Senior Counsel", "employer": "House Financial Services Committee",
                    "source_observed_at": "2026-09-05T00:00:00Z"}),))
    historical = sqlite3.connect(":memory:")
    historical.executescript("""
      CREATE TABLE claim(id TEXT PRIMARY KEY,person TEXT);
      CREATE TABLE clause(id TEXT PRIMARY KEY,claim_id TEXT,raw_text TEXT,status TEXT,assessment TEXT);
      CREATE TABLE support(window_id TEXT,id TEXT,claim_id TEXT,filing_uuid TEXT,activity TEXT);
      INSERT INTO claim VALUES('h1','lda:lobbyist:1');
      INSERT INTO clause VALUES('c1','h1','2017-2018: Senior Counsel, House Financial Services Committee',
        'BOUNDED_SOURCE_INTERVAL','{"start":"2017-12-31","end_exclusive":"2018-01-02"}');
      INSERT INTO support VALUES('historical-2019','s1','h1','filing-1','activity-1');
    """)
    decisions, gaps = classify_historical_clauses(ledger, historical)
    assert gaps == []
    assert decisions[0]["employer_key"] == "house financial services committee"
    assert decisions[0]["title_key"] == "senior counsel"
    assert decisions[0]["filing_support"][0]["filing_uuid"] == "filing-1"
