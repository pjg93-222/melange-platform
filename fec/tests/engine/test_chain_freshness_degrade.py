"""Labelled degrade for the four freshness time bombs (self-healing phase 2, Paul 2026-10-15), on synthetic temp roots.

Inside the window a source is FRESH and nothing about its output changes. Past the window it proceeds on its last-good
generation and is LABELLED STALE_LABELLED (its own receipt where it has one, data/engine/source_freshness.json when that file
exists, a stdout line). It is still refused when it has NEVER succeeded or its last-good bytes are missing/corrupt, and the
DE-055 consistency check (applied money must match its source receipt) stays strict.
"""
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))
import chain_transaction as CT  # noqa: E402
from engine import apply_fec_money_refresh as money  # noqa: E402
from legislation import official_membership_movements as movements  # noqa: E402
from legislation import sentinel_admissions as admissions  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_label_cache():
    CT._NOTED.clear()
    yield
    CT._NOTED.clear()


def stamp(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(root, rel, doc):
    path = Path(root, *rel.split('/'))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding='utf-8')
    return path


# --------------------------------------------------------------------------------------------- FEC money

FIELDS = ('cu', 'banks', 'finserv', 'receipts', 'total', 'saturation', 'cu_pct', 'banks_pct', 'finserv_pct',
          'cu_adj', 'banks_adj', 'finserv_adj', 'industry_adj', 'net_lean', 'money_source_status')


def money_root(tmp_path, days_ago, status='PASS', roster=('A', 'B')):
    root = tmp_path / 'root'
    old = [{'member': m, 'bioguide': m + '1', 'unrelated': 'keep-' + m, **{f: 0 for f in FIELDS}} for m in ('A', 'B')]
    new = [{'member': m, 'bioguide': m + '1', **{f: 7 for f in FIELDS}} for m in roster]
    write(root, 'data/engine/congestion.json', old)
    candidate = write(root, 'data/money/fec_generations/g1/projection.json', new)
    evidence = write(root, 'data/money/fec_generations/g1/evidence.csv', 'a,b\n1,2\n')
    write(root, 'data/money/fec_refresh_current.json', {
        'status': status, 'checked_at': stamp(days_ago), 'cycle': 2026, 'dependencies': {},
        'sources': [{'path': str(evidence), 'sha256': sha(evidence)}],
        'output': {'path': str(candidate), 'sha256': sha(candidate)}, 'unresolved_members': []})
    return root


def test_fresh_money_applies_and_the_receipt_bytes_are_unchanged_from_before(tmp_path):
    root = money_root(tmp_path, 1)
    assert money.apply(root) == 2
    receipt = json.loads((root / 'data/money/fec_money_application.json').read_text())
    assert 'freshness_status' not in receipt and set(receipt) == {
        'schema', 'status', 'cycle', 'source_receipt_sha256', 'congestion_sha256', 'unresolved_members'}
    rows = json.loads((root / 'data/engine/congestion.json').read_text())
    assert rows[0]['cu'] == 7 and rows[0]['unrelated'] == 'keep-A'


def test_stale_money_proceeds_on_last_good_and_is_labelled_in_its_receipt_and_the_freshness_file(tmp_path, capsys):
    root = money_root(tmp_path, 20)
    write(root, 'data/engine/source_freshness.json', {'someone_else': {'status': 'FRESH'}})
    assert money.apply(root) == 2
    receipt = json.loads((root / 'data/money/fec_money_application.json').read_text())
    assert receipt['freshness_status'] == 'STALE_LABELLED' and receipt['freshness_window_days'] == 8
    current = json.loads((root / 'data/money/fec_refresh_current.json').read_text())
    assert receipt['source_checked_at'] == current['checked_at']
    freshness = json.loads((root / 'data/engine/source_freshness.json').read_text())
    assert freshness['someone_else'] == {'status': 'FRESH'}
    assert freshness['fec_money']['status'] == 'STALE_LABELLED' and freshness['fec_money']['window_days'] == 8
    assert 'STALE_LABELLED' in capsys.readouterr().out
    assert json.loads((root / 'data/engine/congestion.json').read_text())[0]['cu'] == 7


def test_stale_money_without_a_freshness_file_still_proceeds_and_creates_nothing(tmp_path):
    root = money_root(tmp_path, 20)
    assert money.apply(root) == 2
    assert not (root / 'data/engine/source_freshness.json').exists()


def test_de055_the_applied_money_still_binds_to_its_source_receipt_when_stale(tmp_path):
    root = money_root(tmp_path, 20)
    money.apply(root)
    receipt = json.loads((root / 'data/money/fec_money_application.json').read_text())
    assert receipt['source_receipt_sha256'] == sha(root / 'data/money/fec_refresh_current.json')
    assert receipt['congestion_sha256'] == sha(root / 'data/engine/congestion.json')


@pytest.mark.parametrize('days', [1, 20])
def test_de055_a_projection_that_does_not_match_its_receipt_refuses_fresh_or_stale(tmp_path, days):
    root = money_root(tmp_path, days)
    Path(json.loads((root / 'data/money/fec_refresh_current.json').read_text())['output']['path']).write_text('[]')
    with pytest.raises(ValueError, match='projection hash mismatch'):
        money.apply(root)


@pytest.mark.parametrize('days', [1, 20])
def test_corrupt_evidence_refuses_fresh_or_stale(tmp_path, days):
    root = money_root(tmp_path, days)
    (root / 'data/money/fec_generations/g1/evidence.csv').write_text('tampered')
    with pytest.raises(ValueError, match='evidence hash mismatch'):
        money.apply(root)


def test_a_different_roster_refuses_even_when_stale(tmp_path):
    root = money_root(tmp_path, 20, roster=('A',))
    with pytest.raises(ValueError, match='different roster'):
        money.apply(root)


def test_money_that_never_succeeded_refuses(tmp_path):
    root = money_root(tmp_path, 20)
    (root / 'data/money/fec_refresh_current.json').unlink()
    with pytest.raises(ValueError, match='No verified current FEC refresh'):
        money.apply(root)
    for days in (1, 20):
        root = money_root(tmp_path / ('failed%d' % days), days, status='FAIL')
        with pytest.raises(ValueError, match='failed/stale'):
            money.apply(root)


def test_a_future_dated_money_receipt_is_a_clock_fault_not_staleness(tmp_path):
    root = money_root(tmp_path, -3)
    with pytest.raises(ValueError, match='failed/stale'):
        money.apply(root)


# --------------------------------------------------------------------------------------------- sentinel admissions

def admissions_doc(days_ago, status='PASS', sources=(), bills=None, decisions=None):
    return {'schema': admissions.SCHEMA, 'status': status, 'checked_at': stamp(days_ago).replace('+00:00', 'Z'),
            'sources': list(sources), 'bills': bills or {}, 'decisions': decisions or {}, 'memberships': []}


def test_admissions_inside_the_window_are_fresh_and_unlabelled(tmp_path):
    write(tmp_path, 'data/legislation/sentinel_admissions.json', admissions_doc(1))
    document = admissions.load(tmp_path)
    assert 'freshness_status' not in document


def test_stale_admissions_are_served_labelled_not_raised(tmp_path, capsys):
    write(tmp_path, 'data/legislation/sentinel_admissions.json', admissions_doc(20))
    write(tmp_path, 'data/engine/source_freshness.json', {})
    document = admissions.load(tmp_path)
    assert document['freshness_status'] == 'STALE_LABELLED' and document['bills'] == {}
    assert json.loads((tmp_path / 'data/engine/source_freshness.json').read_text())['sentinel_admissions']['status'] == 'STALE_LABELLED'
    assert capsys.readouterr().out.count('STALE_LABELLED') == 1
    admissions.load(tmp_path)                                           # hot loader: labelled once per process, not per call
    assert 'STALE_LABELLED' not in capsys.readouterr().out


def test_the_freshness_window_is_still_a_parameter(tmp_path):
    write(tmp_path, 'data/legislation/sentinel_admissions.json', admissions_doc(5))
    assert 'freshness_status' not in admissions.load(tmp_path)
    CT._NOTED.clear()
    assert admissions.load(tmp_path, max_age_days=3)['freshness_status'] == 'STALE_LABELLED'


def test_missing_admissions_remain_an_exact_no_op(tmp_path):
    assert admissions.load(tmp_path) == {'schema': admissions.SCHEMA, 'bills': {}, 'memberships': [], 'decisions': {}}


def test_non_pass_or_future_dated_admissions_still_refuse(tmp_path):
    write(tmp_path, 'data/legislation/sentinel_admissions.json', admissions_doc(20, status='FAIL'))
    with pytest.raises(ValueError, match='schema-valid PASS'):
        admissions.load(tmp_path)
    write(tmp_path, 'data/legislation/sentinel_admissions.json', admissions_doc(-3))
    with pytest.raises(ValueError, match='outside their freshness window'):
        admissions.load(tmp_path)


def test_stale_admissions_with_a_corrupt_evidence_file_still_refuse(tmp_path):
    evidence = write(tmp_path, 'data/legislation/sentinel_evidence/e.txt', 'original')
    record = {'path': str(evidence), 'sha256': sha(evidence)}
    write(tmp_path, 'data/legislation/sentinel_admissions.json', admissions_doc(20, sources=[record]))
    assert admissions.load(tmp_path)['freshness_status'] == 'STALE_LABELLED'
    evidence.write_text('tampered')
    with pytest.raises(ValueError, match='evidence hash mismatch'):
        admissions.load(tmp_path)


def test_a_stale_individual_admission_is_labelled_and_the_later_strict_checks_still_run(tmp_path):
    key = '119:hr:1'
    bad = {'canonical_key': key, 'disposition': 'include', 'confidence': 'high', 'checked_at': stamp(20),
           'beneficiary_class': 'not-a-class', 'categories': []}
    write(tmp_path, 'data/legislation/sentinel_admissions.json',
          admissions_doc(1, bills={key: {'key': key, 'areas': []}}, decisions={key: bad}))
    # The staleness of the decision no longer raises; the classification check that follows it does.
    with pytest.raises(ValueError, match='no beneficiary/category classification'):
        admissions.load(tmp_path)


def test_source_outage_carry_forward_limit_is_unchanged(tmp_path):
    key = '119:hr:1'
    carried = {'canonical_key': key, 'disposition': 'include', 'confidence': 'high', 'checked_at': stamp(45),
               'carried_forward': 'source_unavailable', 'carried_at': stamp(1)}
    write(tmp_path, 'data/legislation/sentinel_admissions.json',
          admissions_doc(1, bills={key: {'key': key, 'areas': []}}, decisions={key: carried}))
    with pytest.raises(ValueError, match='past its verification limit'):
        admissions.load(tmp_path)


# --------------------------------------------------------------------------------------------- official membership movements

def movements_root(tmp_path, days_ago, facts=(), status='PASS', bills=None):
    root = tmp_path / 'root'
    repository = write(root, 'bill_repository_live.json', {'bills': bills or {}})
    write(root, 'data/legislation/official_membership_movements.json', {
        'schema': 'official-membership-movements-v1', 'status': status, 'checked_at': stamp(days_ago),
        'repository_sha256': movements.sha256_file(repository), 'facts': list(facts), 'additions': []})
    return root


def test_fresh_movements_are_unlabelled(tmp_path):
    assert 'freshness_status' not in movements.load(movements_root(tmp_path, 1))


def test_stale_movements_are_served_labelled_not_raised(tmp_path, capsys):
    root = movements_root(tmp_path, 20)
    write(root, 'data/engine/source_freshness.json', {'sources': {}})
    document = movements.load(root)
    assert document['freshness_status'] == 'STALE_LABELLED'
    entry = json.loads((root / 'data/engine/source_freshness.json').read_text())['sources']['official_membership_movements']
    assert entry['status'] == 'STALE_LABELLED' and entry['window_days'] == 8
    assert 'STALE_LABELLED' in capsys.readouterr().out


def test_stale_movements_bound_to_another_repository_still_refuse(tmp_path):
    root = movements_root(tmp_path, 20)
    (root / 'bill_repository_live.json').write_text(json.dumps({'bills': {'new': {}}}))
    with pytest.raises(ValueError, match='refer to a different repository'):
        movements.load(root)


def test_stale_movements_that_differ_from_the_official_source_still_refuse(tmp_path):
    bills = {'k': {'sponsor': {'bioguide': 'S1'}, 'original_cosponsors': [], 'cosponsors': []}}
    fact = {'repository_key': 'k', 'bioguide': 'S1', 'role': 'sponsor', 'withdrawn': True}
    root = movements_root(tmp_path, 20, facts=[fact], bills=bills)
    with pytest.raises(ValueError, match='differs from the official source'):
        movements.load(root)


def test_missing_non_pass_and_future_movements_behave_as_before(tmp_path):
    assert movements.load(tmp_path / 'nothing') == {'facts': [], 'additions': []}
    with pytest.raises(ValueError, match='not a valid PASS'):
        movements.load(movements_root(tmp_path / 'a', 20, status='FAIL'))
    with pytest.raises(ValueError, match='are stale'):
        movements.load(movements_root(tmp_path / 'b', -3))
