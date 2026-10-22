"""The per-member speech-basis CSV (backfill_attribution counts) is keyed by data/engine/roster.json: every current member PLUS the departed ones, each
departed member marked with his service end (a departed member's own historical rows stay his; ruling 2026-10-21).  Synthetic roster / database."""
import argparse
import csv
import json
import sqlite3


def roster_json(root):
    d = root / 'data' / 'engine'; d.mkdir(parents=True, exist_ok=True)
    cur = lambda m, a, b: {'member': m, 'bioguide': 'X', 'term_start': a, 'term_end': b}
    (d / 'roster.json').write_text(json.dumps({'members': [cur('Al Green', '2025-01-03', '2027-01-03'), cur('Darline Graham', '2026-07-12', '2027-01-03'), cur('Jesús García', '2025-01-03', '2027-01-03')],
                                               'departed': [cur('Lindsey Graham', '2021-01-03', '2026-07-11')]}, ensure_ascii=False), encoding='utf-8')


def test_basis_csv_member_set_is_the_roster_plus_the_departed(tool, tmp_path):
    from pathlib import Path
    roster_json(Path(tool['ROOT']))
    db = tmp_path / 'sm.db'; con = sqlite3.connect(str(db))
    con.execute('CREATE TABLE speeches(member TEXT,date TEXT,source TEXT,chamber TEXT,package TEXT,nwords INTEGER,text TEXT)')
    for m, d, s in [('Lindsey Graham', '2019-01-15', 'CREC'), ('Lindsey Graham', '2026-07-01', 'CREC'), ('Al Green', '2025-03-01', 'CHRG'), ('Not On Roster', '2025-01-01', 'CREC')]:
        con.execute('INSERT INTO speeches VALUES(?,?,?,?,?,?,?)', (m, d, s, 'house', 'P', 10, 'x'))
    con.commit(); con.close()
    tool['DB'] = str(db); out = tmp_path / 'basis.csv'
    assert tool['counts'](argparse.Namespace(plan=None, out=str(out))) == 0
    rows = {r['member']: r for r in csv.DictReader(open(out, encoding='utf-8-sig'))}
    assert set(rows) == {'Al Green', 'Darline Graham', 'Jesús García', 'Lindsey Graham'}                         # roster + departed, nobody else
    assert list(next(iter(rows.values())))[:7] == ['member', 'crec_count', 'crec_min_date', 'crec_max_date', 'chrg_count', 'chrg_min_date', 'chrg_max_date']   # old columns first
    g = rows['Lindsey Graham']
    assert (g['status'], g['term_end'], g['crec_count'], g['crec_min_date'], g['crec_max_date']) == ('departed', '2026-07-11', '2', '2019-01-15', '2026-07-01')
    assert rows['Darline Graham']['status'] == 'current' and rows['Darline Graham']['crec_count'] == '0' and rows['Darline Graham']['crec_min_date'] == ''
    assert rows['Al Green']['chrg_count'] == '1' and rows['Al Green']['term_start'] == '2025-01-03'
