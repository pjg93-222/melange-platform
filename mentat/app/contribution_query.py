"""Read-only query planning over the validated contribution index.

The accounting index remains authoritative. This layer computes one grouped
selection and one population summary without rebuilding monetary decisions.

No request holds for a build (batch 2, 2026-09-25). While the query view of a
request's index builds, /api/contributions/query answers at once with
{"status": "building", "phase": "query_view", ...}; while the records sidecar
(records_sidecar.py) builds, /api/contributions/records answers the same with
phase "records_sidecar". One build per key runs, across threads (_jobs) and
processes (a proclock marker). A build that fails is reported as an error on
the next poll, and a request carrying "retry": true starts it again. Every
ready answer of both routes carries "status": "ready".
"""
from collections import OrderedDict
from contextlib import closing, contextmanager
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import threading
import time

import contribution_data as data
import contribution_query_store as store
import contribution_semantics as semantics
import proclock
import records_sidecar as sidecar

LOADED_STAMP=data._stamp(__file__)
LOADED_SHA256=data._hash(__file__)
_metadata_memory_slot=threading.Semaphore(1)
_log=logging.getLogger('mentat.contribution_query')
# Background builds: key -> {'phase','status' ('running'|'failed'),'started' (epoch s),'processed',
# 'total','message','failed_at','sticky'}. A failed job is reported until a retry, or FAILED_RESTART_SECONDS
# on; a sticky one (a built view that did not verify, 2026-09-25) is reported until a retry.
_jobs={}
_jobs_lock=threading.Lock()
FAILED_RESTART_SECONDS=30
PROGRESS_WRITE_SECONDS=1.0
VIEW_MESSAGE='Preparing the query view for this report. The chart appears when it is ready.'
RECORDS_MESSAGE='Ordering the source records for inspection.'
# M6: reported-metadata values of individual observations, read once per index token.
# (token, raw source stamp, semantics, query code, source_id) -> {reported field: value}
_raw_memo=OrderedDict()
_raw_memo_lock=threading.Lock()
RAW_MEMO_LIMIT=50000   # about 15 MB at most; a page holds at most 5,000 observations


@contextmanager
def _metadata_workspace(db,source,refs):
    # Avoid disk-backed automatic-index churn on narrow metadata views. Only
    # one such workspace runs at a time; wider/larger sources retain SQLite's
    # disk-backed temporary storage. This does not modify either source DB.
    keys=set(refs)&set(semantics.RAW)
    source_path=source.get('database')
    eligible=bool(keys and len(keys)<=4 and source_path and Path(source_path).stat().st_size<=1024**3)
    if not eligible:
        yield
        return
    with _metadata_memory_slot:
        db.execute('PRAGMA temp_store=MEMORY')
        db.execute('PRAGMA main.cache_size=-32768')
        yield


def _check_loaded():
    semantics.check()
    sidecar.check()   # records_sidecar.py shapes the records answer too (2026-09-25)
    if data._stamp(__file__)!=LOADED_STAMP:
        raise ValueError('Contribution query code changed. Restart the app to load the current query rules.')


def _scan_table(filters,table='facts'):
    # GROUP BY ZIP otherwise encourages ~N random lookups through a noncovering
    # index into wide facts. Preserve useful selective equality/range indexes.
    selective=any(isinstance(f,dict) and f.get('field') in ('company','zip5','state','committee_id')
                  and f.get('op') in ('eq','equals','in') for f in filters)
    if table!='facts':return table
    return 'facts' if selective else 'facts NOT INDEXED'


def _plan(request,basis,dims,measures,limit,offset,group_table='facts',summary_table='facts',raw_relation=None):
    column=data.BASES[basis][1]
    filters=request.get('filters',[])
    row_where,params=semantics.row_filter(filters,column)
    include=column+' != 0' if basis!='raw' else '1'
    where=row_where+' AND '+include
    aggregate_filters=request.get('aggregate_filters',[])
    if not isinstance(aggregate_filters,list):raise ValueError('Aggregate filters must be a list.')
    filter_measures=[f.get('field') for f in aggregate_filters if isinstance(f,dict) and f.get('field') in semantics.MEASURES]
    all_measures=list(dict.fromkeys(measures+['amount','records','contributors']+filter_measures))
    having,having_params=data._filter(aggregate_filters,set(all_measures))
    sort=request.get('sort') or [{'field':'amount','direction':'desc'}]
    if not isinstance(sort,list) or len(sort)>8:raise ValueError('Choose a valid sort order.')
    order=[];sort_measures=[]
    for item in sort:
        if not isinstance(item,dict) or item.get('field') not in dims+all_measures or item.get('direction','asc') not in ('asc','desc'):
            raise ValueError('Choose valid sort fields and directions.')
        field='CAST(amount_bin AS INTEGER)' if item['field']=='amount_bin' else '"'+item['field']+'"'
        order.append(field+' '+item.get('direction','asc').upper())
        if item['field'] in semantics.MEASURES:sort_measures.append(item['field'])
    order+=['"'+d+'" ASC' for d in dims]
    group_measures=list(dict.fromkeys(measures+['amount']+filter_measures+sort_measures))
    expressions=semantics.expressions(column)
    select=['"'+d+'"' for d in dims]+[expressions[m]+' AS "'+m+'"' for m in group_measures]
    refs=dims+all_measures+[f.get('field') for f in filters if isinstance(f,dict)]
    group_table=semantics.table(group_table,refs,request,raw_relation=raw_relation)
    summary_table=semantics.table(summary_table,all_measures+[f.get('field') for f in filters if isinstance(f,dict)],request,raw_relation=raw_relation)
    grouped='SELECT '+','.join(select)+' FROM '+_scan_table(filters,group_table)+' WHERE '+where+(' GROUP BY '+','.join('"'+d+'"' for d in dims) if dims else '')
    fields=dims+measures
    # A materialized CTE is shared by the summary and sorted page. Rank fixes
    # output order explicitly while permitting summary results on empty pages.
    grouped_sql='''WITH grouped AS MATERIALIZED ('''+grouped+'''),
        selected AS MATERIALIZED (SELECT * FROM grouped WHERE '''+having+'''),
        summary AS (SELECT COUNT(*) AS group_count,COALESCE(SUM(amount),0) AS grouped_amount FROM selected),
        page AS (SELECT *,ROW_NUMBER() OVER(ORDER BY '''+','.join(order)+''') AS _page_rank FROM selected
                 ORDER BY '''+','.join(order)+''' LIMIT ? OFFSET ?)
        SELECT 0 AS _page_rank,group_count AS _group_count,grouped_amount AS _grouped_amount,'''+','.join('NULL AS "'+f+'"' for f in fields)+''' FROM summary
        UNION ALL SELECT _page_rank,NULL,NULL,'''+','.join('"'+f+'"' for f in fields)+''' FROM page ORDER BY _page_rank'''
    selected_amount=column if basis=='raw' else 'CASE WHEN '+include+' THEN '+column+' END'
    totals_expr=semantics.expressions(selected_amount)
    totals_expr['records']='COUNT(*)' if basis=='raw' else 'COUNT(CASE WHEN '+include+' THEN 1 END)'
    totals_expr['contributors']='COUNT(DISTINCT contributor_key)' if basis=='raw' else 'COUNT(DISTINCT CASE WHEN '+include+' THEN contributor_key END)'
    summary_select=[totals_expr[m]+' AS "total__'+m+'"' for m in all_measures]
    comparison_expr={'receipt':'COALESCE(SUM(receipt_cents),0)',
        'original_observed':'COALESCE(SUM(original_cents),0)',
        'original_supported':'COALESCE(SUM(supported_cents),0)',
        'unresolved_gross_exposure':'COALESCE(SUM(CASE WHEN unresolved=1 THEN ABS(raw_cents) ELSE 0 END),0)',
        'unresolved_records':'COUNT(CASE WHEN unresolved=1 THEN 1 END)'}
    summary_select += [expr+' AS "comparison__'+key+'"' for key,expr in comparison_expr.items()]
    summary_select += ['COUNT(CASE WHEN ('+include+') AND zip5!=\'\' THEN 1 END) AS geo__valid',
                      'COALESCE(SUM(CASE WHEN ('+include+') AND zip5=\'\' THEN '+column+' ELSE 0 END),0) AS geo__unmapped']
    summary_sql='SELECT '+','.join(summary_select)+' FROM '+_scan_table(filters,summary_table)+' WHERE '+row_where
    return {'grouped_sql':grouped_sql,'grouped_params':params+having_params+[limit,offset],
            'summary_sql':summary_sql,'summary_params':params,'all_measures':all_measures,
            'comparison_fields':list(comparison_expr),'group_measures':group_measures}


# ----------------------------------------------------------------------------- background builds
class _ViewPending(Exception):
    """The query view of a route request's index is not ready; the route answers 'building'."""


class _ViewUnverified(ValueError):
    """A view build finished but its view does not verify. An automatic restart would repeat that every
    FAILED_RESTART_SECONDS, so the failed job stays until a request carries retry: true (2026-09-25)."""


def _building_root():
    return Path(store.ROOT)/'building'


def _utc_seconds(text):
    try:
        moment=datetime.fromisoformat(text)
        return moment.timestamp()
    except (TypeError,ValueError):
        return None


def _read_progress(path):
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):
        return {}


def _write_progress(path,job):
    try:
        payload=json.dumps({key:job.get(key) for key in ('processed','total','message','started')}).encode('utf-8')
        pending=Path(str(path)+'.'+str(os.getpid())+'.tmp')
        pending.write_bytes(payload);os.replace(pending,path)
    except OSError:
        pass


def _snapshot(job):
    return {key:job.get(key) for key in ('phase','status','started','processed','total','message')}


def _job(phase,key,marker,work,message,retry=False,progress=None):
    """Start or report the one build for `key` and return a snapshot of it. A live holder in another
    process is reported, not joined. A failed build raises its message (ValueError) until a retry, or
    until FAILED_RESTART_SECONDS after it failed; a sticky failure (_ViewUnverified) only until a retry."""
    with _jobs_lock:
        job=_jobs.get(key)
        if job is not None and job['status']=='running':return _snapshot(job)
        if job is not None and job['status']=='failed':
            if not (retry or (not job.get('sticky') and time.time()-job['failed_at']>FAILED_RESTART_SECONDS)):raise ValueError(job['message'])
            del _jobs[key]
        body=proclock.claim(marker,key,extra={'phase':phase})
        if body is None:
            found=proclock.inspect(marker)
            info=(found.info if found is not None else None) or {}
            seen=_read_progress(progress) if progress else {}
            started=seen.get('started') if isinstance(seen.get('started'),(int,float)) else _utc_seconds(info.get('started_utc'))
            return {'phase':phase,'status':'elsewhere','started':started or time.time(),
                    'processed':seen.get('processed') or 0,'total':seen.get('total'),'message':seen.get('message') or message}
        job={'phase':phase,'status':'running','started':time.time(),'processed':0,'total':None,'message':message}
        _jobs[key]=job
    thread=threading.Thread(target=_run,args=(key,marker,body,work,job,progress),daemon=True,
                            name='mentat-'+phase.replace('_','-'))
    thread.start()
    return _snapshot(job)


def _run(key,marker,body,work,job,progress):
    written=[0.0]
    def report(processed,total,message=None):
        with _jobs_lock:
            job['processed']=int(processed or 0);job['total']=int(total) if isinstance(total,int) else None
            if message:job['message']=message
        now=time.monotonic()
        if progress and now-written[0]>=PROGRESS_WRITE_SECONDS:
            written[0]=now;_write_progress(progress,job)
    try:
        if progress:_write_progress(progress,job)
        work(report)
    except BaseException as error:  # noqa: BLE001 - a failed build is reported on the next poll
        message=str(error) if isinstance(error,ValueError) else 'The '+job['phase'].replace('_',' ')+' could not be prepared: '+type(error).__name__
        _log.warning('Background %s build failed: %s',job['phase'],message)
        with _jobs_lock:job.update(status='failed',failed_at=time.time(),message=message,sticky=isinstance(error,_ViewUnverified))
    else:
        with _jobs_lock:
            if _jobs.get(key) is job:del _jobs[key]
    finally:
        if progress:
            try:Path(progress).unlink(missing_ok=True)
            except OSError:pass
        proclock.release(marker,body)


def _retry_after(elapsed_ms):
    return 300 if elapsed_ms<2000 else 750 if elapsed_ms<10000 else 1500


def _building(job,source):
    """The agreed pending answer (Chart Studio polls on it)."""
    elapsed=max(0,int((time.time()-(job.get('started') or time.time()))*1000))
    total=job.get('total')
    return {'schema_version':data.VERSION,'status':'building','phase':job['phase'],'message':job.get('message') or '',
            'processed_records':int(job.get('processed') or 0),'total_records':int(total) if isinstance(total,int) else None,
            'elapsed_ms':elapsed,'retry_after_ms':_retry_after(elapsed),'dataset':source['id'],'provenance':data._provenance(source)}


# store.view's own identity and location, rebuilt here until store.view_ready lands; test_query_building
# builds a real view and proves they agree (2026-09-25).
def _view_identity(path):
    return {'source_stamp':list(data._stamp(path)),'projection_code':store.LOADED_SHA}


def _view_ready(path):
    """store.view_ready(path) (the query view exists and verifies; never builds), or the store's own
    check before it builds when that API is not there yet."""
    ready=getattr(store,'view_ready',None)
    if callable(ready):
        try:return bool(ready(path))
        except (OSError,ValueError):return False
    try:
        identity=_view_identity(path)
        view=Path(store.ROOT)/'views'/(store._key(identity)+'.sqlite')
        return bool(store._valid(view,view.with_suffix('.json'),identity))
    except (OSError,ValueError):
        return False


def _view_job(path,retry=False):
    identity=_view_identity(path);key='view-'+store._key(identity)
    def work(report):
        store.view(path)
        # A view the readiness check refuses would be built again on every poll: this failure is sticky.
        if not _view_ready(path):raise _ViewUnverified('The query view was prepared but did not match the expected identity, so it could not be verified. Retry.')
    return _job('query_view',key,_building_root()/(key+'.lock'),work,VIEW_MESSAGE,retry)


def _sidecar_job(path,retry=False):
    ident=sidecar.identity(path);target,proof,lock,progress=sidecar.location(ident)
    def work(report):
        sidecar.build(path,ident,report)
        if _ready_sidecar(path) is None:raise ValueError('The source records were ordered but could not be verified. Retry.')
        try:sidecar.prune(sidecar.key(ident),live=_live)
        except OSError:pass
    return _job('records_sidecar','records-'+sidecar.key(ident),lock,work,RECORDS_MESSAGE,retry,progress)


def _live(lock):
    found=proclock.inspect(lock)
    return found is not None and found.live


def _ready_sidecar(path):
    """The proven records sidecar of this index, or None."""
    ident=sidecar.identity(path);target,proof,_,_=sidecar.location(ident)
    return target if sidecar.valid(target,proof,ident) else None


# ----------------------------------------------------------------------------- query
def query(report_directory,request,sources=None):
    _check_loaded()
    basis,dims,measures,limit,offset=semantics.validate(request)
    source=data._source(report_directory,request,sources)
    path,pending=data._ready(source,retry=request.get('retry') is True)
    if pending:return pending
    token=data._token(source)
    try:
        result=_query_ready(path,request,source,basis,dims,measures,limit,offset,hold=False)
    except _ViewPending:
        return _building(_view_job(path,retry=request.get('retry') is True),source)
    if data._token(source)!=token:raise ValueError('Contribution source changed while querying. Retry against the current source.')
    if result.get('status')!='ready':result={**result,'status':'ready'}
    return result


def _query_ready(path,request,source,basis,dims,measures,limit,offset,hold=True):
    """The ready answer. hold=False (the route) raises _ViewPending instead of building a missing
    query view in this request; a cached answer is still returned."""
    _check_loaded()
    _plan(request,basis,dims,measures,limit,offset) # validate before any cache hit
    stamp=data._stamp(path)
    uses_raw=bool((set(dims)|{f.get('field') for f in request.get('filters',[]) if isinstance(f,dict)})&set(semantics.RAW))
    raw_path=Path(source['database']) if uses_raw and source.get('database') else None
    raw_stamp=data._stamp(raw_path) if raw_path else None
    def stable():
        _check_loaded();store._loaded()
        return store.source_stable(path,stamp) and (not raw_path or store.source_stable(raw_path,raw_stamp))
    if not stable():raise ValueError('A contribution or reported-metadata source has active writes. Retry after it is stable.')
    # 'retry' restarts a failed build and never changes the answer: it stays out of the cache key (2026-09-25).
    identity={'index':stamp,'raw_source':raw_stamp,'query_code':LOADED_SHA256,'semantics':semantics.SHA,'dataset':source['id'],
              'request':{key:value for key,value in request.items() if key!='retry'}}
    return store.cached('results',identity,
        lambda:_query_uncached(path,request,source,basis,dims,measures,limit,offset,stamp,stable,hold),stable)


def _query_uncached(path,request,source,basis,dims,measures,limit,offset,stamp,stable,hold=True):
    if not hold and not _view_ready(path):raise _ViewPending()
    projected=store.view(path)
    projection_stamp=data._stamp(projected);proof_stamp=data._stamp(projected.with_suffix('.json'))
    def query_stable():
        return (stable() and store.source_stable(projected,projection_stamp)
            and data._stamp(projected.with_suffix('.json'))==proof_stamp)
    if not query_stable():raise ValueError('Query projection changed before its query started.')
    filters=request.get('filters',[])
    filter_refs=[f.get('field') for f in filters if isinstance(f,dict)]
    extra_measures=[f.get('field') for f in request.get('aggregate_filters',[]) if isinstance(f,dict)]
    group_deps=semantics.dependencies(dims+measures+extra_measures)
    filter_deps=semantics.dependencies(filter_refs+measures+extra_measures)
    group_table,summary_table=store.tables(group_deps,[{'field':f} for f in filter_deps])
    with closing(store._readonly(projected)) as db, _metadata_workspace(db,source,dims+filter_refs):
        if set(dims+filter_refs)&(set(semantics.AUDIT)|set(semantics.RAW)):
            db.execute('ATTACH DATABASE ? AS original',(Path(path).resolve().as_uri()+'?mode=ro',))
        relation=semantics.raw_relation(db,source,set(dims+filter_refs))
        plan=_plan(request,basis,dims,measures,limit,offset,group_table,summary_table,relation)
        semantics.register(db)
        db.create_aggregate('median_cent',1,data._Median)
        metadata=json.loads(db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
        summary_key={'index':stamp,'query_code':LOADED_SHA256,'semantics':semantics.SHA,'basis':basis,
            'raw_source':data._stamp(source['database']) if set(filter_refs)&set(semantics.RAW) and source.get('database') else None,
            'filters':request.get('filters',[]),'bin_width_cents':request.get('bin_width_cents',10000),'measures':plan['all_measures']}
        summary=store.cached('summaries',summary_key,
            lambda:dict(db.execute(plan['summary_sql'],plan['summary_params']).fetchone()),query_stable)
        totals={m:summary['total__'+m] for m in plan['all_measures']}
        comparison={m:summary['comparison__'+m] for m in plan['comparison_fields']}
        geography={'observations':totals['records'],'valid_zip_observations':summary['geo__valid'],'unmapped_amount':summary['geo__unmapped']}
        groups=iter(db.execute(plan['grouped_sql'],plan['grouped_params']))
        first=next(groups);count=first['_group_count'];group_amount=first['_grouped_amount']
        result=[{key:row[key] for key in dims+measures} for row in groups]
    if not query_stable():raise ValueError('Query projection changed while computing its query.')
    notices=['Observed original giving remains provisional; supported giving is a subset, not a complete total.',metadata['identity_basis']]
    if basis=='raw':notices.append('Raw observations can repeat gifts, include excluded receipts and contain money stages that must not be added as giving.')
    if basis=='pac_activity':notices.append('PAC activity here is recipient-reported, not a complete outgoing Schedule B census.')
    if count>offset+len(result):notices.append('More groups are available. Totals cover all matching observations, not just this page.')
    if request.get('aggregate_filters'):notices.append('Totals and accounting comparisons describe row-filtered observations before the aggregate filter; grouped_amount_cents describes the retained groups.')
    return {'schema_version':data.VERSION,'status':'ready','dataset':source['id'],'basis':basis,'basis_label':'Supported original giving (gross subset)' if basis=='original_supported' else data.BASES[basis][0],
        'columns':[{**field,'name':'Supported gross amount' if basis=='original_supported' and field['id']=='amount' else field['label']} for key in dims+measures for field in semantics.fields() if field['id']==key],
        'rows':result,'totals':totals,'group_count':count,'offset':offset,'limit':limit,'has_more':offset+len(result)<count,
        'comparison':comparison,'geography':geography,'reconciliation':{'source_receipt_cents':metadata['totals']['receipt'],'expected_receipt_cents':metadata['expected_receipt_cents'],'receipt_matches_workbook':metadata['totals']['receipt']==metadata['expected_receipt_cents'],'filtered_amount_cents':totals['amount'],'grouped_amount_cents':group_amount,'aggregate_filters_applied':bool(request.get('aggregate_filters')),'conserved':group_amount==totals['amount'] if not request.get('aggregate_filters') else None},
        'provenance':{**metadata['provenance'],'ledger_run_id':metadata['ledger_run_id'],'query_engine_sha256':LOADED_SHA256,'field_semantics_sha256':semantics.SHA,'query_projection_sha256':store.LOADED_SHA},'notices':notices,'verified_unique':False}


# ----------------------------------------------------------------------------- records
def records(report_directory,request,sources=None):
    _check_loaded()
    basis,_,_,limit,offset=semantics.validate(request)
    if limit>5000:raise ValueError('Drill-through page size must be at most 5,000.')
    if request.get('aggregate_filters'):raise ValueError('Drill through a selected group using its dimension filters.')
    source=data._source(report_directory,request,sources);path,pending=data._ready(source,retry=request.get('retry') is True)
    if pending:return pending
    token=data._token(source);stamp=data._stamp(path);column=data.BASES[basis][1]
    raw_path=Path(source['database']) if source.get('database') else None;raw_stamp=data._stamp(raw_path) if raw_path else None
    if raw_path and not store.source_stable(raw_path,raw_stamp):raise ValueError('Reported-metadata source has active writes.')
    filters=request.get('filters',[]);where,params=semantics.row_filter(filters,column)
    all_rows=bool(request.get('include_excluded',False)) or basis=='raw'
    if not all_rows:where+=' AND '+column+' != 0'
    dims=request.get('dimensions',[])
    filter_refs=[f.get('field') for f in filters if isinstance(f,dict)]
    # M6: a reported-metadata field asked for only as a column (the conduit report number) is read per
    # source ID and remembered; one used as a filter keeps the records query's full path.
    raw_dims=[key for key in semantics.RAW if key in dims] if not set(filter_refs)&set(semantics.RAW) else []
    base_dims=[d for d in dims if d not in raw_dims]
    ordered=None
    if sidecar.eligible(path) and sidecar.covers(filters,column):
        ordered=_ready_sidecar(path)
        if ordered is None:return _building(_sidecar_job(path,retry=request.get('retry') is True),source)
    rows=None;refs=base_dims+filter_refs
    with closing(data._ro(path)) as db, _metadata_workspace(db,source,refs):
        relation=semantics.raw_relation(db,source,refs)
        table=semantics.table('facts',refs,request,original=True,raw_relation=relation)
        if ordered is not None:
            count,ids=sidecar.page(ordered,request,filters,where,params,column,all_rows,limit,offset)
            rows=[dict(r) for r in db.execute('SELECT *,'+column+' AS amount_cents FROM '+table+' WHERE ('+where+') AND source_id IN (SELECT value FROM json_each(?)) ORDER BY date DESC,source_id',params+[json.dumps(ids)])]
            if [row['source_id'] for row in rows]!=ids:
                _log.warning('Records sidecar page disagreed with the index; answering from the index.')
                rows=None
        if rows is None:
            count=db.execute('SELECT COUNT(*) FROM '+table+' WHERE '+where,params).fetchone()[0]
            rows=[dict(r) for r in db.execute('SELECT *,'+column+' AS amount_cents FROM '+table+' WHERE '+where+' ORDER BY date DESC,source_id LIMIT ? OFFSET ?',params+[limit,offset])]
    if raw_dims and rows:
        values=_reported(path,source,token,raw_stamp,request,raw_dims,[row['source_id'] for row in rows])
        if values is None:return _records_full(path,source,request,where,params,column,dims,filter_refs,limit,offset,token,stamp,raw_path,raw_stamp,basis)
        # The full path's column order: index columns, derived fields, reported fields in
        # semantics.RAW order, then amount_cents.
        for row in rows:
            amount=row.pop('amount_cents');row.update((field,values[row['source_id']][field]) for field in raw_dims);row['amount_cents']=amount
    return _records_answer(rows,count,path,source,token,stamp,raw_path,raw_stamp,basis,limit,offset)


def _records_full(path,source,request,where,params,column,dims,filter_refs,limit,offset,token,stamp,raw_path,raw_stamp,basis):
    """The records query's full path, reported-metadata join included."""
    refs=dims+filter_refs
    with closing(data._ro(path)) as db, _metadata_workspace(db,source,refs):
        relation=semantics.raw_relation(db,source,refs)
        table=semantics.table('facts',refs,request,original=True,raw_relation=relation)
        count=db.execute('SELECT COUNT(*) FROM '+table+' WHERE '+where,params).fetchone()[0]
        rows=[dict(r) for r in db.execute('SELECT *,'+column+' AS amount_cents FROM '+table+' WHERE '+where+' ORDER BY date DESC,source_id LIMIT ? OFFSET ?',params+[limit,offset])]
    return _records_answer(rows,count,path,source,token,stamp,raw_path,raw_stamp,basis,limit,offset)


def _records_answer(rows,count,path,source,token,stamp,raw_path,raw_stamp,basis,limit,offset):
    if not store.source_stable(path,stamp) or (raw_path and not store.source_stable(raw_path,raw_stamp)) or data._token(source)!=token:raise ValueError('Contribution source changed during record inspection.')
    for row in rows:
        row['reasons']=json.loads(row['reasons']);row['evidence_ids']=json.loads(row['evidence_ids']);row.pop('contributor_key',None)
    return {'schema_version':data.VERSION,'status':'ready','dataset':source['id'],'basis':basis,'rows':rows,'total_records':count,'offset':offset,'limit':limit,'has_more':offset+len(rows)<count,
        'provenance':{**data._provenance(source),'index_token':token,'query_engine_sha256':LOADED_SHA256,'field_semantics_sha256':semantics.SHA},'amount_unit':'cents'}


def _reported(path,source,token,raw_stamp,request,fields,ids):
    """{source_id: {field: value}} for reported-metadata `fields` of these observations, from the memo
    or from the records query's own relation (then remembered). None when an observation matches more
    than one metadata row, so the caller keeps the full path's answer."""
    base=(token,tuple(raw_stamp) if raw_stamp else None,semantics.SHA,LOADED_SHA256)
    found={};missing=[]
    with _raw_memo_lock:
        for source_id in dict.fromkeys(ids):
            known=_raw_memo.get(base+(source_id,))
            if known is not None and all(field in known for field in fields):
                found[source_id]=known;_raw_memo.move_to_end(base+(source_id,))
            else:missing.append(source_id)
    if missing:
        read={}
        with closing(data._ro(path)) as db, _metadata_workspace(db,source,fields):
            relation=semantics.raw_relation(db,source,fields)
            table=semantics.table('facts',fields,request,original=True,raw_relation=relation)
            for row in db.execute('SELECT source_id,'+','.join('"'+field+'"' for field in fields)+' FROM '+table+' WHERE source_id IN (SELECT value FROM json_each(?))',(json.dumps(missing),)):
                if row['source_id'] in read:return None
                read[row['source_id']]={field:row[field] for field in fields}
        if set(read)!=set(missing):return None
        with _raw_memo_lock:
            for source_id,value in read.items():
                memo_key=base+(source_id,)
                merged={**_raw_memo.get(memo_key,{}),**value}
                _raw_memo[memo_key]=merged;_raw_memo.move_to_end(memo_key);found[source_id]=merged
            while len(_raw_memo)>RAW_MEMO_LIMIT:_raw_memo.popitem(last=False)
    return found


# ----------------------------------------------------------------------------- warmer entry point
def prebuild_records(report_directory,dataset,*,should_stop=None,wait_seconds=1800.0,poll_seconds=0.5):
    """Build the records sidecar of one dataset's current analytical index in the calling thread, for
    the warmer. Returns {'status': 'ready'|'skipped'|'held_elsewhere'|'index_missing', ...}:
      ready          the sidecar is proven; 'built' says whether this call built it, 'seconds' its cost
      skipped        the index is below records_sidecar.MIN_INDEX_BYTES (records answer from the index)
      held_elsewhere another process still builds it after wait_seconds (or should_stop() turned true)
      index_missing  the analytical index is not built; nothing is built here
    ValueError when the build fails (its message)."""
    source=data._source(report_directory,{'dataset':dataset})
    token=data._token(source);path=data.CACHE_ROOT/(token+'.sqlite')
    if not path.exists():return {'status':'index_missing','dataset':dataset}
    if not sidecar.eligible(path):return {'status':'skipped','dataset':dataset,'reason':'small_index','index_bytes':path.stat().st_size}
    ident=sidecar.identity(path);target,proof,lock,progress=sidecar.location(ident)
    key='records-'+sidecar.key(ident);deadline=time.monotonic()+wait_seconds;started=time.perf_counter()
    while True:
        if sidecar.valid(target,proof,ident):
            return {'status':'ready','dataset':dataset,'built':False,'path':str(target),'seconds':round(time.perf_counter()-started,3)}
        with _jobs_lock:running=_jobs.get(key)
        running=running is not None and running['status']=='running'
        body=None if running else proclock.claim(lock,key,extra={'phase':'records_sidecar'})
        if body is not None:
            job={'phase':'records_sidecar','status':'running','started':time.time(),'processed':0,'total':None,'message':RECORDS_MESSAGE}
            def report(processed,total,message=None):
                job.update(processed=int(processed or 0),total=total if isinstance(total,int) else None,message=message or job['message'])
                _write_progress(progress,job)
            try:
                _write_progress(progress,job)
                sidecar.build(path,ident,report,should_stop)
            except ValueError:
                if not (should_stop and should_stop()):raise   # a stop ends the build with a ValueError
            finally:
                try:Path(progress).unlink(missing_ok=True)
                except OSError:pass
                proclock.release(lock,body)
            # Verified and pruned as _sidecar_job does (2026-09-25).
            if not sidecar.valid(target,proof,ident):
                if should_stop and should_stop():return {'status':'held_elsewhere','dataset':dataset}
                raise ValueError('The source records were ordered but could not be verified. Retry.')
            try:sidecar.prune(sidecar.key(ident),live=_live)
            except OSError:pass
            return {'status':'ready','dataset':dataset,'built':True,'path':str(target),'seconds':round(time.perf_counter()-started,3)}
        if (should_stop and should_stop()) or time.monotonic()>=deadline:
            return {'status':'held_elsewhere','dataset':dataset}
        time.sleep(poll_seconds)
