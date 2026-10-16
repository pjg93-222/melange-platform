"""Roster companies' PACs' OWN giving to House and Senate candidates, from the FEC committee-to-candidate bulk files (pas2).

Port of the phase-2 design's `pod/pac_to_candidate.py` (validated: its all-recipient totals equal the 05 v3 study receipt exactly for
every industry x cycle x channel; banks 4,910,350 / 5,692,500 / 4,926,500, credit unions 36,500 / 80,800 / 65,500), generalised to any
roster and to month-end cumulative cuts of the open cycle.

Rule (owner ruling 2026-09-30, brain D-049): count_latest filing-version resolution (d049.py); counted rows are the included 24K
(contribution) and 24Z (in-kind) transactions, signed so refunds net out; 24A / 24E independent expenditures are excluded. Chamber is
the CAND_ID prefix (H, S; P presidential; anything else, including blank, is "no candidate"). Party comes from the FEC candidate master
(DEM / DFL -> D, REP -> R, everything else "other"). Read-only: the bulk files are only opened for reading.
"""
from collections import defaultdict
from datetime import date
import calendar

import d049

COLS = ['CMTE_ID', 'AMNDT_IND', 'RPT_TP', 'TRANSACTION_PGI', 'IMAGE_NUM', 'TRANSACTION_TP', 'ENTITY_TP', 'NAME', 'CITY',
        'STATE', 'ZIP_CODE', 'EMPLOYER', 'OCCUPATION', 'TRANSACTION_DT', 'TRANSACTION_AMT', 'OTHER_ID', 'CAND_ID',
        'TRAN_ID', 'FILE_NUM', 'MEMO_CD', 'MEMO_TEXT', 'SUB_ID']
COUNTED_TYPES = ('24K', '24Z')
RULE_TEXT = ('FEC committee-to-candidate (pas2) rows, D-049 count_latest filing-version resolution; counted = included 24K + 24Z signed '
             '(direct + in-kind, refunds netted), 24A/24E independent expenditures excluded; chamber from the CAND_ID prefix, party from the '
             'FEC candidate master (DEM/DFL = D, REP = R)')


def iso(mdy):
    """FEC TRANSACTION_DT is MMDDYYYY; anything else is an unknown date ('')."""
    if len(mdy) != 8 or not mdy.isdigit():
        return ''
    try:
        return date(int(mdy[4:8]), int(mdy[0:2]), int(mdy[2:4])).isoformat()
    except ValueError:
        return ''


def month_ends(first_year, last_date):
    """Month-end ISO dates from January of `first_year` through `last_date` (an ISO month-end), inclusive."""
    out = []
    last = date.fromisoformat(last_date)
    year, month = first_year, 1
    while (year, month) <= (last.year, last.month):
        out.append(date(year, month, calendar.monthrange(year, month)[1]).isoformat())
        month, year = (1, year + 1) if month == 12 else (month + 1, year)
    return out


def load_parties(cn_files):
    """{(candidate id, 'yy' or '*'): party} from candidate masters. `cn_files` is an ordered list of (yy, path), latest master first, so
    the '*' entry is the latest master's party (the order of the validated implementation)."""
    out = {}
    for yy, path in cn_files:
        with open(path, encoding='latin-1') as stream:
            for line in stream:
                c = line.rstrip('\n').split('|')
                if len(c) > 5:
                    out.setdefault((c[0], yy), c[2])
                    out.setdefault((c[0], '*'), c[2])
    return out


def party_letter(code):
    return 'D' if code in ('DEM', 'DFL') else 'R' if code == 'REP' else 'other'


def blank_block():
    return {'House': defaultdict(int), 'Senate': defaultdict(int), 'presidential': 0, 'no_candidate': 0, 'all_recipients': 0}


def aggregate_file(path, yy, parties, membership, cuts_by_industry=None):
    """One pas2 file, every roster at once. `membership` = {committee id: [industry id, ...]};
    `cuts_by_industry` = {industry id: [ISO month-end dates]} (cumulative cuts of this file's cycle, per industry's coverage end).

    Returns {industry: {'totals': block, 'cuts': {cut date: block}, 'max_date': str, 'lines': n, 'kept_rows': n, 'statuses': {...}}} where a
    block is {'House': {'D','R','other'}, 'Senate': {...}, 'presidential', 'no_candidate', 'all_recipients'} in integer CENTS."""
    rows, lines = [], 0
    with open(path, encoding='latin-1') as stream:
        for index, line in enumerate(stream):
            lines += 1
            c = line.rstrip('\n').split('|')
            if len(c) < 22 or c[0] not in membership:
                continue
            d = dict(zip(COLS, c))
            try:
                file_num = int(d['FILE_NUM'])
            except ValueError:
                file_num = None
            if not d['TRANSACTION_AMT'].strip():
                raise ValueError('FEC pas2 row ' + str(index + 1) + ' has a missing TRANSACTION_AMT; refusing to infer zero.')
            rows.append(dict(rid=index, cmte_id=d['CMTE_ID'], rpt_tp=d['RPT_TP'], tran_id=d['TRAN_ID'], sub_id=d['SUB_ID'], file_num=file_num,
                             amndt_ind=d['AMNDT_IND'], cand_id=d['CAND_ID'], other_id=d['OTHER_ID'],
                             amount_cents=int(round(float(d['TRANSACTION_AMT'] or 0) * 100)), date=iso(d['TRANSACTION_DT']),
                             transaction_tp=d['TRANSACTION_TP'], memo_cd=d['MEMO_CD'], order=index))
    resolved = d049.resolve(rows)
    cuts_by_industry = cuts_by_industry or {}
    out = {}
    for row in rows:
        verdict = resolved[row['rid']]
        if not verdict['include'] or row['transaction_tp'] not in COUNTED_TYPES:
            continue
        office = row['cand_id'][:1] if row['cand_id'] else ''
        party = party_letter(parties.get((row['cand_id'], yy), parties.get((row['cand_id'], '*'), '')))
        for industry in membership[row['cmte_id']]:
            block = out.setdefault(industry, {'totals': blank_block(), 'cuts': {cut: blank_block() for cut in cuts_by_industry.get(industry, ())}, 'max_date': '', 'lines': lines,
                                              'kept_rows': len(rows)})
            # A row with no usable date is part of the cycle total but of no observed cut: its day is unknown, never "before every cut".
            targets = [block['totals']] + [b for cut, b in block['cuts'].items() if row['date'] and row['date'] <= cut]
            if not row['date']:
                block['undated_cents'] = block.get('undated_cents', 0) + row['amount_cents']
            for target in targets:
                target['all_recipients'] += row['amount_cents']
                if office in ('H', 'S'):
                    target['House' if office == 'H' else 'Senate'][party] += row['amount_cents']
                else:
                    target['presidential' if office == 'P' else 'no_candidate'] += row['amount_cents']
            if row['date'] > block['max_date']:
                block['max_date'] = row['date']
    return out


def dollars(block):
    """A cents block as dollars in the snapshot's shape."""
    def chamber(part):
        return {'D': part['D'] / 100, 'R': part['R'] / 100, 'other': part['other'] / 100}
    house, senate = chamber(block['House']), chamber(block['Senate'])
    to_hs = sum(house.values()) + sum(senate.values())
    return {'House': house, 'Senate': senate, 'to_house_senate': round(to_hs, 2), 'presidential': block['presidential'] / 100,
            'no_candidate': block['no_candidate'] / 100, 'all_recipients': block['all_recipients'] / 100}
