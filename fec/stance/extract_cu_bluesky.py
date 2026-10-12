# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root
"""EXTRACT — API-FREE foundation for the Bluesky CU-stance channel (2026-07-15).

Mirrors extract_cu_speeches.py.  Reads member Bluesky posts out of social_media.db (READ-ONLY) and
emits bluesky_cu.csv: the subset that mentions a CU / CFPB / interchange topic, keyed to a bioguide
and routed to the shared media categories.  This is an INPUT-ONLY artifact — nothing here is wired
into the pipeline, no network, no API, no spend.

Bluesky is the CLEANEST channel for member resolution: the posts table already carries a bioguide,
so there is NO name-resolution step.  We still resolve fail-closed against the 533-member roster:
  * bioguide -> canonical member + chamber via congestion.json (drop if bioguide not in the roster)
  * text     -> category via media_rules.route_raw_categories(text, declared=[])  (drop if none)

Own-words guards (mirror the speech channel's fail-closed drops, each with a reported count):
  * repost_of non-null  -> DROP (a repost is not the member's own words; 0 today, guard kept)
  * handle not verified in the members registry -> DROP (the '[UNVERIFIED — confirm]' tag lives on
    members.bluesky, judged by tweet_collector.verified_bluesky_handle, never in the post text)
Replies and quote-posts are KEPT (the member's own text).
"""
import sys, os, sqlite3, csv, json, collections, hashlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stance.media_rules as MR
from collect.tweet_collector import verified_bluesky_handle

DB      = os.path.join(MB_ROOT, 'social_media.db')
ROSTER  = os.path.join(MB_ROOT, 'data', 'engine', 'congestion.json')
OUT     = os.path.join(MB_ROOT, 'data', 'stance', 'bluesky_cu.csv')
TEXTCAP = 4000

# Topic keywords (full-text substring match, case-insensitive) — the CU/CFPB relevance gate.
# IDENTICAL list to extract_cu_speeches.py (one shared relevance vocabulary across channels).
KEYWORDS = ['cfpb', 'consumer financial protection', 'credit union', 'interchange', 'swipe fee',
            'credit card competition', 'ncua', 'durbin amendment', 'field of membership',
            'member business lending', 'overdraft', 'junk fee']

# ---- roster: bioguide -> canonical member name + chamber, from congestion.json (fail-closed) ----
_roster = json.load(open(ROSTER, encoding='utf-8'))
BIO2MEMBER  = {x['bioguide']: x['member']  for x in _roster if x.get('bioguide')}
BIO2CHAMBER = {x['bioguide']: x.get('chamber', '') for x in _roster if x.get('bioguide')}

# ---- pull the topic subset, READ-ONLY ----
con = sqlite3.connect('file:' + DB + '?mode=ro', uri=True)
cur = con.cursor()
VERIFIED = {bg for bg, handle in cur.execute('select bioguide, bluesky from members') if verified_bluesky_handle(handle)}
where = ' OR '.join(['lower(text) LIKE ?'] * len(KEYWORDS))
params = ['%' + k + '%' for k in KEYWORDS]
# This channel is Bluesky ONLY (platform='bluesky').  Deterministic ORDER BY (never rely on SQLite
# scan order): created_at, then bioguide, then a text prefix.  repost_of is fetched for the own-words
# guard applied below (kept in SQL result so the drop is counted, not silently filtered).
q = ('select bioguide, created_at, repost_of, text from posts '
     'where platform = ? and (' + where + ') '
     'order by created_at, bioguide, substr(text,1,120)')
rows = cur.execute(q, ['bluesky'] + params).fetchall()
con.close()

total = len(rows)
kept = drop_noroster = drop_repost = drop_unverified = drop_nocat = drop_dup = 0
members = set()
catdist = collections.Counter()
out_rows = []
seen = set()
for bioguide, created_at, repost_of, text in rows:
    text = str(text or '')
    if repost_of not in (None, ''):                 # a repost is not the member's own words
        drop_repost += 1
        continue
    if bioguide not in VERIFIED:                    # registry handle unverified/absent -> drop
        drop_unverified += 1
        continue
    member = BIO2MEMBER.get(bioguide)               # fail-closed: drop if not in the 533-member roster
    if not bioguide or not member:
        drop_noroster += 1
        continue
    chamber = BIO2CHAMBER.get(bioguide, '')
    date = str(created_at or '')[:10]               # created_at[:10] == YYYY-MM-DD
    h = (bioguide, hashlib.md5(text.encode('utf-8', 'replace')).hexdigest())   # exact-dup collapse
    if h in seen:
        drop_dup += 1
        continue
    seen.add(h)
    # route on the full untruncated text (same text the keyword gate matched on)
    cats = MR.route_raw_categories(text, [])
    if not cats:
        drop_nocat += 1
        continue
    kept += 1
    members.add(bioguide)
    for c in cats:
        catdist[c] += 1
    out_rows.append([bioguide, member, date, 'bluesky', chamber, ';'.join(cats), text[:TEXTCAP]])

# ---- write bluesky_cu.csv (atomic replace) ----
tmp = OUT + '.tmp'
with open(tmp, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f)
    w.writerow(['bioguide', 'member', 'date', 'source', 'chamber', 'categories', 'text'])
    w.writerows(out_rows)
os.replace(tmp, OUT)

# ---- report ----
print('=== extract_cu_bluesky ===')
print('total matched (platform=bluesky + keyword) : %d' % total)
print('kept                               : %d' % kept)
print('dropped - repost (not own words)   : %d' % drop_repost)
print('dropped - UNVERIFIED handle        : %d' % drop_unverified)
print('dropped - bioguide not in roster   : %d' % drop_noroster)
print('dropped - exact duplicate          : %d' % drop_dup)
print('dropped - no routed category       : %d' % drop_nocat)
print('distinct members (bioguide)        : %d' % len(members))
print('wrote                              : %s' % OUT)
print('--- category distribution (kept rows, multi-cat counted per category) ---')
for cat, n in catdist.most_common():
    print('  %-40s %d' % (cat, n))
