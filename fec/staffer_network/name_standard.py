# -*- coding: utf-8 -*-
"""ONE person-name standard for every staffer-network name source.

Paul's ruling (2026-09-06): one standard across all sources; "normal
nicknames" are canonicalised; suffixes are KEPT as parts (never silently
dropped); the Hill spine WILL be re-keyed later with an id map -- NOT here.
This module is the parser only.  It reads nothing it is not asked to read,
writes nothing, and holds no state beyond a lazily-cached nickname table.

VERSION 1.1 (2026-09-07) -- five rulings, R1..R5, each marked below.  The
version constant :data:`NAME_STANDARD_VERSION` is what a receipt pins.

  R1  A bare trailing "V" is a MIDDLE INITIAL, never a suffix.  The suffix
      set is now exactly {JR, SR, II, III, IV}.  (rule 2)
  R2  In the no-comma House CSV form, ruled surname particles join the
      surname: "DEL BECCARO CHRISTOPHER E" -> last "Del Beccaro".  (rule 3)
  R3  The payroll family strips glued job-title/party fragments before
      parsing.  (rule 4a)
  R4  A parenthetical or quoted group is a NICKNAME only when it is a single
      capitalised word that is not a role/status term; otherwise it is an
      ANNOTATION and is dropped.  Trailing "- EFFECTIVE <date>", unbalanced
      parentheses and scrape artifacts are stripped.  (rule 5)
  R5  ``display_name`` still never renders a nickname -- Paul's
      minimal-duplication law.  Rule 8 is unchanged; the ruling is recorded
      here because the 2026-09-07 gold was relabelled to it.  (rule 8)

VERSION 1.1.1 (2026-09-07) -- lane-owner ruling D, two small DISPLAY-ONLY
changes.  Both are scoped to the display reader on purpose: the payroll
readers are what the Hill spine keys on, and neither change may move a
payroll key.

  D1  "do" joins rule 7's :data:`PARTICLES`.  "da", "das", "dos" and "du"
      were already there, so its absence was an omission in one Portuguese
      form, not a position.  (rule 7)
  D2  A TRAILING run of ruled post-nominals -- :data:`POST_NOMINALS`: ESQ,
      JD, PHD, MBA, CPA, MD -- on a display-source name is lifted into
      :attr:`NameParts.post_nominals`.  A credential is never the surname,
      never reaches :func:`name_key`, and is never rendered by
      :func:`display_name`.  (rules 7 and 8)

Four entry points
-----------------
``parse_name(raw, source) -> NameParts``
``canonical_first(first) -> str``          (nickname CLASS folding)
``name_key(parts, nicknames=True) -> str`` ("last|first_class|mi")
``display_name(parts) -> str``             ("Robert P. Casey Jr.")

Every rule below is stated in prose because a re-key is only attributable if
the reader can say WHY a key moved.


RULE 1 -- token model
---------------------
A token is a run of Unicode word characters (letters/digits, never "_"),
optionally joined internally by ``-`` or an apostrophe (``'`` or U+2019).
Leading/trailing ``-`` and apostrophes are trimmed off a token.  This mirrors
``build_hill_personnel_spine.NAME_TOKEN_RE`` (``[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*``)
except that ours accepts non-ASCII letters instead of dropping them, because
the ascii fold belongs at KEY time (rule 6), not at parse time.  Consequence:
"O'Brien", "Smith-Jones" and "D'Angelo" are ONE token each and keep their
punctuation in the parts.


RULE 1a -- characters that carry no glyph
-----------------------------------------
Before tokenising, any ``str.isspace()`` character becomes one space (so it
still separates), and every control, format, surrogate, private-use or
unassigned character -- plus U+FFFD -- is DELETED IN PLACE.  These are bytes
that lost their character, not separators.  The corpora carry CP850 mojibake:
"Mario Th\\x82riault" is Thériault with the accented byte surviving as the C1
control U+0082.  Splitting on it invents a middle name "Th" and a surname
"riault"; deleting it yields "Thriault", which is exactly what the spine
builder's ascii fold produces, so both sides of a join stay on one key.
See :func:`clean_invisibles`.


RULE 2 -- suffix set and the separator law            [R1 CHANGED THIS RULE]
------------------------------------------------
The suffix set is exactly {JR, SR, II, III, IV}, matched case-insensitively,
with or without trailing periods ("Jr.", "JR", "III.").

R1 (Paul, 2026-09-07): **a bare "V" is a MIDDLE INITIAL, never a suffix.**
Version 1.0 read a whole-token "V" as a Roman-numeral suffix and dropped it
from the key, on the stated ground that treating it as an initial "would move
the payroll key of every 'SMITH JOHN V' row, which is a re-key we were not
asked to make".  We were subsequently asked to make it.  The blind gold
settled the empirical question the 1.0 note left open: all nine sampled
bare-"V" rows (044, 068, 069, 206, 207, 208, 209, 210, 269) are middle
initials and none is a regnal ordinal, and those nine rows were nine of the
ten high-confidence ``all_parts`` failures.  "V" is now treated like any other
initial everywhere -- parts, ``name_key`` and ``display_name``.  There is no
"V." -after-a-suffix special case: V is ALWAYS an initial.  The consequence is
intended and is absorbed by the spine id map.

ONE CARVE-OUT, AND WHY.  "V" IS NEVER A GIVEN NAME.  R1's sentence is about a
bare TRAILING "V"; the corpus also writes "V" where a suffix goes, ahead of the
given name, and reading THAT as an initial does not make it an initial -- it
makes it a first name, and no person is called V.  The census found 11 distinct
House CSV strings of that shape (55 observation rows): "HENNESSY V MICHAEL F",
"SMITH V ROBERT B", "TUXWORTH V FRANK E", "WICKES V WILLIAM", "LAWRENCE V
JAMES", "CHILDERS V WILSON C.", "CAROSI V NICHOLAS", "LOVE V CHARLES M.",
"SEAY V EVERETTE M.", "SOUTHARD V CECIL D.", "STEINBAUM V MATTHEW"; and one
comma string, "QUEALLY V,THOMAS F", whose SURNAME side ends in a bare V.
Without the carve-out those twelve become ``first = "V"`` and
``last = "QUEALLY V"`` respectively -- keys ``hennessy|v|m`` and
``queallyv|thomas|f``.  So:

  * ``house_csv``      -- if the token that would become the GIVEN NAME is a
                          bare "V", it is a suffix instead.
  * ``payroll_comma``  -- if the SURNAME side ends in a bare "V", it is a
                          suffix instead.  A surname side has no middle, so an
                          initial is not an available reading there.
  * everywhere else    -- "V" is an initial, which is R1.

This preserves v1.0 exactly on those twelve strings and changes nothing about
the ~1,120 trailing-V rows R1 is aimed at.  It is a reading of R1, not an
exception to it: R1 says a bare TRAILING V is an initial, and none of these is
trailing.

A suffix is recognised ONLY when it is SEPARATOR-DELIMITED, i.e. it is either
  (a) a whole token          -- "SMITH JOHN JR"      -> suffix JR
  (b) the trailing segment of a hyphen-joined token, when the segment before
      the hyphen is not itself empty
                             -- "DALY-JR"            -> last DALY, suffix JR
A run-on suffix is NEVER split:
                             -- "SMITHJR"            -> last SMITHJR, no suffix
Apostrophe-joined segments are not suffix separators ("O'V" is a surname).
A suffix-only string yields the suffix and empty name parts; a token that is a
suffix is never allowed to become the ONLY surname (see rule 3 degenerate
handling) -- if stripping suffixes would leave nothing, the suffix is put back
and treated as an ordinary token, because a person whose entire recorded name
is "JR" is a data defect we must not turn into an empty key.


RULE 3 -- the four source formats                     [R2 CHANGED house_csv]
---------------------------------
``house_csv``      "LAST FIRST M" -- House Statement-of-Disbursements CSV era,
                   NO comma.  Date hints (rule 4) are removed, the string is
                   tokenised, suffix tokens are lifted out, and the REMAINING
                   tokens are assigned positionally: [0] last, [1] first,
                   [2:] middle -- EXCEPT that R2's surname particles (below)
                   can extend the surname rightwards first.

                   R2 (Paul, 2026-09-07): a leading run of tokens drawn from
                   :data:`HOUSE_CSV_PARTICLES` -- {DE, DEL, DELLA, DI, DA, DU,
                   LA, LE, VAN, VON, VANDER, ST, SAINT, O}, plus the ruled
                   two-token sequence "VAN DER" -- joins with the token that
                   follows it to form the surname, so "DEL BECCARO CHRISTOPHER
                   E" reads last "Del Beccaro", first "Christopher", middle
                   "E".  MC is deliberately NOT a particle: "MC" never appears
                   as a free-standing leading token in the corpus and adding it
                   would only create a way to be wrong.

                   THE GUARD, and why it is not optional.  The corpus really
                   does contain people whose surname IS the particle: "LE
                   CATHERINE L.", "VAN BRITTANY M", "SAINT RICHARD C", "LA
                   ALISA M", "DU MICHELLE C." -- 257 observation rows.  A blind
                   join destroys every one of them.  So the join is accepted
                   only when the token AFTER the joined surname is a plausible
                   given name, i.e. more than one letter; and when the greedy
                   particle run fails that test the run is SHORTENED one token
                   at a time until it passes or disappears.  The back-off is
                   what reads "DE LA O IRVIN F" correctly (surname "De La O",
                   given name "Irvin") instead of over-running onto "Irvin".
                   Measured over 1,083,510 House observation rows: 2,606 rows
                   / 312 distinct raw strings join, 257 rows are saved by the
                   bare-initial guard and 18 by the three-token minimum.

                   DOCUMENTED LIMIT: a two-surname row with NO particle stays
                   positional -- "GRECO JOHNSON KRISTINE M" still reads last
                   GRECO, first JOHNSON.  Nothing in the string distinguishes
                   it from "SMITH JOHN M", so the standard declines to guess.
                   v1.2 also accepts the bounded sequences DE LOS and DE LAS,
                   retaining the same plausible-given-name back-off guard.

                   This still reproduces
                   ``build_hill_personnel_spine.payroll_payee_key``'s no-comma
                   branch for every row where no particle fires and no bare "V"
                   is present.  Three documented differences remain: we RETAIN
                   the suffix in ``suffix`` instead of discarding it; we retain
                   a bare "V" as a middle initial where the builder drops it
                   (R1); and we join a particle surname where the builder
                   splits it (R2).  All three are intended, and the builder
                   equivalence test enumerates them.
                   Fewer than two non-suffix tokens is not an error: the single
                   token becomes ``last`` and ``format_detected`` records it.

``payroll_comma``  "LAST, FIRST M" / "LAST,FIRST M" -- House PDF era and Senate
                   payroll.  Date hints removed, R3's glued annotations
                   removed (rule 4a), then the string is partitioned on the
                   FIRST comma.  Every non-suffix token LEFT of the comma is
                   joined with single spaces into ``last`` (so "VAN DER BERG,
                   JOHN" keeps a three-token surname); right of the comma,
                   token [0] is ``first`` and the rest join into ``middle``.
                   Suffixes are lifted from BOTH sides.
                   A string with no comma falls back to the house_csv rule and
                   says so in ``format_detected``.

``senate``         Senate payroll OFFICE strings that carry a member name.
                   "SENATOR <name>"                -> title dropped, a trailing
                       parenthesised state postal code dropped (R3), remainder
                       parsed in DISPLAY order (first ... last).
                   "INTERN COMPENSATION - <surname>" -> surname only; ``first``
                       is empty.  This is the honest shape: the source really
                       does carry no given name.
                   Anything else: a comma routes to the payroll_comma rule, no
                   comma routes to the display rule.  ``format_detected`` says
                   which.

``display``        Free display names -- Quorum ``display_name``, Politico
                   ``verified_name``, LDA ``display_name``, LittleSis ``name``.
                   Steps, in order:
                     0. R3/R4 annotations are removed: a trailing
                        "- EFFECTIVE <date>", then nicknames-or-annotations
                        (rule 5), then unbalanced parentheses, then a trailing
                        scrape artifact;
                     1. nicknames (rule 5) are lifted out;
                     2. a leading honorific from the documented title set
                        {mr, mrs, ms, miss, mx, dr, prof, professor, hon,
                        honorable, rep, representative, sen, senator, gov,
                        governor, judge, sir, dame, rev, reverend, fr, gen,
                        col, capt, lt, maj, sgt, amb, ambassador} is dropped,
                        but only while at least two tokens would remain;
                     3. suffixes are lifted;
                     4. if a comma remains AND the text right of it is not
                        made up solely of suffixes/honorifics, the string is
                        read INVERTED ("Last, First M"); otherwise it is read
                        in WESTERN order ("First M Last"), which is what makes
                        "John Smith, Jr." parse as John / Smith / JR rather
                        than as a surname "John Smith";
                     5. in western order the surname is extended leftwards
                        across surname particles (rule 7);
                     6. a single remaining token becomes ``last`` (not
                        ``first``): the payroll side of every join we care
                        about is surname-first, so a mononym is more useful as
                        a surname, and ``format_detected`` marks the row so an
                        adjudicator can overrule it.


RULE 4 -- date-hint contamination
---------------------------------
``DATE_HINT_RE`` (``\\b\\d{1,2}/\\d{1,2}/\\d{2,4}\\b``) is the builder's, byte
for byte.  It is substituted with a space BEFORE tokenising for house_csv,
payroll_comma and senate.  It is NOT applied to ``display``: a slash in a
display name is not a payroll date hint and removing it there would silently
edit a name we were given clean.  (The one display row the 2026-09-07 gold
caught with a real payroll date in it, "JILL DOWELL - EFFECTIVE 2/9/21", is
handled by rule 5's EFFECTIVE annotation strip instead, which removes the
annotation AND its date without touching names that merely contain a slash.)


RULE 4a -- glued job-title and party fragments        [R3, NEW IN v1.1]
------------------------------------------------
Paul ruled (2026-09-07) that the payroll family strips glued job-title/party
fragments before parsing.  The list is built from data, not guessed.

CENSUS (read-only, 2026-09-07, over the three bound payroll corpora):
  * 268,013 Senate rows carry exactly 24 balanced parentheticals, over 7
    distinct contents: MINORITY 11, REPUBLICAN 5, CL-II 2, CL 2,
    "WASHINGTON D.C." 2, "FORT WORTH" 1, HEALTH 1.
  * 28 Senate rows carry a ``)`` BEFORE the first comma -- the glued-prefix
    shape, e.g. "ASSISTANT(REPUBLICAN)SOMMER, MATTHEW",
    "DIRECTOR (FORT WORTH)NICHOLSON, EMILY J",
    "ERSHIP LIAISON(MINORITY)HAWKINS, THOMAS P" (itself left-truncated
    upstream), and "AND APPROPRIATIONS)HERRLE, CYNTHIA A" (an unmatched
    ``)``).  In every one of the 28 the real name begins immediately after
    that ``)``.
  * 0 House rows carry any parenthetical at all.
  * 2 Senate rows carry the truncated trailing form
    "CLESI, CHRISTIAN D FELLOW (DEMOCRATIC".

So the rule is exactly two shapes, both narrow:
  (a) LEADING GLUE.  If a ``)`` occurs before the first comma, everything from
      the start of the string through that ``)`` is deleted -- provided a
      comma remains and the remainder still contains a letter.
      ``format_detected`` gains the ``_glued_annotation`` marker.
  (b) TRAILING PARTY/ROLE WORDS.  A trailing run of tokens drawn from
      :data:`PAYROLL_TRAILING_ANNOTATIONS` = {FELLOW, MAJORITY, MINORITY,
      DEMOCRATIC, DEMOCRAT, REPUBLICAN} is removed from the right-of-comma
      side, never touching token [0] (the given name).  The set is this small
      ON PURPOSE.  The same census shows the first middle token is an ordinary
      word on 39,270 Senate rows, and the top of that list is ANN, MARIE,
      ELIZABETH, ANNE, LYNN, JAMES, LEE -- real middle names.  A broad
      "strip trailing job words" rule would eat them.  Truncated column-bleed
      fragments the Senate PDF extractor leaves behind (EGISLATIVE 1,761;
      ENIOR 982; TAFF 875; EPUTY 722) are NOT in the set: Paul did not rule
      them, they never displace a middle initial that is present, and they are
      reported as a remaining defect rather than guessed at.
  (c) STATE CODES.  After a "SENATOR <name>" title, a trailing parenthesised
      two-letter US postal code is a state, not a nickname, and is dropped.

Neither (a) nor (b) is applied to ``display``: a parenthetical in a display
name is rule 5's business.


RULE 5 -- nicknames and annotations                   [R4 CHANGED THIS RULE]
-----------------------------------
Text wrapped in double quotes (``"`` or U+201C/U+201D), paired single curly
quotes (U+2018 ... U+2019), parentheses or square brackets is a DELIMITED
GROUP.  Version 1.0 called every such group a nickname.  R4 (Paul,
2026-09-07) splits them:

  A group is a NICKNAME -- kept in ``nickname_raw``, never in ``first`` --
  when, after its own surrounding quote characters are stripped, it is
    * non-empty,
    * a SINGLE word (contains no whitespace),
    * contains at least one letter,
    * CAPITALISED (its first letter is upper case), and
    * not a role/status term (:data:`ANNOTATION_TERMS`).

  Every other group is an ANNOTATION: it is deleted from the text and is NOT
  recorded as a nickname.

The evidence for the shape of that test is the LDA corpus, the only display
corpus that carries any groups at all (74 of 25,340 rows; Quorum and Politico
carry none).  EVERY multi-word group in it is an annotation -- "NAME ERROR",
"REGISTERED IN ERROR", "NOT A REAL PERSON", "CONTRACT LOBBYIST", "AKA MARY",
"NEE SAVELIEFF", "FORMERLY BONICELLI", "CASSIDY AND ASSOCIATES" -- while the
single-word groups are overwhelmingly real nicknames (CHUCK, JARED, LIZ, TOM,
MANDI, JERRY, ABRAM, LITA, JAKE, BETSY, TIM) or alternate surnames the source
flagged.  The one single-word role term observed, CONTRACTOR, is in
:data:`ANNOTATION_TERMS`.

Stripping the group's own surrounding quotes also fixes a v1.0 defect the gold
caught: 'PATRICIA ("TRISH") REILLY' recorded ``nickname_raw`` as the literal
``"TRISH"``, quote marks included.

A straight ASCII apostrophe is never a group delimiter -- it is an apostrophe
in O'Brien, and a lone U+2019 is treated the same way.

Three further annotation strips, in this order, all sources:
  * a trailing "- EFFECTIVE <anything>" (R4);
  * UNBALANCED parentheses: text from the last unmatched ``(`` to the end of
    the string is truncated, and an unmatched ``)`` is deleted.  This is what
    reads "MATTHEW SMITH (TIKTOK VALESKO" as Matthew Smith instead of
    inventing the surname "Valesko";
  * for ``display`` only, a trailing scrape artifact from
    :data:`DISPLAY_TRAILING_ARTIFACTS`.  That set holds exactly the tokens the
    census actually found -- one, "Search", from "Timothy D Hendrix Search" --
    and is deliberately narrow: a longer guessed list would start deleting
    surnames.  It fires only when at least two tokens survive.


RULE 6 -- the key
-----------------
``name_key`` folds each part with NFKD -> ascii-ignore -> casefold and then
strips everything that is not [a-z0-9], so "O'Brien" and "OBrien" and
"O’Brien" all key as ``obrien`` and "de la Cruz" keys as ``delacruz``.
The suffix is EXCLUDED from the key (that is the point of keeping it as a
part).  The middle contributes exactly its first folded letter.  The shape is
``last|first_class|mi`` -- deliberately the same shape as the spine's
``payee_key`` [last, first, middle_initial] so a re-key delta is attributable
part by part.


RULE 7 -- surname particles (display order only)
------------------------------------------------
In western order the surname starts at the FIRST token at position >= 1 that is
in the particle set {de, del, dela, della, dello, di, da, dal, dalla, das, dos,
du, van, von, vander, vanden, der, den, ter, ten, la, le, lo, st, ste, saint,
bin, ibn, abu, af, av, ver} and is followed by at least one more token; it then
runs to the end.  Position >= 1 is what keeps "Van Jones" and "Al Green" from
losing their given names.  Particles are not applied to the inverted (comma)
form, where the whole left side is already the surname; the payroll formats
use rule 3's own R2 particle rule instead, which is narrower and guarded.


RULE 8 -- display rendering                            [R5 RE-AFFIRMED, 1.0]
---------------------------
``display_name`` renders "First Middle Last Suffix", single-spaced, skipping
empty parts, and does NOT render the nickname.  R5 (Paul, 2026-09-07)
re-affirms this against the blind gold, which had rendered nicknames inline
("Patricia \\"Trish\\" Reilly"): the minimal-duplication law stands and the
gold rows were relabelled to the no-nickname form instead.  Casing:
  * a part token that is already MIXED case is left alone -- the source told us
    how it capitalises itself and we do not know better ("MacKenzie", "diMaggio");
  * an all-upper or all-lower token is title-cased, then:
      - "O'x"/"D'x"          -> "O'Brien", "D'Angelo"
      - "Mc" + >= 2 chars    -> "McDonald"
      - "Mac" + >= 3 chars   -> "MacArthur", except the run-on set
        :data:`MAC_RUNONS` (mac, mace, macer, macey, machado, macias, mack,
        mackey, mackie, macomber, macon, macro, macy) where "Mac" is part of
        the stem rather than a Gaelic prefix;
      - hyphen segments are capitalised individually ("Smith-Jones");
  * a surname particle inside a multi-token surname is lower-cased unless it is
    the first rendered token;
  * a single-letter middle gains a period ("P.") -- which is how a bare "V"
    now renders, "Kelsey V. Wall";
  * suffixes render as "Jr."/"Sr." (with period) and "II"/"III"/"IV" (Roman
    numerals, no period).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable, Mapping

__all__ = [
    "NAME_STANDARD_VERSION",
    "NameParts",
    "SOURCES",
    "SUFFIXES",
    "parse_name",
    "parts_from_payee_key",
    "canonical_first",
    "name_key",
    "display_name",
    "load_class_table",
    "class_table_path",
    "reset_class_table_cache",
    "ascii_fold",
    "fold_key",
    "clean_invisibles",
    "TITLES",
    "PARTICLES",
    "POST_NOMINALS",
    "HOUSE_CSV_PARTICLES",
    "HOUSE_CSV_PARTICLE_SEQUENCES",
    "ANNOTATION_TERMS",
    "PAYROLL_TRAILING_ANNOTATIONS",
    "DISPLAY_TRAILING_ARTIFACTS",
    "STATE_CODES",
]

#: Bumped whenever a RULE changes.  1.0 = 2026-09-06 (the original eight
#: rules).  1.1 = 2026-09-07 (R1 bare-V, R2 house_csv particles, R3 glued
#: payroll annotations, R4 nickname-vs-annotation, R5 display re-affirmed).
#: 1.1.1 = 2026-09-07 lane-owner ruling D, two small display-only changes:
#: "do" joins rule 7's :data:`PARTICLES`, and a trailing post-nominal
#: (:data:`POST_NOMINALS`) on a DISPLAY-source name moves into its own
#: :attr:`NameParts.post_nominals` field instead of being read as a surname.
#: Both are scoped to the display reader, so no payroll key can move under
#: the second and only a comma-less display/senate_display string under the
#: first.
# 1.2: bounded payroll header repair and DE LOS / DE LAS surname particles.
NAME_STANDARD_VERSION = "1.2"

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------
SOURCES = ("house_csv", "payroll_comma", "senate", "display")

#: R1: "V" is NOT here any more.  A bare "V" is a middle initial (rule 2).
SUFFIXES = frozenset({"JR", "SR", "II", "III", "IV"})

#: Byte-identical to ``build_hill_personnel_spine.DATE_HINT_RE``.
DATE_HINT_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")

#: Unicode-accepting sibling of the builder's ``NAME_TOKEN_RE``.
_TOKEN_RE = re.compile(r"[^\W_]+(?:[-'’][^\W_]+)*", re.UNICODE)
_TOKEN_TRIM = "-'’"

_SENATOR_RE = re.compile(r"^\s*SENATOR\s+(.+)$", re.IGNORECASE)
_INTERN_RE = re.compile(r"^\s*INTERN\s+COMPENSATION\s*-\s*(.+)$", re.IGNORECASE)

_NICKNAME_RE = re.compile(
    r'"([^"]*)"'
    r'|“([^”]*)”'
    r'|‘([^’]*)’'
    r'|\(([^)]*)\)'
    r'|\[([^\]]*)\]'
)

#: R3(a): a ``)`` reached before the first comma ends a glued job-title
#: fragment.  ``[^,]*`` cannot cross the comma, so this can only ever match a
#: prefix; it is greedy, so it consumes up to the LAST such ``)``.
_GLUED_LEADING_ANNOTATION_RE = re.compile(r"^[^,]*\)")

#: R4: a trailing "- EFFECTIVE <date>" payroll annotation.
_EFFECTIVE_ANNOTATION_RE = re.compile(
    r"\s*[-–—]\s*EFFECTIVE\b.*$", re.IGNORECASE
)

#: R3(c): a trailing "(XX)" state code after a SENATOR title.
_TRAILING_PAREN_CODE_RE = re.compile(r"\s*\(\s*([A-Za-z]{2})\s*\)\s*$")

TITLES = frozenset({
    "mr", "mrs", "ms", "miss", "mx", "dr", "prof", "professor", "hon",
    "honorable", "rep", "representative", "sen", "senator", "gov", "governor",
    "judge", "sir", "dame", "rev", "reverend", "fr", "gen", "col", "capt",
    "lt", "maj", "sgt", "amb", "ambassador",
})

#: v1.1.1 (ruling D) added "do".  "dos", "da", "das" and "du" were already
#: here, so its absence was an omission in one Portuguese form rather than a
#: position: "Antonia Germano Do Nascimento" read last="Nascimento" where
#: every sibling particle produced "do Nascimento".
PARTICLES = frozenset({
    "de", "del", "dela", "della", "dello", "di", "da", "dal", "dalla", "das",
    "do", "dos", "du", "van", "von", "vander", "vanden", "der", "den", "ter",
    "ten", "la", "le", "lo", "st", "ste", "saint", "bin", "ibn", "abu", "af",
    "av", "ver",
})

#: v1.1.1 (ruling D).  Folded forms -- :func:`fold_key` strips the periods, so
#: "Ph.D.", "PhD" and "ph d" all land on "phd".  A post-nominal is a
#: CREDENTIAL, not a name part: it never becomes the surname, never reaches
#: :func:`name_key`, and :func:`display_name` never renders it.  It is lifted
#: only from a DISPLAY-source name and only from the TRAILING run of tokens,
#: which is the one position the corpora ever write it in; a middle "Md" or a
#: surname "Do" is therefore untouched.
POST_NOMINALS = frozenset({"esq", "jd", "phd", "mba", "cpa", "md"})


def _post_nominal_alternative(word: str) -> str:
    """``"phd"`` -> ``"P\\.?H\\.?D\\.?"`` -- periods allowed, spaces are not."""

    return r"\.?".join(character.upper() for character in word) + r"\.?"


#: One credential, glued.  ``(?![A-Za-z])`` stops "MD" matching inside "MDX";
#: the leading ``\b`` stops it matching the tail of a surname.
_POST_NOMINAL_RE = re.compile(
    r"\b(?:%s)(?![A-Za-z])"
    % "|".join(sorted((_post_nominal_alternative(word) for word in POST_NOMINALS),
                      key=lambda pattern: (-len(pattern), pattern))),
    re.IGNORECASE,
)

#: A trailing RUN of them, with the separators the corpora write: spaces and
#: commas.  Anchored at end of string, so a credential in the middle of a name
#: is left exactly where the source put it.
_TRAILING_POST_NOMINAL_RE = re.compile(
    r"(?:[\s,]*%s)+[\s,]*$" % _POST_NOMINAL_RE.pattern, re.IGNORECASE
)

#: R2: Paul's ruled particle set for the no-comma House CSV form.  Narrower
#: than :data:`PARTICLES` on purpose, and MC is deliberately excluded.
HOUSE_CSV_PARTICLES = frozenset({
    "de", "del", "della", "di", "da", "du", "la", "le", "van", "von",
    "vander", "st", "saint", "o",
})

#: R2: the one ruled MULTI-token particle.  "DER" is not a particle on its
#: own, so "VAN DER LUGT ROELOF" needs the pair to read correctly.
HOUSE_CSV_PARTICLE_SEQUENCES = frozenset({("van", "der"), ("de", "los"), ("de", "las")})

# Exact source header fragments observed before payroll surnames. These are
# anchored prefixes, never arbitrary words deleted from a person's name.
PAYROLL_LEADING_HEADERS = ("SENATE STEERING COMMITTEE", "OF THE SENATE")

#: R4: single-word group contents that are a role or status, not a nickname.
ANNOTATION_TERMS = frozenset({
    "deceased", "decd", "retired", "former", "formerly", "resigned",
    "terminated", "inactive", "vacant", "contract", "contractor", "lobbyist",
    "intern", "fellow", "majority", "minority", "democratic", "democrat",
    "republican", "independent", "error", "unknown", "na", "tbd", "nee",
    "aka", "esq", "deceased", "pending", "duplicate", "test",
})

#: R3(b): trailing party/role words on the given-name side of a payroll row.
PAYROLL_TRAILING_ANNOTATIONS = frozenset({
    "fellow", "majority", "minority", "democratic", "democrat", "republican",
})

#: R4: trailing scrape artifacts in a display name.  Census-derived and
#: deliberately one entry long -- see rule 5.
DISPLAY_TRAILING_ARTIFACTS = frozenset({"search"})

#: R3(c): US postal codes, for the "SENATOR <name> (NM)" form.
STATE_CODES = frozenset({
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy", "dc", "pr", "vi", "gu", "as", "mp",
})

#: Tokens where "Mac" is part of the stem rather than a Gaelic prefix.
MAC_RUNONS = frozenset({
    "mack", "macey", "macy", "machado", "macias", "mackey", "mackie", "macon",
    "macomber", "macro", "mace", "macer",
})

CLASS_TABLE_SCHEMA_ID = "staffer-network-first-name-nickname-class-table-v2"
_CLASS_TABLE_RELATIVE = ("data", "staffer_network", "reference",
                         "first_name_nicknames_v2.json")


@dataclass(frozen=True)
class NameParts:
    """A parsed person name.  Every field is a string; absence is ``""``."""

    first: str = ""
    middle: str = ""
    last: str = ""
    suffix: str = ""
    nickname_raw: str = ""
    format_detected: str = ""
    raw: str = ""
    #: v1.1.1 (ruling D).  Space-joined folded-uppercase credentials lifted off
    #: a display name -- "PHD", "JD ESQ".  LAST field on purpose: every
    #: existing positional construction keeps its meaning.  Excluded from
    #: :func:`name_key` and from :func:`display_name` by construction, since
    #: neither reads this attribute.
    post_nominals: str = ""

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


# --------------------------------------------------------------------------
# Folding helpers
# --------------------------------------------------------------------------
def ascii_fold(value: str | None) -> str:
    """NFKD -> ascii, exactly the builder's ``_ascii_fold``."""

    return (
        unicodedata.normalize("NFKD", value or "")
        .encode("ascii", "ignore")
        .decode("ascii")
    )


def fold_key(value: str | None) -> str:
    """Ascii-fold, casefold, then keep only ``[a-z0-9]`` (rule 6)."""

    folded = ascii_fold(value).casefold()
    return "".join(character for character in folded if character.isalnum())


# --------------------------------------------------------------------------
# Tokenising and suffixes
# --------------------------------------------------------------------------
#: Unicode general categories that carry no glyph.  A character in one of these
#: is an encoding artefact, never a name separator.
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Cn"})
_LOST_CHARACTER = "�"


def clean_invisibles(text: str | None) -> str:
    """Rule 1a -- normalise away characters that carry no glyph.

    Real whitespace (any ``str.isspace()`` character) becomes one space, so it
    still separates tokens.  Control, format, surrogate, private-use and
    unassigned characters, plus U+FFFD, are DELETED IN PLACE and never split a
    token, because that is what they are: bytes that lost their character.

    This matters.  The corpora carry CP850/CP437 mojibake -- ``"Mario
    Th\\x82riault"`` is Thériault with the accented byte surviving as the C1
    control U+0082.  Treating it as a separator invents a middle name "Th" and a
    surname "riault"; deleting it in place yields "Thriault", which is what the
    spine builder's ascii fold already produces, so the two sides of a join stay
    on the same key.
    """

    out: list[str] = []
    for character in text or "":
        if character.isspace():
            out.append(" ")
        elif character == _LOST_CHARACTER:
            continue
        elif unicodedata.category(character) in _INVISIBLE_CATEGORIES:
            continue
        else:
            out.append(character)
    return "".join(out)


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    for token in _TOKEN_RE.findall(clean_invisibles(text)):
        trimmed = token.strip(_TOKEN_TRIM)
        if trimmed:
            out.append(trimmed)
    return out


def _is_suffix_word(token: str) -> bool:
    return ascii_fold(token).strip(".").upper() in SUFFIXES


def _normalise_suffix(token: str) -> str:
    return ascii_fold(token).strip(".").upper()


def _split_suffixes(tokens: Iterable[str]) -> tuple[list[str], list[str]]:
    """Lift separator-delimited suffixes out of ``tokens`` (rule 2).

    Returns ``(remaining_tokens, suffixes)``.  A hyphen-joined trailing suffix
    ("DALY-JR") is split off the token it is glued to; a run-on ("SMITHJR") is
    left alone.  If lifting would leave no tokens at all, nothing is lifted.
    """

    tokens = list(tokens)
    remaining: list[str] = []
    suffixes: list[str] = []
    for token in tokens:
        if _is_suffix_word(token):
            suffixes.append(_normalise_suffix(token))
            continue
        if "-" in token:
            head, _, tail = token.rpartition("-")
            if head and _is_suffix_word(tail):
                remaining.append(head)
                suffixes.append(_normalise_suffix(tail))
                continue
        remaining.append(token)
    if not remaining and tokens:
        # A name that is nothing but a suffix is a data defect, not a suffix.
        return tokens, []
    return remaining, suffixes


def _lift_a_v_that_would_be_a_given_name(
    tokens: list[str], suffixes: list[str], index: int
) -> tuple[list[str], list[str]]:
    """R1 carve-out -- "V" is never a given name.  See rule 2.

    ``index`` is the position a bare "V" may not occupy: token 1 in the
    no-comma House CSV form (the given-name slot), and the LAST token of the
    surname side of a comma form.  When it does, the V is lifted as the suffix
    the source wrote, exactly as v1.0 read it.  Anything else keeps R1: V is an
    initial.
    """

    if len(tokens) < 2 or not (-len(tokens) <= index < len(tokens)):
        return tokens, suffixes
    if fold_key(tokens[index]) != "v":
        return tokens, suffixes
    remaining = tokens[:index] + tokens[index + 1:] if index >= 0 \
        else tokens[:index]
    if not remaining:
        return tokens, suffixes
    return remaining, list(suffixes) + ["V"]


def _join_suffixes(suffixes: Iterable[str]) -> str:
    seen: list[str] = []
    for suffix in suffixes:
        if suffix and suffix not in seen:
            seen.append(suffix)
    return " ".join(seen)


# --------------------------------------------------------------------------
# R2 -- house_csv surname particles
# --------------------------------------------------------------------------
def _house_csv_particle_span(tokens: list[str]) -> int:
    """How many LEADING tokens join the surname under R2.  0 = none.

    The returned span ``n`` means the surname is ``tokens[:n + 1]`` -- the
    particle run PLUS the token it prefixes -- and the given name is
    ``tokens[n + 1]``.  See rule 3 for the guard and why it is not optional.
    """

    if len(tokens) < 3:
        # "LE LINCOLN" has no given name left to give.  Stay positional.
        return 0
    index = 0
    while index < len(tokens):
        if index + 1 < len(tokens):
            pair = (fold_key(tokens[index]), fold_key(tokens[index + 1]))
            if pair in HOUSE_CSV_PARTICLE_SEQUENCES:
                index += 2
                continue
        if fold_key(tokens[index]) in HOUSE_CSV_PARTICLES:
            index += 1
            continue
        break
    span = index
    while span > 0:
        # Accept only when a plausible GIVEN NAME follows the joined surname.
        if len(tokens) >= span + 2 and len(fold_key(tokens[span + 1])) > 1:
            return span
        span -= 1
    return 0


# --------------------------------------------------------------------------
# R3/R4 -- annotation stripping
# --------------------------------------------------------------------------
def _strip_effective_annotation(text: str) -> str:
    """R4 -- drop a trailing "- EFFECTIVE <date>" payroll annotation."""

    return _EFFECTIVE_ANNOTATION_RE.sub("", text)


def _drop_unbalanced_parens(text: str) -> str:
    """R4 -- truncate at an unmatched ``(``; delete an unmatched ``)``.

    Balanced groups have already been consumed by :func:`_lift_nicknames` by
    the time this runs, so anything left is genuinely unbalanced.
    """

    if "(" not in text and ")" not in text:
        return text
    out: list[str] = []
    depth = 0
    for character in text:
        if character == "(":
            depth += 1
            out.append(character)
        elif character == ")":
            if depth:
                depth -= 1
                out.append(character)
            # an unmatched ")" is dropped
        else:
            out.append(character)
    joined = "".join(out)
    if depth:
        cut = joined.rfind("(")
        if cut >= 0:
            joined = joined[:cut]
    return joined


def _is_nickname_content(content: str) -> bool:
    """R4 -- a single capitalised word that is not a role/status term."""

    if not content or any(character.isspace() for character in content):
        return False
    letters = [character for character in content if character.isalpha()]
    if not letters:
        return False
    if not letters[0].isupper():
        return False
    return fold_key(content) not in ANNOTATION_TERMS


def _strip_glued_leading_annotation(text: str) -> tuple[str, bool]:
    """R3(a) -- drop a job-title fragment glued in front of the surname.

    Returns ``(text, stripped)``.  Only fires when a ``)`` is reached before
    the first comma AND the remainder still carries a comma and a letter, so a
    name that merely contains a parenthetical after the comma is untouched.
    """

    if "," not in text:
        return text, False
    for header in PAYROLL_LEADING_HEADERS:
        if text.upper().startswith(header):
            remainder = text[len(header):].lstrip()
            last, _, given = remainder.partition(",")
            if any(c.isalpha() for c in last) and any(c.isalpha() for c in given):
                return remainder, True
    match = _GLUED_LEADING_ANNOTATION_RE.match(text)
    if not match:
        return text, False
    remainder = text[match.end():]
    if "," not in remainder:
        return text, False
    if not any(character.isalpha() for character in remainder):
        return text, False
    return remainder, True


def _strip_trailing_payroll_annotations(tokens: list[str]) -> list[str]:
    """R3(b) -- drop a trailing run of ruled party/role words.

    Never touches ``tokens[0]``: that is the given name, and a person really
    can be called Fellow.
    """

    end = len(tokens)
    while end > 1 and fold_key(tokens[end - 1]) in PAYROLL_TRAILING_ANNOTATIONS:
        end -= 1
    return tokens[:end]


def _strip_trailing_display_artifact(tokens: list[str]) -> list[str]:
    """R4 -- drop one trailing scrape artifact, if two tokens survive."""

    if len(tokens) >= 3 and fold_key(tokens[-1]) in DISPLAY_TRAILING_ARTIFACTS:
        return tokens[:-1]
    return tokens


def _lift_trailing_post_nominals(text: str) -> tuple[str, list[str]]:
    """v1.1.1 (ruling D) -- lift a TRAILING run of credentials off ``text``.

    Returns ``(remaining_text, post_nominals)``, each credential folded to
    upper case so "Ph.D.", "PH.D" and "PHD" are one value.

    This works on the TEXT, before tokenising, and that is the whole point.
    :func:`_tokens` discards the periods, so at token level "Robert M. D."
    (initials) and "Robert M.D." (a physician) are the same two tokens and no
    token-level rule can tell them apart.  :data:`_POST_NOMINAL_RE` allows a
    period between the letters of a credential but never a SPACE, so
    "Robert M D" keeps its initials and only the glued forms are lifted.

    Nothing is lifted when it would leave the text empty: a record whose whole
    content is "MD" is a name this reader cannot decompose, not a credential
    with the person missing.
    """

    match = _TRAILING_POST_NOMINAL_RE.search(text)
    if not match or not match.group(0).strip(" \t,"):
        return text, []
    remainder = text[: match.start()]
    if not remainder.strip(" \t,"):
        return text, []
    found = [
        fold_key(item.group(0)).upper()
        for item in _POST_NOMINAL_RE.finditer(match.group(0))
    ]
    return remainder, found


# --------------------------------------------------------------------------
# Display-source helpers
# --------------------------------------------------------------------------
_GROUP_QUOTE_TRIM = "\"'“”‘’ \t"


def _lift_nicknames(text: str) -> tuple[str, str]:
    """Return ``(text_without_groups, nickname_raw)`` (rule 5).

    EVERY delimited group is removed from the text.  Only the ones that pass
    :func:`_is_nickname_content` are recorded; the rest were annotations.
    """

    found: list[str] = []

    def _capture(match: "re.Match[str]") -> str:
        for group in match.groups():
            if group is not None:
                stripped = group.strip().strip(_GROUP_QUOTE_TRIM).strip()
                if stripped and _is_nickname_content(stripped):
                    found.append(stripped)
                break
        return " "

    cleaned = _NICKNAME_RE.sub(_capture, clean_invisibles(text))
    return cleaned, "; ".join(found)


def _strip_leading_titles(tokens: list[str], minimum: int = 2) -> tuple[list[str], int]:
    """Drop leading honorifics while more than ``minimum`` tokens remain.

    ``minimum`` is 2 in western order (a name needs a given name and a surname)
    and 1 on either side of a comma inversion, where one token per side is the
    whole of that side.
    """

    dropped = 0
    while len(tokens) > minimum and fold_key(tokens[0]) in TITLES:
        tokens = tokens[1:]
        dropped += 1
    return tokens, dropped


def _all_titles_or_suffixes(tokens: Iterable[str]) -> bool:
    tokens = list(tokens)
    if not tokens:
        return True
    return all(
        _is_suffix_word(token) or fold_key(token) in TITLES for token in tokens
    )


def _split_western(tokens: list[str]) -> tuple[str, str, str]:
    """``[First, Middle..., Last]`` with particle extension (rule 7)."""

    if not tokens:
        return "", "", ""
    if len(tokens) == 1:
        return "", "", tokens[0]
    surname_start = len(tokens) - 1
    for index in range(1, len(tokens) - 1):
        if fold_key(tokens[index]) in PARTICLES:
            surname_start = index
            break
    first = tokens[0]
    middle = " ".join(tokens[1:surname_start])
    last = " ".join(tokens[surname_start:])
    return first, middle, last


# --------------------------------------------------------------------------
# parse_name
# --------------------------------------------------------------------------
def _parse_house_csv(raw: str, tag: str = "house_csv") -> NameParts:
    text = DATE_HINT_RE.sub(" ", raw or "")
    tokens, suffixes = _split_suffixes(_tokens(text))
    # R1 carve-out: token 1 is the GIVEN NAME here, and no one is called V.
    tokens, suffixes = _lift_a_v_that_would_be_a_given_name(tokens, suffixes, 1)
    suffix = _join_suffixes(suffixes)
    if not tokens:
        return NameParts(suffix=suffix, format_detected=tag + "_empty", raw=raw)
    if len(tokens) == 1:
        return NameParts(
            last=tokens[0], suffix=suffix,
            format_detected=tag + "_single_token", raw=raw,
        )
    span = _house_csv_particle_span(tokens)
    if span:
        return NameParts(
            first=tokens[span + 1],
            middle=" ".join(tokens[span + 2:]),
            last=" ".join(tokens[:span + 1]),
            suffix=suffix,
            format_detected=tag + "_particle_surname",
            raw=raw,
        )
    return NameParts(
        first=tokens[1],
        middle=" ".join(tokens[2:]),
        last=tokens[0],
        suffix=suffix,
        format_detected=tag,
        raw=raw,
    )


def _parse_payroll_comma(raw: str, tag: str = "payroll_comma") -> NameParts:
    text = DATE_HINT_RE.sub(" ", raw or "")
    text, glued = _strip_glued_leading_annotation(text)
    if "," not in text:
        parts = _parse_house_csv(text if glued else raw, tag=tag)
        return NameParts(
            first=parts.first, middle=parts.middle, last=parts.last,
            suffix=parts.suffix, nickname_raw=parts.nickname_raw,
            format_detected=parts.format_detected.replace(tag, tag + "_no_comma", 1),
            raw=raw,
            post_nominals=parts.post_nominals,
        )
    marker = tag + "_glued_annotation" if glued else tag
    left, _, right = text.partition(",")
    left_tokens, left_suffixes = _split_suffixes(_tokens(left))
    # R1 carve-out: the surname side has no middle, so a trailing bare "V"
    # there is the suffix the source wrote, not an initial.
    left_tokens, left_suffixes = _lift_a_v_that_would_be_a_given_name(
        left_tokens, left_suffixes, -1)
    right_tokens, right_suffixes = _split_suffixes(_tokens(right))
    right_tokens = _strip_trailing_payroll_annotations(right_tokens)
    suffix = _join_suffixes(list(left_suffixes) + list(right_suffixes))
    last = " ".join(left_tokens)
    if not left_tokens and not right_tokens:
        return NameParts(suffix=suffix, format_detected=marker + "_empty", raw=raw)
    if not right_tokens:
        return NameParts(
            last=last, suffix=suffix,
            format_detected=marker + "_missing_given", raw=raw,
        )
    if not left_tokens:
        # "..., FIRST M" with nothing left of the comma: the given name is all
        # we have, and it is a given name, not a surname.
        return NameParts(
            first=right_tokens[0], middle=" ".join(right_tokens[1:]),
            suffix=suffix, format_detected=marker + "_missing_surname", raw=raw,
        )
    return NameParts(
        first=right_tokens[0],
        middle=" ".join(right_tokens[1:]),
        last=last,
        suffix=suffix,
        format_detected=marker,
        raw=raw,
    )


def _parse_display(raw: str, tag: str = "display") -> NameParts:
    text = _strip_effective_annotation(clean_invisibles(raw or ""))
    text, nickname = _lift_nicknames(text)
    text = _drop_unbalanced_parens(text)
    # v1.1.1 (ruling D): credentials are lifted only for the DISPLAY reader,
    # and BEFORE the comma split, so "Doe, Jane, PhD" loses the credential
    # rather than reading it as a given name.  The senate reader borrows this
    # function under its own tags and is deliberately not covered: a payroll
    # key must not move because a surname folds to "MD".
    post_nominals: list[str] = []
    if tag == "display":
        text, post_nominals = _lift_trailing_post_nominals(text)
    lifted = " ".join(post_nominals)
    if "," in text:
        left, _, right = text.partition(",")
        left_tokens = _tokens(left)
        right_tokens = _tokens(right)
        if left_tokens and right_tokens and not _all_titles_or_suffixes(right_tokens):
            left_tokens, _ = _strip_leading_titles(left_tokens, minimum=1)
            left_tokens, left_suffixes = _split_suffixes(left_tokens)
            right_tokens, _ = _strip_leading_titles(right_tokens, minimum=1)
            right_tokens, right_suffixes = _split_suffixes(right_tokens)
            suffix = _join_suffixes(list(left_suffixes) + list(right_suffixes))
            if left_tokens and right_tokens:
                return NameParts(
                    first=right_tokens[0],
                    middle=" ".join(right_tokens[1:]),
                    last=" ".join(left_tokens),
                    suffix=suffix,
                    nickname_raw=nickname,
                    format_detected=tag + "_comma_inverted",
                    raw=raw,
                    post_nominals=lifted,
                )
        # Fall through: the comma carried only a suffix/honorific, or one side
        # was empty.  Read the whole thing in western order.
        text = text.replace(",", " ")
    tokens = _tokens(text)
    if tag == "display":
        tokens = _strip_trailing_display_artifact(tokens)
    tokens, dropped_titles = _strip_leading_titles(tokens)
    tokens, suffixes = _split_suffixes(tokens)
    suffix = _join_suffixes(suffixes)
    if not tokens:
        return NameParts(
            suffix=suffix, nickname_raw=nickname,
            format_detected=tag + "_empty", raw=raw,
            post_nominals=lifted,
        )
    if len(tokens) == 1:
        return NameParts(
            last=tokens[0], suffix=suffix, nickname_raw=nickname,
            format_detected=tag + "_single_token", raw=raw,
            post_nominals=lifted,
        )
    first, middle, last = _split_western(tokens)
    return NameParts(
        first=first, middle=middle, last=last, suffix=suffix,
        nickname_raw=nickname,
        format_detected=tag + ("_western_titled" if dropped_titles else "_western"),
        raw=raw,
        post_nominals=lifted,
    )


def _parse_senate(raw: str) -> NameParts:
    text = DATE_HINT_RE.sub(" ", raw or "")
    senator = _SENATOR_RE.match(text)
    if senator:
        remainder = senator.group(1)
        # R3(c): a trailing "(NM)" after a senator name is a state, not a
        # nickname.  Only a real postal code is dropped.
        code = _TRAILING_PAREN_CODE_RE.search(remainder)
        if code and fold_key(code.group(1)) in STATE_CODES:
            remainder = remainder[: code.start()]
        parts = _parse_display(remainder, tag="senate_senator")
        return NameParts(
            first=parts.first, middle=parts.middle, last=parts.last,
            suffix=parts.suffix, nickname_raw=parts.nickname_raw,
            format_detected=parts.format_detected, raw=raw,
            post_nominals=parts.post_nominals,
        )
    intern = _INTERN_RE.match(text)
    if intern:
        tokens, suffixes = _split_suffixes(_tokens(intern.group(1)))
        return NameParts(
            last=" ".join(tokens), suffix=_join_suffixes(suffixes),
            format_detected=("senate_intern" if tokens else "senate_intern_empty"),
            raw=raw,
        )
    if "," in text:
        parts = _parse_payroll_comma(raw, tag="senate_comma")
        return parts
    parts = _parse_display(raw, tag="senate_display")
    return parts


def parts_from_payee_key(last: str | None, first: str | None,
                         middle_initial: str | None) -> NameParts:
    """Lift an ALREADY-PARSED payroll triple into the standard's parts.

    The Hill spine stores ``payee_key`` as three parsed fields, not as raw text.
    Re-serialising them into "LAST, FIRST M" and running :func:`parse_name` over
    that used to be wrong in one specific and silent way: ``middle_initial``
    "v" is the first letter of VICTOR or VINCENT, but as a bare token it was a
    valid Roman numeral under v1.0, so the suffix rule lifted it and the key
    lost its middle initial.  R1 retired "V" from the suffix set, so that
    particular hazard is gone; the function stays because ``first`` can still
    be a suffix-shaped word ("JR" as a given name) and because the round trip
    through raw text is a needless reconstruction either way.

    Only ``last`` can still carry a separator-delimited suffix.  The builder's
    ``_name_tokens`` drops whole-token suffixes before joining the surname, but
    a hyphen-glued one ("DALY-JR") survives as a single token, so that is the
    one place rule 2 still has work to do here.

    NOTE (v1.1): this lift can NOT see anything the builder's own tokeniser
    already discarded -- a bare "V" (R1), a particle surname split across
    ``last``/``first`` (R2), or a glued job-title fragment (R3).  Those three
    rules are functions of the RAW payee string, so a caller that needs a v1.1
    key must parse the raw, not lift the triple.

    ``format_detected`` is ``"payee_key"``.
    """

    last = last or ""
    first = first or ""
    middle_initial = middle_initial or ""
    tokens, suffixes = _split_suffixes(_tokens(last))
    return NameParts(
        first=first,
        middle=middle_initial,
        last=" ".join(tokens),
        suffix=_join_suffixes(suffixes),
        format_detected="payee_key",
        raw="%s|%s|%s" % (last, first, middle_initial),
    )


def parse_name(raw: str, source: str) -> NameParts:
    """Parse ``raw`` under the rules for ``source``.  Never raises on input.

    ``source`` must be one of :data:`SOURCES`; an unknown source is a
    programming error and raises ``ValueError``.
    """

    if source not in SOURCES:
        raise ValueError(
            "unknown name source %r (expected one of %s)" % (source, ", ".join(SOURCES))
        )
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raw = str(raw)
    if source == "house_csv":
        return _parse_house_csv(raw)
    if source == "payroll_comma":
        return _parse_payroll_comma(raw)
    if source == "senate":
        return _parse_senate(raw)
    return _parse_display(raw)


# --------------------------------------------------------------------------
# Nickname CLASS table
# --------------------------------------------------------------------------
_CLASS_TABLE: dict[str, str] | None = None
_CLASS_TABLE_LOADED = False


def class_table_path() -> Path:
    """Default location of the v2 class table.

    ``LIVE/fec/staffer_network/name_standard.py`` -> ``LIVE/data/...``.
    Overridable with ``STAFFER_NAME_CLASS_TABLE`` for tests.
    """

    override = os.environ.get("STAFFER_NAME_CLASS_TABLE")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2].joinpath(*_CLASS_TABLE_RELATIVE)


def reset_class_table_cache() -> None:
    """Drop the cached table (tests that swap the table file use this)."""

    global _CLASS_TABLE, _CLASS_TABLE_LOADED
    _CLASS_TABLE = None
    _CLASS_TABLE_LOADED = False


def _coerce_class_entries(payload: Any) -> dict[str, str]:
    if not isinstance(payload, Mapping):
        return {}
    entries = payload.get("entries")
    if not isinstance(entries, Mapping):
        return {}
    table: dict[str, str] = {}
    for variant, stem in entries.items():
        if not isinstance(stem, str):
            continue
        variant_key = fold_key(variant)
        stem_key = fold_key(stem)
        if variant_key and stem_key:
            table[variant_key] = stem_key
    return table


def load_class_table(path: Path | str | None = None) -> tuple[dict[str, str], bool]:
    """Return ``(variant -> class stem, loaded)``.

    A missing or unreadable table degrades to the identity mapping rather than
    raising: ``canonical_first`` then returns the folded first name unchanged,
    which is exactly "no nickname folding" and is safe everywhere.
    """

    global _CLASS_TABLE, _CLASS_TABLE_LOADED
    if path is None:
        if _CLASS_TABLE is not None:
            return dict(_CLASS_TABLE), _CLASS_TABLE_LOADED
        target = class_table_path()
    else:
        target = Path(path)
    table: dict[str, str] = {}
    loaded = False
    try:
        payload = json.loads(Path(target).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = None
    if payload is not None:
        table = _coerce_class_entries(payload)
        loaded = bool(table)
    if path is None:
        _CLASS_TABLE = dict(table)
        _CLASS_TABLE_LOADED = loaded
    return dict(table), loaded


def canonical_first(first: str | None, table: Mapping[str, str] | None = None) -> str:
    """Fold a given name onto its nickname CLASS stem.

    Unknown names fold to themselves (``fold_key``).  The table maps every
    variant -- nickname AND formal form -- onto one class stem, and every stem
    is its own fixed point, so the operation is idempotent by construction.
    """

    key = fold_key(first)
    if not key:
        return ""
    if table is None:
        table, _ = load_class_table()
    return table.get(key, key)


# --------------------------------------------------------------------------
# name_key
# --------------------------------------------------------------------------
def name_key(parts: NameParts, nicknames: bool = True,
             table: Mapping[str, str] | None = None) -> str:
    """``"last|first_class|mi"`` -- rule 6.  Suffix excluded by design."""

    last = fold_key(parts.last)
    first = canonical_first(parts.first, table) if nicknames else fold_key(parts.first)
    middle_initial = fold_key(parts.middle)[:1]
    return "%s|%s|%s" % (last, first, middle_initial)


# --------------------------------------------------------------------------
# display_name
# --------------------------------------------------------------------------
#: R1 retired "V" from :data:`SUFFIXES`, so it can no longer reach this table
#: from a parse.  The ``.get`` fallback below renders any stray value
#: unchanged, so nothing depends on it being here.
_SUFFIX_RENDER = {
    "JR": "Jr.", "SR": "Sr.", "II": "II", "III": "III", "IV": "IV",
}


def _cap_segment(segment: str) -> str:
    if not segment:
        return segment
    lowered = segment.casefold()
    folded = fold_key(segment)
    if lowered.startswith("o'") or lowered.startswith("o’"):
        head, apostrophe, tail = segment[0], segment[1], segment[2:]
        return head.upper() + apostrophe + (tail[:1].upper() + tail[1:].casefold())
    if lowered.startswith("d'") or lowered.startswith("d’"):
        head, apostrophe, tail = segment[0], segment[1], segment[2:]
        return head.upper() + apostrophe + (tail[:1].upper() + tail[1:].casefold())
    if folded.startswith("mc") and len(folded) >= 4:
        return segment[:1].upper() + segment[1:2].casefold() + \
            segment[2:3].upper() + segment[3:].casefold()
    if folded.startswith("mac") and len(folded) >= 6 and folded not in MAC_RUNONS:
        return segment[:1].upper() + segment[1:3].casefold() + \
            segment[3:4].upper() + segment[4:].casefold()
    return segment[:1].upper() + segment[1:].casefold()


def _render_token(token: str) -> str:
    if not token:
        return token
    if not (token.isupper() or token.islower()):
        # Mixed case carries information the source chose; leave it alone.
        return token
    pieces = token.split("-")
    return "-".join(_cap_segment(piece) for piece in pieces)


def _render_part(part: str, allow_particles: bool, leading: bool) -> str:
    tokens = part.split()
    rendered: list[str] = []
    for index, token in enumerate(tokens):
        if (
            allow_particles
            and not (leading and index == 0)
            and fold_key(token) in PARTICLES
            and (token.isupper() or token.islower())
        ):
            rendered.append(token.casefold())
        else:
            rendered.append(_render_token(token))
    return " ".join(rendered)


def display_name(parts: NameParts) -> str:
    """Render ``parts`` as "First Middle Last Suffix" -- rule 8.

    R5: the nickname is NEVER rendered.  Paul's minimal-duplication law.
    """

    chunks: list[str] = []
    if parts.first:
        chunks.append(_render_part(parts.first, allow_particles=False, leading=True))
    if parts.middle:
        middle_tokens = parts.middle.split()
        rendered_middle = []
        for token in middle_tokens:
            rendered = _render_token(token)
            if len(fold_key(token)) == 1 and not rendered.endswith("."):
                rendered += "."
            rendered_middle.append(rendered)
        chunks.append(" ".join(rendered_middle))
    if parts.last:
        chunks.append(
            _render_part(parts.last, allow_particles=True, leading=not chunks)
        )
    if parts.suffix:
        for suffix in parts.suffix.split():
            chunks.append(_SUFFIX_RENDER.get(suffix.upper(), suffix.upper()))
    return " ".join(chunk for chunk in chunks if chunk)
