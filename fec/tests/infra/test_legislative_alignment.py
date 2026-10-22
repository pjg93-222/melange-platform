# -*- coding: utf-8 -*-
"""TDD guard for the 'Legislative Score - Normalized' row-alignment bug (2026-07-15).

CONFIRMED BUG
-------------
The 'Legislative Score - Normalized' sheet was misaligned by ONE data row against
'Legislative Score - Raw'.  For every value column the normalized cell at data-row r held the
normalization of the ALPHABETICAL PREDECESSOR (raw row r-1) instead of the member's OWN raw row r.
The displacement is baked in two ways:
  * ~24k cells are pasted LITERAL values equal to  RAW[r-1]/colmax  (a one-row copy-down shift), and
  * ~47k cells are FORMULAS  =IFERROR('Legislative Score - Raw'!<col><r-1>/MAX(...),0)  whose
    relative reference points one row too high.
Evidence: current NRM r15 ('Alexandria Ocasio-Cortez') col7 = 0.194595 = Alex Padilla's (RAW r14)
value, while AOC's own RAW col7 = 2.24 normalizes higher.

DERIVED NORMALIZATION RULE (verified against the surviving aligned cells AND the formula text)
----------------------------------------------------------------------------------------------
Every value column is a signed-magnitude / MAX normalization.  There are NO negative values in any
value column, so the rule reduces to:
        NRM[r][c] = 0.0                     if colmax(c) == 0
                  = RAW[r][c] / colmax(c)   otherwise      (IFERROR(...,0) semantics)
where colmax(c) = max(0, max RAW[r][c] over data rows 3..535).  (Excel's Table1[..] MAX spans rows
2..535, but the weight in row 2 never exceeds the data max for any data-bearing column, so rows
3..535 reproduces every column's denominator exactly; the 6 explicit-range formula columns
99/101/103/145/147/149 already use $..$3:$..$535.)

COLUMNS DELIBERATELY EXCLUDED (a different or underivable rule -- left unchanged by the repair)
----------------------------------------------------------------------------------------------
  * col 144 ('None'): a MIN-MAX feature-scaling formula (value-min)/(max-min) over a DIFFERENT raw
    column (Party_Leadership); it already references the member's own row (not shifted).
  * cols 146,148,150,151,152 (CFPB Reach 'Average'/'Success Rate' sub-columns): the normalized
    cells are blank and the raw column is all-zero -- no derivable normalization (0/0), and no
    member is displaced (a shifted blank column is still blank).
  * cols 153..160 are the CU Tax Status block added 2026-07-17.  They use the standard value/MAX
    rule and therefore remain inside the guarded range.

This test asserts every value/MAX cell equals the member's OWN-row normalization within 1e-6.
It is RED before fec/legislation/repair_legislative_normalized.py runs and GREEN afterward.

Run:  python fec/tests/infra/test_legislative_alignment.py
"""
from __future__ import annotations

import os
import sys

import openpyxl

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)
from paths import WORKBOOK  # noqa: E402
import roster  # noqa: E402

FIRST_DATA_ROW = roster.first_row("Legislative Score - Raw")
LAST_DATA_ROW = roster.last_row("Legislative Score - Raw")           # the member block: 535 at 533 members
FIRST_VALUE_COL = 3           # col A=names, B=district, C.. = category value columns
LAST_VALUE_COL = 160           # CU Tax Status block extends the value region through col 160
TOL = 1e-6

# Columns whose normalization is a different rule or is underivable (all-zero/blank) -- not repaired.
EXCLUDED_COLS = frozenset({144, 146, 148, 150, 151, 152})


def num(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def value_max_cols():
    return [c for c in range(FIRST_VALUE_COL, LAST_VALUE_COL + 1) if c not in EXCLUDED_COLS]


def read_grids(workbook_path):
    """RAW (all literal numbers) and NRM (formula-view: formulas surface as strings)."""
    wb = openpyxl.load_workbook(workbook_path, data_only=False, read_only=True)
    raw = list(wb["Legislative Score - Raw"].iter_rows(values_only=True))
    nrm = list(wb["Legislative Score - Normalized"].iter_rows(values_only=True))
    wb.close()
    return raw, nrm


def cell(grid, row, col):  # 1-based
    r = grid[row - 1]
    return r[col - 1] if col - 1 < len(r) else None


def col_max(raw, col):
    return max([0.0] + [num(cell(raw, r, col)) for r in range(FIRST_DATA_ROW, LAST_DATA_ROW + 1)])


def expected_norm(value, colmax):
    return 0.0 if colmax == 0 else value / colmax


def evaluate(workbook_path):
    """Return (misaligned_nonnumeric, misaligned_numeric, shift_hits, own_hits, examples)."""
    raw, nrm = read_grids(workbook_path)
    cols = value_max_cols()
    colmax = {c: col_max(raw, c) for c in cols}
    misaligned_nonnumeric = 0   # a value/MAX cell that is not a live number (formula string / blank)
    misaligned_numeric = 0      # a numeric cell whose value != own-row normalization
    shift_hits = own_hits = numeric_cells = 0
    examples = []
    for c in cols:
        mx = colmax[c]
        for r in range(FIRST_DATA_ROW, LAST_DATA_ROW + 1):
            want = expected_norm(num(cell(raw, r, c)), mx)
            got = cell(nrm, r, c)
            if not is_number(got):
                misaligned_nonnumeric += 1
                if len(examples) < 8:
                    examples.append((r, c, "non-numeric", repr(got)[:48], want))
                continue
            numeric_cells += 1
            if abs(got - want) > TOL:
                misaligned_numeric += 1
                if len(examples) < 8:
                    examples.append((r, c, "wrong", got, want))
            if abs(got - want) <= TOL:
                own_hits += 1
            if r - 1 >= FIRST_DATA_ROW:
                shifted = expected_norm(num(cell(raw, r - 1, c)), mx)
                if abs(got - shifted) <= TOL:
                    shift_hits += 1
    return dict(nonnumeric=misaligned_nonnumeric, numeric_wrong=misaligned_numeric,
                numeric_cells=numeric_cells, shift_hits=shift_hits, own_hits=own_hits,
                examples=examples, ncols=len(cols))


def main():
    print("Legislative-alignment TDD guard")
    print("workbook:", WORKBOOK)
    m = evaluate(WORKBOOK)
    total = m["nonnumeric"] + m["numeric_wrong"]
    print("value/MAX columns checked : %d  (excluded: %s)" % (m["ncols"], sorted(EXCLUDED_COLS)))
    print("numeric cells present     : %d" % m["numeric_cells"])
    print("misaligned (numeric wrong): %d   <- the pasted-value shift the report measured (~7,000)" % m["numeric_wrong"])
    print("misaligned (non-numeric)  : %d   <- formula cells still referencing r-1 / not yet materialized" % m["nonnumeric"])
    print("TOTAL misaligned          : %d" % total)
    print("shift signature  NRM[r]==RAW[r-1]/max : %d numeric hits" % m["shift_hits"])
    print("own-row  match   NRM[r]==RAW[r]/max    : %d numeric hits" % m["own_hits"])
    if m["examples"]:
        print("examples:")
        for r, c, kind, got, want in m["examples"]:
            print("  r%d c%d [%s] got=%s want=%.6f" % (r, c, kind, got, want))
    if total == 0:
        print("\nRESULT: GREEN -- every value/MAX cell holds its OWN member's normalization.")
        print("legislative alignment: 1 passed, 0 failed")   # the kernel runner's native verdict shape
        return 0
    print("\nRESULT: RED -- %d value/MAX cells are not the member's own-row normalization." % total)
    print("legislative alignment: 0 passed, 1 failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
