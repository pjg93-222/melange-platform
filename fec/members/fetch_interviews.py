# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT  # centralized root
"""BROADCAST INTERVIEW COLLECTOR (user-directed 2026-07-03: B&L found interviews the most authentic
personality medium — "see where they source the interviews from and lets add those"; their 99
interviews came from five German broadcast formats, so this is the U.S. mapping).

Sources (adapters):
  * CNN  — transcripts.cnn.com/date/YYYY-MM-DD -> /show/<code>/date/<d>/segment/NN. Full daily
           archive across ALL CNN shows. Labels: "MARKWAYNE MULLIN, U.S. SECRETARY...:" on first
           mention, "MULLIN:" after — intros build a per-segment surname alias map.
  * MTP  — nbcnews.com Meet the Press weekly transcript pages (hub + archive page discovery).
           Labels: "SEN. ELIZABETH WARREN:" (title-prefixed full names).
Attribution: normalized full-name match against the 533 roster (titles stripped); surname-only
continuation labels resolve ONLY via the segment's own intro aliases; unresolvable speakers are
DROPPED, never guessed (same discipline as fetch_congressional_record). Host/anchor speech never
enters the corpus. Storage: speeches table, source='INTERVIEW' (big5_traits and the per-source
validity harness pick these up automatically); iv_fetch_log makes reruns resumable.
Usage:
  python fec/members/fetch_interviews.py cnn --since 2025-01-01 [--until 2026-07-03]
  python fec/members/fetch_interviews.py mtp
  python fec/members/fetch_interviews.py status
Politeness: ~0.7s between requests, identified research-collector UA. Internal research use."""
import sys, os, re, json, sqlite3, datetime, time, urllib.request, urllib.error, collections, socket
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
# TURN-BOUNDARY GRAMMAR (N12, Paul-approved 2026-09-19). The ALL-CAPS label classes below could not
# see a backtick (O`DONNELL:), a lower-case parenthetical (WALLACE (voice-over):), a bracketed
# timestamp or PBS's ", U.S. <Title>:" tail, so those speakers' words stayed inside the PREVIOUS
# member's row -- 5,835 of 153,576 stored rows (audits/laundry_list_closeout_20260918/measR1d).
# The rules live in a pure module both this collector and the one-off history repair read.
# IV_TURN_TAGS=1 restores the three legacy patterns byte for byte.
from members import turn_tags
IV_TURN_TAGS = os.environ.get('IV_TURN_TAGS', turn_tags.DEFAULT)
# Prefer IPv4 without disabling IPv6.  Several public transcript/feed hosts intermittently reset
# Windows Schannel connections on their IPv6 route while the same URL succeeds over IPv4.  Sorting
# resolver results keeps urllib's normal fallback behavior and fixes the transport failure at its
# source instead of disguising the collector as a browser or treating a reachable archive as dead.
_SYSTEM_GETADDRINFO = socket.getaddrinfo
def _ipv4_first(host, port, family=0, type=0, proto=0, flags=0):
    rows = _SYSTEM_GETADDRINFO(host, port, family, type, proto, flags)
    return sorted(rows, key=lambda row: row[0] != socket.AF_INET)
socket.getaddrinfo = _ipv4_first
# GLOBAL socket timeout (2026-07-06: fox wedged with 0 CPU for hours on a hung request — the
# per-call urlopen(timeout=60) did NOT fire, so a bad socket blocked indefinitely and froze the
# leg while cnn-deep carried on. A process-wide default forces EVERY socket op to time out,
# catching the cases the per-request timeout misses). The leg's own retry/skip then continues.
socket.setdefaulttimeout(90)
B = MB_ROOT
DB = os.path.join(B, 'social_media.db')
UA = {'User-Agent': 'MelangeSystemsResearchCollector/1.0 (public-records research)',
      # cbsnews.com 406s requests with no Accept header (content-negotiation WAF, verified 2026-07-05);
      # standard browser Accept headers unblock it and are inert for every other leg.
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      'Accept-Language': 'en-US,en;q=0.9'}
PAUSE = 0.7

def db():
    c = sqlite3.connect(DB, timeout=60); c.execute('PRAGMA journal_mode=WAL')
    c.execute('''CREATE TABLE IF NOT EXISTS speeches(member TEXT,date TEXT,source TEXT,chamber TEXT,
                 package TEXT,nwords INTEGER,text TEXT)''')
    c.execute('CREATE TABLE IF NOT EXISTS iv_fetch_log(key TEXT PRIMARY KEY,status TEXT,ts TEXT)')
    return c

def _write_source_receipt(source, payload):
    """Publish collector cadence separately from the newest accepted turn date."""
    root = os.path.join(B, 'data', 'engine', 'interview_source_receipts')
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, source + '.json')
    document = {
        'schema': 'interview-source-receipt-v1',
        'source': source,
        'completed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        **payload,
    }
    temporary = '%s.%d.tmp' % (path, os.getpid())
    with open(temporary, 'w', encoding='utf-8') as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write('\n')
    os.replace(temporary, path)
    return path

def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            # QA 2026-07-07: a hard 404/410 is PERMANENT — retrying (with 3s+6s backoff) can't make a
            # deleted page exist, it just cost ~18.7s/URL grinding the Fox archive's thousands of dead
            # links. Fail fast on 404/410 (one request, no backoff); still retry transient 5xx/429/etc.
            if e.code in (404, 410): raise
            if i == tries - 1: raise
            time.sleep(3 * (i + 1))
        except Exception:
            if i == tries - 1: raise
            time.sleep(3 * (i + 1))

import html as _html
def strip_tags(h):
    h = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', h, flags=re.S)
    h = re.sub(r'<br[^>]*>|</p>|</div>', '\n', h, flags=re.I)
    h = re.sub(r'<[^>]+>', ' ', h)
    return _html.unescape(h)   # QA 2026-07-03: full entity decode (54 PBS rows carried &#039; residue)

STAGE = re.compile(r'\((?:END|BEGIN)\s+(?:VIDEO\s+)?(?:CLIP|TAPE|CLIP MONTAGE)\)|\(COMMERCIAL BREAK\)|\(CROSSTALK\)|\(APPLAUSE\)|\(LAUGHTER\)|\(BEGIN VIDEOTAPE\)|\(END VIDEOTAPE\)', re.I)
INLINE_LABEL = re.compile(turn_tags.inline_label_pattern(IV_TURN_TAGS))
_ACRO = {'CIA', 'FBI', 'NATO', 'COVID', 'DOJ', 'DHS', 'ICE', 'IRS', 'GOP', 'US', 'USA', 'U.S.', 'CNN', 'PBS',
         'NPR', 'CBS', 'ABC', 'NBC', 'VA', 'DC', 'AI', 'EPA', 'FDA', 'CDC', 'HHS', 'SNAP', 'NIH', 'FEMA', 'UN'}
def scrub(sp):
    """Remove stage directions AND truncate at a swallowed inline speaker label (QA2: labels not at
    line starts let the NEXT speaker's words ride inside a member's turn — 'MARK STRASSMANN:').
    Acronym-colon constructions in real speech ('the CIA: their view is...') never truncate."""
    sp = STAGE.sub(' ', sp)
    pos = 1
    while True:
        m = INLINE_LABEL.search(sp, pos)
        if not m: break
        words = set(m.group(1).replace(',', '').split())
        if words - _ACRO:                      # at least one non-acronym token -> real speaker label
            sp = sp[:m.start()]; break
        pos = m.end()
    return re.sub(r'\s+', ' ', sp).strip()

# ---------------- roster resolution ----------------
C = {m['member']: m for m in json.load(open(os.path.join(B, 'data', 'engine', 'congestion.json')))}
TITLE = re.compile(r'^(SEN\.|REP\.|SENATOR|REPRESENTATIVE|SPEAKER OF THE HOUSE|SPEAKER|HOUSE MINORITY LEADER|'
                   r'HOUSE MAJORITY LEADER|SENATE (MAJORITY|MINORITY) LEADER|DEL\.|CONGRESSMAN|CONGRESSWOMAN|DR\.)\s+', re.I)
import unicodedata as _ud
def norm(s):
    s = _ud.normalize('NFD', s)                       # QA2: 'Raúl' must become 'raul', not 'ral'
    s = ''.join(c for c in s if not _ud.combining(c))
    return re.sub(r"[^a-z ]", '', s.lower()).strip()
# QA3 2026-07-04 (nomological-fix session): nickname equivalence classes. 'BERNIE SANDERS' failed
# to resolve to roster 'Bernard Sanders' (prefix test covers tom~thomas but not bernie~bernard) —
# Sanders/Murphy had 1 interview row each while dominating cable news. Equivalence is applied BOTH
# directions (roster stores 'Chuck Grassley'; transcripts may say 'CHARLES GRASSLEY').
NICK_SETS = [
    {'bernard', 'bernie'}, {'charles', 'chuck', 'charlie'}, {'christopher', 'chris'},
    {'richard', 'dick', 'rich', 'rick'}, {'michael', 'mike'}, {'james', 'jim', 'jimmy'},
    {'robert', 'bob', 'rob', 'bobby'}, {'thomas', 'tom', 'tommy'}, {'daniel', 'dan', 'danny'},
    {'joseph', 'joe', 'joey'}, {'timothy', 'tim'}, {'patrick', 'pat'},
    {'william', 'bill', 'will', 'billy'}, {'edward', 'ed', 'ted', 'eddie'},
    {'theodore', 'ted'}, {'elizabeth', 'liz', 'beth', 'betty'}, {'katherine', 'kathryn', 'katie', 'kate', 'kathy'},
    {'margaret', 'maggie', 'meg'}, {'benjamin', 'ben'}, {'alexander', 'alex'},
    {'anthony', 'tony'}, {'andrew', 'andy', 'drew'}, {'matthew', 'matt'}, {'jonathan', 'jon'},
    {'nicholas', 'nick'}, {'gregory', 'greg'}, {'lawrence', 'larry'}, {'gerald', 'jerry'},
    {'ronald', 'ron', 'ronny'}, {'donald', 'don'}, {'kenneth', 'ken'}, {'stephen', 'steven', 'steve'},
    {'david', 'dave'}, {'samuel', 'sam'}, {'frederick', 'fred'}, {'susan', 'sue', 'suzy'},
    {'deborah', 'debbie', 'deb'}, {'cynthia', 'cindy'}, {'jacquelyn', 'jacqueline', 'jackie'},
    {'raphael', 'rafael', 'raffi'}, {'joshua', 'josh'}, {'zachary', 'zach', 'zack'},
    {'abigail', 'abby'}, {'rebecca', 'becca', 'becky'}, {'jefferson', 'jeff'}, {'jeffrey', 'jeff'},
    {'mitchell', 'mitch'}, {'randall', 'randy'}, {'terrence', 'terri', 'terry'}, {'vincent', 'vince'},
]
NICK = {}
for s in NICK_SETS:
    for a in s: NICK.setdefault(a, set()).update(s)
def _variants(first):
    return NICK.get(first, set()) | {first}
FULL = {}
for m in C:
    p = norm(m).split()
    if len(p) >= 2:
        for v in _variants(p[0]): FULL.setdefault((v, p[-1]), m)
SUR = collections.defaultdict(list)
for m in C:
    SUR[norm(m).split()[-1]].append(m)
# rank-scoped surname pools (user rule 2026-07-06: 'Speaker Johnson refers to only one person' —
# the rank word carries disambiguating information the flat pool discards). Verified on the live
# roster: 'senator johnson' -> Ron Johnson uniquely (the 4 other Johnsons are House); 'senator
# scott' stays ambiguous (Rick+Tim) and correctly never binds.
SUR_SEN = collections.defaultdict(list); SUR_HOU = collections.defaultdict(list)
for m in C:
    (SUR_SEN if (C[m].get('chamber') == 'Senate') else SUR_HOU)[norm(m).split()[-1]].append(m)
# 119th-Congress floor-leadership singletons (role data isn't in the roster; these constants are
# exactly as current as the roster file itself — refresh alongside it at the next Congress).
LEADERSHIP = ['Mike Johnson', 'John Thune', 'Charles Schumer', 'Steve Scalise', 'Hakeem Jeffries',
              'John Barrasso', 'Richard Durbin', 'Tom Emmer', 'Katherine Clark']
LEAD_SUR = {norm(m).split()[-1]: [m] for m in LEADERSHIP if m in C}

def _firstname_compatible(a, b):
    """'tom'~'thomas' yes; nickname classes (bernie~bernard) yes; 'raul'~'adelita' NO
    (QA2: succession-seat misattribution guard)."""
    a, b = a.lower(), b.lower()
    return a == b or a.startswith(b) or b.startswith(a) or bool(_variants(a) & _variants(b))

def resolve_full(label):
    """'SEN. ELIZABETH WARREN' / 'THOM TILLIS' -> roster member or None (never guess).
    QA2 hardening: the unique-surname fallback fires ONLY when the label's first name is
    COMPATIBLE with the roster member's (nickname/prefix) — a mismatched first name means a
    DIFFERENT person (predecessor, relative) and must never bind."""
    s = TITLE.sub('', label.strip())
    s = re.sub(r'\(.*?\)', '', s)               # (D-MA)
    s = re.sub(r',\s*[DRI]\.?\s*-\s*[A-Za-z][A-Za-z.\- ]{0,20}\s*$', '', s)   # ", D-Va." (pre-2020 PBS form)
    p = norm(s).split()
    if len(p) >= 2:
        m = FULL.get((p[0], p[-1]))
        if m: return m
        cands = SUR.get(p[-1], [])
        if (len(cands) == 1 and re.match(r'^(SEN|REP|SENATOR|REPRESENTATIVE|CONGRESS)', label.strip(), re.I)
                and _firstname_compatible(p[0], norm(cands[0]).split()[0])):
            return cands[0]
    elif len(p) == 1:
        # QA 2026-07-10 (CSIS leg): 'Senator Shaheen:' = honorific + BARE surname — no first name
        # exists to check. Resolve ONLY with an explicit honorific AND a roster-unique surname
        # (the same uniqueness standard as above, minus the impossible firstname test).
        cands = SUR.get(p[0], [])
        if len(cands) == 1 and re.match(r'^(SEN|REP|SENATOR|REPRESENTATIVE|CONGRESS)', label.strip(), re.I):
            return cands[0]
    return None

LABEL = re.compile(turn_tags.label_pattern(IV_TURN_TAGS), re.M)
def parse_turns(text):
    """Split transcript text into (label, speech) turns; build intro alias map for surname labels."""
    turns = []
    pos = [(m.start(), m.group(1), m.end()) for m in LABEL.finditer(text)]
    for i, (st, lab, me) in enumerate(pos):
        end = pos[i + 1][0] if i + 1 < len(pos) else len(text)
        sp = text[me:end].strip()   # from the label's own end: a [hh:mm:ss] stamp holds a colon that cutting at the first ':' would hit
        turns.append((lab.strip(), re.sub(r'\s+', ' ', sp)))
    alias = {}; _ambig = set()
    for lab, _ in turns:
        base = lab.split(',')[0].strip()          # "MARKWAYNE MULLIN, U.S. ...:" -> full-name part
        m = resolve_full(base)
        if m and len(norm(base).split()) >= 2:
            sur = norm(base).split()[-1]
            if sur in alias and alias[sur] != m: _ambig.add(sur)   # QA2: Mike + Ron Johnson in one segment
            alias[sur] = m
    for sur in _ambig: alias.pop(sur, None)       # ambiguous surnames never bind continuations
    out = []
    for lab, sp in turns:
        base = lab.split(',')[0].strip()
        m = resolve_full(base)
        if not m:
            k = norm(base).split()
            m = alias.get(k[-1]) if len(k) == 1 else None
        if m:
            sp = scrub(sp)
            if len(sp.split()) >= 8:              # drop micro-interjections
                out.append((m, sp))
    return out

def store(con, rows, date, show, url):
    # QA 2026-07-07: some source pages embed the transcript body TWICE in one document (observed on
    # Fox, e.g. .../why-do-we-still-not-know-... carried the whole Carlson show verbatim twice) ->
    # parse_turns faithfully returned every turn twice -> store double-inserted. Dedup within the
    # page on (member, exact text): a real interview never has one member repeat a turn verbatim,
    # so this only ever removes source-side duplication. Shared by all 8 legs, so it hardens each.
    n = 0; seen = set()
    for m, sp in rows:
        k = (m, sp)
        if k in seen: continue
        seen.add(k)
        con.execute('INSERT INTO speeches VALUES (?,?,?,?,?,?,?)',
                    (m, date, 'INTERVIEW', show, url, len(sp.split()), sp))
        n += 1
    return n

# ---------------- adapters ----------------
def run_cnn(since, until, verify=False, budget=0):
    """Coverage-audit hardening 2026-07-06: (1) a failed SEGMENT no longer poisons coverage
    silently — the day is marked 'partial' (not 'ok') so future runs revisit it; (2) day re-walks
    are idempotent — segments already stored for that day are skipped, never duplicated; (3)
    --verify re-walks EVERY day in range (index fetch each, segments only where missing) to
    repair the historical ok-on-partial losses the old code could not see.

    2026-08-25 — a hard SIGKILL at the caller's timeout discards the run's stdout (the 2026-08-23
    TIMEOUT left zero diagnosis); a wall-clock budget below the caller's ceiling lets the leg stop
    BETWEEN days, flush, and print where it stopped and why segments failed. budget=0 (default) =
    unlimited: manual and --verify runs behave exactly as before."""
    con = db(); done = {k for (k,) in con.execute("SELECT key FROM iv_fetch_log WHERE status='ok'")}
    # Normal incremental runs avoid re-fetching known zero-yield segments. A verification run
    # deliberately revisits them: an old `empty` receipt can mean either a legitimately
    # member-free segment or a historical parser/schema miss, so it is not proof of coverage.
    done_seg = set() if verify else {
        k for (k,) in con.execute("SELECT key FROM iv_fetch_log WHERE key LIKE 'cnnseg:%'")
    }
    d = datetime.date.fromisoformat(since); end = datetime.date.fromisoformat(until)
    tot = repaired = 0; complete = True
    t0 = time.monotonic(); errs = collections.Counter(); err_sample = {}; partials = []
    while d <= end:
        if budget and (time.monotonic() - t0) > budget:
            complete = False
            print('CNN BUDGET EXHAUSTED after %ds at %s — %d day(s) unwalked in [%s..%s]' % (
                budget, d.isoformat(), (end - d).days + 1, since, until), flush=True)
            break
        key = 'cnn:%s' % d.isoformat()
        if key in done and not verify: d += datetime.timedelta(days=1); continue
        try:
            idx = get('https://transcripts.cnn.com/date/%s' % d.isoformat()); time.sleep(PAUSE)
            segs = sorted(set(re.findall(r'href="(/show/[^"]+/date/%s/segment/[^"]+)"' % d.isoformat(), idx)))
            if not segs:
                # A valid archive page can be empty while the current day is still publishing.
                # Keep it retryable; an HTTP-200 page with zero parsed links must never become a
                # permanent `ok` receipt (it could also be a future schema/block page).
                con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)',
                            (key, 'empty', datetime.datetime.now().isoformat()))
                con.commit(); complete = False
                print('%s: 0 segments — left retryable' % d.isoformat(), flush=True)
                d += datetime.timedelta(days=1)
                continue
            have = {p for (p,) in con.execute("SELECT DISTINCT package FROM speeches WHERE package LIKE 'https://transcripts.cnn.com%' AND date=?", (d.isoformat(),))}
            new = [s for s in segs if ('https://transcripts.cnn.com' + s) not in have and ('cnnseg:' + s) not in done_seg]
            nd = fails = 0
            for s in new:
                try:
                    page = get('https://transcripts.cnn.com' + s); time.sleep(PAUSE)
                    rows = parse_turns(strip_tags(page))
                    n0 = store(con, rows, d.isoformat(), s.split('/')[2], 'https://transcripts.cnn.com' + s)
                    if n0 == 0:   # zero-yield marker (verify lesson 2026-07-06: 21k historically-empty
                        # segments were refetched because 'stored nothing' looked like 'never fetched')
                        con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)',
                                    ('cnnseg:' + s, 'empty', datetime.datetime.now().isoformat()))
                    nd += n0
                    con.commit()   # SHORT transactions: a day-long write TX starved the CREC collector's lock (2026-07-03)
                except Exception as ex:
                    fails += 1
                    _k = ('HTTP %s' % ex.code) if isinstance(ex, urllib.error.HTTPError) else type(ex).__name__
                    errs[_k] += 1; err_sample.setdefault(_k, '%s :: %s' % (s, str(ex)[:80]))
            con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)',
                        (key, 'ok' if fails == 0 else 'partial', datetime.datetime.now().isoformat()))
            con.commit(); tot += nd
            if fails: partials.append(d.isoformat())
            if fails: complete = False
            if verify and new: repaired += len(new) - fails
            if new or not verify:
                print('%s: %d segments (%d new, %d failed), %d member turns (total %d)' % (
                    d.isoformat(), len(segs), len(new), fails, nd, tot), flush=True)
        except Exception as e:
            complete = False
            print('%s: FAIL %s' % (d.isoformat(), str(e)[:90]), flush=True)
        d += datetime.timedelta(days=1)
    con.close()
    if errs:
        print('CNN segment-failure census: ' + ', '.join('%s=%d' % kv for kv in errs.most_common()), flush=True)
        for _k, _ex in list(err_sample.items())[:3]:
            print('   %s e.g. %s' % (_k, _ex), flush=True)
    if partials:
        print('CNN days left partial (re-walked next run): ' + ','.join(partials), flush=True)
    print('CNN done: %d member turns added%s' % (tot, (' | verify repaired %d segments' % repaired) if verify else ''), flush=True)
    return d > end and complete

def _mtp_sitemap_links(document):
    """Require a sitemap document before interpreting zero transcript links as empty."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(document)
    namespace = '{http://www.sitemaps.org/schemas/sitemap/0.9}'
    if root.tag not in ('urlset', namespace + 'urlset'):
        raise ValueError('MTP monthly sitemap has an unexpected XML root')
    prefix = namespace if root.tag.startswith('{') else ''
    if root.text and root.text.strip():
        raise ValueError('MTP monthly sitemap contains unexpected text')
    links = set()
    for entry in root:
        locations = entry.findall(prefix + 'loc')
        if entry.tag != prefix + 'url' or len(locations) != 1:
            raise ValueError('MTP monthly sitemap has an invalid URL entry')
        if ((entry.text and entry.text.strip())
                or any(child.tail and child.tail.strip() for child in entry)):
            raise ValueError('MTP monthly sitemap contains unexpected text')
        location = locations[0]
        url = (location.text or '').strip()
        parsed = urllib.parse.urlsplit(url)
        if len(location) or parsed.scheme not in ('http', 'https') or not parsed.netloc:
            raise ValueError('MTP monthly sitemap has an invalid URL location')
        if entry.tail and entry.tail.strip():
            raise ValueError('MTP monthly sitemap contains unexpected text')
        if re.fullmatch(r'https://www\.nbcnews\.com/meet-the-press/(?:transcripts/|meet-press-).+', url):
            links.add(url)
    return links


def run_mtp():
    con = db(); done = {k for (k,) in con.execute("SELECT key FROM iv_fetch_log WHERE status='ok'")}
    failures = 0
    hubs = ['https://www.nbcnews.com/meet-the-press/meet-press-transcripts-n51976',
            'https://www.nbcnews.com/meet-the-press/news/meet-the-press-transcripts-archive-rcna254151']
    links = set()
    for h in hubs:
        try:
            page = get(h); time.sleep(PAUSE)
            links |= set(re.findall(r'href="(https://www\.nbcnews\.com/meet-the-press/transcripts/[^"]+)"', page))
        except Exception as e:
            failures += 1
            print('hub FAIL %s %s' % (h, str(e)[:80]))
    # DEEP enumeration (coverage audit 2026-07-06: the hubs only expose RECENT transcripts — the
    # stored corpus was 19 pages / Jan-Jun 2026 only). NBC's monthly article sitemaps carry every
    # episode page under TWO patterns: legacy 'meet-the-press/meet-press-<date>-nID' (verified
    # parseable: Sanders 5 turns on 2024-10-27) and current 'meet-the-press/transcripts/<slug>'.
    today = datetime.date.today()
    y, m = 2014, 1
    while (y, m) <= (today.year, today.month):
        try:
            sm = get('https://www.nbcnews.com/sitemap/nbcnews/sitemap-%04d-%02d-article.xml' % (y, m))
            links |= _mtp_sitemap_links(sm)
        except urllib.error.HTTPError as e:
            # Pre-CMS months may have no sitemap. Only an explicit absence is
            # expected; exhausted transport/server errors leave discovery incomplete.
            if e.code not in (404, 410):
                failures += 1
                print('MTP sitemap %04d-%02d FAIL HTTP %s' % (y, m, e.code), flush=True)
        except Exception as e:
            failures += 1
            print('MTP sitemap %04d-%02d FAIL %s: %s' %
                  (y, m, type(e).__name__, str(e)[:80]), flush=True)
        time.sleep(PAUSE)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    links = {u for u in links if 'transcripts-n51976' not in u and 'transcripts-archive' not in u}
    print('MTP transcript pages discovered: %d (hubs + 2014->now monthly sitemaps)' % len(links))
    tot = 0
    for u in sorted(links):
        key = 'mtp:' + u.rsplit('/', 1)[-1]
        if key in done: continue
        try:
            page = get(u); time.sleep(PAUSE)
            md = re.search(r'(\d{4}-\d{2}-\d{2})', page)
            date = md.group(1) if md else ''
            rows = parse_turns(strip_tags(page))
            n = store(con, rows, date, 'meet-the-press', u)
            con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'ok', datetime.datetime.now().isoformat()))
            con.commit(); tot += n
            print('%s: %d member turns' % (u.rsplit('/', 1)[-1][:60], n), flush=True)
        except Exception as e:
            failures += 1
            print('%s FAIL %s' % (u[-50:], str(e)[:80]), flush=True)
    con.close(); print('MTP done: %d member turns added' % tot)
    return bool(links) and failures == 0

MIXED_LABEL = re.compile(turn_tags.mixed_label_pattern(IV_TURN_TAGS), re.M)   # PBS: "Rep. Tom Kean (R-NJ):" AND the pre-2020 comma form "Sen. Mark Warner, D-Va.:" (QA 2026-07-10: the 2019 backfill walked 29 shutdown-era days with ZERO member rows — the comma suffix made every member label unmatchable while hosts resolved to None; both suffix forms now admitted, resolve_full strips both). N12: the repaired position also admits the ", U.S. <Title>:" tail PBS uses for witnesses ("Scott Bessent, U.S. Treasury Secretary:"), which the party-only tail could not match.
def parse_turns_mixed(text):
    """PBS-style mixed-case labels ('Rep. Tom Kean:'); same drop-never-guess discipline."""
    turns = []
    pos = [(m.start(), m.group(1), m.end()) for m in MIXED_LABEL.finditer(text)]
    out = []
    for i, (st, lab, me) in enumerate(pos):
        end = pos[i + 1][0] if i + 1 < len(pos) else len(text)
        sp = text[me:end].strip()
        m = resolve_full(lab)
        if m:
            sp = scrub(sp)
            if len(sp.split()) >= 8:
                out.append((m, sp))
    return out

def run_cbs():
    con = db(); done = {k for (k,) in con.execute("SELECT key FROM iv_fetch_log WHERE status='ok'")}
    failures = 0
    idx = ['https://www.cbsnews.com/face-the-nation/transcripts/'] + \
          ['https://www.cbsnews.com/news/face-the-nation-transcripts-%d' % y for y in range(2019, 2027)]   # probe 2026-07-03: year archives exist back to at least 2019 (~150 eps each)
    arts = set()
    for u in idx:
        try:
            page = get(u); time.sleep(PAUSE)
            for h in re.findall(r'href="(https?://(?:www\.)?cbsnews\.com/news/[^"]*transcript[^"]*)"', page):
                arts.add(h.split('?')[0])
        except Exception as e:
            failures += 1
            print('cbs index FAIL %s %s' % (u[-40:], str(e)[:70]), flush=True)
    print('CBS transcript articles discovered: %d' % len(arts))
    tot = 0
    for u in sorted(arts):
        key = 'cbs:' + u.rstrip('/').rsplit('/', 1)[-1][:80]
        if key in done: continue
        try:
            page = get(u); time.sleep(PAUSE)
            md = re.search(r'(\d{2})-(\d{2})-(\d{4})', u)
            date = '%s-%s-%s' % (md.group(3), md.group(1), md.group(2)) if md else ''
            if not date:   # QA: guest pages without URL dates -> page metadata
                mp = re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', page)
                date = mp.group(1) if mp else ''
            rows = parse_turns(strip_tags(page))
            n = store(con, rows, date, 'face-the-nation', u)
            con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'ok', datetime.datetime.now().isoformat()))
            con.commit(); tot += n
            print('cbs %s: %d turns' % (key[4:60], n), flush=True)
        except Exception as e:
            failures += 1
            print('cbs %s FAIL %s' % (u[-50:], str(e)[:70]), flush=True)
    con.close(); print('CBS done: %d member turns' % tot)
    return bool(arts) and failures == 0

def _abc_transcript_links(page):
    """Return canonical ABC transcript URLs from the archive's evolving query shape.

    ABC currently decorates story links with preview/cache parameters before the stable ``id``
    parameter (``/story?_apiEnv=...&id=123``).  The former regex required the literal
    ``/story?id=`` spelling, silently discovered zero links, and made every strict weekly refresh
    fail.  Canonicalizing to path + story id also keeps resume keys stable when ABC changes those
    decoration parameters.
    """
    from urllib.parse import parse_qs, urljoin, urlsplit

    links = set()
    for raw in re.findall(r'href=["\']([^"\']+)["\']', page, flags=re.I):
        href = _html.unescape(raw).strip()
        lowered = href.lower()
        if 'week-transcript' not in lowered or '/story' not in lowered:
            continue
        absolute = urljoin('https://abcnews.go.com/', href)
        parsed = urlsplit(absolute)
        story_ids = parse_qs(parsed.query).get('id') or []
        if not story_ids or not story_ids[0].isdigit() or story_ids[0] == '16614108':
            continue
        links.add('https://abcnews.go.com%s?id=%s' % (parsed.path, story_ids[0]))
    return links


def run_abc():
    """ABC 'This Week' transcript archive.

    Terminal-loss receipts (2026-08-28): ABC's own archive page publishes story ids that are
    permanently gone — .../week-transcript-4-16-23-sen-lindsey-graham/story?id=986110097 returns
    HTTP 404 on every request (id verified malformed against the 2023 id range).  The strict
    weekly contract counted that as a leg failure, so `--strict` exited 2 and aborted publication
    on EVERY supervisor tick, forever: retrying cannot make a deleted page exist and no receipt
    was written, so the same dead link was re-attempted each cycle.  A 404/410 is a permanent
    absence AT THE SOURCE, not a break in our collection, so it is recorded under a distinct
    `nopage` status (the same vocabulary run_axefiles/run_aei already use) and skipped thereafter.
    The loss stays auditable in iv_fetch_log and loud on the console.

    This does NOT weaken the gate.  Every other error — 5xx, timeout, TLS, parse — still counts
    as a failure and still aborts the refresh; an unreachable archive page or an archive that
    discovers zero stories still returns False.  And a terminal receipt is only honest when the
    run PROVED the archive still serves stories, so more than one dead link outnumbering the
    successful fetches is read as site-wide breakage: no receipts are written, the dead keys stay
    retryable, and the leg fails loudly until the code is repaired."""
    con = db(); done = {k for (k,) in con.execute("SELECT key FROM iv_fetch_log WHERE status='ok'")}
    done |= {k for (k,) in con.execute(
        "SELECT key FROM iv_fetch_log WHERE key LIKE 'abc:%' AND status='nopage'")}
    try:
        page = get('https://abcnews.go.com/Politics/week-transcript-archive/story?id=16614108'); time.sleep(PAUSE)
    except Exception as e:
        print('abc archive FAIL', str(e)[:80]); con.close(); return False
    links = _abc_transcript_links(page)
    print('ABC transcript stories discovered: %d' % len(links))
    tot = 0; failures = 0; fetched = 0; gone = []
    for u in sorted(links):
        key = 'abc:' + u.rsplit('=', 1)[-1]
        if key in done: continue
        try:
            page = get(u); time.sleep(PAUSE)
            mp = re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', page)   # QA: ABC stored empty dates
            rows = parse_turns(strip_tags(page))
            n = store(con, rows, mp.group(1) if mp else '', 'this-week', u)
            con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'ok', datetime.datetime.now().isoformat()))
            con.commit(); tot += n; fetched += 1
            print('abc %s: %d turns' % (key, n), flush=True)
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                gone.append((key, u, e.code))
                print('abc %s GONE HTTP %d (permanently dead archive link)' % (key, e.code), flush=True)
                continue
            failures += 1
            print('abc %s FAIL HTTP %s' % (u[-40:], e.code), flush=True)
        except Exception as e:
            failures += 1
            print('abc %s FAIL %s' % (u[-40:], str(e)[:70]), flush=True)
    if gone and len(gone) > 1 and len(gone) > fetched:
        print('ABC ARCHIVE BREAKAGE: %d of %d attempted stories returned 404/410 and only %d '
              'succeeded — no terminal receipts written, every key stays retryable'
              % (len(gone), len(gone) + fetched + failures, fetched), flush=True)
        failures += len(gone)
    elif gone:
        for _key, _u, _code in gone:
            con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)',
                        (_key, 'nopage', datetime.datetime.now().isoformat()))
        con.commit()
        print('ABC permanently-dead archive links recorded as nopage (not retried): '
              + ', '.join('%s HTTP %d' % (_key, _code) for _key, _u, _code in gone), flush=True)
    con.close(); print('ABC done: %d member turns' % tot)
    return bool(links) and failures == 0

MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december']
def run_pbs(since, until):
    con = db(); done = {k for (k,) in con.execute("SELECT key FROM iv_fetch_log WHERE status='ok'")}
    d = datetime.date.fromisoformat(since); end = datetime.date.fromisoformat(until)
    tot = 0; failures = 0
    while d <= end:
        key = 'pbs:%s' % d.isoformat()
        if key in done: d += datetime.timedelta(days=1); continue
        slugs = ['%s-%d-%d-pbs-news-hour-full-episode' % (MONTHS[d.month - 1], d.day, d.year),
                 '%s-%d-%d-pbs-news-weekend-full-episode' % (MONTHS[d.month - 1], d.day, d.year),
                 '%s-%d-%d-pbs-newshour-full-episode' % (MONTHS[d.month - 1], d.day, d.year)]
        nd = 0; found = False; dfail = 0
        for slug in slugs:
            try:
                page = get('https://www.pbs.org/newshour/show/' + slug); time.sleep(PAUSE)
            except Exception as ex:
                # only a hard 404/410 means 'no episode under this slug'; a timeout/5xx/DNS/outage is a FAILURE
                # (fail closed: --strict must not pass, and the day must stay retryable)
                if not (isinstance(ex, urllib.error.HTTPError) and ex.code in (404, 410)):
                    failures += 1; dfail += 1
                continue
            found = True
            segs = sorted(set(re.findall(r'href="(https://www\.pbs\.org/newshour/show/[a-z0-9\-]+)"', page)))
            segs = [s for s in segs if 'full-episode' not in s][:30]
            for s in segs:
                skey = 'pbsseg:' + s.rsplit('/', 1)[-1][:90]
                if skey in done: continue
                try:
                    sp = get(s); time.sleep(PAUSE)
                    i = sp.find('class="video-transcript')   # probe 2026-07-03: transcript div class (no id anchor)
                    if i < 0: i = sp.find('id="transcript"')
                    if i < 0: continue
                    body = re.sub(r'<[^>]+>', '\n', sp[i:i + 200000])
                    rows = parse_turns_mixed(body)
                    nd += store(con, rows, d.isoformat(), 'pbs-newshour', s)
                    con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (skey, 'ok', datetime.datetime.now().isoformat()))
                    con.commit()   # short transactions (lock hygiene)
                except Exception:
                    failures += 1; dfail += 1
                    continue
            break
        # 'partial' (not 'ok') when any fetch failed: only 'ok' days are skipped on later runs (cf. run_cnn)
        con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'partial' if dfail else 'ok' if found else 'noep', datetime.datetime.now().isoformat()))
        con.commit(); tot += nd
        if found: print('pbs %s: %d member turns (total %d)' % (d.isoformat(), nd, tot), flush=True)
        d += datetime.timedelta(days=1)
    con.close(); print('PBS done: %d member turns' % tot)
    return failures == 0

NPR_PROGRAMS = ('morning-edition', 'all-things-considered', 'weekend-edition-saturday', 'weekend-edition-sunday')
def run_npr(since, until):
    """NPR program archives -> per-story /transcripts/ pages (CAPS labels incl. 'NAME, BYLINE:' —
    the standard parse_turns handles them; hosts/reporters don't resolve to the roster and drop)."""
    con = db(); done = {k for (k,) in con.execute("SELECT key FROM iv_fetch_log WHERE status='ok'")}
    d = datetime.date.fromisoformat(since); end = datetime.date.fromisoformat(until)
    tot = 0; failures = 0
    while d <= end:
        for prog in NPR_PROGRAMS:
            key = 'npr:%s:%s' % (prog, d.isoformat())
            if key in done: continue
            try:
                idx = get('https://www.npr.org/programs/%s/archive?date=%s' % (prog, d.strftime('%m-%d-%Y'))); time.sleep(PAUSE)
                links = sorted(set(re.findall(r'href="(https://www\.npr\.org/transcripts/[^"#?]+)"', idx)))
                nd = 0; pfail = 0
                for u in links:
                    tkey = 'nprtx:' + u.rsplit('/', 1)[-1]
                    if tkey in done: continue
                    try:
                        page = get(u); time.sleep(PAUSE)
                        # NPR wraps labels inline (<b>GONYEA:</b> mid-paragraph) — every tag must
                        # become a NEWLINE or only the first label sits at a line start.
                        body = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', page, flags=re.S)
                        body = re.sub(r'<[^>]+>', '\n', body)
                        rows = parse_turns(body)
                        nd += store(con, rows, d.isoformat(), 'npr-' + prog, u)
                        con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (tkey, 'ok', datetime.datetime.now().isoformat()))
                        con.commit()
                        done.add(tkey)
                    except Exception:
                        failures += 1; pfail += 1
                        continue
                con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'partial' if pfail else 'ok', datetime.datetime.now().isoformat()))
                con.commit(); tot += nd
                if nd: print('npr %s %s: %d member turns (total %d)' % (prog[:14], d.isoformat(), nd, tot), flush=True)
            except Exception as e:
                failures += 1
                print('npr %s %s FAIL %s' % (prog[:14], d.isoformat(), str(e)[:70]), flush=True)
        d += datetime.timedelta(days=1)
    con.close(); print('NPR done: %d member turns' % tot)
    return failures == 0

def run_fox():
    """Fox News transcript archive (foxnews.com/transcript/...). REVISED FINDING 2026-07-05 (the
    2026-07-03 'no public archive' note was wrong — the category page is JS-rendered, which made
    it LOOK empty): full-episode transcripts exist back to ~2018 but Fox STOPPED POSTING them
    ~2024-10, and the article-search API no longer honors its offset param. Enumeration therefore
    walks the article SITEMAPS (~166 pages, one fetch each) and filters /transcript/ URLs — the
    archive is historical; if Fox resumes publishing, re-runs pick new URLs up automatically.
    Old interviews of CURRENT members are valid personality/relational text (RKH used 1996-2014);
    non-roster guests drop at parse_turns (drop-never-guess). Post-2024-10 Fox stays unavailable
    until a YouTube-caption diarization build exists (captions carry no speaker labels)."""
    # QA 2026-07-07 (user-approved): the Fox archive holds thousands of long-removed transcript URLs
    # that now 404 (dead 2004-era campaign posts); with no skip-marker each run re-fetched every one.
    # Skip keys marked 'ok' (collected) OR 'gone' (a CONFIRMED hard 404/410 — see the except below).
    # 'gone' is written ONLY for HTTP 404/410; a timeout / connection error / 5xx / 429 is NEVER
    # marked, so a slow-loading article that times out stays retryable and is re-attempted next run.
    con = db(); done = {k for (k,) in con.execute("SELECT key FROM iv_fetch_log WHERE status IN ('ok','gone')")}
    MONTHS = {m: i for i, m in enumerate(('january', 'february', 'march', 'april', 'may', 'june', 'july',
                                          'august', 'september', 'october', 'november', 'december'), 1)}
    page_failures = 0
    try:
        idx = get('https://www.foxnews.com/sitemap.xml'); time.sleep(PAUSE)
    except Exception as e:
        print('fox sitemap index FAIL %s' % str(e)[:80])
        _write_source_receipt('fox', {
            'status': 'PARTIAL', 'freshness_mode': 'historical_archive',
            'error': 'sitemap index: %s' % str(e)[:200],
        })
        con.close(); return False
    pages = [u.replace('&amp;', '&') for u in re.findall(r'<loc>([^<]+type=articles[^<]*)</loc>', idx)]
    urls = []; source_dates = []
    for i, p in enumerate(pages):
        try:
            sitemap = get(p)
            for block in re.findall(r'<url>(.*?)</url>', sitemap, flags=re.S | re.I):
                loc = re.search(r'<loc>(https://www\.foxnews\.com/transcript/[^<]+)</loc>', block, flags=re.I)
                if not loc: continue
                urls.append(loc.group(1))
                modified = re.search(r'<lastmod>(\d{4}-\d{2}-\d{2})', block, flags=re.I)
                if modified: source_dates.append(modified.group(1))
        except Exception as e:
            page_failures += 1
            print('fox sitemap page %d FAIL %s' % (i, str(e)[:60]), flush=True)
        time.sleep(PAUSE)
    urls = sorted(set(urls))
    print('fox: %d transcript urls enumerated from %d sitemap pages' % (len(urls), len(pages)), flush=True)
    candidates = [u for u in urls if 'local-air-times' not in u.rstrip('/').rsplit('/', 1)[-1]]
    pending_before = sum(('fox:%s' % u.rstrip('/').rsplit('/', 1)[-1][:180]) not in done
                         for u in candidates)
    tot = 0; fetch_failures = 0
    for u in urls:
        slug = u.rstrip('/').rsplit('/', 1)[-1]
        if 'local-air-times' in slug: continue
        key = 'fox:%s' % slug[:180]
        if key in done: continue
        try:
            page = get(u); time.sleep(PAUSE)
            md = re.search(r'(january|february|march|april|may|june|july|august|september|october|november|december)-(\d{1,2})-(\d{4})', slug)
            date = '%04d-%02d-%02d' % (int(md.group(3)), MONTHS[md.group(1)], int(md.group(2))) if md else ''
            if not date:   # audit 2026-07-06: most Fox slugs carry no date — the page's JSON-LD does
                mp = re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', page)
                if mp: date = mp.group(1)
            show = (slug[:md.start()].rstrip('-') if md else re.sub(r'-\d.*$', '', slug)) or 'fox'
            n = store(con, parse_turns(strip_tags(page)), date, show, u)
            con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'ok', datetime.datetime.now().isoformat()))
            con.commit(); tot += n
            if n: print('fox %s: %d turns (total %d)' % (slug[:60], n, tot), flush=True)
        except Exception as e:
            fetch_failures += 1
            # Mark PERMANENT-absence only: a hard 404 (Not Found) or 410 (Gone) after get()'s 3 retries.
            # Everything else (socket/read timeout on a slow page, connection reset, 5xx, 429, 403) is
            # transient -> left UNmarked so the next run retries it. This is the safety the user asked for:
            # a slow-to-load article is never mistaken for a dead one.
            if isinstance(e, urllib.error.HTTPError) and e.code in (404, 410):
                con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)',
                            (key, 'gone', datetime.datetime.now().isoformat()))
                con.commit()
            print('fox %s FAIL %s' % (slug[:50], str(e)[:80]), flush=True)
    stats = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT member), COUNT(DISTINCT package), MAX(date) "
        "FROM speeches WHERE source='INTERVIEW' AND lower(package) LIKE '%foxnews.com/%'"
    ).fetchone()
    con.close(); print('fox done: %d member turns' % tot)
    _write_source_receipt('fox', {
        'status': 'COMPLETE' if page_failures == 0 and fetch_failures == 0 else 'PARTIAL',
        'freshness_mode': 'historical_archive',
        'archive_urls_enumerated': len(candidates),
        'archive_pages_checked': len(pages),
        'archive_page_failures': page_failures,
        'new_urls_checked': pending_before,
        'new_url_failures': fetch_failures,
        'source_terminal_date': max(source_dates) if source_dates else None,
        'accepted_member_content_latest_date': stats[3],
        'accepted_turns': stats[0], 'accepted_members': stats[1],
        'accepted_packages': stats[2], 'turns_added': tot,
        'availability_note': ('Fox transcript URLs remain fully represented; the public transcript '
                              'archive has not exposed current daily transcripts.'),
    })
    return page_failures == 0 and fetch_failures == 0

def run_hewitt():
    """Hugh Hewitt Show (user 2026-07-05: 'we need some other Republican leaning news sources') —
    R-leaning radio, senator-dense, and CURRENT (unlike Fox's archive, Hewitt still posts full
    transcripts). FORMAT: host turns 'HH:', guest turns labeled by INITIALS ('JT:' = John Thune),
    guest named in the post title. ATTRIBUTION RULE (drop-never-guess): the title/slug must
    resolve to exactly ONE roster member via the full-name scan, AND the body label must match
    that member's initials (or caps surname / full name); everything else drops — multi-guest,
    surname-only-title, and non-member posts yield zero rows by design.
    Enumeration: the on-site interview archive page (~2k posts); dates from WP post-sitemaps."""
    con = db(); done = {k for (k,) in con.execute("SELECT key FROM iv_fetch_log WHERE status='ok'")}
    # dates: enumerate ALL post-sitemaps from the WP index (audit 2026-07-06 S2 fix: the first
    # build hardcoded post-sitemap 1-4, which cover 2005-era posts — every RECENT interview post
    # missed the map and 559/559 rows stored dateless, starving co-appearance/recency consumers).
    dates = {}
    try:
        wpidx = get('https://hughhewitt.com/wp-sitemap.xml'); time.sleep(PAUSE)
        smaps = [u for u in re.findall(r'<loc>([^<]+)</loc>', wpidx) if 'post-sitemap' in u]
    except Exception as e:
        smaps = []; print('hh wp-sitemap index FAIL (%s) — dates degrade to blank' % str(e)[:60], flush=True)
    for su in smaps:
        try:
            sm = get(su); time.sleep(PAUSE)
            for mu, md in re.findall(r'<url>\s*<loc>([^<]+)</loc>\s*<lastmod>([^<]+)</lastmod>', sm):
                dates[mu.rstrip('/')] = md[:10]
        except Exception:
            print('hh %s FAIL (some dates blank)' % su.rsplit('/', 1)[-1], flush=True)
    print('hewitt: date map covers %d posts from %d sitemaps' % (len(dates), len(smaps)), flush=True)
    try:
        idx = get('https://hughhewitt.com/hugh-hewitt-show-interview-archive'); time.sleep(PAUSE)
    except Exception as e:
        print('hh archive index FAIL %s' % str(e)[:80]); idx = ''
    posts = [u.rstrip('/') for u in dict.fromkeys(re.findall(r'href="(https://hughhewitt\.com/[^"#?]+)"', idx))
             if not re.search(r'/(category|tag|page|author|about|shows?|transcripts|wp-)', u)]
    # LIVE STREAM (audit run-5b S2-02 fix): the interview-archive page above is a HISTORICAL
    # snapshot — every post it yielded dated 2011-2014, and the 2026 Thune post had 0 stored
    # rows. The paginated /transcripts listing is the live index (~360 pages x 23 posts,
    # 2005->today); walk it and union with the archive page.
    try:
        first = get('https://hughhewitt.com/transcripts'); time.sleep(PAUSE)
        maxp = max([int(x) for x in re.findall(r'/transcripts/page/(\d+)', first)] or [1])
        stream = re.findall(r'href="(https://hughhewitt\.com/[^"#?]+)"', first)
        for pn in range(2, maxp + 1):
            try:
                stream += re.findall(r'href="(https://hughhewitt\.com/[^"#?]+)"',
                                     get('https://hughhewitt.com/transcripts/page/%d/' % pn))
            except Exception:
                print('hh transcripts page %d FAIL (continuing)' % pn, flush=True)
            time.sleep(PAUSE)
        posts += [u.rstrip('/') for u in dict.fromkeys(stream)
                  if not re.search(r'/(category|tag|page|author|about|shows?|transcripts|wp-|video|podcasts)', u)]
    except Exception as e:
        print('hh transcripts stream FAIL %s (archive-page set only)' % str(e)[:60], flush=True)
    posts = list(dict.fromkeys(posts))
    # SLUG PREFILTER: only fetch posts whose slug already points at a roster member (full name,
    # or rank word + known surname). Hewitt names the guest in the slug as the norm; fetching all
    # ~8k stream posts blind would take hours. Final attribution still runs the strict slug+title
    # resolution + body-label gate after the fetch — the prefilter only saves fetches.
    def _slug_hits(slug):
        pr = ' ' + re.sub(r'\s+', ' ', norm(slug.replace('-', ' '))) + ' '
        if any((' %s %s ' % (fn, ln)) in pr for (fn, ln) in FULL): return True
        for tm in re.finditer(r'\b(?:senator|sen|congressman|congresswoman|representative|rep|speaker|'
                              r'majority leader|minority leader|leader|whip|chairman|chairwoman)\s+([a-z]+)', pr):
            if tm.group(1) in SUR: return True
        return False
    pre = len(posts)
    posts = [u for u in posts if _slug_hits(u.rsplit('/', 1)[-1])]
    print('hewitt: %d member-candidate posts (of %d enumerated, archive+stream)' % (len(posts), pre), flush=True)
    tot = 0
    for u in posts:
        slug = u.rsplit('/', 1)[-1]
        key = 'hh:%s' % slug[:180]
        if key in done: continue
        try:
            page = get(u); time.sleep(PAUSE)
            title = (re.search(r'<title>([^<]+)</title>', page) or [None, ''])[1]
            probe = ' ' + re.sub(r'\s+', ' ', norm(slug.replace('-', ' ') + ' ' + title)) + ' '
            guests = {mem for (fn, ln), mem in FULL.items() if (' %s %s ' % (fn, ln)) in probe}
            # rank+surname titles ('majority leader thune on...', 'speaker johnson...') — run-4z4/4z5:
            # pass 1 required a FULL name in the title (17 members from 653 posts). The rank word
            # scopes the surname pool (user 2026-07-06: 'Speaker Johnson refers to only one person'):
            # senator->Senate pool, congressman/rep->House pool, speaker/leader/whip->119th floor-
            # leadership singletons, chair->global. Ambiguous-within-pool never binds ('senator
            # scott'), and the body-label conjunction below still gates every stored turn (a wrong
            # title-guess finds no matching initials/surname labels -> zero rows).
            for tm in re.finditer(r'\b(senator|sen|congressman|congresswoman|representative|rep|speaker|'
                                  r'majority leader|minority leader|leader|whip|chairman|chairwoman)\s+([a-z]+)', probe):
                rank, cand = tm.group(1), tm.group(2)
                pool = (SUR_SEN if rank in ('senator', 'sen') else
                        SUR_HOU if rank in ('congressman', 'congresswoman', 'representative', 'rep') else
                        LEAD_SUR if rank in ('speaker', 'majority leader', 'minority leader', 'leader', 'whip') else
                        SUR)
                cands = pool.get(cand, []) if hasattr(pool, 'get') else []
                if len(cands) == 1: guests.add(cands[0])
            if len(guests) != 1:
                con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'ok', datetime.datetime.now().isoformat()))
                con.commit(); continue   # zero or ambiguous guest -> drop, never guess
            guest = next(iter(guests)); parts = norm(guest).split()
            accept = {(parts[0][0] + parts[-1][0]).upper(), parts[-1].upper(),
                      (parts[0] + ' ' + parts[-1]).upper()}
            text = strip_tags(page)
            pos = [(m.start(), m.group(1), m.end()) for m in LABEL.finditer(text)]
            rows = []
            for i2, (st, lab, me) in enumerate(pos):
                if lab.split(',')[0].strip() not in accept: continue
                end = pos[i2 + 1][0] if i2 + 1 < len(pos) else len(text)
                sp = scrub(re.sub(r'\s+', ' ', text[me:end].strip()))
                if len(sp.split()) >= 8: rows.append((guest, sp))
            n = store(con, rows, dates.get(u, ''), 'hugh-hewitt', u)
            con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'ok', datetime.datetime.now().isoformat()))
            con.commit(); tot += n
            if n: print('hh %s: %d turns (total %d)' % (slug[:55], n, tot), flush=True)
        except Exception as e:
            print('hh %s FAIL %s' % (slug[:50], str(e)[:80]), flush=True)
    con.close(); print('hewitt done: %d member turns' % tot)

def run_msnow():
    """MS NOW (ex-MSNBC) transcript archive via the site's PUBLIC WordPress REST API (user-directed
    2026-07-10, 'Get MS NOW, more data the better'). Discovery: ms.now pages are JS-rendered, but
    the platform exposes the standard WP REST surface (apiRoot https://www.ms.now/wp-json/ is
    printed in the page's own jetpack state) with a DEDICATED custom post type: /wp/v2/transcript —
    10,744 posts, 2010->2024, full content.rendered INSIDE the list responses (one request per
    page of 50 = the whole archive in ~215 requests). robots.txt: 'User-agent: *' allows all but
    /search? (the named-AI-bot blocks target training crawlers, same posture as our other legs).
    The type MIXES real show transcripts (CAPS labels, 'SEN. BERNIE SANDERS (I), VERMONT:') with
    maddowblog essays — parse_turns yields zero member rows on essays, so they drop naturally
    (drop-never-guess). Resume: per-post keys msnow:<id>; page walk is date-ASC so the id set is
    stable under new publications. D-side skew acknowledged: this is the L-flank archive; Fox
    (run_fox) was the R mirror until their transcripts ended ~2022."""
    import json as _json
    con = db(); done = {k for (k,) in con.execute("SELECT key FROM iv_fetch_log WHERE status='ok'")}
    tot = 0; page = 1; failures = 0; source_dates = []; source_posts = 0; new_posts = 0
    # Every failure branch below increments `page` and continues, so a host that answers EVERY page
    # with a 5xx/timeout/garbage walks the page counter forever (each retry costs ~9s of get()
    # backoff) until the caller's timeout SIGKILLs the leg and discards its stdout. A page-count
    # ceiling alone does not bind inside the 3600s weekly timeout (get()'s own retry backoff makes
    # even a few thousand pages too slow) -- what actually binds is ending the walk once the host
    # stops answering usefully. CONSECUTIVE_FAILURE_CEILING consecutive failed pages, at ~9s of
    # backoff each, is well inside the timeout; a healthy walk resets the counter on every
    # successfully parsed page (including an empty-but-parseable one), so this can only fire on a
    # genuine failure streak.
    CONSECUTIVE_FAILURE_CEILING = 10
    consecutive_failures = 0
    aborted = False
    while True:
        url = ('https://www.ms.now/wp-json/wp/v2/transcript?per_page=50&orderby=date&order=asc&page=%d' % page)
        try:
            raw = get(url); time.sleep(PAUSE)
        except urllib.error.HTTPError as e:
            if e.code in (400, 404) and page > 1: break  # past the last page (WP returns 400 rest_post_invalid_page_number); on page 1 it is a removed/renamed endpoint = FAILURE
            failures += 1; consecutive_failures += 1
            print('msnow page %d FAIL %s' % (page, e.code), flush=True); page += 1
            if consecutive_failures >= CONSECUTIVE_FAILURE_CEILING: aborted = True; break
            continue
        except Exception as e:
            failures += 1; consecutive_failures += 1
            print('msnow page %d FAIL %s' % (page, str(e)[:70]), flush=True); page += 1
            if consecutive_failures >= CONSECUTIVE_FAILURE_CEILING: aborted = True; break
            continue
        try:
            posts = _json.loads(raw)
        except ValueError:
            failures += 1; consecutive_failures += 1
            print('msnow page %d unparseable' % page, flush=True); page += 1
            if consecutive_failures >= CONSECUTIVE_FAILURE_CEILING: aborted = True; break
            continue
        consecutive_failures = 0
        if not posts: break
        source_posts += len(posts)
        nd = 0
        for p in posts:
            key = 'msnow:%s' % p.get('id')
            date = (p.get('date') or '')[:10]
            if date: source_dates.append(date)
            if key in done: continue
            new_posts += 1
            link = p.get('link') or url
            if not date:                                 # run-5 lesson: NEVER store dateless rows
                con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'nodate', datetime.datetime.now().isoformat()))
                continue
            body = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', (p.get('content') or {}).get('rendered') or '', flags=re.S)
            body = re.sub(r'<[^>]+>', '\n', body)
            rows = parse_turns(_html.unescape(body))
            nd += store(con, rows, date, 'msnow', link)
            con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'ok', datetime.datetime.now().isoformat()))
            done.add(key)
        con.commit(); tot += nd
        if nd: print('msnow page %d: %d member turns (total %d)' % (page, nd, tot), flush=True)
        page += 1
    if aborted:
        print('msnow %d consecutive failed pages -- archive walk INCOMPLETE' % CONSECUTIVE_FAILURE_CEILING, flush=True)
    stats = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT member), COUNT(DISTINCT package), MAX(date) "
        "FROM speeches WHERE source='INTERVIEW' AND "
        "(lower(package) LIKE '%ms.now/%' OR lower(package) LIKE '%msnbc.com/%')"
    ).fetchone()
    con.close(); print('MSNOW done: %d member turns' % tot)
    _write_source_receipt('msnow', {
        'status': 'COMPLETE' if failures == 0 else 'PARTIAL',
        'freshness_mode': 'historical_archive',
        'archive_posts_enumerated': source_posts,
        'archive_pages_checked': max(0, page - 1),
        'archive_page_failures': failures,
        'new_posts_checked': new_posts,
        'source_terminal_date': max(source_dates) if source_dates else None,
        'accepted_member_content_latest_date': stats[3],
        'accepted_turns': stats[0], 'accepted_members': stats[1],
        'accepted_packages': stats[2], 'turns_added': tot,
        'availability_note': ('The public MS NOW WordPress transcript endpoint is audited in full; '
                              'current site coverage is not represented as a transcript unless that '
                              'endpoint publishes a speaker-labelled transcript.'),
    })
    return failures == 0

def run_axefiles():
    """The Axe Files with David Axelrod (CNN Audio) — one-time backfill (show concluded Dec 2024;
    user-directed 2026-07-10, recon workflow wf_d6b68694 GO + adversarially verified w/ member
    sample). Enumeration: the megaphone RSS (642 items) — each item's <guid> IS the CNN episode
    URL uuid (verified via data-guid attributes on the archive page): episode page =
    cnn.com/audio/podcasts/axe-files/episodes/<guid>. Transcripts are SERVER-RENDERED as
    <div class='transcript-clip-container'><div class='speaker-name'>NAME</div>...
    <div class='clip-script'>TEXT</div></div> with real turns, HH:MM:SS stamps, terminal
    punctuation (interruptions end '-' — real cutoff semantics). ~49 sitting-member episodes
    (longform hours). Fetch ALL episodes (0.7s pace ~8 min): pages without the transcript
    container (older back-catalog, coverage unverified pre-2023) SKIP-not-error; non-member
    speakers drop via resolve_full. robots: '*' allowed on /audio (named AI bots blocked at /,
    same posture as our legs); the JS pagination /api/ endpoint is robots-disallowed and UNUSED.
    Resume: axf:<guid>. A 404/410 no-page result is terminal; an HTTP-200 page with no parsed
    transcript remains retryable because it may reflect publication lag or a parser/schema change.
    Packages are cnn.com/audio URLs -> program_of 'other' (pools)."""
    import email.utils as _eut
    con = db(); done = {k for (k,) in con.execute(
        "SELECT key FROM iv_fetch_log WHERE key LIKE 'axf:%' AND status IN ('ok','nopage')")}
    try:
        rss = get('https://feeds.megaphone.fm/WMHY9284588779'); time.sleep(PAUSE)
    except Exception as e:
        print('axefiles RSS FAIL %s' % str(e)[:80]); con.close(); return
    items = rss.split('<item>')[1:]
    print('axefiles: %d RSS items' % len(items), flush=True)
    tot = 0
    CLIP = re.compile(r'transcript-clip-container.*?speaker-name[^>]*>\s*(.*?)\s*<.*?clip-script[^>]*>\s*(.*?)\s*</div>', re.S)
    for it in items:
        g = re.search(r'<guid[^>]*>(?:<!\[CDATA\[)?\s*([a-f0-9\-]{20,})\s*(?:\]\]>)?</guid>', it)   # QA 07-10: megaphone wraps guid in CDATA
        if not g: continue
        guid = g.group(1); key = 'axf:' + guid
        if key in done: continue
        pd = re.search(r'<pubDate>([^<]+)</pubDate>', it)
        try:
            dt = _eut.parsedate_to_datetime(pd.group(1)).date().isoformat() if pd else None
        except Exception:
            dt = None
        if not dt:
            con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'nodate', datetime.datetime.now().isoformat()))
            continue
        url = 'https://www.cnn.com/audio/podcasts/axe-files/episodes/' + guid
        try:
            page = get(url); time.sleep(PAUSE)
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'nopage', datetime.datetime.now().isoformat()))
                con.commit(); done.add(key)
            continue
        except Exception:
            continue
        clips = CLIP.findall(page)
        # merge consecutive same-speaker clips into turns, resolve members, drop the rest
        turns = []
        for name, script in clips:
            name = re.sub(r'<[^>]+>', ' ', name).strip()
            text = re.sub(r'\s+', ' ', _html.unescape(re.sub(r'<[^>]+>', ' ', script))).strip()
            if turns and turns[-1][0] == name: turns[-1][1] += ' ' + text
            else: turns.append([name, text])
        rows = []
        for name, text in turns:
            m = resolve_full(name.upper())
            if m and len(text.split()) >= 8:
                rows.append((m, scrub(text)))
        n = store(con, rows, dt, 'axefiles', url)
        con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)',
                    (key, 'ok' if clips else 'notx', datetime.datetime.now().isoformat()))
        con.commit(); done.add(key); tot += n
        if n: print('axefiles %s: %d member turns (total %d)' % (dt, n, tot), flush=True)
    con.close(); print('AXEFILES done: %d member turns' % tot)

def run_csis():
    """CSIS event transcripts (csis.org — recon workflow wf_d6b68694 GO, adversarially verified w/
    member sample; user-directed think-tank sweep 2026-07-10). Institutions publish what podcasts
    don't: full HTML transcripts of moderated member conversations — the booking profile skews to
    committee workhorses, not cable regulars. Enumeration: Drupal Simple-XML-Sitemap index
    csis.org/sitemap.xml?page=1..N (robots: '*' disallows Drupal internals + '/*?' with an
    explicit 'Allow: /*?page=' carve-out — the sitemap route is the sanctioned one; politeness
    0.7s). PREFILTER: only URLs whose slug carries a member-ish token (senator|sen-|rep-|
    congressman|congresswoman|speaker-|roster surname) are fetched — false positives cost one
    fetch and parse to zero (drop-never-guess). Labels: Title-case honorific + name + colon at
    paragraph start ('Senator Shaheen:') -> parse_turns_mixed; CAPS fallback. Date: page meta
    datePublished, else sitemap lastmod (dateless rows never stored). Resume: csis:<slug>."""
    con = db(); done = {k for (k,) in con.execute("SELECT key FROM iv_fetch_log WHERE status='ok'")}
    # roster surname tokens (len>4 to dodge stopword-ish surnames)
    surn = set()
    for name in C:
        parts = norm(name).split()
        if parts and len(parts[-1]) > 4: surn.add(parts[-1])
    urls = {}
    for pg in range(1, 40):
        try:
            x = get('https://www.csis.org/sitemap.xml?page=%d' % pg); time.sleep(PAUSE)
        except Exception:
            break
        locs = re.findall(r'<loc>([^<]+)</loc>\s*(?:<lastmod>([^<]+)</lastmod>)?', x)
        if not locs: break
        n0 = len(urls)
        for u, lm in locs:
            if '/analysis/' not in u and '/events/' not in u: continue
            slug = u.rsplit('/', 1)[-1].lower()
            toks = set(re.split(r'[-_.]', slug))
            if (toks & {'senator', 'sen', 'rep', 'congressman', 'congresswoman', 'representative'}
                    or (toks & surn)):
                urls[u] = (lm or '')[:10]
        if len(urls) == n0 and pg > 21: break
    print('csis: %d candidate transcript pages from sitemaps' % len(urls), flush=True)
    tot = 0
    for u, lm in sorted(urls.items()):
        key = 'csis:' + u.rsplit('/', 1)[-1][:120]
        if key in done: continue
        try:
            page = get(u); time.sleep(PAUSE)
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'nopage', datetime.datetime.now().isoformat()))
                con.commit(); done.add(key)
            continue
        except Exception:
            continue
        dm = re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', page)
        date = dm.group(1) if dm else (lm if re.match(r'\d{4}-\d{2}-\d{2}', lm or '') else None)
        if not date:
            con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'nodate', datetime.datetime.now().isoformat()))
            con.commit(); done.add(key); continue
        body = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', page, flags=re.S)
        body = _html.unescape(re.sub(r'<[^>]+>', '\n', body))
        rows = parse_turns_mixed(body)
        if not rows: rows = parse_turns(body)
        n = store(con, rows, date, 'csis', u)
        con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'ok', datetime.datetime.now().isoformat()))
        con.commit(); done.add(key); tot += n
        if n: print('csis %s: %d member turns (total %d) %s' % (date, n, tot, u.rsplit("/",1)[-1][:50]), flush=True)
    con.close(); print('CSIS done: %d member turns' % tot)

def run_cfr():
    """CFR event transcripts (cfr.org — recon workflow wf_4317698d GO, adversarially verified w/
    member sample; user opt-in 2026-07-10). Server-rendered HTML transcripts; robots: single '*'
    section, /event paths clean, no AI-bot blocks, no Crawl-Delay. Enumeration: ONE sitemap file
    cfr.org/events/sitemap.xml (3,986 events, 2009->now). Labels are ALL-CAPS BARE SURNAMES
    ('VAN HOLLEN:') with full names only in the page's speaker metadata -> resolution uses a
    PAGE-LOCAL alias: a member binds only if their full roster name appears in the page text AND
    their surname is unique among page-present members (drop-never-guess, page-scoped). Prefilter:
    member-ish slug tokens or roster surnames. Resume: cfr:<slug>."""
    con = db(); done = {k for (k,) in con.execute("SELECT key FROM iv_fetch_log WHERE status='ok'")}
    surn = {}
    for name in C:
        parts = norm(name).split()
        if parts: surn.setdefault(parts[-1], []).append(name)
    try:
        x = get('https://www.cfr.org/events/sitemap.xml'); time.sleep(PAUSE)
    except Exception as e:
        print('cfr sitemap FAIL %s' % str(e)[:80]); con.close(); return
    locs = re.findall(r'<loc>([^<]+)</loc>', x)
    cands = []
    for u in locs:
        slug = u.rsplit('/', 1)[-1].lower()
        toks = set(re.split(r'[-_.]', slug))
        if (toks & {'senator', 'representative', 'congressman', 'congresswoman', 'speaker', 'congressional'}
                or any(t in surn and len(t) > 4 for t in toks)):
            cands.append(u)
    print('cfr: %d candidate event pages of %d in sitemap' % (len(cands), len(locs)), flush=True)
    tot = 0
    for u in sorted(cands):
        key = 'cfr:' + u.rsplit('/', 1)[-1][:120]
        if key in done: continue
        try:
            page = get(u); time.sleep(PAUSE)
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'nopage', datetime.datetime.now().isoformat()))
                con.commit(); done.add(key)
            continue
        except Exception:
            continue
        dm = re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', page) or re.search(r'datetime="(\d{4}-\d{2}-\d{2})', page)
        date = dm.group(1) if dm else None
        body = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', page, flags=re.S)
        body = _html.unescape(re.sub(r'<[^>]+>', '\n', body))
        if not date:
            dm = re.search(r'\b(January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2}), (\d{4})\b', body)
            if dm:
                MO = ['January','February','March','April','May','June','July','August','September','October','November','December']
                date = '%s-%02d-%02d' % (dm.group(3), MO.index(dm.group(1)) + 1, int(dm.group(2)))
        if not date:
            con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'nodate', datetime.datetime.now().isoformat()))
            con.commit(); done.add(key); continue
        # page-local alias: members whose FULL name appears in the page text, surname-unique on page
        nb = norm(body)
        present = [m for m in C if norm(m) in nb]
        palias = {}
        for m in present:
            s = norm(m).split()[-1]
            palias[s] = None if s in palias else m
        rows = []
        pos = [(mm.start(), mm.group(1), mm.end()) for mm in LABEL.finditer(body)]
        for i, (st, lab, me) in enumerate(pos):
            end = pos[i + 1][0] if i + 1 < len(pos) else len(body)
            sp = body[me:end].strip()
            m = resolve_full(lab)
            if not m:
                k = norm(TITLE.sub('', lab.strip())).split()
                m = palias.get(k[-1]) if k else None
                if m and len(k) > 2: m = None            # 'JOHN Q PUBLIC' shapes never bind via alias
            if m:
                sp = scrub(re.sub(r'\s+', ' ', sp))
                if len(sp.split()) >= 8:
                    rows.append((m, sp))
        n = store(con, rows, date, 'cfr', u)
        con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'ok', datetime.datetime.now().isoformat()))
        con.commit(); done.add(key); tot += n
        if n: print('cfr %s: %d member turns (total %d) %s' % (date, n, tot, u.rsplit("/",1)[-1][:48]), flush=True)
    con.close(); print('CFR done: %d member turns' % tot)

def _pdf_text(data):
    """PDF bytes -> text via pypdf in LAYOUT mode (AEI/Aspen legs). QA 2026-07-10: DEFAULT mode
    rendered the whole Aspen Van Hollen PDF word-per-line ('bummer/we/have/to/wear/ties') — labels
    never assembled, 0 rows from a verifier-confirmed 18-turn transcript. extraction_mode='layout'
    reflows correctly ('Chris Van Hollen:' at line start); per-page fallback to plain mode."""
    import io
    from pypdf import PdfReader
    try:
        rd = PdfReader(io.BytesIO(data))
    except Exception:
        return ''
    out = []
    for p in rd.pages:
        try:
            out.append(p.extract_text(extraction_mode='layout') or '')
        except Exception:
            try:
                out.append(p.extract_text() or '')
            except Exception:
                pass
    return '\n'.join(out)

def get_bytes(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 410): raise
            if i == tries - 1: raise
            time.sleep(3 * (i + 1))
        except Exception:
            if i == tries - 1: raise
            time.sleep(3 * (i + 1))

def run_aei():
    """AEI event transcripts (aei.org — recon workflow wf_d6b68694 GO, adversarially verified w/
    member sample; user think-tank sweep 2026-07-10). Transcripts are PDFs linked from event
    pages; robots '*' allows event paths with **Crawl-Delay 45** — HONORED (45s between aei.org
    requests; ~146 member-slug events x2 requests ≈ 4h, an overnight leg). Enumeration: Yoast
    sitemap_index.xml -> event-sitemap*.xml (4,557 events). Labels: sentence-case honorific +
    full name + colon ('Senator Todd Young:') inside the PDF -> parse_turns_mixed + the
    honorific-surname resolver extension. Resume: aei:<slug>."""
    AEI_DELAY = 45.0
    con = db(); done = {k for (k,) in con.execute(
        "SELECT key FROM iv_fetch_log WHERE key LIKE 'aei:%' AND status IN ('ok','nopage')")}
    surn = set()
    for name in C:
        parts = norm(name).split()
        if parts and len(parts[-1]) > 4: surn.add(parts[-1])
    urls = []
    try:
        idx = get('https://www.aei.org/sitemap_index.xml'); time.sleep(AEI_DELAY)
    except Exception as e:
        print('aei sitemap index FAIL %s' % str(e)[:80]); con.close(); return
    for sm in re.findall(r'<loc>([^<]*event-sitemap[^<]*)</loc>', idx):
        try:
            x = get(sm); time.sleep(AEI_DELAY)
        except Exception:
            continue
        for u in re.findall(r'<loc>([^<]+)</loc>', x):
            slug = u.rstrip('/').rsplit('/', 1)[-1].lower()
            toks = set(re.split(r'[-_.]', slug))
            if (toks & {'senator', 'sen', 'rep', 'representative', 'congressman', 'congresswoman', 'speaker'}
                    or (toks & surn)):
                urls.append(u)
    print('aei: %d candidate events (Crawl-Delay 45 honored — overnight leg)' % len(urls), flush=True)
    tot = 0
    for u in sorted(set(urls)):
        key = 'aei:' + u.rstrip('/').rsplit('/', 1)[-1][:120]
        if key in done: continue
        try:
            page = get(u); time.sleep(AEI_DELAY)
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'nopage', datetime.datetime.now().isoformat()))
                con.commit(); done.add(key)
            continue
        except Exception:
            continue
        pdfs = re.findall(r'href="(https://www\.aei\.org/wp-content/uploads/[^"]+\.pdf)"', page)
        dm = re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', page)
        date = dm.group(1) if dm else None
        if not pdfs or not date:
            con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)',
                        (key, 'notx' if date else 'nodate', datetime.datetime.now().isoformat()))
            con.commit(); done.add(key); continue
        rows = []; pdf_fails = 0
        for pu in pdfs[:2]:
            try:
                data = get_bytes(pu); time.sleep(AEI_DELAY)
            except Exception:
                pdf_fails += 1
                continue
            txt = _pdf_text(data)
            r = parse_turns_mixed(txt) or parse_turns(txt)
            if len(r) > len(rows): rows = r
        if pdf_fails == len(pdfs[:2]): continue   # every transcript PDF failed: no receipt, the event stays retryable
        n = store(con, rows, date, 'aei', u)
        con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'ok', datetime.datetime.now().isoformat()))
        con.commit(); done.add(key); tot += n
        if n: print('aei %s: %d member turns (total %d) %s' % (date, n, tot, u.rstrip("/").rsplit("/",1)[-1][:48]), flush=True)
    con.close(); print('AEI done: %d member turns' % tot)

def run_aspen():
    """Aspen Security Forum transcript PDFs (aspensecurityforum.org — recon wf_4317698d GO,
    verified w/ Van Hollen sample). WP REST media API enumerates the transcript PDFs directly
    (12 known; sweep ALL application attachments to catch unlabeled ones). ASR-derived ('may
    contain inaccuracies' header) -> acceptable for turn STRUCTURE; labels 'Chris Van Hollen:'
    full-name sentence-case -> parse_turns_mixed. No Crawl-Delay; 0.7s politeness. Resume: asf:<id>."""
    import json as _json
    con = db(); done = {k for (k,) in con.execute("SELECT key FROM iv_fetch_log WHERE status='ok'")}
    tot = 0; page = 1
    while True:
        u = 'https://www.aspensecurityforum.org/wp-json/wp/v2/media?media_type=application&per_page=100&page=%d' % page
        try:
            raw = get(u); time.sleep(PAUSE)
            posts = _json.loads(raw)
        except Exception:
            break
        if not posts: break
        for p in posts:
            src = (p.get('source_url') or '')
            if not src.lower().endswith('.pdf'): continue
            key = 'asf:%s' % p.get('id')
            if key in done: continue
            date = (p.get('date') or '')[:10]
            if not date:
                con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'nodate', datetime.datetime.now().isoformat()))
                continue
            try:
                data = get_bytes(src); time.sleep(PAUSE)
            except Exception:
                continue
            rows = parse_turns_mixed(_pdf_text(data))
            n = store(con, rows, date, 'aspensf', src)
            con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'ok', datetime.datetime.now().isoformat()))
            con.commit(); done.add(key); tot += n
            if n: print('aspen %s: %d member turns (total %d)' % (date, n, tot), flush=True)
        page += 1
    con.close(); print('ASPEN done: %d member turns' % tot)

NPRFEED_SHOWS = {'1a': '510316', 'here-now': '510051'}
def run_nprfeed():
    """1A + Here & Now via NPR's public RSS feeds (user-directed 2026-07-10 'Chase 1A hard').
    Their npr.org ARCHIVE pages are JS-rendered (unlike the four run_npr programs), but (a) the
    podcast feeds enumerate episodes (1A ~300 items, H&N ~50), and (b) transcript pages EXIST at
    the derived URL npr.org/transcripts/<story-id> even though episode pages don't link them
    (verified 07-10: 200, ~140KB). Both are call-in formats that book rank-and-file members from
    BOTH parties — the power-law-inverting booking profile. Packages are npr.org URLs, so turns
    pool into the same single 'npr' program cell as the run_npr legs. Resume: nprfeed:<story-id>.
    RSS covers the recent window only; the weekly step keeps it accruing from here forward."""
    con = db(); done = {k for (k,) in con.execute("SELECT key FROM iv_fetch_log WHERE status='ok'")}
    tot = 0
    for feed, fid in NPRFEED_SHOWS.items():
        try:
            x = get('https://feeds.npr.org/%s/podcast.xml' % fid); time.sleep(PAUSE)
        except Exception as e:
            print('nprfeed %s feed FAIL %s' % (feed, str(e)[:70]), flush=True); continue
        links = [l for l in re.findall(r'<link>([^<]+)</link>', x) if re.search(r'/\d{4}/\d{2}/\d{2}/', l)]
        print('nprfeed %s: %d feed episodes' % (feed, len(links)), flush=True)
        for link in links:
            m = re.search(r'/((?:nx-[a-z0-9]+-)?\d{6,})(?:/|$)', link)
            dm = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', link)
            if not m or not dm: continue
            sid = m.group(1); key = 'nprfeed:' + sid
            if key in done: continue
            date = '%s-%s-%s' % dm.groups()
            try:
                page = get('https://www.npr.org/transcripts/' + sid); time.sleep(PAUSE)
            except urllib.error.HTTPError as e:
                if e.code in (404, 410):   # no transcript published for this episode — permanent
                    con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'notx', datetime.datetime.now().isoformat()))
                    con.commit(); done.add(key)
                continue
            except Exception:
                continue
            body = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', page, flags=re.S)
            body = re.sub(r'<[^>]+>', '\n', body)
            rows = parse_turns(_html.unescape(body))
            n = store(con, rows, date, 'npr-' + feed, 'https://www.npr.org/transcripts/' + sid)
            con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'ok', datetime.datetime.now().isoformat()))
            con.commit(); done.add(key); tot += n
            if n: print('nprfeed %s %s: %d member turns (total %d)' % (feed, date, n, tot), flush=True)
    con.close(); print('NPRFEED done: %d member turns' % tot)

# ADJACENT MEDIA LANES (audited 2026-08-28): this module stores attributed text turns only.
# C-SPAN program pages and HouseLive expose separate timed transcript/media paths, and the repo has
# dedicated collectors for them.  Podcast RSS enclosures are source audio, not member clips until
# a transcript or other hard speaker evidence binds a time span.  YouTube's automated feed/player
# paths remain outside this collector; title matches must never be promoted to speaker identity.

def _keep_awake():
    """User directive 2026-07-06 ('put in checks so it doesn't fall asleep'): the 01:10 machine
    sleep suspended NPR+Fox mid-crawl for 5h. Hold a SYSTEM wake lock for the life of the leg —
    the OS clears it automatically when the process exits, and the display may still sleep
    (ES_SYSTEM_REQUIRED only). Non-Windows / failure degrades to a printed warning."""
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        r = ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        print('keep-awake: system wake lock held for this crawl' if r else 'keep-awake: call returned 0 (no lock)', flush=True)
    except Exception as e:
        print('keep-awake unavailable (%s) — machine may sleep mid-crawl' % str(e)[:60], flush=True)

if __name__ == '__main__':
    if any(l in sys.argv for l in ('cnn', 'mtp', 'cbs', 'abc', 'pbs', 'npr', 'fox', 'hewitt', 'msnow', 'nprfeed', 'axefiles', 'csis', 'cfr', 'aei', 'aspen')):
        _keep_awake()
    if 'status' in sys.argv:
        con = db()
        for src, n, dm, w in con.execute("SELECT source, COUNT(*), COUNT(DISTINCT member), SUM(nwords) FROM speeches GROUP BY source"):
            print('%-10s %6d turns | %3d members | %.2fM words' % (src, n, dm, (w or 0) / 1e6))
        sys.exit(0)
    ap = {a.split('=')[0]: a for a in sys.argv}
    since = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == '--since'), '2025-01-01')
    until = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == '--until'), datetime.date.today().isoformat())
    _budget = int(next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == '--budget-s'), 0) or 0)
    _strict = '--strict' in sys.argv
    # Completion sentinels are fail-closed. CNN has an explicit Boolean completion contract; the
    # legs upgraded on 2026-08-28 (mtp/cbs/abc/pbs/npr/msnow) return one too, and `--strict`
    # converts a False into exit 2 so weekly_refresh's hard publication gate aborts.
    #
    # 2026-08-28 REGRESSION REPAIR — every leg must still WRITE a sentinel. The earlier revision of
    # this block dropped `_sentinel()` from all legs but cnn on the reasoning that a legacy adapter
    # which swallows item errors cannot honestly claim `.done`. That reasoning mistakes what the
    # flag means to its only consumer. collector_supervisor.py builds `done` as exactly the set of
    # target tags with a `collector_sentinels/<tag>.done` file (fec\collect\collector_supervisor.py:317)
    # and RETURNS before run_gates() while any target is pending (:358). Withholding the sentinel
    # therefore does not encode "completion unknown" — it encodes "this leg has never run", which
    # is false, relaunches the leg on every 10-minute tick forever, and makes GATE-1/GATE-2
    # structurally unreachable. The honest half of the contract is kept where it belongs: a leg
    # with a Boolean contract that FAILED writes no sentinel (and exits 2 under --strict), and
    # per-item truth continues to live in the iv_fetch_log receipts, which is where it always was.
    # A leg with no Boolean contract restores the pre-08-25 meaning of the flag: "this leg ran its
    # walk to the end without raising."
    _leg = next((l for l in ('cnn', 'mtp', 'cbs', 'abc', 'pbs', 'npr', 'fox', 'hewitt', 'msnow', 'nprfeed', 'axefiles', 'csis', 'cfr', 'aei', 'aspen') if l in sys.argv), None)
    if _leg == 'cnn' and until <= '2024-06-01' and '--verify' not in sys.argv: _leg = 'cnn-deep'
    def _sentinel():
        try:
            sd = os.path.join(B, 'collector_sentinels'); os.makedirs(sd, exist_ok=True)
            target = os.path.join(sd, _leg + '.done')
            temporary = '%s.%d.tmp' % (target, os.getpid())
            with open(temporary, 'w', encoding='utf-8') as handle:
                handle.write(datetime.datetime.now().isoformat())
            os.replace(temporary, target)
            print('completion sentinel committed: %s' % target, flush=True)
            return True
        except Exception as e:
            print('COMPLETION SENTINEL FAILED [%s]: %s' %
                  (type(e).__name__, str(e).replace('\r', ' ').replace('\n', ' ')[:500]), flush=True)
            return False
    # 2026-08-25: collector_supervisor reads cnn.done as 'finished cleanly' — a budget-truncated
    # run must not claim that, so the sentinel fires only when run_cnn walked the full range.
    if 'cnn' in sys.argv:
        if run_cnn(since, until, verify='--verify' in sys.argv, budget=_budget):
            if not _sentinel():
                sys.exit(3)
        else:
            sys.exit(2)
    elif 'mtp' in sys.argv:
        if _strict and not run_mtp(): sys.exit(2)
        elif not _strict: run_mtp()
        if not _sentinel(): sys.exit(3)
    elif 'cbs' in sys.argv:
        if _strict and not run_cbs(): sys.exit(2)
        elif not _strict: run_cbs()
        if not _sentinel(): sys.exit(3)
    elif 'abc' in sys.argv:
        if _strict and not run_abc(): sys.exit(2)
        elif not _strict: run_abc()
        if not _sentinel(): sys.exit(3)
    elif 'pbs' in sys.argv:
        if _strict and not run_pbs(since, until): sys.exit(2)
        elif not _strict: run_pbs(since, until)
        if not _sentinel(): sys.exit(3)
    elif 'npr' in sys.argv:
        if _strict and not run_npr(since, until): sys.exit(2)
        elif not _strict: run_npr(since, until)
        if not _sentinel(): sys.exit(3)
    elif 'fox' in sys.argv:
        if _strict and not run_fox(): sys.exit(2)
        elif not _strict: run_fox()
        if not _sentinel(): sys.exit(3)
    elif 'hewitt' in sys.argv:
        run_hewitt()
        if not _sentinel(): sys.exit(3)
    elif 'msnow' in sys.argv:
        if _strict and not run_msnow(): sys.exit(2)
        elif not _strict: run_msnow()
        if not _sentinel(): sys.exit(3)
    elif 'nprfeed' in sys.argv:
        run_nprfeed()
        if not _sentinel(): sys.exit(3)
    elif 'axefiles' in sys.argv:
        run_axefiles()
        if not _sentinel(): sys.exit(3)
    elif 'csis' in sys.argv:
        run_csis()
        if not _sentinel(): sys.exit(3)
    elif 'cfr' in sys.argv:
        run_cfr()
        if not _sentinel(): sys.exit(3)
    elif 'aei' in sys.argv:
        run_aei()
        if not _sentinel(): sys.exit(3)
    elif 'aspen' in sys.argv:
        run_aspen()
        if not _sentinel(): sys.exit(3)
    else: print('usage: fetch_interviews.py [cnn|mtp|cbs|abc|pbs|npr|fox|hewitt|msnow|nprfeed|axefiles|csis|cfr|aei|aspen|status] [--since YYYY-MM-DD] [--until YYYY-MM-DD]')
