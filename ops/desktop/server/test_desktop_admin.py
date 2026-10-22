import base64
import concurrent.futures
import http.client
import json
from pathlib import Path
import ssl
import threading
import time
from unittest import mock
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from desktop_admin import (APPS, BoundedServer, Refused, Store, b64, canonical,
                           digest, file_hashes, handler_for, invite_env, main, open_dashboard_window,
                           private_address, protect, unb64, verify_envelope)


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "publisher")
    result.init()
    return result


def pairing(store, app="melange", device=None):
    encoded = store.pairing(app, "https://127.0.0.1:38443")
    bundle = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    request = {"enrollmentCode": bundle["code"], "appId": app,
               "deviceId": device or str(uuid.uuid4()), "platform": "win32", "arch": "x64", "currentVersion": "1.0.0"}
    return bundle, request


def enrolled(store, app="melange"):
    bundle, request = pairing(store, app)
    result = store.enroll(request)
    return result, store.authenticate("Bearer " + result["deviceToken"], app)


def publish_fixture(store, monkeypatch, tmp_path, devices, app="melange", sequence=1, version="1.0.1"):
    # A fixture tests transactional distribution independently from OS signing.
    # Production CLI has no option to bypass its native signing gate.
    monkeypatch.setattr(store, "verify_release_gates", lambda *_: None)
    package = tmp_path / f"{app}-{version}.exe"
    package.write_bytes(b"test-package-not-an-installable-program" * 100)
    return store.publish(app, package, {}, version, sequence, "win32", "x64", [d["deviceId"] for d in devices])


def test_init_does_not_replace_keys(store):
    before = store.config()
    with pytest.raises(Refused, match="already initialized"):
        store.init()
    assert store.config() == before
    assert set(before["apps"]) == APPS


def test_dpapi_roundtrip():
    secret = b"a unique local test secret"
    assert protect(protect(secret), decrypt=True) == secret


def test_cli_default_state_ignores_packaged_host_localappdata(tmp_path, monkeypatch):
    import desktop_admin
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'virtualized-appdata'))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'profile')
    observed = []
    class StateProbe:
        def __init__(self, root, *, public_origin=None):
            assert public_origin is None
            observed.append(root)
        def init(self):
            pass
    monkeypatch.setattr(desktop_admin, 'Store', StateProbe)
    assert desktop_admin.main(['init']) == 0
    assert observed == [tmp_path / 'profile' / 'Documents' / 'MelangeDesktopAdmin']


def test_enrollment_single_use_and_application_bound(store):
    _, request = pairing(store)
    with pytest.raises(Refused):
        store.enroll(dict(request, appId="terrier"))
    result = store.enroll(request)
    with pytest.raises(Refused):
        store.enroll(dict(request, deviceId=str(uuid.uuid4())))
    with pytest.raises(Refused):
        store.authenticate("Bearer " + result["deviceToken"], "terrier")


def test_concurrent_enrollment_only_one_wins(store):
    _, request = pairing(store)
    def attempt(_):
        try:
            store.enroll(dict(request, deviceId=str(uuid.uuid4())))
            return True
        except Refused:
            return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(attempt, range(4))) == 1


def test_expired_code_refused(store):
    _, request = pairing(store)
    with store.connect() as db:
        db.execute("UPDATE pairings SET expires=0")
    with pytest.raises(Refused):
        store.enroll(request)


@pytest.mark.parametrize("url", ["http://192.168.1.3:38443", "https://8.8.8.8", "https://example.org", "https://user:pass@192.168.1.3", "https://192.168.1.3/path"])
def test_pairing_private_https_only(store, url):
    with pytest.raises(Refused):
        store.pairing("melange", url)


@pytest.mark.parametrize("address,expected", [("127.0.0.1", True), ("192.168.2.3", True), ("172.31.2.1", True), ("0.0.0.0", False), ("8.8.8.8", False), ("169.254.1.1", False)])
def test_private_listener_boundaries(address, expected):
    assert private_address(address) == expected


def test_revocation_stops_access(store):
    auth, device = enrolled(store)
    store.revoke("melange", auth["deviceId"])
    with pytest.raises(Refused):
        store.authenticate("Bearer " + auth["deviceToken"])


def test_publication_requires_real_release_gates(store, tmp_path):
    auth, _ = enrolled(store)
    package = tmp_path / "unsigned.exe"
    package.write_bytes(b"not signed")
    with pytest.raises(Refused, match="receipt"):
        store.publish("melange", package, {}, "1.0.1", 1, "win32", "x64", [auth["deviceId"]])
    assert not list((store.root / "packages").rglob("*.exe"))
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM releases").fetchone()[0] == 0


def test_all_green_receipt_cannot_bypass_os_publisher(store, tmp_path):
    auth, _ = enrolled(store)
    package = tmp_path / "unsigned.exe"
    package.write_bytes(b"not signed")
    receipt = {"schemaVersion": 1, "appId": "melange", "version": "1.0.1", "platform": "win32", "arch": "x64", "sha256": file_hashes(package)["sha256"],
               "gates": {name: {"status": "passed", "evidence": "test-fixture"} for name in ("tests", "security", "dependencies", "redistribution", "dataAllowlist")}, "osSignature": {"status": "passed"}}
    with pytest.raises(Refused, match="publisher"):
        store.publish("melange", package, receipt, "1.0.1", 1, "win32", "x64", [auth["deviceId"]])


def test_native_signature_verifies_private_staged_bytes(store, monkeypatch, tmp_path):
    auth, device = enrolled(store)
    package = tmp_path / "build-output.exe"
    original = b"original immutable package"
    package.write_bytes(original)
    def verify(receipt, manifest, staged):
        assert staged != package
        assert staged.is_relative_to(store.root)
        assert staged.read_bytes() == original
        package.write_bytes(b"producer changed build output after staging")
        assert file_hashes(staged)["sha256"] == manifest["package"]["sha256"]
    monkeypatch.setattr(store, "verify_release_gates", verify)
    store.publish("melange", package, {}, "1.0.1", 1, "win32", "x64", [auth["deviceId"]])
    installed, _ = store.package(device, 1, package.name)
    assert installed.read_bytes() == original


def test_signed_manifest_tamper_app_and_replay(store, monkeypatch, tmp_path):
    auth, _ = enrolled(store)
    envelope = publish_fixture(store, monkeypatch, tmp_path, [auth])
    keys = store.config()["apps"]["melange"]["publicKeys"]
    manifest = verify_envelope(envelope, keys, app="melange", platform="win32", arch="x64")
    assert manifest["version"] == "1.0.1"
    with pytest.raises(Refused, match="wrong app"):
        verify_envelope(envelope, keys, app="terrier")
    with pytest.raises(Refused, match="replayed"):
        verify_envelope(envelope, keys, minimum_sequence=1)
    with pytest.raises(Refused):
        verify_envelope(dict(envelope, payload=b64(canonical(dict(manifest, version="9.9.9")))), keys)
    with pytest.raises(Refused):
        verify_envelope(envelope, store.config()["apps"]["terrier"]["publicKeys"])


def test_recipient_restrictions_and_package_paths(store, monkeypatch, tmp_path):
    auth, device = enrolled(store)
    other_auth, other_device = enrolled(store)
    envelope = publish_fixture(store, monkeypatch, tmp_path, [auth])
    assert store.latest(other_device, "win32", "x64") is None
    manifest = json.loads(unb64(envelope["payload"]))
    with pytest.raises(Refused):
        store.package(other_device, 1, manifest["package"]["name"])
    with pytest.raises(Refused):
        store.package(device, 1, "../../admin.sqlite3")
    package, metadata = store.package(device, 1, manifest["package"]["name"])
    assert file_hashes(package)["sha256"] == metadata["sha256"]


def test_release_version_must_advance(store, monkeypatch, tmp_path):
    auth, _ = enrolled(store)
    publish_fixture(store, monkeypatch, tmp_path, [auth], sequence=2, version="1.0.2")
    with pytest.raises(Refused, match="advance"):
        publish_fixture(store, monkeypatch, tmp_path, [auth], sequence=3, version="1.0.1")


def test_rotation_chain_signed_by_previously_trusted_key(store):
    before = store.config()["apps"]["melange"]
    key_id = store.rotate_key("melange")
    after = store.config()["apps"]["melange"]
    transition = after["transitions"][0]
    key = Ed25519PublicKey.from_public_bytes(unb64(before["publicKeys"][transition["keyId"]]))
    key.verify(unb64(transition["signature"]), unb64(transition["payload"]))
    payload = json.loads(unb64(transition["payload"]))
    assert payload["fromKeyId"] == before["active"]
    assert payload["toKeyId"] == key_id == digest(unb64(payload["publicKey"]))[:24]
    assert payload["appId"] == "melange"


def test_withdraw_and_status_minimization(store, monkeypatch, tmp_path):
    auth, device = enrolled(store)
    publish_fixture(store, monkeypatch, tmp_path, [auth])
    store.withdraw("melange", 1)
    assert store.latest(device, "win32", "x64") is None
    with pytest.raises(Refused):
        store.status(device, {"appId": "melange", "version": "1.0.0", "status": "failed", "errorCode": "private voter names"})
    store.status(device, {"appId": "melange", "version": "1.0.0", "status": "failed", "errorCode": "HASH_MISMATCH"})


@pytest.fixture
def live_server(store):
    server = BoundedServer(("127.0.0.1", 0), handler_for(store), store.tls_context())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    context = ssl.create_default_context(cafile=str(store.root / "tls.crt"))
    # Test client trusts only the generated certificate, not arbitrary certificates.
    context.check_hostname = False
    def request(method, path, body=None, headers=None):
        connection = http.client.HTTPSConnection("127.0.0.1", server.server_port, context=context, timeout=5)
        data = json.dumps(body).encode() if body is not None else None
        actual_headers = {"Content-Type": "application/json", **(headers or {})}
        connection.request(method, path, data, actual_headers)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result
    yield request
    server.shutdown()
    server.server_close()
    thread.join(5)


def test_https_enrollment_download_status_and_access_control(store, monkeypatch, tmp_path, live_server):
    _, request = pairing(store)
    status, body = live_server("POST", "/v1/enroll", request)
    assert status == 200
    auth = json.loads(body)
    header = {"Authorization": "Bearer " + auth["deviceToken"]}
    assert live_server("GET", "/v1/apps/melange/updates?platform=win32&arch=x64", headers=header)[0] == 204
    envelope = publish_fixture(store, monkeypatch, tmp_path, [auth])
    status, body = live_server("GET", "/v1/apps/melange/updates?platform=win32&arch=x64", headers=header)
    assert status == 200 and json.loads(body) == envelope
    manifest = json.loads(unb64(envelope["payload"]))
    status, body = live_server("GET", manifest["package"]["path"], headers=header)
    assert status == 200 and digest(body) == manifest["package"]["sha256"]
    status, body = live_server("GET", "/v1/apps/melange/feed/latest.yml?noCache=123", headers=header)
    assert status == 200 and json.loads(body)["version"] == "1.0.1"
    assert live_server("GET", "/v1/apps/terrier/updates?platform=win32&arch=x64", headers=header)[0] == 403
    assert live_server("GET", "/v1/apps/melange/updates")[0] == 403
    assert live_server("GET", "/v1/apps/melange/updates", headers={**header, "Origin": "https://evil.invalid"})[0] == 403
    assert live_server("POST", "/publish", {}, header)[0] == 404
    assert live_server("POST", "/v1/status", {"appId": "melange", "version": "1.0.1", "status": "installed"}, header)[0] == 200


def test_no_secrets_in_database_or_http_errors(store, live_server):
    bundle, request = pairing(store)
    auth = store.enroll(request)
    data = store.db.read_bytes()
    assert bundle["code"].encode() not in data
    assert auth["deviceToken"].encode() not in data
    status, body = live_server("POST", "/v1/enroll", {"appId": "bad"})
    assert status == 403
    assert body == b'{"error":"REQUEST_REFUSED"}'


def test_recovery_lookup_is_exact_recipient_target_and_revocation_bound(store, monkeypatch, tmp_path, live_server):
    auth, device = enrolled(store)
    other, other_device = enrolled(store)
    envelope = publish_fixture(store, monkeypatch, tmp_path, [auth], version='1.0.0')
    header = {'Authorization': 'Bearer ' + auth['deviceToken']}
    endpoint = '/v1/apps/melange/recovery?platform=win32&arch=x64&version=1.0.0'
    status, body = live_server('GET', endpoint, headers=header)
    assert status == 200 and json.loads(body) == envelope
    assert live_server('GET', endpoint, headers={'Authorization': 'Bearer ' + other['deviceToken']})[0] == 204
    assert live_server('GET', endpoint.replace('1.0.0', '1.0.1'), headers=header)[0] == 204
    assert live_server('GET', endpoint.replace('x64', 'arm64'), headers=header)[0] == 403
    assert live_server('GET', endpoint.replace('melange', 'terrier'), headers=header)[0] == 403
    assert live_server('GET', endpoint.split('&version')[0], headers=header)[0] == 403
    store.revoke('melange', auth['deviceId'])
    assert live_server('GET', endpoint, headers=header)[0] == 403
    store.withdraw('melange', 1)
    assert store.recovery_seed(other_device, 'win32', 'x64', '1.0.0') is None


def test_local_existing_release_authorization_is_audited_and_never_revives_withdrawn_release(store, monkeypatch, tmp_path, live_server):
    original, _ = enrolled(store)
    later, later_device = enrolled(store)
    foreign, _ = enrolled(store, app='terrier')
    publish_fixture(store, monkeypatch, tmp_path, [original], version='1.0.0')
    assert store.recovery_seed(later_device, 'win32', 'x64', '1.0.0') is None
    with pytest.raises(Refused):
        store.authorize_existing_release('melange', 1, [later['deviceId'], foreign['deviceId']])
    assert store.recovery_seed(later_device, 'win32', 'x64', '1.0.0') is None
    store.authorize_existing_release('melange', 1, [later['deviceId']])
    assert store.recovery_seed(later_device, 'win32', 'x64', '1.0.0')['version'] == '1.0.0'
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM audit WHERE action='existing-release-authorized' AND device_id=?", (later['deviceId'],)).fetchone()[0] == 1
    assert live_server('POST', '/v1/authorize-existing-release', {}, {'Authorization': 'Bearer ' + later['deviceToken']})[0] == 404
    store.withdraw('melange', 1)
    with pytest.raises(Refused):
        store.authorize_existing_release('melange', 1, [later['deviceId']])
    assert store.recovery_seed(later_device, 'win32', 'x64', '1.0.0') is None


@pytest.mark.parametrize('registered', [True, False])
def test_dashboard_window_is_registered_browser_app_mode_else_default_browser(registered, monkeypatch):
    winreg = pytest.importorskip('winreg')
    url = 'http://127.0.0.1:1/#bootstrap=x'
    monkeypatch.setenv('LOCALAPPDATA', r'C:\fake\local')
    with mock.patch.object(winreg, 'OpenKey', side_effect=None if registered else OSError), \
         mock.patch.object(winreg, 'QueryValue', return_value=r'C:\fake\msedge.exe'), \
         mock.patch('subprocess.Popen') as popen, mock.patch('webbrowser.open') as opened:
        open_dashboard_window(url)
    if registered:
        popen.assert_called_once()
        argv = popen.call_args.args[0]
        assert argv[0] == r'C:\fake\msedge.exe' and '--app=' + url in argv
        assert sum(arg.startswith('--user-data-dir=') for arg in argv) == 1
        opened.assert_not_called()
    else:
        popen.assert_not_called()
        opened.assert_called_once_with(url)


@pytest.mark.parametrize('raw,expected', [(None, False), ('', False), (' ', False), ('0', False), ('false', False), ('No', False), (' OFF ', False),
                                          ('1', True), (' 1', True), ('true', True), ('TRUE', True), ('Yes', True), (' on	', True)])
def test_invite_env_accepts_the_documented_values(raw, expected):
    assert invite_env({} if raw is None else {'MELANGE_ACCESS_REQUIRE_INVITE': raw}) is expected


@pytest.mark.parametrize('raw', ['2', 'enabled', 'y', 'tru', 'on off', '1 1', '-1'])
def test_invite_env_refuses_unrecognised_values_instead_of_leaving_enforcement_off(raw):
    with pytest.raises(Refused, match='MELANGE_ACCESS_REQUIRE_INVITE'):
        invite_env({'MELANGE_ACCESS_REQUIRE_INVITE': raw})


@pytest.mark.parametrize('raw,flag,expected', [(None, [], False), ('true', [], True), ('off', [], False), ('0', ['--require-invite'], True)])
def test_serve_resolves_enforcement_from_environment_and_flag(store, monkeypatch, raw, flag, expected):
    monkeypatch.delenv('MELANGE_ACCESS_REQUIRE_INVITE', raising=False)
    if raw is not None: monkeypatch.setenv('MELANGE_ACCESS_REQUIRE_INVITE', raw)
    with mock.patch('desktop_admin.handler_for') as handler, mock.patch('desktop_admin.BoundedServer', side_effect=OSError('stop here')):
        assert main(['--state', str(store.root), 'serve', *flag]) == 2
    assert handler.call_args.args[1] is expected


@pytest.mark.parametrize('flag', [[], ['--require-invite']])
def test_serve_refuses_a_junk_enforcement_value_before_listening(store, monkeypatch, capsys, flag):
    monkeypatch.setenv('MELANGE_ACCESS_REQUIRE_INVITE', 'enabled')
    with mock.patch('desktop_admin.BoundedServer') as server:
        assert main(['--state', str(store.root), 'serve', *flag]) == 2
    server.assert_not_called()
    assert 'MELANGE_ACCESS_REQUIRE_INVITE' in capsys.readouterr().err


def test_junk_enforcement_value_does_not_affect_other_commands(store, monkeypatch):
    monkeypatch.setenv('MELANGE_ACCESS_REQUIRE_INVITE', 'enabled')
    assert main(['--state', str(store.root), 'devices']) == 0
