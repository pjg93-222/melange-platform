"""Resumable disclosed-spending frontier; no origin-dollar attribution or implicit collection.

Every explicit reporting committee remains traversable, including campaign
committees and negative/zero relationships. A finite physical request allowance
pauses work; it never defines completeness. Importing this module makes no calls.

Ledger schema 3 (D073) stores each origin once (origin_catalog) and each seeded unit/origin once (origin_seeds); which units an origin reaches is derived on demand by
origin_closure from the seeds, the archive-bound edges and the recorded later-cycle policy, so collecting an edge never writes per-unit origin rows. A schema-2 ledger is
refused; `--migrate-to-schema3 SRC DST` writes a NEW schema-3 ledger plus MIGRATION.json (lossless or refused) and never rewrites SRC.

One process: the main thread is the only SQLite writer and dispatcher; --workers N fetch threads admit, request and archive
but never touch the ledger. A run lease plus per-run START records make a run that dies before its receipt recoverable.
D072 adds campaign spending (totals and candidate_committees contracts, a persisted candidate priority) as additive tables on schema 3.
Offline subcommands (no provider call, no credentials, run lease held, never create a ledger): --recover-crashed-runs, --rebind-endpoint-contracts (append-only
superset intent version), --close-out-legacy-run (a pre-D071 run with no START: hot-journal rollback, orphan pairs quarantined, never replayed). A live run on a state
without frontier.sqlite is refused unless --init-new-ledger is given. A 429 cools only its key and returns its endpoint to pending at once.
D075 (opt-in container runtime; the default is unchanged): --transport broker runs the same loop on a key-free RemoteEvidenceTransport, a host broker holding the keys, the
shared quota ledger and the archive; --archive-root says where responses-<run> lives when that is not --state; MENTAT_STORED_PATH_MAP makes every RECORDED path the host's
Windows spelling while reads go through the container's mounts; --run-id and --stop-after-seconds serve a launcher; the run lease has its fcntl backend.
D076 (opt-in; without the flags every byte of behaviour is as before): --bulk-activity-index INDEX.json --expect-bulk-index-sha256 H persists a request-ORDER tier per B / E_regular / E_notice
endpoint in the additive table endpoint_bulk_tier: 1 = the verified bulk files observed rows for that committee/cycle/kind, 2 = unknown (the index does not cover that kind/cycle),
3 = the bulk files cover it and observed none. The queue is ordered by D072 campaign priority, then tier, then the existing fair turns; an endpoint with 3 or more attempts beyond its stored pages sorts after tier 3 and stays pending. A tier is only an ordering hint: it is never read by
completion, accounting, admission or a receipt count, tier 3 is still fetched (last) and is never complete, zero or skipped without its own fetched pages, and an index never creates a unit, edge or payment.
"""
from contextlib import contextmanager,nullcontext
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
import argparse,collections,hashlib,json,os,re,shutil,sqlite3,sys,threading,time,uuid
from concurrent.futures import FIRST_COMPLETED,ThreadPoolExecutor,wait
from pathlib import Path
from urllib.error import HTTPError,URLError
import origin_closure
import stored_paths
from spending_endpoint_contracts import (CONTRACTS,activation_plan,bind_enablement,verify_enablement,
 EndpointContractRefused,legacy_cursor_valid,query as contract_query,inspect_page as inspect_contract_page,
 candidate_committees_query,inspect_candidate_committees_page)

KINDS=('B','E_regular','E_notice','F','H4','communication_costs','electioneering','totals_reports_unitemized','C_loans','D_debts','efile_amendment_timing')
SUPPORTED={'B':('schedules/schedule_b/','disbursement_amount'),
 'E_regular':('schedules/schedule_e/','expenditure_amount'),
 'E_notice':('schedules/schedule_e/','expenditure_amount'),
 'A':('schedules/schedule_a/','contribution_receipt_amount')}
COMMITTEE=re.compile(r'C[0-9]{8}');CANDIDATE=re.compile(r'[HSP][A-Z0-9]{8}')
SCHEMA_VERSION=3
# totals is a per-committee family that is never a payment: no edges, no payees, no candidate links, no leaf boundaries.
NON_PAYMENT_KINDS=('totals',)
LOOKUP_ENDPOINT=re.compile(r'candidate/[HSP][A-Z0-9]{8}/committees/')
MIGRATION_COMMAND='python collect_committee_activity.py --migrate-to-schema3 <schema-2 ledger> <new schema-3 ledger> --later-cycle-context true|false [--seed-file <captured seed file>] [--snapshot-proof <SNAPSHOT.json>]'
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
def stored(path):return stored_paths.to_stored(path)   # the spelling to RECORD (identity unless MENTAT_STORED_PATH_MAP is set)
def hosted(path):return stored_paths.to_host(path)   # the path to READ a recorded spelling through
def _stored_enablement(value):
 pairs=stored_paths.pairs()
 if not pairs or not value.get('binding'):return value
 return{**value,'binding':{**value['binding'],'path':stored_paths.to_stored(value['binding']['path'],pairs)}}
def _verify_archive(evidence,message):
 """The recorded original body must exist and hash as the evidence says. Read through the container's mounts when the recorded spelling is the host's; a missing or stale
 read there fails closed as ArchiveBindingFailed (a global stop that leaves the ledger consistent). Host-native behaviour is unchanged."""
 archive=Path(evidence['path']);readable=Path(hosted(evidence['path']))
 try:ok=readable.stat().st_size==evidence['bytes']and sha(readable)==evidence['sha256']
 except OSError:
  if readable==archive:raise
  ok=False
 if not ok:raise ArchiveBindingFailed(message)
 return archive
class ArchiveBindingFailed(ValueError):pass
def captured_seeds(path):
 raw=Path(path).read_bytes();fingerprint=hashlib.sha256(raw).hexdigest();document=json.loads(raw)
 if not isinstance(document,dict)or document.get('schema_version')!=1 or not isinstance(document.get('seed_units'),list):raise ValueError('Reviewed seed schema1 and seed_units array required.')
 return document['seed_units'],{'path':str(path),'sha256':fingerprint,'bytes':len(raw)},raw
def seed_after(receipt):
 try:after=sha(hosted(receipt['path']))
 except OSError:after=None
 return {**receipt,'sha256_after':after,'after_unchanged':after==receipt['sha256']}

SCHEMA='''
CREATE TABLE IF NOT EXISTS units(committee_id TEXT NOT NULL,cycle INTEGER NOT NULL,created TEXT NOT NULL,PRIMARY KEY(committee_id,cycle));
CREATE TABLE IF NOT EXISTS origin_catalog(origin_ref INTEGER PRIMARY KEY,origin_id TEXT NOT NULL UNIQUE,base_metadata_json TEXT NOT NULL,first_reached_cycle INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS origin_seeds(committee_id TEXT NOT NULL,cycle INTEGER NOT NULL,origin_ref INTEGER NOT NULL,PRIMARY KEY(committee_id,cycle,origin_ref))WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS ledger_policy(name TEXT PRIMARY KEY,value TEXT NOT NULL)WITHOUT ROWID;
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
# The schema-2 layout (one materialized origins row per unit and origin) is kept ONLY to verify a migration source; nothing opens or writes it.
SCHEMA2=''.join(line+'\n' for line in SCHEMA.splitlines() if not line.startswith(('CREATE TABLE IF NOT EXISTS origin_','CREATE TABLE IF NOT EXISTS ledger_policy')))+'CREATE TABLE IF NOT EXISTS origins(committee_id TEXT NOT NULL,cycle INTEGER NOT NULL,origin_id TEXT NOT NULL,metadata_json TEXT NOT NULL,PRIMARY KEY(committee_id,cycle,origin_id));\n'

def seed_assignments(rows):
 """(committee_id, cycle, origin_id, base metadata, first_reached_cycle) for every seed row, exactly as seed() stores them; shared with the migration."""
 for row in rows:
  if type(row['cycle'])is not int or row['cycle']not in origin_closure.CYCLES:raise ValueError('Explicit C committee ID and exact integer cycle2022/2024/2026 required.')
  for origin in row.get('origins') or row.get('industries') or [row.get('origin_id') or 'explicit-seed']:
   first=row.get('first_reached_cycle',(row.get('origin_metadata')or{}).get('first_reached_cycle',row['cycle']))
   if type(first)is not int or first not in origin_closure.CYCLES or first>row['cycle']:raise ValueError('Origin first-reached cycle must be an exact permitted integer no later than activity cycle.')
   yield row['committee_id'],row['cycle'],str(origin),origin_closure.base_of(row.get('origin_metadata')or{'source':'explicit seed','scope':str(origin)}),first

# Additive extension (D072). Created lazily with IF NOT EXISTS inside a writer transaction, only when campaign priority or an
# extension contract is used, so an unextended ledger keeps its exact reviewed table set and read-only opens never write. user_version stays 3.
EXTENSION_TABLES=(
 'CREATE TABLE IF NOT EXISTS candidate_committee_map(candidate_id TEXT NOT NULL,cycle INTEGER NOT NULL,committee_id TEXT NOT NULL,designation TEXT NOT NULL,source TEXT NOT NULL,source_sha256 TEXT NOT NULL,vintage_or_retrieved_at TEXT,detail_json TEXT NOT NULL,recorded TEXT NOT NULL,PRIMARY KEY(candidate_id,cycle,committee_id))',
 'CREATE TABLE IF NOT EXISTS candidate_committee_lookups(candidate_id TEXT NOT NULL,cycle INTEGER NOT NULL,source TEXT NOT NULL,attempt INTEGER NOT NULL,status TEXT NOT NULL,run_id TEXT,http_attempts INTEGER NOT NULL,archive_refs_json TEXT NOT NULL,detail_json TEXT NOT NULL,recorded TEXT NOT NULL,PRIMARY KEY(candidate_id,cycle,source,attempt))',
 'CREATE TABLE IF NOT EXISTS campaign_priority(candidate_id TEXT NOT NULL,cycle INTEGER NOT NULL,priority_rank INTEGER NOT NULL,created TEXT NOT NULL,PRIMARY KEY(candidate_id,cycle))',
 'CREATE VIEW IF NOT EXISTS candidate_committee_lookups_current AS SELECT l.* FROM candidate_committee_lookups l WHERE l.status IN(\'found\',\'none_found\') AND l.attempt=(SELECT MAX(x.attempt) FROM candidate_committee_lookups x WHERE x.candidate_id=l.candidate_id AND x.cycle=l.cycle AND x.source=l.source AND x.status IN(\'found\',\'none_found\'))',
 'CREATE TABLE IF NOT EXISTS endpoint_retrievals(committee_id TEXT NOT NULL,cycle INTEGER NOT NULL,kind TEXT NOT NULL,page_no INTEGER NOT NULL,retrieved_at TEXT NOT NULL,archive_sha256 TEXT NOT NULL,PRIMARY KEY(committee_id,cycle,kind,page_no))')
EXTENSION_NAMES=('candidate_committee_map','candidate_committee_lookups','campaign_priority','endpoint_retrievals')
EXTENSION_CONTRACT_KINDS=('totals','candidate_committees')
def load_priority_candidates(path):
 raw=Path(path).read_bytes();document=json.loads(raw);entries=[];seen=set()
 if not isinstance(document,list)or not document:raise ValueError('Priority file must be a non-empty JSON list of {candidate_id,cycle}.')
 for item in document:
  if not isinstance(item,dict)or set(item)!={'candidate_id','cycle'}or not isinstance(item['candidate_id'],str)or not CANDIDATE.fullmatch(item['candidate_id'])or type(item['cycle'])is not int or item['cycle']not in(2022,2024,2026):
   raise ValueError('Each priority entry needs exactly an exact candidate_id and an integer cycle 2022/2024/2026.')
  key=(item['candidate_id'],item['cycle'])
  if key in seen:raise ValueError('Duplicate priority entry: '+item['candidate_id']+' '+str(item['cycle']))
  seen.add(key);entries.append(key)
 return entries,{'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'entries':len(entries)}
# D076: a bulk activity index (tools/build_bulk_activity_index.py) orders requests. Its table is separate from EXTENSION_NAMES on purpose: extensions_ready, every D072 path and every
# ledger that never saw an index stay exactly as they were. bulk_rows is NULL unless rows were observed (a stored zero would read as an observed absence); a tier is never an input of completion.
BULK_TIER_TABLE='CREATE TABLE IF NOT EXISTS endpoint_bulk_tier(committee_id TEXT NOT NULL,cycle INTEGER NOT NULL,kind TEXT NOT NULL,tier INTEGER NOT NULL CHECK(tier IN(1,2,3)),bulk_rows INTEGER,index_sha256 TEXT NOT NULL,source TEXT NOT NULL,recorded TEXT NOT NULL,PRIMARY KEY(committee_id,cycle,kind))'
BULK_KINDS=('B','E_regular','E_notice')
BULK_SOURCE='bulk_activity_index'
BULK_INDEX_KIND='mentat-bulk-activity-index'
HEX64=re.compile(r'[0-9a-f]{64}')
class BulkIndex:
 def __init__(self,rows,coverage,receipt):self.rows,self.coverage,self.receipt=rows,coverage,receipt
 def tier(self,committee,cycle,kind):
  """(tier, observed row count or None) for one endpoint. 1: the bulk files observed rows. 3: the index covers this kind/cycle completely and observed none. 2: anything else (unknown)."""
  seen=self.rows.get((committee,cycle,kind))
  if seen:return 1,seen
  return(3,None)if self.coverage.get((kind,cycle))else(2,None)
def _no_duplicate_keys(pairs):
 found={}
 for key,value in pairs:
  if key in found:raise ValueError('Duplicate key in the bulk activity index: '+key)
  found[key]=value
 return found
BULK_COUNT_LIMIT=2**62   # a row count must fit SQLite's integer with room to spare; a larger one is a defect, not data. Each per-source count is >=1 and they must sum to the row count, so they are below the limit too
def _short_text(value,limit=64):return value is None or(isinstance(value,str)and len(value)<=limit)
def load_bulk_activity_index(path,expect_sha256):
 """Strict reader of the JSON index. The file must hash to the pinned value before a byte is trusted; any defect refuses the whole index (nothing partial is ever used) with a ValueError, never a traceback."""
 try:return _read_bulk_activity_index(path,expect_sha256)
 except(TypeError,AttributeError,KeyError,IndexError,OverflowError,RecursionError,UnicodeError)as error:raise ValueError('The index is malformed ('+type(error).__name__+').')from None
def _read_bulk_activity_index(path,expect_sha256):
 raw=Path(path).read_bytes();fingerprint=hashlib.sha256(raw).hexdigest()   # the path is this process's own view (like --seeds); only the RECORDED spelling goes through stored()
 if not isinstance(expect_sha256,str)or not HEX64.fullmatch(expect_sha256):raise ValueError('--expect-bulk-index-sha256 must be 64 lowercase hex digits.')
 if fingerprint!=expect_sha256:raise ValueError('The index file hashes to '+fingerprint+', not the pinned '+expect_sha256+'.')
 try:document=json.loads(raw,object_pairs_hook=_no_duplicate_keys)
 except ValueError as error:raise ValueError('The index is not strict JSON: '+str(error))from None
 if not isinstance(document,dict)or document.get('schema_version')!=1 or document.get('kind')!=BULK_INDEX_KIND:raise ValueError('Not a schema-1 '+BULK_INDEX_KIND+' document.')
 provenance=document.get('provenance')
 if not isinstance(provenance,dict)or not isinstance(provenance.get('sources'),list)or not provenance['sources']:raise ValueError('The index carries no source provenance.')
 sources={}
 for item in provenance['sources']:
  if not(isinstance(item,dict)and isinstance(item.get('id'),str)and item['id']and HEX64.fullmatch(str(item.get('sha256')))and isinstance(item.get('vintage_asof'),str)and item['vintage_asof']and item.get('cycle')in origin_closure.CYCLES)or item['id']in sources:
   raise ValueError('A source entry of the index lacks an id, sha256, vintage or cycle, or repeats an id.')
  sources[item['id']]=item
 coverage={};block=document.get('coverage')
 for kind in BULK_KINDS:
  for cycle in origin_closure.CYCLES:
   entry=block.get(kind,{}).get(str(cycle))if isinstance(block,dict)and isinstance(block.get(kind),dict)else None
   if not isinstance(entry,dict)or type(entry.get('complete'))is not bool or not isinstance(entry.get('ingested'),list)or any(name not in sources for name in entry['ingested']):
    raise ValueError('The index coverage for '+kind+' '+str(cycle)+' is missing or malformed.')
   coverage[kind,cycle]=entry['complete']
 if not isinstance(document.get('rows'),list):raise ValueError('The index has no rows list.')
 rows={}
 for item in document['rows']:
  if not(isinstance(item,list)and len(item)==5):raise ValueError('An index row is not [committee, cycle, kind, count, per-source counts].')
  committee,cycle,kind,count,per=item
  if not(isinstance(committee,str)and COMMITTEE.fullmatch(committee)and type(cycle)is int and cycle in origin_closure.CYCLES and kind in BULK_KINDS and type(count)is int and 1<=count<BULK_COUNT_LIMIT and isinstance(per,dict)and per
         and all(name in sources and type(value)is int and 1<=value for name,value in per.items())and sum(per.values())==count):
   raise ValueError('An index row is malformed: '+json.dumps(item)[:200])
  if(committee,cycle,kind)in rows:raise ValueError('An index row repeats: '+json.dumps(item[:3]))
  rows[committee,cycle,kind]=count
 vintage=provenance.get('vintage')if isinstance(provenance.get('vintage'),dict)else{};builder=provenance.get('builder')if isinstance(provenance.get('builder'),dict)else{}
 if not all(_short_text(value)for value in(provenance.get('built_utc'),builder.get('sha256'),vintage.get('oldest_asof'),vintage.get('newest_asof'))):raise ValueError('The index provenance text (build time, builder hash, vintage) is not short text.')
 if not all(_short_text(source.get('kind'),32)and len(source['id'])<=64 and len(source['vintage_asof'])<=64 for source in provenance['sources']):raise ValueError('A source entry of the index carries text that is not short text.')
 receipt={'path':stored(path),'sha256':fingerprint,'bytes':len(raw),'schema_version':1,'built_utc':provenance.get('built_utc'),'builder_sha256':builder.get('sha256'),
  'vintage':{'oldest_asof':vintage.get('oldest_asof'),'newest_asof':vintage.get('newest_asof')},'rows':len(rows),
  'sources':[{key:source.get(key)for key in('id','kind','cycle','sha256','vintage_asof')}for source in provenance['sources']],
  'coverage_complete':{kind:{str(cycle):coverage[kind,cycle]for cycle in origin_closure.CYCLES}for kind in BULK_KINDS}}
 return BulkIndex(rows,coverage,receipt)
def ccl_vintage(path):
 receipt=Path(path).with_name('source-receipt.json')
 try:
  value=json.loads(receipt.read_text(encoding='utf-8')).get('last_modified')
  if isinstance(value,str)and value:return value
 except(OSError,ValueError,AttributeError):pass
 return 'file_mtime_utc:'+datetime.fromtimestamp(Path(path).stat().st_mtime,timezone.utc).isoformat()
def principal_committees_from_ccl(path,cycle,wanted):
 # ccl columns: CAND_ID|CAND_ELECTION_YR|FEC_ELECTION_YR|CMTE_ID|CMTE_TP|CMTE_DSGN|LINKAGE_ID. Cycle = FEC_ELECTION_YR; P = principal campaign committee.
 raw=Path(path).read_bytes();found={}
 for line in raw.decode('utf-8','replace').splitlines():
  part=line.strip().split('|')
  if len(part)>=7 and part[0]in wanted and part[2]==str(cycle)and part[5]=='P'and COMMITTEE.fullmatch(part[3]):
   found.setdefault(part[0],{})[part[3]]={'cand_election_yr':part[1],'cmte_tp':part[4],'linkage_id':part[6]}
 return found,hashlib.sha256(raw).hexdigest(),len(raw)

class Frontier:
 def __init__(self,path,*,optional_receipts=False,include_later_cycle_context=False,enabled_endpoint_contracts=(),rebind_endpoint_contracts=False):
  requested=activation_plan([],enabled_endpoint_contracts)["enabled_contract_ids"]
  self.endpoint_contracts={kind:value for kind,value in CONTRACTS.items()if value["contract_id"]in requested and value.get("scope","unit")=="unit"}
  self.lookup_contracts={kind:value for kind,value in CONTRACTS.items()if value["contract_id"]in requested and value.get("scope","unit")=="candidate"}
  self.supported={**SUPPORTED,**{kind:(value["endpoint"],value["amount_field"])for kind,value in self.endpoint_contracts.items()}}
  self.counterparty_fields={**COUNTERPARTY_C_FIELDS,**{kind:value["paid_recipient_committee_fields"]for kind,value in self.endpoint_contracts.items()}}
  self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
  self.db=sqlite3.connect(self.path,timeout=1,isolation_level=None);self.db.row_factory=sqlite3.Row
  version=self.db.execute('PRAGMA user_version').fetchone()[0]
  existing={r[0]for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  if existing and version==2:self.db.close();raise ValueError('Frontier ledger is schema 2 (materialized per-unit origins). Schema 3 is never mixed with it and an existing ledger is never rewritten in place; write a new ledger with: '+MIGRATION_COMMAND)
  if existing and version!=SCHEMA_VERSION:self.db.close();raise ValueError('Unknown frontier schema/version; no automatic migration or provider admission.')
  if existing:
   model=sqlite3.connect(':memory:');model.executescript(SCHEMA);expected={r[0]for r in model.execute("SELECT name FROM sqlite_master WHERE type='table'")}
   for statement in EXTENSION_TABLES:model.execute(statement)
   model.execute(BULK_TIER_TABLE)   # D076: optional like the extension tables; an existing ledger may hold it only with exactly this layout
   allowed={r[0]for r in model.execute("SELECT name FROM sqlite_master WHERE type='table'")}
   # The reviewed tables must all be present; the additive extension tables may be present (created) or absent (not yet needed).
   valid=expected<=existing<=allowed and all([tuple(r)for r in self.db.execute('PRAGMA table_info('+name+')')]==[tuple(r)for r in model.execute('PRAGMA table_info('+name+')')]for name in existing);model.close()
   if not valid:self.db.close();raise ValueError('Frontier schema differs from reviewed contract; no automatic migration.')
  self.db.execute('PRAGMA journal_mode=DELETE');self.db.execute('PRAGMA synchronous=FULL')
  if not existing:self.db.executescript(SCHEMA);self.db.execute("INSERT INTO ledger_policy VALUES('later_cycle_context',?)",('true'if include_later_cycle_context else'false',));self.db.execute('PRAGMA user_version='+str(SCHEMA_VERSION))
  try:recorded=origin_closure.read_policy(self.db)
  except(ValueError,sqlite3.Error)as error:self.db.close();raise ValueError('Frontier ledger lacks its recorded later-cycle context policy: '+str(error))
  if recorded!=bool(include_later_cycle_context):self.db.close();raise ValueError('Ledger policy later_cycle_context='+str(recorded).lower()+' differs from this invocation; the closure is defined by one recorded policy, never mixed.')
  self.optional_receipts=optional_receipts
  self.bulk_index=None  # D076: set by set_bulk_index(); None means no endpoint is tiered by this process
  self.run_id=None      # set by main(); recorded on every candidate-committee lookup attempt row
  self.include_later_cycle_context=include_later_cycle_context
  try:
   # Without enablement nothing is written, so a read-only open never contends for the writer lease.
   with(self.transaction()if requested else nullcontext()):
    active={row[0]for row in self.db.execute("SELECT DISTINCT kind FROM endpoints WHERE status!='unsupported'")}
    if active-set(self.supported):raise EndpointContractRefused("Active endpoint state lacks its explicit pinned contract; no fallback.")
    self.contract_enablement=bind_enablement(self.db,self.path,requested,rebind=bool(rebind_endpoint_contracts))
    if any(value["contract_id"]in requested for kind,value in CONTRACTS.items()if kind in EXTENSION_CONTRACT_KINDS):self._ensure_extensions()
  except BaseException:self.db.close();raise
 @contextmanager
 def transaction(self):
  # One short exclusive-writer transaction per committed page or failure; fetches run outside it, and main()'s run lease excludes other collectors.
  # A crash releases SQLite ownership; unfinished page data/checkpoint rolls back.
  self.db.execute('BEGIN IMMEDIATE')
  try:yield
  except BaseException:self.db.execute('ROLLBACK');raise
  else:self.db.execute('COMMIT')
 @property
 def extensions_ready(self):
  return self.db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN("+",".join("?"for _ in EXTENSION_NAMES)+")",EXTENSION_NAMES).fetchone()[0]==len(EXTENSION_NAMES)
 def _ensure_extensions(self):
  # Additive and idempotent: IF NOT EXISTS only, no row of the reviewed tables is read or written. Call inside a writer transaction.
  for statement in EXTENSION_TABLES:self.db.execute(statement)
 def _unit(self,committee,cycle):
  if not isinstance(committee,str)or not COMMITTEE.fullmatch(committee)or type(cycle)is not int or cycle not in(2022,2024,2026):raise ValueError('Explicit C committee ID and exact integer cycle2022/2024/2026 required.')
  self.db.execute('INSERT OR IGNORE INTO units VALUES(?,?,?)',(committee,cycle,now()))
  for kind in KINDS+(('A',) if self.optional_receipts else ())+(('totals',) if 'totals' in self.supported else ()):
   state='pending' if kind in self.supported else 'unsupported'
   why=None if state=='pending' else 'Required disclosure family '+kind+' has no reviewed driver coverage contract yet; exhaustive coverage remains false.'
   self.db.execute('INSERT OR IGNORE INTO endpoints(committee_id,cycle,kind,status,last_error) VALUES(?,?,?,?,?)',(committee,cycle,kind,state,why))
  if self.bulk_index is not None:self._tier_endpoints(committee,cycle)
 def _tier_endpoints(self,committee,cycle):
  # D076, inside a writer transaction and only while an index is loaded. Records the request-order tier of this unit's B / E_regular / E_notice endpoints; a row is only ever
  # (re)written when it came from another index. The tier changes no status, count, cursor or receipt figure: only _next_rows reads it.
  recorded=now()
  for kind in BULK_KINDS:
   if kind not in self.supported:continue
   tier,seen=self.bulk_index.tier(committee,cycle,kind)
   self.db.execute('INSERT INTO endpoint_bulk_tier VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(committee_id,cycle,kind) DO UPDATE SET tier=excluded.tier,bulk_rows=excluded.bulk_rows,index_sha256=excluded.index_sha256,source=excluded.source,recorded=excluded.recorded WHERE endpoint_bulk_tier.index_sha256!=excluded.index_sha256',
    (committee,cycle,kind,tier,seen,self.bulk_index.receipt['sha256'],BULK_SOURCE,recorded))
 def set_bulk_index(self,index):
  # Serial, before dispatch (like set_priority): tier every existing B / E endpoint; units created later are tiered in _unit(). Creates no unit, endpoint, edge or payment.
  try:
   with self.transaction():
    self.db.execute(BULK_TIER_TABLE);self.bulk_index=index
    for committee,cycle in self.db.execute("SELECT DISTINCT committee_id,cycle FROM endpoints WHERE kind IN('B','E_regular','E_notice')ORDER BY committee_id,cycle").fetchall():self._tier_endpoints(committee,cycle)
  except BaseException:self.bulk_index=None;raise
  return self.bulk_tier_summary()
 def _tiers_present(self):
  return self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='endpoint_bulk_tier'").fetchone()is not None
 def bulk_tier_summary(self):
  if not self._tiers_present():return None
  by={}
  for kind,tier,count in self.db.execute('SELECT kind,tier,COUNT(*) FROM endpoint_bulk_tier GROUP BY kind,tier'):by.setdefault(kind,{})[str(tier)]=count
  total={str(tier):sum(item.get(str(tier),0)for item in by.values())for tier in(1,2,3)}
  return {'source':BULK_SOURCE,'tiered_endpoints':sum(total.values()),'by_tier':total,'by_kind_tier':{kind:by[kind]for kind in sorted(by)},'index_sha256s':sorted({r[0]for r in self.db.execute('SELECT DISTINCT index_sha256 FROM endpoint_bulk_tier')}),
   'order':'D072 campaign priority, then tier 1 / 2 / 3 (an endpoint with 3 or more attempts beyond its stored pages sorts after tier 3, still pending), then the existing fair turns','tier_is_an_ordering_hint_only':True}
 def _forward(self,committee,cycle):
  # With the ledger's later-cycle policy on, every later cycle of an admitted unit is itself a unit; origins reach it by derivation, never by stored copies.
  if self.include_later_cycle_context:
   for later in origin_closure.CYCLES:
    if later>cycle:self._unit(committee,later)
 def _catalog(self,origin,base,first):
  stored=self.db.execute('SELECT origin_ref,base_metadata_json,first_reached_cycle FROM origin_catalog WHERE origin_id=?',(origin,)).fetchone()
  if stored is None:return self.db.execute('INSERT INTO origin_catalog(origin_id,base_metadata_json,first_reached_cycle) VALUES(?,?,?)',(origin,encoded(base),first)).lastrowid
  if stored[1]!=encoded(base):raise ValueError('Origin base metadata conflicts with its catalog entry; a catalog base is written once and frozen.')
  if first<stored[2]:self.db.execute('UPDATE origin_catalog SET first_reached_cycle=? WHERE origin_ref=?',(first,stored[0]))   # monotone: it may only fall
  return stored[0]
 def seed(self,rows):
  with self.transaction():
   for committee,cycle,origin,base,first in seed_assignments(rows):
    self._unit(committee,cycle);self._forward(committee,cycle)
    self.db.execute('INSERT OR IGNORE INTO origin_seeds VALUES(?,?,?)',(committee,cycle,self._catalog(origin,base,first)))
 def closure(self):return origin_closure.from_db(self.db,self.include_later_cycle_context)
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
   self._unit(destination,unit[1]);self.db.execute('INSERT INTO edges VALUES(?,?,?,?,?,?,?,?,?,?,?)',(*unit,kind,occurrence,sid,destination,unit[1],amount,rowhash,identity_status,encoded(identity)));self._forward(destination,unit[1])
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
 def _prepare(self,row):
  # Writer-side pre-admission checks; a refusal here is recorded with zero attempts, as before.
  kind=row['kind']
  if kind not in self.supported:raise EndpointContractRefused("Pending endpoint kind has no enabled pinned contract in this invocation; no fallback.")
  endpoint,field=self.supported[kind]
  endpoint=endpoint.replace('{committee_id}',row['committee_id'])   # totals: the committee is a path component, never a query selector
  verify_enablement(self.contract_enablement)
  return _Job(row,endpoint,field,self._query(row))
 def _apply(self,row,endpoint,field,query,data,evidence,attempts):
  unit=(row['committee_id'],row['cycle']);kind=row['kind']
  if evidence.get('status')!=200 or evidence.get('original_response_bytes')is not True or evidence.get('query')!=query or evidence.get('endpoint')!=endpoint:
   raise ArchiveBindingFailed('Successful original response evidence does not bind the exact request.')
  archive=_verify_archive(evidence,'Original response archive does not verify; no page admitted.')
  if not isinstance(data,dict)or not isinstance(data.get('results'),list):
   raise ValueError('Archived response has no result array; no page shape inferred.')
  pagination=data.get('pagination')or{}
  if not isinstance(pagination,dict):pagination={}
  count=pagination.get('count');batch=data['results']
  issues=set(json.loads(row['coverage_issues_json']))
  count_valid=type(count)is int and count>=0
  exact=count_valid and pagination.get('is_count_exact')is True
  if not exact and kind not in NON_PAYMENT_KINDS:issues.add('count_exactness_unknown')
  if row['expected_count']is not None and count_valid and count!=row['expected_count']:
   issues.add('advertised_count_changed')
  expected=row['expected_count']if row['expected_count']is not None else(count if count_valid else None)
  page_no=row['pages_stored']+1
  for ordinal,observation in enumerate(batch):
   occurrence=evidence['sha256']+':'+str(page_no)+':'+str(ordinal)
   rowhash=digest(observation);raw=encoded(observation)
   sid=None;amount_text=None;amount_status='row_not_object'
   if isinstance(observation,dict)and kind in NON_PAYMENT_KINDS:amount_status='not_a_payment_totals_row'
   elif isinstance(observation,dict):
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
  if kind in NON_PAYMENT_KINDS:
   # Retrieved-and-parsed certifies this family; a following page is requested only when the provider reports one.
   progress=inspect_contract_page(kind,query,data,rows_before=row["rows_stored"])
   issues.update(progress["issues"]);cursor=progress["next_cursor"]
   if cursor and self.db.execute("SELECT 1 FROM pages WHERE committee_id=? AND cycle=? AND kind=? AND request_query_json=?",(*unit,kind,encoded(contract_query(kind,unit[0],unit[1],cursor=cursor)))).fetchone():cursor={};issues.add("cursor_recovery_required")
   count_closed=not cursor and not issues
   closed=not cursor
  elif kind in self.endpoint_contracts:
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
  if self.extensions_ready:self.db.execute('INSERT OR REPLACE INTO endpoint_retrievals VALUES(?,?,?,?,?,?)',(*unit,kind,page_no,evidence.get('captured_utc')or now(),evidence['sha256']))
  self.db.execute('UPDATE endpoints SET status=?,request_cursor_json=?,expected_count=?,rows_stored=?,pages_stored=pages_stored+1,last_error=NULL,coverage_issues_json=?,attempt_count=attempt_count+?,last_attempt_at=?,retry_after=NULL WHERE committee_id=? AND cycle=? AND kind=?',(status,encoded(cursor),expected,total,encoded(sorted(issues)),attempts,time.time(),*unit,kind))
 def _commit(self,job,outcome):
  # Writer only: one short exclusive transaction per page (or per failure). Returns the PageFailure it recorded, if any.
  if outcome.skipped:return None
  try:
   if outcome.error is not None:raise outcome.error
   with self.transaction():
    current=self.db.execute('SELECT * FROM endpoints WHERE committee_id=? AND cycle=? AND kind=?',job.key).fetchone()
    if current is None or tuple(current)!=tuple(job.row):raise sqlite3.IntegrityError('In-flight endpoint changed under the single writer; refusing.')
    self._apply(job.row,job.endpoint,job.field,job.query,outcome.data,outcome.evidence,outcome.attempts)
   return None
  except Exception as error:
   failed=PageFailure(job.row,error,job.query,outcome.evidence,outcome.attempts)
   with self.transaction():self._failure(failed.row,failed.error,failed.query,failed.evidence,_status(failed.error),failed.attempts)
   return failed
 def _next_rows(self,limit,exclude):
  if limit<=0:return[]
  # One page per endpoint per round: outgoing endpoints receive fair turns; an endpoint in flight is simply taken.
  tiered=self._tiers_present()and self.db.execute('SELECT 1 FROM endpoint_bulk_tier LIMIT 1').fetchone()is not None   # D076: persisted tiers order the queue right after campaign priority
  if self.extensions_ready and self.db.execute('SELECT 1 FROM campaign_priority LIMIT 1').fetchone():
   # Priority committees' B (and totals) endpoints are served first, still one page per endpoint per round (fair turns inside the
   # priority group; ties by list order). Everything else keeps the original fair-turn order after them. Priority lives in the ledger.
   sql=("SELECT e.* FROM endpoints e LEFT JOIN (SELECT m.committee_id AS pc,m.cycle AS py,MIN(p.priority_rank) AS pr FROM campaign_priority p JOIN candidate_committee_map m ON m.candidate_id=p.candidate_id AND m.cycle=p.cycle GROUP BY m.committee_id,m.cycle) q ON q.pc=e.committee_id AND q.py=e.cycle AND e.kind IN('B','totals') WHERE e.status='pending' AND (? OR e.kind!='A') AND(e.kind!='A' OR NOT EXISTS(SELECT 1 FROM endpoints x WHERE x.kind IN('B','E_regular','E_notice')AND x.status!='complete')) ORDER BY CASE e.kind WHEN 'A' THEN 1 ELSE 0 END,CASE WHEN q.pr IS NULL THEN 1 ELSE 0 END,e.attempt_count,e.pages_stored,COALESCE(q.pr,0),e.cycle,e.committee_id,e.kind LIMIT ?")
   if tiered:sql=sql.replace(" WHERE e.status='pending'",TIER_JOIN+" WHERE e.status='pending'",1).replace("CASE WHEN q.pr IS NULL THEN 1 ELSE 0 END,","CASE WHEN q.pr IS NULL THEN 1 ELSE 0 END,"+TIER_KEY+",",1)
  else:
   sql=("SELECT * FROM endpoints WHERE status='pending' AND (? OR kind!='A') AND(kind!='A' OR NOT EXISTS(SELECT 1 FROM endpoints WHERE kind IN('B','E_regular','E_notice')AND status!='complete')) ORDER BY CASE kind WHEN 'A' THEN 1 ELSE 0 END,attempt_count,pages_stored,cycle,committee_id,kind LIMIT ?")
   if tiered:sql="SELECT e.* FROM endpoints e"+TIER_JOIN+" WHERE e.status='pending' AND (? OR e.kind!='A') AND(e.kind!='A' OR NOT EXISTS(SELECT 1 FROM endpoints x WHERE x.kind IN('B','E_regular','E_notice')AND x.status!='complete')) ORDER BY CASE e.kind WHEN 'A' THEN 1 ELSE 0 END,"+TIER_KEY+",e.attempt_count,e.pages_stored,e.cycle,e.committee_id,e.kind LIMIT ?"
  rows=self.db.execute(sql,(self.optional_receipts,limit+len(exclude))).fetchall()
  return[r for r in rows if(r['committee_id'],r['cycle'],r['kind'])not in exclude][:limit]
 def _dispatch_limit(self,transport,workers,inflight):
  free=workers-len(inflight)
  # Refill in batches of workers//8 (1 up to 15 workers): one scheduler query then serves several dispatches, and in-flight dips at most that far.
  if free<=0 or(inflight and free<workers//8):return 0
  maximum=getattr(transport,'maximum',None)
  if type(maximum)is not int:return free
  left=maximum-transport.http_attempts-len(inflight)   # conservative: an in-flight job may already have started
  if left>0:return min(free,left)
  return 0 if inflight else 1   # one job reaches the cap refusal: the same single stop record as serial
 def run(self,transport,*,max_failed_attempts=8,workers=1,progress=None,progress_seconds=30,initial_stop=None,stop_after_seconds=None):
  if type(max_failed_attempts)is not int or max_failed_attempts<1:raise ValueError('Positive failed-attempt allowance required.')
  if stop_after_seconds is not None and not(isinstance(stop_after_seconds,(int,float))and not isinstance(stop_after_seconds,bool)and stop_after_seconds>0):raise ValueError('The stop-after allowance must be a positive number of seconds.')
  if type(workers)is not int or not 1<=workers<=MAX_WORKERS:raise ValueError('Worker count must be an integer from 1 to '+str(MAX_WORKERS)+'.')
  per_call=getattr(transport,'supports_concurrent_fetch',False)is True
  if workers>1 and not per_call:raise ValueError('Concurrent workers need a transport that reports per-call attempts and evidence.')
  # initial_stop: a global stop already recorded before dispatch (a refused candidate-committee lookup); nothing is dispatched, the receipt still carries every run field.
  if initial_stop is None:self.resume()
  stopped=initial_stop;completed=failures=attempts=seq=peak=skipped=limited=idle=0;strikes={};benched=set()
  halt=threading.Event();inflight={}
  physical=lambda:getattr(transport,'http_attempts',attempts);rates=_Rates(physical());shown=[time.monotonic()]
  deadline=None if stop_after_seconds is None else time.monotonic()+stop_after_seconds
  def tick():
   if halt.is_set():getattr(getattr(transport,'pool',None),'stop',lambda:None)()   # M1: waiting acquirers are released and refuse (recorded with 0 attempts, left pending); idempotent
   rates.note(physical())
   if progress and time.monotonic()-shown[0]>=progress_seconds:
    shown[0]=time.monotonic()
    progress({'event':'progress','elapsed_s':round(shown[0]-rates.start,1),'physical_attempts':physical()-rates.base,'pages_committed':completed,'failures':failures,'rate_limited_responses':limited,'in_flight':len(inflight),
     'requests_per_hour_60s':round(rates.over(60)),'requests_per_hour_10min':round(rates.over(600)),'transport':getattr(transport,'telemetry',lambda:None)()})
  pool=ThreadPoolExecutor(workers,thread_name_prefix='frontier-fetch')if workers>1 else None
  try:
   while True:
    tick()
    # D075: past the allowance nothing new is dispatched; what is in flight drains and commits, and the receipt says DeadlineReached (never a kill that would leave a crash to recover).
    if deadline is not None and stopped is None and time.monotonic()>=deadline:stopped={'error_type':'DeadlineReached','detail':'The --stop-after-seconds allowance ended; dispatch stopped and in-flight work drained.','global_admission_archive_auth_quota_or_failure_allowance':False}
    rows=self._next_rows(self._dispatch_limit(transport,workers,inflight),set(inflight)|benched)if stopped is None and not halt.is_set()else[]
    for row in rows:
     if stopped is not None:break
     seq+=1
     try:job=self._prepare(row)
     except Exception as error:
      with self.transaction():self._failure(row,error,{},None,_status(error),0)
      failures+=1;stopped=_stop_for(error,failures,max_failed_attempts,halt);continue
     job.seq=seq;inflight[job.key]=(seq,job,pool.submit(_fetch,transport,job,halt,per_call)if pool else _fetch(transport,job,halt,per_call))
    peak=max(peak,len(inflight))
    if not inflight:
     if rows:continue   # every row of this round was refused before admission: select again
     break   # nothing pending and nothing in flight, or stopped and drained
    quiet=True
    for key in sorted(_ready(inflight,pool,tick),key=lambda item:inflight[item][0]):   # dispatch order: deterministic stop pick
     _,job,value=inflight.pop(key);outcome=value.result()if pool else value
     skipped+=outcome.skipped;attempts+=outcome.attempts;quiet=quiet and outcome.skipped
     failed=self._commit(job,outcome)
     if failed is None:completed+=not outcome.skipped;continue
     if _pool_stopped(failed.error):continue   # M1: released on purpose after a global stop: recorded (0 attempts, pending), never the cause and not counted
     if _key_scoped(failed.error):   # recorded with its evidence; that key is cooling, the run goes on and the allowance is not spent
      limited+=1;strikes[job.key]=strikes.get(job.key,0)+1
      if strikes[job.key]==2:   # L1: a second 429 for one endpoint: no more keys are cooled for it this run; it stays pending in the ledger with a note
       benched.add(job.key)
       with self.transaction():self.db.execute('UPDATE endpoints SET last_error=? WHERE committee_id=? AND cycle=? AND kind=?',('HTTP 429 twice in one run; skipped for the rest of that run and left pending: '+str(failed.error),*job.key))
      continue
     failures+=1
     if stopped is None:stopped=_stop_for(failed.error,failures,max_failed_attempts,halt)
    idle=idle+1 if quiet and stopped is None else 0
    if idle>=3:raise RuntimeError('Fetches were halted without a recorded stop; refusing to loop.')   # fail closed: unreachable unless halt and stop disagree
  finally:
   # A BaseException leaves workers to finish archiving but commits nothing more; main() leaves the START record unresolved.
   if pool:pool.shutdown(wait=True,cancel_futures=True)
  tick();report=self.report(completed,stopped)
  report.update(workers=workers,peak_in_flight=peak,halted_unadmitted_jobs=skipped,attempts_recorded=attempts,rate_limited_responses=limited,rate_limited_endpoints_skipped=sorted('/'.join(map(str,key))for key in benched)[:100],run_seconds=round(time.monotonic()-rates.start,3),
   achieved_requests_per_hour=round(rates.overall(),1),rolling_60s_requests_per_hour_peak=round(rates.peak60,1),rolling_10min_requests_per_hour_last=round(rates.over(600),1))
  return report

 def _bound_pair(self,body,meta):return _bound_pair(body,meta)
 def _orphan_row(self,record):
  query=record['query'];cycle=query.get('two_year_transaction_period',query.get('cycle'));found=[]
  committee=query.get('committee_id')
  if committee is None:
   path=re.fullmatch(r'committee/(C[0-9]{8})/totals/',record['endpoint']);committee=path.group(1)if path else None
  for kind,(endpoint,_) in self.supported.items():
   if endpoint.replace('{committee_id}',str(committee))!=record['endpoint']:continue
   row=self.db.execute("SELECT * FROM endpoints WHERE committee_id=? AND cycle=? AND kind=? AND status='pending'",(committee,cycle,kind)).fetchone()
   if row is None:continue
   try:match=self._query(row)==query
   except Exception:match=False
   if match:found.append(row)
  return found[0]if len(found)==1 else None
 def recover(self,folder,quarantine,readonly=False):
  """Reconcile one crashed run's archive folder with the ledger: replay complete unreferenced pairs through the live commit, quarantine the rest.
  Classification and every refusal happen before any file move or ledger write; each step is idempotent so an interrupted recovery is simply rerun."""
  folder=Path(folder);names=sorted(p.name for p in folder.iterdir())if folder.is_dir()else[]
  held=set();metadata_only=set()
  for(path,)in self.db.execute('SELECT archive_path FROM pages'):held.add(_key(path))
  if self.extensions_ready:
   for(refs_text,text)in self.db.execute('SELECT archive_refs_json,detail_json FROM candidate_committee_lookups'):
    for part in(json.loads(refs_text),json.loads(text)):held.update(_key(item)for item in _walk_strings(part)if item.endswith('.body.bin'))
    for ref in json.loads(refs_text):
     if ref.get('refused_secret_echo')is True and type(ref.get('attempt'))is int and isinstance(ref.get('sha256'),str):metadata_only.add('attempt-%06d-%s'%(ref['attempt'],ref['sha256'][:12]))
  for(text,)in self.db.execute('SELECT archive_record_json FROM failures WHERE archive_record_json IS NOT NULL'):
   record=json.loads(text);held.update(_key(item)for item in _walk_strings(record)if item.endswith('.body.bin'))
   if isinstance(record,dict)and record.get('refused_secret_echo')is True and type(record.get('attempt'))is int and isinstance(record.get('sha256'),str):metadata_only.add('attempt-%06d-%s'%(record['attempt'],record['sha256'][:12]))
  stems={}
  for name in names:
   if name.endswith('.body.bin'):stems.setdefault(name[:-9],{})['body']=name
   elif name.endswith('.json'):stems.setdefault(name[:-5],{})['meta']=name
   else:stems.setdefault(name,{})['other']=name
  move=[];orphans=[];referenced=0
  for stem,part in sorted(stems.items()):
   body,meta,other=part.get('body'),part.get('meta'),part.get('other')
   if body and not meta and _key(folder/body)in held:raise RecoveryRefused('Ledger references '+body+' but its metadata is missing; a human must decide. Nothing was changed.')
   if body and meta:
    if _key(folder/body)in held:referenced+=1;continue
    bound=self._bound_pair(folder/body,folder/meta)
    if bound is None:move+=[(body,'archive pair does not bind'),(meta,'archive pair does not bind')]
    elif LOOKUP_ENDPOINT.fullmatch(bound[0]['endpoint']):move+=[(body,'uncommitted candidate-committee lookup page; the lookup is repeated'),(meta,'uncommitted candidate-committee lookup page; the lookup is repeated')]
    else:orphans.append((bound[0],bound[1],folder/meta))
   elif meta:
    if stem in metadata_only:referenced+=1
    else:move.append((meta,'metadata without a body'))
   elif body:move.append((body,'body without metadata'))
   else:move.append((other,'unexpected file'))
  targets={}
  for record,_,meta in orphans:
   row=self._orphan_row(record)
   if row is None:raise RecoveryRefused('Archive pair '+meta.name+' cannot be matched to exactly one pending endpoint query; a human must decide. Nothing was changed.')
   key=(row['committee_id'],row['cycle'],row['kind'])
   if key in targets:raise RecoveryRefused('Two archive pairs map to endpoint '+'/'.join(map(str,key))+'; a human must decide. Nothing was changed.')
   targets[key]=meta
  if move and readonly:raise RecoveryRefused('The archive root is read-only here, so '+', '.join(name for name,_ in move)+' cannot be moved aside; run api_host_pool.py quarantine on the host for this run first (an uncommitted candidate-committee lookup page needs a human). Nothing was changed.')
  quarantined=_quarantine_files(folder,quarantine,move)
  replayed=0
  for record,raw,meta in sorted(orphans,key=lambda item:item[0]['attempt']):
   key=next(k for k,v in targets.items()if v==meta)
   job=self._prepare(self.db.execute('SELECT * FROM endpoints WHERE committee_id=? AND cycle=? AND kind=?',key).fetchone())
   evidence={**record,'receipt_path':record['path'][:-len('.body.bin')]+'.json'}
   if record['status']!=200:outcome=_Outcome(evidence=evidence,attempts=1,error=HTTPError(record['endpoint'],record['status'],'FEC evidence response was not successful.',record.get('headers')or{},None))
   else:
    try:outcome=_Outcome(json.loads(raw,parse_float=Decimal),evidence,1)
    except ValueError as error:outcome=_Outcome(evidence=evidence,attempts=1,error=error)
   self._commit(job,outcome);replayed+=1
  return{'quarantined':quarantined,'replayed_pairs':replayed,'referenced_pairs':referenced+replayed}

 def _record_lookup(self,candidate,cycle,source,status,detail,*,attempts=0,refs=()):
  # Append-only (owner ruling): every lookup attempt is its own row, numbered per (candidate, cycle, source), carrying its run, the physical attempts it spent and
  # every archive pair it produced. A row is never replaced or deleted, so no archived lookup pair can lose its reference. A repeated IDENTICAL local read adds nothing.
  latest=self.db.execute('SELECT status,detail_json FROM candidate_committee_lookups WHERE candidate_id=? AND cycle=? AND source=? ORDER BY attempt DESC LIMIT 1',(candidate,cycle,source)).fetchone()
  if source=='local_ccl'and latest is not None and tuple(latest)==(status,detail):return
  number=self.db.execute('SELECT COALESCE(MAX(attempt),0)+1 FROM candidate_committee_lookups WHERE candidate_id=? AND cycle=? AND source=?',(candidate,cycle,source)).fetchone()[0]
  self.db.execute('INSERT INTO candidate_committee_lookups VALUES(?,?,?,?,?,?,?,?,?,?)',(candidate,cycle,source,number,status,self.run_id,attempts,encoded(list(refs)),detail,now()))
 def set_priority(self,entries,*,ccl=None):
  # entries: ordered [(candidate_id,cycle)]; ccl: {cycle:{'path','vintage'}}. Upserts priority (list order = rank), maps candidates to their
  # principal (designation P) committees from the local linkage file when no mapping is stored, and seeds any absent committee/cycle unit.
  with self.transaction():
   self._ensure_extensions()
   for rank,(candidate,cycle)in enumerate(entries):
    self.db.execute('INSERT INTO campaign_priority VALUES(?,?,?,?) ON CONFLICT(candidate_id,cycle) DO UPDATE SET priority_rank=excluded.priority_rank',(candidate,cycle,rank,now()))
   for cycle,spec in sorted((ccl or{}).items()):
    wanted={c for c,y in entries if y==cycle and not self.db.execute('SELECT 1 FROM candidate_committee_map WHERE candidate_id=? AND cycle=?',(c,cycle)).fetchone()}
    if not wanted:continue
    found,fingerprint,size=principal_committees_from_ccl(spec['path'],cycle,wanted)
    for candidate in sorted(wanted):
     for committee,detail in sorted(found.get(candidate,{}).items()):
      self.db.execute('INSERT OR IGNORE INTO candidate_committee_map VALUES(?,?,?,?,?,?,?,?,?)',(candidate,cycle,committee,'P','local_ccl',fingerprint,spec['vintage'],encoded({'file':Path(spec['path']).name,'bytes':size,**detail}),now()))
     self._record_lookup(candidate,cycle,'local_ccl','found'if found.get(candidate)else'none_found',encoded({'sha256':fingerprint,'vintage':spec['vintage']}))
   seeded=self._seed_priority_units()
  summary=self.priority_summary();summary['units_seeded_now']=seeded;return summary
 def _seed_priority_units(self):
  seeded=0
  for candidate,cycle,committee in self.db.execute('SELECT DISTINCT m.candidate_id,m.cycle,m.committee_id FROM campaign_priority p JOIN candidate_committee_map m ON m.candidate_id=p.candidate_id AND m.cycle=p.cycle ORDER BY p.priority_rank,m.committee_id').fetchall():
   if self.db.execute('SELECT 1 FROM units WHERE committee_id=? AND cycle=?',(committee,cycle)).fetchone():continue
   # Discovery only: not giving or payment evidence, never coverage. Consumers must exclude this origin from every giving view (committee_activity_reader docstring).
   base=origin_closure.base_of({'source':'campaign_priority','discovery_source':'campaign_priority','giving_or_payment_evidence':False,'hint_not_coverage':True,'candidate_id':candidate})
   self._unit(committee,cycle);self._forward(committee,cycle)
   self.db.execute('INSERT OR IGNORE INTO origin_seeds VALUES(?,?,?)',(committee,cycle,self._catalog('campaign-priority-'+candidate+'-'+str(cycle),base,cycle)));seeded+=1
  return seeded
 def fill_priority_via_api(self,transport):
  # Serial, before dispatch. Each lookup page is a physical attempt of THIS invocation that no endpoints.attempt_count row carries, so it is recorded on its own
  # candidate_committee_lookups row (http_attempts) and reported separately; main()'s receipt guard subtracts it. Fetches run outside any ledger transaction.
  contract=self.lookup_contracts.get('candidate_committees')
  outcome={'lookups':0,'found':0,'none_found':0,'failed':0,'http_attempts':0,'stopped':None,'units_seeded_now':0}
  if contract is None:return{**outcome,'skipped':'candidate_committees contract not enabled'}
  verify_enablement(self.contract_enablement)
  todo=self.db.execute("SELECT p.candidate_id,p.cycle FROM campaign_priority p WHERE NOT EXISTS(SELECT 1 FROM candidate_committee_map m WHERE m.candidate_id=p.candidate_id AND m.cycle=p.cycle) AND NOT EXISTS(SELECT 1 FROM candidate_committee_lookups l WHERE l.candidate_id=p.candidate_id AND l.cycle=p.cycle AND l.source='api_candidate_committees' AND l.status IN('found','none_found')) ORDER BY p.priority_rank").fetchall()
  for candidate,cycle in todo:
   before=transport.http_attempts;records_before=len(getattr(transport,'records',[]));failure=None
   cursor={};ids=[];archived=[];issues=set();ignored=0;endpoint=contract['endpoint'].replace('{candidate_id}',candidate)
   try:
    while True:
     query=candidate_committees_query(candidate,cycle,cursor=cursor)
     data,evidence=transport.fetch(endpoint,query,exact_decimal=True)
     if evidence.get('status')!=200 or evidence.get('original_response_bytes')is not True or evidence.get('query')!=query or evidence.get('endpoint')!=endpoint:raise ArchiveBindingFailed('Successful original response evidence does not bind the exact request.')
     archive=_verify_archive(evidence,'Original response archive does not verify; no lookup admitted.')
     progress=inspect_candidate_committees_page(query,data);issues.update(progress['issues']);ignored+=progress['ignored_non_principal_rows']
     ids.extend(item for item in progress['committee_ids']if item not in ids)
     archived.append({'page':query['page'],'path':str(archive),'sha256':evidence['sha256'],'bytes':evidence['bytes'],'captured_utc':evidence.get('captured_utc')})
     cursor=progress['next_cursor']
     if len(archived)>=20 and cursor:issues.add('too_many_pages_unresolved')
     if not cursor or issues or any(item['page']==cursor.get('page')for item in archived):break
   except Exception as caught:failure=caught
   attempts=transport.http_attempts-before
   # every archive pair this attempt produced, including the one a failing page left behind (an attempt that never got a response has none)
   refs=[{'attempt':r.get('attempt'),'status':r.get('status'),'path':r.get('path'),'receipt_path':r.get('receipt_path'),'sha256':r.get('sha256'),'bytes':r.get('bytes'),'captured_utc':r.get('captured_utc'),'refused_secret_echo':bool(r.get('refused_secret_echo'))}for r in list(getattr(transport,'records',[]))[records_before:]]
   with self.transaction():
    if failure is None:
     detail=encoded({'archive_pages':archived,'issues':sorted(issues),'ignored_non_principal_rows':ignored,'http_attempts':attempts,'contract_id':contract['contract_id']})
     status='failed'if issues else'found'if ids else'none_found'
     if status=='found':
      for committee in ids:self.db.execute('INSERT OR IGNORE INTO candidate_committee_map VALUES(?,?,?,?,?,?,?,?,?)',(candidate,cycle,committee,'P','api_candidate_committees',archived[0]['sha256'],archived[0]['captured_utc']or now(),detail,now()))
    else:
     status='failed'
     detail=encoded({'error_type':type(failure).__name__,'detail':str(failure),'http_attempts':attempts,'archive_record':refs[-1]if refs else None})
    self._record_lookup(candidate,cycle,'api_candidate_committees',status,detail,attempts=attempts,refs=refs)
   outcome['lookups']+=1;outcome[status]+=1;outcome['http_attempts']+=attempts
   if failure is not None and _is_global(failure):
    outcome['stopped']={'error_type':type(failure).__name__,'detail':str(failure),'global_admission_archive_auth_quota_or_failure_allowance':True};break
  with self.transaction():outcome['units_seeded_now']=self._seed_priority_units()
  return outcome
 def priority_summary(self):
  if not self.extensions_ready:return None
  one=lambda sql:self.db.execute(sql).fetchone()[0]
  return {'priority_entries':one('SELECT COUNT(*) FROM campaign_priority'),
   'entries_with_principal_committee_mapping':one('SELECT COUNT(*) FROM campaign_priority p WHERE EXISTS(SELECT 1 FROM candidate_committee_map m WHERE m.candidate_id=p.candidate_id AND m.cycle=p.cycle)'),
   'priority_committee_units':one('SELECT COUNT(*) FROM (SELECT DISTINCT m.committee_id,m.cycle FROM campaign_priority p JOIN candidate_committee_map m ON m.candidate_id=p.candidate_id AND m.cycle=p.cycle)'),
   'candidate_committee_map_rows':one('SELECT COUNT(*) FROM candidate_committee_map')}
 def report(self,completed_pages=0,stopped=None):
  endpoint_counts={r[0]+':'+r[1]:r[2] for r in self.db.execute('SELECT kind,status,COUNT(*) FROM endpoints GROUP BY kind,status')}
  outgoing=sorted(kind for kind in self.supported if kind!='A'and kind not in NON_PAYMENT_KINDS)
  pending=self.db.execute("SELECT COUNT(*) FROM endpoints WHERE kind IN("+",".join("?"for _ in outgoing)+")AND status!='complete'",outgoing).fetchone()[0]
  count=lambda table:self.db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
  closure=self.closure()   # derived from seeds+edges+policy; never stored per unit
  result={'schema_version':1,'ledger_schema_version':SCHEMA_VERSION,'generated_utc':now(),'ledger':stored(self.path),'units':count('units'),'origin_seed_assignments':count('origin_seeds'),'origin_catalog_entries':count('origin_catalog'),'origin_unit_assignments':closure.total,**origin_closure.disclosure(),'origin_unit_assignments_note':'Derived by origin_closure from origin_seeds, edges and the recorded later-cycle policy: origins OBSERVED to reach a unit through collected disclosures. A lower bound; an absent origin is not observed, never proven not to reach.','pages':count('pages'),'observations':count('observations'),'edges':count('edges'),'candidate_resolutions_required':count('candidate_resolutions'),'candidate_beneficiary_or_unverified_references':count('candidate_references'),'counterparty_identity_conflicts':count('counterparty_conflicts'),'leaf_disclosure_boundaries':count('leaf_coverage'),'endpoint_status_counts':endpoint_counts,'new_pages_this_invocation':completed_pages,'stop':stopped,'supported_outgoing_endpoints_complete':pending==0,'exhaustive_disclosed_spending_complete':False,'required_unsupported_endpoints':sorted({r[0]for r in self.db.execute("SELECT kind FROM endpoints WHERE status='unsupported'")}),
   'endpoint_contract_enablement':_stored_enablement(self.contract_enablement),'supported_outgoing_endpoint_kinds':outgoing,
   'later_cycle_context_explicitly_enabled':self.include_later_cycle_context,'later_context_missing_when_disabled':not self.include_later_cycle_context,'unknown_or_subcent_amount_occurrences':self.db.execute("SELECT COUNT(*) FROM observations WHERE amount_status!='valid_decimal'AND kind NOT IN('totals')").fetchone()[0],
   'other_spending_families':'/'.join(['B/E']+[kind for kind in('F','H4')if kind in outgoing])+' are overlapping evidence. Other disclosure families, report/unitemized totals, loans/debts and efile timing require independent coverage.','legacy_snapshot_compatible':False,'snapshots_json_or_evidence_json_published':False,'incoming_receipts_not_requested_means_unknown_not_empty':True,'no_exact_origin_dollar_attribution':True,'no_B_E_grand_total':True,'UI_top_depth_or_page_limits_used':False,'campaign_priority':self.priority_summary()}
  tiers=self.bulk_tier_summary()   # D076: present only in a ledger that holds endpoint_bulk_tier
  if tiers is not None:result['bulk_activity_tiers']=tiers
  return result
 def close(self):self.db.close()

class PageFailure(Exception):
 def __init__(self,row,error,query,evidence,attempts):self.row,self.error,self.query,self.evidence,self.attempts=row,error,query,evidence,attempts;super().__init__(str(error))

TIER_JOIN=" LEFT JOIN endpoint_bulk_tier t ON t.committee_id=e.committee_id AND t.cycle=e.cycle AND t.kind=e.kind"
# M1 (owner ruling): an endpoint that keeps failing (3 or more attempts beyond its stored pages) sorts AFTER tier 3, still pending, so a handful of failing tier 1/2 endpoints can never
# take the whole failure allowance of every run and starve tier 3. The 4 is only a sort position: it is never stored, shown or read anywhere else.
TIER_KEY="CASE WHEN e.attempt_count-e.pages_stored>=3 THEN 4 ELSE COALESCE(t.tier,2) END"
COPIED_TABLES=('units','endpoints','pages','observations','edges','counterparty_conflicts','candidate_resolutions','candidate_references','leaf_coverage','failures')
def _immutable(path):
 db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro&immutable=1',uri=True,timeout=.5);db.execute('PRAGMA query_only=ON');return db
def migrate_to_schema3(source,destination,*,later_cycle_context,seed_file=None,snapshot_proof=None):
 """Write a NEW schema-3 ledger (and MIGRATION.json) from a quiescent schema-2 ledger; the source is only read (immutable open), never rewritten.
 The new ledger is accepted only if the closure derived from its catalog+seeds+edges+policy reproduces EVERY stored origins row and its metadata
 byte for byte (0 mismatches); otherwise nothing is left behind. Seeds come from the captured seed file when given (exactly what seed() stores),
 else they are the stored rows no edge predecessor and no earlier-cycle carrier explains."""
 if type(later_cycle_context)is not bool:raise ValueError('The ledger later-cycle context policy must be stated explicitly (true/false).')
 source,destination=Path(source).resolve(),Path(destination).resolve();partial=destination.with_name(destination.name+'.partial');proof_path=destination.with_name('MIGRATION.json')
 for path in(destination,partial,proof_path):
  if path.exists():raise FileExistsError('Migration writes new files only; already exists: '+str(path))
 if not destination.parent.is_dir():raise FileNotFoundError('Destination folder must exist: '+str(destination.parent))
 for suffix in('-journal','-wal','-shm'):
  if Path(str(source)+suffix).exists()and Path(str(source)+suffix).stat().st_size:raise ValueError('Quiescent source required; '+suffix+' present.')
 if shutil.disk_usage(destination.parent).free<source.stat().st_size+(64<<20):raise OSError('Not enough free space beside the destination for a new ledger.')
 source_sha=sha(source);seed_receipt=None;file_rows=None;binding=None
 if snapshot_proof is not None:   # an accepted snapshot's SNAPSHOT.json binds the bytes and the policy the ledger was collected under
  recorded=json.loads(Path(snapshot_proof).read_bytes())
  if recorded.get('snapshot_sha256')!=source_sha or recorded.get('later_cycle_context_explicitly_enabled')is not later_cycle_context:raise ValueError('Snapshot proof does not bind this source ledger and the stated later-cycle policy.')
  binding={'path':str(Path(snapshot_proof).resolve()),'sha256':sha(snapshot_proof)}
 if seed_file is not None:file_rows,seed_receipt,_=captured_seeds(seed_file)
 src=_immutable(source);problems={}
 try:
  if src.execute('PRAGMA user_version').fetchone()[0]!=2:raise ValueError('Migration source must be a schema-2 ledger.')
  if src.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Migration source integrity failed.')
  model=sqlite3.connect(':memory:');model.executescript(SCHEMA2);names={r[0]for r in model.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  if {r[0]for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}!=names or any([tuple(r)for r in src.execute('PRAGMA table_info('+n+')')]!=[tuple(r)for r in model.execute('PRAGMA table_info('+n+')')]for n in names):raise ValueError('Migration source differs from the reviewed schema-2 layout.')
  model.close()
  units=[(r[0],r[1])for r in src.execute('SELECT committee_id,cycle FROM units')];known=set(units)
  edges=[tuple(r)for r in src.execute('SELECT DISTINCT committee_id,cycle,destination_committee_id,destination_cycle FROM edges')]
  if({(e[0],e[1])for e in edges}|{(e[2],e[3])for e in edges})-known:raise ValueError('An edge names a unit the ledger does not hold.')
  if later_cycle_context and any((c,y2)not in known for c,y in units for y2 in origin_closure.CYCLES if y2>y):raise ValueError('Policy true but a unit lacks a later-cycle unit; the policy and the ledger disagree.')
  catalog=[];ref_of={};file_seeds=set()   # catalog entries [origin_id,base,first]; refs start at 1 like the collector's own INTEGER PRIMARY KEY
  def entry(origin,base,first):
   if origin not in ref_of:ref_of[origin]=len(catalog)+1;catalog.append([origin,base,first])
   item=catalog[ref_of[origin]-1]
   if encoded(item[1])!=encoded(base):raise ValueError('Origin '+origin+' has more than one base metadata; schema 3 stores one.')
   item[2]=min(item[2],first);return ref_of[origin]
  for committee,cycle,origin,base,first in(seed_assignments(file_rows)if file_rows is not None else()):file_seeds.add((committee,cycle,entry(origin,base,first)))
  stored=collections.defaultdict(int);firsts=collections.defaultdict(set);rows=0
  for committee,cycle,origin,text in src.execute('SELECT committee_id,cycle,origin_id,metadata_json FROM origins'):
   rows+=1;metadata=json.loads(text);base=origin_closure.base_of(metadata);first=metadata.get('first_reached_cycle')
   if origin not in ref_of:
    if file_rows is not None:problems['origin_rows_not_in_seed_file']=problems.get('origin_rows_not_in_seed_file',0)+1
    entry(origin,base,first if type(first)is int else 2026)
   elif encoded(catalog[ref_of[origin]-1][1])!=encoded(base):problems['origin_rows_with_other_base_metadata']=problems.get('origin_rows_with_other_base_metadata',0)+1
   firsts[ref_of[origin]].add(first);stored[(committee,cycle)]|=1<<ref_of[origin]
  if file_rows is None:
   for ref,seen in firsts.items():catalog[ref-1][2]=min([v for v in seen if type(v)is int]or[2026])
  parents=collections.defaultdict(list)
  for c,y,d,dy in edges:
   if dy>=y and(c,y)!=(d,dy):parents[(d,dy)].append((c,y))
  if later_cycle_context:
   for c,y in units:
    for later in origin_closure.CYCLES:
     if later>y:parents[(c,later)].append((c,y))
  derived=set()
  for unit,bits in stored.items():
   explained=0
   for parent in parents.get(unit,()):explained|=stored.get(parent,0)
   derived.update((unit[0],unit[1],ref)for ref in origin_closure.iter_bits(bits&~explained))
  seeds=file_seeds if file_rows is not None else derived
  seed_check={'derived_unexplained_seed_rows':len(derived),'seed_file_seed_rows':len(file_seeds)if file_rows is not None else None,'seed_file_equals_derived':(file_seeds==derived)if file_rows is not None else None}
  dst=sqlite3.connect(partial.as_uri(),uri=True,isolation_level=None)
  try:
   dst.execute('PRAGMA journal_mode=OFF');dst.execute('PRAGMA synchronous=OFF');dst.executescript(SCHEMA)
   dst.execute('ATTACH DATABASE ? AS src',(source.as_uri()+'?mode=ro&immutable=1',));dst.execute('BEGIN')
   for table in COPIED_TABLES:dst.execute('INSERT INTO main.'+table+' SELECT * FROM src.'+table)
   dst.executemany('INSERT INTO origin_catalog VALUES(?,?,?,?)',[(number+1,item[0],encoded(item[1]),item[2])for number,item in enumerate(catalog)])
   dst.executemany('INSERT INTO origin_seeds VALUES(?,?,?)',sorted(seeds));dst.execute("INSERT INTO ledger_policy VALUES('later_cycle_context',?)",('true'if later_cycle_context else'false',));dst.execute('COMMIT')
   copied={}
   for table in COPIED_TABLES:
    left=src.execute('SELECT * FROM '+table+' ORDER BY rowid');right=dst.execute('SELECT * FROM main.'+table+' ORDER BY rowid');count=0;same=True
    for a,b in zip(left,right):
     count+=1;same=same and tuple(a)==tuple(b)
    left.close();right.close()
    total=src.execute('SELECT COUNT(*) FROM '+table).fetchone()[0];copied[table]={'rows':total,'identical':same and count==total==dst.execute('SELECT COUNT(*) FROM main.'+table).fetchone()[0]}
   dst.execute('DETACH DATABASE src');dst.execute('PRAGMA user_version='+str(SCHEMA_VERSION))
   # Verification reads the NEW file back: catalog, seeds, policy and edges are all that a consumer will have.
   closure=origin_closure.from_db(dst);written=origin_closure.read_catalog(dst);earliest=closure.earliest_cycles();only_stored=only_closure=0
   for unit in set(units)|set(stored):
    only_stored+=(stored.get(unit,0)&~closure.bitset(unit)).bit_count();only_closure+=(closure.bitset(unit)&~stored.get(unit,0)).bit_count()
   equal=unequal=0
   for committee,cycle,origin,text in src.execute('SELECT committee_id,cycle,origin_id,metadata_json FROM origins'):
    item=written[ref_of[origin]];expected=origin_closure.metadata_json(item[1],item[2],cycle)
    if text==expected:equal+=1
    else:unequal+=1
   r2=sum(1 for ref,item in written.items()if earliest.get(ref)!=item[2])
   mismatches=only_stored+only_closure+unequal+sum(problems.values())
   lossless=mismatches==0 and rows==closure.total and not closure.undeclared and not closure.missing_forward and all(v['identical']for v in copied.values())and dst.execute('PRAGMA quick_check').fetchone()[0]=='ok'
   dst_counts={'units':len(units),'edges_distinct_unit_pairs':len(edges),'origins_rows_stored_schema2':rows,'origin_catalog_entries':len(written),'origin_seed_rows':len(seeds),'closure_rows_derived':closure.total,'scc_groups_with_cycles':len(closure.cyclic_components())}
  finally:dst.close()
  if not lossless:
   partial.unlink(missing_ok=True)
   raise ValueError('Migration refused (nothing written): '+encoded({'mismatches':mismatches,'rows_only_in_stored':only_stored,'rows_only_in_closure':only_closure,'metadata_unequal':unequal,'problems':problems,'r2_violations':r2,'counts':dst_counts,'copied':{k:v['identical']for k,v in copied.items()}}))
  with partial.open('rb+')as stream:os.fsync(stream.fileno())
  partial_sha=sha(partial);os.rename(partial,destination)
 finally:src.close()
 proof={'schema_version':1,'generated_utc':now(),'tool':{'path':str(Path(__file__).resolve()),'sha256':sha(__file__),'origin_closure_sha256':sha(origin_closure.__file__),'command':'--migrate-to-schema3'},
  'source':{'path':str(source),'sha256':source_sha,'bytes':source.stat().st_size,'user_version':2,'opened':'mode=ro&immutable=1','never_rewritten':True},
  'destination':{'path':str(destination),'sha256':sha(destination),'sha256_before_rename':partial_sha,'bytes':destination.stat().st_size,'user_version':SCHEMA_VERSION},
  'later_cycle_context':{'value':later_cycle_context,'basis':'explicit argument; the new ledger records it and refuses a different invocation policy'},
  'snapshot_proof':binding,'seed_basis':'captured_seed_file'if file_rows is not None else'derived_unexplained_rows','seed_file':seed_receipt,'seed_cross_check':seed_check,'counts':dst_counts,
  'equality':{'closure_equals_stored_origins':True,'rows_only_in_stored':only_stored,'rows_only_in_closure':only_closure,'metadata_byte_equal_rows':equal,'metadata_mismatches':unequal,'mismatches_total':mismatches,
   'first_reached_cycle_not_earliest_carrying_cycle':r2,'origin_rows_with_other_base_metadata':problems.get('origin_rows_with_other_base_metadata',0)},
  'copied_tables_row_for_row_identical':copied,'result':'lossless'}
 with proof_path.open('x',encoding='utf-8')as stream:json.dump(proof,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
 return proof
def migrate_main(argv):
 parser=argparse.ArgumentParser(prog='collect_committee_activity.py',description=migrate_to_schema3.__doc__)
 parser.add_argument('--migrate-to-schema3',nargs=2,required=True,metavar=('SRC','DST'),type=Path);parser.add_argument('--later-cycle-context',required=True,choices=('true','false'),help='the ledger policy; recorded in the new ledger')
 parser.add_argument('--seed-file',type=Path,help='captured seed file; seeds are then exactly what seed() stores, otherwise they are derived from the stored rows');parser.add_argument('--snapshot-proof',type=Path,help='SNAPSHOT.json of the source; must bind its bytes and the stated policy')
 args=parser.parse_args(argv);proof=migrate_to_schema3(*args.migrate_to_schema3,later_cycle_context=args.later_cycle_context=='true',seed_file=args.seed_file,snapshot_proof=args.snapshot_proof)
 print(encoded({'migrated':proof['destination']['path'],'migration_proof':str(Path(proof['destination']['path']).with_name('MIGRATION.json')),'result':proof['result'],'closure_rows':proof['counts']['closure_rows_derived']}))

MAX_WORKERS=32
class RecoveryRefused(RuntimeError):pass
def _key(path):return os.path.normcase(str(Path(hosted(path)).resolve()))
def _walk_strings(value):
 if isinstance(value,str):yield value
 elif isinstance(value,dict):
  for item in value.values():yield from _walk_strings(item)
 elif isinstance(value,list):
  for item in value:yield from _walk_strings(item)
def _quarantine_files(folder,quarantine,move):
 """Move each (name, reason) out of the run folder into quarantine, returning the manifest; every refusal leaves the folder as it was found."""
 quarantined=[]
 if move:
  quarantine=Path(quarantine);quarantine.mkdir(parents=True,exist_ok=True)
  try:
   for name,reason in move:
    source=Path(folder)/name
    try:entry={'name':name,'sha256':sha(source),'bytes':source.stat().st_size,'reason':reason}
    except OSError as error:raise RecoveryRefused('Cannot read '+name+' to quarantine it ('+str(error)+'); rerun once it is released.')from None
    try:_move(source,quarantine/name)
    except OSError as error:raise RecoveryRefused('Cannot move '+name+' out of the run folder ('+str(error)+'); rerun once it is released.')from None
    quarantined.append(entry)
  except RecoveryRefused:
   for empty in(quarantine,quarantine.parent):
    try:empty.rmdir()
    except OSError:pass
   raise
 return quarantined
def _bound_pair(body,meta,relocated=False):
 # (record,bytes) when the metadata binds this exact body, else None.
 try:
  raw=body.read_bytes();record=json.loads(meta.read_bytes())
  ok=(isinstance(record,dict)and record.get('sha256')==hashlib.sha256(raw).hexdigest()and record.get('bytes')==len(raw)and record.get('original_response_bytes')is True
   and record.get('raw_body_persisted')is True and type(record.get('status'))is int and type(record.get('attempt'))is int and isinstance(record.get('endpoint'),str)
   and isinstance(record.get('query'),dict)and isinstance(record.get('path'),str)and (Path(record['path']).name==body.name if relocated else _key(record['path'])==_key(body)))
 except(OSError,ValueError):return None
 return(record,raw)if ok else None
def _move(source,target,attempts=5):
 # Windows AV/indexers hold files briefly: retry a sharing violation, then let the caller refuse.
 for number in range(attempts):
  try:os.replace(source,target);return
  except PermissionError:
   if number+1==attempts:raise
   time.sleep(.2*(number+1))
class _Job:
 __slots__=('seq','row','key','endpoint','field','query')
 def __init__(self,row,endpoint,field,query):self.seq=0;self.row=row;self.key=(row['committee_id'],row['cycle'],row['kind']);self.endpoint=endpoint;self.field=field;self.query=query
class _Outcome:
 __slots__=('data','evidence','attempts','error','skipped')
 def __init__(self,data=None,evidence=None,attempts=0,error=None,skipped=False):self.data=data;self.evidence=evidence;self.attempts=attempts;self.error=error;self.skipped=skipped
def _status(error):
 source_refused=isinstance(error,HTTPError)and error.code in(400,404,405,410,422)
 # Refusals that are not about this endpoint's evidence leave it pending and unmodified.
 return 'pending'if type(error).__name__ in('FECAdmissionRefused','EndpointContractRefused','BrokerUnavailable')or _key_scoped(error)else'source_refused'if source_refused else'inconsistent'if isinstance(error,(ValueError,AssertionError))and type(error).__name__ not in('FECArchiveFailed','ArchiveBindingFailed')else'blocked'
# HTTP 429 is scoped to the one key that received it: the pool cools that key until its window resets and every other key keeps going.
# Only when no key can be admitted any more does admission refuse (FECAdmissionRefused), which is the global stop.
# The 429'd endpoint goes straight back to pending (no retry_after, no parking) so another key can serve it; its failure row and archive pair are still written.
def _is_global(error):return type(error).__name__ in('FECAdmissionRefused','FECArchiveFailed','ArchiveBindingFailed','EndpointContractRefused','BrokerUnavailable')or isinstance(error,sqlite3.Error)or isinstance(error,HTTPError)and error.code in(401,403)
def _key_scoped(error):return isinstance(error,HTTPError)and error.code==429
def _pool_stopped(error):return type(error).__name__=='FECAdmissionRefused'and'request pool is stopped'in str(error)
class _Rates:
 # Achieved physical requests/hour, whole run and rolling (a shorter history uses the span it has).
 def __init__(self,total):self.start=time.monotonic();self.base=total;self.samples=collections.deque([(self.start,total)]);self.peak60=0.0
 def note(self,total):
  now=time.monotonic()
  if len(self.samples)>1 and now-self.samples[-1][0]<.25:self.samples.pop()
  self.samples.append((now,total))
  while len(self.samples)>2 and self.samples[1][0]<=now-600:self.samples.popleft()
  if now-self.samples[0][0]>=10:self.peak60=max(self.peak60,self.over(60))
 def over(self,seconds):
  now,total=self.samples[-1];first=next((s for s in self.samples if s[0]>=now-seconds),self.samples[0]);span=now-first[0]
  return(total-first[1])/span*3600 if span>0 else 0.0
 def overall(self):
  span=self.samples[-1][0]-self.start;return(self.samples[-1][1]-self.base)/span*3600 if span>0 else 0.0
def _stop_for(error,failures,limit,halt):
 if _is_global(error)or failures>=limit:
  halt.set();return{'error_type':type(error).__name__,'detail':str(error),'global_admission_archive_auth_quota_or_failure_allowance':True}
def _fetch(transport,job,halt,per_call):
 # Never raises: the writer commits every outcome. A halted job never touched the transport (no ledger change, no pair).
 if halt.is_set():return _Outcome(skipped=True)
 if per_call:
  call={}
  try:
   data,evidence=transport.fetch(job.endpoint,job.query,exact_decimal=True,call=call)
   return _Outcome(data,evidence,call.get('attempts',0))
  except Exception as error:
   if _is_global(error):halt.set()   # bounds post-refusal admissions
   return _Outcome(evidence=call.get('evidence'),attempts=call.get('attempts',0),error=error)
 # Serial transports without per-call reporting (workers==1 only): the global deltas are exact when nothing runs concurrently.
 before,seen=transport.http_attempts,len(getattr(transport,'records',[]))
 try:
  data,evidence=transport.fetch(job.endpoint,job.query,exact_decimal=True)
  return _Outcome(data,evidence,transport.http_attempts-before)
 except Exception as error:
  evidence=None
  if len(getattr(transport,'records',[]))>seen:
   last=transport.records[-1]
   if last.get('query')==job.query and last.get('endpoint')==job.endpoint:evidence=last
  return _Outcome(evidence=evidence,attempts=transport.http_attempts-before,error=error)
def _ready(inflight,pool,tick):
 if pool is None:return list(inflight)
 futures={value[2]:key for key,value in inflight.items()}
 while True:   # short waits keep Ctrl-C responsive on Windows and drive the progress telemetry
  done,_=wait(list(futures),timeout=.5,return_when=FIRST_COMPLETED)
  if done:return[futures[future]for future in done]
  tick()
def write_new(path,document):
 # Atomic and exclusive (the contracts module's intent publication): write a partial file, fsync it, hard-link it to the final name, drop the partial. The final name
 # therefore never holds a truncated file, so a kill mid-write leaves the run UNRESOLVED (a stray *.partial-* file is ignored by every glob); an existing file is never replaced.
 path=Path(path);partial=path.with_name(path.name+'.partial-'+uuid.uuid4().hex)
 try:
  with partial.open('x',encoding='utf-8')as f:json.dump(document,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
  os.link(partial,path)
  try:descriptor=os.open(str(path.parent),os.O_RDONLY)
  except OSError:descriptor=None   # Windows cannot open a directory; the file itself is fsynced
  if descriptor is not None:
   try:os.fsync(descriptor)
   except OSError:pass
   finally:os.close(descriptor)
 finally:
  try:partial.unlink(missing_ok=True)
  except OSError:pass   # e.g. a sharing violation from a scanner or sync engine: the final name is already published and a stray *.partial-* file is ignorable
class _MsvcrtLease:
 @staticmethod
 def acquire(stream):
  import msvcrt;stream.seek(0)
  try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
  except OSError:return False
  return True
 @staticmethod
 def release(stream):
  import msvcrt;stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
class _FcntlLease:
 # POSIX (the D075 container runtime): an advisory flock on the lease file; the kernel drops it on any exit, including a kill, so a dead holder never blocks the next run.
 # A second open of the same file in this process conflicts too (flock locks the open file description), as the msvcrt lease does.
 @staticmethod
 def acquire(stream):
  import fcntl
  try:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return False
  return True
 @staticmethod
 def release(stream):
  import fcntl;fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
def lease_backend(platform=None):return _MsvcrtLease if(os.name if platform is None else platform)=='nt'else _FcntlLease
@contextmanager
def run_lease(state,*,backend=None):
 # One collector (or recovery, close-out, re-bind) per state directory; short commits no longer hold the SQLite writer across a fetch.
 # The OS drops the lock on any exit, including a hard kill. Observers must never open this file.
 backend=backend or lease_backend()
 with(Path(state)/'frontier.sqlite.run-lease').open('a+b')as stream:
  if stream.tell()==0:stream.write(b'0');stream.flush()
  if not backend.acquire(stream):raise SystemExit('Another collector invocation or recovery holds the run lease; nothing was written.')
  try:yield
  finally:backend.release(stream)
def unresolved_runs(state,archive_root=None):
 """(crashed, legacy): runs with a START but neither receipt nor ABANDONED marker, and response folders with no START or receipt at all.
 archive_root (D075): where responses-<run> folders live when that is not `state` (the host evidence root, seen read-only from a container); a receipt or marker in either place resolves a run."""
 state=Path(state);archive=Path(archive_root)if archive_root is not None else state
 resolved=lambda run:any((root/('RECEIPT-'+run+'.json')).exists()or(root/('RUN-'+run+'.ABANDONED.json')).exists()for root in(state,archive))
 starts=lambda root:[p.name[len('RUN-'):-len('.START.json')]for p in sorted(root.glob('RUN-*.START.json'))]
 started=starts(state);known=set(started)|set(starts(archive))
 legacy=[p.name[len('responses-'):]for p in sorted(archive.glob('responses-*'))if p.is_dir()and p.name[len('responses-'):]not in known and not resolved(p.name[len('responses-'):])]
 return[run for run in started if not resolved(run)],legacy
def recover_crashed_runs(state,archive_root=None):
 state=Path(state);archive=Path(archive_root)if archive_root is not None else state;crashed,legacy=unresolved_runs(state,archive_root)
 if legacy:raise RecoveryRefused('Folder(s) responses-'+', responses-'.join(legacy)+' have neither a START record nor a receipt (a legacy crash, or a collector still running); a human must decide. Nothing was changed.')
 if not crashed:return{'recovered':False,'reason':'No unresolved collector run.'}
 if len(crashed)>1:raise RecoveryRefused('More than one unresolved run ('+', '.join(crashed)+'); a human must decide. Nothing was changed.')
 run=crashed[0];start=json.loads((state/('RUN-'+run+'.START.json')).read_bytes())
 if start.get('schema_version')!=1 or start.get('run_id')!=run:raise RecoveryRefused('Run start record is malformed; nothing was changed.')
 folder=archive/('responses-'+run);base=start['ledger_at_start']
 frontier=Frontier(state/'frontier.sqlite',optional_receipts=start['optional_receipts'],include_later_cycle_context=start['later_cycle_context'],enabled_endpoint_contracts=start['enabled_endpoint_contracts'])
 try:
  work=frontier.recover(folder,state/'crash-quarantine'/run,readonly=archive!=state)
  pages,total=frontier.db.execute('SELECT (SELECT COUNT(*) FROM pages),(SELECT COALESCE(SUM(attempt_count),0) FROM endpoints)').fetchone()
  lookups=(frontier.db.execute('SELECT COALESCE(SUM(http_attempts),0) FROM candidate_committee_lookups').fetchone()[0]if frontier.extensions_ready else 0)-base.get('sum_lookup_http_attempts',0)
  if work['referenced_pairs']==0 and pages==base['pages']and total==base['sum_attempt_count']and lookups==0:
   marker=state/('RUN-'+run+'.ABANDONED.json');write_new(marker,{'schema_version':1,'purpose':'collector-run-abandoned','run_id':run,'abandoned_utc':now(),'quarantined':work['quarantined'],'ledger_at_start':base,'reason':'The run left no referenced archive and no ledger attempts.'})
   return{'recovered':True,'run_id':run,'receipt':None,'abandoned':str(marker),'quarantined':len(work['quarantined'])}
  report=frontier.report(pages-base['pages'],{'error_type':'CrashRecovered','detail':'Collector process ended before writing its receipt; this receipt was written by --recover-crashed-runs.','global_admission_archive_auth_quota_or_failure_allowance':True})
  # Every physical attempt counts: endpoint attempts plus candidate-committee lookup pages (the R1 equality is per physical attempt).
  report.update(http_attempts=total-base['sum_attempt_count']+lookups,endpoint_attempts_recorded=total-base['sum_attempt_count'],candidate_committee_lookup_http_attempts=lookups,physical_request_cap=start['max_requests'],failed_attempt_allowance=start['max_failed_attempts'],raw_archive_directory=stored(folder),workers=start['workers'],
   recovered_after_crash=True,recovery={'quarantined':work['quarantined'],'replayed_pairs':work['replayed_pairs'],'unrecorded_physical_attempts':'unknown; the shared budget ledger may exceed the recorded attempts (requests in flight at the kill are charged but never recorded)'})
  report.update(ledger_sha256=sha(frontier.path),seed_receipt=seed_after(start['seed_receipt']))
  output=state/('RECEIPT-'+run+'.json');write_new(output,report)
  return{'recovered':True,'run_id':run,'receipt':str(output),'http_attempts':report['http_attempts'],'replayed_pairs':work['replayed_pairs'],'quarantined':len(work['quarantined'])}
 finally:frontier.close()

def _process_alive(pid,creation_filetime):
 # Fails CLOSED: only "no such process" (ERROR_INVALID_PARAMETER) or a different creation time (a reused pid) proves the process is gone. Access denied, an unreadable
 # handle, a bad pid value (anything but 0 < pid < 2**32) or a platform that cannot tell all say alive, so the close-out refuses. Never os.kill (it terminates the process on Windows).
 if os.name!='nt'or type(pid)is not int or not 0<pid<2**32:return True
 import ctypes
 from ctypes import wintypes
 kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.OpenProcess.restype=wintypes.HANDLE;kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
 kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.c_void_p]*4;kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.c_void_p];kernel.CloseHandle.argtypes=[wintypes.HANDLE]   # 64-bit handles must not be truncated
 handle=kernel.OpenProcess(0x1000,False,pid)   # PROCESS_QUERY_LIMITED_INFORMATION
 if not handle:return ctypes.get_last_error()!=87
 try:
  created,exited,kernel_time,user_time=(wintypes.FILETIME()for _ in range(4));code=wintypes.DWORD()
  if not kernel.GetProcessTimes(handle,*(ctypes.byref(item)for item in(created,exited,kernel_time,user_time))):return True
  if creation_filetime is not None and(created.dwHighDateTime<<32|created.dwLowDateTime)!=creation_filetime:return False   # a different process now owns this pid
  if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)):return True
  return code.value==259   # STILL_ACTIVE
 finally:kernel.CloseHandle(handle)
def _launched_processes(launch):
 """(label, pid, creation filetime) of every process LAUNCHED.json names: the collector child (required) and, when recorded, the observer."""
 child=launch.get('child_pid')
 if type(child)is not int:raise RecoveryRefused('LAUNCHED.json names no child process id, so it cannot be proven gone; nothing was changed.')
 found=[('collector child',child,launch.get('child_creation_filetime'))]
 observer=(launch.get('preflight')or{}).get('observer_pid')
 if observer is not None:
  if type(observer)is not int:raise RecoveryRefused('LAUNCHED.json has an unusable observer process id; nothing was changed.')
  found.append(('observer',observer,(launch.get('preflight')or{}).get('observer_creation_filetime')))
 return found
def _launched_settings(launch):
 argv=launch.get('actual_driver_argv')
 if not isinstance(argv,list)or not argv or not all(isinstance(item,str)for item in argv):raise RecoveryRefused('LAUNCHED.json has no driver argv; nothing was changed.')
 flags=argv[1:]
 if '--enable-endpoint-contract' in flags or '--workers' in flags:raise RecoveryRefused('The legacy close-out covers a serial B/E run only; this launch enabled contracts or workers. Nothing was changed.')
 def value(flag,default=None):
  if flag not in flags:return default
  index=flags.index(flag)
  if index+1>=len(flags):raise RecoveryRefused('LAUNCHED.json argv has '+flag+' without a value; nothing was changed.')
  return flags[index+1]
 try:cap=int(value('--max-requests'));allowance=int(value('--max-failed-attempts',8))
 except(TypeError,ValueError):raise RecoveryRefused('LAUNCHED.json argv has no integer --max-requests; nothing was changed.')from None
 if launch.get('physical_cap',cap)!=cap or launch.get('source_failure_allowance',allowance)!=allowance:raise RecoveryRefused('LAUNCHED.json argv disagrees with its own recorded cap or allowance; nothing was changed.')
 return{'max_requests':cap,'allowance':allowance,'later':'--include-later-cycle-context'in flags,'optional':'--optional-receipts'in flags,'seeds':value('--seeds')}
def _legacy_report(db,path,*,pages_new,stopped,later):
 # The schema-2 receipt shape the pre-D073 collector wrote (kept so the chain's receipts stay uniform); the ledger is only read.
 endpoint_counts={r[0]+':'+r[1]:r[2]for r in db.execute('SELECT kind,status,COUNT(*) FROM endpoints GROUP BY kind,status')}
 outgoing=sorted(kind for kind in SUPPORTED if kind!='A')
 pending=db.execute("SELECT COUNT(*) FROM endpoints WHERE kind IN("+",".join("?"for _ in outgoing)+")AND status!='complete'",outgoing).fetchone()[0]
 count=lambda table:db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
 return{'schema_version':1,'ledger_schema_version':2,'generated_utc':now(),'ledger':str(path),'units':count('units'),'origins':count('origins'),'pages':count('pages'),'observations':count('observations'),'edges':count('edges'),'candidate_resolutions_required':count('candidate_resolutions'),'candidate_beneficiary_or_unverified_references':count('candidate_references'),'counterparty_identity_conflicts':count('counterparty_conflicts'),'leaf_disclosure_boundaries':count('leaf_coverage'),'endpoint_status_counts':endpoint_counts,'new_pages_this_invocation':pages_new,'stop':stopped,'supported_outgoing_endpoints_complete':pending==0,'exhaustive_disclosed_spending_complete':False,'required_unsupported_endpoints':sorted({r[0]for r in db.execute("SELECT kind FROM endpoints WHERE status='unsupported'")}),
  'endpoint_contract_enablement':{'enabled_contract_ids':[],'activated_existing_endpoints':0,'blocked_existing_endpoints':0,'binding':None},'supported_outgoing_endpoint_kinds':outgoing,
  'later_cycle_context_explicitly_enabled':later,'later_context_missing_when_disabled':not later,'unknown_or_subcent_amount_occurrences':db.execute("SELECT COUNT(*) FROM observations WHERE amount_status!='valid_decimal'").fetchone()[0],
  'other_spending_families':'B/E are overlapping evidence. Other disclosure families, report/unitemized totals, loans/debts and efile timing require independent coverage.','legacy_snapshot_compatible':False,'snapshots_json_or_evidence_json_published':False,'incoming_receipts_not_requested_means_unknown_not_empty':True,'no_exact_origin_dollar_attribution':True,'no_B_E_grand_total':True,'UI_top_depth_or_page_limits_used':False}
def close_out_legacy_run(folder,*,baseline_snapshot,baseline_receipt,launched,state=None):
 """Offline close-out of a pre-D071 collector run (no START record, no receipt) whose process is gone; makes no provider call.

 The ledger's FIRST open is a normal read-write open, so SQLite rolls back any hot journal (never immutable first). The run's START baseline is
 synthesized from the previous accepted batch (its SNAPSHOT.json, and the receipt that snapshot binds) plus the run's LAUNCHED.json. Archive pairs
 the ledger does not reference are QUARANTINED to crash-quarantine/<run>/, never replayed (a schema-2 ledger has no commit path; the endpoint stays
 pending and the request is repeated). Every check runs before any file moves; the ledger rows are never written. Returns the summary printed by the CLI."""
 folder=Path(folder);state=Path(state)if state else folder.parent
 match=re.fullmatch(r'responses-([0-9a-f]{32})',folder.name)
 if not match or not folder.is_dir():raise RecoveryRefused('Name a responses-<32 hex> folder; nothing was changed.')
 run=match.group(1)
 if(state/('RUN-'+run+'.START.json')).exists():raise RecoveryRefused('Run '+run+' has a START record, so it is a D071 run: use --recover-crashed-runs. Nothing was changed.')
 if(state/('RECEIPT-'+run+'.json')).exists()or(state/('RUN-'+run+'.ABANDONED.json')).exists():raise RecoveryRefused('Run '+run+' already has a receipt or ABANDONED marker. Nothing was changed.')
 crashed,legacy=unresolved_runs(state);others=[item for item in crashed+legacy if item!=run]
 if others:raise RecoveryRefused('Other unresolved run(s) '+', '.join(others)+'; a human must decide first. Nothing was changed.')
 ledger=state/'frontier.sqlite'
 if not ledger.is_file():raise RecoveryRefused('No frontier.sqlite in '+str(state)+'; a close-out never creates a ledger. Nothing was changed.')
 launch=json.loads(Path(launched).read_bytes());settings=_launched_settings(launch)
 for label,pid,filetime in _launched_processes(launch):
  if _process_alive(pid,filetime):raise RecoveryRefused('The launched '+label+' process '+str(pid)+' is still alive or cannot be proven gone; it must be gone before a close-out. Nothing was changed.')
 snapshot=json.loads(Path(baseline_snapshot).read_bytes());snapshot_db=Path(baseline_snapshot).with_name('frontier.sqlite')
 prior_bytes=Path(baseline_receipt).read_bytes();prior=json.loads(prior_bytes)
 if sha(snapshot_db)!=snapshot.get('snapshot_sha256'):raise RecoveryRefused('The baseline snapshot does not hash to its SNAPSHOT.json; nothing was changed.')
 if hashlib.sha256(prior_bytes).hexdigest()!=(snapshot.get('original_collection_receipt')or{}).get('sha256'):raise RecoveryRefused('The baseline receipt is not the one the baseline snapshot binds; nothing was changed.')
 if snapshot.get('later_cycle_context_explicitly_enabled')is not settings['later']:raise RecoveryRefused('The launch and the baseline snapshot disagree on the later-cycle context; nothing was changed.')
 seeds=prior.get('seed_receipt')
 if not isinstance(seeds,dict)or not isinstance(seeds.get('sha256'),str):raise RecoveryRefused('The baseline receipt has no seed receipt; nothing was changed.')
 if settings['seeds']and Path(settings['seeds']).is_file()and sha(settings['seeds'])!=seeds['sha256']:raise RecoveryRefused('The launched seed file differs from the baseline receipt\'s seed pin; nothing was changed.')
 base=_immutable(snapshot_db)
 try:
  if base.execute('PRAGMA user_version').fetchone()[0]!=2:raise RecoveryRefused('The baseline snapshot is not a schema-2 ledger; nothing was changed.')
  base_pages,base_sum=base.execute('SELECT (SELECT COUNT(*) FROM pages),(SELECT COALESCE(SUM(attempt_count),0) FROM endpoints)').fetchone()
 finally:base.close()
 journal=Path(str(ledger)+'-journal');hot=journal.exists()and journal.stat().st_size>0
 db=sqlite3.connect(ledger,timeout=1,isolation_level=None);db.row_factory=sqlite3.Row
 try:
  version=db.execute('PRAGMA user_version').fetchone()[0];db.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()   # the first read rolls a hot journal back
  left=[suffix for suffix in('-journal','-wal','-shm')if Path(str(ledger)+suffix).exists()and Path(str(ledger)+suffix).stat().st_size]
  if left:raise RecoveryRefused('Sidecar file(s) '+', '.join(left)+' remain after the first read-write open; nothing was changed.')
  if version!=2:raise RecoveryRefused('The legacy close-out covers a schema-2 ledger only (found user_version '+str(version)+'); nothing was changed.')
  model=sqlite3.connect(':memory:');model.executescript(SCHEMA2);names={r[0]for r in model.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  same={r[0]for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}==names and all([tuple(r)for r in db.execute('PRAGMA table_info('+n+')')]==[tuple(r)for r in model.execute('PRAGMA table_info('+n+')')]for n in names);model.close()
  if not same:raise RecoveryRefused('The ledger differs from the reviewed schema-2 layout; nothing was changed.')
  if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RecoveryRefused('Ledger integrity check failed after the rollback; nothing was changed.')
  pages_now,sum_now=db.execute('SELECT (SELECT COUNT(*) FROM pages),(SELECT COALESCE(SUM(attempt_count),0) FROM endpoints)').fetchone()
  held=set();in_folder_pages=0;metadata_only=set()
  for(path,)in db.execute('SELECT archive_path FROM pages'):
   if Path(path).parent.name==folder.name:held.add(Path(path).name);in_folder_pages+=1
  for(text,)in db.execute('SELECT archive_record_json FROM failures WHERE archive_record_json IS NOT NULL'):
   record=json.loads(text);held.update(Path(item).name for item in _walk_strings(record)if item.endswith('.body.bin')and Path(item).parent.name==folder.name)
   if isinstance(record,dict)and record.get('refused_secret_echo')is True and type(record.get('attempt'))is int and isinstance(record.get('sha256'),str):metadata_only.add('attempt-%06d-%s'%(record['attempt'],record['sha256'][:12]))
  if in_folder_pages!=pages_now-base_pages:raise RecoveryRefused('The ledger holds '+str(in_folder_pages)+' page(s) from this folder but '+str(pages_now-base_pages)+' new page(s) since the baseline; the ledger and folder do not reconcile. Nothing was changed.')
  if not 0<=sum_now-base_sum<=settings['max_requests']:raise RecoveryRefused('The ledger recorded '+str(sum_now-base_sum)+' attempt(s) since the baseline, outside the launched cap of '+str(settings['max_requests'])+'. Nothing was changed.')
  stems={}
  for name in sorted(item.name for item in folder.iterdir()):
   if name.endswith('.body.bin'):stems.setdefault(name[:-9],{})['body']=name
   elif name.endswith('.json'):stems.setdefault(name[:-5],{})['meta']=name
   else:stems.setdefault(name,{})['other']=name
  move=[];referenced=orphan_pairs=0
  for stem,part in sorted(stems.items()):
   body,meta,other=part.get('body'),part.get('meta'),part.get('other')
   if body and not meta and body in held:raise RecoveryRefused('Ledger references '+body+' but its metadata is missing; a human must decide. Nothing was changed.')
   if body and meta:
    if body in held:referenced+=1;continue
    if _bound_pair(folder/body,folder/meta,relocated=True)is None:move+=[(body,'archive pair does not bind'),(meta,'archive pair does not bind')]
    else:orphan_pairs+=1;move+=[(body,'complete archive pair not referenced by the ledger; quarantined, never replayed (the request is repeated)'),(meta,'complete archive pair not referenced by the ledger; quarantined, never replayed (the request is repeated)')]
   elif meta:
    if stem in metadata_only:referenced+=1
    else:move.append((meta,'metadata without a body'))
   elif body:move.append((body,'body without metadata'))
   else:move.append((other,'unexpected file'))
  if orphan_pairs>1:raise RecoveryRefused(str(orphan_pairs)+' complete unreferenced pairs: a serial legacy run can leave at most one in flight; a human must decide. Nothing was changed.')
  quarantined=_quarantine_files(folder,state/'crash-quarantine'/run,move)
  stop={'error_type':'CrashRecovered','detail':'Legacy collector run (no START record) ended without its receipt; this receipt was written by --close-out-legacy-run.','global_admission_archive_auth_quota_or_failure_allowance':True}
  report=_legacy_report(db,ledger,pages_new=pages_now-base_pages,stopped=stop,later=settings['later'])
 finally:db.close()
 report.update(http_attempts=sum_now-base_sum,physical_request_cap=settings['max_requests'],failed_attempt_allowance=settings['allowance'],raw_archive_directory=str(folder),workers=1,recovered_after_crash=True,legacy_close_out=True,
  recovery={'mode':'legacy_close_out','quarantined':quarantined,'replayed_pairs':0,'replay_policy':'none: unreferenced pairs are quarantined (schema-2 ledger, no commit path); the endpoint stays pending','referenced_pairs':referenced,
   'hot_journal_found':hot,'hot_journal_rolled_back':hot and not journal.exists(),'synthesized_start':{'pages':base_pages,'sum_attempt_count':base_sum},
   'baseline_snapshot':{'path':str(baseline_snapshot),'sha256':sha(baseline_snapshot)},'baseline_receipt':{'path':str(baseline_receipt),'sha256':hashlib.sha256(prior_bytes).hexdigest()},
   'launched':{'path':str(launched),'sha256':sha(launched),'argv':launch['actual_driver_argv']},'unrecorded_physical_attempts':'unknown; the shared budget ledger may exceed the recorded attempts (a request in flight at the kill is charged but never recorded)'},
  ledger_sha256=sha(ledger),seed_receipt=seeds)
 output=state/('RECEIPT-'+run+'.json');write_new(output,report)
 return{'closed_out':True,'run_id':run,'receipt':str(output),'http_attempts':report['http_attempts'],'new_pages':report['new_pages_this_invocation'],'quarantined':len(quarantined),'hot_journal_found':hot}
def rebind_run(state,*,ids,optional_receipts=False,later=False):
 """Append a superset endpoint-contract intent (see spending_endpoint_contracts.bind_enablement); offline, no provider call, never while a run is unresolved."""
 state=Path(state);crashed,legacy=unresolved_runs(state)
 if crashed or legacy:raise RecoveryRefused('Unresolved collector run(s) '+', '.join(crashed+legacy)+'; resolve them before re-binding. Nothing was changed.')
 frontier=Frontier(state/'frontier.sqlite',optional_receipts=optional_receipts,include_later_cycle_context=later,enabled_endpoint_contracts=ids,rebind_endpoint_contracts=True)
 try:return frontier.contract_enablement
 finally:frontier.close()

def main():
 if '--migrate-to-schema3' in sys.argv[1:]:return migrate_main(sys.argv[1:])
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--state',required=True,type=Path);parser.add_argument('--seeds',type=Path);parser.add_argument('--max-requests',type=int);parser.add_argument('--max-failed-attempts',type=int,default=8);parser.add_argument('--optional-receipts',action='store_true');parser.add_argument('--include-later-cycle-context',action='store_true');parser.add_argument('--live',action='store_true')
 parser.add_argument('--transport',choices=('local','broker'),default='local',help='local: this process holds the keys and archives itself (the default). broker: a key-free client of the host evidence broker named by MENTAT_REQUEST_POOL_CONFIG (D075).')
 parser.add_argument('--archive-root',type=Path,help='Where responses-<run> lives, as this process sees it (default: --state). Required with --transport broker (the container sees the host evidence root read-only); --recover-crashed-runs reads it too.')
 parser.add_argument('--run-id',help='32 lowercase hex digits; default a new uuid. A launcher names it so the host broker and this process agree.')
 parser.add_argument('--stop-after-seconds',type=float,help='Stop dispatching after this many seconds, drain what is in flight and write the receipt (stop DeadlineReached).')
 parser.add_argument('--enable-endpoint-contract',action='append',default=[],help='Explicit pinned contract ID; repeatable, required on every restart after enablement.')
 parser.add_argument('--workers',type=int,default=1,help='Concurrent fetch workers, 1 to '+str(MAX_WORKERS)+'; the keys hourly allowance is the real ceiling.')
 parser.add_argument('--priority-candidates',type=Path,help='JSON list of {candidate_id,cycle}; their principal campaign committees get their B (and totals) pages first. Persisted in the ledger.')
 parser.add_argument('--ccl',action='append',default=[],metavar='CYCLE=PATH',help='Local candidate-committee linkage file (ccl.txt) for one cycle; repeatable.')
 parser.add_argument('--ccl-vintage',action='append',default=[],metavar='CYCLE=TEXT',help='Optional vintage text for a --ccl file (default: source-receipt.json last_modified beside it, else file mtime).')
 parser.add_argument('--init-new-ledger',action='store_true',help='Permit creating frontier.sqlite when --state has none; without it a missing ledger is refused (a new ledger forks the frontier).')
 parser.add_argument('--recover-crashed-runs',action='store_true',help='Reconcile a run that ended without its receipt; makes no provider call and loads no credentials.')
 parser.add_argument('--rebind-endpoint-contracts',action='store_true',help='Append a new versioned endpoint-contract intent: a superset of the prior IDs bound to this module\'s hash. Offline; removal is never allowed.')
 parser.add_argument('--close-out-legacy-run',type=Path,metavar='RESPONSES_DIR',help='Offline close-out of a pre-D071 run folder (no START): rolls back a hot journal, quarantines unreferenced pairs, writes its receipt.')
 parser.add_argument('--baseline-snapshot',type=Path,help='SNAPSHOT.json of the previous accepted batch (with --close-out-legacy-run).')
 parser.add_argument('--baseline-receipt',type=Path,help='The previous batch receipt that snapshot binds (with --close-out-legacy-run).')
 parser.add_argument('--launched',type=Path,help='The run\'s LAUNCHED.json (with --close-out-legacy-run).')
 parser.add_argument('--bulk-activity-index',type=Path,help='D076: JSON index from build_bulk_activity_index.py. Orders requests (campaign priority, then tier 1 observed / 2 unknown / 3 none observed, then fair turns); never completes, skips, zeroes or creates anything. Persisted per endpoint in the ledger.')
 parser.add_argument('--expect-bulk-index-sha256',help='The pinned sha256 of --bulk-activity-index (required with it; the file must hash to exactly this before it is read).')
 args=parser.parse_args()
 if bool(args.bulk_activity_index)!=bool(args.expect_bulk_index_sha256):parser.error('--bulk-activity-index and --expect-bulk-index-sha256 are given together or not at all.')
 if args.expect_bulk_index_sha256 and not HEX64.fullmatch(args.expect_bulk_index_sha256):parser.error('--expect-bulk-index-sha256 must be 64 lowercase hex digits.')
 if not 1<=args.workers<=MAX_WORKERS:parser.error('--workers must be an integer from 1 to '+str(MAX_WORKERS))
 if args.run_id is not None and not re.fullmatch(r'[0-9a-f]{32}',args.run_id):parser.error('--run-id must be 32 lowercase hex digits')
 if args.stop_after_seconds is not None and not args.stop_after_seconds>0:parser.error('--stop-after-seconds must be positive')
 if args.transport=='broker' and args.archive_root is None:parser.error('--transport broker needs --archive-root')
 if args.transport=='broker' and not args.run_id:parser.error('--transport broker needs --run-id (the broker token is bound to it)')
 ccl={};vintage={}
 for text,target in((args.ccl,ccl),(args.ccl_vintage,vintage)):
  for item in text:
   name,separator,value=item.partition('=')
   if not separator or not name.isdigit()or int(name)not in(2022,2024,2026)or not value:parser.error('CYCLE=VALUE with cycle 2022/2024/2026 required: '+item)
   target[int(name)]=value
 if(ccl or vintage)and not args.priority_candidates:parser.error('--ccl/--ccl-vintage need --priority-candidates.')
 if vintage.keys()-ccl.keys():parser.error('--ccl-vintage given for a cycle without --ccl.')
 ledger=args.state/'frontier.sqlite'
 offline=[name for name,on in(('--recover-crashed-runs',args.recover_crashed_runs),('--rebind-endpoint-contracts',args.rebind_endpoint_contracts),('--close-out-legacy-run',args.close_out_legacy_run is not None))if on]
 if len(offline)>1:parser.error('One offline subcommand at a time: '+', '.join(offline))
 if offline:
  if args.live or args.seeds is not None or args.max_requests is not None or args.priority_candidates or ccl or vintage or args.init_new_ledger or args.workers!=1 or args.transport!='local' or args.run_id or args.stop_after_seconds is not None:parser.error(offline[0]+' takes no --live, --seeds, --max-requests, --workers, --priority-candidates, --ccl, --init-new-ledger, --transport, --run-id or --stop-after-seconds')
  if args.bulk_activity_index:parser.error(offline[0]+' takes no --bulk-activity-index')
  if args.archive_root is not None and not args.recover_crashed_runs:parser.error('--archive-root belongs to a live run and --recover-crashed-runs only')
  if args.close_out_legacy_run is None and(args.baseline_snapshot or args.baseline_receipt or args.launched):parser.error('--baseline-snapshot, --baseline-receipt and --launched belong to --close-out-legacy-run')
  if args.rebind_endpoint_contracts and not args.enable_endpoint_contract:parser.error('--rebind-endpoint-contracts needs the full --enable-endpoint-contract set to bind')
  if args.close_out_legacy_run is not None:
   if not(args.baseline_snapshot and args.baseline_receipt and args.launched):parser.error('--close-out-legacy-run needs --baseline-snapshot, --baseline-receipt and --launched')
   if args.close_out_legacy_run.resolve().parent!=args.state.resolve():parser.error('The responses folder must sit directly in --state')
  if not ledger.is_file():raise SystemExit('No frontier.sqlite in '+str(args.state)+'; an offline subcommand never creates a ledger. Nothing was written.')
  with run_lease(args.state):
   try:
    if args.recover_crashed_runs:print(encoded(recover_crashed_runs(args.state,args.archive_root)))
    elif args.rebind_endpoint_contracts:print(encoded(rebind_run(args.state,ids=args.enable_endpoint_contract,optional_receipts=args.optional_receipts,later=args.include_later_cycle_context)))
    else:print(encoded(close_out_legacy_run(args.close_out_legacy_run,baseline_snapshot=args.baseline_snapshot,baseline_receipt=args.baseline_receipt,launched=args.launched,state=args.state)))
   except(RecoveryRefused,EndpointContractRefused)as error:raise SystemExit(str(error))from None
  return
 if args.baseline_snapshot or args.baseline_receipt or args.launched:parser.error('--baseline-snapshot, --baseline-receipt and --launched belong to --close-out-legacy-run')
 if args.seeds is None or args.max_requests is None:parser.error('--seeds and --max-requests are required')
 if not args.live:raise SystemExit('No implicit provider calls. Review the seed/coverage plan and pass --live only after authorization.')
 if not ledger.exists()and not args.init_new_ledger:raise SystemExit('No frontier.sqlite in '+str(args.state)+'; refusing to create a new ledger (a new ledger forks the frontier). Pass --init-new-ledger to start one on purpose. Nothing was written.')
 if ledger.exists()and args.init_new_ledger:raise SystemExit('--init-new-ledger was given but '+str(ledger)+' exists; an existing ledger is never re-initialised. Nothing was written.')
 remote=None
 if args.transport=='broker':
  # Before ANYTHING is written (not even the state folder): the descriptor must be this run's, its families must cover what this run will request, the mounted tree must be
  # the one the broker imported, and the broker's recorded spelling must be the one this process's map reads back.
  from api_request_pool import RemoteEvidenceTransport
  try:remote=RemoteEvidenceTransport.from_environment(run_id=args.run_id,max_requests=args.max_requests,workers=args.workers)
  except ValueError as error:raise SystemExit(str(error)+' Nothing was written.')from None
  ids=activation_plan([],args.enable_endpoint_contract)['enabled_contract_ids']
  needed={'B','E'}|{kind for kind,value in CONTRACTS.items()if value['contract_id']in ids}|({'A'}if args.optional_receipts else set())
  if not needed<=set(remote.families):raise SystemExit('The broker descriptor does not admit family '+', '.join(sorted(needed-set(remote.families)))+' which this run needs. Nothing was written.')
  if stored_paths.pairs()and not stored_paths.same(stored(args.archive_root),remote.descriptor['stored_root']):raise SystemExit('The broker records archives as '+remote.descriptor['stored_root']+' but MENTAT_STORED_PATH_MAP spells the archive root '+stored(args.archive_root)+'; recorded paths would not read back. Nothing was written.')
 bulk_index=None
 if args.bulk_activity_index:
  try:bulk_index=load_bulk_activity_index(args.bulk_activity_index,args.expect_bulk_index_sha256)
  except(OSError,ValueError)as error:raise SystemExit('Bulk activity index refused: '+str(error)+' Nothing was written.')from None
 from fec_accounted_client import AccountedTransport
 args.state.mkdir(parents=True,exist_ok=True)
 with run_lease(args.state):
  crashed,legacy=unresolved_runs(args.state,args.archive_root)
  if crashed or legacy:raise SystemExit('Unresolved collector run(s) '+', '.join(crashed+legacy)+': run --recover-crashed-runs (START present) or --close-out-legacy-run (no START, after a human review). Nothing was written.')
  run_id=args.run_id or uuid.uuid4().hex;archive=(args.archive_root or args.state)/('responses-'+run_id);start_path=args.state/('RUN-'+run_id+'.START.json')
  if args.run_id and any(item.exists()for item in(start_path,args.state/('RECEIPT-'+run_id+'.json'),archive)):raise SystemExit('Run id '+run_id+' was used before; a run id is never reused. Nothing was written.')
  rows,seed_receipt,seed_bytes=captured_seeds(args.seeds)
  seed_receipt={**seed_receipt,'path':stored(seed_receipt['path'])}
  copy=args.state/('seeds-'+seed_receipt['sha256']+'.json')
  if copy.exists():
   if copy.read_bytes()!=seed_bytes:raise ValueError('Captured seed copy conflicts with its fingerprint.')
  else:
   with copy.open('xb')as stream:stream.write(seed_bytes);stream.flush();os.fsync(stream.fileno())
  frontier=Frontier(ledger,optional_receipts=args.optional_receipts,include_later_cycle_context=args.include_later_cycle_context,enabled_endpoint_contracts=args.enable_endpoint_contract)
  try:
   frontier.run_id=run_id
   frontier.seed(rows)
   priority_file=None
   if args.priority_candidates:
    entries,priority_file=load_priority_candidates(args.priority_candidates)
    frontier.set_priority(entries,ccl={cycle:{'path':path,'vintage':vintage.get(cycle)or ccl_vintage(path)}for cycle,path in ccl.items()})
   if bulk_index is not None:frontier.set_bulk_index(bulk_index)
   pages,total=frontier.db.execute('SELECT (SELECT COUNT(*) FROM pages),(SELECT COALESCE(SUM(attempt_count),0) FROM endpoints)').fetchone()
   base_lookups=frontier.db.execute('SELECT COALESCE(SUM(http_attempts),0) FROM candidate_committee_lookups').fetchone()[0]if frontier.extensions_ready else 0
   # Written before the transport exists, so a response folder never exists without it; never rewritten.
   write_new(start_path,{'schema_version':1,'purpose':'collector-run-start','run_id':run_id,'started_utc':now(),'collector_sha256':sha(__file__),'adapter_sha256':sha(sys.modules[AccountedTransport.__module__].__file__),
    'workers':args.workers,'max_requests':args.max_requests,'max_failed_attempts':args.max_failed_attempts,'later_cycle_context':args.include_later_cycle_context,'optional_receipts':args.optional_receipts,'enabled_endpoint_contracts':args.enable_endpoint_contract,
    'priority_candidates_file':priority_file,**({'bulk_activity_index':bulk_index.receipt}if bulk_index is not None else{}),'init_new_ledger':args.init_new_ledger,'seed_receipt':{**seed_receipt,'captured_copy':stored(copy)},'ledger_at_start':{'pages':pages,'sum_attempt_count':total,'sum_lookup_http_attempts':base_lookups},
    **({'transport':remote.receipt_info()}if remote else{})})
   with(remote.session(start_path,sha(__file__))if remote else AccountedTransport.live(archive,args.max_requests))as transport:
    lookup=frontier.fill_priority_via_api(transport)if args.priority_candidates else None
    report=frontier.run(transport,max_failed_attempts=args.max_failed_attempts,workers=args.workers,progress=lambda snapshot:print(encoded(snapshot),file=sys.stderr,flush=True),initial_stop=lookup['stopped']if lookup else None,stop_after_seconds=args.stop_after_seconds)
    recorded=frontier.db.execute('SELECT COALESCE(SUM(attempt_count),0) FROM endpoints').fetchone()[0]-total;lookups=lookup['http_attempts']if lookup else 0
    # Every physical attempt is either a dispatched endpoint attempt (outcomes AND endpoints.attempt_count agree) or a candidate-committee lookup page.
    if report['attempts_recorded']+lookups!=transport.http_attempts or recorded!=report['attempts_recorded']:raise RuntimeError('The ledger did not record every physical attempt (transport '+str(transport.http_attempts)+', outcomes '+str(report['attempts_recorded'])+', lookup pages '+str(lookups)+', endpoint attempt_count delta '+str(recorded)+'); no receipt written. Run --recover-crashed-runs.')
    report.update(http_attempts=transport.http_attempts,physical_request_cap=transport.maximum,failed_attempt_allowance=args.max_failed_attempts,raw_archive_directory=remote.archive_stored if remote else str(archive),endpoint_attempts_recorded=recorded,candidate_committee_lookup_http_attempts=lookups)
    if remote:report.update(transport=remote.receipt_info())
    if args.priority_candidates:report.update(priority_candidates_file=priority_file,candidate_committee_api_lookup=lookup)
    if bulk_index is not None:report.update(bulk_activity_index=bulk_index.receipt)
   report.update(ledger_sha256=sha(frontier.path),seed_receipt={**seed_after(seed_receipt),'captured_copy':stored(copy)})
   output=args.state/('RECEIPT-'+run_id+'.json');write_new(output,report)
   print(encoded({'receipt':str(output),'http_attempts':report['http_attempts'],'exhaustive_complete':False}))
  finally:frontier.close()

if __name__=='__main__':main()
