# -*- coding: utf-8 -*-
"""Fault-injection suite for the tiered degrade of the refresh tick runner (2026-10-15).

WHAT IT PROVES.  fec/refresh_tick.py is now the single scheduled orchestrator, and it must never
halt the whole system.  Every scenario here injects ONE fault into a stub producer and asserts what
the runner does with it:

  * an enrichment source that 500s degrades the publication with a LABEL; every other source and
    the compute phase still run
  * an admissions quota stop is a BUDGET: recorded PASS-partial, not retried the same day, the
    last-good generation served and labelled -- and the tick passed the producer its cost bounds
  * a transient failure backs off 15 min, 30, 60 ... to a 4 h cap, and at most 6 attempts a day
  * a data-contract refusal is dead-lettered with its log tail and never retried until an input
    generation changes
  * press exit 3 is a PARTIAL success, never a failure
  * a CORE source that never succeeded refuses compute with a clear reason; past its window its
    last-good is served and labelled
  * data/engine/source_freshness.json carries the schema the dashboard reads

HOW IT IS SAFE.  Like test_refresh_tick.py (whose stub producer and helpers it reuses), every tick
runs in a temp root against stub producers: no network, no collector, no pipeline, no classifier.
Ticks that need the compute phase cost about a minute on this workstation, so most scenarios use
--only / --skip-compute and the rest are driven in process on a constructed Tick.

  python fec/tests/infra/test_refresh_degrade.py
"""
import ast
import datetime
import json
import os
import shutil
import subprocess
import sys
import types
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
FEC = os.path.dirname(os.path.dirname(_HERE))
REPO_ROOT = os.path.dirname(FEC)
for _path in (FEC, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import test_refresh_tick as base          # noqa: E402  (pins the engines to the host on import)
import pipeline_lock                       # noqa: E402
import refresh_failure as F                # noqa: E402
import refresh_state as S                  # noqa: E402
import refresh_table as T                  # noqa: E402
import refresh_tick as R                   # noqa: E402

CNN = 'interviews: CNN (14d)'
MTP = 'interviews: MTP'
CBS = 'interviews: CBS'
CREC = base.CREC
CHRG = base.CHRG
DISCOVERY = base.CORE
ADMISSIONS = base.CORE_B
REPOSITORY = 'Congress.gov bill repository'
BLUESKY = 'Bluesky collector'
PRESS_DELTA = base.PRESS_DELTA
PRESS_INTAKE = 'press intake audit'
INTAKE = base.INTAKE
ROSTER = 'collector roster verification'
CORE_NAMES = ('Congress.gov bill discovery',
              'Official bill evidence and automatic scoring admission',
              'FEC source money refresh', 'Congress rollcall source refresh',
              'Congress.gov bill repository', 'GovInfo BILLSTATUS bulk refresh')

FRESHNESS_FIELDS = ('tier', 'cadence_h', 'last_success_utc', 'age_h', 'status', 'detail', 'state',
                    'generation', 'failure_class', 'next_attempt_utc', 'attempts_24h', 'partial',
                    'budget_stop', 'served_last_good')


def ago(hours):
    return S.stamp(S.utcnow() - datetime.timedelta(hours=hours))


def ahead(hours):
    return S.stamp(S.utcnow() + datetime.timedelta(hours=hours))


def stale_entry(hours=192.0):
    """A source that last succeeded `hours` ago: past its cadence, inside nothing."""
    return {'state': S.OK, 'generation': 1, 'last_ok_utc': ago(hours),
            'last_attempt_utc': ago(hours)}


def edit_state(root, name, **fields):
    path = os.path.join(root, 'data', 'engine', R.STATE_NAME)
    with open(path, encoding='utf-8') as handle:
        payload = json.load(handle)
    payload['steps'][name].update(fields)
    S.write_json_atomic(path, payload)


def read_freshness(root):
    with open(os.path.join(root, 'data', 'engine', R.FRESHNESS_NAME), encoding='utf-8') as handle:
        return json.load(handle)


def dead_letter_lines(root, step):
    path = os.path.join(root, 'data', 'engine', R.DEAD_LETTER_DIR, '%s.jsonl' % step.slug)
    if not os.path.exists(path):
        return []
    with open(path, 'rb') as handle:
        raw = handle.read()
    assert b'\r' not in raw, 'a dead-letter file must be LF only'
    return [json.loads(line.decode('utf-8')) for line in raw.split(b'\n') if line.strip()]


def hours_since(stamp):
    return (S.utcnow() - S.parse_stamp(stamp)).total_seconds() / 3600.0


def minutes_between(first, second):
    return (S.parse_stamp(second) - S.parse_stamp(first)).total_seconds() / 60.0


class DegradeTest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self._roots = []
        for name in (pipeline_lock.LOCK_TOKEN_ENV, pipeline_lock.LOCK_OWNER_PID_ENV):
            os.environ.pop(name, None)

    def tearDown(self):
        for name in (pipeline_lock.LOCK_TOKEN_ENV, pipeline_lock.LOCK_OWNER_PID_ENV):
            os.environ.pop(name, None)
        for root in self._roots:
            shutil.rmtree(root, ignore_errors=True)

    def root(self, control=None, skip_producers=()):
        path = base.make_root(control, skip_producers)
        self._roots.append(path)
        return path

    def tick_for(self, root, **kwargs):
        """A constructed Tick over a seeded root: state loaded, nothing launched or locked."""
        kwargs.setdefault('emit', lambda *a, **k: None)
        kwargs.setdefault('log', lambda line: None)
        tick = R.Tick(root, **kwargs)
        tick.state.load(rotate=False)
        return tick

    def step(self, name):
        return T.by_name()[name]

    # =========================================================================================
    # 1  an enrichment source 500s: it is a LABEL; every other source and the compute proceed
    # =========================================================================================
    def test_enrichment_500_degrades_the_publication_and_stops_nothing(self):
        cnn = self.step(CNN)
        root = self.root({base.stub_key(cnn): {
            'always_fail': True, 'stdout': 'HTTP Error 500: Internal Server Error\n'}})
        base.seed_state(root, steps={CNN: stale_entry(), MTP: stale_entry(), CBS: stale_entry()})
        code, output, _ = base.run_tick(root)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED_DEGRADED, base.outcome(root), output[-3000:])
        state = base.read_state(root)
        # the failing source: classified, labelled, NOT retried inside the same tick
        entry = state['steps'][CNN]
        self.assertEqual(S.FAILED, entry['state'])
        self.assertEqual(S.TRANSIENT, entry['failure_class'])
        self.assertEqual('HTTP 5xx', entry['failure']['reason'])
        self.assertEqual(1, base.invocations(root, cnn),
                         'a TRANSIENT failure was retried inside the backoff window')
        self.assertGreaterEqual(minutes_between(entry['last_attempt_utc'],
                                                entry['next_attempt_utc']), 14.9)
        self.assertEqual({CNN: S.TRANSIENT}, read_ticks_last(root)['failure_classes'])
        # every other source still ran, and so did the whole compute phase
        for name in (MTP, CBS):
            self.assertEqual(S.OK, state['steps'][name]['state'], name)
            self.assertEqual(2, state['steps'][name]['generation'], name)
            self.assertEqual(1, base.invocations(root, self.step(name)), name)
        self.assertEqual(S.OK, state['steps'][R.CHAIN_STEP]['state'])
        self.assertEqual(1, base.invocations(root, self.step(R.CHAIN_STEP)))
        # the label travels with the publication and with the feed
        publication = state['publication']
        self.assertTrue(publication['degraded'])
        self.assertEqual([CNN], publication['stale_sources'])
        self.assertEqual(S.DEGRADED, publication['labels'][CNN]['status'])
        self.assertIn('latest attempt failed (TRANSIENT)', publication['labels'][CNN]['detail'])
        self._assert_freshness_schema(root)
        feed = read_freshness(root)
        self.assertEqual(S.DEGRADED, feed['sources'][CNN]['status'])
        self.assertTrue(feed['sources'][CNN]['served_last_good'])
        self.assertEqual(S.TRANSIENT, feed['sources'][CNN]['failure_class'])
        self.assertEqual([CNN], feed['summary']['not_fresh'])
        self.assertEqual(S.FRESH, feed['sources'][MTP]['status'])
        # dry-run reports the label without writing
        before = open(os.path.join(root, 'data', 'engine', R.FRESHNESS_NAME), 'rb').read()
        code, dry, _ = base.run_tick(root, '--dry-run')
        self.assertEqual(0, code, dry)
        self.assertIn('source labels: 1 of 23 not FRESH', dry)
        self.assertIn('DEGRADED', dry)
        self.assertEqual(before, open(os.path.join(root, 'data', 'engine', R.FRESHNESS_NAME),
                                      'rb').read(), '--dry-run rewrote the freshness feed')

    # =========================================================================================
    # 2  admissions (D-211): a QUOTA stop is a FAILED step, a call-budget stop is a PASS
    # =========================================================================================
    def test_admissions_quota_is_a_failed_step_labelled_stale_and_never_respent(self):
        admissions = self.step(ADMISSIONS)
        self.assertEqual(['--limit', '80', '--max-classifier-calls', '48'], R.render_argv(admissions.tick_argv))
        root = self.root({base.stub_key(admissions): {
            'always_fail': True, 'write_then_fail': True,     # it checkpointed before it stopped
            'stdout': 'CLASSIFIER_QUOTA_EXHAUSTED: spend limit reached\n'
                      'Run stopped on classifier quota at HR1-119; 3 selected key(s) not '
                      'attempted. Checkpointed decisions will be reused on rerun.\n'}})
        seeded = stale_entry(25.0)
        base.seed_state(root, steps={ADMISSIONS: dict(seeded)})
        code, output, _ = base.run_tick(root)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED_DEGRADED, base.outcome(root), output[-3000:])
        # the budget flags reached the producer: the stub names its counter file after its argv
        self.assertEqual(1, base.invocations(root, admissions),
                         'the tick did not pass the producer its cost bounds')
        state = base.read_state(root)
        entry = state['steps'][ADMISSIONS]
        # FAILED -- never reported as a pass or a partial -- and nothing new stands
        self.assertEqual(S.FAILED, entry['state'])
        self.assertEqual(S.BUDGET_QUOTA, entry['failure_class'])
        self.assertEqual((S.EXIT, 1, S.BUDGET_QUOTA, S.STOP_QUOTA),
                         (entry['failure']['kind'], entry['failure']['exit'],
                          entry['failure']['class'], entry['failure']['reason']))
        self.assertEqual(seeded['last_ok_utc'], entry['last_ok_utc'])
        self.assertEqual(1, entry['generation'], 'a quota stop advanced the generation')
        self.assertEqual(S.STOP_QUOTA, entry['budget_stop']['reason'])
        self.assertTrue(entry['quota_stop_utc'])
        self.assertIsNone(entry['next_attempt_utc'])
        # the checkpoint it wrote is manifested, so the clean stop is not read as a tear
        self.assertFalse(entry['torn'])
        self.assertEqual([], entry['unverified_files'])
        self.assertTrue(entry['manifest_sha'])
        record = base.read_ticks(root)[-1]
        self.assertEqual([], record['partial_steps'])
        self.assertEqual({ADMISSIONS: S.BUDGET_QUOTA}, record['failure_classes'])
        # compute proceeded ON the last-good generation, labelled STALE_LABELLED
        self.assertEqual(S.OK, state['steps'][R.CHAIN_STEP]['state'])
        label = state['publication']['labels'][ADMISSIONS]
        self.assertEqual(S.STALE_LABELLED, label['status'])
        self.assertIn('classifier quota exhausted', label['detail'])
        self.assertEqual([ADMISSIONS], state['publication']['stale_sources'])
        feed = read_freshness(root)['sources'][ADMISSIONS]
        self.assertEqual(S.STALE_LABELLED, feed['status'])
        self.assertTrue(feed['served_last_good'])
        self.assertEqual(S.BUDGET_QUOTA, feed['failure_class'])
        self.assertEqual(S.STOP_QUOTA, feed['budget_stop'])
        # the same day: nothing is due, nothing is retried (no re-spend, DE-020)
        code, output, _ = base.run_tick(root)
        self.assertEqual(0, code, output[-2000:])
        self.assertEqual(R.NOTHING_DUE, base.outcome(root), output[-2000:])
        self.assertEqual(1, base.invocations(root, admissions), 'a quota stop was retried')
        # not even when its cadence stamp is ancient: the window is the stop's, not the schedule's
        edit_state(root, ADMISSIONS, last_ok_utc=ago(400.0), last_attempt_utc=ago(3.0))
        base.run_tick(root, '--only', ADMISSIONS, '--skip-compute')
        self.assertEqual(1, base.invocations(root, admissions))
        # the next daily window: exactly ONE attempt, and a second quota stop opens a new window
        edit_state(root, ADMISSIONS, quota_stop_utc=ago(25.0), last_attempt_utc=ago(25.0))
        base.run_tick(root, '--only', ADMISSIONS, '--skip-compute')
        self.assertEqual(2, base.invocations(root, admissions))
        entry = base.read_state(root)['steps'][ADMISSIONS]
        self.assertEqual(S.BUDGET_QUOTA, entry['failure_class'])
        self.assertLess(hours_since(entry["quota_stop_utc"]), 0.2)
        base.run_tick(root, '--only', ADMISSIONS, '--skip-compute')
        self.assertEqual(2, base.invocations(root, admissions), 'a second attempt in one window')

    def test_a_clean_exit_on_the_call_budget_is_a_pass_at_the_normal_cadence(self):
        admissions = self.step(ADMISSIONS)
        root = self.root({base.stub_key(admissions): {
            'stdout': 'Run stopped on the classifier call budget (48 call(s)) at HR9-119; '
                      '12 selected key(s) not attempted and left for the next run.\n'}})
        base.seed_state(root, steps={ADMISSIONS: stale_entry(25.0)})
        code, output, _ = base.run_tick(root, '--only', ADMISSIONS, '--skip-compute')
        self.assertEqual(1, code, output[-2000:])             # --skip-compute never publishes
        entry = base.read_state(root)['steps'][ADMISSIONS]
        self.assertEqual(S.OK, entry['state'], 'an intentional bound is a PASS, not a partial')
        self.assertIsNone(entry['failure'])
        self.assertIsNone(entry['failure_class'])
        self.assertIsNone(entry['quota_stop_utc'])
        self.assertEqual(2, entry['generation'])
        self.assertEqual(S.STOP_CALLS, entry['budget_stop']['reason'])
        self.assertEqual([], base.read_ticks(root)[-1]['partial_steps'])
        self.assertEqual({}, base.read_ticks(root)[-1]['failure_classes'])
        feed = read_freshness(root)['sources'][ADMISSIONS]
        self.assertEqual(S.FRESH, feed['status'])
        self.assertTrue(feed['partial'], 'the backlog is not drained and the feed says so')
        self.assertEqual(S.STOP_CALLS, feed['budget_stop'])
        # the normal cadence: not due again until a day has passed
        tick = self.tick_for(root)
        self.assertFalse(tick.due(admissions)[0])
        tick.state.record(ADMISSIONS, last_ok_utc=ago(24.0), last_attempt_utc=ago(24.0))
        self.assertTrue(tick.due(admissions)[0])

    def test_a_quota_stop_on_a_core_source_that_never_succeeded_promotes_nothing(self):
        admissions = self.step(ADMISSIONS)
        root = self.root({base.stub_key(admissions): {
            'always_fail': True, 'write_then_fail': True,     # an artifact exists on disk ...
            'stdout': 'CLASSIFIER_QUOTA_EXHAUSTED: spend limit reached\n'
                      'Run stopped on classifier quota at HR1-119; 3 selected key(s) not '
                      'attempted. Checkpointed decisions will be reused on rerun.\n'}})
        base.seed_state(root, steps={ADMISSIONS: dict(S.blank_step())})
        code, output, _ = base.run_tick(root, '--only', ADMISSIONS)
        # ... and still: no last_ok, no generation, nothing served, and compute refuses
        entry = base.read_state(root)['steps'][ADMISSIONS]
        self.assertEqual(S.FAILED, entry['state'])
        self.assertIsNone(entry['last_ok_utc'])
        self.assertEqual(0, entry['generation'])
        self.assertEqual(S.BUDGET_QUOTA, entry['failure_class'])
        feed = read_freshness(root)['sources'][ADMISSIONS]
        self.assertFalse(feed['served_last_good'])
        self.assertEqual((S.DEGRADED, 'has never succeeded'), (feed['status'], feed['detail']))
        tick = self.tick_for(root)
        eligible, blockers = tick.eligibility()
        self.assertFalse(eligible)
        self.assertIn((ADMISSIONS, 'has never succeeded'), blockers)
        self.assertEqual(1, code, output[-2000:])
        self.assertEqual(R.COLLECTED_NOT_PUBLISHED, base.outcome(root))
        self.assertIn({'what': ADMISSIONS, 'why': 'has never succeeded'},
                      base.read_ticks(root)[-1]['blockers'])

    # =========================================================================================
    # 3  TRANSIENT: 15 min doubling to a 4 h cap, at most 6 attempts a day
    # =========================================================================================
    def test_transient_backoff_schedule_is_respected(self):
        mtp = self.step(MTP)
        root = self.root({base.stub_key(mtp): {'always_fail': True}})     # exit 1, nothing printed
        base.seed_state(root, steps={MTP: stale_entry()})
        waits = []
        for attempt in (1, 2, 3):
            code, output, _ = base.run_tick(root, '--only', MTP, '--skip-compute')
            self.assertEqual(1, code, output[-2000:])
            entry = base.read_state(root)['steps'][MTP]
            self.assertEqual(attempt, base.invocations(root, mtp), output[-1500:])
            self.assertEqual(attempt, entry['attempts_since_ok'])
            self.assertEqual(S.TRANSIENT, entry['failure_class'])
            self.assertIn('unclassified non-zero exit', entry['failure']['reason'])
            waits.append(minutes_between(entry['last_attempt_utc'], entry['next_attempt_utc']))
            if attempt == 1:
                # still backing off: not due, not launched, and the dry-run says why
                code, output, _ = base.run_tick(root, '--only', MTP, '--skip-compute')
                self.assertEqual(1, base.invocations(root, mtp),
                                 'a step was launched inside its backoff window')
                code, dry, _ = base.run_tick(root, '--dry-run')
                self.assertIn('backing off until', dry)
            edit_state(root, MTP, next_attempt_utc=ago(1.0 / 60.0))        # the wait has elapsed
        for observed, expected in zip(waits, (15.0, 30.0, 60.0)):
            self.assertGreaterEqual(observed, expected, waits)
            self.assertLess(observed, expected + 1.0, waits)
        # the whole ladder, and its cap, from the one function that owns it
        self.assertEqual([15.0, 30.0, 60.0, 120.0, 240.0, 240.0, 240.0, 240.0],
                         [F.backoff_minutes(n) for n in range(1, 9)])
        self.assertEqual(0.0, F.backoff_minutes(5, base=0))
        self.assertEqual(6, F.MAX_ATTEMPTS_PER_DAY)

    def test_six_attempts_a_day_is_the_cap_and_the_window_rolls(self):
        root = self.root()
        mtp = self.step(MTP)
        base.seed_state(root, steps={MTP: stale_entry()})
        tick = self.tick_for(root)
        failure = {'kind': S.EXIT, 'exit': 1, 'detail': 'x'}
        tick.state.record(MTP, state=S.FAILED, failure=failure, failure_class=S.TRANSIENT,
                          attempts_since_ok=6, last_attempt_utc=ago(0.6),
                          next_attempt_utc=ago(0.5),
                          recent_attempts_utc=[ago(hours) for hours in (1, 2, 3, 4, 5, 6)])
        due, why = tick.due(mtp)
        self.assertFalse(due)
        self.assertIn('attempted 6 times in the last 24 h', why)
        self.assertIn('attempted', why)
        # a dependency's new information skips the WAIT, never the cap
        self.assertFalse(tick.failure_gate(mtp, tick.state.step(MTP), triggered=True)[0])
        # the oldest attempt leaves the rolling day: five remain, the wait is over, it runs
        tick.state.record(MTP, recent_attempts_utc=[ago(hours) for hours in (1, 2, 3, 4, 25)])
        due, why = tick.due(mtp)
        self.assertTrue(due, why)
        # a stamp absurdly far ahead is clock damage: it is not waited for
        tick.state.record(MTP, next_attempt_utc=ahead(10.0))
        self.assertTrue(tick.due(mtp)[0])
        # and it never holds an operator off
        tick.state.record(MTP, next_attempt_utc=ahead(0.2),
                          recent_attempts_utc=[ago(hours) for hours in (1, 2, 3, 4, 5, 6)])
        self.assertFalse(tick.due(mtp)[0])
        forced = self.tick_for(root, force_due=(MTP,))
        self.assertEqual((True, 'forced'), forced.due(mtp))

    # =========================================================================================
    # 4  DATA_CONTRACT: dead-letter, alert, no automatic retry until an input generation changes
    # =========================================================================================
    def test_data_contract_refusal_is_dead_lettered_and_never_retried(self):
        mtp = self.step(MTP)
        root = self.root({base.stub_key(mtp): {
            'always_fail': True,
            'stdout': '\n'.join('line %d of the producer log' % index for index in range(60))
                      + '\nValueError: exact duplicate interview-turn groups\n'}})
        base.seed_state(root, steps={MTP: stale_entry()})
        code, output, _ = base.run_tick(root, '--only', MTP, '--skip-compute')
        self.assertEqual(1, code, output[-2000:])
        entry = base.read_state(root)['steps'][MTP]
        self.assertEqual(S.DATA_CONTRACT, entry['failure_class'])
        self.assertIsNone(entry['next_attempt_utc'])
        self.assertEqual({}, entry['dead_letter']['signature'])      # a collector has no upstream
        letters = dead_letter_lines(root, mtp)
        self.assertEqual(1, len(letters))
        letter = letters[0]
        self.assertEqual(MTP, letter['step'])
        self.assertEqual(S.DATA_CONTRACT, letter['class'])
        self.assertEqual(1, letter['exit'])
        self.assertTrue(S.parse_stamp(letter['ts']))
        self.assertLessEqual(len(letter['log_tail']), F.LOG_TAIL_LINES)
        self.assertEqual(F.LOG_TAIL_LINES, len(letter['log_tail']))
        self.assertIn('exact duplicate interview-turn groups', '\n'.join(letter['log_tail']))
        self.assertNotIn('line 0 of the producer log', letter['log_tail'])    # only the TAIL
        self.assertIn('DEAD-LETTERED', output)
        self.assertEqual(S.DEAD_LETTER, self._freshness(root, MTP)['status'])
        # many ticks later, even with the cadence long past: no retry, no second line
        for _ in range(2):
            edit_state(root, MTP, last_ok_utc=ago(400.0), last_attempt_utc=ago(30.0))
            code, output, _ = base.run_tick(root, '--only', MTP, '--skip-compute')
            self.assertEqual(1, base.invocations(root, mtp), output[-1500:])
        self.assertEqual(1, len(dead_letter_lines(root, mtp)))
        code, dry, _ = base.run_tick(root, '--dry-run')
        self.assertIn('no automatic retry until an input generation changes', dry)
        # The owner can still force it, and a success clears the letter and the label.
        base.write_control(root, {})
        code, output, _ = base.run_tick(root, '--only', MTP, '--skip-compute', '--force-due', MTP)
        self.assertEqual(2, base.invocations(root, mtp))
        entry = base.read_state(root)['steps'][MTP]
        self.assertEqual(S.OK, entry['state'])
        self.assertIsNone(entry['dead_letter'])
        self.assertIsNone(entry['failure_class'])
        self.assertEqual(S.FRESH, self._freshness(root, MTP)['status'])
        self.assertEqual(1, len(dead_letter_lines(root, mtp)), 'the history must survive')

    def test_a_dead_letter_is_released_only_by_an_input_generation_change(self):
        root = self.root()
        intake = self.step(PRESS_INTAKE)
        base.seed_state(root)
        tick = self.tick_for(root)
        signature = tick.input_signature(intake)
        self.assertEqual({PRESS_DELTA: 1}, signature)
        tick.state.record(PRESS_INTAKE, state=S.FAILED, last_attempt_utc=ago(30.0),
                          last_ok_utc=ago(400.0),
                          failure={'kind': S.EXIT, 'exit': 1, 'detail': 'contract'},
                          failure_class=S.DATA_CONTRACT,
                          dead_letter={'at': ago(30.0), 'signature': signature, 'exit': 1})
        due, why = tick.due(intake)
        self.assertFalse(due)
        self.assertIn('dead-lettered', why)
        tick.state.record(PRESS_DELTA, generation=2)               # the upstream produced new input
        due, why = tick.due(intake)
        self.assertTrue(due, why)
        self.assertIn('input generation changed', why)

    # =========================================================================================
    # 5  SOURCE_OUTAGE: last-good kept, probed on cadence, never retried inside the tick
    # =========================================================================================
    def test_source_outage_keeps_last_good_and_probes_on_cadence(self):
        chrg = self.step(CHRG)
        root = self.root({base.stub_key(chrg): {
            'always_fail': True, 'exit': 2,
            'stdout': 'CHRG: total outage (zero ingested, >=3 errors) -- reporting collection '
                      'failure\n'}})
        previous = base.output_path(root, chrg)
        os.makedirs(os.path.dirname(previous), exist_ok=True)
        with open(previous, 'wb') as handle:
            handle.write(b'id,value\nLAST-GOOD,1\n')
        base.seed_state(root, steps={CHRG: stale_entry()})
        code, output, _ = base.run_tick(root, '--only', CHRG, '--skip-compute',
                                        '--backoff-minutes', '0')
        self.assertEqual(1, code, output[-2000:])
        entry = base.read_state(root)['steps'][CHRG]
        self.assertEqual(S.SOURCE_OUTAGE, entry['failure_class'])
        self.assertIsNone(entry['next_attempt_utc'])
        self.assertEqual(1, base.invocations(root, chrg), 'an outage was retried inside the tick')
        self.assertEqual(b'id,value\nLAST-GOOD,1\n', open(previous, 'rb').read())
        self.assertEqual(S.DEGRADED, self._freshness(root, CHRG)['status'])
        self.assertTrue(self._freshness(root, CHRG)['served_last_good'])
        tick = self.tick_for(root)
        due, why = tick.due(chrg)
        self.assertFalse(due)
        self.assertIn('last-good kept', why)
        tick.state.record(CHRG, last_attempt_utc=ago(5.0))
        self.assertFalse(tick.due(chrg)[0], 'probed again long before its cadence')
        tick.state.record(CHRG, last_attempt_utc=ago(25.0))
        due, why = tick.due(chrg)
        self.assertTrue(due, why)
        self.assertEqual('source outage probe', why)

    # =========================================================================================
    # 6  press exit 3 is a PARTIAL success
    # =========================================================================================
    def test_press_exit_three_is_a_partial_success_never_a_failure(self):
        press = self.step(PRESS_DELTA)
        root = self.root({base.stub_key(press): {'always_fail': True, 'exit': 3,
                                                 'write_then_fail': True}})
        base.seed_state(root, steps={PRESS_DELTA: stale_entry()})
        code, output, _ = base.run_tick(root, '--only', PRESS_DELTA, '--skip-compute')
        self.assertEqual(1, code, output[-2000:])             # --skip-compute never publishes
        entry = base.read_state(root)['steps'][PRESS_DELTA]
        self.assertEqual(S.PARTIAL, entry['state'])
        self.assertIsNone(entry['failure'])
        self.assertIsNone(entry['failure_class'])
        self.assertIsNone(entry['next_attempt_utc'])
        self.assertEqual({}, base.read_ticks(root)[-1]['failure_classes'])
        self.assertEqual([PRESS_DELTA], base.read_ticks(root)[-1]['partial_steps'])
        feed = self._freshness(root, PRESS_DELTA)
        self.assertEqual(S.FRESH, feed['status'])
        self.assertTrue(feed['partial'])
        self.assertEqual(1, base.invocations(root, press))

    # =========================================================================================
    # 7  CORE: never succeeded refuses compute with a clear reason; stale is served and labelled
    # =========================================================================================
    def test_core_source_that_never_succeeded_refuses_compute_with_a_reason(self):
        discovery = self.step(DISCOVERY)
        root = self.root({base.stub_key(discovery): {'always_fail': True}})
        base.seed_state(root, steps={DISCOVERY: dict(S.blank_step())})
        code, output, _ = base.run_tick(root)
        self.assertEqual(1, code, output[-3000:])
        self.assertEqual(R.COLLECTED_NOT_PUBLISHED, base.outcome(root), output[-3000:])
        record = base.read_ticks(root)[-1]
        self.assertIn({'what': DISCOVERY, 'why': 'has never succeeded'}, record['blockers'])
        self.assertIn('NOT ELIGIBLE', output)
        self.assertEqual(0, base.invocations(root, self.step(R.CHAIN_STEP)),
                         'compute ran without a last-good CORE generation')
        self.assertIsNone(base.read_state(root)['publication'])
        feed = self._freshness(root, DISCOVERY)
        self.assertEqual(S.DEGRADED, feed['status'])
        self.assertEqual('has never succeeded', feed['detail'])
        self.assertFalse(feed['served_last_good'])

    def test_eligibility_is_strict_about_core_integrity_and_loose_about_everything_else(self):
        root = self.root()
        base.seed_state(root)
        tick = self.tick_for(root)
        self.assertEqual((True, []), tick.eligibility())
        # ENRICHMENT never blocks: never collected, torn, suspect -- all labels
        tick.state.record(CREC, last_ok_utc=None, state=S.NEVER)
        tick.state.record(CHRG, torn=True, unverified_files=['data/out/x.csv'])
        tick.state.record(CNN, coverage=S.COVERAGE_SUSPECT)
        tick.state.record(MTP, last_ok_utc=ago(900.0))
        self.assertEqual((True, []), tick.eligibility())
        records = tick.freshness_records()
        self.assertEqual('has never succeeded', records[CREC]['detail'])
        self.assertEqual('outputs are torn', records[CHRG]['detail'])
        self.assertEqual('coverage is SUSPECT', records[CNN]['detail'])
        for name in (CREC, CHRG, CNN):
            self.assertEqual(S.DEGRADED, records[name]['status'], name)
        self.assertEqual(S.STALE_LABELLED, records[MTP]['status'])
        # CORE past its window is SERVED and labelled, not a block
        tick.state.record(DISCOVERY, state=S.FAILED, last_ok_utc=ago(20 * 24.0),
                          failure={'kind': S.EXIT, 'exit': 1, 'detail': 'x'},
                          failure_class=S.TRANSIENT)
        self.assertEqual((True, []), tick.eligibility())
        label = tick.freshness_records()[DISCOVERY]
        self.assertEqual(S.DEGRADED, label['status'])
        self.assertIn('past the 14-day window', label['detail'])
        self.assertTrue(label['served_last_good'])
        # ...but CORE integrity stays strict
        for field, value, reason in (('torn', True, 'outputs are torn'),
                                     ('torn', None, 'outputs await complete snapshot '
                                                    'reconciliation'),
                                     ('coverage', S.COVERAGE_SUSPECT, 'coverage is SUSPECT'),
                                     ('last_ok_utc', None, 'has never succeeded')):
            probe = self.tick_for(root)
            probe.state.record(ADMISSIONS, **{field: value})
            eligible, blockers = probe.eligibility()
            self.assertFalse(eligible, field)
            self.assertEqual([(ADMISSIONS, reason)], blockers)
        # an unreadable manifest blocks a CORE source only; an ENRICHMENT one is a label (D-212)
        probe = self.tick_for(root)
        probe.unavailable_manifests.add(DISCOVERY)
        self.assertEqual([(DISCOVERY, 'manifest cannot be read; evidence is unverified')],
                         probe.eligibility()[1])
        probe = self.tick_for(root)
        probe.unavailable_manifests.update([CREC, CNN])
        self.assertEqual((True, []), probe.eligibility())
        records = probe.freshness_records()
        for name in (CREC, CNN):
            self.assertEqual(S.DEGRADED, records[name]['status'], name)
            self.assertIn('UNVERIFIED', records[name]['detail'])
        self.assertEqual(S.FRESH, records[MTP]['status'])
        # ... even when the source has no verified generation at all: labelled missing, compute goes on
        probe.state.record(CREC, last_ok_utc=None, state=S.NEVER)
        self.assertEqual((True, []), probe.eligibility())
        self.assertEqual('has never succeeded', probe.freshness_records()[CREC]['detail'])

    def test_an_unreadable_enrichment_manifest_degrades_the_publication_and_never_blocks_it(self):
        cnn = self.step(CNN)
        root = self.root()
        sha = 'abc123abc123'
        base.seed_state(root, steps={CNN: {'state': S.OK, 'generation': 1, 'manifest_sha': sha,
                                           'last_ok_utc': ago(1.0), 'last_attempt_utc': ago(1.0)}})
        # A DIRECTORY where the manifest belongs: it exists, and it cannot be opened.
        os.makedirs(os.path.join(root, 'data', 'engine', 'refresh_manifests',
                                 '%s.%s.json' % (cnn.slug, sha)), exist_ok=True)
        code, output, _ = base.run_tick(root)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED_DEGRADED, base.outcome(root), output[-3000:])
        self.assertIn('could not be opened', output)
        self.assertEqual(1, base.invocations(root, self.step(R.CHAIN_STEP)),
                         'compute was held for an enrichment source it could still read around')
        self.assertEqual(0, base.invocations(root, cnn))
        entry = base.read_state(root)['steps'][CNN]
        self.assertEqual((S.OK, sha), (entry['state'], entry['manifest_sha']),
                         'the recorded success was erased by a lock window')
        publication = base.read_state(root)['publication']
        self.assertEqual([CNN], publication['stale_sources'])
        self.assertEqual(S.DEGRADED, publication['labels'][CNN]['status'])
        self.assertIn('UNVERIFIED', publication['labels'][CNN]['detail'])
        self.assertEqual(S.DEGRADED, self._freshness(root, CNN)['status'])

    def test_a_compute_step_stands_on_a_core_sources_last_good(self):
        root = self.root()
        base.seed_state(root)
        tick = self.tick_for(root)
        roster = self.step(ROSTER)
        tick.succeeded.append('accepted audio intake')
        tick.state.record(REPOSITORY, state=S.FAILED, last_ok_utc=ago(20 * 24.0),
                          failure={'kind': S.EXIT, 'exit': 1, 'detail': 'x'},
                          failure_class=S.TRANSIENT)
        self.assertEqual((True, None), tick.requirements_met(roster))
        tick.state.record(REPOSITORY, torn=True)
        self.assertEqual((False, REPOSITORY), tick.requirements_met(roster))
        tick.state.record(REPOSITORY, torn=False, last_ok_utc=None)
        self.assertEqual((False, REPOSITORY), tick.requirements_met(roster))
        # the COLLECT phase keeps its strict rule: a press step does not stand on a failed upstream
        tick.state.record(PRESS_DELTA, state=S.FAILED,
                          failure={'kind': S.EXIT, 'exit': 1, 'detail': 'x'})
        self.assertEqual((False, PRESS_DELTA), tick.requirements_met(self.step(PRESS_INTAKE)))

    def test_a_publication_computed_on_stale_core_data_is_labelled(self):
        root = self.root()
        base.seed_state(root)
        tick = self.tick_for(root)
        tick.open_observation()
        tick.state.record(DISCOVERY, state=S.FAILED, last_ok_utc=ago(20 * 24.0),
                          failure={'kind': S.EXIT, 'exit': 1, 'detail': 'x'},
                          failure_class=S.TRANSIENT)
        tick.succeeded.extend([R.CHAIN_STEP])
        tick.skipped_not_configured.add(R.PUBLISH_STEP)
        record = tick.publication_record()
        self.assertTrue(record['degraded'])
        self.assertEqual([DISCOVERY], record['stale_sources'])
        self.assertEqual(S.DEGRADED, record['labels'][DISCOVERY]['status'])
        self.assertIn('past the 14-day window', record['labels'][DISCOVERY]['detail'])

    # =========================================================================================
    # 8  the compute trigger: coalesced, core at once, enrichment after 4 h
    # =========================================================================================
    def _published(self, published_utc, **kwargs):
        root = self.root()
        base.seed_published(root, published_utc=published_utc)
        return root, self.tick_for(root, **kwargs)

    def test_a_core_change_computes_at_once(self):
        root, tick = self._published(ago(0.5))
        self.assertEqual((False, ''), tick.compute_wanted())
        tick.state.record(DISCOVERY, generation=2)
        wanted, why = tick.compute_wanted()
        self.assertTrue(wanted)
        self.assertEqual('new evidence since the last publication: %s' % DISCOVERY, why)

    def test_an_enrichment_change_waits_four_hours_since_the_last_compute(self):
        root, tick = self._published(ago(1.0))
        tick.state.record(CREC, generation=2)
        self.assertEqual((False, ''), tick.compute_wanted())
        self.assertIn('coalescing until 4 h', tick.hold_note)
        self.assertEqual('', tick.hold_blocker)
        # still held when the change was made THIS tick: the gap, not the tick, decides
        tick.succeeded.append(CREC)
        self.assertEqual((False, ''), tick.compute_wanted())
        # five hours on, the same change is wanted
        tick.state.publication()['last_publish_utc'] = ago(5.0)
        wanted, why = tick.compute_wanted()
        self.assertTrue(wanted, why)
        self.assertEqual('', tick.hold_note)
        # the owner forcing that source this tick is a request to publish it
        root, forced = self._published(ago(0.1), force_due=(CREC,))
        forced.state.record(CREC, generation=2)
        forced.succeeded.append(CREC)
        self.assertEqual((True, 'new evidence this tick: %s' % CREC), forced.compute_wanted())
        # a clock that stepped backwards (a negative gap) counts as elapsed
        root, skewed = self._published(ahead(3.0))
        skewed.state.record(CREC, generation=2)
        self.assertTrue(skewed.compute_wanted()[0])

    def test_a_held_enrichment_change_is_not_lost_it_waits_in_the_generation(self):
        root, tick = self._published(ago(1.0))
        tick.state.record(CREC, generation=2)
        self.assertFalse(tick.compute_wanted()[0])
        # the publication still records generation 1, so the tick that finally computes sees it
        self.assertEqual(1, tick.state.publication()['inputs'][CREC]['generation'])
        tick.state.publication()['last_publish_utc'] = ago(4.5)
        self.assertEqual((True, 'new evidence since the last publication: %s' % CREC),
                         tick.compute_wanted())

    def test_a_failed_hard_compute_step_holds_the_phase_by_class(self):
        root, tick = self._published(ago(2.0))
        tick.state.record(DISCOVERY, generation=2)                      # compute is otherwise wanted
        self.assertTrue(tick.compute_wanted()[0])
        intake = self.step(INTAKE)
        failure = {'kind': S.EXIT, 'exit': 1, 'detail': 'x'}
        tick.state.record(INTAKE, state=S.FAILED, failure=failure, failure_class=S.TRANSIENT,
                          attempts_since_ok=1, last_attempt_utc=ago(0.1),
                          next_attempt_utc=ahead(0.2), recent_attempts_utc=[ago(0.1)])
        self.assertEqual((False, ''), tick.compute_wanted())
        self.assertIn('%s: attempted' % INTAKE, tick.hold_blocker)
        self.assertIn('backing off until', tick.hold_blocker)
        tick.state.record(INTAKE, next_attempt_utc=ago(0.01))             # the wait is over
        self.assertTrue(tick.compute_wanted()[0])
        self.assertEqual('', tick.hold_blocker)
        # a soft derived step failing never holds the phase
        tick.state.record(INTAKE, state=S.OK, failure=None, failure_class=None)
        tick.state.record('CU hearings extract', state=S.FAILED, failure=failure,
                          failure_class=S.TRANSIENT, last_attempt_utc=ago(0.1),
                          next_attempt_utc=ahead(0.2))
        self.assertTrue(tick.compute_wanted()[0])
        # DATA_CONTRACT on a gate holds until ANY evidence generation changes
        tick.state.record('CU hearings extract', state=S.OK, failure_class=None)
        signature = tick.input_signature(intake)
        self.assertEqual(set(step.name for step in T.evidence_steps()), set(signature))
        tick.state.record(INTAKE, state=S.FAILED, failure=failure,
                          failure_class=S.DATA_CONTRACT,
                          dead_letter={'at': ago(0.1), 'signature': signature, 'exit': 1})
        self.assertEqual((False, ''), tick.compute_wanted())
        self.assertIn('dead-lettered', tick.hold_blocker)
        tick.state.record(CREC, generation=7)
        wanted, _why = tick.compute_wanted()
        self.assertTrue(wanted)
        self.assertEqual('', tick.hold_blocker)

    def test_a_held_compute_is_reported_not_silent(self):
        root, tick = self._published(ago(2.0))
        tick.state.record(DISCOVERY, generation=2)
        tick.state.record(INTAKE, state=S.FAILED,
                          failure={'kind': S.EXIT, 'exit': 1, 'detail': 'x'},
                          failure_class=S.TRANSIENT, attempts_since_ok=1,
                          last_attempt_utc=ago(0.1), next_attempt_utc=ahead(0.2),
                          recent_attempts_utc=[ago(0.1)])
        tick.open_observation()
        tick.deadline = 10 ** 12
        self.assertEqual(R.COLLECTED_NOT_PUBLISHED, tick._compute_and_publish())
        self.assertEqual('compute held', tick.blockers[0][0])
        self.assertEqual(0, base.invocations(root, self.step(R.CHAIN_STEP)))

    def test_an_enrichment_collapse_is_labelled_and_reported_but_never_blocks(self):
        crec = self.step(CREC)
        root = self.root({base.stub_key(crec): {'rows': [60, 0]}})
        base.seed_published(root)
        code, output, _ = base.run_tick(root, '--only', CREC, '--force-due', CREC,
                                        '--skip-compute')
        self.assertEqual(S.OK, base.read_state(root)['steps'][CREC]['state'], output[-2000:])
        code, output, _ = base.run_tick(root, '--force-due', CREC)
        entry = base.read_state(root)['steps'][CREC]
        self.assertEqual(S.COVERAGE_SUSPECT, entry['coverage'], output[-2000:])
        # a failure THIS tick is never reported as "nothing happened" ...
        self.assertEqual(1, code, output[-2000:])
        self.assertEqual(R.COLLECTED_NOT_PUBLISHED, base.outcome(root))
        # ... yet it holds nothing: the source is a label, and the publication is still permitted
        code, dry, _ = base.run_tick(root, '--dry-run')
        self.assertIn('eligible to publish: True', dry)
        feed = self._freshness(root, CREC)
        self.assertEqual(S.DEGRADED, feed['status'])
        self.assertEqual('coverage is SUSPECT', feed['detail'])

    def test_a_coalesced_enrichment_change_is_quiet_and_says_so(self):
        root = self.root()
        base.seed_published(root)
        edit_state(root, CREC, generation=2)
        code, output, _ = base.run_tick(root)
        self.assertEqual(0, code, output[-2000:])
        self.assertEqual(R.NOTHING_DUE, base.outcome(root))
        self.assertIn('compute phase held: only enrichment changed', output)
        self.assertEqual(0, base.invocations(root, self.step(R.CHAIN_STEP)))
        self.assertEqual(1, base.read_state(root)['publication']['inputs'][CREC]['generation'])

    # =========================================================================================
    # 8b  every way of starting a step asks the SAME barrier (external review 2026-10-15:
    #     _run_dependents skipped the cap, due_now / failure_pending_tick_end ignored it,
    #     compute_hold ignored the unfinished-tick override, a coverage flag reopened a quota window)
    # =========================================================================================
    @staticmethod
    def _fail_transient(tick, name, attempts, **extra):
        fields = dict(state=S.FAILED, failure={'kind': S.EXIT, 'exit': 1, 'detail': 'x'},
                      failure_class=S.TRANSIENT, attempts_since_ok=attempts,
                      last_attempt_utc=ago(0.5), next_attempt_utc=ago(0.1),
                      recent_attempts_utc=[ago(hours) for hours in range(1, attempts + 1)])
        fields.update(extra)
        tick.state.record(name, **fields)

    def _dependents_rig(self):
        root = self.root()
        base.seed_state(root)
        lines, ran = [], []
        tick = self.tick_for(root, log=lines.append)
        tick.run_step = lambda step, phase: ran.append(step.name) or True
        tick.succeeded.append(PRESS_INTAKE)             # the upstream that just recovered
        return tick, ran, lines

    def test_a_recovered_dependent_keeps_the_daily_attempt_cap(self):
        feed = self.step('press engine feed')
        tick, ran, lines = self._dependents_rig()
        recovered = self.step(PRESS_INTAKE)
        self._fail_transient(tick, feed.name, 6)
        # the primary-run gate refuses it, with or without the upstream's new information ...
        self.assertFalse(tick.due(feed)[0])
        self.assertFalse(tick.failure_gate(feed, tick.state.step(feed.name), triggered=True)[0])
        # ... and so must the recovery path
        tick._run_dependents(recovered)
        self.assertEqual([], ran, 'a recovered dependent ran past the six-attempt daily cap')
        self.assertTrue(any('DEPENDENCY RECOVERED' in line and 'not run' in line
                            and 'attempted 6 times' in line for line in lines), lines)
        # inside the cap the recovery IS new information: it skips the backoff WAIT, no more
        self._fail_transient(tick, feed.name, 5, next_attempt_utc=ahead(0.2))
        self.assertFalse(tick.failure_gate(feed, tick.state.step(feed.name))[0])
        tick._run_dependents(recovered)
        self.assertEqual([feed.name], ran)

    def test_a_recovered_dependent_that_was_dead_lettered_waits_for_a_new_generation(self):
        feed = self.step('press engine feed')
        tick, ran, lines = self._dependents_rig()
        recovered = self.step(PRESS_INTAKE)
        signature = tick.input_signature(feed)
        self.assertEqual({PRESS_INTAKE: 1}, signature)
        tick.state.record(feed.name, state=S.FAILED, last_attempt_utc=ago(1.0),
                          failure={'kind': S.EXIT, 'exit': 1, 'detail': 'contract'},
                          failure_class=S.DATA_CONTRACT,
                          dead_letter={'at': ago(1.0), 'signature': signature, 'exit': 1})
        tick._run_dependents(recovered)
        self.assertEqual([], ran, 'a dead-lettered dependent was retried on unchanged input')
        tick.state.record(PRESS_INTAKE, generation=2)           # the upstream really did recover
        tick._run_dependents(recovered)
        self.assertEqual([feed.name], ran)

    def test_a_healthy_dependent_still_runs_when_its_upstream_recovers(self):
        tick, ran, lines = self._dependents_rig()
        tick.state.record('press engine feed', state=S.SKIPPED, skip=S.DEPENDENCY)
        tick._run_dependents(self.step(PRESS_INTAKE))
        self.assertEqual(['press engine feed'], ran)

    def test_no_way_of_starting_a_step_crosses_the_daily_attempt_cap(self):
        mtp = self.step(MTP)
        root = self.root()
        base.seed_state(root)
        exit_failure = {'kind': S.EXIT, 'exit': 1, 'detail': 'x'}
        routes = (
            ('plain failure', dict(state=S.FAILED, failure=exit_failure,
                                   failure_class=S.TRANSIENT), False),
            ('unfinished tick: pending flag', dict(state=S.FAILED, failure=exit_failure,
                                                   failure_class=S.TRANSIENT,
                                                   failure_pending_tick_end=True), False),
            ('unfinished tick: unclassified failure', dict(state=S.FAILED, failure=exit_failure,
                                                           failure_pending_tick_end=True), False),
            ('unfinished tick: died running', dict(state=S.RUNNING), False),
            ('killed', dict(state=S.FAILED, failure={'kind': S.KILLED, 'exit': None,
                                                      'detail': 'console'}), False),
            ('interrupted', dict(state=S.FAILED, failure={'kind': S.INTERRUPTED, 'exit': None,
                                                          'detail': 'died'}), False),
            ('due_now', dict(state=S.FAILED, failure=exit_failure, failure_class=S.TRANSIENT), True),
        )
        for label, fields, due_now in routes:
            for attempts, expected in ((5, True), (6, False)):
                tick = self.tick_for(root)
                tick.state.record(MTP, last_attempt_utc=ago(0.5), next_attempt_utc=ago(0.1),
                                  recent_attempts_utc=[ago(hours)
                                                       for hours in range(1, attempts + 1)],
                                  **fields)
                if due_now:
                    tick.due_now.add(MTP)
                due, why = tick.due(mtp)
                self.assertEqual(expected, due, '%s with %d attempts: %s' % (label, attempts, why))
                if not expected:
                    self.assertIn('attempted 6 times in the last 24 h', why, label)
        # the retry pass asks the same function
        for attempts, expected in ((5, [MTP]), (6, [])):
            ran = []
            tick = self.tick_for(root)
            tick.run_step = lambda step, phase: ran.append(step.name) or True
            self._fail_transient(tick, MTP, attempts)
            tick.filed = [MTP]
            tick.retry_pass()
            self.assertEqual(expected, ran, 'retry pass with %d attempts' % attempts)
        # only an explicit --force-due reaches past it
        forced = self.tick_for(root, force_due=(MTP,))
        self._fail_transient(forced, MTP, 6)
        self.assertEqual((True, 'forced'), forced.due(mtp))

    def test_compute_hold_honours_the_unfinished_tick_override(self):
        root, tick = self._published(ago(2.0))
        tick.state.record(DISCOVERY, generation=2)                  # compute is otherwise wanted
        chain = self.step(R.CHAIN_STEP)
        self._fail_transient(tick, chain.name, 1, next_attempt_utc=ahead(0.25),
                             last_attempt_utc=ago(0.0))

        def agree(expected_open):
            due = tick.due(chain)[0]
            held = tick.compute_hold()[0]
            self.assertEqual(expected_open, due, 'due() disagrees')
            self.assertEqual(expected_open, not held, 'compute_hold() disagrees with due()')

        # an ordinary failure inside its backoff: both hold
        agree(False)
        self.assertEqual((False, ''), tick.compute_wanted())
        self.assertIn(R.CHAIN_STEP, tick.hold_blocker)
        # the same failure committed just before the tick DIED: both are open (the wait is skipped)
        tick.state.record(chain.name, failure_pending_tick_end=True)
        agree(True)
        self.assertTrue(tick.compute_wanted()[0])
        self.assertEqual('', tick.hold_blocker)
        # ... but the override never lifts the daily cap
        self._fail_transient(tick, chain.name, 6, next_attempt_utc=ahead(0.25),
                             failure_pending_tick_end=True)
        agree(False)
        self.assertIn('attempted 6 times', tick.compute_hold()[1])
        # ... nor a data-contract dead letter: the same bytes would be refused again
        tick.state.record(chain.name, failure_class=S.DATA_CONTRACT, recent_attempts_utc=[ago(1)],
                          dead_letter={'at': ago(0.1), 'exit': 1,
                                       'signature': tick.input_signature(chain)})
        agree(False)
        self.assertIn('dead-lettered', tick.compute_hold()[1])
        # a step the previous tick died INSIDE is due now, held or not -- until the cap
        self._fail_transient(tick, chain.name, 1, next_attempt_utc=ahead(0.25))
        tick.due_now.add(chain.name)
        agree(True)
        self._fail_transient(tick, chain.name, 6, next_attempt_utc=ahead(0.25))
        agree(False)
        # an UNCLASSIFIED hard failure (a torn step, a killed one) honours the cap too
        tick.state.record(chain.name, failure_class=None, failure={'kind': S.KILLED, 'exit': None,
                                                                   'detail': 'console'})
        self.assertTrue(tick.compute_hold()[0])
        tick.state.record(chain.name, recent_attempts_utc=[ago(1)])
        self.assertFalse(tick.compute_hold()[0])
        # and once THIS tick has attempted it, its own pending flag is no override at all
        tick.attempted.append(chain.name)
        tick.state.record(chain.name, failure_pending_tick_end=True)
        self.assertEqual('', tick.recovery_override(chain, tick.state.step(chain.name)))

    def test_a_crash_after_a_hard_compute_failure_recovers_immediately(self):
        intake = self.step(INTAKE)
        root = self.root({base.stub_key(intake): {'always_fail': True}})
        base.seed_state(root)
        code, output, _ = base.run_tick(root, **{R.CRASH_ENV: 'after_failed_step_commit'})
        self.assertEqual(137, code, output[-2000:])
        entry = base.read_state(root)['steps'][INTAKE]
        self.assertEqual(S.TRANSIENT, entry['failure_class'])
        self.assertTrue(entry['failure_pending_tick_end'])
        self.assertGreaterEqual(minutes_between(entry['last_attempt_utc'],
                                                entry['next_attempt_utc']), 14.9)
        base.write_control(root, {})
        code, output, _ = base.run_tick(root)
        self.assertEqual(0, code, output[-3000:])
        self.assertEqual(R.PUBLISHED, base.outcome(root), output[-3000:])
        self.assertEqual(2, base.invocations(root, intake))
        self.assertNotIn('compute held', output)

    def test_a_quota_stop_blocks_every_rerun_until_the_next_window_whatever_the_flags(self):
        adm = self.step(ADMISSIONS)
        root = self.root()
        base.seed_state(root)
        exit_failure = {'kind': S.EXIT, 'exit': 1, 'detail': 'x'}
        variants = (
            ('plain quota failure', dict(failure=exit_failure, failure_class=S.BUDGET_QUOTA)),
            ('coverage SUSPECT (the zero floor overwrote the class)',
             dict(coverage=S.COVERAGE_SUSPECT, failure={'kind': S.COVERAGE, 'exit': 1,
                                                        'detail': 'zero floor'})),
            ('torn', dict(torn=True, unverified_files=['data/out/x.csv'], failure=exit_failure)),
            ('unfinished tick', dict(failure=exit_failure, failure_class=S.BUDGET_QUOTA,
                                     failure_pending_tick_end=True)),
            ('killed', dict(failure={'kind': S.KILLED, 'exit': None, 'detail': 'console'})),
            ('due_now', dict(failure=exit_failure, failure_class=S.BUDGET_QUOTA)),
        )
        for label, fields in variants:
            for stopped, expected in ((2.0, False), (23.5, False), (25.0, True)):
                tick = self.tick_for(root)
                tick.state.record(ADMISSIONS, state=S.FAILED, last_ok_utc=ago(30.0),
                                  last_attempt_utc=ago(stopped), quota_stop_utc=ago(stopped),
                                  recent_attempts_utc=[ago(stopped)], **fields)
                if label == 'due_now':
                    tick.due_now.add(ADMISSIONS)
                due, why = tick.due(adm)
                self.assertEqual(expected, due, '%s, stopped %.1f h ago: %s' % (label, stopped,
                                                                                 why))
                if not expected:
                    self.assertIn('one attempt per daily window', why, label)
        # a record from before the field existed is held by its failure class and attempt stamp
        tick = self.tick_for(root)
        tick.state.record(ADMISSIONS, state=S.FAILED, failure=exit_failure,
                          failure_class=S.BUDGET_QUOTA, last_attempt_utc=ago(2.0),
                          quota_stop_utc=None, recent_attempts_utc=[ago(2.0)])
        self.assertFalse(tick.due(adm)[0])
        # a stamp far in the future is clock damage: it must not hold the source for days
        tick.state.record(ADMISSIONS, quota_stop_utc=ahead(30.0), failure_class=None,
                          recent_attempts_utc=[])
        self.assertNotIn('quota', tick.attempt_barrier(adm, tick.state.step(ADMISSIONS))[1])
        # only --force-due reaches past it
        forced = self.tick_for(root, force_due=(ADMISSIONS,))
        forced.state.record(ADMISSIONS, quota_stop_utc=ago(1.0))
        self.assertEqual((True, 'forced'), forced.due(adm))

    def test_a_quota_stop_whose_checkpoint_trips_the_zero_floor_still_holds_the_window(self):
        adm = self.step(ADMISSIONS)
        key = base.stub_key(adm)
        root = self.root()
        base.seed_state(root)
        code, output, _ = base.run_tick(root, '--only', ADMISSIONS, '--skip-compute',
                                        '--force-due', ADMISSIONS)
        self.assertEqual(S.OK, base.read_state(root)['steps'][ADMISSIONS]['state'], output[-2000:])
        base.write_control(root, {key: {
            'always_fail': True, 'write_then_fail': True, 'empty': True,
            'stdout': 'CLASSIFIER_QUOTA_EXHAUSTED: spend limit reached\n'}})
        code, output, _ = base.run_tick(root, '--only', ADMISSIONS, '--skip-compute',
                                        '--force-due', ADMISSIONS)
        entry = base.read_state(root)['steps'][ADMISSIONS]
        self.assertEqual(S.COVERAGE_SUSPECT, entry['coverage'], output[-2000:])
        self.assertEqual(S.COVERAGE, entry['failure']['kind'])        # the zero floor owns the record
        self.assertTrue(entry['quota_stop_utc'], 'the quota window was lost with the class')
        self.assertEqual(2, base.invocations(root, adm))
        # hours later in the same day: coverage SUSPECT used to let it run again
        edit_state(root, ADMISSIONS, last_attempt_utc=ago(2.0))
        base.run_tick(root, '--only', ADMISSIONS, '--skip-compute')
        self.assertEqual(2, base.invocations(root, adm), 'a coverage flag reopened a quota window')
        code, dry, _ = base.run_tick(root, '--dry-run')
        self.assertIn('one attempt per daily window', dry)

    # =========================================================================================
    # 9  the freshness feed
    # =========================================================================================
    def _freshness(self, root, name):
        return read_freshness(root)['sources'][name]

    def _assert_freshness_schema(self, root):
        engine = os.path.join(root, 'data', 'engine')
        feed = read_freshness(root)
        self.assertEqual(S.FRESHNESS_SCHEMA, feed['schema'])
        self.assertTrue(S.parse_stamp(feed['updated_utc']))
        self.assertEqual(14, feed['window_days'])
        self.assertEqual(sorted(step.name for step in T.evidence_steps()), sorted(feed['sources']))
        for name, record in feed['sources'].items():
            step = self.step(name)
            self.assertEqual(sorted(FRESHNESS_FIELDS), sorted(record), name)
            self.assertIn(record['status'], S.FRESHNESS_STATUSES, name)
            self.assertEqual(step.tier, record['tier'], name)
            self.assertEqual(step.cadence_h, record['cadence_h'], name)
            self.assertIsInstance(record['generation'], int)
            self.assertIsInstance(record['attempts_24h'], int)
            self.assertIsInstance(record['partial'], bool)
            self.assertIsInstance(record['served_last_good'], bool)
            if record['last_success_utc'] is None:
                self.assertIsNone(record['age_h'], name)
            else:
                self.assertTrue(S.parse_stamp(record['last_success_utc']), name)
                self.assertIsInstance(record['age_h'], float, name)
        self.assertEqual(set(CORE_NAMES), {name for name, record in feed['sources'].items()
                                           if record['tier'] == 'core'})
        by_status = {}
        for record in feed['sources'].values():
            by_status[record['status']] = by_status.get(record['status'], 0) + 1
        self.assertEqual(by_status, feed['summary']['by_status'])
        self.assertEqual(sorted(name for name, record in feed['sources'].items()
                                if record['status'] != S.FRESH),
                         sorted(feed['summary']['not_fresh']))
        leftovers = [name for name in os.listdir(engine)
                     if name.endswith('.part') or name.startswith('.refresh.tmp')]
        self.assertEqual([], leftovers, 'the feed was not written atomically')

    def test_source_freshness_schema_on_a_quiet_tick(self):
        root = self.root()
        base.seed_published(root)
        code, output, _ = base.run_tick(root)
        self.assertEqual(0, code, output[-2000:])
        self.assertEqual(R.NOTHING_DUE, base.outcome(root))
        self._assert_freshness_schema(root)
        first = read_freshness(root)
        self.assertEqual({'FRESH': 23}, first['summary']['by_status'])
        self.assertEqual({'core': {'FRESH': 6}, 'enrichment': {'FRESH': 17}},
                         first['summary']['by_tier'])
        # written every tick, not only when something ran
        code, output, _ = base.run_tick(root)
        self.assertEqual(0, code, output[-2000:])
        second = read_freshness(root)
        self.assertGreaterEqual(S.parse_stamp(second['updated_utc']),
                                S.parse_stamp(first['updated_utc']))
        self._assert_freshness_schema(root)

    def test_producer_labels_survive_the_tick_rewrite(self):
        # chain_transaction.note_freshness (P2) merges a loader's own STALE_LABELLED verdict into
        # this file under ``sources``; the tick rewrites the file every commit.  The label must
        # survive -- moved to ``producer_labels`` -- and ``sources`` must stay the table's rows.
        root = self.root()
        base.seed_published(root)
        code, output, _ = base.run_tick(root)
        self.assertEqual(0, code, output[-2000:])
        path = os.path.join(root, 'data', 'engine', 'source_freshness.json')
        feed = read_freshness(root)
        label = {'status': 'STALE_LABELLED', 'checked_at': '2026-10-01T00:00:00+00:00',
                 'window_days': 8, 'producer': 'engine/apply_fec_money_refresh.py',
                 'evaluated_utc': '2026-10-15T00:00:00Z'}
        feed['sources']['fec_money'] = label
        with open(path, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(feed, handle)
        for _ in range(2):
            code, output, _ = base.run_tick(root)
            self.assertEqual(0, code, output[-2000:])
            feed = read_freshness(root)
            self._assert_freshness_schema(root)
            self.assertNotIn('fec_money', feed['sources'])
            self.assertEqual(label, feed['producer_labels']['fec_money'])

    def test_freshness_labels_truth_table(self):
        now = S.utcnow()

        def record(tier='core', cadence=24, window=14, **fields):
            entry = S.blank_step()
            entry.update(fields)
            return S.freshness_record(tier, cadence, entry, now, window)

        ok = {'state': S.OK, 'last_ok_utc': ago(2.0)}
        self.assertEqual(S.FRESH, record(**ok)['status'])
        self.assertEqual(S.STALE_LABELLED, record(state=S.OK, last_ok_utc=ago(30.0))['status'])
        failed = {'state': S.FAILED, 'failure': {'kind': S.EXIT, 'exit': 1, 'detail': 'x'},
                  'failure_class': S.TRANSIENT}
        self.assertEqual(S.DEGRADED, record(last_ok_utc=ago(30.0), **failed)['status'])
        # a failed attempt inside the cadence does not make fresh data degraded
        self.assertEqual(S.FRESH, record(last_ok_utc=ago(2.0), **failed)['status'])
        self.assertEqual(S.TRANSIENT, record(last_ok_utc=ago(2.0), **failed)['failure_class'])
        self.assertEqual(S.DEGRADED, record()['status'])                       # never succeeded
        letter = {'at': ago(1.0), 'signature': {}, 'exit': 1}
        dead = dict(failed, failure_class=S.DATA_CONTRACT, dead_letter=letter)
        self.assertEqual(S.DEAD_LETTER, record(last_ok_utc=ago(2.0), **dead)['status'])
        self.assertEqual(S.DEAD_LETTER, record(**dead)['status'])
        self.assertEqual(S.DEGRADED, record(state=S.PARTIAL, last_ok_utc=ago(1.0), torn=True)
                         ['status'])
        # a QUOTA stop is a FAILED step: STALE_LABELLED whatever its age (nothing new was installed)
        quota_stop = dict(failed, failure_class=S.BUDGET_QUOTA,
                          budget_stop={'reason': S.STOP_QUOTA, 'at': ago(1.0)})
        for age in (2.0, 30.0):
            quota = record(last_ok_utc=ago(age), **quota_stop)
            self.assertEqual(S.STALE_LABELLED, quota['status'], age)
            self.assertTrue(quota['served_last_good'])
            self.assertEqual(S.BUDGET_QUOTA, quota['failure_class'])
        never = record(**quota_stop)                   # a source that never succeeded has nothing to serve
        self.assertEqual(S.DEGRADED, never['status'])
        self.assertFalse(never['served_last_good'])
        # an intentional CALL-BUDGET stop is a PASS at the normal cadence
        calls = record(state=S.OK, last_ok_utc=ago(1.0),
                       budget_stop={'reason': S.STOP_CALLS, 'at': ago(1.0)})
        self.assertEqual(S.FRESH, calls['status'])
        self.assertTrue(calls['partial'])
        self.assertEqual(S.STALE_LABELLED, record(state=S.OK, last_ok_utc=ago(30.0),
                                                  budget_stop={'reason': S.STOP_CALLS,
                                                               'at': ago(30.0)})['status'])
        # an unreadable manifest labels the source and never anything more (D-212)
        unreadable = S.freshness_record('enrichment', 168, dict(S.blank_step(), state=S.OK,
                                                                 last_ok_utc=ago(1.0)),
                                        S.utcnow(), 14, manifest_unreadable=True)
        self.assertEqual(S.DEGRADED, unreadable['status'])
        self.assertIn('UNVERIFIED', unreadable['detail'])
        # a KILLED step's stale class from an earlier failure is not reported as the reason
        killed = dict(failed, failure={'kind': S.KILLED, 'exit': None, 'detail': 'console'})
        self.assertIsNone(record(last_ok_utc=ago(30.0), **killed)['failure_class'])
        self.assertEqual(S.DEGRADED, record(last_ok_utc=ago(30.0), **killed)['status'])

    # =========================================================================================
    # 10  the table: daily rows are lifted from the runners they replace, byte for byte
    # =========================================================================================
    def test_the_core_rows_and_the_bluesky_row_carry_the_daily_runners_commands(self):
        source_path = os.path.join(FEC, 'legislation', 'refresh_congress_pipeline.py')
        with open(source_path, encoding='utf-8') as handle:
            tree = ast.parse(handle.read(), filename=source_path)
        constants = {}
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Constant)):
                constants[node.targets[0].id] = node.value.value
        budget_path = os.path.join(FEC, 'admission_budget.py')       # the one place the ruled 80 / 48 are written
        with open(budget_path, encoding='utf-8') as handle:
            ruled = {node.targets[0].id: node.value.value for node in ast.parse(handle.read()).body
                     if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)}
        self.assertEqual((80, 48), (ruled['KEY_LIMIT'], ruled['CALL_BUDGET_DEFAULT']))
        with mock.patch.dict(os.environ):
            os.environ.pop('MELANGE_SENTINEL_CALL_BUDGET', None)
            self.assertEqual(['--limit', str(ruled['KEY_LIMIT']),
                              '--max-classifier-calls', str(ruled['CALL_BUDGET_DEFAULT'])],
                             R.render_argv(T.ADMISSION_TICK_ARGV))   # the ruled default, rendered at launch from the daily runner
        # steps(): every (name, script, args, timeout) tuple, with the discovery IfExp unwrapped
        steps_def = next(node for node in ast.walk(tree)
                         if isinstance(node, ast.FunctionDef) and node.name == 'steps')
        scripts = {}
        for node in ast.walk(steps_def):
            if isinstance(node, ast.Tuple) and len(node.elts) == 4:
                name, script, _args, timeout = node.elts
                if isinstance(script, ast.IfExp):
                    script = script.orelse
                if (isinstance(name, ast.Constant) and isinstance(script, ast.Constant)
                        and isinstance(script.value, str) and isinstance(timeout, ast.Constant)):
                    scripts[name.value] = (script.value, timeout.value)
        expected = {'discovery': DISCOVERY, 'admissions': ADMISSIONS, 'votes': 'Congress rollcall '
                    'source refresh', 'repository': REPOSITORY,
                    'billstatus': 'GovInfo BILLSTATUS bulk refresh'}
        for runner_name, row_name in expected.items():
            script, timeout = scripts[runner_name]
            row = self.step(row_name)
            self.assertEqual(script, row.argv[0], row_name)
            self.assertEqual(timeout, row.timeout_s, row_name)
            self.assertEqual(24, row.cadence_h, row_name)
            self.assertEqual('core', row.tier, row_name)
            if row_name == ADMISSIONS:
                self.assertEqual(T.ADMISSION_TICK_ARGV, row.tick_argv)
            else:
                self.assertEqual((), row.tick_argv, row_name)
        fec_money = self.step('FEC source money refresh')
        self.assertEqual((24, 'core', 7200), (fec_money.cadence_h, fec_money.tier,
                                              fec_money.timeout_s))
        # Bluesky: daily_media_refresh.source_steps() is the source of truth
        media_path = os.path.join(FEC, 'collect', 'daily_media_refresh.py')
        with open(media_path, encoding='utf-8') as handle:
            media_tree = ast.parse(handle.read(), filename=media_path)
        receipt = next(node.value.args[0].value for node in media_tree.body
                       if isinstance(node, ast.Assign)
                       and getattr(node.targets[0], 'id', None) == 'BLUESKY_RECEIPT')
        source_steps = next(node for node in ast.walk(media_tree)
                            if isinstance(node, ast.FunctionDef) and node.name == 'source_steps')
        bluesky = next(call for call in ast.walk(source_steps)
                       if isinstance(call, ast.Call) and getattr(call.func, 'id', None) == 'Step'
                       and isinstance(call.args[0], ast.Constant)
                       and call.args[0].value == 'bluesky')
        self.assertEqual('collect/tweet_collector.py', bluesky.args[1].value)
        literal = [element.value for element in bluesky.args[2].elts
                   if isinstance(element, ast.Constant)]
        self.assertEqual(14400, bluesky.args[3].args[0].value)
        row = self.step(BLUESKY)
        rendered = R.render_argv(row.argv, datetime.date(2026, 10, 15))
        self.assertEqual(bluesky.args[1].value, rendered[0])
        self.assertEqual(literal[:6] + literal[6:8], rendered[1:7] + rendered[8:10])
        self.assertEqual(('2026-10-01', '--pages', '50', '--receipt', receipt),
                         tuple(rendered[7:12]))
        self.assertEqual(14400, row.timeout_s)
        self.assertEqual((24, 'enrichment', True), (row.cadence_h, row.tier, row.tick_only))
        self.assertTrue(row.evidence)

    def test_every_collect_row_has_a_tier_and_no_other_row_does(self):
        self.assertEqual([], T.validate())
        for step in T.TABLE:
            if step.kind in T.COLLECT_KINDS:
                self.assertIn(step.tier, T.TIERS, step.name)
            else:
                self.assertIsNone(step.tier, step.name)
        self.assertEqual(set(CORE_NAMES), {step.name for step in T.TABLE if step.tier == 'core'})
        zero_row = self.step(base.ZERO_ROW_REVIEW)
        self.assertEqual(('enrichment', True, False), (zero_row.tier, zero_row.tick_only,
                                                       zero_row.evidence))
        broken = T.Step('x', ('x.py',), 10, T.SOURCE)
        self.assertIn('needs tier', '; '.join(T.validate((broken,))))
        broken = T.Step('y', ('y.py',), 10, T.DERIVE, tier='core')
        self.assertIn('only collect rows carry a tier', '; '.join(T.validate((broken,))))

    def test_validate_accepts_only_single_element_admission_tokens_in_tick_argv(self):
        good = T.Step('g', ('g.py',), 10, T.SOURCE, tier='core', tick_argv=T.ADMISSION_TICK_ARGV)
        self.assertEqual([], T.validate((good,)))
        for bad_argv in ((('admission_limit', 'extra'),), (('admission_limit', 5),), ((),), (('bogus',),), (['admission_limit'],), (5,)):
            broken = T.Step('b', ('b.py',), 10, T.SOURCE, tier='core', tick_argv=bad_argv)
            self.assertIn('tick_argv must be literal strings or admission tokens', '; '.join(T.validate((broken,))), bad_argv)

    def test_compute_is_always_the_full_chain_never_a_slice(self):
        """DE-016/043/051/055: the daily runner's ``run_pipeline.py --from ...`` slice is NOT lifted."""
        chain = self.step(R.CHAIN_STEP)
        self.assertEqual(('run_pipeline.py',), chain.argv)
        self.assertEqual((), chain.tick_argv)
        for step in T.TABLE:
            if T.producer_basename(step) == 'run_pipeline.py':
                self.assertEqual(R.CHAIN_STEP, step.name)
            for token in step.argv + step.tick_argv:
                self.assertNotIn(token, ('--from', '--only', '--start-at'), step.name)

    def test_the_tick_launches_the_budget_flags_it_was_given(self):
        tick = self.tick_for(self.root())
        seen = {}

        def fake_run(argv, **kwargs):
            seen['argv'] = list(argv)
            return types.SimpleNamespace(timed_out=False, returncode=0, stdout='', stderr='')

        real = R.bounded_subprocess.run_bounded
        R.bounded_subprocess.run_bounded = fake_run
        try:
            tick.index_producers()
            tick._launch(self.step(ADMISSIONS), 60)
        finally:
            R.bounded_subprocess.run_bounded = real
        self.assertEqual(['--limit', '80', '--max-classifier-calls', '48'], seen['argv'][2:])
        self.assertTrue(seen['argv'][1].endswith('refresh_sentinel_admissions.py'))

    def test_the_tick_takes_its_admission_bound_from_the_daily_runner(self):
        import admission_budget
        from legislation import refresh_congress_pipeline as daily
        self.assertEqual([], T.validate())                       # the tokens are legal in tick_argv
        with mock.patch.dict(os.environ):
            os.environ.pop('MELANGE_SENTINEL_CALL_BUDGET', None)
            rendered = R.render_argv(T.ADMISSION_TICK_ARGV)
            daily_argv = dict((name, args) for name, _, args, _ in daily.steps())['admissions']
        self.assertEqual(['--limit', str(admission_budget.KEY_LIMIT), '--max-classifier-calls',
                          str(admission_budget.CALL_BUDGET_DEFAULT)], rendered)
        self.assertEqual(daily_argv, rendered)                   # the same two numbers, from the same module
        self.assertEqual(['--limit', '80', '--max-classifier-calls', '48'], rendered)   # D-211 unchanged

    def _launched_admissions_argv(self):
        tick = self.tick_for(self.root())
        seen = {}

        def fake_run(argv, **kwargs):
            seen['argv'] = list(argv)
            return types.SimpleNamespace(timed_out=False, returncode=0, stdout='', stderr='')

        real = R.bounded_subprocess.run_bounded
        R.bounded_subprocess.run_bounded = fake_run
        try:
            tick.index_producers()
            tick._launch(self.step(ADMISSIONS), 60)
        finally:
            R.bounded_subprocess.run_bounded = real
        return seen.get('argv')

    def test_melange_sentinel_call_budget_is_one_setting_for_the_tick_and_the_daily_runner(self):
        from legislation import refresh_congress_pipeline as daily
        # One setting floods: the key limit rises with the budget (selected keys are capped at the limit), in both runners.
        with mock.patch.dict(os.environ, {'MELANGE_SENTINEL_CALL_BUDGET': '120'}):
            self.assertEqual(['--limit', '120', '--max-classifier-calls', '120'], R.render_argv(T.ADMISSION_TICK_ARGV))
            daily_argv = dict((name, args) for name, _, args, _ in daily.steps())['admissions']
            self.assertEqual(['--limit', '120', '--max-classifier-calls', '120'], daily_argv)   # the daily runner reads the same variable
            self.assertEqual(['--limit', '120', '--max-classifier-calls', '120'], self._launched_admissions_argv()[2:])
        with mock.patch.dict(os.environ, {'MELANGE_SENTINEL_CALL_BUDGET': '60'}):    # below the ruled limit: the limit stays 80
            self.assertEqual(['--limit', '80', '--max-classifier-calls', '60'], R.render_argv(T.ADMISSION_TICK_ARGV))

    def test_an_invalid_call_budget_fails_the_tick_admissions_step_loudly_and_launches_nothing(self):
        for bad in ('0', '-3', 'abc', '', '4.5'):
            with mock.patch.dict(os.environ, {'MELANGE_SENTINEL_CALL_BUDGET': bad}):
                with self.assertRaisesRegex(ValueError, 'MELANGE_SENTINEL_CALL_BUDGET'):
                    R.render_argv(T.ADMISSION_TICK_ARGV)
                with self.assertRaisesRegex(ValueError, 'MELANGE_SENTINEL_CALL_BUDGET'):
                    self._launched_admissions_argv()                 # the producer is never started with a guessed budget

    # =========================================================================================
    # 11  the classifier and its helpers
    # =========================================================================================
    def test_classify_reads_exit_codes_and_log_tails(self):
        cases = [
            ((1, False, '', 'urllib.error.HTTPError: HTTP Error 503: Service Unavailable'),
             S.TRANSIENT, 'HTTP 5xx'),
            ((1, False, '', 'requests.exceptions.ConnectionError: Connection reset by peer'),
             S.TRANSIENT, 'network reset'),
            ((1, False, '', 'PermissionError: [WinError 5] Access is denied'),
             S.TRANSIENT, 'sharing violation'),
            ((1, False, '', "OSError: [WinError 32] The process cannot access the file"),
             S.TRANSIENT, 'sharing violation'),
            ((None, True, 'a contract mismatch that must not count', ''), S.TRANSIENT, 'timeout'),
            ((1, False, '', 'KeyError: something nobody classified'), S.TRANSIENT,
             'unclassified non-zero exit 1 (treated as transient)'),
            ((1, False, 'deferred: SourceUnavailable: the House Code service answered 503', ''),
             S.SOURCE_OUTAGE, 'SourceUnavailable'),
            ((2, False, 'CREC: total outage (zero ingested, >=3 non-404 errors)', ''),
             S.SOURCE_OUTAGE, 'total outage'),
            ((1, False, 'CLASSIFIER_QUOTA_EXHAUSTED: spend limit', ''), S.BUDGET_QUOTA,
             S.STOP_QUOTA),
            ((1, False, '', 'anthropic.RateLimitError: 429 spend limit reached'), S.BUDGET_QUOTA,
             S.STOP_QUOTA),
            # a call-budget stop is a CLEAN exit: the same words on a run that FAILED explain nothing
            ((1, False, 'Run stopped on the classifier call budget (48 call(s))', ''),
             S.TRANSIENT, 'unclassified non-zero exit 1 (treated as transient)'),
            ((1, False, '', 'ValueError: conflicting duplicate FEC transaction'),
             S.DATA_CONTRACT, 'conflicting duplicate'),
            ((1, False, '', 'errors: source WAV contract mismatch for 2026-09-01'),
             S.DATA_CONTRACT, 'contract mismatch'),
            ((1, False, '', 'refused: duplicate House out_wav groups'), S.DATA_CONTRACT,
             'duplicate House out_wav groups'),
            ((1, False, '', 'gold-label set disagrees with the intake'), S.DATA_CONTRACT,
             'gold-label'),
            # precedence: the more specific, less retryable verdict wins
            ((1, False, 'Connection reset by peer\nexact duplicate interview-turn groups', ''),
             S.DATA_CONTRACT, 'exact duplicate'),
            # "refused" alone is a socket, not a contract
            ((1, False, '', 'ConnectionRefusedError: [WinError 10061] connection refused'),
             S.TRANSIENT, 'network reset'),
        ]
        for arguments, klass, reason in cases:
            returncode, timed_out, stdout, stderr = arguments
            self.assertEqual((klass, reason), F.classify(returncode, timed_out, stdout, stderr),
                             arguments)
        # a clean exit's budget stop honours the producer's own tokens only
        self.assertEqual(S.STOP_CALLS, F.budget_stop_reason(
            'Run stopped on the classifier call budget (48 call(s)) at HR1; 12 key(s) left', ''))
        self.assertIsNone(F.budget_stop_reason(
            'CLASSIFIER_QUOTA_EXHAUSTED: spend limit\nRun stopped on the classifier call budget', ''))
        self.assertIsNone(F.budget_stop_reason('the spend limit was never reached', ''))
        self.assertIsNone(F.budget_stop_reason('{"status": "PASS"}', ''))
        tail = F.log_tail('\n'.join(str(index) for index in range(100)), 'tail line\n')
        self.assertEqual(40, len(tail))
        self.assertEqual('tail line', tail[-1])
        self.assertEqual(['x' * F.LINE_LIMIT], F.log_tail('x' * 5000, ''))

    def test_attempts_are_a_rolling_day(self):
        now = S.utcnow()
        recent, almost_a_day, future = ago(1.0), ago(23.0), ahead(0.5)
        stamps = [recent, almost_a_day, ago(25.0), future, ahead(5.0), 'not a stamp']
        self.assertEqual([recent, almost_a_day, future], S.recent_attempts(stamps, now))
        kept = S.record_attempt(stamps, now)
        self.assertEqual(S.stamp(now), kept[-1])
        self.assertEqual(4, len(kept))
        many = [ago(0.01 * index) for index in range(40)]
        self.assertEqual(S.ATTEMPTS_REMEMBERED, len(S.record_attempt(many, now)))

    # =========================================================================================
    # 12  the runner's own files never count as a producer's output
    # =========================================================================================
    def test_the_feed_and_the_dead_letters_are_runner_owned(self):
        self.assertTrue(R.runner_owned('data/engine/source_freshness.json'))
        self.assertTrue(R.runner_owned('data/engine/dead_letter/interviews-mtp.jsonl'))
        self.assertFalse(R.runner_owned('data/engine/press_collection_intake_receipt.json'))

    def test_pythonw_tasks_start_their_producers_under_python_exe(self):
        real = sys.executable
        try:
            folder = self.root()
            for name in ('pythonw.exe', 'python.exe'):
                open(os.path.join(folder, name), 'wb').close()
            sys.executable = os.path.join(folder, 'pythonw.exe')
            self.assertEqual(os.path.join(folder, 'python.exe'), R._child_python())
            self.assertEqual(os.name == 'nt', R._windowless_parent())
            os.remove(os.path.join(folder, 'python.exe'))
            self.assertEqual(os.path.join(folder, 'pythonw.exe'), R._child_python())
            sys.executable = os.path.join(folder, 'python.exe')
            self.assertEqual(os.path.join(folder, 'python.exe'), R._child_python())
            self.assertFalse(R._windowless_parent())
        finally:
            sys.executable = real

    # =========================================================================================
    # 13  the compute engines: a chain container gets the lease for the length of the step
    # =========================================================================================
    def _chain_container_rig(self):
        root = self.root()
        tick = self.tick_for(root)
        tick.open_observation()
        tick.index_producers()
        self.assertTrue(tick.acquire_lease())
        lock = os.path.join(root, '.pipeline_run.lock')
        seen = {}
        spec = R.compute_engine.Launch(['docker', 'compose', 'run', 'chain'], {'ENV': 'compose'},
                                       'melange-chain-test', 'chain')
        reaped = []

        def fake_launch(script, arguments, **kwargs):
            seen['script'] = script
            return spec

        def fake_run(argv, **kwargs):
            seen['argv'], seen['env'] = list(argv), kwargs['env']
            seen['lock_during'] = os.path.exists(lock)
            seen['token_during'] = os.environ.get(pipeline_lock.LOCK_TOKEN_ENV)
            return types.SimpleNamespace(timed_out=False, returncode=0, stdout='ok', stderr='')

        originals = (R.compute_engine.launch, R.compute_engine.reap, R.bounded_subprocess.run_bounded)
        R.compute_engine.launch = fake_launch
        R.compute_engine.reap = reaped.append
        R.bounded_subprocess.run_bounded = fake_run
        return tick, lock, seen, spec, reaped, originals

    def _restore_rig(self, originals):
        (R.compute_engine.launch, R.compute_engine.reap,
         R.bounded_subprocess.run_bounded) = originals

    def test_a_chain_container_takes_the_lease_and_the_tick_takes_it_back(self):
        tick, lock, seen, spec, reaped, originals = self._chain_container_rig()
        try:
            self.assertTrue(os.path.exists(lock))
            run = tick._launch(self.step(R.CHAIN_STEP), 60)
            self.assertEqual('ok', run.stdout)
            self.assertFalse(seen['lock_during'], 'the container could not have taken the lease')
            self.assertIsNone(seen['token_during'])
            self.assertEqual(['docker', 'compose', 'run', 'chain'], seen['argv'])
            self.assertEqual({'ENV': 'compose'}, seen['env'])
            self.assertEqual([spec], reaped)
            self.assertTrue(os.path.exists(lock), 'the tick did not take the lease back')
            self.assertIsNotNone(tick._lease)
            self.assertIsNotNone(os.environ.get(pipeline_lock.LOCK_TOKEN_ENV))
        finally:
            self._restore_rig(originals)
            tick.release_lease()

    def test_losing_the_lease_after_a_chain_container_ends_the_tick_not_the_step(self):
        tick, lock, seen, spec, reaped, originals = self._chain_container_rig()
        real_lock = pipeline_lock.pipeline_run_lock

        def refuse(*args, **kwargs):
            raise RuntimeError('another writer took the lease')

        try:
            tick.state.load()
            pipeline_lock.pipeline_run_lock = refuse
            with self.assertRaises(R.LeaseLost):
                tick.run_step(self.step(R.CHAIN_STEP), R.PHASE_COMPUTE)
            self.assertEqual([spec], reaped)
        finally:
            pipeline_lock.pipeline_run_lock = real_lock
            self._restore_rig(originals)
            tick.release_lease()

    # =========================================================================================
    # 14  the scheduled task registration (parsed, never run)
    # =========================================================================================
    def test_the_registration_script_parses_and_says_what_it_registers(self):
        path = os.path.join(REPO_ROOT, 'ops', 'scheduler', 'register_refresh_tick_task.ps1')
        with open(path, encoding='utf-8') as handle:
            text = handle.read()
        for needle in ("'MelangeRefreshTick'", '<MultipleInstancesPolicy>IgnoreNew', 'PT0S',
                       '<StartWhenAvailable>true', '<TimeTrigger id="hourly">',
                       '<Interval>PT1H</Interval>', '<LogonTrigger', 'EventID=507',
                       'MoneyballCongressRefresh', 'MoneyballWeeklyRefresh',
                       'MoneyballDailyRefreshRetry', '[switch]$Unregister',
                       '[switch]$DisableCongressRefresh', '[switch]$DisableWeeklyRefresh',
                       '[switch]$DisableDailyRetry', 'fec\\refresh_tick.py'):
            self.assertIn(needle, text)
        self.assertNotIn('<Duration>', text, 'the hourly repetition must be indefinite')
        self.assertRegex(text, r'(?m)^\[CmdletBinding\(\)\]')
        shell = shutil.which('powershell') or shutil.which('pwsh')
        if not shell:
            self.skipTest('no PowerShell to parse the script with')
        command = ('$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]'
                   '::ParseFile($args[0],[ref]$t,[ref]$e);'
                   'if($e.Count){$e|ForEach-Object{$_.Message};exit 1}')
        done = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command',
                               "& { %s } '%s'" % (command, path.replace("'", "''"))],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)


def read_ticks_last(root):
    return base.read_ticks(root)[-1]


if __name__ == '__main__':
    unittest.main(verbosity=1)
