#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-area primacy grade (D-031): the member's per-year, per-policy-area rate of
signed legislative action, blended with press, normalised WITHIN CHAMBER.

Producer, not a library: it reads published artifacts and writes exactly one file,
``data/engine/per_area_grade.json``. It imports no surface and no board module, so
importing it can never move a rank.

WHAT IT IS. For each member m and each of the 18 IMP policy areas a:

    leg(m,a)   = SUM over deduped acts of role_w[role] * sign * (1 + k * cu_cell)
                * beneficiary_class_magnitude
               + w_vote * signed_vote_sum(m,a)
    rate(m,a)  = leg(m,a) / years_served(m)
    n_leg(a)   = within-chamber percentile of rate(.,a)
    press(m)   = press_proportion(m) if press_items(m) >= press_floor else 0.0
    n_press    = within-chamber percentile of press
    grade(m)   = SUM_a IMP[a] * (0.80 * n_leg(a)[m] + 0.20 * n_press[m])
    grade_pct  = within-chamber percentile of grade

The two 0.80s are unrelated and must not be collapsed: the role weights happen to
sum to 0.80 (0.40 + 0.25 + 0.15), and the grade's legislation share is separately
0.80 against a press share of 0.20. There is no congress-decay term in this lane.

WHO CONSUMES IT. ``fec/tier_util.py`` under ``PRIMACY_BOARD=per_area_v1``, which
seats on ``factor_ranked * (0.80 + 0.20 * grade_percentile)``. ``grade_percentile``
and ``chamber`` are the only two keys the board reads; everything else in the
artifact is provenance for the receipt.

PROVENANCE, AND WHY IT IS IN THE FILE. Every input is hashed into ``inputs`` and
every selection count into ``counts``, because the grade is derived from nine
artifacts that move on different chain steps: a silent input change would
otherwise reseat the board with no trace. ``as_of`` is a policy input (it closes
the service-date window), so it is recorded and ``--as-of`` overrides it; the
artifact carries no wall-clock stamp, so two runs of the same day are byte-identical.

RATIFIED VALUES (Phase 2 authorization, 2026-09-15/16): R1 the product form
``P * (0.80 + 0.20 * grade%)`` lives in tier_util, not here; R2 ``w_vote = 0.4``
(exactly one sponsorship per vote at the W1 sponsor weight 0.40); R3 press floor 5
scored items; D-027 within-chamber normalisation. ``k = 0.5`` and the W1 role
weights are the prototype's, fixed by the brief.

2026-09-20: CLASS_DOCK_PRESS_GRADE_ALL is user-ratified. Beneficiary class
magnitudes now qualify legislative grade contributions, and press is the
signed CU-weighted intensity share of the entire adopted classified corpus.
The five-observed-item gate, votes, tensor bonus and within-chamber seating stay.
See fec/docs/primacy_diagnostic_20260920.md for provenance and corpus limits.

REVERT: ``PRIMACY_METHOD=legacy`` followed by regeneration restores the former
grade and uncertainty dock. ``PRIMACY_BOARD=factor`` only changes seating.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date

FEC_BOOTSTRAP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if FEC_BOOTSTRAP not in sys.path:
    sys.path.insert(0, FEC_BOOTSTRAP)

from paths import ROOT as MB_ROOT
import roster as roster_module  # (several functions here bind a local named roster)
from moneyball_core import IMP, dump_atomic
from engine.primacy_method import DIAGNOSTIC, METHOD, BENEFICIARY_W
from engine import primacy_press

SCHEMA = 'per-area-grade-v1'
OUT_PATH = 'data/engine/per_area_grade.json'

# The 18 ABB26 policy areas, in a stable order (moneyball_core.IMP is the single
# source of the weights; sorting makes the artifact key order deterministic).
CATEGORIES = tuple(sorted(IMP))

# Role weights: variant W1 of the point-7 sweep, ratified with the per-area design.
ROLE_W = {'sponsor': 0.40, 'original-cosponsor': 0.25, 'cosponsor': 0.15}
# Dedupe priority when the same (member, area, bill, congress) appears twice.
ROLE_PRIORITY = {'other': 0, 'cosponsor': 1, 'original-cosponsor': 2, 'sponsor': 3}

W_VOTE = 0.4          # R2: one sponsorship-equivalent per signed committee/floor vote
PRESS_FLOOR = 5       # R3: fewer than this many scored press+social items -> press 0
K_TENSOR = 0.5        # CU relevance x valence cell, same k as primacy_factors' tensor
LEG_SHARE = 0.80
PRESS_SHARE = 0.20
YEARS_MIN, YEARS_MAX = 0.5, 6.0
WINDOW_CONGRESSES = (117, 118, 119)
WINDOW_LO = date(2021, 1, 3)          # 117th Congress start
ACCEPTED_DISPOSITIONS = ('selected', 'neutral-registry', 'neutral-official-evidence')
NORMALIZATION = 'within_chamber'

# congress-legislators snapshot: the installed commit-pinned one (roster.snapshot_dir), as a repo-relative path.  A new
# snapshot from the roster refresh changes this input (and so can change per-area grades).
REFERENCE_DIR = os.path.relpath(roster_module.snapshot_dir(), MB_ROOT)

INPUT_FILES = (
    'data/engine/congestion.json',
    'data/engine/primacy_factors.json',
    'ranks_python.json',
    'legislation_routing_audit.json',
    'data/lobbying/industry_axes.ndjson',
    'vote_category_lean.json',
    'press_stance_final.csv',
    'data/stance/tweet_stance_final.csv',
    'data/stance/bluesky_stance_final.csv',
    REFERENCE_DIR.replace(os.sep, '/') + '/legislators-current.yaml',
    REFERENCE_DIR.replace(os.sep, '/') + '/legislators-historical.yaml',
)
if DIAGNOSTIC:
    INPUT_FILES += ('bill_beneficiary_tags.ndjson',
                    'data/legislation/sentinel_admissions.json',
                    primacy_press.LEDGER, primacy_press.RECEIPT)


# ---------------------------------------------------------------------------
# Percentile — the repo convention, re-derived from
# fec/engine/effectiveness_integration.py:30-38 (share strictly below the
# first-tie index over n-1, truncated to 5 decimals). Copied rather than
# imported so this producer stays free of the shadow-comparison machinery;
# fec/tests/engine/test_per_area_board.py pins the two against each other.
# ---------------------------------------------------------------------------
def trunc5(value):
    """Truncate toward zero at 5 decimals, with a 1e-7 guard so a value that is
    mathematically k/1e5 is not pushed a digit down by binary representation."""
    scaled = value * 100000.0
    eps = 1e-7
    floored = math.floor(scaled + eps) if scaled >= 0 else math.ceil(scaled - eps)
    return floored / 100000.0


def percentrank(values):
    """values: {key: float} -> {key: percentile}. Ties share the first index."""
    count = len(values)
    if count == 0:
        return {}
    if count == 1:
        return {key: 1.0 for key in values}
    ordered = sorted(values.values())
    first = {}
    for index, value in enumerate(ordered):
        first.setdefault(value, index)
    return {key: trunc5(first[value] / (count - 1)) for key, value in values.items()}


def within_chamber_percentile(values, chamber_of):
    """D-027: House and Senate are ranked against themselves, never against
    each other. A member absent from ``chamber_of`` is a caller bug, not a
    default-to-House case, so the KeyError is deliberate."""
    out = {}
    for chamber in sorted({chamber_of[key] for key in values}):
        subset = {key: value for key, value in values.items()
                  if chamber_of[key] == chamber}
        out.update(percentrank(subset))
    return out


def canonical_role(role):
    """fec/bill_rules.py:222-230, copied (this producer imports no rules module)."""
    value = str(role or '').strip().lower()
    if 'original' in value:
        return 'original-cosponsor'
    if 'cosponsor' in value or 'co-spons' in value:
        return 'cosponsor'
    if 'sponsor' in value:
        return 'sponsor'
    return value or 'other'


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Loaders. Plain JSON/CSV/YAML-subset reads of published artifacts; each names
# the live producer whose selection it reproduces.
# ---------------------------------------------------------------------------
def load_roster(path):
    """data/engine/congestion.json -- the display roster, and the ONLY chamber
    source this lane uses (build_field_action_evidence.py:113-122 uses the same)."""
    with open(path, encoding='utf-8') as handle:
        rows = json.load(handle)
    roster = {}
    for row in rows:
        chamber = row['chamber']
        if chamber not in ('House', 'Senate'):
            raise ValueError('congestion.json: unknown chamber %r for %r'
                             % (chamber, row.get('member')))
        roster[row['member']] = {'bioguide': row['bioguide'], 'chamber': chamber}
    return roster


def load_primacy_members(path):
    """primacy_factors.json membership only -- the factor itself is the board's
    input, not the grade's, and is read by tier_util."""
    with open(path, encoding='utf-8') as handle:
        doc = json.load(handle)
    return set(doc.get('members') or {})


def load_pct_members(path):
    """ranks_python.json 'pcts', restricted to entries carrying a usable 'leg'
    percentile -- exactly tier_util.primacy_scores' membership gate (:411-415),
    so the artifact covers every member the board can rank."""
    with open(path, encoding='utf-8') as handle:
        doc = json.load(handle)
    out = set()
    for member, values in (doc.get('pcts') or {}).items():
        try:
            float(values['leg'])
        except (KeyError, TypeError, ValueError):
            continue
        out.add(member)
    return out


def load_acts(path, stats):
    """legislation_routing_audit.json -- the member/bill action source carrying
    role, congress and policy area. Accepted dispositions and the
    (member, area, measure, congress) dedupe keeping the strongest role follow
    build_field_action_evidence.py:165-193."""
    with open(path, encoding='utf-8') as handle:
        doc = json.load(handle)
    if doc.get('schema') != 'legislation-routing-audit-v1':
        raise ValueError('legislation_routing_audit.json: unexpected schema %r'
                         % doc.get('schema'))
    best = {}
    for row in doc['rows']:
        stats['rows'] += 1
        if row['disposition'] not in ACCEPTED_DISPOSITIONS:
            continue
        stats['accepted'] += 1
        congress = int(row['congress'])
        if congress not in WINDOW_CONGRESSES:
            stats['outside_window'] += 1
            continue
        category = row.get('effective_category')
        if category not in CATEGORIES:
            stats['unknown_category'] += 1
            continue
        role = canonical_role(row.get('effective_role') or row['source_role'])
        if role not in ROLE_W:
            stats['unknown_role'] += 1
            continue
        key = (row['member'], category, str(row['bill']).strip(), str(congress))
        prior = best.get(key)
        if prior is None or ROLE_PRIORITY[role] > ROLE_PRIORITY[prior['role']]:
            if prior is not None:
                stats['dedup_upgraded'] += 1
            best[key] = {'role': role, 'sign': int(row['sign'])}
        else:
            stats['dedup_dropped'] += 1
    stats['kept'] = len(best)
    return best


def load_cu_cells(path):
    """data/lobbying/industry_axes.ndjson, credit_union + active rows only --
    the same selection and rounding primacy_factors.py:111-194 applies
    (cell = relevance * valence)."""
    out = {}
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if str(row.get('industry') or '').strip() != 'credit_union':
                continue
            if str(row.get('status') or '').strip() != 'active':
                continue
            key = (str(row['bill']).strip(), str(row['congress']).strip())
            out[key] = round(float(row['relevance']) * float(row['valence']), 6)
    return out


def read_service_spine(reference_dir):
    """Restricted reader for the two congress-legislators YAML files, copied from
    fec/legislation/effectiveness_sources.py:39-68. Deliberately not a YAML
    parser: it accepts only the dated ``rep``/``sen`` term shape it needs."""
    people = {}
    for name in ('legislators-current.yaml', 'legislators-historical.yaml'):
        with open(os.path.join(reference_dir, name), encoding='utf-8') as handle:
            text = handle.read()
        for block in re.split(r"(?m)^- id:\s*$", text)[1:]:
            match = re.search(r"(?m)^    bioguide: ['\"]?([A-Z]\d{6})", block)
            if not match:
                continue
            parts = block.split("  terms:\n", 1)
            if len(parts) < 2:
                continue
            terms = []
            for term in re.split(r"(?m)^  - type: ", parts[1])[1:]:
                values = dict(re.findall(r"(?m)^    (start|end): (.+)$", term))
                values = {key: value.strip("'\"").strip()
                          for key, value in values.items()}
                kind = term.splitlines()[0].strip()
                if kind not in ('rep', 'sen'):
                    continue
                # Preserve incomplete terms: dropping one would understate a
                # member's exposure when another valid term remains. Date
                # validation belongs in years_served, so unrelated historical
                # people cannot block a generation they do not enter.
                terms.append((kind, values.get('start'), values.get('end')))
            target = people.setdefault(match[1], [])
            for term in terms:
                if term not in target:
                    target.append(term)
    return people


def years_served(spine, bioguide, as_of):
    """Dated service inside [2021-01-03, as_of], overlapping rep/sen spans merged
    so a House-to-Senate transition is not double counted, clipped to
    [0.5, 6.0]. Returns (years, basis)."""
    spans = []
    documented_start_today = False
    for _kind, start, end in spine.get(bioguide) or []:
        try:
            first = date.fromisoformat(start)
            last = date.fromisoformat(end)
        except (TypeError, ValueError) as exc:
            raise ValueError('invalid service dates for %s: %r to %r'
                             % (bioguide, start, end)) from exc
        if last <= first:
            raise ValueError('nonpositive service interval for %s: %s to %s'
                             % (bioguide, start, end))
        documented_start_today |= (first == as_of and first >= WINDOW_LO)
        first = max(first, WINDOW_LO)
        last = min(last, as_of)
        if last > first:
            spans.append((first, last))
    spans.sort()
    merged = []
    for first, last in spans:
        if merged and first <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    total = sum((last - first).days for first, last in merged) / 365.25
    basis = 'service_dates'
    if total <= 0 and not documented_start_today:
        # Unknown exposure is not evidence of a very short tenure. Refuse the
        # generation instead of boosting annualized activity with a .5-year
        # invented denominator. A documented first day remains known tenure.
        raise ValueError('no dated service in the grading window for %s as of %s'
                         % (bioguide, as_of.isoformat()))
    return min(YEARS_MAX, max(YEARS_MIN, total)), basis


def load_signed_votes(path):
    """vote_category_lean.json 'votes' = {bioguide: {area: {lean, n}}}.
    fec/legislation/vote_by_category.py:84-93 builds lean as (sum of aligned
    votes)/n, so lean * n IS the signed vote sum -- one signed unit per vote,
    which is what build_field_action_evidence.vote_actions:440-441 emits. The
    roll-call source covers only 117/118/119, so no window filter is needed."""
    with open(path, encoding='utf-8') as handle:
        doc = json.load(handle)
    out = defaultdict(dict)
    counts = defaultdict(dict)
    for bioguide, areas in (doc.get('votes') or {}).items():
        for area, record in areas.items():
            if area not in IMP:
                continue
            out[bioguide][area] = float(record['lean']) * int(record['n'])
            counts[bioguide][area] = int(record['n'])
    return out, counts


def load_press_proportion(roster, press_path, tweet_path, bluesky_path):
    """press(m) = (supportive - opposed) / (all scored items of m).

    PER-ITEM POLICY-AREA TAGS DO NOT EXIST for the press+social corpus, so the
    member's overall proportion is applied to every area -- the honest form of
    the instruction, and the reason press enters at 20% rather than per-area.
    Sources: press_stance_final.csv (label ``stack_label``, has bioguide),
    data/stance/tweet_stance_final.csv (``stack_stance``, display-name join),
    data/stance/bluesky_stance_final.csv (``stance``). Denominator = items
    labelled support/neutral/oppose; 'unrelated' is excluded, not counted as
    neutral -- an unrelated item is not evidence about this industry.
    """
    name_to_bioguide = {member: row['bioguide'] for member, row in roster.items()}
    positive = Counter()
    negative = Counter()
    total = Counter()
    stats = Counter()
    scored = {'support': 1, 'oppose': -1, 'neutral': 0}

    def absorb(bioguide, label, tag):
        if label not in scored:
            stats[tag + '_unscored'] += 1
            return
        if not bioguide:
            stats[tag + '_unmatched'] += 1
            return
        total[bioguide] += 1
        if scored[label] > 0:
            positive[bioguide] += 1
        elif scored[label] < 0:
            negative[bioguide] += 1
        stats[tag + '_used'] += 1

    with open(press_path, encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            stats['press_rows'] += 1
            absorb((row.get('bioguide') or '').strip(),
                   (row.get('stack_label') or '').strip().lower(), 'press')
    with open(tweet_path, encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            stats['tweet_rows'] += 1
            absorb(name_to_bioguide.get((row.get('member') or '').strip()),
                   (row.get('stack_stance') or '').strip().lower(), 'tweet')
    with open(bluesky_path, encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            stats['bluesky_rows'] += 1
            absorb((row.get('bioguide') or '').strip(),
                   (row.get('stance') or '').strip().lower(), 'bluesky')

    proportion = {}
    items = {}
    for member, row in roster.items():
        bioguide = row['bioguide']
        items[member] = total[bioguide]
        proportion[member] = ((positive[bioguide] - negative[bioguide]) / total[bioguide]
                              if total[bioguide] else 0.0)
    return proportion, items, stats


# ---------------------------------------------------------------------------
# The grade
# ---------------------------------------------------------------------------
def load_method_inputs(roster, root):
    """Load the adopted method, or the exact pre-adoption inputs for rollback."""
    path = lambda relative: os.path.join(root, relative)
    if not DIAGNOSTIC:
        proportion, items, _ = load_press_proportion(
            roster, path('press_stance_final.csv'),
            path('data/stance/tweet_stance_final.csv'),
            path('data/stance/bluesky_stance_final.csv'))
        return proportion, items, None, None
    from engine.primacy_factors import load_tags
    from legislation import sentinel_admissions as SA
    tags = SA.effective_tags(load_tags(path('bill_beneficiary_tags.ndjson')), SA.load(root))
    weights = {(tag['bill'], str(tag['congress'])): BENEFICIARY_W[tag['class']]
               for tag in tags.values() if tag['class'] in BENEFICIARY_W}
    proportion, items, _, receipt = primacy_press.load(roster, root)
    return proportion, items, weights, receipt


def build(roster, acts, cu_cells, spine, votes, press_proportion, press_items,
          rankable, as_of, *, w_vote=W_VOTE, press_floor=PRESS_FLOOR, k=K_TENSOR,
          beneficiary_weights=None):
    """Return every intermediate the artifact records. Pure function of its
    arguments -- no file access, so tests can drive it with fixtures."""
    chamber_of = {member: roster[member]['chamber'] for member in rankable}
    years = {}
    years_basis = Counter()
    for member in rankable:
        value, basis = years_served(spine, roster[member]['bioguide'], as_of)
        years[member] = value
        years_basis[basis] += 1

    legislative = defaultdict(lambda: defaultdict(float))
    acts_with_cell = 0
    for (member, area, bill, congress), row in acts.items():
        if member not in years:
            continue
        cell = cu_cells.get((bill, congress))
        if cell is None:
            cell = 0.0
        else:
            acts_with_cell += 1
        magnitude = (1.0 if beneficiary_weights is None else
                     beneficiary_weights.get((bill, congress), 0.0))
        legislative[member][area] += (ROLE_W[row['role']] * row['sign']
                                     * (1.0 + k * cell) * magnitude)
    if w_vote:
        for member in rankable:
            for area, signed in (votes.get(roster[member]['bioguide']) or {}).items():
                legislative[member][area] += w_vote * signed

    rate = {member: {area: legislative[member].get(area, 0.0) / years[member]
                     for area in CATEGORIES}
            for member in rankable}
    n_leg = {area: within_chamber_percentile(
                 {member: rate[member][area] for member in rankable}, chamber_of)
             for area in CATEGORIES}

    press = {member: (press_proportion[member]
                      if press_items[member] >= press_floor else 0.0)
             for member in rankable}
    n_press = within_chamber_percentile(press, chamber_of)

    grade = {member: sum(IMP[area] * (LEG_SHARE * n_leg[area][member]
                                      + PRESS_SHARE * n_press[member])
                         for area in CATEGORIES)
             for member in rankable}
    grade_percentile = within_chamber_percentile(grade, chamber_of)
    return {
        'chamber_of': chamber_of,
        'years': years,
        'years_basis': dict(years_basis),
        'legislative': legislative,
        'rate': rate,
        'n_leg': n_leg,
        'press': press,
        'n_press': n_press,
        'grade': grade,
        'grade_percentile': grade_percentile,
        'acts_with_cell': acts_with_cell,
        'press_floor_zeroed': sum(1 for member in rankable
                                  if press_items[member] < press_floor),
        'members_at_year_cap': sum(1 for value in years.values()
                                   if value >= YEARS_MAX),
    }


def document(roster, rankable, result, press_proportion, press_items, inputs,
             act_stats, cu_cells, as_of, *, w_vote=W_VOTE,
             press_floor=PRESS_FLOOR, k=K_TENSOR):
    members = {}
    for member in rankable:
        members[member] = {
            'bioguide': roster[member]['bioguide'],
            'chamber': roster[member]['chamber'],
            'years': round(result['years'][member], 6),
            'press_items': press_items[member],
            'press_proportion': round(press_proportion[member], 6),
            'n_press': result['n_press'][member],
            'rates': {
                area: {
                    'signed': round(result['legislative'][member].get(area, 0.0), 6),
                    'rate': round(result['rate'][member][area], 6),
                    'n_leg': result['n_leg'][area][member],
                }
                for area in CATEGORIES
            },
            'grade': round(result['grade'][member], 6),
            'grade_percentile': result['grade_percentile'][member],
        }
    return {
        'schema': SCHEMA,
        'as_of': as_of.isoformat(),
        'policy': {
            'role_weights': dict(ROLE_W),
            'w_vote': w_vote,
            'k': k,
            'press_floor': press_floor,
            'leg_share': LEG_SHARE,
            'press_share': PRESS_SHARE,
            'years_min': YEARS_MIN,
            'years_max': YEARS_MAX,
            'congresses': list(WINDOW_CONGRESSES),
            'normalization': NORMALIZATION,
            'accepted_dispositions': list(ACCEPTED_DISPOSITIONS),
            'area_weights': dict(IMP),
        },
        'inputs': inputs,
        'counts': {
            'members': len(rankable),
            'rows': act_stats['rows'],
            'accepted': act_stats['accepted'],
            'outside_window': act_stats['outside_window'],
            'unknown_category': act_stats['unknown_category'],
            'unknown_role': act_stats['unknown_role'],
            'dedup_upgraded': act_stats['dedup_upgraded'],
            'dedup_dropped': act_stats['dedup_dropped'],
            'kept': act_stats['kept'],
            'tensor_cells': len(cu_cells),
            'acts_with_cell': result['acts_with_cell'],
            'press_floor_zeroed': result['press_floor_zeroed'],
            'years_basis': result['years_basis'],
            'members_at_year_cap': result['members_at_year_cap'],
        },
        'members': members,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--root', default=MB_ROOT,
                        help='project root (default: paths.ROOT)')
    parser.add_argument('--as-of', default=None,
                        help='YYYY-MM-DD service-window close (default: today)')
    parser.add_argument('--out', default=None,
                        help='output path (default: <root>/%s)' % OUT_PATH)
    args = parser.parse_args(argv)

    root = args.root
    path = lambda *parts: os.path.join(root, *parts)
    as_of = (date.fromisoformat(args.as_of) if args.as_of else date.today())

    roster = load_roster(path('data', 'engine', 'congestion.json'))
    scored_members = load_pct_members(path('ranks_python.json'))
    factor_members = load_primacy_members(path('data', 'engine', 'primacy_factors.json'))
    rankable = [member for member in sorted(roster)
                if member in scored_members and member in factor_members]
    if not rankable:
        raise ValueError('no rankable members: congestion.json, ranks_python.json '
                         'pcts and primacy_factors.json do not intersect')
    # Every member the board can rank must receive a grade, or tier_util's loader
    # fails loud at seating time. Catch it here, where the inputs are in hand.
    missing = sorted((scored_members & factor_members) - set(rankable))
    if missing:
        raise ValueError(
            'rankable members absent from congestion.json (no chamber, so no '
            'within-chamber percentile): %s' % ', '.join(missing[:10]))

    act_stats = Counter()
    acts = load_acts(path('legislation_routing_audit.json'), act_stats)
    cu_cells = load_cu_cells(path('data', 'lobbying', 'industry_axes.ndjson'))
    spine = read_service_spine(path(REFERENCE_DIR))
    votes, _vote_counts = load_signed_votes(path('vote_category_lean.json'))
    press_proportion, press_items, beneficiary_weights, press_receipt = (
        load_method_inputs(roster, root))

    result = build(roster, acts, cu_cells, spine, votes, press_proportion,
                   press_items, rankable, as_of, beneficiary_weights=beneficiary_weights)
    inputs = {relative: sha256_of(path(*relative.split('/')))
              for relative in INPUT_FILES}
    doc = document(roster, rankable, result, press_proportion, press_items,
                   inputs, act_stats, cu_cells, as_of)
    if DIAGNOSTIC:
        doc['policy'].update(method=METHOD, beneficiary_weights=BENEFICIARY_W,
                             press_corpus=press_receipt)
        doc['counts']['unclassified_acts_excluded'] = sum(
            (bill, congress) not in beneficiary_weights
            for member, area, bill, congress in acts if member in rankable)

    output_path = args.out or path(*OUT_PATH.split('/'))
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    dump_atomic(doc, output_path, indent=1, ensure_ascii=False, sort_keys=True)
    print('per_area_grade: %d members | as_of %s | kept %d acts | %d tensor cells '
          'on %d acts | press floor %d zeroes %d | years %s'
          % (len(rankable), as_of.isoformat(), act_stats['kept'], len(cu_cells),
             result['acts_with_cell'], PRESS_FLOOR, result['press_floor_zeroed'],
             result['years_basis']))
    return doc


if __name__ == '__main__':
    main()
