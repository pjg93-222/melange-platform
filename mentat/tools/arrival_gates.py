"""Earliest arrival: which rows of a downstream committee network the root's
money could have reached. One rule, beside transfer_rule.py, for every reader.

F12 (Paul approved 2026-09-25). The downstream walk went by cycle, not by
date, so a branch whose onward money moved BEFORE the root's money arrived
still entered the network: Senate Leadership Fund paid Conservative Americans
PAC $895,000 on 2023-12-04, but that committee's only onward payment - $41,727.99
to America Fund - is dated 2023-02-08, and America Fund's whole 2024 outflow
was counted downstream of SLF. The rule:

  1. gate(root) is the first day of the root's cycle window, and all of a
     root's own activity counts.
  2. A transfer X -> Y dated t is traversable only if t >= gate(X);
     gate(Y) is the earliest t over Y's traversable incoming transfers.
     Gates are iterated to a fixed point; they only fall, so loops end.
  3. A reached committee's activity - independent expenditures, gifts to
     candidates, onward transfers, every other disbursement - counts
     downstream only when dated on or after its gate.
  4. A back-edge to a root never re-adds the root's activity: a root's gate
     is pinned and the root is counted once.
  5. A row with no usable date cannot be ordered. Under D-040 ambiguity keeps
     dollars: the row counts, flagged date_unverified, and an undated
     transfer lets money through at its source's own gate.

A pair X -> Y is followed, as before this rule, only when X's counted
transfers to Y net above zero; the money arrives on the earliest counted
positive transfer. When a refund dated earlier than the transfer it refunds
flips that netting as X's gate falls, the lower gate stands: the rule keeps
dollars on ambiguity, it never drops them.

CARRYOVER (Paul approved option (b), 2026-09-25): "a root's transfers in the
previous cycle also open the recipient's gate in the next cycle. The cash a
committee carries across the boundary came from those transfers, so its
spending before new money arrives stays downstream." For a root in cycle C the
walk runs over one timeline from the first day of cycle C-1: a committee first
reached during C-1 opens in C at C's first day (gate_source 'carryover'); one
first reached during C keeps its arrival date ('in-cycle'); a root is 'root'.
Only cycle-C rows are ever counted - a C-1 transfer only opens a gate and is
disclosed as such, never as a cycle-C flow. One cycle back only. The default
follows onward C-1 transfers too, each dated on or after its own source's
arrival (variant ii, 'onward'); 'direct' opens only the root's own C-1
recipients (variant i). When the root's C-1 snapshot is not saved the
carryover is 'unknown' and the in-cycle rule stands alone.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal
import re

UNVERIFIED = 'date_unverified'
#: Where a committee/cycle's gate came from.
ROOT, CARRYOVER, IN_CYCLE = 'root', 'carryover', 'in-cycle'
#: 'onward' (the rule, variant ii), 'direct' (variant i: the root's own C-1
#: recipients only) or 'off' (no carryover: the in-cycle rule alone).
CARRYOVER_MODES = ('onward', 'direct', 'off')
#: The date each saved openFEC schedule records for a row, first choice first.
#: Schedule E's expenditure date is the payment or dissemination, whichever came
#: first; its dissemination date answers only when the expenditure date is blank.
DATE_FIELDS = {
    'disbursements': ('disbursement_date',),
    'expenditures': ('expenditure_date', 'dissemination_date'),
    'receipts': ('contribution_receipt_date',),
}
_DAY = re.compile(r'(\d{4})-(\d{2})-(\d{2})')


def row_date(row, schedule):
    """'YYYY-MM-DD' for a saved row, or '' when it carries no usable date."""
    for field in DATE_FIELDS[schedule]:
        text = str(row.get(field) or '').strip()[:10]
        found = _DAY.fullmatch(text)
        if not found:
            continue
        try:
            date(int(found[1]), int(found[2]), int(found[3]))
        except ValueError:
            continue
        return text
    return ''


def cycle_start(cycle):
    """The first day of an FEC two-year cycle's window: 2024 -> '2023-01-01'."""
    digits = re.findall(r'\d{4}', str(cycle))
    if not digits:
        raise ValueError(f'{cycle!r} is not a cycle')
    year = int(digits[-1])
    year += year % 2
    return f'{year - 1}-01-01'


def previous_cycle(cycle):
    """The cycle before `cycle`: '2026' -> '2024'. One cycle back is the limit."""
    return str(int(cycle_start(cycle)[:4]) - 1)


def cycle_words(cycle):
    """The page's words for a cycle: '2024' -> '2023-2024'."""
    first = cycle_start(cycle)[:4]
    return f'{first}-{int(first) + 1}'


def counts(when, gate):
    """Does a row dated `when` count under `gate`? No gate (a root) and no date
    (D-040: ambiguity keeps dollars) both count."""
    return gate is None or not when or when >= gate


def earliest_arrival(roots, legs):
    """{key: gate} for every committee/cycle the roots' money can reach, by date.

    roots maps each root key to its pinned gate (cycle_start). legs(key) gives
    the key's outgoing committee transfers as (destination key, date or '',
    Decimal amount) - read once per key, whatever its gate does later. A
    destination is reached when its source's counted transfers to it (all of a
    root's; a reached committee's dated on or after its gate, or undated) net
    above zero, and arrives on the earliest counted positive transfer - on the
    source's own gate when that transfer is undated or a root's transfer
    predates the root's window.
    """
    gates = dict(roots)
    read = {}
    pending, queued = list(roots), set(roots)
    while pending:
        key = pending.pop()
        queued.discard(key)
        gate, root = gates[key], key in roots
        if key not in read:
            read[key] = list(legs(key))
        net, arrival = defaultdict(Decimal), {}
        for destination, when, value in read[key]:
            if not root and when and when < gate:
                continue
            net[destination] += value
            if value > 0:
                at = when if when and when >= gate else gate
                if destination not in arrival or at < arrival[destination]:
                    arrival[destination] = at
        for destination in sorted(net, key=str):
            if net[destination] <= 0 or destination not in arrival or destination in roots:
                continue
            if destination not in gates or arrival[destination] < gates[destination]:
                gates[destination] = arrival[destination]
                if destination not in queued:
                    queued.add(destination)
                    pending.append(destination)
    return gates


def carryover_arrivals(roots, legs, onward=True):
    """{key: arrival} for every previous-cycle committee the roots' previous-cycle
    transfers reach - the committees whose gate opens at the next cycle's first day.

    roots maps each root's previous-cycle key to that cycle's first day (pinned);
    legs is earliest_arrival's. onward=True (variant ii, the rule) follows every
    transfer dated on or after its own source's arrival; onward=False (variant
    i) counts only the roots' own transfers.
    """
    gates = earliest_arrival(roots, legs if onward else (lambda key: legs(key) if key in roots else []))
    return {key: gate for key, gate in gates.items() if key not in roots}


def opened_by(roots, reached, legs):
    """{key: [(source key, first date, Decimal amount)]}: the previous-cycle transfers
    that could reach each carried-over committee - its disclosure, never a flow."""
    gates, found = {**roots, **reached}, defaultdict(dict)
    for source in sorted(gates, key=str):
        gate, root = gates[source], source in roots
        for destination, when, value in legs(source):
            if destination not in reached or value <= 0 or (not root and when and when < gate):
                continue
            first, total = found[destination].get(source, ('', Decimal(0)))
            found[destination][source] = (min(first, when) if first and when else first or when, total + value)
    return {key: sorted(((source, first, total) for source, (first, total) in value.items()), key=str)
            for key, value in found.items()}
