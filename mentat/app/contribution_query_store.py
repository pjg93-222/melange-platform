"""Disposable query acceleration, derived from immutable contribution indexes.

Amounts and observations are copied, never reclassified or aggregated here.
The small fact table keeps common chart scans away from long audit/detail text.
"""
from collections import OrderedDict
from contextlib import closing,contextmanager
import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

import contribution_data as data

ROOT=data.PROJECT/'state/contribution-query-cache'
DEFAULT_ROOT=ROOT
# Response budget per responses/<namespace> folder (prewarm.warm_capacity models the same limits).
RESPONSE_KEEP_FILES=1024
RESPONSE_KEEP_BYTES=512*1024*1024
# A process recounts a folder after this many of its own writes, bytes or seconds (others write too).
RECOUNT_WRITES=64
RECOUNT_BYTES=32*1024*1024
RECOUNT_SECONDS=300
# A response younger than this is evicted only after every older one: its access may still be buffered
# in the process that wrote it.
FRESH_SECONDS=300
# prewarm.DEMOTED_BASE_NS and DEMOTED_CEILING_NS: a response the warmer wrote that no click has used
# carries an mtime in this range (prewarm.demote; prewarm.promote moves a used one back out).
WARM_MTIME_NS=(946684800*10**9,946684800*10**9+40000*10**12)
CORE=('cycle','year','company','date','zip5','state')
DETAIL=tuple(k for k in data.DIMENSIONS if k not in CORE)
MONEY=('raw_cents','receipt_cents','original_cents','supported_cents','pac_cents','unresolved')
LOADED_STAMP=data._stamp(__file__)
LOADED_SHA=data._hash(__file__)
_mutex=threading.RLock()
_locks={}
_memory=OrderedDict()
_memory_bytes=0
_verified={}
_usage={}  # folder -> [files, bytes, writes since recount, bytes since recount, recounted (monotonic)]
_ledger_module=[]
MEMORY_LIMIT=32*1024*1024
ENTRY_LIMIT=24*1024*1024

def _loaded():
    if data._stamp(__file__)!=LOADED_STAMP:raise ValueError('Query cache code changed. Restart the app to load the current rules.')

def _key(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def source_stable(path,stamp):
    try:
        if data._stamp(path)!=stamp:return False
        return not any(p.exists() and p.stat().st_size for suffix in ('-wal','-journal') if (p:=Path(str(path)+suffix)))
    except OSError:return False

@contextmanager
def _lock(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with _mutex:mutex=_locks.setdefault(str(path),threading.RLock())
    with mutex:
        with path.open('a+b') as stream:
            if stream.tell()==0:stream.write(b'0');stream.flush()
            stream.seek(0)
            if os.name=='nt':
                import msvcrt
                while True:
                    try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1);break
                    except OSError:time.sleep(.025)
                try:yield
                finally:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(stream,fcntl.LOCK_EX)
                try:yield
                finally:fcntl.flock(stream,fcntl.LOCK_UN)

def _atomic(path,payload):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    pending=path.with_suffix('.'+uuid.uuid4().hex+'.pending')
    try:pending.write_bytes(payload);os.replace(pending,path)
    finally:
        if pending.exists():pending.unlink()

def _readonly(path):
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
    return db


def _projection_signature(path):
    # Same-size SQLite commits can share Windows mtime/creation-time stamps.
    # The database header includes SQLite's transaction change counter, so a
    # completed SQL write cannot reuse verification solely on that stat tuple.
    stamp=data._stamp(path)
    with Path(path).open('rb') as stream:header=stream.read(100)
    return stamp,header


def _current_hash(path):
    # Do not use data._hash here: its stat-keyed digest can survive the exact
    # timestamp collision that invalidated the projection's header signature.
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def _valid(path,proof_path,identity):
    if path.exists() and not source_stable(path,data._stamp(path)):
        raise ValueError('The query projection has active writes; retry after they finish.')
    try:
        stamp=(_projection_signature(path),data._stamp(proof_path))
        with _mutex:
            if _verified.get(str(path))==stamp:return True
        proof=json.loads(proof_path.read_text())
        if not isinstance(proof,dict):return False
        if proof['identity']!=identity or _current_hash(path)!=proof['sha256']:return False
        if not source_stable(path,stamp[0][0]) or (_projection_signature(path),data._stamp(proof_path))!=stamp:return False
        with _mutex:_verified[str(path)]=stamp
        return True
    except (OSError,ValueError,KeyError,TypeError):return False

def view(source_path):
    """Return an exactly copied, narrower query index; coordinate all builders."""
    _loaded();source_path=Path(source_path);stamp=data._stamp(source_path)
    identity={'source_stamp':list(stamp),'projection_code':LOADED_SHA}
    key=_key(identity);path=ROOT/'views'/(key+'.sqlite');proof_path=path.with_suffix('.json')
    if not source_stable(source_path,stamp):raise ValueError('The contribution index has active writes.')
    if _valid(path,proof_path,identity):return path
    with _lock(path.with_suffix('.lock')):
        if not source_stable(source_path,stamp):raise ValueError('Contribution index changed while preparing its query view.')
        if _valid(path,proof_path,identity):return path
        pending=path.with_suffix('.'+uuid.uuid4().hex+'.pending');started=time.perf_counter()
        try:
            original_sha=data._hash(source_path)
            with closing(sqlite3.connect(pending,uri=True)) as db:
                db.execute('PRAGMA journal_mode=OFF');db.execute('PRAGMA synchronous=OFF')
                db.execute('ATTACH DATABASE ? AS original',(source_path.resolve().as_uri()+'?mode=ro',))
                metadata=json.loads(db.execute("SELECT value FROM original.metadata WHERE key='manifest'").fetchone()[0])
                db.execute('CREATE TABLE donors(id INTEGER PRIMARY KEY,value TEXT UNIQUE NOT NULL)')
                db.execute('INSERT INTO donors(value) SELECT DISTINCT contributor_key FROM original.facts WHERE contributor_key IS NOT NULL')
                db.execute('CREATE TABLE facts(fact_id INTEGER PRIMARY KEY,'+','.join('"'+k+'" TEXT NOT NULL' for k in CORE)+',contributor_key INTEGER,'+','.join(k+' INTEGER NOT NULL' for k in MONEY)+')')
                db.execute('INSERT INTO facts SELECT s.rowid,'+','.join('s."'+k+'"' for k in CORE)+',d.id,'+','.join('s.'+k for k in MONEY)+' FROM original.facts s LEFT JOIN donors d ON d.value=s.contributor_key')
                db.execute('CREATE TABLE detail(fact_id INTEGER PRIMARY KEY,'+','.join('"'+k+'" TEXT NOT NULL' for k in DETAIL)+')')
                db.execute('INSERT INTO detail SELECT rowid,'+','.join('"'+k+'"' for k in DETAIL)+' FROM original.facts')
                db.execute('CREATE VIEW facts_full AS SELECT f.*,'+','.join('d."'+k+'"' for k in DETAIL)+' FROM facts f JOIN detail d ON d.fact_id=f.fact_id')
                db.executescript('CREATE INDEX facts_company_cycle ON facts(company,cycle); CREATE INDEX facts_zip ON facts(zip5); CREATE INDEX facts_state ON facts(state); CREATE INDEX detail_recipient ON detail(committee_id);')
                # An identity-preserving join proves each copied field, not just
                # balanced totals. Donor IDs must decode to the exact source key.
                checks=['f.fact_id IS NULL','d.fact_id IS NULL','f.contributor_key IS NOT NULL AND ids.value IS NULL']
                checks += ['f."'+k+'" IS NOT s."'+k+'"' for k in CORE+MONEY]
                checks += ['d."'+k+'" IS NOT s."'+k+'"' for k in DETAIL]
                checks += ['ids.value IS NOT s.contributor_key']
                mismatch=db.execute('SELECT COUNT(*) FROM original.facts s LEFT JOIN facts f ON f.fact_id=s.rowid LEFT JOIN detail d ON d.fact_id=s.rowid LEFT JOIN donors ids ON ids.id=f.contributor_key WHERE '+' OR '.join(checks)).fetchone()[0]
                count=db.execute('SELECT COUNT(*) FROM original.facts').fetchone()[0]
                if mismatch or db.execute('SELECT COUNT(*) FROM facts').fetchone()[0]!=count or db.execute('SELECT COUNT(*) FROM detail').fetchone()[0]!=count:raise ValueError('The query projection does not preserve every contribution field.')
                db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                db.execute('INSERT INTO metadata VALUES (?,?)',('manifest',json.dumps(metadata)))
                db.commit()
                if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('The query projection failed integrity verification.')
            if not source_stable(source_path,stamp):raise ValueError('Contribution index changed while preparing its query view.')
            proof={'identity':identity,'sha256':data._hash(pending),'source_index_sha256':original_sha,'fact_count':count,'field_mismatches':mismatch,'seconds':round(time.perf_counter()-started,6)}
            os.replace(pending,path);_atomic(proof_path,json.dumps(proof,sort_keys=True).encode())
            with _mutex:_verified[str(path)]=(_projection_signature(path),data._stamp(proof_path))
            return path
        finally:
            if pending.exists():pending.unlink()

def tables(dimensions,filters):
    fields={f.get('field') for f in filters if isinstance(f,dict)}
    return ('facts_full' if (set(dimensions)|fields)&set(DETAIL) else 'facts',
            'facts_full' if fields&set(DETAIL) else 'facts')

def _remember(key,payload):
    global _memory_bytes
    if len(payload)>ENTRY_LIMIT:return
    with _mutex:
        previous=_memory.pop(key,None)
        if previous:_memory_bytes-=len(previous)
        _memory[key]=payload;_memory_bytes+=len(payload)
        while _memory_bytes>MEMORY_LIMIT:
            _,old=_memory.popitem(last=False);_memory_bytes-=len(old)

def _ledger():
    # access_stats is looked up by name, not imported by a statement: it ranks what is kept and never
    # decides an answer, so it stays out of every code identity that follows import statements
    # (response_cache.module_closure). A cache outside the live state tree never writes the live ledger.
    if not _ledger_module:
        try:_ledger_module.append(importlib.import_module('access_stats'))
        except Exception:_ledger_module.append(None)
    ledger=_ledger_module[0]
    if ledger is None:return None
    try:
        live=os.path.normcase(os.path.abspath(ROOT))==os.path.normcase(os.path.abspath(DEFAULT_ROOT))
        return ledger if live or not ledger.is_default() else None
    except Exception:return None

def _note(namespace,key,cost_s=None,size=None):
    ledger=_ledger()
    if ledger is not None:ledger.record('store/'+namespace,key,cost_s=cost_s,size=size)

def _responses(directory):
    """(name, mtime_ns, size) of each response JSON in one folder, from one directory listing (on
    Windows os.scandir carries size and mtime in the listing: no stat call per file)."""
    found=[]
    with os.scandir(directory) as items:
        for item in items:
            if not item.name.endswith('.json'):continue
            try:
                if not item.is_file(follow_symlinks=False):continue
                info=item.stat(follow_symlinks=False)
            except OSError:continue
            found.append((item.name,info.st_mtime_ns,info.st_size))
    return found

def _evictions(files,scores,keep=None,keep_files=None,keep_bytes=None,now_ns=None):
    """Names to remove, lowest value first, until at most keep_files responses and keep_bytes remain:
    first responses the warmer wrote that no click has used (oldest plan day, lowest priority first),
    then the lowest access_stats score (ties: oldest), responses younger than FRESH_SECONDS last.
    `keep`, the response just written, is never removed."""
    keep_files=RESPONSE_KEEP_FILES if keep_files is None else keep_files
    keep_bytes=RESPONSE_KEEP_BYTES if keep_bytes is None else keep_bytes
    count=len(files);total=sum(size for _,_,size in files)
    if count<=keep_files and total<=keep_bytes:return []
    fresh=(time.time_ns() if now_ns is None else now_ns)-FRESH_SECONDS*10**9
    def rank(item):
        name,mtime,_=item
        if WARM_MTIME_NS[0]<=mtime<WARM_MTIME_NS[1]:return (0,0.0,mtime,name)
        return (2 if mtime>=fresh else 1,scores.get(name[:-5],0.0),mtime,name)
    removed=[]
    for name,_,size in sorted((item for item in files if item[0]!=keep),key=rank):
        if count<=keep_files and total<=keep_bytes:break
        removed.append(name);count-=1;total-=size
    return removed

def _prune(directory,keep=None):
    """Hold one responses/<namespace> folder to RESPONSE_KEEP_FILES and RESPONSE_KEEP_BYTES in value
    order (_evictions). Only disposable response JSON is removed: lock files keep cross-process lock
    identities, and views, sources and indexes are never touched. Returns the names removed."""
    directory=Path(directory);removed=[]
    try:
        if not directory.resolve().is_relative_to((ROOT/'responses').resolve()):return removed
        files=_responses(directory)
        count=len(files);total=sum(size for _,_,size in files)
        if count>RESPONSE_KEEP_FILES or total>RESPONSE_KEEP_BYTES:
            ledger=_ledger()
            scores=ledger.scores('store/'+directory.name) if ledger is not None else {}
            sizes={name:size for name,_,size in files}
            for name in _evictions(files,scores,keep):
                try:(directory/name).unlink(missing_ok=True)
                except OSError:continue
                removed.append(name);count-=1;total-=sizes[name]
        with _mutex:_usage[str(directory)]=[count,total,0,0,time.monotonic()]
    except OSError:pass
    return removed

def _wrote(directory,name,size):
    """Account one response this process wrote; prune only when the running count says the folder may
    be over its cap, or this process has not recounted it for RECOUNT_WRITES/_BYTES/_SECONDS."""
    with _mutex:
        usage=_usage.get(str(directory))
        if usage is not None:
            usage[0]+=1;usage[1]+=size;usage[2]+=1;usage[3]+=size
            due=(usage[0]>RESPONSE_KEEP_FILES or usage[1]>RESPONSE_KEEP_BYTES or usage[2]>=RECOUNT_WRITES
                 or usage[3]>=RECOUNT_BYTES or time.monotonic()-usage[4]>=RECOUNT_SECONDS)
    if usage is None or due:_prune(directory,keep=name)

def cached(namespace,identity,compute,stable=lambda:True):
    """Return fresh object copies and publish only stable, complete responses."""
    _loaded();key=_key([LOADED_SHA,namespace,identity]);path=ROOT/'responses'/namespace/(key+'.json')
    def read():
        with _mutex:
            payload=_memory.get(key)
            if payload is not None:_memory.move_to_end(key);return payload
        try:
            envelope=json.loads(path.read_bytes())
            if not isinstance(envelope,dict):return None
            payload=envelope['payload'].encode('utf-8')
            if envelope['key']!=key or hashlib.sha256(payload).hexdigest()!=envelope['sha256']:return None
            _remember(key,payload);return payload
        except (OSError,ValueError,KeyError,AttributeError,TypeError):return None
    if not stable():raise ValueError('Contribution source changed before its query completed.')
    payload=read();built=None
    if payload is None:
        with _lock(path.with_suffix('.lock')):
            payload=read()
            if payload is None:
                started=time.perf_counter();result=compute()
                if not stable():raise ValueError('Contribution source changed while computing its query.')
                payload=json.dumps(result,ensure_ascii=False,separators=(',',':')).encode('utf-8')
                built=(time.perf_counter()-started,None)
                if len(payload)<=ENTRY_LIMIT:
                    envelope={'key':key,'sha256':hashlib.sha256(payload).hexdigest(),'payload':payload.decode('utf-8')}
                    stored=json.dumps(envelope,separators=(',',':')).encode('utf-8')
                    _atomic(path,stored);_remember(key,payload);built=(built[0],len(stored))
                    _wrote(path.parent,path.name,len(stored))
    if not stable():raise ValueError('Contribution source changed while reading its query cache.')
    _note(namespace,key,*(built or ()))
    return json.loads(payload)
