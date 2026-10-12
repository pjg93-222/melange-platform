"""The console's main page, rendered from the Jinja2 template set beside it.

The page used to be one hand-written file, mentat/app/index.html, served
straight off disk. It is now composed at request time from
mentat/app/templates/: index.html.j2 includes one partial per coherent block,
and every partial is a verbatim slice of the page as it has always been.

index.html stays in the repo as the GOLDEN RENDER. Nothing serves it any more
and nothing hand-edits it. It is what `mentat/tools/render_console.py --write`
regenerates, what `--check` compares against, and what fourteen node suites
still read off disk to assert the markup they pin. The render and the golden
file are identical once CRLF is folded to LF; that identity is the whole point
of test_console_templates.py.

Three rules this module keeps, each for a reason this console has already paid
for once:

  * jinja2 is imported inside the first render, never at module import.
    test_startup_performance.py times a cold launch, and server.py's own
    comment beside its lazy wrappers says why an unused import is not free.
  * A failed render answers the golden bytes instead of raising. The page is
    how the operator reaches every other route, so a templating mistake must
    not be able to turn `/` into a 500.
  * Rendering is cached on the same file identity static_asset() uses --
    (path, size, mtime_ns, ctime_ns) for every template file -- so an edited
    template is picked up on the next request and an unchanged set costs one
    stat per file and nothing else.

The served page carries LF line endings. index.html in this Windows checkout is
CRLF, so the two differ byte for byte on disk and are equal canonically; the
browser, the ETag and every check here read the LF form.
"""
import hashlib
import sys
from functools import lru_cache
from pathlib import Path

APP = Path(__file__).resolve().parent
TEMPLATE_DIRECTORY = APP / 'templates'
ROOT_TEMPLATE = 'index.html.j2'
GOLDEN_PAGE = APP / 'index.html'

# A reason is announced once, not per request: a broken template would otherwise
# write a line into the console log on every page load for as long as the window
# stays open, and bury the first one that said what went wrong.
_announced = set()


def template_files():
    """Every file under templates/, sorted, as absolute paths.

    server.py folds these into asset_files(), so editing a partial moves
    on_disk_assets_sha256_now and the page offers Restart -- exactly what
    editing index.html used to do.
    """
    try:
        found = [path for path in TEMPLATE_DIRECTORY.rglob('*') if path.is_file()]
    except OSError:
        return []
    return sorted(found, key=lambda path: path.relative_to(TEMPLATE_DIRECTORY).as_posix())


def stamp():
    """The identity of the whole template set, cheap enough to take per request.

    The four fields static_asset() keys on. server.py takes this before and
    after a render for the double-stat guard the static route already has: a
    page half from one edit and half from the next is refused, not served.
    """
    identity = []
    for path in template_files():
        try:
            info = path.stat()
            identity.append((str(path), info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        except OSError:
            identity.append((str(path), -1, -1, -1))
    return tuple(identity)


def _tagged(content):
    """(bytes, weak ETag) in the shape static_asset() answers, so `/` revalidates alike."""
    return content, 'W/"' + hashlib.sha256(content).hexdigest() + '"'


@lru_cache(maxsize=4)
def _rendered(template_stamp):
    import jinja2  # first render only, so a console that never serves / pays nothing
    if not template_stamp:
        raise FileNotFoundError('no console templates under %s' % TEMPLATE_DIRECTORY)
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIRECTORY), encoding='utf-8'),
        autoescape=jinja2.select_autoescape(['html', 'j2']),
        undefined=jinja2.StrictUndefined,
        # The three settings that make the render byte-exact: the trailing
        # newline is kept, newlines come out as LF whatever the checkout stored
        # them as, and neither trim_blocks nor lstrip_blocks is on to eat a line
        # of real markup.
        keep_trailing_newline=True,
        newline_sequence='\n')
    return _tagged(environment.get_template(ROOT_TEMPLATE).render().encode('utf-8'))


def render():
    """(bytes, etag) from the templates, raising on any failure.

    The strict door. Tools and tests use it, because a check that quietly fell
    back to the golden file would certify a broken template set as correct.
    """
    return _rendered(stamp())


@lru_cache(maxsize=2)
def _golden(golden_stamp):
    return _tagged(GOLDEN_PAGE.read_bytes().replace(b'\r\n', b'\n'))


def _golden_stamp():
    info = GOLDEN_PAGE.stat()
    return (str(GOLDEN_PAGE), info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def page():
    """(bytes, etag) for `/`. The forgiving door: templating never breaks the page.

    Falls back to the golden index.html -- canonical LF, same ETag scheme -- when
    jinja2 is absent or a template fails, and says once on stderr why. It raises
    only if index.html is unreadable too, which is the app folder being gone
    rather than anything to do with templating; do_GET answers that the way it
    always answered a missing asset.
    """
    try:
        return _rendered(stamp())
    except Exception as error:               # no jinja2, a bad template, an unreadable file
        reason = '%s: %s' % (type(error).__name__, error)
        if reason not in _announced:
            _announced.add(reason)
            print('MENTAT: serving the golden index.html; the console page did not render (%s)'
                  % reason, file=sys.stderr)
        return _golden(_golden_stamp())
