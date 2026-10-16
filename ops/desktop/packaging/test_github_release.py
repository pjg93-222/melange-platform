"""Synthetic packages exercise staging; native OS signing is an explicit test double."""
import base64
import copy
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parent))
import github_release as release
from desktop_admin import GATES, Store, Refused, b64, canonical, digest, file_hashes, verify_envelope


class FixtureSigner:
    verify_release_gates = Store.verify_release_gates

    def __init__(self, root):
        self.root = root
        root.mkdir()
        self.keys = {app: Ed25519PrivateKey.generate() for app in release.APPS}
        self.signed = []
        self.public = {'windowsPublisher': 'CN=Synthetic test signer', 'apps': {}}
        for app, key in self.keys.items():
            public = key.public_key().public_bytes_raw()
            key_id = digest(public)[:24]
            self.public['apps'][app] = {'active': key_id, 'publicKeys': {key_id: b64(public)}, 'transitions': []}

    def config(self):
        return self.public

    def sign(self, app, manifest):
        self.signed.append(app)
        payload = canonical(manifest)
        return {'schemaVersion': 1, 'keyId': self.public['apps'][app]['active'],
                'payload': b64(payload), 'signature': b64(self.keys[app].sign(payload))}


@pytest.fixture
def prepared_inputs(tmp_path, monkeypatch):
    if os.name != 'nt':
        pytest.skip('Windows package publication uses the Windows Authenticode gate')
    store = FixtureSigner(tmp_path / 'protected-state')
    verified = []
    def native_signature(argv, **kwargs):
        assert argv[0] == 'powershell.exe'
        verified.append(kwargs['env']['DESKTOP_VERIFY_PACKAGE'])
        return SimpleNamespace(stdout=json.dumps({'Status': 'Valid', 'Subject': 'CN=Synthetic test signer'}))
    monkeypatch.setattr('desktop_admin.subprocess.run', native_signature)
    sources = {app: {'repository': 'example/source', 'ref': 'main', 'commit': str(index)*40}
               for index, app in enumerate(sorted(release.APPS), 1)}
    spec = {'repository': 'example/desktop', 'version': '1.0.0', 'sequence': 1,
            'platform': 'win32', 'arch': 'x64', 'sourceRefs': sources, 'packages': {}}
    for app in sorted(release.APPS):
        package = tmp_path / (app + '.exe')
        package.write_bytes(b'SYNTHETIC NONINSTALLABLE FIXTURE ' + app.encode())
        receipt = tmp_path / (app + '.json')
        receipt.write_text(json.dumps({'schemaVersion': 1, 'appId': app, 'version': spec['version'],
                                      'platform': 'win32', 'arch': 'x64', 'sha256': file_hashes(package)['sha256'],
                                      'source': sources[app], 'osSignature': {'status': 'passed'},
                                      'gates': {name: {'status': 'passed', 'evidence': 'synthetic-test-only'} for name in GATES}}))
        spec['packages'][app] = {'package': str(package), 'receipt': str(receipt)}
    return store, spec, verified


def test_three_app_release_signs_exact_assets_and_only_public_material(tmp_path, prepared_inputs):
    store, spec, verified = prepared_inputs
    output = tmp_path / 'public-assets'
    index = release.prepare(store, spec, output)
    assert index['tag'] == 'desktop-v1.0.0' and index['published'] is False
    assert len(verified) == 3
    assert len(list(output.iterdir())) == 11
    for app in sorted(release.APPS):
        envelope = json.loads((output / f'{app}-win32-x64.manifest.json').read_text())
        manifest = verify_envelope(envelope, store.config()['apps'][app]['publicKeys'], app=app, platform='win32', arch='x64')
        assert manifest['purpose'] == 'melange-desktop-github-release'
        assert manifest['distribution'] == 'public-installations'
        assert manifest['repository'] == spec['repository']
        assert manifest['sourceRefs'] == spec['sourceRefs']
        package = output / manifest['package']['name']
        assert package.read_bytes() == Path(spec['packages'][app]['package']).read_bytes()
        assert json.loads((output / (app + '-trust.json')).read_text()) == {'transitions': []}
        assert str(store.root) not in (output / 'release.json').read_text()
    for line in (output / 'SHA256SUMS').read_text().splitlines():
        checksum, name = line.split('  ')
        assert file_hashes(output / name)['sha256'] == checksum
    assert not any(path.name.endswith('.key') or 'receipt' in path.name or path.suffix == '.sqlite3' for path in output.iterdir())
    assert release.verify_bundle(store, output) == index


@pytest.mark.parametrize('mutation', ['package', 'extra', 'index', 'checksum', 'manifest', 'trust'])
def test_upload_verification_refuses_changed_or_extra_bundle_files(tmp_path, prepared_inputs, mutation):
    store, spec, _ = prepared_inputs
    output = tmp_path / 'public-assets'
    release.prepare(store, spec, output)
    if mutation == 'package':
        (output / 'melange-1.0.0-win32-x64.exe').write_bytes(b'changed')
    elif mutation == 'extra':
        (output / 'private-state.json').write_text('{}')
    elif mutation == 'index':
        index = json.loads((output / 'release.json').read_text()); index['repository'] = 'different/repository'
        (output / 'release.json').write_text(json.dumps(index))
    elif mutation == 'checksum':
        (output / 'SHA256SUMS').write_text('')
    elif mutation == 'manifest':
        envelope = json.loads((output / 'melange-win32-x64.manifest.json').read_text())
        envelope['signature'] = b64(b'0'*64)
        (output / 'melange-win32-x64.manifest.json').write_text(json.dumps(envelope))
    else:
        (output / 'melange-trust.json').write_text('{"transitions":[{}]}')
    with pytest.raises(Refused):
        release.verify_bundle(store, output)


def test_preparation_is_idempotent_but_refuses_equivocation_and_downgrade(tmp_path, prepared_inputs):
    store, spec, _ = prepared_inputs
    one, two = tmp_path / 'one', tmp_path / 'two'
    release.prepare(store, spec, one)
    release.prepare(store, {**spec, 'repository': 'Example/Desktop'}, two)
    assert len(store.signed) == 3
    for path in one.iterdir():
        assert path.read_bytes() == (two / path.name).read_bytes()
    changed = copy.deepcopy(spec)
    changed['sourceRefs']['melange']['commit'] = 'a'*40
    receipt_path = Path(changed['packages']['melange']['receipt'])
    receipt = json.loads(receipt_path.read_text()); receipt['source'] = changed['sourceRefs']['melange']
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(Refused, match='already binds'):
        release.prepare(store, changed, tmp_path / 'equivocation')
    receipt['source'] = spec['sourceRefs']['melange']; receipt_path.write_text(json.dumps(receipt))
    changed = {**spec, 'sequence': 2}
    with pytest.raises(Refused, match='must advance'):
        release.prepare(store, changed, tmp_path / 'same-version')
    assert not (tmp_path / 'equivocation').exists() and not (tmp_path / 'same-version').exists()


def test_gate_failure_and_source_mismatch_never_emit_signed_assets(tmp_path, prepared_inputs):
    store, spec, _ = prepared_inputs
    receipt_path = Path(spec['packages']['melange']['receipt'])
    receipt = json.loads(receipt_path.read_text())
    receipt['source']['commit'] = 'f'*40
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(Refused, match='committed source'):
        release.prepare(store, spec, tmp_path / 'wrong-source')
    receipt['source'] = spec['sourceRefs']['melange']
    receipt['gates']['security']['status'] = 'failed'
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(Refused, match='gates must pass'):
        release.prepare(store, spec, tmp_path / 'failed-gate')
    assert store.signed == []
    assert not (tmp_path / 'wrong-source').exists() and not (tmp_path / 'failed-gate').exists()


def test_staged_package_mutation_and_missing_native_identity_are_refused(tmp_path, prepared_inputs):
    store, spec, _ = prepared_inputs
    store.public['windowsPublisher'] = None
    with pytest.raises(Refused, match='publisher identity'):
        release.prepare(store, spec, tmp_path / 'no-native-identity')
    store.public['windowsPublisher'] = 'CN=Synthetic test signer'
    original = store.verify_release_gates
    def mutate(receipt, manifest, package):
        original(receipt, manifest, package)
        package.write_bytes(b'changed-after-gate')
    store.verify_release_gates = mutate
    with pytest.raises(Refused, match='changed during verification'):
        release.prepare(store, spec, tmp_path / 'changed-package')
    assert store.signed == []


def test_unreviewed_platform_or_partial_app_release_is_refused(tmp_path, prepared_inputs):
    store, spec, _ = prepared_inputs
    for changes in ({'platform': 'darwin'}, {'sequence': True}, {'repository': 'https://github.com/example/apps'},
                    {'repository': 'a'*101 + '/app'}, {'version': str(2**53)+'.0.0'},
                    {'packages': None}, {'version': None}, {'sourceRefs': {}},
                    {'sourceRefs': {**spec['sourceRefs'], 'melange': {**spec['sourceRefs']['melange'], 'repository': 'a/' + 'b'*101}}}):
        with pytest.raises(Refused):
            release.prepare(store, {**spec, **changes}, tmp_path / 'invalid')
