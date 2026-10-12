# -*- coding: utf-8 -*-
"""CU PLACEHOLDER-LINK BACKFILL - sidecar for the 2026-06-25 CU cosponsor network.

WHY. The R scraper (Draft Systems/congress_member_scraper.R, parse_single_bill_page) took a
one-hit detail page's DetailLink from '#summarySelector a:nth-child(1)', which on 172 rows of
congress_bills_archive_20260624_115822.xlsx is the glossary help link
'/help/legislative-glossary#glossary_billsummary'. Stage 3 dedups sponsored bills by DetailLink,
so 139 sponsored bills collapsed onto that one URL; the 111 with no other real-link row lost
every sponsor -> cosponsor edge (the scraper is fixed 2026-10-11 for future scrapes).

WHAT. For each lost bill this rebuilds the ties Stage 3 would have built, from BILLSTATUS on disk
(data/holistic/billcache_<congress>.ndjson: sponsor and cosponsor bioguides, original flag), with
Stage 3's own weighting: role (original 1.0 / regular 0.5) x SUCCESS (3.0 when the archive
LastAction matches PASSED_RE) x DecayWeight / sqrt(C), C = cosponsors listed (withdrawn ones
included, as the cosponsors page lists them). Ties are summed per directed (From, To), as Stage 3
groups them. The sponsor node is BILLSTATUS's bioguide, and a bill whose BILLSTATUS sponsor is not
the archive Member's roster bioguide is REFUSED (the Menendez lesson), never guessed.

The replica of Stage 3 over the 06-24 archive's REAL-link bills reproduces 1,755 of the file's
2,214 edges to 1e-6 (the rest is cosponsor drift since 06-25 plus the 9 Menendez edges).

READ-ONLY on inputs; writes ONE sidecar (default data/network/cu_placeholder_backfill_20260625.json).
moneyball_core.cu_edge_rows() merges it into that vintage at read time (CU_PLACEHOLDER_BACKFILL=1,
default; 0 = off). Import has no side effects; run:  python fec/network/build_cu_placeholder_backfill.py
"""
import os as _o, sys as _s
_s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import argparse, collections, hashlib, json, math, os, re, sys

VINTAGE = 'congress_cosponsor_network_20260625_205554.xlsx'
ARCHIVE = 'congress_bills_archive_20260624_115822.xlsx'
OUT_REL = os.path.join('data', 'network', 'cu_placeholder_backfill_20260625.json')
BILL_PATH_RE = re.compile(r'/\d+(st|nd|rd|th)-congress/(house|senate)-(bill|resolution|joint-resolution|concurrent-resolution)/\d+')
PASSED_RE = re.compile('passed|agreed to in|became.*law|public law|to president|resolving differences|veto', re.I)  # Stage 3 PASSED_RE
TYPES = {'H.R.': 'hr', 'S.': 's', 'H.Res.': 'hres', 'S.Res.': 'sres', 'H.J.Res.': 'hjres',
         'S.J.Res.': 'sjres', 'H.Con.Res.': 'hconres', 'S.Con.Res.': 'sconres'}
W_ORIGINAL, W_REGULAR, SUCCESS_MULT = 1.0, 0.5, 3.0


def is_bill_link(link):
    return bool(BILL_PATH_RE.search(str(link or '')))


def bill_key(name, congress):
    m = re.match(r'^([A-Za-z.]+?)\s*(\d+)$', str(name or '').strip())
    t = TYPES.get(m.group(1)) if m else None
    return (int(congress), t, int(m.group(2))) if t else None


def lost_sponsored_bills(rows):
    """Sponsor rows (archive order) whose bill has NO real-link sponsor row: first row per bill."""
    spon = [r for r in rows if str(r.get('Role', '')).lower() == 'sponsor']
    real = {(str(r['Name']), int(r['Congress'])) for r in spon if is_bill_link(r.get('DetailLink'))}
    out, seen = [], set()
    for r in spon:
        k = (str(r['Name']), int(r['Congress']))
        if is_bill_link(r.get('DetailLink')) or k in real or k in seen:
            continue
        seen.add(k); out.append(r)
    return out


def backfill_ties(bills, cache, roster_bioguide):
    """-> (ties {(from,to): [weight, nbills, norig, nreg]}, refused [(name, congress, reason)])."""
    ties = collections.defaultdict(lambda: [0.0, 0, 0, 0]); refused = []
    for r in bills:
        k = bill_key(r['Name'], r['Congress']); d = cache.get(k) if k else None
        if not d:
            refused.append((r['Name'], int(r['Congress']), 'not in BILLSTATUS cache')); continue
        if d.get('sponsor') != roster_bioguide.get(r.get('Member')):
            refused.append((r['Name'], int(r['Congress']), 'BILLSTATUS sponsor %s != roster %s'
                            % (d.get('sponsor'), roster_bioguide.get(r.get('Member'))))); continue
        cos = list(dict.fromkeys(c[0] for c in d['cos']))
        orig = {c[0] for c in d['cos'] if c[1]}
        if not cos:
            continue
        decay = float(r.get('DecayWeight')) if r.get('DecayWeight') not in (None, '') else 1.0
        if not math.isfinite(decay): decay = 1.0
        f = (SUCCESS_MULT if PASSED_RE.search(str(r.get('LastAction') or '')) else 1.0) * decay / math.sqrt(len(cos))
        for bio in cos:
            if bio == d['sponsor']:
                continue
            t = ties[(d['sponsor'], bio)]
            t[0] += (W_ORIGINAL if bio in orig else W_REGULAR) * f; t[1] += 1
            t[2 if bio in orig else 3] += 1
    return ties, refused


def load_cache(cache_dir):
    cache = {}
    for c in (117, 118, 119):
        with open(os.path.join(cache_dir, 'billcache_%d.ndjson' % c), encoding='utf-8') as fh:
            for line in fh:
                d = json.loads(line); m = re.match(r'BILLSTATUS-(\d+)([a-z]+)(\d+)\.xml', d['src'])
                cache[(int(m.group(1)), m.group(2), int(m.group(3)))] = d
    return cache


def _sha(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main(argv=None):
    from paths import ROOT
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default=os.path.join(ROOT, OUT_REL))
    a = ap.parse_args(argv)
    import pandas as pd
    import moneyball_core as MC
    arch = os.path.join(ROOT, ARCHIVE); cdir = os.path.join(ROOT, 'data', 'holistic')
    rows = pd.read_excel(arch, sheet_name='All Bills').to_dict('records')
    roster = {m['member']: m.get('bioguide') for m in json.load(open(os.path.join(ROOT, 'data', 'engine', 'congestion.json'), encoding='utf-8'))}
    lost = lost_sponsored_bills(rows)
    ties, refused = backfill_ties(lost, load_cache(cdir), roster)
    doc = {'schema': 'cu-placeholder-backfill-v1', 'vintage': VINTAGE,
           'inputs_sha256': {ARCHIVE: _sha(arch), **{'data/holistic/billcache_%d.ndjson' % c: _sha(os.path.join(cdir, 'billcache_%d.ndjson' % c)) for c in (117, 118, 119)}},
           'weighting': 'Stage 3: role(1.0 orig/0.5 reg) x SUCCESS(3.0 if PASSED_RE on archive LastAction) x DecayWeight / sqrt(C incl. withdrawn); summed per (From,To)',
           'lost_bills': [[str(r['Name']), int(r['Congress']), r['Member']] for r in lost],
           'refused': refused,
           'edges': [{'from': f, 'to': t, 'weight': v[0], 'nbills': v[1], 'noriginal': v[2], 'nregular': v[3]}
                     for (f, t), v in sorted(ties.items())]}
    MC.dump_atomic(doc, a.out)
    print('cu placeholder backfill: %d lost bills, %d refused, %d directed edges -> %s'
          % (len(lost), len(refused), len(doc['edges']), a.out))
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
