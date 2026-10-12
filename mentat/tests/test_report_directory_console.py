"""All report readers and final output paths use one explicit report folder."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

APP = Path(__file__).resolve().parents[1] / 'app'
spec = importlib.util.spec_from_file_location('authoritative_report_console',APP / 'server.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class ReportDirectoryConsoleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.reports = self.root / 'Published Reports'
        self.reports.mkdir()
        configured = patch.object(app,'report_directory',return_value=self.reports)
        configured.start(); self.addCleanup(configured.stop)
        keys = patch.object(app,'environment_keys',return_value=[])
        keys.start(); self.addCleanup(keys.stop)
        self.console = app.Console(self.root / 'state',rscript=sys.executable)

    def report(self,name):
        path = self.reports / (name + ' Industry Spending Overview.xlsx')
        path.write_bytes(b'catalog only; workbook contents must not be read')
        return path

    def legacy_run(self,path):
        directory = self.root / 'legacy-run'
        directory.mkdir()
        (directory / 'status.json').write_text(json.dumps({'stage':'completed','industries':[
            {'industry':'Banking','status':'succeeded','output_workbook':str(path)}]}))
        return {'id':'old-run','name':'Old collection','directory':directory,'state':'completed'}

    def test_one_folder_source_lists_all_eight_exact_industries(self):
        names = ['Banking','Community Banks','Credit Arms','Credit Union','Crypto','Fintech','Property & Casualty Insurance','Telecom']
        for name in names: self.report(name)
        (self.reports / '~$locked.xlsx').write_bytes(b'ignore')
        sources = self.console.report_sources()
        self.assertEqual([r['id'] for r in sources['runs']],['published-reports'])
        self.assertEqual(sources['report_directory'],str(self.reports))
        catalog = self.console.report_catalog('published-reports')
        self.assertEqual({r['industry'] for r in catalog['reports']},set(names))
        with patch.object(app,'graph_schema',return_value=['2023-2024']) as schema:
            graph = self.console.graph_catalog('published-reports')
        self.assertEqual({row['name'] for row in graph['industries']},set(names))
        self.assertTrue(all(Path(call.args[0][0]).parent == self.reports for call in schema.call_args_list))

    def test_companion_data_book_files_under_its_industry_not_a_new_one(self):
        # The Data workbook is the tall view of the SAME export, published into
        # the same folder. Before the suffix was recognised it listed as an
        # industry literally called "Example Industry Spending Data", which put
        # a second, differently shaped entry beside every real industry in the
        # chart source picker.
        self.report('Example')
        data = self.reports / 'Example Industry Spending Data.xlsx'
        data.write_bytes(b'catalog only; workbook contents must not be read')
        rows = self.console.report_catalog('published-reports')['reports']
        self.assertEqual({r['industry'] for r in rows},{'Example'})
        self.assertEqual({r['kind'] for r in rows},{'overview','data'})
        self.assertEqual(
            next(r['name'] for r in rows if r['kind'] == 'data'),data.name)
        self.assertEqual(len({r['index'] for r in rows}),2)
        with patch.object(app,'graph_schema',return_value=['2023-2024']):
            graph = self.console.graph_catalog('published-reports')
        self.assertEqual([row['name'] for row in graph['industries']],['Example'])

    def test_index_is_stable_when_another_workbook_is_added(self):
        original = self.report('Banking')
        first = self.console.report_catalog('published-reports')['reports'][0]
        self.report('Alphabetically earlier')
        self.assertEqual(self.console.report_path('published-reports',first['index']),original)
        original.unlink()
        with self.assertRaises(ValueError): self.console.report_path('published-reports',first['index'])

    def test_cycle_snapshots_stay_distinct_in_report_and_graph_catalogs(self):
        self.report('Fixture')
        for cycle in ('2021-2022', '2023-2024'):
            for kind in ('Overview', 'Data'):
                path = self.reports / f'Fixture Industry Spending {kind} {cycle}.xlsx'
                path.write_bytes(b'catalog only')
        reports = self.console.report_catalog('published-reports')['reports']
        expected = {'Fixture', 'Fixture 2021-2022', 'Fixture 2023-2024'}
        self.assertEqual({row['industry'] for row in reports}, expected)
        with patch.object(app, 'graph_schema', return_value=['2023-2024']):
            graph = self.console.graph_catalog('published-reports')
        self.assertEqual({row['name'] for row in graph['industries']}, expected)
        selected = next(row for row in graph['industries'] if row['name'] == 'Fixture 2023-2024')
        self.assertTrue(all('2023-2024.xlsx' in self.console.report_path('published-reports', i).name
                            for i in selected['indices']))

    def test_legacy_outside_path_is_rejected_not_remapped_to_same_filename(self):
        inside = self.report('Banking')
        outside = self.root / inside.name
        outside.write_bytes(b'old workbook must not be read')
        run = self.legacy_run(outside)
        with (patch.object(self.console,'find',return_value=run),
             patch.object(self.console,'inspect_report',side_effect=AssertionError('must not read old workbook')),
             patch.object(self.console,'collection_coverage',return_value=[])):
            with self.assertRaisesRegex(ValueError,'outside'): self.console.report_path('old-run',0)
            self.assertFalse(self.console.report_catalog('old-run')['reports'])
            self.assertFalse(self.console.graph_catalog('old-run')['industries'])
            snapshot = self.console.snapshot('old-run')
            self.assertFalse(snapshot['reports'])
            self.assertIn('Historical report paths',snapshot['report_source_notice'])

    def test_graph_comparison_and_freshness_receive_only_authoritative_paths(self):
        path = self.report('Banking')
        with (patch.object(app,'graph_schema',return_value=['2023-2024']),
             patch.object(app,'graph_view',return_value={'reports':[{}]}) as graph,
             patch.object(app,'report_freshness',return_value={}) as freshness,
             patch.object(app,'comparison_subjects',return_value={}) as subjects,
             patch.object(app,'comparison_flows',return_value={}) as flows):
            self.console.graph('published-reports','Banking',{})
            self.console.comparison('published-reports','Banking')
            self.console.comparison('published-reports','Banking',True)
        for call in (graph.call_args,subjects.call_args,flows.call_args): self.assertEqual(call.args[0],[path])
        freshness.assert_called_once_with(path)

    def test_missing_folder_has_no_fallback(self):
        self.reports.rmdir()
        with self.assertRaisesRegex(ValueError,'no alternate'): self.console.published_reports()

    def test_symlink_escape_is_not_listed(self):
        path = self.report('Banking')
        with patch.object(app,'require_report_path',side_effect=ValueError('outside')):
            self.assertEqual(self.console.published_reports(),[])

    def test_child_output_and_environment_are_pinned(self):
        original_read = app.read_json
        # This pins the child's output directory, not the FEC bulk staging, and
        # those snapshots are not kept in git.
        with patch.object(app,'load_keys',return_value=['fixture-key']), \
             patch.object(app,'copy_fec_sources'), \
             patch.object(app,'read_json',side_effect=lambda path: {} if Path(path).name == 'active_fec_sources.json' else original_read(path)):
            roster = self.console.create_roster({'sector':'Fixture','companies':[{'name':'Example','aliases':[]}]})
            process = MagicMock(pid=987654);process.poll.return_value=None
            with patch.object(app.subprocess,'Popen',return_value=process) as launch,patch.object(app.threading,'Thread'):
                self.console.launch({'id':roster['id'],'cycles':[2024]})
        command = launch.call_args.args[0]
        self.assertEqual(Path(command[command.index('-OutputDirectory')+1]),self.reports)
        self.assertEqual(Path(launch.call_args.kwargs['env']['MENTAT_REPORT_DIRECTORY']),self.reports)

    def test_main_resolves_data_root_before_report_working_directory(self):
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir,previous)
        fake = MagicMock()
        with patch.object(sys,'argv',['server.py','--data-root','relative-state','--read-only']), \
             patch.object(app,'ThreadingHTTPServer',return_value=fake),patch.object(app,'Console') as console:
            app.main()
        self.assertEqual(Path.cwd(),self.reports)
        self.assertEqual(console.call_args.args[0],self.root / 'relative-state')


if __name__ == '__main__': unittest.main()
