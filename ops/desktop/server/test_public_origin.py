"""Internet recipients require an explicit origin; trust and admin boundaries remain."""
import base64
import http.client
import json
import secrets
import ssl
import threading
import uuid
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from desktop_admin import BoundedServer, Refused, Store, handler_for, https_origin
from access_http import HTTPRefused
from access_store import AccessStore


def test_public_origin_validation_and_explicit_opt_in(tmp_path):
    assert https_origin('https://Updates.Example.com:443/', allow_public=True) == 'https://updates.example.com'
    assert https_origin('https://203.0.113.10:38443', allow_public=True) == 'https://203.0.113.10:38443'
    bad = ['http://updates.example.com', 'https://user@updates.example.com', 'https://updates.example.com/path',
           'https://updates.example.com?', 'https://updates.example.com#', 'https://updates.example.com:0',
           'https://updates.example.com:0443', 'https://example.0x1',
           'https://updates.example.com:', 'https://updates.example.com:65536', 'https://updates.example.com./',
           'https://*.example.com', 'https://localhost', 'https://127.1', 'https://2130706433',
           'https://0177.0.0.1', 'https://%65xample.com', 'https://updates.example.com\\evil',
           'https://updates.example.com\n', 'https://[::1]', 'https://0.0.0.0', 'https://224.0.0.1']
    for value in bad:
        with pytest.raises(Refused):
            https_origin(value, allow_public=True)
    private = Store(tmp_path)
    assert private.origin('https://192.168.2.1:38443') == 'https://192.168.2.1:38443'
    with pytest.raises(Refused):
        private.origin('https://updates.example.com')
    public = Store(tmp_path, public_origin='https://updates.example.com')
    with pytest.raises(Refused):
        public.origin('https://other.example.com')
    with pytest.raises(Refused):
        BoundedServer(('127.0.0.1', 0), handler_for(public), None)


def test_public_peer_and_host_checks_are_explicit_and_ignore_forwarded_headers(tmp_path):
    store = Store(tmp_path, public_origin='https://updates.example.com')
    handler_type = handler_for(store)
    request = object.__new__(handler_type)
    request.client_address = ('203.0.113.10', 12345)
    request.server = SimpleNamespace(public_origin=store.public_origin, server_address=('127.0.0.1', 38443))
    request.headers = Message()
    request.headers['Host'] = 'updates.example.com'
    request.path = '/v1/access/status'
    # Both the update routes and application-access routes use the configured origin.
    request.preflight()
    with patch.object(Path, 'is_file', return_value=True):
        request.access_preflight()
    request.headers.replace_header('Host', 'evil.example.com')
    request.headers['X-Forwarded-Host'] = 'updates.example.com'
    request.headers['Forwarded'] = 'host=updates.example.com;proto=https'
    with pytest.raises(Refused):
        request.preflight()
    with pytest.raises(HTTPRefused):
        request.access_preflight()
    request.headers.replace_header('Host', 'updates.example.com')
    request.headers['Host'] = 'updates.example.com'
    with pytest.raises(Refused):
        request.preflight()
    request.server.public_origin = None
    with pytest.raises(Refused):
        request.preflight()
    with pytest.raises(HTTPRefused):
        request.access_preflight()
    # The socket acceptance gate also stays closed for public peers by default.
    server = object.__new__(BoundedServer)
    server.public_origin = None
    server.slots = threading.BoundedSemaphore(1)
    with patch.object(server, 'shutdown_request') as rejected, patch('http.server.ThreadingHTTPServer.process_request') as accepted:
        server.process_request(object(), ('203.0.113.10', 1))
        rejected.assert_called_once()
        accepted.assert_not_called()
        server.public_origin = store.public_origin
        server.process_request(object(), ('203.0.113.10', 1))
        accepted.assert_called_once()


def test_explicit_public_origin_over_real_tls_keeps_auth_and_enrollment_trust(tmp_path):
    origin = 'https://updates.example.com'
    publisher = Store(tmp_path / 'state', public_origin=origin)
    publisher.init()
    access = AccessStore(publisher)
    access.initialize()
    assert access.descriptor(origin)['url'] == origin
    with pytest.raises(Refused):
        publisher.pairing('melange', 'https://other.example.com')
    pair = publisher.pairing('melange', origin)
    assert json.loads(base64.urlsafe_b64decode(pair + '=' * (-len(pair) % 4)))['url'] == origin
    value = dict(requestId=str(uuid.uuid4()), deviceId=str(uuid.uuid4()), appId='melange',
                 platform='win32', arch='x64', displayName='Synthetic recipient', requestSecret=secrets.token_urlsafe(32))
    access.request(value, '203.0.113.10')
    approval = access.approve(value['requestId'], 'local_user', [])
    activated = access.activate({**{key: value[key] for key in ('requestId', 'deviceId', 'appId', 'platform', 'arch')},
                                 'activationKey': approval['activationKey']}, value['requestSecret'])
    server = BoundedServer(('127.0.0.1', 0), handler_for(publisher), publisher.tls_context(), public_origin=origin)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    def call(path, *, host='updates.example.com', token=None, body=None):
        connection = http.client.HTTPSConnection('127.0.0.1', server.server_port,
                                                 context=ssl._create_unverified_context(), timeout=5)
        try:
            headers = {'Host': host, 'Content-Type': 'application/json'}
            if token:
                headers['Authorization'] = 'Bearer ' + token
            connection.request('POST' if body is not None else 'GET', path,
                               body=json.dumps(body) if body is not None else None, headers=headers)
            result = connection.getresponse()
            return result.status, json.loads(result.read())
        finally:
            connection.close()
    try:
        assert call('/v1/access/status')[0] == 403
        assert call('/v1/access/status', token=activated['accessToken'], host='evil.example.com')[0] == 403
        assert call('/v1/access/status', token=activated['accessToken'])[0] == 200
        status, enrolled = call('/v1/access/update-enrollment', token=activated['accessToken'], body={'currentVersion': '1.0.0'})
        assert status == 200
        assert enrolled['trust']['url'] == origin
        assert enrolled['deviceToken'] != activated['accessToken']
        assert call('/v1/apps/melange/trust', token=enrolled['deviceToken'])[0] == 200
        assert call('/v1/apps/melange/trust', token=enrolled['deviceToken'], host='evil.example.com')[0] == 403
        assert call('/admin', token=enrolled['deviceToken'])[0] == 404
        # A deliberate, independently distributed origin change preserves credentials on retry.
        publisher.public_origin = 'https://new-updates.example.com'
        retry = access.update_enrollment(activated['accessToken'], {'currentVersion': '1.0.0'}, publisher.public_origin)
        assert retry['deviceToken'] == enrolled['deviceToken']
        assert retry['trust'] == {**enrolled['trust'], 'url': publisher.public_origin}
        publisher.rotate_key('melange')
        refreshed = access.descriptor(publisher.public_origin)
        rotated = access.update_enrollment(activated['accessToken'], {'currentVersion': '1.0.0'}, publisher.public_origin)
        assert rotated['deviceToken'] == enrolled['deviceToken']
        assert rotated['trust']['publicKeys'] == refreshed['updatePublicKeysByApp']['melange']
        assert rotated['trust']['publicKeys'] != enrolled['trust']['publicKeys']
    finally:
        server.shutdown()
        worker.join(5)
        server.server_close()
