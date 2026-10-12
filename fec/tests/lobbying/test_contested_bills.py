# -*- coding: utf-8 -*-
"""Standalone tests for the per-bill contested/advisory registry."""

import datetime
import json
import os
import sys
import tempfile

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

from lobbying import contested_bills as CB

P = F = 0


def ok(name, cond, detail=''):
    global P, F
    P, F = (P + 1, F) if cond else (P, F + 1)
    print(
        ('  OK   ' if cond else 'FAIL  ') + name
        + (('  :: ' + str(detail)) if (detail and not cond) else '')
    )


ROOT = tempfile.mkdtemp()


def write_registry(name, payload):
    path = os.path.join(ROOT, name)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle)
    return path


def base_record(**changes):
    record = {
        'bill': 'H.R.1919',
        'congress': '119',
        'mode': 'contested',
        'counterparty_industries': ['community_banks'],
        'ratified_by': 'Paul',
        'ratified_date': '2026-07-31',
    }
    record.update(changes)
    return record


def rejected(name, payload=None, text=None):
    path = os.path.join(ROOT, name + '.json')
    if text is None:
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle)
    else:
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)
    try:
        CB.load_contested_bills(path, today=datetime.date(2026, 8, 1))
        raised = False
    except CB.ContestedBillsError:
        raised = True
    ok('fail loud: ' + name, raised)
    return path


missing_path = os.path.join(ROOT, 'does-not-exist.json')
missing = CB.load_contested_bills(missing_path)
ok(
    'missing registry is absent with empty records',
    missing['status'] == 'absent' and missing['records'] == {},
    missing,
)
ok(
    'missing registry selects standard mode',
    CB.mode_for(missing, 'H.R.1919', '119') == ('standard', None),
)
ok(
    'mode_for standard on unparseable identifier (absent)',
    CB.mode_for(missing, 'LTR-CRAPO-2025', '119') == ('standard', None),
)

valid_payload = {
    'records': [
        {
            'bill': 'H.R.1919',
            'congress': '119',
            'mode': 'contested',
            'counterparty_industries': ['community_banks', 'crypto'],
            'ratified_by': 'Paul',
            'ratified_date': '2026-07-31',
        },
        {
            'bill': 'S.869',
            'congress': '119',
            'mode': 'advisory',
            'counterparty_industries': ['fintech'],
            'active_from': '2030-01-01',
            'ratified_by': 'Paul',
            'ratified_date': '2026-07-31',
        },
    ],
}
valid_path = write_registry('valid.json', valid_payload)
registry = CB.load_contested_bills(
    valid_path, today=datetime.date(2026, 8, 1)
)
contested_record = registry['active'][0]
advisory_record = registry['dormant'][0]
ok(
    'valid registry has one active and one dormant record',
    registry['status'] == 'present'
    and len(registry['active']) == 1
    and len(registry['dormant']) == 1,
    registry,
)
ok(
    'active contested record selects contested mode',
    CB.mode_for(registry, 'H.R.1919', '119')
    == ('contested', contested_record),
)
ok(
    'mode_for accepts float-string congress',
    CB.mode_for(registry, 'H.R.1919', '119.0')
    == ('contested', contested_record),
)
ok(
    'dormant advisory record selects standard with record',
    CB.mode_for(registry, 'S.869', '119') == ('standard', advisory_record),
)
ok(
    'absent bill selects standard without record',
    CB.mode_for(registry, 'H.R.9999', '119') == ('standard', None),
)
ok(
    'mode_for standard on unparseable identifier (present)',
    CB.mode_for(registry, 'LTR-CRAPO-2025', '119') == ('standard', None),
)

try:
    CB.mode_for(registry, 'H.R.1919', 'abc')
    invalid_lookup_congress_raised = False
except CB.ContestedBillsError:
    invalid_lookup_congress_raised = True
ok(
    'mode_for still raises on invalid congress',
    invalid_lookup_congress_raised,
)

for spelling in ('HR 1919', 'H. R. 1919', 'hr1919'):
    ok(
        'bill normalization accepts ' + spelling,
        CB.mode_for(registry, spelling, '119')
        == ('contested', contested_record),
    )

sres_path = write_registry(
    'sres.json', {'records': [base_record(bill='S.Res.14')]}
)
sres_registry = CB.load_contested_bills(
    sres_path, today=datetime.date(2026, 8, 1)
)
sres_record = sres_registry['active'][0]
ok(
    'S.Res spelling accepted',
    CB.mode_for(sres_registry, 'S.Res.14', '119')
    == ('contested', sres_record),
)

window_payload = {
    'records': [base_record(
        active_from='2026-01-01', active_to='2026-06-30'
    )],
}
window_path = write_registry('window.json', window_payload)
after_window = CB.load_contested_bills(
    window_path, today=datetime.date(2026, 8, 1)
)
ok(
    'active_to expired record is dormant and standard',
    len(after_window['dormant']) == 1
    and CB.mode_for(after_window, 'H.R.1919', '119')[0] == 'standard',
)
on_last_day = CB.load_contested_bills(
    window_path, today=datetime.date(2026, 6, 30)
)
ok(
    'active_to final day is inclusive',
    len(on_last_day['active']) == 1
    and CB.mode_for(on_last_day, 'H.R.1919', '119')[0] == 'contested',
)

malformed_path = rejected('malformed JSON', text='{not-json')
rejected('unknown top-level key', {'records': [], 'extra': True})
rejected('records not a list', {'records': {}})
rejected(
    'record with unknown key',
    {'records': [base_record(extra=True)]},
)
missing_ratifier = base_record()
del missing_ratifier['ratified_by']
rejected('record missing ratified_by', {'records': [missing_ratifier]})
missing_ratified_date = base_record()
del missing_ratified_date['ratified_date']
rejected(
    'record missing ratified_date',
    {'records': [missing_ratified_date]},
)
rejected(
    'unparseable bill GARBAGE',
    {'records': [base_record(bill='GARBAGE')]},
)
rejected(
    'bill trailing garbage',
    {'records': [base_record(
        bill='H.R.1919 totally different bill 9999'
    )]},
)
rejected(
    'ambiguous S.R. spelling',
    {'records': [base_record(bill='S.R.100')]},
)
rejected('congress 99', {'records': [base_record(congress='99')]})
rejected('congress abc', {'records': [base_record(congress='abc')]})
rejected('congress 119.9', {'records': [base_record(congress=119.9)]})
rejected(
    'congress 1e309',
    text=(
        '{"records": [{"bill": "H.R.1919", "congress": 1e309, '
        '"mode": "contested", "counterparty_industries": '
        '["community_banks"], "ratified_by": "Paul", '
        '"ratified_date": "2026-07-31"}]}'
    ),
)
rejected('mode global', {'records': [base_record(mode='global')]})
rejected(
    'duplicate JSON key mode',
    text=(
        '{"records": [{"bill": "H.R.1919", "congress": "119", '
        '"mode": "advisory", "mode": "contested", '
        '"counterparty_industries": ["community_banks"], '
        '"ratified_by": "Paul", "ratified_date": "2026-07-31"}]}'
    ),
)
rejected(
    'empty counterparty_industries',
    {'records': [base_record(counterparty_industries=[])]},
)
rejected(
    'unknown industry big_oil',
    {'records': [base_record(counterparty_industries=['big_oil'])]},
)
rejected(
    'ours-side industry credit_union',
    {'records': [base_record(counterparty_industries=['credit_union'])]},
)
rejected(
    'duplicate industry',
    {'records': [base_record(
        counterparty_industries=['crypto', 'crypto']
    )]},
)
rejected(
    'ratified_date not-a-date',
    {'records': [base_record(ratified_date='not-a-date')]},
)
rejected(
    'active_to earlier than active_from',
    {'records': [base_record(
        active_from='2026-08-01', active_to='2026-07-31'
    )]},
)
rejected(
    'duplicate bill and congress',
    {'records': [base_record(), base_record(bill='HR 1919')]},
)

override_path = os.path.join(ROOT, 'override.json')
sentinel = object()
prior_override = os.environ.get('CONTESTED_BILLS_PATH', sentinel)
try:
    os.environ['CONTESTED_BILLS_PATH'] = override_path
    ok(
        'CONTESTED_BILLS_PATH overrides registry path',
        CB.registry_path() == override_path,
        CB.registry_path(),
    )
finally:
    if prior_override is sentinel:
        del os.environ['CONTESTED_BILLS_PATH']
    else:
        os.environ['CONTESTED_BILLS_PATH'] = prior_override

ok(
    'CLI valid registry returns 0',
    CB.main(['--path', valid_path, '--today', '2026-08-01']) == 0,
)
ok(
    'CLI malformed registry returns 2',
    CB.main(['--path', malformed_path]) == 2,
)

print('\nCONTESTED-BILLS TESTS — %d passed, %d FAILED' % (P, F))
sys.exit(1 if F else 0)
