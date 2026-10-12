"""Static self-check for the Mentat Pocket deliverables. No browser.

Every data assertion is recomputed from the RAW per-scope result sets carried in
pocket-data.json, never from the extractor's own integrity block. The two sides
of the reconciliation came from two different server queries (dimensions [cycle]
for the industry, dimensions [company, cycle] for the companies), so the check
has an independent source of truth and can actually fail.
"""
import argparse
from decimal import Decimal, ROUND_HALF_UP
import json
import re
import struct
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).parent
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
        'meta', 'param', 'source', 'track', 'wbr'}
EMOJI_RANGES = [(0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2B00, 0x2BFF),
                (0xFE00, 0xFE0F), (0x1F1E6, 0x1F1FF), (0x2700, 0x27BF)]

failures = []
notes = []


def check(condition, message):
    print(('  PASS  ' if condition else '  FAIL  ') + message)
    if not condition:
        failures.append(message)
    return condition


class Structure(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.problems = []
        self.tags = set()

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        if tag not in VOID:
            self.stack.append((tag, self.getpos()))

    def handle_startendtag(self, tag, attrs):
        self.tags.add(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack:
            self.problems.append('stray </{}> at {}'.format(tag, self.getpos()))
            return
        if self.stack[-1][0] != tag:
            self.problems.append('</{}> closes <{}> opened at {}'.format(
                tag, self.stack[-1][0], self.stack[-1][1]))
            return
        self.stack.pop()


def check_html(path):
    print('\nHTML structure -- {}'.format(path.name))
    text = path.read_text(encoding='utf-8')
    lowered = text.lower()
    # The moved template carries its own wrappers. In the audit packet this file
    # was a fragment and an artifact publisher supplied the document around it,
    # so these four checks asserted the wrappers were ABSENT. A page served from
    # the report folder has no such publisher, so the assertion is now the
    # opposite one: the document has to be complete on its own.
    check(lowered.lstrip().startswith('<!doctype html>'), 'starts with <!DOCTYPE html>')
    for tag in ('html', 'head', 'body'):
        # \b so that <header> is not mistaken for <head>.
        check(re.search(r'<' + tag + r'\b', lowered) is not None and '</' + tag + '>' in lowered,
              '<{0}> ... </{0}> wrapper'.format(tag))
    check(lowered.count('<title>') == 1 and '<title>Mentat Pocket</title>' in text,
          'exactly one <title>Mentat Pocket</title>')

    parser = Structure()
    parser.feed(text)
    check(not parser.problems, 'no mismatched end tags' +
          ('' if not parser.problems else ' -- ' + '; '.join(parser.problems[:4])))
    check(not parser.stack, 'no unclosed elements' +
          ('' if not parser.stack else ' -- ' + '; '.join(t for t, _ in parser.stack)))

    required = [
        ('name="viewport"', 'width=device-width, initial-scale=1, viewport-fit=cover'),
        ('name="apple-mobile-web-app-capable"', 'content="yes"'),
        ('name="apple-mobile-web-app-status-bar-style"', 'content="default"'),
        ('name="apple-mobile-web-app-title"', 'content="Mentat"'),
    ]
    for needle, value in required:
        check(needle in text and value in text, 'meta {} present'.format(needle))
    check('rel="apple-touch-icon" href="icon-180.png"' in text, 'apple-touch-icon link')
    check('rel="manifest" href="manifest.webmanifest"' in text, 'manifest link')
    check('env(safe-area-inset-top)' in text and 'env(safe-area-inset-bottom)' in text
          and 'env(safe-area-inset-left)' in text and 'env(safe-area-inset-right)' in text,
          'safe-area insets on all four sides')
    check('-webkit-text-size-adjust:100%' in text, '-webkit-text-size-adjust: 100%')
    check('prefers-reduced-motion' in text, 'prefers-reduced-motion respected')
    check('font-variant-numeric:tabular-nums' in text, 'tabular-nums on numbers')

    # Theme rules: the complete light palette on bare :root, tokens only after.
    check(text.count(':root:not([data-theme="light"])') == 1,
          'dark media block guarded as :root:not([data-theme="light"])')
    check(text.count(':root[data-theme="dark"]') == 1, ':root[data-theme="dark"] block present')
    check(re.search(r'body\{[^}]*background:var\(--page\)', text) is not None,
          'body sets an explicit token background')
    for token in ('--paper:#fcf7f4', '--ivory:#f7ede4', '--plum:#342649', '--purple:#57447b',
                  '--gold:#eca91c', '--lilac:#d6c8e2', '--teal:#327a7a', '--line:#e4d9d0',
                  '--muted:#716a64', '--negative:#bf3f36'):
        check(token in text, 'console token {}'.format(token))
    check('--party-dem:#2f5fa8' in text and '--party-rep:#b8322e' in text
          and '--party-other:#9a9297' in text, 'light party marks are the validated hexes')
    check('--party-dem:#4a80d0' in text and '--party-rep:#c9564f' in text,
          'dark party marks are the stepped-up hexes')

    # Only Google Fonts may be fetched; nothing else external, no external script.
    externals = re.findall(r'(?:src|href)="(https?://[^"]+)"', text)
    allowed = [u for u in externals if u.startswith('https://fonts.googleapis.com')
               or u.startswith('https://fonts.gstatic.com')]
    check(len(externals) == len(allowed),
          'every external URL is a Google Fonts host -- found {}'.format(externals))
    check(not re.search(r'<script[^>]+src=', text), 'no external <script src>')
    check(text.count('<script>') == 1 and text.count('</script>') == 1, 'exactly one script block')

    bad = sorted({ch for ch in text
                  if any(low <= ord(ch) <= high for low, high in EMOJI_RANGES)})
    check(not bad, 'no emoji' + ('' if not bad else ' -- found ' + repr(bad)))
    check('JSON.parse(' not in text.split('var DATA')[1].split(';')[0],
          'data is a JS object literal, not JSON.parse')
    return text


def check_script(text):
    print('\nScript -- node --check')
    body = text.split('<script>', 1)[1].rsplit('</script>', 1)[0]
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as handle:
        handle.write(body)
        temporary = handle.name
    try:
        result = subprocess.run(['node', '--check', temporary], capture_output=True, text=True)
        check(result.returncode == 0, 'node --check on the inlined script' +
              ('' if result.returncode == 0 else ' -- ' + (result.stderr or '').strip()[:400]))
    except FileNotFoundError:
        notes.append('node not on PATH; node --check was skipped')
        print('  SKIP  node not on PATH')
    finally:
        Path(temporary).unlink(missing_ok=True)

    print('\nInlined data literal')
    literal = body.split('/*__DATA_START__*/', 1)[1].split('/*__DATA_END__*/', 1)[0]
    parsed = None
    try:
        parsed = json.loads(literal.replace('<\\/', '</'))
        check(True, 'inlined data object parses ({} bytes, {} industries)'.format(
            len(literal), len(parsed.get('ind', []))))
    except ValueError as error:
        check(False, 'inlined data object parses -- ' + str(error))
    check('</script' not in literal.lower(), 'no raw </script> inside the data literal')
    return parsed


def check_data(payload, label):
    print('\nData integrity -- {} (recomputed from the raw result sets)'.format(label))
    check(bool(payload.get('g')) and payload.get('u') == 'cents',
          'generated_at {!r}, amounts in {!r}'.format(payload.get('g'), payload.get('u')))
    check(payload.get('b') == 'Published included receipts',
          'basis label is {!r}'.format(payload.get('b')))
    # The audit packet always carried all eight industries, so this asserted
    # exactly eight. The publisher takes a dataset selection, so the count is
    # data now, not a contract; what must hold is that the snapshot is not
    # empty. The names are still printed, so a short publish is visible.
    check(bool(payload['ind']), '{} industr{}: {}'.format(
        len(payload['ind']), 'y' if len(payload['ind']) == 1 else 'ies',
        ', '.join(i['n'] for i in payload['ind'])))

    for industry in payload['ind']:
        name = industry['n']
        cycles = industry['cyc']
        scopes = [('industry', industry['S'])] + [
            (industry['co'][i], block) for i, block in enumerate(industry['C'])]

        # 1. company totals must sum to the industry total, per cycle, to the cent.
        for index, label_cycle in enumerate(cycles):
            industry_amount = sum(r[1] for r in industry['S']['t'] if r[0] == index)
            industry_records = sum(r[2] for r in industry['S']['t'] if r[0] == index)
            company_amount = sum(r[1] for block in industry['C'] for r in block['t'] if r[0] == index)
            company_records = sum(r[2] for block in industry['C'] for r in block['t'] if r[0] == index)
            check(industry_amount == company_amount and industry_records == company_records,
                  '{} {}: company sum {} cents / {} records == industry {} / {}'.format(
                      name, label_cycle, company_amount, company_records,
                      industry_amount, industry_records))

        # 2. recipient_category and party must each partition the same receipts.
        for scope_name, block in scopes:
            for index, label_cycle in enumerate(cycles):
                total_amount = sum(r[1] for r in block['t'] if r[0] == index)
                total_records = sum(r[2] for r in block['t'] if r[0] == index)
                for key, dimension in (('k', 'recipient_category'), ('p', 'party')):
                    part_amount = sum(r[2] for r in block[key] if r[0] == index)
                    part_records = sum(r[3] for r in block[key] if r[0] == index)
                    if part_amount != total_amount or part_records != total_records:
                        check(False, '{} / {} / {}: {} sums to {} cents / {} records, '
                                     'cycle total is {} / {}'.format(
                                         name, scope_name, label_cycle, dimension,
                                         part_amount, part_records, total_amount, total_records))

        # 3. every interned index must resolve, and every top list must be
        #    ranked by amount, at most eight long.
        for scope_name, block in scopes:
            for row in block['k']:
                if not 0 <= row[1] < len(industry['cat']):
                    check(False, '{} / {}: category index out of range'.format(name, scope_name))
            for row in block['p']:
                if not 0 <= row[1] < len(industry['par']):
                    check(False, '{} / {}: party index out of range'.format(name, scope_name))
            for key, rows in block['r'].items():
                if len(rows) > 8:
                    check(False, '{} / {} / {}: {} recipients, expected at most 8'.format(
                        name, scope_name, key, len(rows)))
                amounts = [row[1] for row in rows]
                if amounts != sorted(amounts, reverse=True):
                    check(False, '{} / {} / {}: recipients not ranked by amount'.format(
                        name, scope_name, key))
                for row in rows:
                    if not 0 <= row[0] < len(industry['rec']):
                        check(False, '{} / {}: recipient index out of range'.format(name, scope_name))
    check(True, 'category and party partitions, index ranges and recipient ranking verified '
                'for {} scopes'.format(sum(1 + len(i['C']) for i in payload['ind'])))

    # 4. Collection gaps (FIX 4). The page words each cell as "N% of FEC-reported records", so the
    #    percent it prints must be the cell's own counts, rounded half up: recomputed here with
    #    Decimal, not read back. A held count at or above what FEC reported is not a shortfall.
    for industry in payload['ind']:
        gaps = industry.get('gp')
        if gaps is None:
            continue
        bad = []
        for cell in gaps.get('c', []):
            if len(cell) != 6:
                bad.append(repr(cell))
                continue
            company, cycle, destination, held, reported, shown = cell
            exact = (Decimal(held) * 100 / Decimal(reported)).quantize(Decimal('1'), rounding=ROUND_HALF_UP) \
                if isinstance(reported, int) and reported > 0 else None
            if not (company and cycle and destination and isinstance(held, int) and 0 <= held < (reported or 0)
                    and exact is not None and shown == exact):
                bad.append('{}/{} {} {} of {} printed as {}%'.format(company, cycle, destination, held, reported, shown))
        check(gaps.get('f') == 1 and isinstance(gaps.get('u'), int) and gaps['u'] >= 0 and not bad,
              '{} collection gaps: {} cells, each percent recomputed from its counts{}'.format(
                  industry['n'], len(gaps.get('c', [])), '' if not bad else ' -- ' + '; '.join(bad[:4])))


def check_icon(path):
    print('\nIcon -- {}'.format(path.name))
    raw = path.read_bytes()
    check(raw[:8] == b'\x89PNG\r\n\x1a\n', 'PNG signature')
    width, height = struct.unpack('>II', raw[16:24])
    check((width, height) == (180, 180), 'dimensions {}x{}'.format(width, height))
    check(raw.endswith(b'IEND\xaeB`\x82'), 'IEND terminator present')


def check_manifest(path):
    print('\nManifest -- {}'.format(path.name))
    value = json.loads(path.read_text(encoding='utf-8'))
    check(value.get('name') == 'Mentat Pocket', 'name')
    check(value.get('short_name') == 'Mentat', 'short_name')
    check(value.get('display') == 'standalone', 'display')
    check(value.get('background_color') == '#fcf7f4', 'background_color')
    check(value.get('theme_color') == '#f7ede4', 'theme_color')
    check(value.get('start_url') == './', 'start_url')
    icons = value.get('icons') or []
    check(len(icons) == 1 and icons[0] == {'src': 'icon-180.png', 'sizes': '180x180',
                                           'type': 'image/png'}, 'icons entry')


def main():
    parser = argparse.ArgumentParser(description='Static self-check for the Mentat Pocket deliverables.')
    parser.add_argument('--dir', dest='directory', default='',
                        help='Folder holding the built page and its data; default is this script folder')
    parser.add_argument('--data', default='',
                        help='pocket-data.json to check; default is pocket-data.json inside --dir')
    options = parser.parse_args()
    # --dir defaults to the directory of the pocket-data.json being checked, and
    # that in turn defaults to this script's own folder, so running selfcheck.py
    # with no arguments behaves exactly as it did in the audit packet.
    data = Path(options.data) if options.data else None
    directory = Path(options.directory) if options.directory else (data.parent if data else HERE)
    if data is None:
        data = directory / 'pocket-data.json'

    page = directory / 'mentat-pocket.html'
    text = check_html(page)
    parsed = check_script(text)
    if parsed:
        check_data(parsed, 'inlined in ' + page.name)
    check_data(json.loads(data.read_text(encoding='utf-8')), data.name)
    check_icon(directory / 'icon-180.png')
    check_manifest(directory / 'manifest.webmanifest')

    print('\nSizes')
    for name in ('mentat-pocket.html', 'pocket-data.json', 'icon-180.png',
                 'manifest.webmanifest', 'pocket-integrity.json'):
        path = directory / name
        print('  {:<28} {:>9,} bytes'.format(name, path.stat().st_size if path.exists() else -1))
    # The extractor is source, not a deliverable: it stays with this script.
    print('  {:<28} {:>9,} bytes'.format('extract_pocket_data.py',
                                         (HERE / 'extract_pocket_data.py').stat().st_size
                                         if (HERE / 'extract_pocket_data.py').exists() else -1))

    print('\n' + ('ALL CHECKS PASSED' if not failures
                  else '{} CHECK(S) FAILED'.format(len(failures))))
    for failure in failures:
        print('  - ' + failure)
    for note in notes:
        print('  note: ' + note)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
