"""Read-only process existence and start-time checks; never signal a Windows process."""
import os

# FILETIME counts 100-nanosecond intervals from 1601-01-01; Unix counts seconds from 1970-01-01.
_FILETIME_EPOCH_DELTA=11644473600.0
_FILETIME_TICKS_PER_SECOND=10000000.0


def process_alive(pid):
    try: pid=int(pid)
    except (ValueError,TypeError): return False
    if pid<=0: return False
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        kernel.WaitForSingleObject.restype=wintypes.DWORD
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        kernel.CloseHandle.restype=wintypes.BOOL
        # SYNCHRONIZE permits a zero-time wait and grants no termination right.
        handle=kernel.OpenProcess(0x00100000,False,pid)
        if not handle:
            # Access denied/unknown is conservatively live for writer exclusion.
            return ctypes.get_last_error()!=87
        try: return kernel.WaitForSingleObject(handle,0)!=0
        finally: kernel.CloseHandle(handle)
    try:
        os.kill(pid,0)
        return True
    except PermissionError: return True
    except OSError: return False


def process_start_time(pid):
    """Unix epoch seconds at which THIS pid's process started, or None when unreadable.

    A live pid is not proof that the owner of a lock file is still alive: Windows recycles pids
    aggressively, and a recycled pid wedged .pipeline_run.lock until a human deleted it.  The
    creation time settles it -- two readings of the SAME kernel record must agree, and a
    process that began after the lease was written cannot be the process that wrote it.  None
    means "cannot tell", and every caller must then treat the holder as LIVE, because removing
    a lease on a guess is how two writers meet.
    """
    try: pid=int(pid)
    except (ValueError,TypeError): return None
    if pid<=0: return None
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
        kernel.GetProcessTimes.restype=wintypes.BOOL
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        kernel.CloseHandle.restype=wintypes.BOOL
        # PROCESS_QUERY_LIMITED_INFORMATION (0x1000) is readable across integrity levels and
        # grants no termination right; fall back to PROCESS_QUERY_INFORMATION for old hosts.
        handle=kernel.OpenProcess(0x1000,False,pid) or kernel.OpenProcess(0x0400,False,pid)
        if not handle: return None
        try:
            created,exited,kernel_time,user_time=(wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle,ctypes.byref(created),ctypes.byref(exited),
                                          ctypes.byref(kernel_time),ctypes.byref(user_time)):
                return None
            ticks=(created.dwHighDateTime<<32)|created.dwLowDateTime
            if ticks<=0: return None
            return ticks/_FILETIME_TICKS_PER_SECOND-_FILETIME_EPOCH_DELTA
        finally: kernel.CloseHandle(handle)
    # POSIX: /proc/<pid>/stat field 22 counts clock ticks since boot, and /proc/stat's btime is
    # the boot instant.  UNTESTED on this box (Windows only); anything missing returns None.
    try:
        with open('/proc/%d/stat'%pid,encoding='utf-8',errors='replace') as handle:
            fields=handle.read().rsplit(')',1)[1].split()   # comm may contain spaces and ')'
        ticks_since_boot=float(fields[19])                  # field 22, 1-based, after comm
        with open('/proc/stat',encoding='utf-8',errors='replace') as handle:
            boot=next(float(line.split()[1]) for line in handle if line.startswith('btime '))
        hertz=float(os.sysconf('SC_CLK_TCK'))
        if hertz<=0: return None
        return boot+ticks_since_boot/hertz
    except (OSError,IndexError,ValueError,StopIteration,AttributeError):
        return None
