"""Inline pocket-data.json into mentat-pocket.html between its data markers.

Idempotent: the markers survive the rewrite, so the build can run again over an
already-built page. The data is emitted as a JS object literal (JSON is a subset
of JS object-literal syntax), never as a JSON.parse call.

--data names the snapshot to inline and --out the page to write. The template
read is always this folder's mentat-pocket.html, so a publish can build into the
report folder and leave the repository's template alone. With neither flag the
original in-place behaviour is unchanged.
"""
import argparse
import json
import re
from pathlib import Path

HERE = Path(__file__).parent
PAGE = HERE / 'mentat-pocket.html'
DATA = HERE / 'pocket-data.json'
PATTERN = re.compile(r'(/\*__DATA_START__\*/).*?(/\*__DATA_END__\*/)', re.S)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default=str(DATA), help='Snapshot JSON to inline')
    parser.add_argument('--out', default='', help='Page to write; default is the template, in place')
    options = parser.parse_args()
    target = Path(options.out) if options.out else PAGE
    payload = json.loads(Path(options.data).read_text(encoding='utf-8'))
    literal = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
    # A JS source file must not contain a raw </script> or U+2028/U+2029.
    literal = (literal.replace('</', '<\\/')
                      .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))
    page = PAGE.read_text(encoding='utf-8')
    if not PATTERN.search(page):
        raise SystemExit('Data markers not found in ' + str(PAGE))
    built = PATTERN.sub(lambda m: m.group(1) + literal + m.group(2), page, count=1)
    target.parent.mkdir(parents=True, exist_ok=True)
    # CRLF written explicitly: the bytes Windows text mode gives, on every platform (compute parity, D-177).
    with open(target, 'w', encoding='utf-8', newline='\r\n') as stream:
        stream.write(built)
    print('inlined {} bytes of data; {} is now {} bytes'.format(
        len(literal), target, target.stat().st_size))


if __name__ == '__main__':
    main()
