"""CREC/CHRG speaker-label attribution (audit 2026-10-20: 24 roster members had ZERO stored turns).

Three defects, one fixture each:
  * the press-stance CSV carries a state for only 383/533 members, so a member without one lost every shared-surname
    label ("Mr. SMITH of New Jersey", "Mr. TORRES of New York", "Mrs. MILLER of Illinois") -> HSall state fallback;
  * CREC prefixes a given name when state alone cannot separate colleagues ("Mr. AUSTIN SCOTT of Georgia");
  * an accented roster name never met the plain-ASCII label (fixed 2026-10-14; pinned here).
And the converse: a state tag that names someone off the roster must DROP, never fall through to the lone same-surname member.
The roster, HSall and CSV are synthetic; the live roster, keys and database are never read.
"""
import ast
import html
import re
import sqlite3
from pathlib import Path

import pytest

FEC = Path(__file__).resolve().parents[2]

UNRESOLVED = None


def test_state_falls_back_to_hsall_when_the_press_csv_has_none(cr):
    assert cr.state['Christopher Smith'] == 'NJ'          # absent from the CSV, supplied by HSall
    assert cr.state['Adam Smith'] == 'WA'                 # CSV still wins where it speaks


@pytest.mark.parametrize('sur,st,chamber,congress,expected', [
    ('SMITH', 'New Jersey', 'house', 119, 'Christopher Smith'),      # the headline case: four House Smiths, state breaks the tie
    ('SMITH', 'Washington', 'house', 119, 'Adam Smith'),
    ('SMITH', None, 'house', 119, UNRESOLVED),                       # no state, four candidates: refuse
    ('SMITH', None, 'senate', 119, 'Tina Smith'),                    # one Senate Smith needs no state
    ('SMITH', 'Nebraska', 'house', 119, UNRESOLVED),                 # Adrian Smith is off this roster: never fall through to a lone Smith
    ('SCOTT', 'Virginia', 'house', 119, 'Robert Scott'),
    ('SCOTT', 'Georgia', 'house', 119, UNRESOLVED),                  # Austin AND David Scott are both Georgia: ambiguous without a first name
    ('AUSTIN SCOTT', 'Georgia', 'house', 119, 'Austin Scott'),       # CREC's own disambiguation
    ('DAVID SCOTT', 'Georgia', 'house', 119, UNRESOLVED),            # David Scott is off this roster: dropped, not credited to Austin
    ('CHRIS SMITH', 'New Jersey', 'house', 119, 'Christopher Smith'),  # nickname = prefix of the roster given name
    ('ADAM SMITH', 'Washington', 'house', 119, 'Adam Smith'),
    ('DERRICK VAN ORDEN', 'Wisconsin', 'house', 119, 'Derrick Van Orden'),
    ('VAN ORDEN', 'Wisconsin', 'house', 119, 'Derrick Van Orden'),
    ('GARCIA', 'Illinois', 'house', 119, 'Jesús García'),            # accented roster name, plain-ASCII label
    ('GARCIA', 'Texas', 'house', 119, 'Sylvia Garcia'),
    ('ROBERT GARCIA', 'California', 'house', 119, 'Robert Garcia'),
    ('GARCIA', None, 'house', 119, UNRESOLVED),
    ('HERNANDEZ', None, 'house', 119, 'Pablo Hernández Rivera'),    # roster spells him 'Hernández Rivera'; HSall and CREC print 'Hernández'
    ('HERNÁNDEZ', 'Puerto Rico', 'house', 119, 'Pablo Hernández Rivera'),
    ('RIVERA', None, 'house', 119, 'Pablo Hernández Rivera'),
    ('VELÁZQUEZ', None, 'house', 119, 'Nydia Velázquez'),            # accented label folds to the same key
    ('VELAZQUEZ', None, 'house', 119, 'Nydia Velázquez'),
    ('LUJÁN', None, 'senate', 119, 'Ben Luján'),
    ('LUJAN', 'New Mexico', 'house', 117, 'Ben Luján'),              # prior House service, same person
    ('KENNEDY', 'New York', 'house', 119, 'Timothy Kennedy'),
    ('KENNEDY', 'Utah', 'house', 119, 'Mike Kennedy'),
    ('KENNEDY', None, 'senate', 119, 'John Kennedy'),
    ('DANNY DAVIS', 'Illinois', 'house', 118, 'Danny Davis'),         # "Mr. DANNY K. DAVIS of Illinois": the middle initial is not part of the name
    ('DANNY K DAVIS', 'Illinois', 'house', 118, 'Danny Davis'),
    ('DANNY K DAVIS', None, 'house', 118, 'Danny Davis'),            # the first name alone separates him from Don and Susan Davis
    ('DAVIS', 'Illinois', 'house', 118, 'Danny Davis'),
    ('DAVIS', 'North Carolina', 'house', 118, 'Donald Davis'),
    ('DAVIS', None, 'house', 118, UNRESOLVED),                       # Danny, Donald and Susan Davis: refuse without a state
    ('BRENDAN F BOYLE', 'Pennsylvania', 'house', 118, 'Brendan Boyle'),
    ('JOHN W ROSE', 'Tennessee', 'house', 118, 'John Rose'),
    ('DAVID P ROE', 'Tennessee', 'house', 118, UNRESOLVED),          # David Roe is not on the roster: dropped, never credited to a Rose
    ('GOLDMAN', 'New York', 'house', 119, 'Daniel Goldman'),         # Craig Goldman (TX) is in HSall, off this roster
    ('GOLDMAN', None, 'house', 119, UNRESOLVED),                     # HSall sees two Goldmans in the 119th: refuse without a state
    ('HIGGINS', 'Louisiana', 'house', 118, 'Clay Higgins'),
    ('HIGGINS', 'New York', 'house', 118, UNRESOLVED),               # Brian Higgins' turns must not become Clay Higgins'
])
def test_label_resolution(cr, sur, st, chamber, congress, expected):
    assert cr.resolve(sur, st, chamber, congress) == expected


@pytest.mark.parametrize('sur,st,year,expected', [
    ('GRIJALVA', None, 2026, 'Adelita Grijalva'),   # Raul died in 2025: in 2026 only Adelita can be speaking
    ('GRIJALVA', None, 2025, UNRESOLVED),           # either could have spoken in 2025: refuse, never guess
    ('GRIJALVA', None, None, UNRESOLVED),           # no date (hearings carry the publication date): never apply the exclusion
])
def test_deceased_namesake_is_excluded_only_by_a_floor_date(cr, sur, st, year, expected):
    assert cr.resolve(sur, st, 'house', 119, year) == expected


def _page(*turns):
    filler = (' Mr. Speaker, I rise today to speak on a matter of importance to the people of my district, who sent me here to work for them,'
              ' and I ask my colleagues to join me in supporting the families and workers who depend on this body to act.')   # >150 chars: the parser drops procedural one-liners
    return '\n'.join('    %s.%s' % (t, filler) for t in turns) + '\n'


def test_parse_pages_stores_each_label_under_the_right_member(cr):
    con = sqlite3.connect(':memory:')
    con.execute('CREATE TABLE speeches(member,date,source,chamber,package,nwords,text)')
    text = _page('Mr. SMITH of New Jersey', 'Mr. AUSTIN SCOTT of Georgia', 'Mr. SCOTT of Georgia', 'Ms. VELAZQUEZ',
                 'Mr. KENNEDY of New York', 'Mr. HIGGINS of New York')
    cr.parse_pages([('CREC-2025-09-10-pt1-PgH1.htm', text)], lambda f: 'house', '2025-09-10', 'CREC', 'CREC-2025-09-10', con.cursor())
    got = sorted(r[0] for r in con.execute('SELECT member FROM speeches'))
    assert got == sorted(['Christopher Smith', 'Austin Scott', 'Nydia Velázquez', 'Timothy Kennedy'])


@pytest.mark.parametrize('line,pre,sur,st', [
    ('  Mr. DANNY K. DAVIS of Illinois. Mr. Speaker, I rise', 'Mr', 'DANNY K. DAVIS', 'Illinois'),
    ('  Mrs. CAROLYN B. MALONEY of New York. Madam Speaker', 'Mrs', 'CAROLYN B. MALONEY', 'New York'),
    ('  Mr. DAVID P. ROE of Tennessee. Mr. Speaker', 'Mr', 'DAVID P. ROE', 'Tennessee'),
    ('  Mr. BRENDAN F. BOYLE of Pennsylvania. Mr. Speaker', 'Mr', 'BRENDAN F. BOYLE', 'Pennsylvania'),
    ('  Mr. JOHN W. ROSE of Tennessee. Mr. Chairman', 'Mr', 'JOHN W. ROSE', 'Tennessee'),
    ('  Mr. DANNY K. DAVIS. Mr. Speaker', 'Mr', 'DANNY K. DAVIS', None),
    # shapes that must not change
    ('  Mr. SMITH of New Jersey. Mr. Speaker', 'Mr', 'SMITH', 'New Jersey'),
    ('  Mr. AUSTIN SCOTT of Georgia. Mr. Speaker', 'Mr', 'AUSTIN SCOTT', 'Georgia'),
    ('  Ms. JACKSON LEE. Madam Speaker', 'Ms', 'JACKSON LEE', None),
    ('  Mrs. McMORRIS RODGERS of Washington. Mr. Speaker', 'Mrs', 'McMORRIS RODGERS', 'Washington'),
    ('  Mr. SEAN PATRICK MALONEY of New York. Mr. Speaker', 'Mr', 'SEAN PATRICK MALONEY', 'New York'),
    ('  Mr. Danny K. Davis. Thank you, Mr. Chairman', 'Mr', 'Danny K. Davis', None),
    ('  Mr. Davis of Illinois. Thank you, Mr. Chairman', 'Mr', 'Davis', 'Illinois'),
])
def test_turn_label_keeps_a_middle_initial_inside_the_name(cr, line, pre, sur, st):
    m = cr.TURN.search(line)
    assert m and (m.group(1), m.group(2), m.group(3)) == (pre, sur, st)


@pytest.mark.parametrize('line', [
    'Ms. Marshall, what do you think?',            # direct address, not a label
    '  Mr. K. what is the matter',                 # a lone initial is not a name
])
def test_turn_still_refuses_non_labels(cr, line):
    assert not cr.TURN.search(line)


def test_parse_pages_keeps_the_turn_boundary_at_a_middle_initial_label(cr):
    con = sqlite3.connect(':memory:')
    con.execute('CREATE TABLE speeches(member,date,source,chamber,package,nwords,text)')
    text = _page('Mr. AUSTIN SCOTT of Georgia', 'Mr. DANNY K. DAVIS of Illinois', 'Mr. BRENDAN F. BOYLE of Pennsylvania',
                 'Mr. JOHN W. ROSE of Tennessee', 'Mr. DAVID P. ROE of Tennessee')
    cr.parse_pages([('CREC-2023-03-10-pt1-PgH1.htm', text)], lambda f: 'house', '2023-03-10', 'CREC', 'CREC-2023-03-10', con.cursor())
    rows = list(con.execute('SELECT member,text FROM speeches ORDER BY rowid'))
    assert [r[0] for r in rows] == ['Austin Scott', 'Danny Davis', 'Brendan Boyle', 'John Rose']   # David Roe is off the roster
    # each text starts at the speech, not at a surname fragment left behind by a mis-cut label
    assert all(r[1].startswith('Mr. Speaker, I rise today') for r in rows)


def test_hearing_sequence_resolves_a_middle_initial_label(cr):
    """fetch_hearings_antag shares cr.TURN / cr.resolve: its sequence parser must see the same member."""
    path = FEC / 'collect' / 'fetch_hearings_antag.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    fn = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'parse_sequence']
    scope = {'CR': cr, 're': re, 'html': html, 'ENDDASH': re.compile(r'-{2,}\s*$'), 'party_of': lambda who: 'D'}
    exec(compile(ast.Module(body=fn, type_ignores=[]), str(path), 'exec'), scope)
    txt = ('Chairman HILL. The gentleman from Illinois is recognized.\n'
           'Mr. DANNY K. DAVIS of Illinois. Thank you, Mr. Chairman, for holding this hearing on an important matter.\n'
           'Chairman HILL. The gentleman from Pennsylvania is recognized.\n'
           'Mr. BRENDAN F. BOYLE of Pennsylvania. I thank the witnesses for appearing before us today.\n')
    seq = scope['parse_sequence'](txt, 'house', 118)
    assert [(w, role) for w, role, *_ in seq if w] == [('Danny Davis', 'spk'), ('Brendan Boyle', 'spk')]
    assert seq[1][3].startswith('Thank you, Mr. Chairman') and seq[3][3].startswith('I thank the witnesses')


# ---- comma labels: "Mr. COONS, Madam President, I rise ..." (statements printed in the Record; Paul 2026-10-20) --------------------
@pytest.mark.parametrize('line,pre,sur,st', [
    ('  Mr. COONS, Madam President, I rise to recognize the service of a Delawarean.', 'Mr', 'COONS', None),
    ('  Mr. MOORE of North Carolina, Mr. Speaker, today is a sad occasion as I rise', 'Mr', 'MOORE', 'North Carolina'),
    ('  Ms. GARCIA of Texas, Mr. Speaker, I rise today to mark 13 years of protecting Dreamers', 'Ms', 'GARCIA', 'Texas'),
    ('  Mr. DANNY K. DAVIS of Illinois, Mr. Speaker, I rise', 'Mr', 'DANNY K. DAVIS', 'Illinois'),
    ('  Ms. HIRONO, Mr. President, I rise today to introduce the Fair Day in Court for Kids Act', 'Ms', 'HIRONO', None),
    ('  Mr. SMITH of New Jersey. Mr. Speaker, I rise', 'Mr', 'SMITH', 'New Jersey'),            # the period form is unchanged
])
def test_crec_turn_keeps_a_comma_label_when_the_next_words_address_the_chair(cr, line, pre, sur, st):
    m = cr.TURN_C.search(line)
    assert m and (m.group(1), m.group(2), m.group(3)) == (pre, sur, st)


@pytest.mark.parametrize('line', [
    '  Mrs. WATSON COLEMAN, Mr. RUSH, Mrs. BEATTY, and Mr. RICHMOND changed their vote',      # a vote list is not a speech
    '  Ms. Rainey, what do you think?',                                                      # direct address
    '  Ms. MARSHALL, Madame Secretary, what do you think?',                                  # not an address to the chair
    '  we thank Mr. SMITH, Mr. Speaker, for yielding',                                       # a label starts a line
])
def test_crec_turn_still_refuses_non_labels(cr, line):
    assert not cr.TURN_C.search(line)


def test_hearing_regex_is_unchanged_by_the_comma_rule(cr):
    assert not cr.TURN.search('  Mr. COONS, Madam President, I rise to recognize')        # hearings keep the period-only rule (fetch_hearings_antag uses TURN)
    assert cr.TURN.search('  Mr. Hill. I would like to ask the witnesses about this').group(2) == 'Hill'


def test_parse_pages_stores_a_comma_label_statement_for_crec_only(cr):
    body = ('Mr. President, I rise today to introduce the Fair Day in Court for Kids Act. This important bill would guarantee that every child '
            'facing removal has counsel at government expense and a fair day in court, and I urge my colleagues to support it.')
    page = '  to the Committee on the Judiciary.\n  Ms. HIRONO, %s\n' % body
    for source, expect in (('CREC', ['Mazie Hirono']), ('CHRG', [])):
        con = sqlite3.connect(':memory:')
        con.execute('CREATE TABLE speeches(member,date,source,chamber,package,nwords,text)')
        cr.parse_pages([('CREC-2023-03-10-pt1-PgS1.htm', page)], lambda f: 'senate', '2023-03-10', source, 'P', con.cursor(), congress=118)
        assert [r[0] for r in con.execute('SELECT member FROM speeches')] == expect
