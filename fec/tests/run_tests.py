# -*- coding: utf-8 -*-
"""UNIT TESTS for the math kernels (architecture review 2026-07-02, roadmap #5).
Plain-assert style, zero dependencies:  python fec/tests/run_tests.py
Covers the documented invariants of moneyball_core, money_response, and flip_model —
the layer that makes calibration changes and refactors mechanically verifiable
(protocol: change -> run_tests -> run_pipeline -> zero rank delta -> audit clean)."""
import os,sys,importlib.util
FEC=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,FEC)
P=F=0
def ok(name,cond,detail=''):
    global P,F
    P,F=(P+1,F) if cond else (P,F+1)
    print(('  OK   ' if cond else 'FAIL  ')+name+(('  :: '+str(detail)) if (detail and not cond) else ''))

import moneyball_core as MC
import bill_rules as BR
import stance.stance_rules as SR
import stance.media_rules as MR
from engine import influence_roles as IR
import tier_util as TU
import stance.tweet_image_evidence as TIE
from members.member_identity import Resolver
print('[core: CRN kernel]')
xs=[MC.sm64(i) for i in range(5000)]
ok('sm64 in [0,1)',all(0<=x<1 for x in xs))
ok('sm64 deterministic',all(MC.sm64(i)==xs[i] for i in range(0,5000,97)))
ok('sm64 well-spread (mean ~0.5)',abs(sum(xs)/len(xs)-0.5)<0.02,sum(xs)/len(xs))
ok('sm64 key-independent (no order effect)',MC.sm64(12345)==MC.sm64(12345) and MC.sm64(1)!=MC.sm64(2))
print('[core: atomic dump]')
import tempfile,json as _json
_tf=os.path.join(tempfile.gettempdir(),'mb_atomic_test.json')
MC.dump_atomic({'a':1,'b':[2,3]},_tf,separators=(',',':'))
ok('dump_atomic writes valid json',_json.load(open(_tf))=={'a':1,'b':[2,3]})
# Scoped to this target's own temp pattern because the global tempdir is shared with concurrent processes and a stranger's .tmp is not our leak.
_pre=set(f for f in os.listdir(tempfile.gettempdir()) if f.startswith(os.path.basename(_tf)) and f.endswith('.tmp'))
try: MC.dump_atomic({'x':object()},_tf)   # unserializable -> must raise and leave NO temp behind
except TypeError: pass
_leaked=[f for f in os.listdir(tempfile.gettempdir()) if f.startswith(os.path.basename(_tf)) and f.endswith('.tmp') and f not in _pre]
ok('dump_atomic leaves original intact + no temp on failure',_json.load(open(_tf))=={'a':1,'b':[2,3]} and not _leaked)
_txt=_tf+'.html'; MC.write_text_atomic('<b>complete</b>',_txt)
ok('write_text_atomic writes complete text',open(_txt,encoding='utf-8').read()=='<b>complete</b>')
os.remove(_txt)
import openpyxl as _ox
_xf=_tf+'.xlsx'; _tw=_ox.Workbook(); _tw.active['A1']='complete'; MC.save_workbook_atomic(_tw,_xf)
_tr=_ox.load_workbook(_xf,read_only=True,data_only=True)
ok('save_workbook_atomic writes a readable verified workbook',_tr.active['A1'].value=='complete')
_tr.close(); os.remove(_xf)
os.remove(_tf)
print('[core: constants]')
ok('IMP has all 18 categories incl. CU Tax Status',len(MC.IMP)==18 and MC.IMP.get('CU Tax Status')==3.0)
ok('allocation blocks expose CU Tax Status as its own money-engine topic',
   153 in MC.blocks and MC.BLOCKCAT.get(153)=='CU Tax Status' and MC.CATBLOCKS[153]==[153])
ok('PERS silence == Ally parity (user-ratified 2026-07-02; 0.40 per decision F-9 2026-10-11)',MC.PERS['—']==0.40 and MC.PERS['—']<=MC.PERS['Ally'])
ok('CFPB blocks folded (99+145)',sorted(MC.CATBLOCKS[99])==[99,145])
ok('SGATE monotone down the full ladder',
   all(MC.SGATE[a]>=MC.SGATE[b] for a,b in zip(
       ('Highly Persuadable','Persuadable','Lean Persuadable','True Neutral','Lean Skeptic','Skeptic','Highly Skeptical'),
       ('Persuadable','Lean Persuadable','True Neutral','Lean Skeptic','Skeptic','Highly Skeptical','Opponent'))))
ok('auto-repair SMART Acts excluded even when source title is ambiguous',
   BR.excluded_bill('H.R.3664',117,'SMART Act','') and
   BR.excluded_bill('H.R.1707',118,'SMART Act',''))
ok('financial/regulatory SMART Acts remain included',
   not BR.excluded_bill('S.2801',117,'SMART Act of 2022','') and
   not BR.excluded_bill('S.4264',118,'SMART Act of 2024','') and
   not BR.excluded_bill('H.R.4437',119,'Supervisory Modifications for Appropriate Risk-based Testing Act',''))
ok('misfiled CDFI/privacy bills have source-controlled effective categories',
   BR.override_for({},'H.R.7733',117).get('Category')=='CDFI Fund' and
   BR.override_for({},'S.869',118).get('Category')=='CDFI Fund' and
   BR.override_for({},'H.R.7036',118).get('Category')=='Data Privacy & Cyber')
ok('duplicate bill roles retain only the strongest role',
   BR.preferred_role(('cosponsor','original-cosponsor'))=='original-cosponsor' and
   BR.preferred_role(('cosponsor','sponsor','original-cosponsor'))=='sponsor')
ok('letters use the same per-Congress decay as bills',
   BR.congress_decay(119)==1.0 and BR.congress_decay(118)==.8 and BR.congress_decay(117)==.64 and
   BR.evidence_decay('LTR-TEST',117,1.0)==.64)
ok('manual letter identifiers never collide through the formal-bill parser',
   BR.evidence_key('LTR-A')!=BR.evidence_key('LTR-B') and
   BR.evidence_key('H.R. 1')==BR.evidence_key('H.R.1'))
_elite={'role_type':'party_elite','influence_bonus':1.5}
ok('Influence bonus is limited to party elites',
   IR.eligible_bonus(_elite,'Banking (full) [Highly]')[0]==1.5 and
   IR.eligible_bonus({'role_type':'constitutional_officer','influence_bonus':1},'')[0]==0)
ok('committee Chair/Ranking overlap zeros the party-elite bonus',
   IR.eligible_bonus(_elite,'Finance (full) [Highly/Chair]')[0]==0 and
   IR.eligible_bonus(_elite,'Banking (full) [Highly/Ranking]')[0]==0)
print('[stance: fixed bands + bounded rounding]')
ok('same thresholds in every domain; only top label changes',
   SR.classify(.70,True,False)=='Champion' and SR.classify(.70,True,True)=='Defender' and
   SR.classify(.34,True,False)=='Highly Persuadable' and SR.classify(-.11,True,True)=='Lean Skeptic')
ok('all ten fixed tiers are reachable without a curve',
   [SR.classify(x) for x in (.70,.35,.25,.15,.05,0,-.10,-.20,-.50,-.80)]==
   ['Champion','Ally','Highly Persuadable','Persuadable','Lean Persuadable','True Neutral',
    'Lean Skeptic','Skeptic','Highly Skeptical','Opponent'])
ok('positive scores cannot be skeptic and negative scores cannot be highly persuadable',
   all(SR.classify(x) not in ('Lean Skeptic','Skeptic','Opponent') for x in (.001,.099,.1,.69,1.0)) and
   all(SR.classify(x) not in ('Highly Persuadable','Ally','Champion') for x in (-.001,-.099,-.1,-.69,-1.0)))
ok('no-signal stance remains em dash',SR.classify(.99,False)=='—' and MC.overall_tier({})=='—')
ok('missing media does not dilute present sponsored-legislation evidence',
   SR.combine_evidence({'legislation':.86,'media':None,'vote':None},
                       {'legislation':.55,'media':.35,'vote':.10})==.86)
_near={'Examination & Supervision':'Champion','CU Capital Requirements':'Champion',
       'Technology & Emerging':'Champion','Fraud Prevention':'Champion','Charter Modernization':'Champion',
       'CFPB':'Champion','Member Business Lending':'Champion','Data Privacy & Cyber':'Ally',
       'Credit Card Mandates':'Ally','Mortgage & Housing':'Ally','Interchange':'Highly Persuadable',
       'Defense CU Issues':'Highly Persuadable','Payments':'True Neutral'}
ok('overall rounding is bounded; Tier 1 is sufficient for the Champion display crown',
   MC.overall_rounding(_near)==.05 and MC.overall_tier(_near)=='Ally' and
   MC.overall_display_tier({'x':'True Neutral'},1)=='Champion' and
   MC.overall_display_tier(_near,2)=='Ally' and
   dict((tier,cut) for cut,tier in MC.OVERALL_BANDS)['Ally']==3.0)
_ob=dict((tier,cut) for cut,tier in MC.OVERALL_BANDS)
ok('overall bands use the strict 2026-07-18 fixed ladder',
   _ob=={'Ally':3.0,'Highly Persuadable':2.0,'Persuadable':1.0,'Lean Persuadable':.4,
         'True Neutral':0.0,'Lean Skeptic':-.3,'Skeptic':-.75,'Highly Skeptical':-1.5} and
   MC.overall_tier_from_value(0)=='True Neutral')
_opp_probe={'opp%d'%i:'Opponent' for i in range(4)}; _opp_probe['hs']='Highly Skeptical'
ok('overall Opponent cutoff is ordered, provenance-visible, and reachable',
   MC.OVERALL_BANDS[-2][0]>MC.OVERALL_OPPONENT_CUTOFF and
   MC.overall_thresholds()['bands'][-1]['minimum']==MC.OVERALL_OPPONENT_CUTOFF and
   MC.overall_tier(_opp_probe)=='Opponent')
_ts=TU.legislative_press_scores(
    {'French Hill':{'leg':.9,'press':.8},'Daniel Meuser':{'leg':.998,'press':.414}},.30,.25)
ok('Legislative+Press tier preserves pillar magnitudes (no second percentile transform)',
   _ts['French Hill']>_ts['Daniel Meuser'] and abs(_ts['French Hill']-.47)<1e-12)
_adj,_fac=SR.exceptional_rounding({'a':.69,'b':.9,'c':-.8,'empty':0},{'a':1,'b':1,'c':1,'empty':0})
ok('exceptional rounding is bounded, sign-aware, and never fills empty domains',
   all(abs(v)<=SR.ROUNDING_CAP+1e-12 for v in _fac.values()) and _fac['empty']==0 and _adj['empty']==0)
print('[media: shared routing + signed normalization + General CU exclusion]')
ok('General CU subject words route to a real policy domain',
   MR.route_raw_categories('Credit unions help underserved rural communities')==['Field of Membership'])
ok('press target beats unsupported source tags',
   MR.press_routing_decision('Bean Social Security simplification bill',
                             'Bean Bill to Simplify Social Security Moves Forward',
                             ['Technology & Emerging Issues'],
                             'Social Security retirement ages')['categories']==[])
ok('media context guards block road, generic tax-exempt, and community-bank false positives',
   'Interchange' not in MR.route_raw_categories('new highway interchange and exit ramp') and
   'Credit Union Tax Status' not in MR.route_raw_categories('tax-exempt nonprofit organization') and
   MR.COMPETITIVE not in MR.route_raw_categories('visited a local community bank'))
_norm=MR.normalize_signed_extrema({'A':8,'B':2,'C':-10,'D':-2.5,'E':0})
ok('domain normalization preserves relative signed magnitude',
   _norm=={'A':1.0,'B':.25,'C':-1.0,'D':-.25,'E':0.0})
_general=MR.general_exclusion_ledger({'A':.8,'B':0,'C':-.4})
ok('residual General CU evidence is explicit and never projected into policy domains',
   set(_general)=={'A','C'} and all(not a['applied_to_policy_domains'] for a in _general.values()))
_if=TIE.fuse_stance('neutral','review','support',.9)
_ic=TIE.fuse_stance('support','unanimous','oppose',.99)
ok('image evidence resolves weak text but preserves clear-text conflicts for review',
   _if['stance']=='support' and _if['image_disposition']=='image-resolved' and
   _ic['stance']=='support' and _ic['image_disposition']=='conflict-review')
print('[identity: fail-closed joins]')
_ir=Resolver(['John Smith','Jane Smith','Tim Scott'],{'Tim Scott':'S001184'})
ok('identity resolver accepts exact, Last/First, and bioguide matches',
   _ir.resolve('Scott, Tim')=='Tim Scott' and _ir.resolve(None,'S001184')=='Tim Scott')
ok('identity resolver rejects an ambiguous surname instead of taking first roster row',
   _ir.resolve('Smith') is None and _ir.resolve('X. Smith') is None)
print('[core: rosters + network]')
mem=MC.members()
ok('533 members',len(mem)==533,len(mem))
prt=MC.party()
ok('party coverage (no ? beyond a handful)',sum(1 for m in mem if prt[m]=='?')<=5,sum(1 for m in mem if prt[m]=='?'))
# PIN TO CU. load_network() reads NET_CONFIG per call, so unscoped these gates bind whatever the
# ambient shell happens to carry and then report a pass for a graph they never meant to test. With
# five configs shipped that is a live hazard, and 'network symmetric' below would additionally
# FAIL under holistic_norm, which is DIRECTED by design -- a spurious red on a correct artifact.
_prev_nc=os.environ.pop('NET_CONFIG',None)
try:
    adj=MC.load_network()
finally:
    if _prev_nc is not None: os.environ['NET_CONFIG']=_prev_nc
ok('kernel network gates ran on CU regardless of ambient NET_CONFIG',
   MC._c.get('netfile') is not None and 'congress_cosponsor_network' in (MC._c.get('netfile') or ''),
   MC._c.get('netfile'))
ok('network symmetric',all(abs(adj[u][v]-adj[v][u])<1e-12 for u in list(adj)[:100] for v in adj[u]))
ok('no self-loops',all(u not in adj[u] for u in adj))
ok('all weights positive & sane',all(0<w<=10 for u in adj for w in adj[u].values()))
print('[core: physics]')
d,r=None,None
for a in mem:
    for b in mem[1:]:
        if prt.get(a)=='D' and prt.get(b)=='R': d,r=a,b; break
    if d: break
ok('cpf_diffusion cross-party dampened (<1), same-party 1.0',MC.cpf_diffusion(d,r)<1.0 and MC.cpf_diffusion(d,d)==1.0)
ok('cpf_pressure co-partisan 1.7 / out-partisan 0.5',MC.cpf_pressure(d,d)==1.7 and MC.cpf_pressure(d,r)==0.5)
Pk=MC.build_P(adj)
ok('cascade kernel probabilities in (0,1)',all(0<p<1 for u in Pk for p in Pk[u].values()))
print('[core: receptivity gate]')
gs=[MC.gate(m,{m:'Skeptic'}) for m in mem[:50]]+[MC.gate(m,{}) for m in mem[:50]]
ok('gate within [0.15, 0.90]',all(0.15<=g<=0.90 for g in gs),(min(gs),max(gs)))
ok('opponent gate < persuadable gate (same member)',MC.gate(mem[0],{mem[0]:'Opponent'})<MC.gate(mem[0],{mem[0]:'Highly Persuadable'}))

print('[money_response]')
def imp(n,f):
    s=importlib.util.spec_from_file_location(n,os.path.join(FEC,f)); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
mr=imp('mr','engine/money_response.py')
g='Jared Golden'
s1,s2,s4=mr.shift_lean(g,'cu',100000),mr.shift_lean(g,'cu',200000),mr.shift_lean(g,'cu',400000)
ok('saturating (concave) money curve',0<s1<s2<s4 and s2<2*s1 and s4<2*s2,(s1,s2,s4))
ok('bounded by MAXSHIFT x pressure x LOSSFRAME',abs(mr.shift_lean(g,'comp',10**9))<=mr.MAXSHIFT*1.60*mr.LOSSFRAME+1e-9)
ok('loss-frame asymmetry exact',abs(mr.shift_lean(g,'comp',100000)+mr.LOSSFRAME*s1)<1e-9)
ok('COMPET_MULT: shift_lean cm=1.0 is bitwise the old value; c*amount is the effective dollar on both legs',
   mr.shift_lean(g,'cu',100000,1.0)==s1 and mr.shift_lean(g,'cu',50000,2.0)==mr.shift_lean(g,'cu',100000) and mr.shift_lean(g,'comp',50000,2.0)==mr.shift_lean(g,'comp',100000))
ok('sub-threshold absorbed',mr.defection(g,-mr.DTHRESH*0.99) is None)
ok('defection outputs only valid rungs',all((mr.defection(m,-30) in (None,)+tuple(mr.ORDER)) for m in list(mr.C)[:100]))
ok('committed terminal rungs immune',all(mr.defection(m,-99) is None for m in mr.C if mr.PROJ.get(mr.OV.get(m),mr.OV.get(m)) in mr.COMMITTED))

print('[flip_model]')
fm=imp('fm','engine/flip_model.py')
n=fm.members[0]
kw=dict(R_n=fm.R[n],nA_n=1.0,nB_n=1.0,mAt=2.0,mBt=2.0)
p0=fm.pflip(0,0,**kw); p1=fm.pflip(50000,0,**kw); p2=fm.pflip(0,50000,**kw)
ok('pflip in (0,1)',all(0<x<1 for x in (p0,p1,p2)))
ok('CU money raises pflip',p1>p0)
ok('industry money lowers pflip',p2<p0)
ok('receptivity scales money channel (pm=2 amplifies CU effect)',fm.pflip(50000,0,pm=2.0,**kw)>p1)
# COMPET_MULT: the default cm=1.0 is a bitwise no-op on pflip (dial OFF == the pre-dial arithmetic) and c sits on BOTH money legs
ok('pflip default cm=1.0 is a bitwise no-op; c lifts both legs (equal money stays 0.5)',
   all(fm.pflip(cu_,ind_,pm=pm_,**kw)==fm.pflip(cu_,ind_,pm=pm_,cm=1.0,**kw) for cu_ in (0,5000,50000.5) for ind_ in (0,40000) for pm_ in (1.0,0.9,1.2))
   and fm.pflip(0,0,cm=1.7,**kw)==fm.pflip(0,0,**kw) and fm.pflip(30000,30000,cm=1.83,**kw)==fm.pflip(30000,30000,cm=1.0,**kw)==fm.pflip(30000,30000,cm=1.2,pm=0.9,**kw)==0.5
   and fm.pflip(50000,0,cm=2.0,**kw)>p1 and fm.pflip(0,50000,cm=2.0,**kw)<p2)
ok('COMPET_MULT default ON: every member c is the validated artifact value in [1,2] and fm.CM carries it (COMPET_MULT=0 would give exactly 1.0)',
   MC.compet_on() and all(1.0<=v<=2.0 for v in fm.CM.values()) and fm.CM[n]==MC.cmult(n) and any(v>1.0 for v in fm.CM.values()))

print('[say-do gate + delivery discount]')
# Cap law invariants on a synthetic member. Committee office is intentionally not an action path.
MC._CORR={'__test__':{'sponsored':{'CFPB'},'advanced':{'CFPB'}}}
MC._VOTECORR={'__test__':{'Payments'}}
_cm={'CFPB':'Champion','Payments':'Champion','Interchange':'Defender','CU Tax Status':'Champion','TCPA':'Skeptic'}
_g,_cap,_prov,_cor=MC.cap_uncorroborated('__test__',_cm)
ok('authored Champion survives + earns proven (Path A)',_g['CFPB']=='Champion' and _prov==['CFPB'] and _cor.get('CFPB')=='A')
ok('recorded floor vote corroborates (Path V)',_g['Payments']=='Champion' and _cor.get('Payments')=='V')
ok('committee title does not substitute for action',_g['CU Tax Status']=='Ally' and 'CU Tax Status' in _cap)
ok('uncorroborated top cells cap to Ally, raw preserved',
   _g['Interchange']=='Ally' and _cap=={'Interchange':'Defender','CU Tax Status':'Champion'})
ok('cap touches nothing else',_g['TCPA']=='Skeptic')
# Current overall Champion floor is 3.65. Use equal-weight synthetic domains so the gated mix remains
# a Champion while the aggregate delivery coverage is below 2/3 (5/8): this isolates the overall cap.
MC._CORR['__test__']={'sponsored':{'top%d'%i for i in range(1,6)},'advanced':set()}
MC._VOTECORR['__test__']=set()
_raw8={'top%d'%i:'Champion' for i in range(1,9)}
_g8,_c8,_p8,_r8=MC.cap_uncorroborated('__test__',_raw8)
_ov,_od=MC.overall_say_do_gate('__test__',_g8,_raw8,_r8)
ok('overall Champion requires delivery in at least two-thirds of raw top cells',
   _od['raw']=='Champion' and _ov=='Ally' and _od['delivery_ratio']==.625)
# Four of six equal-weight top domains delivered: gated mean remains above the current Champion floor
# and the exact 2/3 aggregate coverage clears.
MC._CORR['__test__']={'sponsored':{'top%d'%i for i in range(1,5)},'advanced':set()}
_raw6={'top%d'%i:'Champion' for i in range(1,7)}
_g3,_c3,_p3,_r3=MC.cap_uncorroborated('__test__',_raw6)
_ov3,_od3=MC.overall_say_do_gate('__test__',_g3,_raw6,_r3)
ok('six top domains with two-thirds delivered clear the aggregate say-do rule',
   len(_od3['top_cells'])==6 and _od3['delivery_ratio']>=2/3 and _ov3=='Champion')
MC._CORR['__test__']['direct_sponsored_bills']={('H.R.%d'%i,'119') for i in range(10)}
MC._CORR['__test__']['advanced_direct_bills']={('H.R.0','119'),('H.R.1','119')}
_ov4,_od4=MC.overall_say_do_gate('__test__',_g3,_raw6,_r3)
ok('fixed high-volume low-followthrough rule caps an introduced-only paper trail',
   _ov4=='Ally' and 'high_volume_low_followthrough' in _od4['cap_reasons'])
MC._CORR['__test__']['direct_sponsored_bills']={('H.R.%d'%i,'119') for i in range(20)}
_r5={c:'A' for c in _raw6}
_ov5,_od5=MC.overall_say_do_gate('__test__',_raw6,_raw6,_r5)
ok('exceptional direct authorship with full domain delivery clears the paper-trail cap',
   _ov5=='Champion' and _od5['exceptional_authorship_override'] and not _od5['cap_reasons'])
# B-1, 2026-09-15: the CELL gate no longer fails open on an empty accepted-evidence map.
# bill_corroboration() now RAISES on a missing/unadjudicated source (routing-schema check, sentinel
# live-pillar check, and the tracked 'refusing to disable the say-do gate' guard), so an empty return
# means "loaded, corroborates nobody" = no authorship, not a missing input. The AGGREGATE gate still
# fails open on the same sentinel (overall_say_do_gate's `available=bool(corr)`). Both halves of that
# asymmetry are asserted here on purpose. See audits/members_section_20260914/evidence/a1-b1-resolution.md.
MC._CORR={}   # empty accepted evidence -> cells fail CLOSED; the overall gate still fails open
_g2,_cap2,_,_=MC.cap_uncorroborated('__test__',_cm)
_ov2,_od2=MC.overall_say_do_gate('__test__',_g2,_cm,{})
ok('empty accepted evidence caps every top cell but leaves the overall gate open',
   _cap2=={'CFPB':'Champion','Payments':'Champion','Interchange':'Defender','CU Tax Status':'Champion'}
   and all(_g2[c]=='Ally' for c in _cap2) and _g2['TCPA']=='Skeptic'
   and _ov2==_od2['raw'] and _od2['final']==_od2['raw'] and _od2['cap_reasons']
   and not _od2['evidence_available'],(_g2,_cap2,_od2))
MC._CORR=None; MC._VOTECORR=None   # restore lazy loaders
MC._DELIV={'talker':4.0,'mild':1.5,'doer':-2.0}
ok('delivery_mult bounds [0.70,1.00] + neutral fallback',
   MC.delivery_mult('talker')==0.70 and abs(MC.delivery_mult('mild')-0.925)<1e-9
   and MC.delivery_mult('doer')==1.0 and MC.delivery_mult('unknown-member')==1.0)
MC._DELIV=None

print('[CHRG attribution + ordinary-questioning guards]')
cr=imp('cr','collect/fetch_congressional_record.py')
_hearing='''OPENING STATEMENT OF HON. FRENCH HILL, A REPRESENTATIVE
Chairman Hill. This opening statement is deliberately long and must never enter the ordinary question-period corpus even though it resembles member speech in every mechanical way.
STATEMENT OF SUSANNAH MARSHALL, BANK COMMISSIONER
Ms. Marshall. This witness testimony is deliberately long and must never be attributed to a current member who happens to have the same surname as this witness.
[The prepared statement of Ms. Marshall follows:]
Chairman Hill. I recognize myself for five minutes for questions. This is the live question-period member turn and it is deliberately long enough to clear the procedural-fragment floor. Ms. Marshall, what do you think about this policy and how should Congress respond to it?
Ms. Marshall. This witness answer is deliberately long enough that only the explicit witness-surname guard, rather than the length threshold, can prevent its admission to the member corpus.
QUESTIONS SUBMITTED BY MEMBERS POST HEARING
Chairman Hill. This written question is deliberately long but belongs to the post-hearing appendix and therefore must not enter the live question-period corpus.'''
ok('comma direct-address is not a speaker label',
   not any(m.group(2)=='Marshall' for m in cr.TURN.finditer('    Ms. Marshall, what do you think?')))
ok('opening member statement is not classified as witness',
   'HILL' not in cr.witness_surnames(_hearing,'house',119))
ok('same-surname outside witness is blocklisted',
   'MARSHALL' in cr.witness_surnames(_hearing,'house',119))
_mem=__import__('sqlite3').connect(':memory:')
_mem.execute('CREATE TABLE speeches(member,date,source,chamber,package,nwords,text)')
cr.parse_pages([('synthetic.htm',_hearing)],lambda _:'house','2025-02-05','CHRG',
               'CHRG-119hhrg99999',_mem.cursor(),congress=119)
_rows=list(_mem.execute('SELECT member,chamber,text FROM speeches')); _mem.close()
ok('only live member questioning survives hearing parser',
   len(_rows)==1 and _rows[0][0]=='French Hill' and _rows[0][1]=='house' and
   'live question-period' in _rows[0][2] and 'opening statement' not in _rows[0][2].lower() and
   'written question' not in _rows[0][2].lower(),_rows)
ok('joint package scope is explicit',cr.chrg_scope('CHRG-117jhrg12345')==(117,'joint'))

print('[audio speaker-identity layer: module suites (design 2026-07-16, user-directed)]')
# Each identity module ships its own plain-assert suite (enroll+verify2, diarize, align, attrib,
# hearings-media map). They are registered here as subprocesses so the kernel gate covers them
# without loading external model weights or touching the network. The Wav2Vec stream mocks and
# paper-exact neural-head fixture use local CPU torch; speechbrain/faster-whisper remain unnecessary.
import subprocess as _sp,re as _re
import bounded_subprocess as _bounded
# test_lda_rebuild is LARGE (247 tests, ~250KB of test code) and materially increases this
# runner's wall-clock. It is registered here deliberately because it was broken in two places
# for an unknown period while this runner reported 89/0 green. A suite no runner invokes is
# indistinguishable from a deleted one.
_LANE_MODULES=('test_audio_calibrate','test_audio_identity','test_audio_linear_head',
               'test_audio_neural_head',
               'test_audio_diarize','test_audio_align','test_audio_attrib','test_hearings_media',
               # Registered 2026-09-13 (v2 batch 2): existed with 13 tests, appeared in NO lane
               # tuple, so no runner ever invoked it. Its one live-artifact test self-guards with
               # skipTest. test_big5_gold_audio_tdd is deliberately NOT registered: its setUp
               # unconditionally reads three live artifacts (big5_gold_anchored.json,
               # profile_basis_v2.json, member_desk_bundle.json) with no skip guard, so every test
               # in the class errors when an artifact is absent (VERIFY_AUDIOB.md). Run it
               # standalone against the live tree first.
               'test_cspan_identity',
               'test_bill_rules_routing',
               # BUILD A/B refresh producers: the BILLSTATUS bulk refresh and the hash-gated
               # forecast chain step, plus the compare_inputs function audit_full.py's
               # cosponsor_forecast_freshness check shares with that step's gate.
               'test_cosponsor_forecast_refresh',
               'test_bill_audit_console_resolution',
               'test_bill_archive_provenance',
               'test_bill_collision_guards',
               'test_b5_onc_successor',
               'test_b5_o_coverage_recovery',
               'test_primacy_tier',
               # The per-area board (D-031) that test_primacy_tier now calls the
               # revert switch of. It shipped 2026-09-16 in NO lane tuple, so the
               # kernel never ran its 32 pins -- the same "a suite no runner
               # invokes is indistinguishable from a deleted one" reasoning as
               # test_lda_rebuild and path4_parity below.
               'test_per_area_board',
               'test_primacy_diagnostic',
               'test_primacy_numeric_guards',
               'test_primacy_constraint_composition',
               'test_primacy_affirmative',
               'test_bill_seed_identity',
               # D4/D5 (packet audits/members_section_20260914). CROSSPOST_DEDUP
               # moves an ENGINE input (the media blend written into
               # 'Press Release - Raw'), and DESK_RESPONSIVENESS retires a desk
               # section and a chain step; both carry a revert switch that these
               # suites pin against live evidence.
               'test_crosspost_scoring',
               'test_desk_responsiveness_dial',
               # A5/N2 (Paul-approved 2026-09-19, packet
               # audits/laundry_list_closeout_20260918 FINDINGS.md A5 and A3). Both
               # are KEY changes behind a revert switch whose other position must be
               # an exact identity, so each suite pins the legacy key byte for byte
               # AND pins the key that must NOT move -- canonical_url for A5 (every
               # press join reads it) and normalized_text for N2 (the
               # tweet_stance_final join). Registered beside the other dial suites.
               # Synthetic fixtures only: no corpus, no workbook, no artifact.
               'test_press_url_canon',
               'test_crosspost_key',
               'test_crosspost_key_engine_wiring',
               # The reference layer's three admission filters (Paul-approved 2026-09-19,
               # packet audits/laundry_list_closeout_20260918 FINDINGS.md B4/B5/B6): the
               # PRESS own-domain gate, the roll-call/signature-block exclusion, and
               # identical windows counting once. Registered here beside the other dial
               # suites because each filter carries a revert switch whose '0' position must
               # stay an exact identity, and because the producer cannot be imported --
               # member_reference_layer.py runs its whole body and writes the live tree, so
               # the rules live in the pure fec/members/reference_filters.py and the wiring
               # is pinned as text. Synthetic fixtures only: no roster, no db, no artifact.
               'test_reference_admission_filters',
               'test_interview_turn_tags',
               # N1a (Paul-approved 2026-09-19, packet
               # audits/laundry_list_closeout_20260918 FINDINGS.md A1): the member desk
               # now reads the same press stream as the ranking engine, unioned with the
               # workbook sheet. DESK_PRESS_SOURCE=sheet must restore the pre-change
               # input byte for byte, so the suite pins both dial positions, the release
               # identity the union keys on (A5's), the field map, and the ledger that
               # keeps the desk's press census closed. Registered beside the other dial
               # suites. The producer cannot be imported -- import == execution:
               # make_member_desk.py runs its whole body and overwrites the live desk --
               # so the rules live in the pure fec/surfaces/desk_press_source.py and the
               # wiring is pinned as text. Synthetic fixtures only: no workbook, no
               # corpus, no artifact.
               'test_desk_press_source',
               # The shared line-wise cutter fec/tools/live_patch.py (Paul-approved 2026-09-19,
               # "Reconcile those loose ends and apply"), written because the 09-19 census found
               # 312 of 2,892 tracked text files MIXED (main 35521f9aa). Pins the LF-only line
               # split (str.splitlines also breaks on FF and U+2028), per-line anchors that must
               # hit exactly once, the majority terminator for new lines, and byte-exact proof
               # through `git apply` in a temp dir outside every repository. Synthetic bytes only.
               'test_live_patch',
               # The shared bounded launcher fec/bounded_subprocess.py, written because the
               # 09-19 stall had nowhere else to be fixed: a suite's child froze inside
               # `import scipy` and held this runner for 14 minutes, and the chain's own launch
               # would have held the pipeline lock the same way. Pins the DE-026 shape, the
               # process-tree kill, the two measured bounds, and both call sites. Synthetic
               # children only: no producer runs, no lock is taken, no chain event is emitted.
               'test_bounded_subprocess',
               # Control-command bounds of fec/compute_engine.py (image resolution, container stop/rm) on
               # the bounded runner, and run_bounded rejecting NaN/infinite timeouts. Mocked: no process starts.
               'test_compute_control_bounds',
               # The refresh TICK runner (fec/refresh_tick.py + refresh_table + refresh_state),
               # written because fec/weekly_refresh.py is one 49-step run that either finishes
               # or is lost and has not finished since 2026-07-19 on a box that dies about every
               # two days. The suite crashes the runner at every commit point it declares, kills
               # it from outside mid-step and with CTRL_BREAK, corrupts its state, breaks its
               # status marker and steps the clock backwards under its writer lease, then
               # asserts the NEXT fire converges to a publication whose output bytes equal an
               # uninterrupted control run's. It also pins the 49-row step table against
               # weekly_refresh.py by AST, so the two cannot drift while both exist. Stub
               # producers in temp roots with MONEYBALL_ROOT and GLASSBOX_EVENTS_DIR redirected:
               # no producer runs, nothing is written under the repository, and the last test in
               # the file is the negative control for exactly that.
               'test_refresh_tick',
               # Behavior-qualified honors: rank/evidence authority, worksheet
               # isolation, both public renderers, and matched-generation export.
               # The desk Python suite also runs its seven Node rendering tests
               # with a checked nonzero test count; none are definition-only gates.
               'test_rank_honors',
               # Rule G's full field gauntlet and the unified vote direction,
               # each with its legacy dial pinned in the same suite.
               'test_rank_honors_ladder_g',
               'test_vote_direction_unified',
               'test_overall_stance_policy',
               'test_audit_rank_honors',
               'test_honors_workbook',
               # The workbook binding's SCOPE (2026-09-18). The binding used to be a
               # whole-file hash, so an edit to any sheet the publication never wrote
               # -- Committee Data, which is Paul's -- refused --prepare-model-update
               # and blocked the chain; it cost this lane two manual rebases. This
               # suite pins the region scope, the by-name refusal that must still
               # fire on an edit INSIDE an honors view, and HONORS_WORKBOOK_SCOPE=legacy.
               'test_honors_workbook_region',
               'test_dashboard_honors',
               'test_member_desk_honors',
               'test_honors_publication',
               'test_zip_compression',
               'test_voteview_party_map',
               'test_coverage_claim_text',
               'test_findings_causal_claims',
               'test_lda_rebuild',
               # Outside-firm fees and in-house expenses are two series, never a total
               # (2026-10-09, ruling D-064): loader rows, server-rendered cards, and the
               # shipped industry-ui.js under node:vm. Synthetic fixtures only; the
               # overlay layer SKIPs clean (still OK) where node is absent.
               'test_industry_series_not_summed',
               # Demographic expansion (Phases A-H), v2 batch 2 (2026-09-13). These four suites
               # existed but were in NO lane list, so this runner -- which selects by explicit
               # lane, never by the discovery walk -- never invoked them. Same reasoning as
               # test_lda_rebuild and path4_parity above: a suite no runner invokes is
               # indistinguishable from a deleted one.
               'test_acs_community',
               'test_demographic_potential',
               # D3 (audits/members_section_20260914/phase2/design-d2-d5.md): the
               # DEMO_POTENTIAL footprint dial. Registered beside the suite it
               # parameterises -- test_demographic_potential now pins growth_room
               # explicitly, this one pins the new footprint default.
               'test_demographic_potential_footprint',
               # D-034 fix-up: the desk POSTFLIGHT must follow the same dial.
               # dashboard_integrity.py asserted the growth-room sticker shape
               # unconditionally and killed the chain inside build_primacy_surfaces
               # after 3831.7 s (phase2/chain_run_20260917e.log). Both modes are
               # driven here, with a negative control pinning the four retired
               # predicates so a deletion cannot masquerade as a fix.
               'test_dashboard_integrity_demographic_potential',
               'test_hmda_transport',
               'test_dashboard_adjustments',
               # Committee score became a chain producer 2026-09-18 (engine/committee_scores.py).
               # Pins the 2026-06-30 weights, the deleted 'Small Business' override, the now-fatal
               # bracketless seat token, and a live-data control that the shipped workbook already
               # agrees with its own seat text -- the gate the producer exists to hold.
               'test_committee_scores',
               'test_lobbyview_network',
               'test_lobby_interactions',
               # holistic v1 (MEA-8 registration, merge 2026-08-17): the pure COR-1 fixture
               # suite always runs; the config battery certifies the pinned 2026-08-17
               # artifact bed and SELF-RETIRES with a visible SKIP-CLEAN when the families
               # move past its pins (guard at the top of the file).
               'test_holistic_base',
               'test_holistic_config',
               # staffer-network Phase 0 contracts (2026-08-29): typed ledger/schema,
               # uncertain intervals, A-0/A-1/A-2 manifests, immutable generation
               # publication, disabled-path isolation, and authority-gated ingest.
               'test_staffer_schema',
               'test_staffer_intervals',
               'test_staffer_source_manifest',
               'test_staffer_publication',
               'test_staffer_isolation',
               'test_staffer_ingest_contracts',
               'test_staffer_littlesis_contract',
               'test_staffer_source_native_corpus',
               'test_staffer_documents',
               'test_staffer_phase0_gates',
               'test_staffer_politico_normalize',
               'test_staffer_politico_adjudication',
               'test_staffer_politico_adjudication_run',
               # Part-A library suite, v2 batch 2 (2026-09-13): the employment-episode /
               # temporal-sidecar suite existed but was never registered, so no runner invoked
               # it. Self-inserts fec/ on sys.path and prints a native verdict shape. (Its
               # siblings test_staffer_name_standard and test_lda_employment are already
               # registered below, each under its own dedicated entry.)
               'test_staffer_employment_history',
               # NAME STANDARD (Paul-ruled 2026-09-06): one parser for every
               # person-name source. Registered here because the standard is what
               # the spine re-key and every cross-source join will be measured
               # against -- an unregistered parser suite is a standard nobody
               # checks. Carries 5,248 REAL raw House SOD CSV strings with the
               # spine builder's own key pinned beside each, so a drift in either
               # the parser or build_hill_personnel_spine.payroll_payee_key goes
               # red. tmp-free, read-only, ~0.6s.
               'test_staffer_name_standard',
               # dashboard Staffer Network section producer (2026-09-04). The producer is
               # receipt-gated: it refuses unless the v4 verification receipt is PASS and every
               # byte it reads matches BOTH the generation receipt and the verification
               # receipt's own independent record. This suite mints a synthetic generation and
               # proves each refusal actually fires (status not PASS, a recorded failure, a
               # canonical promotion, a tampered artifact, a wrong receipt hash, a missing
               # field, a broken artifact join, the size cap) alongside the happy-path counts,
               # the shared-staff language rule, and byte-level determinism. Registered rather
               # than left to the discovery walk because this runner selects by explicit lane,
               # so an unregistered suite is never invoked.
               'test_staffer_network_section',
               # Staffer GROUPS section (2026-09-07), owner ruling D-022: organisations,
               # offices and policy areas leave the people network and become a flat
               # directory. The suite holds the three rules the ruling turns on -- only
               # people are ever listed as people (negative control: a container id
               # arriving where a person id belongs must REFUSE, not print an office as a
               # staffer), each person once per group, and current people first -- plus the
               # cap and its remainder, the byte-cap refusal, receipt binding for the
               # corroboration sidecar and the LDA corpus, byte-level determinism, and the
               # LDA status vocabulary pinned character for character because it is what a
               # non-technical reader is shown. SPLIT 2026-09-07: the producer now writes
               # THREE artifacts -- staffer_offices_section.json and staffer_organisations_
               # section.json, because one combined document measured 8,285,129 bytes on the
               # real generation against a 5,000,000 pin, and then staffer_clients_section.
               # json, because the client half of the employment ruling measured 21,012,744
               # bytes written as cards and the threshold that fit shipped 203 of the 41,203
               # client organisations the ruling reaches. That third artifact is NORMALIZED
               # -- each name written once, each tie a pair of positions -- which holds all
               # 41,203 clients and all 132,738 filer-client ties in 3,892,152 bytes, and
               # the suite's conservation checks are what make that a measurement rather
               # than a claim: every client present, every tie present, nothing dropped,
               # every tie index resolving, and a negative control where a dangling index
               # must refuse. The suite covers all three parts, the all-or-nothing cap on
               # --part both, the single-filer name-only line and its count-only fallback,
               # and BOTH generation shapes -- v5 containers from the node scores, v6
               # containers from staffer_social_groups.ndjson.gz. Its last case builds the
               # REAL generation and asserts all three under the pin; it SKIPs clean where
               # the generation or the machine-local LDA corpus is absent.
               'test_staffer_groups_section',
               # THE EMPLOYMENT RULING (Paul, 2026-09-07): "For the filer, you do what is
               # chronologically the most recent. If it appears they are contract/mercenary
               # they are tied to all those organizations as clients."
               # fec/staffer_network/lda_employment.py is the ONE place that sentence is
               # code, and THREE surfaces read it -- the static Organisations payload above,
               # the sidecar Groups tab below, and the plain-words status table -- so a
               # defect there is a defect on three surfaces at once. Registered separately
               # from the producer suite because the parts that matter most are the ones an
               # integration test cannot reach: the REFUSALS (an unknown filing period is
               # never sorted by guess; activities that disagree about their own filing are
               # never resolved by picking a winner; the filing table's packing refuses to
               # overflow rather than silently moving somebody's employer), all four rungs
               # of the tie ladder including the one that falls through to the registrant
               # id, and the name normalisation the whole contract/in-house split rests on.
               # Synthetic corpus in a tmpdir, no machine-local data, ~0.2s.
               'test_lda_employment',
               # The 8841 sidecar's Groups tab is a SECOND implementation of one ruled
               # question -- which containers are offices -- and until 2026-09-07 it
               # answered differently from the producer above: it whitelisted 'admin'.
               # Administrative payroll headers are not workplaces (18 of them carry
               # current people on the real generation and would otherwise ship as
               # offices), so the two lists are now held equal by import here, and the
               # exclusion is driven end to end with a negative control that re-widens the
               # scope and proves the fixture would have produced the office without it.
               'test_staffer_groups_index',
               # Staffer Network SURFACE (2026-09-04), the section producer's two consumers.
               # test_make_staffer_network_html renders the real template from a synthetic
               # section and holds the four things make_combined.extract() silently depends on
               # (one <style>, one bare <script>, no second script in the body, every id
               # prefixed stf-), plus the no-external-loads contract and the owner language law
               # -- the zero-overlap rejection label, the "not ties between members" legend, and
               # a banned-vocabulary scan with two counted carve-outs. test_combined_staffer_
               # section reads make_combined.py as TEXT (importing it rebuilds a 76 MB artifact)
               # and asserts the panel, the layer + all:revert reset, every controller arm
               # including popstate and initial load, the three new id-collision pairs, the
               # staleness guard, and that the retired 8841 <script src> hook cannot come back.
               'test_make_staffer_network_html',
               'test_combined_staffer_section',
               # EMAIL SPLIT (2026-09-05). The fourth surface pushed the all-in-one attachment
               # from 14,167,350 to 14,808,817 bytes against a 14,680,064 budget, make_email_
               # package.py refused the package, and the chain halted before the audit. The fix
               # splits the deliverables: make_combined.py now emits Moneyball.html (four
               # surfaces, the file-open/live deliverable) AND Moneyball_Email.html (three,
               # emailed under the arcname Moneyball.html). test_combined_email_variant lifts
               # assemble() out of the builder's AST and execs it in an EMPTY namespace -- which
               # is what proves it pure -- then pins assemble(pieces,True) against a hand
               # transcription of the pre-refactor concatenation so the four-surface document
               # cannot drift silently, and pins the variant NEGATIVELY as that document minus
               # exactly three chunks. test_email_package_variant proves the zip is built from
               # the email variant and not from a Moneyball.html sitting beside it, that the
               # receipt hash-binds the SOURCE (the join audit_full.py re-checks), and that the
               # budget check still fires -- with a negative control, because "it raised" on its
               # own proves nothing about the budget.
               'test_combined_email_variant',
               'test_email_package_variant',
               # identity-collision fixes (Paul-ordered 2026-08-18). The pre-existing inline
               # Resolver assertions below use a 3-name toy roster in which none of the three
               # real failure modes is reachable -- they passed continuously while the shipped
               # Resolver returned Johnson -> Julie Johnson and Michael Barr -> Andy Barr
               # against the live 533. These two suites test against the REAL roster.
               'test_member_identity_roster',
               'test_hearings_chamber_scope',
               # rendered-desk audit (2026-08-27). Every gate we had validated the member desk's
               # BUNDLE; twice on 08-26 the desk shipped a view in which a module-scope index was
               # built over a DESK_SPLIT-deferred field and froze empty, while
               # dashboard_integrity.py reported "pass, bills: 5899" against a page showing 0 of
               # 464. This suite EXECUTES the shipped html + profiles under a DOM stub and reads the
               # indexes back, with five negative controls. SKIP-CLEAN on a fresh clone (surfaces
               # not built, or node absent), so it costs an unbuilt tree nothing.
               'test_rendered_desk',
               # containerization phase 0 (2026-08-26): the planted-defect battery behind
               # audit_full's [SYS] manifest-vs-disk section. Each control breaks exactly one
               # thing in a temp fixture and proves the corresponding assertion goes red for the
               # stated reason. Registered here for the same reason test_lda_rebuild is: a suite
               # no runner invokes is indistinguishable from a deleted one.
               'test_system_manifest_check',
               # incremental press collection (Paul-ordered 2026-08-30). The fold is the only
               # thing that writes the production URL baseline the anti-join trusts, so its
               # safety properties are gates, not documentation: an index row with no stored
               # body must never enter (it would be excluded forever with its text never
               # collected), an OFFICIAL_MENTION slice must not vouch for its host page, a
               # second fold must add zero rows, and a schema mismatch or a row-count decrease
               # must refuse and restore from the backup. Carries the Rscript-verified
               # canonical_url() equivalence table -- the fold's keys must match the R
               # anti-join's or a folded row would not actually be excluded next run.
               'test_press_baseline_fold',
               # The two suites the 2026-09-19 press repair added. Tombstones: a body URL the
               # walk has permanently given up on is recorded once, so revisits stop starving
               # behind it and a week's press is no longer refused over URLs that can never
               # be reached. Zero-row review: a member whose site goes silent is NAMED in the
               # receipt instead of disappearing into a zero that sums correctly. Both are
               # registered here rather than left unregistered, on the same reasoning as
               # test_lda_rebuild above -- a suite no runner invokes is indistinguishable
               # from a deleted one, and these two guard the fetched-evidence rule that
               # decides what may enter the production URL baseline.
               'test_press_pending_tombstones',
               'test_press_zero_row_review',
               'test_staffer_surface_import_modes',
               # staffer social network v4, PERSONS ONLY (D-022, 2026-09-06). The whole
               # point of the rework is a structural guarantee, and a structural guarantee
               # nothing checks is a comment: the scored matrix must contain people and
               # nothing else, so no container, organisation or policy area can ever again
               # draw restart mass a person did not -- the kind-balanced restart gave 36
               # policy nodes what 144,693 people got, and the 2026-09-05 audit measured
               # the resulting 11.6x inflation. This suite mints a synthetic input world
               # and gates: non-overlapping same-office pairs get NO edge and are counted
               # as rejected (D-019); node_scores and the edges file are person-only; every
               # grouping-layer row declares operator_included False; office_diversity is
               # Simpson over OFFICES, hand-checked, with the retired bridging_diversity
               # field absent; the restart is uniform over persons; a Politico bridge bound
               # to a different spine generation is REFUSED without --spine-id-map and fails
               # loudly on an unmapped id. It also carries a negative control proving the
               # verifier's naming law still fires on the new generic artifact names.
               'test_staffer_social_persons_only',
               # LONG-FORM AUDIO lane (fec/tests/speech/**), v2 batch 2 (2026-09-13). Of the
               # eleven speech suites that were unregistered (this runner selects by explicit
               # lane, and the only documented alternative -- data/speech/
               # CONGRESS_SPEECH_DATA_GUIDE_20260831.md:319, 'python -m unittest discover -s
               # fec/tests/speech' -- cannot collect the pytest-style modules at all), these two
               # insert fec/ on sys.path themselves and are registered here.
               # test_descriptive_contract_and_surfaces is deliberately NOT registered in this
               # pass; held out pending its own check.
               'test_descriptive_speech',
               'test_materialize_legacy_exact_windows',
               # S-2 census (decision F-3, 2026-10-11): every git-tracked fec/tests suite is
               # named here or on that suite's BACKLOG with a reason, and BACKLOG may only shrink.
               'test_kernel_registration_census',
               # S-2 drain (2026-10-11): pytest exits on these at collection; as scripts they
               # print the native 'N passed, 0 FAILED' shape. Ran green alone from LIVE.
               'test_opposition_prior_scope','test_contested_bills',
               # Census follow-up 2026-10-11: reads the live workbook's Normalized sheet (all
               # values, no formula cache) and now prints the native verdict shape. ~4 s.
               'test_legislative_alignment')

# Read the suites' five native verdict shapes here instead of editing 39 test files: fewer
# edits, zero risk of changing a test's meaning, and the change is fully reversible in one file.
def _suite_verdict(_text):
    # Ordering is load-bearing: shape A must claim "22 passed, 2 failed" first,
    # and shape B must retain the unittest result before pytest's independent tokens.
    _m=_re.search(r'(\d+) passed, (\d+) failed',_text,_re.I)
    if _m:
        return int(_m.group(1)),int(_m.group(2))
    _ran=_re.search(r'Ran (\d+) tests?',_text)
    if _ran:
        _total=int(_ran.group(1))
        if _re.search(r'^OK\b',_text,_re.M):
            return _total,0
        _failed_line=_re.search(r'^FAILED\b[^\r\n]*',_text,_re.M)
        if _failed_line:
            _failures=_re.search(r'failures=(\d+)',_failed_line.group(0))
            _errors=_re.search(r'errors=(\d+)',_failed_line.group(0))
            _failed=(int(_failures.group(1)) if _failures else 0)+(int(_errors.group(1)) if _errors else 0)
            return _total-_failed,_failed
        return None
    _passes=_re.findall(r'^PASS \S',_text,_re.M)
    if _passes:
        return len(_passes),0
    # Gate pytest's independent outcome tokens on its timed summary signature so they
    # cannot shadow any native shape above.
    if _re.search(r'\b(?:passed|failed|error|errors)\b[^\n]*\bin\s+\d+(?:\.\d+)?s\b',_text,_re.I):
        _pytest_passed=_re.search(r'(\d+) passed',_text,_re.I)
        _pytest_failed=_re.search(r'(\d+) failed',_text,_re.I)
        _pytest_errors=_re.search(r'(\d+) error(?:s)?\b',_text,_re.I)
        return ((int(_pytest_passed.group(1)) if _pytest_passed else 0),
                (int(_pytest_failed.group(1)) if _pytest_failed else 0)+
                (int(_pytest_errors.group(1)) if _pytest_errors else 0))
    _m=_re.search(r'(\d+)\s+\S+.*?\btests?\s+passed\b',_text)
    if _m:
        return int(_m.group(1)),0
    return None

# The original 39 were verified before registration: run in isolation, 0 of 59 mutated the tree,
# and the tree was byte-identical to its pre-flight snapshot afterwards.
_LANE_LEGISLATION=(
    'test_accepted_legislation_cross_producer_contract','test_assemble_approved_legislation_generation',
    'test_audit_billstatus_snapshot_divergence','test_audit_legislation_decay_propagation',
    'test_build_accepted_legislation_generation','test_build_accepted_legislation_workbook',
    'test_build_billstatus_relevance_sidecar','test_build_billstatus_review_sidecar',
    'test_build_legislation_identifier_probe_shards','test_build_legislation_membership_triage',
    'test_build_legislation_workbook_numeric_plan','test_build_partial_legislation_review_prefill',
    'test_build_partial_legislation_review_queue','test_compute_legislation_policy_scenarios',
    'test_extract_legislation_workbook_audited_layout','test_legislation_base_network',
    'test_legislation_computation_decision_template','test_legislation_direction_ledger',
    'test_legislation_measure_key_contract','test_legislation_shadow_consumer_plan',
    'test_legislation_shadow_input_closure','test_legislation_sidecar_downstream_isolation',
    'test_manage_legislation_junk_pile','test_materialize_legislation_base_and_bipartisan',
    'test_materialize_legislation_rollcall_aggregate','test_official_billstatus_rollcall_source',
    'test_refresh_billstatus_snapshot_evidence','test_review_legislation_candidate_memberships',
    'test_run_billstatus_bulk_enrichment','test_run_legislation_acceptance_service',
    'test_run_legislation_dashboard_member_desk','test_run_legislation_final_regression',
    'test_run_legislation_pillars_and_ranks','test_run_legislation_review_batches',
    'test_run_legislation_say_do_passes','test_run_legislation_second_pass_dependents',
    'test_run_legislation_timeout_rollover','test_run_legislation_timeout_rollover_watcher',
    'test_sponsor_column','test_sponsor_column_producer_wiring','test_stage_billstatus_bulk_enrichment',
    'test_archive_identity_billstatus','test_cu_placeholder_backfill')

# These use ``from fec import ...`` (namespace-package style), so they need the repo root
# on PYTHONPATH; one also imports fec siblings bare, so fec/ rides along. Registered with
# an env override rather than editing eight test files.
_LANE_LEGISLATION_PKG=(
    'test_audit_legfix5_artifact_gaps',
    'test_audit_legfix5_coverage_reconciliation',
    'test_audit_legislation_final_regression_currentness',
    'test_audit_legislation_query_specificity',
    'test_build_legislation_candidate_evidence',
    'test_build_legislation_culling_control_plane',
    'test_build_legislation_membership_decisions',
    'test_build_legislation_staged_integration_gate')

# Speech-lane unittest suites that import 'fec.speech.*' (namespace-package style) and so
# need the repo root on PYTHONPATH, exactly like _LANE_LEGISLATION_PKG above. v2 batch 2
# (2026-09-13).
_LANE_SPEECH_PKG=(
    'test_locked_exact_profile_gap',
    'test_source_media_recovery',
    'test_speaker_anchor_recovery',
    'test_voice_first_recovery')

# The first four need pytest (installed 2026-08-04, Paul-authorized); the next five define
# module-level tests but no runner block -- under plain python they exited 0 having executed
# NOTHING, the worst kind of green. One of those bare-tests suites also needs the namespace-package
# path. The tenth is the 117s shadow-recompute suite, registered deliberately at Paul's "fix all of
# those" despite the wall-clock. The last (2026-09-06) guards the supplemental adjudication overlay
# that sync_sentinel_bill_ledger.py reads ALONGSIDE the frozen 2026-07-28 ledger -- its central
# assertion is the negative control that an absent overlay leaves the plan byte-identical, which is
# only a gate if a runner invokes it. tmp_path-only, non-mutating.
# -p no:cacheprovider keeps the runner non-mutating.
# 2026-09-13 review, v2 batch 2: five more suites that guard this repo's collector/chain-step
# lane existed in fec/tests/ but were named by NO runner anywhere in the tree -- including the
# two that guard chain steps 11 and 12 (verify_collector_roster_updates.py,
# sync_sentinel_bill_ledger.py). A suite no runner invokes is indistinguishable from a deleted
# one, the same reasoning as path4_parity below. test_collector_roster_updates and
# test_sentinel_bill_workbook_sync use tmp_path fixtures; pytest runs the three
# unittest.TestCase suites unchanged. test_congress_advanced_pages_contract is deliberately NOT
# registered here: it reads three source_data/legislation/*.json files that are gitignored and
# absent from a fresh worktree, with no tmp_path/skip guard -- registering it would error most
# of its tests on FileNotFoundError in this tree and any fresh clone.
_LANE_PYTEST=(
    # S-2 drain (decision F-3, 2026-10-11): 176 suites that existed but no lane named. Each ran
    # green ALONE from LIVE (pytest, this lane's env, 300 s bound, one at a time) and wrote nothing
    # of its own into the tree. The suites that stayed red are on the census BACKLOG
    # (fec/tests/infra/test_kernel_registration_census.py) with a one-line reason each.
    'test_acquire_member_longform_audio', 'test_acu_bill_title_corroboration_query_plan',
    'test_adjudicated_corroboration', 'test_admission_membership_projection',
    'test_assess_member_behavior_evidence', 'test_audit_candidate_membership_review_partition',
    'test_audit_congress_identifier_failure_resolution', 'test_audit_failure_gate_ratified',
    'test_audit_fixes_20261001', 'test_audit_formula_cache_freshness',
    'test_audit_named_outline_recall_misses', 'test_audit_postrefresh_behavior_stance_semantics',
    'test_audit_postrefresh_rank_drift', 'test_audit_postrefresh_workbook_ooxml_semantics',
    'test_audit_quorum_recall_floor', 'test_augment_partial_review_prefill_structured_relevance',
    'test_automatic_sentinel_measurement', 'test_bill_impact', 'test_bill_milestones',
    'test_bill_refresh_publication', 'test_bounded_whisper_features',
    'test_build_bank_anchor_backfill', 'test_build_member_longform_additional_retry_queue',
    'test_build_member_longform_review_queue', 'test_build_member_longform_turn_features',
    'test_campaign_sites_2026', 'test_channel_gold_queues', 'test_classifier_runtime',
    'test_classify_member_longform_full_turns', 'test_classify_member_longform_full_turns_local',
    'test_clean_workspace_loading', 'test_closeness_math', 'test_coalition_recommender',
    'test_collect_fec_spending_telecom', 'test_collect_govinfo_amendment_identity',
    'test_compact_existing_review', 'test_compact_partial_historical_pages',
    'test_complete_existing_career_histories', 'test_congress_result_summary_settling',
    'test_continue_member_longform_profile_ready_script', 'test_cook_pvi',
    'test_covered_date_refinements', 'test_covered_date_scope', 'test_crec_http_completion',
    'test_culling_refresh_continuity', 'test_daily_refresh_scheduler',
    'test_dashboard_legislative_workbench', 'test_descriptive_profile_publication',
    'test_discover_leadership_prerole_interviews', 'test_discovery_refresh_cycle',
    'test_draft_member_longform_reviews', 'test_e1_stance_imputation_harness', 'test_edge_startup',
    'test_effectiveness_integration', 'test_effectiveness_sources',
    'test_f2_stance_rationale_sidecars', 'test_filter_senate_payroll_employment_candidates',
    'test_govinfo_discovery_fallback', 'test_hearing_display_feed',
    'test_held_evidence_reconciliation', 'test_historical_evidence', 'test_industry_valence',
    'test_integrate_backfilled_careers', 'test_integrate_reviewed_statements',
    'test_integrate_target_history', 'test_leadership_audio_source_era',
    'test_leadership_prerole_reviewed_sources', 'test_legislative_effectiveness',
    'test_live_dashboard_alias', 'test_live_fec_money', 'test_live_legislative_pillar',
    'test_longform_automated_authority', 'test_longform_delivery_ledger',
    'test_longform_methodology_compatibility', 'test_longform_semantic_ledger_authority',
    'test_m1_trade_association_linkage', 'test_match_lda_senate_payroll',
    'test_materialize_leadership_prerole_reviewed_turns',
    'test_materialize_member_longform_reviewed_turns', 'test_media_freshness_contract',
    'test_melange_dashboard', 'test_member_interview_long_turns',
    'test_member_longform_acquisition_queue', 'test_member_longform_discovery',
    'test_member_longform_no_cue_retry_queue', 'test_member_longform_youtube_discovery',
    'test_money_observation', 'test_moneyball_desktop', 'test_moneyball_manual_corroboration_roles',
    'test_mtp_sitemap_fail_closed', 'test_official_senate_payroll_lane', 'test_operational_spine',
    'test_operational_spine_evaluation', 'test_personal_display_dedupe', 'test_pillar_freshness',
    'test_pillar_policy', 'test_prepare_member_longform_automated_batch', 'test_press_backlog',
    'test_press_official_coverage', 'test_press_official_dated_listings',
    'test_press_release_date_precedence', 'test_press_request_deadlines',
    'test_press_source_freshness', 'test_press_wordpress_custom_posts', 'test_process_liveness',
    'test_promote_reconciliation_release', 'test_published_source_contracts',
    'test_quorum_current_adjudication', 'test_ranking_math', 'test_recompute_legislative_workbook',
    'test_reconcile_member_longform_candidate_passes', 'test_recorrection_contracts',
    'test_reference_cache', 'test_reference_extraction', 'test_refresh_integrity',
    'test_relationship_uncertainty', 'test_responsiveness_layers', 'test_run_pipeline_tolerance',
    'test_screen_member_longform_identity_cues', 'test_select_member_longform_profile_sources',
    'test_sentinel_decision_stability', 'test_sentinel_network_retries', 'test_sentinel_qme_fixes',
    'test_sentinel_source_unavailable', 'test_sentinel_statute_identity',
    'test_snapshot_member_longform_ready_sources',
    'test_speech_channel', 'test_staffer_assertions', 'test_staffer_automatic_service',
    'test_staffer_canonical_graph', 'test_staffer_connection_facts', 'test_staffer_contact_group',
    'test_staffer_contacts', 'test_staffer_distinct_routes', 'test_staffer_ecosystem_routes',
    'test_staffer_evidence_boundaries', 'test_staffer_governing_audit',
    'test_staffer_identity_auto', 'test_staffer_identity_records',
    'test_staffer_identity_resolution', 'test_staffer_identity_status_count',
    'test_staffer_identity_web', 'test_staffer_inventory_cache', 'test_staffer_lda_catalog',
    'test_staffer_live_server', 'test_staffer_lock_free_view', 'test_staffer_network_explorer',
    'test_staffer_network_scope', 'test_staffer_office_covered_staff',
    'test_staffer_payroll_people', 'test_staffer_payroll_promotion',
    'test_staffer_person_inventory', 'test_staffer_prewarm', 'test_staffer_rebuild_view',
    'test_staffer_reconciled_evidence', 'test_staffer_refresh_memo',
    'test_staffer_request_work_index', 'test_staffer_route_alternatives', 'test_staffer_route_jobs',
    'test_staffer_route_latency', 'test_staffer_routing_evidence', 'test_staffer_row_equality',
    'test_staffer_search_differential', 'test_staffer_source_catalog',
    'test_staffer_typeahead_http', 'test_staffer_typeahead_identifiers',
    'test_staffer_unreachable_hint', 'test_staffer_work_time_routes',
    'test_transcribe_member_longform_captions', 'test_transcribe_member_longform_local_asr',
    'test_verify_effectiveness_shadow', 'test_verify_member_longform_profile_readiness',
    'test_verify_member_longform_speaker_continuity', 'test_wikipedia_biographies',
    'test_workbook_cell_sync', 'test_workbook_contracts', 'test_workbook_stance_views',
    # 80d7355e4: audit workbook builds in a scratch file and replaces live only after both steps.
    'test_audit_workbook_atomic_build',
    'test_audit_congress_collector_resume_continuity',
    'test_collect_govinfo_bill_xml_versions',
    'test_collect_official_rollcall_events',
    'test_collector_roster_updates',
    'test_sentinel_bill_workbook_sync',
    'test_desk_membership_source',
    'test_desk_membership_fixed_point',
    'test_build_legislation_category_review_queue',
    'test_legislation_opus_candidate_adjudication',
    'test_run_legislation_claude_ambiguity_review',
    'test_run_reversible_legislation_culling',
    'test_legislation_article_cross_reference',
    'test_legislation_headless_harvest_contract',
    'test_legislation_measure_weight',
    'test_run_legislation_network_dependents',
    'test_legislation_culling_integration_contract',
    'test_legislation_shadow_recompute',
    'test_sentinel_supplemental_adjudications',
    # Ruled LDA status/employer-line table (D-022 item 3 + 2026-09-07 follow-up),
    # fec/staffer_network/lda_status_labels.py -- the shared module
    # fec/tests/surfaces/test_staffer_groups_section.py's own [i2] holds equal
    # with a byte-identical local copy. Registered here rather than beside its
    # staffer siblings in _LANE_MODULES because it is pytest-native (fixtures,
    # parametrize, pytest.raises, no __main__ block) and imports `fec.` as an
    # absolute package, exactly the shape _LANE_MODULES's plain `python <path>`
    # subprocess cannot run: no _pkg_env there means no repo root on
    # PYTHONPATH, so the import would fail outright -- or, if it somehow did
    # not, plain python would define the tests and invoke none of them, the
    # vacuous-green failure this file's own notes warn about above.
    'test_lda_status_labels',
    # Descriptive-profile prose (2026-09-07). This suite EXISTED and was never
    # registered, so nothing ran it -- and it was the only place that could have
    # caught the two-audio-layer divergence in prose: 74 members holding an
    # identity-verified voiceprint shipped with the sentence "There is no accepted
    # audio sample in this evidence summary". Same lane reasoning as the entry
    # above: it is pytest-native (module fixtures, parametrize) and imports `fec.`
    # as an absolute package, which _LANE_MODULES's plain `python <path>` cannot
    # run and would silently define-without-invoking if it somehow could.
    'test_descriptive_profiles',
    'test_profile_evidence_refresh',
    # The recurring daily job (2026-09-13). All four suites EXISTED and none was
    # registered, so nothing in the kernel ran the gates that decide whether a day
    # can be declared complete: the media collector's PASS/PARTIAL/FAIL rule and
    # its success anchor, the wrapper's publication and media-freshness binding,
    # the daily gate's owned-receipt check and its BUSY exits, and the admissions
    # no-model pass. Same lane reasoning as the two entries above -- all are
    # pytest-native (fixtures, parametrize, pytest.raises, no __main__ block), so
    # _LANE_MODULES's plain `python <path>` would define every test and invoke
    # none. tmp_path-only: they start no collector, scheduler or publication.
    'test_daily_media_refresh',
    'test_refresh_congress_pipeline',
    'test_daily_refresh',
    'test_sentinel_admissions_no_llm',
    # 2026-09-30 (Paul: "Hold low-confidence + longer timeout"): below-high-confidence classifier decisions
    # become retryable holds for every key, the claude-cli call is bounded at 480 s, and a failed call reports
    # its stdout. Pytest-native and tmp_path-only; imports the import-safe refresh_sentinel_admissions module.
    'test_sentinel_validation_hold',
    # 2026-10-01 (Paul: "Hold both bills"): an include whose categories carry both +1 and -1 becomes the same
    # retryable hold on the reused, fresh and --no-llm carried-forward paths, and sentinel_admissions.load refuses
    # a generation that still admits one. Pytest-native and tmp_path-only, like the suite above.
    'test_sentinel_mixed_directions',
    # The shared atomic-rename retry (2026-09-13). A concurrent reader holding a
    # status document made os.replace fail with WinError 5 and killed a whole
    # scheduled run; this suite is the only thing that asserts the retry, the
    # transient-code predicate and the no-debris guarantee.
    'test_atomic_replace_retry',
    # Speech-lane suites that define module-level test_* functions and no runner block: under
    # plain python they exit 0 having executed NOTHING, and unittest discovery does not collect
    # them either. v2 batch 2 (2026-09-13).
    'test_build_audio_profile_recovery_gap',
    'test_classify_local_youtube_turns',
    'test_import_wj_clips',
    'test_verify_local_youtube_exact_windows',
    # Part-A suites that import via `from fec...` (namespace-package style) and so need the
    # repo root this lane's env already supplies; the second is pytest-parametrised and defines
    # no runner block of its own. v2 batch 2 (2026-09-13).
    'test_staffer_employment_promotion',
    'test_name_standard_identity_repairs',
    # D-030/D-031 publication crown (2026-09-17). The chain's LAST step refused to
    # publish because audit_measurement_layers still expected the retired
    # Legislative+Press Tier-1 crown while the desk and dashboard publish the honors
    # board's top 13. Registered here because the suite is pytest-native (module
    # fixture, pytestmark skipif, no __main__ block): under plain python it would
    # define its tests and run none of them, the vacuous green this file warns about.
    'test_publication_crown_guard',
    # Source-to-dashboard and measurement-layer audits (2026-09-17). Both suites
    # EXISTED and neither was registered, so nothing in the kernel ran the two
    # gates that decide whether a generation may be published at all: the whole
    # source -> pillar -> rank -> surface chain with its stale-pillar, invalid-
    # candidate and four wrong-measurement negative controls, and the
    # layer-by-layer connectedness audit_measurement_layers.verify() performs
    # as the chain's last step. Same lane reasoning as the entries above -- both
    # are pytest-native (module fixtures, parametrize, no __main__ block), so
    # _LANE_MODULES's plain `python <path>` would define every test and invoke
    # none of them, the vacuous green this file's own notes warn about.
    'test_source_to_dashboard',
    'test_measurement_layers',
    # 2026-09-30 fix round (Paul: "F1. Yes", "F4. Proceed"): the joint optimizer's system-level stance
    # gate, and the desk efficiency metrics (gap, midrank eff_value, lossless opt.alloc, ROI parity and
    # gate). Both are pytest-native and fixture-only; they lift the shipped code with ast and import
    # none of the modules that execute on import.
    'test_optimizer_stance_gate',
    'test_desk_efficiency_metrics',
    # COMPET_MULT (Paul 2026-10-05): the competitiveness multiplier c -- builder join/formula, fail-closed kernel accessor, cm=1.0 bitwise identity.
    'test_competitiveness_c',
    # COMPET_MULT rulings (Paul 2026-10-07, brain D-107): R1 pressure-field alias, R2 Kiley orientation, R3/R4 ballot overrides, R6 LEV=0, R7 provenance.
    'test_competitiveness_rulings',
    # Exact stored frontier cutoff and its independent audit/display contracts.
    'test_frontier_coalition_fixes',
    'test_frontier_knee_display',
    # 2026-10-10 security baseline suites
    'test_fetch_press_text_public_only',
    'test_campaign_sites_output_path',
    'test_refresh_email',
    'test_security_baseline_config',
    'test_build_member_longform_review_app',
    'test_model_integrity_sidecar',
    'test_glassbox_app_server_host_guard',
    'test_member_desk_template_escaping',
    'test_staffer_identity_api',
    'test_lock_dashboard',
    'test_press_recovery_monitor_host',
    'test_bug_report_regressions',
    # 2026-10-11 Mentat money wiring (decision B: MW-1..MW-6, PM-A4)
    'test_import_mentat_money',
    'test_reconcile_mentat_money',
    # Census follow-up 2026-10-11: backlog suites turned green alone from LIVE (stale fixtures
    # and module paths fixed in the tests; extract_cu_bluesky fixed to read the handle registry).
    'test_audit_postrefresh_downstream_drift','test_audit_postrefresh_planning_dashboard_semantics',
    'test_build_vote_category','test_canonical_cu_board','test_lane_atomic_write_concurrency',
    'test_lt_priming_qme_20261001','test_ndjson_bom_tolerance','test_verified_bluesky_refresh',
    'test_b0_bicam_arrival_audit',
    # Post-refresh item 3 (2026-10-11): chain-order pin repointed to the real producer.
    'test_pipeline_producer_scope',
    # Post-refresh items 1-2 (2026-10-11): the dead vote_category_lean KNOWN_GATED_FAILURES entry is
    # gone, and the three live-data suites are re-pinned to the post-refresh publication (RE-1 G1
    # board, CU_IDENTITY_FIX narrative ledger).
    'test_collection_stream_wiring',
    'test_audit_completion_20260716',
    'test_member_narratives_v2_repair',
    'test_policy_repairs_20260718',
    # Staffer fixture isolation and native verdict/exit-status fail-closed contract (QME 2026-10-02).
    'test_staffer_harness_contract',
    # Stale-import detector controls (QME 2026-10-02).
    'test_stale_imports_contract',
    'test_legacy_direction_archive',
    # 2026-10-15 self-healing P2 (D-212): pipeline failure diagnostics, transactional chain, labelled freshness
    # degrade, gold-orphan policy.
    'test_pipeline_failure_diagnostics',
    'test_chain_transaction',
    'test_chain_freshness_degrade',
    'test_gold_orphan_policy',
    # 2026-10-15 admissions spend controls (D-211): classifier quota stop and per-run call budget.
    'test_sentinel_classifier_quota',
    'test_sentinel_classifier_budget',
    # 2026-10-15 self-healing P1 (D-216): tick runner failure classes, tiered degrade, freshness feed.
    'test_refresh_degrade',
    # 2026-10-15 self-healing P4 (D-213): stdlib watchdog over the freshness feed, lease, LIVE age and tick task; toast + alert log.
    'test_refresh_watchdog',
    # 2026-10-15 press flow and scoring (D-209, D-210, D-219, D-221): carried-pending promotion, FIFO backlog, ratified Stage 5, stack leak fix.
    'test_press_audit_scored_partitions',
    'test_press_feed_stage5',
    'test_press_refresh_scoring_filters',
    'test_press_stage5',
    'test_press_haiku_rater',
    'test_press_stance_stack_refresh',
    # 2026-10-15 census follow-up: tracked suites no lane named (collector supervisor fixes, press recovery speed, repair_jsonl, prospective outcome contract).
    'test_collector_supervisor_fixes',
    'test_press_recovery_speed',
    'test_repair_jsonl',
    'test_outcome_contract',
    # 2026-10-16 adapters: catchnet rerun, quarantine collision and retained-audio safety (audit hooks armed only during guarded calls).
    'test_catchnet_rerun',
    'test_quarantine_collision_safety',
    'test_retained_audio_safety',
    'test_surface_byte_preservation',
    # 2026-10-16 RECONCILE42 (lane/m3-reconcile42) boundary suites.
    'test_congress_reconciliation_boundaries',
    'test_lease_and_legacy_review_boundaries',
    'test_pending_native_reconciliation',
    'test_reconcile42_full_audit_blocks',
    'test_reconcile42_manifest_boundaries',
    'test_reconcile42_mechanical_boundaries',
    'test_reconcile42_security_boundaries',
    'test_refresh_hash_refusal',
    'test_repin_process_identity',
    'test_snapshot_reconciliation_boundaries',
    # 2026-10-16 weekly refresh: a deferred discovery is a soft failure, never a gate (fec_root.md:20).
    'test_weekly_discovery_deferral',
    # 2026-10-16 completed surface repairs (FEC_SURFACES_REPAIR.md): companions of the receipt-binding,
    # email-snapshot, surface-gate and workbook/observability source repairs. Synthetic fixtures; each
    # ran green alone on the composed source and wrote nothing into the tree.
    'test_graph_snapshot_binding','test_email_snapshot_binding','test_surface_gate_regressions',
    'test_observability_snapshot',
    # 2026-10-16 defensive input repairs (G2): non-object cached handoff re-extracts; missing page-data names the page.
    'test_surface_input_refusals',
    # 2026-10-16 completed audit repairs (G4; recovery/fec-*-20261015 receipts): artifact gaps, payload currentness,
    # pre-culling reporting, stale-import AST, tier-boundary status, recursive scanners/walk errors, and the
    # groups binding/provenance companions of the already-published audit_full.py. Synthetic fixtures.
    'test_artifact_gap_completion','test_payload_currentness_regression','test_pre_culling_inventory_reporting',
    'test_stale_imports_ast_regression','test_tier_boundary_status','test_recursive_source_scanners',
    'test_groups_local_binding_runtime','test_groups_provenance_currentness',
    # 2026-10-16 completed infra repairs (G4b; FEC_ROOT_REPAIR.md:34,41): calibration refusal, workbook plan snapshot.
    'test_calibration_guard_unavailable','test_workbook_author_plan_snapshot',
    # 2026-10-16 bill override CSV overflow guard (G4c; fec-debug-findings.md:66-78).
    'test_bill_override_csv_overflow',
    # 2026-10-16 say-do first-pass independent-rounding tolerance (G5; fec_legislation.md:97,144).
    'test_say_do_validate_output_rounding',
    # 2026-10-16 second-pass child timeout seam (G6b; FEC_SURFACES_REPAIR.md:119-134).
    'test_second_pass_child_timeout',
    # 2026-10-16 forecast ledger safe deferral (G9; CODEX_DEBUG_ASSIST.md:27-42). Synthetic tmp_path roots only.
    'test_forecast_ledger_resolution')

# Node lane (2026-10-11, census follow-up to 7d57fb209): the git-tracked fec/tests/**/test_*.cjs
# suites, run as `node <file>`. Most use node:test, the rest plain assert with a nonzero exit on
# failure, so the verdict is the exit code alone. Each ran green ALONE from LIVE and wrote nothing.
# None starts a server or a browser; load and launch-loop tests never go here.
_LANE_NODE=(
    'test_approved_storage.cjs','test_clean_workspace.cjs','test_desk_cook_pvi.cjs',
    'test_desk_overlays_r5.cjs','test_industry_frame_loading.cjs','test_melange_people_navigation.cjs',
    'test_member_desk_honors.cjs','test_mlk_boot.cjs','test_people_directory_names.cjs',
    'test_staffer_groups_view.cjs','test_staffer_inventory_worker.cjs',
    'test_staffer_inventory_worker_codes.cjs','test_staffer_inventory_worker_committee_names.cjs',
    'test_staffer_inventory_worker_ids.cjs','test_staffer_left_out_staff.cjs',
    'test_staffer_live_query_committee_names.cjs','test_staffer_live_query_ids.cjs',
    'test_staffer_map_scope.cjs','test_staffer_payroll_map.cjs','test_staffer_reconciled_display.cjs',
    'test_staffer_route_jobs_client.cjs','test_staffer_route_models.cjs',
    'test_staffer_snapshot_recovery.cjs','test_staffer_unreachable_hint.cjs','test_staffer_zoom.cjs',
    # 2026-10-16 completed lobby-selection repair (FEC_SURFACES_REPAIR.md): node:test, synthetic, no server.
    'test_lobby_sector_state.cjs')
_node=__import__('shutil').which('node')

# Of the 20 originally held, 18 are now registered: eight via the PYTHONPATH lane, four via
# pytest proper, five rescued from vacuous-green via pytest collection, and one slow suite at
# Paul's instruction. Of that original 20, two remain unregistered (the 2026-10-11 census found 199
# tracked suites unregistered in all; fec/tests/infra/test_kernel_registration_census.py lists the
# rest with a reason each): test_audit_legislation_downstream_readiness
# and test_run_legislation_presync_audit. They are operational PRE-SYNC gates asserting that live
# recorded receipts (workbook receipt, behavior_panel.json receipt, staged-producer source hashes)
# are current AT THIS MOMENT. The workbook is user-edited and those receipts drift by design, so
# in the kernel runner they would go red on every routine workbook edit -- a cries-wolf gate. They
# are run at sync time instead. Their receipt drift is being re-attested separately.

_mb_root=os.path.dirname(FEC)
_pkg_env=dict(os.environ)
_pkg_env['PYTHONPATH']=_mb_root+os.pathsep+FEC
# 2026-08-04 Phase 1: tests live in domain subdirs; this recursive resolver is
# the single source of path truth. A registered suite it cannot find is never
# skipped -- see _test_path below.
_test_paths={}
for _test_dir,_test_dirs,_test_files in os.walk(os.path.join(FEC,'tests')):
    _test_dirs.sort()
    for _test_file in sorted(_test_files):
        if not (_test_file.startswith('test_') and _test_file.endswith(('.py','.cjs'))):
            continue
        # A .cjs suite keeps its extension as its name, so it never collides with a .py sibling.
        _test_name=_test_file[:-3] if _test_file.endswith('.py') else _test_file
        _test_full_path=os.path.join(_test_dir,_test_file)
        if _test_name in _test_paths:
            raise RuntimeError('duplicate test basename %s: %s and %s'%
                               (_test_name,_test_paths[_test_name],_test_full_path))
        _test_paths[_test_name]=_test_full_path

# 2026-09-06: an absent suite file is a FAILING suite, not a fatal runner error. Raising here
# aborted the whole harness on the FIRST missing file and threw away the verdicts of the other
# 47 -- and a worktree or partial checkout that carries the data but lacks one lane's files hits
# exactly that. Absence stays strict: every missing name is reported below and counted into the
# FAILED total, so a suite that silently vanishes can never pass unnoticed. All of them are
# collected, not just the first, so one run names everything that is gone. The duplicate-basename
# check above stays fatal -- that is a programming mistake, not an environment condition.
_missing_suites=[]
def _test_path(_test_name):
    if _test_name not in _test_paths:
        if _test_name not in _missing_suites:
            _missing_suites.append(_test_name)
        return None
    return _test_paths[_test_name]

# Specs whose path came back None are the missing ones: dropped from the run here and re-emitted
# as failures below, so the execution loop's contract (every spec is runnable) is unchanged.
_suite_specs=tuple(_s for _s in (
    tuple((_t,[sys.executable,_test_path(_t)],None)
          for _t in _LANE_MODULES+_LANE_LEGISLATION)+
    tuple((_t,[sys.executable,_test_path(_t)],_pkg_env)
          for _t in _LANE_LEGISLATION_PKG+_LANE_SPEECH_PKG)+
    tuple((_t,[sys.executable,'-m','pytest',_test_path(_t),
               '-q','--no-header','-p','no:cacheprovider'],_pkg_env)
          for _t in _LANE_PYTEST)+
    tuple((_t,[_node,_test_path(_t)],None) for _t in _LANE_NODE if _node)+
    # Stale-import tripwire (Paul-approved 2026-08-05): lives in fec/audit/, not tests/,
    # because it is a repo-layout gate. Prints 'stale imports: N passed, M failed'.
    (('audit_stale_imports',[sys.executable,os.path.join(FEC,'audit','audit_stale_imports.py')],None),
    # PATH-4 kernel-mirror parity. Registered by explicit path (2026-08-19) because it predates
    # the test_*.py discovery glob and so was NEVER INVOKED -- it guards that the Python kernel
    # and the dashboard JS still compute the same p, now across EVERY NET_CONFIG. It was written
    # for two configs while five shipped. Same reasoning as test_lda_rebuild above: a suite no
    # runner invokes is indistinguishable from a deleted one.
     ('path4_parity',[sys.executable,os.path.join(FEC,'tests','holistic','path4_parity.py')],None),))
    if None not in _s[1])

# Missing suites are reported in the same per-suite shape the loop below prints, so the run stays
# readable and the verdict line keeps its format. Nothing is printed when nothing is missing, so a
# complete tree's output is byte-identical to before.
if _missing_suites:
    print('[registered suites absent from this tree]')
    for _t in _missing_suites:
        ok('%s suite clean'%_t,False,'registered test suite not found')
# No node on PATH is one named failure, never a silent skip of the whole lane.
if not _node:
    ok('node lane ran',False,'node not on PATH; %d .cjs suites not run'%len(_LANE_NODE))

# An unrecognised verdict is failure by design, never a skip: output the runner cannot read
# is a suite it cannot trust.
# Every launch is BOUNDED (2026-09-19): one suite's child froze inside `import scipy` -- no CPU,
# no I/O, no output -- and with no timeout here it held this whole run for 14 minutes without a
# line to say so. After 30 minutes, which is longer than a whole healthy kernel run takes for all
# of them together, its process tree is killed and that suite is a NAMED failure carrying the
# note below -- never a skip, and never a stalled kernel.
for _t,_argv,_env in _suite_specs:
    _r=_bounded.run_bounded(_argv,timeout=_bounded.KERNEL_SUITE_TIMEOUT_SECONDS,env=_env)
    _output=(_r.stdout or '')+'\n'+(_r.stderr or '')
    # Opt-in flake trap (2026-08-05): with RUNTESTS_DUMP_DIR set, any failing suite's FULL
    # output lands there. The ok() line keeps only the last line, which for an intermittent
    # in-kernel failure (test_legislation_base_network failed 2/3 kernel runs while passing
    # every standalone run) is just "AssertionError" — useless. Zero cost when unset.
    _dump=os.environ.get('RUNTESTS_DUMP_DIR')
    if _dump and _r.returncode!=0:
        open(os.path.join(_dump,_t+'.out'),'w',encoding='utf-8').write(_output)
    _verdict=_suite_verdict(_output)
    ok('%s suite clean'%_t,
       _r.returncode==0 and (_t.endswith('.cjs') or (_verdict is not None and _verdict[1]==0 and _verdict[0]>0)),
       _r.timeout_note if _r.timed_out else ('unrecognised verdict' if _verdict is None and not _t.endswith('.cjs') else ((_output.strip() or 'no output').splitlines()[-1])))

print('\nKERNEL TESTS — %d passed, %d FAILED'%(P,F))
sys.exit(1 if F else 0)
