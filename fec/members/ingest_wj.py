# -*- coding: utf-8 -*-
"""Ingest USER-DOWNLOADED C-SPAN Washington Journal transcripts (2026-07-11). C-SPAN robots.txt blocks
ClaudeBot/anthropic-ai/Claude-Web with Disallow:/ -- so CLAUDE never fetches C-SPAN. Paul downloads each
WJ episode transcript himself (public content, the block is on AI crawlers not on him; each WJ page has a
'Download Transcript' button, or Save-Page-As HTML), drops the files in a folder, and THIS script parses
them offline -- no C-SPAN access from here.

WJ interviews are the ideal reactivity source: 30-45 min call-in format, member is the RESPONDENT
(disposition-eliciting), speaker-labeled HOST/GUEST/CALLER, and it books the interview-poor near-miss
members. GUEST turns = the member -> stored as source='INTERVIEW', chamber='cspan-wj'.

USAGE: put files in  <root>/wj_transcripts/  named with the member (e.g. 'randy-fine_2026-03-14.txt' or
'.html'); optional manifest.csv (file,member,date) overrides. Then:  python fec/members/ingest_wj.py
Files may be (a) the 'Download Transcript' text ('00:00:16 GUEST text...') or (b) saved episode HTML
(section.transcript with speaker-name / clip-script divs). Both handled. Resume: iv_fetch_log 'wj:<file>'.
"""
import os, re, sys, csv, glob, html as _html, sqlite3, datetime, json, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import ROOT
B = ROOT
WJ_DIR = os.path.join(B, 'wj_transcripts')

# ---- roster resolve (inlined; member from filename slug or manifest) ----
C = {m['member']: m for m in json.load(open(B + r'\data\engine\congestion.json'))}
def norm(s):
    import unicodedata
    s = unicodedata.normalize('NFD', s)
    return re.sub(r'[^a-z ]', ' ', ''.join(c for c in s if not unicodedata.combining(c)).lower()).strip()
SUR = collections.defaultdict(list); FULL = {}
for m in C:
    p = norm(m).split()
    if p: SUR[p[-1]].append(m); FULL[(p[0], p[-1])] = m
NICK = {'greg': 'gregory', 'bob': 'robert', 'mike': 'michael', 'jim': 'james', 'chris': 'christopher',
        'dan': 'daniel', 'tom': 'thomas', 'joe': 'joseph', 'tim': 'timothy', 'ben': 'benjamin', 'sam': 'samuel'}
def resolve_member(hint):
    """hint = filename slug or 'First Last' -> roster member (unique surname or first+last)."""
    toks = norm(hint.replace('-', ' ').replace('_', ' ')).split()
    toks = [t for t in toks if not t.isdigit() and len(t) > 1]
    if not toks: return None
    for i in range(len(toks)):
        for j in range(i + 1, len(toks) + 1):
            first = toks[i]; last = toks[j - 1]
            if (first, last) in FULL: return FULL[(first, last)]
            if (NICK.get(first, first), last) in FULL: return FULL[(NICK.get(first, first), last)]
    for t in toks:                                    # unique surname fallback
        if len(SUR.get(t, [])) == 1: return SUR[t][0]
    return None

def parse_text(txt):
    """Download-Transcript text: lines like '00:00:31 GUEST I THINK...' -> [(role, text)] merged by role run."""
    turns = []
    for role, body in re.findall(r'(?:\d{1,2}:\d{2}:\d{2}\s+)?\b(HOST|GUEST|CALLER|CO-HOST)\b[:\s]+(.*?)(?=(?:\d{1,2}:\d{2}:\d{2}\s+)?\b(?:HOST|GUEST|CALLER|CO-HOST)\b[:\s]|$)', txt, re.S):
        turns.append((role.upper(), re.sub(r'\s+', ' ', body).strip()))
    return turns

def parse_html(h):
    """Saved episode HTML: section.transcript -> transcript-clip-container(speaker-name, clip-script)."""
    guest = None
    gm = re.search(r'video-page-people-list.*?/person/\?\d+[^>]*>\s*([^<]+?)\s*<', h, re.S)
    if gm: guest = gm.group(1).strip()
    turns = []
    for spk, script in re.findall(r'speaker-name["\']?\s*>\s*(.*?)\s*<.*?clip-script["\']?\s*>\s*(.*?)\s*</div>', h, re.S):
        spk = re.sub(r'<[^>]+>', '', spk).strip(); body = _html.unescape(re.sub(r'<[^>]+>', ' ', script)).strip()
        role = 'GUEST' if re.search(r'guest', spk, re.I) else 'HOST' if re.search(r'host', spk, re.I) else 'CALLER' if re.search(r'caller', spk, re.I) else spk
        turns.append((role, re.sub(r'\s+', ' ', body)))
    return turns, guest

def db():
    con = sqlite3.connect(B + r'\social_media.db', timeout=60)
    return con

def run():
    if not os.path.isdir(WJ_DIR):
        os.makedirs(WJ_DIR, exist_ok=True)
        print('Created %s -- put C-SPAN WJ transcript files there (named with the member), then re-run.' % WJ_DIR); return
    man = {}
    mp = os.path.join(WJ_DIR, 'manifest.csv')
    if os.path.exists(mp):
        for r in csv.DictReader(open(mp, encoding='utf-8')):
            if r.get('file'): man[r['file'].strip()] = (r.get('member', '').strip(), (r.get('date', '') or '').strip())
    con = db(); done = {k for (k,) in con.execute("SELECT key FROM iv_fetch_log WHERE status='ok'")}
    files = [f for f in glob.glob(os.path.join(WJ_DIR, '*')) if f.lower().endswith(('.txt', '.html', '.htm'))]
    print('WJ ingest: %d files in %s' % (len(files), WJ_DIR))
    tot = 0
    for fp in sorted(files):
        fn = os.path.basename(fp); key = 'wj:' + fn
        if key in done: continue
        raw = open(fp, encoding='utf-8', errors='replace').read()
        mem_hint, date = man.get(fn, ('', ''))
        if fp.lower().endswith(('.html', '.htm')):
            turns, guest = parse_html(raw)
            if not mem_hint and guest: mem_hint = guest
            dm = re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', raw) or re.search(r'(\d{4}-\d{2}-\d{2})', fn)
            if not date and dm: date = dm.group(1)
        else:
            turns = parse_text(raw)
        if not mem_hint: mem_hint = fn
        member = resolve_member(mem_hint)
        if not date:
            dm = re.search(r'(\d{4})[-_]?(\d{2})[-_]?(\d{2})', fn); date = '%s-%s-%s' % dm.groups() if dm else ''
        if not member:
            print('  ?? %-40s could not resolve member from "%s" -- add to manifest.csv' % (fn[:40], mem_hint[:30])); continue
        if not date:
            print('  ?? %-40s no date in manifest or filename -- add date to manifest.csv' % fn[:40]); continue
        rows = [(member, date, 'INTERVIEW', 'cspan-wj', fn, len(t.split()), t) for role, t in turns if role == 'GUEST' and len(t.split()) >= 5]
        # dedup within file
        seen = set(); ins = 0
        for row in rows:
            if row[6] in seen: continue
            seen.add(row[6]); con.execute('INSERT INTO speeches VALUES (?,?,?,?,?,?,?)', row); ins += 1
        if not ins:
            con.commit(); print('  ?? %-40s 0 GUEST turns -- not logged, will retry; fix manifest.csv/file' % fn[:40]); continue
        con.execute('INSERT OR REPLACE INTO iv_fetch_log VALUES (?,?,?)', (key, 'ok', datetime.datetime.now().isoformat()))
        con.commit(); tot += ins
        print('  %-42s -> %-22s %s: %d GUEST turns' % (fn[:42], member, date, ins))
    con.close(); print('WJ ingest done: %d member turns added' % tot)

if __name__ == '__main__':
    run()
