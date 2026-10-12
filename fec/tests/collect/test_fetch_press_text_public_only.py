"""fetch_press_text reads URLs from a CSV, so it must fetch only http(s) pages on public addresses.

The script runs at import, so each test runs it with a throwaway root, a mocked resolver and a fake
transport (the HTTP handlers' *_open methods): nothing touches the network or the real DNS.
"""
import csv
import email.message
import http.client
import io
import ipaddress
import runpy
import socket
import sys
import time
import urllib.error
import urllib.request
import urllib.response
from pathlib import Path

import pytest

FEC = Path(__file__).resolve().parents[2]
SCRIPT = FEC / "collect" / "fetch_press_text.py"
PUBLIC = "93.184.216.34"
PAGE = b"<html><body><p>" + b"credit union members benefit from this long enough article body " * 4 + b"</p></body></html>"

# host -> addresses the mocked resolver returns; anything unlisted is a public host
DNS = {
    "internal.example": ["10.1.2.3"],
    "rebind.example": ["192.168.1.5"],
    "v6.example": ["fd00::1"],
    "mapped.example": ["::ffff:127.0.0.1"],
    "multi.example": [PUBLIC, "127.0.0.1"],
    "nat64.example": ["64:ff9b::a00:1"],
    "sixtofour.example": ["2002:a00:1::"],
    "multicast.example": ["224.0.0.1"],
    "multi6.example": [PUBLIC, "64:ff9b::7f00:1"],
    "2130706433": ["127.0.0.1"],
    "gone.example": socket.gaierror(11001, "no such host"),
}


@pytest.fixture
def run(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "ROOT", str(tmp_path))
    monkeypatch.setattr(paths, "DRAFT", str(tmp_path / "draft"))
    (tmp_path / "draft").mkdir()
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    monkeypatch.syspath_prepend(str(FEC))
    opened = []
    redirects = {}

    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        try:
            answer = [str(ipaddress.ip_address(host))]      # a literal address resolves to itself
        except ValueError:
            answer = DNS.get(host, [PUBLIC])
        if isinstance(answer, Exception):
            raise answer
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, type, proto, "", (a, port)) for a in answer]

    def fake_open(self, req):
        opened.append(req.full_url)
        headers = http.client.HTTPMessage()
        if req.full_url in redirects:
            headers["Location"] = redirects[req.full_url]
            response = urllib.response.addinfourl(io.BytesIO(b""), headers, req.full_url, 302)
            response.msg = "Found"
        else:
            response = urllib.response.addinfourl(io.BytesIO(PAGE), headers, req.full_url, 200)
            response.msg = "OK"
        return response

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(urllib.request.HTTPHandler, "http_open", fake_open)
    monkeypatch.setattr(urllib.request.HTTPSHandler, "https_open", fake_open)

    def go(urls):
        with open(tmp_path / "draft" / "cu_press_stance_tone.csv", "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["url"])
            writer.writerows([[u] for u in urls])
        try:
            runpy.run_path(str(SCRIPT), run_name="__main__")
        except SystemExit as exit_:
            assert exit_.code == 2        # some URL failed
        cache = tmp_path / "press_text_cache.jsonl"
        import json
        return {r["url"]: r for r in map(json.loads, cache.read_text(encoding="utf-8").splitlines())} if cache.exists() else {}

    go.opened, go.redirects = opened, redirects
    return go


HOSTILE = [
    ("file:///C:/Windows/win.ini", "http or https"),
    ("ftp://example.gov/x", "http or https"),
    ("gopher://example.gov/x", "http or https"),
    ("http://127.0.0.1/", "loopback"),
    ("http://localhost:8806/api/state", "localhost"),
    ("http://169.254.169.254/latest/meta-data/", "link-local"),
    ("http://10.0.0.5/", "private"),
    ("http://172.16.0.9/", "private"),
    ("http://192.168.0.1/", "private"),
    ("http://[::1]/", "loopback"),
    ("http://internal.example/", "non-public address 10.1.2.3"),
    ("http://rebind.example/", "non-public address 192.168.1.5"),
    ("http://v6.example/", "non-public address fd00::1"),
    ("http://mapped.example/", "non-public address 127.0.0.1"),
    ("http://multi.example/", "non-public address 127.0.0.1"),
    ("http://2130706433/", "non-public address 127.0.0.1"),     # decimal spelling of 127.0.0.1
    ("http://gone.example/", "does not resolve"),
    # ranges ipaddress.is_global alone passes
    ("http://224.0.0.1/", "non-public address 224.0.0.1"),
    ("http://239.255.255.250/", "non-public address 239.255.255.250"),
    ("http://[ff02::1]/", "non-public address ff02::1"),
    ("http://[::10.0.0.1]/", "non-public address ::a00:1"),                    # IPv4-compatible
    ("http://[::127.0.0.1]/", "non-public address ::7f00:1"),
    ("http://[fec0::1]/", "non-public address fec0::1"),                        # site-local
    ("http://multicast.example/", "non-public address 224.0.0.1"),
    # a non-public IPv4 carried inside an IPv6 address
    ("http://[64:ff9b::10.0.0.1]/", "non-public address 64:ff9b::a00:1"),      # NAT64
    ("http://[64:ff9b::127.0.0.1]/", "non-public address 64:ff9b::7f00:1"),
    ("http://[64:ff9b::169.254.169.254]/", "non-public address 64:ff9b::a9fe:a9fe"),
    ("http://[64:ff9b::224.0.0.1]/", "non-public address 64:ff9b::e000:1"),
    ("http://[64:ff9b:1::a00:1]/", "non-public address 64:ff9b:1::a00:1"),     # local-use NAT64
    ("http://[2002:a00:1::]/", "non-public address 2002:a00:1::"),             # 6to4
    ("http://[2002:7f00:1::1]/", "non-public address 2002:7f00:1::1"),
    ("http://[2002:a9fe:a9fe::]/", "non-public address 2002:a9fe:a9fe::"),
    ("http://[2001::]/", "non-public address 2001::"),                          # Teredo
    ("http://[2001:0:a00:1::f5ff:fffe]/", "non-public address 2001:0:a00:1::f5ff:fffe"),
    ("http://[2001:0:5db8:d822::a247:27dd]/", "non-public address 2001:"),     # both inner IPv4 public: the range is still refused
    ("http://nat64.example/", "non-public address 64:ff9b::a00:1"),
    ("http://sixtofour.example/", "non-public address 2002:a00:1::"),
    ("http://multi6.example/", "non-public address 64:ff9b::7f00:1"),
]


@pytest.mark.parametrize("url, why", HOSTILE)
def test_non_public_targets_are_refused_before_any_connection(run, url, why):
    row = run([url])[url]
    assert row["text"] == "" and row["classification"] == "live_retry_failed"
    assert why in row["err"]
    assert run.opened == []


def test_public_https_and_http_hosts_are_fetched(run):
    rows = run(["https://www.example.gov/press/1", "http://news.example.org/a"])
    assert run.opened == ["https://www.example.gov/press/1", "http://news.example.org/a"]
    assert all(row["text"] and "err" not in row for row in rows.values())


# literal public addresses, including IPv6 forms that wrap a public IPv4 and must not be refused for it
PUBLIC_LITERALS = [
    "https://93.184.216.34/press/1",
    "https://[2606:4700::1111]/press/1",
    "http://[64:ff9b::5db8:d822]/press/1",      # NAT64 wrapping 93.184.216.34
    "http://[2002:5db8:d822::1]/press/1",       # 6to4 wrapping 93.184.216.34
]


@pytest.mark.parametrize("url", PUBLIC_LITERALS)
def test_public_literal_addresses_are_fetched(run, url):
    row = run([url])[url]
    assert row["text"] and "err" not in row
    assert run.opened == [url]


def test_a_redirect_to_a_private_address_is_refused(run):
    run.redirects["https://www.example.gov/old"] = "http://169.254.169.254/latest/meta-data/"
    row = run(["https://www.example.gov/old"])["https://www.example.gov/old"]
    assert row["text"] == "" and "link-local" in row["err"]
    assert run.opened == ["https://www.example.gov/old"]          # the redirect target was never contacted


@pytest.mark.parametrize("target, why", [
    ("http://rebind.example/x", "non-public address 192.168.1.5"),
    ("http://127.0.0.1:8806/", "loopback"),
    ("http://[64:ff9b::10.0.0.1]/", "non-public address 64:ff9b::a00:1"),
    ("file:///etc/passwd", "not allowed"),                       # urllib's own redirect scheme check
])
def test_redirects_are_rechecked_at_every_hop(run, target, why):
    run.redirects["https://www.example.gov/a"] = "https://other.example.gov/b"
    run.redirects["https://other.example.gov/b"] = target
    row = run(["https://www.example.gov/a"])["https://www.example.gov/a"]
    assert row["text"] == "" and why in row["err"].lower()
    assert run.opened == ["https://www.example.gov/a", "https://other.example.gov/b"]


def test_a_redirect_to_another_public_host_is_followed(run):
    run.redirects["https://www.example.gov/old"] = "https://www.example.gov/new"
    row = run(["https://www.example.gov/old"])["https://www.example.gov/old"]
    assert row["text"] and "err" not in row
    assert run.opened == ["https://www.example.gov/old", "https://www.example.gov/new"]


def test_the_timeout_is_still_25_seconds(run, monkeypatch):
    seen = []
    real_open = urllib.request.OpenerDirector.open

    def spy(self, fullurl, data=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        seen.append(timeout)
        return real_open(self, fullurl, data, timeout)

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", spy)
    run(["https://www.example.gov/press/1"])
    assert seen == [25]
