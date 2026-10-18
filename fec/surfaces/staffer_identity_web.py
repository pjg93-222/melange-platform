"""Durable, off-request public-reference discovery for identity reconciliation."""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
from http import client as http_client
from pathlib import Path
from urllib.parse import urlsplit, urlencode
from urllib import request, error, robotparser
import hashlib
import io
import ipaddress
import json
import re
import socket
import sqlite3
import time
import xml.etree.ElementTree as ET

try:
    from .staffer_identity_auto import name_parts, normalized, compatible
except ImportError:  # direct script execution: no package context
    import os as _bootstrap_os, sys as _bootstrap_sys
    _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
    if _bootstrap_root not in _bootstrap_sys.path:
        _bootstrap_sys.path.insert(0, _bootstrap_root)
    from fec.surfaces.staffer_identity_auto import name_parts, normalized, compatible

AGENT = 'MelangeIdentity/1.0 (public professional identity references)'
SEEDS = Path(__file__).with_name('staffer_identity_reference_seeds.json')
SEED_PRIORITY = 300  # trusted-source families outrank the profile tier (200) so they refresh within a cycle
LOOKUP_BUDGET_SECONDS = 120  # per-family wall-clock budget; a request already in flight is never interrupted
STALE_CLAIM_SECONDS = 3600  # a 'checking' claim older than this was orphaned by an escaped error
REVISIT_SECONDS = 30 * 86400
TERMINAL_HTTP = {400, 401, 403, 404, 405, 406, 410, 451}


def stable_evidence(value):
    """Discard changing receipt bindings only when content has its own hash."""
    if isinstance(value, list):
        return [stable_evidence(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: stable_evidence(item) for key, item in value.items()}
    provenance = result.get('provenance')
    if isinstance(provenance, dict) and provenance.get('sha256'):
        volatile = {'receipt_sha256', 'accepted_prefix_sha256',
                    'receipt_verified_at', 'receipt_path', 'receipt_binding',
                    'binding_sha256', 'accepted_prefix_count'}
        if provenance.get('sha256_scope') == 'CANONICAL_PROFILE_CONTENT':
            volatile.update({'verified_at', 'verified_prefix_bytes', 'verified_prefix_sha256',
                             'raw_line_sha256', 'profile_raw_line_sha256', 'profile_row_sha256',
                             'byte_start', 'byte_end', 'observed_at', 'prefix_binding'})
        result['provenance'] = {key: item for key, item in provenance.items() if key not in volatile}
    return result


def now():
    return datetime.now(timezone.utc).isoformat()


def _public_addresses(host):
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('Reference URL resolves outside the public internet')
    return addresses


def public_url(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError('Reference must be a public HTTPS URL')
    _public_addresses(parts.hostname)
    return url


def _direct_public_url(url):
    public_url(url)
    proxies = request.getproxies()
    if (proxies.get('https') or proxies.get('all')) and not request.proxy_bypass(urlsplit(url).hostname):
        raise ValueError('Reference downloads require direct HTTPS; configured trusted-proxy support is unavailable')
    return url


class PublicHTTPSConnection(http_client.HTTPSConnection):
    def connect(self):
        if self._tunnel_host or self.port != 443:
            raise ValueError('Reference connections require direct HTTPS on port 443')
        # The OS resolver itself cannot be interrupted here. Its elapsed time counts
        # against one connection deadline shared by address fallback and TLS.
        deadline = time.monotonic() + float(self.timeout)
        addresses = _public_addresses(self.host)
        failure = None
        for family, kind, protocol, _, sockaddr in addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Reference connection deadline exceeded')
            transport = socket.socket(family, kind, protocol)
            try:
                transport.settimeout(remaining)
                transport.connect(sockaddr)  # Captured numeric sockaddr; no second DNS lookup.
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Reference connection deadline exceeded')
                transport.settimeout(remaining)
                # Original host remains the TLS certificate/SNI identity.
                self.sock = self._context.wrap_socket(transport, server_hostname=self.host)
                return
            except OSError as exc:
                transport.close()
                failure = exc
        if failure is not None:
            raise failure
        raise OSError('Reference has no usable public address')


class PublicHTTPSHandler(request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(PublicHTTPSConnection, req, context=self._context)


class PublicRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _direct_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, maximum=4_000_000):
    _direct_public_url(url)
    opener = request.build_opener(request.ProxyHandler({}), PublicRedirect(), PublicHTTPSHandler())
    with opener.open(request.Request(url, headers={'User-Agent': AGENT}), timeout=12) as response:
        data = response.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError('Reference exceeds the download bound')
        return data, response.headers.get('Content-Type', ''), response.url


def terminal_failure(exc):
    """Deterministic per-URL outcomes; transient network conditions keep the retry backoff."""
    if isinstance(exc, error.HTTPError):
        return exc.code in TERMINAL_HTTP
    if isinstance(exc, ValueError):
        return True  # public-URL, site access, redirect and size rules are properties of the URL
    return type(exc).__module__.split('.')[0] == 'pypdf'  # a complete download that is not a readable PDF


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.buffer, self.skip, self.heading = [], [], 0, False

    def flush(self):
        text = ' '.join(' '.join(self.buffer).split())
        if text:
            self.blocks.append((self.heading, text))
        self.buffer = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'nav', 'footer', 'header', 'svg'):
            self.skip += 1
        if not self.skip and tag in ('p', 'div', 'li', 'article', 'section', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.flush()
            self.heading = tag.startswith('h') and len(tag) == 2

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'nav', 'footer', 'header', 'svg'):
            self.skip = max(0, self.skip - 1)
        if not self.skip and tag in ('p', 'div', 'li', 'article', 'section', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.flush()
            self.heading = False

    def handle_data(self, data):
        if not self.skip:
            self.buffer.append(data)


def actual_subject(text, name):
    parts = name_parts({'label': name})
    if not parts:
        return None
    try:
        from .staffer_identity_auto import NICKNAMES
    except ImportError:  # direct script execution: no package context
        import os as _bootstrap_os, sys as _bootstrap_sys
        _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
            _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
        if _bootstrap_root not in _bootstrap_sys.path:
            _bootstrap_sys.path.insert(0, _bootstrap_root)
        from fec.surfaces.staffer_identity_auto import NICKNAMES
    firsts = {parts[0]} | {k for k, v in NICKNAMES.items() if v == parts[0]}
    pattern = (r'\b(?:' + '|'.join(re.escape(f) for f in firsts) + r')'
               + r'(?: [a-z]+){0,2}? ' + re.escape(parts[1])
               + r'(?: (?:jr|sr|ii|iii|iv))?\b')
    for match in re.finditer(pattern, normalized(text)):
        candidate = match.group(0)
        if compatible({'label': name}, {'label': candidate}):
            return candidate.title()
    return None


def subject_text(blocks, name):
    """Select one person's biography, stopping before another heading/biography."""
    parts = name_parts({'label': name})
    if not parts:
        return ''
    first, last = parts[:2]
    firsts = {first}
    try:
        from .staffer_identity_auto import NICKNAMES
    except ImportError:  # direct script execution: no package context
        import os as _bootstrap_os, sys as _bootstrap_sys
        _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
            _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
        if _bootstrap_root not in _bootstrap_sys.path:
            _bootstrap_sys.path.insert(0, _bootstrap_root)
        from fec.surfaces.staffer_identity_auto import NICKNAMES
    firsts.update(k for k, v in NICKNAMES.items() if v == first)
    def mentions(text):
        return actual_subject(text, name) is not None

    def subject_heading(text):
        candidate = actual_subject(text, name)
        return bool(candidate and normalized(candidate) == normalized(text))

    def subject_lead(text):
        candidate = actual_subject(text, name)
        norm = normalized(text)
        if not candidate or not norm.startswith(normalized(candidate) + ' '):
            return False
        tail = norm[len(normalized(candidate)):].strip()
        return bool(re.match(r'^(?:is|was|serves|served|joined|leads|led|has|works|worked|'
                             r'holds|began|currently|previously|recently|brings)\b', tail))

    def foreign_biography(text):
        # Detect another named person's role statement, even in the same block.
        for match in re.finditer(
                r"\b([A-Z][a-z]+(?: [A-Z]\.?)? [A-Z][a-z]+) "
                r"(?:is|was|serves|served|joined|leads|led|has|works|worked|previously)\b", text):
            if not mentions(match.group(1)):
                return True
        return False

    # PDF extraction can put the title and entire biography into one block.
    # A standalone subject-name line supplies the same explicit boundary as HTML.
    expanded = []
    for heading, text in blocks:
        lines = text.splitlines()
        subject_lines = [i for i, line in enumerate(lines) if subject_heading(line)]
        if len(lines) > 1 and subject_lines:
            cut = subject_lines[0]
            expanded.append((True, lines[cut].strip()))
            remainder = '\n'.join(lines[cut + 1:]).strip()
            if remainder:
                expanded.append((False, remainder))
        else:
            expanded.append((heading, text))
    blocks = expanded
    headings = [i for i, (heading, text) in enumerate(blocks)
                if heading and subject_heading(text)]
    hits = [i for i, (_, text) in enumerate(blocks)
            if mentions(text) and subject_lead(text) and not foreign_biography(text)]
    if not headings and not hits:
        return ''
    start = headings[0] if headings else hits[0]
    selected = [blocks[start][1]]
    for heading, text in blocks[start+1:start+18]:
        if heading and not subject_heading(text):
            break
        if foreign_biography(text):
            break
        words = normalized(text).split()
        # Continuation must refer back to this subject. Unknown first-name
        # paragraphs cannot inherit the preceding subject's identity.
        if not heading and not mentions(text) and words:
            continuation = words[0] in firsts | {last, 'he', 'she', 'his', 'her',
                'before', 'prior', 'previously', 'earlier', 'during', 'in', 'after', 'as'}
            if not continuation:
                break
        if len(' '.join(selected)) > 6500:
            break
        selected.append(text)
    return '\n'.join(selected)[:9000]



class IdentityWebIndex:
    def __init__(self, source_root):
        self.path = Path(source_root) / 'audits/staffer_live_runtime/identity_reference_v1.sqlite'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.loads(SEEDS.read_text(encoding='utf-8')) if SEEDS.exists() else []
        self.seeds = payload if isinstance(payload, list) else payload.get('references', payload.get('seeds', []))
        self.seeds = [s for s in self.seeds if s.get('eligible_for_reference_fetch', True)]
        self.trusted = {urlsplit(s['url']).hostname for s in self.seeds}
        self.robots = {}
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS queue (
                key TEXT PRIMARY KEY, name TEXT NOT NULL, node_ids TEXT NOT NULL, priority INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS documents (
                key TEXT PRIMARY KEY, name TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);''')
            db.execute("UPDATE queue SET state='queued' WHERE state='checking'")
            columns = {row[1] for row in db.execute('PRAGMA table_info(queue)')}
            if 'next_check' not in columns:
                db.execute('ALTER TABLE queue ADD COLUMN next_check REAL NOT NULL DEFAULT 0')
            if 'signature' not in columns:
                db.execute('ALTER TABLE queue ADD COLUMN signature TEXT')
            if 'names_json' not in columns:
                db.execute("ALTER TABLE queue ADD COLUMN names_json TEXT NOT NULL DEFAULT '[]'")
                db.execute("DELETE FROM metadata WHERE key='inventory_revision'")
            if 'variant_cursor' not in columns:
                db.execute('ALTER TABLE queue ADD COLUMN variant_cursor INTEGER NOT NULL DEFAULT 0')
            if 'priority_refresh_pending' not in columns:
                db.execute('ALTER TABLE queue ADD COLUMN priority_refresh_pending INTEGER NOT NULL DEFAULT 0')
            if 'accepted_references' not in columns:
                db.execute('ALTER TABLE queue ADD COLUMN accepted_references INTEGER NOT NULL DEFAULT 0')
            if 'claimed_at' not in columns:
                db.execute('ALTER TABLE queue ADD COLUMN claimed_at REAL NOT NULL DEFAULT 0')
            db.execute('CREATE TABLE IF NOT EXISTS priority_receipts (node_id TEXT PRIMARY KEY, digest TEXT NOT NULL, family_key TEXT NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS reference_outcomes (key TEXT NOT NULL, name TEXT NOT NULL, url TEXT NOT NULL,
                outcome TEXT NOT NULL, detail TEXT, recorded_at REAL NOT NULL, PRIMARY KEY(key,name,url))''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.execute('PRAGMA journal_mode=WAL')
        return db

    def enqueue(self, groups):
        rows = []
        for group in groups:
            name = group.get('display_name') or group.get('name') or ''
            parts = name_parts({'label': name})
            if not parts:
                continue
            key = ' '.join(parts[:2])
            seed = any(name_parts({'label': s.get('name', '')}) and name_parts({'label': s['name']})[:2] == parts[:2] for s in self.seeds)
            priority = SEED_PRIORITY if seed else 10 if group.get('priority') == 'corroborated_candidate' else 1
            rows.append((key, name, json.dumps(group.get('node_ids', [])), priority))
        with self.connect() as db:
            db.executemany('INSERT INTO queue(key,name,node_ids,priority) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET priority=MAX(priority,excluded.priority)', rows)

    def enqueue_people(self, nodes, source_revision=None):
        """All people enter the cycle; changed source records invalidate their lookup."""
        if source_revision:
            with self.connect() as db:
                prior = db.execute("SELECT value FROM metadata WHERE key='inventory_revision'").fetchone()
            if prior and prior[0] == source_revision:
                return
        families = {}
        count = 0
        for ident, node in nodes.items():
            if node.get('kind') != 'person':
                continue
            count += 1
            parts = name_parts(node)
            if not parts:
                continue
            key = ' '.join(parts[:2])
            family = families.setdefault(key, {'name': node['label'], 'records': []})
            family['records'].append([ident, node['label'], node.get('last_reported_at'),
                                      node.get('source_summary'), node.get('source_profile_uuid'),
                                      node.get('provenance', {}).get('sha256'),
                                      hashlib.sha256(json.dumps([node.get('profile_content_sha256'),
                                          stable_evidence(node.get('career_anchors')), stable_evidence(node.get('covered_positions')),
                                          stable_evidence(node.get('employment_history')), stable_evidence(node.get('stints'))], sort_keys=True).encode()).hexdigest()])
        rows = []
        for key, family in families.items():
            records = sorted(family['records'])
            signature = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
            rows.append((key, family['name'], json.dumps([r[0] for r in records]),
                         10 if len(records) > 1 else 0, signature, json.dumps(sorted({r[1] for r in records})) ))
        # A NULL fingerprint means prioritize_people or a seed scheduled the family before
        # the inventory fingerprinted it: adopt the fingerprint without discarding the lookup
        # that already ran, unless name variants appeared that no lookup has covered.
        changed = ("(queue.names_json='[]' OR (queue.signature IS NULL AND queue.names_json<>excluded.names_json)"
                   " OR (queue.signature IS NOT NULL AND queue.signature<>excluded.signature))")
        with self.connect() as db:
            db.executemany('''INSERT INTO queue(key,name,node_ids,priority,signature,names_json) VALUES(?,?,?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET node_ids=excluded.node_ids,
                state=CASE WHEN queue.state='checking' THEN queue.state WHEN {changed} THEN 'queued' ELSE queue.state END,
                next_check=CASE WHEN queue.state='checking' THEN queue.next_check WHEN {changed} THEN 0 ELSE queue.next_check END,
                priority_refresh_pending=CASE WHEN queue.state='checking' AND {changed} THEN 1 ELSE queue.priority_refresh_pending END,
                variant_cursor=CASE WHEN queue.state='checking' THEN queue.variant_cursor WHEN {changed} THEN 0 ELSE queue.variant_cursor END,
                names_json=excluded.names_json,signature=excluded.signature,priority=MAX(queue.priority,excluded.priority)'''.format(changed=changed), rows)
            db.execute("INSERT OR REPLACE INTO metadata VALUES('total_people',?)", (str(count),))
            if source_revision:
                db.execute("INSERT OR REPLACE INTO metadata VALUES('inventory_revision',?)", (source_revision,))

    def prioritize_people(self, nodes):
        """Prioritize changed, caller-validated profiles without replacing a family.

        This schedules reference discovery only; it neither fetches nor verifies
        receipts. The inventory signature remains owned by enqueue_people.
        """
        values = nodes.items() if hasattr(nodes, 'items') else ((n.get('id'), n) for n in nodes)
        changed, unchanged, families = 0, 0, set()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for ident, node in values:
                ident = node.get('id') or ident
                parts = name_parts(node) if node.get('kind') == 'person' else None
                if not ident or not parts:
                    continue
                key = ' '.join(parts[:2])
                provenance = node.get('provenance') or {}
                profile_digest = node.get('profile_content_sha256') or provenance.get('sha256')
                content = {'profile_content_sha256': profile_digest, 'label': node.get('label')}
                if not profile_digest:
                    content.update({field: node.get(field) for field in (
                        'history', 'employment_history', 'covered_positions',
                        'career_anchors', 'stints', 'source_profile_uuid', 'profile_digest', 'content_digest')})
                digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                prior = db.execute('SELECT digest,family_key FROM priority_receipts WHERE node_id=?', (ident,)).fetchone()
                if prior == (digest, key):
                    unchanged += 1
                    continue
                row = db.execute('SELECT name,node_ids,names_json,state FROM queue WHERE key=?', (key,)).fetchone()
                if row:
                    ids = sorted(set(json.loads(row[1])) | {ident})
                    names = sorted(set(json.loads(row[2])) | {row[0], node['label']})
                    checking = row[3] == 'checking'
                    # The stored fingerprint predates this membership change; the check scheduled
                    # here covers it, so the next inventory pass adopts its fingerprint instead.
                    db.execute("""UPDATE queue SET node_ids=?,names_json=?,priority=MAX(priority,200),signature=NULL,
                        state=CASE WHEN state='checking' THEN state ELSE 'queued' END,
                        next_check=CASE WHEN state='checking' THEN next_check ELSE 0 END,
                        variant_cursor=CASE WHEN state='checking' THEN variant_cursor ELSE 0 END,
                        priority_refresh_pending=? WHERE key=?""",
                        (json.dumps(ids), json.dumps(names), int(checking), key))
                else:
                    db.execute('INSERT INTO queue(key,name,node_ids,names_json,priority) VALUES(?,?,?,?,200)',
                        (key, node['label'], json.dumps([ident]), json.dumps([node['label']])))
                db.execute('INSERT OR REPLACE INTO priority_receipts VALUES(?,?,?)', (ident, digest, key))
                changed += 1
                families.add(key)
        return {'changed_people': changed, 'unchanged_people': unchanged, 'prioritized_families': len(families)}

    def revision(self):
        with self.connect() as db:
            value = db.execute("SELECT value FROM metadata WHERE key='reference_revision'").fetchone()
        return value[0] if value else '0'

    def references(self):
        with self.connect() as db:
            references = [json.loads(row[0]) for row in db.execute('SELECT payload FROM documents')]
        # Legacy cache rows may have stored the lookup label instead of the
        # actual subject. Contradictory cached biographies are not merge evidence.
        return [r for r in references if actual_subject(r.get('text', ''), r.get('name', ''))]

    @staticmethod
    def _scanned_people(db):
        """People covered by the families already checked, counted by the database, not by parsing every row in Python.

        node_ids is always a JSON array, so the sum of the array lengths is the sum of len(json.loads(row)).
        A SQLite build without the JSON functions falls back to the loop, which gives the same total.
        """
        where = "WHERE updated_at IS NOT NULL AND state IN ('referenced','unresolved')"
        try:
            return db.execute("SELECT coalesce(sum(json_array_length(node_ids)), 0) FROM queue " + where).fetchone()[0]
        except sqlite3.OperationalError as exc:
            if 'json_array_length' not in str(exc):
                raise
            return sum(len(json.loads(row[0])) for row in db.execute("SELECT node_ids FROM queue " + where))

    def status(self):
        with self.connect() as db:
            states = dict(db.execute('SELECT state,count(*) FROM queue GROUP BY state'))
            # One biography stored under several name variants is one verified reference.
            total = len({json.loads(row[0]).get('sha256') for row in db.execute('SELECT payload FROM documents')})
            last = db.execute("SELECT max(updated_at) FROM queue WHERE state IN ('referenced','unresolved')").fetchone()[0]
            errors = [row[0] for row in db.execute("SELECT error FROM queue WHERE state='retry' AND error IS NOT NULL GROUP BY error ORDER BY max(updated_at) DESC LIMIT 3")]
            total_people = db.execute("SELECT value FROM metadata WHERE key='total_people'").fetchone()
            scanned_people = self._scanned_people(db)
            outcomes = dict(db.execute('SELECT outcome,count(*) FROM reference_outcomes GROUP BY outcome'))
        queued = states.get('queued', 0) + states.get('checking', 0) + states.get('retry', 0)
        return {'status': 'checking_references' if queued else 'cycle_complete', 'queued': queued,
                'total_people': int(total_people[0]) if total_people else None, 'scanned_people': scanned_people,
                'retrying': states.get('retry', 0), 'attention': states.get('retry', 0),
                'referenced': states.get('referenced', 0), 'unresolved': states.get('unresolved', 0),
                'checked': states.get('referenced', 0) + states.get('unresolved', 0),
                'references_verified': total, 'reference_outcomes': outcomes, 'last_completed_at': last, 'errors': errors}

    def allowed(self, url):
        parts = urlsplit(url)
        root = 'https://' + parts.netloc
        if root not in self.robots:
            parser = robotparser.RobotFileParser()
            try:
                data, _, _ = download(root + '/robots.txt', 300_000)
                parser.parse(data.decode('utf-8', errors='replace').splitlines())
            except error.HTTPError as exc:
                if exc.code == 404:
                    parser.parse([])
                elif exc.code in (408, 429) or exc.code >= 500:
                    raise OSError('robots.txt unavailable for ' + parts.netloc + ': ' + str(exc)[:120]) from exc
                else:
                    return False
            except ValueError:
                return False
            except OSError as exc:
                # An unreachable robots.txt is transient; only a fetched rule set is a site access decision.
                raise OSError('robots.txt unavailable for ' + parts.netloc + ': ' + str(exc)[:120]) from exc
            self.robots[root] = parser
        return self.robots[root].can_fetch(AGENT, url)

    def discover(self, name):
        # Search returns links only. Search snippets never become identity evidence.
        url = 'https://www.bing.com/search?' + urlencode({'format': 'rss', 'q': '"' + name + '" biography lobbying Congress'})
        data, _, _ = download(url, 500_000)
        root = ET.fromstring(data)
        return [item.findtext('link') for item in root.findall('./channel/item') if item.findtext('link')][:6]

    def fetch_reference(self, url, name, source_kind=None, title=None):
        host = urlsplit(url).hostname or ''
        if host in {'www.bing.com', 'www.google.com', 'www.linkedin.com', 'www.facebook.com', 'x.com'}:
            return None
        if not self.allowed(url):
            raise ValueError('Reference unavailable under site access rules: ' + host)
        raw, content_type, final_url = download(url)
        final_host = urlsplit(final_url).hostname or ''
        if (host in self.trusted or host.endswith('.gov')) and not (final_host in self.trusted or final_host.endswith('.gov')):
            raise ValueError('Reference redirected outside its trusted source')
        if 'pdf' in content_type or final_url.lower().endswith('.pdf'):
            from pypdf import PdfReader
            body = '\n'.join(page.extract_text() for page in PdfReader(io.BytesIO(raw)).pages[:30])
            blocks = [(False, text) for text in body.split('\n\n') if text.strip()]
        else:
            parser = PageText()
            parser.feed(raw.decode('utf-8', errors='replace'))
            parser.flush()
            blocks = parser.blocks
        text = subject_text(blocks, name)
        subject = actual_subject(text, name)
        if len(text) < 80 or not subject:
            return None
        reference = {'url': final_url, 'name': subject, 'query_name': name, 'title': title or name + ' — source biography',
                     'text': text, 'sha256': hashlib.sha256(text.encode()).hexdigest(),
                     'response_sha256': hashlib.sha256(raw).hexdigest(), 'retrieved_at': now(),
                     'subject_scoped': True,
                     'source_kind': ('official_filing' if final_host.endswith('.gov') else
                                     'official_biography' if final_host in self.trusted else 'public_biography_candidate')}
        key = hashlib.sha256((name + '\n' + url).encode()).hexdigest()
        with self.connect() as db:
            prior = db.execute('SELECT payload FROM documents WHERE key=?', (key,)).fetchone()
            db.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?)', (key, name, json.dumps(reference)))
            if not prior or json.loads(prior[0]).get('sha256') != reference['sha256']:
                db.execute("INSERT OR REPLACE INTO metadata VALUES('reference_revision',?)", (str(time.time_ns()),))
        return reference

    def process_one(self):
        started = time.monotonic()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # A claim left in 'checking' past the stale window was orphaned; reclaim it.
            row = db.execute("SELECT key,name,attempts,names_json,variant_cursor,accepted_references FROM queue WHERE (state<>'checking' OR claimed_at<=?) AND next_check<=? ORDER BY priority DESC,key LIMIT 1",
                             (time.time() - STALE_CLAIM_SECONDS, time.time())).fetchone()
            if not row:
                return False
            key, fallback_name, attempts, names_json, cursor, carried = row
            names = json.loads(names_json) or [fallback_name]
            cursor = cursor if cursor < len(names) else 0
            name = names[cursor]
            db.execute("UPDATE queue SET state='checking',attempts=attempts+1,claimed_at=? WHERE key=?", (time.time(), key))
            known = {url: (outcome, recorded) for url, outcome, recorded in db.execute(
                'SELECT url,outcome,recorded_at FROM reference_outcomes WHERE key=? AND name=?', (key, name))}
        matched = [s for s in self.seeds if compatible({'label': name}, {'label': s.get('name', '')})]
        urls = [(s['url'], s.get('source_kind'), s.get('title')) for s in matched]
        failures, accepted, outcomes, fetched, deferred = [], 0, [], 0, 0
        try:
            if not urls:
                urls = [(url, None, None) for url in self.discover(name)]
            for url, kind, title in urls[:6]:
                prior = known.get(url)
                if prior and prior[1] > time.time() - REVISIT_SECONDS:
                    accepted += prior[0] == 'accepted'  # recorded outcome for this URL/variant
                    continue
                if fetched and time.monotonic() - started >= LOOKUP_BUDGET_SECONDS:
                    deferred += 1  # yield the worker; the request already made is never interrupted
                    continue
                fetched += 1
                try:
                    reference = self.fetch_reference(url, name, kind, title)
                    outcomes.append((url, 'accepted' if reference else 'no_subject', None))
                    accepted += bool(reference)
                except Exception as exc:  # noqa: BLE001 - every outcome is recorded; the worker moves on
                    if terminal_failure(exc):
                        outcomes.append((url, 'unavailable', str(exc)[:250]))
                    else:
                        failures.append(str(exc)[:250])
        except Exception as exc:  # noqa: BLE001 - discovery failures are transient for the whole family
            failures.append(str(exc)[:250])
        if deferred:
            failures.append('Lookup deferred after %ds budget; %d of %d references still unchecked'
                            % (LOOKUP_BUDGET_SECONDS, deferred, len(urls[:6])))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            pending, family_ids = db.execute('SELECT priority_refresh_pending,node_ids FROM queue WHERE key=?', (key,)).fetchone()
            # Retry the same variant after any transient failure. A family only
            # counts as scanned after every spelling/initial variant completes,
            # and a reference accepted under any variant makes it referenced.
            accepted_total = accepted + (carried if cursor else 0)
            if failures:
                state, next_cursor = 'retry', cursor
                delay = min(86400, 300 * 2 ** min(attempts, 8))
            elif cursor + 1 < len(names):
                state, next_cursor, delay = 'queued', cursor + 1, 0
            else:
                state, next_cursor, delay = ('referenced' if accepted_total else 'unresolved'), 0, REVISIT_SECONDS
            if pending:
                state, next_cursor = 'queued', 0
            due = 0 if pending else time.time() + delay
            if not pending and state in ('referenced', 'unresolved'):
                base_priority = SEED_PRIORITY if matched else 10 if len(json.loads(family_ids)) > 1 else 0
                db.execute('UPDATE queue SET priority=? WHERE key=? AND priority>=200', (base_priority, key))
            db.executemany('INSERT OR REPLACE INTO reference_outcomes VALUES(?,?,?,?,?,?)',
                           [(key, name, url, outcome, detail, time.time()) for url, outcome, detail in outcomes])
            db.execute('UPDATE queue SET state=?,updated_at=?,error=?,next_check=?,variant_cursor=?,accepted_references=?,priority_refresh_pending=0 WHERE key=?',
                       (state, now(), '; '.join(failures) or None, due, next_cursor, accepted_total, key))
        return True
