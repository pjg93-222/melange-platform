"""The apply path of the refine pass (Coordinator 2026-10-21: approved).  Dry-run is the default; an apply restores a quarantined row to
`speeches` under its ORIGINAL rowid, or updates the former member it is parked for; every change is logged before/after in
speeches_quarantine_log under a batch id, and undo_refine(batch) puts the database back exactly.  The evidence kind is recorded per row,
so the CHRG 'waived-on' risk (evidence_kind = hearing_roster) is visible in the ledger.  Synthetic roster from conftest.py."""
import argparse
import csv
import gzip
import json
import sqlite3

import pytest

PKG = 'CREC-2023-03-14'
DATE = '2023-03-14'


def lorem(tag):
    return ('Madam Speaker, %s: I rise today to speak on a matter of importance to the people of my district, who sent me here to work for them, '
            'and I ask my colleagues to join me in supporting the families and workers who depend on this body to act.' % tag)


T1, T2, T3 = lorem('one'), lorem('two'), lorem('three')


def verdict(rowid, stored, new, proposed, bio, method='roster', reason='chrg_unreproduced', old='ambiguous'):
    return dict(rowid=rowid, package=PKG, source='CREC', reason=reason, stored_member=stored, old_outcome=old, method=method, new_outcome=new,
                proposed_member=proposed, proposed_bioguide=bio, proposed_on_roster=int(new != 'reassign_former'), note='',
                evidence_kind={'roster': 'hearing_roster', 'context': 'recognition_line', 'label': 'exact_turn_label'}.get(method, method))


def make_db(tmp_path):
    con = sqlite3.connect(str(tmp_path / 'sm.db'))
    con.execute('CREATE TABLE speeches(member TEXT,date TEXT,source TEXT,chamber TEXT,package TEXT,nwords INTEGER,text TEXT)')
    for i, m, t in [(1, 'Al Green', T1), (2, 'Clay Higgins', T2), (3, 'Sylvia Garcia', T3), (4, 'Christopher Smith', lorem('four'))]:
        con.execute('INSERT INTO speeches(rowid,member,date,source,chamber,package,nwords,text) VALUES(?,?,?,?,?,?,?,?)', (i, m, DATE, 'CREC', 'house', PKG, len(t.split()), t))
    con.commit()
    return con


def quarantine(tool, con):
    dec = [dict(rowid=1, package=PKG, source='CREC', stored_member='Al Green', reason='namesake_ambiguous', label='Mr GREEN', status='ambiguous', proposed_member=None,
                proposed_bioguide=None, proposed_on_roster=0, note='2 people fit'),
           dict(rowid=2, package=PKG, source='CREC', stored_member='Clay Higgins', reason='state_tag_other_person', label='Mr HIGGINS of New York', status='parked_former',
                proposed_member='Brian Higgins', proposed_bioguide='H001078', proposed_on_roster=0, note='former'),
           dict(rowid=3, package=PKG, source='CREC', stored_member='Sylvia Garcia', reason='unverified', label='Mr GARCIA', status='ambiguous', proposed_member=None,
                proposed_bioguide=None, proposed_on_roster=0, note='')]
    tool['apply_decisions'](con, dec, 'Q1'); con.commit()


def snap(con):
    return (con.execute('SELECT rowid,member,date,source,chamber,package,nwords,text FROM speeches ORDER BY rowid').fetchall(),
            con.execute('SELECT orig_rowid,member,reason,label,outcome,proposed_member,proposed_bioguide,proposed_on_roster,note,status,restored_at,restored_member,evidence_kind,refine_batch '
                        'FROM speeches_quarantine ORDER BY orig_rowid').fetchall())


def test_apply_restores_under_the_original_rowid_reparks_and_logs(tool, tmp_path):
    con = make_db(tmp_path); quarantine(tool, con); tool['refine_table'](con); before = snap(con)
    v = [verdict(1, 'Al Green', 'restore', 'Al Green', 'G000553'),
         verdict(2, 'Clay Higgins', 'reassign_former', 'Brian Higgins II', 'H001078', method='label', reason='state_tag_other_person', old='parked_former'),
         verdict(3, 'Sylvia Garcia', 'reassign_current', 'Jesús García', 'G000586', method='context')]
    res = tool['apply_refine'](con, v, 'R1'); con.commit()
    assert {r['rowid']: r['applied'] for r in res} == {1: 'restored', 2: 'reparked', 3: 'restored'}
    rows = {r[0]: r for r in con.execute('SELECT rowid,member,text FROM speeches')}
    assert rows[1][1:] == ('Al Green', T1) and rows[3][1:] == ('Jesús García', T3) and 2 not in rows          # original rowids, texts intact
    q = {r[0]: r[1:] for r in con.execute('SELECT orig_rowid,status,outcome,proposed_member,restored_member,evidence_kind,refine_batch FROM speeches_quarantine')}
    assert q[1] == ('restored', 'restored', 'Al Green', 'Al Green', 'hearing_roster', 'R1')                      # the waived-on caveat travels with the row
    assert q[2] == ('quarantined', 'parked_former', 'Brian Higgins II', None, 'exact_turn_label', 'R1')
    assert q[3] == ('restored', 'restored', 'Jesús García', 'Jesús García', 'recognition_line', 'R1')
    assert con.execute("SELECT COUNT(*) FROM speeches_quarantine_log WHERE batch_id='R1'").fetchone()[0] == 3
    mid = snap(con)
    again = tool['apply_refine'](con, v, 'R2'); con.commit()                                                     # idempotent
    assert [r['applied'] for r in again] == ['no_change'] * 3 and snap(con) == mid
    tool['undo_refine'](con, 'R1'); con.commit()
    assert snap(con)[0] == before[0]                                                                              # speeches back byte-for-byte
    assert snap(con)[1] == before[1]                                                                              # quarantine rows back to the pre-pass state
    assert {r[0] for r in con.execute("SELECT undone_at IS NOT NULL FROM speeches_quarantine_log WHERE batch_id='R1'")} == {1}


def test_a_restore_is_skipped_when_the_member_already_has_the_turn(tool, tmp_path):
    con = make_db(tmp_path); quarantine(tool, con)
    con.execute('INSERT INTO speeches(rowid,member,date,source,chamber,package,nwords,text) VALUES(900,?,?,?,?,?,?,?)', ('Al Green', DATE, 'CREC', 'house', PKG, 10, T1)); con.commit()
    res = tool['apply_refine'](con, [verdict(1, 'Al Green', 'restore', 'Al Green', 'G000553')], 'R1'); con.commit()
    assert res[0]['applied'] == 'skipped_duplicate' and con.execute('SELECT COUNT(*) FROM speeches WHERE text=?', (T1,)).fetchone()[0] == 1
    assert con.execute('SELECT status FROM speeches_quarantine WHERE orig_rowid=1').fetchone()[0] == 'quarantined'


def test_only_restore_and_reassign_verdicts_change_anything(tool, tmp_path):
    con = make_db(tmp_path); quarantine(tool, con); tool['refine_table'](con); before = snap(con)
    v = [verdict(1, 'Al Green', 'stay_ambiguous', None, None), verdict(2, 'Clay Higgins', 'unchanged', 'Brian Higgins', 'H001078', method='label'),
         verdict(3, 'Sylvia Garcia', 'stay_unresolved', None, None)]
    assert [r['applied'] for r in tool['apply_refine'](con, v, 'R1')] == ['no_change'] * 3 and snap(con) == before


def test_refine_command_dry_run_then_apply_then_undo(tool, tmp_path):
    con = make_db(tmp_path); quarantine(tool, con); tool['refine_table'](con); before = snap(con); con.close()
    cache = tmp_path / 'cache'; cache.mkdir()
    page = ('    The SPEAKER pro tempore. The Chair recognizes the gentleman from Texas (Mr. GREEN) for 5 minutes.\n    Mr. GREEN. %s\n'
            '    Mr. HIGGINS of New York. %s\n' % (T1, T2))
    with gzip.open(cache / (PKG + '.json.gz'), 'wt', encoding='utf-8') as f: json.dump([('CREC-2023-03-14-pt1-PgH1.htm', page)], f)
    tool['DB'] = str(tmp_path / 'sm.db')
    def args(apply): return argparse.Namespace(cache=str(cache), out=str(tmp_path / 'out'), apply=apply)
    assert tool['refine_cmd'](args(False)) == 0
    led = {int(r['rowid']): r for r in csv.DictReader(open(tmp_path / 'out' / 'refine_ledger.csv', encoding='utf-8-sig'))}
    assert led[1]['new_outcome'] == 'restore' and led[1]['evidence_kind'] == 'recognition_line' and led[1]['applied'] == 'dry_run'
    assert led[1]['final_location'] == 'speeches' and led[1]['final_member'] == 'Al Green' and led[2]['new_outcome'] == 'unchanged'
    con = sqlite3.connect(tool['DB']); assert snap(con) == before; con.close()                                      # a dry run writes nothing
    assert tool['refine_cmd'](args(True)) == 0
    led = {int(r['rowid']): r for r in csv.DictReader(open(tmp_path / 'out' / 'refine_ledger.csv', encoding='utf-8-sig'))}
    batch = led[1]['batch']
    con = sqlite3.connect(tool['DB'])
    assert led[1]['applied'] == 'restored' and con.execute('SELECT member FROM speeches WHERE rowid=1').fetchone() == ('Al Green',)
    con.close()
    assert tool['refine_cmd'](args(True)) == 0                                                                      # rerun: nothing left to restore
    assert not [r for r in csv.DictReader(open(tmp_path / 'out' / 'refine_ledger.csv', encoding='utf-8-sig')) if r['applied'] in ('restored', 'reparked')]
    tool['undo_cmd'](argparse.Namespace(batch=None, refine_batch=batch, apply=True))
    con = sqlite3.connect(tool['DB']); assert snap(con)[0] == before[0]


def test_guard_refuses_to_apply_with_mojibaked_roster_names(tool):
    tool['guard_roster']()                                                           # the synthetic roster is clean
    tool['CR'].C['Nydia VelÃ¡zquez'] = {}                                            # what a cp1252 read of raw-UTF-8 congestion.json produces
    try:
        with pytest.raises(SystemExit, match='mojibake'):
            tool['guard_roster']()
    finally:
        del tool['CR'].C['Nydia VelÃ¡zquez']


def test_reconciled_inserts_and_relabels_are_ledgered_and_exactly_reversible(tool, tmp_path):
    con = make_db(tmp_path); before = con.execute('SELECT rowid,member,date,source,chamber,package,nwords,text FROM speeches ORDER BY rowid').fetchall()
    new_row = ('Jesús García', DATE, 'CREC', 'house', PKG, 30, lorem('five'))
    led = tool['apply_reconciled'](con, [new_row], [(3, 'Sylvia Garcia', 'Jesús García')], 'I1'); con.commit()
    assert [(r[0], r[1], r[3]) for r in led] == [('I1', 'insert', 'Jesús García'), ('I1', 'relabel', 'Jesús García')]
    assert led[0][2] == 5 and led[1][2] == 3 and led[1][6] == 'Sylvia Garcia'                                      # new rowid recorded; relabel remembers who it was
    assert con.execute('SELECT member FROM speeches WHERE rowid=3').fetchone() == ('Jesús García',) and con.execute('SELECT COUNT(*) FROM speeches').fetchone()[0] == 5
    assert tool['undo_applied'](con, led) == {'deleted': 1, 'unrelabelled': 1, 'skipped': 0}; con.commit()
    assert con.execute('SELECT rowid,member,date,source,chamber,package,nwords,text FROM speeches ORDER BY rowid').fetchall() == before     # back byte-for-byte
    assert tool['undo_applied'](con, led) == {'deleted': 0, 'unrelabelled': 0, 'skipped': 2}                       # idempotent: nothing left to reverse


def test_undo_applied_will_not_delete_a_row_whose_text_changed_since(tool, tmp_path):
    con = make_db(tmp_path)
    led = tool['apply_reconciled'](con, [('Jesús García', DATE, 'CREC', 'house', PKG, 30, lorem('five'))], [], 'I1'); con.commit()
    con.execute('UPDATE speeches SET text=? WHERE rowid=?', ('edited by someone else', led[0][2])); con.commit()
    assert tool['undo_applied'](con, led) == {'deleted': 0, 'unrelabelled': 0, 'skipped': 1}
    assert con.execute('SELECT COUNT(*) FROM speeches').fetchone()[0] == 5
