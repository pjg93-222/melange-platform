# -*- coding: utf-8 -*-
"""Single source of truth for the member roster, its size and the workbook member rows (roster lane, 2026-10-20).

Paul, 2026-10-20: the roster goes to 535 now and "this needs to always auto-update".  Before this module, 64 production
files hardcoded 533 and 27 hardcoded the workbook window rows 3..535.  They now ask this module instead.

    import roster
    roster.count()                      # current members (535 after the 2026-10-20 refresh)
    roster.names()                      # display names, the key every workbook sheet and congestion.json join on
    roster.rows('Rank & Allocation')    # range(3, 3 + count()): that sheet's member rows
    roster.footer_row('Press Release - Raw')   # the 'Weights' row right under the press member block

The artifact is data/engine/roster.json, written ONLY by fec/collect/refresh_roster.py (the roster refresh step) from a
commit-pinned unitedstates/congress-legislators snapshot cross-checked against clerk.house.gov and senate.gov:

    {"schema": "melange-roster-v1", "as_of": ..., "source": {...snapshot sha, manifest sha...},
     "members":  [{"member", "bioguide", "chamber", "state", "district", "party", "term_start", "terms": [...], ...}],
     "departed": [{"member", "bioguide", ..., "departed_on", "reason", "terms": [...]}]}

Departed members stay in "departed" as history (never hard-deleted); "terms" are [{"chamber", "start", "end", "state"}]
so date-aware consumers (the CREC speaker resolver) can tell same-surname successors apart.
A missing or malformed artifact raises: nothing may fall back to a remembered 533.
"""
import json
import os

SCHEMA = 'melange-roster-v1'

# Scope (unchanged rule, now explicit): every senator, every voting representative, and the delegates of DC and Puerto Rico.
# The four territorial delegates (US Virgin Islands, American Samoa, Guam, Northern Mariana Islands) are outside the model.
DELEGATES_IN = frozenset({'DC', 'PR'})
TERRITORIES_OUT = frozenset({'VI', 'AS', 'GU', 'MP'})

# First member row of each member-indexed workbook sheet; its block is count() rows long.  Sheets with a footer keep it on the
# row right after the block (footer_row).
FIRST_ROW = {
    'Rank & Allocation': 3, 'Blended Model': 3, 'IM Allocation': 3, 'Congestion': 3,
    'Primary Layer': 3, 'Primary Layer - Normalized': 3,
    'Legislative Score - Raw': 3, 'Legislative Score - Normalized': 3,
    'Sub Layer - Raw': 4, 'Sub Layer - Normalized': 4,
    'Demographic Factors - Raw': 2, 'Demographic - Normalized': 2,
    'Press Release - Raw': 2, 'Press Release - Normalized': 2,
    'Category Stance': 2,
}
FOOTER_SHEETS = frozenset({'Press Release - Raw', 'Press Release - Normalized', 'Demographic - Normalized'})

_CACHE = {}


def in_scope(chamber, state):
    """The roster's membership rule for one seat: chamber 'sen'/'senate' or a House seat outside the four territories."""
    ch = str(chamber or '').lower()
    if ch in ('sen', 'senate'):
        return True
    if ch in ('rep', 'house'):
        return str(state or '').upper() not in TERRITORIES_OUT
    raise ValueError('unknown chamber %r' % chamber)


def _root(root):
    if root is None:
        try:
            import paths
        except ImportError:          # imported as fec.roster without fec/ on sys.path
            from fec import paths
        root = paths.ROOT
    return root


def path(root=None):
    return os.path.join(_root(root), 'data', 'engine', 'roster.json')


def reference_dir(root=None):
    return os.path.join(_root(root), 'data', 'staffer_network', 'reference', 'congress-legislators')


def pointer_path(root=None):
    return os.path.join(reference_dir(root), 'CURRENT')


def snapshot_dir(root=None):
    """The installed commit-pinned congress-legislators snapshot: the one CURRENT names (written by the roster refresh), or,
    before the first refresh, the only snapshot directory there is.  Older snapshots stay on disk as provenance."""
    ref = reference_dir(root)
    p = pointer_path(root)
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            d = os.path.join(ref, f.read().strip())
        if not os.path.isfile(os.path.join(d, 'source_manifest.json')):
            raise FileNotFoundError('%s names %s, which is not an installed snapshot' % (p, d))
        return d
    dirs = sorted(x for x in os.listdir(ref) if len(x) == 40 and os.path.isdir(os.path.join(ref, x)))
    if len(dirs) != 1:
        raise RuntimeError('%s has no CURRENT pointer and %d snapshot directories' % (ref, len(dirs)))
    return os.path.join(ref, dirs[0])


def load(root=None):
    """The roster artifact (cached per path and mtime).  Raises if it is missing or not a melange-roster-v1 document."""
    p = path(root)
    key = (p, os.stat(p).st_mtime_ns)
    if key not in _CACHE:
        with open(p, encoding='utf-8') as f:
            doc = json.load(f)
        if doc.get('schema') != SCHEMA or not isinstance(doc.get('members'), list) or not doc['members']:
            raise ValueError('%s is not a %s roster with members' % (p, SCHEMA))
        seen = [m['member'] for m in doc['members']]
        if len(set(seen)) != len(seen) or len({m['bioguide'] for m in doc['members']}) != len(seen):
            raise ValueError('%s repeats a member name or bioguide' % p)
        _CACHE.clear()
        _CACHE[key] = doc
    return _CACHE[key]


def members(root=None):
    return load(root)['members']


def departed(root=None):
    return load(root).get('departed') or []


# An arrival is flagged thin_evidence by the roster refresh, or seated on a term that began after the 119th Congress opened
# (members/communication_style.py applies the same rule): an artifact built before the refresh cannot know these members.
ARRIVAL_AFTER = '2025-01-03'


def arrivals(root=None):
    return {m['member'] for m in members(root)
            if m.get('thin_evidence') or str(m.get('term_start') or '') > ARRIVAL_AFTER}


def drift(current, covered, root=None):
    """How an artifact built before the last roster refresh may differ from the `current` names without being wrong.

    Returns (withheld, dropped, bad_missing, bad_extra): roster arrivals the artifact does not cover yet; departed members it still
    carries; and every other missing or extra name, which is a real mismatch the caller must fail on."""
    current, covered = set(current), set(covered)
    new, gone = arrivals(root), {d['member'] for d in departed(root)}
    lacking, extra = current - covered, covered - current
    return lacking & new, extra & gone, lacking - new, extra - gone


def names(root=None):
    return [m['member'] for m in members(root)]


def bioguides(root=None):
    return [m['bioguide'] for m in members(root)]


def count(root=None):
    return len(members(root))


def chamber_counts(root=None):
    out = {'House': 0, 'Senate': 0}
    for m in members(root):
        out[m['chamber']] += 1
    return out


def first_row(sheet='Rank & Allocation'):
    return FIRST_ROW[sheet]


def last_row(sheet='Rank & Allocation', root=None):
    return FIRST_ROW[sheet] + count(root) - 1


def rows(sheet='Rank & Allocation', root=None):
    """The member rows of a workbook sheet: range(first, first + count())."""
    return range(FIRST_ROW[sheet], FIRST_ROW[sheet] + count(root))


def footer_row(sheet, root=None):
    if sheet not in FOOTER_SHEETS:
        raise KeyError('%s has no footer row under its member block' % sheet)
    return last_row(sheet, root) + 1


def col_range(col, sheet='Rank & Allocation', root=None, absolute=True):
    """An A1 range over a sheet's member rows, e.g. col_range('G') -> '$G$3:$G$537'."""
    d = '$' if absolute else ''
    return '%s%s%s%d:%s%s%s%d' % (d, col, d, first_row(sheet), d, col, d, last_row(sheet, root))
