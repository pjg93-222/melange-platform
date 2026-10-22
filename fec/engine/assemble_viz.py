import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
import moneyball_core as MBC
import roster
import json, sys, math, os, openpyxl
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
WM,WP=0.6,0.4; CAP=50000
PRO={'Champion','Defender','Ally'}; ANTI={'Opponent','Highly Skeptical','Skeptic'}   # user-ratified 2026-07-03: col-G ladder vocabulary (Defender/Highly Skeptical postdate this viz)
C={m['member']:m for m in json.load(open(MB_ROOT+r'\data\engine\congestion.json'))}
bio2name={m['bioguide']:m['member'] for m in C.values() if m['bioguide']}
AL=json.load(open(MB_ROOT+r'\im_allocation.json'))
flip=AL['flip']; coal=AL['coalition']
wb=openpyxl.load_workbook(MB_ROOT+r'\Moneyball Main Version - Blended.xlsx',data_only=True)
ra=wb['Rank & Allocation']
stance={ra.cell(r,1).value:ra.cell(r,7).value for r in roster.rows('Rank & Allocation') if ra.cell(r,1).value}
members=[m['member'] for m in C.values()]
ind={n:(C[n]['banks'] or 0)+(C[n]['finserv'] or 0) for n in members}
cu={n:(C[n]['cu'] or 0) for n in members}
st={n:stance.get(n,'Neutral') for n in members}
camp={n:('pro' if st[n] in PRO else 'anti' if st[n] in ANTI else 'neu') for n in members}
adj=MBC.load_network()
nbrA={n:sum(w for u,w in adj[n].items() if camp[u]=='pro') for n in members}
nbrB={n:sum(w for u,w in adj[n].items() if camp[u]=='anti') for n in members}
mA=max(nbrA.values()) or 1; mB=max(nbrB.values()) or 1
moneyM=max(math.sqrt(cu[n]+CAP) for n in members) or 1; moneyB=max(math.sqrt(ind[n]+1) for n in members) or 1
def pf(n,al):
    a=WM*math.sqrt(cu[n]+al+1)/moneyM+WP*nbrA[n]/mA; b=WM*math.sqrt(ind[n]+1)/moneyB+WP*nbrB[n]/mB
    return a/(a+b) if a+b>0 else 0
rows=[]
for n in members:
    fa=flip.get(n,0); ca=coal.get(n,0)
    if fa<=0 and ca<=0: continue
    rows.append([n,st[n][:4],fa,ca,round(pf(n,0),2),round(pf(n,fa),2),C[n]['net_lean']])
MBC.dump_atomic({'rows':rows,'flipTot':sum(flip.values()),'coalTot':sum(coal.values()),'flipN':len(flip),'coalN':len(coal)},
          MB_ROOT+r'\data\lobbying\imviz.json',separators=(',',':'),ensure_ascii=False)
print('rows',len(rows),'| flip $%s / %d  coal $%s / %d'%(f"{sum(flip.values()):,}",len(flip),f"{sum(coal.values()):,}",len(coal)),'| bytes',os.path.getsize(MB_ROOT+r'\data\lobbying\imviz.json'))
