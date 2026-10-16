"""tools/replay_proxy.py (D-180) against the real clients: a hand-built cassette answers api_request_pool.request and pac_evidence.Client.page
through the proxy, a miss is a 502 and exit 3, an unknown host is refused, loopback bypasses the proxy, a recording never lets the key reach the
cassette, and R's curl and httr2 are served the same way. Read-only use of the collection modules; fake keys only; no real FEC request."""
import hashlib
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import replay_proxy as rp  # noqa: E402

try:
    import cryptography  # noqa: F401
except ImportError:
    cryptography = None
RSCRIPT = Path(os.environ.get('MENTAT_RSCRIPT') or 'C:/Program Files/R/R-4.6.0/bin/Rscript.exe')
FEC = 'https://api.open.fec.gov/v1/'
KEY = 'fake-key-0123456789abcdef'

POOL_AND_PAGE = '''
import json, sys
sys.path.insert(0, %r)
import api_request_pool, pac_evidence
pool = api_request_pool.request({'params': {'committee_id': 'C00000001', 'per_page': 100}}, 'replay-dummy-0001')
page = pac_evidence.Client(%r, key='replay-dummy-0001').page('committee/C00000001/totals/', {'cycle': 2024, 'per_page': 100})
print(json.dumps([pool['status'], json.loads(pool['body']), page]))
'''
TRY_URL = '''
import sys, urllib.request, urllib.error
try:
    print(urllib.request.urlopen(sys.argv[1], timeout=20).read().decode())
except urllib.error.HTTPError as error:
    print(error.code, error.headers.get('X-Replay-Miss'))
except Exception as error:
    print('refused', type(error).__name__)
'''


class Proxy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not rp.find_http_replay():
            raise unittest.SkipTest('Melange http_replay.py not found; set MELANGE_HTTP_REPLAY')
        cls.module, cls.source = rp.load_http_replay()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def cassette(self, *entries):
        """entries: (url, body bytes) in call order; the log format http_replay's proxy reads."""
        folder = self.base / 'cassette'
        (folder / 'bodies').mkdir(parents=True)
        lines = []
        for seq, (url, body) in enumerate(entries):
            digest = hashlib.sha256(body).hexdigest()
            (folder / 'bodies' / digest).write_bytes(body)
            norm = self.module.normalize_url(url)
            lines.append(json.dumps({'v': 1, 'seq': seq, 'kind': 'http', 'key': 'GET ' + norm + ' -', 'error': None, 'request': {'method': 'GET', 'url': norm},
                                     'response': {'status': 200, 'reason': 'OK', 'url': norm, 'raised': False, 'headers': [['Content-Type', 'application/json']],
                                                  'body_sha256': digest, 'body_len': len(body)}}))
        (folder / 'cassette.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
        return folder

    def mint(self):
        if cryptography is None:
            self.skipTest('cryptography is not installed (the host mints the certificate)')
        rp.mint(self.base / 'tls')
        return self.base / 'tls'

    def child(self, name, source):
        path = self.base / name
        path.write_text(source, encoding='utf-8', newline='\n')
        return path

    def replay(self, cassette, command, *more):
        report = self.base / 'report.json'
        env = {k: v for k, v in os.environ.items() if not k.lower().endswith('_proxy')}
        done = subprocess.run([sys.executable, str(ROOT / 'tools' / 'replay_proxy.py'), 'replay', str(cassette), '--tls', str(self.base / 'tls'), '--allow-unused',
                               '--http-replay', str(self.source), '--report', str(report), *more, '--', *command], capture_output=True, text=True, env=env, timeout=180)
        return done, json.loads(report.read_text(encoding='utf-8'))


class ReplayTests(Proxy):
    def setUp(self):
        super().setUp()
        self.mint()

    def test_the_real_pool_and_evidence_clients_are_served_and_the_report_counts_them(self):
        pool_body, page_body = {'results': [{'sub_id': '1'}], 'pagination': {'count': 1}}, {'results': [{'cycle': 2024}]}
        cassette = self.cassette((FEC + 'schedules/schedule_a/?committee_id=C00000001&per_page=100', json.dumps(pool_body).encode()),
                                 (FEC + 'committee/C00000001/totals/?cycle=2024&per_page=100', json.dumps(page_body).encode()))
        script = self.child('pool_and_page.py', POOL_AND_PAGE % (str(ROOT / 'tools'), str(self.base / 'evidence')))
        done, report = self.replay(cassette, [sys.executable, str(script)])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), [200, pool_body, page_body])
        self.assertEqual((report['served'], report['misses'], report['unused'], report['refused_tls_hosts']), (2, [], [], []))
        self.assertEqual(sorted(report['served_per_key'].values()), [1, 1])
        self.assertEqual(report['http_replay_sha256'], hashlib.sha256(Path(self.source).read_bytes()).hexdigest())
        self.assertEqual(report['cassette_sha256'], rp.cassette_digest(cassette))

    def test_a_request_the_cassette_lacks_is_a_502_and_the_run_exits_3(self):
        cassette = self.cassette((FEC + 'a/', b'{}'))
        script = self.child('try.py', TRY_URL)
        done, report = self.replay(cassette, [sys.executable, str(script), FEC + 'never-recorded/'])
        self.assertEqual((done.returncode, done.stdout.split()), (3, ['502', '1']), done.stderr)
        self.assertEqual(len(report['misses']), 1)
        self.assertIn('never-recorded', report['misses'][0])
        self.assertEqual(len(report['unused']), 1)                                              # the entry nobody asked for, allowed by --allow-unused

    def test_a_host_without_a_minted_certificate_is_refused(self):
        script = self.child('try.py', TRY_URL)
        done, report = self.replay(self.cassette((FEC + 'a/', b'{}')), [sys.executable, str(script), 'https://example.invalid/anything'])
        self.assertEqual(done.stdout.split()[0], 'refused', done.stderr)
        self.assertEqual(report['refused_tls_hosts'], ['example.invalid'])
        self.assertEqual((done.returncode, report['exit_code']), (3, 3))                        # the child swallowed the refusal and exited 0; the run still fails

    def test_the_cassette_digest_sees_a_body_edited_in_place(self):
        cassette = self.cassette((FEC + 'a/', b'{"results": [1]}'))
        before = rp.cassette_digest(cassette)
        self.assertEqual(rp.cassette_digest(cassette), before)
        body = next((cassette / 'bodies').iterdir())
        body.write_bytes(b'{"results": [2]}')                                                   # same name, other content
        self.assertNotEqual(rp.cassette_digest(cassette), before)

    def test_loopback_services_bypass_the_proxy(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', '4')
                self.end_headers()
                self.wfile.write(b'pool')

            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        script = self.child('try.py', TRY_URL)
        done, report = self.replay(self.cassette((FEC + 'a/', b'{}')), [sys.executable, str(script), 'http://127.0.0.1:%d/batch' % server.server_port])
        self.assertEqual((done.returncode, done.stdout.strip()), (0, 'pool'), done.stderr)
        self.assertEqual((report['served'], report['misses']), (0, []))

    @unittest.skipUnless(RSCRIPT.is_file(), 'no Rscript')
    def test_r_curl_and_httr2_are_served_with_the_openssl_backend(self):
        url = FEC + 'committee/C00000001/totals/?cycle=2024&per_page=100'
        script = self.child('fetch.R', '\n'.join([
            'for (p in c("curl", "httr2")) if (!requireNamespace(p, quietly = TRUE)) quit(status = 77)',
            'url <- "%s"' % url,
            'handle <- curl::new_handle(); curl::handle_setheaders(handle, "X-Api-Key" = "replay-dummy-0001")',
            'cat("curl", curl::curl_fetch_memory(url, handle)$status_code, "\\n")',
            'response <- httr2::req_perform(httr2::req_headers(httr2::request(url), "X-Api-Key" = "replay-dummy-0001"))',
            'cat("httr2", httr2::resp_status(response), httr2::resp_body_string(response), "\\n")']))
        cassette = self.cassette((url, b'{"results": []}'), (url, b'{"results": []}'))
        done, report = self.replay(cassette, [str(RSCRIPT), str(script)])
        if done.returncode == 77:
            self.skipTest('R curl/httr2 not installed')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('curl 200', done.stdout)
        self.assertIn('httr2 200 {"results": []}', done.stdout)
        self.assertEqual((report['served'], report['misses']), (2, []))


class RecordTests(Proxy):
    def test_the_key_stays_out_of_every_cassette_byte_while_the_client_sees_the_live_reply(self):
        if cryptography is None:
            self.skipTest('cryptography is not installed (record runs on the host)')

        class Echo(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({'you_sent': self.headers.get('X-Api-Key')}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Content-Type', 'application/json')
                self.send_header('X-Echo', self.headers.get('X-Api-Key'))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Echo)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        keys = self.child('keys.txt', KEY + '\n')
        script = self.child('call.py', 'import sys, urllib.request\n'
                            'r = urllib.request.Request(sys.argv[1], headers={"X-Api-Key": %r})\n'
                            'print(urllib.request.urlopen(r, timeout=20).read().decode())\n' % KEY)
        cassette, report = self.base / 'tape', self.base / 'report.json'
        env = {k: v for k, v in os.environ.items() if not k.lower().endswith('_proxy')}
        done = subprocess.run([sys.executable, str(ROOT / 'tools' / 'replay_proxy.py'), 'record', str(cassette), '--secret-file', str(keys), '--http-replay', str(self.source),
                               '--report', str(report), '--no-proxy', '', '--', sys.executable, str(script), 'http://127.0.0.1:%d/v1/x?per_page=1' % server.server_port],
                              capture_output=True, text=True, env=env, timeout=180)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(KEY, done.stdout)                                                         # the client was handed the unscrubbed reply
        files = [cassette / 'cassette.jsonl', *(cassette / 'bodies').glob('*')]
        for blob in (f.read_bytes() for f in files):
            self.assertNotIn(KEY.encode(), blob)
        self.assertIn(b'REDACTED', b''.join(f.read_bytes() for f in files))
        data = json.loads(report.read_text(encoding='utf-8'))
        self.assertEqual((data['mode'], data['interactions'], data['secret_leak']), ('record', 1, None))
        self.assertEqual(data['cassette_sha256'], rp.cassette_digest(cassette))

    def test_mint_writes_the_certificates_and_never_a_ca_key(self):
        if cryptography is None:
            self.skipTest('cryptography is not installed')
        rp.mint(self.base / 'tls')
        names = sorted(p.name for p in (self.base / 'tls').iterdir())
        self.assertEqual(names, ['ca.pem', 'hosts.txt', 'leaf.pem'])
        self.assertEqual((self.base / 'tls' / 'hosts.txt').read_text(), 'api.open.fec.gov\n')
        self.assertNotIn('PRIVATE KEY', (self.base / 'tls' / 'ca.pem').read_text())
        self.assertEqual((self.base / 'tls' / 'leaf.pem').read_text().count('PRIVATE KEY-----'), 2)   # one BEGIN and one END: the leaf's key only
        with self.assertRaises(FileExistsError):
            rp.mint(self.base / 'tls')


if __name__ == '__main__':
    unittest.main()
