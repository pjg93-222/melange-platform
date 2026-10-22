import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
from moneyball_core import (build_party_map, dump_atomic, overall_display_tier, salience_issue_params,
                            select_network_file, cu_dropped_sponsors, cu_edge_rows)   # shared artifact + stance/network authorities
import json, sys, glob, os, csv, openpyxl, networkx as nx, math, random, re, unicodedata, zlib, hashlib
import roster
from legislation import dashboard_legislative_workbench as DLW
from legislation.legislation_culling_status import load_surface_status
from members.hearing_display_contract import load_optional as load_hearing_display
from speech.descriptive_contract import load_optional as load_speech_descriptive
from stance.honors_views import load_honors
from surfaces.dashboard_honors import dashboard_honors_payload, overall_stances_from_gen
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
HONORS_DOC=load_honors(MB_ROOT)
C=json.load(open(os.path.join(MB_ROOT, 'data', 'engine', 'congestion.json')))
cong={m['member']:m for m in C}
_CS_PATH=os.environ.get('CATEGORY_STANCE_PATH') or os.path.join(MB_ROOT, 'category_stance.json')
CS_DOC=json.load(open(_CS_PATH))
CULLING_STATUS=load_surface_status(MB_ROOT)
CS=CS_DOC['matrix']; OVERALL_CAPS=(CS_DOC.get('gate') or {}).get('overall_capped') or {}
FL=json.load(open(os.path.join(MB_ROOT, 'per_topic_alloc.json')))
CO=json.load(open(os.path.join(MB_ROOT, 'data', 'lobbying', 'per_topic_coalition.json')))
# label -> matrix category.  CFPB is exposed once under the ratified pro-CU frame; the former
# "Reach (-)" duplicate presented the same bytes as an apparent opposite policy axis.
LBLCAT={'Examination & Supervision':'Examination & Supervision','Credit Union Capital Requirements':'CU Capital Requirements',
'NCUA Budget & Share Insurance Fund':'NCUA Budget & Insurance','Interchange':'Interchange','Payments':'Payments',
'Technology & Emerging Issues':'Technology & Emerging','Fraud Prevention':'Fraud Prevention',
'Data Privacy & Cybersecurity':'Data Privacy & Cyber','Charter Modernization & Governance':'Charter Modernization',
'Defense Credit Union Issues':'Defense CU Issues','Field of Membership':'Field of Membership','CDFI Fund':'CDFI Fund',
'CFPB Restrain':'CFPB','CFPB Reach (-)':'CFPB','Telephone Consumer Protection Act Issues':'TCPA',
'Credit Card & Consumer-Fee Mandates':'Credit Card Mandates','Member Business Lending':'Member Business Lending',
'Mortgage Lending & Housing':'Mortgage & Housing','Credit Union Tax Status':'CU Tax Status'}
DISPLAY_LABEL={'CFPB Restrain':'CFPB Restrain (pro-CU; limits agency reach)'}
DROPPED_DISPLAY_CLONES={'CFPB Reach (-)'}


def dashboard_issue_contract(labels):
    """Separate source keys, human labels, and canonical engine/stance categories."""
    source=[label for label in labels if label not in DROPPED_DISPLAY_CLONES]
    display=[DISPLAY_LABEL.get(label,label) for label in source]
    categories=[LBLCAT.get(label,label) for label in source]
    if not (len(source)==len(display)==len(categories)) or len(categories)!=len(set(categories)):
        raise RuntimeError('dashboard issue mapping is not one-to-one after display-clone removal')
    return source,display,categories


source_issues,issues,issue_cat=dashboard_issue_contract(list(FL.keys()))
wb=openpyxl.load_workbook(os.path.join(MB_ROOT, 'Moneyball Main Version - Blended.xlsx'),data_only=True)
ra=wb['Rank & Allocation']
# authoritative rank = Blended Model col20 (was erroneously reading Rank & Allocation col2 = District/State)
bm=wb['Blended Model']
from moneyball_core import ranks as _pyranks
rank=_pyranks()   # PHASE-2: Python rank authority
# committee memberships (base committee per member, subcommittees folded into parent) from the Committee Data
# sheet -> drives the committee-cluster layout (Porter et al. bipartite member-committee structure).
def _nfc(s): return unicodedata.normalize('NFC',str(s)) if s else s
cd=wb['Committee Data']; MEM_COMM={}; MEM_SEATS={}
for r in range(2,cd.max_row+1):
    nm=cd.cell(r,1).value; seats=str(cd.cell(r,5).value or '')
    if not nm or not seats.strip(): continue
    bases=set(); parsed=[]
    for part in seats.split('|'):
        mm=re.match(r'^(.*?)\s*\[([^\]]+)\]',part.strip())
        if mm:
            base=re.split(r' - | \(full\)',mm.group(1).strip())[0].strip()
            if base: bases.add(base)
            tags=mm.group(2).strip().split('/')
            parsed.append({
                'name':mm.group(1).strip(),
                'role':tags[1].strip() if len(tags)>1 else '',
            })
    if bases: MEM_COMM[_nfc(nm)]=bases
    if parsed: MEM_SEATS[_nfc(nm)]=parsed
members=[m['member'] for m in C]; idx={n:i for i,n in enumerate(members)}
_SPEECH_ART=load_speech_descriptive(MB_ROOT)
_SPEECH_MEMBERS=(_SPEECH_ART or {}).get('members') or {}
_SPEECH_PROFILE_PATH=os.path.join(MB_ROOT,'data','members','member_narrative_evidence_v4.json')
_SPEECH_PROFILE_ART=(json.load(open(_SPEECH_PROFILE_PATH,encoding='utf-8'))
                     if os.path.exists(_SPEECH_PROFILE_PATH) else None)
_PROFILE_BASIS_PATH=os.path.join(MB_ROOT,'data','members','profile_basis_v2.json')
_PROFILE_BASIS_SHA=(hashlib.sha256(open(_PROFILE_BASIS_PATH,'rb').read()).hexdigest()
                    if os.path.exists(_PROFILE_BASIS_PATH) else None)
_SPEECH_PROFILE_OK=(not _SPEECH_PROFILE_ART) or (
        _SPEECH_PROFILE_ART.get('schema')=='member-narrative-evidence-v4' and
        _SPEECH_PROFILE_ART.get('status')=='ACTIVE_FAIL_CLOSED_INPUT' and
        _SPEECH_PROFILE_ART.get('basis_sha256')==_PROFILE_BASIS_SHA and
        (_SPEECH_PROFILE_ART.get('counts') or {}).get('members')==roster.count() and
        (_SPEECH_PROFILE_ART.get('counts') or {}).get('speech_pass')==roster.count() and
        len(_SPEECH_PROFILE_ART.get('members') or {})==roster.count() and
        all((((row.get('speech_profile') or {}).get('status'))=='PASS')
            for row in (_SPEECH_PROFILE_ART.get('members') or {}).values()))
# A failed contract (e.g. the artifact still describes the pre-refresh roster) withholds the profiles loudly, as make_member_desk
# does for the same artifact; the media-inclusive run regenerates it.  It no longer takes the whole bundle down.
if not _SPEECH_PROFILE_OK:
    print('  !! member_narrative_evidence_v4.json failed its %d-member speech-profile contract; '
          'speech profiles WITHHELD' % roster.count())
_SPEECH_PROFILES=((_SPEECH_PROFILE_ART or {}).get('members') or {}) if _SPEECH_PROFILE_OK else {}
# One overall display implementation across workbook col G, dashboard, and member desk: Ally is the
# scored top, while Champion is the active primacy board's top 13 WITH a qualifying action (D-030).
# The retired "Legislative+Press Tier 1 -> Champion" overlay is the no-board fallback in
# moneyball_core.overall_display_tier and must not be reached here: _top13 below is that board, and
# honors_views.load_honors refuses a generation seated on a different board basis than tier_util's.
from tier_util import legislative_press_tiers as _lptiers
_tier,_trank,_tierdetail=_lptiers(members,with_detail=True)
_top13=set((HONORS_DOC or {}).get('overall_board',{}).get('top13') or ()) or None
gen={n:overall_display_tier(CS.get(n,{}),_tier.get(n,0),n,OVERALL_CAPS,_top13) for n in members}
# party from voteview members (party_code 100=D,200=R,else I); prefer most recent congress
party=build_party_map([(congN,os.path.join(MB_ROOT, 'votes', 'HS%d_members.csv'%congN)) for congN in (119,118,117)])
bio2name={cong[n]['bioguide']:n for n in members if cong[n].get('bioguide')}
# network
net=select_network_file(glob.glob(os.path.join(MB_ROOT, 'congress_cosponsor_network_*.xlsx')))
ne=openpyxl.load_workbook(net,data_only=True)['Edges']
G=nx.Graph(); G.add_nodes_from(members); ew={}
for r in range(2,ne.max_row+1):
    a,b,w=ne.cell(r,1).value,ne.cell(r,2).value,ne.cell(r,3).value
    a=bio2name.get(a); b=bio2name.get(b)
    if a in idx and b in idx and a!=b and isinstance(w,(int,float)):
        G.add_edge(a,b,weight=w); ew[(a,b)]=ew.get((a,b),0)+w
print('graph: %d nodes %d edges; laying out...'%(G.number_of_nodes(),G.number_of_edges()))
pos=nx.spring_layout(G,weight='weight',k=0.45,iterations=60,seed=7)
xs=[p[0] for p in pos.values()]; ys=[p[1] for p in pos.values()]
x0,x1,y0,y1=min(xs),max(xs),min(ys),max(ys)
def sx(v):return round(40+920*(v-x0)/(x1-x0))
def sy(v):return round(40+920*(v-y0)/(y1-y0))
TC={'Champion':'C','Defender':'F','Ally':'A','Highly Persuadable':'H','Persuadable':'P','Lean Persuadable':'G','True Neutral':'T','Lean Skeptic':'L','Neutral':'N','Skeptic':'S','Highly Skeptical':'K','Opponent':'O','—':'-'}
# bipartisanship = weighted fraction of COSPONSORSHIP ties crossing party, shrunk toward the global
# rate (low-degree members aren't 0/100).
# S2-01 fix (audit 2026-07-02): the display graph G above deliberately uses the AUG network (the live
# cascade reality incl. social/press layers), but BP is DEFINED on cosponsorship — cross-party reposts
# are not cosponsorship, and letting them in both redefined bipartisanship unratified AND fed back into
# cpf_diffusion/tgt/coalition boosts via bipartisan.json. BP therefore uses the newest NON-AUG file.
# BP_SRC=aug restores the augmented definition if the user ever ratifies it.
if os.environ.get('BP_SRC','cosponsor')=='cosponsor':
    _n0=max([q for q in glob.glob(os.path.join(MB_ROOT, 'congress_cosponsor_network_*.xlsx')) if 'AUG' not in os.path.basename(q)],key=os.path.getmtime)
    _e0=openpyxl.load_workbook(_n0,data_only=True)['Edges']
    G0=nx.Graph(); G0.add_nodes_from(members); _drop0=cu_dropped_sponsors(_n0)   # CU_IDENTITY_FIX (decision G)
    for a,b,w in cu_edge_rows(_n0,_e0):   # + CU_PLACEHOLDER_BACKFILL (2026-10-11)
        a=None if a in _drop0 else bio2name.get(a); b=bio2name.get(b)
        if a in idx and b in idx and a!=b and isinstance(w,(int,float)):
            w=(G0[a][b]['weight']+w)/2 if G0.has_edge(a,b) else w   # D2 mean-symmetrize
            G0.add_edge(a,b,weight=w)
else: G0=G
raw={}; gs=gc=0.0
for n in members:
    pn=party.get(cong[n].get('bioguide',''),'?'); same=cross=0.0
    for nb in G0.neighbors(n):
        pnb=party.get(cong[nb].get('bioguide',''),'?')
        if pn in ('D','R') and pnb in ('D','R'):
            w=G0[n][nb].get('weight',1.0)
            if pnb==pn: same+=w
            else: cross+=w
    raw[n]=(same,cross); gs+=same; gc+=cross
prior=gc/(gs+gc) if (gs+gc)>0 else 0.12; ALPHA=6.0
bp={n:(raw[n][1]+ALPHA*prior)/(raw[n][0]+raw[n][1]+ALPHA) for n in members}
cfp=os.path.join(MB_ROOT, 'data', 'members', 'confidence.json'); CONF=json.load(open(cfp)) if os.path.exists(cfp) else {}
# receptivity color layer (2026-07-04): pressure_mult per member (behavioral basis) for colorBy=recept
_rcp=os.path.join(MB_ROOT, 'member_receptivity.json')
_RC=(json.load(open(_rcp)).get('members',{}) if os.path.exists(_rcp) else {})
# Hearing conduct is display-only and fail-closed.  The legacy July artifact and
# V1 database rows are no longer eligible; only a current hash-bound feed using
# chamber-scoped attribution may enter a surface.
_HEARING_DISPLAY=load_hearing_display()
_HA=(_HEARING_DISPLAY or {}).get('members',{})
name=[];X=[];Y=[];PARTY=[];GEN=[];RANK=[];NL=[];STANCE=[];BP=[];CF=[];ANTAG=[];GRAND=[];SPEECH=[]   # columnar to drop repeated JSON keys
for n in members:
    sm=''.join(TC.get(CS.get(n,{}).get(issue_cat[i],'—'),'-') for i in range(len(issues)))
    c=cong[n]
    name.append(n);X.append(sx(pos[n][0]));Y.append(sy(pos[n][1]))
    PARTY.append(party.get(c.get('bioguide',''),'?'));GEN.append(TC.get(gen.get(n,'—'),'-'))
    RANK.append(rank.get(n,0) or 0);NL.append(round((c.get('net_lean') or 0)*10));STANCE.append(sm);BP.append(round(bp[n]*100));CF.append(CONF.get(n,50))
    ANTAG.append(_HA[n]['antagonism_z'] if n in _HA else None);GRAND.append(_HA[n]['grandstand_z'] if n in _HA else None)
    _sp=_SPEECH_MEMBERS.get(n);_profile=((_SPEECH_PROFILES.get(n) or {}).get('speech_profile') or {})
    if _profile.get('status')=='PASS':
        _ga=_profile.get('general_audio_habits') or {};_hd=(_sp or {}).get('headline_interviews_only') or {}
        _words=((_profile.get('general_word_habits') or {}).get('raw_text_observations') or {})
        # LAYER-AWARE (2026-09-07).  Slot 0 is descriptive CLIP count and is 0 for the 74
        # members whose only audio is the verified voiceprint; the tooltip used to read that
        # zero as "audio coverage limited", which is false for a member with tens of minutes
        # of identity-verified audio.  Slot 8 carries the voiceprint minutes (None when the
        # member has no voiceprint) so the renderer can tell "no audio" from "a different
        # KIND of audio".  It is a separate slot, never merged into slot 1: slot 1 is
        # clip-derived minutes on the raw-clip-mean layer, slot 8 is duration on the
        # within-source-z layer, and the two are not the same measurement.
        _vp=_profile.get('verified_voiceprint_baseline') or {}
        _vpmin=_vp.get('minutes')
        SPEECH.append([int(_ga.get('clips') or 0),round(_ga.get('minutes') or 0,1),
                       len(_profile.get('by_setting') or {}),
                       _hd.get('pitch_var_percentile'),_hd.get('volume_dyn_percentile'),
                       'PASS',_ga.get('coverage_strength') or (
                           'verified_voiceprint_only' if _vpmin is not None else 'word_interaction_only'),
                       int(_words.get('interview_words') or 0),
                       round(_vpmin,1) if _vpmin is not None else None])
    else:
        SPEECH.append(None)
# ---------- COMMITTEE-CLUSTER LAYOUT (Porter et al. 2005): a second set of node positions where members
# cluster by committee. Committees are positioned by NORMALIZED INTERLOCK (shared members / expected-by-chance)
# via a spring layout, then each member sits at the centroid of their committees' anchors (+ deterministic
# jitter); members with no CU-tracked committee ring the periphery. Toggled against the cosponsorship layout.
def mc(n): return MEM_COMM.get(_nfc(n))
comms=sorted({c for s in MEM_COMM.values() for c in s})
NC=len(MEM_COMM); csize={c:sum(1 for s in MEM_COMM.values() if c in s) for c in comms}
CG=nx.Graph(); CG.add_nodes_from(comms)
for ia,a in enumerate(comms):
    for b in comms[ia+1:]:
        shared=sum(1 for s in MEM_COMM.values() if a in s and b in s); exp=csize[a]*csize[b]/NC if NC else 0
        if shared>0 and exp>0: CG.add_edge(a,b,weight=shared/exp)        # normalized interlock
canc=nx.spring_layout(CG,weight='weight',k=0.9,iterations=200,seed=7)
craw={}; ncirc=[n for n in members if not mc(n)]
for k,n in enumerate(ncirc):
    ang=2*math.pi*k/max(1,len(ncirc)); craw[n]=(1.85*math.cos(ang),1.85*math.sin(ang))
for n in members:
    s=mc(n)
    if not s: continue
    ax=sum(canc[c][0] for c in s)/len(s); ay=sum(canc[c][1] for c in s)/len(s)
    rg=random.Random(zlib.crc32(n.encode('utf-8'))); j=0.07   # STABLE per-name seed (Python's hash() is salted per-process -> non-reproducible layout)
    craw[n]=(ax+rg.uniform(-j,j), ay+rg.uniform(-j,j))
cxs=[p[0] for p in craw.values()]; cys=[p[1] for p in craw.values()]
cx0,cx1,cy0,cy1=min(cxs),max(cxs),min(cys),max(cys)
def csx(v):return round(40+920*(v-cx0)/(cx1-cx0))
def csy(v):return round(40+920*(v-cy0)/(cy1-cy0))
CX=[csx(craw[n][0]) for n in members]; CY=[csy(craw[n][1]) for n in members]
COMMS=[[c,csx(canc[c][0]),csy(canc[c][1]),csize[c]] for c in comms]   # committee anchors + sizes for labels
CMI=[ (comms.index(sorted(mc(n))[0]) if mc(n) else -1) for n in members]  # primary committee index per member (tint)
edges=sorted(ew.items(),key=lambda kv:-kv[1])[:420]
E=[[idx[a],idx[b]] for (a,b),w in edges]
# --- layer toggles (user-ratified 2026-07-04): per-drawn-edge provenance code + signed reference layer ---
# Provenance sheet (AUG builder) maps bioguide pairs -> layer; drawn edges get 1 char each:
# c=cosponsor s=social h=hcoi e=engagement m=mixed (missing -> c, the dominant layer).
_prov={}
try:
    _pv=openpyxl.load_workbook(net,data_only=True)['Provenance']
    for r in range(2,_pv.max_row+1):
        a,b,ly=bio2name.get(_pv.cell(r,1).value),bio2name.get(_pv.cell(r,2).value),str(_pv.cell(r,4).value or '')
        if a and b and ly: _prov[frozenset((a,b))]=('m' if '+' in ly else {'cosponsor':'c','social':'s','hcoi':'h','engagement':'e'}.get(ly,'c'))
except KeyError: pass   # non-AUG file without a Provenance sheet -> all edges default to 'c'
ELAY=''.join(_prov.get(frozenset(ab),'c') for ab,w in edges)
# signed cross-media reference layer (member_references.json: CREC/interview/X mentions, Fable-valenced,
# dyad-capped at build time). n_scored>=2 keeps one-off mentions out of the drawing.
REFE=[]
_rfp=os.path.join(MB_ROOT, 'member_references.json')
if os.path.exists(_rfp):
    for _e in json.load(open(_rfp,encoding='utf-8'))['edges']:
        if _e['a'] in idx and _e['b'] in idx and _e.get('n_scored',0)>=2:
            REFE.append([idx[_e['a']],idx[_e['b']],round(_e.get('net',0),2),_e['n_scored']])
# co-appearance layer (data/network/co_appearance.json: substantial joint interview-episode appearances,
# floors + segment-merge at build time). n>=2 keeps one-off co-bookings out of the drawing.
COAP=[]
_cap=os.path.join(MB_ROOT, 'data', 'network', 'co_appearance.json')
if os.path.exists(_cap):
    for _e in json.load(open(_cap,encoding='utf-8'))['edges']:
        if _e['a'] in idx and _e['b'] in idx and _e.get('n',0)>=2:
            COAP.append([idx[_e['a']],idx[_e['b']],_e['n'],1 if _e.get('bipartisan') else 0])
print('layer provenance: %s | reference layer: %d dyads (n_scored>=2) | co-appearance: %d pairs (n>=2)'%(
    {c:ELAY.count(c) for c in 'cshem' if ELAY.count(c)},len(REFE),len(COAP)))
# ---------- HOLISTIC NETWORK LAYER (Paul-ruled 2026-08-18: a live dashboard toggle) ----------
# ADDITIVE AND ABSENT BY DEFAULT. When no holistic_network_* artifact exists at the root this
# block emits nothing and the bundle is byte-identical to a pre-toggle build -- the CU
# configuration remains exactly what every reader gets until they flip the switch.
#
# This supersedes D7(i) ("engines + labeled preview only") on Paul's instruction, and it is the
# work PATH-3 was blocked on: the display graph above comes from this file's OWN hardcoded CU
# glob (:77), never the config-keyed loader, which is why a NET_CONFIG=holistic run of this
# builder previously produced a CU bundle wearing a holistic banner.
#
# The full 62,996-pair graph ships UNPRUNED (~1.0 MB serialized, bundle 481 KB -> ~1.5 MB).
# Pruning to w>=0.2 would halve that but silently change every cascade, path and centrality
# result the toggle produces -- a methodology change disguised as an optimization. Size measured
# before choosing: the desk surface is already 27 MB, so 1.5 MB is not the binding constraint.
#
# Node POSITIONS now follow the configuration (Paul-gated 2026-08-18, superseding the earlier
# fixed-position rule). The controlled-comparison property is preserved by the FRAME, not by
# freezing: all three layouts are warm-started from the CU layout and Procrustes-aligned back
# onto it in one shared canvas frame, so a member that moves has been moved by its tie
# structure, not by solver gauge. Measured on the 63,707-pair holistic graph: median
# displacement 75 of 1000 canvas units, vs 224 for a naive cold start + per-layout rescale --
# two thirds of that naive movement is frame artifact. See PER-CONFIG NODE LAYOUTS below.
HOL_ADJ=[]; HOL_EDGES=[]; HOL_ELAY=''; HOL_META=None
NRM_ADJ=[]; NRM_EDGES=[]; NRM_ELAY=''; NRM_META=None
try:
    _hol_files=glob.glob(os.path.join(MB_ROOT,'holistic_network_BASE_*.xlsx'))+glob.glob(os.path.join(MB_ROOT,'holistic_network_AUG_*.xlsx'))
    if _hol_files:
        _prev=os.environ.get('NET_CONFIG')
        os.environ['NET_CONFIG']='holistic'
        try:
            import moneyball_core as _MCH
            _hadj=_MCH.load_network()
            _hfile=_MCH._c.get('netfile@holistic')
        finally:
            if _prev is None: os.environ.pop('NET_CONFIG',None)
            else: os.environ['NET_CONFIG']=_prev
        _hew={}
        for _u,_nb in _hadj.items():
            if _u not in idx: continue
            for _v,_w in _nb.items():
                if _v not in idx or _u==_v: continue
                _k=(min(idx[_u],idx[_v]),max(idx[_u],idx[_v]))
                if _k not in _hew: _hew[_k]=_w
        HOL_ADJ=[[a,b,round(w,4)] for (a,b),w in sorted(_hew.items())]
        _htop=sorted(_hew.items(),key=lambda kv:-kv[1])[:len(E)]
        HOL_EDGES=[[a,b] for (a,b),w in _htop]
        # provenance per drawn holistic edge, from the holistic AUG Provenance sheet
        _hprov={}
        try:
            _hp=openpyxl.load_workbook(os.path.join(MB_ROOT,_hfile),data_only=True)['Provenance']
            for _r in range(2,_hp.max_row+1):
                _a,_b,_ly=bio2name.get(_hp.cell(_r,1).value),bio2name.get(_hp.cell(_r,2).value),str(_hp.cell(_r,4).value or '')
                if _a in idx and _b in idx and _ly:
                    _hprov[(min(idx[_a],idx[_b]),max(idx[_a],idx[_b]))]=(
                        'm' if '+' in _ly else {'cosponsor':'c','social':'s','hcoi':'h','engagement':'e','coappear':'p'}.get(_ly,'c'))
        except (KeyError,FileNotFoundError): pass
        HOL_ELAY=''.join(_hprov.get(tuple(ab),'c') for ab in HOL_EDGES)
        HOL_META={'available':True,'graph':_hfile,'pairs':len(HOL_ADJ),'drawn':len(HOL_EDGES),
                  'scope':{'cosponsor':'all-bills','engagement':'cu-corpus','hcoi':'cu-corpus',
                           'social':'cu-boolean-vendor','coappear':'all-interviews'},
                  'bpf':'cu-scoped (D12) — cross-party physics keeps the CU-defined bipartisan prior',
                  'note':'all-bills 117-119 universe; original cosponsorship 1.0, regular 0.2 (D1). '
                         'Node positions are this network\'s own force layout, Procrustes-aligned '
                         'onto the credit-union layout so the two share one coordinate frame.'}
        print('holistic layer: %d pairs, %d drawn, from %s'%(len(HOL_ADJ),len(HOL_EDGES),_hfile))
    else:
        print('holistic layer: no holistic_network_*.xlsx at root — toggle omitted (bundle unchanged)')
except Exception as _e:
    HOL_ADJ=[]; HOL_EDGES=[]; HOL_ELAY=''; HOL_META=None
    print('holistic layer: SKIPPED (%s: %s) — bundle falls back to CU-only'%(type(_e).__name__,str(_e)[:90]))

# ---------- NORMALIZED holistic layer (third toggle option, Paul-ruled 2026-08-18) ----------
# Chamber-equalized + per-member mass cap. See build_holistic_normalized.py for the measured
# rationale; the short version is that per-edge normalization cannot remove a degree artifact
# (Katz sums over edges) but capping each member's TOTAL tie mass can, and does: it moves the
# non-voting DC delegate from #15 to #149, right about where the CU graph puts her (#178).
try:
    _nrm=glob.glob(os.path.join(MB_ROOT,'holistic_network_NORM_*.xlsx')) or glob.glob(os.path.join(MB_ROOT,'holistic_network_NBASE_*.xlsx'))
    if _nrm:
        _nf=os.path.basename(max(_nrm,key=os.path.getmtime))
        _prev=os.environ.get('NET_CONFIG'); os.environ['NET_CONFIG']='holistic_norm'
        try:
            import moneyball_core as _MCN
            _nadj=_MCN.load_network()
        finally:
            if _prev is None: os.environ.pop('NET_CONFIG',None)
            else: os.environ['NET_CONFIG']=_prev
        _new={}
        for _u,_nb in _nadj.items():
            if _u not in idx: continue
            for _v,_w in _nb.items():
                if _v not in idx or _u==_v: continue
                _k=(min(idx[_u],idx[_v]),max(idx[_u],idx[_v]))
                _new[_k]=_new.get(_k,0.0)+_w/2.0      # symmetrize for display/kernel parity
        NRM_ADJ=[[a,b,round(w,5)] for (a,b),w in sorted(_new.items())]
        NRM_EDGES=[[a,b] for (a,b),w in sorted(_new.items(),key=lambda kv:-kv[1])[:len(E)]]
        NRM_ELAY='c'*len(NRM_EDGES)
        NRM_META={'available':True,'graph':_nf,'pairs':len(NRM_ADJ),'drawn':len(NRM_EDGES),
                  'note':'all-bills, chamber-equalized, per-member tie-mass capped at 1.5x the median. '
                         'Removes the activity artifact that put a non-voting delegate top of the raw '
                         'all-bills ranking. Media layers admitted under the relaxed rule.'}
        print('normalized layer: %d pairs from %s'%(len(NRM_ADJ),_nf))
except Exception as _e:
    NRM_ADJ=[]; NRM_EDGES=[]; NRM_ELAY=''; NRM_META=None
    print('normalized layer: SKIPPED (%s: %s)'%(type(_e).__name__,str(_e)[:80]))


# ---------- PER-CONFIG NODE LAYOUTS (Paul-gated 2026-08-18) -------------------------------------
# Each network configuration gets its own force layout, emitted in ONE SHARED COORDINATE FRAME so
# the toggle animates structural movement instead of solver gauge. Three things make it shared:
#  1. WARM START from the published CU layout. The FR energy is rotation/reflection invariant, so a
#     cold start lands in an arbitrary orientation; starting at CU makes the solver descend to the
#     NEAREST local minimum, so displacement means "how far the new ties pull this member".
#  2. PROCRUSTES ALIGNMENT back onto the CU canvas coords (rotation + reflection + uniform scale +
#     translation). spring_layout ends with rescale_layout(), which normalizes EVERY layout to
#     max|coord|=1 about its own centroid -- orientation and absolute size carry no information and
#     must not be rendered as movement. Reflection is ALLOWED: a force layout has no handedness, and
#     barring it would leave a mirrored solution permanently misaligned.
#  3. NO per-layout min/max rescaler. Copying the sx/sy pattern (:89-90) per config would pin every
#     config's extreme member to 40 and 960 on both axes -- a second, independent frame.
# k is scaled by the cube root of the mean-weight ratio: the FR energy is invariant under
# (A,k) -> (cA, c**(1/3) k), so holding mean_weight/k**3 equal to CU's runs every config on the same
# spring physics and the three networks' arbitrary weight units cannot masquerade as layout change.
# Reads only members/X/Y/G/HOL_ADJ/NRM_ADJ -- all themselves emitted into the bundle, so the
# published layouts are recomputable from the published artifact alone.
XHOL=YHOL=XNRM=YNRM=None
_WMEAN_CU=sum(_d['weight'] for _,_,_d in G.edges(data=True))/max(1,G.number_of_edges())
def _config_layout(adj_triples):
    """Force layout of an index-keyed edge list, expressed in the CU canvas frame.

    Returns (Xc,Yc,meta) with Xc/Yc roster-length int lists in `members` order, or None."""
    import numpy as _np
    if not adj_triples: return None
    H=nx.Graph(); H.add_nodes_from(members)          # NODE ORDER FIRST: every member gets a position
    for _a,_b,_w in adj_triples:                     # (pos[n] can never KeyError) -- mirrors :79
        if _w>0: H.add_edge(members[_a],members[_b],weight=_w)
    if H.number_of_edges()==0: return None
    _wm=sum(_d['weight'] for _,_,_d in H.edges(data=True))/H.number_of_edges()
    _k=0.45*(_wm/_WMEAN_CU)**(1.0/3.0)
    _warm={n:((X[i]-500)/500.0,(Y[i]-500)/500.0) for i,n in enumerate(members)}
    _raw=nx.spring_layout(H,weight='weight',k=_k,iterations=60,seed=7,pos=_warm)
    _S=_np.array([_raw[n] for n in members],dtype=float)
    _R=_np.array([[X[i],Y[i]] for i in range(len(members))],dtype=float)
    _S0=_S-_S.mean(0); _rm=_R.mean(0); _R0=_R-_rm
    _U,_sv,_Vt=_np.linalg.svd(_S0.T@_R0)
    _Q=_U@_Vt                                        # reflection allowed -- no handedness to protect
    _A=(_S0@_Q)*(_sv.sum()/max(1e-12,float((_S0*_S0).sum())))+_rm
    _has=set()
    for _a,_b,_w in adj_triples:
        if _w>0: _has.add(_a); _has.add(_b)
    _clamped=0; _Xc=[]; _Yc=[]
    for i in range(len(members)):
        if i not in _has:                            # isolate keeps its stable CU reference position
            _Xc.append(X[i]); _Yc.append(Y[i]); continue
        _x=int(round(_A[i,0])); _y=int(round(_A[i,1]))
        if _x<20 or _x>980 or _y<20 or _y>980: _clamped+=1
        _Xc.append(max(20,min(980,_x))); _Yc.append(max(20,min(980,_y)))   # canvas box is 1000x1000
    _d=sorted(math.hypot(_Xc[i]-X[i],_Yc[i]-Y[i]) for i in range(len(members)))
    return _Xc,_Yc,{'k':round(_k,4),'warm_start':'cu_canvas','align':'procrustes(O2+scale+shift)',
                    'frame':'shared CU canvas (no per-layout rescale)',
                    'isolates':len(members)-len(_has),'clamped':_clamped,
                    'median_shift':round(_d[len(_d)//2],1),
                    'p90_shift':round(_d[int(0.9*len(_d))],1),'max_shift':round(_d[-1],1)}
# ---------- PROMOTED SECTOR NETWORKS (Paul 2026-08-18: bank_reg, then crypto) -----------------
# Registry-driven rather than another copy of the HOL_/NRM_ quad: at five configs the per-config
# copy-paste pattern is ~15 hardcoded sites and four N-way ternaries, which is how the normalized
# config ended up fabricating its provenance string and how a single layout failure came to wipe
# every config's layout. Each entry carries its OWN engine-floor numbers, because promoting a
# sector means the engines run on it and the banner has to state how thin it is rather than let
# the toggle imply parity with CU.
SECTORS = {}
# Driven off the SHARED registry, not a literal tuple. The previous version claimed to be
# registry-driven in its comment while iterating a hardcoded ('bank_reg','crypto'), so a sixth
# config added to moneyball_core.NETWORKS would have been silently absent from the bundle -- the
# comment described the intent and the code did something else.
import moneyball_core as _MCREG
for _sec in sorted(k for k, v in _MCREG.NETWORKS.items() if v.get('family') == 'sector'):
    _files = glob.glob(os.path.join(MB_ROOT, 'sector_network_%s_*.xlsx' % _sec))
    if not _files:
        print('sector %s: no sector_network_%s_*.xlsx at root - omitted from the bundle' % (_sec, _sec))
        continue
    try:
        _prev = os.environ.get('NET_CONFIG')
        os.environ['NET_CONFIG'] = _sec
        try:
            import moneyball_core as _MCS
            _sadj = _MCS.load_network()
            _sfile = _MCS._c.get('netfile@' + _sec)
        finally:
            if _prev is None: os.environ.pop('NET_CONFIG', None)
            else: os.environ['NET_CONFIG'] = _prev
        _sew = {}
        for _u, _nb in _sadj.items():
            if _u not in idx: continue
            for _v, _w in _nb.items():
                if _v not in idx or _u == _v: continue
                _k = (min(idx[_u], idx[_v]), max(idx[_u], idx[_v]))
                if _k not in _sew: _sew[_k] = _w
        _adj = [[a, b, round(w, 5)] for (a, b), w in sorted(_sew.items())]
        _top = sorted(_sew.items(), key=lambda kv: -kv[1])[:len(E)]
        _edges = [[a, b] for (a, b), w in _top]
        # These graphs are cosponsorship-only by construction -- no media layer is folded into a
        # sector base -- so 'c' is the TRUE provenance code here, not the fabricated stub the
        # normalized config carries.
        _elay = 'c' * len(_edges)
        _ws = sorted(_sew.values())
        _pf = lambda w: w / (w + 6.0)
        # CU baseline recomputed from the adjacency THIS bundle ships, never pinned. These three
        # numbers are rendered into the sector banner; hardcoding them would keep quoting a stale
        # credit-union network the day CU is refreshed, which is the same failure already corrected
        # twice on this surface.
        # Source is `ew` (:79-84), the CU edge map -- ADJ is not built until :532, well after this
        # block. Rounded to 3 to match exactly what ADJ ships, so the comparator describes the
        # numbers actually in the bundle. NO silent fallback: an empty CU map is a broken build and
        # must raise here rather than emit None comparators into a user-facing banner.
        _cu_w = sorted(round(float(_w), 3) for _w in ew.values())
        if not _cu_w:
            raise RuntimeError('CU edge map empty -- refusing to emit null CU comparators')
        _cu = {'cu_dyads': len(_cu_w),
               'cu_p_max': round(_pf(_cu_w[-1]), 4),
               'cu_dyads_p_ge_010': sum(1 for _w in _cu_w if _pf(_w) >= 0.10)}
        _tied = {i for k in _sew for i in k}
        _iso = len(members) - len(_tied)
        # HUB CONCENTRATION. A sector universe is often one big bill plus a fringe: crypto's top
        # node has degree 167 against a second-place 25, because one CBDC bill carried ~167
        # cosponsors and a star is what a star looks like. Aggregate weakness does not convey that
        # -- a reader seeing "444 pairs" imagines a graph, not a hub. Measured here so the banner
        # can say it without any number being restated by hand.
        _degree = {}
        _wsum = {}
        for (_a, _b), _w in _sew.items():
            _degree[_a] = _degree.get(_a, 0) + 1
            _degree[_b] = _degree.get(_b, 0) + 1
            _wsum[_a] = _wsum.get(_a, 0.0) + _w
            _wsum[_b] = _wsum.get(_b, 0.0) + _w
        _rank = sorted(_degree.items(), key=lambda kv: -kv[1])
        _tw = sum(_sew.values()) or 1.0
        _conc = None
        if _rank:
            _h, _hd = _rank[0]
            _sd = _rank[1][1] if len(_rank) > 1 else 0
            _conc = {'hub': members[_h], 'hub_degree': _hd, 'second_degree': _sd,
                     'hub_share_of_weight_pct': round(_wsum.get(_h, 0.0) / _tw * 100, 1),
                     # "one giant star" is the condition worth calling out on the surface, not
                     # merely a hub existing -- CU has a top node too.
                     'dominated': bool(_sd and _hd >= 3 * _sd)}
        SECTORS[_sec] = {
            'adj': _adj, 'edges': _edges, 'elay': _elay, 'X': None, 'Y': None,
            'meta': {'available': True, 'graph': os.path.basename(_sfile or _files[0]),
                     'pairs': len(_adj), 'drawn': len(_edges),
                     'members_tied': len(_tied), 'members_isolated': _iso,
                     'concentration': _conc,
                     'engine_floor': {'p_max': round(_pf(_ws[-1]), 4) if _ws else 0.0,
                                      'p_median': round(_pf(_ws[len(_ws) // 2]), 4) if _ws else 0.0,
                                      'dyads_p_ge_010': sum(1 for w in _ws if _pf(w) >= 0.10),
                                      **_cu},
                     'scope': {'cosponsor': 'sector-scoped', 'engagement': 'NOT APPLIED',
                               'hcoi': 'NOT APPLIED', 'social': 'NOT APPLIED',
                               'coappear': 'NOT APPLIED'},
                     'bpf': 'cu-scoped - cross-party physics keeps the CU-defined bipartisan prior'},
        }
        _ef = SECTORS[_sec]['meta']['engine_floor']
        print('sector %s: %d pairs, %d drawn, from %s (p_max %.4f vs CU %.4f; %d dyads p>=0.10 vs CU %d; %d isolated)'
              % (_sec, len(_adj), len(_edges), os.path.basename(_sfile or ''),
                 _ef['p_max'], _ef['cu_p_max'], _ef['dyads_p_ge_010'], _ef['cu_dyads_p_ge_010'], _iso))
        if _conc:
            print('    concentration: %s degree %d vs second %d, %.1f%% of edge weight%s'
                  % (_conc['hub'], _conc['hub_degree'], _conc['second_degree'],
                     _conc['hub_share_of_weight_pct'],
                     '  <-- DOMINATED (>=3x second)' if _conc['dominated'] else ''))
    except Exception as _e:
        print('sector %s: SKIPPED (%s: %s)' % (_sec, type(_e).__name__, str(_e)[:90]))

try:
    if HOL_META and HOL_ADJ:
        _r=_config_layout(HOL_ADJ)
        if _r:
            XHOL,YHOL,HOL_META['layout']=_r
            print('holistic layout: k=%s, median shift %s / p90 %s / max %s canvas units, %d clamped'%(
                HOL_META['layout']['k'],HOL_META['layout']['median_shift'],
                HOL_META['layout']['p90_shift'],HOL_META['layout']['max_shift'],
                HOL_META['layout']['clamped']))
    if NRM_META and NRM_ADJ:
        _r=_config_layout(NRM_ADJ)
        if _r:
            XNRM,YNRM,NRM_META['layout']=_r
            print('normalized layout: k=%s, median shift %s / p90 %s / max %s canvas units, %d clamped'%(
                NRM_META['layout']['k'],NRM_META['layout']['median_shift'],
                NRM_META['layout']['p90_shift'],NRM_META['layout']['max_shift'],
                NRM_META['layout']['clamped']))
except Exception as _e:
    XHOL=YHOL=XNRM=YNRM=None
    # clear BOTH, or a failure on the second layout leaves the first advertising one the bundle lacks
    if HOL_META: HOL_META.pop('layout',None)
    if NRM_META: NRM_META.pop('layout',None)
    print('per-config layouts: SKIPPED (%s: %s) - both configs fall back to the CU layout'%(
        type(_e).__name__,str(_e)[:90]))

# Sector layouts compute PER CONFIG with ISOLATED failure. The holistic pair above shares one try
# block, so one bad graph clears both -- at five configs that blast radius is unacceptable, and a
# sector graph is the likeliest to fail (crypto leaves 233 of 533 members isolated).
for _sec, _S in SECTORS.items():
    try:
        _r = _config_layout(_S['adj'])
        if _r:
            _S['X'], _S['Y'], _S['meta']['layout'] = _r
            _L = _S['meta']['layout']
            print('%s layout: k=%s, median shift %s / p90 %s / max %s canvas units, %d clamped'
                  % (_sec, _L['k'], _L['median_shift'], _L['p90_shift'], _L['max_shift'], _L['clamped']))
    except Exception as _e:
        _S['X'] = _S['Y'] = None; _S['meta'].pop('layout', None)
        print('%s layout: SKIPPED (%s: %s) - this config ALONE falls back to the CU layout'
              % (_sec, type(_e).__name__, str(_e)[:80]))


def opt_alloc_thousands(dollars):
    """opt.alloc keeps its unit (thousands of dollars) and loses nothing (F4c, 2026-09-30). A whole-thousand
    amount stays an int, byte-identical to the old encoding; anything else keeps its exact dollars as a decimal
    ($500 -> 0.5). The old round(v/1000) was Python half-even and zeroed every $500 allocation (47 of 389 on
    the 2026-09-20 chain), which efficiency_layers then rebuilt as $0 and labelled from."""
    dollars=int(round(dollars))
    return dollars//1000 if dollars%1000==0 else round(dollars/1000.0,3)
# QME 2026-10-01: flip/coal/Overall use the same dollar-preserving thousands as opt.alloc (F4c) -- half-even round(v/1000) sent 10500->10 and $500->0. Whole thousands stay ints (bytes unchanged); the rest are exact decimals ($500 -> 0.5).
flip=[{str(idx[n]):opt_alloc_thousands(v) for n,v in FL[lb].items() if v>=10000} for lb in source_issues]
coal=[{str(idx[n]):opt_alloc_thousands(v) for n,v in CO[lb].items() if v>0} for lb in source_issues]
stats=[]
for i,lb in enumerate(source_issues):
    col=[STANCE[k][i] for k in range(len(name))]
    stats.append({'champ':col.count('C')+col.count('F'),'ally':col.count('A'),'opp':col.count('O'),'skep':col.count('S'),
                  'flip':sum(FL[lb].values()),'coal':sum(CO[lb].values()),'coalN':len(coal[i])})
B=MB_ROOT
# --- electoral contest/defend plays (per member) ---
EP=json.load(open(os.path.join(B, 'data', 'money', 'electoral_plays.json'))) if os.path.exists(os.path.join(B, 'data', 'money', 'electoral_plays.json')) else {}
def _playcode(n):   # c = contest via PRIMARY (same-party), g = contest via GENERAL (cross-party flip), d = defend, n = none
    e=EP.get(n) or {}; pl=e.get('play','none')
    if pl=='contest': return 'g' if e.get('route')=='general' else 'c'
    return {'defend':'d'}.get(pl,'n')
PLAY=''.join(_playcode(n) for n in members)
EPVAL=[round((EP.get(n) or {}).get('value',0)) for n in members]
# --- nested budget frontier per issue (slider does a live lookup of the precomputed path) ---
if not os.path.exists(os.path.join(B, 'data', 'money', 'budget_frontier.json')): print('  !! budget_frontier.json MISSING — frontier sliders will be EMPTY (run budget_frontier.py)')   # S3-05: silent-empty sections now announce themselves
BF=json.load(open(os.path.join(B, 'data', 'money', 'budget_frontier.json'))) if os.path.exists(os.path.join(B, 'data', 'money', 'budget_frontier.json')) else {'topics':{}}
def frontier_for(lb):
    t=BF['topics'].get(lb)
    if not t: return None
    pts=[[p['b'],p['spread'],(idx.get(p['seed']) if p.get('seed') else -1),(p.get('type','') or '')[:1]] for p in t['path']]
    return {'base':t['base'],'knee':t.get('knee',0),'knee_found':bool(t.get('knee_found')),'core':t.get('core_seeds',0),'path':pts}
frontier=[frontier_for(lb) for lb in source_issues]
# --- joint optimizer (cross-topic CU allocation; explicit fixed-state/replanning guarantee scope) ---
if not os.path.exists(os.path.join(B, 'optimize_result.json')): print('  !! optimize_result.json MISSING — optimizer panel will be EMPTY (run optimize.py)')   # S3-05
OPT=json.load(open(os.path.join(B, 'optimize_result.json'))) if os.path.exists(os.path.join(B, 'optimize_result.json')) else None
opt=None
if OPT:
    # QME 2026-10-01: the old `if n in idx` filters dropped any optimizer name the bundle could not place, silently.
    # The panel must show every planned dollar and conversion: print what is unmatched and fail loudly.
    _oa_drop={n:v for n,v in OPT['alloc'].items() if n not in idx}
    _cv_drop=[c[0] for c in OPT['conversions'] if c[0] not in idx]
    if _oa_drop or _cv_drop:
        print('  !! optimize_result names not in the bundle roster: alloc %d ($%s of $%s planned) %s; conversions %d %s'
              %(len(_oa_drop),format(sum(_oa_drop.values()),',.0f'),format(sum(OPT['alloc'].values()),',.0f'),sorted(_oa_drop),len(_cv_drop),sorted(_cv_drop)))
        raise ValueError('build_dashboard_bundle: %d optimizer alloc name(s) / %d conversion name(s) unmatched to the roster'%(len(_oa_drop),len(_cv_drop)))
    opt={'budget':OPT['budget'],'base':round(OPT['base'],1),'final':round(OPT['final'],1),
         'mode':OPT.get('mode','legacy'),'certificate':bool(OPT.get('certificate_valid')),
         'bound':OPT.get('opt_bound'),'guarantee':OPT.get('guarantee') or {},
         'knee':OPT.get('knee',0),'knee_found':bool(OPT.get('knee_found')),
         'alloc':sorted([[idx[n],opt_alloc_thousands(v)] for n,v in OPT['alloc'].items() if n in idx],key=lambda x:-x[1]),
         'conv':[idx[c[0]] for c in OPT['conversions'] if c[0] in idx]}
# ====== LIVE SCENARIO ENGINE DATA (faithful in-browser port of flip_model + competitive cascade + lt_priming) ======
from engine import flip_model as fmE   # same code tree as this builder; MONEYBALL_ROOT selects data, never code
ADJ=[[idx[a],idx[b],round(w,3)] for (a,b),w in ew.items() if a in idx and b in idx]   # FULL network (engine cascade uses all edges, not just the top-420 drawn)
ENG=[[round(fmE.eng[fmE.cat2sc[issue_cat[i]]][n],3) for n in members] for i in range(len(issues))]   # legislative engagement per issue x member
PRIORITY=[[round(fmE.topic_priority(issue_cat[i],'flip')[n],6) for n in members] for i in range(len(issues))]
ORIENTATION=[[round(fmE.MC.engine_orientation(fmE.CS_SCORES.get(n,{}),issue_cat[i]),6) for n in members] for i in range(len(issues))]
# PUBLISHED-NETWORK PROVENANCE. Every non-cu config records the workbook it came from; CU recorded
# nothing, so the one graph every published figure rests on could not be traced from the artifact.
_CU_META={'available':True,'graph':os.path.basename(net),'pairs':len(ADJ),'published':True,
          'note':'the credit-union network -- what every published figure uses'}
def _envnum(k,d):   # same env semantics as engine/money_response.py + lt_priming.py; int when integral so a default bundle stays byte-identical
    v=float(os.environ.get(k,d)); return int(v) if v==int(v) else v
engine={'adj':ADJ,
        # the holistic adjacency travels beside the CU one; the JS rebuilds its kernel on toggle.
        # Same precedent as the member desk's second directed edge array (HAS_DIR).
        **({'adj_holistic':HOL_ADJ} if HOL_META else {}),
        **({'adj_holistic_norm':NRM_ADJ} if NRM_META else {}),
        **{('adj_'+_k):_v['adj'] for _k,_v in SECTORS.items()},
 'categories':list(issue_cat),
 'cu':[round(fmE.cu0[n]) for n in members],'ind':[round(fmE.ind0[n]) for n in members],
 'rcp':[round(fmE.R[n]) for n in members],
 'tierp':[round(fmE.primacy_priority[n],6) for n in members],
 'priority':PRIORITY,'orientation':ORIENTATION,
 'nlraw':[round(cong[n].get('net_lean') or 0,1) for n in members],'bpf':[round(bp[n],4) for n in members],
 'eng':ENG,
 'const':{'WM':fmE.WM,'WP':fmE.WP,'HALFF':fmE.HALFF,'BASE':fmE.BASE,
          'PRIMACY_LEVELS':{str(k):round(v,6) for k,v in fmE.MC.PRIMACY_ENGINE_LEVELS.items()},
          'PRIORITY_ORIENTATION_ALPHA':fmE.MC.PRIORITY_ORIENTATION_ALPHA,
          'PRIORITY_POLICY':'tier + alpha * fixed-IMP signed leave-one-topic-out orientation; floor after engagement',
          'MAXSHIFT':_envnum('MAXSHIFT','18'),'HALF':_envnum('HALF','0.10'),'DTHRESH':_envnum('DTHRESH','11.25'),'LOSSFRAME':_envnum('LOSSFRAME','1.10'),'BSCALE':400000,'SRCPRO':1.7,'SRCANTI':0.5,'DCROSS':0.4,'CEDGE':_envnum('CEDGE','10000'),'RFLOOR':200000,'MC':20,'SEED':7,
          'LTOPP':1 if os.environ.get('LT_OPP_TARGET','0')=='1' else 0,'LTPAR':0 if os.environ.get('LT_MONEY_PARITY','1')=='0' else 1,'LTHALF':float(os.environ.get('HALF','0.10')) if os.environ.get('LT_MONEY_PARITY','1')!='0' else 0.10},   # QME 2026-10-01: lt_priming dials reach the in-browser prime() (LT_OPP_TARGET / LT_MONEY_PARITY / HALF), same env semantics as engine/lt_priming.py
 'pers':fmE.PERS,'theta':{'Highly Persuadable':0.6,'Persuadable':0.8,'Lean Persuadable':0.9,
                          'True Neutral':1.0,'Neutral':1.0,'Lean Skeptic':1.5,'Skeptic':2.0,
                          'Highly Skeptical':2.5,'Opponent':3.0,'Ally':0.0},
 'refflip':[round(fmE.flip_value(issue_cat[i],fmE.CS0,fmE.cu0,fmE.ind0),3) for i in range(len(issues))]}   # Python baseline per issue -> in-browser self-check
# psych parity for the in-browser engine (2026-07-09, closes the audit N-1 JS half): the JS mirror
# previously consumed NONE of the receptivity multipliers the Python engine applies (pressure_mult on
# money terms, delivery_mult on target value, edge_mult in the cascade kernel) -> ship all three
# (neutral 1.0 fallback keeps old bundles working).
engine['pmult']=[round(fmE.rc(n,'pressure_mult',1.0),3) for n in members]
engine['emult']=[round(fmE.rc(n,'edge_mult',1.0),3) for n in members]
engine['dmult']=[round(fmE.MC.delivery_mult(n),3) for n in members]
# COMPET_MULT (Paul 2026-10-05; default ON since Paul 2026-10-06): the competitiveness multiplier c travels with the engine so the in-browser flip/priming/break-even math
# (tpl: EG.cmult -> CMU) and the member desk's ROI (eng.get('cmult')) use the SAME c as optimize/flip_model. Added ONLY when the dial is ON, so a
# dial-OFF bundle is byte-identical to the pre-dial one; old bundles lack it -> neutral 1.0.
if fmE.MC.compet_on():
    engine['cmult']=[fmE.CM[n] for n in members]
    engine['const']['COMPET']={'amplitude':fmE.MC.COMPET_AMPLITUDE,'scale':fmE.MC.COMPET_SCALE,'artifact_sha256':fmE.MC.compet_record()[1]}
# ---- Noble & Turner (2024) going-public parameters per issue -> System-shocks exec cue ----
# z = sincere polarization (D-R stance gap on the issue); psi = missing posture + True-Neutral
# uncertainty. The exact estimator is shared with salience_shock.py in moneyball_core.
_DEF=set(CS_DOC['defensive'])
engine['sal']=[]
_party_by_member={name[k]:PARTY[k] for k in range(len(name))}
for i in range(len(issues)):
    _sp=salience_issue_params(CS,name,_party_by_member,issue_cat[i],_DEF)
    engine['sal'].append({'z':_sp['z'],'p':_sp['p'],'q':_sp['q'],'b':_sp['b'],
                          'def':1 if _sp['sq']=='defend' else 0})
engine['const']['EXECP']='R'   # executive party for the going-public cue (119th Congress)
# go-public advisory verdicts (salience_shock.py --advise), surfaced in the System-shocks panel
_advmap={}
try:
    _advl=json.load(open(os.path.join(MB_ROOT, 'salience_advisory.json')))['advisory']
    if not _advl: print('  !! salience_advisory.json advisory list EMPTY - advisory column will be EMPTY')
    else:
        _cut=sorted((a['value'] for a in _advl),reverse=True)[max(0,len(_advl)//4-1)]
        for a in _advl:
            v='GO PUBLIC' if (a['value']>=_cut and a['value']>0) else 'quiet work'
            if a.get('defections_prevented'): v+=' · holds %d'%len(a['defections_prevented'])
            _advmap[a['issue']]=v
except FileNotFoundError: print('  !! salience_advisory.json MISSING — advisory column will be EMPTY (run salience_shock.py --advise)')   # S3-05: was `except Exception: pass`, which also buried schema drift forever
engine['adv']=[_advmap.get(issue_cat[i],'') for i in range(len(issues))]
# ===== Overall CU stance as a selectable ISSUE layer (general stance, toggleable like Interchange) =====
_ovf={str(k):opt_alloc_thousands(sum(FL[lb].get(name[k],0) for lb in source_issues)) for k in range(len(name)) if sum(FL[lb].get(name[k],0) for lb in source_issues)>=10000}
_ovc={str(k):opt_alloc_thousands(sum(CO[lb].get(name[k],0) for lb in source_issues)) for k in range(len(name)) if sum(CO[lb].get(name[k],0) for lb in source_issues)>0}
issues.append('Overall CU stance')
engine['categories'].append(None)
for k in range(len(name)): STANCE[k]=STANCE[k]+GEN[k]                                   # this issue's stance = overall (R&A col 7) tier
flip.append(_ovf); coal.append(_ovc); frontier.append(None)                              # no single-topic frontier for the aggregate layer
engine['eng'].append([round(sum(engine['eng'][i][k] for i in range(len(engine['eng'])))/max(1,len(engine['eng'])),3) for k in range(len(name))])  # overall engagement = mean per-issue eng
engine['priority'].append([round(fmE.MC.blended_engine_priority(
    fmE.primacy_tier[n],fmE.CS_SCORES.get(n,{}),None,'flip'),6) for n in members])
engine['orientation'].append([round(fmE.MC.engine_orientation(
    fmE.CS_SCORES.get(n,{}),None),6) for n in members])
engine['refflip'].append(0)                                                              # synthetic layer -> engchk self-check skips it
engine['sal'].append({'z':0,'p':0.6,'q':0.3,'b':0.1,'def':0})                            # neutral going-public params for the aggregate layer
stats.append({'champ':GEN.count('C')+GEN.count('F'),'ally':GEN.count('A'),'opp':GEN.count('O'),'skep':GEN.count('S'),'flip':sum(_ovf.values())*1000,'coal':sum(_ovc.values())*1000,'coalN':len(_ovc)})
# ===== PRIMACY TIER — engine priority anchor; definition in fec/tier_util.py =====
# The official rank remains a four-pillar reporting score. Engines anchor on
# the tier, then add the small leave-one-topic-out orientation adjustment
# shipped in engine.priority. Topic engagement, issue movability, network,
# money, and receptivity remain separate inputs.
TIER=[_tier.get(n,0) for n in members]; TIERRANK=[_trank.get(n,0) for n in members]   # aligned to members/name order
TIERDETAIL=[_tierdetail.get(n) for n in members]
print('tier (legislative primacy + net conduct): T1=%d T2=%d T3=%d untiered=%d'%(
    TIER.count(1),TIER.count(2),TIER.count(3),TIER.count(0)))
influence_policy=DLW.policy_bundle(issue_cat,[
    {'name':members[i],'stances':STANCE[i][:-1],
     'committees':MEM_SEATS.get(_nfc(members[i]),[])}
    for i in range(len(members))
])
influence_policy['domains'].append({
    'area':'Overall CU stance',
    **DLW.domain_profile(GEN),
})
_stance_curve=CS_DOC.get('curve') or {}
_stance_curve_meta={
    key:value for key,value in _stance_curve.items()
    if key!='label_scores'
}
_stance_curve_meta['issue_indices']=[
    i for i,category in enumerate(issue_cat)
    if category in set(_stance_curve.get('categories') or [])
]
_role_strength={'-':0,'v':1,'r':2,'c':3}
for i,codes in enumerate(influence_policy['roles']):
    strongest=max(codes or '-',key=lambda code:_role_strength.get(code,0))
    influence_policy['roles'][i]=codes+strongest
# ===== COALITION POSTURE — per-bill contested/advisory registry (fec/lobbying/contested_bills.py, ratified @a120b9d) =====
# Standard mode is the STRUCTURAL default: an absent registry means no second-industry term anywhere, so peace
# is guaranteed by construction rather than by remembering to unset a flag. Advisory records are display-only
# beside the standard ranking and never reorder it; contested records carry a per-bill counterparty industry
# set (never a global one). DISPLAY-ONLY here — this block is surfaced in the header Posture control and is fed
# to NO engine, so it moves no rank. The CONTESTED_V1 consumer gate is unopened and remains Paul's to open.
# Fail-loud matches the registry contract: absent is fine, present-but-malformed must never read as absent.
from lobbying import contested_bills as _CB
try: _reg=_CB.load_contested_bills()
except _CB.ContestedBillsError as _e:
    print('  !! contested_bills registry PRESENT BUT INVALID — refusing to silently fall back to standard mode'); raise
def _posture_rec(r):
    return {'bill':r['bill'],'congress':r['congress'],'mode':r['mode'],'ind':r['counterparty_industries'],
            'from':r['active_from'].isoformat(),'to':(r['active_to'].isoformat() if r['active_to'] else ''),
            'by':r['ratified_by'],'note':r['note']}
POSTURE={'status':_reg['status'],'file':os.path.basename(_reg['path']),'consumer':False,
         'active':[_posture_rec(r) for r in _reg['active']],
         'dormant':[_posture_rec(r) for r in _reg['dormant']]}
print('coalition posture: registry %s — %d active, %d dormant (display-only; no engine consumer wired)'%(
    _reg['status'],len(_reg['active']),len(_reg['dormant'])))
bundle={'issues':issues,'name':name,'x':X,'y':Y,'party':''.join(PARTY),'gen':''.join(GEN),
        **dashboard_honors_payload(HONORS_DOC,name,issue_cat,overall_stances_from_gen(name,GEN)),
        'rank':RANK,'nl':NL,'bp':BP,'cf':CF,'stance':STANCE,'edges':E,'flip':flip,'coal':coal,'stats':stats,
        'play':PLAY,'epval':EPVAL,'frontier':frontier,'opt':opt,'engine':engine,
        'cx':CX,'cy':CY,'comms':COMMS,'cmi':CMI,   # committee-cluster layout (Porter): positions + anchors + per-member primary committee
        'elay':ELAY,'refs':REFE,'coap':COAP,   # layer toggles: per-drawn-edge provenance code + signed reference dyads + joint-appearance pairs
        # HOLISTIC TOGGLE payload — present only when a holistic artifact exists at the root,
        # so a CU-only tree emits exactly the bundle it always did (verified by hash).
        **({'edges_holistic':HOL_EDGES,'elay_holistic':HOL_ELAY,'holistic':HOL_META} if HOL_META else {}),
        **({'edges_holistic_norm':NRM_EDGES,'elay_holistic_norm':NRM_ELAY,'holistic_norm':NRM_META} if NRM_META else {}),
        # per-config node layouts, in the SAME coordinate frame as 'x'/'y' (Procrustes-aligned
        # onto the CU layout). Absent -> the JS falls back to DATA.x/DATA.y and nothing moves.
        **({'x_holistic':XHOL,'y_holistic':YHOL} if XHOL else {}),
        **({'x_holistic_norm':XNRM,'y_holistic_norm':YNRM} if XNRM else {}),
        # promoted sector networks -- same shape, emitted from the registry rather than by hand
        'cu':_CU_META,
        **{('edges_'+_k):_v['edges'] for _k,_v in SECTORS.items()},
        **{('elay_'+_k):_v['elay'] for _k,_v in SECTORS.items()},
        **{_k:_v['meta'] for _k,_v in SECTORS.items()},
        **{('x_'+_k):_v['X'] for _k,_v in SECTORS.items() if _v['X']},
        **{('y_'+_k):_v['Y'] for _k,_v in SECTORS.items() if _v['Y']},
        'recept':[round((_RC.get(n) or {}).get(fmE.MC.pressure_field(),1.0),2) for n in members],   # behavioral receptivity (colorBy=recept); R1 (Paul ruling 2026-10-07): the engines' field
        'antag':ANTAG,'grand':GRAND,   # ratified hearing-antagonism facet (colorBy=antag/grand); DISPLAY-ONLY, rank-independent
        **({'speech':SPEECH,'speech_meta':{
            'schema':_SPEECH_ART['schema'],'status':_SPEECH_ART['status'],
            'contract':_SPEECH_ART['contract'],'corpus':_SPEECH_ART['corpus']}}
           if _SPEECH_ART else {}),   # observed audio context; DISPLAY-ONLY, never read by engine construction
        **({'speech_profile_meta':{
            'schema':_SPEECH_PROFILE_ART['schema'],'status':'PASS_ALL_MEMBERS',
            'basis_sha256':_SPEECH_PROFILE_ART['basis_sha256'],
            'counts':_SPEECH_PROFILE_ART['counts'],'method':_SPEECH_PROFILE_ART['method']}}
           if _SPEECH_PROFILE_ART and _SPEECH_PROFILE_OK else {}),   # a WITHHELD artifact never publishes a PASS receipt
        'tier':TIER,'tierrank':TIERRANK,'tierdetail':TIERDETAIL,   # Legislative+Press tier (1/2/3, 0=untiered) + its leg+press rank; DISPLAY-ONLY, rank-independent
        'legislation_culling':{
            'status':CULLING_STATUS.get('status'),
            'phase':CULLING_STATUS.get('phase'),
            'promotion_performed':CULLING_STATUS.get('promotion_performed'),
            'counts':CULLING_STATUS.get('counts') or {},
            'generation_id':CULLING_STATUS.get('generation_id'),
            'provenance':{
                'staged_receipt':CULLING_STATUS.get('staged_receipt'),
                'promotion_receipt':CULLING_STATUS.get('promotion_receipt'),
            },
        },
        'stance_curves':_stance_curve_meta,
        'influence_policy': influence_policy,   # Legislative+Press tier + interactive-only domain/chair policy
        'posture':POSTURE}   # coalition posture registry (standard/advisory/contested); DISPLAY-ONLY, rank-independent, no engine consumer
out=os.path.join(MB_ROOT, 'dashboard_data.json'); _bp=os.path.join(MB_ROOT, 'bipartisan.json')
_dashout=os.environ.get('DASH_OUT')   # no-clobber redirect for preview/test runs (matches FRONTIER_OUT/OPT_OUT); prod = unset -> live tree
if _dashout:
    out=_dashout; _bp=os.path.join(os.path.dirname(os.path.abspath(_dashout)) or '.','bipartisan.json')
from engine.effectiveness_integration import optional_shadow_explanations
bundle=optional_shadow_explanations(bundle,out)
dump_atomic({name[i]:BP[i] for i in range(len(name))},_bp)
dump_atomic(bundle,out,separators=(',',':'))
print('wrote %s  (%.1f KB)  nodes=%d edges=%d issues=%d'%(out,os.path.getsize(out)/1024,len(name),len(E),len(issues)))
