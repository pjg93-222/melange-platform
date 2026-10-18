"""Synthetic packages exercise staging; native OS signing is an explicit test double."""
import base64
import copy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parent))
import github_release as release
from desktop_admin import GATES, Store, Refused, b64, canonical, digest, file_hashes, verify_envelope

CLIENT = Path(__file__).resolve().parents[1] / 'client'
RUN = subprocess.run  # captured before the fixture replaces subprocess.run with the Authenticode double


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


def write_receipt(spec, app, sources=None):
    package = Path(spec['packages'][app]['package'])
    Path(spec['packages'][app]['receipt']).write_text(json.dumps({
        'schemaVersion': 1, 'appId': app, 'version': spec['packages'][app].get('version', spec['version']), 'platform': 'win32', 'arch': 'x64',
        'sha256': file_hashes(package)['sha256'], 'source': (sources or spec['sourceRefs'])[app],
        'osSignature': {'status': 'passed'},
        'gates': {name: {'status': 'passed', 'evidence': 'synthetic-test-only'} for name in GATES}}))


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
    sources = {app: {'repository': release.REPOSITORIES[app], 'ref': 'main', 'commit': str(index)*40}
               for index, app in enumerate(sorted(release.APPS), 1)}
    spec = {'version': '1.0.0', 'sequence': 1,
            'platform': 'win32', 'arch': 'x64', 'sourceRefs': sources, 'packages': {}}
    for app in sorted(release.APPS):
        package = tmp_path / (app + '.exe')
        package.write_bytes(b'SYNTHETIC NONINSTALLABLE FIXTURE ' + app.encode())
        spec['packages'][app] = {'package': str(package), 'receipt': str(tmp_path / (app + '.json'))}
        write_receipt(spec, app)
    return store, spec, verified


def test_each_app_is_signed_for_its_own_repository_with_only_public_material(tmp_path, prepared_inputs):
    store, spec, verified = prepared_inputs
    output = tmp_path / 'public-assets'
    indexes = release.prepare(store, spec, output)
    assert len(verified) == 3
    assert sorted(path.name for path in output.iterdir()) == sorted(release.APPS)
    assert {app: index['repository'] for app, index in indexes.items()} == {
        'melange': 'pjg93-222/melange', 'mentat': 'pjg93-222/mentat', 'terrier': 'pjg93-222/geo'}
    for app in sorted(release.APPS):
        directory, index = output / app, indexes[app]
        assert index['tag'] == 'desktop-v1.0.0' and index['published'] is False and index['appId'] == app
        assert len(list(directory.iterdir())) == 5
        envelope = json.loads((directory / f'{app}-win32-x64.manifest.json').read_text())
        manifest = verify_envelope(envelope, store.config()['apps'][app]['publicKeys'], app=app, platform='win32', arch='x64')
        assert manifest['purpose'] == 'melange-desktop-github-release'
        assert manifest['distribution'] == 'public-installations'
        assert manifest['repository'] == release.REPOSITORIES[app].lower()
        assert manifest['sourceRefs'] == spec['sourceRefs']
        package = directory / manifest['package']['name']
        assert package.read_bytes() == Path(spec['packages'][app]['package']).read_bytes()
        assert json.loads((directory / (app + '-trust.json')).read_text()) == {'transitions': []}
        assert str(store.root) not in (directory / 'release.json').read_text()
        assert not any(other in path.name for path in directory.iterdir() for other in release.APPS - {app})
        for line in (directory / 'SHA256SUMS').read_text().splitlines():
            checksum, name = line.split('  ')
            assert file_hashes(directory / name)['sha256'] == checksum
        assert not any(path.name.endswith('.key') or 'receipt' in path.name or path.suffix == '.sqlite3' for path in directory.iterdir())
        assert release.verify_bundle(store, directory) == index
        assert release.verify_bundle(store, directory, release.REPOSITORIES[app]) == index
    with sqlite3.connect(store.root / 'github-prepared.sqlite3') as ledger:
        assert sorted(ledger.execute('SELECT repository, app, sequence FROM prepared')) == [
            ('pjg93-222/geo', 'terrier', 1), ('pjg93-222/melange', 'melange', 1), ('pjg93-222/mentat', 'mentat', 1)]


def test_release_for_one_app_is_refused_for_another_apps_repository_or_identity(tmp_path, prepared_inputs, monkeypatch, capsys):
    store, spec, _ = prepared_inputs
    output = tmp_path / 'public-assets'
    release.prepare(store, spec, output)
    monkeypatch.setattr(release, 'Store', lambda _state: store)
    state = ['--state', str(store.root), '--verify-output', str(output / 'terrier')]
    assert release.main(state + ['--repository', 'pjg93-222/Geo']) == 0
    assert json.loads(capsys.readouterr().out)['releases'] == {'terrier': {'repository': 'pjg93-222/geo', 'tag': 'desktop-v1.0.0'}}
    assert release.main(state + ['--repository', 'pjg93-222/Melange']) == 1
    assert 'belongs to pjg93-222/Geo' in capsys.readouterr().err
    for app in sorted(release.APPS):
        for other in sorted(release.APPS - {app}):
            with pytest.raises(Refused, match='belongs to'):
                release.verify_bundle(store, output / app, release.REPOSITORIES[other])
            # A signed manifest cannot be relabelled as another app's.
            with pytest.raises(Refused, match='appId'):
                verify_envelope(json.loads((output / app / f'{app}-win32-x64.manifest.json').read_text()),
                                {**store.config()['apps'][app]['publicKeys'], **store.config()['apps'][other]['publicKeys']}, app=other)
        with pytest.raises(Refused, match='platform'):
            verify_envelope(json.loads((output / app / f'{app}-win32-x64.manifest.json').read_text()),
                            store.config()['apps'][app]['publicKeys'], app=app, platform='darwin')
    # Moving one app's manifest into another app's release is refused by key and identity.
    shutil.copyfile(output / 'melange' / 'melange-win32-x64.manifest.json', output / 'mentat' / 'mentat-win32-x64.manifest.json')
    with pytest.raises(Refused):
        release.verify_bundle(store, output / 'mentat')
    for change in ({'appId': 'mentat'}, {'repository': 'pjg93-222/mentat'}, {'appId': 'unknown'}):
        index = json.loads((output / 'terrier' / 'release.json').read_text())
        (output / 'terrier' / 'release.json').write_text(json.dumps({**index, **change}))
        with pytest.raises(Refused):
            release.verify_bundle(store, output / 'terrier')


def test_repository_overrides_may_only_restate_the_fixed_map(tmp_path, prepared_inputs):
    store, spec, _ = prepared_inputs
    for changes in ({'repository': 'pjg93-222/Melange'}, {'repositories': {'melange': 'pjg93-222/Geo'}},
                    {'repositories': {'terrier': 'pjg93-222/Terrier'}}, {'repositories': {'other': 'pjg93-222/Geo'}},
                    {'repositories': ['pjg93-222/Melange']}, {'repositories': {'mentat': None}}):
        with pytest.raises(Refused):
            release.prepare(store, {**spec, **changes}, tmp_path / 'invalid')
    assert store.signed == [] and not (tmp_path / 'invalid').exists()
    indexes = release.prepare(store, {**spec, 'repositories': {'terrier': 'PJG93-222/geo', 'melange': 'pjg93-222/Melange'}}, tmp_path / 'ok')
    assert indexes['terrier']['repository'] == 'pjg93-222/geo'


@pytest.mark.parametrize('mutation', ['package', 'extra', 'index', 'checksum', 'manifest', 'payload', 'trust', 'foreign'])
def test_upload_verification_refuses_changed_or_extra_release_files(tmp_path, prepared_inputs, mutation):
    store, spec, _ = prepared_inputs
    output = tmp_path / 'public-assets'
    release.prepare(store, spec, output)
    directory = output / 'melange'
    manifest_path = directory / 'melange-win32-x64.manifest.json'
    if mutation == 'package':
        (directory / 'melange-1.0.0-win32-x64.exe').write_bytes(b'changed')
    elif mutation == 'extra':
        (directory / 'private-state.json').write_text('{}')
    elif mutation == 'index':
        index = json.loads((directory / 'release.json').read_text()); index['repository'] = 'different/repository'
        (directory / 'release.json').write_text(json.dumps(index))
    elif mutation == 'checksum':
        (directory / 'SHA256SUMS').write_text('')
    elif mutation == 'manifest':
        envelope = json.loads(manifest_path.read_text())
        envelope['signature'] = b64(b'0'*64)
        manifest_path.write_text(json.dumps(envelope))
    elif mutation == 'payload':
        envelope = json.loads(manifest_path.read_text())
        payload = json.loads(base64.b64decode(envelope['payload'])); payload['repository'] = 'pjg93-222/geo'
        envelope['payload'] = b64(canonical(payload))
        manifest_path.write_text(json.dumps(envelope))
    elif mutation == 'trust':
        (directory / 'melange-trust.json').write_text('{"transitions":[{}]}')
    else:
        shutil.copyfile(output / 'mentat' / 'mentat-trust.json', directory / 'mentat-trust.json')
    with pytest.raises(Refused):
        release.verify_bundle(store, directory)


def test_preparation_is_idempotent_but_refuses_equivocation_and_downgrade(tmp_path, prepared_inputs):
    store, spec, _ = prepared_inputs
    one, two = tmp_path / 'one', tmp_path / 'two'
    release.prepare(store, spec, one)
    release.prepare(store, {**spec, 'repositories': {'melange': 'PJG93-222/MELANGE'}}, two)
    assert len(store.signed) == 3
    for path in one.rglob('*'):
        assert path.is_dir() or path.read_bytes() == (two / path.relative_to(one)).read_bytes()
    changed = copy.deepcopy(spec)
    changed['sourceRefs']['melange']['commit'] = 'a'*40
    write_receipt(changed, 'melange')
    with pytest.raises(Refused, match='already binds'):
        release.prepare(store, changed, tmp_path / 'equivocation')
    write_receipt(spec, 'melange')
    with pytest.raises(Refused, match='must advance'):
        release.prepare(store, {**spec, 'sequence': 2}, tmp_path / 'same-version')
    upgraded = {**spec, 'version': '1.0.2', 'sequence': 3}
    for app in sorted(release.APPS):
        write_receipt(upgraded, app)
    release.prepare(store, upgraded, tmp_path / 'three')
    lower = {**spec, 'version': '1.0.1', 'sequence': 2}
    later_lower_version = {**spec, 'version': '1.0.1', 'sequence': 4}
    for candidate, name in ((lower, 'downgraded-sequence'), (later_lower_version, 'downgraded-version')):
        for app in sorted(release.APPS):
            write_receipt(candidate, app)
        with pytest.raises(Refused, match='must advance'):
            release.prepare(store, candidate, tmp_path / name)
    assert len(store.signed) == 6
    for name in ('equivocation', 'same-version', 'downgraded-sequence', 'downgraded-version'):
        assert not (tmp_path / name).exists()


def test_sequence_ledger_is_per_app_even_across_a_legacy_repository(tmp_path, prepared_inputs):
    store, spec, _ = prepared_inputs
    release.prepare(store, spec, tmp_path / 'one')
    with sqlite3.connect(store.root / 'github-prepared.sqlite3') as ledger:
        ledger.execute("UPDATE prepared SET repository='example/desktop'")
    changed = {**spec, 'version': '1.0.1'}
    for app in sorted(release.APPS):
        write_receipt(changed, app)
    with pytest.raises(Refused, match='already binds'):
        release.prepare(store, changed, tmp_path / 'reused-sequence')
    assert len(store.signed) == 3 and not (tmp_path / 'reused-sequence').exists()


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


def test_unreviewed_platform_or_incomplete_release_is_refused(tmp_path, prepared_inputs):
    store, spec, _ = prepared_inputs
    melange = spec['packages']['melange']
    for changes in ({'platform': 'darwin'}, {'arch': 'arm64'}, {'sequence': True}, {'sequence': 0},
                    {'version': str(2**53)+'.0.0'}, {'version': '1.0.0-beta'},
                    {'packages': None}, {'packages': {}}, {'version': None}, {'sourceRefs': {}},
                    {'packages': {'melange': melange, 'unknown': melange}},
                    # A subset release still binds the source of all three apps (the client requires it).
                    {'packages': {'melange': melange}, 'sourceRefs': {app: spec['sourceRefs'][app] for app in ('melange', 'mentat')}},
                    {'packages': {'melange': {**melange, 'extra': 'x'}}},
                    {'packages': {'melange': {**melange, 'version': '1.0'}}},
                    {'packages': {'melange': {**melange, 'sequence': 0}}},
                    {'packages': {'melange': {'package': melange['package']}}},
                    {'sourceRefs': {**spec['sourceRefs'], 'melange': {**spec['sourceRefs']['melange'], 'repository': 'a/' + 'b'*101}}}):
        with pytest.raises(Refused):
            release.prepare(store, {**spec, **changes}, tmp_path / 'invalid')
    assert store.signed == []


def test_melange_only_release_is_signed_alone_and_binds_all_three_sources(tmp_path, prepared_inputs):
    store, spec, verified = prepared_inputs
    subset = {**spec, 'packages': {'melange': {**spec['packages']['melange'], 'version': '1.0.5', 'sequence': 7}}}
    write_receipt(subset, 'melange')
    output = tmp_path / 'melange-only'
    indexes = release.prepare(store, subset, output)
    assert list(indexes) == ['melange'] and store.signed == ['melange'] and len(verified) == 1
    assert sorted(path.name for path in output.iterdir()) == ['melange']
    index = indexes['melange']
    assert (index['tag'], index['version'], index['sequence']) == ('desktop-v1.0.5', '1.0.5', 7)
    envelope = json.loads((output / 'melange' / 'melange-win32-x64.manifest.json').read_text())
    manifest = verify_envelope(envelope, store.config()['apps']['melange']['publicKeys'], app='melange', platform='win32', arch='x64')
    assert manifest['sourceRefs'] == spec['sourceRefs'] and set(manifest['sourceRefs']) == release.APPS
    assert manifest['package']['name'] == 'melange-1.0.5-win32-x64.exe' and manifest['package']['path'] == '/v1/packages/melange/7/melange-1.0.5-win32-x64.exe'
    assert release.verify_bundle(store, output / 'melange', 'pjg93-222/Melange') == index
    with sqlite3.connect(store.root / 'github-prepared.sqlite3') as ledger:
        assert list(ledger.execute('SELECT repository, app, sequence, version FROM prepared')) == [('pjg93-222/melange', 'melange', 7, '1.0.5')]
    # The other apps' ledgers are untouched: a later three-app release at sequence 1 still prepares for them,
    # while melange must advance beyond its own last release.
    write_receipt(spec, 'melange')
    with pytest.raises(Refused, match='must advance'):
        release.prepare(store, spec, tmp_path / 'three-after-subset')
    later = {**spec, 'packages': {app: spec['packages'][app] for app in ('mentat', 'terrier')}}
    assert sorted(release.prepare(store, later, tmp_path / 'others')) == ['mentat', 'terrier']


def test_named_app_without_its_verified_inputs_is_refused(tmp_path, prepared_inputs, monkeypatch):
    store, spec, verified = prepared_inputs
    two = {**spec, 'packages': {app: spec['packages'][app] for app in ('melange', 'terrier')}}
    receipt = Path(two['packages']['terrier']['receipt'])
    receipt.unlink()
    with pytest.raises(FileNotFoundError):
        release.prepare(store, two, tmp_path / 'missing-receipt')
    write_receipt(two, 'terrier')
    Path(two['packages']['terrier']['package']).write_bytes(b'other bytes than the receipt binds')
    with pytest.raises(Refused, match='does not bind this package'):
        release.prepare(store, two, tmp_path / 'unbound-package')
    write_receipt(two, 'terrier')
    def unsigned_terrier(argv, **kwargs):
        status = 'NotSigned' if kwargs['env']['DESKTOP_VERIFY_PACKAGE'].endswith('terrier-1.0.0-win32-x64.exe') else 'Valid'
        return SimpleNamespace(stdout=json.dumps({'Status': status, 'Subject': 'CN=Synthetic test signer'}))
    monkeypatch.setattr('desktop_admin.subprocess.run', unsigned_terrier)
    with pytest.raises(Refused, match='not signed by the pinned Windows publisher'):
        release.prepare(store, two, tmp_path / 'unsigned-terrier')
    assert store.signed == []
    assert not any((tmp_path / name).exists() for name in ('missing-receipt', 'unbound-package', 'unsigned-terrier'))
    assert not (store.root / 'github-prepared.sqlite3').exists() or not list(sqlite3.connect(store.root / 'github-prepared.sqlite3').execute('SELECT * FROM prepared'))


def test_unchanged_client_accepts_each_manifest_only_for_its_own_repository(tmp_path, prepared_inputs):
    """Cross-check against the shipped receiver (client/trust.cjs + github-source.cjs githubPolicy)."""
    node = shutil.which('node')
    # The locked dependencies may come from this checkout (npm ci) or from NODE_PATH.
    if not node or RUN([node, '-e', "require.resolve('semver')"], cwd=CLIENT, capture_output=True, timeout=30).returncode != 0:
        pytest.skip('client receiver needs node and the locked client dependencies (npm ci or NODE_PATH)')
    store, spec, _ = prepared_inputs
    output = tmp_path / 'public-assets'
    release.prepare(store, spec, output)
    envelopes = {app: json.loads((output / app / f'{app}-win32-x64.manifest.json').read_text()) for app in release.APPS}
    tampered = dict(envelopes['melange'])
    payload = json.loads(base64.b64decode(tampered['payload'])); payload['sequence'] = 9
    tampered['payload'] = b64(canonical(payload))
    cases = {}
    for app in sorted(release.APPS):
        keys = store.config()['apps'][app]['publicKeys']
        base = {'envelope': envelopes[app], 'keys': keys, 'repository': release.REPOSITORIES[app], 'appId': app, 'platform': 'win32'}
        cases[app + ':own-repository'] = base
        for other in sorted(release.APPS - {app}):
            cases[f'{app}:repository-of-{other}'] = {**base, 'repository': release.REPOSITORIES[other]}
            cases[f'{app}:installed-as-{other}'] = {**base, 'appId': other}
        cases[app + ':wrong-platform'] = {**base, 'platform': 'darwin'}
        cases[app + ':downgraded-sequence'] = {**base, 'previous': {'sequence': 2}}
        cases[app + ':downgraded-version'] = {**base, 'version': '1.0.1'}
    cases['melange:tampered-payload'] = {**cases['melange:own-repository'], 'envelope': tampered}
    script = ("const {verifyEnvelope}=require(process.argv[1]);const cases=JSON.parse(require('fs').readFileSync(0,'utf8'));"
              "const out={};for(const [name,c] of Object.entries(cases)){try{verifyEnvelope(c.envelope,{source:'github',repository:c.repository,publicKeys:c.keys},"
              "{appId:c.appId,platform:c.platform,arch:'x64',version:c.version||'1.0.0'},c.previous||{});out[name]='accepted';}catch(error){out[name]=error.message;}}"
              "process.stdout.write(JSON.stringify(out));")
    result = RUN([node, '-e', script, str(CLIENT / 'trust.cjs')], input=json.dumps(cases), capture_output=True,
                 text=True, timeout=60, check=True)
    outcomes = json.loads(result.stdout)
    assert set(outcomes) == set(cases)
    for name, outcome in outcomes.items():
        if name.endswith(':own-repository'):
            assert outcome == 'accepted', name
        else:
            assert outcome != 'accepted', name
    assert 'policy does not match' in outcomes['melange:repository-of-terrier']
    assert 'Invalid update signature' == outcomes['melange:tampered-payload']


def test_unchanged_client_accepts_a_melange_only_release(tmp_path, prepared_inputs):
    """A subset release changes nothing the receiver checks: all three source references are still signed."""
    node = shutil.which('node')
    if not node or RUN([node, '-e', "require.resolve('semver')"], cwd=CLIENT, capture_output=True, timeout=30).returncode != 0:
        pytest.skip('client receiver needs node and the locked client dependencies (npm ci or NODE_PATH)')
    store, spec, _ = prepared_inputs
    subset = {**spec, 'packages': {'melange': {**spec['packages']['melange'], 'version': '1.0.5'}}}
    write_receipt(subset, 'melange')
    release.prepare(store, subset, tmp_path / 'melange-only')
    envelope = json.loads((tmp_path / 'melange-only' / 'melange' / 'melange-win32-x64.manifest.json').read_text())
    keys = store.config()['apps']['melange']['publicKeys']
    cases = {'own': {'repository': 'pjg93-222/Melange', 'appId': 'melange'}, 'other-repository': {'repository': 'pjg93-222/Geo', 'appId': 'melange'},
             'other-app': {'repository': 'pjg93-222/Melange', 'appId': 'terrier'}}
    script = ("const {verifyEnvelope}=require(process.argv[1]);const input=JSON.parse(require('fs').readFileSync(0,'utf8'));const out={};"
              "for(const [name,c] of Object.entries(input.cases)){try{verifyEnvelope(input.envelope,{source:'github',repository:c.repository,publicKeys:input.keys},"
              "{appId:c.appId,platform:'win32',arch:'x64',version:'1.0.4'},{});out[name]='accepted';}catch(error){out[name]=error.message;}}"
              "process.stdout.write(JSON.stringify(out));")
    result = RUN([node, '-e', script, str(CLIENT / 'trust.cjs')], input=json.dumps({'envelope': envelope, 'keys': keys, 'cases': cases}),
                 capture_output=True, text=True, timeout=60, check=True, cwd=CLIENT)
    outcomes = json.loads(result.stdout)
    assert outcomes['own'] == 'accepted', outcomes
    assert outcomes['other-repository'] != 'accepted' and outcomes['other-app'] != 'accepted'
