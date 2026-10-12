"""Windows launcher integration: occupied port, concurrent start, and reuse."""
import concurrent.futures
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
import hashlib
import importlib.util
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

APP = Path(__file__).resolve().parents[1] / 'app'
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
import python_child  # noqa: E402  (the stale fixture is one process, as the launcher's servers are)


def stop_verified_server(health, directory):
    if health and Path(health['data_root']) == Path(directory):
        # Stop-Process directly opens the verified fixture PID; taskkill's
        # process enumeration can fail in a restricted Windows test host.
        subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                        f"Stop-Process -Id {int(health['pid'])} -Force -ErrorAction Stop"],
                       env=os.environ.copy(), capture_output=True, check=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)


STALE_SERVER = '''import json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
health = json.loads(sys.argv[1])
health['pid'] = os.getpid()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        body = json.dumps(health).encode() if self.path == '/api/health' else b'not found'
        self.send_response(200 if self.path == '/api/health' else 404)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
print(server.server_port, flush=True)
server.serve_forever()
'''


def code_tree_hash(app_directory):
    """Re-derived here on purpose: the launcher and the server must agree."""
    digest = hashlib.sha256()
    for path in sorted(app_directory.glob('*.py'), key=lambda item: item.name):
        digest.update(path.name.encode('utf-8'))
        digest.update(b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        digest.update(b'\0')
    return digest.hexdigest()


_server_module_cache = None


def _static_assets(app_directory):
    """The served-file list, read from server.py itself (its own source of
    truth for what an asset build covers) rather than duplicated by hand."""
    global _server_module_cache
    if _server_module_cache is None:
        spec = importlib.util.spec_from_file_location('desktop_launcher_test_server', app_directory / 'server.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _server_module_cache = module
    return _server_module_cache.STATIC_ASSETS


def asset_tree_hash(app_directory):
    """Re-derived here on purpose, same method as code_tree_hash: the
    launcher and the server must agree on what the on-disk assets hash to."""
    paths = sorted({app_directory / name for name, _ in _static_assets(app_directory).values()},
                    key=lambda item: item.name)
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode('utf-8'))
        digest.update(b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        digest.update(b'\0')
    return digest.hexdigest()


def start_stale_server(directory, busy=False, read_only=False, drop_busy=False, stale_code=True, stale_assets=True):
    """A real server.py process of this identity, running a different build.

    The launcher only replaces a process whose Win32_Process command line names
    server.py, so the fixture is written to a file with that name. on_disk_*_now
    always reports the true current on-disk hash - that is what a real process,
    old or new, would compute fresh on every health call - while stale_code /
    stale_assets control whether the *loaded* fields still lag behind it.
    """
    project = Path(__file__).resolve().parents[1]
    app_directory = project / 'app'
    report_directory = str(Path(json.loads((project / 'config/report-directory.json').read_text())['directory']).resolve())
    real_code_hash = code_tree_hash(app_directory)
    real_assets_hash = asset_tree_hash(app_directory)
    health = {'application': 'MENTAT', 'project': str(project), 'data_root': directory,
              'resolved_data_root': directory, 'report_directory': report_directory,
              'server_code_sha256': 'older-build',
              'code_sha256': 'older-build' if stale_code else real_code_hash,
              'assets_sha256': 'older-assets' if stale_assets else real_assets_hash,
              'on_disk_code_sha256_now': real_code_hash,
              'on_disk_assets_sha256_now': real_assets_hash,
              'read_only': read_only, 'busy': busy}
    if drop_busy:
        health.pop('busy')
    script = Path(directory) / 'stale' / 'server.py'
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(STALE_SERVER, encoding='utf-8')
    # One process (python_child), as the launcher's own servers are: the pid the fixture reports in its
    # health block is stale.pid, under Mentat's venv too.
    command, env = python_child.argv([sys.executable, str(script), json.dumps(health)])
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, text=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    port = int(process.stdout.readline().strip())
    process.stdout.close()
    with urlopen(f'http://127.0.0.1:{port}/api/health', timeout=5) as response:
        json.load(response)
    return process, port


def run_launcher(script, port, directory):
    with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as output:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script),
                                 '-NoBrowser', '-Port', str(port), '-DataRoot', directory],
                                cwd=directory, stdout=output, stderr=output, timeout=90)
        output.seek(0)
        return result.returncode, output.read()


@unittest.skipUnless(os.name == 'nt', 'Windows desktop launcher')
class DesktopLauncherTests(unittest.TestCase):
    def test_conflict_concurrent_start_and_reuse(self):
        script = Path(__file__).resolve().parents[1] / 'app/Start-MentatApp.ps1'
        with tempfile.TemporaryDirectory(prefix='MENTAT desktop ') as directory, socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            # Bound without listening: the port is unavailable without accepting HTTP.
            base = occupied.getsockname()[1]
            command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                       '-File', str(script), '-NoBrowser', '-Port', str(base),
                       '-DataRoot', directory]
            health = None
            def launch():
                # Detached Windows children can inherit pipe handles. Disk-backed
                # diagnostics let the launcher finish independently of its server.
                with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as output:
                    result = subprocess.run(command, stdout=output, stderr=output, timeout=70)
                    output.seek(0)
                    return result.returncode, output.read()
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda _: launch(), range(2)))
                for code, output in results:
                    self.assertEqual(code, 0, output)
                urls = [output.strip().splitlines()[-1].split('Mentat app: ')[-1] for _, output in results]
                self.assertEqual(urls[0], urls[1], 'Concurrent launches must reuse one server')
                self.assertNotEqual(urls[0], f'http://127.0.0.1:{base}')
                with urlopen(urls[0] + '/api/health', timeout=5) as response:
                    health = json.load(response)
                self.assertEqual(health['application'], 'MENTAT')
                self.assertEqual(Path(health['project']), script.parent.parent)
                self.assertEqual(Path(health['data_root']), Path(directory))
                self.assertEqual(health['server_code_sha256'], hashlib.sha256((script.parent/'server.py').read_bytes()).hexdigest())
                self.assertFalse(health['read_only'])
                with urlopen(urls[0] + '/coverage-view.js', timeout=5) as response:
                    self.assertIn(b'root.MentatCoverage = api', response.read())
                # Freeing the original port must not change this app's origin.
                occupied.close()
                code, output = launch()
                self.assertEqual(code, 0, output)
                with urlopen(urls[0] + '/api/health', timeout=5) as response:
                    self.assertEqual(json.load(response)['pid'], health['pid'])
                with urlopen(urls[0] + '/mentat-icon.svg', timeout=5) as response:
                    self.assertIn(b'#342649', response.read())
            finally:
                # Only terminate the isolated server whose identity/root we verified.
                stop_verified_server(health, directory)

    def test_old_and_read_only_servers_are_not_reused_from_another_working_directory(self):
        script=Path(__file__).resolve().parents[1]/'app/Start-MentatApp.ps1'
        project=script.parent.parent
        report_directory=json.loads((project/'config/report-directory.json').read_text())['directory']
        current_hash=hashlib.sha256((script.parent/'server.py').read_bytes()).hexdigest()
        for version in ({}, {'server_code_sha256':'old-build','read_only':False},
                        {'server_code_sha256':current_hash,'read_only':True}):
            with self.subTest(version=version), tempfile.TemporaryDirectory(prefix='MENTAT old server ') as directory:
                old_health=dict(application='MENTAT',project=str(project),data_root=directory,
                    resolved_data_root=directory,report_directory=report_directory,**version)
                class OldServer(BaseHTTPRequestHandler):
                    def log_message(self,*args):pass
                    def do_GET(self):
                        body=json.dumps(old_health).encode() if self.path=='/api/health' else b'not found'
                        self.send_response(200 if self.path=='/api/health' else 404)
                        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)))
                        self.end_headers();self.wfile.write(body)
                server=ThreadingHTTPServer(('127.0.0.1',0),OldServer)
                worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
                health=None
                try:
                    with tempfile.TemporaryFile(mode='w+',encoding='utf-8') as output:
                        result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),
                            '-NoBrowser','-Port',str(server.server_port),'-DataRoot',directory],
                            cwd=directory,stdout=output,stderr=output,timeout=70)
                        output.seek(0);text=output.read()
                    self.assertEqual(result.returncode,0,text)
                    url=text.strip().splitlines()[-1].split('Mentat app: ')[-1]
                    self.assertNotEqual(url,f'http://127.0.0.1:{server.server_port}')
                    with urlopen(url+'/api/health',timeout=5) as response:health=json.load(response)
                    self.assertEqual(health['server_code_sha256'],current_hash)
                    self.assertEqual(Path(health['data_root']),Path(directory))
                    self.assertFalse(health['read_only'])
                    with urlopen(url+'/coverage-view.js',timeout=5) as response:self.assertEqual(response.status,200)
                    with urlopen(f'http://127.0.0.1:{server.server_port}/api/health',timeout=5) as response:
                        self.assertEqual(json.load(response),old_health,'An older server may still own running work')
                finally:
                    stop_verified_server(health, directory)
                    server.shutdown();server.server_close();worker.join()

    def test_an_idle_stale_server_is_replaced_on_its_own_port(self):
        script = Path(__file__).resolve().parents[1] / 'app/Start-MentatApp.ps1'
        with tempfile.TemporaryDirectory(prefix='MENTAT stale ') as directory:
            stale, port = start_stale_server(directory, busy=False)
            health = None
            try:
                code, text = run_launcher(script, port, directory)
                self.assertEqual(code, 0, text)
                self.assertIn('Stopping stale MENTAT server pid ' + str(stale.pid), text)
                url = text.strip().splitlines()[-1].split('Mentat app: ')[-1]
                self.assertEqual(url, f'http://127.0.0.1:{port}',
                                 'an idle stale server must give its own port back\n' + text)
                with urlopen(url + '/api/health', timeout=5) as response:
                    health = json.load(response)
                self.assertEqual(health['code_sha256'], code_tree_hash(script.parent))
                self.assertNotEqual(health['pid'], stale.pid)
                self.assertIsNotNone(stale.poll(), 'the stale server must have been stopped')
            finally:
                stop_verified_server(health, directory)
                if stale.poll() is None:
                    stale.kill()
                stale.wait(timeout=15)

    def test_an_idle_server_with_current_code_but_stale_assets_is_replaced_on_its_own_port(self):
        """A JS/CSS-only change must not be mistaken for a current server
        just because the Python code hash still matches on disk."""
        script = Path(__file__).resolve().parents[1] / 'app/Start-MentatApp.ps1'
        with tempfile.TemporaryDirectory(prefix='MENTAT stale assets ') as directory:
            stale, port = start_stale_server(directory, busy=False, stale_code=False)
            health = None
            try:
                code, text = run_launcher(script, port, directory)
                self.assertEqual(code, 0, text)
                self.assertIn('Stopping stale MENTAT server pid ' + str(stale.pid), text)
                url = text.strip().splitlines()[-1].split('Mentat app: ')[-1]
                self.assertEqual(url, f'http://127.0.0.1:{port}',
                                 'an idle assets-stale server must give its own port back\n' + text)
                with urlopen(url + '/api/health', timeout=5) as response:
                    health = json.load(response)
                self.assertEqual(health['code_sha256'], code_tree_hash(script.parent))
                self.assertNotEqual(health['pid'], stale.pid)
                self.assertIsNotNone(stale.poll(), 'the stale server must have been stopped')
            finally:
                stop_verified_server(health, directory)
                if stale.poll() is None:
                    stale.kill()
                stale.wait(timeout=15)

    def test_a_busy_or_read_only_stale_server_is_left_running(self):
        script = Path(__file__).resolve().parents[1] / 'app/Start-MentatApp.ps1'
        for label, extra in (('busy', {'busy': True}), ('read-only', {'read_only': True, 'busy': False}),
                             ('no busy field', {'drop_busy': True})):
            with self.subTest(server=label), tempfile.TemporaryDirectory(prefix='MENTAT keep ') as directory:
                stale, port = start_stale_server(directory, **extra)
                health = None
                try:
                    code, text = run_launcher(script, port, directory)
                    self.assertEqual(code, 0, text)
                    self.assertNotIn('Stopping stale MENTAT server', text)
                    url = text.strip().splitlines()[-1].split('Mentat app: ')[-1]
                    self.assertNotEqual(url, f'http://127.0.0.1:{port}',
                                        'this server may still own running work\n' + text)
                    self.assertIsNone(stale.poll(), 'this server must still be running')
                    with urlopen(f'http://127.0.0.1:{port}/api/health', timeout=5) as response:
                        self.assertEqual(json.load(response)['pid'], stale.pid)
                    with urlopen(url + '/api/health', timeout=5) as response:
                        health = json.load(response)
                    self.assertEqual(health['code_sha256'], code_tree_hash(script.parent))
                finally:
                    stop_verified_server(health, directory)
                    if stale.poll() is None:
                        stale.kill()
                    stale.wait(timeout=15)


if __name__ == '__main__':
    unittest.main()
