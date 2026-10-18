"""Inert archives/SQLite only: no credentials, providers or product files."""
import hashlib,json,sqlite3,sys,tempfile,unittest
from decimal import Decimal
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from collect_committee_activity import Frontier,captured_seeds,seed_after
import origin_closure

C1='C00000001';C2='C00000002';C3='C00000003';C4='C00000004'
class FECAdmissionRefused(ValueError):pass
class Transport:
 def __init__(self,root,handler,cap=10000):self.root=Path(root);self.root.mkdir();self.handler=handler;self.cap=cap;self.http_attempts=0;self.records=[];self.calls=[]
 def fetch(self,endpoint,query,exact_decimal=False):
  assert exact_decimal
  if self.http_attempts>=self.cap:raise FECAdmissionRefused('inert physical request cap')
  self.http_attempts+=1;self.calls.append((endpoint,dict(query)));data=self.handler(endpoint,query)
  body=json.dumps(data,default=str).encode();path=self.root/(str(self.http_attempts)+'.bin');path.write_bytes(body)
  record={'path':str(path),'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body),'status':200,'endpoint':endpoint,'query':dict(query),'original_response_bytes':True};self.records.append(record)
  return data,record
def result(rows=(),count=None,next_cursor=None,**pagination):
 return {'results':list(rows),'pagination':{'count':len(rows)if count is None else count,'is_count_exact':True,**({'last_indexes':next_cursor}if next_cursor else {}),**pagination}}
def empty(endpoint,query):return result()
def derived(frontier):
 """D073 adaptation: schema 3 stores no per-unit origins; these are the (committee,cycle,origin_id,metadata_json) rows schema 2 stored, rebuilt from the catalog and the derived closure."""
 closure=frontier.closure();catalog=origin_closure.read_catalog(frontier.db);rows=[]
 for unit in sorted(closure.units):
  for ref in closure.origins(unit):rows.append((unit[0],unit[1],catalog[ref][0],origin_closure.metadata_json(catalog[ref][1],catalog[ref][2],unit[1])))
 return rows
class Tests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.frontier=Frontier(self.root/'frontier.sqlite')
 def tearDown(self):self.frontier.close();self.tmp.cleanup()
 def seed(self,**extra):self.frontier.seed([{'committee_id':C1,'cycle':2026,'origins':['industryA','industryB'],**extra}])
 def test_signed_zero_candidate_edges_and_cycle_origins_close_without_type_filter(self):
  self.seed()
  def handler(e,q):
   if e.endswith('schedule_b/')and q['committee_id']==C1:return result([{'sub_id':'9007199254740993123','recipient_committee_id':C2,'disbursement_amount':Decimal('12.34'),'candidate_id':'H0AA00001'},{'sub_id':'2','recipient_committee_id':C3,'disbursement_amount':Decimal('-12.34')},{'sub_id':'3','recipient_committee_id':C4,'disbursement_amount':Decimal('0.00')}])
   if e.endswith('schedule_b/')and q['committee_id']==C2:return result([{'sub_id':'4','recipient_committee_id':C1,'disbursement_amount':Decimal('1.00')}])
   return result()
  transport=Transport(self.root/'raw',handler);proof=self.frontier.run(transport)
  self.assertEqual(proof['units'],4);self.assertEqual(proof['edges'],4);self.assertEqual(proof['origin_seed_assignments'],2);self.assertEqual(proof['origin_unit_assignments'],8);self.assertEqual((proof['closure_basis'],proof['lower_bound']),('collected_edges_only',True));self.assertNotIn('origins',proof);self.assertTrue(proof['supported_outgoing_endpoints_complete']);self.assertFalse(proof['exhaustive_disclosed_spending_complete'])
  self.assertEqual(self.frontier.db.execute("SELECT amount_text FROM observations WHERE source_id='9007199254740993123'").fetchone()[0],'12.34')
 def test_request_cap_resume_never_means_zero_and_outgoing_independent_A(self):
  self.frontier.close();self.frontier=Frontier(self.root/'frontier.sqlite',optional_receipts=True);self.seed()
  first=Transport(self.root/'raw1',empty,cap=1);proof=self.frontier.run(first)
  self.assertFalse(proof['supported_outgoing_endpoints_complete']);self.assertEqual(first.http_attempts,1)
  second=Transport(self.root/'raw2',empty,cap=2);proof=self.frontier.run(second)
  self.assertTrue(proof['supported_outgoing_endpoints_complete']);self.assertFalse(proof['legacy_snapshot_compatible']);self.assertEqual(proof['endpoint_status_counts']['A:pending'],1)
  self.assertTrue(all(not e.endswith('schedule_a/')for e,q in first.calls+second.calls))
 def test_duplicate_source_id_and_changed_count_retain_pages_without_certificate(self):
  for mode in ('duplicate','count'):
   with self.subTest(mode=mode):
    file=self.root/(mode+'.sqlite');f=Frontier(file);f.seed([{'committee_id':C1,'cycle':2026}])
    def handler(e,q):
     if not e.endswith('schedule_b/'):return result()
     if 'last_index'not in q:return result([{'sub_id':'1','disbursement_amount':1}],count=2,next_cursor={'last_index':1})
     return result([{'sub_id':'1'if mode=='duplicate'else'2','disbursement_amount':2}],count=2 if mode=='duplicate' else 3)
    proof=f.run(Transport(self.root/('raw-'+mode),handler));self.assertEqual(proof['endpoint_status_counts']['B:closed_unknown'],1);self.assertEqual(proof['observations'],2);self.assertFalse(proof['supported_outgoing_endpoints_complete']);f.close()
 def test_invalid_amount_retained_and_clear_committee_still_discovered(self):
  self.seed()
  def handler(e,q):return result([{'sub_id':'1','recipient_committee_id':C2,'disbursement_amount':'1.00'},{'sub_id':'2','disbursement_amount':'notmoney'}])if e.endswith('schedule_b/')and q['committee_id']==C1 else result()
  proof=self.frontier.run(Transport(self.root/'raw',handler));self.assertEqual(proof['observations'],2);self.assertEqual(proof['edges'],1);self.assertEqual(proof['units'],2);self.assertEqual(self.frontier.db.execute("SELECT amount_status FROM observations WHERE source_id='2'").fetchone()[0],'invalid')
 def test_inexact_count_preserves_clear_edges_until_cursor_closure(self):
  self.seed()
  def handler(e,q):
   if e.endswith('schedule_b/')and q['committee_id']==C1:
    if 'last_index'not in q:
     data=result([{'sub_id':'1','recipient_committee_id':C2,'disbursement_amount':0}],count=999,next_cursor={'last_index':1});data['pagination']['is_count_exact']=False;return data
    data=result(count=999);data['pagination']['is_count_exact']=False;return data
   return result()
  proof=self.frontier.run(Transport(self.root/'raw',handler));self.assertEqual(proof['units'],2);self.assertEqual(proof['edges'],1);self.assertEqual(proof['observations'],1);self.assertEqual(proof['endpoint_status_counts']['B:closed_unknown'],1);self.assertFalse(proof['supported_outgoing_endpoints_complete'])
 def test_IE_beneficiary_H_id_never_becomes_payment_resolution(self):
  self.seed()
  def handler(e,q):return result([{'sub_id':'1','candidate_id':'H0AA00001','expenditure_amount':1}])if e.endswith('schedule_e/')and q['is_notice']=='false'else result()
  proof=self.frontier.run(Transport(self.root/'raw',handler));self.assertEqual(proof['candidate_resolutions_required'],0);self.assertEqual(proof['units'],1);self.assertFalse(proof['exhaustive_disclosed_spending_complete']);self.assertEqual(self.frontier.db.execute('SELECT role FROM candidate_references').fetchone()[0],'supported_or_opposed_beneficiary_not_paid_counterparty')
 def test_unknown_schema_and_coerced_cycles_are_refused(self):
  for cycle in (True,'2026',2026.0,2028):
   with self.assertRaises(ValueError):self.frontier.seed([{'committee_id':C1,'cycle':cycle}])
  bad=self.root/'unknown.sqlite';c=sqlite3.connect(bad);c.execute('CREATE TABLE unrelated(x)');c.close()
  with self.assertRaises(ValueError):Frontier(bad)
 def test_explicit_later_context_preserves_origin_cycle_and_no_backwards_edge(self):
  self.frontier.close();self.frontier=Frontier(self.root/'later-context.sqlite',include_later_cycle_context=True)   # D073: the policy is recorded when a ledger is created, so a different policy needs a new ledger
  self.frontier.seed([{'committee_id':C1,'cycle':2024,'origins':['Rust2024']}]);self.assertEqual(self.frontier.report()['units'],2)
  self.assertEqual({r[0]for r in self.frontier.db.execute('SELECT cycle FROM units')},{2024,2026})
  metadata=[json.loads(r[3])for r in derived(self.frontier)];self.assertTrue(all(m['first_reached_cycle']==2024 for m in metadata));self.assertEqual(sum(m['later_cycle_context']for m in metadata),1)
  self.assertEqual(self.frontier.report()['edges'],0)
 def test_new_origin_propagates_through_already_collected_cycle(self):
  self.seed()
  def handler(e,q):return result([{'sub_id':'1','recipient_committee_id':C2,'disbursement_amount':1}])if e.endswith('schedule_b/')and q['committee_id']==C1 else result()
  self.frontier.run(Transport(self.root/'raw',handler));self.frontier.seed([{'committee_id':C1,'cycle':2026,'origins':['industryC']}]);self.assertEqual(len([r for r in derived(self.frontier)if r[0]==C2]),3)
 def test_more_than_1000_pages_and_regular_notice_never_combine(self):
  self.seed()
  def handler(e,q):
   if e.endswith('schedule_b/'):
    n=int(q.get('page',1));return result([{'sub_id':str(n),'disbursement_amount':Decimal('0.01')}],count=1001,page=n,pages=1001)
   if e.endswith('schedule_e/'):return result([{'sub_id':'same-id-in-independent-E-sets','expenditure_amount':Decimal('1.01')}])
   return result()
  proof=self.frontier.run(Transport(self.root/'raw',handler));self.assertTrue(proof['supported_outgoing_endpoints_complete']);self.assertEqual(proof['pages'],1003);self.assertEqual(proof['observations'],1003);self.assertTrue(proof['no_B_E_grand_total'])
 def test_one_writer_owns_admission_fetch_and_checkpoint(self):
  self.seed();other=Frontier(self.root/'frontier.sqlite');calls=[]
  try:
   with self.frontier.transaction():
    with self.assertRaises(sqlite3.OperationalError):other.run(Transport(self.root/'raw-other',empty))
   self.assertEqual(other.report()['pages'],0)
  finally:other.close()
 def test_empty_exact_endpoint_is_verified_zero_but_A_F_H4_are_not(self):
  self.seed();proof=self.frontier.run(Transport(self.root/'raw',empty));self.assertTrue(proof['supported_outgoing_endpoints_complete']);self.assertEqual(proof['observations'],0);self.assertEqual(proof['pages'],3);self.assertEqual(proof['endpoint_status_counts']['F:unsupported'],1);self.assertEqual(proof['endpoint_status_counts']['H4:unsupported'],1)
 def test_transport_interruption_after_archive_retains_unknown_then_resume(self):
  self.seed();transport=Transport(self.root/'raw1',empty)
  original=transport.fetch
  def interrupted(*a,**k):original(*a,**k);raise OSError('inert interruption after durable archive')
  transport.fetch=interrupted;proof=self.frontier.run(transport);self.assertEqual(proof['pages'],0);self.assertFalse(proof['supported_outgoing_endpoints_complete']);self.assertEqual(len(transport.records),3)
  self.frontier.db.execute('UPDATE endpoints SET retry_after=0')
  proof=self.frontier.run(Transport(self.root/'raw2',empty));self.assertTrue(proof['supported_outgoing_endpoints_complete'])
 def test_nested_payee_C_and_source_committee_object_not_confused(self):
  self.seed()
  def handler(e,q):return result([{'sub_id':'1','recipient_committee':{'committee_id':C2},'committee':{'committee_id':C3},'disbursement_amount':0}])if e.endswith('schedule_b/')and q['committee_id']==C1 else result()
  proof=self.frontier.run(Transport(self.root/'raw',handler));self.assertEqual(proof['units'],2);self.assertEqual({r[0]for r in self.frontier.db.execute('SELECT committee_id FROM units')},{C1,C2})
 def test_conflicting_flat_nested_payee_ids_are_possible_not_two_payments(self):
  self.seed()
  def handler(e,q):return result([{'sub_id':'1','recipient_committee_id':C2,'recipient_committee':{'committee_id':C3},'disbursement_amount':1}])if e.endswith('schedule_b/')and q['committee_id']==C1 else result()
  proof=self.frontier.run(Transport(self.root/'raw',handler));self.assertEqual(proof['units'],3);self.assertEqual(proof['observations'],1);self.assertEqual(proof['edges'],2);self.assertEqual(self.frontier.db.execute('SELECT COUNT(*) FROM counterparty_conflicts').fetchone()[0],1);self.assertTrue(all('not_two_payments'in r[0]for r in self.frontier.db.execute('SELECT identity_status FROM edges')))
 def test_timeout_endpoint_defers_without_starving_other_units_or_retrying_first(self):
  self.frontier.seed([{'committee_id':C1,'cycle':2026},{'committee_id':C2,'cycle':2026}])
  def handler(e,q):
   if e.endswith('schedule_b/')and q['committee_id']==C1:raise TimeoutError('inert endpoint timeout')
   return result()
  first=Transport(self.root/'raw1',handler);proof=self.frontier.run(first);self.assertEqual(proof['pages'],5);self.assertEqual(proof['endpoint_status_counts']['B:blocked'],1);self.assertEqual(first.http_attempts,6)
  second=Transport(self.root/'raw2',empty);self.frontier.run(second);self.assertEqual(second.http_attempts,0,'cooldown persists across resume')
  self.frontier.db.execute('UPDATE endpoints SET retry_after=0');proof=self.frontier.run(Transport(self.root/'raw3',empty));self.assertTrue(proof['supported_outgoing_endpoints_complete'])
 def test_count_matched_malformed_cursor_never_complete(self):
  self.seed()
  def handler(e,q):
   if e.endswith('schedule_b/'):
    data=result([{'sub_id':'1','disbursement_amount':1}]);data['pagination']['last_indexes']='malformed';return data
   return result()
  proof=self.frontier.run(Transport(self.root/'raw',handler));self.assertEqual(proof['endpoint_status_counts']['B:closed_unknown'],1);self.assertFalse(proof['supported_outgoing_endpoints_complete'])
 def test_late_origin_propagates_existing_multi_hop_and_later_cycles(self):
  self.frontier.close();self.frontier=Frontier(self.root/'later-context.sqlite',include_later_cycle_context=True)   # D073: the policy is recorded when a ledger is created, so a different policy needs a new ledger
  self.frontier.seed([{'committee_id':C1,'cycle':2022,'origins':['old']}])
  def handler(e,q):
   target={C1:C2,C2:C3}.get(q['committee_id'])
   return result([{'sub_id':'1','recipient_committee_id':target,'disbursement_amount':1}])if e.endswith('schedule_b/')and q['two_year_transaction_period']==2022 and target else result()
  self.frontier.run(Transport(self.root/'raw',handler));self.frontier.seed([{'committee_id':C1,'cycle':2022,'origins':['late']}])
  rows=[(r[1],r[3])for r in derived(self.frontier)if r[0]==C3 and r[2]=='late'];self.assertEqual({r[0]for r in rows},{2022,2024,2026})
  self.assertTrue(all(json.loads(r[1])['activity_cycle']==r[0]and json.loads(r[1])['first_reached_cycle']==2022 for r in rows))
 def test_seed_bytes_hash_capture_survives_file_mutation(self):
  p=self.root/'seed.json';p.write_text(json.dumps({'schema_version':1,'seed_units':[{'committee_id':C1,'cycle':2026}]}));rows,receipt,raw=captured_seeds(p);expected=hashlib.sha256(raw).hexdigest()
  p.write_text(json.dumps({'schema_version':1,'seed_units':[{'committee_id':C2,'cycle':2026}]}));after=seed_after(receipt)
  self.assertEqual(rows[0]['committee_id'],C1);self.assertEqual(after['sha256'],expected);self.assertFalse(after['after_unchanged'])


 def test_same_origin_earliest_cycle_is_order_independent_and_propagates_late(self):
  results=[]
  for label,cycles in [('later-first',(2024,2022)),('earlier-first',(2022,2024))]:
   f=Frontier(self.root/(label+'.sqlite'),include_later_cycle_context=True)
   for cycle in cycles:f.seed([{'committee_id':C1,'cycle':cycle,'origins':['same']}])
   results.append(([(r[0],r[1],r[3])for r in derived(f)],[tuple(r)for r in f.db.execute('SELECT origin_id,base_metadata_json,first_reached_cycle FROM origin_catalog')]));f.close()
  self.assertEqual(results[0],results[1])
  self.frontier.close();self.frontier=Frontier(self.root/'later-context.sqlite',include_later_cycle_context=True)   # D073: the policy is recorded when a ledger is created, so a different policy needs a new ledger
  self.frontier.seed([{'committee_id':C1,'cycle':2024,'origins':['same']}])
  def handler(e,q):
   target={C1:C2,C2:C3}.get(q['committee_id'])
   return result([{'sub_id':'1','recipient_committee_id':target,'disbursement_amount':1}])if e.endswith('schedule_b/')and q['two_year_transaction_period']==2024 and target else result()
  self.frontier.run(Transport(self.root/'late-first-raw',handler));before=[tuple(r)for r in self.frontier.db.execute('SELECT * FROM observations ORDER BY committee_id,cycle,kind,observation_id')]
  self.frontier.seed([{'committee_id':C1,'cycle':2022,'origins':['same']}])
  rows=[(r[1],r[3])for r in derived(self.frontier)if r[0]==C3 and r[2]=='same'];self.assertEqual({r[0]for r in rows},{2024,2026})
  self.assertTrue(all(json.loads(r[1])['first_reached_cycle']==2022 and json.loads(r[1])['activity_cycle']==r[0]and json.loads(r[1])['later_cycle_context']for r in rows))
  self.assertEqual(before,[tuple(r)for r in self.frontier.db.execute('SELECT * FROM observations ORDER BY committee_id,cycle,kind,observation_id')])

if __name__=='__main__':unittest.main()
