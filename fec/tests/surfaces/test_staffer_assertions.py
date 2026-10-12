import json
from datetime import datetime, timezone

import pytest

from fec.surfaces import staffer_assertions as assertions
from fec.surfaces.staffer_routing import AdvocacyRouter


@pytest.fixture
def saved(tmp_path, monkeypatch):
    path = tmp_path / 'assertions.json'
    path.write_text(json.dumps({'identity_groups': [{'primary_id': 'person:a', 'alias_ids': ['person:b']}],
        'coworker_assertions': [{'id': 'statement', 'status': 'active', 'as_of': '2026-09-04',
            'assertion': 'A and B currently work together', 'source_person_ids': ['lda:lobbyist:1', 'lda:lobbyist:2'],
            'provenance': {'source': 'user_statement'}}]}))
    monkeypatch.setattr(assertions._identity_module(), 'USER_ASSERTIONS', path)
    monkeypatch.setattr(assertions, 'local_date', lambda: '2026-09-05')
    return path


def test_retraction_preserves_original_and_identity_decisions(saved):
    original = json.loads(saved.read_text())
    result = assertions.record_coworker_decision('statement', 'retract')
    data = json.loads(saved.read_text())
    record = data['coworker_assertions'][0]
    assert record['assertion'] == original['coworker_assertions'][0]['assertion']
    assert record['effective_until'] == '2026-09-05'
    assert record['status'] == 'retracted'
    assert data['identity_groups'] == original['identity_groups']
    assert datetime.fromisoformat(result['recorded_at']).utcoffset().total_seconds() == 0
    assert assertions.load_coworker_assertions()[0]['person_ids'] == ['person:lda:lobbyist:1', 'person:lda:lobbyist:2']


def test_supersession_creates_a_new_record_and_retains_evidence(saved):
    result = assertions.record_coworker_decision('statement', 'supersede', statement='A and B still work together at their new firm')
    old, new = json.loads(saved.read_text())['coworker_assertions']
    assert old['status'] == 'superseded' and old['effective_until'] == new['as_of']
    assert new['supersedes'] == old['id'] and result['replacement_id'] == new['id']
    assert new['status'] == 'active' and 'effective_until' not in new


@pytest.mark.parametrize('payload', [
    ('unknown', 'retract', None, None), ('statement', 'invalid', None, None),
    ('statement', 'retract', '2026-09-03', None), ('statement', 'supersede', None, ''),
    ('statement', 'retract', '2026-09-06', None),
])
def test_invalid_decision_does_not_change_saved_records(saved, payload):
    before = saved.read_bytes()
    with pytest.raises(ValueError):
        assertions.record_coworker_decision(*payload)
    assert saved.read_bytes() == before


def test_retired_assertion_no_longer_supports_current_route():
    nodes = {i: {'kind': 'person', 'label': i.title()} for i in ('a', 'b')}
    episodes = [{'person_id': i, 'employer_id': 'firm', 'as_of': '2026-09-04',
        'current_evidence': True, 'user_asserted': True, 'assertion_end_exclusive': '2026-09-05'} for i in nodes]
    prior = AdvocacyRouter(nodes, episodes, 'r', '2026-09-04')
    current = AdvocacyRouter(nodes, episodes, 'r', '2026-09-05')
    assert prior.route('a', 'b', {'include_user_asserted': True})['cost'] == 1
    assert current.route('a', 'b', {'include_user_asserted': True})['cost'] == 3
    assert current.route('a', 'b', {'include_user_asserted': True, 'include_historical': False})['status'] == 'unreachable'


def test_date_uses_eastern_even_after_utc_midnight(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 5, 0, 30, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(assertions, 'datetime', FixedDateTime)
    assert assertions.local_date() == '2026-09-04'
