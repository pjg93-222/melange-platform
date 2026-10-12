# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT,DRAFT  # centralized roots
"""MEDIA BLEND (user-directed 2026-07-02): the press pillar becomes MEDIA = 50% press releases +
50% social media, at the pillar's existing weight.  Insertion point = 'Press Release - Raw' (Table10):
its LET-normalized sheet and the Rank & Allocation SUMPRODUCT consume whatever sits there, so writing
media_raw = 0.5*press_component + 0.5*social_component updates the ENTIRE workbook chain (normalized
sheet -> pillar -> Blended Model rank) with zero formula edits.  Components are each scaled by their
own per-category p80 before mixing, so 50/50 means 50/50 regardless of raw units; the workbook's
pos/neg-max normalization then reapplies downstream exactly as before.

SOCIAL DECAY (user spec: "similar to press, more aggressive in frequency, not decline"): the press
recency curve fits a continuous exponential with HALF-LIFE ~2 YEARS (measured: w(1yr)=0.70,
w(2yr)=0.50, w(3yr)=0.35).  Social uses the SAME exponential shape on a 4x faster clock ->
HALF-LIFE 0.5 YEARS (env SOCIAL_HL_YEARS to re-dial).

Safety: timestamped backup copy of the workbook is written FIRST; only raw VALUE cells are touched
(all formulas preserved); revert = restore the backup."""
import sys, os, json, re, shutil, datetime, collections, hashlib
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
import pandas as pd, openpyxl
import stance.media_rules as MR
import config_overlay as CO
from moneyball_core import save_workbook_atomic
from members.member_identity import Resolver
M=MB_ROOT
WBP=M+r'\Moneyball Main Version - Blended.xlsx'
_roster=json.load(open(M+r'\data\engine\congestion.json',encoding='utf-8'))
_resolver=Resolver([x['member'] for x in _roster],{x['member']:x.get('bioguide') for x in _roster})
HL=float(os.environ.get('SOCIAL_HL_YEARS','0.5')); PRESS_W=0.5; SOCIAL_W=0.5
# DEEP-AUDIT DIALS — USER-RATIFIED 2026-07-02 (both now ON by default; env-overridable):
#  SOCIAL_SHRINK=1 (default) -> in categories where <15 members carry social signal, the social share
#                      scales by nz/15 and press absorbs the remainder. Kills the
#                      one-tweet-=-eight-press-releases artifact in the 6 thin categories (finding A2).
#  RT_W (default 0.5) -> retweets count at half the stance weight of an original post (finding B2:
#                      an RT is amplification, not authorship; hostile-quote-RT damage halved).
SHRINK=os.environ.get('SOCIAL_SHRINK','1')=='1'; RT_W=float(os.environ.get('RT_W','0.5'))
# CROSSPOST_DEDUP (D4, 2026-09-16, packet audits/members_section_20260914 §D4.b) -- THIS is the site
# that reaches the ranking engine.  The dedup below used to `continue` past an exact cross-post, so
# the row contributed 0 to soc[member][cat] and therefore 0 to the media blend written into
# 'Press Release - Raw' (the sheet the workbook's own SUMPRODUCT chain and press_matrix() both read).
# Default '0' -- a member who says the same thing on two platforms said it twice; the duplicate is
# counted, not dropped.  CROSSPOST_DEDUP=1 restores the drop byte for byte.  This is a SEPARATE
# implementation from the desk's (pandas here, openpyxl there, different upstream filter), so its
# ndupe is not the desk bundle's 1,028 and is reported on its own.
# Link-only posts (normalized text empty after URL stripping) carry no scorable text and are skipped
# under the dial -- the same explicit rule the desk states, not a side effect of an empty key.
DEDUP=os.environ.get('CROSSPOST_DEDUP','0')=='1'
TODAY=datetime.date.today()
# category keyword map -> the raw sheet's own headers (built from quorum_query.txt vocabulary groups)
CATKW=MR.RAW_PATTERNS
CATRX={k:re.compile(v,re.I) for k,v in CATKW.items()}
# keyword-cats == the RAW sheet's own header names (all 20 category columns E..X)
HDRMAP={k:k for k in CATKW}
SIGN={'support':1.0,'oppose':-1.0,'neutral':0.0}
def _sha256_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()
def p80(vals):
    a=sorted(abs(x) for x in vals if abs(x)>1e-9); return a[int(0.8*len(a))] if a else 1.0
# ---- social aggregates: sign * intensity(margin) * decay(0.5^(age/HL)), multi-cat split evenly ----
corp=pd.read_excel(M+r'\Social Master.xlsx',sheet_name='Corpus',dtype=str)
corp=corp[(corp['stance'].fillna('')!='')&(corp['verified_handle'].astype(str)=='1')]
# ---- STANCE STACK OVERRIDE (user-ratified 2026-07-04 "lets first wire this in"): per-tweet stance
# from tweet_stance_stack.py (0.855 acc / 0.819 F1 honest OOF vs the user's golds; old transfer
# model measured 0.481). Join by member+normalized-text (100% match on probe). Intensity becomes
# tier-weighted confidence (unanimous/majority/review); 'unrelated' maps to sign 0 (excluded) —
# ~900 noise posts stop polluting the blend. Unmatched rows keep their existing label. Absent file
# -> legacy behavior (neutral fallback discipline).
STACK={}
_sf=M+r'\data\stance\tweet_stance_final.csv'
if os.path.exists(_sf):
    import csv as _csv,io as _io
    def _nrm(t): return MR.normalized_text(t)
    # 3rd-pass S2 fix: distinct tweets can share a (member, normalized-text) key; last-write-wins made the
    # sign order-dependent (~46 keys). Collapse DETERMINISTICALLY by majority vote of stack_stance (tie->neutral),
    # keeping a representative row with the winning label at its highest tier.
    _grp=collections.defaultdict(list)
    for _r in _csv.DictReader(_io.open(_sf,encoding='utf-8-sig',newline='')):
        _grp[(_r['member'],_nrm(_r['text']))].append(_r)
    _TR={'unanimous':3,'majority':2,'review':1}
    for _k,_rows in _grp.items():
        if len(_rows)==1: STACK[_k]=_rows[0]; continue
        _top=collections.Counter(_x['stack_stance'] for _x in _rows).most_common()
        _lab=_top[0][0] if (len(_top)==1 or _top[0][1]>_top[1][1]) else 'neutral'   # genuine tie -> neutral
        _cands=[_x for _x in _rows if _x['stack_stance']==_lab] or _rows
        STACK[_k]=max(_cands,key=lambda _x:_TR.get(_x.get('tier',''),0))
    _TIERW={'unanimous':1.0,'majority':0.7,'review':0.35}
    print('stance-stack override ACTIVE: %d labeled tweets loaded'%len(STACK))
else:
    print('!! tweet_stance_final.csv missing — legacy per-tweet stances in force')
soc=collections.defaultdict(lambda:collections.defaultdict(float)); nassigned=0
nover=0; ndupe=0; nunknown=0; nlinkonly=0; _seen=set()
for _,r in corp.iterrows():
    _st=r['stance']; _inten_override=None
    if STACK:
        _s=STACK.get((r['member'],_nrm(r['text'])))
        if _s:
            _st=_s['stack_stance']; _inten_override=_TIERW.get(_s['tier'],0.5); nover+=1
    sg=SIGN.get(_st,0.0)
    if sg==0.0: continue
    _member=_resolver.resolve(r['member'])
    if not _member: nunknown+=1; continue
    # N2 (2026-09-19, packet audits/laundry_list_closeout_20260918 FINDINGS.md A3): the
    # duplicate key is the WHOLE normalised statement, not MR.normalized_text's first 120
    # characters. 445 of 1,434 groups (9,502-row scope) were FALSE collisions on a shared
    # prefix, so at CROSSPOST_DEDUP=1 those rows would be dropped as duplicates of posts
    # they are not. MR.normalized_text stays the tweet_stance_final join key at _nrm above,
    # untouched. CROSSPOST_KEY_CHARS=120 restores the old key; CROSSPOST_DEDUP is unchanged.
    _event=(_member,MR.crosspost_key(r['text']))
    _dupe=_event in _seen
    if _dupe: ndupe+=1
    _seen.add(_event)                      # a no-op when already present: same set either way
    if DEDUP:
        if _dupe: continue
    elif not _event[1]: nlinkonly+=1; continue
    try: age=(TODAY-datetime.date.fromisoformat(str(r['date'])[:10])).days/365.25
    except Exception: continue
    if _inten_override is not None:
        inten=_inten_override
    else:
        try: inten=max(0.25,min(1.5,float(r['stance_margin'])))
        except (TypeError,ValueError): inten=0.5
    if str(r.get('is_rt','')).strip().lower() in ('1','true','yes'): inten*=RT_W
    decay=0.5**(max(0.0,age)/HL)
    hits=MR.route_raw_categories(r['text'] or '')
    if not hits: continue
    nassigned+=1
    for c in hits: soc[_member][c]+=sg*inten*decay/len(hits)
print('social: %d source rows -> %d scored/category-assigned | %d exact cross-post duplicates %s | %d link-only posts skipped | %d unresolved identities excluded | %d members | half-life %.2f yr (press: 2.0 yr)'%(len(corp),nassigned,ndupe,'removed (CROSSPOST_DEDUP=1)' if DEDUP else 'retained',nlinkonly,nunknown,len(soc),HL))
# ---- CU FLOOR-SPEECH CHANNEL (VALIDATED; default ON 2026-07-16) -------------------------------------
# A THIRD own-source channel: speech_stance_final.csv (speech_stance_stack.py) — the member's own CREC
# floor words on CU/CFPB/interchange topics.  SIGN{support:+1,oppose:-1,neutral:0}, category-split,
# decay 0.5^(age/2.0yr) like press, tier-weighted intensity (gold 1.0 / review 0.35 — the API-free
# heuristic is a WEAK prior until Paul's gold lands), own-source weight 1.0 (floor speeches are
# member-authored, attribution-clean like a .gov press release).  It rides the press side of the 50/50
# mix, each channel p80-scaled for commensurability.
# CU_SPEECH_CHANNEL=0 remains an emergency rollback; validated production default is ON.
SPEECH_ON=os.environ.get('CU_SPEECH_CHANNEL','1')=='1'
def _cu_stale(_final,_deps):
    """GATE-SAFETY (audit 2026-07-15): a channel's stance_final must be NEWER than its corpus+gold inputs.
    Returns the list of newer inputs (=> stale, skip the channel fail-closed) or [] when fresh/overridden
    (CU_STALE_OK=1). This also protects direct or partial blend runs that bypass the channel scorers."""
    if os.environ.get('CU_STALE_OK','0')=='1': return []
    try: _fm=os.path.getmtime(_final)
    except OSError: return []
    return [os.path.basename(_d) for _d in _deps if os.path.exists(_d) and os.path.getmtime(_d)>_fm]
spc=collections.defaultdict(lambda:collections.defaultdict(float))
_sn=0; _snm=set(); _speech_loaded=False
if SPEECH_ON:
    import csv as _csv2,io as _io2
    _spf=M+r'\speech_stance_final.csv'
    _stl=_cu_stale(_spf,[M+'\\speeches_cu.csv',M+'\\data\\stance\\speech_gold_queue.csv'])
    if _stl:
        print('!! CU speech channel: speech_stance_final.csv is OLDER than %s — STALE, channel SKIPPED (re-run speech_stance_stack.py; CU_STALE_OK=1 overrides)'%'+'.join(_stl))
    elif os.path.exists(_spf):
        _speech_loaded=True
        _prows=list(_csv2.DictReader(_io2.open(_spf,encoding='utf-8-sig',newline='')))
        # C per-event normalization (rank-integrity audit 2026-07-17, refuter-confirmed HIGH): the
        # hearing + interview blocks gained this on 2026-07-15 but the speech block was missed —
        # passage-level rows let one EVENT (member,date) multiply its own weight (Ogles ~6x).
        # Weight each row 1/n so one event contributes one unit, the EXACT sibling-block rule.
        _ev=collections.Counter((_x.get('member'),(_x.get('date') or '')[:10]) for _x in _prows)
        for _r in _prows:
            _sg=SIGN.get((_r.get('stance') or '').strip().lower(),0.0)
            if _sg==0.0: continue                                  # neutral / unrelated -> no signal
            _m=_resolver.resolve(_r.get('member'),_r.get('bioguide'))
            if not _m: continue                                    # fail-closed on unresolved identity
            try: _age=(TODAY-datetime.date.fromisoformat((_r.get('date') or '')[:10])).days/365.25
            except Exception: continue
            _inten=MR.own_source_tier_weight(_r.get('tier'))
            if _inten==0.0: continue                               # unknown tier -> fail closed
            _dec=0.5**(max(0.0,_age)/2.0)                          # press-like 2.0yr half-life
            _cats=[c.strip() for c in (_r.get('categories') or '').split(';') if c.strip()]
            if not _cats: continue
            _sn+=1; _snm.add(_m)
            _evn=max(1,_ev[(_r.get('member'),(_r.get('date') or '')[:10])])
            for _c in _cats: spc[_m][_c]+=_sg*_inten*_dec/len(_cats)/_evn
        print('CU speech channel ACTIVE (CU_SPEECH_CHANNEL=1): %d signed floor speeches -> %d members (own-source weight 1.0, half-life 2.0yr)'%(_sn,len(_snm)))
    else:
        print('!! CU_SPEECH_CHANNEL=1 but speech_stance_final.csv missing — no speech signal added')
# ---- CU HEARING CHANNEL (VALIDATED GOLD-ONLY; default ON 2026-07-16) --------------------------------
# Own-words committee-hearing (source='CHRG') CU stance from hearing_stance_stack.py — an EXACT structural
# mirror of the CU floor-speech block above: SIGN{support:+1,oppose:-1,neutral:0}, category-split, decay
# 0.5^(age/2.0yr) like press, tier-weighted intensity (gold 1.0 / review 0.35).  Own-source weight 0.85
# (below floor speeches' 1.0: CHRG carries a witness/member attribution risk — a witness surname can
# collide with a member's — so the channel is discounted).  Rides the press side, p80-scaled for parity.
# CU_HEARING_CHANNEL=0 remains an emergency rollback; validated production default is ON.
HEARING_ON=os.environ.get('CU_HEARING_CHANNEL','1')=='1'
HEARING_W=0.85
hrc=collections.defaultdict(lambda:collections.defaultdict(float))
_hn=0; _hnm=set(); _hreview=0; _hearing_loaded=False
if HEARING_ON:
    import csv as _csvh,io as _ioh
    _hf=M+r'\data\stance\hearing_stance_final.csv'
    _stl=_cu_stale(_hf,[M+'\\data\\stance\\hearings_cu.csv',M+'\\data\\stance\\hearing_gold_queue.csv'])
    if _stl:
        print('!! CU hearing channel: hearing_stance_final.csv is OLDER than %s — STALE, channel SKIPPED (re-run hearing_stance_stack.py; CU_STALE_OK=1 overrides)'%'+'.join(_stl))
    elif os.path.exists(_hf):
        _hearing_loaded=True
        _prows=list(_csvh.DictReader(_ioh.open(_hf,encoding='utf-8-sig',newline='')))
        _eligible=[_r for _r in _prows if MR.hearing_tier_weight(_r.get('tier'))>0.0]
        _hreview=len(_prows)-len(_eligible)
        # C per-event normalization (Paul-delegated audit fix 2026-07-15): the hearing channel is
        # PASSAGE-level — one appearance can yield several CU passages. Weight each row 1/n so one
        # EVENT (member,date) contributes one unit of signal regardless of passage count.
        _ev=collections.Counter((_x.get('member'),(_x.get('date') or '')[:10]) for _x in _eligible)
        for _r in _eligible:
            _sg=SIGN.get((_r.get('stance') or '').strip().lower(),0.0)
            if _sg==0.0: continue                                  # neutral / unrelated -> no signal
            _m=_resolver.resolve(_r.get('member'),_r.get('bioguide'))
            if not _m: continue                                    # fail-closed on unresolved identity
            try: _age=(TODAY-datetime.date.fromisoformat((_r.get('date') or '')[:10])).days/365.25
            except Exception: continue
            _inten=MR.hearing_tier_weight(_r.get('tier'))           # gold=1.0; every other tier=0.0
            _dec=0.5**(max(0.0,_age)/2.0)                          # press-like 2.0yr half-life
            _cats=[c.strip() for c in (_r.get('categories') or '').split(';') if c.strip()]
            if not _cats: continue
            _hn+=1; _hnm.add(_m)
            _evn=max(1,_ev[(_r.get('member'),(_r.get('date') or '')[:10])])
            for _c in _cats: hrc[_m][_c]+=_sg*_inten*_dec/len(_cats)/_evn
        print('CU hearing channel ACTIVE, GOLD ONLY (CU_HEARING_CHANNEL=1): %d signed gold rows -> '
              '%d members | %d non-gold rows excluded (own-source weight 0.85, half-life 2.0yr)'%
              (_hn,len(_hnm),_hreview))
    else:
        print('!! CU_HEARING_CHANNEL=1 but hearing_stance_final.csv missing — no hearing signal added')
# ---- CU INTERVIEW CHANNEL (VALIDATED; default ON 2026-07-16) -----------------------------------------
# Own-words broadcast/podcast interview (source='INTERVIEW') CU stance from interview_stance_stack.py —
# EXACT structural mirror of the speech block.  Own-source weight 1.0 (interviews are the member's own
# words, attribution-clean like floor speeches).  SIGN / category-split / decay / tier-intensity identical.
# CU_INTERVIEW_CHANNEL=0 remains an emergency rollback; validated production default is ON.
INTERVIEW_ON=os.environ.get('CU_INTERVIEW_CHANNEL','1')=='1'
INTERVIEW_W=1.0
ivc=collections.defaultdict(lambda:collections.defaultdict(float))
_in=0; _inm=set(); _interview_loaded=False
if INTERVIEW_ON:
    import csv as _csvi,io as _ioi
    _if=M+r'\data\stance\interview_stance_final.csv'
    _stl=_cu_stale(_if,[M+'\\data\\stance\\interviews_cu.csv',M+'\\data\\stance\\interview_gold_queue.csv'])
    if _stl:
        print('!! CU interview channel: interview_stance_final.csv is OLDER than %s — STALE, channel SKIPPED (re-run interview_stance_stack.py; CU_STALE_OK=1 overrides)'%'+'.join(_stl))
    elif os.path.exists(_if):
        _interview_loaded=True
        _prows=list(_csvi.DictReader(_ioi.open(_if,encoding='utf-8-sig',newline='')))
        # C per-event normalization (Paul-delegated audit fix 2026-07-15): the interview channel is
        # PASSAGE-level — one appearance can yield several CU passages. Weight each row 1/n so one
        # EVENT (member,date) contributes one unit of signal regardless of passage count.
        _ev=collections.Counter((_x.get('member'),(_x.get('date') or '')[:10]) for _x in _prows)
        for _r in _prows:
            _sg=SIGN.get((_r.get('stance') or '').strip().lower(),0.0)
            if _sg==0.0: continue                                  # neutral / unrelated -> no signal
            _m=_resolver.resolve(_r.get('member'),_r.get('bioguide'))
            if not _m: continue                                    # fail-closed on unresolved identity
            try: _age=(TODAY-datetime.date.fromisoformat((_r.get('date') or '')[:10])).days/365.25
            except Exception: continue
            _inten=MR.own_source_tier_weight(_r.get('tier'))
            if _inten==0.0: continue                               # unknown tier -> fail closed
            _dec=0.5**(max(0.0,_age)/2.0)                          # press-like 2.0yr half-life
            _cats=[c.strip() for c in (_r.get('categories') or '').split(';') if c.strip()]
            if not _cats: continue
            _in+=1; _inm.add(_m)
            _evn=max(1,_ev[(_r.get('member'),(_r.get('date') or '')[:10])])
            for _c in _cats: ivc[_m][_c]+=_sg*_inten*_dec/len(_cats)/_evn
        print('CU interview channel ACTIVE (CU_INTERVIEW_CHANNEL=1): %d signed interviews -> %d members (own-source weight 1.0, half-life 2.0yr)'%(_in,len(_inm)))
    else:
        print('!! CU_INTERVIEW_CHANNEL=1 but interview_stance_final.csv missing — no interview signal added')
# ---- CU BLUESKY CHANNEL (GATED, default OFF) — DOUBLE-COUNT QUARANTINE (integrate 2026-07-15) ---------
# Structural sibling of the hearing/interview own-source blocks, but DELIBERATELY NOT WIRED into the blend
# math.  Verified at integrate time: Social Master.xlsx 'Corpus' (the SOCIAL side, variable `corp` above)
# ALREADY ingests Bluesky posts — 74 rows (source='direct', platform='bluesky'); 35 pass the
# stance!='' & verified_handle=='1' filter and are already scored into `soc` today.  bluesky_cu.csv
# (16 rows / 10 members) is a SUBSET of those (16/16 text overlap, all 10 members present), so adding
# bluesky_stance_final.csv as a second own-source injection would DOUBLE-COUNT the same member posts
# (once on the social side via `soc`, once on the press side here).  Per the integrate directive we expose
# the gate for parity/future use but REFUSE to inject; wiring first requires de-duplicating Bluesky OUT of
# the social corpus.  GATE: CU_BLUESKY_CHANNEL (default 0) — setting it to 1 logs the refusal, adds nothing.
BLUESKY_ON=os.environ.get('CU_BLUESKY_CHANNEL','0')=='1'
BLUESKY_W=0.7   # own-source weight that WOULD apply once the double-count is resolved (documented, not applied)
blc=collections.defaultdict(lambda:collections.defaultdict(float))   # intentionally EMPTY — never injected (see above)
if BLUESKY_ON:
    print('!! CU_BLUESKY_CHANNEL=1 IGNORED: Bluesky already rides the SOCIAL corpus (Social Master.xlsx: '
          '74 posts, 35 scored) and bluesky_cu.csv is a subset of it — injecting bluesky_stance_final.csv '
          'would double-count. De-duplicate Bluesky out of the social corpus before wiring this channel.')
# ---- workbook surgery ----
os.makedirs(M+r'\backups',exist_ok=True)
bak=M+r'\backups\Moneyball Main Version - Blended.BACKUP-%s.xlsx'%datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
shutil.copy2(WBP,bak); print('backup:','backups/'+os.path.basename(bak))
wb=openpyxl.load_workbook(WBP)             # formulas preserved
rw=wb['Press Release - Raw']
hdr={rw.cell(1,c).value:c for c in range(1,30) if rw.cell(1,c).value}
members=[rw.cell(r,1).value for r in range(2,536) if rw.cell(r,1).value]
mrow={rw.cell(r,1).value:r for r in range(2,536) if rw.cell(r,1).value}
# IDEMPOTENCE: press components never come from the live sheet (it holds the blend after run 1).
# 2026-07-04 INTEGRITY FIX (user-ratified "fix it"): the frozen 07-02 archive meant NO press article
# collected after 2026-07-02 could ever reach the workbook (197 members, frozen forever). The press
# component is now DYNAMIC — aggregated from the CURRENT cu_press_stance_tone.csv with the same
# shape as the social side: sign x intensity(press_value) x decay(0.5^(age/2.0yr press half-life)),
# category-split. R's exact archive units were not reproducible (best r=0.38; hidden steps), and
# don't need to be: the blend p80-normalizes press and social, so relative structure is what matters.
# PRESS_DYNAMIC=0 reverts to the frozen archive.
ORIG=json.load(open(M+r'\data\stance\press_raw_original.json',encoding='utf-8'))
if os.environ.get('PRESS_DYNAMIC','1')!='0':
    import csv as _csv,io as _io
    from press_stream import (canonical_url as _canonical_press_url,
                              load_fulltext as _load_press_fulltext,
                              load_tone_rows as _load_press_tone_rows,
                              source_paths as _press_source_paths)
    _tone_sources=_press_source_paths()
    # A5 (2026-09-19, packet audits/laundry_list_closeout_20260918 FINDINGS.md A5):
    # load_tone_rows now collapses one release ingested under two URL forms
    # (PRESS_URL_CANON, default 2). The collapsed rows are NOT lost from the accounting:
    # the ledger names each one, so rows_read still closes to rows_returned +
    # official_mention + no_url + exact_url_turn + duplicate_url_form.
    _tone_ingest={}
    _tone_rows=_load_press_tone_rows(ledger=_tone_ingest)
    _dyn=collections.defaultdict(lambda:collections.defaultdict(float))
    _n=0; _nm=set(); _pvs=[]
    _domain_audit=[]
    _api_target={}
    _api_path=M+r'\data\stance\api_press_stance.csv'
    if os.path.exists(_api_path):
        for _r in _csv.DictReader(_io.open(_api_path,encoding='utf-8-sig',newline='')):
            _api_target[(_r.get('url') or '').strip().rstrip('/')]=(_r.get('api_target') or '').strip()
    _fulltext=_load_press_fulltext()
    # STACK OVERRIDE (user-ratified 2026-07-04 "wire in the most accurate stack"): press_stance_final.csv
    # (press_stance_stack.py, 760-gold meta: 0.837 all-gold frame / 0.765 adversarial frame; DEBATE alone
    # 0.796/0.571). Stack label replaces DEBATE sign; UNRELATED -> 0 (kills ACU trade-digest mention
    # noise, the Warren #103 driver); tier-weighted trust (measured: unan .955 / maj .776 / rev .638).
    # Missing sidecar or unmatched url -> DEBATE fallback (legacy behavior, never silently empty).
    _stk={}
    _sfp=M+r'\press_stance_final.csv'
    if os.path.exists(_sfp):
        for _r in _csv.DictReader(_io.open(_sfp,encoding='utf-8-sig',newline='')):
            _sk=(_canonical_press_url(_r.get('url'),keep_fragment=True),
                 (_r.get('bioguide') or _r.get('member') or '').strip().upper())
            _stk[_sk]=((_r.get('stack_label') or '').strip().lower(),(_r.get('tier') or '').strip().lower())
    _pov=CO.merged_rows('data/stance/press_stance_overrides.json','corrections',
                        ('url','title_contains'),data_root=M)
    def _press_gold(_url,_title):
        _ul=(_url or '').strip().rstrip('/'); _tl=(_title or '').lower()
        for _g in _pov:
            _st=(_g.get('stance') or '').strip().lower()
            if _st not in SIGN: continue
            if _g.get('url') and (_g.get('url') or '').strip().rstrip('/')==_ul: return _st
            if _g.get('title_contains') and (_g.get('title_contains') or '').lower() in _tl: return _st
        return None
    _TW={'unanimous':1.0,'majority':0.7,'review':0.35}
    # ATTRIBUTION-STRENGTH channel weight. HISTORY: set to 0.5 on 2026-07-04 (user "that is such a
    # regression" — Warren pulled to #83 by trade-press tagging) to discount trade/third-party
    # coverage that merely tags a member, versus a member's OWN .gov release. His golds label trade
    # coverage support (41/65 third-party = support) — the LABELS always stood; only the CREDIT was cut.
    # REVERSED 2026-07-19 (user: "That coverage should be counted the same, with the decay"): trade
    # coverage now carries the SAME weight as an own release. Rationale on the record: the mention-noise
    # the 0.5 was aimed at is now killed upstream by the stack's UNRELATED->0 rule (see STACK OVERRIDE
    # above), so the flat penalty had become a second, blunter charge for the same problem; recency is
    # still handled by the 2-year half-life _dec below, which applies to every article regardless of
    # source. PRESS_THIRD_W=0.5 restores the 2026-07-04 discount.
    _W3=float(os.environ.get('PRESS_THIRD_W','1.0'))
    _n_stack=0; _n_fallback=0; _n_unrel=0; _n_own=0; _n_third=0; _n_gold=0
    def _pdate(s):
        for _f in ('%m/%d/%Y','%Y-%m-%d','%m/%d/%y'):
            try: return datetime.datetime.strptime((s or '').strip(),_f).date()
            except ValueError: pass
        return None
    for _r in _tone_rows:
        _uk=_canonical_press_url(_r.get('url'),keep_fragment=True)
        _turn=(_uk,(_r.get('bioguide') or _r.get('member') or '').strip().upper())
        _declared=[c.strip() for c in (_r.get('categories') or '').split(';') if c.strip()]
        _route=MR.press_routing_decision(_api_target.get(_uk,''),_r.get('title') or '',
                                         _declared,_fulltext.get(_uk,''))
        _cats=[c for c in _route['categories'] if c in ORIG]
        _audit={'url':_r.get('url'),'member_source':_r.get('member'),'title':_r.get('title'),
                'api_target':_api_target.get(_uk,''),'declared_categories':_declared,
                'effective_categories':_cats,'routing_basis':_route['basis'],
                'discarded_declared':_route['discarded_declared'],'scoring_disposition':'pending'}
        _domain_audit.append(_audit)
        _m=_resolver.resolve(_r.get('member'),_r.get('bioguide'))
        _hit=_stk.get(_turn)
        if _hit is not None:
            _lab,_tier=_hit
            if _lab=='unrelated':
                _n_unrel+=1; _sg=0.0; _audit['scoring_disposition']='unrelated-zeroed'
            else:
                _sg=SIGN.get(_lab,0.0)*_TW.get(_tier,0.7)
                if _sg!=0.0: _n_stack+=1
        else:
            _sg=SIGN.get((_r.get('stance') or '').lower(),0.0)
            if _sg!=0.0: _n_fallback+=1
        _gold=_press_gold(_r.get('url'),_r.get('title'))
        if _gold is not None:
            _sg=SIGN[_gold]; _n_gold+=1
            _audit['gold_override']=_gold
        if not _m:
            _audit['scoring_disposition']='unresolved-member'; continue
        if _sg==0.0:
            _audit['scoring_disposition']='neutral-zeroed'; continue
        # intensity = |press_value| (column embeds sign; stance supplies sign here). NO floor:
        # the csv's units are tiny (~0.03 scale) and a 0.25 floor saturated 100% of articles,
        # erasing intensity into a pure count measure. Top clip 3.0 = outlier guard only.
        try: _pv=min(3.0,abs(float(_r.get('press_value'))))
        except (TypeError,ValueError): _pv=0.0
        if _pv==0.0:
            _audit['scoring_disposition']='zero-intensity'; continue
        _d=_pdate(_r.get('date'))
        if _d is not None: _dec=0.5**(max(0.0,(TODAY-_d).days/365.25)/2.0)
        else:
            try: _dec=max(0.0,min(1.0,float(_r.get('recency_weight') or 0.5)))
            except (TypeError,ValueError): _dec=0.5
        if not _cats:
            _audit['scoring_disposition']='no-policy-domain'; continue
        _ul=(_r.get('url') or '').lower()
        if '.house.gov' in _ul or '.senate.gov' in _ul: _cw=1.0; _n_own+=1
        else: _cw=_W3; _n_third+=1
        _audit['scoring_disposition']='used'
        _n+=1; _nm.add(_m); _pvs.append(_pv)
        for _c in _cats: _dyn[_c][_m]+=_sg*_pv*_dec*_cw/len(_cats)
    ORIG={c:dict(_dyn.get(c,{})) for c in ORIG}   # same shape/keys as the archive
    from moneyball_core import dump_atomic as _da
    _pvs.sort()
    _meta_inputs=list(_tone_sources)+[_sfp,M+r'\data\stance\interview_stance_final.csv',
        M+r'\data\stance\interview_stance_final.receipt.json',
        M+r'\data\stance\press_refresh_feed_receipt.json']
    _da({'built_from_csv_mtime':max((os.path.getmtime(_p) for _p in _tone_sources),default=None),
         'tone_sources':_tone_sources,'tone_rows':len(_tone_rows),
         'tone_ingest_dedup':_tone_ingest,
         'inputs_sha256':{os.path.relpath(_p,M).replace('\\','/'):_sha256_file(_p)
                          for _p in _meta_inputs if os.path.exists(_p)},
         'articles_used':_n,'members_nonzero':len(_nm),
         'pv_stats':{'min':_pvs[0] if _pvs else 0.0,'median':_pvs[len(_pvs)//2] if _pvs else 0.0,
                     'max':_pvs[-1] if _pvs else 0.0,'n_distinct':len(set(_pvs))},
         'stack':{'sidecar':os.path.exists(_sfp),'stack_signed':_n_stack,'debate_fallback':_n_fallback,
                  'unrelated_zeroed':_n_unrel,'gold_overrides':_n_gold},
         'channel_weight':{'third_w':_W3,'own_domain':_n_own,'third_party':_n_third},
         'own_source_channels':{
             'speech':{'enabled':SPEECH_ON,'loaded':_speech_loaded,'signed_rows':_sn,
                       'members':len(_snm),'tier_policy':'gold 1.0 / unanimous 0.9 / majority 0.6 / review 0.35'},
             'hearing':{'enabled':HEARING_ON,'loaded':_hearing_loaded,'signed_gold_rows':_hn,
                        'members':len(_hnm),'non_gold_rows_excluded':_hreview,'tier_policy':'gold only'},
             'interview':{'enabled':INTERVIEW_ON,'loaded':_interview_loaded,'signed_rows':_in,
                          'members':len(_inm),'tier_policy':'gold 1.0 / unanimous 0.9 / majority 0.6 / review 0.35'},
             'bluesky':{'enabled':False,'loaded':False,'tier_policy':'existing social event only; no press-side duplicate'}},
         'routing':{'schema':'press-domain-routing-v1','rows':len(_domain_audit),
                    'basis':dict(collections.Counter(x['routing_basis'] for x in _domain_audit)),
                    'dispositions':dict(collections.Counter(x['scoring_disposition'] for x in _domain_audit))},
         'note':'dynamic press component (PRESS_DYNAMIC); archive fallback with PRESS_DYNAMIC=0'},
        M+r'\data\stance\press_component_meta.json')
    _da({'schema':'press-domain-routing-audit-v1','sources':_tone_sources,'rows':_domain_audit,
         'summary':{'basis':dict(collections.Counter(x['routing_basis'] for x in _domain_audit)),
                    'dispositions':dict(collections.Counter(x['scoring_disposition'] for x in _domain_audit)),
                    'effective_categories':dict(collections.Counter(
                        c for x in _domain_audit for c in x['effective_categories']))}},
        M+r'\data\stance\press_domain_routing_audit.json')
    print('DYNAMIC press component: %d signed articles -> %d members (stack-signed %d, DEBATE fallback %d, gold overrides %d, unrelated zeroed %d; own-domain %d @1.0, third-party %d @%.2f)'%(_n,len(_nm),_n_stack,_n_fallback,_n_gold,_n_unrel,_n_own,_n_third,_W3))
changed=0
for kwcat,hname in HDRMAP.items():
    col=hdr.get(hname)
    if not col: print('  !! header not found:',hname); continue
    pressvals={m:ORIG.get(hname,{}).get(m,0.0) for m in members}
    socvals={m:soc.get(m,{}).get(kwcat,0.0) for m in members}
    sp=p80(pressvals.values()); ss=p80(socvals.values())
    nz=sum(1 for v in socvals.values() if abs(v)>1e-12)
    f=min(1.0,nz/15.0) if SHRINK else 1.0   # SOCIAL_SHRINK: thin-category social share scales with signal breadth
    # GATED speech channel: own p80 so it is commensurable with press before it rides the press side.
    speechvals={m:spc.get(m,{}).get(kwcat,0.0) for m in members} if SPEECH_ON else None
    ssp=p80(speechvals.values()) if SPEECH_ON else 0.0
    hearingvals={m:hrc.get(m,{}).get(kwcat,0.0) for m in members} if HEARING_ON else None
    hsp=p80(hearingvals.values()) if HEARING_ON else 0.0
    interviewvals={m:ivc.get(m,{}).get(kwcat,0.0) for m in members} if INTERVIEW_ON else None
    isp=p80(interviewvals.values()) if INTERVIEW_ON else 0.0
    for m in members:
        pv=pressvals[m]/sp if sp else 0.0; sv=socvals[m]/ss if ss else 0.0
        if SPEECH_ON and ssp:
            pv=pv+(speechvals[m]/ssp)   # own-source floor speeches ride the press side at weight 1.0
        if HEARING_ON and hsp:
            pv=pv+HEARING_W*(hearingvals[m]/hsp)   # own-source hearing questioning rides the press side at weight 0.85
        if INTERVIEW_ON and isp:
            pv=pv+INTERVIEW_W*(interviewvals[m]/isp)   # own-source interviews ride the press side at weight 1.0
        nv=(1.0-SOCIAL_W*f)*pv+SOCIAL_W*f*sv
        old=rw.cell(mrow[m],col).value or 0.0
        nv_rounded=round(nv,6)
        rw.cell(mrow[m],col).value=nv_rounded
        if abs(nv_rounded-old)>1e-9: changed+=1
rw['Z1']='MEDIA BLEND %s: cells = press(p80-scaled)+social(p80-scaled, half-life %.2fy) at 50/50 (thin-cat shrink %s, RT weight %.2f). Backup: %s'%(TODAY,HL,'ON' if SHRINK else 'off',RT_W,os.path.basename(bak))
if _speech_loaded:
    rw['Z1']=str(rw['Z1'].value or '')+' | +CU floor-speech learned stack (gold 1.0, unanimous 0.9, majority 0.6, review 0.35)'
if _hearing_loaded:
    rw['Z1']=str(rw['Z1'].value or '')+' | +CU hearing channel (GOLD ONLY; own-source 0.85, half-life 2.0y; non-gold excluded)'
if _interview_loaded:
    rw['Z1']=str(rw['Z1'].value or '')+' | +CU interview learned stack (gold 1.0, unanimous 0.9, majority 0.6, review 0.35)'
wb.calculation.fullCalcOnLoad=True   # PHASE-2: the workbook is a VIEW — Excel recalcs it on open; no COM recalc anywhere
save_workbook_atomic(wb,WBP)
print('wrote media blend into Press Release - Raw: %d cells changed (formulas untouched)'%changed)
