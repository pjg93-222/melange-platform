"""Expose Mentat's existing project layout without mixing code and writable data.

The small workspace is reconstructable application cache. Directory junctions
point either at installed code or at this installation's user-selected data.
No developer checkout or existing personal state is selected implicitly.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess


CODE_DIRS = ('app', 'tools', 'forecast', 'R', 'config')
DATA_DIRS = ('inputs', 'state', '.cache', 'reports', 'outputs', 'melange')


def canonical_root(path):
    return os.path.normcase(str(Path(path).resolve()))


def workspace_path(data, state):
    identity = hashlib.sha256(canonical_root(data).encode('utf-8')).hexdigest()[:24]
    return Path(state).resolve().parent / 'runtime-workspaces' / identity


def load_connections(code, data, state, protected_roots=()):
    """Read an explicit local path map; never infer/copy a developer's data."""
    code, data, state = Path(code).resolve(), Path(data).resolve(), Path(state).resolve()
    paths = {'console': state / 'console', 'reports': data / 'reports', 'app_data': data / 'app-data', 'receipt_roots': []}
    config = state.parent / 'workspace-connection.json'
    if not config.exists():
        return paths
    if config.stat().st_size > 16384:
        raise ValueError('Mentat workspace connection is too large')
    value = json.loads(config.read_text(encoding='utf-8-sig'))
    allowed = {'schemaVersion', 'dataRoot', 'consoleRoot', 'reportsRoot', 'appDataRoot', 'receiptRoots'}
    if not isinstance(value, dict) or set(value) - allowed or value.get('schemaVersion') != 1:
        raise ValueError('Invalid Mentat workspace connection')
    if not isinstance(value.get('dataRoot'), str) or not Path(value['dataRoot']).is_absolute() or canonical_root(value['dataRoot']) != canonical_root(data):
        raise ValueError('Mentat workspace connection does not match the selected data root')
    protected = [code, *(Path(root).resolve() for root in protected_roots)]
    for key, destination in (('consoleRoot', 'console'), ('reportsRoot', 'reports'), ('appDataRoot', 'app_data')):
        if key not in value:
            continue
        if not isinstance(value[key], str) or not Path(value[key]).is_absolute():
            raise ValueError('Mentat workspace connections require absolute directory paths')
        target = Path(value[key]).resolve()
        if not target.is_dir() or target == Path(target.anchor):
            raise ValueError('Mentat workspace connection directory is unavailable')
        for root in protected:
            if target == root or target.is_relative_to(root) or root.is_relative_to(target):
                raise ValueError('Mentat workspace connection overlaps installed application files')
        paths[destination] = target
    mappings = value.get('receiptRoots', [])
    if not isinstance(mappings, list) or len(mappings) > 64:
        raise ValueError('Invalid Mentat receipt root mappings')
    seen = set()
    for item in mappings:
        if not isinstance(item, dict) or set(item) != {'sourceRoot', 'targetRoot', 'relativePath'}:
            raise ValueError('Invalid Mentat receipt root mapping')
        original, target_name, relative = item['sourceRoot'], item['targetRoot'], item['relativePath']
        if not isinstance(original, str) or not PureWindowsPath(original).is_absolute():
            raise ValueError('Receipt source root must be an explicit absolute Windows path')
        key = str(PureWindowsPath(original)).casefold()
        if key in seen or not isinstance(target_name, str) or target_name not in {'data', 'console'} or not isinstance(relative, str):
            raise ValueError('Ambiguous or invalid Mentat receipt root mapping')
        seen.add(key)
        base = data if target_name == 'data' else paths['console'].resolve()
        if relative:
            parts = relative.replace('\\', '/').split('/')
            if any(part in {'', '.', '..'} or ':' in part for part in parts):
                raise ValueError('Receipt destination must be a contained relative path')
            target = base.joinpath(*parts).resolve()
            if not target.is_relative_to(base):
                raise ValueError('Receipt destination leaves its configured root')
        paths['receipt_roots'].append(item.copy())
    return paths


def bind_analytics_paths(module, code, workspace, data):
    """Keep unchanged accounting rules and existing index identities across layout aliases.

    Rules still refer to the exact loaded bytes. Only their directory aliases and
    the selected data root used to relativize receipts differ from source mode.
    No index is opened, rebuilt, or modified here.
    """
    code, workspace, data = Path(code).resolve(), Path(workspace).absolute(), Path(data).resolve()
    own_file = Path(module.__file__)
    rules = []
    for original in module.RULE_PATHS:
        original = Path(original)
        if original == own_file:
            rules.append(original)  # Preserve the engine's own-file exclusion.
            continue
        relative = original.resolve().relative_to(code)
        alias = workspace / relative
        if not alias.is_file() or alias.read_bytes().replace(b'\r\n', b'\n') != original.read_bytes().replace(b'\r\n', b'\n'):
            raise ValueError('Selected Mentat accounting rules differ from the installed engine')
        rules.append(alias)
    module.RULE_PATHS = rules
    module.LOADED_RULE_STAMPS = [(str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in rules]
    original_portable = module._portable
    def portable(path, roots):
        return original_portable(path, (*roots, data))
    module._portable = portable


def prepare_workspace(code, data, state):
    code, data, state = Path(code).resolve(), Path(data).resolve(), Path(state).resolve()
    if code == data or code.is_relative_to(data) or data.is_relative_to(code):
        raise ValueError('Mentat code and writable data must be separate trees')
    # Selecting another data directory must not rewrite a live session's links.
    # Legacy runtime-workspace directories are preserved, never repointed.
    workspace = workspace_path(data, state)
    workspace.mkdir(parents=True, exist_ok=True)
    pairs = []
    for name in CODE_DIRS:
        target = code / name
        if not target.is_dir():
            raise ValueError('Required installed Mentat code directory is absent: ' + name)
        pairs.append((workspace / name, target))
    for name in DATA_DIRS:
        target = data / name
        target.mkdir(parents=True, exist_ok=True)
        pairs.append((workspace / name, target))
    missing = []
    for link, target in pairs:
        if link.exists() or link.is_symlink() or link.is_junction():
            if not (link.is_symlink() or link.is_junction()) or link.resolve() != target.resolve():
                raise ValueError('Mentat workspace link has an unexpected target: ' + link.name)
        else:
            missing.append({'path': str(link), 'target': str(target)})
    if missing and os.name == 'nt':
        environment = dict(os.environ, MENTAT_DESKTOP_LINKS=json.dumps(missing))
        script = "$ErrorActionPreference='Stop'; foreach($link in ($env:MENTAT_DESKTOP_LINKS | ConvertFrom-Json)) { New-Item -ItemType Junction -Path $link.path -Target $link.target | Out-Null }"
        subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script], env=environment,
                       capture_output=True, timeout=45, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    elif missing:
        for item in missing:
            Path(item['path']).symlink_to(item['target'], target_is_directory=True)
    for link, target in pairs:
        if link.resolve() != target.resolve():
            raise ValueError('Mentat workspace target verification failed')
    shutil.copy2(code / 'mentat.R', workspace / 'mentat.R')
    for name in ('fec_receipt_rules.json', 'fec_transaction_types.json'):
        source, destination = code / 'inputs' / name, data / 'inputs' / name
        if not destination.exists():
            shutil.copy2(source, destination)
    return workspace
