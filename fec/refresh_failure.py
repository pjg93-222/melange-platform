# -*- coding: utf-8 -*-
"""What a failed refresh step MEANS, and what the tick runner may do about it.

Owner rulings, 2026-10-15: the refresh never halts the whole system, never retries in a storm
(DE-020) and never puts an AI in the loop (DE-019).  So a failure is classified by DETERMINISTIC
rules over what the producer left behind -- its exit code and the tail of its log -- and each class
carries its own, bounded, answer:

  TRANSIENT      timeout, HTTP 5xx, network reset, sharing violation (WinError 5/32/33), and ANY
                 non-zero exit nothing else claims.  Retried after an exponential backoff of 15
                 minutes doubling to a 4 h cap, at most 6 attempts per step per rolling day.
  SOURCE_OUTAGE  the source itself is down (``SourceUnavailable``, a documented total-outage exit).
                 The last-good generation is kept and served, and the source is probed on its
                 cadence -- not hammered.
  BUDGET_QUOTA   the PROVIDER refused (``CLASSIFIER_QUOTA_EXHAUSTED``, a 429 / spend limit).  The step
                 FAILED -- never a PASS, never a PARTIAL (D-211) -- its generation was not installed,
                 so the last-good one is served and labelled STALE_LABELLED.  Not retried before the
                 next daily window (a retry meets the same limit), then one attempt per window; the
                 producer reuses its checkpoint, so nothing is paid for twice.
  DATA_CONTRACT  the producer refused the DATA (contract, gold-label, intake-duplicate).  Retrying
                 refuses the same bytes again, so the failure is quarantined to
                 ``data/engine/dead_letter/<step>.jsonl`` and alerted, and the step is not run
                 again until an input generation changes (or the owner forces it).

Two things that look alike and are not: an INTENTIONAL call-budget stop (``--max-classifier-calls``,
``CLASSIFIER_CALL_BUDGET``) exits 0 and is a PASS -- it never reaches :func:`classify`, only
:func:`budget_stop_reason` -- and press exit 3 is the table's ``partial_codes``, committed as a
PARTIAL success.  Neither is a failure.

The patterns below are the whole contract.  They were taken from the producers' own messages
(refresh_sentinel_admissions.py, audit_collection.py, fetch_congressional_record.py,
refresh_fec_money.py); a producer that wants a different treatment prints one of these tokens.
Precedence is BUDGET_QUOTA, DATA_CONTRACT, SOURCE_OUTAGE, TRANSIENT: the more specific, less
retryable verdict wins when a log carries both.

Import-safe and stdlib-only: nothing here reads the environment or launches anything.
"""
import json
import os
import re

import refresh_state as state_store
from refresh_state import (BUDGET_QUOTA, DATA_CONTRACT, SOURCE_OUTAGE, STOP_CALLS, STOP_QUOTA,
                           TRANSIENT)

# ---- the numbers the owner ruled ----------------------------------------------------------------
BACKOFF_BASE_MINUTES = 15.0
BACKOFF_CAP_MINUTES = 240.0
MAX_ATTEMPTS_PER_DAY = 6
# A source that is down is probed on its cadence, but never less often than daily.
OUTAGE_PROBE_CAP_HOURS = 24.0
# A provider quota stop is tried once per window: not before, and then once.  The next daily window.
QUOTA_WINDOW_HOURS = 24.0
# The dead letter keeps this many lines of the producer's own output.
LOG_TAIL_LINES = 40
# Classification reads this much of the tail: a budget stop is printed before the producer's final
# summary line, so the scan window is wider than the 40 lines a dead letter keeps.
SCAN_LINES = 200
LINE_LIMIT = 400

_FLAGS = re.IGNORECASE

# Specific to the admissions producer's own two stops.
_QUOTA_SPECIFIC = re.compile(r'CLASSIFIER_QUOTA_EXHAUSTED|Run stopped on classifier quota', _FLAGS)
_CALLS_SPECIFIC = re.compile(
    r'CLASSIFIER_CALL_BUDGET|Run stopped on the classifier call budget', _FLAGS)
# A generic provider word: honoured only because the step has already failed.
_QUOTA_GENERIC = re.compile(r'spend[- ]limit', _FLAGS)

_DATA_CONTRACT = re.compile(
    r'\bcontract (?:violation|mismatch|refus\w*|failed|error)\b'
    r'|\bContract(?:Violation|Error)\b|\bDataContract\b|\bdata[- ]contract\b'
    r'|\bgold[- ]label\b'
    r'|\bintake[- ]duplicate\b|\bexact duplicate\b|\bconflicting duplicate\b'
    r'|\bunexpected/duplicate\b'
    r'|\bduplicate (?:[\w-]+ ){0,3}(?:groups?|keys?|rows?|turns?|transactions?|GUID)\b'
    r'|\bschema (?:mismatch|violation)\b', _FLAGS)

_SOURCE_OUTAGE = re.compile(
    r'SourceUnavailable|SOURCE_UNAVAILABLE|\bsource[ _-]unavailable\b|\btotal outage\b'
    r'|\bmaintenance page\b|\bdocumented outage\b', _FLAGS)

# (label, pattern).  Labels only explain WHY the class is TRANSIENT; an unmatched non-zero exit is
# TRANSIENT as well, under the same caps.
_TRANSIENT_LABELS = (
    ('timeout', re.compile(r'\btimed? ?out\b|TimeoutError|ReadTimeout|ConnectTimeout', _FLAGS)),
    ('HTTP 5xx', re.compile(
        r'(?:HTTP(?:Error)?|status(?: code)?|Server Error)\D{0,12}\b5\d\d\b'
        r'|\b5\d\d (?:Server Error|Bad Gateway|Service Unavailable|Gateway Time-?out)', _FLAGS)),
    ('network reset', re.compile(
        r'Connection ?(?:reset|aborted|refused)|ECONNRESET|RemoteDisconnected|IncompleteRead'
        r'|BrokenPipe|getaddrinfo failed|NameResolutionError|Network is unreachable'
        r'|Temporary failure', _FLAGS)),
    ('sharing violation', re.compile(
        r'WinError (?:5|32|33)\b|PermissionError|sharing violation'
        r'|being used by another process', _FLAGS)),
)


def _lines(stdout, stderr, detail=''):
    out = []
    for text in (stdout, stderr, detail):
        if text:
            out.extend(str(text).splitlines())
    return out


def log_tail(stdout, stderr, detail='', limit=LOG_TAIL_LINES):
    """The last ``limit`` lines the producer wrote (stdout, then stderr), each bounded."""
    return [line[:LINE_LIMIT] for line in _lines(stdout, stderr, detail)[-limit:]]


def _scan_text(stdout, stderr, detail):
    return '\n'.join(_lines(stdout, stderr, detail)[-SCAN_LINES:])


def quota_reason(text):
    """STOP_QUOTA when ``text`` carries a provider quota stop, else None."""
    if _QUOTA_SPECIFIC.search(text) or _QUOTA_GENERIC.search(text):
        return STOP_QUOTA
    return None


def budget_stop_reason(stdout, stderr):
    """STOP_CALLS when a CLEAN exit reports an intentional call-budget stop, else None.

    The admissions producer's ``--max-classifier-calls`` stop is a normal bounded run: it exits 0
    and says so on stdout, and it is a PASS (D-211).  Only its own token counts, and only on a
    clean exit -- a failed run that also mentions the budget failed for another reason, and a
    generic "spend limit" in a log that ended well proves nothing.
    """
    text = _scan_text(stdout, stderr, '')
    if _QUOTA_SPECIFIC.search(text):
        return None
    return STOP_CALLS if _CALLS_SPECIFIC.search(text) else None


def _first(pattern, text):
    match = pattern.search(text)
    return match.group(0).strip()[:80] if match else None


def classify(returncode, timed_out, stdout='', stderr='', detail=''):
    """(failure class, reason) for a step that did not succeed.

    ``detail`` is the runner's own text when the producer never ran (a launch error).  A timeout is
    TRANSIENT whatever the log says: the tree was killed, so nothing in the tail is a verdict.
    """
    if timed_out:
        return TRANSIENT, 'timeout'
    text = _scan_text(stdout, stderr, detail)
    reason = quota_reason(text)
    if reason:
        return BUDGET_QUOTA, reason
    found = _first(_DATA_CONTRACT, text)
    if found:
        return DATA_CONTRACT, found
    found = _first(_SOURCE_OUTAGE, text)
    if found:
        return SOURCE_OUTAGE, found
    for label, pattern in _TRANSIENT_LABELS:
        if pattern.search(text):
            return TRANSIENT, label
    return TRANSIENT, 'unclassified non-zero exit %s (treated as transient)' % (returncode,)


def backoff_minutes(failures, base=BACKOFF_BASE_MINUTES, cap=BACKOFF_CAP_MINUTES):
    """Minutes to wait after the ``failures``-th consecutive failure: 15, 30, 60, 120, 240, 240...

    ``base`` is the first wait; 0 means "no wait" (a test and an operator's escape hatch), never a
    negative or a growing number.
    """
    steps = max(1, int(failures)) - 1
    return max(0.0, min(float(cap), float(base) * (2 ** min(steps, 20))))


def dead_letter_path(directory, step_slug):
    return os.path.join(str(directory), '%s.jsonl' % step_slug)


def append_dead_letter(directory, step_slug, record):
    """One JSON line appended, LF only.  Best effort and False on failure: losing the line must
    not lose the tick -- the state entry carries the same verdict."""
    return state_store.append_line(dead_letter_path(directory, step_slug),
                                   json.dumps(record, ensure_ascii=False, sort_keys=True))
