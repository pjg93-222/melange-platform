"""One figure to one PNG image: the document builder, and one real render.

The builder test needs no browser and no console. The render test draws a small
SVG figure through headless Edge or Chrome and is skipped on a computer that has
neither.
"""
import importlib.util
import re
import struct
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / 'app'
spec = importlib.util.spec_from_file_location('figure_pdf_server', APP / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
import figure_pdf  # noqa: E402  (server.py put app/ on sys.path)


def request_body(**changes):
    body = {'body': '<div id="fig">hello</div>', 'stylesheets': [], 'width': 300, 'height': 200,
            'viewport': {'width': 1400, 'height': 900}}
    body.update(changes)
    return body


class DocumentTests(unittest.TestCase):
    def test_the_document_is_inert_one_sheet_and_carries_only_served_css(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / 'keep.css').write_text('.keep{color:red}@media print{.gone{x:1}}', encoding='utf-8')
            (directory / 'secret.css').write_text('.secret{color:green}', encoding='utf-8')
            (directory / 'code.js').write_text('var SECRETJS=1', encoding='utf-8')
            assets = {'/keep.css': ('keep.css', 'text/css'), '/code.js': ('code.js', 'text/javascript')}
            markup = ('<div id="a" onclick="boom()" class="ok"><script>alert(1)</script><iframe src="http://x"><b>inner</b></iframe>'
                      '<object data="x"></object><embed src="x"><link rel="stylesheet" href="http://x/a.css">'
                      '<meta http-equiv="refresh" content="0"><base href="http://x/"><img src="http://evil/p.png" onerror="boom()">'
                      '<img src="data:image/png;base64,AAAA" alt=""><svg viewBox="0 0 10 10"><path d="M0 0L5 5"/></svg></div>')
            document = figure_pdf.build_document(
                request_body(body=markup, width=640, height=480, html_attrs={'lang': 'en'}, body_attrs={'data-view': 'graph'},
                             stylesheets=['/keep.css', '/code.js', '/missing.css', '../secret.css', 'secret.css', '/keep.css']),
                assets, directory)
        # the policy is the first element in <head>
        head = re.search(r'<head>(<meta[^>]*>)', document).group(1)
        self.assertIn('http-equiv="Content-Security-Policy"', head)
        self.assertLess(document.index('Content-Security-Policy'), document.index('<style>'))
        self.assertIn('<html lang="en">', document)
        self.assertIn('<body data-view="graph">', document)
        # only a stylesheet the console serves is inlined, once, and its print block is gone
        self.assertEqual(document.count('.keep{color:red}'), 1)
        for absent in ('SECRETJS', '.secret', '.gone'):
            self.assertNotIn(absent, document)
        # one page, sized to the figure
        self.assertIn('@page{size:640px 480px;margin:0}', document)
        # nothing that runs or fetches survives; the figure's own markup does
        body = document.split('<body', 1)[1]
        for gone in ('<script', 'alert(1)', '<iframe', 'inner', '<object', '<embed', '<link', '<meta', '<base',
                     'onclick', 'onerror', 'http://evil', 'http://x'):
            self.assertNotIn(gone, body)
        for kept in ('id="a"', 'class="ok"', 'src="data:image/png;base64,AAAA"', '<path d="M0 0L5 5" />'):
            self.assertIn(kept, body)
        with self.assertRaises(figure_pdf.BadRequest):
            figure_pdf.build_document(request_body(body=''), assets, Path(folder))

    def test_style_elements_go_with_their_content_even_where_the_browser_reads_them_as_markup(self):
        # HTMLParser reads <style> as raw text; inside <svg>/<math> the browser parses it as markup.
        for payload, survivors in (
                ('<svg><style><meta http-equiv=refresh content="0;url=file:///C:/x"></style></svg>', ('meta', 'refresh')),
                ('<math><style><img src=x onerror=boom()></style></math>', ('<img', 'onerror')),
                ('<svg><style><base href="file:///C:/"></style></svg>', ('base', 'file:'))):
            with self.subTest(payload=payload):
                cleaned = figure_pdf.sanitize('<div>a%sb</div>' % payload)
                self.assertNotIn('style', cleaned)
                for survivor in survivors:
                    self.assertNotIn(survivor, cleaned)
                self.assertTrue(cleaned.startswith('<div>a') and cleaned.endswith('b</div>'))


@unittest.skipUnless(figure_pdf.find_browser(), 'no Edge or Chrome on this computer')
class RenderTests(unittest.TestCase):
    def test_a_small_svg_figure_is_one_png_at_twice_the_requested_size(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'a.css').write_text('.panel{border:1px solid #333;font:14px Arial}', encoding='utf-8')
            markup = ('<div class="figure-pdf-sheet"><div class="figure-pdf-head"><div class="figure-pdf-title">Test</div></div>'
                      '<section data-figure-pdf-skeleton id="view-graph"><div class="panel" style="width:360px;height:180px">'
                      '<svg viewBox="0 0 100 40" width="320" height="120"><rect x="4" y="4" width="60" height="12" fill="#57447b"/>'
                      '<text x="70" y="14" font-size="8">$1.2M</text></svg></div></section></div>')
            document = figure_pdf.build_document(
                request_body(body=markup, stylesheets=['/a.css'], width=400, height=300),
                {'/a.css': ('a.css', 'text/css')}, Path(folder))
            png = figure_pdf.render(document, 400, 300)
        self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertTrue(png.endswith(b'\x00\x00\x00\x00IEND\xaeB`\x82'))   # the whole file, not a half-written one
        width, height = struct.unpack('>II', png[16:24])                   # IHDR
        self.assertEqual((width, height), (800, 600))                      # 400 x 300 CSS px at 2x: no padding, no clipping


if __name__ == '__main__':
    unittest.main()
