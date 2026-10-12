# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""Fix the committee/influence pillar: it was double-percentile-ranked, which flattened a backbencher (committee
score 2.5) up to the 90th percentile alongside chairs. Replace Blended Model col18 (Influence %, column R) with a
normalized RAW committee score = committee_raw / MAX(committee_raw), so leadership/seniority is distinguished from
plain membership. COM + recalc."""
import sys, shutil, os, datetime
import win32com.client as win32
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
WB = B + r'\Moneyball Main Version - Blended.xlsx'
bk = B + r'\backups\Moneyball Main Version - Blended.PRE-CMTEFIX-%s.xlsx' % datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
shutil.copy2(WB, bk); print('backup ->', os.path.basename(bk))
xl = win32.DispatchEx('Excel.Application'); xl.Visible = False; xl.DisplayAlerts = False
try:
    wb = xl.Workbooks.Open(WB); ws = wb.Worksheets('Blended Model')
    print('before col18(R) row3:', ws.Cells(3,18).Formula)
    ws.Range('R3:R535').Formula = '=E3/MAX($E$3:$E$535)'   # Influence% = committee_raw / max (E=col5 Committee raw)
    print('after  col18(R) row3:', ws.Cells(3,18).Formula, '| row4:', ws.Cells(4,18).Formula)
    xl.CalculateFull()
    wb.Save(); wb.Close(SaveChanges=True); print('SAVED + recalculated')
finally:
    xl.Quit()
