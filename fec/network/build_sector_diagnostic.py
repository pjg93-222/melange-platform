# -*- coding: utf-8 -*-
"""Sector-scoped cosponsorship networks -- AUDIT-ONLY DIAGNOSTICS (Paul-gated 2026-08-18).

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
=============================================
Paul asked for sector networks alongside the credit-union one. The scouting verdict was that
CU's advantage is not code -- it is a hand-authored, adjudicated bill universe (17 congress.gov
full-text search templates plus a curated false-positive drop list, in the EXTERNAL R scraper).
No equivalent exists for any other sector. Paul then ruled: build CRYPTO and BANK REGULATION as
DIAGNOSTICS, drop fintech entirely (36 bills, whose largest edge contributor was a VA benefits
bill -- noise with a name).

So this producer emits AUDIT-ONLY artifacts. They are NOT a NET_CONFIG value, NOT on the
dashboard, and NOT engine inputs. That is a deliberate epistemic choice, not an unfinished
feature: measured on the shipped kernel p = w/(w+6), the crypto graph's strongest edge reaches
p = 0.23 against CU's 0.52, with 24 dyads at p>=0.10 against CU's 252. Running cascade,
pathfinder or influence-maximization on it would produce confident output from a graph an order
of magnitude weaker in the tail. `assert_output_name` below enforces the separation mechanically.

UNIVERSE DEFINITIONS -- the honest part
=======================================
CRYPTO: there is NO CRS subject term for crypto. Across the 55,195-bill 117-119 archive, zero of
the 1,072 distinct legislativeSubjects terms match crypto/digital-asset/blockchain/token/ledger/
stablecoin. Title matching is therefore the ONLY available method, and DE-001 permits it strictly
as a NEGATIVE screen -- it bounds what could be in scope, it does not label. Every bill this
screen admits is a CANDIDATE, and the artifact says so.

BANK REGULATION: here the CRS vocabulary does exist, so the universe is a subject-term set
(Tier B of the scouted proposal) rather than a keyword screen. TWO deliberate departures:
  * `Currency` is EXCLUDED despite being in Tier B. It is the CRS home for digital-currency
    bills (96 of its 161 bills are unique to it), so including it would silently re-import the
    crypto scope Paul just separated into its own diagnostic.
  * `National Credit Union Administration` is retained but contributes 0 unique bills; FDIC,
    Federal Reserve System and FSOC add 6/4/1. They cost nothing and document intent.
Subjects are populated on only 23,221 of 55,195 bills (42.1%), so this universe is bounded by
subject-coding coverage, not by the true extent of bank-regulation legislation. Stated in the
manifest, because a reader would otherwise take the bill count as the population.

NUMBERS THAT DID NOT REPRODUCE (recorded so they are not re-quoted)
==================================================================
An earlier analysis reported crypto at 133 bills / 307 members / 450 pairs and claimed the CBDC
bill family carried 69% of edge mass. Neither reproduces. This producer's auditable baseline is
119 bills (129 with the two measured term additions) and the CBDC-3 family carries 16.4% of mass
under star construction with the holistic role weights -- the maximum over a 32-variant
construction sweep is 50.7%, so 69% is unreachable. The pair counts DID roughly reproduce
(436 vs 450), which is why the 69% is believed to be a mass-accounting error rather than a
different universe.

The real weakness is not concentration, it is EMPTINESS: of 119 screened bills only 75 contribute
a single tie (35 have zero cosponsors, 9 have an off-roster sponsor). The graph rests on ~621 tie
events. Both counts ship in the manifest; quoting only the screened count overstates the base.

CONSTRUCTION
============
Star (sponsor -> cosponsor), never cliques. A clique construction inflates a 165-cosponsor bill
into 13,530 pairs and was measured to take crypto from 450 to 14,021 -- 68% of it from one bill.
`assemble` and `bill_factor` are imported VERBATIM from build_holistic_base so this shares the
scraper-parity weight formula, the duplicate-cosponsor collapse, the D6-c/D6-d roster rules and
the separated WOriginal/WRegular subtotals.
"""
import os, re, sys, json, math, zipfile, hashlib, datetime, collections
import xml.etree.ElementTree as ET

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
from paths import ROOT as MB_ROOT
from network.build_holistic_base import (assemble, bill_factor, bill_passed, success_cache_dir,
                                         SUCCESS_MULT, PRODUCTION_NAME_RE)

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

RAW = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Melange', 'lda_rebuild', 'raw')
CACHE_DIR = os.path.join(MB_ROOT, 'data', 'holistic')
OUT_DIR = os.path.join(MB_ROOT, 'audits', 'sector_diagnostics')

# ---- output-name guard, mirroring build_holistic_base:142-151 (COR-9) -------------------------
HOLISTIC_NAME_RE = re.compile(r'^holistic_network_', re.I)

# Named production artifacts that must never be the target, even under a legal-looking name.
FORBIDDEN_NAMES = frozenset({
    'moneyball main version - blended.xlsx', 'bipartisan.json', 'behavior_panel.json',
    'behavioral_dispositions.json', 'ranks_python.json', 'dashboard_data.json',
})


def allowed_output_roots():
    """Directory confinement: audits/ or a temp dir. Modelled on
    build_legislation_base_network.allowed_output_roots (:229-235)."""
    import tempfile
    roots = {os.path.realpath(os.path.join(MB_ROOT, 'audits')),
             os.path.realpath(os.path.join(MB_ROOT, 'tmp')),
             os.path.realpath(tempfile.gettempdir())}
    for ev in ('TEMP', 'TMP', 'TMPDIR'):
        v = os.environ.get(ev)
        if v: roots.add(os.path.realpath(v))
    return tuple(sorted(roots))


def _under(path, root):
    try:
        return os.path.commonpath([os.path.realpath(path), root]) == root
    except ValueError:      # different drive on Windows
        return False


def assert_output_name(path, promoted=False):
    """Refuse any name a production globber could pick up, AND confine the directory.

    TWO legal shapes, never a blanket exemption:
      * DIAGNOSTIC  sector_diagnostic_*  under audits/ or a temp dir.
      * PROMOTED    sector_network_*     at the live root, engine-loadable via NET_CONFIG.
    Promotion (Paul 2026-08-18: bank_reg then crypto) is what lets moneyball_core._select_sector
    find the graph. Both shapes must still fail the congress_cosponsor_network_* family (18 call
    sites, three of them max-by-mtime with no AUG filter) and the holistic_network_* family
    (_select_holistic RAISES on any member it cannot classify).

    Nineteen live call sites glob congress_cosponsor_network_* across seventeen files; the
    holistic family has its own consumers. A diagnostic must fail BOTH families, carry its own
    prefix, and -- the part that is easy to forget -- it must not be possible to aim the producer
    at the live root via its output dial. `fullmatch` rather than `match` is deliberate: it is the
    stricter read and is what the legislation-base precedent uses (:246)."""
    base = os.path.basename(path)
    if base.lower() in FORBIDDEN_NAMES:
        raise ValueError('SEC-1: refusing a named production artifact: ' + base)
    if PRODUCTION_NAME_RE.fullmatch(base):
        raise ValueError('SEC-1: refusing a production-family filename: ' + base)
    if HOLISTIC_NAME_RE.match(base):
        raise ValueError('SEC-1: refusing a holistic-family filename: ' + base)
    if not base.lower().endswith(('.xlsx', '.manifest.json')):
        raise ValueError('SEC-1: sector output must be .xlsx or .manifest.json: ' + base)
    want = 'sector_network_' if promoted else 'sector_diagnostic_'
    if not base.startswith(want):
        raise ValueError('SEC-1: %s output must be named %s*: %s'
                         % ('promoted' if promoted else 'diagnostic', want, base))
    if promoted:
        # engine-loadable, so it MUST sit at the live root where _select_sector globs
        if os.path.dirname(os.path.realpath(path)) != os.path.realpath(MB_ROOT):
            raise ValueError('SEC-1: a promoted sector network must be written to the live root: '
                             + str(path))
    else:
        roots = allowed_output_roots()
        if not any(_under(path, r) for r in roots):
            raise ValueError('SEC-1: output escaped the allowed roots (audits/ or temp): ' + str(path))

# ---- CRYPTO: title screen (DE-001 NEGATIVE screen -- candidates, never labels) ----------------
# Per-term yields measured on display titles over 117-119. `digital commodity` and `BITCOIN` are
# ADDITIONS beyond the scouted 11: together they close 8 of the 15 adjudicated-set recall misses
# (11/26 -> 19/26) for +10 bills and no observed false positive. A branded-act regex
# (GENIUS|STABLE|ECASH|FIT21|CLARITY) would reach 25/26 but explodes the screen to 184 bills with
# ~50 pure false positives from 'Clarity Act' alone -- measured, and rejected.
CRYPTO_TERMS = [
    ('digital asset',              re.compile(r'digital\s+asset', re.I)),
    ('cryptocurrency',             re.compile(r'crypto[-\s]?currenc(?:y|ies)', re.I)),
    ('virtual currency',           re.compile(r'virtual\s+currenc(?:y|ies)', re.I)),
    ('blockchain',                 re.compile(r'block[-\s]?chain', re.I)),
    ('stablecoin',                 re.compile(r'stable[-\s]?coin', re.I)),
    ('CBDC',                       re.compile(r'\bCBDC\b')),                    # case-SENSITIVE
    ('central bank digital currency', re.compile(r'central\s+bank\s+digital\s+currenc(?:y|ies)', re.I)),
    ('distributed ledger',         re.compile(r'distributed\s+ledger', re.I)),
    ('token',                      re.compile(r'\btoken(?:s|ized|ization|ised|isation)?\b', re.I)),
    ('DeFi',                       re.compile(r'\bDeFi\b')),                    # case-SENSITIVE
    ('crypto',                     re.compile(r'\bcrypto\w*', re.I)),
    ('digital commodity',          re.compile(r'digital\s+commodit(?:y|ies)', re.I)),
    ('BITCOIN',                    re.compile(r'\bBITCOIN\b')),                 # case-SENSITIVE acronym
]

# Hand-adjudicated exceptions, in the house format used by
# build_greenlit_legislation_primacy_merge.py:110-158 -- key 'congress:type:number' ->
# (disposition, authority, rationale). Only CLASS 1 (not crypto in any sense) is DROPPED here.
# The non-financial blockchain/DLT class is FLAGGED-BUT-KEPT: those bills are championed by the
# same trade associations as the digital-asset bills, so culling them is a judgment for Paul, not
# a measurement I should make silently. Every flagged row is listed in the manifest.
CRYPTO_EXCEPTIONS = {
    '119:hr:9516':  ('drop', 'L7:title-rule',
                     'Post-quantum CRYPTOGRAPHY (EO 14412), not digital assets; caught by crypto*'),
    '119:sres:598': ('drop', 'L7:title-rule',
                     'UAE advanced AI chip export condemnation; crypto appears only incidentally'),
}

# ---- BANK REGULATION: CRS subject terms (Tier B less Currency) --------------------------------
BANKREG_SUBJECTS = [
    'Banking and financial institutions regulation',
    'Bank accounts, deposits, capital',
    'Federal Deposit Insurance Corporation (FDIC)',
    'National Credit Union Administration',
    'Federal Reserve System',
    'Financial crises and stabilization',
    'Foreign and international banking',
    'Financial Stability Oversight Council',
    'Consumer credit',
    'Credit and credit markets',
    'Consumer Financial Protection Bureau',
    'Financial literacy',
    'Monetary policy',
]
BANKREG_EXCLUDED = {'Currency': 'CRS home for digital-currency bills (96 of 161 unique to it); '
                                'including it would re-import the crypto scope, which Paul '
                                'separated into its own diagnostic'}

SRC_RE = re.compile(r'BILLSTATUS-(\d+)([a-z]+)(\d+)\.xml$', re.I)


def dials():
    return {
        'congresses': tuple(int(c) for c in os.environ.get('SECTOR_CONGRESSES', '117,118,119').split(',')),
        'decay': float(os.environ.get('SECTOR_DECAY', '0.8')),
        'newest': int(os.environ.get('SECTOR_NEWEST', '119')),
        'ow': float(os.environ.get('SECTOR_ORIGINAL_W', '1.0')),
        'rw': float(os.environ.get('SECTOR_REGULAR_W', '0.2')),   # holistic parity; CU uses 0.5
        'types': tuple(t.strip().lower() for t in os.environ.get(
            'SECTOR_TYPES', 'hconres,hjres,hr,hres,s,sconres,sjres,sres').split(',')),
        'out_dir': os.environ.get('SECTOR_OUT_DIR', OUT_DIR),
    }


def parse_with_text(data):
    """build_holistic_base.parse_billstatus_xml EXTENDED with title, policyArea and subjects.

    The shipped parser keeps only {congress, passed, sponsor, cos}, and the shipped cache
    (data/holistic/billcache_*.ndjson) therefore carries no title and no subjects -- neither
    universe here can be defined from it. The shipped parser also deliberately skips
    bill/subjects when reading policyArea, so subject extraction had to be added, not reused."""
    root = ET.fromstring(data)
    bill = root.find('bill') if root.find('bill') is not None else root
    cg = bill.findtext('congress')
    sponsor = bill.findtext('sponsors/item/bioguideId')
    cos = []
    for it in bill.findall('cosponsors/item'):
        cos.append((it.findtext('bioguideId'),
                    (it.findtext('isOriginalCosponsor') or '').strip().lower() == 'true',
                    bool((it.findtext('sponsorshipWithdrawnDate') or '').strip())))
    subs = [s.text.strip() for s in bill.findall('subjects/legislativeSubjects/item/name')
            if s is not None and s.text]
    # SUCCESS via the shared selector (D6-b, HOLISTIC_SUCCESS) so the two builders cannot diverge.
    return {'congress': int(cg) if cg else None, 'passed': bill_passed(bill),
            'sponsor': sponsor, 'cos': cos,
            'title': (bill.findtext('title') or '').strip(),
            'policy': (bill.findtext('policyArea/name') or '').strip(),
            'subjects': subs}


def extract_congress(cg, types, cache_dir):
    """Title+subject-bearing NDJSON cache, resume-safe.

    Deliberately named sectorcache_* rather than billcache_* -- the shipped cache-hit check
    compares record COUNT against the zips, so a title-bearing cache under the same name would
    false-hit against the existing title-less one and silently yield zero screen matches.
    For the same reason each SUCCESS semantics (D6-b) gets its own directory."""
    cache_dir = success_cache_dir(cache_dir)
    os.makedirs(cache_dir, exist_ok=True)
    cache = os.path.join(cache_dir, 'sectorcache_%d.ndjson' % cg)
    zdir = os.path.join(RAW, 'billstatus%d' % cg)
    if not os.path.isdir(zdir):
        raise SystemExit('SEC-2: BILLSTATUS archive missing for congress %d at %s' % (cg, zdir))
    zips = sorted(z for z in os.listdir(zdir) if z.lower().endswith('.zip')
                  and z.replace('BILLSTATUS-%d-' % cg, '').replace('.zip', '').lower() in types)
    missing = set(types) - {z.replace('BILLSTATUS-%d-' % cg, '').replace('.zip', '').lower() for z in zips}
    if missing and cg >= 117:
        raise SystemExit('SEC-2: dialled bill types with no zip for %d: %s' % (cg, sorted(missing)))
    expect, shas = 0, {}
    for z in zips:
        p = os.path.join(zdir, z)
        with zipfile.ZipFile(p) as zf:
            expect += sum(1 for n in zf.namelist() if n.lower().endswith('.xml'))
        h = hashlib.sha256()
        with open(p, 'rb') as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b''): h.update(chunk)
        shas[z] = {'sha256': h.hexdigest(), 'mtime': int(os.path.getmtime(p))}
    if os.path.exists(cache):
        have = sum(1 for _ in open(cache, encoding='utf-8'))
        if have == expect:
            print('  cache hit sectorcache_%d.ndjson (%d bills)' % (cg, have))
            return cache, shas
    n = bad = 0
    with open(cache + '.tmp', 'w', encoding='utf-8') as out:
        for z in zips:
            with zipfile.ZipFile(os.path.join(zdir, z)) as zf:
                for name in zf.namelist():
                    if not name.lower().endswith('.xml'): continue
                    try:
                        rec = parse_with_text(zf.read(name)); rec['src'] = name
                        out.write(json.dumps(rec) + '\n'); n += 1
                    except Exception as ex:
                        bad += 1; print('  PARSE FAIL %s: %s' % (name, ex))
            print('    %s (cum %d)' % (z, n))
    os.replace(cache + '.tmp', cache)
    if bad: print('  WARNING congress %d: %d parse failures of %d' % (cg, bad, expect))
    return cache, shas


def bill_key(rec):
    m = SRC_RE.search(rec.get('src') or '')
    if not m: return None
    return '%s:%s:%s' % (m.group(1), m.group(2).lower(), int(m.group(3)))


def screen_crypto(recs):
    """NEGATIVE screen -> candidates. Returns (kept, per_term, dropped, flagged)."""
    kept, per_term, dropped = [], collections.Counter(), []
    for r in recs:
        title = r.get('title') or ''
        hits = [name for name, rx in CRYPTO_TERMS if rx.search(title)]
        if not hits: continue
        k = bill_key(r)
        exc = CRYPTO_EXCEPTIONS.get(k)
        if exc and exc[0] == 'drop':
            dropped.append({'key': k, 'title': title[:150], 'terms': hits,
                            'authority': exc[1], 'rationale': exc[2]})
            continue
        for h in hits: per_term[h] += 1
        r['_key'] = k; r['_terms'] = hits
        kept.append(r)
    return kept, per_term, dropped


def screen_bankreg(recs):
    want = set(BANKREG_SUBJECTS)
    kept, per_term = [], collections.Counter()
    for r in recs:
        hits = [s for s in (r.get('subjects') or []) if s in want]
        if not hits: continue
        for h in hits: per_term[h] += 1
        r['_key'] = bill_key(r); r['_terms'] = hits
        kept.append(r)
    return kept, per_term, [], []


def cu_overlap(edges):
    """Dyad overlap against the PUBLISHED CU network.

    The decisive honesty number for a sector view: a graph whose ties already exist in the
    published network is not an independent view of anything, however large it looks. Measured
    through load_network so the CU side is exactly the published 2,555 dyads (roster filter +
    mean-symmetrize), not the raw 2,895 workbook rows."""
    try:
        import moneyball_core as mc
        cu = set()
        for u, d in mc.load_network().items():
            for v in d:
                cu.add((u, v) if u < v else (v, u))
        C = json.load(open(os.path.join(MB_ROOT, 'data', 'engine', 'congestion.json'), encoding='utf-8'))
        b2n = {m['bioguide']: m['member'] for m in C if m.get('bioguide')}
        mine = set()
        for (a, b) in edges:
            na, nb = b2n.get(a), b2n.get(b)
            if na and nb and na != nb:
                mine.add((na, nb) if na < nb else (nb, na))
        inter = mine & cu
        return {'cu_dyads': len(cu), 'sector_dyads_mapped': len(mine),
                'shared': len(inter),
                'pct_of_sector_already_in_cu': round(100.0 * len(inter) / max(1, len(mine)), 1),
                'pct_of_cu_covered': round(100.0 * len(inter) / max(1, len(cu)), 1),
                'novel_dyads': len(mine - inter),
                'pct_novel': round(100.0 * len(mine - inter) / max(1, len(mine)), 1),
                'jaccard': round(len(inter) / max(1, len(mine | cu)), 3)}
    except Exception as ex:
        return {'error': '%s: %s' % (type(ex).__name__, str(ex)[:120])}


def roster():
    C = json.load(open(os.path.join(MB_ROOT, 'data', 'engine', 'congestion.json'), encoding='utf-8'))
    return {m['bioguide'] for m in C if m.get('bioguide')}, len(C)


def graph_stats(edges, bills, D):
    """Diagnostic-grade statistics. The engine-floor numbers are the point of the artifact.

    p uses the bare shipped kernel w/(w+6) (moneyball_core:371 with cpf=sigma=1). That is an
    UPPER BOUND on real activation: the full kernel multiplies w by cpf<=1.7 and a susceptibility
    term, so true engine probabilities are generally lower still. Reported this way because it is
    what reproduces the published CU reference (2,555 dyads, p_max 0.5198, 252 at p>=0.10)."""
    sym = {}
    for (a, b), e in edges.items():
        w = e['Weight']
        k = (a, b) if a < b else (b, a)
        sym[k] = (sym[k] + w) / 2.0 if k in sym else w
    ws = sorted(sym.values())
    p = lambda w: w / (w + 6.0)
    members = {m for k in sym for m in k}
    return {
        'directed_pairs': len(edges),
        'dyads': len(sym),
        'members_touched': len(members),
        'total_edge_mass': round(sum(e['Weight'] for e in edges.values()), 4),
        'w_max': round(ws[-1], 4) if ws else 0.0,
        'p_max': round(p(ws[-1]), 4) if ws else 0.0,
        'p_median': round(p(ws[len(ws) // 2]), 4) if ws else 0.0,
        'dyads_p_ge_010': sum(1 for w in ws if p(w) >= 0.10),
        'dyads_p_ge_005': sum(1 for w in ws if p(w) >= 0.05),
        'bills_screened': len(bills),
        'bills_contributing': None,     # filled by caller
        # HUB CONCENTRATION -- the audit record must carry what the dashboard banner shows.
        # Aggregate weakness does not convey WHY a sector graph is thin: crypto is one 167-degree
        # star (the CBDC cosponsor roster) against a second-place 25, and cascade reach measured
        # 33 from the hub vs 7 from the next best seed. 'dominated' is the surface-worthy
        # condition -- a hub merely EXISTING is unremarkable, CU has one too.
        'concentration': _concentration(sym),
    }


def _concentration(sym):
    """Degree/weight concentration of the symmetrized dyad map."""
    if not sym: return None
    deg, wsum = {}, {}
    for (a, b), w in sym.items():
        deg[a] = deg.get(a, 0) + 1; deg[b] = deg.get(b, 0) + 1
        wsum[a] = wsum.get(a, 0.0) + w; wsum[b] = wsum.get(b, 0.0) + w
    rank = sorted(deg.items(), key=lambda kv: -kv[1])
    hub, hd = rank[0]
    sd = rank[1][1] if len(rank) > 1 else 0
    tw = sum(sym.values()) or 1.0
    return {'hub_bioguide': hub, 'hub_degree': hd, 'second_degree': sd,
            'hub_share_of_weight_pct': round(wsum.get(hub, 0.0) / tw * 100, 1),
            'dominated': bool(sd and hd >= 3 * sd)}


def build(sector, recs, D, rost, n_roster, shas):
    if sector == 'crypto':
        kept, per_term, dropped, flagged = screen_crypto(recs) + ([],)
    else:
        kept, per_term, dropped, flagged = screen_bankreg(recs)

    zero_cos = sum(1 for r in kept if not r.get('cos'))
    off_sponsor = sum(1 for r in kept if r.get('sponsor') not in rost)
    edges, st = assemble(kept, rost, D['ow'], D['rw'], D['decay'], D['newest'])
    contributing = sum(1 for r in kept
                       if r.get('sponsor') in rost and any(c[0] in rost for c in (r.get('cos') or [])))

    stats = graph_stats(edges, kept, D)
    stats['bills_contributing'] = contributing
    stats['bills_zero_cosponsor'] = zero_cos
    stats['bills_sponsor_off_roster'] = off_sponsor
    stats['roster_size'] = n_roster
    stats['member_coverage_pct'] = round(100.0 * stats['members_touched'] / max(1, n_roster), 1)

    by_cg = collections.Counter(r.get('congress') for r in kept)
    return {'edges': edges, 'bills': kept, 'stats': stats, 'per_term': dict(per_term),
            'dropped': dropped, 'flagged': flagged, 'by_congress': dict(sorted(by_cg.items())),
            'assemble_stats': st}


def write_xlsx(path, edges, promoted=False):
    import openpyxl
    assert_output_name(path, promoted)
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'Edges'
    ws.append(['From', 'To', 'Weight', 'NBills', 'NOriginal', 'NRegular', 'WOriginal', 'WRegular'])
    for (a, b), e in sorted(edges.items()):
        ws.append([a, b, round(e['Weight'], 6), e.get('NBills', 0), e.get('NOriginal', 0),
                   e.get('NRegular', 0), round(e.get('WOriginal', 0.0), 6),
                   round(e.get('WRegular', 0.0), 6)])
    wb.save(path)
    return ws.max_row - 1


def main():
    D = dials()
    rost, n_roster = roster()
    only = os.environ.get('SECTOR_ONLY', 'crypto,bank_reg').split(',')
    promote = os.environ.get('SECTOR_PROMOTE', '') == '1'
    os.makedirs(D['out_dir'], exist_ok=True)
    stamp = os.environ.get('SECTOR_STAMP') or datetime.datetime.now().strftime('%Y%m%d')

    print('loading BILLSTATUS (title+subject bearing)...')
    recs, shas = [], {}
    for cg in D['congresses']:
        cache, s = extract_congress(cg, D['types'], CACHE_DIR)
        shas['billstatus%d' % cg] = s
        with open(cache, encoding='utf-8') as fh:
            for line in fh: recs.append(json.loads(line))
    print('  %d bills across congresses %s' % (len(recs), list(D['congresses'])))

    summary = {}
    for sector in [s.strip() for s in only if s.strip()]:
        print('\n=== %s ===' % sector)
        R = build(sector, recs, D, rost, n_roster, shas)
        st = R['stats']
        if promote:
            out = os.path.join(MB_ROOT, 'sector_network_%s_%s.xlsx' % (sector, stamp))
        else:
            out = os.path.join(D['out_dir'], 'sector_diagnostic_%s_%s.xlsx' % (sector, stamp))
        rows = write_xlsx(out, R['edges'], promote)
        canon = hashlib.sha256(repr(sorted(
            (a, b, round(e['Weight'], 6)) for (a, b), e in R['edges'].items())).encode()).hexdigest()

        man = {
            'sector': sector,
            'AUDIT_ONLY': not promote,
            'PROMOTED': promote,
            'engine_status': (('PROMOTED to a NET_CONFIG value (Paul 2026-08-18). The engines DO '
                               'run on this graph. It remains measurably below CU on the shipped '
                               'kernel -- see engine_floor -- and the dashboard banner states '
                               'those numbers so the toggle does not imply parity.')
                              if promote else
                              ('NOT a NET_CONFIG value and NOT on the dashboard. Below the engine '
                               'operating floor: see engine_floor.')),
            'built_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
            'dials': {k: v for k, v in D.items() if k != 'out_dir'},
            'universe_method': ('CRS legislativeSubjects term set' if sector == 'bank_reg'
                                else 'display-title keyword screen (DE-001 NEGATIVE screen: '
                                     'candidates, never labels)'),
            'terms': (BANKREG_SUBJECTS if sector == 'bank_reg' else [t for t, _ in CRYPTO_TERMS]),
            'terms_excluded': (BANKREG_EXCLUDED if sector == 'bank_reg' else {}),
            'per_term_bill_counts': R['per_term'],
            'bills_by_congress': R['by_congress'],
            'dropped_by_adjudication': R['dropped'],
            'stats': st,
            'engine_floor': {
                'kernel': 'p = w/(w+6) (moneyball_core:371 with cpf=1, sigma=1) -- UPPER BOUND',
                'this_graph': {'p_max': st['p_max'], 'p_median': st['p_median'],
                               'dyads_p_ge_010': st['dyads_p_ge_010'], 'dyads': st['dyads']},
                'cu_reference': {'p_max': 0.5198, 'p_median': 0.0217, 'dyads_p_ge_010': 252,
                                 'dyads': 2555,
                                 'source': 'congress_cosponsor_network_AUG20260802.xlsx through '
                                           'load_network (roster filter + mean-symmetrize)'},
            },
            'caveats': [
                'STAR construction (sponsor->cosponsor), never cliques. A clique build inflates a '
                '165-cosponsor bill into 13,530 pairs.',
                'Reported dyads are mean-symmetrized to match the CU published convention; '
                'directed_pairs is the native star count.',
                ('Only %d of %d screened bills contribute a tie (%d zero-cosponsor, %d '
                 'sponsor-off-roster). Quoting the screened count alone overstates the base.'
                 % (st['bills_contributing'], st['bills_screened'],
                    st['bills_zero_cosponsor'], st['bills_sponsor_off_roster'])),
                ('role weights ow=%s rw=%s follow the HOLISTIC base, not the CU base (rw=0.5); '
                 'the two shipped families are already on different role-weight scales.'
                 % (D['ow'], D['rw'])),
            ],
            'cu_overlap': cu_overlap(R['edges']),
            'inputs_sha256': shas,
            'output_canonical_rows_sha256': canon,
            'output_rows': rows,
        }
        if sector == 'bank_reg':
            man['caveats'].append(
                'CRS subjects are populated on only 23,221 of 55,195 bills (42.1%). This universe '
                'is bounded by subject-coding coverage, not by the true extent of bank-regulation '
                'legislation.')
        else:
            man['caveats'].append(
                'No CRS subject term exists for crypto (0 of 1,072 terms match), so a title screen '
                'is the only available method. It MISSES adjudicated crypto bills whose titles '
                'carry only a branded act name (BITCOIN/STABLE/FIT21/ECASH/GENIUS); a branded-act '
                'regex was measured and rejected (184 bills, ~50 false positives from Clarity Act).')
            man['caveats'].append(
                'Bill-level only. The 75 GENIUS-Act (S.1582) amendments that make up 74% of the '
                'adjudicated crypto universe are invisible to a bill-level BILLSTATUS screen and '
                'are NOT represented here.')

        man_path = out.replace('.xlsx', '.manifest.json')
        assert_output_name(man_path, promote)   # derived paths get the same guard, not a free pass
        with open(man_path, 'w', encoding='utf-8') as fh:
            json.dump(man, fh, indent=1, sort_keys=True)

        print('  bills screened      %d  (contributing %d; %d zero-cos, %d off-roster sponsor)'
              % (st['bills_screened'], st['bills_contributing'],
                 st['bills_zero_cosponsor'], st['bills_sponsor_off_roster']))
        print('  directed pairs      %d   dyads %d   members %d (%.1f%% of roster)'
              % (st['directed_pairs'], st['dyads'], st['members_touched'], st['member_coverage_pct']))
        print('  engine floor        p_max %.4f (CU 0.5198)   dyads p>=0.10: %d (CU 252)'
              % (st['p_max'], st['dyads_p_ge_010']))
        _ov = man['cu_overlap']
        if 'error' not in _ov:
            print('  vs published CU     %.1f%% of these ties ALREADY in CU; %d novel (%.1f%%); jaccard %.3f'
                  % (_ov['pct_of_sector_already_in_cu'], _ov['novel_dyads'], _ov['pct_novel'], _ov['jaccard']))
        print('  wrote %s (%d rows)' % (os.path.basename(out), rows))
        summary[sector] = st

    print('\nSECTOR DIAGNOSTICS COMPLETE (audit-only; not engine inputs)')
    return summary


if __name__ == '__main__':
    main()
