# -*- coding: utf-8 -*-
"""build_dashboard_bundle withholds speech profiles that fail the roster-sized contract instead of aborting the bundle.

member_narrative_evidence_v4.json is rebuilt by the media-phase producers; after the roster refresh (533 -> 535) it still
describes 533 members, so its ``counts.members == roster.count()`` contract fails until the media-inclusive run regenerates it.
The bundle used to raise ValueError and take the whole dashboard down; make_member_desk already withholds the same artifact
loudly.  The bundle now does the same: ``_SPEECH_PROFILES`` is empty and a "!!" line says so.

FIXTURE ONLY.  build_dashboard_bundle executes on import, so the three statements under test are lifted out of the shipped
source with ``ast`` and run against a synthetic artifact (the pattern of test_reconcile42_full_audit_blocks.py).
"""
import ast
import os
from types import SimpleNamespace

import pytest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUNDLE = os.path.join(FEC, 'surfaces', 'build_dashboard_bundle.py')
SOURCE = open(BUNDLE, encoding='utf-8').read()
TREE = ast.parse(SOURCE, filename=BUNDLE)
SHA = 'a' * 64


def _assign(node, name):
    return isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)


def _lift():
    ok = next((n for n in TREE.body if _assign(n, '_SPEECH_PROFILE_OK')), None)
    guard = next((n for n in TREE.body if isinstance(n, ast.If) and ast.unparse(n.test) == 'not _SPEECH_PROFILE_OK'), None)
    profiles = next((n for n in TREE.body if _assign(n, '_SPEECH_PROFILES')), None)
    assert ok is not None, '_SPEECH_PROFILE_OK is not assigned at module scope in build_dashboard_bundle.py'
    assert guard is not None, 'no "if not _SPEECH_PROFILE_OK:" guard in build_dashboard_bundle.py'
    assert profiles is not None
    return compile(ast.Module(body=[ok, guard, profiles], type_ignores=[]), BUNDLE, 'exec')


def run(art, roster_count=3):
    notices = []
    namespace = {'_SPEECH_PROFILE_ART': art, '_PROFILE_BASIS_SHA': SHA,
                 'roster': SimpleNamespace(count=lambda: roster_count),
                 'print': lambda *a, **k: notices.append(' '.join(str(x) for x in a))}
    exec(_lift(), namespace)  # noqa: S102 - three vetted statements
    return namespace['_SPEECH_PROFILES'], notices


def artifact(n=3, **over):
    members = {'m%d' % i: {'speech_profile': {'status': 'PASS'}} for i in range(n)}
    doc = {'schema': 'member-narrative-evidence-v4', 'status': 'ACTIVE_FAIL_CLOSED_INPUT', 'basis_sha256': SHA,
           'counts': {'members': n, 'speech_pass': n}, 'members': members}
    doc.update(over)
    return doc


def test_the_bundle_no_longer_aborts_on_a_failed_speech_profile_contract():
    assert "raise ValueError('member narrative speech-profile artifact failed" not in SOURCE
    assert 'speech profiles WITHHELD' in SOURCE


def test_a_withheld_artifact_must_not_be_published_as_a_pass_receipt():
    # speech_profile_meta says status=PASS_ALL_MEMBERS and carries the artifact's counts: only a contract-passing
    # artifact may publish it (before, the module raised first, so this was unreachable for a failed one).
    assert "if _SPEECH_PROFILE_ART and _SPEECH_PROFILE_OK else {}" in SOURCE


def test_a_passing_artifact_keeps_its_profiles_silently():
    doc = artifact()
    profiles, notices = run(doc)
    assert profiles is doc['members'] and notices == []


def test_an_artifact_for_the_pre_refresh_roster_is_withheld_loudly_not_fatal():
    # 533-style artifact against the 535-member roster
    profiles, notices = run(artifact(3), roster_count=5)
    assert profiles == {}
    [line] = notices
    assert line.startswith('  !!') and 'speech profiles WITHHELD' in line and '5' in line


@pytest.mark.parametrize('over', [
    {'schema': 'something-else'}, {'status': 'DRAFT'}, {'basis_sha256': 'b' * 64},
    {'counts': {'members': 3, 'speech_pass': 2}},
])
def test_every_other_contract_failure_is_withheld_too(over):
    profiles, notices = run(artifact(**over))
    assert profiles == {} and len(notices) == 1 and 'WITHHELD' in notices[0]


def test_one_member_that_is_not_pass_withholds_the_artifact():
    doc = artifact()
    doc['members']['m1']['speech_profile']['status'] = 'FAIL'
    profiles, notices = run(doc)
    assert profiles == {} and len(notices) == 1


def test_an_absent_artifact_is_unchanged_empty_and_silent():
    assert run(None) == ({}, [])
