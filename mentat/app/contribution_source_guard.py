"""Bind recovered source universes to the original published financial facts.

Metadata repairs may change a database hash. They may not silently add, remove,
or change source financial identities, including records excluded from a report.
"""
from collections import Counter
from contextlib import closing
from datetime import date
from decimal import Decimal
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from receipt_path import receipt_path

PROJECT=Path(__file__).resolve().parents[1]
ACCEPTED_ROOT=PROJECT/'state/accepted-report-sources'
CACHE_ROOT=PROJECT/'state/contribution-source-proofs'
CYCLES=(2022,2024,2026)
VERSION=1


def _stamp(path):
    path=Path(path).resolve();stat=path.stat()
    return str(path),stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns


LOADED_ALGORITHM_STAMP=_stamp(__file__)


@lru_cache(maxsize=128)
def _hash(stamp):
    with open(stamp[0],'rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def _quiescent(path):
    for suffix in ('-wal','-journal'):
        sidecar=Path(str(path)+suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError('The recovered source has active uncheckpointed writes.')


def _financial_facts(database,industry):
    # Same canonical financial fields as report_source_guard.source_facts; use
    # a narrow SQL projection so a large metadata payload is never materialized.
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        for table in ('contributions','corporate_giving'):
            for sid,committee,contributor,day,amount in db.execute(
                    'SELECT sub_id,committee_id,contributor_name,contribution_receipt_date,contribution_receipt_amount FROM '+table+' WHERE industry=?',(industry,)):
                day=str(day or '')[:10]
                try:year=date.fromisoformat(day).year
                except ValueError as error:raise ValueError('A recovered source has an invalid contribution date.') from error
                cycle=year+year%2
                if cycle not in CYCLES:continue
                if not sid or amount is None or isinstance(amount,bool):raise ValueError('A recovered financial source identity or amount is missing.')
                number=Decimal(str(amount))
                if not number.is_finite():raise ValueError('A recovered financial source amount is invalid.')
                facts={'sub_id':sid or '','committee_id':committee or '','contributor_name':contributor or '',
                       'date':day,'amount_cents':int((number*100).quantize(Decimal('1'))),'cycle':cycle}
                yield str(sid),json.dumps(facts,sort_keys=True)


def _compare(database,snapshot,industry):
    with closing(sqlite3.connect(Path(snapshot).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        baseline=dict(db.execute('SELECT id,raw FROM facts'))
    counts=Counter();seen={}
    for sid,raw in _financial_facts(database,industry):
        counts['source_observations']+=1
        digest=hashlib.sha256(raw.encode()).digest()
        if sid in seen:
            counts['repeated_source_observations']+=1
            if seen[sid]!=digest:counts['conflicting_source_copies']+=1
            continue
        seen[sid]=digest;counts['source_ids']+=1
        old=baseline.pop(sid,None)
        counts['added' if old is None else 'changed' if old!=raw else 'unchanged']+=1
    counts['missing']=len(baseline)
    if any(counts[k] for k in ('added','changed','missing','conflicting_source_copies')):
        raise ValueError('Recovered source financial facts differ from the published source universe '
                         '('+', '.join(str(counts[k])+' '+k for k in ('added','changed','missing','conflicting_source_copies'))+'). Reconcile the source lineage before charting.')
    return {k:counts[k] for k in ('source_ids','source_observations','repeated_source_observations','unchanged','added','changed','missing','conflicting_source_copies')}


@lru_cache(maxsize=32)
def _verify_cached(database_stamp,pointer_stamp,snapshot_stamp,current_hash,original_hash,industry,algorithm_hash,cache_root):
    for stamp in (database_stamp,snapshot_stamp):_quiescent(stamp[0])
    accepted=_read(pointer_stamp[0])
    if (accepted.get('status')!='verified' or accepted.get('industry')!=industry
            or tuple(sorted(accepted.get('cycles',[])))!=CYCLES or accepted.get('database_sha256')!=original_hash
            or receipt_path(accepted.get('snapshot','')).resolve()!=Path(snapshot_stamp[0])):
        raise ValueError('The accepted financial snapshot does not match the original published source scope.')
    if _hash(database_stamp)!=current_hash:raise ValueError('The recovered database changed while its source lineage was checked.')
    snapshot_hash=_hash(snapshot_stamp)
    if snapshot_hash!=accepted.get('snapshot_sha256'):raise ValueError('The accepted financial source snapshot failed its fingerprint check.')
    identity={'version':VERSION,'algorithm_sha256':algorithm_hash,'industry':industry,'cycles':list(CYCLES),
              'database_sha256':current_hash,'original_database_sha256':original_hash,
              'accepted_pointer_sha256':_hash(pointer_stamp),'snapshot_sha256':snapshot_hash}
    token=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    cache=Path(cache_root)/(token+'.json')
    publish=False
    try:
        proof=_read(cache)
        if proof.get('identity')!=identity or proof.get('full_financial_universe_preserved') is not True:
            raise ValueError('Invalid cached source proof')
        counts=proof['counts']
        if any(counts[k]!=0 for k in ('added','changed','missing','conflicting_source_copies')):raise ValueError('Invalid cached source counts')
        if not (isinstance(counts['source_ids'],int) and counts['source_ids']>=0 and counts['unchanged']==counts['source_ids']
                and counts['source_observations']>=counts['source_ids']):raise ValueError('Invalid cached source counts')
    except (OSError,ValueError,KeyError,TypeError):
        counts=_compare(database_stamp[0],snapshot_stamp[0],industry)
        proof={'identity':identity,'counts':counts,'full_financial_universe_preserved':True}
        publish=True
    for stamp in (database_stamp,pointer_stamp,snapshot_stamp):
        if _stamp(stamp[0])!=stamp:raise ValueError('Source lineage changed during verification. Retry against stable source files.')
    for stamp in (database_stamp,snapshot_stamp):_quiescent(stamp[0])
    if publish:
        cache.parent.mkdir(parents=True,exist_ok=True)
        pending=cache.with_suffix('.'+uuid.uuid4().hex+'.pending')
        try:
            pending.write_text(json.dumps(proof,sort_keys=True),encoding='utf-8')
            os.replace(pending,cache)
        finally:
            if pending.exists():pending.unlink()
    return {**proof,'snapshot':snapshot_stamp[0],'accepted_pointer':pointer_stamp[0],'proof_token':token}


def verify(source):
    """Validate a recovered source; exact original database hashes need no repair proof."""
    if _stamp(__file__)!=LOADED_ALGORITHM_STAMP:
        raise ValueError('Source verification code changed. Restart the app to load the current source-verification rules.')
    if not source.get('hydration_manifest'):
        database_stamp=_stamp(source['database']);_quiescent(database_stamp[0])
        expected=source['database_sha256'];original=source.get('original_database_sha256',expected)
        if expected!=original or _hash(database_stamp)!=original:
            raise ValueError('The original source database failed its published fingerprint check.')
        if _stamp(database_stamp[0])!=database_stamp:raise ValueError('The original source changed during verification.')
        _quiescent(database_stamp[0])
        return {'full_financial_universe_preserved':True,'basis':'original_database_fingerprint','database_sha256':original}
    industry=source['industry'];slug=re.sub(r'[^a-z0-9]+','_',industry.lower()).strip('_')
    pointer=ACCEPTED_ROOT/slug/'2022-2024-2026/current.json'
    pointer_stamp=_stamp(pointer);accepted=_read(pointer)
    snapshot=receipt_path(accepted['snapshot']).resolve()
    if not snapshot.is_relative_to(ACCEPTED_ROOT.resolve()):raise ValueError('The accepted source snapshot is outside its preserved lineage directory.')
    database_stamp=_stamp(source['database']);snapshot_stamp=_stamp(snapshot)
    _quiescent(database_stamp[0]);_quiescent(snapshot_stamp[0])
    # The code half of the proof key reads CRLF as LF, as the index token does: every checkout names the same proof.
    import contribution_data as data
    proof=_verify_cached(database_stamp,pointer_stamp,snapshot_stamp,source['database_sha256'],
                         source['original_database_sha256'],industry,data._rule_digest_stamped(LOADED_ALGORITHM_STAMP),str(CACHE_ROOT))
    # A WAL can appear without changing the database-file stamp or the LRU key.
    _quiescent(database_stamp[0]);_quiescent(snapshot_stamp[0])
    return proof
