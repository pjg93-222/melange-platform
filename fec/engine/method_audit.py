import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
import moneyball_core as MBC
import json, sys, math, collections, random, statistics, openpyxl
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
random.seed(3)
WM,WP=0.6,0.4; CAP=50000
PRO={'Champion','Ally'}; ANTI={'Opponent','Skeptic'}
C={m['member']:m for m in json.load(open(MB_ROOT+r'\data\engine\congestion.json'))}
bio2name={m['bioguide']:m['member'] for m in C.values() if m['bioguide']}
wb=openpyxl.load_workbook(MB_ROOT+r'\Moneyball Main Version - Blended.xlsx',data_only=True)
bm=wb['Blended Model']; ra=wb['Rank & Allocation']
stance={ra.cell(r,1).value:ra.cell(r,7).value for r in range(3,536) if ra.cell(r,1).value}
rank={bm.cell(r,1).value:bm.cell(r,20).value for r in range(3,536) if bm.cell(r,1).value}
members=[m['member'] for m in C.values()]
ind={n:(C[n]['banks'] or 0)+(C[n]['finserv'] or 0) for n in members}; cu={n:(C[n]['cu'] or 0) for n in members}
st={n:stance.get(n,'Neutral') for n in members}
camp={n:('pro' if st[n] in PRO else 'anti' if st[n] in ANTI else 'neu') for n in members}
adj=MBC.load_network()
nbrA={n:sum(w for u,w in adj[n].items() if camp[u]=='pro') for n in members}
nbrB={n:sum(w for u,w in adj[n].items() if camp[u]=='anti') for n in members}
mA=max(nbrA.values()) or 1; mB=max(nbrB.values()) or 1
moneyscale=max(max(math.sqrt(cu[n]+CAP) for n in members),max(math.sqrt(ind[n]+1) for n in members)) or 1  # FIXED: common scale
def pflip(n,al=0):
    a=WM*math.sqrt(cu[n]+al+1)/moneyscale+WP*nbrA[n]/mA; b=WM*math.sqrt(ind[n]+1)/moneyscale+WP*nbrB[n]/mB
    return a/(a+b) if a+b>0 else 0
order=['Champion','Ally','Neutral','Unengaged','Skeptic','Opponent']
print('=== M1 FLIP CALIBRATION: baseline P_flip by stance (expect MONOTONIC decreasing) ===')
pf=collections.defaultdict(list); rk=collections.defaultdict(list)
for n in members: pf[st[n]].append(pflip(n)); rk[st[n]].append(rank[n] or 999)
for s in order:
    print('   %-10s n=%3d  mean P %.3f   mean rank %3.0f'%(s,len(pf[s]),statistics.mean(pf[s]),statistics.mean(rk[s])))
allp=[pflip(n) for n in members]
mono=all(statistics.mean(pf[order[i]])>=statistics.mean(pf[order[i+1]])-0.02 for i in range(len(order)-1))
print('   monotonic(P): %s | median P %.3f | FIXED common money scale %.1f (was asymmetric 295 vs 422)'%(mono,statistics.median(allp),moneyscale))
# spearman P vs stance ordinal
so={s:i for i,s in enumerate(order)}
xs=[so[st[n]] for n in members]; ys=[pflip(n) for n in members]
def spear(a,b):
    ra_=sorted(range(len(a)),key=lambda i:a[i]); rb=sorted(range(len(b)),key=lambda i:b[i])
    rka=[0]*len(a); rkb=[0]*len(b)
    for i,j in enumerate(ra_): rka[j]=i
    for i,j in enumerate(rb): rkb[j]=i
    n=len(a); d2=sum((rka[i]-rkb[i])**2 for i in range(n)); return 1-6*d2/(n*(n*n-1))
print('   Spearman(P_flip, stance-ordinal) = %.3f (negative = correctly anti-aligned w/ pro->anti order)'%spear(xs,ys))
print('\n=== M2 COALITION (IC) DIAGNOSTIC ===')
P={n:{u:w/(w+4.0) for u,w in adj[n].items()} for n in members}
def cascade(A,B):
    col={**dict.fromkeys(A,'A'),**dict.fromkeys(B,'B')}; fr=list(col.items())
    while fr:
        nf=[]
        for u,c in fr:
            for v,p in P[u].items():
                if v not in col and random.random()<p: col[v]=c; nf.append((v,c))
        fr=nf
    return sum(1 for c in col.values() if c=='A')
Aseed=set(n for n in members if camp[n]=='pro'); anti=[n for n in members if camp[n]=='anti']; BSCALE=150000
def bseeds(): return set(anti)|set(n for n in members if camp[n]=='neu' and random.random()<min(0.9,ind[n]/BSCALE))
runs=[cascade(Aseed,bseeds()) for _ in range(15)]
cv=statistics.pstdev(runs)/statistics.mean(runs)
print('   base A-spread over 15 MC runs: mean %.1f  std %.2f  CV %.3f (stable if <0.03)'%(statistics.mean(runs),statistics.pstdev(runs),cv))
print('   FIXED: congestion now seeds B-pressure (congested neutrals lean opposition, BSCALE=$150k); %d neutrals carry >10%% B-seed prob.'%len([n for n in members if camp[n]=='neu' and ind[n]/BSCALE>0.1]))
print('\n=== M3 CONGESTION CROSS-CHECK vs OpenSecrets "to candidates" (your pasted data) ===')
comm={'American Bankers Assn':(['C00004275'],2917903),'JPMorgan Chase':(['C00104299','C00128512'],6018012),
'Wells Fargo':(['C00034595','C00300178','C00429852'],2978057),'Bank of America':(['C00043489','C00364778'],2694456),
'Citigroup':(['C00008474','C00039305'],1488140)}
pac=collections.defaultdict(float)
for line in open('itpas2.txt',encoding='latin-1'):
    p=line.split('|')
    if len(p)<17 or p[5] not in ('24K','24Z') or (len(p)>19 and p[19]=='X'): continue
    pac[p[0]]+=0 if not p[14] else float(p[14] or 0)
print('   %-22s %12s %14s %8s'%('entity','my PAC $','OpenSecrets $','PAC frac'))
for nm,(ids,osd) in comm.items():
    mine=sum(pac.get(i,0) for i in ids); print('   %-22s %12s %14s   %5.0f%%'%(nm,f'{mine:,.0f}',f'{osd:,}',100*mine/osd))
print('   (trade assoc ABA ~all-PAC -> high frac; corp like JPM has big individual portion -> low frac. Both expected.)')
print('\n=== M4 RANK CALIBRATION already shown above (mean rank by stance, expect Champion low# -> Opponent high#) ===')
