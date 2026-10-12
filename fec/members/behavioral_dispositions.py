# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""BEHAVIORAL DISPOSITIONS (2026-07-04 nomological fix, user: 'fix it — do whatever it takes').
The pre-registered label-free validation (big5_nomological_prereg.md) INDICTED the lexical Big5
for behavioral prediction (C reversed on participation, E measures broadcast talkativeness,
r_alerting -0.76). The research-correct replacement: measure dispositions FROM BEHAVIOR — the
criteria were better measures than the instruments. Each score maps to the construct ROLE the
receptivity layer needs, using the panel outcomes whose validity is definitional (they ARE the
behavior) plus the interaction channel where the literature grounds it:

  cooperation_z  bipartisanship + cosponsor breadth + cosponsor count      [A-role: the ONE corner
                 lexical A validated on (+.11 FDR-sig) — measured directly instead of inferred]
  diligence_z    roll-call participation (votes_cast, z-log within chamber) [C-role: RKH's
                 strongest C criterion; our lexical C REVERSED on it]
  dominance_z    interaction channel (verbal output + floor-holding Gini + floor defense,
                 per-program z) + committee leadership                      [E-dominance role:
                 turn statistics are the replicated dominance signal (Rienks&Heylen; Valente)]
  visibility_z   broadcast engagement (interview count + program breadth)   [E-sociability role:
                 'takes meetings' proxied by demonstrably taking interviews]
  breadth_z      legislative portfolio breadth (# distinct issue sections   [O-role: engages
                 with any sponsor/cosponsor role in the bills archive)       across novel domains]

All z within chamber where the raw scale differs by chamber. Missing data -> None (consumers fall
back to neutral 0 — never an invented disposition). Output: behavioral_dispositions.json."""
import csv, glob, hashlib, json, math, os, sys, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import openpyxl
B = MB_ROOT
C = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}
PANEL = json.load(open(B + r'\behavior_panel.json'))['members']
chamber = {m: (C.get(m) or {}).get('chamber') or '?' for m in C}

def zscore(vals, log=False):
    """vals {m: raw} -> z within chamber (pooled if chamber unknown)."""
    out = {}
    for ch in ('House', 'Senate'):
        ms = [m for m in vals if chamber.get(m) == ch and vals[m] is not None]
        if len(ms) < 5: continue
        xs = {m: (math.log1p(max(0.0, float(vals[m]))) if log else float(vals[m])) for m in ms}
        mu = sum(xs.values()) / len(xs)
        sd = (sum((v - mu) ** 2 for v in xs.values()) / max(1, len(xs) - 1)) ** 0.5 or 1.0
        for m in ms: out[m] = (xs[m] - mu) / sd
    return out

def mean_z(zdicts, need=1):
    """average available z-scores per member; None if fewer than `need` components."""
    out = {}
    for m in C:
        vs = [z[m] for z in zdicts if m in z]
        if len(vs) >= need: out[m] = sum(vs) / len(vs)
    return out

# cooperation: bipartisanship (raw ratio) + cosponsor degree + cosponsor count
coop = mean_z([zscore({m: d.get('bipartisanship') for m, d in PANEL.items() if d.get('bipartisanship') is not None}),
               zscore({m: d.get('cosponsor_degree') for m, d in PANEL.items() if d.get('cosponsor_degree') is not None}, log=True),
               zscore({m: d.get('n_cosponsored') for m, d in PANEL.items() if d.get('n_cosponsored') is not None}, log=True)], need=2)

# diligence: mean of the two independently observed, verified legislative-execution components
# C-behaviors, need=1, each z within its covered population; missing components never penalize.
#   1. roll-call participation (original component; full-term guard + clip as before)
#   2. LEP follow-through: committee-action+ stage events per bill introduced (data/members/lep_criterion.json;
#      static CEL data through the 118th, so reading last cycle's diagnostic file is exact)
# The former CHRG hearing-participation component is quarantined: the legacy collector contains
# verified witness/wrong-chamber attribution errors. It may return only after a clean re-ingest and
# independent attribution audit.
_vc = {m: d.get('votes_cast') for m, d in PANEL.items() if d.get('votes_cast')}
_med = {}
for _ch in ('House', 'Senate'):
    _vs = sorted(v for m, v in _vc.items() if chamber.get(m) == _ch)
    if _vs: _med[_ch] = _vs[len(_vs) // 2]
_dil_votes = zscore({m: v for m, v in _vc.items() if v >= 0.25 * _med.get(chamber.get(m), 1e9)}, log=True)
_dil_votes = {m: max(-3.0, min(3.0, v)) for m, v in _dil_votes.items()}
_dil_parts = [_dil_votes]
_lcp = B + r'\data\members\lep_criterion.json'
_ftv = {}
if os.path.exists(_lcp):
    _ftv = {m: d.get('followthrough') for m, d in json.load(open(_lcp))['per_member'].items()
            if d.get('followthrough') is not None}
    if _ftv: _dil_parts.append(zscore(_ftv))
dil = mean_z(_dil_parts, need=1)
dil = {m: max(-3.0, min(3.0, v)) for m, v in dil.items()}

# dominance: interaction channel + committee leadership
dom_parts = []
ixp = B + r'\data\lobbying\interaction_features.json'
if os.path.exists(ixp):
    IX = {m: d for m, d in json.load(open(ixp))['members'].items() if d.get('turns', 0) >= 30}
    dom_parts.append({m: (d['wpt_mean'] + d['gini'] + d['defense']) / 3.0 for m, d in IX.items()})
dom_parts.append(zscore({m: d.get('committee_leader', 0) for m, d in PANEL.items()}))
dom = mean_z(dom_parts, need=1)

# reactivity (N-role, round-3 2026-07-09, user-ratified build — "can you safely create it? proceed"):
# interruption/trail-off proneness in LIVE interviews = the cutoff share from interaction_features.py
# (per-program z, >=12 turns/program, >=8 members/program, turn-weighted) — the Byrne
# 'pressure-reactivity' behavior, Celli-Rossi round-3 survivor. Probe record (methods §19):
# orthogonal to all five dispositions (worst |r|=.15 dom), party -.09 / gender +.02 clean, NOT
# long-windedness (+.07 wpt) or floor-defense (-.09); program-split-half SB=.43 (real but modest) ->
# GRADED SHRINKAGE sh=sqrt(min(1, turns/REACT_TURNS)) (the Ramey token-shrinkage pattern; default
# 150, env dial) so thin-coverage members shrink toward neutral instead of carrying noise. Members
# with <30 turns -> None (neutral downstream). Live speech = unstaffable; interaction STRUCTURE,
# neither text content nor audio acoustics. Note burst was PROBED AND REJECTED (split-half SB=.04 =
# situational noise) — do not add it back.
REACT_TURNS = float(os.environ.get('REACT_TURNS', '150'))
rea = {}
if os.path.exists(ixp):
    for m, d in json.load(open(ixp))['members'].items():
        t = d.get('turns', 0)
        if t >= 30 and d.get('cutoff') is not None:
            rea[m] = d['cutoff'] * math.sqrt(min(1.0, t / REACT_TURNS))

# visibility: broadcast engagement. INTERIM GUARD (until the nickname-fixed re-ingest lands):
# members with <3 attributed interviews are indistinguishable from alias-dropped members
# (Bernie/Bernard-class) — neutral (None), never penalized for a resolver gap.
vis = mean_z([zscore({m: d.get('n_interviews') for m, d in PANEL.items() if (d.get('n_interviews') or 0) >= 3}, log=True),
              zscore({m: d.get('n_interview_programs') for m, d in PANEL.items() if (d.get('n_interviews') or 0) >= 3}, log=True)], need=1)

# breadth: distinct issue sections with any legislative role (bills archive)
bp = max(glob.glob(B + r'\congress_bills_archive_*.xlsx'), key=os.path.getmtime)
ws = openpyxl.load_workbook(bp, read_only=True)['All Bills']
rows = ws.iter_rows(min_row=1, values_only=True)
hdr = [str(h or '') for h in next(rows)]
iS, iM, iN, iCg = hdr.index('Section'), hdr.index('Member'), hdr.index('Name'), hdr.index('Congress')
# CU_IDENTITY_FIX: name-keyed archive (Sen. Menendez's Senate bills -> Rep. M001226); shared check.
from members.member_identity import load_billstatus, archive_row_unverified
_billstatus = load_billstatus(B); identity_rejected = collections.Counter()
secs = collections.defaultdict(set)
for r in rows:
    if r[iM] in C and r[iS]:
        if archive_row_unverified(_billstatus, r[iN], r[iCg], C[r[iM]].get('bioguide')):
            identity_rejected[r[iM]] += 1; continue
        secs[r[iM]].add(str(r[iS]))
print('identity rows unverified by BILLSTATUS (CU_IDENTITY_FIX): %d' % sum(identity_rejected.values()), dict(identity_rejected))
bre = zscore({m: len(s) for m, s in secs.items()}, log=False)

from moneyball_core import dump_atomic
out = {}
for m in C:
    d = {}
    for k, src in (('cooperation_z', coop), ('diligence_z', dil), ('dominance_z', dom),
                   ('visibility_z', vis), ('breadth_z', bre), ('reactivity_z', rea)):
        if m in src: d[k] = round(src[m], 3)
    if d: out[m] = d
cov = {k: sum(1 for m in out if k in out[m]) for k in
       ('cooperation_z', 'diligence_z', 'dominance_z', 'visibility_z', 'breadth_z', 'reactivity_z')}
if cov['diligence_z'] < 525 or any(not math.isfinite(row['diligence_z']) or not -3 <= row['diligence_z'] <= 3
                                  for row in out.values() if row.get('diligence_z') is not None):
    raise ValueError('diligence integrity gate failed')

def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()

_inputs = {'behavior_panel.json': _sha256(B + r'\behavior_panel.json')}
if os.path.exists(_lcp): _inputs['data/members/lep_criterion.json'] = _sha256(_lcp)
dump_atomic({'schema': 'behavioral-dispositions-v2', 'status': 'PASS',
             'members': out, 'coverage': cov,
             'inputs_sha256': _inputs,
             'integrity': {'diligence': {
                 'roll_call_member_n': len(_dil_votes),
                 'lep_followthrough_member_n': len(_ftv),
                 'both_components_member_n': len(set(_dil_votes) & set(_ftv)),
                 'combined_member_n': len(dil),
                 'component_rule': 'mean of available within-chamber z scores; need at least one',
                 'missing_components_never_penalized': True,
                 'legacy_chrg_enters_diligence': False,
                 'output_clip': [-3.0, 3.0],
             }},
             'method': 'dispositions measured from behavior (nomological fix 2026-07-04; diligence verified 2-component '
                       'run-4w; reactivity round-3 2026-07-09): cooperation=bipartisanship+cosponsor embedding; '
                       'diligence=roll-call participation+LEP follow-through (mean of available; legacy CHRG '
                       'hearing counts quarantined after attribution-integrity failure); dominance=interaction '
                       'turn-stats+leadership; visibility='
                       'broadcast engagement; breadth=legislative portfolio sections; reactivity=live-interview '
                       'cutoff proneness (per-program z, sqrt(turns/%g) shrinkage, N-role); z within chamber; '
                       'missing -> neutral downstream' % REACT_TURNS},
            B + r'\behavioral_dispositions.json')
print('behavioral dispositions: %d members | coverage %s' % (len(out), cov))
for k in cov:
    vals = sorted(((out[m][k], m) for m in out if k in out[m]), reverse=True)
    print('  %-14s hi: %-40s lo: %s' % (k.replace('_z', ''),
          ', '.join('%s %+0.1f' % (m, v) for v, m in vals[:2]),
          ', '.join('%s %+0.1f' % (m, v) for v, m in vals[-2:])))
