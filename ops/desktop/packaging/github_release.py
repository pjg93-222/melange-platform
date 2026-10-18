"""Stage three gated, signed desktop apps, each for its own public GitHub repository; never upload."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from desktop_admin import APPS, Refused, Store, canonical, file_hashes, verify_envelope

REPOSITORY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*')
VERSION = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)')
# Each app updates only from its own public repository (Paul, 2026-10-15); the
# same map is the client builder default (client/builder.cjs).
REPOSITORIES = {'melange': 'pjg93-222/Melange', 'mentat': 'pjg93-222/Mentat', 'terrier': 'pjg93-222/Geo'}
assert set(REPOSITORIES) == APPS


def app_assets(app, version):
    return sorted((f'{app}-win32-x64.manifest.json', app + '-trust.json', f'{app}-{version}-win32-x64.exe'))


def repositories(spec):
    """Fixed per-app destinations. An explicit override may only restate its app's repository."""
    if 'repository' in spec:
        raise Refused('a single release repository is retired; each app publishes to its own fixed repository')
    overrides = spec.get('repositories', {})
    if not isinstance(overrides, dict) or not set(overrides) <= APPS:
        raise Refused('repository overrides must be keyed by app id')
    for app, value in overrides.items():
        if not isinstance(value, str) or value.lower() != REPOSITORIES[app].lower():
            raise Refused(f'{app} must be released to {REPOSITORIES[app]}')
    return {app: REPOSITORIES[app].lower() for app in APPS}


def valid_version(value):
    return (isinstance(value, str) and len(value) <= 256 and VERSION.fullmatch(value) is not None
            and all(int(part) <= 2**53-1 for part in value.split('.')))


def valid_sequence(value):
    return type(value) is int and 1 <= value <= 2**53-1


def app_release(spec, app):
    """(version, sequence) of one app: its own entry in packages, else the specification's defaults."""
    files = spec['packages'][app]
    return files.get('version', spec['version']), files.get('sequence', spec['sequence'])


def validate_spec(spec):
    if not isinstance(spec, dict):
        raise Refused('release specification must be an object')
    if not valid_version(spec.get('version')):
        raise Refused('a three-part stable application version is required')
    if not valid_sequence(spec.get('sequence')):
        raise Refused('positive safe-integer release sequence required')
    if (spec.get('platform'), spec.get('arch')) != ('win32', 'x64'):
        raise Refused('only the verified Windows x64 activation lane can be prepared')
    # A release may name any non-empty subset of the apps (release owner, 2026-10-16: Melange first). Every signed
    # manifest still binds the committed source of all three apps, which the installed client requires
    # (client/github-source.cjs githubPolicy), so sourceRefs always names all three.
    if (not isinstance(spec.get('packages'), dict) or not isinstance(spec.get('sourceRefs'), dict)
            or not spec['packages'] or not set(spec['packages']) <= set(APPS) or set(spec['sourceRefs']) != set(APPS)):
        raise Refused('a release names one or more of the apps, and the source references of all three')
    for app in sorted(APPS):
        source = spec['sourceRefs'][app]
        if (not isinstance(source, dict) or set(source) != {'repository', 'ref', 'commit'}
                or not isinstance(source.get('repository'), str) or not REPOSITORY.fullmatch(source['repository'])
                or any(len(part) > 100 for part in source['repository'].split('/'))
                or not isinstance(source.get('ref'), str) or not 1 <= len(source['ref']) <= 512
                or any(ord(c) < 32 or ord(c) == 127 for c in source['ref'])
                or not isinstance(source.get('commit'), str) or not re.fullmatch(r'[0-9a-f]{40}', source['commit']) or source['commit'] == '0'*40):
            raise Refused('invalid committed source reference for ' + app)
    for app in sorted(spec['packages']):
        files = spec['packages'][app]
        if (not isinstance(files, dict) or not {'package', 'receipt'} <= set(files) <= {'package', 'receipt', 'version', 'sequence'}
                or any(not isinstance(files[key], str) or not files[key] or '\0' in files[key] for key in ('package', 'receipt'))):
            raise Refused('package and exact-byte gate receipt required for ' + app)
        version, sequence = app_release(spec, app)
        if not valid_version(version) or not valid_sequence(sequence):
            raise Refused('invalid version or sequence for ' + app)


def prepare(store, spec, output):
    """Sign the named apps atomically (one or more); output/<app>/ is that app's release for its own repository."""
    validate_spec(spec)
    repos = repositories(spec)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise Refused('output must be a new directory; existing release files are never replaced')
    output.parent.mkdir(parents=True, exist_ok=True)
    config = store.config()  # Existing independently provisioned signing identities only.
    platform, arch = spec['platform'], spec['arch']
    apps = sorted(spec['packages'])
    ledger = store.root / 'github-prepared.sqlite3'
    if ledger.is_symlink():
        raise Refused('release ledger may not be a symlink')
    with tempfile.TemporaryDirectory(prefix='.github-release-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'release'
        manifests = {}
        for app in apps:
            version, sequence = app_release(spec, app)
            package_input = Path(spec['packages'][app]['package'])
            if package_input.is_symlink() or not package_input.is_file() or package_input.suffix.lower() != '.exe':
                raise Refused('a regular NSIS installer is required for ' + app)
            (stage / app).mkdir(parents=True)
            name = f'{app}-{version}-{platform}-{arch}.exe'
            destination = stage / app / name
            shutil.copyfile(package_input, destination)
            hashes = file_hashes(destination)
            receipt = json.loads(Path(spec['packages'][app]['receipt']).read_text(encoding='utf-8-sig'))
            if not isinstance(receipt, dict) or receipt.get('source') != spec['sourceRefs'][app]:
                raise Refused('package receipt must bind the declared committed source for ' + app)
            manifest = {
                'schemaVersion': 1, 'appId': app, 'version': version, 'sequence': sequence,
                'platform': platform, 'arch': arch,
                'issuedAt': dt.datetime.now(dt.timezone.utc).isoformat(),
                'minimumUpdaterVersion': '1.0.0', 'dataSchemaVersion': 1,
                'purpose': 'melange-desktop-github-release',
                'distribution': 'public-installations', 'repository': repos[app],
                'sourceRefs': spec['sourceRefs'],
                'package': {'name': name, 'path': f'/v1/packages/{app}/{sequence}/{name}', **hashes},
            }
            # The actual publisher OS-signature and exact-byte gates run against
            # the staged copy. No synthetic receipt or bypass exists in this tool.
            store.verify_release_gates(receipt, manifest, destination)
            if file_hashes(destination) != hashes:
                raise Refused('installer changed during verification')
            manifests[app] = manifest
        connection = sqlite3.connect(ledger, timeout=15)
        if os.name != 'nt':
            ledger.chmod(0o600)
        try:
            connection.execute('CREATE TABLE IF NOT EXISTS prepared (repository TEXT, app TEXT, platform TEXT, arch TEXT, sequence INTEGER, version TEXT, envelope TEXT, PRIMARY KEY(repository,app,platform,arch,sequence))')
            connection.execute('BEGIN IMMEDIATE')
            indexes = {}
            for app in apps:
                manifest = manifests[app]
                version, sequence = manifest['version'], manifest['sequence']
                tag = 'desktop-v' + version
                # Rows are written per (repository, app). The advance check reads every
                # row for the app, so no repository (including a legacy one) can reuse
                # or lower its sequence/version.
                row = connection.execute('SELECT sequence,version,envelope FROM prepared WHERE app=? AND platform=? AND arch=? ORDER BY sequence DESC LIMIT 1',
                                         (app, platform, arch)).fetchone()
                if row and sequence == row[0]:
                    envelope = json.loads(row[2])
                    previous = json.loads(base64.b64decode(envelope['payload']))
                    manifest['issuedAt'] = previous['issuedAt']
                    if manifest != previous:
                        raise Refused('release sequence already binds different bytes or metadata')
                else:
                    if row and (sequence < row[0] or tuple(map(int, version.split('.'))) <= tuple(map(int, row[1].split('.')))):
                        raise Refused('version and sequence must advance beyond the last prepared release')
                    envelope = store.sign(app, manifest)
                    connection.execute('INSERT INTO prepared VALUES (?,?,?,?,?,?,?)',
                                       (repos[app], app, platform, arch, sequence, version, canonical(envelope).decode()))
                verify_envelope(envelope, config['apps'][app]['publicKeys'], app=app, platform=platform, arch=arch)
                directory = stage / app
                if file_hashes(directory / manifest['package']['name']) != {key: manifest['package'][key] for key in ('size', 'sha256', 'sha512')}:
                    raise Refused('installer changed before staging completed')
                (directory / f'{app}-{platform}-{arch}.manifest.json').write_bytes(canonical(envelope))
                (directory / (app + '-trust.json')).write_bytes(canonical({'transitions': config['apps'][app].get('transitions', [])}))
                assets = app_assets(app, version)
                index = {'schemaVersion': 1, 'purpose': 'melange-desktop-github-release', 'repository': repos[app], 'appId': app,
                         'tag': tag, 'version': version, 'sequence': sequence, 'platform': platform, 'arch': arch,
                         'sourceRefs': spec['sourceRefs'], 'assets': assets, 'published': False}
                (directory / 'release.json').write_bytes(canonical(index))
                (directory / 'SHA256SUMS').write_text(''.join(file_hashes(directory / name)['sha256'] + '  ' + name + '\n'
                                                              for name in sorted(assets + ['release.json'])), encoding='ascii')
                indexes[app] = index
            # Reserve exact signed identities before exposing output. A failed move
            # can be retried at another new path using the identical signed bytes.
            connection.commit()
            os.replace(stage, output)
            return indexes
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def verify_bundle(store, output, repository=None):
    """Recheck one app's exact public release directory immediately before (and after) a separate gh upload."""
    output = Path(output)
    index = json.loads((output / 'release.json').read_text(encoding='utf-8'))
    app = index.get('appId') if isinstance(index, dict) else None
    if app not in APPS:
        raise Refused('staged release must name one app')
    validate_spec({**index, 'packages': {name: {'package': 'unused.exe', 'receipt': 'unused.json'} for name in APPS}})
    if (index.get('schemaVersion') != 1 or index.get('purpose') != 'melange-desktop-github-release'
            or index.get('repository') != REPOSITORIES[app].lower() or index.get('tag') != 'desktop-v' + index['version']
            or index.get('published') is not False):
        raise Refused('invalid staged release identity')
    if repository is not None and (not isinstance(repository, str) or repository.lower() != index['repository']):
        raise Refused(f'{app} release belongs to {REPOSITORIES[app]}, not {repository}')
    assets = app_assets(app, index['version'])
    if index.get('assets') != assets or {path.name for path in output.iterdir()} != set(assets + ['release.json', 'SHA256SUMS']):
        raise Refused('public release must contain exactly the declared app assets')
    if any(path.is_symlink() or not path.is_file() for path in output.iterdir()):
        raise Refused('public assets must be regular files')
    config = store.config()
    envelope = json.loads((output / f'{app}-win32-x64.manifest.json').read_text(encoding='utf-8'))
    manifest = verify_envelope(envelope, config['apps'][app]['publicKeys'], app=app, platform='win32', arch='x64')
    for key in ('version', 'sequence', 'repository', 'sourceRefs', 'purpose'):
        if manifest.get(key) != index[key]:
            raise Refused('signed manifest differs from release identity: ' + key)
    name = f"{app}-{index['version']}-win32-x64.exe"
    package = manifest['package']
    if (manifest.get('distribution') != 'public-installations' or package['name'] != name
            or package['path'] != f"/v1/packages/{app}/{index['sequence']}/{name}"
            or file_hashes(output / name) != {key: package[key] for key in ('size', 'sha256', 'sha512')}):
        raise Refused('installer bytes or public distribution identity changed')
    trust = json.loads((output / (app + '-trust.json')).read_text(encoding='utf-8'))
    if trust != {'transitions': config['apps'][app].get('transitions', [])}:
        raise Refused('trust transitions changed; reprepare with the current protected state')
    checksums = ''.join(file_hashes(output / item)['sha256'] + '  ' + item + '\n'
                        for item in sorted(assets + ['release.json']))
    if (output / 'SHA256SUMS').read_text(encoding='ascii') != checksums:
        raise Refused('public release checksum list changed')
    return index


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True, help='existing protected publisher signing state')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--spec', type=Path, help='three-app exact source/package/receipt specification')
    mode.add_argument('--verify-output', type=Path, help="verify one app's release directory before or after a separate gh upload")
    parser.add_argument('--output', type=Path, help='new public-assets staging directory (one subdirectory per app)')
    parser.add_argument('--repository', help='with --verify-output: the destination the directory was or will be uploaded to')
    args = parser.parse_args(argv)
    try:
        if args.spec and not args.output:
            parser.error('--spec requires --output')
        if args.spec and args.repository:
            parser.error('--repository applies only to --verify-output')
        if args.verify_output:
            index = verify_bundle(Store(args.state), args.verify_output, args.repository)
            result = {index['appId']: index}
        else:
            result = prepare(Store(args.state), json.loads(args.spec.read_text(encoding='utf-8-sig')), args.output)
        print(json.dumps({'releases': {app: {'repository': value['repository'], 'tag': value['tag']} for app, value in sorted(result.items())},
                          'output': str(args.verify_output or args.output), 'verified': bool(args.verify_output), 'published': False}))
        return 0
    except (Refused, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        print('Preparation refused: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
