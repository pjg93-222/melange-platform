import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
import moneyball_core as MBC
import roster
import json, sys, random, os, openpyxl
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
SEED=int(os.environ.get('SEED','7')); random.seed(SEED)   # fixed seed -> reproducible; scenarios use SAME seed so deltas aren't MC noise
BUDGET=2_000_000; CAP=50_000; MC=int(os.environ.get('MC','20')); BSCALE=400000
PRO={'Champion','Defender','Ally'}; ANTI={'Opponent','Highly Skeptical','Skeptic'}
C={m['member']:m for m in json.load(open(os.path.join(MB_ROOT, 'data', 'engine', 'congestion.json')))}
bio2name={m['bioguide']:m['member'] for m in C.values() if m['bioguide']}
wb=openpyxl.load_workbook(os.path.join(MB_ROOT, 'Moneyball Main Version - Blended.xlsx'),data_only=True)
bm=wb['Blended Model']; nrm=wb['Legislative Score - Normalized']
members=[m['member'] for m in C.values()]
primacy_tier,primacy_rank,primacy_priority=MBC.primacy_engine_state(members)
ind={n:(C[n]['banks'] or 0)+(C[n]['finserv'] or 0) for n in members}
# QME 2026-10-01 (fix 2): raw ind=0 for members with NO FEC record read as "no industry bribe pressure" (PB=0), the H3 misread
# per_topic_flip/flip_model/budget_frontier already closed with the R2-ratified median fill. Same rule here: verified PAC zeros survive
# (industry_money_missing), only unobserved members get the positive-median. COAL_IMPUTE=0 (or global IMPUTE_IND0=0) restores raw ind exactly.
_iv=sorted(v for v in ind.values() if v>0); _imed=_iv[len(_iv)//2] if _iv else 0
if os.environ.get('COAL_IMPUTE','1')!='0' and os.environ.get('IMPUTE_IND0','1')!='0':
    from engine.money_observation import industry_money_missing
    ind={n:(_imed if industry_money_missing(C[n]) else ind[n]) for n in members}
_CS_DOC=json.load(open(os.path.join(MB_ROOT, 'category_stance.json'))); CS=_CS_DOC['matrix']; CS_SCORES=_CS_DOC.get('scores') or {}
# QME 2026-10-01 (fix 1): system-level stance-integrity gate (Paul 2026-09-30 "F1. Yes"), same kernel read per_topic_flip uses. A member whose overall tier
# is Skeptic/Highly Skeptical/Opponent is not a coalition money target because one topic cell is Neutral. Gates only RECIPIENTS (cand); anti/pressure
# seeds are untouched. COAL_STANCE_GATE=0 restores the ungated shortlist exactly.
OVT=MBC.stance_gate_tiers(_CS_DOC,members); BLOCK_OVERALL=MBC.STANCE_GATE_BLOCKED; GATE_ON=os.environ.get('COAL_STANCE_GATE','1')!='0'
def stance_pool(names,ovt,blocked,on):   # QME 2026-10-01: pure order-preserving gate filter (unit-tested via ast-lift)
    return [n for n in names if not (on and ovt.get(n) in blocked)]
bpf=MBC.bpf()
BETA_C=0.8   # bipartisan members get a coalition-seed boost (natural cross-aisle bridges)
prt=MBC.party()
cpf=MBC.cpf_diffusion
adj=MBC.load_network()
# RECEPTIVITY LAYER (2026-07-02): receiver susceptibility on edges + contingency-aware seed activation.
rc=MBC.rc
from moneyball_core import gate
P=MBC.build_P(adj)
idx={n:i for i,n in enumerate(members)}; NM=len(members)
_sm=MBC.sm64
# QME 2026-10-01 (fix 3): the splitmix keys below did not depend on SEED, so SEED!=7 reruns shared identical edge/activation draws (only the
# random.random() bx stream moved) and "seed sensitivity" was understated. _SO shifts every key by (SEED-7)*odd-golden-constant. It is EXACTLY 0 when
# SEED==7 (the default), so default keys -- hence every default artifact byte -- are unchanged; no dial needed.
_SO=(SEED-7)*0x9E3779B97F4A7C15
def cascade(Aseeds,Bseeds,it):
    # HIGH-1 fix: edge (u,v) at iteration `it` always draws the SAME value regardless of which seeds changed,
    # so adding a seed no longer desyncs the stream -> marginal isolates the TRUE effect, not RNG noise.
    col={**dict.fromkeys(sorted(Aseeds),'A'),**dict.fromkeys(sorted(Bseeds),'B')}; fr=list(col.items())
    off=it*NM*NM+_SO                  # QME 2026-10-01 +_SO (0 at SEED 7); loop-invariant, hoisted 2026-08-03: identical integer
                                      # key; verified byte-identical on a full re-run
    while fr:
        nf=[]
        for u,c in fr:
            iu=off+idx[u]*NM
            for v,p in P[u].items():
                if v not in col and _sm(iu+idx[v])<p: col[v]=c; nf.append((v,c))
        fr=nf
    return sum(1 for c in col.values() if c=='A')
from moneyball_core import blocks,BLOCKCAT
labels={sc:str(nrm.cell(1,sc).value).replace(' - Sponsor','').replace(' Sponsor','').strip() for sc in blocks}
nrow={nrm.cell(r,1).value:r for r in roster.rows('Legislative Score - Normalized') if nrm.cell(r,1).value}
# row-2 block weights are member-INVARIANT; read once per block instead of once per member
# (2026-08-03; flip_model.py already hoists these. Verified byte-identical output.)
SW={sc:[nrm.cell(2,sc+o).value or 0 for o in (0,2,4)] for sc in blocks}
def eng(sc,n):
    r=nrow.get(n); sw=SW[sc]
    return sum(sw[i]*(nrm.cell(r,sc+[0,2,4][i]).value or 0) for i in range(3)) if r else 0
COAL_WATERFILL=os.environ.get('COAL_WATERFILL','1')!='0'
def coal_waterfill(boost,cap,budget,unit,on=True):
    """QME 2026-10-01 (fix 8): proportional split of `budget` by marginal, per-member cap. Old rule clipped each member at cap and DROPPED the excess, so
    cap-heavy topics spent well under budget (Interchange $1.52M of $2M). Water-fill: members whose proportional share exceeds cap are pinned at cap and
    their excess is re-split over the remaining members pro rata to marginal (iterated until none exceeds). No member over cap => old rule to within one
    `unit` per member (largest-remainder rounding, 2026-10-11); if EVERY member caps, the shortfall is real headroom exhaustion and is left unspent. on=False (COAL_WATERFILL=0) = old clip."""
    tot=sum(boost.values()) or 1; out={}
    if not on: return {n:min(cap,round(budget*v/tot/unit)*unit) for n,v in boost.items()}
    act=dict(boost); rem=budget
    while act:
        t=sum(act.values()) or 1; over=[n for n,v in act.items() if rem*v/t>cap]
        if not over: break
        for n in over: out[n]=cap; rem-=cap; del act[n]
    # 2026-10-11 (audit G2 $38,003,000 > 19 x $2M): per-member round() could sum past budget. Largest remainder instead: floor every
    # share to `unit`, then hand the leftover units to the largest fractional parts (tie -> member id). Sum <= budget; == budget when integral.
    t=sum(act.values()) or 1; x={n:rem*v/t/unit for n,v in act.items()}
    fl={n:int(x[n]+1e-9) for n in x}; left=int(sum(x.values())+1e-9)-sum(fl.values())
    up=set(sorted(x,key=lambda n:(fl[n]-x[n],n))[:max(0,left)])
    for n in x: out[n]=min(cap,(fl[n]+(n in up))*unit)
    return out
ALL={}; summ=[]
for sc in blocks:
    cat=BLOCKCAT[sc]; cs={n:CS.get(n,{}).get(cat,'—') for n in members}
    campT={n:('pro' if cs[n] in PRO else 'anti' if cs[n] in ANTI else 'neu') for n in members}
    priority={n:MBC.blended_engine_priority(
        primacy_tier[n],CS_SCORES.get(n,{}),cat,'coalition') for n in members}
    Aseed=set(n for n in members if campT[n]=='pro'); anti=[n for n in members if campT[n]=='anti']
    # per-member bribe probability is fixed by `ind` and BSCALE, neither of which changes inside
    # spread(). Precomputed 2026-08-03; verified byte-identical. The DRAW ORDER is untouched:
    # random.random() is still the LEFT operand, so it still fires for EVERY member before any
    # filtering -- that ordering is the 5.A CRN fix and must not be disturbed.
    #
    # DO NOT also hoist `set(anti)` out of the loop below. It looks equally inert and it is NOT:
    # replacing `set(anti)|set(bx)` with a pre-built `ANTI|set(bx)` reproducibly changes
    # per_topic_coalition.json -- Interchange base 20 -> 21, allocation $1,516,000 -> $1,393,000.
    # Measured 2026-08-03 across repeated runs and NOT yet explained: the two unions compare
    # equal in content and in iteration order at every call site, and cascade() sorts its seed
    # sets, so the sensitivity is somewhere downstream. Left alone deliberately.
    PB=[min(0.9,ind[n]/BSCALE) for n in members]
    def spread(extra):
        ex=set(extra); tot=0
        for it in range(MC):
            random.seed(1000003*SEED+it)   # CRN: base & every spread({n}) share draws -> marginal isolates the true effect, not noise
            # contingency-aware: lobbied seeds take w.p. personality seed_prob x stance gate (dedicated _sm stream)
            s=Aseed|{n for n in ex if _sm((1<<62)+it*NM+idx[n]+_SO)<gate(n,cs)}
            bx=[n for i,n in enumerate(members) if random.random()<PB[i] and campT[n]=='neu' and n not in s]   # 5.A FIX: roll for EVERY member FIRST (CRN-stable -- base & spread({n}) share draws), THEN filter. The old order gated the draw behind `n not in s`, so adding a seed changed how many draws fired -> stream desync -> MC noise in the marginal.
            tot+=cascade(s,set(anti)|set(bx),it)
        return tot/MC
    ev={n:MBC.priority_base_signal(eng(sc,n),priority[n],0.15)*MBC.delivery_mult(n) for n in members}   # Direct issue engagement + tier/LOO-orientation coalition priority; x say-do delivery discount.
    # Allies are already unconditional A seeds in every spread() call. Evaluating/funding them has
    # exactly zero causal effect and used to consume most of the top-45 shortlist on Ally-rich topics.
    cand=sorted(stance_pool([n for n in members if campT[n]=='neu'],OVT,BLOCK_OVERALL,GATE_ON),key=lambda n:-ev[n])[:45]   # QME 2026-10-01: stance-gated recipients dropped BEFORE the top-45 cut so the shortlist backfills (COAL_STANCE_GATE=0 = old)
    base=spread(set()); mg={n:max(0.0,spread({n})-base) for n in cand}
    pos={n:v for n,v in mg.items() if v>0}
    boost={n:v*(1+BETA_C*bpf.get(n,0.24)) for n,v in pos.items()}   # bridges get a larger seed
    al={n:0 for n in members}
    al.update(coal_waterfill(boost,CAP,BUDGET,1000,COAL_WATERFILL))   # QME 2026-10-01: water-fill the CAP-clipped excess (was dropped); COAL_WATERFILL=0 = old clip
    top=sorted([(n,al[n]) for n in members if al[n]>0],key=lambda x:-x[1])
    ALL[labels[sc]]={n:a for n,a in top}
    # M2 fix 2026-07-02: spread is submodular, so summing INDIVIDUAL marginals overstates the joint gain.
    # Report the honest joint spread of the funded seed set (one extra MC evaluation per topic).
    funded=set(n for n,a in top if a>0)
    joint=spread(funded) if funded else base
    summ.append((labels[sc],sum(al.values()),len(top),base,joint,top[:4]))
    print('%-30s base %.0f -> %.0f (+%.1f joint; naive-additive +%.1f) | $%s, %d seeds: %s'%(labels[sc][:30],base,joint,joint-base,sum(pos.values()),f'{sum(al.values()):,}',len(top),', '.join('%s $%dk'%(n.split()[-1],a//1000) for n,a in top[:4])))
tag='' if (SEED==7 and os.environ.get('MC','20')=='20') else '_seed%d_mc%d'%(SEED,MC)
MBC.dump_atomic(ALL,os.path.join(MB_ROOT, 'data', 'lobbying', 'per_topic_coalition%s.json'%tag))
print('\nwrote per_topic_coalition%s.json (seed %d, MC %d, %d sub-categories)'%(tag,SEED,MC,len(ALL)))
