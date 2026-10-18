#!/usr/bin/env python3
"""Record/replay of collector network traffic (D-180). Secrets are scrubbed before they reach disk.

A collector is proven in the compute container by feeding it the exact responses its Windows run saw (F7: the
same request at two moments returns different bytes, so a live comparison proves nothing). The collector runs
unchanged under this runner; the cassette makes its inputs identical. Two transports, one cassette format:

  Python collectors, hooked in-process (stdlib only, no proxy):
    python ops/compute/http_replay.py record DIR [options] -- fec/collect/refresh_fec_money.py ARGS...   (Windows, live)
    python ops/compute/http_replay.py replay DIR [options] -- fec/collect/refresh_fec_money.py ARGS...   (container, network none)
      * urllib: OpenerDirector.open, the one call urlopen and every custom opener share. Redirect hops inside one open()
        belong to that call; a no-redirect opener's 3xx/4xx/5xx HTTPError is recorded and re-raised as itself;
      * requests: HTTPAdapter.send, when requests is importable (the collector image does not carry it);
      * the claude CLI: subprocess.run of a `claude` binary, or its bounded Popen over temp files (D-211), so a classifier
        step is replayed, never called twice.
    Replay blocks every non-loopback socket connect, so traffic the hooks cannot see (code that opens sockets itself)
    fails loudly instead of silently going live.

  Any other client (R httr/rvest, a Node script, a child process), through an environment-configured proxy:
    python ops/compute/http_replay.py proxy record DIR [options] -- Rscript collect/refresh_press_delta.R ARGS...
    python ops/compute/http_replay.py proxy replay DIR [options] -- Rscript collect/refresh_press_delta.R ARGS...
    A local forward proxy on 127.0.0.1 serves the child: HTTP_PROXY/HTTPS_PROXY/ALL_PROXY point at it, NO_PROXY keeps
    loopback services direct, and HTTPS (CONNECT) is terminated with leaf certificates from a throwaway in-memory CA whose
    certificate alone reaches the child (SSL_CERT_FILE, CURL_CA_BUNDLE, REQUESTS_CA_BUNDLE, NODE_EXTRA_CA_CERTS; and
    CURL_SSL_BACKEND=openssl, because Windows R's libcurl defaults to Schannel, which ignores the bundle). Needs
    `cryptography` on the machine that runs the proxy (requirements.txt pins it; the R image must carry it). Verified with
    R 4.6.0 + httr 1.4.8 + libcurl 8.14.1 and with the curl command line against loopback servers; the proxy never follows
    a redirect (the client does, one cassette entry per hop) and never decodes a body (gzip passes through raw).
    Limits: the upstream sees Python's TLS fingerprint, not libcurl's, so a bot-protected host may answer differently
    while recording; chunked request bodies and HTTP/2 are not supported; a client that ignores proxy variables fails
    loudly in replay (network none) and goes live in record, so record only collectors known to honour them.

NOT covered by either transport: a browser session (Node/Playwright driving Chromium: Cloudflare-fronted pages carry bot
checks and rotating cookies and are not replayable byte for byte), and chromote.

Cassette: DIR/cassette.jsonl (one JSON object per interaction, in call order) + DIR/bodies/<sha256> (content-addressed
response bodies). Requests are matched on method + URL (secret query parameters redacted, query sorted) + body
sha256 as sent (a secret inside a request BODY therefore has to be the same value in replay); several interactions on
one key are served in the order they were recorded (a retry sees what the original run saw). A request with no
recorded interaction raises ReplayMiss (a URLError, so the collector treats it as a network failure; in proxy mode a
502 marked X-Replay-Miss) AND fails the run (exit 3); recorded interactions the replay never asked for fail it too
(exit 4, --allow-unused downgrades): request-set equality in both directions. The collector's own exit code wins.

Scrubbing: secret query parameters and header values are replaced by REDACTED; every secret VALUE (env vars, key
files, --secret-file) found in a stored body, URL or header is replaced too; a final scan refuses a cassette that
still holds one. The collector itself is handed the unscrubbed response in record mode, so a recorded run behaves
exactly like a live one; only the cassette is scrubbed (a collector that echoes a secret into its output will show
REDACTED in the replay output, and the parity comparison will say so).
"""
from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import http.client
import http.server
import importlib
import io
import ipaddress
import json
import os
import re
import runpy
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import urllib.response
from collections import defaultdict, deque
from pathlib import Path

REDACTED = "REDACTED"
MIN_SECRET = 8   # shorter values would scrub ordinary words
SECRET_PARAMS = frozenset({"api_key", "apikey", "key", "access_token", "token", "auth", "password", "secret",
                           "client_secret", "x-api-key"})
SECRET_HEADERS = frozenset({"authorization", "proxy-authorization", "x-api-key", "api-key", "x-goog-api-key",
                            "x-auth-token", "x-amz-security-token", "cookie", "set-cookie", "set-cookie2"})
DEFAULT_SECRET_ENVS = ("GOVINFO_KEY", "GOVINFO_API_KEY", "FEC_API_KEY", "FEC_API_KEYS", "DATA_GOV_API_KEY",
                       "CONGRESS_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
_REPO = Path(__file__).resolve().parents[2]
DEFAULT_SECRET_FILES = (_REPO / "fec" / "govinfo_key.txt", _REPO / "fec" / "anthropic_key.txt")
# Exception classes a cassette may rebuild: standard library networking and requests only.
_EXC_MODULES = ("builtins", "urllib.error", "http.client", "socket", "ssl", "subprocess", "requests.exceptions",
                "urllib3.exceptions")


class ReplayMiss(urllib.error.URLError):
    """A request the cassette has no (more) recorded answer for."""


class ReplayBlocked(OSError):
    """A live (non-loopback) socket connect attempted during replay."""


class SecretLeak(RuntimeError):
    """A secret value is present in a cassette."""


# ------------------------------------------------------------------ scrubbing / keys

def _is_secret_param(piece: str, extra: frozenset) -> bool:
    return urllib.parse.unquote_plus(piece.partition("=")[0]).lower() in SECRET_PARAMS | extra


def normalize_url(url: str, extra_params: frozenset = frozenset()) -> str:
    """Drop userinfo and fragment, redact secret query parameters, sort the query: stable across runs."""
    parts = urllib.parse.urlsplit(url)
    pieces = sorted(f"{p.partition('=')[0]}={REDACTED}" if _is_secret_param(p, extra_params) else p
                    for p in parts.query.split("&") if p)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc.rpartition("@")[2], parts.path, "&".join(pieces), ""))


def scrub_headers(pairs) -> list[list[str]]:
    return [[k, REDACTED if k.lower() in SECRET_HEADERS else v] for k, v in pairs]


def _body_bytes(body) -> bytes | None:
    if body is None:
        return None
    if isinstance(body, (bytes, bytearray, memoryview)):
        return bytes(body)
    if isinstance(body, str):
        return body.encode("utf-8")
    raise TypeError(f"http_replay cannot key a request body of type {type(body).__name__}")


def _sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def collect_secrets(env_names=DEFAULT_SECRET_ENVS, files=DEFAULT_SECRET_FILES) -> list[str]:
    """Secret values from env vars (comma/space lists allowed) and key files (.json: every string leaf)."""
    values: set[str] = set()
    for name in env_names:
        values.update(re.split(r"[,\s]+", os.environ.get(name, "")))
    for file in files:
        path = Path(file)
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if path.suffix.lower() == ".json":
            stack = [json.loads(text)]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    stack.extend(node.values())
                elif isinstance(node, list):
                    stack.extend(node)
                elif isinstance(node, str):
                    values.add(node)
        else:
            values.update(line.strip() for line in text.splitlines())
    return sorted(v for v in values if len(v) >= MIN_SECRET)


def _needles(secrets) -> list[bytes]:
    return [n for s in secrets for n in (s.encode("utf-8"), urllib.parse.quote(s, safe="").encode("utf-8"))]


def verify_clean(directory, secrets) -> None:
    """Raise SecretLeak (never printing the value) if any secret appears in the cassette."""
    directory = Path(directory)
    needles = _needles(secrets)
    for file in [directory / "cassette.jsonl", *sorted((directory / "bodies").glob("*"))]:
        if file.is_file():
            data = file.read_bytes()
            if any(n in data for n in needles):
                raise SecretLeak(f"a secret value is present in {file.name}; delete {directory} and re-record")


# ------------------------------------------------------------------ exceptions <-> JSON

def _dump_arg(value):
    if isinstance(value, (bytes, bytearray)):
        return {"b64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, (list, tuple)):
        return [_dump_arg(v) for v in value]
    return value if value is None or isinstance(value, (int, float, str, bool)) else str(value)


def _load_arg(value):
    if isinstance(value, dict):
        return base64.b64decode(value["b64"])
    return [_load_arg(v) for v in value] if isinstance(value, list) else value


def dump_exc(exc: BaseException) -> dict:
    out = {"type": f"{type(exc).__module__}.{type(exc).__qualname__}", "args": [_dump_arg(a) for a in exc.args]}
    reason = getattr(exc, "reason", None)
    if isinstance(exc, urllib.error.URLError) and isinstance(reason, BaseException):
        out["args"] = []
        out["reason"] = dump_exc(reason)
    return out


def load_exc(data: dict) -> BaseException:
    module, _, name = data["type"].rpartition(".")
    args = [_load_arg(a) for a in data.get("args", [])]
    try:
        if module not in _EXC_MODULES:
            raise ImportError(module)
        cls = getattr(importlib.import_module(module), name)
        if not (isinstance(cls, type) and issubclass(cls, BaseException)):
            raise TypeError(name)
        if "reason" in data:
            return cls(load_exc(data["reason"]), *args)
        return cls(*args)
    except (ImportError, AttributeError, TypeError):
        return urllib.error.URLError(f"recorded {data['type']}: {data.get('args')}")


# ------------------------------------------------------------------ replayed objects

class _Reply(urllib.response.addinfourl):
    """What urlopen would have returned: addinfourl plus the HTTPResponse accessors collectors use."""

    def getheader(self, name, default=None):
        found = self.headers.get_all(name)
        return ", ".join(found) if found else default

    def getheaders(self):
        return list(self.headers.items())


def _headers(pairs) -> http.client.HTTPMessage:
    message = http.client.HTTPMessage()
    for key, value in pairs:
        message[key] = value
    return message


def _reply(status, reason, url, pairs, blob):
    reply = _Reply(io.BytesIO(blob), _headers(pairs), url, status)
    reply.reason = reply.msg = reason
    reply.version = 11
    return reply


def _http_error(status, reason, url, pairs, blob):
    return urllib.error.HTTPError(url, status, reason, _headers(pairs), io.BytesIO(blob))


# ------------------------------------------------------------------ the harness

class Harness:
    """Install with `with Harness("record"|"replay", dir):`; the patches are process-wide until it exits."""

    def __init__(self, mode: str, directory, *, secrets=(), extra_params=(), allow_unused: bool = False, patch: bool = True):
        if mode not in ("record", "replay"):
            raise ValueError(mode)
        self.mode, self.dir = mode, Path(directory)
        self.secrets, self.allow_unused, self.patch = list(secrets), allow_unused, patch   # patch=False: the proxy does the traffic
        self.extra_params = frozenset(p.lower() for p in extra_params)
        self.misses: list[str] = []
        self._lock = threading.Lock()
        self._restore: list = []
        self._seq = 0
        self._pending: dict[str, deque] = defaultdict(deque)
        self._total = 0

    # -- lifecycle
    def __enter__(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        log = self.dir / "cassette.jsonl"
        if self.mode == "record":
            if log.exists():
                raise FileExistsError(f"{log} exists; a cassette is recorded once (choose a new directory)")
            (self.dir / "bodies").mkdir(exist_ok=True)
            self._fh = log.open("a", encoding="utf-8", newline="\n")
        else:
            if not log.is_file():
                raise FileNotFoundError(f"no cassette at {log}")
            for line in log.read_text(encoding="utf-8").splitlines():
                entry = json.loads(line)
                self._pending[entry["key"]].append(entry)
                self._total += 1
        if self.patch:
            if self.mode == "replay":
                self._patch(socket.socket, "connect", self._guard(socket.socket.connect))
                self._patch(socket.socket, "connect_ex", self._guard(socket.socket.connect_ex))
            self._patch(urllib.request.OpenerDirector, "open", self._urllib_open(urllib.request.OpenerDirector.open))
            self._patch(subprocess, "run", self._cli_run(subprocess.run))
            self._patch(subprocess, "Popen", self._cli_popen(subprocess.Popen))
            try:
                import requests.adapters
            except ImportError:
                pass
            else:
                self._patch(requests.adapters.HTTPAdapter, "send", self._requests_send(requests.adapters.HTTPAdapter.send))
        return self

    def __exit__(self, *exc_info):
        for owner, name, original in reversed(self._restore):
            setattr(owner, name, original)
        self._restore.clear()
        if self.mode == "record":
            self._fh.close()
            verify_clean(self.dir, self.secrets)
        return False

    def _patch(self, owner, name, replacement):
        self._restore.append((owner, name, getattr(owner, name)))
        setattr(owner, name, replacement)

    def report(self) -> dict:
        unused = [k for k, q in self._pending.items() for _ in q]
        return {"mode": self.mode, "interactions": self._seq if self.mode == "record" else self._total,
                "served": self._total - len(unused) if self.mode == "replay" else None,
                "misses": list(self.misses), "unused": unused}

    # -- cassette I/O
    def _put(self, blob: bytes) -> str:
        for needle in _needles(self.secrets):
            blob = blob.replace(needle, REDACTED.encode())
        digest = _sha(blob)
        path = self.dir / "bodies" / digest
        if not path.exists():
            tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_bytes(blob)
            os.replace(tmp, path)
        return digest

    def _get(self, digest) -> bytes | None:
        return None if digest is None else (self.dir / "bodies" / digest).read_bytes()

    def _scrub(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, REDACTED).replace(urllib.parse.quote(secret, safe=""), REDACTED)
        return text

    def _record(self, kind, key, request, response=None, error=None) -> None:
        with self._lock:
            line = {"v": 1, "seq": self._seq, "kind": kind, "key": key, "request": request,
                    "response": response, "error": error}
            self._seq += 1
            self._fh.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
            self._fh.flush()

    def _next(self, key, what) -> dict:
        with self._lock:
            queue = self._pending.get(key)
            if queue:
                return queue.popleft()
            self.misses.append(key)
        raise ReplayMiss(f"no recorded response for {what} [{key}]")

    def _http_key(self, method, url, body):
        norm = normalize_url(url, self.extra_params)
        digest = _sha(body) if body is not None else "-"
        return f"{method} {norm} {digest}", {"method": method, "url": self._scrub(norm), "body_sha256": digest if body is not None else None,
                                            "body_len": None if body is None else len(body)}

    # -- replay-only network guard
    def _guard(self, original):
        def connect(sock, address, *args):
            host = address[0] if isinstance(address, tuple) else None
            if host is not None and not (host in ("localhost", "::1") or str(host).startswith("127.")):
                raise ReplayBlocked(f"replay: live network blocked ({host})")
            return original(sock, address, *args)
        return connect

    # -- urllib
    def _urllib_open(self, original):
        depth = threading.local()

        def open_(opener, fullurl, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
            if getattr(depth, "n", 0):   # a redirect hop: the outer open() is the recorded unit
                return original(opener, fullurl, data, timeout)
            req = urllib.request.Request(fullurl, data) if isinstance(fullurl, str) else fullurl
            if urllib.parse.urlsplit(req.full_url).scheme not in ("http", "https"):
                return original(opener, fullurl, data, timeout)
            body = _body_bytes(data if data is not None else req.data)
            method = getattr(req, "method", None) or ("POST" if body is not None else "GET")
            key, request = self._http_key(method, req.full_url, body)
            request["headers"] = self._headers_out(req.header_items())
            if self.mode == "replay":
                return self._materialize(self._next(key, req.full_url), req.full_url)
            depth.n = 1
            try:
                try:
                    try:
                        resp, raised = original(opener, fullurl, data, timeout), False
                    except urllib.error.HTTPError as err:
                        resp, raised = err, True
                    blob = resp.read()
                    status, reason, url = resp.status, resp.reason, resp.geturl()
                    pairs = list(resp.headers.items())
                    resp.close()
                except Exception as exc:
                    self._record("http", key, request, error=dump_exc(exc))
                    raise
            finally:
                depth.n = 0
            self._record("http", key, request, response=self._response(status, reason, url, pairs, blob, raised))
            if raised:
                raise _http_error(status, reason, url, pairs, blob)
            return _reply(status, reason, url, pairs, blob)

        return open_

    def _headers_out(self, pairs) -> list[list[str]]:
        return [[k, self._scrub(v)] for k, v in scrub_headers(pairs)]

    def _response(self, status, reason, url, pairs, blob, raised=False) -> dict:
        return {"status": status, "reason": reason, "url": self._scrub(normalize_url(url, self.extra_params)),
                "raised": raised, "headers": self._headers_out(pairs),
                "body_sha256": self._put(blob), "body_len": len(blob)}

    def _materialize(self, entry, url):
        if entry["error"]:
            raise load_exc(entry["error"])
        r = entry["response"]
        blob = self._get(r["body_sha256"])
        # the stored URL is normalised (secrets redacted); hand back what the collector asked for
        if r["raised"]:
            raise _http_error(r["status"], r["reason"], url, r["headers"], blob)
        return _reply(r["status"], r["reason"], url if r["url"] == normalize_url(url, self.extra_params) else r["url"],
                      r["headers"], blob)

    # -- requests
    def _requests_send(self, original):
        def send(adapter, request, **kwargs):
            body = _body_bytes(request.body)
            key, meta = self._http_key(request.method, request.url, body)
            meta["headers"] = self._headers_out(request.headers.items())
            if self.mode == "replay":
                return self._requests_response(self._next(key, request.url), request)
            try:
                resp = original(adapter, request, **kwargs)
                blob = resp.content
            except Exception as exc:
                self._record("http", key, meta, error=dump_exc(exc))
                raise
            self._record("http", key, meta, response=self._response(
                resp.status_code, resp.reason, resp.url, list(resp.headers.items()), blob))
            return resp

        return send

    def _requests_response(self, entry, request):
        if entry["error"]:
            raise load_exc(entry["error"])
        import requests
        r, blob = entry["response"], self._get(entry["response"]["body_sha256"])
        resp = requests.Response()
        resp.status_code, resp.reason, resp.url = r["status"], r["reason"], request.url
        resp.headers = requests.structures.CaseInsensitiveDict((k, v) for k, v in r["headers"])
        resp._content, resp._content_consumed, resp.raw = blob, True, io.BytesIO(blob)
        resp.encoding = requests.utils.get_encoding_from_headers(resp.headers)
        resp.request = request
        return resp

    # -- the claude CLI
    def _cli_run(self, original):
        def run(*popenargs, **kwargs):
            args = popenargs[0] if popenargs else kwargs.get("args")
            if not (isinstance(args, (list, tuple)) and args and _is_claude(args[0])):
                return original(*popenargs, **kwargs)
            key, request = self._cli_key(args, kwargs.get("input"))
            if self.mode == "replay":
                return self._cli_result(self._next(key, "claude CLI call"), args, kwargs)
            try:
                done = original(*popenargs, **kwargs)
            except Exception as exc:
                self._record("cli", key, request, error=dump_exc(exc))
                raise
            text = isinstance(done.stdout, str) or isinstance(done.stderr, str)
            blobs = [None if s is None else (s.encode("utf-8") if isinstance(s, str) else s) for s in (done.stdout, done.stderr)]
            self._record("cli", key, request, response={
                "returncode": done.returncode, "text": text,
                "stdout_sha256": None if blobs[0] is None else self._put(blobs[0]),
                "stderr_sha256": None if blobs[1] is None else self._put(blobs[1])})
            return done

        return run

    def _cli_result(self, entry, args, kwargs):
        if entry["error"]:
            raise load_exc(entry["error"])
        r = entry["response"]
        out = []
        for digest in (r["stdout_sha256"], r["stderr_sha256"]):
            blob = self._get(digest)
            out.append(blob.decode("utf-8") if blob is not None and r["text"] else blob)
        if kwargs.get("check") and r["returncode"]:
            raise subprocess.CalledProcessError(r["returncode"], args, out[0], out[1])
        return subprocess.CompletedProcess(args, r["returncode"], out[0], out[1])

    def _cli_key(self, args, stdin):
        """One key for run(input=text) and Popen(stdin=file holding the same text's UTF-8 bytes): _body_bytes encodes str as UTF-8."""
        argv = [str(a) for a in args]
        for i, a in enumerate(argv[:-1]):   # the remaining-budget value moves with spend, not with the question
            if a == "--max-budget-usd":
                argv[i + 1] = "-"
        stdin_sha = _sha(_body_bytes(stdin)) if stdin is not None else "-"
        model = argv[argv.index("--model") + 1] if "--model" in argv[:-1] else "-"
        return f"CLI {model} {_sha(json.dumps([argv[1:], stdin_sha]).encode())}", {
            "model": model, "argv_sha256": _sha(json.dumps(argv[1:]).encode()), "stdin_sha256": stdin_sha}

    # -- the claude CLI, bounded shape. D-211: classifier_runtime.classify_json no longer calls run(); it spawns Popen with temp-file
    #    stdin/stdout/stderr and waits with a timeout, which the run() hook cannot see. Popen is replaced by a SUBCLASS (subclassing
    #    and isinstance against subprocess.Popen keep working) that diverts only a claude argv whose stdout is a file; every other
    #    Popen, including the PIPE one run() builds internally, is constructed untouched.
    def _cli_popen(self, original):
        harness = self

        class Popen(original):
            def __new__(cls, *popenargs, **kwargs):
                args = popenargs[0] if popenargs else kwargs.get("args")
                if isinstance(args, (list, tuple)) and args and _is_claude(args[0]) and hasattr(kwargs.get("stdout"), "write"):
                    return harness._cli_spawn(original, popenargs, kwargs)
                return super().__new__(cls)

        return Popen

    def _cli_spawn(self, original, popenargs, kwargs):
        args, stdin = popenargs[0] if popenargs else kwargs["args"], kwargs.get("stdin")
        data = None
        if hasattr(stdin, "read"):   # key on what the child will read, then put the position back for it
            at = stdin.tell()
            data = stdin.read()
            stdin.seek(at)
        key, request = self._cli_key(args, data)
        streams = [s if hasattr(s, "write") else None for s in (kwargs.get("stdout"), kwargs.get("stderr"))]
        if self.mode == "replay":
            entry = self._next(key, "claude CLI call")
            if entry["error"]:
                raise load_exc(entry["error"])
            r = entry["response"]
            for stream, digest in zip(streams, (r["stdout_sha256"], r["stderr_sha256"])):
                if stream is not None and digest is not None:
                    stream.write(self._get(digest))
                    stream.flush()
            return _ReplayedCli(args, r["returncode"])
        starts = [None if s is None else s.tell() for s in streams]
        try:
            process = original(*popenargs, **kwargs)
        except Exception as exc:
            self._record("cli", key, request, error=dump_exc(exc))
            raise

        def finish(returncode):
            blobs = []
            for stream, start in zip(streams, starts):
                if stream is None:
                    blobs.append(None)
                    continue
                at = stream.tell()
                stream.seek(start)
                blobs.append(stream.read())
                stream.seek(at)
            self._record("cli", key, request, response={
                "returncode": returncode, "text": False,
                "stdout_sha256": None if blobs[0] is None else self._put(blobs[0]),
                "stderr_sha256": None if blobs[1] is None else self._put(blobs[1])})

        return _RecordedCli(process, finish)


class _RecordedCli:
    """The real claude child (record mode): wait()/poll() delegate, and the first one that sees it exit records the interaction."""

    def __init__(self, process, finish):
        self._process, self._finish = process, finish

    def __getattr__(self, name):   # pid, returncode, args, kill, terminate, send_signal: the real process's
        return getattr(self._process, name)

    def _done(self, returncode):
        if returncode is not None and self._finish:
            finish, self._finish = self._finish, None
            finish(returncode)
        return returncode

    def wait(self, timeout=None):
        return self._done(self._process.wait(timeout))

    def poll(self):
        return self._done(self._process.poll())


class _ReplayedCli:
    """A claude child rebuilt from the cassette (replay mode): it has already exited, nothing was spawned, nothing can be killed."""
    pid = None

    def __init__(self, args, returncode):
        self.args, self.returncode = args, returncode

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        pass

    terminate = kill


def _is_claude(command) -> bool:
    configured = os.environ.get("MELANGE_CLASSIFIER_COMMAND")
    return Path(str(command)).stem.lower() == "claude" or (bool(configured) and str(command) == configured)


# ------------------------------------------------------------------ proxy mode: R, Node, anything that honours HTTPS_PROXY

HOP_BY_HOP = frozenset({"connection", "proxy-connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
                        "trailer", "transfer-encoding", "upgrade"})
_DAY = datetime.timedelta(days=1)


class Authority:
    """A throwaway CA that signs one leaf certificate per tunnelled host (needs `cryptography`, which requirements.txt pins).
    The CA key never leaves this process; the child is handed the CA certificate only (http-replay-ca.pem)."""

    def __init__(self, directory):
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
        except ImportError as exc:
            raise SystemExit("http_replay proxy mode needs the `cryptography` package (pip install cryptography)") from exc
        self._x509, self._hashes, self._ser = x509, hashes, serialization
        self._eku, self._cn = ExtendedKeyUsageOID.SERVER_AUTH, NameOID.COMMON_NAME
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._ca_key, self._leaf_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "http_replay throwaway CA")])
        now = datetime.datetime.now(datetime.timezone.utc)
        self._ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(self._ca_key.public_key())
                    .serial_number(x509.random_serial_number()).not_valid_before(now - _DAY).not_valid_after(now + 7 * _DAY)
                    .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                    .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                                 key_encipherment=False, data_encipherment=False, key_agreement=False,
                                                 encipher_only=False, decipher_only=False), critical=True)
                    .sign(self._ca_key, hashes.SHA256()))
        self.ca_pem = self.dir / "http-replay-ca.pem"
        self.ca_pem.write_bytes(self._ca.public_bytes(serialization.Encoding.PEM))
        self._contexts: dict[str, ssl.SSLContext] = {}
        self._lock = threading.Lock()

    def context_for(self, host: str) -> ssl.SSLContext:
        with self._lock:
            if host not in self._contexts:
                self._contexts[host] = self._mint(host)
            return self._contexts[host]

    def _mint(self, host: str) -> ssl.SSLContext:
        x509, ser = self._x509, self._ser
        try:
            san = x509.IPAddress(ipaddress.ip_address(host))
        except ValueError:
            san = x509.DNSName(host)
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(self._cn, host[:64])]))
                .issuer_name(self._ca.subject).public_key(self._leaf_key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - _DAY).not_valid_after(now + 7 * _DAY)
                .add_extension(x509.SubjectAlternativeName([san]), critical=False)
                .add_extension(x509.ExtendedKeyUsage([self._eku]), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .sign(self._ca_key, self._hashes.SHA256()))
        pem = self.dir / f"leaf-{threading.get_ident()}.pem"   # load_cert_chain wants a path; the file lives for one call
        pem.write_bytes(cert.public_bytes(ser.Encoding.PEM) + self._leaf_key.private_bytes(
            ser.Encoding.PEM, ser.PrivateFormat.PKCS8, ser.NoEncryption()))
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(pem))
        finally:
            pem.unlink(missing_ok=True)
        return context


class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    """An HTTP/1.1 forward proxy. Plain-HTTP requests arrive in absolute form; HTTPS arrives as CONNECT, is terminated with a
    certificate the Authority mints for that host, and its requests are then read off the tunnel. One cassette entry per request."""

    protocol_version = "HTTP/1.1"
    tunnel: tuple[str, int] | None = None

    def setup(self):
        self.timeout = self.server.timeout   # an idle keep-alive connection ends its thread
        super().setup()

    def log_message(self, *args):
        pass

    def do_CONNECT(self):
        host, _, port = self.path.rpartition(":")
        host = host.strip("[]")
        try:
            self.send_response(200, "Connection Established")
            self.end_headers()
            tls = self.server.authority.context_for(host).wrap_socket(self.connection, server_side=True)
        except (OSError, ValueError):
            self.close_connection = True
            return
        self.connection, self.rfile, self.wfile = tls, tls.makefile("rb"), tls.makefile("wb", 0)
        self.tunnel, self.close_connection = (host, int(port or 443)), False
        try:
            while not self.close_connection:
                self.handle_one_request()
        finally:
            self.close_connection = True
            try:
                tls.close()
            except OSError:
                pass

    def _url(self) -> str:
        if self.tunnel is None:
            return self.path
        host, port = self.tunnel
        return f"https://{host if ':' not in host else '[' + host + ']'}{'' if port == 443 else f':{port}'}{self.path}"

    def _serve(self):
        harness = self.server.harness
        url = self._url()
        if not url.startswith(("http://", "https://")):
            return self._plain(400, "http_replay: not a proxy request")
        if self.headers.get("Transfer-Encoding"):
            return self._plain(411, "http_replay: chunked request bodies are not supported")
        length = self.headers.get("Content-Length")
        body = self.rfile.read(int(length)) if length else None
        key, meta = harness._http_key(self.command, url, body)
        meta["headers"] = harness._headers_out(self.headers.items())
        if harness.mode == "replay":
            try:
                entry = harness._next(key, url)
            except ReplayMiss as exc:
                return self._plain(502, str(exc), {"X-Replay-Miss": "1"})
            if entry["error"]:
                return self._plain(502, entry["error"]["type"], {"X-Replay-Recorded-Error": entry["error"]["type"]})
            r = entry["response"]
            return self._relay(r["status"], r["reason"], r["headers"], harness._get(r["body_sha256"]))
        try:
            status, reason, pairs, blob = self._upstream(url, body)
        except Exception as exc:
            recorded = dump_exc(exc)
            harness._record("http", key, meta, error=recorded)
            return self._plain(502, recorded["type"], {"X-Replay-Recorded-Error": recorded["type"]})   # replay answers with exactly this
        harness._record("http", key, meta, response=harness._response(status, reason, url, pairs, blob))
        self._relay(status, reason, pairs, blob)   # the collector sees the live, unscrubbed response

    def _upstream(self, url, body):
        parts = urllib.parse.urlsplit(url)
        server = self.server
        if parts.scheme == "https":
            conn = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=server.timeout, context=server.upstream)
        else:
            conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=server.timeout)
        try:
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
            conn.request(self.command, parts.path + (f"?{parts.query}" if parts.query else "") or "/", body=body, headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.reason, resp.getheaders(), resp.read()
        finally:
            conn.close()

    def _relay(self, status, reason, pairs, blob):
        head = self.command == "HEAD"
        self.send_response_only(status, reason)
        for name, value in pairs:
            if name.lower() not in HOP_BY_HOP and not (name.lower() == "content-length" and not head):
                self.send_header(name, value)
        if not head and status not in (204, 304) and status >= 200:
            self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        if not head and status not in (204, 304):
            self.wfile.write(blob)

    def _plain(self, status, text, headers=None):
        blob = text.encode("utf-8", "replace")
        self.send_response_only(status)
        for name, value in {"Content-Type": "text/plain; charset=utf-8", **(headers or {})}.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _serve


class ProxyServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, harness: Harness, authority: Authority, upstream_cafile=None, timeout: float = 60):
        super().__init__(("127.0.0.1", 0), _ProxyHandler)
        self.harness, self.authority, self.timeout = harness, authority, timeout
        self.upstream = ssl.create_default_context(cafile=str(upstream_cafile) if upstream_cafile else None)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def start(self) -> "ProxyServer":
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


def child_env(proxy: ProxyServer, no_proxy: str) -> dict:
    """What a proxy-aware child needs: where the proxy is, which CA to trust, and (record only) nothing else."""
    env = dict(os.environ)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env[name] = proxy.url
    for name in ("NO_PROXY", "no_proxy"):
        env[name] = no_proxy
    for name in ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS"):
        env[name] = str(proxy.authority.ca_pem)
    # Windows R's libcurl is a multi-SSL build whose default backend is Schannel, which ignores CURL_CA_BUNDLE and rejects the
    # throwaway CA (SEC_E_UNTRUSTED_ROOT). Asking for OpenSSL makes it honour the bundle; single-backend builds ignore the variable.
    env.setdefault("CURL_SSL_BACKEND", "openssl")
    return env


# ------------------------------------------------------------------ runner

def _secrets_from(args) -> list[str]:
    return collect_secrets((*DEFAULT_SECRET_ENVS, *args.secret_env), (*DEFAULT_SECRET_FILES, *args.secret_file))


def _finish(harness: Harness, code: int, allow_unused: bool, label: str) -> int:
    """Print the request-set summary and fold it into the exit code: the command's own failure wins, then a miss, then an unused entry."""
    report = harness.report()
    replay = report["mode"] == "replay"
    print(f"{label} {report['mode']}: {report['interactions']} interactions"
          + (f", served {report['served']}, missed {len(report['misses'])}, unused {len(report['unused'])}" if replay else ""),
          file=sys.stderr)
    for kind, keys in (("miss", report["misses"]), ("unused", report["unused"])):
        for key in keys:
            print(f"  {kind}: {key}", file=sys.stderr)
    if code:
        return code
    if report["misses"]:
        return 3
    return 4 if report["unused"] and not allow_unused else 0


def _parser(prog: str, with_proxy: bool = False) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=prog)
    ap.add_argument("mode", choices=("record", "replay"))
    ap.add_argument("directory", type=Path)
    ap.add_argument("--secret-env", action="append", default=[], help="extra env var holding a secret")
    ap.add_argument("--secret-file", action="append", default=[], type=Path, help="extra key file (.json: every string leaf)")
    ap.add_argument("--secret-param", action="append", default=[], help="extra secret query-parameter name")
    ap.add_argument("--allow-unused", action="store_true", help="replay: do not fail on recorded interactions never requested")
    if with_proxy:
        ap.add_argument("--upstream-cafile", type=Path, help="record: trust this CA file for upstream servers instead of the system roots")
        ap.add_argument("--no-proxy", default="localhost,127.0.0.1,::1", help="NO_PROXY handed to the child (loopback services stay direct)")
    return ap


def proxy_main(argv: list[str]) -> int:
    split = argv.index("--") if "--" in argv else len(argv)
    args, command = _parser("http_replay.py proxy", with_proxy=True).parse_args(argv[:split]), argv[split + 1:]
    if not command:
        print("usage: http_replay.py proxy {record,replay} DIR [options] -- Rscript script.R [args...]", file=sys.stderr)
        return 2
    harness = Harness(args.mode, args.directory, secrets=_secrets_from(args), extra_params=args.secret_param, patch=False)
    code = 0
    with tempfile.TemporaryDirectory(prefix="http-replay-") as tmp:
        authority = Authority(tmp)
        try:
            with harness:
                proxy = ProxyServer(harness, authority, args.upstream_cafile).start()
                try:
                    code = subprocess.run(command, env=child_env(proxy, args.no_proxy)).returncode
                finally:
                    proxy.stop()
        except FileNotFoundError as exc:
            print(f"http_replay: {exc}", file=sys.stderr)
            return 2
    return _finish(harness, code, args.allow_unused, "http_replay proxy")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["proxy"]:
        return proxy_main(argv[1:])
    if "--" not in argv:
        print("usage: http_replay.py {record,replay} DIR [options] -- script.py [args...]\n"
              "       http_replay.py proxy {record,replay} DIR [options] -- command [args...]", file=sys.stderr)
        return 2
    split = argv.index("--")
    args, command = _parser("http_replay.py").parse_args(argv[:split]), argv[split + 1:]
    if not command:
        print("http_replay.py: no collector command after --", file=sys.stderr)
        return 2
    script = Path(command[0]).resolve()
    sys.argv, code = [str(script), *command[1:]], 0
    sys.path.insert(0, str(script.parent))
    harness = Harness(args.mode, args.directory, secrets=_secrets_from(args), extra_params=args.secret_param)
    try:
        with harness:
            try:
                runpy.run_path(str(script), run_name="__main__")
            except SystemExit as exit_:
                code = exit_.code if isinstance(exit_.code, int) else (0 if exit_.code is None else 1)
    except BaseException:   # a collector that raises still gets its request-set summary before the traceback
        _finish(harness, 1, args.allow_unused, "http_replay")
        raise
    return _finish(harness, code, args.allow_unused, "http_replay")


if __name__ == "__main__":
    sys.exit(main())
