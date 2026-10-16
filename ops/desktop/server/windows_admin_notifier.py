"""Administrator-only Windows tray. No application window or PowerShell is needed."""
import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from desktop_admin import Store, Refused, atomic_write, canonical
from access_store import AccessStore
from local_notifications import LocalNotifications

PACKAGING = Path(__file__).resolve().parents[1] / 'packaging'
if str(PACKAGING) not in sys.path:
    sys.path.insert(0, str(PACKAGING))
from native_access_state import windows_sid


def _sid_text(pointer):
    advapi=C.WinDLL('advapi32',use_last_error=True); kernel=C.WinDLL('kernel32',use_last_error=True)
    advapi.ConvertSidToStringSidW.argtypes=[C.c_void_p,C.POINTER(W.LPWSTR)]
    kernel.LocalFree.argtypes=[C.c_void_p]
    text=W.LPWSTR()
    if not advapi.ConvertSidToStringSidW(pointer,C.byref(text)): raise Refused('Windows identity unavailable')
    try: return text.value
    finally: kernel.LocalFree(C.cast(text,C.c_void_p))


def assert_protected_state(root):
    """Reject foreign owners, readable DACLs and state redirection; never fix ACLs."""
    if os.name!='nt': raise Refused('Windows administrator session required')
    if C.WinDLL('shell32').IsUserAnAdmin(): raise Refused('ordinary Windows user required')
    original=Path(root)
    if not original.is_absolute() or original.is_symlink() or original.is_junction(): raise Refused('local protected state required')
    root=original.resolve(strict=True)
    allowed={windows_sid(),'S-1-5-18'}
    advapi=C.WinDLL('advapi32',use_last_error=True); kernel=C.WinDLL('kernel32',use_last_error=True)
    advapi.GetNamedSecurityInfoW.argtypes=[W.LPWSTR,W.DWORD,W.DWORD,C.POINTER(C.c_void_p),C.c_void_p,C.POINTER(C.c_void_p),C.c_void_p,C.POINTER(C.c_void_p)]
    advapi.GetNamedSecurityInfoW.restype=W.DWORD
    advapi.GetAclInformation.argtypes=[C.c_void_p,C.c_void_p,W.DWORD,C.c_int]
    advapi.GetAce.argtypes=[C.c_void_p,W.DWORD,C.POINTER(C.c_void_p)]
    kernel.LocalFree.argtypes=[C.c_void_p]
    class Size(C.Structure): _fields_=[('count',W.DWORD),('used',W.DWORD),('free',W.DWORD)]
    for path in (root,root/'access.sqlite3',root/'access-trust.json',root/'keys'):
        if not path.exists() or path.is_symlink() or path.is_junction(): raise Refused('protected state unavailable')
        owner,dacl,descriptor=C.c_void_p(),C.c_void_p(),C.c_void_p()
        if advapi.GetNamedSecurityInfoW(str(path),1,5,C.byref(owner),None,C.byref(dacl),None,C.byref(descriptor)):
            raise Refused('protected state security unavailable')
        try:
            if not owner.value or _sid_text(owner) not in allowed or not dacl.value: raise Refused('protected state owner mismatch')
            info=Size()
            if not advapi.GetAclInformation(dacl,C.byref(info),C.sizeof(info),2) or info.count>128: raise Refused('invalid protected ACL')
            current_access=False
            for index in range(info.count):
                ace=C.c_void_p()
                if not advapi.GetAce(dacl,index,C.byref(ace)): raise Refused('protected ACL unavailable')
                kind=C.c_ubyte.from_address(ace.value).value
                if kind==1: continue  # A deny entry cannot widen access.
                if kind!=0: raise Refused('unsupported protected ACL entry')
                sid=_sid_text(C.c_void_p(ace.value+8))
                if sid not in allowed: raise Refused('protected state permits another identity')
                if sid==windows_sid(): current_access=True
            if not current_access: raise Refused('current user lacks explicit protected access')
        finally: kernel.LocalFree(descriptor)
    return root


class InstanceMutex:
    def __init__(self,root):
        self.kernel=C.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateMutexW.argtypes=[C.c_void_p,W.BOOL,W.LPCWSTR];self.kernel.CreateMutexW.restype=W.HANDLE
        self.kernel.CloseHandle.argtypes=[W.HANDLE]
        self.kernel.ReleaseMutex.argtypes=[W.HANDLE]
        name='Local\\MelangeAccessNotifier-'+windows_sid()+'-'+hashlib.sha256(str(root).lower().encode()).hexdigest()[:16]
        self.handle=self.kernel.CreateMutexW(None,True,name)
        if not self.handle: raise Refused('notifier ownership unavailable')
        self.acquired=C.get_last_error()!=183
    def close(self):
        if self.acquired: self.kernel.ReleaseMutex(self.handle)
        self.kernel.CloseHandle(self.handle)


class Tray:
    def __init__(self,root,smoke_seconds=0):
        self.root=assert_protected_state(root);self.access=AccessStore(Store(self.root))
        self.notifications=LocalNotifications(self.access)
        self.smoke_seconds=smoke_seconds;self.started=time.monotonic();self.last_open=0
        self.stop=threading.Event();self.pending=queue.Queue(maxsize=1);self.results=queue.Queue(maxsize=1)
        self.handoffs=0;self.worker=None;self.hwnd=None;self.icon_added=False
        self.user=C.WinDLL('user32',use_last_error=True);self.shell=C.WinDLL('shell32',use_last_error=True);self.kernel=C.WinDLL('kernel32',use_last_error=True)
        self._configure_api()

    def _configure_api(self):
        self.callback_type=C.WINFUNCTYPE(C.c_ssize_t,W.HWND,W.UINT,W.WPARAM,W.LPARAM)
        class WindowClass(C.Structure):
            _fields_=[('style',W.UINT),('procedure',self.callback_type),('classExtra',C.c_int),('windowExtra',C.c_int),('instance',W.HINSTANCE),('icon',W.HICON),('cursor',W.HANDLE),('background',W.HBRUSH),('menu',W.LPCWSTR),('name',W.LPCWSTR)]
        class GUID(C.Structure): _fields_=[('a',W.DWORD),('b',W.WORD),('c',W.WORD),('d',C.c_byte*8)]
        class Icon(C.Structure):
            _fields_=[('size',W.DWORD),('window',W.HWND),('id',W.UINT),('flags',W.UINT),('callback',W.UINT),('icon',W.HICON),('tip',W.WCHAR*128),('state',W.DWORD),('stateMask',W.DWORD),('info',W.WCHAR*256),('timeout',W.UINT),('title',W.WCHAR*64),('infoFlags',W.DWORD),('guid',GUID),('balloonIcon',W.HICON)]
        self.WindowClass,self.Icon=WindowClass,Icon
        self.user.RegisterClassW.argtypes=[C.POINTER(WindowClass)];self.user.RegisterClassW.restype=W.ATOM
        self.user.CreateWindowExW.argtypes=[W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,C.c_int,C.c_int,C.c_int,C.c_int,W.HWND,W.HMENU,W.HINSTANCE,C.c_void_p];self.user.CreateWindowExW.restype=W.HWND
        self.user.DefWindowProcW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM];self.user.DefWindowProcW.restype=C.c_ssize_t
        self.user.LoadIconW.argtypes=[W.HINSTANCE,C.c_void_p];self.user.LoadIconW.restype=W.HICON
        self.user.SetTimer.argtypes=[W.HWND,C.c_size_t,W.UINT,C.c_void_p];self.user.SetTimer.restype=C.c_size_t
        self.user.KillTimer.argtypes=[W.HWND,C.c_size_t];self.user.DestroyWindow.argtypes=[W.HWND]
        self.user.GetMessageW.argtypes=[C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT];self.user.GetMessageW.restype=C.c_int
        self.user.TranslateMessage.argtypes=[C.POINTER(W.MSG)];self.user.DispatchMessageW.argtypes=[C.POINTER(W.MSG)];self.user.DispatchMessageW.restype=C.c_ssize_t
        self.user.PostQuitMessage.argtypes=[C.c_int];self.user.UnregisterClassW.argtypes=[W.LPCWSTR,W.HINSTANCE]
        self.user.RegisterWindowMessageW.argtypes=[W.LPCWSTR];self.user.RegisterWindowMessageW.restype=W.UINT
        self.kernel.GetModuleHandleW.argtypes=[W.LPCWSTR];self.kernel.GetModuleHandleW.restype=W.HMODULE
        self.shell.Shell_NotifyIconW.argtypes=[W.DWORD,C.POINTER(Icon)];self.shell.Shell_NotifyIconW.restype=W.BOOL

    def _icon(self):
        value=self.Icon();value.size=C.sizeof(value);value.window=self.hwnd;value.id=1
        return value

    def add_icon(self):
        value=self._icon();value.flags=7;value.callback=0x8001;value.icon=self.user.LoadIconW(None,C.c_void_p(32516));value.tip='Application access requests - click to review'
        self.icon_added=bool(self.shell.Shell_NotifyIconW(0,C.byref(value)))
        if self.icon_added:
            value.timeout=4;self.shell.Shell_NotifyIconW(4,C.byref(value))
        return self.icon_added

    def launch_dashboard(self):
        if time.monotonic()-self.last_open<5: return
        assert_protected_state(self.root)
        pythonw=Path(sys.executable).with_name('pythonw.exe')
        if not pythonw.is_file(): raise Refused('verified local Python window launcher missing')
        subprocess.Popen([str(pythonw),str(Path(__file__).with_name('desktop_admin.py')),'--state',str(self.root),'access-dashboard'],
                         stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        self.last_open=time.monotonic()

    def status(self,state):
        atomic_write(self.root/'notifier-status.json',canonical({'schemaVersion':1,'pid':os.getpid(),'state':state,'at':int(time.time()),'handedToWindows':self.handoffs,'seen':False}),secret=True)

    def background(self):
        while not self.stop.is_set():
            try:
                assert_protected_state(self.root)
                value=self.notifications.claim()
                if value['state']=='claimed':
                    value['deadline']=time.monotonic()+10
                    self.pending.put(value,timeout=1)
                    outcome='unknown'
                    while time.monotonic()<value['deadline']:
                        try: delivery,result=self.results.get(timeout=max(.01,value['deadline']-time.monotonic()))
                        except queue.Empty: break
                        if delivery==value['deliveryId']:
                            outcome=result;break
                    self.notifications.acknowledge(value['deliveryId'],outcome)
                self.status('running')
            except Exception:
                # The dashboard remains authoritative; no request details/logs.
                try: self.status('notification-deferred')
                except Exception: pass
            self.stop.wait(30)

    def window_proc(self,window,message,wparam,lparam):
        try:
            if message==self.taskbar_created:
                self.add_icon();return 0
            if message==0x8001 and (lparam & 0xffff) in {0x202,0x203,0x205,0x400,0x401,0x405}:
                self.launch_dashboard();return 0
            if message==0x113:
                if self.smoke_seconds and time.monotonic()-self.started>=self.smoke_seconds:
                    self.user.DestroyWindow(window);return 0
                try: request=self.pending.get_nowait()
                except queue.Empty: return 0
                count=request['requestCount']
                if time.monotonic()>=request['deadline']: outcome='unknown'
                elif type(count) is not int or not 1<=count<=100: outcome='deferred'
                else:
                    value=self._icon();value.flags=0x10;value.title='Application access review';value.info=f'{count} request(s) await your review. Click to open the local dashboard.';value.infoFlags=1;value.timeout=8000
                    outcome='handed_to_windows' if self.icon_added and self.shell.Shell_NotifyIconW(1,C.byref(value)) else 'deferred'
                    if outcome=='handed_to_windows': self.handoffs+=1
                try: self.results.put_nowait((request['deliveryId'],outcome))
                except queue.Full: pass
                return 0
            if message==2:
                self.user.PostQuitMessage(0);return 0
        except Exception:
            return 0
        return self.user.DefWindowProcW(window,message,wparam,lparam)

    def run(self):
        mutex=InstanceMutex(self.root)
        if not mutex.acquired:
            mutex.close();return False
        self.callback=self.callback_type(self.window_proc)
        instance=self.kernel.GetModuleHandleW(None);name='MelangeAccessNotifierWindow'
        window_class=self.WindowClass();window_class.procedure=self.callback;window_class.instance=instance;window_class.name=name
        self.taskbar_created=self.user.RegisterWindowMessageW('TaskbarCreated')
        try:
            if not self.user.RegisterClassW(C.byref(window_class)): raise Refused('native notification window unavailable')
            self.hwnd=self.user.CreateWindowExW(0,name,'Application access',0,0,0,0,0,None,None,instance,None)
            if not self.hwnd or not self.add_icon(): raise Refused('Windows notifications unavailable')
            if not self.user.SetTimer(self.hwnd,1,100,None): raise Refused('native notification timer unavailable')
            self.worker=threading.Thread(target=self.background,daemon=True,name='local-access-notifications');self.worker.start()
            message=W.MSG()
            while True:
                result=self.user.GetMessageW(C.byref(message),None,0,0)
                if result==0: break
                if result<0: raise Refused('Windows message loop unavailable')
                self.user.TranslateMessage(C.byref(message));self.user.DispatchMessageW(C.byref(message))
            return True
        finally:
            self.stop.set()
            if self.worker: self.worker.join(11)
            if self.hwnd:
                self.user.KillTimer(self.hwnd,1)
                self.shell.Shell_NotifyIconW(2,C.byref(self._icon()))
                self.user.DestroyWindow(self.hwnd)
            self.user.UnregisterClassW(name,instance)
            self.status('stopped');mutex.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--smoke-seconds',type=int,default=0)
    options=parser.parse_args()
    if not 0<=options.smoke_seconds<=30: return 2
    try:
        Tray(options.state,options.smoke_seconds).run();return 0
    except Exception:
        if sys.stderr: sys.stderr.write('Local desktop notifier unavailable; use the administrator dashboard.\n')
        return 2


if __name__=='__main__':
    raise SystemExit(main())
