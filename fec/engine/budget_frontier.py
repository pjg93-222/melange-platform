import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
import moneyball_core as MBC
import json, sys, random, os, openpyxl, heapq, re
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
B=MB_ROOT
SEED=int(os.environ.get('SEED','7')); random.seed(SEED)
MC=int(os.environ.get('MC','20')); CAP=50_000; BSCALE=400000; MAXSEEDS=int(os.environ.get('MAXSEEDS','60'))
LEVON=os.environ.get('LEV','1')!='0'   # electoral leverage on (competitive seats cheaper to move)
ONLY=os.environ.get('TOPIC')           # optional single-topic filter (substring) for fast validation
def scoped_output_path(explicit,default,selector):
    """A TOPIC probe gets its own artifact unless the caller explicitly supplies an output path."""
    if explicit: return explicit
    if not selector: return default
    slug=re.sub(r'[^a-z0-9]+','_',selector.lower()).strip('_') or 'topic'
    stem,ext=os.path.splitext(default)
    return stem+'_topic_'+slug+ext
PRO={'Champion','Defender','Ally'}; ANTI={'Opponent','Highly Skeptical','Skeptic'}
C={m['member']:m for m in json.load(open(os.environ.get('CONGEST',os.path.join(B, 'data', 'engine', 'congestion.json'))))}   # scenario can override money state
bio2name={m['bioguide']:m['member'] for m in C.values() if m['bioguide']}
wb=openpyxl.load_workbook(os.path.join(MB_ROOT, 'Moneyball Main Version - Blended.xlsx'),data_only=True)
bm=wb['Blended Model']; nrm=wb['Legislative Score - Normalized']
members=[m['member'] for m in C.values()]
primacy_tier,primacy_rank,primacy_priority=MBC.primacy_engine_state(members)
ind={n:(C[n]['banks'] or 0)+(C[n]['finserv'] or 0) for n in members}
# Match flip_model: preserve verified PAC observations; only missing evidence gets
# the existing median industry-money estimate for bystander activation.
_iv=sorted(v for v in ind.values() if v>0); _imed=_iv[len(_iv)//2] if _iv else 0
from engine.money_observation import industry_money_missing
if os.environ.get('IMPUTE_IND0','1')!='0':
    ind={n:(_imed if industry_money_missing(C[n]) else ind[n]) for n in members}
_CS_DOC=json.load(open(os.environ.get('CATSTANCE',os.path.join(B, 'category_stance.json'))))   # scenario can override stance state
CS=_CS_DOC['matrix']; CS_SCORES=_CS_DOC.get('scores') or {}
# QME 2026-10-01 (fix 1): stance-integrity gate (Paul 2026-09-30 "F1. Yes"), same kernel read as per_topic_flip. A member whose overall tier is Skeptic/
# Highly Skeptical/Opponent is not seeded as a CU target (neutral coalition cand, defend pool) merely because one topic cell is Neutral/at-risk-ally.
# Gates RECIPIENTS only: the `anti` pressure set and contest plays (repl: money goes to the CHALLENGER, the gated incumbent is the opponent being
# replaced) are untouched; at-risk allies stay in the held-draw model (atrisk) so base spread is unchanged -- only the defend POOL drops them.
# FRONTIER_STANCE_GATE=0 restores the ungated pools exactly.
OVT=MBC.stance_gate_tiers(_CS_DOC,members); BLOCK_OVERALL=MBC.STANCE_GATE_BLOCKED; GATE_ON=os.environ.get('FRONTIER_STANCE_GATE','1')!='0'
def stance_pool(names,ovt,blocked,on):   # QME 2026-10-01: pure order-preserving gate filter (unit-tested via ast-lift)
    return [n for n in names if not (on and ovt.get(n) in blocked)]
# QME 2026-10-01 (fix 4): FRONTIER_CRN=1 draws the at-risk `held` rolls from splitmix in a private key space instead of random.random(), which in electoral
# mode consumed stream positions BEFORE the bx rolls and shifted them vs coalition mode (no longer a common-random-number pair). FRONTIER_CRN=0 = old.
CRN=os.environ.get('FRONTIER_CRN','1')!='0'
# QME 2026-10-01 (fix 5): coalition scores candidates x delivery_mult (per_topic_coalition.py:78); the frontier ranked the same candidates without it.
# FRONTIER_DELIVERY=0 = old (undiscounted) ranking.
DELIV=os.environ.get('FRONTIER_DELIVERY','1')!='0'
# QME 2026-10-01 (fix 7): knee/peak from the TRUE un-clamped path (clamped display curve hides MC dips so its marginals were biased up/zeroed). 0 = old.
KNEE_RAW=os.environ.get('FRONTIER_KNEE_RAW','1')!='0'
def frontier_knee(path,raw=True):
    """Inclusive quarter-peak rule on the stored one-decimal spread path, in exact tenths."""
    if len(path)<2: return 0,False
    key='spread_raw' if raw else 'spread'
    ticks=[int(round(p[key]*10)) for p in path]   # recover the already-rounded decimal lattice before subtraction
    gains=[ticks[i]-ticks[i-1] for i in range(1,len(ticks))]
    peak=max(gains); knee=0
    for point,gain in zip(path[1:],gains):
        if 4*gain>=peak: knee=point['b']
    return knee,0<knee<path[-1]['b']
bpf=MBC.bpf()
EL=json.load(open(os.path.join(B, 'data', 'money', 'electoral.json'))); lev={m:(EL.get(m,{}).get('leverage',1.0) if LEVON else 1.0) for m in members}
# COMPET_MULT (Paul 2026-10-05): when ON the Ballotpedia leverage factor is REPLACED by the Cook-PVI competitiveness multiplier c, same position in `fac`
# (REPFAC*lev / lev below). Dial OFF leaves lev exactly as above; LEV=0 leaves every factor at 1.0 in either position. Bystander activation min(.9,ind/BSCALE) is untouched (Paul ruling 2026-10-07, D-108: bystanders stay unscaled); build_electoral.py is untouched.
# R6 (Paul ruling 2026-10-07, D-107): LEV=0 forces the leverage factor to 1 in EITHER dial position (lev is already all 1.0 above).
LEV_SRC=('none' if not LEVON else 'competitiveness_c' if MBC.compet_on() else 'ballotpedia_lev')
lev=({m:MBC.cmult(m) for m in members} if LEV_SRC=='competitiveness_c' else lev)
ELECTORAL=os.environ.get('ELECTORAL','0')=='1' or '--electoral' in sys.argv   # toggleable mode: add contest plays to the frontier
REPFAC=float(os.environ.get('REPFAC','0.6'))                                   # primary-win feasibility discount on contest plays
EP=json.load(open(os.path.join(B, 'data', 'money', 'electoral_plays.json')))
prt=MBC.party()
cpf=MBC.cpf_diffusion
adj=MBC.load_network()
# RECEPTIVITY LAYER (2026-07-02): edge_mult = receiver susceptibility (Byrne); seed_prob = contingency-
# aware activation (Chen UAI'21 / SNAM behaviour-aware IM: a lobbied seed takes with probability, it is
# not a guaranteed activation). Neutral fallback = legacy behavior.
rc=MBC.rc
from moneyball_core import gate
P=MBC.build_P(adj)
idx={n:i for i,n in enumerate(members)}; NM=len(members)
_sm=MBC.sm64
# QME 2026-10-01 (fix 3): the splitmix keys (edge + seed-activation) did not depend on SEED, so SEED!=7 reruns reused identical draws and understated seed
# variance. _SO shifts every key by (SEED-7)*odd-golden-constant: EXACTLY 0 when SEED==7 (the default) => default keys and artifacts byte-identical; no dial.
_SO=(SEED-7)*0x9E3779B97F4A7C15
def cascade(A,Bs,it):
    # HIGH-1 fix: edge-keyed CRN (see scenario.py) -- adding a seed no longer desyncs the draw stream,
    # so a marginal spread(S+c)-spread(S) isolates the true effect instead of MC noise.
    col={**dict.fromkeys(sorted(A),'A'),**dict.fromkeys(sorted(Bs),'B')}; fr=list(col.items())
    while fr:
        nf=[]
        for u,c in fr:
            iu=idx[u]*NM
            for v,p in P[u].items():
                if v not in col and _sm(it*NM*NM+iu+idx[v]+_SO)<p: col[v]=c; nf.append((v,c))
        fr=nf
    return sum(1 for c in col.values() if c=='A')
from moneyball_core import blocks,BLOCKCAT
labels={sc:str(nrm.cell(1,sc).value).replace(' - Sponsor','').replace(' Sponsor','').strip() for sc in blocks}
nrow={nrm.cell(r,1).value:r for r in range(3,536) if nrm.cell(r,1).value}
# H2 fix 2026-07-02: the M3 block merge (CFPB Restrain 99 + CFPB Reach 145 -> one CFPB engagement signal)
# was applied to flip_model but never here — the CFPB coalition-candidate ranking ran on half the signal.
from moneyball_core import CATBLOCKS
def eng(sc,n):
    r=nrow.get(n)
    if not r: return 0
    return sum((nrm.cell(2,blk+o).value or 0)*(nrm.cell(r,blk+o).value or 0) for blk in CATBLOCKS[sc] for o in (0,2,4))
frontier={}
for sc in blocks:
    if sc in (145,): continue
    lab=labels[sc]
    if ONLY and ONLY.lower() not in lab.lower(): continue
    cat=BLOCKCAT[sc]; cs={n:CS.get(n,{}).get(cat,'—') for n in members}
    campT={n:('pro' if cs[n] in PRO else 'anti' if cs[n] in ANTI else 'neu') for n in members}
    priority={n:MBC.blended_engine_priority(
        primacy_tier[n],CS_SCORES.get(n,{}),cat,'coalition') for n in members}
    Aseed=set(n for n in members if campT[n]=='pro'); anti=set(n for n in members if campT[n]=='anti')
    comp={n:1-EL.get(n,{}).get('security',0.5) for n in members}
    atrisk=sorted([n for n in Aseed if ELECTORAL and EP.get(n,{}).get('play')=='defend' and comp[n]>=0.4])  # competitive allies that can be insured
    Asafe=Aseed-set(atrisk); risk={n:comp[n] for n in atrisk}
    def spread(extra,defended=()):
        defended=set(defended); ex=set(extra); tot=0
        for it in range(MC):
            random.seed(1000003*SEED+it)
            held=set()
            for m in atrisk:
                r=_sm((1<<61)+it*NM+idx[m]+_SO) if CRN else random.random()   # QME 2026-10-01 fix 4: private splitmix key space (2^61, below activation 2^62, above edge keys) keeps the bx random.random() stream identical across modes; FRONTIER_CRN=0 = old
                if m in defended or r<(1-risk[m]): held.add(m)   # always draw (CRN); undefended at-risk ally holds only w.p. 1-risk
            # CONTINGENCY-AWARE seeding: a lobbied seed activates only w.p. personality seed_prob x
            # stance gate (dedicated CRN stream, offset past the edge keys — no draw collisions).
            activated={n for n in ex if _sm((1<<62)+it*NM+idx[n]+_SO)<gate(n,cs)}
            # A failed contest activation remains an opponent. The old anti-ex subtraction granted the
            # conversion even when the seed gate failed, inflating electoral frontier marginals.
            aa=anti-activated
            s=Asafe|held|activated
            bx=[n for n in members if random.random()<min(0.9,ind[n]/BSCALE) and campT[n]=='neu' and n not in s]  # roll for every member (CRN-stable), then filter
            tot+=cascade(s,aa|set(bx),it)
        return tot/MC
    ev={n:MBC.priority_base_signal(eng(sc,n),priority[n],0.15)*(MBC.delivery_mult(n) if DELIV else 1.0) for n in members}   # QME 2026-10-01 fix 5: x say-do delivery (FRONTIER_DELIVERY=0 = old); *1.0 is exact for the old path
    cand=sorted(stance_pool([n for n in members if campT[n]=='neu'],OVT,BLOCK_OVERALL,GATE_ON),key=lambda n:-ev[n])[:45]   # QME 2026-10-01 fix 1: gate BEFORE the top-45 cut   # coalition = activate Neutrals; Allies already in pro core / defend pool
    # the inner `ELECTORAL and` was inert: the trailing `if ELECTORAL else []` already decides
    # whether the comprehension runs at all, so the test could never be False when reached
    # (dropped 2026-08-03; provably a no-op in both modes)
    repl=[n for n in members if EP.get(n,{}).get('play')=='contest' and campT[n]=='anti'] if ELECTORAL else []
    rset=set(repl); dset=set(atrisk); pool=cand+repl+stance_pool(atrisk,OVT,BLOCK_OVERALL,GATE_ON)   # QME 2026-10-01 fix 1: gated at-risk allies leave the defend POOL only (dset/atrisk still drive held draws)
    fac={n:(REPFAC*lev[n] if n in rset else 1.0 if n in dset else lev[n]) for n in pool}
    # marg() already evaluates spread() at the post-add state. When a candidate is POPPED with a
    # current-iteration marginal (liter==it below) that state is exactly the state we are about to
    # commit to, so the `cur=spread(S,D)` that followed the add was recomputing a value marg() had
    # just produced — one full MC=20 cascade sweep per accepted seed, thrown away and redone.
    # `_sp` carries it across instead. max(0.0,...) and *fac[c] make the marginal non-invertible,
    # which is why the raw spread is stashed rather than reconstructed from the priority. Cached
    # only for the (candidate, iteration) actually evaluated, so a stale state can never be read.
    # (2026-08-03; verified byte-identical on a full re-run of both modes.)
    _sp={}
    def marg(c,S,D,cur):
        sp=spread(S,D|{c}) if c in dset else spread(S|{c},D)
        _sp[c]=sp
        return max(0.0,sp-cur)*fac[c]
    base=spread(set(),set()); cur=base; prev=base; S=set(); D=set(); path=[{'b':0,'spread':round(base,1),'spread_raw':round(base,1),'seed':None}]
    pq=[]
    for c in pool: heapq.heappush(pq,(-marg(c,set(),set(),base),c,0))
    it=0
    while pq and (len(S)+len(D))<MAXSEEDS:
        negeff,c,liter=heapq.heappop(pq)
        if liter==it:
            if -negeff<=1e-12: break
            if c in dset: D.add(c); typ='defend'
            else: S.add(c); typ='contest' if c in rset else 'coalition'
            it+=1; cur=_sp[c]; prev=max(prev,cur)        # == spread(S,D) at this exact state, from marg() above (see _sp); display curve clamped non-decreasing (dips are MC noise); cur stays true for marginals
            path.append({'b':(len(S)+len(D))*CAP,'spread':round(prev,1),'spread_raw':round(cur,1),'seed':c,'type':typ})   # QME 2026-10-01 fix 7: spread_raw = true un-clamped spread; 'spread' (clamped) stays the display curve
        else:
            heapq.heappush(pq,(-marg(c,S,D,cur),c,it))
    # diminishing-returns knee: last seed whose marginal >= 25% of the best marginal
    knee,knee_found=frontier_knee(path,KNEE_RAW)   # exact tenths: a stored 0.3 gain qualifies at a 1.2 peak
    nrep=sum(1 for p in path[1:] if p.get('type')=='contest'); ndef=sum(1 for p in path[1:] if p.get('type')=='defend')
    frontier[lab]={'base':round(base,1),'core_seeds':len(Aseed),'knee':knee,'knee_found':knee_found,
                   'contest_seeds':nrep,'defend_seeds':ndef,'path':path}
    print('%-26s core %3d (base %.0f) -> +%d (%dR %dD) to %.0f | knee $%dk | first: %s'%(
        lab[:26],len(Aseed),base,len(S)+len(D),nrep,ndef,cur,knee//1000,', '.join(p['seed'].split()[-1]+{'contest':'*','defend':'^'}.get(p.get('type'),'') for p in path[1:4])))
defend_ov=sorted([m for m in members if EP.get(m,{}).get('play')=='defend'],key=lambda m:-EP[m]['value'])[:20]
out={'mode':'electoral' if ELECTORAL else 'coalition','leverage':LEVON,'topic':ONLY,**({'compet_mult':True} if MBC.compet_on() else {}),'leverage_source':LEV_SRC,**({'compet_artifact_sha256':MBC.compet_record()[1]} if MBC.compet_on() else {}),   # R6: 'leverage' = a non-unit leverage factor is applied (LEVON); its source is leverage_source
     'defend_overview':[{'m':m,'v':EP[m]['value'],'party':EP[m].get('party'),'elavg':EP[m].get('elavg')} for m in defend_ov],
     'topics':frontier}
# QME 2026-10-01 fix 6: a SEED!=7 or MC!=20 sensitivity run with neither FRONTIER_OUT nor TOPIC used to overwrite the production artifact; it now gets a
# _seed<S>_mc<M> tag (same convention as per_topic_coalition.py). Zero delta at the default SEED=7/MC=20 or whenever FRONTIER_OUT/TOPIC is set.
_tag='' if (ONLY or (SEED==7 and MC==20)) else '_seed%d_mc%d'%(SEED,MC)
_default=os.path.join(B, 'data', 'money', 'budget_frontier%s%s.json'%('_electoral' if ELECTORAL else '',_tag))
_outpath=scoped_output_path(os.environ.get('FRONTIER_OUT'),_default,ONLY)
MBC.dump_atomic(out,_outpath)
print('\nwrote %s (mode %s, leverage %s, %d topics)'%(os.path.basename(_outpath),'ELECTORAL' if ELECTORAL else 'coalition','%s (%s)'%('ON' if LEVON else 'OFF',LEV_SRC),len(frontier)))
