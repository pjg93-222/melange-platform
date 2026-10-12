"""CPI multipliers of the forecast's 2025-dollar convention (read-only).

The models are in 2025 dollars: a cycle t (the calendar years t-1 and t) is deflated by cpi[2025] / mean(cpi[t-1], cpi[t]), CPI-U annual
averages (1982-84 = 100) from the Sector Growth Study's 00_controls.xlsx (the file the refit pipeline pins). A cycle whose CPI is not yet
published (2026: no 2026 CPI) has NO multiplier; the console then shows observed amounts at face value and says so.
"""
import openpyxl

BASE_YEAR = 2025
SERIES = 'CPI-U annual average (1982-84=100)'


def read_cpi(path, sheet='data'):
    """{year: cpi} from the workbook's `data` sheet (columns `year`, `cpi_annual_average`); rows with no CPI yet are skipped."""
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = book[sheet].iter_rows(values_only=True)
        header = list(next(rows))
        year_col, cpi_col = header.index('year'), header.index('cpi_annual_average')
        return {int(r[year_col]): float(r[cpi_col]) for r in rows if r[year_col] is not None and r[cpi_col] is not None}
    finally:
        book.close()


def multipliers(cpi, cycles):
    """{str(cycle end year): cpi[2025] / mean(cpi[t-1], cpi[t])} for the cycles whose CPI exists."""
    out = {}
    if BASE_YEAR not in cpi:
        return out
    for cycle in cycles:
        if cycle - 1 in cpi and cycle in cpi:
            out[str(cycle)] = cpi[BASE_YEAR] / ((cpi[cycle - 1] + cpi[cycle]) / 2)
    return out
