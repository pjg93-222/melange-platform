import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
import moneyball_core as MBC
"""END-TO-END SCENARIO RUNNER (Melange Systems).
Apply a scenario -> propagate through the competitive cascade + money curve -> report the change in
CU coalition influence vs the frozen baseline, topic by topic.  Grounded in the research library:
  - Bharathi, Kempe & Salek, "Competitive Influence Maximization" (viral-competitive): the A(pro)/B(anti)
    first-to-reach cascade a shock perturbs is exactly their competing-diffusion model.
  - Golovin & Krause, "Adaptive Submodularity" (guarantee scope): a scenario reports MODELED outcomes (money curve +
    defection threshold, cascade, LT flips); nothing is observed or re-optimized, so it is not an adaptive policy.
    The allocator's scope is stated once, in optimize.py's docstring. The (1-1/e) bound covers the coalition side
    (follower-vs-fixed-opponent IC is submodular per Bharathi-Kempe-Salek); competitive LT (FLIP) has NO general
    guarantee — Borodin et al. prove submodularity can fail there — so it is a heuristic (audit_frontier.py checks only coalition/electoral frontier marginals).
  - Ren et al., "Multi-Objective Path-Based D* Lite": recompute only what the scenario actually touches.
  - money_response.py: saturation/capacity-aware money->lean curve + defection threshold.

Usage (any number of moves):
  python fec/engine/scenario.py --money "Jared Golden:comp:250000" --money "Tim Scott:cu:100000" --stance "X:Interchange:Opponent"
    money  Member:cu|comp:amount   (cu = our spend [helps], comp = competitor flood [hurts])
    defect Member                  (force a money-style defection regardless of threshold)
    stance Member:Issue:Tier       (hand-set a per-category stance)
"""
import json, sys, os, random, importlib.util, argparse
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
B=MB_ROOT
spec=importlib.util.spec_from_file_location('mr',os.path.join(B, 'fec', 'engine', 'money_response.py')); mr=importlib.util.module_from_spec(spec); spec.loader.exec_module(mr)
_fs=importlib.util.spec_from_file_location('fm',os.path.join(B, 'fec', 'engine', 'flip_model.py')); fm=importlib.util.module_from_spec(_fs); _fs.loader.exec_module(fm)   # Linear-Threshold winnability channel
SEED=7; MC=int(os.environ.get('MC','40')); BSCALE=400000
PRO={'Champion','Defender','Ally'}; ANTI={'Opponent','Highly Skeptical','Skeptic'}
CTORD=['Champion','Defender','Ally','Highly Persuadable','Persuadable','Lean Persuadable',
       'True Neutral','Lean Skeptic','Skeptic','Highly Skeptical','Opponent']  # per-category pro->anti
CSJ=json.load(open(os.path.join(B, 'category_stance.json'))); MAT0=CSJ['matrix']; cats=CSJ['cats']; DEFENSIVE=set(CSJ['defensive'])
C={m['member']:m for m in json.load(open(os.path.join(B, 'data', 'engine', 'congestion.json')))}; members=[m['member'] for m in C.values()]
bio2name={m['bioguide']:m['member'] for m in C.values() if m['bioguide']}
bpf=MBC.bpf()
prt=MBC.party()
adj=MBC.load_network()
# N-1 (audit 2026-07-08): the ONE shared cascade kernel. build_P adds the receiver-susceptibility factor
# em(v)=edge_mult the old inline P=(w*cpf)/(w*cpf+6) dropped (diverged up to ~13%/edge from the live kernel).
P=MBC.build_P(adj)
idx={m:i for i,m in enumerate(members)}; NM=len(members)
_sm=MBC.sm64
def cascade(A,Bs,it):
    # H2 fix: TRUE common random numbers -- edge (u,v) at iteration `it` always draws the SAME value, regardless of
    # which seeds/bystanders changed.  So baseline vs scenario differ ONLY by the real perturbation, not RNG desync.
    col={**dict.fromkeys(sorted(A),'A'),**dict.fromkeys(sorted(Bs),'B')}; fr=list(col.items())
    while fr:
        nf=[]
        for u,c in fr:
            iu=idx[u]*NM
            for v,p in P[u].items():
                if v not in col and _sm(it*NM*NM+iu+idx[v])<p: col[v]=c; nf.append((v,c))
        fr=nf
    return sum(1 for c in col.values() if c=='A')
def camp(t): return 'pro' if t in PRO else 'anti' if t in ANTI else 'neu'
def spread(cat,MAT,ind):
    A=[n for n in members if camp(MAT[n][cat])=='pro']; anti=set(n for n in members if camp(MAT[n][cat])=='anti'); tot=0
    for it in range(MC):
        random.seed(1000003*SEED+it)
        bx=[n for n in members if random.random()<min(0.9,(ind[n] or 0)/BSCALE) and camp(MAT[n][cat])=='neu']
        tot+=cascade(A,anti|set(bx),it)
    return tot/MC
# ---- parse + apply scenario ----
ap=argparse.ArgumentParser(); ap.add_argument('--money',action='append',default=[]); ap.add_argument('--defect',action='append',default=[]); ap.add_argument('--stance',action='append',default=[])
a=ap.parse_args()
MAT={m:dict(MAT0[m]) for m in members}; ind0={n:(C[n]['banks'] or 0)+(C[n]['finserv'] or 0) for n in members}; ind=dict(ind0); cu_state={n:(C[n]['cu'] or 0) for n in members}
defections=[]; log=[]
def shift_stance(m,issues,steps,toward_anti):
    moved=[]
    for iss in issues:
        cur=MAT[m].get(iss)
        if cur in CTORD and cur not in ('Champion','Defender','Opponent'):
            i=CTORD.index(cur); j=max(0,min(len(CTORD)-1,i+(steps if toward_anti else -steps)))
            if CTORD[j]!=cur: MAT[m][iss]=CTORD[j]; moved.append(iss)
    return moved
for mv in a.money:
    m,src,amt=mv.split(':'); amt=float(amt)
    if m not in C: log.append('!! unknown member: '+m); continue
    dlean=mr.shift_lean(m,src,amt,fm.CM[m])   # COMPET_MULT: same c as flip_value below (dial OFF -> 1.0)
    if src=='comp': ind[m]=ind.get(m,0)+amt
    elif src=='cu': cu_state[m]=cu_state.get(m,0)+amt
    newov=mr.defection(m,dlean)
    if newov:
        # M2: `steps` from the OVERALL 5-tier spectrum (dlean//DTHRESH) is applied in the finer 8-tier
        # CTORD below -- the cross-taxonomy magnitude is unjustified. A single money-triggered defection is
        # one disposition nudge; cap the per-category move at 1 fine tier (env-tunable via CONV_MAXSTEP).
        steps=min(int(abs(dlean)//mr.DTHRESH),int(os.environ.get('CONV_MAXSTEP','1'))); toward_anti=(src=='comp')
        moved=shift_stance(m,cats,steps,toward_anti)          # a defection is a general disposition shift across the member's movable positions
        defections.append((m,mr.OV.get(m),newov,round(dlean,1),moved))
    log.append('money %s %s $%s -> lean %+.1f%s'%(m,src,f'{int(amt):,}',dlean,' [DEFECTS->%s]'%newov if newov else ' (absorbed, no defection)'))
for m in a.defect:
    if m in C: moved=shift_stance(m,sorted(DEFENSIVE),2,True); defections.append((m,mr.OV.get(m),'forced',None,moved)); log.append('forced defection %s on %s'%(m,moved))
for mv in a.stance:
    m,iss,tier=mv.split(':')
    if m in C and iss in cats: MAT[m][iss]=tier; log.append('stance %s %s -> %s'%(m,iss,tier))
# ---- propagate + report ----
print('================ SCENARIO ================')
for l in log: print('  '+l)
if defections:
    print('\nDEFECTIONS TRIGGERED:')
    for m,old,new,dl,moved in defections: print('   %-22s overall %s->%s (lean %s) | per-issue shifted: %s'%(m,old,new,dl,[i.split(" &")[0] for i in moved] or 'none'))
print('\nCU COALITION INFLUENCE CHANGE (competitive cascade spread, baseline -> scenario):')
tot0=tot1=0; rows=[]
for c in cats:
    s0=spread(c,MAT0,ind0); s1=spread(c,MAT,ind); tot0+=s0; tot1+=s1
    if abs(s1-s0)>=0.3: rows.append((c,s0,s1))
for c,s0,s1 in sorted(rows,key=lambda x:abs(x[2]-x[1]),reverse=True):
    print('   %-28s %6.1f -> %6.1f   (%+.1f)'%(c[:28],s0,s1,s1-s0))
if not rows: print('   (no topic moved by >=0.3 co-sponsors)')
print('\nNET (coalition/mobilization): total CU spread %.1f -> %.1f  (%+.1f co-sponsors)'%(tot0,tot1,tot1-tot0))
print('\nFLIP MODEL — winnability change (Linear-Threshold expected weighted flips, baseline -> scenario):')
ind_flip={n:fm.ind0[n]+(ind[n]-ind0[n]) for n in members}   # use fm's IMPUTED baseline (not raw) + scenario's competitor deltas, so both sides share one footing
f0=f1=0; frows=[]
for c in cats:
    if c not in fm.cat2sc: continue
    a=fm.flip_value(c,MAT0,fm.cu0,fm.ind0); b=fm.flip_value(c,MAT,cu_state,ind_flip); f0+=a; f1+=b
    if abs(b-a)>=0.03: frows.append((c,a,b))
for c,a,b in sorted(frows,key=lambda x:abs(x[2]-x[1]),reverse=True)[:12]:
    print('   %-28s %6.2f -> %6.2f   (%+.2f)'%(c[:28],a,b,b-a))
if not frows: print('   (no topic winnability moved by >=0.03)')
print('\nNET (flip/persuasion): total winnability %.2f -> %.2f  (%+.2f expected flips)'%(f0,f1,f1-f0))
