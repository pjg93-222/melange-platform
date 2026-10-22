# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""Raw/MAX normalization fix (2026-07-01). Make the pillar percentages SIGNED MAGNITUDE (value / MAX)
instead of PERCENTRANK, so stance-signed negatives (leg-stance opponents, anti-CU press) actually pull the
score DOWN instead of being floored back up by the percentile rank. Sets, on 'Blended Model':
    R (Influence %)   = E/MAX(E)     committee          [CMTEFIX -- re-affirmed]
    I (Press %)       = C/MAX(C)     press              [attribution+sign fix]
Leg pillar (J, P) and demo (M) stay on PERCENTRANK. Raw/MAX was tried on leg (2026-07-01) and REVERTED: the
leg-stance raw is long-tailed (a few allies at +17..+27) so value/MAX squashed the mid-board and floated
disengaged skeptics UP (AOC #344->#191). The press fix already ranks opponents low. COM + full recalc.
Backs up first; idempotent (safe to re-run)."""
import sys, shutil, os, datetime, time
import win32com.client as win32
import roster
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
WB = B + r'\Moneyball Main Version - Blended.xlsx'
FORMS = {'R': '=E3/MAX(%s)' % roster.col_range('E', 'Blended Model'), 'I': '=C3/MAX(%s)' % roster.col_range('C', 'Blended Model')}
bk = WB.replace('.xlsx', '.PRE-RAWMAXNORM-%s.xlsx' % datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
shutil.copy2(WB, bk); print('backup ->', os.path.basename(bk))
xl = win32.DispatchEx('Excel.Application'); xl.Visible = False; xl.DisplayAlerts = False
try:
    wb = xl.Workbooks.Open(WB); ws = wb.Worksheets('Blended Model')
    for c, f in FORMS.items():
        print('  %s3  %-30s -> %s' % (c, ws.Range(c + '3').Formula, f))
        ws.Range(roster.col_range(c, 'Blended Model', absolute=False)).Formula = f
    xl.CalculateFull()
    saved = False
    for a in range(6):
        try: wb.Save(); saved = True; break
        except Exception as e: print('save retry %d: %s' % (a, str(e)[:70])); time.sleep(3)
    wb.Close(SaveChanges=False); print('SAVED' if saved else 'NOT SAVED')
    if not saved: sys.exit(2)
finally:
    xl.Quit()
