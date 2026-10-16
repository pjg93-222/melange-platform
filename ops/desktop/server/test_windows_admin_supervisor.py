"""Bounded policy and owned-process tests; never start the real publisher/tray."""
import copy
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import windows_admin_supervisor as supervisor
from desktop_admin import Refused


def config(root):
    python = Path(sys.executable).resolve()
    return supervisor.Config(Path(root), python, python.with_name('pythonw.exe'), '192.168.45.83', 'Fixture LAN', 38443)


def approved(config):
    return {
        'addresses': [{'ip': config.listen, 'index': 9, 'state': 'Preferred'}],
        'profiles': [{'name': config.network, 'category': 'Private', 'index': 9, 'alias': 'Wi-Fi'}],
        'rules': [{'enabled': 'True', 'direction': 'Inbound', 'action': 'Allow', 'profile': 'Private'}],
        'addressesFilter': [{'local': [config.listen], 'remote': ['LocalSubnet']}],
        'ports': [{'protocol': 'TCP', 'local': [str(config.port)]}],
        'applications': [{'program': str(config.python)}],
        'interfaces': [{'aliases': ['Wi-Fi']}],
    }


class Child:
    def __init__(self, command):
        self.command, self.pid, self.exited, self.closed = command, 1234, False, False
    def poll(self): return 2 if self.exited else None
    def close(self): self.closed = True


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = config(self.temp.name)

    def test_policy_requires_exact_assigned_private_expected_lan_and_firewall(self):
        good = approved(self.config)
        self.assertTrue(supervisor.network_permitted(self.config, good))
        changes = [
            ('addresses', 'ip', '192.168.45.84'), ('addresses', 'state', 'Tentative'),
            ('addresses', 'index', 8), ('profiles', 'name', 'Fixture lan'),
            ('profiles', 'category', 'Public'), ('profiles', 'category', 'DomainAuthenticated'),
            ('rules', 'enabled', 'False'), ('rules', 'direction', 'Outbound'),
            ('rules', 'action', 'Block'), ('rules', 'profile', 'Any'),
            ('addressesFilter', 'local', ['Any']), ('addressesFilter', 'remote', ['Any']),
            ('ports', 'protocol', 'UDP'), ('ports', 'local', ['Any']),
            ('applications', 'program', 'Any'), ('interfaces', 'aliases', ['Any']),
        ]
        for key, field, value in changes:
            with self.subTest(key=key, field=field):
                altered = copy.deepcopy(good); altered[key][0][field] = value
                self.assertFalse(supervisor.network_permitted(self.config, altered))
        for key in good:
            with self.subTest(ambiguous=key):
                altered = copy.deepcopy(good); altered[key].append(copy.deepcopy(altered[key][0]))
                self.assertFalse(supervisor.network_permitted(self.config, altered))
                altered[key] = []
                self.assertFalse(supervisor.network_permitted(self.config, altered))
        self.assertFalse(supervisor.network_permitted(self.config, None))

    def test_lan_loss_stops_only_owned_publisher_and_retains_notifier(self):
        children = []
        def spawn(command):
            child = Child(command); children.append(child); return child
        ready = [False]
        service = supervisor.Supervisor(self.config, check=lambda _: ready[0], spawn=spawn, protect=lambda _: None)
        self.addCleanup(service.close)
        service.tick()
        self.assertEqual(len(children), 1)
        notifier = service.notifier
        self.assertEqual(Path(notifier.command[3]).name, 'windows_admin_notifier.py')
        self.assertEqual(notifier.command[1:3], ['-E', '-s'])
        self.assertIsNone(service.publisher)
        ready[0] = True; service.tick()
        publisher = service.publisher
        self.assertEqual(Path(publisher.command[3]).name, 'desktop_admin.py')
        self.assertIn('serve', publisher.command)
        service.tick(); self.assertEqual(len(children), 2)
        ready[0] = False; service.tick()
        self.assertTrue(publisher.closed)
        self.assertIsNone(service.publisher)
        self.assertIs(service.notifier, notifier)
        self.assertFalse(notifier.closed)
        status = json.loads((self.config.state/'listener-status.json').read_text())
        self.assertEqual(status['status'], 'waiting-for-approved-lan')
        self.assertEqual(set(status), {'schemaVersion', 'status', 'checkedAt', 'supervisorPid', 'publisherPid', 'notifierPid'})
        ready[0] = True; service.tick()
        self.assertEqual(len(children), 3)

    def test_exited_children_restart_once_per_tick_and_policy_errors_fail_closed(self):
        children = []
        def spawn(command):
            child = Child(command); children.append(child); return child
        service = supervisor.Supervisor(self.config, check=lambda _: True, spawn=spawn, protect=lambda _: None)
        self.addCleanup(service.close)
        service.tick(); old_notifier, old_publisher = children
        old_notifier.exited = old_publisher.exited = True
        service.tick()
        self.assertTrue(old_notifier.closed and old_publisher.closed)
        self.assertEqual(len(children), 4)
        service.check = lambda _: (_ for _ in ()).throw(TimeoutError())
        service.tick()
        self.assertTrue(children[3].closed)
        self.assertFalse(children[2].closed)
        self.assertEqual(len(children), 4)

    def test_notifier_failure_does_not_block_listener_but_invalid_state_stops_loop(self):
        def spawn(command):
            if Path(command[3]).name == 'windows_admin_notifier.py': raise OSError('fixture')
            return Child(command)
        service = supervisor.Supervisor(self.config, check=lambda _: True, spawn=spawn, protect=lambda _: None)
        self.addCleanup(service.close)
        service.tick(); self.assertIsNotNone(service.publisher); self.assertIsNone(service.notifier)
        service.tick()
        self.assertEqual(json.loads((self.config.state/'listener-status.json').read_text())['status'], 'running-notifier-deferred')
        service.protect = lambda _: (_ for _ in ()).throw(Refused('fixture'))
        with self.assertRaises(Refused): service.tick()

    def test_network_query_is_fixed_read_only_no_script_or_policy_override(self):
        good = approved(self.config)
        result = subprocess.CompletedProcess([], 0, json.dumps(good).encode(), b'')
        with patch.object(supervisor.subprocess, 'run', return_value=result) as run:
            self.assertTrue(supervisor.query_network(self.config))
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[-1], supervisor.NETWORK_QUERY)
        self.assertNotIn('-File', arguments)
        self.assertNotIn('-EncodedCommand', arguments)
        self.assertNotIn('-ExecutionPolicy', arguments)
        self.assertEqual(run.call_args.kwargs['timeout'], 20)
        self.assertEqual(json.loads(run.call_args.kwargs['input']), {'ip': self.config.listen, 'port': self.config.port})
        for forbidden in ('Set-Net', 'New-Net', 'Remove-Net', 'Start-Process', 'Invoke-Expression'):
            self.assertNotIn(forbidden, supervisor.NETWORK_QUERY)

    def test_input_validation_no_public_loopback_wildcard_or_missing_python(self):
        for address in ('0.0.0.0', '127.0.0.1', '8.8.8.8', '::1', '192.168.1.001'):
            with self.subTest(address=address), self.assertRaises(Refused):
                supervisor.validate_inputs(self.temp.name, self.config.python, address, 'Fixture', 38443)
        with patch.object(supervisor, 'assert_protected_state', return_value=self.config.state):
            (self.config.state/'trust.json').write_text('{}')
            valid = supervisor.validate_inputs(self.temp.name, self.config.python, self.config.listen, 'Fixture', 38443)
            self.assertEqual(valid.python, self.config.python)


@unittest.skipUnless(os.name == 'nt', 'native Windows ownership proof')
class NativeJobTests(unittest.TestCase):
    def test_duplicate_mutex_other_process_refuses_and_releases_after_owner_exit(self):
        sid = 'fixture-' + uuid.uuid4().hex
        source = ("import time,windows_admin_supervisor as m; m.windows_sid=lambda:" + repr(sid) +
                  "; x=m.SupervisorMutex(); print(x.acquired,flush=True); time.sleep(20)")
        child = subprocess.Popen([sys.executable, '-E', '-s', '-c', source], cwd=Path(supervisor.__file__).parent,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'True')
            with patch.object(supervisor, 'windows_sid', return_value=sid):
                second = supervisor.SupervisorMutex()
                try: self.assertFalse(second.acquired)
                finally: second.close()
            child.terminate(); child.wait(timeout=5)
            with patch.object(supervisor, 'windows_sid', return_value=sid):
                recovered = supervisor.SupervisorMutex()
                try: self.assertTrue(recovered.acquired)
                finally: recovered.close()
        finally:
            if child.poll() is None: child.terminate(); child.wait(timeout=5)
            child.stdout.close(); child.stderr.close()

    def test_owned_job_closes_its_parent_and_descendant_only(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory)/'descendant.pid'
            inner = 'import time; time.sleep(60)'
            outer = ('import pathlib,subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c",' + repr(inner) +
                     '],creationflags=subprocess.CREATE_NO_WINDOW); pathlib.Path(' + repr(str(pid_file)) +
                     ').write_text(str(p.pid)); time.sleep(60)')
            child = supervisor.OwnedChild([sys.executable, '-E', '-s', '-c', outer])
            other = supervisor.OwnedChild([sys.executable, '-E', '-s', '-c', inner])
            kernel = C.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]; kernel.OpenProcess.restype = W.HANDLE
            kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]; kernel.WaitForSingleObject.restype = W.DWORD
            kernel.CloseHandle.argtypes = [W.HANDLE]
            descendant = None
            try:
                deadline = time.monotonic()+10
                while not pid_file.exists() and time.monotonic()<deadline: time.sleep(.05)
                self.assertTrue(pid_file.exists())
                descendant = kernel.OpenProcess(0x100000, False, int(pid_file.read_text()))
                self.assertTrue(descendant)
                self.assertIsNone(child.poll()); self.assertIsNone(other.poll())
                child.close()
                self.assertEqual(kernel.WaitForSingleObject(descendant, 5000), 0)
                self.assertIsNone(other.poll())
            finally:
                child.close(); other.close()
                if descendant: kernel.CloseHandle(descendant)


if __name__ == '__main__': unittest.main()
