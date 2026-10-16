from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop_figure_renderer import install


class BadRequest(Exception): pass
class BrowserMissing(Exception): pass
class RenderFailed(Exception): pass
class RenderTimeout(Exception): pass


class RenderTests(unittest.TestCase):
    def test_fixed_loopback_authenticated_bytes_and_no_redirects(self):
        received=[]
        png=b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\x00IEND\xaeB`\x82'
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                if self.headers.get('Authorization') != 'Bearer '+'x'*40:
                    self.send_error(403); return
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append(payload)
                if payload['document']=='redirect':
                    self.send_response(302); self.send_header('Location', '/other'); self.end_headers(); return
                self.send_response(200); self.send_header('Content-Type','image/png'); self.end_headers(); self.wfile.write(png)
            def do_GET(self):
                received.append('redirect-followed'); self.send_error(500)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        module=SimpleNamespace(MIN_PAGE_PX=100,MAX_PAGE_PX=19000,MAX_REQUEST_BYTES=40*1024*1024,RENDER_TIMEOUT_SECONDS=3,
                               PNG_SIGNATURE=png[:8],PNG_END=png[8:],BadRequest=BadRequest,BrowserMissing=BrowserMissing,
                               RenderTimeout=RenderTimeout,RenderFailed=RenderFailed)
        install(module)
        try:
            with patch.dict(os.environ,{'MENTAT_DESKTOP_RENDER_URL':f'http://127.0.0.1:{server.server_port}/render','MENTAT_DESKTOP_RENDER_TOKEN':'x'*40}):
                self.assertEqual(module.render('<p>synthetic</p>',200,100),png)
                self.assertEqual(received[-1]['scale'],2)
                with self.assertRaises(BadRequest):module.render('x',19000,19000)
                with self.assertRaises(RenderFailed):module.render('redirect',200,100)
                self.assertNotIn('redirect-followed',received)
                with patch.dict(os.environ,{'MENTAT_DESKTOP_RENDER_URL':'http://example.invalid/render'}):
                    with self.assertRaises(BrowserMissing):module.render('x',200,100)
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
