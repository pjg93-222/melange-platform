# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
import moneyball_core as MBC
"""SALIENCE SHOCK — Noble & Turner (2024) "Presidential Leadership and Legislative Polarization"
mapped onto the CU policy domain (user-directed 2026-07-01: "use the Noble article to enhance the
system shock within the system").

The old system shock was a FLAT dial: exec opposes -> all opposition bystanders x1.6; supports -> x0.6.
Noble & Turner show that "going public" (an executive, or by extension any high-salience advocacy
campaign such as an ABB26 fly-in) is STRUCTURED:

  1. It activates PARTY-BASE pressure beta asymmetrically: the actor's co-partisans always move toward
     the actor MORE than out-partisans do (beta_co > beta_out), under three regimes:
        Rally (prob p)    : both parties move toward the actor's position
        Retreat (prob q)  : co-partisans toward, out-partisans AWAY (Lee 2009)
        Backfire (1-p-q)  : both away, out-partisans more
     [Their example: infrastructure (7-pt partisan gap) rallies; abortion/immigration retreat.
      Ours: Fraud Prevention / Data Privacy rally; CFPB / Credit Card Mandates retreat.]
  2. Going public ALWAYS widens the gridlock interval (Prop 1) -- the persuadable middle hollows out --
     even under a Rally, because the co-partisan move is bigger.
  3. The payoff depends on the STATUS QUO (Prop 2): on issues where the status quo favors you
     (our DEFENSIVE issues -- Interchange, Credit Card Mandates, CU Tax Status: CU wins via gridlock),
     going public is a DEFENSIVE APPEAL that raises co-partisan defection thresholds (delta channel) --
     "targeting fair-weather friends".  [Their example: Biden going public on abortion not to persuade
      Republicans but to keep Manchin from defecting -> ours: ACU going loud on CU Tax Status not to
      persuade the tax-writers but to keep lukewarm allies from trading the exemption away.]
     On issues where you need to CHANGE policy (offensive issues needing out-party pivots), raising
     salience under Retreat hardens the pivot against you -> work those quietly (Prop 2/4).
  4. Comparative statics: polarization z_c per issue (Prop 4), policy uncertainty psi_c (Prop 5 --
     new/emerging issues: wait and see), electoral strength rho (A.4 -- electorally weak members
     "gamble for reelection" and respond MORE to salience).

All regime dials are env-overridable; z_c, psi_c, p_c, q_c are ESTIMATED FROM THE STANCE MATRIX,
not hand-set.  Usage:
  python fec/engine/salience_shock.py --issue "CFPB" --actor pro --intensity 1.0 [--regime auto|rally|retreat|backfire]
  python fec/engine/salience_shock.py --advise            # rank ALL issues: where does going public help vs hurt?
  actor pro  = a CU-side champion goes public FOR the CU position (exec-party via --party, default R)
  actor anti = an executive/industry campaign goes public AGAINST the CU position"""
import json, sys, os, math, random, collections, argparse, importlib.util
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
B=MB_ROOT
spec=importlib.util.spec_from_file_location('mr',B+r'\fec\engine\money_response.py'); mr=importlib.util.module_from_spec(spec); spec.loader.exec_module(mr)
_fs=importlib.util.spec_from_file_location('fm',B+r'\fec\engine\flip_model.py'); fm=importlib.util.module_from_spec(_fs); _fs.loader.exec_module(fm)
SEED=7; MC=int(os.environ.get('MC','20'))   # MC=20 validated vs MC=100 (mc_stability_check: 100% seed overlap)
BSCALE=400000
PRO={'Champion','Defender','Ally'}; ANTI={'Opponent','Highly Skeptical','Skeptic'}
CTORD=['Champion','Defender','Ally','Highly Persuadable','Persuadable','Lean Persuadable',
       'True Neutral','Lean Skeptic','Skeptic','Highly Skeptical','Opponent']
TSCORE=MBC.SALIENCE_TSCORE
MIDBAND={'Highly Persuadable','Persuadable','Lean Persuadable','True Neutral','Lean Skeptic'}
CSJ=json.load(open(B+r'\category_stance.json')); MAT0=CSJ['matrix']; cats=CSJ['cats']; DEFENSIVE=set(CSJ['defensive'])
C={m['member']:m for m in json.load(open(B+r'\data\engine\congestion.json'))}; members=[m['member'] for m in C.values()]
bio2name={m['bioguide']:m['member'] for m in C.values() if m['bioguide']}
bpf=MBC.bpf()
prt=MBC.party()
# electoral vulnerability rho (Noble A.4): members on the electoral playbook (defend/contest) have a live
# race -> larger delta stake -> respond MORE to salience ("gambling for reelection").
try:   # M3 fix 2026-07-02: read plays from the SOURCE (electoral_plays.json), not the previous cycle's dashboard bundle
    _ep=json.load(open(B+r'\data\money\electoral_plays.json',encoding='utf-8'))
    vuln={m:(1.35 if _ep.get(m,{}).get('play') in ('defend','contest') else 1.0) for m in members}
except FileNotFoundError: vuln={}   # S2-03 audit run-2: missing file falls to neutral LOUDLY; a corrupt file now crashes instead of silently neutralizing
if not vuln: print('  !! electoral_plays.json MISSING — electoral-vulnerability amplifier NEUTRAL for all members. Run build_electoral_plays.py.')
VULN=lambda m: vuln.get(m,1.0)
cpf=MBC.cpf_diffusion
# RECEPTIVITY LAYER (2026-07-02): receiver-susceptibility on cascade edges (Byrne choking composite)
# + susceptibility multiplier on shock steps. Neutral fallback = legacy behavior.
rc=MBC.rc
adj=MBC.load_network()
P=MBC.build_P(adj)
idx={m:i for i,m in enumerate(members)}; NM=len(members)
_sm=MBC.sm64
def cascade(A,Bs,it):
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

# ---------- Noble parameter estimation from the stance matrix ----------
BACKFIRE0=float(os.environ.get('SAL_B0','0.05'))     # base backfire probability
BACKF_PSI=float(os.environ.get('SAL_BPSI','0.10'))   # backfire grows with policy uncertainty (Prop 5 caution)
DELTA=float(os.environ.get('SAL_DELTA','0.5'))       # White-House/electoral-control payoff -> defensive-appeal DTHRESH lift (Eq. 1: threshold + delta/2)
def issue_params(cat):
    return MBC.salience_issue_params(MAT0,members,prt,cat,DEFENSIVE,BACKFIRE0,BACKF_PSI)

# ---------- the shock: regime-signed, party-asymmetric stance perturbation ----------
def steps_for(member,actorparty,actorside,regime,intensity):
    """Signed fine-ladder steps for one member.  +ve = toward the ACTOR's position.
    beta_co > beta_out always (Noble's only hard restriction).  Cross-pressured out-partisans
    (high bipartisanship) are the ones who move WITH the actor under a Rally."""
    co=(prt.get(member)==actorparty)
    v=VULN(member)*rc(member,'susc_mult')              # electoral gamble (Noble A.4) x pressure susceptibility (Byrne)
    if regime=='rally':
        s=1.0 if co else (0.5 if bpf.get(member,0.24)>=0.30 else 0.0)   # out-partisan moves only if cross-pressured
    elif regime=='retreat':
        s=1.0 if co else -1.0
    else:  # backfire: both bases sour; co-partisans with a live race hold the line (delta dominates base: Sec 3.1)
        s=(0.0 if VULN(member)>1.0 else -0.5) if co else -1.0
    return int(math.copysign(int(abs(s)*intensity*v+0.5),s)) if s else 0
def apply_shock(cat,actorside,actorparty,regime,intensity):
    """Returns (MAT', notes).  actorside pro: 'toward actor' = toward the CU position (up CTORD);
    anti: toward the industry position (down CTORD).  Terminal rungs are committed and never move."""
    MAT={m:dict(MAT0[m]) for m in members}; moved=collections.Counter()
    for m in members:
        cur=MAT[m].get(cat)
        if cur not in CTORD or cur in ('Champion','Defender','Opponent'): continue
        st=steps_for(m,actorparty,actorside,regime,intensity)
        if not st: continue
        toward_cu=(st>0)==(actorside=='pro')            # toward actor mapped onto the pro<->anti axis
        i=CTORD.index(cur); j=max(0,min(len(CTORD)-1,i+(-abs(st) if toward_cu else abs(st))))
        if CTORD[j]!=cur: MAT[m][cat]=CTORD[j]; moved[('co' if prt.get(m)==actorparty else 'out',('toward' if toward_cu==(actorside=='pro') else 'away'))]+=1
    return MAT,moved
def gridlock_width(cat,MAT): return sum(1 for m in members if MAT[m].get(cat) in MIDBAND)
PROBE=float(os.environ.get('SAL_PROBE','250000'))   # standardized industry flood used to test the defection window
def defensive_appeal_value(cat,actorparty,shock_on):
    """Prop 2 defensive-appeal channel: while a co-partisan-directed appeal is live, co-partisan defection
    needs dlean >= DTHRESH*(1+DELTA/2) (their Eq.1: threshold + delta/2).  Count currently-PRO co-partisans
    whom a standardized $PROBE competitor shock would flip at the OLD threshold but NOT the lifted one =
    defections prevented ('fair-weather friends' held)."""
    probe=PROBE
    held=[]
    for m in members:
        if camp(MAT0[m].get(cat,'') )!='pro' or prt.get(m)!=actorparty: continue
        dl=abs(mr.shift_lean(m,'comp',probe,fm.CM[m]))
        if mr.DTHRESH<=dl<mr.DTHRESH*(1+DELTA/2): held.append(m)
    return held if shock_on else []

# M6 fix 2026-07-02: same H3 imputation as flip_model/budget_frontier — no-FEC-record members get median
# industry money for bystander activation instead of a fabricated permanent zero.
IND0={n:(C[n]['banks'] or 0)+(C[n]['finserv'] or 0) for n in members}
_iv=sorted(v for v in IND0.values() if v>0); _imed=_iv[len(_iv)//2] if _iv else 0
from engine.money_observation import industry_money_missing
if os.environ.get('IMPUTE_IND0','1')!='0':
    IND0={n:(_imed if industry_money_missing(C[n]) else IND0[n]) for n in members}
def run_issue(cat,actorside,actorparty,regime,intensity,report=True):
    par=issue_params(cat)
    regs=[(regime,1.0)] if regime!='auto' else [('rally',par['p']),('retreat',par['q']),('backfire',par['b'])]
    ind0=IND0
    s0=spread(cat,MAT0,ind0); g0=gridlock_width(cat,MAT0)
    f0=fm.flip_value(cat,MAT0,fm.cu0,fm.ind0) if cat in fm.cat2sc else None
    es=0.0; ef=0.0; eg=0.0; det={}
    for rg,w in regs:
        if w<=0: continue
        MAT,moved=apply_shock(cat,actorside,actorparty,rg,intensity)
        s1=spread(cat,MAT,ind0); g1=gridlock_width(cat,MAT)
        f1=fm.flip_value(cat,MAT,fm.cu0,fm.ind0) if f0 is not None else None
        es+=w*(s1-s0); eg+=w*(g1-g0)
        if f0 is not None: ef+=w*(f1-f0)
        det[rg]={'w':round(w,3),'dspread':round(s1-s0,2),'dgridlockband':g1-g0,'dwin':None if f0 is None else round(f1-f0,3),'moved':dict(moved)}
    held=defensive_appeal_value(cat,actorparty,actorside=='pro')
    out={'issue':cat,'params':par,'actor':actorside,'actor_party':actorparty,'intensity':intensity,
         'E_dspread':round(es,2),'E_dwin':round(ef,3) if f0 is not None else None,'E_dmidband':round(eg,1),
         'defections_prevented':held,'regimes':det}
    if report:
        print('================ SALIENCE SHOCK — %s ================'%cat)
        print('  issue params: z=%.2f (partisan gap) psi=%.2f (uncertainty) | P(rally)=%.2f P(retreat)=%.2f P(backfire)=%.2f | status quo: %s'
              %(par['z'],par['psi'],par['p'],par['q'],par['b'],par['sq'].upper()))
        print('  actor: %s-side campaign (%s co-partisans), intensity %.1f'%(actorside.upper(),actorparty,intensity))
        for rg,d in det.items():
            print('   regime %-9s w=%.2f  spread %+0.2f | winnability %s | persuadable band %+d | moved %s'
                  %(rg,d['w'],d['dspread'],('%+0.3f'%d['dwin']) if d['dwin'] is not None else 'n/a',d['dgridlockband'],d['moved']))
        # NB: 'winnability' is the REMAINING persuadable-pool value (flip_model): conversions to Ally DRAIN it
        # even though they are wins -- read it as pool depletion, not campaign loss.  Band delta<0 = Prop 1
        # hollowing; >0 on defend issues = anti-side co-partisans pulled INTO the middle (also a win for gridlock).
        print('  EXPECTED: spread %+0.2f co-sponsors | remaining-pool winnability %+0.3f | persuadable-band occupancy %+0.1f'%(es,ef,eg))
        if held: print('  DEFENSIVE APPEAL (Prop 2): co-partisan defection threshold lifted x%.2f -> %d fair-weather all%s held vs a $%dk competitor shock: %s'
                       %(1+DELTA/2,len(held),'y' if len(held)==1 else 'ies',PROBE/1000,', '.join(held[:6])+('...' if len(held)>6 else '')))
        elif actorside=='pro' and par['sq']=='defend': print('  DEFENSIVE APPEAL: no fair-weather co-partisan sits in the $%dk defection window right now'%(PROBE/1000))
    return out

ap=argparse.ArgumentParser()
ap.add_argument('--issue'); ap.add_argument('--actor',default='pro',choices=['pro','anti'])
ap.add_argument('--party',default='R'); ap.add_argument('--regime',default='auto',choices=['auto','rally','retreat','backfire'])
ap.add_argument('--intensity',default='1.0'); ap.add_argument('--advise',action='store_true')
a=ap.parse_args()
# M7 fix 2026-07-02: explicit short-name -> frame_alignment long-name map (the old first-word substring
# match was fragile: 'CU' hit multiple keys). Fallback = old fuzzy match for any unmapped category.
SHORT2LONG={'Examination & Supervision':'Examination & Supervision','CU Capital Requirements':'Credit Union Capital Requirements',
 'NCUA Budget & Insurance':'NCUA Budget & Share Insurance Fund','Interchange':'Interchange','Payments':'Payments',
 'Technology & Emerging':'Technology & Emerging Issues','Fraud Prevention':'Fraud Prevention',
 'Data Privacy & Cyber':'Data Privacy & Cybersecurity','Charter Modernization':'Charter Modernization & Governance',
 'Defense CU Issues':'Defense Credit Union Issues','Field of Membership':'Field of Membership',
 'CU Tax Status':'Credit Union Tax Status','CDFI Fund':'CDFI Fund','CFPB':'CFPB Issues & Structural Reform',
 'Credit Card Mandates':'Credit Card & Consumer-Fee Mandates','TCPA':'Telephone Consumer Protection Act',
 'Member Business Lending':'Member Business Lending','Mortgage & Housing':'Mortgage Lending & Housing'}
def _intensity(cat):
    """--intensity auto: campaign strength ∝ measured frame alignment (frame_alignment.py) — a shop that
    already runs ONE script on an issue (their Venezuela case; our CU Tax Status at 65%) hits harder than
    one where every office freelances.  intensity = 0.5 + alignment ∈ [0.5, 1.5]."""
    if a.intensity!='auto': return float(a.intensity)
    try:
        fa=json.load(open(B+r'\data\stance\frame_alignment.json',encoding='utf-8'))
        al={x['issue']:x['alignment'] for x in fa['alignment']}
        if SHORT2LONG.get(cat) in al: return round(0.5+al[SHORT2LONG[cat]],2)
        best=max((v for k,v in al.items() if cat.split(' &')[0].split()[0].lower() in k.lower()),default=0.2)
        return round(0.5+best,2)
    except Exception: return 1.0
if a.advise:
    # GO-PUBLIC ADVISORY (Prop 2 + Prop 4 operationalized for the CU shop): for every issue, the expected
    # value of a CU-side salience campaign.  Noble's prediction: positive on DEFEND issues (defensive appeal:
    # co-partisan constraint + retreat is harmless when you want gridlock) and weakest / negative on OFFENSE
    # issues with high z (you need the out-party pivot; retreat hardens them).  Quiet diplomacy beats the
    # bully pulpit exactly where the model says it should.
    print('================ GO-PUBLIC ADVISORY — CU-side salience campaign, all issues ================')
    print('%-30s %5s %5s %6s %6s %8s %8s %8s  %s'%('issue','z','psi','P(ral)','P(ret)','E dsprd','worst','sq','verdict'))
    rows=[]
    PSI_DAMP=float(os.environ.get('SAL_PSI_DAMP','0'))   # M4 dial (default off): damp value on high-uncertainty issues (Prop 5 wait-and-see)
    for c in cats:
        r=run_issue(c,'pro',a.party,'auto',_intensity(c),report=False)
        # value = realized coalition gain (cascade spread) + defensive holds.  E_dwin is NOT folded in:
        # it measures the REMAINING persuadable pool, which conversions legitimately drain.
        val=((r['E_dspread'] or 0)+1.5*len(r['defections_prevented']))*(1.0-PSI_DAMP*r['params']['psi'])
        worst=min((d['dspread'] for d in r['regimes'].values()),default=0.0)   # tail risk: worst-regime outcome
        rows.append((val,worst,c,r))
    # Noble's discipline is the OPPORTUNITY COST c: you cannot go public on everything (Fig. 2).  So the
    # verdict is RELATIVE — spend the bully pulpit on the top tier; below that, quiet work dominates.
    vals=sorted((v for v,_,_,_ in rows),reverse=True); cut=vals[max(0,len(vals)//4-1)]
    for val,worst,c,r in sorted(rows,key=lambda t:-t[0]):
        p_=r['params']
        nodata=all(MAT0[m].get(c) not in TSCORE for m in members)
        if nodata: verdict='no stance data yet'
        elif val>=cut and val>0: verdict='GO PUBLIC'
        elif worst<-15: verdict='quiet work (retreat/backfire tail risk)'
        elif val<=0: verdict='quiet work'
        else: verdict='worth it only if cheap (opportunity cost call)'
        if p_['sq']=='defend' and r['defections_prevented']: verdict+=' + defensive appeal: holds %d'%len(r['defections_prevented'])
        if p_['psi']>=0.8 and 'no stance data' not in verdict: verdict+=' [high-ψ: wait-and-see caution (Prop 5)]'
        print('%-30s %5.2f %5.2f %6.2f %6.2f %+8.2f %+8.2f %8s  %s'%(c[:30],p_['z'],p_['psi'],p_['p'],p_['q'],
              r['E_dspread'],worst,p_['sq'],verdict))
    MBC.dump_atomic({'advisory':[{'issue':c,'value':round(v,3),'worst_regime_dspread':round(w,2),
               **{k:r[k] for k in ('params','E_dspread','E_dwin','E_dmidband')},
               'defections_prevented':r['defections_prevented']} for v,w,c,r in sorted(rows,key=lambda t:-t[0])],**({'dials':dict(zip(('COMPET_MULT','COMPET_SRC_SHA256'),MBC.compet_record()))} if MBC.compet_on() else {})},   # R7 (Paul ruling 2026-10-07, D-107): recorded ONLY when ON (optimize.py precedent; dial-off file byte-identical)
              os.environ.get('SAL_OUT') or B+r'\salience_advisory.json',indent=1)   # SAL_OUT: no-clobber redirect for probe/candidate runs (default = the production path)
    print('\nwrote salience_advisory.json')
elif a.issue:
    if a.issue not in cats: print('unknown issue; choose from:',cats); sys.exit(1)
    run_issue(a.issue,a.actor,a.party,a.regime,_intensity(a.issue))
else:
    ap.print_help()
