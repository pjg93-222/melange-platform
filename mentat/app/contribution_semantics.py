"""Typed analytical fields over the preserved observation grain.

No source schema changes: derived dimensions are SQL expressions and audit
fields are related by the already-verified physical row identity.
"""
from decimal import Decimal, ROUND_HALF_EVEN
from functools import lru_cache
import hashlib
from pathlib import Path
import math
import re
import sqlite3
import contribution_data as data
import contribution_query_store as store
import evidence_csv

STAMP=data._stamp(__file__)

def identity():
    """This file's content plus the earmark-conduit registry it reads: a changed registry is a different
    semantics, so no cached answer carrying a recipient category outlives it."""
    return hashlib.sha256((data._hash(__file__)+evidence_csv.conduit_fingerprint()).encode()).hexdigest()

# SHA is identity(): this file's hash plus the earmark-conduit registry's fingerprint (evidence_csv.conduit_fingerprint),
# so it is not the sha256 of this file alone and cannot be checked against one. Code that caches an answer carrying a
# recipient category keys it on this value, which is why a registry change must move it.
SHA=identity()
AUDIT={'source_id':'Source observation ID','raw_zip':'Recorded postal code','source_table':'Original source table',
       'source_url':'Source filing URL','reasons':'Accounting reasons (complete list)','evidence_ids':'Evidence references (complete list)'}
DERIVED={'month':'Contribution month','quarter':'Contribution quarter','weekday':'Contribution weekday',
         'review_required':'Needs accounting review','amount_sign':'Amount direction','amount_bin':'Contribution amount bin'}
RAW_NAMES=['transaction_id','original_sub_id','contributor_first_name','contributor_last_name','contributor_suffix',
 'contributor_aggregate_ytd','recipient_committee_type','recipient_committee_designation','entity_type','election_type',
 'fec_election_type_desc','receipt_type_full','memo_code','memoed_subtotal','filing_form','receipt_type','line_number_label',
 'back_reference_transaction_id','back_reference_schedule_name','memo_text','conduit_committee_id','conduit_committee_name',
 'amendment_indicator','report_year','report_type','file_num','two_year_transaction_period','line_number','load_date',
 'matched_variant','phase','match_confidence','scraped_at','is_conduit_leg','is_memo_leg','is_passthrough_leg','memo_review',
 'possible_amendment','superseded_by_amendment','duplicate_filing_version','duplicate_memo','is_refund','is_entity_contributor',
 'company_match_status','company_match_reason','excluded_reason']
RAW={'reported_'+key:'Reported '+key.replace('_',' ') for key in RAW_NAMES}
DIMENSIONS={**data.DIMENSIONS,**AUDIT,**DERIVED,**RAW}
EXTRA={'q1_amount':'First quartile observation amount','q3_amount':'Third quartile observation amount',
       'stddev_amount':'Population standard deviation of amounts','committees':'Distinct recipient committees',
       'companies':'Distinct attributed companies','active_days':'Days with observations',
       'first_date':'First observation date','last_date':'Last observation date','span_days':'Observed date span (days)'}
MEASURES={**data.MEASURES,**EXTRA}
COUNTS={'records','contributors','committees','companies','active_days','span_days'}
DATES={'first_date','last_date'}
MONEY=set(MEASURES)-COUNTS-DATES
TIME={'date','year','cycle','quarter','month'}

def check():
    if data._stamp(__file__)!=STAMP:raise ValueError('Field definitions changed. Restart the app to load current semantics.')

def fields():
    result=[]
    # Keep every existing field index stable for previously saved sheets.
    order=[k for k in data.DIMENSIONS if k not in data.DESTINATIONS]+list(data.MEASURES)+list(AUDIT)+list(DERIVED)+list(EXTRA)+list(RAW)+list(data.DESTINATIONS)
    for key in order:
        measure=key in MEASURES
        unit='date' if key in DATES else 'count' if key in COUNTS else 'cents' if measure else None
        kind='date' if key in DATES or key=='date' else 'boolean' if key=='review_required' else 'decimal' if measure and key not in COUNTS else 'integer' if key in COUNTS or key=='year' else 'string'
        table='Recipient destinations' if key in data.DESTINATIONS else 'Source evidence' if key in AUDIT else 'Recipient attribution' if key in {'recipient','committee_id','recipient_category','party'} else 'Contributor geography' if key in {'zip5','state','city','raw_zip'} else 'Contribution observations'
        description=('One value per source observation. List values stay intact; never exploded into copies of money.' if key in AUDIT else
            'Computed from selected individual observations, before chart pagination.' if measure else 'Groups source observations without changing their amounts.')
        if key=='amount_bin':description='Lower bound in cents; fixed-width bins include their lower bound and exclude the upper bound.'
        if key in RAW:
            table='Reported receipt metadata';kind='string'
            description='Raw reported metadata, not a current accounting decision. Conflicting values remain a complete JSON list; missing values remain blank. Never summed as contribution money.'
        result.append({'id':key,'label':MEASURES[key] if measure else DIMENSIONS[key],'role':'measure' if measure else 'dimension',
            'type':'number' if measure and key not in DATES else 'date' if key=='date' or key in DATES else 'category',
            'data_type':kind,'unit':unit,'table':table,'discrete':not measure,'is_time':key in TIME,
            'geographic_role':{'zip5':'postal_code','state':'state','city':'city'}.get(key),'description':description,
            'aggregation':'source aggregate' if measure else None})
    return result

def model():
    return {'grain':'One preserved source observation, identified by source_id; not necessarily one unique economic gift.',
        'tables':[
            {'name':'Contribution observations','grain':'One source observation','key':'source_id','source':'Verified analytical facts'},
            {'name':'Recipient attribution','grain':'One retained attribution per observation','key':'source_id','source':'Verified report/source attribution'},
            {'name':'Contributor geography','grain':'Recorded postal fields per observation','key':'source_id','source':'Source receipt geography'},
            {'name':'Source evidence','grain':'One audit record per observation; reference lists stay intact','key':'source_id','source':'Preserved analytical index'},
            {'name':'Reported receipt metadata','grain':'Source ID, industry and original table; conflicting metadata stays one value list','key':'source_id + industry + source_table','source':'Pinned contributions / corporate_giving tables'},
            {'name':'Census geography','grain':'Geographic feature and boundary vintage','key':'ZIP / state / Congress','source':'Official Census geometry'},
            {'name':'Report worksheets','grain':'Varies by sheet and section','key':'No automatic fact join','source':'All worksheets of the configured published reports'}],
        'relationships':[
            {'from':'Contribution observations','to':name,'keys':['source_id'],'cardinality':'one-to-one','matching':'All analytical observations retain one detail row','behavior':'Preserves row identity; measures aggregate once.'} for name in ['Recipient attribution','Contributor geography','Source evidence']
        ]+[
            {'from':'Contribution observations','to':'Reported receipt metadata','keys':['source_id','industry','source_table'],'cardinality':'many-to-one after grouping metadata variants','matching':'Some records or fields may be missing','behavior':'LEFT relationship preserves observations. Reported flags never replace accounting decisions.'},
            {'from':'Contributor geography','to':'Census geography','keys':['ZIP / state','boundary vintage'],'cardinality':'postal lookup; district overlap can be many-to-many','matching':'Some records match','behavior':'Overlay only. Unmatched money remains visible; no district allocation.'},
            {'from':'Contribution observations','to':'Report worksheets','keys':[],'cardinality':'Alternative summaries / separate tables','matching':'No automatic join','behavior':'Select Report worksheet to inspect every sheet; do not add alternative summaries together.'}],
        'hierarchies':{'Contribution date':['year','quarter','month','date'],'Donor geography':['state','city','zip5']},
        'notes':['These are fixed, audited relationships, not an editable arbitrary-join engine.',
                 'Candidate, committee spending, coverage and evidence sheets remain separate sources at their own grain.']}

def catalog(directory, sources=None):
    result=data.catalog(directory, sources);result['fields']=fields();result['model']=model()
    for item in result['datasets']:item['fields']=result['fields'];item['model']=result['model']
    return result

def validate(request):
    if not isinstance(request,dict):raise ValueError('Contribution requests must be objects.')
    # Reuse the original bounds and basis rules with known placeholder fields.
    basis,_,_,limit,offset=data._validate({**request,'dimensions':[],'measures':['amount']})
    dims=request.get('dimensions',['cycle']);measures=request.get('measures',['amount'])
    if not isinstance(dims,list) or len(dims)>8 or any(not isinstance(d,str) or d not in DIMENSIONS for d in dims) or len(set(dims))!=len(dims):raise ValueError('Choose up to eight distinct dimensions.')
    if not isinstance(measures,list) or not measures or any(not isinstance(m,str) or m not in MEASURES for m in measures) or len(set(measures))!=len(measures):raise ValueError('Choose valid distinct measures.')
    width=request.get('bin_width_cents',10000)
    if type(width) is not int or not 1<=width<=100000000000:raise ValueError('Bin width must be positive integer cents, at most one billion dollars.')
    return basis,dims,measures,limit,offset

def row_filter(filters,column):return data._row_filter(filters,column,set(DIMENSIONS)-set(data.DIMENSIONS))

def dependencies(refs):
    result=set(refs)&set(data.DIMENSIONS)
    if set(refs)&{'month','quarter','weekday','active_days','first_date','last_date','span_days'}:result.add('date')
    if 'committees' in refs:result.add('committee_id')
    if 'companies' in refs:result.add('company')
    return result

def raw_relation(db,source,refs):
    keys=[key for key in RAW if key in refs]
    if not keys:return None
    if not source.get('database'):return None
    db.execute('ATTACH DATABASE ? AS receipt_source',(Path(source['database']).resolve().as_uri()+'?mode=ro',))
    tables={r[0] for r in db.execute("SELECT name FROM receipt_source.sqlite_master WHERE type='table'")}
    selects=[]
    for name in ('contributions','corporate_giving'):
        if name not in tables:continue
        columns={r[1] for r in db.execute('PRAGMA receipt_source.table_info('+name+')')}
        if not {'sub_id','industry'}<=columns:continue
        unique=False
        for index in db.execute('PRAGMA receipt_source.index_list('+name+')'):
            if not index[2] or index[4]:continue
            names={r[2] for r in db.execute('PRAGMA receipt_source.index_info("'+index[1].replace('"','""')+'")')}
            if 'sub_id' in names and names<={'sub_id','industry'}:unique=True
        fields=[]
        for key in keys:
            raw=key[len('reported_'):]
            expression=(f'CAST("{raw}" AS TEXT)' if unique else f"CASE WHEN COUNT(DISTINCT quote(\"{raw}\"))<=1 THEN MAX(CAST(\"{raw}\" AS TEXT)) ELSE json_group_array(DISTINCT \"{raw}\") END") if raw in columns else 'NULL'
            fields.append('COALESCE('+expression+",'') AS \""+key+'"')
        selects.append("SELECT '"+name+"' AS _raw_table,CAST(sub_id AS TEXT) AS _raw_id,industry AS _raw_industry,"+','.join(fields)+' FROM receipt_source.'+name+' NOT INDEXED'+('' if unique else ' GROUP BY sub_id,industry'))
    return '('+' UNION ALL '.join(selects)+')' if selects else None

@lru_cache(maxsize=1)
def _index_columns():
    """The columns of an analytical index's facts table, in table order, from the schema that creates it."""
    with sqlite3.connect(':memory:') as db:
        data._schema(db)
        return [row[1] for row in db.execute('PRAGMA table_info(facts)')]

def _relation_columns(base):
    """The columns, in order, of one of the tables a chart or a record is read from: the analytical index
    ('facts'), its query view ('facts_full') or a records sidecar ('ordered'); None for any other name."""
    # 'facts' is the analytical index's table (data._schema), never the query projection's own 'facts' table: that one
    # holds only store.CORE, contributor_key and store.MONEY, with no recipient_category or committee_id. A projection
    # read that needs the category goes through its 'facts_full' view, so _conduit_relation may wrap only the
    # analytical index's facts, the projection's facts_full and a records sidecar's ordered table.
    if base=='facts':return _index_columns()
    if base=='facts_full':return ['fact_id',*store.CORE,'contributor_key',*store.MONEY,*store.DETAIL]
    if base=='ordered':
        import records_sidecar   # it imports this module
        return ['record_rank',*records_sidecar.COLUMNS]
    return None

def _conduit_relation(base):
    """`base` with recipient_category read through the earmark-conduit registry.

    The contribution facts stores were built from payloads that file ActBlue and WinRed under Super PAC
    (and the same label sits in each store's query view and records sidecar). Rebuilding every industry's
    store for a label would cost hundreds of megabytes apiece, so the label is applied where it is read:
    the same columns, in the same order, with this one computed. Amounts are not touched."""
    columns=_relation_columns(base)
    if columns is None:return base
    ids=sorted(evidence_csv.EARMARK_CONDUITS)
    if not all(re.fullmatch(r'C\d{8}',item) for item in ids):raise ValueError('The earmark conduit registry holds an invalid committee ID.')
    label="CASE WHEN committee_id IN ("+','.join("'"+item+"'" for item in ids)+") THEN '"+evidence_csv.CONDUIT+"' ELSE \"recipient_category\" END AS \"recipient_category\""
    return '(SELECT '+','.join(label if name=='recipient_category' else '"'+name+'"' for name in columns)+' FROM '+base+')'

def table(base,refs,request,original=False,raw_relation=None):
    refs=set(refs);extra=[]
    if 'recipient_category' in refs:base=_conduit_relation(base)
    if refs&set(AUDIT) and not original:extra += ['a."'+key+'" AS "'+key+'"' for key in AUDIT if key in refs]
    expressions={'month':"substr(date,1,7)",'quarter':"substr(date,1,4)||'-Q'||CAST((CAST(substr(date,6,2) AS INTEGER)+2)/3 AS INTEGER)",
        'weekday':"CASE strftime('%w',date) WHEN '0' THEN 'Sunday' WHEN '1' THEN 'Monday' WHEN '2' THEN 'Tuesday' WHEN '3' THEN 'Wednesday' WHEN '4' THEN 'Thursday' WHEN '5' THEN 'Friday' WHEN '6' THEN 'Saturday' END",
        'review_required':"CASE WHEN unresolved=1 THEN 'True' ELSE 'False' END"}
    amount=data.BASES[request.get('basis','receipt')][1];width=request.get('bin_width_cents',10000)
    expressions['amount_sign']=f"CASE WHEN {amount}>0 THEN 'Positive' WHEN {amount}<0 THEN 'Negative' ELSE 'Zero' END"
    expressions['amount_bin']=f"CAST(({amount}/{width}-CASE WHEN {amount}<0 AND {amount}%{width}!=0 THEN 1 ELSE 0 END)*{width} AS TEXT)"
    extra += [expr+' AS "'+key+'"' for key,expr in expressions.items() if key in refs]
    extra += [(f'COALESCE(s."{key}",\'\')' if raw_relation else "''")+' AS "'+key+'"' for key in RAW if key in refs]
    if not extra:return base
    join=' JOIN original.facts a ON a.rowid=f.fact_id' if refs&(set(AUDIT)|set(RAW)) and not original else ''
    if raw_relation and refs&set(RAW):
        origin='f' if original else 'a'
        join+=' LEFT JOIN '+raw_relation+' s ON s._raw_id='+origin+'.source_id AND s._raw_table='+origin+'.source_table AND s._raw_industry='+origin+'.industry'
    # Qualify original fields when both sources have matching column names.
    if join:
        import re
        extra=[re.sub(r'(?<![\w.])('+ '|'.join(['date','unresolved']+list(data.BASES[k][1] for k in data.BASES))+r')(?!\w)',r'f.\1',e) if not e.startswith('a.') else e for e in extra]
    return '(SELECT f.*,'+','.join(extra)+' FROM '+base+' f'+join+')'

def expressions(amount):
    result=data._expressions(amount)
    present=f'({amount}) IS NOT NULL'
    result.update(q1_amount=f'q1_cent({amount})',q3_amount=f'q3_cent({amount})',stddev_amount=f'stddev_cent({amount})',
        committees=f"COUNT(DISTINCT CASE WHEN {present} AND committee_id!='' THEN committee_id END)",
        companies=f"COUNT(DISTINCT CASE WHEN {present} AND company!='' THEN company END)",
        active_days=f'COUNT(DISTINCT CASE WHEN {present} THEN date END)',
        first_date=f'MIN(CASE WHEN {present} THEN date END)',last_date=f'MAX(CASE WHEN {present} THEN date END)',
        span_days=f'CAST(julianday(MAX(CASE WHEN {present} THEN date END))-julianday(MIN(CASE WHEN {present} THEN date END)) AS INTEGER)')
    return result

class Quantile:
    q=Decimal('.25')
    def __init__(self):self.values=[]
    def step(self,value):
        if value is not None:self.values.append(value)
    def finalize(self):
        if not self.values:return None
        self.values.sort();at=Decimal(len(self.values)-1)*self.q;lo=int(at);hi=min(lo+1,len(self.values)-1)
        return int((Decimal(self.values[lo])+(Decimal(self.values[hi])-Decimal(self.values[lo]))*(at-lo)).quantize(Decimal('1'),rounding=ROUND_HALF_EVEN))
class ThirdQuartile(Quantile):q=Decimal('.75')
class Stddev:
    def __init__(self):self.n=0;self.mean=0.;self.m2=0.
    def step(self,value):
        if value is None:return
        self.n+=1;delta=value-self.mean;self.mean+=delta/self.n;self.m2+=delta*(value-self.mean)
    def finalize(self):return round(math.sqrt(max(0,self.m2/self.n))) if self.n else None

def register(db):
    db.create_aggregate('q1_cent',1,Quantile);db.create_aggregate('q3_cent',1,ThirdQuartile);db.create_aggregate('stddev_cent',1,Stddev)
