"""Persistent user relationship statements, separate from source employment facts."""
import json
import os
import re
import tempfile
import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

def _identity_module():
    # Resolution imports routing evidence; resolve this shared store only after
    # module initialization, rather than creating an import cycle.
    try:
        from . import staffer_identity_resolution as identity
    except ImportError:  # direct script execution: no package context
        import os as _bootstrap_os, sys as _bootstrap_sys
        _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
            _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
        if _bootstrap_root not in _bootstrap_sys.path:
            _bootstrap_sys.path.insert(0, _bootstrap_root)
        from fec.surfaces import staffer_identity_resolution as identity
    return identity

DISPLAY_TIMEZONE = 'America/New_York'


def local_date():
    return datetime.now(ZoneInfo(DISPLAY_TIMEZONE)).date().isoformat()


def load_coworker_assertions():
    identity = _identity_module()
    data = json.loads(identity.USER_ASSERTIONS.read_text(encoding='utf-8'))
    records = data.get('coworker_assertions', [])
    return [{**r, 'person_ids': [i if i.startswith('person:') else 'person:' + i for i in r['source_person_ids']]}
            for r in records]


def record_coworker_decision(assertion_id, action, effective_date=None, statement=None):
    identity = _identity_module()
    if action not in {'retract', 'supersede'}:
        raise ValueError('Choose retract or supersede')
    effective_date = effective_date or local_date()
    effective_date = date.fromisoformat(effective_date).isoformat()
    if effective_date > local_date():
        raise ValueError('Use today or an earlier effective date')
    if action == 'supersede' and (not isinstance(statement, str) or not 1 <= len(statement.strip()) <= 2000):
        raise ValueError('A replacement statement is required')
    with identity._DECISION_LOCK:
        data = json.loads(identity.USER_ASSERTIONS.read_text(encoding='utf-8'))
        records = data.setdefault('coworker_assertions', [])
        old = next((r for r in records if r.get('id') == assertion_id), None)
        if old is None:
            raise ValueError('Unknown assertion')
        if old.get('status', 'active') != 'active':
            raise ValueError('This assertion is already retired')
        if effective_date < old['as_of']:
            raise ValueError('The end date cannot precede the statement effective date')
        at = datetime.now(timezone.utc).isoformat()
        old.update(status='retracted' if action == 'retract' else 'superseded', effective_until=effective_date)
        event = {'action': action, 'effective_date': effective_date, 'recorded_at': at,
                 'timezone': DISPLAY_TIMEZONE, 'source': 'user_action'}
        old.setdefault('events', []).append(event)
        if action == 'supersede':
            replacement = {**old, 'id': 'coworker:' + uuid.uuid4().hex,
                'assertion': statement.strip(), 'as_of': effective_date, 'status': 'active',
                'recorded_at': at, 'supersedes': old['id'], 'events': [],
                'provenance': {'source': 'user_statement', 'recorded_at': at, 'timezone': DISPLAY_TIMEZONE}}
            replacement.pop('effective_until', None)
            records.append(replacement)
            event['replacement_id'] = replacement['id']
        fd, temporary = tempfile.mkstemp(prefix='.staffer-assertions-', suffix='.json', dir=identity.USER_ASSERTIONS.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, identity.USER_ASSERTIONS)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {'assertion_id': assertion_id, **event}
