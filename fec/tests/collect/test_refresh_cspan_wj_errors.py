"""Washington Journal failures must say WHY (weekly 2026-10-18: `cspan_scrape.R failed with exit 1`).

refresh_cspan_wj.run_checked used to discard the R child's output when it raised, and
weekly_refresh.step() logged only the last 300 characters of the Python child's stderr, which on a
Python traceback is nothing but traceback.  The operator saw "failed with exit 1" and no cause.

run_checked now keeps the tail of the R child's stdout and stderr (streaming both live, unchanged) and
puts it in the RuntimeError; weekly_refresh logs the last 2000 characters of stderr, which holds the
whole error message.  weekly_refresh.py cannot be imported (module scope takes locks and runs the
refresh), so the real step() is executed from the source text over a stubbed child process, the same
way test_weekly_discovery_deferral does.
"""
import ast
import datetime
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest import mock

import pytest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))


def import_under_scratch_root(name):
    """Import `name` with MONEYBALL_ROOT on a scratch root whose roster is empty.

    refresh_cspan_wj imports members.fetch_interviews, which reads data/engine/congestion.json when it is
    imported, and paths.ROOT is fixed the first time paths is imported, so without the private data tree
    this suite could not even be collected.  The project modules the import loads are forgotten afterwards,
    so no other test inherits a module bound to the scratch root (the same helper sits in test_msnow_walk.py)."""
    root = tempfile.mkdtemp(prefix="moneyball_root_")
    before = dict(sys.modules)
    try:
        os.makedirs(os.path.join(root, "data", "engine"))
        Path(root, "data", "engine", "congestion.json").write_text("[]", encoding="utf-8")
        sys.modules.pop("paths", None)   # paths.ROOT is read once, at its first import
        with mock.patch.dict(os.environ, {"MONEYBALL_ROOT": root}):
            return importlib.import_module(name)
    finally:
        for key in [k for k in sys.modules if k not in before]:
            module = sys.modules[key]
            where = getattr(module, "__file__", None) or next(iter(getattr(module, "__path__", [])), "")
            if str(where).startswith(str(FEC)):   # project modules only; stdlib modules stay shared
                del sys.modules[key]
                parent, _, child = key.rpartition(".")
                if parent in before and getattr(before[parent], child, None) is module:
                    delattr(before[parent], child)
        if "paths" in before:
            sys.modules["paths"] = before["paths"]
        shutil.rmtree(root, ignore_errors=True)


wj = import_under_scratch_root("collect.refresh_cspan_wj")

WEEKLY_SOURCE = (FEC / "weekly_refresh.py").read_text(encoding="utf-8-sig")
WEEKLY_TREE = ast.parse(WEEKLY_SOURCE)


def fake_rscript(tmp_path, body):
    """A stand-in for `Rscript cspan_scrape.R ...`: run_checked sees [interpreter, script, ...]."""
    script = tmp_path / "cspan_scrape.R"
    script.write_text(body, encoding="utf-8")
    return [sys.executable, str(script)]


R_FAILURE = """
import sys
for n in range(1, 61):
    print("progress-%02d" % n, flush=True)
print("Error in value[[3L]](cond) : chromote could not start Chrome", file=sys.stderr, flush=True)
print("Calls: new_session -> ChromoteSession$new", file=sys.stderr, flush=True)
print("Execution halted", file=sys.stderr, flush=True)
sys.exit(1)
"""


def test_nonzero_exit_carries_the_last_lines_of_r_stderr_and_stdout(tmp_path):
    with pytest.raises(RuntimeError) as raised:
        wj.run_checked(fake_rscript(tmp_path, R_FAILURE), cwd=tmp_path)
    message = str(raised.value)
    assert message.splitlines()[0] == "cspan_scrape.R failed with exit 1"   # the existing headline survives
    assert "Error in value[[3L]](cond) : chromote could not start Chrome" in message
    assert "Execution halted" in message
    assert "progress-60" in message                       # stdout context is kept too
    assert "progress-20" not in message and "progress-21" in message   # last 40 lines of the stream, no more


def test_the_error_tail_is_bounded_so_the_weekly_log_line_can_hold_all_of_it(tmp_path):
    flood = "import sys\nfor n in range(500):\n    print('E' * 500, file=sys.stderr)\nsys.exit(3)\n"
    with pytest.raises(RuntimeError) as raised:
        wj.run_checked(fake_rscript(tmp_path, flood), cwd=tmp_path)
    assert str(raised.value).startswith("cspan_scrape.R failed with exit 3")
    assert len(str(raised.value)) <= 1950                 # weekly_refresh logs the last 2000 characters


def test_output_still_streams_to_the_parent_so_the_stage_log_is_unchanged(tmp_path, capfd):
    with pytest.raises(RuntimeError):
        wj.run_checked(fake_rscript(tmp_path, R_FAILURE), cwd=tmp_path)
    captured = capfd.readouterr()
    assert "progress-01" in captured.out and "progress-60" in captured.out   # every line, not just the tail
    assert "Execution halted" in captured.err


def test_success_returns_quietly_and_still_streams(tmp_path, capfd):
    assert wj.run_checked(fake_rscript(tmp_path, "print('all good', flush=True)"), cwd=tmp_path) is None
    assert "all good" in capfd.readouterr().out


def test_timeout_keeps_the_headline_and_what_the_child_said_before_it_was_killed(tmp_path):
    hang = "import sys, time\nprint('scraping 3 of 40', flush=True)\nprint('403 from CloudFront', file=sys.stderr, flush=True)\ntime.sleep(60)\n"
    started = time.monotonic()
    with pytest.raises(RuntimeError) as raised:
        wj.run_checked(fake_rscript(tmp_path, hang), cwd=tmp_path, timeout=2)
    assert time.monotonic() - started < 30
    message = str(raised.value)
    assert message.splitlines()[0] == "cspan_scrape.R timed out after 2s"
    assert "scraping 3 of 40" in message and "403 from CloudFront" in message


def test_an_unencodable_character_in_r_output_does_not_wedge_the_child(tmp_path, monkeypatch):
    """The parent's stdout can be a cp1252 file; a reader that died on the first bad character would
    stop draining the pipe and the R child would block forever on a full pipe."""
    class Narrow:
        encoding = "ascii"
        def write(self, text): text.encode("ascii")
        def flush(self): pass
    monkeypatch.setattr(sys, "stdout", Narrow())
    noisy = "import sys\nprint('caf\\u00e9 ' * 3000, flush=True)\nprint('done', flush=True)\nsys.exit(1)\n"
    with pytest.raises(RuntimeError) as raised:
        wj.run_checked(fake_rscript(tmp_path, noisy), cwd=tmp_path, timeout=30)
    assert "done" in str(raised.value)


class StrictSink:
    """A stage-log sink that is strict about its encoding, like a cp1252 pipe under weekly_refresh."""
    def __init__(self, encoding="ascii"):
        self.encoding, self.written = encoding, []
    def write(self, text):
        text.encode(self.encoding or "utf-8")
        self.written.append(text)
    def flush(self):
        pass


def test_an_unencodable_line_reaches_the_stage_log_with_the_character_replaced():
    sink, tail = StrictSink("ascii"), []
    wj._tee(iter(["café scraped\n", "plain line\n"]), sink, tail)
    assert sink.written == ["caf? scraped\n", "plain line\n"]    # was: the first line vanished from the log
    assert tail == ["café scraped", "plain line"]            # the failure tail keeps the original text


def test_a_sink_without_an_encoding_falls_back_to_utf8_replacement():
    sink = StrictSink(None)
    wj._tee(iter(["lone \ud800 surrogate\n"]), sink, [])
    assert sink.written == ["lone ? surrogate\n"]


def test_a_sink_that_fails_even_after_the_replacement_is_still_drained():
    class Broken:
        encoding = "ascii"
        def write(self, text): raise UnicodeEncodeError("ascii", text, 0, 1, "always")
        def flush(self): pass
    tail = []
    wj._tee(iter(["one\n", "two\n"]), Broken(), tail)             # must not raise: a dead reader wedges the child
    assert tail == ["one", "two"]


def test_run_checked_keeps_unencodable_lines_in_the_stage_log(tmp_path, monkeypatch):
    sink = StrictSink("ascii")
    monkeypatch.setattr(sys, "stdout", sink)
    script = "import sys\nprint('caf\\u00e9 line', flush=True)\nprint('done', flush=True)\nsys.exit(1)\n"
    with pytest.raises(RuntimeError):
        wj.run_checked(fake_rscript(tmp_path, script), cwd=tmp_path, timeout=30)
    assert "".join(sink.written).splitlines() == ["caf? line", "done"]


# ---- weekly_refresh.step(): the log line -----------------------------------------------------------

def function_source(name):
    node = next(n for n in WEEKLY_TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(WEEKLY_SOURCE, node)


def weekly_step_with_stderr(stderr):
    logs = []

    def run_bounded(argv, **kwargs):
        return SimpleNamespace(stdout="", stderr=stderr, returncode=1, timed_out=False, timeout_note="")

    spec = SimpleNamespace(argv=[sys.executable], env=None, phase="collect", container=None)
    namespace = {
        "bounded_subprocess": SimpleNamespace(run_bounded=run_bounded),
        "compute_engine": SimpleNamespace(launch=lambda *a, **k: spec, reap=lambda s: None),
        "subprocess": subprocess, "sys": sys, "os": os, "time": time, "datetime": datetime,
        "FE": str(FEC), "_LIVE_CHILD": [None], "_tolerated": [], "_soft_failures": [],
        "_producer_path": lambda name: name, "_chain_tail_is_audit_full": lambda: True, "log": logs.append,
        "_write_status": lambda **kw: None, "_emit": lambda *a, **k: None,
        "_hard_abort": lambda *a, **k: (_ for _ in ()).throw(AssertionError("soft step aborted")),
        "_lease_handoff_out": lambda: None, "_lease_handoff_in": lambda: None,
    }
    for name in ("_adopt_live_child", "_soft_fail", "step"):
        exec(function_source(name), namespace)
    assert namespace["step"]("interviews: Washington Journal (21d)", ["refresh_cspan_wj.py"]) is False
    return [line for line in logs if line.lstrip().startswith("stderr:")]


def test_weekly_log_shows_the_whole_error_message_not_just_its_last_300_characters():
    # run_checked's message ends with up to ~1850 characters of R output; the decisive line sits near
    # the top of it, well outside the old 300-character window.
    message = ("RuntimeError: cspan_scrape.R failed with exit 1\n--- stderr (last 40 lines) ---\n"
               "Error in value[[3L]](cond) : chromote could not start Chrome\n" + "Calls: frame\n" * 100 +
               "Execution halted\n")
    assert len(message) > 1000
    (line,) = weekly_step_with_stderr("Traceback (most recent call last):\n" + message)
    assert "Error in value[[3L]](cond) : chromote could not start Chrome" in line
    assert "Execution halted" in line


def test_weekly_log_line_is_still_bounded():
    (line,) = weekly_step_with_stderr("x" * 50_000)
    assert 1000 < len(line) <= 2100
