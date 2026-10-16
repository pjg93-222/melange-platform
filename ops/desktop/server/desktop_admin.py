"""Private desktop update publisher. No remote publishing or command execution API.

Application payloads and signing identities must pass release gates before publishing.
State belongs outside the source checkout. Run `python desktop_admin.py --help`.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import ctypes
import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, ec
from cryptography.x509.oid import NameOID

APPS = {"melange", "terrier", "mentat"}
TARGETS = {("win32", "x64"), ("darwin", "arm64"), ("darwin", "x64")}
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,180}$")
GATES = {"tests", "security", "dependencies", "redistribution", "dataAllowlist"}
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(s) for s in
                         ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "::1/128"))


class Refused(ValueError):
    """A request or release failed a mandatory validation."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def b64(value):
    return base64.b64encode(value).decode("ascii")


def unb64(value):
    return base64.b64decode(value, validate=True)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def app_check(app):
    if app not in APPS:
        raise Refused("unknown application")


def private_address(address):
    try:
        ip = ipaddress.ip_address(address)
        return any(ip in network for network in PRIVATE_NETWORKS if ip.version == network.version)
    except ValueError:
        return False


def https_origin(value, *, allow_public=False):
    """Canonical, explicitly configured origin; never inferred from HTTP headers."""
    if (not isinstance(value, str) or not value or len(value) > 2048 or not value.isascii()
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
            or any(c in value for c in ('\\', '%', '?', '#', '@'))):
        raise Refused('plain HTTPS origin required')
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise Refused('plain HTTPS origin required') from None
    if parsed.scheme != 'https' or not host or parsed.netloc.endswith(':') or parsed.path not in ('', '/') or parsed.username or parsed.password:
        raise Refused('plain HTTPS origin required')
    if port is not None and not 1 <= port <= 65535:
        raise Refused('invalid origin port')
    if allow_public:
        if port is not None and not re.fullmatch(r'[1-9][0-9]{0,4}', parsed.netloc.rsplit(':', 1)[1]):
            raise Refused('canonical origin port required')
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            labels = host.split('.')
            if (len(labels) < 2 or len(host) > 253 or not re.search(r'[a-z]', labels[-1])
                    or re.fullmatch(r'0x[0-9a-f]+', labels[-1])
                    or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)):
                raise Refused('fully qualified ASCII DNS name or canonical IPv4 origin required') from None
        else:
            if address.version != 4 or str(address) != host or address.is_unspecified or address.is_multicast:
                raise Refused('canonical IPv4 origin required')
    elif not private_address(host):
        raise Refused('explicit private HTTPS origin required')
    authority = '[' + host + ']' if ':' in host else host
    if port not in (None, 443):
        authority += ':' + str(port)
    return 'https://' + authority


def request_origin(server):
    configured = getattr(server, 'public_origin', None)
    if configured:
        return configured
    host, port = server.server_address[:2]
    return 'https://' + ('[' + host + ']' if ':' in host else host) + ':' + str(port)


def protect(data: bytes, decrypt=False):
    """Windows user-bound DPAPI; Unix files are restricted to the current owner."""
    if os.name != "nt":
        return data
    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    source_buffer = ctypes.create_string_buffer(data)
    entropy_buffer = ctypes.create_string_buffer(b"MelangeDesktopAdmin/v1")
    source = Blob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    entropy = Blob(len(b"MelangeDesktopAdmin/v1"), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    operation = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not operation(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(output)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def atomic_write(path, data, secret=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        with open(temporary, "xb") as stream:
            if secret and os.name != "nt":
                os.chmod(temporary, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def file_hashes(path):
    sha256, sha512, count = hashlib.sha256(), hashlib.sha512(), 0
    with open(path, "rb") as stream:
        while block := stream.read(1024 * 1024):
            count += len(block)
            sha256.update(block)
            sha512.update(block)
    return {"size": count, "sha256": sha256.hexdigest(), "sha512": b64(sha512.digest())}


def verify_envelope(envelope, public_keys, *, app=None, platform=None, arch=None, minimum_sequence=0):
    try:
        if envelope["schemaVersion"] != 1:
            raise Refused("unsupported signed envelope")
        payload = unb64(envelope["payload"])
        key = ed25519.Ed25519PublicKey.from_public_bytes(unb64(public_keys[envelope["keyId"]]))
        key.verify(unb64(envelope["signature"]), payload)
        manifest = json.loads(payload)
        if canonical(manifest) != payload:
            raise Refused("noncanonical signed payload")
        validate_manifest(manifest)
        for field, expected in (("appId", app), ("platform", platform), ("arch", arch)):
            if expected is not None and manifest[field] != expected:
                raise Refused("wrong " + field)
        if manifest["sequence"] <= minimum_sequence:
            raise Refused("replayed or downgraded release")
        return manifest
    except Refused:
        raise
    except Exception as error:
        raise Refused("invalid release signature or envelope") from error


def validate_manifest(manifest):
    if manifest.get("schemaVersion") != 1:
        raise Refused("unsupported desktop manifest")
    app_check(manifest.get("appId"))
    if (manifest.get("platform"), manifest.get("arch")) not in TARGETS:
        raise Refused("unsupported platform or architecture")
    if not VERSION.fullmatch(str(manifest.get("version", ""))):
        raise Refused("version must be stable major.minor.patch")
    if type(manifest.get("sequence")) is not int or manifest["sequence"] < 1:
        raise Refused("invalid release sequence")
    if manifest.get("minimumUpdaterVersion") != "1.0.0" or manifest.get("dataSchemaVersion") != 1:
        raise Refused("unsupported updater or data schema; migration implementation required")
    package = manifest.get("package", {})
    name = package.get("name", "")
    if not SAFE_NAME.fullmatch(name) or ".." in name:
        raise Refused("unsafe package name")
    expected = f"/v1/packages/{manifest['appId']}/{manifest['sequence']}/{name}"
    if package.get("path") != expected:
        raise Refused("package URL must be same-origin and application-bound")
    if type(package.get("size")) is not int or package["size"] <= 0:
        raise Refused("invalid package size")
    if not re.fullmatch(r"[0-9a-f]{64}", package.get("sha256", "")):
        raise Refused("invalid package hash")
    if len(unb64(package.get("sha512", ""))) != 64:
        raise Refused("invalid package checksum")


class Store:
    def __init__(self, root, *, public_origin=None):
        self.root = Path(root).resolve()
        self.db = self.root / "admin.sqlite3"
        self.config_path = self.root / "trust.json"
        self.public_origin = https_origin(public_origin, allow_public=True) if public_origin is not None else None

    def origin(self, value):
        origin = https_origin(value, allow_public=self.public_origin is not None)
        if self.public_origin is not None and origin != self.public_origin:
            raise Refused('origin must equal the explicitly configured public origin')
        return origin

    @contextlib.contextmanager
    def connect(self):
        if not self.db.is_file():
            raise Refused("publisher not initialized")
        connection = sqlite3.connect(self.db, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def config(self):
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def init(self):
        if self.db.exists() or self.config_path.exists():
            raise Refused("publisher already initialized; existing keys will not be replaced")
        self.root.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            identity = subprocess.check_output(["whoami"], text=True).strip()
            subprocess.run(["icacls", str(self.root), "/inheritance:r", "/grant:r", f"{identity}:(OI)(CI)F", "SYSTEM:(OI)(CI)F"], check=True, capture_output=True)
        else:
            os.chmod(self.root, 0o700)
        keys = {}
        for app in sorted(APPS):
            key = ed25519.Ed25519PrivateKey.generate()
            public = key.public_key().public_bytes_raw()
            key_id = digest(public)[:24]
            atomic_write(self.root / "keys" / f"{app}-{key_id}.key", protect(key.private_bytes_raw()), secret=True)
            keys[app] = {"active": key_id, "publicKeys": {key_id: b64(public)}}
        tls_key = ec.generate_private_key(ec.SECP256R1())
        now = dt.datetime.now(dt.timezone.utc)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Local Desktop Update Publisher")])
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                       .public_key(tls_key.public_key()).serial_number(x509.random_serial_number())
                       .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=365))
                       .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                       .sign(tls_key, hashes.SHA256()))
        atomic_write(self.root / "tls.crt", certificate.public_bytes(serialization.Encoding.PEM))
        pem = tls_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        atomic_write(self.root / "keys" / "tls.key", protect(pem), secret=True)
        spki = tls_key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        atomic_write(self.config_path, canonical({"schemaVersion": 1, "apps": keys, "tlsSpkiSha256": digest(spki),
                                                "windowsPublisher": None, "macReceiptPublicKey": None}))
        connection = sqlite3.connect(self.db)
        connection.executescript("""
            CREATE TABLE pairings (code_hash TEXT PRIMARY KEY, app TEXT NOT NULL, expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE devices (device_id TEXT NOT NULL, app TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
                platform TEXT NOT NULL, arch TEXT NOT NULL, version TEXT NOT NULL, last_seen INTEGER, status TEXT, PRIMARY KEY(device_id,app));
            CREATE TABLE releases (app TEXT NOT NULL, platform TEXT NOT NULL, arch TEXT NOT NULL, sequence INTEGER NOT NULL, version TEXT NOT NULL,
                envelope TEXT NOT NULL, package TEXT NOT NULL, recipients TEXT NOT NULL, withdrawn INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(app,platform,arch,sequence));
            CREATE TABLE audit (time INTEGER NOT NULL, action TEXT NOT NULL, app TEXT, device_id TEXT, detail TEXT);
        """)
        connection.close()

    def sign(self, app, payload):
        config = self.config()["apps"][app]
        key_id = config["active"]
        key_bytes = protect((self.root / "keys" / f"{app}-{key_id}.key").read_bytes(), decrypt=True)
        raw = canonical(payload)
        signature = ed25519.Ed25519PrivateKey.from_private_bytes(key_bytes).sign(raw)
        return {"schemaVersion": 1, "keyId": key_id, "payload": b64(raw), "signature": b64(signature)}

    def audit(self, connection, action, app=None, device=None, detail=None):
        connection.execute("INSERT INTO audit VALUES (?,?,?,?,?)", (int(time.time()), action, app, device, detail))

    def pairing(self, app, url, expires=600):
        app_check(app)
        origin = self.origin(url)
        if not 1 <= expires <= 600:
            raise Refused("pairing lifetime must be at most 600 seconds")
        code = secrets.token_urlsafe(32)
        with self.connect() as connection:
            connection.execute("DELETE FROM pairings WHERE expires < ?", (int(time.time()),))
            connection.execute("INSERT INTO pairings VALUES (?,?,?,0)", (digest(code.encode()), app, int(time.time()) + expires))
            self.audit(connection, "pairing-created", app)
        config = self.config()
        bundle = {"schemaVersion": 1, "url": origin, "appId": app, "code": code,
                  "tlsSpkiSha256": config["tlsSpkiSha256"], "publicKeys": config["apps"][app]["publicKeys"]}
        return base64.urlsafe_b64encode(canonical(bundle)).decode().rstrip("=")

    def enroll(self, request):
        app = request.get("appId")
        app_check(app)
        device = str(uuid.UUID(request["deviceId"]))
        platform, arch = request.get("platform"), request.get("arch")
        if (platform, arch) not in TARGETS or not VERSION.fullmatch(request.get("currentVersion", "")):
            raise Refused("invalid device target or version")
        code = request.get("enrollmentCode", "")
        if not isinstance(code, str) or len(code) > 100:
            raise Refused("invalid enrollment code")
        token = secrets.token_urlsafe(48)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM pairings WHERE code_hash=?", (digest(code.encode()),)).fetchone()
            if not row or row["app"] != app or row["used"] or row["expires"] <= int(time.time()):
                raise Refused("invalid or expired enrollment")
            if connection.execute("SELECT 1 FROM devices WHERE device_id=? AND app=?", (device, app)).fetchone():
                raise Refused("device already enrolled; use a new device identity when re-enrolling")
            connection.execute("UPDATE pairings SET used=1 WHERE code_hash=?", (row["code_hash"],))
            connection.execute("INSERT INTO devices (device_id,app,token_hash,platform,arch,version) VALUES (?,?,?,?,?,?)",
                               (device, app, digest(token.encode()), platform, arch, request["currentVersion"]))
            self.audit(connection, "device-enrolled", app, device)
        return {"deviceToken": token, "deviceId": device, "appId": app}

    def authenticate(self, header, app=None):
        if not header or not header.startswith("Bearer ") or len(header) > 256:
            raise Refused("authentication required")
        token_hash = digest(header[7:].encode())
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM devices WHERE token_hash=? AND revoked=0", (token_hash,)).fetchone()
            if not row or (app is not None and row["app"] != app):
                raise Refused("device not authorized")
            return dict(row)

    def latest(self, device, platform, arch):
        if (platform, arch) != (device["platform"], device["arch"]):
            raise Refused("device platform mismatch")
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM releases WHERE app=? AND platform=? AND arch=? AND withdrawn=0 ORDER BY sequence DESC",
                                      (device["app"], platform, arch)).fetchall()
        for row in rows:
            if device["device_id"] in json.loads(row["recipients"]):
                return dict(row)
        return None

    def package(self, device, sequence, name):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM releases WHERE app=? AND platform=? AND arch=? AND sequence=? AND withdrawn=0",
                                      (device["app"], device["platform"], device["arch"], sequence)).fetchone()
        if not row or device["device_id"] not in json.loads(row["recipients"]):
            raise Refused("package not authorized")
        manifest = verify_envelope(json.loads(row["envelope"]), self.config()["apps"][device["app"]]["publicKeys"])
        if name != manifest["package"]["name"]:
            raise Refused("unknown package")
        path = (self.root / row["package"]).resolve()
        if not path.is_relative_to((self.root / "packages").resolve()) or path.is_symlink():
            raise Refused("unsafe package path")
        return path, manifest["package"]

    def recovery_seed(self, device, platform, arch, version):
        if (platform, arch) != (device['platform'], device['arch']) or not VERSION.fullmatch(str(version)):
            raise Refused('invalid recovery target')
        with self.connect() as connection:
            rows = connection.execute('SELECT * FROM releases WHERE app=? AND platform=? AND arch=? AND version=? AND withdrawn=0 ORDER BY sequence DESC',
                                      (device['app'], platform, arch, version)).fetchall()
        return next((dict(row) for row in rows if device['device_id'] in json.loads(row['recipients'])), None)

    def authorize_existing_release(self, app, sequence, recipients):
        app_check(app)
        if type(sequence) is not int or sequence < 1 or not recipients:
            raise Refused('application, release sequence and recipients are required')
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            for recipient in set(recipients):
                device = connection.execute('SELECT * FROM devices WHERE app=? AND device_id=? AND revoked=0', (app, recipient)).fetchone()
                if not device:
                    raise Refused('recipient is not enrolled and active for this application')
                release = connection.execute('SELECT * FROM releases WHERE app=? AND platform=? AND arch=? AND sequence=? AND withdrawn=0',
                                             (app, device['platform'], device['arch'], sequence)).fetchone()
                if not release:
                    raise Refused('approved active release missing for recipient target')
                allowed = sorted(set(json.loads(release['recipients'])) | {recipient})
                connection.execute('UPDATE releases SET recipients=? WHERE app=? AND platform=? AND arch=? AND sequence=?',
                                   (json.dumps(allowed), app, device['platform'], device['arch'], sequence))
                self.audit(connection, 'existing-release-authorized', app, recipient, str(sequence))

    def status(self, device, request):
        if request.get("appId") != device["app"] or not VERSION.fullmatch(request.get("version", "")):
            raise Refused("invalid status identity")
        status = request.get("status")
        if status not in {"current", "downloaded", "installed", "deferred", "failed", "rolled-back"}:
            raise Refused("invalid update status")
        error = request.get("errorCode", "")
        if not isinstance(error, str) or not re.fullmatch(r"[A-Z0-9_]{0,64}", error):
            raise Refused("status accepts error codes, not logs or user data")
        with self.connect() as connection:
            connection.execute("UPDATE devices SET version=?,status=?,last_seen=? WHERE device_id=? AND app=?",
                               (request["version"], status, int(time.time()), device["device_id"], device["app"]))
            self.audit(connection, "client-status", device["app"], device["device_id"], status + (":" + error if error else ""))

    def revoke(self, app, device):
        app_check(app)
        with self.connect() as connection:
            if connection.execute("UPDATE devices SET revoked=1 WHERE app=? AND device_id=?", (app, device)).rowcount != 1:
                raise Refused("unknown device")
            self.audit(connection, "device-revoked", app, device)

    def rotate_key(self, app):
        app_check(app)
        config = self.config()
        previous = config["apps"][app]
        key = ed25519.Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes_raw()
        key_id = digest(public)[:24]
        transition = {"schemaVersion": 1, "kind": "keyTransition", "appId": app,
                      "sequence": previous.get("keySequence", 0) + 1,
                      "fromKeyId": previous["active"], "toKeyId": key_id, "publicKey": b64(public),
                      "issuedAt": dt.datetime.now(dt.timezone.utc).isoformat()}
        envelope = self.sign(app, transition)
        atomic_write(self.root / "keys" / f"{app}-{key_id}.key", protect(key.private_bytes_raw()), secret=True)
        previous.setdefault("transitions", []).append(envelope)
        previous["keySequence"] = transition["sequence"]
        previous["publicKeys"][key_id] = b64(public)
        previous["active"] = key_id
        atomic_write(self.config_path, canonical(config))
        with self.connect() as connection:
            self.audit(connection, "signing-key-rotated", app, detail=key_id)
        return key_id

    def withdraw(self, app, sequence):
        app_check(app)
        with self.connect() as connection:
            count = connection.execute("UPDATE releases SET withdrawn=1 WHERE app=? AND sequence=?", (app, sequence)).rowcount
            if not count:
                raise Refused("unknown release")
            self.audit(connection, "release-withdrawn", app, detail=str(sequence))

    def verify_release_gates(self, receipt, manifest, package):
        for field in ("appId", "version", "platform", "arch"):
            if receipt.get(field) != manifest[field]:
                raise Refused("release gate receipt target mismatch")
        if receipt.get("schemaVersion") != 1 or receipt.get("sha256") != manifest["package"]["sha256"]:
            raise Refused("release gate receipt does not bind this package")
        if any(receipt.get("gates", {}).get(gate, {}).get("status") != "passed" or
               not receipt["gates"][gate].get("evidence") for gate in GATES):
            raise Refused("all tests, security, dependency, redistribution and data gates must pass")
        if receipt.get("osSignature", {}).get("status") != "passed":
            raise Refused("operating-system signature gate has not passed")
        config = self.config()
        if manifest["platform"] == "win32":
            if os.name != "nt" or not config.get("windowsPublisher"):
                raise Refused("pinned Windows publisher identity is not configured")
            # Pass paths through environment, never interpolate them into shell source.
            env = dict(os.environ, DESKTOP_VERIFY_PACKAGE=str(package))
            command = "$s=Get-AuthenticodeSignature -LiteralPath $env:DESKTOP_VERIFY_PACKAGE; @{Status=[string]$s.Status;Subject=$s.SignerCertificate.Subject}|ConvertTo-Json -Compress"
            result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], env=env,
                                    capture_output=True, text=True, timeout=60, check=True)
            signature = json.loads(result.stdout)
            if signature.get("Status") != "Valid" or signature.get("Subject") != config["windowsPublisher"]:
                raise Refused("package is not signed by the pinned Windows publisher")
        else:
            public = config.get("macReceiptPublicKey")
            attestation = receipt.get("macAttestation")
            if not public or not attestation:
                raise Refused("trusted native macOS signing/notarization attestation required")
            body = {key: value for key, value in receipt.items() if key != "macAttestation"}
            try:
                ed25519.Ed25519PublicKey.from_public_bytes(unb64(public)).verify(unb64(attestation), canonical(body))
            except Exception as error:
                raise Refused("invalid macOS signing/notarization attestation") from error

    def publish(self, app, package, receipt, version, sequence, platform, arch, recipients):
        app_check(app)
        original = Path(package)
        source = original.resolve(strict=True)
        if original.is_symlink() or not source.is_file():
            raise Refused("package must be a regular file")
        extension = ".exe" if platform == "win32" else ".zip"
        if source.suffix.lower() != extension:
            raise Refused("update payload must be a Windows installer or Mac application ZIP")
        manifest = {"schemaVersion": 1, "appId": app, "version": version, "sequence": sequence, "platform": platform, "arch": arch,
                    "issuedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "minimumUpdaterVersion": "1.0.0", "dataSchemaVersion": 1,
                    "package": {"name": source.name, **file_hashes(source), "path": f"/v1/packages/{app}/{sequence}/{source.name}"}}
        validate_manifest(manifest)
        recipients = sorted(set(recipients))
        if not recipients:
            raise Refused("explicit enrolled recipients required")
        destination = self.root / "packages" / app / str(sequence) / platform / arch / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise Refused("immutable package already exists; use a fresh release sequence")
        temporary = destination.with_name("stage-" + secrets.token_hex(12) + "-" + destination.name)
        try:
            with source.open("rb") as origin, temporary.open("xb") as target:
                shutil.copyfileobj(origin, target, 1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            actual = file_hashes(temporary)
            if any(actual[key] != manifest["package"][key] for key in actual):
                raise Refused("package changed while staging")
            # Verify the private staged bytes, never the mutable build output.
            self.verify_release_gates(receipt, manifest, temporary)
            envelope = self.sign(app, manifest)
            with self.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute("SELECT sequence,version FROM releases WHERE app=? AND platform=? AND arch=?", (app, platform, arch)).fetchall()
                if any(sequence <= row["sequence"] or tuple(map(int, version.split('.'))) <= tuple(map(int, row["version"].split('.'))) for row in rows):
                    raise Refused("release sequence and version must advance")
                for recipient in recipients:
                    if not connection.execute("SELECT 1 FROM devices WHERE app=? AND device_id=? AND revoked=0 AND platform=? AND arch=?",
                                              (app, recipient, platform, arch)).fetchone():
                        raise Refused("recipient is unknown, revoked, or targets another platform")
                os.replace(temporary, destination)
                connection.execute("INSERT INTO releases VALUES (?,?,?,?,?,?,?,?,0)",
                                   (app, platform, arch, sequence, version, canonical(envelope).decode(), str(destination.relative_to(self.root)), json.dumps(recipients)))
                self.audit(connection, "release-published", app, detail=f"{version}/{sequence}/{platform}/{arch}")
            return envelope
        finally:
            temporary.unlink(missing_ok=True)

    def tls_context(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        key = protect((self.root / "keys" / "tls.key").read_bytes(), decrypt=True)
        with tempfile.TemporaryDirectory(prefix="tls-", dir=self.root) as directory:
            path = Path(directory) / "key.pem"
            atomic_write(path, key, secret=True)
            context.load_cert_chain(self.root / "tls.crt", path)
        return context


def handler_for(store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DesktopUpdates/1"
        publisher_public_origin = store.public_origin

        def log_message(self, *_):
            pass  # URLs, enrollment codes and credentials never enter stdout logs.

        def setup(self):
            super().setup()
            self.connection.settimeout(20)

        def response(self, status, body=None, content_type="application/json"):
            data = canonical(body) if body is not None and content_type == "application/json" else body or b""
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def preflight(self):
            if not self.server.public_origin and not private_address(self.client_address[0]):
                raise Refused("local network required")
            if self.headers.get_all('Host', []) != [urlsplit(request_origin(self.server)).netloc]:
                raise Refused('configured origin required')
            if self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site"):
                raise Refused("browser requests are not supported")
            if self.headers.get("Transfer-Encoding"):
                raise Refused("chunked requests are not supported")
            if len(self.path) > 2048 or "%" in self.path or "\\" in self.path or ".." in self.path:
                raise Refused("invalid request path")

        def do_POST(self):
            try:
                self.preflight()
                if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    raise Refused("JSON required")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384:
                    raise Refused("invalid request body size")
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict):
                    raise Refused("JSON object required")
                if self.path == "/v1/enroll":
                    self.response(200, store.enroll(request))
                elif self.path == "/v1/status":
                    device = store.authenticate(self.headers.get("Authorization"))
                    store.status(device, request)
                    self.response(200, {"ok": True})
                else:
                    self.response(404, {"error": "NOT_FOUND"})
            except (Refused, ValueError, KeyError, TypeError):
                self.response(403, {"error": "REQUEST_REFUSED"})
            except Exception:
                self.response(500, {"error": "INTERNAL_ERROR"})

        def do_GET(self):
            try:
                self.preflight()
                parsed = urlsplit(self.path)
                parts = parsed.path.strip("/").split("/")
                if len(parts) >= 4 and parts[:2] == ["v1", "apps"]:
                    app = parts[2]
                    app_check(app)
                    device = store.authenticate(self.headers.get("Authorization"), app)
                    if parts[3:] == ["trust"]:
                        self.response(200, {"transitions": store.config()["apps"][app].get("transitions", [])})
                        return
                    query = parse_qs(parsed.query, strict_parsing=True)
                    if any(len(values) != 1 for values in query.values()) or set(query) - {"platform", "arch", "version", "noCache"}:
                        raise Refused("invalid query")
                    if parts[3:] == ['recovery']:
                        if set(query) != {'platform', 'arch', 'version'}:
                            raise Refused('exact recovery target required')
                        release = store.recovery_seed(device, query['platform'][0], query['arch'][0], query['version'][0])
                        self.response(200, json.loads(release['envelope'])) if release else self.response(204)
                        return
                    release = store.latest(device, query.get("platform", [device["platform"]])[0], query.get("arch", [device["arch"]])[0])
                    if not release:
                        self.response(204)
                        return
                    envelope = json.loads(release["envelope"])
                    manifest = verify_envelope(envelope, store.config()["apps"][app]["publicKeys"])
                    if parts[3:] == ["updates"]:
                        if query.get("version", [None])[0] == manifest["version"]:
                            self.response(204)
                        else:
                            self.response(200, envelope)
                    elif parts[3:] in (["feed", "latest.yml"], ["feed", "latest-mac.yml"]):
                        package = manifest["package"]
                        # JSON is a YAML subset. The client must verify the signed envelope first.
                        feed = {"version": manifest["version"], "files": [{"url": package["path"], "sha512": package["sha512"], "size": package["size"]}],
                                "path": package["path"], "sha512": package["sha512"], "releaseDate": manifest["issuedAt"]}
                        self.response(200, canonical(feed), "application/yaml")
                    else:
                        self.response(404, {"error": "NOT_FOUND"})
                elif len(parts) == 5 and parts[:2] == ["v1", "packages"]:
                    app = parts[2]
                    device = store.authenticate(self.headers.get("Authorization"), app)
                    path, metadata = store.package(device, int(parts[3]), parts[4])
                    # Open once and stream that handle; clients independently verify hashes.
                    with path.open("rb") as stream:
                        if os.fstat(stream.fileno()).st_size != metadata["size"]:
                            raise Refused("package size changed")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.send_header("Content-Length", str(metadata["size"]))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        shutil.copyfileobj(stream, self.wfile, 1024 * 1024)
                else:
                    self.response(404, {"error": "NOT_FOUND"})
            except (Refused, ValueError, KeyError, TypeError):
                self.response(403, {"error": "REQUEST_REFUSED"})
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                return
            except Exception:
                self.response(500, {"error": "INTERNAL_ERROR"})
    from access_store import AccessStore
    from access_http import handler_for as access_handler_for
    return access_handler_for(AccessStore(store), Handler)


class BoundedServer(ThreadingHTTPServer):
    """Bounded clients, including TLS handshakes; one stalled peer cannot block acceptance."""
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, handler, tls_context, *, public_origin=None):
        self.slots = threading.BoundedSemaphore(16)
        self.tls_context = tls_context
        self.public_origin = https_origin(public_origin, allow_public=True) if public_origin is not None else None
        if self.public_origin != getattr(handler, 'publisher_public_origin', None):
            raise Refused('listener and publisher origins must match')
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if (not self.public_origin and not private_address(client_address[0])) or not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        wrapped = None
        try:
            request.settimeout(10)
            wrapped = self.tls_context.wrap_socket(request, server_side=True)
            self.finish_request(wrapped, client_address)
        except (ssl.SSLError, OSError, TimeoutError):
            pass
        finally:
            self.shutdown_request(wrapped or request)
            self.slots.release()


def main(argv=None):
    # Avoid packaged-host AppData virtualization and redirected/synced known folders.
    default = Path.home() / "Documents" / "MelangeDesktopAdmin"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=default,
                        help="protected publisher state directory (default: %(default)s)")
    parser.add_argument('--public-origin', help='explicit HTTPS recipient origin; permits public clients only in this mode')
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser('access-init', help='initialize separate protected application-access signing/state')
    sub.add_parser('access-owner', help='explicit local owner mapping; bounded JSON on stdin only')
    access_trust = sub.add_parser('access-trust', help='export public pinned application-access trust only')
    access_trust.add_argument('--url', required=True)
    access_dashboard = sub.add_parser('access-dashboard', help='open local application approval dashboard')
    access_dashboard.add_argument('--port', type=int, default=0)
    access_dashboard.add_argument('--background', action='store_true', help='start the local dashboard without opening a browser tab')
    sub.add_parser('notification-claim', help='local startup notifier: claim generic pending count')
    notification_ack = sub.add_parser('notification-ack', help='local startup notifier: record Windows handoff')
    notification_ack.add_argument('--delivery', required=True)
    notification_ack.add_argument('--outcome', choices=['handed_to_windows','deferred','unknown'], required=True)
    pair = sub.add_parser("pair")
    pair.add_argument("--app", choices=sorted(APPS), required=True)
    pair.add_argument("--url", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--listen", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=38443)
    sub.add_parser("devices")
    revoke = sub.add_parser("revoke")
    revoke.add_argument("--app", choices=sorted(APPS), required=True)
    revoke.add_argument("--device", required=True)
    rotate = sub.add_parser("rotate-key")
    rotate.add_argument("--app", choices=sorted(APPS), required=True)
    withdraw = sub.add_parser("withdraw")
    withdraw.add_argument("--app", choices=sorted(APPS), required=True)
    withdraw.add_argument("--sequence", type=int, required=True)
    authorize = sub.add_parser('authorize-existing-release', help='explicitly approve an existing active release for named enrolled recipients')
    authorize.add_argument('--app', choices=sorted(APPS), required=True)
    authorize.add_argument('--sequence', type=int, required=True)
    authorize.add_argument('--recipient', action='append', required=True)
    publish = sub.add_parser("publish")
    for field in ("app", "package", "receipt", "version", "platform", "arch"):
        publish.add_argument("--" + field, required=True)
    publish.add_argument("--sequence", type=int, required=True)
    publish.add_argument("--recipient", action="append", required=True)
    trust = sub.add_parser("trust-publisher")
    trust.add_argument("--windows-subject")
    trust.add_argument("--mac-receipt-key")
    args = parser.parse_args(argv)
    try:
        store = Store(args.state, public_origin=args.public_origin)
        if args.command == "init":
            store.init()
            print("Publisher initialized. Keys protected. No releases published; no network listener started.")
        elif args.command == 'access-init':
            from access_store import AccessStore
            AccessStore(store).initialize()
            print('Application access initialized. No requests approved and no listener started.')
        elif args.command == 'access-owner':
            from access_store import AccessStore
            from access_http import unique_object
            from owner_issuer import provision
            try:
                raw = sys.stdin.buffer.read(32769)
                if not raw or len(raw) > 32768:
                    raise Refused('bounded owner configuration required')
                value = json.loads(raw, object_pairs_hook=unique_object,
                                   parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                result = provision(AccessStore(store), value)
            except Exception:
                raise Refused('owner provisioning did not complete; preserve existing state and resume the same reviewed configuration') from None
            print(json.dumps(result))
        elif args.command == 'access-trust':
            from access_store import AccessStore
            print(json.dumps(AccessStore(store).descriptor(args.url), indent=2))
        elif args.command == 'access-dashboard':
            import webbrowser
            from access_store import AccessStore
            from admin_dashboard import create_server, SessionCapacityReached
            from dashboard_instance import DashboardInstance, ReopenLimit
            access = AccessStore(store)
            access.config()
            instance = DashboardInstance(store.root)
            try:
                if not instance.owner:
                    try: instance.request_open()
                    except Refused as error:
                        import ctypes
                        text = ('Four protected dashboard sessions are already open. End a session in an existing dashboard tab, then try this shortcut again.'
                                if isinstance(error, ReopenLimit) else 'The local dashboard is starting or busy. Try this shortcut again shortly.')
                        ctypes.windll.user32.MessageBoxW(None, text, 'Application Access Admin', 0x40)
                    return 0
                server, url = create_server(access, args.port)
                def reopen_dashboard():
                    try: fresh_url = server.fresh_bootstrap_url()
                    except SessionCapacityReached: return False
                    webbrowser.open(fresh_url)
                    return True
                instance.start(reopen_dashboard)
                print('Local application approval dashboard opened. No approval keys are logged.', flush=True)
                # Expired sessions must not leave a permanent listener.
                lifetime = threading.Timer(8*3600+60, server.shutdown)
                lifetime.daemon = True
                lifetime.start()
                try:
                    if not args.background: webbrowser.open(url)
                    server.serve_forever()
                finally:
                    lifetime.cancel()
                    server.server_close()
            finally:
                instance.close()
        elif args.command in {'notification-claim', 'notification-ack'}:
            from access_store import AccessStore
            from local_notifications import LocalNotifications
            notifications = LocalNotifications(AccessStore(store))
            result = notifications.claim() if args.command == 'notification-claim' else notifications.acknowledge(args.delivery, args.outcome)
            print(json.dumps(result))
        elif args.command == "pair":
            print(store.pairing(args.app, args.url))
        elif args.command == "devices":
            with store.connect() as connection:
                print(json.dumps([dict(row) for row in connection.execute("SELECT device_id,app,platform,arch,version,revoked,last_seen,status FROM devices")], indent=2))
        elif args.command == "revoke":
            store.revoke(args.app, args.device)
            print("Device update access revoked.")
        elif args.command == "rotate-key":
            print(json.dumps({"appId": args.app, "newKeyId": store.rotate_key(args.app)}))
        elif args.command == "withdraw":
            store.withdraw(args.app, args.sequence)
            print("Release withdrawn; existing installations remain intact.")
        elif args.command == 'authorize-existing-release':
            store.authorize_existing_release(args.app, args.sequence, args.recipient)
            print('Named recipients authorized for the existing active release. No package was changed or installed.')
        elif args.command == "trust-publisher":
            config = store.config()
            if not args.windows_subject and not args.mac_receipt_key:
                raise Refused("provide a verified signing identity")
            if args.windows_subject:
                config["windowsPublisher"] = args.windows_subject
            if args.mac_receipt_key:
                ed25519.Ed25519PublicKey.from_public_bytes(unb64(args.mac_receipt_key))
                config["macReceiptPublicKey"] = args.mac_receipt_key
            atomic_write(store.config_path, canonical(config))
            print("Publisher trust updated locally.")
        elif args.command == "publish":
            receipt = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
            envelope = store.publish(args.app, args.package, receipt, args.version, args.sequence, args.platform, args.arch, args.recipient)
            print(json.dumps({"published": True, "appId": args.app, "version": args.version, "sequence": args.sequence, "keyId": envelope["keyId"]}))
        elif args.command == "serve":
            try:
                listen = ipaddress.ip_address(args.listen)
                allowed_bind = private_address(args.listen) or (store.public_origin and listen.version == 4 and not listen.is_unspecified and not listen.is_multicast)
            except ValueError:
                allowed_bind = False
            if not allowed_bind or not 1024 <= args.port <= 65535:
                raise Refused("bind an explicit permitted IP on an unprivileged port; public IPs require --public-origin and wildcard binding is forbidden")
            server = BoundedServer((args.listen, args.port), handler_for(store), store.tls_context(), public_origin=store.public_origin)
            print(f"Authenticated update service: {request_origin(server)}", flush=True)
            try:
                server.serve_forever()
            finally:
                server.server_close()
        return 0
    except (Refused, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    # Lazy access modules must share this script's exception/helper identities.
    sys.modules.setdefault('desktop_admin', sys.modules[__name__])
    raise SystemExit(main())
