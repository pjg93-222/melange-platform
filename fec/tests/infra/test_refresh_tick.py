# -*- coding: utf-8 -*-
"""Sign-off suite for the refresh tick runner (fec/refresh_tick.py + refresh_table + refresh_state).

WHAT IT PROVES.  The old weekly refresh is one run of 49 steps that either finishes or is lost;
it has not completed since 2026-07-19 on a laptop that dies about every two days.  The tick runner
replaces it with short, resumable fires.  That claim is only worth what its crash evidence is
worth, so this suite CRASHES the runner at every commit point it declares, kills it from outside
mid-step, breaks its marker, corrupts its state, steps the clock backwards under its lock, and
then asserts the next fire converges to a publication whose OUTPUT BYTES equal an uninterrupted
control run's (T5).

HOW IT IS SAFE.  Every tick runs in a temp root with MONEYBALL_ROOT and GLASSBOX_EVENTS_DIR
redirected, and every producer is a STUB written into that temp tree.  No real producer, collector
or R script is ever imported or launched (import == execution in this repo), nothing is written
under the repository's own data/, and no event reaches the real Glass Box stream -- the last test
in the file is the negative control for exactly that.  The real modules under test are the real
ones: the runner is launched from its true path and resolves everything else from the temp root.

  python fec/tests/infra/test_refresh_tick.py
"""
import ast
import datetime
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
FEC = os.path.dirname(os.path.dirname(_HERE))
REPO_ROOT = os.path.dirname(FEC)
if FEC not in sys.path:
    sys.path.insert(0, FEC)

import paths
import pipeline_lock
import process_liveness
import refresh_state as S
import refresh_table as T
import refresh_tick as R

# The tick starts each step where ops/compute/engines.json says its PHASE runs.  A developer's
# engines.json may say 'container'; every tick this suite launches, and every in-process Tick it
# builds, must run the stubs on the host, so the override is pinned for the whole file.
for _engine_key in ('MELANGE_ENGINE_COLLECT', 'MELANGE_ENGINE_CHAIN', 'MELANGE_ENGINE_OFFICE'):
    os.environ[_engine_key] = 'windows'

TICK_PATH = os.path.join(FEC, 'refresh_tick.py')
WEEKLY_REFRESH_PATH = os.path.join(FEC, 'weekly_refresh.py')
# Anything the suite writes under the repository itself would be a bug; this is what it must not
# grow.  Captured at import, checked by the last test.
_REAL_ENGINE = os.path.join(REPO_ROOT, 'data', 'engine')


def _real_engine_fingerprint():
    try:
        return sorted(name for name in os.listdir(_REAL_ENGINE)
                      if name.startswith(('refresh_state', 'refresh_tick', 'refresh_ticks',
                                          'refresh_review', 'refresh_manifests')))
    except OSError:
        return []


_REAL_ENGINE_BEFORE = _real_engine_fingerprint()

# ---------------------------------------------------------------------------------------------
# the stub producer.  Deterministic bytes, a per-key control file, a per-key invocation counter.
# ---------------------------------------------------------------------------------------------
STUB = '''# -*- coding: utf-8 -*-
"""STUB producer for test_refresh_tick.  Writes deterministic bytes into the temp root only."""
import json, os, sys, time
ROOT = os.environ['MONEYBALL_ROOT']
def _is_date(text):
    return len(text) == 10 and text[4] == '-' and text[7] == '-' and text[:4].isdigit()
KEY = '|'.join([os.path.basename(os.path.abspath(__file__))]
               + [a for a in sys.argv[1:] if not _is_date(a)])
SLUG = ''.join(c if c.isalnum() else '-' for c in KEY).strip('-')
try:
    with open(os.path.join(ROOT, 'fec', '_stub_control.json'), encoding='utf-8') as fh:
        CONTROL = json.load(fh)
except OSError:
    CONTROL = {}
PLAN = CONTROL.get(KEY) or {}
counts = os.path.join(ROOT, 'fec', '_stub_counts')
os.makedirs(counts, exist_ok=True)
counter = os.path.join(counts, SLUG + '.txt')
try:
    seen = int(open(counter, encoding='utf-8').read().strip() or '0')
except (OSError, ValueError):
    seen = 0
seen += 1
open(counter, 'w', encoding='utf-8').write(str(seen))
out_dir = os.path.join(ROOT, 'data', 'out')
os.makedirs(out_dir, exist_ok=True)
# 'out' lets two steps write ONE shared file, which is the hand-over the zero floor must
# survive.  'rows' and 'empty' may each be a LIST -- one value per invocation -- so a two-tick
# scenario is one control file and the stub stays deterministic on its own invocation counter.
out = os.path.join(out_dir, PLAN.get('out') or (SLUG + '.csv'))
rows = PLAN.get('rows', 60)
if isinstance(rows, list):
    rows = rows[min(seen, len(rows)) - 1]
empty = PLAN.get('empty')
if isinstance(empty, list):
    empty = empty[min(seen, len(empty)) - 1]
failing = seen <= int(PLAN.get('fail_first') or 0) or bool(PLAN.get('always_fail'))
code = int(PLAN.get('exit') or 1) if failing else 0
if not failing or PLAN.get('write_then_fail'):
    body = '' if empty else (
        'id,value\\n' + ''.join('r%d,%d\\n' % (i, i * 7 % 101) for i in range(int(rows))))
    with open(out, 'w', encoding='utf-8', newline='\\n') as fh:
        fh.write(body)
junk = PLAN.get('junk')
if junk:
    junk = os.path.join(out_dir, junk)
    if failing:
        open(junk, 'w', encoding='utf-8').write('partial')
    elif os.path.exists(junk):
        os.remove(junk)
if PLAN.get('delete') and failing:
    os.remove(os.path.join(out_dir, PLAN['delete']))
if PLAN.get('sleep'):
    time.sleep(float(PLAN['sleep']))
if PLAN.get('stdout'):
    sys.stdout.write(PLAN['stdout'])
if failing:
    sys.stderr.write('stub %s failing with %d (invocation %d)\\n' % (KEY, code, seen))
    sys.exit(code)
print('stub %s wrote %s rows (invocation %d)' % (KEY, rows, seen))
'''

# The chain-tail guard parses run_pipeline.py TEXTUALLY and fails closed unless CHAIN ends with
# audit_full.py, so the stub must carry a CHAIN block of the real shape.
RUN_PIPELINE_CHAIN = '''
CHAIN = [
    ('compute_pillars.py', []),
    ('audit_full.py', []),
]
'''

# A real audit_full tail that audit_failure_gate can parse, carrying ONLY ratified dissent.
GATED_AUDIT_TAIL = ("FULL-BODY AUDIT - 245 passed, 1 FAILED\n"
                    "FAILURES: ['culling surface reports the hash-bound production promotion "
                    "state']\n")
NOVEL_AUDIT_TAIL = ("FULL-BODY AUDIT - 245 passed, 1 FAILED\n"
                    "FAILURES: ['own-source channel inputs within 28 days of the corpus "
                    "(channels are default-ON)']\n")

EVIDENCE_NAMES = tuple(step.name for step in T.evidence_steps())
CREC = 'CREC floor speeches'
CHRG = 'CHRG finance hearings'
MTP = 'interviews: MTP'
PRESS_DELTA = 'press delta refresh'
INTAKE = 'collection intake audit'
ZERO_ROW_REVIEW = 'press zero-row review'
# Two CORE sources, for the tests that need an integrity failure to BLOCK a publication.  Since
# 2026-10-15 an ENRICHMENT source (CREC, CHRG, the interviews ...) can never hold one; a CORE source
# can.  CORE_B is the LATER row, so it is the writer that takes a shared file over from CORE.
CORE = 'Congress.gov bill discovery'
CORE_B = 'Official bill evidence and automatic scoring admission'
# The one output two steps are made to share, so a hand-over can be driven on purpose (T20).
SHARED = 'data/out/shared.csv'


def stub_key(step):
    """The control-file key a step's stub will compute for itself."""
    rendered = R.render_argv(step.argv)[1:] + R.render_argv(step.tick_argv)
    return '|'.join([T.producer_basename(step)]
                    + [a for a in rendered if not _looks_like_date(a)])


def _looks_like_date(text):
    return len(text) == 10 and text[4] == '-' and text[7] == '-' and text[:4].isdigit()


def stub_slug(step):
    return ''.join(c if c.isalnum() else '-' for c in stub_key(step)).strip('-')


# ---------------------------------------------------------------------------------------------
# temp-root helpers
# ---------------------------------------------------------------------------------------------
def make_root(control=None, skip_producers=()):
    root = tempfile.mkdtemp(prefix='refresh_tick_test_')
    fec = os.path.join(root, 'fec')
    for sub in ('collect', 'legislation', 'stance', 'surfaces'):
        os.makedirs(os.path.join(fec, sub), exist_ok=True)
    os.makedirs(os.path.join(root, 'data', 'engine'), exist_ok=True)
    for step in T.TABLE:
        if T.producer_basename(step) in skip_producers:
            continue
        path = os.path.join(fec, step.argv[0].replace('/', os.sep))
        if os.path.exists(path):
            continue
        body = STUB + (RUN_PIPELINE_CHAIN if os.path.basename(path) == 'run_pipeline.py' else '')
        with open(path, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(body)
    write_control(root, control or {})
    return root


def write_control(root, control):
    with open(os.path.join(root, 'fec', '_stub_control.json'), 'w', encoding='utf-8') as handle:
        json.dump(control, handle)


def invocations(root, step):
    path = os.path.join(root, 'fec', '_stub_counts', stub_slug(step) + '.txt')
    try:
        return int(open(path, encoding='utf-8').read().strip() or '0')
    except (OSError, ValueError):
        return 0


def output_path(root, step):
    return os.path.join(root, 'data', 'out', stub_slug(step) + '.csv')


def tick_env(root, **extra):
    env = dict(os.environ, MONEYBALL_ROOT=root,
               GLASSBOX_EVENTS_DIR=os.path.join(root, '_glassbox'),
               PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    env.pop('DASHBOARD_PASSWORD_FILE', None)
    env.pop(pipeline_lock.LOCK_TOKEN_ENV, None)
    env.pop(pipeline_lock.LOCK_OWNER_PID_ENV, None)
    env.pop(R.CRASH_ENV, None)
    for key, value in extra.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = str(value)
    return env


KILL_TIMEOUT_SECONDS = 30


def kill_tree(process):
    """Kill the tick and everything it launched, bounded end to end: taskkill itself gets a
    timeout, and a tree that will not die cannot hold the test past the wait below."""
    try:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=KILL_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, OSError):
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=KILL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def run_tick(root, *args, timeout=600, **extra_env):
    """Launch the REAL runner against the temp root.  Returns (exit_code, output, seconds).

    stdin is closed: a child that inherited the harness's stdin (an outer runner's open pipe)
    could block on it forever with an idle CPU.  On a timeout the tree is killed (bounded) and
    the failure carries the tick's last output, so a stall is never silent.
    """
    started = time.time()
    with tempfile.TemporaryFile() as sink:
        process = subprocess.Popen([sys.executable, TICK_PATH] + list(args),
                                   env=tick_env(root, **extra_env), stdout=sink,
                                   stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(process)
            sink.seek(0)
            tail = sink.read().decode('utf-8', 'replace')[-4000:]
            raise AssertionError('the tick did not finish within %ss; its last output:\n%s'
                                 % (timeout, tail)) from None
        sink.seek(0)
        output = sink.read().decode('utf-8', 'replace')
    return code, output, time.time() - started


def start_tick(root, *args, **extra_env):
    """Launch a tick WITHOUT waiting; the caller kills it or waits itself."""
    sink = tempfile.TemporaryFile()
    process = subprocess.Popen([sys.executable, TICK_PATH] + list(args),
                               env=tick_env(root, **extra_env), stdout=sink,
                               stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    process._sink = sink
    return process


def drain(process):
    process._sink.seek(0)
    text = process._sink.read().decode('utf-8', 'replace')
    process._sink.close()
    return text


def read_state(root):
    with open(os.path.join(root, 'data', 'engine', 'refresh_state.json'), encoding='utf-8') as fh:
        return json.load(fh)


def read_status(root):
    with open(os.path.join(root, 'data', 'engine', 'refresh_tick_status.json'),
              encoding='utf-8') as fh:
        return json.load(fh)


def read_ticks(root):
    path = os.path.join(root, 'data', 'engine', 'refresh_ticks.jsonl')
    if not os.path.exists(path):
        return []
    with open(path, 'rb') as fh:
        raw = fh.read()
    return [json.loads(line.decode('utf-8')) for line in raw.split(b'\n') if line.strip()]


def read_review(root):
    path = os.path.join(root, 'data', 'engine', 'refresh_review.json')
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def outcome(root):
    return (read_state(root).get('last_tick') or {}).get('outcome')


def seed_state(root, evidence_age_h=1.0, attempt_age_h=1.0, steps=None, publication=None,
               last_tick=None):
    """Write a state file in which every collect row already succeeded and is fresh.

    Lets one test drive ONE step without paying for the other 22 collectors, while keeping the
    publication contract honest: eligibility still reads these rows and still refuses on anything
    torn, suspect or out of window.
    """
    now = S.utcnow()
    payload = {'schema': S.SCHEMA, 'updated_utc': S.stamp(now), 'steps': {},
               'publication': publication, 'last_tick': last_tick}
    for step in T.TABLE:
        entry = S.blank_step()
        if step.kind in T.COLLECT_KINDS:
            entry.update(state=S.OK, generation=1,
                         last_ok_utc=S.stamp(now - datetime.timedelta(hours=evidence_age_h)),
                         last_attempt_utc=S.stamp(now
                                                  - datetime.timedelta(hours=attempt_age_h)))
        payload['steps'][step.name] = entry
    for name, patch in (steps or {}).items():
        payload['steps'].setdefault(name, S.blank_step()).update(patch)
    os.makedirs(os.path.join(root, 'data', 'engine'), exist_ok=True)
    with open(os.path.join(root, 'data', 'engine', 'refresh_state.json'), 'w',
              encoding='utf-8') as handle:
        json.dump(payload, handle, indent=1)
    return payload


def seeded_publication(published_utc, generation=1, legacy=False, source_utc=None):
    """A publication record shaped exactly as ``Tick.publication_record`` writes one.

    ``legacy=True`` omits the per-source ``generation``.  That is how EVERY record written before
    the clock-free rule looks, and compute_wanted() must still fall back to the stamp comparison
    for those, or the first tick after this lands would read an old record as "no evidence at all".
    """
    inputs = {}
    for step in T.evidence_steps():
        record = {'last_ok_utc': source_utc, 'manifest_sha': 'seeded', 'age_h': 0.0}
        if not legacy:
            record['generation'] = int(generation)
        inputs[step.name] = record
    return {'last_publish_utc': published_utc, 'degraded': False, 'inputs': inputs,
            'stale_sources': []}


def seed_published(root, published_utc=None, generation=1, legacy=False, steps=None, **kwargs):
    """Seed a system that has ALREADY published and has nothing to do: a tick here is NOTHING_DUE.

    The chain row is seeded SUCCEEDED as well.  compute_wanted() asks "did the last compute
    succeed?" AFTER it asks about evidence, so a NEVER chain row makes every tick want compute
    and any test built on that would be proving nothing about the evidence rule.
    """
    rows = dict(steps or {})
    rows.setdefault(R.CHAIN_STEP, {'state': S.OK, 'generation': 1,
                                   'last_ok_utc': S.stamp(S.utcnow())})
    return seed_state(root, steps=rows,
                      publication=seeded_publication(published_utc or S.stamp(S.utcnow()),
                                                     generation=generation, legacy=legacy),
                      **kwargs)


def read_manifest_of(root, step, sha):
    path = os.path.join(root, 'data', 'engine', 'refresh_manifests',
                        '%s.%s.json' % (step.slug, sha))
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def data_digest(root):
    """Content digest of exactly what the runner watches, so T5 compares the bytes it manages."""
    recursive, top_level, extra = R.scan_spec(root)
    snap = S.snapshot(root, recursive=recursive, top_level=top_level, extra_files=extra,
                      exclude=R.runner_owned)
    digest = hashlib.sha256()
    for key in sorted(snap):
        try:
            with open(S.resolve_key(root, key), 'rb') as handle:
                body = handle.read()
        except OSError:
            continue
        digest.update(key.encode('utf-8'))
        digest.update(hashlib.sha256(body).digest())
    return digest.hexdigest()


def tree_digest(root):
    """Content digest of EVERYTHING under root, including the runner's own files."""
    digest = hashlib.sha256()
    for directory, subdirectories, files in os.walk(root):
        subdirectories.sort()
        for name in sorted(files):
            full = os.path.join(directory, name)
            relative = os.path.relpath(full, root).replace('\\', '/')
            with open(full, 'rb') as handle:
                digest.update(relative.encode('utf-8'))
                digest.update(hashlib.sha256(handle.read()).digest())
    return digest.hexdigest()


# A live peer that takes the real writer lease and holds it until a sentinel file appears.
LEASE_HOLDER = '''
import os, sys, time
sys.path.insert(0, %r)
from pipeline_lock import pipeline_run_lock
with pipeline_run_lock(%r, owner_kind='test_peer') as lease:
    open(%r, 'w').write(str(os.getpid()))
    deadline = time.time() + 120
    while time.time() < deadline and not os.path.exists(%r):
        time.sleep(0.05)
'''


class RefreshTickTest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self._roots = []
        self._children = []

    def tearDown(self):
        for process in self._children:
            try:
                if process.poll() is None:
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    process.wait(timeout=30)
            except Exception:
                pass
        for root in self._roots:
            shutil.rmtree(root, ignore_errors=True)

    def root(self, control=None, skip_producers=()):
        path = make_root(control, skip_producers)
        self._roots.append(path)
        return path

    def hold_lease(self, root):
        """Start a LIVE peer holding the writer lease; returns (process, release_callable)."""
        ready = os.path.join(root, '_peer_ready')
        release = os.path.join(root, '_peer_release')
        source = LEASE_HOLDER % (FEC, os.path.join(root, '.pipeline_run.lock'), ready, release)
        process = subprocess.Popen([sys.executable, '-c', source],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._children.append(process)
        deadline = time.time() + 30
        while time.time() < deadline and not os.path.exists(ready):
            time.sleep(0.02)
        self.assertTrue(os.path.exists(ready), 'the lease-holding peer never acquired')
        return process, (lambda: open(release, 'w').close())

    # =========================================================================================
    # T1  everything succeeds -> PUBLISHED, verified manifests, a complete publication record
    # =========================================================================================
    def test_T01_all_ok_publishes_with_verified_manifests(self):
        root = self.root()
        code, output, seconds = run_tick(root)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED, outcome(root), output[-3000:])
        state = read_state(root)
        bad = {name: entry['state'] for name, entry in state['steps'].items()
               if entry['state'] not in (S.OK, S.PARTIAL, S.SKIPPED)}
        self.assertEqual({}, bad)
        # Every OK step names a manifest, that manifest exists, and every file in it verifies.
        checked = 0
        for step in T.TABLE:
            entry = state['steps'][step.name]
            if entry['state'] not in (S.OK, S.PARTIAL):
                continue
            self.assertTrue(entry['manifest_sha'], '%s is OK without a manifest' % step.name)
            path = os.path.join(root, 'data', 'engine', 'refresh_manifests',
                                '%s.%s.json' % (step.slug, entry['manifest_sha']))
            self.assertTrue(os.path.exists(path), 'missing manifest for %s' % step.name)
            with open(path, encoding='utf-8') as handle:
                payload = json.load(handle)
            self.assertEqual([], S.verify_manifest(root, payload),
                             '%s manifest does not verify' % step.name)
            checked += 1
        self.assertEqual(50, checked)          # 51 rows, one SKIPPED_NOT_CONFIGURED deployment
        publication = state['publication']
        self.assertFalse(publication['degraded'])
        self.assertEqual(sorted(EVIDENCE_NAMES), sorted(publication['inputs']))
        for name, record in publication['inputs'].items():
            self.assertTrue(record['last_ok_utc'], name)
            self.assertTrue(record['manifest_sha'], name)
        self.assertEqual(S.NOT_CONFIGURED, state['steps'][R.PUBLISH_STEP]['skip'])
        self.assertLess(seconds, 240)

    # =========================================================================================
    # T2  a source fails once and recovers in the retry pass
    # =========================================================================================
    def test_T02_filed_failure_recovers_in_the_retry_pass(self):
        crec = T.by_name()[CREC]
        root = self.root({stub_key(crec): {'fail_first': 1}})
        seed_state(root, steps={CREC: dict(S.blank_step())})
        # The retry pass honours the TRANSIENT backoff (15 min by default, DE-020); 0 minutes is
        # the explicit "no wait" control that drives the same-tick retry itself.
        code, output, _ = run_tick(root, '--backoff-minutes', '0')
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED, outcome(root))
        self.assertIn('FILED', output)
        self.assertIn('RETRY 1 of 2', output)
        self.assertEqual(2, invocations(root, crec))
        self.assertEqual(S.OK, read_state(root)['steps'][CREC]['state'])
        self.assertEqual([], read_ticks(root)[-1]['filed'])

    # =========================================================================================
    # T3  a source that always fails: inside the window it degrades, outside it refuses
    # =========================================================================================
    def test_T03a_stale_but_intact_source_publishes_degraded(self):
        crec = T.by_name()[CREC]
        root = self.root({stub_key(crec): {'always_fail': True}})
        seed_state(root, steps={CREC: {'state': S.OK, 'generation': 1,
                                       'last_ok_utc': S.stamp(S.utcnow()
                                                              - datetime.timedelta(days=8)),
                                       'last_attempt_utc': S.stamp(S.utcnow()
                                                                   - datetime.timedelta(days=8))}})
        code, output, _ = run_tick(root, '--backoff-minutes', '0')
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED_DEGRADED, outcome(root), output[-3000:])
        publication = read_state(root)['publication']
        self.assertTrue(publication['degraded'])
        self.assertEqual([CREC], publication['stale_sources'])
        self.assertEqual(3, invocations(root, crec))      # the pass plus two retries

    def test_T03b_source_older_than_the_window_is_served_and_labelled(self):
        """REPLACES "older than the window refuses and touches nothing" (2026-10-15 ruling).

        Past its window a source's last-good generation is SERVED and LABELLED, not a stop: the
        refusal was the all-or-nothing halt the tiered degrade removes.  What the old test
        protected still holds -- the previous bytes are untouched by a failing collector.
        """
        crec = T.by_name()[CREC]
        root = self.root({stub_key(crec): {'always_fail': True}})
        os.makedirs(os.path.join(root, 'data', 'out'), exist_ok=True)
        previous = output_path(root, crec)
        with open(previous, 'wb') as handle:
            handle.write(b'id,value\nPREVIOUS,1\n')
        seed_state(root, steps={CREC: {'state': S.OK, 'generation': 1,
                                       'last_ok_utc': S.stamp(S.utcnow()
                                                              - datetime.timedelta(days=15)),
                                       'last_attempt_utc': S.stamp(S.utcnow()
                                                                   - datetime.timedelta(days=15))}})
        code, output, _ = run_tick(root)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED_DEGRADED, outcome(root), output[-3000:])
        self.assertEqual(b'id,value\nPREVIOUS,1\n', open(previous, 'rb').read(),
                         'a failing collector rewrote the last-good bytes it was serving')
        publication = read_state(root)['publication']
        self.assertEqual([CREC], publication['stale_sources'])
        self.assertIn('past the 14-day window', publication['labels'][CREC]['detail'])
        self.assertNotIn('older than the 14-day window', output)

    # =========================================================================================
    # T4  torn outputs block publication until the step's next success
    # =========================================================================================
    def test_T04_torn_source_blocks_then_heals(self):
        core = T.by_name()[CORE]
        key = stub_key(core)
        root = self.root({key: {'always_fail': True, 'write_then_fail': True}})
        # It HAS succeeded before, so "torn" is the only thing that can block it here: a source
        # that never succeeded would be refused for that reason instead and prove nothing.
        seed_state(root)
        code, output, _ = run_tick(root, '--force-due', CORE)
        self.assertEqual(1, code, output[-3000:])
        self.assertEqual(R.COLLECTED_NOT_PUBLISHED, outcome(root))
        self.assertTrue(read_state(root)['steps'][CORE]['torn'], 'the step wrote and died')
        self.assertIn('outputs are torn', output)
        write_control(root, {})
        code, output, _ = run_tick(root, '--force-due', CORE)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED, outcome(root))
        self.assertFalse(read_state(root)['steps'][CORE]['torn'])

    def test_T04b_never_committed_partial_file_deleted_by_next_success_heals(self):
        core = T.by_name()[CORE]
        key = stub_key(core)
        root = self.root({key: {'always_fail': True, 'junk': 'x.partial'}})
        seed_state(root)
        code, output, _ = run_tick(root, '--force-due', CORE)
        self.assertEqual(1, code, output[-3000:])
        self.assertTrue(read_state(root)['steps'][CORE]['torn'])
        write_control(root, {key: {'junk': 'x.partial'}})
        code, output, _ = run_tick(root, '--force-due', CORE)
        self.assertEqual(0, code, output[-3000:])
        self.assertNotIn('did not repair', output)
        self.assertFalse(read_state(root)['steps'][CORE]['torn'])

    def test_T04b_deleting_a_file_then_failing_is_torn(self):
        core = T.by_name()[CORE]
        key = stub_key(core)
        root = self.root({key: {'always_fail': True, 'delete': 'victim.csv'}})
        seed_state(root)
        victim = os.path.join(root, 'data', 'out', 'victim.csv')
        os.makedirs(os.path.dirname(victim), exist_ok=True)
        with open(victim, 'w', encoding='utf-8') as handle:
            handle.write('id,value\nA,1\n')
        code, output, _ = run_tick(root, '--force-due', CORE)
        self.assertEqual(1, code, output[-3000:])
        self.assertFalse(os.path.exists(victim), 'the stub did not delete the file')
        entry = read_state(root)['steps'][CORE]
        self.assertTrue(entry['torn'], 'a deletion before the failure was not seen as a write')
        self.assertIn('data/out/victim.csv', entry['unverified_files'])

    # =========================================================================================
    # T5  every crash point, an external kill and a CTRL_BREAK all converge to the same bytes
    # =========================================================================================
    def _control_digest(self):
        control = {stub_key(T.by_name()[CREC]): {'sleep': 4}}
        root = self.root(control)
        code, output, _ = run_tick(root)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED, outcome(root))
        return control, data_digest(root)

    def _assert_no_unverified_ok(self, root):
        state = read_state(root)
        for step in T.TABLE:
            entry = state['steps'][step.name]
            if entry['state'] not in (S.OK, S.PARTIAL):
                continue
            self.assertTrue(entry['manifest_sha'],
                            '%s is OK with no manifest' % step.name)
            path = os.path.join(root, 'data', 'engine', 'refresh_manifests',
                                '%s.%s.json' % (step.slug, entry['manifest_sha']))
            self.assertTrue(os.path.exists(path), '%s names a manifest that is gone' % step.name)
            with open(path, encoding='utf-8') as handle:
                self.assertEqual([], S.verify_manifest(root, json.load(handle)),
                                 '%s manifest does not verify' % step.name)

    def test_T05_crash_points_and_kills_converge_to_the_control_bytes(self):
        control, expected = self._control_digest()
        for point in ('after_step_exit', 'after_manifest_write', 'after_state_commit',
                      'before_compute', 'after_compute_before_publication_record'):
            with self.subTest(crash=point):
                root = self.root(control)
                code, first, _ = run_tick(root, **{R.CRASH_ENV: point})
                self.assertEqual(137, code, 'crash point %s never fired:\n%s'
                                 % (point, first[-2000:]))
                self._assert_no_unverified_ok(root)
                code, second, _ = run_tick(root)
                self.assertEqual(0, code, second[-3000:])
                self.assertEqual(R.PUBLISHED, outcome(root), second[-3000:])
                self._assert_no_unverified_ok(root)
                self.assertEqual(expected, data_digest(root),
                                 'crash at %s produced different output bytes' % point)

        for how in ('external_kill', 'ctrl_break'):
            with self.subTest(kill=how):
                root = self.root(control)
                process = start_tick(root)
                state_path = os.path.join(root, 'data', 'engine', 'refresh_state.json')
                deadline = time.time() + 60
                running = None
                while time.time() < deadline and running is None:
                    try:
                        with open(state_path, encoding='utf-8') as handle:
                            payload = json.load(handle)
                        running = next((name for name, entry in payload['steps'].items()
                                        if entry.get('state') == S.RUNNING), None)
                    except (OSError, ValueError, KeyError):
                        pass
                    if running is None:
                        time.sleep(0.05)
                self.assertEqual(CREC, running, 'never caught the sleeping step running')
                killed_at = time.time()
                if how == 'external_kill':
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    os.kill(process.pid, signal.CTRL_BREAK_EVENT)
                process.wait(timeout=60)
                elapsed = time.time() - killed_at
                drain(process)
                # The contract's bound is 2 s; 20 s here so a loaded kernel cannot turn "did not
                # hang" into a red, while a tick that waited on its sleeping child still fails.
                self.assertLess(elapsed, 20, 'the tick took %.1fs to die after %s' % (elapsed,
                                                                                      how))
                self._assert_no_unverified_ok(root)
                code, second, _ = run_tick(root)
                self.assertEqual(0, code, second[-3000:])
                self.assertEqual(R.PUBLISHED, outcome(root), second[-3000:])
                self.assertEqual(expected, data_digest(root),
                                 '%s produced different output bytes' % how)

    # =========================================================================================
    # T6  N1 and N2: an unpublished tick never exits 0, and a post-chain failure is not success
    # =========================================================================================
    def test_T06a_every_unpublished_outcome_exits_non_zero(self):
        for name, code in R.EXIT_CODES.items():
            if name in (R.PUBLISHED, R.PUBLISHED_DEGRADED, R.NOTHING_DUE):
                self.assertEqual(0, code, name)
            else:
                self.assertNotEqual(0, code, '%s exits 0 while nothing was published' % name)

    def test_T06b_hard_failure_after_the_chain_is_not_reported_as_published(self):
        publish = T.by_name()[R.PUBLISH_STEP]
        root = self.root({stub_key(publish): {'always_fail': True}})
        password = os.path.join(root, 'dashboard_password.txt')
        with open(password, 'w', encoding='utf-8') as handle:
            handle.write('x')
        code, output, _ = run_tick(root, DASHBOARD_PASSWORD_FILE=password)
        state = read_state(root)
        self.assertEqual(S.OK, state['steps'][R.CHAIN_STEP]['state'],
                         'the chain itself must have succeeded for this test to mean anything')
        self.assertEqual(S.FAILED, state['steps'][R.PUBLISH_STEP]['state'])
        self.assertEqual(R.COMPUTE_FAILED, outcome(root), output[-3000:])
        self.assertEqual(1, code)
        self.assertIsNone(state['publication'], 'a failed deployment was recorded as published')

    # =========================================================================================
    # T7  an unwritable status marker costs under two seconds and loses no outcome
    # =========================================================================================
    def test_T07_unwritable_status_marker_costs_under_two_seconds(self):
        # The direct measurement first: the marker is a projection nobody depends on, so even a
        # tick that tried to write it on every step boundary must not pay seconds for it.
        root = self.root()
        blocked_path = os.path.join(root, 'data', 'engine', R.STATUS_NAME)
        os.makedirs(blocked_path, exist_ok=True)                     # a DIRECTORY in its place
        marker = S.StatusMarker(blocked_path)
        started = time.monotonic()
        for _ in range(200):
            marker.write({'phase': 'x'})
        self.assertLess(time.monotonic() - started, 2.0,
                        '200 marker writes against an unwritable path took too long')
        self.assertTrue(marker.abandoned, 'the marker never gave up')
        # And end to end. Wall clock under a loaded kernel is noisy, so each side is the BEST of
        # three: a load spike must not be able to read as marker cost.
        control = {}
        writable = self.root(control)
        baseline = min(run_tick(writable, '--only', CREC, '--force-due', 'all')[2]
                       for _ in range(3))
        blocked = self.root(control)
        os.makedirs(os.path.join(blocked, 'data', 'engine', R.STATUS_NAME), exist_ok=True)
        runs = [run_tick(blocked, '--only', CREC, '--force-due', 'all') for _ in range(3)]
        code, output, _seconds = runs[-1]
        slowed = min(run[2] for run in runs)
        self.assertIn('status marker unwritable', output)
        self.assertLess(slowed - baseline, 2.0,
                        'an unwritable marker added %.2fs (baseline %.2fs)'
                        % (slowed - baseline, baseline))
        self.assertEqual(R.COLLECTED_NOT_PUBLISHED, outcome(blocked), output[-2000:])
        self.assertEqual(code, 1)
        self.assertEqual(S.OK, read_state(blocked)['steps'][CREC]['state'])

    # =========================================================================================
    # T8  no terminal outcome is visible while a tick is still running
    # =========================================================================================
    def test_T08_no_terminal_outcome_while_the_tick_runs(self):
        crec = T.by_name()[CREC]
        root = self.root({stub_key(crec): {'sleep': 5}})
        previous = {'outcome': R.PUBLISHED_DEGRADED, 'exit_code': 0,
                    'started_utc': S.stamp(S.utcnow()), 'ended_utc': S.stamp(S.utcnow())}
        seed_state(root, steps={CREC: dict(S.blank_step())},
                   publication={'last_publish_utc': S.stamp(S.utcnow()), 'degraded': True,
                                'inputs': {}, 'stale_sources': [CREC]},
                   last_tick=previous)
        process = start_tick(root, '--force-due', CREC)
        seen = []
        deadline = time.time() + 60
        while time.time() < deadline and process.poll() is None:
            try:
                status = read_status(root)
            except (OSError, ValueError):
                time.sleep(0.05)
                continue
            if status.get('phase') in (R.PHASE_COLLECT, R.PHASE_RETRY, R.PHASE_COMPUTE):
                seen.append(status)
                if len(seen) >= 2:
                    break
            time.sleep(0.05)
        self.assertTrue(seen, 'never observed the tick mid-run')
        for status in seen:
            self.assertEqual(R.PUBLISHED_DEGRADED, (status['last_tick'] or {})['outcome'],
                             'a terminal outcome appeared while the tick was still running')
            self.assertNotIn('tick', status)
        process.wait(timeout=300)
        drain(process)
        self.assertEqual(R.PUBLISHED, outcome(root))
        self.assertEqual(R.PHASE_IDLE, read_status(root)['phase'])
        self.assertEqual(R.PUBLISHED, read_status(root)['last_tick']['outcome'])

    # =========================================================================================
    # T9  BLOCKED leaves the last publication and the last good tick intact
    # =========================================================================================
    def test_T09_blocked_preserves_the_last_publication(self):
        root = self.root()
        code, output, _ = run_tick(root)
        self.assertEqual(0, code, output[-2000:])
        before_state = read_state(root)
        before_ticks = read_ticks(root)
        before_status = read_status(root)
        _peer, release = self.hold_lease(root)
        try:
            code, output, seconds = run_tick(root, '--lease-wait-hours', '0',
                                             '--force-due', 'all')
        finally:
            release()
        self.assertEqual(75, code, output[-2000:])
        self.assertEqual(R.BLOCKED, outcome(root), output[-2000:])
        self.assertLess(seconds, 120)
        after_state = read_state(root)
        self.assertEqual(before_state['publication'], after_state['publication'],
                         'BLOCKED erased the publication record')
        self.assertEqual(R.PUBLISHED, before_ticks[-1]['outcome'])
        self.assertEqual([R.PUBLISHED, R.BLOCKED], [t['outcome'] for t in read_ticks(root)])
        self.assertEqual(before_status['last_publication'],
                         read_status(root)['last_publication'])

    # =========================================================================================
    # T10  lock identity: recycled pids, a backward clock step, and a refused second acquirer
    # =========================================================================================
    @staticmethod
    def _wip_rule_holder_is_live(payload):
        """The WIP's rule, reproduced EXACTLY as the negative control (commit 352a5e495).

        It compared the holder's kernel start time with the WALL-CLOCK ``started_utc`` written at
        acquisition, with five seconds of slack.
        """
        if not process_liveness.process_alive(payload.get('pid')):
            return False
        started = process_liveness.process_start_time(payload.get('pid'))
        if started is None:
            return True
        try:
            written = float(payload.get('started_utc'))
        except (TypeError, ValueError):
            return True
        return started <= written + 5.0

    def _live_child(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._children.append(process)
        start = process_liveness.process_start_time(process.pid)
        self.assertIsNotNone(start, 'process_start_time could not read a child we just spawned')
        return process, start

    def test_T10a_recycled_pid_is_detected(self):
        root = self.root()
        path = os.path.join(root, '.pipeline_run.lock')
        process, start = self._live_child()
        body = {'pid': process.pid, 'token': 'abc', 'owner_kind': 'weekly_refresh',
                'host': pipeline_lock.host_identity(),
                'started_utc': start - 3600.0, 'proc_start': start - 3600.0}
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(body, handle)
        self.assertFalse(pipeline_lock._holder_is_live(body),
                         'a pid whose process started at a different instant is a RECYCLED pid')
        self.assertTrue(pipeline_lock._stale_lock_may_be_removed(
            pipeline_lock.Path(path), body))

    def test_T10b_backward_clock_step_cannot_reclaim_a_live_holder(self):
        root = self.root()
        path = os.path.join(root, '.pipeline_run.lock')
        process, start = self._live_child()
        # The clock stepped back an hour between the holder's START and its ACQUISITION, so the
        # wall-clock stamp it wrote is an hour EARLIER than its own kernel creation time.
        body = {'pid': process.pid, 'token': 'abc', 'owner_kind': 'weekly_refresh',
                'started_utc': start - 3600.0, 'proc_start': start}
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(body, handle)
        self.assertTrue(pipeline_lock._holder_is_live(body),
                        'the shipped rule reclaimed a LIVE holder after a backward clock step')
        self.assertFalse(pipeline_lock._stale_lock_may_be_removed(
            pipeline_lock.Path(path), body))
        # NEGATIVE CONTROL: the WIP rule DOES reclaim it in exactly this scenario.
        self.assertFalse(self._wip_rule_holder_is_live(body),
                         'the negative control no longer reproduces the defect it exists for')

    def test_T10c_absent_proc_start_keeps_the_pid_rule(self):
        root = self.root()
        path = os.path.join(root, '.pipeline_run.lock')
        process, _start = self._live_child()
        live = {'pid': process.pid, 'token': 'abc', 'owner_kind': 'weekly_refresh',
                'host': pipeline_lock.host_identity(), 'started_utc': time.time()}
        self.assertTrue(pipeline_lock._holder_is_live(live))
        self.assertFalse(pipeline_lock._stale_lock_may_be_removed(pipeline_lock.Path(path), live))
        dead = subprocess.Popen([sys.executable, '-c', 'pass'])
        dead.wait(timeout=30)
        stale = {'pid': dead.pid, 'token': 'abc', 'owner_kind': 'weekly_refresh',
                 'host': pipeline_lock.host_identity(), 'started_utc': time.time()}
        self.assertFalse(pipeline_lock._holder_is_live(stale))

    def test_T10d_second_acquirer_is_refused_while_the_first_lives(self):
        root = self.root()
        _peer, release = self.hold_lease(root)
        try:
            with self.assertRaises(RuntimeError):
                with pipeline_lock.pipeline_run_lock(os.path.join(root, '.pipeline_run.lock'),
                                                     owner_kind='refresh_tick'):
                    pass
            with open(os.path.join(root, '.pipeline_run.lock'), encoding='utf-8') as handle:
                body = json.load(handle)
            self.assertEqual('test_peer', body['owner_kind'])
            self.assertIn('proc_start', body)
            self.assertAlmostEqual(process_liveness.process_start_time(body['pid']),
                                   body['proc_start'], delta=2.0)
        finally:
            release()

    # =========================================================================================
    # T11  the budget stops the tick launching anything else
    # =========================================================================================
    def test_T11_budget_stops_further_launches(self):
        crec, chrg = T.by_name()[CREC], T.by_name()[CHRG]
        root = self.root({stub_key(crec): {'sleep': 3}})
        code, output, _ = run_tick(root, '--budget-hours', '0.0004')   # 1.44 s
        self.assertEqual(1, code, output[-3000:])
        self.assertEqual(R.COLLECTED_NOT_PUBLISHED, outcome(root))
        record = read_ticks(root)[-1]
        self.assertEqual([CREC], record['steps_run'])
        self.assertEqual(0, invocations(root, chrg), 'a step launched after the budget was spent')
        self.assertIn('budget', [b['what'] for b in record['blockers']])
        self.assertFalse(os.path.exists(output_path(root, chrg)))

    # =========================================================================================
    # T12  the table is pinned to weekly_refresh.py by AST
    # =========================================================================================
    @staticmethod
    def _main_steps():
        """weekly_refresh.py's step() calls, read as TEXT (importing it would run the refresh)."""
        with open(WEEKLY_REFRESH_PATH, encoding='utf-8') as handle:
            tree = ast.parse(handle.read())
        today = datetime.date.today()

        def literal(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'str':
                inner = node.args[0]
                if isinstance(inner, ast.Name) and inner.id == 'yesterday':
                    return str(today - datetime.timedelta(days=1))
                if isinstance(inner, ast.Name) and inner.id == 'today':
                    return str(today)
                if (isinstance(inner, ast.BinOp) and isinstance(inner.op, ast.Sub)
                        and isinstance(inner.left, ast.Name) and inner.left.id == 'today'
                        and isinstance(inner.right, ast.Call)):
                    days = {kw.arg: kw.value.value for kw in inner.right.keywords}['days']
                    return str(today - datetime.timedelta(days=days))
            raise AssertionError('unhandled argv element at line %d' % node.lineno)

        rows = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'step'):
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords}
            rows.append((node.lineno, node.args[0].value,
                         tuple(literal(el) for el in node.args[1].elts),
                         keywords['timeout'].value,
                         bool(keywords['hard'].value) if 'hard' in keywords else False))
        rows.sort()
        return [row[1:] for row in rows]

    def test_T12_table_matches_weekly_refresh_step_for_step(self):
        expected = self._main_steps()
        today = datetime.date.today()
        actual = [(step.name, tuple(R.render_argv(step.argv, today)), step.timeout_s, step.hard)
                  for step in T.pinned_steps()]
        self.assertEqual(49, len(expected), 'weekly_refresh.py no longer declares 49 steps')
        self.assertEqual(len(expected), len(actual))
        for index, (want, got) in enumerate(zip(expected, actual)):
            self.assertEqual(want, got, 'row %d drifted from weekly_refresh.py' % (index + 1))
        self.assertEqual([], T.validate())
        # The zero-row review and the Bluesky collector (the daily media refresh's own leg).
        self.assertEqual(2, sum(1 for step in T.TABLE if step.tick_only))

    # =========================================================================================
    # T13  an unreadable state file is rotated, not trusted
    # =========================================================================================
    def test_T13_corrupt_state_is_rotated_and_everything_becomes_due(self):
        root = self.root()
        engine = os.path.join(root, 'data', 'engine')
        corrupt = b'{"schema": 1, "steps": {  <- not json at all\n'
        with open(os.path.join(engine, R.STATE_NAME), 'wb') as handle:
            handle.write(corrupt)
        code, output, _ = run_tick(root)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED, outcome(root))
        self.assertIn('rotated to refresh_state.corrupt-', output)
        rotated = [name for name in os.listdir(engine)
                   if name.startswith('refresh_state.corrupt-')]
        self.assertEqual(1, len(rotated), rotated)
        self.assertEqual(corrupt, open(os.path.join(engine, rotated[0]), 'rb').read())
        self.assertEqual(50, len(read_ticks(root)[-1]['steps_run']))

    # =========================================================================================
    # T14  --dry-run and --status take no lock and change no byte
    # =========================================================================================
    def test_T14_read_only_modes_change_nothing(self):
        root = self.root()
        code, output, _ = run_tick(root)
        self.assertEqual(0, code, output[-2000:])
        before = tree_digest(root)
        code, dry, _ = run_tick(root, '--dry-run')
        self.assertEqual(0, code, dry)
        self.assertIn('DRY RUN (no locks, no writes, no launches)', dry)
        self.assertIn('eligible to publish: True', dry)
        self.assertEqual(before, tree_digest(root), '--dry-run changed the tree')
        code, status, _ = run_tick(root, '--status')
        self.assertEqual(0, code, status)
        self.assertEqual(R.PUBLISHED, json.loads(status)['last_tick']['outcome'])
        self.assertEqual(before, tree_digest(root), '--status changed the tree')
        self.assertFalse(os.path.exists(os.path.join(root, '.pipeline_run.lock')))
        # And a corrupt state is NOT rotated by a read-only mode either.
        with open(os.path.join(root, 'data', 'engine', R.STATE_NAME), 'wb') as handle:
            handle.write(b'not json')
        before = tree_digest(root)
        run_tick(root, '--dry-run')
        run_tick(root, '--status')
        self.assertEqual(before, tree_digest(root), 'a read-only mode rotated a corrupt state')

    # =========================================================================================
    # T15  the zero floor blocks; every other shrink is recorded and does not
    # =========================================================================================
    def test_T15_zero_floor_blocks_and_a_forty_percent_shrink_does_not(self):
        core = T.by_name()[CORE]
        key = stub_key(core)
        root = self.root()
        code, output, _ = run_tick(root)
        self.assertEqual(0, code, output[-2000:])
        write_control(root, {key: {'empty': True}})
        code, output, _ = run_tick(root, '--force-due', CORE)
        self.assertEqual(1, code, output[-3000:])
        entry = read_state(root)['steps'][CORE]
        self.assertEqual(S.FAILED, entry['state'])
        self.assertEqual(S.COVERAGE, entry['failure']['kind'])
        self.assertEqual(S.COVERAGE_SUSPECT, entry['coverage'])
        self.assertEqual(R.COLLECTED_NOT_PUBLISHED, outcome(root))
        blocking = [row for row in read_review(root) if row['blocking']]
        self.assertEqual(1, len(blocking), read_review(root))
        self.assertEqual(CORE, blocking[0]['step'])
        self.assertEqual(61, blocking[0]['was_rows'])
        # A coverage verdict is not a flaky socket: it is recorded ONCE, never blind-retried.
        self.assertEqual(2, invocations(root, core))
        self.assertEqual([], read_ticks(root)[-1]['filed'])
        # A 40% shrink is information, not a block.
        write_control(root, {key: {'rows': 36}})
        code, output, _ = run_tick(root, '--force-due', CORE)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED, outcome(root))
        entry = read_state(root)['steps'][CORE]
        self.assertEqual(S.OK, entry['state'])
        self.assertEqual(S.COVERAGE_OK, entry['coverage'])
        with open(os.path.join(root, 'data', 'engine', 'refresh_manifests',
                               '%s.%s.json' % (core.slug, entry['manifest_sha'])),
                  encoding='utf-8') as handle:
            manifest = json.load(handle)
        self.assertEqual(1, len(manifest['shrinks']), manifest.get('shrinks'))
        self.assertAlmostEqual(37 / 61.0, manifest['shrinks'][0]['ratio'], places=3)
        recorded = [row for row in read_review(root) if not row['blocking']]
        self.assertEqual(1, len(recorded))

    def test_T15b_a_file_taken_over_by_another_step_is_not_read_as_vanished(self):
        """Last writer owns.  A handover must never read as a collapsed output."""
        root = self.root()
        state = S.RefreshState(os.path.join(root, 'data', 'engine', R.STATE_NAME))
        tick = R.Tick(root, emit=lambda *a, **k: None, log=lambda line: None)
        tick.state = state
        # CREC owned a 200-row file last week; CHRG has since written it, so it is CHRG's now.
        shared = 'data/out/shared.csv'
        os.makedirs(os.path.join(root, 'data', 'out'), exist_ok=True)
        with open(os.path.join(root, 'data', 'out', 'shared.csv'), 'w', encoding='utf-8',
                  newline='\n') as handle:
            handle.write('id\n' + ''.join('r%d\n' % i for i in range(200)))
        described = S.describe_file(root, shared)
        self.assertIsNotNone(described)
        tick.manifests[CREC] = {'step': CREC, 'files': {shared: described}}
        tick.owner[shared] = CHRG
        previous_files = {relative: entry
                          for relative, entry in tick.manifests[CREC]['files'].items()
                          if tick.owner.get(relative) in (None, CREC)}
        self.assertEqual({}, previous_files, 'a taken-over file stayed in the baseline')
        zeros, _shrinks = S.coverage_review(previous_files, {})
        self.assertEqual([], zeros, 'a handover was read as a vanished output')
        # The same file genuinely gone, still owned, IS the zero floor.
        zeros, _shrinks = S.coverage_review({shared: described}, {})
        self.assertEqual(1, len(zeros))
        self.assertEqual('missing', zeros[0]['now'])

    def test_scan_spec_watches_the_four_shapes_and_nothing_else(self):
        """data/ and outputs/ recursively, the ROOT's own files, and the named external file."""
        root = self.root()
        write = lambda path, body=b'x': (os.makedirs(os.path.dirname(path), exist_ok=True),
                                         open(path, 'wb').write(body))
        write(os.path.join(root, 'data', 'engine', 'receipt.json'), b'{}')
        write(os.path.join(root, 'outputs', 'press_run_20260919', 'sealed.json'), b'{}')
        write(os.path.join(root, 'social_media.db'), b'sqlite')
        write(os.path.join(root, 'social_media.db-wal'), b'wal')
        write(os.path.join(root, 'press_text_cache.jsonl'), b'{}\n')
        write(os.path.join(root, 'ranks_python.json'), b'{}')
        write(os.path.join(root, 'data', 'stance', 'notes.md'), b'a data-tree file of any type')
        # Must NOT be watched: a subdirectory of ROOT that is not data/ or outputs/, a log, a
        # lock, a git object and a pycache entry -- and the foreign noise that would mark a
        # failing step TORN for something it never wrote: a human's note or script at the ROOT,
        # an open Office document's ``~$`` lock file, and SQLite's -wal/-shm/-journal siblings.
        write(os.path.join(root, 'fec', 'members', 'producer.py'), b'code')
        write(os.path.join(root, 'weekly_refresh.log'), b'noise')
        write(os.path.join(root, 'something.lock'), b'lock')
        write(os.path.join(root, 'data', '.git', 'objects', 'ab', 'cdef'), b'git')
        write(os.path.join(root, 'data', '__pycache__', 'x.pyc'), b'pyc')
        write(os.path.join(root, 'NOTES.md'), b'a human edits this at any hour')
        write(os.path.join(root, 'scratch_script.py'), b'print(1)')
        write(os.path.join(root, '~$Moneyball.xlsx'), b'office lock')
        write(os.path.join(root, 'data', 'engine', '~$review.xlsx'), b'office lock')
        write(os.path.join(root, 'social_media.db-shm'), b'shm')
        write(os.path.join(root, 'data', 'staffer_network', 'graph.db-journal'), b'journal')
        write(os.path.join(root, 'Moneyball.xlsx'), b'the workbook IS a producer output')
        external = os.path.abspath(os.path.join(root, os.pardir, 'Draft Systems',
                                                'all_press_release_index.csv'))
        recursive, top_level, extra = R.scan_spec(root)
        self.assertEqual((os.path.join(root, 'data'), os.path.join(root, 'outputs')), recursive)
        self.assertEqual((root,), top_level)
        self.assertEqual((external,), extra, 'the external file is not the press fold baseline')
        keys = set(S.snapshot(root, recursive=recursive, top_level=top_level,
                              extra_files=extra, exclude=R.runner_owned))
        for wanted in ('data/engine/receipt.json', 'outputs/press_run_20260919/sealed.json',
                       'social_media.db', 'press_text_cache.jsonl', 'ranks_python.json',
                       'Moneyball.xlsx', 'data/stance/notes.md'):
            self.assertIn(wanted, keys)
        for unwanted in ('fec/members/producer.py', 'weekly_refresh.log', 'something.lock',
                         'data/.git/objects/ab/cdef', 'data/__pycache__/x.pyc',
                         'NOTES.md', 'scratch_script.py', '~$Moneyball.xlsx',
                         'data/engine/~$review.xlsx', 'social_media.db-wal',
                         'social_media.db-shm', 'data/staffer_network/graph.db-journal'):
            self.assertNotIn(unwanted, keys, '%s must not count as a producer output' % unwanted)
        # An external file gets an ext: key that round-trips to its absolute path.
        os.makedirs(os.path.dirname(external), exist_ok=True)
        try:
            with open(external, 'wb') as handle:
                handle.write(b'url\n')
            keys = set(S.snapshot(root, recursive=(), top_level=(), extra_files=extra,
                                  exclude=R.runner_owned))
            self.assertEqual({S.EXTERNAL_PREFIX + external}, keys)
            self.assertEqual(external, S.resolve_key(root, S.EXTERNAL_PREFIX + external))
            self.assertIsNotNone(S.describe_file(root, S.EXTERNAL_PREFIX + external))
        finally:
            shutil.rmtree(os.path.dirname(external), ignore_errors=True)

    # =========================================================================================
    # T16  a hard gate failure stops the compute phase only
    # =========================================================================================
    def test_T16_hard_gate_failure_stops_the_phase_not_the_tick(self):
        intake = T.by_name()[INTAKE]
        root = self.root({stub_key(intake): {'always_fail': True}})
        code, output, _ = run_tick(root)
        self.assertEqual(1, code, output[-3000:])
        self.assertEqual(R.COMPUTE_FAILED, outcome(root), output[-3000:])
        record = read_ticks(root)[-1]
        self.assertEqual(INTAKE, record['compute_failure'])
        state = read_state(root)
        self.assertEqual(S.FAILED, state['steps'][INTAKE]['state'])
        for step in T.TABLE:
            if step.kind == T.DIAG:
                self.assertEqual(S.NEVER, state['steps'][step.name]['state'],
                                 'a diagnostic ran after the compute phase stopped')
                self.assertEqual(0, invocations(root, step))
        self.assertEqual(S.NEVER, state['steps']['accepted audio intake']['state'])
        # The TICK itself still finished cleanly: state, ledger and marker all present.
        self.assertEqual(R.PHASE_IDLE, read_status(root)['phase'])
        for step in T.evidence_steps():
            self.assertIn(state['steps'][step.name]['state'], (S.OK, S.PARTIAL))

    # =========================================================================================
    # T17  one failing source never prevents any other source from running
    # =========================================================================================
    def test_T17_a_failing_source_isolates(self):
        chrg = T.by_name()[CHRG]
        root = self.root({stub_key(chrg): {'always_fail': True}})
        code, output, _ = run_tick(root, '--backoff-minutes', '0')
        # CHANGED 2026-10-15: CHRG is an ENRICHMENT source that has never succeeded.  That used to
        # refuse the whole publication (exit 1); it is now a LABEL on a degraded publication.
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED_DEGRADED, outcome(root), output[-3000:])
        self.assertEqual([CHRG], read_state(root)['publication']['stale_sources'])
        never_ran = [step.name for step in T.TABLE
                     if step.kind in T.COLLECT_KINDS and step.name != CHRG
                     and invocations(root, step) == 0]
        self.assertEqual([], never_ran, 'one failing source stopped other collectors')
        state = read_state(root)
        for step in T.TABLE:
            if step.kind in T.COLLECT_KINDS and step.name != CHRG:
                self.assertIn(state['steps'][step.name]['state'], (S.OK, S.PARTIAL), step.name)
        self.assertEqual(3, invocations(root, chrg))

    # =========================================================================================
    # T18  the lease is polled, the wait is visible, and it acquires when the holder exits
    # =========================================================================================
    def test_T18_lease_wait_publishes_a_growing_wait_then_acquires(self):
        root = self.root()
        _peer, release = self.hold_lease(root)
        tick = R.Tick(root, lease_poll_seconds=0.05, lease_event_seconds=0.2,
                      lease_wait_hours=60.0 / 3600.0, emit=lambda *a, **k: None,
                      log=lambda line: None)
        tick.open_observation()
        tick.state.load()
        result = {}

        def acquire():
            result['ok'] = tick.acquire_lease()

        worker = threading.Thread(target=acquire)
        worker.start()
        try:
            waits, deadline = [], time.time() + 30
            while time.time() < deadline and len(waits) < 3:
                try:
                    status = read_status(root)
                except (OSError, ValueError):
                    time.sleep(0.02)
                    continue
                if status.get('phase') == R.PHASE_WAITING:
                    if not waits or status['waited_seconds'] != waits[-1]:
                        waits.append(status['waited_seconds'])
                time.sleep(0.02)
            self.assertGreaterEqual(len(waits), 3, waits)
            self.assertEqual(sorted(waits), waits, 'waited_seconds did not increase')
            self.assertLess(waits[0], waits[-1])
        finally:
            release()
            worker.join(timeout=60)
        self.assertTrue(result.get('ok'), 'the tick never acquired the lease the peer released')
        self.assertIsNotNone(tick._lease)
        tick.release_lease()
        self.assertFalse(os.path.exists(os.path.join(root, '.pipeline_run.lock')))

    def test_T18b_exhausted_lease_wait_is_blocked_not_a_crash(self):
        root = self.root()
        _peer, release = self.hold_lease(root)
        try:
            tick = R.Tick(root, lease_poll_seconds=0.01, lease_event_seconds=0.05,
                          lease_wait_hours=0.05 / 3600.0, emit=lambda *a, **k: None,
                          log=lambda line: None)
            tick.open_observation()
            tick.state.load()
            self.assertFalse(tick.acquire_lease())
            self.assertEqual('writer lease', tick.blockers[0][0])
        finally:
            release()

    # =========================================================================================
    # T19  a partial exit is committed, satisfies requires, and is never retried
    # =========================================================================================
    def test_T19a_partial_exit_is_committed_and_not_retried(self):
        press = T.by_name()[PRESS_DELTA]
        self.assertEqual((3,), press.partial_codes)
        root = self.root({stub_key(press): {'always_fail': True, 'exit': 3,
                                            'write_then_fail': True}})
        code, output, _ = run_tick(root)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED, outcome(root), output[-3000:])
        state = read_state(root)
        entry = state['steps'][PRESS_DELTA]
        self.assertEqual(S.PARTIAL, entry['state'])
        self.assertTrue(entry['last_ok_utc'])
        self.assertTrue(entry['manifest_sha'])
        self.assertFalse(entry['torn'])
        self.assertEqual(1, invocations(root, press), 'a PARTIAL was retried')
        # It satisfies requires: the whole press chain behind it ran.
        for name in ('press intake audit', 'press engine feed', 'fold promoted press runs'):
            self.assertEqual(S.OK, state['steps'][name]['state'], name)
        record = read_ticks(root)[-1]
        self.assertEqual([PRESS_DELTA], record['partial_steps'])
        self.assertEqual([PRESS_DELTA], state['publication']['partial_steps'])
        self.assertEqual([PRESS_DELTA], read_status(root)['last_tick']['partial_steps'])
        self.assertIn('PARTIAL', output)

    def test_T19b_the_same_code_without_a_partial_code_is_a_plain_failure(self):
        chrg = T.by_name()[CHRG]
        self.assertEqual((), chrg.partial_codes)
        root = self.root({stub_key(chrg): {'always_fail': True, 'exit': 3}})
        code, output, _ = run_tick(root, '--backoff-minutes', '0')
        # CHANGED 2026-10-15: an ENRICHMENT source that fails and never succeeded degrades the
        # publication with a label; it no longer refuses it (exit 1).
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED_DEGRADED, outcome(root), output[-3000:])
        entry = read_state(root)['steps'][CHRG]
        self.assertEqual(S.FAILED, entry['state'])
        self.assertEqual(S.EXIT, entry['failure']['kind'])
        self.assertEqual(3, entry['failure']['exit'])
        self.assertEqual(3, invocations(root, chrg), 'a plain failure was not retried twice')
        self.assertEqual([], read_ticks(root)[-1]['partial_steps'])

    # =========================================================================================
    # T20  HIGH-1: the zero floor follows what the STEP ITSELF last produced, across a hand-over
    #
    # "Last writer owns" strips a file from a step's manifest the moment a later step rewrites
    # it, so a baseline built from ownership alone goes BLIND on exactly the files two steps
    # share.  The sandbox drove that: a collector's 60 rows became 0, nothing noticed, and the
    # tick published it as clean.  ``produced`` in the manifest survives the hand-over.
    # =========================================================================================
    def _hand_over_root(self, second_run):
        """Two collect steps writing ONE file: CORE first, CORE_B (a LATER row) last."""
        core, core_b = T.by_name()[CORE], T.by_name()[CORE_B]
        control = {stub_key(core): dict(second_run, out='shared.csv'),
                   stub_key(core_b): {'out': 'shared.csv', 'rows': 80}}
        root = self.root(control)
        seed_state(root)
        return root, core, core_b

    def _establish_hand_over(self, root, core, core_b):
        """Tick 1: A writes 60 rows, B rewrites the same file, so B owns it.  Returns A's
        manifest.  ``--skip-compute`` because this tick only has to leave the two manifests."""
        code, output, _ = run_tick(root, '--skip-compute', '--force-due', CORE,
                                   '--force-due', CORE_B)
        self.assertEqual(1, code, output[-3000:])       # --skip-compute never publishes
        state = read_state(root)
        self.assertEqual(S.OK, state['steps'][CORE]['state'], output[-3000:])
        self.assertEqual(S.OK, state['steps'][CORE_B]['state'], output[-3000:])
        manifest_a = read_manifest_of(root, core, state['steps'][CORE]['manifest_sha'])
        manifest_b = read_manifest_of(root, core_b, state['steps'][CORE_B]['manifest_sha'])
        self.assertNotIn(SHARED, manifest_a['files'],
                         'the LATER writer did not take the file over, so there is no hand-over '
                         'to test')
        self.assertIn(SHARED, manifest_b['files'])
        # 60 data rows plus the header line: a CSV's row count includes its header.
        self.assertEqual(61, manifest_a['produced'][SHARED])
        return manifest_a

    def test_T20a_a_handed_over_file_collapsing_to_zero_still_blocks(self):
        root, core, core_b = self._hand_over_root({'empty': [False, True]})
        manifest_a = self._establish_hand_over(root, core, core_b)

        code, output, _ = run_tick(root, '--force-due', CORE, '--force-due', CORE_B)
        self.assertEqual(1, code, output[-3000:])
        self.assertEqual(R.COLLECTED_NOT_PUBLISHED, outcome(root), output[-3000:])
        entry = read_state(root)['steps'][CORE]
        self.assertEqual(S.FAILED, entry['state'])
        self.assertEqual(S.COVERAGE, entry['failure']['kind'])
        self.assertEqual(S.COVERAGE_SUSPECT, entry['coverage'])
        blocking = [row for row in read_review(root) if row['blocking']]
        self.assertEqual(1, len(blocking), read_review(root))
        self.assertEqual(CORE, blocking[0]['step'])
        self.assertEqual(SHARED, blocking[0]['file'])
        self.assertEqual(61, blocking[0]['was_rows'])
        self.assertIsNone(read_state(root)['publication'],
                          'a source that collapsed to zero rows was published anyway')

        # NEGATIVE CONTROL.  The rule this fix replaced, reproduced against THESE manifests and
        # the values the blocked run actually measured: the old baseline was "what the step still
        # owns", and the hand-over had already emptied it, so the old rule finds no zero at all.
        now = {SHARED: {'size': blocking[0]['now_size'], 'rows': blocking[0]['now_rows']}}
        old_zeros, _shrinks = S.coverage_review(manifest_a['files'], now)
        self.assertEqual([], old_zeros,
                         'the pre-fix rule already caught this, so the control is dead and the '
                         'test proves nothing')
        new_baseline = dict(manifest_a['files'])
        new_baseline[SHARED] = {'rows': manifest_a['produced'][SHARED], 'size': None}
        new_zeros, _shrinks = S.coverage_review(new_baseline, now)
        self.assertEqual(1, len(new_zeros), new_zeros)
        self.assertEqual(61, new_zeros[0]['was_rows'])

    def test_T20b_a_handed_over_file_that_merely_shrinks_does_not_block(self):
        """NEGATIVE CONTROL for T20a: the same hand-over, 55 rows instead of none, publishes."""
        root, core, core_b = self._hand_over_root({'rows': [60, 55]})
        self._establish_hand_over(root, core, core_b)
        code, output, _ = run_tick(root, '--force-due', CORE, '--force-due', CORE_B)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED, outcome(root), output[-3000:])
        state = read_state(root)
        self.assertEqual(S.OK, state['steps'][CORE]['state'])
        self.assertEqual(S.COVERAGE_OK, state['steps'][CORE]['coverage'])
        self.assertEqual([], [row for row in read_review(root) if row['blocking']],
                         'a 10% shrink through a hand-over was read as a collapse')
        self.assertIsNotNone(state['publication'])

    # =========================================================================================
    # T21  HIGH-2: "is there new evidence?" is clock-free
    #
    # This laptop's standby and time sync step the clock backwards.  The sandbox found that one
    # backward step made fresh evidence carry an OLDER stamp than the last publication, so every
    # later tick exited 0 NOTHING_DUE over data nobody had computed -- permanently.  The clock
    # cannot be moved in a test, so the same relation is built with a publication stamped in the
    # FUTURE: every source's stamp is older than it, exactly as after a backward step.
    # =========================================================================================
    @staticmethod
    def _three_hours_ahead():
        return S.stamp(S.utcnow() + datetime.timedelta(hours=3))

    def test_T21a_evidence_collected_in_this_tick_always_counts(self):
        root = self.root()
        future = self._three_hours_ahead()
        seed_published(root, published_utc=future)
        code, output, _ = run_tick(root, '--force-due', CREC)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED, outcome(root), output[-3000:])
        self.assertIn('new evidence this tick: %s' % CREC, output)
        state = read_state(root)
        # The defect condition, confirmed after the fact: the evidence this tick collected still
        # carries a stamp EARLIER than the publication it has to supersede, so the stamp rule
        # would have said "nothing is due" and exited 0 over uncomputed data.
        self.assertLess(S.parse_stamp(state['steps'][CREC]['last_ok_utc']),
                        S.parse_stamp(future))
        publication = state['publication']
        self.assertEqual(sorted(EVIDENCE_NAMES), sorted(publication['inputs']))
        for name, record in publication['inputs'].items():
            self.assertIsInstance(record['generation'], int, name)
            self.assertGreaterEqual(record['generation'], 1, name)
        self.assertEqual(2, publication['inputs'][CREC]['generation'])

    def test_T21b_new_evidence_from_an_earlier_tick_still_wants_compute(self):
        root = self.root()
        future = self._three_hours_ahead()
        seed_published(root, published_utc=future)
        code, output, _ = run_tick(root, '--force-due', CREC, '--skip-compute')
        self.assertEqual(1, code, output[-3000:])
        entry = read_state(root)['steps'][CREC]
        self.assertEqual(2, entry['generation'], 'the collector did not advance its generation')
        self.assertLess(S.parse_stamp(entry['last_ok_utc']), S.parse_stamp(future),
                        'the control needs the fresh evidence to carry the OLDER stamp')
        # The next tick has nothing due and nothing in ``succeeded``: the ONLY thing that can
        # see the new data is the generation the publication recorded.
        code, output, _ = run_tick(root)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED, outcome(root), output[-3000:])
        self.assertIn('new evidence since the last publication: %s' % CREC, output)
        self.assertEqual(1, invocations(root, T.by_name()[CREC]),
                         'the second tick re-ran a collector that was not due')
        self.assertEqual(2, read_state(root)['publication']['inputs'][CREC]['generation'])

    def test_T21c_equal_generations_with_nothing_due_is_nothing_due(self):
        """CONTROL for T21a/b: the clock-free rule must still exit 0 over a quiet system."""
        root = self.root()
        seed_published(root)
        code, output, _ = run_tick(root)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.NOTHING_DUE, outcome(root), output[-3000:])
        self.assertEqual([], read_ticks(root)[-1]['steps_run'])
        self.assertEqual(0, invocations(root, T.by_name()[CREC]))

    def test_T21d_a_publication_without_generations_still_uses_the_stamp_rule(self):
        """A record written before generations were recorded must not read as "no evidence"."""
        root = self.root()
        published = S.stamp(S.utcnow() - datetime.timedelta(hours=6))
        # Sources refreshed one hour ago, a legacy publication six hours old: new evidence.
        seed_published(root, published_utc=published, legacy=True, evidence_age_h=1.0)
        code, dry, _ = run_tick(root, '--dry-run')
        self.assertEqual(0, code, dry)
        self.assertIn('compute wanted: True (new evidence since the last publication: %s' % CREC,
                      dry)
        # The same legacy record with every source OLDER than it: nothing new, by the same rule.
        seed_published(root, published_utc=published, legacy=True, evidence_age_h=9.0)
        code, dry, _ = run_tick(root, '--dry-run')
        self.assertEqual(0, code, dry)
        self.assertIn('compute wanted: False', dry)
        self.assertNotIn('compute wanted: True', dry)

    # =========================================================================================
    # T22  HIGH-3: an inherited lease claim that does not validate is fatal before step 1
    #
    # A stale token left in the environment named a lease file nobody held.  Nothing a poll can
    # wait for changes that, so the tick spent its whole six-hour wait on it -- holding the
    # single-instance lock, so every other fire exited 75 too.
    # =========================================================================================
    def test_T22a_a_stale_inherited_lease_claim_is_fatal_before_step_one(self):
        root = self.root()
        dead = subprocess.Popen([sys.executable, '-c', 'pass'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        dead.wait(timeout=30)
        code, output, seconds = run_tick(
            root, timeout=180,
            **{pipeline_lock.LOCK_TOKEN_ENV: 'not-a-token-any-lease-ever-had',
               pipeline_lock.LOCK_OWNER_PID_ENV: str(dead.pid)})
        self.assertEqual(1, code, output[-3000:])
        self.assertEqual(R.FATAL_PRESTEP, outcome(root), output[-3000:])
        # The default wait is SIX HOURS of 60-second polls for a lease that can never appear.
        self.assertLess(seconds, 10,
                        'the tick polled for a lease nobody holds (%.1fs)' % seconds)
        self.assertIn(pipeline_lock.LOCK_TOKEN_ENV, output, 'the log does not name the variable')
        self.assertIn(pipeline_lock.LOCK_OWNER_PID_ENV, output)
        self.assertEqual([], read_ticks(root)[-1]['steps_run'])
        self.assertEqual(0, invocations(root, T.by_name()[CREC]))
        self.assertFalse(os.path.exists(os.path.join(root, '.pipeline_run.lock')),
                         'a tick that never validated a lease left one behind')

    def test_T22b_an_adopted_lease_still_works_and_is_never_deleted_by_the_child(self):
        """POSITIVE CONTROL for T22a: a REAL handoff from this process still adopts and runs."""
        root = self.root()
        seed_published(root)            # nothing is due: what is under test is the lease
        lock_path = os.path.join(root, '.pipeline_run.lock')
        for name in (pipeline_lock.LOCK_TOKEN_ENV, pipeline_lock.LOCK_OWNER_PID_ENV):
            os.environ.pop(name, None)
        with pipeline_lock.pipeline_run_lock(lock_path, owner_kind='test_parent') as lease:
            handoff = pipeline_lock.child_lock_environment(lease)
            code, output, _ = run_tick(root, timeout=300, **handoff)
            self.assertEqual(0, code, output[-3000:])
            self.assertEqual(R.NOTHING_DUE, outcome(root), output[-3000:])
            self.assertTrue(os.path.exists(lock_path),
                            'the child DELETED a lease it had only adopted')
            with open(lock_path, encoding='utf-8') as handle:
                body = json.load(handle)
            self.assertEqual(lease['token'], body['token'])
            self.assertEqual(os.getpid(), body['pid'], 'the child took the lease over')
        self.assertFalse(os.path.exists(lock_path))

    # =========================================================================================
    # T23  MEDIUM-4: one locked manifest file must not erase a step's recorded success
    # =========================================================================================
    def test_T23a_read_manifest_separates_a_verdict_from_a_lock(self):
        root = self.root()
        engine = os.path.join(root, 'data', 'engine')

        payload, problem, transient = S.read_manifest(os.path.join(engine, 'no-such.json'))
        self.assertIsNone(payload)
        self.assertEqual('missing', problem)
        self.assertFalse(transient)

        corrupt = os.path.join(engine, 'corrupt.json')
        with open(corrupt, 'wb') as handle:
            handle.write(b'{"files": {  <- not json at all\n')
        payload, problem, transient = S.read_manifest(corrupt)
        self.assertIsNone(payload)
        self.assertIn('JSONDecodeError', problem)
        self.assertFalse(transient)

        not_a_manifest = os.path.join(engine, 'not_a_manifest.json')
        S.write_json_atomic(not_a_manifest, {'step': CREC, 'generation': 4})
        payload, problem, transient = S.read_manifest(not_a_manifest)
        self.assertIsNone(payload)
        self.assertEqual('not a manifest document', problem)
        self.assertFalse(transient)

        # Cannot be OPENED.  On Windows a DIRECTORY at the path raises PermissionError, the same
        # shape as the sharing violation OneDrive and the antivirus scanner produce.
        locked = os.path.join(engine, 'locked.json')
        os.makedirs(locked, exist_ok=True)
        pauses = []
        payload, problem, transient = S.read_manifest(locked, attempts=4, sleep=pauses.append)
        self.assertIsNone(payload)
        self.assertTrue(transient, problem)
        self.assertTrue(problem)
        # FOUR tries, so three waits between them: the last attempt is not followed by a wait.
        self.assertEqual(3, len(pauses), pauses)

    def test_T23b_a_missing_manifest_demotes_the_step_and_runs_it_in_the_same_tick(self):
        crec = T.by_name()[CREC]
        root = self.root()
        five_minutes_ago = S.stamp(S.utcnow() - datetime.timedelta(minutes=5))
        seed_state(root, attempt_age_h=5.0 / 60.0,
                   steps={CREC: {'state': S.OK, 'generation': 3,
                                 'manifest_sha': 'deadbeefcafe',
                                 'last_ok_utc': S.stamp(S.utcnow()
                                                        - datetime.timedelta(hours=1)),
                                 'last_attempt_utc': five_minutes_ago}})
        code, output, _ = run_tick(root)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED, outcome(root), output[-3000:])
        self.assertIn('manifest deadbeefcafe missing', output)
        self.assertIn('unverified and due now', output)
        # The one-hour attempt gap does NOT get to postpone it.  Without "due now" the step is
        # demoted to NEVER and then refused for having been attempted five minutes ago, so the
        # tick spends itself and publishes nothing -- a wasted fire, every fire.
        self.assertEqual(1, invocations(root, crec), output[-3000:])
        entry = read_state(root)['steps'][CREC]
        self.assertEqual(S.OK, entry['state'])
        self.assertTrue(entry['manifest_sha'])
        self.assertNotEqual('deadbeefcafe', entry['manifest_sha'])

    def test_T23c_a_manifest_that_cannot_be_opened_does_not_erase_the_step(self):
        """A CORE source's unreadable manifest still blocks (D-212); an ENRICHMENT one is a
        label, pinned in test_refresh_degrade.py."""
        core = T.by_name()[CORE]
        root = self.root()
        sha, last_ok = 'abc123abc123', S.stamp(S.utcnow() - datetime.timedelta(hours=1))
        seed_published(root, steps={CORE: {'state': S.OK, 'generation': 1, 'manifest_sha': sha,
                                           'last_ok_utc': last_ok,
                                           'last_attempt_utc': last_ok}})
        # A DIRECTORY where the manifest belongs: it exists, and it cannot be opened.
        os.makedirs(os.path.join(root, 'data', 'engine', 'refresh_manifests',
                                 '%s.%s.json' % (core.slug, sha)), exist_ok=True)
        code, output, _ = run_tick(root)
        self.assertEqual(1, code, output[-3000:])
        self.assertEqual(R.COLLECTED_NOT_PUBLISHED, outcome(root), output[-3000:])
        self.assertIn('could not be opened', output)
        entry = read_state(root)['steps'][CORE]
        self.assertEqual(S.OK, entry['state'],
                         'one file this workstation held open erased a recorded success')
        self.assertEqual(last_ok, entry['last_ok_utc'])
        self.assertEqual(sha, entry['manifest_sha'])
        self.assertEqual(0, invocations(root, core))

    # =========================================================================================
    # T24  MEDIUM-5: no step may eat the whole tick, and a step that HUNG is not retried into a
    #      second hang.  Driven directly on a constructed Tick: a real 36 h timeout is not a test.
    # =========================================================================================
    def test_T24a_no_step_may_spend_more_than_half_a_tick(self):
        root = self.root()
        press, short = T.by_name()[PRESS_DELTA], T.by_name()[MTP]
        self.assertEqual(129600, press.timeout_s)      # 36 h against an 18 h tick
        self.assertEqual(1200, short.timeout_s)
        tick = R.Tick(root, budget_hours=18.0, emit=lambda *a, **k: None, log=lambda line: None)
        self.assertEqual(0.5, R.MAX_STEP_BUDGET_FRACTION)
        self.assertEqual(32400, tick.step_timeout(press), 'the press walk can still eat the tick')
        self.assertEqual(1200, tick.step_timeout(short), 'a short row lost its own bound')
        # What is LEFT still caps it below the share.
        tick.deadline = time.monotonic() + 600
        self.assertAlmostEqual(600, tick.step_timeout(press), delta=2)
        tiny = R.Tick(root, budget_hours=0.001, emit=lambda *a, **k: None, log=lambda line: None)
        self.assertEqual(60, R.MIN_STEP_TIMEOUT_SECONDS)
        self.assertEqual(R.MIN_STEP_TIMEOUT_SECONDS, tiny.step_timeout(short))

    def test_T24b_a_step_that_hung_is_not_retried_into_a_second_hang(self):
        root = self.root()
        lines, ran = [], []
        tick = R.Tick(root, emit=lambda *a, **k: None, log=lines.append)
        tick.state.load()
        tick.run_step = lambda step, phase: ran.append((step.name, phase)) or True

        def file_a(kind, burned):
            lines[:] = []
            del ran[:]
            tick.state.record(CREC, state=S.FAILED, duration_s=burned,
                              failure={'kind': kind, 'exit': None, 'detail': 'x'})
            tick.filed = [CREC]

        file_a(S.TIMEOUT, 100.0)
        tick.deadline = time.monotonic() + 150          # less than 2 x the 100 s it burned
        tick.retry_pass()
        self.assertEqual([], ran, 'a hung step was retried into a second hang')
        self.assertTrue(any('NOT RETRIED' in line for line in lines), lines)

        file_a(S.TIMEOUT, 100.0)
        tick.deadline = time.monotonic() + 500          # room for a second hang: retry it
        tick.retry_pass()
        self.assertEqual([(CREC, R.PHASE_RETRY)], ran, lines)

        # And the rule is about a HANG, not about failure in general: a refusal is still retried
        # on the same budget that refused the timeout.
        file_a(S.EXIT, 100.0)
        tick.deadline = time.monotonic() + 150
        tick.retry_pass()
        self.assertEqual([(CREC, R.PHASE_RETRY)], ran,
                         'the hang rule fired on a failure that was not a hang')

    # =========================================================================================
    # T25  MEDIUM-6: the runner imports NO producer, and the one restated path cannot drift
    # =========================================================================================
    def test_T25a_the_press_baseline_path_is_pinned_to_the_press_lane(self):
        """The fold's own constant, read as TEXT.

        The runner RESTATES this path instead of importing ``collect.press_baseline_fold``,
        because that module imports ``build_press_engine_feed`` -- step 21's own producer -- and
        ``--dry-run``/``--status`` promise to change no byte.  This suite makes the same promise
        (see the module docstring), so the fold's constant is parsed rather than imported: that
        pins the anchor AND the filename without executing one line of the press lane.
        """
        source_path = os.path.join(FEC, 'collect', 'press_baseline_fold.py')
        with open(source_path, encoding='utf-8') as handle:
            tree = ast.parse(handle.read(), filename=source_path)
        assigned = [node for node in ast.walk(tree)
                    if isinstance(node, ast.Assign)
                    and any(getattr(target, 'id', None) == 'DEFAULT_BASELINE'
                            for target in node.targets)]
        self.assertEqual(1, len(assigned), 'press_baseline_fold.DEFAULT_BASELINE moved')
        call = assigned[0].value
        self.assertIsInstance(call, ast.Call)
        self.assertEqual('join', getattr(call.func, 'attr', None))
        anchors = {'DRAFT': paths.DRAFT, 'ROOT': paths.ROOT}
        parts = []
        for argument in call.args:
            if isinstance(argument, ast.Name):
                self.assertIn(argument.id, anchors,
                              'the fold now anchors its baseline on %r' % argument.id)
                parts.append(anchors[argument.id])
            else:
                self.assertIsInstance(argument, ast.Constant, ast.dump(argument))
                parts.append(argument.value)
        self.assertEqual(os.path.join(paths.DRAFT, R.PRESS_BASELINE_NAME), os.path.join(*parts))

    def test_T25b_the_runner_imports_no_producer_in_any_mode(self):
        banned = ('collect', 'legislation', 'stance', 'surfaces')
        allowed = {'surfaces.glassbox_emit'}
        seen = 0
        for name in ('refresh_tick.py', 'refresh_state.py'):
            path = os.path.join(FEC, name)
            with open(path, encoding='utf-8') as handle:
                tree = ast.parse(handle.read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or '']
                else:
                    if isinstance(node, ast.Name) and node.id in ('__import__', 'importlib'):
                        self.fail('%s:%d reaches for %s' % (name, node.lineno, node.id))
                    continue
                for module in modules:
                    seen += 1
                    if module in allowed:
                        continue
                    self.assertNotIn(module.split('.')[0], banned,
                                     '%s:%d imports the producer lane %r'
                                     % (name, node.lineno, module))
                    self.assertNotEqual('importlib', module.split('.')[0],
                                        '%s:%d imports importlib' % (name, node.lineno))
        self.assertGreater(seen, 10, 'the scan found almost no imports; it is not working')

    # =========================================================================================
    # T26  LOW-7: the review file is capped, and the OLDEST information rows go first
    # =========================================================================================
    def test_T26_the_review_file_is_capped_and_blocking_rows_survive_the_trim(self):
        root = self.root()
        path = os.path.join(root, 'data', 'engine', R.REVIEW_NAME)
        self.assertEqual(2000, S.REVIEW_MAX_ENTRIES)
        seeded = ([{'step': CREC, 'blocking': True, 'reason': 'zero floor', 'seq': index}
                   for index in range(5)]
                  + [{'step': CHRG, 'blocking': False, 'reason': 'shrink recorded', 'seq': index}
                     for index in range(2100)])
        S.write_json_atomic(path, seeded)
        self.assertEqual(3, S.append_review(path, [{'step': CREC, 'blocking': False,
                                                    'reason': 'shrink recorded',
                                                    'seq': 9000 + index}
                                                   for index in range(3)]))
        rows = read_review(root)
        self.assertEqual(S.REVIEW_MAX_ENTRIES, len(rows))
        self.assertEqual(5, len([row for row in rows if row['blocking']]),
                         'the trim dropped a blocking row while information rows survived')
        self.assertEqual([9000, 9001, 9002], [row['seq'] for row in rows[-3:]])
        surviving = sorted(row['seq'] for row in rows if not row['blocking'])
        self.assertEqual(108, surviving[0], 'the OLDEST information rows were not the ones to go')
        # A file of nothing but blocking rows is still capped, oldest first.
        S.write_json_atomic(path, [{'step': CREC, 'blocking': True, 'seq': index}
                                   for index in range(2100)])
        S.append_review(path, [{'step': CREC, 'blocking': True, 'seq': 9000 + index}
                               for index in range(3)])
        rows = read_review(root)
        self.assertEqual(S.REVIEW_MAX_ENTRIES, len(rows))
        self.assertEqual([9000, 9001, 9002], [row['seq'] for row in rows[-3:]])
        self.assertEqual(103, rows[0]['seq'])

    # =========================================================================================
    # T27  --accept-zero: the owner's standing answer for a source that has truly gone quiet
    #      (without it, the same collapse blocks -- that control is T15, not repeated here)
    # =========================================================================================
    def test_T27_accept_zero_records_the_collapse_instead_of_blocking(self):
        core = T.by_name()[CORE]
        root = self.root({stub_key(core): {'empty': [False, True, True]}})
        seed_state(root)
        code, output, _ = run_tick(root, '--skip-compute', '--force-due', CORE)
        self.assertEqual(1, code, output[-3000:])
        self.assertEqual(S.OK, read_state(root)['steps'][CORE]['state'], output[-3000:])

        code, output, _ = run_tick(root, '--accept-zero', CORE)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED, outcome(root), output[-3000:])
        # The flag implies the step is due: its last success is minutes old against a weekly
        # cadence, so nothing else in the runner could have run it.
        self.assertEqual(2, invocations(root, core), 'the acceptance did not make the step due')
        entry = read_state(root)['steps'][CORE]
        self.assertEqual(S.OK, entry['state'])
        self.assertEqual(S.COVERAGE_OK, entry['coverage'])
        self.assertIn('ZERO FLOOR ACCEPTED BY THE OWNER', output)
        rows = read_review(root)
        self.assertEqual([], [row for row in rows if row['blocking']], rows)
        accepted = [row for row in rows if 'ACCEPTED' in (row.get('reason') or '')]
        self.assertEqual(1, len(accepted), rows)
        self.assertEqual(CORE, accepted[0]['step'])
        self.assertEqual(61, accepted[0]['was_rows'])
        self.assertFalse(accepted[0]['blocking'])

        # The accepted run IS the new baseline: a PLAIN tick over the same empty output, forced
        # due, is not blocked either -- the acceptance was not a one-tick suppression that hands
        # Paul the same finding again next week.
        code, output, _ = run_tick(root, '--force-due', CORE)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED, outcome(root), output[-3000:])
        self.assertEqual(S.OK, read_state(root)['steps'][CORE]['state'])
        self.assertEqual([], [row for row in read_review(root) if row['blocking']])

    # =========================================================================================
    # T28  a CSV that comes back HEADER-ONLY is a collapse: the header is a row
    # =========================================================================================
    def test_T28a_a_csv_that_comes_back_header_only_is_a_collapse(self):
        core = T.by_name()[CORE]
        root = self.root({stub_key(core): {'rows': [60, 0]}})
        seed_state(root)
        code, output, _ = run_tick(root, '--skip-compute', '--force-due', CORE)
        self.assertEqual(1, code, output[-3000:])
        self.assertEqual(S.OK, read_state(root)['steps'][CORE]['state'], output[-3000:])
        code, output, _ = run_tick(root, '--force-due', CORE)
        self.assertEqual(1, code, output[-3000:])
        self.assertEqual(R.COLLECTED_NOT_PUBLISHED, outcome(root), output[-3000:])
        entry = read_state(root)['steps'][CORE]
        self.assertEqual(S.FAILED, entry['state'])
        self.assertEqual(S.COVERAGE, entry['failure']['kind'])
        self.assertEqual(S.COVERAGE_SUSPECT, entry['coverage'])
        blocking = [row for row in read_review(root) if row['blocking']]
        self.assertEqual(1, len(blocking), read_review(root))
        self.assertEqual(61, blocking[0]['was_rows'])
        # NEGATIVE CONTROL: the rule before this fix asked for zero ROWS or zero BYTES, and this
        # file has one row and nine bytes.  It is the commonest plausible zero in this repo -- a
        # collector that found nothing and still wrote its header.
        self.assertEqual(1, blocking[0]['now_rows'])
        self.assertGreater(blocking[0]['now_size'], 0)

    def test_T28b_the_header_floor_is_for_csv_only_and_needs_a_real_prior(self):
        before = {'data/out/a.csv': {'rows': 61, 'size': 900},
                  'data/out/b.jsonl': {'rows': 60, 'size': 900},
                  'data/out/small.csv': {'rows': 10, 'size': 90}}
        after = {'data/out/a.csv': {'rows': 1, 'size': 9},
                 'data/out/b.jsonl': {'rows': 1, 'size': 30},
                 'data/out/small.csv': {'rows': 1, 'size': 9}}
        zeros, shrinks = S.coverage_review(before, after)
        self.assertEqual(['data/out/a.csv'], [zero['file'] for zero in zeros])
        self.assertEqual(['data/out/b.jsonl', 'data/out/small.csv'],
                         sorted(shrink['file'] for shrink in shrinks))
        # A .jsonl with ONE line is a record, not a header; only no lines at all is a collapse.
        zeros, _shrinks = S.coverage_review({'data/out/b.jsonl': {'rows': 60, 'size': 900}},
                                            {'data/out/b.jsonl': {'rows': 0, 'size': 0}})
        self.assertEqual(1, len(zeros))

    # =========================================================================================
    # the amendment's other rule, and the chain gate
    # =========================================================================================
    def test_missing_producer_fails_one_step_not_the_tick(self):
        root = self.root(skip_producers=('press_zero_row_review.py',))
        code, output, _ = run_tick(root)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED, outcome(root), output[-3000:])
        entry = read_state(root)['steps'][ZERO_ROW_REVIEW]
        self.assertEqual(S.FAILED, entry['state'])
        self.assertIn('producer script not found', entry['failure']['detail'])
        self.assertIn('table producer(s) not on disk', output)
        self.assertNotIn(R.FATAL_PRESTEP, output)
        self.assertEqual([ZERO_ROW_REVIEW], read_ticks(root)[-1]['filed'])

    def test_duplicate_producer_is_fatal_before_step_one(self):
        root = self.root()
        duplicate = os.path.join(root, 'fec', 'legislation', 'big5_traits.py')
        shutil.copyfile(os.path.join(root, 'fec', 'big5_traits.py'), duplicate)
        code, output, _ = run_tick(root)
        self.assertEqual(1, code, output[-3000:])
        self.assertEqual(R.FATAL_PRESTEP, outcome(root))
        self.assertIn('duplicate producer basename big5_traits.py', output)
        self.assertEqual(0, invocations(root, T.by_name()[CREC]))

    def test_chain_gate_tolerates_ratified_dissent_and_refuses_novel(self):
        chain = T.by_name()[R.CHAIN_STEP]
        root = self.root({stub_key(chain): {'always_fail': True, 'stdout': GATED_AUDIT_TAIL}})
        code, output, _ = run_tick(root)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED, outcome(root), output[-3000:])
        self.assertEqual(S.OK, read_state(root)['steps'][R.CHAIN_STEP]['state'])
        root = self.root({stub_key(chain): {'always_fail': True, 'stdout': NOVEL_AUDIT_TAIL}})
        code, output, _ = run_tick(root)
        self.assertEqual(1, code, output[-3000:])
        self.assertEqual(R.COMPUTE_FAILED, outcome(root))
        self.assertIn('NOVEL', output)

    def test_chain_gate_refuses_when_the_chain_tail_is_no_longer_audit_full(self):
        chain = T.by_name()[R.CHAIN_STEP]
        root = self.root({stub_key(chain): {'always_fail': True, 'stdout': GATED_AUDIT_TAIL}})
        path = os.path.join(root, 'fec', 'run_pipeline.py')
        with open(path, encoding='utf-8') as handle:
            source = handle.read()
        with open(path, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(source.replace("    ('audit_full.py', []),\n",
                                        "    ('audit_full.py', []),\n"
                                        "    ('something_after_the_audit.py', []),\n"))
        code, output, _ = run_tick(root)
        self.assertEqual(1, code, output[-3000:])
        self.assertEqual(R.COMPUTE_FAILED, outcome(root))
        self.assertIn('chain tail is no longer audit_full.py', output)

    def test_second_instance_leaves_the_first_instances_marker_alone(self):
        crec = T.by_name()[CREC]
        root = self.root({stub_key(crec): {'sleep': 6}})
        first = start_tick(root)
        deadline, collecting = time.time() + 60, False
        while time.time() < deadline and not collecting:
            try:
                collecting = read_status(root).get('phase') == R.PHASE_COLLECT
            except (OSError, ValueError):
                pass
            if not collecting:
                time.sleep(0.05)
        self.assertTrue(collecting, 'the first tick never reached the collect phase')
        before = read_status(root)
        code, output, _ = run_tick(root, timeout=120)
        after = read_status(root)
        self.assertEqual(75, code, output[-2000:])
        self.assertIn('another refresh tick already holds the single-instance lock', output)
        self.assertEqual(before['pid'], after['pid'],
                         'the second instance overwrote the running instance marker')
        first.wait(timeout=300)
        drain(first)
        self.assertEqual(R.PUBLISHED, outcome(root))

    def test_nothing_due_after_a_publication_exits_zero_without_running_anything(self):
        root = self.root()
        code, output, _ = run_tick(root)
        self.assertEqual(0, code, output[-2000:])
        code, output, _ = run_tick(root)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.NOTHING_DUE, outcome(root), output[-3000:])
        self.assertEqual([], read_ticks(root)[-1]['steps_run'])
        self.assertEqual(1, invocations(root, T.by_name()[CREC]))

    def test_glassbox_events_reach_the_stream(self):
        root = self.root()
        code, output, _ = run_tick(root)
        self.assertEqual(0, code, output[-2000:])
        events = os.listdir(os.path.join(root, '_glassbox'))
        self.assertGreater(len(events), 50, 'the tick barely emitted')
        kinds = set()
        for name in events:
            with open(os.path.join(root, '_glassbox', name), encoding='utf-8') as handle:
                payload = json.load(handle)
            self.assertEqual(R.GLASSBOX_SOURCE, payload['source'])
            kinds.add(payload['kind'])
        self.assertEqual({'refresh.tick', 'refresh.step'}, kinds)

    def test_a_bug_in_the_runner_still_leaves_a_terminal_record(self):
        """An unattended job that dies with a traceback tells the supervisor nothing."""
        root = self.root()

        class BuggyTick(R.Tick):
            def publication_record(self):
                raise RuntimeError('a bug in the runner, not in a producer')

        tick = BuggyTick(root, emit=lambda *a, **k: None)
        os.environ['MONEYBALL_ROOT'] = root
        try:
            result = tick.run()
        finally:
            os.environ.pop('MONEYBALL_ROOT', None)
        self.assertEqual(R.COMPUTE_FAILED, result)
        self.assertEqual(R.COMPUTE_FAILED, outcome(root))
        self.assertIn('RuntimeError', read_ticks(root)[-1]['unhandled'])
        self.assertEqual(R.PHASE_IDLE, read_status(root)['phase'])
        self.assertFalse(os.path.exists(os.path.join(root, '.pipeline_run.lock')),
                         'the writer lease outlived a crashing tick')

    def test_a_table_hook_that_does_not_exist_is_fatal_before_step_one(self):
        root = self.root()
        broken = tuple(
            step if step.name != R.CHAIN_STEP
            else T.Step(step.name, step.argv, step.timeout_s, step.kind, hard=step.hard,
                        gate='no_such_gate', requires=step.requires)
            for step in T.TABLE)
        tick = R.Tick(root, table=broken, emit=lambda *a, **k: None)
        os.environ['MONEYBALL_ROOT'] = root
        try:
            self.assertEqual(R.FATAL_PRESTEP, tick.run())
        finally:
            os.environ.pop('MONEYBALL_ROOT', None)
        self.assertEqual(0, invocations(root, T.by_name()[CREC]))

    def test_nothing_was_written_under_the_repository(self):
        """Negative control for the whole file: every tick ran in a temp root."""
        self.assertEqual(_REAL_ENGINE_BEFORE, _real_engine_fingerprint())
        self.assertFalse(os.path.exists(os.path.join(REPO_ROOT, '.pipeline_run.lock')),
                         'the suite left a writer lease in the repository')

    def test_T29_external_damage_survives_failures_and_noop_until_repaired(self):
        crec = T.by_name()[CREC]
        root = self.root()
        seed_state(root)
        code, output, _ = run_tick(root, '--force-due', CREC)
        self.assertEqual(0, code, output[-2000:])
        original_publication = read_state(root)['publication']
        with open(output_path(root, crec), 'w', encoding='utf-8') as handle:
            handle.write('id,value\n')
        write_control(root, {stub_key(crec): {'always_fail': True}})
        code, output, _ = run_tick(root, '--only', CREC, '--force-compute')
        self.assertEqual(1, code, output[-2000:])
        self.assertTrue(read_state(root)['steps'][CREC]['torn'])
        self.assertEqual(original_publication, read_state(root)['publication'])
        # A successful no-op still leaves the damaged bytes in place.
        producer = os.path.join(root, 'fec', crec.argv[0])
        with open(producer, 'w', encoding='utf-8') as handle:
            handle.write('pass\n')
        code, output, _ = run_tick(root, '--only', CREC, '--force-due', CREC)
        self.assertEqual(1, code, output[-2000:])
        self.assertTrue(read_state(root)['steps'][CREC]['torn'])
        self.assertEqual(original_publication, read_state(root)['publication'])
        # A real rewrite repairs the evidence and permits publication.
        with open(producer, 'w', encoding='utf-8') as handle:
            handle.write(STUB)
        write_control(root, {})
        code, output, _ = run_tick(root, '--force-due', CREC)
        self.assertEqual(0, code, output[-2000:])
        state = read_state(root)
        self.assertFalse(state['steps'][CREC]['torn'])
        self.assertEqual([], S.verify_manifest(root, read_manifest_of(
            root, crec, state['steps'][CREC]['manifest_sha'])))

    def test_T30_shared_zero_verdict_survives_a_successful_noop(self):
        root, core, core_b = self._hand_over_root({'empty': [False, True]})
        self._establish_hand_over(root, core, core_b)
        code, output, _ = run_tick(root, '--only', CORE, '--force-due', CORE)
        self.assertEqual(1, code, output[-2000:])
        self.assertEqual(S.COVERAGE_SUSPECT, read_state(root)['steps'][CORE]['coverage'])
        with open(os.path.join(root, 'fec', core.argv[0]), 'w', encoding='utf-8') as handle:
            handle.write('pass\n')
        code, output, _ = run_tick(root, '--only', CORE, '--force-due', CORE)
        self.assertEqual(1, code, output[-2000:])
        self.assertEqual(S.COVERAGE_SUSPECT, read_state(root)['steps'][CORE]['coverage'])
        self.assertEqual(R.COLLECTED_NOT_PUBLISHED, outcome(root))

    def test_T31_locked_manifest_defers_due_step_without_erasing_baseline(self):
        crec = T.by_name()[CREC]
        root = self.root({stub_key(crec): {'empty': [False, True]}})
        seed_state(root)
        code, output, _ = run_tick(root, '--force-due', CREC)
        self.assertEqual(0, code, output[-2000:])
        state = read_state(root)
        entry = dict(state['steps'][CREC])
        manifest = os.path.join(root, 'data', 'engine', 'refresh_manifests',
                                '%s.%s.json' % (crec.slug, entry['manifest_sha']))
        os.rename(manifest, manifest + '.saved')
        os.makedirs(manifest)
        code, output, _ = run_tick(root, '--force-due', CREC)
        self.assertEqual(1, code, output[-2000:])
        self.assertEqual(entry, read_state(root)['steps'][CREC])
        self.assertEqual(1, invocations(root, crec), 'collector ran without its baseline')
        self.assertEqual(state['publication'], read_state(root)['publication'])
        os.rmdir(manifest)
        os.rename(manifest + '.saved', manifest)
        code, output, _ = run_tick(root, '--force-due', CREC)
        self.assertEqual(1, code, output[-2000:])
        self.assertEqual(S.COVERAGE_SUSPECT, read_state(root)['steps'][CREC]['coverage'])

    def test_T32_timestamp_only_change_does_not_rerun_source(self):
        crec = T.by_name()[CREC]
        root = self.root()
        seed_state(root)
        code, output, _ = run_tick(root, '--force-due', CREC)
        self.assertEqual(0, code, output[-2000:])
        path = output_path(root, crec)
        info = os.stat(path)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1000000000))
        code, output, _ = run_tick(root)
        self.assertEqual(0, code, output[-2000:])
        self.assertEqual(R.NOTHING_DUE, outcome(root))
        self.assertEqual(1, invocations(root, crec))

    def test_T33_console_unknown_torn_flag_preserves_prior_damage(self):
        crec = T.by_name()[CREC]
        root = self.root()
        seed_state(root, steps={CREC: {
            'state': S.FAILED, 'torn': None,
            'unverified_files': ['data/out/damaged.csv'],
            'failure': {'kind': S.KILLED, 'exit': None, 'detail': 'console'}}})
        damaged = os.path.join(root, 'data', 'out', 'damaged.csv')
        os.makedirs(os.path.dirname(damaged), exist_ok=True)
        with open(damaged, 'w', encoding='utf-8') as handle:
            handle.write('partial')
        tick = R.Tick(root, emit=lambda *a, **k: None)
        S.write_json_atomic(tick.pre_snapshot_path(crec),
                            {k: list(v) for k, v in tick.snapshot().items()})
        with open(os.path.join(root, 'fec', crec.argv[0]), 'w', encoding='utf-8') as handle:
            handle.write('pass\n')
        code, output, _ = run_tick(root, '--only', CREC)
        self.assertEqual(1, code, output[-2000:])
        self.assertTrue(read_state(root)['steps'][CREC]['torn'])
        self.assertIsNone(read_state(root)['publication'])

    def test_T34_crash_after_filing_failure_bypasses_attempt_gap(self):
        # A tree kill can kill the child first. The parent files an ordinary exit failure
        # before it dies, so neither RUNNING nor failure.kind identifies the interruption.
        crec = T.by_name()[CREC]
        root = self.root({stub_key(crec): {'always_fail': True}})
        seed_state(root)
        code, output, _ = run_tick(root, '--force-due', CREC,
                                   **{R.CRASH_ENV: 'after_failed_step_commit'})
        self.assertEqual(137, code, output[-2000:])
        self.assertEqual(S.EXIT, read_state(root)['steps'][CREC]['failure']['kind'])
        preview = R.Tick(root, emit=lambda *a, **k: None)
        preview.state.load(rotate=False)
        self.assertTrue(preview.due(crec)[0], 'dry-run must agree with crash recovery')
        write_control(root, {})
        code, output, _ = run_tick(root)
        self.assertEqual(0, code, output[-2000:])
        self.assertEqual(2, invocations(root, crec))
        self.assertEqual(R.PUBLISHED, outcome(root))
        self.assertNotIn('failure_pending_tick_end', read_state(root)['steps'][CREC])

    def test_T35_normal_failed_tick_keeps_attempt_cooldown(self):
        crec = T.by_name()[CREC]
        root = self.root({stub_key(crec): {'always_fail': True}})
        seed_state(root)
        code, output, _ = run_tick(root, '--only', CREC, '--force-due', CREC, '--skip-compute')
        self.assertEqual(1, code, output[-2000:])
        self.assertNotIn('failure_pending_tick_end', read_state(root)['steps'][CREC])
        preview = R.Tick(root, emit=lambda *a, **k: None)
        preview.state.load(rotate=False)
        due, why = preview.due(crec)
        self.assertFalse(due)
        self.assertIn('attempted', why)


if __name__ == '__main__':
    unittest.main(verbosity=1)
