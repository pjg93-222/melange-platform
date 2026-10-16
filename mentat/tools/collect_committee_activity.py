"""Resumable disclosed-spending frontier; no origin-dollar attribution or implicit collection.

Every explicit reporting committee remains traversable, including campaign
committees and negative/zero relationships. A finite physical request allowance
pauses work; it never defines completeness. Importing this module makes no calls.
"""
from contextlib import contextmanager,nullcontext
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
import argparse,hashlib,json,os,re,sqlite3,time,uuid
from pathlib import Path
from urllib.error import HTTPError,URLError
from spending_endpoint_contracts import (CONTRACTS,activation_plan,bind_enablement,verify_enablement,
 EndpointContractRefused,legacy_cursor_valid,query as contract_query,inspect_page as inspect_contract_page)

KINDS=('B','E_regular','E_notice','F','H4','communication_costs','electioneering','totals_reports_unitemized','C_loans','D_debts','efile_amendment_timing')
SUPPORTED={'B':('schedules/schedule_b/','disbursement_amount'),
 'E_regular':('schedules/schedule_e/','expenditure_amount'),
 'E_notice':('schedules/schedule_e/','expenditure_amount'),
 'A':('schedules/schedule_a/','contribution_receipt_amount')}
COMMITTEE=re.compile(r'C[0-9]{8}');CANDIDATE=re.compile(r'[HSP][A-Z0-9]{8}')
SCHEMA_VERSION=2
# Only verified paid-counterparty fields may create collection edges. Schedule
# E candidate_id is a supported/opposed beneficiary, not a money recipient.
COUNTERPARTY_C_FIELDS={'B':('recipient_committee_id',),'E_regular':(),'E_notice':(),'A':()}
def now():return datetime.now(timezone.utc).isoformat()
def exact_json(value):
 if isinstance(value,Decimal):return str(value)
 raise TypeError(type(value).__name__)
def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),default=exact_json,allow_nan=False)
def digest(value):return hashlib.sha256(encoded(value).encode()).hexdigest()
def sha(path):
 with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
class ArchiveBindingFailed(ValueError):pass
def captured_seeds(path):
 raw=Path(path).read_bytes();fingerprint=hashlib.sha256(raw).hexdigest();document=json.loads(raw)
 if not isinstance(document,dict)or document.get('schema_version')!=1 or not isinstance(document.get('seed_units'),list):raise ValueError('Reviewed seed schema1 and seed_units array required.')
 return document['seed_units'],{'path':str(path),'sha256':fingerprint,'bytes':len(raw)},raw
def seed_after(receipt):
 try:after=sha(receipt['path'])
 except OSError:after=None
 return {**receipt,'sha256_after':after,'after_unchanged':after==receipt['sha256']}

SCHEMA='''
CREATE TABLE IF NOT EXISTS units(committee_id TEXT NOT NULL,cycle INTEGER NOT NULL,created TEXT NOT NULL,PRIMARY KEY(committee_id,cycle));
CREATE TABLE IF NOT EXISTS origins(committee_id TEXT NOT NULL,cycle INTEGER NOT NULL,origin_id TEXT NOT NULL,metadata_json TEXT NOT NULL,PRIMARY KEY(committee_id,cycle,origin_id));
CREATE TABLE IF NOT EXISTS endpoints(committee_id TEXT NOT NULL,cycle INTEGER NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL,request_cursor_json TEXT NOT NULL DEFAULT '{}',expected_count INTEGER,rows_stored INTEGER NOT NULL DEFAULT 0,pages_stored INTEGER NOT NULL DEFAULT 0,last_error TEXT,coverage_issues_json TEXT NOT NULL DEFAULT '[]',attempt_count INTEGER NOT NULL DEFAULT 0,last_attempt_at REAL,retry_after REAL,PRIMARY KEY(committee_id,cycle,kind));
CREATE TABLE IF NOT EXISTS pages(committee_id TEXT NOT NULL,cycle INTEGER NOT NULL,kind TEXT NOT NULL,page_no INTEGER NOT NULL,request_query_json TEXT NOT NULL,response_pagination_json TEXT NOT NULL,next_cursor_json TEXT NOT NULL,archive_path TEXT NOT NULL,archive_sha256 TEXT NOT NULL,archive_bytes INTEGER NOT NULL,records_count INTEGER NOT NULL,PRIMARY KEY(committee_id,cycle,kind,page_no));
CREATE TABLE IF NOT EXISTS observations(committee_id TEXT NOT NULL,cycle INTEGER NOT NULL,kind TEXT NOT NULL,observation_id TEXT NOT NULL,page_no INTEGER NOT NULL,row_ordinal INTEGER NOT NULL,source_id TEXT,raw_json TEXT NOT NULL,raw_row_sha256 TEXT NOT NULL,amount_text TEXT,amount_field TEXT NOT NULL,amount_status TEXT NOT NULL,PRIMARY KEY(committee_id,cycle,kind,observation_id));
CREATE TABLE IF NOT EXISTS edges(committee_id TEXT NOT NULL,cycle INTEGER NOT NULL,kind TEXT NOT NULL,observation_id TEXT NOT NULL,source_id TEXT,destination_committee_id TEXT NOT NULL,destination_cycle INTEGER NOT NULL,amount_text TEXT,raw_row_sha256 TEXT NOT NULL,identity_status TEXT NOT NULL,counterparty_evidence_json TEXT NOT NULL,PRIMARY KEY(committee_id,cycle,kind,observation_id,destination_committee_id,destination_cycle));
CREATE TABLE IF NOT EXISTS counterparty_conflicts(committee_id TEXT NOT NULL,cycle INTEGER NOT NULL,kind TEXT NOT NULL,observation_id TEXT NOT NULL,source_id TEXT,flat_committee_id TEXT NOT NULL,nested_committee_id TEXT NOT NULL,status TEXT NOT NULL,PRIMARY KEY(committee_id,cycle,kind,observation_id));
CREATE TABLE IF NOT EXISTS candidate_resolutions(candidate_id TEXT NOT NULL,cycle INTEGER NOT NULL,committee_id TEXT NOT NULL,kind TEXT NOT NULL,source_id TEXT NOT NULL,status TEXT NOT NULL,PRIMARY KEY(candidate_id,cycle,committee_id,kind,source_id));
CREATE TABLE IF NOT EXISTS candidate_references(candidate_id TEXT NOT NULL,cycle INTEGER NOT NULL,committee_id TEXT NOT NULL,kind TEXT NOT NULL,observation_id TEXT NOT NULL,source_id TEXT,field TEXT NOT NULL,role TEXT NOT NULL,PRIMARY KEY(candidate_id,cycle,committee_id,kind,observation_id,field));
CREATE TABLE IF NOT EXISTS leaf_coverage(committee_id TEXT NOT NULL,cycle INTEGER NOT NULL,kind TEXT NOT NULL,observation_id TEXT NOT NULL,source_id TEXT,reason TEXT NOT NULL,raw_row_sha256 TEXT NOT NULL,PRIMARY KEY(committee_id,cycle,kind,observation_id));
CREATE TABLE IF NOT EXISTS failures(id INTEGER PRIMARY KEY,committee_id TEXT,cycle INTEGER,kind TEXT,at TEXT NOT NULL,error_type TEXT NOT NULL,detail TEXT NOT NULL,request_query_json TEXT,archive_record_json TEXT);
CREATE INDEX IF NOT EXISTS frontier_pending ON endpoints(status,kind);
CREATE INDEX IF NOT EXISTS source_occurrences ON observations(committee_id,cycle,kind,source_id);
'''

class Frontier:
 def __init__(self,path,*,optional_receipts=False,include_later_cycle_context=False,enabled_endpoint_contracts=()):
  requested=activation_plan([],enabled_endpoint_contracts)["enabled_contract_ids"]
  self.endpoint_contracts={kind:value for kind,value in CONTRACTS.items()if value["contract_id"]in requested}
  self.supported={**SUPPORTED,**{kind:(value["endpoint"],value["amount_field"])for kind,value in self.endpoint_contracts.items()}}
  self.counterparty_fields={**COUNTERPARTY_C_FIELDS,**{kind:value["paid_recipient_committee_fields"]for kind,value in self.endpoint_contracts.items()}}
  self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
  self.db=sqlite3.connect(self.path,timeout=1,isolation_level=None);self.db.row_factory=sqlite3.Row
  version=self.db.execute('PRAGMA user_version').fetchone()[0]
  existing={r[0]for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  if existing and version!=SCHEMA_VERSION:self.db.close();raise ValueError('Unknown frontier schema/version; no automatic migration or provider admission.')
  if existing:
   model=sqlite3.connect(':memory:');model.executescript(SCHEMA);expected={r[0]for r in model.execute("SELECT name FROM sqlite_master WHERE type='table'")}
   valid=existing==expected and all([tuple(r)for r in self.db.execute('PRAGMA table_info('+name+')')]==[tuple(r)for r in model.execute('PRAGMA table_info('+name+')')]for name in expected);model.close()
   if not valid:self.db.close();raise ValueError('Frontier schema differs from reviewed contract; no automatic migration.')
  self.db.execute('PRAGMA journal_mode=DELETE');self.db.execute('PRAGMA synchronous=FULL')
  if not existing:self.db.executescript(SCHEMA);self.db.execute('PRAGMA user_version='+str(SCHEMA_VERSION))
  self.optional_receipts=optional_receipts
  self.include_later_cycle_context=include_later_cycle_context
  try:
   # Without enablement nothing is written, so a read-only open never contends for the writer lease.
   with(self.transaction()if requested else nullcontext()):
    active={row[0]for row in self.db.execute("SELECT DISTINCT kind FROM endpoints WHERE status!='unsupported'")}
    if active-set(self.supported):raise EndpointContractRefused("Active endpoint state lacks its explicit pinned contract; no fallback.")
    self.contract_enablement=bind_enablement(self.db,self.path,requested)
  except BaseException:self.db.close();raise
 @contextmanager
 def transaction(self):
  # Admission and fetch occur while this exclusive-writer transaction is held.
  # A crash releases SQLite ownership; unfinished page data/checkpoint rolls back.
  self.db.execute('BEGIN IMMEDIATE')
  try:yield
  except BaseException:self.db.execute('ROLLBACK');raise
  else:self.db.execute('COMMIT')
 def _unit(self,committee,cycle):
  if not isinstance(committee,str)or not COMMITTEE.fullmatch(committee)or type(cycle)is not int or cycle not in(2022,2024,2026):raise ValueError('Explicit C committee ID and exact integer cycle2022/2024/2026 required.')
  self.db.execute('INSERT OR IGNORE INTO units VALUES(?,?,?)',(committee,cycle,now()))
  for kind in KINDS+(('A',) if self.optional_receipts else ()):
   state='pending' if kind in self.supported else 'unsupported'
   why=None if state=='pending' else 'Required disclosure family '+kind+' has no reviewed driver coverage contract yet; exhaustive coverage remains false.'
   self.db.execute('INSERT OR IGNORE INTO endpoints(committee_id,cycle,kind,status,last_error) VALUES(?,?,?,?,?)',(committee,cycle,kind,state,why))
 def _origin(self,committee,cycle,origin,metadata):
  todo=[(committee,cycle)];seen=set()
  while todo:
   key=todo.pop()
   if key in seen:continue
   seen.add(key);self._unit(*key)
   first=metadata.get('first_reached_cycle',cycle)
   local={**metadata,'first_reached_cycle':first,'activity_cycle':key[1],'later_cycle_context':key[1]>first}
   existing=self.db.execute('SELECT metadata_json FROM origins WHERE committee_id=? AND cycle=? AND origin_id=?',(*key,origin)).fetchone()
   changed=False
   if existing:
    prior=json.loads(existing[0]);previous=prior.get('first_reached_cycle',key[1])
    if first<previous:
     local={**prior,'first_reached_cycle':first,'activity_cycle':key[1],'later_cycle_context':key[1]>first}
     self.db.execute('UPDATE origins SET metadata_json=? WHERE committee_id=? AND cycle=? AND origin_id=?',(encoded(local),*key,origin));changed=True
   else:
    self.db.execute('INSERT INTO origins VALUES(?,?,?,?)',(*key,origin,encoded(local)));changed=True
   if changed:
    if self.include_later_cycle_context:todo.extend((key[0],yr)for yr in(2022,2024,2026)if yr>key[1])
    todo.extend((r[0],r[1])for r in self.db.execute('SELECT DISTINCT destination_committee_id,destination_cycle FROM edges WHERE committee_id=? AND cycle=?',key)if r[1]>=key[1])
 def seed(self,rows):
  with self.transaction():
   for row in rows:
    committee=row['committee_id'];cycle=row['cycle'];self._unit(committee,cycle)
    origins=row.get('origins') or row.get('industries') or [row.get('origin_id') or 'explicit-seed']
    for origin in origins:
     first=row.get('first_reached_cycle',(row.get('origin_metadata')or{}).get('first_reached_cycle',cycle))
     if type(first)is not int or first not in(2022,2024,2026)or first>cycle:raise ValueError('Origin first-reached cycle must be an exact permitted integer no later than activity cycle.')
     metadata={**(row.get('origin_metadata')or{'source':'explicit seed','scope':str(origin)}),'first_reached_cycle':first}
     for activity_cycle in(yr for yr in(2022,2024,2026)if yr>=cycle and(self.include_later_cycle_context or yr==cycle)):
      self._origin(committee,activity_cycle,str(origin),{**metadata,'activity_cycle':activity_cycle,'later_cycle_context':activity_cycle>metadata['first_reached_cycle']})
 def resume(self):
  with self.transaction():self.db.execute("UPDATE endpoints SET status='pending' WHERE status='blocked' AND retry_after<=?",(time.time(),))
 def _query(self,row):
  if row["kind"]in self.endpoint_contracts:
   return contract_query(row["kind"],row["committee_id"],row["cycle"],cursor=json.loads(row["request_cursor_json"]))
  if not legacy_cursor_valid(row["kind"],json.loads(row["request_cursor_json"]),stored_or_generated_page=True):
   raise ValueError("Stored pagination cursor could change unit selectors; endpoint recovery required before admission.")
  kind=row['kind'];q={'committee_id':row['committee_id'],'per_page':100}
  if kind.startswith('E_'):q.update(cycle=row['cycle'],is_notice='true' if kind=='E_notice' else 'false')
  else:q['two_year_transaction_period']=row['cycle']
  q.update(json.loads(row['request_cursor_json']));return q
 def _store_edge(self,unit,kind,sid,occurrence,row,amount,rowhash):
  # Incoming Schedule A supplies optional context, not downstream reachability.
  if kind=='A':return
  destinations={str(row.get(k) or '') for k in self.counterparty_fields[kind]}
  destinations={v for v in destinations if COMMITTEE.fullmatch(v)}
  identity_status='verified_counterparty_field';identity={}
  if kind=='B':
   flat=str(row.get('recipient_committee_id')or'');nested=row.get('recipient_committee')
   nested=str(nested.get('committee_id')or'')if isinstance(nested,dict)else''
   flat=flat if COMMITTEE.fullmatch(flat)else'';nested=nested if COMMITTEE.fullmatch(nested)else''
   identity={'flat_recipient_committee_id':flat or None,'nested_recipient_committee_id':nested or None}
   if nested:destinations.add(nested)
   if flat and nested and flat!=nested:
    identity_status='conflicting_possible_counterparties_not_two_payments'
    self.db.execute('INSERT INTO counterparty_conflicts VALUES(?,?,?,?,?,?,?,?)',(*unit,kind,occurrence,sid,flat,nested,'identity_unresolved'))
  for destination in sorted(destinations):
   self._unit(destination,unit[1]);self.db.execute('INSERT INTO edges VALUES(?,?,?,?,?,?,?,?,?,?,?)',(*unit,kind,occurrence,sid,destination,unit[1],amount,rowhash,identity_status,encoded(identity)))
   for origin in self.db.execute('SELECT origin_id,metadata_json FROM origins WHERE committee_id=? AND cycle=?',unit).fetchall():
    metadata=json.loads(origin[1]);metadata.setdefault('first_reached_cycle',unit[1])
    for activity_cycle in(yr for yr in(2022,2024,2026)if yr>=unit[1]and(self.include_later_cycle_context or yr==unit[1])):
     self._origin(destination,activity_cycle,origin[0],{**metadata,'activity_cycle':activity_cycle,'later_cycle_context':activity_cycle>metadata['first_reached_cycle']})
  candidates={k:str(row.get(k) or '')for k in('candidate_id','candidate_id_s','candidate_id_as_filed')if CANDIDATE.fullmatch(str(row.get(k)or''))}
  for field,candidate in candidates.items():
   role='supported_or_opposed_beneficiary_not_paid_counterparty'if kind.startswith('E_')else'associated_candidate_payment_role_unverified'
   if kind=='F':role='candidate_beneficiary_of_coordinated_party_spending_not_paid_counterparty'
   self.db.execute('INSERT OR IGNORE INTO candidate_references VALUES(?,?,?,?,?,?,?,?)',(candidate,unit[1],unit[0],kind,occurrence,sid,field,role))
  if not destinations:
   reason='No verified paid-counterparty reporting committee ID in this disclosure; candidate beneficiary/association is not a payment path. Not proof of a nonreporting counterparty or zero onward spending.'
   self.db.execute('INSERT INTO leaf_coverage VALUES(?,?,?,?,?,?,?)',(*unit,kind,occurrence,sid,reason,rowhash))
 def _failure(self,row,error,query,record,status,attempts):
  retry=time.time()+min(3600,60*(2**min(row['attempt_count'],6)))if status=='blocked'else None
  self.db.execute('UPDATE endpoints SET status=?,last_error=?,attempt_count=attempt_count+?,last_attempt_at=?,retry_after=? WHERE committee_id=? AND cycle=? AND kind=?',(status,str(error),attempts,time.time(),retry,row['committee_id'],row['cycle'],row['kind']))
  self.db.execute('INSERT INTO failures(committee_id,cycle,kind,at,error_type,detail,request_query_json,archive_record_json) VALUES(?,?,?,?,?,?,?,?)',(row['committee_id'],row['cycle'],row['kind'],now(),type(error).__name__,str(error),encoded(query),encoded(record) if record else None))
 def page(self,transport,row):
  unit=(row['committee_id'],row['cycle'])
  kind=row['kind'];endpoint=field=None
  query={};evidence=None
  attempts_before=transport.http_attempts;records_before=len(getattr(transport,'records',[]))
  try:
   if kind not in self.supported:raise EndpointContractRefused("Pending endpoint kind has no enabled pinned contract in this invocation; no fallback.")
   endpoint,field=self.supported[kind]
   verify_enablement(self.contract_enablement)
   query=self._query(row)
   data,evidence=transport.fetch(endpoint,query,exact_decimal=True)
   if evidence.get('status')!=200 or evidence.get('original_response_bytes')is not True or evidence.get('query')!=query or evidence.get('endpoint')!=endpoint:
    raise ArchiveBindingFailed('Successful original response evidence does not bind the exact request.')
   archive=Path(evidence['path'])
   if archive.stat().st_size!=evidence['bytes']or sha(archive)!=evidence['sha256']:
    raise ArchiveBindingFailed('Original response archive does not verify; no page admitted.')
   if not isinstance(data,dict)or not isinstance(data.get('results'),list):
    raise ValueError('Archived response has no result array; no page shape inferred.')
   pagination=data.get('pagination')or{}
   if not isinstance(pagination,dict):pagination={}
   count=pagination.get('count');batch=data['results']
   issues=set(json.loads(row['coverage_issues_json']))
   count_valid=type(count)is int and count>=0
   exact=count_valid and pagination.get('is_count_exact')is True
   if not exact:issues.add('count_exactness_unknown')
   if row['expected_count']is not None and count_valid and count!=row['expected_count']:
    issues.add('advertised_count_changed')
   expected=row['expected_count']if row['expected_count']is not None else(count if count_valid else None)
   page_no=row['pages_stored']+1
   for ordinal,observation in enumerate(batch):
    occurrence=evidence['sha256']+':'+str(page_no)+':'+str(ordinal)
    rowhash=digest(observation);raw=encoded(observation)
    sid=None;amount_text=None;amount_status='row_not_object'
    if isinstance(observation,dict):
     original=observation.get('sub_id')
     sid=str(original)if type(original)in(str,int)and str(original)else None
     if sid is None:issues.add('source_identifier_missing_or_invalid')
     elif self.db.execute('SELECT 1 FROM observations WHERE committee_id=? AND cycle=? AND kind=? AND source_id=?',(*unit,kind,sid)).fetchone():
      issues.add('repeated_source_identifier')
     amount=observation.get(field)
     amount_status='missing'
     if amount is not None:
      try:
       decimal=Decimal(str(amount))
       if not decimal.is_finite():raise InvalidOperation
       amount_text=str(decimal)
       amount_status='valid_decimal'if decimal*100==(decimal*100).to_integral_value()else'subcent'
      except InvalidOperation:amount_status='invalid'
     # Amount uncertainty does not erase an explicit paid-counterparty C ID.
     self._store_edge(unit,kind,sid,occurrence,observation,amount_text,rowhash)
    else:issues.add('result_row_not_object')
    self.db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(*unit,kind,occurrence,page_no,ordinal,sid,raw,rowhash,amount_text,field,amount_status))
   total=row['rows_stored']+len(batch)
   if kind in self.endpoint_contracts:
    prior_ids=[item[0]for item in self.db.execute("SELECT source_id FROM observations WHERE committee_id=? AND cycle=? AND kind=? AND page_no<? AND source_id IS NOT NULL",(*unit,kind,page_no))]
    progress=inspect_contract_page(kind,query,data,rows_before=row["rows_stored"],expected_count=row["expected_count"],seen_ids=prior_ids)
    issues.update(progress["issues"]);cursor=progress["next_cursor"]
    # Valid progress needs new source identifiers; a repeating provider or an exact count
    # already exceeded stops here as recovery-required instead of spending the allowance.
    known=set(prior_ids);page_ids=[item[0]for item in self.db.execute("SELECT source_id FROM observations WHERE committee_id=? AND cycle=? AND kind=? AND page_no=? AND source_id IS NOT NULL",(*unit,kind,page_no))]
    if cursor and(page_ids and all(item in known for item in page_ids)or exact and total>count):
     cursor={};issues.add("pagination_made_no_valid_progress")
    count_closed=progress["page_end_matches_count"]and not issues
    if not count_closed and not cursor and batch:issues.add("cursor_recovery_required")
    closed=count_closed or not cursor
    if not closed:
     next_query=contract_query(kind,unit[0],unit[1],cursor=cursor)
     repeated=bool(self.db.execute("SELECT 1 FROM pages WHERE committee_id=? AND cycle=? AND kind=? AND request_query_json=?",(*unit,kind,encoded(next_query))).fetchone())
     if encoded(cursor)==row["request_cursor_json"]or repeated:issues.add("cursor_recovery_required");closed=True
   else:
    if exact and total>count:issues.add('retained_occurrences_exceed_count')
    count_closed=exact and total==count and not issues
    next_cursor=pagination.get('last_indexes')or{}
    if not isinstance(next_cursor,dict):next_cursor={};issues.add('invalid_cursor_shape')
    if not legacy_cursor_valid(kind,next_cursor):next_cursor={};issues.add('unsafe_cursor_fields_require_recovery')
    next_cursor={k:v for k,v in next_cursor.items()if v is not None}
    # Existing B/E tests also exercise a numbered-page server response. The
    # endpoint-specific official offset contract is added separately for F.
    if not next_cursor and not({'invalid_cursor_shape','unsafe_cursor_fields_require_recovery'}&issues) and type(pagination.get('page'))is int and type(pagination.get('pages'))is int and pagination['page']<pagination['pages']:
     next_cursor={'page':pagination['page']+1}
    # Cursor-shape issues added above must invalidate a count-matched page too.
    count_closed=exact and total==count and not issues
    closed=count_closed or not batch
    if closed:
     cursor={}
     if not count_closed:issues.add('cursor_closed_without_exact_count_certificate')
    else:
     cursor=next_cursor
     next_query={k:v for k,v in query.items()if k not in json.loads(row['request_cursor_json'])};next_query.update(cursor)
     repeated=bool(self.db.execute('SELECT 1 FROM pages WHERE committee_id=? AND cycle=? AND kind=? AND request_query_json=?',(*unit,kind,encoded(next_query))).fetchone())
     if not cursor or encoded(cursor)==row['request_cursor_json']or repeated:
      issues.add('cursor_recovery_required');closed=True
   status='complete'if count_closed else'closed_unknown'if closed else'pending'
   self.db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?,?,?,?,?)',(*unit,kind,page_no,encoded(query),encoded(pagination),encoded(cursor),str(archive),evidence['sha256'],evidence['bytes'],len(batch)))
   self.db.execute('UPDATE endpoints SET status=?,request_cursor_json=?,expected_count=?,rows_stored=?,pages_stored=pages_stored+1,last_error=NULL,coverage_issues_json=?,attempt_count=attempt_count+?,last_attempt_at=?,retry_after=NULL WHERE committee_id=? AND cycle=? AND kind=?',(status,encoded(cursor),expected,total,encoded(sorted(issues)),transport.http_attempts-attempts_before,time.time(),*unit,kind))
   return True
  except Exception as error:
   if evidence is None and len(getattr(transport,'records',[]))>records_before:
    last=transport.records[-1]
    if last.get('query')==query and last.get('endpoint')==endpoint:evidence=last
   raise PageFailure(row,error,query,evidence,transport.http_attempts-attempts_before)from error
 def run(self,transport,*,max_failed_attempts=8):
  if type(max_failed_attempts)is not int or max_failed_attempts<1:raise ValueError('Positive failed-attempt allowance required.')
  self.resume();stopped=None;completed_pages=0;failures=0
  while True:
   try:
    with self.transaction():
     # One page per endpoint per round: outgoing endpoints receive fair turns.
     row=self.db.execute("SELECT * FROM endpoints WHERE status='pending' AND (? OR kind!='A') AND(kind!='A' OR NOT EXISTS(SELECT 1 FROM endpoints WHERE kind IN('B','E_regular','E_notice')AND status!='complete')) ORDER BY CASE kind WHEN 'A' THEN 1 ELSE 0 END,attempt_count,pages_stored,cycle,committee_id,kind LIMIT 1",(self.optional_receipts,)).fetchone()
     if row is None:break
     self.page(transport,row);completed_pages+=1
   except PageFailure as failed:
    source_refused=isinstance(failed.error,HTTPError)and failed.error.code in(400,404,405,410,422)
    # Refusals that are not about this endpoint's evidence leave it pending and unmodified.
    status='pending'if type(failed.error).__name__ in('FECAdmissionRefused','EndpointContractRefused')else'source_refused'if source_refused else'inconsistent'if isinstance(failed.error,(ValueError,AssertionError))and type(failed.error).__name__ not in('FECArchiveFailed','ArchiveBindingFailed')else'blocked'
    with self.transaction():self._failure(failed.row,failed.error,failed.query,failed.evidence,status,failed.attempts)
    failures+=1
    global_refusal=type(failed.error).__name__ in('FECAdmissionRefused','FECArchiveFailed','ArchiveBindingFailed','EndpointContractRefused')or isinstance(failed.error,sqlite3.Error)or isinstance(failed.error,HTTPError)and failed.error.code in(401,403,429)
    if global_refusal or failures>=max_failed_attempts:
     stopped={'error_type':type(failed.error).__name__,'detail':str(failed.error),'global_admission_archive_auth_quota_or_failure_allowance':True};break
  return self.report(completed_pages,stopped)
 def report(self,completed_pages=0,stopped=None):
  endpoint_counts={r[0]+':'+r[1]:r[2] for r in self.db.execute('SELECT kind,status,COUNT(*) FROM endpoints GROUP BY kind,status')}
  outgoing=sorted(kind for kind in self.supported if kind!='A')
  pending=self.db.execute("SELECT COUNT(*) FROM endpoints WHERE kind IN("+",".join("?"for _ in outgoing)+")AND status!='complete'",outgoing).fetchone()[0]
  count=lambda table:self.db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
  return {'schema_version':1,'ledger_schema_version':SCHEMA_VERSION,'generated_utc':now(),'ledger':str(self.path),'units':count('units'),'origins':count('origins'),'pages':count('pages'),'observations':count('observations'),'edges':count('edges'),'candidate_resolutions_required':count('candidate_resolutions'),'candidate_beneficiary_or_unverified_references':count('candidate_references'),'counterparty_identity_conflicts':count('counterparty_conflicts'),'leaf_disclosure_boundaries':count('leaf_coverage'),'endpoint_status_counts':endpoint_counts,'new_pages_this_invocation':completed_pages,'stop':stopped,'supported_outgoing_endpoints_complete':pending==0,'exhaustive_disclosed_spending_complete':False,'required_unsupported_endpoints':sorted({r[0]for r in self.db.execute("SELECT kind FROM endpoints WHERE status='unsupported'")}),
   'endpoint_contract_enablement':self.contract_enablement,'supported_outgoing_endpoint_kinds':outgoing,
   'later_cycle_context_explicitly_enabled':self.include_later_cycle_context,'later_context_missing_when_disabled':not self.include_later_cycle_context,'unknown_or_subcent_amount_occurrences':self.db.execute("SELECT COUNT(*) FROM observations WHERE amount_status!='valid_decimal'").fetchone()[0],
   'other_spending_families':'/'.join(['B/E']+[kind for kind in('F','H4')if kind in outgoing])+' are overlapping evidence. Other disclosure families, report/unitemized totals, loans/debts and efile timing require independent coverage.','legacy_snapshot_compatible':False,'snapshots_json_or_evidence_json_published':False,'incoming_receipts_not_requested_means_unknown_not_empty':True,'no_exact_origin_dollar_attribution':True,'no_B_E_grand_total':True,'UI_top_depth_or_page_limits_used':False}
 def close(self):self.db.close()

class PageFailure(Exception):
 def __init__(self,row,error,query,evidence,attempts):self.row,self.error,self.query,self.evidence,self.attempts=row,error,query,evidence,attempts;super().__init__(str(error))

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--state',required=True,type=Path);parser.add_argument('--seeds',required=True,type=Path);parser.add_argument('--max-requests',required=True,type=int);parser.add_argument('--max-failed-attempts',type=int,default=8);parser.add_argument('--optional-receipts',action='store_true');parser.add_argument('--include-later-cycle-context',action='store_true');parser.add_argument('--live',action='store_true')
 parser.add_argument('--enable-endpoint-contract',action='append',default=[],help='Explicit pinned F/H4 contract ID; repeatable, required on every restart after enablement.')
 args=parser.parse_args()
 if not args.live:raise SystemExit('No implicit provider calls. Review the seed/coverage plan and pass --live only after authorization.')
 from fec_accounted_client import AccountedTransport
 args.state.mkdir(parents=True,exist_ok=True);run_id=uuid.uuid4().hex;archive=args.state/('responses-'+run_id)
 rows,seed_receipt,seed_bytes=captured_seeds(args.seeds)
 copy=args.state/('seeds-'+seed_receipt['sha256']+'.json')
 if copy.exists():
  if copy.read_bytes()!=seed_bytes:raise ValueError('Captured seed copy conflicts with its fingerprint.')
 else:
  with copy.open('xb')as stream:stream.write(seed_bytes);stream.flush();os.fsync(stream.fileno())
 frontier=Frontier(args.state/'frontier.sqlite',optional_receipts=args.optional_receipts,include_later_cycle_context=args.include_later_cycle_context,enabled_endpoint_contracts=args.enable_endpoint_contract)
 try:
  frontier.seed(rows)
  with AccountedTransport.live(archive,args.max_requests)as transport:report=frontier.run(transport,max_failed_attempts=args.max_failed_attempts);report.update(http_attempts=transport.http_attempts,physical_request_cap=transport.maximum,failed_attempt_allowance=args.max_failed_attempts,raw_archive_directory=str(archive))
  report.update(ledger_sha256=sha(frontier.path),seed_receipt={**seed_after(seed_receipt),'captured_copy':str(copy)})
  output=args.state/('RECEIPT-'+run_id+'.json')
  with output.open('x',encoding='utf-8')as f:json.dump(report,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
  print(encoded({'receipt':str(output),'http_attempts':report['http_attempts'],'exhaustive_complete':False}))
 finally:frontier.close()

if __name__=='__main__':main()
