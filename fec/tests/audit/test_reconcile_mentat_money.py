"""Gate 1 tolerance contract (decision B MW-6). Synthetic fixtures; includes the planted-$5,000 mutation check."""
from pathlib import Path
from datetime import date
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from audit import reconcile_mentat_money as R

FEC = {'A%06d' % i: 5000.0 for i in range(200)}


def test_closed_cycle_passes_when_identical_and_fails_on_planted_5000_row():
    assert R.gate_closed(FEC, dict(FEC))['pass']
    planted = dict(FEC); planted['A000007'] += 5000.0          # mutation: one planted $5,000 row
    res = R.gate_closed(FEC, planted)
    assert res['share_exact'] == 0.995 and res['agg_rel'] <= 0.01   # 99.5% exact, aggregate +0.5%...
    planted['A000008'] += 5000.0; planted['A000009'] += 5000.0     # ...three plants break the 99% bar
    assert not R.gate_closed(FEC, planted)['pass']


def test_closed_cycle_dollar_tolerance_and_aggregate_bar():
    near = {b: v + 0.99 for b, v in FEC.items()}
    assert R.gate_closed(FEC, near)['pass']                       # |delta| <= $1 is exact
    low = {b: v * 0.98 for b, v in FEC.items()}
    assert not R.gate_closed(FEC, low)['pass']                    # aggregate off 2%
    assert not R.gate_closed({}, {})['pass']                      # nothing to compare is not a pass


def test_current_cycle_aggregate_and_explanation_floor():
    m = dict(FEC); m['A000001'] -= 400.0                          # under the $500 floor: no class needed
    assert R.gate_current(FEC, m, {})['pass']
    m['A000002'] -= 5000.0                                        # planted $5,000 gap, unexplained
    res = R.gate_current(FEC, m, {})
    assert not res['pass'] and res['unexplained'] == ['A000002']
    assert R.gate_current(FEC, m, {'A000002': ('timing', 'x')})['pass']
    assert not R.gate_current(FEC, m, {'A000002': ('mentat_receipt_missing', 'x')})['pass']
    big = {b: v * 0.95 for b, v in FEC.items()}
    assert not R.gate_current(FEC, big, {b: ('timing', '') for b in FEC})['pass']   # classes never relieve the 1% bar


def test_classify_uses_committee_identity_for_misfiling_and_window_for_timing():
    pin = date(2026, 6, 30)
    f = [(date(2024, 9, 13), 5000.0, 'C00850420')]
    assert R.classify(f, [], pin, unlinked=[(date(2024, 9, 20), 5000.0, 'C00850420', 'X')])[0] == 'recipient_misfiling'
    assert R.classify(f, [], pin, unlinked=[(date(2024, 9, 20), 5000.0, 'C00999999', 'Y')])[0] == 'mentat_receipt_missing'
    assert R.classify([(date(2026, 6, 10), 2500.0, 'C1')], [], pin)[0] == 'timing'
    assert R.classify([(date(2026, 1, 10), 2500.0, 'C1')], [(date(2026, 1, 12), 2500.0)], pin)[0] == 'timing'
    assert R.classify([], [(date(2025, 1, 1), 5000.0)], pin)[0] == 'mentat_only_receipts'
    assert R.classify([(date(2025, 3, 1), -500.0, 'C1')], [], pin)[0] == 'refund_or_redesignation'
