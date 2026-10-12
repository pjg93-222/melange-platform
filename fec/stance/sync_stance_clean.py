# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
import sys
sys.exit('SUPERSEDED (2026-07-02 architecture review): restores a PRISTINE backup OVER the live workbook, wiping every later model edit — use sync_stance_g.py. Remove this guard only if you are certain.')

"""Clean stance sync: restore the pristine pre-edit workbook (PRE-STANCEGUARD), then write ONLY the
Stance Tier column (G) with Excel calculation set to MANUAL so nothing downstream recalculates. Guarantees
the Blended Model (ranks/scores) stays byte-identical to the original; only col G changes."""
import json, io, sys, shutil, glob, os, collections
import win32com.client as win32
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
WB = B + r'\Moneyball Main Version - Blended.xlsx'
PRISTINE = max(glob.glob(B + r'\Moneyball Main Version - Blended.PRE-STANCEGUARD-*.xlsx'), key=os.path.getmtime)
M = json.load(io.open(B + r'\category_stance.json', encoding='utf-8'))['matrix']
TORD = {'Champion':4,'Defender':4,'Ally':2,'Highly Persuadable':1,'True Neutral':0,'Neutral':0,'Lean Skeptic':-1,'Skeptic':-2,'Opponent':-4}
IMP = {'Examination & Supervision':0.75,'CU Capital Requirements':0.25,'NCUA Budget & Insurance':1.0,'Interchange':2.0,
 'Payments':0.75,'Technology & Emerging':0.75,'Fraud Prevention':0.75,'Data Privacy & Cyber':1.0,'Charter Modernization':1.0,
 'Defense CU Issues':0.2,'Field of Membership':1.0,'CDFI Fund':0.1,'CFPB':1.0,'Credit Card Mandates':3.0,'TCPA':0.2,
 'Member Business Lending':0.3,'Mortgage & Housing':0.4,'CU Tax Status':3.0}   # ABB26-anchored (2026-07-01)
def agg(n):
    cm = M.get(n, {}); sw = si = 0.0
    for c, t in cm.items():
        if t in TORD: w = IMP.get(c, 0.5); sw += TORD[t]*w; si += w
    if si == 0: return 'Neutral'
    x = sw/si
    return ('Champion' if x>=2.5 else 'Ally' if x>=1.75 else 'Highly Persuadable' if x>=1.25 else
            'Persuadable' if x>=0.75 else 'Lean Persuadable' if x>=0.25 else 'True Neutral' if x>=-0.25 else
            'Lean Skeptic' if x>=-0.75 else 'Skeptic' if x>=-1.25 else 'Highly Skeptical' if x>=-1.9 else 'Opponent')

print('restoring pristine ->', os.path.basename(PRISTINE))
shutil.copy2(PRISTINE, WB)
MANUAL = -4135
xl = win32.DispatchEx('Excel.Application'); xl.Visible = False; xl.DisplayAlerts = False
try: xl.Calculation = MANUAL
except Exception as e: print('pre-open calc set skipped:', e)
try:
    wb = xl.Workbooks.Open(WB)
    try: xl.Calculation = MANUAL
    except Exception: pass
    ws = wb.Worksheets('Rank & Allocation'); cnt = collections.Counter(); n = 0
    for r in range(3, 536):
        nm = ws.Cells(r, 1).Value
        if not nm: continue
        s = agg(nm); ws.Cells(r, 7).Value = s; cnt[s] += 1; n += 1
    print('wrote %d stance cells (calc=MANUAL, no recalc)' % n)
    print('distribution:', dict(cnt))
    wb.Save(); wb.Close(SaveChanges=True); print('SAVED.')
finally:
    xl.Quit()
