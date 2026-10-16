from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import time
import ctypes as C

from desktop_admin import Store, Refused
from access_store import AccessStore
from dashboard_instance import DashboardInstance, ReopenLimit, _retained_operations
from admin_dashboard import AdminSessions


class DashboardInstanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'state'
        publisher=Store(self.root);publisher.init();AccessStore(publisher).initialize()
        self.owner=DashboardInstance(self.root);self.addCleanup(self.owner.close)

    def test_second_process_sends_only_open_to_existing_owner_without_browser(self):
        called=threading.Event();self.owner.start(called.set)
        script="from dashboard_instance import DashboardInstance; import sys; i=DashboardInstance(sys.argv[1]); assert not i.owner; assert i.request_open(); i.close(); print('opened')"
        result=subprocess.run([sys.executable,'-c',script,str(self.root)],cwd=Path(__file__).parent,capture_output=True,text=True,timeout=8)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'opened')
        self.assertTrue(called.is_set())

    def test_wrong_and_oversized_commands_never_open_and_service_recovers(self):
        calls=[];self.owner.start(lambda:calls.append(True))
        for command in (b'READ\n',b'OPEN path\n',b'x'*4096):
            client=DashboardInstance(self.root)
            try:
                with self.assertRaises(Refused):client.request_open(command)
            finally:client.close()
        self.assertEqual(calls,[])
        client=DashboardInstance(self.root)
        try:self.assertTrue(client.request_open())
        finally:client.close()
        self.assertEqual(calls,[True])

    def test_session_reopen_keeps_prior_approval_session_and_enforces_limit(self):
        clock=[0];sessions=AdminSessions(clock=lambda:clock[0])
        first=sessions.exchange({'bootstrap':sessions.bootstrap})
        for _ in range(3):
            token=sessions.new_bootstrap();sessions.exchange({'bootstrap':token})
        sessions.authenticate('Bearer '+first['bearer'],first['csrf'])
        with self.assertRaises(Refused):sessions.new_bootstrap()
        clock[0]=8*3600+1
        fresh=sessions.new_bootstrap()
        self.assertTrue(sessions.exchange({'bootstrap':fresh})['bearer'])

    def test_stalled_client_cancel_teardown_observes_completion(self):
        calls=[];self.owner.start(lambda:calls.append(True))
        self.assertTrue(self.owner.kernel.WaitNamedPipeW(self.owner.name,3000))
        handle=self.owner.kernel.CreateFileW(self.owner.name,0xC0000000,0,None,3,0x40000000,None)
        self.assertNotEqual(handle,C.c_void_p(-1).value)
        try:
            time.sleep(.15)
            start=time.monotonic();self.owner.close()
            self.assertLess(time.monotonic()-start,2)
            self.assertFalse(self.owner.thread.is_alive())
            self.assertFalse(_retained_operations)
            self.assertEqual(calls,[])
        finally:self.owner.kernel.CloseHandle(handle)

    def test_stalled_read_times_out_then_fixed_open_recovers(self):
        calls=[];self.owner.start(lambda:calls.append(True))
        self.assertTrue(self.owner.kernel.WaitNamedPipeW(self.owner.name,3000))
        handle=self.owner.kernel.CreateFileW(self.owner.name,0xC0000000,0,None,3,0x40000000,None)
        self.assertNotEqual(handle,C.c_void_p(-1).value)
        try: time.sleep(3.3)
        finally:self.owner.kernel.CloseHandle(handle)
        client=DashboardInstance(self.root)
        try:self.assertTrue(client.request_open())
        finally:client.close()
        self.assertEqual(calls,[True]);self.assertFalse(_retained_operations)

    def test_capacity_reply_does_not_spawn_another_server(self):
        self.owner.start(lambda:False)
        client=DashboardInstance(self.root)
        try:
            with self.assertRaises(ReopenLimit):client.request_open()
        finally:client.close()
