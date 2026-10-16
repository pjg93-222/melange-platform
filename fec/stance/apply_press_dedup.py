# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT,DRAFT as MB_DRAFT  # centralized root (arch review 2026-07-02)
"""Apply the press-pillar de-dup: rebuild 'Press Release - Raw' EXCLUDING the 1310 billstatus bill-cosponsorship
rows (which double-count against the legislative pillar), using the EXACT run_stage5 aggregation (recency-weighted
mean of per-release press_value, categories split on ';'). Writes via Excel COM so the Blended Model formulas
recalc -> ranks update. Backs up the workbook first."""
import csv, collections, sys, shutil, os, datetime, unicodedata, re
# RETIRED GUARD (mirrors apply_press_overrides.py, Durbin-canary incident 2026-07-16): the COM re-flow below rebuilds
# 'Press Release - Raw' from Draft Systems' cu_press_stance_tone.csv, which is FROZEN at 2026-06-30. It ignores the
# media blend, the stack labels and the press_stance_overrides ledger, so running it flattens the live blended press cells.
if os.environ.get('APPLY_PRESS_DEDUP_LEGACY') != '1':
    raise SystemExit('RETIRED RE-FLOW: this script\'s workbook rewrite uses the frozen 2026-06-30 press basis '
                     'and corrupts the live press pillar (Durbin canary incident 2026-07-16).\n'
                     'The LIVE press producer is fec/stance/build_media_blend.py (with press_stance_stack.py and the '
                     'press_stance_overrides.json ledger), run by the chain. Set APPLY_PRESS_DEDUP_LEGACY=1 '
                     'only if you truly intend the legacy COM re-flow.')
import win32com.client as win32
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
WB = B + r'\Moneyball Main Version - Blended.xlsx'
ST = MB_DRAFT+r'\cu_press_stance_tone.csv'
def nfc(s): return unicodedata.normalize('NFC', str(s)) if s is not None else s
def num(x):
    try: return float(x)
    except (TypeError, ValueError): return 0.0
def _dom(u):
    m = re.search(r'https?://([^/]+)', str(u)); return m.group(1).replace('www.', '').lower() if m else ''
def _own(r):   # ATTRIBUTION FIX (2026-07-01): a member's press = only their OWN releases
    d = _dom(r.get('url', '')); return d.endswith('senate.gov') or d.endswith('house.gov')

# ---- de-dup'd by-category (member, category) -> recency-weighted mean of REAL, OWN-DOMAIN press only ----
rows = list(csv.DictReader(open(ST, encoding='utf-8-sig', newline='')))
acc = collections.defaultdict(lambda: [0.0, 0.0])
nbill = 0; ntp = 0
for r in rows:
    if str(r.get('url', '')).startswith('billstatus:') or r.get('stance_src') == 'bill':
        nbill += 1; continue
    if not _own(r):   # drop third-party (americascreditunions.org etc.): the lobby's pro-CU stance was being
        ntp += 1; continue   # mis-attributed to the members its articles merely mention (e.g. Marshall)
    pv = num(r.get('press_value')); rw = num(r.get('recency_weight')) or 1.0
    m = nfc(r.get('member'))
    for c in str(r.get('categories', '')).split(';'):
        c = c.strip()
        if not c: continue
        a = acc[(m, c)]; a[0] += pv * rw; a[1] += rw
dedup = {k: round(v[0] / v[1], 6) for k, v in acc.items() if v[1]}
print('excluded %d billstatus + %d third-party rows | de-dup by-category cells (own-domain press only): %d' % (nbill, ntp, len(dedup)))

# ---- backup ----
bk = WB.replace('.xlsx', '.PRE-PRESSDEDUP-%s.xlsx' % datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
shutil.copy2(WB, bk); print('backup ->', os.path.basename(bk))

# ---- COM write + full recalc ----
xl = win32.DispatchEx('Excel.Application'); xl.Visible = False; xl.DisplayAlerts = False
try:
    wb = xl.Workbooks.Open(WB)
    ws = wb.Worksheets('Press Release - Raw')
    data = ws.UsedRange.Value                       # one COM read: tuple of row-tuples (0-indexed here)
    hdr = data[0]
    catcols = {hdr[c]: c for c in range(len(hdr)) if hdr[c] and hdr[c] not in ('member', 'chamber', 'state', 'party')}
    print('category columns:', len(catcols))
    changed = 0
    for ri in range(1, len(data)):
        mname = data[ri][0]
        if not mname: continue
        mn = nfc(mname)
        for cat, ci in catcols.items():
            newv = dedup.get((mn, cat), 0.0)
            cur = data[ri][ci]; cur = cur if isinstance(cur, (int, float)) else 0.0
            if abs(cur - newv) > 1e-9:
                ws.Cells(ri + 1, ci + 1).Value = newv; changed += 1
    print('cells updated: %d' % changed)
    xl.CalculateFull()
    wb.Save(); wb.Close(SaveChanges=True); print('SAVED + recalculated (Blended Model ranks updated)')
finally:
    xl.Quit()
