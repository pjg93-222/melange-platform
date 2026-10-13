# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from paths import ROOT as MB_ROOT, LOCAL_MELANGE   # centralized root (arch review 2026-07-02)
"""COALITION PRICING + THE "IGNORED" DIMENSION  (built 2026-08-03).

WHAT THIS IS
  The coalition builder ranks candidates on how much NEW REACH they add. Reach says nothing
  about what a member COSTS to reach or how crowded their door already is, so the ranking
  systematically rewards members who are already saturated with finance lobbying. This module
  emits the missing half: a per-member ACCESS PRICE and a majority-neutral CAPABILITY score,
  which together give the builder's "Underpriced" basis.

  Scope: decision-layer surface, like dashboard_legislative_workbench.py. It does NOT feed the
  calibrated Python allocation chain (optimize.py / budget_frontier.py / per_topic_coalition.py)
  and changes no rank in ranks_python.json.

THE OWNER'S PROBLEM, in his words (2026-08-03):
  "lets also control for that irrelevance, again people who are good but ignored for a variety
   of reasons, gwen moore is an example of this"
  "its not that Emmer should be NOWHERE, is just that there is such a strong bias to already
   established members and chairs, again the guardrails are failing"

RATIFIED CONSTANTS - all four ratified by Paul on 2026-08-03. Do not change without a new ruling.
  SEN_K     = 0.35   seniority cost term
  LOWCONF_K = 1.10   low-confidence cost multiplier
  EB_K      = 2.0    empirical-Bayes shrinkage prior (member-years) on the contest rate
  W_C = W_X = 0.5    cost and contest penalty weights

MEASURED BASIS FOR THE CAPABILITY DEFINITION (this file's one non-handed-down choice; see the
report of 2026-08-03 for the full tables):
  Classic Legislative Effectiveness (lesclassic) is PASSAGE-weighted, and passage requires
  majority control. Measured on the CEL panel 115-118 with CONTEMPORANEOUS majority status
  (n=2,202): structure alone explains R2=0.213 of lesclassic; majority-party members average
  1.365 vs 0.595 for the minority - a 2.29x ratio, Cohen d=0.79, rising to 2.87x in the 118th
  House. Majority status ALONE explains R2=0.136. So lesclassic encodes who holds the gavel at
  least as much as who is any good, and it must not be used as a capability input.
  (Testing it against 119th-Congress majority status instead gives a misleading d=0.11, because
  lesclassic is the 117-118 mean and the majority flipped between those Congresses. The
  contemporaneous specification is the correct one.)

  Components were screened on two bars - structural R2 and the majority-party Cohen d - and only
  signals a member can produce WITHOUT controlling the floor were kept:
      KEPT      do_z (CU legislative action)   structR2 0.074   maj d +0.32
                intro rate per congress served structR2 0.330   maj d -0.29
                diligence_z                    structR2 0.048   maj d +0.42
                breadth_z                      structR2 0.056   maj d -0.40
      DROPPED   followthrough                  structR2 0.106   maj d +0.62
                  -> "committee action and beyond" requires the MAJORITY to schedule the markup,
                     so it re-imports the exact bias being removed. This is the single most
                     important exclusion in the file.
      DROPPED   raw bill counts (structR2 0.33, dominated by chamber and tenure) - replaced by a
                per-congress-served rate.
      DROPPED   committee_score (positional by construction) and cosponsorship degree (that IS
                reach; the builder's own objective already prices it, so including it would
                double-count).
  Resulting capability: structural R2 = 0.049, majority Cohen d = +0.027 - i.e. essentially
  orthogonal to who holds power, which is the whole point.

ATTENTION WINDOW: the contest measure is distinct lobbying organisations from the LDA rebuild,
  fullscope/strict. It is taken on the CURRENT Congress (2025-2026), not pooled 2021-2026.
  Pooling inverts the sign - Democrats held the majority in 2021-22 and dominate the pooled
  union, giving majority Cohen d = -0.348, whereas the current window gives +0.571. The builder
  advises on the present, so the present window is the correct one.
"""
import json, sys, os, csv, math, glob, re, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import moneyball_core as MBC
from moneyball_core import dump_atomic

B = MB_ROOT
LA = LOCAL_MELANGE
LDA = os.path.join(LA, 'lda_rebuild', 'derived')
SNAPDIR = os.path.join(LA, 'lda_rebuild', 'raw', 'committee_snapshots')

# ---------------------------------------------------------------- ratified constants
SEN_K = 0.35        # seniority cost term                      (Paul ratified 2026-08-03)
LOWCONF_K = 1.10    # low-confidence cost multiplier           (Paul ratified 2026-08-03)
EB_K = 2.0          # member-years shrinkage prior on contest  (Paul ratified 2026-08-03)
W_C = 0.5           # cost penalty weight                      (Paul ratified 2026-08-03)
W_X = 0.5           # contest penalty weight                   (Paul ratified 2026-08-03)

# Role access costs. '-' / 'v' / 'r' mirror dashboard_legislative_workbench.ROLE_PRIORS; the
# chair figure is the calibrated one - see CHAIR_ACCESS_COST there for the working.
ROLE_ACCESS_COST = {'-': 1.00, 'v': 1.12, 'r': 1.25, 'c': 1.75}

# The relevant-jurisdiction pool (the irrelevance gate). Full committees only.
# Small Business is included per Paul's 2026-07-04 directive (SBA lending is CU-relevant), the
# same rule make_member_desk.py already applies.
JURISDICTION = {'HSBA': 'House Financial Services', 'HSWM': 'House Ways & Means',
                'HSSM': 'House Small Business', 'SSBK': 'Senate Banking',
                'SSFI': 'Senate Finance', 'SSSB': 'Senate Small Business'}
CONTEST_YEARS = (2025, 2026)          # current Congress; see ATTENTION WINDOW above
INTRO_WINDOW = 4                      # congresses in the LEP 115-118 pooling window
CHAIR_TITLES = ('chairman', 'chair', 'chairwoman', 'cochairman')


def _load(name, default=None):
    p = os.path.join(B, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding='utf-8') as fh:
        return json.load(fh)


def _parse_committee_yaml(path):
    """Minimal reader for the pinned committee snapshot's fixed shape."""
    out, cur, rec = collections.OrderedDict(), None, None
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            line = line.rstrip('\n')
            if not line.strip():
                continue
            m = re.match(r'^([A-Za-z0-9_]+):\s*$', line)
            if m:
                cur = m.group(1); out[cur] = []; rec = None; continue
            m = re.match(r'^-\s+(\w+):\s*(.*)$', line)
            if m:
                rec = {m.group(1): m.group(2).strip()}; out[cur].append(rec); continue
            m = re.match(r'^\s+(\w+):\s*(.*)$', line)
            if m and rec is not None:
                rec[m.group(1)] = m.group(2).strip()
    return out


def pct_rank(values):
    """Tie-aware midrank percentile in [0,1] - matches CENTRALITY_POLICY's rank_method."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        p = ((i + j) / 2.0) / max(1, len(values) - 1)
        for t in range(i, j + 1):
            out[order[t]] = p
        i = j + 1
    return out


def _z_within(values, groups):
    """z within chamber - the normalisation say_do_gap.py already uses."""
    out = [None] * len(values)
    for g in set(groups):
        idx = [i for i in range(len(values)) if groups[i] == g and values[i] is not None]
        if len(idx) < 2:
            continue
        mu = sum(values[i] for i in idx) / len(idx)
        sd = (sum((values[i] - mu) ** 2 for i in idx) / len(idx)) ** 0.5 or 1.0
        for i in idx:
            out[i] = (values[i] - mu) / sd
    return out


def build():
    cong = _load('data/engine/congestion.json') or []
    C = {m['member']: m for m in cong}
    members = sorted(C)
    bg = {n: (C[n].get('bioguide') or '') for n in members}

    desk = _load('member_desk_bundle.json') or {}
    DESK = {r['name']: r for r in desk.get('members', [])}
    sd = (_load('say_do_gap.json') or {}).get('members', {})
    lep = (_load('data/members/lep_criterion.json') or {}).get('per_member', {})
    bd = (_load('behavioral_dispositions.json') or {}).get('members', {})
    rcj = _load('member_receptivity.json') or {}
    rc = rcj.get('members', rcj)

    # ---- pinned 119th committee snapshot: jurisdiction, chair/ranking, majority ----
    snaps = sorted(glob.glob(os.path.join(SNAPDIR, '*119*.yaml')))
    seats = collections.defaultdict(list)
    if snaps:
        for code, roster in _parse_committee_yaml(snaps[-1]).items():
            for row in roster:
                b = row.get('bioguide')
                if not b:
                    continue
                seats[b].append({'code': code, 'full': len(code) <= 5,
                                 'title': (row.get('title') or '').strip().lower()})
    else:
        print('WARNING: no pinned 119th committee snapshot found under %s' % SNAPDIR)

    # ---- seniority: distinct congresses served (Voteview HSall) ----
    terms = collections.defaultdict(set)
    hs = os.path.join(B, 'votes', 'HSall_members.csv')
    if os.path.exists(hs):
        want = set(v for v in bg.values() if v)
        with open(hs, encoding='utf-8-sig', errors='replace', newline='') as fh:
            for row in csv.DictReader(fh):
                b = row.get('bioguide_id')
                if b in want:
                    try:
                        terms[b].add(int(row['congress']))
                    except (TypeError, ValueError):
                        pass

    # ---- contest: distinct lobbying orgs on the CURRENT Congress ----
    orgs, oyears = collections.defaultdict(set), collections.defaultdict(set)
    for y in CONTEST_YEARS:
        p = os.path.join(LDA, 'our_network_fullscope_%d_strict.csv' % y)
        if not os.path.exists(p):
            print('WARNING: missing %s - contest will understate' % p)
            continue
        with open(p, encoding='utf-8-sig', errors='replace', newline='') as fh:
            for row in csv.DictReader(fh):
                b, lid = row.get('bioguide_id'), row.get('lob_id')
                if b and lid:
                    orgs[b].add(lid); oyears[b].add(y)

    # ---- assemble per-member raw inputs ----
    rec = {}
    for n in members:
        b = bg[n]
        st = seats.get(b, [])
        full = [s for s in st if s['full']]
        jur = [s for s in full if s['code'] in JURISDICTION]
        chair = any(s['title'] in CHAIR_TITLES for s in full)
        rank_ = any('ranking' in s['title'] for s in full)
        vice = any('vice' in s['title'] for s in full)
        sen = len(terms.get(b, ())) or 1
        lp = lep.get(n) or {}
        bills = lp.get('bills_115_118')
        served = max(1, min(INTRO_WINDOW, sen))
        rec[n] = {
            'bioguide': b,
            'chamber': (DESK.get(n) or {}).get('chamber') or C[n].get('chamber') or 'House',
            'party': (DESK.get(n) or {}).get('party') or '',
            'seniority': sen,
            'in_jurisdiction': bool(jur),
            'jurisdiction': sorted(JURISDICTION[s['code']] for s in jur),
            'role_code': 'c' if chair else ('r' if rank_ else ('v' if vice else '-')),
            'chair_of_jurisdiction': any(s['title'] in CHAIR_TITLES for s in jur),
            'lowconf': bool((rc.get(n) or {}).get('lowconf')),
            'do_z': (sd.get(n) or {}).get('do_z'),
            'intro_rate': (math.log1p(bills / served) if bills is not None else None),
            'diligence_z': (bd.get(n) or {}).get('diligence_z'),
            'breadth_z': (bd.get(n) or {}).get('breadth_z'),
            'contest': len(orgs.get(b, ())),
            'contest_years': len(oyears.get(b, ())),
            'lesclassic': lp.get('lesclassic'),
        }

    cham = [rec[n]['chamber'] for n in members]
    comps = ['do_z', 'intro_rate', 'diligence_z', 'breadth_z']
    zcols = []
    for c in comps:
        raw = [rec[n][c] for n in members]
        z = _z_within(raw, cham)
        present = [v for v in z if v is not None]
        fill = sorted(present)[len(present) // 2] if present else 0.0
        zcols.append([(v if v is not None else fill) for v in z])
    capability = [sum(col[i] for col in zcols) / len(zcols) for i in range(len(members))]
    cap_pct = pct_rank(capability)

    # EMPIRICAL-BAYES contest rate: orgs per member-year, shrunk toward zero by EB_K prior
    # member-years. A member observed in only one year with a handful of filings is not
    # evidence of a quiet door; EB_K stops that thin denominator from inventing a bargain.
    rate = [rec[n]['contest'] / (rec[n]['contest_years'] + EB_K) for n in members]
    att_pct = pct_rank(rate)

    sen_max = max(rec[n]['seniority'] for n in members) or 1
    out = {}
    for i, n in enumerate(members):
        r = rec[n]
        cost = (ROLE_ACCESS_COST.get(r['role_code'], 1.0)
                * (1.0 + SEN_K * (r['seniority'] / sen_max))
                * (LOWCONF_K if r['lowconf'] else 1.0))
        # PRICE keeps cost on its RAW scale on purpose. An earlier draft percentile-ranked the
        # cost first; because chairs already sit at the top of the cost ordering, that made the
        # chair constant COMPLETELY INERT - moving it from 1.40 to 2.50 changed nothing at all.
        # That is the same "guardrail present but not biting" failure this work exists to fix.
        price = 1.0 + W_C * (cost - 1.0) + W_X * att_pct[i]
        ign = cap_pct[i] - att_pct[i]
        out[n] = {
            **{k: r[k] for k in ('bioguide', 'chamber', 'party', 'seniority', 'in_jurisdiction',
                                 'jurisdiction', 'role_code', 'chair_of_jurisdiction', 'lowconf',
                                 'contest', 'contest_years', 'lesclassic')},
            'capability': round(capability[i], 4),
            'capability_pct': round(cap_pct[i], 4),
            'contest_rate_eb': round(rate[i], 4),
            'attention_pct': round(att_pct[i], 4),
            'access_cost': round(cost, 4),
            'price': round(price, 4),
            'ignored': round(ign, 4),
            'quadrant': ('ignored' if cap_pct[i] >= .5 and att_pct[i] < .5 else
                         'courted' if cap_pct[i] >= .5 else
                         'irrelevant' if att_pct[i] < .5 else 'over-attended'),
        }
    return out, members


def main():
    out, members = build()
    n = len(members)
    q = collections.Counter(v['quadrant'] for v in out.values())
    print('coalition pricing: %d members' % n)
    print('  quadrants: %s' % dict(q))
    jur = [m for m in members if out[m]['in_jurisdiction']]
    print('  jurisdiction pool: %d | chairs of jurisdiction: %s'
          % (len(jur), [m for m in jur if out[m]['chair_of_jurisdiction']]))
    lc = sum(1 for m in members if out[m]['lowconf'])
    print('  lowconf members: %d  (LOWCONF_K is INERT on this vintage if 0 - reported, not hidden)' % lc)
    board = sorted(members, key=lambda m: -(out[m]['capability_pct'] / out[m]['price']))
    print('\n  top 12 UNDERPRICED (capability / price), jurisdiction pool only:')
    for m in [x for x in board if out[x]['in_jurisdiction']][:12]:
        v = out[m]
        print('    %-24s %-11s cap p%.2f  attn p%.2f  price %.2f  ignored %+.3f'
              % (m, v['party'], v['capability_pct'], v['attention_pct'], v['price'], v['ignored']))
    mo = out.get('Gwen Moore')
    if mo:
        print('\n  Gwen Moore: cap p%.3f | attention p%.3f | ignored %+.3f | quadrant %s | jurisdiction %s'
              % (mo['capability_pct'], mo['attention_pct'], mo['ignored'], mo['quadrant'], mo['jurisdiction']))
    dump_atomic({
        'schema': 'coalition-pricing-v1',
        'scope': 'interactive dashboard only - does NOT feed the calibrated allocation chain',
        'production_engine_changed': False,
        'ratified': {'date': '2026-08-03', 'by': 'Paul',
                     'SEN_K': SEN_K, 'LOWCONF_K': LOWCONF_K, 'EB_K': EB_K, 'W_C': W_C, 'W_X': W_X,
                     'ROLE_ACCESS_COST': dict(ROLE_ACCESS_COST)},
        'capability_components': ['do_z', 'intro_rate_per_congress', 'diligence_z', 'breadth_z'],
        'capability_excluded': {
            'lesclassic': 'passage-weighted; CEL panel 115-118 contemporaneous majority d=0.79, R2=0.213',
            'followthrough': 'committee action requires majority scheduling; majority d=+0.62',
            'raw_bill_counts': 'structural R2=0.33 (chamber+tenure); replaced by per-congress rate',
            'committee_score': 'positional by construction',
            'cosponsorship_degree': 'that is reach - the builder objective already prices it'},
        'contest_window': list(CONTEST_YEARS),
        'contest_source': 'LDA rebuild our_network_fullscope_{year}_strict.csv (distinct lob_id)',
        'jurisdiction': dict(JURISDICTION),
        'members': out,
    }, os.path.join(B, 'data', 'lobbying', 'coalition_pricing.json'))
    print('\nwrote coalition_pricing.json (%d members)' % n)


if __name__ == '__main__':
    main()
