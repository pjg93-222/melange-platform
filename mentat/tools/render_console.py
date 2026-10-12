"""Carry an edit to mentat/app/templates/ into the golden mentat/app/index.html.

    python mentat/tools/render_console.py --check    # exit 1 and print a diff when they have drifted
    python mentat/tools/render_console.py --write    # regenerate index.html from the templates

The console itself no longer reads index.html: `/` is rendered from the
templates on every request. The file stays because fourteen node suites read it
off disk and assert the markup they pin, and because test_contribution_http.py
wants it in the asset hash. So it is a GENERATED artifact now -- never
hand-edited, regenerated here after a template changes.

`--check` compares canonically, folding CRLF to LF on both sides: this checkout
stores index.html with CRLF and the render emits LF, and a line-ending
difference is not drift. `--write` writes LF, and writes nothing at all when the
content already agrees, so an up-to-date tree is left exactly as it was.
"""
import argparse
import difflib
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
import console_page                                               # noqa: E402


def canonical(raw):
    return raw.replace(b'\r\n', b'\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true',
                        help='exit 1 with a unified diff when index.html is out of date')
    action.add_argument('--write', action='store_true',
                        help='regenerate index.html from the templates, with LF endings')
    arguments = parser.parse_args(argv)

    golden = console_page.GOLDEN_PAGE
    # The strict render on purpose: console_page.page() would fall back to the
    # very file being checked, and certify a broken template set as correct.
    rendered, _etag = console_page.render()
    on_disk = golden.read_bytes() if golden.is_file() else b''

    if canonical(rendered) == canonical(on_disk):
        print('%s is up to date with %s (%d bytes rendered)'
              % (golden.name, console_page.TEMPLATE_DIRECTORY.name, len(rendered)))
        return 0

    if arguments.write:
        golden.write_bytes(rendered)
        print('wrote %s, %d bytes, LF' % (golden, len(rendered)))
        return 0

    diff = difflib.unified_diff(
        canonical(on_disk).decode('utf-8').splitlines(keepends=True),
        canonical(rendered).decode('utf-8').splitlines(keepends=True),
        fromfile='%s (on disk)' % golden.name, tofile='%s (rendered)' % golden.name)
    sys.stdout.writelines(diff)
    print('\n%s is out of date. Run: python %s --write'
          % (golden.name, Path(__file__).name), file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
