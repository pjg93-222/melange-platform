# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT,today  # centralized root
"""PYTHON RANK AUTHORITY — roadmap #9 phase 2 (system-of-record inversion, user sign-off 2026-07-02).

Python computes the ENTIRE rank chain from raw inputs; the workbook is a self-refreshing VIEW
(fullCalcOnLoad — Excel recalculates its formulas whenever the user opens it):

  Press Release - Raw ──(pos/neg-max normalization, Python: moneyball_core.press_matrix)──> normalized
  normalized × row-535 tiered weights (user dial literals) ──> press pillar
  Press% = pillar/max (active at a diminished weight)
  Influence% = committee E/max · Leg% = PERCENTRANK(O,5) · Demo% = PERCENTRANK(F,5)
  Score  = .45·Leg% + .10·Press% + .40·Influence% + .05·Demo%
  Rank   = descending competition rank

Excel-exact PERCENTRANK, pinned empirically (phase 1): truncate to 5 DECIMAL digits.

MODES
  --write : write ranks_python.json (atomic) = the official reporting rank
            authority (moneyball_core.scores()/ranks()). Allocation engines use
            the primacy tier plus the shared leave-one-topic-out orientation
            adjustment for their general-priority term.
  (none)  : audit mode. If the workbook's cached formula results are COHERENT with its raw inputs
            (i.e. Excel recalculated since the last raw write), enforce EXACT parity — the
            formula drift tripwire. Formal office is retained as display-only public-exposure
            context; it is not an unratified additive term in the rank authority. If
            the view is stale, verify the authority against a fresh recompute instead. Exit 1 only
            on real drift or a stale AUTHORITY (rerun pipeline)."""
import sys,math,json,os,datetime,statistics
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
# Candidate entry point consumes frozen nonlegislative values and exits before
# workbook/snapshot mutation. The production --write path remains unchanged.
if '--effectiveness-shadow' in sys.argv:
    from engine.effectiveness_integration import reporting_shadow_cli
    reporting_shadow_cli(sys.argv[1:])
    sys.exit(0)
import openpyxl
import moneyball_core as MC
from engine import influence_roles as IR
from engine.pillar_policy import PILLAR_WEIGHTS, scoring_policy
from engine.effectiveness_integration import legislative_percentiles
B=MB_ROOT
WRITE='--write' in sys.argv
wb=openpyxl.load_workbook(os.path.join(B, 'Moneyball Main Version - Blended.xlsx'),data_only=True)   # NOT read_only (random-access pathology; empty reads on openpyxl-saved files)
pn=wb['Press Release - Normalized']; ra=wb['Rank & Allocation']; bm=wb['Blended Model']
# Member roster from R&A col A (TYPED values — survives openpyxl saves; Blended col A is formula-backed
# and its cache dies whenever sync/blend save the workbook: the openpyxl-save/cached-value dragon).
members=[ra.cell(r,1).value for r in range(3,536) if ra.cell(r,1).value]
bmrows={m:i+3 for i,m in enumerate(members)}   # Blended rows mirror R&A row order (asserted in fallback path)
prow={pn.cell(r,1).value:r for r in range(2,535) if pn.cell(r,1).value}
norm,w535,hdrs=MC.press_matrix()
COLS=list(norm)
# ---- press pillar ----
pillar={m:sum(norm[c].get(m,0.0)*w535[c] for c in COLS) for m in members}
# ---- percents + score + rank (Excel-exact) ----
def percentrank(vals,x):
    # Excel PERCENTRANK.INC(...,5): (#strictly below)/(n-1), TRUNCATED to 5 DECIMAL digits
    # (empirically pinned 2026-07-02: 'significance' behaves as decimal places, not sig digits).
    below=sum(1 for v in vals if v<x)
    return math.floor(below/(len(vals)-1)*1e5)/1e5
# O/E/F are FORMULA-backed in Blended -> their caches die after any openpyxl save (sync/blend) and
# only revive when the user opens+saves in Excel. They change ONLY via user edits in Excel (which
# restores caches), so: read live when alive (+ refresh the snapshot), else fall back to the snapshot.
# Bootstrap: newest cache-alive workbook in backups/ (pre-write copies are Excel-saved).
SNAP=os.path.join(B, 'data', 'engine', 'pillar_inputs_snapshot.json')
def _read_OEF(bmx,rows):
    o={m:bmx.cell(rows[m],15).value for m in members}
    e={m:bmx.cell(rows[m],5).value  for m in members}
    fv={m:bmx.cell(rows[m],6).value for m in members}
    alive=sum(1 for m in members if o[m] is not None)>=len(members)*0.9
    return alive,{m:(o[m] or 0.0) for m in members},{m:(e[m] or 0.0) for m in members},{m:(fv[m] or 0.0) for m in members}
_live_policy_path=os.path.join(B,'data/engine/sentinel_admission_policy.json')
_live_policy=json.load(open(_live_policy_path,encoding='utf-8')) if os.path.isfile(_live_policy_path) else {}
_live_legislative=None
if _live_policy.get('live_legislative_pillar') is True:
    from engine.live_legislative_pillar import recompute,nonlegislative_inputs
    _live_legislative=recompute(B,wb,members,MC.IMP)
    O=_live_legislative['pillar']
    E,F=nonlegislative_inputs(wb,members)
    alive=False
else:
    _names_alive=sum(1 for m in members if bm.cell(bmrows[m],1).value==m)
    alive,O,E,F=_read_OEF(bm,bmrows) if _names_alive>=len(members)*0.9 else (False,{},{},{})
    import datetime as _dt
    # PILLAR_SNAPSHOT_FREEZE=1 (set by audit_full's shadow-parity run) makes this computation strictly
    # read-only: the audit must never advance the snapshot vintage (rank-integrity audit 2026-07-17).
    _SNAP_FREEZE=os.environ.get('PILLAR_SNAPSHOT_FREEZE')=='1'
    if alive:
        if not _SNAP_FREEZE:
            MC.dump_atomic({'O':O,'E':E,'F':F,'stamped':_dt.datetime.now().astimezone().isoformat(timespec='seconds'),
                            'note':'pillar formula-cell snapshot (auto-refreshed while view cache alive)'},SNAP)
    else:
        try:
            _sn=json.load(open(SNAP)); O,E,F=_sn['O'],_sn['E'],_sn['F']
            _stamp=_sn.get('stamped')
            _agenote=''
            if _stamp:
                _aged=( _dt.datetime.now().astimezone()-_dt.datetime.fromisoformat(_stamp)).days
                _agenote=' stamped %s (%dd old)'%(_stamp,_aged)
                if _aged>14: print('  !! WARNING: pillar snapshot is %d days old — the leg/inf/demo basis may be badly stale (2026-07-16 522-rank incident class); open+recalc the workbook in Excel'%_aged)
            else:
                _agenote=' (UNSTAMPED pre-2026-07-17 snapshot — vintage unknown)'
            print('  (view cache dead -> O/E/F from snapshot%s; they change only via user Excel edits, which revive the cache)'%_agenote)
        except FileNotFoundError:
            import glob as _g
            cands=sorted(_g.glob(os.path.join(B, 'backups', '*.xlsx')),key=os.path.getmtime,reverse=True)
            for cb in cands:
                try:
                    _wb2=openpyxl.load_workbook(cb,data_only=True); _bm2=_wb2['Blended Model']
                    rows2={_bm2.cell(r,1).value:r for r in range(3,536) if _bm2.cell(r,1).value}
                    if len(rows2)<len(members)*0.9: continue
                    a2,O,E,F=_read_OEF(_bm2,rows2)
                    if a2:
                        if not _SNAP_FREEZE:
                            MC.dump_atomic({'O':O,'E':E,'F':F,'stamped':_dt.datetime.now().astimezone().isoformat(timespec='seconds'),
                                            'note':'bootstrapped from %s'%os.path.basename(cb)},SNAP)
                        print('  (bootstrapped O/E/F snapshot from cache-alive backup: %s)'%os.path.basename(cb)); break
                except Exception: continue
            else:
                print('FATAL: no O/E/F source (view dead, no snapshot, no cache-alive backup)'); sys.exit(1)
# Formal chamber/party office is display-only public-exposure context. The registry is validated and
# shipped beside the rank decomposition, but its hand-entered weights do not modify Influence or rank.
ROLE_PATH=os.path.join(B, 'data', 'members', 'formal_visibility_roles.json')
try:
    _roles=json.load(open(ROLE_PATH,encoding='utf-8'))
    if _roles.get('schema')!='party-elite-influence-roles-v3': raise ValueError('schema mismatch')
    if today()>=datetime.date.fromisoformat(_roles['valid_until']): raise ValueError('registry expired')
    _unknown=sorted(set((_roles.get('members') or {}))-set(members))
    if _unknown: raise ValueError('unknown roster names: %s'%_unknown[:3])
    _not_elite=sorted(m for m,v in (_roles.get('members') or {}).items() if v.get('role_type')!='party_elite')
    if _not_elite: raise ValueError('non-party-elite registry entries: %s'%_not_elite[:3])
except (FileNotFoundError,KeyError,ValueError) as _e:
    print('FATAL: formal role influence registry invalid: %s'%_e); sys.exit(1)
# LEAD-TIME WARNING (2026-07-19 audit): the expiry above is a HARD chain abort at step ~13 of ~38 —
# before ranks, dashboards or desks exist — and data/members/formal_visibility_roles.json has NO producer script,
# so nothing regenerates it. valid_until is currently 2027-01-03, the 120th-Congress seating date;
# the unknown-roster-names check on line 106 trips on the freshman swap independently of the calendar,
# whichever lands first. This warning is deliberately NOT an audit_full ck(): a failed check exits 1
# and run_pipeline aborts the chain, so a "warn early" check would itself cause the outage it warns
# about. It is also appended to this script's FINAL stdout line (see the ranks_python print below),
# because run_pipeline keeps only that last line, truncated to 90 chars. Owner action = refresh the
# registry for the new Congress; do NOT just extend valid_until, which hides a stale roster.
ROLE_EXPIRY_WARN=''
try:
    _dleft=(datetime.date.fromisoformat(_roles['valid_until'])-today()).days
    if _dleft<=120:
        ROLE_EXPIRY_WARN='!! ROLE REGISTRY EXPIRES IN %d DAYS (%s) - CHAIN WILL FATAL HERE !! '%(_dleft,_roles['valid_until'])
        print('WARNING: data/members/formal_visibility_roles.json expires in %d days (%s). compute_pillars will'
              ' sys.exit(1) on that date and the whole chain stops. No producer script exists —'
              ' refresh the registry for the new Congress (see moneyball-congress-rollover-campaign).'
              %(_dleft,_roles['valid_until']))
except (KeyError,ValueError):
    pass
Ebase=dict(E)
_cd=wb['Committee Data']
COMMITTEE_SEATS={r[0]:str(r[4] or '') for r in _cd.iter_rows(min_row=2,values_only=True) if r[0]}
ROLE_NOMINAL={m:float(((_roles.get('members') or {}).get(m) or {}).get('influence_bonus') or 0.0) for m in members}
ROLE_BONUS={}; ROLE_REASON={}; ROLE_COMMITTEE={}; ROLE_OFFICE={}
for m in members:
    _entry=(_roles.get('members') or {}).get(m) or {}
    ROLE_BONUS[m],ROLE_REASON[m],ROLE_COMMITTEE[m]=IR.eligible_bonus(_entry,COMMITTEE_SEATS.get(m,''))
    ROLE_OFFICE[m]=_entry.get('office') or ''
ROLE_DISPLAY_WEIGHT=dict(ROLE_BONUS)
ROLE_BONUS={m:0.0 for m in members}
E=dict(Ebase)
AB={r:(bm.cell(r,28).value or 0.0) for r in (2,3,4,5)}        # pillar weights (TYPED user dials — always alive)
# GUARD (rank-integrity audit 2026-07-17; diminished sentiment policy 2026-09-03):
# the four cells are load-bearing user constants. Press remains measured and active, but at the
# deliberately diminished policy weight. Fail loudly if a stale workbook restores another mix.
_abv=[float(AB[r] or 0.0) for r in (2,3,4,5)]
_expected=[PILLAR_WEIGHTS[r] for r in (2,3,4,5)]
if any(v<0.0 for v in _abv) or abs(sum(_abv)-1.0)>1e-6 or any(
        abs(actual-expected)>1e-9 for actual,expected in zip(_abv,_expected)):
    print('FATAL: pillar weights do not match the ratified diminished-sentiment policy '
          '(AB2:AB5=%s; expected=%s)'%(_abv,_expected))
    sys.exit(1)
Ov=list(O.values()); Fv=list(F.values())
pmaxC=max(pillar.values()) or 1.0; emax=max(E.values()) or 1.0
score={}; pcts={}; base_score={}
_leg_percentiles=legislative_percentiles(O,mode='legacy')
for m in members:
    legp=_leg_percentiles[m]; pressp=pillar[m]/pmaxC; infp=E[m]/emax; demp=percentrank(Fv,F[m])
    pcts[m]={'leg':legp,'press':round(pressp,9),'inf':round(infp,9),'dem':demp}
    score[m]=AB[2]*legp+AB[3]*pressp+AB[4]*infp+AB[5]*demp
    base_score[m]=score[m]
def _ranks(values):
    order=sorted(members,key=lambda m:-values[m]); out={}
    for i,m in enumerate(order): out[m]=out[order[i-1]] if i and values[m]==values[order[i-1]] else i+1
    return out
rank=_ranks(score); base_rank=_ranks(base_score)

# Nominal weights alone do not describe realized model influence: a heavily weighted
# pillar with little cross-member dispersion can move ranks less than a lighter but
# widely dispersed pillar. Publish both so the UI/audit never mistakes one for the
# other. This is diagnostic only; it does not alter the ratified AB2:AB5 dials.
_pillar_series={
    'leg': [pcts[m]['leg'] for m in members],
    'press': [pcts[m]['press'] for m in members],
    'inf': [pcts[m]['inf'] for m in members],
    'dem': [pcts[m]['dem'] for m in members],
}
_pillar_weights={'leg':float(AB[2]),'press':float(AB[3]),'inf':float(AB[4]),'dem':float(AB[5])}
_weighted_spread={k:_pillar_weights[k]*statistics.pstdev(v) for k,v in _pillar_series.items()}
_spread_total=sum(_weighted_spread.values()) or 1.0
effective_weight_diagnostics={
    'contract':'diagnostic only; realized spread is nominal weight times population standard deviation',
    'nominal_weights':_pillar_weights,
    'normalization':{
        'leg':'Excel PERCENTRANK.INC, truncated to 5 decimal places',
        'press':'pillar divided by cross-member maximum',
        'inf':'committee influence divided by cross-member maximum; formal office is display-only',
        'dem':'Excel PERCENTRANK.INC, truncated to 5 decimal places',
    },
    'pillar_stddev':{k:round(statistics.pstdev(v),9) for k,v in _pillar_series.items()},
    'weighted_spread':{k:round(v,9) for k,v in _weighted_spread.items()},
    'realized_spread_share':{k:round(v/_spread_total,9) for k,v in _weighted_spread.items()},
}
# ---- view coherence: does the sheet's normalized CACHE match Python's normalization of raw? ----
coherent=True
if _live_legislative is not None:
    coherent=all(bm.cell(bmrows[m],15).value is not None and
                 abs(float(bm.cell(bmrows[m],15).value)-O[m])<1e-9 for m in members)
for c in list(COLS)[::4]:
    for m in list(norm[c])[::97]:
        if m in prow and abs((pn.cell(prow[m],c).value or 0.0)-norm[c][m])>1e-9: coherent=False; break
    if not coherent: break
if WRITE:
    if _live_legislative is not None:
        MC.dump_atomic(_live_legislative,os.path.join(B,'data/engine/live_legislative_pillar.json'))
    MC.dump_atomic({'score':score,'rank':rank,'base_score':base_score,'base_rank':base_rank,
        'legislative_pillar':_live_legislative,
        'press_pillar':{m:round(v,9) for m,v in pillar.items()},
        'pcts':pcts,'ab_weights':AB,
        'scoring_policy':scoring_policy(),
        'effective_weight_diagnostics':effective_weight_diagnostics,
        'influence_raw_base':{m:round(Ebase[m],6) for m in members},
        'formal_role_influence_nominal':{m:round(ROLE_NOMINAL[m],6) for m in members},
        'formal_role_influence_bonus':{m:round(ROLE_BONUS[m],6) for m in members},
        'formal_role_display_weight':{m:round(ROLE_DISPLAY_WEIGHT[m],6) for m in members},
        'formal_role_influence_office':ROLE_OFFICE,
        'formal_role_influence_eligibility':ROLE_REASON,
        'formal_role_influence_committee_overlap':ROLE_COMMITTEE,
        'influence_raw_adjusted':{m:round(E[m],6) for m in members},
        'formal_role_influence':{'schema':_roles['schema'],'verified_on':_roles['verified_on'],
                                 'valid_until':_roles['valid_until'],
                                 'listed_party_elites':sum(v>0 for v in ROLE_NOMINAL.values()),
                                 'applied_party_elites':sum(v>0 for v in ROLE_BONUS.values()),
                                 'display_context_party_elites':sum(v>0 for v in ROLE_DISPLAY_WEIGHT.values()),
                                 'committee_overlap_excluded':sum(ROLE_REASON[m]=='committee_leadership_already_in_base' for m in members),
                                 'contract':'formal office is display-only public-exposure context; no office weight modifies Influence or rank'},
        'provenance':'python rank authority (compute_pillars --write); press sentiment is active at a diminished 10% weight and may affect labels; legislative action is 45%; influence = workbook committee score only; formal office is display-only context and does not modify rank'},
        os.path.join(B, 'ranks_python.json'))
    # NOTE the warning PREFIXES this line: run_pipeline truncates the step's last line at 90 chars
    # and this line already exceeds that, so a suffix would be silently cut off and never seen.
    print('%swrote ranks_python.json (rank authority): %d members | %d formal-office contexts display-only | %d committee overlaps noted | workbook view %s'%(
        ROLE_EXPIRY_WARN,len(members),sum(v>0 for v in ROLE_DISPLAY_WEIGHT.values()),
        sum(ROLE_REASON[m]=='committee_leadership_already_in_base' for m in members),
        'coherent' if coherent else 'stale'))
    sys.exit(0)
# ---- audit mode ----
if coherent:
    # Rank authority and workbook implement the same committee formula; office context is not a
    # hidden Python-only overlay.
    smis=[m for m in members if bm.cell(bmrows[m],19).value is None or abs(score[m]-bm.cell(bmrows[m],19).value)>1e-7]
    rmis=[m for m in members if bm.cell(bmrows[m],20).value!=rank[m]]
    print('WORKBOOK FORMULA: score %s | rank %s'%(
        'OK' if not smis else '%d MISMATCH'%len(smis),'EXACT %d/%d'%(len(members)-len(rmis),len(members))))
    if smis or rmis:
        print('WORKBOOK FORMULA: DRIFT DETECTED — a workbook formula no longer matches the spec math (e.g. %s)'%(smis or rmis)[:3]); sys.exit(1)
    print('WORKBOOK FORMULA: CLEAN')
else:
    print('view STALE (expected between pipeline runs and Excel opens) -> verifying the AUTHORITY instead')
try:
    auth=json.load(open(os.path.join(B, 'ranks_python.json')))
    amis=[m for m in members if abs(auth['score'].get(m,-1)-score[m])>1e-12 or auth['rank'].get(m)!=rank[m]]
    if amis:
        print('SHIPPED AUTHORITY PARITY: AUTHORITY STALE — ranks_python.json does not match current inputs (%d members, e.g. %s). Rerun the pipeline.'%(len(amis),amis[:3])); sys.exit(1)
except FileNotFoundError:
    print('SHIPPED AUTHORITY PARITY: AUTHORITY MISSING — run `python fec/engine/compute_pillars.py --write` / the pipeline.'); sys.exit(1)
print('SHIPPED AUTHORITY PARITY: CLEAN — ranks_python.json matches recomputed scores and ranks')
print('PARITY: CLEAN — Python authority excludes unratified formal-office weights%s'%
      (' + exact workbook formula parity' if coherent else ' (workbook view stale)'))
sys.exit(0)
