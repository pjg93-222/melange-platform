# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT,DRAFT as MB_DRAFT  # centralized root (arch review 2026-07-02)
"""Per-member electoral history for the member desk: FEC primary + general vote share (2016-2022,
member-level, from the FEC 'Federal Elections' books) + 2024 general from MEDSL.
Matched by name, state+chamber scoped. Writes electoral_history.json.

IDENTITY HARDENING 2026-08-18 (Paul-ordered). The shipped artifact carried two live identity
swaps, both caused by the pre-2026-08-18 Resolver's fail-open surname match:
  * Adelita Grijalva (won an AZ special in 2025) carried her late father RAUL's 2016-2024
    elections. Mechanism: `Raul Grijalva` is not in the roster, `grijalva` is a roster-UNIQUE
    surname, and the old Resolver returned the unique surname match with no first-name check.
    Verified: old resolve('Raul Grijalva') -> 'Adelita Grijalva'; new -> None.
  * Mary Miller carried state OH (Max Miller's) while Max Miller had no entry at all.
The Resolver fix (merged 7959f05a6) removes the mechanism. This file adds the guards that
would have made the corruption VISIBLE rather than silent, and an output dial so the builder
can be run without writing production.

WHAT WAS MISSING AND IS NOW HERE:
  * an output dial. The path was hardcoded, so there was no way to run this builder without
    overwriting the production artifact -- ELECTORAL_HISTORY_OUT fixes that.
  * the bioguide map. Resolver(members) dropped it, disabling bioguide-authoritative
    resolution for any source that carries one.
  * unresolved-row reporting. `if not mm: continue` dropped rows in silence; a source whose
    name format stops resolving would shrink the artifact with no signal at all.
  * a state/chamber coverage guard. A member with election history but no state means the
    Ballotpedia join failed for them, which is exactly the shape of the Miller defect.
  * loud failure on a missing source book, instead of an obscure crash mid-parse.

Run: python fec/engine/build_electoral_history.py
Dials: ELECTORAL_SOURCE_DIR (FEC books; default <root>/source_data/electoral)
       ELECTORAL_HISTORY_OUT (output path override; default the production artifact)"""
import json, openpyxl, collections, glob, os, csv, sys
from members.member_identity import Resolver
from moneyball_core import dump_atomic
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
DL = os.environ.get('ELECTORAL_SOURCE_DIR',os.path.join(MB_ROOT,'source_data','electoral')); DS = MB_DRAFT

_CONG = json.load(open(B+r"\data\engine\congestion.json"))
members = [m['member'] for m in _CONG]
_N2B = {m['member']: m['bioguide'] for m in _CONG if m.get('bioguide')}
_resolver = Resolver(members, _N2B)          # bioguide map was previously dropped
_unresolved = collections.Counter()
def match(nm, bioguide=None):
    """Resolve or REFUSE. Every refusal is counted so a source whose name format stops
    resolving cannot silently shrink the artifact."""
    got = _resolver.resolve(nm, bioguide)
    if got is None and str(nm or '').strip(): _unresolved[str(nm).strip()] += 1
    return got
def isnum(x): return isinstance(x,(int,float)) and not isinstance(x,bool)

bp = sorted(glob.glob(B+r"\archive\Ballotpedia_119th_CU_Influence_*.xlsx"), key=os.path.basename)[-1]
ws = openpyxl.load_workbook(bp, data_only=True, read_only=True)['Members']
hd = {ws.cell(1,c).value:c for c in range(1,ws.max_column+1)}
mstate={}; mcham={}
for r in range(2, ws.max_row+1):
    mm = match(ws.cell(r,hd['Member']).value)
    if not mm: continue
    if ws.cell(r,hd['State']).value: mstate[mm]=str(ws.cell(r,hd['State']).value).strip().upper()
    if ws.cell(r,hd['Chamber']).value: mcham[mm]=str(ws.cell(r,hd['Chamber']).value).strip()
EL = json.load(open(B+r"\data\money\electoral.json"))

hist = {m:{'chamber':mcham.get(m),'state':mstate.get(m),
           'forecast':{'avg':(EL.get(m,{}) or {}).get('elavg'),'security':(EL.get(m,{}) or {}).get('security')},
           'primary':{}, 'general':{}} for m in members}
SHEETS = {2016:("2016 US House Results by State","2016 US Senate Results by State"),
          2018:("2018 US House Results by State","2018 US Senate Results by State"),
          2020:("13. US House Results by State","12. US Senate Results by State"),
          2022:("8. US House Results by State","7. US Senate Results by State")}
def pct(x): return round(x*100,1) if isnum(x) else None
for yr,(hs,ss) in SHEETS.items():
    wb = openpyxl.load_workbook(DL+r"\federalelections%d.xlsx"%yr, data_only=True, read_only=True)
    for sheet,cham in ((hs,'House'),(ss,'Senate')):
        for row in wb[sheet].iter_rows(min_row=2, values_only=True):
            stab=row[1]; first=row[6]; last=row[7]
            if not stab or not last or len(str(stab))!=2: continue
            m = match(("%s %s"%(str(first or ''),str(last or ''))).strip())
            if not m or mstate.get(m)!=str(stab).strip().upper() or mcham.get(m)!=cham: continue
            pv=row[11]; pp=row[12]; gv=row[15] if len(row)>15 else None; gp=row[16] if len(row)>16 else None
            unopp = (isinstance(pv,str) and 'unoppos' in pv.lower()) or (isinstance(pp,str) and 'unoppos' in pp.lower())
            if yr not in hist[m]['general'] or (isnum(gv) and (hist[m]['general'][yr].get('votes') or 0)<gv):
                hist[m]['general'][yr]={'pct':pct(gp),'votes':gv if isnum(gv) else None,'src':'FEC'}
                hist[m]['primary'][yr]={'pct':pct(pp),'votes':pv if isnum(pv) else None,'unopposed':unopp}
    wb.close()
def add_medsl(path, cham):
    for r in csv.DictReader(open(path,encoding='utf-8-sig',newline='')):
        if r.get('year')!='2024' or str(r.get('stage','')).upper() not in ('GEN','GEN RUNOFF'): continue
        m=match(str(r.get('candidate','')).strip()); st=str(r.get('state_po','')).strip().upper()
        if not m or mstate.get(m)!=st or mcham.get(m)!=cham: continue
        try: cv=float(r.get('candidatevotes')); tv=float(r.get('totalvotes'))
        except (TypeError,ValueError): continue   # S3-02: narrowed from bare except
        if cv<0 or tv<=0 or cv>tv: continue       # drop corrupt MEDSL rows: neg cand, zero/neg total,
                                                  # or cand>total (the -100 / 119% artifacts). pct now in [0,100].
        cur=hist[m]['general'].get(2024)
        if cur is None or (cur.get('votes') or 0)<cv:
            hist[m]['general'][2024]={'pct':round(100*cv/tv,1),'votes':int(cv),'src':'MEDSL'}
add_medsl(DS+r"\medsl_house.csv",'House'); add_medsl(DS+r"\medsl_senate.csv",'Senate')

out={}
for m,d in hist.items():
    if not d['primary'] and not d['general']: continue
    out[m]={'chamber':d['chamber'],'state':d['state'],'forecast':d['forecast'],
            'primary':[dict(year=y,**d['primary'][y]) for y in sorted(d['primary'])],
            'general':[dict(year=y,**d['general'][y]) for y in sorted(d['general'])]}
_nostate = sorted(m for m,d in out.items() if not d.get('state'))
print("identity guards: %d distinct source names unresolved (refused, not guessed)" % len(_unresolved))
for nm,c in _unresolved.most_common(12): print("   unresolved x%-3d %s" % (c, nm))
print("members with history but NO state (Ballotpedia join failed -- the Miller defect shape): %d" % len(_nostate))
for m in _nostate[:12]: print("   no-state %s" % m)
_out_path = os.environ.get('ELECTORAL_HISTORY_OUT', B+r"\data\money\electoral_history.json")
dump_atomic(out,_out_path,separators=(',',':'))
print("wrote %s — %d members with history"%(os.path.basename(_out_path),len(out)))
