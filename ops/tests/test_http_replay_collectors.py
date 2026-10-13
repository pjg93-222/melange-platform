"""A real Melange collector, unmodified, proven by record/replay (D-180): fec/collect/fetch_congressional_record.py (CREC).

Recording is run with a stand-in for the GovInfo server (a patch of OpenerDirector.open BELOW the harness's own hook, so the harness
records exactly what a live run would have seen); replay is run WITHOUT the stand-in and with live sockets blocked, so any request that
escaped the cassette would fail. The comparison is on what the collector produced (the speeches and fetch-log rows of the SQLite
store) and on what it printed, which is the D-177 content-level equivalence. The three dates exercise the paths the collector
has: a session day (zip of floor pages), a non-session day (HTTP 404) and a malformed zip confirmed absent by the package summary.
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops"
COLLECTOR = ROOT / "fec" / "collect" / "fetch_congressional_record.py"
pytestmark = pytest.mark.skipif(not COLLECTOR.is_file(), reason="fec/ is not in this tree")

SECRET = "govinfo-" + "7c1d9e3a5b" * 3

SPEECH = ("Ms. DOE. Madam Speaker, I rise today to speak about the cost of credit for working families in my district and the "
          "role that member-owned financial cooperatives play in keeping that credit affordable, local and accountable. "
          "I urge my colleagues to consider the evidence before them and to support the measure under debate.")

DRIVER = r'''
import io, sys, urllib.error, urllib.request, zipfile
sys.path.insert(0, sys.argv[1])
import http_replay

SPEECH = sys.argv[2]


def page_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CREC-2026-01-05/html/CREC-2026-01-05-pt1-PgH100.htm", "<html><pre>\n  " + SPEECH + "\n</pre></html>")
        z.writestr("CREC-2026-01-05/html/CREC-2026-01-05-pt1-PgE100.htm", "<html><pre>\n  " + SPEECH + "\n</pre></html>")   # extensions: excluded
    return buf.getvalue()


class Upstream(io.BytesIO):   # a stand-in for www.govinfo.gov; present ONLY while recording
    def __init__(self, url, body):
        super().__init__(body)
        self.url, self.status, self.reason = url, 200, "OK"
        self.headers = http_replay._headers([("Content-Type", "application/zip" if url.endswith(".zip") else "text/html")])

    def geturl(self):
        return self.url


def not_found(url):
    return urllib.error.HTTPError(url, 404, "Not Found", http_replay._headers([("Content-Type", "text/html")]), io.BytesIO(b"<html>404</html>"))


def fake_open(self, fullurl, data=None, timeout=None):
    url = fullurl if isinstance(fullurl, str) else fullurl.full_url
    if url.endswith("CREC-2026-01-05.zip"):
        return Upstream(url, page_zip())
    if url.endswith("CREC-2026-01-06.zip"):
        raise not_found(url)
    if url.endswith("CREC-2026-01-07.zip"):
        return Upstream(url, b"<html>govinfo error page, HTTP 200</html>")        # GovInfo's redirect to an HTML error page
    if url.startswith("https://api.govinfo.gov/packages/CREC-2026-01-07/summary"):
        raise not_found(url)
    raise AssertionError("the collector asked for an unexpected URL: " + url)


import os
if os.environ.get("UPSTREAM") == "1":
    urllib.request.OpenerDirector.open = fake_open
sys.exit(http_replay.main(sys.argv[3:]))
'''


def make_root(base: Path) -> Path:
    root = base / "root"
    (root / "data" / "engine").mkdir(parents=True)
    (root / "votes").mkdir()
    (root / "data" / "engine" / "congestion.json").write_text(
        json.dumps([{"member": "Jane Doe", "chamber": "House", "bioguide": "D000001"}]), encoding="utf-8")
    (root / "votes" / "HSall_members.csv").write_text("bioguide_id,chamber,congress\nD000001,House,119\n", encoding="utf-8")
    return root


def rows(root: Path):
    with sqlite3.connect(root / "social_media.db") as con:
        return (con.execute("SELECT member,date,source,chamber,package,nwords,text FROM speeches ORDER BY package,rowid").fetchall(),
                con.execute("SELECT key,status FROM cr_fetch_log ORDER BY key").fetchall())


def test_the_crec_collector_replays_to_the_same_store_and_output_with_the_network_blocked(tmp_path):
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER, encoding="utf-8")
    cassette = tmp_path / "cas"

    def run(mode, upstream, name):
        base = tmp_path / name
        base.mkdir()
        root = make_root(base)
        env = {**os.environ, "MONEYBALL_ROOT": str(root), "MONEYBALL_DRAFT": str(base / "no-draft"), "GOVINFO_KEY": SECRET, "UPSTREAM": upstream}
        cmd = [sys.executable, str(driver), str(OPS / "compute"), SPEECH, mode, str(cassette), "--", str(COLLECTOR),
               "crec", "--since", "2026-01-05", "--until", "2026-01-07"]
        done = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=180, encoding="utf-8")
        return done, root

    rec, rec_root = run("record", "1", "record")
    assert rec.returncode == 0, rec.stderr + rec.stdout
    rep, rep_root = run("replay", "0", "replay")
    assert rep.returncode == 0, rep.stderr + rep.stdout
    assert "served 4, missed 0, unused 0" in rep.stderr                         # zip, 404, bad zip, package summary: the request set matched both ways

    speeches, log = rows(rep_root)
    assert (speeches, log) == rows(rec_root)
    assert len(speeches) == 1 and speeches[0][:6] == ("Jane Doe", "2026-01-05", "CREC", "house", "CREC-2026-01-05", len(SPEECH[len("Ms. DOE. "):].split()))
    assert log == [("crec:CREC-2026-01-05", "ok"), ("crec:CREC-2026-01-06", "nosession"), ("crec:CREC-2026-01-07", "nosession")]
    assert rec.stdout == rep.stdout                                              # the collector's own report is identical too

    on_disk = b"".join(p.read_bytes() for p in sorted(cassette.rglob("*")) if p.is_file())
    assert SECRET.encode() not in on_disk                                        # the X-Api-Key header never reached the cassette
