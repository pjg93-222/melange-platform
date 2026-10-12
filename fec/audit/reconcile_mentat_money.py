"""Gate 1 (decision B MW-6): does Mentat's direct-PAC channel measure the same dollars as Melange's FEC leg?

Read-only. Writes only audits/mentat_money_reconcile_<YYYYMMDD>/ (report.md + report.json).

Intersection: the 44 FEC-leg committees whose company Mentat also covers (in an admitted
tidy book). Mentat side: that company's Corporate-tier, included contributions (Analysis
Amount) to a principal/authorized committee of a roster member. FEC side: the same
committees' Schedule B 24K/24Z disbursements, memos excluded, through the FEC leg's own
`aggregate` rule: 2025-26 from the applied OpenFEC generation (fec_refresh_current.json,
hash-verified), 2023-24 from the bulk itpas2 file (closed cycle).

Bars (fixed before looking; decision B MW-6):
- closed 2023-24: |delta| <= $1 for >= 99% of members with nonzero money on either source,
  and the aggregate within 1%.
- current 2025-26: both sources pinned to transactions dated <= the earlier source's data
  date; aggregate within 1%; every member with |delta| > $500 listed and classed (timing,
  refund_or_redesignation, recipient_misfiling, coverage). An unexplained residual fails.
- the coverage gap (44-committees Mentat does not cover) is reported, not gated.
"""
from pathlib import Path
import sys, os
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse, json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from paths import ROOT
from collect.refresh_fec_money import committee_policy, candidate_map, aggregate, digest
from collect import import_mentat_money as IM

CLASSES = ('timing', 'refund_or_redesignation', 'recipient_misfiling', 'coverage')
EXACT, AGG_REL, FLOOR, SHARE = 1.0, 0.01, 500.0, 0.99


def gate_closed(fec, mentat):
    bios = sorted(b for b in set(fec) | set(mentat) if fec.get(b, 0) or mentat.get(b, 0))
    exact = [b for b in bios if abs(fec.get(b, 0) - mentat.get(b, 0)) <= EXACT]
    af, am = sum(fec.values()), sum(mentat.values())
    rel = abs(af - am) / af if af else (0.0 if not am else float('inf'))
    share = len(exact) / len(bios) if bios else 1.0
    return {'members': len(bios), 'exact': len(exact), 'share_exact': round(share, 4), 'agg_fec': round(af, 2),
            'agg_mentat': round(am, 2), 'agg_rel': round(rel, 6), 'pass': bool(bios) and share >= SHARE and rel <= AGG_REL,
            'misses': {b: round(mentat.get(b, 0) - fec.get(b, 0), 2) for b in bios if b not in exact}}


def gate_current(fec, mentat, classes):
    af, am = sum(fec.values()), sum(mentat.values())
    rel = abs(af - am) / af if af else (0.0 if not am else float('inf'))
    big = {b: round(mentat.get(b, 0) - fec.get(b, 0), 2) for b in sorted(set(fec) | set(mentat))
           if abs(mentat.get(b, 0) - fec.get(b, 0)) > FLOOR}
    cls = {b: (classes.get(b) or ('unexplained', ''))[0] for b in big}
    unexplained = sorted(b for b in big if cls[b] not in CLASSES)
    return {'agg_fec': round(af, 2), 'agg_mentat': round(am, 2), 'agg_rel': round(rel, 6),
            'over_floor': {b: {'delta': d, 'class': cls[b], 'evidence': (classes.get(b) or ('', ''))[1]} for b, d in big.items()},
            'unexplained': unexplained, 'pass': (bool(af) or bool(am)) and rel <= AGG_REL and not unexplained}


def pair(left, right, days=90):
    """Remove equal-amount pairs dated within `days`; return the unpaired remainders."""
    left, right = sorted(left, key=lambda x: x[:2]), sorted(right, key=lambda x: x[:2])
    for t in list(left):
        hit = next((u for u in right if u[1] == t[1] and abs((u[0] - t[0]).days) <= days), None)
        if hit:
            left.remove(t); right.remove(hit)
    return left, right


def classify(fec_tx, mentat_tx, pin, unlinked=(), window=60):
    """Class one member's gap from its transactions [(date, amount)]; returns (class, evidence).
    recipient_misfiling: the FEC check's Mentat receipt exists but its committee has no ccl candidate link.
    timing: every unpaired check sits within `window` days of the pin. A check Mentat never stored is
    'mentat_receipt_missing', which is NOT an allowed class: it fails the gate (decision B MW-6)."""
    f, m = pair(fec_tx, mentat_tx)   # FEC tuples carry the recipient committee id third; pairing uses (date, amount)
    if not f and not m:
        return 'timing', 'same checks; dates straddle the pin'
    if any(t[1] < 0 for t in f + m):
        return 'refund_or_redesignation', 'negative amount present'
    f2 = [t for t in f if not any(u[1] == t[1] and len(t) > 2 and u[2] == t[2] and abs((u[0] - t[0]).days) <= 90 for u in unlinked)]
    if not f2 and not m:
        return 'recipient_misfiling', 'Mentat holds the receipt at a committee with no ccl candidate link'
    if all((pin - t[0]).days <= window for t in f2 + m):
        return 'timing', 'within %d days of the pin' % window
    if not f2:
        return 'mentat_only_receipts', 'Mentat receipts with no FEC check: %s' % [(str(t[0]), t[1]) for t in m]
    return 'mentat_receipt_missing', 'FEC checks with no Mentat receipt: %s' % [(str(t[0]), t[1], t[2] if len(t) > 2 else None) for t in f2]


def fec_pages(root):
    cur = json.loads((Path(root) / 'data/money/fec_refresh_current.json').read_text(encoding='utf-8'))
    rows = []
    for s in cur['sources']:
        if digest(s['path']) != s['sha256']:
            raise ValueError('FEC evidence hash mismatch: %s' % s['path'])
        if '/schedules/schedule_b/' in s['url']:   # the rest are candidates/totals pages
            rows += json.loads(Path(s['path']).read_text(encoding='utf-8')).get('results', [])
    return cur, rows


def fec_bulk(path, committees, cycle_end):
    rows = []
    with open(path, encoding='latin-1') as f:
        for line in f:
            p = line.rstrip('\n').split('|')
            if p[0] in committees:
                d = p[13]
                rows.append({'sub_id': p[21], 'committee_id': p[0], 'two_year_transaction_period': cycle_end,
                             'disbursement_type': p[5], 'candidate_id': p[16] or None, 'memo_code': p[19],
                             'memoed_subtotal': False, 'disbursement_amount': p[14], 'recipient_committee_id': p[15] or None,
                             'disbursement_date': '%s-%s-%s' % (d[4:8], d[0:2], d[2:4]) if len(d) == 8 else ''})
    return rows


def per_member(rows, cands, committees, cycle_end, pin=None):
    keep = [r for r in rows if r['committee_id'] in committees and (pin is None or str(r.get('disbursement_date') or '')[:10] <= str(pin))]
    totals, rejected, latest = aggregate(keep, cands, {c: 'x' for c in committees}, cycle_end)
    tx = defaultdict(list)
    for r in keep:
        if r.get('memo_code') != 'X' and not r.get('memoed_subtotal') and r.get('disbursement_type') in ('24K', '24Z') \
                and r.get('candidate_id') in cands and r.get('disbursement_date'):
            tx[cands[r['candidate_id']]].append((date.fromisoformat(r['disbursement_date'][:10]), float(r['disbursement_amount']),
                                                 r.get('recipient_committee_id')))
    return {b: float(v['x']) for b, v in totals.items()}, tx, rejected, latest


def mentat_side(wb, companies, cycle, cands, pin=None):
    links = {}
    for r in IM.sheet_rows(wb, 'Committee Sponsors'):
        if r.get('Link Source') == 'FEC ccl' and r.get('Relationship') in IM.CANDIDATE_LINKS and r.get('Election Cycle') == cycle:
            links.setdefault(r.get('Committee ID'), set()).add(r.get('Candidate ID'))
    totals, tx, latest, unlinked = defaultdict(float), defaultdict(list), None, []
    year = cycle[-4:]
    for name in [n for n in wb.sheetnames if n in ('Contributions', 'Contributions ' + year)]:
        it = wb[name].iter_rows(values_only=True); header = next(it)
        for row in it:
            r = dict(zip(header, row))
            if r.get('Election Cycle') != cycle or not r.get('Date'):
                continue
            d = r['Date'].date() if hasattr(r['Date'], 'date') else date.fromisoformat(str(r['Date'])[:10])
            latest = max(latest or d, d)   # the BOOK's data date (every receipt in the cycle), not one company's
            if r.get('Company') not in companies or r.get('Contributor Tier') != 'Corporate' or not r.get('Include in Analysis'):
                continue
            if pin and d > pin:
                continue
            amt = float(r.get('Analysis Amount') or 0)
            bios = {cands.get(c) for c in links.get(r.get('Committee ID'), ())}
            if len(bios) != 1 or None in bios:
                unlinked.append((d, amt, r.get('Committee ID'), r.get('Recipient')))
                continue
            b = bios.pop()
            totals[b] += amt; tx[b].append((d, amt))
    return dict(totals), tx, latest, unlinked


def run(root, out_dir, mentat_inputs, today, plant=None):
    import openpyxl
    root = Path(root)
    groups = committee_policy(root / 'fec/attic/full_congestion.py')
    roster = json.loads((root / 'data/engine/congestion.json').read_text(encoding='utf-8'))
    bios = {r['bioguide'] for r in roster if r.get('bioguide')}
    cands = {c: b for c, b in candidate_map(root / 'fec/legislators.csv').items() if b in bios}
    pac44 = IM.pac44_companies(groups, Path(mentat_inputs) / 'verified_company_affiliations.json',
                               root / 'data/money/mentat_company_pacs.json')
    proj = json.loads((root / 'data/money/mentat_money_projection.json').read_text(encoding='utf-8'))
    books = {b: e for b, e in proj['header']['books'].items() if e.get('status') == 'ok'}
    report = {'books': {b: e['workbook'] for b, e in books.items()}, 'cycles': {}}
    cur, pages = fec_pages(root)
    # Second path for the FEC leg: the pages reproduce congestion.json for all 44 committees.
    allt, _, _ = aggregate([r for r in pages if r['committee_id'] in groups], cands, groups, cur['cycle'])
    byb = {r['bioguide']: r for r in roster}
    leg_mismatch = sorted(b for b in set(cands.values()) if byb[b].get('money_source_status') == 'VERIFIED_OPENFEC_CURRENT_CYCLE'
                          and any(max(0, round(allt.get(b, {}).get(g, Decimal(0)))) != (byb[b].get(g) or 0)
                                  for g in ('cu', 'banks', 'finserv')))   # project(): per group, refunds floor at 0
    report['fec_leg_reproduced'] = {'members_mismatched': len(leg_mismatch), 'examples': leg_mismatch[:5]}
    covered, comp_of = set(), {}
    for book, e in books.items():
        wb = openpyxl.load_workbook(e['workbook'], read_only=True, data_only=True)
        companies = {r.get('Company') for r in IM.sheet_rows(wb, 'Recipient Facts')} & set(pac44)
        for co in companies:
            covered |= set(pac44[co]); comp_of[co] = book
        for cycle, cycle_end in (('2023-2024', 2024), ('2025-2026', 2026)):
            if not companies:
                continue
            m_tot, m_tx, m_latest, unl = mentat_side(wb, companies, cycle, cands)
            committees = {c for co in companies for c in pac44[co]}
            if cycle_end == 2024:
                f_rows = fec_bulk(Path(mentat_inputs) / 'fec_beneficiary/pas224/itpas2.txt', committees, 2024)
                f_tot, f_tx, rej, f_latest = per_member(f_rows, cands, committees, 2024)
                if plant: m_tot[plant] = m_tot.get(plant, 0) + 5000.0
                res = gate_closed(f_tot, m_tot)
                res['miss_classes'] = {b: classify(f_tx.get(b, []), m_tx.get(b, []), date(2024, 12, 31), unl)
                                       for b in res['misses']}   # explanation only; the closed bar is not class-relieved
            else:
                pin = min(m_latest or date.min, date.fromisoformat(cur['latest_attributed_transaction_date']))
                m_tot, m_tx, _, unl = mentat_side(wb, companies, cycle, cands, pin)
                f_tot, f_tx, rej, f_latest = per_member(pages, cands, committees, cycle_end, pin)
                if plant:
                    m_tot[plant] = m_tot.get(plant, 0) + 5000.0; m_tx[plant] = m_tx[plant] + [(pin - timedelta(days=200), 5000.0)]
                classes = {b: classify(f_tx.get(b, []), m_tx.get(b, []), pin, unl) for b in set(f_tot) | set(m_tot)}
                res = gate_current(f_tot, m_tot, classes); res['pin'] = str(pin)
            res.update(book=book, companies=sorted(companies), committees=sorted(committees), fec_rejections=rej)
            report['cycles'].setdefault(cycle, []).append(res)
        wb.close()
    report['coverage_gap_committees'] = sorted(set(groups) - covered)
    verdicts = [r['pass'] for rs in report['cycles'].values() for r in rs]
    report['gate1_pass'] = bool(verdicts) and all(verdicts) and not leg_mismatch
    if out_dir:
        out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
        (out / 'report.json').write_text(json.dumps(report, indent=1, sort_keys=True, default=str), encoding='utf-8')
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--root', type=Path, default=Path(ROOT))
    p.add_argument('--out', type=Path, default=None)
    p.add_argument('--mentat-inputs', default=r'C:\Users\pgoff\Mentat\mentat\inputs')
    p.add_argument('--plant', default=None, help='mutation check: add a $5,000 Mentat row to this bioguide; the gate must FAIL')
    a = p.parse_args(argv)
    today = datetime.now(timezone.utc).date()
    out = a.out or (a.root / 'audits' / ('mentat_money_reconcile_%s' % today.strftime('%Y%m%d')))
    r = run(a.root, None if a.plant else out, a.mentat_inputs, today, a.plant)
    print(json.dumps({k: v for k, v in r.items() if k != 'cycles'}, default=str))
    for cycle, rs in r['cycles'].items():
        for x in rs:
            print(cycle, x['book'], 'PASS' if x['pass'] else 'FAIL',
                  {k: x[k] for k in x if k in ('agg_fec', 'agg_mentat', 'agg_rel', 'share_exact', 'members', 'unexplained', 'pin')})
    print('GATE 1:', 'PASS' if r['gate1_pass'] else 'FAIL')
    return 0 if r['gate1_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
