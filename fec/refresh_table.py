# -*- coding: utf-8 -*-
"""The refresh step table: the 49 steps of the weekly refresh, as PURE DATA.

This is the same plan ``fec/weekly_refresh.py`` executes -- same steps, same order, same names,
same argv, same timeouts, same hard/soft -- lifted out of that script's control flow so a runner
can decide WHICH of them are due instead of replaying all 49 from the top.  Nothing here runs,
reads or writes anything: no I/O, no subprocess, no environment, stdlib typing/dataclasses only.
``fec/tests/infra/test_refresh_tick.py``'s table pin (T12) parses ``weekly_refresh.py``'s
``step()`` calls by AST and asserts this sequence still equals it, so the two cannot drift while
both exist.

WHY A TABLE.  weekly_refresh.py is one run of 49 steps that either finishes or is lost: its worst
path budgets 76.4 h, this laptop dies about every two days, and it has not completed since
2026-07-19.  A table plus per-step state turns that into short ticks: a crash costs one step.

DATE ARGUMENTS ARE TOKENS.  weekly_refresh.py computes ``today``/``yesterday`` once at the top of
its run and interpolates them into argv.  A table cannot hold a date without going stale, so the
date arguments are tokens -- ``('days_ago', 14)``, ``('yesterday',)`` -- that the runner renders
at LAUNCH time, in LOCAL date, exactly as the script does (``datetime.date.today()``).

FIELDS

``name``       the step's name, verbatim from weekly_refresh.py; the state key and the log label.
``argv``       argv as weekly_refresh.py passes it, with dates as tokens.  ``argv[0]`` stays the
               string the script uses (``'collect/fetch_congressional_record.py'``); the runner
               resolves it the same way -- a basename index over ``fec/`` restricted to the
               table's own producers, where a duplicated real producer is fatal before step 1.
``timeout_s``  the step's own bound, verbatim from weekly_refresh.py.
``kind``       which phase the step belongs to and how it is treated; see below.
``hard``       verbatim from weekly_refresh.py.  In the COLLECT phase it has no power at all (a
               collector failure is filed and every other collector still runs); in the COMPUTE
               phase it keeps its original meaning -- a hard failure stops that phase.  The two
               are not in conflict: a failed press step still blocks publication, because press
               rows are EVIDENCE rows and the compute phase only runs on complete evidence.
``evidence``   this step's output is an input to publication.  Every evidence row must be present,
               intact and inside the vintage window before anything is computed or published.
``cadence_h``  how often the step is wanted (168 h = weekly for every collector).  None means the
               step is not cadence-driven: it runs when the compute phase runs.
``requires``   steps that must be satisfied first.  This is the ONLY thing one step may do to
               another; everything not named here runs regardless.
``gate``       name of the runner's tolerance function for this step (see refresh_tick.GATES).
``skip_if``    name of the runner's configuration predicate (see refresh_tick.SKIPS); a step it
               refuses is SKIPPED_NOT_CONFIGURED, which is NOT a failure.
``partial_codes``  exit codes this producer uses to mean "ran to the end, some work deferred".
               Measured 2026-09-13: the press walk ran 3 h 13 m, exited 3, and left 2,200 URLs in
               9 members unattempted because the 600 s per-member budget ran out.  Retrying that
               inside one tick would burn three three-hour walks for nothing, and the daily lane
               already reads rc 3 as PARTIAL (fec/collect/daily_media_refresh.py:410-417).  A
               partial exit is COMMITTED like a success -- manifest, last_ok_utc, generation --
               satisfies ``requires``, counts as fresh, and is NOT retried; whether the deferred
               work is acceptable is the downstream press gates' decision, not the runner's.
``tick_only``  this row exists only in the tick runner and has no counterpart in
               weekly_refresh.py.  The T12 pin compares the ``tick_only == False`` rows with main.
``tier``       (collect rows only) how publication treats this source, owner ruling 2026-10-15.
               ``core`` -- bills/discovery, admissions, rollcall votes, BILLSTATUS, the bill
               repository and FEC money -- stays STRICT: it must have a last-good generation, intact
               and whole, before anything is computed, but past its window that last-good is SERVED
               and LABELLED instead of stopping the system.  ``enrichment`` -- press, interviews,
               Wikipedia, Bluesky, CREC/CHRG -- degrades with labels and can never hold a
               publication.  The tier is what ``data/engine/source_freshness.json`` reports.
``tick_argv``  arguments the TICK appends to ``argv`` that weekly_refresh.py does not pass.  They are
               kept apart from ``argv`` so the T12 pin still compares the weekly script's command
               verbatim; a second test pins them to the daily congress runner they were lifted from.

KINDS

``source``  (1-18)  the scheduled collectors.  Evidence, soft, isolated, filed on failure, retried.
``press``   (19-22) the press transaction.  Evidence; ordered internally by ``requires``.
``gate``    (23-25) the collection-integrity gates; hard, and the first steps of the COMPUTE phase.
``derive``  (26-39, 41) short derived layers; soft.
``compute`` (40, 42) the reference layer and the full chain; hard.
``publish`` (43) dashboard deployment; hard, skipped when deployment is not configured on this box.
``diag``    (44-49) post-chain diagnostics; soft, and only after the chain succeeded in that tick.
"""
from dataclasses import dataclass, field
from typing import Optional, Tuple, Union

SCHEMA = 1

SOURCE = 'source'
PRESS = 'press'
GATE = 'gate'
DERIVE = 'derive'
COMPUTE = 'compute'
PUBLISH = 'publish'
DIAG = 'diag'

# The two phases.  Everything in COLLECT_KINDS is isolated from everything else; everything in
# COMPUTE_KINDS is one transaction that either reaches publication or is retried next tick.
COLLECT_KINDS = (SOURCE, PRESS)
COMPUTE_KINDS = (GATE, DERIVE, COMPUTE, PUBLISH, DIAG)

# Weekly, the cadence the whole system was synchronized on; daily for the congress sources the
# daily pipeline used to refresh (owner ruling 2026-10-15: the tick is the single orchestrator).
WEEK_HOURS = 168
DAY_HOURS = 24

# Publication tiers (see ``tier`` above).
CORE = 'core'
ENRICHMENT = 'enrichment'
TIERS = (CORE, ENRICHMENT)

# The admissions producer's cost bound, lifted from fec/legislation/refresh_congress_pipeline.py
# (ADMISSION_KEY_LIMIT, ADMISSION_CALL_BUDGET; owner ruling 2026-10-15, cap interactive + $150 a
# month).  --limit bounds the keys looked at, --max-classifier-calls the PAID model calls among
# them; a budget stop is a normal PASS and the unattempted keys wait for the next day.  A tick that
# ran admissions without these would be an unbounded spend on an hourly schedule.
ADMISSION_TICK_ARGV = ('--limit', '80', '--max-classifier-calls', '48')

# argv element: a literal string, or a date token the runner renders at launch.
Token = Tuple[Union[str, int], ...]
Arg = Union[str, Token]


@dataclass(frozen=True)
class Step(object):
    """One row.  Frozen: the table is data, and a runner that mutated it would be lying."""

    name: str
    argv: Tuple[Arg, ...]
    timeout_s: int
    kind: str
    hard: bool = False
    evidence: bool = False
    cadence_h: Optional[int] = None
    requires: Tuple[str, ...] = ()
    gate: Optional[str] = None
    skip_if: Optional[str] = None
    partial_codes: Tuple[int, ...] = ()
    tick_only: bool = False
    tier: Optional[str] = None
    tick_argv: Tuple[Arg, ...] = ()

    @property
    def slug(self) -> str:
        return slug(self.name)


def slug(name: str) -> str:
    """A filesystem-safe, stable key for one step name.

    Used for the manifest filenames, so it must not change once a manifest exists.  Lower-case,
    every run of non-alphanumeric characters collapsed to one hyphen.  Uniqueness across the table
    is checked by :func:`validate`, not assumed.
    """
    out = []
    for char in str(name).lower():
        if char.isalnum() and char.isascii():
            out.append(char)
        elif out and out[-1] != '-':
            out.append('-')
    return ''.join(out).strip('-')


# ---------------------------------------------------------------------------------------------
# THE TABLE.  Order is weekly_refresh.py's order; changing either without the other breaks T12.
# ---------------------------------------------------------------------------------------------
TABLE: Tuple[Step, ...] = (
    # ---- 1-18  scheduled source collectors -------------------------------------------------
    # Every one of these is incremental and checkpointed (per-URL or per-day 'ok' markers), so a
    # re-run after a failure is cheap and safe.  In weekly_refresh.py a failure here was appended
    # to one _deferred_collection_failures list that hard-aborted after the press transaction --
    # one collector losing its network for a minute threw away every other leg's completed work.
    Step('CREC floor speeches',
         ('collect/fetch_congressional_record.py', 'crec', '--since', ('days_ago', 14)),
         3000, SOURCE, evidence=True, cadence_h=WEEK_HOURS, tier=ENRICHMENT),
    # Hearing transcripts publish MONTHS late (coverage audit 2026-07-06), hence the 400-day window.
    Step('CHRG finance hearings',
         ('collect/fetch_congressional_record.py', 'chrg', '--since', ('days_ago', 400)),
         3000, SOURCE, evidence=True, cadence_h=WEEK_HOURS, tier=ENRICHMENT),
    # The six CORE congress sources run DAILY, as refresh_congress_pipeline.py ran them: same
    # producers, same timeouts (a test pins both to that script).  FEC money moves weekly -> daily.
    Step('Congress.gov bill discovery',
         ('legislation/refresh_legislation_discovery.py',),
         21600, SOURCE, evidence=True, cadence_h=DAY_HOURS, tier=CORE),
    Step('Official bill evidence and automatic scoring admission',
         ('legislation/refresh_sentinel_admissions.py',),
         14400, SOURCE, evidence=True, cadence_h=DAY_HOURS, tier=CORE,
         tick_argv=ADMISSION_TICK_ARGV),
    Step('FEC source money refresh',
         ('collect/refresh_fec_money.py',),
         7200, SOURCE, evidence=True, cadence_h=DAY_HOURS, tier=CORE),
    Step('Congress rollcall source refresh',
         ('legislation/refresh_vote_sources.py',),
         1800, SOURCE, evidence=True, cadence_h=DAY_HOURS, tier=CORE),
    Step('Congress.gov bill repository',
         ('legislation/update_bill_repository.py',),
         14400, SOURCE, evidence=True, cadence_h=DAY_HOURS, tier=CORE),
    Step('GovInfo BILLSTATUS bulk refresh',
         ('legislation/refresh_billstatus_bulk.py',),
         3600, SOURCE, evidence=True, cadence_h=DAY_HOURS, tier=CORE),
    Step('interviews: CNN (14d)',
         ('fetch_interviews.py', 'cnn', '--since', ('days_ago', 14), '--until', ('yesterday',),
          '--strict'),
         1800, SOURCE, evidence=True, cadence_h=WEEK_HOURS, tier=ENRICHMENT),
    Step('interviews: MTP', ('fetch_interviews.py', 'mtp', '--strict'),
         1200, SOURCE, evidence=True, cadence_h=WEEK_HOURS, tier=ENRICHMENT),
    Step('interviews: CBS', ('fetch_interviews.py', 'cbs', '--strict'),
         1200, SOURCE, evidence=True, cadence_h=WEEK_HOURS, tier=ENRICHMENT),
    Step('interviews: ABC', ('fetch_interviews.py', 'abc', '--strict'),
         1200, SOURCE, evidence=True, cadence_h=WEEK_HOURS, tier=ENRICHMENT),
    Step('interviews: PBS (21d)',
         ('fetch_interviews.py', 'pbs', '--since', ('days_ago', 21), '--until', ('yesterday',),
          '--strict'),
         1800, SOURCE, evidence=True, cadence_h=WEEK_HOURS, tier=ENRICHMENT),
    Step('interviews: NPR (14d)',
         ('fetch_interviews.py', 'npr', '--since', ('days_ago', 14), '--until', ('yesterday',),
          '--strict'),
         1800, SOURCE, evidence=True, cadence_h=WEEK_HOURS, tier=ENRICHMENT),
    Step('interviews: Fox archive audit', ('fetch_interviews.py', 'fox', '--strict'),
         1800, SOURCE, evidence=True, cadence_h=WEEK_HOURS, tier=ENRICHMENT),
    Step('interviews: MS NOW', ('fetch_interviews.py', 'msnow', '--strict'),
         3600, SOURCE, evidence=True, cadence_h=WEEK_HOURS, tier=ENRICHMENT),
    Step('interviews: Washington Journal (21d)',
         ('refresh_cspan_wj.py', '--since', ('days_ago', 21), '--until', ('yesterday',)),
         5400, SOURCE, evidence=True, cadence_h=WEEK_HOURS, tier=ENRICHMENT),
    Step('Wikipedia biographies (full roster)', ('wikipedia_biographies.py', '--strict'),
         1800, SOURCE, evidence=True, cadence_h=WEEK_HOURS, tier=ENRICHMENT),
    # TICK-ONLY.  The daily Bluesky leg of fec/collect/daily_media_refresh.py (source_steps(): the
    # same producer, flags, 14-day window, receipt path and 14400 s bound).  Its extract is the
    # existing 'CU bluesky extract' DERIVE row, which already runs in every compute phase, so only
    # the collector is new.  ``daily_media_refresh`` stretches the window back to the last success
    # after a long gap; the tick keeps the fixed 14 days every other windowed collector here uses.
    Step('Bluesky collector',
         ('collect/tweet_collector.py', 'fetch', '--platform', 'bluesky', '--verified-only',
          '--strict', '--since', ('days_ago', 14), '--pages', '50',
          '--receipt', 'data/engine/social_source_receipts/bluesky.json'),
         14400, SOURCE, evidence=True, cadence_h=DAY_HOURS, tier=ENRICHMENT, tick_only=True),
    # ---- 19-22  the press transaction ------------------------------------------------------
    # hard=True here is weekly_refresh.py's value and is kept verbatim, but in the collect phase
    # it stops nothing: press failure blocks publication through EVIDENCE, not through an exit.
    # 'press intake audit' audits what is ON DISK, so the runner runs it whenever 'press delta
    # refresh' was ATTEMPTED in the tick, not only when it succeeded (refresh_tick.ATTEMPT_TRIGGERED).
    Step('press delta refresh', ('refresh_press_stream.py',),
         129600, PRESS, hard=True, evidence=True, cadence_h=WEEK_HOURS, partial_codes=(3,),
         tier=ENRICHMENT),
    Step('press intake audit',
         ('audit_collection.py', '--scope', 'press',
          '--output', 'data/engine/press_collection_intake_receipt.json'),
         1800, PRESS, hard=True, evidence=True, cadence_h=WEEK_HOURS,
         requires=('press delta refresh',), tier=ENRICHMENT),
    Step('press engine feed',
         ('build_press_engine_feed.py',
          '--intake-receipt', 'data/engine/press_collection_intake_receipt.json'),
         1800, PRESS, hard=True, evidence=True, cadence_h=WEEK_HOURS,
         requires=('press intake audit',), tier=ENRICHMENT),
    Step('fold promoted press runs', ('fold_promoted_press_runs.py',),
         1800, PRESS, hard=True, evidence=True, cadence_h=WEEK_HOURS,
         requires=('press engine feed',), tier=ENRICHMENT),
    # TICK-ONLY.  No counterpart in weekly_refresh.py, so the T12 pin skips it.  Reviews the
    # zero-row members the fold just promoted; evidence=False and soft, so it reports without
    # ever being able to hold publication.  Its producer is the press lane's; a table producer
    # that is not on disk is a FILED failure of this one step, never a fatal pre-step error.
    Step('press zero-row review', ('collect/press_zero_row_review.py',),
         600, PRESS, cadence_h=WEEK_HOURS, requires=('fold promoted press runs',),
         tick_only=True, tier=ENRICHMENT),
    # ---- 23-25  collection-integrity gates: the first steps of the COMPUTE phase ------------
    Step('collection intake audit',
         ('audit_collection.py', '--scope', 'all',
          '--output', 'data/engine/collection_intake_receipt.json'),
         1800, GATE, hard=True),
    Step('accepted audio intake', ('b5_audio_manifest_adapter.py',),
         3600, GATE, hard=True, requires=('collection intake audit',)),
    # Only a complete bill repository may update the verified collector-to-ledger sidecar.
    Step('collector roster verification', ('legislation/verify_collector_roster_updates.py',),
         900, GATE, hard=True,
         requires=('accepted audio intake', 'Congress.gov bill repository')),
    # ---- 26-39  derived layers -------------------------------------------------------------
    Step('CU hearings extract', ('stance/extract_cu_hearings.py',), 600, DERIVE),
    Step('CU interviews extract', ('stance/extract_cu_interviews.py',), 600, DERIVE),
    Step('CU bluesky extract', ('stance/extract_cu_bluesky.py',), 600, DERIVE),
    Step('Big Five traits', ('big5_traits.py',), 1800, DERIVE),
    Step('interaction features', ('interaction_features.py',), 900, DERIVE),
    Step('behavior panel', ('behavior_panel.py',), 900, DERIVE),
    Step('behavioral dispositions', ('behavioral_dispositions.py',), 900, DERIVE),
    Step('Big5 v2 (behavior-calibrated)', ('big5_lexcal_v2.py',), 900, DERIVE),
    Step('Big5 v3 (hybrid)', ('big5_hybrid.py',), 900, DERIVE),
    Step('social interaction layer', ('social_interaction_layer.py',), 900, DERIVE),
    Step('press engagement layer', ('press_engagement_layer.py',), 600, DERIVE),
    Step('press HCoI layer', ('press_hcoi.py',), 600, DERIVE),
    Step('multilayer network', ('build_multilayer_network.py',), 900, DERIVE),
    Step('co-appearance layer', ('co_appearance_layer.py',), 600, DERIVE),
    # ---- 40-43  the chain and its publication ----------------------------------------------
    Step('reference layer (classify new evidence)',
         ('member_reference_layer.py', '--score', '--go'),
         14400, COMPUTE, hard=True, requires=('collector roster verification',)),
    Step('press stance stack', ('press_stance_stack.py',), 1800, DERIVE),
    # The one gated step: run_pipeline's tail is audit_full, so a NON-ZERO exit may be tolerated
    # when every failing check is in audit_failure_gate.KNOWN_GATED_FAILURES and the chain tail is
    # still audit_full.py.  Anything novel stays a hard failure.  See refresh_tick.GATES.
    Step('FULL PIPELINE + dashboards', ('run_pipeline.py',),
         7200, COMPUTE, hard=True, gate='audit_failure_gate',
         requires=('reference layer (classify new evidence)',)),
    Step('prepare dashboard deployment', ('surfaces/publish_dashboard.py',),
         1800, PUBLISH, hard=True, requires=('FULL PIPELINE + dashboards',),
         skip_if='deployment_not_configured'),
    # ---- 44-49  post-chain diagnostics -----------------------------------------------------
    # The chain consumes none of these; they feed the desk External row.  Soft, and only after
    # 'FULL PIPELINE + dashboards' has succeeded in the SAME tick.
    Step('signed coalitions (diag)', ('signed_coalitions.py',), 900, DIAG),
    Step('KB benchmark (diag)', ('kb_benchmark.py',), 900, DIAG),
    Step('LEP criterion (diag)', ('lep_criterion.py',), 900, DIAG),
    Step('say-do gap (diag)', ('say_do_gap.py',), 900, DIAG),
    Step('financial_other discovery (review sheet)', ('financial_other_discovery.py',),
         1800, DIAG),
    Step('forecast ledger (freeze+score)', ('forecast_ledger.py',), 900, DIAG),
)


def by_name(table=TABLE):
    """{name: Step} in table order."""
    return {step.name: step for step in table}


def evidence_steps(table=TABLE):
    """The rows publication depends on, in table order."""
    return tuple(step for step in table if step.evidence)


def collect_steps(table=TABLE):
    return tuple(step for step in table if step.kind in COLLECT_KINDS)


def compute_steps(table=TABLE):
    return tuple(step for step in table if step.kind in COMPUTE_KINDS)


def pinned_steps(table=TABLE):
    """The rows that must still equal weekly_refresh.py's ``step()`` sequence (T12)."""
    return tuple(step for step in table if not step.tick_only)


def producer_basename(step):
    return step.argv[0].replace('\\', '/').rsplit('/', 1)[-1]


def producer_names(table=TABLE):
    """The set of producer basenames this table launches -- and NOTHING else.

    weekly_refresh.py indexes exactly this set: on 2026-09-10 two unrelated ``__init__.py`` under
    ``fec/`` made a walk over every .py a duplicate-basename error, and the refresh died in 0.2 s
    before step 1 (main 61be75ddc fixed it the same way).
    """
    return {producer_basename(step) for step in table}


def validate(table=TABLE):
    """Return a list of problems with the table.  Empty list = the table is self-consistent.

    Pure: it inspects the rows and nothing else.  The runner calls it before step 1 so a mistyped
    ``requires`` is a named pre-step failure and not a step that silently never runs.
    """
    problems = []
    names = [step.name for step in table]
    if len(set(names)) != len(names):
        problems.append('duplicate step name(s): %s'
                        % sorted({n for n in names if names.count(n) > 1}))
    slugs = [slug(step.name) for step in table]
    if len(set(slugs)) != len(slugs):
        problems.append('slug collision(s): %s' % sorted({s for s in slugs if slugs.count(s) > 1}))
    position = {name: index for index, name in enumerate(names)}
    for index, step in enumerate(table):
        if step.kind not in COLLECT_KINDS + COMPUTE_KINDS:
            problems.append('%s: unknown kind %r' % (step.name, step.kind))
        if not isinstance(step.timeout_s, int) or step.timeout_s <= 0:
            problems.append('%s: timeout_s is %r' % (step.name, step.timeout_s))
        if not step.argv or not isinstance(step.argv[0], str):
            problems.append('%s: argv[0] is not a producer path' % step.name)
        if step.evidence and not step.cadence_h:
            problems.append('%s: evidence rows need a cadence' % step.name)
        tier = getattr(step, 'tier', None)
        tick_argv = getattr(step, 'tick_argv', ())
        if step.kind in COLLECT_KINDS and tier not in TIERS:
            problems.append('%s: a collect row needs tier core or enrichment, got %r'
                            % (step.name, tier))
        if step.kind in COMPUTE_KINDS and tier is not None:
            problems.append('%s: only collect rows carry a tier, got %r' % (step.name, tier))
        if tick_argv and not all(isinstance(item, str) for item in tick_argv):
            problems.append('%s: tick_argv must be literal strings' % step.name)
        if any((not isinstance(code, int)) or code == 0 for code in step.partial_codes):
            problems.append('%s: partial_codes must be non-zero ints, got %r'
                            % (step.name, step.partial_codes))
        for required in step.requires:
            if required not in position:
                problems.append('%s: requires unknown step %r' % (step.name, required))
            elif position[required] == index:
                problems.append('%s: cannot require itself' % step.name)
            elif position[required] > index:
                problems.append('%s: requires %r, which runs later' % (step.name, required))
    return problems
