import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
import moneyball_core as MC
import roster
"""Shared Linear-Threshold flip model (imported by scenario.py and optimize.py).
Winnability of a member on a topic:  pflip(n) = a/(a+b),
  a = WM*pm*(cu+1)/((cu+1)+HALFF*R) + WP*nA/mAt   (party-weighted proximity to champions)
  b = WM*pm*(ind+1)/((ind+1)+HALFF*R) + WP*nB/mBt (party-weighted proximity to opponents)
CU money raises a with saturating (Michaelis-Menten) returns; competitor money raises b.
COMPET_MULT (Paul 2026-10-05, default ON since Paul 2026-10-06; OFF -> c==1.0 exactly): the competitiveness multiplier c scales EFFECTIVE dollars on
BOTH legs, a: (c*cu+1)/((c*cu+1)+HALFF*R), b: (c*ind+1)/((c*ind+1)+HALFF*R); pm still multiplies both and the same c sits on
both, so equal money and equal peer pull stay at pflip 0.5. Real dollars (the $10k cap, the pot) are never scaled.
PHYSICS (obs-(e) ruling 2026-08-17): proximity here uses party-only cpf_pressure -- NOT the
bridge-amplified cpf_diffusion of the cascade engines (the two-physics design). Earlier
docstring prose claiming bridge amplification and sqrt returns was stale; corrected, code
unchanged. SPECIFICATION (obs-(d) ruling): pm (pressure_mult) multiplies BOTH money terms
and cancels in the money-only limit -- it reweights money-vs-peer channels; it is NOT a
level effect on pflip and must not be analyzed as an additive predictor.
Expected flips on a topic = sum_n tgt(n)*pflip(n).  Faithful to per_topic_flip.py.
"""
import json, os, sys, openpyxl
B=MB_ROOT
WM,WP=0.35,0.65; CAP=50000; BASE=0.15   # CALIBRATED: money is the minority partner to peer/network influence (money buys access/effort > votes — Ansolabehere; Hall&Wayman; Kalla&Broockman)
# M5: '—' = member has taken NO recorded position on THIS issue, which is 59.8% of matrix cells.
# At the old 0.55 a silent member ranked as MORE persuadable than a known 'Ally' -- conceptually
# backwards: a declared Ally has already signaled sympathy, whereas silence is honest uncertainty, not
# enthusiasm. Sensitivity test (—∈{0.30,0.45,0.55,0.75,0.95}): total winnability ran 149.5->245.8 (the
# value scales the whole objective since '—' dominates), and top-20 overlap vs 0.55 was 16/18/20/18/15 --
# the extremes (esp. 0.95) pulled record-less freshmen up the board. 0.45 was chosen believing Ally=0.45
# (the attic allocator's table); the kernel's Ally is 0.40, so 0.45 let silence outrank an Ally. Decision
# F-9 (2026-10-11) set 0.40: a silent member sits at PARITY with a known Ally (uncertain, not presumed
# eager) and never outranks one. Judgment dial, env-overridable via PERS_NONE (0.45 = pre-F-9).
from moneyball_core import PERS   # single copy (was drift-prone x5)
PRO={'Champion','Defender','Ally'}; ANTI={'Opponent','Highly Skeptical','Skeptic'}
C={m['member']:m for m in json.load(open(os.path.join(B, 'data', 'engine', 'congestion.json')))}
members=[m['member'] for m in C.values()]
HALFF=float(os.environ.get('HALFF','0.10'))   # capacity scale: money == HALFF*receipts -> half the max money-pull (reconciles with money_response)
if HALFF<=0: raise ValueError('HALFF must be > 0 (got %g): pflip divides by (cu+1)+HALFF*R'%HALFF)   # QME 2026-10-01: fail loud, not ZeroDivision in pflip
_rv=sorted((C[n].get('receipts') or 0) for n in members if (C[n].get('receipts') or 0)>0); _RMED=_rv[len(_rv)//2] if _rv else 200000
R={n:(max((C[n].get('receipts') or 0),200000) if (C[n].get('receipts') or 0)>0 else _RMED) for n in members}   # HIGH-2: receipts=0 is MISSING data -> impute MEDIAN (floor only real small fundraisers). Flooring no-record members to $200k had made them the CHEAPEST to convert / most winnable. Matches money_response.receipts().
bio2name={m['bioguide']:m['member'] for m in C.values() if m['bioguide']}
cu0={n:(C[n]['cu'] or 0) for n in members}; ind0={n:(C[n]['banks'] or 0)+(C[n]['finserv'] or 0) for n in members}
# Impute only unobserved money. A verified PAC query can establish a real zero or
# positive contribution even when the fundraising-totals endpoint has no record.
# IMPUTE_IND0=0 disables the existing median fallback for unobserved members.
_iv=sorted(v for v in ind0.values() if v>0); _imed=_iv[len(_iv)//2] if _iv else 0
from engine.money_observation import industry_money_missing
IMPUTE_IND0=os.environ.get('IMPUTE_IND0','1')   # QME 2026-10-01: effective value recorded in optimize_result.json dials ('0' disables the median fallback)
ind_imputed={n for n in members if industry_money_missing(C[n])} if IMPUTE_IND0!='0' else set()
ind0={n:(_imed if n in ind_imputed else ind0[n]) for n in members}
nodata=set(ind_imputed)   # truly-blind members (no FEC record): winnability is imputed, not measured
wb=openpyxl.load_workbook(os.path.join(B, 'Moneyball Main Version - Blended.xlsx'),data_only=True)
bm=wb['Blended Model']; nrm=wb['Legislative Score - Normalized']
primacy_tier,primacy_rank,primacy_priority=MC.primacy_engine_state(members)
_CS_DOC=json.load(open(os.path.join(B, 'category_stance.json')))
CS0=_CS_DOC['matrix']; CS_SCORES=_CS_DOC.get('scores') or {}
adj=MC.load_network()
# (moneyscale removed -- dead since the capacity-aware pflip rewrite; CU pull is now receipts-relative, not /moneyscale)
prt=MC.party()
bpf=MC.bpf()
cpf=MC.cpf_pressure   # named physics (was ambiguous 'cpf' x8)
from moneyball_core import blocks,BLOCKCAT
cat2sc={}
for sc in blocks: cat2sc.setdefault(BLOCKCAT[sc],sc)
# M3: two legislative blocks map to the single 'CFPB' category -- 99 (CFPB Restrain) and 145 (CFPB Reach).
# The combined CFPB STANCE already merges both (category_stance.json), but the legislative `eng` signal here
# previously kept block 99 ONLY (cat2sc.setdefault dropped 145), discarding CFPB-Reach engagement. Fold both
# blocks INTO the CFPB eng signal so the engagement grain matches the combined-stance grain and no signal is
# lost. cat2blocks lists every contributing block per canonical category sc.
cat2blocks={sc:[b for b in blocks if BLOCKCAT[b]==BLOCKCAT[sc]] for sc in set(cat2sc.values())}
nrow={nrm.cell(r,1).value:r for r in roster.rows('Legislative Score - Normalized') if nrm.cell(r,1).value}
eng={}
for sc in set(cat2sc.values()):
    d={n:0.0 for n in members}
    for blk in cat2blocks[sc]:                                  # sum engagement across all blocks for this category (CFPB: 99+145)
        sw=[nrm.cell(2,blk+o).value or 0 for o in (0,2,4)]
        for n in members:
            r=nrow.get(n)
            if r: d[n]+=sum(sw[i]*(nrm.cell(r,blk+[0,2,4][i]).value or 0) for i in range(3))
    eng[sc]=d
# QME 2026-10-01: engagement is read data_only=True with `or 0`, so an uncached formula cell or a member absent from the sheet is SILENTLY 0.
# Values unchanged; only count + warn (members with zero engagement on every issue, members with no sheet row).
_eng_missing=sorted(n for n in members if n not in nrow)
_eng_zero=sorted(n for n in members if all(eng[sc][n]==0 for sc in eng))
if _eng_missing or _eng_zero:
    print('WARNING: flip_model engagement: %d members have zero engagement on ALL issues (first: %s); %d members missing from Legislative Score - Normalized rows (first: %s) -- uncached formula cells read as 0 under data_only=True'%(
        len(_eng_zero),', '.join(_eng_zero[:5]) or '-',len(_eng_missing),', '.join(_eng_missing[:5]) or '-'),file=sys.stderr)
def camp(t): return 'pro' if t in PRO else 'anti' if t in ANTI else 'neu'
# RECEPTIVITY LAYER (2026-07-02): pressure_mult scales how hard MONEY pressure lands on this member
# (Byrne susceptibility x F&H electoral incentive x Linde-Vis PT domain); peer channel is handled by
# edge_mult inside the cascade engines. Neutral fallback = legacy.
# (F&H competitiveness term is neutral when COMPET_MULT is on: rc returns pressure_mult_compet, Paul ruling 2026-10-07.)
rc=MC.rc
CM={n:MC.cmult(n) for n in members}   # COMPET_MULT: per-member competitiveness multiplier, precomputed once (dial OFF -> all exactly 1.0, no file read); optimize/salience/audit read fm.CM
def pflip(cu_n,ind_n,R_n,nA_n,nB_n,mAt,mBt,pm=1.0,cm=1.0):
    # capacity-aware saturating money pull (Michaelis-Menten form): $X moves a big-receipts member less,
    # diminishes within a member, and matches money_response's curve -> one consistent diminishing-returns model.
    a=WM*pm*(cm*cu_n+1)/((cm*cu_n+1)+HALFF*R_n)+WP*nA_n/mAt   # cm=1.0 (COMPET_MULT off) is bitwise the old arithmetic
    b=WM*pm*(cm*ind_n+1)/((cm*ind_n+1)+HALFF*R_n)+WP*nB_n/mBt
    return a/(a+b) if a+b>0 else 0.0
_cache={}
_priority_cache={}
def topic_priority(cat,mode='flip'):
    key=(cat,mode)
    if key not in _priority_cache:
        _priority_cache[key]={
            n:MC.blended_engine_priority(
                primacy_tier[n],CS_SCORES.get(n,{}),cat,mode)
            for n in members}
    return _priority_cache[key]
_rowcache={}
def _stance_rows(MAT):
    """Row VIEW of MAT in `members` order, so the per-call signature does not redo per-member lookups
    into the outer dict (2026-08-03). The signature itself is unchanged — same values, same order,
    same tuple — this only removes the outer `MAT[n]` indirection and swaps the generator for a
    list comprehension. Measured ~2x on the signature build, which is ~80% of a topic_setup cache
    HIT, and topic_setup is called ~9,600 times per greedy step by optimize.marg_at.

    INVARIANT this depends on: MAT's INNER dicts are mutated in place and never REPLACED.
    That holds today — optimize.py builds them once (`MAT={m:dict(fm.CS0[m]) ...}`) and mutates
    cells with `MAT[m][c]=...`. If you ever rebind a whole row (`MAT[m]={...}`) this view goes
    stale and topic_setup will hit the cache on an old signature. The O(1) guards below catch a
    wholesale rebuild; they cannot catch one row being swapped, so don't do that.
    A strong reference to MAT is held so id(MAT) cannot be recycled onto a different object."""
    key=id(MAT)
    hit=_rowcache.get(key)
    if hit is not None:
        owner,rows=hit
        if owner is MAT and len(rows)==len(members) and rows[0] is MAT[members[0]] and rows[-1] is MAT[members[-1]]:
            return rows
    rows=[MAT[n] for n in members]
    if len(_rowcache)>8: _rowcache.clear()      # only a couple of MATs exist; bound the strong refs
    _rowcache[key]=(MAT,rows)
    return rows
def topic_setup(cat,MAT):
    """Fixed parts for a topic given stance (camp, proximity nA/nB, target value tgt). Cached per stance signature."""
    rows=_stance_rows(MAT)
    sc=cat2sc[cat]; sig=(cat,tuple([r.get(cat,'—') for r in rows]))
    if sig in _cache: return _cache[sig]
    cs={n:MAT[n].get(cat,'—') for n in members}; campT={n:camp(cs[n]) for n in members}
    nA={n:sum(w*cpf(n,u) for u,w in adj[n].items() if campT[u]=='pro') for n in members}
    nB={n:sum(w*cpf(n,u) for u,w in adj[n].items() if campT[u]=='anti') for n in members}
    mAt=max(nA.values()) or 1; mBt=max(nB.values()) or 1
    priority=topic_priority(cat,'flip')
    tgt={n:MC.priority_base_signal(eng[sc][n],priority[n],BASE)*PERS[cs[n]]*(1+0.3*bpf[n])*MC.delivery_mult(n) for n in members}   # Direct issue engagement + tier-anchored, leave-one-topic-out orientation priority. +PASSAGE bonus (Volden-Wiseman +8-14%); x say-do delivery discount.
    r=(cs,campT,nA,nB,mAt,mBt,tgt); _cache[sig]=r; return r
def flip_value(cat,MAT,cu_state,ind_state):
    """Expected weighted flips on a topic = sum tgt*pflip, given a money state."""
    cs,campT,nA,nB,mAt,mBt,tgt=topic_setup(cat,MAT)
    return sum(tgt[n]*pflip(cu_state[n],ind_state[n],R[n],nA[n],nB[n],mAt,mBt,rc(n,'pressure_mult'),CM[n]) for n in members)
cats=list(cat2sc.keys())
