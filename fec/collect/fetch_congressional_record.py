# -*- coding: utf-8 -*-
import os as _o,sys as _s;_s.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))));from paths import ROOT as MB_ROOT,DRAFT as MB_DRAFT  # centralized root (arch review 2026-07-02)
"""CONGRESSIONAL RECORD + HEARING TRANSCRIPT COLLECTOR (GovInfo) — the trait-corpus upgrade the
Ramey pipeline actually calls for: floor speeches (CREC) and committee hearing transcripts (CHRG),
attributed to members by the Record's speaker headers ("Mr. DURBIN.", "Ms. WARREN of Massachusetts.",
"Chairman HILL.").  Tweets stay in the corpus; this adds the papers' native medium.

Design:
  * Discovery via api.govinfo.gov collections listing (GOVINFO_KEY env; DEMO_KEY works but is
    rate-limited to ~30 req/hr — a free key from https://api.data.gov/signup lifts it to 1,000/hr.
    Discovery needs only a handful of listing calls; CONTENT downloads are keyless).
  * Each package downloads as ONE keyless zip (www.govinfo.gov/content/pkg/<id>.zip); pages are
    parsed locally and the zip is deleted — nothing bulky is kept.
  * CREC: only spoken floor pages (PgH/PgS); Extensions of Remarks (PgE, inserted written matter)
    and the Daily Digest (PgD) are excluded to stay faithful to "floor speech."
  * CHRG: hearing packages filtered to finance-relevant titles (banking/financial services/credit
    union/monetary/housing/securities) — member Q&A turns attribute the same way; witnesses don't
    match the roster and fall away naturally.
  * Attribution: surname (+ optional "of State") -> roster. Ambiguous surnames within a chamber
    require the state tag; unresolvable turns are DROPPED, never guessed.
  * Storage: `speeches` table in social_media.db (WAL), one row per member-turn; `fetch_log` rows
    keyed crec:<pkg>/chrg:<pkg> make the job resumable (rerun skips ingested packages).
Usage:
  python fec/collect/fetch_congressional_record.py crec --since 2025-07-01 [--until 2026-07-02]
  python fec/collect/fetch_congressional_record.py chrg --since 2025-01-01
  python fec/collect/fetch_congressional_record.py status"""
import sys,os,re,json,io,csv,zipfile,sqlite3,datetime,time,argparse,collections,html,unicodedata
sys.stdout.reconfigure(encoding='utf-8',errors='replace')
import urllib.request
import urllib.parse
import urllib.error
B=MB_ROOT
KEY=os.environ.get('GOVINFO_KEY') or (open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),'govinfo_key.txt'),encoding='utf-8-sig').read().strip()
     if os.path.exists(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),'govinfo_key.txt')) else 'DEMO_KEY')
DB=os.path.join(B,'social_media.db')
UA={'User-Agent':'Mozilla/5.0 (research collector)'}
def db():
    c=sqlite3.connect(DB,timeout=60); c.execute('PRAGMA journal_mode=WAL')
    c.execute('''CREATE TABLE IF NOT EXISTS speeches(member TEXT,date TEXT,source TEXT,chamber TEXT,
                 package TEXT,nwords INTEGER,text TEXT)''')
    c.execute('CREATE INDEX IF NOT EXISTS ix_sp_m ON speeches(member)')
    c.execute('''CREATE TABLE IF NOT EXISTS cr_fetch_log(key TEXT PRIMARY KEY,status TEXT,ts TEXT)''')
    return c
def _redact_url(url):
    parts=urllib.parse.urlsplit(url); query=urllib.parse.parse_qsl(parts.query,keep_blank_values=True)
    clean=urllib.parse.urlencode([(k,'REDACTED' if k.lower() in {'api_key','key','token'} else v) for k,v in query])
    return urllib.parse.urlunsplit((parts.scheme,parts.netloc,parts.path,clean,parts.fragment))
def _strip_api_key(url):
    parts=urllib.parse.urlsplit(url); query=urllib.parse.parse_qsl(parts.query,keep_blank_values=True)
    clean=urllib.parse.urlencode([(k,v) for k,v in query if k.lower() not in {'api_key','key','token'}])
    return urllib.parse.urlunsplit((parts.scheme,parts.netloc,parts.path,clean,parts.fragment))
def get(url,binary=False,tries=3,api_key=None):
    for i in range(tries):
        try:
            headers=dict(UA)
            if api_key: headers['X-Api-Key']=api_key
            with urllib.request.urlopen(urllib.request.Request(_strip_api_key(url),headers=headers),timeout=120) as r:
                d=r.read()
                if binary: return d
                charset=r.headers.get_content_charset() or 'utf-8'
                try: return d.decode(charset,'strict')
                except UnicodeDecodeError as exc:
                    raise ValueError('response is not valid %s: %s'%(charset,_redact_url(url))) from exc
        except Exception as e:
            # a 4xx other than 408/429 is a permanent answer (bad key, missing page): retrying only burns quota
            if i==tries-1 or (isinstance(e,urllib.error.HTTPError) and 400<=e.code<500 and e.code not in (408,429)):
                raise RuntimeError('request failed: '+_redact_url(url)) from e
            time.sleep(3*(i+1))

def _http_status_code(error):
    """Read typed transport status without undoing redacted request messages."""
    seen=set()
    while isinstance(error,BaseException) and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error,urllib.error.HTTPError):
            return error.code
        error=error.__cause__
    return None

def _completed_crec_keys(rows,today=None):
    """Recheck recent 404 dates so delayed official publication is not lost."""
    today=today or datetime.date.today()
    cutoff=(today-datetime.timedelta(days=14)).isoformat()
    return {key for key,status in rows
            if status=='ok' or (status=='nosession' and key.removeprefix('crec:CREC-')<cutoff)}

def _crec_package_absent(package):
    """Confirm a malformed ZIP response against the official package API.

    GovInfo redirects missing content ZIPs to an HTTP-200 HTML error page.
    That page proves no absence: only a fresh typed 404 from this package's
    summary endpoint may authorize ``nosession``. Existing packages, auth or
    quota errors and malformed summaries remain collection failures. The
    authenticated request uses the same redacted header transport as CHRG.
    """
    url='https://api.govinfo.gov/packages/'+package+'/summary'
    try:
        payload=json.loads(get(url,api_key=KEY,tries=1))
    except Exception as error:
        if _http_status_code(error)!=404:
            raise
        print('  official package-summary absence: %s HTTP 404 %s'%(package,url))
        return True
    if not isinstance(payload,dict) or payload.get('packageId')!=package:
        raise ValueError('official package summary identity mismatch: '+package)
    print('  official package-summary confirms existing package: '+package)
    return False
# ---------------- roster: SURNAME(+state) -> canonical member ----------------
C={m['member']:m for m in json.load(open(os.path.join(B,'data','engine','congestion.json')))}
state={}
try:
    for r in csv.DictReader(open(os.path.join(MB_DRAFT,'cu_press_stance_tone.csv'),encoding='utf-8')):
        if r['member'] in C and r.get('state'): state[r['member']]=r['state'].strip()
except FileNotFoundError: pass
SFIX={'jr','sr','ii','iii','iv'}
def _fold(s): return ''.join(c for c in unicodedata.normalize('NFKD',s) if not unicodedata.combining(c))   # NFKD + drop combining marks: accented and plain spellings of a surname meet
def surkeys(m):
    ts=[t for t in _fold(m).replace('.','').split() if t.lower().rstrip(',') not in SFIX]
    ks={ts[-1].upper()}
    if len(ts)>=3: ks.add((ts[-2]+' '+ts[-1]).upper())          # VAN ORDEN, DE LA CRUZ...
    if len(ts)>=4: ks.add((ts[-3]+' '+ts[-2]+' '+ts[-1]).upper())
    return {re.sub(r"[^A-Z\- ']",'',k) for k in ks}
ROSTER=collections.defaultdict(list)   # (surname_key, chamber) -> [current-roster member]
SERVICE=set()                          # (member, Congress, chamber), including prior chamber service
HIST=collections.defaultdict(set)      # (surname_key, Congress, chamber) -> {(person id, state)} for EVERY HSall member, not just the roster
_bio2member={r.get('bioguide'):m for m,r in C.items() if r.get('bioguide')}
try:
    for r in csv.DictReader(open(os.path.join(B,'votes','HSall_members.csv'),encoding='utf-8-sig')):
        member=_bio2member.get(r.get('bioguide_id')); chamber=(r.get('chamber') or '').lower()
        if chamber not in ('house','senate'): continue
        try: congress=int(float(r.get('congress') or ''))
        except ValueError: continue
        sn=re.sub(r"[^A-Z\- ']",'',_fold(r.get('bioname') or '').split(',')[0].upper()).strip()   # same normalisation as resolve()
        pid=r.get('bioguide_id') or r.get('icpsr')
        if sn and pid:
            for k in {sn,sn.split()[-1]}: HIST[(k,congress,chamber)].add((pid,(r.get('state_abbrev') or '').upper()))
        if member: SERVICE.add((member,congress,chamber))
except FileNotFoundError:
    pass
for m in C:
    chambers={ch for member,_congress,ch in SERVICE if member==m}
    if not chambers: chambers={(C[m].get('chamber') or '').lower()}
    for ch in chambers:
        for k in surkeys(m): ROSTER[(k,ch)].append(m)
# CREC says "of California"; the roster carries two-letter codes -> full-name map for disambiguation
ST2AB={'alabama':'AL','alaska':'AK','arizona':'AZ','arkansas':'AR','california':'CA','colorado':'CO',
'connecticut':'CT','delaware':'DE','florida':'FL','georgia':'GA','hawaii':'HI','idaho':'ID','illinois':'IL',
'indiana':'IN','iowa':'IA','kansas':'KS','kentucky':'KY','louisiana':'LA','maine':'ME','maryland':'MD',
'massachusetts':'MA','michigan':'MI','minnesota':'MN','mississippi':'MS','missouri':'MO','montana':'MT',
'nebraska':'NE','nevada':'NV','new hampshire':'NH','new jersey':'NJ','new mexico':'NM','new york':'NY',
'north carolina':'NC','north dakota':'ND','ohio':'OH','oklahoma':'OK','oregon':'OR','pennsylvania':'PA',
'rhode island':'RI','south carolina':'SC','south dakota':'SD','tennessee':'TN','texas':'TX','utah':'UT',
'vermont':'VT','virginia':'VA','washington':'WA','west virginia':'WV','wisconsin':'WI','wyoming':'WY',
'puerto rico':'PR','guam':'GU','american samoa':'AS','virgin islands':'VI','district of columbia':'DC',
'northern mariana islands':'MP'}
def resolve(sur,st,chamber,congress=None):
    sur=re.sub(r"[^A-Z\- ']",'',_fold(sur).upper()).strip()
    ab=ST2AB.get((st or '').lower(),st)
    # A joint hearing can contain members from both chambers.  Resolve across the combined candidate
    # set and require uniqueness; never restore the legacy Senate-first guess.
    chambers=[chamber] if chamber in ('house','senate') else ['house','senate']
    cand=[]
    for ch in chambers:
        for member in ROSTER.get((sur,ch),[]):
            if congress and SERVICE and (member,congress,ch) not in SERVICE: continue
            if member not in cand: cand.append(member)
    who=None
    if ab:
        hit=[m for m in cand if state.get(m,'').upper()==str(ab).upper()]
        if len(hit)==1: who=hit[0]
        elif hit: cand=hit
    if who is None and len(cand)==1: who=cand[0]
    # The roster alone cannot see a historical namesake (backfills): refuse when HSall shows >1 distinct
    # person with this surname in that Congress/chamber (after the state tag, if any).  Empty HIST never refuses.
    if who and congress and HIST:
        h={e for ch in chambers for e in HIST.get((sur,congress,ch),())}
        if ab: h={e for e in h if e[1]==str(ab).upper()}
        if len({e[0] for e in h})>1: return None
    return who
# ---------------- speaker-turn parser ----------------
TURN=re.compile(r'(?m)^ {0,6}(?:The )?(Mr|Mrs|Ms|Miss|Senator|Chairman|Chairwoman|Chair)\.?[ ]+'
                # Latin-1 letters (no multiplication/division signs) so "Mr. VELÁZQUEZ." and "Ms. BARRAGÁN." stay turns
                r"([A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'\-]+(?:[ \-][A-Za-zÀ-ÖØ-öø-ÿ'\-]+){0,2}?)"
                # A transcript speaker label ends in a period.  Accepting a comma caused direct
                # address ("Ms. Rainey, what do you think?") to become a false speaker turn.
                r'(?: of ([A-Z][a-z]+(?: [A-Z][a-z]+)?))?\. ')
NONSPK={'SPEAKER','PRESIDENT','PRESIDING','CLERK','ACTING','VICE','CHAIR','CHAIRMAN','SECRETARY'}
def upperish(s):
    letters=[c for c in s if c.isalpha()]
    return letters and sum(1 for c in letters if c.isupper())/len(letters)>=0.6
def congress_for_date(date):
    try:
        d=datetime.date.fromisoformat(str(date)[:10])
        congress=(d.year-1789)//2+1
        return congress-1 if d.year%2==1 and (d.month,d.day)<(1,3) else congress
    except (TypeError,ValueError): return None

WITNESS_HEAD=re.compile(r'(?im)^\s*(?:STATEMENT|TESTIMONY)\s+OF\s+([^,\n\r]{3,100}),')
PREPARED_FOLLOWS=re.compile(r'(?i)\[(?:the\s+)?prepared\s+statement\s+of\s+[^\]\r\n]{1,120}?\s+follows:?\]')
HEARING_END_HEAD=re.compile(
    r'(?im)^\s*(?:APPENDIX|QUESTIONS\s+SUBMITTED\s+BY\s+MEMBERS\s+POST\s+HEARING|'
    r'RESPONSES\s+TO\s+QUESTIONS\s+FOR\s+THE\s+RECORD|QUESTIONS\s+FOR\s+THE\s+RECORD|'
    r'POST[- ]HEARING\s+QUESTIONS)\s*:?[ \t]*$')

_PERSON_NOISE={'THE','HON','HONORABLE','DR','MR','MRS','MS','PROFESSOR','JR','SR','II','III','IV',
               'MG','BG','LTG','GEN','COL','LTC','USA','USAF','USN','USMC','RET'}

def _person_tokens(name):
    return [t.upper() for t in re.findall(r"[A-Za-z][A-Za-z'\-]+",_fold(name)) if t.upper() not in _PERSON_NOISE]

def _header_is_serving_member(head,chamber,congress):
    toks=_person_tokens(head)
    if len(toks)<2: return False
    first,surname=toks[0],toks[-1]
    chambers=[chamber] if chamber in ('house','senate') else ['house','senate']
    for ch in chambers:
        for member in ROSTER.get((surname,ch),[]):
            if congress and SERVICE and (member,congress,ch) not in SERVICE: continue
            mt=_person_tokens(member)
            if len(mt)>=2 and mt[-1]==surname and (mt[0]==first or mt[0][:1]==first[:1] and len(first)==1):
                return True
    return False

def witness_surnames(text,chamber=None,congress=None):
    """Conservative CHRG witness blocklist from standalone testimony headers.

    Anchoring the header matters: ``OPENING STATEMENT OF`` belongs to committee members, while a
    standalone ``STATEMENT OF NAME, TITLE`` identifies a witness.
    """
    out=set()
    bounds=hearing_qa_bounds(text)
    pre_q_and_a=text[:bounds[0]] if bounds else text
    for head in WITNESS_HEAD.findall(pre_q_and_a):
        toks=_person_tokens(head)
        # Members sometimes testify briefly before the outside panel (for example, bill sponsors).
        # The pre-Q&A span already removes that statement; do not block their later live questions.
        if toks and not _header_is_serving_member(head,chamber,congress): out.add(toks[-1])
    return out

def hearing_qa_bounds(text):
    """Return the main ordinary-questioning span, or ``None`` when it cannot be isolated.

    GovInfo hearing HTML contains a front matter/table of contents, opening statements, sequential
    witness statements, the live question period, and then a written appendix.  We start after the
    last witness's prepared-statement marker (falling back to the final witness header) and stop at
    the first appendix heading after that point.  Missing witness structure fails closed.
    """
    heads=list(WITNESS_HEAD.finditer(text))
    if not heads: return None
    last=heads[-1]
    follows=[m for m in PREPARED_FOLLOWS.finditer(text,last.start(),min(len(text),last.start()+40000))]
    start=follows[-1].end() if follows else last.end()
    ends=[m.start() for m in HEARING_END_HEAD.finditer(text,start)]
    end=min(ends) if ends else len(text)
    return (start,end) if end>start else None

def member_service_chamber(member,congress,fallback):
    if fallback in ('house','senate'): return fallback
    chambers=sorted({ch for m,c,ch in SERVICE if m==member and c==congress})
    return chambers[0] if len(chambers)==1 else ''

def _page_text(t): return html.unescape(re.sub(r'<[^>]+>',' ',t))

def parse_pages(texts,chamber_of,date,source,pkg,cur,congress=None):
    rows=0; agg=collections.Counter()
    for fname,txt in texts:
        ch=chamber_of(fname)
        txt=_page_text(txt)
        # CHRG-only: hearing witnesses can share a member's surname (Gov. Michael Barr before Rep. Andy
        # Barr) — harvest witness surnames from the STATEMENT OF/TESTIMONY OF headers and EXCLUDE those
        # surnames for this hearing entirely (honest drop beats mis-attribution).
        wit=witness_surnames(txt,ch,congress or congress_for_date(date)) if source=='CHRG' else set()
        if source=='CHRG':
            bounds=hearing_qa_bounds(txt)
            if not bounds: continue
            txt=txt[bounds[0]:bounds[1]]
        ms=list(TURN.finditer(txt))
        for i,mt in enumerate(ms):
            sur=mt.group(2)
            # CREC prints speakers in ALL CAPS; hearing prints use mixed case -> caps filter is CREC-only
            if source!='CHRG' and not upperish(sur): continue
            if sur.upper().split()[0] in NONSPK: continue
            if source=='CHRG' and _fold(sur).upper().split()[-1] in wit: continue
            who=resolve(sur,mt.group(3),ch,congress or congress_for_date(date))
            if not who: continue
            seg=txt[mt.end():ms[i+1].start() if i+1<len(ms) else min(len(txt),mt.end()+20000)]
            # presiding-officer/clerk interjections aren't the member's words — cut the segment there
            seg=re.split(r'The (?:PRESIDING OFFICER|SPEAKER|ACTING PRESIDENT|VICE PRESIDENT|CLERK|CHAIR)\b',seg)[0]
            seg=re.sub(r'\s+',' ',seg).strip()
            seg=re.sub(r'\[\[Page [^\]]+\]\]','',seg); seg=seg.replace('____________________',' ')
            if len(seg)<150: continue                     # procedural one-liners
            row_chamber=member_service_chamber(who,congress or congress_for_date(date),ch)
            cur.execute('INSERT INTO speeches VALUES(?,?,?,?,?,?,?)',
                        (who,date,source,row_chamber,pkg,len(seg.split()),seg))
            rows+=1; agg[who]+=len(seg.split())
    return rows,agg
# ---------------- CREC ----------------
def list_packages(coll,since):
    # offsetMark cursor pagination — plain offset paging 500s on large collections (CHRG)
    url='https://api.govinfo.gov/collections/%s/%sT00:00:00Z?offsetMark=*&pageSize=1000'%(coll,since)
    out=[]
    while url:
        j=json.loads(get(url,api_key=KEY)); out+=j.get('packages',[])
        url=_strip_api_key(j.get('nextPage')) if j.get('nextPage') else None
        time.sleep(0.3)
    return out
def run_crec(since,until):
    con=db(); cur=con.cursor()
    # CREC package ids are DATE-BASED (CREC-YYYY-MM-DD), so discovery needs NO API call at all: generate
    # every date in [since, until] and try the keyless content zip; non-session days 404 and are marked.
    # (The API collections listing filters by lastModified, not dateIssued — it fed us 1995 Records once,
    # and DEMO_KEY 429s under any real backfill. Keyless is both safer and unthrottled.)
    d0=datetime.date.fromisoformat(since); d1=datetime.date.fromisoformat(until)
    dates=[(d0+datetime.timedelta(days=i)).isoformat() for i in range((d1-d0).days+1)]
    done=_completed_crec_keys(cur.execute("SELECT key,status FROM cr_fetch_log WHERE status IN ('ok','nosession')"))
    # STORED-PACKAGE GUARD (integrity fix 2026-07-06: the 4-year backfill re-inserted 922 exact-dup
    # rows because a date whose rows were committed but whose cr_fetch_log 'ok' mark was lost — a kill
    # in the commit->mark window, or an overlap with the original ingest — got re-processed. A CREC
    # package is atomic per date, so if ANY row for it is already stored, don't re-insert; just mark done).
    have_pkg={p for (p,) in cur.execute("SELECT DISTINCT package FROM speeches WHERE source='CREC'")}
    todo=[d for d in dates if 'crec:CREC-'+d not in done]
    print('CREC: %d candidate dates %s..%s, %d to try'%(len(dates),since,until,len(todo)))
    _ingested=_errored=0
    for date in todo:
        pid='CREC-'+date
        if pid in have_pkg:   # already stored -> mark done, never duplicate
            cur.execute("INSERT OR REPLACE INTO cr_fetch_log VALUES(?,?,?)",('crec:'+pid,'ok',datetime.datetime.now().isoformat()))
            con.commit(); continue
        try:
            try:
                z=zipfile.ZipFile(io.BytesIO(get('https://www.govinfo.gov/content/pkg/%s.zip'%pid,binary=True,tries=1)))
            except Exception as e:
                absent=_http_status_code(e)==404
                if isinstance(e,zipfile.BadZipFile):
                    absent=_crec_package_absent(pid)
                if absent:
                    cur.execute("INSERT OR REPLACE INTO cr_fetch_log VALUES(?,?,?)",('crec:'+pid,'nosession',datetime.datetime.now().isoformat()))
                    con.commit(); continue
                raise
            texts=[]
            for n in z.namelist():
                b=os.path.basename(n)
                if b.endswith('.htm') and ('-PgH' in b or '-PgS' in b):
                    try: texts.append((b,z.read(n).decode('utf-8','strict')))
                    except UnicodeDecodeError: print('  !! %s %s: not valid UTF-8 - page skipped'%(pid,b)); continue   # never decode with replacement: it would score corrupted text
            cham=lambda f:'house' if '-PgH' in f else 'senate'
            rows,agg=parse_pages(texts,cham,date,'CREC',pid,cur)
            cur.execute("INSERT OR REPLACE INTO cr_fetch_log VALUES(?,?,?)",('crec:'+pid,'ok',datetime.datetime.now().isoformat()))
            con.commit()
            _ingested+=1
            print('  %s: %3d pages -> %4d member turns (%d members)'%(pid,len(texts),rows,len(agg)))
        except Exception as e:
            con.rollback()   # drop any speech rows this package inserted before failing, or the next run would treat the partial package as stored
            _errored+=1
            cur.execute("INSERT OR REPLACE INTO cr_fetch_log VALUES(?,?,?)",('crec:'+pid,'err:%s'%str(e)[:80],datetime.datetime.now().isoformat()))
            con.commit(); print('  !!',pid,str(e)[:100])
    # TOTAL-OUTAGE VERDICT ONLY.  Every per-date failure is still absorbed into cr_fetch_log and
    # retried next run, so no single transient failure -- or even a small run of them -- can block
    # publication.  A 404 or confirmed-absent package (via _http_status_code()/_crec_package_absent()
    # above) is not an error at all and never reaches _errored.  Requiring BOTH _ingested==0 and
    # _errored>=3 keeps a recess/holiday week (zero session days -> zero errors, zero ingests) and a
    # week with one or two transient 5xx's (which the retry-on-next-run design already tolerates)
    # from ever triggering exit 2: only a run in which nothing was ingested AND at least three
    # separate non-404 fetch attempts errored counts as the total outage this verdict exists to
    # catch.
    return not (_errored>=3 and not _ingested)
# ---------------- CHRG (hearings) ----------------
FINRX=re.compile(r'banking|financial (service|institution|stability)|credit union|monetary|housing|securities|consumer (financial|credit)|capital market|fintech|digital asset|insurance fund',re.I)
def chrg_scope(pid):
    m=re.search(r'CHRG-(\d+)([hsj])hrg',pid,re.I)
    if not m: raise ValueError('unrecognized CHRG package chamber: '+pid)
    return int(m.group(1)),{'h':'house','s':'senate','j':'joint'}[m.group(2).lower()]

def fetch_chrg_html(pid):
    """Fetch the original GovInfo HTML directly; the PDF-heavy package zip is unnecessary."""
    url='https://www.govinfo.gov/content/pkg/%s/html/%s.htm'%(pid,pid)
    return get(url)

def run_chrg(since,until):
    con=db(); cur=con.cursor()
    listed=list_packages('CHRG',since)
    # a listing row without a string packageId/dateIssued would KeyError the whole run -> skip it and say so
    wellformed=[p for p in listed if isinstance(p,dict) and isinstance(p.get('packageId'),str) and p['packageId'] and isinstance(p.get('dateIssued'),str) and p['dateIssued']]
    if len(wellformed)<len(listed): print('CHRG: %d malformed listing rows skipped'%(len(listed)-len(wellformed)))
    pkgs=[p for p in wellformed if since<=p['dateIssued']<=until and FINRX.search(p.get('title') or '')]
    done={r[0] for r in cur.execute("SELECT key FROM cr_fetch_log WHERE status IN ('ok','noqa')")}   # noqa is terminal: re-fetching cannot conjure a Q&A span
    have_pkg={p for (p,) in cur.execute("SELECT DISTINCT package FROM speeches WHERE source='CHRG'")}   # stored-package guard (2026-07-06)
    todo=[p for p in sorted(pkgs,key=lambda p:p['dateIssued']) if 'chrg:'+p['packageId'] not in done]
    print('CHRG: %d finance-relevant hearings since %s, %d to ingest'%(len(pkgs),since,len(todo)))
    _ingested=_errored=_noqa=0
    for p in todo:
        pid=p['packageId']; date=p['dateIssued']
        if pid in have_pkg:   # already stored -> mark done, never duplicate
            cur.execute("INSERT OR REPLACE INTO cr_fetch_log VALUES(?,?,?)",('chrg:'+pid,'ok',datetime.datetime.now().isoformat()))
            con.commit(); continue
        try:
            congress,hearing_chamber=chrg_scope(pid)
            texts=[(pid+'.htm',fetch_chrg_html(pid))]
            if hearing_qa_bounds(_page_text(texts[0][1])) is None:   # parse_pages would skip the page and yield 0 rows: do not call that 'ok'
                cur.execute("INSERT OR REPLACE INTO cr_fetch_log VALUES(?,?,?)",('chrg:'+pid,'noqa',datetime.datetime.now().isoformat()))
                con.commit(); _noqa+=1; print('  -- %s: no ordinary-questioning span (noqa)'%pid); continue
            rows,agg=parse_pages(texts,lambda f:hearing_chamber,date,'CHRG',pid,cur,congress=congress)
            cur.execute("INSERT OR REPLACE INTO cr_fetch_log VALUES(?,?,?)",('chrg:'+pid,'ok',datetime.datetime.now().isoformat()))
            con.commit()
            _ingested+=1
            print('  %s [%s]: %d member turns'%(pid,(p.get('title') or '')[:60],rows))
        except Exception as e:
            con.rollback()   # same as run_crec: no partial speech rows under an err mark
            _errored+=1
            cur.execute("INSERT OR REPLACE INTO cr_fetch_log VALUES(?,?,?)",('chrg:'+pid,'err:%s'%str(e)[:80],datetime.datetime.now().isoformat()))
            con.commit(); print('  !!',pid,str(e)[:100])
    # TOTAL-OUTAGE VERDICT ONLY.  Same rule as run_crec, for the same reason.  Unlike CREC, CHRG
    # package ids come from the collections API listing rather than being guessed by date, so a
    # bare 404 here would be a genuine anomaly rather than the routine "no session that day" case
    # the _http_status_code()/_crec_package_absent() check in run_crec exists for -- but the floor
    # stays the same regardless: requiring BOTH _ingested==0 and (_errored+_noqa)>=3 keeps a week with
    # zero finance-relevant hearings (zero attempts, zero errors, zero ingests) and a week with one or
    # two transient failures (retried next run) from ever triggering exit 2.  A package whose page has
    # no ordinary-questioning span ('noqa', terminal) counts with the errors: a run where nothing was
    # ingested and three or more packages yielded nothing usable is a parser/source failure, not quiet.
    return not ((_errored+_noqa)>=3 and not _ingested)
def status():
    con=db()
    for src in ('CREC','CHRG'):
        r=con.execute("SELECT COUNT(*),COUNT(DISTINCT member),COALESCE(SUM(nwords),0),MIN(date),MAX(date) FROM speeches WHERE source=?",(src,)).fetchone()
        print('%s: %d turns | %d members | %s words | %s..%s'%(src,r[0],r[1],f'{r[2]:,}',r[3],r[4]))
    t=con.execute("SELECT member,SUM(nwords) w FROM speeches GROUP BY member ORDER BY w DESC LIMIT 5").fetchall()
    print('top speakers:',', '.join('%s %s'%(m,f'{w:,}') for m,w in t))
def _keep_awake():
    """User directive 2026-07-06 ('put in checks so it doesn't fall asleep'): hold a system wake
    lock while a backfill runs; the OS clears it on process exit; display may still sleep."""
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        print('keep-awake: system wake lock held')
    except Exception as e:
        print('keep-awake unavailable (%s)' % str(e)[:60])

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('cmd',choices=['crec','chrg','status'])
    ap.add_argument('--since',default='2025-07-01'); ap.add_argument('--until',default=str(datetime.date.today()))
    a=ap.parse_args()
    if a.cmd=='crec':
        _keep_awake(); _complete=run_crec(a.since,a.until); status()
        if not _complete:
            print('CREC: total outage (zero ingested, >=3 non-404 errors) — reporting collection failure'); return 2
    elif a.cmd=='chrg':
        _keep_awake(); _complete=run_chrg(a.since,a.until); status()
        if not _complete:
            print('CHRG: total outage (zero ingested, >=3 errors or no-Q&A packages) — reporting collection failure'); return 2
    else: status()
    return 0

if __name__=='__main__': sys.exit(main())
