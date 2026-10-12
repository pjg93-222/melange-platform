"""Open Melange without depending on Windows PowerShell script policy."""
import argparse
import ctypes
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

SCHEMAS = {'melange-staffer-live-v1', 'melange-staffer-live-v2'}


def health(port, timeout=5):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f'http://127.0.0.1:{port}/api/staffer-live?limit=1', timeout=timeout) as response:
        result = json.load(response)
    if result.get('schema') not in SCHEMAS:
        raise ValueError(f'Port {port} is not serving Melange; no process was stopped.')
    return result


def ensure_service(settings):
    root = Path(settings['source_root']).resolve()
    port = settings.get('port', 8841)
    dashboard = settings.get('dashboard_file', 'Moneyball_Redesign.html')
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Invalid local service port')
    if dashboard not in {'Moneyball.html', 'Moneyball_Redesign.html'}:
        raise ValueError('Unsupported dashboard artifact')
    script = root / 'fec/surfaces/staffer_live_server.py'
    if not script.is_file() or not (root / dashboard).is_file():
        raise FileNotFoundError('The configured Melange service or dashboard is missing')
    with socket.socket() as probe:
        probe.settimeout(1)
        occupied = probe.connect_ex(('127.0.0.1', port)) == 0
    if occupied:
        health(port, timeout=45)
        return 'REUSED_EXISTING_SERVICE'
    runtime = root / 'audits/staffer_live_runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    python = Path(settings.get('service_python') or sys.executable).with_name('python.exe')
    if not python.is_file():
        raise FileNotFoundError(f'Service Python is missing: {python}')
    with (runtime / 'server.stdout.log').open('ab') as out, (runtime / 'server.stderr.log').open('ab') as err:
        process = subprocess.Popen(
            [str(python), str(script), '--source-root', str(root), '--port', str(port),
             '--dashboard-file', dashboard], cwd=root, stdout=out, stderr=err,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    (runtime / 'server.json').write_text(json.dumps({
        'pid': process.pid, 'port': port, 'source_root': str(root),
        'dashboard_file': dashboard, 'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    }, indent=2), encoding='utf-8')
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Melange service exited during startup. See {runtime / "server.stderr.log"}')
        try:
            # A valid loading response is enough to open the UI; indexing stays observable.
            health(port)
            return 'STARTED_LOCAL_SERVICE'
        except (OSError, ValueError):
            time.sleep(.6)
    raise TimeoutError(f'Melange did not respond. See {runtime / "server.stderr.log"}')


def edge_path():
    for base in ('ProgramFiles(x86)', 'ProgramFiles', 'LOCALAPPDATA'):
        if os.environ.get(base):
            candidate = Path(os.environ[base]) / 'Microsoft/Edge/Application/msedge.exe'
            if candidate.is_file():
                return candidate
    raise FileNotFoundError('Microsoft Edge is not installed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('moneyball-edge.json'))
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    runtime = Path(os.environ['LOCALAPPDATA']) / 'Melange/MoneyballApp/edge'
    runtime.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=runtime / 'launcher.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    try:
        settings = json.loads(args.config.read_text(encoding='utf-8-sig'))
        mode = ensure_service(settings)
        edge = edge_path()
        url = f'http://127.0.0.1:{settings.get("port", 8841)}/?v=desktop-app'
        profile = runtime.parent / 'edge-profile'
        profile.mkdir(exist_ok=True)
        if not args.check_only:
            subprocess.Popen([str(edge), f'--app={url}', f'--user-data-dir={profile}', '--no-first-run'])
        receipt = {'status': 'PASS', 'service': mode, 'url': url,
                   'browser_launched': not args.check_only, 'launcher': str(Path(__file__).resolve()),
                   'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        (runtime / 'startup_receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        logging.info('Startup complete: %s', receipt)
        return 0
    except Exception as exc:
        logging.exception('Startup failed')
        if not args.check_only:
            ctypes.windll.user32.MessageBoxW(None, f'{exc}\n\nStartup log: {runtime / "launcher.log"}', 'Melange could not start', 0x10)
        return 1


if __name__ == '__main__':
    sys.exit(main())
