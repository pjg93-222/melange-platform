# -*- coding: utf-8 -*-
"""Contract tests for Paul's 2026-09-05 ratification of the [SYS] rows and the personal-texture
check into fec\\audit_failure_gate.py's KNOWN_GATED set.

Loads the module UNDER TEST by file path (not by package import) so this file can run either from
its live location or from a scratch copy — ROOT is derived from this file's own path, three parents
up, matching the fec/tests/infra/<this file> layout. Plain pytest-style functions, no fixtures, so
pytest can collect them directly."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
_MODULE_PATH = ROOT / 'fec' / 'audit_failure_gate.py'

_spec = importlib.util.spec_from_file_location('audit_failure_gate_ratified_under_test', _MODULE_PATH)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


# The exact 11-name FAILURES list from the 2026-09-05 full-body audit run, retained verbatim as the
# historical record. After the DE-011 sentinel-exclusion landing (2026-09-06), FIVE of its A2b names
# are no longer tolerated -- they are stores the chain writes into, whose sentinels no longer name
# chain outputs, so a red there is novel. See FAILURES_POST_DE011 below for the current expectation.
FAILURES_11 = [
    'personal-texture layer propagates exactly to member profiles (display-only)',
    'press refresh feed is hash-bound and keeps official mentions out of stance/speech',
    'vote_category_lean.json fresh vs bills archive (manual frozen input; see V-1)',
    'culling surface reports the hash-bound production promotion state',
    'A2b repo.data_legislation sentinels unchanged',
    'A2b repo.data_members sentinels unchanged',
    'A2b repo.data_money sentinels unchanged',
    'A2 repo.data_engine size_class agrees',
    'A2b repo.data_engine sentinels unchanged',
    'A2b repo.outputs sentinels unchanged',
    'A2b glassbox.events sentinels unchanged',
]

# What the same run looks like once chain outputs are out of sentinel duty: the five chain-written
# A2b rows go green; local.billstatus and glassbox.events keep firing for non-chain reasons.
# 2026-10-11: the vote_category_lean name left KNOWN_GATED_FAILURES (audit_full no longer emits it; reworded
# b11680957), so it drops out of the tolerated post-DE-011 set too.
VOTE_LEAN_NAME = 'vote_category_lean.json fresh vs bills archive (manual frozen input; see V-1)'
FAILURES_POST_DE011 = [n for n in FAILURES_11
                       if not n.startswith('A2b repo.') and n != VOTE_LEAN_NAME] + [
    'A2b local.billstatus sentinels unchanged',
]

# The five names the narrowing REMOVED from tolerance. Named explicitly rather than derived, so a
# future edit to FAILURES_11 cannot quietly shrink what this file claims to be guarding.
CHAIN_WRITTEN_A2B_NOW_NOVEL = (
    'A2b repo.data_legislation sentinels unchanged',
    'A2b repo.data_members sentinels unchanged',
    'A2b repo.data_money sentinels unchanged',
    'A2b repo.data_engine sentinels unchanged',
    'A2b repo.outputs sentinels unchanged',
    'A2b local.lda_derived sentinels unchanged',
)

PRESS_NAME = 'press refresh feed is hash-bound and keeps official mentions out of stance/speech'
CONVO_ROLES_NAME = 'qualified conversation roles v2 propagate exactly to the desk and remain engine-isolated'


def _blob(names, passed, failed):
    return 'FULL-BODY AUDIT — %d passed, %d FAILED\nFAILURES: %r\n' % (passed, failed, names)


def test_post_de011_failures_are_all_tolerated():
    stdout = _blob(FAILURES_POST_DE011, 572, len(FAILURES_POST_DE011))
    tolerate, names = gate.tolerate_known_gated(stdout, '')
    assert tolerate is True
    assert names == FAILURES_POST_DE011
    novel = [n for n in names
             if n not in gate.KNOWN_GATED_FAILURES
             and not any(p.match(n) for p in gate.KNOWN_GATED_PATTERNS)]
    assert novel == []
    # The set must still contain BOTH shapes, or this test would pass on an empty pattern list.
    assert any(n.startswith('A2 ') for n in names)
    assert any(n.startswith('A2b ') for n in names)


def test_the_2026_09_05_eleven_are_no_longer_all_tolerated():
    """The historical list is kept, and it must now FAIL. If this ever goes green again the
    narrowing has been reverted -- silently, since nothing else in the suite would notice."""
    stdout = _blob(FAILURES_11, 572, 11)
    tolerate, names = gate.tolerate_known_gated(stdout, '')
    assert tolerate is False
    assert names == FAILURES_11
    novel = [n for n in names
             if n not in gate.KNOWN_GATED_FAILURES
             and not any(p.match(n) for p in gate.KNOWN_GATED_PATTERNS)]
    assert sorted(novel) == sorted([n for n in CHAIN_WRITTEN_A2B_NOW_NOVEL if n in FAILURES_11] + [VOTE_LEAN_NAME])


def test_post_de011_failures_without_press_are_all_tolerated():
    without_press = [n for n in FAILURES_POST_DE011 if n != PRESS_NAME]
    assert len(without_press) == len(FAILURES_POST_DE011) - 1
    stdout = _blob(without_press, 572, len(without_press))
    tolerate, names = gate.tolerate_known_gated(stdout, '')
    assert tolerate is True
    assert names == without_press


def test_a1_exists_and_a3_topology_are_not_tolerated():
    for name in ('A1 repo.data_money exists', 'A3 repo.data_money host topology agrees'):
        stdout = _blob([name], 572, 1)
        tolerate, names = gate.tolerate_known_gated(stdout, '')
        assert tolerate is False
        assert names == [name]
        assert not any(p.match(name) for p in gate.KNOWN_GATED_PATTERNS)
        assert name not in gate.KNOWN_GATED_FAILURES


def test_user_completed_backup_scope_is_narrow_and_visible():
    waived=['A1 repo.rescue_dir still exists','A1 repo.rescue_manifest_a still exists',
            'A1 repo.rescue_manifest_b still exists','A1 repo.rescue_manifest_c still exists']
    assert gate.tolerate_known_gated(_blob(waived,572,4),'')==(True,waived)
    current_loss='A1 repo.data_money still exists'
    assert gate.tolerate_known_gated(_blob(waived+[current_loss],572,5),'')[0] is False


def test_pipeline_preserves_long_final_audit_verdict_without_waiving_novel_failure():
    failures=list(gate.KNOWN_GATED_FAILURES)+['A2 repo.data_engine size_class agrees',
              'A2 mirror.melange file_count within tolerance']
    failures+=['A2 repo.fixture_%d file_count within tolerance'%i for i in range(10)]
    report=_blob(failures,572,len(failures))
    assert len(report)>800
    forwarded=gate.pipeline_failure_output('audit_full.py','detail\n'*1000+report)
    assert gate.tolerate_known_gated(forwarded,'')[0] is True
    failures.append('Unexpected fresh source mismatch')
    forwarded=gate.pipeline_failure_output('audit_full.py',_blob(failures,572,len(failures)))
    assert gate.tolerate_known_gated(forwarded,'')[0] is False


def test_a_chain_written_store_A2b_is_now_NOVEL():
    """THE POINT OF THE NARROWING. Before DE-011 these were tolerated by pattern; now that the
    producer excludes chain outputs from sentinel duty, a red here means a NEW chain output took a
    sentinel slot and its store needs a sentinel_excludes entry. Tolerating it would re-create the
    always-red row the exclusion exists to remove."""
    for name in CHAIN_WRITTEN_A2B_NOW_NOVEL:
        stdout = _blob([name], 572, 1)
        tolerate, names = gate.tolerate_known_gated(stdout, '')
        assert tolerate is False, name
        assert names == [name]
        assert not any(p.match(name) for p in gate.KNOWN_GATED_PATTERNS), name
        assert name not in gate.KNOWN_GATED_FAILURES, name


def test_the_two_surviving_A2b_stores_are_still_tolerated():
    """The other half of the discriminator: the narrowing is a NARROWING, not a retirement. If both
    of these went novel too, KNOWN_GATED_PATTERNS[1] would effectively be gone and the test above
    would still pass."""
    for name in ('A2b local.billstatus sentinels unchanged',
                 'A2b glassbox.events sentinels unchanged'):
        stdout = _blob([name], 572, 1)
        tolerate, names = gate.tolerate_known_gated(stdout, '')
        assert tolerate is True, name
        assert names == [name]
        assert any(p.match(name) for p in gate.KNOWN_GATED_PATTERNS), name


def test_the_A2_scale_pattern_is_untouched_by_the_narrowing():
    """KNOWN_GATED_PATTERNS[0] was explicitly left alone: a chain run still moves a store's size
    class and file count minutes before its own terminal audit, including on the very stores whose
    A2b rows just stopped being tolerated."""
    for name in ('A2 repo.data_engine size_class agrees',
                 'A2 repo.data_money file_count within tolerance',
                 'A2 repo.outputs size_class agrees',
                 'A2 local.lda_derived file_count within tolerance'):
        stdout = _blob([name], 572, 1)
        tolerate, names = gate.tolerate_known_gated(stdout, '')
        assert tolerate is True, name
        assert gate.KNOWN_GATED_PATTERNS[0].match(name), name


def test_trailing_detail_breaks_the_anchored_pattern():
    # Re-pointed at a store still IN the pattern. With repo.data_money removed from it, the old
    # name would fail this assertion for the wrong reason and stop testing the anchoring at all.
    name = 'A2b local.billstatus sentinels unchanged :: extra'
    stdout = _blob([name], 572, 1)
    tolerate, names = gate.tolerate_known_gated(stdout, '')
    assert tolerate is False
    assert names == [name]
    assert not any(p.match(name) for p in gate.KNOWN_GATED_PATTERNS)


def test_front_truncated_failures_line_fails_closed():
    # A front-truncated FAILURES line (as run_pipeline's 800-char stdout tail can produce) does not
    # start with "FAILURES: [" at all -- it starts mid-list, so the FAILURES regex finds nothing.
    truncated_tail = ("layer propagates exactly to member profiles (display-only)', "
                       "'press refresh feed is hash-bound and keeps official mentions out of "
                       "stance/speech']\n")
    stdout = 'FULL-BODY AUDIT — 572 passed, 11 FAILED\n' + truncated_tail
    names, reason = gate.audit_failures(stdout, '')
    assert names is None
    assert reason
    tolerate, tolerated_names = gate.tolerate_known_gated(stdout, '')
    assert tolerate is False
    assert tolerated_names == []


def test_known_gated_failures_contains_only_ratified_checks_and_completed_backup_scope():
    assert len(gate.KNOWN_GATED_FAILURES) == 7
    assert {n for n in gate.KNOWN_GATED_FAILURES if n.startswith('A1 ')} == {
        'A1 repo.rescue_dir still exists','A1 repo.rescue_manifest_a still exists',
        'A1 repo.rescue_manifest_b still exists','A1 repo.rescue_manifest_c still exists'}
    assert 'culling surface reports the hash-bound production promotion state' in gate.KNOWN_GATED_FAILURES
    assert VOTE_LEAN_NAME not in gate.KNOWN_GATED_FAILURES   # dead entry removed 2026-10-11
    assert 'personal-texture layer propagates exactly to member profiles (display-only)' in gate.KNOWN_GATED_FAILURES
    assert PRESS_NAME in gate.KNOWN_GATED_FAILURES


def test_convo_roles_is_the_lone_novel_alongside_the_post_de011_set():
    failures = FAILURES_POST_DE011 + [CONVO_ROLES_NAME]
    stdout = _blob(failures, 572, len(failures))
    tolerate, names = gate.tolerate_known_gated(stdout, '')
    assert tolerate is False
    assert names == failures
    novel = [n for n in names
             if n not in gate.KNOWN_GATED_FAILURES
             and not any(p.match(n) for p in gate.KNOWN_GATED_PATTERNS)]
    assert novel == [CONVO_ROLES_NAME]
