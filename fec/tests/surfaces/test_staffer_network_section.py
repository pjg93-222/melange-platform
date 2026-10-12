# -*- coding: utf-8 -*-
"""Contract suite for fec/surfaces/build_staffer_network_section.py.

Everything runs against a synthetic generation minted in a temp tree: three
roster members, six people, one person shared across two members, one person
attached to a member who is NOT on the roster, mixed current/departed stints,
and office moves in both directions. The receipt / manifest / verification
receipt are minted exactly the way the shipped adapter expects to find them
(artifact hashes in the receipt's files list, receipt and manifest hashes in the
verification receipt's twin_byte_identity, and a second independent record in
primary_file_integrity), so the producer's hash binding is exercised for real
rather than stubbed.

Plain-assert style, no dependencies:  python fec/tests/surfaces/test_staffer_network_section.py
"""
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
FEC = os.path.dirname(os.path.dirname(HERE))
PRODUCER = os.path.join(FEC, 'surfaces', 'build_staffer_network_section.py')

GENERATION = 'staffer_social_network_test_v9_primary'
VERIFICATION_DIR = 'staffer_social_network_test_v9_verification'
STATEMENT = ('Observed payroll co-tenure only. People who held the same office at '
             'non-overlapping times carry NO edge here. Not identity findings, not '
             'primary weights.')
ZERO_OVERLAP = 4242

P = F = 0


def ok(name, cond, detail=''):
    global P, F
    P, F = (P + 1, F) if cond else (P, F + 1)
    print(('  OK   ' if cond else 'FAIL  ') + name + (('  :: ' + str(detail)) if (detail and not cond) else ''))


def eq(name, actual, expected):
    ok(name, actual == expected, 'got %r, expected %r' % (actual, expected))


# ------------------------------------------------------------------- fixture

ROSTER = [
    {'member': 'Alpha Member', 'bioguide': 'A000001', 'chamber': 'House'},
    {'member': 'Bravo Member', 'bioguide': 'B000002', 'chamber': 'Senate'},
    {'member': 'Charlie Member', 'bioguide': 'C000003', 'chamber': 'House'},
]

# key, member bioguides, pagerank@0.85, co_tenure_degree, eigen, bridging, revolving, component, size
PEOPLE = [
    ('p1', ['A000001'], 0.9, 50, 0.50, 0.10, 1, 0, 5),
    ('p2', ['A000001'], 0.3, 10, 0.20, 0.20, 0, 0, 5),
    ('p3', ['A000001', 'B000002'], 0.3, 20, 0.30, 0.80, 2, 0, 5),
    ('p4', ['B000002'], 0.7, 30, 0.40, 0.30, 0, 0, 5),
    ('p5', ['A000001', 'Z000009'], 0.5, 40, 0.60, 0.70, 3, 0, 5),
    ('p6', [], 0.1, 1, 0.05, 0.00, 0, 1, 1),
]

STINTS = {
    'p1': [('A000001', '2023-01-01', '2024-01-01', 'current', 'CHIEF OF STAFF')],
    'p2': [('A000001', '2019-01-01', '2020-01-01', 'departed', 'STAFF ASSISTANT')],
    'p3': [('A000001', '2021-01-01', '2022-01-01', 'current', 'LEGISLATIVE DIRECTOR'),
           ('B000002', '2018-01-01', '2019-01-01', 'departed', 'LEGISLATIVE ASSISTANT')],
    'p4': [('B000002', '2024-01-01', None, 'current', 'DISTRICT DIRECTOR')],
    'p5': [('A000001', None, None, 'current', 'CHIEF OF STAFF')],
    'p6': [(None, '2015-01-01', '2016-01-01', 'departed', 'PAGE')],
}

MOVES = [
    ('container:office:house:ADMIN', 'admin', None,
     'container:office:house:HON:A000001', 'member_office', 'A000001', 5),
    ('container:office:house:HON:A000001', 'member_office', 'A000001',
     'container:office:senate:HON:B000002', 'member_office', 'B000002', 3),
    ('container:office:senate:HON:B000002', 'member_office', 'B000002',
     'container:office:senate:QUIT', 'admin', None, 2),
]


def spine_id(key):
    return 'hill:person:' + key


def node_id(key):
    """The node scores key people as "person:" + spine_person_id; the join between
    the two artifacts is exactly this string, so the fixture must reproduce it."""
    return 'person:' + spine_id(key)


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


def node_score_rows(drop_field=None, shape='v5'):
    """The generation's node scores, in one of the two shapes that exist.

    v5   heterogeneous: person rows carry bridging_diversity_exploratory and
         revolving_degree, and the container is a row in this same file.
    v6   PERSONS ONLY (D-022): the diversity field is office_diversity_
         exploratory, the per-person revolving column is registrant_organization
         _group_count, and there is no container row here at all -- containers
         moved to staffer_social_groups.ndjson.gz.

    The producer must read both, so both are minted here and driven end to end.
    """
    rows = []
    for key, bios, pagerank, co_tenure, eigen, bridging, revolving, component, size in PEOPLE:
        row = {
            'node_id': node_id(key), 'node_kind': 'person', 'identity_tier': 'S1',
            'member_bioguides': list(bios), 'attached_member_bioguides': list(bios),
            'attached_container_ids': ['container:office:house:HON:' + b for b in bios],
            'chambers': ['house'], 'co_tenure_degree': co_tenure,
            'component_id': component, 'component_size': size, 'isolate': False,
            'connectedness_pagerank_exploratory': [{'alpha': 0.7, 'score': pagerank * 0.9},
                                                   {'alpha': 0.85, 'score': pagerank},
                                                   {'alpha': 0.95, 'score': pagerank * 1.1}],
            'eigenvector_component_local_exploratory': eigen,
            'quorum_staff_id': None, 'lda_source_person_id': None,
            'review_status': 'REVIEW_REQUIRED', 'exploratory': True,
        }
        if shape == 'v6':
            row['office_diversity_exploratory'] = bridging
            row['office_diversity_distinct_offices'] = len(bios)
            row['registrant_organization_group_count'] = revolving
            row['policy_area_group_count'] = 0
            row['attached_container_count'] = len(bios)
        else:
            row['bridging_diversity_exploratory'] = bridging
            row['revolving_degree'] = revolving
            row['membership_degree'] = len(bios)
            row['policy_area_degree'] = 0
        if drop_field and key == 'p1':
            row.pop(drop_field, None)
        rows.append(row)
    if shape != 'v6':
        # revolving_degree on the CONTAINER row is what office_revolving_edge_count
        # sums.  It is non-zero here on purpose: a v6 generation has no container
        # row at all and must report null, and a null-vs-zero contract cannot be
        # tested against a fixture whose real answer is zero either way.
        rows.append({'node_id': 'container:office:house:HON:A000001', 'node_kind': 'container',
                     'container_kind': 'member_office', 'member_bioguide': 'A000001',
                     'revolving_degree': 4, 'component_id': 0, 'component_size': 5})
    return rows


# The receipt block that carries the policy-evidence control, one per shape.
# v5 ranking_controls is a counterfactual rebuild reported as a top-100 retention
# band; v6 policy_link_pagerank_control is one descriptive ratio on the published
# persons-only operator.  Different measurements, one meta block on the payload,
# and meta.pagerank_policy_control_source has to say which one it was.
RANKING_CONTROLS_V5 = {
    'people_with_policy_evidence': 2,
    'published_top100_people_with_policy_evidence': 1,
    'semantics': 'counterfactual rebuilds over the kind-balanced operator',
    'scenarios': [{'scenario': 'uniform_teleport', 'top100_person_overlap_with_published': 91},
                  {'scenario': 'no_policy_nodes', 'top100_person_overlap_with_published': 88}],
}
POLICY_LINK_CONTROL_V6 = {
    'question': 'after removing policy areas from the operator, do people with a Politico '
                'policy link still score differently from everyone else?',
    'people_with_policy_evidence': 2,
    'people_without_policy_evidence': 4,
    'mean_pagerank_085_with_policy_evidence': 0.0008,
    'mean_pagerank_085_without_policy_evidence': 0.0004,
    'ratio_with_over_without': 2.0,
    'policy_edges_in_operator': 0,
    'semantics': 'A descriptive comparison on the PUBLISHED persons-only operator. It is NOT a '
                 'counterfactual rebuild and NOT a calibration.',
}


def trajectory_rows(break_join=False):
    rows = []
    for key, _bios, _pr, _ct, _ei, _bd, _rv, _cid, _sz in PEOPLE:
        stints = []
        for bioguide, start, end, status, title in STINTS[key]:
            stints.append({'container_id': 'container:office:house:HON:' + (bioguide or 'ADMIN'),
                           'container_kind': 'member_office' if bioguide else 'admin',
                           'member_bioguide': bioguide, 'core_start': start,
                           'core_end_exclusive': end, 'status': status, 'title_last': title,
                           'chamber': 'house', 'periods_observed': 2,
                           'date_precision': 'DAY' if start else 'REPORT_PERIOD'})
        identifier = ('unjoinable:' + key) if (break_join and key == 'p1') else spine_id(key)
        rows.append({'spine_person_id': identifier, 'stints': stints, 'moves': [],
                     'stint_count': len(stints), 'move_count': 0, 'identity_tier': 'S1',
                     'member_bioguides': [s[0] for s in STINTS[key] if s[0]],
                     'review_status': 'REVIEW_REQUIRED'})
    return rows


def move_rows():
    return [{'from_container_id': fc, 'from_container_kind': fk, 'from_member_bioguide': fb,
             'to_container_id': tc, 'to_container_kind': tk, 'to_member_bioguide': tb,
             'people': people, 'direction': 'directed', 'review_status': 'REVIEW_REQUIRED'}
            for fc, fk, fb, tc, tk, tb, people in MOVES]


SPINE_DIR = 'hill_personnel_spine_test_v9_primary'
SPINE_NAME = 'hill_person_spine.ndjson.gz'

# payee_key as the real spine stores it: already parsed into three fields and
# CASEFOLDED.  p3 is the regression that decides how display_name_for is allowed to
# be implemented -- a middle initial of "v" is the first letter of VICTOR and also a
# valid Roman-numeral suffix token, so re-serialising to "LAST, FIRST M" and
# re-parsing loses it.  p4 has no name parts at all.
SPINE_KEYS = {
    'p1': {'first': 'ada', 'last': 'bridger', 'middle_initial': None},
    'p2': {'first': 'elizabeth', 'last': 'mcdonald', 'middle_initial': 'e'},
    'p3': {'first': 'james', 'last': 'daly-jr', 'middle_initial': 'v'},
    'p4': {'first': None, 'last': None, 'middle_initial': None},
}


def spine_rows():
    return [{'spine_person_id': spine_id(key), 'identity_tier': 'S1',
             'payee_key': SPINE_KEYS.get(key, {'first': None, 'last': None,
                                               'middle_initial': None}),
             'lda_source_person_id': None}
            for key, *_rest in PEOPLE]


def make_tree(root, verification_status='PASS', failures=(), promotions=0,
              drop_field=None, tamper=None, break_receipt_hash=False, break_join=False,
              spine=False, shape='v5', groups=None):
    """Mint a complete synthetic generation plus the roster the kernel loads.

    ``spine`` mints the display-name spine and binds it as a receipt INPUT, which
    is what switches the producer's display-name join on.  It stays off by
    default so the no-spine contract (name_policy says names are unavailable, and
    top_staff carries no name key) keeps being tested as it was.

    ``groups`` mints staffer_social_groups.ndjson.gz (ruling 1, RELABEL,
    2026-09-07) -- id/kind/name/people_count rows -- and binds it as a receipt
    FILE (it is emitted by the same generation, unlike the spine). Left absent by
    default so the v5 fallback path (container_display_name / trajectories'
    distinct-attachment count) keeps being tested as it was."""
    os.makedirs(os.path.join(root, 'data', 'engine'), exist_ok=True)
    os.makedirs(os.path.join(root, 'fec'), exist_ok=True)
    with open(os.path.join(root, 'data', 'engine', 'congestion.json'), 'w', encoding='utf-8') as s:
        json.dump(ROSTER, s)
    with open(os.path.join(root, 'fec', 'legislators.csv'), 'w', encoding='utf-8', newline='') as s:
        s.write('bioguide_id,state,party\n')
        s.write('A000001,MN,Democrat\n')
        s.write('B000002,ID,Republican\n')

    base = os.path.join(root, 'audits', 'staffer_network', GENERATION)
    verification_base = os.path.join(root, 'audits', 'staffer_network', VERIFICATION_DIR)
    os.makedirs(base, exist_ok=True)
    os.makedirs(verification_base, exist_ok=True)

    artifacts = (
        ('staffer_social_node_scores', 'staffer_social_node_scores.ndjson.gz',
         node_score_rows(drop_field, shape)),
        ('staffer_trajectories', 'staffer_trajectories.ndjson.gz', trajectory_rows(break_join)),
        ('staffer_office_moves', 'staffer_office_moves.ndjson.gz', move_rows()),
    )
    files = []
    for role, name, records in artifacts:
        path = os.path.join(base, name)
        write_ndjson_gz(path, records)
        files.append({'role': role, 'path': name, 'records': len(records),
                      'bytes': os.path.getsize(path), 'sha256': sha256(path)})
    files.append({'role': 'staffer_social_edges', 'path': 'staffer_social_edges.ndjson.gz',
                  'records': 0, 'bytes': 0, 'sha256': '0' * 64})
    if groups is not None:
        groups_path = os.path.join(base, 'staffer_social_groups.ndjson.gz')
        write_ndjson_gz(groups_path, groups)
        # ROLE, not filename stem.  build_staffer_social_network.py's
        # file_specs list emits this artifact under the role "groups"
        # (audits/staffer_network/build_staffer_social_network.py:3350);
        # "staffer_social_groups" is only ever its FILENAME.  Minting the
        # stem here made the fixture agree with a producer bug that looked
        # the entry up by the stem, so [i4] passed without ever exercising
        # the real contract.  Keep this string equal to the builder's.
        files.append({'role': 'groups', 'path': 'staffer_social_groups.ndjson.gz',
                      'records': len(groups), 'bytes': os.path.getsize(groups_path),
                      'sha256': sha256(groups_path)})

    receipt_inputs = []
    if spine:
        spine_base = os.path.join(root, 'audits', 'staffer_network', SPINE_DIR)
        os.makedirs(spine_base, exist_ok=True)
        spine_path = os.path.join(spine_base, SPINE_NAME)
        write_ndjson_gz(spine_path, spine_rows())
        receipt_inputs.append({'role': 'hill_person_spine', 'path': spine_path,
                               'bytes': os.path.getsize(spine_path),
                               'sha256': sha256(spine_path)})

    receipt = {
        'inputs': receipt_inputs,
        'schema_id': 'staffer-network-social-receipt-v1',
        'rule_version': 'staffer-social-network-test',
        'generated_at': '2026-09-03T00:00:00Z',
        'review_status': 'REVIEW_REQUIRED', 'exploratory': True, 'primary': False,
        'canonical_eligible': False, 'canonical_employment_promotions': promotions,
        'canonical_identity_promotions': 0, 'primary_weights_emitted': 0,
        'statement': STATEMENT,
        'node_universe': {'person': 6, 'container': 1, 'total': 7},
        'edge_counts': {'co_tenure_guaranteed': 11, 'membership': 7,
                        'co_tenure_zero_overlap_REJECTED_never_emitted': ZERO_OVERLAP,
                        'total_rows_emitted': 18},
        'overlap_rule_check': {'pairs_REJECTED_for_no_overlap': ZERO_OVERLAP,
                               'rule': 'no person-person edge without actual temporal overlap'},
        'files': files,
    }
    if shape == 'v6':
        receipt['policy_link_pagerank_control'] = POLICY_LINK_CONTROL_V6
        receipt['node_universe'] = {'person': 6, 'total': 6,
                                    'note': 'the node universe IS the person universe (D-022)'}
        receipt['group_universe'] = {'container': 1, 'organization': 0, 'policy_area': 0,
                                     'total': 1}
    else:
        receipt['ranking_controls'] = RANKING_CONTROLS_V5
    receipt_path = os.path.join(base, 'staffer_social_network_receipt.json')
    with open(receipt_path, 'w', encoding='utf-8') as stream:
        json.dump(receipt, stream, sort_keys=True)
    manifest_path = os.path.join(base, 'staffer_social_network_manifest.json')
    with open(manifest_path, 'w', encoding='utf-8') as stream:
        json.dump({'generation_id': GENERATION, 'schema_id': 'staffer-network-manifest-v1'},
                  stream, sort_keys=True)

    receipt_sha = sha256(receipt_path)
    verification = {
        'schema_id': 'staffer-network-social-verification-receipt-v1',
        'status': verification_status, 'failures': list(failures),
        'twin_byte_identity': [
            {'path': 'staffer_social_network_receipt.json',
             'primary_sha256': ('f' * 64) if break_receipt_hash else receipt_sha,
             'twin_sha256': receipt_sha, 'identical': True},
            {'path': 'staffer_social_network_manifest.json',
             'primary_sha256': sha256(manifest_path), 'twin_sha256': sha256(manifest_path),
             'identical': True, 'note': 'generation_id differs by design'},
        ],
        'primary_file_integrity': [
            {'path': entry['path'], 'receipt_sha256': entry['sha256'],
             'recomputed_sha256': entry['sha256'], 'match': True}
            for entry in files if entry['role'] != 'staffer_social_edges'
        ],
        'promotion_tripwire': {'receipt_canonical_employment_promotions': promotions},
    }
    with open(os.path.join(verification_base, 'staffer_social_network_verification_receipt.json'),
              'w', encoding='utf-8') as stream:
        json.dump(verification, stream, sort_keys=True)

    if tamper:
        with open(os.path.join(base, tamper), 'ab') as stream:
            stream.write(b'\x00')
    return base


def make_shared_employee_tree(root):
    """A separate, minimal generation for ruling 3's own fixture requirement: 'a
    fixture member whose highest-ranked person is a SHARED EMPLOYEE must not list
    them in top_staff and must count them.' Kept apart from the shared PEOPLE/
    STINTS fixture above so this scenario cannot perturb [a]'s many position-
    indexed assertions.

    Also exercises the office_key-direct-name path container_display_name() takes
    for a non-member-office container: x and y's shared container is a COMMITTEE
    carrying its own office_key ("JUDICIARY"), unlike every container in the
    shared fixture above, which only ever exercises the roster/HON: fallback.
    """
    os.makedirs(os.path.join(root, 'data', 'engine'), exist_ok=True)
    os.makedirs(os.path.join(root, 'fec'), exist_ok=True)
    with open(os.path.join(root, 'data', 'engine', 'congestion.json'), 'w', encoding='utf-8') as s:
        json.dump([{'member': 'Delta Member', 'bioguide': 'D000004', 'chamber': 'House'}], s)
    with open(os.path.join(root, 'fec', 'legislators.csv'), 'w', encoding='utf-8', newline='') as s:
        s.write('bioguide_id,state,party\nD000004,NY,Democrat\n')

    base = os.path.join(root, 'audits', 'staffer_network', GENERATION)
    verification_base = os.path.join(root, 'audits', 'staffer_network', VERIFICATION_DIR)
    os.makedirs(base, exist_ok=True)
    os.makedirs(verification_base, exist_ok=True)

    committee = 'container:office:senate:JUDICIARY'
    # x is the higher-pagerank, higher-co_tenure_degree person -- the one that
    # WOULD be top_staff[0] -- but carries the excluded title on its one stint.
    node_scores = [
        {'node_id': node_id('x'), 'node_kind': 'person', 'identity_tier': 'S1',
         'member_bioguides': ['D000004'], 'attached_member_bioguides': ['D000004'],
         'co_tenure_degree': 5, 'component_id': 0, 'component_size': 2,
         'connectedness_pagerank_exploratory': [{'alpha': 0.85, 'score': 0.9}],
         'eigenvector_component_local_exploratory': 0.5,
         'bridging_diversity_exploratory': 0.1, 'revolving_degree': 0},
        {'node_id': node_id('y'), 'node_kind': 'person', 'identity_tier': 'S1',
         'member_bioguides': ['D000004'], 'attached_member_bioguides': ['D000004'],
         'co_tenure_degree': 2, 'component_id': 0, 'component_size': 2,
         'connectedness_pagerank_exploratory': [{'alpha': 0.85, 'score': 0.4}],
         'eigenvector_component_local_exploratory': 0.3,
         'bridging_diversity_exploratory': 0.1, 'revolving_degree': 0},
        {'node_id': committee, 'node_kind': 'container', 'container_kind': 'committee',
         'office_key': 'JUDICIARY', 'member_bioguide': None, 'revolving_degree': 0,
         'component_id': 0, 'component_size': 2},
    ]
    trajectories = [
        {'spine_person_id': spine_id('x'), 'moves': [], 'stints': [
            {'container_id': committee, 'container_kind': 'committee', 'member_bioguide': 'D000004',
             'core_start': '2020-01-01', 'core_end_exclusive': '2024-01-01',
             'status': 'current', 'title_last': 'SHARED EMPLOYEE'}]},
        {'spine_person_id': spine_id('y'), 'moves': [], 'stints': [
            {'container_id': committee, 'container_kind': 'committee', 'member_bioguide': 'D000004',
             'core_start': '2021-01-01', 'core_end_exclusive': '2022-01-01',
             'status': 'departed', 'title_last': 'LEGISLATIVE AIDE'},
            {'container_id': 'container:office:house:HON:D000004', 'container_kind': 'member_office',
             'member_bioguide': 'D000004', 'core_start': '2018-01-01', 'core_end_exclusive': '2019-01-01',
             'status': 'departed', 'title_last': 'LEGISLATIVE AIDE'}]},
    ]
    files = []
    for role, name, records in (
        ('staffer_social_node_scores', 'staffer_social_node_scores.ndjson.gz', node_scores),
        ('staffer_trajectories', 'staffer_trajectories.ndjson.gz', trajectories),
        ('staffer_office_moves', 'staffer_office_moves.ndjson.gz', []),
    ):
        path = os.path.join(base, name)
        write_ndjson_gz(path, records)
        files.append({'role': role, 'path': name, 'records': len(records),
                      'bytes': os.path.getsize(path), 'sha256': sha256(path)})
    files.append({'role': 'staffer_social_edges', 'path': 'staffer_social_edges.ndjson.gz',
                  'records': 0, 'bytes': 0, 'sha256': '0' * 64})

    receipt = {
        'inputs': [], 'schema_id': 'staffer-network-social-receipt-v1',
        'rule_version': 'staffer-social-network-test-shared',
        'generated_at': '2026-09-07T00:00:00Z',
        'review_status': 'REVIEW_REQUIRED', 'exploratory': True, 'primary': False,
        'canonical_eligible': False, 'canonical_employment_promotions': 0,
        'canonical_identity_promotions': 0, 'primary_weights_emitted': 0,
        'statement': STATEMENT,
        'node_universe': {'person': 2, 'container': 1, 'total': 3},
        'edge_counts': {'co_tenure_guaranteed': 0, 'membership': 2,
                        'co_tenure_zero_overlap_REJECTED_never_emitted': 0,
                        'total_rows_emitted': 2},
        'overlap_rule_check': {'pairs_REJECTED_for_no_overlap': 0,
                               'rule': 'no person-person edge without actual temporal overlap'},
        'ranking_controls': RANKING_CONTROLS_V5,
        'files': files,
    }
    receipt_path = os.path.join(base, 'staffer_social_network_receipt.json')
    with open(receipt_path, 'w', encoding='utf-8') as stream:
        json.dump(receipt, stream, sort_keys=True)
    manifest_path = os.path.join(base, 'staffer_social_network_manifest.json')
    with open(manifest_path, 'w', encoding='utf-8') as stream:
        json.dump({'generation_id': GENERATION, 'schema_id': 'staffer-network-manifest-v1'},
                  stream, sort_keys=True)

    receipt_sha = sha256(receipt_path)
    verification = {
        'schema_id': 'staffer-network-social-verification-receipt-v1',
        'status': 'PASS', 'failures': [],
        'twin_byte_identity': [
            {'path': 'staffer_social_network_receipt.json', 'primary_sha256': receipt_sha,
             'twin_sha256': receipt_sha, 'identical': True},
            {'path': 'staffer_social_network_manifest.json',
             'primary_sha256': sha256(manifest_path), 'twin_sha256': sha256(manifest_path),
             'identical': True},
        ],
        'primary_file_integrity': [
            {'path': entry['path'], 'receipt_sha256': entry['sha256'],
             'recomputed_sha256': entry['sha256'], 'match': True}
            for entry in files if entry['role'] != 'staffer_social_edges'
        ],
        'promotion_tripwire': {'receipt_canonical_employment_promotions': 0},
    }
    with open(os.path.join(verification_base, 'staffer_social_network_verification_receipt.json'),
             'w', encoding='utf-8') as stream:
        json.dump(verification, stream, sort_keys=True)
    return base


HON_LEDGER_GENERATION = 'house_personnel_ledger_test_v9_primary'


def make_office_label_tree(root, ledger=True, politico=True):
    """The owner ruling of 2026-09-07 on the office label, on its own fixture.

    "It should be for most recent office or whatever committee they belong too,
    payroll takes precedent, then politico." Every branch of that sentence, plus
    the naming rule that stops a container id reaching a reader, gets one person:

      r1  a LONG old stint and a SHORT recent one -- recency wins, which is the
          whole ruling; the old rule (most days) would name the other office
      r2  most recent office belongs to a member off the current roster -- named
          from the House personnel ledger's own printed string
      r3  most recent office is a vacant-seat header (HON:11TH|JERSEY) -- named by
          nothing, so the cell says so rather than printing the key
      r4  most recent office is a committee -- the record's own office_key
      r5  no dated payroll stint, but a Politico current-committee observation
      r6  no dated payroll stint and no Politico row -- honest absence
      r7  office key spells the member out (HON:SAM|RAYBURN)
      r8  two offices tie on the same last covered day -- the current one wins

    r1 also carries a Politico current-committee row for a DIFFERENT office, which
    must lose to payroll, and r6 carries a politico_employment_history row, which
    is not an admissible layer and must not name anything.

    The same tree also carries office MOVES, because the move tables name their
    offices in the browser and the same ruling has to reach them. Foxtrot Member
    exists only so a move can have a second ROSTER office on one side of it: that
    is the one case the page can already name for itself and the producer must not
    spend bytes on.
    """
    os.makedirs(os.path.join(root, 'data', 'engine'), exist_ok=True)
    os.makedirs(os.path.join(root, 'fec'), exist_ok=True)
    with open(os.path.join(root, 'data', 'engine', 'congestion.json'), 'w', encoding='utf-8') as s:
        json.dump([{'member': 'Echo Member', 'bioguide': 'E000005', 'chamber': 'House'},
                   {'member': 'Foxtrot Member', 'bioguide': 'F000006', 'chamber': 'House'}], s)
    with open(os.path.join(root, 'fec', 'legislators.csv'), 'w', encoding='utf-8', newline='') as s:
        s.write('bioguide_id,state,party\nE000005,TX,Republican\nF000006,OH,Democrat\n')

    base = os.path.join(root, 'audits', 'staffer_network', GENERATION)
    verification_base = os.path.join(root, 'audits', 'staffer_network', VERIFICATION_DIR)
    os.makedirs(base, exist_ok=True)
    os.makedirs(verification_base, exist_ok=True)

    echo = 'container:office:house:HON:E000005'
    foxtrot = 'container:office:house:HON:F000006'
    offroster = 'container:office:house:HON:Z000099'
    vacant = 'container:office:house:HON:11TH|JERSEY'
    committee = 'container:office:senate:APPROPRIATIONS'
    spelled = 'container:office:house:HON:SAM|RAYBURN'

    def person(key, pagerank):
        return {'node_id': node_id(key), 'node_kind': 'person', 'identity_tier': 'S1',
                'member_bioguides': ['E000005'], 'attached_member_bioguides': ['E000005'],
                'co_tenure_degree': 3, 'component_id': 0, 'component_size': 8,
                'connectedness_pagerank_exploratory': [{'alpha': 0.85, 'score': pagerank}],
                'eigenvector_component_local_exploratory': 0.5,
                'bridging_diversity_exploratory': 0.2, 'revolving_degree': 0}

    def container(ident, key, bioguide, kind='member_office'):
        return {'node_id': ident, 'node_kind': 'container', 'container_kind': kind,
                'office_key': key, 'member_bioguide': bioguide, 'revolving_degree': 0,
                'component_id': 0, 'component_size': 8}

    node_scores = [person(k, score) for k, score in (
        ('r1', 0.90), ('r2', 0.80), ('r3', 0.70), ('r4', 0.60),
        ('r5', 0.50), ('r6', 0.40), ('r7', 0.30), ('r8', 0.20))]
    node_scores += [
        container(echo, 'HON:E000005', 'E000005'),
        container(foxtrot, 'HON:F000006', 'F000006'),
        container(offroster, 'HON:Z000099', 'Z000099'),
        container(vacant, 'HON:11TH|JERSEY', None),
        container(committee, 'APPROPRIATIONS', None, 'committee'),
        container(spelled, 'HON:SAM|RAYBURN', None),
    ]

    # One move per naming case, so every branch of the move-label rule is a row
    # somebody can point at. `people` is distinct per row so a mis-keyed map shows
    # up as a wrong number rather than as a coincidence.
    def move(people, src, src_kind, src_bio, dst, dst_kind, dst_bio):
        return {'people': people, 'from_container_id': src,
                'from_container_kind': src_kind, 'from_member_bioguide': src_bio,
                'to_container_id': dst, 'to_container_kind': dst_kind,
                'to_member_bioguide': dst_bio}

    office_moves = [
        # off the roster -> named from the House personnel ledger (map entry)
        move(5, offroster, 'member_office', 'Z000099', echo, 'member_office', 'E000005'),
        # a vacant-seat header -> named by nothing (absent from the map)
        move(4, vacant, 'member_office', None, echo, 'member_office', 'E000005'),
        # the key spells the member out -> the name standard (map entry)
        move(3, spelled, 'member_office', None, echo, 'member_office', 'E000005'),
        # a committee -> the record's own office string, printed by the page itself
        move(2, echo, 'member_office', 'E000005', committee, 'committee', None),
        # a roster office on both sides -> the page already knows both names
        move(1, foxtrot, 'member_office', 'F000006', echo, 'member_office', 'E000005'),
    ]

    def stint(ident, start, end, status, title='LEGISLATIVE AIDE', bioguide='E000005'):
        return {'container_id': ident, 'container_kind': 'member_office',
                'member_bioguide': bioguide, 'core_start': start,
                'core_end_exclusive': end, 'status': status, 'title_last': title,
                'chamber': 'house', 'periods_observed': 1, 'date_precision': 'DAY'}

    trajectories = [
        {'spine_person_id': spine_id('r1'), 'moves': [], 'stints': [
            stint(offroster, '2010-01-01', '2020-01-01', 'departed', bioguide='Z000099'),
            stint(echo, '2025-01-01', '2025-04-01', 'current')]},
        {'spine_person_id': spine_id('r2'), 'moves': [], 'stints': [
            stint(echo, '2015-01-01', '2016-01-01', 'departed'),
            stint(offroster, '2024-01-01', '2026-01-01', 'current', bioguide='Z000099')]},
        {'spine_person_id': spine_id('r3'), 'moves': [], 'stints': [
            stint(echo, '2015-01-01', '2016-01-01', 'departed'),
            stint(vacant, '2023-01-01', '2024-01-01', 'departed', bioguide=None)]},
        {'spine_person_id': spine_id('r4'), 'moves': [], 'stints': [
            stint(echo, '2015-01-01', '2016-01-01', 'departed'),
            stint(committee, '2024-01-01', '2025-01-01', 'current', bioguide=None)]},
        {'spine_person_id': spine_id('r5'), 'moves': [], 'stints': [
            stint(echo, None, None, 'current')]},
        {'spine_person_id': spine_id('r6'), 'moves': [], 'stints': [
            stint(echo, None, None, 'current')]},
        {'spine_person_id': spine_id('r7'), 'moves': [], 'stints': [
            stint(echo, '2015-01-01', '2016-01-01', 'departed'),
            stint(spelled, '2024-01-01', '2025-01-01', 'departed', bioguide=None)]},
        {'spine_person_id': spine_id('r8'), 'moves': [], 'stints': [
            stint(committee, '2024-06-01', '2025-01-01', 'departed', bioguide=None),
            stint(echo, '2024-01-01', '2025-01-01', 'current')]},
    ]

    politico_rows = [
        # r5: the only person this may name -- no dated payroll stint anywhere.
        {'layer': 'politico_current_committee', 'spine_person_id': spine_id('r5'),
         'object_id': committee, 'object_kind': 'container',
         'mapping_status': 'EXACT_PAYROLL_CONTAINER', 'observed_at': '2026-09-03T20:07:33.011Z',
         'role': 'Policy Adviser', 'review_status': 'REVIEW_REQUIRED', 'exploratory': True},
        # r1: payroll already places r1, so this must lose.
        {'layer': 'politico_current_committee', 'spine_person_id': spine_id('r1'),
         'object_id': committee, 'object_kind': 'container',
         'mapping_status': 'EXACT_PAYROLL_CONTAINER', 'observed_at': '2026-09-03T20:07:33.011Z',
         'role': 'Policy Adviser', 'review_status': 'REVIEW_REQUIRED', 'exploratory': True},
        # r6: an inadmissible layer -- not a container, dates not promotable.
        {'layer': 'politico_employment_history', 'spine_person_id': spine_id('r6'),
         'object_id': 'politico-employer:a162afdc', 'object_kind': 'source_employer',
         'date_text': 'May 2008 - Dec 2014', 'observed_at': '2026-09-05T16:12:39.890187Z',
         'temporal_scope': 'SOURCE_TEXT_ONLY_NO_INTERVAL_PROMOTION',
         'review_status': 'REVIEW_REQUIRED', 'exploratory': True},
        # r6 again, as a NEGATIVE CONTROL: the right layer, pointing at a real
        # container, but the layer's own mapping_status does not claim it matched
        # that container. Refused, and counted as refused.
        {'layer': 'politico_current_committee', 'spine_person_id': spine_id('r6'),
         'object_id': spelled, 'object_kind': 'container',
         'mapping_status': 'NO_MATCHED_CURRENT_COMMITTEE',
         'observed_at': '2026-09-04T00:00:00.000Z', 'role': 'Unmatched',
         'review_status': 'REVIEW_REQUIRED', 'exploratory': True},
    ]

    artifacts = [
        ('staffer_social_node_scores', 'staffer_social_node_scores.ndjson.gz', node_scores),
        ('staffer_trajectories', 'staffer_trajectories.ndjson.gz', trajectories),
        ('staffer_office_moves', 'staffer_office_moves.ndjson.gz', office_moves),
    ]
    if politico:
        artifacts.append(('staffer_social_politico_review_layers',
                          'staffer_social_politico_review_layers.ndjson.gz', politico_rows))
    files = []
    for role, name, records in artifacts:
        path = os.path.join(base, name)
        write_ndjson_gz(path, records)
        files.append({'role': role, 'path': name, 'records': len(records),
                      'bytes': os.path.getsize(path), 'sha256': sha256(path)})
    files.append({'role': 'staffer_social_edges', 'path': 'staffer_social_edges.ndjson.gz',
                  'records': 0, 'bytes': 0, 'sha256': '0' * 64})

    receipt = {
        'inputs': [], 'schema_id': 'staffer-network-social-receipt-v1',
        'rule_version': 'staffer-social-network-test-office-label',
        'generated_at': '2026-09-07T00:00:00Z',
        'review_status': 'REVIEW_REQUIRED', 'exploratory': True, 'primary': False,
        'canonical_eligible': False, 'canonical_employment_promotions': 0,
        'canonical_identity_promotions': 0, 'primary_weights_emitted': 0,
        'statement': STATEMENT,
        'node_universe': {'person': 8, 'container': 6, 'total': 14},
        'edge_counts': {'co_tenure_guaranteed': 0, 'membership': 8,
                        'co_tenure_zero_overlap_REJECTED_never_emitted': 0,
                        'total_rows_emitted': 8},
        'overlap_rule_check': {'pairs_REJECTED_for_no_overlap': 0,
                               'rule': 'no person-person edge without actual temporal overlap'},
        'ranking_controls': RANKING_CONTROLS_V5,
        'files': files,
    }
    receipt_path = os.path.join(base, 'staffer_social_network_receipt.json')
    with open(receipt_path, 'w', encoding='utf-8') as stream:
        json.dump(receipt, stream, sort_keys=True)
    manifest_path = os.path.join(base, 'staffer_social_network_manifest.json')
    with open(manifest_path, 'w', encoding='utf-8') as stream:
        json.dump({'generation_id': GENERATION, 'schema_id': 'staffer-network-manifest-v1'},
                  stream, sort_keys=True)
    receipt_sha = sha256(receipt_path)
    verification = {
        'schema_id': 'staffer-network-social-verification-receipt-v1',
        'status': 'PASS', 'failures': [],
        'twin_byte_identity': [
            {'path': 'staffer_social_network_receipt.json', 'primary_sha256': receipt_sha,
             'twin_sha256': receipt_sha, 'identical': True},
            {'path': 'staffer_social_network_manifest.json',
             'primary_sha256': sha256(manifest_path), 'twin_sha256': sha256(manifest_path),
             'identical': True},
        ],
        'primary_file_integrity': [
            {'path': entry['path'], 'receipt_sha256': entry['sha256'],
             'recomputed_sha256': entry['sha256'], 'match': True}
            for entry in files if entry['role'] != 'staffer_social_edges'
        ],
        'promotion_tripwire': {'receipt_canonical_employment_promotions': 0},
    }
    with open(os.path.join(verification_base, 'staffer_social_network_verification_receipt.json'),
              'w', encoding='utf-8') as stream:
        json.dump(verification, stream, sort_keys=True)

    if ledger:
        ledger_base = os.path.join(root, 'audits', 'staffer_network', HON_LEDGER_GENERATION)
        os.makedirs(ledger_base, exist_ok=True)
        ledger_path = os.path.join(ledger_base, 'house_member_quarters.ndjson.gz')
        write_ndjson_gz(ledger_path, [
            # The MOST RECENT printed string wins over the older one.
            {'member_bioguide': 'Z000099', 'quarter': '2019Q1',
             'office_family_normalized': 'HON. ZELDA OLDNAME'},
            {'member_bioguide': 'Z000099', 'quarter': '2021Q3',
             'office_family_normalized': 'HON. ZELDA QUINCE'},
            # Not a HON. string, so it names nothing.
            {'member_bioguide': 'Y000098', 'quarter': '2021Q3',
             'office_family_normalized': 'COMMITTEE ON RULES'},
        ])
        with open(os.path.join(ledger_base, 'house_personnel_ledger_receipt.json'),
                  'w', encoding='utf-8') as stream:
            json.dump({'status': 'PASS', 'outputs': [
                {'path': 'house_member_quarters.ndjson.gz',
                 'bytes': os.path.getsize(ledger_path), 'sha256': sha256(ledger_path)}]},
                stream, sort_keys=True)
    return base


# A healthy producer run on these fixtures takes 1-2 s, 9 s at worst with six running at once.
# On 2026-09-19 one froze inside `import scipy` for 14 minutes and, with no timeout here, stalled
# the whole kernel run without a line of output. 180 s is twenty times the slowest healthy run.
PRODUCER_TIMEOUT_SECONDS = 180
PRODUCER_TIMED_OUT = 124


def run(root, out, extra=(), epoch='1750000000'):
    env = dict(os.environ)
    env.pop('MONEYBALL_ROOT', None)
    env['SOURCE_DATE_EPOCH'] = epoch
    argv = [sys.executable, PRODUCER, '--root', root, '--generation', GENERATION,
            '--out', out, '--no-glassbox'] + list(extra)
    # Popen + real files + wait(timeout) + kill, never subprocess.run(capture_output=True,
    # timeout=...): run()'s timeout path re-enters communicate() to drain the pipes and can block
    # there, so its timeout bounds the wait but not the call (brain DE-026).
    timed_out = False
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                   env=env)
        try:
            code = process.wait(timeout=PRODUCER_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            code, timed_out = PRODUCER_TIMED_OUT, True
        stdout.seek(0)
        stderr.seek(0)
        raw = stdout.read() + stderr.read()
    # Same text the old text-mode capture produced: utf-8 with replacement, universal newlines.
    text = raw.decode('utf-8', 'replace').replace('\r\n', '\n').replace('\r', '\n')
    if timed_out:
        # Its own named failure, so a timeout can never pass for a refusal some case expects.
        note = ('PRODUCER TIMED OUT after %s s and was killed (pid %d): %s'
                % (PRODUCER_TIMEOUT_SECONDS, process.pid, ' '.join(argv[2:])))
        ok('producer finished within %s s' % PRODUCER_TIMEOUT_SECONDS, False, note)
        text += '\n' + note
    return code, text


# --------------------------------------------------------------------- tests

def main():
    workspace = tempfile.mkdtemp(prefix='staffer_section_tests_')
    try:
        # ---------------------------------------------------- (a) happy path
        print('[a] happy path')
        root = os.path.join(workspace, 'happy')
        make_tree(root)
        out = os.path.join(root, 'data', 'engine', 'staffer_network_section.json')
        code, text = run(root, out)
        ok('producer exits 0', code == 0, text.strip()[-400:])
        ok('section written', os.path.exists(out))
        if not os.path.exists(out):
            print('\n%d passed, %d failed' % (P, F))
            return 1
        raw = open(out, encoding='utf-8').read()
        payload = json.loads(raw)
        members, summary, meta = payload['members'], payload['global'], payload['meta']

        alpha, bravo, charlie = members['A000001'], members['B000002'], members['C000003']
        eq('roster identity joins the shared kernel', alpha['display_name'], 'Alpha Member')
        eq('state joins legislators.csv', alpha['state'], 'MN')
        eq('chamber comes from the roster row', bravo['chamber'], 'Senate')
        eq('A staff_total counts people with any stint for the member', alpha['staff_total'], 4)
        eq('A staff_current counts a live stint', alpha['staff_current'], 3)
        eq('A staff_departed is the remainder', alpha['staff_departed'], 1)
        eq('B staff_total', bravo['staff_total'], 2)
        eq('B staff_current', bravo['staff_current'], 1)
        eq('a member with no staff reports zero', charlie['staff_total'], 0)
        eq('trajectory and node-score populations agree',
           [m['staff_total'] for m in (alpha, bravo, charlie)],
           [m['staff_total_node_scores'] for m in (alpha, bravo, charlie)])

        eq('A shared_staff counts people attached to >=2 member offices',
           alpha['shared_staff'], 2)
        eq('B shared_staff', bravo['shared_staff'], 1)
        eq('A shared_with names only roster partners',
           alpha['shared_with'], [{'bioguide': 'B000002', 'people': 1}])
        eq('B shared_with is the mirror image',
           bravo['shared_with'], [{'bioguide': 'A000001', 'people': 1}])
        ok('shared_with pairing is symmetric',
           {(e['bioguide'], e['people']) for e in alpha['shared_with']} ==
           {('B000002', 1)} and
           {(e['bioguide'], e['people']) for e in bravo['shared_with']} == {('A000001', 1)})
        eq('an attachment to a non-roster member is counted separately',
           alpha['shared_staff_with_unrostered_members'], 1)
        eq('a member with no staff has no shared_with', charlie['shared_with'], [])

        order = [entry['node_id'] for entry in alpha['top_staff']]
        eq('uncertainty-adjusted dated anchors favor more recent evidence; undated people remain unranked',
           order, [node_id(k) for k in ('p1', 'p3', 'p2')])
        ranking = [[(-e['connection_score'], -e['member_connection_strength'], e['node_id'])
                    for e in entry['top_staff']] for entry in members.values()]
        ok('the emitted top_staff order is reproducible from the emitted fields',
           all(keys == sorted(keys) for keys in ranking))
        bridge = [(-e['bridge_connection_strength'], e['node_id'])
                  for e in summary['top_bridge_people']]
        ok('the emitted bridge order is reproducible from the emitted fields',
           bridge == sorted(bridge))
        eq('top_staff exposes exactly the contracted fields',
           sorted(alpha['top_staff'][0]),
           sorted(['node_id', 'title_last', 'status', 'identity_tier', 'co_tenure_degree',
                   'pagerank_085', 'eigen_component_local', 'office_diversity',
                   'other_member_count', 'tenure_days_with_member',
                   'co_tenure_office_name', 'co_tenure_office_size', 'member_connection_strength', 'rank_tie_size', 'connection_score', 'member_context_score']))
        ok('top_staff carries no person name',
           # Narrowed from a blanket 'name' substring match on 2026-09-07: ruling 1
           # added co_tenure_office_name, a legitimate always-present field with no
           # bearing on the person-name-without-a-bound-spine contract this checks
           # (see [h] for the spine-bound case, where display_name DOES appear).
           not any('display_name' in key for entry in alpha['top_staff'] for key in entry))
        eq('title comes from the stint for this member',
           alpha['top_staff'][0]['title_last'], 'CHIEF OF STAFF')
        eq('status is current when any stint for this member is current',
           alpha['top_staff'][0]['status'], 'current')
        eq('tenure_days_with_member sums dated stints',
           alpha['top_staff'][0]['tenure_days_with_member'], 365)
        ok('undated employment cannot produce a ranked office-access anchor',
           node_id('p5') not in order)
        eq('a shared person reports other_member_count 1',
           next(e for e in alpha['top_staff'] if e['node_id']==node_id('p3'))['other_member_count'], 1)
        eq('the shared person tenure is scoped to this member only',
           next(e for e in alpha['top_staff'] if e['node_id']==node_id('p3'))['tenure_days_with_member'], 365)
        eq('a member with no staff has an empty top_staff', charlie['top_staff'], [])

        # ------------------------------------------ (a2) co-tenure office relabel
        # Ruling 1 (RELABEL, 2026-09-07): every top_staff row also names the
        # payroll container the person spent the most days in, GLOBALLY -- not
        # scoped to whichever member's page is being read -- with its size as the
        # distinct people ever attached to it. p1's only container is A000001's
        # own office (id resolves through the roster to the member's display
        # name, since this v5 fixture's one container row carries no office_key);
        # its size is 4 because p1, p2, p3 and p5 all have a stint there.
        print('[a2] co-tenure office relabel')
        eq("p1's home office resolves through the roster (no office_key on this v5 container row)",
           alpha['top_staff'][0]['co_tenure_office_name'], 'Alpha Member')
        eq('its size is every person ever attached, not just this member\'s current staff',
           alpha['top_staff'][0]['co_tenure_office_size'], 4)
        p3_on_alpha = next(e for e in alpha['top_staff'] if e['node_id'] == node_id('p3'))
        p3_on_bravo = next(e for e in bravo['top_staff'] if e['node_id'] == node_id('p3'))
        eq('the office named is the MOST RECENT one (owner ruling 2026-09-07): p3\'s '
           'A000001 stint (2021-2022) ends after the B000002 stint (2018-2019), and '
           'would win on recency even if the day counts were not equal',
           p3_on_alpha['co_tenure_office_name'], 'Alpha Member')
        eq('the field is person-scoped, not member-scoped: p3 reports the SAME '
           'office on bravo\'s page as on alpha\'s, even though p3 is displayed '
           'under bravo (B000002) here',
           p3_on_bravo['co_tenure_office_name'], p3_on_alpha['co_tenure_office_name'])
        eq('and the same size too', p3_on_bravo['co_tenure_office_size'],
           p3_on_alpha['co_tenure_office_size'])
        eq('only dated employment supports a Bravo recommendation',
           [e['node_id'] for e in bravo['top_staff']], [node_id('p3')])
        ok('p4 and p5 remain in staffing totals but have no dated recommendation',
           bravo['staff_total']==2 and alpha['staff_total']==4)
        eq('meta carries the one-sentence relabel definition verbatim',
           meta['co_tenure_office_note'],
           "People whose payroll intervals overlapped this person's under the same "
           "office header; the office named is this person's most recent office or "
           'committee.')
        ok('meta states the most-recent rule the producer applied',
           'newest last covered day (core_end_exclusive)' in meta['co_tenure_office_rule']
           and 'Payroll takes precedence' in meta['co_tenure_office_rule'],
           meta['co_tenure_office_rule'])
        eq('meta counts which record named each published cell',
           meta['office_name_source_counts'],
           {'roster_payroll': 4})
        eq('no House personnel ledger on this fixture, and meta says so rather than '
           'implying one was consulted', meta['hon_ledger_generation'], None)
        eq('no Politico layer on this generation either', meta['politico_fallback']['bound'], False)
        # The regression that made this test worth writing: a local named
        # `bioguide` inside the top_staff loop shadowed the member being built and
        # rekeyed the whole members dict to whichever office the last displayed
        # person came from, silently deleting a member.
        eq('every roster member appears exactly once, keyed by their own bioguide',
           sorted(members), ['A000001', 'B000002', 'C000003'])
        ok('and each member payload agrees with its own key',
           all(entry['bioguide'] == key for key, entry in members.items()))
        eq('meta names the v5 fallback size source when no staffer_social_groups '
           'artifact is on this generation',
           meta['co_tenure_office_size_source'],
           'computed from staffer_trajectories.ndjson.gz: distinct spine_person_id ever '
           'attached to the container (no staffer_social_groups.ndjson.gz on this generation)')
        eq('no SHARED EMPLOYEE title is in this fixture, so the new meta counter is zero',
           summary['top_staff_excluded_shared_employee'], 0)
        eq('and agrees with the existing by-title sum it is named for',
           summary['top_staff_excluded_shared_employee'],
           summary['top_staff_excluded_by_title_sum_over_members'])

        eq('titles counts current staff only',
           alpha['titles'], [{'title_last': 'CHIEF OF STAFF', 'people': 2},
                             {'title_last': 'LEGISLATIVE DIRECTOR', 'people': 1}])
        eq('B titles', bravo['titles'], [{'title_last': 'DISTRICT DIRECTOR', 'people': 1}])

        eq('A moves_in sums people arriving', alpha['moves_in'], 5)
        eq('A moves_out sums people leaving', alpha['moves_out'], 3)
        eq('B moves_in', bravo['moves_in'], 3)
        eq('B moves_out', bravo['moves_out'], 2)
        eq('A top_sources names the origin container',
           alpha['top_sources'], [{'container_id': 'container:office:house:ADMIN',
                                   'container_kind': 'admin', 'member_bioguide': None,
                                   'people': 5}])
        eq('A top_destinations names the destination container',
           alpha['top_destinations'], [{'container_id': 'container:office:senate:HON:B000002',
                                        'container_kind': 'member_office',
                                        'member_bioguide': 'B000002', 'people': 3}])
        eq('a member with no moves reports zero', charlie['moves_in'], 0)

        eq('revolving_degree_sum adds over the member people',
           alpha['revolving_degree_sum'], 6)
        eq('component_ids are the distinct components of the member people',
           alpha['component_ids'], [0])
        eq('largest_component_share', alpha['largest_component_share'], 1.0)
        eq('a member with no staff has no component share',
           charlie['largest_component_share'], None)

        eq('zero_overlap_pairs_rejected is copied from the receipt',
           summary['zero_overlap_pairs_rejected'], ZERO_OVERLAP)
        eq('zero_overlap is mirrored into meta so the surface can show it',
           meta['zero_overlap_pairs_rejected'], ZERO_OVERLAP)
        eq('node_universe is copied, not recomputed',
           summary['node_universe'], {'person': 6, 'container': 1, 'total': 7})
        eq('rule_version is copied', summary['rule_version'], 'staffer-social-network-test')
        eq('generated_at is copied', summary['generated_at'], '2026-09-03T00:00:00Z')
        eq('roster_size', summary['roster_size'], 3)
        eq('members_with_any_staff', summary['members_with_any_staff'], 2)
        eq('members_with_shared_staff', summary['members_with_shared_staff'], 2)
        eq('person components counted over every person node',
           summary['person_components'],
           {'distinct_component_ids': 2, 'largest_component_size': 5})
        eq('person nodes with a member attachment',
           summary['person_nodes_with_member_attachment'], 5)
        eq('staff_total sums to the per-person roster attachment count',
           summary['staff_total_sum_over_members'], 6)
        ok('staff_total sum is at least the attached-person count',
           summary['staff_total_sum_over_members'] >=
           summary['person_nodes_with_roster_member_attachment'])
        eq('dated direct employment supplies size-adjusted bridge anchors independently of old graph attribution',
            [entry['node_id'] for entry in summary['top_bridge_people'][:2]],
           [node_id('p3')])

        eq('meta.exploratory', meta['exploratory'], True)
        eq('meta.review_status', meta['review_status'], 'REVIEW_REQUIRED')
        eq('meta.canonical_eligible', meta['canonical_eligible'], False)
        eq('meta.statement is the receipt statement verbatim', meta['statement'], STATEMENT)
        eq('meta.language_note is the shared-staff rule',
           meta['language_note'],
           'Shared staff means people attached to both offices. It is not a tie between the '
           'two members and carries no weight.')
        eq('meta.generation', meta['generation'], GENERATION)
        eq('meta.verification.status', meta['verification']['status'], 'PASS')
        ok('meta records a sha256 for every consumed artifact',
           all(meta['inputs'][role]['sha256'] and meta['inputs'][role]['bound_against']
               for role in ('staffer_social_node_scores', 'staffer_trajectories',
                            'staffer_office_moves', 'generation_receipt',
                            'generation_manifest')))
        ok('the unread edge file is declared unbound, not silently claimed',
           meta['inputs']['staffer_social_edges']['bound_against'] == [] and
           'not_recomputed' in meta['inputs']['staffer_social_edges'])
        eq('meta counts the attached bioguides that are not on the roster',
           meta['unrostered_member_bioguides'], 1)
        eq('meta.bytes equals the file it is written into',
           meta['bytes'], os.path.getsize(out))
        eq('meta.bytes equals the serialized length',
           meta['bytes'], len(raw.encode('utf-8')))

        # ---------------------------------------- (f) no member-member vocabulary
        print('[f] language law')
        for token in ('member_tie', 'member_edge', 'influence'):
            ok('the output never says %r' % token, token not in raw.lower())
        ok('no member-member weight key of any kind',
           'member_weight' not in raw.lower() and 'member_to_member' not in raw.lower())

        # ---------------------------------------------- (b) verification not PASS
        print('[b] verification gate')
        for label, kwargs in (('status FAIL', {'verification_status': 'FAIL'}),
                              ('a recorded failure', {'failures': ['co_tenure count mismatch']})):
            root_b = os.path.join(workspace, 'verify_' + label.split()[0] + label.split()[-1])
            make_tree(root_b, **kwargs)
            out_b = os.path.join(root_b, 'out.json')
            code, text = run(root_b, out_b)
            eq('%s refuses with exit 2' % label, code, 2)
            ok('%s writes nothing' % label, not os.path.exists(out_b))
            ok('%s says why' % label, 'REFUSED' in text, text.strip()[-200:])

        root_promo = os.path.join(workspace, 'promotions')
        make_tree(root_promo, promotions=1)
        out_promo = os.path.join(root_promo, 'out.json')
        code, text = run(root_promo, out_promo)
        eq('a canonical promotion trips the wire', code, 2)
        ok('promotion tripwire writes nothing', not os.path.exists(out_promo))
        ok('promotion tripwire names itself', 'promotion tripwire' in text.lower(),
           text.strip()[-200:])

        # ------------------------------------------------- (c) tampered artifact
        print('[c] hash binding')
        root_c = os.path.join(workspace, 'tampered')
        make_tree(root_c, tamper='staffer_social_node_scores.ndjson.gz')
        out_c = os.path.join(root_c, 'out.json')
        code, text = run(root_c, out_c)
        eq('a tampered artifact refuses with exit 2', code, 2)
        ok('a tampered artifact writes nothing', not os.path.exists(out_c))
        ok('a tampered artifact reports a sha256 mismatch',
           'sha256 mismatch' in text.lower(), text.strip()[-300:])

        root_c2 = os.path.join(workspace, 'receipt_hash')
        make_tree(root_c2, break_receipt_hash=True)
        out_c2 = os.path.join(root_c2, 'out.json')
        code, text = run(root_c2, out_c2)
        eq('a receipt that does not match the verification receipt refuses', code, 2)
        ok('the receipt mismatch writes nothing', not os.path.exists(out_c2))

        root_c3 = os.path.join(workspace, 'missing_field')
        make_tree(root_c3, drop_field='connectedness_pagerank_exploratory')
        out_c3 = os.path.join(root_c3, 'out.json')
        code, text = run(root_c3, out_c3)
        eq('a missing node-score field refuses rather than improvising', code, 2)
        ok('the missing field writes nothing', not os.path.exists(out_c3))
        ok('the missing field is named', 'missing required field' in text.lower(),
           text.strip()[-300:])

        # ------------------------------------------------------- (d) size cap
        print('[d] size cap')
        root_d = os.path.join(workspace, 'sizecap')
        make_tree(root_d)
        out_d = os.path.join(root_d, 'out.json')
        code, text = run(root_d, out_d, extra=['--max-bytes', '500'])
        eq('an oversize section refuses with exit 3', code, 3)
        ok('an oversize section writes nothing', not os.path.exists(out_d))
        ok('the refusal prints the size and the cap',
           'exceeds --max-bytes 500' in text, text.strip()[-300:])
        ok('the refusal lists the largest offenders',
           'largest members:' in text and 'A000001=' in text, text.strip()[-300:])
        ok('the refusal lists the section sizes',
           'sections: ' in text and 'members=' in text, text.strip()[-300:])

        # ----------------------------------------------------- (e) determinism
        print('[e] determinism')
        root_e = os.path.join(workspace, 'determinism')
        make_tree(root_e)
        first = os.path.join(root_e, 'first.json')
        second = os.path.join(root_e, 'second.json')
        third = os.path.join(root_e, 'third.json')
        code_1, _ = run(root_e, first)
        code_2, _ = run(root_e, second)
        code_3, _ = run(root_e, third, epoch='1760000000')
        ok('all three runs succeed', code_1 == 0 and code_2 == 0 and code_3 == 0)
        first_bytes = open(first, 'rb').read()
        second_bytes = open(second, 'rb').read()
        ok('two runs are byte-identical', first_bytes == second_bytes,
           '%d vs %d bytes' % (len(first_bytes), len(second_bytes)))
        payload_1 = json.loads(first_bytes.decode('utf-8'))
        payload_3 = json.loads(open(third, encoding='utf-8').read())
        ok('a different build stamp changes only built_at',
           payload_1['meta']['built_at'] != payload_3['meta']['built_at'] and
           {k: v for k, v in payload_1['meta'].items() if k != 'built_at'} ==
           {k: v for k, v in payload_3['meta'].items() if k != 'built_at'} and
           payload_1['members'] == payload_3['members'] and
           payload_1['global'] == payload_3['global'])
        ok('the file is exactly the sorted, compact serialization',
           first_bytes.decode('utf-8') == json.dumps(
               payload_1, sort_keys=True, separators=(',', ':'), ensure_ascii=False))

        # ------------------------------------------- join gate (negative control)
        print('[g] artifact join gate')
        root_g = os.path.join(workspace, 'brokenjoin')
        make_tree(root_g, break_join=True)
        out_g = os.path.join(root_g, 'out.json')
        code, text = run(root_g, out_g)
        eq('a trajectory that does not join the node scores refuses', code, 2)
        ok('the broken join writes nothing', not os.path.exists(out_g))
        ok('the broken join is named, not silently nulled',
           'do not join the node scores' in text, text.strip()[-300:])

        # ------------------------------------------------- (h) display_name_for
        # The spine's payee_key is casefolded, so joining the three fields raw
        # printed lower-case names on the surface.  display_name_for is the one
        # place a spine record becomes a printable name, and it renders through
        # name_standard rather than by string join.
        print('[h] display names')
        root_h = os.path.join(workspace, 'names')
        make_tree(root_h, spine=True)
        out_h = os.path.join(root_h, 'out.json')
        code, text = run(root_h, out_h)
        ok('a tree with a bound spine builds', code == 0, text.strip()[-400:])
        payload_h = json.loads(open(out_h, encoding='utf-8').read())
        named = {entry['node_id']: entry['display_name']
                 for member in payload_h['members'].values()
                 for entry in member['top_staff']}
        named.update({entry['node_id']: entry['display_name']
                      for entry in payload_h['global']['top_bridge_people']})
        eq('a casefolded payee_key renders as a display name, not as source case',
           named.get(node_id('p1')), 'Ada Bridger')
        eq('name_standard owns the Mc rule and the middle initial',
           named.get(node_id('p2')), 'Elizabeth E. McDonald')
        eq('a middle initial that is also a Roman numeral survives the join',
           named.get(node_id('p3')), 'James V. Daly Jr.')
        eq('an undated record cannot enter recommendations through a name join',
           named.get(node_id('p4')), None)
        ok('every displayed person carries a display_name',
           all(isinstance(v, str) and v for v in named.values()), named)
        ok('the name policy states the exact-ID join',
           'exact spine ID' in (payload_h['meta'].get('name_policy') or ''),
           payload_h['meta'].get('name_policy'))
        ok('the spine is bound by hash in meta.inputs',
           payload_h['meta']['inputs'].get('hill_person_spine_display_names', {}).get('verified')
           is True, sorted(payload_h['meta']['inputs']))

        # ------------------------------------------------- (i) generation shapes
        # The staffer social network moved to a PERSONS-ONLY operator (D-022) and
        # renamed two person-row fields plus one receipt block on the way.  The
        # pinned generation is still v5 today, so this producer must read BOTH and
        # say which it read; a producer that only reads one is a chain that breaks
        # on the day the generation is repointed.  [i] drives the v6 shape end to
        # end and [i2] pins what each shape puts in meta.
        print('[i] v6 (persons-only) generation shape')
        root_v6 = os.path.join(workspace, 'v6')
        make_tree(root_v6, spine=True, shape='v6')
        out_v6 = os.path.join(root_v6, 'out.json')
        code, text = run(root_v6, out_v6)
        ok('a persons-only v6 generation builds', code == 0, text.strip()[-500:])
        payload_v6 = json.loads(open(out_v6, encoding='utf-8').read())
        meta_v6 = payload_v6['meta']
        alpha_v6 = payload_v6['members']['A000001']
        eq('the diversity column is read from the v6 field name',
           meta_v6['office_diversity_source'], 'office_diversity_exploratory')
        eq('the v5 field name is what a v5 generation reports',
           meta['office_diversity_source'], 'bridging_diversity_exploratory')
        eq('the per-person revolving column is read from the v6 field name',
           meta_v6['revolving_source'], 'registrant_organization_group_count')
        eq('and from the v5 field name on a v5 generation',
           meta['revolving_source'], 'revolving_degree')
        ok('the payload field is called office_diversity on BOTH shapes, so one '
           'consumer serves both',
           all('office_diversity' in entry
               for p in (payload_v6, payload_h)
               for member in p['members'].values() for entry in member['top_staff']))
        ok('the retired name never reaches the payload',
           'bridging_diversity' not in json.dumps(payload_v6))
        eq('the v6 numbers are the generation own numbers, not zeros',
           [entry['office_diversity'] for entry in alpha_v6['top_staff']],
           [entry['office_diversity'] for entry in payload_h['members']['A000001']['top_staff']])
        ok('the score note says which field the column came from, in the payload',
           'office_diversity_exploratory' in meta_v6['score_notes']['office_diversity'],
           meta_v6['score_notes']['office_diversity'])
        ok('a v5-sourced column says in the payload that it is NOT office bridging',
           'not over offices' in meta['score_notes']['office_diversity'],
           meta['score_notes']['office_diversity'])

        print('[i2] the policy-evidence control, and the honest null')
        eq('a v6 receipt block is read from policy_link_pagerank_control',
           meta_v6['pagerank_policy_control_source'], 'policy_link_pagerank_control')
        eq('a v5 receipt block is read from ranking_controls',
           meta['pagerank_policy_control_source'], 'ranking_controls')
        eq('the v6 block is carried through unedited',
           meta_v6['pagerank_policy_control'], POLICY_LINK_CONTROL_V6)
        eq('the v5 block is carried through unedited',
           meta['pagerank_policy_control'], RANKING_CONTROLS_V5)
        ok('the retired meta key is gone', 'ranking_controls' not in meta)
        # A persons-only generation carries no container rows, so office-to-
        # organization edges cannot be summed from the node scores.  Null, and a
        # sentence saying why: a zero here would read as "no such edges".
        eq('office_revolving_edge_count is null, not zero, when it cannot be summed',
           alpha_v6['office_revolving_edge_count'], None)
        eq('and is a real number on a generation that carries container rows',
           payload_h['members']['A000001']['office_revolving_edge_count'], 4)
        ok('the payload says why it is null',
           'Null is the honest value' in meta_v6['score_notes']['office_revolving_edge_count'],
           meta_v6['score_notes']['office_revolving_edge_count'])

        print('[i4] staffer_social_groups.ndjson.gz (v6) takes priority over the fallback')
        root_v6b = os.path.join(workspace, 'v6-groups')
        make_tree(root_v6b, spine=True, shape='v6', groups=[
            {'group_id': 'container:office:house:HON:A000001', 'group_kind': 'container',
             'container_kind': 'member_office',
             'name': 'Group File Office Name', 'people_count': 99},
        ])
        out_v6b = os.path.join(root_v6b, 'out.json')
        code, text = run(root_v6b, out_v6b)
        ok('a v6 generation with a staffer_social_groups artifact builds', code == 0,
           text.strip()[-400:])
        payload_v6b = json.loads(open(out_v6b, encoding='utf-8').read())
        alpha_v6b = payload_v6b['members']['A000001']
        eq('p1 is still alpha\'s top_staff[0] on this shape',
           alpha_v6b['top_staff'][0]['node_id'], node_id('p1'))
        eq('the generation\'s own staffer_social_groups name wins over the computed '
           'roster/office_key fallback',
           alpha_v6b['top_staff'][0]['co_tenure_office_name'], 'Group File Office Name')
        eq('and its own people_count wins over the trajectories-computed distinct count',
           alpha_v6b['top_staff'][0]['co_tenure_office_size'], 99)
        eq('meta names the v6 size source when a staffer_social_groups artifact is present',
           payload_v6b['meta']['co_tenure_office_size_source'],
           "this generation's own staffer_social_groups.ndjson.gz people_count for a "
           'container it lists; computed as distinct spine_person_id ever attached, from '
           'staffer_trajectories.ndjson.gz, for any container it does not')

        print('[i5] a group office key is resolved, never printed as a name')
        root_v6c = os.path.join(workspace, 'v6-group-office-key')
        make_tree(root_v6c, spine=True, shape='v6', groups=[
            {'group_id': 'container:office:house:HON:A000001', 'group_kind': 'container',
             'container_kind': 'member_office', 'name': 'HON:A000001', 'people_count': 99},
        ])
        out_v6c = os.path.join(root_v6c, 'out.json')
        code, text = run(root_v6c, out_v6c)
        ok('the actual group schema carrying a payroll key builds', code == 0, text[-400:])
        payload_v6c = json.loads(open(out_v6c, encoding='utf-8').read())
        alpha_v6c = payload_v6c['members']['A000001']
        eq('a raw group key cannot override the resolved roster name',
           alpha_v6c['top_staff'][0]['co_tenure_office_name'], alpha_v6c['display_name'])
        ok('move labels also contain resolved names instead of HON keys',
           all(not str(name).startswith('HON:')
               for name in payload_v6c['meta']['move_office_names'].values()))

        print('[i3] negative control: an unknown node-score shape refuses')
        root_v7 = os.path.join(workspace, 'unknownshape')
        make_tree(root_v7, drop_field='bridging_diversity_exploratory')
        code, text = run(root_v7, os.path.join(root_v7, 'out.json'))
        eq('a person row carrying NEITHER diversity field refuses rather than '
           'printing an empty column', code, 2)
        ok('the refusal names both field names it looked for',
           'office_diversity_exploratory' in text and 'bridging_diversity_exploratory' in text,
           text.strip()[-300:])
        root_v8 = os.path.join(workspace, 'norevolving')
        make_tree(root_v8, drop_field='revolving_degree')
        code, text = run(root_v8, os.path.join(root_v8, 'out.json'))
        eq('a person row carrying neither revolving field refuses too', code, 2)

        # ------------------------------------------------ (j) SHARED EMPLOYEE
        print('[j] SHARED EMPLOYEE leaves the ranking pool (ruling 3 fixture test)')
        root_j = os.path.join(workspace, 'shared_employee')
        make_shared_employee_tree(root_j)
        out_j = os.path.join(root_j, 'out.json')
        code, text = run(root_j, out_j)
        ok('the shared-employee fixture builds', code == 0, text.strip()[-400:])
        payload_j = json.loads(open(out_j, encoding='utf-8').read())
        delta = payload_j['members']['D000004']
        eq('x (the highest-ranked person: pagerank 0.9 vs y\'s 0.4) carries the '
           'excluded title and must not appear in top_staff at all -- excluded '
           'from the ranking POOL, not merely demoted within it',
           [e['node_id'] for e in delta['top_staff']], [node_id('y')])
        eq('x is still counted: staff_total sees both people on the payroll record',
           delta['staff_total'], 2)
        eq('the exclusion is counted once, by title, on the member',
           delta['top_staff_excluded_by_title'], 1)
        eq('and the new ruling-3 meta counter counts the same removed cell',
           payload_j['global']['top_staff_excluded_shared_employee'], 1)
        eq("y's home container names the committee directly from its own "
           "office_key ('JUDICIARY'), not the roster/HON: fallback path [a2] "
           'exercises for a member-office container',
           delta['top_staff'][0]['co_tenure_office_name'], 'JUDICIARY')
        eq('its size counts everyone ever attached, including the excluded x: '
           'exclusion from the RANKING never removes anyone from the graph or '
           'from a container size',
           delta['top_staff'][0]['co_tenure_office_size'], 2)

        # -------------------------------------------- (k) the office-label ruling
        print('[k] most recent office, payroll then politico, honest absence '
              '(owner ruling 2026-09-07)')
        root_k = os.path.join(workspace, 'office_label')
        make_office_label_tree(root_k)
        out_k = os.path.join(root_k, 'out.json')
        code, text = run(root_k, out_k, extra=['--hon-ledger-generation',
                                               HON_LEDGER_GENERATION])
        ok('the office-label fixture builds', code == 0, text.strip()[-500:])
        payload_k = json.loads(open(out_k, encoding='utf-8').read())
        echo = payload_k['members']['E000005']
        meta_k = payload_k['meta']
        by_person = {entry['node_id']: entry for entry in echo['top_staff']}

        def office(key):
            entry = by_person[node_id(key)]
            return entry['co_tenure_office_name'], entry['co_tenure_office_size']

        eq('THE RULING: r1 spent ten years in the off-roster office and three '
           'months in Echo\'s, and the office named is the RECENT one. Under the '
           'old most-days rule this cell read "Zelda Quince".',
           office('r1'), ('Echo Member', 8))
        eq('r2\'s most recent office belongs to a member off the current roster, '
           'so it is named from the House personnel ledger\'s own printed string '
           '-- and from the MOST RECENT quarter of it (2021Q3 "ZELDA QUINCE", not '
           '2019Q1 "ZELDA OLDNAME")',
           office('r2'), ('Zelda Quince', 2))
        eq('r3\'s most recent office is a vacant-seat header that names no member; '
           'the cell says the name is missing instead of printing HON:11TH|JERSEY',
           office('r3'), ('Office name not in the records', 1))
        eq('r4\'s most recent office is a committee, named by the record\'s own '
           'office_key', office('r4'), ('APPROPRIATIONS', 2))
        ok('undated r5/r6 cannot become office-access recommendations from committee affiliations',
           node_id('r5') not in by_person and node_id('r6') not in by_person)
        eq('and that refused row is COUNTED, so a generation that renames or drops '
           'mapping_status shows up as a number rather than as a fallback that '
           'quietly stopped firing',
           meta_k['politico_fallback']['current_committee_rows_refused'], 1)
        eq('r7\'s office key spells the member out, and is rendered through the '
           'same name standard a person is', office('r7'), ('Sam Rayburn', 1))
        eq('r8 holds two offices ending on the same day; the one the record marks '
           'current wins the tie', office('r8'), ('Echo Member', 8))
        ok('PAYROLL TAKES PRECEDENCE: r1 also carries a Politico current-committee '
           'row naming APPROPRIATIONS, and it loses to the payroll office',
           by_person[node_id('r1')]['co_tenure_office_name'] == 'Echo Member')
        eq('only the one admissible Politico layer is counted',
           meta_k['politico_fallback']['people_with_a_current_committee'], 2)
        eq('undated committee fallback does not supply a ranked personal-office anchor',
           meta_k['office_name_source_counts'].get('office_key_politico', 0), 0)
        ok('the Politico layer is bound by hash like every other consumed file',
           meta_k['politico_fallback']['bound']
           and 'staffer_social_politico_review_layers' in meta_k['inputs'])
        eq('the ledger generation is named in meta, not assumed',
           meta_k['hon_ledger_generation'], HON_LEDGER_GENERATION)
        ok('and both ledger files are bound by hash',
           {'house_personnel_ledger_receipt', 'house_member_quarters'}
           <= set(meta_k['inputs']))
        eq('every published cell is accounted for by exactly one naming source',
           sum(meta_k['office_name_source_counts'].values()), len(echo['top_staff']))
        serialized = json.dumps(payload_k, sort_keys=True)
        ok('NO RAW CONTAINER ID REACHES THE PAGE: no office name in the payload '
           'contains a HON: key or a container: id',
           not any('HON:' in str(entry['co_tenure_office_name'] or '')
                   or str(entry['co_tenure_office_name'] or '').startswith('container:')
                   for entry in echo['top_staff']), serialized[:400])

        # ---------------------------------- (l) the SAME ruling on the move tables
        print('[l] the move tables are named by the producer, not by the browser '
              '(owner ruling 2026-09-07)')
        moves_map = meta_k['move_office_names']
        moves_counts = meta_k['move_office_name_source_counts']
        sources_k = {row['container_id']: row for row in echo['top_sources']}
        dests_k = {row['container_id']: row for row in echo['top_destinations']}
        eq('all four inbound moves publish', len(sources_k), 4)
        eq('and the one outbound move publishes', len(dests_k), 1)

        eq('THE FIX: an office off the current roster is named from the House '
           'personnel ledger, keyed by the HON: key the page itself extracts -- '
           'this is the cell that used to read "HON:Z000099"',
           moves_map.get('Z000099'), 'Zelda Quince')
        eq('a key that spells the member out is rendered through the same name '
           'standard a person is', moves_map.get('SAM|RAYBURN'), 'Sam Rayburn')
        ok('a vacant-seat header names nobody, so it is ABSENT from the map -- '
           'absence is what tells the page to print the honest-absence label '
           'rather than the key', '11TH|JERSEY' not in moves_map, sorted(moves_map))
        ok('NO GUESS EITHER: the vacant seat is not named after its own key',
           not any('11TH' in str(value) for value in moves_map.values()),
           sorted(moves_map.items()))
        ok('a roster office costs NOTHING: the page already has that name and the '
           'map does not carry it', 'E000005' not in moves_map and 'F000006' not in moves_map,
           sorted(moves_map))
        ok('a committee is not in the map either: its key IS its name and the page '
           'prints it verbatim',
           not any('APPROPRIATIONS' in key for key in moves_map), sorted(moves_map))
        eq('the map carries exactly the two offices the page cannot work out',
           sorted(moves_map), ['SAM|RAYBURN', 'Z000099'])

        eq('every published move cell on EVERY member is accounted for by exactly '
           'one source -- six, not Echo\'s five: the Foxtrot move publishes a '
           'destination row on Foxtrot\'s page too, and a map built from one '
           'member\'s rows would miss it',
           sum(moves_counts.values()),
           sum(len(payload_k['members'][bio][field])
               for bio in payload_k['members']
               for field in ('top_sources', 'top_destinations')))
        eq('the ledger named the off-roster office', moves_counts.get('ledger'), 1)
        eq('the name standard named the spelled-out key',
           moves_counts.get('key_spelled'), 1)
        eq('one cell is honestly unnamed', moves_counts.get('unresolved'), 1)
        eq('one cell is the record\'s own office string', moves_counts.get('office_key'), 1)
        eq('both ends of the Foxtrot move are roster offices the page already '
           'names, and neither costs a map entry',
           moves_counts.get('roster'), 2)
        ok('on House-style keys the page and the office column print the same '
           'string, so nothing lands in page_key_differs. (The real build DOES '
           'carry 272 there, all Senate offices spelled in prose; that bucket '
           'exists to surface them rather than to assert they cannot happen.)',
           'page_key_differs' not in moves_counts, moves_counts)
        ok('the payload says how the move tables are named rather than leaving the '
           'reader to infer it from the map',
           'move_office_names' in meta_k['move_office_name_policy']
           and 'office_name_unresolved_label' in meta_k['move_office_name_policy'],
           meta_k['move_office_name_policy'])
        ok('NO RAW CONTAINER ID IS OFFERED AS A NAME: no value in the move map is '
           'a HON: key or a container: id',
           not any(str(value).startswith(('HON:', 'container:'))
                   for value in moves_map.values()), sorted(moves_map.items()))

        # The ledger is optional; its absence costs names and must SAY so.
        root_k2 = os.path.join(workspace, 'office_label_noledger')
        make_office_label_tree(root_k2, ledger=False)
        out_k2 = os.path.join(root_k2, 'out.json')
        code, text = run(root_k2, out_k2, extra=['--hon-ledger-generation',
                                                 HON_LEDGER_GENERATION])
        ok('the build still succeeds with no House personnel ledger present',
           code == 0, text.strip()[-400:])
        payload_k2 = json.loads(open(out_k2, encoding='utf-8').read())
        echo2 = {entry['node_id']: entry
                 for entry in payload_k2['members']['E000005']['top_staff']}
        eq('without the ledger, r2\'s off-roster office is honestly unnamed rather '
           'than printed as HON:Z000099',
           echo2[node_id('r2')]['co_tenure_office_name'],
           'Office name not in the records')
        eq('and meta says no ledger was consulted',
           payload_k2['meta']['hon_ledger_generation'], None)

        # NEGATIVE CONTROL for [l], the failure this whole change exists to stop:
        # with no ledger the off-roster office cannot be named, and the ONLY
        # acceptable outcome is that it drops out of the map. A map that still
        # carried Z000099 -- under any string -- would mean something invented a
        # name, and a map that carried the key itself would put HON:Z000099 back
        # on the page by a different route.
        moves_map2 = payload_k2['meta']['move_office_names']
        ok('without the ledger the off-roster move office is ABSENT from the map, '
           'not named after its own key', 'Z000099' not in moves_map2, sorted(moves_map2))
        ok('and nothing else invented a name for it',
           not any('Z000099' in str(value) or 'Zelda' in str(value)
                   for value in moves_map2.values()), sorted(moves_map2.items()))
        eq('that cell now counts as honestly unnamed instead of ledger-named: two '
           'unresolved cells where the ledger build had one',
           payload_k2['meta']['move_office_name_source_counts'].get('unresolved'), 2)
        ok('the ledger is the ONLY thing that changed: the spelled-out key is '
           'still named, because the name standard needs no ledger',
           moves_map2.get('SAM|RAYBURN') == 'Sam Rayburn', sorted(moves_map2.items()))

        # ---------------------------------- (m) groups role regression
        print('[m] the groups artifact binds by the ROLE the producer actually '
              'emits, not by its filename stem (regression, 2026-09-07)')
        # build_staffer_social_network.py emits this file under role "groups"
        # (audits/staffer_network/build_staffer_social_network.py:3350) -- never
        # "staffer_social_groups", which is only ever the filename stem. [i4]
        # above exercises this same code path through make_tree()'s `groups=`
        # fixture, but that fixture itself mints the file entry under the
        # filename-stem string (see the sanity check below), so it happens to
        # match a lookup keyed to the wrong string and cannot catch this defect
        # either way. This block rewrites the receipt in place to the
        # producer's REAL role so the fixture stops mirroring the bug.
        root_m = os.path.join(workspace, 'v6-groups-real-role')
        make_tree(root_m, spine=True, shape='v6', groups=[
            {'id': 'container:office:house:HON:A000001', 'kind': 'member_office',
             'name': 'Real Role Office Name', 'people_count': 77},
        ])
        receipt_path_m = os.path.join(root_m, 'audits', 'staffer_network', GENERATION,
                                      'staffer_social_network_receipt.json')
        with open(receipt_path_m, encoding='utf-8') as stream:
            receipt_m = json.load(stream)
        groups_entry_m = next(f for f in receipt_m['files']
                              if f['path'] == 'staffer_social_groups.ndjson.gz')
        eq('sanity: make_tree must mint the groups file entry under the same '
           'role the builder actually emits (audits/staffer_network/'
           'build_staffer_social_network.py:3350) -- if this ever drifts, the '
           'rewrite below silently stops proving anything',
           groups_entry_m['role'], 'groups')
        # No-op now that make_tree mints the correct role checked above (the
        # fixture already agrees with the builder). Left in place -- costs
        # nothing, and keeps this block correct if the fixture ever regresses
        # to minting the filename-stem string again.
        groups_entry_m['role'] = 'groups'
        with open(receipt_path_m, 'w', encoding='utf-8') as stream:
            json.dump(receipt_m, stream, sort_keys=True)
        receipt_sha_m = sha256(receipt_path_m)
        verification_path_m = os.path.join(
            root_m, 'audits', 'staffer_network', VERIFICATION_DIR,
            'staffer_social_network_verification_receipt.json')
        with open(verification_path_m, encoding='utf-8') as stream:
            verification_m = json.load(stream)
        verification_m['twin_byte_identity'][0]['primary_sha256'] = receipt_sha_m
        verification_m['twin_byte_identity'][0]['twin_sha256'] = receipt_sha_m
        with open(verification_path_m, 'w', encoding='utf-8') as stream:
            json.dump(verification_m, stream, sort_keys=True)

        out_m = os.path.join(root_m, 'out.json')
        code, text = run(root_m, out_m)
        ok('a v6 generation whose groups file carries the producer\'s real role '
           'still builds', code == 0, text.strip()[-400:])
        payload_m = json.loads(open(out_m, encoding='utf-8').read())
        alpha_m = payload_m['members']['A000001']
        eq('NEGATIVE CONTROL: the groups file\'s own office name is used -- a '
           'lookup keyed to "staffer_social_groups" never finds this entry under '
           'its real role "groups" and silently falls back to the computed name '
           'instead, which is exactly the defect this block exists to catch',
           alpha_m['top_staff'][0]['co_tenure_office_name'], 'Real Role Office Name')
        eq('and its own people_count wins over the trajectories-computed distinct '
           'count, for the same reason',
           alpha_m['top_staff'][0]['co_tenure_office_size'], 77)
        eq('meta names the v6 size source, proving the file was actually bound '
           'and hash-checked rather than silently unbound',
           payload_m['meta']['co_tenure_office_size_source'],
           "this generation's own staffer_social_groups.ndjson.gz people_count for a "
           'container it lists; computed as distinct spine_person_id ever attached, from '
           'staffer_trajectories.ndjson.gz, for any container it does not')
    except Exception as exc:
        # A producer that timed out leaves no out.json, so the case that reads it raises. Record
        # it and keep the 'N passed, M failed' tail: without the tail the kernel runner can say
        # only "unrecognised verdict", which is how the 2026-09-19 stall first showed up.
        traceback.print_exc(file=sys.stdout)
        ok('suite ran to its end', False, '%s: %s' % (type(exc).__name__, exc))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    print('\n%d passed, %d failed' % (P, F))
    return 1 if F else 0


if __name__ == '__main__':
    sys.exit(main())
