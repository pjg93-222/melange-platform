"""One figure to one PNG image, rendered on this computer by headless Edge.

figure-pdf.js posts the figure's markup, the page's stylesheet paths and the
page size it measured; this module builds one self-contained HTML document from
them and has the browser photograph it at twice the screen's resolution. Nothing
leaves the machine and nothing is installed: Edge ships with Windows, Chrome is
the fallback, and MENTAT_PDF_BROWSER names any other Chromium.

The document is built to be inert and faithful:

* Its first <head> element is a Content-Security-Policy that forbids script and
  every network or file load; only inline styles and data: images and fonts
  remain. The markup is also stripped of script-bearing elements and event
  attributes, so the policy is a second lock, not the only one.
* Only stylesheets that the console itself serves (a .css entry of
  STATIC_ASSETS) are inlined; any other requested path is ignored, so the
  request can never name a file to read.
* The stylesheets are rewritten once: @media print blocks go (the page being
  printed is not the page being looked at), @media screen becomes @media all,
  and width/height media queries are decided against the viewport the reader
  had on screen instead of the printed page's width.
* The window is sized to the figure and its header, so the image is exactly that.

Nothing here logs the markup it is given.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

MAX_REQUEST_BYTES = 40 * 1024 * 1024
RENDER_TIMEOUT_SECONDS = 60
MAX_CONCURRENT_RENDERS = 2
MIN_PAGE_PX, MAX_PAGE_PX = 100, 19000
# The image is drawn at SCALE x; a figure over MAX_SHARP_PIXELS (CSS px) is drawn at 1x to bound memory.
SCALE, MAX_SHARP_PIXELS = 2, 24_000_000
PNG_SIGNATURE, PNG_END = b'\x89PNG\r\n\x1a\n', b'\x00\x00\x00\x00IEND\xaeB`\x82'
MAX_STYLESHEETS = 64
MAX_ATTRIBUTES = 64

CONTENT_SECURITY_POLICY = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:"

EDGE_PATHS = (r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
              r'C:\Program Files\Microsoft\Edge\Application\msedge.exe')
CHROME_PATHS = (r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe')

_slots = threading.BoundedSemaphore(MAX_CONCURRENT_RENDERS)


class PdfError(Exception):
    """A refusal with the HTTP status the console answers it with."""
    status = 500


class BadRequest(PdfError):
    status = 400


class BrowserMissing(PdfError):
    status = 503


class RenderTimeout(PdfError):
    status = 504


class RenderFailed(PdfError):
    status = 500


# ----------------------------------------------------------------- markup

FORBIDDEN_TAGS = frozenset({'script', 'style', 'iframe', 'object', 'embed', 'link', 'meta', 'base'})
# Elements whose content goes with them. link, meta, base and embed have none.
# style: HTMLParser reads it as raw text, but inside <svg>/<math> the browser
# parses it as markup, so its content is never safe to pass on; the app's
# stylesheets are inlined server-side and its CSP forbids inline <style>.
_DROP_WITH_CONTENT = frozenset({'script', 'style', 'iframe', 'object'})
_VOID_FORBIDDEN = FORBIDDEN_TAGS - _DROP_WITH_CONTENT
_URL_ATTRIBUTES = frozenset({'src', 'href', 'xlink:href', 'srcset', 'poster', 'action', 'formaction', 'data', 'background'})
_ATTRIBUTE_NAME = re.compile(r'^[A-Za-z_:][-A-Za-z0-9_:.]*$')


def _keep_attribute(name, value):
    name = name.lower()
    if name.startswith('on'):
        return False
    if name in _URL_ATTRIBUTES:
        # Inline data and in-document references only: nothing the document
        # could be asked to fetch from the network or the disk.
        return (value or '').strip().lower().startswith(('data:', '#'))
    return True


class _Sanitizer(HTMLParser):
    """Re-emits the markup without forbidden elements, event handlers or external references.

    convert_charrefs is off so text and entities pass through exactly as the
    page serialised them; attribute values are decoded by the parser and
    escaped again here.
    """

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out = []
        self.skipping = None          # tag name of an element being dropped with its content
        self.depth = 0                # nesting of that same tag inside the dropped element

    def _tag(self, tag, attrs, closed):
        parts = ['<', tag]
        for name, value in attrs:
            if _keep_attribute(name, value):
                parts.append(' %s' % name if value is None else ' %s="%s"' % (name, html.escape(value, quote=True)))
        parts.append(' />' if closed else '>')
        return ''.join(parts)

    def handle_starttag(self, tag, attrs):
        if self.skipping:
            if tag == self.skipping:
                self.depth += 1
            return
        if tag in _DROP_WITH_CONTENT:
            self.skipping, self.depth = tag, 1
        elif tag not in _VOID_FORBIDDEN:
            self.out.append(self._tag(tag, attrs, False))

    def handle_startendtag(self, tag, attrs):
        if self.skipping or tag in FORBIDDEN_TAGS:
            return
        self.out.append(self._tag(tag, attrs, True))

    def handle_endtag(self, tag):
        if self.skipping:
            if tag == self.skipping:
                self.depth -= 1
                if self.depth <= 0:
                    self.skipping = None
            return
        if tag not in FORBIDDEN_TAGS:
            self.out.append('</%s>' % tag)

    def _text(self, text):
        if not self.skipping:
            self.out.append(text)

    def handle_data(self, data):
        self._text(data)

    def handle_entityref(self, name):
        self._text('&%s;' % name)

    def handle_charref(self, name):
        self._text('&#%s;' % name)

    def handle_comment(self, data):
        pass

    def handle_decl(self, decl):
        pass

    def unknown_decl(self, data):
        pass

    def handle_pi(self, data):
        pass


def sanitize(markup):
    parser = _Sanitizer()
    parser.feed(markup)
    parser.close()
    return ''.join(parser.out)


def _attributes(values, label):
    """An attribute dict for <html> or <body>: valid names, string values, no handlers."""
    if values is None:
        return ''
    if not isinstance(values, dict) or len(values) > MAX_ATTRIBUTES:
        raise BadRequest('%s must be an object of at most %d attributes.' % (label, MAX_ATTRIBUTES))
    parts = []
    for name, value in values.items():
        if not isinstance(name, str) or not isinstance(value, str) or not _ATTRIBUTE_NAME.match(name):
            raise BadRequest('%s holds an attribute this export cannot use.' % label)
        if _keep_attribute(name, value):
            parts.append(' %s="%s"' % (name, html.escape(value, quote=True)))
    return ''.join(parts)


# -------------------------------------------------------------------- CSS

_COMMENT = re.compile(r'/\*.*?\*/', re.S)
_AT_MEDIA = re.compile(r'@media\b', re.I)
_FEATURE = re.compile(r'\(\s*(min|max)-(width|height)\s*:\s*(-?[\d.]+)\s*(px|em|rem)\s*\)', re.I)
_ALWAYS, _NEVER = '(min-width:0px)', '(min-width:99999px)'


def _block_end(css, brace):
    """Index just past the } that closes the { at `brace`; strings are skipped."""
    depth, i, quote = 0, brace, None
    while i < len(css):
        char = css[i]
        if quote:
            if char == '\\':
                i += 1
            elif char == quote:
                quote = None
        elif char in '"\'':
            quote = char
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(css)


def _decide(match, viewport):
    kind, axis, number, unit = match.group(1).lower(), match.group(2).lower(), float(match.group(3)), match.group(4).lower()
    size = number * (16 if unit in ('em', 'rem') else 1)
    reading = viewport[0] if axis == 'width' else viewport[1]
    return _ALWAYS if (reading >= size if kind == 'min' else reading <= size) else _NEVER


def _screen_query(prelude, viewport):
    """The query as the reader's screen answered it, or None when it can only mean print."""
    kept = []
    for item in prelude.split(','):
        words = item.strip().lower().split()
        if words and words[0] == 'only':
            words = words[1:]
        negated = bool(words) and words[0] == 'not'
        kind = (words[1] if negated and len(words) > 1 else words[0]) if words else ''
        kind = re.match(r'[a-z]*', kind).group(0)
        if (kind == 'print' and not negated) or (kind == 'screen' and negated):
            continue
        item = _FEATURE.sub(lambda match: _decide(match, viewport), item.strip())
        if kind == 'screen' or (kind == 'print' and negated):
            item = re.sub(r'^(?:only\s+)?(?:not\s+)?(?:screen|print)\b', 'all', item, count=1, flags=re.I)
        kept.append(item)
    return ', '.join(kept) if kept else None


def screen_css(css, viewport=(1280, 800)):
    """The stylesheet as the reader saw it on screen, ready to print.

    A printed page is narrower than the window the reader was in, so every
    (min|max)-(width|height) feature is decided against `viewport` here and
    replaced by an always-true or always-false test; @media print blocks, which
    would restyle the figure for paper, are removed; @media screen is widened to
    all, because the print medium would otherwise skip it.
    """
    css = _COMMENT.sub('', css)
    out, i = [], 0
    while True:
        found = _AT_MEDIA.search(css, i)
        if not found:
            out.append(css[i:])
            break
        out.append(css[i:found.start()])
        brace = css.find('{', found.end())
        if brace < 0:
            break
        end = _block_end(css, brace)
        query = _screen_query(css[found.end():brace], viewport)
        if query is not None:
            out.append('@media %s{%s}' % (query, screen_css(css[brace + 1:end - 1], viewport)))
        i = end
    return ''.join(out)


def _stylesheets(paths, static_assets, directory, viewport):
    """The inlined CSS for the requested paths that the console really serves."""
    chunks, seen = [], set()
    for path in paths:
        key = urlparse(path).path
        entry = static_assets.get(key)
        if key in seen or not entry or not key.endswith('.css') or not entry[1].startswith('text/css'):
            continue
        seen.add(key)
        try:
            text = (Path(directory) / entry[0]).read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        chunks.append(screen_css(text, viewport).replace('</style', '<\\/style'))
    return '\n'.join(chunks)


def _print_css(width, height):
    return (
        '@page{size:%(w)dpx %(h)dpx;margin:0}'
        'html,body{margin:0!important;padding:0!important;background:#fff!important;width:%(w)dpx!important;'
        'height:%(h)dpx!important;min-height:0!important;max-height:none!important;overflow:hidden!important;'
        'display:block!important;position:static!important;transform:none!important;'
        '-webkit-print-color-adjust:exact;print-color-adjust:exact}'
        '[data-figure-pdf-skeleton]{display:block!important;position:static!important;float:none!important;'
        'margin:0!important;padding:0!important;border:0!important;background:none!important;box-shadow:none!important;'
        'width:auto!important;height:auto!important;min-width:0!important;min-height:0!important;'
        'max-width:none!important;max-height:none!important;overflow:visible!important;transform:none!important;'
        'inset:auto!important}'
        '*,*::before,*::after{animation:none!important;transition:none!important}'
        '.figure-pdf-btn{display:none!important}'
        % {'w': width, 'h': height})


def _number(data, key, low, high):
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise BadRequest('%s must be a number from %d to %d.' % (key, low, high))
    return int(round(value))


def build_document(data, static_assets, directory):
    """The self-contained HTML document for one request body (already parsed JSON)."""
    if not isinstance(data, dict):
        raise BadRequest('Expected a figure request.')
    markup = data.get('body')
    if not isinstance(markup, str) or not markup.strip():
        raise BadRequest('The figure markup is missing.')
    paths = data.get('stylesheets', [])
    if not isinstance(paths, list) or len(paths) > MAX_STYLESHEETS or not all(isinstance(path, str) for path in paths):
        raise BadRequest('stylesheets must be a list of paths.')
    width = _number(data, 'width', MIN_PAGE_PX, MAX_PAGE_PX)
    height = _number(data, 'height', MIN_PAGE_PX, MAX_PAGE_PX)
    viewport = data.get('viewport') or {}
    if not isinstance(viewport, dict):
        raise BadRequest('viewport must be an object.')
    seen = (_number(viewport, 'width', 200, 20000) if 'width' in viewport else 1280,
            _number(viewport, 'height', 200, 20000) if 'height' in viewport else 800)
    return (
        '<!doctype html>\n<html%s><head>'
        '<meta http-equiv="Content-Security-Policy" content="%s">'
        '<meta charset="utf-8"><title>Mentat figure</title>'
        '<style>%s</style><style>%s</style></head><body%s>%s</body></html>\n'
        % (_attributes(data.get('html_attrs'), 'html_attrs'), html.escape(CONTENT_SECURITY_POLICY, quote=True),
           _stylesheets(paths, static_assets, directory, seen), _print_css(width, height),
           _attributes(data.get('body_attrs'), 'body_attrs'), sanitize(markup)))


# ------------------------------------------------------------ the browser

def find_browser():
    """MENTAT_PDF_BROWSER, else Edge, else Chrome; None when none is installed."""
    configured = os.environ.get('MENTAT_PDF_BROWSER', '').strip()
    if configured:
        return configured if Path(configured).is_file() else None
    for path in EDGE_PATHS + CHROME_PATHS:
        if Path(path).is_file():
            return path
    for name in ('msedge', 'chrome', 'google-chrome', 'chromium'):
        found = shutil.which(name)
        if found:
            return found
    return None


def _finished(path):
    """The PNG's bytes once the browser has written all of it (its IEND chunk), else None."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data if data.startswith(PNG_SIGNATURE) and data.endswith(PNG_END) else None


def _kill_tree(process):
    """End the browser and everything it started; an image already on disk is safe."""
    # Unconditional: children can outlive the main process and keep the profile open.
    if os.name == 'nt':
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _reap(process, folder):
    """Off the request thread: ending a browser tree takes seconds, the image is already in hand."""
    try:
        if process is not None:
            _kill_tree(process)
    finally:
        folder.cleanup()


def _sweep_stale(max_age=600):
    """Best effort: delete mentat-pdf-* temp dirs older than `max_age` seconds."""
    # ponytail: stale dirs linger until the next render; a startup/timer sweep if that ever matters
    cutoff = time.time() - max_age
    for old in Path(tempfile.gettempdir()).glob('mentat-pdf-*'):
        try:
            if old.is_dir() and old.stat().st_mtime < cutoff:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass


def render(document, width, height):
    """PNG bytes (SCALE x width by SCALE x height pixels) for one HTML document. Raises PdfError (503, 504 or 500)."""
    browser = find_browser()
    if not browser:
        raise BrowserMissing('Image export needs Microsoft Edge or Chrome, and neither was found on this computer.')
    scale = SCALE if width * height <= MAX_SHARP_PIXELS else 1
    with _slots:
        _sweep_stale()
        folder = tempfile.TemporaryDirectory(prefix='mentat-pdf-', ignore_cleanup_errors=True)
        process, data = None, None
        try:
            root = Path(folder.name)
            page, out = root / 'figure.html', root / 'out.png'
            page.write_text(document, encoding='utf-8')
            command = [browser, '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                       '--disable-extensions', '--user-data-dir=%s' % (root / 'profile'), '--hide-scrollbars',
                       '--force-device-scale-factor=%d' % scale, '--window-size=%d,%d' % (width, height),
                       '--screenshot=%s' % out, page.as_uri()]
            try:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            except OSError:
                raise RenderFailed('The image browser could not be started.') from None
            deadline = time.monotonic() + RENDER_TIMEOUT_SECONDS
            # The browser writes the image in a second or two and then lingers
            # while it shuts down; the finished file is the finish line.
            while data is None:
                data = _finished(out)
                if data is not None:
                    break
                exit_code = process.poll()
                if exit_code is not None:
                    data = _finished(out)
                    # Edge's launcher can hand the job to a relaunched copy and exit 0 at once
                    # (here: after 0.07 s, the file follows about a second later), so only a
                    # failing exit, or a finished file, ends the wait before the deadline.
                    if data is not None or exit_code != 0:
                        break
                if time.monotonic() > deadline:
                    raise RenderTimeout('The image took longer than %d seconds to draw.' % RENDER_TIMEOUT_SECONDS)
                time.sleep(0.05)
        finally:
            threading.Thread(target=_reap, args=(process, folder), daemon=True).start()
    if not data:
        raise RenderFailed('The image could not be drawn.')
    return data


def file_name(requested):
    """A safe .png file name for Content-Disposition."""
    stem = re.sub(r'[^A-Za-z0-9._-]+', '-', requested if isinstance(requested, str) else '')
    stem = re.sub(r'\.{2,}', '.', stem).strip('.-_')
    stem = re.sub(r'(?i)\.(?:png|pdf)$', '', stem)[:120].strip('.-_')
    return (stem or 'mentat-figure') + '.png'


def export(raw, static_assets, directory):
    """(png bytes, file name) for a raw JSON request body."""
    if len(raw) > MAX_REQUEST_BYTES:
        raise BadRequest('That figure is too large to export.')
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise BadRequest('Expected a JSON figure request.') from None
    document = build_document(data, static_assets, directory)
    # build_document has already checked both numbers.
    size = (_number(data, 'width', MIN_PAGE_PX, MAX_PAGE_PX), _number(data, 'height', MIN_PAGE_PX, MAX_PAGE_PX))
    return render(document, *size), file_name(data.get('filename'))


def respond(handler, static_assets, directory, policy):
    """Answer POST /api/export/pdf on a console Handler (the path keeps its old name).

    Reads the body itself because it may be larger than the console's ordinary
    7 MB request cap, then writes the PNG as a download.
    """
    try:
        length = int(handler.headers.get('Content-Length', '0'))
    except (TypeError, ValueError):
        length = 0
    if length < 1 or length > MAX_REQUEST_BYTES:
        handler.close_connection = True
        return handler.send({'error': 'Request is empty or too large.'}, 400)
    try:
        png, name = export(handler.rfile.read(length), static_assets, directory)
    except PdfError as error:
        return handler.send({'error': str(error)}, error.status)
    handler._sent_bytes = len(png)
    handler.send_response(200)
    for header, value in (('Content-Type', 'image/png'), ('Content-Length', str(len(png))),
                          ('Content-Disposition', 'attachment; filename="%s"' % name),
                          ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'),
                          ('Content-Security-Policy', policy)):
        handler.send_header(header, value)
    handler.end_headers()
    handler.wfile.write(png)
