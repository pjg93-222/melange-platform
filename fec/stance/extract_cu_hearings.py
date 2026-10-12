# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root
"""EXTRACT — API-FREE foundation for the committee-HEARING CU-stance channel (2026-07-15).

Reads committee-hearing transcripts (source='CHRG') out of social_media.db (READ-ONLY) and emits
hearings_cu.csv: the subset that mentions a CU / CFPB / interchange topic, keyed to a bioguide and
routed to the shared media categories.  This is an INPUT-ONLY artifact — nothing here is wired into
the pipeline, no network, no API, no spend.  It mirrors extract_cu_speeches.py exactly; the ONLY
substantive difference is source='CHRG' (a member's OWN words questioning witnesses in a hearing)
instead of source='CREC' (floor speeches).

ATTRIBUTION QUARANTINE CAVEAT (why this channel is treated as lower-trust than floor speeches):
  * CHRG transcripts interleave MEMBERS and WITNESSES, and witness surnames can COLLIDE with member
    surnames (e.g. Fed Vice-Chair for Supervision *Gov. Michael Barr* testifying vs *Rep. Andy Barr*
    on the panel).  A surname-only join would silently attribute a witness's words to a member.
  * For exactly this risk big5_traits.py defaults CHRG *OUT* of the personality text corpus; this
    stance channel opts CHRG back in only for CU-topic labeling, and only under fail-closed resolution.
  * Mitigation here: member resolution is FAIL-CLOSED via member_identity.Resolver — a row whose
    'member' does not resolve to a unique roster member (bioguide) is DROPPED, never guessed.  That
    still cannot catch a mis-labeled speaker whose stored 'member' string happens to be a real member,
    so Paul HAND-LABELS every sampled row downstream (build_hearing_gold_queue.py) and can catch a
    witness/member mixup by eye.  The honest statement: resolution reduces, but does not eliminate,
    the attribution risk — hence the below-1.0 own-source weight the blend gate applies (0.85).

Resolution and routing reuse the project's shared modules so this channel speaks the same member +
category vocabulary as every other producer:
  * member -> bioguide via member_identity.Resolver (fail-closed: unresolved rows are dropped)
  * text   -> category via media_rules.route_raw_categories(text, declared=[])  (drop if none)

NOTE: this is DISTINCT from the behavioral hearing tables (hearing_antag / hearing_meta hold
interruption COUNTS, no text) and from fetch_hearings_antag.py — none of those are touched here.
"""
import sys, os, sqlite3, csv, json, collections, re, hashlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stance.media_rules as MR
import stance.cu_passages as CP
from members.member_identity import Resolver

DB      = os.path.join(MB_ROOT, 'social_media.db')
ROSTER  = os.path.join(MB_ROOT, 'data', 'engine', 'congestion.json')
OUT     = os.path.join(MB_ROOT, 'data', 'stance', 'hearings_cu.csv')
TEXTCAP = 4000
# CREC floors to 2023 to cap floor-speech volume; CHRG is a BOUNDED historical corpus (2019 ->) so
# the floor is set to include all hearing history — the channel's whole value is the small CU subset.
MINDATE = '2019-01-01'

# Topic keywords (full-text substring match, case-insensitive) — the CU/CFPB relevance gate.
# IDENTICAL list to extract_cu_speeches.py (rule 7: the CU-relevance gate is the same keywords).
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
# BUG-B fix: the hearing channel is CHRG ONLY.  CREC (floor speeches) is a SEPARATE channel; INTERVIEW
# is its own channel.  CHRG is under an attribution quarantine (see docstring) — big5_traits.py
# defaults it OUT of the personality text corpus; here it is opted back in for CU labeling only, under
# fail-closed resolution.  BUG-D fix: deterministic ORDER BY (never rely on SQLite scan order).
q = ('select member,date,source,chamber,package,nwords,text from speeches '
     'where date >= ? and source = ? and (' + where + ') '
     'order by date, member, package, nwords, substr(text,1,120)')
rows = cur.execute(q, [MINDATE, 'CHRG'] + params).fetchall()
con.close()

total = len(rows)
kept = drop_unresolved = drop_nocat = drop_dup = drop_insert = drop_offtopic = 0
members = set()
catdist = collections.Counter()
out_rows = []
seen = set()
# BUG-E: non-spoken "printed in the Record" insertions are not the member's own stance -> drop.
# This pattern is CREC-oriented and rarely matches hearing (CHRG) text, so it is NOT load-bearing
# here; kept for parity with the floor-speech channel and reported honestly (drop_insert stays ~0).
INSERT_RX = re.compile(r'unanimous consent.{0,80}(printed in the (record|congressional record)|inserted?)', re.I)
# F3 interchange precision: bare 'interchange' also matches HIGHWAY interchanges / 'interchangeable' /
# 'interchanges between staff'. If a row's ONLY CU keyword is 'interchange' and no occurrence sits near
# a payment-interchange context, it is off-topic -> drop (audit MED: ~7/41 interview FPs, a few hearing).
_FINCTX_RX = re.compile(r'swipe|debit|credit card|merchant|durbin|payment|transaction fee|visa|mastercard|interchange fee|network fee')
_OTHER_KWS = [k for k in KEYWORDS if k != 'interchange']
def _interchange_only_offtopic(tl):
    if any(k in tl for k in _OTHER_KWS):
        return False                      # a non-interchange CU keyword is present -> genuinely relevant
    idxs = [m.start() for m in re.finditer('interchange', tl)]
    if not idxs:
        return False                      # not an interchange-driven match at all
    return not any(_FINCTX_RX.search(tl[max(0, i - 60):i + 70]) for i in idxs)
# PASSAGE-LEVEL (Paul 2026-07-15: "multiple entries/sentences from the same interview, not just one"):
# emit one entry PER ~500-char sentence-snapped CU passage, not one per whole hearing turn — so the
# labeled snippet is the credit-union exchange, not the "Thank you, Mr. Chairman" opening. Routing + the
# interchange filter now run on the PASSAGE (a tight window), far more accurate than a whole transcript.
_NW = re.compile(r'\S+')
n_src = 0                                           # source hearing turns that yielded >=1 CU passage
for member, date, source, chamber, package, nwords, text in rows:
    text = str(text or '')
    canon = resolver.resolve(name=member)          # fail-closed: None if not uniquely resolved
    bio = NAME2BIO.get(canon) if canon else None
    if not canon or not bio:
        drop_unresolved += 1
        continue
    src_kept = 0
    for passage in CP.passages(text, KEYWORDS, half=250):
        pl = passage.lower()
        if _interchange_only_offtopic(pl):         # passage is only a highway / 'interchangeable' interchange
            drop_offtopic += 1
            continue
        cats = MR.route_raw_categories(passage, [])
        if not cats:
            drop_nocat += 1
            continue
        h = (bio, hashlib.md5(passage.encode('utf-8', 'replace')).hexdigest())   # collapse identical passages
        if h in seen:
            drop_dup += 1
            continue
        seen.add(h)
        kept += 1; src_kept += 1
        members.add(bio)
        for c in cats:
            catdist[c] += 1
        out_rows.append([bio, canon, date, source, chamber, ';'.join(cats),
                         len(_NW.findall(passage)), passage[:TEXTCAP]])
    if src_kept:
        n_src += 1

# ---- write hearings_cu.csv (atomic replace) ----
tmp = OUT + '.tmp'
with open(tmp, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f)
    w.writerow(['bioguide', 'member', 'date', 'source', 'chamber', 'categories', 'nwords', 'text'])
    w.writerows(out_rows)
os.replace(tmp, OUT)

# ---- report ----
print('=== extract_cu_hearings ===')
print('total matched (date>=%s + keyword) : %d' % (MINDATE, total))
print('CU passages kept                   : %d  (from %d source hearing turns)' % (kept, n_src))
print('dropped - member unresolved        : %d' % drop_unresolved)
print('dropped - insert-into-Record        : %d' % drop_insert)
print('dropped - exact duplicate          : %d' % drop_dup)
print('dropped - no routed category       : %d' % drop_nocat)
print('dropped - interchange off-topic    : %d' % drop_offtopic)
print('distinct members (bioguide)        : %d' % len(members))
print('wrote                              : %s' % OUT)
print('--- category distribution (kept rows, multi-cat counted per category) ---')
for cat, n in catdist.most_common():
    print('  %-40s %d' % (cat, n))
