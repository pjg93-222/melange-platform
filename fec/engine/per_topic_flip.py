import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
import moneyball_core as MC
import json, sys, heapq, os, openpyxl
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
WM,WP=0.35,0.65; CAP=50000; BUDGET=2_000_000; INC=1000; BASE=0.15
if BUDGET<0 or INC<=0: raise ValueError('BUDGET must be nonnegative and INC must be positive')
# per-CATEGORY persuadability (same scale as overall PERS; '—' = no position on this issue)
# M5: '—' (no recorded position on this issue, ~60% of cells) lowered 0.55->0.45, then 0.40 (F-9 2026-10-11), so a silent member sits
# at PARITY with a known 'Ally' rather than ranking ABOVE one (silence is uncertainty, not enthusiasm).
# Kept identical to flip_model.py for grain consistency; env-overridable via PERS_NONE.
from moneyball_core import PERS
PRO={'Champion','Defender','Ally'}; ANTI={'Opponent','Highly Skeptical','Skeptic'}
C={m['member']:m for m in json.load(open(os.path.join(MB_ROOT, 'data', 'engine', 'congestion.json')))}
bio2name={m['bioguide']:m['member'] for m in C.values() if m['bioguide']}
wb=openpyxl.load_workbook(os.path.join(MB_ROOT, 'Moneyball Main Version - Blended.xlsx'),data_only=True)
bm=wb['Blended Model']; ra=wb['Rank & Allocation']; nrm=wb['Legislative Score - Normalized']
members=[m['member'] for m in C.values()]
primacy_tier,primacy_rank,primacy_priority=MC.primacy_engine_state(members)
ind={n:(C[n]['banks'] or 0)+(C[n]['finserv'] or 0) for n in members}
_iv=sorted(v for v in ind.values() if v>0); _imed=_iv[len(_iv)//2] if _iv else 0
# Match flip_model: verified PAC evidence survives missing fundraising totals.
# IMPUTE_IND0=0 disables the existing median fallback for unobserved members.
_imp0=os.environ.get('IMPUTE_IND0','1')!='0'
from engine.money_observation import industry_money_missing
ind={n:(_imed if (_imp0 and industry_money_missing(C[n])) else ind[n]) for n in members}
cu={n:(C[n]['cu'] or 0) for n in members}
HALFF=float(os.environ.get('HALFF','0.10'))   # capacity scale: money == HALFF*receipts -> half max money-pull (matches money_response + flip_model)
_rv=sorted((C[n].get('receipts') or 0) for n in members if (C[n].get('receipts') or 0)>0); _RMED=_rv[len(_rv)//2] if _rv else 200000
Rcap={n:(max((C[n].get('receipts') or 0),200000) if (C[n].get('receipts') or 0)>0 else _RMED) for n in members}   # HIGH-2: receipts=0 = MISSING -> impute median (not floor to $200k, which made no-record members cheapest to convert); matches money_response + flip_model
# PER-CATEGORY stance matrix (the 3rd dimension) -- drives camp/persuadability/proximity per issue
_CS_DOC=json.load(open(os.path.join(MB_ROOT, 'category_stance.json'))); CS=_CS_DOC['matrix']; CS_SCORES=_CS_DOC.get('scores') or {}
OVT=MC.stance_gate_tiers(_CS_DOC,members)
# Match the ratified stance-integrity gate in lt_priming/dashboard prime: a system-level Skeptic,
# Highly Skeptical member, or Opponent is not a money target merely because one topic cell is mild.
# One kernel copy since 2026-09-30, shared with optimize.py: stance_gate_tiers is this file's former
# inline OVT expression moved verbatim, so per_topic_alloc.json is unchanged by construction.
BLOCK_OVERALL=MC.STANCE_GATE_BLOCKED
# network proximity
adj=MC.load_network()
# (moneyscale removed -- dead since the capacity-aware pflip rewrite)
# NOTE (obs-(e) ruling 2026-08-17): pflip proximity binds party-only cpf_pressure (below);
# bridge amplification (cpf_diffusion) belongs to the CASCADE engines only. The prior
# bipartisanship comment described the wrong physics for this file; code unchanged.
prt=MC.party()
bpf=MC.bpf()
DIRECT=('--bridge' in sys.argv) or os.environ.get('BRIDGE')=='1'; GAMMA=1.2   # toggleable mode: directly prioritize bridge-builders in flip targeting
cpf=MC.cpf_pressure
# RECEPTIVITY LAYER (2026-07-02): money pressure scaled by pressure_mult (matches flip_model).
rc=MC.rc
CM={n:MC.cmult(n) for n in members}   # COMPET_MULT (Paul 2026-10-05): c scales EFFECTIVE dollars on BOTH legs; dial OFF -> exactly 1.0, no file read
def pflip(n,al,nA,nB,mAt,mBt):
    pm=rc(n,'pressure_mult')
    a=WM*pm*(CM[n]*(cu[n]+al)+1)/((CM[n]*(cu[n]+al)+1)+HALFF*Rcap[n])+WP*nA[n]/mAt   # capacity-aware saturating CU pull (al = REAL allocated dollars; c*(cu+al) = effective)
    b=WM*pm*(CM[n]*ind[n]+1)/((CM[n]*ind[n]+1)+HALFF*Rcap[n])+WP*nB[n]/mBt        # ... and industry pull (same c)
    return a/(a+b) if a+b>0 else 0
# 18 legislative topic blocks -> the 17 matrix categories (CFPB Restrain+Reach share the combined CFPB stance)
from moneyball_core import blocks,BLOCKCAT
labels={sc:str(nrm.cell(1,sc).value).replace(' - Sponsor','').replace(' Sponsor','').strip() for sc in blocks}
nrow={nrm.cell(r,1).value:r for r in range(3,536) if nrm.cell(r,1).value}
eng={}
for sc in blocks:
    sw=[nrm.cell(2,sc+o).value or 0 for o in (0,2,4)]; d={}
    for n in members:
        r=nrow.get(n); d[n]=sum(sw[i]*(nrm.cell(r,sc+[0,2,4][i]).value or 0) for i in range(3)) if r else 0
    eng[sc]=d
def alloc_topic(sc):
    cat=BLOCKCAT[sc]
    priority={n:MC.blended_engine_priority(
        primacy_tier[n],CS_SCORES.get(n,{}),cat,'flip') for n in members}
    cs={n:CS.get(n,{}).get(cat,'—') for n in members}                       # THIS issue's stance per member
    campT={n:('pro' if cs[n] in PRO else 'anti' if cs[n] in ANTI else 'neu') for n in members}
    nA={n:sum(w*cpf(n,u) for u,w in adj[n].items() if campT[u]=='pro') for n in members}   # party-weighted (cpf_pressure) proximity to THIS issue's champions
    nB={n:sum(w*cpf(n,u) for u,w in adj[n].items() if campT[u]=='anti') for n in members}  # ... and opponents
    mAt=max(nA.values()) or 1; mBt=max(nB.values()) or 1
    tgt={n:(0.0 if OVT.get(n) in BLOCK_OVERALL else
            MC.priority_base_signal(eng[sc][n],priority[n],BASE)*PERS[cs[n]]*
            ((1+GAMMA*bpf[n]) if DIRECT else (1+0.3*bpf[n]))*MC.delivery_mult(n))
         for n in members}  # direct issue engagement + tier/LOO-orientation priority x persuadability x bridge/delivery, with system-level integrity gate
    al={n:0 for n in members}
    def marg(n): return 0.0 if al[n]>=CAP else tgt[n]*(pflip(n,al[n]+INC,nA,nB,mAt,mBt)-pflip(n,al[n],nA,nB,mAt,mBt))
    h=[(-marg(n),n) for n in members if tgt[n]>0]; heapq.heapify(h); spent=0
    while spent+INC<=BUDGET and h:
        g,n=heapq.heappop(h)
        if -g<=0: break
        al[n]+=INC; spent+=INC; heapq.heappush(h,(-marg(n),n))
    return al,spent,cs
def main():   # L3: guarded so importing this module for its functions doesn't run the pipeline / overwrite per_topic_alloc.json
    ALL={}
    print('PER-SUB-CATEGORY FLIP -- driven by PER-CATEGORY stance (topic-specific camp/persuadability/proximity):\n')
    for sc in blocks:
        al,sp,cs=alloc_topic(sc); top=sorted([(n,al[n]) for n in members if al[n]>0],key=lambda x:-x[1])
        ALL[labels[sc]]={n:al[n] for n,a in top}
        nch=sum(1 for n in members if cs[n] in PRO); nop=sum(1 for n in members if cs[n] in ANTI)
        print('%-30s ($%s, %d funded | %d pro/%d anti here): %s'%(labels[sc][:30],f'{sp:,}',len(top),nch,nop,
            ', '.join('%s $%dk'%(n.split()[-1],a//1000) for n,a in top[:4])))
    outf=os.environ.get('PTF_OUT') or (os.path.join(MB_ROOT, 'data', 'lobbying', 'per_topic_alloc_bridge.json') if DIRECT else os.path.join(MB_ROOT, 'per_topic_alloc.json'))   # PTF_OUT: no-clobber redirect for probe/candidate runs (default = today's paths)
    MC.dump_atomic(ALL,outf)
    MC.write_engine_dials(outf)   # R7 (Paul ruling 2026-10-07, D-107): <stem>.dials.json records COMPET_MULT and the c artifact sha (the alloc map cannot carry metadata)
    print('\nwrote %s  (mode: %s, %d sub-categories)'%(os.path.basename(outf),'BRIDGE-BOOST' if DIRECT else 'baseline',len(ALL)))
if __name__=='__main__': main()
