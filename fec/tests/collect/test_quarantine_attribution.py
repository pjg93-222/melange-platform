"""Quarantine + fix pass over the stored CREC/CHRG rows the current resolver no longer reproduces (Paul, 2026-10-20:
"quarantine the misattributed rows, but aim to fix them").  Loads only the pure functions, against the synthetic roster in
conftest.py; the live roster, database and network are never touched."""
import csv
import sqlite3
from pathlib import Path

import pytest

FEC = Path(__file__).resolve().parents[2]


def text(tag):
    return ('Mr. Speaker, %s: I rise today to speak on a matter of importance to the people of my district, who sent me here to work '
            'for them, and I ask my colleagues to join me in supporting the families and workers who depend on this body to act.' % tag)


def page(*turns):
    return '\n'.join('    %s. %s' % (label, t) for label, t in turns) + '\n'


# ---- the historical (all-HSall) resolver ---------------------------------------------------------------------------
@pytest.mark.parametrize('sur,st,chamber,congress,year,expected', [
    ('HIGGINS', 'New York', 'house', 118, None, {'H001078'}),            # Brian Higgins: a real, unique, off-roster person
    ('HIGGINS', 'Louisiana', 'house', 118, None, {'H001077'}),
    ('HIGGINS', None, 'house', 118, None, {'H001077', 'H001078'}),       # no state: both
    ('SCOTT', 'Georgia', 'house', 118, None, {'S001189', 'S001190'}),    # Austin and David Scott
    ('AUSTIN SCOTT', 'Georgia', 'house', 118, None, {'S001189'}),
    ('DAVID SCOTT', 'Georgia', 'house', 118, None, {'S001190'}),
    ('DANNY K DAVIS', None, 'house', 118, None, {'D000096'}),            # initial dropped, first name qualifies
    ('DAVIS', None, 'house', 118, None, {'D000096', 'D000230', 'D000598'}),
    ('GRIJALVA', None, 'house', 119, 2025, {'G000551', 'G000606'}),      # either could be speaking in 2025
    ('GRIJALVA', None, 'house', 119, 2026, {'G000606'}),                 # Raul died in 2025
    ('DAVID P ROE', 'Tennessee', 'house', 116, None, {'R000601'}),
    ('DAVID P ROE', 'Tennessee', 'house', 118, None, set()),             # Roe did not serve the 118th
    ('NOBODY', None, 'house', 118, None, set()),
])
def test_hist_persons(tool, sur, st, chamber, congress, year, expected):
    assert tool['hist_persons'](sur, st, chamber, congress, year) == expected


def test_hist_names_are_given_name_first(tool):
    names = tool['hist_names']()
    assert names['H001078'] == 'Brian Higgins' and names['G000551'].endswith('Grijalva') and names['V000135'] == 'Derrick Van Orden'


# ---- why the current resolver refuses a label ----------------------------------------------------------------------
@pytest.mark.parametrize('sur,st,chamber,congress,year,code', [
    ('HIGGINS', 'New York', 'house', 118, 2023, 'state_tag_other_person'),     # Clay Higgins (LA) is the only roster Higgins
    ('SCOTT', 'Georgia', 'house', 118, 2023, 'namesake_ambiguous'),             # Austin and David Scott, both Georgia
    ('SMITH', None, 'house', 118, 2023, 'namesake_ambiguous'),
    ('DAVID P ROE', 'Tennessee', 'house', 118, 2023, 'unverified'),             # no roster Roe at all
    ('GRIJALVA', None, 'house', 118, 2023, 'not_serving'),                      # Adelita is on the roster but not in the 118th
])
def test_refusal_reason_codes(tool, sur, st, chamber, congress, year, code):
    assert tool['refusal_reason'](sur, st, chamber, congress, year) == code


# ---- the fix pass: one package, five stored rows ---------------------------------------------------------------------
PKG, DATE = 'CREC-2023-03-10', '2023-03-10'
T_HIG, T_SCOTT, T_ROE, T_SMITH, T_LOST = (text(t) for t in ('higgins', 'scott', 'roe', 'smith', 'nolabel'))
PAGES = [('CREC-2023-03-10-pt1-PgH1.htm', page(('Mr. HIGGINS of New York', T_HIG), ('Mr. SCOTT of Georgia', T_SCOTT),
                                               ('Mr. DAVID P. ROE of Tennessee', T_ROE), ('Mr. SMITH of New Jersey', T_SMITH)))]
STORED = [(1, 'Clay Higgins', T_HIG), (2, 'Austin Scott', T_SCOTT), (3, 'John Rose', T_ROE),
          (4, 'Christopher Smith', T_SMITH), (5, 'Danny Davis', T_LOST)]


def test_suspect_rows_are_classified_and_resolved_against_all_of_hsall(tool):
    got = {r['rowid']: r for r in tool['suspect_rows']('CREC', PKG, DATE, PAGES, STORED, tool['hist_names']())}
    assert sorted(got) == [1, 2, 3, 5]                                    # row 4 is reproduced: not a suspect
    h = got[1]; assert (h['reason'], h['status'], h['proposed_member'], h['proposed_bioguide'], h['proposed_on_roster']) == \
        ('state_tag_other_person', 'parked_former', 'Brian Higgins', 'H001078', 0)
    s = got[2]; assert (s['reason'], s['status'], s['proposed_member']) == ('namesake_ambiguous', 'ambiguous', None)
    r = got[3]; assert (r['reason'], r['status'], r['proposed_member']) == ('unverified', 'unresolved', None)
    n = got[5]; assert (n['reason'], n['status'], n['label']) == ('unverified', 'unresolved', '')   # no segment carries that text


def test_skip_set_makes_the_pass_idempotent(tool):
    assert tool['suspect_rows']('CREC', PKG, DATE, PAGES, STORED, tool['hist_names'](), skip={1, 2, 3, 5}) == []


@pytest.mark.parametrize('stored,persons,rows,status,member', [
    ('Clay Higgins', {'G000586'}, [], 'restored', 'Jesús García'),                       # unique, on the roster: back under the right member
    ('Jesús García', {'G000586'}, [], 'restored', 'Jesús García'),                       # the row was right all along
    ('Sylvia Garcia', {'G000586'}, [(9, 'Jesús García', T_HIG)], 'duplicate_of_existing', 'Jesús García'),   # he already has it: never double-store
    ('Clay Higgins', {'H001078'}, [], 'parked_former', 'Brian Higgins'),                 # unique but off the roster
    ('Clay Higgins', {'H001078', 'H001077'}, [], 'ambiguous', None),
    ('Clay Higgins', set(), [], 'unresolved', None),
])
def test_decide(tool, stored, persons, rows, status, member):
    d = tool['decide'](stored, persons, tool['hist_names'](), T_HIG, rows)
    assert (d['status'], d['proposed_member']) == (status, member)


# ---- the move: quarantine table, restore, undo ----------------------------------------------------------------------
def _db(tmp_path):
    con = sqlite3.connect(str(tmp_path / 'sm.db'))
    con.execute('CREATE TABLE speeches(member TEXT,date TEXT,source TEXT,chamber TEXT,package TEXT,nwords INTEGER,text TEXT)')
    rows = [(1, 'Clay Higgins', T_HIG), (2, 'Austin Scott', T_SCOTT), (3, 'John Rose', T_ROE), (4, 'Christopher Smith', T_SMITH),
            (5, 'Danny Davis', T_LOST), (6, 'Sylvia Garcia', text('garcia'))]
    con.executemany('INSERT INTO speeches(rowid,member,date,source,chamber,package,nwords,text) VALUES(?,?,?,?,?,?,?,?)',
                    [(i, m, DATE, 'CREC', 'house', PKG, len(t.split()), t) for i, m, t in rows])
    con.commit()
    return con


def _dump(con): return con.execute('SELECT rowid,member,date,source,chamber,package,nwords,text FROM speeches ORDER BY rowid').fetchall()


def test_quarantine_moves_restores_logs_and_undoes_exactly(tool, tmp_path):
    con = _db(tmp_path); before = _dump(con)
    dec = tool['suspect_rows']('CREC', PKG, DATE, PAGES, [(i, m, t) for i, m, d, s, c, p, n, t in before if i in (1, 2, 3, 4, 5)], tool['hist_names']())
    dec.append({'rowid': 6, 'package': PKG, 'source': 'CREC', 'stored_member': 'Sylvia Garcia', 'reason': 'unverified', 'label': 'MR. GARCIA of Illinois',
                'status': 'restored', 'proposed_member': 'Jesús García', 'proposed_bioguide': 'G000586', 'proposed_on_roster': 1, 'note': ''})
    tool['apply_decisions'](con, dec, 'B1'); con.commit()
    left = {r[0]: r[1] for r in _dump(con)}
    assert left == {4: 'Christopher Smith', 6: 'Jesús García'}                          # 1,2,3,5 moved out; 6 re-labelled in place; 4 untouched
    q = {r[0]: r[1:] for r in con.execute('SELECT orig_rowid,status,member,reason,proposed_member,restored_member FROM speeches_quarantine')}
    assert q[1] == ('quarantined', 'Clay Higgins', 'state_tag_other_person', 'Brian Higgins', None)
    assert q[6] == ('restored', 'Sylvia Garcia', 'unverified', 'Jesús García', 'Jesús García')
    assert con.execute('SELECT text FROM speeches_quarantine WHERE orig_rowid=1').fetchone()[0] == T_HIG   # the row's text survives intact
    tool['apply_decisions'](con, dec, 'B2'); con.commit()                                # second apply: no change
    assert con.execute('SELECT COUNT(*) FROM speeches_quarantine').fetchone()[0] == 5 and {r[0]: r[1] for r in _dump(con)} == left
    tool['undo_batch'](con, 'B1'); con.commit()
    assert _dump(con) == before                                                          # byte-for-byte, original rowids
    assert {r[0] for r in con.execute("SELECT status FROM speeches_quarantine")} == {'undone'}



def test_quarantine_command_end_to_end_dry_run_apply_rerun_undo(tool, tmp_path):
    """The command itself (package loop, cache read, ledger CSV, per-package commit), not just its helpers."""
    import argparse, gzip, json
    con = _db(tmp_path); before = _dump(con)
    con.execute('CREATE TABLE cr_fetch_log(key TEXT PRIMARY KEY,status TEXT,ts TEXT)')
    con.execute("INSERT INTO cr_fetch_log VALUES('crec:CREC-2023-03-10','ok','x')"); con.commit(); con.close()
    cache = tmp_path / 'cache'; cache.mkdir()
    with gzip.open(cache / (PKG + '.json.gz'), 'wt', encoding='utf-8') as f: json.dump(PAGES, f)
    tool['DB'] = str(tmp_path / 'sm.db')
    def args(apply): return argparse.Namespace(cache=str(cache), out=str(tmp_path / 'out'), since='2019-01-01', until='2026-12-31', apply=apply, sources=['CREC'])
    assert tool['quarantine_cmd'](args(False)) == 0                                       # dry run: ledger only
    dry = list(csv.DictReader(open(tmp_path / 'out' / 'quarantine_ledger_dry.csv', encoding='utf-8-sig')))
    assert sorted(int(r['rowid']) for r in dry) == [1, 2, 3, 5, 6] and {r['final_location'] for r in dry} == {'speeches_quarantine'}
    con = sqlite3.connect(str(tmp_path / 'sm.db')); assert _dump(con) == before and not con.execute("SELECT 1 FROM sqlite_master WHERE name='speeches_quarantine'").fetchone(); con.close()
    assert tool['quarantine_cmd'](args(True)) == 0                                        # apply
    con = sqlite3.connect(str(tmp_path / 'sm.db'))
    assert {r[0] for r in _dump(con)} == {4}                                              # row 6 carries a text no page has: no label, so it waits in quarantine too
    assert con.execute('SELECT outcome,COUNT(*) FROM speeches_quarantine GROUP BY outcome ORDER BY 1').fetchall() == [('ambiguous', 1), ('parked_former', 1), ('unresolved', 3)]
    batch = con.execute('SELECT DISTINCT batch_id FROM speeches_quarantine').fetchone()[0]; con.close()
    assert tool['quarantine_cmd'](args(True)) == 0                                        # rerun: nothing new
    again = list(csv.DictReader(open(tmp_path / 'out' / 'quarantine_ledger_apply.csv', encoding='utf-8-sig')))
    assert again == []
    tool['undo_cmd'](argparse.Namespace(batch=batch, apply=True))
    con = sqlite3.connect(str(tmp_path / 'sm.db')); assert _dump(con) == before
