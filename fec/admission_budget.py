"""The admissions cost bound, in one place for the daily congress runner and the refresh tick.

Owner ruling 2026-10-15 (cap: interactive + $150/month, D-211): at most KEY_LIMIT keys looked at per run, and at most
CALL_BUDGET paid classifier calls among them (a budget stop is a normal PASS; the unattempted keys wait for the next run).

MELANGE_SENTINEL_CALL_BUDGET raises the paid-call budget for a flood run. Unset keeps the ruled default; a value that is
not a positive integer raises ValueError instead of falling back to it, so a typo can never silently under-run a flood or
leave a run unbounded. The daily runner (legislation/refresh_congress_pipeline.py) reads it when it builds its steps, the
tick renders it at launch (refresh_tick.render_argv), so both always agree.

FLOOD CEILING. Admissions never looks at more than --limit keys per run, so a budget above KEY_LIMIT would buy nothing:
the key limit follows the budget, key_limit() = max(KEY_LIMIT, budget). A budget of 120 therefore gives --limit 120
--max-classifier-calls 120 in both the daily runner and the tick, and one setting floods. An explicit --admission-limit on
the daily runner is the operator's word and is not raised.

OUT OF SCOPE: weekly_refresh.py:499-501 runs refresh_sentinel_admissions.py with no arguments, so it keeps that script's own
--limit 32 and no call budget. This module does not reach it.

Stdlib only and no producer imports: refresh_tick.py is forbidden from importing a producer lane (test_T25b).
"""

from typing import Mapping

KEY_LIMIT = 80
CALL_BUDGET_DEFAULT = 48
CALL_BUDGET_ENV = "MELANGE_SENTINEL_CALL_BUDGET"


def key_limit(environ: Mapping[str, str]) -> int:
    return max(KEY_LIMIT, call_budget(environ))


def call_budget(environ: Mapping[str, str]) -> int:
    raw = environ.get(CALL_BUDGET_ENV)
    if raw is None:
        return CALL_BUDGET_DEFAULT
    message = f"{CALL_BUDGET_ENV} must be a positive integer, got {raw[:40]!r}"
    text = raw.strip()
    # int() would also take "1_000", "+5" and non-ASCII digits (Arabic-Indic, fullwidth): only plain ASCII digits are a budget.
    if not (text.isascii() and text.isdigit()):
        raise ValueError(message)
    try:
        value = int(text)
    except ValueError as error:      # a digit string longer than sys.get_int_max_str_digits()
        raise ValueError(message) from error
    if value < 1:
        raise ValueError(message)
    return value
