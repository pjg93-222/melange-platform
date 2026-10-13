"""Content-level parity of a Mentat compute job's outputs (D-173, D-177): the Windows run against the container run.

Bytes are the wrong test (openpyxl, Excel and SQLite stamp their own metadata; the platforms write different line endings), so each
file is read as what it holds and compared by content: JSON by leaf, CSV by cell, SQLite per table (schema, row count, a digest per
column over rows in primary-key order), workbooks per cell (formula and cached value), zips per decompressed member, text with
line endings normalised. The method is the design's section 4:

    1. variance A1 A2 --out mask.json   run the Windows job twice; what differs between those runs is run noise, and only that is masked.
    2. compare A B --mask mask.json     A (a Windows run) against B (the container run), same frozen inputs. The mask is refused unless
                                        it was derived from this A; nothing is ever masked from the container side.
    --map C:/Users/pgoff/Mentat=/mentat  declares the mount: values that differ only by that prefix count as PATH_ONLY, and say so.

Classes per file: BYTE_EQUAL, <KIND>_CANONICAL (equal as content), EOL_ONLY, PATH_ONLY, DIFFERENT. The run fails on any DIFFERENT file or
any file present on one side only. Cell styles, column widths and workbook table definitions are not compared.
"""
import argparse
import csv
import fnmatch
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import zipfile

SCHEMA = 'mentat-parity-mask-v1'
TEXT = {'.txt', '.md', '.log', '.html', '.js', '.css', '.ps1', '.py', '.r', '.yaml', '.yml', '.tsv'}
KINDS = {'.json': 'JSON', '.csv': 'CSV', '.sqlite': 'SQLITE', '.sqlite3': 'SQLITE', '.db': 'SQLITE', '.xlsx': 'OOXML', '.zip': 'ZIP'}


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def kind(path):
    suffix = Path(path).suffix.lower()
    return KINDS.get(suffix) or ('TEXT' if suffix in TEXT else 'BYTES')


def _flatten(value, prefix, out):
    if isinstance(value, dict) and value:
        for key in sorted(value):
            _flatten(value[key], prefix + '/' + str(key).replace('~', '~0').replace('/', '~1'), out)
    elif isinstance(value, list) and value:
        for index, item in enumerate(value):
            _flatten(item, prefix + '/' + str(index), out)
    else:
        out[prefix or '/'] = value


def _json_view(path):
    out = {}
    _flatten(json.loads(Path(path).read_text(encoding='utf-8-sig')), '', out)
    return out


def _csv_view(path):
    out = {}
    with open(path, newline='', encoding='utf-8-sig') as stream:
        rows = 0
        for row_number, row in enumerate(csv.reader(stream), 1):
            rows = row_number
            for column, cell in enumerate(row, 1):
                out['r%d/c%d' % (row_number, column)] = cell
    out['rows'] = rows
    return out


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _sqlite_view(path):
    out, db = {}, sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        for name, type_, sql in db.execute("SELECT name, type, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall():
            out['%s:%s/sql' % (type_, name)] = sql
            if type_ != 'table':
                continue
            info = db.execute('PRAGMA table_info(%s)' % _quote(name)).fetchall()
            columns = [row[1] for row in info]
            order = ', '.join(_quote(row[1]) for row in sorted(info, key=lambda r: r[5]) if row[5]) or ', '.join(str(i + 1) for i in range(len(columns)))
            digests, rows = {column: hashlib.sha256() for column in columns}, 0
            try:
                for row in db.execute('SELECT * FROM %s ORDER BY %s' % (_quote(name), order)):
                    rows += 1
                    for column, value in zip(columns, row):
                        digests[column].update(repr(value).encode('utf-8') + b'\x1f')
            except sqlite3.Error as error:               # a virtual table whose module is absent: its schema is still compared
                out['table:%s/error' % name] = str(error)
                continue
            out['table:%s/rows' % name] = rows
            for column in columns:
                out['table:%s/col:%s' % (name, column)] = digests[column].hexdigest()
    finally:
        db.close()
    return out


def _xlsx_view(path):
    import openpyxl
    from openpyxl.utils import get_column_letter
    out = {}
    for data_only, suffix in ((False, ''), (True, '@cached')):
        book = openpyxl.load_workbook(path, read_only=True, data_only=data_only)
        out['sheets' + suffix] = json.dumps(book.sheetnames)
        for sheet in book.worksheets:
            for row_number, row in enumerate(sheet.iter_rows(values_only=True), 1):
                for column, value in enumerate(row, 1):
                    if value is not None:
                        out['%s!%s%d%s' % (sheet.title, get_column_letter(column), row_number, suffix)] = value if isinstance(value, (str, int, float, bool)) else str(value)
        book.close()
    return out


def _zip_view(path):
    out = {}
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            out[name] = hashlib.sha256(archive.read(name)).hexdigest()
    out['members'] = json.dumps(sorted(out))
    return out


def _text_view(path):
    return {'text': hashlib.sha256(Path(path).read_bytes().replace(b'\r\n', b'\n')).hexdigest()}


def view(path):
    """A file as a flat {location: value} mapping of what it holds."""
    reader = {'JSON': _json_view, 'CSV': _csv_view, 'SQLITE': _sqlite_view, 'OOXML': _xlsx_view, 'ZIP': _zip_view, 'TEXT': _text_view}.get(kind(path))
    return reader(path) if reader else {'bytes': sha256_file(path)}


def tree(root, ignore=()):
    root = Path(root)
    return {p.relative_to(root).as_posix(): p for p in sorted(root.rglob('*')) if p.is_file() and not any(fnmatch.fnmatch(p.relative_to(root).as_posix(), g) for g in ignore)}


def tree_digest(root, ignore=()):
    return hashlib.sha256('\n'.join('%s %s' % (rel, sha256_file(path)) for rel, path in tree(root, ignore).items()).encode()).hexdigest()


def _normalise(value, maps):
    """A path-like string with every declared mount prefix replaced by its container form and backslashes made slashes."""
    if not isinstance(value, str) or not maps:
        return value
    hit = False
    for host, container in maps:
        for form in {host, host.replace('/', '\\')}:
            if form.lower() in value.lower():
                value, hit = re.sub(re.escape(form), lambda _m: container, value, flags=re.I), True
    return value.replace('\\', '/') if hit or re.match(r'[A-Za-z]:\\', value) else value        # ponytail: prefix replacement, not path parsing; a message quoting a path is covered, a relative path is not


def variance(a1, a2, ignore=()):
    """The mask: per file, the locations that differ between two runs of the same job on the same frozen inputs."""
    one, two, files = tree(a1, ignore), tree(a2, ignore), {}
    unstable = sorted(set(one) ^ set(two))
    for rel in sorted(set(one) & set(two)):
        if sha256_file(one[rel]) == sha256_file(two[rel]):
            continue
        v1, v2 = view(one[rel]), view(two[rel])
        noisy = sorted(k for k in set(v1) | set(v2) if v1.get(k) != v2.get(k) or (k in v1) != (k in v2))
        if noisy:
            files[rel] = noisy
    return {'schema': SCHEMA, 'a1_digest': tree_digest(a1, ignore), 'a2_digest': tree_digest(a2, ignore), 'ignore': list(ignore), 'unstable_files': unstable, 'files': files}


def _pair(a, b, masked, maps):
    if sha256_file(a) == sha256_file(b):
        return 'BYTE_EQUAL', []
    va, vb = view(a), view(b)
    keys = (set(va) | set(vb)) - set(masked)
    raw = sorted(k for k in keys if (k in va) != (k in vb) or va.get(k) != vb.get(k))
    mapped = sorted(k for k in raw if (k in va) != (k in vb) or _normalise(va.get(k), maps) != _normalise(vb.get(k), maps))
    if mapped:
        return 'DIFFERENT', mapped
    if raw:
        return 'PATH_ONLY', raw
    name = kind(a)
    return {'TEXT': 'EOL_ONLY', 'BYTES': 'MASKED'}.get(name, name + '_CANONICAL'), []


def compare(a, b, mask=None, maps=(), ignore=()):
    """Compare run A (the reference) with run B. -> report dict; report['ok'] is the verdict."""
    if mask is not None:
        if mask.get('schema') != SCHEMA or tree_digest(a, ignore) not in (mask.get('a1_digest'), mask.get('a2_digest')):
            raise ValueError('The mask was not derived from this reference run (neither A1 nor A2 digest matches); masks come only from two runs of the reference side.')
    one, two = tree(a, ignore), tree(b, ignore)
    allowed = set((mask or {}).get('unstable_files', []))
    report = {'only_in_a': sorted(set(one) - set(two) - allowed), 'only_in_b': sorted(set(two) - set(one) - allowed), 'files': {}, 'counts': {}}
    for rel in sorted(set(one) & set(two)):
        verdict, where = _pair(one[rel], two[rel], (mask or {}).get('files', {}).get(rel, ()), maps)
        report['files'][rel] = {'class': verdict, 'where': where[:20]}
        report['counts'][verdict] = report['counts'].get(verdict, 0) + 1
    report['ok'] = not (report['only_in_a'] or report['only_in_b'] or report['counts'].get('DIFFERENT'))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    noise = commands.add_parser('variance')
    noise.add_argument('a1')
    noise.add_argument('a2')
    noise.add_argument('--out', required=True)
    both = commands.add_parser('compare')
    both.add_argument('a')
    both.add_argument('b')
    both.add_argument('--mask')
    both.add_argument('--map', action='append', default=[], metavar='HOST=CONTAINER')
    both.add_argument('--report')
    for sub in (noise, both):
        sub.add_argument('--ignore', action='append', default=[], help='glob of run-specific files to leave out (receipts, logs, Glass Box events)')
    args = parser.parse_args(argv)
    if args.command == 'variance':
        mask = variance(args.a1, args.a2, args.ignore)
        Path(args.out).write_text(json.dumps(mask, indent=1) + '\n', encoding='utf-8')
        print(json.dumps({'masked_files': len(mask['files']), 'masked_locations': sum(map(len, mask['files'].values())), 'unstable_files': len(mask['unstable_files'])}))
        return 0
    mask = json.loads(Path(args.mask).read_text(encoding='utf-8')) if args.mask else None
    report = compare(args.a, args.b, mask, [tuple(pair.split('=', 1)) for pair in args.map], args.ignore)
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'ok': report['ok'], 'counts': report['counts'], 'only_in_a': report['only_in_a'], 'only_in_b': report['only_in_b'],
                      'different': {rel: v['where'] for rel, v in report['files'].items() if v['class'] == 'DIFFERENT'}}, indent=1))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
