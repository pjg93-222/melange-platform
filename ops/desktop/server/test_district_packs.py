import base64
import contextlib
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import desktop_admin
from desktop_admin import Refused, Store
from access_store import AccessStore
import district_packs as packs

CLIENT = Path(__file__).resolve().parent.parent / 'client'
NODE = shutil.which('node')
SIGNED = {'schemaVersion', 'datasetId', 'revisionId', 'parentRevisionIds', 'districtId', 'kind', 'adapterVersion', 'objects', 'counts',
          'purpose', 'serverId', 'appId', 'originPrincipalId', 'originDeviceId', 'committedAt', 'catalogSequence'}  # dataset-protocol.cjs SIGNED
SECRETS = ('.secret', '.street-salt', '.walk-salt', '.desktop-password-lock-disabled', '.demo-key', 'migration-state.json')
OPENED = None  # list while recording


def _audit(event, args):
    if OPENED is not None and event in ('open', 'sqlite3.connect'):
        OPENED.append((event, str(args[0])))


sys.addaudithook(_audit)


def store(path, rows, wal=False):
    with contextlib.closing(sqlite3.connect(path)) as db:
        if wal:
            db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS t (x TEXT)')
        db.executemany('INSERT INTO t VALUES (?)', [(row,) for row in rows])
        db.commit()


def stamp(path, district):
    """Terrier's metadata.source_sha256 row (a JSON string), as build_street/build_contacts write it."""
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.execute('INSERT OR REPLACE INTO metadata VALUES (?, ?)', ('source_sha256', json.dumps(packs.SOURCE_SHA256[district])))
        db.commit()


def make_district(root, district='co-05', marker='a'):
    """A synthetic Terrier district directory: allowlisted stores plus every file a pack must never read."""
    data = Path(root)
    data.mkdir(parents=True)
    for name in ('contacts', 'roads', 'scores', 'street', 'walk'):
        store(data / (name + '.sqlite3'), [name + '-' + marker], wal=name == 'street')
    for name in ('street', 'contacts'):
        stamp(data / (name + '.sqlite3'), district)
    (data / 'density.json').write_text('{"density":1}', encoding='utf-8')
    (data / 'credit-quality.json').write_text('{"schema":"credit-quality-v1"}', encoding='utf-8')
    for name in SECRETS:
        (data / name).write_text('synthetic secret', encoding='utf-8')
    for folder in ('audit', 'logs', 'backups', 'migration-backups', 'walk-backups'):
        (data / folder).mkdir()
        (data / folder / 'x.sqlite3').write_text('synthetic', encoding='utf-8')
    prefix = packs.SNAPSHOT_PREFIX[district]
    active, inactive = prefix + '0123456789ab', prefix + 'ba9876543210'
    for key in (active, inactive):
        snapshot = data / 'snapshots' / key
        snapshot.mkdir(parents=True)
        for name in packs.FILES_FOR['cu-summary-v4']:
            value = {'schema': 'cu-summary-v4', 'snapshot': key} if name == 'manifest.json' else {'name': name, 'key': key}
            (snapshot / name).write_text(json.dumps(value, separators=(',', ':')), encoding='utf-8')
    with contextlib.closing(sqlite3.connect(data / 'accounts.sqlite3')) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE research_snapshot (id INTEGER PRIMARY KEY, key TEXT, manifest TEXT, active BOOL, created_at TEXT)')
        db.executemany('INSERT INTO research_snapshot(key,manifest,active,created_at) VALUES (?,?,?,?)',
                       [(active, '{}', 1, 'now'), (inactive, '{}', 0, 'now')])
        db.commit()
    return data


class DistrictPackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        publisher = Store(self.root / 'state')
        publisher.init()
        self.access = AccessStore(publisher)
        self.access.initialize()
        environment = patch.dict(os.environ, {'LOCALAPPDATA': str(self.root / 'local')})  # never the real pack store
        environment.start()
        self.addCleanup(environment.stop)
        self.packs = self.root / 'local' / 'MelangeDesktopAdmin' / 'packs'

    def build(self, district='co-05', name='build', marker='a'):
        data = self.root / ('data-' + district)
        if not data.exists():
            make_district(data, district, marker)
        packs.build(district, data, self.root / name)
        return self.root / name

    def payload(self, envelope):
        raw = base64.b64decode(envelope['payload'], validate=True)
        public = self.access.config()['publicKeys'][envelope['keyId']]
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public)).verify(base64.b64decode(envelope['signature']), raw)
        return json.loads(raw)

    def test_build_reads_only_the_allowlist_and_never_opens_secrets(self):
        global OPENED
        data = make_district(self.root / 'data')
        OPENED = []
        try:
            files = packs.build('co-05', data, self.root / 'build')
        finally:
            opened, OPENED = OPENED, None
        self.assertEqual(files, sorted(packs.REQUIRED + packs.OPTIONAL + packs.BUNDLE))
        read = [Path(path) for event, path in opened if event == 'open' and path.startswith(str(data))]
        sqlite = [path for event, path in opened if event == 'sqlite3.connect' and str(data.as_uri()) in path]
        self.assertTrue(read and sqlite)
        for path in read:
            self.assertNotIn(path.name, SECRETS + ('accounts.sqlite3',))
            self.assertFalse({'audit', 'logs', 'backups', 'migration-backups', 'walk-backups'} & set(path.relative_to(data).parts))
            self.assertNotIn('ba9876543210', str(path))  # inactive snapshot
        for uri in sqlite:
            self.assertTrue(uri.endswith('?mode=ro'), uri)  # accounts and every source store: read-only, never copied
        self.assertIn(data.joinpath('accounts.sqlite3').as_uri() + '?mode=ro', sqlite)
        produced = os.listdir(self.root / 'build')
        self.assertFalse([name for name in produced if name in packs.FORBIDDEN or name.startswith(packs.FORBIDDEN_PREFIXES)])

    def test_copies_are_delete_mode_checked_and_include_uncheckpointed_wal(self):
        data = make_district(self.root / 'data')
        live = sqlite3.connect(data / 'street.sqlite3')  # a live writer keeps its WAL frames uncheckpointed
        self.addCleanup(live.close)
        live.execute('INSERT INTO t VALUES (?)', ('live-row',))
        live.commit()
        self.assertTrue((data / 'street.sqlite3-wal').stat().st_size > 0)
        packs.build('co-05', data, self.root / 'build')
        for name in ('contacts', 'roads', 'scores', 'street', 'walk'):
            copy = self.root / 'build' / (name + '.sqlite3')
            self.assertEqual(copy.read_bytes()[18:20], b'\x01\x01')
            with contextlib.closing(sqlite3.connect(copy)) as db:
                self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
                rows = [row[0] for row in db.execute('SELECT x FROM t')]
            self.assertEqual(rows, [name + '-a'] + (['live-row'] if name == 'street' else []))
        self.assertEqual(sorted(p.name for p in (self.root / 'build').iterdir() if p.name.endswith(('-wal', '-shm', '-journal'))), [])

    def test_output_inside_onedrive_or_git_is_refused_and_nothing_written(self):
        data = make_district(self.root / 'data')
        (self.root / 'repo').mkdir()
        (self.root / 'repo' / '.git').write_text('gitdir: elsewhere', encoding='utf-8')  # a worktree's .git is a file
        for out in (self.root / 'OneDrive - Org' / 'pack', self.root / 'repo' / 'nested' / 'pack'):
            with self.assertRaises(Refused):
                packs.build('co-05', data, out)
            self.assertFalse(out.exists())
        with patch.dict(os.environ, {'OneDrive': str(self.root / 'synced')}), self.assertRaises(Refused):
            packs.build('co-05', data, self.root / 'synced' / 'pack')
        with self.assertRaises(Refused):
            packs.refuse_synced(Path(__file__))  # this checkout is a git worktree (and under OneDrive)

    def test_bundle_zip_is_deterministic_exact_and_importable_shape(self):
        first, second = self.build(name='one'), self.build(name='two')
        for name in packs.REQUIRED + packs.OPTIONAL + packs.BUNDLE:
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes(), name)
        with zipfile.ZipFile(first / 'bundle.zip') as archive:
            self.assertEqual(archive.namelist(), list(packs.FILES_FOR['cu-summary-v4']))
            self.assertEqual({info.date_time for info in archive.infolist()}, {(1980, 1, 1, 0, 0, 0)})
            self.assertEqual(json.loads(archive.read('manifest.json'))['snapshot'], 'co-cd005-0123456789ab')
        self.assertEqual((first / 'bundle.zip.sha256').read_text(encoding='ascii'), hashlib.sha256((first / 'bundle.zip').read_bytes()).hexdigest() + '\n')

    def test_publish_chains_parents_and_skips_an_unchanged_district(self):
        first = packs.publish(self.access, 'co-05', self.build(), keep_build=True)
        self.assertIsNone(packs.publish(self.access, 'co-05', self.build(name='same')))
        self.assertFalse((self.root / 'same').exists())  # unchanged is still a successful publish: plaintext deleted
        one = self.payload(first)
        self.assertEqual(set(one), SIGNED)
        self.assertEqual((one['catalogSequence'], one['parentRevisionIds'], one['kind'], one['appId']), (1, [], 'terrier.district-data.v1', 'terrier'))
        server = self.access.config()['serverId']
        self.assertEqual((one['originPrincipalId'], one['originDeviceId']), (server, server))
        self.assertEqual(one['datasetId'], str(uuid.uuid5(uuid.UUID(server), 'co-05')))
        self.assertEqual([item['role'] for item in one['objects']], sorted(packs.REQUIRED + packs.OPTIONAL + packs.BUNDLE))
        for item in one['objects']:
            stored = self.packs / 'objects' / item['sha256']
            self.assertEqual(sorted(item), ['rawSize', 'role', 'sha256', 'size'])
            self.assertEqual(stored.stat().st_size, item['size'])
            self.assertEqual(gzip.decompress(stored.read_bytes()), (self.root / 'build' / item['role']).read_bytes())
            self.assertEqual(item['rawSize'], (self.root / 'build' / item['role']).stat().st_size)
        other = self.payload(packs.publish(self.access, 'or-05', self.build('or-05', 'or')))
        self.assertEqual((other['catalogSequence'], other['parentRevisionIds']), (2, []))
        self.assertNotEqual(other['datasetId'], one['datasetId'])
        store(self.root / 'data-co-05' / 'walk.sqlite3', ['walked'])
        two = self.payload(packs.publish(self.access, 'co-05', self.build(name='changed')))
        self.assertEqual((two['catalogSequence'], two['parentRevisionIds'], two['datasetId']), (3, [one['revisionId']], one['datasetId']))
        self.assertEqual(sorted(p.name for p in (self.packs / 'revisions').iterdir()), ['1.json', '2.json', '3.json'])
        self.assertFalse((self.access.root / 'packs').exists())  # never in the Documents-based state root
        with self.assertRaises(Refused):
            packs.publish(self.access, 'ct-01', self.build(name='again'))  # a co-05 build never publishes to another district

    def test_cli_publish_deletes_the_plaintext_build_unless_kept_or_refused(self):
        def run(*args, store=()):
            stderr = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
                code = desktop_admin.main(['--state', str(self.access.root), *store, 'pack-publish', '--district', *args])
            return code, 'WARNING: plaintext voter data remains in' in stderr.getvalue()
        self.assertEqual(run('or-05', '--build', str(self.build(name='wrong'))), (2, True))  # refused: kept for a retry, warned
        self.assertTrue((self.root / 'wrong').is_dir())
        self.assertEqual(run('co-05', '--build', str(self.root / 'wrong'), '--keep-build'), (0, True))
        self.assertTrue((self.root / 'wrong' / 'street.sqlite3').is_file())
        self.assertEqual(run('co-05', '--build', str(self.root / 'wrong')), (0, False))  # unchanged, still deleted, no warning
        self.assertFalse((self.root / 'wrong').exists())
        self.assertEqual([p.name for p in (self.packs / 'revisions').iterdir()], ['1.json'])
        other = self.root / 'other-packs'
        self.assertEqual(run('co-05', '--build', str(self.build(name='wrong')), store=('--packs', str(other))), (0, False))
        self.assertEqual([p.name for p in (other / 'revisions').iterdir()], ['1.json'])  # --packs overrides the default store

    def test_stores_built_from_another_districts_voter_file_are_refused(self):
        data = make_district(self.root / 'data')
        with self.assertRaises(Refused) as caught:
            packs.build('or-05', data, self.root / 'build')  # co-05 stores, or-05 bundle state would not even be read
        self.assertIn('street.sqlite3 was not built from the or-05 voter file', str(caught.exception))
        self.assertFalse((self.root / 'build').exists())  # refused before anything was copied
        stamp(data / 'contacts.sqlite3', 'ct-01')  # a mixed directory: street co-05, contacts ct-01
        with self.assertRaisesRegex(Refused, 'contacts.sqlite3 was not built from the co-05'):
            packs.build('co-05', data, self.root / 'build')
        stamp(data / 'contacts.sqlite3', 'co-05')
        with contextlib.closing(sqlite3.connect(data / 'street.sqlite3')) as db:
            db.execute("DELETE FROM metadata")
            db.commit()
        with self.assertRaisesRegex(Refused, 'street.sqlite3 was not built'):  # no recorded source is refused too
            packs.build('co-05', data, self.root / 'build')
        build = self.build('or-05', 'or')  # publish re-checks the stores it signs, not only the bundle manifest
        stamp(build / 'street.sqlite3', 'co-05')
        with self.assertRaisesRegex(Refused, 'street.sqlite3 was not built from the or-05'):
            packs.publish(self.access, 'or-05', build)
        self.assertTrue(build.is_dir())

    @unittest.skipUnless(os.name == 'nt', 'Windows ACL')
    def test_build_and_pack_store_are_owner_and_system_only(self):
        build = self.build()
        packs.publish(self.access, 'co-05', build, keep_build=True)
        for folder in (build, self.packs):
            acl = subprocess.run(['icacls', str(folder)], capture_output=True, text=True, check=True).stdout
            entries = [line.strip().removeprefix(str(folder)).strip() for line in acl.splitlines()[:-1] if line.strip()]
            self.assertEqual(len(entries), 2, acl)  # the owner and SYSTEM, nothing inherited
            self.assertFalse([entry for entry in entries if '(I)' in entry], acl)
            self.assertTrue(any('SYSTEM:(OI)(CI)(F)' in entry for entry in entries), acl)

    def test_revisions_are_cached_until_the_folder_changes_and_a_corrupt_file_is_skipped(self):
        packs.publish(self.access, 'co-05', self.build())
        first = packs.published(self.packs)
        self.assertIs(packs.published(self.packs), first)  # unchanged folder: not parsed again
        (self.packs / 'revisions' / '2.json').write_bytes(b'{"payload":"not base64"}')
        with self.assertLogs('district_packs', 'WARNING') as logged:
            second = packs.published(self.packs)
        self.assertEqual(second, first)  # the corrupt file is skipped, the valid revision still served
        self.assertIn('2.json', logged.output[0])
        store(self.root / 'data-co-05' / 'walk.sqlite3', ['walked'])
        third = packs.publish(self.access, 'co-05', self.build(name='changed'))
        payload = self.payload(third)
        self.assertEqual(payload['catalogSequence'], 3)  # past the corrupt 2.json, never colliding with it
        with self.assertLogs('district_packs', 'WARNING'):
            self.assertEqual([p['catalogSequence'] for p, _ in packs.published(self.packs)], [1, 3])
        grant = {'app': 'terrier', 'districts': ['co-05']}
        with patch.object(packs, 'event_scope'):
            self.assertEqual(len(packs.catalog(self.packs, grant, 0, 25, 'co-05')['revisions']), 2)

    def client(self, envelope, districts, routes=()):
        """Run the client's own verifyRevision and route() (read-only require of ops/desktop/client)."""
        trust = self.access.descriptor('https://127.0.0.1:38443')
        script = ("const p=require(process.argv[1]),t=require(process.argv[2]);let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{"
                  "const v=JSON.parse(s);const r=p.verifyRevision(v.envelope,v.trust,v.grant,{now:v.now}).revision;"
                  "for(const x of v.routes)t.route(x,'GET');process.stdout.write(JSON.stringify(r));});")
        value = {'envelope': envelope, 'trust': trust, 'routes': list(routes), 'now': self.payload(envelope)['committedAt'] * 1000,
                 'grant': {'appId': 'terrier', 'districtIds': districts, 'serverId': trust['serverId']}}
        return subprocess.run([NODE, '-e', script, str(CLIENT / 'dataset-protocol.cjs'), str(CLIENT / 'dataset-transport.cjs')],
                              input=json.dumps(value), capture_output=True, text=True, timeout=60, cwd=self.root)

    def test_default_pack_store_is_local_app_data_and_a_synced_one_is_refused(self):
        self.assertEqual(packs.default_packs(), self.packs)
        with patch.dict(os.environ, {'OneDrive': str(self.root / 'synced')}), self.assertRaises(Refused):
            packs.publish(self.access, 'co-05', self.build(), packs=self.root / 'synced' / 'packs')
        self.assertFalse((self.root / 'synced').exists())

    @unittest.skipUnless(NODE, 'node is required to run the client verifier')
    def test_signature_verifies_with_the_client_protocol_and_routes(self):
        packs.publish(self.access, 'co-05', self.build())
        store(self.root / 'data-co-05' / 'walk.sqlite3', ['walked'])
        envelope = packs.publish(self.access, 'co-05', self.build(name='changed'))
        revision = self.payload(envelope)
        routes = ['/v1/data/catalog?after=0&limit=25&districtId=co-05',
                  '/v1/data/objects/%s?offset=0&length=4194304&revisionId=%s' % (revision['objects'][0]['sha256'], revision['revisionId'])]
        result = self.client(envelope, ['co-05'], routes)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), revision)  # including the parent chain
        self.assertTrue(all(len(route) <= 200 for route in routes))
        self.assertIn('Dataset district is not approved', self.client(envelope, ['or-05']).stderr)


if __name__ == '__main__':
    unittest.main()
