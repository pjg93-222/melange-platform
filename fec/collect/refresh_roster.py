# -*- coding: utf-8 -*-
"""ROSTER REFRESH: keep the member roster current, every run (roster lane 2026-10-20).

Paul, 2026-10-20: "The roster goes to 535 now, and this needs to always auto-update."

What one run does, in order (Glassbox: every stage prints, and the run leaves a receipt):
  1. SNAPSHOT  resolve unitedstates/congress-legislators main to a commit, and install that commit's six reference files as a
     NEW directory data/staffer_network/reference/congress-legislators/<sha>/ with a sealed source manifest (the snapshot's
     freshness rule: refresh only through a new commit-pinned manifest, never mutate a snapshot).  The pointer file CURRENT
     names the installed commit; fec/roster.py:snapshot_dir() resolves it for every consumer.
  2. CROSS-CHECK  the in-scope member set (roster.in_scope: senators, voting representatives, DC and PR delegates; never the
     four territorial delegates) must be the SAME set of bioguides in the snapshot, clerk.house.gov MemberData.xml and the
     senate.gov senators XML, and the legislators-current.csv export must agree with the snapshot on members and FEC ids.
     Any disagreement HOLDS the run: nothing is applied, the receipt names every difference, exit 0 (sources lag each
     other by days; a held day is not a failed refresh).
  3. DELTA  against data/engine/roster.json: departures (term ended or died: the last term's end date), arrivals (the oath
     date = the new term's start), party changes, name changes (official_full; reported, never renamed automatically: a
     display name is the join key of every sheet and artifact), seat changes, FEC id changes.
  4. APPLY (--apply)  backups first (workbook first) under backups/roster_refresh_<stamp>/ with a sha256 manifest; then
       the workbook   departed rows deleted and arrival rows inserted at their alphabetical place in every member block
                      (fec/workbook_rows.py: Excel reference semantics; seat demographics from the district census file);
                      Committee Data rows for departures removed and for arrivals added from committee-membership-current
                      (plus --committee-rows from an official-source check); existing members' seat text is hand-curated
                      and is never rewritten here (upstream drift is listed in the receipt instead);
       fec/legislators.csv, data/engine/congestion.json (arrivals carry zero money until FEC review),
       data/members/member_desk_src.json (departed members move to its 'departed' list as history),
       and last data/engine/roster.json + the snapshot pointer.
     An open workbook (PermissionError / Excel owner file) refuses the whole apply before the first write: retry later.
     A failure after the first write restores every file from the backup.
  5. MONEY FENCE  nothing that changes FEC ids or money attribution applies on its own: a changed or new candidate id waits
     in data/engine/roster_paul_review.json until it appears in data/engine/roster_fec_approvals.json (Paul's per-change OK).

Exit: 0 applied / no change / held | 1 error (restored) | 2 refused (workbook open, pipeline lock) - retry later.
Usage:
  python fec/collect/refresh_roster.py [--apply] [--snapshot-dir DIR] [--sources-dir DIR] [--committee-rows FILE]
  python fec/collect/refresh_roster.py --bootstrap     # write roster.json for the CURRENT roster (no other writes)
"""
import argparse
import csv
import datetime
import hashlib
import io
import json
import os
import shutil
import sys
import time
import traceback
import urllib.request
import xml.etree.ElementTree as ET

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
_FEC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _FEC not in sys.path:
    sys.path.insert(0, _FEC)
import paths  # noqa: E402
import roster  # noqa: E402

ROOT = paths.ROOT
REPO = 'unitedstates/congress-legislators'
FILES = ['committee-membership-current.yaml', 'committees-current.yaml', 'committees-historical.yaml',
         'legislators-current.yaml', 'legislators-historical.yaml', 'LICENSE']
URL_HEAD = 'https://api.github.com/repos/%s/commits/main' % REPO
URL_RAW = 'https://raw.githubusercontent.com/%s/%%s/%%s' % REPO
URL_CSV = 'https://unitedstates.github.io/congress-legislators/legislators-current.csv'
URL_CLERK = 'https://clerk.house.gov/xml/lists/MemberData.xml'
URL_SENATE = 'https://www.senate.gov/general/contact_information/senators_cfm.xml'
UA = {'User-Agent': 'melange-roster-refresh (research; contact via repository owner)'}
STALE_DAYS = 21          # an unreachable upstream is a warning until the installed snapshot is this old, then an error

LEG_CSV = os.path.join(ROOT, 'fec', 'legislators.csv')
CONGESTION = os.path.join(ROOT, 'data', 'engine', 'congestion.json')
DESK_SRC = os.path.join(ROOT, 'data', 'members', 'member_desk_src.json')
CENSUS = os.path.join(ROOT, 'Congressional_District_Census_Data.xlsx')
RECEIPT = os.path.join(ROOT, 'data', 'engine', 'roster_refresh_receipt.json')
REVIEW = os.path.join(ROOT, 'data', 'engine', 'roster_paul_review.json')
APPROVALS = os.path.join(ROOT, 'data', 'engine', 'roster_fec_approvals.json')

# Display names Paul ruled (2026-10-20).  Every other arrival follows display_name()'s rule.
DISPLAY_NAME = {'G000608': 'Darline Graham', 'B001328': 'Everton Blair'}
SUFFIXES = {'jr', 'jr.', 'sr', 'sr.', 'ii', 'iii', 'iv'}

# Member-block sheets that share the Rank & Allocation order (alphabetical by display name): (sheet, name column).
ORDERED_SHEETS = [('Rank & Allocation', 'A'), ('Blended Model', None), ('IM Allocation', 'A'),
                  ('Legislative Score - Raw', 'A'), ('Legislative Score - Normalized', 'A'),
                  ('Primary Layer', 'B'), ('Primary Layer - Normalized', 'B'),
                  ('Sub Layer - Raw', 'B'), ('Sub Layer - Normalized', 'B'),
                  ('Demographic Factors - Raw', 'A'), ('Demographic - Normalized', 'A'),
                  ('Press Release - Raw', 'A'), ('Press Release - Normalized', 'A')]
# Committee Data seat text -> congress-legislators committee ids (audits/roster_refresh_20261020/tools/cmte_rows.py, checked
# against the sheet by Jaccard overlap of holders; 'Commerce (full)' is SSCM itself, the monetary-policy task force HSBA23).
SEAT_IDS = {
    'Agriculture (full)': ['HSAG', 'SSAF'], 'Agriculture - Commodity Markets, Digital Assets & Rural Development': ['HSAG22'],
    'Agriculture - Rural Development, Energy & Credit': ['SSAF15'], 'Banking (full)': ['SSBK'],
    'Banking - Digital Assets': ['SSBK13'], 'Banking - Economic Policy': ['SSBK12'],
    'Banking - Financial Institutions & Consumer Protection': ['SSBK08'],
    'Banking - Housing, Transportation & Community Development': ['SSBK09'],
    'Banking - Securities, Insurance & Investment': ['SSBK04'], 'Commerce (full)': ['SSCM'],
    'Commerce - Consumer Protection, Technology & Data Privacy': ['SSCM35'], 'Energy & Commerce (full)': ['HSIF'],
    'Energy & Commerce - Commerce, Manufacturing & Trade': ['HSIF17'], 'Finance (full)': ['SSFI'],
    'Finance - Social Security, Pensions & Family Policy': ['SSFI02'], 'Finance - Taxation & IRS Oversight': ['SSFI11'],
    'Financial Services (full)': ['HSBA'], 'Financial Services - Capital Markets': ['HSBA16'],
    'Financial Services - Digital Assets, FinTech & AI': ['HSBA21'], 'Financial Services - Financial Institutions': ['HSBA20'],
    'Financial Services - Housing & Insurance': ['HSBA04'],
    'Financial Services - National Security & Illicit Finance': ['HSBA10'],
    'Financial Services - Oversight & Investigations': ['HSBA09'], 'Joint Committee on Taxation': ['JSTX'],
    'Joint Economic Committee': ['JSEC'], 'Judiciary (full)': ['HSJU', 'SSJU'], 'Oversight & Govt Reform (full)': ['HSGO'],
    'Oversight - Cybersecurity, IT & Govt Innovation': ['HSGO12'],
    'Oversight - Economic Growth, Energy Policy & Regulatory Affairs': ['HSGO05'],
    'Oversight - Health Care & Financial Services': ['HSGO27'], 'Small Business (full)': ['HSSM', 'SSSB'],
    'Small Business - Economic Growth, Tax & Capital Access': ['HSSM27'],
    'Small Business - Oversight, Investigations & Regulations': ['HSSM24'],
    'Small Business - Rural Development, Energy & Supply Chains': ['HSSM21'], "Veterans' Affairs (full)": ['HSVR', 'SSVA'],
    'Ways & Means (full)': ['HSWM'], 'Ways & Means - Oversight': ['HSWM06'], 'Ways & Means - Tax': ['HSWM05'],
    'Ways & Means - Trade': ['HSWM04'],
}


class Refused(RuntimeError):
    """The run cannot write now (workbook open, lock held); nothing was written.  Exit 2."""


def log(msg):
    print('[roster] ' + msg, flush=True)


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha_file(p):
    with open(p, 'rb') as f:
        return sha_bytes(f.read())


def fetch(url, timeout=120, accept=None):
    h = dict(UA)
    if accept:
        h['Accept'] = accept
    last = None
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
                return r.read()
        except Exception as e:    # network: retried, then raised with the URL
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError('fetch failed: %s (%s)' % (url, last))


def load_yaml(p):
    import yaml
    with open(p, encoding='utf-8') as f:
        return yaml.load(f, Loader=getattr(yaml, 'CSafeLoader', yaml.SafeLoader))


def _json_style(path):
    """The byte style an existing JSON artifact was written in, so a rewrite keeps it.  ~250 readers open JSON with no
    encoding= (cp1252 on this box): a file that was ASCII-escaped must stay ASCII-escaped or accented names garble."""
    try:
        with open(path, 'rb') as f:
            b = f.read()
    except FileNotFoundError:
        return {'ascii': True, 'indent': 1, 'newline': True}       # new files: safe under every reader
    head = b[:4096]
    return {'ascii': all(x < 128 for x in b), 'indent': 1 if b'\n' in head.rstrip(b'\n') else None,
            'newline': b.endswith(b'\n')}


def dump_json(path, doc):
    style = _json_style(path)
    text = json.dumps(doc, ensure_ascii=style['ascii'], indent=style['indent']) + ('\n' if style['newline'] else '')
    tmp = path + '.%d.tmp' % os.getpid()
    with open(tmp, 'w', encoding='ascii' if style['ascii'] else 'utf-8', newline='\n') as f:
        f.write(text)
    os.replace(tmp, path)


# ------------------------------------------------------------------------------------------------- 1. snapshot

def stage_snapshot(sha, run_dir, staged=None):
    """Snapshot `sha` validated in the run directory (the installed one, a staged copy, or a fresh download).  Nothing under
    data/staffer_network/reference/ changes here: a dry run never moves what roster.snapshot_dir() resolves."""
    from staffer_network.source_manifest import seal_source_manifest, validate_manifest_files
    installed = os.path.join(roster.reference_dir(), sha)
    if os.path.isdir(installed):
        validate_manifest_files(json.load(open(os.path.join(installed, 'source_manifest.json'), encoding='utf-8')), installed)
        return installed
    out = os.path.join(run_dir, 'snapshot', sha)
    os.makedirs(out)
    if staged:
        for n in FILES + ['source_manifest.json']:
            shutil.copy2(os.path.join(staged, n), os.path.join(out, n))
        m = json.load(open(os.path.join(out, 'source_manifest.json'), encoding='utf-8'))
        if m.get('crawl_watermark') != 'git:' + sha:
            raise RuntimeError('staged manifest is for %s, not %s' % (m.get('crawl_watermark'), sha))
    else:
        meta = json.loads(fetch('https://api.github.com/repos/%s/commits/%s' % (REPO, sha), accept='application/vnd.github+json'))
        commit_iso = meta['commit']['committer']['date'].replace('Z', '+00:00')
        entries = []
        for n in FILES:
            body = fetch(URL_RAW % (sha, n), timeout=180)
            with open(os.path.join(out, n), 'wb') as f:
                f.write(body)
            entries.append({'path': n, 'sha256': sha_bytes(body), 'bytes': len(body)})
            log('  downloaded %-36s %9d B  %s' % (n, len(body), entries[-1]['sha256'][:16]))
        base = json.load(open(os.path.join(roster.snapshot_dir(), 'source_manifest.json'), encoding='utf-8'))
        m = {k: v for k, v in base.items() if k != 'manifest_sha256'}
        stamp = now_iso()
        m.update({'terms_uri': 'https://github.com/%s/blob/%s/LICENSE' % (REPO, sha), 'terms_checked_at': stamp,
                  'crawl_watermark': 'git:' + sha, 'source_schema_version': 'upstream-git-' + sha,
                  'source_as_of': commit_iso, 'collected_at': stamp, 'collector_version': 'fec/collect/refresh_roster.py',
                  'files': entries})
        with open(os.path.join(out, 'source_manifest.json'), 'w', encoding='utf-8') as f:
            f.write(json.dumps(seal_source_manifest(m), indent=2, ensure_ascii=False) + '\n')
    validate_manifest_files(json.load(open(os.path.join(out, 'source_manifest.json'), encoding='utf-8')), out)
    log('staged snapshot %s (manifest validated) in %s' % (sha, out))
    return out


def install_snapshot(staged_dir):
    """Copy a validated staged snapshot into the reference directory (a new <sha> directory; never mutates an old one).
    The CURRENT pointer is written by the caller once everything else has landed."""
    from staffer_network.source_manifest import validate_manifest_files
    sha = os.path.basename(staged_dir)
    final = os.path.join(roster.reference_dir(), sha)
    if os.path.normpath(staged_dir) == os.path.normpath(final):
        return final
    if not os.path.exists(roster.pointer_path()):      # pin today's snapshot before a second directory appears
        today = os.path.basename(roster.snapshot_dir())
        with open(roster.pointer_path(), 'w', encoding='utf-8', newline='\n') as f:
            f.write(today + '\n')
    tmp = final + '.installing'
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(staged_dir, tmp)
    validate_manifest_files(json.load(open(os.path.join(tmp, 'source_manifest.json'), encoding='utf-8')), tmp)
    os.replace(tmp, final)
    log('installed snapshot %s' % sha)
    return final


# ------------------------------------------------------------------------------------------------- roster building

def display_name(p):
    b = p['id']['bioguide']
    if b in DISPLAY_NAME:
        return DISPLAY_NAME[b]
    n = p['name']
    full = n.get('official_full')
    if full:
        toks = [t for t in full.replace(',', ' ').split() if t.lower() not in SUFFIXES and not (len(t) == 2 and t.endswith('.'))
                and not t.startswith('"')]
        if len(toks) >= 2:
            return ' '.join(toks)
    return '%s %s' % (n.get('nickname') or n['first'], n['last'])


def _chamber(t):
    return 'Senate' if t['type'] == 'sen' else 'House'


def person_record(p, name):
    t = p['terms'][-1]
    return {
        'member': name, 'bioguide': p['id']['bioguide'], 'chamber': _chamber(t), 'state': t['state'],
        'district': t.get('district') if t['type'] == 'rep' else None, 'party': t.get('party'),
        'term_start': t['start'], 'term_end': t['end'], 'how': t.get('how'),
        'official_full': p['name'].get('official_full'), 'fec_ids': list(p['id'].get('fec') or []),
        'terms': [{'chamber': 'senate' if x['type'] == 'sen' else 'house', 'start': x['start'], 'end': x['end'],
                   'state': x['state'], 'district': x.get('district')} for x in p['terms']],
    }


def scope_people(current):
    return {p['id']['bioguide']: p for p in current if roster.in_scope(p['terms'][-1]['type'], p['terms'][-1]['state'])}


def roster_doc(members, departed, snap_dir):
    man = json.load(open(os.path.join(snap_dir, 'source_manifest.json'), encoding='utf-8'))
    members = sorted(members, key=lambda m: m['member'])
    counts = {'House': sum(m['chamber'] == 'House' for m in members), 'Senate': sum(m['chamber'] == 'Senate' for m in members)}
    counts['total'] = len(members)
    return {
        'schema': roster.SCHEMA, 'built_at': now_iso(), 'as_of': man.get('source_as_of'),
        'source': {'repository': REPO, 'commit': os.path.basename(snap_dir), 'manifest_sha256': man.get('manifest_sha256'),
                   'snapshot_dir': os.path.relpath(snap_dir, ROOT).replace('\\', '/')},
        'scope': {'rule': 'senators, voting representatives, and the DC and Puerto Rico delegates; '
                          'the VI, AS, GU and MP delegates are outside the model (fec/roster.py:in_scope)',
                  'delegates_in': sorted(roster.DELEGATES_IN), 'territories_out': sorted(roster.TERRITORIES_OUT)},
        'counts': counts, 'members': members, 'departed': departed,
    }


def bootstrap():
    """roster.json for the roster that is live NOW (congestion.json names), from the installed snapshot."""
    snap = roster.snapshot_dir()
    cur = load_yaml(os.path.join(snap, 'legislators-current.yaml'))
    hist = load_yaml(os.path.join(snap, 'legislators-historical.yaml'))
    by = {p['id']['bioguide']: p for p in hist}
    by.update({p['id']['bioguide']: p for p in cur})
    cong = json.load(open(CONGESTION, encoding='utf-8'))
    members = []
    for row in cong:
        p = by.get(row['bioguide'])
        if p is None:
            raise RuntimeError('bootstrap: %s (%s) is in neither legislators file' % (row['member'], row['bioguide']))
        members.append(person_record(p, row['member']))
    doc = roster_doc(members, [], snap)
    dump_json(roster.path(), doc)
    log('bootstrap: wrote %s with %d members %s from snapshot %s' % (roster.path(), len(members), doc['counts'],
                                                                     os.path.basename(snap)))


# ------------------------------------------------------------------------------------------------- 2. cross-check

def clerk_set(xml_bytes):
    r = ET.fromstring(xml_bytes)
    out = {}
    for m in r.findall('members/member'):
        b = (m.findtext('member-info/bioguideID') or '').strip()
        se = m.find('member-info/state')
        st = se.get('postal-code') if se is not None else None     # statedistrict uses AQ for American Samoa; postal-code is AS
        sworn = m.find('member-info/sworn-date')
        if b and roster.in_scope('rep', st):
            out[b] = {'statedistrict': m.findtext('statedistrict'), 'sworn': sworn.get('date') if sworn is not None else None,
                      'committees': sorted({'HS' + c.get('comcode')[:2] + ('' if c.get('comcode').endswith('00') else c.get('comcode')[2:])
                                            for c in m.findall('committee-assignments/committee') if c.get('comcode')}
                                           | {'HS' + c.get('subcomcode') for c in m.findall('committee-assignments/subcommittee')
                                              if c.get('subcomcode')})}
    return out, r.get('publish-date')


def senate_set(xml_bytes):
    r = ET.fromstring(xml_bytes)
    return {m.findtext('bioguide_id').strip(): m.findtext('state') for m in r.findall('member') if m.findtext('bioguide_id')}


def read_csv_bytes(b):
    return list(csv.DictReader(io.StringIO(b.decode('utf-8-sig'))))


def cross_check(people, clerk, senate, csv_rows):
    """-> list of human-readable disagreements (empty = the three official sources and the csv export agree)."""
    bad = []
    house = {b for b, p in people.items() if p['terms'][-1]['type'] == 'rep'}
    sen = {b for b, p in people.items() if p['terms'][-1]['type'] == 'sen'}
    for label, a, b in (('House snapshot vs clerk.house.gov', house, set(clerk)), ('Senate snapshot vs senate.gov', sen, set(senate))):
        if a != b:
            bad.append('%s: snapshot-only %s, official-only %s' % (label, sorted(a - b), sorted(b - a)))
    rows = {r['bioguide_id']: r for r in csv_rows if roster.in_scope(r['type'], r['state'])}
    if set(rows) != set(people):
        bad.append('legislators-current.csv vs snapshot: csv-only %s, snapshot-only %s' % (sorted(set(rows) - set(people)),
                                                                                         sorted(set(people) - set(rows))))
    for b in set(rows) & set(people):
        ids = sorted(x for x in (rows[b]['fec_ids'] or '').split(',') if x)
        if ids != sorted(people[b]['id'].get('fec') or []):
            bad.append('legislators-current.csv fec_ids for %s %s != snapshot %s' % (b, ids, sorted(people[b]['id'].get('fec') or [])))
    return bad


# ------------------------------------------------------------------------------------------------- 3. delta

def compute_delta(installed, people, hist_by):
    have = {m['bioguide']: m for m in installed['members']}
    names = {m['member'] for m in installed['members']}
    d = {'departures': [], 'arrivals': [], 'party_changes': [], 'name_changes': [], 'seat_changes': []}
    for b, m in sorted(have.items(), key=lambda x: x[1]['member']):
        if b not in people:
            p = hist_by.get(b)
            end = p['terms'][-1]['end'] if p else None
            d['departures'].append({'member': m['member'], 'bioguide': b, 'departed_on': end,
                                    'reason': ('last term ended %s (congress-legislators)' % end) if end else
                                              'no longer in congress-legislators current; end date unknown',
                                    'record': person_record(p, m['member']) if p else m})
    for b, p in sorted(people.items()):
        if b in have:
            m, t = have[b], p['terms'][-1]
            if (t.get('party') or None) != (m.get('party') or None):
                d['party_changes'].append({'member': m['member'], 'bioguide': b, 'from': m.get('party'), 'to': t.get('party')})
            if (p['name'].get('official_full') or None) != (m.get('official_full') or None) and m.get('official_full') is not None:
                d['name_changes'].append({'member': m['member'], 'bioguide': b, 'from': m.get('official_full'),
                                          'to': p['name'].get('official_full')})
            if (_chamber(t), t['state'], t.get('district') if t['type'] == 'rep' else None) != (m['chamber'], m['state'], m.get('district')):
                d['seat_changes'].append({'member': m['member'], 'bioguide': b,
                                          'from': [m['chamber'], m['state'], m.get('district')],
                                          'to': [_chamber(t), t['state'], t.get('district')]})
        else:
            name = display_name(p)
            if name in names:
                raise RuntimeError('arrival %s would take the display name %r of a sitting member; Paul names it' % (b, name))
            d['arrivals'].append(person_record(p, name))
    return d


def fec_fence(installed_csv, new_csv, approvals, arrivals):
    """-> (fec_ids to WRITE per bioguide, held review items).  An id set changes only when Paul approved exactly it."""
    old = {r['bioguide_id']: r['fec_ids'] for r in installed_csv}
    ok = {(a['bioguide'], ','.join(sorted(a['fec_ids']))) for a in approvals.get('approved', [])}
    write, held = {}, []
    arriving = {a['bioguide'] for a in arrivals}
    for r in new_csv:
        b, ids = r['bioguide_id'], r['fec_ids'] or ''
        before = old.get(b, '')
        if b not in old and b not in arriving:          # a row the installed file never had (e.g. a new territorial delegate)
            if ids and (b, ','.join(sorted(filter(None, ids.split(','))))) not in ok:
                write[b] = ''
                held.append({'kind': 'fec_ids', 'bioguide': b, 'member_or_new': r.get('full_name') or r.get('last_name'),
                             'installed': '', 'upstream': ids, 'why': "new row outside the roster; ids wait for Paul's OK"})
            continue
        if sorted(filter(None, ids.split(','))) == sorted(filter(None, before.split(','))):
            write[b] = ids
        elif (b, ','.join(sorted(filter(None, ids.split(','))))) in ok:
            write[b] = ids
        else:
            write[b] = before
            held.append({'kind': 'fec_ids', 'bioguide': b, 'member_or_new': r.get('full_name') or r.get('last_name'),
                         'installed': before, 'upstream': ids,
                         'why': 'changes PAC money attribution (refresh_fec_money.candidate_map); needs Paul\'s per-change OK '
                                'in data/engine/roster_fec_approvals.json'})
    return write, held


# ------------------------------------------------------------------------------------------------- 4. workbook

def census_rows():
    import openpyxl
    ws = openpyxl.load_workbook(CENSUS, read_only=True)['State_and_CD']
    it = ws.iter_rows(values_only=True)
    head = list(next(it))
    rows = [dict(zip(head, r)) for r in it]
    return head, rows


def seat_row(rows, chamber, state, district):
    if chamber == 'Senate':
        hit = [r for r in rows if r['Geography_Level'] == 'State' and r['State_Abbr'] == state]
    else:
        code = {'DC': '98', 'PR': '98'}.get(state) if not district else '%02d' % int(district)
        code = code or '00'
        hit = [r for r in rows if r['Geography_Level'] == 'Congressional District' and r['State_Abbr'] == state
               and str(r['District_Code']).zfill(2) == code]
    if len(hit) != 1:
        raise RuntimeError('census file has %d rows for %s %s %s' % (len(hit), chamber, state, district))
    return hit[0]


def workbook_writable(path):
    owner = os.path.join(os.path.dirname(path), '~$' + os.path.basename(path))
    if os.path.exists(owner):
        raise Refused('the workbook is open in Excel (%s); retry later' % owner)
    try:
        with open(path, 'r+b'):
            pass
    except PermissionError as e:
        raise Refused('the workbook cannot be opened for writing (%s); retry later' % e)


def plan_workbook(wb_path, delta, committee_rows, people, snap_dir):
    """Edit a copy of the workbook; returns (temp path, report).  Raises before touching the live file."""
    import openpyxl
    import workbook_rows as WR
    head, crow = census_rows()
    ro = openpyxl.load_workbook(wb_path, read_only=True)
    ra = [r[0] for r in ro['Rank & Allocation'].iter_rows(min_row=3, max_row=roster.FIRST_ROW['Rank & Allocation'] + 2000,
                                                           max_col=1, values_only=True)]
    while ra and ra[-1] is None:
        ra.pop()
    if None in ra or ra != sorted(ra):
        raise RuntimeError('Rank & Allocation names are not one sorted block; refusing to place rows')
    lists = {'Blended Model': list(ra)}          # Blended Model col A is ='Rank & Allocation'!A<r>: positional by construction
    misordered = {}
    for sheet, col in ORDERED_SHEETS:
        if col is None:
            continue
        f = roster.FIRST_ROW[sheet]
        ci = ord(col) - 64
        got = [r[0] for r in ro[sheet].iter_rows(min_row=f, max_row=f + len(ra) - 1, min_col=ci, max_col=ci, values_only=True)]
        if sorted(got, key=str) != sorted(ra) or len(set(got)) != len(got):
            raise RuntimeError('%s does not hold the Rank & Allocation member set in rows %d..%d' % (sheet, f, f + len(ra) - 1))
        if got != ra:   # same members, another order (Press sheets file 'Andre Carson' by accent-blind order): keep, report
            misordered[sheet] = [(i, a, b) for i, (a, b) in enumerate(zip(got, ra)) if a != b]
            log('note: %s orders %d members differently from Rank & Allocation (pre-existing; positional formulas pair '
                'those rows across sheets): %s' % (sheet, len(misordered[sheet]), misordered[sheet][:6]))
        lists[sheet] = got
    dr = [r for r in ro['Demographic Factors - Raw'].iter_rows(min_row=1, max_row=roster.FIRST_ROW['Demographic Factors - Raw'] + len(ra) - 1,
                                                               values_only=True)]
    sub_head = next(ro['Sub Layer - Raw'].iter_rows(min_row=2, max_row=2, values_only=True))
    demo_norm = {}
    for ci, h in enumerate(sub_head):
        if h in dr[0] and ci >= 2:
            vals = [row[dr[0].index(h)] for row in dr[1:] if row[0] and row[dr[0].index(h)] is not None]
            demo_norm[WR.column_letter(ci + 1)] = (min(vals), max(vals), h)
    if len(demo_norm) != 11:
        raise RuntimeError('Sub Layer - Raw demographic columns: expected 11 named like Demographic Factors - Raw, found %s' % sorted(demo_norm))
    cg = [r for r in ro['Congestion'].iter_rows(min_row=3, max_row=3 + len(ra) + 50, max_col=3, values_only=True)]
    cg_names = [r[0] for r in cg if r[0]]
    cd = [r for r in ro['Committee Data'].iter_rows(min_row=2, max_row=420, max_col=5, values_only=True)]
    ro.close()

    book = WR.Book(wb_path)
    report = {'deleted': [], 'inserted': [], 'committee': [], 'misordered_sheets': {k: len(v) for k, v in misordered.items()}}
    order = list(ra)
    for dep in delta['departures']:
        name = dep['member']
        if name not in order:
            raise RuntimeError('departing %s is not in Rank & Allocation' % name)
        k = order.index(name)
        own = {s: lists[s].index(name) for s, _c in ORDERED_SHEETS}   # each sheet drops the departing member's OWN row
        book.apply_group([WR.Op(s, 'delete', roster.FIRST_ROW[s] + own[s]) for s, _c in ORDERED_SHEETS])
        for s, _c in ORDERED_SHEETS:
            lists[s].pop(own[s])
        order.pop(k)
        if name in cg_names:
            book.apply_group([WR.Op('Congestion', 'delete', 3 + cg_names.index(name))])
            cg_names.remove(name)
        report['deleted'].append(name)
    seat_ids = {}
    for arr in sorted(delta['arrivals'], key=lambda a: a['member']):
        name = arr['member']
        import bisect
        k = bisect.bisect(order, name)
        end = k == len(order)
        # the SAME block index in every sheet, so the positional formulas (Sub Layer <- Legislative/Press, Primary <- Sub Layer,
        # Blended Model <- Rank & Allocation) pair the arrival with itself
        book.apply_group([WR.Op(s, 'insert', roster.FIRST_ROW[s] + k, 1, grow_end=end) for s, _c in ORDERED_SHEETS])
        order.insert(k, name)
        for s, _c in ORDERED_SHEETS:
            lists[s].insert(k, name)
        seat = seat_row(crow, arr['chamber'], arr['state'], arr['district'])
        label = seat['Geography_Name']
        for sheet, _col in ORDERED_SHEETS:
            r = roster.FIRST_ROW[sheet] + k
            book.clone_row(sheet, r - 1 if k > 0 else r + 1, r)
        party = arr['party']
        vals = {
            'Rank & Allocation': {'A': name, 'B': label},
            'IM Allocation': {'A': name},
            'Legislative Score - Raw': {'A': name, 'B': label}, 'Legislative Score - Normalized': {'A': name, 'B': label},
            'Primary Layer': {'A': label, 'B': name}, 'Primary Layer - Normalized': {'A': label, 'B': name},
            'Sub Layer - Raw': {'A': label, 'B': name}, 'Sub Layer - Normalized': {'A': label, 'B': name},
            'Demographic Factors - Raw': dict({'A': name, 'B': label},
                                              **{WR.column_letter(i + 3): seat[h] for i, h in enumerate(DEMO_COLUMNS)}),
            'Demographic - Normalized': {'A': name, 'B': label},
            'Press Release - Raw': {'A': name, 'B': arr['chamber'], 'C': arr['state'], 'D': party},
            'Press Release - Normalized': {'A': name, 'B': arr['chamber'], 'C': arr['state'], 'D': party},
        }
        for sheet, cells in vals.items():
            r = roster.FIRST_ROW[sheet] + k
            for col, v in cells.items():
                if _is_formula(book, sheet, '%s%d' % (col, r)):
                    continue                     # the cloned row computes it
                book.set_value(sheet, '%s%d' % (col, r), v)
            for col in ZERO_FILL.get(sheet, ()):
                for c in _literal_cols(book, sheet, r, col):
                    book.set_value(sheet, '%s%d' % (c, r), 0)
        # Sub Layer - Raw V..AF are pasted min-max normalised seat demographics (x - min) / (max - min) over Demographic Factors
        # - Raw (exact for every current member, checked 2026-10-20); the arrival gets the same, against the current pool
        r = roster.FIRST_ROW['Sub Layer - Raw'] + k
        for col, (lo, hi, head_name) in demo_norm.items():
            x = seat[head_name]
            book.set_value('Sub Layer - Raw', '%s%d' % (col, r), min(1.0, max(0.0, (x - lo) / (hi - lo))) if hi > lo and x is not None else 0)
        # Congestion sheet: own order (total, descending); an arrival has no money yet -> bottom of the block
        cr = 3 + len(cg_names)
        book.apply_group([WR.Op('Congestion', 'insert', cr, 1, grow_end=True)])
        book.clone_row('Congestion', cr - 1, cr)
        for col, v in (('A', name), ('B', arr['bioguide']), ('C', arr['chamber'])):
            book.set_value('Congestion', '%s%d' % (col, cr), v)
        for c in _literal_cols(book, 'Congestion', cr, 'D'):
            book.set_value('Congestion', '%s%d' % (c, cr), 0)
        cg_names.append(name)
        report['inserted'].append({'member': name, 'block_row': k, 'seat': label})
    # Committee Data: name-keyed rows 2..420 (Rank & Allocation col E looks up $A$2:$A$420)
    cd_names = [r[0] for r in cd]
    for dep in delta['departures']:
        if dep['member'] in cd_names:
            i = cd_names.index(dep['member'])
            book.apply_group([WR.Op('Committee Data', 'delete', 2 + i)])
            cd_names.pop(i)
            cd.pop(i)
            report['committee'].append({'op': 'DELETE_ROW', 'member': dep['member']})
    for name, seats in committee_rows.items():
        if not seats:
            continue
        if name in cd_names:
            raise RuntimeError('Committee Data already has a row for arriving %s' % name)
        free = next((i for i, r in enumerate(cd) if not r[0]), None)
        if free is None and len(cd) < 419:
            free = len(cd)
        if free is None:
            raise RuntimeError('Committee Data has no free row inside the $A$2:$A$420 lookup range')
        r = 2 + free
        if r - 1 >= len(cd):
            book.clone_row('Committee Data', r - 1, r)
        book.set_value('Committee Data', 'A%d' % r, name)
        for col, v in zip('BCD', seats['score_B_C_D']):
            book.set_value('Committee Data', '%s%d' % (col, r), v)
        book.set_value('Committee Data', 'E%d' % r, seats['seats_E'])
        while len(cd) <= free:
            cd.append((None,) * 5)
        cd[free] = (name,) + tuple(seats['score_B_C_D']) + (seats['seats_E'],)
        cd_names = [x[0] for x in cd]
        report['committee'].append({'op': 'ADD_ROW', 'member': name, 'row': r, 'seats_E': seats['seats_E'],
                                    'source': seats.get('source')})
    tmp = os.path.join(os.path.dirname(wb_path), '.roster-refresh.%d.tmp.xlsx' % os.getpid())   # openpyxl verifies it: keep .xlsx
    book.save(tmp)
    report['order'] = order
    report['sheet_orders'] = lists
    return tmp, report


DEMO_COLUMNS = ['OwnerOcc_Total', 'OwnerOcc_With_Mortgage', 'Pct_OwnerOcc_With_Mortgage', 'Mortgaged_Units_Total',
                'Mortgaged_CostBurden_30plus', 'Pct_Mortgaged_CostBurden_30plus', 'Mortgaged_HH_Total', 'Mortgaged_HH_Age_35_64',
                'Pct_Mortgaged_HH_Age_35_64', 'Median_Household_Income', 'Per_Capita_Income',
                'Civilian_Employed_16plus', 'Employed_Finance_RealEstate', 'Pct_Finance_RealEstate', 'Workers_Total_B08126',
                'Workers_Finance_DroveAlone', 'Pct_Finance_DroveAlone', 'Pop_1yr_and_over', 'Moved_From_Abroad', 'Pct_Moved_From_Abroad',
                'Median_Age', 'Poverty_Universe', 'Below_Poverty', 'Poverty_Rate_Pct', 'CBP_Establishments']
# Member-level literal columns that start at zero for an arrival (no recorded press or legislative events yet): every literal
# cell of the cloned row from this column on.  Primary Layer C (Electoral Integrity) and other unknowns stay BLANK.
ZERO_FILL = {'Press Release - Raw': ('E',), 'Legislative Score - Raw': ('C',), 'Legislative Score - Normalized': ('C',),
             'Sub Layer - Raw': ('C',)}


def _is_formula(book, sheet, addr):
    import workbook_rows as WR
    r = int(WR.CELL.fullmatch(addr).group(4))
    row = book._row(sheet, r)
    c = next((x for x in row.findall(WR.Q + 'c') if x.get('r') == addr), None) if row is not None else None
    return c is not None and c.find(WR.Q + 'f') is not None


def _literal_cols(book, sheet, r, from_col):
    """Columns (>= from_col) of row r that are literal (no formula) in the cloned row."""
    import workbook_rows as WR
    from openpyxl.utils.cell import column_index_from_string
    lo = column_index_from_string(from_col)
    row = book._row(sheet, r)
    out = []
    for c in row.findall(WR.Q + 'c'):
        col = WR.CELL.fullmatch(c.get('r')).group(2)
        if column_index_from_string(col) >= lo and c.find(WR.Q + 'f') is None:
            out.append(col)
    return out


def verify_workbook(tmp, order, committee_expect, sheet_orders=None):
    import openpyxl
    wb = openpyxl.load_workbook(tmp, read_only=True)
    n = len(order)
    for sheet, col in ORDERED_SHEETS:
        if col is None:
            continue
        f = roster.FIRST_ROW[sheet]
        ci = ord(col) - 64
        got = [r[0] for r in wb[sheet].iter_rows(min_row=f, max_row=f + n, min_col=ci, max_col=ci, values_only=True)]
        want = (sheet_orders or {}).get(sheet, order)
        if got[:n] != want or sorted(want) != sorted(order) or (got[n:] and got[n] not in (None, 'Weights')):
            raise RuntimeError('verify: %s block is not the new roster' % sheet)
    bm = [r[0] for r in wb['Blended Model'].iter_rows(min_row=3, max_row=3 + n, max_col=1, values_only=True)]
    want = ["='Rank & Allocation'!A%d" % (3 + i) for i in range(n)]
    if bm[:n] != want or (len(bm) > n and bm[n] is not None):
        raise RuntimeError('verify: Blended Model col A is not positional over the new roster')
    for sheet in ('Press Release - Raw', 'Press Release - Normalized', 'Demographic - Normalized'):
        if wb[sheet].cell(roster.FIRST_ROW[sheet] + n, 1).value != 'Weights':
            raise RuntimeError('verify: %s Weights footer is not right under the block' % sheet)
    cd = {r[0]: r[4] for r in wb['Committee Data'].iter_rows(min_row=2, max_row=420, max_col=5, values_only=True) if r[0]}
    for name, text in committee_expect.items():
        if text is None and name in cd:
            raise RuntimeError('verify: Committee Data still has %s' % name)
        if text is not None and cd.get(name) != text:
            raise RuntimeError('verify: Committee Data row for %s is %r' % (name, cd.get(name)))
    wb.close()


def upstream_committee_rows(arrivals, snap_dir, tiers):
    """Seat text for arrivals from committee-membership-current (tier per seat from the sheet's existing text)."""
    mm = load_yaml(os.path.join(snap_dir, 'committee-membership-current.yaml'))
    out = {}
    for a in arrivals:
        parts = []
        for seat, ids in SEAT_IDS.items():
            hit = [x for cid in ids for x in mm.get(cid, []) if x.get('bioguide') == a['bioguide']]
            if not hit:
                continue
            t = (hit[0].get('title') or '').lower()
            role = 'Vice' if 'vice' in t else 'Chair' if 'chair' in t else 'Ranking' if 'ranking' in t else ''
            parts.append('%s [%s%s]' % (seat, tiers[seat], '/' + role if role else ''))
        out[a['member']] = {'seats_E': ' | '.join(parts), 'score_B_C_D': None, 'source': 'committee-membership-current'} if parts else None
    return out


def sheet_tiers(wb_path):
    import openpyxl
    import re
    wb = openpyxl.load_workbook(wb_path, read_only=True)
    tiers = {}
    for r in wb['Committee Data'].iter_rows(min_row=2, max_row=420, max_col=5, values_only=True):
        for part in str(r[4] or '').split('|'):
            m = re.match(r'^(.*?)\s*\[([^\]/]+)', part.strip())
            if m:
                tiers.setdefault(m.group(1).strip(), m.group(2).strip())
    wb.close()
    return tiers


# ------------------------------------------------------------------------------------------------- 5. other artifacts

def _arrival_money_status(bioguide, fec_ids, no_upstream_ids):
    if (fec_ids or {}).get(bioguide):
        return 'NEW_MEMBER_AWAITING_FEC_MONEY_REFRESH'          # ids applied (Paul-approved): money comes with refresh_fec_money
    if bioguide in (no_upstream_ids or ()):
        return 'NEW_MEMBER_NO_FEC_ID'                 # e.g. an appointed senator who never filed: nothing to attribute
    return 'NEW_MEMBER_FEC_IDS_PENDING_PAUL_REVIEW'


def new_congestion(old, delta, fec_ids=None, no_upstream_ids=None):
    """Departures leave (their row stays in roster.json 'departed'); arrivals join with zero money.  The money itself comes from
    the FEC money refresh (refresh_fec_money.py), which attributes only through legislators.csv fec_ids."""
    gone = {d['bioguide'] for d in delta['departures']}
    out = [dict(r, money_source_status=_arrival_money_status(r['bioguide'], fec_ids, no_upstream_ids))
           if r.get('money_source_status') == 'NEW_MEMBER_FEC_IDS_PENDING_PAUL_REVIEW' else r
           for r in old if r['bioguide'] not in gone]   # an arrival whose ids Paul has since approved, or who has none
    zero = {'cu': 0, 'banks': 0, 'finserv': 0, 'receipts': 0, 'total': 0, 'saturation': 0.0, 'cu_pct': 0.0, 'banks_pct': 0.0,
            'finserv_pct': 0.0, 'cu_adj': 0.0, 'banks_adj': 0.0, 'finserv_adj': 0.0, 'industry_adj': 0.0, 'net_lean': 0.0}
    for a in delta['arrivals']:
        out.append(dict({'member': a['member'], 'bioguide': a['bioguide'], 'chamber': a['chamber']}, **zero,
                        money_source_status=_arrival_money_status(a['bioguide'], fec_ids, no_upstream_ids)))
    return out


def new_desk_src(old, delta, crow):
    gone = {d['member']: d for d in delta['departures']}
    doc = dict(old)
    doc['members'] = [m for m in old['members'] if m['name'] not in gone]
    doc['departed'] = list(old.get('departed') or []) + [
        dict(m, departed=gone[m['name']]['departed_on'], departed_reason=gone[m['name']]['reason'])
        for m in old['members'] if m['name'] in gone]
    for a in delta['arrivals']:
        s = seat_row(crow, a['chamber'], a['state'], a['district'])
        demo = {'pct_ownerocc_mort': s['Pct_OwnerOcc_With_Mortgage'], 'pct_costburden': s['Pct_Mortgaged_CostBurden_30plus'],
                'income': s['Median_Household_Income'], 'percap': s['Per_Capita_Income'], 'pct_finance': s['Pct_Finance_RealEstate'],
                'median_age': s['Median_Age'], 'poverty_rate': s['Poverty_Rate_Pct'], 'constituents': s['Pop_1yr_and_over'],
                'establishments': s['CBP_Establishments']}
        doc['members'].append({'name': a['member'], 'district': s['Geography_Name'], 'press_raw': 0.0, 'leg_raw': 0.0,
                               'comm_raw': 0.0, 'demo_raw': None, 'press_n': None, 'leg_n': None, 'comm_n': None, 'demo_n': None,
                               'score': None, 'rank': None, 'alloc': 0, 'coalition': None, 'chamber': a['chamber'],
                               'party': a['party'], 'demo': demo, 'committee': None, 'articles': [],
                               'thin_evidence': 'arrived %s; no recorded press, legislative or money history yet' % a['term_start']})
    doc['members'].sort(key=lambda m: m['name'])
    return doc


def write_csv(path, header, rows):
    try:                                         # keep the installed file's line endings (fec/legislators.csv is CRLF)
        with open(path, 'rb') as f:
            eol = '\r\n' if b'\r\n' in f.readline() else '\n'
    except FileNotFoundError:
        eol = '\r\n'
    tmp = path + '.%d.tmp' % os.getpid()
    with open(tmp, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=header, lineterminator=eol)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def backup(files, stamp):
    d = os.path.join(paths.BACKUPS, 'roster_refresh_' + stamp)
    os.makedirs(d, exist_ok=False)
    man = {'schema': 'roster-refresh-backup-v1', 'stamp': stamp, 'files': []}
    for p in files:          # the workbook is first in `files`
        if not os.path.exists(p):
            man['files'].append({'path': p, 'absent': True})
            continue
        dst = os.path.join(d, os.path.basename(p))
        shutil.copy2(p, dst)
        if sha_file(dst) != sha_file(p):
            raise RuntimeError('backup copy of %s does not match' % p)
        man['files'].append({'path': p, 'backup': dst, 'sha256': sha_file(p), 'bytes': os.path.getsize(p)})
        log('  backed up %s' % p)
    dump_json(os.path.join(d, 'manifest.json'), man)
    return d, man


def restore(man):
    for f in man['files']:
        if f.get('absent'):
            if os.path.exists(f['path']):
                os.remove(f['path'])
        else:
            shutil.copy2(f['backup'], f['path'])
    log('RESTORED every file from the backup')


# ------------------------------------------------------------------------------------------------- main

def run(a):
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    run_dir = os.path.join(ROOT, 'audits', 'roster_refresh', stamp)
    os.makedirs(run_dir, exist_ok=True)
    rec = {'schema': 'roster-refresh-receipt-v1', 'started_at': now_iso(), 'run_dir': os.path.relpath(run_dir, ROOT),
           'apply': bool(a.apply), 'status': None}
    installed = roster.load()
    rec['installed'] = {'count': len(installed['members']), 'commit': installed['source']['commit']}
    log('installed roster: %d members, snapshot %s' % (len(installed['members']), installed['source']['commit'][:12]))
    # 1. snapshot
    try:
        if a.snapshot_dir:
            sha = os.path.basename(os.path.normpath(a.snapshot_dir))
            snap = stage_snapshot(sha, run_dir, staged=a.snapshot_dir)
        else:
            sha = json.loads(fetch(URL_HEAD, accept='application/vnd.github+json'))['sha']
            log('upstream %s main = %s' % (REPO, sha))
            snap = stage_snapshot(sha, run_dir)
    except Exception as e:
        age = (datetime.datetime.now().astimezone() - datetime.datetime.fromisoformat(installed['built_at'])).days
        rec.update(status='SOURCE_UNAVAILABLE', error=str(e), installed_age_days=age)
        log('!! upstream unavailable: %s (installed roster is %d days old)' % (e, age))
        finish(rec, run_dir)
        return 1 if age > STALE_DAYS else 0
    rec['snapshot'] = {'commit': sha, 'dir': os.path.relpath(snap, ROOT)}
    cur = load_yaml(os.path.join(snap, 'legislators-current.yaml'))
    hist = load_yaml(os.path.join(snap, 'legislators-historical.yaml'))
    hist_by = {p['id']['bioguide']: p for p in hist}
    people = scope_people(cur)
    # 2. official sources
    src = {}
    for key, url, fname in (('clerk', URL_CLERK, 'clerk_MemberData.xml'), ('senate', URL_SENATE, 'senate_cfm.xml'),
                            ('csv', URL_CSV, 'legislators-current.csv')):
        if a.sources_dir:
            src[key] = open(os.path.join(a.sources_dir, fname), 'rb').read()
        else:
            src[key] = fetch(url)
        with open(os.path.join(run_dir, fname), 'wb') as f:
            f.write(src[key])
    clerk, clerk_date = clerk_set(src['clerk'])
    senate = senate_set(src['senate'])
    csv_rows = read_csv_bytes(src['csv'])
    rec['sources'] = {'clerk_publish_date': clerk_date, 'clerk_in_scope': len(clerk), 'senate': len(senate),
                      'snapshot_in_scope': len(people), 'csv_rows': len(csv_rows),
                      'sha256': {k: sha_bytes(v) for k, v in src.items()}}
    log('counts: snapshot %d (House %d, Senate %d) | clerk %d (published %s) | senate %d | csv %d rows' % (
        len(people), sum(p['terms'][-1]['type'] == 'rep' for p in people.values()),
        sum(p['terms'][-1]['type'] == 'sen' for p in people.values()), len(clerk), clerk_date, len(senate), len(csv_rows)))
    bad = cross_check(people, clerk, senate, csv_rows)
    if bad:
        rec.update(status='HELD_SOURCES_DISAGREE', disagreements=bad)
        for b in bad:
            log('!! ' + b)
        log('HELD: the official sources disagree; nothing applied (retry on a later run)')
        finish(rec, run_dir)
        return 0
    log('cross-check: snapshot, clerk.house.gov, senate.gov and the csv export agree (%d members)' % len(people))
    # 3. delta
    delta = compute_delta(installed, people, hist_by)
    rec['delta'] = {k: v for k, v in delta.items() if k != 'arrivals'}
    rec['delta']['arrivals'] = [{k: x[k] for k in ('member', 'bioguide', 'chamber', 'state', 'district', 'party', 'term_start', 'how')}
                                for x in delta['arrivals']]
    for k, v in delta.items():
        for x in v:
            log('delta %-13s %s' % (k, json.dumps({kk: x.get(kk) for kk in ('member', 'bioguide', 'departed_on', 'term_start', 'from', 'to')
                                                   if x.get(kk) is not None}, ensure_ascii=False)))
    approvals = json.load(open(APPROVALS, encoding='utf-8')) if os.path.exists(APPROVALS) else {'approved': []}
    old_csv = list(csv.DictReader(open(LEG_CSV, encoding='utf-8-sig')))
    write_ids, held = fec_fence(old_csv, csv_rows, approvals, delta['arrivals'])
    before_ids = {r['bioguide_id']: r['fec_ids'] for r in old_csv}
    rec['fec_approvals_applied'] = [a for a in approvals.get('approved', [])
                                    if write_ids.get(a['bioguide']) is not None
                                    and sorted(filter(None, write_ids[a['bioguide']].split(','))) == sorted(a['fec_ids'])
                                    and write_ids[a['bioguide']] != before_ids.get(a['bioguide'], '')]
    for ok in rec['fec_approvals_applied']:     # not `a`: that name is the parsed argv
        log('FEC ids applied under Paul\'s OK: %s %s -> %s (%s)' % (ok['bioguide'], before_ids.get(ok['bioguide'], ''),
                                                                    ','.join(ok['fec_ids']), ok.get('ruling', '')[:80]))
    held += [dict(x, kind='name_change', why='display names are join keys; Paul renames') for x in delta['name_changes']]
    held += [dict(x, kind='seat_change', why='a seat change re-keys district rows; Paul reviews') for x in delta['seat_changes']]
    rec['held_for_paul'] = held
    for h in held:
        log('HELD for Paul: %s %s' % (h['kind'], json.dumps({k: h.get(k) for k in ('bioguide', 'member', 'installed', 'upstream', 'from', 'to')
                                                              if h.get(k) is not None}, ensure_ascii=False)))
    structural = delta['departures'] or delta['arrivals']
    csv_header = list(csv_rows[0].keys())
    new_csv = [dict(r, fec_ids=write_ids.get(r['bioguide_id'], r['fec_ids'])) for r in csv_rows]
    csv_changed = [ {k: r[k] for k in csv_header} for r in new_csv] != [{k: r.get(k) for k in csv_header} for r in old_csv]
    if not structural and not csv_changed and not delta['party_changes'] and sha == installed['source']['commit']:
        rec['status'] = 'NO_CHANGE'
        log('no change')
        finish(rec, run_dir, held)
        return 0
    if not a.apply:
        rec['status'] = 'DRY_RUN'
        finish(rec, run_dir, held)
        return 0
    # 4. apply
    if structural:
        workbook_writable(paths.WORKBOOK)
    files = [paths.WORKBOOK, LEG_CSV, CONGESTION, DESK_SRC, roster.path(), roster.pointer_path()]
    bdir, man = backup(files, stamp)
    rec['backup'] = os.path.relpath(bdir, ROOT)
    try:
        snap = install_snapshot(snap)
        rec['snapshot']['dir'] = os.path.relpath(snap, ROOT)
        if structural:
            committee = {a_['member']: None for a_ in delta['arrivals']}
            committee.update({k: v for k, v in upstream_committee_rows(delta['arrivals'], snap, sheet_tiers(paths.WORKBOOK)).items() if v})
            if a.committee_rows:
                for op in json.load(open(a.committee_rows, encoding='utf-8'))['committee_data_sheet']['row_ops_needed']:
                    if op['op'] == 'ADD_ROW':
                        nm = op['member'].split(' (')[0]
                        committee[nm] = {'seats_E': op['seats_E'], 'score_B_C_D': op['score_B_C_D'], 'source': op.get('source')}
            for nm, v in committee.items():
                if v and v.get('score_B_C_D') is None:
                    v['score_B_C_D'] = [0, 0, 0]   # committee_scores.py --write (chain) recomputes B/C/D from the seat text in E
            tmp, wrep = plan_workbook(paths.WORKBOOK, delta, {k: v for k, v in committee.items() if v}, people, snap)
            expect = {d['member']: None for d in delta['departures']}
            expect.update({k: v['seats_E'] for k, v in committee.items() if v})
            try:
                verify_workbook(tmp, wrep['order'], expect, wrep['sheet_orders'])
                workbook_writable(paths.WORKBOOK)
                os.replace(tmp, paths.WORKBOOK)
            except PermissionError as e:
                raise Refused('could not put the workbook in place (%s); retry later' % e)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)          # never leave a half-built copy beside the live workbook
            rec['workbook'] = wrep
            log('workbook: deleted %s, inserted %s, committee %s' % (wrep['deleted'], [x['member'] for x in wrep['inserted']],
                                                                   wrep['committee']))
        write_csv(LEG_CSV, csv_header, new_csv)
        log('wrote %s (%d rows)' % (LEG_CSV, len(new_csv)))
        _, crow = census_rows()
        cong = new_congestion(json.load(open(CONGESTION, encoding='utf-8')), delta, write_ids,
                              {b for b, p in people.items() if not p['id'].get('fec')})
        dump_json(CONGESTION, cong)
        log('wrote %s (%d rows)' % (CONGESTION, len(cong)))
        desk = new_desk_src(json.load(open(DESK_SRC, encoding='utf-8')), delta, crow)
        dump_json(DESK_SRC, desk)
        log('wrote %s (%d members, %d departed)' % (DESK_SRC, len(desk['members']), len(desk['departed'])))
        members = {m['bioguide']: m for m in installed['members']}
        for d in delta['departures']:
            members.pop(d['bioguide'])
        held_seats = {x['bioguide'] for x in delta['seat_changes']}
        for b, p in people.items():
            if b in members:
                was = members[b]
                rec_ = dict(person_record(p, was['member']), fec_ids=[x for x in write_ids.get(b, '').split(',') if x])
                if was.get('thin_evidence'):
                    rec_['thin_evidence'] = True        # an arrival stays flagged on every later run (communication_style reads it)
                if b in held_seats:                     # a seat change waits for Paul: keep the installed seat
                    rec_.update({k: was.get(k) for k in ('chamber', 'state', 'district')})
                members[b] = rec_
        for x in delta['arrivals']:
            members[x['bioguide']] = dict(x, fec_ids=[i for i in write_ids.get(x['bioguide'], '').split(',') if i],
                                          thin_evidence=True)
        departed = list(installed.get('departed') or []) + [dict(d['record'], departed_on=d['departed_on'], reason=d['reason'])
                                                             for d in delta['departures']]
        doc = roster_doc(list(members.values()), departed, snap)
        if len(cong) != doc['counts']['total'] or {r['member'] for r in cong} != {m['member'] for m in doc['members']}:
            raise RuntimeError('congestion.json and roster.json disagree after the apply')
        with open(roster.pointer_path(), 'w', encoding='utf-8', newline='\n') as f:
            f.write(sha + '\n')
        dump_json(roster.path(), doc)
        log('wrote %s: %d members %s, %d departed; pointer -> %s' % (roster.path(), doc['counts']['total'], doc['counts'],
                                                                    len(departed), sha[:12]))
        rec['status'] = 'APPLIED'
        rec['roster_after'] = doc['counts']
    except Refused:
        restore(man)
        raise
    except Exception:
        restore(man)
        rec.update(status='FAILED_RESTORED', error=traceback.format_exc())
        finish(rec, run_dir, held)
        raise
    finish(rec, run_dir, held)
    return 0


def finish(rec, run_dir, held=None):
    rec['finished_at'] = now_iso()
    dump_json(os.path.join(run_dir, 'receipt.json'), rec)
    dump_json(RECEIPT, rec)
    if held is not None:
        dump_json(REVIEW, {'schema': 'roster-paul-review-v1', 'as_of': rec['finished_at'], 'items': held,
                           'how_to_approve': 'add {"bioguide": ..., "fec_ids": [...]} to data/engine/roster_fec_approvals.json '
                                             '"approved" (Paul\'s per-change OK); the next roster refresh applies it'})
    log('status %s; receipt %s' % (rec['status'], RECEIPT))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--bootstrap', action='store_true')
    ap.add_argument('--snapshot-dir')
    ap.add_argument('--sources-dir')
    ap.add_argument('--committee-rows')
    a = ap.parse_args(argv)
    if a.bootstrap:
        bootstrap()
        return 0
    try:
        return run(a)
    except Refused as e:
        log('REFUSED: %s' % e)
        return 2
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
