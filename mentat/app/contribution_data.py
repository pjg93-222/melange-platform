"""Typed, source-bound contribution analytics. Sources are immutable; only an
indexed analytical cache is written. Currency travels over the API in cents.

Define/identify/connect/count/verify reuse the project's evidence-aware engine.
The historical worksheet receipt basis stays separately selectable.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import closing
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN
from functools import lru_cache
from itertools import groupby
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
import time
import uuid
import zipfile
from xml.etree import ElementTree as ET

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'tools'))
from cross_reference_engine import analyze
from run_cross_reference import coalesce_source_observations
from cross_reference_store import EvidenceStore
from receipt_path import receipt_path

CACHE_ROOT = PROJECT / 'state' / 'contribution-analytics'
VERSION = 3
RULE_PATHS = [PROJECT/'tools'/ (name+'.py') for name in ('run_cross_reference','cross_reference_engine',
    'cross_reference_store','pac_evidence','report_source_guard','validate_industry_coverage','fec_filing_headers')]
RULE_PATHS += [PROJECT/'inputs/fec_receipt_rules.json',Path(__file__)]
# Bind the cache to the code actually imported by this process. A running app
# must restart after edits rather than label old Python objects as new code.
LOADED_RULE_STAMPS = [(str(p),p.stat().st_size,p.stat().st_mtime_ns) for p in RULE_PATHS]
_lock = threading.RLock()
_jobs = {}
_build_gate = threading.Semaphore(1)
DESTINATIONS = {'recipient_state':'Recipient committee state','represented_state':'Candidate represented state',
    'recipient_location_status':'Committee location status','represented_location_status':'Candidate location status',
    'recipient_location_evidence':'Committee location evidence','represented_location_evidence':'Candidate link evidence'}
DIMENSIONS = {
    'industry':'Industry', 'cycle':'Election cycle', 'year':'Contribution year',
    'company':'Company', 'date':'Contribution date',
    'employer':'Reported employer', 'occupation':'Reported occupation',
    'street_1':'Contributor street 1', 'street_2':'Contributor street 2',
    'city':'Contributor city', 'state':'Contributor state', 'zip5':'Contributor ZIP',
    'contributor':'Reported contributor',
    'recipient':'Recipient committee',
    'committee_id':'Committee ID', 'recipient_category':'Recipient category',
    'party':'Recipient party', 'funding_source':'Funding source',
    'decision_status':'Accounting status', 'receipt_role':'Receipt role',
    **DESTINATIONS,
}
MEASURES = {'amount':'Net amount', 'gross_amount':'Positive amount', 'refunds':'Negative adjustments',
            'records':'Observation count', 'contributors':'Distinct reported donor identities',
            'average_amount':'Average observation amount', 'min_amount':'Minimum amount',
            'max_amount':'Maximum amount', 'median_amount':'Median observation amount'}
BASES = {
    'original_observed':('Observed original giving (provisional)', 'original_cents'),
    'original_supported':('Supported original giving subset', 'supported_cents'),
    'receipt':('Published included receipt view', 'receipt_cents'),
    'raw':('Raw source observations (not giving)', 'raw_cents'),
    'pac_activity':('PAC activity on recipient receipts', 'pac_cents'),
}


@lru_cache(maxsize=128)
def _json_stamped(stamp):
    with Path(stamp[0]).open(encoding='utf-8-sig') as stream:
        return json.load(stream)


def _json(path):
    # Parsed once per file state (stamp), like _hash_stamp. The object is shared: read it, never edit it.
    return _json_stamped(_stamp(path))


def _stamp(path):
    path = Path(path).resolve()
    stat = path.stat()
    return str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@lru_cache(maxsize=128)
def _hash_stamp(stamp):
    with open(stamp[0], 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _hash(path):
    return _hash_stamp(_stamp(path))


def _cents(value):
    if value is None or isinstance(value, bool):
        raise ValueError('A source amount is missing or invalid.')
    amount = Decimal(str(value))
    if not amount.is_finite():
        raise ValueError('A source amount is not finite.')
    return int((amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_EVEN))


def _ro(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.execute('PRAGMA query_only=ON')
    db.row_factory = sqlite3.Row
    return db


@lru_cache(maxsize=64)
def _payload_metadata_stamped(stamp):
    # Metadata is the first object of the generator's JSON, never load a 1GB
    # payload merely to find its source path.
    with Path(stamp[0]).open(encoding='utf-8-sig') as stream:
        head = stream.read(2_000_000)
    match = re.search(r'"metadata"\s*:\s*', head)
    if not match:
        raise ValueError('The report payload has no source metadata.')
    return json.JSONDecoder().raw_decode(head[match.end():])[0]


def _payload_metadata(path):
    # Read once per file state (stamp), so a scan does not re-read the 2 MB head of every payload.
    # The object is shared: read it, never edit it.
    return _payload_metadata_stamped(_stamp(path))


def _payload_rows(path, name='raw_rows'):
    """Stream one top-level payload array without materializing other tables."""
    decoder = json.JSONDecoder()
    pattern = re.compile(r'"' + re.escape(name) + r'"\s*:\s*\[')
    with Path(path).open(encoding='utf-8-sig') as stream:
        buffer = ''
        while True:
            chunk = stream.read(65536)
            if not chunk:
                raise ValueError('The report payload lacks contribution facts.')
            buffer += chunk
            match = pattern.search(buffer)
            if match:
                buffer = buffer[match.end():]
                break
            buffer = buffer[-256:]
        while True:
            buffer = buffer.lstrip(' \r\n\t,')
            if buffer.startswith(']'):
                return
            try:
                item, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                chunk = stream.read(65536)
                if not chunk:
                    raise ValueError('The report payload contribution array is incomplete.')
                buffer += chunk
                continue
            yield item
            buffer = buffer[end:]


@lru_cache(maxsize=24)
def _workbook_source(stamp):
    # The hash is printed in report source notes. Read ZIP members as streams;
    # never use worksheet dimension limits as contribution coverage limits.
    with zipfile.ZipFile(stamp[0]) as archive:
        members = ['xl/sharedStrings.xml'] if 'xl/sharedStrings.xml' in archive.namelist() else []
        members += [n for n in archive.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', n)]
        for name in members:
            with archive.open(name) as stream:
                for _, element in ET.iterparse(stream, events=('end',)):
                    if element.tag.endswith('}t') and element.text:
                        found = re.search(r'Source DB SHA-256:\s*([a-f0-9]{64})', element.text)
                        if found:
                            return found.group(1)
                    element.clear()
    raise ValueError('This workbook has no pinned contribution source.')


def _slug(industry):
    return re.sub(r'[^a-z0-9]+', '_', industry.lower()).strip('_')


def _sources(directory):
    """Only filenames in the supplied server-owned report directory enter scope.
    Internal payloads are lineage evidence, never substitute report workbooks.
    """
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise ValueError('The configured report directory is unavailable.')
    result = []
    from report_identity import identity, companion_name, lineage_paths
    for workbook in sorted(directory.glob('* Industry Spending Overview*.xlsx')):
        info = identity(workbook.name)
        if workbook.name.startswith('~$') or info is None or info['kind'] != 'overview':
            continue
        if not workbook.resolve().is_relative_to(directory):
            raise ValueError('A report resolves outside its configured directory.')
        industry = info['industry']
        item = {'id':info['label'], 'industry':industry, 'label':info['label'], 'workbook':workbook}
        if info['cycle_label']:
            item.update(snapshot_label=info['label'], cycle_label=info['cycle_label'])
        try:
            item['workbook_sha256'] = _hash(workbook)
        except OSError:
            # One book this process cannot open - Excel holding it, a scanner
            # mid-pass - used to abort the whole list, so every route that
            # lists snapshots answered 400 instead of naming the one snapshot
            # that cannot be read. The entry is reported unavailable and the
            # scan continues. No filename enters the message, as everywhere.
            item.update(workbook_sha256=None, available=False,
                        message='Report workbook is unreadable.')
            result.append(item)
            continue
        # The companion Data workbook is published beside the Overview by the
        # same run, from the same staging store and the same database. It is
        # recorded as lineage evidence ONLY: the verified dataset still reads
        # the pinned payload, so a Data book that is stale, hand-edited or
        # simply absent cannot become a substitute report source. A lineage
        # mismatch is reported, never silently accepted.
        sibling = workbook.with_name(companion_name(info))
        if sibling.is_file() and sibling.resolve().is_relative_to(directory):
            item['data_workbook'] = sibling
            try:
                item['data_workbook_sha256'] = _hash(sibling)
            except OSError:
                # The companion is lineage evidence, never a report source, so
                # an unreadable one is a reported blank plus a failed lineage
                # check below - it does not disqualify the Overview book.
                item['data_workbook_sha256'] = None
            try:
                item['data_lineage_matches'] = (
                    _workbook_source(_stamp(sibling)) == _workbook_source(_stamp(workbook)))
            except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                item['data_lineage_matches'] = False
        try:
            db_hash = _workbook_source(_stamp(workbook))
            # The P&C cache historically omits the ampersand, which slug() also does.
            cache = PROJECT / '.cache' / 'industry-report' / _slug(industry)
            manifest_path, payload = lineage_paths(cache, db_hash, info['cycle_label'])
            manifest = _json(manifest_path)
            if manifest.get('database_sha256') != db_hash:
                raise ValueError('Report source fingerprint disagrees with its payload manifest.')
            metadata = _payload_metadata(payload)
            if info['cycle_label']:
                if (manifest.get('cycle_label') != info['cycle_label']
                        or set(metadata.get('expected_cycle_totals', {})) != {info['cycle_label']}
                        or metadata.get('industry') != industry
                        or manifest.get('published_workbook') != workbook.name
                        or manifest.get('workbook_sha256') != item['workbook_sha256']
                        or not re.fullmatch(r'[a-f0-9]{64}', str(manifest.get('data_sha256', '')))
                        or _hash(payload) != manifest['data_sha256']):
                    raise ValueError('Cycle-specific report payload does not match its published scope and fingerprint.')
            database = receipt_path(metadata['database_path']).resolve()
            original_hash = db_hash
            hydration_path = None
            if not database.is_file():
                # A preserved recovery receipt is an explicit source lineage,
                # not a newest-file or arbitrary-database fallback.
                hydration_path=PROJECT/'.cache/receipt-metadata-repair-20260910'/_slug(industry)/'mentat.db.hydration.json'
                hydration=_json(hydration_path)
                if hydration.get('source_sha256') != original_hash:
                    raise ValueError('Recovered source lineage does not match this workbook.')
                database=receipt_path(hydration['output']).resolve()
                if not database.is_relative_to((PROJECT/'.cache').resolve()) or not database.is_file():
                    raise ValueError('The pinned recovered contribution database is unavailable.')
                db_hash=_hash(database)
            item.update(available=True, database=database, database_sha256=db_hash,
                        original_database_sha256=original_hash,hydration_manifest=hydration_path,
                        payload=payload, payload_sha256=manifest.get('data_sha256'),
                        manifest=manifest_path, expected_cycle_totals=copy.deepcopy(metadata.get('expected_cycle_totals', {})),
                        destination_schema=metadata.get('destination_schema',0))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
            item.update(available=False, message=str(error) if isinstance(error, ValueError) else 'Pinned report source evidence is unavailable.')
        result.append(item)
    return result


def _fields():
    dimension=lambda k,v:{'id':k,'label':v,'name':v,'type':'date' if k=='date' else 'category','role':'dimension'}
    return ([dimension(k,v) for k,v in DIMENSIONS.items() if k not in DESTINATIONS] +
            [{'id':k,'label':v,'name':v,'type':'number','role':'measure','unit':'count' if k in ('records','contributors') else 'cents'} for k,v in MEASURES.items()] +
            [dimension(k,v) for k,v in DESTINATIONS.items()])


def _provenance(source):
    data_workbook = source.get('data_workbook')
    return {'workbook':source['workbook'].name,'workbook_sha256':source['workbook_sha256'],
            'data_workbook':data_workbook.name if data_workbook else None,
            'data_workbook_sha256':source.get('data_workbook_sha256'),
            # None when there is no companion book; False is a REPORTED
            # disagreement between the two books' pinned source databases, not a
            # missing file, and the two must never read the same.
            'data_lineage_matches':source.get('data_lineage_matches'),
            'destination_schema':source.get('destination_schema',0),
            'database_sha256':source.get('database_sha256'),'payload_sha256':source.get('payload_sha256'),
            'original_database_sha256':source.get('original_database_sha256'),
            'accounting_engine_sha256':_hash(PROJECT/'tools/cross_reference_engine.py'),
            'cycle_basis':'Contribution date, calendar election cycle',
            'scope':'Tracked companies represented by the pinned report source. Collection completeness is not inferred.'}


def catalog(report_directory, sources=None):
    # `sources` lets one request reuse a scan it already paid for.
    datasets=[]
    for source in (_sources(report_directory) if sources is None else sources):
        datasets.append({k:source[k] for k in ('id','industry','label','available')})
        datasets[-1]['industry'] = source.get('snapshot_label', source['industry'])
        datasets[-1].update(fields=_fields(), bases=[{'id':k,'label':v[0],'status':'provisional' if k=='original_observed' else 'supported_subset' if k=='original_supported' else 'observed'} for k,v in BASES.items()],
                            provenance=_provenance(source), message=source.get('message'), default_basis='receipt')
    return {'schema_version':VERSION,'datasets':datasets,'fields':_fields(),'amount_unit':'cents'}


@lru_cache(maxsize=64)
def _rule_digest_stamped(stamp):
    # Code and rule files are identified by content, CRLF read as LF, so a checkout on another machine
    # (other path, other line endings, other timestamps) names the same index.
    return hashlib.sha256(Path(stamp[0]).read_bytes().replace(b'\r\n',b'\n')).hexdigest()


def _portable(path,roots):
    # A data input is identified by its path under the report folder or the checkout (posix separators),
    # never by where those sit on this machine; outside both, by file name.
    path=Path(path).resolve()
    for root in roots:
        if path.is_relative_to(root):return path.relative_to(root).as_posix()
    return path.name


def _token(source):
    if LOADED_RULE_STAMPS != [(str(p),p.stat().st_size,p.stat().st_mtime_ns) for p in RULE_PATHS]:
        raise ValueError('Accounting code changed. Restart the app to load the current accounting rules.')
    paths=[source['workbook'],source['database'],source['payload'],source['manifest']]
    if source.get('hydration_manifest'):paths.append(source['hydration_manifest'])
    # The selected, hash-verified ledger is an explicitly pinned evidence epoch.
    # Without a ledger, pin the current evidence store including SQLite sidecars.
    if 'ledger' not in source:
        source['ledger'],source['ledger_report']=_compatible_ledger(source)
    if source['ledger']:
        paths += [source['ledger'],source['ledger'].with_name('report.json')]
    else:
        # SHM is a lock/index file: ordinary read-only connections change it.
        # An empty WAL is likewise equivalent to absence, not new evidence.
        paths += [p for suffix in ('','-wal')
                  if (p:=PROJECT/('state/cross-reference/evidence.sqlite'+suffix)).exists() and p.stat().st_size]
    roots=(Path(source['workbook']).resolve().parent,PROJECT.resolve())
    inputs=[[_portable(p,roots),*_stamp(p)[1:3]] for p in paths]   # name under its root, size, mtime_ns
    rules=[_rule_digest_stamped(_stamp(p)) for p in RULE_PATHS]
    return hashlib.sha256(json.dumps([inputs,rules],sort_keys=True).encode()).hexdigest()


def _source(report_directory, request, sources=None):
    # `sources` lets one request reuse a scan it already paid for.
    if not isinstance(request,dict) or any(k in request for k in ('path','database','file','source_path')):
        raise ValueError('Choose a configured contribution dataset, not a file path.')
    name=request.get('dataset')
    found=next((s for s in (_sources(report_directory) if sources is None else sources) if s['id']==name),None)
    if not found:raise ValueError('Choose an available contribution dataset.')
    if not found['available']:raise ValueError(found['message'])
    fields=set(request.get('dimensions',[]))|{f.get('field') for f in request.get('filters',[]) if isinstance(f,dict)}
    if fields.intersection(DESTINATIONS) and found.get('destination_schema',0)<1:
        raise ValueError('Regenerate this workbook to create its recipient and candidate state destinations. Contributor state is a different view.')
    return found


def _progress(token, **values):
    with _lock:
        _jobs.setdefault(token,{}).update(values)


def _schema(db):
    dims=', '.join('"'+name+'" TEXT NOT NULL' for name in DIMENSIONS)
    db.executescript(f'''CREATE TABLE facts(source_id TEXT PRIMARY KEY,{dims},
        contributor_key TEXT, raw_zip TEXT, raw_cents INTEGER NOT NULL,receipt_cents INTEGER NOT NULL,
        original_cents INTEGER NOT NULL,supported_cents INTEGER NOT NULL,pac_cents INTEGER NOT NULL,
        unresolved INTEGER NOT NULL,reasons TEXT NOT NULL,evidence_ids TEXT NOT NULL,source_url TEXT,
        source_table TEXT NOT NULL);
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE INDEX facts_company_cycle ON facts(company,cycle);
        CREATE INDEX facts_zip ON facts(zip5);
        CREATE INDEX facts_recipient ON facts(committee_id);
        CREATE INDEX facts_state ON facts(state);''')


def _record(raw, decision, receipt):
    day=str(raw.get('contribution_receipt_date') or '')[:10]
    try:year=date.fromisoformat(day).year
    except ValueError as error:raise ValueError('Source contribution date is invalid.') from error
    cycle=year+year%2
    if receipt:
        compact_cycle=receipt.get('_verified_compact_cycle')
        date_matches=(type(compact_cycle) is int and compact_cycle==cycle and receipt.get('date') is None
                      if '_verified_compact_cycle' in receipt else str(receipt.get('date') or '')[:10]==day)
        if (_cents(receipt['amount'])!=_cents(raw['contribution_receipt_amount'])
                or str(receipt.get('committee_id') or '')!=str(raw.get('committee_id') or '')
                or not date_matches
                or ' '.join(str(receipt.get('contributor') or '').upper().split())!=' '.join(str(raw.get('contributor_name') or '').upper().split())):
            raise ValueError('A contribution differs financially from its published source representation.')
    zipcode=str(raw.get('contributor_zip') or '').strip()
    zip5=zipcode[:5] if re.fullmatch(r'\d{5}(?:\d{4}|-\d{4})?',zipcode) else ''
    name=' '.join(str(raw.get('contributor_name') or '').upper().split())
    # Reuse known report person keys; fallback is a reported donor identity,
    # explicitly not a claim of person linkage or unique real-world donors.
    person=str((receipt or {}).get('person_key') or '')
    if not person and name:person='reported:'+ '|'.join((name,str(raw.get('contributor_state') or '').upper(),zip5))
    d={k:'' for k in DIMENSIONS}
    d.update(source_id=str(raw['sub_id']),industry=str(raw.get('industry') or ''),cycle=f'{cycle-1}-{cycle}',year=str(year),date=day,
        company=str(decision.get('company') or raw.get('company') or ''),zip5=zip5,raw_zip=zipcode,
        contributor=str(raw.get('contributor_name') or ''),
        state=str(raw.get('contributor_state') or '').upper().strip(),city=str(raw.get('contributor_city') or '').strip(),
        employer=str(raw.get('contributor_employer') or '').strip(),occupation=str(raw.get('contributor_occupation') or '').strip(),
        street_1=str(raw.get('contributor_street_1') or '').strip(),street_2=str(raw.get('contributor_street_2') or '').strip(),
        recipient=str(raw.get('committee_name') or raw.get('committee_id') or ''),committee_id=str(raw.get('committee_id') or ''),
        party=str((receipt or {}).get('party') or raw.get('baseline_party') or 'Unknown'),
        recipient_category=str((receipt or {}).get('recipient_category') or raw.get('baseline_category') or 'Unclassified'),
        funding_source=str(decision.get('bucket') or 'unverified'),decision_status=str(decision.get('status') or 'provisional'),
        receipt_role=str(decision.get('receipt_role') or 'unverified'),contributor_key=person or None,
        raw_cents=_cents(raw['contribution_receipt_amount']),receipt_cents=_cents(receipt['amount']) if receipt and receipt.get('include_in_analysis')=='Yes' else 0,
        original_cents=decision['counted_cents'],supported_cents=decision.get('supported_unique_cents',0),pac_cents=decision.get('pac_activity_cents',0),
        unresolved=int(decision.get('status') in ('needs_review','held_for_attribution_review','provisional')),
        reasons=json.dumps(decision.get('reasons',[])),evidence_ids=json.dumps(decision.get('evidence_ids',[])),
        source_url=str(raw.get('pdf_url') or ''),source_table=str(raw.get('source_table') or ''))
    d.update({k:str((receipt or {}).get(k) or ('Unknown' if k.endswith('_state') else 'Workbook destination data unavailable')) for k in DESTINATIONS})
    return d


def _insert(db, raw, decisions, receipts):
    for decision in decisions:
        row=_record(raw[decision['source_id']],decision,receipts.get(decision['source_id']))
        columns=list(row)
        db.execute('INSERT INTO facts ('+','.join('"'+c+'"' for c in columns)+') VALUES ('+','.join('?' for _ in columns)+')',[row[c] for c in columns])


def _compatible_ledger(source):
    candidates=[]
    for path in (PROJECT/'state/cross-reference').glob('*/report.json'):
        try:
            report=_json(path);scope=report.get('scope',{})
            if (report.get('status')=='completed' and scope.get('database_sha256')==source['database_sha256']
                and scope.get('industries')==[source['industry']]
                and sorted(scope.get('cycles',[]))==([int(source['cycle_label'][-4:])] if source.get('cycle_label') else [2022,2024,2026])):
                fingerprints={k.replace('\\','/'):v for k,v in scope.get('rule_fingerprints',{}).items()}
                required=[p for p in RULE_PATHS if p!=Path(__file__)]
                if all(fingerprints.get(p.relative_to(PROJECT).as_posix())==_hash(p) for p in required):
                    companies,count=_source_scope(_stamp(source['database']),source['industry'])
                    if sorted(scope.get('companies',[]))==companies and scope.get('distinct_source_ids')==count:
                        candidates.append((path,report))
        except (OSError,ValueError):continue
    for path,report in sorted(candidates,reverse=True):
        ledger=path.with_name('ledger.sqlite')
        if ledger.exists() and _hash(ledger)==report.get('ledger_sha256'):
            return ledger,report
    return None,None


@lru_cache(maxsize=24)
def _source_scope(stamp,industry):
    with closing(_ro(stamp[0])) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        source_tables=[t for t in ('contributions','corporate_giving') if t in tables]
        parts=['SELECT CAST(sub_id AS TEXT) AS sid,company FROM '+t+' WHERE industry=? AND contribution_receipt_date>=? AND contribution_receipt_date<?' for t in source_tables]
        params=[v for _ in source_tables for v in (industry,'2021-01-01','2027-01-01')]
        query=' UNION ALL '.join(parts)
        count=db.execute('SELECT COUNT(DISTINCT sid) FROM ('+query+')',params).fetchone()[0]
        if 'companies' in tables:
            companies=sorted(r[0] for r in db.execute('SELECT DISTINCT company FROM companies WHERE industry=?',(industry,)))
        else:companies=sorted(r[0] for r in db.execute('SELECT DISTINCT company FROM ('+query+')',params))
    return companies,count


class _Receipts:
    """Disk-backed, narrow projection; Banking's payload exceeds 1 GB."""
    FIELDS=('amount','committee_id','date','contributor','include_in_analysis','person_key','party','recipient_category',*DESTINATIONS)

    def __init__(self, db, path, progress=lambda n:None, source_proof=None):
        self.db=db
        compact=_payload_metadata(path).get('payload_compaction',{})
        fields=compact.get('raw_fields')
        declared_date_omission=(compact.get('mode')=='formula_fact_required_fields' and isinstance(fields,list)
            and 'date' not in fields and all(k in fields for k in ('source_id','amount','committee_id','contributor','cycle')))
        db.execute('CREATE TABLE receipts(source_id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        count=0
        for raw in _payload_rows(path):
            sid=str(raw.get('source_id') or '')
            if not sid:raise ValueError('A published contribution is missing its source ID.')
            projected={k:raw.get(k) for k in self.FIELDS}
            if 'date' not in raw:
                if not declared_date_omission or not source_proof or source_proof.get('full_financial_universe_preserved') is not True:
                    raise ValueError('A payload date omission lacks an explicit compact schema and complete source proof.')
                if type(raw.get('cycle')) is not int or raw['cycle'] not in (2022,2024,2026):
                    raise ValueError('A compact contribution lacks a valid election cycle.')
                projected['_verified_compact_cycle']=raw['cycle']
            payload=json.dumps(projected,separators=(',',':'))
            prior=db.execute('SELECT payload FROM receipts WHERE source_id=?',(sid,)).fetchone()
            if prior:
                if prior[0]!=payload:raise ValueError('A published source ID has conflicting receipt representations.')
            else:db.execute('INSERT INTO receipts VALUES (?,?)',(sid,payload))
            count+=1
            if count%10000==0:progress(count)

    def get(self, sid, default=None):
        row=self.db.execute('SELECT payload FROM receipts WHERE source_id=?',(sid,)).fetchone()
        return json.loads(row[0]) if row else default


def _effective_row(row, evidence):
    """Use only identity-checked engine evidence for geographical enrichment."""
    remote=evidence.get(row['sub_id'])
    if not remote:return row
    for field in ('sub_id','committee_id','contributor_name','transaction_id'):
        if row.get(field) not in (None,'') and ' '.join(str(row[field]).upper().split())!=' '.join(str(remote.get(field) or '').upper().split()):
            raise ValueError('An evidence identity differs from its stored contribution.')
    if (_cents(row['contribution_receipt_amount'])!=_cents(remote['contribution_receipt_amount'])
            or str(row['contribution_receipt_date'])[:10]!=str(remote['contribution_receipt_date'])[:10]):
        raise ValueError('An evidence amount/date differs from its stored contribution.')
    protected={k:row[k] for k in ('company','industry','cycle','current_receipt_cents','company_match_status',
        'company_match_reason','excluded_reason','source_conflicts','attribution_claims') if k in row}
    return {**row,**{k:v for k,v in remote.items() if v is not None},**protected}


def _ledger_facts(ledger):
    # Stream coalesced source IDs, preserving accepted-query priority and all
    # metadata enrichment; MIN(rowid) can choose a rejected or incomplete copy.
    with closing(_ro(ledger)) as db:
        evidence={sid:json.loads(payload) for sid,payload in db.execute('SELECT source_id,payload_json FROM evidence')}
        decisions=iter(db.execute('SELECT source_id,payload_json FROM decisions ORDER BY source_id'))
        sources=db.execute('SELECT source_id,payload_json FROM source_records ORDER BY source_id')
        for sid,group in groupby(sources,key=lambda r:r[0]):
            raw=coalesce_source_observations([json.loads(r[1]) for r in group])[0]
            found=next(decisions,None)
            if found is None or found[0]!=sid:raise ValueError('The canonical ledger does not contain one decision per source ID.')
            yield _effective_row(raw,evidence),json.loads(found[1])
        if next(decisions,None):raise ValueError('The canonical ledger contains an observation without a source record.')


def _stage_sources(target, source, receipts, progress=lambda n,phase:None):
    """Canonicalize globally before partitioning engine work by company."""
    target.executescript('CREATE TABLE observations(source_id TEXT,payload TEXT);'
                        'CREATE TABLE canonical_sources(source_id TEXT PRIMARY KEY,company TEXT,committee TEXT,transaction_id TEXT,file_num TEXT,payload TEXT);')
    with closing(_ro(source['database'])) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        claims={};conflicts={}
        for table,key in (('attribution_claims',claims),('attribution_conflicts',claims),('source_record_conflicts',conflicts)):
            if table not in tables:continue
            for item in db.execute('SELECT * FROM '+table):
                row=dict(item);sid=str(row.get('sub_id') or '')
                if table=='attribution_conflicts':row={'company':row.get('incoming_company'),'status':'competing_legacy_claim'}
                key.setdefault(sid,[]).append(row)
        processed=0
        for table,period in (('contributions','two_year_transaction_period'),('corporate_giving','cycle')):
            if table not in tables:continue
            for item in db.execute('SELECT * FROM '+table+' WHERE industry=?',(source['industry'],)):
                row=dict(item);sid=str(row['sub_id']);day=date.fromisoformat(str(row.get('contribution_receipt_date') or '')[:10]);cycle=day.year+day.year%2
                if cycle not in (2022,2024,2026):continue
                if source.get('cycle_label') and cycle != int(source['cycle_label'][-4:]):continue
                receipt=receipts.get(sid)
                row.update(sub_id=sid,source_table=table,source_query_cycle=row.get(period),cycle=cycle,
                    current_receipt_cents=_cents(receipt['amount']) if receipt and receipt.get('include_in_analysis')=='Yes' else 0,
                    attribution_claims=claims.get(sid,[]),source_conflicts=conflicts.get(sid,[]))
                target.execute('INSERT INTO observations VALUES (?,?)',(sid,json.dumps(row,separators=(',',':'))))
                processed+=1
                if processed%10000==0:progress(processed,'preserving source observations')
    target.execute('CREATE INDEX observations_source ON observations(source_id)')
    processed=0
    for sid,group in groupby(target.execute('SELECT source_id,payload FROM observations ORDER BY source_id'),key=lambda r:r[0]):
        row=coalesce_source_observations([json.loads(r[1]) for r in group])[0]
        target.execute('INSERT INTO canonical_sources VALUES (?,?,?,?,?,?)',(sid,row['company'],str(row.get('committee_id') or ''),str(row.get('transaction_id') or ''),str(row.get('file_num') or ''),json.dumps(row,separators=(',',':'))))
        processed+=1
        if processed%10000==0:progress(processed,'coalescing source IDs across company queries')
    target.execute('DROP TABLE observations')
    target.execute('CREATE INDEX canonical_company ON canonical_sources(company)')


def _fallback_facts(target, source, receipts, progress):
    _stage_sources(target,source,receipts,progress)
    evidence_path=PROJECT/'state/cross-reference/evidence.sqlite'
    store=None
    try:
        if evidence_path.exists():
            store=EvidenceStore.__new__(EvidenceStore);store.db=_ro(evidence_path)
            store._verified_documents=OrderedDict();store._verification_stamp=None
        # Explicit filing roots, never coincident transaction IDs, establish
        # cross-company ambiguity. Keep those rows unresolved before partition.
        target.execute('CREATE TABLE filing_roots(file_num TEXT PRIMARY KEY,committee TEXT,root TEXT)')
        if store:
            for file_num,payload in store.db.execute('SELECT file_num,payload_json FROM headers'):
                header=json.loads(payload)
                if header.get('relationship_evidence') is not False:
                    target.execute('INSERT INTO filing_roots VALUES (?,?,?)',(str(file_num),str(header.get('committee_id') or ''),str(header.get('original_file_num') or file_num)))
        cross_company={r[0] for r in target.execute('''SELECT c.source_id FROM canonical_sources c JOIN filing_roots h ON c.file_num=h.file_num AND c.committee=h.committee
            JOIN (SELECT c.committee,h.root,c.transaction_id FROM canonical_sources c JOIN filing_roots h ON c.file_num=h.file_num AND c.committee=h.committee
                WHERE c.transaction_id!='' GROUP BY c.committee,h.root,c.transaction_id HAVING COUNT(DISTINCT c.company)>1) x
            ON c.committee=x.committee AND h.root=x.root AND c.transaction_id=x.transaction_id''')}
        companies=[r[0] for r in target.execute('SELECT DISTINCT company FROM canonical_sources ORDER BY company')]
        processed=0
        for company in companies:
            rows=[json.loads(r[0]) for r in target.execute('SELECT payload FROM canonical_sources WHERE company=?',(company,))]
            for row in rows:
                if row['sub_id'] in cross_company:row['source_conflicts'].append({'reason':'amendment_changes_company_identity','source_id':row['sub_id']})
            evidence,headers,conflicts=store.resolve(rows) if store else ({},{},[])
            by_id={r['sub_id']:r for r in rows}
            for conflict in conflicts:by_id[conflict['source_id']]['source_conflicts'].append(conflict)
            for row in rows:
                header=headers.get(str(row.get('file_num')))
                if header and str(header.get('committee_id'))==str(row.get('committee_id')) and not row.get('filing_form'):
                    row['filing_form']=header.get('filing_form')
            result=analyze(rows,evidence=evidence,headers=headers)
            for decision in result['decisions']:yield _effective_row(by_id[decision['source_id']],evidence),decision
            processed+=len(rows);progress(processed,company)
    finally:
        if store:store.db.close()
    target.executescript('DROP TABLE filing_roots; DROP TABLE canonical_sources;')


def _materialize(source,token,destination):
    pending=destination.with_suffix('.'+uuid.uuid4().hex+'.pending')
    try:
        with _build_gate:
            _progress(token,status='preparing',message='Verifying the pinned contribution sources.')
            if _hash(source['database'])!=source['database_sha256']:raise ValueError('The pinned source database changed; reconcile its lineage before querying.')
            if source.get('payload_sha256') and _hash(source['payload'])!=source['payload_sha256']:raise ValueError('The report payload no longer matches its fingerprint.')
            for suffix in ('-wal','-journal'):
                p=Path(str(source['database'])+suffix)
                if p.exists() and p.stat().st_size:raise ValueError('The pinned source has active uncheckpointed writes.')
            import contribution_source_guard
            source_proof=contribution_source_guard.verify(source)
            CACHE_ROOT.mkdir(parents=True,exist_ok=True)
            with closing(sqlite3.connect(pending)) as target:
                target.execute('PRAGMA auto_vacuum=FULL')
                target.execute('PRAGMA temp_store=FILE')
                target.execute('PRAGMA cache_size=-32768')
                _schema(target)
                _progress(token,message='Indexing published receipt identities in bounded storage.')
                receipts=_Receipts(target,source['payload'],lambda n:_progress(token,processed_records=n),source_proof=source_proof)
                ledger,report=source.get('ledger'),source.get('ledger_report')
                processed=0
                if ledger:
                    _progress(token,message='Indexing canonical contribution decisions and transaction ZIPs.')
                    facts=_ledger_facts(ledger)
                else:
                    _progress(token,message='Connecting observations with preserved attribution and accounting evidence.')
                    facts=_fallback_facts(target,source,receipts,lambda n,company:_progress(token,processed_records=n,message='Indexed '+str(n)+' observations; '+str(company)))
                for raw,decision in facts:
                    _insert(target,{decision['source_id']:raw},[decision],receipts)
                    processed+=1
                    if processed%10000==0:_progress(token,processed_records=processed)
                totals=dict(zip(('raw','receipt','original_observed','original_supported','pac_activity','records'),target.execute('SELECT SUM(raw_cents),SUM(receipt_cents),SUM(original_cents),SUM(supported_cents),SUM(pac_cents),COUNT(*) FROM facts').fetchone()))
                expected=sum(_cents(v) for v in source.get('expected_cycle_totals',{}).values())
                cycle_totals=dict(target.execute('SELECT cycle,SUM(receipt_cents) FROM facts GROUP BY cycle'))
                expected_cycles={k:_cents(v) for k,v in source.get('expected_cycle_totals',{}).items()}
                missing=target.execute('SELECT COUNT(*) FROM receipts r LEFT JOIN facts f USING(source_id) WHERE f.source_id IS NULL').fetchone()[0]
                if missing:raise ValueError('Published source observations are missing from the contribution index.')
                if expected and totals['receipt']!=expected:raise ValueError('Contribution-level receipt totals do not reconcile to the pinned workbook payload.')
                if expected_cycles and any(cycle_totals.get(k,0)!=v for k,v in expected_cycles.items()):
                    raise ValueError('Contribution-level receipt cycles do not reconcile to the pinned workbook payload.')
                metadata={'token':token,'provenance':{**_provenance(source),'index_token':token},'totals':totals,'ledger_run_id':report.get('run_id') if report else None,'expected_receipt_cents':expected,
                          'receipt_cycles':cycle_totals,'expected_receipt_cycles':expected_cycles,'source_proof':source_proof,
                          'built_at':time.time(),'identity_basis':'Known report person keys when available; otherwise reported name/state/ZIP identity. Distinct counts are not certified distinct people.'}
                target.execute('DROP TABLE receipts')
                target.execute('INSERT INTO metadata VALUES (?,?)',('manifest',json.dumps(metadata)))
                target.commit()
                if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('The analytical index failed integrity verification.')
            if _token(source)!=token:raise ValueError('The source changed while its analytical index was built. Retry with the current source.')
            os.replace(pending,destination)
            _progress(token,status='ready',message='Contribution index ready.',processed_records=processed)
    except Exception as error:
        _progress(token,status='error',failed_at=time.time(),message=str(error) if isinstance(error,ValueError) else 'The contribution index could not be prepared: '+type(error).__name__)
        if pending.exists():pending.unlink()


def _ready(source,wait=False,retry=False):
    token=_token(source);destination=CACHE_ROOT/(token+'.sqlite')
    if destination.exists():return destination,None
    with _lock:
        job=_jobs.get(token)
        if not job or (job.get('status')=='error' and (retry or time.time()-job.get('failed_at',0)>30)):
            _jobs[token]={'status':'preparing','message':'Preparing contribution data.','processed_records':0}
            thread=threading.Thread(target=_materialize,args=(source,token,destination),daemon=True,name='contribution-index')
            thread.start()
        else:thread=None
    if wait:
        while not destination.exists():
            with _lock:job=dict(_jobs[token])
            if job['status']=='error':raise ValueError(job['message'])
            time.sleep(.05)
        return destination,None
    with _lock:status=dict(_jobs[token])
    if status['status']=='error':raise ValueError(status['message'])
    return None,{'schema_version':VERSION,**status,'retry_after_ms':1500,'dataset':source['id'],'provenance':_provenance(source)}


def _filter(filters,allowed):
    clauses=[];params=[]
    if not isinstance(filters,list) or len(filters)>40:raise ValueError('Use at most 40 filters.')
    for item in filters:
        if not isinstance(item,dict) or item.get('field') not in allowed:raise ValueError('Choose a valid filter field.')
        field='"'+item['field']+'"';op=item.get('op');value=item.get('value')
        if isinstance(value,(dict,list)) and op!='in':raise ValueError('Filter values must be scalar values.')
        if op=='in' and isinstance(value,list) and any(isinstance(v,(dict,list)) for v in value):raise ValueError('Filter choices must be scalar values.')
        if op in ('eq','equals','ne','gte','lte','gt','lt'):
            clauses.append(field+' '+{'eq':'=','equals':'=','ne':'!=','gte':'>=','lte':'<=','gt':'>','lt':'<'}[op]+' ?');params.append(value)
        elif op=='in' and isinstance(value,list) and 0<len(value)<=1000:
            clauses.append(field+' IN ('+','.join('?' for _ in value)+')');params.extend(value)
        elif op=='contains':
            clauses.append('instr(lower('+field+'),lower(?))>0');params.append(str(value))
        else:raise ValueError('Choose a supported filter operation.')
    return ' AND '.join(clauses) or '1',params


def _row_filter(filters,column,extra=()):
    if not isinstance(filters,list):raise ValueError('Filters must be a list.')
    normalized=[]
    for item in filters:
        if not isinstance(item,dict):raise ValueError('A filter must be an object.')
        item=dict(item)
        if item.get('field') in ('amount','amount_cents'):
            item['field']=column
            values=item.get('value') if item.get('op')=='in' else [item.get('value')]
            if not isinstance(values,list) or any(isinstance(v,bool) or not isinstance(v,int) for v in values):raise ValueError('Money filter values must be integer cents.')
        normalized.append(item)
    return _filter(normalized,set(DIMENSIONS)|{column}|set(extra))


class _Median:
    def __init__(self):self.values=[]
    def step(self,v):
        if v is not None:self.values.append(v)
    def finalize(self):
        if not self.values:return None
        self.values.sort();n=len(self.values)
        return int(((Decimal(self.values[(n-1)//2])+Decimal(self.values[n//2]))/2).quantize(Decimal('1'),rounding=ROUND_HALF_EVEN))


def _expressions(amount):
    return {'amount':f'COALESCE(SUM({amount}),0)','gross_amount':f'COALESCE(SUM(MAX({amount},0)),0)',
        'refunds':f'COALESCE(SUM(MIN({amount},0)),0)','records':'COUNT(*)','contributors':'COUNT(DISTINCT contributor_key)',
        'average_amount':f'CAST(ROUND(AVG({amount})) AS INTEGER)','min_amount':f'MIN({amount})','max_amount':f'MAX({amount})','median_amount':f'median_cent({amount})'}


def _validate(request):
    if not isinstance(request,dict):raise ValueError('Contribution requests must be objects.')
    basis=request.get('basis','receipt')
    if basis not in BASES:raise ValueError('Choose a supported accounting basis.')
    dims=request.get('dimensions',['cycle'])
    measures=request.get('measures',['amount'])
    if not isinstance(dims,list) or len(dims)>8 or any(not isinstance(x,str) or x not in DIMENSIONS for x in dims) or len(set(dims))!=len(dims):raise ValueError('Choose up to eight distinct dimensions.')
    if not isinstance(measures,list) or not measures or any(not isinstance(x,str) or x not in MEASURES for x in measures) or len(set(measures))!=len(measures):raise ValueError('Choose valid distinct measures.')
    limit=request.get('limit',1000);offset=request.get('offset',0)
    if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=100000:raise ValueError('Query page size must be 1–100,000.')
    if isinstance(offset,bool) or not isinstance(offset,int) or offset<0:raise ValueError('Offset must be a nonnegative integer.')
    return basis,dims,measures,limit,offset


def query(report_directory,request):
    basis,dims,measures,limit,offset=_validate(request)
    source=_source(report_directory,request)
    path,pending=_ready(source,retry=request.get('retry') is True)
    if pending:return pending
    return _query_ready(path,request,source,basis,dims,measures,limit,offset)


def _query_ready(path,request,source,basis,dims,measures,limit,offset):
    column=BASES[basis][1]
    where,params=_row_filter(request.get('filters',[]),column)
    # Noncounted representations are drill-through evidence, not zero-valued
    # observations that distort means/counts in a chosen included basis.
    if basis!='raw':where+=' AND '+column+' != 0'
    expressions=_expressions(column)
    select=[f'"{d}"' for d in dims]+[expressions[m]+' AS "'+m+'"' for m in measures]
    # Carry amount/records privately when needed for aggregate-filter receipts.
    aggregate_filters=request.get('aggregate_filters',[])
    if not isinstance(aggregate_filters,list):raise ValueError('Aggregate filters must be a list.')
    filter_measures=[f.get('field') for f in aggregate_filters if isinstance(f,dict) and f.get('field') in MEASURES]
    all_measures=list(dict.fromkeys(measures+['amount','records','contributors']+filter_measures))
    select=[f'"{d}"' for d in dims]+[expressions[m]+' AS "'+m+'"' for m in all_measures]
    grouped='SELECT '+','.join(select)+' FROM facts WHERE '+where+(' GROUP BY '+','.join('"'+d+'"' for d in dims) if dims else '')
    having,having_params=_filter(request.get('aggregate_filters',[]),set(all_measures))
    sort=request.get('sort') or [{'field':'amount','direction':'desc'}]
    if not isinstance(sort,list) or len(sort)>8:raise ValueError('Choose a valid sort order.')
    order=[]
    for item in sort:
        if not isinstance(item,dict) or item.get('field') not in dims+all_measures or item.get('direction','asc') not in ('asc','desc'):raise ValueError('Choose valid sort fields and directions.')
        order.append('"'+item['field']+'" '+item.get('direction','asc').upper())
    order += ['"'+d+'" ASC' for d in dims]
    with closing(_ro(path)) as db:
        db.create_aggregate('median_cent',1,_Median)
        metadata=json.loads(db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
        totalrow=db.execute('SELECT '+','.join(expressions[k]+' AS "'+k+'"' for k in all_measures)+' FROM facts WHERE '+where,params).fetchone()
        totals=dict(totalrow)
        selected='SELECT * FROM ('+grouped+') WHERE '+having
        count=db.execute('SELECT COUNT(*) FROM ('+selected+')',params+having_params).fetchone()[0]
        result=[dict(row) for row in db.execute(selected+' ORDER BY '+','.join(order)+' LIMIT ? OFFSET ?',params+having_params+[limit,offset])]
        group_amount=db.execute('SELECT COALESCE(SUM(amount),0) FROM ('+selected+')',params+having_params).fetchone()[0]
        comparison=dict(db.execute('SELECT COALESCE(SUM(receipt_cents),0) AS receipt,COALESCE(SUM(original_cents),0) AS original_observed,COALESCE(SUM(supported_cents),0) AS original_supported,COALESCE(SUM(CASE WHEN unresolved=1 THEN ABS(raw_cents) ELSE 0 END),0) AS unresolved_gross_exposure,COUNT(CASE WHEN unresolved=1 THEN 1 END) AS unresolved_records FROM facts WHERE '+_row_filter(request.get('filters',[]),column)[0],params).fetchone())
        geography=dict(db.execute('SELECT COUNT(*) AS observations,COUNT(CASE WHEN zip5 != ? THEN 1 END) AS valid_zip_observations,COALESCE(SUM(CASE WHEN zip5 = ? THEN '+column+' ELSE 0 END),0) AS unmapped_amount FROM facts WHERE '+where,['','']+params).fetchone())
    notices=['Observed original giving remains provisional; supported giving is a subset, not a complete total.',metadata['identity_basis']]
    if basis=='raw':notices.append('Raw observations can repeat gifts, include excluded receipts and contain money stages that must not be added as giving.')
    if basis=='pac_activity':notices.append('PAC activity here is recipient-reported, not a complete outgoing Schedule B census.')
    if count>offset+len(result):notices.append('More groups are available. Totals cover all matching observations, not just this page.')
    if request.get('aggregate_filters'):notices.append('Totals and accounting comparisons describe row-filtered observations before the aggregate filter; grouped_amount_cents describes the retained groups.')
    return {'schema_version':VERSION,'status':'ready','dataset':source['id'],'basis':basis,'basis_label':BASES[basis][0],
        'columns':[{'id':d,'name':DIMENSIONS[d],'type':'category','role':'dimension'} for d in dims]+[{'id':m,'name':MEASURES[m],'type':'number','role':'measure','unit':'count' if m in ('records','contributors') else 'cents'} for m in measures],
        'rows':[{k:r[k] for k in dims+measures} for r in result],'totals':totals,'group_count':count,'offset':offset,'limit':limit,'has_more':offset+len(result)<count,
        'comparison':comparison,'geography':geography,'reconciliation':{'source_receipt_cents':metadata['totals']['receipt'],'expected_receipt_cents':metadata['expected_receipt_cents'],'receipt_matches_workbook':metadata['totals']['receipt']==metadata['expected_receipt_cents'],'filtered_amount_cents':totals['amount'],'grouped_amount_cents':group_amount,'aggregate_filters_applied':bool(request.get('aggregate_filters')),'conserved':group_amount==totals['amount'] if not request.get('aggregate_filters') else None},
        'provenance':{**metadata['provenance'],'ledger_run_id':metadata['ledger_run_id']},'notices':notices,'verified_unique':False}


def records(report_directory,request):
    basis,_,_,limit,offset=_validate(request)
    if limit>5000:raise ValueError('Drill-through page size must be at most 5,000.')
    if request.get('aggregate_filters'):raise ValueError('Drill through a selected group using its dimension filters.')
    source=_source(report_directory,request);path,pending=_ready(source,retry=request.get('retry') is True)
    if pending:return pending
    column=BASES[basis][1]
    where,params=_row_filter(request.get('filters',[]),column,{'source_id'})
    if not request.get('include_excluded',False) and basis!='raw':where+=' AND '+column+' != 0'
    with closing(_ro(path)) as db:
        count=db.execute('SELECT COUNT(*) FROM facts WHERE '+where,params).fetchone()[0]
        rows=[dict(r) for r in db.execute('SELECT *,'+column+' AS amount_cents FROM facts WHERE '+where+' ORDER BY date DESC,source_id LIMIT ? OFFSET ?',params+[limit,offset])]
    for row in rows:
        row['reasons']=json.loads(row['reasons']);row['evidence_ids']=json.loads(row['evidence_ids'])
        row.pop('contributor_key',None)
    return {'schema_version':VERSION,'status':'ready','dataset':source['id'],'basis':basis,'rows':rows,'total_records':count,'offset':offset,'limit':limit,'has_more':offset+len(rows)<count,'provenance':{**_provenance(source),'index_token':_token(source)},'amount_unit':'cents'}
