"""Attach explicit transfer paths, conserved estimates and unresolved coverage."""
import argparse
from collections import defaultdict
import json
import csv
import hashlib
from pathlib import Path
import sqlite3
from fec_transfer_graph import allocate,reachable_rows,atomic_json


# --- tidy staging store (schema_version "tidy-1") ---------------------------
#
# These seven tables exist only on the Python side of the export: R has already
# written the receipt and dimension tables into the same SQLite file and the
# orchestrator publishes it once both producers are done. They are appended
# here, in place, because this is the only process that ever holds them.
#
# Column names are the contract's (section 2.12), not this module's internal
# keys, and the types are declared explicitly: SQLite would otherwise give an
# all-NULL column BLOB affinity, and the workbook writer reads the declared type
# to choose a currency, integer or date cell format.

def _cycle_label(cycle):
    try:
        year=int(cycle)
    except (TypeError,ValueError):
        return None
    return f'{year - 1}-{year}'


def _text(value):
    if value is None:return None
    if isinstance(value,(dict,list)):return json.dumps(value,sort_keys=True)
    text=str(value).strip()
    return text or None


def _number(value):
    if value is None or isinstance(value,bool):return None
    try:
        return float(value)
    except (TypeError,ValueError):
        return None


def _count(value):
    number=_number(value)
    return None if number is None else int(round(number))


def _amount_map(value):
    """A {committee: dollars} map as it survives the R round trip.

    jsonlite writes an EMPTY named list as `[]`, not `{}`, and an absent key
    arrives as None, so anything that is not an object carries no amounts.
    """
    return value if isinstance(value,dict) else {}


def _flag(value):
    if value is None:return None
    if isinstance(value,bool):return int(value)
    if isinstance(value,(int,float)):return int(bool(value))
    text=str(value).strip().lower()
    if text in ('yes','true','t','y','1'):return 1
    if text in ('no','false','f','n','0'):return 0
    return None


STAGING_TABLES={
    # F14 (Paul approved option (a), 2026-09-26): one row per transfer edge and cycle.
    # The root x committee x edge detail is transfer_path_detail, written while the
    # expansion streams (TransferPathDetail) and never held in the payload.
    'transfer_paths':(
        (('cycle_label','TEXT'),lambda r:_cycle_label(r.get('cycle'))),
        (('cycle','INTEGER'),lambda r:_count(r.get('cycle'))),
        (('source_committee','TEXT'),lambda r:_text(r.get('source'))),
        (('destination_committee','TEXT'),lambda r:_text(r.get('destination'))),
        (('reported_net_transfer','REAL'),lambda r:_number(r.get('amount'))),
        (('record_count','INTEGER'),lambda r:_count(r.get('record_count'))),
        (('first_transfer_date','TEXT'),lambda r:_text(r.get('first_date'))),
        (('last_transfer_date','TEXT'),lambda r:_text(r.get('last_date'))),
        (('traversal_status','TEXT'),lambda r:_text(r.get('status'))),
        (('funded_roots_reaching','INTEGER'),lambda r:_count(r.get('roots_reaching'))),
        (('shortest_depth','INTEGER'),lambda r:_count(r.get('min_depth'))),
        (('top_funded_roots','TEXT'),lambda r:_text(r.get('top_roots'))),
        (('roots_via_carryover','INTEGER'),lambda r:_count(r.get('roots_via_carryover'))),
        (('date_rule','TEXT'),lambda r:_text(r.get('date_rule'))),
        (('source_gate','TEXT'),lambda r:_text(r.get('source_gate'))),
        (('source_gate_source','TEXT'),lambda r:_text(r.get('source_gate_source'))),
        (('excluded_before_gate_records','INTEGER'),lambda r:_count(r.get('excluded_before_gate_records'))),
        (('excluded_before_gate_amount','REAL'),lambda r:_number(r.get('excluded_before_gate_amount'))),
        (('source_carryover_route','TEXT'),lambda r:_text(r.get('source_carryover_route'))),
        (('carryover_status','TEXT'),lambda r:_text(r.get('carryover_status'))),
    ),
    'funding_cohorts':(
        (('cycle_label','TEXT'),lambda r:_cycle_label(r.get('cycle'))),
        (('cycle','INTEGER'),lambda r:_count(r.get('cycle'))),
        (('company','TEXT'),lambda r:_text(r.get('company'))),
        (('funding_type','TEXT'),lambda r:_text(r.get('funding_type'))),
        (('recipient_committee','TEXT'),lambda r:_text(r.get('committee_id'))),
        (('receipt_role','TEXT'),lambda r:_text(r.get('receipt_role'))),
        (('included_net_receipts','REAL'),lambda r:_number(r.get('amount'))),
        (('receipt_count','INTEGER'),lambda r:_count(r.get('receipt_count'))),
        (('first_receipt','TEXT'),lambda r:_text(r.get('first_date'))),
        (('last_receipt','TEXT'),lambda r:_text(r.get('last_date'))),
    ),
    'allocation_estimates':(
        (('cycle_label','TEXT'),lambda r:_cycle_label(r.get('cycle'))),
        (('cycle','INTEGER'),lambda r:_count(r.get('cycle'))),
        (('funded_committee','TEXT'),lambda r:_text(r.get('root'))),
        (('candidate_id','TEXT'),lambda r:_text(r.get('candidate_id'))),
        (('candidate','TEXT'),lambda r:_text(r.get('candidate_name'))),
        (('direction','TEXT'),lambda r:_text(r.get('direction'))),
        (('conditional_estimate','REAL'),lambda r:_number(r.get('estimated_amount'))),
    ),
    'allocation_coverage':(
        (('cycle_label','TEXT'),lambda r:_cycle_label(r.get('cycle'))),
        (('cycle','INTEGER'),lambda r:_count(r.get('cycle'))),
        (('funded_committee','TEXT'),lambda r:_text(r.get('root'))),
        (('net_input_funding','REAL'),lambda r:_number(r.get('input_net_funding'))),
        (('nonnegative_model_funding','REAL'),lambda r:_number(r.get('model_funding'))),
        (('estimated_candidate_activity','REAL'),
         lambda r:_number(r.get('estimated_candidate_activity'))),
        (('unresolved','REAL'),lambda r:_number(r.get('unresolved'))),
        (('negative_net_funding','REAL'),
         lambda r:_number(r.get('negative_net_funding_excluded'))),
        # A reason map is evidence a reader opens, not a dimension. Splitting it
        # into rows here would multiply every unresolved dollar by its reasons.
        (('unresolved_reasons','TEXT'),lambda r:_text(r.get('unresolved_reasons'))),
    ),
    'source_freshness':(
        (('cycle_label','TEXT'),lambda r:_cycle_label(r.get('cycle'))),
        (('cycle','INTEGER'),lambda r:_count(r.get('cycle'))),
        (('fec_dataset','TEXT'),lambda r:_text(r.get('source'))),
        (('checked_at','TEXT'),lambda r:_text(r.get('checked_at'))),
        (('server_last_modified','TEXT'),lambda r:_text(r.get('last_modified'))),
        (('sha256','TEXT'),lambda r:_text(r.get('sha256'))),
        (('source_url','TEXT'),lambda r:_text(r.get('url'))),
    ),
    'classification_review':(
        (('cycle_label','TEXT'),lambda r:_cycle_label(r.get('cycle'))),
        (('cycle','INTEGER'),lambda r:_count(r.get('cycle'))),
        (('committee_id','TEXT'),lambda r:_text(r.get('committee_id'))),
        (('committee','TEXT'),lambda r:_text(r.get('committee_name'))),
        (('official_type','TEXT'),lambda r:_text(r.get('master_committee_type'))),
        (('reporting_category','TEXT'),lambda r:_text(r.get('recipient_category'))),
        (('included_funding','REAL'),
         lambda r:_number(r.get('preliminary_crypto_funding'))),
    ),
}

DATA_QUALITY_COLUMNS=(('check_id','TEXT'),('source','TEXT'),('severity','TEXT'),
                      ('passed','INTEGER'),('detail','TEXT'),('metric','TEXT'))

# Additive audit tables shared by the Overview and Data workbook producers.
for _table, _fields in {
    'downstream_reach': [('cycle','INTEGER'),('view','TEXT'),('company','TEXT'),
        ('source_committee_id','TEXT'),('representative_root','TEXT'),('representative_path','TEXT')],
    'downstream_measurement_ledger': [('cycle','INTEGER'),('view','TEXT'),('company','TEXT'),
        ('candidate_id','TEXT'),('before_amount','REAL'),('after_amount','REAL'),('delta','REAL'),('basis','TEXT')],
    'downstream_sources': [('cycle','INTEGER'),('source','TEXT'),('path','TEXT'),
        ('sha256','TEXT'),('status','TEXT'),('policy','TEXT')],
}.items():
    STAGING_TABLES[_table] = tuple((
        (name, kind), lambda row, name=name, kind=kind:
        (_number(row.get(name)) if kind == 'REAL' else _count(row.get(name)) if kind == 'INTEGER' else _text(row.get(name)))
    ) for name, kind in _fields)


# --- transfer paths (F14) -------------------------------------------------------
#
# Paul approved option (a), 2026-09-26. The per-root expansion (every funded root x
# committee it reaches x edge out of it) ran to 8.9M rows for Crypto, past Excel's
# 1,048,576-row sheet and the Node string limit. The payload and both workbooks now
# carry one row per transfer edge and cycle with the number of roots that reach it
# and the largest of them; the full expansion goes only to the tidy staging store's
# transfer_path_detail table, written while it streams.
TRANSFER_PATH_TOP_ROOTS=5
TRANSFER_PATH_DETAIL='transfer_path_detail'
TRANSFER_PATH_DETAIL_COLUMNS=(('cycle_label','TEXT'),('cycle','INTEGER'),('funded_committee','TEXT'),
    ('source_committee','TEXT'),('destination_committee','TEXT'),('reported_net_transfer','REAL'),
    ('depth','INTEGER'),('representative_path','TEXT'),('traversal_status','TEXT'),('via_carryover','INTEGER'))
TRANSFER_PATH_DETAIL_INDEXES=(('transfer_path_detail_root',('cycle','funded_committee')),
    ('transfer_path_detail_edge',('cycle','source_committee','destination_committee')))


class TransferPathDetail:
    """The root x committee x edge rows, appended to the tidy staging store in batches.

    The store must already hold the R-side tables (as stage() requires); the table is
    replaced whole. Indexes are built once the rows are in, for the console's lookups
    by root and by edge.
    """

    def __init__(self,path,batch=50000):
        path=Path(path)
        if not path.is_file():
            raise ValueError(f'Tidy staging store does not exist: {path}')
        self.database=sqlite3.connect(str(path))
        tables={row[0] for row in self.database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'contributions' not in tables:
            self.database.close()
            raise ValueError(f'Tidy staging store holds no contributions table: {path}')
        self.database.execute(f'DROP TABLE IF EXISTS "{TRANSFER_PATH_DETAIL}"')
        self.database.execute('CREATE TABLE "{}" ({})'.format(TRANSFER_PATH_DETAIL,
            ','.join(f'"{name}" {kind}' for name,kind in TRANSFER_PATH_DETAIL_COLUMNS)))
        self.insert='INSERT INTO "{}" VALUES ({})'.format(TRANSFER_PATH_DETAIL,
            ','.join('?' for _ in TRANSFER_PATH_DETAIL_COLUMNS))
        self.batch,self.pending,self.rows=batch,[],0

    def write(self,cycle,row):
        self.pending.append((_cycle_label(cycle),_count(cycle),_text(row.get('root')),_text(row.get('source')),
            _text(row.get('destination')),_number(row.get('amount')),_count(row.get('depth')),
            _text(row.get('path')),_text(row.get('status')),1 if row.get('via_carryover') else 0))
        if len(self.pending)>=self.batch:self.flush()

    def flush(self):
        if self.pending:
            self.database.executemany(self.insert,self.pending)
            self.rows+=len(self.pending);self.pending=[]

    def close(self):
        self.flush()
        for name,columns in TRANSFER_PATH_DETAIL_INDEXES:
            self.database.execute('CREATE INDEX "{}" ON "{}" ({})'.format(
                name,TRANSFER_PATH_DETAIL,','.join(f'"{column}"' for column in columns)))
        self.database.commit();self.database.close()
        return self.rows


def carryover_starts(edges,roots):
    """{root: [(committee, route)]}: each committee whose gate a root's previous-cycle
    money opened (F12 carryover), from the route the export wrote on its edges."""
    starts=defaultdict(dict)
    for edge in edges:
        route=edge.get('source_carryover_route') or ''
        if edge.get('source_gate_source')!='carryover' or not route:continue
        root=route.split(' > ')[0]
        if root in roots:starts[root].setdefault(edge['source'],route)
    return {root:sorted(found.items()) for root,found in starts.items()}


def transfer_path_edges(cycle,roots,edges,stop_nodes,root_funding,rule=None,detail=None):
    """One row per transfer edge that a funded root reaches, from the streamed expansion.

    roots_reaching counts the roots whose breadth-first routes reach the edge (each
    root reaches an edge at most once); top_roots names the largest of them by their
    funding; status is candidate_committee (terminal), followed, or cycle (the edge
    only ever returns to a committee already on the route).
    """
    by_key={(edge['source'],edge['destination']):edge for edge in edges if edge['amount']>0}
    seen={}
    starts=carryover_starts(edges,roots)
    for row in reachable_rows(roots,edges,max_depth=None,stop_nodes=stop_nodes,starts=starts):
        key=(row['source'],row['destination'])
        entry=seen.get(key)
        if entry is None:
            entry=seen[key]={'roots':0,'depth':row['depth'],'statuses':set(),'top':[],'carried':0}
        entry['roots']+=1
        entry['depth']=min(entry['depth'],row['depth'])
        entry['statuses'].add(row['status'])
        if row.get('via_carryover'):entry['carried']+=1
        rank=(-float(root_funding.get(row['root'],0.0)),row['root'])
        top=entry['top']
        if len(top)<TRANSFER_PATH_TOP_ROOTS or rank<top[-1]:
            top.append(rank);top.sort();del top[TRANSFER_PATH_TOP_ROOTS:]
        if detail is not None:detail.write(cycle,row)
    rule=rule or {}
    output=[]
    for key in sorted(seen):
        edge,entry=by_key[key],seen[key]
        status='candidate_committee' if key[1] in stop_nodes else 'followed' if 'followed' in entry['statuses'] else 'cycle'
        output.append({'cycle':cycle,'source':key[0],'destination':key[1],'amount':edge['amount'],
            'record_count':edge.get('record_count'),'first_date':edge.get('first_date'),'last_date':edge.get('last_date'),
            'status':status,'roots_reaching':entry['roots'],'min_depth':entry['depth'],
            'top_roots':'; '.join(root for _,root in entry['top']),'roots_via_carryover':entry['carried'],
            'date_rule':rule.get('rule',''),'source_gate':edge.get('source_gate'),
            'source_gate_source':edge.get('source_gate_source'),
            'excluded_before_gate_records':edge.get('excluded_before_gate_records'),
            'excluded_before_gate_amount':edge.get('excluded_before_gate_amount'),
            'source_carryover_route':edge.get('source_carryover_route') or '',
            'carryover_status':rule.get('carryover_status','')})
    return output


def stage(payload,path):
    """Append the Python-side tables to an existing tidy staging store.

    The store MUST already hold the R-side tables. Connecting to a missing file
    would create an empty database and publish a companion workbook with seven
    sheets and no receipts - a failure that looks like a successful build.
    """
    path=Path(path)
    if not path.is_file():
        raise ValueError(f'Tidy staging store does not exist: {path}')
    with sqlite3.connect(str(path)) as database:
        existing={row[0] for row in database.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if 'contributions' not in existing:
            raise ValueError(
                f'Tidy staging store holds no contributions table: {path}')
        written={}
        for table,specification in STAGING_TABLES.items():
            columns=[column for column,_ in specification]
            database.execute(f'DROP TABLE IF EXISTS "{table}"')
            database.execute('CREATE TABLE "{}" ({})'.format(
                table,','.join(f'"{name}" {kind}' for name,kind in columns)))
            rows=[tuple(extract(row) for _,extract in specification)
                  for row in payload.get(table,[]) or []]
            if rows:
                database.executemany('INSERT INTO "{}" VALUES ({})'.format(
                    table,','.join('?' for _ in columns)),rows)
            written[table]=len(rows)
        if 'data_quality' not in existing:
            database.execute('CREATE TABLE "data_quality" ({})'.format(
                ','.join(f'"{name}" {kind}' for name,kind in DATA_QUALITY_COLUMNS)))
        # Replace only this producer's rows: the R guards own source 'r_guard'
        # and the workbook writer appends source 'writer' after this runs.
        database.execute("DELETE FROM data_quality WHERE source='enrichment'")
        checks=[(_text(check.get('id')),'enrichment',_text(check.get('severity')),
                 _flag(check.get('passed')),_text(check.get('detail')),
                 _text(check.get('metric')))
                for check in payload.get('quality_checks',[]) or []]
        if checks:
            database.executemany('INSERT INTO data_quality VALUES (?,?,?,?,?,?)',checks)
        written['data_quality(enrichment)']=len(checks)
        database.commit()
    return written


# --- transfer index status ----------------------------------------------------
#
# Paul's ruling (2026-09-25, F2): a cycle whose transfer index is stale, missing
# or cannot place its source publishes NO transfer-derived numbers. Transfer
# Paths and Allocation Estimates carry no rows for it, and its conditional
# estimates are blank (None), never 0.00: a zero claims the network was traced
# and found nothing, a blank says it was not traced. Direct candidate funding
# does not depend on the index and publishes as before. The status is named per
# cycle in metadata.transfer_index_status ({"2024": "stale", ...}), in a
# transfer_index_<cycle> quality check and in the audit manifest.
TRANSFER_INDEX_STATUSES=('current','stale','missing','unresolved')


def transfer_index_status(source_coverage):
    """current | stale | missing | unresolved for one cycle's transfer_coverage rows.

    R/fec_transfer_graph.R writes one row per cycle: 'indexed', 'stale_transfer_index'
    (another index version or policy, or a raw OTH file that changed since the
    build) or 'missing_transfer_source'. No row at all is missing. An indexed
    source that cannot place malformed rows (unresolved_all_sources), or a status
    this module does not know, is unresolved: neither can be traced.
    """
    if not source_coverage:return 'missing'
    found=set()
    for row in source_coverage:
        status=row.get('status')
        if status=='stale_transfer_index':found.add('stale')
        elif status=='missing_transfer_source':found.add('missing')
        elif status!='indexed' or row.get('unresolved_all_sources'):found.add('unresolved')
        else:found.add('current')
    return next(name for name in ('stale','missing','unresolved','current') if name in found)


def transfer_rebuild_command(directory,cycle):
    """The command that writes the index version R/fec_transfer_graph.R requires."""
    tools=Path(__file__).resolve().parent
    script=tools/'fec_transfer_graph.py'
    headers=tools.parent/'state'/'cross-reference'/'filing-headers'
    command=f'python "{script}" --directory "{Path(directory).resolve()}" --cycles {cycle}'
    if headers.is_dir():command+=f' --filing-header-directory "{headers}"'
    return command


def not_traced_note(cycle,status,directory):
    return f'Not traced: the transfer index for {cycle} is {status}; rebuild with {transfer_rebuild_command(directory,cycle)}'


def audit_transfer_index(payload):
    """The named status of the cycles an audit bundle publishes; {} for a payload enriched before it existed."""
    metadata=payload.get('metadata') or {}
    if 'transfer_index_status' not in metadata:return {}
    cycles={str(row.get('cycle')) for row in payload.get('cycles') or []}
    return {'transfer_index_status':{k:v for k,v in metadata['transfer_index_status'].items() if k in cycles},
            'transfer_index_notes':{k:v for k,v in (metadata.get('transfer_index_notes') or {}).items() if k in cycles}}


def enrich(payload,directory,detail=None):
    paths=[];estimates=[];coverage=[];source_dates=[];review=[]
    index_status={};index_notes={};untraced=set()
    masters={(r['cycle'],r['candidate_id']):r for r in payload.get('candidate_master',[])}
    links=defaultdict(set)
    for row in payload.get('candidate_committee_links',[]):
        if row['designation'] in ('P','A'):links[row['cycle'],row['committee_id']].add(row['candidate_id'])
    grouped=defaultdict(float)
    cohorts=defaultdict(lambda: {'amount':0., 'receipt_count':0, 'first_date':'', 'last_date':''})
    for row in payload['raw_rows']:
        if row['include_in_analysis']=='Yes':
            grouped[row['cycle'],row['committee_id']]+=row['amount']
            key=(row['cycle'],row.get('company',''),row.get('employee_tier',''),row['committee_id'],row.get('receipt_role','unverified_receipt_role'),
                 row.get('funding_source_type','Unverified source type'),
                 row.get('funding_direction', 'Into company PAC' if row.get('recipient_category')=='Company PAC' else 'To named recipient'))
            cohort=cohorts[key]
            cohort['amount']+=row['amount'];cohort['receipt_count']+=1
            day=row.get('date','')
            if day:
                cohort['first_date']=min(cohort['first_date'] or day,day)
                cohort['last_date']=max(cohort['last_date'],day)
    totals=defaultdict(lambda:{'direct':0.,'estimated_non_opposition':0.,'estimated_opposition':0.})
    for cycle in (c['cycle'] for c in payload['cycles']):
        funding={committee:value for (year,committee),value in grouped.items() if year==cycle and committee}
        direct_ids={committee for (year,committee) in links if year==cycle}
        for committee in direct_ids:
            if committee not in funding:continue
            candidates=links[cycle,committee]
            if len(candidates)==1:totals[cycle,next(iter(candidates))]['direct']+=funding.pop(committee)
            else:
                amount=funding.pop(committee)
                coverage.append({'cycle':cycle,'root':committee,'input_net_funding':amount,
                    'model_funding':max(0,amount),'estimated_candidate_activity':0,
                    'unresolved':max(0,amount),'negative_net_funding_excluded':min(0,amount),
                    'unresolved_reasons':{'ambiguous_authorized_candidate':max(0,amount)}})
        # Named before anything transfer-derived is computed: a cycle that is
        # not current keeps its direct candidate funding (above) and nothing else.
        status=transfer_index_status([r for r in payload['metadata'].get('transfer_coverage',[]) if r['cycle']==cycle])
        index_status[str(cycle)]=status
        if status!='current':
            index_notes[str(cycle)]=not_traced_note(cycle,status,directory)
            untraced.add(cycle)
            continue
        edges=[row for row in payload.get('transfer_edges',[]) if row['cycle']==cycle]
        activity=[{'source':row['source_committee_id'],'candidate':row['candidate_id'],
                   'direction':row['support_oppose'],'amount':row['preliminary_included_amount'],
                   'transfer_records':row.get('transfer_records',[])}
                  for row in payload['downstream_activity'] if row['cycle']==cycle]
        source_coverage=[r for r in payload['metadata'].get('transfer_coverage',[]) if r['cycle']==cycle]
        # A committee holding one unplaceable row still has clean edges. Its
        # withheld dollars become a share of its own outflow (both sides carry
        # the size), never a blacklist over everything downstream of it.
        withheld=defaultdict(float)
        for row in source_coverage:
            for node,value in _amount_map(row.get('unresolved_source_amounts')).items():
                amount=_number(value)
                if node and amount and amount>0:withheld[node]+=amount
        for row in payload['downstream_activity']:
            if row['cycle']!=cycle:continue
            amount=_number(row.get('unresolved_version_amount'))
            if amount and amount>0:withheld[row['source_committee_id']]+=amount
        # A malformed source file or a stale index says nothing about ANY
        # committee in the cycle, so that case keeps the blanket treatment.
        unresolved_nodes=set()
        if any(r.get('unresolved_all_sources') or r['status']=='stale_transfer_index' for r in source_coverage):
            unresolved_nodes.update(funding)
        company_funding=defaultdict(float)
        for row in payload['raw_rows']:
            if row['cycle']==cycle and row['include_in_analysis']=='Yes':
                company_funding[row.get('company',''),row['committee_id']]+=row['amount']
        route_roots={committee for (_,committee),value in company_funding.items()
                     if value>0 and committee and committee not in direct_ids}
        root_funding=defaultdict(float)
        for (_,committee),value in company_funding.items():
            if value>0:root_funding[committee]+=value
        rule=next((r.get('date_rule') for r in source_coverage if isinstance(r.get('date_rule'),dict)),None)
        paths.extend(transfer_path_edges(cycle,route_roots,edges,direct_ids,root_funding,rule,detail))
        assigned,unresolved=allocate(funding,edges,activity,stop_nodes=direct_ids,
            candidate_links={committee:links[cycle,committee] for committee in direct_ids},
            unresolved_nodes=unresolved_nodes,unresolved_amounts=withheld)
        for row in assigned:
            candidate=masters.get((cycle,row['candidate_id']),{})
            estimates.append({**row,'cycle':cycle,'candidate_name':candidate.get('candidate_name',row['candidate_id'])})
            field='estimated_opposition' if row['direction']=='Oppose' else 'estimated_non_opposition'
            totals[cycle,row['candidate_id']][field]+=row['estimated_amount']
        coverage.extend(dict(row,cycle=cycle) for row in unresolved)
    summary=[]
    for (cycle,candidate_id),values in totals.items():
        candidate=masters.get((cycle,candidate_id),{})
        if cycle in untraced:
            # Not traced is blank, never 0.00; the direct funding stands.
            values=dict(values,estimated_non_opposition=None,estimated_opposition=None)
        summary.append({'industry':payload['metadata']['industry'],'cycle':cycle,'candidate_id':candidate_id,
            'candidate_name':candidate.get('candidate_name',candidate_id),'party':candidate.get('candidate_party',''),
            **values,'direct_plus_estimated_non_opposition':None if cycle in untraced else values['direct']+values['estimated_non_opposition']})
    for file in [*directory.glob('*/source-receipt.json'), *(directory.parent/'fec_committee_master').glob('*/source-receipt.json')]:
        source=json.loads(file.read_text(encoding='utf-8'))
        source_dates.append({'source':source['family'],'cycle':source['cycle'],'checked_at':source['checked_at'],
                             'last_modified':source.get('last_modified'),'sha256':source['sha256'],'url':source['url']})
    for row in payload['committee_map']:
        if row['recipient_category']=='Unclassified' and row['preliminary_crypto_funding']:
            review.append({key:row.get(key) for key in ('cycle','committee_id','committee_name','master_committee_type','recipient_category','preliminary_crypto_funding')})
    payload.update(transfer_paths=paths,allocation_estimates=estimates,allocation_coverage=coverage,
                   funding_cohorts=[dict(cycle=k[0],company=k[1],funding_type=k[2],committee_id=k[3],receipt_role=k[4],funding_source_type=k[5],funding_direction=k[6],**v)
                                    for k,v in sorted(cohorts.items())],
                   attribution_summary=sorted(summary,key=lambda r:(r['cycle'],-(r['direct'] if r['direct_plus_estimated_non_opposition'] is None else r['direct_plus_estimated_non_opposition']),r['candidate_id'])),
                   source_freshness=source_dates,classification_review=review)
    payload['metadata']['allocation_method']='Conditional industry-level activity allocation across observed candidate activity and 24G/24K transfers, maximum 3 transfers. Not traced dollars, company attribution or a lower bound. Pools corporate and personal funding. Donation timing, in-kind liquidation, all-donor funding, retained cash and operating expenses are not modelled. Opposition stays against its named target; negative activity is excluded from weights. Use funding cohorts and dated PAC evidence for donor-specific review.'
    payload['metadata']['company_cash_attribution_available']=False
    payload['metadata']['allocation_conservation_verified']=True
    payload['metadata']['transfer_index_status']=index_status
    payload['metadata']['transfer_index_notes']=index_notes
    payload['quality_checks']=[*payload['metadata'].get('export_guards',[])]
    unverified=[row for row in payload['raw_rows'] if row.get('include_in_analysis')=='Yes'
                and row.get('receipt_role','unverified_receipt_role').startswith('unverified_')]
    payload['quality_checks'].append({'id':'receipt_role_metadata','severity':'warn','passed':not unverified,
        'detail':f'{len(unverified)} included rows lack sufficient filing-form/line metadata to verify their receipt role. Name matching alone does not verify a contribution; refresh legacy source rows before donor-specific cash attribution.'})
    for source in payload['metadata'].get('transfer_coverage',[]):
        # A cycle that is not current is reported once, by its named status below.
        if index_status.get(str(source['cycle']),'current')!='current':continue
        invalid=sum(source.get('counts',{}).get(key,0) for key in
            ('malformed_rows','invalid_transfer_dates','invalid_transfer_amounts','invalid_transfer_destinations','unresolved_rows'))
        payload['quality_checks'].append({'id':f"transfer_source_{source['cycle']}",
            'severity':'warn','passed':source['status']=='indexed' and invalid==0,'detail':json.dumps(source)})
    for cycle,status in index_status.items():
        evidence=[r for r in payload['metadata'].get('transfer_coverage',[]) if str(r.get('cycle'))==cycle]
        payload['quality_checks'].append({'id':f'transfer_index_{cycle}','severity':'info' if status=='current' else 'warn',
            'passed':status=='current','status':status,'cycle':int(cycle),
            'detail':f'Traced: the transfer index for {cycle} is current.' if status=='current'
                else index_notes[cycle]+' Evidence: '+json.dumps(evidence)})
    pending=[row for row in coverage if row['unresolved']>.005]
    payload['quality_checks'].append({'id':'allocation_coverage','severity':'warn','passed':not pending,
        'detail':f'{len(pending)} funding roots have unresolved allocation; consult allocation_coverage for amounts and reasons.'})
    payload['quality_checks'].append({'id':'allocation_conservation','severity':'fail','passed':True,
        'detail':'Arithmetic conservation does not establish complete data or traced dollars.'})
    return payload


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('payload',type=Path);parser.add_argument('directory',type=Path)
    parser.add_argument('--audit-directory',type=Path)
    parser.add_argument('--audit-only',action='store_true')
    parser.add_argument('--workbook',type=Path)
    # Absent means "no tidy staging": every existing invocation, including the
    # --audit-only companion publication at build time, is unchanged.
    parser.add_argument('--staging',type=Path,
                        help='Tidy staging SQLite written by R/tidy_stage.R; the Python-side tables are appended to it.')
    args=parser.parse_args()
    value=json.loads(args.payload.read_text(encoding='utf-8-sig'))
    # F14: the per-root transfer path expansion streams into the staging store.
    detail=TransferPathDetail(args.staging) if args.staging and not args.audit_only else None
    if not args.audit_only:value=enrich(value,args.directory,detail)
    detail_rows=detail.close() if detail is not None else None
    if args.audit_directory:
        args.audit_directory.mkdir(parents=True,exist_ok=True)
        for name in ('transfer_paths','allocation_estimates','allocation_coverage','attribution_summary','funding_cohorts','source_freshness','classification_review','quality_checks','downstream_reach','downstream_measurement_ledger','downstream_sources'):
            rows=value.get(name,[])
            fields=list(dict.fromkeys(key for row in rows for key in row)) or ['status']
            path=args.audit_directory/f'{name}.csv'
            temporary=path.with_suffix('.pending')
            with temporary.open('w',encoding='utf-8-sig',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
                for row in rows:
                    safe={}
                    for key,item in row.items():
                        if isinstance(item,(dict,list)):item=json.dumps(item)
                        if isinstance(item,str) and item[:1] in ('=','+','-','@'):item="'"+item
                        safe[key]=item
                    writer.writerow(safe)
            temporary.replace(path)
        if args.workbook:
            def signature(path):
                with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
            manifest={
                'version':1,'workbook_name':args.workbook.name,'workbook_sha256':signature(args.workbook),
                'payload_path':str(args.payload.resolve()),'payload_sha256':signature(args.payload),
                'csv_sha256':{path.name:signature(path) for path in args.audit_directory.glob('*.csv')}}
            # Additive: absent for a payload enriched before the status existed.
            manifest.update(audit_transfer_index(value))
            atomic_json(args.audit_directory/'audit-manifest.json',manifest)
    if not args.audit_only:atomic_json(args.payload,value)
    if args.staging and not args.audit_only:
        staged=stage(value,args.staging)
        staged[TRANSFER_PATH_DETAIL]=detail_rows
        print('Tidy staging:',json.dumps(staged,sort_keys=True),flush=True)
    print('Transfer paths:',len(value['transfer_paths']),'Allocation records:',len(value['allocation_estimates']),flush=True)


if __name__=='__main__':main()
