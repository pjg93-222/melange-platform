# -*- coding: utf-8 -*-
"""The hearing display feed producer and the provenance chain that lets it declare v2 honestly.

The display surface is fail-closed by contract (members/hearing_display_contract.load_optional):
with no hash-bound artifact and receipt it is OMITTED. The producer that writes them may only do so
over a corpus certified as chamber-scoped-speaker-attribution-v2, because the shipped corpus was
collected 2026-07-11 -- before the 2026-08-18 chamber-scope fix -- and 9.7% of its attributed turns
are in the wrong chamber.

THE POSITIVE TEST RUNS THE REAL RESOLVER. Its fixture sequences are produced by
collect.fetch_hearings_seq.parse_seq against the real roster, over a transcript that is
BYTE-IDENTICAL between the two packages. The only difference is the package id, and that alone must
send "Mr. MORAN." to Nathaniel Moran in the House hearing and Jerry Moran in the Senate one -- the
exact correction the contract certifies (pre-fix: Jerry Moran 4,571 turns, Nathaniel Moran 0).

Every negative control asserts a NAMED reason, so a refusal can never be mistaken for an accident.
No network, no live database: fixtures are tmp_path sqlite files and JSON.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))

# The resolver is the point of the positive test, so it needs the real roster. Checked before the
# import because fetch_hearings_seq loads both files at module scope.
_ROSTER = [FEC.parent / "data" / "engine" / "congestion.json", FEC / "legislators.csv"]
_ABSENT = [path.name for path in _ROSTER if not path.is_file()]
if _ABSENT:
    pytest.skip("needs the real roster by design: %s absent" % ", ".join(_ABSENT),
                allow_module_level=True)

from collect import fetch_hearings_seq as collector
from members import build_hearing_display_feed as producer
from members.hearing_display_contract import (
    ATTRIBUTION_CONTRACT, load_optional, sha256_file)


NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
SINCE, UNTIL = "2026-01-01", "2026-06-30"
HOUSE = ("CHRG-119hhrg90001", "2026-05-01", "Oversight of the Department")
SENATE = ("CHRG-119shrg90002", "2026-05-02", "Appropriations review")

# One transcript, used verbatim for BOTH hearings: three "Mr. MORAN." turns, one witness turn, one
# non-speaker turn. Nothing in the text names a chamber, so the attribution can only come from the
# package id -- which is what makes this a test of the fix rather than of the fixture.
TRANSCRIPT = """
    COMMITTEE HEARING

    Mr. MORAN. Thank you, Mr. Chairman, for holding this hearing.

    Secretary DRISCOLL. Thank you for having me today.

    Mr. MORAN. My question is about the budget request.

    Mr. CLERK. The reading of the roll is concluded.

    Mr. MORAN. I yield back the balance of my time.
"""

ANTAGONISM_MEMBERS = {
    "Nathaniel Moran": {"antagonism_z": 0.42, "grandstand_z": -0.11,
                        "n_hearings": 1, "n_disp": 0},
    "Jerry Moran": {"antagonism_z": -0.37, "grandstand_z": 0.08,
                    "n_hearings": 1, "n_disp": 0},
}


def _named_turns(sequence):
    return [turn[0] for turn in sequence if turn[0] not in producer.NON_MEMBER_KINDS]


def _seq_db(directory, packages=(HOUSE, SENATE)):
    """A fixture sequence database whose rows come from the REAL parser and resolver."""
    path = Path(directory) / "hearings_seq.db"
    connection = collector.seqdb(str(path))          # production schema, via the --db override
    sequences = {}
    for package, date, _title in packages:
        sequence, _chair = collector.parse_seq(
            TRANSCRIPT, collector.chamber_of_package(package))
        sequences[package] = sequence
        connection.execute("INSERT OR REPLACE INTO hearing_seq VALUES(?,?,?)",
                           (package, date, json.dumps(sequence, separators=(",", ":"))))
        connection.execute("INSERT OR REPLACE INTO seq_log VALUES(?,?)", (package, "ok"))
    connection.commit()
    connection.close()
    return path, sequences


def _meta_db(directory, packages=(HOUSE, SENATE)):
    path = Path(directory) / "social_media.db"
    connection = sqlite3.connect(str(path))
    connection.execute(
        "CREATE TABLE hearing_meta(package TEXT PRIMARY KEY, date TEXT, title TEXT, "
        "chair_member TEXT, chair_party TEXT, n_named_turns INT, n_members INT)")
    for package, date, title in packages:
        connection.execute("INSERT INTO hearing_meta VALUES(?,?,?,?,?,?,?)",
                           (package, date, title, None, "?", 3, 1))
    connection.commit()
    connection.close()
    return path


def _antagonism(directory, binding, members=None, drop_binding=False):
    path = Path(directory) / "hearing_antagonism.json"
    document = {"members": ANTAGONISM_MEMBERS if members is None else members,
                "n": 2, "corpus_hearings": 2, "basis": "fixture",
                "seq_db_sha256": binding, "seq_receipt_sha256": None}
    if drop_binding:
        document.pop("seq_db_sha256")
    path.write_text(json.dumps(document, indent=0) + "\n", encoding="utf-8", newline="\n")
    return path


def _corpus(tmp_path, rebuild=True, failed=0, binding=True, drop_binding=False):
    """Build a complete, ACCEPTABLE fixture corpus, then apply exactly one defect if asked.

    ``rebuild=False`` is the pre-fix case and is produced by the REAL receipt writer rather than by
    hand-editing a field: a first receipt over an inherited database chains to nothing, so it
    declares unknown-pre-fix-corpus on its own."""
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    seq_db, sequences = _seq_db(tmp_path)
    meta_db = _meta_db(tmp_path)
    receipt_path = Path(str(seq_db) + ".receipt.json")
    seq_receipt = collector.write_seq_receipt(
        seq_db, since=SINCE, until=UNTIL, listed=2, ok=2 - failed, nopage=0, failed=failed,
        rebuild=rebuild, previous_receipt_path=receipt_path, now=NOW)
    antagonism = _antagonism(
        tmp_path, seq_receipt["db_sha256"] if binding else None, drop_binding=drop_binding)
    return SimpleNamespace(
        seq_db=seq_db, meta_db=meta_db, receipt_path=receipt_path, seq_receipt=seq_receipt,
        antagonism=antagonism, sequences=sequences,
        artifact=tmp_path / "out" / "hearing_display_feed.json",
        receipt=tmp_path / "out" / "hearing_display_receipt.json")


def _build(env, **overrides):
    arguments = dict(seq_db=env.seq_db, meta_db=env.meta_db, seq_receipt_path=env.receipt_path,
                     antagonism_path=env.antagonism, artifact_path=env.artifact, now=NOW,
                     ttl_days=10)
    arguments.update(overrides)
    return producer.build_feed(**arguments)


def _refusal(env, **overrides):
    with pytest.raises(producer.HearingFeedRejected) as rejected:
        _build(env, **overrides)
    return rejected.value


# --------------------------------------------------------------------------------------------
# the fixture itself: the correction the contract certifies
# --------------------------------------------------------------------------------------------

def test_the_same_transcript_attributes_by_chamber_not_by_senate_first_guess(tmp_path):
    """THE DEFECT, and the fix, on one transcript. Identical text; only the package id differs."""
    _seq, sequences = _seq_db(tmp_path)
    assert _named_turns(sequences[HOUSE[0]]) == ["Nathaniel Moran"] * 3
    assert _named_turns(sequences[SENATE[0]]) == ["Jerry Moran"] * 3
    # and the non-member turns are present, so the turn count below is a real filter
    assert [turn[0] for turn in sequences[HOUSE[0]]] == [
        "Nathaniel Moran", "W", "Nathaniel Moran", "O", "Nathaniel Moran"]


# --------------------------------------------------------------------------------------------
# positive: a certified corpus produces a feed the contract accepts
# --------------------------------------------------------------------------------------------

def test_certified_corpus_produces_a_feed_the_contract_accepts(tmp_path):
    env = _corpus(tmp_path)
    exit_code = producer.main([
        "--seq-db", str(env.seq_db), "--meta-db", str(env.meta_db),
        "--seq-receipt", str(env.receipt_path), "--antagonism", str(env.antagonism),
        "--artifact", str(env.artifact), "--receipt", str(env.receipt),
        "--ttl-days", "10", "--now", NOW.isoformat()])
    assert exit_code == 0

    loaded = load_optional(env.artifact, env.receipt, now=NOW)
    assert loaded is not None, "the contract rejected a feed built from a certified corpus"
    assert loaded["appearances"] == [
        {"member": "Nathaniel Moran", "date": "2026-05-01", "package": HOUSE[0],
         "title": "Oversight of the Department", "turns": 3},
        {"member": "Jerry Moran", "date": "2026-05-02", "package": SENATE[0],
         "title": "Appropriations review", "turns": 3},
    ]
    assert loaded["members"] == ANTAGONISM_MEMBERS

    receipt = json.loads(env.receipt.read_text(encoding="utf-8"))
    assert receipt["status"] == "ACCEPTED"
    assert receipt["attribution_contract"] == ATTRIBUTION_CONTRACT
    assert receipt["coverage"] == {"status": "COMPLETE_TO_DECLARED_CEILING",
                                   "ceiling": UNTIL, "max_date": "2026-05-02"}
    assert receipt["artifact_sha256"] == sha256_file(env.artifact)
    assert receipt["expires_at"] == (NOW + timedelta(days=10)).isoformat()
    assert receipt["seq_db_sha256"] == env.seq_receipt["db_sha256"]
    assert receipt["seq_receipt_sha256"] == sha256_file(env.receipt_path)
    assert receipt["antagonism_sha256"] == sha256_file(env.antagonism)
    assert receipt["counts"] == {"members": 2, "appearances": 2}
    assert receipt["artifact_path"] == str(env.artifact.resolve())
    # reading the corpus must not disturb it, or the hash binding would flap
    assert sha256_file(env.seq_db) == env.seq_receipt["db_sha256"]


def test_an_expired_receipt_is_omitted_by_the_contract(tmp_path):
    """The TTL is load-bearing: the same accepted feed goes dark once it lapses."""
    env = _corpus(tmp_path)
    assert producer.main([
        "--seq-db", str(env.seq_db), "--meta-db", str(env.meta_db),
        "--seq-receipt", str(env.receipt_path), "--antagonism", str(env.antagonism),
        "--artifact", str(env.artifact), "--receipt", str(env.receipt),
        "--now", NOW.isoformat()]) == 0
    assert load_optional(env.artifact, env.receipt, now=NOW + timedelta(days=9)) is not None
    assert load_optional(env.artifact, env.receipt, now=NOW + timedelta(days=11)) is None


def test_a_missing_meta_database_leaves_titles_empty_and_creates_no_file(tmp_path):
    """A hearing with no meta row still gets an appearance record (the desk falls back to the
    package id) -- and the producer must never CREATE the database it failed to find."""
    env = _corpus(tmp_path)
    absent = tmp_path / "no_such_social_media.db"
    artifact, _receipt = _build(env, meta_db=absent)
    assert [row["title"] for row in artifact["appearances"]] == ["", ""]
    assert not absent.exists()


# --------------------------------------------------------------------------------------------
# negative controls: every refusal is NAMED
# --------------------------------------------------------------------------------------------

def test_pre_fix_corpus_is_refused_as_attribution_contract_not_v2(tmp_path):
    """The corpus this whole chain exists to keep off the surface. Its receipt is not hand-written:
    a non-rebuild run with nothing to chain to declares unknown-pre-fix-corpus by itself."""
    env = _corpus(tmp_path, rebuild=False)
    assert env.seq_receipt["attribution_contract"] == "unknown-pre-fix-corpus"
    rejected = _refusal(env)
    assert rejected.reason == "ATTRIBUTION_CONTRACT_NOT_V2"
    assert "unknown-pre-fix-corpus" in rejected.detail


def test_database_modified_after_the_receipt_is_refused_as_hash_mismatch(tmp_path):
    env = _corpus(tmp_path)
    connection = sqlite3.connect(str(env.seq_db))
    connection.execute("INSERT INTO hearing_seq VALUES(?,?,?)",
                       ("CHRG-119hhrg90003", "2026-05-03", "[]"))
    connection.commit(); connection.close()
    rejected = _refusal(env)
    assert rejected.reason == "SEQ_DB_HASH_MISMATCH"
    assert env.seq_receipt["db_sha256"] in rejected.detail


def test_unaccounted_packages_are_refused_as_coverage_incomplete(tmp_path):
    env = _corpus(tmp_path, failed=1)
    rejected = _refusal(env)
    assert rejected.reason == "COVERAGE_INCOMPLETE"
    assert "1 of 2 listed packages" in rejected.detail


def test_antagonism_without_a_corpus_binding_is_refused_as_unbound(tmp_path):
    for env in (_corpus(tmp_path / "null", binding=False),
                _corpus(tmp_path / "absent", drop_binding=True)):
        rejected = _refusal(env)
        assert rejected.reason == "ANTAGONISM_UNBOUND"
        assert env.seq_receipt["db_sha256"] in rejected.detail


def test_antagonism_bound_to_a_different_corpus_is_refused_as_unbound(tmp_path):
    """NEGATIVE CONTROL for the control: a binding that is PRESENT but wrong must also refuse --
    otherwise scores computed over the pre-fix corpus could ride a certified corpus's receipt."""
    env = _corpus(tmp_path)
    _antagonism(tmp_path, "0" * 64)
    assert _refusal(env).reason == "ANTAGONISM_UNBOUND"


def test_missing_corpus_receipt_is_refused_by_name(tmp_path):
    env = _corpus(tmp_path)
    env.receipt_path.unlink()
    assert _refusal(env).reason == "SEQ_RECEIPT_MISSING"


def test_missing_antagonism_artifact_is_refused_by_name(tmp_path):
    env = _corpus(tmp_path)
    env.antagonism.unlink()
    assert _refusal(env).reason == "ANTAGONISM_MISSING"


def test_a_refused_build_exits_two_and_writes_nothing(tmp_path):
    """The refusal has to be a REFUSAL: no partial artifact, no stale receipt left behind."""
    env = _corpus(tmp_path, rebuild=False)
    exit_code = producer.main([
        "--seq-db", str(env.seq_db), "--meta-db", str(env.meta_db),
        "--seq-receipt", str(env.receipt_path), "--antagonism", str(env.antagonism),
        "--artifact", str(env.artifact), "--receipt", str(env.receipt),
        "--now", NOW.isoformat()])
    assert exit_code == 2
    assert not env.artifact.exists() and not env.receipt.exists()
    assert load_optional(env.artifact, env.receipt, now=NOW) is None


# --------------------------------------------------------------------------------------------
# the corpus receipt chain
# --------------------------------------------------------------------------------------------

def _receipt(db, previous, now, rebuild=False, pre=None, listed=2, ok=2, failed=0):
    return collector.write_seq_receipt(
        db, since=SINCE, until=UNTIL, listed=listed, ok=ok, nopage=0, failed=failed,
        rebuild=rebuild, previous_receipt_path=previous, now=now, pre_run_db_sha256=pre)


def _grow(db, package, date):
    connection = sqlite3.connect(str(db))
    connection.execute("INSERT INTO hearing_seq VALUES(?,?,?)", (package, date, "[]"))
    connection.execute("INSERT INTO seq_log VALUES(?,?)", (package, "ok"))
    connection.commit(); connection.close()


def test_fresh_rebuild_declares_v2(tmp_path):
    db, _sequences = _seq_db(tmp_path)
    path = Path(str(db) + ".receipt.json")
    receipt = _receipt(db, path, NOW, rebuild=True)
    assert receipt["attribution_contract"] == ATTRIBUTION_CONTRACT
    assert receipt["schema"] == "hearings-seq-receipt-v1"
    assert receipt["rebuild"] is True
    assert receipt["previous_receipt_sha256"] is None
    assert receipt["db_sha256"] == sha256_file(db)
    assert receipt["max_date"] == "2026-05-02"
    assert receipt["packages"] == {"listed": 2, "ok": 2, "nopage": 0, "failed": 0}
    assert receipt["built_at"] == NOW.isoformat()
    assert json.loads(path.read_text(encoding="utf-8")) == receipt


def test_an_incremental_run_chained_to_a_v2_receipt_stays_v2(tmp_path):
    """The honest continuation: the corpus grew, but it grew from the certified database."""
    db, _sequences = _seq_db(tmp_path)
    path = Path(str(db) + ".receipt.json")
    first = _receipt(db, path, NOW, rebuild=True)
    first_file_sha = sha256_file(path)
    _grow(db, "CHRG-119hhrg90003", "2026-06-01")
    second = _receipt(db, path, NOW + timedelta(days=1), pre=first["db_sha256"], listed=3, ok=3)
    assert second["attribution_contract"] == ATTRIBUTION_CONTRACT
    assert second["rebuild"] is False
    assert second["previous_receipt_sha256"] == first_file_sha
    assert second["db_sha256"] != first["db_sha256"]
    assert second["max_date"] == "2026-06-01"


def test_a_run_that_collected_nothing_chains_on_the_current_hash(tmp_path):
    """With no new packages the database is unchanged, so no pre-run hash is needed."""
    db, _sequences = _seq_db(tmp_path)
    path = Path(str(db) + ".receipt.json")
    _receipt(db, path, NOW, rebuild=True)
    assert _receipt(db, path, NOW + timedelta(days=1))["attribution_contract"] == ATTRIBUTION_CONTRACT


def test_a_database_that_does_not_match_the_previous_receipt_is_downgraded(tmp_path):
    """The chain breaks at the hash: an out-of-band edit or a swapped file loses the claim."""
    db, _sequences = _seq_db(tmp_path)
    path = Path(str(db) + ".receipt.json")
    _receipt(db, path, NOW, rebuild=True)
    downgraded = _receipt(db, path, NOW + timedelta(days=1), pre="0" * 64)
    assert downgraded["attribution_contract"] == "unknown-pre-fix-corpus"
    assert downgraded["attribution_contract"] == collector.UNKNOWN_ATTRIBUTION


def test_a_pre_fix_corpus_cannot_be_laundered_by_an_incremental_run(tmp_path):
    """THE PROPERTY THAT MATTERS. The live database inherits no receipt, so its first receipt is
    unknown -- and no number of clean incremental runs over it ever promotes it. Only a fresh
    --rebuild into a new path can claim the corrected contract."""
    db, _sequences = _seq_db(tmp_path)
    path = Path(str(db) + ".receipt.json")
    inherited = _receipt(db, path, NOW)
    assert inherited["attribution_contract"] == "unknown-pre-fix-corpus"
    assert inherited["previous_receipt_sha256"] is None
    for day in (1, 2, 3):
        chained = _receipt(db, path, NOW + timedelta(days=day), pre=inherited["db_sha256"])
        assert chained["attribution_contract"] == "unknown-pre-fix-corpus", day
        assert chained["previous_receipt_sha256"] is not None, "the chain is still recorded"


def test_rebuild_refuses_an_existing_database_and_leaves_it_untouched(tmp_path):
    """A rebuild is the only route to a v2 claim, so the temptation is to aim it at the live
    corpus. That would destroy hours of collection; refuse instead of dropping or truncating."""
    existing = tmp_path / "hearings_seq.db"
    existing.write_bytes(b"seven thousand packages")
    with pytest.raises(collector.RebuildTargetError) as refused:
        collector.require_fresh_rebuild_target(str(existing))
    assert "refuses an existing database" in str(refused.value)
    assert str(existing) in str(refused.value)
    assert existing.read_bytes() == b"seven thousand packages"

    with pytest.raises(collector.RebuildTargetError) as no_path:
        collector.require_fresh_rebuild_target(None)
    assert "requires --db" in str(no_path.value)

    fresh = tmp_path / "rebuilt.db"
    assert collector.require_fresh_rebuild_target(str(fresh)) == str(fresh)
    assert not fresh.exists(), "the guard must not create the target it approves"
