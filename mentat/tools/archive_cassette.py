"""A replay cassette built READ-ONLY from archived FEC responses (D075), and an opener that serves it in place of the provider.

The downstream collector archives every physical response (original bytes plus a D067 metadata record naming the endpoint, the exact query, the status and the hash), so any
finished run can be replayed with no provider: key = the request URL after the origin (endpoint + sorted query), value = the responses archived for it, in attempt order. A repeated
request (a retry after a 504, a cursor page asked twice) is served in order; a request the cassette lacks is a MISS, recorded and refused, never invented.

    cassette = Cassette.from_archive([folder, ...])          # reads the folders, copies nothing
    opener = CassetteOpener(cassette)                          # AccountedTransport(..., opener=opener)
    write_cassette(cassette, destination)                      # index.json + bodies/<sha256>.bin, for a read-only compose mount
    Cassette.load(destination)

Standard library only; makes no network call and loads no key.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlencode

ORIGIN = 'https://api.open.fec.gov/v1/'
SCHEMA = 'mentat-archive-cassette-v1'


def request_key(endpoint, params):
    """The request exactly as the transport puts it on the wire (fec_accounted_client.public_request): endpoint + '?' + the sorted, url-encoded query."""
    return endpoint + '?' + urlencode(sorted(params.items()), doseq=True)


class CassetteMiss(RuntimeError):
    """A request the cassette does not hold. Never caught by the transport (it is not an OSError): the replay stops loudly."""


class Cassette:
    def __init__(self, entries, bodies):
        # entries: [{'key','attempt','status','headers','sha256','bytes'}] in attempt order; bodies: {sha256: bytes | Path}
        self.entries, self.bodies = list(entries), dict(bodies)
        self.queue = {}
        for entry in self.entries:
            self.queue.setdefault(entry['key'], []).append(entry)
        self.lock, self.served, self.misses, self.cursor = threading.Lock(), [], [], {}

    @classmethod
    def from_archive(cls, folders):
        entries, bodies = [], {}
        for folder in folders:
            folder = Path(folder)
            for meta in sorted(folder.glob('attempt-*.json')):
                record = json.loads(meta.read_bytes())
                body = meta.with_name(meta.name[:-5] + '.body.bin')
                if not body.is_file() or record.get('raw_body_persisted') is not True:
                    continue                                   # a sanitized secret-echo record has no body to serve
                raw = body.read_bytes()
                if hashlib.sha256(raw).hexdigest() != record['sha256'] or len(raw) != record['bytes']:
                    raise ValueError('Archived pair does not bind its own hash: ' + str(meta))
                entries.append({'key': request_key(record['endpoint'], record['query']), 'attempt': record['attempt'], 'status': record['status'],
                                'headers': record.get('headers') or {}, 'sha256': record['sha256'], 'bytes': record['bytes'], 'source': str(folder.name)})
                bodies[record['sha256']] = body
        entries.sort(key=lambda item: (item['source'], item['attempt']))
        return cls(entries, bodies)

    @classmethod
    def load(cls, folder):
        folder = Path(folder)
        document = json.loads((folder / 'index.json').read_bytes())
        if document.get('schema') != SCHEMA:
            raise ValueError('Not an archive cassette.')
        bodies = {entry['sha256']: folder / 'bodies' / (entry['sha256'] + '.bin') for entry in document['entries']}
        return cls(document['entries'], bodies)

    def body(self, sha256):
        value = self.bodies[sha256]
        return value if isinstance(value, bytes) else Path(value).read_bytes()

    def take(self, key):
        """The next unserved response for this request, or a recorded miss."""
        with self.lock:
            queue = self.queue.get(key, [])
            position = self.cursor.get(key, 0)
            if position >= len(queue):
                self.misses.append(key)
                raise CassetteMiss('The cassette holds no (further) response for ' + key[:160])
            self.cursor[key] = position + 1
            entry = queue[position]
            self.served.append(key)
            return entry

    def unused(self):
        with self.lock:
            return sorted(key for key, queue in self.queue.items() for _ in queue[self.cursor.get(key, 0):])

    def report(self):
        with self.lock:
            return {'entries': len(self.entries), 'served': len(self.served), 'misses': list(self.misses), 'unused': len(self.unused_unlocked())}

    def unused_unlocked(self):
        return [key for key, queue in self.queue.items() for _ in queue[self.cursor.get(key, 0):]]


def write_cassette(cassette, destination):
    """index.json + bodies/<sha256>.bin, each file new (a cassette is never overwritten)."""
    destination = Path(destination)
    (destination / 'bodies').mkdir(parents=True, exist_ok=False)
    for sha256 in sorted({entry['sha256'] for entry in cassette.entries}):
        with open(destination / 'bodies' / (sha256 + '.bin'), 'xb') as stream:
            stream.write(cassette.body(sha256))
    document = {'schema': SCHEMA, 'entries': [{key: value for key, value in entry.items() if key != 'source'} for entry in cassette.entries]}
    with open(destination / 'index.json', 'xb') as stream:
        stream.write((json.dumps(document, indent=1) + '\n').encode('utf-8'))
        stream.flush()
        os.fsync(stream.fileno())
    return document


class Response(io.BytesIO):
    def __init__(self, body, status, headers):
        super().__init__(body)
        self.status, self.headers = status, headers


class CassetteOpener:
    """The urllib opener AccountedTransport takes (opener=): answers every request from the cassette, whatever its status."""
    def __init__(self, cassette):
        self.cassette = cassette

    def open(self, request, timeout=None):
        url = request.full_url
        if not url.startswith(ORIGIN):
            raise CassetteMiss('Not an FEC origin URL: ' + url[:80])
        entry = self.cassette.take(url[len(ORIGIN):])
        return Response(self.cassette.body(entry['sha256']), entry['status'], dict(entry['headers']))
