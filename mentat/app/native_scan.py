"""Optional Rust decoding accelerator. Accounting stays in the Python/R rules.

No compiler runs on a request. Build once with tools/build_native.ps1. A missing
binary uses the reference reader; a scanner error is never an empty dataset.
"""
import atexit
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import json
import os
from pathlib import Path
import queue
import subprocess
import threading

import drawer_sidecars

PROJECT = Path(__file__).resolve().parents[1]
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='mentat-native')
_workers = queue.LifoQueue()
_all = []
_guard = threading.Lock()


def executable():
    if os.environ.get('MENTAT_NATIVE', '').lower() in ('0', 'off', 'false'):
        return None
    explicit = os.environ.get('MENTAT_NATIVE_SCANNER')
    path = Path(explicit) if explicit else PROJECT / '.cache/native-target/release' / (
        'mentat-scan.exe' if os.name == 'nt' else 'mentat-scan')
    if not path.is_file():
        return None
    # Do not run a stale local build after the scanner's sources change.
    sources = [PROJECT / 'native/mentat-scan' / name for name in
               ('Cargo.toml', 'Cargo.lock', 'src/main.rs')]
    if not explicit and any(p.exists() and p.stat().st_mtime_ns > path.stat().st_mtime_ns for p in sources):
        return None
    return path


def status():
    binary = executable()
    return {'engine': 'rust' if binary else 'python', 'protocol': 1,
            'build_command': 'tools/build_native.ps1'}


class Worker:
    def __init__(self, path):
        self.path = path
        self.stamp = path.stat().st_mtime_ns
        self.process = subprocess.Popen([str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        self.process.stdin.close()
        self.process.stdout.close()

    def request(self, payload):
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n')
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise ValueError('Native workbook scanner stopped without a result.')
        value = json.loads(line)
        if value.get('protocol') != 1:
            raise ValueError('Native workbook scanner protocol mismatch; rebuild it.')
        if value.get('error'):
            raise ValueError(value['error'])
        return value


def tables(file_stamp, requests, max_rows=1_000_000, payload_table=None):
    binary = executable()
    if binary is None:
        return None
    # The Graph drawer's two cold reads - the pinned payload's transfer paths (Banking: a 25 s
    # scan of 1.4 GB) and a workbook's Downstream Activity - are kept on disk, keyed by the
    # input's full-content hash, the scanner binary and the request (drawer_sidecars; perf.md
    # H8). Every other scan runs exactly as before.
    key = drawer_sidecars.native_key(binary, file_stamp, requests, max_rows, payload_table)
    if key is None:
        return _scan(binary, file_stamp, requests, max_rows, payload_table)
    result = drawer_sidecars.native_result(key, lambda: _scan(binary, file_stamp, requests, max_rows, payload_table))
    if result is not None:
        from report_insights import stamp
        if stamp(file_stamp[0]) != file_stamp:
            raise ValueError('The workbook changed while it was being read. Refresh Graph.')
    return result


def _scan(binary, file_stamp, requests, max_rows, payload_table):
    with _guard:
        try:
            worker = _workers.get_nowait()
        except queue.Empty:
            worker = None
        if worker and (worker.path != binary or worker.stamp != binary.stat().st_mtime_ns or worker.process.poll() is not None):
            worker.close()
            _all.remove(worker)
            worker = None
        if worker is None and len(_all) < 2:
            worker = Worker(binary)
            _all.append(worker)
    if worker is None:
        # Busy native workers must not starve unrelated UI requests.
        return None
    good = False
    try:
        payload = {'path': file_stamp[0], 'tables': requests, 'max_rows': max_rows}
        if payload_table:
            payload['payload_table'] = payload_table
        future = _executor.submit(worker.request, payload)
        try:
            result = future.result(timeout=120)
        except TimeoutError as error:
            raise ValueError('Native workbook scan timed out; no partial graph was created.') from error
        from report_insights import stamp
        if stamp(file_stamp[0]) != file_stamp:
            raise ValueError('The workbook changed while it was being read. Refresh Graph.')
        good = True
        return result
    finally:
        if good:
            _workers.put(worker)
        else:
            worker.close()
            with _guard:
                _all.remove(worker)


def shutdown():
    with _guard:
        for worker in _all:
            try:
                worker.close()
            except (OSError, subprocess.TimeoutExpired):
                pass
        _all.clear()
        while not _workers.empty():
            _workers.get_nowait()


atexit.register(shutdown)
