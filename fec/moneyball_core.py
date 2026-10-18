# -*- coding: utf-8 -*-
"""MONEYBALL SHARED KERNEL (architecture review 2026-07-02) — the single source for every block that
was previously copy-pasted across the engines and drifted (measured pre-refactor: network loader x17,
party loader x11, cpf x8 under TWO different physics, IMP x6, PERS x5, splitmix x4, receptivity x9;
three of the week's five code bugs were duplication drift: the CFPB block-merge missed in the
frontier, the BETA export break, the PERS_NONE comment/code contradiction).

Physics note — there are deliberately TWO partisan edge factors, now under distinct names:
  cpf_diffusion : cascade/contagion physics — cross-party edges dampened unless both ends are
                  bridge-builders (0.4+0.6*sqrt(bpf_a*bpf_b)); same-party 1.0.
  cpf_pressure  : direct-persuasion physics (Furnas/Bakker source-alignment) — co-partisan pressure
                  x1.7, out-partisan x0.5.
Loaders are lazy + cached; importing this module does NO workbook IO."""
import os,sys,json,csv,glob,collections,math
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from paths import ROOT,DRAFT,WORKBOOK,p
import bill_rules as BR
import stance.media_rules as MR
_c={}
def congestion():
    if 'C' not in _c: _c['C']={m['member']:m for m in json.load(open(p('data/engine/congestion.json')))}
    return _c['C']
def members(): return list(congestion())
def name2bio():
    if 'n2b' not in _c: _c['n2b']={m:v['bioguide'] for m,v in congestion().items() if v.get('bioguide')}
    return _c['n2b']
def bio2name():
    if 'b2n' not in _c: _c['b2n']={v:k for k,v in name2bio().items()}
    return _c['b2n']
def build_party_map(congress_paths):
    """Prefer each bioguide's first congress in input order; within it, Voteview's larger numeric
    ICPSR wins because 9 prefixes the post-switch identity. Equal numeric ties (arbitrary) or
    non-numeric values keep file order; receipts print only when party codes differ."""
    party={}
    for congN,f in congress_paths:
        if not os.path.exists(f): continue
        bybio={}
        with open(f,encoding='utf-8') as fh:
            for row in csv.DictReader(fh):
                b=row.get('bioguide_id')
                if b: bybio.setdefault(b,[]).append(row)
        for b,rows in bybio.items():
            chosen=rows[0]; rule='file order (non-numeric icpsr)'
            if len(rows)>1:
                party_codes=[row.get('party_code') for row in rows]
                try: icpsrs=[int(row.get('icpsr')) for row in rows]
                except (TypeError,ValueError): pass
                else:
                    if len(set(icpsrs))>1:
                        chosen=max(zip(icpsrs,rows),key=lambda item:item[0])[1]
                        rule='largest numeric icpsr'
                    else: rule='identical icpsr - file order, ARBITRARY'
                if len(set(party_codes))>1:
                    print('voteview party duplicate: congress=%s bioguide=%s icpsrs=%s party_codes=%s chosen_icpsr=%s chosen_party_code=%s rule=%s'%(
                        congN,b,[row.get('icpsr') for row in rows],party_codes,
                        chosen.get('icpsr'),chosen.get('party_code'),rule))
            if b not in party:
                pc=str(chosen.get('party_code'))
                party[b]={'100':'D','200':'R'}.get(pc,'I')
    return party
def party():
    """member name -> 'D'/'R'/'I'/'?'; first congress containing the bioguide in 119/118/117 wins.
    Within-congress duplicates use Voteview's larger numeric (9-prefixed post-switch) ICPSR."""
    if 'prt' not in _c:
        pb=build_party_map([(cn,p('votes','HS%d_members.csv'%cn)) for cn in (119,118,117)])
        _c['prt']={m:pb.get(v.get('bioguide',''),'?') for m,v in congestion().items()}
    return _c['prt']
def bpf():
    if 'bpf' not in _c:
        BP=json.load(open(p('bipartisan.json')))
        _c['bpf']={m:BP.get(m,24)/100.0 for m in congestion()}
    return _c['bpf']
def recept_layer_on(): return os.environ.get('RECEPT_LAYER','1')!='0'   # QME 2026-10-01: single read, also recorded in optimize_result.json dials
# COMPETITIVENESS MULTIPLIER c (COMPET_MULT; Paul 2026-10-05; DEFAULT ON since Paul 2026-10-06: unset or '' -> ON, '0' -> OFF, '1' -> ON, anything else RAISES;
# the OFF path reads NO file and returns exactly 1.0).
# c = 1 + AMPLITUDE*exp(-max(lean,0)/SCALE), range (1,2], 6 dp at the source (fec/engine/build_competitiveness.py);
# lean = Cook PVI points toward the member's own party. It scales EFFECTIVE dollars on BOTH money legs of the flip family
# (a: c*cu, b: c*ind; money_response.shift_lean r=c*amount/receipts; lt_priming money_units *c, dollars_for_units /c) -- the
# per-member $10k cap and the pot stay REAL dollars. AMPLITUDE/SCALE are Paul's JUDGMENT constants: uncalibrated, and the
# reciprocity they encode is NOT validated. COMPET_SRC overrides the artifact path (default data/money/competitiveness_c.json, written by the
# chain step build_competitiveness.py); with the dial ON and only a .candidate on disk and COMPET_SRC unset, cmult RAISES (a candidate is never consumed by accident).
# Paul rulings 2026-10-07 (brain D-107): R1 pressure_field()/rc alias below; R7 compet_check_inputs re-checks the input CSV hashes on load.
COMPET_AMPLITUDE=1.0; COMPET_SCALE=5.0
COMPET_SCHEMA='melange-competitiveness-c-v2'   # v2 (Paul rulings 2026-10-07, D-107): orientation overrides, ballot-district overrides, kernel-checked input hashes
# The inputs whose sha256 the artifact header records AND the loader re-checks (R7). build_competitiveness.py reads its paths from here.
COMPET_INPUTS=(('cook_house_csv','data/money/cook_pvi_2026/cook_pvi_2026_districts.csv'),
               ('cook_state_csv_supplied_by_paul','data/money/cook_pvi_2026/cook_pvi_states_supplied_20261005.csv'),
               ('ballot_district_overrides_csv','data/money/cook_pvi_2026/ballot_district_overrides.csv'))
COMPET_JOIN_METHODS=frozenset(('cook_incumbent_exact','cook_incumbent_fuzzy','district_number','state_table','territory_delegate','unmatched','ballot_district_override'))
def compet_c(lean):
    """The single copy of Paul's formula. lean None (unmatched / unoriented / territory) -> exactly 1.0."""
    if lean is None: return 1.0
    return round(1.0+COMPET_AMPLITUDE*math.exp(-max(lean,0.0)/COMPET_SCALE),6)
def compet_on():
    v=os.environ.get('COMPET_MULT','1').strip() or '1'   # unset or '' == ON (Paul 2026-10-06); '0' is the revert switch
    if v not in ('0','1'): raise ValueError("COMPET_MULT must be '0' or '1' (got %r)"%v)
    return v=='1'
def compet_roster_hash(cong=None):
    """sha256 over the sorted 'member|bioguide' lines of the 533-member roster (congestion.json)."""
    import hashlib
    rows=list(cong.values()) if isinstance(cong,dict) else (cong if cong is not None else list(congestion().values()))
    return hashlib.sha256('\n'.join(sorted('%s|%s'%(m['member'],m.get('bioguide') or '') for m in rows)).encode('utf-8')).hexdigest()
def compet_validate(doc,cong=None):
    """Fail-closed validation of a competitiveness artifact against the roster. Returns {member: c}; RAISES ValueError."""
    cong=cong if cong is not None else congestion()
    names=set(cong) if isinstance(cong,dict) else {m['member'] for m in cong}
    hd=doc.get('header') or {}; mem=doc.get('members')
    if hd.get('schema')!=COMPET_SCHEMA or not isinstance(mem,dict): raise ValueError('competitiveness artifact: bad schema/members')
    if hd.get('amplitude')!=COMPET_AMPLITUDE or hd.get('scale')!=COMPET_SCALE:
        raise ValueError('competitiveness artifact: header amplitude/scale %r/%r != kernel %r/%r'%(hd.get('amplitude'),hd.get('scale'),COMPET_AMPLITUDE,COMPET_SCALE))
    if hd.get('roster_sha256')!=compet_roster_hash(cong): raise ValueError('competitiveness artifact: roster hash does not match congestion.json (roster drift; rebuild)')
    if set(mem)!=names: raise ValueError('competitiveness artifact: member set != congestion roster (missing %s, extra %s)'%(sorted(names-set(mem))[:3],sorted(set(mem)-names)[:3]))
    ovh=hd.get('ballot_overrides'); orh=hd.get('orientation_overrides')
    if not isinstance(ovh,dict) or not isinstance(orh,dict): raise ValueError('competitiveness artifact: header lacks ballot_overrides/orientation_overrides (schema v2)')
    running=set(ovh.get('running_house') or []); declared=running|set(ovh.get('not_running_house') or []); seen=set()
    out={}
    for n,r in mem.items():
        c=r.get('c'); lean=r.get('lean')
        if isinstance(c,bool) or not isinstance(c,(int,float)) or not math.isfinite(c) or not (1.0<=c<=2.0): raise ValueError('competitiveness artifact: c out of [1,2] for %s: %r'%(n,c))
        if lean is not None and (isinstance(lean,bool) or not isinstance(lean,(int,float)) or not math.isfinite(lean)): raise ValueError('competitiveness artifact: non-numeric or non-finite lean for %s'%n)
        if not (abs(c-compet_c(lean))<=1e-9): raise ValueError('competitiveness artifact: c for %s (%r) does not recompute from lean %r (%r)'%(n,c,lean,compet_c(lean)))
        jm=r.get('join_method'); fl=r.get('flags') or []; b=r.get('bioguide'); op=r.get('oriented_party'); bo=r.get('ballot_override') or {}
        if bo and bo.get('pvi_basis') not in ('ballot_2026','current_seat_2025'): raise ValueError('competitiveness artifact: ballot override for %s has bad pvi_basis %r'%(n,bo.get('pvi_basis')))
        pv=r.get('pvi2025') if bo.get('pvi_basis')=='current_seat_2025' else r.get('pvi2026')   # D-107 R3/R4: a retiree's override carries his current-seat 2025 PVI
        if jm not in COMPET_JOIN_METHODS: raise ValueError('competitiveness artifact: unknown join_method for %s: %r'%(n,jm))
        if bo and jm!='ballot_district_override': raise ValueError('competitiveness artifact: %s carries a ballot_override but join_method %r'%(n,jm))
        if lean is not None and (op not in ('D','R') or isinstance(pv,bool) or not isinstance(pv,(int,float)) or lean!=pv*(1 if op=='R' else -1)):
            raise ValueError('competitiveness artifact: lean for %s (%r) does not follow its PVI %r and oriented party %r'%(n,lean,pv,op))
        if jm=='ballot_district_override':
            if r.get('chamber')!='House' or 'ballot_district_override' not in fl or b not in declared or not bo:
                raise ValueError('competitiveness artifact: ballot-district override row for %s is not declared in the header'%n)
            seen.add(b)
        if b in orh and (op!=(orh[b] or {}).get('party') or 'orientation_override' not in fl):
            raise ValueError('competitiveness artifact: orientation override for %s not applied'%n)
        out[n]=float(c)
    if seen!=declared: raise ValueError('competitiveness artifact: header ballot_overrides %s != members joined by override %s'%(sorted(declared-seen)[:3],sorted(seen-declared)[:3]))
    return out
def compet_check_inputs(doc):
    """R7 (Paul ruling 2026-10-07, D-107): the Cook House CSV, Paul's state CSV and the ballot-district override CSV must still be the bytes
    the artifact was built from; RAISES ValueError on a missing hash, a missing file or a mismatch (rerun build_competitiveness.py)."""
    import hashlib
    rec=((doc or {}).get('header') or {}).get('input_sha256') or {}
    for key,rel in COMPET_INPUTS:
        exp=rec.get(key)
        if not (isinstance(exp,str) and len(exp)==64): raise ValueError('competitiveness artifact: header input_sha256 lacks %s (rebuild with fec/engine/build_competitiveness.py)'%key)
        try:
            with open(p(*rel.split('/')),'rb') as fh: got=hashlib.sha256(fh.read()).hexdigest()
        except FileNotFoundError: raise ValueError('competitiveness artifact: input %s is missing (%s)'%(key,rel))
        if got!=exp: raise ValueError('competitiveness artifact: input %s changed since the build (artifact %s.., file %s..); rerun fec/engine/build_competitiveness.py'%(key,exp[:12],got[:12]))
def compet_artifact_path():
    src=os.environ.get('COMPET_SRC','').strip()
    if src: return src if os.path.isabs(src) else p(src)
    prod=p('data','money','competitiveness_c.json')
    if os.path.exists(prod): return prod
    if os.path.exists(prod+'.candidate'):
        raise RuntimeError('COMPET_MULT=1 but only %s.candidate exists: a candidate is never consumed implicitly; set COMPET_SRC to it explicitly or promote it'%os.path.basename(prod))
    raise FileNotFoundError('COMPET_MULT=1 but %s is absent (run fec/engine/build_competitiveness.py)'%prod)
def compet_state():
    if 'compet' not in _c:
        path=compet_artifact_path()
        with open(path,'rb') as fh: raw=fh.read()
        import hashlib
        doc=json.loads(raw.decode('utf-8'))
        vals=compet_validate(doc); compet_check_inputs(doc)
        _c['compet']=(vals,path,hashlib.sha256(raw).hexdigest())
    return _c['compet']
def cmult(n):
    """Per-member competitiveness multiplier: dial OFF -> exactly 1.0 and NO file is read; ON -> the validated artifact's c."""
    if not compet_on(): return 1.0
    return compet_state()[0][n]
def compet_record():
    """(dial, artifact sha256 or None) for engine result files that record their effective dials."""
    return ('1',compet_state()[2]) if compet_on() else ('0',None)
def write_engine_dials(artifact_path,sidecar_path=None):
    """R7 (Paul ruling 2026-10-07, D-107): <stem>.dials.json beside an engine artifact whose own schema cannot carry metadata
    (per_topic_alloc.json is {topic: {member: $}} and 8+ readers iterate its keys as topics). Written in BOTH dial positions; the
    artifact itself is never touched. Records the artifact's sha256 so a stale sidecar is detectable."""
    import hashlib
    sidecar_path=sidecar_path or os.path.splitext(artifact_path)[0]+'.dials.json'
    with open(artifact_path,'rb') as fh: sha=hashlib.sha256(fh.read()).hexdigest()
    dial,csha=compet_record()
    doc={'schema':'melange-engine-dials-v1','artifact':os.path.basename(artifact_path),'artifact_sha256':sha,
         'dials':{'COMPET_MULT':dial,'COMPET_SRC_SHA256':csha}}
    dump_atomic(doc,sidecar_path,indent=1,sort_keys=True)
    return doc
# R1 (Paul ruling 2026-10-07, brain D-107): with COMPET_MULT on, the Cook-PVI multiplier c carries seat competitiveness on the money legs, so the Fouirnaies-Hall
# competitiveness term 1+.30*(comp-0.5) is NEUTRAL in the engines' pressure_mult. receptivity_layer.py writes that product as 'pressure_mult_compet' (only when the
# dial is on) next to the unchanged 'pressure_mult'; rc(m,'pressure_mult') returns the field for the CURRENT dial position, so COMPET_MULT=0 reads exactly the pre-ruling value from any file.
PRESSURE_FIELD_COMPET='pressure_mult_compet'
def pressure_field():
    """The member_receptivity.json field that IS the engines' pressure_mult for the current COMPET_MULT position."""
    return PRESSURE_FIELD_COMPET if compet_on() else 'pressure_mult'
def incent_field():
    """The member_receptivity.json incentive field behind pressure_field() for the current COMPET_MULT position (research sweeps rebuild variants from it)."""
    return 'incent_mult_compet' if compet_on() else 'incent_mult'
def receptivity():
    if 'rec' not in _c:
        _c['rec_pkey']=pressure_field()
        try:
            _c['rec']=json.load(open(p('member_receptivity.json')))['members'] if recept_layer_on() else {}
        except FileNotFoundError: _c['rec']={}
    return _c['rec']
def rc(m,k,d=1.0):
    rec=receptivity()
    if k=='pressure_mult':
        k=_c.get('rec_pkey') or pressure_field()   # R1: follows the COMPET_MULT position (see pressure_field)
        if k!='pressure_mult' and _c.get('rec_checked') is not rec:
            # fail closed, checked once per loaded dict and only for the pressure field (edge_mult/seed_prob readers never need the compet field)
            _miss=sorted(n for n,v in rec.items() if isinstance(v.get(k),bool) or not isinstance(v.get(k),(int,float)))
            if _miss:
                raise RuntimeError('COMPET_MULT is on but member_receptivity.json lacks %s for %d members (e.g. %s): it predates Paul ruling '
                                   '2026-10-07 R1 or was built with COMPET_MULT=0; rerun fec/members/receptivity_layer.py'%(k,len(_miss),_miss[:3]))
            _c['rec_checked']=rec
    return rec.get(m,{}).get(k,d)
def press_matrix():
    """PHASE-2 (system-of-record inversion, user sign-off 2026-07-02): Python-owned press
    normalization. Reads 'Press Release - Raw' VALUES (always fresh — the blend writes literals)
    and the row-535 tiered weights (user-edited literals on the Normalized sheet), applies signed
    positive/negative-extrema normalization within each domain. The upstream p80 transform only
    makes press and social components commensurable; this distinct step makes domains comparable.
    No dependency on the sheet's cached formula results,
    hence no COM recalc needed anywhere in the pipeline.
    Returns (norm {col:{member:val}}, w535 {col:weight}, headers {col:name})."""
    if 'pressM' not in _c:
        import openpyxl
        wb=openpyxl.load_workbook(WORKBOOK,data_only=True)   # NOT read_only: streamed sheets make random .cell() O(sheet) per call (640s pathology) and misread openpyxl-saved files
        rw=wb['Press Release - Raw']; pn=wb['Press Release - Normalized']
        rrow={rw.cell(r,1).value:r for r in range(2,535) if rw.cell(r,1).value}
        COLS=list(range(5,24))
        ALL_COLS=COLS+[24]   # X = General Credit Union; audited below, never invented into a domain
        headers={c:rw.cell(1,c).value for c in ALL_COLS}
        raw_all={c:{m:(rw.cell(rrow[m],c).value or 0.0) for m in rrow} for c in ALL_COLS}
        norm={c:MR.normalize_signed_extrema(raw_all[c]) for c in COLS}
        _c['press_general_exclusions']=MR.general_exclusion_ledger(raw_all[24])
        _c['pressM']=(norm,{c:(pn.cell(535,c).value or 0.0) for c in COLS},
                      {c:rw.cell(1,c).value for c in COLS})
        wb.close()
    return _c['pressM']
def press_general_exclusions():
    """Per-member ledger of General-CU media retained for audit but excluded from policy scoring."""
    press_matrix()
    return _c.get('press_general_exclusions',{})
def pillar_scores():
    """The Python rank authority (ranks_python.json, written by compute_pillars --write in the
    pipeline). Loud failure if missing — engines must not run on a stale/absent authority."""
    if 'pillars' not in _c:
        f=p('ranks_python.json')
        if not os.path.exists(f):
            raise FileNotFoundError('ranks_python.json missing — run `python fec/engine/compute_pillars.py --write` '
                                    '(or the full pipeline); the workbook is a VIEW, not the score source.')
        _c['pillars']=json.load(open(f))
    return _c['pillars']
def scores(): return pillar_scores()['score']
def ranks():  return pillar_scores()['rank']

# The primacy board supplies the engine's discrete general-priority anchor.
# Direct issue evidence remains continuous and a small continuous orientation
# adjustment is added below, but the old composite rank score must not leak
# into allocation. Equal tier spacing avoids manufacturing within-tier rank
# precision.
PRIMACY_ENGINE_LEVELS = {0: 0.0, 1: 1.0, 2: 2.0 / 3.0, 3: 1.0 / 3.0}


def primacy_engine_value(tier):
    """Return the engine priority for one validated primacy tier."""
    if isinstance(tier, bool):
        raise ValueError('primacy engine tier must be 0, 1, 2, or 3')
    try:
        tier = int(tier)
    except (TypeError, ValueError):
        raise ValueError('primacy engine tier must be 0, 1, 2, or 3')
    if tier not in PRIMACY_ENGINE_LEVELS:
        raise ValueError('primacy engine tier must be 0, 1, 2, or 3')
    return PRIMACY_ENGINE_LEVELS[tier]


def primacy_engine_state(roster=None, *, mode=None, effectiveness_path=None,
                         shadow_root=None, root=None):
    """Return ``(tier, tier_rank, priority)`` for the engine roster.

    This is fail-closed because primacy is now a production engine input. The
    continuous official score/rank remains the reporting authority. This
    function returns only the tier anchor; ``blended_engine_priority`` adds
    orientation later without changing tier or rank. Topic engagement, issue
    movability, network, money, and receptivity remain separate model inputs.
    """
    roster = tuple(roster if roster is not None else members())
    cache = _c.setdefault('primacy_engine_state', {})
    candidate = any(value is not None for value in (mode,effectiveness_path,shadow_root,root))
    cache_key = (roster,mode,str(effectiveness_path),str(shadow_root),str(root)) if candidate else roster
    if cache_key not in cache:
        from tier_util import legislative_press_tiers
        tier, tier_rank = (legislative_press_tiers(list(roster), mode=mode,
                           effectiveness_path=effectiveness_path, shadow_root=shadow_root,
                           root=root) if candidate else legislative_press_tiers(list(roster)))
        missing = [
            member for member in roster
            if member not in tier or member not in tier_rank
        ]
        if missing:
            raise ValueError(
                'primacy engine tier roster is incomplete: %s' % missing[:10]
            )
        invalid = {
            member: value for member, value in tier.items()
            if value not in PRIMACY_ENGINE_LEVELS
        }
        if invalid:
            raise ValueError(
                'primacy engine tier values are invalid: %s'
                % list(invalid.items())[:10]
            )
        priority = {
            member: primacy_engine_value(tier[member])
            for member in roster
        }
        cache[cache_key] = (tier, tier_rank, priority)
    return cache[cache_key]


def dump_atomic(obj,path,**kw):
    """S2-03 (audit 2026-07-02): write JSON to a temp sibling then os.replace() — atomic on NTFS, so a
    crash/kill mid-write (Excel force-kill, OneDrive contention) can never leave a truncated artifact.
    Drop-in for json.dump(obj, open(path,'w'), **kw)."""
    import json as _j,tempfile as _t
    d=os.path.dirname(path) or '.'
    fd,tmp=_t.mkstemp(dir=d,suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8', newline='\r\n') as fh:
            _j.dump(obj,fh,**kw)
            fh.flush(); os.fsync(fh.fileno())  # data on disk before the rename (hard power-offs)
        os.replace(tmp,path)
    except BaseException:
        try: os.remove(tmp)
        except OSError: pass
        raise
def write_text_atomic(text,path,encoding='utf-8'):
    """Atomically replace a generated text/HTML artifact."""
    import tempfile as _t
    d=os.path.dirname(path) or '.'
    fd,tmp=_t.mkstemp(dir=d,suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding=encoding,newline='') as fh:
            fh.write(text)
            fh.flush(); os.fsync(fh.fileno())  # data on disk before the rename (hard power-offs)
        os.replace(tmp,path)
    except BaseException:
        try: os.remove(tmp)
        except OSError: pass
        raise
def json_for_script(obj,**kw):
    """Serialize JSON for an inline ``<script>`` without permitting an HTML end tag.

    JSON's Unicode escapes decode to the original values in JavaScript/JSON.parse, while escaping
    ``<`` prevents user/source text such as ``</script>`` from terminating the data element early.
    U+2028/U+2029 are escaped for older JavaScript parsers as well.
    """
    raw=json.dumps(obj,**kw)
    return (raw.replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e')
               .replace('\u2028','\\u2028').replace('\u2029','\\u2029'))
def save_workbook_atomic(workbook,path):
    """Save an openpyxl workbook to a verified sibling ZIP, then replace."""
    import tempfile as _t,zipfile as _z
    d=os.path.dirname(path) or '.'
    fd,tmp=_t.mkstemp(dir=d,suffix='.xlsx.tmp'); os.close(fd)
    try:
        workbook.save(tmp)
        with open(tmp,'rb+') as _fh: os.fsync(_fh.fileno())  # data on disk before verify + rename
        with _z.ZipFile(tmp,'r') as archive:
            bad=archive.testzip()
            if bad: raise IOError('workbook ZIP verification failed at '+bad)
        os.replace(tmp,path)
    except BaseException:
        try: os.remove(tmp)
        except OSError: pass
        raise
# CU scrape identity fix (decision G, 2026-10-11; audits/holistic_v1_eval/RECORD.md "CORRECTION OF
# RECORD"). The R scraper keys a SPONSOR by display name, so the 2026-06-25 base credits Sen. Robert
# Menendez's (M000639, departed, off-roster) CU bills S.2142/S.3128 to Rep. Robert Menendez
# (M001226): all 9 M001226 out-edges are unsupported by BILLSTATUS. In-edges are bioguide-keyed and
# correct. Keyed to the defective VINTAGE so a corrected re-scrape (whose real M001226 out-edges
# are legitimate) is never filtered. CU_IDENTITY_FIX=0 restores the misattributed edges exactly.
CU_MISATTRIBUTED_SPONSORS={'congress_cosponsor_network_20260625_205554.xlsx':frozenset({'M001226'})}
def cu_identity_fix_on():
    """CU_IDENTITY_FIX: unset/''/1 = ON, 0 = OFF, anything else RAISES. Also gates the
    legislative archive's BILLSTATUS identity check (build_archive_legislative.py)."""
    v=os.environ.get('CU_IDENTITY_FIX','1')
    if v not in ('0','1',''): raise ValueError('CU_IDENTITY_FIX must be 0|1, got %r'%v)
    return v!='0'
def cu_dropped_sponsors(path):
    """Bioguides whose OUT-edges are misattributed in this CU network file (empty when clean)."""
    return CU_MISATTRIBUTED_SPONSORS.get(os.path.basename(str(path)),frozenset()) if cu_identity_fix_on() else frozenset()
# CU placeholder-link backfill (2026-10-11). The 06-25 scrape took a glossary help link as the
# DetailLink of 172 archive rows and Stage 3 deduped by link, so 111 sponsored bills lost all their
# sponsor->cosponsor edges. fec/network/build_cu_placeholder_backfill.py rebuilds those ties from
# on-disk BILLSTATUS with Stage 3's weighting into a frozen sidecar for that vintage only.
# CU_PLACEHOLDER_BACKFILL=0 restores the file's edges exactly; other values RAISE; ON with the
# sidecar missing RAISES (fail-closed).
CU_PLACEHOLDER_BACKFILL_FILES={'congress_cosponsor_network_20260625_205554.xlsx':'data/network/cu_placeholder_backfill_20260625.json'}
def cu_backfill_ties(path):
    """{(from_bioguide,to_bioguide): weight} to SUM into this CU network file's directed rows."""
    v=os.environ.get('CU_PLACEHOLDER_BACKFILL','1')
    if v not in ('0','1',''): raise ValueError('CU_PLACEHOLDER_BACKFILL must be 0|1, got %r'%v)
    base=os.path.basename(str(path)); rel=CU_PLACEHOLDER_BACKFILL_FILES.get(base)
    if v=='0' or not rel: return {}
    doc=json.load(open(os.path.join(os.path.dirname(os.path.abspath(str(path))),rel),encoding='utf-8'))
    if doc.get('vintage')!=base: raise ValueError('CU backfill sidecar is for %r, not %r'%(doc.get('vintage'),base))
    return {(e['from'],e['to']):float(e['weight']) for e in doc['edges']}
def cu_edge_rows(path,ws):
    """(From,To,Weight) rows of a network 'Edges' sheet with the CU backfill summed in per direction
    (as Stage 3 groups ties by (From,To)); new pairs follow the file's rows."""
    add=cu_backfill_ties(path)
    for a,b,w in ws.iter_rows(min_row=2,max_col=3,values_only=True):
        if (a,b) in add and isinstance(w,(int,float)): w=w+add.pop((a,b))
        yield a,b,w
    for (a,b),w in add.items(): yield a,b,w
def select_network_file(files):
    """Select the newest augmented network, falling back to a base network only if needed."""
    files=[str(f) for f in files]
    if not files: raise FileNotFoundError('no congress_cosponsor_network_*.xlsx files found')
    augmented=[f for f in files if 'AUG' in os.path.basename(f).upper()]
    return max(augmented or files,key=os.path.getmtime)
# ---- NETWORK REGISTRY (sector promotion 2026-08-18) ------------------------------------------
# Paul promoted bank_reg and crypto from audit-only diagnostics to full networks. That took the
# config count from 3 to 5, and the blast-radius census warned that doing it by copy-paste means
# ~15 hardcoded per-config sites and four N-way ternaries. So the per-config FACTS live here once
# and the loaders read them, instead of every call site growing another branch.
#
# `symmetrize` is NOT derivable from the name and must be declared per config: holistic_norm is
# directed by design (it divides by sqrt(Out_u*In_v), so w(u->v) != w(v->u) is real information),
# every other family is mean-symmetrized.
#
# `floor` records how far below CU the graph runs, measured on the shipped kernel p=w/(w+6).
# It is carried into the bundle so the dashboard banner can state it: promoting a sector means the
# cascade/pathfinder/influence-max engines WILL run on it, and the surface has to say how thin the
# evidence is rather than let the toggle imply parity with CU.
NETWORKS = {
    'cu': {'label': 'Credit union', 'family': None, 'symmetrize': True, 'banner': None,
           'published': True},
    'holistic': {'label': 'All bills', 'family': 'holistic', 'symmetrize': True,
                 'banner': 'all-bills universe; scope labels in the manifest beside it',
                 'published': False},
    'holistic_norm': {'label': 'All bills - normalized', 'family': 'holistic_norm',
                      'symmetrize': False,
                      'banner': 'all-bills, volume- and chamber-normalized, DIRECTED '
                                'sponsor->cosponsor -- not symmetrized',
                      'published': False},
    'bank_reg': {'label': 'Bank regulation', 'family': 'sector', 'sector': 'bank_reg',
                 'symmetrize': True,
                 'banner': 'bank / depository-institution regulation, defined by CRS subject terms',
                 'published': False},
    'crypto': {'label': 'Crypto', 'family': 'sector', 'sector': 'crypto', 'symmetrize': True,
               'banner': 'digital assets, defined by a title screen (no CRS subject term exists)',
               'published': False},
}


def _net_config():
    """NET_CONFIG dial. 'cu' (default - byte-for-byte the pre-dial behavior) or any other key of
    NETWORKS; fail-closed, never a silent CU fallback. Read PER CALL so one process can serve
    every config through the config-keyed memo cache (ISO-5)."""
    c=os.environ.get('NET_CONFIG','cu')
    if c not in NETWORKS:
        raise ValueError('NET_CONFIG must be one of %s, got %r'%('|'.join(sorted(NETWORKS)),c))
    return c


def _select_sector(sector):
    """sector_network_<sector>_*.xlsx, newest. Fail-closed.

    Deliberately its own filename family: it must fail the congress_cosponsor_network_* glob (18
    call sites, three of them max-by-mtime with NO AUG filter, so a misnamed file would silently
    re-point the press/HCoI/stance layers) AND it must not be a holistic_network_* name, because
    _select_holistic RAISES on any member of that family it cannot classify as BASE_/AUG_."""
    pat='sector_network_%s_*.xlsx'%sector
    files=[str(f) for f in glob.glob(p(pat))]
    if not files:
        raise FileNotFoundError('NET_CONFIG=%s: no %s at root (run build_sector_diagnostic.py '
                                'with SECTOR_PROMOTE=1; fail-closed, no CU fallback)'%(sector,pat))
    return max(files,key=os.path.getmtime)
def _select_normalized(prefer_norm):
    """holistic_norm family: NORM (media folded on) preferred, NBASE otherwise. Fail-closed."""
    files=[str(f) for f in glob.glob(p('holistic_network_N*.xlsx'))]
    norm=[f for f in files if os.path.basename(f).startswith('holistic_network_NORM_')]
    nbase=[f for f in files if os.path.basename(f).startswith('holistic_network_NBASE_')]
    bad=[os.path.basename(f) for f in files if f not in norm and f not in nbase]
    if bad: raise ValueError('NET_CONFIG=holistic_norm: unclassifiable holistic_network_N* files: %s'%bad)
    pool=(norm or nbase) if prefer_norm else nbase
    if not pool: raise FileNotFoundError('NET_CONFIG=holistic_norm: no holistic_network_%s*.xlsx at root (run build_holistic_normalized.py; fail-closed, no fallback)'%('NORM_/NBASE_' if prefer_norm else 'NBASE_'))
    return max(pool,key=os.path.getmtime)
def _select_holistic(prefer_aug):
    """Newest holistic-family file. Categorical AUG preference when prefer_aug (a newer BASE never
    displaces an AUG — CU-parity rule); BASE-only otherwise (direction lives only in BASE).
    Unclassifiable holistic_network_* names and empty selections RAISE (D3 fail-closed)."""
    # Scope to THIS family only: the normalized config owns holistic_network_NBASE_/NORM_, and
    # those must not read as "unclassifiable" here (they tripped the ambiguity guard once, which
    # is the guard working -- but the guard belongs to the raw family, not the normalized one).
    files=[str(f) for f in glob.glob(p('holistic_network_*.xlsx'))
           if not os.path.basename(f).startswith(('holistic_network_NBASE_','holistic_network_NORM_'))]
    aug=[f for f in files if os.path.basename(f).startswith('holistic_network_AUG_')]
    base=[f for f in files if os.path.basename(f).startswith('holistic_network_BASE_')]
    bad=[os.path.basename(f) for f in files if f not in aug and f not in base]
    if bad: raise ValueError('NET_CONFIG=holistic: unclassifiable holistic_network_* files (not BASE_/AUG_): %s'%bad)
    pool=(aug or base) if prefer_aug else base
    if not pool: raise FileNotFoundError('NET_CONFIG=holistic: no holistic_network_%s*.xlsx at root (fail-closed; no CU fallback)'%('' if prefer_aug else 'BASE_'))
    return max(pool,key=os.path.getmtime)
def load_network():
    """Newest augmented congress_cosponsor network (base only when no AUG exists),
    mean-symmetrized (D2: 41 reverse-dup asymmetric pairs), name-keyed adjacency dict.
    Under NET_CONFIG=holistic: the holistic family instead (AUG-preferred, fail-closed)."""
    cfg=_net_config(); ck='adj' if cfg=='cu' else 'adj@'+cfg
    if ck not in _c:
        import openpyxl
        _spec=NETWORKS[cfg]
        if cfg=='cu':
            net=select_network_file(glob.glob(p('congress_cosponsor_network_*.xlsx')))
        elif cfg=='holistic_norm':
            net=_select_normalized(prefer_norm=True)
        elif _spec['family']=='sector':
            net=_select_sector(_spec['sector'])
        else:
            net=_select_holistic(prefer_aug=True)
        if _spec.get('banner'):
            print('[NET_CONFIG=%s] engine graph: %s (%s)'%(cfg,os.path.basename(net),_spec['banner']))
        ne=openpyxl.load_workbook(net,data_only=True)['Edges']
        b2n=bio2name(); mem=set(congestion()); adj=collections.defaultdict(dict); drop=cu_dropped_sponsors(net)
        for a,b,w in cu_edge_rows(net,ne):   # + CU_PLACEHOLDER_BACKFILL (2026-10-11)
            a=None if a in drop else b2n.get(a); b=b2n.get(b)
            if a in mem and b in mem and a!=b and isinstance(w,(int,float)):
                if not NETWORKS[cfg]['symmetrize']:
                    # DIRECTED by design: the normalized graph divides by sqrt(Out_u*In_v), so
                    # w(u->v) != w(v->u) carries real information about who recruited whom.
                    # Symmetrizing would average that away, which is the whole point of the config.
                    adj[a][b]=adj[a].get(b,0.0)+w
                else:
                    w=(adj[a][b]+w)/2 if b in adj[a] else w
                    adj[a][b]=w; adj[b][a]=w
        for m in mem: adj.setdefault(m,{})   # contract: EVERY member has an entry (isolated -> {});
                                             # the old per-file defaultdicts behaved this way implicitly
        _c[ck]=dict(adj); _c['netfile' if cfg=='cu' else 'netfile@'+cfg]=os.path.basename(net)
    return _c[ck]
def select_network_file_directed(files):
    """Newest NON-augmented network — the only vintage that still carries edge DIRECTION.

    build_multilayer_network.py re-keys every edge as ``(min(a,b),max(a,b))`` before writing the
    AUG workbook, so in an AUG file `source_bioguide` is simply the alphabetically-smaller
    bioguide: 100% of AUG rows satisfy source<target and 0 dyads appear in both directions.
    Any direction read off an AUG file is an artifact of that sort, not a fact about Congress.
    The base scrape is the authority (45.7% source<target, 45 genuinely reciprocal dyads)."""
    files=[str(f) for f in files if 'AUG' not in os.path.basename(str(f)).upper()]
    if not files: raise FileNotFoundError('no non-augmented congress_cosponsor_network_*.xlsx found')
    return max(files,key=os.path.getmtime)
def load_network_directed():
    """DIRECTED sponsor -> cosponsor edges, name-keyed:  {sponsor: {cosponsor: weight}}.

    The upstream scraper (congress_member_scraper.R, Stage 3) walks each bill a member SPONSORS
    and emits one row per cosponsor, so ``From`` is the SPONSOR and ``To`` is the COSPONSOR:
        out-degree = how many members signed YOUR bills   (sponsorship pull)
        in-degree  = how many sponsors YOU signed on with (cosponsor propensity)
    load_network() adds these two together, which is why the builder's hubs are 92.8% out-edge:
    for coalition RECRUITMENT the in-side is the asset and the out-side is nearly irrelevant.

    ADDITIVE — load_network() and every engine that consumes it are untouched.
    Under NET_CONFIG=holistic: the holistic BASE vintage only (the direction-preserving one)."""
    cfg=_net_config(); dk='dadj' if cfg=='cu' else 'dadj@'+cfg
    if dk not in _c:
        import openpyxl
        if cfg=='cu':
            net=select_network_file_directed(glob.glob(p('congress_cosponsor_network_*.xlsx')))
        elif cfg=='holistic_norm':
            net=_select_normalized(prefer_norm=False)
            print('[NET_CONFIG=holistic_norm] directed graph: %s (NBASE vintage; normalized + directed)'%os.path.basename(net))
        else:
            net=_select_holistic(prefer_aug=False)
            print('[NET_CONFIG=holistic] directed graph: %s (BASE vintage; direction preserved)'%os.path.basename(net))
        ne=openpyxl.load_workbook(net,data_only=True)['Edges']
        b2n=bio2name(); mem=set(congestion()); dadj=collections.defaultdict(dict); drop=cu_dropped_sponsors(net)
        for a,b,w in cu_edge_rows(net,ne):   # + CU_PLACEHOLDER_BACKFILL (2026-10-11)
            a=None if a in drop else b2n.get(a); b=b2n.get(b)
            if a in mem and b in mem and a!=b and isinstance(w,(int,float)):
                # same-direction duplicates are summed (the R producer already groups by (From,To),
                # so this only guards a re-scrape); the REVERSE direction stays a separate edge.
                dadj[a][b]=dadj[a].get(b,0.0)+w
        for m in mem: dadj.setdefault(m,{})   # same contract as load_network(): every member present
        _c[dk]=dict(dadj); _c['dnetfile' if cfg=='cu' else 'dnetfile@'+cfg]=os.path.basename(net)
    return _c[dk]
def cpf_diffusion(n,u):
    prt=party(); bf=bpf()
    return 0.4+0.6*math.sqrt(bf.get(n,0.24)*bf.get(u,0.24)) if (prt.get(n)!=prt.get(u) and prt.get(n) in 'DR' and prt.get(u) in 'DR') else 1.0
def cpf_pressure(n,u):
    prt=party()
    return 0.5 if (prt.get(n)!=prt.get(u) and prt.get(n) in 'DR' and prt.get(u) in 'DR') else (1.7 if (prt.get(n)==prt.get(u) and prt.get(n) in 'DR') else 1.0)
def sm64(x):
    """splitmix64 -> [0,1): deterministic per-key draw, independent of evaluation order (TRUE CRN)."""
    x=(x+0x9E3779B97F4A7C15)&0xFFFFFFFFFFFFFFFF
    x=((x^(x>>30))*0xBF58476D1CE4E5B9)&0xFFFFFFFFFFFFFFFF
    x=((x^(x>>27))*0x94D049BB133111EB)&0xFFFFFFFFFFFFFFFF
    return ((x^(x>>31))&0xFFFFFFFFFFFFFFFF)/0x10000000000000000
def build_P(adj=None,physics=cpf_diffusion,edge_mult=True):
    """Cascade kernel p = w*cpf*sigma_v / (w*cpf*sigma_v + 6); sigma_v = receiver susceptibility."""
    adj=adj if adj is not None else load_network()
    em=(lambda v: rc(v,'edge_mult')) if edge_mult else (lambda v:1.0)
    # `for x in (expr,)` binds the numerator ONCE instead of evaluating w*physics(u,v)*em(v) twice
    # per edge (2026-08-03). Same operations in the same order on the same operands, so the result
    # is bit-identical; physics() and em() are pure, so halving the call count is unobservable.
    return {u:{v:x/(x+6.0) for v,w in nb.items() for x in (w*physics(u,v)*em(v),)} for u,nb in adj.items()}
# ---- calibrated constant tables (single copies; env dials preserved) ----
PERS_NONE=float(os.environ.get('PERS_NONE','0.40'))   # USER-RATIFIED 2026-07-02: Ally parity — silence never outranks a declared Ally (Ally=0.40; the old 0.45 broke it; decision F-9 2026-10-11, PERS_NONE=0.45 reverts)
PERS={'Champion':0.10,'Defender':0.12,'Ally':0.40,'Highly Persuadable':0.65,
      'Persuadable':0.60,'Lean Persuadable':0.55,'True Neutral':0.50,
      'Lean Skeptic':0.40,'Neutral':0.50,'Skeptic':0.25,'Highly Skeptical':0.15,
      'Opponent':0.08,'—':PERS_NONE}
IMP={'Examination & Supervision':0.75,'CU Capital Requirements':0.25,'NCUA Budget & Insurance':1.0,'Interchange':2.0,
 'Payments':0.75,'Technology & Emerging':0.75,'Fraud Prevention':0.75,'Data Privacy & Cyber':1.0,'Charter Modernization':1.0,
 'Defense CU Issues':0.2,'Field of Membership':1.0,'CDFI Fund':0.1,'CFPB':1.0,'Credit Card Mandates':3.0,'TCPA':0.2,
 'Member Business Lending':0.3,'Mortgage & Housing':0.4,'CU Tax Status':3.0}   # ABB26 handout anchoring — 18 categories
# General engine priority is tier-anchored, then adjusted by a centered
# continuous read of the member's OTHER CU issue positions. The current topic
# is excluded so its stance cannot enter both this term and PERS. Influence is
# deliberately absent: network physics and bpf already model it.
PRIORITY_ORIENTATION_ALPHA={'flip':0.15,'coalition':0.25}
def engine_orientation(category_scores,exclude_category=None):
    """Fixed-importance signed CU orientation in [-1,1].

    Missing cells are explicit zeroes and remain in the denominator, so sparse
    records shrink toward neutral without a roster percentile. Excluding the
    canonical topic also excludes both CFPB source blocks because they share
    one category score.
    """
    category_scores=category_scores or {}
    weights={cat:w for cat,w in IMP.items() if cat!=exclude_category}
    denom=sum(weights.values())
    if denom<=0: return 0.0
    total=0.0
    for cat,weight in weights.items():
        raw=category_scores.get(cat,0.0)
        try: value=float(raw or 0.0)
        except (TypeError,ValueError):
            raise ValueError('category stance score must be numeric: %s=%r'%(cat,raw))
        if not -1.0<=value<=1.0:
            raise ValueError('category stance score must be in [-1,1]: %s=%r'%(cat,raw))
        total+=weight*value
    return max(-1.0,min(1.0,total/denom))
def blended_engine_priority(tier,category_scores,category=None,mode='flip'):
    """Tier priority plus an un-clipped leave-one-topic-out adjustment.

    Do not clip here: an upper clip erases Tier-1 differentiation. Consumers
    enforce nonnegative target mass only after adding direct issue engagement.
    """
    if mode not in PRIORITY_ORIENTATION_ALPHA:
        raise ValueError('engine priority mode must be flip or coalition')
    return (primacy_engine_value(tier)
            +PRIORITY_ORIENTATION_ALPHA[mode]
            *engine_orientation(category_scores,category))
_NONFINITE_WARNED={'priority_base_signal':0,'delivery_mult':0}   # QME 2026-10-01: one-time stderr warning per process per site
def _warn_nonfinite(site,detail):
    if not _NONFINITE_WARNED[site]: print('WARNING: moneyball_core.%s saw a non-finite input (%s); NaN is mapped to the floor/neutral value as before (further occurrences silent)'%(site,detail),file=sys.stderr)
    _NONFINITE_WARNED[site]+=1
def priority_base_signal(engagement,priority,base=0.15):
    """Replace only ``engagement + BASE*tier_priority`` and floor target mass."""
    total=float(engagement)+float(base)*float(priority)
    if not math.isfinite(total): _warn_nonfinite('priority_base_signal','engagement=%r priority=%r base=%r'%(engagement,priority,base))   # QME 2026-10-01: max(0.0,nan) is 0.0 -- value kept, now announced
    return max(0.0,total)
blocks=[3,11,19,27,35,43,51,59,67,75,83,91,99,145,115,107,123,131,153]
BLOCKCAT={3:'Examination & Supervision',11:'CU Capital Requirements',19:'NCUA Budget & Insurance',27:'Interchange',
 35:'Payments',43:'Technology & Emerging',51:'Fraud Prevention',59:'Data Privacy & Cyber',67:'Charter Modernization',
 75:'Defense CU Issues',83:'Field of Membership',91:'CDFI Fund',99:'CFPB',145:'CFPB',115:'TCPA',
 107:'Credit Card Mandates',123:'Member Business Lending',131:'Mortgage & Housing',153:'CU Tax Status'}
CATBLOCKS={sc:[b for b in blocks if BLOCKCAT[b]==BLOCKCAT[sc]] for sc in blocks}   # M3/H2: CFPB 99+145 fold
SGATE={'Highly Persuadable':1.00,'Persuadable':0.96,'Lean Persuadable':0.93,
       'True Neutral':0.90,'Neutral':0.90,'—':0.85,'Lean Skeptic':0.75,
       'Skeptic':0.55,'Highly Skeptical':0.45,'Opponent':0.35,
       'Ally':1.0,'Champion':1.0,'Defender':1.0}
def gate(n,cs): return max(0.15,rc(n,'seed_prob',1.0)*SGATE.get(cs.get(n,'—'),0.85))
TORD={'Champion':4,'Defender':4,'Ally':3,'Highly Persuadable':2,'Persuadable':1,
      'Lean Persuadable':0.5,'True Neutral':0,'Neutral':0,'Lean Skeptic':-0.5,
      'Skeptic':-1,'Highly Skeptical':-2,'Opponent':-4}
# Overall score rungs are weighted averages of TORD prototypes. Each cutoff is the midpoint between
# adjacent prototypes; the Highly-Skeptical/Opponent midpoint is -3.0.
# This prevents positive records from being labeled skeptical and negative records from being labeled
# persuadable. The env override remains available for controlled sensitivity checks.
OVERALL_OPPONENT_CUTOFF=float(os.environ.get('OVERALL_OPPONENT_CUTOFF','-1.50'))
# 2026-07-18 roster calibration (Paul, "tighten the bands again, too many allies"): the positive
# labels now require a full prototype-strength record, and the negative tail is compressed so
# genuinely adverse mixed records no longer disappear into Neutral/persuadable labels. Zero remains
# the sign boundary: positive records cannot become skeptical, negative records cannot become
# persuadable. Ally owns the scored top of the ladder; Champion is not a score band -- Legislative+
# Press Tier 1 is the complete and sufficient qualification for the display crown. The 3.65 legacy
# floor is retained solely for the already-ratified aggregate say-do audit below.
# PROVENANCE 2026-07-19: these are the values ratified on lane
# claude/legislative-letters-import-be42f9 @f6388d9. The live tree was on a branch that never
# received them, so a regeneration silently rewrote the published ladder back to the older
# prototype-midpoint values (Ally 2.50) and Ally ballooned to 247/533. Restored here WITHOUT taking
# that commit's unrelated money-engine and letter-decay repairs, which remain an open merge decision.
OVERALL_CHAMPION_SCORE_FLOOR=3.65
OVERALL_BANDS=((3.00,'Ally'),(2.00,'Highly Persuadable'),
               (1.00,'Persuadable'),(0.40,'Lean Persuadable'),(0.00,'True Neutral'),
               (-0.30,'Lean Skeptic'),(-0.75,'Skeptic'),(OVERALL_OPPONENT_CUTOFF,'Highly Skeptical'))
def validate_overall_bands(bands,opponent_cutoff):
    """Fail closed when an env override makes the fixed overall ladder non-monotone."""
    cuts=[float(c) for c,_ in bands]; labels=[t for _,t in bands]
    if not cuts or any(cuts[i]<=cuts[i+1] for i in range(len(cuts)-1)):
        raise ValueError('overall band cutoffs must be strictly descending')
    if len(labels)!=len(set(labels)):
        raise ValueError('overall band labels must be unique')
    if labels[-1]!='Highly Skeptical' or abs(cuts[-1]-float(opponent_cutoff))>1e-12:
        raise ValueError('OVERALL_OPPONENT_CUTOFF must be the Highly Skeptical floor')
    return tuple(bands)
validate_overall_bands(OVERALL_BANDS,OVERALL_OPPONENT_CUTOFF)
def overall_thresholds():
    return {'scale':'strict-fixed-nine-tier-base-plus-board-top13-crown-v7',
            'bands':[{'minimum':cut,'tier':tier} for cut,tier in OVERALL_BANDS],
            'below_last':'Opponent','missing':'—','normalization':'fixed-not-roster-percentile',
            'champion_rule':{'board':'active_primacy_board_top13',
                             'qualification':'top13_with_a_qualifying_action',
                             'enforced_at':'fec/stance/overall_stance_policy.py:161'}}
def overall_rounding(cm):
    """Bounded +/-0.05 nudge from exceptional cross-domain performance."""
    earned=[t for t in cm.values() if t in TORD]
    if not earned: return 0.0
    balance=sum(t in ('Champion','Defender') for t in earned)-sum(t=='Opponent' for t in earned)
    return max(-0.05,min(0.05,0.10*balance/len(earned)))
def _overall_value(cm):
    sw=si=0.0
    for c,t in cm.items():
        if t in TORD: w=IMP.get(c,0.5); sw+=TORD[t]*w; si+=w
    return None if si==0 else sw/si+overall_rounding(cm)
def _overall_scored_tier(cm):
    """Legacy high-score marker used only by the aggregate say-do audit."""
    x=_overall_value(cm)
    if x is None: return '—'
    if x+1e-12>=OVERALL_CHAMPION_SCORE_FLOOR: return 'Champion'
    return overall_tier_from_value(x)
def overall_tier_from_value(value):
    """Map one numeric overall score to the fixed semantic ladder."""
    if value is None: return '—'
    x=float(value)
    return next((label for cut,label in OVERALL_BANDS if x+1e-12>=cut),'Opponent')
def overall_tier(cm,member=None,overall_capped=None):
    """IMP-weighted fixed base tier. Champion is deliberately absent; see overall_display_tier."""
    return overall_tier_from_value(_overall_value(cm))
def overall_display_tier(cm,legislative_press_tier,member=None,overall_capped=None,board_top13=None):
    """One overall label: the analytic band, crowned only for the board's top 13.

    The crown is the active primacy board's top 13 WITH a qualifying action, not
    a separate score overlay; pass board_top13 (rank_honors overall_board.top13)
    to read that board directly. Priority tier 1 IS that top 13 -- honors_views
    rejects any generation whose tier map is not the board's 13/37/85 bucketing
    -- so the tier is only the fallback when no board is supplied. The
    qualifying-action half is enforced fail-closed by the honors producer
    (overall_stance_policy.py:161), which refuses a crown without action.
    """
    crowned=member in board_top13 if board_top13 is not None else int(legislative_press_tier or 0)==1
    if crowned:
        return 'Champion'
    return overall_tier(cm,member,overall_capped)
# System-level stance-integrity gate for the money allocators (per_topic_flip, optimize; Paul 2026-09-30,
# "F1. Yes"). A member whose IMP-weighted analytic overall tier (overall_tier: no display crown) is in this
# set is not a money target merely because one topic cell is mild; overall 'Lean Skeptic' stays eligible.
# lt_priming.py and the dashboard prime() (BLOCKOV) hold the same set as their own copies.
STANCE_GATE_BLOCKED=frozenset({'Skeptic','Highly Skeptical','Opponent'})
def stance_gate_tiers(cs_doc,members):
    """Overall tier per member, read exactly as per_topic_flip's gate always has (category_stance.json).

    FAILS OPEN (QME 2026-10-01): a member with no matrix row is evaluated on an empty row ({}), so the tier
    is '—' (overall_tier of no evidence), which is not in STANCE_GATE_BLOCKED, and the member stays ELIGIBLE
    for money. Callers needing fail-closed must check `n in cs_doc['matrix']` themselves.
    `caps` (the local overall_capped read below) is passed to overall_tier as `overall_capped`, which does
    not consult it: the parameter is dead there today, kept because callers pass it. Gate results unchanged.
    """
    caps=(cs_doc.get('gate') or {}).get('overall_capped') or {}
    matrix=cs_doc['matrix']
    return {n:overall_tier(matrix.get(n,{}),n,caps) for n in members}

# Noble-Turner going-public parameters. Both the Python shock engine and shipped dashboard bundle
# call this exact implementation so fine stance tiers and the True-Neutral uncertainty term cannot drift.
SALIENCE_TSCORE={'Champion':3,'Defender':2.5,'Ally':2,'Highly Persuadable':1.25,
                 'Persuadable':0.75,'Lean Persuadable':0.25,'True Neutral':0,
                 'Lean Skeptic':-0.5,'Skeptic':-1,'Highly Skeptical':-2,'Opponent':-3}
def salience_issue_params(matrix,members,party_by_member,cat,defensive=(),backfire0=0.05,backfire_psi=0.10):
    sD=[SALIENCE_TSCORE[matrix.get(m,{}).get(cat)] for m in members
        if party_by_member.get(m)=='D' and matrix.get(m,{}).get(cat) in SALIENCE_TSCORE]
    sR=[SALIENCE_TSCORE[matrix.get(m,{}).get(cat)] for m in members
        if party_by_member.get(m)=='R' and matrix.get(m,{}).get(cat) in SALIENCE_TSCORE]
    nall=sum(1 for m in members if matrix.get(m,{}).get(cat) in SALIENCE_TSCORE)
    z=abs((sum(sD)/max(1,len(sD)))-(sum(sR)/max(1,len(sR))))
    zn=min(1.0,z/3.0)
    psi=1.0-nall/max(1,len(members))
    neutral=sum(1 for m in members if matrix.get(m,{}).get(cat)=='True Neutral')
    psi=min(1.0,psi+0.3*(neutral/max(1,nall)))
    b=min(0.35,float(backfire0)+float(backfire_psi)*psi)
    return {'z':round(z,2),'zn':round(zn,3),'psi':round(psi,3),
            'p':round((1-b)*(1-zn),3),'q':round((1-b)*zn,3),'b':round(b,3),
            'sq':'defend' if cat in set(defensive) else 'offense'}

# ---- SAY-DO gate + delivery discount (user-ratified 2026-07-07, boss review: "Hill says a lot,
# doesn't follow through") — SINGLE COPY, applied where category_stance.json is WRITTEN so every
# consumer (engines, desk, dashboard, col G, audit) inherits it identically. Cheap-talk vs costly
# signals: the top stance rungs (Champion/Defender) must be backed by a COSTLY, RECORDED action.
# A cell keeps its top tier if either recorded-action path holds; else it displays Ally (raw preserved
# in the gate block). Committee office is deliberately NOT a say-do action: it is already represented
# in the Influence pillar and cannot substitute for authored legislation or a recorded vote.
#   A  Authored     — sponsored/original-cosponsored an in-category bill (offense: you write reform).
#   V  Voted line   — a recorded final-passage CU-aligned vote in the category (vote_category_lean).
# Rule C (badge, never a cap): 'proven' = an AUTHORED bill in the category that advanced past
# introduction. A second aggregate guard requires A/V evidence in at least two-thirds of the raw
# top-tier cells supporting an overall Champion label. A fixed high-volume follow-through check also
# caps prolific direct sponsors (10+ CU bills) when no more than 20% advanced, except where exceptional
# direct authorship (20+ tracked bills) corroborates every top-tier domain. This preserves the say-do
# safeguard without penalizing an unusually substantial sponsored-but-incomplete record. The thresholds
# are roster-independent; ranks are unaffected (stances never feed the pillars).
_ADV_ACTIONS={'Passed House','Passed Senate','Agreed to in Senate','Resolving Differences','Became Law',
              'Failed to pass over veto'}
# label->canonical map + curated non-CU drop list: kept in lockstep with make_member_desk's copies
# (the desk parses the same role sheets for display; consolidate to this import at next touch).
_LBL2CAT=dict(BR.LABEL_TO_CANONICAL)
_DROP_BILLS=BR.EXCLUDED_TITLE_PHRASES
_CORR=None
def manual_corroboration_role(value):
    """Exact role semantics: a later cosponsor is not an author."""
    normalized=''.join(str(value or '').casefold().split()).replace('-','')
    return {'sponsor':'sponsor','originalcosponsor':'original-cosponsor',
            'cosponsor':'cosponsor'}.get(normalized,'other')

def bill_corroboration():
    """{member: {'sponsored': set(canonical cats), 'advanced': set(...)}} from the workbook role
    sheets ('Sponsor Interactions' + 'Original Co-Sponsor Interaction'; plain cosponsorship does NOT
    corroborate under Rule A). Lazy + cached; NFC-casefold name match against the roster."""
    global _CORR
    if _CORR is not None: return _CORR
    routing_path=p('legislation_routing_audit.json')
    if os.path.isfile(routing_path):
        from stance.adjudicated_corroboration import from_routing
        with open(routing_path,encoding='utf-8') as handle:
            routing=json.load(handle)
        if routing.get('schema')!='legislation-routing-audit-v1':
            raise ValueError('Invalid adjudicated corroboration routing schema')
        with open(p('bill_repository_live.json'),encoding='utf-8') as handle:
            repository=json.load(handle)
        _CORR=from_routing(routing['rows'],congestion(),repository['bills'])
        return _CORR
    policy_path=p('data/engine/sentinel_admission_policy.json')
    if os.path.isfile(policy_path):
        with open(policy_path,encoding='utf-8') as handle:
            if json.load(handle).get('live_legislative_pillar') is True:
                raise ValueError('Live label corroboration requires adjudicated routing')
    import openpyxl,unicodedata
    nrm=lambda s:unicodedata.normalize('NFC',str(s or '')).strip().lower()
    roster={nrm(m):m for m in congestion()}
    out=collections.defaultdict(lambda:{'sponsored':set(),'advanced':set(),
                                        'direct_sponsored_bills':set(),'advanced_direct_bills':set()})
    try:
        wb=openpyxl.load_workbook(WORKBOOK,read_only=True,data_only=True)
        missing=[s for s in ('Sponsor Interactions','Original Co-Sponsor Interaction') if s not in wb.sheetnames]
        if missing: raise KeyError('missing required bill sheets: %s'%missing)
        overrides=BR.load_overrides(p('data/legislation/bill_overrides.csv'))
        for sheet in ('Sponsor Interactions','Original Co-Sponsor Interaction'):
            for r in wb[sheet].iter_rows(min_row=2,values_only=True):
                # Artifact-tool writes a standards-valid sparse row when the
                # trailing bill_summary is blank.  Read-only openpyxl then
                # returns a shorter tuple; pad to the named 13-column bill
                # contract instead of treating an omitted empty XML cell as
                # corrupt evidence.
                # Exactly 12 IS that sparse row; anything shorter is a malformed sheet and must still reach the fail-closed IndexError below.
                if len(r)==12: r=tuple(r)+(None,)
                m=roster.get(nrm(r[2]))
                if not m: continue
                if BR.excluded_bill(r[3],r[9],r[4],r[12]): continue
                cat=_LBL2CAT.get(r[0],r[0]) if r[0] else ''
                ov=BR.override_for(overrides,r[3],r[9])
                if ov and not BR.override_included(ov): continue
                if ov and ov.get('Category'): cat=BR.canonical_category(ov['Category']) or cat
                if not cat: continue
                out[m]['sponsored'].add(cat)
                if str(r[8] or '') in _ADV_ACTIONS: out[m]['advanced'].add(cat)
                if sheet=='Sponsor Interactions':
                    key=(str(r[3] or ''),str(r[9] or ''))
                    out[m]['direct_sponsored_bills'].add(key)
                    if str(r[8] or '') in _ADV_ACTIONS: out[m]['advanced_direct_bills'].add(key)
        wb.close()
        manual=p('manual_bill_evidence.csv')
        if os.path.exists(manual):
            for r in csv.DictReader(open(manual,encoding='utf-8-sig')):
                role=manual_corroboration_role(r.get('Role'))
                if role not in ('sponsor','original-cosponsor'): continue
                m=roster.get(nrm(r.get('Member')))
                if not m or BR.excluded_bill(r.get('Bill'),r.get('Congress'),r.get('Title'),r.get('Summary')): continue
                cat=BR.canonical_category(r.get('Category'))
                if not cat: continue
                out[m]['sponsored'].add(cat)
                if str(r.get('LastAction') or '') in _ADV_ACTIONS: out[m]['advanced'].add(cat)
                if role.strip()=='sponsor':
                    key=(str(r.get('Bill') or ''),str(r.get('Congress') or ''))
                    out[m]['direct_sponsored_bills'].add(key)
                    if str(r.get('LastAction') or '') in _ADV_ACTIONS: out[m]['advanced_direct_bills'].add(key)
    except (OSError,KeyError,ValueError,IndexError) as e:
        raise RuntimeError('required bill corroboration source is invalid; refusing to disable the say-do gate') from e
    _CORR=dict(out); return _CORR
_VOTECORR=None
def vote_corroboration():
    """{name: set(cats with a recorded net-CU-aligned final-passage vote, lean>=0.5, n>=1)} from
    vote_category_lean.json (Path V — a floor vote is on the record, more than talk). Lazy+cached;
    missing file -> {} (Path V simply contributes nothing; never fail-caps)."""
    global _VOTECORR
    if _VOTECORR is not None: return _VOTECORR
    try:
        with open(p('vote_category_lean.json'),encoding='utf-8') as handle:
            VL=json.load(handle)
        nof=VL['name_of']
        out=collections.defaultdict(set)
        for bio,cats in VL['votes'].items():
            nm=nof.get(bio)
            if not nm: continue
            for c,d in cats.items():
                if (d.get('lean',0) or 0)>=0.5 and (d.get('n',0) or 0)>=1: out[nm].add(c)
        _VOTECORR=dict(out)
    except FileNotFoundError:
        _VOTECORR={}
    except Exception as exc:
        raise RuntimeError(
            'vote_category_lean.json is present but invalid; refusing to treat malformed vote evidence as absent'
        ) from exc
    return _VOTECORR
def cap_uncorroborated(member,cm):
    """Say-do gate on one member's category map (paths A/V — see header). Returns (gated_cm,
    capped{cat:raw_tier}, proven[cats], corrob{cat:'A'|'V'}). Only ever LOWERS Champion/Defender
    to Ally; all other tiers pass through untouched."""
    corr=bill_corroboration()
    cm2=dict(cm); capped={}; proven=[]; corrob={}
    # Empty accepted evidence supplies no authorship; Path V may still qualify.
    spon=corr.get(member,{}).get('sponsored',set()); adv=corr.get(member,{}).get('advanced',set())
    vcats=vote_corroboration().get(member,set())
    for c,t in cm.items():
        if t in ('Champion','Defender'):
            if c in spon:
                corrob[c]='A'
                if c in adv: proven.append(c)
            elif c in vcats: corrob[c]='V'
            else: capped[c]=t; cm2[c]='Ally'
    return cm2,capped,sorted(proven),corrob

SAY_DO_OVERALL_RATIO=2.0/3.0
SAY_DO_MIN_TOP_CELLS=6
SAY_DO_DIRECT_VOLUME_MIN=10
SAY_DO_MAX_ADVANCEMENT_RATE=0.20
SAY_DO_EXCEPTIONAL_AUTHORSHIP_MIN=20
def overall_say_do_gate(member,gated_cm,raw_cm,corroborated):
    """Return (final overall tier, audit detail) for the aggregate say-do rule.

    The denominator is the member's raw Champion/Defender cells, before the cell-level A/V caps.
    The numerator is the subset with authored/original-cosponsored legislation or an aligned recorded
    vote. A separate fixed-volume check catches a large introduced-only direct-sponsorship trail. The
    bill source is the availability sentinel: if it cannot be loaded, the rule fails open instead of
    penalizing an entire roster for a missing input.
    """
    # The public base ladder now tops out at Ally. Keep the legacy 3.65 high-score marker here so
    # the aggregate say-do evidence audit remains available as provenance. It no longer vetoes the
    # Tier-1 display crown: Tier 1 is the complete Champion qualification.
    raw=_overall_scored_tier(raw_cm)
    top=sorted(c for c,t in raw_cm.items() if t in ('Champion','Defender'))
    corr=bill_corroboration(); member_corr=corr.get(member,{}) or {}
    delivered=sorted(c for c in top if corroborated.get(c) in ('A','V'))
    available=bool(corr)
    ratio=(len(delivered)/len(top)) if top else 1.0
    direct=len(member_corr.get('direct_sponsored_bills',set()))
    advanced_direct=len(member_corr.get('advanced_direct_bills',set()))
    advancement_rate=(advanced_direct/direct) if direct else None
    exceptional_authorship=bool(direct>=SAY_DO_EXCEPTIONAL_AUTHORSHIP_MIN and top and
                                len(delivered)==len(top))
    thin_high_volume=bool(direct>=SAY_DO_DIRECT_VOLUME_MIN and advancement_rate is not None and
                          advancement_rate<=SAY_DO_MAX_ADVANCEMENT_RATE+1e-12 and
                          not exceptional_authorship)
    low_coverage=bool(top and ratio+1e-12<SAY_DO_OVERALL_RATIO)
    insufficient_breadth=len(top)<SAY_DO_MIN_TOP_CELLS
    capped=bool(available and raw=='Champion' and (insufficient_breadth or low_coverage or thin_high_volume))
    final='Ally' if capped else raw
    return final,{'raw':raw,'final':final,'top_cells':top,'delivered_cells':delivered,
                  'delivery_ratio':round(ratio,6),'threshold':round(SAY_DO_OVERALL_RATIO,6),
                  'direct_sponsored_bills':direct,'advanced_direct_bills':advanced_direct,
                  'direct_advancement_rate':(round(advancement_rate,6) if advancement_rate is not None else None),
                  'minimum_top_tier_cells':SAY_DO_MIN_TOP_CELLS,
                  'high_volume_minimum':SAY_DO_DIRECT_VOLUME_MIN,
                  'high_volume_max_advancement_rate':SAY_DO_MAX_ADVANCEMENT_RATE,
                  'exceptional_authorship_minimum':SAY_DO_EXCEPTIONAL_AUTHORSHIP_MIN,
                  'exceptional_authorship_override':exceptional_authorship,
                  'cap_reasons':([r for r,flag in (('insufficient_top_tier_breadth',insufficient_breadth),
                                                   ('top_tier_action_coverage',low_coverage),
                                                   ('high_volume_low_followthrough',thin_high_volume)) if flag]),
                  'evidence_available':available}
_DELIV=None
def delivery_mult(m):
    """Say-do talker discount on engine TARGET VALUE (never on rank): clip(1-0.15*max(0,gap-1),
    0.70,1.00). gap from say_do_gap.json (weekly diagnostic); missing artifact or member -> neutral
    1.0 (the receptivity layer's fallback pattern)."""
    global _DELIV
    if _DELIV is None:
        try:
            with open(p('say_do_gap.json'),encoding='utf-8') as handle:
                g=json.load(handle)['members']
            _DELIV={n:(v.get('gap') or 0.0) for n,v in g.items()}   # raw gaps; clip computed per call (testable)
        except FileNotFoundError:
            _DELIV={}
        except Exception as exc:
            raise RuntimeError(
                'say_do_gap.json is present but invalid; refusing to treat malformed delivery evidence as neutral'
            ) from exc
    gap=_DELIV.get(m,0.0)
    if isinstance(gap,str) or not isinstance(gap,(int,float)): raise TypeError('say_do_gap.json gap for %r must be numeric, got %s %r'%(m,type(gap).__name__,gap))   # QME 2026-10-01: clear message (was an opaque str-minus-float TypeError)
    if not math.isfinite(gap): _warn_nonfinite('delivery_mult','member=%r gap=%r'%(m,gap))   # QME 2026-10-01: max(0.0,nan) is 0.0 -> mult 1.0 kept, now announced
    return max(0.70,min(1.00,1-0.15*max(0.0,gap-1.0)))

# Nickname equivalence classes (QA3 2026-07-04, Bernie/Bernard-class resolver misses) — SINGLE COPY.
# fetch_interviews.py carries the original inline; consolidate it to this import at next touch.
NICK_SETS=[{'bernard','bernie'},{'charles','chuck','charlie'},{'christopher','chris'},{'richard','dick','rich','rick'},
 {'michael','mike'},{'james','jim','jimmy'},{'robert','bob','rob','bobby'},{'thomas','tom','tommy'},{'daniel','dan','danny'},
 {'joseph','joe','joey'},{'timothy','tim'},{'patrick','pat'},{'william','bill','will','billy'},{'edward','ed','ted','eddie'},
 {'theodore','ted'},{'elizabeth','liz','beth','betty'},{'katherine','kathryn','katie','kate','kathy'},{'margaret','maggie','meg'},
 {'benjamin','ben'},{'alexander','alex'},{'anthony','tony'},{'andrew','andy','drew'},{'matthew','matt'},{'jonathan','jon'},
 {'nicholas','nick'},{'gregory','greg'},{'lawrence','larry'},{'gerald','jerry'},{'ronald','ron','ronny'},{'donald','don'},
 {'kenneth','ken'},{'stephen','steven','steve'},{'david','dave'},{'samuel','sam'},{'frederick','fred'},{'susan','sue','suzy'},
 {'deborah','debbie','deb'},{'cynthia','cindy'},{'jacquelyn','jacqueline','jackie'},{'raphael','rafael','raffi'},
 {'joshua','josh'},{'zachary','zach','zack'},{'abigail','abby'},{'rebecca','becca','becky'},{'jefferson','jeff'},
 {'jeffrey','jeff'},{'mitchell','mitch'},{'randall','randy'},{'terrence','terri','terry'},{'vincent','vince'}]
_NICK={}
for _ns in NICK_SETS:
    for _a in _ns: _NICK.setdefault(_a,set()).update(_ns)
def name_variants(first):
    """All nickname-equivalent first names for `first` (incl. itself)."""
    return _NICK.get(first,set())|{first}
