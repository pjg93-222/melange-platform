from collections import defaultdict
import json
from pathlib import Path
import sys
import tempfile
import unittest
import hashlib
import sqlite3
from contextlib import closing
import subprocess
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import fec_transfer_graph
from fec_transfer_graph import CASH_CODES,allocate,reachable,transfer_index


def transfer_row(source='C00000001',dest='C00000002',amount='100',kind='24K',sub='1',transaction='T1',file='1',memo='',date='01022024',amend='N'):
    fields=['']*21
    for index,value in {0:source,1:amend,2:'Q1',5:kind,13:date,14:amount,15:dest,16:transaction,17:file,18:memo,20:sub}.items():fields[index]=value
    return '|'.join(fields)


def header(number,original=None):
    from fec_filing_headers import parse_header
    fields=['']*15
    fields[0]='F3XA' if original else 'F3XN';fields[1]='C00000001';fields[9]='Q1'
    fields[13]='20240101';fields[14]='20240331'
    prefix=('HDR,FEC,8.5,Fixture,1,'+str(original or '')+',1\n'+','.join(fields)+'\n').encode()
    return dict(header=parse_header(prefix,number),prefix_hex=prefix.hex(),
                prefix_sha256=hashlib.sha256(prefix).hexdigest(),url=f'https://docquery.fec.gov/dcdev/posted/{number}.fec')


class Transfers(unittest.TestCase):
    def test_multihop_conserves_funding_and_keeps_opposition_separate(self):
        edges=[{'source':'A','destination':'B','amount':100},{'source':'A','destination':'C','amount':100}]
        activity=[{'source':'B','candidate':'H1','direction':'Support','amount':90},
                  {'source':'B','candidate':'H2','direction':'Oppose','amount':10}]
        rows,coverage=allocate({'A':1000},edges,activity)
        values={(r['candidate_id'],r['direction']):r['estimated_amount'] for r in rows}
        self.assertEqual(values,{('H1','Support'):450,('H2','Oppose'):50})
        self.assertEqual(coverage[0]['unresolved'],500)
        self.assertEqual(coverage[0]['estimated_candidate_activity']+coverage[0]['unresolved'],1000)

    def test_cycles_and_depth_limits_become_unresolved(self):
        cycle=[{'source':'A','destination':'B','amount':100},{'source':'B','destination':'A','amount':100}]
        rows,coverage=allocate({'A':50},cycle,[])
        self.assertEqual(rows,[])
        self.assertEqual(coverage[0]['unresolved_reasons'],{'cycle':50})
        chain=[{'source':str(i),'destination':str(i+1),'amount':1} for i in range(5)]
        _,coverage=allocate({'0':50},chain,[],max_depth=2)
        self.assertEqual(coverage[0]['unresolved_reasons'],{'depth_limit':50})

    def test_candidate_transfer_is_not_counted_twice(self):
        record=dict(file_num=10,transaction_id='T1',date='2024-01-01',cents=10000,type='24K')
        edges=[{'source':'A','destination':'CMTE','amount':100,'records':[record]}]
        activity=[{'source':'A','candidate':'H1','direction':'Contribution / Other','amount':100,
                   'transfer_records':[dict(record,destination='CMTE')]}]
        rows,coverage=allocate({'A':25},edges,activity,stop_nodes={'CMTE'},candidate_links={'CMTE':{'H1'}})
        self.assertEqual(rows[0]['estimated_amount'],25)
        self.assertEqual(coverage[0]['unresolved'],0)

    def test_unmatched_candidate_transfer_retains_its_share(self):
        edges=[dict(source='A',destination='CMTE',amount=90)]
        activity=[dict(source='A',candidate='OTHER',direction='Oppose',amount=10)]
        rows,coverage=allocate({'A':100},edges,activity,stop_nodes={'CMTE'})
        self.assertEqual(rows[0]['estimated_amount'],10)
        self.assertEqual(coverage[0]['unresolved_reasons'],{'unmatched_candidate_transfer':90})

    def test_candidate_duplicate_match_preserves_signed_corrections(self):
        paid=dict(file_num=10,transaction_id='T1',date='2024-01-01',cents=10000,type='24K')
        refund=dict(paid,transaction_id='REFUND',cents=-9000)
        activity=[dict(source='A',candidate='H1',direction='Contribution / Other',amount=10,
            transfer_records=[dict(r,destination='CMTE') for r in (paid,refund)])]
        edges=[dict(source='A',destination='CMTE',amount=10,records=[paid,refund])]
        rows,coverage=allocate({'A':25},edges,activity,stop_nodes={'CMTE'},candidate_links={'CMTE':{'H1'}})
        self.assertEqual(rows[0]['estimated_amount'],25)
        self.assertEqual(coverage[0]['unresolved'],0)
        edges[0]['records']=[paid,dict(refund,transaction_id='UNMATCHED')]
        rows,coverage=allocate({'A':25},edges,activity,stop_nodes={'CMTE'},candidate_links={'CMTE':{'H1'}})
        self.assertEqual(rows,[])
        self.assertEqual(coverage[0]['unresolved'],25)

    def test_amount_and_candidate_match_do_not_prove_duplicate(self):
        record=dict(file_num=10,transaction_id='T1',date='2024-01-01',cents=10000,type='24K')
        for change in ({'transaction_id':'DIFFERENT'},{'file_num':11},{'date':'2024-01-02'},{'type':'24Z'}):
            with self.subTest(change=change):
                edges=[dict(source='A',destination='CMTE',amount=100,records=[record])]
                activity=[dict(source='A',candidate='H1',direction='Contribution / Other',amount=100,
                    transfer_records=[dict(record,destination='CMTE',**change)])]
                rows,coverage=allocate({'A':25},edges,activity,stop_nodes={'CMTE'},candidate_links={'CMTE':{'H1'}})
                self.assertEqual(rows,[])
                self.assertEqual(coverage[0]['unresolved'],25)

    def test_unresolved_descendant_does_not_renormalize_other_activity(self):
        rows,coverage=allocate({'A':100},[dict(source='A',destination='B',amount=90)],
            [dict(source='A',candidate='OTHER',direction='Oppose',amount=10)],unresolved_nodes={'B'})
        self.assertEqual(rows[0]['estimated_amount'],10)
        self.assertEqual(coverage[0]['unresolved_reasons'],{'unresolved_transfer_records':90})

    def test_negative_funding_does_not_become_positive_estimate(self):
        rows,coverage=allocate({'A':-15},[],[])
        self.assertEqual(rows,[])
        self.assertEqual(coverage[0]['negative_net_funding_excluded'],-15)

    def test_oth_direction_amendments_memo_and_dates(self):
        row=transfer_row
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'itoth.txt'
            path.write_text('\n'.join([row(),row(amount='150',sub='2',file='2',amend='A'),
                row(amount='150',sub='2',file='2',amend='A'),row(kind='18K',transaction='IN',sub='3'),
                row(transaction='M',memo='X',sub='4'),row(transaction='IE',kind='24A',sub='5'),
                row(transaction='T1',date='01022023',sub='6',file='3')]),encoding='utf-8')
            evidence=[header(1),header(2,1),header(3)]
            index=transfer_index(path,2024,evidence)
            self.assertEqual(len(index['edges']),1)
            self.assertEqual(index['edges'][0]['amount'],250)
            self.assertEqual(index['edges'][0]['first_date'],'2023-01-02')
            self.assertEqual(index['counts']['memo_rows'],1)
            self.assertEqual(transfer_index(path,2024,evidence),index)
            with closing(sqlite3.connect(index['ledger_path'])) as connection:
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM records').fetchone()[0],5)
                self.assertEqual(index['counts']['source_rows'],7)

    def test_reclassification_and_new_year_amendment_require_filing_evidence(self):
        for change in ({'kind':'24Z'},{'date':'01012024'},{'dest':''},{'memo':'X'}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                path=Path(temporary)/'itoth.txt'
                original=transfer_row(date='12312023')
                amended=transfer_row(**dict(date='12312023',sub='2',file='2',amend='A',**change)) if 'date' not in change else transfer_row(date=change['date'],sub='2',file='2',amend='A')
                path.write_text(original+'\n'+amended,encoding='utf-8')
                resolved=transfer_index(path,2024,[header(1),header(2,1)])
                if 'date' in change:
                    self.assertEqual(resolved['edges'][0]['amount'],100)
                    self.assertEqual(resolved['edges'][0]['first_date'],'2024-01-01')
                else:self.assertEqual(resolved['edges'],[])
                # Without headers the default takes the FEC's own convention,
                # the highest filing number. For every one of these changes it
                # lands on the SAME answer the filing headers give, and the
                # original is never counted beside the amendment.
                provisional=transfer_index(path,2024)
                self.assertEqual(sum(e['amount'] for e in provisional['edges']),200 if 'date' in change else 100)
                self.assertGreater(provisional['counts']['unverified_version_rows'],0)
                # Under the other reading nothing is counted at all. A memo
                # restatement of the same destination, amount, date and type is
                # one transaction re-reported, so it settles by file number
                # either way.
                original_policy=fec_transfer_graph.AMBIGUOUS_VERSION_POLICY
                fec_transfer_graph.AMBIGUOUS_VERSION_POLICY='withhold'
                try:unknown=transfer_index(path,2024)
                finally:fec_transfer_graph.AMBIGUOUS_VERSION_POLICY=original_policy
                self.assertEqual(unknown['edges'],[])
                self.assertEqual(unknown['unresolved_sources'],
                                 [] if change=={'memo':'X'} else ['C00000001'])

    def test_invalid_amount_or_date_correction_cannot_revive_original(self):
        for change in ({'amount':'NaN'},{'amount':'0.001'},{'date':'BAD'}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                path=Path(temporary)/'itoth.txt'
                path.write_text(transfer_row()+'\n'+transfer_row(sub='2',file='2',amend='A',**change),encoding='utf-8')
                value=transfer_index(path,2024,[header(1),header(2,1)])
                self.assertEqual(value['edges'],[])
                self.assertEqual(value['unresolved_sources'],['C00000001'])

    def test_corrupt_header_or_cached_ledger_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'itoth.txt';path.write_text(transfer_row(),encoding='utf-8')
            bad=header(1);bad['header']['original_file_num']=2
            with self.assertRaisesRegex(ValueError,'evidence'):transfer_index(path,2024,[bad])
            value=transfer_index(path,2024)
            with Path(value['ledger_path']).open('ab') as stream:stream.write(b'altered')
            with self.assertRaisesRegex(ValueError,'checksum'):transfer_index(path,2024)

    @unittest.skipUnless(Path('C:/Program Files/R/R-4.6.0/bin/Rscript.exe').is_file(),'R integration requires installed R')
    def test_r_retained_records_match_python_transfer_evidence_end_to_end(self):
        from enrich_downstream import enrich
        project=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);payload_path=root/'r-evidence.json'
            result=subprocess.run(['C:/Program Files/R/R-4.6.0/bin/Rscript.exe',
                str(project/'tests/test_transfer_evidence.R'),str(payload_path)],
                capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            path=root/'itoth.txt';path.write_text(transfer_row(date='12312023'),encoding='utf-8')
            index=transfer_index(path,2024)
            payload=json.loads(payload_path.read_text())
            payload.update(metadata=dict(industry='Fixture',transfer_coverage=[dict(cycle=2024,status='indexed',counts=index['counts'])]),
                cycles=[dict(cycle=2024)],committee_map=[],transfer_edges=index['edges'],
                raw_rows=[dict(cycle=2024,committee_id='C00000001',amount=25,include_in_analysis='Yes',receipt_role='contribution')],
                candidate_committee_links=[dict(cycle=2024,committee_id='C00000002',candidate_id='H00000001',designation='P')])
            enriched=enrich(payload,root)
            self.assertEqual(enriched['allocation_estimates'][0]['estimated_amount'],25)
            self.assertEqual(enriched['allocation_coverage'][0]['unresolved'],0)
            self.assertTrue(all(r['passed'] for r in enriched['quality_checks']))

    @unittest.skipUnless(Path('C:/Program Files/R/R-4.6.0/bin/Rscript.exe').is_file(),'R integration requires installed R')
    def test_published_edge_records_survive_the_r_reader_into_allocate(self):
        # 2026-09-25, cycle 2020 Crypto: publish_index() writes transfer-edges.csv
        # with the csv module, which doubles every quote inside the JSON `records`
        # field; data.table::fread handed the field back with the doubled quotes
        # still in it, the exporter wrote that into transfer_edges, and
        # record_list() below died on json.loads. The test above builds its edges
        # in Python and never crossed the CSV. Here the real R reader reads a real
        # published index, the exporter's JSON options write it, and allocate()
        # runs the one path that parses edge records: a candidate-committee stop.
        project=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);oth=root/'oth24';oth.mkdir()
            # T2 has no sub-ID, so its evidence holds an empty JSON string ("").
            (oth/'itoth.txt').write_text(transfer_row(sub='1',transaction='T1')+'\n'
                +transfer_row(sub='',transaction='T2',amount='50'),encoding='utf-8')
            index=transfer_index(oth/'itoth.txt',2024)
            fec_transfer_graph.publish_index(oth,index)
            self.assertIn('""""',(oth/'transfer-edges.csv').read_text(encoding='utf-8'))
            script=root/'read_edges.R';exported=root/'edges.json'
            script.write_text('\n'.join([
                'args <- commandArgs(TRUE)',
                "source(file.path(args[[1L]], 'R', 'fec_transfer_graph.R'))",
                "roots <- data.table::data.table(cycle=2024L, committee_id='C00000001', preliminary_crypto_funding=100)",
                "links <- data.table::data.table(cycle=2024L, committee_id='C00000002', designation='P')",
                'context <- fec_transfer_context(args[[2L]], roots, links, 2024L)',
                'writeLines(as.character(jsonlite::toJSON(list(transfer_edges=context$edges),',
                "  dataframe='rows', auto_unbox=TRUE, digits=NA, na='null', pretty=FALSE)), args[[3L]], useBytes=TRUE)",
            ]),encoding='utf-8')
            result=subprocess.run(['C:/Program Files/R/R-4.6.0/bin/Rscript.exe',str(script),
                str(project),str(root),str(exported)],capture_output=True,text=True,timeout=120)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            edges=json.loads(exported.read_text(encoding='utf-8'))['transfer_edges']
            self.assertEqual(len(edges),1)
            evidence=json.loads(index['edges'][0]['records'])
            self.assertEqual(fec_transfer_graph.record_list(edges[0]['records']),evidence)
            self.assertIn('',[record['sub_id'] for record in evidence])
            activity=[dict(source='C00000001',candidate='H00000001',direction='Contribution / Other',amount=150,
                transfer_records=[dict(record,destination='C00000002') for record in evidence])]
            rows,coverage=allocate({'C00000001':25},edges,activity,stop_nodes={'C00000002'},
                                   candidate_links={'C00000002':{'H00000001'}})
            # Both records match the candidate's own receipt of the transfer, so
            # the edge is not counted a second time and nothing is unresolved.
            self.assertEqual([(r['candidate_id'],r['estimated_amount']) for r in rows],[('H00000001',25)])
            self.assertEqual(coverage[0]['unresolved'],0)


STATUSES={'observed','current','superseded','duplicate','unresolved_version','unresolved_record'}


class VersionIdentity(unittest.TestCase):
    """VERSION 6: a transaction label or attribute equality is not source identity.

    One test per class of the rule. `committee|report_type|transaction_id` is
    still the group; inside it, rows are partitioned by the counterparty, the
    amount in cents, the transaction date and the transaction type.
    """

    def build(self,lines,evidence=(),cycle=2024):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path=Path(temporary.name)/'itoth.txt'
        path.write_text('\n'.join(lines),encoding='utf-8')
        index=transfer_index(path,cycle,list(evidence))
        with closing(sqlite3.connect(index['ledger_path'])) as connection:
            connection.row_factory=sqlite3.Row
            ledger=[dict(row) for row in connection.execute('SELECT rowid,* FROM records ORDER BY rowid')]
        self.assert_one_status_and_no_double_count(ledger)
        return index,ledger

    def assert_one_status_and_no_double_count(self,ledger):
        """The conservation invariant, recomputed from the ledger itself.

        Counted dollars cannot exceed the distinct source observations.
        Verified amendment families may reduce that amount; mere attribute
        equality cannot. Fixtures here use nonnegative amounts.
        """
        groups=defaultdict(list)
        for row in ledger:
            self.assertIn(row['status'],STATUSES)
            if row['status']!='duplicate':groups[row['version_key']].append(row)
        for key,rows in groups.items():
            cash=[r for r in rows if r['type'] in CASH_CODES and r['memo']!='X']
            counted=sum(r['cents'] or 0 for r in cash if r['status']=='current')
            copies=defaultdict(lambda:defaultdict(int))
            for row in cash:
                copies[(row['destination'],row['cents'],row['date'],row['type'])][row['file_num']]+=1
            # Without a filing relationship, distinct source observations may
            # both be real. Attribute equality cannot lower this ceiling.
            ceiling=sum(sum(files.values())*(identity[1] or 0) for identity,files in copies.items())
            self.assertLessEqual(counted,ceiling,f'{key} counts more than its distinct identities allow')

    def test_class_a_an_exactly_repeated_raw_line_is_a_duplicate(self):
        line=transfer_row(sub='1',file='1')
        index,ledger=self.build([line,line])
        self.assertEqual([row['status'] for row in ledger],['current','duplicate'])
        self.assertEqual(index['edges'][0]['amount'],100)

    def test_identical_attributes_in_two_filings_require_a_relationship(self):
        index,ledger=self.build([transfer_row(sub='1',file='1'),transfer_row(sub='2',file='2')])
        self.assertEqual({row['file_num']:(row['status'],row['version_basis']) for row in ledger},
                         {1:('current','filing_relationship_unverified'),2:('current','filing_relationship_unverified')})
        self.assertEqual([edge['amount'] for edge in index['edges']],[200])
        self.assertEqual(index['unresolved_sources'],[])

    def test_conflicting_facts_for_same_source_identity_stop_indexing(self):
        with self.assertRaisesRegex(ValueError,'Conflicting FEC sub-ID'):
            self.build([transfer_row(sub='1',amount='100'),transfer_row(sub='1',amount='150')])

    def test_class_b_records_the_header_route_when_headers_settle_the_family(self):
        index,ledger=self.build([transfer_row(sub='1',file='1'),transfer_row(sub='2',file='2',amend='A')],
                                [header(1),header(2,1)])
        self.assertEqual({row['file_num']:(row['status'],row['version_basis']) for row in ledger},
                         {1:('superseded','header'),2:('current','header')})
        self.assertEqual([edge['amount'] for edge in index['edges']],[100])

    def test_an_fec_reload_differing_only_in_sub_id_counts_once(self):
        # F11 (Paul 2026-09-25): same transaction id, file, identity and memo code,
        # only the SUB_ID differs - one record loaded twice. The highest SUB_ID stays.
        index,ledger=self.build([transfer_row(sub='1',file='1'),transfer_row(sub='2',file='1')])
        self.assertEqual({row['sub_id']:(row['status'],row['version_basis']) for row in ledger},
                         {'1':('duplicate','repeated_itemization'),'2':('current','distinct_observation')})
        self.assertEqual(index['edges'][0]['amount'],100)
        self.assertEqual(index['edges'][0]['record_count'],1)

    def test_two_genuine_equal_gifts_on_one_day_are_both_counted(self):
        index,ledger=self.build([transfer_row(sub='1',file='1',transaction='SB23.1'),
                                 transfer_row(sub='2',file='1',transaction='SB23.2')])
        self.assertEqual([row['status'] for row in ledger],['current','current'])
        self.assertEqual(index['edges'][0]['amount'],200)
        self.assertEqual(index['edges'][0]['record_count'],2)

    def test_class_c_collapse_also_applies_on_the_header_route(self):
        # Filing headers settle which filing is current; they say nothing about
        # the same line appearing twice INSIDE that filing.
        index,ledger=self.build([transfer_row(sub='1',file='1'),
                                 transfer_row(sub='2',file='2',amend='A'),
                                 transfer_row(sub='3',file='2',amend='A')],
                                [header(1),header(2,1)])
        self.assertEqual({row['sub_id']:(row['status'],row['version_basis']) for row in ledger},
                         {'1':('superseded','header'),'2':('duplicate','repeated_itemization'),
                          '3':('current','header')})
        self.assertEqual([edge['amount'] for edge in index['edges']],[100])

    def test_class_d_one_filing_reusing_a_line_label_keeps_both_transactions(self):
        index,ledger=self.build([transfer_row(dest='C00000002',amount='100',sub='1',file='1',transaction='SE.5'),
                                 transfer_row(dest='C00000003',amount='250',sub='2',file='1',transaction='SE.5')])
        self.assertEqual([row['status'] for row in ledger],['current','current'])
        self.assertEqual(sorted(edge['amount'] for edge in index['edges']),[100,250])
        self.assertTrue(all(row['tran_id_reused']==1 for row in ledger))

    def test_class_d_a_reused_label_across_filings_keeps_every_counterparty(self):
        # The C00864926 `SE.5` shape from the measurement: one label, three
        # filings, three different recipients, three real expenditures.
        index,ledger=self.build([transfer_row(dest='C00000002',amount='100',sub='1',file='1759197',transaction='SE.5'),
                                 transfer_row(dest='C00000003',amount='250',sub='2',file='1779689',transaction='SE.5'),
                                 transfer_row(dest='C00000004',amount='700',sub='3',file='1788908',transaction='SE.5')])
        self.assertEqual({row['status'] for row in ledger},{'current'})
        self.assertEqual(sum(edge['amount'] for edge in index['edges']),1050)
        self.assertEqual(index['unresolved_sources'],[])
        self.assertEqual(index['unresolved_source_amounts'],{})

    def policy(self,value):
        self.addCleanup(setattr,fec_transfer_graph,'AMBIGUOUS_VERSION_POLICY',
                        fec_transfer_graph.AMBIGUOUS_VERSION_POLICY)
        fec_transfer_graph.AMBIGUOUS_VERSION_POLICY=value

    def test_a_reused_label_a_year_apart_is_two_transactions_not_a_version(self):
        # The C00692343 `SB22.2` shape: one label, the SAME report type, two
        # filings a year apart, different amount AND different date. An
        # amendment corrects a figure or a date; rows that share neither have
        # nothing in common but a recycled label.
        index,ledger=self.build([
            transfer_row(amount='586067',date='06302023',sub='1',file='1713770',transaction='SB22.2'),
            transfer_row(amount='878065',date='06302024',sub='2',file='1798394',transaction='SB22.2')])
        self.assertEqual({row['status'] for row in ledger},{'current'})
        self.assertTrue(all(row['tran_id_reused']==1 for row in ledger))
        self.assertEqual(sum(edge['amount'] for edge in index['edges']),586067+878065)
        self.assertEqual(index['unresolved_source_amounts'],{})

    def test_same_counterparty_and_amount_on_another_date_is_still_ambiguous(self):
        self.policy('withhold')
        index,ledger=self.build([transfer_row(amount='100',date='06302023',sub='1',file='1'),
                                 transfer_row(amount='100',date='06302024',sub='2',file='2')])
        self.assertEqual({row['status'] for row in ledger},{'unresolved_version'})
        self.assertEqual(index['edges'],[])

    def test_same_counterparty_and_date_for_another_amount_is_still_ambiguous(self):
        self.policy('withhold')
        index,ledger=self.build([transfer_row(amount='100',date='06302024',sub='1',file='1'),
                                 transfer_row(amount='150',date='06302024',sub='2',file='2')])
        self.assertEqual({row['status'] for row in ledger},{'unresolved_version'})
        self.assertEqual(index['edges'],[])

    def test_class_e_by_default_counts_the_latest_row_and_flags_it_unverified(self):
        # The default reading. A row loses its amount only where evidence
        # contradicts it; nothing here contradicts either row, so the dollars
        # stay and carry the flag.
        index,ledger=self.build([transfer_row(amount='100',sub='1',file='1'),
                                 transfer_row(amount='150',sub='2',file='2')])
        self.assertEqual(index['ambiguous_version_policy'],'count_all')
        self.assertEqual({row['file_num']:(row['status'],row['unverified_version'],row['version_basis'])
                          for row in ledger},
                         {1:('current',1,'filing_relationship_unverified'),
                          2:('current',1,'filing_relationship_unverified')})
        self.assertEqual([edge['amount'] for edge in index['edges']],[250])
        self.assertEqual(index['unresolved_source_amounts'],{})

    def test_class_e_withhold_reading_keeps_no_dollars_and_reports_the_share(self):
        self.policy('withhold')
        index,ledger=self.build([transfer_row(amount='100',sub='1',file='1'),
                                 transfer_row(amount='150',sub='2',file='2')])
        self.assertEqual({row['status'] for row in ledger},{'unresolved_version'})
        self.assertEqual(index['edges'],[])
        self.assertEqual(index['unresolved_sources'],['C00000001'])
        self.assertEqual(index['unresolved_source_amounts'],{'C00000001':150.0})
        self.assertEqual(index['ambiguous_version_policy'],'withhold')

    def test_a_cross_filing_type_change_is_not_counted_as_a_second_transaction(self):
        # Under either reading the 24K row never survives beside the 24Z one.
        index,_=self.build([transfer_row(sub='1',file='1'),transfer_row(sub='2',file='2',kind='24Z')])
        self.assertEqual([edge['amount'] for edge in index['edges']],[100])
        self.policy('withhold')
        withheld,_=self.build([transfer_row(sub='1',file='1'),transfer_row(sub='2',file='2',kind='24Z')])
        self.assertEqual(withheld['edges'],[])
        self.assertEqual(withheld['unresolved_sources'],['C00000001'])

    def test_the_policy_choice_cannot_be_served_out_of_the_other_readings_cache(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        path=Path(temporary.name)/'itoth.txt'
        path.write_text(transfer_row(amount='100',sub='1',file='1')+'\n'
                        +transfer_row(amount='150',sub='2',file='2'),encoding='utf-8')
        counted=transfer_index(path,2024)
        self.policy('withhold')
        withheld=transfer_index(path,2024)
        self.assertEqual([edge['amount'] for edge in counted['edges']],[250])
        self.assertEqual(withheld['edges'],[])
        self.assertNotEqual(withheld['ledger_path'],counted['ledger_path'])

    def test_class_f_withheld_rows_take_a_prorata_share_not_the_whole_committee(self):
        edges=[dict(source='A',destination='B',amount=60)]
        activity=[dict(source='A',candidate='H1',direction='Support',amount=40)]
        rows,coverage=allocate({'A':100},edges,activity,unresolved_amounts={'A':100})
        self.assertAlmostEqual(rows[0]['estimated_amount'],20)
        self.assertAlmostEqual(coverage[0]['unresolved_reasons']['unresolved_transfer_records'],50)
        self.assertAlmostEqual(coverage[0]['unresolved_reasons']['no_observed_outflow'],30)
        self.assertAlmostEqual(coverage[0]['estimated_candidate_activity']+coverage[0]['unresolved'],100)

    def test_class_f_a_malformed_source_file_still_blocks_the_whole_committee(self):
        rows,coverage=allocate({'A':100},[dict(source='A',destination='B',amount=60)],
                               [dict(source='A',candidate='H1',direction='Support',amount=40)],
                               unresolved_nodes={'A'})
        self.assertEqual(rows,[])
        self.assertEqual(coverage[0]['unresolved_reasons'],{'unresolved_transfer_records':100})

    def test_every_class_together_conserves_and_gives_each_row_one_status(self):
        self.policy('withhold')
        line=transfer_row(dest='C00000002',amount='100',sub='1',file='1',transaction='SE.5')
        index,ledger=self.build([
            line,line,
            transfer_row(dest='C00000003',amount='250',sub='3',file='1',transaction='SE.5'),
            transfer_row(dest='C00000003',amount='250',sub='4',file='1',transaction='SE.5'),
            transfer_row(dest='C00000004',amount='700',sub='5',file='1',transaction='SE.5'),
            transfer_row(dest='C00000004',amount='700',sub='6',file='2',transaction='SE.5'),
            transfer_row(dest='C00000005',amount='900',sub='7',file='1',transaction='SE.5'),
            transfer_row(dest='C00000005',amount='950',sub='8',file='2',transaction='SE.5')])
        statuses=defaultdict(int)
        for row in ledger:statuses[row['status']]+=1
        self.assertEqual(dict(statuses),{'current':3,'duplicate':2,'superseded':1,'unresolved_version':2})
        self.assertEqual(sum(edge['amount'] for edge in index['edges']),100+250+700)
        self.assertEqual(index['unresolved_source_amounts'],{'C00000001':950.0})


if __name__=='__main__':unittest.main()
