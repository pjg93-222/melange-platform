import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
from moneyball_core import build_party_map, dump_atomic   # S2-03: crash-safe artifact write
import json,sys,csv,os,collections,statistics,openpyxl
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
B=MB_ROOT
CS=json.load(open(os.path.join(B, 'category_stance.json'))); SCO=CS['scores']; cats=CS['cats']
EL=json.load(open(os.path.join(B, 'data', 'money', 'electoral.json')))
C={m['member']:m for m in json.load(open(os.path.join(B, 'data', 'engine', 'congestion.json')))}; members=list(C.keys())
party=build_party_map([(congN,os.path.join(B, 'votes', 'HS%d_members.csv'%congN)) for congN in (119,118,117)])
prt={m:party.get(C[m].get('bioguide',''),'?') for m in members}
avgP={}
for c in cats:
    for p in ('D','R'):
        vals=[SCO[m][c] for m in members if prt[m]==p and m in SCO]
        avgP[(p,c)]=statistics.mean(vals) if vals else 0.0
ra=openpyxl.load_workbook(os.path.join(MB_ROOT, 'Moneyball Main Version - Blended.xlsx'),data_only=True)['Rank & Allocation']
OV={ra.cell(r,1).value:ra.cell(r,7).value for r in range(3,536) if ra.cell(r,1).value}
PROO={'Champion','Defender','Ally'}
plays={}
for m in members:
    p=prt[m]
    if p not in ('D','R') or m not in SCO: plays[m]={'play':'none','value':0,'comp':0}; continue
    opp='R' if p=='D' else 'D'; ov=OV.get(m,'Neutral')
    comp=round(1-EL.get(m,{}).get('security',0.5),3)
    gap_opp={c:SCO[m][c]-avgP[(opp,c)] for c in cats}     # vs cross-party (general) challenger
    gap_same={c:SCO[m][c]-avgP[(p,c)] for c in cats}       # vs same-party (primary) challenger
    if ov in PROO:                                          # DEFEND a genuine ally vs cross-party loss
        defend=comp*sum(max(0,g) for g in gap_opp.values())/len(cats)
        plays[m]={'play':'defend','route':'incumbent','value':round(defend,3),'comp':comp,'party':p,'overall':ov,'elavg':EL.get(m,{}).get('elavg')}
    elif ov=='Opponent':                                    # CONTEST a genuine adversary
        primary=sum(max(0,-g) for g in gap_same.values())/len(cats)   # non-partisan: same-party improvement (intra-party outlier)
        general=comp*sum(max(0,-g) for g in gap_opp.values())/len(cats) # partisan: cross-party flip (competitive only)
        plays[m]={'play':'contest','route':'primary' if primary>=general else 'general','primary':round(primary,3),'general':round(general,3),
                  'value':round(max(primary,general),3),'comp':comp,'party':p,'overall':ov,'elavg':EL.get(m,{}).get('elavg')}
    else:
        plays[m]={'play':'none','value':0,'comp':comp,'party':p,'overall':ov}
dump_atomic(plays,os.path.join(B, 'data', 'money', 'electoral_plays.json'))
def show(kind,key,n=8):
    xs=[(m,plays[m]) for m in members if plays[m]['play']==kind]
    xs=sorted(xs,key=lambda x:-x[1].get(key,x[1]['value']))[:n]
    return [(m.split()[-1],pl['party'],'v%.2f'%pl.get(key,pl['value']),'c%.2f'%pl['comp']) for m,pl in xs]
rep=[m for m in members if plays[m]['play']=='contest']; dfd=[m for m in members if plays[m]['play']=='defend']
print('CONTEST via PRIMARY (non-partisan: friendlier same-party challenger to a genuine Opponent):')
for x in show('contest','primary'): print('   ',x)
pb=collections.Counter(plays[m]['party'] for m in rep)
print('  contest party balance: D %d / R %d'%(pb['D'],pb['R']))
print('\nDEFEND (back a genuine ally in a contestable seat):')
for x in show('defend','value'): print('   ',x)
db=collections.Counter(plays[m]['party'] for m in dfd)
print('  defend party balance: D %d / R %d'%(db['D'],db['R']))
print('\nWarnock now:',plays['Raphael Warnock']['play'],'| %d contest / %d defend / %d none'%(len(rep),len(dfd),533-len(rep)-len(dfd)))
