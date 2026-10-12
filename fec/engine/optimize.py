import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
from moneyball_core import dump_atomic, stance_gate_tiers, STANCE_GATE_BLOCKED   # S2-03: crash-safe artifact write; F1 stance gate (one kernel copy with per_topic_flip)
"""JOINT CU BUDGET OPTIMIZER (lazy greedy with an explicit guarantee scope).
Greedily allocates a CU budget across members to maximize total Linear-Threshold winnability
(sum over topics of expected weighted flips). In a fixed stance state, the objective is separable
by member and every member's equal-cost contribution chunks have diminishing returns. Selecting
the largest next marginal is therefore the exact discrete resource-allocation optimum; lazy
evaluation only accelerates that selection. A Golovin-Krause data-dependent upper bound is retained
as a diagnostic. If a modeled money threshold can change stance state, the engine replans after
that modeled transition but does NOT call the run an observed adaptive policy and does NOT claim
the fixed-state certificate. Members behind the system-level stance gate (overall Skeptic, Highly
Skeptical or Opponent, as in per_topic_flip) receive no allocation.

Usage:  python fec/engine/optimize.py            (default $2.5M, all issues)
        BUDGET=1000000 INC=25000 python fec/engine/optimize.py
        TOPIC=Interchange python fec/engine/optimize.py     (optimize one issue's winnability)
"""
import sys, os, heapq, collections, importlib.util, re
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
B=MB_ROOT
def imp(n,f): s=importlib.util.spec_from_file_location(n,B+r'\fec\engine\%s'%f); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
fm=imp('fm','flip_model.py'); mr=imp('mr','money_response.py')
# Pot = one PAC's budget (Paul 2026-09-30). PMAX $10k = that PAC's per-candidate per-cycle limit ($5k primary +
# $5k general). Headroom is measured above existing CU money because other CU PACs' gifts do not count against
# this PAC's limit (unless affiliated); this PAC's own prior gifts are not tracked.
BUDGET=int(os.environ.get('BUDGET','2500000')); INC=int(os.environ.get('INC','500'))   # TOTAL pot ~$2.5M (user 2026-07-01) -- THE joint allocation across all 17 issues (per-topic tools are per-issue diagnostics, not additive budgets). INC = allocation granularity ($500 = realistic contribution step)
PMAX=int(os.environ.get('PMAX','10000'))   # per-member max above existing CU money = this PAC's FEC per-candidate per-cycle limit (see the 2026-09-30 ruling above)
ONLY=os.environ.get('TOPIC')
CONV_MAXSTEP=int(os.environ.get('CONV_MAXSTEP','1'))   # QME 2026-10-01: read once here (was re-read per shift_pro call; same value)
if CONV_MAXSTEP<0: raise ValueError('CONV_MAXSTEP must be >= 0 (got %d): a negative step would move converted members AWAY from pro'%CONV_MAXSTEP)
if BUDGET<0 or INC<=0 or PMAX<0: raise ValueError('BUDGET/PMAX must be nonnegative and INC must be positive')
if 0<PMAX<INC: raise ValueError('INC (%d) exceeds PMAX (%d): no member could ever receive a full chunk, so the plan would silently fund nothing'%(INC,PMAX))   # QME 2026-10-01: the docstring's own BUDGET=1000000 INC=25000 example hit this with the default PMAX
def scoped_output_path(explicit,default,selector):
    """A TOPIC probe gets its own artifact unless the caller explicitly supplies an output path."""
    if explicit: return explicit
    if not selector: return default
    slug=re.sub(r'[^a-z0-9]+','_',selector.lower()).strip('_') or 'topic'
    stem,ext=os.path.splitext(default)
    return stem+'_topic_'+slug+ext
OUTF=scoped_output_path(os.environ.get('OPT_OUT'),B+r'\optimize_result.json',ONLY)
cats=[c for c in fm.cats if (not ONLY or ONLY.lower() in c.lower())]
if ONLY and not cats: raise ValueError('TOPIC %r matches no category'%ONLY)   # QME 2026-10-01: a typo used to finish with base 0 and an empty plan under a topic-scoped filename
members=fm.members
# F1 SYSTEM-LEVEL STANCE GATE (Paul 2026-09-30, "F1. Yes"): inherit per_topic_flip's gate -- a member whose
# overall tier is Skeptic, Highly Skeptical or Opponent is not a money target merely because some topic cells
# are mild. Same kernel helper and same category_stance.json read as per_topic_flip. Gated members are
# EXCLUDED FROM ALLOCATION (marg()==0) but their flip value still counts in the objective, so `base` stays
# flip_model.flip_value (refflip, the JS engine); only who receives money changes. A member who can receive
# no money has no chunk sequence to certify and cannot be converted by money, so the concavity check and
# conversion_reachable range over fundable members only.
OVT=stance_gate_tiers(fm._CS_DOC,members)
GATED=frozenset(m for m in members if OVT.get(m) in STANCE_GATE_BLOCKED)
# COMPET_MULT (Paul 2026-10-05; dial OFF -> every c exactly 1.0): the competitiveness multiplier scales EFFECTIVE dollars -- c*cu and c*ind in pflip, c*amount
# in shift_lean -- never the per-member PMAX cap, INC or the pot, which stay REAL dollars.
CM=fm.CM
conversion_reachable=any(m not in GATED and mr.OV.get(m) not in mr.COMMITTED and
                         abs(mr.shift_lean(m,'cu',PMAX,CM[m]))>=mr.DTHRESH for m in members)
observations=[]   # reserved for real observed outcomes; modeled threshold crossings are stored in `conversions`
MAT={m:dict(fm.CS0[m]) for m in members}; cu=dict(fm.cu0); ind=dict(fm.ind0)
won=set(); secured=0.0      # H1: members WON via conversion are realized (their flip banked in `secured`) and leave the flippable pool
CTORD=['Champion','Defender','Ally','Highly Persuadable','Persuadable','Lean Persuadable',
       'True Neutral','Lean Skeptic','Skeptic','Highly Skeptical','Opponent']
def marg_at(m,cu_now):                            # fixed-state marginal from one full CU chunk on m
    g=0.0; pm=fm.rc(m,'pressure_mult'); cm=CM[m]
    for c in cats:
        cs,campT,nA,nB,mAt,mBt,tgt=fm.topic_setup(c,MAT)
        if tgt[m]<=0: continue
        g+=tgt[m]*(fm.pflip(cu_now+INC,ind[m],fm.R[m],nA[m],nB[m],mAt,mBt,pm,cm)-fm.pflip(cu_now,ind[m],fm.R[m],nA[m],nB[m],mAt,mBt,pm,cm))
    return g
def marg(m):                                      # current next-chunk marginal
    if m in GATED or m in won or cu[m]+INC>fm.cu0[m]+PMAX: return 0.0   # F1: a stance-gated member is never funded; a won member is realized (no more spend); per-member cap requires room for a FULL chunk (never overshoot the FEC limit when INC does not divide PMAX)
    return marg_at(m,cu[m])
def concavity_violations():
    """Verify the condition that makes equal-chunk greedy exactly optimal in the fixed state."""
    bad=[]
    for m in members:
        if m in GATED: continue                   # F1: never funded, so outside the certificate's scope
        prev=float('inf'); x=fm.cu0[m]; k=0
        while x+INC<=fm.cu0[m]+PMAX:
            g=marg_at(m,x)
            if g>prev+1e-10:
                bad.append({'member':m,'chunk':k+1,'previous':prev,'current':g})
                break
            prev=g; x+=INC; k+=1
    return bad
def total_winnability(): return sum(fm.flip_value(c,MAT,cu,ind) for c in cats)
def unwon_winnability():                          # winnability over members NOT yet won (won members are realized -> in `secured`)
    t=0.0
    for c in cats:
        cs,campT,nA,nB,mAt,mBt,tgt=fm.topic_setup(c,MAT)
        t+=sum(tgt[n]*fm.pflip(cu[n],ind[n],fm.R[n],nA[n],nB[n],mAt,mBt,fm.rc(n,'pressure_mult'),CM[n]) for n in members if n not in won)
    return t
def shift_pro(m,steps):                           # a converted member improves toward pro on their movable positions
    # M2: `steps` is derived from the OVERALL 5-tier spectrum (dlean//DTHRESH) but advances tiers in the
    # finer per-category 8-tier CTORD -- the cross-taxonomy magnitude mapping is unjustified (one coarse
    # step != one fine step). A single money-triggered defection is one disposition nudge; cap the
    # per-category move at 1 fine tier so the magnitude is defensible and robust to DTHRESH/MAXSHIFT env
    # changes (env-tunable via CONV_MAXSTEP for callers who want a larger move).
    steps=min(steps,CONV_MAXSTEP)
    moved=[]
    for c in cats:
        t=MAT[m].get(c)
        if t in CTORD and t not in ('Champion','Defender','Opponent'):
            j=max(0,CTORD.index(t)-steps)
            if CTORD[j]!=t: MAT[m][c]=CTORD[j]; moved.append(c)
    return moved
def dd_bound(val_now):
    # Golovin-Krause DATA-DEPENDENT upper bound (JAIR'11 Lemma 5.3 / A.9), kept as a DIAGNOSTIC: from the current
    # MODELED fixed stance state, NO allocation spending <= BUDGET can gain more than the budget-filling sum of current
    # per-chunk marginals -- by concavity a member's later chunks each gain <= their current next-chunk marginal, and
    # a member can absorb at most their PMAX headroom. Nothing is observed (deterministic model); callers compute it only
    # under the fixed-state certificate (no reachable modeled conversion, verified diminishing returns) and report the
    # tightest of start/end. Greedy is already the exact fixed-state optimum; this bound and the (1-1/e) floor are context.
    cand=sorted(((marg(m),(fm.cu0[m]+PMAX-cu[m])//INC) for m in members if m not in won),reverse=True)
    left=BUDGET//INC; z=0.0
    for g,nch in cand:
        if left<=0 or g<=1e-12: break
        t=min(int(nch),left); z+=t*g; left-=t
    return val_now+z
base=total_winnability(); cur=base
concavity_bad=concavity_violations() if not conversion_reachable else []
fixed_state_concave=not conversion_reachable and not concavity_bad
bound0=dd_bound(base) if fixed_state_concave else None   # the certificate excludes state-changing conversions/non-concave chunk sequences
alloc=collections.Counter(); spent=0; conversions=[]; converted=set(); path=[(0,round(base,3))]
selected_marginals=[]; conv_idx=set()   # conv_idx: indices into selected_marginals of conversion steps
pq=[(-marg(m),m,0) for m in members]; heapq.heapify(pq); step=0
while spent+INC<=BUDGET and pq:
    neg,m,le=heapq.heappop(pq)
    if -neg<=1e-12: break                         # no positive marginal left
    if le==step:
        selected_marginals.append(-neg)
        alloc[m]+=INC; cu[m]+=INC; spent+=INC; cur+=-neg; step+=1
        # MODELED state transition: did cumulative CU cross the deterministic response threshold?
        # This triggers scenario replanning, not a claim that a real-world outcome was observed.
        converted_now=False
        if m not in converted:
            added=cu[m]-fm.cu0[m]; nv=mr.defection(m,mr.shift_lean(m,'cu',added,CM[m]))
            if nv and mr.OV.get(m) not in ('Champion','Opponent'):
                steps=int(abs(mr.shift_lean(m,'cu',added,CM[m]))//mr.DTHRESH)
                contrib=sum(fm.topic_setup(c,MAT)[6][m] for c in cats)   # H1: bank m's flip at certainty (winning is a GAIN, not the old persuadability-loss)
                conv_idx.add(len(selected_marginals)-1)   # QME 2026-10-01: this step's chunk is a conversion step -> dropped from the knee series below
                mv=shift_pro(m,steps); converted.add(m); won.add(m); secured+=contrib; conversions.append((m,added,nv,mv))
                fm._cache.clear(); cur=secured+unwon_winnability()       # re-optimize: m realized into `secured`, others' proximity updated
                pq=[(-marg(x),x,0) for x in members]; heapq.heapify(pq); step=0; converted_now=True
        path.append((spent,round(cur,3)))
        # re-queue m for a further INC (multi-increment greedy: a high-value member accumulates up to PMAX in
        # $500 steps as its marginal stays competitive). Skip if it just converted (pq already rebuilt) or hit cap.
        if not converted_now and m not in won and cu[m]+INC <= fm.cu0[m]+PMAX:
            heapq.heappush(pq,(-marg(m),m,step))
    else:
        heapq.heappush(pq,(-marg(m),m,step))
# diminishing-returns knee (last $ whose selected pre-conversion chunk marginal >= 20% of peak). A min-chunk thin spread
# keeps every marginal near-flat, so a "knee" often does not exist within budget -- report that honestly rather
# than implying the knee sits exactly at the full budget. Conversion jumps are excluded from the marginal series.
sel_all=selected_marginals   # QME 2026-10-01: keep the step-indexed series so the knee $ stays at (step+1)*INC; conversion steps are skipped
selected_marginals=[g for i,g in enumerate(sel_all) if i not in conv_idx]   # diagnostic list only (code now matches the comment above); alloc/path/final unchanged
margs=selected_marginals; peak=max(margs) if margs else 0
knee=0
for i,g in enumerate(sel_all):
    if i not in conv_idx and g>=0.20*peak: knee=(i+1)*INC
knee_found = 0<knee<spent           # a real knee means returns fell off BEFORE the budget was exhausted
top=sorted(alloc.items(),key=lambda kv:-kv[1])
print('JOINT BUDGET OPTIMIZER — budget $%s, INC $%s%s'%(f'{BUDGET:,}',f'{INC:,}',' | issue: '+ONLY if ONLY else ' | all %d issues'%len(cats)))
kmsg=('knee at $%s'%f'{knee:,}') if knee_found else 'no diminishing-returns knee within budget (marginals stay flat -- thin spread)'
print('total winnability %.2f -> %.2f  (+%.2f expected flips)  | %s'%(base,cur,cur-base,kmsg))
certificate_valid=fixed_state_concave and not conversions
data_bound=min(bound0,dd_bound(cur)) if certificate_valid else None
# For this model's fixed state, the objective is separable by member and each legal equal-cost
# chunk sequence is verified non-increasing above. Highest-next-marginal allocation is therefore
# the exact optimum on the INC grid, which is stronger than the general (1-1/e) submodular floor.
opt_bound=cur if certificate_valid else None
worst_case_ratio=1-1/2.718281828459045
bound_ratio=((cur-base)/max(data_bound-base,1e-9)) if certificate_valid else None
certified_gain_ratio=1.0 if certificate_valid else None
if certificate_valid:
    print('Fixed-state certificate: exact $%s-grid optimum %.2f; G-K diagnostic upper bound %.2f (gain ratio %.1f%%); general submodular floor %.1f%%'%(
        f'{INC:,}',opt_bound,data_bound,100*bound_ratio,100*worst_case_ratio))
else:
    reason=('a modeled money-triggered conversion can change the objective state'
            if conversion_reachable or conversions else
            '%d member chunk sequences violate diminishing returns'%len(concavity_bad))
    print('Fixed-state exact certificate: unavailable because %s.'%reason)
print('Stance gate: %d members with overall tier %s excluded from allocation (per_topic_flip BLOCK_OVERALL)'%(
    len(GATED),'/'.join(sorted(STANCE_GATE_BLOCKED))))
print('\nTop CU allocations:')
for m,v in top[:14]: print('   %-24s $%-9s%s'%(m,f'{v:,}','  [CONVERTED->coalition]' if m in converted else ''))
print('\nMODELED THRESHOLD CONVERSIONS (not observed outcomes):')
for m,a,nv,mv in conversions[:12]: print('   %-22s at $%-8s -> %s (improved on %d issues)'%(m,f'{int(a):,}',nv,len(mv)))
if not conversions: print('   (none at this budget)')
optimizer_mode='fixed_state_lazy_greedy' if certificate_valid else 'modeled_threshold_replanning'
guarantee=({'kind':'exact_discrete_separable_concave','certified_gain_ratio':1.0,
            'exact_grid_increment':INC,'exact_upper_bound':round(opt_bound,3),
            'worst_case_submodular_floor':round(1-1/2.718281828459045,6),
            'data_dependent_upper_bound':round(data_bound,3),
            'data_bound_gain_ratio':round(bound_ratio,6),
            'concavity_violations':0,
            'scope':'fixed stance state; separable member objective; verified diminishing returns; uniform full chunks'}
           if certificate_valid else
           {'kind':'none',
            'reason':('modeled state-changing conversions invalidate the fixed-state certificate'
                      if conversion_reachable or conversions else
                      'member chunk marginals are not diminishing'),
            'concavity_violations':len(concavity_bad)})
dials={'BUDGET':BUDGET,'INC':INC,'PMAX':PMAX,'TOPIC':ONLY,'CONV_MAXSTEP':CONV_MAXSTEP,   # QME 2026-10-01: effective model dials (values actually used) so a result file is self-describing
       'HALFF':fm.HALFF,'WM':fm.WM,'WP':fm.WP,'BASE':fm.BASE,'MAXSHIFT':mr.MAXSHIFT,'HALF':mr.HALF,'DTHRESH':mr.DTHRESH,'LOSSFRAME':mr.LOSSFRAME,
       'IMPUTE_IND0':fm.IMPUTE_IND0,'PERS_NONE':fm.MC.PERS_NONE,'RECEPT_LAYER':'1' if fm.MC.recept_layer_on() else '0'}
if fm.MC.compet_on(): dials['COMPET_MULT'],dials['COMPET_SRC_SHA256']=fm.MC.compet_record()   # COMPET_MULT: recorded ONLY when ON (dial OFF leaves this file byte-identical to the pre-dial result)
dump_atomic({'budget':BUDGET,'dials':dials,'spent':spent,'increment':INC,'per_member_cap':PMAX,
             'topic':ONLY,'issues':cats,'objective':'expected weighted flips',
             'stance_gate':{'rule':'per_topic_flip BLOCK_OVERALL via moneyball_core.stance_gate_tiers(category_stance.json)',
                            'blocked_tiers':sorted(STANCE_GATE_BLOCKED),'blocked_members':sorted(GATED),
                            'treatment':'excluded from allocation; flip value still counted in base and final'},
             'general_priority':{
                 'source':'primacy_tier',
                 'levels':{
                     str(key):round(value,6)
                     for key,value in fm.MC.PRIMACY_ENGINE_LEVELS.items()
                 },
             },
             'mode':optimizer_mode,'observations':observations,'guarantee':guarantee,
             'base':base,'final':cur,
             'opt_bound':(round(opt_bound,3) if opt_bound is not None else None),
             'certificate_valid':certificate_valid,'conversion_reachable':conversion_reachable,
             'fixed_state_concave':fixed_state_concave,'concavity_violations':concavity_bad,
             'knee':knee,'knee_found':knee_found,'alloc':dict(alloc),
             'conversions':[(m,a,nv) for m,a,nv,_ in conversions],'path':path,
             'selected_marginals':[round(g,9) for g in selected_marginals]},OUTF)
print('\nwrote %s (frontier + allocation + conversions)'%os.path.basename(OUTF))
