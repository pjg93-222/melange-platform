import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
import moneyball_core as MBC
"""Linear-Threshold priming (roadmap step 3, the flip/join side of the two models).
A member co-sponsors / flips when their PRO-influence clears their threshold:
    pressure(active pro neighbors) + money-influence  >=  theta(resistance)   [Linear Threshold]
For each persuadable target we solve for the CHEAPEST balance of network pressure (which neighbors to
recruit) and direct money (diminishing returns) that tips them over."""
import json, sys, os, re
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
B=MB_ROOT
TOPIC=os.environ.get('TOPIC','Mortgage')
CEDGE=float(os.environ.get('CEDGE','10000'))
MONEY_PARITY=os.environ.get('LT_MONEY_PARITY','1')!='0'   # QME 2026-10-01: 1 (default) = money term IS money_response.shift_lean (HALF from the HALF dial, pressure_mult applied); 0 = old hardcoded HALF=0.10, no pressure_mult. Mirrored in dashboard prime() via K.LTPAR/K.LTHALF.
OPP_TARGET=os.environ.get('LT_OPP_TARGET','0')=='1'       # QME 2026-10-01: 0 (default) = a committed Opponent topic cell is never a target (the documented rule); 1 = old (Opponent cells were targets unless blocked overall). Mirrored via K.LTOPP.
MAXSHIFT=18.0; RFLOOR=200000
HALF=float(os.environ.get('HALF','0.10')) if MONEY_PARITY else 0.10   # money curve (shared with money_response.py: same HALF dial, QME 2026-10-01; was hardcoded 0.10)
PRO={'Champion','Defender','Ally'}
# requirement to flip, by current tier (units of pro-influence); committed opponents excluded (QME 2026-10-01: enforced in prime(), LT_OPP_TARGET=1 reverts; 'Opponent' stays on the ladder for the dashboard's EG.theta)
THETA={'Highly Persuadable':0.6,'Persuadable':0.8,'Lean Persuadable':0.9,
       'True Neutral':1.0,'Neutral':1.0,'Lean Skeptic':1.5,'Skeptic':2.0,
       'Highly Skeptical':2.5,'Opponent':3.0,'Ally':0.0}   # fixed ladder resistance
C={m['member']:m for m in json.load(open(B+r'\data\engine\congestion.json'))}; members=list(C.keys())
bio2name={m['bioguide']:m['member'] for m in C.values() if m['bioguide']}
CS=json.load(open(B+r'\category_stance.json')); MAT=CS['matrix']; cats=CS['cats']
def pick_topic(topic,cats):
    # QME 2026-10-01: exact (case-insensitive) match first, then a UNIQUE substring, else ValueError listing the candidates. The old
    # [0] of a substring filter silently took the first hit and IndexError'd on no match.
    t=topic.strip().lower(); ex=[c for c in cats if c.lower()==t]
    if len(ex)==1: return ex[0]
    sub=[c for c in cats if t and t in c.lower()]
    if len(sub)==1: return sub[0]
    raise ValueError('TOPIC %r %s; candidates: %s'%(topic,'is ambiguous' if sub else 'matches no category',sub or list(cats)))
cat=pick_topic(TOPIC,cats)
def scoped_output_path(explicit,default,selector):
    # QME 2026-10-01 (optimize.py pattern): a non-default TOPIC run gets its own artifact so it cannot overwrite production; LT_OUT is an explicit override.
    if explicit: return explicit
    if not selector: return default
    slug=re.sub(r'[^a-z0-9]+','_',selector.lower()).strip('_') or 'topic'
    stem,ext=os.path.splitext(default)
    return stem+'_topic_'+slug+ext
try: _DEFCAT=pick_topic('Mortgage',cats)
except ValueError: _DEFCAT=None
OUTF=scoped_output_path(os.environ.get('LT_OUT'),B+r'\data\network\lt_priming.json',None if cat==_DEFCAT else cat)   # default TOPIC 'Mortgage' resolves uniquely to 'Mortgage & Housing' -> production path, zero delta
adj=MBC.load_network()
# L4: unify the diffusion kernel with the rest of the system (coalition/scenario/frontier use the
# bridge-amplified P=(w*cpf)/(w*cpf+4); this module previously used the un-amplified P=w/(w+4)). Adopt the
# amplified kernel so the pressure a primeable target feels from cross-party bridge neighbors matches the
# cascade -- one diffusion law everywhere.
bpf=MBC.bpf()
prt=MBC.party()
# N-1 (audit 2026-07-08): the ONE shared cascade kernel. build_P adds the receiver-susceptibility factor
# em(v)=edge_mult that this inline copy dropped -- the old P=(w*cpf)/(w*cpf+6) diverged from the live
# kernel by up to ~13%/edge (530/533 members have edge_mult!=1), silently feeding a divergent kernel into
# data/network/lt_priming.json despite this module's "one diffusion law everywhere" intent. (BETA was dead.)
P=MBC.build_P(adj)   # bridge-amplified (L4) + susceptibility-aware
# BAKKER CROSS-CUE SIGNING (lit-audit run-4j forward item, applied at the 2026-07-05 merge; BAKKER_XCUE=0
# reverts). Bakker-Lelkes-Malka (JOP): out-party cues do NOT persuade (null-to-backfire) — the kernel's
# unconditional 0.4 cross-party floor is exactly an attenuated-positive out-party cue. In the PRIMING
# pressure/recruit terms ONLY (kernel untouched): a cross-party pro-cue passes through sqrt(bpf_u*bpf_v),
# so perfect bridge pairs keep full influence (the kernel's own philosophy at cpf=1.0) and non-bridge
# out-party cues fall to ~0 (clamped at 0, never negative — backfire magnitude not invented). Mirrored in
# the dashboard JS port (make_dashboard_html_00.tpl prime()).
XCUE=os.environ.get('BAKKER_XCUE','1')!='0'
def xf(u,v): return 1.0 if (not XCUE or prt.get(u)==prt.get(v)) else (bpf.get(u,0.0)*bpf.get(v,0.0))**0.5
_rvals=sorted((C[m].get('receipts') or 0) for m in C if (C[m].get('receipts') or 0)>0); _RMED=_rvals[len(_rvals)//2] if _rvals else RFLOOR
def receipts(m):
    # HIGH-2 (ported 2026-07-01; the fix landed in money_response/flip_model/per_topic_flip but was missed here):
    # receipts=0 is MISSING data, not a tiny fundraiser -- flooring to $200k made the 18 no-record members look
    # ~5-10x cheaper to prime with money. Impute the MEDIAN so they read as typically movable.
    rr=C[m].get('receipts') or 0
    return max(rr,RFLOOR) if rr>0 else _RMED
def pmult(m):                                       # QME 2026-10-01: money_response.shift_lean multiplies by pressure_mult; parity=0 -> 1.0 (old)
    return max(MBC.rc(m,'pressure_mult'),1e-9) if MONEY_PARITY else 1.0
CMX={m:MBC.cmult(m) for m in members}              # COMPET_MULT (Paul 2026-10-05): c scales EFFECTIVE dollars; dial OFF -> exactly 1.0, no file read. Mirrored in the dashboard JS (EG.cmult).
def money_units(m,dollars):                         # net_lean shift -> pro-influence units (MAXSHIFT pts == 1 unit) = shift_lean/MAXSHIFT
    r=CMX[m]*dollars/receipts(m); return (MAXSHIFT*(r/(r+HALF))*pmult(m))/MAXSHIFT   # c*dollars = effective dollars (real $ stay the price)
def dollars_for_units(m,u):                         # invert: $ needed for u units of pro-influence (diminishing); u/pm = saturating fraction
    u=min(u/pmult(m),0.98); return HALF*receipts(m)*u/(1-u)/CMX[m]   # /c: REAL dollars for u units (c==1.0 -> exactly the old value)
proset={m for m in members if MAT.get(m,{}).get(cat) in PRO}
# ---- stance-integrity gates (user-ratified 2026-07-04; 'Lean Skeptic' explicitly carved out of every one) ----
# Root cause (the Warren sighting, July-1 artifact): single-cell myopia admitted a system-level Skeptic
# (overall tier, CFPB/FoM/Tech Opponent) as a True-Neutral Mortgage target AND as a pro-pressure recruit,
# and the net_lean resistance term only sees money-TAKERS — a PAC-free ideologue priced as CHEAP.
# Mirrored in the dashboard's in-browser port (make_dashboard_html_00.tpl prime/cheapestFlips).
BLOCK_OVERALL=MBC.STANCE_GATE_BLOCKED   # QME 2026-10-01: the kernel's one copy (== {'Skeptic','Highly Skeptical','Opponent'}, zero delta); overall 'Lean Skeptic' stays eligible
BLOCK_RECRUIT_CELL={'Skeptic','Highly Skeptical','Opponent'}  # committed cells cannot be recruited as pressure sources
COMMIT=0.25                                               # +theta per declared Opponent cell anywhere in the matrix — opposition breadth the money term can't see
OVERALL_CAPS=(CS.get('gate') or {}).get('overall_capped') or {}
OVT={m:MBC.overall_tier(MAT.get(m,{}),m,OVERALL_CAPS) for m in members}
def recruit_cost(u):
    # CEDGE scaled by persuadability (kernel PERS, anchored so True Neutral == CEDGE exactly);
    # Lean Skeptic pinned to flat CEDGE — the carve-out: gates/pricing leave LS exactly as before
    t=MAT.get(u,{}).get(cat)
    if t=='Lean Skeptic': return CEDGE
    return CEDGE*0.50/max(0.05,MBC.PERS.get(t,MBC.PERS_NONE))
def prime(v):
    tier=MAT.get(v,{}).get(cat)
    if tier not in THETA or tier in PRO: return None
    if tier=='Opponent' and not OPP_TARGET: return None               # QME 2026-10-01: committed Opponents are never targets (module comment + audit_full rule); LT_OPP_TARGET=1 restores
    if OVT.get(v) in BLOCK_OVERALL: return None                      # one mild topic cell can't make a system-level Skeptic a target
    theta=THETA[tier]+max(0,-(C[v].get('net_lean') or 0)/120)        # money-captured members resist more
    theta+=COMMIT*sum(1 for t in MAT.get(v,{}).values() if t=='Opponent')   # ideological commitment resists too (PAC-free opponents no longer read cheap)
    pressure=sum(P[u].get(v,0)*xf(u,v) for u in sorted(proset) if u in P[v])  # QME 2026-10-01: sorted = fixed summation order (set order is PYTHONHASHSEED-dependent in the last bits); pro sources deliberately NOT filtered by overall tier (a pro topic cell pressures that topic by design); pro pull u->v read at receiver v (P[u][v], target's edge_mult; index was reversed until 2026-09-30); cross-party cues bridge-gated (Bakker)
    gap=theta-pressure
    pm=pmult(v); mcap=0.97*pm                                         # QME 2026-10-01: money alone can deliver < pm units (pm==1.0 -> exactly the old 0.97)
    if gap<=1e-6: return {'topic':cat,'v':v,'tier':tier,'theta':round(theta,2),'pressure':round(pressure,2),'gap':round(gap,2),'cost':0,'money':0,'recruits':[],
                          'units':{'theta':round(theta,9),'pressure':round(pressure,9),'recruit_pull':0.0,'money_units':0.0}}
    money_only=dollars_for_units(v,min(mcap,gap))                     # $ to close whole gap with money alone
    avg_rate=money_only/min(mcap,gap)                                 # money's average $ per influence-unit
    # recruit not-yet-pro neighbors that are cheaper per unit than money; each adds P[u][v].
    # Stance floors (2026-07-04): the model may not enlist as a pro-advocate a member its own stance law
    # calls opposed — no Skeptic/Opponent topic cell, no Skeptic-or-worse overall read (Lean Skeptic allowed).
    # GUARD REMOVED 2026-08-03. This was `recruit_cost(u)/max(0.1, add)`. For add>=0.1 the divisor
    # cancels against the `c*add` booking below and a recruit is charged exactly recruit_cost(u),
    # which is the intended economics. Below 0.1 the divide-by-zero guard stopped cancelling, so a
    # weak-edge recruit was BOOKED at 10*add*recruit_cost(u) — i.e. at a fraction of what it costs —
    # and simultaneously looked cheaper per unit than it is, so it sorted higher and was accepted
    # more readily. A numerical guard was setting a price. It was also never needed: the
    # comprehension already filters `add>1e-6`, so the divisor cannot be zero here. Using the true
    # `add` makes `c*add == recruit_cost(u)` for EVERY edge, which is the whole point.
    # DIMENSIONAL FIX 2026-08-03 (Paul: "We gotta adjust the cost-guard"). The acceptance test and
    # the greedy sort key both used c/add = recruit_cost(u)/add**2. Units: recruit_cost is DOLLARS,
    # add is INFLUENCE-UNITS, so c = recruit_cost/add is already the $-per-influence-unit rate — the
    # exact quantity avg_rate ($/unit, = money_only/gap) is denominated in. Dividing by `add` a
    # SECOND time yields $/unit**2 and compares it against a $/unit threshold. The rate conversion
    # was being applied twice. Because every add here is < 1, add**2 < add, so the doubled division
    # INFLATED the apparent rate and the test rejected recruits that are genuinely cheaper than
    # money — 70 of 77 targets bought their whole gap with money and enlisted nobody. Both the test
    # and the sort now use the coherent c = recruit_cost(u)/add. Booking is unchanged: c*add ==
    # recruit_cost(u) exactly. Mirrored in make_dashboard_html_00.tpl prime().
    cands=sorted([(P[u].get(v,0)*xf(u,v),u,recruit_cost(u)/(P[u].get(v,0)*xf(u,v))) for u in P[v]
                  if u not in proset and MAT.get(u,{}).get(cat) not in BLOCK_RECRUIT_CELL
                  and OVT.get(u) not in BLOCK_OVERALL and P[u].get(v,0)*xf(u,v)>1e-6],key=lambda x:x[2])
    got=0.0; cost=0.0; recruits=[]
    for add,u,c in cands:                                             # c = $ per influence-unit
        if got>=gap: break
        if c<avg_rate: got+=add; cost+=c*add; recruits.append((u,round(add,2)))
    resid=max(0.0,gap-got)
    if resid>mcap: return None                                       # money can't close (>1 unit residual) -> not primeable on money alone
    money=round(dollars_for_units(v,resid)) if resid>1e-6 else 0
    cost+=money
    # QME 2026-10-01: 'units' = the recipe's own influence budget (full precision) so audit_full can check pressure+recruit_pull+money_units>=theta; 'topic' = the cell this artifact was solved for.
    return {'topic':cat,'v':v,'tier':tier,'theta':round(theta,2),'pressure':round(pressure,2),'gap':round(gap,2),
            'cost':round(cost),'money':money,'recruits':recruits,
            'units':{'theta':round(theta,9),'pressure':round(pressure,9),'recruit_pull':round(got,9),'money_units':round(resid,9)}}
res=[r for r in (prime(v) for v in members) if r and r['cost']>0]
res.sort(key=lambda r:r['cost'])
print('LT PRIMING — issue: %s | %d pro, %d primeable targets'%(cat,len(proset),len(res)))
print('Cheapest members to tip into co-sponsoring (pressure + money balance):')
MBC.dump_atomic(res,OUTF)
print('%-22s %-18s %5s %5s   %10s = %9s money + pressure(recruit)'%('member','tier','gap','press','TOTAL$',''))
for r in res[:14]:
    rc=', '.join('%s'%u.split()[-1] for u,a in r['recruits'][:3]) or '—'
    print('   %-22s [%-16s] %.2f %.2f  $%-8s = $%-7s + recruit %s'%(
        r['v'][:22],r['tier'],r['gap'],r['pressure'],f"{r['cost']:,}",f"{r['money']:,}",rc))
