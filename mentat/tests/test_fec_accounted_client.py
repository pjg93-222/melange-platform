"""Offline original Client + shared admission + original-byte transport controls."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
# Staged fixture uses real, unchanged production dependencies after staged tools.
if not (TOOLS/'pac_evidence.py').exists():
    sys.path.append(r'C:\Users\pgoff\Mentat\mentat\tools')
import fec_accounted_client as accounted
from api_request_budget import budget_id, legacy_budget_id
import hydrate_receipt_metadata as hydration
import pac_evidence

KEY = 'inert-D067-synthetic-key-not-a-provider-credential'
A = 'schedules/schedule_a/'
B = 'schedules/schedule_b/'
PARAMS = {'committee_id':'C00000001', 'two_year_transaction_period':2024}


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status, self.headers = status, headers or {'Content-Type':'application/json'}


class Opener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request.full_url, timeout, request.get_header('X-api-key')))
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return Response(*value)


def body(value):
    return json.dumps(value, separators=(',', ':')).encode()


def page(rows, count=None, indexes=None):
    return {'results':rows, 'pagination':{'is_count_exact':True,
            'count':len(rows) if count is None else count, 'last_indexes':indexes or {}}}


class AccountedClientTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='mentat-D067-inert-')
        self.root = Path(self.directory.name)
        self.sequence = 0
        self.guards = [patch('fec_key_store.load_keys', side_effect=AssertionError('credentials forbidden')),
                       patch('pac_evidence.load_keys', side_effect=AssertionError('credentials forbidden')),
                       patch('pac_evidence.urlopen', side_effect=AssertionError('direct network forbidden')),
                       patch('pac_evidence.time.sleep', return_value=None)]
        for guard in self.guards:
            guard.start()

    def tearDown(self):
        for guard in reversed(self.guards):
            guard.stop()
        self.directory.cleanup()

    def transport(self, responses, maximum=20, cap=1000, ledger=None, legacy=()):
        self.sequence += 1
        opener = Opener(responses)
        value = accounted.AccountedTransport([KEY], ledger or self.root/'quota.sqlite',
            self.root/f'archive-{self.sequence}', maximum, opener=opener, cap=cap, legacy_paths=legacy)
        self.addCleanup(value.close)
        return value, opener

    def client(self, transport, name='cache'):
        return pac_evidence.Client(self.root/name, key='transport-owned', transport=transport)

    def used(self, ledger=None):
        with closing(sqlite3.connect(ledger or self.root/'quota.sqlite')) as db:
            return db.execute('SELECT requests_used FROM api_request_budget WHERE budget_id=?',
                              (budget_id(KEY),)).fetchone()[0]

    def assert_archives(self, transport, payloads):
        self.assertEqual(len(transport.records), len(payloads))
        for record, raw in zip(transport.records, payloads):
            self.assertEqual(Path(record['path']).read_bytes(), raw)
            self.assertEqual(record['sha256'], hashlib.sha256(raw).hexdigest())
            saved = json.loads(Path(record['receipt_path']).read_bytes())
            self.assertEqual(saved['sha256'], record['sha256'])
            self.assertTrue(saved['original_response_bytes'])
            self.assertNotIn(KEY, json.dumps(saved))

    def test_original_client_retry_charges_each_attempt_and_keeps_cache(self):
        payloads = [b'{"error":"temporary"}', b'{"error":"temporary2"}', body(page([]))]
        transport, opener = self.transport([(payloads[0],503),(payloads[1],500),(payloads[2],200)])
        client = self.client(transport)
        self.assertEqual(client.page(A, PARAMS), page([]))
        self.assertEqual((transport.http_attempts, len(opener.calls), self.used()), (3,3,3))
        self.assert_archives(transport, payloads)
        self.assertEqual(client.page(A, PARAMS), page([]))
        self.assertEqual(len(opener.calls), 3)
        # A valid existing parsed cache requires no provider attempt or new raw claim.
        second, other = self.transport([], maximum=0)
        self.assertEqual(self.client(second).page(A, PARAMS), page([]))
        self.assertEqual((second.http_attempts, len(other.calls), second.records), (0,0,[]))

    def test_run_cap_and_shared_quota_are_nonretryable(self):
        for reason in ('run','shared'):
            with self.subTest(reason=reason):
                ledger = self.root/f'{reason}.sqlite'
                transport, opener = self.transport([(b'{}',503)]*4,
                    maximum=2 if reason=='run' else 20, cap=2 if reason=='shared' else 1000, ledger=ledger)
                with self.assertRaises(accounted.FECAdmissionRefused):
                    self.client(transport, reason).page(A, PARAMS)
                self.assertEqual((transport.http_attempts, len(opener.calls), self.used(ledger)),(2,2,2))
                self.assertFalse(list((self.root/reason).glob('*.json')))

    def test_legacy_import_and_parallel_admission_never_grant_extra_capacity(self):
        legacy = self.root/'old.sqlite'
        with closing(sqlite3.connect(legacy)) as db, db:
            db.execute('CREATE TABLE api_request_budget(budget_id TEXT PRIMARY KEY,window_started_epoch REAL,requests_used INTEGER)')
            db.execute('INSERT INTO api_request_budget VALUES(?,?,?)', (legacy_budget_id(KEY),accounted.time.time(),2))
        original = legacy.read_bytes()
        first, first_opener = self.transport([(b'{}',200)]*5, cap=5, legacy=(legacy,))
        second, second_opener = self.transport([(b'{}',200)]*5, cap=5, legacy=(legacy,))
        start = threading.Barrier(2)
        def fetch_until_refused(transport):
            start.wait()
            while True:
                try: transport.fetch(B, PARAMS)
                except accounted.FECAdmissionRefused: return
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(fetch_until_refused, (first,second)))
        self.assertEqual(len(first_opener.calls)+len(second_opener.calls),3)
        self.assertEqual(self.used(),5)
        self.assertEqual(legacy.read_bytes(),original)

    def test_archive_failure_and_secret_echo_refuse_before_cache_or_parse(self):
        transport, opener = self.transport([(b'not JSON',200)])
        with patch.object(accounted,'durable_new',side_effect=OSError('inert storage refusal')):
            with self.assertRaises(accounted.FECArchiveFailed): self.client(transport).page(A,PARAMS)
        self.assertEqual((len(opener.calls), self.used()),(1,1))
        self.assertFalse(list((self.root/'cache').glob('*.json')))
        for name, raw, headers in [('body',body({'echo':KEY}),{}),
                                    ('header',b'{}',{'ETag':KEY})]:
            with self.subTest(name=name):
                other, calls = self.transport([(raw,200,headers)])
                with self.assertRaises(accounted.FECArchiveFailed): self.client(other,name).page(A,PARAMS)
                self.assertEqual(len(calls.calls),1)
                self.assertFalse(list(other.archive.glob('*.body.bin')))
                for path in other.archive.iterdir():
                    self.assertNotIn(KEY.encode(),path.read_bytes())

    def test_original_client_pagination_preserves_ids_signs_and_refuses_incomplete(self):
        rows = [{'sub_id':'9999999999999999991','disbursement_amount':-12.5},
                {'sub_id':'9999999999999999992','disbursement_amount':0},
                {'sub_id':'9999999999999999993','disbursement_amount':10.25}]
        payloads = [body(page(rows[:2],3,{'last_index':'9999999999999999992'})),body(page(rows[2:],3))]
        transport, opener = self.transport([(raw,200) for raw in payloads])
        self.assertEqual(self.client(transport).rows(B,PARAMS),rows)
        self.assertIn('last_index=9999999999999999992',opener.calls[1][0])
        self.assert_archives(transport,payloads)
        broken, calls = self.transport([(payloads[0],200),(body(page(rows[2:],4)),200)])
        with self.assertRaisesRegex(ValueError,'count changed'):
            self.client(broken,'broken').rows(B,PARAMS)
        self.assertEqual((len(calls.calls),len(broken.records)),(2,2))
        decimal, _ = self.transport([(b'{"amount":-12.50,"sub_id":"9999999999999999991"}',200)])
        parsed, _ = decimal.fetch(B,PARAMS,exact_decimal=True)
        self.assertEqual(parsed['amount'],Decimal('-12.50'))
        self.assertIsInstance(parsed['amount'],Decimal)
        self.assertIsInstance(parsed['sub_id'],str)

    def test_no_redirect_or_credential_query_and_connection_attempt_is_charged(self):
        transport, opener = self.transport([(b'{}',302),URLError('inert socket failure')])
        with self.assertRaises(HTTPError) as result: transport.fetch(B,PARAMS)
        self.assertEqual(result.exception.code,302)
        with self.assertRaises(URLError): transport.fetch(B,PARAMS)
        self.assertEqual((len(opener.calls),self.used()),(2,2))
        self.assertEqual(len(transport.records),1)
        for endpoint, params in [('https://untrusted.invalid/',PARAMS),(A,{'api_key':KEY})]:
            with self.assertRaises(ValueError): transport.fetch(endpoint,params)
        self.assertEqual(len(opener.calls),2)
        self.assertIsNone(accounted.NoRedirect().redirect_request(None,None,302,'',{},'https://untrusted.invalid/'))

    def test_official_spending_endpoint_allowlist_and_parse_failure_retention(self):
        contracts = [
            ('schedules/schedule_f/',{'committee_id':'C00000001','cycle':2024,'page':2},'expenditure_amount'),
            ('schedules/schedule_h4/',{'committee_id':'C00000001','cycle':2024,'last_index':'9999999999999999991'},'disbursement_amount'),
            ('communication_costs/',{'committee_id':'C00000001','min_date':'2023-01-01','max_date':'2024-12-31','page':2},'transaction_amount'),
            ('electioneering/',{'committee_id':'C00000001','report_year':[2023,2024]},'disbursement_amount'),
            ('schedules/schedule_e/',{'committee_id':'C00000001','cycle':2024,'is_notice':'true'},'expenditure_amount')]
        for endpoint, params, field in contracts:
            with self.subTest(endpoint=endpoint):
                raw = ('{"'+field+'":-12.50,"sub_id":"9999999999999999991"}').encode()
                transport, opener = self.transport([(raw,200)])
                parsed, record = transport.fetch(endpoint,params,exact_decimal=True)
                self.assertEqual(parsed[field],Decimal('-12.50'))
                self.assertEqual(record['query'],params)
                self.assertEqual(len(opener.calls),1)
        invalid, opener = self.transport([(b'not-json-original',200)])
        with self.assertRaisesRegex(ValueError,'not JSON'): self.client(invalid,'invalid').page(A,PARAMS)
        self.assertEqual(len(opener.calls),1)
        self.assert_archives(invalid,[b'not-json-original'])
        self.assertFalse(list((self.root/'invalid').glob('*.json')))
        for params in ({'api_token':'inert'},{'memo':KEY}):
            with self.assertRaises(ValueError): invalid.fetch(A,params)
        self.assertEqual(len(opener.calls),1)
        special = 'inert/credential?with "escapes" and Unicode \u03a9'
        guarded = accounted.AccountedTransport([special],self.root/'special.sqlite',self.root/'special',1,opener=Opener([]))
        self.addCleanup(guarded.close)
        with self.assertRaises(ValueError): guarded.fetch(A,{'memo':[special]})
        self.assertEqual((guarded.http_attempts,list(guarded.archive.iterdir())),(0,[]))
        for invalid_cap in (True,0,-1,1.5):
            with self.assertRaises(ValueError):
                accounted.AccountedTransport([KEY],self.root/'invalid.sqlite',self.root/'never-created',1,cap=invalid_cap)

    def test_actual_hydration_private_copy_preserves_financial_facts(self):
        source = self.root/'source.sqlite'
        columns = ('sub_id TEXT,committee_id TEXT,contributor_name TEXT,contribution_receipt_amount REAL,'
                   'contribution_receipt_date TEXT,company TEXT,company_match_status TEXT,is_memo_leg INTEGER,')
        with closing(sqlite3.connect(source)) as db, db:
            db.execute('CREATE TABLE contributions('+columns+'two_year_transaction_period INTEGER)')
            db.execute('CREATE TABLE corporate_giving('+columns+'cycle INTEGER)')
            db.execute('INSERT INTO contributions VALUES(?,?,?,?,?,?,?,?,?)',
                ('9999999999999999991','C00000001','INERT EXAMPLE',-12.5,'2024-01-02','INERT','accepted',0,2024))
        before = source.read_bytes()
        evidence = {'sub_id':'9999999999999999991','committee_id':'C00000001','contributor_name':'INERT EXAMPLE',
                    'contribution_receipt_amount':-12.5,'contribution_receipt_date':'2024-01-02',
                    'filing_form':'F3X','line_number':'11AI'}
        for name, responses, cap, status, physical in [
                ('complete',[(body(page([evidence])),200)],2,'complete',1),
                ('limited',[(b'{}',503)]*4,2,'needs_evidence',2)]:
            with self.subTest(name=name):
                transport, opener = self.transport(responses,maximum=cap)
                with patch.object(accounted.AccountedTransport,'live',return_value=transport):
                    result = hydration.hydrate(source,self.root/(name+'.sqlite'),fetch=True,max_requests=cap)
                self.assertEqual((result['status'],result['physical_requests'],len(opener.calls)),(status,physical,physical))
                self.assertEqual(source.read_bytes(),before)
                with closing(sqlite3.connect(result['output'])) as db:
                    self.assertEqual(db.execute('SELECT sub_id,contribution_receipt_amount,contribution_receipt_date FROM contributions').fetchone(),
                                     ('9999999999999999991',-12.5,'2024-01-02'))
                if status=='needs_evidence':
                    self.assertEqual(result['unresolved_count'],1)
                    self.assertIn('unknown',result['fetch_admission_refusal'])
                else:
                    self.assertEqual(result['unresolved_count'],0)
                    self.assertTrue(result['original_http_archive'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
