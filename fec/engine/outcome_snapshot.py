# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""PROSPECTIVE OUTCOME TEST — freeze today's receptivity predictions, score them against the future.
(2026-07-04, the A-grade path for the receptivity magnitudes: sensitivity bounded them as
decision-robust; only OUT-OF-SAMPLE OUTCOMES can validate them. This harness makes that a
one-command test the day enough future data exists.)

PRE-REGISTERED HYPOTHESES (locked at freeze time, before any outcome is knowable):
  H1 (seeding, Chen/SNAM):   frozen seed_prob correlates POSITIVELY with NEW cosponsorships adopted
      after the freeze (Spearman over members with panel coverage in both snapshots).
  H2 (pressure, Byrne/F&H):  frozen pressure_mult correlates POSITIVELY with |overall-tier movement|
      after the freeze; strongest among members the user's In-contact log marks engaged (run both
      unconditional and engaged-only when contact data exists).
  BARS (locked): rho >= +.20 PASS · +.10..+.20 DIRECTIONAL · below +.10 the magnitude claims stay
      design choices and the dials deserve a user re-look. Negative with |rho|>=.10 = INDICTED.
  MINIMUM DATA: >= 60 days elapsed AND >= 150 members with any cosponsorship delta, else the scorer
      refuses (underpowered).

Usage:
  python fec/engine/outcome_snapshot.py --freeze            # write outcome_snapshot_<today>.json
  python fec/engine/outcome_snapshot.py --score <snap.json> # compare CURRENT state against a frozen snapshot"""
import datetime, json, sys, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import moneyball_core as MC
from tier_util import legislative_press_tiers
B = MB_ROOT

def state():
    R = json.load(open(B + r'\member_receptivity.json'))['members']
    P = json.load(open(B + r'\behavior_panel.json'))['members']
    CS_DOC = json.load(open(B + r'\category_stance.json'))
    CS = CS_DOC['matrix']; overall_caps=(CS_DOC.get('gate') or {}).get('overall_capped') or {}
    tier,_tier_rank=legislative_press_tiers(list(R))
    out = {}
    for m in R:
        d = R[m]; p = P.get(m, {})
        out[m] = {'seed_prob': d['seed_prob'], 'pressure_mult': d['pressure_mult'],
                  'retiring': bool(d.get('retiring')),
                  'n_cosponsored': p.get('n_cosponsored'), 'cosponsor_degree': p.get('cosponsor_degree'),
                  'overall': MC.overall_display_tier(CS.get(m, {}),tier.get(m,0),m,overall_caps)}
    return out

def spearman(pairs):
    xs = [p[0] for p in pairs]; ys = [p[1] for p in pairs]
    def rank(v):
        s = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0] * len(v); i = 0
        while i < len(s):
            j = i
            while j + 1 < len(s) and v[s[j + 1]] == v[s[i]]: j += 1
            for k in range(i, j + 1): r[s[k]] = (i + j) / 2.0
            i = j + 1
        return r
    rx, ry = rank(xs), rank(ys); n = len(xs)
    mx = sum(rx) / n; my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = (sum((a - mx) ** 2 for a in rx)) ** 0.5; dy = (sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / (dx * dy) if dx and dy else 0.0

TORDN = dict(MC.TORD)

if '--freeze' in sys.argv:
    today = datetime.date.today().isoformat()
    snap = {'frozen': today, 'doc': __doc__, 'members': state()}
    out = B + r'\data\engine\outcome_snapshot_%s.json' % today; _o.makedirs(_o.path.dirname(out), exist_ok=True)   # beside the 2026-07-04 snapshot (reorg moved it); dump_atomic's mkstemp needs the dir
    MC.dump_atomic(snap, out)
    cov = sum(1 for v in snap['members'].values() if v['n_cosponsored'] is not None)
    print('FROZEN %s: %d members (%d with cosponsor baseline). Score after >=60 days:' % (today, len(snap['members']), cov))
    print('  python fec/engine/outcome_snapshot.py --score "%s"' % out)
    sys.exit(0)

if '--score' in sys.argv:
    sp = sys.argv[sys.argv.index('--score') + 1]
    snap = json.load(open(sp)); frozen = snap['members']
    days = (datetime.date.today() - datetime.date.fromisoformat(snap['frozen'])).days
    cur = state()
    dc = []
    for m, f in frozen.items():
        c = cur.get(m)
        if not c or f['n_cosponsored'] is None or c['n_cosponsored'] is None: continue
        dc.append((m, max(0, c['n_cosponsored'] - f['n_cosponsored'])))
    nz = sum(1 for _, d in dc if d > 0)
    print('elapsed %d days | %d members with panel in both | %d with new cosponsorships' % (days, len(dc), nz))
    if days < 60 or nz < 150:
        print('UNDERPOWERED (pre-set minimum: 60 days AND 150 movers) — wait and rerun.'); sys.exit(0)
    h1 = spearman([(frozen[m]['seed_prob'], d) for m, d in dc])
    moved = [(frozen[m]['pressure_mult'], abs(TORDN.get(cur[m]['overall'], 0) - TORDN.get(frozen[m]['overall'], 0)))
             for m in frozen if m in cur]
    h2 = spearman(moved)
    def verdict(r): return ('PASS' if r >= 0.20 else 'DIRECTIONAL' if r >= 0.10 else
                            'INDICTED' if r <= -0.10 else 'NULL — magnitudes stay design choices')
    print('H1 seed_prob -> new cosponsorships : rho %+.3f  (n=%d)  %s' % (h1, len(dc), verdict(h1)))
    print('H2 pressure_mult -> |tier movement|: rho %+.3f  (n=%d)  %s' % (h2, len(moved), verdict(h2)))
    print('(H2 engaged-only variant: filter to In-contact-logged members when that export exists.)')
    sys.exit(0)

print('usage: outcome_snapshot.py [--freeze | --score <snapshot.json>]')
