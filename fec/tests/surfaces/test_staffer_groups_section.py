# -*- coding: utf-8 -*-
"""Contract suite for fec/surfaces/build_staffer_groups_section.py.

Everything runs against a synthetic generation, a synthetic corroboration sidecar
and a synthetic LDA corpus in a temp tree, through the shipped producer's own
switches, so the real binding, the real caps and the real label table are
exercised rather than a stand-in.

What is asserted, and why each one is a gate rather than documentation:

  * ONLY PEOPLE ARE PEOPLE.  Owner ruling D-022 took organisations, offices and
    policy areas OUT of the people network; the whole point of this section is
    that the groups are containers and the members of a group are people.  A
    container id that reaches a people list is the exact failure the ruling was
    written against, so it is a negative control with its own poisoned fixture,
    not a comment.
  * ONCE PER GROUP.  A person with two stints in the same office is one entry.
  * CURRENT DAY ONLY (owner ruling 2026-09-07, item 1).  An office is shown at
    all only when at least one person on it is current; every current person
    is listed in full, and everyone who served earlier is a count
    (earlier_count), never a second list of names.
  * HON:<bioguide> NAMING (owner ruling 2026-09-07, item 2).  An office whose
    member is off the current roster is named from the House personnel
    ledger's own most recent printed office string for that bioguide, never
    guessed; one the ledger never printed a name for keeps its bare key and is
    counted in offices_unnamed.
  * THE CAP IS REAL.  people[] is capped and `more` carries the remainder, so a
    reader can always see that a group is bigger than the list.
  * THE LABEL STRINGS ARE THE OWNER'S WORDS, byte for byte.  These are shown to a
    non-technical reader; a paraphrase is a different claim.  L4/L5 confirmed,
    L2/L3 likely, L0/L1 no record found, and the employer line on/not-on file.
  * NO EMPLOYMENT CLAIM LEAKS IN (D-021): canonical flags stay false and the
    promotion counter stays zero.

Plain-assert style, no dependencies:  python fec/tests/surfaces/test_staffer_groups_section.py
"""
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FEC = os.path.dirname(os.path.dirname(HERE))
PRODUCER = os.path.join(FEC, 'surfaces', 'build_staffer_groups_section.py')

GENERATION = 'staffer_social_network_test_g1_primary'
VERIFICATION_DIR = 'staffer_social_network_test_g1_verification'
SPINE_DIR = 'hill_personnel_spine_test_g1_primary'
SIDECAR = 'tiered_corroboration_sidecar_test_g1_primary'
LDA_GENERATION = 'generation-test-g1'

STATEMENT = ('Observed payroll co-tenure only. People who held the same office at '
             'non-overlapping times carry NO edge here. Not identity findings, not '
             'primary weights, no influence claim.')
ZERO_OVERLAP = 4242

CONFIRMED = 'Lobbyist · former Hill staff, confirmed'
LIKELY = 'Lobbyist · former Hill staff, likely'
NO_RECORD = 'Lobbyist · no Hill record found'
EMPLOYER_ON = 'Employer on file: %s'
# Ruled 2026-09-07: "Employer not on file" is retired everywhere. What is left
# is the separate, narrower case of no resolvable name at any tier.
EMPLOYER_UNRESOLVED = 'No employer name on file'
CORROB_R2 = 'corroborated by dated evidence'
CORROB_R1 = 'corroborated by disclosure'
CORROB_R0 = 'not independently corroborated'
# The employment ruling's own two engagement values (owner ruling 2026-09-07);
# these are the RAW strings a card's `engagement` field carries, not a display
# label -- fec/staffer_network/lda_status_labels.engagement_label() is where
# the display words ("Contract lobbyist" / "In-house lobbyist") live.
CONTRACT = 'contract'
IN_HOUSE = 'in_house'
TIE_BY_PERIOD_FILINGS = 'more_filings_in_latest_period'

OFFICE_A = 'container:office:house:HON:A000001'
OFFICE_B = 'container:office:senate:HON:B000002'
OFFICE_C = 'container:office:senate:JUDICIARY'
OFFICE_D = 'container:office:house:SPEAKER OF THE HOUSE'
OFFICE_ADMIN = 'container:office:house:ADMIN'          # kind 'admin' -- never an office here
# The House key spells the member out; no bioguide was ever resolved for it.
OFFICE_SPELLED = 'container:office:house:HON:NICHOLAS|LALOTA'
# A bioguide that is not on the current roster: an identifier, not a name.  The
# House ledger fixture below prints a HON. string for this one across two
# quarters (the more recent one must win), and never prints one for the next.
OFFICE_HISTORIC = 'container:office:house:HON:Z000099'
OFFICE_HISTORIC_UNRESOLVED = 'container:office:house:HON:Y000088'
ORG_NODE = 'organization:lda:registrant:100'

HON_LEDGER_GENERATION = 'house_personnel_ledger_test_g1_primary'
HON_LEDGER_ROWS = [
    {'member_bioguide': 'Z000099', 'quarter': '2011Q1',
     'office_family_normalized': 'HON. ZELDA OLDNAME'},
    {'member_bioguide': 'Z000099', 'quarter': '2015Q3',
     'office_family_normalized': 'HON. ZELDA QUINCY'},
]

ROSTER = [
    {'member': 'Alpha Member', 'bioguide': 'A000001', 'chamber': 'House'},
    {'member': 'Bravo Member', 'bioguide': 'B000002', 'chamber': 'Senate'},
]

CONTAINERS = [
    (OFFICE_A, 'member_office', 'A000001', 'house', 'HON:A000001'),
    (OFFICE_B, 'member_office', 'B000002', 'senate', 'HON:B000002'),
    (OFFICE_C, 'committee', None, 'senate', 'JUDICIARY'),
    (OFFICE_D, 'leadership', None, 'house', 'SPEAKER OF THE HOUSE'),
    (OFFICE_ADMIN, 'admin', None, 'house', 'ADMIN'),
    (OFFICE_SPELLED, 'member_office', None, 'house', 'HON:NICHOLAS|LALOTA'),
    (OFFICE_HISTORIC, 'member_office', 'Z000099', 'house', 'HON:Z000099'),
    (OFFICE_HISTORIC_UNRESOLVED, 'member_office', 'Y000088', 'house', 'HON:Y000088'),
]

# spine key -> [(container, core_start, core_end_exclusive, status, title_last)]
STINTS = {
    's1': [(OFFICE_A, '2023-01-01', '2024-01-01', 'current', 'CHIEF OF STAFF')],
    's2': [(OFFICE_A, '2019-01-01', '2020-01-01', 'departed', 'STAFF ASSISTANT')],
    # two stints in ONE office: one person, one entry, union of the two windows
    # a THIRD, CURRENT stint is added below (owner ruling 2026-09-07, item 1):
    # the two DEPARTED stints below must still union their days once a later
    # current stint makes the person current overall, so that math stays
    # covered even though a purely-departed multi-stint person is no longer
    # printed here at all.
    's3': [(OFFICE_A, '2015-01-01', '2016-01-01', 'departed', 'INTERN'),
           (OFFICE_A, '2021-01-01', '2022-01-01', 'departed', 'LEGISLATIVE DIRECTOR'),
           (OFFICE_A, '2023-06-01', None, 'current', 'DEPUTY CHIEF OF STAFF')],
    's4': [(OFFICE_C, '2024-01-01', None, 'current', 'ASSOCIATE COUNSEL')],
    's5': [(OFFICE_ADMIN, '2010-01-01', '2011-01-01', 'departed', 'PAGE')],
    's6': [(OFFICE_D, '2025-01-01', None, 'current', 'FLOOR DIRECTOR')],
    's7': [(OFFICE_B, '2022-01-01', '2023-01-01', 'departed', 'SCHEDULER')],
    's8': [(OFFICE_B, '2024-01-01', '2025-01-01', 'departed', 'PRESS SECRETARY')],
    's9': [(OFFICE_B, '2020-01-01', '2021-01-01', 'departed', 'DEPUTY')],
    # current-day fixtures (owner ruling 2026-09-07, item 1): each keeps an
    # otherwise-empty office alive under the current_count >= 1 rule, so the
    # HON-naming cases below (item 2) still have an office to appear on.
    's10': [(OFFICE_HISTORIC, '2024-06-01', None, 'current', 'CONSTITUENT LIAISON')],
    's11': [(OFFICE_HISTORIC_UNRESOLVED, '2024-06-01', None, 'current', 'CASEWORKER')],
    's12': [(OFFICE_SPELLED, '2024-06-01', None, 'current', 'DISTRICT DIRECTOR')],
}
PAYEE = {
    's1': {'first': 'ada', 'last': 'bridger', 'middle_initial': None},
    's2': {'first': 'bo', 'last': 'carver', 'middle_initial': 'q'},
    's3': {'first': 'cy', 'last': 'mcdonald', 'middle_initial': None},
    's4': {'first': 'dee', 'last': 'ellery', 'middle_initial': None},
    's5': {'first': 'ed', 'last': 'foster', 'middle_initial': None},
    's6': {'first': 'fay', 'last': 'gaines', 'middle_initial': None},
    's7': {'first': 'gil', 'last': 'hobbs', 'middle_initial': None},
    's8': {'first': 'hal', 'last': 'irving', 'middle_initial': None},
    's9': {'first': 'ivy', 'last': 'jones', 'middle_initial': None},
    's10': {'first': 'joy', 'last': 'nkemdirim', 'middle_initial': None},
    's11': {'first': 'kip', 'last': 'oduya', 'middle_initial': None},
    's12': {'first': 'lane', 'last': 'usher', 'middle_initial': None},
}

# lobbyist -> (registrant org id, registrant name, registrant_side_tier, matched name, max_tier)
#
# registrant:100 has three filers and registrant:200 has two, so both are listed in
# full.  registrant:300 has exactly ONE, which under the owner's 2026-09-07 ruling
# makes it a NAME-ONLY line -- {id, name, lobbyist_count} with no lobbyists array --
# so the registrant stays in the directory and the single filer is not named.
# lobbyist:6 has no registrant at all and is listed under nothing.
LOBBYISTS = [
    ('lda:lobbyist:1', 'lda:registrant:100', 'ACME ADVOCACY', 'R1', 'ACME ADVOCACY', 'L5'),
    ('lda:lobbyist:2', 'lda:registrant:100', 'ACME ADVOCACY', 'R0', 'ACME ADVOCACY', 'L2'),
    ('lda:lobbyist:3', 'lda:registrant:100', 'ACME ADVOCACY', 'R1', 'ACME ADVOCACY', 'L0'),
    ('lda:lobbyist:4', 'lda:registrant:200', 'BOREAL GROUP LLC', 'R2', 'BOREAL GROUP LLC', 'L4'),
    ('lda:lobbyist:5', 'lda:registrant:200', 'BOREAL GROUP LLC', 'R1', 'ACME ADVOCACY', 'L3'),
    ('lda:lobbyist:6', None, None, 'R0', None, 'L1'),          # no registrant on file at all
    ('lda:lobbyist:7', 'lda:registrant:300', 'SOLO CONSULTING LLC', 'R2',
     'SOLO CONSULTING LLC', 'L3'),                             # the single-filer registrant
]
LOBBYIST_NAMES = {
    'lda:lobbyist:1': 'KENT R HANCE',
    'lda:lobbyist:2': 'ANSHUMAN NEMALI',
    'lda:lobbyist:3': 'MARIA O’BRIEN',
    'lda:lobbyist:4': 'JAMES MACLEOD',
    'lda:lobbyist:5': 'PAT DALY JR',
    'lda:lobbyist:6': 'ROBIN WEST',
    'lda:lobbyist:7': 'DANA SOLO',
}
# Bulk single-filer registrants, minted only where a test needs the name-only lines
# to be a large enough share of the artifact that the byte cap can be made to fall
# between the two singleton modes.  Long names on purpose: the whole point of the
# fallback is that these lines cost bytes.
EXTRA_SINGLETON_NAME = ('LONGLY NAMED GOVERNMENT RELATIONS AND PUBLIC AFFAIRS PARTNERS '
                        'INTERNATIONAL HOLDINGS NUMBER %03d, LLC')

P = F = 0


def ok(name, cond, detail=''):
    global P, F
    P, F = (P + 1, F) if cond else (P, F + 1)
    print(('  OK   ' if cond else 'FAIL  ') + name + (('  :: ' + str(detail)) if (detail and not cond) else ''))


def eq(name, got, want):
    ok(name, got == want, 'got %r want %r' % (got, want))


def sha256(path):
    handle = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            handle.update(block)
    return handle.hexdigest()


def write_ndjson_gz(path, records):
    with gzip.open(path, 'wt', encoding='utf-8') as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True) + '\n')


def spine_id(key):
    return 'hill:person:' + key


def node_score_rows(shape='v5'):
    """The node scores in one of the two shapes that exist.

    v5   heterogeneous: containers, organisations and policy areas are rows here.
    v6   PERSONS ONLY (D-022): only person rows survive; everything else moved to
         staffer_social_groups.ndjson.gz, which group_rows() below mints.
    """
    rows = []
    for key in sorted(STINTS):
        rows.append({'node_id': 'person:' + spine_id(key), 'node_kind': 'person',
                     'identity_tier': 'S1', 'member_bioguides': [], 'co_tenure_degree': 1,
                     'component_id': 0, 'component_size': 9})
    if shape == 'v6':
        return rows
    for node_id, kind, bioguide, chamber, office_key in CONTAINERS:
        rows.append({'node_id': node_id, 'node_kind': 'container', 'container_kind': kind,
                     'member_bioguide': bioguide, 'chamber': chamber, 'office_key': office_key,
                     'membership_degree': 3, 'component_id': 0, 'component_size': 9})
    rows.append({'node_id': ORG_NODE, 'node_kind': 'organization',
                 'organization_source_id': 'lda:registrant:100', 'revolving_degree': 2})
    rows.append({'node_id': 'policy_area:banking', 'node_kind': 'policy_area'})
    return rows


def group_rows():
    """The v6 grouping roster: every container, organisation and policy area once.

    The container rows carry ``name`` where a v5 node row carried ``office_key``;
    on the real 2,000-person dry run those two strings are byte-identical, which is
    why the naming policy needs no second branch.
    """
    rows = []
    for node_id, kind, bioguide, chamber, office_key in CONTAINERS:
        rows.append({'group_id': node_id, 'group_kind': 'container', 'container_kind': kind,
                     'member_bioguide': bioguide, 'chamber': chamber, 'name': office_key,
                     'people_count': 3, 'operator_included': False,
                     'review_status': 'REVIEW_REQUIRED', 'source_system': 'PAYROLL'})
    rows.append({'group_id': ORG_NODE, 'group_kind': 'organization', 'member_bioguide': None,
                 'name': None, 'organization_source_id': 'lda:registrant:100',
                 'people_count': 0, 'operator_included': False,
                 'review_status': 'REVIEW_REQUIRED', 'source_system': 'LDA_REGISTRANT'})
    rows.append({'group_id': 'policy:politico:banking', 'group_kind': 'policy_area',
                 'member_bioguide': None, 'name': 'Banking', 'issue_area': 'Banking',
                 'people_count': 1, 'operator_included': False,
                 'review_status': 'REVIEW_REQUIRED',
                 'source_system': 'POLITICO_PRO_STAFFER_DIRECTORY'})
    return rows


def trajectory_rows(poison_person=None):
    rows = []
    for key in sorted(STINTS):
        stints = [{'container_id': container, 'container_kind': 'member_office',
                   'member_bioguide': None, 'chamber': 'house', 'core_start': start,
                   'core_end_exclusive': end, 'status': status, 'title_last': title,
                   'date_precision': 'DAY', 'periods_observed': 1}
                  for container, start, end, status, title in STINTS[key]]
        rows.append({'spine_person_id': spine_id(key), 'stints': stints, 'moves': [],
                     'stint_count': len(stints), 'move_count': 0, 'identity_tier': 'S1',
                     'member_bioguides': [], 'review_status': 'REVIEW_REQUIRED'})
    if poison_person:
        # A container id arriving where a person id belongs: the negative control
        # for "no organisation or office is ever printed as a person".
        rows.append({'spine_person_id': poison_person, 'stint_count': 1, 'moves': [],
                     'move_count': 0, 'identity_tier': 'S1', 'member_bioguides': [],
                     'review_status': 'REVIEW_REQUIRED',
                     'stints': [{'container_id': OFFICE_A, 'container_kind': 'member_office',
                                 'member_bioguide': 'A000001', 'chamber': 'house',
                                 'core_start': '2024-01-01', 'core_end_exclusive': None,
                                 'status': 'current', 'title_last': 'GHOST',
                                 'date_precision': 'DAY', 'periods_observed': 1}]})
    return rows


def spine_rows(poison_person=None):
    rows = [{'spine_person_id': spine_id(key), 'identity_tier': 'S1',
             'payee_key': PAYEE[key], 'lda_source_person_id': None}
            for key in sorted(STINTS)]
    if poison_person:
        rows.append({'spine_person_id': poison_person, 'identity_tier': 'S1',
                     'payee_key': {'first': 'ghost', 'last': 'office', 'middle_initial': None},
                     'lda_source_person_id': None})
    return rows


def extra_singletons(count):
    """`count` more registrants with exactly one filer each."""
    out = []
    for i in range(count):
        out.append(('lda:lobbyist:9%03d' % i, 'lda:registrant:9%03d' % i,
                    EXTRA_SINGLETON_NAME % i, 'R1', EXTRA_SINGLETON_NAME % i, 'L1'))
    return out


# ---------------------------------------------------------------------------
# THE EMPLOYMENT RULING (owner ruling 2026-09-07): fec/staffer_network/
# lda_employment.py reads three more corpus files -- organisation identity,
# one activity per filing, and the NAMED_ON_ACTIVITY edge tying a lobbyist to
# their filings -- and answers, per filer, who employs them (their most
# recent filing period), contract or in-house, and for a contract filer every
# client they are tied to.  EVERY person the sidecar knows about, LOBBYISTS
# and extra_singletons() included, needs at least one filing here:
# build_organisations refuses the WHOLE build the moment a sidecar-known
# filer has no counterpart in this corpus (missing_from_ledger) -- the two
# files are supposed to describe one corpus, and a gap between them is a
# contradiction, not a row this producer skips past.
#
# RULING_LOBBYISTS carries the sidecar-side row (tier, registrant_side name)
# for four dedicated people who exist to exercise the ruling's four cases
# ([d3]-[d6] below); base_filings() is what the raw corpus says about ALL of
# them, existing LOBBYISTS/extra_singletons included.
# ---------------------------------------------------------------------------
RULING_LOBBYISTS = [
    # [d3] in-house: registrant:400's only-ever client is itself.
    ('lda:lobbyist:401', 'lda:registrant:400', 'QUAIL HOLLOW AFFAIRS', 'R1',
     'QUAIL HOLLOW AFFAIRS', 'L3'),
    ('lda:lobbyist:402', 'lda:registrant:400', 'QUAIL HOLLOW AFFAIRS', 'R2',
     'QUAIL HOLLOW AFFAIRS', 'L2'),
    # [d4] contract, three distinct clients (501) and one (502), same registrant.
    ('lda:lobbyist:501', 'lda:registrant:500', 'MERIDIAN GOVERNMENT RELATIONS', 'R2',
     'MERIDIAN GOVERNMENT RELATIONS', 'L4'),
    ('lda:lobbyist:502', 'lda:registrant:500', 'MERIDIAN GOVERNMENT RELATIONS', 'R1',
     'MERIDIAN GOVERNMENT RELATIONS', 'L1'),
    # [d5] changed registrant: 601 files under 610 (2024 Q1) then 620 (2026 Q2).
    # 612/613 are 610's stable employees so 610 still has 2 once 601 leaves;
    # 621 is 620's stable employee so 620 has 2 including 601.
    ('lda:lobbyist:601', 'lda:registrant:620', 'SUMMIT POLICY PARTNERS', 'R1',
     'SUMMIT POLICY PARTNERS', 'L2'),
    ('lda:lobbyist:612', 'lda:registrant:610', 'HARBOR POINT ADVISORS', 'R0',
     'HARBOR POINT ADVISORS', 'L1'),
    ('lda:lobbyist:613', 'lda:registrant:610', 'HARBOR POINT ADVISORS', 'R2',
     'HARBOR POINT ADVISORS', 'L5'),
    ('lda:lobbyist:621', 'lda:registrant:620', 'SUMMIT POLICY PARTNERS', 'R1',
     'SUMMIT POLICY PARTNERS', 'L3'),
    # [d6] a tie: 701 files registrant:710 TWICE and registrant:720 ONCE, all in
    # the same latest period; 711/721/722 are stable employees so both
    # registrants still have 2 employees once the tie is resolved.
    ('lda:lobbyist:701', 'lda:registrant:710', 'CASCADE ADVOCACY GROUP', 'R1',
     'CASCADE ADVOCACY GROUP', 'L3'),
    ('lda:lobbyist:711', 'lda:registrant:710', 'CASCADE ADVOCACY GROUP', 'R2',
     'CASCADE ADVOCACY GROUP', 'L4'),
    ('lda:lobbyist:721', 'lda:registrant:720', 'TIDEWATER CONSULTING', 'R0',
     'TIDEWATER CONSULTING', 'L1'),
    ('lda:lobbyist:722', 'lda:registrant:720', 'TIDEWATER CONSULTING', 'R1',
     'TIDEWATER CONSULTING', 'L2'),
]
RULING_LOBBYIST_NAMES = {
    'lda:lobbyist:401': 'IVY QUAILE', 'lda:lobbyist:402': 'JAY QUAILE',
    'lda:lobbyist:501': 'REN ASHFORD', 'lda:lobbyist:502': 'MO PALMER',
    'lda:lobbyist:601': 'TESS OKONKWO', 'lda:lobbyist:612': 'IAN MARQUEZ',
    'lda:lobbyist:613': 'ZOE PRENTISS', 'lda:lobbyist:621': 'COLE BRANNIGAN',
    'lda:lobbyist:701': 'MILO STRAND', 'lda:lobbyist:711': 'PRIYA VANCE',
    'lda:lobbyist:721': 'DASH FENWICK', 'lda:lobbyist:722': 'ONYX REDD',
}
# registrant id -> display name, read by the producer from the NEW
# lda_organization_assertions.ndjson.gz (organisation_name() in the producer),
# NOT from the sidecar's own registrant_rows() name field, which the ruling
# leaves in place only for the unplaced/sidecar_latest bookkeeping.
# lda:registrant:650 is lda:lobbyist:6's home in the raw corpus: the sidecar
# carries no registrant for them at all (registrants=[] below, preserving
# lobbyists_with_no_registrant_on_file), but the raw corpus still must, or
# the whole build refuses -- see the module note above.
REGISTRANT_NAMES = {
    'lda:registrant:100': 'ACME ADVOCACY', 'lda:registrant:200': 'BOREAL GROUP LLC',
    'lda:registrant:300': 'SOLO CONSULTING LLC', 'lda:registrant:650': 'ORPHANED FILINGS LLC',
    'lda:registrant:400': 'QUAIL HOLLOW AFFAIRS',
    'lda:registrant:500': 'MERIDIAN GOVERNMENT RELATIONS',
    'lda:registrant:610': 'HARBOR POINT ADVISORS', 'lda:registrant:620': 'SUMMIT POLICY PARTNERS',
    'lda:registrant:710': 'CASCADE ADVOCACY GROUP', 'lda:registrant:720': 'TIDEWATER CONSULTING',
}
# Three genuinely distinct clients, none of them named like MERIDIAN, so
# registrant:500 reads as CONTRACT under lda_employment.normalize_org_name.
CLIENT_NAMES = {
    'lda:client:501a': 'GLOBAL WIDGETS INC', 'lda:client:501b': 'PRAIRIE TELECOM CORP',
    'lda:client:501c': 'NORTHSTAR MARITIME LLC',
}
PERIOD_WORD = {1: 'Q1', 2: 'Q2', 3: 'Q3', 4: 'Q4'}
PERIOD_RANK = {'first_quarter': 1, 'second_quarter': 2, 'third_quarter': 3, 'fourth_quarter': 4}


def period_label(year, period):
    """"2026 Q2" -- mirrors lda_employment.period_label without importing it, so
    this fixture and the module under test cannot both be wrong the same way by
    sharing the one implementation."""
    return '%d %s' % (year, PERIOD_WORD[PERIOD_RANK[period]])


def _self_client(registrant_id):
    """The client id an IN-HOUSE registrant files for itself under: a distinct
    id in the client namespace (registrants and clients never share ids in the
    real corpus) carrying the registrant's OWN display name, so
    lda_employment.normalize_org_name reads the registrant as its own client."""
    return 'lda:client:self:' + registrant_id.rsplit(':', 1)[-1]


def base_filings(bad_person_id=None, singletons=0):
    """Every filing in the raw LDA corpus fixture: one row per (person,
    registrant, client, filing_year, filing_period).

    Threads bad_person_id the same way registrant_rows/summary_rows/
    lobbyist_assertion_rows do, so the poisoned id actually reaches the
    employment ledger and [i]'s negative control still exercises
    assert_lobbyists_only rather than being caught earlier, for an unrelated
    reason, by the corpus/sidecar join-integrity check.
    """
    def pid(person):
        return bad_person_id if (bad_person_id and person == 'lda:lobbyist:1') else person

    filings = []
    for person, org, _name, _tier, _matched, _max in LOBBYISTS:
        if not org:
            continue                              # lda:lobbyist:6 -- see below
        filings.append((pid(person), org, _self_client(org), 2026, 'second_quarter'))
    filings.append(('lda:lobbyist:6', 'lda:registrant:650', _self_client('lda:registrant:650'),
                    2026, 'second_quarter'))
    for person, org, _name, _tier, _matched, _max in extra_singletons(singletons):
        filings.append((person, org, _self_client(org), 2026, 'second_quarter'))
    filings += [
        ('lda:lobbyist:401', 'lda:registrant:400', _self_client('lda:registrant:400'),
         2026, 'second_quarter'),
        ('lda:lobbyist:402', 'lda:registrant:400', _self_client('lda:registrant:400'),
         2026, 'second_quarter'),
        ('lda:lobbyist:501', 'lda:registrant:500', 'lda:client:501a', 2025, 'fourth_quarter'),
        ('lda:lobbyist:501', 'lda:registrant:500', 'lda:client:501b', 2026, 'first_quarter'),
        ('lda:lobbyist:501', 'lda:registrant:500', 'lda:client:501c', 2026, 'second_quarter'),
        ('lda:lobbyist:502', 'lda:registrant:500', 'lda:client:501a', 2026, 'first_quarter'),
        ('lda:lobbyist:601', 'lda:registrant:610', _self_client('lda:registrant:610'),
         2024, 'first_quarter'),
        ('lda:lobbyist:601', 'lda:registrant:620', _self_client('lda:registrant:620'),
         2026, 'second_quarter'),
        ('lda:lobbyist:612', 'lda:registrant:610', _self_client('lda:registrant:610'),
         2025, 'third_quarter'),
        ('lda:lobbyist:613', 'lda:registrant:610', _self_client('lda:registrant:610'),
         2025, 'third_quarter'),
        ('lda:lobbyist:621', 'lda:registrant:620', _self_client('lda:registrant:620'),
         2025, 'third_quarter'),
        # TWO filings, same person, same registrant, same period: this IS what
        # "more filings in the latest period" looks like in the raw corpus.
        ('lda:lobbyist:701', 'lda:registrant:710', _self_client('lda:registrant:710'),
         2026, 'second_quarter'),
        ('lda:lobbyist:701', 'lda:registrant:710', _self_client('lda:registrant:710'),
         2026, 'second_quarter'),
        ('lda:lobbyist:701', 'lda:registrant:720', _self_client('lda:registrant:720'),
         2026, 'second_quarter'),
        ('lda:lobbyist:711', 'lda:registrant:710', _self_client('lda:registrant:710'),
         2025, 'third_quarter'),
        ('lda:lobbyist:721', 'lda:registrant:720', _self_client('lda:registrant:720'),
         2025, 'third_quarter'),
        ('lda:lobbyist:722', 'lda:registrant:720', _self_client('lda:registrant:720'),
         2025, 'third_quarter'),
    ]
    return filings


def organisation_rows_for(filings, singletons=0):
    """One lda_organization_assertions row per distinct registrant/client id the
    filings reference.  A self-client's name is its registrant's own name,
    verbatim -- that identity is what makes the registrant read as in-house.

    `singletons` widens the name map with the bulk extra_singletons()
    registrants ([j2]/[j3]), which are not in the fixed REGISTRANT_NAMES table.
    """
    registrant_names = dict(REGISTRANT_NAMES)
    for _person, org, name, _tier, _matched, _max in extra_singletons(singletons):
        registrant_names[org] = name
    rows = []
    seen = set()
    for _person, registrant, client, _year, _period in filings:
        if registrant not in seen:
            seen.add(registrant)
            rows.append({'source_org_id': registrant, 'display_name': registrant_names[registrant],
                        'organization_kind': 'registrant',
                        'schema_id': 'staffer-network-lda-organization-assertion-v1'})
        if client not in seen:
            seen.add(client)
            name = CLIENT_NAMES.get(client) or registrant_names[registrant]
            rows.append({'source_org_id': client, 'display_name': name,
                        'organization_kind': 'client',
                        'schema_id': 'staffer-network-lda-organization-assertion-v1'})
    return rows


def activity_and_edge_rows(filings):
    """One activity + one NAMED_ON_ACTIVITY edge per filing.  filing_uuid is
    just the row's own position: uniqueness is all that matters, and two
    filings that are otherwise identical (case [d6]) are exactly what a
    second, distinct LD-2 filing in the same period looks like in the corpus.
    """
    activities, edges = [], []
    for index, (person, registrant, client, year, period) in enumerate(filings):
        uuid = 'fixture-filing-%03d' % index
        activity_id = 'lda:activity:%s:0' % uuid
        activities.append({'source_activity_id': activity_id, 'filing_uuid': uuid,
                           'activity_ordinal': 0, 'registrant_source_org_id': registrant,
                           'client_source_org_id': client, 'filing_year': year,
                           'filing_period': period,
                           'schema_id': 'staffer-network-lda-activity-assertion-v1'})
        edges.append({'edge_id': 'lda:edge:%d' % index, 'predicate': 'NAMED_ON_ACTIVITY',
                     'subject_id': person, 'subject_kind': 'person', 'object_id': activity_id,
                     'object_kind': 'lda_activity',
                     'schema_id': 'staffer-network-lda-industry-edge-v1'})
    return activities, edges


def registrant_rows(bad_person_id=None, singletons=0):
    rows = []
    for person, org, name, tier, matched, _max_tier in (
            LOBBYISTS + RULING_LOBBYISTS + extra_singletons(singletons)):
        rows.append({
            'lda_source_person_id': bad_person_id if (bad_person_id and person == 'lda:lobbyist:1')
                                    else person,
            'registrant_side_tier': tier,
            'ld203': {'matched_registrant_name': matched, 'filing_year_count': 3,
                      'first_filing_year': 2021, 'last_filing_year': 2024},
            'registrants': ([{'registrant_source_org_id': org, 'employer_name': name,
                              'employer_name_normalized': (name or '').lower(),
                              'start_year': 2021, 'end_year': 2024, 'filing_years': [2021]}]
                            if org else []),
            'review_status': 'REVIEW_REQUIRED',
        })
    return rows


def summary_rows(unknown_tier=False, bad_person_id=None, singletons=0):
    rows = []
    for person, _org, _name, tier, _matched, max_tier in (
            LOBBYISTS + RULING_LOBBYISTS + extra_singletons(singletons)):
        rows.append({'lda_source_person_id': (bad_person_id
                                              if (bad_person_id and person == 'lda:lobbyist:1')
                                              else person),
                     'max_tier': ('L9' if (unknown_tier and person == 'lda:lobbyist:1')
                                  else max_tier),
                     'registrant_side_tier': tier})
    return rows


def lobbyist_assertion_rows(bad_person_id=None, singletons=0):
    names = dict(LOBBYIST_NAMES)
    names.update(RULING_LOBBYIST_NAMES)
    for person, _org, name, _tier, _matched, _max in extra_singletons(singletons):
        names[person] = 'FILER %s' % person.rsplit(':', 1)[-1]
    rows = []
    for person, name in sorted(names.items()):
        rows.append({'source_person_id': (bad_person_id
                                          if (bad_person_id and person == 'lda:lobbyist:1')
                                          else person),
                     'display_name': name, 'filing_years': [2021], 'activity_support_count': 1})
    return rows


def make_tree(root, poison_person=None, unknown_tier=False, bad_person_id=None,
              sidecar_status='PASS', lda_status='PASS', hon_ledger_status='PASS',
              shape='v5', singletons=0):
    """Mint a generation, a corroboration sidecar and an LDA corpus that all bind.

    ``shape='v6'`` mints the persons-only generation: no container rows in the node
    scores, and a staffer_social_groups.ndjson.gz declared in the receipt under role
    'groups' and bound through the verification receipt like every other file.
    ``singletons`` adds that many extra one-filer registrants.
    """
    os.makedirs(os.path.join(root, 'data', 'engine'), exist_ok=True)
    os.makedirs(os.path.join(root, 'fec'), exist_ok=True)
    with open(os.path.join(root, 'data', 'engine', 'congestion.json'), 'w', encoding='utf-8') as s:
        json.dump(ROSTER, s)
    with open(os.path.join(root, 'fec', 'legislators.csv'), 'w', encoding='utf-8', newline='') as s:
        s.write('bioguide_id,state,party\nA000001,MN,Democrat\nB000002,ID,Republican\n')

    base = os.path.join(root, 'audits', 'staffer_network', GENERATION)
    verification_base = os.path.join(root, 'audits', 'staffer_network', VERIFICATION_DIR)
    spine_base = os.path.join(root, 'audits', 'staffer_network', SPINE_DIR)
    for path in (base, verification_base, spine_base):
        os.makedirs(path, exist_ok=True)

    artifacts = [
        ('staffer_social_node_scores', 'staffer_social_node_scores.ndjson.gz',
         node_score_rows(shape)),
        ('staffer_trajectories', 'staffer_trajectories.ndjson.gz',
         trajectory_rows(poison_person)),
        ('staffer_office_moves', 'staffer_office_moves.ndjson.gz', []),
    ]
    if shape == 'v6':
        artifacts.append(('groups', 'staffer_social_groups.ndjson.gz', group_rows()))
    files = []
    for role, name, records in artifacts:
        path = os.path.join(base, name)
        write_ndjson_gz(path, records)
        files.append({'role': role, 'path': name, 'records': len(records),
                      'bytes': os.path.getsize(path), 'sha256': sha256(path)})
    files.append({'role': 'staffer_social_edges', 'path': 'staffer_social_edges.ndjson.gz',
                  'records': 0, 'bytes': 0, 'sha256': '0' * 64})

    spine_path = os.path.join(spine_base, 'hill_person_spine.ndjson.gz')
    write_ndjson_gz(spine_path, spine_rows(poison_person))

    receipt = {
        'schema_id': 'staffer-network-social-receipt-v1',
        'rule_version': 'staffer-social-network-test',
        'generated_at': '2026-09-03T00:00:00Z',
        'review_status': 'REVIEW_REQUIRED', 'exploratory': True, 'primary': False,
        'canonical_eligible': False, 'canonical_employment_promotions': 0,
        'canonical_identity_promotions': 0, 'primary_weights_emitted': 0,
        'statement': STATEMENT,
        'inputs': [{'role': 'hill_person_spine', 'path': spine_path,
                    'bytes': os.path.getsize(spine_path), 'sha256': sha256(spine_path)}],
        'node_universe': {'person': len(STINTS), 'container': len(CONTAINERS), 'total': 20},
        'edge_counts': {'co_tenure_guaranteed': 11, 'membership': 9,
                        'co_tenure_zero_overlap_REJECTED_never_emitted': ZERO_OVERLAP,
                        'total_rows_emitted': 20},
        'overlap_rule_check': {'pairs_REJECTED_for_no_overlap': ZERO_OVERLAP,
                               'rule': 'no person-person edge without actual temporal overlap'},
        'files': files,
    }
    receipt_path = os.path.join(base, 'staffer_social_network_receipt.json')
    with open(receipt_path, 'w', encoding='utf-8') as stream:
        json.dump(receipt, stream, sort_keys=True)
    manifest_path = os.path.join(base, 'staffer_social_network_manifest.json')
    with open(manifest_path, 'w', encoding='utf-8') as stream:
        json.dump({'generation_id': GENERATION, 'schema_id': 'staffer-network-manifest-v1'},
                  stream, sort_keys=True)

    verification = {
        'schema_id': 'staffer-network-social-verification-receipt-v1',
        'status': 'PASS', 'failures': [],
        'twin_byte_identity': [
            {'path': 'staffer_social_network_receipt.json',
             'primary_sha256': sha256(receipt_path), 'twin_sha256': sha256(receipt_path),
             'identical': True},
            {'path': 'staffer_social_network_manifest.json',
             'primary_sha256': sha256(manifest_path), 'twin_sha256': sha256(manifest_path),
             'identical': True, 'note': 'generation_id differs by design'},
        ],
        'primary_file_integrity': [
            {'path': entry['path'], 'receipt_sha256': entry['sha256'],
             'recomputed_sha256': entry['sha256'], 'match': True}
            for entry in files if entry['role'] != 'staffer_social_edges'],
        'promotion_tripwire': {'receipt_canonical_employment_promotions': 0},
    }
    with open(os.path.join(verification_base,
                           'staffer_social_network_verification_receipt.json'),
              'w', encoding='utf-8') as stream:
        json.dump(verification, stream, sort_keys=True)

    # --------------------------------------------------------- HON ledger
    # Owner ruling 2026-09-07, item 2: HON:<bioguide> offices off the current
    # roster are named from this ledger instead of left as a bare key.
    hon_base = os.path.join(root, 'audits', 'staffer_network', HON_LEDGER_GENERATION)
    os.makedirs(hon_base, exist_ok=True)
    hon_quarters_path = os.path.join(hon_base, 'house_member_quarters.ndjson.gz')
    write_ndjson_gz(hon_quarters_path, HON_LEDGER_ROWS)
    with open(os.path.join(hon_base, 'house_personnel_ledger_receipt.json'), 'w',
              encoding='utf-8') as stream:
        json.dump({'schema_id': 'staffer-network-house-personnel-ledger-receipt-v1',
                   'status': hon_ledger_status,
                   'outputs': [{'path': 'house_member_quarters.ndjson.gz',
                                'role': 'member_office_quarters',
                                'rows': len(HON_LEDGER_ROWS),
                                'bytes': os.path.getsize(hon_quarters_path),
                                'sha256': sha256(hon_quarters_path)}]},
                  stream, sort_keys=True)

    # ------------------------------------------------------------- sidecar
    side = os.path.join(root, 'audits', 'staffer_network', SIDECAR)
    os.makedirs(side, exist_ok=True)
    side_files = {}
    for name, records in (('registrant_side_records.ndjson.gz',
                           registrant_rows(bad_person_id, singletons)),
                          ('tiered_person_summary_25340.ndjson.gz',
                           summary_rows(unknown_tier, bad_person_id, singletons))):
        path = os.path.join(side, name)
        write_ndjson_gz(path, records)
        side_files[name] = {'bytes': os.path.getsize(path), 'rows': len(records),
                            'sha256': sha256(path)}
    with open(os.path.join(side, 'tiered_corroboration_receipt.json'), 'w',
              encoding='utf-8') as stream:
        json.dump({'schema_id': 'staffer-network-tiered-corroboration-receipt-v1',
                   'status': sidecar_status, 'failures': [], 'files': side_files,
                   'review_status': 'REVIEW_REQUIRED'}, stream, sort_keys=True)

    # ----------------------------------------------------------------- LDA
    # Deliberately OUTSIDE root: the real corpus lives in a machine-local store, and
    # how its path is reported on a shippable page is part of the contract.
    #
    # FOUR files now, not one (owner ruling 2026-09-07): the lobbyist assertions
    # for names, plus the three fec/staffer_network/lda_employment.py reads --
    # organisation identity, one activity per filing, and the NAMED_ON_ACTIVITY
    # edge tying a lobbyist to their filings.  bind_lda hashes all four against
    # this one receipt before build_organisations reads a byte of any of them.
    lda_root = os.path.join(os.path.dirname(root), os.path.basename(root) + '-lda-store')
    lda_base = os.path.join(lda_root, LDA_GENERATION)
    os.makedirs(lda_base, exist_ok=True)
    lobbyist_records = lobbyist_assertion_rows(bad_person_id, singletons)
    filings = base_filings(bad_person_id, singletons)
    activity_records, edge_records = activity_and_edge_rows(filings)
    corpus_files = [
        ('lobbyist_assertions', 'lda_lobbyist_assertions.ndjson.gz', lobbyist_records,
         'restricted-machine-local'),
        ('organization_assertions', 'lda_organization_assertions.ndjson.gz',
         organisation_rows_for(filings, singletons), None),
        ('activity_assertions', 'lda_activity_assertions.ndjson.gz', activity_records, None),
        ('industry_edges', 'lda_industry_edges.ndjson.gz', edge_records, None),
    ]
    receipt_files = []
    for role, name, records, privacy_class in corpus_files:
        path = os.path.join(lda_base, name)
        write_ndjson_gz(path, records)
        entry = {'role': role, 'path': name, 'records': len(records),
                'bytes': os.path.getsize(path), 'sha256': sha256(path)}
        if privacy_class:
            entry['privacy_class'] = privacy_class
        receipt_files.append(entry)
    with open(os.path.join(lda_base, 'lda_source_native_corpus_receipt.json'), 'w',
              encoding='utf-8') as stream:
        json.dump({'schema_id': 'staffer-network-lda-source-native-receipt-v1',
                   'status': lda_status, 'failures': [], 'files': receipt_files},
                  stream, sort_keys=True)
    return lda_root


OFFICES_FILE = 'staffer_offices_section.json'
ORGANISATIONS_FILE = 'staffer_organisations_section.json'
CLIENTS_FILE = 'staffer_clients_section.json'


def paths_in(out_dir):
    return (os.path.join(out_dir, OFFICES_FILE), os.path.join(out_dir, ORGANISATIONS_FILE),
            os.path.join(out_dir, CLIENTS_FILE))


def client_index(payload):
    """The clients artifact re-expanded to {client id: [(filer id, period label)]}.

    Written independently of lda_employment.load_from_clients_artifact so this
    fixture and the module under test cannot both be wrong the same way by sharing
    one implementation -- the same reason period_label() is duplicated above.
    """
    labels = payload['meta']['period_labels']
    expanded = {}
    for position, client in enumerate(payload['c']):
        expanded[client] = [(payload['f'][index], labels[payload['tp'][position][slot]])
                            for slot, index in enumerate(payload['t'][position])]
    return expanded


def run(root, out_dir, lda_root, extra=(), epoch='1750000000'):
    """Drive the shipped producer into `out_dir`, which it fills with three files."""
    env = dict(os.environ)
    env.pop('MONEYBALL_ROOT', None)
    env['SOURCE_DATE_EPOCH'] = epoch
    env['PYTHONIOENCODING'] = 'utf-8'
    argv = [sys.executable, PRODUCER, '--root', root, '--generation', GENERATION,
            '--sidecar-generation', SIDECAR, '--lda-root', lda_root,
            '--lda-generation', LDA_GENERATION,
            '--hon-ledger-generation', HON_LEDGER_GENERATION,
            '--out-dir', out_dir, '--no-glassbox'] + list(extra)
    result = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', env=env)
    return result.returncode, (result.stdout or '') + (result.stderr or '')


def load(path):
    with open(path, encoding='utf-8') as stream:
        return json.load(stream)




def main():
    workspace = tempfile.mkdtemp(prefix='staffer_groups_tests_')
    try:
        # ---------------------------------------------------- (a) happy path
        print('[a] happy path')
        root = os.path.join(workspace, 'happy')
        lda_root = make_tree(root)
        out_dir = os.path.join(root, 'out')
        off_path, org_path, cli_path = paths_in(out_dir)
        code, text = run(root, out_dir, lda_root)
        ok('producer exits 0', code == 0, text.strip()[-500:])
        ok('the offices artifact is written', os.path.exists(off_path))
        ok('the organisations artifact is written', os.path.exists(org_path))
        ok('the clients artifact is written', os.path.exists(cli_path))
        if not all(os.path.exists(p) for p in (off_path, org_path, cli_path)):
            print('\n%d passed, %d failed' % (P, F))
            return 1
        off_raw = open(off_path, encoding='utf-8').read()
        org_raw = open(org_path, encoding='utf-8').read()
        cli_raw = open(cli_path, encoding='utf-8').read()
        off_payload, org_payload = json.loads(off_raw), json.loads(org_raw)
        cli_payload = json.loads(cli_raw)
        eq('the offices payload is exactly {meta, offices}',
           sorted(off_payload), ['meta', 'offices'])
        eq('the organisations payload is exactly {meta, organisations}',
           sorted(org_payload), ['meta', 'organisations'])
        eq('the clients payload is exactly meta plus the nine normalized tables',
           sorted(cli_payload),
           ['c', 'cn', 'e', 'en', 'f', 'fe', 'fn', 'meta', 't', 'tp'])
        meta = off_payload['meta']
        ometa = org_payload['meta']
        cmeta = cli_payload['meta']
        offices = {o['id']: o for o in off_payload['offices']}
        orgs = {o['id']: o for o in org_payload['organisations']}
        clients = client_index(cli_payload)

        # -------------------------------------------- (a2) the split contract
        # The two halves are separate documents that share a generation and
        # nothing else.  Each has to be identifiable on its own -- a reader who
        # finds `offices` must be able to tell it is not a truncated copy of the
        # retired combined v1 document -- and each has to carry its own byte
        # budget, because that is the whole reason the split exists.
        print('[a2] three artifacts, three schemas, three budgets')
        eq('the offices artifact names its own schema',
           meta['schema_id'], 'staffer-offices-section-v2')
        eq('the organisations artifact names its own schema, v4 as of the '
           '2026-09-07 client normalization (v3 carried client cards; a reader '
           'that expects represented_by must not be handed a v4 quietly)',
           ometa['schema_id'], 'staffer-organisations-section-v4')
        eq('the clients artifact names its own schema',
           cmeta['schema_id'], 'staffer-clients-section-v1')
        eq('all three are written by the same producer version',
           (meta['producer_version'], ometa['producer_version'], cmeta['producer_version']),
           ('staffer-groups-section-v4',) * 3)
        eq('each artifact says which part it is',
           (meta['part'], ometa['part'], cmeta['part']),
           ('offices', 'organisations', 'clients'))
        eq('each artifact names its siblings, so a half-built page is detectable',
           (meta['siblings'], ometa['siblings'], cmeta['siblings']),
           (['staffer_organisations_section.json', 'staffer_clients_section.json'],
            ['staffer_offices_section.json', 'staffer_clients_section.json'],
            ['staffer_offices_section.json', 'staffer_organisations_section.json']))
        eq('each artifact carries the byte cap it was held to',
           (meta['max_bytes'], ometa['max_bytes'], cmeta['max_bytes']), (5000000,) * 3)
        eq('each artifact measures itself, not the set',
           (meta['bytes'], ometa['bytes'], cmeta['bytes']),
           (len(off_raw.encode('utf-8')), len(org_raw.encode('utf-8')),
            len(cli_raw.encode('utf-8'))))
        eq('all three bind the same generation',
           (meta['generation'], ometa['generation'], cmeta['generation']), (GENERATION,) * 3)
        eq('the offices artifact says where its containers came from',
           meta['container_source'], 'staffer_social_node_scores')
        ok('each artifact names the files it actually opened, not only the ones '
           'it bound', 'staffer_trajectories' in meta['reads']
           and 'registrant_side_records' in ometa['reads'], (meta['reads'], ometa['reads']))

        print('[b] office selection')
        eq('only offices with at least one CURRENT person are emitted '
           '(owner ruling 2026-09-07, item 1)',
           sorted(offices),
           sorted([OFFICE_A, OFFICE_C, OFFICE_D, OFFICE_SPELLED, OFFICE_HISTORIC,
                   OFFICE_HISTORIC_UNRESOLVED]))
        ok('an admin container is not an office', OFFICE_ADMIN not in offices)
        ok('an organization node is never an office', ORG_NODE not in offices)
        ok('an office with no current staff at all is excluded, not shown empty',
           OFFICE_B not in offices, sorted(offices))
        eq('the office kinds are stated in meta, and admin is not one of them',
           meta['office_kinds'], ['member_office', 'committee', 'leadership'])
        ok('meta says why admin is out, in the payload rather than only in the code',
           'administrative payroll header' in meta['excluded_container_kinds_note'],
           meta.get('excluded_container_kinds_note'))
        eq('a member office is named for its member', offices[OFFICE_A]['name'], 'Alpha Member')
        eq('a committee keeps the record own office_key', offices[OFFICE_C]['name'], 'JUDICIARY')
        eq('a leadership office keeps the record own office_key',
           offices[OFFICE_D]['name'], 'SPEAKER OF THE HOUSE')
        eq('a key that spells the member out is rendered by the name standard',
           offices[OFFICE_SPELLED]['name'], 'Nicholas Lalota')
        eq('a HON:<bioguide> key off the roster is named from the House ledger, '
           'the most recent quarter winning (owner ruling 2026-09-07, item 2)',
           offices[OFFICE_HISTORIC]['name'], 'Zelda Quincy')
        eq('a HON:<bioguide> key the ledger never printed a name for keeps the '
           'key, not an invented name',
           offices[OFFICE_HISTORIC_UNRESOLVED]['name'], 'HON:Y000088')
        ok('the naming rule is stated in meta',
           'HON:FIRST|LAST' in meta['office_name_policy'], meta.get('office_name_policy'))
        ok('the naming rule names the House ledger as the source, not an invention',
           'House personnel ledger' in meta['office_name_policy'], meta.get('office_name_policy'))
        eq('the member bioguide rides along', offices[OFFICE_A]['member_bioguide'], 'A000001')
        eq('a committee has no member bioguide', offices[OFFICE_C]['member_bioguide'], None)

        print('[b2] the HON naming yield is counted, not just applied silently')
        eq('the ledger-resolved office is counted',
           meta['counts']['offices_hon_ledger_resolved'], 1)
        eq('the never-printed office is counted as unnamed, not silently dropped',
           meta['counts']['offices_unnamed'], 1)

        print('[c] people in an office')
        alpha = offices[OFFICE_A]
        eq('people_count counts distinct people ever attached, not stints',
           alpha['people_count'], 3)
        eq('current_count counts people with a CURRENT stint', alpha['current_count'], 2)
        eq('earlier_count counts everyone else, and a two-stint departed person '
           'is still one count', alpha['earlier_count'], 1)
        eq('current people are listed here, in full, no cap at this size',
           len(alpha['people']), 2)
        ok('every listed office person is current (owner ruling 2026-09-07, item '
           '1: no earlier-staff NAME ever reaches this payload)',
           all(p['current'] for p in alpha['people']), alpha['people'])
        names = [p['display_name'] for p in alpha['people']]
        eq('names render through the name standard, not the casefolded source, '
           'and the list sorts by display name',
           names, ['Ada Bridger', 'Cy McDonald'])
        person = {p['display_name']: p for p in alpha['people']}
        eq('a person entry exposes exactly the contracted fields',
           sorted(person['Ada Bridger']),
           ['current', 'display_name', 'id', 'tenure_days', 'title_last'])
        eq('title_last is the title on the representative stint',
           person['Ada Bridger']['title_last'], 'CHIEF OF STAFF')
        eq('tenure_days counts the dated window', person['Ada Bridger']['tenure_days'], 365)
        eq('two DEPARTED stints in one office still union their days once a '
           'later CURRENT stint makes the person current overall',
           person['Cy McDonald']['tenure_days'], 730)
        eq('a current stint supplies the title, ranked ahead of any departed '
           'stint regardless of date',
           person['Cy McDonald']['title_last'], 'DEPUTY CHIEF OF STAFF')
        ok('a departed person is counted in earlier_count and named nowhere on '
           'the page (owner ruling 2026-09-07, item 1)',
           not any(p['display_name'] == 'Bo Carver'
                   for office in off_payload['offices'] for p in office['people']))
        ok('a person whose only stint is in an excluded container is nowhere',
           not any(p['display_name'] == 'Ed Foster'
                   for office in off_payload['offices'] for p in office['people']))
        eq('an undated current stint reports null days',
           {p['display_name']: p['tenure_days'] for p in offices[OFFICE_C]['people']},
           {'Dee Ellery': None})
        eq('no office in this fixture hits the safety cap, and the counter says so',
           (meta['counts']['offices_over_current_cap'],
            meta['counts']['current_people_not_named']), (0, 0))

        print('[d] organisations')
        eq('every registrant the employment ruling reaches becomes an '
           'organisation, single-filer registrants included -- and NO client '
           'does: the client half moved to its own normalized artifact',
           sorted(orgs),
           sorted(['lda:registrant:100', 'lda:registrant:200', 'lda:registrant:300',
                   'lda:registrant:400', 'lda:registrant:500', 'lda:registrant:610',
                   'lda:registrant:620', 'lda:registrant:650', 'lda:registrant:710',
                   'lda:registrant:720']))
        eq('every organisation row is a registrant card',
           sorted({o['kind'] for o in org_payload['organisations']}), ['registrant'])
        ok('no organisation row carries a represented_by roster any more',
           not any('represented_by' in o for o in org_payload['organisations']))
        eq('and the clients artifact holds every client the ruling reaches',
           sorted(clients),
           ['lda:client:501a', 'lda:client:501b', 'lda:client:501c'])
        acme = orgs['lda:registrant:100']
        eq('the registrant name comes from the NEW lda_organization_assertions '
           'file, not the sidecar\'s own registrant_rows() name',
           acme['name'], 'ACME ADVOCACY')
        eq('lobbyist_count counts everyone whose most recent filing period names '
           'this registrant (the ruling), not everyone who ever filed under it',
           acme['lobbyist_count'], 3)
        eq('the card carries the employer line ONCE, unconditioned on any '
           'person\'s tier -- the card IS the employer under the ruling',
           acme['employer_line'], EMPLOYER_ON % 'ACME ADVOCACY')
        eq('and the card states in-house/contract once too',
           acme['engagement'], IN_HOUSE)
        eq('a lobbyist entry exposes exactly the contracted fields -- '
           'employer_line and engagement moved to the CARD, so the per-person '
           'row is left with what actually varies: tier, employer_as_of and, '
           'for a contract filer only, client_count',
           sorted(acme['lobbyists'][0]),
           ['display_name', 'employer_as_of', 'id', 'lda_status_label', 'registrant_tier'])
        by_id = {p['id']: p for org in org_payload['organisations']
                 for p in (org.get('lobbyists') or ())}
        eq('lobbyist names render through the name standard',
           by_id['lda:lobbyist:1']['display_name'], 'Kent R. Hance')
        eq('L5 reads as confirmed', by_id['lda:lobbyist:1']['lda_status_label'], CONFIRMED)
        eq('L4 reads as confirmed', by_id['lda:lobbyist:4']['lda_status_label'], CONFIRMED)
        eq('L2 reads as likely', by_id['lda:lobbyist:2']['lda_status_label'], LIKELY)
        eq('L3 reads as likely', by_id['lda:lobbyist:5']['lda_status_label'], LIKELY)
        eq('L0 reads as no record found', by_id['lda:lobbyist:3']['lda_status_label'], NO_RECORD)
        eq('R1 rides along on the row as registrant_tier, two characters',
           by_id['lda:lobbyist:1']['registrant_tier'], 'R1')
        eq('R2 rides along the same way',
           by_id['lda:lobbyist:4']['registrant_tier'], 'R2')
        eq('R0 rides along too -- no tier gates the card employer_line any more '
           '(ruled 2026-09-07): R0 is not withheld, it is just a lower '
           'corroboration tier on the SAME row',
           by_id['lda:lobbyist:2']['registrant_tier'], 'R0')
        eq('employer_as_of is the period label the ruling put on file for them',
           by_id['lda:lobbyist:1']['employer_as_of'], '2026 Q2')
        ok('an in-house employee carries no client_count key at all',
           'client_count' not in by_id['lda:lobbyist:1'], by_id['lda:lobbyist:1'])
        # lda:lobbyist:6 carries NO registrant in the sidecar's OWN registrant
        # list (registrants=[] in registrant_rows()) -- but every sidecar-known
        # filer must still resolve in the raw corpus (base_filings() gives them
        # one, under a dedicated registrant:650) or the whole build refuses
        # (missing_from_ledger).  RETIRED: "no registrant on file" no longer
        # means "no employer" under the ruling, because the ruling reads the
        # raw corpus directly and does not defer to the sidecar's own,
        # separately-computed registrant match.  What survives is narrower --
        # the sidecar's OWN match still failed for them, and that is still
        # counted -- and lda:lobbyist:6 still is not INDIVIDUALLY NAMED, but
        # now because their real employer happens to be a singleton, not
        # because they have none.
        ok('a lobbyist unplaced by the sidecar\'s OWN registrant match may still '
           'have a real employer under the ruling, so is not necessarily '
           'employer-less any more',
           'lda:lobbyist:6' not in by_id)
        orphaned = orgs['lda:registrant:650']
        eq('and here is that real employer: a genuine singleton the sidecar '
           'never knew about', (orphaned['lobbyist_count'], 'lobbyists' in orphaned),
           (1, False))
        eq('the sidecar\'s OWN placement miss is still counted -- a narrower, '
           'sidecar-only statistic now, not a proxy for "has no employer"',
           ometa['counts']['lobbyists_with_no_registrant_on_file'], 1)
        eq('the label table ships in meta, exactly as printed',
           ometa['lda_status_labels'],
           {'L0': NO_RECORD, 'L1': NO_RECORD, 'L2': LIKELY, 'L3': LIKELY,
            'L4': CONFIRMED, 'L5': CONFIRMED})

        print('[d3] in-house: the registrant\'s only-ever client is itself')
        quail = orgs['lda:registrant:400']
        eq('a two-employee in-house registrant is listed in full',
           sorted(p['id'] for p in quail['lobbyists']),
           ['lda:lobbyist:401', 'lda:lobbyist:402'])
        eq('the card states in_house', quail['engagement'], IN_HOUSE)
        by_id_401 = {p['id']: p for p in quail['lobbyists']}
        ok('an in-house employee\'s row carries no client_count key',
           'client_count' not in by_id_401['lda:lobbyist:401'], by_id_401['lda:lobbyist:401'])
        ok('and neither of QUAIL HOLLOW\'s employees is in the clients artifact '
           'at all -- an in-house filer has no client roster, so they are not '
           'even in the filer table',
           not ({'lda:lobbyist:401', 'lda:lobbyist:402'} & set(cli_payload['f'])),
           cli_payload['f'])

        print('[d4] contract: a filer with three distinct clients, and a '
              'colleague at the same registrant with one')
        meridian = orgs['lda:registrant:500']
        eq('the card states contract, because it files for clients other than '
           'itself', meridian['engagement'], CONTRACT)
        eq('client_count is per person: 3 for the filer with three clients',
           by_id['lda:lobbyist:501']['client_count'], 3)
        eq('and 1 for the colleague at the very same (therefore also contract) '
           'registrant', by_id['lda:lobbyist:502']['client_count'], 1)
        eq('the employer on record is the latest of the three periods filed',
           by_id['lda:lobbyist:501']['employer_as_of'], period_label(2026, 'second_quarter'))
        eq('client:501a is represented by BOTH filers -- one person may '
           'represent many clients and one client may be represented by many '
           'people; neither is a duplicate',
           sorted(person for person, _period in clients['lda:client:501a']),
           ['lda:lobbyist:501', 'lda:lobbyist:502'])
        eq('client:501b is represented by the three-client filer alone',
           [person for person, _period in clients['lda:client:501b']], ['lda:lobbyist:501'])
        eq('client:501c likewise',
           [person for person, _period in clients['lda:client:501c']], ['lda:lobbyist:501'])
        ok('the SAME person appearing under three different clients is the '
           'ruling, not a duplicate: 501 is tied to all three',
           all('lda:lobbyist:501' in [p for p, _k in clients[c]]
               for c in ('lda:client:501a', 'lda:client:501b', 'lda:client:501c')))
        eq('a tie carries the latest period that pairing appeared on, resolved '
           'through meta.period_labels rather than written on the row',
           dict(clients['lda:client:501a']),
           {'lda:lobbyist:501': period_label(2025, 'fourth_quarter'),
            'lda:lobbyist:502': period_label(2026, 'first_quarter')})

        print('[d5] a filer who changed registrant')
        harbor = orgs['lda:registrant:610']
        summit = orgs['lda:registrant:620']
        eq('the mover is NOT listed under their FORMER registrant',
           'lda:lobbyist:601' not in [p['id'] for p in harbor['lobbyists']], True)
        eq('the former registrant still shows its two OTHER, stable employees',
           sorted(p['id'] for p in harbor['lobbyists']),
           ['lda:lobbyist:612', 'lda:lobbyist:613'])
        eq('the mover IS listed under their CURRENT registrant',
           'lda:lobbyist:601' in [p['id'] for p in summit['lobbyists']], True)
        mover = {p['id']: p for p in summit['lobbyists']}['lda:lobbyist:601']
        eq('employer_as_of is the CURRENT registrant\'s period',
           mover['employer_as_of'], period_label(2026, 'second_quarter'))
        eq('former_employers names the OLD registrant with the period they '
           'were last filed under it',
           mover['former_employers'],
           [{'id': 'lda:registrant:610', 'name': 'HARBOR POINT ADVISORS',
             'last_filed': period_label(2024, 'first_quarter')}])
        stable = {p['id']: p for p in harbor['lobbyists']}['lda:lobbyist:612']
        ok('a stable employee who never changed registrant carries no '
           'former_employers key at all', 'former_employers' not in stable, stable)

        print('[d6] a tie broken by filings in the latest period')
        cascade = orgs['lda:registrant:710']
        tidewater = orgs['lda:registrant:720']
        eq('the 2-filing registrant in the tied period is the employer',
           'lda:lobbyist:701' in [p['id'] for p in cascade['lobbyists']], True)
        eq('the 1-filing registrant in that SAME period is NOT',
           'lda:lobbyist:701' not in [p['id'] for p in tidewater['lobbyists']], True)
        eq('tidewater still shows its two other, stable employees',
           sorted(p['id'] for p in tidewater['lobbyists']),
           ['lda:lobbyist:721', 'lda:lobbyist:722'])
        ok('the tie-break rule this build actually used is recorded, not just '
           'the outcome',
           ometa['counts']['employer_tie_rules'].get(TIE_BY_PERIOD_FILINGS, 0) >= 1,
           ometa['counts']['employer_tie_rules'])
        winner = {p['id']: p for p in cascade['lobbyists']}['lda:lobbyist:701']
        eq('the LOSING side of a same-period tie is still a former employer: '
           '`formers` is "every OTHER registrant", not "every earlier one", so '
           'the tie-loser shows up at the SAME period as the real employer',
           winner.get('former_employers'),
           [{'id': 'lda:registrant:720', 'name': 'TIDEWATER CONSULTING',
             'last_filed': period_label(2026, 'second_quarter')}])

        # ------------------------------------------------- (d7) direct checks
        # assert_clients_normalized and assert_no_client_cards guard shapes the
        # real ledger cannot produce end to end: filers_by_client() only ever
        # iterates ledger.clients, an in-house person's ledger.clients entry is
        # unconditionally {} (build_ledger), and build_clients writes its tie
        # arrays from the same index map it writes `f` from, so no fixture driven
        # through the full producer can mint an in-house filer in the client
        # tables or a dangling index.  Exercised directly instead, the same way
        # [i2] and [m] import the producer module rather than shell out to it.
        # These are the NEGATIVE CONTROLS on the normalization: an index that
        # resolves to nothing is the one failure a normalized payload can have
        # that a card-shaped one could not, and a page cannot detect it.
        print('[d7] direct checks: the normalization guards')
        sys.path.insert(0, os.path.dirname(FEC))
        from fec.surfaces import build_staffer_groups_section as producer_module
        from fec.staffer_network import lda_employment as employment_module

        def raises_contradiction(fn, *args):
            try:
                fn(*args)
            except producer_module.InputContradiction as exc:
                return True, str(exc)
            return False, 'did not raise'

        def clients_copy(**changes):
            copy = json.loads(cli_raw)
            copy.update(changes)
            return copy

        engagement_ok = {person: employment_module.CONTRACT for person in cli_payload['f']}
        raised_ctl, detail_ctl = raises_contradiction(
            producer_module.assert_clients_normalized, json.loads(cli_raw), engagement_ok)
        ok('the artifact the producer actually wrote passes every guard '
           '(control on the controls below)', not raised_ctl, detail_ctl)

        dangling = clients_copy()
        dangling['t'] = [list(row) for row in dangling['t']]
        dangling['t'][0][0] = len(dangling['f'])          # one past the last filer
        raised, detail = raises_contradiction(
            producer_module.assert_clients_normalized, dangling, engagement_ok)
        ok('a tie index that resolves to no filer REFUSES -- the one failure a '
           'normalized payload can have that a card could not, and the page '
           'cannot see it',
           raised and 'resolves to no filer' in detail, detail)

        short = clients_copy()
        short['cn'] = short['cn'][:-1]
        raised, detail = raises_contradiction(
            producer_module.assert_clients_normalized, short, engagement_ok)
        ok('parallel arrays of different lengths refuse',
           raised and 'parallel arrays' in detail, detail)

        bad_period = clients_copy()
        bad_period['tp'] = [list(row) for row in bad_period['tp']]
        bad_period['tp'][0][0] = len(cmeta['period_labels'])
        raised, detail = raises_contradiction(
            producer_module.assert_clients_normalized, bad_period, engagement_ok)
        ok('a period code that resolves to no label refuses',
           raised and 'resolves to no label' in detail, detail)

        leaked = clients_copy()
        leaked['t'] = [list(row) for row in leaked['t']]
        leaked['t'][0] = list(leaked['t'][0]) + [leaked['fn'][0]]
        raised, detail = raises_contradiction(
            producer_module.assert_clients_normalized, leaked, engagement_ok)
        ok('a NAME leaked back into a tie row refuses -- names live once, in '
           'cn/fn/en, and that is the whole point of this artifact', raised, detail)

        in_house_engagement = dict(engagement_ok)
        in_house_engagement[cli_payload['f'][0]] = employment_module.IN_HOUSE
        raised, detail = raises_contradiction(
            producer_module.assert_clients_normalized, json.loads(cli_raw), in_house_engagement)
        ok('an in-house filer in the client tables refuses',
           raised and 'in-house filer' in detail, detail)

        raised, detail = raises_contradiction(
            producer_module.assert_no_client_cards,
            [{'id': 'lda:client:poison', 'kind': producer_module.KIND_CLIENT,
              'name': 'POISONED CLIENT', 'represented_by_count': 1,
              'represented_by': [{'id': 'lda:lobbyist:9999', 'display_name': 'GHOST FILER',
                                  'latest_period': '2026 Q2'}], 'more': 0}])
        ok('a client card left in the organisations artifact refuses',
           raised and 'not registrant cards' in detail, detail)
        raised_ok, detail_ok = raises_contradiction(
            producer_module.assert_no_client_cards, org_payload['organisations'])
        ok('the real organisations rows pass it (negative control on the '
           'negative control)', not raised_ok, detail_ok)

        # ------------------------------- (d2) the single-filer registrant rule
        # Owner ruling 2026-09-07.  A registrant with one filer is a directory
        # line and nothing more.  The ABSENCE of the lobbyists key is the
        # contract, not an empty list: the page has to be able to tell "one
        # person on file, not named here" from "nobody on file", and an empty
        # array says the second thing.
        print('[d2] single-filer registrants are name-only lines')
        solo = orgs['lda:registrant:300']
        eq('a single-filer registrant is {id, kind, name, lobbyist_count, '
           'engagement} -- a name-only line names no person, but it still says '
           'what SORT of place the registrant is, because that is a fact about '
           'the organisation and not about its one unnamed filer',
           sorted(solo), ['engagement', 'id', 'kind', 'lobbyist_count', 'name'])
        eq('kind is still registrant, even name-only', solo['kind'], 'registrant')
        eq('and it is in-house, same as any other card', solo['engagement'], IN_HOUSE)
        ok('it carries NO lobbyists key at all, so absence and emptiness stay '
           'distinguishable on the page', 'lobbyists' not in solo, sorted(solo))
        eq('it still reports its real filer count', solo['lobbyist_count'], 1)
        eq('and keeps the registrant name, so the registrant stays findable',
           solo['name'], 'SOLO CONSULTING LLC')
        ok('its single filer is named nowhere in the artifact',
           'DANA SOLO' not in org_raw and 'Dana Solo' not in org_raw)
        eq('the singleton mode in force is stated', ometa['singleton_mode'], 'name_only_lines')
        eq('and the count is carried whether or not the lines are -- TWO '
           'singletons in this fixture: SOLO CONSULTING (a real one-filer '
           'registrant) and ORPHANED FILINGS (lda:lobbyist:6\'s real employer, '
           'see [d])',
           ometa['singleton_registrants'], 2)
        eq('the counts reconcile: full + singleton = every registrant',
           (ometa['counts']['organisations_listed_in_full']
            + ometa['counts']['singleton_registrants']),
           ometa['counts']['registrants_total'])
        ok('the rule is stated in the payload, both halves of it',
           'name-only line' in ometa['singleton_rule']
           and 'singleton_registrants' in ometa['singleton_rule'], ometa['singleton_rule'])
        ok('every full-listed registrant really has two or more filers',
           all(o['lobbyist_count'] >= 2 for o in org_payload['organisations']
               if o.get('kind') == 'registrant' and 'lobbyists' in o))

        print('[e] once per group, and only people')
        for payload_part, key, member_key in ((off_payload, 'offices', 'people'),
                                              (org_payload, 'organisations', 'lobbyists')):
            for group in payload_part[key]:
                ids = [p['id'] for p in (group.get(member_key) or ())]
                ok('%s %s lists each %s once' % (key[:-1], group['id'], member_key[:-1] or member_key),
                   len(ids) == len(set(ids)), ids)
        every = [p['id'] for o in off_payload['offices'] for p in o['people']]
        ok('every office person id is a person id',
           all(i.startswith('person:hill:person:') for i in every), every[:3])
        ok('no container or organization id is anywhere in a people list',
           not ({OFFICE_A, OFFICE_B, OFFICE_C, OFFICE_D, OFFICE_ADMIN, ORG_NODE}
                & set(every) | {i for i in every if 'container:' in i or 'organization:' in i}),
           every[:3])
        ok('every lobbyist id is a lobbyist id',
           all(i.startswith('lda:lobbyist:') for i in by_id), sorted(by_id)[:3])
        represented = [person for members in clients.values() for person, _period in members]
        ok('every id on a client roster is a lobbyist id too',
           all(i.startswith('lda:lobbyist:') for i in represented), represented[:3])
        ok('and every client id is a client id',
           all(i.startswith('lda:client:') for i in cli_payload['c']), cli_payload['c'][:3])
        eq('no person is an employee of two registrants (THAT is the ruling: '
           'one employer, full stop)',
           len(by_id), len({p['id'] for o in org_payload['organisations']
                            if o.get('kind') == 'registrant'
                            for p in (o.get('lobbyists') or ())}))
        ok('a person MAY appear under many clients, and that is not a '
           'duplicate -- lda:lobbyist:501 is on three',
           sum(1 for members in clients.values()
               if 'lda:lobbyist:501' in [p for p, _k in members]) == 3)
        for client, members in sorted(clients.items()):
            ids = [person for person, _period in members]
            ok('client %s lists each filer once' % client, len(ids) == len(set(ids)), ids)

        print('[f] counts reconcile with what is listed')
        eq('the offices meta counts the offices it emitted',
           meta['counts']['offices'], len(off_payload['offices']))
        eq('the organisations meta counts the organisations it emitted',
           ometa['counts']['organisations'], len(org_payload['organisations']))
        eq('office people_listed is the distinct people actually printed there',
           meta['counts']['people_listed'],
           len({p['id'] for o in off_payload['offices'] for p in o['people']}))
        eq('organisation people_listed is the employees it printed, and only '
           'those: the client-roster people are a different artifact now',
           ometa['counts']['people_listed'], len(by_id))
        eq('employees_listed counts the employee side alone', ometa['counts']['employees_listed'],
           len(by_id))
        eq('lobbyist_rows_listed counts rows, people_listed counts people',
           ometa['counts']['lobbyist_rows_listed'],
           sum(len(o.get('lobbyists') or ()) for o in org_payload['organisations']))
        eq('meta.bytes is the size of the file it is written into (offices)',
           meta['bytes'], len(off_raw.encode('utf-8')))
        eq('meta.bytes is the size of the file it is written into (organisations)',
           ometa['bytes'], len(org_raw.encode('utf-8')))
        eq('meta.bytes is the size of the file it is written into (clients)',
           cmeta['bytes'], len(cli_raw.encode('utf-8')))

        # ------------------------------------------- (f2) the clients artifact
        # The normalization's whole claim is CONSERVATION: every client the
        # ruling reaches and every tie, with nothing thresholded.  These are the
        # checks that make that a measurement rather than an assertion, and the
        # numbers come from the artifact and the fixture independently.
        print('[f2] the clients artifact conserves the ruling and repeats no name')
        eq('every client the ruling reaches is in the artifact',
           cmeta['counts']['client_organisations'], len(cli_payload['c']))
        eq('and that equals what the ledger itself reached',
           cmeta['counts']['client_organisations'],
           cmeta['counts']['client_organisations_in_ledger'])
        eq('every filer-client tie is in the artifact',
           cmeta['counts']['filer_client_ties'],
           sum(len(row) for row in cli_payload['t']))
        eq('and that equals the ledger\'s own pair count',
           cmeta['counts']['filer_client_ties'],
           cmeta['counts']['contract_filer_client_pairs_in_ledger'])
        eq('the contract filers in the tables are the ledger\'s contract filers',
           (len(cli_payload['f']), cmeta['counts']['contract_filers']),
           (cmeta['counts']['contract_filers_in_ledger'],) * 2)
        eq('nothing was dropped, and the artifact says so in its own counters',
           (cmeta['counts']['client_organisations_dropped'],
            cmeta['counts']['filer_client_ties_dropped']), (0, 0))
        eq('NO NAME IS REPEATED ACROSS ROWS: cn/fn/en hold one row each and the '
           'tie arrays hold integers only',
           (len(cli_payload['cn']), len(cli_payload['fn']), len(cli_payload['en'])),
           (len(cli_payload['c']), len(cli_payload['f']), len(cli_payload['e'])))
        ok('every value in the tie arrays is an integer, never a name',
           all(isinstance(v, int) and not isinstance(v, bool)
               for key in ('t', 'tp') for row in cli_payload[key] for v in row))
        ok('every tie index resolves to a filer',
           all(0 <= v < len(cli_payload['f'])
               for row in cli_payload['t'] for v in row))
        ok('every period code resolves to a label',
           all(0 <= v < len(cmeta['period_labels'])
               for row in cli_payload['tp'] for v in row))
        ok('every employer index resolves to a registrant',
           all(0 <= v < len(cli_payload['e']) for v in cli_payload['fe']))
        eq('name_rows counts the rows a name is written on, once each',
           cmeta['counts']['name_rows'],
           len(cli_payload['c']) + len(cli_payload['f']) + len(cli_payload['e']))
        ok('the key legend ships in the artifact, one entry per table',
           set(cmeta['key_legend']) == {'c', 'cn', 'e', 'en', 'f', 'fn', 'fe', 't', 'tp'},
           sorted(cmeta['key_legend']))
        eq('the filer table is ordered by display name, so a tie row is already '
           'in name order and the page sorts nothing',
           cli_payload['fn'], sorted(cli_payload['fn']))
        eq('each filer\'s employer index points at their real employer',
           {cli_payload['f'][i]: cli_payload['e'][slot]
            for i, slot in enumerate(cli_payload['fe'])},
           {'lda:lobbyist:501': 'lda:registrant:500',
            'lda:lobbyist:502': 'lda:registrant:500'})
        ok('and the employer NAME is in the artifact too, so a client card can '
           'state a filer\'s employer without reading the sibling',
           cli_payload['en'] == ['MERIDIAN GOVERNMENT RELATIONS'], cli_payload['en'])
        ok('the ruling ships verbatim in the clients artifact as well',
           cmeta['employment_ruling'].startswith('For the filer, you do what is '
                                                 'chronologically the most recent.'),
           cmeta['employment_ruling'])
        ok('and so do the three definitions it turned into',
           all(cmeta.get(key) for key in ('employment_rule', 'contract_rule', 'client_rule',
                                          'no_tenure_note')), sorted(cmeta))
        ok('clients_rule states what is here, with the numbers',
           'Every client organisation the ruling reaches is here' in cmeta['clients_rule'],
           cmeta['clients_rule'])

        print('[g] no employment claim')
        for label, part_meta in (('offices', meta), ('organisations', ometa),
                                 ('clients', cmeta)):
            eq('%s: canonical_eligible stays false' % label, part_meta['canonical_eligible'], False)
            eq('%s: the promotion counter stays zero' % label,
               part_meta['canonical_employment_promotions'], 0)
            eq('%s: the section stays review-required' % label,
               part_meta['review_status'], 'REVIEW_REQUIRED')
            ok('%s: every consumed file is bound by sha256' % label,
               all(spec.get('sha256') for spec in part_meta['inputs'].values()),
               sorted(part_meta['inputs']))
        ok('the status note denies an employment finding',
           'not an employment finding' in ometa['lda_status_note'], ometa['lda_status_note'])
        ok('the spine and the House personnel ledger are bound on the offices side',
           {'hill_person_spine', 'house_personnel_ledger_receipt', 'house_member_quarters'}
           <= set(meta['inputs']), sorted(meta['inputs']))
        ok('the sidecar and all FOUR LDA corpus files (the employment ruling\'s '
           'three, plus the lobbyist assertions) are bound on the '
           'organisations side',
           {'registrant_side_records', 'tiered_person_summary', 'lda_lobbyist_assertions',
            'lda_organization_assertions', 'lda_activity_assertions', 'lda_industry_edges'}
           <= set(ometa['inputs']), sorted(ometa['inputs']))
        ok('the generation binding rides on BOTH, because both pass the same gate',
           {'generation_receipt', 'generation_manifest', 'staffer_social_node_scores'}
           <= (set(meta['inputs']) & set(ometa['inputs'])))
        eq('the LDA privacy class is carried, not dropped',
           ometa['lda_privacy_class'], 'restricted-machine-local')

        print('[g2] what the paths on a shippable page may say')
        eq('a repository input is reported root-relative',
           ometa['inputs']['registrant_side_records']['path'],
           'audits/staffer_network/%s/registrant_side_records.ndjson.gz' % SIDECAR)
        eq('and is labelled as living in the repository',
           ometa['inputs']['registrant_side_records']['store'], 'repository')
        eq('a machine-local input keeps only its generation and file name',
           ometa['inputs']['lda_lobbyist_assertions']['path'],
           '%s/lda_lobbyist_assertions.ndjson.gz' % LDA_GENERATION)
        eq('and is labelled as living outside the repository',
           ometa['inputs']['lda_lobbyist_assertions']['store'], 'machine-local')
        eq('the CLIENTS artifact labels the same file the same way -- three parts '
           'share one corpus binding, and the second part to publish it used to '
           'relabel it `repository` because portable_inputs rewrote the binding '
           'in place and then read its own output',
           cmeta['inputs']['lda_industry_edges']['store'], 'machine-local')
        eq('and the two parts agree on its path, byte for byte',
           cmeta['inputs']['lda_lobbyist_assertions'],
           ometa['inputs']['lda_lobbyist_assertions'])
        # The same defect, exercised directly: publishing twice from one binding
        # must give one answer.  It did not, and any real-corpus build whose
        # organisations ladder walked past its first rung published the LDA corpus
        # as a repository file because of it.
        shared = {'lda_industry_edges': {
            'path': os.path.join(lda_root, LDA_GENERATION, 'lda_industry_edges.ndjson.gz'),
            'sha256': 'x' * 64}}
        first_pass = producer_module.portable_inputs(shared, root)
        second_pass = producer_module.portable_inputs(shared, root)
        eq('publishing one binding twice gives the same answer both times',
           second_pass['lda_industry_edges'], first_pass['lda_industry_edges'])
        eq('and neither pass rewrote the binding it was handed',
           shared['lda_industry_edges']['path'],
           os.path.join(lda_root, LDA_GENERATION, 'lda_industry_edges.ndjson.gz'))
        leaks = [(part, k) for part, inputs in (('offices', meta['inputs']),
                                                ('organisations', ometa['inputs']),
                                                ('clients', cmeta['inputs']))
                 for k, v in inputs.items()
                 if ':' in str(v.get('path', '')) or str(v.get('path', '')).startswith('/')
                 or '..' in str(v.get('path', ''))]
        ok('no input path carries a drive letter, an absolute root or a parent hop',
           not leaks, leaks)
        eq('a generation file keeps the bare name meta.generation_root joins to',
           meta['inputs']['staffer_social_node_scores']['path'],
           'staffer_social_node_scores.ndjson.gz')
        eq('the House ledger input is reported root-relative, like every other '
           'in-repository input',
           meta['inputs']['house_member_quarters']['store'], 'repository')
        ok('the tenure note describes THIS office, not the member section field',
           'in THIS office' in meta['tenure_note'], meta['tenure_note'])
        ok('the tenure note is not the network section note',
           'tenure_days_with_member' not in meta['tenure_note'])

        print('[g3] the employment ruling\'s own provenance ships in meta, verbatim')
        eq('employment_ruling is the owner\'s sentence, verbatim',
           ometa['employment_ruling'],
           'For the filer, you do what is chronologically the most recent. If it appears they '
           'are contract/mercenary they are tied to all those organizations as clients.')
        eq('employment_ruling_date is stated', ometa['employment_ruling_date'], '2026-09-07')
        ok('employment_rule explains the tie-break in the ruling\'s own words',
           'most recent filing period' in ometa['employment_rule'], ometa['employment_rule'])
        ok('contract_rule explains the in-house/contract test',
           'clients other than itself' in ometa['contract_rule'], ometa['contract_rule'])
        ok('client_rule is the ruling module\'s own definition, not a note about '
           'what this build happened to fit',
           ometa['client_rule'] == cmeta['client_rule']
           and 'tied to every client organisation' in ometa['client_rule'],
           ometa['client_rule'])
        ok('employment_note is printed for the Sources-and-method fold',
           'former employers' in ometa['employment_note'], ometa['employment_note'])
        eq('organisation_kinds names the one card kind left here',
           ometa['organisation_kinds'], ['registrant'])
        eq('employer_corroboration is the same R0/R1/R2 phrase map employer_line uses',
           ometa['employer_corroboration'], {'R0': CORROB_R0, 'R1': CORROB_R1, 'R2': CORROB_R2})
        eq('clients_mode says the client ties live in a sibling artifact, which '
           'is what the retired ladder\'s mode string used to occupy',
           ometa['clients_mode'], 'separate_artifact')
        eq('and names the file, so a reader is never left to infer it',
           ometa['clients_artifact'], CLIENTS_FILE)
        ok('the organisations artifact states the count it no longer carries, '
           'in the document that stopped carrying it',
           'Client organisations are not cards in this document' in ometa['clients_note'],
           ometa['clients_note'])
        eq('the cross-check numbers agree with the clients artifact itself',
           (ometa['counts']['client_organisations_in_clients_artifact'],
            ometa['counts']['client_ties_in_clients_artifact']),
           (cmeta['counts']['client_organisations'], cmeta['counts']['filer_client_ties']))
        ok('the retired client ladder is gone from the payload, not left at a '
           'default: a threshold that can still be reached is a threshold',
           not any(key in ometa for key in ('client_ladder', 'client_roster_cap',
                                            'client_min_representing_filers')),
           sorted(k for k in ometa if 'client' in k))
        eq('exactly the two contract filers this fixture minted are counted as '
           'contract, and everyone else as in-house',
           (ometa['counts']['contract_filers'], ometa['counts']['in_house_filers']),
           (2, ometa['counts']['lobbyists_total'] - 2))
        eq('contract + in-house adds up to every filer with an employer on file',
           ometa['counts']['contract_filers'] + ometa['counts']['in_house_filers'],
           ometa['counts']['lobbyists_total'])
        eq('contract_registrants counts registrants, not people: exactly one '
           '(MERIDIAN)', ometa['counts']['contract_registrants'], 1)
        eq('filers_with_a_former_employer counts the [d5] mover AND the [d6] '
           'tie-loser -- formers is "every other registrant that ever listed '
           'the person", which a losing tie contender in the SAME period '
           'still is',
           ometa['counts']['filers_with_a_former_employer'], 2)
        eq('former_employer_rows counts one former-employer row per person '
           'here, since neither of the two carries more than one',
           ometa['counts']['former_employer_rows'], 2)

        # ------------------------------------------------------------ (h) caps
        print('[h] the organisation cap (owner ruling 2026-09-07, item 1)')
        out_h = os.path.join(root, 'capped')
        code, text = run(root, out_h, lda_root, extra=['--org-cap', '2'])
        eq('an org-capped build still exits 0', code, 0)
        capped = load(paths_in(out_h)[1])
        eq('the org cap is recorded in meta', capped['meta']['org_cap'], 2)
        c_org = {o['id']: o for o in capped['organisations']}
        eq('lobbyists is capped, lobbyist_count still reports everyone, and '
           'more carries the remainder',
           (len(c_org['lda:registrant:100']['lobbyists']),
            c_org['lda:registrant:100']['lobbyist_count'],
            c_org['lda:registrant:100']['more']), (2, 3, 1))
        eq('an organisation under the cap reports no remainder',
           c_org['lda:registrant:200']['more'], 0)
        c_off = {o['id']: o for o in load(paths_in(out_h)[0])['offices']}
        eq('the organisation cap does not touch offices',
           len(c_off[OFFICE_A]['people']), 2)

        print('[h2] the office safety cap is a separate, current-people-only dial')
        out_h2 = os.path.join(root, 'office_capped')
        code, text = run(root, out_h2, lda_root, extra=['--office-cap', '1'])
        eq('an office-capped build still exits 0', code, 0)
        office_capped = load(paths_in(out_h2)[0])
        eq('the office safety cap is recorded in meta',
           office_capped['meta']['office_current_safety_cap'], 1)
        oc_off = {o['id']: o for o in office_capped['offices']}
        eq('current people is capped by the safety valve, current_count still '
           'reports everyone current, and more carries the remainder',
           (len(oc_off[OFFICE_A]['people']), oc_off[OFFICE_A]['current_count'],
            oc_off[OFFICE_A]['more']), (1, 2, 1))
        eq('an office under the safety cap reports no remainder',
           oc_off[OFFICE_C]['more'], 0)
        eq('and the counters say how often the cap bound and how many current '
           'people it left unnamed',
           (office_capped['meta']['counts']['offices_over_current_cap'],
            office_capped['meta']['counts']['current_people_not_named']), (1, 1))
        o_org = {o['id']: o for o in load(paths_in(out_h2)[1])['organisations']}
        eq('the office safety cap does not touch organisations',
           len(o_org['lda:registrant:100']['lobbyists']), 3)

        print('[h3] --part writes one artifact and leaves the others alone')
        out_p1 = os.path.join(root, 'part_offices')
        code, text = run(root, out_p1, lda_root, extra=['--part', 'offices'])
        eq('--part offices exits 0', code, 0)
        ok('--part offices writes the offices artifact', os.path.exists(paths_in(out_p1)[0]))
        ok('--part offices writes NOTHING else',
           not any(os.path.exists(p) for p in paths_in(out_p1)[1:]))
        out_p2 = os.path.join(root, 'part_orgs')
        code, text = run(root, out_p2, lda_root, extra=['--part', 'organisations'])
        eq('--part organisations exits 0', code, 0)
        ok('--part organisations writes the organisations artifact',
           os.path.exists(paths_in(out_p2)[1]))
        ok('--part organisations writes NOTHING else',
           not os.path.exists(paths_in(out_p2)[0])
           and not os.path.exists(paths_in(out_p2)[2]))
        out_p3 = os.path.join(root, 'part_clients')
        code, text = run(root, out_p3, lda_root, extra=['--part', 'clients'])
        eq('--part clients exits 0', code, 0)
        ok('--part clients writes the clients artifact', os.path.exists(paths_in(out_p3)[2]))
        ok('--part clients writes NOTHING else',
           not any(os.path.exists(p) for p in paths_in(out_p3)[:2]))
        for index, path in ((0, off_path), (1, org_path), (2, cli_path)):
            out_single = (out_p1, out_p2, out_p3)[index]
            ok('a single-part build is byte-identical to that part of a both build (%d)'
               % index,
               open(paths_in(out_single)[index], 'rb').read() == open(path, 'rb').read())

        # ------------------------------------------------- (i) negative controls
        print('[i] negative controls')
        root_i = os.path.join(workspace, 'poison')
        lda_i = make_tree(root_i, poison_person=OFFICE_ADMIN)
        out_i = os.path.join(root_i, 'out')
        code, text = run(root_i, out_i, lda_i)
        eq('a container id arriving as a person refuses', code, 2)
        ok('nothing is written', not any(os.path.exists(p) for p in paths_in(out_i)))
        ok('the refusal names the offender',
           'non-person identifier' in text, text.strip()[-300:])

        root_j = os.path.join(workspace, 'badlobbyist')
        lda_j = make_tree(root_j, bad_person_id='lda:registrant:100')
        out_j = os.path.join(root_j, 'out')
        code, text = run(root_j, out_j, lda_j)
        eq('a registrant id arriving as a lobbyist refuses', code, 2)
        ok('nothing is written for the bad lobbyist id',
           not any(os.path.exists(p) for p in paths_in(out_j)))

        root_k = os.path.join(workspace, 'unknowntier')
        lda_k = make_tree(root_k, unknown_tier=True)
        out_k = os.path.join(root_k, 'out')
        code, text = run(root_k, out_k, lda_k)
        eq('an unknown corroboration tier refuses rather than guessing a label', code, 2)
        ok('the refusal says it will not guess', 'refuses to guess' in text, text.strip()[-300:])

        root_l = os.path.join(workspace, 'sidecarfail')
        lda_l = make_tree(root_l, sidecar_status='FAIL')
        code, text = run(root_l, os.path.join(root_l, 'out'), lda_l)
        eq('a sidecar receipt that is not PASS refuses', code, 2)
        root_m = os.path.join(workspace, 'ldafail')
        lda_m = make_tree(root_m, lda_status='FAIL')
        code, text = run(root_m, os.path.join(root_m, 'out'), lda_m)
        eq('an LDA corpus receipt that is not PASS refuses', code, 2)

        root_honfail = os.path.join(workspace, 'honledgerfail')
        lda_honfail = make_tree(root_honfail, hon_ledger_status='FAIL')
        code, text = run(root_honfail, os.path.join(root_honfail, 'out'), lda_honfail)
        eq('a House personnel ledger receipt that is not PASS refuses', code, 2)

        # ------------------------------------------- (i2) duplication tripwire
        # fec/staffer_network/lda_status_labels.py did not exist when this producer
        # was written, so build_staffer_groups_section carries its own copy of the
        # owner's status vocabulary and says so in a DUPLICATION NOTE. The module
        # has since appeared. Rather than couple this producer to it while it is
        # still in flight, the two copies are held byte-identical here: the moment
        # either side edits a string this fails, and whoever consolidates them
        # deletes this block along with the local table.
        print('[i2] the duplicated label table cannot drift')
        sys.path.insert(0, os.path.dirname(FEC))
        try:
            from fec.staffer_network import lda_status_labels as shared
        except ImportError:
            shared = None
        if shared is None:
            ok('the shared label module is absent; the local table is the only copy', True)
        else:
            rendered = {tier: shared.status_label(shared.status_code(tier))
                        for tier in ('L0', 'L1', 'L2', 'L3', 'L4', 'L5')}
            eq('the shared module renders the same status line for every tier',
               rendered,
               {'L0': NO_RECORD, 'L1': NO_RECORD, 'L2': LIKELY, 'L3': LIKELY,
                'L4': CONFIRMED, 'L5': CONFIRMED})
            eq('the shared module renders the same employer-on-file line',
               shared.employer_line('R1', 'ACME ADVOCACY'), EMPLOYER_ON % 'ACME ADVOCACY')
            eq('R0 renders the same employer-on-file line too (ruled 2026-09-07)',
               shared.employer_line('R0', 'ACME ADVOCACY'), EMPLOYER_ON % 'ACME ADVOCACY')
            eq('the shared module renders the same no-name fallback, never the retired string',
               shared.employer_line('R0'), EMPLOYER_UNRESOLVED)
            eq('the shared module renders the same R2 corroboration phrase',
               shared.employer_corroboration('R2'), CORROB_R2)
            eq('the shared module renders the same R1 corroboration phrase',
               shared.employer_corroboration('R1'), CORROB_R1)
            eq('the shared module renders the same R0 corroboration phrase',
               shared.employer_corroboration('R0'), CORROB_R0)

        # ------------------------------------------------------- (j) byte caps
        print('[j] byte caps, one per artifact')
        out_n = os.path.join(root, 'toobig')
        code, text = run(root, out_n, lda_root, extra=['--max-bytes', '900'])
        eq('a payload over --max-bytes exits 3', code, 3)
        ok('nothing is written over the cap',
           not any(os.path.exists(p) for p in paths_in(out_n)))
        ok('the refusal names the byte count and the cap',
           'exceeds --max-bytes 900' in text, text.strip()[-600:])
        ok('the refusal names both artifacts and their largest groups',
           'offices:' in text and 'organisations:' in text, text.strip()[-600:])

        print('[j2] the singleton fallback: name-only lines become a count')
        root_big = os.path.join(workspace, 'bigorgs')
        lda_big = make_tree(root_big, singletons=60)
        out_meas = os.path.join(root_big, 'measure')
        code, text = run(root_big, out_meas, lda_big)
        eq('the calibration build succeeds', code, 0)
        off_bytes = os.path.getsize(paths_in(out_meas)[0])
        org_lines_bytes = os.path.getsize(paths_in(out_meas)[1])
        eq('the calibration build lists the singleton lines',
           load(paths_in(out_meas)[1])['meta']['singleton_mode'], 'name_only_lines')

        # NOTE ON THE MARGINS below.  meta.max_bytes is written INTO the document it
        # bounds, so passing a different --max-bytes changes that document's own size
        # by a digit or two.  A cap of exactly `size - 1` is therefore not a reliable
        # way to push a build over its own limit -- it was tried and did not fire.
        # Every calibrated cap here keeps a margin far larger than that wobble and
        # far smaller than the thing being measured (61 long-named singleton lines).
        out_fb = os.path.join(root_big, 'fallback')
        cap_fb = org_lines_bytes - 2000
        code, text = run(root_big, out_fb, lda_big,
                         extra=['--part', 'organisations', '--max-bytes', str(cap_fb)])
        eq('a cap under the name-only build still exits 0, because the fallback '
           'fires instead of refusing', code, 0)
        fb = load(paths_in(out_fb)[1])
        eq('the mode flipped to count_only', fb['meta']['singleton_mode'], 'count_only')
        eq('and the count survives the lines being dropped -- 60 bulk singletons '
           'plus this fixture\'s own two (SOLO CONSULTING and ORPHANED FILINGS, '
           'see [d]/[d2])',
           fb['meta']['singleton_registrants'], 62)
        eq('the counts still reconcile against every registrant on file',
           (fb['meta']['counts']['organisations_listed_in_full']
            + fb['meta']['counts']['singleton_registrants']),
           fb['meta']['counts']['registrants_total'])
        ok('no name-only REGISTRANT line is left in the array',
           all('lobbyists' in o for o in fb['organisations'] if o.get('kind') == 'registrant'))
        ok('every remaining REGISTRANT has two or more filers',
           all(o['lobbyist_count'] >= 2 for o in fb['organisations']
               if o.get('kind') == 'registrant'))
        fb_bytes = os.path.getsize(paths_in(out_fb)[1])
        ok('the fallback build really is smaller, and inside the cap that forced it',
           fb_bytes < org_lines_bytes and fb_bytes <= cap_fb,
           (fb_bytes, cap_fb, org_lines_bytes))

        # ALL-OR-NOTHING.  Calibrated from the fixture's own measured sizes rather
        # than a magic number: a cap that ONE half clears and the other cannot,
        # whichever way round the two happen to fall.
        print('[j3] --part both is all-or-nothing on the cap')
        order = sorted([(off_bytes, 'offices'), (fb_bytes, 'organisations')])
        cap_aon = order[1][0] - 500
        ok('the fixture keeps the all-or-nothing case reachable (one half must fit '
           'under a cap the other cannot)', order[0][0] <= cap_aon,
           (order, cap_aon, org_lines_bytes))
        out_aon = os.path.join(root_big, 'allornothing')
        code, text = run(root_big, out_aon, lda_big, extra=['--max-bytes', str(cap_aon)])
        eq('--part both refuses when only ONE part is over the cap', code, 3)
        ok('and writes NO file, not even the parts that fit',
           not any(os.path.exists(p) for p in paths_in(out_aon)),
           [p for p in paths_in(out_aon) if os.path.exists(p)])
        ok('the refusal names the half that was over',
           ('%s: ' % order[1][1]) in text, text.strip()[-800:])
        ok('the refusal says nothing was written', 'nothing written' in text.lower(),
           text.strip()[-800:])

        print('[k] determinism and clean skip')
        first = os.path.join(root, 'd1')
        second = os.path.join(root, 'd2')
        code_1, _ = run(root, first, lda_root)
        code_2, _ = run(root, second, lda_root)
        ok('both determinism runs succeed', code_1 == 0 and code_2 == 0)
        for index, label in ((0, 'offices'), (1, 'organisations'), (2, 'clients')):
            ok('two %s runs are byte-identical' % label,
               open(paths_in(first)[index], 'rb').read()
               == open(paths_in(second)[index], 'rb').read())
            text_d = open(paths_in(first)[index], encoding='utf-8').read()
            ok('the %s file is exactly the sorted, compact serialization' % label,
               text_d == json.dumps(json.loads(text_d), sort_keys=True,
                                    separators=(',', ':'), ensure_ascii=False))
        root_skip = os.path.join(workspace, 'empty')
        os.makedirs(root_skip, exist_ok=True)
        out_skip = os.path.join(root_skip, 'out')
        code, text = run(root_skip, out_skip, lda_root)
        eq('an absent generation exits 0, not 2', code, 0)
        ok('the skip says so', 'SKIPPED' in text, text.strip()[-200:])
        ok('the skip writes nothing', not any(os.path.exists(p) for p in paths_in(out_skip)))

        # ----------------------------------------------- (l) v6 generation shape
        # The staffer social network moved to a PERSONS-ONLY operator (D-022): the
        # node scores carry no container rows at all and the containers, the
        # organisations and the policy areas are rostered in the generation's own
        # staffer_social_groups.ndjson.gz.  The pinned generation is still v5
        # today, so this producer has to read BOTH and say which it read.
        print('[l] the v6 persons-only generation shape')
        root_v6 = os.path.join(workspace, 'v6')
        lda_v6 = make_tree(root_v6, shape='v6')
        out_v6 = os.path.join(root_v6, 'out')
        code, text = run(root_v6, out_v6, lda_v6)
        eq('a persons-only v6 generation builds', code, 0)
        v6_off = load(paths_in(out_v6)[0])
        v6_meta = v6_off['meta']
        eq('the container source is the grouping roster, and meta says so',
           v6_meta['container_source'], 'staffer_social_groups')
        ok('the grouping roster is bound by sha256 like every other consumed byte',
           bool(v6_meta['inputs'].get('staffer_social_groups', {}).get('sha256')),
           sorted(v6_meta['inputs']))
        ok('and it is bound against the generation receipt, not merely opened',
           'generation_receipt.files'
           in (v6_meta['inputs'].get('staffer_social_groups', {}).get('bound_against') or []),
           v6_meta['inputs'].get('staffer_social_groups'))
        v6_offices = {o['id']: o for o in v6_off['offices']}
        eq('the v6 shape emits the same offices as the v5 shape',
           sorted(v6_offices), sorted(offices))
        eq('and names them identically, from the row own name field',
           {i: o['name'] for i, o in v6_offices.items()},
           {i: o['name'] for i, o in offices.items()})
        ok('an admin container is not an office on the v6 shape either',
           OFFICE_ADMIN not in v6_offices)
        ok('a v6 organization group is never an office', ORG_NODE not in v6_offices)
        eq('the office people are identical across the two shapes',
           {i: [p['display_name'] for p in o['people']] for i, o in v6_offices.items()},
           {i: [p['display_name'] for p in o['people']] for i, o in offices.items()})
        eq('the kind histogram is drawn from the grouping roster on v6',
           sorted(v6_meta['counts']['container_source_kinds']),
           ['container', 'organization', 'policy_area'])
        eq('and from the node kinds on v5',
           sorted(meta['counts']['container_source_kinds']),
           ['container', 'organization', 'person', 'policy_area'])
        v6_org = load(paths_in(out_v6)[1])
        eq('the organisations half does not depend on the generation shape at all',
           [o['id'] for o in v6_org['organisations']],
           [o['id'] for o in org_payload['organisations']])

        # --------------------------------------- (m) the real generation, if here
        # The 5,000,000-byte pin is the whole reason the artifact was split, so it
        # is checked against the generation that forced the split rather than only
        # against a fixture.  SKIP-CLEAN where the real generation, the sidecar or
        # the machine-local LDA corpus is not on this box.
        print('[m] both artifacts fit the pin on the REAL generation')
        try:
            sys.path.insert(0, os.path.dirname(FEC))
            from fec.surfaces import build_staffer_groups_section as producer
        except ImportError as exc:                       # pragma: no cover - env dependent
            producer = None
            print('  SKIP   the producer could not be imported here (%s)' % exc)
        real_root = os.path.dirname(FEC)
        needed = producer and [
            os.path.join(real_root, 'audits', 'staffer_network', producer.GENERATION),
            os.path.join(real_root, 'audits', 'staffer_network', producer.SIDECAR_GENERATION),
            os.path.join(real_root, 'audits', 'staffer_network',
                         producer.HON_LEDGER_GENERATION),
            os.path.join(producer.LDA_DATA_ROOT, producer.LDA_GENERATION),
        ]
        if producer and all(os.path.isdir(p) for p in needed):
            out_real = os.path.join(workspace, 'real')
            env = dict(os.environ)
            env.pop('MONEYBALL_ROOT', None)
            env['PYTHONIOENCODING'] = 'utf-8'
            result = subprocess.run(
                [sys.executable, PRODUCER, '--out-dir', out_real, '--no-glassbox'],
                capture_output=True, text=True, encoding='utf-8', errors='replace', env=env)
            output = (result.stdout or '') + (result.stderr or '')
            ok('the real generation builds', result.returncode == 0, output.strip()[-500:])
            if result.returncode == 0:
                real_off, real_org, real_cli = (os.path.getsize(p) for p in paths_in(out_real))
                ok('the offices artifact fits the 5,000,000-byte pin (%d bytes)' % real_off,
                   real_off <= producer.MAX_BYTES, real_off)
                ok('the organisations artifact fits the same pin (%d bytes)' % real_org,
                   real_org <= producer.MAX_BYTES, real_org)
                ok('the clients artifact fits the same pin (%d bytes)' % real_cli,
                   real_cli <= producer.MAX_BYTES, real_cli)
                ok('and the three together are what one document could not be (%d bytes)'
                   % (real_off + real_org + real_cli),
                   real_off + real_org + real_cli > producer.MAX_BYTES,
                   real_off + real_org + real_cli)
                # THE CLAIM THE NORMALIZATION MAKES, measured against the corpus
                # that broke the card form: every client organisation the ruling
                # reaches and every filer-client tie, inside the pin.  A build
                # that silently thresholded would still fit the pin, so the size
                # check alone proves nothing; these two are what does.
                real_clients = load(paths_in(out_real)[2])
                rc = real_clients['meta']['counts']
                eq('every client organisation the ruling reaches is in it',
                   rc['client_organisations'], rc['client_organisations_in_ledger'])
                eq('every filer-client tie is in it',
                   rc['filer_client_ties'], rc['contract_filer_client_pairs_in_ledger'])
                eq('the tie arrays hold exactly that many entries',
                   sum(len(row) for row in real_clients['t']), rc['filer_client_ties'])
                eq('nothing was dropped', (rc['client_organisations_dropped'],
                                           rc['filer_client_ties_dropped']), (0, 0))
                ok('every tie index in the REAL artifact resolves to a filer',
                   all(0 <= v < len(real_clients['f'])
                       for row in real_clients['t'] for v in row))
                real_org_meta = load(paths_in(out_real)[1])['meta']
                ok('the organisations artifact carries no client card on the real '
               'generation either',
                   not any('represented_by' in o
                           for o in load(paths_in(out_real)[1])['organisations']))
                eq('and points at the sibling that does',
                   real_org_meta['clients_artifact'], CLIENTS_FILE)
        else:
            missing = [p for p in (needed or []) if not os.path.isdir(p)]
            print('  SKIP   real-generation size check :: not on this box (%s)'
                  % (', '.join(os.path.basename(p) for p in missing) or 'producer import'))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    print('\n%d passed, %d failed' % (P, F))
    return 1 if F else 0


if __name__ == '__main__':
    sys.exit(main())
