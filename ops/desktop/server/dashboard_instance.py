"""Current-user local named-pipe OPEN rendezvous. No bootstrap crosses IPC."""
import ctypes as C
from ctypes import wintypes as W
import hashlib
import threading
import time

from desktop_admin import Refused
from windows_admin_notifier import assert_protected_state, windows_sid

# An unexpectedly slow Windows cancellation must not leave an OVERLAPPED or its
# buffer dangling. Only the completion event permits their eventual release.
_retained_operations = {}
_retained_lock = threading.Lock()


def retain_until_completed(kernel, event, overlapped, buffer):
    token = object()
    resources = (kernel, event, overlapped, buffer)
    with _retained_lock: _retained_operations[token] = resources
    def reap():
        held_kernel, held_event, _overlapped, _buffer = resources
        # A dedicated daemon retains these references even if the client or
        # owner closes its pipe handle. Never reuse this operation's memory.
        if held_kernel.WaitForSingleObject(held_event, 0xffffffff) == 0:
            held_kernel.CloseHandle(held_event)
            with _retained_lock: _retained_operations.pop(token, None)
        # An unexpected invalid event cannot prove completion. The global
        # registry retains the objects until process exit in that case.
    try: threading.Thread(target=reap, daemon=True, name='dashboard-io-completion').start()
    except RuntimeError: pass  # Registry still owns every pending I/O reference.


class ReopenLimit(Refused):
    pass


class Overlapped(C.Structure):
    _fields_=[('internal',C.c_size_t),('internalHigh',C.c_size_t),('offset',W.DWORD),('offsetHigh',W.DWORD),('event',W.HANDLE)]


class SecurityAttributes(C.Structure):
    _fields_=[('length',W.DWORD),('descriptor',C.c_void_p),('inherit',W.BOOL)]


class DashboardInstance:
    def __init__(self, root):
        self.root=assert_protected_state(root)
        self.kernel=C.WinDLL('kernel32',use_last_error=True)
        self.advapi=C.WinDLL('advapi32',use_last_error=True)
        self.stop=threading.Event();self.thread=None;self.pipe=None
        self._apis()
        tag=windows_sid()+'-'+hashlib.sha256(str(self.root).lower().encode()).hexdigest()[:16]
        self.name='\\\\.\\pipe\\MelangeAccessDashboard-'+tag
        self.mutex=self.kernel.CreateMutexW(None,True,'Local\\MelangeAccessDashboard-'+tag)
        if not self.mutex: raise Refused('dashboard ownership unavailable')
        self.owner=C.get_last_error()!=183
        if self.owner:
            descriptor=C.c_void_p()
            if not self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:P(A;;GA;;;SY)(A;;GA;;;'+windows_sid()+')',1,C.byref(descriptor),None):
                self.close();raise Refused('dashboard security unavailable')
            try:
                attributes=SecurityAttributes(C.sizeof(SecurityAttributes),descriptor,False)
                self.pipe=self.kernel.CreateNamedPipeW(self.name,3|0x40000000|0x80000,4|2|8,1,64,64,1000,C.byref(attributes))
                if self.pipe==C.c_void_p(-1).value:
                    self.pipe=None;raise Refused('dashboard pipe unavailable')
            except Exception:
                self.close();raise
            finally:self.kernel.LocalFree(descriptor)

    def _apis(self):
        k=self.kernel
        k.CreateMutexW.argtypes=[C.c_void_p,W.BOOL,W.LPCWSTR];k.CreateMutexW.restype=W.HANDLE
        k.ReleaseMutex.argtypes=[W.HANDLE];k.CloseHandle.argtypes=[W.HANDLE];k.LocalFree.argtypes=[C.c_void_p]
        k.CreateNamedPipeW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD,W.DWORD,W.DWORD,W.DWORD,W.DWORD,C.POINTER(SecurityAttributes)];k.CreateNamedPipeW.restype=W.HANDLE
        k.CreateFileW.argtypes=[W.LPCWSTR,W.DWORD,W.DWORD,C.c_void_p,W.DWORD,W.DWORD,W.HANDLE];k.CreateFileW.restype=W.HANDLE
        k.WaitNamedPipeW.argtypes=[W.LPCWSTR,W.DWORD];k.WaitNamedPipeW.restype=W.BOOL
        k.CreateEventW.argtypes=[C.c_void_p,W.BOOL,W.BOOL,W.LPCWSTR];k.CreateEventW.restype=W.HANDLE
        k.ConnectNamedPipe.argtypes=[W.HANDLE,C.POINTER(Overlapped)];k.ConnectNamedPipe.restype=W.BOOL
        k.DisconnectNamedPipe.argtypes=[W.HANDLE]
        k.ReadFile.argtypes=[W.HANDLE,C.c_void_p,W.DWORD,C.POINTER(W.DWORD),C.POINTER(Overlapped)];k.ReadFile.restype=W.BOOL
        k.WriteFile.argtypes=k.ReadFile.argtypes;k.WriteFile.restype=W.BOOL
        k.GetOverlappedResult.argtypes=[W.HANDLE,C.POINTER(Overlapped),C.POINTER(W.DWORD),W.BOOL];k.GetOverlappedResult.restype=W.BOOL
        k.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD];k.WaitForSingleObject.restype=W.DWORD
        k.CancelIoEx.argtypes=[W.HANDLE,C.POINTER(Overlapped)];k.CancelIoEx.restype=W.BOOL
        self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[W.LPCWSTR,W.DWORD,C.POINTER(C.c_void_p),C.c_void_p];self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype=W.BOOL

    def _operation(self,handle,operation,data=None,deadline=3):
        event=self.kernel.CreateEventW(None,True,False,None)
        if not event:raise Refused('dashboard IPC event unavailable')
        overlapped=Overlapped();overlapped.event=event
        buffer=C.create_string_buffer(data) if data is not None else C.create_string_buffer(33)
        transferred=W.DWORD()
        pending=False
        try:
            if operation=='connect': result=self.kernel.ConnectNamedPipe(handle,C.byref(overlapped))
            elif operation=='write': result=self.kernel.WriteFile(handle,buffer,len(data),C.byref(transferred),C.byref(overlapped))
            else:result=self.kernel.ReadFile(handle,buffer,33,C.byref(transferred),C.byref(overlapped))
            error=C.get_last_error()
            if not result and operation=='connect' and error==535:return b''
            if not result:
                if error!=997:raise Refused('dashboard IPC refused')
                pending=True
                end=time.monotonic()+deadline if deadline is not None else None
                while True:
                    status=self.kernel.WaitForSingleObject(event,100)
                    if status==0:
                        pending=False
                        break
                    if self.stop.is_set() or end is not None and time.monotonic()>=end:
                        raise Refused('dashboard IPC timed out')
                    if status!=258:raise Refused('dashboard IPC unavailable')
                if not self.kernel.GetOverlappedResult(handle,C.byref(overlapped),C.byref(transferred),False):raise Refused('dashboard IPC refused')
            return buffer.raw[:transferred.value] if operation=='read' else b''
        finally:
            close_event=True
            if pending:
                self.kernel.CancelIoEx(handle,C.byref(overlapped))
                if self.kernel.WaitForSingleObject(event,1000)!=0:
                    # Do not reuse the owner pipe after uncertain cancellation.
                    self.stop.set()
                    retain_until_completed(self.kernel,event,overlapped,buffer)
                    close_event=False
            if close_event:self.kernel.CloseHandle(event)

    def request_open(self, command=b'OPEN\n'):
        # The optional bytes argument exists only for protocol tests. Public CLI
        # always sends OPEN; no path, URL, token or request content is accepted.
        if self.owner:raise Refused('owner does not use the reopen client')
        if not self.kernel.WaitNamedPipeW(self.name,3000):raise Refused('dashboard is starting or busy')
        handle=self.kernel.CreateFileW(self.name,0xC0000000,0,None,3,0x40000000,None)
        if handle==C.c_void_p(-1).value:raise Refused('dashboard reopen unavailable')
        try:
            self._operation(handle,'write',command)
            response=self._operation(handle,'read')
            self._operation(handle,'write',b'ACK\n')
            if response==b'LIMIT\n':raise ReopenLimit('End a session in an existing dashboard tab, then reopen Application Access Admin.')
            if response!=b'OK\n':raise Refused('dashboard reopen was refused')
            return True
        finally:self.kernel.CloseHandle(handle)

    def start(self, open_callback):
        if not self.owner or self.thread:raise Refused('dashboard owner required')
        def serve():
            while not self.stop.is_set():
                try:
                    self._operation(self.pipe,'connect',deadline=None)
                    request=self._operation(self.pipe,'read')
                    if request!=b'OPEN\n':response=b'REFUSED\n'
                    else:
                        try:response=b'LIMIT\n' if open_callback() is False else b'OK\n'
                        except Exception:response=b'BUSY\n'
                    self._operation(self.pipe,'write',response)
                    # A bounded ACK ensures DisconnectNamedPipe cannot discard
                    # the response before the launcher has read it.
                    self._operation(self.pipe,'read',deadline=1)
                except Refused:pass
                finally:self.kernel.DisconnectNamedPipe(self.pipe)
        self.thread=threading.Thread(target=serve,daemon=True,name='local-dashboard-reopen');self.thread.start()

    def close(self):
        self.stop.set()
        if self.pipe:self.kernel.CancelIoEx(self.pipe,None)
        if self.thread:self.thread.join(4)
        if self.pipe:self.kernel.CloseHandle(self.pipe);self.pipe=None
        if getattr(self,'mutex',None):
            if self.owner:self.kernel.ReleaseMutex(self.mutex)
            self.kernel.CloseHandle(self.mutex);self.mutex=None
