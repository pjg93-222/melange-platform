import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
import moneyball_core as MC
"""Money-response + defection mechanic (roadmap step 2).
A donation shifts a member's money lean with diminishing returns, scaled by how movable they are
(small relative to big fundraising = small move). Big enough competitor shock can trigger a defection."""
import json, sys, os
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
B=MB_ROOT
C={m['member']:m for m in json.load(open(B+r'\data\engine\congestion.json'))}
CS=json.load(open(B+r'\category_stance.json',encoding='utf-8'))
_OVERALL_CAPS=(CS.get('gate') or {}).get('overall_capped') or {}
MAT=CS.get('matrix') or {}
_tier,_trank,_tier_priority=MC.primacy_engine_state(list(MAT))
# --- dials ---
MAXSHIFT=float(os.environ.get('MAXSHIFT','18'))   # CALIBRATED 40->18: money moves POSITIONS little (buys access/effort, not votes — Ansolabehere; Fowler-Garro-Spenkuch null vote-buying); defections now rare/niche
HALF=float(os.environ.get('HALF','0.10'))         # donation = HALF*receipts -> half of MAXSHIFT (diminishing-returns scale)
RFLOOR=200000                                     # receipts floor so tiny-fundraiser members aren't infinitely movable
# DTHRESH = net_lean shift needed to risk a money-triggered defection. RESCALED 25 -> 11.25 (2026-07-01):
# the 25 was calibrated under MAXSHIFT=40 (conversion boundary: donation >= ~1/6 of receipts). When MAXSHIFT
# was cut 40->18 ("money is weak" -- Ansolabehere; Fowler-Garro-Spenkuch), the curve's ceiling (18) fell BELOW
# 25, making defection mathematically impossible at any amount -- the threshold silently bricked the channel.
# 11.25 = 18*(1/6)/((1/6)+HALF) preserves the ORIGINAL calibrated boundary exactly: a chunk >= ~16.7% of
# receipts risks one tier -- still rare/earned (a $10k PAC-cap donation on median receipts shifts ~0.9, far
# below threshold; only large scenario-level shocks convert). Judgment dial, env-overridable.
DTHRESH=float(os.environ.get('DTHRESH','11.25'))  # net_lean shift needed to risk a defection (rescaled to MAXSHIFT=18; boundary = donation >= ~1/6 receipts)
# RECEPTIVITY LAYER (2026-07-02, receptivity_layer.py): pressure_mult = Byrne-choking susceptibility x
# Fouirnaies-Hall electoral incentive x Linde-Vis prospect-theory domain (F&H competitiveness term neutral when COMPET_MULT is on:
# rc returns pressure_mult_compet, Paul ruling 2026-10-07). LOSSFRAME: a competitor THREAT
# (loss frame) moves lean more than an equal CU gift (K&T loss aversion, elite-attenuated per Linde-Vis).
# Fallback = neutral (legacy) when the json is absent or RECEPT_LAYER=0.
LOSSFRAME=float(os.environ.get('LOSSFRAME','1.10'))
if HALF<=0: raise ValueError('HALF must be > 0 (got %g): shift_lean divides by r+HALF'%HALF)   # QME 2026-10-01: fail loud, not ZeroDivision deep in shift_lean
if DTHRESH<=0: raise ValueError('DTHRESH must be > 0 (got %g): optimize.py and defection() divide by it'%DTHRESH)   # QME 2026-10-01
rc=MC.rc
COMMITTED={'Champion','Opponent'}                 # only the TERMINAL rungs are immune to money defection
# defection() walks the FULL overall ladder (R&A col G vocabulary, pro -> anti; same 10-rung order as
# sync_stance_clean / build_dashboard_bundle). One DTHRESH crossing = ONE fine rung -- now consistent with
# shift_pro's CONV_MAXSTEP=1 fine-tier move (the old coarse 5-rung ORDER made one crossing ~2 fine tiers,
# the "unjustified cross-taxonomy magnitude" the M2 note flagged). Highly Skeptical is DEEP BUT MOVABLE
# (user 2026-07-01): 8 rungs from Champion, one from Opponent -- a big shock can still move them (e.g.
# Durbin). The fine tiers (Highly/Persuadable/Lean Persuadable, True Neutral, Lean Skeptic) are real rungs
# here, no longer flattened to a single Neutral.
ORDER=['Champion','Ally','Highly Persuadable','Persuadable','Lean Persuadable','True Neutral',
       'Lean Skeptic','Skeptic','Highly Skeptical','Opponent']
# The primacy tier is an engine input. In the money-response ladder, a Tier-1
# member is promoted to the top of the earned ladder ('Ally') but remains MOVABLE. It is
# deliberately NOT 'Champion', because immunity would overstate a display crown. This intentionally
# diverges from col G (sync_stance_g -> overall_display_tier); the specified divergence is guarded,
# unlike the accidental crown-law divergence corrected earlier today.
def _money_overall_tier(m,cm):
    _base=MC.overall_tier(cm,m,_OVERALL_CAPS)
    if _tier.get(m,0)==1:
        # Today this floor equals assignment: overall_tier's ceiling is 'Ally' and deliberately omits 'Champion'.
        if _base not in ORDER: return _base   # QME 2026-10-01: '—' (tier-1, no earned cells) or any off-ladder tier is non-promotable; ORDER.index used to raise ValueError at import
        return ORDER[min(ORDER.index(_base),ORDER.index('Ally'))]
    return _base
OV={m:_money_overall_tier(m,cm) for m,cm in MAT.items()}
# Aliases only: Defender ~= Champion tier (committed pro, PERS 0.12~=0.10); bare Neutral / legacy Unengaged
# sit at the True Neutral point of the spectrum.
PROJ={'Defender':'Champion','Neutral':'True Neutral','Unengaged':'True Neutral'}
_rvals=sorted(C[m]['receipts'] for m in C if (C[m].get('receipts') or 0)>0); _RMED=_rvals[len(_rvals)//2] if _rvals else RFLOOR
def receipts(m):
    # HIGH-2: receipts=0/null is MISSING data, not a tiny fundraiser. The old max(0,RFLOOR) floored them to
    # $200k, so a $100k chunk read as 50% of "receipts" -> instant conversion (34 members, 18 with no data).
    # Impute the MEDIAN receipts for no-data members -> they're as movable as a typical member, not artificially cheap.
    rr=C[m].get('receipts') or 0
    return max(rr,RFLOOR) if rr>0 else _RMED
def shift_lean(m,source,amount,cm=1.0):
    """source: 'cu' (helps, +) or 'comp' (banks/finserv, -). Returns signed net_lean shift (diminishing, capacity-aware).
    cm = COMPET_MULT competitiveness multiplier (same c on BOTH legs): the EFFECTIVE dollar is cm*amount; cm=1.0 is bitwise the old value."""
    r=cm*amount/receipts(m)                    # (effective) donation relative to their fundraising
    mag=MAXSHIFT*(r/(r+HALF))                   # saturating: diminishing returns + capacity
    mag*=rc(m,'pressure_mult')                 # personality x incentive x PT-domain receptivity
    if source!='cu': mag*=LOSSFRAME            # loss-frame asymmetry: threats land harder than gifts
    return (mag if source=='cu' else -mag)
def defection(m,dlean):
    """Does this lean shift defect the member, and to what new overall stance? (money-triggered)"""
    # A missing stance is not Neutral: the '-' no-position ontology keeps absent evidence off the movable ladder (pre-merge col G behavior).
    st=OV.get(m); st=PROJ.get(st,st)               # ladder label -> nearest coarse rung (see PROJ above)
    if st in COMMITTED or abs(dlean)<DTHRESH or st not in ORDER: return None
    steps=int(abs(dlean)//DTHRESH)             # how many LADDER rungs the money drags them (fine-grained)
    i=ORDER.index(st); j=min(len(ORDER)-1,max(0,i+(steps if dlean<0 else -steps)))  # competitor(-)->toward Opponent
    return ORDER[j] if ORDER[j]!=st else None
if __name__=='__main__':
    print('MONEY-RESPONSE validation (MAXSHIFT=%g, HALF=%g of receipts)\n'%(MAXSHIFT,HALF))
    print('Effect of a $100k shock by receipts size (same $, very different move):')
    for m in ['Steve Daines','Jon Tester','Jared Golden','Marie Gluesenkamp Perez']:
        if m in C:
            print('   %-24s receipts $%-11s  CU+$100k -> lean %+.1f   comp+$100k -> lean %+.1f'%(
                m,f"{C[m].get('receipts') or 0:,}",shift_lean(m,'cu',100000),shift_lean(m,'comp',100000)))
    print('\nDiminishing returns on a single member (Jared Golden):')
    if 'Jared Golden' in C:
        for amt in (25000,50000,100000,250000,500000):
            print('   CU +$%-7s -> lean shift %+.1f'%(f"{amt:,}",shift_lean('Jared Golden','cu',amt)))
    print('\nDefection triggers (competitor floods a persuadable member):')
    cnt=0
    for m in sorted(C,key=lambda x:-(C[x].get('receipts') or 0)):
        d=shift_lean(m,'comp',250000); nw=defection(m,d)
        if nw and OV.get(m) in ('Ally','Highly Persuadable','Persuadable','Lean Persuadable',
                                'True Neutral','Neutral','Lean Skeptic'):
            print('   %-24s [%s] comp+$250k (lean %+.1f) -> defects to %s'%(m,OV.get(m),d,nw)); cnt+=1
        if cnt>=8: break
