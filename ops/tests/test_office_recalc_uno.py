"""The LibreOffice step of office_recalc (libreoffice_calculate / _read_results) against a FAKE soffice and a FAKE `uno`.

Nothing here proves LibreOffice computes anything: it proves the code around it, which otherwise first runs in the
container: the version gate, the connect loop, the load options (read-only, hidden, macros off, no link update), the
Excel document settings, the mapping of every result kind (number, text, logical, error) and of LibreOffice's error
codes, closing without storing, and the timeout paths. The real LibreOffice runs in the smoke test of compute-office.yml."""
import hashlib
import sys
import time
import types
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OPS / "compute"))
import office_recalc as OR  # noqa: E402
import xl_equivalence as X  # noqa: E402

ALLOW = ["_XLFN.XLOOKUP"]
LOGICAL_KEY = 99
ERRORS = {"#DIV/0!": 532}


class NoConnect(Exception):
    pass


class FakeCell:
    def __init__(self, kind, value):
        self.kind, self.value = kind, value
        self.NumberFormat = LOGICAL_KEY if kind == "b" else 0
        self.FormulaResultType2 = 2 if kind == "str" else 4 if kind == "e" else 1

    def getError(self):
        if self.kind == "e":
            return ERRORS.get(self.value, self.value)           # an int not in ERRORS is passed through as a raw LibreOffice code
        return 0

    def getString(self):
        return self.value

    def getValue(self):
        return float(self.value) if self.kind in ("n", "b") else 0.0


class FakeSheet:
    def __init__(self, cells):
        self.cells = cells

    def getCellByPosition(self, col, row):
        return FakeCell(*self.cells[f"{chr(65 + col)}{row + 1}"])


class FakeDoc:
    def __init__(self, sheets, calc_delay=0.0):
        self.sheets, self.props, self.calc_delay = sheets, {}, calc_delay
        self.calculated = self.closed = None
        self.Sheets = types.SimpleNamespace(getByName=lambda name: FakeSheet(self.sheets[name]))

    def setPropertyValue(self, name, value):
        self.props[name] = value

    def getPropertyValue(self, name):
        return self.props[name]

    def getNumberFormats(self):
        return types.SimpleNamespace(getByKey=lambda key: types.SimpleNamespace(Type=1024 if key == LOGICAL_KEY else 0))

    def calculateAll(self):
        if self.calc_delay:
            time.sleep(self.calc_delay)
            raise RuntimeError("DisposedException: the office was killed")
        self.calculated = True

    def close(self, deliver):
        self.closed = deliver


class FakeUno:
    """Installs fake `uno` and `com.sun.star.beans` modules; `connect_after` = failed connection attempts before it works."""

    def __init__(self, monkeypatch, doc, connect_after=1):
        self.doc, self.loaded, self.attempts, self.terminated = doc, [], 0, False
        desktop = types.SimpleNamespace(
            loadComponentFromURL=lambda url, target, flags, props: self.loaded.append((url, target, flags, {p.Name: p.Value for p in props})) or doc,
            terminate=lambda: setattr(self, "terminated", True))

        def resolve(url):
            self.attempts += 1
            if self.attempts <= connect_after:
                raise NoConnect()
            return types.SimpleNamespace(ServiceManager=types.SimpleNamespace(createInstanceWithContext=lambda name, ctx: desktop))
        resolver = types.SimpleNamespace(resolve=resolve)
        local = types.SimpleNamespace(ServiceManager=types.SimpleNamespace(createInstanceWithContext=lambda name, ctx: resolver))
        uno = types.ModuleType("uno")
        uno.getComponentContext = lambda: local
        uno.getClass = lambda name: NoConnect
        uno.Any = lambda type_name, value: value
        uno.systemPathToFileUrl = lambda path: "file:///" + str(path).replace(chr(92), "/")
        beans = types.ModuleType("com.sun.star.beans")
        beans.PropertyValue = lambda **kw: types.SimpleNamespace(**kw)
        for name, mod in (("uno", uno), ("com", types.ModuleType("com")), ("com.sun", types.ModuleType("com.sun")),
                          ("com.sun.star", types.ModuleType("com.sun.star")), ("com.sun.star.beans", beans)):
            monkeypatch.setitem(sys.modules, name, mod)


@pytest.fixture
def probe(tmp_path):
    p = tmp_path / "in" / "probe.xlsx"
    p.parent.mkdir()
    X.write_probe(p)
    return p


@pytest.fixture
def fake_soffice(tmp_path, monkeypatch):
    """An executable named soffice: prints a version for --version, otherwise idles (short, so an orphan dies by itself)."""
    script = tmp_path / "fake_soffice.py"
    script.write_text("import os, sys, time\n"
                      "if '--version' in sys.argv:\n"
                      "    print(os.environ.get('FAKE_SOFFICE_VERSION', 'LibreOffice 25.2.3.2 520(Build:2)'))\n"
                      "else:\n"
                      "    time.sleep(6)\n", encoding="utf-8")
    if sys.platform == "win32":
        exe = tmp_path / "soffice.cmd"
        exe.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        exe = tmp_path / "soffice"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        exe.chmod(0o755)
    return str(exe)


def probe_sheets(probe_rows=None):
    return {"Data": {}, "Probe": {c: (k, v) for c, _f, k, v, _lo in (probe_rows or X.PROBE)}}


def run(probe, tmp_path, fake_soffice, **kw):
    out = tmp_path / "out.xlsx"
    doc = OR.run(probe, hashlib.sha256(probe.read_bytes()).hexdigest(), out, None, allow=ALLOW, soffice=fake_soffice, **kw)
    return doc, out


def test_every_result_kind_goes_through_uno_into_the_view_copy(probe, tmp_path, monkeypatch, fake_soffice):
    sheets = probe_sheets()
    sheets["Probe"]["B7"] = ("e", "#DIV/0!")                                   # LibreOffice reports an error as getError() == 532
    fake = FakeUno(monkeypatch, FakeDoc(sheets))
    doc, out = run(probe, tmp_path, fake_soffice, timeout=120)
    got = OR.read_cached_formula_values(out)["Probe"]
    assert {c: got[c][:2] for c in got} == {c: (k, v) for c, (k, v) in sheets["Probe"].items()}
    assert {got[c][0] for c in got} == {"n", "str", "b", "e"} and got["B14"][:2] == ("str", "")      # the empty string stays text
    url, target, flags, props = fake.loaded[0]
    assert target == "_blank" and url.endswith("calc-copy.xlsx")                # a COPY in the work dir, never the input
    assert props == {"Hidden": True, "ReadOnly": True, "MacroExecutionMode": 0, "UpdateDocMode": 0}
    assert fake.doc.calculated is True and fake.doc.closed is True and fake.terminated   # calculateAll, then closed without storing
    assert fake.attempts == 2                                                  # one NoConnectException, then connected
    assert {k: fake.doc.props[k] for k in ("Wildcards", "RegularExpressions", "MatchWholeCell", "IsIterationEnabled")} == {
        "Wildcards": True, "RegularExpressions": False, "MatchWholeCell": True, "IsIterationEnabled": False}
    engine = doc["engine"]
    assert engine["version"] == "25.2.3" and engine["calculation_request"] == "calculateAll" and engine["document_stored"] is False
    assert engine["document_settings"]["Wildcards"] is True and doc["cached_by_type"] == {"b": 1, "e": 1, "n": 14, "str": 3}


def test_an_error_without_an_excel_equivalent_fails_with_exit_3_and_writes_nothing(probe, tmp_path, monkeypatch, fake_soffice):
    sheets = probe_sheets()
    sheets["Probe"]["B7"] = ("e", 502)                                         # Err:502, invalid argument: #NUM! or #VALUE! depending on the function
    FakeUno(monkeypatch, FakeDoc(sheets))
    with pytest.raises(OR.Unmappable, match="Probe!B7 Err:502"):
        run(probe, tmp_path, fake_soffice, timeout=120)
    assert not (tmp_path / "out.xlsx").exists() and OR.Unmappable.code == OR.UNMAPPABLE


def test_a_libreoffice_older_than_24_8_is_refused_before_it_starts(probe, tmp_path, monkeypatch, fake_soffice):
    monkeypatch.setenv("FAKE_SOFFICE_VERSION", "LibreOffice 7.6.7.2 60(Build:2)")
    fake = FakeUno(monkeypatch, FakeDoc(probe_sheets()))
    with pytest.raises(OR.EngineError, match="older than 24.8"):
        run(probe, tmp_path, fake_soffice, timeout=120)
    assert fake.attempts == 0 and not fake.loaded


def test_no_connection_within_the_timeout_is_a_timeout_not_an_engine_error(probe, tmp_path, monkeypatch, fake_soffice):
    FakeUno(monkeypatch, FakeDoc(probe_sheets()), connect_after=10 ** 9)
    started = time.monotonic()
    with pytest.raises(OR.RecalcTimeout):
        OR.libreoffice_calculate(probe, {"Probe": {"addrs": ["B1"]}}, timeout=1, work=tmp_path / "lo", soffice=fake_soffice)
    assert time.monotonic() - started < 20 and OR.RecalcTimeout.code == OR.TIMEOUT


def test_a_calculation_that_outlives_the_timeout_is_a_timeout(probe, tmp_path, monkeypatch, fake_soffice):
    FakeUno(monkeypatch, FakeDoc(probe_sheets(), calc_delay=3))                # the office "dies" at 3 s, the budget is 1 s
    with pytest.raises(OR.RecalcTimeout):
        OR.libreoffice_calculate(probe, {"Probe": {"addrs": ["B1"]}}, timeout=1, work=tmp_path / "lo", soffice=fake_soffice)


def test_a_uno_failure_that_is_not_a_timeout_is_an_engine_error(probe, tmp_path, monkeypatch, fake_soffice):
    doc = FakeDoc(probe_sheets())
    doc.calculateAll = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    FakeUno(monkeypatch, doc)
    with pytest.raises(OR.EngineError, match="RuntimeError: boom"):
        OR.libreoffice_calculate(probe, {"Probe": {"addrs": ["B1"]}}, timeout=120, work=tmp_path / "lo", soffice=fake_soffice)
