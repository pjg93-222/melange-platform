"""Real verifier/history/merge cycles must preserve accepted memberships."""
import ast
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import textwrap

import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from legislation import verify_collector_roster_updates as CRU
from legislation import sync_sentinel_bill_ledger as HISTORY
from legislation import official_membership_movements as OM
from legislation import sentinel_admissions as SA
from members.member_identity import Resolver
import bill_rules as BR

spec = importlib.util.spec_from_file_location(
    'collector_fixture_helpers', FEC/'tests/legislation/test_collector_roster_updates.py')
FIX = importlib.util.module_from_spec(spec)
spec.loader.exec_module(FIX)
source = (FEC/'surfaces/make_member_desk.py').read_text(encoding='utf-8')
tree = ast.parse(source)
names = {'load_verified_membership_history', 'shape_cumulative_membership_rows'}
nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
namespace = {}
exec(compile(ast.Module(body=nodes, type_ignores=[]), '<actual desk helpers>', 'exec'), namespace)
READ = namespace['load_verified_membership_history']
SHAPE = namespace['shape_cumulative_membership_rows']


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def setup_root(tmp_path, join=True):
    base = FIX._default_bundle()
    repository = FIX._repository([FIX._repo_bill(
        FIX._person('A000001', 'Rep. Adams'),
        originals=[FIX._person('B000002', 'Rep. Brown')],
        cosponsors=[FIX._person('C000003', 'Rep. Cole')] if join else [])])
    root = FIX._build_root(tmp_path, base, repository)
    return root, base


def materialize(root):
    return HISTORY.materialize_verified_history(
        root=root,
        history_path=root/'data/members/sentinel_verified_membership_history.ndjson',
        updates_path=root/CRU.UPDATES_REL,
        bootstrap_path=root/'absent_bootstrap.ndjson',
        receipt_path=root/'data/engine/sentinel_verified_membership_history_receipt.json')


def rebuild(root, base, *, cumulative=True):
    verified_sponsors = None
    if cumulative:
        rows, _ = READ(root)
        path = root/'shaped_for_desk.ndjson'
        shaped = SHAPE(rows, roles=('sponsor', 'original', 'cosponsor'))
        path.write_text(''.join(json.dumps(row)+'\n' for row in shaped), encoding='utf-8')
        repository = json.loads((root/CRU.REPOSITORY_REL).read_text(encoding='utf-8'))
        verified_sponsors = {
            (key, sponsor['bioguide']) for key, bill in repository['bills'].items()
            for sponsor in [bill.get('sponsor') or {}]
            if sponsor.get('bioguide') and not sponsor.get('withdrawn_date')
        }
    else:
        path = root/CRU.UPDATES_REL
    bills = FIX._pre_collapse_ledger(base)
    resolver = Resolver([m['name'] for m in base['members']],
                        {m['name']: m['bio']['bioguide'] for m in base['members']})
    CRU.merge_collector_roster_updates(path, bills, resolver.resolve,
                                     verified_sponsors=verified_sponsors, log=lambda *_: None)
    result = copy.deepcopy(base)
    for member in result['members']:
        member['bills'] = FIX._collapse(bills.get(member['name'], []))
    return result, bills


@pytest.fixture
def published(tmp_path):
    root, base = setup_root(tmp_path)
    CRU.run(root, quiet=True)
    materialize(root)
    return root, base


def test_three_real_refresh_cycles_converge_while_delta_negative_control_loses_join(tmp_path):
    root, base = setup_root(tmp_path)
    bundles = []
    accepted_counts = []
    for cycle in range(3):
        if bundles:
            write_json(root/CRU.BUNDLE_REL, bundles[-1])
        receipt = CRU.run(root, quiet=True)
        accepted_counts.append(receipt['counts']['accepted'])
        history, _ = materialize(root)
        assert len(history) == 1
        current, _ = rebuild(root, base)
        bundles.append(current)
        if cycle == 1:
            delta_only, _ = rebuild(root, base, cumulative=False)
            cara = next(member for member in delta_only['members'] if member['name'] == 'Cara Cole')
            assert cara['bills'] == []  # Actual old path loses the already-accepted join.
            assert receipt['counts']['previously_accepted_absorbed_by_bundle'] == 1
    assert accepted_counts == [1, 0, 0]
    assert bundles[0] == bundles[1] == bundles[2]
    cara = next(member for member in bundles[2]['members'] if member['name'] == 'Cara Cole')
    assert len(cara['bills']) == 1 and cara['bills'][0]['role'] == 'cosponsor'


@pytest.mark.parametrize('relative', [
    'data/members/sentinel_verified_membership_history.ndjson',
    'data/engine/sentinel_verified_membership_history_receipt.json',
    CRU.UPDATES_REL, CRU.RECEIPT_REL, CRU.REPOSITORY_REL])
def test_missing_source_never_becomes_empty_evidence(published, relative):
    root, _ = published
    (root/relative).unlink()
    with pytest.raises(RuntimeError, match='require'):
        READ(root)


def test_bound_history_does_not_skip_malformed_lines(published):
    root, _ = published
    path = root/'data/members/sentinel_verified_membership_history.ndjson'
    with path.open('a', encoding='utf-8') as stream:
        stream.write('{malformed\n')
    with pytest.raises(ValueError):
        READ(root)


@pytest.mark.parametrize('mutation', ['hash', 'count', 'status', 'provenance'])
def test_history_receipt_must_bind_hash_counts_status_and_admissions(published, mutation):
    root, _ = published
    path = root/'data/engine/sentinel_verified_membership_history_receipt.json'
    receipt = json.loads(path.read_text(encoding='utf-8'))
    if mutation == 'hash': receipt['history']['sha256'] = '0'*64
    elif mutation == 'count': receipt['history']['verified_memberships'] += 1
    elif mutation == 'status': receipt['status'] = 'FAIL'
    else:
        receipt['source_receipts'] = []
        receipt['bootstrap_sources'] = []
        receipt['current_delta'] = {}
    write_json(path, receipt)
    with pytest.raises(RuntimeError): READ(root)


def test_empty_set_requires_its_own_validated_zero_count_receipt(tmp_path):
    root, _ = setup_root(tmp_path, join=False)
    CRU.run(root, quiet=True)
    materialize(root)
    rows, binding = READ(root)
    assert rows == [] and binding['verified_memberships'] == 0
    receipt_path = root/'data/engine/sentinel_verified_membership_history_receipt.json'
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    receipt['history']['sha256'] = '0'*64
    write_json(receipt_path, receipt)
    with pytest.raises(RuntimeError, match='zero-count'): READ(root)


def test_history_cannot_lag_the_next_verified_delta(published):
    root, base = published
    bundle, _ = rebuild(root, base)
    write_json(root/CRU.BUNDLE_REL, bundle)
    CRU.run(root, quiet=True)  # The current delta is empty; history receipt still binds the prior one.
    with pytest.raises(RuntimeError, match='stale or unbound'): READ(root)
    materialize(root)
    assert len(READ(root)[0]) == 1


@pytest.mark.parametrize('mutation', ['accepted_identity', 'repository', 'age', 'history_before_verifier'])
def test_stale_or_unverified_current_generation_is_refused(published, mutation):
    root, _ = published
    path = root/CRU.RECEIPT_REL
    receipt = json.loads(path.read_text(encoding='utf-8'))
    if mutation == 'accepted_identity': receipt['accepted'][0]['member'] = 'Other Person'
    elif mutation == 'repository': receipt['repository']['sha256'] = '0'*64
    elif mutation == 'age':
        receipt['generated_utc'] = (datetime.now(timezone.utc)-timedelta(days=9)).isoformat()
    else:
        history_path = root/'data/engine/sentinel_verified_membership_history_receipt.json'
        history = json.loads(history_path.read_text(encoding='utf-8'))
        history['completed_at'] = (datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
        write_json(history_path, history)
    write_json(path, receipt)
    with pytest.raises(RuntimeError): READ(root)


@pytest.mark.parametrize('mutation', ['membership', 'category'])
def test_current_delta_must_actually_be_carried_not_merely_named_in_receipt(published, mutation):
    root, _ = published
    path = root/'data/members/sentinel_verified_membership_history.ndjson'
    rows = HISTORY.load_ndjson(path)
    if mutation == 'membership':
        rows = []
    else:
        rows[0]['areas'] = ['Fraud Prevention']
        rows[0]['area_admissions'][0]['area'] = 'Fraud Prevention'
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows), encoding='utf-8')
    receipt_path = root/'data/engine/sentinel_verified_membership_history_receipt.json'
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    metrics = HISTORY.validate_history_rows(rows)
    receipt['history'].update(sha256=HISTORY.sha256(path), **metrics)
    write_json(receipt_path, receipt)
    with pytest.raises(RuntimeError, match='omits a current accepted membership or category'):
        READ(root)


def test_category_case_changes_follow_the_history_writers_identity_rule(tmp_path):
    root, _ = setup_root(tmp_path)
    CRU.run(root, quiet=True)
    path = root/CRU.UPDATES_REL
    updates = HISTORY.load_ndjson(path)
    updates[0]['areas'] = ['Unmapped Fixture Area']
    path.write_text(''.join(json.dumps(row)+'\n' for row in updates), encoding='utf-8')
    materialize(root)
    updates[0]['areas'] = ['UNMAPPED FIXTURE AREA']
    path.write_text(''.join(json.dumps(row)+'\n' for row in updates), encoding='utf-8')
    materialize(root)
    rows, _ = READ(root)
    assert rows[0]['areas'] == ['Unmapped Fixture Area']


@pytest.mark.parametrize('field', ['bill', 'congress', 'member', 'bioguide', 'repository_key'])
def test_verified_rows_cannot_silently_lose_required_desk_identity(published, field):
    root, _ = published
    path = root/'data/members/sentinel_verified_membership_history.ndjson'
    rows = HISTORY.load_ndjson(path)
    rows[0][field] = ''
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows), encoding='utf-8')
    receipt_path = root/'data/engine/sentinel_verified_membership_history_receipt.json'
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    receipt['history']['sha256'] = HISTORY.sha256(path)
    write_json(receipt_path, receipt)
    with pytest.raises(RuntimeError): READ(root)


@pytest.mark.parametrize('field,value', [('bill', 'H.R.9999'), ('congress', '118')])
def test_bill_display_must_agree_with_verified_repository_identity(published, field, value):
    root, _ = published
    path = root/'data/members/sentinel_verified_membership_history.ndjson'
    rows = HISTORY.load_ndjson(path)
    rows[0][field] = value
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows), encoding='utf-8')
    receipt_path = root/'data/engine/sentinel_verified_membership_history_receipt.json'
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    receipt['history']['sha256'] = HISTORY.sha256(path)
    write_json(receipt_path, receipt)
    with pytest.raises(RuntimeError, match='disagrees with its repository key'): READ(root)


def test_enabled_official_movements_cannot_silently_disappear(published):
    root, _ = published
    write_json(root/'data/engine/sentinel_admission_policy.json', {'automatic_movements': True})
    with pytest.raises(RuntimeError, match='official movement snapshot'): READ(root)


@pytest.mark.parametrize('withdrawn,role', [(True, 'cosponsor'), (False, 'original')])
def test_current_official_withdrawal_or_role_correction_overrules_historical_join(published, withdrawn, role):
    root, base = published
    _, bills = rebuild(root, base)
    assert len(bills['Cara Cole']) == 1
    facts = {'facts': [{'repository_key': FIX.REPO_KEY, 'member': 'Cara Cole',
                        'bioguide': 'C000003', 'withdrawn': withdrawn, 'role': role}]}
    assert OM.apply_to_desk(bills, facts) == 1
    assert bills['Cara Cole'] == []


def test_managed_admission_exclusion_removes_a_historical_join(published):
    root, base = published
    _, bills = rebuild(root, base)
    document = {'bill_catalog': {FIX.REPO_KEY: {'display': FIX.BILL, 'congress': 119}},
                'bills': {}, 'memberships': []}
    assert SA.remove_managed_desk_rows(bills, document) == 3
    assert SA.append_to_desk(bills, document) == 0
    assert all(not rows for rows in bills.values())


@pytest.mark.parametrize('change', ['downgrade', 'withdrawal'])
def test_real_official_refresh_overrides_older_history_after_role_change(tmp_path, change):
    base = FIX._default_bundle()
    first_repo = FIX._repository([FIX._repo_bill(
        FIX._person('A000001', 'Rep. Adams'),
        originals=[FIX._person('B000002', 'Rep. Brown'), FIX._person('C000003', 'Rep. Cole')])])
    root = FIX._build_root(tmp_path, base, first_repo)
    CRU.run(root, quiet=True)
    materialize(root)
    first_bundle, _ = rebuild(root, base)
    assert next(member for member in first_bundle['members']
                if member['name'] == 'Cara Cole')['bills'][0]['role'] == 'original'
    second_repo = FIX._repository([FIX._repo_bill(
        FIX._person('A000001', 'Rep. Adams'),
        originals=[FIX._person('B000002', 'Rep. Brown')],
        cosponsors=[FIX._person('C000003', 'Rep. Cole',
                               withdrawn='2026-09-19' if change == 'withdrawal' else '')])])
    FIX._build_root(tmp_path, first_bundle, second_repo)
    write_json(root/'data/engine/sentinel_admission_policy.json', {'automatic_movements': True})
    CRU.run(root, quiet=True)
    materialize(root)
    _, bills = rebuild(root, base)
    facts = OM.load(root)
    OM.apply_to_desk(bills, facts)
    current = FIX._collapse(bills['Cara Cole'])
    if change == 'withdrawal':
        assert current == []
    else:
        assert len(current) == 1 and current[0]['role'] == 'cosponsor'



def sponsor_root(tmp_path):
    """The act is already in scope through Bob; Alice's official sponsorship is missing."""
    base = FIX._default_bundle()
    next(m for m in base['members'] if m['name'] == 'Alice Adams')['bills'] = []
    repository = FIX._repository([FIX._repo_bill(
        FIX._person('A000001', 'Rep. Adams'),
        originals=[FIX._person('B000002', 'Rep. Brown')])])
    root = FIX._build_root(tmp_path, base, repository)
    write_json(root/'data/engine/sentinel_admission_policy.json', {'automatic_movements': True})
    CRU.run(root, quiet=True)
    materialize(root)
    assert [(row['member'], row['role']) for row in READ(root)[0]] == [('Alice Adams', 'sponsor')]
    return root, base


def finish_memberships(root, bills, base, admissions=None):
    """Run actual final production passes, including the source's withdrawal/collapse block."""
    admissions = admissions or {'bill_catalog': {}, 'bills': {}, 'memberships': []}
    replaced = SA.remove_managed_desk_rows(bills, admissions)
    SA.append_to_desk(bills, admissions)
    removed = OM.apply_to_desk(bills, OM.load(root)) + replaced
    resolver = Resolver([m['name'] for m in base['members']],
                        {m['name']: m['bio']['bioguide'] for m in base['members']})
    block = source[source.index('    _withdrawals = BR.load_membership_withdrawals('):
                   source.index('    # --- committee positions')]
    context = {'BR': BR, 'B': str(root), 'os': os, 'bills': bills,
               'canon': resolver.resolve, 'media_audit': {}, '_movement_removed': removed,
               'print': lambda *_: None}
    exec(compile(textwrap.dedent(block), '<actual withdrawal and collapse passes>', 'exec'), context)
    return context['media_audit']


def test_verified_in_scope_sponsor_survives_three_real_refreshes_but_delta_stays_legacy(tmp_path):
    root, base = sponsor_root(tmp_path)
    bundles, counts = [], []
    for cycle in range(3):
        if cycle:
            FIX._build_root(tmp_path, bundles[-1], json.loads((root/CRU.REPOSITORY_REL).read_text()))
            CRU.run(root, quiet=True)
            materialize(root)
        counts.append(len(FIX._updates(root)))
        _, bills = rebuild(root, base)
        finish_memberships(root, bills, base)
        assert len(bills['Alice Adams']) == 1 and bills['Alice Adams'][0]['role'] == 'sponsor'
        bundle = copy.deepcopy(base)
        for member in bundle['members']:
            member['bills'] = bills.get(member['name'], [])
        bundles.append(bundle)
        if cycle == 0:
            legacy, _ = rebuild(root, base, cumulative=False)
            assert next(m for m in legacy['members'] if m['name'] == 'Alice Adams')['bills'] == []
    assert counts == [1, 0, 0]
    assert bundles[0] == bundles[1] == bundles[2]


@pytest.mark.parametrize('barrier,reason', [
    ('default', 'role'), ('current_sponsor_mismatch', 'role'),
    ('unknown_act', 'unknown_act'), ('excluded', 'excluded'),
    ('withdrawn', 'withdrawn'), ('unknown_bioguide', 'unknown_member')])
def test_opted_in_sponsors_preserve_every_admission_boundary(tmp_path, barrier, reason):
    root, base = sponsor_root(tmp_path)
    [row] = SHAPE(READ(root)[0], roles=('sponsor', 'original', 'cosponsor'))
    verified = {(FIX.REPO_KEY, 'A000001')}
    if barrier == 'default': verified = None
    elif barrier == 'current_sponsor_mismatch': verified = {(FIX.REPO_KEY, 'C000003')}
    elif barrier == 'unknown_act':
        row.update(bill='H.R.4242', repository_key='119:hr:4242')
        verified = {('119:hr:4242', 'A000001')}
    elif barrier == 'withdrawn': row['withdrawn'] = True
    elif barrier == 'unknown_bioguide':
        row['bioguide'] = 'Z999999'  # A correct display name may not rescue a wrong identity.
        verified = {(FIX.REPO_KEY, 'Z999999')}
    path = root/'sponsor_boundary.ndjson'
    path.write_text(json.dumps(row)+'\n', encoding='utf-8')
    bills = FIX._pre_collapse_ledger(base)
    resolver = Resolver([m['name'] for m in base['members']],
                        {m['name']: m['bio']['bioguide'] for m in base['members']})
    audit = CRU.merge_collector_roster_updates(
        path, bills, resolver.resolve, verified_sponsors=verified,
        exclude=lambda *_: barrier == 'excluded', log=lambda *_: None)
    assert audit['collector_roster_skipped_'+reason] == 1
    assert audit['collector_roster_skipped'] == 1
    assert audit['collector_roster_appended'] == 0
    assert not bills.get('Alice Adams')


@pytest.mark.parametrize('change', ['no_longer_sponsor', 'role_correction', 'withdrawal'])
def test_current_official_fact_overrules_old_sponsor_history(tmp_path, change):
    root, base = sponsor_root(tmp_path)
    current, _ = rebuild(root, base)
    if change == 'withdrawal':
        sponsor = FIX._person('A000001', 'Rep. Adams', withdrawn='2026-09-19')
        cosponsors = []
    else:
        sponsor = FIX._person('C000003', 'Rep. Cole')
        cosponsors = [FIX._person('A000001', 'Rep. Adams')] if change == 'role_correction' else []
    repository = FIX._repository([FIX._repo_bill(sponsor,
        originals=[FIX._person('B000002', 'Rep. Brown')], cosponsors=cosponsors)])
    FIX._build_root(tmp_path, current, repository)
    CRU.run(root, quiet=True)
    materialize(root)
    assert any(row['member'] == 'Alice Adams' and row['role'] == 'sponsor' for row in READ(root)[0])
    _, bills = rebuild(root, base)
    finish_memberships(root, bills, base)
    alice = bills.get('Alice Adams', [])
    if change == 'role_correction':
        assert len(alice) == 1 and alice[0]['role'] == 'cosponsor'
    else:
        assert alice == []


@pytest.mark.parametrize('included', [True, False])
def test_managed_admission_remains_authoritative_without_duplicate_sponsor_credit(tmp_path, included):
    root, base = sponsor_root(tmp_path)
    [row] = SHAPE(READ(root)[0], roles=('sponsor', 'original', 'cosponsor'))
    _, bills = rebuild(root, base)
    assert len(bills['Alice Adams']) == 1
    catalog = {FIX.REPO_KEY: {'display': FIX.BILL, 'congress': 119}}
    admissions = {'bill_catalog': catalog, 'bills': catalog if included else {},
                  'memberships': [row] if included else []}
    finish_memberships(root, bills, base, admissions)
    alice = bills['Alice Adams']
    assert len(alice) == (1 if included else 0)
    if included: assert alice[0]['role'] == 'sponsor'


def test_adjudicated_withdrawal_still_suppresses_newly_projected_sponsor(tmp_path):
    root, base = sponsor_root(tmp_path)
    _, bills = rebuild(root, base)
    assert len(bills['Alice Adams']) == 1
    (root/CRU.WITHDRAWALS_REL).write_text(
        FIX._withdrawal_csv(member='Alice Adams', bioguide='A000001'), encoding='utf-8')
    audit = finish_memberships(root, bills, base)
    assert bills['Alice Adams'] == []
    assert audit['bill_withdrawn_rows_removed'] == 1



def test_source_wiring_keeps_all_current_filters_after_history():
    sequence = ['_history_rows, _history_binding = load_verified_membership_history(', 'CRU.merge_collector_roster_updates(',
                'SA.remove_managed_desk_rows(', 'SA.append_to_desk(', 'OM.apply_to_desk(',
                '_withdrawals = BR.load_membership_withdrawals(', '# collapse duplicate rows']
    indexes = [source.index(needle) for needle in sequence]
    assert indexes == sorted(indexes)


def test_sponsor_snapshot_must_match_the_receipt_bound_repository(published):
    root, _ = published
    current = json.loads((root/CRU.REPOSITORY_REL).read_text(encoding='utf-8'))
    rows, _ = READ(root, repository_snapshot=current)
    assert rows
    stale = copy.deepcopy(current)
    next(iter(stale['bills'].values()))['sponsor']['bioguide'] = 'Z999999'
    with pytest.raises(RuntimeError, match='snapshot differs'):
        READ(root, repository_snapshot=stale)


def test_desk_bill_count_comes_from_the_loaded_generation_not_the_raw_file():
    # D-260: SA.load() drops an expired source-outage carry in memory. The desk's "automatically classified bills" figure must
    # count that same loaded generation, or it would still count the bills the desk no longer scores.
    assert "media_audit['bill_admissions_loaded']=len(_admissions['bills'])" in source
    assert source.index('_admissions=SA.load(B)') < source.index("media_audit['bill_admissions_loaded']")
    assert "'automatically_classified_bills':LOAD_AUDIT['bill_admissions_loaded']" in source
    assert "len(_automatic_source.get('bills'" not in source


def test_desk_surfaces_the_load_dropped_carries_in_the_integrity_block():
    # D-260: the keys SA.load() withheld travel with the desk's other source_display counters.
    needle = "media_audit['bill_admissions_carry_expired']=sorted(_admissions.get('carry_expired_keys') or [])"
    assert needle in source and source.index('_admissions=SA.load(B)') < source.index(needle)


def test_desk_review_backlog_counts_the_load_dropped_keys_not_already_deferred():
    # A bill SA.load() withholds is review work even when the generation's deferred_keys does not list it; one that is in both
    # lists counts once.
    needle = ("'bill_review_backlog':len(set(_automatic_source.get('deferred_keys',[]))"
              "|set(LOAD_AUDIT['bill_admissions_carry_expired'])),")
    assert needle in source
