"""Read-only refinement of the quarantined rows (Paul 2026-10-20: "aim to fix them").  Only EXACT, UNIQUE evidence counts:
  * a CHRG transcript prints its committee's members by full name and state in the front matter -- "Mr. GREEN." is resolved against
    that list only (Al Green sits on House Financial Services, Mark Green does not);
  * a CREC recognition line names the state -- "The Chair recognizes the gentleman from Texas (Mr. GREEN)";
  * a row whose speaker label ends in a comma ("Ms. HIRONO, Mr. President, I rise ...") is read from the label printed immediately
    before its text, but only when the text opens by addressing the chair;
  * identical boilerplate spoken by several members in one day is split by elimination, only when the counts close exactly.
Anything else stays where it is.  Synthetic roster from conftest.py; nothing live is read."""
import pytest

PKG = 'CREC-2023-03-10'
DATE = '2023-03-10'
CHRG = 'CHRG-118hhrg12345'

FSC_FRONT = """
                 HOUSE COMMITTEE ON FINANCIAL SERVICES

                 MAXINE WATERS, California, Chairwoman

CAROLYN B. MALONEY, New York         PATRICK McHENRY, North Carolina,
NYDIA M. VELAZQUEZ, New York             Ranking Member
AL GREEN, Texas                      PETER T. KING, New York
WM. LACY CLAY, Missouri              ROGER WILLIAMS, Texas
JESUS ``CHUY'' GARCIA, Illinois      LEE M. ZELDIN, New York
MICHAEL SAN NICOLAS, Guam            ALEXANDER X. MOONEY, West Virginia
SUSIE LEE, Nevada

                   Charla Ouertatani, Staff Director

                           C O N T E N T S

                               WITNESSES
Powell, Jerome H., Chairman, Board of Governors of the Federal
ZZ TOP, Texas
"""


def lorem(tag):
    return ('Thank you, %s: I appreciate the chance to ask about a matter of importance to the people of my district, who sent me here '
            'to work for them, and I would like the witnesses to explain how the rule will affect families and small businesses there.' % tag)


# ---- hearing front matter ---------------------------------------------------------------------------------------------
def test_hearing_roster_reads_the_member_list_and_nothing_else(tool):
    got = set(tool['hearing_roster'](FSC_FRONT))
    for want in [(('MAXINE', 'WATERS'), 'CA'), (('AL', 'GREEN'), 'TX'), (('PATRICK', 'MCHENRY'), 'NC'), (('JESUS', 'GARCIA'), 'IL'),
                 (('LEE', 'ZELDIN'), 'NY'), (('ALEXANDER', 'MOONEY'), 'WV'), (('MICHAEL', 'SAN', 'NICOLAS'), 'GU'), (('SUSIE', 'LEE'), 'NV'),
                 (('CAROLYN', 'MALONEY'), 'NY'), (('WM', 'LACY', 'CLAY'), 'MO')]:
        assert want in got, want
    assert not any('OUERTATANI' in e[0] or 'POWELL' in e[0] or 'TOP' in e[0] for e in got)   # staff, a witness, and a line after CONTENTS


def test_senate_single_column_list(tool):
    got = set(tool['hearing_roster']('SENATE COMMITTEE ON BANKING\n  MIKE CRAPO, Idaho, Chairman\n  SHERROD BROWN, Ohio, Ranking Member\n  TIM SCOTT, South Carolina\n'))
    assert got == {(('MIKE', 'CRAPO'), 'ID'), (('SHERROD', 'BROWN'), 'OH'), (('TIM', 'SCOTT'), 'SC')}


@pytest.mark.parametrize('sur,st,n', [('GREEN', None, 1), ('GREEN', 'Texas', 1), ('GREEN', 'Tennessee', 0), ('LEE', None, 2), ('LEE', 'Nevada', 1),
                                      ('ZELDIN', None, 1), ('AL GREEN', None, 1), ('SMITH', None, 0), ('MCHENRY', None, 1)])
def test_roster_hits(tool, sur, st, n):
    entries = tool['hearing_roster'](FSC_FRONT.replace('SUSIE LEE, Nevada', 'SUSIE LEE, Nevada       BARBARA LEE, California'))
    assert len(tool['roster_hits'](sur, st, entries)) == n


def test_roster_persons_resolve_a_hsall_person_or_stay_silent(tool):
    entries = tool['hearing_roster'](FSC_FRONT)
    assert tool['roster_persons']('GREEN', None, entries, 'house', 118) == {'G000553'}          # Al Green, not Mark Green (TN, off the committee)
    assert tool['roster_persons']('LEE', None, entries, 'house', 118) == {'L000590'}            # Susie Lee, not Barbara Lee / Sheila Jackson Lee
    assert tool['roster_persons']('SMITH', None, entries, 'house', 118) is None                 # the roster is silent: say nothing
    both = tool['hearing_roster'](FSC_FRONT.replace('SUSIE LEE, Nevada', 'SUSIE LEE, Nevada       BARBARA LEE, California'))
    assert tool['roster_persons']('LEE', None, both, 'house', 118) == {'L000590', 'L000551'}    # two Lees on one panel: still ambiguous
    assert tool['roster_persons']('LEE', 'California', both, 'house', 118) == {'L000551'}


# ---- same-day CREC context -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize('before,sur,expect', [
    ('The SPEAKER pro tempore. The Chair recognizes the gentleman from Texas (Mr. GREEN) for 5 minutes.', 'GREEN', 'TX'),
    ('the gentlewoman from New York (Ms. Velazquez) is recognized', 'VELAZQUEZ', 'NY'),
    ('The gentleman from Tennessee (Mr. Green) and then the gentleman from Texas (Mr. GREEN).', 'GREEN', 'TX'),     # the nearest one wins
    ('The Chair recognizes the gentleman from Texas (Mr. GREENE).', 'GREEN', None),                                  # exact surname only
    ('The Chair recognizes the gentleman from Texas (Mr. Cuellar).', 'GREEN', None),
    ('Mr. GREEN of Texas spoke earlier.', 'GREEN', None),
    ('the Senator from West Virginia (Mrs. CAPITO)', 'CAPITO', 'WV'),
])
def test_context_state(tool, before, sur, expect):
    assert tool['context_state'](before, sur) == expect


@pytest.mark.parametrize('before,expect', [
    ('to the Committee on the Judiciary. Ms. HIRONO, ', ('Ms', 'HIRONO', None)),
    ('...bill. Mr. DANNY K. DAVIS of Illinois, ', ('Mr', 'DANNY K. DAVIS', 'Illinois')),
    ('changed their vote from ``aye'' to ``no.'' Mrs. WATSON COLEMAN,', ('Mrs', 'WATSON COLEMAN', None)),
    ('the committee reported the bill. It was so ordered.', None),
    ('Mr. HIRONO said ', None),
])
def test_adjacent_label(tool, before, expect):
    assert tool['adjacent_label'](before) == expect


@pytest.mark.parametrize('text,expect', [
    ('Mr. President, I rise today to introduce the Fair Day in Court for Kids Act.', True),
    ('Madam Speaker, I yield myself such time as I may consume.', True),
    ('Mr. Chair, I have an amendment at the desk.', True),
    ('from the Committee on Rules, submitted a privileged report (Rept. No. 116-17)', False),
    ('Mr. RUSH, Mrs. BEATTY, and Mr. RICHMOND changed their vote from ``no\'\' to ``aye.\'\'', False),
])
def test_addresses_chair(tool, text, expect):
    assert tool['addresses_chair'](text) is expect


# ---- the pass itself ---------------------------------------------------------------------------------------------------
def q(rowid, member, text, reason='chrg_unreproduced', outcome='ambiguous', label='', proposed=None):
    return {'rowid': rowid, 'member': member, 'text': text, 'reason': reason, 'outcome': outcome, 'label': label, 'proposed_member': proposed}


def run(tool, src, pid, date, pages, qrows, stored=(), congress=None, hearing_chamber=None):
    return {r['rowid']: r for r in tool['refine_rows'](src, pid, date, pages, qrows, list(stored), tool['hist_names'](), congress, hearing_chamber)}


def test_hearing_roster_restores_al_green_and_susie_lee(tool):
    green, lee = lorem('Chairwoman Waters'), lorem('Madam Chair')
    page = FSC_FRONT + '\n  Chairwoman WATERS. The gentleman from Texas is recognized.\n  Mr. GREEN. %s\n  Ms. LEE. %s\n' % (green, lee)
    got = run(tool, 'CHRG', CHRG, '2023-03-10', [(CHRG + '.htm', page)],
              [q(1, 'Al Green', green, label='Mr GREEN'), q(2, 'Susie Lee', lee, label='Ms LEE')], congress=118, hearing_chamber='house')
    assert (got[1]['new_outcome'], got[1]['proposed_member'], got[1]['method']) == ('restore', 'Al Green', 'roster')
    assert (got[2]['new_outcome'], got[2]['proposed_member'], got[2]['method']) == ('restore', 'Susie Lee', 'roster')


def test_a_row_the_roster_gives_to_someone_else_is_reassigned(tool):
    green = lorem('Chairwoman Waters')
    page = FSC_FRONT + '\n  Mr. GREEN. %s\n' % green
    got = run(tool, 'CHRG', CHRG, '2023-03-10', [(CHRG + '.htm', page)], [q(1, 'Sylvia Garcia', green, label='Mr GREEN')], congress=118, hearing_chamber='house')
    assert (got[1]['new_outcome'], got[1]['proposed_member']) == ('reassign_current', 'Al Green')


def test_two_lees_on_the_panel_stay_ambiguous(tool):
    lee = lorem('Madam Chair')
    page = FSC_FRONT.replace('SUSIE LEE, Nevada', 'SUSIE LEE, Nevada       BARBARA LEE, California') + '\n  Ms. LEE. %s\n' % lee
    got = run(tool, 'CHRG', CHRG, '2023-03-10', [(CHRG + '.htm', page)], [q(1, 'Susie Lee', lee, label='Ms LEE')], congress=118, hearing_chamber='house')
    assert got[1]['new_outcome'] == 'stay_ambiguous' and got[1]['proposed_member'] is None


def test_hearing_with_no_member_list_changes_nothing(tool):
    green = lorem('Chairwoman Waters')
    got = run(tool, 'CHRG', CHRG, '2023-03-10', [(CHRG + '.htm', 'HEARING\n  Mr. GREEN. %s\n' % green)], [q(1, 'Al Green', green, label='Mr GREEN')], congress=118, hearing_chamber='house')
    assert got[1]['new_outcome'] == 'stay_ambiguous'


def test_recognition_line_picks_the_state(tool):
    t = lorem('Madam Speaker')
    pages = [('CREC-2023-03-10-pt1-PgH1.htm', '    The SPEAKER pro tempore. The Chair recognizes the gentleman from Texas (Mr. GREEN) for 5 minutes.\n    Mr. GREEN. %s\n' % t)]
    got = run(tool, 'CREC', PKG, DATE, pages, [q(1, 'Al Green', t, reason='namesake_ambiguous', label='Mr GREEN')])
    assert (got[1]['new_outcome'], got[1]['proposed_member'], got[1]['method']) == ('restore', 'Al Green', 'context')
    pages2 = [('CREC-2023-03-10-pt1-PgH1.htm', '    The SPEAKER pro tempore. The Chair recognizes the gentleman from Tennessee (Mr. GREEN) for 5 minutes.\n    Mr. GREEN. %s\n' % t)]
    got2 = run(tool, 'CREC', PKG, DATE, pages2, [q(1, 'Al Green', t, reason='namesake_ambiguous', label='Mr GREEN')])
    assert (got2[1]['new_outcome'], got2[1]['proposed_member']) == ('reassign_former', 'Mark E. Green')


def test_comma_label_is_read_only_when_the_text_addresses_the_chair(tool):
    speech = 'Mr. President, I rise today to introduce the Fair Day in Court for Kids Act of 2021. This important bill would guarantee that every child facing removal has counsel at government expense and a fair day in court.'
    votes = "Mr. RUSH, Mrs. BEATTY, and Mr. RICHMOND changed their vote from ``no'' to ``aye.'' So the amendment was agreed to, and the motion to reconsider was laid on the table."
    pages = [('CREC-2023-03-10-pt1-PgS1.htm', '  A bill to provide counsel for unaccompanied children; to the Committee on the Judiciary.\n  Ms. HIRONO, %s\n' % speech),
             ('CREC-2023-03-10-pt1-PgS2.htm', '  Zeldin voted yes. Mrs. WATSON COLEMAN, %s\n' % votes)]
    got = run(tool, 'CREC', PKG, DATE, pages, [q(1, 'Mazie Hirono', speech, reason='unverified', outcome='unresolved'),
                                               q(2, 'Mazie Hirono', votes, reason='unverified', outcome='unresolved')])
    assert (got[1]['new_outcome'], got[1]['proposed_member'], got[1]['method']) == ('restore', 'Mazie Hirono', 'label')   # the collector now cuts a comma label as a turn
    assert got[2]['new_outcome'] == 'stay_unresolved'                                    # a vote-change list is not a speech, whoever is printed before it


def test_identical_boilerplate_is_split_by_elimination(tool):
    t = lorem('Mr. Speaker')
    pages = [('CREC-2023-03-10-pt1-PgH1.htm', '    Mr. SMITH of New Jersey. %s\n    Mr. HIGGINS of New York. %s\n' % (t, t))]
    got = run(tool, 'CREC', PKG, DATE, pages, [q(1, 'Clay Higgins', t, reason='unverified', label='Mr SMITH of New Jersey | Mr HIGGINS of New York')],
              stored=[(4, 'Christopher Smith', t)])
    assert (got[1]['new_outcome'], got[1]['proposed_member'], got[1]['method']) == ('reassign_former', 'Brian Higgins', 'elimination')


def test_elimination_needs_the_counts_to_close(tool):
    t = lorem('Mr. Speaker')
    pages = [('CREC-2023-03-10-pt1-PgH1.htm', '    Mr. SMITH of New Jersey. %s\n    Mr. HIGGINS of New York. %s\n' % (t, t))]
    got = run(tool, 'CREC', PKG, DATE, pages, [q(1, 'Clay Higgins', t, reason='unverified', label='x')])   # nobody stored the Smith copy: two labels, one row
    assert got[1]['new_outcome'] == 'stay_ambiguous'


def test_already_parked_rows_with_nothing_new_are_unchanged(tool):
    t = lorem('Mr. Speaker')
    pages = [('CREC-2023-03-10-pt1-PgH1.htm', '    Mr. HIGGINS of New York. %s\n' % t)]
    got = run(tool, 'CREC', PKG, DATE, pages, [q(1, 'Clay Higgins', t, reason='state_tag_other_person', outcome='parked_former', label='Mr HIGGINS of New York', proposed='Brian Higgins')])
    assert (got[1]['new_outcome'], got[1]['proposed_member']) == ('unchanged', 'Brian Higgins')


def test_a_row_with_no_new_evidence_keeps_its_status_and_says_why(tool):
    t = lorem('Mr. Speaker')
    got = run(tool, 'CREC', PKG, DATE, [('CREC-2023-03-10-pt1-PgH1.htm', '    Mr. SMITH of New Jersey. %s' % lorem('other'))],
              [q(1, 'Clay Higgins', t, reason='unverified', outcome='unresolved')])
    assert got[1]['new_outcome'] == 'stay_unresolved' and got[1]['proposed_member'] is None
    assert 'no speaker segment' in got[1]['note'] or 'places on the day' in got[1]['note']
    assert 'HSall member fits' not in got[1]['note']                                       # the ledger must not blame the label when there simply was none
