"""Collector parity by record and replay (D-180): the same traffic, recorded once, answers every run, so a difference is the code and not the day.

One snapshot = one frozen set of job arguments and inputs, one cassette, and up to four runs of the unchanged job through replay_proxy.py:

    snapshot  --job J --snapshot S [--input NAME=FILE ...] [--workers W] -- job arguments ({work} stands for the run's work folder)
    record    R0: the job live on Windows, its traffic into the cassette (keys staged by compute_secrets.py, wiped in finally, cassette rescanned)
    replay    --side A1 | A2 (Windows, from the cassette) | B (the container, compute.compose.yaml + compute.replay.yaml, network none)
    judge     A1 against A2 (the noise floor), then R0 and B against A1; verdict in reports/judge.json
    status    --job J: each snapshot, and the streak of consecutive passes
    --plan    on any command: print what would run (paths, commands, variable NAMES; never a key) and run nothing

Root: %LOCALAPPDATA%\\Mentat\\replay\\<job>\\<snapshot>\\ (MENTAT_REPLAY_ROOT overrides), owner-only like the key folders. Every run uses the same
<snapshot>\\work path and is moved to runs\\<side> afterwards; outputs and receipts go to scratch only, never the live tree.

The judge (D-177 content parity, tools/compute_parity.py): A1 and A2 build the noise mask. A masked place whose column or leaf name says money (amount,
total, receipt, disburs, expend, cash, count, sum, cents, rank, percentil, threshold) is allowed only if A1 and A2 hold the same VALUES there (same
multiset); the mask never hides a money value. Separately, and never masked: per SQLite/CSV table the row count, the exact Decimal sum of each protected
column and an order-independent multiset digest of the protected projection; per JSON protected leaf the same, by index-free path. Those must be equal
across A1, A2 and B, and R0 against A1. Also: zero misses and refused hosts, equal served counts per request across A1/A2/B, the cassette unchanged,
`compute_job.py verify` passing on every receipt. The streak counts passes on distinct snapshots under one image digest and Mentat commit.
"""
import argparse
import csv
import datetime
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from decimal import Decimal, InvalidOperation

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(HERE))
import compute_job as cj  # noqa: E402
import compute_parity as parity  # noqa: E402
import compute_secrets as secrets  # noqa: E402
import replay_proxy as proxy  # noqa: E402

SNAPSHOT_SCHEMA = 'mentat-replay-snapshot-v1'
PROTECTED = re.compile(r'amount|total|receipt|disburs|expend|cash|count|sum|cents|rank|percentil|threshold', re.I)
IGNORE = ('_replay/*',)                                                   # receipts and replay reports: run noise by construction
WORK_MOUNT = {'pac-evidence': '/evidence', 'collect-fanout': '/collect'}  # where compute.compose.yaml mounts each job's writable folder
SIDES = ('R0', 'A1', 'A2', 'B')


class Refused(Exception):
    """A request that cannot start: nothing ran."""


def slash(path):
    return str(path).replace('\\', '/')


def emit(kind, status, summary, run_id, detail=''):
    """Glass Box event through compute_job.py event, as run_job.ps1 does; never breaks the run."""
    subprocess.run([sys.executable, str(HERE / 'compute_job.py'), 'event', '--kind', kind, '--status', status, '--summary', summary, '--run-id', run_id,
                    '--stage', 'replay', '--detail', detail], capture_output=True, check=False)


def live_fanouts():
    """PIDs of running api_company_cycle_fanout.py processes: a record run shares their key quota and must not overlap them."""
    if os.name == 'nt':
        script = "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -like '*api_company_cycle_fanout.py*' } | ForEach-Object { $_.ProcessId }"
        out = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True, text=True).stdout
    else:
        out = subprocess.run(['pgrep', '-f', 'api_company_cycle_fanout.py'], capture_output=True, text=True).stdout
    return [int(p) for p in out.split() if p.isdigit() and int(p) != os.getpid()]


def identity():
    """(Mentat commit, image digest): what the streak is keyed on. A tree with uncommitted tracked changes is another commit."""
    git = lambda *a: subprocess.run(['git', '-C', str(PROJECT), *a], capture_output=True, text=True).stdout.strip()  # noqa: E731
    commit = git('rev-parse', 'HEAD') + ('+dirty' if git('status', '--porcelain', '--untracked-files=no', '--', '.') else '')
    lock = PROJECT / 'compute.images.lock'
    return commit, str(json.loads(lock.read_text(encoding='utf-8-sig')).get('digest') or 'none') if lock.is_file() else 'none'


class Snap:
    def __init__(self, job, name, root=None):
        if job not in WORK_MOUNT or not secrets.RUN_ID.match(name):
            raise Refused('job must be one of %s and the snapshot name letters, digits, dots, dashes, underscores' % sorted(WORK_MOUNT))
        base = Path(root or os.environ.get('MENTAT_REPLAY_ROOT') or Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'Mentat' / 'replay')
        self.job, self.name, self.dir = job, name, base / job / name
        self.cassette, self.tls, self.inputs, self.work, self.runs, self.reports, self.keys = (
            self.dir / n for n in ('cassette', 'tls', 'inputs', 'work', 'runs', 'reports', 'keys'))

    @property
    def meta(self):
        if not (self.dir / 'snapshot.json').is_file():
            raise Refused('no snapshot ' + str(self.dir))
        return json.loads((self.dir / 'snapshot.json').read_text(encoding='utf-8'))

    def save(self, meta):
        (self.dir / 'snapshot.json').write_text(json.dumps(meta, indent=1) + '\n', encoding='utf-8', newline='\n')

    def run_id(self, side):
        return '%s-%s-%s' % (self.job, self.name, side)

    def load(self, name):
        path = self.reports / name
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


PATH_FLAGS = ('--source-database', '--run-directory', '--output-receipt', '--output')       # the job arguments that name a place the job writes or reads


def check_arguments(args, inputs):
    """Refuse a snapshot whose paths leave the run's work folder: a path argument must sit under {work} (the database comes in with --input), and an input NAME is a bare file name."""
    for i, a in enumerate(args):
        flag, eq, val = a.partition('=')
        v = val if eq else (args[i + 1] if i + 1 < len(args) else '')
        if flag in PATH_FLAGS and not (v == '{work}' or (v.startswith('{work}') and v[6] in '/\\' and '..' not in re.split(r'[\\/]', v))):
            raise Refused(flag + ' must be under {work}; pass the database with --input')
    for pair in inputs:
        file = pair.partition('=')[0]
        if file in ('', '.', '..') or Path(file).name != file or '/' in file or '\\' in file:
            raise Refused('--input name ' + repr(file) + ' must be a bare file name (no folders, no ..)')


def create(job, name, args, inputs=(), workers=2, root=None, restrict=True):
    check_arguments(args, inputs)
    snap = Snap(job, name, root)
    if snap.dir.exists():
        raise Refused(str(snap.dir) + ' exists; a snapshot is frozen once')
    for folder in (snap.inputs, snap.reports, snap.runs, snap.keys):
        folder.mkdir(parents=True)
    if restrict:
        secrets._owner_only(snap.dir)                                    # the icacls call the key folders use
    hashes = {}
    for pair in inputs:
        file, _, source = pair.partition('=')
        shutil.copy2(source, snap.inputs / file)
        hashes[file] = parity.sha256_file(snap.inputs / file)
    commit, digest = identity()
    snap.save({'schema': SNAPSHOT_SCHEMA, 'job': job, 'snapshot': name, 'args': list(args), 'workers': workers, 'inputs': hashes, 'commit': commit,
               'digest': digest, 'created_utc': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'state': 'new'})
    return snap


def job_args(snap, work):
    """The job's arguments for one side: {work} resolved, and (collect-fanout) the worker settings pinned so two runs schedule alike."""
    args = [a.replace('{work}', work) for a in snap.meta['args']]
    if not work.startswith('/'):
        args = [str(Path(a)) if a.startswith(work) else a for a in args]            # one kind of slash after the work folder
    if snap.job == 'collect-fanout':
        join = PurePosixPath if work.startswith('/') else Path
        w = str(snap.meta['workers'])
        args += ['--budget-database', str(join(work, '_replay', 'budget.sqlite')), '--initial-workers', w, '--max-parallel', w, '--max-lane-ceiling', w,
                 '--pool-width', w, '--recovery-workers', w, '--memory-per-lane-mb', '640', '--concurrency-per-key', '8']
    return args


def prepare_work(snap):
    if snap.work.exists():
        raise Refused(str(snap.work) + ' exists (a run was interrupted); move it aside first')
    (snap.work / '_replay' / 'receipts').mkdir(parents=True)
    for source in snap.inputs.iterdir():
        shutil.copy2(source, snap.work / source.name)


def finish_side(snap, side, command, env, run_id, collect=None):
    """Run `command` in the prepared work folder, then move it to runs/<side> and write the run record."""
    emit('run.start', 'running', 'Replay %s of %s %s' % (side, snap.job, snap.name), run_id)
    started = time.time()
    code = subprocess.run(command, env=env, cwd=str(snap.dir)).returncode
    if collect:
        collect()
    os.replace(snap.work, snap.runs / side)
    commit, digest = identity()
    (snap.reports / (side + '.run.json')).write_text(json.dumps({'side': side, 'run_id': run_id, 'exit_code': code, 'commit': commit, 'digest': digest,
                                                                 'seconds': round(time.time() - started, 1)}, indent=1) + '\n', encoding='utf-8', newline='\n')
    emit('run.finish', 'completed' if code == 0 else 'failed', 'Replay %s of %s %s exit %d' % (side, snap.job, snap.name, code), run_id)
    return code


def host_command(snap, side, keys_file):
    http = proxy.find_http_replay()
    if not http:
        raise Refused('http_replay.py not found (set MELANGE_HTTP_REPLAY)')
    run_id = snap.run_id(side)
    report = snap.reports / (side + '.replay.json')
    head = [sys.executable, str(HERE / 'replay_proxy.py'), 'record' if side == 'R0' else 'replay', str(snap.cassette), '--http-replay', str(http), '--report', str(report)]
    head += ['--secret-file', keys_file] if side == 'R0' else ['--tls', str(snap.tls), '--allow-unused']
    return head + ['--', sys.executable, str(HERE / 'compute_job.py'), 'run', snap.job, '--run-id', run_id, '--', *job_args(snap, str(snap.work))]


def host_env(snap, keys_file):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('FEC_API_KEY', 'MENTAT_REPLAY'))}
    env.update(FEC_API_KEYS_FILE=keys_file, MENTAT_COMPUTE_RECEIPT_DIR=str(snap.work / '_replay' / 'receipts'))
    return env


def record(snap, plan=False):
    meta = snap.meta
    run_id = snap.run_id('R0')
    if plan:
        return {'side': 'R0', 'run_id': run_id, 'work': str(snap.work), 'moves_to': str(snap.runs / 'R0'), 'command': host_command(snap, 'R0', '<staged keys file>'),
                'environment_names': ['FEC_API_KEYS_FILE', 'MENTAT_COMPUTE_RECEIPT_DIR'], 'live_fanouts': live_fanouts()}
    if meta['state'] != 'new':
        raise Refused('this snapshot is %s; a cassette is recorded once' % meta['state'])
    if live_fanouts():
        raise Refused('a live api_company_cycle_fanout.py is running (%s); record shares its key quota' % live_fanouts())
    module, _ = proxy.load_http_replay()
    if not (snap.tls / 'ca.pem').exists():
        proxy.mint(snap.tls)
    try:
        info = secrets.stage(run_id)
        prepare_work(snap)
        code = finish_side(snap, 'R0', host_command(snap, 'R0', info['path']), host_env(snap, info['path']), run_id)
    finally:
        secrets.wipe(run_id)
    import fec_key_store
    every_key = fec_key_store.load_keys(path=fec_key_store.key_path(), include_disabled=True)
    try:
        module.verify_clean(snap.cassette, every_key)
        clean = True
    except module.SecretLeak as error:
        clean, code = False, code or 5
        print('replay_parity: ' + str(error), file=sys.stderr)
        if snap.cassette.exists():
            shutil.rmtree(snap.cassette, ignore_errors=False)             # a cassette that holds a key is not kept on disk
    report = snap.load('R0.replay.json') or {}
    meta.update(state='recorded' if clean and code == 0 else ('dirty' if not clean else 'record-failed'), keys_count=info['count'], key_ids=info['key_ids'],
                cassette_sha256=report.get('cassette_sha256'), http_replay_sha256=report.get('http_replay_sha256'), recorded_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    snap.save(meta)
    return code


def dummy_keys(snap):
    path = snap.keys / 'dummy_keys'
    path.write_text(''.join('replay-dummy-%04d\n' % i for i in range(1, snap.meta['keys_count'] + 1)), encoding='utf-8', newline='\n')
    return path


def compose_plan(snap, scratch):
    lock = PROJECT / 'compute.images.lock'
    digest = json.loads(lock.read_text(encoding='utf-8-sig')).get('digest')
    glassbox = os.environ.get('GLASSBOX_EVENTS_DIR') or str(Path.home() / '.claude' / 'glassbox' / 'events')
    env = {'DIGEST': str(digest or '<compute.images.lock has no digest yet>'), 'MENTAT_HOST': slash(PROJECT.parent), 'LOCALAPPDATA_HOST': slash(scratch), 'GLASSBOX_HOST': slash(glassbox),
           'REPLAY_CASSETTE_HOST': slash(snap.cassette), 'REPLAY_TLS_HOST': slash(snap.tls), 'REPLAY_KEYS_FILE': slash(snap.keys / 'dummy_keys'),
           'REPLAY_HTTP_REPLAY_HOST': slash(proxy.find_http_replay() or '<http_replay.py not found>'), 'REPLAY_CACHE_HOST': slash(snap.dir / 'b_cache'),
           ('EVIDENCE_HOST' if snap.job == 'pac-evidence' else 'COLLECT_HOST'): slash(snap.work)}
    run_id = snap.run_id('B')
    command = ['docker', 'compose', '-f', str(PROJECT / 'compute.compose.yaml'), '-f', str(PROJECT / 'compute.replay.yaml'), 'run', '--rm', '--no-deps', '-T',
               '--name', 'mentat-replay-' + run_id.lower(), snap.job, 'run', snap.job, '--run-id', run_id, '--', *job_args(snap, WORK_MOUNT[snap.job])]
    return command, env, bool(re.fullmatch(r'sha256:[0-9a-f]{64}', str(digest)))


def replay(snap, side, plan=False):
    meta = snap.meta
    if side == 'B':
        scratch = snap.dir / 'b_localappdata'
        command, env_vars, has_digest = compose_plan(snap, scratch)
        if plan:
            return {'side': 'B', 'run_id': snap.run_id('B'), 'command': command, 'environment': env_vars, 'image_digest_recorded': has_digest, 'moves_to': str(snap.runs / 'B')}
    elif plan:
        return {'side': side, 'run_id': snap.run_id(side), 'work': str(snap.work), 'moves_to': str(snap.runs / side), 'command': host_command(snap, side, ''),
                'environment_names': ['FEC_API_KEYS_FILE', 'MENTAT_COMPUTE_RECEIPT_DIR']}
    if meta['state'] != 'recorded':
        raise Refused('this snapshot is %s; replay needs a clean recording' % meta['state'])
    if (snap.runs / side).exists():
        raise Refused('%s already ran for this snapshot (a side runs once; use a new snapshot)' % side)
    if side != 'B' and not (snap.runs / 'R0').is_dir():
        raise Refused('R0 has not run')
    keys = dummy_keys(snap)
    prepare_work(snap)
    run_id = snap.run_id(side)
    if side != 'B':
        return finish_side(snap, side, host_command(snap, side, ''), host_env(snap, str(keys)), run_id)
    if not has_digest:
        raise Refused('compute.images.lock has no image digest yet; B needs the CI-built compute-mentat image')
    receipts = scratch / 'Mentat' / 'compute' / 'receipts'
    receipts.mkdir(parents=True, exist_ok=True)
    (snap.dir / 'b_cache').mkdir(exist_ok=True)

    def collect():                                                             # the container wrote its receipt and report to the scratch mount
        shutil.copy2(receipts / (run_id + '.json'), snap.work / '_replay' / 'receipts' / (run_id + '.json'))
        shutil.copy2(receipts / 'replay-report.json', snap.reports / 'B.replay.json')
    return finish_side(snap, 'B', command, {**os.environ, **env_vars}, run_id, collect)


# --- the judge ----------------------------------------------------------------------------------------------------------------------------
def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        found = Decimal(repr(value) if isinstance(value, float) else str(value))
    except InvalidOperation:
        return None
    return found if found.is_finite() else None


def fingerprint(values):
    """Count, exact Decimal sum of the numeric values, and an order-independent multiset digest (a sum of per-value hashes)."""
    count, total, mix, numeric = 0, Decimal(0), 0, False
    for value in values:
        count += 1
        mix = (mix + int(hashlib.sha256(repr(value).encode('utf-8')).hexdigest(), 16)) % (1 << 256)
        found = number(value)
        if found is not None:
            total, numeric = total + found, True
    return {'n': count, 'sum': format(total, 'f') if numeric else None, 'multiset': '%064x' % mix}


def _text(path):
    return io.TextIOWrapper(gzip.open(path, 'rb') if str(path).endswith('.gz') else open(path, 'rb'), encoding='utf-8-sig', newline='')


def pattern(location):
    """A JSON pointer with array positions made '*': the same leaf whichever row it sits in."""
    return re.sub(r'/\d+(?=/|$)', '/*', location)


def money_leaf(location):
    return bool(PROTECTED.search(location.rsplit('/', 1)[-1].replace('~1', '/').replace('~0', '~')))


def groups(path):
    """{group: fingerprint} for every protected column or leaf in one file. JSON/JSONL(.gz): leaves by index-free path; CSV(.gz) and SQLite: per
    column, plus the row count and the protected projection of the rows. ponytail: a CSV is read whole; other file kinds hold no groups."""
    path, name = Path(path), Path(path).name.lower()
    stem = name[:-3] if name.endswith('.gz') else name
    if stem.endswith(('.json', '.jsonl')):
        leaves = {}
        if stem.endswith('.json') and name == stem:
            leaves = parity._json_view(path)
        elif stem.endswith('.jsonl'):
            with _text(path) as stream:
                for index, line in enumerate(stream):
                    if line.strip():
                        parity._flatten(json.loads(line), '/' + str(index), leaves)
        found = defaultdict(list)
        for location, value in leaves.items():
            if money_leaf(location):
                found[pattern(location)].append(value)
        return {key: fingerprint(values) for key, values in found.items()}
    if stem.endswith('.csv'):
        with _text(path) as stream:
            rows = list(csv.reader(stream))
        header, out = (rows[0] if rows else []), {'csv:rows': fingerprint([len(rows)])}
        mine = [i for i, h in enumerate(header) if PROTECTED.search(h)]
        for i in mine:
            out['csv:col:' + header[i]] = fingerprint(r[i] for r in rows[1:] if i < len(r))
        if mine:
            out['csv:proj'] = fingerprint(tuple(r[i] if i < len(r) else None for i in mine) for r in rows[1:])
        return out
    if stem.endswith(('.sqlite', '.sqlite3', '.db')) and name == stem:
        out, db = {}, sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
        try:
            for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
                q = parity._quote(table)
                mine = [r[1] for r in db.execute('PRAGMA table_info(%s)' % q) if PROTECTED.search(r[1])]
                try:
                    out['table:%s/rows' % table] = fingerprint([db.execute('SELECT COUNT(*) FROM %s' % q).fetchone()[0]])
                    for column in mine:
                        out['table:%s/col:%s' % (table, column)] = fingerprint(r[0] for r in db.execute('SELECT %s FROM %s' % (parity._quote(column), q)))
                    if mine:
                        out['table:%s/proj' % table] = fingerprint(db.execute('SELECT %s FROM %s' % (', '.join(map(parity._quote, mine)), q)))
                except sqlite3.Error as error:                                   # a virtual table whose module is absent
                    out['table:%s/error' % table] = fingerprint([str(error)])
        finally:
            db.close()
        return out
    return {}


def fingerprints(root, ignore=IGNORE):
    found = {rel: groups(path) for rel, path in parity.tree(root, ignore).items()}
    return {rel: g for rel, g in found.items() if g}


def differences(left, right):
    return ['%s %s' % (rel, group) for rel in sorted(set(left) | set(right)) for group in sorted(set(left.get(rel, {})) | set(right.get(rel, {})))
            if left.get(rel, {}).get(group) != right.get(rel, {}).get(group)]


def touched(rel, location, view):
    """The protected groups a masked location belongs to (none when it is plain noise)."""
    kind = parity.kind(rel)
    if kind == 'JSON':
        return [pattern(location)] if money_leaf(location) else []
    if kind == 'SQLITE':
        table, _, column = location.partition('/')
        if not table.startswith('table:') or not column:
            return []
        if column == 'rows':
            return [location]
        return [location, table + '/proj'] if column.startswith('col:') and PROTECTED.search(column[4:]) else []
    if kind == 'CSV':
        if location == 'rows':
            return ['csv:rows']
        header = str(view.get('r1/' + location.partition('/')[2], ''))
        return ['csv:col:' + header, 'csv:proj'] if PROTECTED.search(header) else []
    return []


def guard(mask, a1, a2):
    """Problems with the mask: a protected place it hides although A1 and A2 hold different values there."""
    problems = []
    for rel, locations in sorted(mask['files'].items()):
        g1, g2, kind = groups(Path(a1) / rel), groups(Path(a2) / rel), parity.kind(rel)
        if kind == 'BYTES':                                  # opaque to compute_parity: only a decoded .csv.gz/.jsonl.gz shows what it holds
            wanted = set(g1) | set(g2)
            if not wanted:
                problems.append('%s: an opaque file differs between A1 and A2; it cannot be shown to hold no money' % rel)
        else:
            view = parity.view(Path(a1) / rel) if kind == 'CSV' else {}
            wanted = {g for location in locations for g in touched(rel, location, view)}
        problems += ['%s: %s differs between A1 and A2 but is masked' % (rel, g) for g in sorted(wanted) if g1.get(g) != g2.get(g)]
    return problems


def verify_side(snap, side):
    """compute_job.verify on the side's receipt, with its output paths moved from the work folder (or the container mount) to runs/<side>."""
    path = snap.runs / side / '_replay' / 'receipts' / (snap.run_id(side) + '.json')
    if not path.is_file():
        return ['%s: no receipt' % side]
    record = json.loads(path.read_text(encoding='utf-8-sig'))
    old = WORK_MOUNT[snap.job] if side == 'B' else str(snap.work)
    for output in record.get('outputs', []):
        if output['path'].startswith(old):
            output['path'] = str(snap.runs / side) + output['path'][len(old):]
    with tempfile.TemporaryDirectory() as scratch:
        moved = Path(scratch) / 'receipt.json'
        moved.write_text(json.dumps(record), encoding='utf-8')
        return ['%s: %s' % (side, p) for p in cj.verify(moved, snap.job, snap.run_id(side))]


def judge(snap):
    meta, gates = snap.meta, {}
    present = [s for s in SIDES if (snap.runs / s).is_dir()]
    sides = [s for s in ('A1', 'A2', 'B') if s in present]

    def gate(name, problems):
        gates[name] = {'ok': not problems, 'problems': list(problems)[:25]}
    gate('sides', ['%s has not run' % s for s in ('R0', 'A1', 'A2') if s not in present])
    if not gates['sides']['ok']:
        return finish_judge(snap, meta, gates, present)
    reports = {s: snap.load(s + '.replay.json') or {} for s in ('R0', *sides)}
    runs = {s: snap.load(s + '.run.json') or {} for s in ('R0', *sides)}
    gate('recording', ([] if meta['state'] == 'recorded' else ['snapshot is ' + meta['state']])
         + ([] if proxy.cassette_digest(snap.cassette) == meta.get('cassette_sha256') else ['the cassette changed since it was recorded'])
         + ['%s: replayed a different cassette or http_replay.py' % s for s in sides if (reports[s].get('cassette_sha256'), reports[s].get('http_replay_sha256')) !=
            (meta.get('cassette_sha256'), meta.get('http_replay_sha256'))]
         + ['input %s changed' % n for n, h in meta['inputs'].items() if not (snap.inputs / n).is_file() or parity.sha256_file(snap.inputs / n) != h])
    gate('identity', ['%s ran on %s' % (s, (runs[s].get('commit'), runs[s].get('digest'))) for s in ('R0', *sides)
                      if (runs[s].get('commit'), runs[s].get('digest')) != (meta['commit'], meta['digest'])])
    gate('traffic', ['%s: exit %s' % (s, reports[s].get('exit_code')) for s in ('R0', *sides) if reports[s].get('exit_code') != 0]
         + ['%s: missed %s' % (s, reports[s].get('misses')) for s in sides if reports[s].get('misses')]
         + ['%s: refused TLS hosts %s' % (s, reports[s].get('refused_tls_hosts')) for s in sides if reports[s].get('refused_tls_hosts')])
    gate('served', ['%s served %s, A1 served %s' % (s, reports[s].get('served_per_key'), reports['A1'].get('served_per_key')) for s in sides[1:]
                    if reports[s].get('served_per_key') != reports['A1'].get('served_per_key')])
    gate('receipts', [p for s in ('R0', *sides) for p in verify_side(snap, s)])
    one, two, first = (parity.tree(snap.runs / s, IGNORE) for s in ('A1', 'A2', 'R0'))
    gate('files', ['only in one of A1/A2: ' + r for r in sorted(set(one) ^ set(two))] + ['only in one of R0/A1: ' + r for r in sorted(set(one) ^ set(first))])
    mask = parity.variance(snap.runs / 'A1', snap.runs / 'A2', IGNORE)
    (snap.reports / 'mask.json').write_text(json.dumps(mask, indent=1) + '\n', encoding='utf-8', newline='\n')
    gate('mask_guard', guard(mask, snap.runs / 'A1', snap.runs / 'A2'))
    prints = {s: fingerprints(snap.runs / s) for s in ('R0', *sides)}
    gate('money', ['A1 vs %s: %s' % (s, d) for s in ('R0', 'A2', 'B') if s in prints for d in differences(prints['A1'], prints[s])])
    if 'B' in present:
        maps = [(slash(snap.work), WORK_MOUNT[snap.job]), (slash(PROJECT.parent), '/mentat')]
        report = parity.compare(snap.runs / 'A1', snap.runs / 'B', mask, maps, IGNORE)
        gate('content_B', ['%s %s' % (rel, v['where']) for rel, v in report['files'].items() if v['class'] == 'DIFFERENT'] + report['only_in_a'] + report['only_in_b'])
    return finish_judge(snap, meta, gates, present)


def finish_judge(snap, meta, gates, present):
    verdict = {'snapshot': snap.name, 'job': snap.job, 'ok': all(g['ok'] for g in gates.values()), 'complete': 'B' in present, 'sides': present,
               'commit': meta['commit'], 'digest': meta['digest'], 'judged_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'gates': gates}
    (snap.reports / 'judge.json').write_text(json.dumps(verdict, indent=1) + '\n', encoding='utf-8', newline='\n')
    emit('run.finish', 'completed' if verdict['ok'] else 'failed', 'Replay judge %s %s: %s' % (snap.job, snap.name, 'pass' if verdict['ok'] else 'FAIL'), snap.run_id('judge'),
         ', '.join(n for n, g in gates.items() if not g['ok']))
    return verdict


def streak(entries):
    """Trailing run of passes on distinct snapshots under one (digest, commit). A failure, or a pass under other code, restarts it.
    Judgements without B (complete false) say nothing about the container and are skipped."""
    run = []
    for entry in (e for e in entries if e['complete']):
        if not entry['ok']:
            run = []
        elif run and (entry['digest'], entry['commit']) != (run[0]['digest'], run[0]['commit']):
            run = [entry]
        elif entry['snapshot'] not in {r['snapshot'] for r in run}:
            run.append(entry)
    return len(run)


def status(job, root=None):
    base = Snap(job, 'x', root).dir.parent
    rows = []
    for folder in sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []:
        snap = Snap(job, folder.name, root)
        verdict = snap.load('judge.json')
        rows.append({'snapshot': folder.name, 'state': snap.meta['state'], 'sides': [s for s in SIDES if (snap.runs / s).is_dir()],
                     'judged': verdict and {k: verdict[k] for k in ('ok', 'complete', 'judged_utc', 'commit', 'digest')}})
    judged = sorted((dict(r['judged'], snapshot=r['snapshot']) for r in rows if r['judged']), key=lambda e: e['judged_utc'])
    return {'job': job, 'snapshots': rows, 'streak': streak(judged), 'cut_over_needs': 3}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = []
    if '--' in argv:
        split = argv.index('--')
        argv, extra = argv[:split], argv[split + 1:]
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--job', required=True)
    common.add_argument('--snapshot')
    common.add_argument('--root')
    common.add_argument('--plan', action='store_true')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    made = commands.add_parser('snapshot', parents=[common])
    made.add_argument('--input', action='append', default=[], metavar='NAME=FILE')
    made.add_argument('--workers', type=int, default=2)
    commands.add_parser('record', parents=[common])
    commands.add_parser('replay', parents=[common]).add_argument('--side', choices=('A1', 'A2', 'B'), required=True)
    commands.add_parser('judge', parents=[common])
    commands.add_parser('status', parents=[common])
    args = parser.parse_args(argv)
    try:
        if args.command == 'status':
            print(json.dumps(status(args.job, args.root), indent=1))
            return 0
        if not args.snapshot:
            parser.error('--snapshot is required')
        if args.command == 'snapshot':
            if args.plan:
                print(json.dumps({'would_create': str(Snap(args.job, args.snapshot, args.root).dir), 'args': extra, 'inputs': args.input}, indent=1))
                return 0
            snap = create(args.job, args.snapshot, extra, args.input, args.workers, args.root)
            print(json.dumps({'snapshot': str(snap.dir), **{k: snap.meta[k] for k in ('commit', 'digest', 'inputs')}}, indent=1))
            return 0
        snap = Snap(args.job, args.snapshot, args.root)
        if args.command == 'judge':
            verdict = judge(snap)
            print(json.dumps({k: verdict[k] for k in ('snapshot', 'ok', 'complete', 'sides')} | {'failed': [n for n, g in verdict['gates'].items() if not g['ok']]}, indent=1))
            return 0 if verdict['ok'] else 1
        result = record(snap, args.plan) if args.command == 'record' else replay(snap, args.side, args.plan)
        if args.plan:
            print(json.dumps(result, indent=1))
            return 0
        print(json.dumps({'snapshot': snap.name, 'exit_code': result}))
        return result
    except Refused as error:
        print('replay_parity: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
