# -*- coding: utf-8 -*-
"""PLANTED-DEFECT BATTERY for fec/audit/system_manifest_check.py.

A check nobody has watched fail is not a check.  This repo has shipped two that could never fail —
one asserted that a filename started with its own glob prefix — so every assertion in the
manifest-vs-disk section gets a control here that BREAKS it deliberately and proves it goes red for
the stated reason, not merely that it goes red.

  C1  a declared store is deleted from disk           -> A1 existence FAIL
  C2  a file matching a notice-severity absent_expected appears -> NOTICE, and no new FAIL
  C2b a file matching an alarm-severity absent_expected appears -> FAIL (the credential-leak class)
  C3  a store is truncated into a lower size_class     -> A2 size_class FAIL
  C4  an input is touched newer than its derivative    -> A3 ordering FAIL
  C5  one byte flips inside a hash_always member       -> A4 hash FAIL
  C6  a manifest carrying stores: []                   -> contract FAIL, not a vacuous PASS
  C7  a hash_always request on a GIT-TRACKED store     -> the PRODUCER refuses it (scope rule)
  C8  git bookkeeping (.git/index, refs/**, logs/**)   -> never selected as a sentinel, and the
      same names OUTSIDE a git directory still are (C8b); a git store's file_count is no longer
      asserted but an emptied object store still FAILS and a non-git store's drift still FAILS
      (C8c); a bookkeeping sentinel handed to the checker is refused, not honoured (C8d); and the
      producer's and the checker's copies of the predicate are held against each other (C8e)
  C9  a CHAIN OUTPUT the registry names in sentinel_excludes  -> never selected, while the same
      relpaths from the same candidate list in a store without the declaration still are, and are
      still hashed (C9b); sentinel_newest=False drops the by_time row and nothing else (C9c); an
      exclusion that would empty the pool is refused, not honoured (C9d); and the by_hashable
      rescue fires only where nothing else is hashable (C9e)

Plus the anti-vacuity guards themselves: a manifest from the future, an unresolvable
generator_commit, an in-process regeneration, a hand-edited hash_always outside the envelope, a
SECRET asking to be hashed, and a manifest whose every root is missing.

C7 is the only control here that exercises the PRODUCER rather than the checker, because the rule
it proves (Paul, 2026-08-27: hash_always covers only what git does not protect) is enforced at
emission.  It comes with its own negative control — the same request on an UNTRACKED store must be
GRANTED — because a guard that refuses everything is as dead as one that refuses nothing.

Each control builds its OWN fixture tree and its OWN manifest and runs in its OWN test, because a
crashing assertion hides every assertion after it.  Nothing here touches real project data: every
path lives under a fresh tempfile.mkdtemp().
"""
import os, sys, json, time, shutil, tempfile, hashlib, subprocess, unittest, unittest.mock, importlib.util

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

_SPEC = importlib.util.spec_from_file_location(
    'system_manifest_check', os.path.join(FEC, 'audit', 'system_manifest_check.py'))
SMC = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(SMC)

REPO = os.path.dirname(FEC)

PRODUCER_PATH = os.path.join(FEC, 'audit', 'build_system_manifest.py')


def load_producer(alias='build_system_manifest_under_test'):
    """Load the producer under a DELIBERATELY WRONG module name, and never register it.

    The checker fails any run where `build_system_manifest` is present in sys.modules — a manifest
    compared against the thing that made it proves nothing, and TestGuardNoRegeneration exists to
    keep that guard honest.  C7 has to call into the producer anyway, so it loads it under an alias
    and asserts afterwards that the real name never appeared.  Importing under the true name here
    would turn every other control in this file red for a reason none of them are about."""
    spec = importlib.util.spec_from_file_location(alias, PRODUCER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _head_commit():
    """A real commit so the generator_commit guard PASSES in the controls that are not about it."""
    try:
        r = subprocess.run(['git', '-C', REPO, 'rev-parse', 'HEAD'], capture_output=True,
                           text=True, encoding='utf-8', errors='replace', timeout=60)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return None


HEAD = _head_commit()

# Probed ONCE for the whole module: a fixture must stand in for the producer, and the producer
# records the volume->disk map it observed. In production the A6 disk-id check compares the
# PRODUCER's probe against the CHECKER's probe — two separate processes, minutes or days apart.
# Here both sides come from this one call, so that single sub-check is not what these controls
# test; the recompute controls below (TestGuardTopologyRecompute) are.
LIVE_DISK_MAP = SMC._live_volume_disk_map() or {}


def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _size_class(n):
    if n <= 0:              return 'empty'
    if n < (1 << 20):       return 'tiny'
    if n < (10 << 20):      return 'small'
    if n < (100 << 20):     return 'medium'
    if n < (1 << 30):       return 'large'
    if n < (10 << 30):      return 'huge'
    return 'massive'


class Recorder(object):
    """Stands in for audit_full's ck() / notice() so a control can assert on named verdicts."""

    def __init__(self):
        self.passed, self.failed, self.notices = [], [], []

    def emit(self, name, cond, detail=''):
        (self.passed if cond else self.failed).append((name, detail))

    def note(self, text):
        self.notices.append(text)

    def failed_names(self):
        return [n for n, _d in self.failed]

    def has_fail(self, fragment):
        return any(fragment in n for n, _d in self.failed)

    def fail_detail(self, fragment):
        for n, d in self.failed:
            if fragment in n:
                return d
        return ''

    def has_pass(self, fragment):
        return any(fragment in n for n, _d in self.passed)


class Fixture(object):
    """A throwaway root + a manifest that describes it truthfully.  Controls then break ONE thing."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix='sysman_fix_')
        self.stores = []
        self.absent = []
        # A fixture stands in for a producer run ON THIS HOST, so it records the OneDrive root this
        # host actually has. The fixture tree itself lives in %TEMP%, outside it — which is what
        # makes every fixture store legitimately single_disk and lets the recompute control bite.
        self.onedrive = SMC._live_onedrive_root()

    def destroy(self):
        shutil.rmtree(self.root, ignore_errors=True)

    # -- building blocks ---------------------------------------------------------------------
    def write_file(self, rel, data):
        full = os.path.join(self.root, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'wb') as fh:
            fh.write(data if isinstance(data, bytes) else data.encode('utf-8'))
        return full

    def add_file_store(self, sid, rel, cls='DERIVED', policy='hash_never', irrep=2,
                       derives_from=(), present=True):
        full = os.path.join(self.root, rel.replace('/', os.sep))
        st = os.stat(full) if os.path.exists(full) else None
        store = {
            'id': sid, 'root_id': 'fix', 'path': full, 'class': cls,
            'producer': 'test fixture', 'consumers': [], 'restore_route': 'rebuild the fixture',
            'cadence': 'never', 'irreplaceability': irrep,
            'integrity_policy_declared': policy, 'integrity_policy': policy,
            'derives_from': list(derives_from), 'counts_toward_totals': True, 'excludes': [],
            'note': None, 'root_present': True,
            # A fixture root lives in %TEMP%, outside any work tree, so a truthful producer row
            # records it as unprotected by git — which is what entitles it to hash_always at all.
            'git_tracked': False,
            'git_tracking_basis': 'root fix: not_a_work_tree',
            # MEASURED by the producer from the filesystem shape (HEAD + objects/ + refs/). A plain
            # fixture directory is not a git directory; the C8 controls set it True on a tree that
            # really has that shape, and the checker re-observes it rather than trusting this field.
            'git_dir_store': False,
            'measured': {
                'present': bool(st) and present, 'kind': 'file' if st else None,
                'file_count': 1 if st else 0, 'dirs_walked': 0,
                'bytes': st.st_size if st else 0,
                'size_class': _size_class(st.st_size) if st else 'empty',
                'newest_mtime_epoch': st.st_mtime if st else None,
                'oldest_mtime_epoch': st.st_mtime if st else None,
                'unreadable_entries': 0, 'walk_complete': True, 'budget_exceeded': False,
                'mtime_utc': None},
            'sqlite': None, 'hashes': None, 'content_sha256': None, 'sentinels': None,
            'rescue': {'coverage': None, 'via': None, 'basis': 'fixture'},
            'volume': (os.path.splitdrive(full)[0] or '').upper() or None,
            'disk_id': LIVE_DISK_MAP.get((os.path.splitdrive(full)[0] or '').upper()),
            'offbox_replica': None, 'single_disk': True,
            'durable_backup': False, 'topology_basis': 'fixture root is outside any OneDrive root',
            'durable_backup_basis': 'fixture'}
        if policy == 'hash_always' and st:
            store['hashes'] = {os.path.basename(full): _sha(full)}
        self.stores.append(store)
        return store

    def add_dir_store(self, sid, rel, cls='DERIVED', policy='hash_never', irrep=2):
        full = os.path.join(self.root, rel.replace('/', os.sep))
        os.makedirs(full, exist_ok=True)
        nbytes = nfiles = 0
        newest = None
        hashes = {}
        for dirpath, _d, files in os.walk(full):
            for name in files:
                fp = os.path.join(dirpath, name)
                st = os.stat(fp)
                nfiles += 1
                nbytes += st.st_size
                newest = st.st_mtime if newest is None or st.st_mtime > newest else newest
                r = os.path.relpath(fp, full).replace('\\', '/')
                hashes[r] = _sha(fp)
        store = {
            'id': sid, 'root_id': 'fix', 'path': full, 'class': cls,
            'producer': 'test fixture', 'consumers': [], 'restore_route': 'rebuild the fixture',
            'cadence': 'never', 'irreplaceability': irrep,
            'integrity_policy_declared': policy, 'integrity_policy': policy,
            'derives_from': [], 'counts_toward_totals': True, 'excludes': [], 'note': None,
            'root_present': True,
            'git_tracked': False,
            'git_tracking_basis': 'root fix: not_a_work_tree',
            # MEASURED by the producer from the filesystem shape (HEAD + objects/ + refs/). A plain
            # fixture directory is not a git directory; the C8 controls set it True on a tree that
            # really has that shape, and the checker re-observes it rather than trusting this field.
            'git_dir_store': False,
            'measured': {'present': True, 'kind': 'dir', 'file_count': nfiles, 'dirs_walked': 1,
                         'bytes': nbytes, 'size_class': _size_class(nbytes),
                         'newest_mtime_epoch': newest, 'oldest_mtime_epoch': newest,
                         'unreadable_entries': 0, 'walk_complete': True, 'budget_exceeded': False,
                         'mtime_utc': None},
            'sqlite': None, 'hashes': hashes if policy == 'hash_always' else None,
            'content_sha256': None, 'sentinels': None,
            'rescue': {'coverage': None, 'via': None, 'basis': 'fixture'},
            'volume': (os.path.splitdrive(full)[0] or '').upper() or None,
            'disk_id': LIVE_DISK_MAP.get((os.path.splitdrive(full)[0] or '').upper()),
            'offbox_replica': None, 'single_disk': True,
            'durable_backup': False, 'topology_basis': 'fixture root is outside any OneDrive root',
            'durable_backup_basis': 'fixture'}
        self.stores.append(store)
        return store

    def add_absent_expected(self, eid, base_rel, patterns, severity='notice'):
        base = os.path.join(self.root, base_rel.replace('/', os.sep))
        os.makedirs(base, exist_ok=True)
        self.absent.append({'id': eid, 'root_id': 'fix', 'base': base,
                            'patterns': list(patterns), 'severity': severity, 'depth': None,
                            'why': 'fixture negative fact', 'base_present': True,
                            'matches': [], 'scan_complete': True})

    def manifest(self, **overrides):
        man = {
            'schema': SMC.SCHEMA,
            'generated_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - 60)),
            'generated_epoch': time.time() - 60,
            'generator': 'fec/audit/build_system_manifest.py',
            'generator_commit': HEAD,
            'generator_commit_source': 'test fixture (real HEAD)',
            'generator_sha256': None,
            'root': REPO,
            'host': {'onedrive_root': self.onedrive, 'onedrive_root_source': 'fixture',
                     'physical_disk_count': 1, 'physical_disks': [], 'volumes': [],
                     'volume_disk_map': dict(LIVE_DISK_MAP), 'file_history_status': 'Stopped',
                     'file_history_enabled': False, 'restore_point_count': 0,
                     'probe': {'status': 'ok', 'detail': None}},
            'policy': {'hash_always_envelope': {'max_bytes': 8 * 1024 * 1024, 'max_files': 200,
                                                'min_irreplaceability': 2, 'secret_forbidden': True,
                                                'requires_complete_walk': True},
                       'mtime_tolerance_s': 2.0},
            'totals': {}, 'extinction': {}, 'policy_overrides': [],
            'roots': [{'id': 'fix', 'path': self.root, 'label': 'fixture root', 'present': True}],
            'stores': self.stores,
            'absent_expected': self.absent,
        }
        man.update(overrides)
        return man

    def write_manifest(self, **overrides):
        path = os.path.join(self.root, 'system_manifest.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(self.manifest(**overrides), fh, indent=1, default=str)
        # The checker asserts the manifest predates the run; back-date the file so a fixture
        # written microseconds ago is not rejected for the wrong reason.
        old = time.time() - 60
        os.utime(path, (old, old))
        return path


class ControlCase(unittest.TestCase):
    """Every control gets a fresh fixture; setUp/tearDown keep one crash from hiding the rest."""

    def setUp(self):
        self.fx = Fixture()
        self.rec = Recorder()

    def tearDown(self):
        self.fx.destroy()

    def run_check(self, manifest_path):
        return SMC.check_manifest(manifest_path, self.rec.emit, notice=self.rec.note,
                                  git_root=REPO)

    def assert_clean_baseline(self, mp):
        """Prove the fixture PASSES before a control breaks it — otherwise a later FAIL proves
        nothing about the defect we planted."""
        base = Recorder()
        SMC.check_manifest(mp, base.emit, notice=base.note, git_root=REPO)
        self.assertEqual(base.failed_names(), [],
                         'baseline fixture must be clean before planting a defect; got %s'
                         % base.failed_names())
        self.assertGreater(len(base.passed), 0, 'baseline emitted no checks at all')
        return base


# =============================================================================================
# C1 — delete a declared store
# =============================================================================================
class TestC1DeletedStore(ControlCase):
    def test_deleting_a_declared_store_fails_A1(self):
        self.fx.write_file('data/keep.json', '{"a":1}')
        self.fx.write_file('data/doomed.json', '{"b":2}')
        self.fx.add_file_store('fix.keep', 'data/keep.json')
        doomed = self.fx.add_file_store('fix.doomed', 'data/doomed.json')
        mp = self.fx.write_manifest()
        self.assert_clean_baseline(mp)

        os.remove(doomed['path'])                                     # <-- the planted defect

        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A1 fix.doomed still exists'),
                        'deleting a declared-present store did not fail A1; failures were %s'
                        % self.rec.failed_names())
        detail = self.rec.fail_detail('A1 fix.doomed still exists')
        self.assertIn('declared PRESENT', detail)
        self.assertIn('ABSENT', detail)
        self.assertTrue(self.rec.has_pass('A1 fix.keep still exists'),
                        'the surviving store should still pass — the failure must be specific')


# =============================================================================================
# C2 — a file appears that a notice-severity absent_expected says must not
# =============================================================================================
class TestC2AbsentExpectedNotice(ControlCase):
    def test_creating_an_absent_expected_file_raises_a_notice_not_a_failure(self):
        self.fx.write_file('data/keep.json', '{"a":1}')
        self.fx.add_file_store('fix.keep', 'data/keep.json')
        self.fx.add_absent_expected('no_network_json', 'watched', ('network.json',),
                                    severity='notice')
        mp = self.fx.write_manifest()
        base = self.assert_clean_baseline(mp)
        self.assertEqual(base.notices, [], 'baseline should raise no notice')

        self.fx.write_file('watched/network.json', '{}')               # <-- the planted defect

        self.run_check(mp)
        self.assertEqual(self.rec.failed_names(), [],
                         'a notice-severity absent_expected violation must NOT fail the gate; '
                         'failures were %s' % self.rec.failed_names())
        self.assertEqual(len(self.rec.notices), 1,
                         'expected exactly one NOTICE, got %r' % (self.rec.notices,))
        text = self.rec.notices[0]
        self.assertIn('no_network_json', text)
        self.assertIn('network.json', text)
        self.assertIn('disk now finds 1', text)


class TestC2bAbsentExpectedAlarm(ControlCase):
    def test_creating_an_alarm_severity_absent_expected_file_fails(self):
        self.fx.write_file('data/keep.json', '{"a":1}')
        self.fx.add_file_store('fix.keep', 'data/keep.json')
        self.fx.add_absent_expected('no_credentials_in_backup', 'backupdir',
                                    ('*key*.txt', '*.pem'), severity='alarm')
        mp = self.fx.write_manifest()
        self.assert_clean_baseline(mp)

        # A credential landing inside a cloud-synced backup directory is the class this exists for.
        self.fx.write_file('backupdir/nested/anthropic_key.txt', 'not-a-real-key')

        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A1 absent_expected no_credentials_in_backup holds'),
                        'an alarm-severity negative fact must FAIL, not merely notice; failures '
                        'were %s' % self.rec.failed_names())
        detail = self.rec.fail_detail('A1 absent_expected no_credentials_in_backup holds')
        self.assertIn('recorded 0 match', detail)
        self.assertIn('disk: 1 now match', detail)


# =============================================================================================
# C3 — truncate a store into a lower size_class
# =============================================================================================
class TestC3SizeClassDrop(ControlCase):
    def test_truncating_a_store_into_a_lower_size_class_fails_A2(self):
        # 'small' is >= 1 MiB; truncating to a few bytes drops it to 'tiny'.
        self.fx.write_file('data/big.bin', b'\x5a' * (2 * 1024 * 1024))
        store = self.fx.add_file_store('fix.big', 'data/big.bin')
        self.assertEqual(store['measured']['size_class'], 'small')
        mp = self.fx.write_manifest()
        self.assert_clean_baseline(mp)

        with open(store['path'], 'wb') as fh:                          # <-- the planted defect
            fh.write(b'\x5a' * 16)

        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A2 fix.big size_class agrees'),
                        'truncating a store did not fail A2; failures were %s'
                        % self.rec.failed_names())
        detail = self.rec.fail_detail('A2 fix.big size_class agrees')
        self.assertIn('manifest', detail)
        self.assertIn('size_class=small', detail)
        self.assertIn('size_class=tiny', detail)
        self.assertIn('disk', detail)


# =============================================================================================
# C4 — touch an input newer than its derivative
# =============================================================================================
class TestC4MtimeOrdering(ControlCase):
    def test_touching_an_input_newer_than_its_derivative_fails_A3(self):
        self.fx.write_file('data/input.json', '{"in":1}')
        self.fx.write_file('data/output.json', '{"out":1}')
        src = self.fx.add_file_store('fix.input', 'data/input.json')
        self.fx.add_file_store('fix.output', 'data/output.json',
                               derives_from=['fix.input'])
        # Establish the lawful ordering first: input older than derivative.
        base_t = time.time() - 3600
        os.utime(src['path'], (base_t, base_t))
        os.utime(os.path.join(self.fx.root, 'data', 'output.json'), (base_t + 60, base_t + 60))
        mp = self.fx.write_manifest()
        self.assert_clean_baseline(mp)

        os.utime(src['path'], (base_t + 600, base_t + 600))            # <-- the planted defect

        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A3 fix.output is not older than its input fix.input'),
                        'touching an input newer than its derivative did not fail A3; failures '
                        'were %s' % self.rec.failed_names())
        detail = self.rec.fail_detail('A3 fix.output is not older than its input fix.input')
        self.assertIn('derives_from', detail)
        self.assertIn('NEWER', detail)
        self.assertIn('stale', detail)


# =============================================================================================
# C5 — flip a byte inside a hash_always member
# =============================================================================================
class TestC5HashFlip(ControlCase):
    def test_flipping_one_byte_in_a_hash_always_member_fails_A4(self):
        self.fx.write_file('adjudicated/a.ndjson', 'alpha\n')
        self.fx.write_file('adjudicated/b.ndjson', 'bravo\n')
        store = self.fx.add_dir_store('fix.adjudicated', 'adjudicated', cls='SOURCE',
                                      policy='hash_always', irrep=4)
        self.assertEqual(len(store['hashes']), 2)
        mp = self.fx.write_manifest()
        self.assert_clean_baseline(mp)

        victim = os.path.join(store['path'], 'b.ndjson')                # <-- the planted defect
        with open(victim, 'rb') as fh:
            raw = bytearray(fh.read())
        raw[0] ^= 0x01                                                  # 'b' -> 'c': one byte, same size
        with open(victim, 'wb') as fh:
            fh.write(bytes(raw))

        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A4 fix.adjudicated every hashed file byte-identical'),
                        'a one-byte flip did not fail A4; failures were %s'
                        % self.rec.failed_names())
        detail = self.rec.fail_detail('A4 fix.adjudicated every hashed file byte-identical')
        self.assertIn('b.ndjson', detail)
        self.assertIn('manifest=', detail)
        self.assertIn('disk=', detail)
        # Size is unchanged, so A2 must stay green: this proves A4 caught it, not A2 by accident.
        self.assertTrue(self.rec.has_pass('A2 fix.adjudicated size_class agrees'),
                        'the byte flip preserved size, so A2 should still pass — otherwise the '
                        'A4 result is not what caught the defect')


class TestC5bHashSetGainsAFile(ControlCase):
    def test_adding_an_unhashed_file_to_a_hash_always_store_fails_A4(self):
        self.fx.write_file('adjudicated/a.ndjson', 'alpha\n')
        store = self.fx.add_dir_store('fix.adjudicated', 'adjudicated', cls='SOURCE',
                                      policy='hash_always', irrep=4)
        mp = self.fx.write_manifest()
        self.assert_clean_baseline(mp)

        self.fx.write_file('adjudicated/smuggled.ndjson', 'charlie\n')  # <-- the planted defect

        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A4 fix.adjudicated gained no unhashed file'),
                        'a file appearing inside a hash_always store went unnoticed; failures '
                        'were %s' % self.rec.failed_names())
        self.assertIn('smuggled.ndjson',
                      self.rec.fail_detail('A4 fix.adjudicated gained no unhashed file'))


# =============================================================================================
# C6 — a manifest with stores: []
# =============================================================================================
class TestC6DegenerateManifest(ControlCase):
    def test_empty_store_list_is_a_contract_failure_not_a_vacuous_pass(self):
        self.fx.write_file('data/keep.json', '{"a":1}')
        self.fx.add_file_store('fix.keep', 'data/keep.json')
        mp = self.fx.write_manifest(stores=[])                          # <-- the planted defect

        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A0 system manifest declares at least one store'),
                        'a manifest with no stores must FAIL the contract; failures were %s'
                        % self.rec.failed_names())
        self.assertIn('empty', self.rec.fail_detail('A0 system manifest declares at least one store'))
        # And it must not go on to emit agreement passes it never actually made.
        for name, _d in self.rec.passed:
            self.assertFalse(name.startswith(('A1 ', 'A2 ', 'A3 ', 'A4 ', 'A5 ', 'A6 ')),
                             'degenerate manifest still emitted an agreement PASS: %r' % name)


# =============================================================================================
# C7 — a hash_always request on a GIT-TRACKED store must be REFUSED by the producer
#
# Paul's ruling 2026-08-27, "use the narrower scope": hash_always covers only what git does not
# protect.  Where git tracks a store, git already holds its integrity — and a hash set recorded
# here goes stale on the next commit, so the audit reports a disagreement that is not a defect.
# The rule is enforced at EMISSION, so these controls drive the producer, not the checker.
#
# Every one of them resolves tracked-ness by MEASUREMENT against this repo's real git index, not
# by handing the resolver a flag: a control that asserts a boolean it set itself proves only that
# Python assigns variables.
# =============================================================================================
class TestC7GitTrackedRefusedHashAlways(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.BSM = load_producer()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='sysman_c7_')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _store(self, sid, tracked, basis='measured', declared='hash_always', irrep=4):
        """A store shaped exactly as main() hands it to resolve_policy, and INSIDE the envelope on
        every other predicate — so a refusal can only have come from the scope rule."""
        return {'id': sid, 'class': 'SOURCE', 'irreplaceability': irrep,
                'integrity_policy_declared': declared,
                'git_tracked': tracked, 'git_tracking_basis': basis,
                'measured': {'present': True, 'bytes': 1024, 'file_count': 1,
                             'walk_complete': True}}

    # -- the guard fires ------------------------------------------------------------------------
    def test_a_hash_always_request_on_a_git_tracked_store_is_refused_by_the_producer(self):
        idx = self.BSM._probe_git_index('live', REPO, True)
        self.assertEqual(idx.status, 'ok',
                         'this control needs a real git index for %s; probe said %s :: %s'
                         % (REPO, idx.status, idx.detail))
        self.assertGreater(idx.tracked_count, 0, 'git reported an empty index for this repo')

        # MEASURED, not asserted: git really does track this directory in this repo.
        tracked = idx.tracked('fec/audit')
        self.assertIs(tracked, True,
                      'fec/audit is not tracked in %s, so this control has nothing to plant into '
                      '— fix the control, do not weaken the rule' % REPO)

        overrides = []
        store = self._store('fix.tracked_code', tracked,
                            basis='root live: ok, %d tracked paths' % idx.tracked_count)
        policy = self.BSM.resolve_policy(store, overrides)         # <-- the planted request

        self.assertEqual(policy, 'hash_never',
                         'the producer honoured hash_always for a git-tracked store; it resolved '
                         'to %r' % policy)
        self.assertEqual(len(overrides), 1,
                         'the refusal was not recorded at all; overrides=%r' % (overrides,))
        o = overrides[0]
        self.assertEqual(o['store'], 'fix.tracked_code')
        self.assertEqual(o['from'], 'hash_always')
        self.assertEqual(o['to'], 'hash_never')
        self.assertEqual(o['refused_on'], 'git-tracked')
        self.assertIn('git tracks this store', o['reasons'][0])
        self.assertIn('integrity authority', o['reasons'][0])
        # sentinel_files would reintroduce the exact staleness the ruling removes: a sentinel set
        # includes the newest file, and on a tracked store that is whatever the last commit touched.
        self.assertNotEqual(o['to'], 'sentinel_files')

    # -- the guard discriminates (the negative control) -----------------------------------------
    def test_the_same_request_on_an_untracked_path_is_GRANTED(self):
        idx = self.BSM._probe_git_index('live', REPO, True)
        self.assertEqual(idx.status, 'ok')
        untracked = idx.tracked('fec/audit/__no_such_path_is_in_git__.tmp')
        self.assertIs(untracked, False, 'the index claims to track a path that does not exist')

        overrides = []
        store = self._store('fix.adjudicated_data', untracked, basis='root live: ok')
        policy = self.BSM.resolve_policy(store, overrides)

        self.assertEqual(policy, 'hash_always',
                         'the scope guard refused a store git does NOT track — it is not a scope '
                         'rule, it is an off switch; resolved to %r' % policy)
        self.assertEqual(overrides, [],
                         'an untracked store recorded a refusal: %r' % (overrides,))

    def test_a_root_outside_any_work_tree_protects_nothing_and_says_so(self):
        idx = self.BSM._probe_git_index('fix', self.tmp, True)
        self.assertEqual(idx.status, 'not_a_work_tree',
                         'a temp dir outside any repo probed as %s :: %s' % (idx.status, idx.detail))
        self.assertTrue(idx.answers, 'not_a_work_tree is an ANSWER (git protects nothing here), '
                                     'not an absence of one')
        self.assertIs(idx.tracked('anything/at/all'), False)
        self.assertIs(idx.tracked('.'), False)

    # -- the guard degrades gracefully ----------------------------------------------------------
    def test_when_git_cannot_be_asked_tracking_is_unknown_and_the_guard_does_not_fire(self):
        """UNKNOWN is not UNTRACKED and is not TRACKED. Nothing crashes, nothing is silently
        stripped, and the store is named in the manifest as honoured on an unverified probe."""
        old = os.environ.get('SYSMAN_GIT_PROBE')
        os.environ['SYSMAN_GIT_PROBE'] = '0'
        try:
            blind = load_producer('build_system_manifest_probe_disabled')
        finally:
            if old is None:
                os.environ.pop('SYSMAN_GIT_PROBE', None)
            else:
                os.environ['SYSMAN_GIT_PROBE'] = old
        self.assertFalse(blind.GIT_PROBE, 'SYSMAN_GIT_PROBE=0 did not disable the probe')

        idx = blind._probe_git_index('live', REPO, True)
        self.assertEqual(idx.status, 'probe_disabled')
        self.assertFalse(idx.answers)
        self.assertIsNone(idx.tracked('fec/audit'),
                          'a disabled probe reported a tracked-ness it could not have measured')

        overrides = []
        store = self._store('fix.unknown', idx.tracked('fec/audit'), basis='root live: probe_disabled')
        policy = blind.resolve_policy(store, overrides)
        self.assertEqual(policy, 'hash_always',
                         'an unverified guard stripped the hash set anyway; resolved to %r' % policy)
        self.assertEqual(overrides, [])

    def test_the_documented_strict_alternative_actually_refuses_unknown(self):
        """GIT_UNKNOWN_HONOURS_HASH_ALWAYS is documented as a one-flag inversion. A documented
        branch nobody has watched run is a comment, not a behaviour."""
        overrides = []
        store = self._store('fix.unknown', None, basis='root live: git_unavailable')
        old = self.BSM.GIT_UNKNOWN_HONOURS_HASH_ALWAYS
        self.BSM.GIT_UNKNOWN_HONOURS_HASH_ALWAYS = False
        try:
            policy = self.BSM.resolve_policy(store, overrides)
        finally:
            self.BSM.GIT_UNKNOWN_HONOURS_HASH_ALWAYS = old
        self.assertEqual(policy, 'hash_never')
        self.assertEqual(overrides[0]['refused_on'], 'git-unknown')
        self.assertIn('UNKNOWN', overrides[0]['reasons'][0])

    # -- the envelope still runs FIRST ----------------------------------------------------------
    def test_a_store_refused_on_scale_keeps_reporting_scale_not_git(self):
        """repo.audits declares hash_always on merit, is far outside the envelope, AND is partly
        git-tracked. The envelope is checked first on purpose so that row keeps reporting the
        capacity gap it was declared to expose — and keeps the sentinel evidence its 258k
        untracked files would otherwise lose."""
        overrides = []
        store = self._store('fix.huge_and_tracked', True, basis='root live: ok')
        store['measured']['bytes'] = 32 * 1024 * 1024 * 1024
        store['measured']['file_count'] = 300000
        policy = self.BSM.resolve_policy(store, overrides)
        self.assertEqual(policy, 'sentinel_files')
        self.assertEqual(overrides[0]['refused_on'], 'scale')

    # -- and none of this poisoned the rest of the battery --------------------------------------
    def test_loading_the_producer_here_did_not_register_it_under_its_real_name(self):
        self.assertNotIn('build_system_manifest', sys.modules,
                         'C7 registered the producer under its real name; every checker control '
                         'in this file would now fail the no_regeneration guard for the wrong '
                         'reason')


class TestC7bCheckerRefusesToHonourATrackedHashAlways(ControlCase):
    """The producer refuses to EMIT it; this refuses to HONOUR it. Same shape as the envelope
    re-check: manifest-internal, so a row hand-edited back to hash_always while still carrying the
    producer's own measurement of its tracked-ness is caught without a subprocess."""

    def test_a_manifest_claiming_hash_always_for_a_store_it_records_as_tracked_fails_A4(self):
        self.fx.write_file('adjudicated/a.ndjson', 'alpha\n')
        store = self.fx.add_dir_store('fix.adjudicated', 'adjudicated', cls='SOURCE',
                                      policy='hash_always', irrep=4)
        mp = self.fx.write_manifest()
        self.assert_clean_baseline(mp)

        store['git_tracked'] = True                                  # <-- the planted defect
        store['git_tracking_basis'] = 'root fix: ok, 1 tracked path'
        mp = self.fx.write_manifest()

        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A4 fix.adjudicated hash_always is outside git protection'),
                        'a hash_always row the manifest itself records as git-tracked was '
                        'honoured; failures were %s' % self.rec.failed_names())
        detail = self.rec.fail_detail('A4 fix.adjudicated hash_always is outside git protection')
        self.assertIn('git_tracked=true', detail)
        self.assertIn('only what git does not protect', detail)
        # And the checker must STOP there rather than going on to hash a store it just refused.
        self.assertFalse(self.rec.has_pass('A4 fix.adjudicated every hashed file byte-identical'))
        self.assertFalse(self.rec.has_fail('A4 fix.adjudicated every hashed file byte-identical'))


# =============================================================================================
# Anti-vacuity guards — the checks that protect the checks
# =============================================================================================
class TestGuardNoRegeneration(ControlCase):
    def test_producer_loaded_in_process_fails_the_no_regeneration_guard(self):
        self.fx.write_file('data/keep.json', '{"a":1}')
        self.fx.add_file_store('fix.keep', 'data/keep.json')
        mp = self.fx.write_manifest()
        self.assert_clean_baseline(mp)

        sentinel = object()
        sys.modules['build_system_manifest'] = sentinel                 # <-- the planted defect
        try:
            self.run_check(mp)
        finally:
            if sys.modules.get('build_system_manifest') is sentinel:
                del sys.modules['build_system_manifest']
        self.assertTrue(self.rec.has_fail('A0 system manifest was not regenerated in this process'),
                        'the checker did not notice the producer loaded alongside it; failures '
                        'were %s' % self.rec.failed_names())
        self.assertIn('vacuous',
                      self.rec.fail_detail('A0 system manifest was not regenerated in this process'))


class TestGuardManifestFromTheFuture(ControlCase):
    def test_a_manifest_not_predating_the_run_fails(self):
        self.fx.write_file('data/keep.json', '{"a":1}')
        self.fx.add_file_store('fix.keep', 'data/keep.json')
        mp = self.fx.write_manifest(generated_epoch=time.time() + 3600,
                                    generated_utc='the future')
        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A0 system manifest predates this audit run'),
                        'a future-dated manifest passed the age guard; failures were %s'
                        % self.rec.failed_names())
        self.assertIn('PRIOR producer run',
                      self.rec.fail_detail('A0 system manifest predates this audit run'))


class TestGuardBadGeneratorCommit(ControlCase):
    def test_an_unresolvable_generator_commit_fails(self):
        self.fx.write_file('data/keep.json', '{"a":1}')
        self.fx.add_file_store('fix.keep', 'data/keep.json')
        mp = self.fx.write_manifest(generator_commit='0' * 40)
        self.run_check(mp)
        names = self.rec.failed_names()
        self.assertTrue(
            self.rec.has_fail('A0 system manifest generator_commit resolves in git') or not HEAD,
            'an all-zero generator_commit resolved, or the guard did not run; failures were %s'
            % names)


class TestGuardEveryRootMissing(ControlCase):
    def test_a_manifest_whose_every_root_is_absent_fails(self):
        self.fx.write_file('data/keep.json', '{"a":1}')
        self.fx.add_file_store('fix.keep', 'data/keep.json')
        gone = os.path.join(self.fx.root, 'no_such_root_anywhere')
        mp = self.fx.write_manifest(
            roots=[{'id': 'fix', 'path': gone, 'label': 'fixture root', 'present': False}])
        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A0 at least one declared root is present on this host'),
                        'a manifest describing a different machine passed; failures were %s'
                        % self.rec.failed_names())
        self.assertIn('different machine',
                      self.rec.fail_detail('A0 at least one declared root is present on this host'))


class TestGuardOneRootMissingSkipsRatherThanFails(ControlCase):
    def test_an_absent_offrepo_root_skips_its_stores_with_a_reason(self):
        self.fx.write_file('data/keep.json', '{"a":1}')
        self.fx.add_file_store('fix.keep', 'data/keep.json')
        ghost = dict(self.fx.stores[0])
        ghost['id'] = 'ghost.store'
        ghost['root_id'] = 'offrepo'
        ghost['path'] = os.path.join(self.fx.root, 'nowhere', 'ghost.json')
        self.fx.stores.append(ghost)
        mp = self.fx.write_manifest(roots=[
            {'id': 'fix', 'path': self.fx.root, 'label': 'fixture root', 'present': True},
            {'id': 'offrepo', 'path': os.path.join(self.fx.root, 'nowhere'),
             'label': 'absent off-repo root', 'present': False}])
        tally = self.run_check(mp)
        self.assertEqual(self.rec.failed_names(), [],
                         'an absent off-repo root must SKIP, not FAIL; failures were %s'
                         % self.rec.failed_names())
        reasons = [why for name, why in tally['skips'] if 'ghost.store' in name]
        self.assertTrue(reasons, 'the store under the absent root was neither checked nor skipped')
        self.assertIn('root offrepo is absent', reasons[0])


class TestGuardHandEditedHashEnvelope(ControlCase):
    def test_hash_always_claimed_outside_the_declared_envelope_fails(self):
        self.fx.write_file('regenerable/x.json', '{"x":1}')
        store = self.fx.add_dir_store('fix.regenerable', 'regenerable',
                                      policy='hash_always', irrep=0)
        mp = self.fx.write_manifest()
        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A4 fix.regenerable hash_always is inside the declared '
                                          'envelope'),
                        'a hash_always row below the irreplaceability floor was honoured; failures '
                        'were %s' % self.rec.failed_names())
        self.assertIn('irreplaceability 0 < envelope min 2',
                      self.rec.fail_detail('A4 fix.regenerable hash_always is inside the declared '
                                           'envelope'))
        self.assertFalse(store['id'] is None)


class TestGuardSecretNeverHashed(ControlCase):
    def test_a_secret_store_claiming_hash_always_fails_and_is_not_read(self):
        self.fx.write_file('secrets/api_key.txt', 'sk-not-a-real-key')
        store = self.fx.add_file_store('fix.secret', 'secrets/api_key.txt', cls='SECRET',
                                       policy='hash_always', irrep=4)
        store['hashes'] = {'api_key.txt': 'deadbeef' * 8}
        mp = self.fx.write_manifest()
        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A4 fix.secret is not a SECRET claiming hash_always'),
                        'a SECRET asking to be hashed was not refused; failures were %s'
                        % self.rec.failed_names())
        detail = self.rec.fail_detail('A4 fix.secret is not a SECRET claiming hash_always')
        self.assertIn('Refusing to read it', detail)
        # And no hash verdict may have been emitted for it at all.
        self.assertFalse(self.rec.has_fail('A4 fix.secret every hashed file byte-identical'))
        self.assertFalse(self.rec.has_pass('A4 fix.secret every hashed file byte-identical'))


class TestRollingEventRetention(ControlCase):
    def event_store(self):
        producer = load_producer()
        registry = next(row for row in producer.REGISTRY if row['id'] == 'glassbox.events')
        self.assertEqual(producer.resolve_policy(registry, []), 'hash_never')
        self.assertTrue(registry['counts_toward_totals'])
        for index in range(100):
            self.fx.write_file('events/event-%03d.json' % index, '{}')
        store = self.fx.add_dir_store('fix.events', 'events', cls='OPS', policy='hash_never', irrep=4)
        store['cadence'] = registry['cadence']
        return store

    def test_registry_retention_policy_is_valid_and_still_counted(self):
        self.event_store()
        self.assert_clean_baseline(self.fx.write_manifest())

    def test_event_rotation_passes_but_bulk_loss_fails(self):
        store = self.event_store()
        manifest = self.fx.write_manifest()
        self.assert_clean_baseline(manifest)
        for index in range(100):
            os.rename(os.path.join(store['path'], 'event-%03d.json' % index),
                      os.path.join(store['path'], 'next-%03d.json' % index))
        self.run_check(manifest)
        self.assertEqual(self.rec.failed_names(), [])
        for index in range(90):
            os.remove(os.path.join(store['path'], 'next-%03d.json' % index))
        self.run_check(manifest)
        self.assertTrue(self.rec.has_fail('A2 fix.events file_count within tolerance'))

    def test_retained_event_store_cannot_disappear(self):
        store = self.event_store()
        self.fx.write_file('keep.json', '{}')
        self.fx.add_file_store('fix.keep', 'keep.json')
        manifest = self.fx.write_manifest()
        self.assert_clean_baseline(manifest)
        shutil.rmtree(store['path'])
        self.run_check(manifest)
        self.assertTrue(self.rec.has_fail('A1 fix.events still exists'))


class _ContinuousStoreCase(ControlCase):
    """Shared fixture: a sentinel_files store whose registry cadence is 'continuous', so its
    sentinels are recorded for EXISTENCE only. That relaxation is a skip path, and a skip path
    nobody has watched fail is exactly the shape of the two dead checks this repo has shipped —
    so both halves of it get a control."""

    def build(self, cadence):
        self.fx.write_file('churn/state.bin', b'\x01' * 64)
        self.fx.write_file('churn/cursor', b'0')
        store = self.fx.add_dir_store('fix.churn', 'churn', cls='OPS',
                                      policy='sentinel_files', irrep=2)
        store['cadence'] = cadence
        stable = cadence != 'continuous'
        store['sentinels'] = []
        for rel in ('state.bin', 'cursor'):
            full = os.path.join(store['path'], rel)
            st = os.stat(full)
            store['sentinels'].append(
                {'rel': rel, 'bytes': st.st_size, 'mtime_epoch': st.st_mtime,
                 'sha256': _sha(full) if stable else None,
                 'content_stable': stable,
                 'sha256_skipped_reason': None if stable else 'cadence is continuous'})
        return store


class TestContinuousSentinelStillFailsOnDeletion(_ContinuousStoreCase):
    def test_deleting_a_content_unstable_sentinel_still_fails(self):
        store = self.build('continuous')
        mp = self.fx.write_manifest()
        self.assert_clean_baseline(mp)

        os.remove(os.path.join(store['path'], 'cursor'))          # <-- the planted defect

        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A2b fix.churn sentinels unchanged'),
                        'existence-only sentinels stopped detecting DELETION — the relaxation '
                        'became a hole; failures were %s' % self.rec.failed_names())
        self.assertIn('not a file at', self.rec.fail_detail('A2b fix.churn sentinels unchanged'))


class TestContinuousSentinelToleratesRewrite(_ContinuousStoreCase):
    def test_rewriting_a_content_unstable_sentinel_does_not_fail(self):
        store = self.build('continuous')
        mp = self.fx.write_manifest()
        self.assert_clean_baseline(mp)

        # A cursor advancing is this store's ordinary operation, not corruption.
        with open(os.path.join(store['path'], 'cursor'), 'wb') as fh:
            fh.write(b'99999')

        self.run_check(mp)
        self.assertEqual(self.rec.failed_names(), [],
                         'an in-place rewrite of a store the registry calls "continuous" was '
                         'treated as corruption; failures were %s' % self.rec.failed_names())

    def test_the_same_rewrite_DOES_fail_when_the_store_is_not_continuous(self):
        """The negative control for the relaxation: identical mutation, non-continuous cadence."""
        store = self.build('rare')
        mp = self.fx.write_manifest()
        self.assert_clean_baseline(mp)

        with open(os.path.join(store['path'], 'cursor'), 'wb') as fh:
            fh.write(b'99999')

        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A2b fix.churn sentinels unchanged'),
                        'a stable-cadence store tolerated a sentinel rewrite — the cadence gate is '
                        'not actually gating anything; failures were %s' % self.rec.failed_names())
        self.assertIn('cursor', self.rec.fail_detail('A2b fix.churn sentinels unchanged'))


# =============================================================================================
# C8 — GIT BOOKKEEPING IS NOT SENTINEL MATERIAL (root-cause fix, 2026-08-27)
#
# The Phase-0 gate went red on its OWN landing commit.  The producer's sentinel picker took the
# NEWEST file in each git store — `.git/index` for repo.git, `refs/heads/main` for mirror.melange —
# and recorded both as content_stable:true.  The commit rewrote the index (8,371,954 -> 8,372,431
# bytes) and the push moved the ref, so the manifest's recorded hash was the sha256 of the PRE-PUSH
# ref: the audit's "disagreement" WAS the push.  mirror.melange's file_count went 86 -> 100 for the
# same reason.  That is the always-red twin of the never-fails check this battery exists for.
#
# The fix has two halves and BOTH need a control, because both are relaxations and an unwatched
# relaxation is how a check quietly dies:
#   C8   git bookkeeping is excluded from sentinel SELECTION (producer)
#   C8b  the exclusion is NOT a blanket off switch — a non-git path named `index` is still a
#        sentinel and still fails when its bytes change (producer + checker)
#   C8c  a git store's file_count is no longer asserted, but an EMPTIED object store still fails,
#        and the same drift on a non-git store still fails (checker)
#   C8d  the checker refuses to HONOUR a bookkeeping sentinel a manifest hands it (checker)
#   C8e  the two copies of the predicate (the checker may not import the producer) agree
# =============================================================================================
class _GitShapeCase(unittest.TestCase):
    """Builds a directory with the real SHAPE of a git dir — HEAD + objects/ + refs/ — so the
    producer's `is_git_dir` recognises it by MEASUREMENT.  Nothing here declares a flag the code
    under test then reads back; a control that asserts a boolean it set itself proves nothing."""

    @classmethod
    def setUpClass(cls):
        cls.BSM = load_producer('build_system_manifest_c8')

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='sysman_c8_')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, base, rel, nbytes, mtime):
        full = os.path.join(base, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'wb') as fh:
            fh.write(bytes((i % 251) for i in range(nbytes)) if nbytes else b'')
        os.utime(full, (mtime, mtime))
        return full

    def build_git_dir(self, base):
        """A git directory carrying one of every shape that matters.  `index` is deliberately the
        SECOND LARGEST file AND the newest, so under the old picker it was selected twice over."""
        old = time.time() - 3600
        for rel, n in (('HEAD', 21), ('config', 200), ('description', 73),
                       ('hooks/pre-rebase.sample', 4898),
                       ('objects/pack/pack-aaaa.pack', 8192),
                       ('objects/pack/pack-aaaa.idx', 2048),
                       ('objects/ab/cccccccccccccccccccccccccccccccccccccc', 1024),
                       ('objects/info/commit-graph', 6000),
                       ('refs/heads/main', 41), ('packed-refs', 300), ('logs/HEAD', 500)):
            self._write(base, rel, n, old)
        self._write(base, 'index', 7000, time.time() - 10)          # newest by construction
        return base

    def sentinels_for(self, path, git_dir_store, cadence='per push'):
        store = {'id': 'fix.gitlike', 'class': 'SNAPSHOT', 'path': path, 'excludes': [],
                 'cadence': cadence, 'cap': None, 'git_dir_store': git_dir_store}
        measured, _listing, cands = self.BSM.measure(store, {'used': 0, 'total': 10 ** 6})
        store['measured'] = measured
        return store, self.BSM.pick_sentinels(store, cands)


class TestC8GitBookkeepingIsNeverASentinel(_GitShapeCase):

    def test_the_producer_recognises_a_git_directory_by_measurement(self):
        base = self.build_git_dir(os.path.join(self.tmp, 'repo.git'))
        self.assertTrue(self.BSM.is_git_dir(base),
                        'the producer did not recognise a HEAD + objects/ + refs/ tree as a git '
                        'directory, so the whole exclusion below would never engage')
        self.assertFalse(self.BSM.is_git_dir(self.tmp),
                         'a plain directory was classed as a git directory — the detector is a '
                         'rubber stamp')

    def test_no_git_bookkeeping_path_survives_sentinel_selection(self):
        base = self.build_git_dir(os.path.join(self.tmp, 'repo.git'))
        _store, sent = self.sentinels_for(base, True)
        rels = [r['rel'] for r in sent]
        self.assertTrue(rels, 'a git store yielded NO sentinels at all — the exclusion removed the '
                              'evidence instead of narrowing it')
        for banned in ('index', 'HEAD', 'refs/heads/main', 'packed-refs', 'logs/HEAD',
                       'objects/info/commit-graph'):
            self.assertNotIn(banned, rels,
                             'the picker chose git bookkeeping %r as a sentinel; the next commit '
                             'falsifies it. chosen=%r' % (banned, rels))
        # ...and it kept the content-addressed evidence rather than emptying the set. Pack files
        # are NOT that evidence any more (2026-10-11): repack renames them; loose objects stay.
        self.assertIn('objects/ab/cccccccccccccccccccccccccccccccccccccc', rels,
                      'loose objects are content-addressed and must stay eligible; chosen=%r' % rels)
        self.assertNotIn('objects/pack/pack-aaaa.pack', rels,
                         'a pack file was pinned; the next repack renames it. chosen=%r' % rels)
        # Every surviving row must carry the DERIVED stability flag, not a bare true.
        for row in sent:
            self.assertTrue(row['content_stable'],
                            'a non-volatile sentinel on a stable-cadence store was marked unstable: '
                            '%r' % row)
            self.assertIsNotNone(row['sha256'],
                                 'a stable sentinel under the hash cap recorded no sha256: %r' % row)

    def test_the_newest_file_is_not_a_sentinel_inside_a_git_directory(self):
        """`index` is the newest file here. Even with bookkeeping excluded, the newest file in a
        git dir is by construction whatever the last commit wrote, so the by-time slot is dropped —
        and the by-size slots must not silently pick up a fourth row to compensate."""
        base = self.build_git_dir(os.path.join(self.tmp, 'repo.git'))
        _store, sent = self.sentinels_for(base, True)
        self.assertLessEqual(len(sent), self.BSM.SENTINEL_COUNT,
                             'a git store recorded a newest-file sentinel after all: %r'
                             % [r['rel'] for r in sent])

    def test_the_SAME_candidates_DO_yield_index_when_the_store_is_not_a_git_directory(self):
        """THE DISCRIMINATOR.  Identical tree, identical file sizes and mtimes; only the measured
        git-ness differs.  If `index` disappears here too, the exclusion is not a git rule — it is
        a filename ban, and it would be silently deleting evidence from ordinary data stores."""
        base = self.build_git_dir(os.path.join(self.tmp, 'notgit'))
        _store, sent = self.sentinels_for(base, False)
        rels = [r['rel'] for r in sent]
        self.assertIn('index', rels,
                      'a file called `index` outside a git directory was excluded from sentinel '
                      'duty; the predicate is a blanket off switch. chosen=%r' % rels)
        self.assertTrue(any(r['rel'] == 'index' and r['content_stable'] and r['sha256']
                            for r in sent),
                        'the non-git `index` was selected but recorded without stable bytes or a '
                        'hash, which is the evidence half of the check: %r' % sent)


class TestC8ePredicateParityProducerVsChecker(_GitShapeCase):
    """The fix duplicates one predicate across two modules ON PURPOSE — system_manifest_check.py
    must never import the producer (a manifest compared against the thing that made it proves
    nothing), so it re-derives `_git_bookkeeping` the way it already re-derives `_size_class` and
    `_excluded`.  Duplication that nothing checks is duplication that drifts, and the drift would be
    silent in both directions: a producer that stopped excluding a path would have its manifest
    quietly honoured, and a checker that started excluding one would refuse a legitimate sentinel.
    So the two implementations are held against each other here, over every shape that matters."""

    #: (relpath, store_is_git_dir, expected)
    TABLE = (
        ('index', True, True), ('HEAD', True, True), ('ORIG_HEAD', True, True),
        ('FETCH_HEAD', True, True), ('packed-refs', True, True), ('COMMIT_EDITMSG', True, True),
        ('refs/heads/main', True, True), ('refs/remotes/mirror/main', True, True),
        ('logs/HEAD', True, True), ('logs/refs/heads/main', True, True),
        ('worktrees/lane/index', True, True), ('modules/sub/HEAD', True, True),
        ('rebase-merge/head-name', True, True), ('index.lock', True, True),
        ('objects/info/commit-graph', True, True), ('info/refs', True, True),
        ('objects/pack/multi-pack-index', True, True),
        # content-addressed: NOT bookkeeping, and the only real evidence a git store has left
        ('objects/pack/pack-aaaa.pack', True, False), ('objects/pack/pack-aaaa.idx', True, False),
        ('objects/ab/cdef01', True, False), ('lost-found/other/abc', True, False),
        ('config', True, False), ('description', True, False),
        ('hooks/pre-rebase.sample', True, False),
        # the same names OUTSIDE a git directory are ordinary data and stay fully checked
        ('index', False, False), ('HEAD', False, False), ('refs/heads/main', False, False),
        ('packed-refs', False, False), ('logs/HEAD', False, False), ('data/index', False, False),
        # a .git nested inside a non-git store (repo.worktrees is exactly this shape)
        ('wt/lane/.git', False, True), ('wt/lane/.git/index', False, True),
        ('wt/lane/.git/refs/heads/main', False, True),
        ('wt/lane/.git/objects/ab/cdef01', False, False),
        ('wt/lane/data/index', False, False),
    )

    def test_the_two_implementations_agree_on_every_shape(self):
        drift = []
        for rel, git_dir, expected in self.TABLE:
            produced = self.BSM.is_git_bookkeeping(rel, git_dir)
            checked = SMC._git_bookkeeping(rel, git_dir)
            if produced != expected or checked != expected:
                drift.append((rel, git_dir, expected, produced, checked))
        self.assertEqual(drift, [],
                         'producer and checker disagree, or disagree with the intended rule '
                         '(rel, store_is_git_dir, expected, producer, checker): %r' % (drift,))
        # A table that asserted only False everywhere would pass while both sides were dead.
        self.assertTrue(any(e for _r, _g, e in self.TABLE))
        self.assertTrue(any(not e for _r, _g, e in self.TABLE))

    def test_the_two_git_directory_detectors_agree(self):
        base = self.build_git_dir(os.path.join(self.tmp, 'repo.git'))
        plain = os.path.join(self.tmp, 'plain')
        os.makedirs(plain, exist_ok=True)
        for path, expected in ((base, True), (plain, False), (os.path.join(base, 'objects'), False)):
            self.assertIs(self.BSM.is_git_dir(path), expected, path)
            self.assertIs(SMC._is_git_dir(path), expected, path)


class TestC8bNonGitVolatileLookingPathsStayFullyChecked(_GitShapeCase):
    """The negative control for the whole exclusion, in both halves: SELECTED by the producer, and
    still FAILING in the checker when its bytes change."""

    def test_a_non_git_directory_of_git_names_is_selected_and_hashed(self):
        base = os.path.join(self.tmp, 'plain')
        old = time.time() - 3600
        for rel, n in (('index', 7000), ('HEAD', 5000), ('refs/heads/main', 4000)):
            self._write(base, rel, n, old)
        # Distinct newest file, so the by-size and by-time slots do not collide. (The producer
        # feeds the newest file into the candidate list a second time; when it is also one of the
        # largest, that duplicate costs a by-size slot. Pre-existing, harmless — fewer sentinels,
        # never wrong ones — and deliberately not touched by this fix.)
        self._write(base, 'packed-refs', 3000, time.time() - 10)
        self.assertFalse(self.BSM.is_git_dir(base),
                         'a directory of git-shaped NAMES with no objects/ was treated as a git dir')
        _store, sent = self.sentinels_for(base, False, cadence='rare')
        rels = [r['rel'] for r in sent]
        for expected in ('index', 'HEAD', 'refs/heads/main', 'packed-refs'):
            self.assertIn(expected, rels,
                          'a file named %r outside any git directory was denied sentinel duty; the '
                          'exclusion is a filename ban, not a git rule. chosen=%r' % (expected, rels))
        for row in sent:
            self.assertTrue(row['content_stable'])
            self.assertIsNotNone(row['sha256'],
                                 'an ordinary data file was recorded without a hash: %r' % row)


class TestC8bCheckerStillFailsOnANonGitVolatileLookingSentinel(ControlCase):

    def test_flipping_a_byte_in_a_non_git_file_named_index_still_fails_A2b(self):
        self.fx.write_file('plain/index', b'\x01' * 512)
        self.fx.write_file('plain/notes.txt', b'hello')
        store = self.fx.add_dir_store('fix.plain', 'plain', cls='SOURCE',
                                      policy='sentinel_files', irrep=2)
        store['cadence'] = 'rare'
        store['sentinels'] = []
        for rel in ('index', 'notes.txt'):
            full = os.path.join(store['path'], rel)
            st = os.stat(full)
            store['sentinels'].append({'rel': rel, 'bytes': st.st_size,
                                       'mtime_epoch': st.st_mtime, 'sha256': _sha(full),
                                       'content_stable': True, 'sha256_skipped_reason': None})
        mp = self.fx.write_manifest()
        self.assert_clean_baseline(mp)

        with open(os.path.join(store['path'], 'index'), 'wb') as fh:   # <-- the planted defect
            fh.write(b'\x02' * 512)

        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A2b fix.plain sentinels unchanged'),
                        'a byte flip in a non-git file named `index` went undetected — the git '
                        'exclusion leaked into ordinary stores; failures were %s'
                        % self.rec.failed_names())
        detail = self.rec.fail_detail('A2b fix.plain sentinels unchanged')
        self.assertIn('index sha256', detail)
        self.assertNotIn('GIT BOOKKEEPING', detail)


class _GitStoreCheckerCase(ControlCase):
    """A store the checker will observe as a real git directory: HEAD + objects/ + refs/.  Its
    largest file is a hook sample, NOT an object, so the object-store control below can empty
    objects/ without also disturbing the sentinel or the size class."""

    def build(self, rel='gitstore', git_dir_store=True, shape=True):
        if shape:
            self.fx.write_file(rel + '/HEAD', b'ref: refs/heads/main\n')
            self.fx.write_file(rel + '/refs/heads/main', b'0' * 41)
            self.fx.write_file(rel + '/objects/ab/cdef01', b'\x78' * 120)
            self.fx.write_file(rel + '/objects/cd/ef0123', b'\x78' * 130)
        self.fx.write_file(rel + '/hooks/big.sample', b'\x11' * 300000)
        store = self.fx.add_dir_store('fix.%s' % rel, rel, cls='SNAPSHOT',
                                      policy='sentinel_files', irrep=3)
        store['cadence'] = 'per push'
        store['git_dir_store'] = git_dir_store
        full = os.path.join(store['path'], 'hooks', 'big.sample')
        st = os.stat(full)
        store['sentinels'] = [{'rel': 'hooks/big.sample', 'bytes': st.st_size,
                               'mtime_epoch': st.st_mtime, 'sha256': _sha(full),
                               'content_stable': True, 'sha256_skipped_reason': None}]
        return store

    def push(self, store, n=14):
        """Simulate an ordinary push: n new loose objects appear.  This is the exact drift that
        turned the live gate red (mirror.melange 86 -> 100 files, +14 objects)."""
        for i in range(n):
            with open(os.path.join(store['path'], 'objects', 'ab', 'new%02d' % i), 'wb') as fh:
                fh.write(b'\x99' * 64)


class TestC8cGitStoreCountRelaxation(_GitStoreCheckerCase):

    def test_an_ordinary_push_no_longer_turns_the_gate_red(self):
        store = self.build()
        mp = self.fx.write_manifest()
        self.assert_clean_baseline(mp)

        self.push(store)                       # ordinary operation, not a defect

        self.run_check(mp)
        self.assertEqual(self.rec.failed_names(), [],
                         'a normal push still fails the manifest gate — the always-red defect is '
                         'not fixed; failures were %s' % self.rec.failed_names())
        self.assertTrue(self.rec.has_pass('A2 fix.gitstore still holds a populated git object store'),
                        'the git store was never actually routed onto the git path; passes did not '
                        'include the object-store verdict')

    def test_an_emptied_object_store_STILL_fails(self):
        """The relaxation is not an off switch: what a mirror can honestly promise is that its
        objects are still there, and that promise is falsifiable."""
        store = self.build()
        mp = self.fx.write_manifest()
        self.assert_clean_baseline(mp)

        shutil.rmtree(os.path.join(store['path'], 'objects', 'ab'))    # <-- the planted defect
        shutil.rmtree(os.path.join(store['path'], 'objects', 'cd'))

        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A2 fix.gitstore still holds a populated git object store'),
                        'a git store whose object database was emptied passed the gate; failures '
                        'were %s' % self.rec.failed_names())
        detail = self.rec.fail_detail('A2 fix.gitstore still holds a populated git object store')
        self.assertIn('emptied or clobbered', detail)
        # The sentinel and the size class were deliberately left intact, so this is the ONLY
        # verdict that may have moved — otherwise the control would prove nothing specific.
        self.assertEqual(self.rec.failed_names(),
                         ['A2 fix.gitstore still holds a populated git object store'])

    def test_the_SAME_drift_on_a_NON_git_store_still_fails_file_count(self):
        """THE DISCRIMINATOR for the count relaxation.  Identical +14-file drift, identical class
        and tolerance; only the measured git-ness differs.  If this passes, the relaxation is not
        scoped to git stores at all and every data store just lost its count check."""
        store = self.build(rel='plainstore', git_dir_store=False, shape=False)
        mp = self.fx.write_manifest()
        self.assert_clean_baseline(mp)

        os.makedirs(os.path.join(store['path'], 'objects', 'ab'), exist_ok=True)
        self.push(store)

        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A2 fix.plainstore file_count within tolerance'),
                        'a non-git store absorbed a +14 file drift; the git relaxation leaked into '
                        'ordinary stores. failures were %s' % self.rec.failed_names())
        self.assertIn('drift +14', self.rec.fail_detail('A2 fix.plainstore file_count within tolerance'))

    def test_a_manifest_flag_alone_cannot_buy_the_relaxation(self):
        """Fail-closed: the checker re-observes git-ness. A hand-edited manifest that claims
        git_dir_store on an ordinary data store must NOT get the count check switched off."""
        store = self.build(rel='plainstore', git_dir_store=False, shape=False)
        mp = self.fx.write_manifest()
        self.assert_clean_baseline(mp)

        store['git_dir_store'] = True                                  # <-- the planted defect
        os.makedirs(os.path.join(store['path'], 'objects', 'ab'), exist_ok=True)
        self.push(store)
        mp = self.fx.write_manifest()
        os.utime(mp, (time.time() - 60, time.time() - 60))

        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A2 fix.plainstore file_count within tolerance'),
                        'a manifest bought the git relaxation by declaring it; the checker is '
                        'trusting the expectation instead of observing the disk. failures were %s'
                        % self.rec.failed_names())


class TestC8dCheckerRefusesToHonourABookkeepingSentinel(_GitStoreCheckerCase):
    """The producer refuses to SELECT it; this refuses to HONOUR it — the same shape as A4's
    envelope re-assertion.  A manifest built by an older producer, or hand-edited, must not have
    its bookkeeping expectation quietly accepted just because the bytes happen to match today."""

    def test_a_git_bookkeeping_sentinel_fails_even_when_its_bytes_still_match(self):
        store = self.build()
        full = os.path.join(store['path'], 'refs', 'heads', 'main')
        st = os.stat(full)
        store['sentinels'].append({'rel': 'refs/heads/main', 'bytes': st.st_size,
                                   'mtime_epoch': st.st_mtime, 'sha256': _sha(full),
                                   'content_stable': True, 'sha256_skipped_reason': None})
        mp = self.fx.write_manifest()

        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A2b fix.gitstore sentinels unchanged'),
                        'a ref recorded as a stable sentinel was honoured; a producer regression '
                        'would ship unnoticed. failures were %s' % self.rec.failed_names())
        detail = self.rec.fail_detail('A2b fix.gitstore sentinels unchanged')
        self.assertIn('GIT BOOKKEEPING', detail)
        self.assertIn('refs/heads/main', detail)

    def test_the_same_relpath_in_a_NON_git_store_is_honoured(self):
        """The discriminator again: `refs/heads/main` is only bookkeeping inside a git directory."""
        store = self.build(rel='plainstore', git_dir_store=False, shape=False)
        self.fx.write_file('plainstore/refs/heads/main', b'0' * 41)
        full = os.path.join(store['path'], 'refs', 'heads', 'main')
        st = os.stat(full)
        store['measured']['file_count'] += 1
        store['measured']['bytes'] += st.st_size
        store['sentinels'].append({'rel': 'refs/heads/main', 'bytes': st.st_size,
                                   'mtime_epoch': st.st_mtime, 'sha256': _sha(full),
                                   'content_stable': True, 'sha256_skipped_reason': None})
        mp = self.fx.write_manifest()

        self.run_check(mp)
        self.assertEqual(self.rec.failed_names(), [],
                         'an ordinary file at refs/heads/main was refused as git bookkeeping; the '
                         'refusal is not scoped to git directories. failures were %s'
                         % self.rec.failed_names())


# =============================================================================================
# C9 — CHAIN OUTPUTS ARE NOT SENTINEL MATERIAL (DE-011, 2026-09-06)
#
# Same shape as C8, and for the same reason: this is a RELAXATION, and an unwatched relaxation is
# how a check quietly dies.  The 2026-09-05 gate carried five always-red A2b rows whose sentinels
# were files `fec/run_pipeline.py`'s CHAIN rewrites minutes before the chain's own terminal audit
# read the pin — the always-red twin of a never-fails check.  The producer now takes those paths
# out of sentinel CANDIDACY, per store, by path.  Every half of that gets a control and a
# discriminator:
#   C9   a declared sentinel_excludes path is not SELECTED, the store keeps its stable rows, a
#        DIRECTORY prefix excludes its whole subtree, and a declaration matching nothing is
#        recorded as stale rather than passing silently
#   C9b  it is NOT a blanket off switch — the SAME relpaths, from the SAME candidate list, in a
#        store built by the REAL _s() defaults are still selected AND still hashed
#   C9c  sentinel_newest=False drops the by_time row and NOTHING else; the same store with it True
#        keeps it
#   C9d  an exclusion that would empty the candidate pool is REFUSED, not honoured — with a
#        negative control proving the floor is a floor and not a blanket ignore
#   C9e  by_hashable fires only when nothing else is hashable, never adds a row to a store that
#        already has one, and invents nothing when there is no under-cap candidate at all
# =============================================================================================
class _ChainStoreCase(unittest.TestCase):
    """Builds a store the way the producer's own build loop does: through the REAL `_s()`, then
    with `path` and the MEASURED `git_dir_store` attached (build_system_manifest.py:1672-1678).

    Going through `_s()` matters for C9b.  A hand-rolled dict could omit the new keys and "prove"
    the undeclared case by accident; a store from `_s()` carries `sentinel_excludes: []` and
    `sentinel_newest: True`, which is what an undeclared registry row ACTUALLY looks like."""

    @classmethod
    def setUpClass(cls):
        cls.BSM = load_producer('build_system_manifest_c9')

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='sysman_c9_')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, base, rel, nbytes, mtime):
        full = os.path.join(base, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'wb') as fh:
            fh.write(bytes((i % 251) for i in range(nbytes)) if nbytes else b'')
        os.utime(full, (mtime, mtime))
        return full

    def _write_over_cap(self, base, rel, nbytes, mtime):
        """A file genuinely larger than SENTINEL_HASH_MAX_BYTES, written by extension rather than
        by the byte-pattern loop above — 8 MiB through that loop is seconds per file, and the cap
        is the only property these rows are here for.  st_size is the real logical size either
        way, which is what the picker and the cap comparison read."""
        full = os.path.join(base, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'wb') as fh:
            fh.truncate(nbytes)
        os.utime(full, (mtime, mtime))
        return full

    def registry_store(self, path, **kw):
        store = self.BSM._s('fix.chain', 'live_tree', 'data/chain', 'DERIVED',
                            'the pipeline chain', ['engines'], 'Re-run the chain.',
                            'per chain run', 2, 'sentinel_files', **kw)
        store['path'] = path
        store['git_dir_store'] = self.BSM.is_git_dir(path)
        return store

    def measure(self, path):
        probe = {'id': 'probe', 'class': 'DERIVED', 'path': path, 'excludes': [], 'cap': None}
        measured, _listing, cands = self.BSM.measure(probe, {'used': 0, 'total': 10 ** 6})
        return measured, cands

    def pick(self, path, measured, cands, **kw):
        """One store, one selection.  `cands` is passed in so two calls can be given the BYTE-FOR
        BYTE identical candidate list and differ only in the registry declaration."""
        store = self.registry_store(path, **kw)
        store['measured'] = measured
        sent = self.BSM.pick_sentinels(store, list(cands))
        return store, sent

    def build_chain_tree(self, base):
        """A data store shaped like repo.data_engine: three real chain outputs at the top of the
        size order, three stable files under them, and a SEPARATE newest file so the by_size and
        by_time slots never collide.  The names are the actual paths the registry now declares —
        a filename ban hardcoded in the producer would look correct against this tree in C9 and is
        caught by C9b, which asks the same tree for the same files without the declaration."""
        old = time.time() - 3600
        for rel, n in (('staffer_network_section.json', 6000),
                       ('member_narrative_evidence_v4.json', 5000),
                       ('budget_frontier.json', 4000),
                       ('electoral_history.json', 3000),
                       ('affective_n.json', 2000),
                       ('congestion.json', 1000)):
            self._write(base, rel, n, old)
        self._write(base, 'lt_priming.json', 500, time.time() - 10)      # newest by construction
        return base

    CHAIN_OUTPUTS = ['staffer_network_section.json', 'member_narrative_evidence_v4.json',
                     'budget_frontier.json']
    SURVIVORS = ['electoral_history.json', 'affective_n.json', 'congestion.json']


class TestLegislationReceiptSentinel(_ChainStoreCase):
    def test_bank_receipt_rewrite_keeps_stable_sentinels_and_inventory(self):
        # Reproduce the live fallback: three archived votes exceed the hash cap;
        # the small, timestamped receipt would otherwise be the only hashable row.
        old = time.time() - 3600
        for i in range(3):
            self._write_over_cap(self.tmp, 'archive/votes_%d.csv' % i,
                                 self.BSM.SENTINEL_HASH_MAX_BYTES + i + 1, old)
        receipt = 'bank_anchor_reconciliation.json'
        full = self._write(self.tmp, receipt, 100, time.time() - 10)
        store = dict(next(s for s in self.BSM.REGISTRY if s['id'] == 'repo.data_legislation'))
        store.update(path=self.tmp, git_dir_store=False)
        measured, candidates = self.measure(self.tmp)
        store['measured'] = measured
        before = self.BSM.pick_sentinels(store, candidates)
        self.assertEqual({s['rel'] for s in before},
                         {'archive/votes_%d.csv' % i for i in range(3)})
        self.assertEqual(measured['file_count'], 4)
        control = dict(store, sentinel_excludes=[])
        self.assertIn(receipt, {s['rel'] for s in self.BSM.pick_sentinels(control, candidates)})
        with open(full, 'wb') as handle:
            handle.write(b'updated timestamp and reconciliation')
        after_measure, candidates = self.measure(self.tmp)
        store['measured'] = after_measure
        self.assertEqual(before, self.BSM.pick_sentinels(store, candidates))
        self.assertEqual(after_measure['file_count'], 4)
        self.assertNotEqual(measured['bytes'], after_measure['bytes'])


class TestC9ChainOutputsAreNotSentinels(_ChainStoreCase):

    def test_a_declared_chain_output_is_not_selected(self):
        base = self.build_chain_tree(os.path.join(self.tmp, 'data_engine'))
        measured, cands = self.measure(base)
        _store, sent = self.pick(base, measured, cands,
                                 sentinel_excl=self.CHAIN_OUTPUTS, sentinel_newest=False)
        rels = [r['rel'] for r in sent]
        self.assertTrue(rels, 'the store yielded NO sentinels at all — the exclusion removed the '
                              'evidence instead of narrowing it')
        for banned in self.CHAIN_OUTPUTS:
            self.assertNotIn(banned, rels,
                             'the picker chose declared chain output %r as a sentinel; the next '
                             'chain run falsifies it. chosen=%r' % (banned, rels))
        # ...and it kept the stable material that shares the directory, rather than emptying out.
        for kept in self.SURVIVORS:
            self.assertIn(kept, rels,
                          'a stable file in the same directory lost its sentinel slot; the '
                          'exclusion is behaving like a store-level off switch. chosen=%r' % rels)
        for row in sent:
            self.assertTrue(row['content_stable'],
                            'a surviving sentinel on a stable-cadence store was marked unstable: '
                            '%r' % row)
            self.assertIsNotNone(row['sha256'],
                                 'a surviving sentinel under the hash cap recorded no sha256, so '
                                 'the store traded a false red for no byte evidence: %r' % row)

    def test_the_excluded_files_are_still_walked_and_still_counted(self):
        """The whole justification for a sentinel-only exclusion: loss detection for these files
        rides A2 file_count, so they must keep contributing to the measurement they ride on. If
        the exclusion leaked into the walk, this store would silently shrink by three files and
        A2 would stop being able to see them disappear."""
        base = self.build_chain_tree(os.path.join(self.tmp, 'data_engine'))
        measured, cands = self.measure(base)
        store, _sent = self.pick(base, measured, cands,
                                 sentinel_excl=self.CHAIN_OUTPUTS, sentinel_newest=False)
        self.assertEqual(store['measured']['file_count'], 7,
                         'the sentinel exclusion changed the file COUNT; it is supposed to govern '
                         'sentinel duty only. measured=%r' % (store['measured'],))
        self.assertEqual(store['measured']['bytes'], 6000 + 5000 + 4000 + 3000 + 2000 + 1000 + 500,
                         'the excluded files stopped carrying their bytes: %r' % (store['measured'],))
        self.assertEqual(store['excludes'], [],
                         'sentinel_excl leaked into the WALK exclusion list `excludes`')

    def test_a_directory_prefix_excludes_its_whole_subtree(self):
        """repo.outputs declares `email_package`, a DIRECTORY, not a filename — and `_excluded`
        matches a slashless pattern by basename first. If only the basename arm worked, that
        declaration would silently exclude nothing and repo.outputs would stay red."""
        base = os.path.join(self.tmp, 'outputs')
        old = time.time() - 3600
        self._write(base, 'email_package/Moneyball_Email_Package.zip', 9000, old)
        self._write(base, 'email_package/EMAIL_PACKAGE_MANIFEST.json', 8000, old)
        self._write(base, 'lane_record_20260906/report.md', 7000, old)
        self._write(base, 'lane_record_20260906/data.json', 6000, old)
        self._write(base, 'stable_receipt.json', 5000, old)
        measured, cands = self.measure(base)
        _store, sent = self.pick(base, measured, cands,
                                 sentinel_excl=['email_package'], sentinel_newest=False)
        rels = [r['rel'] for r in sent]
        for banned in ('email_package/Moneyball_Email_Package.zip',
                       'email_package/EMAIL_PACKAGE_MANIFEST.json'):
            self.assertNotIn(banned, rels,
                             'a directory-prefix sentinel_excludes matched nothing under it; the '
                             'repo.outputs declaration is inert. chosen=%r' % rels)
        self.assertEqual(rels, ['lane_record_20260906/report.md',
                                'lane_record_20260906/data.json',
                                'stable_receipt.json'],
                         'the prefix exclusion took more (or less) than the email_package subtree: '
                         '%r' % rels)

    def test_a_declaration_that_matches_nothing_is_recorded_as_stale(self):
        """The named cost of a path list: a renamed chain output leaves its exclusion suppressing
        nothing forever. It cannot be a FAIL (a store legitimately may not have the file in its
        top-N candidates on a given run), so it is RECORDED — and a recorded fact nobody writes is
        a fact nobody can act on."""
        base = self.build_chain_tree(os.path.join(self.tmp, 'data_engine'))
        measured, cands = self.measure(base)
        store, _sent = self.pick(
            base, measured, cands,
            sentinel_excl=['budget_frontier.json', 'renamed_last_july.json'],
            sentinel_newest=False)
        self.assertEqual(store.get('sentinel_exclusions_unmatched'), ['renamed_last_july.json'],
                         'the stale half of the declaration was not recorded, or the live half was '
                         'wrongly reported stale: %r' % store.get('sentinel_exclusions_unmatched'))
        self.assertNotIn('sentinel_exclusions_refused', store,
                         'a declaration that left plenty of candidates was reported as refused')

    def test_the_new_keys_reach_the_emitted_store_row(self):
        """`_s()` is the only place these are declared and the build loop emits the store dict
        whole (build_system_manifest.py:1672 `dict(spec)` -> :1708 `stores.append`), so the
        manifest carries the declaration a reader would need to audit this relaxation."""
        store = self.registry_store(self.tmp, sentinel_excl=['a.json'], sentinel_newest=False)
        self.assertEqual(store['sentinel_excludes'], ['a.json'])
        self.assertIs(store['sentinel_newest'], False)
        default = self.registry_store(self.tmp)
        self.assertEqual(default['sentinel_excludes'], [],
                         'an undeclared row does not default to an EMPTY exclusion list')
        self.assertIs(default['sentinel_newest'], True,
                      'an undeclared row does not default to keeping its newest-file sentinel')


class TestC9bTheSamePathsAreStillSelectedWithoutTheDeclaration(_ChainStoreCase):
    """THE DISCRIMINATOR.  One tree, measured ONCE, and the SAME candidate list handed to two
    selections that differ in nothing but the registry declaration.  If these paths disappear here
    too, the filter is a filename ban and it is silently deleting evidence from every store that
    never declared anything — which is the failure this whole design chose a path list to avoid."""

    def test_the_SAME_candidates_ARE_selected_and_hashed_without_the_declaration(self):
        base = self.build_chain_tree(os.path.join(self.tmp, 'data_engine'))
        measured, cands = self.measure(base)

        declared_store, declared = self.pick(base, measured, cands,
                                             sentinel_excl=self.CHAIN_OUTPUTS,
                                             sentinel_newest=False)
        plain_store, plain = self.pick(base, measured, cands)      # real _s() defaults

        # The inputs really were identical: same store path, same measurement, same candidates.
        self.assertEqual(declared_store['path'], plain_store['path'])
        self.assertEqual(declared_store['measured'], plain_store['measured'])
        self.assertEqual(plain_store['sentinel_excludes'], [])
        self.assertIs(plain_store['sentinel_newest'], True)

        plain_rels = [r['rel'] for r in plain]
        for name in self.CHAIN_OUTPUTS:
            self.assertIn(name, plain_rels,
                          'a file named %r was denied sentinel duty in a store that declared NO '
                          'sentinel_excludes; the filter is a filename ban, not a per-store rule. '
                          'chosen=%r' % (name, plain_rels))
            row = next(r for r in plain if r['rel'] == name)
            self.assertTrue(row['content_stable'],
                            'the undeclared chain-output name was selected but marked unstable: %r'
                            % row)
            self.assertIsNotNone(row['sha256'],
                                 'the undeclared chain-output name was selected but recorded with '
                                 'no hash, which is the evidence half of the check: %r' % row)
        # And the declared store really did differ — otherwise both halves could be dead.
        declared_rels = [r['rel'] for r in declared]
        self.assertNotEqual(sorted(plain_rels), sorted(declared_rels))
        for name in self.CHAIN_OUTPUTS:
            self.assertNotIn(name, declared_rels)

    def test_a_store_predating_the_keys_entirely_behaves_like_an_undeclared_one(self):
        """Backward compatibility, and the reason `pick_sentinels` reads both keys through
        `.get`: a store dict from before DE-011 has NEITHER key. It must select exactly what an
        explicitly-undeclared store selects, not crash and not quietly drop its newest row."""
        base = self.build_chain_tree(os.path.join(self.tmp, 'data_engine'))
        measured, cands = self.measure(base)
        _plain_store, plain = self.pick(base, measured, cands)

        legacy = {'id': 'fix.legacy', 'class': 'DERIVED', 'path': base, 'excludes': [],
                  'cadence': 'per chain run', 'cap': None, 'git_dir_store': False,
                  'measured': measured}
        self.assertNotIn('sentinel_excludes', legacy)
        self.assertNotIn('sentinel_newest', legacy)
        legacy_sent = self.BSM.pick_sentinels(legacy, list(cands))
        self.assertEqual([r['rel'] for r in legacy_sent], [r['rel'] for r in plain],
                         'a pre-DE-011 store row selected differently from an undeclared one')


class TestC9cSentinelNewestDropsExactlyTheByTimeRow(_ChainStoreCase):

    def test_sentinel_newest_false_drops_the_by_time_row_and_true_keeps_it(self):
        base = self.build_chain_tree(os.path.join(self.tmp, 'data_engine'))
        measured, cands = self.measure(base)

        _off_store, off = self.pick(base, measured, cands, sentinel_newest=False)
        _on_store, on = self.pick(base, measured, cands, sentinel_newest=True)

        off_rels = [r['rel'] for r in off]
        on_rels = [r['rel'] for r in on]

        self.assertIn('lt_priming.json', on_rels,
                      'the newest file was not a sentinel even with sentinel_newest TRUE — the '
                      'by_time route is dead and this flag proves nothing. chosen=%r' % on_rels)
        self.assertNotIn('lt_priming.json', off_rels,
                         'sentinel_newest=False did not drop the newest-file sentinel. chosen=%r'
                         % off_rels)
        # It must drop THAT ROW AND NOTHING ELSE: the by_size half is untouched, and the picker
        # must not silently promote a fourth largest file to backfill the empty slot.
        self.assertEqual(off_rels, [r for r in on_rels if r != 'lt_priming.json'],
                         'dropping the by_time row changed the by_size selection too: with=%r '
                         'without=%r' % (on_rels, off_rels))
        self.assertEqual(len(off_rels), self.BSM.SENTINEL_COUNT)
        self.assertEqual(len(on_rels), self.BSM.SENTINEL_COUNT + 1)

    def test_the_flag_is_independent_of_the_git_directory_opt_out(self):
        """The two opt-outs share one expression. A store that is NOT a git directory and declares
        sentinel_newest True must keep its newest row — otherwise the git arm has swallowed the
        general case and C8's own control would be the only thing still passing."""
        base = self.build_chain_tree(os.path.join(self.tmp, 'data_engine'))
        measured, cands = self.measure(base)
        store, sent = self.pick(base, measured, cands, sentinel_newest=True)
        self.assertFalse(store['git_dir_store'],
                         'the fixture accidentally measured as a git directory, which would make '
                         'this control vacuous')
        self.assertIn('lt_priming.json', [r['rel'] for r in sent])


class TestC9dEmptyingThePoolIsRefused(_ChainStoreCase):
    """The floor.  A store with zero sentinels is a bigger hole than the false red it was fixing:
    A2b would SKIP (`manifest recorded no sentinel files`) and the store would fall back to A1
    existence.  So an exclusion that would empty the candidate pool is refused, and the refusal is
    recorded rather than being an invisible no-op."""

    def build_all_chain_tree(self, base):
        old = time.time() - 3600
        self._write(base, 'staffer_network_section.json', 6000, old)
        self._write(base, 'responsiveness_layers.json', 5000, old)
        self._write(base, 'efficiency_layers.json', 4000, time.time() - 10)
        return base

    ALL = ['staffer_network_section.json', 'responsiveness_layers.json', 'efficiency_layers.json']

    def test_excluding_every_candidate_is_refused_not_honoured(self):
        base = self.build_all_chain_tree(os.path.join(self.tmp, 'all_chain'))
        measured, cands = self.measure(base)
        store, sent = self.pick(base, measured, cands,
                                sentinel_excl=self.ALL, sentinel_newest=False)
        rels = [r['rel'] for r in sent]
        self.assertEqual(sorted(rels), sorted(self.ALL),
                         'the exclusion emptied the store instead of being refused; the store now '
                         'has NO content evidence at all. chosen=%r' % rels)
        self.assertIn('sentinel_exclusions_refused', store,
                      'the pool was emptied and silently re-filled with no record of it — a '
                      'relaxation that refuses itself invisibly is worse than one that fires')
        detail = store['sentinel_exclusions_refused']
        self.assertIn('would leave no candidate', detail)
        for name in self.ALL:
            self.assertIn(name, detail,
                          'the refusal message does not name the declaration it refused: %r'
                          % detail)
        self.assertNotIn('sentinel_exclusions_unmatched', store,
                         'a refused exclusion also reported staleness, which would read as if the '
                         'declaration had been applied')

    def test_the_SAME_declaration_IS_honoured_when_one_candidate_survives(self):
        """The negative control for the floor. Same declaration, same three files, plus one file
        the declaration does not name. If the exclusion is refused here too, the floor is not a
        floor — it is the exclusion never working at all."""
        base = self.build_all_chain_tree(os.path.join(self.tmp, 'all_chain'))
        self._write(base, 'congestion.json', 3000, time.time() - 3600)
        measured, cands = self.measure(base)
        store, sent = self.pick(base, measured, cands,
                                sentinel_excl=self.ALL, sentinel_newest=False)
        self.assertEqual([r['rel'] for r in sent], ['congestion.json'],
                         'with a survivor present the exclusion should have been honoured; '
                         'chosen=%r' % [r['rel'] for r in sent])
        self.assertNotIn('sentinel_exclusions_refused', store,
                         'the floor fired even though a candidate survived — the exclusion is '
                         'inert')
        self.assertEqual(store.get('sentinel_exclusions_unmatched'), [])

    def test_the_refusal_cannot_resurrect_git_bookkeeping(self):
        """CROSS-FIX INTERACTION, and the one that would be worst to get wrong: the C8 volatility
        filter runs BEFORE this one, so the set the floor falls back to is the already-volatility
        filtered list, not the raw candidates. If the refusal restored the raw set instead, DE-011
        would silently undo the 2026-08-27 git-bookkeeping fix and re-pin `.git/index` — a
        relaxation reopening the very defect the earlier fix closed."""
        base = os.path.join(self.tmp, 'repo.git')
        old = time.time() - 3600
        for rel, n in (('HEAD', 21), ('refs/heads/main', 41), ('packed-refs', 300),
                       ('logs/HEAD', 500), ('objects/ab/cccccccccccccccccccccccccccccccccccccc', 8192)):
            self._write(base, rel, n, old)
        self._write(base, 'index', 7000, time.time() - 10)
        measured, cands = self.measure(base)
        store = self.registry_store(base, sentinel_excl=['objects'], sentinel_newest=False)
        self.assertTrue(store['git_dir_store'],
                        'the fixture did not measure as a git directory, so this control would '
                        'never engage the volatility filter it is about')
        store['measured'] = measured
        sent = self.BSM.pick_sentinels(store, list(cands))
        rels = [r['rel'] for r in sent]
        # Excluding `objects` takes the ONLY non-volatile candidate, so the floor must fire.
        self.assertIn('sentinel_exclusions_refused', store,
                      'the pool was emptied but the floor did not fire; chosen=%r' % rels)
        # What it falls back to is the VOLATILITY-FILTERED list, not the raw one. The loose
        # object is content-addressed and survives; every bookkeeping path was already gone before
        # this filter ran and must stay gone.
        self.assertEqual(rels, ['objects/ab/cccccccccccccccccccccccccccccccccccccc'],
                         'the empty-pool refusal did not restore exactly the post-C8 candidate '
                         'set: %r' % rels)
        for banned in ('index', 'HEAD', 'refs/heads/main', 'packed-refs', 'logs/HEAD'):
            self.assertNotIn(banned, rels,
                             'the refusal restored the PRE-volatility candidate list and handed '
                             'git bookkeeping %r back sentinel duty — DE-011 would be silently '
                             'undoing the 2026-08-27 fix. chosen=%r' % (banned, rels))


class TestC9eByHashableIsTheLastResortOnly(_ChainStoreCase):
    """`by_hashable` exists because two stores (repo.outputs, local.lda_derived) lose their last
    hashable sentinel to the chain exclusions, and a store above the checker's WALK_LIMIT has its
    A2 scale check SKIPPED as well — so it would degenerate to A1 existence.  It is a rescue, and a
    rescue that fires when it is not needed is just a fourth sentinel nobody asked for."""

    OVER = 8 * 1024 * 1024

    def build_starved_tree(self, base):
        old = time.time() - 3600
        self._write_over_cap(base, 'org_mentions/mentions.csv', self.OVER + 300000, old)
        self._write_over_cap(base, 'org_mentions/mentions.json', self.OVER + 200000, old)
        self._write_over_cap(base, 'positions_wide.jsonl', self.OVER + 100000, old)
        self._write(base, 'rebuild_receipt.json', 2000, old)        # largest UNDER the cap
        self._write(base, 'notes.txt', 100, old)
        self._write(base, 'run_stamp.json', 50, time.time() - 10)   # newest
        return base

    def test_by_hashable_rescues_a_store_whose_every_pick_is_over_the_cap(self):
        base = self.build_starved_tree(os.path.join(self.tmp, 'lda_derived'))
        measured, cands = self.measure(base)
        self.assertEqual(self.BSM.SENTINEL_HASH_MAX_BYTES, self.OVER,
                         'the fixture sizes are pinned to the shipped cap; they must be rebuilt if '
                         'D-015 changes it')
        _store, sent = self.pick(base, measured, cands, sentinel_newest=False)
        rels = [r['rel'] for r in sent]
        hashed = [r for r in sent if r['sha256']]

        self.assertEqual(len(sent), self.BSM.SENTINEL_COUNT + 1,
                         'the rescue row was not added: %r' % rels)
        self.assertEqual([r['rel'] for r in hashed], ['rebuild_receipt.json'],
                         'the rescue did not pick the LARGEST candidate under the cap, or picked '
                         'more than one: %r' % [r['rel'] for r in hashed])
        for row in sent:
            if row['rel'] == 'rebuild_receipt.json':
                continue
            self.assertIsNone(row['sha256'])
            self.assertIn('exceeds sentinel hash cap', row['sha256_skipped_reason'] or '',
                          'an over-cap row recorded some other skip reason: %r' % row)
        # Without the rescue this store would carry three rows and ZERO bytes anyone can check.
        self.assertTrue(hashed, 'the store was left with size-and-existence evidence only')

    def test_by_hashable_adds_nothing_when_a_pick_is_already_hashable(self):
        """The negative control. Every candidate here is small, so the by_size rows are already
        hashed; a rescue that still fired would be handing every store in the registry a fourth
        sentinel and quietly inflating what A2b claims to cover."""
        base = self.build_chain_tree(os.path.join(self.tmp, 'data_engine'))
        measured, cands = self.measure(base)
        _store, sent = self.pick(base, measured, cands,
                                 sentinel_excl=self.CHAIN_OUTPUTS, sentinel_newest=False)
        self.assertEqual(len(sent), self.BSM.SENTINEL_COUNT,
                         'a store that already had a hashable sentinel gained a rescue row: %r'
                         % [r['rel'] for r in sent])
        # ...and directly, so the reason is not left to inference from the row count.
        by_size = [('electoral_history.json', 3000, 0.0)]
        self.assertEqual(self.BSM._by_hashable(by_size, [], cands), [],
                         '_by_hashable fired with an under-cap row already among the picks')

    def test_by_hashable_invents_nothing_when_no_candidate_is_under_the_cap(self):
        """The boundary. A store of nothing but over-cap files must come back with its three
        size-only rows — not a crash, and not a phantom row the checker would then try to stat."""
        base = os.path.join(self.tmp, 'all_huge')
        old = time.time() - 3600
        for rel, extra in (('a.f32', 300000), ('b.f32', 200000), ('c.f32', 100000)):
            self._write_over_cap(base, rel, self.OVER + extra, old)
        measured, cands = self.measure(base)
        _store, sent = self.pick(base, measured, cands, sentinel_newest=False)
        self.assertEqual([r['rel'] for r in sent], ['a.f32', 'b.f32', 'c.f32'])
        self.assertEqual([r['sha256'] for r in sent], [None, None, None])
        self.assertEqual(self.BSM._by_hashable(list(cands)[:3], [], cands), [],
                         '_by_hashable invented a row where no candidate is under the cap')

    def test_the_rescue_row_is_a_real_file_the_checker_can_re_stat(self):
        """A2b re-stats every recorded sentinel by relpath. A rescue row that named a path the
        store does not actually contain would fail A2b forever, which is the defect this whole
        landing is removing."""
        base = self.build_starved_tree(os.path.join(self.tmp, 'lda_derived'))
        measured, cands = self.measure(base)
        _store, sent = self.pick(base, measured, cands, sentinel_newest=False)
        row = next(r for r in sent if r['sha256'])
        full = os.path.join(base, row['rel'].replace('/', os.sep))
        self.assertTrue(os.path.isfile(full), full)
        self.assertEqual(os.stat(full).st_size, row['bytes'])
        self.assertEqual(_sha(full), row['sha256'])


class TestGuardSqliteSidecar(ControlCase):
    def test_a_db_store_with_no_sidecar_block_fails_A5(self):
        self.fx.write_file('db/thing.db', b'SQLite format 3\x00')
        self.fx.add_file_store('fix.db', 'db/thing.db')                  # sqlite block left None
        mp = self.fx.write_manifest()
        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A5 fix.db carries a sqlite sidecar block'),
                        'a .db store with no sidecar block passed; failures were %s'
                        % self.rec.failed_names())
        self.assertIn('-wal', self.rec.fail_detail('A5 fix.db carries a sqlite sidecar block'))


class TestGuardTopologyRecompute(ControlCase):
    def test_a_hand_authored_single_disk_value_fails_A6(self):
        self.fx.write_file('data/keep.json', '{"a":1}')
        store = self.fx.add_file_store('fix.keep', 'data/keep.json')
        mp = self.fx.write_manifest()
        self.assert_clean_baseline(mp)

        store['single_disk'] = False                                    # <-- the planted defect
        store['offbox_replica'] = 'onedrive_sync'
        mp = self.fx.write_manifest()
        os.utime(mp, (time.time() - 60, time.time() - 60))
        self.run_check(mp)
        # The fixture root is outside OneDrive, so the recomputation must contradict the claim.
        # A6 is rolled up across stores, so the offender has to be NAMED in the detail — a rolled
        # up verdict that could not say which store failed would be half a check.
        self.assertTrue(
            self.rec.has_fail('A6 every single_disk recomputes from the live OneDrive root'),
            'an authored copy story survived recomputation; failures were %s'
            % self.rec.failed_names())
        detail = self.rec.fail_detail('A6 every single_disk recomputes from the live OneDrive root')
        self.assertIn('fix.keep', detail)
        self.assertIn('manifest=False', detail)
        self.assertIn('DERIVED and must never be authored', detail)
        self.assertTrue(
            self.rec.has_fail('A6 every offbox_replica recomputes from the live OneDrive root'),
            'the offbox_replica half of the authored copy story survived')


class TestBkS2AndD015SentinelHashing(_ChainStoreCase):
    """BK-S2: repo.fec_code no longer pins its newest file (git-tracked source; the pin detected
    commits). D-015: inside a git directory an over-cap sentinel is hashed, not skipped."""

    def test_fec_code_does_not_pin_its_newest_tracked_file(self):
        old = time.time() - 3600
        for i in range(3):
            self._write(self.tmp, 'engine/big_%d.py' % i, 3000 + i, old)
        newest = 'surfaces/staffer_live/approved/workspace.js'
        self._write(self.tmp, newest, 100, time.time() - 10)
        store = dict(next(s for s in self.BSM.REGISTRY if s['id'] == 'repo.fec_code'))
        store.update(path=self.tmp, git_dir_store=False)
        measured, cands = self.measure(self.tmp)
        store['measured'] = measured
        self.assertNotIn(newest, {r['rel'] for r in self.BSM.pick_sentinels(store, cands)})
        control = dict(store, sentinel_newest=True)        # the flag removed
        self.assertIn(newest, {r['rel'] for r in self.BSM.pick_sentinels(control, cands)})

    def test_git_dir_hashes_an_over_cap_loose_object(self):
        base = os.path.join(self.tmp, 'mirror.git')
        os.makedirs(os.path.join(base, 'refs'))
        self._write(base, 'HEAD', 23, time.time() - 3600)
        pack = 'objects/ab/%s' % ('a' * 38)          # a loose object: packs are never sentinels
        full = self._write_over_cap(base, pack, self.BSM.SENTINEL_HASH_MAX_BYTES + 1,
                                    time.time() - 3600)
        with open(full, 'rb') as fh:
            want = hashlib.sha256(fh.read()).hexdigest()
        measured, cands = self.measure(base)
        store, sent = self.pick(base, measured, cands)
        self.assertTrue(store['git_dir_store'])
        row = next(r for r in sent if r['rel'] == pack)
        self.assertEqual(row['sha256'], want, row)
        self.assertIn('sha256_seconds', row)
        # Negative control: the same file outside a git directory keeps the cap.
        store['git_dir_store'] = False
        row = next(r for r in self.BSM.pick_sentinels(store, list(cands)) if r['rel'] == pack)
        self.assertIsNone(row['sha256'])
        self.assertIn('exceeds sentinel hash cap', row['sha256_skipped_reason'] or '')


class TestGitRepackDoesNotRedThePins(_ChainStoreCase):
    """2026-10-11: both A2b failures on the 2026-10-08 manifest were pinned pack files that a
    `git gc` had renamed. A git directory now pins COMMITS (HEAD, trunk, tags) and never a pack.
    Built on a REAL repository, because a repack is only simulated honestly by git itself."""

    def git(self, *args):
        return subprocess.run(['git', '-C', self.work] + list(args), check=True,
                              capture_output=True, text=True).stdout

    def commit(self, name):
        with open(os.path.join(self.work, name), 'wb') as fh:
            fh.write(os.urandom(20000))           # incompressible: the pack outranks the hooks
        self.git('add', name)
        self.git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', name)

    def test_a_repack_that_renames_every_pack_leaves_A2b_green(self):
        self.work = os.path.join(self.tmp, 'w')
        os.makedirs(self.work)
        self.git('init', '-q', '-b', 'main')
        self.commit('a.txt')
        self.git('repack', '-a', '-d', '-q')
        base = os.path.join(self.work, '.git')
        packdir = os.path.join(base, 'objects', 'pack')
        orphan = 'objects/c4/tmp_obj_JfCVqL'          # what a crashed git write leaves; gc deletes it
        self._write(base, orphan, 90000, time.time() - 3600)
        measured, cands = self.measure(base)
        store, sent = self.pick(base, measured, cands)
        self.assertTrue(store['git_dir_store'])
        before = set(os.listdir(packdir))
        self.commit('b.txt')                          # ordinary work, then the simulated gc
        self.git('repack', '-a', '-d', '-q')
        self.assertTrue(before.isdisjoint(os.listdir(packdir)),
                        'the repack kept a pack name, so this control proves nothing')
        rec = Recorder()
        store['sentinels'] = sent
        SMC._check_sentinels(store, 'fix.git', 'gen', rec.emit,
                             lambda n, why: rec.emit(n, False, why), True, rec.note)
        self.assertTrue(rec.has_pass('A2b fix.git sentinels unchanged'), rec.failed)
        self.assertEqual({r.get('git_ref') for r in sent if r.get('git_commit')},
                         {'HEAD', 'refs/heads/main'}, sent)
        self.assertFalse([r for r in sent if 'objects/pack/' in (r['rel'] or '')], sent)
        self.assertNotIn(orphan, [r['rel'] for r in sent], 'a crash-orphaned tmp_obj was pinned')
        # Negative control: a pinned commit the repository no longer reaches goes red.
        dead = dict(next(r for r in sent if r.get('git_commit')), git_commit='0' * 40)
        rec = Recorder()
        SMC._check_sentinels(dict(store, sentinels=[dead]), 'fix.git', 'gen', rec.emit,
                             lambda n, why: rec.emit(n, False, why), True, rec.note)
        self.assertTrue(rec.has_fail('A2b fix.git sentinels unchanged'), rec.failed_names())


class _PosixOs(object):
    """`os` as the checker sees it inside a Linux container: only `name` differs, so nothing else on this host is disturbed."""
    name = 'posix'

    def __getattr__(self, attr):
        return getattr(os, attr)


class TestContainerHost(ControlCase):
    """Compute lane MEL (D-173/D-177): the chain's last step runs this section in a Linux container, where the manifest's
    Windows paths and the Windows host's volumes and OneDrive replica do not exist."""

    def _wrong_topology_manifest(self):
        self.fx.write_file('data/keep.json', '{"a":1}')
        store = self.fx.add_file_store('fix.keep', 'data/keep.json')
        store['volume'] = 'Q:'                                  # a drive this host does not have
        return self.fx.write_manifest(host=dict(self.fx.manifest()['host'], onedrive_root='Q:\\OneDrive'))

    def test_on_windows_a_wrong_volume_still_fails_A6(self):
        # negative control: the skip below must not have switched the check off where it is observable
        mp = self._wrong_topology_manifest()
        self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A6 every store volume matches observation'), self.rec.failed_names())
        self.assertTrue(self.rec.has_fail('A6 OneDrive root matches observation'), self.rec.failed_names())

    def test_off_windows_host_topology_is_skipped_with_its_reason_not_passed_or_failed(self):
        mp = self._wrong_topology_manifest()
        with unittest.mock.patch.object(SMC, 'os', _PosixOs()):
            tally = self.run_check(mp)
        self.assertEqual([n for n in self.rec.failed_names() if 'A6' in n and 'durable' not in n], [], self.rec.failed_names())
        self.assertFalse(self.rec.has_pass('A6 every store volume matches observation'), 'a vacuous pass is not a skip')
        reasons = [why for name, why in tally['skips'] if name == 'A6 host topology']
        self.assertEqual(len(reasons), 1)
        self.assertIn('Windows host', reasons[0])
        self.assertTrue(self.rec.has_pass('A1 fix.keep still exists'), 'the stores themselves are still examined')

    def test_the_manifest_paths_are_mapped_onto_the_mounts(self):
        import paths
        self.fx.write_file('data/keep.json', '{"a":1}')
        store = self.fx.add_file_store('fix.keep', 'data/keep.json')
        store['root_id'] = 'melange_data'
        store['path'] = 'C:\\melange-data\\data\\keep.json'     # as the Windows host wrote it
        mp = self.fx.write_manifest(roots=[
            {'id': 'melange_data', 'path': 'C:\\melange-data', 'label': 'data root', 'present': True}])
        with unittest.mock.patch.object(paths, '_IS_WINDOWS', False), \
                unittest.mock.patch.object(paths, 'MELANGE_DATA', self.fx.root):
            with unittest.mock.patch.object(SMC, 'os', _PosixOs()):
                self.run_check(mp)
        self.assertTrue(self.rec.has_pass('A1 fix.keep still exists'), self.rec.failed_names())
        self.assertFalse(self.rec.has_fail('A0 at least one declared root'), self.rec.failed_names())

    def test_no_git_binary_skips_commit_pins_off_windows_and_still_fails_on_windows(self):
        pin = {'rel': None, 'git_commit': 'a' * 40, 'git_ref': 'HEAD'}
        store = {'path': self.fx.root, 'measured': {'kind': 'dir'}, 'sentinels': [pin]}
        no_git = unittest.mock.patch.object(SMC.subprocess, 'run', side_effect=FileNotFoundError('git'))

        def check():
            rec, skips = Recorder(), []
            SMC._check_sentinels(store, 'fix.git', 'gen', rec.emit, lambda n, why: skips.append((n, why)), True, rec.note)
            return rec, skips

        with no_git, unittest.mock.patch.object(SMC, 'os', _PosixOs()):
            rec, skips = check()
        self.assertEqual(rec.failed_names(), [], 'a container without git must not fail the commit pin')
        self.assertEqual([n for n, _w in skips], ['A2b fix.git git commit pins'])
        with no_git:                                              # negative control: this host is Windows, behaviour unchanged
            rec, skips = check()
        self.assertTrue(rec.has_fail('A2b fix.git sentinels unchanged'), rec.failed_names())
        self.assertEqual(skips, [])

    def test_unmapped_roots_stay_absent_and_every_root_missing_still_fails(self):
        import paths
        self.fx.write_file('data/keep.json', '{"a":1}')
        store = self.fx.add_file_store('fix.keep', 'data/keep.json')
        store['root_id'] = 'elsewhere'
        store['path'] = 'D:\\elsewhere\\keep.json'
        mp = self.fx.write_manifest(roots=[{'id': 'elsewhere', 'path': 'D:\\elsewhere', 'label': 'x', 'present': True}])
        with unittest.mock.patch.object(paths, '_IS_WINDOWS', False):
            self.run_check(mp)
        self.assertTrue(self.rec.has_fail('A0 at least one declared root is present on this host'), self.rec.failed_names())


if __name__ == '__main__':
    unittest.main(verbosity=2)
