# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""PERSONA GOLD VALIDATOR (2026-07-04, closes the 'no validated external criterion' gap).
Scores data/members/agency_communion.json against the USER'S filled persona_gold_sheet.csv (30 members,
agency/communion rated 1-9 from the blind excerpts). Run any time; prints 'unfilled' until the
sheet has ratings. Pass bar (pre-set here, before scores are known — do not tune after):
  PASS      : Spearman rho >= +0.30 on BOTH axes (n=30 -> rho .30 ~ p<.06 one-tailed)
  DIRECTIONAL: rho >= +0.15 on both (right direction, magnitude thin — keep persona display-only)
  FAIL      : either axis < +0.15 -> the persona channel keeps its 'projected persona, not
              criterion-validated' label and PERSONA_W deserves a user re-look.
Usage: python fec/members/validate_persona_gold.py"""
import csv, io, json, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT

def spearman(xs, ys):
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

rows = list(csv.reader(io.open(B + r'\data\stance\persona_gold_sheet.csv', encoding='utf-8-sig')))
hi = next(i for i, r in enumerate(rows) if r and r[0] == 'member')
hdr = rows[hi]; data = [r for r in rows[hi + 1:] if r and r[0].strip()]
ia, ic = hdr.index('your_agency_1to9'), hdr.index('your_communion_1to9')
filled = [r for r in data if len(r) > max(ia, ic) and r[ia].strip() and r[ic].strip()]
if len(filled) < 10:
    # BLIND-sheet path: the _BLIND variant is the same 30 excerpts in the same row order with
    # names redacted (verified 1:1). Rate THERE to stay blind; this maps ratings back by row order.
    bp = B + r'\data\stance\persona_gold_sheet_BLIND.csv'
    if os.path.exists(bp):
        brows = list(csv.reader(io.open(bp, encoding='utf-8-sig')))
        bhi = next(i for i, r in enumerate(brows) if r and r[0] == 'row_id')
        bhdr = brows[bhi]; bdata = [r for r in brows[bhi + 1:] if r and r[0].strip()]
        ba, bc = bhdr.index('your_agency_1to9'), bhdr.index('your_communion_1to9')
        mapped = []
        for i, br in enumerate(bdata):
            if i < len(data) and len(br) > max(ba, bc) and br[ba].strip() and br[bc].strip():
                nr = list(data[i]) + [''] * (max(ia, ic) + 1 - len(data[i]))
                nr[ia], nr[ic] = br[ba], br[bc]
                mapped.append(nr)
        if len(mapped) >= 10:
            filled = mapped
            print('(ratings read from persona_gold_sheet_BLIND.csv, mapped by row order)')
print('persona gold sheet: %d members, %d rated' % (len(data), len(filled)))
if len(filled) < 10:
    print('UNFILLED (need >=10 rated rows for a stable rho) — rate your_agency_1to9 and '
          'your_communion_1to9 in persona_gold_sheet_BLIND.csv (stay blind!) and rerun.')
    sys.exit(0)
AC = json.load(open(B + r'\data\members\agency_communion.json'))
AC = AC.get('members', AC)
pairs_a, pairs_c, missing = [], [], []
for r in filled:
    m = r[0].strip()
    d = AC.get(m)
    if not d: missing.append(m); continue
    try: ga, gc = float(r[ia]), float(r[ic])
    except ValueError: continue
    pairs_a.append((d['agency'], ga)); pairs_c.append((d['communion'], gc))
if missing: print('  (no persona coverage for: %s)' % ', '.join(missing))
ra = spearman([p[0] for p in pairs_a], [p[1] for p in pairs_a])
rc = spearman([p[0] for p in pairs_c], [p[1] for p in pairs_c])
print('agency   : model vs your rating  Spearman rho = %+.3f  (n=%d)' % (ra, len(pairs_a)))
print('communion: model vs your rating  Spearman rho = %+.3f  (n=%d)' % (rc, len(pairs_c)))
verdict = ('PASS — persona criterion-validated; PERSONA_W=0.30 earns its seat' if min(ra, rc) >= 0.30 else
           'DIRECTIONAL — right sign, thin magnitude; keep persona as display/tilt only' if min(ra, rc) >= 0.15 else
           'FAIL — persona stays projected-style only; discuss PERSONA_W with the user')
print('VERDICT (pre-set bars .30/.15): ' + verdict)
