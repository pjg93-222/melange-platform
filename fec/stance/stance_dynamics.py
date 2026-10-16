# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT,DRAFT as MB_DRAFT  # centralized root (arch review 2026-07-02)
"""STANCE DYNAMICS — Zhou & Elejalde (2026, Information Processing & Management, TWLGCN) mapped onto
the CU press corpus.  Their contributions we transplant (sans the GNN — our corpus is 2.4k items, not
1.2M tweets; the DESIGN transfers, the deep model does not):

 1. STANCE AS A TRAJECTORY, not a static label: discrete-time windows (theirs: 28-day snapshots over
    referendum campaigns; ours: 13-week quarters over the 2025-26 Congress), with EMBEDDING PERSISTENCE
    -> a member with no press this quarter KEEPS the last observed stance (their silent-user carry).
 2. The SEVEN-DYNAMICS taxonomy of per-transition movement (their Sec 5.2, verbatim thresholds):
    static (|dp| <= median+MAD), O->MO, O->LO, O->S (flip), S->O (flip), S->LS, S->MS.
 3. FLIP CURVES: per-window counts of O->S vs S->O.  Their finding: the two curves MIRROR each other,
    and simultaneous spikes flag external events (their period 7->8 scandal; ours: e.g. a CFPB fight
    window or an interchange markup).
 4. NEIGHBOR CO-MOVEMENT (their Fig. 6 diagonal dominance): connected users shift in the SAME direction.
    We test it on the COSPONSOR graph — an empirical validation of the cascade premise itself.
 5. SILENT-ACTOR INFERENCE (their headline capability): members with no expressed stance get one
    proposed from network neighbors (collaborative filtering).  DOMAIN RULE: stance labels are the
    USER'S; proposals go to stance_imputation_proposals.csv with provenance, the matrix is untouched.
    Where the matrix already HAS a stance we score the imputation against it (their masking validation).
Writes stance_dynamics.json + stance_imputation_proposals.csv."""
import csv, json, sys, collections, datetime, glob, os
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
B=MB_ROOT
from press_stream import load_tone_rows   # historical + audited refresh rows (same merge the production press stack reads)
WIN=int(os.environ.get('SD_WINDOW','91'))    # days/window; their 28d resolved granularity-vs-sparsity for tweets, quarters do for press
rows=load_tone_rows()
def pdate(r):
    d=(r.get('date') or '').strip()
    if not d or d.upper()=='NA': return None
    try:
        if '/' in d: mm,dd,yy=d.split('/'); return datetime.date(int(yy),int(mm),int(dd))   # historical m/d/yyyy
        return datetime.date.fromisoformat(d[:10])                                          # refresh yyyy-mm-dd
    except Exception: return None
def cats_of(r):
    c=(r.get('categories') or '').strip()
    return [p.strip() for p in c.replace('|',';').split(';') if p.strip()]
dated=[(pdate(r),r) for r in rows]; dated=[(d,r) for d,r in dated if d]
d0=min(d for d,_ in dated); d1=max(d for d,_ in dated)
NW=(d1-d0).days//WIN+1
def wix(d): return (d-d0).days//WIN
print('STANCE DYNAMICS: %d dated items, %s..%s, %d windows of %dd'%(len(dated),d0,d1,NW,WIN))
# member x window pooled stance score p in [-1,1] (conf-weighted mean of stance_sign)
obs=collections.defaultdict(lambda:collections.defaultdict(list))
for d,r in dated:
    try: sg=float(r.get('stance_sign') or 0); cf=float(r.get('stance_conf') or 0.5)
    except ValueError: continue
    obs[r['member']][wix(d)].append((sg,cf))
traj={}; observed={}
for m,ws in obs.items():
    p=[None]*NW; ob=[False]*NW; last=None
    for w in range(NW):
        if w in ws:
            num=sum(s*c for s,c in ws[w]); den=sum(c for _,c in ws[w])
            last=num/den if den else 0.0; ob[w]=True
        p[w]=last                                    # persistence: silent windows carry the last state
    traj[m]=p; observed[m]=ob
# seven dynamics over consecutive transitions (their Sec 5.2, MAD static band).  Band computed over
# OBSERVED->OBSERVED transitions only: persistence-carried windows have dp=0 by construction and would
# collapse median+MAD to zero (their users tweet every snapshot; our members publish quarterly at best).
deltas=[abs(traj[m][w+1]-traj[m][w]) for m in traj for w in range(NW-1)
        if observed[m][w] and observed[m][w+1] and traj[m][w] is not None]
med=sorted(deltas)[len(deltas)//2] if deltas else 0
mad=sorted(abs(x-med) for x in deltas)[len(deltas)//2] if deltas else 0
BAND=max(0.05,med+mad)
def dyn(a,b):
    if abs(b-a)<=BAND: return 'static'
    if a<0:  return 'O->MO' if b<a else ('O->LO' if b<0 else 'O->S')
    else:    return 'S->MS' if b>a else ('S->LS' if b>0 else 'S->O')
dyncount=collections.defaultdict(collections.Counter); trans=collections.defaultdict(list)
for m,p in traj.items():
    for w in range(NW-1):
        if p[w] is None or p[w+1] is None: continue
        d=dyn(p[w],p[w+1]); dyncount[w][d]+=1
        if d!='static': trans[w].append((m,1 if p[w+1]>p[w] else -1))
print('static band |dp| <= %.3f (median+MAD, their Eq.)'%BAND)
print('\nFLIP CURVES (their mirror-curve event detector — simultaneous spikes flag external shocks):')
print('%-18s %6s %6s %7s %7s %7s %7s'%('window','O->S','S->O','S->MS','O->MO','S->LS','O->LO'))
flips=[]
for w in range(NW-1):
    c=dyncount[w]; ws=(d0+datetime.timedelta(days=WIN*w)).isoformat()
    flips.append({'window':ws,'O->S':c['O->S'],'S->O':c['S->O'],'S->MS':c['S->MS'],'O->MO':c['O->MO'],
                  'S->LS':c['S->LS'],'O->LO':c['O->LO'],'static':c['static']})
    print('%-18s %6d %6d %7d %7d %7d %7d'%(ws,c['O->S'],c['S->O'],c['S->MS'],c['O->MO'],c['S->LS'],c['O->LO']))
spike=max(flips,key=lambda f:f['O->S']+f['S->O'],default=None)
if spike: print('  peak flip window: %s (%d flips) — candidate external-event window'%(spike['window'],spike['O->S']+spike['S->O']))
# neighbor co-movement on the cosponsor graph (their Fig. 6: diagonal dominance = homophily in temporal movement)
import openpyxl
C={m['member']:m for m in json.load(open(B+r'\data\engine\congestion.json'))}
bio2name={m['bioguide']:m['member'] for m in C.values() if m['bioguide']}
net=max(glob.glob(B+r'\congress_cosponsor_network_*.xlsx'),key=os.path.getmtime)
ne=openpyxl.load_workbook(net,data_only=True)['Edges']; adj=collections.defaultdict(set)
for r2 in range(2,ne.max_row+1):
    a,b=bio2name.get(ne.cell(r2,1).value),bio2name.get(ne.cell(r2,2).value)
    if a in C and b in C and a!=b: adj[a].add(b); adj[b].add(a)
def comovement(neigh,label):
    same=diff=0
    for w,movers in trans.items():
        dirn=dict(movers)
        for m,s in movers:
            for nb in neigh.get(m,()):
                if nb in dirn:
                    if dirn[nb]==s: same+=1
                    else: diff+=1
    tot=same+diff
    print('NEIGHBOR CO-MOVEMENT (%s): same %d / opposite %d -> %.0f%% co-movement%s'
          %(label,same,diff,100*same/max(1,tot),
            ' — diagonal dominance (their Fig. 6) holds' if tot and same/tot>0.55 else
            (' — null: no homophily in temporal movement on this layer' if tot else '')))
    return same,diff
print()
same,diff=comovement(adj,'cosponsor ties')
# their homophily lives on a COMMUNICATION graph; test the analogous layer: HCoI who-talks-about-the-same-things
hc_same=hc_diff=None
try:
    hc=json.load(open(B+r'\data\stance\press_hcoi.json',encoding='utf-8'))
    hadj=collections.defaultdict(set)
    for cm in hc['communities']:
        for x in cm['members']:
            for y in cm['members']:
                if x!=y: hadj[x].add(y)
    hc_same,hc_diff=comovement(hadj,'HCoI same-community')
except Exception as e: print('(HCoI co-movement skipped: %s)'%e)
# silent-member imputation proposals (collaborative filtering over the cosponsor graph; USER ADJUDICATES)
CSJ=json.load(open(B+r'\category_stance.json')); MAT=CSJ['matrix']; cats=CSJ['cats']
TSCORE={'Champion':3,'Defender':2.5,'Ally':2,'Highly Persuadable':1.25,'Persuadable':0.75,
        'Lean Persuadable':0.25,'True Neutral':0,'Lean Skeptic':-0.5,'Skeptic':-1,
        'Highly Skeptical':-2,'Opponent':-3}
def nearest_tier(x):
    return min(TSCORE.items(),key=lambda kv:abs(kv[1]-x))[0]
BP=json.load(open(B+r'\bipartisan.json'))
members=list(C)
props=[]; agree=tot2=0
CONF=float(os.environ.get('SD_CONF','0.75')); MINNB=int(os.environ.get('SD_MINNB','5'))
for m in members:
    nbs=[n for n in adj.get(m,()) if n in MAT]
    if len(nbs)<MINNB: continue
    for cat in cats:
        vals=[TSCORE[MAT[n][cat]] for n in nbs if MAT[n].get(cat) in TSCORE]
        if len(vals)<MINNB: continue
        est=sum(vals)/len(vals); tier=nearest_tier(est)
        cur=MAT.get(m,{}).get(cat)
        if cur in TSCORE:      # masking validation (their protocol): does the network guess match the known label's SIGN?
            if abs(est)>=CONF:
                tot2+=1; agree+=((est>0)==(TSCORE[cur]>0))
        elif cur in (None,'','—','-') and abs(est)>=CONF:   # only confident, well-evidenced proposals
            props.append({'member':m,'issue':cat,'proposed_tier':tier,'est_score':round(est,2),
                          'evidence_neighbors':len(vals),
                          'provenance':'network-imputed (Zhou-Elejalde silent-actor collaborative filtering) — PROPOSAL ONLY, user adjudicates'})
import io
from moneyball_core import dump_atomic
_PROP=B+r'\data\stance\stance_imputation_proposals.csv'
with io.open(_PROP+'.tmp','w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['member','issue','proposed_tier','est_score','evidence_neighbors','provenance'])
    w.writeheader(); w.writerows(props)
os.replace(_PROP+'.tmp',_PROP)
print('\nSILENT-MEMBER PROPOSALS: %d (member,issue) cells with >=3 informative neighbors -> stance_imputation_proposals.csv'%len(props))
print('  masking validation on %d known cells: sign agreement %.0f%% (their kappa-style sanity check)'%(tot2,100*agree/max(1,tot2)))
dump_atomic({'window_days':WIN,'windows':NW,'static_band':round(BAND,4),'flip_curves':flips,
           'co_movement':{'same':same,'opposite':diff},
           'members_tracked':len(traj),'proposals':len(props),
           'masking_validation':{'n':tot2,'sign_agreement':round(agree/max(1,tot2),3)}},
          B+r'\data\stance\stance_dynamics.json',indent=1)
print('wrote stance_dynamics.json')
