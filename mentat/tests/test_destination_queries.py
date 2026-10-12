from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import test_contribution_data as legacy
import contribution_data as data
import contribution_query as query
import contribution_semantics as semantics

class DestinationTests(unittest.TestCase):
    setUp=legacy.ContributionQueryTests.setUp

    def test_state_partitions_keep_money_and_observation_identity(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE facts SET recipient_state='DC',represented_state=CASE WHEN source_id='a' THEN 'VA' ELSE 'Unknown' END")
            db.commit()
        totals=[]
        for field in ('recipient_state','represented_state'):
            request={'basis':'receipt','dimensions':[field],'measures':['amount','records']}
            result=query._query_ready(self.path,request,self.source,*data._validate(request))
            totals.append(sum(r['amount'] for r in result['rows']))
            self.assertEqual(totals[-1],result['totals']['amount'])
        self.assertEqual(totals[0],totals[1])

    def test_old_workbooks_require_regeneration_before_destination_query(self):
        source={'id':'fixture','available':True,'destination_schema':0}
        with patch.object(data,'_sources',return_value=[source]):
            with self.assertRaisesRegex(ValueError,'Regenerate'):
                data._source(None,{'dataset':'fixture','dimensions':['recipient_state']})
            self.assertIs(data._source(None,{'dataset':'fixture','dimensions':['state']}),source)

    def test_new_fields_append_after_existing_catalog(self):
        fields=semantics.fields()
        self.assertEqual([f['id'] for f in fields[-len(data.DESTINATIONS):]],list(data.DESTINATIONS))
        self.assertTrue(all(f['table']=='Recipient destinations' for f in fields[-len(data.DESTINATIONS):]))

if __name__=='__main__':unittest.main()
