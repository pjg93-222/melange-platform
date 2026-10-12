"""Freshness receipts read without openpyxl's whole-workbook load (perf H2).

report_insights.embedded_receipts streams only the 'Source Freshness' and 'Source
Receipts' sheets plus the shared strings they use. Every case here compares it with
report_insights._workbook_receipts, the whole read-only, data-only openpyxl load it
replaced, value for value and exception for exception.
"""
from datetime import datetime, time, timedelta
import gc
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import report_insights as insights

MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PACKAGE = 'http://schemas.openxmlformats.org/package/2006/relationships'
TYPES = 'application/vnd.openxmlformats-officedocument.spreadsheetml.'
# Style ids used by the cases: 1 builtin date (14), 2 custom date, 3 [h]:mm:ss
# duration, 4 builtin time (21), 5 percent (10).
STYLES = ('<styleSheet xmlns="' + MAIN + '"><numFmts count="2">'
          '<numFmt numFmtId="164" formatCode="yyyy-mm-dd"/><numFmt numFmtId="165" formatCode="[h]:mm:ss"/></numFmts>'
          '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
          '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
          '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
          '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="6">'
          + ''.join('<xf numFmtId="%d" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>' % n
                    for n in (0, 14, 164, 165, 21, 10)) +
          '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


def xlsx(path, sheets, strings=None, date1904=False, workbook_part='xl/workbook.xml', chartsheets=(), styles=True):
    """A minimal package written by hand, so every cell type and layout is exact.

    sheets: [(name, sheetData inner XML)] in workbook order; strings: shared-string
    <si> bodies; chartsheets: sheet names written as chart sheets instead.
    """
    folder = workbook_part.rsplit('/', 1)[0]
    parts, overrides, relations, entries = {}, [], [], []
    for number, (name, rows) in enumerate(sheets, 1):
        kind = 'chartsheet' if name in chartsheets else 'worksheet'
        target = kind + 's/sheet%d.xml' % number
        body = ('<chartsheet xmlns="%s"><sheetViews><sheetView workbookViewId="0"/></sheetViews></chartsheet>' % MAIN
                if kind == 'chartsheet' else '<worksheet xmlns="%s"><sheetData>%s</sheetData></worksheet>' % (MAIN, rows))
        parts[folder + '/' + target] = body
        overrides.append(('/' + folder + '/' + target, TYPES + kind + '+xml'))
        relations.append(('rId%d' % number, REL + '/' + kind, target))
        entries.append('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (name, number, number))
    if strings is not None:
        parts[folder + '/sharedStrings.xml'] = ('<sst xmlns="%s" count="%d" uniqueCount="%d">%s</sst>'
                                                % (MAIN, len(strings), len(strings), ''.join('<si>%s</si>' % s for s in strings)))
        overrides.append(('/' + folder + '/sharedStrings.xml', TYPES + 'sharedStrings+xml'))
        relations.append(('rIdS', REL + '/sharedStrings', 'sharedStrings.xml'))
    if styles:
        parts[folder + '/styles.xml'] = STYLES
        overrides.append(('/' + folder + '/styles.xml', TYPES + 'styles+xml'))
        relations.append(('rIdT', REL + '/styles', 'styles.xml'))
    overrides.append(('/' + workbook_part, TYPES + 'sheet.main+xml'))
    parts[workbook_part] = ('<workbook xmlns="%s" xmlns:r="%s">%s<sheets>%s</sheets></workbook>'
                            % (MAIN, REL, '<workbookPr date1904="1"/>' if date1904 else '', ''.join(entries)))
    parts[folder + '/_rels/' + workbook_part.rsplit('/', 1)[1] + '.rels'] = (
        '<Relationships xmlns="%s">%s</Relationships>'
        % (PACKAGE, ''.join('<Relationship Id="%s" Type="%s" Target="%s"/>' % r for r in relations)))
    parts['_rels/.rels'] = ('<Relationships xmlns="%s"><Relationship Id="rId1" Type="%s/officeDocument" Target="%s"/>'
                            '</Relationships>' % (PACKAGE, REL, workbook_part))
    parts['[Content_Types].xml'] = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + ''.join('<Override PartName="%s" ContentType="%s"/>' % o for o in overrides) + '</Types>')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, text in parts.items():
            archive.writestr(name, '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + text)
    return path


def row(number, *cells):
    return '<row r="%d">%s</row>' % (number, ''.join(cells)) if number else '<row>%s</row>' % ''.join(cells)


def c(ref, value=None, kind=None, style=None, formula=None, inline=None):
    attributes = (' r="%s"' % ref if ref else '') + (' t="%s"' % kind if kind else '') + (' s="%d"' % style if style else '')
    inner = ('<f>%s</f>' % formula if formula is not None else '') + ('<v>%s</v>' % value if value is not None else '')
    if inline is not None:
        attributes, inner = attributes + ' t="inlineStr"', '<is>%s</is>' % inline
    return '<c%s>%s</c>' % (attributes, inner)


def outcome(function, *args):
    try:
        return 'value', function(*args)
    except Exception as error:  # noqa: BLE001 - both sides must fail the same way
        return 'error', type(error), str(error)


class StreamedReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # openpyxl leaves its archive open when load_workbook raises (as the reference
        # does for the damaged cases) until the reader is collected; Windows then
        # refuses to delete the file.
        self.addCleanup(gc.collect)
        self.root = Path(self.tmp.name)
        insights.embedded_receipts.cache_clear()
        self.addCleanup(insights.embedded_receipts.cache_clear)

    def assertSameAsWholeLoad(self, path, streamed=True):
        reference = outcome(insights._workbook_receipts, str(path))
        self.assertEqual(outcome(insights.embedded_receipts, insights.stamp(path)), reference)
        if streamed:
            self.assertIsNotNone(insights._streamed_receipts(str(path)), 'the streamed read must answer this shape itself')
        return reference

    def typed_rows(self):
        strings = ['<t>Source</t>', '<t>Verified Data Through</t>', '<t>Checked At</t>', '<t>Note</t>', '<t>Count</t>',
                   '<t>Flag</t>', '<t>Ratio</t>', '<t>Kind</t>',
                   '<r><t xml:space="preserve">rich </t></r><r><rPr><b/></rPr><t>text</t></r>',
                   '<t>漢字</t><rPh sb="0" eb="2"><t>カンジ</t></rPh>', '<t>a_x005F_b</t>', '<t xml:space="preserve">  padded  </t>']
        rows = [row(1, *(c(ref, i, 's') for i, ref in enumerate(['A1', 'B1', 'C1', 'D1', 'E1', 'F1', 'G1', 'H1']))),
                row(2, c('A2', 8, 's'), c('B2', 45869, style=1), c('C2', '2026-09-08T10:00:00', 'd'), c('D2', 9, 's'),
                    c('E2', 42), c('F2', 1, 'b'), c('G2', '0.25', style=5), c('H2', 10, 's')),
                row(3, c('A3', inline='<t>inline</t>'), c('B3', 45870.5, style=2), c('C3', '1.2534722', style=3),
                    c('D3', '#N/A', 'e'), c('E3', '1E3'), c('F3', 0, 'b'), c('G3', '-3.5'), c('H3', 11, 's')),
                row(4, c('A4', 'ab', 'str', formula='"a"&amp;"b"'), c('B4', '0.5730324', style=4), c('C4', formula='NOW()'),
                    c('D4', 2, formula='1+1'), c('E4', '9007199254740993'), c('F4', ''), c('H4', 0, 's')),
                row(6, c(None, 0, 's'), c(None, 45000, style=1), c(None, inline='<r><t>in</t></r><r><t>line</t></r>')),
                row(0, c('B7', 44000.25, style=1), c('E7', '12.0'))]
        return strings, rows

    def test_typed_cells_match_the_whole_load(self):
        strings, rows = self.typed_rows()
        path = xlsx(self.root / 'typed.xlsx', [('Other', row(1, c('A1', 0, 's'))), ('Source Freshness', ''.join(rows))], strings)
        kind, value = self.assertSameAsWholeLoad(path)
        self.assertEqual(kind, 'value')
        fresh = value['Source Freshness']
        # The fixture really exercises typed conversion (so equality is not vacuous):
        # rich, phonetic and escaped shared strings, an inline string, a styled date,
        # an ISO date, a duration, a boolean, an error, a cached formula, big integers.
        self.assertEqual(fresh[0]['source'], 'rich text')
        self.assertEqual(fresh[0]['verified_data_through'], datetime(2025, 7, 31))
        self.assertEqual(fresh[0]['checked_at'], datetime(2026, 9, 8, 10, 0))
        self.assertEqual(fresh[0]['note'], '漢字')
        self.assertIs(fresh[0]['flag'], True)
        self.assertEqual(fresh[0]['kind'], 'a_b')
        self.assertEqual(fresh[1]['source'], 'inline')
        self.assertIsInstance(fresh[1]['checked_at'], timedelta)
        self.assertEqual(fresh[1]['note'], '#N/A')
        self.assertEqual(fresh[1]['count'], 1000.0)
        self.assertEqual(fresh[1]['kind'], '  padded  ')
        self.assertEqual(fresh[2]['source'], 'ab')
        self.assertIsInstance(fresh[2]['verified_data_through'], time)
        self.assertEqual(fresh[2]['note'], 2)
        self.assertEqual(fresh[2]['count'], 9007199254740993)
        self.assertEqual(fresh[3]['checked_at'], 'inline')

    def test_1904_epoch_dates_match_the_whole_load(self):
        strings, rows = self.typed_rows()
        path = xlsx(self.root / 'epoch.xlsx', [('Source Freshness', ''.join(rows))], strings, date1904=True)
        _, value = self.assertSameAsWholeLoad(path)
        self.assertEqual(value['Source Freshness'][0]['verified_data_through'], datetime(2029, 8, 1))

    def test_receipts_sheet_header_below_titles_and_escaped_strings(self):
        strings = ['<t>Report title</t>', '<t>Industry</t>', '<t>Audit File</t>', '<t>SHA-256</t>', '<t>Test</t>',
                   '<t>source_freshness.csv</t>', '<t>a_x005F_b</t>']
        receipts = ''.join([row(1, c('A1', 0, 's')), row(5, c('A5', 1, 's'), c('B5', 2, 's'), c('C5', 3, 's')),
                            row(6, c('A6', 4, 's'), c('B6', 5, 's'), c('C6', 6, 's'))])
        path = xlsx(self.root / 'receipts.xlsx', [('Source Receipts', receipts), ('Source Freshness', row(1, c('A1', 1, 's')))], strings)
        _, value = self.assertSameAsWholeLoad(path)
        self.assertEqual(value['Source Receipts'], [{'industry': 'Test', 'audit_file': 'source_freshness.csv', 'sha256': 'a_b'}])

    def test_workbook_without_receipt_sheets_reads_no_strings(self):
        strings = ['<t>x</t>'] * 50
        path = xlsx(self.root / 'none.xlsx', [('Data', row(1, c('A1', 49, 's')))], strings)
        with patch.object(insights, 'Text', side_effect=AssertionError('no shared string needed')):
            self.assertEqual(insights._streamed_receipts(str(path)), {})
        self.assertSameAsWholeLoad(path)

    def test_nonstandard_workbook_part_and_no_styles(self):
        path = xlsx(self.root / 'part.xlsx', [('Source Freshness', row(1, c('A1', 0, 's')) + row(2, c('A2', 1, 's')))],
                    ['<t>Source</t>', '<t>cm</t>'], workbook_part='xl/book.xml', styles=False)
        _, value = self.assertSameAsWholeLoad(path)
        self.assertEqual(value, {'Source Freshness': [{'source': 'cm'}]})

    def test_limits_and_damaged_workbooks_fail_exactly_as_before(self):
        header = row(1, c('A1', 0, 's'))
        over = xlsx(self.root / 'over.xlsx', [('Source Freshness', header + ''.join(row(n, c('A%d' % n, 1, 's')) for n in range(2, 2004)))],
                    ['<t>Source</t>', '<t>cm</t>'])
        self.assertEqual(self.assertSameAsWholeLoad(over, streamed=False)[1], ValueError)
        missing = xlsx(self.root / 'index.xlsx', [('Source Freshness', header + row(2, c('A2', 7, 's')))], ['<t>Source</t>'])
        self.assertEqual(self.assertSameAsWholeLoad(missing, streamed=False)[1], IndexError)
        negative = xlsx(self.root / 'negative.xlsx', [('Source Freshness', header + row(2, c('A2', -1, 's')))], ['<t>Source</t>', '<t>last</t>'])
        self.assertEqual(self.assertSameAsWholeLoad(negative, streamed=False)[1]['Source Freshness'], [{'source': 'last'}])
        chart = xlsx(self.root / 'chart.xlsx', [('Source Freshness', '')], [], chartsheets=('Source Freshness',))
        self.assertEqual(self.assertSameAsWholeLoad(chart, streamed=False)[0], 'error')
        broken = self.root / 'broken.xlsx'
        broken.write_bytes(b'not a workbook')
        self.assertEqual(self.assertSameAsWholeLoad(broken, streamed=False)[1], zipfile.BadZipFile)

    def test_rows_past_the_last_readable_row_are_never_resolved(self):
        header = row(1, c('A1', 0, 's'))
        # openpyxl stops after the first row beyond max_row, so a bad index there
        # raises but one a row later is never read. Both reads agree.
        first = xlsx(self.root / 'first.xlsx', [('Source Freshness', header + row(2, c('A2', 1, 's')) + row(3000, c('A3000', 99, 's')))],
                     ['<t>Source</t>', '<t>cm</t>'])
        self.assertEqual(self.assertSameAsWholeLoad(first, streamed=False)[1], IndexError)
        later = xlsx(self.root / 'later.xlsx', [('Source Freshness', header + row(2, c('A2', 1, 's')) + row(3000, c('A3000', 1, 's'))
                                                 + row(3001, c('A3001', 99, 's')))], ['<t>Source</t>', '<t>cm</t>'])
        self.assertEqual(self.assertSameAsWholeLoad(later)[1], {'Source Freshness': [{'source': 'cm'}]})

    def test_receipts_do_not_parse_the_whole_shared_string_table(self):
        # Negative control for the pre-change code: openpyxl.load_workbook reads the
        # whole table through read_string_table, which this refuses.
        strings = ['<t>Source</t>', '<t>cm</t>'] + ['<t>filler %d</t>' % n for n in range(5000)]
        path = xlsx(self.root / 'big.xlsx', [('Source Freshness', row(1, c('A1', 0, 's')) + row(2, c('A2', 1, 's')))], strings)
        with patch('openpyxl.reader.excel.read_string_table', side_effect=AssertionError('whole table parsed')), \
             patch('openpyxl.reader.strings.read_string_table', side_effect=AssertionError('whole table parsed')):
            self.assertEqual(insights.embedded_receipts(insights.stamp(path)), {'Source Freshness': [{'source': 'cm'}]})

    def test_published_workbooks_match_the_whole_load(self):
        try:
            from report_directory import report_directory
            directory = Path(report_directory(Path(insights.__file__).resolve().parents[1]))
        except Exception as error:  # noqa: BLE001
            self.skipTest('no published report directory: ' + str(error))
        books = sorted(directory.glob('*.xlsx'))
        if not books:
            self.skipTest('no published workbooks')
        for book in books:
            with self.subTest(workbook=book.name):
                insights.embedded_receipts.cache_clear()
                self.assertSameAsWholeLoad(book, streamed=not book.name.startswith('~$'))


if __name__ == '__main__':
    unittest.main()
