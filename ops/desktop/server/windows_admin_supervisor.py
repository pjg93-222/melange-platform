"""Ordinary-user Windows LAN publisher and local tray supervision.

This does not install or modify firewall rules, network categories, startup entries,
or execution policy. Children are assigned to owned kill-on-close jobs before they
can execute. Loss of the approved LAN stops only this supervisor's publisher.
"""
from __future__ import annotations

import argparse
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from desktop_admin import Refused, atomic_write, canonical
from windows_admin_notifier import assert_protected_state, windows_sid


# Read-only native cmdlets, with values passed as JSON on stdin. This is not a
# replay of a blocked script. Never interpolate user input into this command.
NETWORK_QUERY = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$x=[Console]::In.ReadToEnd() | ConvertFrom-Json
$a=@(Get-NetIPAddress -AddressFamily IPv4 -IPAddress $x.ip -ErrorAction Stop)
$p=@($a | ForEach-Object { Get-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -ErrorAction Stop })
$r=@(Get-NetFirewallRule -Name ('MelangeDesktopUpdates-LAN-'+$x.port) -ErrorAction Stop)
$af=@($r | Get-NetFirewallAddressFilter)
$pf=@($r | Get-NetFirewallPortFilter)
$ap=@($r | Get-NetFirewallApplicationFilter)
$it=@($r | Get-NetFirewallInterfaceFilter)
[ordered]@{
 addresses=@($a | ForEach-Object { [ordered]@{ip=[string]$_.IPAddress;index=[int]$_.InterfaceIndex;state=[string]$_.AddressState} })
 profiles=@($p | ForEach-Object { [ordered]@{name=[string]$_.Name;category=[string]$_.NetworkCategory;index=[int]$_.InterfaceIndex;alias=[string]$_.InterfaceAlias} })
 rules=@($r | ForEach-Object { [ordered]@{enabled=[string]$_.Enabled;direction=[string]$_.Direction;action=[string]$_.Action;profile=[string]$_.Profile} })
 addressesFilter=@($af | ForEach-Object { [ordered]@{local=@($_.LocalAddress);remote=@($_.RemoteAddress)} })
 ports=@($pf | ForEach-Object { [ordered]@{protocol=[string]$_.Protocol;local=@($_.LocalPort)} })
 applications=@($ap | ForEach-Object { [ordered]@{program=[string]$_.Program} })
 interfaces=@($it | ForEach-Object { [ordered]@{aliases=@($_.InterfaceAlias)} })
} | ConvertTo-Json -Depth 6 -Compress
"""
PRIVATE_LANS = tuple(ipaddress.ip_network(value) for value in
                     ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


@dataclass(frozen=True)
class Config:
    state: Path
    python: Path
    pythonw: Path
    listen: str
    network: str
    port: int


def validate_inputs(state, python, listen, network, port):
    try:
        address = ipaddress.IPv4Address(listen)
        if str(address) != listen or not any(address in net for net in PRIVATE_LANS):
            raise ValueError()
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError()
        if not isinstance(network, str) or not 1 <= len(network) <= 256 or any(ord(c) < 32 for c in network):
            raise ValueError()
        original = Path(python)
        if not original.is_absolute() or original.is_symlink() or original.is_junction():
            raise ValueError()
        python = original.resolve(strict=True)
        pythonw = python.with_name('pythonw.exe')
        if python.name.lower() != 'python.exe' or not python.is_file() or not pythonw.is_file() or pythonw.is_symlink():
            raise ValueError()
        root = assert_protected_state(state)
        trust = root / 'trust.json'
        if not trust.is_file() or trust.is_symlink() or trust.is_junction():
            raise ValueError()
        return Config(root, python, pythonw, listen, network, port)
    except (OSError, ValueError) as exc:
        raise Refused('local supervisor configuration unavailable') from exc


def network_permitted(config, result):
    """Validate the exact prior firewall contract; ambiguous adapters fail closed."""
    try:
        keys = {'addresses', 'profiles', 'rules', 'addressesFilter', 'ports', 'applications', 'interfaces'}
        if not isinstance(result, dict) or set(result) != keys:
            return False
        if any(not isinstance(result[key], list) or len(result[key]) != 1 for key in keys):
            return False
        address, profile, rule = (result[key][0] for key in ('addresses', 'profiles', 'rules'))
        af, ports, application, interface = (result[key][0] for key in ('addressesFilter', 'ports', 'applications', 'interfaces'))
        return (
            address == {'ip': config.listen, 'index': profile['index'], 'state': 'Preferred'}
            and type(profile['index']) is int and profile['index'] > 0
            and set(profile) == {'name', 'category', 'index', 'alias'}
            and profile['name'] == config.network and profile['category'] == 'Private'
            and isinstance(profile['alias'], str) and bool(profile['alias'])
            and rule == {'enabled': 'True', 'direction': 'Inbound', 'action': 'Allow', 'profile': 'Private'}
            and af == {'local': [config.listen], 'remote': ['LocalSubnet']}
            and ports == {'protocol': 'TCP', 'local': [str(config.port)]}
            and set(application) == {'program'}
            and os.path.normcase(application['program']) == os.path.normcase(str(config.python))
            and interface == {'aliases': [profile['alias']]}
        )
    except (KeyError, TypeError, ValueError):
        return False


def query_network(config):
    # Use the fixed Windows system executable, never a PATH-discovered shell.
    kernel = C.WinDLL('kernel32', use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [W.LPWSTR, W.UINT]
    buffer = C.create_unicode_buffer(32768)
    size = kernel.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < size < len(buffer):
        raise Refused('Windows network policy unavailable')
    powershell = Path(buffer.value) / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    result = subprocess.run(
        [str(powershell), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', NETWORK_QUERY],
        input=canonical({'ip': config.listen, 'port': config.port}), capture_output=True,
        timeout=20, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
    if len(result.stdout) > 32768 or result.stderr:
        raise Refused('Windows network policy unavailable')
    return network_permitted(config, json.loads(result.stdout.decode('utf-8-sig')))


class SupervisorMutex:
    """Use the previous supervisor's per-SID name, including abandoned recovery."""
    def __init__(self):
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [C.c_void_p, W.BOOL, W.LPCWSTR]
        self.kernel.CreateMutexW.restype = W.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        self.kernel.WaitForSingleObject.restype = W.DWORD
        self.kernel.ReleaseMutex.argtypes = [W.HANDLE]
        self.kernel.CloseHandle.argtypes = [W.HANDLE]
        self.handle = self.kernel.CreateMutexW(None, False, 'Local\\MelangeDesktopPublisher-' + windows_sid())
        if not self.handle:
            raise Refused('publisher ownership unavailable')
        result = self.kernel.WaitForSingleObject(self.handle, 0)
        self.acquired = result in (0, 0x80)
        if result not in (0, 0x80, 0x102):
            self.close()
            raise Refused('publisher ownership unavailable')

    def close(self):
        if self.handle:
            if self.acquired:
                self.kernel.ReleaseMutex(self.handle)
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class OwnedChild:
    """Create suspended, assign to a private job, then resume; no unowned window."""
    def __init__(self, command):
        self.kernel = k = C.WinDLL('kernel32', use_last_error=True)
        self.job = self.process = self.thread = None
        self.pid = None
        class Basic(C.Structure):
            _fields_ = [('processTime', C.c_longlong), ('jobTime', C.c_longlong), ('flags', W.DWORD),
                        ('minWorkingSet', C.c_size_t), ('maxWorkingSet', C.c_size_t), ('activeProcesses', W.DWORD),
                        ('affinity', C.c_size_t), ('priority', W.DWORD), ('scheduling', W.DWORD)]
        class Io(C.Structure):
            _fields_ = [(name, C.c_ulonglong) for name in ('read', 'write', 'other', 'readBytes', 'writeBytes', 'otherBytes')]
        class Limits(C.Structure):
            _fields_ = [('basic', Basic), ('io', Io), ('processMemory', C.c_size_t), ('jobMemory', C.c_size_t),
                        ('peakProcess', C.c_size_t), ('peakJob', C.c_size_t)]
        class Startup(C.Structure):
            _fields_ = [('cb', W.DWORD), ('reserved', W.LPWSTR), ('desktop', W.LPWSTR), ('title', W.LPWSTR),
                        ('x', W.DWORD), ('y', W.DWORD), ('xSize', W.DWORD), ('ySize', W.DWORD),
                        ('xChars', W.DWORD), ('yChars', W.DWORD), ('fill', W.DWORD), ('flags', W.DWORD),
                        ('show', W.WORD), ('reservedSize', W.WORD), ('reservedData', C.c_void_p),
                        ('stdin', W.HANDLE), ('stdout', W.HANDLE), ('stderr', W.HANDLE)]
        class Process(C.Structure):
            _fields_ = [('process', W.HANDLE), ('thread', W.HANDLE), ('pid', W.DWORD), ('tid', W.DWORD)]
        k.CreateJobObjectW.argtypes = [C.c_void_p, W.LPCWSTR]; k.CreateJobObjectW.restype = W.HANDLE
        k.SetInformationJobObject.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD]
        k.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
        k.CreateProcessW.argtypes = [W.LPCWSTR, W.LPWSTR, C.c_void_p, C.c_void_p, W.BOOL, W.DWORD,
                                    C.c_void_p, W.LPCWSTR, C.POINTER(Startup), C.POINTER(Process)]
        k.ResumeThread.argtypes = [W.HANDLE]; k.ResumeThread.restype = W.DWORD
        k.TerminateProcess.argtypes = [W.HANDLE, W.UINT]
        k.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]; k.WaitForSingleObject.restype = W.DWORD
        k.GetExitCodeProcess.argtypes = [W.HANDLE, C.POINTER(W.DWORD)]
        k.CloseHandle.argtypes = [W.HANDLE]
        try:
            self.job = k.CreateJobObjectW(None, None)
            if not self.job: raise C.WinError(C.get_last_error())
            limits = Limits(); limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k.SetInformationJobObject(self.job, 9, C.byref(limits), C.sizeof(limits)):
                raise C.WinError(C.get_last_error())
            startup = Startup(); startup.cb = C.sizeof(startup); startup.flags = 1; startup.show = 0
            info = Process(); commandline = C.create_unicode_buffer(subprocess.list2cmdline([str(x) for x in command]))
            if not k.CreateProcessW(str(command[0]), commandline, None, None, False, 0x08000004,
                                    None, str(Path(command[0]).parent), C.byref(startup), C.byref(info)):
                raise C.WinError(C.get_last_error())
            self.process, self.thread, self.pid = info.process, info.thread, info.pid
            if not k.AssignProcessToJobObject(self.job, self.process):
                raise C.WinError(C.get_last_error())
            if k.ResumeThread(self.thread) == 0xffffffff:
                raise C.WinError(C.get_last_error())
            k.CloseHandle(self.thread); self.thread = None
        except Exception:
            # The process is still suspended if assignment failed. Terminate only
            # the handle created here; never search names or kill another PID.
            if self.process: k.TerminateProcess(self.process, 2)
            self.close()
            raise

    def poll(self):
        if not self.process: return 0
        result = self.kernel.WaitForSingleObject(self.process, 0)
        if result == 0x102: return None
        if result != 0: raise Refused('owned child state unavailable')
        code = W.DWORD()
        if not self.kernel.GetExitCodeProcess(self.process, C.byref(code)):
            raise Refused('owned child state unavailable')
        return code.value

    def close(self):
        if self.job:
            self.kernel.CloseHandle(self.job); self.job = None
        if self.thread:
            self.kernel.CloseHandle(self.thread); self.thread = None
        if self.process:
            self.kernel.WaitForSingleObject(self.process, 5000)
            self.kernel.CloseHandle(self.process); self.process = None


class Supervisor:
    def __init__(self, config, *, check=query_network, spawn=OwnedChild, protect=assert_protected_state):
        self.config, self.check, self.spawn, self.protect = config, check, spawn, protect
        self.publisher = self.notifier = None
        self.stop = threading.Event()

    def write_status(self, state):
        atomic_write(self.config.state / 'listener-status.json', canonical({
            'schemaVersion': 1, 'status': state, 'checkedAt': int(time.time()), 'supervisorPid': os.getpid(),
            'publisherPid': self.publisher.pid if self.publisher else None,
            'notifierPid': self.notifier.pid if self.notifier else None}), secret=True)

    def _retire_exited(self):
        for field in ('publisher', 'notifier'):
            child = getattr(self, field)
            if child and child.poll() is not None:
                child.close(); setattr(self, field, None)

    def tick(self):
        self.protect(self.config.state)
        self._retire_exited()
        directory = Path(__file__).resolve().parent
        if not self.notifier:
            try:
                self.notifier = self.spawn([self.config.pythonw, '-E', '-s', directory / 'windows_admin_notifier.py',
                                            '--state', self.config.state])
            except Exception:
                # A failed tray must not prevent the separately protected LAN service.
                self.notifier = None
        try: network_ready = self.check(self.config)
        except Exception: network_ready = False
        if not network_ready:
            if self.publisher: self.publisher.close(); self.publisher = None
            self.write_status('waiting-for-approved-lan')
        elif not self.publisher:
            try:
                self.publisher = self.spawn([self.config.python, '-E', '-s', directory / 'desktop_admin.py',
                                             '--state', self.config.state, 'serve', '--listen', self.config.listen,
                                             '--port', str(self.config.port)])
                self.write_status('starting')
            except Exception:
                self.publisher = None
                self.write_status('publisher-start-deferred')
        else:
            self.write_status('running' if self.notifier else 'running-notifier-deferred')

    def close(self):
        self.stop.set()
        for field in ('publisher', 'notifier'):
            child = getattr(self, field)
            if child: child.close(); setattr(self, field, None)

    def run(self):
        mutex = SupervisorMutex()
        try:
            if not mutex.acquired: return False
            while not self.stop.is_set():
                self.tick()
                self.stop.wait(30)
            return True
        finally:
            self.close(); mutex.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--listen', required=True)
    parser.add_argument('--network', required=True)
    parser.add_argument('--port', type=int, default=38443)
    options = parser.parse_args()
    try:
        config = validate_inputs(options.state, options.python, options.listen, options.network, options.port)
        Supervisor(config).run()
        return 0
    except Exception:
        if sys.stderr: sys.stderr.write('Administrator LAN supervisor unavailable; existing settings were not changed.\n')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
