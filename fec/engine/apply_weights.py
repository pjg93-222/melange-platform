# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""Apply the ratified Blended Model score weights (AB2:AB5) and recalc.
Score = AB2*Legislative% + AB3*Press% + AB4*Influence(committee)% + AB5*Demo%.
Press sentiment is active at a deliberately diminished 10% weight."""
import sys, shutil, os, datetime
import win32com.client as win32
import roster
from engine.pillar_policy import PILLAR_WEIGHTS
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
WB = MB_ROOT+r'\Moneyball Main Version - Blended.xlsx'
WEIGHTS = {'AB2_leg': PILLAR_WEIGHTS[2], 'AB3_press': PILLAR_WEIGHTS[3],
           'AB4_committee': PILLAR_WEIGHTS[4], 'AB5_demo': PILLAR_WEIGHTS[5]}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, 'weights must sum to 1'
bk = WB.replace('.xlsx', '.PRE-WEIGHTS-%s.xlsx' % datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
shutil.copy2(WB, bk); print('backup ->', os.path.basename(bk))
xl = win32.DispatchEx('Excel.Application'); xl.Visible = False; xl.DisplayAlerts = False
try:
    wb = xl.Workbooks.Open(WB); ws = wb.Worksheets('Blended Model')
    print('before: Leg=%s Press=%s Cmte=%s Demo=%s' % (ws.Range('AB2').Value, ws.Range('AB3').Value, ws.Range('AB4').Value, ws.Range('AB5').Value))
    ws.Range('AB2').Value = WEIGHTS['AB2_leg']; ws.Range('AB3').Value = WEIGHTS['AB3_press']
    ws.Range('AB4').Value = WEIGHTS['AB4_committee']; ws.Range('AB5').Value = WEIGHTS['AB5_demo']
    ws.Range(roster.col_range('R', 'Blended Model', absolute=False)).Formula = '=E3/MAX(%s)' % roster.col_range('E', 'Blended Model')   # re-assert committee-pillar fix (collision-proof: in same write)
    xl.CalculateFull()
    print('after:  Leg=%s Press=%s Cmte=%s Demo=%s' % (ws.Range('AB2').Value, ws.Range('AB3').Value, ws.Range('AB4').Value, ws.Range('AB5').Value))
    wb.Save(); wb.Close(SaveChanges=True); print('SAVED + recalculated')
finally:
    xl.Quit()
