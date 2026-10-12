# -*- coding: utf-8 -*-
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); from paths import ROOT as MB_ROOT
"""DOMAIN-BASED ATTRIBUTION RESOLVER (2026-07-04, user rule: "cross reference the link for it —
if it is from a member's website its theirs, anything else is third party").

Replaces hand-reading attribution_gold_sheet.csv's 60 excerpts with a mechanical rule: resolve
each row's source URL (title-matched against the press tone csv, same join key the production
reader uses) and apply the domain test already ratified for press channel-weighting in
build_media_blend.py (own .house.gov/.senate.gov -> full attribution; anything else -> third
party). This is a 2-CLASS ground truth (own / thirdparty) — it cannot distinguish "reported an
action" from "voiced an opinion" the way the LLM reader's 3-class field does; that's the
simplification the user asked for, not a bug.

Writes back into attribution_gold_sheet.csv (never overwrites a row the user already hand-labeled):
  your_attribution_own_action_thirdparty : 'own' | 'thirdparty' (blank if url unresolved)
  resolved_url    : the matched url, for the user's own cross-reference / audit
  resolution      : 'domain-rule' | 'user-labeled' (already filled) | 'unresolved'

Usage: python resolve_attribution_domain.py [--write]   (dry-run without --write)"""
import csv, io, os, sys, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = MB_ROOT
SHEET = B + r'\data\stance\attribution_gold_sheet.csv'
TONE = os.path.join(os.path.dirname(B), 'Draft Systems', 'cu_press_stance_tone.csv')

def norm_title(t): return ' '.join((t or '').lower().split())

rows = list(csv.DictReader(io.open(SHEET, encoding='utf-8-sig')))
fieldnames = list(rows[0].keys()) if rows else []
for extra in ('resolved_url', 'resolution'):
    if extra not in fieldnames: fieldnames.append(extra)

by_member_title = {}
by_title = collections.defaultdict(list)
if os.path.exists(TONE):
    for r in csv.DictReader(io.open(TONE, encoding='utf-8-sig', newline='')):
        t = norm_title(r.get('title')); u = (r.get('url') or '').strip()
        m = (r.get('member') or '').strip()
        if not t or not u: continue
        by_member_title[(m, t)] = u
        by_title[t].append(u)
else:
    print('!! press tone csv not found at %s — cannot resolve any row' % TONE)

def domain_class(url):
    ul = url.lower()
    return 'own' if ('.house.gov' in ul or '.senate.gov' in ul) else 'thirdparty'

n_user = n_resolved = n_unresolved = n_own = n_third = 0
for r in rows:
    existing = (r.get('your_attribution_own_action_thirdparty') or '').strip()
    if existing:
        n_user += 1; r['resolution'] = r.get('resolution') or 'user-labeled'; continue
    t = norm_title(r.get('title')); m = (r.get('member') or '').strip()
    url = by_member_title.get((m, t))
    if not url:
        cands = by_title.get(t, [])
        url = cands[0] if len(cands) == 1 else None   # only trust an unambiguous title-only match
    if url:
        cls = domain_class(url)
        r['your_attribution_own_action_thirdparty'] = cls
        r['resolved_url'] = url
        r['resolution'] = 'domain-rule'
        n_resolved += 1; n_own += (cls == 'own'); n_third += (cls == 'thirdparty')
    else:
        r['resolution'] = 'unresolved'
        n_unresolved += 1

print('attribution_gold_sheet.csv: %d rows | already user-labeled %d | resolved-by-domain %d '
      '(own %d / thirdparty %d) | unresolved %d' % (len(rows), n_user, n_resolved, n_own, n_third, n_unresolved))
if n_unresolved:
    print('  unresolved (no title match found — check by hand): %s' %
          ', '.join(r['row_id'] for r in rows if r.get('resolution') == 'unresolved'))

if '--write' in sys.argv:
    with io.open(SHEET, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames); w.writeheader()
        for r in rows: w.writerow(r)
    print('wrote %s' % SHEET)
else:
    print('DRY RUN — rerun with --write to save.')
