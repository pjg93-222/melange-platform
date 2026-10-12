# -*- coding: utf-8 -*-
"""Contract suite for fec/surfaces/make_staffer_network_html.py.

Everything runs against a synthetic section artifact in a temp tree, through the shipped
builder's own env dials (STAFFER_SECTION_DATA_PATH / STAFFER_HTML_OUT), so the real
template is rendered rather than a stand-in.

What is asserted, and why each one is a gate rather than documentation:

  * The template contract make_combined.extract() depends on -- exactly one <style>, then
    body, then exactly one bare <script>.  A second <script> anywhere would make the merge
    silently drop half the app, which is precisely the failure mode extract_multi() had to
    be written for on the industry surface.
  * Every element id is prefixed `stf-`.  That prefix is the whole reason the staffer panel
    needs no renaming pass in make_combined.py; the moment one id loses it, an id can be
    shadowed by one of the other three surfaces in the merged document.
  * No `src=` on any element, and no off-document reference other than the optional LOCAL
    live-view probe and person-detail links. Moneyball.html's contract is one self-contained file; the 8841
    external <script src> that used to be injected into it is exactly what this surface
    replaced (see fec/surfaces/staffer_live_integration.py).
  * The zero-overlap label ships verbatim.  It is the owner's ruling made visible: pairs
    that shared an office at non-overlapping times are REJECTED, never drawn.
  * No advisory / persuasion vocabulary in the TEMPLATE.  Owner ruling 2026-09-07,
    item 3: the payload is data, not template, and an identifier value -- a
    registrant's own legal name, an office's own printed string -- is exempt from
    this grep the same way it is exempt everywhere else on this page.  The scan
    strips the SDATA/GDATA/ODATA data islands (always exactly one line each; json.dumps
    escapes any real newline inside a string) before counting; meta.statement's
    VERBATIM copy of the generation receipt's own disclaimer ("...no influence
    claim") lives inside one of those islands and is never scanned as chrome, so
    its one shipped copy is still checked separately, by exact count, in ALLOWED.
    The network producer applies the analogous carve-out at its own layer for the
    same reason (build_staffer_network_section.check_vocabulary): a copied receipt
    line is never edited.

Plain-assert style, no dependencies:  python fec/tests/surfaces/test_make_staffer_network_html.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FEC = os.path.dirname(os.path.dirname(HERE))
BUILDER = os.path.join(FEC, 'surfaces', 'make_staffer_network_html.py')

# Mirrors the shipped v4 receipt's disclaimer shape, including the trailing negation that
# carries the one permitted occurrence of the barred word.
STATEMENT = ('Observed payroll co-tenure only. People who held the same office at '
             'non-overlapping times carry NO edge here. Not identity findings, not '
             'primary weights, no influence claim.')
LANGUAGE_NOTE = ('Shared staff means people attached to both offices. It is not a tie '
                 'between the two members and carries no weight.')
GENERATION = 'staffer_social_network_test_v9_primary'
ZERO_OVERLAP_LABEL = 'Same-office pairs rejected for zero overlap'
BANNED = ('influence', 'persuad', 'recommend', 'should')
# exact string -> exactly how many times it may appear, and nowhere else
ALLOWED = ((STATEMENT, 1),)   # nav label is now 'Main dashboard' (2026-09-05); only the receipt statement is carved out

P = F = 0


def ok(name, cond, detail=''):
    global P, F
    P, F = (P + 1, F) if cond else (P, F + 1)
    print(('  OK   ' if cond else 'FAIL  ') + name + (('  :: ' + str(detail)) if (detail and not cond) else ''))


def eq(name, got, want):
    ok(name, got == want, 'got %r want %r' % (got, want))


def person(node, tier, degree, rank, title, status, tenure, others=0,
           office_name='Senior Staff Office', office_size=6):
    # office_name/office_size (ruling 1, RELABEL, 2026-09-07): default to a
    # populated office so the common top_staff row is covered; the fixture below
    # passes None for one row too, so the "no container on record" fallback the
    # template's JS falls back to is covered as well.
    return {'node_id': node, 'identity_tier': tier, 'co_tenure_degree': degree,
            'pagerank_085': rank, 'eigen_component_local': 1.1e-9,
            'office_diversity': 0.5, 'other_member_count': others,
            'title_last': title, 'status': status, 'tenure_days_with_member': tenure,
            'co_tenure_office_name': office_name, 'co_tenure_office_size': office_size}


def fixture():
    members = {}
    spec = [('A000001', 'Ada Bridger', 'D', 'Senate', 'ME', 12, 4, 3),
            ('B000002', 'Bo Carver', 'R', 'House', 'TX', 7, 2, 1),
            ('C000003', 'Cy Dunne', 'I', 'House', 'VT', 0, 0, 0)]
    for bio, name, party, chamber, state, total, current, shared in spec:
        members[bio] = {
            'bioguide': bio, 'display_name': name, 'party': party, 'chamber': chamber,
            'state': state,
            'staff_total': total, 'staff_current': current, 'staff_departed': total - current,
            'staff_total_node_scores': total,
            'shared_staff': shared, 'shared_staff_with_unrostered_members': 1,
            'shared_with': ([{'bioguide': 'B000002', 'people': 2}] if bio == 'A000001'
                            else ([{'bioguide': 'A000001', 'people': 2}] if bio == 'B000002' else [])),
            'shared_with_partner_count': 1 if total else 0,
            'top_staff': ([person('person:hill:person:aaa', 'S1', 9, 4.6e-5, 'SENIOR COUNSEL', 'current', 2374, 1),
                           person('person:hill:person:bbb', 'S2', 4, 1.9e-5, 'LEGISLATIVE AIDE', 'departed', None,
                                  office_name=None, office_size=None)]
                          if total else []),
            'titles': ([{'title_last': 'SENIOR COUNSEL', 'people': 1}] if total else []),
            'moves_in': 3 if total else 0, 'moves_out': 2 if total else 0,
            'moves_in_office_pairs': 1 if total else 0, 'moves_out_office_pairs': 1 if total else 0,
            'top_sources': ([{'container_id': 'container:office:senate:SPECIAL COMMITTEE ON AGING',
                              'container_kind': 'committee', 'member_bioguide': None, 'people': 3}]
                            if total else []),
            'top_destinations': ([{'container_id': 'container:office:house:HON:B000002',
                                   'container_kind': 'member_office', 'member_bioguide': 'B000002',
                                   'people': 2}] if total else []),
            'revolving_degree_sum': 0,
            'component_ids': [0], 'largest_component_share': 1.0 if total else None,
        }
    return {
        'meta': {
            'schema_id': 'staffer-network-section-v1',
            'producer': 'fec/surfaces/build_staffer_network_section.py',
            'producer_version': 'staffer-network-section-v1',
            'generation': GENERATION,
            'generation_root': 'audits/staffer_network/' + GENERATION,
            'inputs': {'staffer_social_node_scores': {'path': 'staffer_social_node_scores.ndjson.gz',
                                                      'sha256': 'ab' * 32, 'bytes': 11,
                                                      'bound_against': ['generation_receipt.files']}},
            'verification': {'status': 'PASS', 'failures': 0,
                             'path': 'audits/staffer_network/x/verification.json', 'sha256': 'cd' * 32},
            'roster_source': 'moneyball_core.congestion()', 'roster_size': len(members),
            'exploratory': True, 'review_status': 'REVIEW_REQUIRED',
            'canonical_eligible': False, 'canonical_employment_promotions': 0,
            'statement': STATEMENT, 'language_note': LANGUAGE_NOTE,
            'tenure_note': 'tenure_days_with_member counts days between core_start and core_end_exclusive.',
            'name_policy': 'No person names.',
            'zero_overlap_pairs_rejected': 4242,
            'built_at': '2026-09-04T00:00:00Z', 'bytes': 0,
        },
        'global': {
            'node_universe': {'person': 6, 'container': 4, 'organization': 1, 'policy_area': 1,
                              'total': 12, 'container_by_kind': {'member_office': 3, 'committee': 1}},
            'edge_counts': {'co_tenure_guaranteed': 17, 'co_tenure_zero_overlap_REJECTED_never_emitted': 4242},
            'zero_overlap_pairs_rejected': 4242,
            'zero_overlap_rule': 'no person-person edge without actual temporal overlap',
            'rule_version': 'staffer-social-network-v3',
            'generated_at': '2026-09-03T00:00:00Z',
            'person_components': {'distinct_component_ids': 2, 'largest_component_size': 11},
            'top_bridge_people': [dict(person('person:hill:person:aaa', 'S1', 9, 4.6e-5, None, None, None, 1),
                                       member_count=2, unrostered_member_count=1)],
            'members_with_any_staff': 2, 'members_with_shared_staff': 2, 'roster_size': len(members),
            'person_nodes': 6, 'person_nodes_with_member_attachment': 5,
            'person_nodes_with_roster_member_attachment': 4,
            'staff_total_sum_over_members': 19, 'staff_total_node_scores_sum_over_members': 19,
            'members_where_trajectory_and_node_score_populations_differ': 0,
            'shared_staff_note': "shared_staff counts this member's people who are attached to at "
                                 'least one other member office anywhere in the payroll record.',
        },
        'members': members,
    }


GROUPS_OFFICE = 'container:office:senate:JUDICIARY'
GROUPS_ORG = 'lda:registrant:100'
GROUPS_STATUS = 'Lobbyist · former Hill staff, confirmed'
GROUPS_EMPLOYER = 'Employer on file: ACME ADVOCACY'
# Owner ruling 2026-09-07, item 3: a registrant's own legal name can carry a
# banned token and still ship, because it is payload, not template.  This one
# proves the exemption is not vacuous: the page pin below checks that this
# exact word is what the chrome-only scan is NOT tripped by.
GROUPS_ORG_BANNED_WORD = 'Influence Partners LLC'
# Rule 6 (owner ruling 2026-09-07, item 3): a single-filer registrant is a
# name-only line -- no `lobbyists` key at all.  This id doubles as both the
# single-filer fixture row AND the banned-word pin above: one row proves both
# at once.  Id unchanged from the earlier combined fixture.
GROUPS_ORG_SINGLE_FILER = 'lda:registrant:300'
# A SEPARATE row whose `lobbyists` key is present but EMPTY: "nobody on file",
# a different fact the template must not confuse with the single-filer row.
GROUPS_ORG_EMPTY = 'lda:registrant:500'
GROUPS_ORG_EMPTY_NAME = 'QUIET REGISTRANT LLC'
CURRENT_DAY_NOTE_TEST = ('An office is shown only when at least one person on '
                        'its payroll record is current today.')
# The Groups note describes THIS page's per-office days; the network section's
# tenure_note (in fixture() above) describes a per-member field of the same
# name, so this one has to say something the reader could not mistake for that.
TENURE_NOTE_TEST = ('Days on the record counts a person on THIS office only; it is not the '
                    'network section per-member tenure_note of the same name.')
SINGLETON_RULE_TEST = (
    'A registrant with two or more lobbyists on file is listed in full, capped with the '
    'remainder counted in `more`. A registrant with exactly one lobbyist on file is a '
    'name-only line, with no lobbyists array, so the registrant stays in the directory '
    'and the single filer is not named.')
# The client half of the employment ruling (owner ruling 2026-09-07), which as of
# the client normalization is its OWN artifact and its own data island: parallel
# tables plus integer ties, so that all 41,203 client organisations and all
# 132,738 filer-client ties fit the 5,000,000-byte pin.  Written as cards inside
# the organisations document they measured 21,012,744 bytes and the threshold
# that fit shipped 203 of them.  [h5] below renders a client card out of these
# tables by index and checks that no name was duplicated to do it.
GROUPS_CLIENT = 'lda:client:44'
GROUPS_CLIENT_NAME = 'CORAL BAY UTILITIES'
GROUPS_CLIENT_REPRESENTER = 'lda:lobbyist:4'
GROUPS_CLIENT_REPRESENTER_NAME = 'James MacLeod'
GROUPS_CLIENT_2 = 'lda:client:45'
GROUPS_CLIENT_2_NAME = 'HARBOR LIGHT MUTUAL'
GROUPS_CLIENT_EMPLOYER = 'lda:registrant:900'
GROUPS_CLIENT_EMPLOYER_NAME = 'MERIDIAN GOVERNMENT RELATIONS'
# A string that appears in NO fixture on this page: the search-miss control has to
# be something the haystacks genuinely do not contain, or "no hit" proves nothing.
GROUPS_CLIENT_ABSENT = 'zzqx-no-such-organisation'


def offices_fixture():
    """The offices payload the _01 template renders, shaped exactly as
    fec/surfaces/build_staffer_groups_section.py --part offices writes it.

    Owner ruling 2026-09-07: the single combined Groups document measured
    8.29 MB against the real generation and could not be brought under the
    5 MB producer pin by any cap, so offices and organisations became two
    independent artifacts, each with its own meta, its own generation
    binding and its own counts.  This meta carries no organisations-side key
    (org_cap, lda_status_note, singleton_*); those live only in
    organisations_fixture() below.
    """
    return {
        'meta': {
            'schema_id': 'staffer-offices-section-v2',
            'producer': 'fec/surfaces/build_staffer_groups_section.py',
            'producer_version': 'staffer-groups-section-v2',
            'generation': GENERATION,
            'part': 'offices',
            'siblings': ['staffer_organisations_section.json', 'staffer_clients_section.json'],
            'inputs': {'staffer_trajectories': {
                'path': 'audits/staffer_network/x/staffer_trajectories.ndjson.gz',
                'sha256': 'fa' * 32, 'bytes': 17, 'bound_against': ['receipt.files']}},
            'office_kinds': ['member_office', 'committee', 'leadership'],
            'current_day_note': CURRENT_DAY_NOTE_TEST,
            'tenure_note': TENURE_NOTE_TEST,
            'office_current_safety_cap': 60,
            'exploratory': True, 'review_status': 'REVIEW_REQUIRED',
            'canonical_eligible': False, 'canonical_employment_promotions': 0,
            'counts': {'offices': 2, 'people_listed': 2, 'office_people_total': 3,
                       'offices_unnamed': 0, 'offices_hon_ledger_resolved': 0},
            'built_at': '2026-09-07T00:00:00Z', 'bytes': 0,
        },
        'offices': [
            # Owner ruling 2026-09-07, item 1: current-day only.  Owen Pike (who
            # was in this office but has since departed) is no longer listed by
            # name; earlier_count is his replacement.
            {'id': GROUPS_OFFICE, 'name': 'JUDICIARY', 'kind': 'committee', 'chamber': 'senate',
             'member_bioguide': None, 'people_count': 9, 'current_count': 1,
             'earlier_count': 8, 'more': 0,
             'people': [{'id': 'person:hill:person:aaa', 'display_name': 'Nora Vance',
                         'title_last': 'ASSOCIATE COUNSEL', 'current': True, 'tenure_days': 365}]},
            {'id': 'container:office:house:HON:B000002', 'name': 'Quill Office',
             'kind': 'member_office', 'chamber': 'house', 'member_bioguide': 'B000002',
             'people_count': 1, 'current_count': 1, 'earlier_count': 0, 'more': 0,
             'people': [{'id': 'person:hill:person:ccc', 'display_name': 'Pia Marsh',
                         'title_last': 'SCHEDULER', 'current': True, 'tenure_days': 90}]},
        ],
    }


def organisations_fixture():
    """The organisations payload the _01 template renders, shaped exactly as
    fec/surfaces/build_staffer_groups_section.py --part organisations writes it
    under the 2026-09-07 employment ruling (v3): every row carries `kind`, the
    employer line and in-house/contract engagement moved from the per-person row
    to the CARD, and a person's row is left with what actually varies --
    registrant_tier, employer_as_of and, for a contract filer, client_count.

    Four rows, deliberately distinct:
      * ACME ADVOCACY (kind registrant) -- a MULTI-lobbyist registrant, capped
        with `more`.  Its one listed lobbyist carries registrant_tier and
        employer_as_of, NOT employer_line -- that is on the card now.
      * GROUPS_CLIENT (kind client) -- the OTHER v3 card shape: `represented_by`
        instead of `lobbyists`, the contract filers named on filings made for
        it.  Paul is separately adding a "Represented by" roster to the
        template for cards of this kind; see [h5].
      * GROUPS_ORG_EMPTY (kind registrant) -- a `lobbyists` key that IS present
        but EMPTY, which says "nobody on file" (rule 6, second clause).  The
        template's own orgBody() still branches on this shape explicitly, so
        it stays worth rendering even though the current producer's
        employees_by_registrant() never emits a zero-employee registrant.
      * GROUPS_ORG_SINGLE_FILER (kind registrant) -- a SINGLE-FILER:
        `{id, kind, name, lobbyist_count, engagement}` with NO `lobbyists` key
        at all, so "one person on file, not named here" cannot be confused
        with "nobody on file" (rule 6, first clause).  A name-only line still
        states in-house/contract -- that is a fact about the organisation, not
        about its one unnamed filer.  Doubles as the banned-word pin (item 3):
        a registrant's own legal name is payload data and is exempt from the
        chrome-only advisory-language scan even in this bare shape.
    """
    return {
        'meta': {
            'schema_id': 'staffer-organisations-section-v4',
            'producer': 'fec/surfaces/build_staffer_groups_section.py',
            'producer_version': 'staffer-groups-section-v4',
            'generation': GENERATION,
            'part': 'organisations',
            'siblings': ['staffer_offices_section.json', 'staffer_clients_section.json'],
            'sidecar_generation': 'tiered_corroboration_sidecar_test_primary',
            'lda_generation': 'generation-test',
            'inputs': {'registrant_side_records': {
                'path': 'audits/staffer_network/x/registrant_side_records.ndjson.gz',
                'sha256': 'ef' * 32, 'bytes': 13, 'bound_against': ['receipt.files']}},
            'org_cap': 12,
            'client_roster_cap': 8,
            'exploratory': True, 'review_status': 'REVIEW_REQUIRED',
            'canonical_eligible': False, 'canonical_employment_promotions': 0,
            'lda_status_note': 'The lobbyist line is the corroboration tier, said plainly.',
            'lda_status_labels': {'L5': GROUPS_STATUS},
            'lda_privacy_class': 'restricted-machine-local',
            'singleton_mode': 'name_only_lines',
            'singleton_registrants': 1,
            'singleton_rule': SINGLETON_RULE_TEST,
            'organisation_kinds': ['registrant'],
            'clients_mode': 'separate_artifact',
            'clients_artifact': 'staffer_clients_section.json',
            'clients_note': ('Client organisations are not cards in this document. Every one '
                             'of them is in staffer_clients_section.json.'),
            'counts': {'organisations': 3, 'organisations_listed_in_full': 2,
                       'singleton_registrants': 1, 'people_listed': 1,
                       'employees_listed': 1,
                       'lobbyists_total': 7, 'lobbyists_with_no_registrant_on_file': 0,
                       'client_organisations_in_clients_artifact': 2,
                       'client_ties_in_clients_artifact': 3},
            'built_at': '2026-09-07T00:00:00Z', 'bytes': 0,
        },
        'organisations': [
            {'id': GROUPS_ORG, 'kind': 'registrant', 'name': 'ACME ADVOCACY',
             'engagement': 'in_house', 'employer_line': GROUPS_EMPLOYER,
             'lobbyist_count': 4, 'more': 3,
             'lobbyists': [{'id': 'lda:lobbyist:1', 'display_name': 'Kent R. Hance',
                            'lda_status_label': GROUPS_STATUS,
                            'registrant_tier': 'R1', 'employer_as_of': '2026 Q2'}]},
            # `lobbyists` present but empty: "nobody on file" (rule 6, second clause).
            {'id': GROUPS_ORG_EMPTY, 'kind': 'registrant', 'name': GROUPS_ORG_EMPTY_NAME,
             'engagement': 'in_house',
             'employer_line': 'Employer on file: ' + GROUPS_ORG_EMPTY_NAME,
             'lobbyist_count': 2, 'more': 2, 'lobbyists': []},
            # Single-filer: no `lobbyists` key at all (rule 6, first clause).
            {'id': GROUPS_ORG_SINGLE_FILER, 'kind': 'registrant', 'name': GROUPS_ORG_BANNED_WORD,
             'engagement': 'in_house', 'lobbyist_count': 1},
        ],
    }


def clients_fixture():
    """The clients payload the _01 template renders, shaped exactly as
    fec/surfaces/build_staffer_groups_section.py --part clients writes it.

    NORMALIZED, and this fixture is the shape's own proof: two client
    organisations, two contract filers, three ties -- and the filer who files for
    BOTH clients has their name written ONCE, in `fn`, with the two ties carrying
    only their position in that table.  Written as cards that name would appear
    twice, which at corpus scale is the 21,012,744 bytes that would not fit.

    `f` is ordered by display name, which is what lets a tie row -- written in
    filer-index order -- come out in name order with no sort on the page.
    """
    return {
        'meta': {
            'schema_id': 'staffer-clients-section-v1',
            'producer': 'fec/surfaces/build_staffer_groups_section.py',
            'producer_version': 'staffer-groups-section-v4',
            'generation': GENERATION,
            'part': 'clients',
            'siblings': ['staffer_offices_section.json',
                         'staffer_organisations_section.json'],
            'lda_generation': 'generation-test',
            'inputs': {'lda_industry_edges': {
                'path': 'generation-test/lda_industry_edges.ndjson.gz',
                'sha256': 'ab' * 32, 'bytes': 17, 'bound_against': ['receipt.files']}},
            'key_legend': {'c': 'client organisation ids, sorted, indexed by position',
                           'cn': 'client organisation display names, parallel to c',
                           'e': 'employer registrant ids', 'en': 'their names',
                           'f': 'contract filer person ids', 'fn': 'their display names',
                           'fe': 'employer index into e', 't': 'filer indices per client',
                           'tp': 'period code per tie'},
            'period_labels': ['2026 Q1', '2026 Q2'],
            'client_kind': 'client',
            'organisation_kinds': ['client'],
            'employment_ruling': (
                'For the filer, you do what is chronologically the most recent. If it appears '
                'they are contract/mercenary they are tied to all those organizations as '
                'clients.'),
            'employment_ruling_date': '2026-09-07',
            'client_rule': ('A contract filer is tied to every client organisation named on '
                            'every filing that lists them.'),
            'clients_rule': ('Every client organisation the ruling reaches is here: 2 of them, '
                             'tied to 2 contract filers by 3 filer-client ties.'),
            'lda_privacy_class': 'restricted-machine-local',
            'exploratory': True, 'review_status': 'REVIEW_REQUIRED',
            'canonical_eligible': False, 'canonical_employment_promotions': 0,
            'counts': {'client_organisations': 2, 'contract_filers': 2,
                       'employer_registrants': 1, 'filer_client_ties': 3,
                       'name_rows': 5, 'clients_with_one_filer': 1,
                       'largest_client_roster': 2, 'period_labels': 2,
                       'client_organisations_in_ledger': 2,
                       'contract_filer_client_pairs_in_ledger': 3,
                       'contract_filers_in_ledger': 2,
                       'client_organisations_dropped': 0, 'filer_client_ties_dropped': 0},
            'built_at': '2026-09-07T00:00:00Z', 'bytes': 0,
        },
        'c': [GROUPS_CLIENT, GROUPS_CLIENT_2],
        'cn': [GROUPS_CLIENT_NAME, GROUPS_CLIENT_2_NAME],
        'e': [GROUPS_CLIENT_EMPLOYER],
        'en': [GROUPS_CLIENT_EMPLOYER_NAME],
        # ordered by display name: 'Ada Quill' before 'James MacLeod'
        'f': ['lda:lobbyist:9', GROUPS_CLIENT_REPRESENTER],
        'fn': ['Ada Quill', GROUPS_CLIENT_REPRESENTER_NAME],
        'fe': [0, 0],
        't': [[0, 1], [1]],
        'tp': [[1, 0], [1]],
    }


def build(data_path, out_path, args=(), offices_path=None, organisations_path=None,
         clients_path=None, legacy_groups_path=None):
    env = dict(os.environ)
    env['STAFFER_SECTION_DATA_PATH'] = data_path
    env['STAFFER_HTML_OUT'] = out_path
    env['PYTHONIOENCODING'] = 'utf-8'
    for var, value in (('STAFFER_OFFICES_DATA_PATH', offices_path),
                       ('STAFFER_ORGANISATIONS_DATA_PATH', organisations_path),
                       ('STAFFER_CLIENTS_DATA_PATH', clients_path),
                       ('STAFFER_GROUPS_DATA_PATH', legacy_groups_path)):
        if value is not None:
            env[var] = value
        else:
            env.pop(var, None)
    run = subprocess.run([sys.executable, BUILDER] + list(args), capture_output=True, text=True,
                         encoding='utf-8', errors='replace', env=env)
    return run.returncode, (run.stdout or '') + (run.stderr or '')


def ids(text):
    """make_combined.ids(), copied verbatim so the two agree by construction."""
    return set(re.findall(r'\bid=([A-Za-z0-9_-]+)', text)) | set(re.findall(r'\bid="([^"]+)"', text))


def strip_json_islands(page):
    """Remove the SDATA/GDATA/ODATA data islands from a rendered page.

    Owner ruling 2026-09-07, item 3: the banned-word pin is a CHROME/TEMPLATE
    check.  A registrant's legal name or an office's own printed office string is
    payload data -- an identifier value, exempt from the language grep the same
    way it is exempt everywhere else on this page (Paul's earlier ruling).  All
    three embedded JSON assignments are always exactly one line each (json.dumps
    escapes any real newline inside a string), so removing the three lines removes
    the whole payload and nothing else -- chrome text elsewhere on the page is
    untouched.
    """
    page = re.sub(r'(?m)^const SDATA=.*;$', 'const SDATA={};', page)
    page = re.sub(r'(?m)^const GDATA=.*;$', 'const GDATA={};', page)
    page = re.sub(r'(?m)^const ODATA=.*;$', 'const ODATA={};', page)
    page = re.sub(r'(?m)^const CDATA=.*;$', 'const CDATA={};', page)
    return page


def pin_set(page, label, expect_members=True):
    """The template contract every staffer template has to satisfy.

    Run against BOTH shipped templates: _00 (the network surface) and _01 (the
    simplified Groups surface).  These are the pins make_combined.py, the id
    namespace and the owner's language law depend on; a new template that cannot
    pass them cannot be merged into Moneyball.html.
    """
    def name(text):
        return '%s :: %s' % (label, text)

    print('  [b] data island')
    eq(name('no __DATA__ token survives'), page.count('__DATA__'), 0)
    ok(name('the app reads its data from SDATA'), 'const SDATA=' in page)
    ok(name('the fixture generation is embedded'), GENERATION in page)
    if expect_members:
        ok(name('a fixture member name is embedded'), 'Ada Bridger' in page)
        # Ruling 1 (RELABEL, 2026-09-07): every top_staff row also names the
        # payroll container the person spent the most days in. This suite never
        # executes the page's JS (no browser here -- see the module docstring),
        # so what it can check is that the new field round-trips in the embedded
        # payload and that the template's own JS and header actually reference
        # it, not the rendered table cell, which exists only after a browser
        # runs drawTables().
        ok(name('a top_staff office name round-trips in the embedded payload'),
           'Senior Staff Office' in page)
        ok(name("the template's JS reads co_tenure_office_name"),
           'co_tenure_office_name' in page)
        ok(name("the template's JS reads co_tenure_office_size"),
           'co_tenure_office_size' in page)
        ok(name('the relabelled column states the one-sentence definition, '
                'including WHERE the named office comes from'),
           'the office named is where this person spent the most days' in page)

    print('  [c] make_combined.extract() contract')
    eq(name('exactly one <style>'), len(re.findall(r'<style>', page)), 1)
    eq(name('exactly one </style>'), len(re.findall(r'</style>', page)), 1)
    eq(name('exactly one <script tag of any kind'), len(re.findall(r'<script', page)), 1)
    eq(name('exactly one </script>'), len(re.findall(r'</script>', page)), 1)
    css = re.search(r'<style>(.*?)</style>', page, re.S)
    body = re.search(r'<body[^>]*>(.*?)<script>', page, re.S)
    js = re.search(r'<script>(.*?)</script>', page, re.S)
    ok(name('extract() finds a stylesheet'), css is not None and len(css.group(1)) > 500)
    ok(name('extract() finds body markup'), body is not None and len(body.group(1)) > 500)
    ok(name('extract() finds the app script'),
       js is not None and 'const SDATA=' in js.group(1))
    ok(name('the body carries no <script'),
       body is not None and '<script' not in body.group(1))
    if body is None or js is None:
        return

    print('  [d] id namespace')
    chunk_ids = ids(body.group(1) + js.group(1))
    ok(name('the chunk declares ids at all'), len(chunk_ids) >= 20, len(chunk_ids))
    unprefixed = sorted(i for i in chunk_ids if not i.startswith('stf-'))
    ok(name('every id in the merged chunk is prefixed stf-'), not unprefixed, unprefixed)

    print('  [e] no external loads')
    srcs = re.findall(r'\bsrc\s*=\s*"([^"]*)"', page) + re.findall(r"\bsrc\s*=\s*'([^']*)'", page)
    bare = re.findall(r'\bsrc=([^\s"\'>]+)', page)
    offending = [s for s in srcs + bare if not s.startswith('data:')]
    ok(name('no element carries a non-data src='), not offending, offending[:5])
    ok(name('no stylesheet link'),
       'rel="stylesheet"' not in page and 'rel=stylesheet' not in page)
    ok(name('no CSS @import'), '@import' not in page)
    ok(name('no font load'), 'fonts.googleapis' not in page and 'fonts.gstatic' not in page)
    remote = sorted(set(re.findall(r'https?://[^\s"\'<>)]+', page)))
    allowed_remote = {'http://127.0.0.1:8841/api/staffer-live?limit=1',
                      'http://127.0.0.1:8841/staffer-live/',
                      'http://127.0.0.1:8841/staffer-live/?person=',
                      'http://www.w3.org/2000/svg'}
    ok(name('off-document references are local live navigation and the SVG namespace'),
       set(remote) <= allowed_remote, sorted(set(remote) - allowed_remote))
    ok(name('the live probe is a fetch, never a script load'),
       "fetch('http://127.0.0.1:8841/api/staffer-live?limit=1'" in page
       and 'launch.js' not in page)

    print('  [f] owner language law')
    ok(name('the zero-overlap rejection is labelled verbatim'), ZERO_OVERLAP_LABEL in page)
    ok(name('the legend denies a member-to-member reading'),
       'They are not ties between members.' in page)
    ok(name('the exploratory / review-required flag ships'),
       'Exploratory · review required' in page)
    eq(name('the language note ships exactly once'), page.count(LANGUAGE_NOTE), 1)
    for phrase, allowance in ALLOWED:
        eq(name('the receipt statement ships exactly %d time(s)' % allowance),
           page.count(phrase), allowance)
    # The banned-word pin is a CHROME/TEMPLATE check (owner ruling 2026-09-07,
    # item 3): strip the SDATA/GDATA/ODATA payload islands first, so a
    # registrant's own name or an office's own printed string -- an identifier
    # value -- never trips it.  meta.statement lives inside the SDATA island, so
    # its one permitted "influence" already leaves with the island; the exact-
    # count check above is what verifies that copy still shipped.
    chrome = strip_json_islands(page)
    lowered = chrome.lower()
    for word in BANNED:
        eq(name('no advisory vocabulary in the template outside the payload: %r' % word),
           lowered.count(word), 0)


def main():
    workspace = tempfile.mkdtemp(prefix='mb_staffer_html_')
    try:
        data_path = os.path.join(workspace, 'staffer_network_section.json')
        offices_path = os.path.join(workspace, 'staffer_offices_section.json')
        organisations_path = os.path.join(workspace, 'staffer_organisations_section.json')
        clients_path = os.path.join(workspace, 'staffer_clients_section.json')
        with open(data_path, 'w', encoding='utf-8') as handle:
            json.dump(fixture(), handle, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        with open(offices_path, 'w', encoding='utf-8') as handle:
            json.dump(offices_fixture(), handle, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False)
        with open(organisations_path, 'w', encoding='utf-8') as handle:
            json.dump(organisations_fixture(), handle, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False)
        with open(clients_path, 'w', encoding='utf-8') as handle:
            json.dump(clients_fixture(), handle, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False)

        # (template, --sdata scope, offices, organisations, clients, renders members?)
        templates = (
            ('make_staffer_network_html_00.tpl', 'full', None, None, None, True),
            ('make_staffer_network_html_01.tpl', 'provenance', offices_path,
             organisations_path, clients_path, False),
        )
        for template, scope, off_path, org_path, cli_path, expect_members in templates:
            label = template.replace('make_staffer_network_html_', '').replace('.tpl', '')
            print('[a] render %s' % template)
            out_path = os.path.join(workspace, 'staffer-network-%s.html' % label)
            code, text = build(data_path, out_path,
                               args=['--template', template, '--sdata', scope],
                               offices_path=off_path, organisations_path=org_path,
                               clients_path=cli_path)
            eq('%s :: the builder exits 0' % label, code, 0)
            ok('%s :: the builder names what it wrote' % label,
               'wrote' in text and out_path in text, text.strip()[-300:])
            ok('%s :: the page exists' % label, os.path.exists(out_path))
            if not os.path.exists(out_path):
                continue
            page = open(out_path, encoding='utf-8').read()
            ok('%s :: the page is a complete document' % label,
               page.startswith('<!DOCTYPE html>') and page.rstrip().endswith('</html>'),
               page[:60])
            pin_set(page, label, expect_members=expect_members)

        # ------------------------------------------------------ the _01 payload
        print('[h] the _01 template carries its own three payloads')
        page01 = open(os.path.join(workspace, 'staffer-network-01.html'), encoding='utf-8').read()
        eq('no __OFFICES__ token survives', page01.count('__OFFICES__'), 0)
        eq('no __ORGS__ token survives', page01.count('__ORGS__'), 0)
        eq('no __CLIENTS__ token survives', page01.count('__CLIENTS__'), 0)
        ok('the app reads its offices from GDATA', 'const GDATA=' in page01)
        ok('the app reads its organisations from ODATA', 'const ODATA=' in page01)
        ok('the app reads its clients from CDATA', 'const CDATA=' in page01)
        eq('the page carries exactly one const GDATA= line',
           len(re.findall(r'(?m)^const GDATA=.*;$', page01)), 1)
        eq('the page carries exactly one const ODATA= line',
           len(re.findall(r'(?m)^const ODATA=.*;$', page01)), 1)
        eq('the page carries exactly one const CDATA= line',
           len(re.findall(r'(?m)^const CDATA=.*;$', page01)), 1)
        ok('an office name is embedded', 'JUDICIARY' in page01)
        ok('an organisation name is embedded', 'ACME ADVOCACY' in page01)
        # json_for_script leaves json.dumps at its ensure_ascii default, so the U+00B7
        # in the owner's label reaches the page as ·.  Decode the island rather
        # than grep for the literal: what has to be exact is the string the reader is
        # shown, not the escape the transport happens to use.
        offices_island = re.search(r'const GDATA=(.*?);\n', page01, re.S)
        orgs_island = re.search(r'const ODATA=(.*?);\n', page01, re.S)
        clients_island = re.search(r'const CDATA=(.*?);\n', page01, re.S)
        ok('the offices island is one decodable JSON value', offices_island is not None)
        ok('the organisations island is one decodable JSON value', orgs_island is not None)
        ok('the clients island is one decodable JSON value', clients_island is not None)
        decoded_offices = json.loads(offices_island.group(1)) if offices_island else {}
        decoded_orgs = json.loads(orgs_island.group(1)) if orgs_island else {}
        decoded_clients = json.loads(clients_island.group(1)) if clients_island else {}
        eq('the offices island round-trips to the fixture payload exactly',
           decoded_offices, offices_fixture())
        eq('the organisations island round-trips to the fixture payload exactly',
           decoded_orgs, organisations_fixture())
        eq('the clients island round-trips to the fixture payload exactly',
           decoded_clients, clients_fixture())
        eq('the LDA status line ships in the owner vocabulary, character for character',
           decoded_orgs['organisations'][0]['lobbyists'][0]['lda_status_label'], GROUPS_STATUS)
        # v3 (2026-09-07 employment ruling): employer_line and engagement moved
        # from the per-person row to the CARD -- every person on a registrant's
        # card has THAT registrant as their employer, so the card states it
        # once rather than repeating it per row.  What the row keeps is what
        # actually varies: registrant_tier and employer_as_of.
        eq('the employer line ships on the CARD now, not the per-person row',
           decoded_orgs['organisations'][0]['employer_line'], GROUPS_EMPLOYER)
        eq('the card states engagement once too',
           decoded_orgs['organisations'][0]['engagement'], 'in_house')
        eq('the per-person row keeps registrant_tier, which actually varies '
           'person to person',
           decoded_orgs['organisations'][0]['lobbyists'][0]['registrant_tier'], 'R1')
        eq('...and employer_as_of, the period the card employer is on file as of',
           decoded_orgs['organisations'][0]['lobbyists'][0]['employer_as_of'], '2026 Q2')
        ok('employer_line no longer rides on the per-person row at all',
           'employer_line' not in decoded_orgs['organisations'][0]['lobbyists'][0],
           decoded_orgs['organisations'][0]['lobbyists'][0])
        eq('the office people_count and more both survive the transport (owner '
           'ruling 2026-09-07, item 1: more is current-only now, not mixed)',
           (decoded_offices['offices'][0]['people_count'], decoded_offices['offices'][0]['more']), (9, 0))
        eq('earlier_count survives the transport, uncapped and unnamed',
           decoded_offices['offices'][0]['earlier_count'], 8)
        ok('no departed person leaks into an office people list',
           not any(p.get('current') is False
                   for o in decoded_offices['offices'] for p in o['people']))
        ok('provenance scope drops the member map, keeping meta and global',
           '"members":{}' in page01 and 'Ada Bridger' not in page01)
        ok('there is no canvas on the groups surface', '<canvas' not in page01)

        print('[h2] the Sources and method fold states the new office and org policy')
        ok('the current-day office note ships (a full data value, baked into GDATA)',
           CURRENT_DAY_NOTE_TEST in page01)
        # org_cap is a NUMBER the template concatenates client-side (num(OMETA.org_cap)
        # is a JS expression, not a value substitution in this static file), so only the
        # static wording either side of it can be checked without executing the page.
        ok('the organisation cap sentence template ships (rendered client-side '
           'from OMETA.org_cap)',
           'lobbyists are listed under any one organisation' in page01)
        ok('the fixture org_cap value itself reaches the page, inside ODATA',
           decoded_orgs['meta']['org_cap'] == 12)

        print('[h3] the banned-word scan is chrome-only, not a payload grep '
              '(owner ruling 2026-09-07, item 3)')
        ok('the raw page really does carry the banned word inside payload data '
           '(proving the exemption below is not vacuous)',
           GROUPS_ORG_BANNED_WORD in page01)
        chrome01 = strip_json_islands(page01)
        ok('a registrant name containing a banned token is exempt once it is '
           'payload data, not template',
           'influence' not in chrome01.lower())
        ok('stripping the islands actually removed the banned word, not just '
           'coincidentally missed it',
           GROUPS_ORG_BANNED_WORD not in chrome01)

        print('[h4] rule 6: a single-filer row and an empty-lobbyists row render '
              'different sentences')
        ok('the single-filer sentence fragment ships in the template',
           'on file under this registrant, and not named here' in page01)
        ok('the empty-lobbyists sentence ships in the template',
           'No people listed for this organisation.' in page01)
        single_filer_row = next(o for o in decoded_orgs['organisations']
                                if o['id'] == GROUPS_ORG_SINGLE_FILER)
        ok('the single-filer fixture row carries no lobbyists key at all',
           'lobbyists' not in single_filer_row, single_filer_row)
        empty_row = next(o for o in decoded_orgs['organisations'] if o['id'] == GROUPS_ORG_EMPTY)
        ok('the empty-lobbyists fixture row carries the key, not an absence',
           'lobbyists' in empty_row, empty_row)
        eq('the empty-lobbyists fixture row is an empty array, not a name-only line',
           empty_row.get('lobbyists'), [])

        print('[h5] the CLIENT half is a normalized third artifact, and the page '
              'resolves a client card out of it by index')
        ok('no client card is left in the organisations artifact',
           not any(o.get('kind') == 'client' or 'represented_by' in o
                   for o in decoded_orgs['organisations']),
           [o['id'] for o in decoded_orgs['organisations']])
        eq('and the organisations meta says where the client ties went',
           (decoded_orgs['meta']['clients_mode'], decoded_orgs['meta']['clients_artifact']),
           ('separate_artifact', 'staffer_clients_section.json'))
        eq('every client the ruling reaches is in the clients island',
           len(decoded_clients['c']), decoded_clients['meta']['counts']['client_organisations'])
        eq('TIE COUNT CONSERVED: the tie arrays hold exactly the ties the meta counts',
           sum(len(row) for row in decoded_clients['t']),
           decoded_clients['meta']['counts']['filer_client_ties'])
        eq('and that equals what the ledger reached -- nothing thresholded',
           decoded_clients['meta']['counts']['filer_client_ties'],
           decoded_clients['meta']['counts']['contract_filer_client_pairs_in_ledger'])
        eq('nothing was dropped, and the island says so in its own counters',
           (decoded_clients['meta']['counts']['client_organisations_dropped'],
            decoded_clients['meta']['counts']['filer_client_ties_dropped']), (0, 0))
        # NO DUPLICATED NAMES.  The two-client filer is the proof: written as
        # cards their name would be on the page twice, and at corpus scale that
        # repetition is the 21,012,744 bytes that would not fit the pin.
        eq('a filer who files for two clients has their name written ONCE',
           decoded_clients['fn'].count(GROUPS_CLIENT_REPRESENTER_NAME), 1)
        eq('and their id once as well',
           decoded_clients['f'].count(GROUPS_CLIENT_REPRESENTER), 1)
        ok('that filer really is tied to two clients (the check above is not vacuous)',
           sum(1 for row in decoded_clients['t']
               if decoded_clients['f'].index(GROUPS_CLIENT_REPRESENTER) in row) == 2,
           decoded_clients['t'])
        eq('the whole rendered page carries that filer\'s name exactly once, in '
           'the island: nothing about the transport duplicates it either',
           page01.count(GROUPS_CLIENT_REPRESENTER_NAME), 1)
        ok('every value in the tie arrays is an integer, never a name',
           all(isinstance(v, int) and not isinstance(v, bool)
               for key in ('t', 'tp') for row in decoded_clients[key] for v in row))
        # INDEX INTEGRITY, and the negative control on it.
        ok('every tie index resolves to a filer in this island',
           all(0 <= v < len(decoded_clients['f'])
               for row in decoded_clients['t'] for v in row))
        ok('every period code resolves to a label',
           all(0 <= v < len(decoded_clients['meta']['period_labels'])
               for row in decoded_clients['tp'] for v in row))
        ok('every employer index resolves to a registrant',
           all(0 <= v < len(decoded_clients['e']) for v in decoded_clients['fe']))
        dangling = json.loads(json.dumps(decoded_clients))
        dangling['t'][0][0] = len(dangling['f'])
        ok('NEGATIVE CONTROL: a dangling tie index fails the same check -- the '
           'check above is a measurement, not a tautology',
           not all(0 <= v < len(dangling['f']) for row in dangling['t'] for v in row))
        # What the page renders from those tables.
        ok('the client\'s own name is embedded in the page', GROUPS_CLIENT_NAME in page01)
        ok('the second client\'s name is embedded too', GROUPS_CLIENT_2_NAME in page01)
        ok('the template states how many lobbyists represent a client organisation',
           'lobbyists represent this organisation' in page01
           and 'lobbyist represents this organisation' in page01)
        ok('a client card\'s roster is built by index lookup out of the tables, '
           'not from names on the row',
           'CT[ci]' in page01 and 'FN[fx]' in page01)
        ok('a contract filer\'s own line states their employer',
           "'Employer on file: '+employer" in page01)
        ok('...and their client count, derived from the tie arrays rather than '
           'stored a second time', 'FCOUNT[fx]' in page01
           and "plural(FCOUNT[fx],'client organisation','client organisations')" in page01)
        ok('a client is reachable by search on its own name',
           'CLIENT_HAY' in page01 and 'clientMatches' in page01)
        ok('...and by the name of a lobbyist who files for it',
           'FILER_HAY' in page01 and 'FCLIENTS' in page01)
        ok('the search-miss control string is genuinely absent from the page',
           GROUPS_CLIENT_ABSENT not in page01)

        page00 = open(os.path.join(workspace, 'staffer-network-00.html'), encoding='utf-8').read()
        ok('the network surface is untouched and still carries its canvas', '<canvas' in page00)
        ok('the network surface carries no offices island', 'const GDATA=' not in page00)
        ok('the network surface carries no organisations island', 'const ODATA=' not in page00)

        print('[i] offices and organisations are each independently optional')
        # (i-1) offices absent, organisations present: the offices directory renders
        # empty and the organisations directory is NOT blanked by the absence.
        out_no_offices = os.path.join(workspace, 'no-offices.html')
        code, text = build(data_path, out_no_offices,
                           args=['--template', 'make_staffer_network_html_01.tpl',
                                 '--sdata', 'provenance'],
                           offices_path=os.path.join(workspace, 'not-there-offices.json'),
                           organisations_path=organisations_path)
        eq('an absent offices artifact still renders', code, 0)
        ok('the builder says it rendered an empty offices directory',
           'NOTE: offices section absent' in text, text.strip()[-300:])
        no_offices_page = open(out_no_offices, encoding='utf-8').read()
        ok('the empty offices payload is explicit, not a silent zero',
           'No offices section artifact was present' in no_offices_page)
        ok('the organisations directory is NOT blanked by an absent offices artifact',
           'ACME ADVOCACY' in no_offices_page)
        pin_set(no_offices_page, '01-no-offices', expect_members=False)

        # (i-2) organisations absent, offices present: the organisations directory
        # renders empty and the offices directory is NOT blanked by the absence.
        out_no_orgs = os.path.join(workspace, 'no-orgs.html')
        code, text = build(data_path, out_no_orgs,
                           args=['--template', 'make_staffer_network_html_01.tpl',
                                 '--sdata', 'provenance'],
                           offices_path=offices_path,
                           organisations_path=os.path.join(workspace, 'not-there-organisations.json'))
        eq('an absent organisations artifact still renders', code, 0)
        ok('the builder says it rendered an empty organisations directory',
           'NOTE: organisations section absent' in text, text.strip()[-300:])
        no_orgs_page = open(out_no_orgs, encoding='utf-8').read()
        ok('the empty organisations payload is explicit, not a silent zero',
           'No organisations section artifact was present' in no_orgs_page)
        ok('the offices directory is NOT blanked by an absent organisations artifact',
           'JUDICIARY' in no_orgs_page)
        pin_set(no_orgs_page, '01-no-orgs', expect_members=False)

        print('[k] the retired STAFFER_GROUPS_DATA_PATH dial is a hard error')
        out_retired = os.path.join(workspace, 'retired.html')
        code, text = build(data_path, out_retired,
                           legacy_groups_path=os.path.join(workspace, 'legacy-groups-would-go-here.json'))
        eq('a run with the retired dial set exits 1 (SystemExit with a message)', code, 1)
        ok('the retirement message names the dial',
           'STAFFER_GROUPS_DATA_PATH is retired' in text, text.strip()[-300:])
        ok('a retired-dial run writes nothing', not os.path.exists(out_retired))

        print('[g] clean skip')
        missing = os.path.join(workspace, 'not-there.json')
        out2 = os.path.join(workspace, 'skipped.html')
        code, text = build(missing, out2)
        eq('an absent section exits 0, not 2', code, 0)
        ok('the skip says so', 'SKIPPED' in text, text.strip()[-200:])
        ok('the skip writes nothing', not os.path.exists(out2))
        # A caller that invokes this builder with no switches at all must now get the
        # CURRENT shipped surface, not the retired network-only one (2026-09-07: DEFAULT_
        # TEMPLATE flipped _00 -> _01 because a switchless run used to silently revert the
        # shipped page). The chain itself names --template explicitly anyway (belt and
        # suspenders, see run_pipeline.py), but anything else that calls this bare must not
        # regress, so the default is pinned here by building it both ways. Both builds get
        # the same offices/organisations/clients fixtures so the only variable under test is
        # which template a switchless run resolves to, not what data it happened to find.
        #
        # --sdata's default follows --template too, and that half was the actual bug fixed
        # 2026-09-07: DEFAULT_TEMPLATE flipped to _01 but parse_args still defaulted --sdata
        # to the fixed string 'full', so a switchless run against the real generation embedded
        # the whole ~4.99 MB member map into a template that renders none of it and blew
        # MAX_BYTES. The reference build below pins --sdata explicitly (provenance), not just
        # --template, so this comparison is a real regression test of the default-resolution
        # logic rather than a tautology: if SDATA_DEFAULT_BY_TEMPLATE ever silently fell back
        # to 'full' for _01, the switchless build would drift from this known-correct explicit
        # reference instead of matching it for the wrong reason.
        print('[j] the default template is now _01, and --sdata follows it to provenance')
        default_out = os.path.join(workspace, 'default.html')
        code, text = build(data_path, default_out, offices_path=offices_path,
                           organisations_path=organisations_path, clients_path=clients_path)
        eq('a switchless build exits 0', code, 0)
        ok('a switchless build resolves --sdata to provenance (printed, not just applied)',
           'sdata=provenance' in text, text.strip()[-300:])
        default_ref_out = os.path.join(workspace, 'default-ref.html')
        build(data_path, default_ref_out,
             args=['--template', 'make_staffer_network_html_01.tpl', '--sdata', 'provenance'],
             offices_path=offices_path, organisations_path=organisations_path,
             clients_path=clients_path)
        ok('a switchless build renders the _01 template at --sdata provenance, byte for byte',
           open(default_out, encoding='utf-8').read()
           == open(default_ref_out, encoding='utf-8').read())

        # ---------------------------------------------- (m) the move-label ruling
        # Owner ruling 2026-09-07 applied to the move tables. The producer resolves
        # the names (fec/tests/surfaces/test_staffer_network_section.py [l]); what
        # this page owes is to CONSULT them and to never fall through to the raw
        # key again. containerLabel used to end in `return s`, and that one line
        # put "HON:H001060" in 1,514 of 5,912 published move cells. This asserts on
        # the shipped function body, because that is where the regression would
        # reappear and a render that merely succeeded would not notice it.
        print('[m] containerLabel consults the producer\'s move names and never '
              'falls back to the container key')
        eq('the rendered page carries containerLabel exactly once',
           page00.count('function containerLabel('), 1)
        fn = page00.split('function containerLabel(', 1)[1].split('\n}', 1)[0]
        ok('it reads the producer\'s map', 'META.move_office_names' in fn, fn)
        ok('it prints the producer\'s own honest-absence label when the map has no '
           'entry for a member office, rather than the key',
           'META.office_name_unresolved_label' in fn, fn)
        ok('THE REGRESSION GATE: the stripped container key is returned on exactly '
           'one branch, the non-member one, where that key IS the record\'s own '
           'office string ("COMMITTEE ON APPROPRIATIONS")',
           fn.count('return s;') == 1 and 'if(!hon)return s;' in fn, fn)
        ok('a page built from a section artifact predating the ruling names the '
           'stale artifact instead of printing a key',
           'not in this section artifact' in fn, fn)
        # NEGATIVE CONTROL: the exact function this ruling replaced. Every check
        # above must FAIL on it, or the checks above are decoration.
        prefix = ("id){\n  var s=String(id||'');\n"
                  "  s=s.replace(/^container:office:(house|senate):/,'');\n"
                  "  s=s.replace(/^container:/,'');\n"
                  "  var hon=s.match(/^HON:(.+)$/);\n"
                  "  if(hon&&nameOf[hon[1]])return nameOf[hon[1]];\n"
                  "  return s;")
        ok('the pre-ruling function consults no map', 'META.move_office_names' not in prefix)
        ok('the pre-ruling function carries no honest-absence label',
           'META.office_name_unresolved_label' not in prefix)
        ok('and it is exactly the shape the gate above rejects: `return s;` with no '
           'non-member guard in front of it -- the raw key reaching a reader',
           prefix.count('return s;') == 1 and 'if(!hon)return s;' not in prefix)

        print('[l] the chrome scan still catches a real violation (negative control)')
        fake_page = ('<style>.x{color:red}</style><body>'
                     '<summary>Please note: readers should contact their representative.</summary>'
                     '<script>\n'
                     'const SDATA={"note":"Influence Partners LLC should not trip this"};\n'
                     'const GDATA={"offices":[{"name":"Influence Partners LLC"}]};\n'
                     'const ODATA={"organisations":[{"name":"Influence Partners LLC"}]};\n'
                     '(function(){})();\n'
                     '</script>')
        fake_chrome = strip_json_islands(fake_page)
        ok('the synthetic data islands are gone after stripping',
           'Influence Partners LLC' not in fake_chrome)
        ok('stripping does not remove chrome text outside the data islands',
           'Please note' in fake_chrome)
        lowered_fake = fake_chrome.lower()
        ok('a banned word placed in CHROME (not in a data island) is still '
           'caught by the same scan the real pages use',
           any(lowered_fake.count(word) > 0 for word in BANNED))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    print('\n%d passed, %d failed' % (P, F))
    return 1 if F else 0


if __name__ == '__main__':
    sys.exit(main())
