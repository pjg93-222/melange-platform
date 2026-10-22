# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))  # centralized root
from paths import ROOT as _ROOT
# CURRENT CONTRACT (D-032 and diagnostic adoption, 2026-09-20): per_area_v1
# scores P * (0.80 + 0.20 * grade percentile), then compares within chamber.
# RE-1 (2026-10-11): by default the multiplier now scales only P's distance
# from the roster baseline (PRIMACY_GRADE_FORM, below); 'whole' reverts.
# The diagnostic factor retains shrinkage but removes the extra uncertainty
# dock (K=0). Evidence and conduct floors are enforced jointly. The notes
# below describe the historical factor-only mode, retained as a revert.
# HISTORICAL CONTRACT (2026-07-29, user directive): this is the beneficiary-primacy
# board and engine priority. Press, Committee, and Demo do not enter its score —
# and as of the factor-only directive, NEITHER DOES the Legislative percentile.
# Classified acts and primacy_adjudication_ledger.ndjson feed the factor upstream
# in primacy_factors.py; no member is assigned a fixed display slot.
# PRIMACY TIER — shared by
# build_dashboard_bundle.py and make_member_desk.py so the tier definition lives in ONE place (no drift).
#
# The overall reporting rank still blends four pillars. This board does not:
# score = factor_ranked, the shrunk beneficiary-primacy factor discounted by
# the uncertainty penalty K*(1-lambda)*|raw-baseline| ("certainty outranks
# estimation", user-ratified 2026-07-29: a thin-evidence point estimate can
# no longer outrank a full-evidence measurement at a tier boundary; K=0.25
# in fec/engine/primacy_factors.py, chosen so Vargas keeps Tier 1), followed by the
# evidence floor (PRIMACY_MIN_CUPRIM cu_primary credit to hold any tier seat;
# "purity ranks, evidence qualifies", user-ratified 2026-07-29), then the
# grievance/delivery conduct ladder, then fixed buckets of top 13 / next 24 /
# next 48. The allocation engine anchors general priority on the resulting tier
# and adds a small leave-one-topic-out orientation adjustment in moneyball_core;
# neither Press nor the official rank enters this board.
#
# WHY the Legislative percentile was dropped (2026-07-29 investigation): Leg% is
# 0.7*agenda-percentile + 0.3*legislative-EFFICIENCY, and that efficiency term
# could not be reconstructed from the success-rate chain it documents. 203 of 533
# members hold all-zero success rates in BOTH Legislative Score sheets, so they
# must tie in any success-rate-derived measure; the workbook instead spreads them
# across 151 distinct values, and 113 of those zero-data members outscored a
# member holding real data. The board no longer multiplies by a term that cannot
# be explained. Blended Model!G provenance remains OPEN and still feeds the
# four-pillar reporting rank — this change does not touch that.
#
# CONSEQUENCES ACCEPTED WITH THE DIRECTIVE (both verified 2026-07-29):
#   * the grievance/delivery conduct ladder goes INERT — under factor-only no
#     member scores high enough for a ceiling to bite (previously it held down
#     Andy Barr 18->38 and John Rose 38->37), so a cure earns nothing back;
#   * the 2026-07-29 five-member adjudication no longer lands two of its five
#     inside Tier 1 (Bill Huizenga -> 18, Young Kim -> 14, both limit 13).
# REVERT: PRIMACY_BOARD=legxfactor restores Leg% x factor exactly.
# REVERT (2026-09-16, D-031): PRIMACY_BOARD=factor restores the factor-only
# board described above byte-for-byte -- same score, same sort key, same floor.
# The per-area grade artifact is still built by the chain; it is simply not read.
import os, json, math
from engine.primacy_factors import (
    AFFIRMATIVE_PRIMARY_FIELD, AFFIRMATIVE_PRIMARY_POLICY,
    PRIMACY_T1_MIN_AFFIRMATIVE,
)

BUCKETS = (13, 24, 48)   # Tier 1 / 2 / 3 sizes; cumulative cutoffs 13 / 37 / 85, rest untiered (tier 0)
# Board basis. 'per_area_v1' (default, D-031, Paul 2026-09-15) scores on the
# purity factor multiplied by the member's per-area grade percentile --
# factor_ranked * (0.80 + 0.20 * grade%) -- and seats on the WITHIN-CHAMBER
# percentile of that product, ties on the raw product then name (D-027). The
# grade itself is the per-year rate of signed legislative action in each of the
# 18 IMP areas, 80%, blended with the member's press proportion, 20%; it is
# built by fec/engine/per_area_grade.py, the LAST entry of ENGINE_INPUT_STEPS
# in fec/engine/build_primacy_surfaces.py (:28-:56), run immediately after
# engine/primacy_factors.py -- 4dc148fc1 moved it there from its own
# run_pipeline.py step. It does NOT run before every consumer of this board:
# the analytic desk pass earlier in that same tuple already calls
# legislative_press_tiers (make_member_desk.py:2520), which is why main()
# prepends the grade step on a tree that has no artifact yet (:94-:99).
# 'factor' is the REVERT SWITCH and restores the
# 2026-07-29 factor-only board exactly; 'legxfactor' still restores the
# pre-directive Legislative-percentile x factor product.
# WHY a percentile and not the raw product: the grade is normalised within
# chamber, so a Senate grade and a House grade are not comparable numbers. Sorting
# the raw product would silently re-import the cross-chamber comparison that
# D-027 removed.
PRIMACY_BOARD = (os.environ.get('PRIMACY_BOARD') or 'per_area_v1').strip().lower()
PRIMACY_BOARD_MODES = ('factor', 'legxfactor', 'realized_results_v1', 'per_area_v1')
# The purity factor keeps this share of the product; the per-area grade moves the
# rest. 0.80/0.20 is the ratified P1 form (R1), not a tuning dial.
PER_AREA_PURITY_SHARE = 0.80
# RE-1 (2026-10-11, Paul-delegated decision, audits/decisions_20261011/
# C_forecast_compet_research.md): HOW the grade multiplier m = 0.80 + 0.20*grade%
# meets the factor. 'whole' (D-032) scales the whole value, P*m, so a high grade
# lifts a below-baseline purity record by volume. 'deviation' (G1) scales only
# a POSITIVE distance from the stored roster baseline B: B + (P-B)*m when
# P >= B, and P itself when P < B. The grade therefore never lifts a
# below-baseline record, and the score never rises as the grade falls
# (monotone; coordinator ruling 2026-10-11). Parameter-free: B is
# primacy_factors._constants.roster_baseline, read every run. per_area_v1 only.
# 'whole' reverts byte-for-byte.
# Default 'deviation' since 2026-10-11: the read-only re-run on the 10-08
# artifacts passed all five pre-fixed RE-1 gates (whole = live 533/533;
# deviation at grade 1 = grade-off 533/533; Tier-1 overlap 12/13; size-bias
# rho .32 -> .10; R:D of the 85 57:28 -> 56:29). Below-baseline members in the
# 85: 3 -> 0.
PRIMACY_GRADE_FORM = (os.environ.get('PRIMACY_GRADE_FORM') or 'deviation').strip().lower()
PRIMACY_GRADE_FORMS = ('whole', 'deviation')
if PRIMACY_GRADE_FORM not in PRIMACY_GRADE_FORMS:
    raise ValueError('PRIMACY_GRADE_FORM must be one of %s; got %r'
                     % (', '.join(PRIMACY_GRADE_FORMS), PRIMACY_GRADE_FORM))
PER_AREA_GRADE_PATH = 'data/engine/per_area_grade.json'
PER_AREA_GRADE_SCHEMA = 'per-area-grade-v1'
# Evidence floor ("purity ranks, evidence qualifies", user-ratified 2026-07-29):
# a member must hold at least this much cu_primary role-credit to OCCUPY a tier
# seat. Ranking among the qualified stays pure factor; failing members re-seat
# below the tiers and the next-highest qualified factors fill the 85 seats.
# Closes the narrow-vs-pure gap: a member can no longer look pure by having done
# almost nothing (Maloy tiered on 1.25 total credits / 0.50 cu_primary). 0.75
# leaves Tier 1 untouched and drops only the thin tail (14 of 85 at adoption).
# PRIMACY_MIN_CUPRIM=0 disables the gate. Factor-mode only: the legxfactor
# revert switch restores the ENTIRE previous board, floor included, exactly.
PRIMACY_MIN_CUPRIM = float(os.environ.get('PRIMACY_MIN_CUPRIM') or '0.75')
if PRIMACY_MIN_CUPRIM < 0:
    raise ValueError(
        'PRIMACY_MIN_CUPRIM must be >= 0; got %r' % PRIMACY_MIN_CUPRIM
    )
PRIMACY_PATH = 'data/engine/primacy_factors.json'
LEDGER_PATH = 'grievance_ledger.ndjson'
DELIVERY_PATH = 'delivery_ledger.ndjson'
CONDUCT_LADDER = ((9.0, 0), (6.0, 3), (3.0, 2))
_CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OVERRIDE_PATH = os.path.join(_CODE_ROOT, 'tier_overrides.json')


def legislative_press_scores(pcts, legislative_weight, press_weight):
    """Blend the already-normalized Legislative and Press pillar inputs without rescaling them."""
    out = {}
    for member, values in (pcts or {}).items():
        try:
            leg = float(values['leg'])
            press = float(values['press'])
        except (KeyError, TypeError, ValueError):
            continue
        out[member] = float(legislative_weight) * leg + float(press_weight) * press
    return out


def _load_tier_policy(path=None):
    """Load the source-controlled display-tier policy document."""
    path = path or _OVERRIDE_PATH
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def load_tier_weights(path=None, fallback=(0.30, 0.25)):
    """Return (Legislative, Press) display-tier weights.

    The explicit policy must be a positive, unit-sum pair. The legacy workbook ratio remains a fallback
    only for installations that do not yet carry ``tier_overrides.json``.
    """
    doc = _load_tier_policy(path)
    weights = doc.get('weights') if isinstance(doc, dict) else None
    if not weights:
        return tuple(float(value) for value in fallback)
    try:
        legislative = float(weights['legislation'])
        press = float(weights['press'])
    except (KeyError, TypeError, ValueError):
        raise ValueError('tier weights require numeric legislation and press values')
    if legislative <= 0.0 or press <= 0.0 or abs(legislative + press - 1.0) > 1e-9:
        raise ValueError('tier weights must be positive and sum to 1.0')
    return legislative, press


def load_tier_overrides(path=None):
    """Load exact member->tier placements from the source-controlled display-tier contract."""
    doc = _load_tier_policy(path)
    rows = doc if isinstance(doc, list) else doc.get('placements', [])
    out = {}
    for row in rows:
        member = str(row.get('member') or '').strip()
        try:
            tier = int(row.get('tier'))
        except (TypeError, ValueError):
            tier = 0
        if not member or tier not in (1, 2, 3):
            raise ValueError('invalid tier override row: %r' % row)
        if member in out:
            raise ValueError('duplicate tier override member: %s' % member)
        out[member] = tier
    return out


def load_tier_positions(path=None):
    """Load optional exact within-bucket display slots: {member: 1-based position in its tier}.

    A placement row may carry ``"position": N`` to pin WHERE inside its bucket the member is shown.
    Rows without ``position`` are unaffected and keep score order.
    """
    doc = _load_tier_policy(path)
    rows = doc if isinstance(doc, list) else doc.get('placements', [])
    out = {}
    for row in rows:
        if row.get('position') is None:
            continue
        member = str(row.get('member') or '').strip()
        try:
            slot = int(row.get('position'))
        except (TypeError, ValueError):
            raise ValueError('invalid tier position row: %r' % row)
        if not member or slot < 1:
            raise ValueError('invalid tier position row: %r' % row)
        if member in out:
            raise ValueError('duplicate tier position member: %s' % member)
        out[member] = slot
    return out


def apply_tier_overrides(order, overrides=None, buckets=BUCKETS, positions=None):
    """Return a fixed-size display order honoring exact placements.

    Forced members occupy their named bucket. Remaining seats are filled from the unmodified
    score order, excluding members forced into another bucket. Within each bucket, members retain
    their relative score order so the override changes placement, not the underlying score.

    EXCEPTION (Paul directive 2026-07-19, "have Brian take Wagner's former spot, like number 5"):
    a placement row carrying ``position: N`` pins that member to the Nth slot of its bucket,
    displacing the score order for that bucket only. This DELIBERATELY breaks the
    "within a bucket, order == score order" property above, so a pinned bucket's internal order is
    no longer readable as a ranking. Only pinned members are affected; everyone else keeps their
    relative score order around them. Pins are display-only and change no score, pillar, or rank.
    """
    order = list(order)
    pos = {member: i for i, member in enumerate(order)}
    # FAIL LOUD on unresolved placement names (rank-integrity audit 2026-07-17): a typo'd or
    # rolled-over member in tier_overrides.json used to be silently dropped, quietly changing
    # Tier buckets and the Champion crown. Paul's placement contract must bind exactly.
    _unknown = sorted(m for m in (overrides or {}) if m not in pos)
    if _unknown:
        raise ValueError('tier placement names not in the rankable roster: %s' % _unknown)
    overrides = {member: int(tier) for member, tier in (overrides or {}).items()}
    if any(tier not in (1, 2, 3) for tier in overrides.values()):
        raise ValueError('tier overrides must be 1, 2, or 3')
    assigned = set()
    display_order = []
    for tier, capacity in enumerate(tuple(buckets), start=1):
        forced = [member for member in order if overrides.get(member) == tier]
        if len(forced) > capacity:
            raise ValueError('tier %d has %d forced members for %d seats' %
                             (tier, len(forced), capacity))
        eligible = [member for member in order
                    if member not in assigned and member not in overrides]
        selected = forced + eligible[:capacity - len(forced)]
        selected.sort(key=pos.__getitem__)
        # Explicit within-bucket slots (see docstring). Applied lowest-slot-first so the result is
        # deterministic and independent of dict order; everyone unpinned keeps their relative order.
        _pins = sorted(((int(s), m) for m, s in (positions or {}).items() if m in selected),
                       key=lambda kv: (kv[0], pos[kv[1]]))
        if _pins:
            for _slot, _m in _pins:
                if _slot > len(selected):
                    raise ValueError('tier %d position %d exceeds its %d seats (%s)'
                                     % (tier, _slot, len(selected), _m))
            _seen = {}
            for _slot, _m in _pins:
                if _slot in _seen:
                    raise ValueError('duplicate tier position %d: %s and %s'
                                     % (_slot, _seen[_slot], _m))
                _seen[_slot] = _m
            _rest = [m for m in selected if m not in dict(( (m2, s2) for s2, m2 in _pins ))]
            for _slot, _m in _pins:
                _rest.insert(_slot - 1, _m)
            selected = _rest
        assigned.update(selected)
        display_order.extend(selected)
    display_order.extend(member for member in order if member not in assigned)
    return display_order


def load_primacy_factors(root, path=None):
    """Return member -> shrunk primacy factor.

    Missing evidence fails open to a neutral factor. Present but malformed
    evidence fails loud so corruption cannot masquerade as absence.
    """
    target = os.path.join(root, path or PRIMACY_PATH)
    if not os.path.exists(target):
        return {}
    try:
        with open(target, encoding='utf-8') as handle:
            doc = json.load(handle)
        return {
            str(member): _finite_primacy_number(
                values['factor_shrunk'], 'shrunk primacy factor for %r' % member)
            for member, values in (doc.get('members') or {}).items()
        }
    except Exception as exc:
        raise ValueError(
            'primacy_factors.json is present but invalid; refusing to '
            'treat malformed primacy evidence as absent'
        ) from exc


def load_open_breaches(root, path=None):
    """Load and sum severity times leverage for every open grievance."""
    target = os.path.join(root, path or LEDGER_PATH)
    out = {}
    if not os.path.exists(target):
        return out
    with open(target, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            if str(event.get('status', 'open')) != 'open':
                continue
            event_label = 'grievance %r' % event.get('event_id')
            severity = _nonnegative_primacy_number(
                event.get('severity', 0), event_label + ' severity')
            # Missing leverage uses the documented neutral multiplier. A real
            # zero remains zero; truthiness must never turn it into one.
            multiplier = _nonnegative_primacy_number(
                event.get('leverage_mult', 1.0), event_label + ' leverage multiplier')
            if severity == 0:
                continue
            weighted_severity = _nonnegative_primacy_number(
                severity * multiplier, event_label + ' weighted severity')
            record = out.setdefault(
                str(event.get('member')),
                {
                    'effective_severity': 0.0,
                    'events': [],
                    'event_detail': [],
                },
            )
            record['effective_severity'] = _nonnegative_primacy_number(
                record['effective_severity'] + weighted_severity,
                'total conduct severity for %r' % event.get('member'),
            )
            record['events'].append(
                (str(event.get('event_id')), severity, multiplier)
            )
            vehicle = str(event.get('vehicle') or '').strip()
            event_type = str(event.get('type') or '').strip()
            if event_type == 'G1_refused_ask_on_vehicle':
                reason = (
                    'Refused credit-union asks on %s while exercising %s.'
                    % (
                        vehicle or 'the identified vehicle',
                        str(event.get('leverage') or 'identified leverage'),
                    )
                )
            elif event_type == 'G4_credit_appropriation':
                reason = (
                    'Claimed credit-union benefit on %s without a '
                    'substantive credit-union provision.'
                    % (vehicle or 'the identified vehicle')
                )
            else:
                reason = event_type.replace('_', ' ') or 'Open grievance'
            evidence_urls = []
            for evidence in event.get('evidence') or []:
                url = str((evidence or {}).get('url') or '').strip()
                if url and url not in evidence_urls:
                    evidence_urls.append(url)
            record['event_detail'].append({
                'event_id': str(event.get('event_id') or ''),
                'type': event_type,
                'vehicle': vehicle,
                'severity': severity,
                'leverage': str(event.get('leverage') or ''),
                'leverage_mult': multiplier,
                'weighted_severity': round(weighted_severity, 4),
                'reason': reason,
                'cure': str(event.get('cure') or '').strip(),
                'asks_excluded': [
                    str(value) for value in event.get('asks_excluded') or []
                    if str(value).strip()
                ],
                'evidence_urls': evidence_urls,
            })
    for record in out.values():
        record['effective_severity'] = round(
            record['effective_severity'], 4
        )
    return out


def load_delivery_credit(root, path=None):
    """Load positive delivery credit from open, in-universe events."""
    target = os.path.join(root, path or DELIVERY_PATH)
    out = {}
    if not os.path.exists(target):
        return out
    with open(target, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            if str(event.get('status', 'open')) != 'open':
                continue
            event_label = 'delivery %r' % event.get('event_id')
            credit = event.get('delivered_credit')
            if credit is None:
                # Null/missing direct credit selects the existing derived path;
                # every participating raw quantity still has to be valid.
                act_weight = _nonnegative_primacy_number(
                    event.get('act_weight', 0.0), event_label + ' act weight')
                instrument = _nonnegative_primacy_number(
                    event.get('instrument_mult', 0.0), event_label + ' instrument multiplier')
                credit = act_weight * instrument
            credit = _nonnegative_primacy_number(credit, event_label + ' credit')
            if credit == 0:
                continue
            record = out.setdefault(
                str(event.get('member')),
                {'delivered': 0.0, 'events': [], 'event_detail': []},
            )
            record['delivered'] = _nonnegative_primacy_number(
                record['delivered'] + credit,
                'total delivery credit for %r' % event.get('member'),
            )
            record['events'].append((
                str(event.get('event_id')),
                str(event.get('bill')),
                str(event.get('beneficiary_class')),
                credit,
            ))
            record['event_detail'].append({
                'event_id': str(event.get('event_id') or ''),
                'bill': str(event.get('bill') or ''),
                'congress': str(event.get('congress') or ''),
                'beneficiary_class': str(
                    event.get('beneficiary_class') or ''
                ),
                'instrument': str(event.get('instrument') or ''),
                'credit': credit,
            })
    for record in out.values():
        record['delivered'] = round(record['delivered'], 4)
    return out


def tier_ceiling(effective_severity, ladder=CONDUCT_LADDER):
    """Best tier a member may occupy; 1 is unconstrained, 0 untiered."""
    for threshold, ceiling in ladder:
        if effective_severity >= threshold:
            return ceiling
    return 1


def _cutoffs(buckets):
    first, second, third = buckets
    return first, first + second, first + second + third


def _first_slot_of_tier(tier, buckets):
    first, second, third = _cutoffs(buckets)
    return {1: 0, 2: first, 3: second}.get(tier, third)


def _finite_primacy_number(value, label):
    """Reject invalid numeric evidence before it reaches ordering arithmetic."""
    if isinstance(value, bool):
        raise ValueError('%s must be a finite number, not a boolean' % label)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('%s must be a finite number' % label) from exc
    if not math.isfinite(number):
        raise ValueError('%s must be a finite number' % label)
    return number


def _nonnegative_primacy_number(value, label):
    number = _finite_primacy_number(value, label)
    if number < 0:
        raise ValueError('%s must be nonnegative' % label)
    return number


def _grade_percentile(value, member):
    grade = _finite_primacy_number(value, 'grade percentile for %r' % member)
    if not 0.0 <= grade <= 1.0:
        raise ValueError('grade percentile for %r must be in [0, 1]' % member)
    return grade


def primacy_scores(pcts, factors, mode=None, grades=None, *, grade_form=None,
                   baseline=None):
    """Board score for every rankable member.

    FACTOR MODE (user directive 2026-07-29): the score IS the shrunk beneficiary-
    primacy factor. The Legislative percentile no longer multiplies it — see the
    module header for the evidence that retired it. ``PRIMACY_BOARD=legxfactor``
    restores the previous product exactly.

    Membership is still gated on a usable ``leg`` percentile so the rankable
    roster is identical under both modes; the value is simply not scored under
    'factor'.

    Under 'factor' the neutral fail-open default is REMOVED. A member with no
    factor — or a wholly absent primacy_factors.json, which ``load_primacy_factors``
    reports as ``{}`` — previously scored a neutral 1.0. With the percentile gone
    that 1.0 is the maximum possible score, so a single missing member would take
    slot 1 and an absent file would collapse the entire board to alphabetical
    order, silently. Missing evidence now fails loud.

    ACTIVE (D-032, following D-031): under 'per_area_v1' the score is that same
    factor multiplied by (0.80 + 0.20 * the member's per-area grade percentile),
    so the positive-domain modifier ranges from .80 to 1.00. This is not a
    bound on rank movement. ``grades`` is
    {member: grade_percentile} from ``load_per_area_grade``; it has no default
    for the reason given in that loader's docstring.

    RE-1 / G1: ``grade_form='deviation'`` scores B + (factor - B) * m when
    factor >= B and the factor itself below B, with B = ``baseline`` (the
    factor artifact's roster_baseline, required).
    """
    mode = (mode or PRIMACY_BOARD)
    if mode not in PRIMACY_BOARD_MODES:
        raise ValueError(
            'PRIMACY_BOARD must be one of %s; got %r'
            % (', '.join(PRIMACY_BOARD_MODES), mode)
        )
    grade_form = grade_form or PRIMACY_GRADE_FORM
    if grade_form not in PRIMACY_GRADE_FORMS:
        raise ValueError('PRIMACY_GRADE_FORM must be one of %s; got %r'
                         % (', '.join(PRIMACY_GRADE_FORMS), grade_form))
    if mode == 'per_area_v1' and grade_form == 'deviation':
        if baseline is None:
            raise ValueError('PRIMACY_GRADE_FORM=deviation needs the roster '
                             'baseline; set PRIMACY_GRADE_FORM=whole to revert')
        baseline = _finite_primacy_number(baseline, 'primacy roster baseline')
    out = {}
    for member, values in (pcts or {}).items():
        try:
            legislative = float(values['leg'])
        except (KeyError, TypeError, ValueError):
            continue
        legislative = _finite_primacy_number(
            values['leg'], 'legislative percentile for %r' % member)
        if mode == 'legxfactor':
            factor = _finite_primacy_number(
                factors.get(member, 1.0), 'primacy factor for %r' % member)
            out[member] = _finite_primacy_number(
                legislative * factor, 'primacy score for %r' % member)
            continue
        try:
            raw_factor = factors[member]
        except (KeyError, TypeError):
            raise ValueError(
                'no primacy factor for %r. The factor-only board has no neutral '
                'default: a missing factor would score 1.0 and take slot 1, and '
                'an absent primacy_factors.json would order the board '
                'alphabetically. Rebuild the factors (fec/engine/primacy_factors.py) or '
                'set PRIMACY_BOARD=legxfactor.' % member
            )
        score = _finite_primacy_number(raw_factor, 'primacy factor for %r' % member)
        if mode == 'per_area_v1':
            # The ratified modifier rewards a higher grade only for P >= 0.
            # Negative opposition evidence needs an explicit signed-domain
            # policy; never silently clip it or invert the grade's benefit.
            if score < 0:
                raise ValueError(
                    'negative primacy factor for %r is unsupported by the '
                    'per_area_v1 grade product; review signed-domain policy '
                    'before publication' % member)
            try:
                raw_grade = grades[member]
            except (KeyError, TypeError):
                raise ValueError(
                    'no per-area grade for %r. The per_area_v1 board has no '
                    'neutral default: 0.0 is a live score (the bottom of the '
                    'chamber), so a missing grade would silently demote that '
                    'member and an absent artifact would silently collapse the '
                    'board to the factor alone. Rebuild the grade '
                    '(fec/engine/per_area_grade.py) or set PRIMACY_BOARD=factor.'
                    % member
                )
            grade = _grade_percentile(raw_grade, member)
            multiplier = (PER_AREA_PURITY_SHARE
                          + (1.0 - PER_AREA_PURITY_SHARE) * grade)
            if grade_form == 'deviation':
                # Monotone in grade (coordinator ruling 2026-10-11): only a
                # POSITIVE deviation is scaled; below B the score is P, so a
                # falling grade can never raise a member's score.
                if score >= baseline:
                    score = baseline + (score - baseline) * multiplier
            else:
                score *= multiplier
        out[member] = score
    return out


def withhold_roster_arrivals(members, pcts, factors, root=None):
    """Roster-refresh drift (2026-10-21): an arrival the factor artifact predates is WITHHELD
    from the board, never defaulted. Returns (pcts, withheld). Any other member without a
    factor stays in pcts so primacy_scores still fails loud."""
    if not any(m in pcts and m not in factors for m in members):
        return pcts, []          # no drift: never read roster.json
    import roster
    withheld = roster.drift(members, factors, root)[0]
    if withheld:
        print('  !! primacy board: roster drift; %d WITHHELD (%s)'
              % (len(withheld), ', '.join(sorted(withheld))))
        pcts = {m: v for m, v in pcts.items() if m not in withheld}
    return pcts, sorted(withheld)


def apply_conduct_ladder(order, breaches, delivered=None, buckets=BUCKETS, *,
                         minimum_slots=None):
    """Seat once under the strictest evidence or net-conduct restriction.

    ``minimum_slots`` contains zero-based eligibility floors, calculated from
    the original order. Combining by maximum prevents a conduct demotion from
    promoting someone past an evidence restriction. Explicit tier placements
    may exempt evidence checks, but never exempt the conduct ladder.
    """
    delivered = delivered or {}
    floors = _validated_slot_floors(minimum_slots or {})
    detail = {}
    for member in order:
        grievance = breaches.get(member)
        delivery = delivered.get(member)
        if not grievance and not delivery:
            continue
        gross = (
            _finite_primacy_number(grievance['effective_severity'],
                                   'conduct severity for %r' % member)
            if grievance else 0.0
        )
        credit = (
            _finite_primacy_number(delivery['delivered'],
                                   'delivery credit for %r' % member)
            if delivery else 0.0
        )
        if gross < 0 or credit < 0:
            raise ValueError('conduct severity and delivery credit for %r '
                             'must be nonnegative' % member)
        net = round(gross - credit, 4)
        ceiling = tier_ceiling(net)
        detail[member] = {
            'gross_severity': gross,
            'delivered_credit': credit,
            'net_severity': net,
            'effective_severity': net,
            'tier_ceiling': ceiling,
            'open_events': (
                [event[0] for event in grievance['events']]
                if grievance else []
            ),
            'delivery_events': (
                [event[0] for event in delivery['events']]
                if delivery else []
            ),
            'grievances': (
                list(grievance.get('event_detail') or [])
                if grievance else []
            ),
            'deliveries': (
                list(delivery.get('event_detail') or [])
                if delivery else []
            ),
        }
        if ceiling != 1:
            floors[member] = max(
                floors.get(member, 0), _first_slot_of_tier(ceiling, buckets)
            )
    return _reseat_with_floors(order, floors), detail


def _validated_slot_floors(floors):
    for member, floor in floors.items():
        if isinstance(floor, bool) or not isinstance(floor, int) or floor < 0:
            raise ValueError('minimum slot for %r must be a nonnegative integer'
                             % member)
    return dict(floors)


def _reseat_with_floors(order, floors):
    """Stably re-seat members at or below their floor slot.

    Shared by the conduct ladder and the evidence floor so both constraints
    use identical mechanics: everyone unconstrained keeps relative score
    order; a floored member takes the first slot at or below their floor.
    A compact board has no vacant slots. If none of the remaining candidates
    can occupy the next slot, no compact arrangement can satisfy the floors;
    fail instead of appending candidates in seats they cannot legally occupy.
    """
    order = list(order)
    if len(set(order)) != len(order):
        raise ValueError('tier seating roster contains duplicate members')
    floors = _validated_slot_floors(floors)
    if not floors:
        return order
    remaining = list(order)
    final = []
    for slot in range(len(order)):
        for index, member in enumerate(remaining):
            if floors.get(member, 0) <= slot:
                final.append(remaining.pop(index))
                break
        else:
            blocked = ', '.join('%s>=rank %d' % (member, floors[member] + 1)
                                for member in remaining)
            raise ValueError(
                'Infeasible tier seating at rank %d of %d: no remaining '
                'member satisfies the minimum slot (%s)'
                % (slot + 1, len(order), blocked)
            )
    return final


def load_primacy_ranked(root, path=None):
    """Return member -> board ranking value (factor_ranked).

    factor_ranked = factor_shrunk - K*(1-lambda)*|factor_raw - baseline|
    ("certainty outranks estimation", user-ratified 2026-07-29): a
    full-evidence member keeps their exact measured factor; a thin-evidence
    point estimate is discounted by the unverified share of its distance
    from baseline, so it cannot outrank a fully-measured value at a tier
    boundary. The penalty is computed by fec/engine/primacy_factors.py and shipped
    in the artifact; PRIMACY_UNCERT_K=0 there restores factor_shrunk
    ranking exactly.

    A pre-penalty artifact (no factor_ranked field) falls back to
    factor_shrunk -- the K=0 board -- and the audit pins the artifact's
    uncertainty_penalty_k constant, so a stale artifact fails loud there
    rather than silently seating the wrong members. Missing file fails open
    to {}; malformed fails loud (same posture as load_primacy_factors).
    Consumed by the factor-mode board only: the PRIMACY_BOARD=legxfactor
    revert ranks on factor_shrunk via load_primacy_factors, untouched.
    """
    target = os.path.join(root, path or PRIMACY_PATH)
    if not os.path.exists(target):
        return {}
    try:
        with open(target, encoding='utf-8') as handle:
            doc = json.load(handle)
        return {
            str(member): _finite_primacy_number(
                values['factor_ranked']
                if values.get('factor_ranked') is not None
                else values['factor_shrunk'],
                'ranked primacy factor for %r' % member,
            )
            for member, values in (doc.get('members') or {}).items()
        }
    except Exception as exc:
        raise ValueError(
            'primacy_factors.json is present but invalid; refusing to '
            'treat malformed ranking evidence as absent'
        ) from exc


def load_primacy_baseline(root, path=None):
    """The factor artifact's roster_baseline (G1's B). Fails loud: no default."""
    target = os.path.join(root, path or PRIMACY_PATH)
    with open(target, encoding='utf-8') as handle:
        constants = json.load(handle).get('_constants') or {}
    if constants.get('roster_baseline') is None:
        raise ValueError('primacy_factors.json carries no roster_baseline; '
                         'PRIMACY_GRADE_FORM=deviation cannot score')
    return _finite_primacy_number(constants['roster_baseline'],
                                  'primacy roster baseline')


def load_primacy_cu_credit(root, path=None):
    """Return member -> cu_primary role-credit from the factor mix.

    Same posture as ``load_primacy_factors``: an absent file fails open to
    ``{}`` (the gate then stands down rather than untier the whole roster on
    missing evidence); a present-but-malformed file fails loud.
    """
    target = os.path.join(root, path or PRIMACY_PATH)
    if not os.path.exists(target):
        return {}
    try:
        with open(target, encoding='utf-8') as handle:
            doc = json.load(handle)
        return {
            str(member): _nonnegative_primacy_number(
                (values.get('mix') or {}).get('cu_primary', 0.0),
                'CU-primary credit for %r' % member,
            )
            for member, values in (doc.get('members') or {}).items()
        }
    except Exception as exc:
        raise ValueError(
            'primacy_factors.json is present but invalid; refusing to '
            'treat malformed cu_primary evidence as absent'
        ) from exc


def _read_affirmative_evidence(root, path=None):
    """Validate present values even when the artifact needs a schema seed."""
    target = os.path.join(root, path or PRIMACY_PATH)
    if not os.path.exists(target):
        return {}, True
    with open(target, encoding='utf-8') as handle:
        doc = json.load(handle)
    if not isinstance(doc, dict) or not isinstance(doc.get('members'), dict) or not doc['members']:
        raise ValueError('affirmative primacy artifact must carry a nonempty member map')
    constants = doc.get('_constants', {})
    if not isinstance(constants, dict):
        raise ValueError('affirmative primacy constants must be an object')
    marker = constants.get('affirmative_primary_credit_policy')
    needs_seed = 'affirmative_primary_credit_policy' not in constants
    if not needs_seed and marker != AFFIRMATIVE_PRIMARY_POLICY:
        raise ValueError('unknown affirmative primacy evidence policy')
    out = {}
    for member, values in doc['members'].items():
        if not isinstance(values, dict):
            raise ValueError('invalid affirmative primacy member %r' % member)
        parsed = {}
        for field in (AFFIRMATIVE_PRIMARY_FIELD, 'factor_raw'):
            if field not in values:
                needs_seed = True
                continue
            parsed[field] = _finite_primacy_number(values[field], '%s for %r' % (field, member))
        if parsed.get(AFFIRMATIVE_PRIMARY_FIELD, 0.0) < 0:
            raise ValueError('affirmative CU-primary credit must be nonnegative for %r' % member)
        if len(parsed) == 2:
            out[member] = (parsed[AFFIRMATIVE_PRIMARY_FIELD], parsed['factor_raw'])
    return out, needs_seed


def affirmative_schema_seed_required(root, path=None):
    """Only missing contract fields qualify for producer migration; bad data fails."""
    return _read_affirmative_evidence(root, path)[1]


def load_primacy_affirmative_evidence(root, path=None):
    evidence, needs_seed = _read_affirmative_evidence(root, path)
    if needs_seed:
        raise ValueError('affirmative primacy evidence contract is missing; rebuild '
                         'fec/engine/primacy_factors.py before seating Tier 1')
    return evidence


def load_per_area_grade(root, path=None):
    """Return member -> (chamber, grade_percentile) for the per_area_v1 board.

    Produced by fec/engine/per_area_grade.py. Only these two values reach the
    board; everything else in that artifact is provenance.

    FAILS LOUD on an absent or malformed file, and ``primacy_scores`` fails loud
    again on a rankable member carrying no grade. Same posture, and the same
    reason, as the factor-only board at :396-403: 0.0 is a LIVE score here (it is
    the bottom of the member's chamber), so a neutral default would quietly demote
    exactly the member whose evidence went missing, and an absent artifact would
    quietly collapse the board back to the factor alone with no error. The
    supported way to run without the grade is PRIMACY_BOARD=factor.

    load_primacy_ranked / load_primacy_cu_credit return empty maps on a missing
    file. The ranking caller still rejects a rankable member without a factor;
    an absent credit map preserves the historical gate-stand-down behavior.
    """
    target = os.path.join(root, path or PER_AREA_GRADE_PATH)
    if not os.path.exists(target):
        raise ValueError(
            'per-area grade artifact is missing at %s. The per_area_v1 board '
            'cannot seat without it: run fec/engine/per_area_grade.py, or set '
            'PRIMACY_BOARD=factor to restore the factor-only board.' % target
        )
    try:
        with open(target, encoding='utf-8') as handle:
            doc = json.load(handle)
    except Exception as exc:
        raise ValueError(
            'per_area_grade.json is present but unreadable; refusing to treat '
            'malformed grade evidence as absent'
        ) from exc
    if doc.get('schema') != PER_AREA_GRADE_SCHEMA:
        raise ValueError(
            'per_area_grade.json carries schema %r, expected %r'
            % (doc.get('schema'), PER_AREA_GRADE_SCHEMA)
        )
    out = {}
    for member, values in (doc.get('members') or {}).items():
        try:
            chamber = str(values['chamber'])
            grade = _grade_percentile(values['grade_percentile'], member)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                'per_area_grade.json: %r carries no usable grade_percentile'
                % member
            ) from exc
        if chamber not in ('House', 'Senate'):
            raise ValueError(
                'per_area_grade.json: unknown chamber %r for %r'
                % (chamber, member)
            )
        out[str(member)] = (chamber, grade)
    if not out:
        raise ValueError('per_area_grade.json carries no members')
    return out


def _trunc5(value):
    """Truncate toward zero at 5 decimals, with a 1e-7 representation guard."""
    scaled = value * 100000.0
    eps = 1e-7
    floored = (math.floor(scaled + eps) if scaled >= 0
               else math.ceil(scaled - eps))
    return floored / 100000.0


def _percentrank(values):
    """Share of the population strictly below, ties sharing the first index.

    The repo percentile convention (fec/engine/effectiveness_integration.py:30-38),
    re-derived here so the board takes no dependency on the shadow-comparison
    module; fec/engine/per_area_grade.py holds the identical pair, and
    fec/tests/engine/test_per_area_board.py pins all three against each other.
    """
    count = len(values)
    if count == 0:
        return {}
    if count == 1:
        return {key: 1.0 for key in values}
    ordered = sorted(values.values())
    first = {}
    for index, value in enumerate(ordered):
        first.setdefault(value, index)
    return {key: _trunc5(first[value] / (count - 1))
            for key, value in values.items()}


def within_chamber_percentile(values, chamber_of):
    """Rank House against House and Senate against Senate (D-027).

    A member absent from ``chamber_of`` raises rather than defaulting to a
    chamber: the caller has already failed loud upstream if that can happen.
    """
    out = {}
    for chamber in sorted({chamber_of[key] for key in values}):
        subset = {key: value for key, value in values.items()
                  if chamber_of[key] == chamber}
        out.update(_percentrank(subset))
    return out


def apply_evidence_floor(order, cu_credit, minimum=None, buckets=BUCKETS,
                         exempt=None):
    """Purity ranks, evidence qualifies (user-ratified 2026-07-29).

    Members holding less than ``minimum`` cu_primary role-credit cannot occupy
    a tier seat: they re-seat below the tiers (stable order preserved) and the
    next-highest qualified factors fill the vacated seats. The SCORE is
    untouched -- this is an eligibility gate, not a volume term; reintroducing
    volume into the score was considered and rejected with the directive.

    ``minimum<=0`` disables the gate. An empty ``cu_credit`` map (absent
    evidence file) stands the gate down entirely -- without this, a missing
    file would silently untier every member. ``exempt`` members (explicit
    tier placements, Paul's pins) are never displaced by the gate.
    """
    floors = evidence_slot_floors(order, cu_credit, minimum=minimum,
                                 buckets=buckets, exempt=exempt)
    return _reseat_with_floors(order, floors)


def evidence_slot_floors(order, cu_credit, minimum=None, buckets=BUCKETS,
                         exempt=None):
    """Return eligibility floors without seating, for joint constraint use.

    This has the same evidence and explicit-placement semantics as
    ``apply_evidence_floor``. Callers with conduct restrictions must pass the
    result to ``apply_conduct_ladder`` rather than seating in two passes.
    """
    minimum = _finite_primacy_number(
        PRIMACY_MIN_CUPRIM if minimum is None else minimum,
        'minimum CU-primary credit',
    )
    if minimum <= 0 or not cu_credit:
        return {}
    exempt = exempt or ()
    first_untiered = sum(tuple(buckets))
    return {
        member: first_untiered
        for member in order
        if member not in exempt
        and _finite_primacy_number(cu_credit.get(member, 0.0),
                                  'CU-primary credit for %r' % member) < minimum
    }


def affirmative_slot_floors(order, evidence, minimum=None, buckets=BUCKETS):
    """Tier 1 needs positive primary support and observed net-positive alignment.

    The minimum uses the established CU-primary credit unit. It does not add a
    purity threshold or alter Tier 2/3 qualification. There are no pin exemptions.
    Zero disables the complete new gate, including its artifact requirement.
    """
    minimum = _finite_primacy_number(
        PRIMACY_T1_MIN_AFFIRMATIVE if minimum is None else minimum,
        'minimum affirmative CU-primary credit')
    if minimum < 0:
        raise ValueError('minimum affirmative CU-primary credit must be nonnegative')
    if minimum == 0:
        return {}
    floors = {}
    for member in order:
        if member not in evidence:
            raise ValueError('no affirmative CU-primary evidence for %r' % member)
        credit, raw = evidence[member]
        credit = _finite_primacy_number(credit, 'affirmative CU-primary credit for %r' % member)
        raw = _finite_primacy_number(raw, 'observed primacy factor for %r' % member)
        if credit < 0:
            raise ValueError('affirmative CU-primary credit must be nonnegative for %r' % member)
        if credit < minimum or raw <= 0:
            floors[member] = buckets[0]
    if len(order) < buckets[0]:
        raise ValueError('Infeasible Tier 1 quota: %d seats require at least %d members'
                         % (buckets[0], buckets[0]))
    return floors


def legislative_press_tiers(members, root=None, with_detail=False, *, mode=None,
                            effectiveness_path=None, shadow_root=None):
    """Return the primacy tier used by displays and allocation engines.

    The default remains the legacy ``(tier, tierrank)`` tuple. Opting into
    ``with_detail`` adds the grievance/delivery explanation dictionary.
    """
    root = root or _ROOT
    board_mode = mode or PRIMACY_BOARD
    if board_mode == 'realized_results_v1':
        from engine.effectiveness_integration import load_shadow_comparison, require_shadow_path
        candidate_path = effectiveness_path or os.environ.get('EFFECTIVENESS_COMPARISON_PATH')
        candidate_root = shadow_root or os.environ.get('EFFECTIVENESS_SHADOW_ROOT')
        if not candidate_path:
            raise ValueError('realized_results_v1 requires an explicit shadow comparison')
        if mode is None:
            # Environment selection reaches allocation scripts as well as views.
            # A production-root pipeline must not consume an uncalibrated board.
            require_shadow_path(os.path.join(root,'candidate-generation.json'), candidate_root)
        comparison = load_shadow_comparison(candidate_path, shadow_root=candidate_root)
        return realized_results_tiers(members, comparison, root=root, with_detail=with_detail)
    if board_mode not in ('factor', 'legxfactor', 'per_area_v1'):
        raise ValueError('Unknown primacy board mode: ' + str(board_mode))
    with open(os.path.join(root, 'ranks_python.json'), encoding='utf-8') as handle:
        rp = json.load(handle)
    # Factor mode ranks on the uncertainty-discounted factor_ranked
    # ("certainty outranks estimation"); the legxfactor revert keeps ranking
    # on factor_shrunk so it reproduces the pre-directive board exactly.
    factors = (
        load_primacy_ranked(root)
        if board_mode in ('factor', 'per_area_v1')
        else load_primacy_factors(root)
    )
    breaches = load_open_breaches(root)
    delivered = load_delivery_credit(root)
    grade_pct = None
    chamber_of = None
    if board_mode == 'per_area_v1':
        grade_map = load_per_area_grade(root)
        chamber_of = {member: value[0] for member, value in grade_map.items()}
        grade_pct = {member: value[1] for member, value in grade_map.items()}
    baseline = (load_primacy_baseline(root)
                if board_mode == 'per_area_v1' and PRIMACY_GRADE_FORM == 'deviation'
                else None)
    pcts, _withheld = withhold_roster_arrivals(members, rp.get('pcts', {}), factors, root)
    all_scores = primacy_scores(
        pcts, factors, mode=board_mode, grades=grade_pct,
        baseline=baseline,
    )
    rankable = [member for member in members if member in all_scores]
    if board_mode == 'per_area_v1':
        # D-027 seating: the within-chamber percentile of the board value first,
        # then the raw value, then the name. The percentile is taken over the
        # members actually being seated -- the population the board is a board
        # OF -- which is the display roster every production caller
        # passes. A caller that passes a subset gets that subset's percentiles,
        # exactly as it already gets that subset's 13/24/48 buckets.
        seat_pct = within_chamber_percentile(
            {member: all_scores[member] for member in rankable}, chamber_of
        )
        score_order = sorted(
            rankable,
            key=lambda member: (-seat_pct[member], -all_scores[member], member),
        )
    else:
        score_order = sorted(
            rankable, key=lambda member: (-all_scores[member], member)
        )
    overrides = load_tier_overrides()
    order = apply_tier_overrides(
        score_order,
        overrides,
        positions=load_tier_positions(),
    )
    # Evidence floor ("purity ranks, evidence qualifies"): factor-mode only,
    # so PRIMACY_BOARD=legxfactor restores the entire previous board exactly.
    # Explicit placements are exempt -- a pinned seat beats the gate.
    # per_area_v1 keeps the gate (R4, 2026-09-16). The point-7 prototype did NOT
    # apply it, so ranks below Tier 1 differ from the measured sweep by design:
    # 27 of the tiered 85 sit under 0.75 cu_primary in the ungated board and are
    # re-seated below the tiers here. Tier 1 is unaffected either way -- its
    # minimum cu_primary is 1.75.
    minimum_slots = {}
    if board_mode in ('factor', 'per_area_v1'):
        minimum_slots = evidence_slot_floors(
            order, load_primacy_cu_credit(root), exempt=set(overrides)
        )
        if PRIMACY_T1_MIN_AFFIRMATIVE != 0:
            affirmative = affirmative_slot_floors(
                order, load_primacy_affirmative_evidence(root), buckets=BUCKETS)
            for member, slot in affirmative.items():
                minimum_slots[member] = max(minimum_slots.get(member, 0), slot)
    final, detail = apply_conduct_ladder(
        order, breaches, delivered, buckets=BUCKETS,
        minimum_slots=minimum_slots,
    )
    b1, b2, b3 = _cutoffs(BUCKETS)
    tier = {}
    tierrank = {}
    for index, member in enumerate(final):
        tierrank[member] = index + 1
        tier[member] = (
            1 if index < b1 else
            2 if index < b2 else
            3 if index < b3 else 0
        )
    return (
        (tier, tierrank, detail)
        if with_detail else (tier, tierrank)
    )


def realized_results_tiers(members, comparison, *, root=None, with_detail=False,
                           buckets=BUCKETS):
    """Versioned candidate board with a distinct-measure gate and conduct rules.

    Missing results receive no rank or seat. Ineligible records cannot acquire a
    seat by a fixed override or because the observed candidate roster is small.
    This is deliberately independent of the legacy weighted-credit threshold.
    A short qualified roster that cannot satisfy conduct floors is infeasible;
    it cannot publish compact ranks that contradict their corresponding tiers.
    """
    import math
    from engine.effectiveness_integration import SCHEMA, BOARD_MODE, RESULTS_GATE
    if comparison.get('schema') != SCHEMA or comparison.get('status') != 'shadow_candidate':
        raise ValueError('Invalid realized-results comparison')
    gate = comparison.get('results_evidence_gate', {})
    if gate.get('id') != RESULTS_GATE:
        raise ValueError('Unknown results evidence gate')
    minimum = gate.get('minimum_distinct_signed_measures')
    if not isinstance(minimum, int) or minimum < 1:
        raise ValueError('Results gate requires a positive distinct-measure minimum')
    rows = comparison['members']
    eligible = []
    detail = {}
    for name in members:
        row = rows.get(name, {})
        value = row.get('realized_results')
        count = row.get('distinct_signed_measures')
        available = isinstance(value, (int,float)) and not isinstance(value,bool) and math.isfinite(value)
        qualified = available and isinstance(count,int) and not isinstance(count,bool) and count >= minimum
        if qualified:
            eligible.append(name)
        detail[name] = {'board_mode': BOARD_MODE, 'results_gate': RESULTS_GATE,
                        'results_gate_qualified': qualified, 'distinct_signed_measures': count,
                        'realized_results': value, 'orientation_factor': row.get('orientation_factor'),
                        'coverage': row.get('coverage', {'member_term': 'not_covered'})}
    order = sorted(eligible, key=lambda name: (-rows[name]['realized_results'], name))
    # Overrides are the same named policy, but can only seat observed, qualified
    # members. Never turn missing milestone coverage into an implied result.
    overrides = {name:tier for name,tier in load_tier_overrides().items() if name in eligible}
    positions = {name:position for name,position in load_tier_positions().items() if name in eligible}
    order = apply_tier_overrides(order, overrides, buckets=buckets, positions=positions)
    root = root or _ROOT
    final, conduct = apply_conduct_ladder(order, load_open_breaches(root),
                                        load_delivery_credit(root), buckets=buckets)
    cutoffs = _cutoffs(buckets)
    tiers = dict.fromkeys(members, 0)
    ranks = dict.fromkeys(members, 0)
    for index,name in enumerate(final):
        ranks[name] = index+1
        tier = 1 if index<cutoffs[0] else 2 if index<cutoffs[1] else 3 if index<cutoffs[2] else 0
        # Joint seating has already enforced every conduct ceiling. Tier and
        # compact rank must describe the same seat, including short rosters.
        tiers[name] = tier
        detail[name].update(conduct.get(name, {}))
    return (tiers,ranks,detail) if with_detail else (tiers,ranks)
