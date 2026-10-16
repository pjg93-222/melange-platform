"""Exercise two real private-runtime launches against one synthetic SQLite state."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    result = {'appId': 'terrier', 'launches': 0, 'dataSurvived': False}
    with tempfile.TemporaryDirectory(prefix='terrier-restart-') as folder:
        root = Path(folder)
        state = root / 'state'
        for count in range(2):
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            token = secrets.token_urlsafe(32)
            env = {k:v for k,v in os.environ.items() if not k.startswith(('CU_', 'ACCESS_', 'TERRIER_', 'PYTHON'))}
            env['DESKTOP_LAUNCH_TOKEN'] = token
            command = [str(runtime/'python/python.exe'), '-E', '-s', str(runtime/'bootstrap/desktop_backend.py'),
                       '--app', 'terrier', '--source-root', str(runtime/'sources'), '--data-root', str(root/'data'),
                       '--state-root', str(state), '--port', str(port)]
            with (root/f'launch-{count}.log').open('w') as log:
                process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                ready = False
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        request = urllib.request.Request(f'http://127.0.0.1:{port}/__desktop/health', headers={'X-Desktop-Token': token})
                        with urllib.request.urlopen(request, timeout=2) as response:
                            ready = json.load(response).get('appId') == 'terrier'
                        if ready:
                            break
                    except OSError:
                        time.sleep(.5)
                if not ready:
                    raise RuntimeError((root/f'launch-{count}.log').read_text(errors='replace')[-2000:])
                result['launches'] += 1
                with closing(sqlite3.connect(state/'accounts.sqlite3')) as connection:
                    if count == 0:
                        connection.execute('CREATE TABLE desktop_smoke_state(value TEXT NOT NULL)')
                        connection.execute('INSERT INTO desktop_smoke_state VALUES (?)', ('synthetic-preserve-me',))
                        connection.commit()
                    else:
                        result['dataSurvived'] = connection.execute('SELECT value FROM desktop_smoke_state').fetchone() == ('synthetic-preserve-me',)
                        result['integrity'] = connection.execute('PRAGMA integrity_check').fetchone()[0]
            finally:
                if process.poll() is None:
                    subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
                process.wait(timeout=30)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
    return 0 if result['dataSurvived'] and result.get('integrity') == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
