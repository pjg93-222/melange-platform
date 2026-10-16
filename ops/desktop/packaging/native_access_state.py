"""Windows user DPAPI records for desktop access; stdin/stdout is private IPC.

Only trusted main/local administrator code supplies the profile. Fixed filenames
and profile/kind-bound entropy prevent a protected file from becoming a generic
file interface or being copied into a different installation profile.
"""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys

KINDS = frozenset({'identity', 'trust', 'state', 'owner', 'activity', 'dataset-journal'})
MAX_VALUE = 8 * 1024 * 1024
MAX_REQUEST = 12 * 1024 * 1024
MAGIC = b'MDA1\0'


class StateError(ValueError):
    pass


def canonical_path(value):
    if os.name != 'nt' or not isinstance(value, (str, Path)):
        raise StateError('Windows local path required')
    candidate = Path(value)
    if not candidate.is_absolute():
        raise StateError('Absolute local profile required')
    resolved = candidate.resolve(strict=True)
    if not resolved.is_dir() or len(resolved.drive) != 2 or resolved.drive[1] != ':':
        raise StateError('Existing local directory required')
    text = str(resolved).replace('\\', '/').lower()
    return text.rstrip('/') if len(text) > 3 else text


def path_hash(value):
    return hashlib.sha256(('win32:' + canonical_path(value)).encode('utf-8')).hexdigest()


def windows_sid():
    if os.name != 'nt':
        raise StateError('Windows user identity required')
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    token, length = wintypes.HANDLE(), wintypes.DWORD()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise StateError('Windows user identity unavailable')
    try:
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
        if not 0 < length.value < 65536:
            raise StateError('Invalid Windows user identity')
        data = ctypes.create_string_buffer(length.value)
        if not advapi.GetTokenInformation(token, 1, data, length, ctypes.byref(length)):
            raise StateError('Windows user identity unavailable')
        sid_pointer = ctypes.cast(data, ctypes.POINTER(ctypes.c_void_p))[0]
        text = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid_pointer, ctypes.byref(text)):
            raise StateError('Windows user identity unavailable')
        try:
            return text.value
        finally:
            kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        kernel.CloseHandle(token)


def _crypt(data, entropy_data, decrypt=False):
    if os.name != 'nt':
        raise StateError('Windows protected storage required')

    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    source_buffer, entropy_buffer = ctypes.create_string_buffer(data), ctypes.create_string_buffer(entropy_data)
    source = Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy = Blob(len(entropy_data), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    if not operation(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(output)):
        raise StateError('Protected access storage unavailable')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def _location(profile, kind):
    if kind not in KINDS:
        raise StateError('Unsupported protected access record')
    canonical_path(profile)
    root = Path(profile).resolve(strict=True)
    target = root / ('access-' + kind + '.dpapi')
    if target.is_symlink() or target.exists() and (not target.is_file() or target.resolve().parent != root):
        raise StateError('Unsafe protected access record path')
    entropy = ('MelangeDesktopAccess/v1|' + path_hash(root) + '|' + kind).encode('ascii')
    return target, entropy


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise StateError('Duplicate protected field')
        value[key] = item
    return value


def read(profile, kind):
    target, entropy = _location(profile, kind)
    if not target.exists():
        return None
    if target.stat().st_size > MAX_VALUE + 65536:
        raise StateError('Protected access record too large')
    data = target.read_bytes()
    if not data.startswith(MAGIC):
        raise StateError('Unsupported protected access format')
    plain = _crypt(data[len(MAGIC):], entropy, decrypt=True)
    if len(plain) > MAX_VALUE:
        raise StateError('Protected access record too large')
    return json.loads(plain, object_pairs_hook=_unique, parse_constant=lambda _: (_ for _ in ()).throw(StateError('Invalid number')))


def write(profile, kind, value):
    target, entropy = _location(profile, kind)
    plain = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('utf-8')
    if len(plain) > MAX_VALUE:
        raise StateError('Protected access record too large')
    encrypted = MAGIC + _crypt(plain, entropy)
    temporary = target.with_name(target.name + '.' + secrets.token_hex(12) + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def describe(profile):
    return {'windowsSid': windows_sid(), 'profileHash': path_hash(profile)}


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if not raw or len(raw) > MAX_REQUEST:
            raise StateError('Invalid protected access request')
        request = json.loads(raw, object_pairs_hook=_unique)
        if not isinstance(request, dict):
            raise StateError('Invalid protected access request')
        action = request.get('action')
        fields = {'action', 'profile'} | ({'kind'} if action in {'read', 'write'} else set()) | ({'value'} if action == 'write' else set())
        if action not in {'read', 'write', 'describe'} or set(request) != fields:
            raise StateError('Invalid protected access request')
        if action == 'describe':
            result = describe(request['profile'])
        elif action == 'read':
            result = {'value': read(request['profile'], request['kind'])}
        else:
            write(request['profile'], request['kind'], request['value'])
            result = {'ok': True}
        sys.stdout.write(json.dumps(result, separators=(',', ':'), allow_nan=False) + '\n')
        return 0
    except Exception:
        sys.stderr.write('Protected access storage request failed.\n')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
