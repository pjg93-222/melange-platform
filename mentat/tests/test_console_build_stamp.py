"""The build stamp answers at once, even when the revision lookup is wedged.

A `git` child on this OneDrive-backed tree can stop being killable, and the
console window hung at startup because /api/session waited for one. Nothing
here spawns git or reads the real repository's refs: the lookup is replaced by
a stand-in that blocks, the wedged child is a sleeping Python, and every gitdir
layout is built in a temporary folder.
"""
import http.client
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / "app"
spec = importlib.util.spec_from_file_location("console_build_stamp_server", APP / "server.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

# Every field the page and the launcher read out of the build stamp.
BUILD_FIELDS = ('git_sha', 'code_sha256', 'assets_sha256', 'on_disk_code_sha256_now',
                'on_disk_assets_sha256_now', 'started_at', 'port', 'pid', 'busy')
RESOLVED = 'feedface1'


def join_resolver(timeout=10):
    """Wait out the resolver's thread, found by the name the resolver gives it."""
    for thread in threading.enumerate():
        if thread.name == 'git-sha':
            thread.join(timeout=timeout)
            return not thread.is_alive()
    return True


class BuildStampResolverTests(unittest.TestCase):
    """The request path against a revision lookup that never comes back."""

    def setUp(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.calls = []
        self.temp = tempfile.TemporaryDirectory(prefix='mentat-build-stamp-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        app.reset_git_sha_cache()
        lookup = patch.object(app, 'git_sha_lookup', self.lookup)
        lookup.start()
        # Cleanups run last-registered-first, so the resolver thread is let go
        # and joined before its stand-in is withdrawn. A worker released after
        # that could call the real lookup and reach the real repository.
        self.addCleanup(app.reset_git_sha_cache)
        self.addCleanup(lookup.stop)
        self.addCleanup(self.let_go)

    def lookup(self):
        """Stand in for the resolver's lookup: count, announce, then block."""
        with self.lock:
            self.calls.append(1)
        self.entered.set()
        self.release.wait(30)
        return RESOLVED

    def let_go(self):
        self.release.set()
        self.assertTrue(join_resolver(), 'the resolver thread outlived the test')

    def resolved(self, timeout=10):
        """The published revision, waited for through the public reader only."""
        deadline = time.monotonic() + timeout
        value = app.git_sha_cached()
        while value == 'unknown' and time.monotonic() < deadline:
            time.sleep(0.01)
            value = app.git_sha_cached()
        return value

    def start_server(self):
        console = SimpleNamespace(token='build-stamp-test-token', read_only=True,
                                  report_directory=self.root, configured_root=str(self.root),
                                  root=self.root)
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        server.app = console
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()

        def stop():
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive(), 'the console thread did not stop')

        self.addCleanup(stop)
        return server

    def get(self, server, path):
        """One local GET. No Accept-Encoding is sent, so the body is plain JSON."""
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            connection.request('GET', path, headers={'Host': '127.0.0.1:%d' % server.server_port})
            response = connection.getresponse()
            body = response.read()
            self.assertIsNone(response.getheader('Content-Encoding'))
            return response.status, json.loads(body)
        finally:
            connection.close()

    def test_a_wedged_revision_lookup_does_not_delay_the_session(self):
        server = self.start_server()
        started = time.monotonic()
        status, body = self.get(server, '/api/session?light=1')
        elapsed = time.monotonic() - started
        self.assertEqual(status, 200)
        self.assertLess(elapsed, 2, '/api/session took %.2fs with the revision lookup wedged' % elapsed)
        self.assertTrue(self.entered.wait(5), 'the resolver never ran its lookup')
        for field in BUILD_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, body)
                self.assertIn(field, body['build'])
        self.assertEqual(body['git_sha'], 'unknown')
        self.assertEqual(body['build']['git_sha'], 'unknown')
        self.assertEqual(body['code_sha256'], app.CODE_TREE_SHA256)
        self.assertEqual(body['assets_sha256'], app.ASSET_TREE_SHA256)
        self.assertEqual(body['on_disk_code_sha256_now'], body['code_sha256'])
        self.assertEqual(body['on_disk_assets_sha256_now'], body['assets_sha256'])
        self.assertEqual((body['pid'], body['port']), (os.getpid(), server.server_port))
        self.assertGreater(body['started_at'], 0)
        self.assertIs(body['busy'], False)

    def test_concurrent_callers_start_exactly_one_lookup(self):
        barrier = threading.Barrier(8)
        answers = []
        answers_lock = threading.Lock()

        def caller():
            barrier.wait(10)
            value = app.git_sha_cached()
            with answers_lock:
                answers.append(value)

        threads = [threading.Thread(target=caller, name='caller-%d' % index, daemon=True)
                   for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive(), 'a caller blocked on the revision lookup')
        self.assertTrue(self.entered.wait(5), 'the resolver never ran its lookup')
        self.assertEqual(len(self.calls), 1, '%d lookups ran where one was allowed' % len(self.calls))
        self.assertEqual(len(answers), 8)
        for value in answers:
            self.assertIsInstance(value, str)
            self.assertEqual(value, 'unknown')
        self.release.set()
        self.assertEqual(self.resolved(), RESOLVED)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(app.build_status(SimpleNamespace(), 8802)['git_sha'], RESOLVED)

    def test_a_lookup_that_raises_neither_escapes_nor_repeats(self):
        calls = []
        called = threading.Event()

        def failing():
            calls.append(1)
            called.set()
            raise RuntimeError('the gitdir is unreadable')

        with patch.object(app, 'git_sha_lookup', failing):
            self.assertEqual(app.git_sha_cached(), 'unknown')
            self.assertTrue(called.wait(5), 'the resolver never ran its lookup')
            self.assertTrue(join_resolver(5), 'the resolver thread did not finish')
            self.assertEqual(app.git_sha_cached(), 'unknown')
            self.assertEqual(len(calls), 1, 'a failed lookup was retried on the request path')


class GitShaSubprocessTests(unittest.TestCase):
    """git_sha() is the fallback, so it must return even from a wedged child."""

    def test_a_child_that_outlives_the_timeout_answers_unknown(self):
        children = []
        real_popen = app.subprocess.Popen

        def spy(*args, **keywords):
            child = real_popen(*args, **keywords)
            children.append(child)
            return child

        # A sleeping Python stands in for the git child that would not die.
        command = [sys.executable, '-c', 'import time; time.sleep(60)']
        with patch.object(app, 'GIT_SHA_COMMAND', command), \
                patch.object(app, 'GIT_SHA_TIMEOUT_SECONDS', 1), \
                patch.object(app.subprocess, 'Popen', spy):
            started = time.monotonic()
            value = app.git_sha()
            elapsed = time.monotonic() - started
        self.assertEqual(len(children), 1)
        self.addCleanup(children[0].kill)
        self.assertEqual(value, 'unknown')
        self.assertLess(elapsed, 20, 'git_sha waited %.1fs on a child sleeping 60s' % elapsed)
        self.assertIsNotNone(children[0].poll(), 'the timed-out child was left running')

    def test_a_child_that_leaks_its_stdout_does_not_hold_the_lookup(self):
        """The shape of the real wedge: killing the child does not close stdout.

        A grandchild inherits the handle, so draining a PIPE would wait for it.
        This is what capture_output did, and what a temporary file does not.
        """
        directory = tempfile.TemporaryDirectory(prefix='mentat-leaky-child-')
        self.addCleanup(directory.cleanup)
        record = Path(directory.name) / 'grandchild.pid'
        leaky = ('import subprocess, sys, time; '
                 'child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]); '
                 'open(sys.argv[1], "w").write(str(child.pid)); '
                 'time.sleep(30)')
        command = [sys.executable, '-c', leaky, str(record)]
        with patch.object(app, 'GIT_SHA_COMMAND', command), \
                patch.object(app, 'GIT_SHA_TIMEOUT_SECONDS', 1):
            started = time.monotonic()
            value = app.git_sha()
            elapsed = time.monotonic() - started
        self.addCleanup(self.reap, record)
        self.assertEqual(value, 'unknown')
        self.assertLess(elapsed, 5, 'git_sha waited %.1fs on a leaked stdout handle' % elapsed)

    def reap(self, record):
        """Kill the grandchild the leaky child left holding the handle."""
        try:
            os.kill(int(record.read_text().strip()), signal.SIGTERM)
        except (OSError, ValueError):
            pass

    def test_a_child_that_prints_nothing_useful_answers_unknown(self):
        command = [sys.executable, '-c', 'print("not a revision")']
        with patch.object(app, 'GIT_SHA_COMMAND', command):
            self.assertEqual(app.git_sha(), 'unknown')

    def test_a_revision_on_stdout_is_read_back_off_the_temporary_file(self):
        command = [sys.executable, '-c', 'print("b9fe34d02")']
        with patch.object(app, 'GIT_SHA_COMMAND', command):
            self.assertEqual(app.git_sha(), 'b9fe34d02')


class GitDirReaderTests(unittest.TestCase):
    """The revision read out of Git's own files, across the layouts on this box."""

    SHA = 'b9fe34d02ad08a2197677b76a0d07690797c3676'
    OTHER = '1c2d3e4f50000000000000000000000000000000'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mentat-gitdir-')
        self.addCleanup(self.temp.cleanup)

    def use(self, project):
        """Point the reader at a synthetic checkout instead of the real one."""
        patcher = patch.object(app, 'PROJECT', project)
        patcher.start()
        self.addCleanup(patcher.stop)
        return project

    def worktree(self, name, head, *, local_ref=None, common_ref=None, packed=None,
                 commondir='../..', pointer=None, marker=None):
        """A linked worktree: `.git` is a FILE naming a gitdir under another repo.

        The `.git` file sits in the app folder's parent, one level above
        PROJECT, which is how the Mentat checkout on this box is laid out.
        """
        root = Path(self.temp.name) / name
        project = root / 'checkout' / 'mentat'
        project.mkdir(parents=True)
        common = root / 'common' / '.git'
        gitdir = common / 'worktrees' / 'mentat'
        gitdir.mkdir(parents=True)
        if head is not None:
            (gitdir / 'HEAD').write_text(head + '\n', encoding='utf-8')
        if commondir is not None:
            (gitdir / 'commondir').write_text(commondir + '\n', encoding='utf-8')
        for base, value in ((gitdir, local_ref), (common, common_ref)):
            if value is not None:
                reference = base / 'refs' / 'heads' / 'main'
                reference.parent.mkdir(parents=True, exist_ok=True)
                reference.write_text(value + '\n', encoding='utf-8')
        if packed is not None:
            (common / 'packed-refs').write_text(packed, encoding='utf-8')
        if marker is None:
            marker = 'gitdir: %s' % (pointer or str(gitdir))
        (project.parent / '.git').write_text(marker + '\n', encoding='utf-8')
        return self.use(project)

    def checkout(self, name, head, *, loose=None):
        """A plain checkout: `.git` is a directory, with no commondir at all."""
        root = Path(self.temp.name) / name
        project = root / 'checkout' / 'mentat'
        project.mkdir(parents=True)
        gitdir = root / 'checkout' / '.git'
        gitdir.mkdir(parents=True)
        if head is not None:
            (gitdir / 'HEAD').write_text(head + '\n', encoding='utf-8')
        if loose is not None:
            reference = gitdir / 'refs' / 'heads' / 'main'
            reference.parent.mkdir(parents=True)
            reference.write_text(loose + '\n', encoding='utf-8')
        return self.use(project)

    def test_a_worktree_resolves_a_branch_from_its_own_loose_ref(self):
        self.worktree('local', 'ref: refs/heads/main', local_ref=self.SHA)
        self.assertEqual(app.git_sha_from_gitdir(), self.SHA[:9])

    def test_a_shared_branch_resolves_through_commondir(self):
        # The live case: the worktree owns no ref, the branch is in the common dir.
        self.worktree('shared', 'ref: refs/heads/main', common_ref=self.SHA)
        self.assertEqual(app.git_sha_from_gitdir(), self.SHA[:9])

    def test_a_shared_branch_resolves_from_packed_refs(self):
        packed = ('# pack-refs with: peeled fully-peeled sorted \n'
                  '%s refs/tags/v1\n'
                  '^%s\n'
                  '%s refs/heads/main\n' % (self.OTHER, self.OTHER, self.SHA))
        self.worktree('packed', 'ref: refs/heads/main', packed=packed)
        # The tag and its peel line come first, so a reader that answered with
        # the leading sha of any line would return OTHER here.
        self.assertEqual(app.git_sha_from_gitdir(), self.SHA[:9])

    def test_a_detached_head_is_its_own_revision(self):
        self.worktree('detached', self.SHA)
        self.assertEqual(app.git_sha_from_gitdir(), self.SHA[:9])

    def test_a_relative_gitdir_pointer_is_resolved(self):
        self.worktree('relative', self.SHA, pointer='../common/.git/worktrees/mentat')
        self.assertEqual(app.git_sha_from_gitdir(), self.SHA[:9])

    def test_a_plain_checkout_with_a_git_directory_resolves(self):
        self.checkout('plain', 'ref: refs/heads/main', loose=self.SHA)
        self.assertEqual(app.git_sha_from_gitdir(), self.SHA[:9])

    def test_a_git_file_without_a_gitdir_pointer_answers_nothing(self):
        self.worktree('no-pointer', self.SHA, marker='this is not a gitdir pointer')
        self.assertIsNone(app.git_sha_from_gitdir())

    def test_nothing_is_answered_when_head_cannot_be_followed(self):
        cases = {'no-head': dict(head=None),
                 'not-a-ref': dict(head='HEAD is not a revision'),
                 'unresolvable': dict(head='ref: refs/heads/main'),
                 'escaping-ref': dict(head='ref: refs/heads/../../../../evil', local_ref=self.SHA),
                 'truncated-ref': dict(head='ref: refs/heads/main', common_ref='b9fe34d0')}
        for name, keywords in cases.items():
            with self.subTest(case=name):
                self.worktree(name, **keywords)
                self.assertIsNone(app.git_sha_from_gitdir())

    def test_the_reader_finds_the_checkout_above_the_app_folder(self):
        # PROJECT is <checkout>/mentat, so a reader that looked only at
        # PROJECT/.git would never find this repository at all.
        project = self.worktree('upward', self.SHA)
        self.assertFalse((project / '.git').exists())
        self.assertTrue((project.parent / '.git').is_file())
        self.assertEqual(app.git_sha_from_gitdir(), self.SHA[:9])


if __name__ == "__main__":
    unittest.main()
