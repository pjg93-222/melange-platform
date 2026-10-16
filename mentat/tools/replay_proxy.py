"""Record and replay of a compute job's web traffic (D-180), around Melange's http_replay.py, without touching the collection code.

The jobs' clients (api_request_pool.request, pac_evidence.Client.page, R httr2/curl) already honour HTTPS_PROXY / NO_PROXY / SSL_CERT_FILE
(R on Windows also CURL_SSL_BACKEND=openssl + CURL_CA_BUNDLE), so a local forward proxy that serves the cassette is the whole layer.
http_replay.py is used unchanged: this file only chooses how its classes are wired (three seams, below).

    python replay_proxy.py mint TLS_DIR [--host H ...]               host only: ca.pem (certificate), leaf.pem, hosts.txt; the CA key is never written
    python replay_proxy.py record CASSETTE --secret-file F [...] -- command...       live traffic through the proxy into CASSETTE (host)
    python replay_proxy.py replay CASSETTE --tls TLS_DIR [--allow-unused] -- command...   CASSETTE answers; a request it lacks is a 502 and exit 3

Seams: (1) the CA. http_replay's Authority needs `cryptography`, which the compute image lacks, so mint runs on the host (leaf for the
fetched hosts, CA key discarded) and replay serves that leaf from the stdlib-only Preminted. (2) http_replay computes parents[2] of
its own path at import: mount it at /melange-replay/ops/compute/http_replay.py (three levels deep). (3) its proxy timeout is 60 s and
api_request_pool waits 120 s: the proxy is built here with 150.

REPLAY_REPORT (or --report) names a JSON file: http_replay.py sha256, cassette digest, served, misses, unused, served per request key,
TLS hosts refused. Exit code: the child's when nonzero, else 3 on a miss (4 on an unused entry unless --allow-unused), 5 if a secret reached
the cassette.
"""
import argparse
import collections
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading

TIMEOUT = 150
HOSTS = ('api.open.fec.gov',)
LOCATIONS = ('/melange-replay/ops/compute/http_replay.py', r'C:\Users\pgoff\OneDrive\Desktop\Melange Systems\ops\compute\http_replay.py')


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def find_http_replay(path=None):
    """The reference http_replay.py: --http-replay, else MELANGE_HTTP_REPLAY, else the container mount, else the live Melange tree."""
    for candidate in (path, os.environ.get('MELANGE_HTTP_REPLAY'), *LOCATIONS):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def load_http_replay(path=None):
    found = find_http_replay(path)
    if not found:
        raise SystemExit('replay_proxy: http_replay.py not found (--http-replay, MELANGE_HTTP_REPLAY, ' + ' or '.join(LOCATIONS) + ')')
    spec = importlib.util.spec_from_file_location('http_replay', found)
    module = importlib.util.module_from_spec(spec)
    sys.modules['http_replay'] = module
    spec.loader.exec_module(module)
    return module, found


def cassette_digest(directory):
    """The log's bytes, then every body's name and the sha256 of its content, in name order (a hand-edited body changes the digest)."""
    directory, digest = Path(directory), hashlib.sha256()
    with open(directory / 'cassette.jsonl', 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    for body in sorted((p for p in (directory / 'bodies').glob('*') if p.is_file()), key=lambda p: p.name):
        digest.update(b'\n' + body.name.encode() + b' ' + sha256_file(body).encode())
    return digest.hexdigest()


# --- mint (host only) ---------------------------------------------------------------------------------------------------------------------
def mint(tls, hosts=HOSTS, days=5 * 365):
    """One CA and one leaf for `hosts`, signed, then the CA key is dropped: it is never written, so nothing can mint another certificate."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization as ser
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    tls = Path(tls)
    if (tls / 'ca.pem').exists():
        raise FileExistsError(str(tls / 'ca.pem') + ' exists; a snapshot mints its certificates once')
    tls.mkdir(parents=True, exist_ok=True)
    ca_key, leaf_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    name = lambda cn: x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])          # noqa: E731
    base = lambda subject, issuer, key: (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())  # noqa: E731
                                         .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
                                         .not_valid_after(now + datetime.timedelta(days=days)))
    ca = (base(name('mentat replay CA (key discarded)'), name('mentat replay CA (key discarded)'), ca_key)
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)      # Python 3.13 verifies strictly: SKI/AKI
          .sign(ca_key, hashes.SHA256()))
    leaf = (base(name(hosts[0]), ca.subject, leaf_key)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in hosts]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    (tls / 'ca.pem').write_bytes(ca.public_bytes(ser.Encoding.PEM))
    (tls / 'leaf.pem').write_bytes(leaf.public_bytes(ser.Encoding.PEM) + leaf_key.private_bytes(ser.Encoding.PEM, ser.PrivateFormat.PKCS8, ser.NoEncryption()))
    (tls / 'hosts.txt').write_text('\n'.join(hosts) + '\n', encoding='utf-8', newline='\n')


class Preminted:
    """http_replay.Authority without `cryptography`: serves the leaf mint() wrote. A host it was not minted for is refused (and remembered)."""

    def __init__(self, tls):
        self.dir = Path(tls)
        self.ca_pem = self.dir / 'ca.pem'
        self.hosts = {line.strip().lower() for line in (self.dir / 'hosts.txt').read_text(encoding='utf-8').splitlines() if line.strip()}
        self.refused, self._context, self._lock = set(), None, threading.Lock()

    def context_for(self, host):
        if host.lower() not in self.hosts:
            self.refused.add(host.lower())
            raise OSError('replay_proxy: no certificate was minted for ' + host)
        with self._lock:
            if self._context is None:
                self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                self._context.minimum_version = ssl.TLSVersion.TLSv1_2
                self._context.load_cert_chain(str(self.dir / 'leaf.pem'))
            return self._context


# --- record / replay ----------------------------------------------------------------------------------------------------------------------
def counting(module):
    """http_replay.Harness that also counts what each request key was served."""
    class Counting(module.Harness):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.per_key, self._count = collections.Counter(), threading.Lock()

        def _next(self, key, what):
            entry = super()._next(key, what)
            with self._count:
                self.per_key[key] += 1
            return entry
    return Counting


def serve(mode, args, command):
    module, source = load_http_replay(args.http_replay)
    cassette = Path(args.cassette)
    secrets = module.collect_secrets(module.DEFAULT_SECRET_ENVS, (*module.DEFAULT_SECRET_FILES, *args.secret_file)) if mode == 'record' else []
    harness = counting(module)(mode, cassette, secrets=secrets, patch=False)
    refused, code, leak = set(), 0, None
    with tempfile.TemporaryDirectory(prefix='replay-proxy-') as scratch:
        authority = module.Authority(scratch) if mode == 'record' else Preminted(args.tls)
        try:
            with harness:
                proxy = module.ProxyServer(harness, authority, timeout=TIMEOUT).start()
                try:
                    code = subprocess.run(command, env=module.child_env(proxy, args.no_proxy)).returncode
                finally:
                    proxy.stop()
        except FileNotFoundError as error:
            print('replay_proxy: ' + str(error), file=sys.stderr)
            return 2
        except module.SecretLeak as error:                    # raised by the harness on exit; the message never holds the value
            print('replay_proxy: ' + str(error), file=sys.stderr)
            leak = str(error)
        refused = getattr(authority, 'refused', set())
    code = module._finish(harness, code, args.allow_unused or mode == 'record', 'replay_proxy')
    code = 5 if leak else code
    if refused and not code:                                  # a certificate refused for an unminted host is a failed run even when the child swallowed it
        code = 3
    report = harness.report()
    path = args.report or os.environ.get('REPLAY_REPORT')
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps({
            'mode': mode, 'exit_code': code, 'http_replay_sha256': sha256_file(source), 'cassette_sha256': cassette_digest(cassette) if (cassette / 'cassette.jsonl').is_file() else None,
            'interactions': report['interactions'], 'served': report['served'], 'misses': report['misses'], 'unused': report['unused'],
            'served_per_key': dict(sorted(harness.per_key.items())), 'refused_tls_hosts': sorted(refused), 'secret_leak': leak}, indent=1) + '\n', encoding='utf-8', newline='\n')
    return code


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    command = []
    if '--' in argv:
        split = argv.index('--')
        argv, command = argv[:split], argv[split + 1:]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='mode', required=True)
    minter = commands.add_parser('mint')
    minter.add_argument('tls', type=Path)
    minter.add_argument('--host', action='append', default=[])
    for mode in ('record', 'replay'):
        sub = commands.add_parser(mode)
        sub.add_argument('cassette', type=Path)
        sub.add_argument('--http-replay', type=Path)
        sub.add_argument('--report', type=Path)
        sub.add_argument('--no-proxy', default='localhost,127.0.0.1,::1', help='NO_PROXY handed to the child: loopback services stay direct')
        sub.add_argument('--secret-file', action='append', default=[], type=Path)
        sub.add_argument('--allow-unused', action='store_true')
        if mode == 'replay':
            sub.add_argument('--tls', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.mode == 'mint':
        mint(args.tls, tuple(args.host) or HOSTS)
        return 0
    if not command:
        parser.error('no command after --')
    return serve(args.mode, args, command)


if __name__ == '__main__':
    sys.exit(main())
