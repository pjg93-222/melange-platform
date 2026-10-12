"""Collect dated, source-linked PAC legs; never label pooled money as traced dollars.

Reads the encrypted FEC key store. Cache receipts include the public query and
response hash, never credentials. New output directories force a fresh snapshot.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import time
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fec_key_store import load_keys
from fec_transfer_graph import atomic_json
from transfer_rule import (CANDIDATE, FALLBACK_BASIS, RELOAD_KEY, TRANSFER, TRANSFER_TYPES, count_reloads_once,
                           new_reload_census, schedule_b_leg, transaction_type)


def money(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError('Missing or invalid monetary evidence; no zero is inferred.') from error
    if not result.is_finite() or result * 100 != (result * 100).to_integral_value():
        raise ValueError('Monetary evidence must be finite whole cents.')
    return result


def name_key(value):
    return ' '.join(re.findall(r'[A-Z0-9]+', str(value or '').upper()))


@lru_cache(maxsize=1)
def receipt_rules():
    return json.loads((Path(__file__).resolve().parents[1]/'inputs/fec_receipt_rules.json').read_text())


def is_memo(row):
    return str(row.get('memo_code') or '').strip().upper() == 'X' or any(
        str(row.get(k) or '').strip().upper() in ('TRUE','1') for k in ('memoed_subtotal','is_memo_leg'))


def receipt_line_code(row):
    """Twin of collect.R fec_receipt_line_code(). A blank line number is missing
    information, not evidence: recover the schedule line from the transaction id
    prefix (SA17.621125) before classifying."""
    line = re.sub(r'^SA','',re.sub(r'[() ]','',str(row.get('line_number') or '').strip().upper()))
    if not line:
        found = re.match(r'^SA(\d+[A-Z]*)\.', str(row.get('transaction_id') or '').strip().upper())
        line = found.group(1) if found else ''
    return line


def receipt_role(row):
    rules = receipt_rules()
    receipt_description = name_key(str(row.get('receipt_type_full') or ''))
    memo_description = name_key(str(row.get('memo_text') or ''))
    description = (receipt_description + ' ' + memo_description).strip()
    memo = is_memo(row)
    if re.search(rules['sale_identity_pattern'],description) and re.search(rules['sale_event_pattern'],description):
        return 'asset_liquidation'
    form = str(row.get('filing_form') or '').strip().upper()
    line = receipt_line_code(row)
    role = next((role for role,lines in rules['forms'].get(form,{}).items() if line in lines),'unverified_receipt_role')
    special = form == 'F3X' and line == '17' and re.search(rules['special_account_pattern'], description)
    exact_descriptions = {name_key(value) for value in rules.get('non_giving_exact_descriptions', [])}
    receipt_exact = (bool(re.search(rules['non_giving_exact_pattern'], receipt_description))
                     or receipt_description in exact_descriptions)
    memo_exact = (bool(re.search(rules['non_giving_exact_pattern'], memo_description))
                  or memo_description in exact_descriptions)
    exact_non_giving = receipt_exact or memo_exact
    receipt_event_description = re.sub(rules['special_account_pattern'], '', receipt_description).strip()
    memo_event_description = re.sub(rules['special_account_pattern'], '', memo_description).strip()
    receipt_non_giving_event = bool(re.search(rules['non_giving_event_pattern'], receipt_event_description))
    memo_non_giving_event = bool(re.search(rules['non_giving_event_pattern'], memo_event_description))
    non_giving = (exact_non_giving or receipt_non_giving_event or memo_non_giving_event
                  or re.search(rules['non_giving_event_pattern'], description))
    # An account label describes where the receipt belongs, not what created
    # it. Explicit interest is still income in a special account. A separate
    # actual contribution description, however, conflicts with that event.
    event_description = ' '.join(re.sub(rules['special_account_pattern'], '', part)
                                 for part, is_non_giving in (
                                     (receipt_description, receipt_exact or receipt_non_giving_event),
                                     (memo_description, memo_exact or memo_non_giving_event))
                                 if not is_non_giving)
    # A redesignation moves money already given; CONTRIBUTION beside it names
    # that same money, so it never contradicts the redesignation.
    redesignation = re.search(rules['redesignation_event_pattern'], description)
    giving_event = (not redesignation) and re.search(rules['giving_event_pattern'], event_description)
    if special:
        role = ('other_receipt' if exact_non_giving and not giving_event else
                'unverified_other_receipt' if non_giving else 'contribution')
    elif role == 'unverified_other_receipt' and non_giving:
        role = 'unverified_other_receipt' if giving_event else 'other_receipt'
    if role == 'unverified_other_receipt' and re.search(rules['transfer_event_pattern'], receipt_description) and not giving_event:
        role = 'transfer_receipt'
    if role in ('unverified_other_receipt', 'unverified_receipt_role') and non_giving and giving_event:
        role = 'unverified_contradicted_receipt'
    # Exclusion requires an explicit compatible link to a retained parent;
    # committee/cycle/amount equality alone does not prove duplication. This
    # single-row classifier therefore returns the counted, flagged verdict.
    if memo and re.search(rules['disclosure_pattern'],description) and role != 'other_receipt':
        return 'unverified_disclosure_memo'
    if role == 'contribution' and re.search(rules['refund_review_pattern'], description):
        return 'unverified_refund_adjustment'
    if role == 'contribution' and re.search(r'IN KIND|NOT LIQUIDATED',description):
        return 'in_kind_contribution'
    return role


REDISCLOSURE_REASON = 'memo_redisclosure_of_counted_parent'


def _identity_text(value):
    return str(value if value is not None else '').strip().upper()


def _identity_day(value):
    text = str(value if value is not None else '').strip()[:10]
    return text if re.fullmatch(r'\d{4}-\d{2}-\d{2}', text) else ''


def _identity_cents(value):
    """The amount to the cent, as schema.R's mentat_filing_identity_amount keys it; None when unreadable."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return amount.quantize(Decimal('0.01')) if amount.is_finite() else None


def memo_redisclosure_parents(rows, counted=None, role=None):
    """F13, the memo re-disclosure rule (Paul approved 2026-09-26, "Approve F13").

    Twin of schema.R mentat_memo_redisclosure_parents(); the cases in
    tests/fixtures/memo_redisclosure_cases.json pin both. A memo whose own text
    states that it re-discloses a gift already itemized (receipt role
    unverified_disclosure_memo: a memo row matching fec_receipt_rules.json's
    disclosure_pattern) duplicates the counted parent it restates: same company,
    same recipient committee, same contributor (FEC contributor_id when both
    rows carry one, else the normalised name), the same nonzero amount to the
    cent, dated on or before the memo, and not itself a memo. The money counts
    once, via the parent; with several such parents the latest-dated one is
    recorded (then the lowest sub_id). A memo that calls the gift unitemized
    restates no itemized parent. Missing or ambiguous information never excludes
    (D-040): with no parent the memo stays counted, flagged
    disclosure_memo_unmatched, and equal amounts without the statement stay a
    clue, never proof.

    A row takes part when its company match (if it has one) is accepted, it
    carries no excluded_reason, its role is giving, and counted(row) - the
    caller's own exclusions - allows it. role(row) may supply a receipt role the
    caller has already computed. Returns {memo sub_id: parent sub_id}.
    """
    unitemized = receipt_rules()['redisclosure_unitemized_pattern']
    role_of = role or receipt_role
    rows = list(rows)

    def eligible(row, row_role):
        return (row_role not in ('other_receipt', 'asset_liquidation')
                and _identity_text(row.get('company_match_status')) in ('', 'ACCEPTED')
                and not _identity_text(row.get('excluded_reason'))
                and (counted is None or bool(counted(row))))

    def key(row):
        return (_identity_text(row.get('company')), _identity_text(row.get('committee_id')),
                _identity_cents(row.get('contribution_receipt_amount')))

    memos = []
    for row in rows:
        if not is_memo(row):
            continue
        row_role = role_of(row)
        if row_role != 'unverified_disclosure_memo' or not eligible(row, row_role):
            continue
        memo_key, day = key(row), _identity_day(row.get('contribution_receipt_date'))
        statement = (name_key(row.get('receipt_type_full')) + ' ' + name_key(row.get('memo_text'))).strip()
        if memo_key[1] and memo_key[2] and day and not re.search(unitemized, statement):
            memos.append((row, memo_key, day))
    if not memos:
        return {}
    wanted = {memo_key for _, memo_key, _ in memos}
    pool = defaultdict(list)
    for row in rows:
        if is_memo(row):
            continue
        parent_key = key(row)
        if parent_key not in wanted:
            continue
        sub_id, day = str(row.get('sub_id') or '').strip(), _identity_day(row.get('contribution_receipt_date'))
        if sub_id and day and eligible(row, role_of(row)):
            pool[parent_key].append((sub_id, day, _identity_text(row.get('contributor_id')),
                                     name_key(row.get('contributor_name'))))
    parents = {}
    for row, memo_key, day in memos:
        entity, name = _identity_text(row.get('contributor_id')), name_key(row.get('contributor_name'))
        found = [(sub_id, when) for sub_id, when, parent_entity, parent_name in pool.get(memo_key, [])
                 if when <= day and (parent_entity == entity if entity and parent_entity
                                     else bool(name) and parent_name == name)]
        if found:
            parents[str(row.get('sub_id') or '').strip()] = min(
                found, key=lambda item: (-int(item[1].replace('-', '')), item[0]))[0]
    return parents


def source_link(row):
    url = row.get('pdf_url') or ''
    if url.startswith('https://docquery.fec.gov/'):
        return url
    image = str(row.get('image_number') or '')
    return 'https://docquery.fec.gov/cgi-bin/fecimg/?' + image if image.isdigit() else ''


def filing_record(row, *, amount_field, date_field, activity_type):
    """Keep one source transaction attached to its original FEC filing."""
    return {
        'date': str(row.get(date_field) or '')[:10],
        'amount': float(money(row.get(amount_field))),
        'activity_type': activity_type,
        'filing_form': row.get('filing_form') or '',
        'file_number': row.get('file_number') or row.get('file_num') or '',
        'transaction_id': row.get('transaction_id') or '',
        'sub_id': row.get('sub_id') or '',
        'payee': row.get('payee_name') or row.get('recipient_name') or '',
        'purpose': (row.get('expenditure_description') or row.get('expenditure_purpose_descrip')
                    or row.get('disbursement_description') or ''),
        'source_url': source_link(row),
    }


class Client:
    def __init__(self, directory, key=None, transport=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.keys = [key] if key else load_keys()
        if not self.keys:
            raise ValueError('No enabled FEC key is saved.')
        self.transport = transport

    def page(self, endpoint, params):
        url = 'https://api.open.fec.gov/v1/' + endpoint + '?' + urlencode(sorted(params.items()), doseq=True)
        path = self.directory / (hashlib.sha256(url.encode()).hexdigest() + '.json')
        if path.exists():
            saved = json.loads(path.read_bytes())
            if saved.get('url') != url or hashlib.sha256(json.dumps(saved['response'], sort_keys=True).encode()).hexdigest() != saved.get('sha256'):
                raise ValueError(f'FEC evidence cache integrity check failed for {path}.')
            return saved['response']
        for attempt in range(4):
            try:
                if self.transport:
                    response = self.transport(endpoint, params)
                else:
                    key_index = (int(hashlib.sha256(url.encode()).hexdigest()[:8], 16) + attempt) % len(self.keys)
                    request = Request(url, headers={'X-Api-Key': self.keys[key_index], 'User-Agent': 'Mentat PAC evidence audit'})
                    with urlopen(request, timeout=60) as stream:
                        response = json.load(stream)
                break
            except HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise ValueError(f'FEC evidence request failed: HTTP {error.code}; URL {url}') from None
                time.sleep(min(30, 2 ** attempt * 2))
            except URLError as error:
                if attempt == 3:
                    raise ValueError(f'FEC evidence request failed: {error.reason}; URL {url}') from None
                time.sleep(min(30, 2 ** attempt * 2))
            except TimeoutError:
                if attempt == 3:
                    raise ValueError(f'FEC evidence request failed: timed out after 60 s; URL {url}') from None
                time.sleep(min(30, 2 ** attempt * 2))
            except json.JSONDecodeError:
                raise ValueError(f'FEC evidence request failed: response was not JSON; URL {url}') from None
        atomic_json(path, {'url': url, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                          'sha256': hashlib.sha256(json.dumps(response, sort_keys=True).encode()).hexdigest(), 'response': response})
        return response

    def rows(self, endpoint, params, max_pages=1000):
        # Large national PACs can exceed 20,000 Schedule A rows in one cycle.
        # Exact FEC pagination still bounds the work; this ceiling only refuses
        # pathological responses after 100,000 rows instead of truncating a
        # legitimate committee at the former 200-page limit.
        collected, ids, cursors, cursor = [], set(), set(), {}
        expected = None
        for page in range(1, max_pages + 1):
            data = self.page(endpoint, {**params, 'per_page': 100, **cursor})
            pagination = data.get('pagination', {})
            if pagination.get('is_count_exact') is not True or not isinstance(pagination.get('count'), int):
                raise ValueError('PAC evidence requires an exact FEC result count.')
            count = pagination['count']
            if expected is not None and count != expected:
                raise ValueError('FEC result count changed during collection; use a new snapshot directory.')
            expected = count
            batch = data.get('results', [])
            for row in batch:
                identifier = str(row.get('sub_id') or '')
                if not identifier or identifier in ids:
                    raise ValueError('Missing or duplicate FEC transaction identifier during pagination.')
                ids.add(identifier)
                collected.append(row)
            if len(collected) == count:
                return collected
            next_cursor = pagination.get('last_indexes') or {}
            signature = json.dumps(next_cursor, sort_keys=True)
            if not batch or not next_cursor or signature in cursors or len(collected) > count:
                raise ValueError('Incomplete or repeated FEC pagination; evidence not published.')
            cursors.add(signature)
            cursor = next_cursor
        raise ValueError('FEC evidence page limit reached; evidence not published.')


def collect_committee(client, committee, cycle):
    if not re.fullmatch(r'C\d{8}', committee) or cycle % 2:
        raise ValueError('Use FEC committee IDs and even election cycles.')
    base = {'committee_id': committee, 'two_year_transaction_period': cycle}
    receipts = client.rows('schedules/schedule_a/', base)
    disbursements = client.rows('schedules/schedule_b/', base)
    expenditures = client.rows('schedules/schedule_e/', {'committee_id': committee, 'cycle': cycle, 'is_notice': 'false'})
    if any(row.get('is_notice') is not False for row in expenditures):
        raise ValueError('Regular-report IE snapshot unexpectedly contains notice or unknown report types.')
    totals = client.page(f'committee/{committee}/totals/', {'cycle': cycle, 'per_page': 100})['results']
    if len(totals) != 1:
        raise ValueError('Expected one committee financial summary for this cycle.')
    return {'committee_id': committee, 'cycle': cycle, 'receipts': receipts,
            'disbursements': disbursements, 'expenditures': expenditures, 'totals': totals[0]}


def collect_network(client, roots, max_units=30):
    """Collect reachable committee/cycles once; disclose the bounded frontier.

    Candidate-linked Schedule B gifts are terminal activity. The collection
    budget bounds requests, never silently defines the financial network.
    """
    roots = set(roots)
    pending = deque(sorted(roots, key=lambda key: (key[1], key[0])))
    if max_units < len(pending) or max_units < 1:
        raise ValueError('Collection budget must cover every selected root.')
    visited, snapshots, missing = set(), [], []
    attempts = 0
    while pending:
        committee, cycle = pending.popleft()
        if (committee, cycle) in visited:
            continue
        visited.add((committee, cycle))
        if attempts >= max_units:
            missing.append({'committee_id': committee, 'cycle': cycle, 'status': 'collection_limit'})
            continue
        attempts += 1
        try:
            unit = collect_committee(client, committee, cycle)
        except (ValueError, OSError) as error:
            if (committee, cycle) in roots:
                raise
            missing.append({'committee_id':committee, 'cycle':cycle,
                            'status':'collection_failed', 'detail':str(error)})
            continue
        snapshots.append(unit)
        destinations = defaultdict(Decimal)
        # F11: a reload of a transfer is one transfer, not two.
        for row in count_reloads_once(unit['disbursements'], 'disbursements', committee):
            # F8: a committee is followed when transfer_rule calls the row a
            # transfer - by its transaction type, the transfer index's set.
            destination = str(row.get('recipient_committee_id') or '')
            if destination and schedule_b_leg(row)[0] == TRANSFER:
                destinations[destination] += money(row.get('disbursement_amount'))
        pending.extend((destination, cycle) for destination, value in sorted(destinations.items())
                       if value > 0 and (destination, cycle) not in visited)
    return snapshots, missing


def summarize(snapshots, company, aliases):
    """Observed network context. Candidate spend is never a donor allocation."""
    alias_keys = {name_key(value) for value in [company, *aliases] if name_key(value)}
    if not name_key(company):
        raise ValueError('Provide a nonempty canonical company name.')
    receipts, transfers, activity, finances = [], [], [], []
    receipt_rows = []  # the filed rows behind receipts, for F13 below
    # F8: how many rows carried no transaction type, so the old line rule
    # decided them - counted, never silently folded into a total.
    rule = {'transaction_types': sorted(TRANSFER_TYPES), 'rows_without_transaction_type': 0,
            'legs_by_line_fallback': 0, 'amount_by_line_fallback': Decimal(0), 'by_committee': []}
    # F11: a reload - same committee, report type, transaction id, file number,
    # counterparty, amount, date and type under another SUB_ID - counts once.
    reload = {'key': RELOAD_KEY, **new_reload_census(), 'by_committee': []}
    for snapshot in snapshots:
        committee, cycle = snapshot['committee_id'], snapshot['cycle']
        census = new_reload_census()
        schedules = {schedule: list(count_reloads_once(snapshot[schedule], schedule, committee, census))
                     for schedule in ('receipts', 'disbursements', 'expenditures')}
        for name in ('rows_without_transaction_id_or_file_number', 'reload_rows', 'reload_amount'):
            reload[name] += census[name]
        if census['rows_without_transaction_id_or_file_number'] or census['reload_rows']:
            reload['by_committee'].append({'committee_id': committee, 'cycle': cycle,
                                           **census, 'reload_amount': float(census['reload_amount'])})
        total = snapshot['totals']
        actual_ie = sum((money(r.get('expenditure_amount')) for r in schedules['expenditures']
                         if not is_memo(r)), Decimal(0))
        finances.append({'committee_id': committee, 'cycle': cycle,
                         'committee_name': next(((r.get('committee') or {}).get('name') for r in schedules['receipts'] if (r.get('committee') or {}).get('name')), committee),
                         'receipts_as_reported': total.get('receipts'), 'disbursements_as_reported': total.get('disbursements'),
                         'ending_cash_as_reported': total.get('last_cash_on_hand_end_period'),
                         'coverage_start': str(total.get('coverage_start_date') or '')[:10],
                         'coverage_end': str(total.get('coverage_end_date') or '')[:10],
                         'regular_ie_total': float(actual_ie),
                         'regular_ie_summary_total': total.get('independent_expenditures'),
                         'regular_ie_reconciles': (abs(actual_ie - money(total['independent_expenditures'])) <= Decimal('.01'))
                             if total.get('independent_expenditures') not in (None, '') else None,
                         'url': f'https://www.fec.gov/data/committee/{committee}/?cycle={cycle}'})
        for row in schedules['receipts']:
            if name_key(row.get('contributor_name')) not in alias_keys:
                continue
            role = receipt_role(row)
            receipts.append({'company': company, 'reported_name': row.get('contributor_name'),
                             'committee_id': committee, 'cycle': cycle, 'sub_id': row['sub_id'],
                             'date': row.get('contribution_receipt_date'), 'amount': float(money(row.get('contribution_receipt_amount'))),
                             'role': role, 'included_company_contribution': role not in ('other_receipt','asset_liquidation'),
                             'receipt_role_unverified': role not in ('contribution','in_kind_contribution') or is_memo(row),
                             'description': row.get('receipt_type_full'), 'line_number': row.get('line_number'),
                             'transaction_id': row.get('transaction_id'), 'source_url': source_link(row)})
            receipt_rows.append({**row, 'company': company, 'committee_id': committee})
        candidate_payments = defaultdict(list)
        untyped, fallback_legs, fallback_amount = 0, 0, Decimal(0)
        for row in schedules['disbursements']:
            destination = row.get('recipient_committee_id')
            if is_memo(row):
                continue
            # A committee ID can also identify a vendor/refund recipient, so a
            # committee payee alone is never a transfer. transfer_rule decides
            # by the row's transaction type (the transfer index's 24G/24K),
            # and by the old line 22/23 rule only when the row records no type.
            leg, basis = schedule_b_leg(row)
            if not transaction_type(row):
                untyped += 1
            if not leg:
                continue
            if basis == FALLBACK_BASIS:
                fallback_legs += 1
                fallback_amount += money(row.get('disbursement_amount'))
            if destination:
                transfers.append({'cycle': cycle, 'source': committee, 'destination': destination,
                              'candidate_id': row.get('candidate_id') or '',
                              'destination_name': row.get('recipient_name'), 'date': row.get('disbursement_date'),
                              'amount': float(money(row.get('disbursement_amount'))), 'sub_id': row['sub_id'], 'source_url': source_link(row),
                              'transfer_basis': basis})
            if leg == CANDIDATE:
                candidate_payments[(row['candidate_id'],
                                    row.get('candidate_name') or row['candidate_id'])].append(row)
        rule['rows_without_transaction_type'] += untyped
        rule['legs_by_line_fallback'] += fallback_legs
        rule['amount_by_line_fallback'] += fallback_amount
        if untyped:
            rule['by_committee'].append({'committee_id': committee, 'cycle': cycle,
                                         'rows_without_transaction_type': untyped,
                                         'legs_by_line_fallback': fallback_legs,
                                         'amount_by_line_fallback': float(fallback_amount)})
        for (candidate, reported_name), rows in candidate_payments.items():
            dates = sorted(str(row.get('disbursement_date') or '')[:10] for row in rows)
            filings = sorted((filing_record(row, amount_field='disbursement_amount',
                                            date_field='disbursement_date',
                                            activity_type='Candidate contribution / other payment')
                              for row in rows),
                             key=lambda item: (item['date'], item['source_url'], item['sub_id']))
            activity.append({'cycle':cycle, 'committee_id':committee,
                'candidate_id_as_filed':candidate, 'candidate_name_as_filed':reported_name,
                'direction':'Contribution / Other', 'activity_type':'Schedule B candidate payment',
                'committee_spending':float(sum((money(row.get('disbursement_amount'))
                                                for row in rows), Decimal(0))),
                'first_date':dates[0], 'last_date':dates[-1], 'source_rows':len(rows),
                'source_urls':sorted({filing['source_url'] for filing in filings}),
                'filings':filings, 'company_attributed_amount':None,
                'evidence':'Reported committee activity; donor allocation unidentifiable'})
        grouped = defaultdict(list)
        for row in schedules['expenditures']:
            if is_memo(row):
                continue
            grouped[row.get('candidate_id') or '', row.get('candidate_name') or '', row.get('support_oppose_indicator') or ''].append(row)
        for (candidate, reported_name, direction), rows in grouped.items():
            dates = sorted(str(row.get('expenditure_date') or '')[:10] for row in rows)
            filings = sorted((filing_record(row, amount_field='expenditure_amount',
                                            date_field='expenditure_date',
                                            activity_type='Independent expenditure')
                              for row in rows),
                             key=lambda item: (item['date'], item['source_url'], item['sub_id']))
            activity.append({'cycle': cycle, 'committee_id': committee, 'candidate_id_as_filed': candidate,
                             'candidate_name_as_filed': reported_name, 'direction': {'S': 'Support', 'O': 'Oppose'}.get(direction, 'Unresolved'),
                             'committee_spending': float(sum((money(r.get('expenditure_amount')) for r in rows), Decimal(0))),
                             'first_date': dates[0], 'last_date': dates[-1], 'source_rows': len(rows),
                             'source_urls': sorted({source_link(row) for row in rows}),
                             'filings': filings,
                             'company_attributed_amount': None, 'evidence': 'Reported committee activity; donor allocation unidentifiable'})
    # F13 (Paul approved 2026-09-26): a memo whose own text re-discloses a gift
    # already itemized counts once, via the counted parent it restates; without
    # one it stays included and says why (memo_redisclosure_parents).
    restated = memo_redisclosure_parents(receipt_rows)
    for receipt, row in zip(receipts, receipt_rows):
        parent = restated.get(str(row.get('sub_id') or '').strip())
        if parent:
            receipt.update(included_company_contribution=False, duplicate_of_sub_id=parent,
                           duplicate_reason=REDISCLOSURE_REASON)
        elif receipt['role'] == 'unverified_disclosure_memo':
            receipt['receipt_role_reason'] = 'disclosure_memo_unmatched'
    return {'version': 1, 'company': company, 'aliases': sorted(alias_keys),
            'method': 'Observed receipt, transfer and regular-report expenditure legs. Pooled funds do not establish donor-to-candidate attribution. No pro-rata dollar estimate is published.',
            'scope': 'Itemized Schedule A/B and regular Schedule E; 24/48-hour notices are excluded to avoid double counting. Financial summary includes unitemized and other activity.',
            'receipts': receipts, 'transfers': transfers, 'candidate_activity': activity, 'committee_finances': finances,
            'transfer_rule': {**rule, 'amount_by_line_fallback': float(rule['amount_by_line_fallback'])},
            'reload_rule': {**reload, 'reload_amount': float(reload['reload_amount'])}}


def temporal_routes(evidence, max_hops=3):
    """Reachability under dates, not attribution. No backwards-time edges.

    Do not spend an in-kind asset until a documented liquidation link exists.
    Same-day edges have unknown ordering and are explicitly marked as such.
    Negative flows remain in source evidence and never open a funding route.
    """
    results = []
    outbound = defaultdict(list)
    activity = defaultdict(list)
    for transfer in evidence['transfers']:
        if not transfer.get('candidate_id') and transfer.get('date') and money(transfer.get('amount')) > 0:
            outbound[transfer['cycle'], transfer['source']].append(transfer)
    for item in evidence['candidate_activity']:
        if money(item['committee_spending']) > 0:
            activity[item['cycle'], item['committee_id']].append(item)
    for receipt in evidence['receipts']:
        if not receipt['included_company_contribution'] or money(receipt.get('amount')) <= 0:
            continue
        initial = {'receipt_sub_id': receipt['sub_id'], 'company': receipt['company'], 'cycle': receipt['cycle'],
                   'receipt_date': receipt.get('date'), 'receipt_amount': receipt['amount'], 'company_attributed_amount': None}
        if receipt['role'] == 'in_kind_contribution':
            results.append({**initial, 'status': 'asset_liquidation_link_required', 'path': [receipt['committee_id']]})
            continue
        if not receipt.get('date'):
            results.append({**initial, 'status': 'receipt_date_missing', 'path': [receipt['committee_id']]})
            continue
        queue = [(receipt['committee_id'], receipt['date'][:10], [receipt['committee_id']], False)]
        earliest = {}
        emitted = 0
        for committee, available, path, same_day in queue:
            key = (committee, len(path), same_day)
            if earliest.get(key, '9999') <= available:
                continue
            earliest[key] = available
            for item in activity[receipt['cycle'], committee]:
                if not item['last_date'] or item['last_date'] < available:
                    continue
                status = 'dates_allow_possible_route'
                if item['first_date'] < available or same_day or item['first_date'] == available:
                    status = 'date_order_or_partial_period_review'
                results.append({**initial, 'status': status, 'path': path, 'available_no_earlier_than': available,
                                'candidate_id_as_filed': item['candidate_id_as_filed'],
                                'candidate_name_as_filed': item['candidate_name_as_filed'], 'direction': item['direction']})
                emitted += 1
            if len(path) - 1 >= max_hops:
                for transfer in outbound[receipt['cycle'], committee]:
                    if transfer['date'][:10] >= available and transfer['destination'] not in path:
                        results.append({**initial, 'status':'depth_limit',
                            'path':[*path, transfer['destination']], 'max_hops':max_hops})
                        emitted += 1
                continue
            for transfer in outbound[receipt['cycle'], committee]:
                day = transfer['date'][:10]
                if day < available or transfer['destination'] in path:
                    continue
                queue.append((transfer['destination'], day, [*path, transfer['destination']], same_day or day == available))
        if not emitted:
            results.append({**initial, 'status': 'no_dated_route_in_observed_scope', 'path': [receipt['committee_id']]})
    return results


def reconcile_candidates(client, evidence):
    ids = sorted({row['candidate_id_as_filed'] for row in evidence['candidate_activity']
                  if re.fullmatch('[HSP][A-Z0-9]{8}', row['candidate_id_as_filed'])})
    def lookup(identifier):
        rows = client.page(f'candidate/{identifier}/', {'per_page': 100}).get('results', [])
        return identifier, rows[0].get('name', '') if len(rows) == 1 else ''
    with ThreadPoolExecutor(max_workers=3) as executor:
        catalog = dict(executor.map(lookup, ids))
    for row in evidence['candidate_activity']:
        official = catalog.get(row['candidate_id_as_filed'], '')
        row['registered_candidate_name'] = official
        row['identity_status'] = ('name_matches_registration' if official and name_key(official) == name_key(row['candidate_name_as_filed'])
                                  else 'name_id_review' if official else 'candidate_registration_unresolved')
        row['candidate_registration_url'] = 'https://www.fec.gov/data/candidate/' + row['candidate_id_as_filed'] + '/'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--committees', required=True, help='Comma-separated root FEC IDs; recipient PACs are followed within the collection budget')
    parser.add_argument('--max-units', type=int, default=30, help='Maximum committee/cycle snapshots; any uncollected frontier is disclosed')
    parser.add_argument('--cycles', default='2024')
    parser.add_argument('--company', required=True)
    parser.add_argument('--aliases', default='', help='Pipe-separated explicit aliases; transaction roles remain separate')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    committees = list(dict.fromkeys(args.committees.split(',')))
    cycles = list(dict.fromkeys(int(value) for value in args.cycles.split(',')))
    args.output.mkdir(parents=True, exist_ok=True)
    client = Client(args.output / 'source-cache')
    work = [(committee, cycle) for committee in committees for cycle in cycles]
    snapshots, missing = collect_network(client, work, args.max_units)
    result = summarize(snapshots, args.company, args.aliases.split('|'))
    reconcile_candidates(client, result)
    result['temporal_routes'] = temporal_routes(result)
    result['collection_roots'] = [{'committee_id': committee, 'cycle': cycle} for committee, cycle in work]
    result['downstream_coverage'] = missing
    result['unexpanded_committees'] = sorted({row['committee_id'] for row in missing})
    result['retrieved_at'] = datetime.now(timezone.utc).isoformat()
    atomic_json(args.output / 'snapshots.json', snapshots)
    result['source_snapshot_sha256'] = hashlib.sha256((args.output / 'snapshots.json').read_bytes()).hexdigest()
    atomic_json(args.output / 'evidence.json', result)
    from pac_cash_model import write_estimates
    write_estimates(args.output)
    print(json.dumps({'committees': committees, 'cycles': cycles, 'receipt_legs': len(result['receipts']),
                      'transfer_legs': len(result['transfers']), 'candidate_activity_rows': len(result['candidate_activity']),
                      'all_regular_ie_totals_reconcile': all(r['regular_ie_reconciles'] for r in result['committee_finances'])}))


if __name__ == '__main__':
    main()
