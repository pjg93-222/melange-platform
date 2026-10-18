"""Wrap one app's verified, signed Windows installer in a zip to send to a person; never builds, signs or uploads."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import github_release  # also puts ops/desktop/server on sys.path for desktop_admin
from build_runtime import DENIED_NAMES
from desktop_admin import APPS, Refused, Store, file_hashes, verify_envelope

NAMES = {'melange': 'Melange', 'mentat': 'Mentat', 'terrier': 'Terrier'}
assert set(NAMES) == APPS
DENIED_PATTERNS = ('*.sqlite*', '.env*', '*.key', '*.local')
STAMP = (2026, 1, 1, 0, 0, 0)  # fixed so the same inputs give the same zip bytes
CHUNK = 1024 * 1024


def denied(name):
    """Names that must never travel in a zip: the build deny list plus databases, env files and keys."""
    lowered = name.lower()
    return lowered in DENIED_NAMES or any(fnmatch.fnmatchcase(lowered, pattern) for pattern in DENIED_PATTERNS)


def bundle_dir(bundle, app):
    """The per-app release directory, given either it or the prepare output root."""
    bundle = Path(bundle)
    return bundle / app if (bundle / app / 'release.json').is_file() else bundle


def authenticode(installer, publisher):
    """Like desktop_admin.Store.verify_release_gates (Valid, pinned publisher), but an unpinned publisher refuses instead of skipping."""
    if os.name != 'nt':
        print("Authenticode check SKIPPED: not Windows; this zip's installer signature was NOT checked", file=sys.stderr)
        return 'skipped'
    # The path travels in the environment, never in the command text.
    env = dict(os.environ, DESKTOP_VERIFY_PACKAGE=str(installer))
    command = "$s=Get-AuthenticodeSignature -LiteralPath $env:DESKTOP_VERIFY_PACKAGE; @{Status=[string]$s.Status;Subject=$s.SignerCertificate.Subject}|ConvertTo-Json -Compress"
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command], env=env,
                                capture_output=True, text=True, timeout=60, check=True)
    except subprocess.SubprocessError as error:
        raise Refused('Authenticode check could not run: ' + str(error)) from error
    signature = json.loads(result.stdout)
    if signature.get('Status') != 'Valid':
        raise Refused(f"installer Authenticode status is {signature.get('Status')}, not Valid")
    if not publisher:
        raise Refused('no Windows publisher is pinned in the signing state')
    if signature.get('Subject') != publisher:
        raise Refused('installer is not signed by the pinned Windows publisher')
    return 'Valid'


def signed_installer_sha256(store, directory, app, index):
    """The installer sha256 the release signature vouches for; the bundle file itself can change after verify_bundle."""
    envelope = json.loads((directory / f'{app}-win32-x64.manifest.json').read_text(encoding='utf-8'))
    # Re-reading is safe: our hash comes from a signature-verified manifest whose full release identity matches the verified
    # release.json, so a substitute would need a second validly signed release with the same app, version, sequence,
    # repository and sourceRefs. These are the same identity checks verify_bundle makes.
    manifest = verify_envelope(envelope, store.config()['apps'][app]['publicKeys'], app=app, platform='win32', arch='x64')
    for key in ('version', 'sequence', 'repository', 'sourceRefs', 'purpose'):
        if manifest.get(key) != index[key]:
            raise Refused('signed manifest differs from release identity: ' + key)
    name = f"{app}-{index['version']}-win32-x64.exe"
    package = manifest['package']
    if (manifest.get('distribution') != 'public-installations' or package['name'] != name
            or package['path'] != f"/v1/packages/{app}/{index['sequence']}/{name}"):
        raise Refused('signed manifest differs from release identity: distribution or package')
    return package['sha256']


def readme(name, version, setup_sha256, publisher):
    setup = f'{name}-{version}-Setup.exe'
    lines = [
        f'{name} {version} for Windows', '',
        'What this is',
        f'{name} is a desktop program for 64-bit Windows. This zip holds its installer, {setup} (version {version}).']
    if publisher:
        lines.append(f'Signed by: {publisher}')
    # Paul, 2026-10-18: the signing certificate is self-signed, so recipients check the hash first, then pass SmartScreen.
    lines += [
        '', 'How to install',
        '1. Right-click the zip and choose Extract All. Do not run the installer from inside the zip.',
        '2. Check the installer before running it. In PowerShell, in the extracted folder, run:',
        f'       Get-FileHash .\\{setup} -Algorithm SHA256',
        '   The Hash it prints must be exactly:',
        f'       {setup_sha256.upper()}',
        '   If it does not match, stop and tell the person who sent you the zip.',
        f'3. Double-click {setup}.',
        '   Windows may say "Windows protected your PC" and "Unknown publisher". That is expected:',
        "   the installer is signed with Melange Systems' own certificate, which your PC does not know.",
        '   If the hash in step 2 matched, click More info, then Run anyway.',
        '4. Setup is a step-by-step wizard. When it asks who to install for, keep "Only for me".',
        f'   That installs {name} for your Windows account only and does not need an administrator.',
        '   ("Anyone who uses this computer" asks for administrator permission; you do not need it.)',
        '5. Setup lets you choose the install folder; the suggested one is fine.',
        f'6. Setup does not start {name} when it finishes. Open it from the Start menu or the Desktop shortcut.',
        'To remove it later, use Windows Settings > Apps. Removing it keeps your data.',
        '', 'Checking the zip itself (optional)',
        f'If you were also sent {name}-{version}-windows.zip.sha256, run this in the folder with the zip:',
        f'    Get-FileHash .\\{name}-{version}-windows.zip -Algorithm SHA256',
        'The Hash it prints must match the one in that file (PowerShell prints capital letters; the file uses small ones).']
    return ('\r\n'.join(lines) + '\r\n').encode('utf-8')


def zip_info(name):
    info = zipfile.ZipInfo(name, STAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def write_zip(path, installer, setup_name, publisher, name, version):
    """Stream the installer in (installers run to hundreds of MB), then the README and checksum list; returns the installer sha256."""
    with zipfile.ZipFile(path, 'w') as archive:
        info = zip_info(setup_name)
        info.file_size = installer.stat().st_size  # lets zipfile choose ZIP64 for a streamed member
        digest = hashlib.sha256()
        with installer.open('rb') as source, archive.open(info, 'w') as target:
            while block := source.read(CHUNK):
                digest.update(block)
                target.write(block)
        setup_sha256 = digest.hexdigest()
        text = readme(name, version, setup_sha256, publisher)
        archive.writestr(zip_info('README.txt'), text)
        sums = {setup_name: setup_sha256, 'README.txt': hashlib.sha256(text).hexdigest()}
        archive.writestr(zip_info('SHA256SUMS'), ''.join(f'{sums[item]}  {item}\n' for item in sorted(sums)).encode('ascii'))
    return setup_sha256


def check_zip(path, setup_name, setup_sha256, signed_sha256):
    """Reopen the finished zip: exactly the three expected members, and the installer inside is the signed release installer."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if sorted(names) != sorted((setup_name, 'README.txt', 'SHA256SUMS')) or any(denied(item) for item in names):
            raise Refused('zip members are not exactly the installer, README.txt and SHA256SUMS')
        readback = hashlib.sha256()
        with archive.open(setup_name) as stored:
            while block := stored.read(CHUNK):
                readback.update(block)
    if not setup_sha256 == readback.hexdigest() == signed_sha256:
        raise Refused('zipped installer differs from the signed release installer')
    return sorted(names)


def publish(temporary, final):
    """Move a finished temp file to its final name without ever replacing a file that is already there."""
    try:
        os.link(temporary, final)  # unlike os.replace, os.link is atomic and fails if `final` exists (NTFS and POSIX)
    except FileExistsError as error:
        raise Refused(f'{final} already exists; it is never replaced') from error
    try:
        os.unlink(temporary)
    except BaseException:  # failing now must not leave `final` behind; os.link only succeeded because `final` did not exist, so it is the link this call just made
        os.unlink(final)
        raise


def build(app, bundle, state, out):
    name = NAMES[app]
    directory = bundle_dir(bundle, app)
    for entry in directory.iterdir():
        if denied(entry.name):
            raise Refused(f'bundle contains a file that must never be sent: {entry.name}')
    store = Store(state)
    index = github_release.verify_bundle(store, directory, github_release.REPOSITORIES[app])
    if index['appId'] != app:
        raise Refused(f"bundle is for {index['appId']}, not {app}")
    version = index['version']
    signed_sha256 = signed_installer_sha256(store, directory, app, index)
    installer = directory / f'{app}-{version}-win32-x64.exe'
    if installer.is_symlink() or not installer.is_file():
        raise Refused(f'installer is missing or not a regular file: {installer.name}')
    publisher = store.config().get('windowsPublisher')
    signature = authenticode(installer, publisher)
    zip_path = Path(out) / f'{name}-{version}-windows.zip'
    sum_path = zip_path.with_name(zip_path.name + '.sha256')
    for path in (zip_path, sum_path):
        if path.exists() or path.is_symlink():
            raise Refused(f'{path} already exists; it is never replaced')
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.send-zip-', suffix='.tmp', dir=zip_path.parent)
    os.close(handle)
    try:
        setup_name = f'{name}-{version}-Setup.exe'
        setup_sha256 = write_zip(temporary, installer, setup_name, publisher if signature == 'Valid' else None, name, version)
        members = check_zip(temporary, setup_name, setup_sha256, signed_sha256)
        zip_sha256 = file_hashes(temporary)['sha256']
        publish(temporary, zip_path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    sum_temporary = None
    try:
        handle, sum_temporary = tempfile.mkstemp(prefix='.send-zip-', suffix='.tmp', dir=zip_path.parent)
        os.close(handle)
        Path(sum_temporary).write_bytes(f'{zip_sha256}  {zip_path.name}\n'.encode('ascii'))
        publish(sum_temporary, sum_path)
    except BaseException:  # never leave our zip without its checksum: a rerun would refuse "already exists"
        if sum_temporary:
            Path(sum_temporary).unlink(missing_ok=True)
        zip_path.unlink(missing_ok=True)  # sum_path is never unlinked here: if it already existed, it is not ours
        raise
    return {'app': app, 'version': version, 'zip': str(zip_path), 'zipSha256': zip_sha256, 'members': members,
            'setupSha256': setup_sha256, 'authenticode': signature}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True, choices=sorted(NAMES))
    parser.add_argument('--bundle', type=Path, required=True, help="one app's prepared release directory, or the prepare output root holding <app>/")
    parser.add_argument('--state', type=Path, required=True, help='existing protected publisher signing state (verifies the signed bundle)')
    parser.add_argument('--out', type=Path, default=Path(__file__).resolve().parent / 'dist' / 'send',  # git-ignored by packaging/.gitignore
                        help='directory for the zip and its .sha256 (default ops/desktop/packaging/dist/send)')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(build(args.app, args.bundle, args.state, args.out)))
        return 0
    except (Refused, OSError, ValueError, KeyError, TypeError) as error:
        print('Send zip refused: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
