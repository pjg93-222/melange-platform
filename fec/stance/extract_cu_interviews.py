# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root
"""EXTRACT — API-FREE foundation for the broadcast/podcast INTERVIEW CU-stance channel (2026-07-15).

Reads attributed interview transcripts out of social_media.db (READ-ONLY) and emits
interviews_cu.csv: the subset where the attributed speaker (the member's OWN words) mentions a
CU / CFPB / interchange topic, keyed to a bioguide and routed to the shared media categories.
This is an INPUT-ONLY artifact — nothing here is wired into the pipeline, no network, no API, no spend.

Mirrors extract_cu_speeches.py exactly (same helpers, same fixes, same routing); only the source
filter and output name differ.  Resolution and routing reuse the project's shared modules so this
channel speaks the same member + category vocabulary as every other producer:
  * member -> bioguide via member_identity.Resolver (fail-closed: unresolved rows are dropped)
  * text   -> category via media_rules.route_raw_categories(text, declared=[])  (drop if none)
"""
import sys, os, sqlite3, csv, json, collections, re, hashlib
from datetime import datetime, timezone
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stance.media_rules as MR
import stance.cu_passages as CP
from members.member_identity import Resolver

DB      = os.path.join(MB_ROOT, 'social_media.db')
ROSTER  = os.path.join(MB_ROOT, 'data', 'engine', 'congestion.json')
OUT     = os.path.join(MB_ROOT, 'data', 'stance', 'interviews_cu.csv')
TEXTCAP = 4000
# The CU-relevant INTERVIEW subset is tiny (<250 rows across the whole corpus). Unlike the CREC
# floor channel — whose 104k rows justify a 2023 recency gate — this channel LABELS THE FULL modern-era
# set, so MINDATE is a permissive floor (earliest CU interview is 2011-11-10) kept only for structure
# and to guard against any null/blank date. It admits every CU-relevant interview row (task: no down-sampling).
MINDATE = '2009-01-01'

# Topic keywords (full-text substring match, case-insensitive) — the CU/CFPB relevance gate.
# C1 (2026-07-15, Paul-directed): the INTERVIEW gate is BROADENED beyond the shared speech list to
# lift coverage (agency-name variants, CFPB director 'chopra', bare 'durbin', payday/medical-debt/
# consumer-bureau) — accepting more noise that hand labeling + the narrator/interchange/keyword-in-
# stored guards trim.
KEYWORDS = ['cfpb', 'consumer financial protection', 'financial protection', 'consumer bureau',
            'credit union', 'interchange', 'swipe fee', 'credit card competition', 'ncua',
            'durbin', 'durbin amendment', 'field of membership', 'member business lending',
            'overdraft', 'junk fee', 'chopra', 'payday', 'medical debt']

# ---- roster: canonical member names + name->bioguide, from congestion.json ----
_roster = json.load(open(ROSTER, encoding='utf-8'))
NAME2BIO = {x['member']: x.get('bioguide') for x in _roster}
resolver = Resolver([x['member'] for x in _roster], NAME2BIO)

# ---- pull the topic subset, READ-ONLY ----
con = sqlite3.connect('file:' + DB + '?mode=ro', uri=True)
cur = con.cursor()
# Pin the topic subset and the full provenance projection to one SQLite read snapshot.  Without an
# explicit transaction, a WAL writer could land between the two SELECTs and produce an extract from
# generation A with a receipt describing generation B.
cur.execute('BEGIN')
where = ' OR '.join(['lower(text) LIKE ?'] * len(KEYWORDS))
params = ['%' + k + '%' for k in KEYWORDS]
# BUG-B fix: this channel is INTERVIEW ONLY. CREC (floor speeches) and CHRG (hearings) are SEPARATE
# channels (extract_cu_speeches.py handles CREC; CHRG stays under its speaker-verified quarantine);
# the source filter pins to INTERVIEW so the streams never cross-contaminate. The stored 'member' is
# the collector's attributed speaker (nickname/rank-scoped surname pools) — resolved fail-closed anyway.
# BUG-D fix: deterministic ORDER BY (never rely on SQLite scan order).
q = ('select member,date,source,chamber,package,nwords,text from speeches '
     'where date >= ? and source = ? and (' + where + ') '
     'order by date, member, package, nwords, substr(text,1,120), rowid')
rows = cur.execute(q, [MINDATE, 'INTERVIEW'] + params).fetchall()
# Bind the extract to the complete attributed INTERVIEW projection, not a permissive mtime window.
_db_digest = hashlib.sha256()
_db_rows = 0; _db_max_rowid = 0; _db_max_date = ''
for _row in cur.execute('select rowid,member,date,chamber,package,nwords,text from speeches '
                        'where source=? order by rowid', ('INTERVIEW',)):
    _db_rows += 1; _db_max_rowid = max(_db_max_rowid, int(_row[0] or 0))
    _db_max_date = max(_db_max_date, str(_row[2] or ''))
    _db_digest.update(json.dumps(_row,ensure_ascii=False,separators=(',',':'),default=str)
                      .encode('utf-8','replace'))
    _db_digest.update(b'\n')
con.close()

total = len(rows)
kept = drop_unresolved = drop_nocat = drop_dup = drop_insert = drop_offtopic = drop_durbin = 0
members = set()
catdist = collections.Counter()
out_rows = []
seen = set()
# BUG-E: non-spoken "printed in the Record" insertions are not the member's own stance -> drop.
# (A CREC-inherited guard; it effectively never fires on interview transcripts but is retained for
# structural parity and to keep the drop accounting identical across channels.)
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
# Durbin precision (Paul 2026-07-15): the broadened interview gate added bare 'durbin', which matches
# Senator Durbin in ANY context. Keep a durbin-driven row ONLY when it is the Durbin AMENDMENT or Durbin
# speaking in a credit-union / interchange context; drop bare Senator-Durbin mentions unrelated to CUs.
_DURBIN_CTX_RX = re.compile(r'interchange|swipe|debit|credit union|credit card|merchant|dodd.?frank|routing number|card fee|transaction fee|network fee|cfpb|consumer financial protection|financial protection|overdraft')
_OTHER_KWS_ND = [k for k in KEYWORDS if k not in ('durbin', 'durbin amendment')]
def _durbin_only_offtopic(tl):
    if 'durbin amendment' in tl:
        return False                       # explicit amendment -> keep
    if any(k in tl for k in _OTHER_KWS_ND):
        return False                       # another real CU keyword present -> relevant regardless of durbin
    idxs = [m.start() for m in re.finditer('durbin', tl)]
    if not idxs:
        return False                       # not a durbin-driven match
    return not any(_DURBIN_CTX_RX.search(tl[max(0, i - 100):i + 100]) for i in idxs)
# PASSAGE-LEVEL (Paul 2026-07-15: "multiple entries/sentences from the same interview, not just one"):
# emit one entry PER ~500-char sentence-snapped CU passage, so the labeled snippet IS the credit-union
# content — not the unrelated opening of a rambling interview. Precision filters + routing now run on the
# PASSAGE (a tight window), which is far more accurate than routing a whole transcript.
_NW = re.compile(r'\S+')
n_src = 0                                           # source interviews that yielded >=1 CU passage
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
        if _durbin_only_offtopic(pl):              # durbin only as the amendment or in a CU context
            drop_durbin += 1
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

# ---- write interviews_cu.csv (atomic replace) ----
tmp = OUT + '.tmp'
with open(tmp, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f)
    w.writerow(['bioguide', 'member', 'date', 'source', 'chamber', 'categories', 'nwords', 'text'])
    w.writerows(out_rows)
os.replace(tmp, OUT)

def _sha256_file(path):
    digest=hashlib.sha256()
    with open(path,'rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''): digest.update(block)
    return digest.hexdigest()

_receipt={
    'schema':'interview-cu-extract-receipt-v1','status':'COMPLETE_HASH_BOUND',
    'generated_at':datetime.now(timezone.utc).isoformat(),
    'database':{'path':DB,'size':os.path.getsize(DB),'mtime_ns':os.stat(DB).st_mtime_ns,
                'interview_rows':_db_rows,'max_rowid':_db_max_rowid,'max_date':_db_max_date,
                'projection_sha256':_db_digest.hexdigest()},
    'extract':{'matched_rows':total,'source_interviews':n_src,'passages':kept,
               'members':len(members),'output':OUT,'output_sha256':_sha256_file(OUT)},
}
_receipt_path=os.path.splitext(OUT)[0]+'.receipt.json'
_receipt_tmp=_receipt_path+'.tmp'
with open(_receipt_tmp,'w',encoding='utf-8', newline='\r\n') as _handle:
    json.dump(_receipt,_handle,ensure_ascii=False,indent=2,sort_keys=True); _handle.write('\n')
os.replace(_receipt_tmp,_receipt_path)

# ---- report ----
print('=== extract_cu_interviews (passage-level) ===')
print('total matched (date>=%s + keyword) : %d' % (MINDATE, total))
print('CU passages kept                   : %d  (from %d source interviews)' % (kept, n_src))
print('dropped - member unresolved        : %d' % drop_unresolved)
print('dropped - insert-into-Record        : %d' % drop_insert)
print('dropped - exact duplicate          : %d' % drop_dup)
print('dropped - no routed category       : %d' % drop_nocat)
print('dropped - interchange off-topic    : %d' % drop_offtopic)
print('dropped - durbin non-CU            : %d' % drop_durbin)
print('distinct members (bioguide)        : %d' % len(members))
print('wrote                              : %s' % OUT)
print('--- category distribution (kept rows, multi-cat counted per category) ---')
for cat, n in catdist.most_common():
    print('  %-40s %d' % (cat, n))
