"""rewrite_percentrank (D-174, Paul's 2026-10-12 approval): PERCENTRANK(range,x,5) -> TRUNC(PERCENTRANK(range,x,15),5).

Synthetic workbooks only: the shape of 'Blended Model' (533 rows over D, G, E, F, O; 494 IFERROR rows over H), no real
workbook and no Excel. The tests pin the exact count, that nothing but the <f> text moves, idempotence, and each refusal."""
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1]
ROOT = OPS.parent
sys.path.insert(0, str(OPS / "compute"))
sys.path.insert(0, str(ROOT / "fec"))
import office_recalc as OR  # noqa: E402
import rewrite_percentrank as RW  # noqa: E402
import xl_equivalence as X  # noqa: E402

COLUMNS = (("J", "D"), ("K", "G"), ("L", "E"), ("M", "F"), ("P", "O"))        # formula column, range column
IFERROR_ROWS = 494
NOW = datetime(2026, 10, 13, 1, 2, 3)


def make_book(path, *, iferror_rows=IFERROR_ROWS, form_a_rows=0, extra=b"", other_cells=b""):
    """A two-sheet package. 'Blended Model' holds the 3,159 old-form formulas (form_a_rows of the J column in form A) and a
    SUM column; 'Other' holds one formula and a text cell that mentions PERCENTRANK. One part is STORED, dates differ."""
    rows = []
    for r in range(3, 536):
        cells = []
        for k, (col, rng) in enumerate(COLUMNS):
            call = f"${rng}$3:${rng}$535,{rng}{r}"
            text = f"TRUNC(PERCENTRANK({call},15),5)" if col == "J" and r - 3 < form_a_rows else f"PERCENTRANK({call},5)"
            cells.append(f'<c r="{col}{r}" s="107"><f>{text}</f><v>0.5</v></c>')
        if r - 3 < iferror_rows:
            cells.append(f'<c r="N{r}" s="107"><f>IFERROR(PERCENTRANK($H$3:$H$535,H{r},5),L{r})</f><v>0.25</v></c>')
        cells.append(f'<c r="S{r}"><f>SUM(A{r}:B{r})</f><v>3</v></c>')
        rows.append(f'<row r="{r}">' + "".join(cells) + "</row>")
    head = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="%s"><sheetData>' % OR.MAIN_NS
    blended = (head + "".join(rows) + extra.decode() + "</sheetData></worksheet>").encode()
    other = (head + '<row r="1"><c r="A1"><f>SUM(1,2)</f><v>3</v></c><c r="A2" t="inlineStr"><is><t>PERCENTRANK note</t></is></c>'
             + other_cells.decode() + "</row></sheetData></worksheet>").encode()
    skeleton = Path(path).with_name("skeleton.xlsx")
    X.write_probe(skeleton)
    with zipfile.ZipFile(skeleton) as zin, zipfile.ZipFile(path, "w") as zout:
        zout.comment = b"synthetic"
        for n, info in enumerate(zin.infolist()):
            body = zin.read(info)
            if info.filename == "xl/worksheets/sheet1.xml":
                body = blended
            elif info.filename == "xl/worksheets/sheet2.xml":
                body = other
            elif info.filename == "xl/workbook.xml":
                body = body.replace(b'name="Data"', b'name="Blended Model"').replace(b'name="Probe"', b'name="Other"')
            zi = zipfile.ZipInfo(info.filename, (2026, 1, 1 + n, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_STORED if info.filename == "xl/styles.xml" else zipfile.ZIP_DEFLATED
            zout.writestr(zi, body, compress_type=zi.compress_type)
    skeleton.unlink()
    return Path(path)


@pytest.fixture
def book(tmp_path):
    return make_book(tmp_path / "book.xlsx")


def sha(path):
    return OR.sha256_file(path)


def listing(directory):
    return sorted(p.name for p in Path(directory).iterdir())


def formulas(path, part="xl/worksheets/sheet1.xml"):
    with zipfile.ZipFile(path) as zf:
        return {m.group(1).decode(): m.group(2).decode() for m in re.finditer(rb'<c r="([A-Z]+[0-9]+)"[^>]*><f>([^<]*)</f>', zf.read(part))}


# ---------------------------------------------------------------------------------------------------- dry run

def test_dry_run_counts_per_column_and_pattern_and_writes_nothing(book, tmp_path, capsys):
    before, files = sha(book), listing(tmp_path)
    assert RW.main([str(book)]) == 0
    text = capsys.readouterr().out
    for col in "DEFGO":
        assert re.search(rf"Blended Model\s+{col}\s+bare\s+533\n", text)
    assert re.search(r"Blended Model\s+H\s+iferror\s+494\n", text)
    assert "to rewrite: 3159 (expected 3159) | already form A: 0 | unrecognised PERCENTRANK formulas: 0 | mentions outside <f>: 1" in text
    assert "pipeline lock" in text and "not live" in text and "DRY RUN: nothing written" in text
    assert sha(book) == before and listing(tmp_path) == files                  # no backup, no temp file, no output
    found = RW.scan(book)
    assert found["old"] == RW.EXPECTED == 3159 and found["already_form_a"] == 0 and not found["unrecognised"]
    assert found["by_sheet_column_shape"] == {("Blended Model", c, "bare"): 533 for c in "DEFGO"} | {("Blended Model", "H", "iferror"): 494}


@pytest.mark.parametrize("iferror_rows", [IFERROR_ROWS - 1, IFERROR_ROWS + 1])
def test_any_other_count_is_refused_in_dry_run_and_apply(tmp_path, capsys, iferror_rows):
    wrong = make_book(tmp_path / "wrong.xlsx", iferror_rows=iferror_rows)
    files = listing(tmp_path)
    assert RW.main([str(wrong)]) == 2
    assert "expected exactly 3159" in capsys.readouterr().err
    assert RW.main([str(wrong), "--apply"]) == 2
    with pytest.raises(OR.Refused, match=f"found {3159 - IFERROR_ROWS + iferror_rows} old"):
        RW.apply(wrong)
    assert listing(tmp_path) == files                                          # refused before anything was written


def test_a_formula_shape_it_does_not_know_is_refused_even_when_the_count_is_right(tmp_path):
    for extra in (b'<row r="900"><c r="A900"><f>PERCENTRANK($D$3:$D$535,D3)</f></c></row>',                    # default significance
                  b'<row r="900"><c r="A900"><f>_xlfn.PERCENTRANK.INC($D$3:$D$535,D3,5)</f></c></row>',
                  b'<row r="900"><c r="A900"><f>percentrank($D$3:$D$535,D3,5)</f></c></row>',
                  b'<row r="900"><c r="A900"><f>PERCENTRANK($D$3:$D$535,D3,5)+1</f></c></row>',
                  b'<row r="900"><c r="A900"><f t="shared" ref="A900:A901" si="0">PERCENTRANK($D$3:$D$535,D3,5)</f></c></row>'):
        odd = make_book(tmp_path / "odd.xlsx", extra=extra)
        with pytest.raises(OR.Refused, match="shape this tool does not recognise"):
            RW.apply(odd)
        assert RW.main([str(odd)]) == 2
        odd.unlink()


def test_a_half_converted_workbook_is_refused(tmp_path):
    half = make_book(tmp_path / "half.xlsx", form_a_rows=10)
    assert RW.scan(half)["old"] == 3149 and RW.scan(half)["already_form_a"] == 10
    with pytest.raises(OR.Refused, match="found 3149 old and 10 already form A"):
        RW.apply(half)


# ---------------------------------------------------------------------------------------------------- apply

def test_apply_to_a_new_file_changes_only_the_f_text_of_the_matching_formulas(book, tmp_path):
    out = tmp_path / "new.xlsx"
    original = book.read_bytes()
    manifest = RW.apply(book, out=out, now=NOW)
    assert book.read_bytes() == original                                       # --out leaves the input alone
    assert manifest["rewritten_formula_count"] == 3159 and manifest["rewritten_parts"] == ["xl/worksheets/sheet1.xml"]
    with zipfile.ZipFile(book) as a, zipfile.ZipFile(out) as b:
        assert [i.filename for i in a.infolist()] == [i.filename for i in b.infolist()]          # same parts, same order
        assert b.comment == a.comment == b"synthetic"
        for ia, ib in zip(a.infolist(), b.infolist()):
            assert (ia.date_time, ia.compress_type, ia.external_attr, ia.create_system) == \
                   (ib.date_time, ib.compress_type, ib.external_attr, ib.create_system), ia.filename
            if ia.filename != "xl/worksheets/sheet1.xml":
                assert a.read(ia) == b.read(ib), ia.filename                   # every other part: byte for byte
        old, new = a.read("xl/worksheets/sheet1.xml"), b.read("xl/worksheets/sheet1.xml")
    assert RW.strip_formulas(old) == RW.strip_formulas(new) and old != new      # outside the <f> text the sheet is identical
    before, after = formulas(book), formulas(out)
    assert before.keys() == after.keys()
    changed = {a for a in before if before[a] != after[a]}
    assert len(changed) == 3159 and all(not a.startswith("S") for a in changed)  # the SUM column is untouched
    assert before["J3"] == "PERCENTRANK($D$3:$D$535,D3,5)" and after["J3"] == "TRUNC(PERCENTRANK($D$3:$D$535,D3,15),5)"
    assert before["N10"] == "IFERROR(PERCENTRANK($H$3:$H$535,H10,5),L10)"
    assert after["N10"] == "IFERROR(TRUNC(PERCENTRANK($H$3:$H$535,H10,15),5),L10)"            # the wrapper stays
    for a in changed:
        assert after[a] == re.sub(r"PERCENTRANK\(([^,]+),([^,]+),5\)", r"TRUNC(PERCENTRANK(\1,\2,15),5)", before[a])
    assert RW.scan(out)["already_form_a"] == 3159 and RW.scan(out)["old"] == 0
    with zipfile.ZipFile(out) as zf:
        other = zf.read("xl/worksheets/sheet2.xml")
    assert b"PERCENTRANK note" in other and b"SUM(1,2)" in other               # the text mention and the other sheet are untouched


def test_in_place_apply_writes_a_named_pre_backup_with_a_manifest_and_replaces_atomically(book, tmp_path):
    original = book.read_bytes()
    manifest = RW.apply(book, now=NOW)
    backup = tmp_path / "book.PRE-PERCENTRANK-TRUNC-20261013_010203.xlsx"
    assert re.fullmatch(r"book\.PRE-[A-Z0-9-]+-\d{8}_\d{6}\.xlsx", backup.name)           # the existing backups' naming
    assert backup.read_bytes() == original                                     # a byte copy of what was there
    assert sorted(listing(tmp_path)) == sorted(["book.xlsx", backup.name, backup.name + ".manifest.json"])   # no temp file left
    saved = json.loads((tmp_path / (backup.name + ".manifest.json")).read_text())
    assert saved == manifest and saved["schema"] == RW.SCHEMA
    assert saved["source"]["sha256"] == saved["backup"]["sha256"] == OR.sha256_file(backup)
    assert saved["output"]["sha256"] == sha(book) != saved["source"]["sha256"] and saved["output"]["path"] == str(book)
    with zipfile.ZipFile(backup) as a, zipfile.ZipFile(book) as b:
        for info in a.infolist():
            assert saved["parts"][info.filename] == {"before": hashlib.sha256(a.read(info)).hexdigest(),
                                                     "after": hashlib.sha256(b.read(info.filename)).hexdigest()}
    assert [n for n, v in saved["parts"].items() if v["before"] != v["after"]] == ["xl/worksheets/sheet1.xml"]


def test_second_run_finds_nothing_and_writes_nothing(book, tmp_path, capsys):
    RW.apply(book, now=NOW)
    after, files = sha(book), listing(tmp_path)
    assert RW.apply(book, now=datetime(2026, 10, 13, 1, 2, 4)) is None
    assert RW.main([str(book), "--apply"]) == 0 and "nothing to do" in capsys.readouterr().out
    assert RW.main([str(book)]) == 0
    assert "to rewrite: 0 (expected 3159) | already form A: 3159" in capsys.readouterr().out
    assert sha(book) == after and listing(tmp_path) == files


def test_a_failed_verification_puts_nothing_in_place(book, tmp_path, monkeypatch, capsys):
    real = RW.write_package

    def corrupt(src, dst, rewritten):
        real(src, dst, {**rewritten, "xl/styles.xml": b"tampered"})            # a part that must not change
    monkeypatch.setattr(RW, "write_package", corrupt)
    original = book.read_bytes()
    assert RW.main([str(book), "--apply"]) == OR.VERIFY
    assert "FAILED" in capsys.readouterr().err
    assert book.read_bytes() == original
    assert not [n for n in listing(tmp_path) if n.endswith(".tmp") or n.endswith(".manifest.json")]


def test_bad_options_and_missing_input_are_refused(book, tmp_path):
    assert RW.main([str(tmp_path / "missing.xlsx")]) == 2
    assert RW.main([str(book), "--out", str(tmp_path / "o.xlsx")]) == 2          # --out without --apply
    for bad in ("lower", "has space", ""):
        with pytest.raises(OR.Refused, match="--tag"):
            RW.apply(book, tag=bad)
    (tmp_path / "junk.xlsx").write_bytes(b"not a zip")
    assert RW.main([str(tmp_path / "junk.xlsx")]) == 2 and RW.main([str(tmp_path / "junk.xlsx"), "--apply"]) == 2


# ---------------------------------------------------------------------------------------------------- the --apply refusals

def run_stamped(book, **kw):
    return RW.apply(book, now=NOW, **kw)


def test_apply_refuses_while_the_pipeline_lock_is_live(book, tmp_path):
    import pipeline_lock as PL
    lock = tmp_path / ".pipeline_run.lock"
    before, files = sha(book), None
    with PL.pipeline_run_lock(lock, owner_kind="test"):                        # fec/pipeline_lock.py itself takes the lease
        files = listing(tmp_path)
        with pytest.raises(OR.Refused, match="is live"):
            run_stamped(book)
        assert RW.main([str(book), "--apply"]) == 2
        assert RW.main([str(book)]) == 0                                       # a dry run only reads, and says so:
        assert listing(tmp_path) == files
    assert sha(book) == before
    assert run_stamped(book)["rewritten_formula_count"] == 3159                # the lease is gone: the same call goes through


def test_a_dead_owners_lease_is_not_live_but_a_fresh_malformed_one_is(book, tmp_path):
    import pipeline_lock as PL
    lock = tmp_path / ".pipeline_run.lock"
    dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True, check=True).stdout.strip()
    lock.write_text(json.dumps({"pid": int(dead), "token": "t", "started_utc": 1.0, "owner_kind": "test", "proc_start": 1.0, "host": PL.host_identity()}))
    assert RW.pipeline_lock_is_live(lock) is False                            # "host": POSIX judges by pid only a lease written from its own pid space
    lock.write_text("")                                                        # created a moment ago, owner not written yet
    assert RW.pipeline_lock_is_live(lock) is True
    with pytest.raises(OR.Refused, match="is live"):
        run_stamped(book)
    lock.write_text(json.dumps({"pid": int(dead), "token": "t", "started_utc": 1.0, "owner_kind": "test", "proc_start": 1.0, "host": PL.host_identity()}))
    assert run_stamped(book)["rewritten_formula_count"] == 3159                # the stale lease does not block (it is not removed)
    assert lock.exists()


def test_apply_refuses_while_the_workbook_is_open(book, tmp_path):
    owner = tmp_path / "~$ook.xlsx"                                            # Excel drops leading characters of the name
    owner.write_bytes(b"\x00")
    other = tmp_path / "~$Unrelated Workbook.xlsx"                             # another workbook's owner file does not count
    other.write_bytes(b"\x00")
    assert RW.excel_owner_files(book) == [owner]
    before = sha(book)
    with pytest.raises(OR.Refused, match="open in Excel"):
        run_stamped(book)
    owner.unlink()
    assert RW.excel_owner_files(book) == []
    assert RW.apply(book, out=tmp_path / "ok.xlsx", now=NOW)["rewritten_formula_count"] == 3159     # the unrelated owner file is ignored
    assert sha(book) == before


def hold_exclusively(path):
    handle = open(path, "r+b")
    if os.name == "nt":
        import msvcrt
        handle.seek(RW.LOCK_AT)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


def test_apply_refuses_when_the_file_cannot_be_opened_exclusively(book, tmp_path):
    assert RW.openable_exclusively(book)
    before, files = sha(book), listing(tmp_path)
    holder = hold_exclusively(book)
    try:
        assert not RW.openable_exclusively(book)
        with pytest.raises(OR.Refused, match="cannot be opened exclusively"):
            run_stamped(book)
        assert RW.main([str(book), "--apply"]) == 2
    finally:
        holder.close()
    assert sha(book) == before and listing(tmp_path) == files
    assert RW.openable_exclusively(book)


def test_apply_refuses_when_an_output_already_exists(book, tmp_path):
    out = tmp_path / "taken.xlsx"
    out.write_bytes(b"precious")
    with pytest.raises(OR.Refused, match="--out must be a new .xlsx"):
        run_stamped(book, out=out)
    assert out.read_bytes() == b"precious"
    for bad in (tmp_path / "x.xls", book):                                     # wrong suffix; the input itself
        with pytest.raises(OR.Refused, match="--out must be a new .xlsx"):
            run_stamped(book, out=bad)
    taken = tmp_path / "book.PRE-PERCENTRANK-TRUNC-20261013_010203.xlsx"
    taken.write_bytes(b"an earlier backup")
    with pytest.raises(OR.Refused, match="PRE-backup already exists"):
        run_stamped(book)
    assert taken.read_bytes() == b"an earlier backup"
    taken.unlink()
    (tmp_path / (taken.name + ".manifest.json")).write_text("{}")
    with pytest.raises(OR.Refused, match="PRE-backup already exists"):
        run_stamped(book)
    assert listing(tmp_path) == sorted(["book.xlsx", "taken.xlsx", taken.name + ".manifest.json"])


# ---------------------------------------------------------------------------------------------------- no emitter writes the old form

def test_no_source_in_fec_emits_the_old_formula_text():
    """The audit of 2026-10-12 found no emitter of PERCENTRANK(range,x,5) formula text in fec/ (the workbook's formulas predate
    the scripts, and survive every openpyxl round trip as text). This keeps it that way: a script that starts writing the old
    form would put the LibreOffice divergence (tdf#104981) back."""
    old = re.compile(r"PERCENTRANK\([^)]*,\s*[^,)]+,\s*5\s*\)")
    for line in ("=PERCENTRANK($D$3:$D$535,D3,5)", "f'=PERCENTRANK({rng},{x}, 5)'", "IFERROR(PERCENTRANK($H$3:$H$535,H3,5),L3)"):
        assert old.search(line), line                                           # the tripwire can fire
    for line in ("Leg% = PERCENTRANK(O,5)", "'PERCENTRANK',", "TRUNC(PERCENTRANK($D$3:$D$535,D3,15),5)"):
        assert not old.search(line), line                                       # and is quiet on prose, the allowlist and form A
    if not (ROOT / "fec").is_dir():
        pytest.skip("no fec/ in this checkout")
    found, scanned = [], 0
    for path in sorted((ROOT / "fec").rglob("*")):
        if path.suffix in {".py", ".ps1", ".cjs", ".mjs", ".R", ".vbs", ".bas"} and path.is_file():
            scanned += 1
            for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if old.search(line):
                    found.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:100]}")
    assert scanned > 500 and not found, (scanned, found)
