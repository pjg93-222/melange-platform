# -*- coding: utf-8 -*-
"""Contract suite for fec/staffer_network/lda_employment.py.

That module is the ONE place Paul's 2026-09-07 ruling is turned into code:

    "For the filer, you do what is chronologically the most recent. If it appears
     they are contract/mercenary they are tied to all those organizations as
     clients."

Three consumers read it -- the static Organisations payload, the sidecar Groups
index and the plain-words status table -- so a defect here is a defect on three
surfaces at once.  The producer suite exercises the ruling end to end through the
real file formats; this one goes at the parts an integration test cannot reach:
the refusals (an unknown filing period, a filing that disagrees with itself, a
packing that no longer fits the corpus), the tie ladder in all four of its rungs,
and the name normalisation the self-filing test rests on.

Plain-assert style, no dependencies:
    python fec/tests/staffer_network/test_lda_employment.py
"""
import gzip
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FEC = os.path.dirname(os.path.dirname(HERE))
ROOT = os.path.dirname(FEC)
for path in (FEC, ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

try:                                                # package context (kernel suite)
    from fec.staffer_network import lda_employment as subject
except ImportError:                                 # direct script execution
    import lda_employment as subject                # noqa: F401  # stale-ok: guarded fallback for direct-script test runs; reached only if the fec.staffer_network import above fails (FEC and repo root are inserted onto sys.path above)

P = F = 0


def ok(name, cond, detail=''):
    global P, F
    P, F = (P + 1, F) if cond else (P, F + 1)
    print(('  OK   ' if cond else 'FAIL  ') + name
          + (('  :: ' + str(detail)) if (detail and not cond) else ''))


def eq(name, got, want):
    ok(name, got == want, 'got %r want %r' % (got, want))


def raises(name, exception, call):
    try:
        call()
    except exception as exc:
        ok(name, True)
        return str(exc)
    except Exception as exc:                        # noqa: BLE001 - the wrong error is a failure
        ok(name, False, 'raised %s: %s' % (type(exc).__name__, exc))
        return ''
    ok(name, False, 'returned without raising')
    return ''


# --------------------------------------------------------------------- fixture
# Five filers, one per case the ruling has to get right.
#
#   L1  in-house      one registrant, and that registrant's only client is itself
#   L2  contract      one registrant, three distinct clients
#   L3  moved         registrant A in 2024 Q1, registrant B in 2026 Q2
#   L4  tie           two registrants in the SAME latest period, 2 filings v 1
#   L5  deep tie      two registrants, same period, same filing count -> lower id
#
# The organisation names matter: SELF FILES INC is spelled two different ways on
# purpose, so the self-filing test is exercised through normalize_org_name rather
# than through string equality that would have passed either way.
ORGANISATIONS = [
    {'source_org_id': 'lda:registrant:1', 'display_name': 'SELF FILES, INC.',
     'organization_kind': 'registrant'},
    {'source_org_id': 'lda:client:1', 'display_name': 'Self Files Inc',
     'organization_kind': 'client'},
    {'source_org_id': 'lda:registrant:2', 'display_name': 'MERCENARY GOVERNMENT AFFAIRS LLC',
     'organization_kind': 'registrant'},
    {'source_org_id': 'lda:client:2', 'display_name': 'ALPHA WORKS', 'organization_kind': 'client'},
    {'source_org_id': 'lda:client:3', 'display_name': 'BETA HOLDINGS',
     'organization_kind': 'client'},
    {'source_org_id': 'lda:client:4', 'display_name': 'GAMMA TRUST',
     'organization_kind': 'client'},
    {'source_org_id': 'lda:registrant:3', 'display_name': 'OLD SHOP LLP',
     'organization_kind': 'registrant'},
    {'source_org_id': 'lda:registrant:4', 'display_name': 'BUSY FIRM LLP',
     'organization_kind': 'registrant'},
    {'source_org_id': 'lda:registrant:5', 'display_name': 'QUIET FIRM LLP',
     'organization_kind': 'registrant'},
]

# (filing uuid, registrant, client, year, period, [lobbyists])
FILINGS = [
    ('f-inhouse', 'lda:registrant:1', 'lda:client:1', 2026, 'second_quarter', ['lda:lobbyist:1']),
    ('f-c1', 'lda:registrant:2', 'lda:client:2', 2026, 'second_quarter', ['lda:lobbyist:2']),
    ('f-c2', 'lda:registrant:2', 'lda:client:3', 2025, 'fourth_quarter', ['lda:lobbyist:2']),
    ('f-c3', 'lda:registrant:2', 'lda:client:4', 2024, 'first_quarter', ['lda:lobbyist:2']),
    ('f-old', 'lda:registrant:3', 'lda:client:2', 2024, 'first_quarter', ['lda:lobbyist:3']),
    ('f-new', 'lda:registrant:2', 'lda:client:2', 2026, 'second_quarter', ['lda:lobbyist:3']),
    ('f-tie-a1', 'lda:registrant:4', 'lda:client:2', 2026, 'first_quarter', ['lda:lobbyist:4']),
    ('f-tie-a2', 'lda:registrant:4', 'lda:client:3', 2026, 'first_quarter', ['lda:lobbyist:4']),
    ('f-tie-b1', 'lda:registrant:5', 'lda:client:2', 2026, 'first_quarter', ['lda:lobbyist:4']),
    ('f-deep-a', 'lda:registrant:4', 'lda:client:2', 2025, 'third_quarter', ['lda:lobbyist:5']),
    ('f-deep-b', 'lda:registrant:5', 'lda:client:3', 2025, 'third_quarter', ['lda:lobbyist:5']),
]


def write(directory, name, records):
    path = os.path.join(directory, name)
    with gzip.open(path, 'wt', encoding='utf-8') as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    return path


def corpus(directory, filings=FILINGS, organisations=ORGANISATIONS):
    """The three files build_ledger reads, in their real shapes."""
    activities, edges = [], []
    for uuid, registrant, client, year, period, lobbyists in filings:
        activity = 'lda:activity:%s:0' % uuid
        activities.append({
            'source_activity_id': activity, 'filing_uuid': uuid, 'activity_ordinal': 0,
            'registrant_source_org_id': registrant, 'client_source_org_id': client,
            'filing_year': year, 'filing_period': period,
            'schema_id': 'staffer-network-lda-activity-assertion-v1'})
        for person in lobbyists:
            edges.append({'edge_id': 'lda:edge:%s:%s' % (uuid, person),
                          'predicate': 'NAMED_ON_ACTIVITY', 'subject_id': person,
                          'subject_kind': 'person', 'object_id': activity,
                          'object_kind': 'lda_activity',
                          'schema_id': 'staffer-network-lda-industry-edge-v1'})
    return (write(directory, 'activities.ndjson.gz', activities),
            write(directory, 'edges.ndjson.gz', edges),
            write(directory, 'organisations.ndjson.gz', organisations))


def main():
    print('[a] the ruling is quoted, not paraphrased')
    ok('the module carries Paul\'s sentence verbatim',
       subject.RULING == ('For the filer, you do what is chronologically the most recent. If it '
                          'appears they are contract/mercenary they are tied to all those '
                          'organizations as clients.'), subject.RULING)
    eq('and the date it was ruled', subject.RULING_DATE, '2026-09-07')

    print('[b] filing periods order by when the period ENDS, and an unknown one refuses')
    eq('Q1 2026 sorts before Q2 2026',
       subject.period_key(2026, 'first_quarter') < subject.period_key(2026, 'second_quarter'),
       True)
    eq('Q4 2025 sorts before Q1 2026',
       subject.period_key(2025, 'fourth_quarter') < subject.period_key(2026, 'first_quarter'),
       True)
    eq('a pre-2008 semiannual mid_year ties with a second quarter',
       subject.period_key(2007, 'mid_year'), subject.period_key(2007, 'second_quarter'))
    eq('a key renders as the period a reader would say',
       subject.period_label(subject.period_key(2026, 'second_quarter')), '2026 Q2')
    message = raises('an unknown filing period REFUSES rather than sorting arbitrarily',
                     subject.EmploymentContradiction,
                     lambda: subject.period_key(2026, 'harvest_festival'))
    ok('and the refusal says it will not guess', 'refuses to guess' in message, message)

    print('[c] organisation names normalise for the self-filing test only')
    eq('corporate form words and punctuation do not make two organisations',
       subject.normalize_org_name('SELF FILES, INC.'), subject.normalize_org_name('Self Files Inc'))
    eq('MICROSOFT CORPORATION is MICROSOFT CORP.',
       subject.normalize_org_name('MICROSOFT CORPORATION'),
       subject.normalize_org_name('Microsoft Corp.'))
    ok('but a different company is still a different company',
       subject.normalize_org_name('MICROSOFT') != subject.normalize_org_name('MICROSOFT MOBILITY'))

    print('[d] the activity id yields its filing, and anything else refuses')
    eq('an activity id gives up its filing uuid',
       subject.filing_uuid_of('lda:activity:2da8c97a-0c80-4137-a257-e760cacb7c67:3'),
       '2da8c97a-0c80-4137-a257-e760cacb7c67')
    raises('a non-activity id refuses', subject.EmploymentContradiction,
           lambda: subject.filing_uuid_of('lda:lobbyist:1'))

    with tempfile.TemporaryDirectory() as directory:
        activities, edges, organisations = corpus(directory)
        ledger = subject.build_ledger(activities, edges, organisations)

        print('[e] one employer per filer: the most recent filing period')
        eq('the in-house filer works for the registrant that filed for it',
           ledger.employer['lda:lobbyist:1'], 'lda:registrant:1')
        eq('the contract filer works for the firm', ledger.employer['lda:lobbyist:2'],
           'lda:registrant:2')
        eq('a filer who CHANGED registrant works for the LATER one, not the earlier',
           ledger.employer['lda:lobbyist:3'], 'lda:registrant:2')
        eq('and the period that employer is on file as of is the later one',
           subject.period_label(ledger.employer_as_of['lda:lobbyist:3']), '2026 Q2')
        eq('the earlier registrant becomes a FORMER employer, with its own last period',
           [(ident, subject.period_label(key))
            for ident, key in ledger.formers['lda:lobbyist:3']],
           [('lda:registrant:3', '2024 Q1')])
        eq('a filer who never moved has no former employers',
           ledger.formers['lda:lobbyist:2'], [])
        eq('the old shop employs nobody now', 'lda:registrant:3',
           next((r for r in ledger.registrant_engagement
                 if r not in ledger.employees_by_registrant()), 'lda:registrant:3'))

        print('[f] a tie inside one period goes to the registrant with more filings')
        eq('the busy firm wins the tie, not the quiet one',
           ledger.employer['lda:lobbyist:4'], 'lda:registrant:4')
        eq('and the ledger records WHICH rule decided it',
           ledger.census['tie_rules'].get(subject.TIE_BY_PERIOD_FILINGS), 1)
        eq('a tie the filing count cannot break falls to the lower registrant id',
           ledger.employer['lda:lobbyist:5'], 'lda:registrant:4')
        eq('and that rung is recorded too',
           ledger.census['tie_rules'].get(subject.TIE_BY_ID), 1)
        eq('the filers with no tie at all are counted separately',
           ledger.census['tie_rules'].get(subject.TIE_SINGLE), 3)

        print('[g] contract or in-house is a property of the employer')
        eq('a registrant whose only client is itself is in-house',
           ledger.engagement['lda:lobbyist:1'], subject.IN_HOUSE)
        eq('a registrant that files for other organisations is contract',
           ledger.engagement['lda:lobbyist:2'], subject.CONTRACT)
        eq('the spelling difference did NOT make the self-filer look contract',
           ledger.registrant_engagement['lda:registrant:1'], subject.IN_HOUSE)
        eq('the census counts filers by engagement',
           (ledger.census['contract'], ledger.census['in_house']), (4, 1))

        print('[h] a contract filer is tied to every client on every filing that lists them')
        eq('all three of the contract filer\'s clients, over all periods',
           sorted(ledger.clients['lda:lobbyist:2']),
           ['lda:client:2', 'lda:client:3', 'lda:client:4'])
        eq('each carrying the LATEST period it appeared on',
           subject.period_label(ledger.clients['lda:lobbyist:2']['lda:client:3']), '2025 Q4')
        eq('an in-house filer has no client roster at all',
           ledger.clients['lda:lobbyist:1'], {})
        eq('the moved filer keeps the client from the registrant they LEFT -- the ruling '
           'says all those organizations, not all the current ones',
           sorted(ledger.clients['lda:lobbyist:3']), ['lda:client:2'])
        by_client = ledger.filers_by_client()
        ok('a client roster names only contract filers',
           all(ledger.is_contract(person)
               for members in by_client.values() for person, _key in members), by_client)
        ok('the self-filing client has no roster, because its filer is in-house',
           'lda:client:1' not in by_client, sorted(by_client))
        eq('one person appears under all three of their clients',
           sorted(client for client, members in by_client.items()
                  if any(person == 'lda:lobbyist:2' for person, _k in members)),
           ['lda:client:2', 'lda:client:3', 'lda:client:4'])

        print('[i] one membership per person on the employee side')
        employees = ledger.employees_by_registrant()
        seen = [person for members in employees.values() for person in members]
        eq('every filer is an employee exactly once', len(seen), len(set(seen)))
        eq('and all five of them are placed', len(seen), 5)

        print('[j] the census is measured, not asserted')
        eq('filers', ledger.census['filers'], 5)
        eq('filings read', ledger.census['filings'], len(FILINGS))
        eq('NAMED_ON_ACTIVITY edges joined', ledger.census['named_on_activity'], len(FILINGS))
        eq('no edge pointed at a filing that is not there',
           ledger.census['unmatched_activities'], 0)
        distribution = ledger.census['clients_per_contract_filer']
        eq('clients per contract filer counts only contract filers',
           distribution['n'], ledger.census['contract'])
        eq('and its maximum is the three-client filer', distribution['max'], 3)
        # Three, not one: each of the two tie filers also carries the registrant
        # that LOST its tie. That loser filed in the same period as the winner, so
        # it is concurrent rather than older, and the field it sits in is called
        # former_employers. The ledger counts that case rather than papering over
        # the word, and this asserts the count is real and not zero-by-accident.
        eq('former employer rows are counted', ledger.census['former_employer_rows'], 3)
        eq('as are the people who have one',
           ledger.census['filers_with_a_former_employer'], 3)
        eq('and the two tie losers are counted as CONCURRENT, not older',
           ledger.census['concurrent_registrants_in_former_employers'], 2)
        eq('the genuinely former one is not counted among them',
           [subject.period_label(key) for _ident, key in ledger.formers['lda:lobbyist:3']],
           ['2024 Q1'])

    print('[j2] filings are counted once per person, not once per issue-area edge')
    with tempfile.TemporaryDirectory() as directory:
        multi = subject.Filings()
        for uuid, registrant in (('fa', 'A'), ('fb1', 'B'), ('fb2', 'B')):
            multi.add(uuid, registrant, 'C', subject.period_key(2026, 'first_quarter'))
        # filing fa names the person on 5 issue areas, fb1 and fb2 on one each
        edge_rows = [{'predicate': 'NAMED_ON_ACTIVITY', 'subject_id': 'p',
                      'object_id': 'lda:activity:%s:%d' % (uuid, n)}
                     for uuid, n in [('fa', i) for i in range(5)] + [('fb1', 0), ('fb2', 0)]]
        edges = write(directory, 'multi_edges.ndjson.gz', edge_rows)
        people, _counts = subject.read_filer_links(edges, multi)
        chosen, _latest, _formers, rule = subject.employer_of(people['p'], multi.registrant_ids)
        eq('the registrant with 2 filings beats the one with 1 filing on 5 activities',
           multi.registrant_ids[chosen], 'B')
        eq('and the tie is recorded as decided by filings in the period', rule,
           subject.TIE_BY_PERIOD_FILINGS)

    print('[j3] the affiliate-shaped count is clients that are not the registrant itself')
    names = {'R': 'AFFIL PARENT INC', 'C1': 'Affil Parent, Inc.', 'C2': 'SUBSIDIARY LLC'}
    mixed = subject.Filings()
    mixed.add('g1', 'R', 'C1', 1)
    mixed.add('g2', 'R', 'C2', 1)
    _engagement, client_counts, other_counts = subject.registrant_engagement(mixed, names)
    eq('the total client count includes the self-filing', client_counts[0], 2)
    eq('the other-client count does not', other_counts[0], 1)

    print('[k] a filing that disagrees with itself REFUSES; it is not resolved by guess')
    with tempfile.TemporaryDirectory() as directory:
        # Two activities inside one filing naming two different clients. Real
        # LD-2s never do this (verified over all 1,037,547 activity rows), which
        # is exactly why a corpus that started to would have to be looked at.
        broken = list(FILINGS) + [('f-c1', 'lda:registrant:2', 'lda:client:4', 2026,
                                   'second_quarter', ['lda:lobbyist:2'])]
        activities, edges, organisations = corpus(directory, filings=broken)
        message = raises('activities that disagree about their own filing refuse the build',
                         subject.EmploymentContradiction,
                         lambda: subject.build_ledger(activities, edges, organisations))
        ok('and the refusal says it will not pick a winner',
           'will not pick a winner' in message, message)

    print('[l] the filing table refuses to overflow its own packing')
    filings = subject.Filings()
    raises('a period key past the packed width refuses rather than wrapping',
           subject.EmploymentContradiction,
           lambda: filings.add('f', 'lda:registrant:1', 'lda:client:1',
                               subject.Filings._MAX_KEY + 1))
    ok('a key inside the width is accepted',
       filings.add('f', 'lda:registrant:1', 'lda:client:1',
                   subject.period_key(2026, 'second_quarter')) is None)
    eq('and reads back as what went in',
       filings.get('f'), (0, 0, subject.period_key(2026, 'second_quarter')))
    eq('an unknown filing reads back as nothing', filings.get('nope'), None)

    print('[m] reading the shipped artifact back gives the same three facts')
    payload = {'meta': {'schema_id': 'staffer-organisations-section-v3',
                        'clients_mode': 'min_20_representing_filers',
                        'counts': {'client_organisations_dropped': 41000}},
               'organisations': [
                   {'id': 'lda:registrant:2', 'kind': 'registrant', 'name': 'MERCENARY GA',
                    'engagement': 'contract', 'lobbyist_count': 2,
                    'lobbyists': [{'id': 'lda:lobbyist:2', 'display_name': 'A Filer',
                                   'employer_as_of': '2026 Q2', 'client_count': 3}]},
                   {'id': 'lda:client:2', 'kind': 'client', 'name': 'ALPHA WORKS',
                    'represented_by_count': 2,
                    'represented_by': [{'id': 'lda:lobbyist:2', 'display_name': 'A Filer',
                                        'latest_period': '2026 Q2'}]}]}
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, 'staffer_organisations_section.json')
        with open(path, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream)
        recovered = subject.load_from_organisations_artifact(path)
    person = recovered['people']['lda:lobbyist:2']
    eq('the employer comes back', person['employer'], 'lda:registrant:2')
    eq('the engagement comes off the CARD, where the artifact states it once',
       person['engagement'], 'contract')
    eq('the period comes back', person['as_of'], '2026 Q2')
    eq('the client count comes back', person['client_count'], 3)
    eq('and the client itself', sorted(person['clients']), ['lda:client:2'])
    eq('a build that dropped client organisations reports itself truncated',
       recovered['truncated'], True)
    with tempfile.TemporaryDirectory() as directory:
        missing = os.path.join(directory, 'nothing.json')
        eq('a missing artifact returns None so a caller can fall back, not raise',
           subject.load_from_organisations_artifact(missing), None)
        wrong = os.path.join(directory, 'wrong.json')
        with open(wrong, 'w', encoding='utf-8') as stream:
            json.dump({'meta': {}, 'offices': []}, stream)
        eq('and so does a document that is not an organisations artifact',
           subject.load_from_organisations_artifact(wrong), None)

    # ---------------------------------------------------------------- (m2)
    # The 2026-09-07 client normalization.  The client ties left the
    # organisations document -- where a byte cap had thresholded them to 203 of
    # 41,203 -- for a normalized sibling that carries all of them.  This module
    # owns both ends of that file: CLIENTS_KEY_LEGEND is what the producer ships
    # as meta.key_legend, and load_from_clients_artifact is what reads it back.
    print('[m2] the normalized clients artifact reads back to the same shape')
    clients_payload = {
        'meta': {'schema_id': subject.CLIENTS_SCHEMA,
                 'period_labels': ['2026 Q1', '2026 Q2'],
                 'counts': {'client_organisations': 2, 'filer_client_ties': 3}},
        'c': ['lda:client:2', 'lda:client:3'],
        'cn': ['ALPHA WORKS', 'BETA MUTUAL'],
        'e': ['lda:registrant:2'],
        'en': ['MERCENARY GA'],
        'f': ['lda:lobbyist:2', 'lda:lobbyist:7'],
        'fn': ['A Filer', 'B Filer'],
        'fe': [0, 0],
        't': [[0, 1], [0]],
        'tp': [[1, 0], [1]],
    }
    organisations_v4 = {
        'meta': {'schema_id': 'staffer-organisations-section-v4',
                 'clients_mode': subject.CLIENTS_ELSEWHERE,
                 'clients_artifact': subject.CLIENTS_FILENAME,
                 'counts': {'employees_not_named': 0}},
        'organisations': [
            {'id': 'lda:registrant:2', 'kind': 'registrant', 'name': 'MERCENARY GA',
             'engagement': 'contract', 'lobbyist_count': 2,
             'lobbyists': [{'id': 'lda:lobbyist:2', 'display_name': 'A Filer',
                            'employer_as_of': '2026 Q2', 'client_count': 2}]}]}
    with tempfile.TemporaryDirectory() as directory:
        org_path = os.path.join(directory, 'staffer_organisations_section.json')
        cli_path = os.path.join(directory, subject.CLIENTS_FILENAME)
        with open(org_path, 'w', encoding='utf-8') as stream:
            json.dump(organisations_v4, stream)
        with open(cli_path, 'w', encoding='utf-8') as stream:
            json.dump(clients_payload, stream)
        direct = subject.load_from_clients_artifact(cli_path)
        merged = subject.load_from_organisations_artifact(org_path)
        merged_explicit = subject.load_from_organisations_artifact(org_path,
                                                                   clients_path=cli_path)
        # The client half alone, before any merge.
        eq('every tie is expanded', direct['ties'], 3)
        eq('every client is present', direct['clients'], 2)
        eq('a filer tied to two clients gets both, resolved by index',
           sorted(direct['people']['lda:lobbyist:2']['clients']),
           ['lda:client:2', 'lda:client:3'])
        eq('a client name is resolved from cn, where it is written once',
           direct['people']['lda:lobbyist:2']['clients']['lda:client:3']['name'],
           'BETA MUTUAL')
        eq('a tie period is resolved through meta.period_labels',
           direct['people']['lda:lobbyist:2']['clients']['lda:client:2']['latest_period'],
           '2026 Q2')
        eq('and a different tie on the same client gets its own period',
           direct['people']['lda:lobbyist:7']['clients']['lda:client:2']['latest_period'],
           '2026 Q1')
        eq('the employer comes off the fe index into e',
           direct['people']['lda:lobbyist:7']['employer'], 'lda:registrant:2')
        # The merge: employer from the registrant card, clients from the sibling.
        eq('the merged read finds the sibling beside the organisations file '
           'without being told where it is',
           sorted(merged['people']['lda:lobbyist:2']['clients']),
           ['lda:client:2', 'lda:client:3'])
        eq('an explicit clients_path gives the same answer',
           merged_explicit['people'], merged['people'])
        eq('the employer line still comes from the registrant CARD',
           merged['people']['lda:lobbyist:2']['employer'], 'lda:registrant:2')
        eq('a filer named on no registrant card still gets their clients',
           sorted(merged['people']['lda:lobbyist:7']['clients']), ['lda:client:2'])
        eq('a complete client half is NOT a truncated read any more: since the '
           'split it is complete or it is missing',
           merged['truncated'], False)
        eq('and the read reports how much of it there was',
           (merged['client_organisations'], merged['client_ties']), (2, 3))
        # Negative controls.
        os.remove(cli_path)
        gone = subject.load_from_organisations_artifact(org_path)
        eq('a MISSING clients sibling reports truncated rather than pretending '
           'the ruling reaches nobody', gone['truncated'], True)
        eq('and leaves the client rosters empty rather than inventing them',
           gone['people']['lda:lobbyist:2']['clients'], {})
        dangling = dict(clients_payload)
        dangling['t'] = [[0, 99], [0]]
        bad_path = os.path.join(directory, 'dangling.json')
        with open(bad_path, 'w', encoding='utf-8') as stream:
            json.dump(dangling, stream)
        try:
            subject.load_from_clients_artifact(bad_path)
            raised, detail = False, 'did not raise'
        except subject.EmploymentContradiction as exc:
            raised, detail = True, str(exc)
        ok('a DANGLING tie index refuses instead of silently dropping the tie -- '
           'a short roster with nothing saying so is the one failure a '
           'normalized payload can have that a card could not',
           raised and 'not one of the' in detail, detail)
        short = dict(clients_payload)
        short['cn'] = ['ALPHA WORKS']
        short_path = os.path.join(directory, 'short.json')
        with open(short_path, 'w', encoding='utf-8') as stream:
            json.dump(short, stream)
        try:
            subject.load_from_clients_artifact(short_path)
            raised_short, detail_short = False, 'did not raise'
        except subject.EmploymentContradiction as exc:
            raised_short, detail_short = True, str(exc)
        ok('parallel arrays of different lengths refuse on the read side too',
           raised_short and 'parallel arrays' in detail_short, detail_short)
        eq('a missing clients artifact returns None, not an exception',
           subject.load_from_clients_artifact(os.path.join(directory, 'nope.json')), None)
        eq('and so does a document that is not a clients artifact',
           subject.load_from_clients_artifact(org_path), None)
    ok('the key legend names every table the producer writes',
       set(subject.CLIENTS_KEY_LEGEND) == {'c', 'cn', 'e', 'en', 'f', 'fn', 'fe', 't', 'tp'},
       sorted(subject.CLIENTS_KEY_LEGEND))

    print('\n%d passed, %d failed' % (P, F))
    return 1 if F else 0


if __name__ == '__main__':
    sys.exit(main())
