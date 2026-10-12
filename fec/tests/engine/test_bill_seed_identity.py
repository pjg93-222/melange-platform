"""A refresh binds formal bill identities; display metadata is not identity."""
import copy,json
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from legislation import congress_bill_repository as C, update_bill_repository as U


class SeedIdentityGate(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.bundle=self.root/'member_desk_bundle.json'
        self.repo=self.root/'bill_repository_live.json';self.receipt=self.root/'receipt.json'
        self.doc={'members':[{'name':'A','bills':[{'bill':'H.R.1','congress':119,'title':'First','sponsor':'Old style','action':'Introduced'}]}]}
        self.data={'schema':C.SCHEMA,'bills':{'119:hr:1':{'key':'119:hr:1','display':'H.R.1','congress':119}}}
        self.write(self.bundle,self.doc);self.write(self.repo,self.data)
        U.write_refresh_receipt(self.receipt,result={'status':'PASS','seeded':1,'refreshed':1,'failed':0},bundle_path=self.bundle,output_path=self.repo)

    def write(self,path,doc):path.write_text(json.dumps(doc),encoding='utf-8')
    def validate(self):return C.validate_live_repository(self.bundle,self.repo,self.receipt)

    def test_changed_display_metadata_and_first_member_do_not_change_identity(self):
        old=C.seed_identity_fingerprint(self.doc)
        self.doc['members'][0]['bills'][0].update(title='Official title',sponsor='Verified sponsor',action='Passed')
        self.doc['members'].insert(0,{'name':'B','bills':copy.deepcopy(self.doc['members'][0]['bills'])})
        self.write(self.bundle,self.doc)
        self.assertEqual(C.seed_identity_fingerprint(self.doc),old)
        self.assertEqual(self.validate(),self.data)

    def test_new_bill_identity_refuses_even_with_same_count(self):
        self.doc['members'][0]['bills'][0]['bill']='H.R.2';self.write(self.bundle,self.doc)
        with self.assertRaisesRegex(C.RepositoryError,'different bill universe'):self.validate()

    def test_legacy_receipt_remains_strict(self):
        r=json.loads(self.receipt.read_text());r.pop('seed_identities');self.write(self.receipt,r)
        self.validate()
        self.doc['members'][0]['bills'][0]['action']='Changed';self.write(self.bundle,self.doc)
        with self.assertRaisesRegex(C.RepositoryError,'different bill universe'):self.validate()

    def test_present_malformed_identity_never_falls_back_to_legacy(self):
        r=json.loads(self.receipt.read_text())
        for bad in (None,{}, {'count':True,'sha256':'0'*64},{'count':1,'sha256':'bad'}):
            with self.subTest(bad=bad):
                r['seed_identities']=bad;self.write(self.receipt,r)
                with self.assertRaisesRegex(C.RepositoryError,'malformed'):self.validate()

    def test_repository_must_cover_actual_keys_not_merely_the_count(self):
        self.data['bills']={'119:hr:2':{'key':'119:hr:2','display':'H.R.2','congress':119}}
        self.write(self.repo,self.data)
        r=json.loads(self.receipt.read_text());r['repository'].update(sha256=C.sha256_file(self.repo),size=self.repo.stat().st_size)
        self.write(self.receipt,r)
        with self.assertRaisesRegex(C.RepositoryError,'does not cover'):self.validate()

    def test_stale_or_tampered_receipt_still_refuses(self):
        r=json.loads(self.receipt.read_text());original=copy.deepcopy(r)
        r['completed_at']='2000-01-01T00:00:00Z';self.write(self.receipt,r)
        with self.assertRaisesRegex(C.RepositoryError,'freshness'):self.validate()
        r=original;r['repository']['sha256']='0'*64;self.write(self.receipt,r)
        with self.assertRaisesRegex(C.RepositoryError,'hash-bound'):self.validate()


if __name__=='__main__':unittest.main()
