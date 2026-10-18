"""Which Python runs office_recalc (MELANGE_OFFICE_PYTHON), and what its soffice cleanup is allowed to kill.

B3 CI run 38882524829: the packaged app ran office_recalc.py under sys.executable, the bundled Python without `uno`, and
nothing read MELANGE_OFFICE_PYTHON (desktop_backend.py and smoke_office_runtime.py set it). Subprocess is replaced
throughout; no LibreOffice is needed. The one real process is the interpreter running the launcher snippet, to prove the
configured program directory reaches sys.path."""
import subprocess
import sys
import types
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OPS / "compute"))
import office_recalc as OR  # noqa: E402
import xl_engine as E  # noqa: E402

SCRIPT = str(OPS / "compute" / "office_recalc.py")


def recalc(tmp_path, calls):
    E.recalc_copy(tmp_path / "c.xlsx", expected_sha256="d" * 64, out=tmp_path / "v.xlsx", engine_name="libreoffice",
                  run=lambda argv, **kw: calls.append(argv))
    return calls[0]


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv(E.LOCAL_ENV, "1")


def test_the_configured_office_python_runs_office_recalc_with_its_program_dir_on_the_path(local, monkeypatch, tmp_path):
    program = tmp_path / "LibreOffice" / "program"
    program.mkdir(parents=True)
    office_python = program / "python.exe"
    office_python.write_text("", encoding="utf-8")
    monkeypatch.setenv("MELANGE_OFFICE_PYTHON", str(office_python))
    argv = recalc(tmp_path, [])
    assert argv[0] == str(office_python) and argv[0] != sys.executable
    assert argv[1:3] == ["-E", "-s"] and argv[3] == "-c"                       # no PYTHON* env, no user site: only what is named
    assert argv[5] == str(program) and argv[6] == SCRIPT                      # program\ is the one directory added, then the script
    assert "--in" in argv and "--receipt" in argv and argv.index("--in") > argv.index(SCRIPT)
    # run the launcher snippet for real, with this interpreter standing in for the office one
    probe = tmp_path / "probe.py"
    probe.write_text("import sys\nprint(sys.path[0]); print(sys.argv[1:])\n", encoding="utf-8")
    done = subprocess.run([sys.executable, *argv[1:5], argv[5], str(probe), "--in", "x"], capture_output=True, text=True, check=True)
    assert done.stdout.splitlines() == [str(program), "['--in', 'x']"]


def test_unset_keeps_the_current_behaviour(local, monkeypatch, tmp_path):
    monkeypatch.delenv("MELANGE_OFFICE_PYTHON", raising=False)
    assert recalc(tmp_path, [])[:2] == [sys.executable, SCRIPT]


@pytest.mark.parametrize("value", ["python.exe", "missing/absolute/python.exe"])
def test_a_configured_interpreter_that_is_not_an_existing_absolute_file_is_refused(local, monkeypatch, tmp_path, value):
    monkeypatch.setenv("MELANGE_OFFICE_PYTHON", value if not value.startswith("missing") else str(tmp_path / value))
    calls = []
    with pytest.raises(RuntimeError, match="MELANGE_OFFICE_PYTHON"):
        recalc(tmp_path, calls)
    assert calls == []                                                          # nothing launched


class FakeProc:
    def __init__(self, pid, polled):
        self.pid, self.polled = pid, polled

    def poll(self):
        return self.polled

    def wait(self, timeout=None):
        return 0


def cleanup_world(monkeypatch, tmp_path, polled):
    """libreoffice_calculate with a fake subprocess module and no `uno`: it launches, fails at `import uno`, and cleans up."""
    seen = {"run": [], "popen": []}

    def run(argv, **kw):
        seen["run"].append((argv, kw))
        return types.SimpleNamespace(stdout="LibreOffice 25.2.3.2 520(Build:2)", stderr="", returncode=0)

    def popen(argv, **kw):
        seen["popen"].append((argv, kw))
        return FakeProc(4242, polled)
    fake = types.SimpleNamespace(run=run, Popen=popen, DEVNULL=-3, CREATE_NO_WINDOW=0x08000000, TimeoutExpired=subprocess.TimeoutExpired)
    monkeypatch.setattr(OR, "subprocess", fake)
    monkeypatch.delattr(OR.os, "killpg", raising=False)                         # the Windows branch, whatever the host
    monkeypatch.setitem(sys.modules, "uno", None)                               # `import uno` fails
    work = tmp_path / "lo"
    work.mkdir()
    with pytest.raises(OR.EngineError):
        OR.libreoffice_calculate(tmp_path / "c.xlsx", {}, timeout=60, work=work, soffice="soffice-fixture")
    return seen


def test_cleanup_kills_the_tracked_pid_tree_and_never_by_name(monkeypatch, tmp_path):
    seen = cleanup_world(monkeypatch, tmp_path, polled=None)
    (popen_argv, popen_kw), = seen["popen"]
    assert popen_kw["creationflags"] == 0x08000000                              # windowless soffice
    assert seen["run"][0][1]["creationflags"] == 0x08000000                     # and its --version probe
    kills = [argv for argv, _ in seen["run"][1:]]
    assert kills == [["taskkill.exe", "/PID", "4242", "/T", "/F"]]              # exactly Popen's pid, tree, force
    flat = " ".join(" ".join(a) for a in kills).lower()
    assert "/im" not in flat and "soffice" not in flat and "/fi" not in flat   # no image-name or filter selection


def test_cleanup_leaves_an_already_exited_process_alone(monkeypatch, tmp_path):
    seen = cleanup_world(monkeypatch, tmp_path, polled=0)                       # exited: the pid may be reused, so no taskkill
    assert len(seen["run"]) == 1                                                # only the --version probe
