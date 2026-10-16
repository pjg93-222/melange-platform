"""Device-token storage through Windows Credential Manager / macOS Keychain.

The helper consumes one bounded JSON request on stdin and emits one JSON reply.
Tokens are never command-line arguments, files, or diagnostic messages. The Mac
backend uses Security.framework SecItem APIs with a non-synchronizing,
device-only data-protection keychain item; native signing/entitlements must allow
the packaged helper to access its own keychain. There is no plaintext fallback.

API references:
https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credwritew
https://developer.apple.com/documentation/security/keychain_services
"""
from __future__ import annotations

import ctypes
import json
import re
import sys
import uuid

APPS = frozenset({"melange", "terrier", "mentat"})
PREFIX = "MelangeSystems.Desktop.Update"
SCOPES = {'update': PREFIX, 'access-token': 'MelangeSystems.Desktop.Access.Token',
          'access-password': 'MelangeSystems.Desktop.Access.Password',
          'access-request': 'MelangeSystems.Desktop.Access.Request'}
MAX_TOKEN_BYTES = 2048  # Below Windows CRED_MAX_CREDENTIAL_BLOB_SIZE (2560).
MAX_REQUEST_BYTES = 8192
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class CredentialError(RuntimeError):
    """Errors intentionally contain no credential data or native diagnostics."""


def _identity(app_id, device_id, scope='update'):
    if not isinstance(app_id, str) or app_id not in APPS:
        raise CredentialError("Invalid credential identity")
    if not isinstance(device_id, str) or not UUID_PATTERN.fullmatch(device_id):
        raise CredentialError("Invalid credential identity")
    parsed = uuid.UUID(device_id)
    if str(parsed) != device_id or parsed.int == 0:
        raise CredentialError("Invalid credential identity")
    if not isinstance(scope, str) or scope not in SCOPES:
        raise CredentialError("Invalid credential scope")
    return f"{SCOPES[scope]}.{app_id}", device_id


def _token_bytes(token):
    if not isinstance(token, str) or not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise CredentialError("Invalid credential token")
    encoded = token.encode("ascii")
    if len(encoded) > MAX_TOKEN_BYTES:
        raise CredentialError("Invalid credential token")
    return encoded


class _Windows:
    def __init__(self):
        from ctypes import wintypes as w

        class Credential(ctypes.Structure):
            _fields_ = [("Flags", w.DWORD), ("Type", w.DWORD),
                        ("TargetName", w.LPWSTR), ("Comment", w.LPWSTR),
                        ("LastWritten", w.FILETIME), ("CredentialBlobSize", w.DWORD),
                        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                        ("Persist", w.DWORD), ("AttributeCount", w.DWORD),
                        ("Attributes", ctypes.c_void_p), ("TargetAlias", w.LPWSTR),
                        ("UserName", w.LPWSTR)]

        self.Credential = Credential
        self.api = ctypes.WinDLL("advapi32.dll", use_last_error=True)
        self.api.CredWriteW.argtypes = [ctypes.POINTER(Credential), w.DWORD]
        self.api.CredWriteW.restype = w.BOOL
        self.api.CredReadW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.POINTER(Credential))]
        self.api.CredReadW.restype = w.BOOL
        self.api.CredDeleteW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD]
        self.api.CredDeleteW.restype = w.BOOL
        self.api.CredFree.argtypes = [ctypes.c_void_p]
        self.api.CredFree.restype = None

    def read(self, service, account):
        pointer = ctypes.POINTER(self.Credential)()
        if not self.api.CredReadW(service + "/" + account, 1, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == 1168:  # ERROR_NOT_FOUND
                return None
            raise CredentialError("Credential store unavailable")
        try:
            item = pointer.contents
            if item.Type != 1 or item.UserName != account or not 0 < item.CredentialBlobSize <= MAX_TOKEN_BYTES:
                raise CredentialError("Invalid stored credential")
            raw = ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize)
            token = raw.decode("ascii")
            _token_bytes(token)
            return token
        finally:
            if pointer:
                if pointer.contents.CredentialBlob and pointer.contents.CredentialBlobSize <= MAX_TOKEN_BYTES:
                    ctypes.memset(pointer.contents.CredentialBlob, 0, pointer.contents.CredentialBlobSize)
                self.api.CredFree(pointer)

    def write(self, service, account, token):
        data = _token_bytes(token)
        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        item = self.Credential()
        item.Type = 1  # CRED_TYPE_GENERIC: opaque application bytes.
        item.TargetName = service + "/" + account
        item.UserName = account
        item.CredentialBlobSize = len(data)
        item.CredentialBlob = buffer
        item.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE, current user's vault only.
        try:
            if not self.api.CredWriteW(ctypes.byref(item), 0):
                raise CredentialError("Credential store unavailable")
        finally:
            ctypes.memset(buffer, 0, len(buffer))

    def delete(self, service, account):
        if not self.api.CredDeleteW(service + "/" + account, 1, 0) and ctypes.get_last_error() != 1168:
            raise CredentialError("Credential store unavailable")


class _Mac:
    """Modern SecItem APIs; constants and CF objects never leave this process."""
    def __init__(self):
        p = ctypes.c_void_p
        self.cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.sec = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        self.cf.CFStringCreateWithBytes.argtypes = [p, p, ctypes.c_ssize_t, ctypes.c_uint32, ctypes.c_bool]
        self.cf.CFStringCreateWithBytes.restype = p
        self.cf.CFDataCreate.argtypes = [p, p, ctypes.c_ssize_t]
        self.cf.CFDataCreate.restype = p
        self.cf.CFDictionaryCreate.argtypes = [p, ctypes.POINTER(p), ctypes.POINTER(p), ctypes.c_ssize_t, p, p]
        self.cf.CFDictionaryCreate.restype = p
        self.cf.CFRelease.argtypes = [p]
        self.cf.CFRelease.restype = None
        self.cf.CFGetTypeID.argtypes = [p]
        self.cf.CFGetTypeID.restype = ctypes.c_ulong
        self.cf.CFDataGetTypeID.argtypes = []
        self.cf.CFDataGetTypeID.restype = ctypes.c_ulong
        self.cf.CFDataGetLength.argtypes = [p]
        self.cf.CFDataGetLength.restype = ctypes.c_ssize_t
        self.cf.CFDataGetBytePtr.argtypes = [p]
        self.cf.CFDataGetBytePtr.restype = p
        for name, args in {"SecItemCopyMatching": [p, ctypes.POINTER(p)], "SecItemAdd": [p, ctypes.POINTER(p)],
                           "SecItemUpdate": [p, p], "SecItemDelete": [p]}.items():
            function = getattr(self.sec, name)
            function.argtypes = args
            function.restype = ctypes.c_int32
        self.key_callbacks = ctypes.addressof(ctypes.c_byte.in_dll(self.cf, "kCFTypeDictionaryKeyCallBacks"))
        self.value_callbacks = ctypes.addressof(ctypes.c_byte.in_dll(self.cf, "kCFTypeDictionaryValueCallBacks"))

    def constant(self, name):
        library = self.cf if name.startswith("kCF") else self.sec
        return ctypes.c_void_p.in_dll(library, name).value

    def dictionary(self, values):
        """Return an owning CFDictionary; its callbacks retain temporary values."""
        keys, refs, owned = [], [], []
        try:
            for name, value in values.items():
                keys.append(self.constant(name))
                if isinstance(value, bool):
                    ref = self.constant("kCFBooleanTrue" if value else "kCFBooleanFalse")
                elif isinstance(value, tuple):
                    ref = self.constant(value[0])
                else:
                    data = value.encode("utf-8") if isinstance(value, str) else value
                    buffer = ctypes.create_string_buffer(data)
                    ref = (self.cf.CFStringCreateWithBytes(None, buffer, len(data), 0x08000100, False)
                           if isinstance(value, str) else self.cf.CFDataCreate(None, buffer, len(data)))
                    ctypes.memset(buffer, 0, len(buffer))
                    if not ref:
                        raise CredentialError("Credential store unavailable")
                    owned.append(ref)
                refs.append(ref)
            array = ctypes.c_void_p * len(keys)
            result = self.cf.CFDictionaryCreate(None, array(*keys), array(*refs), len(keys), self.key_callbacks, self.value_callbacks)
            if not result:
                raise CredentialError("Credential store unavailable")
            return result
        finally:
            for ref in owned:
                self.cf.CFRelease(ref)

    @staticmethod
    def query(service, account):
        return {"kSecClass": ("kSecClassGenericPassword",), "kSecAttrService": service,
                "kSecAttrAccount": account, "kSecUseDataProtectionKeychain": True,
                "kSecAttrSynchronizable": False}

    def read(self, service, account):
        values = self.query(service, account)
        values.update(kSecMatchLimit=("kSecMatchLimitOne",), kSecReturnData=True)
        query, result = self.dictionary(values), ctypes.c_void_p()
        try:
            status = self.sec.SecItemCopyMatching(query, ctypes.byref(result))
            if status == -25300:  # errSecItemNotFound
                return None
            if status or not result.value:
                raise CredentialError("Credential store unavailable")
            if self.cf.CFGetTypeID(result) != self.cf.CFDataGetTypeID():
                raise CredentialError("Invalid stored credential")
            length = self.cf.CFDataGetLength(result)
            if not 0 < length <= MAX_TOKEN_BYTES:
                raise CredentialError("Invalid stored credential")
            token = ctypes.string_at(self.cf.CFDataGetBytePtr(result), length).decode("ascii")
            _token_bytes(token)
            return token
        finally:
            if result.value:
                self.cf.CFRelease(result)
            self.cf.CFRelease(query)

    def write(self, service, account, token):
        data = _token_bytes(token)
        query = self.dictionary(self.query(service, account))
        update = None
        try:
            update = self.dictionary({"kSecValueData": data})
            status = self.sec.SecItemUpdate(query, update)
            if status == -25300:
                values = self.query(service, account)
                values.update(kSecValueData=data, kSecAttrAccessible=("kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly",))
                add = self.dictionary(values)
                try:
                    status = self.sec.SecItemAdd(add, None)
                finally:
                    self.cf.CFRelease(add)
                if status == -25299:  # Concurrent creation: update the exact item.
                    status = self.sec.SecItemUpdate(query, update)
            if status:
                raise CredentialError("Credential store unavailable")
        finally:
            if update:
                self.cf.CFRelease(update)
            self.cf.CFRelease(query)

    def delete(self, service, account):
        query = self.dictionary(self.query(service, account))
        try:
            if self.sec.SecItemDelete(query) not in (0, -25300):
                raise CredentialError("Credential store unavailable")
        finally:
            self.cf.CFRelease(query)


def _backend():
    if sys.platform == "win32":
        return _Windows()
    if sys.platform == "darwin":
        return _Mac()
    raise CredentialError("Native credential storage is unavailable")


def read(app_id, device_id, scope='update'):
    """Return this app/device's token, or None if absent. Never enumerate."""
    return _backend().read(*_identity(app_id, device_id, scope))


def write(app_id, device_id, token, scope='update'):
    identity = _identity(app_id, device_id, scope)
    _token_bytes(token)
    _backend().write(*identity, token)


def delete(app_id, device_id, scope='update'):
    _backend().delete(*_identity(app_id, device_id, scope))


def main():
    """Bridge contract: stdin JSON only, generic failure, no token diagnostics."""
    try:
        data = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if not data or len(data) > MAX_REQUEST_BYTES:
            raise CredentialError("Invalid request")
        request = json.loads(data)
        if not isinstance(request, dict):
            raise CredentialError("Invalid request")
        action = request.get("action")
        expected = {"action", "appId", "deviceId"} | ({"token"} if action == "write" else set())
        if 'scope' in request:
            expected.add('scope')
        if action not in {"read", "write", "delete"} or set(request) != expected:
            raise CredentialError("Invalid request")
        app_id, device_id = request["appId"], request["deviceId"]
        scope = request.get('scope', 'update')
        if action == "read":
            response = {"token": read(app_id, device_id, scope)}
        elif action == "write":
            write(app_id, device_id, request["token"], scope)
            response = {"ok": True}
        else:
            delete(app_id, device_id, scope)
            response = {"ok": True}
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        return 0
    except Exception:
        sys.stderr.write("Native credential request failed.\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
