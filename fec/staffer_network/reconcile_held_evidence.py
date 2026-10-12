"""Exhaustive, resumable accounting and evidence-qualified source bindings.

This produces a separate SQLite release candidate. Source claims, identity
decisions and usable dates are separate; a reviewed record is not necessarily
accepted, and source collection time is never substituted for employment time.
"""
from __future__ import annotations

import argparse
import calendar
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
import zlib

from fec.staffer_network.operational_spine import role_state, profile_current_roles
from fec.surfaces.staffer_connection_facts import employment_dates

VERSION = 'held-evidence-reconciliation-v1'


def norm(value):
    value = unicodedata.normalize('NFKD', str(value or '')).casefold()
    return ' '.join(re.findall(r'[a-z0-9]+', value.encode('ascii', 'ignore').decode()))


def packed(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(packed(value).encode()).hexdigest()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def rows(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8-sig') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def source_bounds(value):
    """LittleSis zero components describe precision, not January 1 facts."""
    if not value:
        return None
    match = re.fullmatch(r'(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?', str(value))
    if not match:
        return None
    y, m, d = int(match[1]), int(match[2] or 0), int(match[3] or 0)
    try:
        if d and not m:
            return None
        if m and d:
            day = date(y, m, d).isoformat()
            return day, day
        if m:
            return date(y, m, 1).isoformat(), date(y, m, calendar.monthrange(y, m)[1]).isoformat()
        return date(y, 1, 1).isoformat(), date(y, 12, 31).isoformat()
    except ValueError:
        return None


def interval(start, end, as_of):
    a, b = source_bounds(start), source_bounds(end)
    if start and not a or end and not b:
        return {'status': 'INVALID_SOURCE_DATE'}
    if not a or not b:
        return {'status': 'MISSING_DATE_BOUND', 'start_bounds': a, 'end_bounds': b}
    if a[0] > b[1]:
        return {'status': 'REVERSED_DATE_BOUNDS', 'start_bounds': a, 'end_bounds': b}
    if a[0] > as_of or b[0] > as_of:
        return {'status': 'FUTURE_SOURCE_DATE', 'start_bounds': a, 'end_bounds': b}
    from datetime import timedelta
    stop = (date.fromisoformat(b[0]) + timedelta(days=1)).isoformat()
    return {'status': 'BOUNDED_SOURCE_INTERVAL' if a[1] < stop else 'POSSIBLE_OVERLAP_ONLY',
            'start_bounds': a, 'end_bounds': b, 'start': a[1], 'end_exclusive': stop}


def covered_state(text):
    key = norm(text)
    if not key:
        return 'EMPTY_OR_PUNCTUATION'
    if key in {'none', 'n a', 'na', 'not applicable', 'no', 'n', 'not covered', 'no covered positions', 'none reported'}:
        return 'EXPLICIT_NO_POSITION'
    return 'SUBSTANTIVE_CLAIM_REQUIRES_RECONCILIATION'


def office(value):
    value = re.sub(r'(Democratic|Republican|Independent)(?=[A-Z-]|$).*$', '', str(value or ''))
    value = re.sub(r'^\s*(?:Office of\s+)?(?:Rep\.?|Sen\.?|Representative|Senator)\s+', '', value, flags=re.I)
    return norm(value)


def title(value):
    return norm(value).replace('advisor', 'adviser')


def quorum_decision(q, candidates, reverse, all_names):
    """One-to-one exact-name, office, title and dated-history corroboration.

    This binds source identities. Only Politico supplies dates and preferred
    roles. Payroll support is never treated as a unique identity by itself.
    """
    qid = q['id']
    if role_state(q.get('title')) != 'SUBSTANTIVE':
        return 'EXCLUDED_OR_UNKNOWN_ROLE', None, []
    accepted = []
    for p in candidates:
        if norm(q['label']) != norm(p['label']) or len(norm(q['label']).split()) < 2:
            continue
        if len(all_names.get(norm(p['label']), set())) != 1 or reverse.get(p['id'], set()) != {qid}:
            continue
        current, _ = profile_current_roles(p['payload'])
        current_match = any(office(r['employer']) == office(q['office']) and
                            title(r['title']) == title(q['title']) and role_state(r['title']) == 'SUBSTANTIVE' for r in current)
        support = []
        for h in p['payload'].get('employment_history', []):
            dates = employment_dates(h.get('date_text'), p['payload'].get('observed_at'))
            if (current_match and office(h.get('employer')) == office(q['office']) and
                    title(h.get('title')) == title(q['title']) and role_state(h.get('title')) == 'SUBSTANTIVE' and
                    dates.get('start') and dates['start'] < dates['end_exclusive']):
                support.append({'source_role': h, 'dates': dates})
        if support:
            accepted.append((p['id'], support))
    if len(accepted) == 1 and len(candidates) == 1:
        return 'ACCEPTED_SOURCE_BINDING', accepted[0][0], accepted[0][1]
    return ('NO_RETRIEVED_CANDIDATE' if not candidates else 'IDENTITY_REVIEW_REQUIRED'), None, []


def organization_key(row):
    """Exact substantive name, jurisdiction and business description only.

    Missing corroboration, location changes and multiple names stay in review;
    no suffix stripping, acronym inference or parent/subsidiary merging.
    """
    names = {norm(n) for n in row.get('name_variants', []) if norm(n)}
    desc = {norm(n) for n in row.get('description_variants', []) if norm(n)}
    states, countries = set(row.get('state_variants', [])), set(row.get('country_variants', []))
    if not all(states) or not all(countries):
        return None
    if not (len(names) == len(desc) == len(states) == len(countries) == 1):
        return None
    name, description = next(iter(names)), next(iter(desc))
    if len(name.split()) < 3 or len(name) < 16 or len(description.split()) < 5:
        return None
    if any(x in name for x in (' on behalf of ', ' formerly ', ' dba ', ' doing business as ')):
        return None
    return name, next(iter(states)), next(iter(countries)), description


def connect(path):
    con = sqlite3.connect(path)
    con.executescript('''
      PRAGMA journal_mode=WAL;
      PRAGMA synchronous=NORMAL;
      PRAGMA cache_size=-131072;
      PRAGMA temp_store=MEMORY;
      CREATE TABLE IF NOT EXISTS stage(name TEXT PRIMARY KEY, status TEXT, receipt TEXT);
      CREATE TABLE IF NOT EXISTS input(name TEXT PRIMARY KEY, path TEXT, sha256 TEXT);
      CREATE TABLE IF NOT EXISTS decision(id TEXT PRIMARY KEY, kind TEXT, subject TEXT, target TEXT, status TEXT, proof TEXT);
      CREATE INDEX IF NOT EXISTS decision_subject ON decision(subject);
      CREATE TABLE IF NOT EXISTS person(id TEXT PRIMARY KEY, source TEXT, label TEXT, canonical_id TEXT);
      CREATE TABLE IF NOT EXISTS claim(id TEXT PRIMARY KEY, source TEXT, person TEXT, employer TEXT, title TEXT,
        raw_start TEXT, raw_end TEXT, start TEXT, end_exclusive TEXT, status TEXT, payload TEXT);
      CREATE INDEX IF NOT EXISTS claim_person ON claim(person);
      CREATE INDEX IF NOT EXISTS claim_employer ON claim(employer);
      CREATE TABLE IF NOT EXISTS support(id TEXT PRIMARY KEY, claim_id TEXT, filing_uuid TEXT, activity_id TEXT, posted TEXT);
      CREATE TABLE IF NOT EXISTS organization(id TEXT PRIMARY KEY, label TEXT, kind TEXT, payload TEXT);
    ''')
    return con


class Run:
    def __init__(self, repo, data, output, as_of):
        self.repo, self.data, self.output, self.as_of = Path(repo), Path(data), Path(output), as_of
        self.output.mkdir(parents=True, exist_ok=True)
        self.db = connect(self.output/'evidence.sqlite')
        self.summary = {'version': VERSION, 'as_of': as_of, 'production_activated': False, 'stages': {}}
        self.aliases = {}
        self._bind('builder', Path(__file__))
        live_path = self.repo/'audits/staffer_live_runtime/identity_resolution_receipt.json'
        frozen = self.output/'identity_input.json'
        if not frozen.exists():
            frozen.write_bytes(live_path.read_bytes())
        self._bind('identity', frozen)
        self.identity = json.loads(frozen.read_bytes())
        self.aliases = self.identity.get('identity_aliases', {})

    def _bind(self, name, path):
        path = Path(path).resolve()
        hashed = sha(path)
        old = self.db.execute('SELECT path,sha256 FROM input WHERE name=?', (name,)).fetchone()
        if old and old != (str(path), hashed):
            raise ValueError('Input changed; use a fresh generation: '+name)
        self.db.execute('INSERT OR IGNORE INTO input VALUES(?,?,?)', (name,str(path),hashed))
        self.db.commit()
        return path

    def canonical(self, ident):
        seen = set()
        while ident in self.aliases and ident not in seen:
            seen.add(ident)
            ident = self.aliases[ident]
        return ident

    def step(self, name, fn):
        old = self.db.execute('SELECT status,receipt FROM stage WHERE name=?', (name,)).fetchone()
        if old and old[0] == 'PASS':
            self.summary['stages'][name] = json.loads(old[1])
            return
        print('START', name, flush=True)
        self.db.execute('BEGIN')
        try:
            counts = fn()
            self.db.execute('INSERT OR REPLACE INTO stage VALUES(?,?,?)', (name,'PASS',packed(counts)))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        self.summary['stages'][name] = counts
        self.save()
        print('DONE', name, packed(counts), flush=True)

    def save(self):
        self.summary['updated_at'] = datetime.now(timezone.utc).isoformat()
        tmp = self.output/'progress.tmp'
        tmp.write_text(json.dumps(self.summary,indent=2),encoding='utf8')
        tmp.replace(self.output/'progress.json')

    def directory(self):
        path = self._bind('spine', self.data/'normalized/operational-spine/generation-20260908-candidate-v3/spine.sqlite')
        crosspath = self._bind('quorum_comparison', self.data/'reconciliation/quorum-politico-20260908-design-v1/candidate_crosswalk.ndjson')
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as source:
            people = {r[0]: {'id':r[0], 'source':r[1], 'label':r[2], 'payload':json.loads(r[3])}
                      for r in source.execute('SELECT id,source,label,payload FROM source_records')}
            pol = {k:v for k,v in people.items() if v['source']=='politico'}
            names, candidate_ids, reverse = defaultdict(set), defaultdict(set), defaultdict(set)
            for k,p in pol.items(): names[norm(p['label'])].add(k)
            for r in rows(crosspath):
                candidate_ids[r['quorum_id']].add(r['politico_id'])
                reverse[r['politico_id']].add(r['quorum_id'])
            counts = Counter()
            for ident,p in people.items():
                canon = self.canonical(ident if not ident.startswith('lda:') else 'person:'+ident)
                self.db.execute('INSERT OR REPLACE INTO person VALUES(?,?,?,?)', (ident,p['source'],p['label'],canon))
                if p['source'] != 'quorum':continue
                payload = p['payload']
                q = {**p, 'office':payload.get('member_office_source_key',''), 'title':payload.get('title','')}
                candidates = [pol[k] for k in sorted(candidate_ids[ident]) if k in pol]
                status,target,support = quorum_decision(q,candidates,reverse,names)
                proof = {'method':'UNIQUE_EXACT_NAME_OFFICE_TITLE_AND_DATED_HISTORY', 'source_ids':[ident,target],
                         'candidate_ids':sorted(candidate_ids[ident]), 'history_support':support,
                         'role_authority':'Politico', 'quorum_service_dates_inferred':False,
                         'payroll_used':False, 'identity_scope':'source_binding', 'source_record_sha256':digest(payload)}
                self.db.execute('INSERT INTO decision VALUES(?,?,?,?,?,?)', (digest([ident,'directory']), 'quorum_politico',ident,target,status,packed(proof)))
                counts[status] += 1
            rolecounts = Counter()
            for row in source.execute('SELECT id,source_record_id,person_id,container_id,title,role_state,source_date_text,start,end_exclusive,payload FROM roles'):
                rid,sid,pid,org,job,rs,raw,start,end,payload = row
                state = 'EXCLUDED_INTERN' if rs=='INTERN' else 'ROLE_REVIEW_REQUIRED' if rs!='SUBSTANTIVE' else 'BOUNDED_SOURCE_INTERVAL' if start and end and start < end else 'MISSING_DATE_BOUND'
                self.db.execute('INSERT INTO claim VALUES(?,?,?,?,?,?,?,?,?,?,?)',('role:'+rid,'directory',sid,org,job,raw,None,start,end,state,payload))
                rolecounts[state] += 1
        return {'directory_source_records':len(people),'quorum_records_accounted':sum(counts.values()),'decisions':dict(counts),'roles_accounted':sum(rolecounts.values()),'role_statuses':dict(rolecounts)}

    def organizations(self):
        path = self._bind('lda_organizations', self.data/'normalized/lda-source-native/generation-20260830T231200Z/lda_organization_assertions.ndjson.gz')
        candidates = defaultdict(list)
        counts = Counter()
        for row in rows(path):
            self.db.execute('INSERT INTO organization VALUES(?,?,?,?)',(row['source_org_id'],row['display_name'],row['organization_kind'],packed(row)))
            counts[row['organization_kind']] += 1
            if row['organization_kind']=='client':candidates[norm(row['display_name'])].append(row)
        statuses = Counter()
        for group in candidates.values():
            qualified = defaultdict(list)
            for row in group:
                key = organization_key(row)
                if key:qualified[key].append(row)
            for row in group:
                key = organization_key(row)
                peers = qualified.get(key,[]) if key else []
                target = min(r['source_org_id'] for r in peers) if len(peers)>1 else None
                status = 'ACCEPTED_CORROBORATED_CLIENT_BINDING' if target else 'CLIENT_IDENTITY_REVIEW_REQUIRED' if len(group)>1 else 'NO_EXACT_NAME_PEER'
                proof = {'method':'EXACT_FULL_NAME_JURISDICTION_AND_BUSINESS_DESCRIPTION','source_record':row,
                         'peer_ids':sorted(r['source_org_id'] for r in peers),'canonical_id':target,
                         'same_name_group_size':len(group),'parent_subsidiary_merge':False,'employment_asserted':False}
                self.db.execute('INSERT INTO decision VALUES(?,?,?,?,?,?)',(digest([row['source_org_id'],'client']), 'client_identity',row['source_org_id'],target,status,packed(proof)))
                statuses[status]+=1
        return {'organizations_accounted':sum(counts.values()),'source_kinds':dict(counts),'client_decisions':dict(statuses)}

    def covered(self):
        path=self._bind('covered_positions',self.data/'normalized/lda-source-native/generation-20260830T231200Z/lda_covered_position_assertions.ndjson.gz')
        counts=Counter(); seen=set()
        for row in rows(path):
            key=digest([row['source_person_id'],norm(row.get('covered_position'))])
            state=covered_state(row.get('covered_position'))
            if key not in seen:
                seen.add(key)
                self.db.execute('INSERT INTO claim VALUES(?,?,?,?,?,?,?,?,?,?,?)',(key,'lda_covered_position',row['source_person_id'],None,None,None,None,None,None,state,packed({'raw_text':row.get('covered_position'),'identity_status':'SOURCE_PERSON_ID','employment_dates_inferred':False})))
                counts['distinct_'+state]+=1
            self.db.execute('INSERT INTO support VALUES(?,?,?,?,?)',(row['assertion_id'],key,row['filing_uuid'],row['source_activity_id'],row['dt_posted']))
            counts['source_'+state]+=1
        return {'source_assertions_accounted':sum(v for k,v in counts.items() if k.startswith('source_')),'distinct_person_text_claims':len(seen),'classifications':dict(counts),'employment_dates_inferred':0}

    def littlesis(self):
        path=self._bind('source_catalog',self.data/'live_catalog/source_catalog_v1.sqlite')
        counts=Counter()
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as source:
            for ident,label in source.execute("SELECT id,label FROM entities WHERE kind='person' AND source='LittleSis'"):
                self.db.execute('INSERT OR REPLACE INTO person VALUES(?,?,?,?)',(ident,'littlesis',label,self.canonical(ident)))
                counts['people']+=1
            # Each payload chunk is decompressed once; no per-record database lookup.
            wanted=defaultdict(list)
            for ident,subject,obj,category,chunk,ordinal in source.execute("SELECT id,subject,object,category,chunk,ordinal FROM relationships WHERE category IN ('Position','Membership','Professional')"):
                wanted[chunk].append((ident,subject,obj,category,ordinal))
            for chunk,entries in sorted(wanted.items()):
                payloads=json.loads(zlib.decompress(source.execute('SELECT payload FROM chunks WHERE id=?',(chunk,)).fetchone()[0]))
                for ident,subject,obj,category,ordinal in entries:
                    row=payloads[ordinal]
                    raw_start=row.get('raw_start_date');raw_end=row.get('raw_end_date')
                    dates=interval(raw_start,raw_end,self.as_of)
                    job=row.get('title') or row.get('title_or_degree_raw') or row.get('description1') or ''
                    state='EXCLUDED_INTERN' if role_state(job)=='INTERN' else 'ROLE_REVIEW_REQUIRED' if role_state(job)=='MIXED_ROLE_REVIEW' else dates['status']
                    if row.get('endpoint_missing'):state='MISSING_SOURCE_ENDPOINT'
                    self.db.execute('INSERT INTO claim VALUES(?,?,?,?,?,?,?,?,?,?,?)',('littlesis:relationship:'+str(ident),'littlesis_'+category.lower(),subject,obj,job,raw_start,raw_end,dates.get('start'),dates.get('end_exclusive'),state,packed({'source_assertion':row,'date_assessment':dates,'relationship_class':category,'identity_adjudication':'REQUIRED_BEFORE_CROSS_SOURCE_ROUTING'})))
                    counts[category]+=1;counts['status_'+state]+=1
        return dict(counts)

    def run(self):
        for name,fn in [('directory',self.directory),('organizations',self.organizations),('covered_positions',self.covered),('littlesis_professional',self.littlesis)]:
            self.step(name,fn)
        self.summary['accounting_status']='PASS'
        self.summary['completion_status']='HELD_EVIDENCE_ACCOUNTED_RECONCILIATION_IN_PROGRESS'
        self.summary['integrity']=self.db.execute('PRAGMA integrity_check').fetchone()[0]
        self.summary['inputs']=[dict(zip(('name','path','sha256'),r)) for r in self.db.execute('SELECT * FROM input')]
        self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.save()
        return self.summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--data',type=Path,default=Path.home()/'AppData/Local/Melange/staffer_network')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--as-of',default=date.today().isoformat())
    args=parser.parse_args()
    Run(args.repo,args.data,args.output,args.as_of).run()


if __name__=='__main__':main()
