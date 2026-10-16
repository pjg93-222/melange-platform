# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT,today  # centralized root (arch review 2026-07-02)
"""MULTILAYER NETWORK BUILDER — integrates the press-derived layers into the live cascade network.

Writes `congress_cosponsor_network_AUG<date>.xlsx` in the exact Edges format the NINE existing loaders
glob by mtime (`congress_cosponsor_network_*.xlsx`) — so the augmented graph goes live pipeline-wide with
ZERO loader changes, and REVERT = delete the AUG file (the original xlsx is never touched).

Layer policy (conservative, provenance-tracked in a second sheet):
  1. COSPONSOR edges: copied verbatim — the augmentation never modifies an existing tie.
  2. ENGAGEMENT edges (press_engagement.json, Fink-normalized empirical influence): added for pairs not
     already tied.  The cascade kernel is p = w/(w+6), so an engagement probability p maps to weight
     w = 6p/(1-p), capped at 2.0 (a strong cosponsor tie).  These are EMPIRICAL probabilities (Fink et
     al. 2023) — the one layer whose weights arrive already in probability units.
  3. HCoI edges (press_hcoi.json, who-talks-about-the-same-issues): added ONLY when incident to a
     cosponsor-ISOLATED member (the D4 fix: leadership doesn't cosponsor by norm; Hoyer/Clyburn/Scalise/
     Jordan/Jason Smith were unreachable by every cascade/route).  Weight = 0.6*r*HCOI_W (default cap
     0.12, i.e. p~0.02 per step — a weak interest-affinity tie, NOT a collaboration claim).  Adding all
     1,239 HCoI edges would triple graph density on a similarity signal; surgical is the honest choice.
Env dials: ENG_CAP (2.0), HCOI_W (0.2 -> max weight 0.12), AUG_OUT (filename override).
After building, run audit_full.py — the whole 40-check suite then validates the augmented graph."""
import json,sys,os,re,glob,collections,datetime,hashlib
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
import openpyxl
B=MB_ROOT
ENG_CAP=float(os.environ.get('ENG_CAP','2.0')); HCOI_W=float(os.environ.get('HCOI_W','0.2'))
# NET_CONFIG (holistic v1; freeze addendum 2026-08-17): 'cu' default = byte-identical behavior;
# 'holistic' re-points the base pick to the holistic BASE family (fail-closed), makes a missing
# layer input a HARD failure (PAR-5 — the artifact claims a layer inventory), adds the D2
# co-appearance layer (fixed per-episode weight w=min(COAP_CAP, COAP_W*n), untied-only, floor
# n>=COAP_FLOOR — the guide's §4.2 sanctioned "fixed per-episode weight" option; cap = HCoI
# ceiling 0.12 (D2a, 2026-10-11; COAP_CAP=0.5 restores the pre-D2a artifacts)), and writes a scope-labeled manifest beside the output (HON-1).
CFG=os.environ.get('NET_CONFIG','cu')
# Validate against the SHARED registry, not a local tuple: moneyball_core grew to five configs
# when bank_reg and crypto were promoted, and a stale copy here refused them with a message
# saying the value was invalid when it is valid and simply not augmentable.
from moneyball_core import NETWORKS as _NETS, cu_dropped_sponsors, cu_edge_rows, cu_backfill_ties, save_workbook_atomic, dump_atomic
if CFG not in _NETS:
    raise SystemExit('NET_CONFIG must be one of %s, got %r' % ('|'.join(sorted(_NETS)), CFG))
if _NETS[CFG].get('family') == 'sector':
    # Refusing is CORRECT and deliberate, not an oversight: a sector graph is cosponsorship-only
    # by construction -- the press, HCoI, social and co-appearance corpora are all CU-scoped, so
    # folding them onto a sector base would attach credit-union media mass to a sector label.
    # The dashboard banner states 'COSPONSORSHIP ONLY' on the strength of this refusal.
    raise SystemExit('NET_CONFIG=%s: sector networks are cosponsorship-only BY DESIGN and are '
                     'never augmented -- the media layers are CU-scoped corpora. Build them with '
                     'fec/network/build_sector_diagnostic.py (SECTOR_PROMOTE=1).' % CFG)
if os.environ.get('NET_VALIDATE_ONLY') == '1' and CFG not in (None,):
    # VALIDATE-ONLY EXIT. This module builds on IMPORT (a known property of the producers here --
    # "import == execution"), so there was no way to test the config guard above without writing an
    # AUG artifact. I proved that the hard way on 2026-08-19: importing this file to check that it
    # refused a sector config silently produced holistic_network_AUG_20260819.xlsx. It was content-
    # identical to the reviewed artifact and was moved aside, but it had already displaced it in
    # newest-by-mtime selection. A guard you cannot exercise without side effects is a guard nobody
    # will exercise.
    print('NET_CONFIG=%s accepted for augmentation (validate-only; nothing written)' % CFG)
    raise SystemExit(0)
HOL=CFG in ('holistic','holistic_norm')
# MEDIA ADMISSION (D10 fork, RELAXED on Paul's ruling 2026-08-18).
#   'untied'    -- the original rule: a media edge is admitted only for a pair with no
#                  cosponsorship tie. On the CU base that was a sensible density guard. On the
#                  all-bills base it went nearly vacuous, because almost every pair is already
#                  tied: media fell to 1.5% of holistic pairs against 28.8% of CU pairs, and the
#                  HCoI layer admitted exactly ZERO edges.
#   'dominance' -- the ruled relaxation: a tied pair takes max(cosponsor, media), so a
#                  relationship that is ALSO live in the press or on social keeps whichever
#                  channel says the tie is stronger. This is an explicit departure from
#                  "same methodology as CU" and it re-injects CU-corpus media mass onto an
#                  all-bills graph -- which is exactly why it is labeled in the manifest and on
#                  the surface rather than done quietly.
MEDIA_ADMIT=os.environ.get('MEDIA_ADMIT','dominance' if HOL else 'untied')
if MEDIA_ADMIT not in ('untied','dominance'): raise SystemExit('MEDIA_ADMIT must be untied|dominance')
def admit(k,layer,w):
    """Admit a media edge. Returns True if written. Under 'dominance' a tied pair is raised to
    max(existing, w) and its layer tag gains the channel, so provenance stays honest."""
    if k not in edges:
        edges[k]=(layer,round(w,4)); have.add(k); return True
    if MEDIA_ADMIT!='dominance': return False
    lay0,w0=edges[k]
    if w<=w0: return False
    edges[k]=(lay0+'+'+layer if layer not in lay0 else lay0, round(w,4)); return True
COAP_W=float(os.environ.get('COAP_W','0.05')); COAP_CAP=float(os.environ.get('COAP_CAP','0.12')); COAP_FLOOR=int(os.environ.get('COAP_FLOOR','2'))
C={m['member']:m for m in json.load(open(os.path.join(B, 'data', 'engine', 'congestion.json')))}
name2bio={m:C[m]['bioguide'] for m in C if C[m].get('bioguide')}
bio2name={v:k for k,v in name2bio.items()}
# 1) original cosponsor edges (pick the newest NON-augmented file so re-runs don't compound)
if CFG=='cu':
    srcs=[p for p in glob.glob(os.path.join(B, 'congress_cosponsor_network_*.xlsx')) if 'AUG' not in os.path.basename(p)]
elif CFG=='holistic_norm':
    srcs=glob.glob(os.path.join(B, 'holistic_network_NBASE_*.xlsx'))
    if not srcs: raise SystemExit('NET_CONFIG=holistic_norm: no holistic_network_NBASE_*.xlsx at root (run build_holistic_normalized.py first; fail-closed)')
else:
    srcs=glob.glob(os.path.join(B, 'holistic_network_BASE_*.xlsx'))
    if not srcs: raise SystemExit('NET_CONFIG=holistic: no holistic_network_BASE_*.xlsx at root (fail-closed; the augmenter never bases on the CU family in holistic mode)')
src=max(srcs,key=os.path.getmtime)
ws=openpyxl.load_workbook(src,data_only=True)['Edges']
edges={}; deg=collections.Counter()
_drop=cu_dropped_sponsors(src); dropped_misattributed=0   # CU_IDENTITY_FIX (decision G 2026-10-11)
for a,b,w in cu_edge_rows(src,ws):   # + CU_PLACEHOLDER_BACKFILL (2026-10-11)
    if a in _drop: dropped_misattributed+=1; continue
    if a and b and isinstance(w,(int,float)):
        k=(min(a,b),max(a,b))
        # deep-audit fix 2026-07-02: the source xlsx carries 41 reverse-dup pairs with asymmetric weights
        # (the old D2 wart). Loaders mean-symmetrize anyway; symmetrize HERE too so the AUG artifact is
        # clean at rest (one row per pair, mean weight) instead of reproducing the wart.
        if k in edges: edges[k]=('cosponsor',(edges[k][1]+w)/2)
        else: edges[k]=('cosponsor',w)
        if a in bio2name: deg[bio2name[a]]+=1
        if b in bio2name: deg[bio2name[b]]+=1
have=set()
for a,b in edges: have.add((min(a,b),max(a,b)))
isolated={m for m in C if deg[m]==0}
print('base: %s (%d edge rows) | cosponsor-isolated members: %d'%(os.path.basename(src),len(edges),len(isolated)))
# 2) engagement layer (empirical influence probabilities -> kernel-inverse weights)
neweng=0
try:
    pe=json.load(open(os.path.join(B, 'data', 'stance', 'press_engagement.json'),encoding='utf-8'))
    for e in pe['symmetric']:
        a,b=name2bio.get(e['a']),name2bio.get(e['b'])
        if not a or not b: continue
        k=(min(a,b),max(a,b))
        w=min(ENG_CAP,6*e['w']/max(1e-6,1-e['w']))
        if w<0.01: continue
        if admit(k,'engagement',w): neweng+=1   # N-2 (audit 2026-07-08): key by sorted k (not raw a,b) so the social branch's `k in edges` reinforcement lookup matches instead of double-writing the pair
except FileNotFoundError:
    if HOL: raise SystemExit('PAR-5: press_engagement.json missing — the holistic AUG refuses to silently drop a declared layer')
    print('  (press_engagement.json missing — run press_engagement_layer.py)')
# 3) HCoI layer — ONLY edges touching a cosponsor-isolated member (the D4 surgical fix)
newhc=0; reconnected=set()
try:
    hc=json.load(open(os.path.join(B, 'data', 'stance', 'press_hcoi.json'),encoding='utf-8'))
    for e in hc['edges']:
        # The ISOLATES-ONLY gate is the second half of the D10 admission rule and it goes fully
        # vacuous on an all-bills base: with 0 cosponsor-isolated members it admitted exactly
        # ZERO HCoI edges. Under the relaxed rule the topic-affinity layer is admitted on its own
        # weight like every other channel; the CU config keeps the original surgical gate.
        if MEDIA_ADMIT != 'dominance' and e['a'] not in isolated and e['b'] not in isolated: continue
        a,b=name2bio.get(e['a']),name2bio.get(e['b'])
        if not a or not b: continue
        k=(min(a,b),max(a,b))
        if admit(k,'hcoi',0.6*e['r']*HCOI_W): newhc+=1   # N-2 (audit 2026-07-08): key by sorted k (see engagement branch)
        for m in (e['a'],e['b']):
            if m in isolated: reconnected.add(m)
except FileNotFoundError:
    if HOL: raise SystemExit('PAR-5: press_hcoi.json missing — the holistic AUG refuses to silently drop a declared layer')
    print('  (press_hcoi.json missing — run press_hcoi.py)')
# 4) SOCIAL INTERACTION layer (social_interaction_layer.py: member reposts + mentions, Fink-normalized)
#    — user 2026-07-02: posts/reposts enter as BOTH network linkages AND edge weights:
#    (a) NEW EDGE for untied pairs: same kernel-inverse mapping as engagement, w=6p/(1-p), cap SOC_CAP;
#    (b) REINFORCEMENT for already-tied pairs: w *= 1 + SOC_REINF*min(1, p/0.10) — a pair at or above
#        10% interaction probability gets the full boost (default +30%, SOC_REINF=0 disables). A repost
#        channel on top of cosponsorship means the tie is live in public, not just on paper.
SOC_CAP=float(os.environ.get('SOC_CAP','2.0')); SOC_REINF=float(os.environ.get('SOC_REINF','0.3'))
newsoc=0; reinf=0
try:
    si=json.load(open(os.path.join(B, 'data', 'lobbying', 'social_interactions.json'),encoding='utf-8'))
    for e in si['symmetric']:
        a,b=name2bio.get(e['a']),name2bio.get(e['b'])
        if not a or not b: continue
        k=(min(a,b),max(a,b))
        if k in edges:
            lay,w0=edges[k]
            edges[k]=(lay+'+social',round(w0*(1.0+SOC_REINF*min(1.0,e['w']/0.10)),4)); reinf+=1
        else:
            w=min(SOC_CAP,6*e['w']/max(1e-6,1-e['w']))
            if w<0.01: continue
            edges[k]=('social',round(w,4)); have.add(k); newsoc+=1
except FileNotFoundError:
    if HOL: raise SystemExit('PAR-5: social_interactions.json missing — the holistic AUG refuses to silently drop a declared layer')
    print('  (social_interactions.json missing — run social_interaction_layer.py)')
# 4b) CO-APPEARANCE layer (holistic only; D2 promoted at the 2026-08-17 freeze): joint interview
#     episodes -> weak context ties of ambiguous sign (QME SOC-24: shared context, not alignment).
#     Fixed per-episode weight w=min(COAP_CAP, COAP_W*n); cap = HCoI ceiling 0.12 (D2a,
#     2026-10-11, audits/decisions_20261011/G_holistic_network.md -- the old 0.5 outranked 77% of
#     all-bills cosponsor ties and claimed an HCoI precedent it did not match),
#     floor n>=COAP_FLOOR (mirrors the drawing floor), UNTIED-ONLY admission (D10 keep-rules —
#     vacuity on a dense base is measured at MEA-4, never assumed away).
newcoap=0
if HOL:
    try:
        cap=json.load(open(os.path.join(B, 'data', 'network', 'co_appearance.json'),encoding='utf-8'))
    except FileNotFoundError:
        raise SystemExit('PAR-5: co_appearance.json missing — the holistic AUG refuses to silently drop a declared layer')
    for e in cap['edges']:
        if e.get('n',0)<COAP_FLOOR: continue
        a,b=name2bio.get(e['a']),name2bio.get(e['b'])
        if not a or not b: continue
        k=(min(a,b),max(a,b))
        if k in have: continue
        w=min(COAP_CAP,COAP_W*e['n'])
        if w<0.01: continue
        if admit(k,'coappear',w): newcoap+=1
# 5) write the augmented workbook (Edges sheet = loader contract; Provenance sheet = audit trail)
if CFG=='cu':
    out=os.environ.get('AUG_OUT',os.path.join(B, 'congress_cosponsor_network_AUG%s.xlsx'%today().strftime('%Y%m%d')))
else:
    _pfx='holistic_network_NORM_' if CFG=='holistic_norm' else 'holistic_network_AUG_'
    out=os.environ.get('AUG_OUT',os.path.join(B,_pfx+today().strftime('%Y%m%d')+'.xlsx'))
    if re.match(r'^congress_cosponsor_network',os.path.basename(out),re.I) or not os.path.basename(out).startswith(_pfx):
        raise SystemExit('COR-9: %s output must be named %s*.xlsx, got %s'%(CFG,_pfx,os.path.basename(out)))
wb=openpyxl.Workbook(); e1=wb.active; e1.title='Edges'
e1.append(['source_bioguide','target_bioguide','weight'])
pr=wb.create_sheet('Provenance'); pr.append(['source_bioguide','target_bioguide','weight','layer'])
for (a,b),(lay,w) in sorted(edges.items()):
    e1.append([a,b,w]); pr.append([a,b,w,lay])
pr.append([]); pr.append(['built',datetime.datetime.now().isoformat(),'base',os.path.basename(src)])
_hc_clause='hcoi on dominance (admitted whether or not members are isolated; tied pairs take max)' if MEDIA_ADMIT=='dominance' else 'hcoi only isolated-incident'
_coap_clause=('; co-appearance untied-only w=min(%.2f,%.2f*n), n>=%d'%(COAP_CAP,COAP_W,COAP_FLOOR)) if HOL else ''
pr.append(['policy','cosponsor verbatim; engagement w=6p/(1-p) cap %.1f; %s, w=0.6*r*%.2f; social new-edge cap %.1f + reinforcement x(1+%.2f*min(1,p/.10)) on tied pairs%s; MEDIA_ADMIT=%s'%(ENG_CAP,_hc_clause,HCOI_W,SOC_CAP,SOC_REINF,_coap_clause,MEDIA_ADMIT)])
if _drop: pr.append(['identity_fix','CU_IDENTITY_FIX dropped %d misattributed out-edges of %s (Menendez, decision G 2026-10-11)'%(dropped_misattributed,','.join(sorted(_drop)))])
_bf=cu_backfill_ties(src)
if _bf: pr.append(['placeholder_backfill','CU_PLACEHOLDER_BACKFILL summed %d directed BILLSTATUS ties (weight %.4f) from bills lost to the placeholder DetailLink (2026-10-11)'%(len(_bf),sum(_bf.values()))])
pr.append(['revert','delete this file — loaders fall back to the newest plain cosponsor xlsx'])
save_workbook_atomic(wb,out)
if HOL:
    def _sha(pth):
        try: return hashlib.sha256(open(pth,'rb').read()).hexdigest()[:16]
        except OSError: return None
    _man={'built_utc':datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'config':CFG,'vintage':'NORM' if CFG=='holistic_norm' else 'AUG','base':os.path.basename(src),'base_sha256_16':_sha(src),
        'dials':{'ENG_CAP':ENG_CAP,'HCOI_W':HCOI_W,'SOC_CAP':SOC_CAP,'SOC_REINF':SOC_REINF,
                 'COAP_W':COAP_W,'COAP_CAP':COAP_CAP,'COAP_FLOOR':COAP_FLOOR},
        'admission':'MEDIA_ADMIT=%s (D10: dominance = tied pairs take max(cosponsor,media); relaxed on Paul ruling 2026-08-18 because untied-only went near-vacuous on the all-bills base)'%MEDIA_ADMIT,
        'layer_scope':{'cosponsor':'all-bills','engagement':'cu-corpus','hcoi':'cu-corpus',
                       'social':'cu-boolean-vendor','coappear':'all-interviews'},
        'bpf':'cu-scoped (D12: cross-party physics consumes CU-defined bipartisan.json until separately ruled)',
        'admitted':{'engagement':neweng,'hcoi':newhc,'social_new':newsoc,'social_reinforced':reinf,'coappear':newcoap},
        'layer_inputs_sha256_16':{'press_engagement.json':_sha(os.path.join(B, 'data', 'stance', 'press_engagement.json')),
                                  'press_hcoi.json':_sha(os.path.join(B, 'data', 'stance', 'press_hcoi.json')),
                                  'social_interactions.json':_sha(os.path.join(B, 'data', 'lobbying', 'social_interactions.json')),
                                  'co_appearance.json':_sha(os.path.join(B, 'data', 'network', 'co_appearance.json'))}}
    dump_atomic(_man,out.replace('.xlsx','.manifest.json'),indent=1)
print('AUGMENTED NETWORK: +%d engagement edges, +%d HCoI edges (%s), +%d SOCIAL edges, %d existing ties socially reinforced'%(neweng,newhc,'dominance: isolated or not' if MEDIA_ADMIT=='dominance' else 'isolated-incident only',newsoc,reinf)+(', +%d CO-APPEARANCE edges'%newcoap if HOL else '')+' [MEDIA_ADMIT=%s]'%MEDIA_ADMIT)
print('  D4: %d/%d formerly-isolated members are now cascade-reachable: %s%s'%(
    len(reconnected),len(isolated),', '.join(sorted(reconnected)[:8]),'...' if len(reconnected)>8 else ''))
if CFG=='cu':
    print('wrote %s  -> ALL loaders pick it up by mtime; regenerate pipeline + audit_full to validate'%os.path.basename(out))
else:
    print('wrote %s  -> served ONLY under NET_CONFIG=holistic (production loaders cannot see this family); scope labels in the manifest beside it'%os.path.basename(out))
