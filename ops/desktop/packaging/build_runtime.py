"""Stage code and an app-private Windows CPython runtime with native dependencies.

This makes a developer artifact, never a publication receipt. The release gate must
also verify distribution rights, complete native dependencies and OS signatures.
"""
from __future__ import annotations
import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import re
import subprocess
import sys
import uuid

HERE = Path(__file__).resolve().parent
CLIENT = HERE.parent / 'client'
EXTENSIONS = {'.py', '.r', '.ps1', '.psm1', '.sh', '.tpl', '.js', '.mjs', '.cjs', '.css', '.html', '.svg', '.png', '.ico', '.xml', '.woff', '.woff2', '.ttf', '.wasm'}
DENIED_PARTS = {'node_modules', '.git', '.venv', '__pycache__', 'tests', 'test', 'fixtures', 'outputs', '.cache'}
DENIED_NAMES = {'keys.json', '.env', '.secret', 'accounts.sqlite3', 'dashboard_password.txt'}
# Runtime databases (and their WAL/SHM/journal side files) are user state, never source, wherever they appear.
DENIED_SUFFIXES = ('.sqlite', '.sqlite3', '.sqlite-wal', '.sqlite-shm', '.sqlite-journal', '.sqlite3-wal', '.sqlite3-shm', '.sqlite3-journal',
                   '.db', '.db-wal', '.db-shm', '.db-journal')
POCKET_TEMPLATE = 'mentat/tools/pocket/mentat-pocket.html'
POCKET_PATTERN = re.compile(r'(/\*__DATA_START__\*/).*?(/\*__DATA_END__\*/)', re.S)
BOOTSTRAP_FILES = (
    'desktop_backend.py', 'desktop_audit.py', 'native_credentials.py', 'native_access_state.py',
    'melange_bootstrap.py', 'melange_data_paths.py', 'mentat_layout.py', 'desktop_migrations.py',
    'terrier_owner.py', 'terrier_district.py', 'desktop_figure_renderer.py',
)


def stage_bootstrap(destination):
    """Copy only the explicitly reviewed launcher support modules."""
    destination.mkdir()
    for name in BOOTSTRAP_FILES:
        shutil.copy2(HERE / name, destination / name)


def stage_file(source, destination, relative):
    """Preserve code bytes except the Pocket template's explicitly marked data."""
    if relative.as_posix() == POCKET_TEMPLATE:
        page = source.read_text(encoding='utf-8')
        if page.count('/*__DATA_START__*/') != 1 or page.count('/*__DATA_END__*/') != 1:
            raise RuntimeError('Pocket template data markers are missing or ambiguous')
        cleaned, count = POCKET_PATTERN.subn(lambda match: match[1] + '{}' + match[2], page)
        if count != 1:
            raise RuntimeError('Pocket template payload cannot be removed')
        destination.write_text(cleaned, encoding='utf-8', newline='\n')
    else:
        shutil.copy2(source, destination)


def selected_files(root, spec):
    result = set()
    for relative in spec['sourceDirectories']:
        directory = root / relative
        if not directory.is_dir():
            raise RuntimeError(f'Required code directory is missing: {directory}')
        for file in directory.rglob('*'):
            parts = file.relative_to(root).parts
            if any(file.relative_to(root).as_posix() == prefix or file.relative_to(root).as_posix().startswith(prefix + '/') for prefix in spec.get('excludedSourcePrefixes', [])):
                continue
            if file.name.lower().endswith(DENIED_SUFFIXES):
                continue
            if file.is_file() and not file.is_symlink() and not any(p in DENIED_PARTS for p in parts) and file.suffix.lower() in EXTENSIONS and file.name not in DENIED_NAMES:
                # CSV files are data unless explicitly named as immutable schema below.
                if file.name.startswith(('test_', '.')) or '.bak' in file.name:
                    continue
                if 'pocket' in file.name.lower() and file.suffix.lower() in {'.html', '.js'} and file.relative_to(root).as_posix() != POCKET_TEMPLATE:
                    raise RuntimeError(f'Pocket source needs the existing release export scrub before packaging: {file}')
                result.add(file.relative_to(root))
    for relative in spec.get('sourceFiles', []):
        file = root / relative
        if not file.is_file() or file.is_symlink():
            raise RuntimeError(f'Required immutable source file is missing: {file}')
        if file.name.lower().endswith(DENIED_SUFFIXES):
            raise RuntimeError(f'Runtime database cannot be a release source file: {file}')
        result.add(Path(relative))
    return sorted(result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', choices=['melange', 'terrier', 'mentat'], required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--stage-only', action='store_true')
    parser.add_argument('--sync-sources-only', action='store_true', help='Refresh an existing developer runtime without rebuilding private dependencies')
    parser.add_argument('--freeze', action='store_true', help='Legacy diagnostic PyInstaller build; not the Windows desktop runtime')
    parser.add_argument('--native-root', type=Path)
    parser.add_argument('--build-root', type=Path, default=Path(os.environ.get('DESKTOP_BUILD_ROOT', str(HERE / '.build'))))
    parser.add_argument('--runtime-root', type=Path, default=Path(os.environ.get('DESKTOP_RUNTIME_ROOT', str(CLIENT / 'runtime'))))
    options = parser.parse_args()
    spec = json.loads((HERE / 'runtime-manifest.json').read_text())['apps'][options.app]
    root = options.source.resolve()
    work = options.build_root.resolve() / options.app / uuid.uuid4().hex
    staging = work / 'sources'
    staging.mkdir(parents=True, exist_ok=True)
    paths = selected_files(root, spec)
    for relative in paths:
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage_file(root / relative, destination, relative)
    (work / 'source-inventory.json').write_text(json.dumps([str(p).replace('\\', '/') for p in paths], indent=2), encoding='utf-8')
    if options.stage_only:
        print(json.dumps({'stage': str(staging), 'files': len(paths), 'publication': 'not-approved'}))
        return
    missing_python = [module for module in spec['hiddenImports'] if importlib.util.find_spec(module) is None]
    if missing_python:
        raise RuntimeError('Build environment lacks required modules: ' + ', '.join(missing_python))
    bootstrap = work / 'bootstrap'
    stage_bootstrap(bootstrap)
    output = options.runtime_root.resolve() / options.app
    if options.sync_sources_only:
        if not (output / 'python' / 'python.exe').is_file():
            raise RuntimeError('Source sync requires a previously built private runtime')
        if output.parent != options.runtime_root.resolve() or not work.resolve().is_relative_to(options.build_root.resolve()):
            raise RuntimeError('Source refresh escaped configured build roots')
        (output / 'sources').rename(work / 'previous-sources')
        staging.rename(output / 'sources')
        shutil.copytree(bootstrap, output / 'bootstrap', dirs_exist_ok=True)
        shutil.copy2(work / 'source-inventory.json', output / 'source-inventory.json')
        print(json.dumps({'runtime':str(output), 'sourceFiles':len(paths), 'publication':'not-approved'}))
        return
    built = work / 'dist' / 'desktop-backend'
    if options.freeze:
        command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--name', 'desktop-backend',
                   '--distpath', str(work / 'dist'), '--workpath', str(work / 'pyinstaller'), '--specpath', str(work),
                   '--add-data', str(staging) + os.pathsep + 'sources']
        for module in spec['hiddenImports']:
            command += ['--collect-all', module, '--exclude-module', module + '.tests', '--exclude-module', module + '.test']
        command += [str(bootstrap / 'desktop_backend.py')]
        subprocess.run(command, check=True)
    else:
        from bundle_python import bundle_python
        bundle_python(built / 'python')
        shutil.copytree(bootstrap, built / 'bootstrap')
        shutil.copytree(staging, built / 'sources')
    if output.exists():
        previous = output.with_name(output.name + '.previous-' + uuid.uuid4().hex)
        if previous.parent != options.runtime_root.resolve():
            raise RuntimeError('Runtime destination escaped its configured root')
        output.rename(previous)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not built.resolve().is_relative_to(options.build_root.resolve()) or output.parent != options.runtime_root.resolve():
        raise RuntimeError('Generated runtime paths escaped configured roots')
    shutil.move(str(built), str(output))
    shutil.copy2(work / 'source-inventory.json', output / 'source-inventory.json')
    dependencies = sorted(({'name': item.metadata['Name'], 'version': item.version,
                            'licenseExpression': item.metadata.get('License-Expression'),
                            'license': item.metadata.get('License'),
                            'licenseFiles': item.metadata.get_all('License-File') or []}
                           for item in importlib.metadata.distributions()), key=lambda item: item['name'].lower())
    (output / 'dependency-inventory.json').write_text(json.dumps(dependencies, indent=2), encoding='utf-8')
    # PyInstaller 6 places data in _internal. The launcher passes an explicit source root.
    frozen_sources = output / '_internal' / 'sources'
    if frozen_sources.exists():
        shutil.copytree(frozen_sources, output / 'sources', dirs_exist_ok=True)
    missing = []
    for name in spec['nativeRuntimes']:
        native = options.native_root / name if options.native_root else None
        if native and native.is_dir():
            shutil.copytree(native, output / 'native' / name, dirs_exist_ok=True, symlinks=True)
        else:
            missing.append(name)
    (output / 'runtime-status.json').write_text(json.dumps({'appId': options.app, 'platform': sys.platform, 'missingNativeRuntimes': missing,
                                                         'publication': 'blocked: native acceptance/security/signature/data gates required'}, indent=2))
    print(json.dumps({'runtime': str(output), 'missingNativeRuntimes': missing, 'publication': 'not-approved'}))


if __name__ == '__main__':
    main()
