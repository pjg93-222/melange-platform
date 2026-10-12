"""What one saved Schedule B row IS: a transfer to another committee, a payment
to a candidate, or neither. One rule, shared by every reader of the rows.

Paul, 2026-09-25 (F8): "one rule, by transaction type, everywhere". The transfer
index (fec_transfer_graph.py, built from the FEC's bulk OTH file) counts an
outgoing record as a committee-to-committee transfer when its transaction type
is in CASH_CODES ({'24G', '24K'}: transfer to an affiliated committee,
contribution to a nonaffiliated committee), the record is not a memo, and it
names the committee that received it. This module applies that same set -
imported from the index, not copied - to the openFEC Schedule B rows the PAC
evidence collector saves, where the transaction type is the `disbursement_type`
field. The collector (tools/pac_evidence.py), the downstream network
(app/downstream.py) and the map (app/evidence_geo.py) all classify through
schedule_b_leg(), so none of them can count a row the others do not.

The line a row was filed on no longer decides. A 24K filed on line 21B is a
transfer; a line-23 row whose type is not 24G or 24K (an in-kind 24Z, say) is
not one.

openFEC fills `disbursement_type` on committee-to-committee rows and leaves it
null on most operating rows. A row with no transaction type is decided by the
rule used before 2026-09-25 - a non-memo line 22 or 23 payment naming a
recipient committee - and its basis says 'line_fallback', so a caller can flag
the row and count how many rows each snapshot decided that way. Falling back
keeps such a row exactly where it was; it never changes a total by itself.

A row that names a candidate stays terminal candidate activity, as before: it
is a candidate payment when it is a transfer by this rule (a 24K to the
candidate's committee, on any line) or when it was filed on line 22 or 23 (the
old rule, which also admits 24C coordinated and 24Z in-kind gifts). This
module decides no geography and no dollars; it only names the leg.

F11, the reload rule (Paul, 2026-09-25), lives here too, so the collector, the
downstream measurement and the map count the same rows: a row counts once as
a reload ONLY when it shares committee | report type | transaction id | file
number | counterparty | amount | date | type with an earlier row and differs
from it in SUB_ID. Genuine repeats - distinct transaction ids - stay counted,
and a row with no transaction id or no file number is never treated as one
(callers count those rows instead). Lane A applies the same key to the R PAS2
path and the transfer index.
"""
from decimal import Decimal, InvalidOperation
import re

from fec_transfer_graph import CASH_CODES

#: The index's own committee-to-committee transfer types, never a second copy.
TRANSFER_TYPES = frozenset(CASH_CODES)
#: The Schedule B lines the pre-2026-09-25 rule read; used only when a row
#: carries no transaction type.
FALLBACK_LINES = frozenset({'22', '23'})

TYPE_BASIS = 'transaction_type'
FALLBACK_BASIS = 'line_fallback'
MEMO_BASIS = 'memo'

TRANSFER = 'transfer'
CANDIDATE = 'candidate'


def is_memo(row):
    """A memo line restates money counted elsewhere; it is never a leg.

    The union of the collector's and the downstream reader's tests, which
    differ only in `is_memo_leg` (the collector's extra field).
    """
    if str(row.get('memo_code') or '').strip().upper() == 'X':
        return True
    return any(str(row.get(key) or '').strip().upper() in ('TRUE', '1')
               for key in ('memoed_subtotal', 'is_memo_leg'))


def transaction_type(row):
    """The row's FEC transaction type ('24K'), or '' when it records none."""
    return str(row.get('disbursement_type') or '').strip().upper()


def line_code(row):
    """'21B', '22', '23' - the Schedule B line, punctuation removed."""
    return re.sub(r'[^0-9A-Z]', '', str(row.get('line_number') or '').strip().upper())


def is_transfer(row):
    """(True/False, basis): does this row move money to another committee?

    basis is 'transaction_type' when the row's own type decided it,
    'line_fallback' when the row carries no type and the old line rule decided,
    and 'memo' for a memo line, which is never a transfer.
    """
    if is_memo(row):
        return False, MEMO_BASIS
    names_committee = bool(str(row.get('recipient_committee_id') or '').strip())
    code = transaction_type(row)
    if code:
        return names_committee and code in TRANSFER_TYPES, TYPE_BASIS
    return names_committee and line_code(row) in FALLBACK_LINES, FALLBACK_BASIS


def schedule_b_leg(row):
    """(leg, basis) for one Schedule B row.

    leg is 'transfer' (money to another committee, followed downstream),
    'candidate' (a payment to a named candidate, terminal) or '' (neither:
    operating, refunds, loans, and every memo line). basis is is_transfer's.
    """
    transfer, basis = is_transfer(row)
    if basis == MEMO_BASIS:
        return '', basis
    if str(row.get('candidate_id') or '').strip():
        return (CANDIDATE if transfer or line_code(row) in FALLBACK_LINES else ''), basis
    return (TRANSFER if transfer else ''), basis


# ------------------------------------------------------------ F11: reloads

#: What each saved openFEC schedule calls the key's counterparty (its id, then
#: its name - both, so two rows naming different parties never merge), amount,
#: date and transaction type. Schedule E carries no type field; its support or
#: opposition indicator is the FEC's 24E / 24A.
RELOAD_FIELDS = {
    'receipts': (('contributor_id', 'contributor_name'), 'contribution_receipt_amount',
                 'contribution_receipt_date', 'receipt_type'),
    'disbursements': (('recipient_committee_id', 'recipient_name'), 'disbursement_amount',
                      'disbursement_date', 'disbursement_type'),
    'expenditures': (('candidate_id', 'payee_name'), 'expenditure_amount', 'expenditure_date', None),
}
SUPPORT_OPPOSE_TYPES = {'S': '24E', 'O': '24A'}
RELOAD_KEY = 'committee|report type|transaction id|file number|counterparty|amount|date|type'


def _text(value):
    return ' '.join(re.findall(r'[A-Z0-9]+', str(value or '').upper()))


def reload_key(row, schedule, committee=''):
    """committee | report type | transaction id | file number | counterparty |
    amount | date | type for one saved row, or None when the row carries no
    transaction id or no file number (the reload rule is then not applied).

    `committee` is the filer, used when the row does not name its own.
    """
    transaction = str(row.get('transaction_id') or '').strip()
    file_number = str(row.get('file_number') or row.get('file_num') or '').strip()
    if not transaction or not file_number:
        return None
    parties, amount_field, date_field, type_field = RELOAD_FIELDS[schedule]
    try:
        amount = str(Decimal(str(row.get(amount_field))).quantize(Decimal('0.01')))
    except (InvalidOperation, ValueError, TypeError):
        amount = str(row.get(amount_field))
    code = (str(row.get(type_field) or '').strip().upper() if type_field else
            SUPPORT_OPPOSE_TYPES.get(str(row.get('support_oppose_indicator') or '').strip().upper(), ''))
    return (str(row.get('committee_id') or committee or '').strip().upper(),
            str(row.get('report_type') or '').strip().upper(), transaction, file_number,
            '|'.join(_text(row.get(field)) for field in parties), amount,
            str(row.get(date_field) or '')[:10], code)


def new_reload_census():
    return {'rows_without_transaction_id_or_file_number': 0, 'reload_rows': 0,
            'reload_amount': Decimal(0)}


def count_reloads_once(rows, schedule, committee='', census=None):
    """Each row once: a later row whose reload_key an earlier row already holds
    under a different SUB_ID is a reload and is skipped. Rows without a key, and
    a repeat of the same SUB_ID (which is not a reload), always pass.

    `census` (from new_reload_census) counts the rows the rule could not be
    applied to, and the non-memo reload rows and dollars it kept from counting
    twice.
    """
    kept = {}
    amount_field = RELOAD_FIELDS[schedule][1]
    for row in rows:
        key = reload_key(row, schedule, committee)
        if key is None:
            if census is not None:
                census['rows_without_transaction_id_or_file_number'] += 1
            yield row
            continue
        sub = str(row.get('sub_id') or '').strip()
        if kept.setdefault(key, sub) != sub:
            if census is not None and not is_memo(row):
                census['reload_rows'] += 1
                try:
                    census['reload_amount'] += Decimal(str(row.get(amount_field)))
                except (InvalidOperation, ValueError, TypeError):
                    pass
            continue
        yield row
