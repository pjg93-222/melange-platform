# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""COOPERATION v2 CANDIDATE (2026-07-09, user-authorized: "De-entangle A criterion").
The shipped cooperation_z is 2/3 raw cosponsorship VOLUME (degree + count), which entangles it with
breadth (+.708, volume common cause) — the documented reason pred_A cannot be diagonal-dominant and
the dependability audit's 'volume proxy' indictment. This CANDIDATE rebuilds cooperation from SHARE
measures only (volume-free by construction), from data already on disk:

  c1_bip     bipartisan.json                cross-party tie share (undirected; already a ratio — the
                                            one component of the old composite that was share-based)
  c2_xshare  bills archive (cosponsor rows) DIRECTIONAL cross-party cosponsorship share: of the bills
                                            m CHOSE to join, what fraction were sponsored by the other
                                            party (sponsor party parsed from the Sponsor column)
  c3_recip   bills archive (both roles)     reciprocity share: of the sponsors m joined (out-partners),
                                            what fraction also joined m's own bills (in-partners) —
                                            the communion signal, volume-free

Composite = mean of z(c1), z(c2) — BOTH REQUIRED (c3 is a distinct anticorrelated facet, reported
never composited; see the pinned-composition comment at the composite block).
CRITERION-SIDE CONSTRUCT WORK ONLY: no instrument (text model) is loaded or paired here; diagnostics
are criterion-vs-criterion and criterion-vs-covariate (the round-2 pre-lock-probe precedent).
Writes coop_v2_candidate.json (consumed by NOTHING until user-ratified promotion)."""
import csv, glob, json, math, os, re, sys, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import openpyxl
B = MB_ROOT
C = {m['member']: m for m in json.load(open(os.path.join(B, 'data', 'engine', 'congestion.json')))}
chamber = {m: (C.get(m) or {}).get('chamber') or '?' for m in C}
import moneyball_core as MC
P = MC.party()

def zscore(vals):
    out = {}
    for ch in ('House', 'Senate'):
        ms = [m for m in vals if chamber.get(m) == ch and vals[m] is not None]
        if len(ms) < 5: continue
        xs = {m: float(vals[m]) for m in ms}
        mu = sum(xs.values()) / len(xs)
        sd = (sum((v - mu) ** 2 for v in xs.values()) / max(1, len(xs) - 1)) ** 0.5 or 1.0
        for m in ms: out[m] = (xs[m] - mu) / sd
    return out

def rr(pairs):
    xs = [a for a, b in pairs]; ys = [b for a, b in pairs]
    n = len(xs)
    if n < 5: return float('nan'), n
    mx, my = sum(xs) / n, sum(ys) / n
    sx = (sum((x - mx) ** 2 for x in xs)) ** .5; sy = (sum((y - my) ** 2 for y in ys)) ** .5
    if sx == 0 or sy == 0: return float('nan'), n
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy), n

def joincorr(d1, d2):
    return rr([(d1[m], d2[m]) for m in d1 if m in d2 and d1[m] is not None and d2[m] is not None])

# ---- parse the bills archive: cosponsor rows (with sponsor party) + bill->sponsor/cosponsor maps ----
bp = max(glob.glob(os.path.join(B, 'congress_bills_archive_*.xlsx')), key=os.path.getmtime)
ws = openpyxl.load_workbook(bp, read_only=True)['All Bills']
rows = ws.iter_rows(min_row=1, values_only=True)
hdr = [str(h or '') for h in next(rows)]
iR, iM, iN, iS, iCg = hdr.index('Role'), hdr.index('Member'), hdr.index('Name'), hdr.index('Sponsor'), hdr.index('Congress')
PARTY_RX = re.compile(r'\[([DRI])-')
bill_sponsor = {}                                  # (congress, bill) -> sponsoring member
bill_cosponsors = collections.defaultdict(set)     # (congress, bill) -> {cosponsoring members}
co_rows = collections.defaultdict(list)            # member -> [(billkey, sponsor_party)] in file order
sp_bills = collections.defaultdict(set)            # member -> {billkeys sponsored}
# CU_IDENTITY_FIX: the archive keys members by NAME (Sen. Menendez's Senate bills -> Rep. M001226);
# drop rows whose bill BILLSTATUS covers without this member's bioguide (shared member_identity check).
from members.member_identity import load_billstatus, archive_row_unverified
_billstatus = load_billstatus(B); identity_rejected = collections.Counter()
for r in rows:
    m = r[iM]
    if m not in C: continue
    if archive_row_unverified(_billstatus, r[iN], r[iCg], C[m].get('bioguide')):
        identity_rejected[m] += 1; continue
    role = str(r[iR] or '').lower(); bk = (r[iCg], str(r[iN] or ''))
    if role.startswith('sponsor'):
        bill_sponsor[bk] = m; sp_bills[m].add(bk)
    elif 'cosponsor' in role:
        mt = PARTY_RX.search(str(r[iS] or ''))
        co_rows[m].append((bk, mt.group(1) if mt else None))
        bill_cosponsors[bk].add(m)
print('identity rows unverified by BILLSTATUS (CU_IDENTITY_FIX): %d' % sum(identity_rejected.values()), dict(identity_rejected))

# ---- components ----
c2 = {}; c2_h1 = {}; c2_h2 = {}                    # cross-party out-share + odd/even halves (SB)
for m, lst in co_rows.items():
    if P.get(m) not in ('D', 'R'): continue
    # sponsor party via the bill->sponsor map (the Sponsor string column is mostly empty on
    # cosponsor rows — 13-member coverage; the map recovers it from the sponsor rows)
    known = [(bk, P.get(bill_sponsor.get(bk))) for bk, sp0 in lst
             if bill_sponsor.get(bk) and bill_sponsor.get(bk) != m and P.get(bill_sponsor.get(bk)) in ('D', 'R')]
    if len(known) < 10: continue
    c2[m] = sum(1 for _, sp in known if sp != P[m]) / len(known)
    h1 = known[0::2]; h2 = known[1::2]
    if len(h1) >= 5 and len(h2) >= 5:
        c2_h1[m] = sum(1 for _, sp in h1 if sp != P[m]) / len(h1)
        c2_h2[m] = sum(1 for _, sp in h2 if sp != P[m]) / len(h2)

c3 = {}; c3_h1 = {}; c3_h2 = {}                    # reciprocity share + partner-hash halves (SB)
for m, lst in co_rows.items():
    outp = {bill_sponsor.get(bk) for bk, _ in lst if bill_sponsor.get(bk) and bill_sponsor.get(bk) != m}
    if len(outp) < 5: continue
    inp = set()
    for bk in sp_bills.get(m, ()): inp |= bill_cosponsors.get(bk, set())
    c3[m] = sum(1 for q in outp if q in inp) / len(outp)
    hA = {q for q in outp if int(__import__('hashlib').md5(q.encode()).hexdigest(), 16) % 2 == 0}; hB = outp - hA   # stable hash: str hash() is salted per process
    if len(hA) >= 3 and len(hB) >= 3:
        c3_h1[m] = sum(1 for q in hA if q in inp) / len(hA)
        c3_h2[m] = sum(1 for q in hB if q in inp) / len(hB)

_bip = os.path.join(B, 'bipartisan.json')
if not os.path.exists(_bip):
    raise FileNotFoundError('required c1 input bipartisan.json is missing; the composite is mean(z(c1),z(c2)) with BOTH required, so a missing c1 must not silently produce an artifact with no cooperation_v2_z')
c1 = {m: v for m, v in json.load(open(_bip)).items() if m in C}

z1, z2, z3 = zscore(c1), zscore(c2), zscore(c3)
# COMPOSITION PINNED (criterion-side evidence + theory, before any instrument pairing):
# composite = mean_z(c1, c2) BOTH REQUIRED — the A-role construct is BRIDGING cooperation (warmth/
# receptivity toward the other side; the corner lexical-A originally validated on). c3_recip is a
# DIFFERENT facet (bonding reciprocity; anticorrelates with both cross-party shares: c1-c3 -.217,
# c2-c3 -.189) — shipped as a separate reported facet, NOT averaged into the composite.
comp = {}
for m in C:
    if m in z1 and m in z2: comp[m] = (z1[m] + z2[m]) / 2.0
if not comp:
    raise ValueError('cooperation_v2 composite is empty (c1 %d members, c2 %d members); refusing to write the ratified A-slot artifact with no cooperation_v2_z' % (len(z1), len(z2)))

# ---- diagnostics (criterion-side only) ----
BD = json.load(open(os.path.join(B, 'behavioral_dispositions.json')))['members']
PANEL = json.load(open(os.path.join(B, 'behavior_panel.json')))['members']
old = {m: d.get('cooperation_z') for m, d in BD.items()}
bre = {m: d.get('breadth_z') for m, d in BD.items()}
dil = {m: d.get('diligence_z') for m, d in BD.items()}
vol = {m: math.log1p(d.get('n_cosponsored') or 0) for m, d in PANEL.items() if d.get('n_cosponsored')}
pty = {m: 1.0 if P.get(m) == 'R' else 0.0 for m in C if P.get(m) in ('D', 'R')}

def sb(h1, h2):
    r, n = joincorr(h1, h2)
    if not (r == r): return float('nan'), n
    return (2 * r) / (1 + r) if r > -1 else float('nan'), n

print('COOPERATION v2 CANDIDATE — coverage: c1_bip %d | c2_xshare %d | c3_recip %d | composite %d'
      % (len(z1), len(z2), len(z3), len(comp)))
for nm, (h1, h2) in (('c2_xshare', (c2_h1, c2_h2)), ('c3_recip', (c3_h1, c3_h2))):
    s, n = sb(h1, h2)
    print('  split-half SB %-10s = %.3f (n=%d)' % (nm, s, n))
for nm, (a, b) in (('c1-c2', (z1, z2)), ('c1-c3', (z1, z3)), ('c2-c3', (z2, z3))):
    r, n = joincorr(a, b)
    print('  component corr %-6s = %+.3f (n=%d)' % (nm, r, n))
print('ENTANGLEMENT (the point of the exercise — old coop vs new):')
for nm, tgt in (('breadth_z', bre), ('diligence_z', dil), ('log n_cosponsored', vol), ('party(R)', pty)):
    ro, no = joincorr(old, tgt); rn, nn = joincorr(comp, tgt)
    print('  vs %-18s old %+.3f (n=%d)  ->  NEW %+.3f (n=%d)' % (nm, ro, no, rn, nn))
r, n = joincorr(comp, old)
print('  new-vs-old cooperation corr = %+.3f (n=%d) — how much the construct moved' % (r, n))

from moneyball_core import dump_atomic
out = {}
for m in C:
    d = {}
    if m in z1: d['c1_bip_z'] = round(z1[m], 3)
    if m in z2: d['c2_xshare_z'] = round(z2[m], 3)
    if m in z3: d['c3_recip_z'] = round(z3[m], 3)
    if m in comp: d['cooperation_v2_z'] = round(comp[m], 3)
    if d: out[m] = d
dump_atomic({'members': out,
             'method': 'share-based cooperation candidate (volume de-entanglement 2026-07-09): '
                       'c1=bipartisan tie share, c2=directional cross-party cosponsorship share '
                       '(>=10 known-party cosponsorships), c3=reciprocity share (>=5 out-partners); '
                       'z within chamber; composite=mean of z(c1),z(c2) BOTH REQUIRED (c3 = distinct '
                       'anticorrelated facet, reported never composited). PROMOTED 2026-07-10 '
                       '(@94f09cf, user-ratified): the engine A-slot (receptivity susc .40 + '
                       'seed .10) and desk radar consume cooperation_v2_z; in-chain since the '
                       '2026-07-10 audit (values refresh every run; composition stays frozen).'},
            os.path.join(B, 'coop_v2_candidate.json'))
print('wrote coop_v2_candidate.json (A-slot criterion values; consumed by receptivity_layer + desk radar)')
