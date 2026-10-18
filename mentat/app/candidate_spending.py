"""How a candidate's campaign spent its money: FEC Schedule B disbursements, read from the crawler's reader.

A separate, read-only path. It touches no receipt-counting, contribution or flow-amount code: the Sankey's
dollars are the PAC's, these are the campaign's own. GET /api/candidate-spending (server.py) answers from the
source adapter below; there is no network in the request path and nothing here collects or writes anything.

The source (the ONE place that knows where Schedule B comes from): mentat/tools/committee_activity_reader.py,
read_campaign_spending(*, cycle, candidate_id=None, committee_id=None, state_dir=None), the crawler lane's typed
reader of its accepted snapshots. It is imported lazily; where it is not installed the answer is "Spending source
not installed". Tests and screenshots set MENTAT_CANDIDATE_SPENDING_FIXTURE (a directory of results in the reader's
exact shape, <candidate or committee id>-<cycle>.json, or one such file); production never sets it.

What is taken from the reader's result (each rule is tested in tests/test_candidate_spending.py):
  * Rows are de-duplicated by sub_id before anything else (the reader can repeat one); the count dropped is reported.
  * coverage_status: 'complete' needs every one of the candidate's committees complete AND its totals retrieved.
    'partial', 'pending', 'not_seeded' and 'mapping_unknown' never imply zero: pending and not_seeded say
    "Spending not collected", an unmapped candidate "Campaign committee not identified", a partial one draws what
    it has under a chip that names the reason.
  * A candidate may have several principal committees in a cycle: their rows are combined, and a payment from one to
    another (recipient_committee_id among the candidate's own committee ids) is dropped so it is counted once; that
    amount is reported as own_transfers.
  * Totals arrive as a list of raw /committee/<id>/totals objects: the one whose cycle is the asked cycle (the latest
    coverage_end_date if several) is used, and only three of its fields: disbursements, loan_repayments and
    transfers_to_other_authorized_committee. reported = sum over the committees of
    disbursements - loan_repayments - transfers_to_other_authorized_committee, and UNKNOWN (None) unless every one of
    those fields is present and numeric in every committee's object: a missing field is never read as $0 (the crawler's
    totals shape is unpinned; its 'complete' example has no loan_repayments, so its coverage is unknown).
  * Presidential committees (P...) file Form 3P, whose totals field names are not pinned: their coverage is always
    unknown ("Coverage not computed for presidential committees") and the donut is drawn from the itemized rows, with
    Form 3P lines 24 (transfers) and 27 (loan repayments) left out as F3 lines 18 and 19 are, and 28 read as refunds.
  * Totals count non-memo rows only (memo_code != 'X'), which is how FEC counts them.
  * A parent ("SEE MEMOS": American Express, Payroll Solutions) with memo children is classified from the
    children only when they sum to the parent within $1.00; otherwise the parent stays in Other as
    "Card/payroll payments (memos not itemized)". A parent and its children are never both counted.
  * Negative rows (a VOID, a reissue) net inside their category. A category whose net is <= 0 is dropped from
    the donut and listed in dropped_negative.
  * coverage = itemized / reported, both without loan repayments (F3 lines 19A/19B) and transfers to authorized
    committees (F3 line 18). Never scaled up to 100% and never clamped to it: above 1.0 is a scope bug and is shown
    as it is, with a warning. Floored (not rounded) to four decimals, so 0.89996 can never read as 90%. Unknown (None)
    when the reported total is.
  * complete also needs every committee the mapping lists to have been returned ("1 of 2 campaign committees not
    collected" is partial) and a totals object for each.
  * A payee is the candidate in person only when entity_type is IND, the surname is the same and the first given name is
    the same (the filed name's own nickname counts: BARR, GARLAND ANDY is "Andy" and "Garland"), organisation-looking
    names never are, AND the payment's purpose is a reimbursement kind (reimbursement, mileage, travel, child care, per
    diem). A namesake staffer on SALARY is therefore payroll, not a candidate reimbursement. A namesake paid a
    reimbursement-kind purpose cannot be told apart from the candidate and is read as the candidate.
"""
import hashlib
import json
import logging
import os
import re
import statistics
import threading
from collections import Counter, OrderedDict, defaultdict
from decimal import Decimal
from pathlib import Path

LOG = logging.getLogger('mentat.candidate_spending')
READER = 'committee_activity_reader'
FIXTURE_ENV = 'MENTAT_CANDIDATE_SPENDING_FIXTURE'
CANDIDATE_ID = re.compile(r'[HSP][0-9A-Z]{8}')
COMMITTEE_ID = re.compile(r'C\d{8}')
PAYMENT_LIST = 100          # payments sent per category on a detail read
PEERS_MIN = 5               # fewer collected peer campaigns than this and no comparison is drawn
PEERS_CAP = 40              # most peer candidates read for one comparison
MEMO_TOLERANCE = 100        # cents: children must sum to their parent within $1.00
UNITEMIZED_PARENT = 'Card/payroll payments (memos not itemized)'
TOTALS_FIELDS = ('disbursements', 'loan_repayments', 'transfers_to_other_authorized_committee')
PRESIDENTIAL_NOTE = 'Coverage not computed for presidential committees'
SHARES_CAP = 256            # peer category shares kept in memory (least recently used go first)
# Schedule B line numbers by form: transfers to other authorized committees, loan repayments, refunds. Form 3 (House,
# Senate) 18 / 19A-B / 20A-C; Form 3P (presidential) 24 / 27A-B / 28A-C.
LINES = {'F3': {'transfers': '18', 'loans': '19', 'refunds': '20'}, 'P': {'transfers': '24', 'loans': '27', 'refunds': '28'}}

CATEGORIES = [
    ('advertising', 'Advertising & media'), ('consulting', 'Consulting'), ('legal_compliance', 'Legal & compliance'),
    ('polling_data', 'Polling & data'), ('payroll', 'Payroll & staff'), ('fundraising_fees', 'Fundraising & processing fees'),
    ('events', 'Events & meetings'), ('travel', 'Travel'), ('printing_mail', 'Printing & mail'),
    ('office', 'Office & admin'), ('contributions_transfers', 'Contributions & transfers'),
    ('refunds', 'Refunds to donors'), ('personal_reimb', 'Candidate reimbursements'), ('other', 'Other')]
LABELS = dict(CATEGORIES)
OFFICES = {'H': 'House', 'S': 'Senate', 'P': 'Presidential'}


# ------------------------------------------------------------------------------------------------ classifier
def _rx(pattern):
    return re.compile(pattern, re.I)


# After the two signals that need no words (the candidate reimbursed, a refund line) the order is the contract:
# the first rule that matches the description wins. COMPLIANCE CONSULTING is legal & compliance, not consulting;
# a card or processing FEE is a fee before any vendor word (PAYMENT FOR LEGAL FEES is not one); sponsorships to party
# committees are transfers before "event"; FOOD, BEVERAGE & EVENT SPACE is an event, OFFICE SPACE is not.
# Phrases that name their category beat a loose word that reads as another one: DATA PLAN - CELL PHONE is office, not
# data; VAN RENTAL is travel, not the voter file (a bare VAN is NGP VAN); PRINTING AND DESIGN is printing, not design.
# STAFF is a weak payroll word (STAFF TRAVEL REIMBURSEMENT, MEALS - STAFF, EVENT STAFFING are travel and events): it is
# read only after travel and events. WEBSITE DESIGN is advertising (digital); WEBSITE HOSTING and DOMAIN are office.
FEES = _rx(r'(?:\b(?:CC|CREDIT CARD|CARD|MERCHANT|PROCESSING|TRANSACTION|PLATFORM|WINRED|ACTBLUE)\b[^,;/]*\b(?:FEES?|CHARGES?|COMMISSIONS?)\b'
           r'|\bPAYMENTS? (?:(?:PROCESSING|PROCESSOR|PLATFORM|TRANSACTION) )?(?:FEES?|CHARGES?)\b'
           r'|\b(?:FEES?|CHARGES?)\b[^,;/]*\b(?:WINRED|ACTBLUE)\b|\b(?:LIST RENTAL|LIST ACQUISITION|FUNDRAISING LIST)\b|^(?:WINRED|ACTBLUE)\b)')
OFFICE_FIRST = _rx(r'\b(?:DATA (?:PLAN|USAGE)|CELL(?:ULAR)?(?: PHONES?| SERVICE| PLAN)?|MOBILE (?:PHONES?|SERVICE|PLAN)|WIRELESS|PHONE (?:SERVICE|PLAN|BILL)'
                   r'|CABLE\s*[/&+-]?\s*INTERNET|(?:INTERNET|BROADBAND|FIBER) (?:SERVICE|ACCESS|CONNECTION)|(?:GOOGLE |G ?)(?:WORKSPACE|SUITE)|(?:MICROSOFT|OFFICE) 365)\b')
TRAVEL_FIRST = _rx(r'\b(?:VAN|MINIVAN|BUS|TRUCK|RV|SUV|CAR|VEHICLE)\s+(?:RENTAL|HIRE|LEASE|CHARTER)\b'
                   r'|\b(?:RENTAL|LEASE|HIRE)\s+(?:OF\s+)?(?:A\s+)?(?:VAN|MINIVAN|BUS|TRUCK|RV|SUV|CAR|VEHICLE)\b')
PRINT_FIRST = _rx(r'\bPRINT(?:ING|ED|S|ERS?)?\b(?!\s+(?:ADS?|ADVERTISING|MEDIA|BUY)\b)')
LEGAL = _rx(r'\b(?:COMPLIANCE|LEGAL|ATTORNEYS?|LAWYERS?|LAW (?:FIRM|OFFICES?)|COUNSEL|ACCOUNT(?:ING|ANT|ANTS)|BOOKKEEP\w*|AUDIT\w*|FEC (?:REPORT|FILING)\w*|TREASURER|TAX (?:PREP\w*|RETURNS?))\b')
POLLING = _rx(r'\b(?:POLL(?:ING|S)?|SURVEYS?|FOCUS GROUPS?|DATABASES?|DATA\b|VOTER (?:FILE|DATA|LIST|CONTACT|REGISTRATION)|VOTER[- ]FILE|ANALYTICS|VAN\b|RESEARCH)')
ADS = _rx(r'\b(?:ADVERTIS\w*|ADS?|MEDIA(?: BUY| PLACEMENT| PRODUCTION| SERVICES)?|TV|TELEVISION|RADIO|CABLE|BROADCAST|STREAMING|DIGITAL (?:ADS?|ADVERTISING|MEDIA|MARKETING)|ONLINE (?:ADS?|ADVERTISING)|FACEBOOK|META|GOOGLE|TWITTER|YOUTUBE|SOCIAL MEDIA|SIGNS?|YARD SIGNS?|BANNERS?|BILLBOARDS?|VIDEO|PHOTOGRAPH\w*|CREATIVE|GRAPHIC\w*|DESIGN\w*|PRODUCTION|PROMOTION\w*|PAID MEDIA)\b')
PAYROLL = _rx(r'\b(?:PAYROLL|SALAR(?:Y|IES)|WAGES?|STIPENDS?|BONUS(?:ES)?|COMPENSATION|HEALTH (?:INSURANCE|BENEFITS?|CARE)|401\(?K\)?|PAYCHECKS?|W-?2|PTO|UNEMPLOYMENT|WORKERS COMP\w*)\b')
PAYROLL_WEAK = _rx(r'\b(?:STAFF(?:ING)?|EMPLOYEES?|EMPLOYMENT|BENEFITS?)\b')
CONSULT = _rx(r'\b(?:CONSULT\w*|STRATEG\w*|ADVIS\w*|RETAINERS?|MANAGEMENT (?:FEES?|SERVICES)|CAMPAIGN MANAGE\w*|PROFESSIONAL SERVICES|FUNDRAISING (?:SERVICES?|COMMISSIONS?|EXPENSES?)|COMMISSIONS?|FIELD (?:SERVICES?|PROGRAM)|CANVASS\w*|PRESS|PUBLIC RELATIONS|PR)\b')
EVENT = _rx(r'\b(?:EVENTS?|CATER\w*|VENUES?|FOOD|BEVERAGES?|MEALS?|BREAKFAST|LUNCH|DINNERS?|RECEPTIONS?|BANQUETS?|MEETINGS?|FUNDRAISERS?|CONFERENCES?|CONVENTIONS?|RALL(?:Y|IES)|(?:EVENT|MEETING) SECURITY|FLORAL|FLOWERS?|DECORATIONS?|CATERING|HALL RENTAL|ROOM RENTAL|TICKETS?|PARTY SUPPLIES|SPEAKERS?|ENTERTAINMENT|BARBECUE|BBQ|PICNIC|GALA)\b')
TRAVEL = _rx(r'\b(?:TRAVEL\w*|AIR(?:FARE|LINES?|PORT)?|FLIGHTS?|HOTELS?|LODGING|MOTEL|MILEAGE|UBER|LYFT|TAXI|CAB|PARKING|CAR RENTAL|RENTAL CAR|TRANSPORTATION|GASOLINE|FUEL|TOLLS?|AMTRAK|TRAIN|BUS|CHARTER|AIRBNB|PER DIEM|SUBSISTENCE|LIMO\w*)\b')
PRINT = _rx(r'\b(?:PRINT\w*|POSTAGE|DIRECT MAIL|MAIL(?:ING|ER|ERS|HOUSE)?|USPS|COPY|COPIES|COPYING|LITERATURE|BROCHURES?|FLYERS?|PALM CARDS?|DOOR HANGERS?|STICKERS?|BUTTONS?|T-?SHIRTS?|APPAREL|MERCHANDISE|MATERIALS?|CAMPAIGN MATERIALS|LEAFLETS?|POSTCARDS?|STATIONERY|ENVELOPES?)\b(?<!E-MAIL)(?<!EMAIL)')
OFFICE = _rx(r'\b(?:RENT|LEASE|STORAGE|PHONES?|TELEPHONE|CELL|MOBILE|INTERNET|WIFI|INSURANCE|SOFTWARE|SUBSCRIPTIONS?|SUPPLIES|OFFICE|UTILIT\w*|BANK(?:ING)? (?:FEES?|CHARGES?|SERVICE\w*)|SERVICE (?:FEES?|CHARGES?)|WEB(?:SITE)?|HOSTING|DOMAIN|EQUIPMENT|COMPUTERS?|IT SERVICES|FURNITURE|COURIER|SHIPPING|DELIVERY|MEMBERSHIP|DUES|LICENSES?|PERMITS?|CLEANING|JANITORIAL|SECURITY SYSTEM|SERVER|CLOUD|EMAIL|E-MAIL|TEXT(?:ING)? (?:SERVICES?|MESSAGING)|SMS|OPERATING|ADMINISTRAT\w*|OVERHEAD)\b')
TRANSFER_WORDS = _rx(r'\b(?:CONTRIBUTIONS?|DONATIONS?|TRANSFERS?|ENDORSEMENT)\b')
SPONSOR = _rx(r'\b(?:SPONSOR\w*|TICKETS?|TABLE|DINNER|GALA|EVENT|DUES|MEMBERSHIP|FILING FEE|ASSESSMENT)\b')
REFUND = _rx(r'\bREFUNDS?\b')
# A payee that is a political committee by its name alone (a party, a "... FOR CONGRESS"), whatever the filing calls it.
POLITICAL = _rx(r'\b(?:PARTY|REPUBLICAN|DEMOCRAT\w*|GOP|CAUCUS|NRCC|NRSC|DCCC|DSCC|RNC|DNC|PAC|FOR (?:CONGRESS|SENATE|PRESIDENT|U\.?S\.?)|(?:FRIENDS|CITIZENS|COMMITTEE|VOTERS) (?:OF|FOR|TO))\b')
# Words a vendor can carry too (CAMPAIGN PROMOTIONS INC, VICTORY ENTERPRISES): a committee only when the filing says so.
POLITICAL_LOOSE = _rx(r'\b(?:VICTORY|COMMITTEE|CAMPAIGN)\b')
COMMITTEE_TYPES = {'COM', 'PAC', 'PTY', 'CCM'}          # FEC entity_type codes of a political committee (ORG is a vendor)
ORG_WORDS = _rx(r'\b(?:THE|OF|FOR|INC|LLC|LLP|LTD|CORP|CORPORATION|CO|COMPANY|GRILL|BAR|RESTAURANT|CAFE|PUB|TAVERN|HOTEL|AIRLINES|FRIENDS|COMMITTEE|CAMPAIGN|PAC|FUND|CONGRESS|SENATE|VICTORY|PARTY|SERVICES|GROUP|ASSOCIATES|PARTNERS|STRATEGIES|CONSULTING|MEDIA|PRINTING|BANK)\b')
NAME_SUFFIXES = {'JR', 'SR', 'II', 'III', 'IV', 'REP', 'HON', 'MR', 'MRS', 'MS', 'DR'}
REIMBURSED = _rx(r'\b(?:REIMB\w*|MILEAGE|TRAVEL\w*|CHILD ?CARE|PER DIEM)\b')
VOID = _rx(r'\b(?:VOID(?:ED)?|VOIDS)\b')
SEE_MEMOS = _rx(r'\bMEMOS?\b')       # a parent that says its payees are in its memo lines

# FEC's own purpose category, used only when no word in the description decided it.
FALLBACK = {'ADMINISTRATIVE': 'office', 'ADVERTISING': 'advertising', 'CONTRIBUTIONS': 'contributions_transfers',
            'POLITICAL CONTRIBUTIONS': 'contributions_transfers', 'TRANSFERS': 'contributions_transfers',
            'EVENTS': 'events', 'FUNDRAISING': 'fundraising_fees', 'MATERIALS': 'printing_mail',
            'REFUNDS': 'refunds', 'TRAVEL': 'travel', 'POLLING': 'polling_data', 'COMMUNICATIONS': 'advertising'}

# (key, pattern) in the order the description is read; the signals in classify() come around them.
DESCRIPTION_RULES = [('fundraising_fees', FEES), ('legal_compliance', LEGAL), ('office', OFFICE_FIRST), ('travel', TRAVEL_FIRST),
                     ('polling_data', POLLING), ('payroll', PAYROLL), ('printing_mail', PRINT_FIRST), ('advertising', ADS),
                     ('consulting', CONSULT), ('events', EVENT), ('travel', TRAVEL), ('payroll', PAYROLL_WEAK),
                     ('printing_mail', PRINT), ('office', OFFICE)]


def line_code(row):
    """The schedule line as FEC prints it: 'SB17' -> '17', '20A'. Blank means unknown, never a guess."""
    return re.sub(r'[^0-9A-Z]', '', re.sub(r'^SB', '', str(row.get('line_number') or '').strip().upper()))


def cents_or_none(value):
    """Dollars -> whole cents, or None when there is no number (missing, blank, not numeric). Totals use this: a
    missing field must stay unknown, never become $0."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int((Decimal(str(value)) * 100).to_integral_value())
    except Exception:
        return None


def cents(value):
    found = cents_or_none(value)
    return 0 if found is None else found


def _tokens(text):
    return re.findall(r'[A-Z0-9]+', str(text or '').upper())


def is_memo(row):
    return str(row.get('memo_code') or '').strip().upper() == 'X'


def _clean(text):
    return re.sub(r'^\s*(?:IN[- ]?KIND\s*[:\-]\s*)', '', str(text or ''), flags=re.I).strip()


def _name_parts(text):
    """A filed name -> (surname tokens, given-name tokens): 'BARR, GARLAND ANDY JR' and 'ANDY BARR' alike. Suffix words
    (JR, SR, II, REP, HON, ...) and single-letter initials are dropped, so they can neither make nor break a match."""
    text = str(text or '')
    if ',' in text:
        before, after = text.split(',', 1)
        surname, given = _tokens(before), _tokens(after)
    else:
        words = [token for token in _tokens(text) if token not in NAME_SUFFIXES]
        surname, given = words[-1:], words[:-1]
    keep = lambda tokens: [token for token in tokens if token not in NAME_SUFFIXES and len(token) > 1]
    return keep(surname), keep(given)


def is_candidate(row, candidate):
    """The payee is, by name, the candidate in person. Strict: entity_type IND; no organisation-looking word in the name
    (INC, LLC, GRILL, FRIENDS OF, COMMITTEE, ...); the same surname; and a first given name that is the candidate's own
    or the nickname the candidate's name as filed carries (BARR, GARLAND ANDY is Garland and Andy; 'BARR, ANDY JR' is the
    same person). The given names may differ only by one side having more of them. A namesake is still a match by name:
    classify() adds the purpose test."""
    if not candidate or str(row.get('entity_type') or '').strip().upper() != 'IND':
        return False
    name = str(row.get('recipient_name') or '')
    if ORG_WORDS.search(name):
        return False
    mine_last, mine_given = _name_parts(candidate)
    last, given = _name_parts(name)
    if not (mine_last and mine_given and last and given):
        return False
    if last[-1] != mine_last[-1] or not (set(last) <= set(mine_last) or set(mine_last) <= set(last)):
        return False
    if given[0] not in mine_given and mine_given[0] not in given:
        return False
    return set(given) <= set(mine_given) or set(mine_given) <= set(given)


def _committee_like(row, payee):
    """A payee that is a committee by its name (a party, FOR CONGRESS) or whose filing says it is (COM, PAC, PTY, CCM).
    CAMPAIGN, COMMITTEE and VICTORY alone are not enough: CAMPAIGN PROMOTIONS INC is a vendor."""
    if POLITICAL.search(payee):
        return True
    return bool(POLITICAL_LOOSE.search(payee)) and str(row.get('entity_type') or '').strip().upper() in COMMITTEE_TYPES


def classify(row, candidate=None, lines=None):
    """One Schedule B row -> a category key. Signals first, then the ordered description words, then FEC's
    purpose category. Deterministic; no row is dropped (an unmatched one is 'other'). `lines`: LINES['F3'] (default)
    or LINES['P'], the form's transfer / refund line numbers."""
    lines = lines or LINES['F3']
    description = _clean(row.get('disbursement_description'))
    description = _clean(VOID.sub(' ', description))
    purpose = str(row.get('disbursement_purpose_category') or '').strip().upper()
    line = line_code(row)
    payee = str(row.get('recipient_name') or '')
    # The candidate is only a reimbursement when paid for one: SMITH, JOHN on SALARY (a namesake staffer) is payroll.
    if is_candidate(row, candidate) and REIMBURSED.search(f'{description} {purpose}'):
        return 'personal_reimb'
    if line.startswith(lines['refunds']) or REFUND.search(description) or purpose == 'REFUNDS':
        return 'refunds'
    if FEES.search(description):
        return 'fundraising_fees'
    if LEGAL.search(description):
        return 'legal_compliance'
    # Another committee on the receiving end: a transfer or contribution, whatever the memo calls it.
    if row.get('recipient_committee_id') or line.startswith(lines['transfers']):
        return 'contributions_transfers'
    if _committee_like(row, payee) and (SPONSOR.search(description) or TRANSFER_WORDS.search(description)):
        return 'contributions_transfers'
    if TRANSFER_WORDS.search(description) and not any(pattern.search(description) for key, pattern in DESCRIPTION_RULES if key != 'office'):
        return 'contributions_transfers'
    for key, pattern in DESCRIPTION_RULES:
        if pattern.search(description):
            return key
    return FALLBACK.get(purpose, 'other')


# ------------------------------------------------------------------------------------------------ the money
def _is_void_text(row):
    return bool(VOID.search(str(row.get('disbursement_description') or '')) or VOID.search(str(row.get('memo_text') or '')))


def _filing(row):
    """Which filing a row is in, as far as the row says: its file number, else its report (year and type), else unknown ('').
    image_number is a PAGE of the filing, so a memo child can be on a later page than its parent: it cannot key the pair."""
    for name in ('file_number', 'filing_id'):
        if str(row.get(name) or '').strip():
            return str(row[name]).strip()
    year, kind = str(row.get('report_year') or '').strip(), str(row.get('report_type') or '').strip()
    return f'{year}|{kind}' if year or kind else ''


def _memo_key(row, transaction_id):
    """A memo child names its parent by transaction id, which only means something inside one committee's one filing."""
    return (str(row.get('committee_id') or ''), _filing(row), str(transaction_id or ''))


def _units(rows):
    """Rows -> (counted units, memo accounting). A unit is {row, cents, category_of}: a non-memo row, or the
    memo children standing in for a 'SEE MEMOS' parent whose children sum to it within $1.00."""
    children = defaultdict(list)
    for row in rows:
        if is_memo(row) and row.get('back_reference_transaction_id'):
            children[_memo_key(row, row['back_reference_transaction_id'])].append(row)
    accounting = {'parents': 0, 'resolved': 0, 'unresolved': 0, 'children_used': 0, 'unresolved_amount': 0.0,
                  'memo_rows': 0, 'memo_rows_not_counted': 0}
    units = []
    for row in rows:
        if is_memo(row):
            continue
        amount = cents(row.get('disbursement_amount'))
        kids = children.get(_memo_key(row, row.get('transaction_id')), []) if SEE_MEMOS.search(str(row.get('disbursement_description') or '')) else []
        if kids:
            accounting['parents'] += 1
            total = sum(cents(kid.get('disbursement_amount')) for kid in kids)
            if abs(total - amount) <= MEMO_TOLERANCE:
                accounting['resolved'] += 1
                accounting['children_used'] += len(kids)
                # the (at most $1.00) difference goes to the largest child, so categories add up to the parent exactly
                first = max(range(len(kids)), key=lambda at: abs(cents(kids[at].get('disbursement_amount'))))
                for at, kid in enumerate(kids):
                    share = cents(kid.get('disbursement_amount')) + (amount - total if at == first else 0)
                    units.append({'row': kid, 'cents': share, 'parent': row})
                continue
            accounting['unresolved'] += 1
            accounting['unresolved_amount'] += amount / 100
            units.append({'row': row, 'cents': amount, 'unresolved': True})
            continue
        units.append({'row': row, 'cents': amount})
    accounting['memo_rows'] = sum(1 for row in rows if is_memo(row))
    accounting['memo_rows_not_counted'] = accounting['memo_rows'] - accounting['children_used']
    accounting['unresolved_amount'] = round(accounting['unresolved_amount'], 2)
    return units, accounting


def _categorise(units, candidate, lines=None):
    """Each unit's category. A negative row that says VOID (or reissues) nets in the category of the positive row
    it undoes (same payee, same amount) when it can be found, else in its own words' category."""
    positives = defaultdict(list)
    for unit in units:
        unit['key'] = ('other' if unit.get('unresolved') else classify(unit['row'], candidate, lines))
        if unit['cents'] > 0:
            positives[(str(unit['row'].get('recipient_name') or '').upper().strip(), unit['cents'])].append(unit)
    for unit in units:
        if unit['cents'] < 0 and not unit.get('unresolved'):
            match = positives.get((str(unit['row'].get('recipient_name') or '').upper().strip(), -unit['cents']))
            if match and (_is_void_text(unit['row']) or unit['key'] == 'other'):
                unit['key'] = match[0]['key']
    return units


def _payee(unit):
    return str(unit['row'].get('recipient_name') or '').strip() or '(no payee filed)'


def _link(row):
    url = str(row.get('pdf_url') or '').strip()
    if url:
        return url
    image = str(row.get('image_number') or '').strip()
    return f'https://docquery.fec.gov/cgi-bin/fecimg/?{image}' if re.fullmatch(r'\d{6,}', image) else ''


def _dollars(value):
    return round(value / 100, 2)


# ------------------------------------------------------------------------------------------------ the source
def read_source(*, cycle, candidate_id=None, committee_id=None):
    """The reader's answer for one candidate or committee and cycle, in its exact shape, or
    {'status': 'source_unavailable'} where the reader is not installed. The ONLY function that reaches the source."""
    fixture = os.environ.get(FIXTURE_ENV, '').strip()
    if fixture:
        return _fixture(Path(fixture), cycle, candidate_id, committee_id)
    try:
        import committee_activity_reader
    except ModuleNotFoundError as error:
        if error.name != READER:
            raise
        return {'status': 'source_unavailable'}
    return committee_activity_reader.read_campaign_spending(cycle=cycle, candidate_id=candidate_id, committee_id=committee_id)


def _fixture(path, cycle, candidate_id, committee_id):
    """Test/screenshot stand-in for the reader. A directory holds one result per <id>-<cycle>.json; a file is either one
    result or the crawler's fixture file ({'examples': [{'call', 'result'}]}) and answers only the calls it carries.
    What it does not hold is not seeded: no committees."""
    empty = {'query': {'cycle': cycle, 'candidate_id': candidate_id, 'committee_id': committee_id}, 'snapshot': None,
             'mapping': None, 'committees': []}
    target = path if path.is_file() else path / f'{candidate_id or committee_id}-{int(cycle)}.json'
    try:
        value = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return empty
    wanted = (candidate_id or committee_id, int(cycle))
    results = [item.get('result') or {} for item in value.get('examples') or []] if 'examples' in value else [value]
    for item in results:
        asked = item.get('query') or {}
        if not path.is_file() or ((asked.get('candidate_id') or asked.get('committee_id')), int(asked.get('cycle') or 0)) == wanted:
            return item
    return empty


def cycle_text(cycle):
    return f'{int(cycle) - 1}–{str(cycle)[2:]}'


def _totals_of(committee, cycle):
    """The /committee/<id>/totals object for the cycle (the latest coverage_end_date if several), or None."""
    found = [item for item in committee.get('totals') or [] if isinstance(item, dict) and str(item.get('cycle')) == str(cycle)
             and item.get('committee_id') in (None, committee.get('committee_id')) and item.get('disbursements') is not None]
    return max(found, key=lambda item: str(item.get('coverage_end_date') or '')) if found else None


def combine(result, cycle):
    """The reader's result -> one campaign: {'status': 'ok', rows, totals, ...} or {'status': <why not>, 'message'}.
    Rows are de-duplicated, a candidate's several committees are combined, and transfers between them are dropped."""
    if not isinstance(result, dict) or result.get('status') == 'source_unavailable':
        return {'status': 'source_unavailable', 'message': 'Spending source not installed'}
    committees = [item for item in result.get('committees') or [] if isinstance(item, dict)]
    usable = [item for item in committees if item.get('coverage_status') in ('complete', 'partial')]
    mapping = result.get('mapping') if isinstance(result.get('mapping'), dict) else None
    ids = list(dict.fromkeys((mapping or {}).get('committee_ids') or [item.get('committee_id') for item in committees]))
    ids = [value for value in ids if value]
    if not usable:
        if not committees or all(item.get('coverage_status') == 'mapping_unknown' for item in committees):
            return {'status': 'mapping_unknown', 'message': f'Campaign committee not identified for {cycle_text(cycle)}'}
        # the ids and per-committee statuses go along so the panel can offer the bulk file's rows (bulk_oppexp.section)
        return {'status': 'not_collected', 'message': f'Spending not collected for {cycle_text(cycle)}', 'committee_ids': ids,
                'committees': [{'committee_id': item.get('committee_id'), 'endpoint_status': item.get('endpoint_status') or {}}
                               for item in committees]}
    seen, rows, duplicates, occurrences = set(), [], 0, 0
    for committee in usable:
        for row in committee.get('rows') or []:
            occurrences += 1
            key = str(row.get('sub_id') or '')
            if key and key in seen:
                duplicates += 1
                continue
            seen.add(key)
            rows.append(row)
    mine = set(ids)
    own = [row for row in rows if row.get('recipient_committee_id') in mine]
    rows = [row for row in rows if row.get('recipient_committee_id') not in mine]
    picked = [_totals_of(item, cycle) for item in committees]
    known = all(item is not None for item in picked)
    # One sum per field, in cents; a field one committee does not carry (or carries as a non-number) is None for all:
    # a missing field is unknown, never $0.
    totals = None
    if known:
        totals = {}
        for name in TOTALS_FIELDS:
            values = [cents_or_none(item.get(name)) for item in picked]
            totals[name] = None if any(value is None for value in values) else sum(values)
    lacking = [name for name in TOTALS_FIELDS if totals is not None and totals[name] is None]
    returned = {item.get('committee_id') for item in committees}
    absent = [value for value in ids if value not in returned]
    statuses = [item.get('coverage_status') for item in committees]
    complete = all(status == 'complete' for status in statuses) and known and not absent
    reasons = []
    for item in committees:
        if item.get('coverage_status') != 'complete':
            said = item.get('endpoint_status') or {}
            reasons.append(f"{item.get('committee_name') or item.get('committee_id')}: {item.get('coverage_status')}"
                           f" (Schedule B {said.get('B', 'unknown')}, totals {said.get('totals', 'unknown')})")
    if absent:
        reasons.append(f'{len(absent)} of {len(ids)} campaign committees not collected')
    if not known and not reasons:
        reasons.append('Totals were not retrieved, so what was reported is unknown')
    if lacking:
        reasons.append(f"Totals lack {', '.join(lacking)}, so what was reported is unknown")
    when = [(item.get('retrieved_at') or {}).get('B_latest') for item in usable]
    who = str((mapping or {}).get('candidate_id') or (result.get('query') or {}).get('candidate_id') or '').strip().upper()
    return {'status': 'ok', 'coverage_status': 'complete' if complete else 'partial', 'reasons': reasons, 'rows': rows,
            'office': who[:1] if who[:1] in OFFICES else '',
            'totals': totals, 'committee_ids': ids or [item.get('committee_id') for item in usable],
            'committees': [{'committee_id': item.get('committee_id'), 'committee_name': item.get('committee_name') or '',
                            'coverage_status': item.get('coverage_status'), 'endpoint_status': item.get('endpoint_status') or {},
                            'retrieved_at': item.get('retrieved_at') or {}} for item in committees],
            'committee_name': ' + '.join(dict.fromkeys(item.get('committee_name') or item.get('committee_id') or '' for item in usable)),
            'cycle': int(cycle), 'retrieved_at': min((value for value in when if value), default=None), 'mapping': mapping,
            'snapshot': result.get('snapshot'), 'row_occurrences': occurrences, 'duplicates_dropped': duplicates,
            'disclosure_boundaries': sum(int(item.get('disclosure_boundaries') or 0) for item in usable),
            'own_transfers': {'amount': _dollars(sum(cents(row.get('disbursement_amount')) for row in own if not is_memo(row))),
                              'rows': sum(1 for row in own if not is_memo(row))}}


def load_campaign_spending(identifier, cycle):
    """A candidate id (H0KY06104) or a committee id (C00467571) and an even cycle -> the campaign, from the source.
    See combine() for what comes back; status says why when there is nothing to draw."""
    key = 'committee_id' if COMMITTEE_ID.fullmatch(str(identifier)) else 'candidate_id'
    made = combine(read_source(cycle=int(cycle), **{key: identifier}), cycle)
    if made.get('status') == 'ok' and not made.get('office') and key == 'candidate_id':
        made['office'] = str(identifier)[:1].upper() if str(identifier)[:1].upper() in OFFICES else ''
    return made


def candidate_hint(names):
    """'ANDY BARR FOR CONGRESS, INC.' -> 'ANDY BARR': the candidate's own name as far as the committee's gives it."""
    return re.split(r'\b(?:FOR|VICTORY|COMMITTEE|INC|LLC|CONGRESS|SENATE|PRESIDENT|PAC|FUND)\b',
                    str(names or '').split(' + ')[0].upper())[0].strip(' ,.')


# ------------------------------------------------------------------------------------------------ the breakdown
def breakdown(campaign, hint=None, detail=False):
    """A campaign (combine()) -> the breakdown the card and panel draw. `hint` is the candidate's name for spotting
    reimbursements to them ('BARR, GARLAND ANDY'); without it the committee's name stands in. See the module docstring."""
    hint = hint or candidate_hint(campaign.get('committee_name'))
    units, memo = _units([row for row in campaign['rows'] if isinstance(row, dict)])
    presidential = campaign.get('office') == 'P'
    lines = LINES['P' if presidential else 'F3']
    # Form 3P totals use other field names that are not pinned: no reported total is worked out for a presidential campaign.
    totals = None if presidential else campaign.get('totals')
    # loan repayments and transfers to authorized committees are not how a campaign spent money on its race:
    # out of both sides of coverage (F3 lines 19A/19B and 18, F3P 27 and 24; the totals separate both).
    loans = [u for u in units if line_code(u['row']).startswith(lines['loans'])]
    moved = [u for u in units if line_code(u['row']).startswith(lines['transfers'])]
    taken = {id(u) for u in loans + moved}
    kept = _categorise([u for u in units if id(u) not in taken], hint, lines)
    by_key = defaultdict(list)
    for unit in kept:
        by_key[unit['key']].append(unit)
    itemized = sum(u['cents'] for u in kept)
    # combine() holds the totals in cents; a field it could not read is None, and then so is the reported total
    reported = (None if totals is None or any(totals.get(name) is None for name in TOTALS_FIELDS)
                else totals['disbursements'] - totals['loan_repayments'] - totals['transfers_to_other_authorized_committee'])
    dollars_of = lambda name: None if totals is None or totals.get(name) is None else _dollars(totals[name])
    reasons = list(campaign['reasons']) + ([PRESIDENTIAL_NOTE] if presidential else [])
    categories, dropped = [], []
    for key, label in CATEGORIES:
        group = by_key.get(key, [])
        if not group:
            continue
        net = sum(u['cents'] for u in group)
        item = {'key': key, 'label': label, 'amount': _dollars(net), 'payments': sum(1 for u in group if u['cents'] > 0)}
        (categories if net > 0 else dropped).append(item)
    positive = sum(cents(item['amount']) for item in categories)
    for item in categories:
        item['share'] = round(cents(item['amount']) / positive, 6) if positive else 0.0
    categories.sort(key=lambda item: -item['amount'])
    unmatched, counts = Counter(), Counter()
    for unit in by_key.get('other', []):
        words = UNITEMIZED_PARENT if unit.get('unresolved') else (str(unit['row'].get('disbursement_description') or '').strip() or '(no description)')
        unmatched[words] += unit['cents']
        counts[words] += 1
    other = next((item for item in categories if item['key'] == 'other'), None)
    # Floored to four decimals in whole cents (0.89996 stays 0.8999, never 0.9) and NOT clamped: above 1.0 is a scope bug
    # (more itemized than the committee reported) that the page must show as it is.
    coverage = (itemized * 10000 // reported) / 10000 if reported and reported > 0 else None
    body = {
        'status': 'ok', 'coverage_status': campaign['coverage_status'], 'coverage_reasons': reasons,
        'committee_id': campaign['committee_ids'][0] if campaign['committee_ids'] else None,
        'committee_ids': campaign['committee_ids'], 'committee_name': campaign['committee_name'],
        'committees': campaign['committees'], 'cycle': campaign['cycle'], 'retrieved_at': campaign['retrieved_at'],
        'total_itemized': _dollars(itemized), 'reported_total': None if reported is None else _dollars(reported),
        'coverage': coverage, 'coverage_unknown': coverage is None,
        'positive_total': _dollars(positive), 'categories': categories, 'dropped_negative': dropped,
        'other_top_descriptions': [{'description': words, 'amount': _dollars(amount), 'payments': counts[words]}
                                   for words, amount in sorted(unmatched.items(), key=lambda pair: -pair[1])[:10]],
        'other_share': round(other['share'], 4) if other else 0.0,
        'memo_handling': memo,
        'own_transfers': campaign['own_transfers'],
        'excluded': {'loan_repayments': {'itemized': _dollars(sum(u['cents'] for u in loans)),
                                         'reported': dollars_of('loan_repayments')},
                     'transfers_to_authorized': {'itemized': _dollars(sum(u['cents'] for u in moved)),
                                                 'reported': dollars_of('transfers_to_other_authorized_committee')}},
        'source': {'schedule': 'FEC Schedule B (itemized disbursements)', 'rows': len(campaign['rows']),
                   'row_occurrences': campaign['row_occurrences'], 'duplicate_rows_dropped': campaign['duplicates_dropped'],
                   'non_memo_rows': sum(1 for row in campaign['rows'] if not is_memo(row)),
                   'disclosure_boundaries': campaign['disclosure_boundaries'],
                   'totals_fields': 'sum of disbursements - loan_repayments - transfers_to_other_authorized_committee, one totals object per committee for the cycle',
                   'mapping': campaign['mapping'], 'snapshot': campaign['snapshot']}}
    if detail:
        body['payees'], body['payments'] = _detail(by_key)
    return body


def _detail(by_key):
    payees, payments = {}, {}
    for key, group in by_key.items():
        pay = defaultdict(lambda: {'amount': 0, 'payments': 0, 'names': Counter()})
        for unit in group:
            slot = pay[_payee(unit).upper()]
            slot['amount'] += unit['cents']
            slot['payments'] += 1 if unit['cents'] > 0 else 0
            slot['names'][_payee(unit)] += 1
        ranked = sorted(pay.values(), key=lambda slot: -slot['amount'])
        payees[key] = [{'payee': slot['names'].most_common(1)[0][0], 'amount': _dollars(slot['amount']), 'payments': slot['payments']}
                       for slot in ranked if slot['amount'] > 0][:5]
        listed = sorted(group, key=lambda unit: str(unit['row'].get('disbursement_date') or ''), reverse=True)
        payments[key] = {'total': len(listed), 'rows': [
            {'date': str(unit['row'].get('disbursement_date') or '')[:10], 'payee': _payee(unit),
             'purpose': (UNITEMIZED_PARENT + ': ' if unit.get('unresolved') else '')
             + str(unit['row'].get('disbursement_description') or unit['row'].get('memo_text') or ''),
             'amount': _dollars(unit['cents']), 'url': _link(unit['row'])} for unit in listed[:PAYMENT_LIST]]}
    return payees, payments


# ------------------------------------------------------------------------------------------------ peers
_SHARES = OrderedDict()     # (candidate, cycle, snapshot sha256 or input digest) -> category shares: classified once per snapshot
_SHARES_LOCK = threading.Lock()


def _fingerprint(campaign):
    """What identifies the input a peer's shares were worked from: the reader's snapshot sha256, or, when the reader gave
    none, a digest of the rows and totals themselves."""
    sha = (campaign.get('snapshot') or {}).get('sha256')
    if sha:
        return f'sha256:{sha}'
    blob = json.dumps([campaign.get('rows'), campaign.get('totals'), campaign.get('office'), campaign.get('committee_ids')],
                      sort_keys=True, default=str)
    return 'input:' + hashlib.sha256(blob.encode('utf-8')).hexdigest()


def _peer_shares(candidate, cycle, campaign):
    key = (candidate, int(cycle), _fingerprint(campaign), tuple(campaign['committee_ids']))
    with _SHARES_LOCK:
        if key in _SHARES:
            _SHARES.move_to_end(key)
            return _SHARES[key]
    shares = {item['key']: item['share'] for item in breakdown(campaign)['categories']}
    with _SHARES_LOCK:
        _SHARES[key] = shares
        _SHARES.move_to_end(key)
        while len(_SHARES) > SHARES_CAP:
            _SHARES.popitem(last=False)
    return shares


def peers(cycle, office, own_ids, candidates):
    """Median category shares of the campaigns the source has complete for the cycle and office (House, Senate,
    Presidential), among `candidates` (the candidate ids the chart draws: the reader answers one campaign at a time and
    has no list). Nothing is fetched. Fewer than PEERS_MIN: no comparison (too_few)."""
    found = {}
    for candidate in list(dict.fromkeys(candidates))[:PEERS_CAP]:
        if candidate[:1] != office:
            continue
        try:
            campaign = load_campaign_spending(candidate, cycle)
        except Exception:               # a peer the source cannot read is left out of the median, not a failed answer
            LOG.warning('candidate-spending: peer %s %s could not be read and is left out', candidate, cycle, exc_info=True)
            continue
        if campaign['status'] != 'ok' or campaign['coverage_status'] != 'complete' or set(campaign['committee_ids']) & set(own_ids):
            continue
        shares = _peer_shares(candidate, cycle, campaign)
        if shares:
            found[candidate] = shares
    result = {'n': len(found), 'office': office, 'cycle': int(cycle), 'min': PEERS_MIN,
              'label': f"Median of {len(found)} collected {OFFICES.get(office, 'candidate')} campaigns, {cycle_text(cycle)}"}
    if len(found) < PEERS_MIN:
        return {**result, 'too_few': True, 'note': 'Too few collected campaigns to compare'}
    median = {key: statistics.median(share.get(key, 0.0) for share in found.values()) for key, _ in CATEGORIES}
    total = sum(median.values())
    if total <= 0:          # no category is spent in by most of them: there is nothing to draw, never an empty bar
        return {**result, 'too_few': True, 'note': 'The collected campaigns share no spending category to compare'}
    return {**result, 'too_few': False, 'shares': {key: round(value / total, 6) for key, value in median.items() if value > 0}}


# ------------------------------------------------------------------------------------------------ the route
def parse_candidate(text):
    """'H0KY06104' or a flow node key 'candidate:H0KY06104:support' -> the candidate id, else ''."""
    for part in str(text or '').strip().split(':'):
        if CANDIDATE_ID.fullmatch(part.strip().upper()):
            return part.strip().upper()
    return ''


def respond(query):
    """GET /api/candidate-spending?candidate=<id or node key>&cycle=YYYY[&name=<LAST, FIRST>][&detail=1&peers=<ids>]
    -> (body, HTTP status). Served from the source only. Nothing to draw is 404 (not_collected, mapping_unknown) or
    503 (source_unavailable) with a JSON body, never a made-up zero. A reader that raises is 503 source_error with its
    message (and a log line), never a dropped connection."""
    first = lambda name: (query.get(name) or [''])[0]
    candidate = parse_candidate(first('candidate'))
    if not candidate:
        raise ValueError('Choose a candidate by FEC candidate ID.')
    try:
        cycle = int(first('cycle'))
    except ValueError:
        raise ValueError('Choose an election cycle (an even year).') from None
    if cycle % 2 or not 1980 <= cycle <= 2100:
        raise ValueError('Choose an election cycle (an even year).')
    detail = first('detail') in ('1', 'true')
    try:
        campaign = load_campaign_spending(candidate, cycle)
    except Exception as error:
        LOG.exception('candidate-spending: the spending source failed for %s %s', candidate, cycle)
        return ({'status': 'source_error', 'error': f'The spending source could not be read: {error or type(error).__name__}',
                 'candidate_id': candidate, 'cycle': cycle}, 503)
    # The panel (detail) also carries the FEC bulk file's operating expenditures for committees whose API Schedule B is not
    # complete: a separate, labeled section (bulk_oppexp.py), never added to the API figures; absent once the API is complete.
    bulk = None
    if detail and campaign.get('committee_ids'):
        import bulk_oppexp
        bulk = bulk_oppexp.section(campaign.get('committees') or [], campaign['committee_ids'], cycle)
    if campaign['status'] != 'ok':
        return ({'status': campaign['status'], 'error': campaign['message'], 'candidate_id': candidate, 'cycle': cycle,
                 **({'bulk': bulk} if bulk else {})}, 503 if campaign['status'] == 'source_unavailable' else 404)
    body = breakdown(campaign, first('name').strip()[:80] or None, detail=detail)
    body.update(candidate_id=candidate, office=candidate[:1])
    if bulk:
        body['bulk'] = bulk
    if detail:
        others = [parse_candidate(item) for item in first('peers').split(',') if item.strip()]
        body['peers'] = peers(cycle, candidate[:1], campaign['committee_ids'], [item for item in others if item and item != candidate])
    return body, 200
