"""Tests for ops/compute/http_replay.py against a fake HTTP server on loopback (no real network, no live collector).

The fake collector below exercises what the real ones do: urlopen with a custom no-redirect opener, redirects followed,
4xx bodies, a transport failure that a retry survives, a POST, repeated GETs of one URL, a binary body. Its output depends
only on the responses, so "record run output == replay run output" is the D-177 content-level equivalence in miniature.
Secret-looking fixtures are built at run time so this file passes its own gate.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

OPS = Path(__file__).resolve().parents[1]
RUNNER = OPS / "compute" / "http_replay.py"
_spec = importlib.util.spec_from_file_location("http_replay", RUNNER)
HR = importlib.util.module_from_spec(_spec)
sys.modules["http_replay"] = HR
_spec.loader.exec_module(HR)

SECRET = "tok-" + "9f8e7d6c5b4a" * 2
BINARY = bytes(range(256)) * 3

COLLECTOR = '''
import hashlib, http.client, json, os, sys, urllib.error, urllib.request
base, out = sys.argv[1], sys.argv[2]
extra = sys.argv[3:]
key = os.environ["FAKE_KEY"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def sha(b):
    return hashlib.sha256(b).hexdigest()


result = {}
req = urllib.request.Request(base + "/ok?b=2&api_key=" + key + "&a=1", headers={"X-Api-Key": key, "User-Agent": "t/1"})
with urllib.request.urlopen(req, timeout=10) as r:
    body = r.read()
    result["ok"] = [r.status, r.headers.get("Content-Type"), r.getheader("X-Multi"), r.reason, sha(body)]
with urllib.request.urlopen(base + "/redir", timeout=10) as r:
    result["follow"] = [r.status, r.geturl().replace(base, ""), sha(r.read())]
try:
    urllib.request.build_opener(NoRedirect()).open(base + "/redir", timeout=10)
except urllib.error.HTTPError as e:
    result["nofollow"] = [e.code, e.headers.get("Location"), sha(e.read())]
try:
    urllib.request.urlopen(base + "/missing", timeout=10)
except urllib.error.HTTPError as e:
    result["missing"] = [e.code, e.reason, e.filename.replace(base, ""), e.read().decode()]
attempts = 0
while True:
    attempts += 1
    try:
        with urllib.request.urlopen(base + "/flaky", timeout=10) as r:
            result["flaky"] = [attempts, r.read().decode()]
            break
    except (http.client.RemoteDisconnected, urllib.error.URLError, ConnectionError):
        if attempts > 4:
            raise
with urllib.request.urlopen(urllib.request.Request(base + "/echo", data=b"payload-1"), timeout=10) as r:
    result["post"] = [r.status, r.read().decode()]
result["seq"] = []
for _ in range(3):
    with urllib.request.urlopen(base + "/seq", timeout=10) as r:
        result["seq"].append(r.read().decode())
with urllib.request.urlopen(base + "/binary", timeout=10) as r:
    result["binary"] = sha(r.read())
for path in extra:
    try:
        urllib.request.urlopen(base + path, timeout=10).read()
    except urllib.error.URLError as e:
        result["extra:" + path] = type(e).__name__
with open(out, "w", encoding="utf-8", newline="\\n") as fh:
    json.dump(result, fh, sort_keys=True)
'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, body=b"", headers=()):
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        srv = self.server
        srv.hits.append(self.path)
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        if parts.path == "/ok":
            self._send(200, b'{"ok": true}', [("Content-Type", "application/json"), ("Set-Cookie", "sid=" + SECRET),
                                              ("X-Multi", "a"), ("X-Multi", "b")])
        elif parts.path == "/echo-key":   # a server that reflects the caller's key into its body and a header
            self._send(200, ("you sent " + query["api_key"][0]).encode(), [("X-Reflect", query["api_key"][0])])
        elif parts.path == "/redir":
            self._send(302, b"", [("Location", "/ok")])
        elif parts.path == "/missing":
            self._send(404, b"not here")
        elif parts.path == "/flaky":
            srv.flaky += 1
            if srv.flaky == 1:
                return   # close without a response: the client sees RemoteDisconnected
            self._send(200, b"third time lucky" if srv.flaky > 2 else b"second time")
        elif parts.path == "/seq":
            srv.seq += 1
            self._send(200, str(srv.seq).encode())
        elif parts.path == "/binary":
            self._send(200, BINARY, [("Content-Type", "application/octet-stream")])
        else:
            self._send(404, b"unknown")

    def do_POST(self):
        self.server.hits.append(self.path)
        data = self.rfile.read(int(self.headers["Content-Length"]))
        self._send(200, hashlib.sha256(data).hexdigest().encode())


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.hits, srv.flaky, srv.seq = [], 0, 0
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    srv.base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def collector(tmp_path, monkeypatch):
    path = tmp_path / "fake_collector.py"
    path.write_text(COLLECTOR, encoding="utf-8")
    monkeypatch.setenv("FAKE_KEY", SECRET)
    return path


def runner(*args, **kwargs):
    return subprocess.run([sys.executable, str(RUNNER), *map(str, args)], capture_output=True, text=True, timeout=120, **kwargs)


def tree_bytes(directory: Path) -> bytes:
    return b"".join(p.read_bytes() for p in sorted(directory.rglob("*")) if p.is_file())


# ---------------------------------------------------------------- end to end

def test_replay_reproduces_the_recorded_run_with_no_live_request_and_the_cassette_holds_no_secret(server, collector, tmp_path):
    cassette, out_a, out_b = tmp_path / "cas", tmp_path / "a.json", tmp_path / "b.json"
    rec = runner("record", cassette, "--secret-env", "FAKE_KEY", "--", collector, server.base, out_a)
    assert rec.returncode == 0, rec.stderr
    recorded_hits = len(server.hits)
    assert recorded_hits >= 12 and "interactions" in rec.stderr

    rep = runner("replay", cassette, "--secret-env", "FAKE_KEY", "--", collector, server.base, out_b)
    assert rep.returncode == 0, rep.stderr
    assert len(server.hits) == recorded_hits                      # not one request reached the server
    assert out_a.read_bytes() == out_b.read_bytes()               # D-177: identical output from identical responses
    result = json.loads(out_a.read_text())
    assert result["flaky"][0] == 2                                # the retry path ran, and replay reproduced it
    assert result["seq"] == ["1", "2", "3"] and result["follow"][:2] == [200, "/ok"]
    assert result["nofollow"][:2] == [302, "/ok"] and result["missing"][:2] == [404, "Not Found"]
    assert result["ok"][2] == "a, b" and result["post"][0] == 200

    assert SECRET.encode() not in tree_bytes(cassette)            # URL param, X-Api-Key and Set-Cookie all scrubbed
    lines = [json.loads(x) for x in (cassette / "cassette.jsonl").read_text(encoding="utf-8").splitlines()]
    (ok,) = [x for x in lines if "/ok?" in x["key"]]
    assert ok["key"].endswith("/ok?a=1&api_key=REDACTED&b=2 -")  # query sorted, secret redacted, GET carries no body
    assert {k.lower(): v for k, v in ok["request"]["headers"]}["x-api-key"] == "REDACTED"
    assert {k.lower(): v for k, v in ok["response"]["headers"]}["set-cookie"] == "REDACTED"


def test_replay_miss_fails_the_run_loudly_and_names_the_request(server, collector, tmp_path):
    cassette = tmp_path / "cas"
    assert runner("record", cassette, "--secret-env", "FAKE_KEY", "--", collector, server.base, tmp_path / "a.json").returncode == 0
    rep = runner("replay", cassette, "--secret-env", "FAKE_KEY", "--", collector, server.base, tmp_path / "b.json", "/never")
    assert rep.returncode == 3                                    # the collector survived (it caught URLError) but the run is red
    assert "/never" in rep.stderr and "missed 1" in rep.stderr
    assert json.loads((tmp_path / "b.json").read_text())["extra:/never"] == "ReplayMiss"


def test_a_request_asked_for_more_often_than_recorded_misses(server, collector, tmp_path):
    cassette = tmp_path / "cas"
    assert runner("record", cassette, "--", collector, server.base, tmp_path / "a.json").returncode == 0
    hits = len(server.hits)
    rep = runner("replay", cassette, "--", collector, server.base, tmp_path / "b.json", "/seq")   # /seq was recorded 3 times
    assert rep.returncode == 3 and len(server.hits) == hits


def test_recorded_interactions_the_replay_never_asks_for_fail_unless_allowed(server, tmp_path):
    short = tmp_path / "short.py"
    short.write_text("import sys, urllib.request\nurllib.request.urlopen(sys.argv[1] + '/binary').read()\n"
                     "if len(sys.argv) > 2:\n    urllib.request.urlopen(sys.argv[1] + '/seq').read()\n", encoding="utf-8")
    cassette = tmp_path / "cas"
    assert runner("record", cassette, "--", short, server.base, "both").returncode == 0
    rep = runner("replay", cassette, "--", short, server.base)
    assert rep.returncode == 4 and "unused" in rep.stderr and "/seq" in rep.stderr
    assert runner("replay", cassette, "--allow-unused", "--", short, server.base).returncode == 0


def test_collector_exit_code_wins_and_is_passed_through(server, tmp_path):
    failing = tmp_path / "failing.py"
    failing.write_text("import sys, urllib.request\nurllib.request.urlopen(sys.argv[1] + '/binary').read()\nsys.exit(7)\n", encoding="utf-8")
    assert runner("record", tmp_path / "cas", "--", failing, server.base).returncode == 7
    assert runner("replay", tmp_path / "cas", "--", failing, server.base).returncode == 7


def test_a_cassette_is_recorded_once(server, tmp_path):
    (tmp_path / "cas").mkdir()
    (tmp_path / "cas" / "cassette.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(FileExistsError):
        HR.Harness("record", tmp_path / "cas").__enter__()
    with pytest.raises(FileNotFoundError):
        HR.Harness("replay", tmp_path / "nope").__enter__()


# ---------------------------------------------------------------- in-process behaviours

def test_a_secret_a_server_reflects_is_scrubbed_from_body_and_headers_and_the_final_scan_passes(server, tmp_path):
    with HR.Harness("record", tmp_path / "cas", secrets=[SECRET]):
        with urllib.request.urlopen(f"{server.base}/echo-key?api_key={SECRET}") as r:
            assert SECRET in r.read().decode() and SECRET in r.headers["X-Reflect"]   # the collector saw the live bytes
    log = (tmp_path / "cas" / "cassette.jsonl").read_text(encoding="utf-8")
    assert SECRET not in log and tree_bytes(tmp_path / "cas").count(SECRET.encode()) == 0
    with HR.Harness("replay", tmp_path / "cas", secrets=[SECRET]):
        with urllib.request.urlopen(f"{server.base}/echo-key?api_key=anything-at-all") as r:   # replay needs no key, and any value matches
            assert r.read() == b"you sent REDACTED"


def test_final_scan_refuses_a_cassette_that_still_holds_a_secret_without_printing_it(tmp_path):
    (tmp_path / "bodies").mkdir()
    (tmp_path / "cassette.jsonl").write_text('{"key": "GET http://x/?p=' + SECRET + ' -"}\n', encoding="utf-8")
    with pytest.raises(HR.SecretLeak) as caught:
        HR.verify_clean(tmp_path, [SECRET])
    assert SECRET not in str(caught.value)
    HR.verify_clean(tmp_path, ["something-else-entirely"])


def test_replay_blocks_non_loopback_sockets_but_not_loopback(server, tmp_path):
    with HR.Harness("record", tmp_path / "cas"):
        urllib.request.urlopen(server.base + "/binary").read()
    with HR.Harness("replay", tmp_path / "cas"):
        with pytest.raises(HR.ReplayBlocked):
            socket.create_connection(("203.0.113.9", 80), timeout=1)      # TEST-NET-3: nothing is ever sent
        with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5):
            pass
        urllib.request.urlopen(server.base + "/binary").read()


def test_patches_are_removed_on_exit(tmp_path):
    before = (urllib.request.OpenerDirector.open, subprocess.run, socket.socket.connect)
    with HR.Harness("record", tmp_path / "cas"):
        assert urllib.request.OpenerDirector.open is not before[0]
    assert (urllib.request.OpenerDirector.open, subprocess.run, socket.socket.connect) == before


def test_threads_record_and_replay_without_crossing_wires(server, tmp_path):
    def fetch(i):
        return urllib.request.urlopen(f"{server.base}/echo", data=f"body-{i}".encode()).read()
    with HR.Harness("record", tmp_path / "cas"):
        with ThreadPoolExecutor(8) as pool:
            live = list(pool.map(fetch, range(24)))
    lines = [json.loads(x) for x in (tmp_path / "cas" / "cassette.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sorted(x["seq"] for x in lines) == list(range(24))
    hits = len(server.hits)
    with HR.Harness("replay", tmp_path / "cas"):
        with ThreadPoolExecutor(8) as pool:
            assert list(pool.map(fetch, range(24))) == live
    assert len(server.hits) == hits


def test_transport_errors_round_trip_as_their_own_types(tmp_path):
    for exc in (ConnectionResetError(10054, "reset"), TimeoutError("slow"), urllib.error.URLError(socket.gaierror(-2, "no name")),
                urllib.error.URLError("plain reason")):
        back = HR.load_exc(json.loads(json.dumps(HR.dump_exc(exc))))
        assert type(back) is type(exc) and str(back) == str(exc)
    import http.client
    partial = http.client.IncompleteRead(b"abc", 9)
    back = HR.load_exc(json.loads(json.dumps(HR.dump_exc(partial))))
    assert isinstance(back, http.client.IncompleteRead) and back.partial == b"abc"
    assert isinstance(HR.load_exc({"type": "os.system", "args": ["echo hi"]}), urllib.error.URLError)   # never imports outside the allowlist


def test_url_and_header_scrubbing_and_key_normalisation():
    url = f"https://u:pw@Host.example/p?z=1&API_KEY={SECRET}&a=2&token=t&x-api-key=k"
    norm = HR.normalize_url(url)
    assert SECRET not in norm and "u:pw" not in norm and "pw@" not in norm
    assert norm == "https://Host.example/p?API_KEY=REDACTED&a=2&token=REDACTED&x-api-key=REDACTED&z=1"
    assert HR.normalize_url("http://h/p?b=2&a=1#frag") == HR.normalize_url("http://h/p?a=1&b=2")
    assert HR.normalize_url("http://h/p?mine=1", frozenset({"mine"})).endswith("mine=REDACTED")
    scrubbed = dict(HR.scrub_headers([("Authorization", "Bearer x"), ("x-api-key", "k"), ("Content-Type", "t"), ("Set-Cookie", "c")]))
    assert scrubbed == {"Authorization": "REDACTED", "x-api-key": "REDACTED", "Content-Type": "t", "Set-Cookie": "REDACTED"}


def test_secrets_are_collected_from_env_lists_text_files_and_json_leaves(tmp_path, monkeypatch):
    keyfile, ring = tmp_path / "k.txt", tmp_path / "ring.json"
    keyfile.write_text("\ufeff" + SECRET + "A\n\nshort\n", encoding="utf-8")
    ring.write_text(json.dumps({"data_gov": {"keys": [SECRET + "B"], "n": 3}, "note": "x"}), encoding="utf-8")
    monkeypatch.setenv("T_KEYS", f"{SECRET}C, {SECRET}D tiny")
    got = HR.collect_secrets(("T_KEYS", "T_ABSENT"), (keyfile, ring, tmp_path / "missing.txt"))
    assert got == sorted(SECRET + c for c in "ABCD")                      # short values are never treated as secrets


# ---------------------------------------------------------------- requests and the claude CLI

def test_requests_round_trip(server, tmp_path):
    requests = pytest.importorskip("requests")
    with HR.Harness("record", tmp_path / "cas", secrets=[SECRET]):
        live = requests.get(f"{server.base}/ok", params={"api_key": SECRET}, headers={"Authorization": "Bearer " + SECRET}, timeout=10)
        posted = requests.post(server.base + "/echo", data=b"p", timeout=10)
        followed = requests.get(server.base + "/redir", timeout=10)               # Session follows: each hop goes through the adapter
        missing = requests.get(server.base + "/missing", timeout=10)
    hits = len(server.hits)
    assert SECRET.encode() not in tree_bytes(tmp_path / "cas")
    with HR.Harness("replay", tmp_path / "cas", secrets=[SECRET]):
        again = requests.get(f"{server.base}/ok", params={"api_key": "different"}, timeout=10)
        assert (again.status_code, again.content, again.headers["Content-Type"]) == (live.status_code, live.content, "application/json")
        assert again.json() == {"ok": True} and again.reason == "OK"
        assert requests.post(server.base + "/echo", data=b"p", timeout=10).text == posted.text
        assert requests.get(server.base + "/redir", timeout=10).text == followed.text
        assert requests.get(server.base + "/missing", timeout=10).status_code == 404 == missing.status_code
    assert len(server.hits) == hits


def fake_claude(monkeypatch, calls):
    def run(command, **kwargs):
        calls.append((list(command), kwargs))
        envelope = {"structured_output": {"answer": len(calls)}, "total_cost_usd": 0.01 * len(calls), "session_id": f"s{len(calls)}"}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(envelope), stderr="warn")
    monkeypatch.setattr(subprocess, "run", run)


def test_claude_cli_calls_are_replayed_not_repeated_and_the_budget_flag_is_not_part_of_the_question(monkeypatch, tmp_path):
    calls: list = []
    fake_claude(monkeypatch, calls)
    def ask(prompt, budget):
        return subprocess.run(["C:/x/claude.exe", "-p", "--model", "sonnet", "--max-budget-usd", budget, "--system-prompt", "S"],
                              input=prompt, text=True, capture_output=True, timeout=5)
    with HR.Harness("record", tmp_path / "cas", secrets=[SECRET]):
        first, second = ask("question one", "0.50"), ask("question two", "0.49")
    assert len(calls) == 2 and first.stdout != second.stdout
    with HR.Harness("replay", tmp_path / "cas") as replay:
        assert ask("question two", "0.12").stdout == second.stdout           # a different remaining budget is the same question
        assert ask("question one", "9.99").stdout == first.stdout
        with pytest.raises(urllib.error.URLError):
            ask("question three", "0.50")                                     # an unrecorded question is a miss, never a live call
    assert len(calls) == 2 and len(replay.report()["misses"]) == 1
    assert (first.returncode, first.stderr) == (0, "warn")


def test_non_claude_subprocesses_pass_through_untouched(tmp_path):
    with HR.Harness("record", tmp_path / "cas"):
        done = subprocess.run([sys.executable, "-c", "print('hi')"], capture_output=True, text=True)
    assert done.stdout.strip() == "hi"
    assert not (tmp_path / "cas" / "cassette.jsonl").read_text(encoding="utf-8")


def test_a_claude_run_with_a_file_stdout_is_refused_in_both_modes_without_spawning(monkeypatch, tmp_path):
    monkeypatch.setenv("MELANGE_CLASSIFIER_COMMAND", sys.executable)          # this interpreter stands in for `claude`
    for mode in ("record", "replay"):                                         # was TypeError in record, an empty file in replay
        with HR.Harness(mode, tmp_path / "cas"), (tmp_path / f"{mode}.out").open("w+b") as out:
            with pytest.raises(ValueError, match="file stdout/stderr"):
                subprocess.run([sys.executable, "-c", "print('hi')"], stdout=out)
        assert (tmp_path / f"{mode}.out").read_bytes() == b""
        assert not (tmp_path / "cas" / "cassette.jsonl").read_text(encoding="utf-8")


def test_check_true_and_failures_are_replayed_faithfully(monkeypatch, tmp_path):
    def run(command, **kwargs):
        if kwargs.get("timeout") == 1:
            raise subprocess.TimeoutExpired(command, 1)
        return subprocess.CompletedProcess(command, 3, stdout="", stderr="login expired")
    monkeypatch.setattr(subprocess, "run", run)
    cmd = ["claude", "-p", "--model", "m"]
    with HR.Harness("record", tmp_path / "cas"):
        with pytest.raises(subprocess.TimeoutExpired):
            subprocess.run(cmd, input="a", timeout=1, capture_output=True, text=True)
        assert subprocess.run(cmd, input="b", capture_output=True, text=True).returncode == 3
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("live call during replay"))
    with HR.Harness("replay", tmp_path / "cas"):
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            subprocess.run(cmd, input="a", timeout=1, capture_output=True, text=True)
        assert caught.value.timeout == 1
        with pytest.raises(subprocess.CalledProcessError) as failed:
            subprocess.run(cmd, input="b", capture_output=True, text=True, check=True)
        assert failed.value.returncode == 3 and failed.value.stderr == "login expired"


# ---------------------------------------------------------------- a real collector, unmodified

VOTEVIEW = OPS.parent / "fec" / "legislation" / "refresh_vote_sources.py"

DRIVER = r'''
import io, os, sys, urllib.error, urllib.request
sys.path.insert(0, sys.argv[1])
import http_replay


def csv_for(url):
    kind = url.rsplit("/", 2)[1]
    congress = url.rsplit("HS", 1)[1].split("_")[0]
    header = {"members": "congress,chamber,icpsr", "rollcalls": "congress,chamber,rollnumber,bill_number,date,vote_question",
              "votes": "congress,chamber,rollnumber,cast_code,icpsr"}[kind]
    rows = {"members": "{c},House,{i}", "rollcalls": "{c},House,{i},HR{i},2025-01-0{i},On Passage", "votes": "{c},House,{i},1,{i}"}[kind]
    return (header + "\n" + "\n".join(rows.format(c=congress, i=i) for i in range(1, 4)) + "\n").encode()


class Upstream(io.BytesIO):   # a stand-in for the Voteview server; present ONLY while recording
    def __init__(self, url):
        super().__init__(csv_for(url))
        self.url, self.status, self.reason = url, 200, "OK"
        self.headers = http_replay._headers([("Last-Modified", "Mon, 06 Oct 2025 12:00:00 GMT"), ("Content-Type", "text/csv")])

    def geturl(self):
        return self.url


if os.environ.get("UPSTREAM") == "1":
    urllib.request.OpenerDirector.open = lambda self, fullurl, data=None, timeout=None: Upstream(fullurl if isinstance(fullurl, str) else fullurl.full_url)
sys.exit(http_replay.main(sys.argv[2:]))
'''


@pytest.mark.skipif(not VOTEVIEW.is_file(), reason="fec/ is not in this tree")
def test_a_real_collector_replays_to_identical_files_with_the_network_blocked(tmp_path):
    root = tmp_path / "root"
    (root / "data" / "legislation").mkdir(parents=True)
    (root / "data" / "legislation" / "vote_source_snapshot.json").write_text("{}", encoding="utf-8")
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER, encoding="utf-8")
    env = {**os.environ, "MONEYBALL_ROOT": str(root)}

    def run(mode, upstream):
        cmd = [sys.executable, str(driver), str(OPS / "compute"), mode, str(tmp_path / "cas"), "--", str(VOTEVIEW), "--no-install"]
        return subprocess.run(cmd, env={**env, "UPSTREAM": upstream}, capture_output=True, text=True, timeout=120)

    rec = run("record", "1")
    assert rec.returncode == 0, rec.stderr
    rep = run("replay", "0")                      # no stand-in: any request that escaped the cassette would hit the blocked network
    assert rep.returncode == 0, rep.stderr
    assert "served 9, missed 0, unused 0" in rep.stderr
    first, second = sorted((root / "data" / "legislation" / "vote_sources").iterdir())
    names = sorted(p.name for p in first.glob("HS*.csv"))
    assert len(names) == 9 and names == sorted(p.name for p in second.glob("HS*.csv"))
    for name in names:
        assert (first / name).read_bytes() == (second / name).read_bytes()
    strip = lambda d: [{k: v for k, v in f.items() if k not in ("path", "retrieved_at")} for f in json.loads((d / "receipt.json").read_text())["files"]]
    assert sorted(strip(first), key=lambda f: f["url"]) == sorted(strip(second), key=lambda f: f["url"])
    assert strip(first)[0]["last_modified"] == "Mon, 06 Oct 2025 12:00:00 GMT"      # a response header the collector reads
