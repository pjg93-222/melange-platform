# -*- coding: utf-8 -*-
"""audit_full: media-phase artifacts built before a roster refresh pass loudly, and only for the refresh's own drift.

``member_receptivity.json`` and the audio-layer evidence ledger are produced by the media-phase producers (CHAIN :52-90).  The
non-media E2E chain starts at ``--from verify_collector_roster_updates.py``, so right after the roster went 533 -> 535 they
still describe the old roster and the exact-count checks (``len(...)==roster.count()``) failed.  ``ck_roster`` passes when the
artifact covers every continuing member, lacks only roster ARRIVALS and carries only DEPARTED members, prints a "!!" line naming
them, and fails on anything else.  Their 535 rebuild comes with the media-inclusive run.

FIXTURE ONLY.  audit_full executes on import, so the helper and the two call sites are lifted out of the shipped source with
``ast`` and run against a synthetic roster.json (the pattern of test_reconcile42_full_audit_blocks.py).
"""
import ast
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))
import roster as roster_module

SOURCE = (FEC / 'audit' / 'audit_full.py').read_text(encoding='utf-8-sig')
TREE = ast.parse(SOURCE)
RECEPTIVITY = 'receptivity layer covers all members'
AUDIO = 'every member holding verified audio evidence publishes an audio layer (no silent blindness)'

ROWS = [
    {'member': 'Continuing', 'bioguide': 'C000001', 'chamber': 'House', 'term_start': '2023-01-03'},
    {'member': 'Also Continuing', 'bioguide': 'C000002', 'chamber': 'Senate', 'term_start': '2021-01-03'},
    {'member': 'Special Election Arrival', 'bioguide': 'N000001', 'chamber': 'House', 'term_start': '2026-01-10'},
    {'member': 'Flagged Thin', 'bioguide': 'N000002', 'chamber': 'House', 'term_start': '2023-01-03', 'thin_evidence': True},
]
DEPARTED = [{'member': 'Resigned', 'bioguide': 'D000001', 'departed_on': '2026-09-01'}]
NAMES = [row['member'] for row in ROWS]


@pytest.fixture()
def fake_roster(tmp_path):
    os.makedirs(tmp_path / 'data' / 'engine')
    (tmp_path / 'data' / 'engine' / 'roster.json').write_text(json.dumps(
        {'schema': roster_module.SCHEMA, 'members': ROWS, 'departed': DEPARTED}), encoding='utf-8')
    return SimpleNamespace(names=lambda: list(NAMES),
                           drift=lambda current, covered: roster_module.drift(current, covered, str(tmp_path)))


def function(name):
    node = next((n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name), None)
    assert node is not None, '%s is not defined at module scope in audit_full.py' % name
    return node


def calls(label):
    return [n for n in ast.walk(TREE) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Name) and n.value.func.id == 'ck_roster'
            and n.value.args and isinstance(n.value.args[0], ast.Constant) and n.value.args[0].value == label]


def execute(nodes, fake_roster, extra=None):
    verdicts, notices = [], []
    namespace = {'ck': lambda *a: verdicts.append(a), 'roster': fake_roster,
                 'print': lambda *a, **k: notices.append(' '.join(str(x) for x in a))}
    namespace.update(extra or {})
    exec(compile(ast.Module(nodes, type_ignores=[]), 'isolated-audit-block', 'exec'), namespace)  # noqa: S102
    return verdicts, notices


def run_helper(fake_roster, covered, cond=True, detail=''):
    verdicts, notices = [], []
    namespace = {'ck': lambda *a: verdicts.append(a), 'roster': fake_roster,
                 'print': lambda *a, **k: notices.append(' '.join(str(x) for x in a))}
    exec(compile(ast.Module([function('ck_roster')], type_ignores=[]), 'isolated-audit-block', 'exec'), namespace)  # noqa: S102
    namespace['ck_roster']('layer', covered, cond, detail)
    return verdicts[0], notices


def test_an_artifact_for_the_pre_refresh_roster_passes_loudly(fake_roster):
    verdict, notices = run_helper(fake_roster, {'Continuing': 1, 'Also Continuing': 1, 'Resigned': 1})
    assert verdict[1] is True
    [line] = notices
    assert line.startswith('  !!') and 'layer' in line
    for name in ('Special Election Arrival', 'Flagged Thin', 'Resigned'):
        assert name in line


def test_a_missing_continuing_member_fails_and_is_named(fake_roster):
    verdict, notices = run_helper(fake_roster, {'Continuing': 1, 'Special Election Arrival': 1, 'Flagged Thin': 1})
    assert verdict[1] is False and 'Also Continuing' in verdict[2]


def test_an_unknown_extra_fails_and_is_named(fake_roster):
    verdict, _ = run_helper(fake_roster, {name: 1 for name in NAMES} | {'Stranger': 1})
    assert verdict[1] is False and 'Stranger' in verdict[2]


def test_a_failed_companion_condition_still_fails_an_exact_match(fake_roster):
    verdict, notices = run_helper(fake_roster, {name: 1 for name in NAMES}, cond=False, detail='2 blind')
    assert verdict[1] is False and '2 blind' in verdict[2] and notices == []


def test_an_exact_match_is_silent_and_passes(fake_roster):
    verdict, notices = run_helper(fake_roster, {name: 1 for name in NAMES})
    assert verdict[1] is True and notices == []


# --- the two call sites ------------------------------------------------------------------------------------------------

def test_receptivity_site_uses_the_roster_aware_check(fake_roster):
    [site] = calls(RECEPTIVITY)
    covered = {'Continuing': 1, 'Also Continuing': 1, 'Resigned': 1}
    verdicts, notices = execute([function('ck_roster'), site], fake_roster, {'_rj': covered})
    assert verdicts[0][0] == RECEPTIVITY and verdicts[0][1] is True and len(notices) == 1
    verdicts, _ = execute([function('ck_roster'), site], fake_roster, {'_rj': {'Continuing': 1}})
    assert verdicts[0][1] is False


def test_audio_site_uses_the_roster_aware_check_and_keeps_its_companion_conditions(fake_roster):
    [site] = calls(AUDIO)
    base = {'_al_blind': [], '_al_missing_desc': [], '_al_missing_vp': [], '_al_empty': [], '_al_clipmismatch': [],
            '_al_expected_any': set()}
    covered = {'Continuing': {}, 'Also Continuing': {}, 'Resigned': {}}
    verdicts, notices = execute([function('ck_roster'), site], fake_roster, dict(base, _alrows=covered))
    assert verdicts[0][0] == AUDIO and verdicts[0][1] is True and len(notices) == 1
    for blind in ('_al_blind', '_al_missing_desc', '_al_missing_vp', '_al_empty', '_al_clipmismatch'):
        verdicts, _ = execute([function('ck_roster'), site], fake_roster, dict(base, _alrows=covered, **{blind: ['x']}))
        assert verdicts[0][1] is False, blind
    verdicts, _ = execute([function('ck_roster'), site], fake_roster, dict(base, _alrows={'Continuing': {}}))
    assert verdicts[0][1] is False


def test_the_exact_count_conditions_are_gone_from_both_sites():
    assert 'len(_rj)==roster.count()' not in SOURCE
    assert 'len(_alrows)==roster.count()' not in SOURCE
