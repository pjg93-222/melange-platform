"""Duration-attributed exploratory ranking rules, versioned independently of identity.

These functions consume resolved person/container IDs; they never match names.
Shared-role damping is a modeling choice, not a calibrated contact probability.
"""
import re

import numpy as np

VERSION = 'staffer-ranking-duration-v1'
SHARED_ROLE_FACTOR = .05
SHARED_TITLES = frozenset({'SHARED EMPLOYEE', 'SHARED EMPLOYEE (OTHER COMPENSATION)'})


def shared_role(title):
    return ' '.join(str(title or '').upper().split()) in SHARED_TITLES


def eligible_container(kind, key):
    # Match the payroll header, not words such as INTERNATIONAL in a title.
    key = re.sub(r'^\d{4}\s+', '', str(key or '').strip().upper())
    return kind != 'admin' and not re.match(r'^INTERN\s+COMPENSATION(?:\s|[-:–—]|$)', key)


def attributed_support(ptr, days, shared, factor=SHARED_ROLE_FACTOR):
    """Return edge attenuation and within-edge mass shares in CSR container order.

    Days are unioned within each container first. Concurrent calendars across
    different containers may overlap: dividing by their sum conserves one edge's
    mass. Its duration transform still uses the cross-container UNION, never sum.
    """
    ptr = np.asarray(ptr, dtype=np.int64)
    days = np.asarray(days, dtype=float)
    shared = np.asarray(shared, dtype=bool)
    if not np.isfinite(factor) or not 0 < factor <= 1:
        raise ValueError('shared-role factor must be finite in (0, 1]')
    if (ptr.ndim != 1 or not ptr.size or ptr[0] != 0 or ptr[-1] != days.size
            or np.any(np.diff(ptr) <= 0) or shared.shape != days.shape
            or days.ndim != 1 or np.any(~np.isfinite(days)) or np.any(days <= 0)):
        if ptr.tolist() == [0] and days.size == 0 and shared.size == 0:
            return np.zeros(0), np.zeros(0)
        raise ValueError('invalid positive container-day support')
    effective = days * np.where(shared, factor, 1.)
    totals = np.add.reduceat(days, ptr[:-1])
    effective_totals = np.add.reduceat(effective, ptr[:-1])
    attenuation = effective_totals / totals
    shares = effective / np.repeat(effective_totals, np.diff(ptr))
    return attenuation, shares


def co_tenure_weight(days, kappa, tier, attenuation=1., tau=365.2425):
    """The producer's scalar/vector weight path; no alternate tested-only formula."""
    days, kappa, tier, attenuation = np.broadcast_arrays(
        np.asarray(days, dtype=float), kappa, tier, attenuation)
    if (not np.isfinite(tau) or tau <= 0 or np.any(~np.isfinite(days))
            or np.any(days < 0) or any(np.any(~np.isfinite(x)) or np.any(x < 0)
                                     or np.any(x > 1) for x in (kappa, tier, attenuation))):
        raise ValueError('invalid co-tenure weight inputs')
    return kappa * tier * (-np.expm1(-days / tau)) * attenuation


def rank_outreach_candidates(candidates, origin_costs, target_costs):
    """Break equal published PageRank by origin -> candidate -> target route cost.

    Cost maps must come from a completed search over the same eligible graph and
    revision. Missing/unreachable endpoints have infinite cost. An incomplete
    search must not be supplied as evidence of no route. Equal numeric keys are
    explicitly returned as ties; IDs provide stable display order only.
    """
    def key(row):
        ident = row['node_id']
        left = float(origin_costs.get(ident, float('inf')))
        right = float(target_costs.get(ident, float('inf')))
        if np.isnan(left) or np.isnan(right) or min(left, right) < 0:
            raise ValueError('route costs must be nonnegative and not NaN')
        return (-row['pagerank_085'], left + right, max(left, right))
    ordered = sorted(candidates, key=lambda row: (*key(row), row['node_id']))
    keys = [key(row) for row in ordered]
    counts = {k: keys.count(k) for k in set(keys)}
    return [dict(row, outreach_tie_size=counts[key(row)],
                 outreach_route_cost=None if not np.isfinite(key(row)[1]) else key(row)[1])
            for row in ordered]
