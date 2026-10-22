"""Launch the three installed desktop apps through the protected local dashboard."""
import ctypes
import os
from pathlib import Path
import stat
import subprocess

from access_store import fields
from apps_admin import AppsError

APPS = {'melange': 'Melange', 'mentat': 'Mentat', 'terrier': 'Terrier'}
ROUTES = {'/desktop-apps', '/desktop-apps/open'}


class DesktopUnavailable(AppsError):
    status, code = 503, 'desktop_app_unavailable'


def file_version(path):
    """Read Windows VERSIONINFO without executing the app or a shell."""
    if os.name != 'nt':
        return None
    from ctypes import wintypes
    version = ctypes.WinDLL('version', use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version.GetFileVersionInfoW.restype = wintypes.BOOL
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    version.VerQueryValueW.restype = wintypes.BOOL
    unused = wintypes.DWORD()
    size = version.GetFileVersionInfoSizeW(str(path), ctypes.byref(unused))
    if not 0 < size <= 1024*1024:
        return None
    data = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(path), 0, size, data):
        return None
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not version.VerQueryValueW(data, '\\', ctypes.byref(pointer), ctypes.byref(length)) or length.value < 52:
        return None
    values = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))
    if values[0] != 0xFEEF04BD:
        return None
    return '.'.join(map(str, (values[2] >> 16, values[2] & 0xffff, values[3] >> 16)))


class DesktopApps:
    def __init__(self, root=None, launch=None, version=None):
        self.root = Path(root) if root is not None else Path.home() / 'Documents' / 'MelangeApplications'
        self.launch = launch or subprocess.Popen
        self.version = version or file_version

    def executable(self, app):
        if not isinstance(app, str) or app not in APPS:
            raise ValueError('Unknown desktop application')
        name = APPS[app]
        path = self.root / name / (name + '.exe')
        # Never follow a substituted shortcut, junction or symlink into another program.
        for item in (path, *path.parents):
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise OSError('The desktop application path contains a link or junction')
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError('The desktop application is not a regular installed file')
        return path

    def handle(self, route, value):
        if route == '/desktop-apps':
            fields(value, set())
            rows = []
            for app, name in APPS.items():
                try:
                    path = self.executable(app)
                    rows.append({'appId': app, 'name': name, 'installed': True,
                                 'version': self.version(path), 'path': str(path)})
                except OSError:
                    rows.append({'appId': app, 'name': name, 'installed': False, 'version': None, 'path': None})
            return {'applications': rows}
        if route != '/desktop-apps/open':
            raise ValueError('Unknown desktop application action')
        fields(value, {'appId'})
        try:
            path = self.executable(value['appId'])
            self.launch([str(path)], shell=False, cwd=str(path.parent), stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as error:
            raise DesktopUnavailable('The installed desktop app could not be opened. Check its installation and Windows notification.') from error
        return {'ok': True, 'appId': value['appId'], 'path': str(path)}
