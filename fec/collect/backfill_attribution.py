# -*- coding: utf-8 -*-
"""RE-ATTRIBUTION BACKFILL for the CREC / CHRG collector (fetch_congressional_record.py).

Why: the collector marks a package 'ok' once and never looks again, so every turn its resolver dropped at ingest time stays
dropped even after the resolver improves.  Audit 2026-10-20: 24 roster members had zero stored turns (accent-folded roster keys,
no state for 150 members, CREC's "Mr. AUSTIN SCOTT of Georgia" given-name labels).  The raw speaker label is not stored, so the
only way to recover a turn is to re-parse the package.

What: for each package, parse it with the CURRENT resolver and
  * INSERT the turns the table does not already cover (same member and one text contains the other, or the same opening 120 chars);
  * RE-LABEL a stored row only when the current parse gives that text (containment) to a SAME-SURNAME colleague who has no row
    for it yet (the old resolver credited "Mr. GARCIA of Illinois" to Sylvia Garcia): UPDATE member, nothing else;
  * never DELETE.  Every other stored row the current parse does not reproduce (another surname now owns it, or the resolver now
    refuses it, e.g. the Al Green / Gene Green ambiguity) goes to the ledger CSV for Paul -- removing those is his call.
Every UPDATE is in the ledger (rowid, old member, new member), so it can be reversed.
Idempotent: a second run changes nothing.  Dry run (database opened read-only) unless --apply.
BACK UP social_media.db FIRST (sqlite3 backup API) and do not --apply while another collector is writing to it.

Floor-page / hearing text is cached under --cache so a resolver change can be re-run without re-downloading:
  CREC-<date>.json.gz   [[page name, html], ...] for the PgH/PgS pages
  CHRG-<pid>.json.gz    [[pid.htm, html]]
An 'err:*' CREC date is probed with a keyless Range request first (GovInfo answers a missing package with an HTML page).
Usage:
  python fec/collect/backfill_attribution.py crec --cache DIR --out DIR [--since D] [--until D] [--apply]
  python fec/collect/backfill_attribution.py chrg --cache DIR --out DIR [--since D] [--until D] [--apply]
  python fec/collect/backfill_attribution.py counts --out FILE.csv [--plan DIR]   # per-member CREC/CHRG counts for the 533 ledger members
      (read-only; --plan adds the planned_crec/chrg.csv a dry run wrote, i.e. the table as it will read after --apply)
  python fec/collect/backfill_attribution.py quarantine --cache DIR --out DIR [--sources CREC,CHRG] [--since D] [--until D] [--apply]
      stored rows the current resolver no longer reproduces -> speeches_quarantine (same rowid, every column, a reason code), then each
      row's original speaker label is re-resolved against ALL of HSall: unique roster member -> restored in place; unique former member
      -> parked with proposed_member; several / nobody / no label -> stays.  Dry run unless --apply; ledger CSV either way.
  python fec/collect/backfill_attribution.py undo --batch Q... [--apply]            # reverse one quarantine batch exactly (dry run unless --apply)
  python fec/collect/backfill_attribution.py refine --cache DIR --out DIR [--apply]  # re-read the quarantined rows with exact, unique evidence
      (hearing front-matter member list, same-day recognition line, comma label, elimination) -> refine_ledger.csv; dry run unless --apply.
      --apply puts a restorable row back in speeches under its ORIGINAL rowid and re-parks a row for the former member the evidence names; every change
      is logged before/after in speeches_quarantine_log under a batch id, and `undo --refine-batch R... --apply` reverses it exactly.
      evidence_kind=hearing_roster marks the CHRG case whose risk is a member who waived on to the hearing but is not on the committee list.
"""
import sys, os, io, re, csv, json, gzip, time, zipfile, sqlite3, datetime, argparse, collections, hashlib
import urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from paths import ROOT
from collect import fetch_congressional_record as CR
DB = os.path.join(ROOT, 'social_media.db')
HEAD = 120   # opening characters that identify "the same speech" when neither segmentation is a substring of the other


def now(): return datetime.datetime.now().isoformat(timespec='seconds')


def cached(cache, name, fetch):
    """The cached [(name, html)] list, else fetch() it (None = package absent) and cache it."""
    p = os.path.join(cache, name + '.json.gz')
    if os.path.exists(p):
        with gzip.open(p, 'rt', encoding='utf-8') as f: return [tuple(x) for x in json.load(f)]
    pages = fetch()
    if pages is None: return None
    os.makedirs(cache, exist_ok=True)
    with gzip.open(p + '.tmp', 'wt', encoding='utf-8') as f: json.dump(pages, f)
    os.replace(p + '.tmp', p)
    return pages


def crec_pages(date):
    url = 'https://www.govinfo.gov/content/pkg/CREC-%s.zip' % date
    for attempt in range(3):   # a transient SSL EOF on the probe must not read as "absent" or abort the date
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={**CR.UA, 'Range': 'bytes=0-3'}), timeout=60) as r:
                head = r.read(4)
            break
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
            if attempt == 2: raise
        except urllib.error.URLError:
            if attempt == 2: raise
        time.sleep(3 * (attempt + 1))
    if head[:2] != b'PK': return None   # a missing package comes back as an HTML page, not a zip
    z = zipfile.ZipFile(io.BytesIO(CR.get(url, binary=True, tries=3)))
    return [(os.path.basename(n), z.read(n).decode('utf-8', 'strict')) for n in z.namelist()
            if os.path.basename(n).endswith('.htm') and ('-PgH' in os.path.basename(n) or '-PgS' in os.path.basename(n))]


class Collect:
    """Stands in for the sqlite cursor parse_pages writes to: keeps the rows, touches no database."""
    def __init__(self): self.rows = []
    def execute(self, sql, args): self.rows.append(args)


def contains(a, b): return a == b or a in b or b in a
def covers(old_text, new_text): return contains(old_text, new_text) or old_text[:HEAD] == new_text[:HEAD]
def surname(m): return CR._fold(m).split()[-1].upper()


def reconcile(new_rows, old_rows):
    """new_rows: tuples from parse_pages (member,date,source,chamber,package,nwords,text); old_rows: (rowid,member,text).
    -> (to_insert, [(stored row, owner now)] re-owned, [(stored row, None)] not reproduced, [(rowid, old member, new member)] same-surname re-labels)."""
    by_member = collections.defaultdict(list)
    for o in old_rows: by_member[o[1]].append(o)
    to_insert, seen = [], set()
    for r in new_rows:   # one row per exact (member, package, text): the audit keeps the corpus exact-duplicate free, and a repeated boilerplate line ("5 legislative days to revise and extend") would otherwise breach it
        if (r[0], r[6]) not in seen and not any(covers(o[2], r[6]) for o in by_member.get(r[0], ())): to_insert.append(r)
        seen.add((r[0], r[6]))
    reowned, unreproduced, relabel = [], [], []
    for o in old_rows:
        if any(r[0] == o[1] and covers(o[2], r[6]) for r in new_rows): continue
        owner = next((r[0] for r in new_rows if contains(o[2], r[6])), None)
        (reowned if owner else unreproduced).append((o, owner))
        whole = next((r[0] for r in new_rows if o[2] in r[6]), None)   # relabel only when the stored row lies wholly inside ONE new turn: a stored blob that merely CONTAINS a turn has other speakers' words in it
        if whole and surname(whole) == surname(o[1]) and not any(contains(x[2], o[2]) for x in by_member.get(whole, ())):
            relabel.append((o[0], o[1], whole))
            to_insert = [r for r in to_insert if not (r[0] == whole and contains(o[2], r[6]))]   # the re-labelled row IS that turn: inserting it too would duplicate it
    return to_insert, reowned, unreproduced, relabel


def work_list(a, src, status):
    """[(date, package id)] a run covers: every logged CREC date that was ok/err, or the finance-titled CHRG packages GovInfo lists."""
    if src == 'CREC':
        return [(d, 'CREC-' + d) for d in sorted(k[len('crec:CREC-'):] for k, s in status.items()
                if k.startswith('crec:CREC-') and (s == 'ok' or s.startswith('err:'))) if a.since <= d <= a.until]
    listed = [p for p in CR.list_packages('CHRG', a.since) if isinstance(p, dict) and isinstance(p.get('packageId'), str)
              and isinstance(p.get('dateIssued'), str) and a.since <= p['dateIssued'] <= a.until and CR.FINRX.search(p.get('title') or '')]
    return [(p['dateIssued'], p['packageId']) for p in sorted(listed, key=lambda p: p['dateIssued'])]


def guard_roster():
    """Refuse to write when the roster names are mojibake (congestion.json is raw UTF-8; a cp1252 read turns Velazquez into VelÃ¡zquez and every such member would mis-resolve)."""
    bad = sorted(m for m in CR.C if 'Ã' in m or 'Â' in m)
    if bad: raise SystemExit('roster names look like mojibake (%d, e.g. %r): congestion.json was read with the wrong encoding; not writing' % (len(bad), bad[0]))


def apply_reconciled(con, to_insert, relabel, batch):
    """Insert the missing turns and re-label the same-surname swaps (the caller commits).  -> ledger rows
    (batch, kind, rowid, member, date, package, was_member, text sha1) so undo_applied() can reverse exactly what this wrote."""
    out = []
    for r in to_insert:
        rid = con.execute('INSERT INTO speeches VALUES(?,?,?,?,?,?,?)', r).lastrowid
        out.append((batch, 'insert', rid, r[0], r[1], r[4], '', hashlib.sha1(r[6].encode('utf-8')).hexdigest()))
    for rid, was, new in relabel:
        con.execute('UPDATE speeches SET member=? WHERE rowid=? AND member=?', (new, rid, was))
        out.append((batch, 'relabel', rid, new, '', '', was, ''))
    return out


def undo_applied(con, rows):
    """Reverse apply_reconciled ledger rows: remove an inserted row only if it is still exactly what was inserted; put a re-labelled row back.  Idempotent."""
    n = collections.Counter()
    for _, kind, rid, member, _, _, was, sha in rows:
        if kind == 'insert':
            cur = con.execute('SELECT member,text FROM speeches WHERE rowid=?', (rid,)).fetchone()
            if cur and cur[0] == member and hashlib.sha1(cur[1].encode('utf-8')).hexdigest() == sha:
                con.execute('DELETE FROM speeches WHERE rowid=?', (rid,)); n['deleted'] += 1
            else: n['skipped'] += 1
        else:
            if con.execute('UPDATE speeches SET member=? WHERE rowid=? AND member=?', (was, rid, member)).rowcount: n['unrelabelled'] += 1
            else: n['skipped'] += 1
    return {'deleted': n['deleted'], 'unrelabelled': n['unrelabelled'], 'skipped': n['skipped']}


def run(a):
    if a.apply: guard_roster()
    os.makedirs(a.out, exist_ok=True)
    # a dry run opens the database read-only: it can neither write nor trigger a WAL checkpoint under a live collector
    con = sqlite3.connect(('file:%s?mode=rw' if a.apply else 'file:%s?mode=ro') % DB.replace('\\', '/'), uri=True, timeout=60)
    con.execute('PRAGMA busy_timeout=60000')
    src = a.cmd.upper(); prefix = a.cmd + ':'
    print('%s loading stored %s rows ...' % (now(), src), flush=True)
    old = collections.defaultdict(list)
    for rowid, member, pkg, text in con.execute('SELECT rowid,member,package,text FROM speeches WHERE source=?', (src,)):
        old[pkg].append((rowid, member, text))
    print('%s stored: %d packages' % (now(), len(old)), flush=True)
    status = dict(con.execute('SELECT key,status FROM cr_fetch_log'))
    work = work_list(a, src, status)
    tot = collections.Counter(); gained = collections.Counter(); lost = collections.Counter(); ledger = []; planned = []
    summ = open(os.path.join(a.out, 'summary_%s.jsonl' % a.cmd), 'a', encoding='utf-8')
    batch = 'I' + datetime.datetime.now().strftime('%Y%m%dT%H%M%S')
    if a.apply:   # reversible: every row this run writes is in applied_<cmd>.csv BEFORE it is committed (`undo --ledger` reverses it)
        lf = open(os.path.join(a.out, 'applied_%s.csv' % a.cmd), 'w', newline='', encoding='utf-8-sig'); lw = csv.writer(lf)
        lw.writerow(['batch', 'kind', 'rowid', 'member', 'date', 'package', 'was_member', 'text_sha1'])
    for n, (date, pid) in enumerate(work, 1):
        rec = {'ts': now(), 'package': pid, 'date': date, 'log': status.get(prefix + pid, 'unlogged')[:24]}
        try:
            if src == 'CREC':
                pages = cached(a.cache, pid, lambda: crec_pages(date)); cham = lambda f: 'house' if '-PgH' in f else 'senate'; congress = None
            else:
                congress, hearing_chamber = CR.chrg_scope(pid); cham = lambda f: hearing_chamber
                pages = cached(a.cache, pid, lambda: [(pid + '.htm', CR.fetch_chrg_html(pid))])
            if pages is None:
                rec['result'] = 'absent'
            else:
                c = Collect(); mine = old.get(pid, [])
                if src == 'CHRG' and CR.hearing_qa_bounds(CR._page_text(pages[0][1])) is None:
                    rec['result'] = 'noqa'; to_insert, reowned, unrep, relabel = [], [], [(o, None) for o in mine], []
                else:
                    CR.parse_pages(pages, cham, date, src, pid, c, congress=congress)
                    rec['result'] = 'parsed'; to_insert, reowned, unrep, relabel = reconcile(c.rows, mine)
                rec.update(pages=len(pages), parsed=len(c.rows), stored=len(mine), insert=len(to_insert), relabel=len(relabel),
                           reowned=len(reowned), unreproduced=len(unrep))
                swap = {rid for rid, _, _ in relabel}
                for o, owner in reowned + unrep:
                    kind = 'unreproduced' if not owner else ('relabelled_same_surname' if a.apply else 'relabel_same_surname') if o[0] in swap else 'reowned_other_surname'
                    ledger.append((pid, o[0], o[1], owner or '', kind, o[2][:100].replace('\n', ' ')))
                tot.update(insert=len(to_insert), relabel=len(relabel), reowned=len(reowned), unreproduced=len(unrep), parsed=len(c.rows), stored=len(mine))
                gained.update(r[0] for r in to_insert); gained.update(m for _, _, m in relabel); lost.update(m for _, m, _ in relabel)
                planned += [('insert', r[0], r[1], pid, r[5], '') for r in to_insert]
                planned += [('relabel', new, date, pid, '', was) for _, was, new in relabel]
                if a.apply and (to_insert or relabel or status.get(prefix + pid) not in ('ok', 'noqa')):
                    lw.writerows(apply_reconciled(con, to_insert, relabel, batch)); lf.flush()
                    con.execute('INSERT OR REPLACE INTO cr_fetch_log VALUES(?,?,?)', (prefix + pid, 'noqa' if rec['result'] == 'noqa' else 'ok', now()))
                    con.commit(); rec['applied'] = True
        except Exception as e:
            con.rollback(); rec['result'] = 'error'; rec['err'] = (type(e).__name__ + ': ' + str(e))[:160]
        print(json.dumps(rec), flush=True); summ.write(json.dumps(rec) + '\n'); summ.flush()
        if n % 50 == 0: print('%s progress %d/%d %s' % (now(), n, len(work), dict(tot)), flush=True)
    summ.close()
    with open(os.path.join(a.out, 'ledger_%s.csv' % a.cmd), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['package', 'rowid', 'stored_member', 'now_owned_by', 'kind', 'text_head']); w.writerows(ledger)
    with open(os.path.join(a.out, 'planned_%s.csv' % a.cmd), 'w', newline='', encoding='utf-8-sig') as f:   # every row this run inserts / re-labels (dry run: would)
        w = csv.writer(f); w.writerow(['kind', 'member', 'date', 'package', 'nwords', 'was_member']); w.writerows(planned)
    with open(os.path.join(a.out, 'gained_%s.json' % a.cmd), 'w', encoding='utf-8') as f:
        json.dump({'gained': gained.most_common(), 'lost_by_relabel': lost.most_common()}, f, ensure_ascii=False)
    if a.apply: lf.close()
    print('%s DONE %s apply=%s batch=%s totals=%s members_gaining=%d' % (now(), src, a.apply, batch, dict(tot), len(gained)), flush=True)
    return 0


# ---- quarantine + fix pass (Paul 2026-10-20: "quarantine the misattributed rows, but aim to fix them") ----------------------
# A stored row the CURRENT resolver no longer reproduces is a suspect: the old resolver (no congress gate, no namesake guard)
# credited it to a roster member the label does not name.  Move every suspect into speeches_quarantine (keeping its rowid and
# every column), then re-resolve its original speaker label against ALL of HSall, not only the 533-member roster:
#   unique person on the roster      -> status 'restored': the row goes back to speeches under that member (same rowid)
#   unique person off the roster     -> 'parked_former': stays quarantined, proposed_member says who
#   several / nobody / no label      -> 'ambiguous' / 'unresolved': stays quarantined with its reason
# Nothing is deleted; every move is in the ledger CSV and undo_batch() reverses a whole batch exactly.
def _norm(sur):
    """Label surname part as resolve() normalises it, middle initials dropped."""
    s = re.sub(r"[^A-Z\- ']", '', CR._fold(sur).upper()).strip()
    return ' '.join(w for w in s.split() if len(w) > 1)


def _chambers(chamber): return [chamber] if chamber in ('house', 'senate') else ['house', 'senate']


def hist_names():
    """{person id: 'Given Surname'} for every HSall member (bioguide id, else icpsr)."""
    out = {}
    with open(os.path.join(ROOT, 'votes', 'HSall_members.csv'), encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            pid = r.get('bioguide_id') or r.get('icpsr'); bio = r.get('bioname') or ''
            if pid and bio:
                sn, _, given = bio.partition(',')
                out[pid] = (given.strip() + ' ' + sn.strip().title()).strip()
    return out


def hist_persons(sur, st, chamber, congress, year=None, date=None):
    """The distinct HSall people (anyone who sat in that Congress, not just the roster) a speaker label can name."""
    s = _norm(sur); ab = CR.ST2AB.get((st or '').lower(), st)
    def ents(key): return {e for ch in _chambers(chamber) for e in CR.HIST.get((key, congress, ch), ())}
    es = ents(s)
    if not es and ' ' in s:   # "AUSTIN SCOTT": last word = surname, the rest must name the person
        *qual, last = s.split(); es = {e for e in ents(last) if CR._given_ok(qual, e[2])}
    if ab: es = {e for e in es if e[1] == str(ab).upper()}
    if year: es = {e for e in es if not (e[3] and e[3] < year)}   # died before this floor day
    served = getattr(CR, '_served', None)   # the collector's term-date rule (a seat that changed hands mid-Congress): same filter resolve() applies
    if date and served: es = {e for e in es if served(e[0], _chambers(chamber), date) is not False}
    return {e[0] for e in es}


def refusal_reason(sur, st, chamber, congress, year=None):
    """Why the roster resolver refuses this label -> one of REASON_CODE's values."""
    s = _norm(sur); ab = CR.ST2AB.get((st or '').lower(), st); chambers = _chambers(chamber)
    def pool(key):
        out = []
        for ch in chambers:
            for m in CR.ROSTER.get((key, ch), []):
                if congress and CR.SERVICE and (m, congress, ch) not in CR.SERVICE: continue
                if m not in out: out.append(m)
        return out
    cand = pool(s)
    if not cand and ' ' in s:
        *q, last = s.split(); cand = [m for m in pool(last) if CR._given_ok(q, CR._given_tokens(m))]
    if not cand: return 'not_serving' if any(CR.ROSTER.get((s.split()[-1], ch)) for ch in chambers) else 'unverified'
    if ab:
        hit = [m for m in cand if CR.state.get(m, '').upper() == str(ab).upper()]
        if not hit and any(CR.state.get(m) for m in cand): return 'state_tag_other_person'
    return 'namesake_ambiguous'


def label_index(pages, src, date, congress=None, hearing_chamber=None):
    """{first 80 chars of a turn: {(label, surname, state, chamber, is_witness)}} for every speaker label on the pages, resolved or not,
    cut exactly as parse_pages cuts them (a CHRG row from an older, looser parse still finds its label: no Q&A bounds here)."""
    idx = collections.defaultdict(set); cg = congress or CR.congress_for_date(date)
    for fname, raw in pages:
        ch = hearing_chamber if src == 'CHRG' else ('house' if '-PgH' in fname else 'senate')
        txt = CR._page_text(raw)
        wit = CR.witness_surnames(txt, ch, cg) if src == 'CHRG' else set()
        ms = list((CR.TURN_C if src == 'CREC' else CR.TURN).finditer(txt))   # the collector's own rule: CREC keeps comma labels, hearings do not
        for i, mt in enumerate(ms):
            sur = mt.group(2)
            if src != 'CHRG' and not CR.upperish(sur): continue
            if sur.upper().split()[0] in CR.NONSPK: continue
            seg = txt[mt.end():ms[i + 1].start() if i + 1 < len(ms) else min(len(txt), mt.end() + 20000)]
            seg = re.split(r'The (?:PRESIDING OFFICER|SPEAKER|ACTING PRESIDENT|VICE PRESIDENT|CLERK|CHAIR)\b', seg)[0]
            seg = re.sub(r'\s+', ' ', seg).strip(); seg = re.sub(r'\[\[Page [^\]]+\]\]', '', seg); seg = seg.replace('____________________', ' ')
            if len(seg) < 150: continue
            label = '%s %s%s' % (mt.group(1), sur, ' of ' + mt.group(3) if mt.group(3) else '')
            idx[re.sub(r'\s+', ' ', seg)[:80]].add((label, sur, mt.group(3), ch, CR._fold(sur).upper().split()[-1] in wit))
    return idx


def decide(stored_member, persons, names, text, rows):
    """persons: the HSall people the label can name; rows: every stored (rowid, member, text) of the package."""
    none = dict(proposed_member=None, proposed_bioguide=None, proposed_on_roster=0)
    if not persons: return dict(status='unresolved', note='no HSall member fits the label', **none)
    if len(persons) > 1:
        return dict(status='ambiguous', note='%d people fit: %s' % (len(persons), ', '.join(sorted(names.get(p, p) for p in persons))), **none)
    pid, = persons; member = CR._bio2member.get(pid)
    if member is None: return dict(status='parked_former', proposed_member=names.get(pid, pid), proposed_bioguide=pid, proposed_on_roster=0, note='former member, off the roster')
    base = dict(proposed_member=member, proposed_bioguide=pid, proposed_on_roster=1)
    if member != stored_member and any(contains(x[2], text) for x in rows if x[1] == member):
        return dict(status='duplicate_of_existing', note='the proposed member already has this turn', **base)
    return dict(status='restored', note='stored member was right' if member == stored_member else '', **base)


def suspect_rows(src, pid, date, pages, mine, names, congress=None, hearing_chamber=None, skip=()):
    """Decision dicts for the stored rows of one package that the current resolver does not reproduce."""
    mine = [o for o in mine if o[0] not in skip]
    if not mine: return []
    cg = congress or CR.congress_for_date(date); year = int(date[:4]) if src == 'CREC' else None   # CHRG date = publication date: no death-year rule
    c = Collect()
    if src == 'CHRG' and CR.hearing_qa_bounds(CR._page_text(pages[0][1])) is None: unrep = [(o, None) for o in mine]
    else:
        CR.parse_pages(pages, (lambda f: 'house' if '-PgH' in f else 'senate') if src == 'CREC' else (lambda f: hearing_chamber), date, src, pid, c, congress=congress)
        unrep = reconcile(c.rows, mine)[2]
    if not unrep: return []
    idx = label_index(pages, src, date, congress, hearing_chamber)
    out = []
    for o, _ in unrep:
        base = dict(rowid=o[0], package=pid, source=src, stored_member=o[1], label='', proposed_member=None, proposed_bioguide=None, proposed_on_roster=0)
        labels = idx.get(re.sub(r'\s+', ' ', o[2])[:80], set())
        if not labels:
            out.append({**base, 'reason': 'chrg_unreproduced' if src == 'CHRG' else 'unverified', 'status': 'unresolved', 'note': 'no speaker segment carries this text'}); continue
        if len(labels) > 1:
            out.append({**base, 'reason': 'chrg_unreproduced' if src == 'CHRG' else 'unverified', 'status': 'ambiguous', 'label': ' | '.join(sorted(l[0] for l in labels)),
                        'note': 'the same opening starts turns under different labels'}); continue
        (label, sur, st, ch, witness), = labels
        fday = date if src == 'CREC' else None
        if CR.resolve(sur, st, ch, cg, year, fday):   # the label resolves today, so the row is not misattributed: leave it where it is
            continue
        reason = 'chrg_unreproduced' if src == 'CHRG' else refusal_reason(sur, st, ch, cg, year)
        if witness:
            out.append({**base, 'reason': reason, 'label': label, 'status': 'unresolved', 'note': 'the surname is a witness at this hearing'}); continue
        out.append({**base, 'reason': reason, 'label': label, **decide(o[1], hist_persons(sur, st, ch, cg, year, fday), names, o[2], mine)})
    return out


def quarantine_table(con):
    con.execute('''CREATE TABLE IF NOT EXISTS speeches_quarantine(
        q_id INTEGER PRIMARY KEY, orig_rowid INTEGER NOT NULL UNIQUE, member TEXT, date TEXT, source TEXT, chamber TEXT, package TEXT,
        nwords INTEGER, text TEXT, reason TEXT NOT NULL, label TEXT, outcome TEXT, proposed_member TEXT, proposed_bioguide TEXT,
        proposed_on_roster INTEGER, note TEXT, status TEXT NOT NULL, quarantined_at TEXT NOT NULL, batch_id TEXT NOT NULL,
        restored_at TEXT, restored_member TEXT, undone_at TEXT)''')


def apply_decisions(con, rows, batch):
    """Move/restore each decided row.  A row already in the table (quarantined or restored), or one that changed or vanished since the
    plan, is skipped, so a second run does nothing."""
    quarantine_table(con)
    for r in rows:
        if con.execute("SELECT 1 FROM speeches_quarantine WHERE orig_rowid=? AND status IN ('quarantined','restored')", (r['rowid'],)).fetchone(): continue
        cur = con.execute('SELECT member,date,source,chamber,package,nwords,text FROM speeches WHERE rowid=?', (r['rowid'],)).fetchone()
        if not cur or cur[0] != r['stored_member']: continue
        restored = r['status'] == 'restored'
        con.execute('INSERT OR REPLACE INTO speeches_quarantine(orig_rowid,member,date,source,chamber,package,nwords,text,reason,label,outcome,proposed_member,'
                    'proposed_bioguide,proposed_on_roster,note,status,quarantined_at,batch_id,restored_at,restored_member) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (r['rowid'], *cur, r['reason'], r['label'], r['status'], r['proposed_member'], r['proposed_bioguide'], r['proposed_on_roster'], r['note'],
                     'restored' if restored else 'quarantined', now(), batch, now() if restored else None, r['proposed_member'] if restored else None))
        if restored: con.execute('UPDATE speeches SET member=? WHERE rowid=? AND member=?', (r['proposed_member'], r['rowid'], cur[0]))
        else: con.execute('DELETE FROM speeches WHERE rowid=?', (r['rowid'],))


def undo_batch(con, batch):
    """Put every row of the batch back exactly as it was (same rowid, same member); the quarantine record is kept, marked 'undone'."""
    for q_id, rowid, member, date, source, chamber, package, nwords, text, status, restored_member in con.execute(
            "SELECT q_id,orig_rowid,member,date,source,chamber,package,nwords,text,status,restored_member FROM speeches_quarantine "
            "WHERE batch_id=? AND status IN ('quarantined','restored')", (batch,)).fetchall():
        if status == 'quarantined':
            if not con.execute('SELECT 1 FROM speeches WHERE rowid=?', (rowid,)).fetchone():
                con.execute('INSERT INTO speeches(rowid,member,date,source,chamber,package,nwords,text) VALUES(?,?,?,?,?,?,?,?)',
                            (rowid, member, date, source, chamber, package, nwords, text))
        else: con.execute('UPDATE speeches SET member=? WHERE rowid=? AND member=?', (member, rowid, restored_member))
        con.execute("UPDATE speeches_quarantine SET status='undone', undone_at=? WHERE q_id=?", (now(), q_id))


def quarantine_cmd(a):
    """Dry run unless --apply: select every suspect row, decide, write the ledger; --apply moves/restores them in short per-package commits."""
    if a.apply: guard_roster()
    os.makedirs(a.out, exist_ok=True)
    con = sqlite3.connect(('file:%s?mode=rw' if a.apply else 'file:%s?mode=ro') % DB.replace('\\', '/'), uri=True, timeout=60)
    con.execute('PRAGMA busy_timeout=60000')
    names = hist_names(); batch = 'Q' + datetime.datetime.now().strftime('%Y%m%dT%H%M%S')
    has = con.execute("SELECT 1 FROM sqlite_master WHERE name='speeches_quarantine'").fetchone()
    skip = {r[0] for r in con.execute("SELECT orig_rowid FROM speeches_quarantine WHERE status IN ('quarantined','restored')")} if has else set()
    status = dict(con.execute('SELECT key,status FROM cr_fetch_log')); ledger = []; tot = collections.Counter(); errors = 0
    for src in a.sources:
        print('%s loading stored %s rows ...' % (now(), src), flush=True)
        old = collections.defaultdict(list)
        for rowid, member, pkg, text in con.execute('SELECT rowid,member,package,text FROM speeches WHERE source=?', (src,)):
            old[pkg].append((rowid, member, text))
        for date, pid in work_list(a, src, status):
            if src == 'CREC': congress = hearing_chamber = None
            else: congress, hearing_chamber = CR.chrg_scope(pid)
            if not any(o[0] not in skip for o in old.get(pid, [])): continue
            pages = cached(a.cache, pid, (lambda: crec_pages(date)) if src == 'CREC' else (lambda: [(pid + '.htm', CR.fetch_chrg_html(pid))]))
            if pages is None: continue
            try:
                rows = suspect_rows(src, pid, date, pages, old[pid], names, congress, hearing_chamber, skip)
                if a.apply and rows: apply_decisions(con, rows, batch); con.commit()
            except Exception as e:
                con.rollback(); errors += 1
                print(json.dumps({'package': pid, 'result': 'error', 'err': (type(e).__name__ + ': ' + str(e))[:160]}), flush=True)
                if errors >= 3: raise SystemExit('3 packages failed; stopping (a systematic fault, not a transient one)')
                continue
            for r in rows:
                kept = r['status'] == 'restored'
                ledger.append((batch, r['rowid'], pid, src, r['stored_member'], r['reason'], r['label'], r['status'], r['proposed_member'] or '', r['proposed_bioguide'] or '',
                               r['proposed_on_roster'], 'speeches' if kept else 'speeches_quarantine', r['proposed_member'] if kept else '', r['note']))
                tot[(src, r['reason'], r['status'])] += 1
            if rows: print(json.dumps({'ts': now(), 'package': pid, 'suspects': len(rows), 'applied': a.apply}), flush=True)
    path = os.path.join(a.out, 'quarantine_ledger_%s.csv' % ('apply' if a.apply else 'dry'))
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['batch', 'rowid', 'package', 'source', 'stored_member', 'reason', 'label', 'outcome', 'proposed_member', 'proposed_bioguide',
                                       'proposed_on_roster', 'final_location', 'final_member', 'note']); w.writerows(ledger)
    with open(os.path.join(a.out, 'quarantine_summary_%s.json' % ('apply' if a.apply else 'dry')), 'w', encoding='utf-8') as f:
        json.dump({'batch': batch, 'apply': a.apply, 'rows': len(ledger), 'by_source_reason_outcome': {'|'.join(k): v for k, v in sorted(tot.items())}}, f, indent=1)
    print('%s DONE quarantine apply=%s batch=%s suspects=%d errors=%d %s' % (now(), a.apply, batch, len(ledger), errors, dict(collections.Counter(r[7] for r in ledger))), flush=True)
    return 1 if errors else 0


def undo_cmd(a):
    con = sqlite3.connect('file:%s?mode=rw' % DB.replace('\\', '/'), uri=True, timeout=60)
    if getattr(a, 'ledger', None):
        rows = [(r['batch'], r['kind'], int(r['rowid']), r['member'], r['date'], r['package'], r['was_member'], r['text_sha1'])
                for r in csv.DictReader(open(a.ledger, encoding='utf-8-sig'))]
        res = undo_applied(con, rows)
        if a.apply: con.commit()
        else: con.rollback()
        print('%s %s %s of %s' % (now(), 'undid' if a.apply else 'would undo', res, a.ledger), flush=True)
        return 0
    if getattr(a, 'refine_batch', None):
        n = con.execute("SELECT COUNT(*) FROM speeches_quarantine_log WHERE batch_id=? AND undone_at IS NULL AND action IN ('restored','reparked')", (a.refine_batch,)).fetchone()[0]
        if a.apply: undo_refine(con, a.refine_batch); con.commit()
        print('%s %s %d rows of refine batch %s' % (now(), 'undid' if a.apply else 'would undo', n, a.refine_batch), flush=True)
        return 0
    n = con.execute("SELECT COUNT(*) FROM speeches_quarantine WHERE batch_id=? AND status IN ('quarantined','restored')", (a.batch,)).fetchone()[0]
    if a.apply: undo_batch(con, a.batch); con.commit()
    print('%s %s %d rows of batch %s' % (now(), 'undid' if a.apply else 'would undo', n, a.batch), flush=True)
    return 0


# ---- refine pass (READ-ONLY): only exact, unique evidence moves a quarantined row ----------------------------------------------
# 1. CHRG: a hearing print lists its committee's members by full name and state in the front matter; "Mr. GREEN." is resolved against
#    THAT list only (Al Green sits on House Financial Services, Mark Green does not).
# 2. CREC: "The Chair recognizes the gentleman from Texas (Mr. GREEN)" printed just before the turn names the state.
# 3. CREC: a row with no speaker segment whose text is printed exactly once, right after "Ms. HIRONO, ", and opens by addressing the chair.
# 4. CREC: identical boilerplate spoken by several members in one day is split by elimination, only when the counts close exactly.
# No fuzzy matching.  This pass writes a ledger and changes nothing in the database.
STATE_NAMES = {k.title(): v for k, v in CR.ST2AB.items()}   # 'North Carolina' -> 'NC'
ROSTER_CELL = re.compile(r"^([A-Z][A-Za-z.'`\-À-ÿ ]+?),\s+(%s)(?:,.*)?,?\s*$" % '|'.join(re.escape(s) for s in sorted(STATE_NAMES, key=len, reverse=True)))
RECOGNIZED = re.compile(r"(?:gentleman|gentlewoman|gentlelady|Senator|Member|Delegate|Resident Commissioner)\s+from\s+([A-Z][a-z]+(?: [A-Z][a-z]+){0,2})"
                        r"\s*\(\s*(?:Mr|Mrs|Ms|Miss)\.\s*([A-Za-z'\-]+(?: [A-Za-z'\-]+)?)\s*\)")
LABEL_BEFORE = re.compile(r"(Mr|Mrs|Ms|Miss)\.\s+((?:[A-Z][A-Za-z'\-]+|[A-Z]\.)(?: (?:[A-Z][A-Za-z'\-]+|[A-Z]\.)){0,3}?)(?: of ([A-Z][a-z]+(?: [A-Z][a-z]+)?))?,\s*$")
ADDRESSES_CHAIR = re.compile(r"(?:Mr\.|Mrs\.|Madam|Madame) (?:Speaker|President|Chair(?:man|woman)?|Vice President)\b")


EVIDENCE = {'roster': 'hearing_roster',          # CHRG: the hearing's own member list.  RISK: a member who waived on to the hearing but is not on that list is
            'context': 'recognition_line',       #   credited to the listed namesake -- recorded per row so the caveat travels with it
            'adjacent_label': 'adjacent_label', 'label': 'exact_turn_label', 'elimination': 'elimination'}


def _flat(s): return re.sub(r'\s+', ' ', s).strip()


def person_of_member(member): return (CR.C.get(member) or {}).get('bioguide')


def hearing_roster(text):
    """[(name tokens, state abbrev)] from the committee / subcommittee member list in a hearing's front matter (before the contents)."""
    cut = re.search(r'C\s?O\s?N\s?T\s?E\s?N\s?T\s?S|\bWITNESSES\b', text)
    out = []
    for line in text[:cut.start() if cut else 40000][:40000].splitlines():
        for cell in re.split(r'\s{2,}', line.strip()):
            m = ROSTER_CELL.match(cell.strip())
            if not m: continue
            name = re.sub(r"``[^']*''|\"[^\"]*\"", ' ', m.group(1))   # ``CHUY'' nickname
            letters = [c for c in name if c.isalpha()]
            toks = tuple(_norm(name).split())
            if letters and sum(c.isupper() for c in letters) / len(letters) >= 0.6 and len(toks) >= 2: out.append((toks, STATE_NAMES[m.group(2)]))
    return list(dict.fromkeys(out))


def roster_hits(sur, st, entries):
    """The member-list entries a label can name: its surname words are the tail of the entry's name, and the state agrees when the label has one."""
    want = tuple(_norm(sur).split()); ab = CR.ST2AB.get((st or '').lower(), st)
    if not want: return []
    return [e for e in entries if len(e[0]) >= len(want) and e[0][-len(want):] == want and (not ab or e[1] == str(ab).upper())]


def roster_persons(sur, st, entries, chamber, congress):
    """HSall people the label names ACCORDING TO THE HEARING'S OWN MEMBER LIST; None when that list is silent about the surname."""
    hits = roster_hits(sur, st, entries)
    if not hits: return None
    out = set()
    for toks, state in hits:
        found = hist_persons(' '.join(toks), state, chamber, congress)
        out |= found or hist_persons(toks[-1], state, chamber, congress)   # 'WM. LACY CLAY' vs HSall's William: state + surname alone
    return out


def context_state(before, sur):
    """State abbrev from the NEAREST 'the gentleman from <State> (Mr. SURNAME)' in the text before a turn; the surname must match exactly."""
    want = _norm(sur); found = None
    for m in RECOGNIZED.finditer(before):
        if _norm(m.group(2)) == want and m.group(1).lower() in CR.ST2AB: found = CR.ST2AB[m.group(1).lower()]
    return found


def adjacent_label(before):
    """(prefix, surname, state) of a 'Ms. HIRONO, ' label that ends the text before a turn (older ingests accepted a comma label)."""
    m = LABEL_BEFORE.search(before.rstrip() + ' ')
    return (m.group(1), m.group(2), m.group(3)) if m and CR.upperish(m.group(2)) else None


def addresses_chair(text): return bool(ADDRESSES_CHAIR.match(text.lstrip()))


def turn_index(pages, src, date, congress=None, hearing_chamber=None):
    """Every speaker turn on the pages cut exactly as parse_pages cuts it: {label, sur, st, ch, witness, seg (whitespace-flattened), before}."""
    cg = congress or CR.congress_for_date(date); out = []
    for fname, raw in pages:
        ch = hearing_chamber if src == 'CHRG' else ('house' if '-PgH' in fname else 'senate')
        txt = CR._page_text(raw)
        wit = CR.witness_surnames(txt, ch, cg) if src == 'CHRG' else set()
        ms = list((CR.TURN_C if src == 'CREC' else CR.TURN).finditer(txt))   # the collector's own rule: CREC keeps comma labels, hearings do not
        for i, mt in enumerate(ms):
            sur = mt.group(2)
            if src != 'CHRG' and not CR.upperish(sur): continue
            if sur.upper().split()[0] in CR.NONSPK: continue
            seg = txt[mt.end():ms[i + 1].start() if i + 1 < len(ms) else min(len(txt), mt.end() + 20000)]
            seg = re.split(r'The (?:PRESIDING OFFICER|SPEAKER|ACTING PRESIDENT|VICE PRESIDENT|CLERK|CHAIR)\b', seg)[0]
            seg = re.sub(r'\s+', ' ', seg).strip(); seg = re.sub(r'\[\[Page [^\]]+\]\]', '', seg); seg = seg.replace('____________________', ' ')
            if len(seg) < 150: continue
            out.append(dict(label='%s %s%s' % (mt.group(1), sur, ' of ' + mt.group(3) if mt.group(3) else ''), sur=sur, st=mt.group(3), ch=ch,
                            witness=CR._fold(sur).upper().split()[-1] in wit, seg=_flat(seg), before=_flat(txt[max(0, mt.start() - 900):mt.start()])[-700:]))
    return out


def refine_rows(src, pid, date, pages, qrows, stored, names, congress=None, hearing_chamber=None):
    """qrows: quarantined rows of one package [{rowid, member, text, reason, outcome, label, proposed_member}]; stored: its other rows (rowid, member, text).
    -> one verdict per quarantined row; nothing is written."""
    cg = congress or CR.congress_for_date(date); year = int(date[:4]) if src == 'CREC' else None; fday = date if src == 'CREC' else None
    by_seg = collections.defaultdict(list)
    for t in turn_index(pages, src, date, congress, hearing_chamber): by_seg[t['seg']].append(t)
    entries = hearing_roster(CR._page_text(pages[0][1])) if src == 'CHRG' else []
    norm_pages = [(f, _flat(CR._page_text(raw))) for f, raw in pages] if src == 'CREC' else []

    def persons_of(t):
        found = hist_persons(t['sur'], t['st'], t['ch'], cg, year, fday)
        if len(found) == 1: return found, 'label'
        if src == 'CHRG' and entries:
            listed = roster_persons(t['sur'], t['st'], entries, t['ch'], cg)
            if listed: return listed, 'roster'
        if src == 'CREC' and not t['st']:
            state = context_state(t['before'], t['sur'])
            narrowed = hist_persons(t['sur'], state, t['ch'], cg, year, fday) if state else set()
            if narrowed: return narrowed, 'context'
        return found, 'label'

    def verdict(q, persons, method, note=''):
        d = decide(q['member'], persons, names, q['text'], stored); s = d['status']
        if s == 'restored': new = 'restore' if d['proposed_member'] == q['member'] else 'reassign_current'
        elif s == 'parked_former': new = 'unchanged' if q['outcome'] == 'parked_former' and d['proposed_member'] == q.get('proposed_member') else 'reassign_former'
        elif s == 'ambiguous': new = 'stay_ambiguous'
        elif s == 'duplicate_of_existing': new = 'stay_duplicate'
        else: new = 'stay_unresolved'
        return dict(rowid=q['rowid'], package=pid, source=src, reason=q['reason'], stored_member=q['member'], old_outcome=q['outcome'], method=method, new_outcome=new,
                    evidence_kind=EVIDENCE.get(method, ''), proposed_member=d['proposed_member'], proposed_bioguide=d['proposed_bioguide'],
                    proposed_on_roster=d['proposed_on_roster'], note=(d['note'] + ' ' + note).strip())

    def stay(q, method, note):   # no new evidence: the row keeps its old status (and, if parked, its old proposed member)
        parked = q['outcome'] == 'parked_former'
        return dict(rowid=q['rowid'], package=pid, source=src, reason=q['reason'], stored_member=q['member'], old_outcome=q['outcome'], method=method,
                    new_outcome='unchanged' if parked else 'stay_ambiguous' if q['outcome'] == 'ambiguous' else 'stay_unresolved', evidence_kind='',
                    proposed_member=q.get('proposed_member') if parked else None, proposed_bioguide=q.get('proposed_bioguide') if parked else None,
                    proposed_on_roster=0, note=note)

    def adjacent(q):
        if src != 'CREC': return stay(q, 'none', 'no speaker segment carries this text')
        head = _flat(q['text'])[:70]
        occ = [(f, m.start(), pt) for f, pt in norm_pages for m in re.finditer(re.escape(head), pt)]
        if len(occ) != 1: return stay(q, 'adjacent_label', '%d places on the day\'s pages open with this text' % len(occ))
        f, pos, pt = occ[0]; lab = adjacent_label(pt[max(0, pos - 160):pos])
        if not lab: return stay(q, 'adjacent_label', 'no speaker label immediately before the text')
        if not addresses_chair(q['text']): return stay(q, 'adjacent_label', 'the text does not open by addressing the chair')
        return verdict(q, hist_persons(lab[1], lab[2], 'house' if '-PgH' in f else 'senate', cg, year, fday), 'adjacent_label')

    def eliminate(ts, qs, key):
        who = [next(iter(p)) if len(p) == 1 else None for p, _ in (persons_of(t) for t in ts)]
        left = list(who)
        for _rid, member, text in stored:
            if _flat(text) == key and person_of_member(member) in left: left.remove(person_of_member(member))
        if len(left) != len(qs) or None in left:
            return [stay(q, 'elimination', '%d labels share this text, %d stored rows account for %d of them' % (len(ts), len(ts) - len(left), len(ts) - len(left))) for q in qs]
        out = []
        for q in sorted(qs, key=lambda r: r['rowid']):
            own = person_of_member(q['member']); pick = own if own in left else sorted(left)[0]; left.remove(pick)
            out.append(verdict(q, {pick}, 'elimination'))
        return out

    groups = collections.defaultdict(list)
    for q in qrows: groups[_flat(q['text'])].append(q)
    out = []
    for key, qs in groups.items():
        ts = by_seg.get(key, [])
        if len(ts) == 1 and ts[0]['witness']: out += [stay(q, 'label', 'the surname is a witness at this hearing') for q in qs]
        elif len(ts) == 1:
            persons, how = persons_of(ts[0]); out += [verdict(q, persons, how) for q in qs]
        elif ts: out += eliminate(ts, qs, key)
        else: out += [adjacent(q) for q in qs]
    return out


def _ensure_cols(con, table, cols):
    have = {r[1] for r in con.execute('PRAGMA table_info(%s)' % table)}
    for c, t in cols:
        if c not in have: con.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, c, t))


def refine_table(con):
    quarantine_table(con)
    _ensure_cols(con, 'speeches_quarantine', [('evidence_kind', 'TEXT'), ('refine_batch', 'TEXT'), ('refined_at', 'TEXT')])
    con.execute('''CREATE TABLE IF NOT EXISTS speeches_quarantine_log(
        log_id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL, orig_rowid INTEGER NOT NULL, action TEXT NOT NULL, evidence_kind TEXT, method TEXT,
        before_status TEXT, before_outcome TEXT, before_proposed_member TEXT, before_proposed_bioguide TEXT, before_proposed_on_roster INTEGER, before_note TEXT,
        after_status TEXT, after_outcome TEXT, after_proposed_member TEXT, after_proposed_bioguide TEXT, after_proposed_on_roster INTEGER, after_note TEXT,
        restored_member TEXT, applied_at TEXT, undone_at TEXT)''')


def apply_refine(con, verdicts, batch):
    """Apply restore / reassign verdicts.  restore & reassign_current: the quarantined row goes back into speeches under its ORIGINAL rowid (unless the
    target member already holds that turn); reassign_former: only the parked-for member changes.  Each change is logged before/after under `batch`.
    -> [{'rowid', 'applied': restored | reparked | skipped_duplicate | skipped_rowid_taken | no_change}]"""
    refine_table(con); out = []
    for v in verdicts:
        res = {'rowid': v['rowid'], 'applied': 'no_change'}; out.append(res)
        if v['new_outcome'] not in ('restore', 'reassign_current', 'reassign_former'): continue
        q = con.execute('SELECT member,date,source,chamber,package,nwords,text,status,outcome,proposed_member,proposed_bioguide,proposed_on_roster,note '
                        'FROM speeches_quarantine WHERE orig_rowid=?', (v['rowid'],)).fetchone()
        if not q or q[7] != 'quarantined' or q[0] != v['stored_member']: continue          # moved on, or not the row the plan saw
        member, date, source, chamber, package, nwords, text, status, outcome, pmember, pbio, pon, note = q
        before = (status, outcome, pmember, pbio, pon, note)
        if v['new_outcome'] == 'reassign_former':
            if outcome == 'parked_former' and pmember == v['proposed_member'] and pbio == v['proposed_bioguide']: continue
            action, after, target = 'reparked', ('quarantined', 'parked_former', v['proposed_member'], v['proposed_bioguide'], 0, v['note']), None
        else:
            target = v['proposed_member']
            if con.execute('SELECT 1 FROM speeches WHERE rowid=?', (v['rowid'],)).fetchone(): action = 'skipped_rowid_taken'
            elif any(contains(t, text) for (t,) in con.execute('SELECT text FROM speeches WHERE member=? AND package=?', (target, package))): action = 'skipped_duplicate'
            else: action = 'restored'
            after = ('restored', 'restored', target, v['proposed_bioguide'], 1, v['note'])
        if action in ('skipped_duplicate', 'skipped_rowid_taken'):
            res['applied'] = action
            con.execute('INSERT INTO speeches_quarantine_log(batch_id,orig_rowid,action,evidence_kind,method,applied_at) VALUES(?,?,?,?,?,?)',
                        (batch, v['rowid'], action, v.get('evidence_kind'), v.get('method'), now()))
            continue
        if action == 'restored':
            con.execute('INSERT INTO speeches(rowid,member,date,source,chamber,package,nwords,text) VALUES(?,?,?,?,?,?,?,?)',
                        (v['rowid'], target, date, source, chamber, package, nwords, text))
        con.execute('UPDATE speeches_quarantine SET status=?,outcome=?,proposed_member=?,proposed_bioguide=?,proposed_on_roster=?,note=?,evidence_kind=?,refine_batch=?,refined_at=?,'
                    'restored_at=?,restored_member=? WHERE orig_rowid=?',
                    (*after, v.get('evidence_kind'), batch, now(), now() if action == 'restored' else None, target if action == 'restored' else None, v['rowid']))
        con.execute('INSERT INTO speeches_quarantine_log(batch_id,orig_rowid,action,evidence_kind,method,before_status,before_outcome,before_proposed_member,before_proposed_bioguide,'
                    'before_proposed_on_roster,before_note,after_status,after_outcome,after_proposed_member,after_proposed_bioguide,after_proposed_on_roster,after_note,restored_member,applied_at) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (batch, v['rowid'], action, v.get('evidence_kind'), v.get('method'), *before, *after, target if action == 'restored' else None, now()))
        res['applied'] = action
    return out


def undo_refine(con, batch):
    """Put every row the batch touched back exactly as it was: a restored row leaves speeches again (its text never left speeches_quarantine)."""
    for lid, rowid, action, rmember, *before in con.execute(
            "SELECT log_id,orig_rowid,action,restored_member,before_status,before_outcome,before_proposed_member,before_proposed_bioguide,before_proposed_on_roster,before_note "
            "FROM speeches_quarantine_log WHERE batch_id=? AND undone_at IS NULL AND action IN ('restored','reparked')", (batch,)).fetchall():
        if action == 'restored': con.execute('DELETE FROM speeches WHERE rowid=? AND member=?', (rowid, rmember))
        con.execute('UPDATE speeches_quarantine SET status=?,outcome=?,proposed_member=?,proposed_bioguide=?,proposed_on_roster=?,note=?,restored_at=NULL,restored_member=NULL,'
                    'evidence_kind=NULL,refine_batch=NULL,refined_at=NULL WHERE orig_rowid=?', (*before, rowid))
        con.execute('UPDATE speeches_quarantine_log SET undone_at=? WHERE log_id=?', (now(), lid))


def refine_cmd(a):
    """Dry run unless --apply: re-read every quarantined row with exact, unique evidence and write refine_ledger.csv; --apply restores / re-parks them
    (reversible: undo --refine-batch)."""
    if a.apply: guard_roster()
    os.makedirs(a.out, exist_ok=True)
    con = sqlite3.connect(('file:%s?mode=rw' if a.apply else 'file:%s?mode=ro') % DB.replace('\\', '/'), uri=True, timeout=60)
    con.execute('PRAGMA busy_timeout=60000')
    names = hist_names(); q = collections.defaultdict(list); batch = 'R' + datetime.datetime.now().strftime('%Y%m%dT%H%M%S')
    for rowid, member, date, src, pkg, reason, outcome, label, prop, pbio, text in con.execute(
            "SELECT orig_rowid,member,date,source,package,reason,outcome,label,proposed_member,proposed_bioguide,text FROM speeches_quarantine WHERE status='quarantined' ORDER BY orig_rowid"):
        q[(src, pkg, date)].append(dict(rowid=rowid, member=member, text=text, reason=reason, outcome=outcome, label=label, proposed_member=prop, proposed_bioguide=pbio))
    stored = collections.defaultdict(list); wanted = {k[1] for k in q}
    for rowid, member, pkg, text in con.execute("SELECT rowid,member,package,text FROM speeches WHERE source IN ('CREC','CHRG')"):
        if pkg in wanted: stored[pkg].append((rowid, member, text))
    out = []
    for n, ((src, pkg, date), qrows) in enumerate(sorted(q.items(), key=lambda kv: (kv[0][2], kv[0][1])), 1):
        pages = cached(a.cache, pkg, lambda: None)
        if pages is None: print(json.dumps({'package': pkg, 'result': 'no cached pages'}), flush=True); continue
        congress, hc = CR.chrg_scope(pkg) if src == 'CHRG' else (None, None)
        vs = refine_rows(src, pkg, date, pages, qrows, stored.get(pkg, []), names, congress, hc)
        if a.apply:
            try: res = {r['rowid']: r['applied'] for r in apply_refine(con, vs, batch)}; con.commit()
            except Exception as e:
                con.rollback(); print(json.dumps({'package': pkg, 'result': 'error', 'err': (type(e).__name__ + ': ' + str(e))[:160]}), flush=True); raise
        else: res = {}
        for v in vs: v['applied'] = res.get(v['rowid'], 'dry_run')
        out += vs
        if n % 100 == 0: print('%s %d/%d packages' % (now(), n, len(q)), flush=True)
    with open(os.path.join(a.out, 'refine_ledger.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        cols = ['batch', 'rowid', 'package', 'source', 'reason', 'stored_member', 'old_outcome', 'method', 'evidence_kind', 'new_outcome', 'applied', 'proposed_member',
                'proposed_bioguide', 'proposed_on_roster', 'final_location', 'final_member', 'note']
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for v in out:
            moved = v['new_outcome'] in ('restore', 'reassign_current') and v['applied'] in ('restored', 'dry_run')
            w.writerow({**v, 'batch': batch if a.apply else '', 'final_location': 'speeches' if moved else 'speeches_quarantine', 'final_member': v['proposed_member'] if moved else ''})
    tot = collections.defaultdict(collections.Counter)
    for r in out: tot[r['reason']][r['new_outcome']] += 1
    print('%s DONE refine apply=%s batch=%s rows=%d applied=%s %s' % (now(), a.apply, batch, len(out), dict(collections.Counter(r['applied'] for r in out)), {k: dict(v) for k, v in tot.items()}), flush=True)
    return 0


def roster_members():
    """[(member, status, term_start, term_end)]: every member of data/engine/roster.json plus its departed ones (a departed member's own historical rows stay his)."""
    r = json.load(open(os.path.join(ROOT, 'data', 'engine', 'roster.json'), encoding='utf-8'))
    return [(m['member'], status, m.get('term_start') or '', m.get('term_end') or '') for status, key in (('current', 'members'), ('departed', 'departed')) for m in r.get(key, [])]


def counts(a):
    ledger = roster_members()
    con = sqlite3.connect('file:%s?mode=ro' % DB.replace('\\', '/'), uri=True, timeout=60)
    agg = {}   # (member, source) -> [n, min date, max date]
    for m, s, n, lo, hi in con.execute("SELECT member,source,COUNT(*),MIN(date),MAX(date) FROM speeches WHERE source IN ('CREC','CHRG') GROUP BY member,source"):
        agg[(m, s)] = [n, lo, hi]
    for cmd in (['crec', 'chrg'] if a.plan else []):
        for r in csv.DictReader(open(os.path.join(a.plan, 'planned_%s.csv' % cmd), encoding='utf-8-sig')):
            k = (r['member'], cmd.upper()); v = agg.setdefault(k, [0, r['date'], r['date']])
            v[0] += 1; v[1] = min(v[1], r['date']); v[2] = max(v[2], r['date'])
            if r['kind'] == 'relabel': agg[(r['was_member'], cmd.upper())][0] -= 1   # its dates stay in the min/max: a reversed row only ever sits inside the range it already had
    with open(a.out, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f); w.writerow(['member', 'crec_count', 'crec_min_date', 'crec_max_date', 'chrg_count', 'chrg_min_date', 'chrg_max_date', 'status', 'term_start', 'term_end'])
        for m, status, t0, t1 in ledger:
            c, h = agg.get((m, 'CREC'), [0, '', '']), agg.get((m, 'CHRG'), [0, '', ''])
            w.writerow([m, c[0], c[1] if c[0] else '', c[2] if c[0] else '', h[0], h[1] if h[0] else '', h[2] if h[0] else '', status, t0, t1])
    print('%s wrote %s: %d members (%d departed), %d with zero CREC, %d with zero CHRG' % (now(), a.out, len(ledger), sum(1 for x in ledger if x[1] == 'departed'),
          sum(1 for x in ledger if not agg.get((x[0], 'CREC'), [0])[0]), sum(1 for x in ledger if not agg.get((x[0], 'CHRG'), [0])[0])))
    return 0


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('cmd', choices=['crec', 'chrg', 'counts', 'quarantine', 'undo', 'refine'])
    ap.add_argument('--cache'); ap.add_argument('--out'); ap.add_argument('--plan'); ap.add_argument('--batch'); ap.add_argument('--refine-batch', dest='refine_batch'); ap.add_argument('--ledger')
    ap.add_argument('--sources', default='CREC,CHRG', type=lambda v: [x.strip().upper() for x in v.split(',')])
    ap.add_argument('--since', default='2019-01-01'); ap.add_argument('--until', default=str(datetime.date.today()))
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    if a.cmd == 'undo':
        if not (a.batch or a.refine_batch or a.ledger): ap.error('undo needs --batch, --refine-batch or --ledger')
        return undo_cmd(a)
    if not a.out: ap.error('--out is required')
    if a.cmd == 'counts': return counts(a)
    if not a.cache: ap.error('%s needs --cache' % a.cmd)
    if a.cmd == 'refine': return refine_cmd(a)
    return quarantine_cmd(a) if a.cmd == 'quarantine' else run(a)


if __name__ == '__main__': sys.exit(main())
