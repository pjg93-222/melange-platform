# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT  # centralized root (arch review 2026-07-02)
"""IDENTITY COLLISION CENSUS - read-only diagnostic (Paul-directed 2026-08-18).

WHY THIS EXISTS. The production CU cosponsor graph was found to credit Senator Robert Menendez's
(M000639, NJ, departed 2024) bill sponsorships to his son Representative Robert Menendez
(M001226, NJ-08). The scrape resolves a sponsor string to a roster member; the two share a full
name; the Senator is absent from the 533 and the Representative is present, so the Senator's work
lands on the son. All 9 of M001226's CU out-edges are affected.

THE LOAD-BEARING POINT: a collision census run WITHIN the roster returns ZERO and declares the
graph clean. The dangerous pair is the one the roster CANNOT SEE - a departed or other-chamber
member colliding with a sitting one. So this census deliberately joins the roster against the
HISTORICAL member universe, not just against itself. The same class is a live risk at the 120th
Congress rollover, where a freshman may share a name with a departing member.

WHAT IT REPORTS (four sections, each with named denominators):
  1. Collisions WITHIN the roster: identical full names, and shared surnames under BOTH hyphen
     treatments (see surname() - reporting one number silently picks a matcher model).
  2. Collisions ACROSS the roster boundary: a roster member sharing first+last with a NON-roster
     member who actually sponsors bills. This is the Menendez class.
  3. Whether each such collision is ACTUALLY EXPLOITED in the shipped CU graph, measured against
     BILLSTATUS ground truth, so it reports real damage rather than theoretical risk.
  4. The repo-wide count of CU edges unsupported by BILLSTATUS, bounding total blast radius.

READ-ONLY AND UNGATED BY DESIGN. It prints; it writes nothing and asserts nothing. It is NOT
registered as a test because the CU graph currently FAILS section 3 (the known Menendez defect) and
encoding a known-failure exception into a gate is Paul's call, never a tool's - see change-control
law 7. Arm it as a gate only after the scraper defect is fixed or the exception is explicitly ratified.
UPDATE 2026-10-11 (decision G): the raw CU file still carries the defect, so section 3 still reports
it, but the production readers (load_network, load_network_directed, the CU augmenter, the
bundle's bipartisanship graph) drop M001226's out-edges from that vintage at read time
(moneyball_core.cu_dropped_sponsors, dial CU_IDENTITY_FIX=1; 0 restores them). The two diagnostic
readers behavior_panel.py and persona_homophily_validate.py still read the raw file.

USAGE:  python fec/tools/identity_collision_census.py
  HOLISTIC_BASE=<path>      default: newest holistic_network_BASE_*.xlsx at root. Sections 3-4 need it.
  HOLISTIC_CACHE_DIR=<dir>  default: <root>/data/holistic. Section 2 needs the BILL CACHE, because the
                            holistic GRAPH is roster-filtered and contains zero non-roster sponsors by
                            construction. (Using the graph here produced a silent 'none found' with
                            denominator 0 on 2026-08-18 - caught only because the denominator prints.)
  MEMBER_NAMES=<csv>        default: the BICAM members.csv snapshot; supplies names for NON-roster
                            members, without which section 2 cannot see across the boundary.
Missing inputs SKIP their section with a visible notice; nothing is silently passed.
"""
import os, sys, csv, json, glob, collections, unicodedata, re
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

B = MB_ROOT
BICAM_DEFAULT = r'C:\melange-data\bicam_snapshot\extracted\bicam_members\members.csv'
SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv', 'v', 'md', 'phd'}


def _strip_accents(s):
    s = unicodedata.normalize('NFKD', s or '')
    return ''.join(c for c in s if not unicodedata.combining(c))


def norm_tokens(s):
    toks = re.sub(r'[^a-z ]', ' ', _strip_accents(s).lower()).split()
    return [t for t in toks if t not in SUFFIXES]


def first_last(name):
    t = norm_tokens(name)
    return (t[0], t[-1]) if len(t) >= 2 else (None, None)


def surname(name, split_hyphen=True):
    """Two treatments, because they model two different matchers and BOTH occur in the wild:
      split_hyphen=True  -- 'Miller-Meeks' -> ['miller','meeks'], surname 'meeks'. Models a matcher
                            that tokenizes on punctuation; it COLLIDES Miller-Meeks with Meeks and
                            Hyde-Smith with the five Smiths.
      split_hyphen=False -- 'Miller-Meeks' -> 'millermeeks'. Models a matcher that keeps the
                            hyphenated surname whole; those two collisions disappear.
    Reporting only one number silently picks a matcher model, so this tool reports both."""
    if split_hyphen:
        t = norm_tokens(name)
    else:
        t = [x for x in re.sub(r'[^a-z ]', '', _strip_accents(name).lower()).split()
             if x not in SUFFIXES]
    return t[-1] if t else ''


def load_roster():
    C = json.load(open(os.path.join(B, 'data', 'engine', 'congestion.json'), encoding='utf-8'))
    return {m['bioguide']: m['member'] for m in C if m.get('bioguide')}, C


def newest(pattern):
    fs = glob.glob(os.path.join(B, pattern))
    return max(fs, key=os.path.getmtime) if fs else None


def directed_edges(path):
    import openpyxl
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True)['Edges']
    out = collections.defaultdict(set)
    for r in ws.iter_rows(min_row=2, values_only=True):
        if r and r[0] and r[1]:
            out[r[0]].add(r[1])
    return out


def main():
    roster, rows = load_roster()
    print('IDENTITY COLLISION CENSUS | roster %d members (%d with bioguide)' % (len(rows), len(roster)))

    # ---------------- 1 ----------------
    print('\n[1] COLLISIONS WITHIN THE ROSTER (denominator: %d members)' % len(roster))
    by_full = collections.defaultdict(list)
    for b, n in roster.items():
        by_full[tuple(norm_tokens(n))].append((b, n))
    full_dups = {k: v for k, v in by_full.items() if len(v) > 1}
    print('    identical full names : %d' % len(full_dups))
    for k, v in sorted(full_dups.items()):
        print('        %s' % ' | '.join('%s (%s)' % (n, b) for b, n in v))

    groups = {}
    for split in (False, True):
        d = collections.defaultdict(list)
        for b, n in roster.items():
            d[surname(n, split)].append((b, n))
        groups[split] = {k: v for k, v in d.items() if len(v) > 1}
        print('    shared surnames, hyphenated %-5s : %d surnames covering %d members'
              % ('SPLIT' if split else 'WHOLE', len(groups[split]),
                 sum(len(v) for v in groups[split].values())))
    for k in sorted(groups[False]):
        print('        %-14s %s' % (k, ', '.join('%s (%s)' % (n, b) for b, n in groups[False][k])))
    extra = sorted(set(groups[True]) - set(groups[False]))
    merged = sorted(k for k in groups[True]
                    if k in groups[False] and len(groups[True][k]) > len(groups[False][k]))
    if extra or merged:
        print('    ONLY when a matcher splits hyphenated surnames:')
        for k in extra:
            print('        %-14s %s' % (k, ', '.join('%s (%s)' % (n, b) for b, n in groups[True][k])))
        for k in merged:
            added = {b for b, _ in groups[True][k]} - {b for b, _ in groups[False][k]}
            print('        %-14s +%s joins the existing group'
                  % (k, ', '.join(n for b, n in groups[True][k] if b in added)))
    print('    NOTE: a first-name-blind matcher collides on every surname above.')

    # ---------------- inputs for 2-4 ----------------
    hol = os.environ.get('HOLISTIC_BASE') or newest('holistic_network_BASE_*.xlsx')
    names_csv = os.environ.get('MEMBER_NAMES', BICAM_DEFAULT)
    cache_dir = os.environ.get('HOLISTIC_CACHE_DIR', os.path.join(B, 'data', 'holistic'))
    caches = sorted(glob.glob(os.path.join(cache_dir, 'billcache_*.ndjson')))

    hist = {}
    if os.path.exists(names_csv):
        with open(names_csv, encoding='utf-8', errors='replace', newline='') as fh:
            for row in csv.DictReader(fh):
                bid = row.get('bioguide_id')
                if bid:
                    hist[bid] = row.get('direct_order_name') or row.get('normalized_name') or ''

    # ---------------- 2 ----------------
    print('\n[2] COLLISIONS ACROSS THE ROSTER BOUNDARY  <-- the Menendez class')
    print('    (roster member sharing first+last with a NON-roster member who SPONSORS bills)')
    pairs = []
    if not caches:
        print('    SKIPPED (not passed): no billcache_*.ndjson under %s.' % cache_dir)
        print('    The roster-filtered graph cannot answer this - it holds no non-roster sponsors.')
    elif not hist:
        print('    SKIPPED (not passed): no member-name source at %s.' % names_csv)
    else:
        sponsors = collections.Counter()
        for cf in caches:
            for line in open(cf, encoding='utf-8'):
                sp = json.loads(line).get('sponsor')
                if sp:
                    sponsors[sp] += 1
        roster_fl = collections.defaultdict(list)
        for b, n in roster.items():
            roster_fl[first_last(n)].append((b, n))
        offr = [b for b in sponsors if b not in roster and hist.get(b)]
        for b in offr:
            for rb, rn in roster_fl.get(first_last(hist[b]), []):
                pairs.append((hist[b], b, sponsors[b], rn, rb))
        print('    denominator: %d non-roster sponsors with a known name, over %d bill-cache files'
              % (len(offr), len(caches)))
        if not pairs:
            print('    none found')
        for nm, b, deg, rn, rb in sorted(pairs, key=lambda x: -x[2]):
            print('    NON-ROSTER %-24s %s (%4d sponsored bills)  ==NAME==  ROSTER %-24s %s'
                  % (nm, b, deg, rn, rb))

    # ---------------- 3-4 ----------------
    if not hol or not os.path.exists(hol):
        print('\n[3-4] SKIPPED (not passed): no holistic_network_BASE_*.xlsx and HOLISTIC_BASE unset.')
        return
    cu_files = [f for f in glob.glob(os.path.join(B, 'congress_cosponsor_network_*.xlsx'))
                if 'AUG' not in os.path.basename(f).upper()]
    if not cu_files:
        print('\n[3-4] SKIPPED (not passed): no CU base workbook at %s.' % B)
        return
    cu = os.environ.get('CU_BASE') or max(cu_files, key=os.path.getmtime)
    hol_out = directed_edges(hol)
    cu_out = directed_edges(cu)
    print('\n    ground truth: %s | CU base: %s' % (os.path.basename(hol), os.path.basename(cu)))

    print('\n[3] IS EACH COLLISION ACTUALLY EXPLOITED?')
    if not pairs:
        print('    (no across-boundary collisions to test)')
    for nm, b, deg, rn, rb in sorted(set(pairs), key=lambda x: -x[2]):
        cu_e = cu_out.get(rb, set())
        unsup = cu_e - hol_out.get(rb, set())
        print('    roster %-24s %s : CU out-edges %3d | unsupported by BILLSTATUS %3d%s'
              % (rn, rb, len(cu_e), len(unsup), '  <-- MISATTRIBUTION' if unsup else ''))

    print('\n[4] TOTAL BLAST RADIUS: CU roster-internal edges unsupported by BILLSTATUS')
    tot = unsup_tot = 0
    who = collections.Counter()
    for a, tgts in cu_out.items():
        if a not in roster:
            continue
        for t in tgts:
            if t not in roster:
                continue
            tot += 1
            if t not in hol_out.get(a, set()):
                unsup_tot += 1
                who[a] += 1
    print('    %d of %d roster-internal CU edges unsupported (%.2f%%)'
          % (unsup_tot, tot, (100.0 * unsup_tot / tot) if tot else 0.0))
    for a, n in who.most_common(10):
        print('        %-9s %-26s %d unsupported out-edges' % (a, roster.get(a, '?'), n))
    print('\n    Reading: an unsupported edge is one BILLSTATUS does not corroborate. A handful spread')
    print('    across many members suggests vintage drift; a block concentrated on ONE member is the')
    print('    signature of an identity misattribution.')


if __name__ == '__main__':
    main()
