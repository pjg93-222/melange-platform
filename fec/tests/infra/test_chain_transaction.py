"""Chain transaction contract (fec/chain_transaction.py), on synthetic temp roots only: no pipeline, collector or network.

Covers the module directly (snapshot, byte-exact rollback including mtime, new-file removal, tracked stores, crash recovery
from an un-finalized journal, the free-space gate, orchestrator state left alone) and, through the real run_pipeline.main over a
fake chain of tiny scripts, the integration the owner ruled on 2026-10-15: a crash at a rank step restores LIVE, a crash after
commit does not, a tolerated display-only failure keeps the chain going marked LAST_GOOD, and a failing terminal audit does not
undo a complete vintage.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))
import chain_transaction as CT  # noqa: E402


@pytest.fixture(autouse=True)
def _no_disk_floor(monkeypatch):
    monkeypatch.setenv(CT.MIN_FREE_GB_ENV, '0')
    monkeypatch.delenv(CT.TXN_DIR_ENV, raising=False)
    # never stage into the real C:\melange-data from a test: a missing data root makes txn_base() fall back in-tree
    monkeypatch.setenv(CT.DATA_ROOT_ENV, os.path.join(os.path.dirname(os.path.abspath(__file__)), '_no_such_melange_data_root_'))


def put(root, rel, data, mtime=None):
    path = Path(root, *rel.split('/'))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode('utf-8'))
    if mtime is not None:
        os.utime(path, ns=(mtime, mtime))
    return path


def live(root):
    """A small LIVE tree: root artifacts, copy trees, a tracked append-only store, and files outside the write set."""
    put(root, 'ranks_python.json', '{"v": 1}', 1_700_000_000_000_000_000)
    put(root, 'dashboard_data.json', '{"d": 1}', 1_700_000_001_000_000_000)
    put(root, 'Moneyball.html', b'<html>one\r\n</html>', 1_700_000_002_000_000_000)
    put(root, 'notes.md', 'not in the write set')
    put(root, 'data/engine/congestion.json', '[1]', 1_700_000_003_000_000_000)
    put(root, 'data/members/m.json', '{"m": 1}', 1_700_000_004_000_000_000)
    put(root, 'data/stance/rank_honors/gen1/a.json', '{"h": 1}', 1_700_000_005_000_000_000)
    put(root, 'data/engine/congress_pipeline_status.json', '{"parent": "owns this"}')
    put(root, 'data/engine/dead_letter/gold_labels.jsonl', '{"id": "x1"}\n')
    return root


def snapshot_of(root):
    """{rel: (bytes, mtime_ns)} for every file under root except the transaction directory itself."""
    out = {}
    for path in Path(root).rglob('*'):
        rel = path.relative_to(root).as_posix()
        if path.is_file() and not rel.startswith('data/engine/chain_txn/'):
            out[rel] = (path.read_bytes(), path.stat().st_mtime_ns)
    return out


def journals(root):
    base = Path(root, 'data/engine/chain_txn')
    return {p.name: json.loads((p / 'journal.json').read_text()) for p in sorted(base.iterdir())} if base.is_dir() else {}


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


# ------------------------------------------------------------------------------------------------ module

def test_rollback_restores_every_path_byte_exact_and_removes_new_files(tmp_path):
    root = live(tmp_path)
    before = snapshot_of(root)
    txn = quiet(CT.begin, root, run_id='run-1')
    # the "chain": rewrite, delete, truncate, create at root and in two trees, create inside the tracked store
    put(root, 'ranks_python.json', '{"v": 2}')
    (root / 'dashboard_data.json').unlink()
    put(root, 'Moneyball.html', b'')
    put(root, 'data/engine/congestion.json', '[1, 2, 3]')
    put(root, 'brand_new.json', '{}')
    put(root, 'data/members/new_member.json', '{}')
    put(root, 'data/stance/rank_honors/gen2/b.json', '{"h": 2}')
    assert quiet(txn.rollback, 'a rank step crashed') is True
    after = snapshot_of(root)
    assert after == before                      # bytes AND mtime_ns of every file, new files gone
    assert txn.state == CT.ROLLED_BACK
    record = json.loads((Path(txn.dir) / 'journal.json').read_text())
    assert record['state'] == CT.ROLLED_BACK and record['rollback']['counts']['errors'] == 0
    assert sorted(record['rollback']['removed']) == ['brand_new.json', 'data/members/new_member.json',
                                                     'data/stance/rank_honors/gen2/b.json']
    assert not (Path(txn.dir) / 'files').exists()   # the payload is dropped once LIVE is proven restored


def test_the_snapshot_is_a_copy_not_a_hardlink(tmp_path):
    root = live(tmp_path)
    txn = quiet(CT.begin, root, run_id='copy')
    staged = Path(txn.dir, 'files', 'ranks_python.json')
    assert staged.read_bytes() == b'{"v": 1}'
    put(root, 'ranks_python.json', '{"v": "mutated"}')      # a hardlink would have carried this into the backup
    assert staged.read_bytes() == b'{"v": 1}'
    quiet(txn.rollback, 'test')


def test_journal_records_path_sha256_size_and_existence(tmp_path):
    root = live(tmp_path)
    txn = quiet(CT.begin, root, run_id='journal')
    entry = txn.journal['files']['ranks_python.json']
    assert entry == {'sha256': hashlib.sha256(b'{"v": 1}').hexdigest(), 'size': 8,
                     'mtime_ns': 1_700_000_000_000_000_000, 'existed': True}
    assert 'notes.md' not in txn.journal['files']                      # outside the write set
    assert 'data/engine/congress_pipeline_status.json' not in txn.journal['files']
    assert 'data/stance/rank_honors/gen1/a.json' in txn.journal['tracked']
    assert txn.state == CT.OPEN and (Path(txn.dir) / 'journal.json').is_file()
    quiet(txn.rollback, 'test')


def test_crash_after_commit_is_unaffected(tmp_path):
    root = live(tmp_path)
    txn = quiet(CT.begin, root, run_id='ok')
    put(root, 'ranks_python.json', '{"v": 2}')
    quiet(txn.commit)
    assert txn.state == CT.COMMITTED
    put(root, 'ranks_python.json', '{"v": 3}')                         # something later goes wrong
    assert quiet(CT.recover, root) == []                                # a finalized transaction is never restored
    assert quiet(txn.rollback, 'too late') is None                      # and rollback after commit is a no-op
    assert (root / 'ranks_python.json').read_bytes() == b'{"v": 3}'


def test_unfinalized_journal_is_restored_on_the_next_start(tmp_path):
    root = live(tmp_path)
    before = snapshot_of(root)
    txn = quiet(CT.begin, root, run_id='died')
    put(root, 'ranks_python.json', '{"v": 2}')
    put(root, 'orphan_output.json', '{}')
    (root / 'data/engine/congestion.json').unlink()
    del txn                                                             # the process was killed: nothing finalized it
    assert [j['state'] for j in journals(root).values()] == [CT.OPEN]
    assert quiet(CT.recover, root) == ['died']
    assert snapshot_of(root) == before
    assert [j['state'] for j in journals(root).values()] == [CT.ROLLED_BACK]
    assert journals(root)[next(iter(journals(root)))]['reason'].startswith('recovered')


def test_a_snapshot_that_never_finished_is_discarded_without_touching_live(tmp_path):
    root = live(tmp_path)
    before = snapshot_of(root)
    partial = Path(root, 'data/engine/chain_txn/20261015T000000Z-partial/files')
    partial.mkdir(parents=True)
    (partial / 'ranks_python.json').write_text('{"v": "half copied"}')
    assert quiet(CT.recover, root) == []
    assert not partial.parent.exists()
    assert snapshot_of(root) == before


# ---- external review (Codex, 2026-10-15) regressions: a rollback is declared only on sha256 proof --------------------------

def test_review_defect_1_changed_bytes_with_size_and_mtime_preserved_are_caught_and_restored(tmp_path):
    root = live(tmp_path)
    original = (root / 'ranks_python.json').stat()
    txn = quiet(CT.begin, root, run_id='metadata-preserving')
    path = root / 'ranks_python.json'
    path.write_bytes(b'{"v": 2}')                                        # same length as {"v": 1}
    os.utime(path, ns=(original.st_mtime_ns, original.st_mtime_ns))     # and the modification time put back
    assert (path.stat().st_size, path.stat().st_mtime_ns) == (original.st_size, original.st_mtime_ns)
    assert quiet(txn.rollback, 'metadata-preserving write') is True
    assert path.read_bytes() == b'{"v": 1}'                             # hashing found it; size/mtime were never trusted
    record = json.loads((Path(txn.dir) / 'journal.json').read_text())
    assert record['state'] == CT.ROLLED_BACK and 'ranks_python.json' in record['rollback']['restored']


def test_review_defect_1_the_before_image_is_kept_unless_every_path_verified(tmp_path, monkeypatch):
    root = live(tmp_path)
    txn = quiet(CT.begin, root, run_id='unverified')
    put(root, 'ranks_python.json', '{"v": 2}')
    real_replace = CT._replace
    monkeypatch.setattr(CT, '_replace', lambda src, dst: os.unlink(src) if str(src).endswith('.rollback.tmp')
                        else real_replace(src, dst))                       # the restore silently never lands; the journal does
    with pytest.raises(CT.RollbackFailed, match='ROLLBACK_FAILED'):
        quiet(txn.rollback, 'restore did not land')
    assert txn.state == CT.ROLLBACK_FAILED
    assert (Path(txn.dir) / 'files' / 'ranks_python.json').read_bytes() == b'{"v": 1}'   # the before-image survives
    assert json.loads((Path(txn.dir) / 'journal.json').read_text())['state'] == CT.ROLLBACK_FAILED


def test_review_defect_1_a_damaged_before_image_never_overwrites_live_bytes(tmp_path):
    root = live(tmp_path)
    txn = quiet(CT.begin, root, run_id='damaged-image')
    put(root, 'ranks_python.json', '{"v": 2}')
    (Path(txn.dir) / 'files' / 'ranks_python.json').write_bytes(b'garbage')
    with pytest.raises(CT.RollbackFailed):
        quiet(txn.rollback, 'test')
    assert (root / 'ranks_python.json').read_bytes() == b'{"v": 2}'      # live was left alone, not replaced with garbage


def test_review_defect_2_an_unrestorable_tracked_mutation_fails_the_rollback_loudly(tmp_path):
    root = live(tmp_path)
    txn = quiet(CT.begin, root, run_id='tracked-mutated')
    put(root, 'ranks_python.json', '{"v": 2}')
    put(root, 'data/stance/rank_honors/gen1/a.json', '{"h": "rewritten"}')       # an immutable generation file changed
    with pytest.raises(CT.RollbackFailed, match='data/stance/rank_honors/gen1/a.json'):
        quiet(txn.rollback, 'tracked store mutated')
    assert txn.state == CT.ROLLBACK_FAILED
    record = json.loads((Path(txn.dir) / 'journal.json').read_text())
    assert record['state'] == CT.ROLLBACK_FAILED
    assert record['rollback']['unrestorable_modified'] == ['data/stance/rank_honors/gen1/a.json']
    assert (Path(txn.dir) / 'files').is_dir()                                     # before-image kept
    assert (root / 'ranks_python.json').read_bytes() == b'{"v": 1}'               # the restorable part WAS restored


def test_review_defect_2_recovery_does_not_accept_a_failed_rollback_and_refuses_to_start(tmp_path):
    root = live(tmp_path)
    txn = quiet(CT.begin, root, run_id='tracked-missing')
    (root / 'data/stance/rank_honors/gen1/a.json').unlink()                       # an immutable generation file vanished
    with pytest.raises(CT.RollbackFailed):
        quiet(txn.rollback, 'test')
    with pytest.raises(CT.RollbackFailed, match='ROLLBACK_FAILED'):
        quiet(CT.recover, root)                                                    # not silently accepted
    with pytest.raises(CT.RollbackFailed):
        with contextlib.redirect_stdout(io.StringIO()):
            with CT.transaction(root, run_id='next-run'):
                raise AssertionError('a new transaction must not open over an unresolved failed rollback')
    # resolution is the operator's: repair the file by hand, discard the failed transaction, and the next start is clean
    put(root, 'data/stance/rank_honors/gen1/a.json', '{"h": 1}')
    shutil.rmtree(txn.dir)
    assert quiet(CT.recover, root) == []


def test_review_defect_3_a_tracked_path_changed_with_size_and_mtime_preserved_fails_the_rollback(tmp_path):
    root = live(tmp_path)
    tracked = root / 'data/stance/rank_honors/gen1/a.json'
    original = tracked.stat()
    txn = quiet(CT.begin, root, run_id='tracked-metadata-preserving')
    assert txn.journal['tracked']['data/stance/rank_honors/gen1/a.json']['sha256'] == hashlib.sha256(b'{"h": 1}').hexdigest()
    tracked.write_bytes(b'{"h": 2}')                                      # same length as {"h": 1}
    os.utime(tracked, ns=(original.st_mtime_ns, original.st_mtime_ns))    # and the modification time put back
    assert (tracked.stat().st_size, tracked.stat().st_mtime_ns) == (original.st_size, original.st_mtime_ns)
    with pytest.raises(CT.RollbackFailed, match='rank_honors/gen1/a.json'):
        quiet(txn.rollback, 'tracked metadata-preserving write')
    assert txn.state == CT.ROLLBACK_FAILED and (Path(txn.dir) / 'files').is_dir()      # before-image kept
    assert json.loads((Path(txn.dir) / 'journal.json').read_text())['rollback']['unrestorable_modified'] == [
        'data/stance/rank_honors/gen1/a.json']
    with pytest.raises(CT.RollbackFailed):                                 # and recovery does not accept it either
        quiet(CT.recover, root)


def test_a_tracked_path_that_was_only_touched_keeps_its_bytes_and_gets_its_mtime_back(tmp_path):
    root = live(tmp_path)
    tracked = root / 'data/stance/rank_honors/gen1/a.json'
    original = tracked.stat()
    txn = quiet(CT.begin, root, run_id='touched')
    os.utime(tracked, ns=(original.st_mtime_ns + 5_000_000_000, original.st_mtime_ns + 5_000_000_000))
    assert quiet(txn.rollback, 'touch only') is True
    assert tracked.stat().st_mtime_ns == original.st_mtime_ns and tracked.read_bytes() == b'{"h": 1}'


def test_a_rollback_that_cannot_be_proven_refuses_the_next_run(tmp_path):
    root = live(tmp_path)
    txn = quiet(CT.begin, root, run_id='broken')
    put(root, 'ranks_python.json', '{"v": 2}')
    (Path(txn.dir) / 'files' / 'ranks_python.json').unlink()           # the before-image itself is damaged
    with pytest.raises(CT.RollbackFailed):
        quiet(txn.rollback, 'test')
    assert txn.state == CT.ROLLBACK_FAILED
    with pytest.raises(RuntimeError, match='ROLLBACK_FAILED'):
        quiet(CT.recover, root)


def test_free_space_gate_refuses_and_reports_the_number(tmp_path, monkeypatch):
    root = live(tmp_path)
    before = snapshot_of(root)
    monkeypatch.setenv(CT.MIN_FREE_GB_ENV, '30')
    usage = shutil.disk_usage(str(tmp_path))
    monkeypatch.setattr(shutil, 'disk_usage', lambda _p: type(usage)(int(100e9), int(80e9), int(31e9) - 1))
    # snapshot is ~100 bytes, so 31e9-1 free leaves ~31 GB: allowed. Shrink free below the floor:
    quiet(CT.begin, root, run_id='roomy').rollback('test')
    monkeypatch.setattr(shutil, 'disk_usage', lambda _p: type(usage)(int(100e9), int(80e9), int(21.06e9)))
    with pytest.raises(RuntimeError) as caught:
        quiet(CT.begin, root, run_id='tight')
    message = str(caught.value)
    assert '21.06 GB' in message and '30 GB' in message and 'refused to start' in message
    assert snapshot_of(root) == before                                  # refused before writing anything to LIVE
    assert not [n for n in journals(root) if n.endswith('tight')]


def test_orchestrator_state_and_dead_letters_are_never_restored_or_deleted(tmp_path):
    root = live(tmp_path)
    txn = quiet(CT.begin, root, run_id='state')
    put(root, 'data/engine/congress_pipeline_status.json', '{"parent": "heartbeat"}')    # parent process keeps writing it
    put(root, 'data/engine/congress_pipeline_live.log', 'new log line')
    put(root, 'data/engine/dead_letter/gold_labels.jsonl', '{"id": "x1"}\n{"id": "x2"}\n')   # an alert raised mid-chain
    put(root, 'data/engine/source_freshness.json', '{"fec_money": {"status": "STALE_LABELLED"}}')
    put(root, 'ranks_python.json', '{"v": 2}')
    quiet(txn.rollback, 'test')
    assert (root / 'ranks_python.json').read_bytes() == b'{"v": 1}'
    assert json.loads((root / 'data/engine/congress_pipeline_status.json').read_text()) == {'parent': 'heartbeat'}
    assert (root / 'data/engine/congress_pipeline_live.log').exists()
    assert (root / 'data/engine/dead_letter/gold_labels.jsonl').read_text().count('\n') == 2
    assert (root / 'data/engine/source_freshness.json').exists()


def test_tracked_store_new_files_go_modified_and_missing_ones_are_reported_loudly(tmp_path):
    root = live(tmp_path)
    txn = quiet(CT.begin, root, run_id='tracked')
    put(root, 'data/stance/rank_honors/gen1/a.json', '{"h": "rewritten"}')           # an immutable generation was modified
    put(root, 'data/stance/rank_honors/gen1/new.json', '{}')
    with pytest.raises(CT.RollbackFailed):                                              # unrestorable -> the rollback is NOT proven
        quiet(txn.rollback, 'test')
    report = json.loads((Path(txn.dir) / 'journal.json').read_text())['rollback']
    assert report['unrestorable_modified'] == ['data/stance/rank_honors/gen1/a.json']
    assert report['removed'] == ['data/stance/rank_honors/gen1/new.json']
    assert not (root / 'data/stance/rank_honors/gen1/new.json').exists()


def test_a_journal_from_another_root_is_left_alone(tmp_path, monkeypatch):
    shared = tmp_path / 'shared_txn'
    monkeypatch.setenv(CT.TXN_DIR_ENV, str(shared))
    a, b = live(tmp_path / 'A'), live(tmp_path / 'B')
    quiet(CT.begin, a, run_id='from-a')
    put(a, 'ranks_python.json', '{"v": "A changed"}')
    assert quiet(CT.recover, b) == []                                   # B must not restore A's bytes into B
    assert (b / 'ranks_python.json').read_bytes() == b'{"v": 1}'
    assert quiet(CT.recover, a) == ['from-a']
    assert (a / 'ranks_python.json').read_bytes() == b'{"v": 1}'


def test_a_journal_that_cannot_be_finalized_never_makes_the_next_start_revert_a_good_run(tmp_path, monkeypatch):
    root = live(tmp_path)
    txn = quiet(CT.begin, root, run_id='disk-full')
    put(root, 'ranks_python.json', '{"v": 2}')
    real = CT._write_json

    def disk_full(*_a, **_k):
        raise OSError(28, 'No space left on device')

    monkeypatch.setattr(CT, '_write_json', disk_full)
    quiet(txn.commit)                                                   # the chain succeeded; finalizing the journal failed
    monkeypatch.setattr(CT, '_write_json', real)
    assert txn.state == CT.COMMITTED
    assert quiet(CT.recover, root) == []                                # the OPEN journal was dropped, not left to be rolled back
    assert (root / 'ranks_python.json').read_bytes() == b'{"v": 2}'


def test_a_staging_directory_inside_a_copy_tree_never_snapshots_or_deletes_itself(tmp_path, monkeypatch):
    root = live(tmp_path)
    monkeypatch.setenv(CT.TXN_DIR_ENV, str(root / 'data' / 'members' / '_txn'))
    txn = quiet(CT.begin, root, run_id='nested')
    assert not [rel for rel in txn.journal['files'] if '_txn' in rel]
    put(root, 'ranks_python.json', '{"v": 2}')
    assert quiet(txn.rollback, 'test') is True and txn.state == CT.ROLLED_BACK
    assert (Path(txn.dir) / 'journal.json').is_file()                   # the rollback's new-file sweep spared its own journal


def test_default_staging_is_the_melange_data_root_outside_the_tree(tmp_path, monkeypatch):
    root = live(tmp_path / 'repo')
    data_root = tmp_path / 'melange-data'
    data_root.mkdir()
    monkeypatch.setenv(CT.DATA_ROOT_ENV, str(data_root))
    assert Path(CT.txn_base(root)) == data_root / 'chain_txn'
    txn = quiet(CT.begin, root, run_id='off-tree')
    assert Path(txn.dir).parent == data_root / 'chain_txn'
    assert not (root / 'data/engine/chain_txn').exists()              # nothing staged inside the (OneDrive) tree
    put(root, 'ranks_python.json', '{"v": 2}')
    assert quiet(txn.rollback, 'test') is True
    assert (root / 'ranks_python.json').read_bytes() == b'{"v": 1}'


def test_a_missing_data_root_falls_back_in_tree(tmp_path, monkeypatch):
    root = live(tmp_path)
    monkeypatch.setenv(CT.DATA_ROOT_ENV, str(tmp_path / 'absent'))
    assert Path(CT.txn_base(root)) == root / 'data/engine/chain_txn'


def test_recovery_also_finishes_a_journal_left_in_tree_after_the_default_moved(tmp_path, monkeypatch):
    root = live(tmp_path / 'repo')
    monkeypatch.setenv(CT.DATA_ROOT_ENV, str(tmp_path / 'absent'))
    quiet(CT.begin, root, run_id='legacy')                             # staged in-tree, then the run died
    put(root, 'ranks_python.json', '{"v": 2}')
    data_root = tmp_path / 'melange-data'
    data_root.mkdir()
    monkeypatch.setenv(CT.DATA_ROOT_ENV, str(data_root))               # the default now points off-tree
    assert quiet(CT.recover, root) == ['legacy']
    assert (root / 'ranks_python.json').read_bytes() == b'{"v": 1}'


def test_only_the_newest_two_finalized_transactions_are_kept(tmp_path, monkeypatch):
    root = live(tmp_path)
    ticks = iter(range(100))
    monkeypatch.setattr(CT, '_stamp', lambda: '20261015T0000%02d000000Z' % next(ticks))
    for n in range(4):
        quiet(CT.begin, root, run_id='r%d' % n).commit()
    assert len(journals(root)) == CT.KEEP == 2
    assert sorted(j['run_id'] for j in journals(root).values()) == ['r2', 'r3']


def test_transaction_context_rolls_back_unless_explicitly_committed(tmp_path):
    root = live(tmp_path)
    before = snapshot_of(root)
    with contextlib.redirect_stdout(io.StringIO()):
        with CT.transaction(root, run_id='ctx-return') as txn:        # a bare `return 1` is just falling out of the block
            put(root, 'ranks_python.json', '{"v": 2}')
        assert snapshot_of(root) == before and txn.state == CT.ROLLED_BACK
        with pytest.raises(KeyboardInterrupt):
            with CT.transaction(root, run_id='ctx-ctrl-c') as txn:
                put(root, 'ranks_python.json', '{"v": 3}')
                raise KeyboardInterrupt
        assert snapshot_of(root) == before and txn.state == CT.ROLLED_BACK
        with CT.transaction(root, run_id='ctx-commit') as txn:
            put(root, 'ranks_python.json', '{"v": 4}')
            txn.commit()
    assert (root / 'ranks_python.json').read_bytes() == b'{"v": 4}' and txn.state == CT.COMMITTED


# ------------------------------------------------------------------------------------------------ labelled degrade helpers

def test_age_status_vocabulary():
    assert CT.age_status(86400, 8, 'x') == CT.FRESH
    assert CT.age_status(8 * 86400, 8, 'x') == CT.FRESH
    assert CT.age_status(8 * 86400 + 1, 8, 'x') == CT.STALE_LABELLED
    assert CT.age_status(-299, 8, 'x') == CT.FRESH
    with pytest.raises(ValueError, match='future stamp'):
        CT.age_status(-301, 8, 'future stamp')


def test_note_freshness_merges_only_its_entry_and_tolerates_absence(tmp_path):
    root = tmp_path
    CT._NOTED.clear()
    lines = []
    assert CT.note_freshness(root, 'fec_money', CT.STALE_LABELLED, checked_at='t0', window_days=8, producer='p', log=lines.append) is False
    assert lines and 'STALE_LABELLED' in lines[0]                        # labelled on stdout even with no file
    assert not (root / 'data/engine/source_freshness.json').exists()     # never created: another phase owns it
    put(root, 'data/engine/source_freshness.json', json.dumps({'other': {'status': 'FRESH'}}))
    CT._NOTED.clear()
    assert CT.note_freshness(root, 'fec_money', CT.STALE_LABELLED, checked_at='t0', window_days=8, producer='p', log=lines.append) is True
    doc = json.loads((root / 'data/engine/source_freshness.json').read_text())
    assert doc['other'] == {'status': 'FRESH'} and doc['fec_money']['status'] == CT.STALE_LABELLED
    CT._NOTED.clear()
    CT.note_freshness(root, 'fec_money', CT.FRESH, checked_at='t1', window_days=8, producer='p', log=lines.append)   # clears the label
    assert json.loads((root / 'data/engine/source_freshness.json').read_text())['fec_money']['status'] == CT.FRESH
    put(root, 'data/engine/source_freshness.json', '{broken')
    CT._NOTED.clear()
    assert CT.note_freshness(root, 'fec_money', CT.FRESH, checked_at='t2', window_days=8, producer='p', log=lines.append) is False
    assert not list((root / 'data/engine').glob('*.tmp'))


# ------------------------------------------------------------------------------------------------ run_pipeline integration

STEP = '''
import os, sys
root = os.environ["CHAIN_TEST_ROOT"]
for arg in sys.argv[1:]:
    kind, _, rest = arg.partition(":")
    if kind == "write":
        rel, _, content = rest.partition("=")
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
    elif kind == "exit":
        sys.exit(int(rest))
'''


@pytest.fixture
def chain(tmp_path, monkeypatch):
    """The real fec/run_pipeline.main over a fake chain of tiny scripts, every path bound to a scratch root."""
    spec = importlib.util.spec_from_file_location('run_pipeline_txn_under_test', FEC / 'run_pipeline.py')
    rp = importlib.util.module_from_spec(spec)
    saved_stdout = sys.stdout
    spec.loader.exec_module(rp)                    # module level only resolves paths and defines functions
    sys.stdout = saved_stdout
    root = live(tmp_path / 'root')
    script = tmp_path / 'step.py'
    script.write_text(STEP, encoding='utf-8')
    monkeypatch.setenv('CHAIN_TEST_ROOT', str(root))
    events = []
    for name, value in {
        'MB_ROOT': str(root), '_producer_path': lambda name: str(script), 'preflight_dependencies': lambda: None,
        'pipeline_run_lock': contextlib.nullcontext, '_gb': lambda *a, **k: events.append((a, k)),
        'OPTIONAL_INPUTS': {}, 'TOLERANT_STEPS': set(), 'LAST_GOOD': {}, 'LAST_GOOD_RECEIPT': {},
        'TOLERATED_RECEIPT': 'data/engine/chain_tolerated_failures.json',
    }.items():
        monkeypatch.setattr(rp, name, value)
    monkeypatch.setattr(rp.bounded_subprocess, 'CHAIN_STEP_TIMEOUT_SECONDS', 30)

    def run(steps, **overrides):
        for name, value in overrides.items():
            monkeypatch.setattr(rp, name, value)
        monkeypatch.setattr(rp, 'CHAIN', steps)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = rp.main([])
        return code, buffer.getvalue()

    return type('Chain', (), {'rp': rp, 'root': root, 'run': staticmethod(run), 'events': events})


def test_crash_at_a_rank_step_mid_chain_restores_live_byte_identical_and_removes_new_files(chain):
    before = snapshot_of(chain.root)
    code, out = chain.run([
        ('calibration_guard.py', ['write:ranks_python.json={"v": 2}', 'write:data/engine/fresh_output.json={}']),
        ('compute_pillars.py', ['write:dashboard_data.json={"d": 2}', 'write:brand_new_surface.html=<p>', 'exit:1']),
        ('audit_full.py', ['write:never_reached.json={}']),
    ])
    assert code == 1
    assert snapshot_of(chain.root) == before
    states = [j['state'] for j in journals(chain.root).values()]
    assert states == [CT.ROLLED_BACK]
    assert 'ROLLED_BACK' in out


def test_a_clean_chain_commits_and_a_later_failed_chain_returns_to_that_vintage_not_the_original(chain):
    code, _ = chain.run([('calibration_guard.py', ['write:ranks_python.json={"v": 2}']),
                         ('audit_full.py', ['write:data/engine/audit.json={"ok": true}'])])
    assert code == 0
    committed = snapshot_of(chain.root)
    assert committed['ranks_python.json'][0] == b'{"v": 2}'
    code, _ = chain.run([('calibration_guard.py', ['write:ranks_python.json={"v": 3}']),
                         ('compute_pillars.py', ['exit:7'])])
    assert code == 1
    assert snapshot_of(chain.root) == committed
    assert sorted(j['state'] for j in journals(chain.root).values()) == [CT.COMMITTED, CT.ROLLED_BACK]


def test_tolerated_display_only_failure_continues_and_is_marked_last_good(chain):
    artifact = put(chain.root, 'lobby-interactions.html', '<p>last good</p>')
    code, out = chain.run(
        [('compute_pillars.py', ['write:ranks_python.json={"v": 2}']),
         ('make_lobby_interactions_html.py', ['exit:3']),               # fails without touching its artifact
         ('make_combined.py', ['write:Moneyball.html=<html>two</html>']),
         ('audit_full.py', [])],
        TOLERANT_STEPS={'make_lobby_interactions_html.py'},
        LAST_GOOD={'make_lobby_interactions_html.py': 'lobby-interactions.html'})
    assert code == 0 and 'tolerated' in out and 'PIPELINE COMPLETE' in out
    assert artifact.read_text() == '<p>last good</p>'
    assert (chain.root / 'ranks_python.json').read_text() == '{"v": 2}'          # the rank step's output stands
    assert (chain.root / 'Moneyball.html').read_text() == '<html>two</html>'     # and so do the steps after the failure
    receipt = json.loads((chain.root / 'data/engine/chain_tolerated_failures.json').read_text())
    assert [(e['step'], e['status'], e['returncode']) for e in receipt] == [('make_lobby_interactions_html.py', 'LAST_GOOD', 3)]
    assert receipt[0]['last_good_path'] == str(artifact) and 'label' in receipt[0]
    assert [j['state'] for j in journals(chain.root).values()] == [CT.COMMITTED]


def test_a_display_step_that_touched_its_artifact_before_failing_is_not_tolerated(chain):
    put(chain.root, 'lobby-interactions.html', '<p>last good</p>', 1_700_000_009_000_000_000)
    before = snapshot_of(chain.root)
    code, _ = chain.run(
        [('compute_pillars.py', ['write:ranks_python.json={"v": 2}']),
         ('make_lobby_interactions_html.py', ['write:lobby-interactions.html=<p>half</p>', 'exit:1']),
         ('audit_full.py', [])],
        TOLERANT_STEPS={'make_lobby_interactions_html.py'},
        LAST_GOOD={'make_lobby_interactions_html.py': 'lobby-interactions.html'})
    assert code == 1
    assert snapshot_of(chain.root) == before                  # fail closed AND rolled back, including the half-written page


def test_a_tolerant_step_with_no_last_good_artifact_is_not_tolerated(chain):
    code, _ = chain.run([('make_lobby_interactions_html.py', ['exit:1']), ('audit_full.py', [])],
                        TOLERANT_STEPS={'make_lobby_interactions_html.py'},
                        LAST_GOOD={'make_lobby_interactions_html.py': 'lobby-interactions.html'})
    assert code == 1


def test_a_failing_terminal_audit_does_not_undo_a_complete_vintage(chain):
    code, out = chain.run([('compute_pillars.py', ['write:ranks_python.json={"v": 2}']),
                           ('audit_full.py', ['write:data/engine/audit_report.json={"failed": 1}', 'exit:1'])])
    assert code == 1
    assert (chain.root / 'ranks_python.json').read_text() == '{"v": 2}'
    assert (chain.root / 'data/engine/audit_report.json').exists()
    (record,) = journals(chain.root).values()
    assert record['state'] == CT.COMMITTED and 'terminal verifier' in record['note']


def test_keyboard_interrupt_restores_live_and_propagates(chain, monkeypatch):
    before = snapshot_of(chain.root)
    calls = {'n': 0}
    real = chain.rp.run_bounded

    def interrupt_second(argv, **kw):
        calls['n'] += 1
        if calls['n'] == 2:
            raise KeyboardInterrupt
        return real(argv, **kw)

    monkeypatch.setattr(chain.rp, 'run_bounded', interrupt_second)
    with pytest.raises(KeyboardInterrupt):
        chain.run([('calibration_guard.py', ['write:ranks_python.json={"v": 2}']), ('compute_pillars.py', [])])
    assert snapshot_of(chain.root) == before


def test_an_unfinalized_journal_is_restored_before_the_new_run_starts(chain):
    before = snapshot_of(chain.root)
    dead = CT.begin(str(chain.root), run_id='killed-run')           # a run that was killed mid-chain
    put(chain.root, 'ranks_python.json', '{"v": "half written"}')
    put(chain.root, 'orphan.json', '{}')
    del dead
    code, out = chain.run([('calibration_guard.py', ['write:ranks_python.json={"v": 9}']), ('audit_full.py', [])])
    assert code == 0 and 'recovered' in out
    assert not (chain.root / 'orphan.json').exists()
    assert (chain.root / 'ranks_python.json').read_text() == '{"v": 9}'
    assert sorted(j['state'] for j in journals(chain.root).values()) == [CT.COMMITTED, CT.ROLLED_BACK]
    assert before['dashboard_data.json'] == snapshot_of(chain.root)['dashboard_data.json']


def test_low_disk_refuses_the_run_before_any_step_executes(chain, monkeypatch):
    monkeypatch.setenv(CT.MIN_FREE_GB_ENV, '30')
    usage = shutil.disk_usage(str(chain.root))
    monkeypatch.setattr(shutil, 'disk_usage', lambda _p: type(usage)(int(100e9), int(90e9), int(12.5e9)))
    before = snapshot_of(chain.root)
    code, out = chain.run([('calibration_guard.py', ['write:ranks_python.json={"v": 2}'])])
    assert code == 1 and snapshot_of(chain.root) == before and not journals(chain.root)


def test_review_defect_2_a_failed_rollback_blocks_the_next_chain_run_until_resolved(chain, capsys):
    txn = quiet(CT.begin, str(chain.root), run_id='failed-earlier')
    put(chain.root, 'data/stance/rank_honors/gen1/a.json', '{"h": "rewritten"}')
    with pytest.raises(CT.RollbackFailed):
        quiet(txn.rollback, 'tracked store mutated')
    before = snapshot_of(chain.root)
    code, out = chain.run([('calibration_guard.py', ['write:ranks_python.json={"v": 9}'])])
    assert code == 1 and snapshot_of(chain.root) == before            # refused: not one step ran, nothing was written
    assert 'ROLLBACK_FAILED' in capsys.readouterr().err
    put(chain.root, 'data/stance/rank_honors/gen1/a.json', '{"h": 1}')   # the operator repairs the file and discards the directory
    shutil.rmtree(txn.dir)
    code, _ = chain.run([('calibration_guard.py', ['write:ranks_python.json={"v": 9}']), ('audit_full.py', [])])
    assert code == 0 and (chain.root / 'ranks_python.json').read_text() == '{"v": 9}'
