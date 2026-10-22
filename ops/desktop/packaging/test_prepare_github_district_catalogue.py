"""Catalogue preparation boundaries with ephemeral synthetic keys and no publisher/network processes."""
import base64
import copy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import tempfile
import unittest
from unittest import mock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_github_district_catalogue as catalogue


def fixture(testcase):
    temporary = tempfile.TemporaryDirectory(prefix='district-catalogue-test-')
    testcase.addCleanup(temporary.cleanup)
    tmp_path = Path(temporary.name)

    def replace(target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        testcase.addCleanup(patcher.stop)

    state = tmp_path / 'synthetic-publisher'
    state.mkdir()
    key = Ed25519PrivateKey.generate()  # Fixture lifetime only; no private material is exported.
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode('ascii')
    store = SimpleNamespace(root=state, db=state / 'admin.sqlite3', config_path=state / 'trust.json')
    store.db.write_bytes(b'synthetic existing publisher marker')
    config = {'apps': {'terrier': {'active': 'synthetic', 'publicKeys': {'synthetic': public}}}}
    store.config_path.write_text(json.dumps(config), encoding='utf-8')
    store.config = lambda: copy.deepcopy(config)
    signed, store_calls, api_calls, native_calls = [], [], [], []

    def sign(app, payload):
        assert app == 'terrier'
        signed.append(copy.deepcopy(payload))
        raw = catalogue.canonical(payload)
        return {'schemaVersion': 1, 'keyId': 'synthetic', 'payload': base64.b64encode(raw).decode('ascii'),
                'signature': base64.b64encode(key.sign(raw)).decode('ascii')}

    store.sign = sign
    store.init = lambda: testcase.fail('preparation must never initialize publisher state or keys')
    replace(catalogue, 'Store', lambda selected: store_calls.append(selected) or store)
    trust = tmp_path / 'synthetic-installed-trust.json'
    trust.write_text(json.dumps({'updatePublicKeysByApp': {'terrier': {'synthetic': public}}}), encoding='utf-8')
    manifest = tmp_path / 'manifest.json'
    # Native protocol acceptance is a separate test double below; these tests exercise preparation/ledger ordering.
    manifest.write_bytes(b'{"schema":"synthetic-test-only"}\n')
    options = SimpleNamespace(state=state, trust=trust, manifest=manifest, tag='district-testsets-2026-10-21',
                              commit='a' * 40, sequence=5, output=tmp_path / 'pointer' / 'current.json',
                              node='synthetic-node-never-executed')
    release = {'tag_name': options.tag, 'draft': False, 'prerelease': True, 'target_commitish': options.commit,
               'assets': [{'id': 123, 'name': 'manifest.json', 'size': manifest.stat().st_size}]}
    tag = {'ref': 'refs/tags/' + options.tag, 'object': {'type': 'commit', 'sha': options.commit}}
    values = {'release': release, 'tag': tag, 'manifest': manifest.read_bytes(), 'native_error': False}

    def api(path, *, binary=False):
        api_calls.append((path, binary))
        if path == 'releases/tags/' + options.tag:
            assert binary is False
            return copy.deepcopy(values['release'])
        if path == 'git/ref/tags/' + options.tag:
            assert binary is False
            return copy.deepcopy(values['tag'])
        assert path == 'releases/assets/123' and binary is True
        return values['manifest']

    def native(argv, **kwargs):
        assert argv[:2] == [options.node, '-e']
        assert Path(argv[-1]).resolve() == Path(__file__).resolve().parents[1] / 'client/github-district-protocol.cjs'
        assert 'p.pointer(v.envelope,v.keys)' in argv[2] and 'p.manifest(' in argv[2]
        assert kwargs['text'] is True and kwargs['capture_output'] is True and kwargs['check'] is True
        body = json.loads(kwargs['input'])
        assert body['keys'] == {'synthetic': public}
        assert base64.b64decode(body['manifest'], validate=True) == manifest.read_bytes()
        envelope = body['envelope']
        raw = base64.b64decode(envelope['payload'], validate=True)
        key.public_key().verify(base64.b64decode(envelope['signature'], validate=True), raw)
        payload = json.loads(raw)
        assert payload['manifest']['sha256'] == hashlib.sha256(manifest.read_bytes()).hexdigest()
        native_calls.append(body)
        if values['native_error']:
            raise subprocess.CalledProcessError(1, argv, stderr='synthetic protocol rejection')
        return SimpleNamespace(stdout='verified')

    replace(catalogue, 'api', api)
    replace(catalogue.subprocess, 'run', native)
    replace(catalogue.subprocess, 'check_output', lambda *_args, **_kwargs: testcase.fail('unexpected process or GitHub access'))

    def rows():
        ledger = state / 'github-district-catalogues.sqlite3'
        if not ledger.exists():
            return []
        with closing(sqlite3.connect(ledger)) as connection:
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='prepared'").fetchone():
                return []
            return connection.execute('SELECT sequence,digest,envelope FROM prepared ORDER BY sequence').fetchall()

    return SimpleNamespace(options=options, store=store, values=values, signed=signed, store_calls=store_calls,
                           api_calls=api_calls, native_calls=native_calls, rows=rows)


class CataloguePreparationTests(unittest.TestCase):
    def test_matching_ledger_is_idempotent_and_reuses_the_original_envelope_without_resigning(self):
        f = fixture(self)
        first = catalogue.prepare(f.options)
        content, modified = f.options.output.read_bytes(), f.options.output.stat().st_mtime_ns
        assert catalogue.prepare(f.options) == first
        assert f.options.output.read_bytes() == content and f.options.output.stat().st_mtime_ns == modified
        assert len(f.signed) == 1 and len(f.native_calls) == 2 and len(f.rows()) == 1
        sequence, digest, envelope = f.rows()[0]
        assert sequence == 5 and digest == first['pointerDigest']
        assert json.loads(envelope) == json.loads(content)
        assert first['manifestSha256'] == hashlib.sha256(f.options.manifest.read_bytes()).hexdigest()
        assert set(first) == {'repository', 'catalogSequence', 'pointerDigest', 'manifestSha256', 'releaseTag'}

    def test_reused_ledger_envelope_must_contain_the_exact_requested_payload(self):
        f = fixture(self)
        catalogue.prepare(f.options)
        different = copy.deepcopy(f.signed[0])
        different['catalogSequence'] += 1
        other_envelope = f.store.sign('terrier', different)
        # The envelope is genuinely signed by the fixture key and binds the same manifest, but is inconsistent with
        # the sequence/digest columns. Native signature/manifest validation alone cannot establish ledger consistency.
        with closing(sqlite3.connect(f.store.root / 'github-district-catalogues.sqlite3')) as connection:
            with connection:
                connection.execute('UPDATE prepared SET envelope=? WHERE sequence=?', (json.dumps(other_envelope), f.options.sequence))
        f.options.output = f.options.output.parent / 'new-output.json'
        original_rows = f.rows()
        with self.assertRaises(ValueError):
            catalogue.prepare(f.options)
        assert len(f.signed) == 2 and len(f.native_calls) == 1
        assert not f.options.output.exists() and f.rows() == original_rows

    def test_lower_or_equivocal_sequence_is_refused_before_signer_and_preserves_ledger(self):
        for change in ['lower', 'different_commit', 'different_manifest']:
            with self.subTest(change=change):
                f = fixture(self)
                catalogue.prepare(f.options)
                original_rows, original_output = f.rows(), f.options.output.read_bytes()
                f.options.output = f.options.output.parent / 'refused.json'
                if change == 'lower':
                    f.options.sequence -= 1
                elif change == 'different_commit':
                    f.options.commit = 'b' * 40
                    f.values['release']['target_commitish'] = f.options.commit
                    f.values['tag']['object']['sha'] = f.options.commit
                else:
                    f.options.manifest.write_bytes(b'{"schema":"different-synthetic-test-only"}\n')
                    f.values['manifest'] = f.options.manifest.read_bytes()
                    f.values['release']['assets'][0]['size'] = len(f.values['manifest'])
                with self.assertRaisesRegex(ValueError, 'rollback or sequence reuse'):
                    catalogue.prepare(f.options)
                assert len(f.signed) == 1 and len(f.native_calls) == 1
                assert f.rows() == original_rows and not f.options.output.exists()
                assert (f.options.output.parent / 'current.json').read_bytes() == original_output

    def test_public_release_tag_and_manifest_mismatch_stop_before_signer(self):
        for change in ['draft', 'stable', 'release_tag', 'release_commit', 'tag_ref', 'tag_commit', 'annotated_tag', 'manifest_bytes', 'missing_manifest', 'duplicate_manifest', 'oversized_manifest']:
            with self.subTest(change=change):
                f = fixture(self)
                if change == 'draft':
                    f.values['release']['draft'] = True
                elif change == 'stable':
                    f.values['release']['prerelease'] = False
                elif change == 'release_tag':
                    f.values['release']['tag_name'] = 'district-testsets-2026-10-22'
                elif change == 'release_commit':
                    f.values['release']['target_commitish'] = 'b' * 40
                elif change == 'tag_ref':
                    f.values['tag']['ref'] = 'refs/tags/other'
                elif change == 'tag_commit':
                    f.values['tag']['object']['sha'] = 'b' * 40
                elif change == 'annotated_tag':
                    f.values['tag']['object']['type'] = 'tag'
                elif change == 'manifest_bytes':
                    f.values['manifest'] = b'different public bytes'
                elif change == 'missing_manifest':
                    f.values['release']['assets'] = []
                elif change == 'duplicate_manifest':
                    f.values['release']['assets'] *= 2
                else:
                    f.values['release']['assets'][0]['size'] = 2 * 1024 * 1024 + 1
                with self.assertRaises(ValueError):
                    catalogue.prepare(f.options)
                assert not f.signed and not f.native_calls and not f.rows() and not f.options.output.exists()
                assert not (f.store.root / 'github-district-catalogues.sqlite3').exists()

    def test_uninitialized_publisher_is_never_created_or_given_keys(self):
        for missing in ['db', 'config_path']:
            with self.subTest(missing=missing):
                f = fixture(self)
                getattr(f.store, missing).unlink()
                before = sorted(path.name for path in f.store.root.iterdir())
                with self.assertRaisesRegex(ValueError, 'initialization is forbidden'):
                    catalogue.prepare(f.options)
                assert sorted(path.name for path in f.store.root.iterdir()) == before
                assert not f.api_calls and not f.signed and not f.native_calls and not f.rows()
                assert not (f.store.root / 'keys').exists() and not f.options.output.exists()

    def test_existing_active_key_must_match_installed_trust_before_any_network_or_signing(self):
        f = fixture(self)
        trust = {'updatePublicKeysByApp': {'terrier': {'synthetic': base64.b64encode(b'x' * 32).decode('ascii')}}}
        f.options.trust.write_text(json.dumps(trust), encoding='utf-8')
        before = f.options.trust.read_bytes(), f.store.config_path.read_bytes(), f.store.db.read_bytes()
        with self.assertRaisesRegex(ValueError, 'does not match installed Terrier trust'):
            catalogue.prepare(f.options)
        assert not f.api_calls and not f.signed and not f.native_calls and not f.rows()
        assert before == (f.options.trust.read_bytes(), f.store.config_path.read_bytes(), f.store.db.read_bytes())
        assert not (f.store.root / 'keys').exists()

    def test_differing_existing_output_is_never_replaced_and_does_not_commit_ledger(self):
        f = fixture(self)
        f.options.output.parent.mkdir()
        f.options.output.write_bytes(b'existing unrelated pointer bytes')
        original = f.options.output.read_bytes(), f.options.output.stat().st_mtime_ns
        with self.assertRaisesRegex(ValueError, 'Output already contains different pointer bytes'):
            catalogue.prepare(f.options)
        assert original == (f.options.output.read_bytes(), f.options.output.stat().st_mtime_ns)
        assert not f.rows()

    def test_native_protocol_rejection_does_not_write_pointer_or_commit_sequence(self):
        f = fixture(self)
        f.values['native_error'] = True
        with self.assertRaises(subprocess.CalledProcessError):
            catalogue.prepare(f.options)
        assert len(f.signed) == 1 and len(f.native_calls) == 1
        assert not f.options.output.exists() and not f.rows()


if __name__ == '__main__':
    unittest.main()
