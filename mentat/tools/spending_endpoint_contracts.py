"""Explicit F/H4 page contracts (plus the unpinned totals and candidate_committees shapes); pure metadata, no I/O or collection at import.

The driver must archive and retain a page BEFORE applying inspect_page issues.
These observations never certify a unit by themselves. Enable/activation is an
explicit driver operation after review, not an automatic schema migration.
"""
from decimal import Decimal
import hashlib
import json
import math
import re

OFFICIAL_COMMIT='9aa2ee30e63e2071f7a6bbf66d2deb5b32586f6b'
CONTRACTS={
 'F':{'contract_id':'openfec-'+OFFICIAL_COMMIT+'-F-offset-v1',
      'endpoint':'schedules/schedule_f/','amount_field':'expenditure_amount','id_field':'sub_id',
      'cycle_selector':'cycle','pagination_family':'offset','default_sort':'expenditure_date',
      'paid_recipient_committee_fields':(),
      'beneficiary_fields':('candidate_id',),'source_or_reporting_fields':('committee_id','subordinate_committee_id'),
      'official_source_sha256':'42b7e6b4b718c4d80dd8c83d4889afe0ea7a4ba73acc653eb5389ddae74a1a8f'},
 'H4':{'contract_id':'openfec-'+OFFICIAL_COMMIT+'-H4-seek-v1',
       'endpoint':'schedules/schedule_h4/','amount_field':'disbursement_amount','id_field':'sub_id',
       'cycle_selector':'cycle','pagination_family':'seek','default_sort':'-event_purpose_date',
       'paid_recipient_committee_fields':(),
       'beneficiary_fields':(),'source_or_reporting_fields':('committee_id','spender_committee_name'),
       'official_source_sha256':'245cc2039b130780f075acc6fd367d29ba383c2807cf12a2aad0c3ccdd578f97'}
}
UNPINNED_NOTE=('UNPINNED: not covered by the retained official source pin '+OFFICIAL_COMMIT+' (webservices/resources/totals.py and candidates.py are '
               'not retained; rest.py only registers the views). The request shape is retained schema knowledge, verified only by an archived live response.')
# Totals are a per-unit family that never creates payees, edges or candidate links; candidate_committees is a per-candidate lookup, not an endpoint family.
CONTRACTS['totals']={'contract_id':'openfec-unpinned-totals-numbered-v1',
      'endpoint':'committee/{committee_id}/totals/','amount_field':'totals_not_a_payment','id_field':None,
      'cycle_selector':'cycle','pagination_family':'offset','default_sort':None,'scope':'unit','creates_missing_endpoint_rows':True,
      'official_commit':None,'pin_status':UNPINNED_NOTE,
      'paid_recipient_committee_fields':(),'beneficiary_fields':(),'source_or_reporting_fields':('committee_id',),
      'official_source_sha256':None}
CONTRACTS['candidate_committees']={'contract_id':'openfec-unpinned-candidate-committees-numbered-v1',
      'endpoint':'candidate/{candidate_id}/committees/','amount_field':None,'id_field':'committee_id',
      'cycle_selector':'cycle','pagination_family':'offset','default_sort':None,'scope':'candidate','designation':'P',
      'official_commit':None,'pin_status':UNPINNED_NOTE,
      'paid_recipient_committee_fields':(),'beneficiary_fields':(),'source_or_reporting_fields':(),
      'official_source_sha256':None}
PER_UNIT_KINDS=tuple(kind for kind,value in CONTRACTS.items() if value.get('scope','unit')=='unit')
LEGACY_CURSOR_FIELDS={
 'A':{'last_index','last_contribution_receipt_date','last_contribution_receipt_amount'},
 'B':{'last_index','last_disbursement_amount','last_disbursement_date'},
 'E_regular':{'last_index','last_expenditure_date','last_expenditure_amount','last_office_total_ytd','last_support_oppose_indicator'},
 'E_notice':{'last_index','last_expenditure_date','last_expenditure_amount','last_office_total_ytd','last_support_oppose_indicator'}}


def _cursor_scalar(value):
    """One keyset component: a bounded scalar, never a container that could add selectors."""
    if value is None:return True
    if type(value) is str:return len(value)<=128
    return type(value) in(int,Decimal) or (type(value) is float and math.isfinite(value))


def legacy_cursor_valid(kind,cursor,*,stored_or_generated_page=False):
    """Pinned public last_* fields; pagination never replaces unit selectors.

    Provider continuation and a cursor read back from storage are both held to the
    same allow-list, so committee_id, cycle, two_year_transaction_period, notice
    flags, page size, sort and every other reserved selector can never be overridden.
    """
    if not isinstance(cursor,dict):return False
    if not cursor:return True
    if set(cursor)=={'page'}:
        return stored_or_generated_page and type(cursor['page']) is int and cursor['page']>0
    if kind not in LEGACY_CURSOR_FIELDS or set(cursor)-LEGACY_CURSOR_FIELDS[kind]:return False
    if not all(_cursor_scalar(value) for value in cursor.values()):return False
    index=cursor.get('last_index')
    return index is None or (type(index) in(str,int) and re.fullmatch('[0-9]+',str(index)) is not None)


def binding(kind):
    value=CONTRACTS[kind]
    metadata={**value,'official_commit':value.get('official_commit',OFFICIAL_COMMIT)}
    raw=json.dumps(metadata,sort_keys=True,separators=(',',':')).encode()
    return {**metadata,'contract_metadata_sha256':hashlib.sha256(raw).hexdigest()}


def query(kind,committee,cycle,*,per_page=100,cursor=None):
    spec=CONTRACTS[kind]
    if not isinstance(committee,str) or not re.fullmatch('C[0-9]{8}',committee):
        raise ValueError('Exact reporting committee ID required.')
    if type(cycle) is not int or cycle not in (2022,2024,2026):
        raise ValueError('Exact authorized activity cycle required.')
    if type(per_page) is not int or not 1<=per_page<=100:
        raise ValueError('Page size must be an integer from1through100.')
    cursor={} if cursor is None else cursor
    if not isinstance(cursor,dict):raise ValueError('Cursor object required.')
    if kind=='totals':
        # The committee is a path component of the endpoint template, never a query selector; no sort is sent for an unpinned shape.
        if set(cursor)-{'page'} or type(cursor.get('page',1)) is not int or cursor.get('page',1)<1:
            raise ValueError('totals requires positive numbered pages, not keyset indexes.')
        return {'cycle':cycle,'per_page':per_page,'page':cursor.get('page',1)}
    if spec.get('scope','unit')!='unit':raise ValueError('Not a per-committee endpoint contract.')
    result={'committee_id':committee,'cycle':cycle,'per_page':per_page,
            'sort':spec['default_sort'],'sort_hide_null':'false','sort_null_only':'false'}
    if kind=='F':
        if set(cursor)-{'page'} or type(cursor.get('page',1)) is not int or cursor.get('page',1)<1:
            raise ValueError('F requires positive numbered pages, not keyset indexes.')
        result['page']=cursor.get('page',1)
    elif cursor:
        if (set(cursor)!={'last_index','last_event_purpose_date'} or
                not isinstance(cursor['last_index'],(str,int)) or isinstance(cursor['last_index'],bool) or
                not re.fullmatch('[0-9]+',str(cursor['last_index'])) or
                not isinstance(cursor['last_event_purpose_date'],str) or
                not re.fullmatch(r'\d{4}-\d{2}-\d{2}',cursor['last_event_purpose_date'])):
            raise ValueError('H4 requires the complete supported date/index cursor; unresolved NULL dates remain unknown.')
        result.update(cursor)
    return result


def candidate_committees_query(candidate,cycle,*,per_page=100,cursor=None):
    """Principal-committee lookup for one candidate/cycle: designation P is part of the pinned request, never a post-filter choice."""
    if not isinstance(candidate,str) or not re.fullmatch('[HSP][A-Z0-9]{8}',candidate):
        raise ValueError('Exact candidate ID required.')
    if type(cycle) is not int or cycle not in (2022,2024,2026):
        raise ValueError('Exact authorized activity cycle required.')
    if type(per_page) is not int or not 1<=per_page<=100:
        raise ValueError('Page size must be an integer from1through100.')
    cursor={} if cursor is None else cursor
    if not isinstance(cursor,dict) or set(cursor)-{'page'} or type(cursor.get('page',1)) is not int or cursor.get('page',1)<1:
        raise ValueError('candidate_committees requires positive numbered pages.')
    return {'designation':CONTRACTS['candidate_committees']['designation'],'cycle':cycle,'per_page':per_page,'page':cursor.get('page',1)}


def _numbered_progress(request,payload):
    """Shared numbered-page reading for the unpinned totals/candidate_committees shapes.

    A response is retrieved when it parses to an object with a result array; a following page
    is requested only when the provider itself reports more pages. Returns (rows, issues, next_cursor)."""
    issues=[]
    if not isinstance(payload,dict):return [],['response_not_object'],{}
    rows=payload.get('results');pagination=payload.get('pagination')
    if not isinstance(rows,list):rows=[];issues.append('results_not_array')
    if any(not isinstance(row,dict) for row in rows):issues.append('result_row_not_object')
    page=request.get('page',1);cursor={}
    if isinstance(pagination,dict) and pagination:
        pages=pagination.get('pages')
        if 'page' in pagination and pagination['page']!=page:issues.append('wrong_numbered_page')
        if 'pages' in pagination and not (type(pages) is int and pages>=0):issues.append('invalid_numbered_pages')
        elif type(pages) is int and page<pages:
            if not rows:issues.append('empty_before_final_page')
            elif not issues:cursor={'page':page+1}
    return rows,issues,cursor


def inspect_totals_page(request,payload):
    rows,issues,cursor=_numbered_progress(request,payload)
    return {'count':None,'record_count':len(rows),'next_cursor':cursor,'page_end_matches_count':not issues and not cursor,
            'issues':sorted(set(issues)),'contract_id':CONTRACTS['totals']['contract_id']}


def inspect_candidate_committees_page(request,payload):
    """Principal-designation committee IDs on this page. Rows with another designation are counted, not kept; a row without an exact C ID is an issue."""
    rows,issues,cursor=_numbered_progress(request,payload)
    found=[];ignored=0;wanted=CONTRACTS['candidate_committees']['designation']
    for row in rows:
        if not isinstance(row,dict):continue
        identifier=row.get('committee_id')
        if not isinstance(identifier,str) or not re.fullmatch('C[0-9]{8}',identifier):issues.append('missing_or_invalid_committee_id');continue
        if row.get('designation')!=wanted:ignored+=1;continue
        if identifier not in found:found.append(identifier)
    return {'committee_ids':found,'record_count':len(rows),'ignored_non_principal_rows':ignored,'next_cursor':cursor,'issues':sorted(set(issues)),
            'contract_id':CONTRACTS['candidate_committees']['contract_id']}


def inspect_page(kind,request,payload,*,rows_before=0,expected_count=None,seen_ids=()):
    """Return page observations/issues AFTER retention; never drop or mutate rows."""
    if kind=='totals':return inspect_totals_page(request,payload)
    spec=CONTRACTS[kind];issues=[]
    if not isinstance(payload,dict):return {'count':None,'record_count':0,'issues':['response_not_object'],'next_cursor':{},'page_end_matches_count':False,'contract_id':spec['contract_id']}
    rows=payload.get('results');pagination=payload.get('pagination')
    if not isinstance(rows,list):rows=[];issues.append('results_not_array')
    if not isinstance(pagination,dict):pagination={};issues.append('pagination_not_object')
    count=pagination.get('count')
    valid_count=type(count) is int and count>=0
    exact=valid_count and pagination.get('is_count_exact') is True
    if not valid_count:issues.append('invalid_count')
    if pagination.get('is_count_exact') is not True:issues.append('missing_or_estimated_count')
    if expected_count is not None:
        if type(expected_count) is not int or expected_count<0:issues.append('invalid_expected_count')
        elif count!=expected_count:issues.append('provider_count_changed')
    if type(rows_before) is not int or rows_before<0:raise ValueError('Exact retained row count required.')
    identifiers=[]
    for row in rows:
        value=row.get(spec['id_field']) if isinstance(row,dict) else None
        if not isinstance(value,str) or not re.fullmatch('[0-9]+',value):issues.append('missing_or_unsafe_source_id')
        else:identifiers.append(value)
    if len(set(identifiers))!=len(identifiers) or set(identifiers)&set(seen_ids):issues.append('duplicate_source_id')
    total=rows_before+len(rows)
    if valid_count and total>count:issues.append('rows_exceed_count')
    if exact and total<count and not rows:issues.append('empty_before_count')
    size=request.get('per_page')
    if type(size) is not int or not 1<=size<=100:raise ValueError('Exact requested page size required.')
    if pagination.get('per_page')!=size or type(pagination.get('per_page')) is not int:issues.append('page_size_changed')
    cursor={}
    if kind=='F':
        page=request.get('page',1);pages=pagination.get('pages')
        if type(page) is not int or page<1:raise ValueError('Exact requested numbered page required.')
        if type(pagination.get('page')) is not int or pagination.get('page')!=page:issues.append('wrong_numbered_page')
        pages_valid=type(pages) is int and pages>=0
        if not pages_valid or exact and pages!=(count+size-1)//size:issues.append('invalid_numbered_pages')
        if exact:
            if rows_before!=(page-1)*size:issues.append('numbered_page_offset_mismatch')
            if len(rows)!=max(0,min(size,count-rows_before)):issues.append('numbered_page_length_mismatch')
            if pages_valid and page>max(1,pages):issues.append('numbered_page_outside_count')
        page_valid=type(pagination.get('page')) is int and pagination['page']==page
        # Estimated/missing counts cannot close retrieval. A valid numbered
        # response with rows can advance, even at its estimated page/count.
        if rows and page_valid and not(exact and total==count and not issues):
            if not exact or len(rows)==size or pages_valid and page<pages:cursor={'page':page+1}
    else:
        raw_cursor=pagination.get('last_indexes')
        exact_end=exact and total==count and not issues
        if raw_cursor is None or raw_cursor=={}:
            if rows and not exact_end:issues.append('missing_cursor_with_nonempty_page')
        elif not isinstance(raw_cursor,dict):issues.append('incomplete_or_unsupported_cursor')
        else:
            # A NULL sort value: the pinned provider (utils.py _get_index_values) drops the date key and emits
            # sort_null_only=True; the older last_<date>=None shape is tolerated too.
            # Only accepted at an exact end: fetch_seek_page applies null-only just for ascending sorts and H4 defaults to descending.
            null_marker=set(raw_cursor)-{'last_index'}
            nullable_shape=('last_index' in raw_cursor and
                isinstance(raw_cursor['last_index'],(str,int)) and not isinstance(raw_cursor['last_index'],bool) and
                re.fullmatch('[0-9]+',str(raw_cursor['last_index'])) and
                (null_marker=={'last_event_purpose_date'} and raw_cursor['last_event_purpose_date'] is None or
                 null_marker=={'sort_null_only'} and raw_cursor['sort_null_only'] is True))
            if nullable_shape and exact_end:
                pass  # A known final page needs no invented date to advance.
            else:
                try:query(kind,request['committee_id'],request['cycle'],per_page=size,cursor=raw_cursor)
                except (ValueError,KeyError,TypeError):issues.append('incomplete_or_unsupported_cursor')
                else:
                    if rows and not exact_end:cursor=dict(raw_cursor)
    return {'count':count if valid_count else None,'record_count':len(rows),'next_cursor':cursor,
            'page_end_matches_count':exact and total==count and not issues,
            'issues':sorted(set(issues)),'contract_id':spec['contract_id']}


def activation_plan(rows,enabled_contract_ids):
    """Plan only clean unsupported→pending transitions; caller logs/applies atomically."""
    enabled=set(enabled_contract_ids)
    if enabled-set(value['contract_id'] for value in CONTRACTS.values()):raise ValueError('Unknown contract enable ID.')
    changes=[];blocked=[]
    for row in rows:
        kind=row['kind']
        if kind not in CONTRACTS or CONTRACTS[kind]['contract_id'] not in enabled or row['status']!='unsupported':continue
        try:
            clean=(type(row['rows_stored']) is int and type(row['pages_stored']) is int and
                   row['rows_stored']==row['pages_stored']==0 and row['expected_count'] is None and
                   json.loads(row['request_cursor_json'])=={} and json.loads(row['coverage_issues_json'])==[])
            query(kind,row['committee_id'],row['cycle'])
        except (ValueError,TypeError,KeyError):clean=False
        key={field:row[field] for field in ('committee_id','cycle','kind')}
        if not clean:blocked.append({**key,'reason':'Existing unsupported state contains evidence or invalid checkpoint; no automatic reset.'});continue
        proof=binding(kind)
        changes.append({**key,'from_status':'unsupported','to_status':'pending',
                        'contract_id':proof['contract_id'],'contract_metadata_sha256':proof['contract_metadata_sha256']})
    return {'changes':changes,'blocked':blocked,'enabled_contract_ids':sorted(enabled),
            'contracts':[binding(kind) for kind,value in CONTRACTS.items() if value['contract_id'] in enabled],
            'reset_or_recertify_other_endpoints':False}


class EndpointContractRefused(ValueError):
    pass


def intent_path(ledger_path,version=1):
    """Version 1 is the original intent file name; later versions are append-only siblings (frontier.endpoint-contracts.v2.json, ...)."""
    from pathlib import Path
    base=Path(ledger_path).with_suffix('.endpoint-contracts.json')
    return base if version==1 else base.with_name(base.name[:-len('.json')]+'.v'+str(version)+'.json')


def _intent_bytes(enabled,module_sha,contracts,version=1,previous=None):
    document={'schema_version':1,'purpose':'explicit-spending-endpoint-contract-intent',
              'enabled_contract_ids':enabled,'module_sha256':module_sha,'contracts':contracts}
    if version>1:
        document.update(version=version,supersedes={'version':version-1,'file':previous['path'].name,'sha256':previous['sha256'],
                                                    'enabled_contract_ids':previous['document']['enabled_contract_ids'],
                                                    'module_sha256':previous['document']['module_sha256']})
    return (json.dumps(document,sort_keys=True,separators=(',',':'))+'\n').encode()


def intent_chain(ledger_path):
    """The verified append-only chain of intent files, oldest first: [{version,path,raw,sha256,document}].

    Every file must be a regular file under 64 KiB; versions must be contiguous (a stray v3 beside v1 is refused); each later version must
    name the exact bytes of its predecessor and carry a SUPERSET of its contract IDs. Anything else raises EndpointContractRefused."""
    import re
    ids={value['contract_id'] for value in CONTRACTS.values()}
    chain=[];version=1
    while True:
        path=intent_path(ledger_path,version)
        if path.is_symlink():raise EndpointContractRefused('Endpoint binding must be a regular file; collection refused.')
        if not path.exists():break
        if path.stat().st_size>64*1024:raise EndpointContractRefused('Endpoint intent is oversized; collection refused.')
        raw=path.read_bytes()
        try:document=json.loads(raw)
        except ValueError:raise EndpointContractRefused('Endpoint intent is not valid JSON; collection refused.')from None
        enabled=document.get('enabled_contract_ids') if isinstance(document,dict) else None
        if (not isinstance(enabled,list) or enabled!=sorted(set(enabled)) or not set(enabled)<=ids or document.get('schema_version')!=1
                or not isinstance(document.get('module_sha256'),str) or not isinstance(document.get('contracts'),list)):
            raise EndpointContractRefused('Endpoint intent is malformed; collection refused.')
        if version>1:
            previous=chain[-1];link=document.get('supersedes')
            if (document.get('version')!=version or not isinstance(link,dict) or link.get('version')!=version-1 or link.get('sha256')!=previous['sha256']
                    or link.get('file')!=previous['path'].name or not set(previous['document']['enabled_contract_ids'])<=set(enabled)):
                raise EndpointContractRefused('Endpoint intent chain is broken or removes a contract ID; collection refused.')
        chain.append({'version':version,'path':path,'raw':raw,'sha256':hashlib.sha256(raw).hexdigest(),'document':document})
        version+=1
    base=intent_path(ledger_path,1).name[:-len('.json')]
    pattern=re.compile(re.escape(base)+r'\.v([0-9]+)\.json')
    stray=sorted(int(match.group(1)) for item in intent_path(ledger_path,1).parent.glob(base+'.v*.json') for match in [pattern.fullmatch(item.name)] if match)
    if stray and stray!=list(range(2,len(chain)+1)):raise EndpointContractRefused('Endpoint intent chain has a gap or an unlinked version; collection refused.')
    return chain


def _publish_intent(path,raw):
    import os
    import uuid
    pending=path.with_name(path.name+'.pending-'+uuid.uuid4().hex)
    try:
        with pending.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        # Exclusive hard-link publication never overwrites another
        # binding; unsupported filesystems fail before DB promotion.
        os.link(pending,path)
        try:
            descriptor=os.open(str(path.parent),os.O_RDONLY)
        except OSError:descriptor=None  # Windows cannot open a directory; the file itself is fsynced.
        if descriptor is not None:
            try:os.fsync(descriptor)
            except OSError:pass
            finally:os.close(descriptor)
    finally:pending.unlink(missing_ok=True)


def bind_enablement(connection,ledger_path,enabled_contract_ids,*,rebind=False):
    """Called before credential/admission; an enabling call runs inside the driver's writer transaction.

    Publish immutable intent first, then promote clean rows. This is not an
    atomic file/DB pair: an intact intent left by rollback is replayed only
    with the exact same enabled IDs and module bytes. Unknown state is held.
    Without enabled IDs it only reads and refuses any existing binding or active per-unit endpoint state.

    One intent is one exact set, and any later change of the set or of this module's bytes is refused, unless the caller passes
    rebind=True (collector flag --rebind-endpoint-contracts): that appends a NEW version (frontier.endpoint-contracts.vN.json) whose
    ID list is a superset of the prior one and which binds this module's current hash. The prior intent file is kept and named in the
    new one by sha256; removing an ID is never allowed. A re-bind that changes nothing writes nothing.
    """
    from pathlib import Path
    plan=activation_plan([],enabled_contract_ids)
    enabled=plan['enabled_contract_ids']
    marks=','.join('?'*len(PER_UNIT_KINDS))
    active=connection.execute("SELECT COUNT(*) FROM endpoints WHERE kind IN("+marks+") AND status!='unsupported'",PER_UNIT_KINDS).fetchone()[0]
    chain=intent_chain(ledger_path)
    if not enabled:
        if rebind:raise EndpointContractRefused('Re-binding needs the full enabled ID set; removing every contract ID is never allowed.')
        if chain or active:raise EndpointContractRefused('Pinned F/H4 enablement is required before collection; no fallback.')
        return {'enabled_contract_ids':[],'activated_existing_endpoints':0,'blocked_existing_endpoints':0,'binding':None}
    module_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    rebound=None
    try:
        if not chain:
            if rebind:raise EndpointContractRefused('Nothing to re-bind: no prior endpoint intent exists for this ledger.')
            if active:raise EndpointContractRefused('Active F/H4 state has no durable contract binding; collection refused.')
            _publish_intent(intent_path(ledger_path,1),_intent_bytes(enabled,module_sha,plan['contracts']))
            chain=intent_chain(ledger_path)
        else:
            latest=chain[-1]
            same=latest['raw']==_intent_bytes(enabled,module_sha,plan['contracts'],latest['version'],chain[-2] if latest['version']>1 else None)
            if not same:
                if not rebind:
                    raise EndpointContractRefused('Pinned endpoint binding differs from enabled IDs/module; collection refused. A superset of the prior IDs or a changed '
                                                  'module needs an explicit --rebind-endpoint-contracts (append-only; removal is never allowed).')
                if not set(latest['document']['enabled_contract_ids'])<=set(enabled):
                    raise EndpointContractRefused('Re-bind may only add contract IDs; removing one is never allowed. Collection refused.')
                version=latest['version']+1
                _publish_intent(intent_path(ledger_path,version),_intent_bytes(enabled,module_sha,plan['contracts'],version,latest))
                chain=intent_chain(ledger_path)
                rebound={'new_version':version,'file':chain[-1]['path'].name,'supersedes_file':latest['path'].name,'supersedes_sha256':latest['sha256'],
                         'added_contract_ids':sorted(set(enabled)-set(latest['document']['enabled_contract_ids'])),'module_sha256_changed':latest['document']['module_sha256']!=module_sha}
    except EndpointContractRefused:raise
    except OSError:raise EndpointContractRefused('Endpoint intent could not be durably published; collection refused.')from None
    latest=chain[-1]
    rows=[dict(row) for row in connection.execute("SELECT * FROM endpoints WHERE kind IN("+marks+")",PER_UNIT_KINDS)]
    plan=activation_plan(rows,enabled)
    for change in plan['changes']:
        connection.execute("UPDATE endpoints SET status='pending',last_error=NULL WHERE committee_id=? AND cycle=? AND kind=? AND status='unsupported'",
                           tuple(change[field] for field in ('committee_id','cycle','kind')))
    # A family that older ledgers never had a row for (totals) is added, pending, for every existing unit. Additive and idempotent: existing rows are never touched.
    created=0
    for kind in PER_UNIT_KINDS:
        if CONTRACTS[kind]['contract_id'] in enabled and CONTRACTS[kind].get('creates_missing_endpoint_rows'):
            created+=connection.execute("INSERT OR IGNORE INTO endpoints(committee_id,cycle,kind,status) SELECT committee_id,cycle,?,'pending' FROM units",(kind,)).rowcount
    result={'enabled_contract_ids':enabled,'activated_existing_endpoints':len(plan['changes']),'created_existing_endpoints':created,
            'blocked_existing_endpoints':len(plan['blocked']),
            'binding':{'path':str(latest['path']),'sha256':latest['sha256'],'module_sha256':module_sha,'version':latest['version'],
                       'chain':[{'version':item['version'],'file':item['path'].name,'sha256':item['sha256'],'enabled_contract_ids':item['document']['enabled_contract_ids'],
                                 'module_sha256':item['document']['module_sha256']}for item in chain]},
            'contract_metadata':[binding(kind)for kind,value in CONTRACTS.items()if value['contract_id']in enabled]}
    if rebound:result['rebind']=rebound
    return result


def verify_enablement(receipt):
    from pathlib import Path
    ref=receipt.get('binding')
    if ref is None:return
    try:
        path=Path(ref['path'])
        if (path.stat().st_size>64*1024 or hashlib.sha256(path.read_bytes()).hexdigest()!=ref['sha256'] or
                hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=ref['module_sha256']):
            raise EndpointContractRefused('Endpoint binding changed before admission; collection refused.')
    except OSError:raise EndpointContractRefused('Endpoint binding unavailable before admission; collection refused.')from None
