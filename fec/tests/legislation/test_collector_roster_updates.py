# -*- coding: utf-8 -*-
"""Contract tests for the collector -> bills-ledger verification gate.

Every test is isolated under tmp_path: no live repository, bundle, receipt or
workbook is read, and no producer is executed against real data.
"""
import json
import os
import sys

import pytest


FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

import bill_rules as BR  # noqa: E402
import legislation.verify_collector_roster_updates as CRU  # noqa: E402
from legislation import update_bill_repository as UPDATER  # noqa: E402
from legislation.congress_bill_repository import seed_fingerprint  # noqa: E402


BILL = 'H.R.1919'
CONGRESS = '119'
REPO_KEY = '119:hr:1919'
TITLE = 'Payments Modernization Act'
SPONSOR_NAME = 'Alice Adams'
SUMMARY = 'A short adjudicated summary.'
ACTION = 'Referred to the Committee on Financial Services.'
URL = 'https://www.congress.gov/bill/119th-congress/house-bill/1919'


# ---------------------------------------------------------------- fixtures

def _ledger_row(role, areas=('Payments',), bill=BILL, congress=CONGRESS,
                title=TITLE, summary=SUMMARY):
    return {
        'role': role, 'areas': list(areas), 'bill': bill, 'title': title,
        'sponsor': SPONSOR_NAME, 'ncos': 3, 'congress': congress,
        'action': ACTION, 'summary': summary, 'url': URL,
    }


def _member(name, bioguide, bills):
    return {'name': name, 'bio': {'bioguide': bioguide}, 'bills': list(bills)}


def _person(bioguide, name, date='2026-03-04', withdrawn=''):
    return {
        'name': name, 'bioguide': bioguide, 'party': 'D', 'state': 'MN',
        'district': '1', 'date': date, 'withdrawn_date': withdrawn,
    }


def _repo_bill(sponsor, originals=(), cosponsors=(), key=REPO_KEY,
               display=BILL, congress=119, title=TITLE):
    return {
        'key': key, 'congress': congress, 'type': 'hr', 'number': 1919,
        'display': display, 'url': URL, 'title': title,
        'introduced_date': '2026-01-05', 'update_date': '2026-03-05',
        'latest_action': {'date': '2026-03-05', 'text': ACTION},
        'sponsor': sponsor, 'original_cosponsors': list(originals),
        'cosponsors': list(cosponsors),
        'cosponsor_count': len(originals) + len(cosponsors),
        'amendments': [], 'summary': SUMMARY, 'policy_area': 'Finance',
        'areas': ['Payments'], 'updated_at': '2026-03-06T00:00:00Z',
        'stale': False, 'source': 'test',
    }


def _repository(bills):
    return {
        'schema': 'congress-bill-repository-v1',
        'updated_at': '2026-03-06T00:00:00Z',
        'source': 'https://www.congress.gov/',
        'bills': {row['key']: row for row in bills},
        'failures': {},
        'self_repair': {'last_good_preserved': True, 'failed_bills_retained': 0},
    }


def _default_bundle():
    """Members ordered so the JOINER is first: this is what makes the
    seed-fingerprint invariant load-bearing rather than incidental."""
    return {'members': [
        _member('Cara Cole', 'C000003', []),                      # the joiner
        _member('Alice Adams', 'A000001', [_ledger_row('sponsor')]),
        _member('Bob Brown', 'B000002', [_ledger_row('original')]),
    ]}


def _build_root(tmp_path, bundle, repository, *, overrides_csv=None,
                withdrawals_csv=None, status='PASS', failed=0):
    root = tmp_path / 'root'
    for rel in ('data/engine', 'data/members', 'data/legislation'):
        (root / rel).mkdir(parents=True, exist_ok=True)
    bundle_path = root / 'member_desk_bundle.json'
    bundle_path.write_text(json.dumps(bundle), encoding='utf-8')
    repository_path = root / 'bill_repository_live.json'
    repository_path.write_text(json.dumps(repository), encoding='utf-8')
    if overrides_csv is not None:
        (root / 'data' / 'legislation' / 'bill_overrides.csv').write_text(
            overrides_csv, encoding='utf-8')
    if withdrawals_csv is not None:
        (root / CRU.WITHDRAWALS_REL).write_text(
            withdrawals_csv, encoding='utf-8')
    UPDATER.write_refresh_receipt(
        root / 'data' / 'engine' / 'congress_bill_refresh_receipt.json',
        result={'status': status, 'seeded': 1, 'refreshed': 1,
                'failed': failed, 'transport': 'test'},
        bundle_path=bundle_path,
        output_path=repository_path,
    )
    return root


def _updates(root):
    path = root / CRU.UPDATES_REL
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding='utf-8').splitlines() if line.strip()]


def _pending(root):
    path = root / CRU.PENDING_REL
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding='utf-8').splitlines() if line.strip()]


def _resolver(bundle):
    """Strict stand-in for make_member_desk's canon(): bioguide, then exact name."""
    by_bio = {m['bio']['bioguide']: m['name'] for m in bundle['members']}
    names = {m['name'] for m in bundle['members']}

    def resolve(name=None, bioguide=None):
        if bioguide and bioguide in by_bio:
            return by_bio[bioguide]
        return name if name in names else None
    return resolve


def _pre_collapse_ledger(bundle):
    """Expand collapsed bundle rows back into the desk's pre-collapse shape."""
    bills = {}
    for member in bundle['members']:
        for row in member['bills']:
            for area in row.get('areas') or ['']:
                flat = {k: v for k, v in row.items() if k != 'areas'}
                flat['area'] = area
                bills.setdefault(member['name'], []).append(flat)
    return bills


def _collapse(rows):
    """Mirrors make_member_desk.py collapse (one row per bill+congress,
    role precedence sponsor>original>cosponsor, areas merged and sorted)."""
    order = {'sponsor': 0, 'original': 1, 'cosponsor': 2}
    groups = {}
    for b in rows:
        k = (b['bill'], str(b['congress']))
        g = groups.get(k)
        if g is None:
            g = {kk: vv for kk, vv in b.items() if kk != 'area'}
            g['areas'] = []
            groups[k] = g
        if order.get(b['role'], 9) < order.get(g['role'], 9):
            g['role'] = b['role']
        if b['area'] and b['area'] not in g['areas']:
            g['areas'].append(b['area'])
    for g in groups.values():
        g['areas'].sort()
    return list(groups.values())


def _count(rows):
    """Mirrors make_member_desk's per-member cos/spons/area counting."""
    cos = spons = 0
    areas = {}
    for row in rows:
        if row['role'] == 'sponsor':
            spons += 1
            continue
        cos += 1
        for area in row.get('areas') or []:
            areas[area] = areas.get(area, 0) + 1
    return cos, spons, areas


# ---------------------------------------------------- (a) auto-accept path

def test_live_join_on_an_adjudicated_bill_is_auto_accepted(tmp_path):
    bundle = _default_bundle()
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        originals=[_person('B000002', 'Rep. Brown')],
        cosponsors=[_person('C000003', 'Rep. Cole', date='2026-03-04')],
    )])
    root = _build_root(tmp_path, bundle, repository)
    document = CRU.run(root, quiet=True)

    rows = _updates(root)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row['member'] == 'Cara Cole'
    assert row['bioguide'] == 'C000003'
    assert row['role'] == 'cosponsor'
    assert row['join_date'] == '2026-03-04'
    assert row['withdrawn'] is False
    assert row['bill'] == BILL and row['congress'] == CONGRESS
    assert row['repository_key'] == REPO_KEY
    # areas are INHERITED from the bill's adjudicated ledger rows, never invented
    assert row['areas'] == ['Payments']
    assert row['title'] == TITLE and row['sponsor'] == SPONSOR_NAME
    assert row['summary'] == SUMMARY and row['action'] == ACTION
    assert row['source'] == CRU.SOURCE
    # deterministic stamp: the repository refresh's own completion time
    assert row['verified_at'] == document['refresh_receipt']['completed_at']

    # Bob is already in the ledger with the same role -> no new row, no queue
    assert document['counts']['already_present'] == 1
    assert document['counts']['accepted'] == 1
    assert [entry['member'] for entry in document['accepted']] == ['Cara Cole']


def test_original_cosponsor_maps_to_the_original_role(tmp_path):
    bundle = _default_bundle()
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        originals=[_person('B000002', 'Rep. Brown'),
                   _person('C000003', 'Rep. Cole')],
    )])
    root = _build_root(tmp_path, bundle, repository)
    CRU.run(root, quiet=True)
    rows = _updates(root)
    assert [row['role'] for row in rows] == ['original']


def test_sponsor_role_is_never_emitted(tmp_path):
    """A sponsor the ledger lacks is a question, never an auto-accept."""
    bundle = {'members': [
        _member('Cara Cole', 'C000003', []),
        _member('Bob Brown', 'B000002', [_ledger_row('original')]),
    ]}
    repository = _repository([_repo_bill(
        _person('C000003', 'Rep. Cole'),          # sponsor absent from ledger
        originals=[_person('B000002', 'Rep. Brown')],
    )])
    root = _build_root(tmp_path, bundle, repository)
    CRU.run(root, quiet=True)
    assert _updates(root) == []
    reasons = {row['reason'] for row in _pending(root)}
    assert 'sponsor_absent_from_ledger' in reasons
    assert all(row['role'] != 'sponsor' for row in _updates(root))


# ------------------------------------------- (b) universe closure negatives

def test_bill_outside_the_ledger_universe_queues_and_is_never_accepted(tmp_path):
    bundle = _default_bundle()
    stranger = _repo_bill(
        _person('A000001', 'Rep. Adams'),
        cosponsors=[_person('C000003', 'Rep. Cole')],
        key='119:hr:4242', display='H.R.4242', congress=119,
    )
    stranger['number'] = 4242
    repository = _repository([
        _repo_bill(_person('A000001', 'Rep. Adams'),
                   originals=[_person('B000002', 'Rep. Brown')]),
        stranger,
    ])
    root = _build_root(tmp_path, bundle, repository)
    CRU.run(root, quiet=True)
    assert _updates(root) == []
    queued = _pending(root)
    assert [row['reason'] for row in queued] == ['bill_not_in_ledger_universe']
    assert queued[0]['repository_key'] == '119:hr:4242'


def test_desk_merge_skips_unknown_act_keys_with_a_warning(tmp_path):
    """Defence in depth for primacy_factors' hard universe-sync assertion."""
    bundle = _default_bundle()
    bills = _pre_collapse_ledger(bundle)
    sidecar = tmp_path / 'updates.ndjson'
    sidecar.write_text(json.dumps({
        'schema': CRU.UPDATE_SCHEMA, 'bill': 'H.R.4242', 'congress': '119',
        'member': 'Cara Cole', 'bioguide': 'C000003', 'role': 'cosponsor',
        'areas': ['Payments'], 'withdrawn': False,
    }) + '\n', encoding='utf-8')
    messages = []
    audit = CRU.merge_collector_roster_updates(
        str(sidecar), bills, _resolver(bundle), log=messages.append)
    assert audit['collector_roster_skipped_unknown_act'] == 1
    assert audit['collector_roster_appended'] == 0
    assert 'Cara Cole' not in bills
    assert messages and 'unknown act 1' in messages[0]


# ------------------------------------------------ (c) never modify the ledger

def test_role_conflict_queues_and_leaves_the_existing_row_alone(tmp_path):
    bundle = {'members': [
        _member('Bob Brown', 'B000002', [_ledger_row('cosponsor')]),
        _member('Alice Adams', 'A000001', [_ledger_row('sponsor')]),
    ]}
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        originals=[_person('B000002', 'Rep. Brown')],   # ledger says cosponsor
    )])
    root = _build_root(tmp_path, bundle, repository)
    CRU.run(root, quiet=True)
    assert _updates(root) == []
    queued = _pending(root)
    assert [row['reason'] for row in queued] == ['role_conflict']
    assert queued[0]['member'] == 'Bob Brown'
    assert 'ledger role cosponsor' in queued[0]['detail']


def test_ledger_row_absent_from_the_live_roster_queues_never_removes(tmp_path):
    bundle = _default_bundle()
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        originals=[],                                  # Bob has vanished
    )])
    root = _build_root(tmp_path, bundle, repository)
    document = CRU.run(root, quiet=True)
    assert _updates(root) == []
    queued = _pending(root)
    assert [row['reason'] for row in queued] == ['ledger_row_absent_from_live_roster']
    assert queued[0]['member'] == 'Bob Brown'
    assert document['contract']['additive_only'] is True


def test_withdrawn_on_an_existing_row_queues_and_a_withdrawn_join_is_refused(tmp_path):
    bundle = _default_bundle()
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        originals=[_person('B000002', 'Rep. Brown', withdrawn='2026-04-01')],
        cosponsors=[_person('C000003', 'Rep. Cole', withdrawn='2026-04-02')],
    )])
    root = _build_root(tmp_path, bundle, repository)
    CRU.run(root, quiet=True)
    assert _updates(root) == []
    reasons = sorted(row['reason'] for row in _pending(root))
    assert reasons == ['withdrawn_join_absent_from_ledger',
                       'withdrawn_on_existing_ledger_row']


def test_desk_merge_refuses_withdrawn_and_sponsor_rows(tmp_path):
    bundle = _default_bundle()
    bills = _pre_collapse_ledger(bundle)
    lines = [
        {'bill': BILL, 'congress': CONGRESS, 'member': 'Cara Cole',
         'bioguide': 'C000003', 'role': 'cosponsor', 'areas': ['Payments'],
         'withdrawn': True},
        {'bill': BILL, 'congress': CONGRESS, 'member': 'Cara Cole',
         'bioguide': 'C000003', 'role': 'sponsor', 'areas': ['Payments'],
         'withdrawn': False},
    ]
    sidecar = tmp_path / 'updates.ndjson'
    sidecar.write_text(''.join(json.dumps(row) + '\n' for row in lines),
                       encoding='utf-8')
    audit = CRU.merge_collector_roster_updates(
        str(sidecar), bills, _resolver(bundle), log=lambda *_a: None)
    assert audit['collector_roster_skipped_withdrawn'] == 1
    assert audit['collector_roster_skipped_role'] == 1
    assert audit['collector_roster_appended'] == 0
    assert 'Cara Cole' not in bills


# ------------------------------------------- (d) strict bioguide resolution

def test_unresolvable_bioguide_is_skipped_and_never_fuzzy_matched(tmp_path):
    """The scraped NAME matches a roster member exactly; the bioguide does not.
    STRICT-BIOGUIDE law: that is a refusal, not an invitation to name-match."""
    bundle = _default_bundle()
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        originals=[_person('B000002', 'Rep. Brown')],
        cosponsors=[_person('ZZ99999', 'Cara Cole')],
    )])
    root = _build_root(tmp_path, bundle, repository)
    document = CRU.run(root, quiet=True)
    assert _updates(root) == []
    assert document['counts']['skipped_unresolvable'] == 1
    assert document['unresolvable_bioguides'] == [
        {'bioguide': 'ZZ99999', 'occurrences': 1}]
    assert all(entry['member'] != 'Cara Cole' for entry in document['accepted'])


def test_a_bioguide_claimed_by_two_roster_names_is_dropped(tmp_path):
    bundle = {'members': [
        _member('Cara Cole', 'C000003', []),
        _member('Cara Cole-Smith', 'C000003', []),
        _member('Alice Adams', 'A000001', [_ledger_row('sponsor')]),
    ]}
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        cosponsors=[_person('C000003', 'Rep. Cole')],
    )])
    root = _build_root(tmp_path, bundle, repository)
    document = CRU.run(root, quiet=True)
    assert _updates(root) == []
    assert document['ambiguous_bioguides'] == ['C000003']
    assert document['counts']['skipped_unresolvable'] == 1


# ------------------------------------------------------- (e) exclusion rules

def test_bill_excluded_by_rules_is_respected(tmp_path):
    excluded_title = 'Safer Gaming Act'
    bundle = {'members': [
        _member('Cara Cole', 'C000003', []),
        _member('Alice Adams', 'A000001',
                [_ledger_row('sponsor', title=excluded_title)]),
    ]}
    assert BR.excluded_bill(BILL, CONGRESS, excluded_title, '')
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        cosponsors=[_person('C000003', 'Rep. Cole')],
        title=excluded_title,
    )])
    root = _build_root(tmp_path, bundle, repository)
    document = CRU.run(root, quiet=True)
    assert _updates(root) == []
    assert document['counts']['skipped_excluded'] == 1


def test_bill_excluded_by_override_is_respected(tmp_path):
    bundle = _default_bundle()
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        originals=[_person('B000002', 'Rep. Brown')],
        cosponsors=[_person('C000003', 'Rep. Cole')],
    )])
    root = _build_root(
        tmp_path, bundle, repository,
        overrides_csv='Bill,Congress,Category,Include,Reason\n'
                      '%s,%s,Payments,0,test exclusion\n' % (BILL, CONGRESS),
    )
    document = CRU.run(root, quiet=True)
    assert _updates(root) == []
    assert document['counts']['skipped_excluded'] == 1


def test_desk_merge_reapplies_the_exclusion_filter(tmp_path):
    bundle = _default_bundle()
    bills = _pre_collapse_ledger(bundle)
    sidecar = tmp_path / 'updates.ndjson'
    sidecar.write_text(json.dumps({
        'bill': BILL, 'congress': CONGRESS, 'member': 'Cara Cole',
        'bioguide': 'C000003', 'role': 'cosponsor', 'areas': ['Payments'],
        'withdrawn': False, 'title': 'Safer Gaming Act',
    }) + '\n', encoding='utf-8')
    audit = CRU.merge_collector_roster_updates(
        str(sidecar), bills, _resolver(bundle),
        exclude=lambda b, c, t, s: bool(BR.excluded_bill(b, c, t, s)),
        log=lambda *_a: None)
    assert audit['collector_roster_skipped_excluded'] == 1
    assert audit['collector_roster_appended'] == 0


# ------------------------------------------------------- (f) determinism

def test_two_runs_produce_identical_bytes(tmp_path):
    bundle = _default_bundle()
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        originals=[_person('B000002', 'Rep. Brown')],
        cosponsors=[_person('C000003', 'Rep. Cole')],
    )])
    root = _build_root(tmp_path, bundle, repository)
    CRU.run(root, quiet=True)
    first_updates = (root / CRU.UPDATES_REL).read_bytes()
    first_pending = (root / CRU.PENDING_REL).read_bytes()
    CRU.run(root, quiet=True)
    assert (root / CRU.UPDATES_REL).read_bytes() == first_updates
    assert (root / CRU.PENDING_REL).read_bytes() == first_pending


def test_sidecar_is_regenerated_whole_not_appended(tmp_path):
    bundle = _default_bundle()
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        originals=[_person('B000002', 'Rep. Brown')],
        cosponsors=[_person('C000003', 'Rep. Cole')],
    )])
    root = _build_root(tmp_path, bundle, repository)
    CRU.run(root, quiet=True)
    assert len(_updates(root)) == 1
    # a stale row from an earlier cycle must not survive a regeneration
    path = root / CRU.UPDATES_REL
    path.write_text(path.read_text(encoding='utf-8') * 3, encoding='utf-8')
    assert len(_updates(root)) == 3
    CRU.run(root, quiet=True)
    assert len(_updates(root)) == 1


# ------------------------------------------------------ (g) fail-closed gate

def _refused(root):
    try:
        CRU.run(root, quiet=True)
    except CRU.VerificationRefused as exc:
        return str(exc)
    raise AssertionError('verifier did not refuse')


def test_missing_receipt_refuses_and_writes_nothing(tmp_path):
    bundle = _default_bundle()
    repository = _repository([_repo_bill(_person('A000001', 'Rep. Adams'))])
    root = _build_root(tmp_path, bundle, repository)
    (root / CRU.REFRESH_RECEIPT_REL).unlink()
    assert 'missing' in _refused(root)
    assert not (root / CRU.UPDATES_REL).exists()
    assert not (root / CRU.RECEIPT_REL).exists()
    assert CRU.main(['--root', str(root), '--quiet']) == 1


def test_failed_receipt_refuses_and_writes_nothing(tmp_path):
    bundle = _default_bundle()
    repository = _repository([_repo_bill(_person('A000001', 'Rep. Adams'))])
    root = _build_root(tmp_path, bundle, repository,
                       status='PARTIAL', failed=3)
    assert 'complete PASS' in _refused(root)
    assert not (root / CRU.UPDATES_REL).exists()
    assert CRU.main(['--root', str(root), '--quiet']) == 1


def test_hash_mismatched_repository_refuses_and_writes_nothing(tmp_path):
    bundle = _default_bundle()
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        cosponsors=[_person('C000003', 'Rep. Cole')],
    )])
    root = _build_root(tmp_path, bundle, repository)
    tampered = dict(repository)
    tampered['failures'] = {'119:hr:1919': 'tampered'}
    (root / 'bill_repository_live.json').write_text(
        json.dumps(tampered), encoding='utf-8')
    assert 'hash-bound' in _refused(root)
    assert not (root / CRU.UPDATES_REL).exists()
    assert CRU.main(['--root', str(root), '--quiet']) == 1


def test_refusal_leaves_the_previously_verified_sidecar_standing(tmp_path):
    bundle = _default_bundle()
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        originals=[_person('B000002', 'Rep. Brown')],
        cosponsors=[_person('C000003', 'Rep. Cole')],
    )])
    root = _build_root(tmp_path, bundle, repository)
    CRU.run(root, quiet=True)
    good = (root / CRU.UPDATES_REL).read_bytes()
    assert len(_updates(root)) == 1
    (root / CRU.REFRESH_RECEIPT_REL).unlink()
    _refused(root)
    assert (root / CRU.UPDATES_REL).read_bytes() == good


# --------------------------------------------------- (h) receipt is accurate

def test_receipt_counts_equal_the_rows_on_disk(tmp_path):
    bundle = _default_bundle()
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        originals=[_person('B000002', 'Rep. Brown')],
        cosponsors=[_person('C000003', 'Rep. Cole'),
                    _person('ZZ99999', 'Ghost Member')],
    )])
    root = _build_root(tmp_path, bundle, repository)
    document = CRU.run(root, quiet=True)
    on_disk = json.loads((root / CRU.RECEIPT_REL).read_text(encoding='utf-8'))
    assert on_disk == document
    assert document['counts']['accepted'] == len(_updates(root))
    assert document['counts']['queued'] == len(_pending(root))
    assert len(document['accepted']) == document['counts']['accepted']
    assert document['schema'] == CRU.RECEIPT_SCHEMA
    assert document['repository']['sha256'] == _updates(root)[0]['repository_sha256']
    # every accepted move is traceable to one receipt line
    assert document['accepted'] == [{
        'member': 'Cara Cole', 'bioguide': 'C000003', 'bill': BILL,
        'congress': CONGRESS, 'role': 'cosponsor', 'date': '2026-03-04',
    }]


# ------------------------------------------------- end-to-end ledger effect

def test_accepted_join_increments_the_desk_cosponsor_and_area_counts(tmp_path):
    bundle = _default_bundle()
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        originals=[_person('B000002', 'Rep. Brown')],
        cosponsors=[_person('C000003', 'Rep. Cole')],
    )])
    root = _build_root(tmp_path, bundle, repository)
    CRU.run(root, quiet=True)

    bills = _pre_collapse_ledger(bundle)
    before = _count(_collapse(bills.get('Cara Cole') or []))
    assert before == (0, 0, {})
    audit = CRU.merge_collector_roster_updates(
        str(root / CRU.UPDATES_REL), bills, _resolver(bundle),
        log=lambda *_a: None)
    after = _count(_collapse(bills['Cara Cole']))
    assert after == (1, 0, {'Payments': 1})
    # the sponsor's own ledger is untouched
    assert _count(_collapse(bills['Alice Adams'])) == (0, 1, {})
    # reconciliation identity the dashboard integrity check enforces
    assert audit['collector_roster_appended'] == 1
    assert audit['collector_roster_skipped'] == 0
    assert (audit['collector_roster_appended'] + audit['collector_roster_skipped']
            == audit['collector_roster_appended'])


def test_role_precedence_means_a_collector_row_can_never_demote(tmp_path):
    """Even a malformed sidecar claiming 'cosponsor' for a sponsor collapses
    back to sponsor: the ledger merge is additive by construction."""
    bundle = _default_bundle()
    bills = _pre_collapse_ledger(bundle)
    sidecar = tmp_path / 'updates.ndjson'
    sidecar.write_text(json.dumps({
        'bill': BILL, 'congress': CONGRESS, 'member': 'Alice Adams',
        'bioguide': 'A000001', 'role': 'cosponsor', 'areas': ['Payments'],
        'withdrawn': False,
    }) + '\n', encoding='utf-8')
    CRU.merge_collector_roster_updates(
        str(sidecar), bills, _resolver(bundle), log=lambda *_a: None)
    collapsed = _collapse(bills['Alice Adams'])
    assert len(collapsed) == 1
    assert collapsed[0]['role'] == 'sponsor'


# ------------------------------- the seed fingerprint both hard gates bind to

def test_merged_sidecar_leaves_the_seed_fingerprint_invariant(tmp_path):
    """make_member_desk:179 and audit_full:1311 hard-fail when
    seeds_from_member_bundle's fingerprint moves.  The joiner is FIRST in the
    members list, so after the merge her row becomes the seed's source row --
    the fingerprint only survives because accepted rows inherit the seed's
    title/sponsor/summary/action verbatim and a subset of its areas."""
    bundle = _default_bundle()
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        originals=[_person('B000002', 'Rep. Brown')],
        cosponsors=[_person('C000003', 'Rep. Cole')],
    )])
    root = _build_root(tmp_path, bundle, repository)
    CRU.run(root, quiet=True)
    before = seed_fingerprint(bundle)

    bills = _pre_collapse_ledger(bundle)
    CRU.merge_collector_roster_updates(
        str(root / CRU.UPDATES_REL), bills, _resolver(bundle),
        log=lambda *_a: None)
    rebuilt = {'members': [
        {**member, 'bills': _collapse(bills.get(member['name']) or [])}
        for member in bundle['members']
    ]}
    assert rebuilt['members'][0]['name'] == 'Cara Cole'
    assert rebuilt['members'][0]['bills'], 'joiner must now carry the act'
    assert seed_fingerprint(rebuilt) == before


def test_a_non_idempotent_summary_is_queued_rather_than_accepted(tmp_path):
    """A summary the desk would re-truncate cannot be accepted: it would move
    the seed fingerprint and break the receipt binding."""
    long_summary = 'x' * (CRU.SUMMARY_WIDTH + 40)
    bundle = {'members': [
        _member('Cara Cole', 'C000003', []),
        _member('Alice Adams', 'A000001',
                [_ledger_row('sponsor', summary=long_summary)]),
    ]}
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        cosponsors=[_person('C000003', 'Rep. Cole')],
    )])
    root = _build_root(tmp_path, bundle, repository)
    CRU.run(root, quiet=True)
    assert _updates(root) == []
    assert [row['reason'] for row in _pending(root)] == [
        'summary_truncation_not_idempotent']


# ---------------------------------------------------------- chain wiring

def test_producer_is_wired_into_both_chains():
    weekly = open(os.path.join(FEC, 'weekly_refresh.py'), encoding='utf-8').read()
    pipeline = open(os.path.join(FEC, 'run_pipeline.py'), encoding='utf-8').read()
    desk = open(os.path.join(FEC, 'surfaces', 'make_member_desk.py'),
                encoding='utf-8').read()
    integrity = open(os.path.join(FEC, 'audit', 'dashboard_integrity.py'),
                     encoding='utf-8').read()
    assert "'legislation/verify_collector_roster_updates.py'," in weekly
    assert weekly.index('update_bill_repository.py') < weekly.index(
        'verify_collector_roster_updates.py'), 'gate must follow the scrape'
    assert "('verify_collector_roster_updates.py',[])" in pipeline
    assert pipeline.index('verify_collector_roster_updates.py') < pipeline.index(
        "('compute_pillars.py'"), 'gate must precede compute_pillars'
    assert "'verify_collector_roster_updates.py':(" in pipeline
    assert 'CRU.merge_collector_roster_updates(' in desk
    assert 'collector_roster_updates.ndjson' in desk
    # the sidecar's rows must be declared to the legislation reconciliation
    assert "bill_collector_roster_rows" in integrity
    assert "'bill_collector_roster_excluded'" in integrity


def test_verifier_makes_no_network_calls():
    source = open(os.path.join(FEC, 'legislation',
                               'verify_collector_roster_updates.py'),
                  encoding='utf-8').read()
    for token in ('requests', 'urllib', 'http.client', 'socket',
                  'subprocess', 'playwright'):
        assert 'import %s' % token not in source, token


def test_a_duplicated_roster_entry_yields_one_row(tmp_path):
    """A member listed twice (or listed as both sponsor and cosponsor) gets one
    decision per bill; the sponsor branch always wins."""
    bundle = _default_bundle()
    dupe = _repo_bill(
        _person('A000001', 'Rep. Adams'),
        originals=[_person('B000002', 'Rep. Brown')],
        cosponsors=[_person('C000003', 'Rep. Cole'),
                    _person('C000003', 'Rep. Cole'),
                    _person('A000001', 'Rep. Adams')],
    )
    root = _build_root(tmp_path, bundle, _repository([dupe]))
    CRU.run(root, quiet=True)
    rows = _updates(root)
    assert len(rows) == 1
    assert rows[0]['member'] == 'Cara Cole'
    assert all(row['member'] != 'Alice Adams' for row in rows)


# ------------------------------------ (f) adjudicated per-member withdrawals

WITHDRAWAL_HEADER = ('Bill,Congress,Member,Bioguide,JoinDate,WithdrawnDate,'
                     'Source,Note\n')


def _withdrawal_csv(bill=BILL, congress=CONGRESS, member='Bob Brown',
                    bioguide='B000002', join='2026-01-05',
                    withdrawn='2026-04-01'):
    return WITHDRAWAL_HEADER + '%s,%s,%s,%s,%s,%s,test,adjudicated\n' % (
        bill, congress, member, bioguide, join, withdrawn)


def _desk_withdrawal_pass(bills, rules, resolve):
    """Mirror make_member_desk's withdrawal pass over a pre-collapse ledger."""
    suppress = {}
    for rule in rules:
        name = resolve(rule['member'], rule['bioguide'] or None)
        assert name, 'unresolvable member must raise in the desk'
        suppress.setdefault((name, rule['key']), 0)
    removed = 0
    for name in list(bills):
        kept = []
        for row in bills[name]:
            key = (name, BR.withdrawal_act_key(row['bill'], row['congress']))
            if key in suppress:
                suppress[key] += 1
                removed += 1
                continue
            kept.append(row)
        bills[name] = kept
    return removed, sum(1 for count in suppress.values() if not count)


def test_withdrawal_removes_only_that_members_row(tmp_path):
    """The whole point: bill_overrides.csv is keyed (bill, congress) only, so it
    could only drop the act for EVERY member. A withdrawal is per-member."""
    path = tmp_path / 'membership_withdrawals.csv'
    path.write_text(_withdrawal_csv(), encoding='utf-8')
    rules = BR.load_membership_withdrawals(str(path))
    assert len(rules) == 1

    bundle = _default_bundle()
    bills = _pre_collapse_ledger(bundle)
    assert [row['role'] for row in bills['Bob Brown']] == ['original']
    removed, absent = _desk_withdrawal_pass(bills, rules, _resolver(bundle))

    assert removed == 1 and absent == 0
    assert bills['Bob Brown'] == []
    # the sponsor on the very same act is untouched
    assert [row['role'] for row in bills['Alice Adams']] == ['sponsor']


def test_withdrawal_matching_nothing_is_counted_not_raised(tmp_path):
    """A rule for a join the ledger never carried (the item-6 shape) is the
    CORRECT steady state, and is also what every rule looks like on the second
    and every later build. It must never halt the desk."""
    path = tmp_path / 'membership_withdrawals.csv'
    path.write_text(_withdrawal_csv(member='Cara Cole', bioguide='C000003'),
                    encoding='utf-8')
    rules = BR.load_membership_withdrawals(str(path))
    bundle = _default_bundle()
    bills = _pre_collapse_ledger(bundle)
    removed, absent = _desk_withdrawal_pass(bills, rules, _resolver(bundle))
    assert removed == 0 and absent == 1


def test_withdrawal_is_idempotent_across_rebuilds(tmp_path):
    path = tmp_path / 'membership_withdrawals.csv'
    path.write_text(_withdrawal_csv(), encoding='utf-8')
    rules = BR.load_membership_withdrawals(str(path))
    bundle = _default_bundle()
    bills = _pre_collapse_ledger(bundle)
    first = _desk_withdrawal_pass(bills, rules, _resolver(bundle))
    second = _desk_withdrawal_pass(bills, rules, _resolver(bundle))
    assert first == (1, 0)
    assert second == (0, 1)          # nothing left to remove, nothing raised


def test_withdrawal_removal_keeps_the_legislation_reconciliation_balanced(
        tmp_path):
    """dashboard_integrity requires source_rows == accounted_rows. Removing the
    N pre-collapse rows of ONE membership drops bills_displayed_unique by 1 and
    bill_duplicate_rows_collapsed by N-1, so the new exclusion term must be N."""
    path = tmp_path / 'membership_withdrawals.csv'
    path.write_text(_withdrawal_csv(), encoding='utf-8')
    rules = BR.load_membership_withdrawals(str(path))
    # Bob holds the act under TWO areas and TWO roles -> 3 pre-collapse rows
    bundle = {'members': [
        _member('Alice Adams', 'A000001', [_ledger_row('sponsor')]),
        _member('Bob Brown', 'B000002', [
            _ledger_row('original', areas=('Payments', 'Interchange'))]),
    ]}
    bills = _pre_collapse_ledger(bundle)
    bills['Bob Brown'].append(
        dict(_ledger_row('cosponsor'), area='Payments', areas=None))
    for row in bills['Bob Brown']:
        row.pop('areas', None)
    source_rows = sum(len(v) for v in bills.values())

    before_collapse = sum(len(v) for v in bills.values())
    collapsed_before = {n: _collapse(v) for n, v in bills.items()}
    displayed_before = sum(len(v) for v in collapsed_before.values())
    dup_before = before_collapse - displayed_before
    assert source_rows == displayed_before + dup_before      # baseline balances

    removed, _ = _desk_withdrawal_pass(bills, rules, _resolver(bundle))
    assert removed == 3
    collapsed_after = {n: _collapse(v) for n, v in bills.items()}
    displayed_after = sum(len(v) for v in collapsed_after.values())
    dup_after = sum(len(v) for v in bills.values()) - displayed_after

    assert displayed_after == displayed_before - 1
    assert dup_after == dup_before - 2
    # source rows are unchanged; the removal must be fully accounted for
    assert source_rows == displayed_after + dup_after + removed


def test_withdrawal_act_key_is_type_aware_and_congress_normalized():
    assert BR.withdrawal_act_key('H.R.1181', 119) != BR.withdrawal_act_key(
        'H.Res.1181', 119)
    # the ledger stores congress as str/int/float depending on the source sheet
    assert (BR.withdrawal_act_key('S.2669', '118')
            == BR.withdrawal_act_key('S.2669', 118)
            == BR.withdrawal_act_key('s. 2669', 118.0))
    # a non-bill evidence identifier keys literally instead of matching nothing
    assert BR.withdrawal_act_key('LTR-119-CDFI-KIMCRAPO', '119') == (
        'LTR-119-CDFI-KIMCRAPO', '119')
    assert BR.withdrawal_act_key('', '119') is None


def test_absent_withdrawal_file_is_a_no_op(tmp_path):
    assert BR.load_membership_withdrawals(str(tmp_path / 'nope.csv')) == []


def test_a_malformed_withdrawal_row_fails_closed(tmp_path):
    """Fail CLOSED: a half-written adjudication must halt, never fail open into
    keeping the membership it was written to remove."""
    for body, needle in (
        (WITHDRAWAL_HEADER + ',119,Bob Brown,B000002,,2026-04-01,t,n', 'act'),
        (WITHDRAWAL_HEADER + 'H.R.1919,,Bob Brown,B000002,,2026-04-01,t,n',
         'act'),
        (WITHDRAWAL_HEADER + 'H.R.1919,119,,B000002,,2026-04-01,t,n', 'Member'),
        (WITHDRAWAL_HEADER + 'H.R.1919,119,Bob Brown,B000002,,,t,n',
         'WithdrawnDate'),
    ):
        path = tmp_path / 'bad.csv'
        path.write_text(body + '\n', encoding='utf-8')
        with pytest.raises(ValueError) as excinfo:
            BR.load_membership_withdrawals(str(path))
        assert needle in str(excinfo.value)


def test_unknown_columns_stay_additive(tmp_path):
    path = tmp_path / 'w.csv'
    path.write_text(
        WITHDRAWAL_HEADER.rstrip('\n') + ',FutureColumn\n'
        'H.R.1919,119,Bob Brown,B000002,2026-01-05,2026-04-01,t,n,ignored\n',
        encoding='utf-8')
    rules = BR.load_membership_withdrawals(str(path))
    assert len(rules) == 1 and rules[0]['withdrawn_date'] == '2026-04-01'


# ------------------------------------------- (g) the queue actually DRAINS

def test_withdrawn_join_absent_from_ledger_drains_once_adjudicated(tmp_path):
    """Item-6 shape: withdrawn, correctly absent from the ledger. Without an
    adjudication this question is re-asked forever."""
    bundle = _default_bundle()
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        originals=[_person('B000002', 'Rep. Brown')],
        cosponsors=[_person('C000003', 'Rep. Cole', withdrawn='2026-04-01')],
    )])

    root = _build_root(tmp_path, bundle, repository)
    CRU.run(root, quiet=True)
    assert 'withdrawn_join_absent_from_ledger' in {
        row['reason'] for row in _pending(root)}

    root2 = _build_root(
        tmp_path / 'b', bundle, repository,
        withdrawals_csv=_withdrawal_csv(member='Cara Cole',
                                        bioguide='C000003'))
    document = CRU.run(root2, quiet=True)
    assert 'withdrawn_join_absent_from_ledger' not in {
        row['reason'] for row in _pending(root2)}
    assert document['counts']['withdrawal_adjudicated'] == 1
    assert document['counts']['withdrawal_rules'] == 1
    assert _updates(root2) == []


def test_withdrawn_on_existing_ledger_row_drains_once_adjudicated(tmp_path):
    """Item-7 shape. Suppressing the ledger row alone would only RENAME the
    question, so the adjudication has to silence both reasons."""
    bundle = _default_bundle()
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        originals=[_person('B000002', 'Rep. Brown', withdrawn='2026-04-01')],
    )])

    root = _build_root(tmp_path, bundle, repository)
    CRU.run(root, quiet=True)
    assert 'withdrawn_on_existing_ledger_row' in {
        row['reason'] for row in _pending(root)}

    root2 = _build_root(tmp_path / 'b', bundle, repository,
                        withdrawals_csv=_withdrawal_csv())
    document = CRU.run(root2, quiet=True)
    reasons = {row['reason'] for row in _pending(root2)}
    assert 'withdrawn_on_existing_ledger_row' not in reasons
    assert 'withdrawn_join_absent_from_ledger' not in reasons
    assert document['counts']['withdrawal_adjudicated'] == 1
    assert document['withdrawal_adjudications'] == [{
        'bill': BILL, 'congress': CONGRESS, 'member': 'Bob Brown',
        'bioguide': 'B000002', 'withdrawn_date': '2026-04-01'}]


def test_a_stale_withdrawal_adjudication_is_queued_and_never_accepted(tmp_path):
    """The converse guard. If the live roster no longer shows the withdrawal
    (the member re-joined), accepting would emit a sidecar row that the desk's
    withdrawal pass then silently drops. Escalate instead."""
    bundle = {'members': [
        _member('Cara Cole', 'C000003', []),
        _member('Alice Adams', 'A000001', [_ledger_row('sponsor')]),
    ]}
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        cosponsors=[_person('C000003', 'Rep. Cole')],      # NOT withdrawn now
    )])
    root = _build_root(tmp_path, bundle, repository,
                       withdrawals_csv=_withdrawal_csv(member='Cara Cole',
                                                       bioguide='C000003'))
    document = CRU.run(root, quiet=True)
    assert _updates(root) == [], 'a stale rule must block acceptance'
    assert [row['reason'] for row in _pending(root)] == [
        'withdrawal_adjudication_stale']
    assert document['counts']['withdrawal_adjudication_stale'] == 1
    assert document['counts']['withdrawal_adjudicated'] == 0


def test_withdrawal_matching_is_bioguide_strict(tmp_path):
    """A rule that names a bioguide matches on that bioguide alone, so it can
    never claim a different member who happens to share a display name."""
    bundle = _default_bundle()
    repository = _repository([_repo_bill(
        _person('A000001', 'Rep. Adams'),
        originals=[_person('B000002', 'Rep. Brown', withdrawn='2026-04-01')],
    )])
    # right member NAME, wrong bioguide -> must NOT silence the queue
    root = _build_root(tmp_path, bundle, repository,
                       withdrawals_csv=_withdrawal_csv(bioguide='Z999999'))
    document = CRU.run(root, quiet=True)
    assert 'withdrawn_on_existing_ledger_row' in {
        row['reason'] for row in _pending(root)}
    assert document['counts']['withdrawal_adjudicated'] == 0


def test_a_malformed_withdrawal_file_refuses_the_whole_run(tmp_path):
    bundle = _default_bundle()
    repository = _repository([_repo_bill(_person('A000001', 'Rep. Adams'))])
    root = _build_root(
        tmp_path, bundle, repository,
        withdrawals_csv=WITHDRAWAL_HEADER + 'H.R.1919,119,Bob,B2,,,t,n\n')
    with pytest.raises(CRU.VerificationRefused) as excinfo:
        CRU.run(root, quiet=True)
    assert 'malformed' in str(excinfo.value)
    # last-good semantics: no sidecar was written
    assert not (root / CRU.UPDATES_REL).exists()


def test_withdrawals_do_not_perturb_the_seed_fingerprint(tmp_path):
    """A withdrawal removes a MEMBER's row, never a seed: the act keeps other
    holders, and title/sponsor/summary/action come from the first holder."""
    path = tmp_path / 'w.csv'
    path.write_text(_withdrawal_csv(), encoding='utf-8')
    rules = BR.load_membership_withdrawals(str(path))
    bundle = _default_bundle()
    before = seed_fingerprint(bundle)
    bills = _pre_collapse_ledger(bundle)
    _desk_withdrawal_pass(bills, rules, _resolver(bundle))
    rebuilt = {'members': [
        {'name': m['name'], 'bio': m['bio'],
         'bills': _collapse(bills.get(m['name']) or [])}
        for m in bundle['members']]}
    assert seed_fingerprint(rebuilt) == before


def test_withdrawal_pass_is_wired_after_every_source_and_before_the_collapse():
    desk = open(os.path.join(FEC, 'surfaces', 'make_member_desk.py'),
                encoding='utf-8').read()
    integrity = open(os.path.join(FEC, 'audit', 'dashboard_integrity.py'),
                     encoding='utf-8').read()
    assert 'BR.load_membership_withdrawals(' in desk
    assert 'membership_withdrawals.csv' in desk
    # must run AFTER the collector merge (the last source) ...
    assert desk.index('CRU.merge_collector_roster_updates(') < desk.index(
        'BR.load_membership_withdrawals('), 'withdrawals must win over sources'
    # ... and BEFORE the collapse, so the suppression cannot be undone
    assert desk.index('BR.load_membership_withdrawals(') < desk.index(
        "order = {'sponsor': 0, 'original': 1, 'cosponsor': 2}")
    # the removed rows must carry an explicit reconciliation exclusion term
    assert "'bill_withdrawn_rows_removed'" in integrity
    # and the new input must be fingerprinted, or a withdrawal-only change
    # would look like same-input/different-bundle corruption
    assert "'data/legislation/membership_withdrawals.csv'," in integrity
