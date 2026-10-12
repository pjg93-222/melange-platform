# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root
"""EXTRACT — API-FREE foundation for the floor-speech CU-stance channel (2026-07-15).

Reads Congressional-Record floor speeches out of social_media.db (READ-ONLY) and emits
speeches_cu.csv: the subset that mentions a CU / CFPB / interchange topic, keyed to a bioguide
and routed to the shared media categories.  This is an INPUT-ONLY artifact — nothing here is
wired into the pipeline, no network, no API, no spend.

Resolution and routing reuse the project's shared modules so this channel speaks the same
member + category vocabulary as every other producer:
  * member -> bioguide via member_identity.Resolver (fail-closed: unresolved rows are dropped)
  * text   -> category via media_rules.route_raw_categories(text, declared=[])  (drop if none)
"""
import sys, os, sqlite3, csv, json, collections, re, hashlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stance.media_rules as MR
from members.member_identity import Resolver

DB      = os.path.join(MB_ROOT, 'social_media.db')
ROSTER  = os.path.join(MB_ROOT, 'data', 'engine', 'congestion.json')
OUT     = os.path.join(MB_ROOT, 'speeches_cu.csv')
TEXTCAP = 4000
MINDATE = '2023-01-01'

# Topic keywords (full-text substring match, case-insensitive) — the CU/CFPB relevance gate.
KEYWORDS = ['cfpb', 'consumer financial protection', 'credit union', 'interchange', 'swipe fee',
            'credit card competition', 'ncua', 'durbin amendment', 'field of membership',
            'member business lending', 'overdraft', 'junk fee']

# ---- roster: canonical member names + name->bioguide, from congestion.json ----
_roster = json.load(open(ROSTER, encoding='utf-8'))
NAME2BIO = {x['member']: x.get('bioguide') for x in _roster}
resolver = Resolver([x['member'] for x in _roster], NAME2BIO)

# ---- pull the topic subset, READ-ONLY ----
con = sqlite3.connect('file:' + DB + '?mode=ro', uri=True)
cur = con.cursor()
where = ' OR '.join(['lower(text) LIKE ?'] * len(KEYWORDS))
params = ['%' + k + '%' for k in KEYWORDS]
# BUG-B fix: the floor-speech channel is CREC ONLY. CHRG (hearings) is a SEPARATE channel under a
# quarantine policy (speaker-verified ordinary questioning only; big5_traits.py:37-40 defaults it OUT);
# INTERVIEW is its own channel. BUG-D fix: deterministic ORDER BY (never rely on SQLite scan order).
q = ('select member,date,source,chamber,package,nwords,text from speeches '
     'where date >= ? and source = ? and (' + where + ') '
     'order by date, member, package, nwords, substr(text,1,120)')
rows = cur.execute(q, [MINDATE, 'CREC'] + params).fetchall()
con.close()

total = len(rows)
kept = drop_unresolved = drop_nocat = drop_dup = drop_insert = 0
members = set()
catdist = collections.Counter()
out_rows = []
seen = set()
# BUG-E: non-spoken "printed in the Record" insertions are not the member's own stance -> drop.
INSERT_RX = re.compile(r'unanimous consent.{0,80}(printed in the (record|congressional record)|inserted?)', re.I)
for member, date, source, chamber, package, nwords, text in rows:
    text = str(text or '')
    canon = resolver.resolve(name=member)          # fail-closed: None if not uniquely resolved
    bio = NAME2BIO.get(canon) if canon else None
    if not canon or not bio:
        drop_unresolved += 1
        continue
    if INSERT_RX.search(text[:220]):               # BUG-E: pure insert-into-the-Record request, not speech
        drop_insert += 1
        continue
    h = (bio, hashlib.md5(text[:TEXTCAP].encode('utf-8', 'replace')).hexdigest())   # BUG-E + B1: dedup on the STORED (truncated) text
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
    members.add(bio)
    for c in cats:
        catdist[c] += 1
    out_rows.append([bio, canon, date, source, chamber, ';'.join(cats),
                     nwords if nwords is not None else '', text[:TEXTCAP]])

# ---- write speeches_cu.csv (atomic replace) ----
tmp = OUT + '.tmp'
with open(tmp, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f)
    w.writerow(['bioguide', 'member', 'date', 'source', 'chamber', 'categories', 'nwords', 'text'])
    w.writerows(out_rows)
os.replace(tmp, OUT)

# ---- report ----
print('=== extract_cu_speeches ===')
print('total matched (date>=%s + keyword) : %d' % (MINDATE, total))
print('kept                               : %d' % kept)
print('dropped - member unresolved        : %d' % drop_unresolved)
print('dropped - insert-into-Record        : %d' % drop_insert)
print('dropped - exact duplicate          : %d' % drop_dup)
print('dropped - no routed category       : %d' % drop_nocat)
print('distinct members (bioguide)        : %d' % len(members))
print('wrote                              : %s' % OUT)
print('--- category distribution (kept rows, multi-cat counted per category) ---')
for cat, n in catdist.most_common():
    print('  %-40s %d' % (cat, n))
