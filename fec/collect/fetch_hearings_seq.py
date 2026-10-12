# -*- coding: utf-8 -*-
"""All-committee hearing DOWNLOAD + TRANSCRIPT-SEQUENCE CACHE (2026-07-10 refinement, user: isolate
member-vs-member antagonism + count member-vs-WITNESS only when HOSTILE — aggressive language, blunt
correction, interrogation — NOT routine procedural time-management, which inflated senior appropriators
like Collins in the v1 prelim).

This is the SLOW part (download), done ONCE and CACHED so the hostility lexicon can be tuned offline
in seconds. Stores a COMPACT ordered turn-sequence per hearing to a SEPARATE hearings_seq.db (keeps the
user's social_media.db unbloated); hearing_meta (chair/contentiousness for confound controls) stays in
the main DB. The BUILDER (hearing_antagonism_build.py) computes refined events from the cache:
  member-vs-member interrupt  = member cuts off a COLLEAGUE (kind is another member) -> clean antagonism
  member-vs-witness HOSTILE   = member turn adjacent to a WITNESS carrying hostile markers (lexicon) OR
                                aggressively cutting off a witness -> counts; procedural time-mgmt does NOT
Compact turn = [kind, party, dash, text]: kind = member-name | 'W'(witness) | 'C'(chair) | 'O'(other);
text stored ONLY for members (what the hostility lexicon reads), capped; party for cross-party detection.
Resume: hearings_seq.db seq_log 'seq:<pid>'. Era: 2019-2024 full HTML (2025+ PDF deferred)."""
import sys, os, re, io, time, json, csv, zipfile, sqlite3, datetime, urllib.request, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from paths import ROOT
B = ROOT
SEQDB = B + r'\hearings_seq.db'
_KF = B + r'\fec\govinfo_key.txt'
KEY = os.environ.get('GOVINFO_KEY') or (open(_KF).read().strip() if os.path.exists(_KF) else 'DEMO_KEY')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) research collector'}
TEXTCAP = 1500

# ---- CORPUS PROVENANCE (2026-09-01) ----------------------------------------------------------
# The chamber-scope fix (2026-08-18) corrected FUTURE collection only: rows already in a sequence
# database were resolved at collection time and stay misattributed. Nothing recorded WHICH corpus a
# database was, so a consumer could not tell a corrected corpus from the 2026-07-11 pre-fix one
# (7,174 packages, 9.7% cross-chamber). A receipt now sits beside every sequence database and names
# the attribution contract its rows were collected under. It may claim the corrected contract ONLY
# when the run was a fresh --rebuild into a new path, or when it CHAINS to a previous receipt that
# already claimed it and is bound to the database's hash as it stood before this run. Any other
# case -- a first receipt over an inherited database, a database edited out of band, a swapped
# file -- declares UNKNOWN_ATTRIBUTION, and the display-feed producer refuses it by name.
from members.hearing_display_contract import ATTRIBUTION_CONTRACT, sha256_file
SEQ_RECEIPT_SCHEMA = 'hearings-seq-receipt-v1'
UNKNOWN_ATTRIBUTION = 'unknown-pre-fix-corpus'


class RebuildTargetError(RuntimeError):
    """--rebuild was aimed at a path that already holds a database."""

def _strip_api_key(url):   # keep the secret OUT of the request URL (logs/proxies) — mirrors CREC collector
    import urllib.parse as _up
    p = _up.urlsplit(url or '')
    q = [(k, v) for k, v in _up.parse_qsl(p.query, keep_blank_values=True) if k.lower() not in ('api_key', 'key', 'token')]
    return _up.urlunsplit((p.scheme, p.netloc, p.path, _up.urlencode(q), p.fragment))
def get(url, binary=False, tries=4, api_key=None):
    headers = dict(UA)
    if api_key: headers['X-Api-Key'] = api_key       # govinfo key in HEADER, never the URL
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(_strip_api_key(url), headers=headers), timeout=120) as r:
                d = r.read(); return d if binary else d.decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            if e.code in (404, 410): raise
            if i == tries - 1: raise
            time.sleep(4 * (i + 1))
        except Exception:
            if i == tries - 1: raise
            time.sleep(4 * (i + 1))

C = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}
SFIX = {'jr', 'sr', 'ii', 'iii', 'iv'}
def surkeys(m):
    ts = [t for t in m.replace('.', '').split() if t.lower().rstrip(',') not in SFIX]
    ks = {ts[-1].upper()}
    if len(ts) >= 3: ks.add((ts[-2] + ' ' + ts[-1]).upper())
    if len(ts) >= 4: ks.add((ts[-3] + ' ' + ts[-2] + ' ' + ts[-1]).upper())
    return {re.sub(r"[^A-Z\- ']", '', k) for k in ks}
ROSTER = collections.defaultdict(list)
for m in C:
    ch = (C[m].get('chamber') or '').lower()
    for k in surkeys(m): ROSTER[(k, ch)].append(m)
def chamber_of_package(pid):
    """govinfo CHRG package ids carry the chamber: ...hhrg... = House, ...shrg... = Senate.
    Anything else (joint/unknown) returns None and resolution falls back to the UNION rule."""
    p = (pid or '').lower()
    if 'hhrg' in p: return 'house'
    if 'shrg' in p: return 'senate'
    return None

def resolve(sur, chamber=None):
    """Resolve a speaker surname to a roster member, or refuse.

    FIXED 2026-08-18 (Paul-ordered). The previous body iterated ['senate','house'] and returned
    the first chamber holding a unique match. Called with no chamber -- which line 138 did for
    every speaker -- that is a SENATE-FIRST GUESS, and it silently handed every ambiguous
    surname to the senator. Measured over the shipped hearings_seq.db (7,174 packages,
    679,221 attributed turns): 46,190 senator-turns sat inside House packages and 19,722
    House-turns inside Senate packages -- 65,912 cross-chamber, 9.7%. The signature was
    unmistakable: Jerry Moran 4,571 turns / Nathaniel Moran 0; Tina Smith 9,109 / all four
    House Smiths 0; Mike Lee 7,090 / all three House Lees 0.

    The rule now matches the sibling CR collector (fetch_congressional_record.py:109-121),
    whose comment already said "never restore the legacy Senate-first guess" -- that fix was
    simply never propagated here: scope to the package's chamber when it is known, and when it
    is not (joint hearings), UNION the chambers and require uniqueness. Never first-wins."""
    sur = re.sub(r"[^A-Z\- ']", '', sur.upper()).strip()
    chambers = [chamber] if chamber in ('house', 'senate') else ['house', 'senate']
    cand = []
    for ch in chambers:
        for m in ROSTER.get((sur, ch), []):
            if m not in cand: cand.append(m)
    return cand[0] if len(cand) == 1 else None
PARTY = {}
for r in csv.DictReader(open(B + r'\fec\legislators.csv', encoding='utf-8')):
    if r.get('bioguide_id'): PARTY[r['bioguide_id']] = (r.get('party') or '')[:1].upper()
BIO = {m: C[m].get('bioguide') for m in C}
def party_of(m): return PARTY.get(BIO.get(m), '?')

# Broadened speaker capture: members AND witnesses (govt/military/state/expert/judicial honorifics) so
# a member grilling 'Secretary Driscoll' or 'General George' is no longer INVISIBLE. Witness turns get a
# TYPE code (user 2026-07-10: control for business execs vs state/govt officials; substance shapes the
# partisan/antagonism read). Type from honorific + the 'STATEMENT OF ..., <title>' header.
MEMBER_HON = r'Mr|Mrs|Ms|Miss|Senator|Representative|Congressman|Congresswoman|Chairman|Chairwoman|Chair'
WIT_HON = (r'Secretary|Under Secretary|Deputy Secretary|Administrator|Deputy Administrator|Acting Administrator|'
           r'Director|Deputy Director|Acting Director|Commissioner|Ambassador|Comptroller|Inspector General|'
           r'General|Lieutenant General|Major General|Brigadier General|Admiral|Vice Admiral|Rear Admiral|'
           r'Colonel|Lieutenant Colonel|Lieutenant|Captain|Major|Commander|Sergeant|'
           r'Governor|Lieutenant Governor|Mayor|Dr|Professor|Justice|Judge|Chief|Sheriff|Honorable')
SPEAKER = re.compile(r'(?m)^ {0,6}(?:The )?(%s|%s)\.?[ ]+'
                     r"([A-Z][A-Za-z'\-]+(?:[ \-][A-Za-z'\-]+){0,2}?)"
                     r'(?: of ([A-Z][a-z]+(?: [A-Z][a-z]+)?))?[.,] ' % (MEMBER_HON, WIT_HON))
MEMBER_RE = re.compile(r'^(Mr|Mrs|Ms|Miss|Senator|Representative|Congressman|Congresswoman|Chairman|Chairwoman|Chair)$')
NONSPK = {'SPEAKER', 'PRESIDING', 'CLERK', 'VICE'}
ENDDASH = re.compile(r'-{2,}\s*$')

def hon_type(pre):
    """Witness TYPE from honorific alone (strong for non-Mr/Ms)."""
    p = pre.lower()
    if re.search(r'general|admiral|colonel|lieutenant|captain|major|commander|sergeant', p): return 'M'   # military
    if re.search(r'governor|mayor|sheriff', p): return 'S'                                                # state/local
    if re.search(r'dr|professor', p): return 'E'                                                          # expert/academic
    if re.search(r'justice|judge', p): return 'J'                                                         # judicial
    if re.search(r'secretary|administrator|director|commissioner|ambassador|comptroller|inspector|chief|honorable', p): return 'G'  # federal govt
    return '?'   # Mr/Ms witness -> resolve via STATEMENT OF title

def title_type(t):
    """Witness TYPE from a STATEMENT OF title/org string (for Mr/Ms witnesses)."""
    t = t.lower()
    if re.search(r'chief executive|c\.?e\.?o|chief financial|chief operating|chief technology|founder|'
                 r'president and ceo|chairman and ceo|executive vice president|\b(inc|corp|corporation|company|'
                 r'llc|holdings|bank|industries|motors|technologies|pharmaceutic|capital|partners|group)\b', t): return 'B'
    if re.search(r'governor|mayor|state (senator|representative|treasurer|attorney)|county|sheriff|city of', t): return 'S'
    if re.search(r'general|admiral|colonel|army|navy|air force|marine|joint chiefs', t): return 'M'
    if re.search(r'professor|ph\.?d|fellow|scholar|economist|university\b', t): return 'E'
    if (re.search(r'president|executive director|director|chair', t) and
            re.search(r'associat|union|coalition|federat|alliance|society|council|foundation|institut|'
                      r'center for|committee for|advocat|project|network|league|conference', t)): return 'A'
    if re.search(r'secretary|administrator|commissioner|inspector general|comptroller|department of|'
                 r'\bu\.?s\.?\b|federal|bureau|agency|office of', t): return 'G'
    return '?'

def parse_seq(txt, chamber=None):
    """Compact ordered turns. member: [name, party, dash, text, 'M'/'C']; witness: ['W', typecode, dash, '']."""
    txt = re.sub(r'<[^>]+>', ' ', txt)
    wit_type = {}   # witness surname -> type, from any 'Name, <title/org>' witness-intro span (format-robust)
    for hd in re.findall(r'(?:STATEMENT|TESTIMONY|PREPARED STATEMENT) OF ([A-Z][A-Za-z .,\'\-]{3,120})', txt):
        toks = [t.strip('.,') for t in hd.split(',')[0].split() if t.strip('.,').isalpha()]
        if toks:
            sur = toks[-1].upper(); t = title_type(hd[len(hd.split(',')[0]):])
            if t != '?': wit_type[sur] = t
    # document-wide 'Mr./Ms./Dr. First Last, <title>' scan (catches witness lists + in-line intros; the
    # 'STATEMENT OF' anchor is absent in many hearings, so this is the primary business/advocate signal)
    for nm, title in re.findall(r'\b(?:Mr|Ms|Mrs|Dr)\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-zA-Z\'\-\.]+){1,2}),\s+([^,\n]{4,90})', txt):
        sur = nm.split()[-1].upper(); t = title_type(title)
        if t != '?' and sur not in wit_type: wit_type[sur] = t
    ms = list(SPEAKER.finditer(txt)); out = []; chair_ct = collections.Counter()
    for i, mt in enumerate(ms):
        pre, sur = mt.group(1), mt.group(2)
        is_member_hon = bool(MEMBER_RE.match(pre))
        is_chair = bool(re.match(r'Chair', pre, re.I))
        seg = txt[mt.end():ms[i + 1].start() if i + 1 < len(ms) else min(len(txt), mt.end() + 20000)]
        segc = re.sub(r'\s+', ' ', re.sub(r'\[\[Page [^\]]+\]\]', '', seg)).strip()
        dash = 1 if ENDDASH.search(seg.strip()) else 0
        surU = sur.upper(); surlast = surU.split()[-1]
        who = resolve(sur, chamber) if is_member_hon else None
        if who:
            if is_chair: chair_ct[who] += 1
            out.append([who, party_of(who), dash, segc[:TEXTCAP], 'C' if is_chair else 'M'])
        elif is_chair or surU.split()[0] in NONSPK:
            out.append(['C' if is_chair else 'O', '?', dash, ''])
        else:
            # witness: type from honorific, else STATEMENT OF title, else '?'
            t = hon_type(pre)
            if t == '?': t = wit_type.get(surlast, '?')
            out.append(['W', t, dash, ''])
    chair = chair_ct.most_common(1)[0][0] if chair_ct else None
    return out, chair

REPAIR_HON = (r'Mr|Mrs|Ms|Miss|Senator|Representative|Chairman|Chairwoman|Chair|Admiral|General|Secretary|'
              r'Colonel|Governor|Dr|Professor|Ambassador|Lieutenant|Captain|Major|Commander|Commissioner|'
              r'Administrator|Director|Justice|Judge|Honorable|Hon')
def repair_pdf(t):
    """119th (2025+) hearings are PDF-only: pypdf DEFAULT-mode splits the small-caps surname's drop-cap
    initial off its remainder ('Mr. C\\nROW.' = Mr. CROW; 'Admiral H OLSEY.' = Admiral HOLSEY). Rejoin
    those, and de-hyphenate words broken across line-wraps, so the HTML-era parse_seq works unchanged."""
    t = re.sub(r'\b(%s)\.[ ]+([A-Z])[ \t]*\n?[ \t]*([A-Z][A-Z\'\-]+)' % REPAIR_HON,
               lambda m: '%s. %s%s' % (m.group(1), m.group(2), m.group(3).title()), t)
    t = re.sub(r'([a-z])-\n([a-z])', r'\1\2', t)      # de-hyphenate line-wrapped words
    return t

def fetch_one_pdf(p):
    """2025+ path: api zip endpoint -> pdf/*.pdf -> pypdf -> repair -> same parse_seq."""
    pid, date = p['packageId'], p['dateIssued']
    try:
        raw = get('https://api.govinfo.gov/packages/%s/zip' % pid, True, api_key=KEY)
    except urllib.error.HTTPError as e:
        return {'pid': pid, 'status': 'nopage' if e.code in (404, 410) else 'err:%d' % e.code}
    except Exception as e:
        return {'pid': pid, 'status': 'err:%s' % str(e)[:40]}
    try:
        import pypdf
        z = zipfile.ZipFile(io.BytesIO(raw))
        pdfs = [n for n in z.namelist() if n.lower().endswith('.pdf')]
        if not pdfs:
            return {'pid': pid, 'status': 'nopdf'}
        r = pypdf.PdfReader(io.BytesIO(z.read(pdfs[0])))
        txt = '\n'.join(pg.extract_text() for pg in r.pages)
        seq, chair = parse_seq(repair_pdf(txt), chamber_of_package(pid))
        named = sum(1 for t in seq if t[0] not in ('W', 'C', 'O'))
        if named < 3:
            return {'pid': pid, 'status': 'err:pdf:%d-named' % named}   # bad extraction -> flag, don't cache junk
        return {'pid': pid, 'date': date, 'title': (p.get('title') or '')[:200], 'status': 'ok',
                'seq': seq, 'chair': chair, 'named': named}
    except Exception as e:
        return {'pid': pid, 'status': 'err:pdf:%s' % str(e)[:40]}

def fetch_one(p):
    pid, date = p['packageId'], p['dateIssued']
    try:
        raw = get('https://www.govinfo.gov/content/pkg/%s.zip' % pid, True)
    except urllib.error.HTTPError as e:
        return {'pid': pid, 'status': 'nopage' if e.code in (404, 410) else 'err:%d' % e.code}
    except Exception as e:
        return {'pid': pid, 'status': 'err:%s' % str(e)[:40]}
    try:
        z = zipfile.ZipFile(io.BytesIO(raw)); seq = []; chair = None; named = 0
        for n in z.namelist():
            if not n.endswith('.htm'): continue
            s, ch = parse_seq(z.read(n).decode('utf-8', 'replace'), chamber_of_package(pid))
            if ch and not chair: chair = ch
            named += sum(1 for t in s if t[0] not in ('W', 'C', 'O'))
            seq += s
        return {'pid': pid, 'date': date, 'title': (p.get('title') or '')[:200], 'status': 'ok',
                'seq': seq, 'chair': chair, 'named': named}
    except Exception as e:
        return {'pid': pid, 'status': 'err:parse:%s' % str(e)[:40]}

def seqdb(path=None):
    con = sqlite3.connect(path or SEQDB, timeout=120)
    con.execute("PRAGMA journal_mode=WAL"); con.execute("PRAGMA busy_timeout=120000")
    con.execute("CREATE TABLE IF NOT EXISTS hearing_seq(package TEXT PRIMARY KEY, date TEXT, seq TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS seq_log(package TEXT PRIMARY KEY, status TEXT)")
    return con

def maindb():
    con = sqlite3.connect(B + r'\social_media.db', timeout=120)
    con.execute("PRAGMA busy_timeout=120000")
    con.execute("CREATE TABLE IF NOT EXISTS hearing_meta(package TEXT PRIMARY KEY, date TEXT, title TEXT, "
                "chair_member TEXT, chair_party TEXT, n_named_turns INT, n_members INT)")
    return con

def list_packages(since):
    url = 'https://api.govinfo.gov/collections/CHRG/%sT00:00:00Z?offsetMark=*&pageSize=1000' % since
    out = []
    while url:
        j = json.loads(get(url, api_key=KEY)); out += j.get('packages', [])
        url = _strip_api_key(j.get('nextPage')) if j.get('nextPage') else None
        time.sleep(0.3)
    return out

def run(since, until, workers=6, db_path=None):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    sc = seqdb(db_path); mc = maindb()
    done = {r[0] for r in sc.execute("SELECT package FROM seq_log WHERE status IN ('ok','nopage')")}
    pkgs = [p for p in list_packages(since) if since <= p['dateIssued'] <= until]
    # EXPEDITE-THE-VERDICT (2026-07-10): the substance-shaped antagonism signal (personal hostility)
    # lives in CONTENTIOUS hearings; genial approps/noms contribute ~0. Front-load likely-contentious
    # titles so the signal-bearing data lands first (full collection still completes -- reorder, not skip).
    CONTENTIOUS = re.compile(r'oversight|judiciar|weaponi|border|afghan|censor|\bfbi\b|department of justice|'
                             r'big tech|social media|radical|accountab|failure|crisis|scandal|hunter|impeach|'
                             r'testimony of|examining|threat|extremis|antisemit|fentanyl|cartel|hamas|'
                             r'\bccp\b|homeland|whistleblow|misconduct|abuse|corrupt', re.I)
    def prio(p): return (0 if CONTENTIOUS.search(p.get('title') or '') else 1, p['dateIssued'])
    todo = [p for p in sorted(pkgs, key=prio) if p['packageId'] not in done]
    ncont = sum(1 for p in todo if CONTENTIOUS.search(p.get('title') or ''))
    print('  (%d likely-contentious hearings front-loaded)' % ncont, flush=True)
    print('SEQ-CACHE: %d hearings %s..%s, %d to cache (%d workers)' % (len(pkgs), since, until, len(todo), workers), flush=True)
    k = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(fetch_one_pdf if p['dateIssued'] >= '2025-01-01' else fetch_one, p) for p in todo]
        for fut in as_completed(futs):
            r = fut.result(); k += 1; pid = r['pid']
            if r['status'] != 'ok':
                sc.execute("INSERT OR REPLACE INTO seq_log VALUES(?,?)", (pid, r['status'])); sc.commit(); continue
            sc.execute("INSERT OR REPLACE INTO hearing_seq VALUES(?,?,?)", (pid, r['date'], json.dumps(r['seq'], separators=(',', ':'))))
            sc.execute("INSERT OR REPLACE INTO seq_log VALUES(?,?)", (pid, 'ok')); sc.commit()
            mc.execute("INSERT OR REPLACE INTO hearing_meta VALUES(?,?,?,?,?,?,?)",
                       (pid, r['date'], r['title'], r['chair'], party_of(r['chair']) if r['chair'] else '?', r['named'], 0)); mc.commit()
            if k % 100 == 0:
                print('  [%d/%d] %s cached' % (k, len(todo), r['date']), flush=True)
    # Census over the LISTED window, read back from seq_log rather than counted incrementally: a
    # package that failed on an earlier run is retried on this one (`done` holds only ok/nopage), so
    # the log -- not this run's increment -- is the authority on what the window contains. Anything
    # listed that carries no terminal status (an error status, or never reached because the run was
    # interrupted) counts as FAILED. That keeps listed == ok + nopage + failed and makes the
    # consumer's coverage check fail closed on a partial run instead of reading it as complete.
    log = dict(sc.execute("SELECT package, status FROM seq_log"))
    ok_n = sum(1 for p in pkgs if log.get(p['packageId']) == 'ok')
    nopage_n = sum(1 for p in pkgs if log.get(p['packageId']) == 'nopage')
    sc.close(); mc.close(); print('SEQ-CACHE done: %d processed' % k)
    return {'listed': len(pkgs), 'ok': ok_n, 'nopage': nopage_n,
            'failed': len(pkgs) - ok_n - nopage_n, 'processed': k}

def require_fresh_rebuild_target(db_path):
    """--rebuild must land on a path that does not exist yet.

    A rebuild is the ONLY way a corpus earns the corrected attribution contract, so the temptation
    is to point it at the live database. That would destroy the only copy of a 7,174-package corpus
    that took hours to collect, and the incremental skip list would make the "rebuild" a no-op
    anyway (every package is already ok/nopage). Refuse instead of dropping or truncating."""
    p = str(db_path or '')
    if not p:
        raise RebuildTargetError('--rebuild requires --db <new path>; no path was given')
    if os.path.exists(p):
        raise RebuildTargetError(
            '--rebuild refuses an existing database: %s. A rebuild must collect into a NEW path; '
            'nothing here will drop or truncate an existing corpus.' % p)
    return p

def max_seq_date(db_path):
    """Latest hearing date actually stored, or None. Read-only; never creates the database."""
    if not os.path.exists(str(db_path)):
        return None
    con = sqlite3.connect(str(db_path), timeout=120)
    try:
        con.execute('PRAGMA query_only=1')
        row = con.execute("SELECT max(date) FROM hearing_seq").fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return row[0] if row else None

def write_seq_receipt(db_path, *, since, until, listed, ok, nopage, failed, rebuild,
                      previous_receipt_path, now, pre_run_db_sha256=None):
    """Write ``<db_path>.receipt.json`` and return the receipt.

    ``attribution_contract`` is the whole point. It claims the corrected contract only when

      * ``rebuild`` is true -- every row in this database was resolved by the fixed resolver; or
      * this run CHAINS: a previous receipt exists, it already claimed the corrected contract, and
        its ``db_sha256`` matches the database as it stood BEFORE this run (``pre_run_db_sha256``;
        when that is not supplied the current hash is used, which is the no-new-packages case).

    Otherwise it declares ``unknown-pre-fix-corpus``. A first receipt over an inherited database, a
    database edited out of band between runs, and a swapped file all land there -- the chain breaks
    at the hash, and it cannot be repaired by re-running: only a fresh --rebuild restores the claim.
    """
    db_path = str(db_path)
    md = max_seq_date(db_path)
    db_sha = sha256_file(db_path)
    previous = None
    previous_sha = None
    if previous_receipt_path and os.path.isfile(str(previous_receipt_path)):
        previous_sha = sha256_file(str(previous_receipt_path))
        try:
            with open(str(previous_receipt_path), encoding='utf-8') as fh:
                loaded = json.load(fh)
            previous = loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError):
            previous = None
    baseline = db_sha if pre_run_db_sha256 is None else pre_run_db_sha256
    chained = bool(previous
                   and previous.get('attribution_contract') == ATTRIBUTION_CONTRACT
                   and previous.get('db_sha256') == baseline)
    receipt = {
        'schema': SEQ_RECEIPT_SCHEMA,
        'built_at': now.isoformat(),
        'since': since,
        'until': until,
        'packages': {'listed': int(listed), 'ok': int(ok),
                     'nopage': int(nopage), 'failed': int(failed)},
        'max_date': md,
        'db_sha256': db_sha,
        'rebuild': bool(rebuild),
        'attribution_contract': ATTRIBUTION_CONTRACT if (rebuild or chained) else UNKNOWN_ATTRIBUTION,
        'previous_receipt_sha256': previous_sha,
    }
    out = db_path + '.receipt.json'
    tmp = out + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    os.replace(tmp, out)
    return receipt

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='CHRG turn-sequence cache + corpus provenance receipt')
    ap.add_argument('--since', default='2019-01-01')
    ap.add_argument('--until', default='2024-12-31')
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--db', default=None, help='sequence database path (default: %s)' % SEQDB)
    ap.add_argument('--rebuild', action='store_true',
                    help='fresh collection into a NEW --db path; the only run that can claim '
                         'the corrected attribution contract')
    a = ap.parse_args(argv)
    db_path = a.db or SEQDB
    if a.rebuild:
        try:
            require_fresh_rebuild_target(a.db)
        except RebuildTargetError as exc:
            print('REBUILD REFUSED: %s' % exc, file=sys.stderr)
            return 2
    receipt_path = db_path + '.receipt.json'
    pre_sha = sha256_file(db_path) if os.path.exists(db_path) else None
    census = run(a.since, a.until, a.workers, db_path)
    receipt = write_seq_receipt(
        db_path, since=a.since, until=a.until, listed=census['listed'], ok=census['ok'],
        nopage=census['nopage'], failed=census['failed'], rebuild=a.rebuild,
        previous_receipt_path=receipt_path,
        now=datetime.datetime.now(datetime.timezone.utc), pre_run_db_sha256=pre_sha)
    print('SEQ-RECEIPT %s: %s | listed %d ok %d nopage %d failed %d | max_date %s'
          % (receipt_path, receipt['attribution_contract'], census['listed'], census['ok'],
             census['nopage'], census['failed'], receipt['max_date']))
    return 0

if __name__ == '__main__':
    sys.exit(main())
