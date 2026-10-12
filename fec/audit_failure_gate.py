# -*- coding: utf-8 -*-
"""KNOWN Paul-gated audit_full dissent — the ONE parser both unattended gates share.

audit_full.py exits 1 whenever ANY check fails (fec\\audit\\audit_full.py:2214-2217), and the
list below plus its companion pattern list are STANDING open items blocked on Paul's decisions,
not on code. Two independent unattended callers have to decide "is this the known dissent, or
novel breakage?":

  * fec\\weekly_refresh.py  — GATE-1's chain step (run_pipeline's tail is audit_full).
  * fec\\collect\\collector_supervisor.py — GATE-2 runs audit_full directly as its last step.

This module exists so that decision has exactly ONE implementation and ONE list. Duplicating
KNOWN_GATED_FAILURES into the supervisor would make the two gates able to disagree about which
failures are ratified, and a name added in one place would silently not apply in the other.

Everything here fails CLOSED: a report that cannot be read as complete and self-consistent is
treated as novel breakage, because a report we cannot read proves nothing. Import cost is one
regex module — the supervisor imports this on every 10-minute tick.

CALLER OBLIGATION (not enforceable here): tolerating audit_full's verdict is only safe when every
PRODUCER has already run, so the audit's dissent cannot be masking unrun work. weekly_refresh
proves that with its chain-tail guard (_chain_tail_is_audit_full); collector_supervisor's GATE-2
satisfies it by construction — audit_full is its last gate_step and every earlier step's result is
already in the required-dict.
"""
import ast
import re

# audit_full.py is the LAST step of run_pipeline's CHAIN (fec\run_pipeline.py:135) and the LAST
# step of collector_supervisor's GATE-2, so when it is the step that fails, every PRODUCER has
# already succeeded: ranks, workbook and dashboards are freshly written and the only dissent is the
# audit's own verdict. Paul's manual chain runs treat "202 passed, 1 FAILED with the legfix5
# culling item" as the documented healthy baseline, because that promotion is blocked on HIS
# 19-category membership review, not on code. Aborting the whole unattended refresh on it froze the
# UI for five weeks (2026-07-26..08-23).
# Each entry is the EXACT ck() name audit_full prints, copied from source — never inferred:
KNOWN_GATED_FAILURES = (
    # User 2026-09-08, replying to the exact missing rescue-folder question:
    # "Oh backups are done, don't bother". These four old local paths are outside
    # the refresh gate. The audit still reports their absence; this is not backup
    # verification and does not waive loss of any current source or scoring file.
    'A1 repo.rescue_dir still exists',
    'A1 repo.rescue_manifest_a still exists',
    'A1 repo.rescue_manifest_b still exists',
    'A1 repo.rescue_manifest_c still exists',
    'culling surface reports the hash-bound production promotion state',   # fec\audit\audit_full.py:1642
    # ratified by Paul 2026-09-05; schema-only mismatch (the desk adds a display_dedupe block; the
    # 480 texture payloads are byte-identical, dashboard-fixes lane 2026-09-05); UN-GATE when the
    # check is narrowed to the texture payload or the desk stops adding the block; the audit check
    # stays live and visible.
    'personal-texture layer propagates exactly to member profiles (display-only)',
    # ratified by Paul 2026-09-05; today's failure is sources=False intake=False (the press engine
    # feed's source-run and intake bindings), which the sentinel-collectors lane's unmerged repair
    # branch claude/sentinel-collectors-progress-f7de7d addresses; UN-GATE when that branch is
    # merged and build_press_engine_feed.py rebuilt; the audit check stays live and visible.
    'press refresh feed is hash-bound and keeps official mentions out of stance/speech',
)
# DELIBERATELY NOT LISTED (these must keep aborting the refresh AND failing GATE-2):
#   'own-source channel inputs within 28 days of the corpus (channels are default-ON)'
#       — fec\audit\audit_full.py:627. A REAL staleness tripwire: it means a channel sidecar has
#         fallen 28+ days behind the corpus, i.e. the refresh would publish a stale-basis rank.
#   'qualified conversation roles v2 propagate exactly to the desk and remain engine-isolated'
#       — fec\audit\audit_full.py:1489. Never adjudicated as Paul-gated; treat as novel.
# Adding a name here is a change-control decision, not a maintenance edit.

# ratified by Paul 2026-09-05, NARROWED 2026-09-06. The A2 scale pattern is unchanged: a chain run
# still moves a store's size class and file count minutes before its own terminal audit.
#
# The A2b pattern is now an EXPLICIT TWO-STORE LIST, not a store-class alternation, because the
# 2026-09-05 un-gate condition ("exclude chain outputs from sentinel duty") is SATISFIED for every
# store the chain writes into: build_system_manifest's per-store sentinel_excludes/sentinel_newest
# take those paths out of sentinel candidacy, so repo.data_{stance,legislation,lobbying,network,
# members,money,engine} and repo.outputs and local.lda_derived must now go GREEN and any red there
# is NOVEL — most likely a new chain output that needs a sentinel_excludes entry.
#
# The two that remain are NOT chain-driven and were never covered by that condition:
#   local.billstatus — BILLSTATUS-119.receipt.json is written by weekly_refresh's GovInfo bulk
#     collector step; refresh_cosponsor_forecast.py (the only chain step touching that tree) merely
#     READS it (:146-151, :208-221) and puts its receipt under data\engine (path at :85).
#   glassbox.events  — the GLASS BOX runtime's own rolling event stream; no chain step writes it.
# UN-GATE local.billstatus when the collector lane stops pinning its receipt; un-gate
# glassbox.events when the events store's rotation is reflected in its sentinel selection.
# Adding a store to this pattern is a change-control decision, exactly like KNOWN_GATED_FAILURES.
KNOWN_GATED_PATTERNS = (
    re.compile(r'^A2 (?:repo|mdata|local|mirror|draft|glassbox)\.[A-Za-z0-9_.\-]+ (?:size_class agrees|file_count within tolerance)$'),
    re.compile(r'^A2b (?:local\.billstatus|glassbox\.events) sentinels unchanged$'),
)

_FAILURES_RE = re.compile(r'^\s*FAILURES:\s*(\[.*\])\s*$', re.M)
_TALLY_RE = re.compile(r'FULL-BODY AUDIT.{0,8}?(\d+) passed, (\d+) FAILED')


def pipeline_failure_output(script, stdout):
    """Preserve the complete terminal verdict for the scheduled failure gate."""
    stdout=stdout or ''
    if script=='audit_full.py':
        verdict=[line for line in stdout.splitlines()
                 if _TALLY_RE.search(line) or _FAILURES_RE.match(line)]
        if verdict:
            return '\n'.join(verdict)
    return stdout[-800:]


def audit_failures(stdout, stderr):
    """Parse audit_full's FAILURES list out of a caller's captured output.

    Returns (names, reason); names is None whenever the output cannot be read as a COMPLETE and
    SELF-CONSISTENT audit report. run_pipeline echoes only the last 800 chars of the audit's stdout
    (fec\\run_pipeline.py:317), so via GATE-1 a long failure list arrives front-truncated — the
    name-count-vs-tally cross-check below is what catches that. (GATE-2 runs audit_full directly
    and captures all of it, so the same parser is strictly better informed there.) Fail CLOSED."""
    blob = (stdout or '') + '\n' + (stderr or '')
    found = _FAILURES_RE.findall(blob)
    if not found:
        return None, 'no complete "FAILURES: [...]" line in the captured output'
    try:
        names = ast.literal_eval(found[-1])
    except Exception as e:
        return None, 'unparseable FAILURES list (%s)' % type(e).__name__
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        return None, 'FAILURES is not a non-empty list of strings'
    tally = _TALLY_RE.findall(blob)
    if not tally:
        return None, 'no "FULL-BODY AUDIT - N passed, M FAILED" tally in the captured output'
    if int(tally[-1][1]) != len(names):
        return None, ('tally says %s FAILED but %d name(s) parsed (output truncated?)'
                      % (tally[-1][1], len(names)))
    return names, ''


def tolerate_known_gated(stdout, stderr, log=print, prefix='   '):
    """Decide whether a non-zero audit_full result is ONLY the known Paul-gated dissent.

    Returns (tolerate, names). Every uncertain path returns tolerate=False. `log` is the caller's
    own logger so the decision lands in whichever unattended log the operator will actually read."""
    names, reason = audit_failures(stdout, stderr)
    if names is None:
        log('%snot a readable audit failure report (%s) — abort stands' % (prefix, reason))
        return False, []
    novel = [n for n in names if n not in KNOWN_GATED_FAILURES and not any(p.match(n) for p in KNOWN_GATED_PATTERNS)]
    if novel:
        log('%sNOVEL audit failure(s) — abort stands:' % prefix)
        for n in novel:
            log('%s  NOVEL: %s' % (prefix, n))
        return False, names
    log('%sknown Paul-gated dissent only — tolerating:' % prefix)
    for n in names:
        tag = '' if n in KNOWN_GATED_FAILURES else ' (pattern)'
        log('%s  %s%s' % (prefix, n, tag))
    return True, names
