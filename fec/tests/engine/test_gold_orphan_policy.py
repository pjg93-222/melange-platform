"""Gold-label orphan policy (self-healing phase 2, Paul 2026-10-15; precedent D-200, the 2026-10-12 step-17 incident).

A hand label that no longer binds to a source row is skipped, dead-lettered to data/engine/dead_letter/gold_labels.jsonl and
announced -- unless more than 5% of the channel's labels are orphaned, which still fails exactly as it always did. Synthetic
rows and a temp root only; nothing is trained and no live file is read.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))
from stance import own_source_stance_model as osm  # noqa: E402


def labels(n):
    return {'x%04d' % i: {'id': 'x%04d' % i, 'gold_stance': 'oppose', 'categories': 'CFPB', 'member': 'M%d' % i,
                          'date': '2026-01-01', 'text_snippet': 'snippet %d' % i} for i in range(n)}


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    monkeypatch.setattr(osm, 'ROOT', str(tmp_path))
    return tmp_path


def wire(monkeypatch, gold, bound):
    """Speech has `gold`; its source rows bind only `bound`. Every other channel is empty."""
    monkeypatch.setattr(osm, 'filled_gold_rows', lambda channel: dict(gold) if channel == 'speech' else {})
    monkeypatch.setattr(osm, 'source_examples',
                        lambda channel: [SimpleNamespace(row_id=rid, label=None) for rid in bound] if channel == 'speech' else [])
    monkeypatch.setattr(osm, 'ADJUDICATED_RETIRED', {})


def dead_letter(root):
    path = Path(root, 'data/engine/dead_letter/gold_labels.jsonl')
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


def test_one_orphan_in_a_hundred_is_skipped_dead_lettered_and_announced(scratch, monkeypatch, capsys):
    gold = labels(100)
    wire(monkeypatch, gold, [rid for rid in gold if rid != 'x0042'])
    examples = osm.own_gold_examples()
    assert len(examples) == 99 and 'x0042' not in {e.row_id for e in examples}
    (record,) = dead_letter(scratch)
    assert record['id'] == 'x0042' and record['channel'] == 'speech' and record['gold_stance'] == 'oppose'
    assert record['channel_gold_labels'] == 100 and record['channel_orphans'] == 1 and record['first_seen_utc']
    out = capsys.readouterr().out
    assert '!! GOLD ORPHAN SKIPPED (speech): 1 of 100' in out and 'x0042' in out


def test_the_dead_letter_is_append_only_and_deduplicated_across_runs(scratch, monkeypatch):
    gold = labels(100)
    wire(monkeypatch, gold, [rid for rid in gold if rid != 'x0042'])
    osm.own_gold_examples()
    osm.own_gold_examples()                                              # a second run sees the same orphan
    assert [r['id'] for r in dead_letter(scratch)] == ['x0042']
    wire(monkeypatch, gold, [rid for rid in gold if rid not in ('x0042', 'x0007')])
    osm.own_gold_examples()
    assert [r['id'] for r in dead_letter(scratch)] == ['x0042', 'x0007']


def test_ten_orphans_in_a_hundred_still_fail_the_channel(scratch, monkeypatch):
    gold = labels(100)
    wire(monkeypatch, gold, [rid for rid in gold if int(rid[1:]) >= 10])
    with pytest.raises(RuntimeError, match=r'speech gold labels no longer bind to source rows.*10 of 100'):
        osm.own_gold_examples()
    assert dead_letter(scratch) == []                                    # a mass loss is not quietly archived either


def test_the_threshold_is_strictly_more_than_five_percent(scratch, monkeypatch):
    gold = labels(100)
    wire(monkeypatch, gold, [rid for rid in gold if int(rid[1:]) >= 5])  # exactly 5 of 100
    assert len(osm.own_gold_examples()) == 95
    wire(monkeypatch, gold, [rid for rid in gold if int(rid[1:]) >= 6])  # 6 of 100
    with pytest.raises(RuntimeError):
        osm.own_gold_examples()


def test_an_unwritable_dead_letter_fails_closed(scratch, monkeypatch, capsys):
    gold = labels(100)
    wire(monkeypatch, gold, [rid for rid in gold if rid != 'x0042'])
    (scratch / 'data/engine').mkdir(parents=True)
    (scratch / 'data/engine/dead_letter').write_text('a FILE squatting where the directory must go')
    with pytest.raises(RuntimeError, match='no longer bind'):
        osm.own_gold_examples()
    assert 'cannot be dead-lettered' in capsys.readouterr().out


def test_adjudicated_retirements_are_unchanged_and_are_not_counted_as_orphans(scratch, monkeypatch):
    gold = labels(100)
    wire(monkeypatch, gold, [rid for rid in gold if int(rid[1:]) >= 8])
    monkeypatch.setattr(osm, 'ADJUDICATED_RETIRED',
                        {'x%04d' % i: ('speech', 'ratified', 'Paul') for i in range(8)})   # 8 ratified-retired rows
    assert len(osm.own_gold_examples()) == 92
    assert dead_letter(scratch) == []


def run_channel_with(monkeypatch, scratch, gold, bound):
    cfg = osm.CONFIG['speech']
    for rel in (cfg['source'], cfg['gold']):
        target = Path(scratch, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('id\n', encoding='utf-8')
    wire(monkeypatch, gold, bound)
    monkeypatch.setattr(osm, 'gold_map', lambda channel: {rid: 'oppose' for rid in gold})

    class Reached(Exception):
        pass

    def stop(*_a, **_k):
        raise Reached
    monkeypatch.setattr(osm, 'load_or_train', stop)
    return Reached


def test_run_channel_continues_past_one_orphan_and_still_returns_3_for_a_mass_loss(scratch, monkeypatch, capsys):
    gold = labels(100)
    reached = run_channel_with(monkeypatch, scratch, gold, [rid for rid in gold if rid != 'x0042'])
    with pytest.raises(reached):                                         # it got PAST the orphan guard to the model
        osm.run_channel('speech')
    assert [r['id'] for r in dead_letter(scratch)] == ['x0042']
    assert 'GOLD ORPHAN SKIPPED' in capsys.readouterr().out

    wire(monkeypatch, gold, [rid for rid in gold if int(rid[1:]) >= 10])
    assert osm.run_channel('speech') == 3                                # unchanged fail-closed return for >5%
    assert '!! GOLD ORPHANS: 10 of' in capsys.readouterr().out
