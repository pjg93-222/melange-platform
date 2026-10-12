#!/usr/bin/env python3
"""The single canonical-measure-key contract for the legislation lane.

Before this module existed the lane carried sixteen independent definitions of
"what a canonical measure key looks like", in three mutually incompatible
dialects:

* **Strict** (most gates and builders)::

      ^(116|117|118|119):(hr|hres|hjres|hconres|hamdt|s|sres|sjres|sconres|samdt):[1-9]\\d*$

* **Permissive** (two collectors: ``collect_govinfo_bill_xml_versions`` and
  ``collect_official_rollcall_events``)::

      ^\\d{1,3}:[a-z]+:\\d+$

  which accepts any Congress, any lowercase measure type, and the numbers
  ``0`` and ``007``.

* **Case-insensitive** (``build_acu_bill_title_corroboration_query_plan``),
  which uniquely accepted ``117:HR:1`` as well as ``117:hr:1``.

Two consequences followed.  First, the collectors minted keys that every
downstream gate rejects, so a bad key surfaced only far downstream as a
"contract violation" rather than at the point of ingestion.  Second, the
hardcoded ``116|117|118|119`` alternation was a dated bomb: the 120th Congress
seats 2027-01-03, and eight separate regular expressions had to be edited in
lockstep or the lane would silently reject every new measure.

**Chosen semantics: strict, case-sensitive, lowercase.**  The collectors were
tightened to match the gates rather than the gates loosened to match the
collectors, because:

* the gates are the authority on what the corpus may contain, so anything a
  collector accepts but a gate rejects is guaranteed waste or silent loss;
* ``0`` and leading zeros are not real measure numbers, and admitting both
  ``117:hr:7`` and ``117:hr:007`` creates two keys for one measure, which
  breaks every join and dedup in the lane;
* ``[a-z]+`` admits measure types that do not exist, so a typo became a new
  measure instead of an error;
* canonical keys are lowercase by contract, and case-insensitive matching
  admits ``117:HR:1`` as a second key for the same measure.  Callers that must
  tolerate mixed-case input normalize explicitly via
  :func:`normalize_canonical_key` / :func:`parse_canonical_key`, which is a
  visible decision rather than an accident of one regex flag.

All 18,557 canonical keys currently held in
``congress_advanced_records.ndjson`` / ``congress_advanced_memberships.ndjson``
satisfy the strict form, so tightening the collectors rejects nothing that the
lane actually holds.

Seating the 120th Congress
--------------------------
Bump :data:`LAST_SUPPORTED_CONGRESS` to ``120``.  That is the only edit this
module needs.  Two *coupled* edits live outside it and are not automatic:

* the ``EXPECTED_DECAY`` maps in ``assemble_approved_legislation_generation``,
  ``build_accepted_legislation_workbook`` and
  ``build_legislation_workbook_numeric_plan`` need a ``120`` entry (decay
  weights are a user-owned decision, not a derived constant);
* ``collect_govinfo_bill_xml_versions.CONGRESS_ORDINAL_NAMES`` needs
  ``"120": "One Hundred Twentieth"``.

This module is deliberately side-effect free.  It reads nothing, writes
nothing, and imports only ``re``.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence


# --- the one dial -----------------------------------------------------------
FIRST_SUPPORTED_CONGRESS = 116
LAST_SUPPORTED_CONGRESS = 119  # bump to 120 on 2027-01-03; see module docstring

SUPPORTED_CONGRESSES: tuple[int, ...] = tuple(
    range(FIRST_SUPPORTED_CONGRESS, LAST_SUPPORTED_CONGRESS + 1)
)

MEASURE_TYPES: tuple[str, ...] = (
    "hr",
    "hres",
    "hjres",
    "hconres",
    "hamdt",
    "s",
    "sres",
    "sjres",
    "sconres",
    "samdt",
)
AMENDMENT_MEASURE_TYPES = frozenset({"hamdt", "samdt"})
BILL_MEASURE_WEIGHT = 1.0
AMENDMENT_MEASURE_WEIGHT = 0.5
MEASURE_WEIGHT_POLICY_NAME = "original_method_bill_1_amendment_0_5"

CONGRESS_GROUP = "congress"
MEASURE_TYPE_GROUP = "measure_type"
NUMBER_GROUP = "number"


class MeasureKeyError(ValueError):
    """Raised when a value is not a canonical ``congress:type:number`` key."""


def _alternation(tokens: Iterable[Any]) -> str:
    """Longest-first alternation so no prefix (``s`` before ``sres``) wins."""

    unique = sorted({str(token) for token in tokens}, key=lambda t: (-len(t), t))
    if not unique:
        raise MeasureKeyError("alternation requires at least one token")
    return "|".join(re.escape(token) for token in unique)


def build_canonical_key_re(
    congresses: Sequence[int] = SUPPORTED_CONGRESSES,
    measure_types: Sequence[str] = MEASURE_TYPES,
) -> re.Pattern[str]:
    """Compile the strict canonical-key pattern for a Congress window.

    The three groups are named *and* ordered ``congress``, ``measure_type``,
    ``number``, so ``match.group(1..3)``, ``match.groups()``, ``match.group(
    "congress")`` and ``match.groupdict()`` all keep working for every call
    site that existed before the definitions were merged.
    """

    return re.compile(
        rf"^(?P<{CONGRESS_GROUP}>{_alternation(congresses)}):"
        rf"(?P<{MEASURE_TYPE_GROUP}>{_alternation(measure_types)}):"
        rf"(?P<{NUMBER_GROUP}>[1-9]\d*)$"
    )


CANONICAL_KEY_RE: re.Pattern[str] = build_canonical_key_re()

# Historical aliases.  The lane named this same object ``CANONICAL_KEY_RE``,
# ``MEASURE_RE``, ``MEASURE_KEY_RE`` and ``KEY_RE`` in different files; the
# aliases let each importer keep its local vocabulary without keeping its own
# copy of the pattern.
MEASURE_RE = CANONICAL_KEY_RE
MEASURE_KEY_RE = CANONICAL_KEY_RE
KEY_RE = CANONICAL_KEY_RE


def is_canonical_key(value: Any) -> bool:
    """True when ``value`` is already an exact canonical key (no coercion)."""

    return isinstance(value, str) and CANONICAL_KEY_RE.fullmatch(value) is not None


def normalize_canonical_key(value: Any) -> str:
    """Return the canonical key for ``value``, or raise :class:`MeasureKeyError`.

    Leading/trailing whitespace and letter case are normalized *explicitly*
    here so no pattern needs ``re.IGNORECASE``.  Numeric components are
    re-rendered from their integer value, so this is also the place a caller
    can rely on to collapse nothing -- ``007`` is rejected, not silently
    rewritten, because a leading zero means the producer is not speaking the
    contract.
    """

    congress, measure_type, number = parse_canonical_key(value)
    return f"{congress}:{measure_type}:{number}"


def parse_canonical_key(value: Any) -> tuple[int, str, int]:
    """Split a canonical key into ``(congress, measure_type, number)``.

    Accepts surrounding whitespace and mixed case; rejects everything else.
    """

    text = str(value or "").strip().casefold()
    match = CANONICAL_KEY_RE.fullmatch(text)
    if not match:
        raise MeasureKeyError(f"invalid canonical measure key: {value!r}")
    return (
        int(match.group(CONGRESS_GROUP)),
        match.group(MEASURE_TYPE_GROUP),
        int(match.group(NUMBER_GROUP)),
    )


def measure_kind(value: Any) -> str:
    """The measure type of a canonical key (``hr``, ``samdt``, ...)."""

    return parse_canonical_key(value)[1]


def is_amendment_key(value: Any) -> bool:
    return measure_kind(value) in AMENDMENT_MEASURE_TYPES


def measure_weight(value: Any) -> float:
    """Return the original-method scoring weight for one canonical measure.

    Bills and resolutions carry full weight. House and Senate amendments carry
    the fixed half weight used by the legacy/manual legislation path. Congress
    decay remains a separate factor and must not be folded into this value.
    """

    return (
        AMENDMENT_MEASURE_WEIGHT
        if is_amendment_key(value)
        else BILL_MEASURE_WEIGHT
    )


DISPLAY_AMENDMENT_RE = re.compile(
    r"^\s*([HS])\.?\s*Amdt\.?\s*(\d+)\s*$", re.IGNORECASE
)


def display_measure_kind(value: Any) -> str:
    """Measure kind for a DISPLAY-form id such as ``H.Amdt.103``.

    The member-desk and primacy surfaces carry display ids, not canonical
    ``congress:type:number`` keys, so ``measure_kind`` raises on them. This is
    the single bridge between the two forms; consumers needing the
    bill/amendment distinction from a display id come through here instead of
    re-deriving it locally. Two such regexes already existed in the tree, and
    ``primacy_factors`` had none at all -- which is exactly how amendments came
    to be scored at full bill weight on the tier board (2026-07-29).

    Returns ``'hamdt'``/``'samdt'`` for amendments, ``'bill'`` otherwise --
    including ids this cannot parse. Failing OPEN to bill weight is
    deliberate: an unrecognized id keeps the weight it has always carried
    rather than being silently halved. ``audit_full`` separately asserts that
    no act whose id looks like an amendment escapes detection here, so a regex
    miss fails loud instead of quietly scoring at 1.0.
    """

    match = DISPLAY_AMENDMENT_RE.match(str(value or ""))
    if not match:
        return "bill"
    return "hamdt" if match.group(1).upper() == "H" else "samdt"


def is_display_amendment(value: Any) -> bool:
    """True when a display-form id names a House or Senate amendment."""

    return display_measure_kind(value) in AMENDMENT_MEASURE_TYPES


def display_measure_weight(value: Any) -> float:
    """``measure_weight`` for a display-form id. See ``display_measure_kind``."""

    return (
        AMENDMENT_MEASURE_WEIGHT
        if is_display_amendment(value)
        else BILL_MEASURE_WEIGHT
    )


def measure_weight_policy() -> dict[str, Any]:
    """Return the manifest-bound original-method measure-weight contract."""

    return {
        "name": MEASURE_WEIGHT_POLICY_NAME,
        "bill_or_resolution_weight": BILL_MEASURE_WEIGHT,
        "amendment_weight": AMENDMENT_MEASURE_WEIGHT,
        "amendment_measure_types": sorted(AMENDMENT_MEASURE_TYPES),
        "congress_decay_is_separate": True,
    }
