"""Actual passive Console/Handler seam, with generated files only."""
from contextlib import contextmanager, redirect_stdout
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from test_public_data import fixture, prepare

APP=Path(__file__).resolve().parents[1]/'app'
sys.path.insert(0,str(APP))
spec=importlib.util.spec_from_file_location('public_data_http_server',APP/'server.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)


class PublicDataHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);source=self.root/'source';source.mkdir();fixture(source,101)
        self.catalogue=prepare(source);self.reports=self.root/'original-reports';self.reports.mkdir()
        with patch.object(app,'report_directory',return_value=self.reports),patch.object(app,'environment_keys',return_value=[]):
            self.console=app.Console(self.root/'original-console',read_only=True,public_data=self.catalogue)
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);self.server.app=self.console
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown();self.server.server_close();self.worker.join(5)

    def request(self,path):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=8)
        try:
            connection.request('GET',path)
            response=connection.getresponse();raw=response.read()
            value=json.loads(raw) if response.getheader('Content-Type')=='application/json' else raw
            return response.status,dict(response.getheaders()),value
        finally:connection.close()

    def test_actual_catalogue_rows_provenance_and_exact_download(self):
        status,_,value=self.request('/api/public-data/catalogue')
        self.assertEqual((status,len(value['tables'])),(200,12))
        status,_,value=self.request('/api/public-data/rows?table=mentat.fec.cm.2022&page=1')
        self.assertEqual((status,len(value['rows']),value['firstRow']),(200,1,101))
        status,_,value=self.request('/api/public-data/provenance?table=mentat.fec.cm.2022')
        self.assertEqual((status,value['document']['table']),(200,'cm'))
        status,headers,value=self.request('/api/public-data/download?table=mentat.fec.cm.2022')
        self.assertEqual(status,200);self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
        self.assertEqual(headers['Content-Disposition'],'attachment; filename="cm-2022.txt"')
        self.assertEqual(value,(self.root/'source/fec/2022/cm.psv').read_bytes())
        self.assertEqual(int(headers['Content-Length']),len(value))

    def test_ordinary_unavailable_context_does_not_probe_a_source(self):
        self.console.public_data=None
        with patch.object(app.Path,'lstat',side_effect=AssertionError('no source probes')):
            status,_,value=self.request('/api/public-data/catalogue')
        self.assertEqual(status,503);self.assertFalse(value['available'])

    def test_same_size_mutation_after_precheck_never_sends_success_headers(self):
        source=self.root/'source/fec/2022/cm.psv'
        original=source.read_bytes();download=self.catalogue.download;statuses=[]
        send_response=app.Handler.send_response
        @contextmanager
        def changed(catalogue,role):
            self.assertIs(catalogue,self.catalogue)
            with download(role) as value:
                # The real reader's initial full hash already passed. Mutate
                # before iterator consumption and its final context check.
                source.write_bytes(b'X'+original[1:])
                yield value
        def response(handler,status,*args,**kwargs):
            statuses.append(status)
            return send_response(handler,status,*args,**kwargs)
        with patch.object(type(self.catalogue),'download',changed),patch.object(app.Handler,'send_response',response):
            status,_,value=self.request('/api/public-data/download?table=mentat.fec.cm.2022')
        self.assertNotEqual(status,200)
        self.assertNotIn(200,statuses)
        self.assertIsInstance(value,dict)
        self.assertIn('error',value)
        self.assertEqual(source.stat().st_size,len(original))

    def test_success_headers_follow_final_context_verification_and_source_reads(self):
        events=[];download=self.catalogue.download;send_response=app.Handler.send_response
        @contextmanager
        def tracked(catalogue,role):
            self.assertIs(catalogue,self.catalogue)
            with download(role) as (metadata,chunks):
                def source_chunks():
                    for chunk in chunks:
                        events.append('source-read')
                        yield chunk
                yield metadata,source_chunks()
            events.append('verified-and-closed')
        def response(handler,status,*args,**kwargs):
            if status==200:events.append('success-headers')
            return send_response(handler,status,*args,**kwargs)
        with patch.object(type(self.catalogue),'download',tracked),patch.object(app.Handler,'send_response',response):
            status,headers,value=self.request('/api/public-data/download?table=mentat.fec.cm.2022')
        self.assertEqual(status,200)
        self.assertEqual(value,(self.root/'source/fec/2022/cm.psv').read_bytes())
        self.assertEqual(int(headers['Content-Length']),len(value))
        self.assertEqual(events[-2:],['verified-and-closed','success-headers'])
        self.assertTrue(events[:-2])
        self.assertTrue(all(event=='source-read' for event in events[:-2]))

    def test_after_verified_context_mutation_cannot_change_immutable_response(self):
        source=self.root/'source/fec/2022/cm.psv';original=source.read_bytes()
        download=self.catalogue.download;finished=[]
        @contextmanager
        def changed_after_verification(catalogue,role):
            self.assertIs(catalogue,self.catalogue)
            with download(role) as value:
                yield value
            # The real source handle and its final checks have completed.
            source.write_bytes(b'X'+original[1:])
            finished.append(True)
        with patch.object(type(self.catalogue),'download',changed_after_verification):
            status,headers,value=self.request('/api/public-data/download?table=mentat.fec.cm.2022')
        self.assertEqual((status,value),(200,original))
        self.assertEqual(int(headers['Content-Length']),len(original))
        self.assertEqual(finished,[True])
        self.assertNotEqual(source.read_bytes(),value)

    def test_declared_bound_total_and_immutable_chunk_types_refuse_before_success(self):
        import public_data
        send_response=app.Handler.send_response
        cases=[(True,[b'x']),(-1,[]),(public_data.MAX_FILE+1,[]),
               (2,[b'abc']),(3,[b'ab']),(2,[bytearray(b'ab')]),
               (2,[memoryview(b'ab')]),(0,[b'']),(65537,[b'x'*65537]),(3,[b'a',b'b',b'c'])]
        for declared,chunks in cases:
            with self.subTest(declared=declared,chunkType=type(chunks[0]).__name__ if chunks else None):
                statuses=[]
                @contextmanager
                def bounded(catalogue,role):
                    self.assertIs(catalogue,self.catalogue)
                    yield {'filename':'cm-2022.txt','bytes':declared},iter(chunks)
                def response(handler,status,*args,**kwargs):
                    statuses.append(status)
                    return send_response(handler,status,*args,**kwargs)
                with patch.object(type(self.catalogue),'download',bounded),patch.object(app.Handler,'send_response',response):
                    status,_,value=self.request('/api/public-data/download?table=mentat.fec.cm.2022')
                self.assertNotEqual(status,200)
                self.assertNotIn(200,statuses)
                self.assertIn('error',value)

    def test_actual_empty_download_keeps_exact_zero_length_response(self):
        fixture(self.root/'source',0)
        self.catalogue=prepare(self.root/'source');self.console.public_data=self.catalogue
        status,headers,value=self.request('/api/public-data/download?table=mentat.fec.cm.2022')
        self.assertEqual((status,value,int(headers['Content-Length'])),(200,b'',0))

    def test_unknown_duplicate_and_targeting_parameters_are_refused(self):
        for query in ('table=../private','table=mentat.fec.cm.2022&party=R',
                'table=mentat.fec.cm.2022&table=mentat.fec.cm.2024','table=mentat.fec.cm.2022&page=-1',
                'table=mentat.fec.cm.2022&page=','table=mentat.fec.cm.2022&name=fixture'):
            with self.subTest(query=query):self.assertEqual(self.request('/api/public-data/rows?'+query)[0],400)
        self.assertEqual(self.request('/api/public-data/unknown')[0],404)

    def test_explicit_capability_preserves_original_private_roots_and_default(self):
        self.assertIs(self.console.public_data,self.catalogue)
        self.assertIs(self.console.report_directory,self.reports)
        self.assertEqual(self.console.root,(self.root/'original-console').resolve())
        self.assertEqual(list(self.reports.iterdir()),[])
        with patch.object(app,'report_directory',return_value=self.reports),patch.object(app,'environment_keys',return_value=[]):
            ordinary=app.Console(self.root/'ordinary',read_only=True)
            self.assertIsNone(ordinary.public_data)
            with self.assertRaises(ValueError):app.Console(self.root/'forbidden',public_data={'complete':True})
        self.assertFalse((self.root/'forbidden').exists())

    def test_main_hook_once_after_original_context_attachment_before_serving(self):
        self._main_hook(False)

    def test_main_hook_failure_closes_new_server_without_readiness_or_serve(self):
        self._main_hook(True)

    def _main_hook(self,fail):
        calls=[];output=io.StringIO()
        server=SimpleNamespace(server_address=('127.0.0.1',12345),server_port=12345,
                               serve_forever=Mock(),server_close=Mock())
        config=SimpleNamespace(enforced=False,bind_host='127.0.0.1')
        def ready(current):
            calls.append(current)
            self.assertIs(current.app.public_data,self.catalogue)
            self.assertIs(current.app.report_directory,self.reports)
            self.assertEqual(current.app.root,(self.root/'main-console').resolve())
            current.serve_forever.assert_not_called()
            self.assertEqual(output.getvalue(),'')
            if fail:raise ValueError('fixture native install refused')
        fake_modules={'source_memo':SimpleNamespace(install=lambda:None),
                      'analysis_data':SimpleNamespace(HINTS_ENABLED=False),
                      'prewarm':SimpleNamespace(AccessRecorder=SimpleNamespace(start=lambda **_:None))}
        with patch.object(sys,'argv',['server','--port','0','--data-root',str(self.root/'main-console')]),\
             patch.object(app,'report_directory',return_value=self.reports),\
             patch.object(app,'environment_keys',return_value=[]),\
             patch.object(app.os,'chdir'),patch.object(app,'console_server',return_value=server),\
             patch.object(app.access_gate,'require_config_or_exit',return_value=config),\
             patch.object(app.access_gate,'assert_bind_consistent'),\
             patch.dict(os.environ,{'MENTAT_PREWARM':'0','MENTAT_STATE_WRITE_ROOT':''}),\
             patch.dict(sys.modules,fake_modules),redirect_stdout(output):
            if fail:
                with self.assertRaisesRegex(ValueError,'fixture native install refused'):
                    app.main(public_data=self.catalogue,on_server_ready=ready)
            else:app.main(public_data=self.catalogue,on_server_ready=ready)
        self.assertEqual(calls,[server])
        if fail:
            server.server_close.assert_called_once_with();server.serve_forever.assert_not_called()
            self.assertEqual(output.getvalue(),'')
        else:
            server.server_close.assert_not_called();server.serve_forever.assert_called_once_with()


if __name__=='__main__':unittest.main()
