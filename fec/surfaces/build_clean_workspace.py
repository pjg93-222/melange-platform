"""Build the selected local workspace with lazy records and isolated analytical engines.

No models are rerun and published/offline artifacts are never overwritten.
"""
from collections import defaultdict
from pathlib import Path, PureWindowsPath
import datetime
import gzip
import hashlib
import importlib.util
import json
import re
import shutil
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import to_host  # noqa: E402  (a hard-coded Windows root maps onto the container mount; identity on Windows)

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(__file__).parent / 'approved/clean'
OUT = Path(__file__).parent / 'staffer_live/approved'


def write_json(name, value):
    p = OUT / 'data' / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=True, separators=(',', ':')), encoding='utf-8', newline='\r\n')


def page_data(name):
    text = (ROOT / name).read_text(encoding='utf-8')
    match = re.search(r'<script[^>]+id=["\']?page-data["\']?[^>]*>(.*?)</script>', text, re.S)
    return json.loads(match[1])


def light_template(text):
    """Recolor stylesheet literals without touching original datasets or engines."""
    colors = {'#1d1912':'#f7ede4','#282113':'#fcf7f4','#2e2514':'#eee3db',
              '#3b2e16':'#e2d5e8','#433317':'#d8cbbc','#564019':'#bcaaa0',
              '#f3f3e6':'#342649','#d2cdbc':'#51405f','#a49d8a':'#6b5e73',
              '#9d9784':'#6b5e73','#eecd5c':'#57447b','#d2a63c':'#57447b',
              '#6b4e1f':'#765399','#86efac':'#216444','#34d399':'#216444',
              '#7ee787':'#216444','#fbbf24':'#785310','#eab308':'#785310',
              '#e3b341':'#785310','#f87171':'#a12e39','#ff7b72':'#a12e39',
              '#58a6ff':'#355d92','#60a5fa':'#355d92','#8b949e':'#6b5e73',
              '#c9d1d9':'#51405f','#2a2518':'#eee3db','#211d15':'#eee3db'}
    def sheet(match):
        return re.sub(r'#[a-fA-F0-9]{6}\b', lambda c: colors.get(c[0].lower(),c[0]), match[0])
    return re.sub(r'<style[^>]*>.*?</style>', sheet, text, flags=re.S)


# ---------------------------------------------------------------------------------------------------------------
# Build 01 (shared shell): data freshness for the page head, colour maps by reference, UI-kit and overlay injection.
# ---------------------------------------------------------------------------------------------------------------
APPROVED_URL = '/staffer-live/approved/'
# Frame document -> overlay files, injected after each page's own CSS and before restored-desk.js (desk) or at the end
# of <body> (other frames). Missing sources get an empty stub in the OUTPUT only; approved/clean is never written.
OVERLAYS = {
    'desk': [('desk-shell', '02'), ('desk-members', '02'), ('desk-compare', '03'), ('desk-profile', '04'),
             ('desk-bills', '05'), ('desk-efficiency', '06'), ('desk-coalition', '07')],
    'industry': [('industry-ui', '10, 11')],
    'champions': [('champions-ui', '12')],
    'interactions': [('interactions-ui', '14, 15')],
    'people': [('people-ui', '16')],
}
SHELL_STUBS = [('forecasts.css', '13')]
# Loading (r5). mlk-boot.js is the first script of every frame document (and of the shell, see index.html). It keeps
# the frame hidden until its v2 overlay has mounted, so the legacy layout never paints first; requests a workspace
# script that failed to load again, in place and in order (data-mlk marks the tags it may re-request); and shows a
# visible error instead of silently leaving the legacy layout (audit r4 mel-a F1, mel-b F-3). MOUNTED names what each
# frame's overlay sets once it has restructured the page ('' = styles only, no overlay script).
BOOT = 'mlk-boot.js'
MOUNTED = {'desk': 'body.dsk-v2', 'industry': 'html.ind-v2', 'champions': 'html.chm-ready', 'interactions': 'html.ixt-on',
           'research-bills': '', 'cu-fintech': '', 'people': 'nav.ppl-tabs', 'network': 'html.mlk-v8'}


def css_links(*names):
    return ''.join(f'<link rel="stylesheet" href="{APPROVED_URL}{n}.css">' for n in names)


def js_scripts(*names):
    return ''.join(f'<script src="{APPROVED_URL}{n}.js" data-mlk></script>' for n in names)


def overlay_names(frame):
    return [name for name, _owner in OVERLAYS.get(frame, [])]


def boot_tag(frame):
    """The loader tag; data-ready is the frame's mount marker (MOUNTED)."""
    return f'<script src="{APPROVED_URL}{BOOT}" data-frame="{frame}" data-ready="{MOUNTED[frame]}"></script>'


def with_boot(html, frame, head='<head>'):
    """The loader goes right after the document's own <head> (its first occurrence), before every other resource."""
    if head not in html:
        raise ValueError(f'{frame}: no {head!r} to place {BOOT} after')
    return html.replace(head, head + boot_tag(frame), 1)


def ensure_stubs():
    """Overlay stubs are source files in approved/clean; if one is missing, serve a valid empty stub instead of a 404."""
    wanted = [(f'{name}.{ext}', owner, frame) for frame, items in OVERLAYS.items() for name, owner in items for ext in ('css', 'js')]
    wanted += [(name, owner, 'shell') for name, owner in SHELL_STUBS]
    for filename, owner, frame in wanted:
        if not (SRC / filename).is_file():
            (OUT / filename).write_text(f'/* {filename}: overlay stub for the {frame} document; owner: prompt {owner}. */\n', encoding='utf-8', newline='\r\n')


def js_truthy(v):
    if v is None or v is False:
        return False
    if isinstance(v, (int, float)):
        return v == v and v != 0
    if isinstance(v, str):
        return v != ''
    return True


def js_number(v):
    """Number(v)||0, as used by the desk's exclusion total."""
    if isinstance(v, bool):
        n = 1.0 if v else 0.0
    elif isinstance(v, (int, float)):
        n = float(v)
    elif v is None:
        n = 0.0
    elif isinstance(v, str):
        s = v.strip()
        try:
            n = float(s) if s else 0.0
        except ValueError:
            n = float('nan')
    elif isinstance(v, list):
        n = 0.0 if not v else js_number(v[0]) if len(v) == 1 and not isinstance(v[0], (list, dict)) else float('nan')
    else:
        n = float('nan')
    return 0 if n != n or n == 0 else n


def js_text(v):
    """esc(v) of the desk (make_member_desk_00.tpl:1862) as text: null/undefined -> ''."""
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def en_us(n):
    """Number.prototype.toLocaleString() in en-US (the desk's reader locale): grouping, up to 3 fraction digits."""
    if float(n).is_integer():
        return f'{int(n):,}'
    return f'{n:,.3f}'.rstrip('0').rstrip('.')


def desk_freshness(D, source):
    """The desk's full-width #integritybar text, computed exactly as make_member_desk_00.tpl:446-449 and :514-518 build it."""
    fr = D.get('freshness')
    generated = fr.get('generated_at') if js_truthy(fr) and isinstance(fr, dict) else None
    generated = js_text(generated) if js_truthy(generated) else 'unknown'
    integrity = D.get('integrity')
    display = integrity.get('source_display') if js_truthy(integrity) and isinstance(integrity, dict) else None
    display = display if js_truthy(display) and isinstance(display, dict) else {}
    excluded = sum(js_number(v) for k, v in display.items() if re.search(r'(excluded|routed)', k))
    honors = D.get('rank_honors')
    honors = honors if js_truthy(honors) else None
    parts = [{'label': 'Data freshness:', 'text': f'generated {generated}'},
             {'label': 'Exclusion accounting:', 'text': f"{en_us(excluded)} source rows explicitly excluded or routed. Open a member's media sections for item-level reasons."}]
    line = f"Data freshness: generated {generated} · Exclusion accounting: {parts[1]['text']}"
    as_of = None
    if honors is not None:
        as_of = js_text(honors.get('as_of') if isinstance(honors, dict) else None)
        parts.append({'label': 'Recognition evidence:', 'text': f'through {as_of}.'})
        line += f' Recognition evidence: through {as_of}.'
    excluded = int(excluded) if float(excluded).is_integer() else excluded
    through = re.match(r'\d{4}-\d{2}-\d{2}', generated)
    return {'through': through[0] if through else None, 'generated_at': None if generated == 'unknown' else generated,
            'excluded_or_routed': excluded, 'recognition_as_of': as_of, 'parts': parts, 'line': line,
            'source': source, 'rule': 'make_member_desk_00.tpl:446-449 (freshness, exclusion accounting) and :514-518 (recognition evidence); '
                                      "the page head's chip shows the date part of generated_at as written (no time-zone conversion)"}


def js_literal_end(text, start):
    """Index just past the bracket that closes text[start] ('{' or '['); string- and comment-aware (JavaScript)."""
    depth, i, n = 0, start, len(text)
    while i < n:
        c = text[i]
        if c in '\'"`':
            q, i = c, i + 1
            while i < n and text[i] != q:
                i += 2 if text[i] == '\\' else 1
        elif text.startswith('//', i):
            nl = text.find('\n', i)
            i = n if nl < 0 else nl
            continue
        elif text.startswith('/*', i):
            e = text.find('*/', i + 2)
            i = n if e < 0 else e + 2
            continue
        elif c in '{[':
            depth += 1
        elif c in '}]':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def parse_js_literal(src):
    """Object/array literal with string, number or nested literal values; any other value is kept as {'$expr': text}."""
    pos = 0

    def ws():
        nonlocal pos
        while pos < len(src) and src[pos] in ' \t\r\n':
            pos += 1

    def string():
        nonlocal pos
        q, pos, out = src[pos], pos + 1, []
        while src[pos] != q:
            if src[pos] == '\\':
                out.append({'n': '\n', 't': '\t', 'r': '\r'}.get(src[pos + 1], src[pos + 1]))
                pos += 2
            else:
                out.append(src[pos])
                pos += 1
        pos += 1
        return ''.join(out)

    def value():
        nonlocal pos
        ws()
        c = src[pos]
        if c == '{':
            pos += 1
            obj = {}
            while True:
                ws()
                if src[pos] == '}':
                    pos += 1
                    return obj
                if src[pos] in '\'"':
                    key = string()
                else:
                    m = re.compile(r'[A-Za-z0-9_$\-.]+').match(src, pos)
                    key, pos = m[0], m.end()
                ws()
                pos += 1  # ':'
                obj[key] = value()
                ws()
                if src[pos] == ',':
                    pos += 1
        if c == '[':
            pos += 1
            arr = []
            while True:
                ws()
                if src[pos] == ']':
                    pos += 1
                    return arr
                arr.append(value())
                ws()
                if src[pos] == ',':
                    pos += 1
        if c in '\'"':
            return string()
        m = re.compile(r'-?\d+(\.\d+)?(?=\s*[,}\]])').match(src, pos)
        if m:
            pos = m.end()
            return float(m[0]) if '.' in m[0] else int(m[0])
        start, depth = pos, 0
        while pos < len(src) and not (depth == 0 and src[pos] in ',}]'):
            depth += src[pos] in '([{'
            depth -= src[pos] in ')]}'
            pos += 1
        return {'$expr': src[start:pos].strip()}

    return value()


COLOR_VALUE = re.compile(r'^(#[0-9a-fA-F]{3,8}|(rgba?|hsla?)\([^)]*\))$')


def colour_leaves(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from colour_leaves(v, path + (k,))
    elif isinstance(obj, str) and COLOR_VALUE.match(obj):
        yield path, obj


def key_pattern(key):
    """How an object key can be written in the source literal: quoted, or bare when it is an identifier or a number."""
    alts = ["'" + re.escape(key) + "'", '"' + re.escape(key) + '"']
    if re.fullmatch(r'[A-Za-z_$][\w$]*|\d+', key):
        alts.append(re.escape(key))
    return '(?:' + '|'.join(alts) + ')'


def extract_map(text, name):
    """Every occurrence of `NAME = {...}` / `NAME: {...}` (byte-exact slice), parsed; same rule as tools/colormap_check.py."""
    found = []
    for m in re.finditer(re.escape(name) + r'\s*[=:]\s*(?=[\{\[])', text):  # literal prefix: fast on 18 MB pages
        if m.start() and re.match(r'[A-Za-z0-9_$.]', text[m.start() - 1]):
            continue
        e = js_literal_end(text, m.end())
        if e > 0:
            found.append((text[m.start():e], text[m.end():e]))
    return found


def colour_maps(documents):
    """data/colors.json: the legacy data colour maps by reference, copied from the served documents and verified."""
    groups = [('desk', 'original-desk.html', 'cu-member-desk.html', 'fec/surfaces/templates/make_member_desk_00.tpl',
               ['STCOL', 'PTCOL', 'TIERCOL', 'HONOR_COLORS', 'STANDING_COLORS', 'PILL', 'POVCOL', 'RELCOL', 'TRK_COL',
                'INDUSTRY_FAMILY', 'BILL_LOBBY_SECTOR_COLORS']),
              ('influence', 'network.html', 'Moneyball Dashboard.html', 'fec/surfaces/templates/make_dashboard_html_00.tpl',
               ['STCOL', 'HONOR_COL', 'PTCOL', 'TIERCOL', 'ELCOL'])]
    out = {'schema': 'melange-colors-v1',
           'note': 'Legacy data colour maps by reference, copied byte-exactly from the served documents by build_clean_workspace.py. '
                   'Read them; never retype a value. provenance[] names the source file and variable of every map.',
           'desk': {}, 'influence': {}, 'industry': {}, 'provenance': []}
    for group, document, source, template, names in groups:
        text = documents[document]
        for name in names:
            hits = extract_map(text, name)
            record = {'map': f'{group}.{name}', 'variable': name, 'document': document, 'source': source, 'template': template,
                      'occurrences': len(hits)}
            if not hits:
                out['provenance'].append(dict(record, found=False))
                continue
            raw, literal = hits[0]
            parsed = parse_js_literal(literal)
            leaves = list(colour_leaves(parsed))
            verified = bool(leaves) and all(re.search(r'[{,\s]' + key_pattern(str(path[-1])) + r'\s*:\s*([\'"])' + re.escape(value) + r'\1', literal)
                                            for path, value in leaves)
            out[group][name] = parsed
            out['provenance'].append(dict(record, found=True, identical_occurrences=len({h[0] for h in hits}) == 1,
                                          sha256_lf=hashlib.sha256(raw.replace('\r\n', '\n').encode('utf-8')).hexdigest(),
                                          bytes=len(raw.encode('utf-8')), colours=len(leaves), verified=verified))
    # Industry tiers: TIER_COLORS in make_lobby_network_html.py, served as page-data tierMeta.colors.
    text = documents['original-industry.html']
    m = re.search(r'"colors"\s*:\s*(?=\{\s*"big")', text)
    record = {'map': 'industry.TIER_COLORS', 'variable': 'TIER_COLORS (served as page-data tierMeta.colors)',
              'document': 'original-industry.html', 'source': 'lobby-network.html', 'template': 'fec/surfaces/make_lobby_network_html.py'}
    if m:
        e = js_literal_end(text, m.end())
        raw, literal = text[m.start():e], text[m.end():e]
        parsed = json.loads(literal)
        leaves = list(colour_leaves(parsed))
        verified = bool(leaves) and all(f'"{path[-1]}":"{value}"' in literal.replace(' ', '') for path, value in leaves)
        out['industry']['TIER_COLORS'] = parsed
        out['provenance'].append(dict(record, found=True, occurrences=1, identical_occurrences=True,
                                      sha256_lf=hashlib.sha256(raw.replace('\r\n', '\n').encode('utf-8')).hexdigest(),
                                      bytes=len(raw.encode('utf-8')), colours=len(leaves), verified=verified))
    else:
        out['provenance'].append(dict(record, found=False))
    return out


def forecast_accuracy_html(name):
    """lobby-forecast's accuracy block, verbatim: the .honesty scope paragraph, the body of the one
    'How accurate this is' details, and the two retention receipts - cut exactly as build 13's splice.py cut the
    FC_ACCURACY snapshot in research.js (13 HANDOFF request R1). None when the page no longer has that shape."""
    try:
        html = (ROOT / name).read_bytes().decode('utf-8')

        def between(start, end, frm):
            i = html.index(start, frm)
            j = html.index(end, i + len(start))
            return html[i:j + len(end)], j + len(end)
        honesty, pos = between('<div class=honesty>', '</div>', html.index('<section class=panel id=forecast'))
        grid = html.index('<div class=forecastgrid>', pos)
        receipt1, _ = between('<p class=receipt>Ranked by predicted probability', '</p>', grid)
        receipt2, _ = between('<p class=receipt>To stay self-contained', '</p>', grid)
        summary = '<details class=methnote><summary>How accurate this is</summary>'
        details, _ = between(summary, '</details>', grid)
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if html.count('<details class=methnote>') != 1:
        return None
    return honesty + details[len(summary):-len('</details>')] + receipt1 + receipt2


# ---------------------------------------------------------------------------------------------------------------
# Build 16 (People > Directory, board #43): "Days on the record" per listed person, from the dated records the
# staffer service on 8841 already serves. Read-only over the staffer data files; nothing here writes outside OUT.
#   Offices: the payroll stints (core_start .. core_end_exclusive) of the pinned social-network generation that
#     staffer_graph_snapshot.StaticGraph loads and /api/staffer-person serves as node.stints, merged over the
#     service's identity aliases the way staffer_person_inventory.apply_identity_overlay merges stints.
#   Organisations: the LDA activity reports (report_start .. report_end_exclusive) in the pinned
#     lda_activity_v2.sqlite that /api/staffer-person-activities serves, naming any of the person's LDA ids under
#     that registrant (employer on file) or for that client (represented), for the ties the 2026-09-07 employment
#     ruling makes (the organisations/clients artifacts staffer_live_service._load_lda_employment reads).
# Each value is `covered` (days inside the union of those dated records: gaps between records are not counted; the
# same measure as tenure_days in data/engine/staffer_offices_section.json, which board #43 draws) or, when the
# records leave gaps, [covered, span] with span = last record end - first record start. It is a record span, never
# employment tenure (D-021). No record, no entry: the page shows a dash.
# ---------------------------------------------------------------------------------------------------------------
PEOPLE_DAYS_VERSION = 'people-days-v1'
PEOPLE_DAYS_DIR = 'people-days'
PEOPLE_DAYS_SHARDS = 64
LIVE_ROOT = Path(to_host(r'C:\Users\pgoff\OneDrive\Desktop\Melange Systems'))   # same default as staffer_live_server.DEFAULT_ROOT
STAFFER_GENERATION = 'staffer_social_network_20260908_v8_math_primary'  # staffer_graph_snapshot.GENERATION
STAFFER_VERIFICATION = 'staffer_social_network_20260908_v8_math_verification'
OFFICE_KINDS = ('member_office', 'committee', 'leadership')              # staffer_groups_index.OFFICE_CONTAINER_KINDS
LDA_PREFIX = 'lda:lobbyist:'


def staffer_root():
    """The tree holding the staffer data files: this one (LIVE), else LIVE itself (a staging copy has no audits/)."""
    for root in (ROOT, LIVE_ROOT):
        if (root / 'audits/staffer_live_runtime/identity_resolution_receipt.json').is_file():
            return root
    return None


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(8 << 20), b''):
            h.update(block)
    return h.hexdigest()


def gz_rows(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def day_number(text):
    try:
        return datetime.date.fromisoformat(str(text)[:10]).toordinal()
    except (TypeError, ValueError):
        return None


def days_value(intervals):
    """covered, or [covered, span] when the dated records leave gaps."""
    covered, end = 0, None
    for a, b in sorted(intervals):
        if end is None or a > end:
            covered, end = covered + b - a, b
        elif b > end:
            covered, end = covered + b - end, b
    span = max(b for _, b in intervals) - min(a for a, _ in intervals)
    return covered if covered == span else [covered, span]


def shard_of(text, shards=PEOPLE_DAYS_SHARDS):
    """FNV-1a (32 bit) over the UTF-16 code units of a group id; people-ui.js computes the same."""
    h, units = 0x811C9DC5, text.encode('utf-16-le')
    for i in range(0, len(units), 2):
        h = ((h ^ (units[i] | units[i + 1] << 8)) * 0x01000193) & 0xFFFFFFFF
    return h % shards


def people_days_inputs(root):
    """Every input file, bound to its receipt where it has one. Returns (paths, sources)."""
    audits, runtime, engine = root / 'audits/staffer_network', root / 'audits/staffer_live_runtime', root / 'data/engine'
    verification = json.loads((audits / STAFFER_VERIFICATION / 'staffer_social_network_verification_receipt.json').read_text(encoding='utf-8'))
    if verification.get('status') != 'PASS' or verification.get('failures'):
        raise ValueError('the pinned staffer graph verification has not passed')
    receipt_path = audits / STAFFER_GENERATION / 'staffer_social_network_receipt.json'
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    files = {x['role']: x for x in receipt['files']}
    spine = {x['role']: x for x in receipt['inputs']}['hill_person_spine']
    pin = json.loads((runtime / 'ecosystem_activity_pin.json').read_text(encoding='utf-8-sig'))
    if pin.get('status') != 'PASS':
        raise ValueError('the LDA activity index pin is not PASS')
    paths = {
        'generation_receipt': (receipt_path, next(x['primary_sha256'] for x in verification['twin_byte_identity'] if x['path'] == receipt_path.name)),
        'staffer_trajectories': (audits / STAFFER_GENERATION / files['staffer_trajectories']['path'], files['staffer_trajectories']['sha256']),
        'groups': (audits / STAFFER_GENERATION / files['groups']['path'], files['groups']['sha256']),
        'hill_person_spine': (audits / PureWindowsPath(spine['path']).parent.name / PureWindowsPath(spine['path']).name, spine['sha256']),
        'identity_resolution_receipt': (runtime / 'identity_resolution_receipt.json', None),
        'staffer_organisations_section': (engine / 'staffer_organisations_section.json', None),
        'staffer_clients_section': (engine / 'staffer_clients_section.json', None),
        'lda_activity_index': (runtime / pin['database'], pin['sha256']),
    }
    sources = {}
    for role, (path, want) in paths.items():
        got = file_sha256(path)
        if want is not None and got != want:
            raise ValueError(f'{role}: sha256 differs from its receipt ({path.name})')
        sources[role] = {'path': str(path), 'sha256': got}
    return {role: path for role, (path, _want) in paths.items()}, sources


def compute_people_days(paths):
    """{group id: {display person id: covered | [covered, span]}} and counts."""
    identity = json.loads(paths['identity_resolution_receipt'].read_text(encoding='utf-8'))
    aliases = identity.get('identity_aliases') or {}   # equal to /api/staffer-network-index identity_aliases

    def canonical(ident):
        seen = set()
        while ident in aliases and ident not in seen:
            seen.add(ident)
            ident = aliases[ident]
        return ident

    merged = defaultdict(set)                            # display person -> every record merged into it
    for alias in aliases:
        merged[canonical(alias)].add(alias)
    group_sources = defaultdict(set)
    for group in identity.get('identity_groups') or ():
        if group.get('status') in ('USER_CONFIRMED_SAME_PERSON', 'REFERENCE_RESOLVED_SAME_PERSON'):
            group_sources[canonical(group['primary_id'])].update(group.get('source_person_ids') or ())

    # Offices: an office is a container whose ROSTER kind is in scope (staffer_groups_index._kind_of reads the
    # node's kind; a stint's own container_kind can say 'unresolved' for a container the roster names a committee).
    offices = {row['group_id'] for row in gz_rows(paths['groups'])
               if row.get('group_kind') == 'container' and row.get('container_kind') in OFFICE_KINDS}
    office = defaultdict(list)
    for row in gz_rows(paths['staffer_trajectories']):
        person = canonical('person:' + row['spine_person_id'])
        for stint in row.get('stints') or ():
            if stint.get('container_id') in offices:
                a, b = day_number(stint.get('core_start')), day_number(stint.get('core_end_exclusive'))
                if a is not None and b is not None and b > a:
                    office[stint['container_id'], person].append((a, b))

    # Organisations: the ruling as shipped -- one employer per filer (a registrant card names them, else the clients
    # artifact's employer column) and every client tie -- and each person's LDA ids as
    # lda_status_labels.lda_source_ids collects them from the merged node.
    employer, ties = {}, defaultdict(set)
    for org in json.loads(paths['staffer_organisations_section'].read_text(encoding='utf-8')).get('organisations') or ():
        if org.get('kind') == 'registrant':
            for entry in org.get('lobbyists') or ():
                employer[entry['id']] = org['id']
    clients = json.loads(paths['staffer_clients_section'].read_text(encoding='utf-8'))
    for index, filers in enumerate(clients['t']):
        for filer in filers:
            ties[clients['f'][filer]].add(clients['c'][index])
            employer.setdefault(clients['f'][filer], clients['e'][clients['fe'][filer]])
    for source, registrant in employer.items():
        if registrant:
            ties[source].add(registrant)
    spine_lda = {'person:' + row['spine_person_id']: row['lda_source_person_id'] for row in gz_rows(paths['hill_person_spine'])
                 if str(row.get('lda_source_person_id') or '').startswith(LDA_PREFIX)}

    def lda(value):
        text = str(value or '').strip().removeprefix('person:')
        return text if text.startswith(LDA_PREFIX) else ''

    holders = {canonical('person:' + source) for source in ties} | {canonical(hill) for hill in spine_lda} | set(group_sources)
    people = {}
    for person in holders:
        ids = {lda(v) for record in (person, *merged.get(person, ())) for v in (record, spine_lda.get(record))}
        ids.update(lda(v) for v in group_sources.get(person, ()))
        ids.discard('')
        if any(ties.get(source) for source in ids):
            people[person] = ids
    periods = defaultdict(set)                           # (LDA id, registrant or client) -> report periods
    with sqlite3.connect('file:' + paths['lda_activity_index'].as_posix() + '?mode=ro&immutable=1', uri=True) as db:
        for source, firm, client, start, end in db.execute(
                'SELECT DISTINCT n.person, a.firm_id, a.client_id, a.start, a.end_exclusive FROM named n JOIN activity a ON a.id = n.activity'):
            a, b = day_number(start), day_number(end)
            if a is not None and b is not None and b > a:
                periods[source, firm].add((a, b))
                periods[source, client].add((a, b))
    db.close()

    days = defaultdict(dict)
    for (group, person), intervals in office.items():
        days[group][person] = days_value(intervals)
    organisation_entries = 0
    for person, ids in people.items():
        for group in {g for source in ids for g in ties.get(source, ())}:
            intervals = [p for source in ids for p in periods.get((source, group), ())]
            if intervals:
                days[group][person] = days_value(intervals)
                organisation_entries += 1
    gaps = sum(isinstance(v, list) for g in days.values() for v in g.values())
    counts = {'groups': len(days), 'entries': len(office) + organisation_entries, 'office_entries': len(office),
              'organisation_entries': organisation_entries, 'entries_with_gaps': gaps, 'identity_aliases': len(aliases)}
    return days, counts


def people_days():
    """data/people-days/index.json + shard-NN.json; reuses the last output when every input hash is unchanged."""
    target = OUT / 'data' / PEOPLE_DAYS_DIR
    meta = {'schema': 'melange-people-days-v1', 'producer': 'build_clean_workspace.py ' + PEOPLE_DAYS_VERSION,
            'label': 'Days on the record', 'available': False}
    try:
        root = staffer_root()
        if root is None:
            raise FileNotFoundError('no staffer data files (audits/staffer_live_runtime) under this tree or ' + str(LIVE_ROOT))
        paths, sources = people_days_inputs(root)
        try:
            prior = json.loads((target / 'index.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            prior = {}
        if (prior.get('available') and prior.get('producer') == meta['producer'] and prior.get('sources') == sources
                and prior.get('shards') == PEOPLE_DAYS_SHARDS
                and all((target / f'shard-{n:02d}.json').is_file() for n in range(PEOPLE_DAYS_SHARDS))):
            print(f"People days: inputs unchanged, kept {prior['counts']['entries']:,} entries.")
            return
        days, counts = compute_people_days(paths)
    except Exception as exc:  # the rest of the workspace does not depend on this column; the page shows the reason
        if target.exists():
            shutil.rmtree(target)
        meta['reason'] = f'{type(exc).__name__}: {exc}'
        write_json(f'{PEOPLE_DAYS_DIR}/index.json', meta)
        print('People days: NOT AVAILABLE -', meta['reason'])
        return
    if target.exists():
        shutil.rmtree(target)
    shards = [{} for _ in range(PEOPLE_DAYS_SHARDS)]
    for group in sorted(days):
        shards[shard_of(group)][group] = {person: days[group][person] for person in sorted(days[group])}
    for n, shard in enumerate(shards):
        write_json(f'{PEOPLE_DAYS_DIR}/shard-{n:02d}.json', {'schema': 'melange-people-days-shard-v1', 'shard': n, 'days': shard})
    meta.update(available=True, shards=PEOPLE_DAYS_SHARDS, shard_rule='FNV-1a 32-bit over the UTF-16 code units of the group id, modulo shards',
                value='covered, or [covered, span] when the dated records leave gaps',
                definition=('Days on the record: the days covered by the dated records that list this person under this office '
                            '(payroll stints) or organisation (lobbying reports), from the first to the last, gaps between records '
                            'not counted. It is a record span, not employment tenure. Senate payroll dates are reporting-period '
                            'edges and lobbying dates are report quarters. No dated record, no entry.'),
                counts=counts, sources=sources)
    write_json(f'{PEOPLE_DAYS_DIR}/index.json', meta)
    print(f"People days: {counts['entries']:,} entries for {counts['groups']:,} groups in {PEOPLE_DAYS_SHARDS} shards.")


def keep_rejected_alias(target, backup):
    """Keep the first alias this build replaced, once. Creates the review folder (audit r4 code F3: a tree without
    outputs/rebuild-review-2026-09-21 crashed here, and the chain's alias sync step with it)."""
    if target.exists() and not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(target, backup)


def alias_receipt_rebinder():
    """Run before main(); returns what to run after it, so a presentation rebuild keeps the alias receipt current.

    sync_live_dashboard_alias.py (the chain's guarded publication step) builds the shell and writes a receipt binding
    the shell's sha256 to this generation. Running this file directly rewrites the shell and used to leave that receipt
    behind, so clean_workspace_current() went False and the audits called the served dashboard stale (audit r4 code
    F4). When the receipt was current for this very generation before the build, it is re-bound to the new shell; with
    no receipt, an older format or another generation nothing is written: only the publication step binds one."""
    try:
        spec = importlib.util.spec_from_file_location('sync_live_dashboard_alias', Path(__file__).with_name('sync_live_dashboard_alias.py'))
        alias = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(alias)
        before = alias.current_receipt(ROOT)
    except Exception as exc:  # a staging tree has no publication step (no fec/paths.py)
        reason = f'{type(exc).__name__}: {exc}'
        return lambda: print(f'Alias receipt: not checked ({reason}).')

    def rebind():
        result = alias.rebind_after_rebuild(ROOT, before)
        if result.get('status') == 'PASS':
            print('Alias receipt: re-bound to the rebuilt shell (same generation).')
        else:
            print(f"Alias receipt: unchanged ({result.get('status')}); run fec/surfaces/sync_live_dashboard_alias.py to publish.")
    return rebind


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if not (SRC / BOOT).is_file():  # every frame document names it first; without it no view could load
        raise FileNotFoundError(f'{SRC / BOOT} is missing')
    d = json.loads((ROOT / 'member_desk_bundle.json').read_text(encoding='utf-8'))
    desk_source = (ROOT/'cu-member-desk.html').read_text(encoding='utf-8')
    try:  # the desk's own `const D=` is what its #integritybar was built from
        desk_d = json.JSONDecoder().raw_decode(desk_source.split('const D=', 1)[1])[0]
        freshness = desk_freshness(desk_d, 'cu-member-desk.html const D')
        del desk_d
    except (IndexError, ValueError):
        freshness = desk_freshness(d, 'member_desk_bundle.json')
    catalog = []
    for i, m in enumerate(d['members']):
        m = dict(m, id=str(i))
        write_json(f'member-{i}.json', m)
        fields = ['id','name','chamber','district','party','state','rank','score','invested','alloc',
                  'ind_money','money_source_status','eff_value','opt_alloc','gap','roi','committee',
                  'committees','overall_stance','stances','adjrank','bio','staffdir']
        row = {k: m.get(k) for k in fields}
        row['bio'] = {k: m.get('bio', {}).get(k) for k in ('bioguide','state','url')} if m.get('bio') else {}
        row['latest'] = max((a.get('date','') for a in m.get('articles', [])), default='')
        row['billCount'] = len(m.get('bills', []))
        row['articleCount'] = len(m.get('articles', []))
        catalog.append(row)
    write_json('catalog.json', {'members':catalog, 'cats':d['cats'], 'sources':d['source_updates'],
                               'asOf':d['rank_honors'].get('as_of'), 'icons':json.loads((ROOT/'outputs/higgsfield-tab-mockups-2026-09-21/standardized/navigation-icons.json').read_text(encoding='utf-8')),
                               'freshness':freshness})
    # Champions is its own signed-attention data product, never dossier action counts.
    write_json('champions.json', page_data('lobby-champions.html'))
    write_json('desk-support.json', {'members_heavy':[
        {k:m.get(k,[]) for k in ('bills','staffdir')} for m in d['members']],
        'bill_lobbying':d.get('bill_lobbying',{})})
    write_json('bills.json', d['bill_live'])
    for key, name in [('research-bills','lobby-bills.html'),('forecasts','lobby-forecast.html'),
                      ('cu-fintech','lobby-screen.html'),('industry','lobby-network.html'),
                      ('interactions','lobby-interactions.html')]:
        value = page_data(name)
        if key == 'forecasts' and isinstance(value, dict):
            accuracy = forecast_accuracy_html(name)
            if accuracy:  # research.js reads d.accuracy_html and keeps FC_ACCURACY only as the fallback
                value['accuracy_html'] = accuracy
        write_json(key+'.json', value)
    # Engines retain their own DOM and event listeners, and exist only on demand.
    network = (ROOT/'Moneyball Dashboard.html').read_text(encoding='utf-8')
    palette = {'#1D1912':'#17131d','#282113':'#342649','#2E2514':'#403153','#3B2E16':'#49385f',
               '#433317':'#645478','#564019':'#8e7b9d','#9D9784':'#d6c9dc','#A49D8A':'#d6c9dc',
               '#D2CDBC':'#f7ede4','#F3F3E6':'#f7ede4','#EECD5C':'#eca91c','#D2A63C':'#eca91c'}
    for old,new in palette.items(): network = network.replace(old,new)
    network = re.sub(r"ctx.globalAlpha=0.16;ctx.fillStyle='#[A-Za-z0-9]+';ctx.beginPath\(\);ctx.arc\(sx,sy,r\+11,0,7\);ctx.fill\(\);ctx.globalAlpha=1;", '', network)
    network = network.replace('</head>', css_links('ui-kit') + '<link rel="stylesheet" href="/staffer-live/approved/network.css"></head>')
    network = network.replace('</body>', js_scripts('ui-kit') + '<script src="/staffer-live/approved/network.js" data-mlk></script></body>')
    network = with_boot(network, 'network')
    network = re.sub(r'function fit\(\)\{[^\n]*\}', lambda _: 'function fit(){if(W<=48||H<=72)return;const xs=LX.filter(Number.isFinite),ys=LY.filter(Number.isFinite);if(!xs.length||!ys.length)return;const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);cam.s=Math.min((W-48)/Math.max(1,x1-x0),(H-72)/Math.max(1,y1-y0));cam.tx=W/2-(x0+x1)*cam.s/2;cam.ty=H/2-(y0+y1)*cam.s/2;}', network, count=1)
    (OUT/'network.html').write_text(network, encoding='utf-8', newline='\r\n')
    desk = desk_source.replace('src="cu-member-desk-profiles.js"','src="/cu-member-desk-profiles.js"')
    (OUT/'advanced-desk.html').write_text(desk, encoding='utf-8', newline='\r\n')
    restored = light_template(desk).replace('<script src="/cu-member-desk-profiles.js" async></script>', '')
    # ui-kit, then the desk's restyle, then the prompt overlays; overlay scripts run before restored-desk.js so they can wrap legacy functions.
    restored = restored.replace('</head>', css_links('ui-kit') + '<link rel="stylesheet" href="/staffer-live/approved/restored.css">' + css_links(*overlay_names('desk')) + '</head>')
    restored = restored.replace('<body>', '<body class="restored-desk">')
    restored = restored.replace('</body>', '<script src="/staffer-live/approved/restored-links.js" data-mlk></script>' + js_scripts('ui-kit', *overlay_names('desk')) + '<script src="/staffer-live/approved/restored-desk.js" data-mlk></script></body>')
    restored = with_boot(restored, 'desk')
    (OUT/'original-desk.html').write_text(restored, encoding='utf-8', newline='\r\n')
    documents = {'network.html': network, 'original-desk.html': restored}
    for key, source in [('industry','lobby-network.html'),('champions','lobby-champions.html'),
                        ('research-bills','lobby-bills.html'),('interactions','lobby-interactions.html'),
                        ('cu-fintech','lobby-screen.html')]:
        original = light_template((ROOT/source).read_text(encoding='utf-8'))
        original = original.replace('<head>', '<head><base href="/">')
        original = with_boot(original, key, head='<head><base href="/">')
        original = original.replace('</head>', css_links('ui-kit') + '<link rel="stylesheet" href="/staffer-live/approved/restored.css">' + css_links(*overlay_names(key)) + '</head>')
        original = original.replace('</body>', '<script src="/staffer-live/approved/restored-links.js" data-mlk></script>' + js_scripts('ui-kit', *overlay_names(key)) + '</body>')
        (OUT/f'original-{key}.html').write_text(original, encoding='utf-8', newline='\r\n')
        documents[f'original-{key}.html'] = original
    people = (Path(__file__).parent/'staffer_live/index.html').read_text(encoding='utf-8')
    people = people.replace('<head>','<head><base href="/staffer-live/">').replace('</head>', css_links('ui-kit') + '<link rel="stylesheet" href="approved/restored-people.css">' + css_links(*overlay_names('people')) + '</head>')
    people = people.replace('</body>', '<script src="/staffer-live/approved/restored-links.js" data-mlk></script>' + js_scripts('ui-kit', *overlay_names('people')) + '</body>')
    people = with_boot(people, 'people', head='<head><base href="/staffer-live/">')
    (OUT/'people.html').write_text(people, encoding='utf-8', newline='\r\n')
    write_json('colors.json', colour_maps(documents))
    people_days()
    for p in SRC.iterdir():
        if p.is_file(): shutil.copyfile(p, OUT/p.name)
    ensure_stubs()
    html = (SRC/'index.html').read_text(encoding='utf-8')
    target = ROOT/'Moneyball_Redesign.html'
    backup = ROOT/'outputs/rebuild-review-2026-09-21/rejected-Moneyball_Redesign.html'
    keep_rejected_alias(target, backup)
    target.write_text(html, encoding='utf-8', newline='\r\n')
    print(f'Built clean shell: {target.stat().st_size:,} bytes; {len(catalog)} profiles loaded individually.')


if __name__ == '__main__':
    rebind = alias_receipt_rebinder()
    main()
    rebind()
