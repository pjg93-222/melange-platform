"""Absolute workplace exposure; independent of row-normalized centralities.

All intervals are half-open. IDs are supplied by the existing identity layer.
Observed concurrent headcount is a census of the supplied evidence, not a claim
of complete staffing coverage. No missing dates, assignments or teams are made up.
"""
from bisect import bisect_right
from collections import defaultdict, Counter
from datetime import date
import math

from fec.staffer_network.ranking_math import eligible_container, shared_role
from fec.surfaces.staffer_network_scope import intern_title
from fec.staffer_network.relationship_uncertainty import uncertainty

VERSION = 'staffer-closeness-exposure-v3-overlap-runs'
TAU = 365.2425 / 19
ANCHOR_TAU = 365.2425 / 20
GAMMA = 1.0
HOP_FACTOR = .8
MEMBER_BETA = .15
SHARED_FACTORS = {'house': .02, 'senate': .05, 'unknown': .02}
HEADCOUNT_SEMANTICS = 'distinct concurrently observed people; incomplete source coverage can undercount actual staff'


def ordinal(value):
    if isinstance(value, int):
        return value
    if isinstance(value, date):
        return value.toordinal()
    return date.fromisoformat(str(value)[:10]).toordinal()


def union(runs):
    out = []
    for start, end in sorted(runs):
        if end <= start:
            continue
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(end, out[-1][1]))
        else:
            out.append((start, end))
    return out


def bounded_strength(exposure, tier=1., tau=TAU):
    if not all(math.isfinite(v) for v in (exposure, tier, tau)) or exposure < 0 or not 0 <= tier <= 1 or tau <= 0:
        raise ValueError('invalid exposure, identity strength or time scale')
    return tier * -math.expm1(-exposure / tau)


def overlap_support(segments):
    """Weight the last day of each continuous joint run by its own exposure.

    Allocation/headcount boundaries must not age a still-continuous contact.
    A later, disconnected token overlap must not refresh all earlier exposure.
    """
    runs = union((s, e) for s, e, weight in segments if weight > 0)
    starts = [s for s, e in runs]
    total = sum(weight for s, e, weight in segments)
    weighted_day = sum(weight * (runs[bisect_right(starts, s)-1][1]-1)
                       for s, e, weight in segments if weight > 0) / total
    return {'overlap_intervals': runs, 'last_overlap_day': runs[-1][1]-1,
            'exposure_weighted_last_overlap_day': weighted_day}


class Census:
    """Piecewise concurrent distinct-person counts and their reciprocal integrals."""
    def __init__(self, calendars, gamma=GAMMA):
        events = Counter()
        for runs in calendars:
            for start, end in union(runs):
                events[start] += 1
                events[end] -= 1
        self.days = sorted(events)
        self.counts, self.peer_prefix, self.member_prefix = [], [0.], [0.]
        count = 0
        for k, day in enumerate(self.days):
            count += events[day]
            self.counts.append(count)
            if k + 1 < len(self.days):
                duration = self.days[k + 1] - day
                self.peer_prefix.append(self.peer_prefix[-1] + duration / max(1, count - 1)**gamma)
                self.member_prefix.append(self.member_prefix[-1] + duration / max(1, count)**gamma)
        self.gamma = gamma

    def _at(self, day, member):
        if not self.days or day <= self.days[0]:
            return 0.
        k = min(bisect_right(self.days, day) - 1, len(self.days) - 1)
        prefix = self.member_prefix if member else self.peer_prefix
        divisor = max(1, self.counts[k] - (0 if member else 1))**self.gamma
        return prefix[k] + (day - self.days[k]) / divisor

    def integral(self, start, end, member=False):
        return max(0., self._at(end, member) - self._at(start, member))

    def range(self, start, end):
        first = max(0, bisect_right(self.days, start) - 1)
        last = bisect_right(self.days, end - 1)
        values = self.counts[first:last]
        return (min(values), max(values)) if values else (0, 0)


class ExposureIndex:
    def __init__(self, episodes, metadata=None, census_episodes=(), tiers=None, gamma=GAMMA):
        if not math.isfinite(gamma) or gamma < 1:
            raise ValueError('gamma must be finite and >= 1 to conserve daily exposure')
        self.metadata = metadata or {}
        self.tiers = tiers or {}
        self.cells = defaultdict(dict)
        self.people_by_workplace = defaultdict(set)
        self.census = {}
        self.rejected = 0
        self.gamma = gamma
        raw, counts = defaultdict(list), defaultdict(list)
        for source, is_census in ((episodes, False), (census_episodes, True)):
            for row in source:
                p, c = row.get('person_id'), row.get('employer_id')
                meta = self.metadata.get(c, {})
                kind = row.get('container_kind') or meta.get('container_kind', 'unresolved')
                if not p or not c or not eligible_container(kind, c) or str(c).startswith(('activity:', 'assertion:')):
                    continue
                try:
                    start, end = ordinal(row.get('start')), ordinal(row.get('end_exclusive'))
                except (ValueError, TypeError):
                    self.rejected += 1
                    continue
                if end <= start:
                    self.rejected += 1
                    continue
                counts[p, c].append((start, end))
                if is_census or intern_title(row.get('title') or row.get('title_last')):
                    continue
                chamber = row.get('chamber') or meta.get('chamber')
                if not chamber:
                    chamber = 'house' if ':house:' in str(c) else 'senate' if ':senate:' in str(c) else 'unknown'
                title = row.get('title') or row.get('title_last') or ''
                q = SHARED_FACTORS.get(chamber, SHARED_FACTORS['unknown']) if shared_role(title) else 1.
                raw[p, c].append((start, end, q))
        by_workplace = defaultdict(list)
        for (p, c), runs in counts.items():
            by_workplace[c].append(runs)
        for c, calendars in by_workplace.items():
            self.census[c] = Census(calendars, gamma)
        # One active allocation per distinct workplace, even with duplicate rows.
        # q is attached to its dated cell, never to the person's whole career.
        events_by_person = defaultdict(lambda: defaultdict(list))
        for (p, c), runs in raw.items():
            for start, end, q in runs:
                events_by_person[p][start].append((c, q, 1))
                events_by_person[p][end].append((c, q, -1))
        for p, events in events_by_person.items():
            active = defaultdict(Counter)
            days = sorted(events)
            for k, day in enumerate(days[:-1]):
                for c, q, delta in events[day]:
                    active[c][q] += delta
                    if active[c][q] == 0:
                        del active[c][q]
                    if not active[c]:
                        del active[c]
                if not active:
                    continue
                # Prefer explicit documented child teams to their parent for the
                # same day, only where metadata actually supplies that hierarchy.
                parents = {self.metadata.get(c, {}).get('parent_workplace_id') for c in active}
                work = [c for c in active if c not in parents]
                if not work:
                    raise ValueError('cyclic workplace hierarchy')
                for c in work:
                    allocation = max(active[c]) / len(work)
                    segments = self.cells[p].setdefault(c, [])
                    end = days[k + 1]
                    if segments and segments[-1][1] == day and segments[-1][2] == allocation:
                        segments[-1] = (segments[-1][0], end, allocation)
                    else:
                        segments.append((day, end, allocation))
                    self.people_by_workplace[c].add(p)
        self._anchors = {}

    def tier(self, person):
        return self.tiers.get(person, 1.)

    def kappa(self, c):
        meta = self.metadata.get(c, {})
        if meta.get('workplace_kappa') is not None:
            value = float(meta['workplace_kappa'])
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError('invalid workplace kappa')
            return value
        return {'member_office': 1., 'committee': .6, 'leadership': .75}.get(meta.get('container_kind'), .5)

    def neighbors(self, person):
        found = set()
        for c in self.cells.get(person, {}):
            found.update(self.people_by_workplace[c])
        found.discard(person)
        return found

    def context_group(self, workplace):
        seen = set()
        while self.metadata.get(workplace, {}).get('parent_workplace_id'):
            if workplace in seen:
                raise ValueError('cyclic workplace hierarchy')
            seen.add(workplace)
            workplace = self.metadata[workplace]['parent_workplace_id']
        return workplace

    def overlaps(self, left, right):
        """Whether two people stood in one workplace together for at least a day: exactly the case pair() can find exposure in.

        The merge below is the one pair() runs over each shared workplace's segments (sorted, never overlapping within
        one person), so it finds an intersection of positive length if and only if pair() does. When it finds none, every
        workplace's exposure is 0, the total is 0 and pair()['strength'] is 0, so a caller that only wants positive
        strengths may skip pair() for the two people. A True answer promises nothing: a workplace whose kappa is 0
        overlaps and still yields no strength.
        """
        a, b = self.cells.get(left, {}), self.cells.get(right, {})
        if len(a) > len(b):
            a, b = b, a
        for c, aa in a.items():
            bb = b.get(c)
            if bb is None:
                continue
            i, j, n, m = 0, 0, len(aa), len(bb)
            while i < n and j < m:
                x, y = aa[i], bb[j]
                if min(x[1], y[1]) > max(x[0], y[0]):
                    return True
                if x[1] <= y[1]:
                    i += 1
                else:
                    j += 1
        return False

    def pair_row(self, left, right):
        """The part of pair() that a hop row reads, from the same float operations in the same order.

        Returns {'strength', 'workplaces'}: each workplace with positive exposure carries employer_id,
        effective_exposure_days, last_overlap_day, exposure_weighted_last_overlap_day and context_group, which is all that
        uncertainty() and evidence_contexts() read. What pair() adds (headcount ranges, durations, strength shares,
        labels) is evidence for a route that has been chosen, and _full_evidence still builds it with pair(). Every number
        here is bit-identical to pair()'s (tests/surfaces/test_staffer_row_equality.py); the saving is work not done.
        """
        if left == right:
            raise ValueError('self exposure is not a relationship')
        a, b = self.cells.get(left, {}), self.cells.get(right, {})
        support, total = [], 0.
        for c in sorted(a.keys() & b.keys()):
            aa, bb, i, j = a[c], b[c], 0, 0
            exposure, contributions, factor, census = 0., [], None, None
            while i < len(aa) and j < len(bb):
                start, end = max(aa[i][0], bb[j][0]), min(aa[i][1], bb[j][1])
                if end > start:
                    if census is None:
                        factor, census = self.kappa(c), self.census[c]
                    amount = factor * aa[i][2] * bb[j][2] * census.integral(start, end)
                    exposure += amount
                    contributions.append((start, end, amount))
                if aa[i][1] <= bb[j][1]:
                    i += 1
                else:
                    j += 1
            if exposure > 0:
                total += exposure
                last = overlap_support(contributions)
                support.append({'employer_id': c, 'effective_exposure_days': exposure,
                                'last_overlap_day': last['last_overlap_day'],
                                'exposure_weighted_last_overlap_day': last['exposure_weighted_last_overlap_day'],
                                'context_group': self.context_group(c)})
        return {'strength': bounded_strength(total, min(self.tier(left), self.tier(right))), 'workplaces': support}

    def pair(self, left, right):
        if left == right:
            raise ValueError('self exposure is not a relationship')
        a, b = self.cells.get(left, {}), self.cells.get(right, {})
        support, all_days, total = [], [], 0.
        for c in sorted(a.keys() & b.keys()):
            aa, bb, i, j = a[c], b[c], 0, 0
            exposure, runs, sizes, contributions = 0., [], [], []
            while i < len(aa) and j < len(bb):
                start, end = max(aa[i][0], bb[j][0]), min(aa[i][1], bb[j][1])
                if end > start:
                    amount = self.kappa(c) * aa[i][2] * bb[j][2] * self.census[c].integral(start, end)
                    exposure += amount
                    contributions.append((start, end, amount))
                    runs.append((start, end))
                    sizes.extend(self.census[c].range(start, end))
                if aa[i][1] <= bb[j][1]:
                    i += 1
                else:
                    j += 1
            if exposure > 0:
                total += exposure
                all_days.extend(runs)
                support.append({'employer_id': c, 'effective_exposure_days': exposure,
                                **overlap_support(contributions),
                                'context_group': self.context_group(c),
                                'overlap_duration_days': sum(e-s for s,e in union(runs)),
                                'headcount_min': min(sizes), 'headcount_max': max(sizes),
                                'kappa': self.kappa(c)})
        weight = bounded_strength(total, min(self.tier(left), self.tier(right)))
        for row in support:
            row['strength_contribution'] = weight * row['effective_exposure_days'] / total
        return {'strength': weight, 'effective_exposure_days': total,
                'overlap_duration_days': sum(e-s for s,e in union(all_days)),
                'workplaces': support, 'headcount_semantics': HEADCOUNT_SEMANTICS,
                'allocation_semantics': 'equal allocation across concurrent distinct workplaces; product models co-presence, schedules unknown',
                'model': VERSION}

    def office_uncertainty(self, person, office, as_of):
        segments = self.cells.get(person, {}).get(office, [])
        contributions = [(s, e, a * self.kappa(office) * self.census[office].integral(s,e,member=True))
                         for s,e,a in segments]
        exposure = sum(amount for s,e,amount in contributions)
        support = [{'employer_id': office, 'context_group': self.context_group(office),
                    'effective_exposure_days': exposure,
                    **overlap_support(contributions)}] if exposure else []
        return uncertainty(support, as_of)

    def adjusted_member_anchors(self, person, as_of):
        result = {}
        for c, strength in self.office_anchors(person).items():
            meta = self.metadata.get(c, {})
            member = meta.get('member_bioguide')
            if meta.get('container_kind') == 'member_office' and member:
                adjusted = strength * self.office_uncertainty(person, c, as_of)['factor']
                result[member] = max(result.get(member, 0.), adjusted)
        return result

    def office_anchors(self, person):
        """Direct employment anchors only; committee membership never fans out."""
        if person not in self._anchors:
            result = {}
            for c, segments in self.cells.get(person, {}).items():
                exposure = sum(a * self.kappa(c) * self.census[c].integral(s, e, member=True) for s, e, a in segments)
                result[c] = bounded_strength(exposure, self.tier(person), ANCHOR_TAU)
            self._anchors[person] = result
        return self._anchors[person]

    def member_anchors(self, person):
        result = defaultdict(float)
        for c, value in self.office_anchors(person).items():
            meta = self.metadata.get(c, {})
            member = meta.get('member_bioguide')
            if meta.get('container_kind') == 'member_office' and member:
                # Aliases of a member office cannot accumulate duplicate anchors.
                result[member] = max(result[member], value)
        return dict(result)
