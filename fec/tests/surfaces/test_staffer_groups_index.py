# -*- coding: utf-8 -*-
"""Contract suite for fec/surfaces/staffer_groups_index.py.

This module builds the Groups tab the 8841 staffer-live sidecar serves.  It is a
second implementation of one ruled question -- WHICH CONTAINERS ARE OFFICES --
and until 2026-09-07 it answered differently from the static page's producer: it
whitelisted ``admin`` and the producer did not.

The ruling in force is that administrative payroll headers are not workplaces.  A
cheque drawn on "SERGEANT AT ARMS AND DOORKEEPER OF THE SENATE" or "CLERK OF THE
HOUSE" is not evidence that anybody worked in a place of that name, and the same
ruling put ``CO_TENURE_EXCLUDED_CONTAINER_KINDS = {'admin'}`` into the social
network builder, where 54 admin containers accounted for 70.8% of the v5
co-tenure pairs.  Measured on the real generation, admin containers with at least
one CURRENT person number 18 of the 721 offices that would otherwise ship.

So the two lists are held EQUAL here, by import, and the exclusion is driven
end to end with a negative control that proves the fixture would have produced
the office had the rule not been applied.  A rule that only ever runs on data
where it changes nothing is not tested, it is decorated.

Plain-assert style, no dependencies:
    python fec/tests/surfaces/test_staffer_groups_index.py
"""
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
FEC = os.path.dirname(os.path.dirname(HERE))
ROOT = os.path.dirname(FEC)
for path in (FEC, ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

try:                                                # package context (kernel suite)
    from fec.surfaces import staffer_groups_index as subject
except ImportError:                                 # direct script execution
    import staffer_groups_index as subject          # noqa: F401  # stale-ok: guarded fallback for direct-script test runs; reached only if the fec.surfaces import above fails (FEC and repo root are inserted onto sys.path above)

P = F = 0


def ok(name, cond, detail=''):
    global P, F
    P, F = (P + 1, F) if cond else (P, F + 1)
    print(('  OK   ' if cond else 'FAIL  ') + name
          + (('  :: ' + str(detail)) if (detail and not cond) else ''))


def eq(name, got, want):
    ok(name, got == want, 'got %r want %r' % (got, want))


# --------------------------------------------------------------------- fixture
# Four containers, one of every kind the payroll record produces, plus one
# organisation.  Each has exactly one person, so an emitted office is
# unmistakable in the output and a suppressed one leaves no trace but a count.
MEMBER_OFFICE = 'container:office:house:HON:A000001'
COMMITTEE = 'container:office:senate:JUDICIARY'
LEADERSHIP = 'container:office:house:SPEAKER OF THE HOUSE'
ADMIN = 'container:office:senate:SERGEANT AT ARMS AND DOORKEEPER OF THE SENATE'
UNRESOLVED = 'container:office:house:UNRESOLVED'
ORG = 'organization:lda:registrant:100'

CONTAINER_KIND = {
    MEMBER_OFFICE: 'member_office',
    COMMITTEE: 'committee',
    LEADERSHIP: 'leadership',
    ADMIN: 'admin',
    UNRESOLVED: 'unresolved',
}
PEOPLE_IN = {
    MEMBER_OFFICE: 'person:hill:person:p1',
    COMMITTEE: 'person:hill:person:p2',
    LEADERSHIP: 'person:hill:person:p3',
    ADMIN: 'person:hill:person:p4',
    UNRESOLVED: 'person:hill:person:p5',
    ORG: 'person:hill:person:p6',
}
NAMES = {'person:hill:person:p1': 'Ada Bridger',
         'person:hill:person:p2': 'Bo Carver',
         'person:hill:person:p3': 'Cy McDonald',
         'person:hill:person:p4': 'Dee Ellery',
         'person:hill:person:p5': 'Ed Foster',
         'person:hill:person:p6': 'Fay Gaines'}


def fixture():
    """(people rows, nodes, facts) in exactly the shapes build_groups_index reads."""
    nodes = {}
    for container, kind in CONTAINER_KIND.items():
        nodes[container] = {'id': container, 'kind': 'container', 'node_kind': 'container',
                            'container_kind': kind, 'label': container.rsplit(':', 1)[-1]}
    nodes[ORG] = {'id': ORG, 'kind': 'organization', 'node_kind': 'organization',
                  'label': 'ACME ADVOCACY'}
    people_rows = []
    features = {}
    for group, person in PEOPLE_IN.items():
        nodes[person] = {
            'id': person, 'kind': 'person', 'node_kind': 'person', 'label': NAMES[person],
            'stints': [{'container_id': group, 'status': 'current', 'title_last': 'AIDE'}],
        }
        # row[0] id, row[1] name, row[8] kind -- the people-index row shape.
        people_rows.append([person, NAMES[person], '', '', '', '', '', '', 'person'])
        features[person] = {'current_employment': {group: {'label': nodes[group]['label']}}}
    facts = SimpleNamespace(people={p: {} for p in NAMES}, features=features, coverage={})
    return people_rows, nodes, facts


def build(people_cap=subject.PEOPLE_CAP, lda_employment=None):
    people_rows, nodes, facts = fixture()
    return subject.build_groups_index(people_rows, nodes, 'rev-test', facts=facts,
                                      people_cap=people_cap,
                                      lda_employment=lda_employment)['payload']


# ---------------------------------------------------- the employment ruling
# Paul, 2026-09-07: "For the filer, you do what is chronologically the most
# recent. If it appears they are contract/mercenary they are tied to all those
# organizations as clients."
#
# Four filers, one of each case the ruling has to get right, all reaching this
# module through the shape lda_employment.load_from_organisations_artifact
# returns.  These people are NOT in PEOPLE_IN: the ruling has to be able to place
# a person the office fixture never touched, which is the real case (a lobbyist
# with no Hill payroll record at all).
CONTRACT_FILER = 'person:lda:lobbyist:11'
IN_HOUSE_FILER = 'person:lda:lobbyist:22'
MOVED_FILER = 'person:lda:lobbyist:33'
UNRULED_FILER = 'person:lda:lobbyist:44'

FIRM = 'organization:lda:registrant:500'
SHOP = 'organization:lda:registrant:600'
CLIENT_A = 'organization:lda:client:701'
CLIENT_B = 'organization:lda:client:702'
CLIENT_C = 'organization:lda:client:703'

RULING_NAMES = {CONTRACT_FILER: 'Gil Harrow', IN_HOUSE_FILER: 'Hana Ito',
                MOVED_FILER: 'Ivo Jansen', UNRULED_FILER: 'Jo Kettle'}

EMPLOYMENT = {
    'people': {
        'lda:lobbyist:11': {
            'employer': FIRM, 'employer_name': 'HARROW & CO GOVERNMENT AFFAIRS',
            'engagement': 'contract', 'as_of': '2026 Q2', 'client_count': 3,
            'clients': {CLIENT_A: {'name': 'ALPHA WORKS', 'latest_period': '2026 Q2'},
                        CLIENT_B: {'name': 'BETA HOLDINGS', 'latest_period': '2025 Q4'},
                        CLIENT_C: {'name': 'GAMMA TRUST', 'latest_period': '2024 Q1'}}},
        'lda:lobbyist:22': {
            'employer': SHOP, 'employer_name': 'ITO INDUSTRIES',
            'engagement': 'in_house', 'as_of': '2026 Q1', 'client_count': None,
            'clients': {}},
        'lda:lobbyist:33': {
            'employer': FIRM, 'employer_name': 'HARROW & CO GOVERNMENT AFFAIRS',
            'engagement': 'contract', 'as_of': '2025 Q3', 'client_count': 1,
            'clients': {CLIENT_A: {'name': 'ALPHA WORKS', 'latest_period': '2025 Q3'}}},
    },
    'truncated': True,
    'meta': {'clients_mode': 'min_20_representing_filers'},
}


def ruling_fixture():
    """The office fixture, plus four LDA filers the ruling can be applied to."""
    people_rows, nodes, facts = fixture()
    for ident, name in RULING_NAMES.items():
        nodes[ident] = {'id': ident, 'kind': 'person', 'node_kind': 'person', 'label': name,
                        'lda_source_person_id': ident.split(':', 1)[1]}
        people_rows.append([ident, name, '', '', '', '', '', '', 'person'])
        facts.people[ident] = {}
    return people_rows, nodes, facts


def build_ruled(lda_employment=EMPLOYMENT, people_cap=subject.PEOPLE_CAP):
    people_rows, nodes, facts = ruling_fixture()
    return subject.build_groups_index(people_rows, nodes, 'rev-test', facts=facts,
                                      people_cap=people_cap,
                                      lda_employment=lda_employment)['payload']


def main():
    print('[a] the ruled office scope')
    eq('OFFICE_CONTAINER_KINDS is exactly the three workplace kinds',
       tuple(subject.OFFICE_CONTAINER_KINDS), ('member_office', 'committee', 'leadership'))
    ok('admin is NOT an office kind (administrative payroll headers are not '
       'workplaces, ruling in force 2026-09-07)',
       'admin' not in subject.OFFICE_CONTAINER_KINDS, subject.OFFICE_CONTAINER_KINDS)
    ok('unresolved is not an office kind either, because it names nothing',
       'unresolved' not in subject.OFFICE_CONTAINER_KINDS, subject.OFFICE_CONTAINER_KINDS)

    print('[b] the two implementations of the same ruling are held equal')
    try:
        from fec.surfaces import build_staffer_groups_section as producer
    except ImportError as exc:                            # pragma: no cover - env dependent
        producer = None
        print('  SKIP   the static-page producer could not be imported here (%s)' % exc)
    if producer is not None:
        eq('the sidecar Groups tab and the static Groups producer whitelist the '
           'same container kinds, in the same order',
           tuple(subject.OFFICE_CONTAINER_KINDS), tuple(producer.OFFICE_KINDS))

    print('[c] _kind_of classifies each container kind')
    for container, kind in sorted(CONTAINER_KIND.items()):
        want = 'office' if kind in subject.OFFICE_CONTAINER_KINDS else None
        eq('a %s container classifies as %r' % (kind, want),
           subject._kind_of(container, {'node_kind': 'container', 'container_kind': kind}), want)
    eq('an organization node still classifies as an organization',
       subject._kind_of(ORG, {'node_kind': 'organization'}), 'organization')
    eq('a LittleSis entity with no node record is still an organisation',
       subject._kind_of('littlesis:entity:9', None), 'organization')
    eq('a bare unknown id with no node record is nothing',
       subject._kind_of('whatever:1', None), None)

    print('[d] end to end: an admin container never becomes an office')
    payload = build()
    offices = {group['id']: group for group in payload['offices']}
    organizations = {group['id']: group for group in payload['organizations']}
    eq('exactly the three workplace containers are emitted as offices',
       sorted(offices), sorted([COMMITTEE, LEADERSHIP, MEMBER_OFFICE]))
    ok('the admin payroll header is not an office', ADMIN not in offices)
    ok('the unresolved container is not an office', UNRESOLVED not in offices)
    ok('and neither is anywhere in the organisations list either',
       ADMIN not in organizations and UNRESOLVED not in organizations, sorted(organizations))
    ok('the person whose only container is the admin header is named in no group',
       not any(entry['id'] == PEOPLE_IN[ADMIN]
               for group in payload['offices'] + payload['organizations']
               for entry in group['people']))
    ok('the suppressed keys are COUNTED, not silently dropped',
       payload['counts']['keys_outside_scope'] >= 2,
       payload['counts']['keys_outside_scope'])
    eq('the payload states the scope it applied',
       payload['office_scope'], ['member_office', 'committee', 'leadership'])
    eq('the organisation is still emitted', sorted(organizations), [ORG])

    print('[e] negative control: the fixture WOULD have produced the office')
    # A rule that changes nothing on the data it runs against is untested.  Put
    # admin back in scope for one build and prove the office appears, so the
    # assertions above are about the rule and not about a fixture that never had
    # an admin container in it in the first place.
    original = subject.OFFICE_CONTAINER_KINDS
    try:
        subject.OFFICE_CONTAINER_KINDS = original + ('admin',)
        widened = build()
    finally:
        subject.OFFICE_CONTAINER_KINDS = original
    widened_offices = {group['id']: group for group in widened['offices']}
    ok('with admin whitelisted the same fixture DOES emit the admin header as an '
       'office, so the exclusion above is doing real work',
       ADMIN in widened_offices, sorted(widened_offices))
    eq('and it would have carried its person',
       [entry['name'] for entry in widened_offices[ADMIN]['people']], ['Dee Ellery'])
    eq('re-running after the restore is back to three offices',
       len(build()['offices']), 3)

    print('[f] group invariants that the exclusion must not have broken')
    eq('every emitted office carries its one current person',
       {i: [e['name'] for e in g['people']] for i, g in offices.items()},
       {MEMBER_OFFICE: ['Ada Bridger'], COMMITTEE: ['Bo Carver'],
        LEADERSHIP: ['Cy McDonald']})
    ok('current_count counts the current people', all(g['current_count'] == 1
                                                      for g in offices.values()))
    ok('a person is listed at most once per group',
       all(len({e['id'] for e in g['people']}) == len(g['people'])
           for g in payload['offices'] + payload['organizations']))
    eq('counts.offices matches what was emitted',
       payload['counts']['offices'], len(payload['offices']))
    eq('counts.organizations matches what was emitted',
       payload['counts']['organizations'], len(payload['organizations']))
    capped = build(people_cap=0)
    ok('the people cap is real and the remainder is counted',
       all(group['people'] == [] and group['more'] == group['people_count']
           for group in capped['offices']), capped['offices'][:1])
    eq('the cap is stated in the payload', capped['people_cap'], 0)
    ok('the note still says a group is not a tie between the people in it',
       'does not establish that two people worked together' in payload['note'],
       payload['note'])

    print('[g] the employment ruling is OFF unless it is handed in')
    # The 8841 sidecar keeps serving while the static artifact it reads is being
    # rebuilt, so the ruling has to be an argument, not an import side effect.
    ok('without an employment map nothing is marked employee or represents',
       payload['counts']['memberships'].get('employee', 0) == 0
       and payload['counts']['memberships'].get('represents', 0) == 0,
       payload['counts']['memberships'])
    eq('and the payload says so outright', payload['employment_ruling_applied'], False)
    eq('every organisation membership is the pre-ruling kind',
       sorted({entry['membership'] for group in payload['organizations']
               for entry in group['people']}), ['listed'])
    ok('an office entry carries no membership or as_of at all -- the ruling is '
       'about lobbying filers and an office has none',
       all(set(entry) == {'id', 'name', 'current', 'title'}
           for group in payload['offices'] for entry in group['people']),
       [entry for group in payload['offices'] for entry in group['people']][:1])
    eq('no LDA organisation appeared out of nowhere',
       sorted(group['id'] for group in payload['organizations']), [ORG])

    print('[h] one employer per filer, and it is the most recent one')
    ruled = build_ruled()
    ruled_orgs = {group['id']: group for group in ruled['organizations']}
    eq('the ruling reports itself applied', ruled['employment_ruling_applied'], True)
    ok('the firm is emitted as an organisation', FIRM in ruled_orgs, sorted(ruled_orgs))
    eq('the firm is named from the ruling, not from the id',
       ruled_orgs[FIRM]['name'], 'HARROW & CO GOVERNMENT AFFAIRS')
    eq('both of the firm\'s filers are its EMPLOYEES',
       sorted(e['name'] for e in ruled_orgs[FIRM]['people']
              if e['membership'] == subject.MEMBERSHIP_EMPLOYEE),
       ['Gil Harrow', 'Ivo Jansen'])
    eq('the firm\'s employee_count says two', ruled_orgs[FIRM]['employee_count'], 2)
    employers = {}
    for group in ruled['organizations']:
        for entry in group['people']:
            if entry['membership'] == subject.MEMBERSHIP_EMPLOYEE:
                employers.setdefault(entry['id'], []).append(group['id'])
    ok('no filer is an employee of two organisations -- that IS the ruling',
       all(len(v) == 1 for v in employers.values()), employers)
    eq('the period the employer is on file as of travels with the person',
       [e['as_of'] for e in ruled_orgs[FIRM]['people'] if e['name'] == 'Gil Harrow'],
       ['2026 Q2'])

    print('[i] a contract filer is tied to every client, and that is not a duplicate')
    for client, name in ((CLIENT_A, 'ALPHA WORKS'), (CLIENT_B, 'BETA HOLDINGS'),
                         (CLIENT_C, 'GAMMA TRUST')):
        ok('%s is listed as an organisation' % name, client in ruled_orgs, sorted(ruled_orgs))
        if client in ruled_orgs:
            eq('%s carries Gil Harrow as a representative, not an employee' % name,
               [(e['name'], e['membership']) for e in ruled_orgs[client]['people']
                if e['name'] == 'Gil Harrow'],
               [('Gil Harrow', subject.MEMBERSHIP_REPRESENTS)])
    represented = [group['id'] for group in ruled['organizations']
                   for entry in group['people']
                   if entry['id'] == CONTRACT_FILER
                   and entry['membership'] == subject.MEMBERSHIP_REPRESENTS]
    eq('the same person appears under all three of their clients',
       sorted(represented), sorted([CLIENT_A, CLIENT_B, CLIENT_C]))
    eq('a client\'s represented_by_count counts them',
       ruled_orgs[CLIENT_A]['represented_by_count'], 2)
    eq('and its employee_count is zero -- a client employs nobody here',
       ruled_orgs[CLIENT_A]['employee_count'], 0)
    eq('the client roster carries the latest period each filer filed for it',
       sorted((e['name'], e['as_of']) for e in ruled_orgs[CLIENT_B]['people']),
       [('Gil Harrow', '2025 Q4')])

    print('[j] an in-house filer has an employer and no clients')
    ok('the in-house shop is an organisation', SHOP in ruled_orgs, sorted(ruled_orgs))
    eq('its filer is an employee of it',
       [(e['name'], e['membership']) for e in ruled_orgs[SHOP]['people']],
       [('Hana Ito', subject.MEMBERSHIP_EMPLOYEE)])
    ok('the in-house filer is on no client roster at all',
       not any(entry['id'] == IN_HOUSE_FILER
               for group in ruled['organizations']
               for entry in group['people']
               if entry['membership'] == subject.MEMBERSHIP_REPRESENTS))

    print('[k] a filer the ruling does not reach is left alone, not guessed at')
    ok('the unruled filer is in no organisation',
       not any(entry['id'] == UNRULED_FILER for group in ruled['organizations']
               for entry in group['people']),
       [group['id'] for group in ruled['organizations']
        for entry in group['people'] if entry['id'] == UNRULED_FILER])
    eq('the payload says the shipped ruling was truncated by its own byte caps',
       ruled['employment_ruling_truncated'], True)
    eq('the membership census adds up',
       ruled['counts']['memberships'][subject.MEMBERSHIP_EMPLOYEE], 3)
    eq('and counts the client ties separately',
       ruled['counts']['memberships'][subject.MEMBERSHIP_REPRESENTS], 4)
    eq('the payload names the three membership kinds in plain words',
       sorted(ruled['membership_kinds']), ['employee', 'listed', 'represents'])
    ok('the note says the same person under many clients is the tie, not a duplicate',
       'not a duplicate' in ruled['employment_note'], ruled['employment_note'])

    print('[l] the offices half is untouched by the ruling')
    eq('the same three offices are emitted with the ruling on as with it off',
       sorted(group['id'] for group in ruled['offices']),
       sorted(group['id'] for group in payload['offices']))
    eq('and they still carry only their own payroll people',
       {g['id']: [e['name'] for e in g['people']] for g in ruled['offices']},
       {g['id']: [e['name'] for e in g['people']] for g in payload['offices']})

    print('\n%d passed, %d failed' % (P, F))
    return 1 if F else 0


if __name__ == '__main__':
    sys.exit(main())
