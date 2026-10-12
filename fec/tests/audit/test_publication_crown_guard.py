# -*- coding: utf-8 -*-
"""The publication guard and the published surfaces must share ONE crown law (D-030/D-031).

Reproduces the 2026-09-17 chain refusal. sync_live_dashboard_alias.py -> audit_source_to_dashboard
-> audit_measurement_layers rejected a correctly built generation with

    ValueError: Dashboard publication refused: Overall label mismatch: <name>; ...

because the guard still derived the overall Champion crown from the RETIRED
"Legislative+Press Tier 1 -> Champion" overlay, while build_dashboard_bundle.py:96 and the member
desk publish the crown the rulings put in force: the active honors board's top 13 with a
qualifying action. Every member whose board seat and Tier-1 seat disagree failed the guard.

Structural, not snapshotted (see HEAD 4dc148fc1): nothing here pins a member name or a board
size. The reproduction skips itself in a generation where the two rules happen to coincide.

2026-09-17 SECOND DEFECT (FIXUP-CROWN). Realigning the guard to the honors board made the guard
and the surfaces agree with each other while BOTH disagreed with the ruled board: generation
4f2b24fec711a3920df0a6e7 was seated on the PRIMACY_BOARD=factor revert switch (stance/
build_rank_honors.py and stance/honors_views.py each carried their own `or 'factor'` fallback)
while tier_util seated the displayed tier on the ruled default per_area_v1. The crown followed a
board no surface showed - Tier-2 members crowned, Tier-1 members not. Everything below the first
three tests passed throughout, which is exactly why the missing assertion is that the honors
board and tier_util's seating are ONE board.
"""
import json
import sys
import unicodedata
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import moneyball_core as MC
import tier_util as TU
from audit import audit_measurement_layers as audit
from paths import ROOT
from stance.build_rank_honors import build_honors
from stance.honors_views import validate_honors

LIVE = Path(ROOT)
REQUIRED = ('category_stance.json', 'member_desk_bundle.json', 'dashboard_data.json',
            'data/stance/rank_honors/active.json')

# Only the LIVE-artifact half is gated. The synthetic-fixture test and the two source
# pins below assert the mechanism itself and must run in a tree with no generation --
# a module-level skip here is how a crown regression would sail through a clean checkout.
live_only = pytest.mark.skipif(
    not all((LIVE / relative).is_file() for relative in REQUIRED),
    reason='no published generation in this tree')


def nfc(value):
    return unicodedata.normalize('NFC', str(value))


def source(relative):
    return (LIVE / 'fec' / relative).read_text(encoding='utf-8')


@pytest.fixture(scope='module')
def published():
    """Everything the guard compares, read straight off the published artifacts."""
    read = lambda relative: json.loads((LIVE / relative).read_text(encoding='utf-8'))
    labels, desk, dashboard = read('category_stance.json'), read('member_desk_bundle.json'), read('dashboard_data.json')
    # A generation seated on a stale board basis is refused by the loader (that refusal IS the
    # fix). Catch it here so exactly the tests that assert the board source go red, instead of
    # erroring out the whole suite and hiding the checks that still hold.
    try:
        honors, refusal = audit.active_overall_board(LIVE), None
    except ValueError as error:
        pointer = read('data/stance/rank_honors/active.json')
        honors = read('data/stance/rank_honors/%s/rank_honors.json' % pointer['generation_id'])
        refusal = '%s: %s' % (type(error).__name__, error)
    assert honors is not None, 'active honors generation did not load'
    names = [member['name'] for member in desk['members']]
    tiers, _positions = TU.legislative_press_tiers(names, root=str(LIVE))
    state = {
        'matrix': {nfc(k): v for k, v in labels['matrix'].items()},
        'caps': (labels.get('gate') or {}).get('overall_capped') or {},
        'tiers': {nfc(k): v for k, v in tiers.items()},
        'letters': {v: k for k, v in desk['tiername'].items()},
        'desk': {nfc(m['name']): m for m in desk['members']},
        'dashboard': dashboard,
        'board': {nfc(n) for n in ((honors.get('overall_board') or {}).get('top13') or ())},
        'honors_overall': {nfc(k): (v.get('overall') or {}) for k, v in (honors.get('members') or {}).items()},
        'board_mode': (honors.get('overall_board') or {}).get('mode'),
        'refusal': refusal,
    }
    state['tier1'] = {n for n, tier in state['tiers'].items() if tier == 1}
    return state


def label_for(state, name, board):
    """The overall letter one crown rule assigns; board=None is the retired Tier-1 overlay."""
    return state['letters'][MC.overall_display_tier(
        state['matrix'][name], state['tiers'][name], name, state['caps'], board)]


def disagreements(state, board):
    bad = []
    for index, raw in enumerate(state['dashboard']['name']):
        name = nfc(raw)
        expected = label_for(state, name, board)
        if state['dashboard']['gen'][index] != expected or state['desk'][name]['overall_stance'] != expected:
            bad.append(name)
    return bad


@live_only
def test_published_surfaces_already_agree_with_each_other(published):
    """Desk and dashboard were never in conflict; only the guard's expectation was."""
    split = [nfc(raw) for index, raw in enumerate(published['dashboard']['name'])
             if published['desk'][nfc(raw)]['overall_stance'] != published['dashboard']['gen'][index]]
    assert not split, 'desk and dashboard overall labels differ for %s' % split[:10]


@live_only
def test_retired_tier1_overlay_refuses_the_published_generation(published):
    """REPRODUCTION: the pre-fix guard expectation (board=None) rejects a correct generation."""
    moved = published['board'] ^ published['tier1']
    if not moved:
        pytest.skip('board top 13 and Legislative+Press Tier 1 coincide in this generation')
    assert set(disagreements(published, None)) == moved, (
        'the overlay must fail on exactly the members whose board seat and Tier-1 seat disagree')


@live_only
def test_board_crown_accepts_the_published_generation(published):
    """THE FIX: with the board supplied, every published overall label reconciles."""
    assert not disagreements(published, published['board'])


@live_only
def test_a_genuinely_wrong_overall_label_still_fails(published):
    """NEGATIVE CONTROL: the board rule is not vacuous - it still catches a bad label."""
    dashboard = json.loads(json.dumps(published['dashboard']))
    index = 0
    name = nfc(dashboard['name'][index])
    wrong = 'O' if dashboard['gen'][index] != 'O' else 'C'
    dashboard['gen'] = wrong + dashboard['gen'][1:]
    tampered = dict(published, dashboard=dashboard)
    assert disagreements(tampered, published['board']) == [name]


@live_only
def test_every_crown_carries_a_qualifying_action(published):
    """D-030: Champion is the board's top 13 WITH a qualifying action, never the seat alone."""
    for name in sorted(published['board']):
        row = published['honors_overall'].get(name) or {}
        assert row.get('recognition') == 'Champion', name
        assert row.get('reason') == 'active_overall_board_top13', name
        assert row.get('evidence_status') == 'observed', name
        assert (row.get('action_count') or 0) >= 1, name
        assert label_for(published, name, published['board']) == published['letters']['Champion'], name


@live_only
def test_uncrowned_members_carry_the_analytic_band(published):
    """D-030: every non-crowned member shows the analytic band, never a crown."""
    crown = published['letters']['Champion']
    for raw in published['dashboard']['name']:
        name = nfc(raw)
        if name in published['board']:
            continue
        assert label_for(published, name, published['board']) != crown, name


@live_only
def test_full_audit_expected_gen_string_matches_the_published_dashboard(published):
    """audit_full.py's dashboard crown-parity check must agree after the same fix."""
    codes = {'Champion': 'C', 'Defender': 'F', 'Ally': 'A', 'Highly Persuadable': 'H',
             'Persuadable': 'P', 'Lean Persuadable': 'G', 'True Neutral': 'T', 'Lean Skeptic': 'L',
             'Neutral': 'N', 'Skeptic': 'S', 'Highly Skeptical': 'K', 'Opponent': 'O', '—': '-'}
    def expected(board):
        return ''.join(codes.get(MC.overall_display_tier(
            published['matrix'].get(nfc(m), {}), published['tiers'].get(nfc(m), 0),
            nfc(m), published['caps'], board), '-') for m in published['dashboard']['name'])
    assert expected(published['board']) == published['dashboard']['gen']
    if published['board'] ^ published['tier1']:
        assert expected(None) != published['dashboard']['gen']



def fixture_generation(size=40, *, silent=()):
    """A published-shaped generation with no artifacts: 40 members, board = roster order.

    build_honors with no board_metadata seats the fixture board on the same 13/37/85
    buckets tier_util uses, so 'priority tier 1' and 'top 13' are the same thirteen names
    here for the same reason they are in production -- which is the property under test.
    """
    roster = {'Member %03d' % i: {'member_id': 'M%06d' % i, 'chamber': 'House'} for i in range(size)}
    actions = []
    for index, name in enumerate(roster):
        if name in silent:
            continue
        measure = '119:hr:%d' % (index + 1)
        event = 'legislative|M%06d|%s' % (index, measure)
        actions.append({'member_id': 'M%06d' % index, 'measure_id': measure, 'overall_event_id': event,
                        'evidence_id': event + '|Field', 'category_id': 'Field', 'congress': 119,
                        'kind': 'legislative', 'role': 'sponsor', 'weight': str(size - index),
                        'decay': '1', 'alignment': 1, 'position_on_measure': 1,
                        'tensor_position_scoped': True})
    doc = build_honors(roster, ['Field'], actions, {}, list(roster), '2026-09-17')
    # The published shape the validator expects; nothing here reads or writes a file.
    doc['generation_id'] = 'b' * 24
    return roster, list(roster), doc


def test_only_the_boards_tier_one_is_ever_crowned_from_a_synthetic_generation():
    """D-030/D-031 on a fixture: the board is the ONE crown source, tier 2 can never win it.

    Four claims, none of which needs a published artifact:
      1. every Tier-1 member with a qualifying action IS crowned, in the honors record and
         in the display function both surfaces call;
      2. no member outside Tier 1 is crowned by either, even when the raw tier says 1 --
         the board, not the tier, decides once a board is supplied;
      3. a Tier-1 member with no qualifying action is refused at build time, fail-closed,
         rather than crowned on the seat alone;
      4. a generation whose crown, top 13 or tier map is moved off the board is rejected.
    """
    roster, board, doc = fixture_generation()
    metadata = doc['overall_board']
    top13, tiers = set(metadata['top13']), metadata['priority_tiers']
    assert len(top13) == 13 and {n for n, tier in tiers.items() if tier == 1} == top13

    # 1. Tier 1 with action -> crowned, by the record and by the display function.
    for name in sorted(top13):
        row = doc['members'][name]['overall']
        assert (row['recognition'], row['reason']) == ('Champion', 'active_overall_board_top13'), name
        assert row['evidence_status'] == 'observed' and row['action_count'] >= 1, name
        assert MC.overall_display_tier({}, tiers[name], name, {}, top13) == 'Champion', name

    # 2. Everyone else -> never crowned, including with a forged Tier-1 input.
    outside = sorted(set(roster) - top13)
    assert tiers[outside[0]] == 2, 'the fixture must exercise a real Tier-2 member'
    for name in outside:
        assert doc['members'][name]['overall']['recognition'] is None, name
        assert MC.overall_display_tier({}, tiers[name], name, {}, top13) != 'Champion', name
        assert MC.overall_display_tier({}, 1, name, {}, top13) != 'Champion', name
    # NEGATIVE CONTROL: the retired overlay (board=None) crowns exactly what the board rule
    # refuses, so claim 2 is a real constraint and not a tautology about empty inputs.
    assert MC.overall_display_tier({}, 1, outside[0], {}, None) == 'Champion'

    # 3. A seat without a qualifying action is refused, never crowned on the seat alone.
    with pytest.raises(ValueError, match='Champion lacks qualifying action'):
        fixture_generation(silent={board[0]})

    # 4. The validator rejects every way of crowning outside the board.
    validate_honors(deepcopy(doc))
    forged_crown = deepcopy(doc)
    forged_crown['members'][outside[0]]['overall']['recognition'] = 'Champion'
    with pytest.raises(ValueError, match='crown differs from canonical top 13'):
        validate_honors(forged_crown)
    forged_seat = deepcopy(doc)
    forged_seat['overall_board']['top13'] = [outside[0]] + metadata['top13'][1:]
    with pytest.raises(ValueError, match='crown differs from canonical top 13'):
        validate_honors(forged_seat)
    forged_tier = deepcopy(doc)
    forged_tier['overall_board']['priority_tiers'][outside[0]] = 1
    with pytest.raises(ValueError, match='fixed active board buckets'):
        validate_honors(forged_tier)


def test_guard_and_full_audit_read_the_board_not_the_overlay():
    """Source pins: neither verifier may fall back to the retired overlay call signature."""
    layers = source('audit/audit_measurement_layers.py')
    assert 'MC.overall_display_tier(matrix[n],tiers[n],n,overall_caps,board_top13)' in layers
    assert 'def active_overall_board(root):' in layers
    assert "'Overall crown lacks a qualifying action: '+n" in layers
    full = source('audit/audit_full.py')
    assert "_display_board={m:_mc.overall_display_tier(MAT.get(m,{}),_tier.get(m,0),m,_oc,_board13)" in full
    assert "_expected_gen=''.join(_tc.get(_display_board.get(m,'—'),'-')" in full


@live_only
def test_active_generation_loads_under_the_ruled_board_basis(published):
    """FIXUP-CROWN: the honors board basis must BE the dial tier_util seats on.

    Fails on the 2026-09-17 artifacts: the generation carries mode 'factor' while tier_util
    seats on 'per_area_v1', so the crown was drawn from the revert switch. Rebuild the
    generation (build_rank_honors.py --publish) after the producer fix to clear it.
    """
    assert published['refusal'] is None, published['refusal']
    assert published['board_mode'] == TU.PRIMACY_BOARD, (
        'honors board basis %r but tier_util seats on %r'
        % (published['board_mode'], TU.PRIMACY_BOARD))


@live_only
def test_honors_board_seats_exactly_the_ruled_tier_one(published):
    """FIXUP-CROWN REPRODUCTION: crown == the ruled board's Tier 1, on every surface.

    This is the assertion whose absence let the publication guard pass on wrong surfaces.
    On the 2026-09-17 artifacts it fails with Tier-2 members crowned (Crapo, Rounds, Cramer)
    and Tier-1 members uncrowned (Huizenga, Young Kim, Meuser). Structural: no name, no size.
    """
    crowned_below_tier1 = sorted(
        (name, published['tiers'].get(name)) for name in published['board']
        if published['tiers'].get(name) != 1)
    tier1_uncrowned = sorted(published['tier1'] - published['board'])
    assert not crowned_below_tier1 and not tier1_uncrowned, (
        'crowned outside the ruled Tier 1: %s; ruled Tier 1 left uncrowned: %s'
        % (crowned_below_tier1, tier1_uncrowned))


@live_only
def test_the_ruled_tier_one_comparison_is_not_vacuous(published):
    """NEGATIVE CONTROL: swapping one seat off the ruled board must fail the same predicate."""
    outside = sorted(n for n in published['tiers'] if published['tiers'][n] != 1)
    assert outside, 'every member sits in Tier 1; the comparison would be vacuous'
    tampered = (published['board'] - {sorted(published['board'])[0]}) | {outside[0]}
    assert sorted((n, published['tiers'].get(n)) for n in tampered
                  if published['tiers'].get(n) != 1), 'a non-Tier-1 seat went undetected'


def test_honors_producer_and_loader_read_the_board_basis_from_tier_util():
    """Source pins: neither honors file may carry its own board-dial fallback again."""
    for relative in ('stance/build_rank_honors.py', 'stance/honors_views.py'):
        text = source(relative)
        assert "or 'factor')" not in text, relative
        assert "(os.environ.get('PRIMACY_BOARD') or TU.PRIMACY_BOARD).strip().lower()" in text, relative
    assert "sources.append(TU.PER_AREA_GRADE_PATH)" in source('stance/build_rank_honors.py')


def test_no_published_surface_re_derives_the_crown_from_the_tier():
    """Source pins: workbook column G and its verifier read the board, like the pages do."""
    workbook_producer = source('stance/sync_stance_g.py')
    assert "from stance.honors_views import load_honors" in workbook_producer
    assert "_BOARD13=set((load_honors(B).get('overall_board') or {}).get('top13') or ())" in workbook_producer
    assert "overall_display_tier(M.get(n,{}),_tier.get(n,0),n,OVERALL_CAPS,_BOARD13)" in workbook_producer
    layers = source('audit/audit_measurement_layers.py')
    assert 'expected=MC.overall_display_tier(matrix[n],tiers[n],n,overall_caps,board_top13)' in layers
    full = source('audit/audit_full.py')
    assert '_display={m:_mc.overall_display_tier(MAT.get(m,{}),_tier.get(m,0),m,_oc) for m in MAT}' not in full
