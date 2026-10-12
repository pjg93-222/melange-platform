# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""HOLISTIC NORMALIZED BASE — volume- and chamber-normalized all-bills graph (Paul-ruled 2026-08-18).

WHY THIS EXISTS. The raw holistic graph made the influence engines measure ACTIVITY rather than
POSITION. Measured on the shipped artifacts before this file was written:

  * Spearman(Katz centrality, raw tie volume) = **+0.845** on holistic vs +0.247 on CU. At 0.845
    the centrality is very nearly a restatement of "how many cosponsorships did this member
    accumulate." The corroborating tell: Eleanor Norton, a NON-VOTING DC delegate, topped
    holistic PageRank. A member who cannot vote leading an influence ranking is prima facie
    evidence the metric is counting paperwork.
  * The eigenvector/Katz family was NOT degenerate in the way one would expect -- its holistic
    distribution is FLATTER than CU's (top-10 share 5.1% vs 10.6%), so localization collapse is
    refuted. The real failure: spectral radius 19.07 forces Katz's attenuation to
    delta = 0.82/rho = 0.043, so walks past one or two hops contribute almost nothing and
    "eigenvector centrality" degenerates toward weighted degree. That is why it tracks volume.
    It is a normalization failure, not a bug.
  * CHAMBER SKEW, the largest single distortion and the one nobody had flagged: Senate-Senate
    ties are ~7.5x heavier than House-House at the median (2.176 vs 0.342 mean; 1.173 vs 0.157
    median), because 100 senators cosponsoring each other produce far denser mutual ties than
    433 representatives. The 1/sqrt(C) size-norm does not touch it. Result: median senator Katz
    ran 2.27x median House Katz purely from pool size.

WHAT WAS MEASURED AND *REJECTED* (recorded so it is not re-proposed): dropping regular
cosponsorship. Regular contributes only **12.3%** of total edge weight at the ruled 1.0/0.2
dials, and original ties actually OUTNUMBER regular (WOriginal 46,408 vs WRegular 32,621). The
volume artifact comes from ORIGINAL cosponsorship, because prolific members are prolific at
both. Dropping regular would be a real change aimed at the wrong target; the 0.2 dial already
does that work.

THE THREE TRANSFORMS, in order (order matters -- see the note at the bottom):

 1. VOLUME NORMALIZATION (association strength / cosine form):
        w1(u->v) = w(u->v) / sqrt(Out_u * In_v)
    the directed analogue of the standard co-occurrence fix. Out_u is u's total sponsor-side
    weight, In_v is v's total cosponsor-side weight, so it divides out BOTH the sponsor's
    prolificacy and the cosponsor's promiscuity. A tie now says "unusually strong FOR THESE TWO"
    rather than "these two are both busy."

 2. CHAMBER NORMALIZATION: divide each edge by the mean w1 of its chamber stratum
    (Senate/Senate, House/House, House/Senate), equalizing the three strata to mean 1. A
    senator's centrality then reflects genuinely stronger ties, not a 100-seat pool.

 3. KERNEL RESCALE: multiply by a single constant k so the median edge weight equals the CU
    graph's median (HOLISTIC_ANCHOR, default 0.1333, measured 2026-08-18). This is what keeps
    `p = w/(w+6)` meaning the same thing across configurations -- steps 1-2 leave weights in
    arbitrary units, and without this the whole graph would collapse to p ~ 0. The effective
    kernel constant is recorded in the manifest so nobody reads a normalized weight against the
    raw "weight 6 ~ 50%" rule of thumb.

DIRECTION IS PRESERVED. The output stays sponsor->cosponsor directed; the loader for
NET_CONFIG=holistic_norm does not symmetrize, so the engines see who recruited whom. Asymmetry is
the point: In_v and Out_u differ, so w1(u->v) != w1(v->u) by construction.

MEASURED AND REVISED 2026-08-18, after the first build of this file failed its own acceptance
test. Recorded because the failure is instructive, not because it is tidy:

  * PER-EDGE VOLUME NORMALIZATION DOES NOT REMOVE A DEGREE ARTIFACT. Dividing each tie by
    sqrt(Out_u*In_v) down-weights every tie a prolific member has -- but they still have 5,000 of
    them, and Katz SUMS over edges. Measured on the symmetrized, chamber-equalized graph:
    association +0.552, row-stochastic +0.477, no volume norm at all +0.604 (CU reference
    +0.247). Row-normalization, the strongest candidate, still left Eleanor Norton -- a
    NON-VOTING DELEGATE -- in the top 2. You cannot divide a degree artifact out one edge at a
    time. HOLISTIC_VOLUME_NORM therefore defaults OFF; it is kept as a dial, not a default.
  * THE FIRST BUILD "PASSED" AT +0.011 BECAUSE THE METRIC WAS BROKEN, not because the graph was
    fixed. Directed + volume-normalized gave rho(P)=0.048 and max/median = 3e27 -- the Katz
    series exploding on a few nodes while the rest sat flat. Its top-8 was alphabetically
    clustered (W-Y surnames), the signature of a ranking with no real spread. An acceptance test
    can pass on a degenerate measurement; check conditioning before believing an improvement.
  * WHAT ACTUALLY WORKS is capping the SUM. A hard cap on each member's total tie mass moves
    Eleanor Norton from #1 to #37 (to #243 at a 1.0x cap) and compresses max/median from 5.95 to
    1.57 while keeping rho(P) at 8.2, where Katz is well conditioned. Katz~volume stays near
    +0.62 and that is EXPECTED: the rank correlation is carried by the broad middle where volume
    and connectedness genuinely coincide on an all-bills graph. The cap fixes the extremes, which
    is where the nonsense lived. Norton's rank and max/median are the honest instruments here;
    the bulk correlation is not.
  * CHAMBER NORMALIZATION equalizes measured TIE STRENGTH (Senate/Senate ties ran ~7.5x heavier
    than House/House purely from a 100- vs 433-seat pool). It deliberately does NOT force the
    centrality OUTCOME to parity: the residual (senators ~0.41x House median Katz) is a real
    structural fact -- the House is a denser cosponsoring body -- and forcing that ratio to 1.0
    would be choosing the answer rather than removing a distortion.

Dials: HOLISTIC_ANCHOR=0.1333   HOLISTIC_CHAMBER_NORM=1   HOLISTIC_VOLUME_NORM=0 (see above)
       HOLISTIC_CAP=1.5  -- per-member total tie mass ceiling, in multiples of the median member's
                            total; 0 disables. Applied to BOTH in- and out-mass.
       HOLISTIC_NBASE_IN (source BASE; default newest holistic_network_BASE_*.xlsx)
       HOLISTIC_NBASE_OUT (output path override)
Import-safe: pure functions at module level, I/O under main()."""
import os, re, json, math, glob, hashlib, collections, datetime, statistics

PRODUCTION_NAME_RE = re.compile(r'^congress_cosponsor_network(?:_AUG)?_?.*\.xlsx$', re.I)


def dials():
    return {'anchor': float(os.environ.get('HOLISTIC_ANCHOR', '0.1333')),
            'chamber': os.environ.get('HOLISTIC_CHAMBER_NORM', '1') == '1',
            'volume': os.environ.get('HOLISTIC_VOLUME_NORM', '0') == '1',
            'cap': float(os.environ.get('HOLISTIC_CAP', '1.5'))}


def assert_output_name(path):
    base = os.path.basename(path)
    if PRODUCTION_NAME_RE.match(base):
        raise ValueError('COR-9: refusing to write a production-family filename: ' + base)
    if not base.startswith('holistic_network_NBASE_'):
        raise ValueError('output must be named holistic_network_NBASE_*: ' + base)
    if not base.lower().endswith('.xlsx'):
        raise ValueError('output must be a .xlsx workbook: ' + base)


def cap_member_mass(edges, mult):
    """Hard ceiling on each member's TOTAL tie mass, in multiples of the median member's total.

    A member whose in- or out-mass exceeds the ceiling has ALL their edges scaled down
    proportionally; members below it are untouched. This is the one transform that reaches the
    SUM rather than the individual term, which is why it is the thing that works -- see the
    measured comparison in the module docstring. mult<=0 disables."""
    if not mult or mult <= 0: return dict(edges), {'applied': False}
    import statistics as _st
    ow = collections.Counter(); iw = collections.Counter()
    for (u, v), w in edges.items(): ow[u] += w; iw[v] += w
    med_i = _st.median([x for x in iw.values() if x > 0] or [1.0])
    med_o = _st.median([x for x in ow.values() if x > 0] or [1.0])
    Ti, To = mult * med_i, mult * med_o
    out = {}; n_in = sum(1 for x in iw.values() if x > Ti); n_out = sum(1 for x in ow.values() if x > To)
    for (u, v), w in edges.items():
        f = 1.0
        if iw[v] > Ti: f *= Ti / iw[v]
        if ow[u] > To: f *= To / ow[u]
        out[(u, v)] = w * f
    return out, {'applied': True, 'mult': mult, 'cap_in': Ti, 'cap_out': To,
                 'members_capped_in': n_in, 'members_capped_out': n_out}


def normalize(edges, chamber, anchor=0.1333, do_volume=False, do_chamber=True, cap=1.5):
    """edges: {(u,v): w} directed. chamber: {member: 'House'|'Senate'}.

    Returns (normalized_edges, report). Pure -- no I/O, so the fixtures can exercise it."""
    out_w = collections.Counter(); in_w = collections.Counter()
    for (u, v), w in edges.items():
        out_w[u] += w; in_w[v] += w
    rep = {'edges': len(edges), 'volume_norm': do_volume, 'chamber_norm': do_chamber}

    if do_volume:
        w1 = {}
        for (u, v), w in edges.items():
            d = math.sqrt(out_w[u] * in_w[v])
            w1[(u, v)] = (w / d) if d > 0 else 0.0
    else:
        w1 = dict(edges)

    if do_chamber:
        strat = collections.defaultdict(list)
        def key(u, v): return tuple(sorted((chamber.get(u, '?'), chamber.get(v, '?'))))
        for (u, v), w in w1.items(): strat[key(u, v)].append(w)
        means = {k: (sum(vs) / len(vs) if vs else 1.0) for k, vs in strat.items()}
        rep['chamber_strata'] = {'/'.join(k): {'n': len(strat[k]), 'mean_before': means[k]} for k in strat}
        w2 = {}
        for (u, v), w in w1.items():
            m = means.get(key(u, v)) or 1.0
            w2[(u, v)] = w / m if m > 0 else 0.0
    else:
        w2 = w1

    w2, caprep = cap_member_mass(w2, cap)
    rep['cap'] = caprep

    vals = [w for w in w2.values() if w > 0]
    med = statistics.median(vals) if vals else 1.0
    k = (anchor / med) if med > 0 else 1.0
    w3 = {e: w * k for e, w in w2.items()}
    rep.update({'median_before_rescale': med, 'rescale_k': k, 'anchor': anchor,
                'effective_kernel_constant': 6.0,
                'median_after': statistics.median([w for w in w3.values() if w > 0]) if vals else 0.0})
    return w3, rep


def main():
    import openpyxl
    d = dials()
    src = os.environ.get('HOLISTIC_NBASE_IN')
    if not src:
        cands = glob.glob(os.path.join(MB_ROOT, 'holistic_network_BASE_*.xlsx'))
        if not cands:
            raise SystemExit('no holistic_network_BASE_*.xlsx at %s -- run build_holistic_base.py first' % MB_ROOT)
        src = max(cands, key=os.path.getmtime)
    out = os.environ.get('HOLISTIC_NBASE_OUT',
                         os.path.join(MB_ROOT, 'holistic_network_NBASE_%s.xlsx'
                                      % datetime.date.today().strftime('%Y%m%d')))
    assert_output_name(out)
    print('NORMALIZED HOLISTIC BASE | src=%s' % os.path.basename(src))
    print('  dials: volume_norm=%s chamber_norm=%s anchor=%.4f' % (d['volume'], d['chamber'], d['anchor']))

    C = json.load(open(os.path.join(MB_ROOT, 'data', 'engine', 'congestion.json'), encoding='utf-8'))
    bio2cham = {m['bioguide']: (m.get('chamber') or '?') for m in C if m.get('bioguide')}
    bio2name = {m['bioguide']: m['member'] for m in C if m.get('bioguide')}

    ws = openpyxl.load_workbook(src, read_only=True, data_only=True)['Edges']
    edges = {}; meta = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r or not r[0] or not r[1] or not isinstance(r[2], (int, float)): continue
        edges[(r[0], r[1])] = r[2]
        meta[(r[0], r[1])] = (r[3], r[4], r[5], r[8], r[9])   # NBills,NOrig,NReg,WOriginal,WRegular
    print('  read %d directed edges' % len(edges))

    norm, rep = normalize(edges, bio2cham, d['anchor'], d['volume'], d['chamber'], d['cap'])

    wb = openpyxl.Workbook(); e1 = wb.active; e1.title = 'Edges'
    e1.append(['From', 'To', 'Weight', 'NBills', 'NOriginal', 'NRegular',
               'From_label', 'To_label', 'WOriginal', 'WRegular', 'WeightRaw'])
    for (a, b), w in sorted(norm.items()):
        nb, no, nr, wo, wr = meta[(a, b)]
        e1.append([a, b, round(w, 8), nb, no, nr, bio2name.get(a, a), bio2name.get(b, b),
                   wo, wr, edges[(a, b)]])
    pr = wb.create_sheet('Provenance')
    pr.append(['source_bioguide', 'target_bioguide', 'weight', 'layer'])
    for (a, b), w in sorted(norm.items()): pr.append([a, b, round(w, 8), 'cosponsor'])
    pr.append([]); pr.append(['built', datetime.datetime.now().isoformat(), 'base', os.path.basename(src)])
    pr.append(['policy', 'volume-normalized w/sqrt(Out_u*In_v); chamber strata equalized; '
                         'rescaled to median %.4f (effective kernel constant 6/k)' % d['anchor']])
    wb.save(out)

    man = {'built_utc': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
           'config': 'holistic_norm', 'vintage': 'NBASE', 'source': os.path.basename(src),
           'source_sha256_16': hashlib.sha256(open(src, 'rb').read()).hexdigest()[:16],
           'dials': d, 'normalization': rep, 'directed': True,
           'scope': {'cosponsorship': 'all-bills, volume- and chamber-normalized'},
           'reading_note': 'weights are NORMALIZED. The raw "weight 6 ~ 50% activation" rule of '
                           'thumb does NOT apply directly; the rescale constant k is recorded above '
                           'and the median weight is pinned to the CU anchor so p-values stay '
                           'comparable across configurations.'}
    json.dump(man, open(out.replace('.xlsx', '.manifest.json'), 'w', encoding='utf-8'), indent=1)
    print('  chamber strata (mean before equalizing): %s'
          % {k: round(v['mean_before'], 6) for k, v in (rep.get('chamber_strata') or {}).items()})
    _cp = rep.get('cap') or {}
    if _cp.get('applied'):
        print('  cap %.2fx median: %d members capped on in-mass, %d on out-mass'
              % (_cp['mult'], _cp['members_capped_in'], _cp['members_capped_out']))
    print('  median before rescale %.6g -> k=%.4g -> median after %.4f'
          % (rep['median_before_rescale'], rep['rescale_k'], rep['median_after']))
    print('wrote %s + manifest' % os.path.basename(out))


if __name__ == '__main__':
    main()
