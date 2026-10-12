"""Conditional cash-pool attribution, independent of the industry allocation.

An accounting assumption, not an estimate of influence or an earmark. Never
invent missing dates, asset ownership, cash, or links to make a ledger balance.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import re

METHOD = 'dated-proportional-cash-v2'
OTHER = 'Other / unresolved funding'


def amount(value):
    if value is None or isinstance(value, bool):
        raise ValueError('A cash amount is missing.')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Invalid cash amount.') from None
    if not result.is_finite() or result < 0:
        raise ValueError('Negative or non-finite cash requires a reconciled correction/refund event.')
    return result


def day(value):
    return date.fromisoformat(str(value or '')[:10]).isoformat()


def cents(value):
    return float(value.quantize(Decimal('.01')))


def run_pool(opening, events, expected_closing):
    """Dated normalized cash ledger, all amounts nonnegative and source-backed.

    Each opening balance is a mapping of funding labels to cash, including an
    explicit Other/unresolved label. A transfer is ONE event with send/receive
    dates, not two donations. Receipts and arriving transfers precede payments
    on a shared date; same-day transfer cycles are refused. Refunds remove the
    named source's balance. Rounded results are views of Decimal calculations.
    """
    pools = {committee: {source: amount(value) for source, value in sources.items()}
             for committee, sources in opening.items()}
    schedules, seen, in_transit = defaultdict(list), set(), {}
    initial = sum((sum(p.values()) for p in pools.values()), Decimal(0))
    deposited, spent = Decimal(0), Decimal(0)
    receipts_by_source = defaultdict(Decimal)
    for sources in pools.values():
        for source, value in sources.items():
            receipts_by_source[source] += value
    for original in events:
        row = dict(original)
        identifier = str(row.get('id') or '')
        if not identifier or identifier in seen:
            raise ValueError('Cash events need unique source identifiers.')
        seen.add(identifier)
        row['amount'] = amount(row.get('amount'))
        row['date'] = day(row.get('date'))
        if row.get('kind') not in ('receipt', 'transfer', 'candidate', 'operating', 'refund', 'outside_scope'):
            raise ValueError('Unclassified cash event.')
        if row.get('committee') not in pools:
            raise ValueError('Cash event has no opening committee balance.')
        schedules[row['date']].append(row)
        if row['kind'] == 'transfer':
            received = day(row.get('received_date'))
            if received < row['date'] or row.get('destination') not in pools or row['destination'] == row['committee']:
                raise ValueError('Transfer dates or committee boundary are unresolved.')
            schedules[received].append({**row, 'kind': 'arrival'})
    allocations, same_day_dates = [], []

    def debit(committee, value, source=None):
        pool = pools[committee]
        balance = sum(pool.values(), Decimal(0))
        if value > balance + Decimal('.00000001'):
            raise ValueError(f'Insufficient dated cash for {committee}; no balancing money was invented.')
        if source is not None:
            if value > pool.get(source, 0):
                raise ValueError('Refund exceeds the remaining balance of its identified funding source.')
            removed = {source: value}
        elif not value:
            removed = {}
        else:
            removed = {key: value * share / balance for key, share in pool.items()}
        for key, value in removed.items():
            pool[key] -= value
        return removed

    def credit(committee, sources):
        for key, value in sources.items():
            pools[committee][key] = pools[committee].get(key, Decimal(0)) + value

    for stamp, rows in sorted(schedules.items()):
        if any(r['kind'] in ('receipt', 'arrival') for r in rows) and any(r['kind'] not in ('receipt', 'arrival') for r in rows):
            same_day_dates.append(stamp)
        for row in sorted((r for r in rows if r['kind'] == 'receipt'), key=lambda r: r['id']):
            source = row.get('funding_source') or OTHER
            credit(row['committee'], {source: row['amount']})
            receipts_by_source[source] += row['amount']
            deposited += row['amount']
        # Process incoming transfers before outgoing transfers at each node.
        pending = sorted((r for r in rows if r['kind'] in ('transfer', 'arrival')), key=lambda r: (r['kind'], r['id']))
        while pending:
            progressed = False
            for row in pending[:]:
                if row['kind'] == 'arrival':
                    if row['id'] not in in_transit:
                        continue
                    credit(row['destination'], in_transit.pop(row['id']))
                else:
                    if any(r['kind'] == 'arrival' and r['destination'] == row['committee'] for r in pending):
                        continue
                    in_transit[row['id']] = debit(row['committee'], row['amount'])
                pending.remove(row)
                progressed = True
            if not progressed:
                raise ValueError('Same-day circular transfers have no established ordering.')
        # Proportional debits commute. Process identified refunds first so they
        # do not get diluted into other donors' expenses on the same day.
        for row in sorted((r for r in rows if r['kind'] not in ('receipt', 'transfer', 'arrival')),
                          key=lambda r: (r['kind'] != 'refund', r['id'])):
            if row['kind'] == 'refund' and not row.get('funding_source'):
                raise ValueError('A refund needs an identified funding source.')
            shares = debit(row['committee'], row['amount'], row.get('funding_source') if row['kind'] == 'refund' else None)
            spent += row['amount']
            allocations.append({**{k: v for k, v in row.items() if k != 'amount'},
                                'amount': cents(row['amount']), 'estimated_funding': {k: cents(v) for k, v in shares.items()}})
    closing = sum((sum(p.values()) for p in pools.values()), Decimal(0))
    if in_transit or abs(initial + deposited - spent - closing) > Decimal('.0000001'):
        raise ValueError('Cash conservation or transfer settlement failed.')
    if set(pools) != set(expected_closing):
        raise ValueError('Every committee needs a closing cash check.')
    for committee, pool in pools.items():
        if abs(sum(pool.values()) - amount(expected_closing[committee])) > Decimal('.01'):
            raise ValueError(f'Closing cash does not reconcile for {committee}.')
    return {'method': METHOD, 'status': 'estimated', 'allocations': allocations,
            'closing_funding': {k: {s: cents(v) for s, v in p.items()} for k, p in pools.items()},
            'source_funding': {k: cents(v) for k, v in receipts_by_source.items()},
            'same_day_assumption_dates': same_day_dates, 'cash_conservation_verified': True,
            'assumptions': ['All available cash is pooled proportionally, without donor earmarks.',
                            'Same-day receipts and arriving transfers precede outgoing transfers, refunds and other payments.',
                            'Unidentified opening cash and external receipts retain an Other / unresolved funding label.',
                            'Estimates describe funding under this rule, not donor intent, influence or causal effects.']}


def estimate_snapshots(snapshots, company, aliases):
    """Strict F3X adapter. Missing cash facts produce reasons, never zero estimates.

    Supports ordinary cash receipts and unique, exact-amount reciprocal PAC
    transfers. Asset lots/refunds require extra reconciliation before this
    automatic adapter can publish company totals. Prior-cycle donor ownership
    is unknown here; run_pool accepts an explicitly reconciled opening ledger.
    """
    from pac_evidence import name_key, receipt_role, source_link
    # F8 and F11 (Paul, 2026-09-25): the collector, the network and the map name
    # a transfer and count a reload through tools/transfer_rule.py; so does this.
    from transfer_rule import count_reloads_once, schedule_b_leg
    aliases = {name_key(value) for value in [company, *aliases] if name_key(value)}
    cycles = []
    for cycle in sorted({s['cycle'] for s in snapshots}):
        batch = [s for s in snapshots if s['cycle'] == cycle]
        known = {s['committee_id'] for s in batch}
        issues, opening, closing, events, pending_receipts, transfers = [], {}, {}, [], [], []
        def issue(committee, code, detail):
            value = {'committee_id': committee, 'check': code, 'detail': detail}
            if value not in issues:
                issues.append(value)
        for snapshot in batch:
            committee, total = snapshot['committee_id'], snapshot['totals']
            try:
                start, end = day(total.get('coverage_start_date')), day(total.get('coverage_end_date'))
                first, last = amount(total.get('cash_on_hand_beginning_period')), amount(total.get('last_cash_on_hand_end_period'))
                opening[committee], closing[committee] = {OTHER: first}, last
                if first:
                    issue(committee, 'opening_ownership', 'Opening cash has no reconciled donor ownership; carry forward a verified prior-period ledger.')
                reported_in, reported_out = amount(total.get('receipts')), amount(total.get('disbursements'))
                if abs(first + reported_in - reported_out - last) > Decimal('.01'):
                    issue(committee, 'summary_balance', 'Opening cash + receipts − disbursements does not match closing cash.')
                actual_in, actual_out = Decimal(0), Decimal(0)
                # F11: a reload - a row sharing committee, report type, transaction
                # id, file number, counterparty, amount, date and type with an
                # earlier row under another SUB_ID - counts once.
                schedules = {name: list(count_reloads_once(snapshot[name], name, committee))
                             for name in ('receipts', 'disbursements', 'expenditures')}
                for category, field, date_field in [('receipts', 'contribution_receipt_amount', 'contribution_receipt_date'),
                                                   ('disbursements', 'disbursement_amount', 'disbursement_date'),
                                                   ('expenditures', 'expenditure_amount', 'disbursement_dt')]:
                    for row in schedules[category]:
                        if row.get('memoed_subtotal') or row.get('memo_code') == 'X':
                            continue
                        value = amount(row.get(field))
                        if category == 'receipts': actual_in += value
                        else: actual_out += value
                        if row.get('filing_form') != 'F3X':
                            issue(committee, 'form_scope', 'This cash adapter requires Form 3X cash schedules.')
                        # Schedule E expenditure_date may be dissemination;
                        # use the actual payment field for the cash timeline.
                        try:
                            stamp = day(row.get(date_field))
                            if not start <= stamp <= end: raise ValueError()
                        except ValueError:
                            issue(committee, 'payment_dates', 'A cash receipt/payment date is missing or outside the financial summary period.')
                            continue
                        event = {'id': category + ':' + str(row.get('sub_id') or ''), 'committee': committee,
                                 'date': stamp, 'amount': str(value), 'source_url': source_link(row)}
                        if not row.get('sub_id'):
                            issue(committee, 'source_id', 'A transaction lacks its source identifier.')
                        if category == 'receipts':
                            role = receipt_role(row)
                            matched = name_key(row.get('contributor_name')) in aliases
                            if role in ('in_kind_contribution', 'asset_liquidation'):
                                issue(committee, 'asset_cash_links', 'Noncash receipts and liquidation proceeds need matched donor lots and offset entries before cash attribution.')
                            if role.startswith('unverified_'):
                                issue(committee, 'receipt_role', 'A receipt has no verified cash transaction role.')
                            pending_receipts.append((row, {**event, 'kind': 'receipt', 'funding_source': company if matched and role == 'contribution' else OTHER}))
                        elif category == 'expenditures':
                            if row.get('is_notice') is not False or row.get('line_number') != '24':
                                issue(committee, 'regular_ie', 'Only regular-report Schedule E payments may enter the cash ledger.')
                            direction = {'S': 'Support', 'O': 'Oppose'}.get(row.get('support_oppose_indicator'))
                            if not direction or not row.get('candidate_id'):
                                issue(committee, 'candidate_identity', 'Candidate spending needs a candidate ID and support/opposition direction.')
                            events.append({**event, 'kind': 'candidate', 'candidate_id_as_filed': row.get('candidate_id'),
                                           'candidate_name_as_filed': row.get('candidate_name'), 'direction': direction})
                        else:
                            description = name_key(str(row.get('disbursement_description') or '') + ' ' + str(row.get('memo_text') or ''))
                            if re.search(r'IN KIND|NOT LIQUIDATED', description):
                                issue(committee, 'asset_cash_links', 'Noncash receipts and liquidation proceeds need matched donor lots and offset entries before cash attribution.')
                            line = row.get('line_number')
                            if line == '24':
                                issue(committee, 'duplicate_ie_scope', 'Schedule B line 24 may duplicate Schedule E; reconcile the payment linkage.')
                            if line in ('28A', '28B', '28C'):
                                issue(committee, 'refund_links', 'Contribution refunds require an identified original funding source.')
                            destination = row.get('recipient_committee_id')
                            # F8: "one rule, by transaction type, everywhere". A row
                            # moves money to another committee when transfer_rule
                            # names it a leg - by its type (the index's 24G/24K),
                            # by the old line 22/23 rule only when it records none.
                            leg, _basis = schedule_b_leg(row)
                            if leg and destination:
                                if destination in known:
                                    transfers.append((row, {**event, 'kind': 'transfer', 'destination': destination}))
                                else:
                                    events.append({**event, 'kind': 'outside_scope', 'destination': destination})
                            elif line in ('21B', '29'):
                                events.append({**event, 'kind': 'operating'})
                            else:
                                issue(committee, 'disbursement_role', 'A disbursement needs an explicit cash category before allocation.')
                for label, actual, reported in [('receipts', actual_in, reported_in), ('disbursements', actual_out, reported_out)]:
                    difference = reported - actual
                    if abs(difference) > Decimal('.01'):
                        issue(committee, label + '_gap', f'Reported {label} differ from dated nonmemo records by ${difference:,.2f}; collect/reconcile the missing cash entries.')
                actual_ie = sum((amount(r.get('expenditure_amount')) for r in schedules['expenditures']
                                 if not r.get('memoed_subtotal') and r.get('memo_code') != 'X'), Decimal(0))
                if abs(actual_ie - amount(total.get('independent_expenditures'))) > Decimal('.01'):
                    issue(committee, 'independent_expenditures_gap', 'Regular-report candidate spending does not reconcile to the independent-expenditure summary.')
            except (ValueError, TypeError) as error:
                issue(committee, 'cash_inputs', str(error))
        consumed = set()
        receipt_index = defaultdict(list)
        for row, event in pending_receipts:
            receipt_index[event['committee'], row.get('contributor_id'), amount(event['amount'])].append((row, event))
        for row, transfer in transfers:
            matches = [(r, e) for r, e in receipt_index[transfer['destination'], transfer['committee'], amount(transfer['amount'])]
                       if e['date'] >= transfer['date']]
            same_day = [(r, e) for r, e in matches if e['date'] == transfer['date']]
            matches = same_day or matches
            if len(matches) != 1 or matches[0][1]['id'] in consumed:
                issue(transfer['committee'], 'transfer_pairing', 'An outgoing PAC transfer lacks a unique reciprocal receipt with matching IDs, amount and valid dates.')
            else:
                receipt = matches[0][1]
                consumed.add(receipt['id'])
                events.append({**transfer, 'received_date': receipt['date'], 'receipt_source_url': receipt['source_url']})
        for row, event in pending_receipts:
            if event['id'] not in consumed:
                if row.get('contributor_id') in known:
                    issue(event['committee'], 'transfer_pairing', 'An internal transfer receipt lacks its matching outgoing payment.')
                events.append(event)
        result = {'cycle': cycle, 'status': 'withheld', 'issues': issues, 'method': METHOD,
                  'committee_ids': sorted(known), 'company': company, 'allocations': []}
        if not issues:
            try:
                result.update(run_pool(opening, events, closing))
            except ValueError as error:
                issue('', 'ledger_reconciliation', str(error))
        cycles.append(result)
    return {'method': METHOD, 'cycles': cycles,
            'meaning': 'Estimated company funding under proportional cash pooling; not traced dollars or donor intent.',
            'requirements': ['All-donor cash receipts, opening cash and retained balances.',
                             'Dated payments, operating costs and linked refunds.',
                             'Donor-linked asset liquidations and reciprocal PAC transfers.',
                             'Separate support/opposition and as-filed candidate identities.']}


def write_estimates(directory):
    """Rebuild a sidecar from unchanged, hash-verified source evidence."""
    import hashlib
    import json
    from pathlib import Path
    from datetime import datetime, timezone
    from fec_transfer_graph import atomic_json
    directory = Path(directory)
    evidence_bytes = (directory / 'evidence.json').read_bytes()
    evidence = json.loads(evidence_bytes)
    source = (directory / 'snapshots.json').read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    if digest != evidence.get('source_snapshot_sha256'):
        raise ValueError('The source snapshot hash differs from its evidence receipt.')
    result = estimate_snapshots(json.loads(source), evidence['company'], evidence['aliases'])
    result.update(source_snapshot_sha256=digest, evidence_sha256=hashlib.sha256(evidence_bytes).hexdigest(),
                  generated_at=datetime.now(timezone.utc).isoformat(),
                  method_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    atomic_json(directory / 'cash-estimates.json', result)
    return result


if __name__ == '__main__':
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', required=True)
    args = parser.parse_args()
    value = write_estimates(args.evidence_dir)
    print(json.dumps({'method': METHOD, 'cycles': [{'cycle': c['cycle'], 'status': c['status'], 'unresolved_checks': len(c['issues'])} for c in value['cycles']]}))
