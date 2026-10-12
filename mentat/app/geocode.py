"""Street addresses to congressional districts: the precise layer (S8b).

S8 places a contributor by their ZIP, through a ZCTA-to-district crosswalk
that splits a straddling ZIP by land area. That is approximate twice over. This
module makes the exact answer available for the rows that have a street
address, and only when the reader asks for it.

    normalise(street, city, state, zip)   the cache key and the cleaned parts
    Cache(data_root)                      sqlite, shared, survives restarts
    Client()                              the US Census BATCH geocoder
    Job / start(...)                      one resumable job per import

RULING 10 (S6_DESIGN section H, Paul 2026-09-17): geocoding runs only on
Paul's explicit Start, per import. Nothing in this module runs on a GET, on a
page load, or on a console start. A read-only console never starts a job at
all: it shares its data root with the console that owns these imports.

WHAT LEAVES THE MACHINE, EXACTLY. Street, city, state and a five-digit ZIP,
and nothing else. Never a name, never an amount, never an employer or an
occupation. The rows are uploaded as `id,street,city,state,zip` where `id` is
this module's own opaque cache key, so the service is not told which import,
committee or person an address belongs to.

THE SERVICE, AND WHAT IT ACTUALLY ANSWERS (measured 2026-09-18, not guessed)

  GET  https://geocoding.geo.census.gov/geocoder/benchmarks
       -> id 4 "Public_AR_Current" (default), 8 "Public_AR_ACS2025",
          11 "Public_AR_LUCA", 2020 "Public_AR_Census2020".
  GET  .../vintages?benchmark=4
       -> id 4 "Current_Current" (default), 420 "Census2020_Current",
          423 "ACS2023_Current", 424 "ACS2024_Current", ... 425 ACS2025.

  The ONE-ADDRESS route `geographies/address` returns the district directly:
  benchmark 4 + vintage 4 answers a "119th Congressional Districts" layer
  (field CD119) and benchmark 4 + vintage 423 answers a "118th Congressional
  Districts" layer (field CD118). Those are the identifiers the design asked
  for, and they are recorded here because they are the ones a reader would
  reach for.

  THE BATCH ROUTE DOES NOT. `POST geographies/addressbatch` with
  `returntype=geographies` answers a fixed CSV whose geography columns are
  STATE, COUNTY, TRACT, BLOCK - the 2020 census block - and nothing else. It
  is identical under vintage 4 and vintage 420, and `layers=54`,
  `layers=Congressional Districts` and `layers=all` are all ignored (three
  byte-identical responses). So the batch answer is bridged locally: the 2020
  census TRACT it returns determines the district through the Census's own
  CD-to-tract tabulation, shipped as geo/tract_cd119.json and
  geo/tract_cd118.json (tools/build_tract_geo.py). A tract split across two
  districts is not in those files, so an address inside one keeps its
  approximate ZIP basis instead of being claimed as an exact match.

  The rows come back OUT OF ORDER and the id is the only link, which is why
  the id is the cache key and never a row number.

FAILURE IS NEVER SILENT. A request that fails records its reason on the job
and the job stops; the cache keeps every address already answered, so Start
again resumes rather than re-sends. A row whose address is absent or unusable
is never sent, never dropped, and stays ZIP-based.
"""
from __future__ import annotations

from contextlib import closing
import csv
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import district_geo

HOST = 'https://geocoding.geo.census.gov/geocoder'
#: MENTAT_GEOCODE_URL points the batch client at a local fixture server for a
#: browser test or an offline pass. The service NAME below is derived from
#: whatever URL is actually in force, so the status line can never say "US
#: Census Geocoder" about a host that is not one.
BATCH_URL = os.environ.get('MENTAT_GEOCODE_URL') or (HOST + '/geographies/addressbatch')
BENCHMARKS_URL = HOST + '/benchmarks'
VINTAGES_URL = HOST + '/vintages'
#: Public Address Ranges - Current Benchmark (id 4), discovered from the
#: service; see the module docstring. Overridable for a test or a future move.
BENCHMARK = os.environ.get('MENTAT_GEOCODE_BENCHMARK') or '4'
#: Census2020_Current (id 420): the vintage that names the 2020 tabulation the
#: shipped tract crosswalk is built from. Vintage 4 answers identically today;
#: 420 says out loud which tracts are meant.
VINTAGE = os.environ.get('MENTAT_GEOCODE_VINTAGE') or '420'
#: The single-address vintages that DO carry a district layer, recorded so the
#: identifiers are in the code rather than only in a receipt.
DISTRICT_VINTAGES = {'119': '4', '118': '423'}
#: The service's own documented ceiling for one upload.
BATCH_LIMIT = 10000
#: How many rows this module actually sends at once. The service is slower per
#: row on a full 10,000 upload than on a few smaller ones and a failure costs
#: less, so the chunk is smaller than the ceiling on purpose.
CHUNK = 2000
TIMEOUT = 600
USER_AGENT = 'Mentat/1.0 (PAC evidence district map)'


def service_name(url=None):
    """What to call the host this console would actually send addresses to."""
    host = urllib.parse.urlsplit(url or BATCH_URL).netloc or 'geocoding.geo.census.gov'
    return (f'US Census Geocoder ({host})' if host.endswith('census.gov')
            else f'geocoder at {host}')


SERVICE = service_name()

MATCH_CLASSES = ('exact', 'non_exact', 'tie', 'no_match')
STATE = re.compile(r'^[A-Z]{2}$')
IDENTIFIER = re.compile(r'[a-z0-9][a-z0-9_-]{0,99}')

M_READ_ONLY = ('This preview is read-only. Start geocoding from the console that owns '
               'these imports.')
M_NO_ROWS = 'No row in this import carries a street address, so there is nothing to geocode.'
M_RUNNING = 'Geocoding is already running for this import.'
M_PRIVACY = (f'Start sends the contributor street address, city, state and ZIP of this import '
             f'to the {SERVICE}. No name, amount, employer or occupation is sent.')

#: The columns an import may carry an address in, in the order S2 wrote them.
STREET_FIELDS = ('street_1', 'street', 'contributor_street_1', 'address_1', 'address',
                 'contributor_address', 'street_address')
STREET2_FIELDS = ('street_2', 'contributor_street_2', 'address_2')
CITY_FIELDS = ('city', 'contributor_city')


# ----------------------------------------------------------------- normalise

_PUNCTUATION = re.compile(r'[^A-Z0-9 ]+')
_SPACES = re.compile(r'\s+')


def clean(value):
    """Upper-cased, punctuation stripped, whitespace collapsed."""
    text = _PUNCTUATION.sub(' ', str(value or '').upper())
    return _SPACES.sub(' ', text).strip()


def zip5(value):
    """The first five digits of anything written as a ZIP, or ''.

    ZIP+4 truncates: '94111-2708', '941112708' and '94111' are one address.
    """
    digits = re.sub(r'\D', '', str(value or ''))
    return digits[:5] if len(digits) >= 5 else ''


def normalise(street, city, state, zip_value):
    """(key, street, city, state, zip5) for one address, or None when unusable.

    The key is content-addressed: two imports that share a donor share the
    lookup, and the same address written in two ways is one cache row. An
    address with no street, or with neither a state nor a ZIP, is not an
    address this service can answer and is refused here rather than sent.
    """
    street_text = clean(street)
    city_text = clean(city)
    state_text = clean(state)
    postal = zip5(zip_value)
    if not STATE.match(state_text):
        state_text = ''
    if not street_text or not any(character.isdigit() for character in street_text):
        return None
    if not postal and not (state_text and city_text):
        return None
    key = '|'.join((street_text, city_text, state_text, postal))
    return key, street_text, city_text, state_text, postal


def address_of(values):
    """The address columns of one row, whatever the file or the filing calls them.

    Values are coerced to text first: an imported cell is already a string but
    a snapshot receipt carries None and real numbers, and `first` would raise
    on them. A column this row does not have is simply absent.
    """
    import evidence_csv
    text = {name: ('' if value is None else str(value))
            for name, value in dict(values or {}).items()}
    street = evidence_csv.first(text, STREET_FIELDS) or evidence_csv.first(text, STREET2_FIELDS)
    return (street, evidence_csv.first(text, CITY_FIELDS),
            evidence_csv.first(text, ('contributor_state', 'state', 'contributor_st', 'st')),
            evidence_csv.first(text, ('contributor_zip', 'zip', 'zip_code',
                                      'contributor_zip_code', 'zip5', 'postal_code')))


# --------------------------------------------------------------------- cache

SCHEMA = """
CREATE TABLE IF NOT EXISTS addresses (
    key         TEXT PRIMARY KEY,
    street      TEXT NOT NULL DEFAULT '',
    city        TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL DEFAULT '',
    zip5        TEXT NOT NULL DEFAULT '',
    latitude    REAL,
    longitude   REAL,
    tract       TEXT NOT NULL DEFAULT '',
    district_119 TEXT NOT NULL DEFAULT '',
    district_118 TEXT NOT NULL DEFAULT '',
    matched     TEXT NOT NULL DEFAULT 'no_match',
    source      TEXT NOT NULL DEFAULT '',
    fetched_at  REAL NOT NULL DEFAULT 0
)
"""
COLUMNS = ('key', 'street', 'city', 'state', 'zip5', 'latitude', 'longitude', 'tract',
           'district_119', 'district_118', 'matched', 'source', 'fetched_at')


class Cache:
    """Every address this data root has ever asked about, once.

    Shared by every console on the same data root and by both this process's
    threads and another process's, so the connection is opened per call with
    WAL and a busy timeout rather than held. A row already here is NEVER
    re-sent: that is the whole point of the file.
    """

    def __init__(self, data_root, create=True):
        """`create=False` opens an EXISTING cache and never makes one.

        A map request must not write into the data root: the console serving it
        may be a read-only preview sharing the owning console's root, and a GET
        that quietly creates a database is a side effect nobody asked for. The
        map opens the cache with create=False and simply has no precise layer
        when there is none; only a Start creates the file.
        """
        self.directory = Path(data_root) / 'geocode'
        self.path = self.directory / 'addresses.sqlite'
        if not create:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.execute(SCHEMA)
            connection.commit()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA busy_timeout=30000')
        return connection

    def get(self, key):
        rows = self.get_many([key])
        return rows.get(key)

    def get_many(self, keys):
        """{key: row} for the keys this cache already answers. Never raises."""
        wanted = [key for key in dict.fromkeys(keys) if key]
        if not wanted:
            return {}
        found = {}
        try:
            with closing(self.connect()) as connection:
                connection.row_factory = sqlite3.Row
                for start in range(0, len(wanted), 400):
                    chunk = wanted[start:start + 400]
                    marks = ','.join('?' * len(chunk))
                    for row in connection.execute(
                            f'SELECT {",".join(COLUMNS)} FROM addresses WHERE key IN ({marks})',
                            chunk):
                        found[row['key']] = dict(row)
        except sqlite3.Error:
            return found
        return found

    def put_many(self, rows):
        """Write answered addresses. Returns how many rows were stored."""
        values = [tuple(row.get(name) for name in COLUMNS) for row in rows]
        if not values:
            return 0
        marks = ','.join('?' * len(COLUMNS))
        with closing(self.connect()) as connection:
            connection.executemany(
                f'INSERT OR REPLACE INTO addresses ({",".join(COLUMNS)}) VALUES ({marks})', values)
            connection.commit()
        return len(values)

    def total(self):
        try:
            with closing(self.connect()) as connection:
                return int(connection.execute('SELECT COUNT(*) FROM addresses').fetchone()[0])
        except sqlite3.Error:
            return 0


def district_of(row, cycle):
    """The cached district for a cycle's boundary vintage, or ''.

    The cache holds the answer on BOTH maps because one geocode serves every
    cycle; which one a pane reads is decided here, from the cycle's vintage.
    """
    vintage = district_geo.vintage_for_cycle(cycle)
    if not vintage or not isinstance(row, dict):
        return ''
    return str(row.get(f'district_{vintage}') or '')


# ------------------------------------------------------------- batch client

def build_batch_csv(items):
    """`id,street,city,state,zip` with no header, exactly what the service takes.

    `items` is [(id, street, city, state, zip5)]. The CSV is written with the
    csv module so a comma inside a street name cannot shift a column.
    """
    body = io.StringIO(newline='')
    writer = csv.writer(body, lineterminator='\n')
    for identifier, street, city, state, postal in items:
        writer.writerow([identifier, street, city, state, postal])
    return body.getvalue().encode('utf-8')


def parse_batch_csv(text):
    """{id: result} from the service's answer. Unknown shapes are no_match.

    The answer is quoted CSV with no header and a variable width: a match
    carries eleven more fields, a refusal carries two. The rows come back out
    of order, so the id leads every result.
    """
    results = {}
    for fields in csv.reader(io.StringIO(text, newline='')):
        if not fields:
            continue
        identifier = fields[0].strip()
        if not identifier:
            continue
        status = (fields[2].strip().upper() if len(fields) > 2 else '')
        quality = (fields[3].strip().upper() if len(fields) > 3 else '')
        if status == 'MATCH':
            matched = 'non_exact' if quality and quality != 'EXACT' else 'exact'
        elif status == 'TIE':
            matched = 'tie'
        else:
            matched = 'no_match'
        entry = {'id': identifier, 'matched': matched, 'latitude': None, 'longitude': None,
                 'tract': '', 'matched_address': fields[4].strip() if len(fields) > 4 else ''}
        if matched in ('exact', 'non_exact') and len(fields) >= 12:
            point = (fields[5] or '').split(',')
            if len(point) == 2:
                try:
                    entry['longitude'] = float(point[0])
                    entry['latitude'] = float(point[1])
                except ValueError:
                    entry['longitude'] = entry['latitude'] = None
            state_fips = re.sub(r'\D', '', fields[8])[:2].zfill(2)
            county = re.sub(r'\D', '', fields[9])[:3].zfill(3)
            tract = re.sub(r'\D', '', fields[10])[:6].zfill(6)
            if state_fips.strip('0') or county.strip('0') or tract.strip('0'):
                entry['tract'] = state_fips + county + tract
        results[identifier] = entry
    return results


class Client:
    """The Census batch geocoder. One method that matters: `geocode`."""

    def __init__(self, url=BATCH_URL, benchmark=BENCHMARK, vintage=VINTAGE, timeout=TIMEOUT,
                 opener=None):
        self.url = url
        self.benchmark = benchmark
        self.vintage = vintage
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen

    def post(self, payload):
        """The raw answer text for one uploaded CSV, or a ValueError naming why."""
        boundary = '----mentat' + uuid.uuid4().hex
        fields = {'benchmark': self.benchmark, 'vintage': self.vintage,
                  'returntype': 'geographies'}
        chunks = []
        for name, value in fields.items():
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
                          f'\r\n\r\n{value}\r\n'.encode('utf-8'))
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="addressFile"; '
                      f'filename="addresses.csv"\r\nContent-Type: text/csv\r\n\r\n'.encode('utf-8'))
        chunks.append(payload)
        chunks.append(f'\r\n--{boundary}--\r\n'.encode('utf-8'))
        data = b''.join(chunks)
        request = urllib.request.Request(self.url, data=data, method='POST', headers={
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'Content-Length': str(len(data)), 'User-Agent': USER_AGENT})
        try:
            with self.opener(request, timeout=self.timeout) as response:
                return response.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as error:
            raise ValueError(f'The {SERVICE} answered {error.code} {error.reason}.') from error
        except urllib.error.URLError as error:
            raise ValueError(f'The {SERVICE} could not be reached ({error.reason}).') from error
        except OSError as error:
            raise ValueError(f'The {SERVICE} could not be reached '
                             f'({type(error).__name__}: {error}).') from error

    def geocode(self, items):
        """[(id, street, city, state, zip5)] -> {id: result}, one request.

        More than the service's own ceiling is refused here rather than sent
        and silently truncated.
        """
        items = list(items)
        if not items:
            return {}
        if len(items) > BATCH_LIMIT:
            raise ValueError(f'The {SERVICE} takes at most {BATCH_LIMIT:,} addresses per request; '
                             f'{len(items):,} were offered.')
        return parse_batch_csv(self.post(build_batch_csv(items)))


def service_identifiers(opener=None):
    """What the service says its benchmarks and vintages are, right now.

    Read-only, and used by the receipt rather than by the job: the identifiers
    the job sends are pinned constants, and this is how they are checked
    against the service instead of being taken on trust.
    """
    open_url = opener or urllib.request.urlopen

    def read(url):
        request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        with open_url(request, timeout=60) as response:
            return json.loads(response.read().decode('utf-8', 'replace'))

    benchmarks = read(BENCHMARKS_URL).get('benchmarks') or []
    vintages = read(f'{VINTAGES_URL}?benchmark={BENCHMARK}').get('vintages') or []
    return {'benchmarks': benchmarks, 'vintages': vintages,
            'benchmark_used': BENCHMARK, 'vintage_used': VINTAGE,
            'district_vintages': dict(DISTRICT_VINTAGES)}


# ----------------------------------------------------------------- the jobs

_LOCK = threading.Lock()
#: (data root, import id) -> the running thread, so a record that claims to be
#: running can be told apart from one whose thread died with a restart.
_JOBS = {}
#: (data root, import id) -> the live Job, so Stop reaches the thread itself.
_RUNS = {}


def job_path(data_root, identifier):
    return Path(data_root) / 'geocode' / f'{identifier}.job.json'


def _write_job(path, job):
    """Write the job state, tolerating a poller's brief hold on the file."""
    for attempt in range(6):
        temporary = path.with_suffix(f'.tmp{attempt}')
        try:
            temporary.write_text(json.dumps(job), encoding='utf-8')
            os.replace(temporary, path)
            return
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            time.sleep(0.05 * (attempt + 1))


def read_job(data_root, identifier):
    """The stored state of one import's geocoding, or None.

    A job whose thread is gone (a restart, a crash) is reported stopped rather
    than left claiming to run: the cache is still valid and Start resumes.
    """
    path = job_path(data_root, identifier)
    try:
        job = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(job, dict):
        return None
    if job.get('state') == 'running':
        with _LOCK:
            thread = _JOBS.get((str(data_root), identifier))
        if thread is None or not thread.is_alive():
            job['state'] = 'stopped'
            job['message'] = ('Geocoding stopped when the console did; the addresses already '
                              'matched are kept. Start again to carry on.')
    return job


def running(data_root, identifier):
    with _LOCK:
        for finished in [key for key, thread in _JOBS.items() if not thread.is_alive()]:
            _JOBS.pop(finished, None)
        thread = _JOBS.get((str(data_root), identifier))
    return bool(thread is not None and thread.is_alive())


def progress_line(job):
    """The one sentence the page shows while a job runs, or when it ends."""
    done, total = int(job.get('rows_done') or 0), int(job.get('rows_total') or 0)
    matched, cached = int(job.get('matched') or 0), int(job.get('cached') or 0)
    if job.get('state') == 'running':
        return (f'Geocoding {done:,} of {total:,} addresses · {matched:,} matched · '
                f'{cached:,} already cached · sent to the {SERVICE}')
    if job.get('state') == 'failed':
        return job.get('message') or 'Geocoding failed.'
    if job.get('state') == 'stopped':
        return (f'Geocoding stopped at {done:,} of {total:,} addresses · {matched:,} matched · '
                'the rest stay by ZIP. Start again to carry on.')
    return (f'{matched:,} of {total:,} addresses matched to a street address · '
            'the rest stay by ZIP')


class Job:
    """One import's geocoding run: a background thread and a json state file.

    Resumable by construction. The cache is the record of what is done, so a
    job that stops half way and starts again sends only what is still missing.
    No exception may leave `run`: a failure is recorded on the job and the
    thread ends.
    """

    def __init__(self, data_root, identifier, addresses, cache=None, client=None):
        self.data_root = str(data_root)
        self.identifier = identifier
        self.addresses = list(addresses)
        self.cache = cache or Cache(data_root)
        self.client = client or Client()
        self.path = job_path(data_root, identifier)
        self.stopping = threading.Event()
        self.job = {'id': identifier, 'state': 'running', 'message': '',
                    'rows_total': len(self.addresses), 'rows_done': 0, 'matched': 0,
                    'non_exact': 0, 'no_match': 0, 'tie': 0, 'cached': 0, 'sent': 0,
                    'requests': 0, 'service': SERVICE, 'benchmark': self.client.benchmark,
                    'vintage': self.client.vintage, 'started_at': time.time(),
                    'finished_at': None, 'seconds': 0.0}
        self.job['progress'] = progress_line(self.job)

    def save(self):
        self.job['progress'] = progress_line(self.job)
        _write_job(self.path, dict(self.job))

    def stop(self):
        self.stopping.set()

    def run(self):
        started = time.monotonic()
        try:
            self._run()
        except ValueError as error:
            self.job['state'] = 'failed'
            self.job['message'] = str(error)
        except Exception as error:                       # never leave the thread
            self.job['state'] = 'failed'
            self.job['message'] = f'Geocoding failed ({type(error).__name__}: {error}).'
        finally:
            self.job['seconds'] = round(time.monotonic() - started, 2)
            self.job['finished_at'] = time.time()
            if self.job['state'] == 'running':
                self.job['state'] = 'stopped' if self.stopping.is_set() else 'done'
            self.save()
            with _LOCK:
                _JOBS.pop((self.data_root, self.identifier), None)

    def _run(self):
        known = self.cache.get_many([item[0] for item in self.addresses])
        self.job['cached'] = len(known)
        self.job['rows_done'] = len(known)
        self.job['matched'] = sum(1 for row in known.values()
                                  if row.get('matched') in ('exact', 'non_exact'))
        self.save()
        pending = [item for item in self.addresses if item[0] not in known]
        for start in range(0, len(pending), CHUNK):
            if self.stopping.is_set():
                return
            chunk = pending[start:start + CHUNK]
            results = self.client.geocode(chunk)
            self.job['requests'] += 1
            self.job['sent'] += len(chunk)
            stamp = time.time()
            rows = []
            for key, street, city, state, postal in chunk:
                result = results.get(key) or {'matched': 'no_match', 'latitude': None,
                                              'longitude': None, 'tract': ''}
                tract = str(result.get('tract') or '')
                rows.append({
                    'key': key, 'street': street, 'city': city, 'state': state, 'zip5': postal,
                    'latitude': result.get('latitude'), 'longitude': result.get('longitude'),
                    'tract': tract,
                    'district_119': district_geo.district_for_tract(tract, '119') or '',
                    'district_118': district_geo.district_for_tract(tract, '118') or '',
                    'matched': result.get('matched') or 'no_match', 'source': SERVICE,
                    'fetched_at': stamp})
                grade = result.get('matched') or 'no_match'
                # `matched` counts every address the service PLACED, exact or
                # not; the three other counters name why the rest were not.
                if grade in ('exact', 'non_exact'):
                    self.job['matched'] += 1
                if grade in ('non_exact', 'tie', 'no_match'):
                    self.job[grade] += 1
            self.cache.put_many(rows)
            self.job['rows_done'] += len(chunk)
            self.save()


def addresses_for(store, identifier, committee='', cycle=''):
    """The unique, usable addresses of one import: [(key, street, city, state, zip5)].

    Reads the same SUPPORTED rows the map reads, through the same store, and
    keeps each address once. A row with no usable address is skipped here, not
    dropped from the map: the map still places it by ZIP.
    """
    import evidence_csv
    import evidence_geo
    _catalog, _chosen, headers, columns, rows = evidence_geo.import_rows(
        store, identifier, committee, cycle)
    seen, items = set(), []
    for line_number, cells in rows:
        row = evidence_csv.rebuild(headers, columns, line_number, json.loads(cells))
        values = dict(zip(columns, row['raw'].values()))
        parts = normalise(*address_of(values))
        if parts is None or parts[0] in seen:
            continue
        seen.add(parts[0])
        items.append(parts)
    return items


def start(data_root, identifier, addresses, cache=None, client=None):
    """Begin one import's job, or refuse because one is already running."""
    if running(data_root, identifier):
        raise ValueError(M_RUNNING)
    if not addresses:
        raise ValueError(M_NO_ROWS)
    job = Job(data_root, identifier, addresses, cache=cache, client=client)
    job.save()
    thread = threading.Thread(target=job.run, daemon=True, name=f'geocode-{identifier}')
    with _LOCK:
        _JOBS[(str(data_root), identifier)] = thread
        _RUNS[(str(data_root), identifier)] = job
    thread.start()
    return dict(job.job)


def stop(data_root, identifier):
    """Ask a running job to stop after the request it is in. Idempotent."""
    job = _RUNS.get((str(data_root), identifier))
    if job is not None:
        job.stop()
    current = read_job(data_root, identifier)
    if current is None:
        raise ValueError('There is no geocoding job for this import.')
    if current.get('state') == 'running':
        current = dict(current)
        current['message'] = 'Stopping after the request in flight; matched addresses are kept.'
    return current


def status(data_root, identifier, addresses=None):
    """What the page shows next to Start, whether or not a job ever ran."""
    job = read_job(data_root, identifier)
    if job is not None:
        return {**job, 'privacy': M_PRIVACY, 'running': running(data_root, identifier)}
    total = len(addresses) if addresses is not None else 0
    return {'id': identifier, 'state': 'idle', 'message': '', 'rows_total': total,
            'rows_done': 0, 'matched': 0, 'cached': 0, 'service': SERVICE,
            'benchmark': BENCHMARK, 'vintage': VINTAGE, 'privacy': M_PRIVACY,
            'running': False, 'progress': ''}
