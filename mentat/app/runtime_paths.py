"""Explicit local data roots shared by desktop readers and collector children.

No data is discovered by searching other users' folders. Historical receipts are
never rewritten: an administrator-provided root map resolves their old locations.
Accounting validation remains with the existing source and fingerprint guards.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re


CODE_ROOT = Path(__file__).resolve().parents[1]


def _root(variable, default):
    value = os.environ.get(variable, '').strip()
    path = Path(value) if value else Path(default)
    if not path.is_absolute():
        raise ValueError('Mentat runtime roots must be absolute directories')
    return path.resolve()


def data_root():
    return _root('MENTAT_DATA_ROOT', CODE_ROOT)


def workspace_root():
    return _root('MENTAT_WORKSPACE_ROOT', CODE_ROOT)


def app_data_root():
    default = data_root() / 'app-data' if os.environ.get('MENTAT_DATA_ROOT') else CODE_ROOT / 'app' / 'data'
    return _root('MENTAT_APP_DATA_ROOT', default)


def forecast_data_root():
    return _root('MENTAT_FORECAST_DATA_ROOT', data_root() / 'forecast')


def console_root():
    if os.environ.get('MENTAT_CONSOLE_ROOT'):
        return _root('MENTAT_CONSOLE_ROOT', CODE_ROOT)
    default = Path(os.environ['LOCALAPPDATA']) if os.environ.get('LOCALAPPDATA') else Path.home()
    default = default / 'Mentat' / 'console'
    return _root('MENTAT_CONSOLE_ROOT', default)


def report_cache_root():
    return _root('MENTAT_REPORT_CACHE_ROOT', data_root() / '.cache' / 'industry-report')


def _child(root, relative):
    text = str(relative).replace('\\', '/')
    parts = text.split('/')
    if not text or text.startswith('/') or any(part in {'', '.', '..'} or ':' in part for part in parts):
        raise ValueError('Receipt location must be a contained relative path')
    result = (root / Path(*parts)).resolve()
    if not result.is_relative_to(root):
        raise ValueError('Receipt location leaves its configured data root')
    return result


def _receipt_roots():
    return {'data': data_root(), 'console': console_root()}


def _mappings():
    raw = os.environ.get('MENTAT_RECEIPT_ROOTS', '')
    if not raw:
        return []
    if len(raw.encode('utf-8')) > 65536:
        raise ValueError('Receipt root map is too large')
    value = json.loads(raw)
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError('Invalid receipt root map')
    roots, seen, output = _receipt_roots(), set(), []
    for item in value:
        if not isinstance(item, dict) or set(item) != {'sourceRoot', 'targetRoot', 'relativePath'}:
            raise ValueError('Invalid receipt root mapping')
        source = item['sourceRoot']
        if not isinstance(source, str) or not PureWindowsPath(source).is_absolute():
            raise ValueError('Legacy receipt source roots must be explicit Windows paths')
        original = PureWindowsPath(source)
        key = str(original).casefold()
        if key in seen or not isinstance(item['targetRoot'], str) or item['targetRoot'] not in roots or not isinstance(item['relativePath'], str):
            raise ValueError('Ambiguous or invalid receipt root mapping')
        seen.add(key)
        target = roots[item['targetRoot']]
        if item['relativePath']:
            target = _child(target, item['relativePath'])
        output.append((original, target))
    return output


def resolve_receipt(value, expected_sha256=None):
    """Resolve a portable reference or explicitly mapped old Windows root.

    Explicit selected roots win even if an old drive path exists. There is no
    basename search or inferred migration. Callers that hold a pinned digest may
    request verification; ordinary catalogue reads keep their existing guards.
    """
    if not isinstance(value, (str, Path)):
        raise ValueError('Invalid receipt location')
    text = str(value)
    match = re.fullmatch(r'mentat-(data|console):/(.+)', text)
    if match:
        path = _child(_receipt_roots()[match[1]], match[2])
    elif text.startswith('mentat-'):
        raise ValueError('Invalid portable receipt location')
    else:
        windows = PureWindowsPath(text)
        matches = []
        if windows.is_absolute():
            for source, target in _mappings():
                if windows.is_relative_to(source):
                    relative = windows.relative_to(source)
                    matches.append((len(source.parts), _child(target, relative.as_posix())))
        path = max(matches, key=lambda item: item[0])[1] if matches else Path(text)
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_sha256):
            raise ValueError('Invalid pinned receipt digest')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected_sha256:
                raise ValueError('Receipt file differs from its pinned digest')
    return path


def portable_receipt(path):
    path = Path(path).resolve()
    candidates = [(len(root.parts), key, root) for key, root in _receipt_roots().items() if path.is_relative_to(root)]
    if not candidates:
        # Existing explicit CLI sources outside these roots retain their exact
        # location; no claim of portability is made for an unmapped source.
        return str(path)
    _, key, root = max(candidates)
    return 'mentat-' + key + ':/' + path.relative_to(root).as_posix()


def collector_environment(environment, runtime):
    """Keep shared publications discoverable while pinning each run's code/input copy."""
    result = dict(environment)
    runtime = Path(runtime).resolve()
    result.update(MENTAT_WORKSPACE_ROOT=str(runtime), MENTAT_CODE_ROOT=str(runtime),
                  MENTAT_INPUT_DIR=str(runtime / 'inputs'), MENTAT_INPUTS_DIR=str(runtime / 'inputs'),
                  MENTAT_DATA_ROOT=str(data_root()), MENTAT_CONSOLE_ROOT=str(console_root()),
                  MENTAT_APP_DATA_ROOT=str(app_data_root()), MENTAT_FORECAST_DATA_ROOT=str(forecast_data_root()),
                  MENTAT_REPORT_CACHE_ROOT=str(report_cache_root()))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--encode-receipt', type=Path)
    parser.add_argument('--report-cache-root', action='store_true')
    options = parser.parse_args()
    if options.encode_receipt:
        print(portable_receipt(options.encode_receipt))
    elif options.report_cache_root:
        print(report_cache_root())
    else:
        print(json.dumps({'data': str(data_root()), 'console': str(console_root()),
                          'models': str(app_data_root()), 'reportCache': str(report_cache_root())}))
