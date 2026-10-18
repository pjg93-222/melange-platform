"""Shared helpers of the inert D075 controls: an in-process evidence broker (a fake provider behind the REAL AccountedTransport), a direct post() into it so no socket is needed,
and the collector driven in `--transport broker` mode through main(). No credentials, provider or network: every ledger, archive and state directory is a temporary directory."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
from unittest import mock

import test_collect_committee_activity_concurrency as cc

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
import api_host_pool as host  # noqa: E402
import api_request_pool as pool  # noqa: E402

driver, accounted = cc.driver, cc.accounted
RUN = 'ab' * 16
FAKE_STORED = 'C:\\Users\\pgoff\\Mentat\\mentat\\state\\committee-activity'      # the Windows spelling batches 01 to 03 recorded


class PlainOpener:
    """A provider that answers every URL with an empty, well-formed page (for controls about admission, not about content)."""
    def __init__(self, status=200, body=b'{"results":[],"pagination":{"count":0,"is_count_exact":true,"per_page":100,"pages":1,"last_indexes":{}}}', headers=None):
        self.status, self.body, self.headers, self.calls = status, body, headers or {'Content-Type': 'application/json'}, []

    def open(self, request, timeout):
        self.calls.append((request.full_url, request.get_header('X-api-key')))
        return cc.Response(self.body, self.status, dict(self.headers))


def make_broker(root, *, families=('B', 'E'), max_attempts=100000, opener=None, keys=cc.KEYS, run_id=RUN, cap=100000, stored_root=None, per_key_concurrency=2, name='host'):
    root = Path(root)
    return host.EvidenceBroker(keys=list(keys), ledger=root / 'quota.sqlite', archive_root=root / name, run_id=run_id, families=list(families), max_attempts=max_attempts,
                               opener=opener or cc.ScriptedOpener(cc.World()), cap=cap, per_key_concurrency=per_key_concurrency, stored_root=stored_root)


def descriptor_of(broker, reach='loopback'):
    return json.loads(host.evidence_descriptor_text(broker, 'http://127.0.0.1:9/v2', reach))


def direct_post(broker, token=None):
    """post(operation, payload) straight into broker.handle: the wire without a socket (JSON in and out, so nothing but JSON-able values can cross)."""
    def post(operation, payload):
        status, answer = broker.handle('POST', '/v2/' + operation, {'Authorization': 'Bearer ' + (token or broker.token)}, json.dumps(payload).encode())
        return status, json.loads(json.dumps(answer))
    return post


def client(broker, *, run_id=RUN, max_requests=100000, workers=1, post=None, tools=TOOLS):
    return pool.RemoteEvidenceTransport(descriptor_of(broker), run_id=run_id, max_requests=max_requests, workers=workers, post=post or direct_post(broker), tools=tools)


def call(broker, operation, payload, *, token=None, headers=None, method='POST', body=None):
    raw = json.dumps(payload).encode() if body is None else body
    sent = {'Authorization': 'Bearer ' + (broker.token if token is None else token)} if headers is None else headers
    status, answer = broker.handle(method, '/v2/' + operation, sent, raw)
    return status, json.loads(json.dumps(answer))


def journal(broker):
    return [json.loads(line) for line in broker.journal_path.read_text(encoding='utf-8').splitlines()]


def folder_of(broker):
    return broker.archive_root / ('responses-' + broker.run_id)


def invoke_broker(test, broker, flags, *, state, archive_root=None, environment=None, seeds=None, run_id=RUN):
    """driver.main() in `--transport broker` mode with the broker's descriptor in MENTAT_REQUEST_POOL_CONFIG and its wire replaced by a direct post. Returns the JSON line it prints."""
    descriptor = test.root / 'request-pool.json'
    descriptor.write_text(host.evidence_descriptor_text(broker, 'http://127.0.0.1:9/v2', 'loopback'), encoding='utf-8')
    argv = ['collect', '--state', str(state), '--archive-root', str(archive_root or broker.archive_root), '--transport', 'broker', '--run-id', run_id, '--live']
    if seeds is not False:
        argv += ['--seeds', str(seeds or test.seeds)]
    argv += list(flags)
    env = {'MENTAT_REQUEST_POOL_CONFIG': str(descriptor), **(environment or {})}
    output = io.StringIO()
    with mock.patch.object(sys, 'argv', argv), mock.patch.dict('os.environ', env), mock.patch.object(pool.RemoteEvidenceTransport, 'default_post', lambda self: direct_post(broker)):
        with redirect_stdout(output):
            driver.main()
    return json.loads(output.getvalue().strip().splitlines()[-1]) if output.getvalue().strip() else {}
