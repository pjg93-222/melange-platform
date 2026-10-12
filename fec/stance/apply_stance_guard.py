# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
import sys
sys.exit('SUPERSEDED (2026-07-02 architecture review): overwrites col G with the superseded activity-based formula, clobbering per-category tiers — use sync_stance_g.py. Remove this guard only if you are certain.')

"""Root fix: update the Rank & Allocation 'Stance Tier' formula in the master workbook so a member who
actively cosponsors CU bills (Legislative >= ally dial L8) is NOT tagged Opponent/Skeptic on press alone.
Done via Excel COM so Tables/LET formulas are preserved and the workbook recalculates (openpyxl can't).
Backs the workbook up first."""
import win32com.client as win32, shutil, datetime, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
WB = MB_ROOT+r'\Moneyball Main Version - Blended.xlsx'
bak = WB.replace('.xlsx', '.PRE-STANCEGUARD-%s.xlsx' % datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
shutil.copy2(WB, bak); print('backup ->', bak)

# guarded formula (row-3 relative; Excel fills relative refs down the range; $L$ dials stay absolute)
F = ('=IF(OR(D3<=$L$3,AND(C3<=$L$4,D3<$L$8)),"Opponent",'
     'IF(OR(D3<=$L$5,AND(C3<=$L$6,D3<$L$8)),"Skeptic",'
     'IF(D3>=$L$7,"Champion",IF(D3>=$L$8,"Ally",IF(D3>$L$9,"Neutral","Unengaged")))))')

xl = win32.DispatchEx('Excel.Application'); xl.Visible = False; xl.DisplayAlerts = False
try:
    wb = xl.Workbooks.Open(WB)
    ws = wb.Worksheets('Rank & Allocation')
    ws.Range('G3:G535').Formula = F
    xl.CalculateFull()
    import collections
    cnt = collections.Counter(ws.Cells(r, 7).Value for r in range(3, 536) if ws.Cells(r, 1).Value)
    print('Stance Tier counts after guard:', dict(cnt))
    for r in (51, 458, 491):
        print('  %-16s -> %s' % (ws.Cells(r, 1).Value, ws.Cells(r, 7).Value))
    wb.Save(); wb.Close(SaveChanges=True)
    print('SAVED workbook with guarded Stance Tier.')
finally:
    xl.Quit()
