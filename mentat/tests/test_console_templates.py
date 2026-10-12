"""The console page is rendered from templates and is still the page it was.

`/` no longer serves mentat/app/index.html. It is rendered at request time from
mentat/app/templates/ by console_page.py. index.html stays in the repo as the
GOLDEN RENDER, because fourteen node suites read it off disk and assert the
markup they pin, and because the asset hash counts it.

So the binding claim of this lane is an identity, and it is the first test here:
the render equals the golden file byte for byte once CRLF is folded to LF. The
checkout stores index.html with CRLF and the render emits LF; that is the only
difference allowed, and the final newline is part of the comparison.

Nothing here starts a server, binds a port, spawns git, or writes into the app
folder.
"""
import contextlib
import importlib.util
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
import console_page                                                   # noqa: E402

spec = importlib.util.spec_from_file_location('console_templates_server', APP / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

GOLDEN = APP / 'index.html'
# Every Jinja delimiter. J1 is pure composition: the root composes, the partials
# carry markup and nothing else, and no delimiter survives into the page.
DELIMITERS = ('{{', '}}', '{%', '%}', '{#', '#}')


def canonical(raw):
    """The bytes with every CRLF folded to LF. The one difference this lane allows."""
    return raw.replace(b'\r\n', b'\n')


class ConsolePageIdentityTests(unittest.TestCase):
    """The render is the golden page, to the byte."""

    def test_the_render_is_the_golden_index_html_byte_for_byte(self):
        content, etag = console_page.render()
        golden = GOLDEN.read_bytes()
        self.assertEqual(canonical(content), canonical(golden),
                         'the render has drifted from index.html; '
                         'run: python mentat/tools/render_console.py --check')
        self.assertTrue(content.endswith(b'>\n'), 'the rendered page lost its final newline')
        self.assertNotIn(b'\r', content, 'the served page is LF; index.html on disk is CRLF')
        self.assertTrue(etag.startswith('W/"') and etag.endswith('"'), etag)

    def test_the_render_is_reproducible_and_carries_no_jinja_left_over(self):
        first, first_tag = console_page.render()
        second, second_tag = console_page.render()
        self.assertEqual((first, first_tag), (second, second_tag))
        for delimiter in DELIMITERS:
            self.assertNotIn(delimiter.encode(), first,
                             'the page still carries the Jinja delimiter %r' % delimiter)

    def test_the_page_falls_back_to_the_golden_file_when_it_cannot_render(self):
        """No jinja2, or a jinja2 that is not the library, must not lose the page."""
        golden_lf = canonical(GOLDEN.read_bytes())
        self.addCleanup(console_page._rendered.cache_clear)
        self.addCleanup(console_page._announced.clear)
        cases = {'jinja2 is not installed': {'jinja2': None},
                 'jinja2 is not the library': {'jinja2': object()}}
        for name, replacement in cases.items():
            with self.subTest(case=name):
                console_page._rendered.cache_clear()
                console_page._announced.clear()
                noise = io.StringIO()
                with patch.dict(sys.modules, replacement), contextlib.redirect_stderr(noise):
                    content, etag = console_page.page()
                    again, _ = console_page.page()
                self.assertEqual(content, golden_lf)
                self.assertEqual(again, golden_lf, 'the fallback is not stable across requests')
                self.assertTrue(etag.startswith('W/"'), etag)
                lines = [line for line in noise.getvalue().splitlines() if line.strip()]
                self.assertEqual(len(lines), 1, 'the fallback said %d lines, not one: %r'
                                 % (len(lines), lines))
                self.assertIn('index.html', lines[0])
        console_page._rendered.cache_clear()
        console_page._announced.clear()
        self.assertEqual(console_page.page()[0], console_page.render()[0],
                         'the page did not recover once jinja2 was back')


class TemplateSetTests(unittest.TestCase):
    """Nothing in templates/ is an orphan, and nothing in it does more than compose."""

    def test_every_template_file_is_reached_by_the_render(self):
        import jinja2

        class RecordingLoader(jinja2.FileSystemLoader):
            def __init__(self, path):
                super().__init__(path, encoding='utf-8')
                self.reached = set()

            def get_source(self, environment, template):
                self.reached.add(template)
                return super().get_source(environment, template)

        loader = RecordingLoader(str(console_page.TEMPLATE_DIRECTORY))
        environment = jinja2.Environment(loader=loader, cache_size=0,
                                         autoescape=jinja2.select_autoescape(['html', 'j2']),
                                         undefined=jinja2.StrictUndefined,
                                         keep_trailing_newline=True, newline_sequence='\n')
        rendered = environment.get_template(console_page.ROOT_TEMPLATE).render().encode('utf-8')
        self.assertEqual(canonical(rendered), canonical(GOLDEN.read_bytes()))
        on_disk = {path.relative_to(console_page.TEMPLATE_DIRECTORY).as_posix()
                   for path in console_page.template_files()}
        self.assertEqual(on_disk, loader.reached,
                         'templates on disk the render never reached: %s'
                         % sorted(on_disk - loader.reached))
        self.assertGreater(len(on_disk), 1)

    def test_the_root_composes_and_the_partials_only_carry_markup(self):
        root = console_page.TEMPLATE_DIRECTORY / console_page.ROOT_TEMPLATE
        text = root.read_text(encoding='utf-8')
        statements = re.findall(r'\{%(.*?)%\}', text, re.S)
        self.assertTrue(statements, 'the root template includes nothing')
        for statement in statements:
            self.assertRegex(statement.strip(), r'^include "[\w.]+" -$',
                             'J1 is composition only; the root did more than include: %r'
                             % statement)
        for path in console_page.template_files():
            if path == root:
                continue
            body = path.read_text(encoding='utf-8')
            for delimiter in DELIMITERS:
                self.assertNotIn(delimiter, body,
                                 '%s carries the Jinja delimiter %r; a partial is verbatim '
                                 'markup, so it would need {%% raw %%}' % (path.name, delimiter))


class ContentSecurityPolicyTests(unittest.TestCase):
    """The CSP is `script-src 'self'; style-src 'self'` with no nonce anywhere."""

    SCRIPT = re.compile(rb'<script\b[^>]*>(.*?)</script>', re.I | re.S)
    HANDLER = re.compile(rb'<[a-zA-Z][^<>]*\son[a-zA-Z]+\s*=', re.I)
    STYLE = re.compile(rb'<style\b', re.I)

    def test_the_rendered_page_carries_nothing_the_policy_would_block(self):
        content, _etag = console_page.render()
        inline = [body for body in self.SCRIPT.findall(content) if body.strip()]
        self.assertEqual(inline, [], 'an inline <script> body would be blocked by script-src')
        self.assertEqual(self.HANDLER.findall(content), [],
                         'an on* attribute would be blocked by script-src')
        self.assertEqual(self.STYLE.findall(content), [],
                         'an inline <style> would be blocked by style-src')


class AssetHashTests(unittest.TestCase):
    """A template edit has to move the build hash the page reads."""

    def test_asset_files_covers_the_templates_and_still_covers_index_html(self):
        assets = server.asset_files()
        self.assertIn(APP / 'index.html', assets,
                      'index.html left the asset hash; test_contribution_http.py pins it')
        templates = console_page.template_files()
        self.assertGreater(len(templates), 1)
        for path in templates:
            self.assertIn(path, assets, '%s is not in the asset hash' % path.name)
        self.assertEqual(len(assets), len(set(assets)))

    def test_every_asset_relative_path_is_unique(self):
        """Separate geography layers may legitimately reuse state filenames."""
        names = [path.relative_to(APP).as_posix() for path in server.asset_files()]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        self.assertEqual(duplicates, [],
                         'tree_sha256 hashes by relative path; these would collide: %s' % duplicates)

    def test_same_named_layers_have_stable_distinct_identities(self):
        with tempfile.TemporaryDirectory(prefix='mentat-layer-hash-') as temporary:
            paths = [Path(temporary) / layer / 'TX.json' for layer in ('districts', 'sld')]
            for path, content in zip(paths, (b'congressional', b'state legislative')):
                path.parent.mkdir()
                path.write_bytes(content)
            before = server.tree_sha256(paths)
            self.assertEqual(before, server.tree_sha256(list(reversed(paths))))
            first, second = [path.read_bytes() for path in paths]
            paths[0].write_bytes(second)
            paths[1].write_bytes(first)
            server.file_digest.cache_clear()
            self.addCleanup(server.file_digest.cache_clear)
            self.assertNotEqual(before, server.tree_sha256(paths))

    def test_a_changed_template_moves_the_tree_hash(self):
        """Proved on a copy: nothing here writes into the app folder."""
        with tempfile.TemporaryDirectory(prefix='mentat-template-hash-') as temporary:
            partial = Path(temporary) / 'view_evidence.html'
            partial.write_bytes(b'<section id="view-evidence"></section>\n')
            before = server.tree_sha256([partial])
            partial.write_bytes(b'<section id="view-evidence"><p>edited</p></section>\n')
            server.file_digest.cache_clear()
            self.addCleanup(server.file_digest.cache_clear)
            self.assertNotEqual(server.tree_sha256([partial]), before,
                                'a changed template did not move the assets hash, so the page '
                                'would never offer Restart after a template edit')


if __name__ == '__main__':
    unittest.main()
