# -*- coding: utf-8 -*-
"""All-committee committee-hearing ANTAGONISM/DEFIANCE collector, v2 ROLE-SCOPE semantics.

User directive 2026-07-10 (verbatim): "When did I tell you that partisan interruptions can't be
counted, that going against the chair or the chair enforcing can't be counted? Those totally can
be counted toward agreeableness and emotional stability. * Go over hearings again, and even
hearings out of our domain if they contain the members"

Role-scope refinement, same user, 2026-07-10 (verbatim): "If it is the chair enforcing order, then
that does not count, if the chair relents that is a sign of agreeableness, if the chair blows up
at someone that counts. It only really doesn't count if it is within the normal scope of their
duties as chair/ranking member, however if their tone elevates or they act in what could be
considered a temperamental manner that counts. Rule of thumb: is this beyond what is required of
them"

Implementation of the rule of thumb ("is this beyond what is required of them"):
  - Turns taken AS CHAIR (the 'Chairman./Chairwoman./Chair.' header): routine order enforcement
    (GAVEL phrases) is the job -> tallied as chair_enforce CONTEXT, never as antagonism; the
    chair's interruptions/friction phrases are NOT counted while they are routine enforcement.
  - chair_blowup (+antagonism): enforcement delivered with temperament -- blow-up phrases
    (BLOWUP), an exclamation mark on an enforcement line, or >=3 gavel commands hammered into a
    single turn.
  - chair_relent (+agreeableness): the chair backs down after a conflict moment -- grants extra
    time / lets the member or witness finish (RELENT), within 6 turns of an enforcement,
    friction, or interruption event (or in the same breath as their own gavel).
  - Non-chair member turns keep the v1 agentic semantics unchanged: intr (interrupt GIVEN),
    xparty (cross-party subset), friction (procedural combat SPOKEN), defiance (speaking through
    the chair's gavel -- going against the chair counts, per the directive), yieldback
    (cooperative negative control).
  - RANKING MEMBERS: transcripts do not mark the ranking member, so their within-role carve-out
    (points of order / regular order in defense of the minority) CANNOT be applied at parse time.
    It is the BUILDER's contract: join committee-leadership rosters and apply the same role-scope
    rule to ranking-member rows before any trait claim. Chair role, majority/minority, and
    hearing contentiousness stay builder-residualized (meta columns stored for that purpose).

SCOPE: drops the finance-only title filter (fetch_congressional_record.FINRX) -- ALL committees.
STORAGE: dedicated tables only (hearing_antag2, hearing_meta2). Deliberately does NOT write member
turns into speeches -- dumping all-committee hearing text into the Big-Five text corpus would silently
alter the validated text measures; that is a separate, deliberate decision, not a side effect here.
V1 tables (hearing_antag, hearing_meta) and 'antag:' resume keys are SUPERSEDED by this role-scope
version and safe to drop once no v1 process is running:
  DROP TABLE hearing_antag; DROP TABLE hearing_meta;
  DELETE FROM cr_fetch_log WHERE key LIKE 'antag:%';
Resume: cr_fetch_log key 'antag2:<pid>'. Era split unchanged: 2019-2024 HTML parses clean; 2025+
(119th) is PDF-only stubs and stays DEFERRED to a PDF-parser follow-up.
"""
import sys, os, re, io, time, json, csv, zipfile, sqlite3, datetime, urllib.request, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
B = r'C:\Users\pgoff\OneDrive\Desktop\Melange Systems'
_KF = B + r'\fec\govinfo_key.txt'   # live tree; the key is gitignored so it is not in the worktree checkout
KEY = os.environ.get('GOVINFO_KEY') or (open(_KF).read().strip() if os.path.exists(_KF) else 'DEMO_KEY')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) research collector'}
PAUSE = 1.0

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

# ---- roster + party (inlined; avoids fetch_congressional_record import-time argparse) ----
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
def resolve(sur, chamber=None):
    sur = re.sub(r"[^A-Z\- ']", '', sur.upper()).strip()
    for ch in ([chamber] if chamber else ['senate', 'house']):
        cand = ROSTER.get((sur, ch), [])
        if len(cand) == 1: return cand[0]
    return None
PARTY = {}
for r in csv.DictReader(open(B + r'\fec\legislators.csv', encoding='utf-8')):
    if r.get('bioguide_id'): PARTY[r['bioguide_id']] = (r.get('party') or '')[:1].upper()
BIO = {m: C[m].get('bioguide') for m in C}
def party_of(m): return PARTY.get(BIO.get(m), '?')

TURN = re.compile(r'(?m)^ {0,6}(?:The )?(Mr|Mrs|Ms|Miss|Senator|Chairman|Chairwoman|Chair)\.?[ ]+'
                  r"([A-Z][A-Za-z'\-]+(?:[ \-][A-Za-z'\-]+){0,2}?)"
                  r'(?: of ([A-Z][a-z]+(?: [A-Z][a-z]+)?))?[.,] ')
NONSPK = {'SPEAKER', 'PRESIDENT', 'PRESIDING', 'CLERK', 'ACTING', 'VICE', 'CHAIR', 'CHAIRMAN', 'SECRETARY'}
FRICTION = re.compile(r'reclaim\w+ my time|regular order|will not yield|decline to yield|point of order|regain my time', re.I)
YIELDBACK = re.compile(r'yield back|i yield the balance', re.I)
# GAVEL = routine order enforcement (the chair's job -- within role, never antagonism by itself)
GAVEL = re.compile(r'time (has )?expired|will suspend|is not recognized|not in order|'
                   r'gentlem[ae][ns].{0,25}(suspend|expired|not recognized)|'
                   r'take (his|her|your) seat|(committee|hearing) will (come to|be in) order', re.I)
# BLOWUP = temperament beyond the role (user rule: "if their tone elevates or they act in what
# could be considered a temperamental manner that counts") -- first-cut lexical markers; refine in QA
BLOWUP = re.compile(r'shame on|a disgrace|how dare|out of line|will not tolerate|'
                    r'this is (outrageous|absurd|ridiculous)|you\s?(\'re| are) (done|finished)|be quiet', re.I)
# RELENT = the chair backing down after friction (user rule: "if the chair relents that is a sign
# of agreeableness") -- only counted near a conflict moment, so routine 'without objection' grants don't fire
RELENT = re.compile(r'additional (\d+ )?(second|minute)s?|extra (time|minute)|time is extended|'
                    r'may (proceed|continue|finish|answer|respond)|i(\'ll| will) (allow|permit)|'
                    r'take the time you need|finish your (thought|answer|statement|sentence)|'
                    r'without objection[^.]{0,40}(proceed|continue|respond|answer|additional)', re.I)
ENDDASH = re.compile(r'-{2,}\s*$')

def parse_sequence(txt, chamber=None):
    """chamber = the hearing's own chamber ('house' for hhrg / 'senate' for shrg packages):
    surname resolution is scoped to it (unique-in-chamber or DROP) so a House 'Mr. LEE' can no
    longer bind a Senator — drop-never-guess. A 'Senator X' header overrides the hint (senators
    do appear in joint/House settings and say so in the title)."""
    txt = re.sub(r'<[^>]+>', ' ', txt)
    wit = set()
    for w in re.findall(r'(?:STATEMENT|TESTIMONY) OF ([A-Z][A-Z .,\'\-]{3,60})', txt):
        toks = [t.strip('.,') for t in w.split() if t.strip('.,').isalpha()]
        if toks: wit.add(toks[-1].upper())
    ms = list(TURN.finditer(txt)); seq = []
    for i, mt in enumerate(ms):
        pre, sur = mt.group(1), mt.group(2)
        role = 'chair' if re.match(r'Chair', pre, re.I) else 'spk'
        seg = txt[mt.end():ms[i + 1].start() if i + 1 < len(ms) else min(len(txt), mt.end() + 20000)]
        segc = re.sub(r'\s+', ' ', re.sub(r'\[\[Page [^\]]+\]\]', '', seg)).strip()
        dash = bool(ENDDASH.search(seg.strip()))
        if sur.upper().split()[0] in NONSPK and role != 'chair':
            seq.append((None, 'other', '?', segc, dash)); continue
        if sur.upper().split()[-1] in wit:
            seq.append((None, 'witness', '?', segc, dash)); continue
        hint = 'senate' if pre.lower().startswith('senator') else chamber
        who = resolve(sur, hint)
        seq.append((who, role, party_of(who) if who else '?', segc, dash))
    return seq

def hearing_events(seq):
    """Per-member per-hearing counters under the role-scope rule (docstring). Chair turns:
    routine enforcement -> chair_enforce context only; temperamental -> chair_blowup counts;
    backing down after conflict -> chair_relent counts. Non-chair turns: v1 semantics."""
    ev = collections.defaultdict(lambda: collections.Counter())
    chair_turns = collections.Counter()
    last_conflict = None   # index of the most recent enforcement / friction / interruption turn
    for i, (who, role, pty, text, dash) in enumerate(seq):
        as_chair = (role == 'chair')
        enforce = as_chair and bool(GAVEL.search(text))
        blowup = as_chair and bool(BLOWUP.search(text) or ('!' in text and enforce)
                                   or len(GAVEL.findall(text)) >= 3)
        relent = as_chair and bool(RELENT.search(text)) and (
            enforce or (last_conflict is not None and i - last_conflict <= 6))
        if who is not None:
            ev[who]['turns'] += 1
            if as_chair:
                chair_turns[who] += 1
                ev[who]['chair_turns'] += 1
            if i > 0:
                pw, prole, ppty, ptext, pdash = seq[i - 1]
                # interrupt GIVEN -- but a chair cutting in as routine enforcement is the job
                if pdash and pw is not None and pw != who and not (enforce and not blowup):
                    ev[who]['intr'] += 1
                    if ppty in ('D', 'R') and pty in ('D', 'R') and ppty != pty: ev[who]['xparty'] += 1
                if prole == 'chair' and GAVEL.search(ptext) and not as_chair: ev[who]['defiance'] += 1
            # procedural-combat phrases from the chair ARE the job unless the turn is a blow-up
            if FRICTION.search(text) and not (as_chair and not blowup): ev[who]['friction'] += 1
            if YIELDBACK.search(text): ev[who]['yieldback'] += 1
            if enforce and not blowup: ev[who]['chair_enforce'] += 1
            if blowup: ev[who]['chair_blowup'] += 1
            if relent: ev[who]['chair_relent'] += 1
        if dash or enforce or blowup or FRICTION.search(text): last_conflict = i
    chair = chair_turns.most_common(1)[0][0] if chair_turns else None
    return ev, chair

def db():
    con = sqlite3.connect(B + r'\social_media.db', timeout=60)
    con.execute("CREATE TABLE IF NOT EXISTS hearing_antag2(package TEXT, date TEXT, member TEXT, "
                "turns INT, chair_turns INT, intr INT, xparty INT, friction INT, defiance INT, yieldback INT, "
                "chair_enforce INT, chair_relent INT, chair_blowup INT)")
    con.execute("CREATE TABLE IF NOT EXISTS hearing_meta2(package TEXT PRIMARY KEY, date TEXT, title TEXT, "
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

def commit_retry(con, tries=8):
    """social_media.db is shared with other live collectors -- a >60s writer lock must back off,
    not kill an hours-long run."""
    for t in range(tries):
        try:
            con.commit(); return
        except sqlite3.OperationalError:
            if t == tries - 1: raise
            time.sleep(20 * (t + 1))

def run(since, until):
    con = db(); cur = con.cursor()
    done = {r[0] for r in cur.execute("SELECT key FROM cr_fetch_log WHERE status IN ('ok','nopage')")}
    pkgs = [p for p in list_packages(since) if since <= p['dateIssued'] <= until]
    todo = [p for p in sorted(pkgs, key=lambda p: p['dateIssued']) if 'antag2:' + p['packageId'] not in done]
    print('HEARINGS(antag2): %d all-committee hearings %s..%s, %d to ingest' % (len(pkgs), since, until, len(todo)), flush=True)
    tot_ev = 0
    for k, p in enumerate(todo):
        pid, date = p['packageId'], p['dateIssued']
        try:
            try:
                z = zipfile.ZipFile(io.BytesIO(get('https://www.govinfo.gov/content/pkg/%s.zip' % pid, True)))
                time.sleep(PAUSE)
            except urllib.error.HTTPError as e:
                if e.code in (404, 410):
                    cur.execute("INSERT OR REPLACE INTO cr_fetch_log VALUES(?,?,?)", ('antag2:' + pid, 'nopage', datetime.datetime.now().isoformat())); commit_retry(con)
                continue
            except Exception as e:
                cur.execute("INSERT OR REPLACE INTO cr_fetch_log VALUES(?,?,?)", ('antag2:' + pid, 'err:%s' % str(e)[:60], datetime.datetime.now().isoformat())); commit_retry(con)
                continue
            agg = collections.defaultdict(lambda: collections.Counter()); chair = None; named = 0
            pl = pid.lower()
            chamber = 'house' if 'hhrg' in pl else ('senate' if 'shrg' in pl else None)
            for n in z.namelist():
                if not n.endswith('.htm'): continue
                seq = parse_sequence(z.read(n).decode('utf-8', 'replace'), chamber)
                ev, ch = hearing_events(seq)
                if ch and not chair: chair = ch
                for m, c in ev.items():
                    for kk, vv in c.items(): agg[m][kk] += vv
                named += sum(1 for s in seq if s[0])
            for m, c in agg.items():
                cur.execute("INSERT INTO hearing_antag2 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (pid, date, m, c['turns'], c['chair_turns'], c['intr'], c['xparty'], c['friction'],
                             c['defiance'], c['yieldback'], c['chair_enforce'], c['chair_relent'], c['chair_blowup']))
                tot_ev += c['intr'] + c['friction'] + c['defiance'] + c['chair_blowup']
            cur.execute("INSERT OR REPLACE INTO hearing_meta2 VALUES(?,?,?,?,?,?,?)",
                        (pid, date, (p.get('title') or '')[:200], chair, party_of(chair) if chair else '?', named, len(agg)))
            cur.execute("INSERT OR REPLACE INTO cr_fetch_log VALUES(?,?,?)", ('antag2:' + pid, 'ok', datetime.datetime.now().isoformat()))
            commit_retry(con)
        except Exception as e:
            # ANY per-package failure (bad zip member, transient sqlite lock past retries, parse
            # surprise) is logged and skipped -- a multi-hour walk must never die on one package
            print('  !! %s: %s: %s' % (pid, type(e).__name__, str(e)[:120]), flush=True)
            try:
                cur.execute("INSERT OR REPLACE INTO cr_fetch_log VALUES(?,?,?)", ('antag2:' + pid, 'err:%s' % str(e)[:60], datetime.datetime.now().isoformat())); commit_retry(con)
            except Exception:
                pass
            continue
        if k % 50 == 0 or len(agg) > 0:
            print('  [%d/%d] %s %s: %d members, %d antag-events (running %d)' % (k + 1, len(todo), date, pid[:20], len(agg), sum(c['intr'] + c['friction'] + c['defiance'] + c['chair_blowup'] for c in agg.values()), tot_ev), flush=True)
    con.close(); print('HEARINGS(antag2) done: %d antagonism events across hearings' % tot_ev)

if __name__ == '__main__':
    since = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == '--since'), '2023-01-01')
    until = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == '--until'), datetime.date.today().isoformat())
    run(since, until)
