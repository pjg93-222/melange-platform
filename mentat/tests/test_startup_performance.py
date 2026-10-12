"""Cold launcher probes against synthetic local servers; never start a collector."""
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import Request, urlopen

PROJECT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT / 'app/Start-MentatApp.ps1'
CHOOSER = PROJECT / 'tools/mentat_python.psm1'
STUB_SERVER = '''import argparse,hashlib,json,os,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import openpyxl
p=argparse.ArgumentParser()
p.add_argument('--port',type=int)
p.add_argument('--data-root')
args=p.parse_args()
Path(args.data_root,'server-%d.pid'%args.port).write_text(str(os.getpid()),encoding='utf-8')
folder=Path(__file__).resolve().parent
def tree(code):
 # A self-consistent stand-in for server.py's code and asset tree digests: the
 # launcher only asks whether a server's latched build still matches its folder.
 digest=hashlib.sha256()
 for f in sorted(x for x in folder.iterdir() if x.is_file() and (x.suffix=='.py')==code):
  digest.update(f.name.encode()+hashlib.sha256(f.read_bytes()).digest())
 return digest.hexdigest()
health=dict(application='MENTAT',project=str(Path(__file__).resolve().parents[1]),
 data_root=args.data_root,report_directory=os.environ['MENTAT_REPORT_DIRECTORY'],
 server_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),read_only=False,
 busy=False,code_sha256=tree(True),assets_sha256=tree(False),pid=os.getpid())
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  body=json.dumps(dict(health,on_disk_code_sha256_now=tree(True),
   on_disk_assets_sha256_now=tree(False))).encode()
  self.send_response(200)
  self.send_header('Content-Type','application/json')
  self.send_header('Content-Length',str(len(body)))
  self.end_headers()
  self.wfile.write(body)
 def do_POST(self):
  if self.path!='/test-only/stop':
   self.send_error(404)
   return
  self.send_response(200)
  self.send_header('Content-Length','0')
  self.end_headers()
  threading.Thread(target=self.server.shutdown,daemon=True).start()
ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
'''


def fixture_project(directory, source=None):
    root = Path(directory)
    for folder in ('app', 'config', 'reports', 'tools'):
        (root / folder).mkdir()
    (root / 'app/Start-MentatApp.ps1').write_text(source or LAUNCHER.read_text(), encoding='utf-8')
    # The launcher's interpreter chooser: with it a venv's python.exe starts as one process
    # (Get-MentatPythonLaunch), so the fixture server's pid and log files are its own.
    shutil.copyfile(CHOOSER, root / 'tools/mentat_python.psm1')
    (root / 'app/server.py').write_text(STUB_SERVER, encoding='utf-8')
    (root / 'config/report-directory.json').write_text(json.dumps({
        'schema_version': 1, 'directory': str(root / 'reports')}), encoding='utf-8')
    return root


def launch_fixture(root, port):
    environment = os.environ.copy()
    environment.pop('MENTAT_REPORT_DIRECTORY', None)
    with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as output:
        started = time.perf_counter()
        result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
            '-ExecutionPolicy', 'Bypass', '-File', str(root / 'app/Start-MentatApp.ps1'),
            '-NoBrowser', '-Port', str(port), '-PythonPath', sys.executable, '-DataRoot', str(root / 'console')],
            cwd=root, env=environment, stdout=output, stderr=output, timeout=35)
        elapsed = time.perf_counter() - started
        output.seek(0)
        text = output.read()
    if result.returncode:
        raise AssertionError(text)
    url = text.strip().splitlines()[-1].split('Mentat app: ')[-1]
    with urlopen(url + '/api/health', timeout=5) as response:
        health = json.load(response)
    assert Path(health['project']) == root
    assert Path(health['data_root']) == root / 'console'
    assert Path(health['report_directory']) == root / 'reports'
    assert health['server_code_sha256'] == hashlib.sha256((root / 'app/server.py').read_bytes()).hexdigest()
    assert health['read_only'] is False
    return {'url': url, 'pid': health['pid'], 'seconds': elapsed}


def fixture_servers(root):
    """(pid, port) for every server this fixture recorded under its own data root."""
    servers = []
    for receipt in sorted((root / 'console').glob('server-*.pid')):
        try:
            servers.append((int(receipt.read_text()), int(receipt.stem.rsplit('-', 1)[-1])))
        except (OSError, ValueError):
            pass
    return servers


def wait_for_exit(pid, seconds):
    """True once the process is gone. Until then it holds its log file open."""
    handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return True
    try:
        return ctypes.windll.kernel32.WaitForSingleObject(handle, int(seconds * 1000)) == 0
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def owns_fixture_process(pid, root):
    # A pid is only ever terminated once its command line still names this
    # fixture's own temporary folder, so a reused pid is left alone.
    listing = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command',
        '[string]((Get-CimInstance Win32_Process -Filter "ProcessId = %d").CommandLine)'
        '.Contains($env:MENTAT_FIXTURE_ROOT)' % pid], capture_output=True, text=True,
        env=dict(os.environ, MENTAT_FIXTURE_ROOT=str(root)), timeout=20)
    return listing.stdout.strip() == 'True'


def stop_fixture(root):
    """Stop every server this fixture started, before its folder is removed."""
    for pid, port in fixture_servers(root):
        url = 'http://127.0.0.1:%d' % port
        try:
            # Identity first: only this fixture's own server is asked to stop.
            with urlopen(url + '/api/health', timeout=5) as response:
                health = json.load(response)
            if health['pid'] == pid and Path(health['project']) == root:
                with urlopen(Request(url + '/test-only/stop', data=b'', method='POST'), timeout=5):
                    pass
        except (OSError, ValueError, KeyError):
            pass
        if wait_for_exit(pid, 5) or not owns_fixture_process(pid, root):
            continue
        subprocess.run(['taskkill.exe', '/PID', str(pid), '/T', '/F'], capture_output=True, timeout=20)
        if not wait_for_exit(pid, 10):
            raise AssertionError('fixture server pid %d still holds %s' % (pid, root))


@unittest.skipUnless(os.name == 'nt', 'Windows launcher')
class StartupPerformanceTests(unittest.TestCase):
    def test_tcp_probe_does_not_wait_for_http_on_nonlisteners(self):
        with socket.socket() as bound, socket.socket() as listener:
            bound.bind(('127.0.0.1', 0))
            listener.bind(('127.0.0.1', 0))
            listener.listen(4)
            code = """
$text=Get-Content -LiteralPath '%s' -Raw
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$tokens,[ref]$errors)
$definition=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Test-MentatPortListening'},$true))
. ([scriptblock]::Create($definition[0].Extent.Text))
function Invoke-RestMethod {throw 'TCP probes must not send HTTP.'}
$watch=[Diagnostics.Stopwatch]::StartNew()
$closed=Test-MentatPortListening %d
$open=Test-MentatPortListening %d
[pscustomobject]@{closed=$closed;open=$open;seconds=$watch.Elapsed.TotalSeconds}|ConvertTo-Json -Compress
""" % (str(LAUNCHER).replace("'", "''"), bound.getsockname()[1], listener.getsockname()[1])
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                base64.b64encode(code.encode('utf-16le')).decode()], env=os.environ.copy(),
                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertFalse(value['closed'])
        self.assertTrue(value['open'])
        self.assertLess(value['seconds'], 1.5)

    def test_cold_start_and_saved_port_reuse_have_matching_identity(self):
        temporary = Path(tempfile.mkdtemp(prefix='Mentat startup fixture '))
        # Cleanups run in reverse: every server started inside the fixture is
        # stopped first, because each holds a log file open in that folder.
        self.addCleanup(shutil.rmtree, temporary)
        self.addCleanup(stop_fixture, temporary)
        root = fixture_project(temporary)
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1', 0))
            port = reserve.getsockname()[1]
        result = launch_fixture(root, port)
        again = launch_fixture(root, port)
        self.assertEqual(again['url'], result['url'])
        self.assertEqual(again['pid'], result['pid'])


if __name__ == '__main__':
    unittest.main()
