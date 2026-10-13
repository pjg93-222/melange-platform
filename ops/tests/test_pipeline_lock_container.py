# -*- coding: utf-8 -*-
"""Contract suite for the container-safe writer lease in fec/pipeline_lock.py (design finding F9).

A container cannot see Windows pids, so a container that judged a Windows-held lease by pid would
call it stale and remove it -- two writers on one tree.  The lease therefore records its holder's
host identity, and a lease from another host or pid namespace is HELD unless the holder's own
heartbeat file (``.pipeline_run.lock`` -> ``.pipeline_run.heartbeat.lock``) is older than the timeout.  Same host and namespace keep the
existing pid / process-start-time rule exactly.

The operating system is simulated by patching the one seam ``pipeline_lock._windows`` (and
``host_identity`` for a fixed POSIX identity); ``os.name`` is never patched.  Every lease lives in a
tempfile.mkdtemp() tree; no chain runs, no real /proc is needed, and every wait is bounded under 3 s.

Plain unittest, no dependencies:  python ops/tests/test_pipeline_lock_container.py
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fec"))
import pipeline_lock
from process_liveness import process_start_time

MINE = 'posix:hostA:boot1:pidns1:' + 'a' * 64
OTHER = 'posix:hostB:boot2:pidns2:' + 'b' * 64
THREAD = 'pipeline-lock-heartbeat'


def heartbeat_threads():
    return [t for t in threading.enumerate() if t.name == THREAD]


def dead_pid():
    child = subprocess.Popen([sys.executable, '-c', 'pass'])
    child.wait(timeout=30)
    return child.pid


class LeaseTestCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.root), ignore_errors=True)
        self.path = self.root / '.pipeline_run.lock'
        self.beat = self.root / '.pipeline_run.heartbeat.lock'
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for name in (pipeline_lock.LOCK_TOKEN_ENV, pipeline_lock.LOCK_OWNER_PID_ENV,
                     pipeline_lock.LOCK_STALE_SECONDS_ENV):
            os.environ.pop(name, None)

    def patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sim(self, windows):
        self.patch(pipeline_lock, '_windows', return_value=windows)
        if not windows:
            self.patch(pipeline_lock, 'host_identity', return_value=MINE)

    def write_lease(self, pid, host=None, token='tok', recycled=False, **extra):
        body = {'pid': pid, 'token': token, 'started_utc': time.time(), 'owner_kind': 'peer'}
        start = process_start_time(pid)
        if start is not None:
            body['proc_start'] = start - 3600.0 if recycled else start
        if host is not None:
            body['host'] = host
        body.update(extra)
        self.path.write_text(json.dumps(body), encoding='utf-8')
        return body

    def write_beat(self, token='tok', age=0.0, text=None):
        self.beat.write_text(text if text is not None else json.dumps(
            {'token': token, 'host': OTHER, 'pid': 1, 'ts': time.time() - age}), encoding='utf-8')
        when = time.time() - age
        os.utime(str(self.beat), (when, when))

    def acquire(self):
        """Take the lease and return the lease dict (the context closes at once)."""
        with pipeline_lock.pipeline_run_lock(self.path, owner_kind='test') as lease:
            return lease

    def assertBlocked(self, host_in_message=None):
        with self.assertRaises(RuntimeError) as caught:
            self.acquire()
        message = str(caught.exception)
        self.assertTrue(message.startswith('Moneyball pipeline is already running (pid '), message)
        self.assertTrue(self.path.exists(), 'a blocked acquirer removed the lease')
        if host_in_message:
            self.assertIn(', host ' + host_in_message, message)
        return message

    def assertRecovered(self):
        lease = self.acquire()
        self.assertFalse(lease['adopted'])
        self.assertEqual(os.getpid(), lease['pid'])
        self.assertFalse(self.path.exists(), 'the new holder did not release its own lease')


class PayloadAndIdentity(LeaseTestCase):
    def _payload_carries_host(self):
        with pipeline_lock.pipeline_run_lock(self.path, owner_kind='test') as lease:
            self.assertEqual(pipeline_lock.host_identity(), lease['host'])
            on_disk = json.loads(self.path.read_text(encoding='utf-8'))
            self.assertEqual(pipeline_lock.host_identity(), on_disk['host'])
            for key in ('pid', 'token', 'started_utc', 'owner_kind', 'proc_start'):
                self.assertIn(key, on_disk)      # the existing keys are unchanged; host is additive

    def test_payload_and_lease_file_carry_host_on_posix(self):
        self.sim(False)
        self._payload_carries_host()

    def test_payload_and_lease_file_carry_host_on_windows(self):
        self.sim(True)
        self._payload_carries_host()
        self.assertTrue(pipeline_lock.host_identity().startswith('nt:'))

    def _posix_identity(self, files, namespace='pid:[4026531836]'):
        readlink = {'side_effect': OSError} if namespace is None else {'return_value': namespace}
        with mock.patch.object(pipeline_lock, '_windows', return_value=False), \
                mock.patch.object(pipeline_lock.socket, 'gethostname', return_value='box'), \
                mock.patch.object(pipeline_lock, '_proc_text', side_effect=lambda name: files.get(name, '')), \
                mock.patch.object(pipeline_lock.os, 'readlink', **readlink):
            return pipeline_lock.host_identity()

    def test_host_identity_shape_windows(self):
        with mock.patch.object(pipeline_lock, '_windows', return_value=True), \
                mock.patch.object(pipeline_lock.socket, 'gethostname', return_value='box'):
            self.assertEqual('nt:box', pipeline_lock.host_identity())

    def test_host_identity_shape_posix_container_id_from_mountinfo_then_cgroup(self):
        cid = 'c0ffee' + '0' * 58
        mount = ('421 410 0:61 / / rw - overlay\n'
                 '422 421 8:1 /var/lib/docker/containers/%s/hostname /etc/hostname' % cid)
        boot = {'/proc/sys/kernel/random/boot_id': 'bootid\n'}
        self.assertEqual('posix:box:bootid:pid:[4026531836]:' + cid,
                         self._posix_identity({**boot, '/proc/self/mountinfo': mount}))
        self.assertEqual('posix:box:bootid:pid:[4026531836]:' + cid,
                         self._posix_identity({**boot, '/proc/self/mountinfo': 'no id here',
                                               '/proc/self/cgroup': '12:cpu:/docker/%s\n' % cid}))

    def test_host_identity_ignores_hex_strings_that_are_not_exactly_64_long(self):
        for length in (63, 65):
            with self.subTest(length=length):
                files = {'/proc/self/mountinfo': '/x/' + 'a' * length + '/y'}
                self.assertEqual('posix:box::pid:[4026531836]:', self._posix_identity(files))

    def test_host_identity_posix_with_nothing_readable(self):
        self.assertEqual('posix:box:::', self._posix_identity({}, namespace=None))


class OwnHostKeepsThePidRule(LeaseTestCase):
    def setUp(self):
        super().setUp()
        self.sim(False)

    def test_live_holder_blocks(self):
        self.write_lease(os.getpid(), host=MINE)
        message = self.assertBlocked()
        self.assertNotIn(', host ', message, 'a same-host refusal grew a host hint')

    def test_dead_pid_is_recovered(self):
        self.write_lease(dead_pid(), host=MINE)
        self.assertRecovered()

    def test_recycled_pid_is_recovered(self):
        self.write_lease(os.getpid(), host=MINE, recycled=True)
        self.assertRecovered()

    def test_heartbeat_is_not_consulted_for_an_own_host_lease(self):
        # NEGATIVE CONTROL for the foreign tests below: an ancient heartbeat must not rescue a LIVE own-host holder.
        self.write_lease(os.getpid(), host=MINE)
        self.write_beat(age=10 ** 6)
        self.assertBlocked()


class ForeignHostIsHeldUnlessItsHeartbeatIsStale(LeaseTestCase):
    def setUp(self):
        super().setUp()
        self.sim(False)
        self.dead = dead_pid()      # the pid rule would call this holder stale; it must not be asked

    def test_fresh_heartbeat_blocks_and_the_message_names_the_host_and_the_way_out(self):
        self.write_lease(self.dead, host=OTHER)
        self.write_beat(age=1)
        message = self.assertBlocked(host_in_message=OTHER)
        self.assertIn('.pipeline_run.heartbeat.lock', message)
        self.assertIn(pipeline_lock.LOCK_STALE_SECONDS_ENV, message)

    def test_same_lease_from_this_host_is_recovered_control(self):
        self.write_lease(self.dead, host=MINE)
        self.assertRecovered()

    def test_stale_heartbeat_releases_the_lease(self):
        self.write_lease(self.dead, host=OTHER)
        self.write_beat(age=pipeline_lock.LOCK_STALE_SECONDS_DEFAULT + 100)
        lease = self.acquire()
        self.assertEqual(os.getpid(), lease['pid'])
        self.assertNotEqual('tok', lease['token'])

    def test_heartbeat_just_inside_the_timeout_still_blocks(self):
        self.write_lease(self.dead, host=OTHER)
        self.write_beat(age=pipeline_lock.LOCK_STALE_SECONDS_DEFAULT - 100)
        self.assertBlocked()

    def test_no_heartbeat_file_blocks(self):
        self.write_lease(self.dead, host=OTHER)
        self.assertFalse(self.beat.exists())
        self.assertBlocked()

    def test_heartbeat_with_another_token_blocks_even_when_ancient(self):
        self.write_lease(self.dead, host=OTHER)
        self.write_beat(token='someone-else', age=10 ** 6)
        self.assertBlocked()

    def test_unreadable_or_malformed_heartbeat_blocks_even_when_ancient(self):
        self.write_lease(self.dead, host=OTHER)
        for text in ('{not json', '[]', '"tok"', ''):
            with self.subTest(text=text):
                self.write_beat(text=text, age=10 ** 6)
                self.assertBlocked()

    def test_lease_without_a_token_blocks_even_with_an_ancient_tokenless_heartbeat(self):
        self.write_lease(self.dead, host=OTHER, token=None)
        self.write_beat(text='{}', age=10 ** 6)
        self.assertBlocked()

    def test_heartbeat_dated_in_the_future_is_clock_skew_and_fresh(self):
        self.write_lease(self.dead, host=OTHER)
        self.write_beat(age=-3600)
        self.assertBlocked()

    def test_decision_function_alone_without_a_path_holds_a_foreign_lease(self):
        body = {'pid': self.dead, 'token': 'tok', 'host': OTHER}
        self.assertTrue(pipeline_lock._holder_is_live(body))
        self.write_beat(age=10 ** 6)
        self.assertTrue(pipeline_lock._holder_is_live(body), 'no path => no heartbeat to consult => held')
        self.assertFalse(pipeline_lock._holder_is_live(body, self.path))
        self.assertTrue(pipeline_lock._stale_lock_may_be_removed(self.path, body))

    def test_stale_timeout_env_is_honoured_and_a_bad_value_falls_back(self):
        self.write_lease(self.dead, host=OTHER)
        self.write_beat(age=100)
        self.assertEqual(600.0, pipeline_lock._stale_seconds())
        self.assertBlocked()
        os.environ[pipeline_lock.LOCK_STALE_SECONDS_ENV] = '50'
        self.assertEqual(50.0, pipeline_lock._stale_seconds())
        self.assertRecovered()
        for bad in ('abc', '', '0', '-5', 'nan', 'inf'):
            with self.subTest(value=bad):
                os.environ[pipeline_lock.LOCK_STALE_SECONDS_ENV] = bad
                self.assertEqual(600.0, pipeline_lock._stale_seconds())
                self.write_lease(self.dead, host=OTHER)
                self.write_beat(age=100)
                self.assertBlocked()

    def test_a_windows_lease_is_foreign_to_a_posix_peer_and_never_heartbeats(self):
        self.write_lease(self.dead, host='nt:WINBOX')
        self.assertBlocked(host_in_message='nt:WINBOX')


class LegacyAndWindowsPeers(LeaseTestCase):
    def test_legacy_lease_on_windows_keeps_the_pid_rule(self):
        self.sim(True)
        self.write_lease(dead_pid())
        self.assertRecovered()
        self.write_lease(os.getpid())
        self.assertNotIn(', host', self.assertBlocked(), 'the Windows refusal text changed')

    def test_legacy_lease_on_posix_is_foreign_even_with_a_dead_pid(self):
        self.sim(False)
        self.write_lease(dead_pid())
        self.assertBlocked(host_in_message='unknown')
        self.write_beat(age=10 ** 6)       # control: only the holder's own stale heartbeat frees it
        self.assertRecovered()

    def test_windows_peer_with_another_hostname_still_gets_the_pid_rule(self):
        self.sim(True)
        self.write_lease(dead_pid(), host='nt:RENAMED-OR-OTHER')
        self.assertRecovered()
        self.write_lease(os.getpid(), host='nt:RENAMED-OR-OTHER')
        self.assertBlocked()

    def test_posix_lease_is_foreign_to_a_windows_peer_even_with_a_dead_pid(self):
        self.sim(True)
        self.write_lease(dead_pid(), host=OTHER)
        self.assertBlocked(host_in_message=OTHER)
        self.write_beat(age=10 ** 6)
        self.assertRecovered()


class HeartbeatWriter(LeaseTestCase):
    def test_posix_heartbeat_exists_refreshes_and_is_removed_on_exit(self):
        self.sim(False)
        self.patch(pipeline_lock, 'HEARTBEAT_SECONDS', new=0.05)
        with pipeline_lock.pipeline_run_lock(self.path, owner_kind='test') as lease:
            first = json.loads(self.beat.read_text(encoding='utf-8'))
            self.assertEqual(lease['token'], first['token'])
            self.assertEqual(MINE, first['host'])
            self.assertEqual(os.getpid(), first['pid'])
            self.assertEqual(1, len(heartbeat_threads()))
            deadline = time.time() + 2.5
            refreshed = False
            while time.time() < deadline and not refreshed:
                try:
                    refreshed = json.loads(self.beat.read_text(encoding='utf-8'))['ts'] != first['ts']
                except (OSError, ValueError):
                    pass
                time.sleep(0.02)
            self.assertTrue(refreshed, 'the heartbeat was never rewritten')
        self.assertFalse(self.beat.exists())
        self.assertFalse(self.path.exists())
        self.assertEqual([], heartbeat_threads())
        self.assertEqual([], [p.name for p in self.root.iterdir()], 'debris left beside the lease')

    def test_heartbeat_file_name_ends_in_lock_so_the_system_manifest_walk_skips_it(self):
        # MINOR 2 of the 2026-10-12 review: the manifest/sentinel walk excludes '*.lock' and '.tmp'; a '.heartbeat' suffix was walked.
        beat = pipeline_lock._heartbeat_path(self.path)
        self.assertEqual(('.pipeline_run.heartbeat.lock', self.root), (beat.name, beat.parent))
        self.assertEqual('custom.heartbeat.lock', pipeline_lock._heartbeat_path(self.root / 'custom').name)   # no .lock suffix: appended
        self.assertNotEqual(self.path, beat)
        self.sim(False)
        with pipeline_lock.pipeline_run_lock(self.path, owner_kind='test'):      # first beat is written before the yield
            names = sorted(p.name for p in self.root.iterdir())
        self.assertEqual(['.pipeline_run.heartbeat.lock', '.pipeline_run.lock'], names)
        self.assertTrue(all(n.endswith('.lock') for n in names))

    def test_windows_writes_no_heartbeat_and_starts_no_thread(self):
        self.sim(True)
        self.patch(pipeline_lock, 'HEARTBEAT_SECONDS', new=0.05)
        with pipeline_lock.pipeline_run_lock(self.path, owner_kind='test'):
            time.sleep(0.2)
            self.assertFalse(self.beat.exists())
            self.assertEqual([], heartbeat_threads())
            self.assertEqual(['.pipeline_run.lock'], [p.name for p in self.root.iterdir()])
        self.assertEqual([], list(self.root.iterdir()))

    def test_heartbeat_with_another_token_is_not_deleted_on_exit(self):
        self.sim(False)
        with pipeline_lock.pipeline_run_lock(self.path, owner_kind='test'):
            self.write_beat(token='a-successor')
        self.assertTrue(self.beat.exists(), 'exit deleted a heartbeat that is not ours')
        self.assertEqual('a-successor', json.loads(self.beat.read_text(encoding='utf-8'))['token'])

    def test_heartbeat_failures_never_reach_the_chain(self):
        self.sim(False)
        with mock.patch.object(pipeline_lock.os, 'replace', side_effect=PermissionError('locked')):
            with pipeline_lock.pipeline_run_lock(self.path, owner_kind='test') as lease:
                self.assertFalse(lease['adopted'])
        self.assertEqual([], list(self.root.iterdir()), 'a failed heartbeat left the lease or a temp file behind')

    def test_adopted_lease_writes_nothing_and_removes_nothing(self):
        self.sim(False)
        with pipeline_lock.pipeline_run_lock(self.path, owner_kind='parent') as lease:
            self.beat.unlink()
            handoff = pipeline_lock.child_lock_environment(lease)
            with mock.patch.dict(os.environ, handoff), \
                    mock.patch.object(os, 'getppid', return_value=os.getpid()):
                before = len(heartbeat_threads())
                with pipeline_lock.pipeline_run_lock(self.path) as child:
                    self.assertTrue(child['adopted'])
                self.assertFalse(self.beat.exists(), 'an adopting child wrote a heartbeat')
                self.assertEqual(before, len(heartbeat_threads()))
            self.assertTrue(self.path.exists(), 'the adopting child removed the parent lease')
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
