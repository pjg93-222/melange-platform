"""Date-aware CREC speaker resolution (roster lane 2026-10-20).

Lindsey Graham (G000359) died 2026-07-11; Darline Graham (G000608) was sworn into the same South Carolina seat 2026-07-14.
Once the roster carries Darline, "Mr. GRAHAM." on a floor day before 2026-07-14 must not resolve to her, and on a day after it
the departed namesake must not block her.  The roster artifact's term dates decide; a person it does not know keeps the
Congress / death-year rules (pinned in test_crec_speaker_attribution.py).  Everything here is synthetic.
"""
import csv
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

FEC = Path(__file__).resolve().parents[2]
LINDSEY = ('G000359', 'GRAHAM, Lindsey O.', 'SC', '2026.0')
ROSTER_JSON = {
    'schema': 'melange-roster-v1',
    'members': [
        {'member': 'Darline Graham', 'bioguide': 'G000608', 'chamber': 'Senate', 'state': 'SC',
         'terms': [{'chamber': 'senate', 'start': '2026-07-14', 'end': '2027-01-03', 'state': 'SC'}]},
        {'member': 'Tina Smith', 'bioguide': 'S001203', 'chamber': 'Senate', 'state': 'MN',
         'terms': [{'chamber': 'senate', 'start': '2018-01-03', 'end': '2021-01-03', 'state': 'MN'},
                   {'chamber': 'senate', 'start': '2021-01-03', 'end': '2027-01-03', 'state': 'MN'}]},
    ],
    'departed': [
        {'member': 'Lindsey Graham', 'bioguide': 'G000359', 'chamber': 'Senate', 'state': 'SC', 'departed_on': '2026-07-11',
         'terms': [{'chamber': 'senate', 'start': '2003-01-07', 'end': '2009-01-03', 'state': 'SC'},
                   {'chamber': 'senate', 'start': '2021-01-03', 'end': '2026-07-11', 'state': 'SC'}]},
    ],
}


def _load(tmp, darline_in_hsall, roster_json=True):
    root = tmp / ('hs%d_rj%d' % (darline_in_hsall, roster_json))
    (root / 'data' / 'engine').mkdir(parents=True)
    (root / 'votes').mkdir()
    (root / 'draft').mkdir()
    (root / 'data' / 'engine' / 'congestion.json').write_text(json.dumps([
        {'member': 'Darline Graham', 'bioguide': 'G000608', 'chamber': 'Senate'},
        {'member': 'Tina Smith', 'bioguide': 'S001203', 'chamber': 'Senate'}]), encoding='utf-8')
    if roster_json:
        (root / 'data' / 'engine' / 'roster.json').write_text(json.dumps(ROSTER_JSON), encoding='utf-8')
    with open(root / 'votes' / 'HSall_members.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['congress', 'chamber', 'bioguide_id', 'icpsr', 'bioname', 'state_abbrev', 'died'])
        for congress in (118, 119):
            w.writerow([congress, 'Senate', LINDSEY[0], '', LINDSEY[1], LINDSEY[2], LINDSEY[3]])
            w.writerow([congress, 'Senate', 'S001203', '', 'SMITH, Tina', 'MN', ''])
        if darline_in_hsall:
            w.writerow([119, 'Senate', 'G000608', '', 'GRAHAM, Darline', 'SC', ''])
    with open(root / 'draft' / 'cu_press_stance_tone.csv', 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerow(['member', 'state'])
    saved = {k: os.environ.get(k) for k in ('MONEYBALL_ROOT', 'MONEYBALL_DRAFT')}
    os.environ['MONEYBALL_ROOT'] = str(root); os.environ['MONEYBALL_DRAFT'] = str(root / 'draft')
    prior_paths = sys.modules.pop('paths', None)
    try:
        spec = importlib.util.spec_from_file_location('crec_terms_under_test', FEC / 'collect' / 'fetch_congressional_record.py')
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    finally:
        sys.modules.pop('paths', None)
        if prior_paths is not None: sys.modules['paths'] = prior_paths
        for k, v in saved.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    return mod


@pytest.fixture(scope='module', params=[False, True], ids=['hsall-lags', 'hsall-has-darline'])
def cr(request, tmp_path_factory):
    return _load(tmp_path_factory.mktemp('crec_terms'), request.param)


@pytest.mark.parametrize('date,expected', [
    ('2026-06-10', None),               # Lindsey's floor day: Darline was not seated, and Lindsey is off the roster -> drop
    ('2026-07-11', None),               # his last day
    ('2026-07-13', None),               # the seat was vacant
    ('2026-07-14', 'Darline Graham'),   # sworn
    ('2026-09-30', 'Darline Graham'),   # the departed namesake no longer blocks her
])
def test_same_surname_successor_resolves_by_floor_date(cr, date, expected):
    assert cr.resolve('GRAHAM', None, 'senate', 119, int(date[:4]), date) == expected
    assert cr.resolve('GRAHAM', 'South Carolina', 'senate', 119, int(date[:4]), date) == expected


def test_no_date_keeps_the_congress_rules(cr):
    # CHRG passes no date: two Grahams served the 119th Senate, so refuse rather than guess (when HSall knows both),
    # and never credit a 2025 backfill to the 2026 appointee.
    assert cr.resolve('GRAHAM', None, 'senate', 119) in (None, 'Darline Graham')
    assert cr.resolve('GRAHAM', None, 'senate', 119, 2025, '2025-03-04') is None


def test_unrelated_member_unchanged(cr):
    assert cr.resolve('SMITH', None, 'senate', 119, 2026, '2026-09-30') == 'Tina Smith'


def test_roster_state_fills_a_new_arrival(cr):
    assert cr.state['Darline Graham'] == 'SC'


def test_parse_pages_passes_the_floor_date(cr):
    import sqlite3
    filler = (' Mr. President, I rise today to speak on a matter of importance to the people of my state, who sent me here to work'
              ' for them, and I ask my colleagues to join me in supporting the families and workers who depend on this body.')
    for date, want in (('2026-06-10', []), ('2026-09-30', ['Darline Graham'])):
        con = sqlite3.connect(':memory:')
        con.execute('CREATE TABLE speeches(member,date,source,chamber,package,nwords,text)')
        cr.parse_pages([('CREC-%s-pt1-PgS1.htm' % date, '    Mrs. GRAHAM.%s\n' % filler)], lambda f: 'senate', date, 'CREC',
                       'CREC-' + date, con.cursor())
        assert [r[0] for r in con.execute('SELECT member FROM speeches')] == want


def test_missing_roster_artifact_turns_the_date_rule_off(tmp_path):
    mod = _load(tmp_path, True, roster_json=False)
    assert not mod.TERMS
    assert mod.resolve('SMITH', None, 'senate', 119, 2026, '2026-09-30') == 'Tina Smith'


def _williams(tmp):
    """Roger Williams (House since 2013) and Nikema Williams (sworn 2021-01-03, the first day of the 117th)."""
    root = tmp / 'williams'
    (root / 'data' / 'engine').mkdir(parents=True)
    (root / 'votes').mkdir()
    (root / 'draft').mkdir()
    (root / 'data' / 'engine' / 'congestion.json').write_text(json.dumps([
        {'member': 'Roger Williams', 'bioguide': 'W000816', 'chamber': 'House'},
        {'member': 'Nikema Williams', 'bioguide': 'W000788', 'chamber': 'House'}]), encoding='utf-8')
    (root / 'data' / 'engine' / 'roster.json').write_text(json.dumps({'schema': 'melange-roster-v1', 'members': [
        {'member': 'Roger Williams', 'bioguide': 'W000816', 'chamber': 'House', 'state': 'TX',
         'terms': [{'chamber': 'house', 'start': '%d-01-03' % y, 'end': '%d-01-03' % (y + 2), 'state': 'TX'} for y in range(2013, 2027, 2)]},
        {'member': 'Nikema Williams', 'bioguide': 'W000788', 'chamber': 'House', 'state': 'GA',
         'terms': [{'chamber': 'house', 'start': '%d-01-03' % y, 'end': '%d-01-03' % (y + 2), 'state': 'GA'} for y in (2021, 2023, 2025)]}],
        'departed': []}), encoding='utf-8')
    with open(root / 'votes' / 'HSall_members.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['congress', 'chamber', 'bioguide_id', 'icpsr', 'bioname', 'state_abbrev', 'died'])
        for c in (116, 117, 118, 119):
            w.writerow([c, 'House', 'W000816', '', 'WILLIAMS, Roger', 'TX', ''])
        for c in (117, 118, 119):
            w.writerow([c, 'House', 'W000788', '', 'WILLIAMS, Nikema', 'GA', ''])
    with open(root / 'draft' / 'cu_press_stance_tone.csv', 'w', newline='', encoding='utf-8') as f:
        csv.writer(f).writerow(['member', 'state'])
    saved = {k: os.environ.get(k) for k in ('MONEYBALL_ROOT', 'MONEYBALL_DRAFT')}
    os.environ['MONEYBALL_ROOT'] = str(root); os.environ['MONEYBALL_DRAFT'] = str(root / 'draft')
    prior_paths = sys.modules.pop('paths', None)
    try:
        spec = importlib.util.spec_from_file_location('crec_williams_under_test', FEC / 'collect' / 'fetch_congressional_record.py')
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    finally:
        sys.modules.pop('paths', None)
        if prior_paths is not None: sys.modules['paths'] = prior_paths
        for k, v in saved.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    return mod


def test_a_term_starting_on_the_next_congress_jan_3_is_not_the_previous_congress(tmp_path):
    cr = _williams(tmp_path)
    assert cr.resolve('WILLIAMS', None, 'house', 116) == 'Roger Williams'          # Nikema was sworn 2021-01-03 = 117th
    assert cr.resolve('WILLIAMS', None, 'house', 117) is None                     # both serve the 117th: refuse
    assert cr.resolve('WILLIAMS', 'Georgia', 'house', 117) == 'Nikema Williams'   # sworn Jan 3: resolved in the new Congress
    assert cr.resolve('WILLIAMS', 'Georgia', 'house', 117, 2021, '2021-01-03') == 'Nikema Williams'
    assert cr.resolve('WILLIAMS', 'Georgia', 'house', 116, 2021, '2021-01-02') is None
