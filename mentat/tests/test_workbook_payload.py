from copy import deepcopy
from pathlib import Path
import sys
import unittest
import json
import os
import shutil
import subprocess
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from prepare_workbook_payload import project, RAW_FIELDS, write_parts
from fec_transfer_graph import atomic_json


class WorkbookProjectionTests(unittest.TestCase):
    def test_downstream_audit_tables_follow_selected_cycle_and_survive_projection(self):
        fields=('downstream_reach','downstream_measurement_ledger','downstream_sources')
        payload={'metadata':{},'raw_rows':[],'cycles':[
            {'cycle':2024,'label':'2023-2024'},{'cycle':2026,'label':'2025-2026'}],
            **{field:[{'cycle':2024,'source':'kept'},{'cycle':2026,'source':'outside'}] for field in fields}}
        projected=project(deepcopy(payload))
        for field in fields:
            self.assertEqual(projected[field],payload[field])
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.json'; output=Path(directory)/'cycle.json'
            source.write_text(json.dumps(projected))
            subprocess.run(['node',str(Path(__file__).resolve().parents[1]/'tools/filter_industry_cycle_data.mjs'),
                str(source),str(output),'2023-2024'],check=True,capture_output=True)
            selected=json.loads(output.read_text())
            for field in fields:
                self.assertEqual(selected[field],[{'cycle':2024,'source':'kept'}])

    def test_projection_retains_destination_lineage_and_both_complete_partitions(self):
        raw={'source_id':'1','recipient_state':'DC','represented_state':'VA',
             'recipient_location_status':'known','represented_location_status':'known',
             'recipient_location_evidence':'cm24','represented_location_evidence':'ccl24; cn24'}
        states=[{'location_view':'recipient_state','destination':'DC','net_amount':100},
                {'location_view':'represented_state','destination':'VA','net_amount':100}]
        payload={'metadata':{'destination_schema':1},'raw_rows':[raw],
                 'state_destinations':states,'recipient_locations':[dict(raw)]}
        result=project(deepcopy(payload))
        self.assertEqual(result['raw_rows'],[raw])
        self.assertEqual(result['state_destinations'],states)
        self.assertEqual(result['recipient_locations'],[raw])

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell quoting regression')
    def test_powershell_large_json_validation(self):
        tools = Path(__file__).resolve().parents[1]/'tools'
        with tempfile.TemporaryDirectory(prefix='report json ') as directory:
            root = Path(directory)
            (root/'valid.json').write_text('{"company":"Unicode café"}',encoding='utf-8')
            (root/'invalid.json').write_text('{"company":')
            script = root/'validate.ps1'
            script.write_text('''param($toolsPath,$runtime,$fixture)
$scriptRoot=$toolsPath
$PythonPath=$runtime
$source=Get-Content -Raw -LiteralPath (Join-Path $toolsPath 'build_industry_report.ps1')
$ast=[System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$null,[ref]$null)
$function=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Test-JsonFile'},$true)
Invoke-Expression ($function.Extent.Text.Replace('480MB','1'))
if (-not (Test-JsonFile (Join-Path $fixture 'valid.json'))) {throw 'Valid JSON rejected'}
if (Test-JsonFile (Join-Path $fixture 'invalid.json')) {throw 'Invalid JSON accepted'}
''')
            result = subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),
                str(tools),sys.executable,str(root)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_chunk_reader_preserves_every_value_and_rejects_corruption(self):
        node = os.environ.get('MENTAT_NODE_PATH') or shutil.which('node')
        if not node:
            self.skipTest('Node runtime required')
        module = (Path(__file__).resolve().parents[1]/'tools/read_report_payload.mjs').as_uri()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'report.json'
            payload = {'metadata': {'label': 'Unicode café, escaped "quote"'},
                       'rows': [{'id':str(10**20+i),'amount':-1.25} for i in range(7)],
                       'empty': [], 'nil': None, 'nested': [[1,2],[True,False]]}
            atomic_json(source, payload)
            write_parts(source, payload, rows_per_part=2)
            script = Path(directory)/'read.mjs'
            script.write_text('import {readReportPayload} from '+json.dumps(module)+';\n'
                              'console.log(JSON.stringify(await readReportPayload(process.argv[2], 0)));')
            def run():
                return subprocess.run([node,str(script),str(source)],capture_output=True,text=True,encoding='utf-8')
            good = run()
            self.assertEqual(good.returncode,0,good.stderr)
            self.assertEqual(json.loads(good.stdout),payload)
            part = source.with_name(source.name+'.parts')/'1-0.json'
            saved = part.read_bytes()
            part.write_text('[]')
            self.assertIn('checksum mismatch',run().stderr)
            part.write_bytes(saved)
            source.write_text('{}')
            self.assertIn('do not match source',run().stderr)

    def test_projection_preserves_financial_and_identity_inputs(self):
        raw={'cycle':2024,'cycle_label':'2023-2024','company':'Fixture','amount':-125.75,
             'employee_tier':'Employee','party':'D','recipient_category':'Carey',
             'include_in_analysis':'Yes','company_match_status':'accepted',
             'person_key':'person:one','contributor':'PERSON, ONE',
             'recipient_key':'FEC:C1','recipient':'COMMITTEE','committee_id':'C1',
             'category_source':'official','party_source':'official','source_cycle':2024,
             'source_id':'12345678901234567890','unused_explanation':'long source text'}
        payload={'metadata':{},'raw_rows':[raw],
                 'cycle_exceptions':[{'source_id':'outside','amount':100}],
                 'allocation_estimates':[{'root':'C1','estimated_amount':25}],
                 'candidate_master':[{'candidate_id':'H1'}]}
        baseline=deepcopy(payload)
        result=project(payload)
        self.assertEqual(result['raw_rows'][0],{key:value for key,value in raw.items() if key in RAW_FIELDS})
        self.assertEqual(result['cycle_exceptions'],baseline['cycle_exceptions'])
        self.assertEqual(result['allocation_estimates'],baseline['allocation_estimates'])
        self.assertNotIn('candidate_master',result)
        self.assertEqual(result['metadata']['workbook_projection'],'formula-facts-v1')

    def test_projection_preserves_fine_grain_detail_frames(self):
        # The console can only chart columns a worksheet publishes, and the two
        # detail frames are the only published home for committee type, ZIP,
        # city, state, employer and occupation - none of which are in
        # RAW_FIELDS. They ride their own payload keys, so the projection that
        # strips raw_rows must leave them byte-for-byte alone.
        detail={'recipient_detail':[{'cycle':2024,'cycle_label':'2023-2024',
                    'company':'Fixture','committee_id':'C1','recipient':'COMMITTEE',
                    'committee_type':'O','designation':'U',
                    'committee_type_label':'Independent expenditure-only (Super PAC)',
                    'designation_label':'Unauthorized','recipient_category':'Super PAC',
                    'party':'D','total_giving':-125.75,'gift_count':2,'contributors':1,
                    'first_date':'2023-01-05','last_date':'2024-11-02','folded_rows':0}],
                'contributor_detail':[{'cycle':2024,'cycle_label':'2023-2024',
                    'company':'Fixture','state':'MA','city':'Boston','zip5':'02134',
                    'employer':'Fixture','occupation':'Engineer','total_giving':125.75,
                    'gift_count':1,'contributors':1,'folded_rows':0}]}
        payload={'metadata':{},'raw_rows':[],**deepcopy(detail)}
        result=project(payload)
        self.assertEqual(result['recipient_detail'],detail['recipient_detail'])
        self.assertEqual(result['contributor_detail'],detail['contributor_detail'])
        for field in ('zip5','city','state','employer','occupation','committee_type'):
            self.assertNotIn(field,RAW_FIELDS)
        # A leading-zero ZIP stays a string; coerced to a number it becomes 2134.
        self.assertEqual(result['contributor_detail'][0]['zip5'],'02134')


if __name__=='__main__':unittest.main()
