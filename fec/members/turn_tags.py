# -*- coding: utf-8 -*-
"""TURN-BOUNDARY GRAMMAR for broadcast interview transcripts (Paul-approved 2026-09-19, N12).

WHAT THIS FIXES. fec/members/fetch_interviews.py:202 splits a transcript into turns with
`^\\s*([A-Z][A-Z .,\\'()\\-]{1,70}?):\\s` -- an ALL-CAPS character class anchored at LINE START.
The class holds no backtick and no lower-case letter, so two shapes the networks actually
write are invisible to it:

    O`DONNELL:                  MS NOW writes the backtick form (1,434 occurrences)
    WALLACE (voice-over):       any lower-case parenthetical (CNN/Fox reporter tags)

A tag the splitter cannot see is not a boundary, so the NEXT speaker's words stay inside the
PREVIOUS member's row.  scrub() (:100-113) uses the same ALL-CAPS class in INLINE_LABEL (:97)
and therefore misses the same shapes, and parse_turns_mixed's MIXED_LABEL (:421) admits only a
party tail (", D-Va.") so PBS's ", U.S. Treasury Secretary:" form is invisible too.

MEASURED, NOT GUESSED: audits/laundry_list_closeout_20260918/measR1d/receipts.
5,835 of 153,576 stored INTERVIEW rows carry another speaker's words (MS NOW 8.7%, CNN 4.5%,
PBS / NPR / C-SPAN 0%); 999 member->member reference mentions are mis-attributed; 18 of 1,059
"speaker-verified" desk excerpts show someone else's words; one in-use gold interview stance
item scores a host's voice-over.  Graded precision: 40 of 40 flagged rows real (U2), 72 of 72
residual-class rows real (U2b R1-R4).  Class E (colon-less) was censused but NOT graded there;
N12 graded it separately -- see audits/laundry_list_closeout_20260918/fixN12.

THIS MODULE IS PURE.  No filesystem, no database, no network, no environment read inside any
function, no import from the production tree.  fec/members/fetch_interviews.py cannot be
imported (line 2 resolves MB_ROOT and line 116 reads congestion.json at import time: import ==
execution), so every rule it needs is stated HERE and the collector imports it, rather than the
other way round.  The rules that were copied out of the collector or out of the graded detector
carry a file:line citation at the point of use.

TWO RULES, ONE DIAL.  `IV_TURN_TAGS` selects which collector patterns fetch_interviews.py
compiles: '2' (default) = repaired, '1' = LEGACY, byte for byte.  The stored-text grammar
(split_turns) is the repair itself and has no legacy position: the legacy rule cannot see the
tags at all, which is what the migration exists to undo.

WHY STORED TEXT NEEDS ITS OWN ANCHOR.  Every writer runs re.sub(r'\\s+',' ') before INSERT
(fetch_interviews.py:211,:246), so a stored row has NO newlines (0 of 270,489 -- measR1d
U2_row_census.py:8-11) and a surviving tag is always MID-STRING.  The stored-text rules
therefore anchor on SENTENCE start as well as string start.
"""
import re
import unicodedata

# --------------------------------------------------------------------------- the dial
DIAL = 'IV_TURN_TAGS'
LEGACY = '1'
REPAIRED = '2'
DEFAULT = REPAIRED


def dial_position(value):
    """Normalise a raw dial value.  Anything that is not the legacy position is the default,
    so a typo can never silently select a third behaviour."""
    return LEGACY if str(value or '').strip() == LEGACY else REPAIRED


# ------------------------------------------------------- collector patterns (raw page text)
# LEGACY: fetch_interviews.py:202, :97 and :421 at 35521f9aa, character for character.
LEGACY_LABEL = r'^\s*([A-Z][A-Z .,\'()\-]{1,70}?):\s'
LEGACY_INLINE_LABEL = r'\s([A-Z][A-Z.\'\-]+(?: [A-Z.,\'()\-]+[A-Z.\'\-]*){0,4}):\s'
LEGACY_MIXED_LABEL = (r'^\s*((?:Sen\.|Rep\.|Del\.|Gov\.|Speaker|Leader|Dr\.)?\s*[A-Z][A-Za-z.\'\-]+'
                      r'(?: [A-Z][A-Za-z.\'\-]+){0,3}'
                      r'(?:\s*\([A-Za-z.\- ]+\)|,\s*[DRI]\.?\s*-\s*[A-Za-z][A-Za-z.\- ]{0,20})?):\s')

# REPAIRED.  Three additions and nothing else:
#   (1) the BACKTICK joins the name class                          -> O`DONNELL:
#   (2) a trailing parenthetical of ANY case before the colon      -> WALLACE (voice-over):
#       (':' is excluded from the parenthetical body because parse_turns cuts the label off with
#        sp.index(':') (fetch_interviews.py:210) -- a colon inside the label would cut mid-tag)
#   (3) an optional bracketed timestamp before the name            -> [08:05:03] RAJU (on camera):
# The name part itself stays ALL-CAPS, so ordinary sentence case still cannot become a boundary.
_TS = r'(?:\[\d{1,2}:\d{2}(?::\d{2})?\]\s*)?'
_CAPS_PAREN = r'(?:\s*\([^):]{1,30}\))?'
REPAIRED_LABEL = r'^\s*' + _TS + r'([A-Z][A-Z .,\'`()\-]{1,70}?' + _CAPS_PAREN + r'):\s'
REPAIRED_INLINE_LABEL = (r'\s(' + _TS + r'[A-Z][A-Z.\'`\-]+(?: [A-Z.,\'`()\-]+[A-Z.\'`\-]*){0,4}'
                         + _CAPS_PAREN + r'):\s')
# PBS/CSIS/AEI/Aspen: "Scott Bessent, U.S. Treasury Secretary:" / "Fmr. Attorney General Vanita
# Gupta:".  The legacy tail admits a PARTY suffix only, so the title form is invisible (measR1d
# residual class R4, 12 rows, 12 of 12 graded real).  The widened tail is Title-case only and
# still requires the colon, so it cannot fire on ordinary prose.
_MIXED_TITLE_TAIL = (r'(?:\s*\([A-Za-z.\- ]+\)'
                     r'|,\s*[DRI]\.?\s*-\s*[A-Za-z][A-Za-z.\- ]{0,20}'
                     r'|,\s*(?:U\.S\.|Former|Fmr\.|Acting|Ex-)?\s*[A-Z][A-Za-z.\-]*'
                     r'(?: [A-Z][A-Za-z.\-]*){0,4})?')
REPAIRED_MIXED_LABEL = (r'^\s*((?:Sen\.|Rep\.|Del\.|Gov\.|Speaker|Leader|Dr\.|Fmr\.|Former|Acting)?'
                        r'\s*[A-Z][A-Za-z.\'\-]+(?: [A-Z][A-Za-z.\'\-]+){0,5}'
                        + _MIXED_TITLE_TAIL + r'):\s')


def label_pattern(dial=DEFAULT):
    """The pattern fetch_interviews.py compiles into LABEL (re.M)."""
    return LEGACY_LABEL if dial_position(dial) == LEGACY else REPAIRED_LABEL


def inline_label_pattern(dial=DEFAULT):
    """The pattern fetch_interviews.py compiles into INLINE_LABEL (scrub's truncation marker)."""
    return LEGACY_INLINE_LABEL if dial_position(dial) == LEGACY else REPAIRED_INLINE_LABEL


def mixed_label_pattern(dial=DEFAULT):
    """The pattern fetch_interviews.py compiles into MIXED_LABEL (re.M)."""
    return LEGACY_MIXED_LABEL if dial_position(dial) == LEGACY else REPAIRED_MIXED_LABEL


# --------------------------------------------------- stored-text grammar: shared vocabulary
# Verbatim from the graded detector, measR1d/receipts/U2_row_census.py:85-93, with the four
# grammar gaps the independent grade measured closed (gradeN12/receipts/FINDINGS.txt).  Each
# widening carries its counter-example and its corpus census at the point of use.
HON = (r"(?:SEN\.|REP\.|PRES\.|GOV\.|DR\.|FMR\.|AMB\.|DEL\.|MR\.|MS\.|MRS\.|SENATOR|"
       r"REPRESENTATIVE|CONGRESSMAN|CONGRESSWOMAN|CHAIRMAN|CHAIRWOMAN|PRESIDENT|GOVERNOR|"
       r"SPEAKER|SECRETARY|GENERAL|JUDGE|MAYOR|ADMIRAL|COLONEL|CAPTAIN)")
# GAP (e): ABC/this-week writes the CURLY apostrophe, so O’BRIEN was a half-name to the ASCII
# class -- 183 INTERVIEW rows carry an ALL-CAPS tag with U+2019 (fixN12v2 probe census).
_AP = "'`’‘ʼ"
# The corpus stores accents BOTH ways: 1,428 INTERVIEW rows carry a precomposed letter and 8 carry
# an NFD sequence (a base letter plus U+0301), which is how 'Laura Barrón-López:' in row
# 258713 broke every name class while the precomposed spelling in 258736 matched.  Every name
# class therefore admits the combining-mark block; normalize() strips the marks, so matching a
# member is unaffected either way.
_CMB = r"̀-ͯ"
NAMETOK = r"[A-Z][A-Z." + _CMB + _AP + r"\-]{0,24}"
PAREN = r"(?:\s*\((?:[^)]{1,30})\))?"
# GAP (MECHANISM 2): the legacy tail excluded digits, commas and quotation marks, so
# 'UNIDENTIFIED MALE, MSNBC, AUG. 3, 2017:' (75 rows) and 'BRET BAIER, HOST OF "SPECIAL REPORT":'
# could not match, and 'BRIAN NELSON, GENERAL COUNSEL, CALIFORNIA DEPARTMENT OF JUSTICE
# (2011- 2015):' matched only from its second comma, leaving the NAME with the member (G2d).
# The body is UPPER-CASE ONLY -- is_upper_tag() rejects a lower-case name part anyway, and an
# upper-case-only tail cannot run away into ordinary prose.
_TAILBODY = r"[A-Z0-9." + _CMB + _AP + r"“”\"&/()#,\- ]"
TAIL = r"(?:\s*,\s*[A-Z0-9(\"“]" + _TAILBODY + r"{1,70}?)?"
# 'UNIDENTIFIED MALE #1:' / 'UNIDENTIFIED PERSON #2:' (rows 70228, 70229, 99772).
_NUMSUF = r"(?:\s*#\s?\d{1,3})?"
ANCHOR = r"(?:^|(?<=[.?!])|(?<=\-\-)|(?<=—)|(?<=>>)|(?<=[.?!][\"”']))"
# GAP (MECHANISM 1): NBC/MTP writes 'CHUCK TODD:It' with NO space after the colon, so every MTP
# host turn was invisible.  33 INTERVIEW rows carry the shape and a census of every tag it
# produces (fixN12v2 v2_01 probe) found 33/33 real speaker tags and no false marker; the
# no-space form is admitted only for a STRONG tag -- see find_turn_tags.
_SEP = r"\s*(::?)(?=\s|$|—|\-\-|[A-Z\"“])"
TAG = re.compile(ANCHOR + r"[ ]{0,2}((?:" + HON + r"\s+)?" + NAMETOK +
                 r"(?:\s+" + NAMETOK + r"){0,3}" + TAIL + _NUMSUF + PAREN + r")" + _SEP)
# Class C: the same tag body with NO anchor requirement, but the parenthetical is MANDATORY --
# that is what keeps it off "told CNN: ..." (U2b_recall_probe.py:46-49).
TAG_NOANCHOR = re.compile(r"((?:" + HON + r"\s+)?" + NAMETOK + r"(?:\s+" + NAMETOK + r"){0,3}"
                          + TAIL + _NUMSUF + r"\s*\([^)]{1,30}\))" + _SEP)
# A broadcast parenthetical is itself proof of a speaker tag, so a STOP name in front of one
# ('FOX (voice-over):', where FOX is CNN's Lauren Fox) is a FALSE rejection, and so is a
# correspondent surname the lexicon never saw in full-name form (U2b_recall_probe.py:44-45).
BROADCAST_PAREN = re.compile(r'\((?:voice[\s\-]?over|on\s?camera|through\s+(?:a\s+)?translator|'
                             r'voiceover|on\s?the\s?phone|via\s+\w+|audio|recorded|taped)\)', re.I)
# Class D: mixed-case label at a sentence start, in the legs whose collector uses
# parse_turns_mixed (U2b_recall_probe.py:51-55).  Widened over the probe's pattern so the match
# starts at the NAME, not mid-title: the probe's own offsets left "Scott Bessent, U.S." glued to
# the member's words.  Justified and re-graded in fixN12.
# GAP (a): MIXED's name tokens were ASCII-only and demanded an upper-case start for EVERY token,
# so PBS's 'Laura Barrón-López:' (accented) and 'Fred de Sam Lazaro:' (lower-case particle) never
# matched and up to 270 words of anchor narration stayed in the member's row (rows 258713,
# 257343, 258736, 258820, 258130).  The widening is exactly: Latin-1/Latin-Extended letters, and
# at most two lower-case name particles between tokens.  It is still Title-case only and still
# demands the colon, so it cannot fire on ordinary prose.
_MU = r"A-ZÀ-ÖØ-Þ"
_ML = r"a-zß-öø-ÿ" + _CMB
_MANY = r"A-Za-zÀ-ɏ" + _CMB + _AP + r".\-"
_MTOK = r"[" + _MU + r"][" + _ML + r"][" + _MANY + r"]{1,20}"
# A SUBSEQUENT token keeps the legacy shape, which admits a middle initial ('Jamil N. Jaffer',
# row 175681) and an ALL-CAPS token; only the first token must look Title-case.
_MTOK2 = r"[" + _MU + r"][" + _MANY + r"]{1,20}"
_MPART = r"(?:de|del|della|der|den|di|du|da|dos|la|le|van|von|bin|al|el)"
MIXED = re.compile(r"(?:^|(?<=[.?!])|(?<=\-\-)|(?<=—))[ ]{0,2}"
                   r"((?:Sen\.|Rep\.|Del\.|Gov\.|Dr\.|Speaker|Leader|Fmr\.|Former|Acting)?\s*"
                   # The first name token may not be a bare abbreviation: PBS writes "Mr.
                   # President. Rep. Nancy Pelosi, De-Calif.:", where the '.' after 'Mr' opens a
                   # false sentence and 'President.' would otherwise be read as the first name
                   # token, moving one of the member's own words into the removed segment.
                   r"(?![A-Z][a-z]+\.\s)"
                   + _MTOK + r"(?:\s+(?:" + _MPART + r"\s+)?" + _MTOK2 + r"){1,5}"
                   r"(?:,\s*(?:U\.S\.|Former|Fmr\.|Acting|Ex-)?\s*[A-Z][A-Za-z.\-]*"
                   r"(?:\s+[A-Z][A-Za-z.\-]*){0,4})?"
                   r"(?:\s*\([A-Za-z.\- ]{1,24}\))?)(:)(?=\s)")
MIXED_CASE_SHOWS = frozenset(('pbs-newshour', 'csis', 'aei', 'aspensf'))
# Class E: the splitter sometimes loses the colon entirely ("... BLUMENTHAL, D-CONN.There's a
# gra...").  Lane B's NOCOLON regex, verbatim (U2c_colonless.py:17-20).
NOCOLON_HON = (r'(?:SEN\.|REP\.|PRES\.|GOV\.|DR\.|FMR\.|AMB\.|SENATOR|REPRESENTATIVE|CONGRESSMAN|'
               r'CONGRESSWOMAN|CHAIRMAN|CHAIRWOMAN|PRESIDENT|GOVERNOR|SPEAKER)')
NOCOLON_TAIL = (r'(?:\s*,\s*[RDI]-[A-Z][A-Z\.]{1,14}\.?|\s*\([RDI]-[A-Z]{2}[^)]{0,22}\)'
                r'|\s*\([^)]{1,22}\))?')
NOCOLON = re.compile(NOCOLON_HON + r'\s+[A-Z][A-Z\'\.\-]{1,22}'
                     r'(?:\s+[A-Z][A-Z\'\.\-]{1,22}){0,3}' + NOCOLON_TAIL)
# Class E needs two guards the ungraded lane-B regex had none of.  N12 graded the class (a 25-row
# seeded sample plus every false-marker candidate; receipts in
# audits/laundry_list_closeout_20260918/fixN12) and found exactly two failure shapes:
#   (i)  a match inside an unclosed '(' is a stage direction, not a turn:
#        "(DEEPFAKE RECORDING OF SEN. RICHARD BLUMENTHAL'S VOICE PLAYS)" (rows 225771, 242500)
#        and "(SEN. DICK DURBIN BANGS THE GAVEL)" (row 225128).
#   (ii) without a colon the regex can start INSIDE the tag, because its honorific list holds
#        role words that also sit in a role tail: "KAMALA HARRIS, VICE | PRESIDENT OF THE UNITED
#        STATES" cut after 'VICE', leaving the host's name glued to the member's words.  The tag
#        is the whole ALL-CAPS run, so the start is extended backwards over it -- never across a
#        token carrying a lower-case letter and never across a sentence terminator, so
#        "...raised it with the FBI. SEN. X" keeps 'FBI.' with the member.  After the extension
#        the start must be sentence-anchored, which is what keeps the rule off a mid-sentence
#        mention ("I spoke with SEN. PATTY MURRAY (D-WA) about the bill").
# A POSSESSIVE guard was tried and REJECTED on the evidence: the only hit it rejected that the
# parenthetical guard does not is row 191195, "HENRY ROBERTO CUELLAR, (D) U.S REPRESENTATIVE FOR
# TEXAS'S 28TH", which is a REAL bleed.  It cost a true positive and bought nothing.
_ANCHOR_AT_END = re.compile(ANCHOR + r'[ ]{0,2}$')
# A bracketed timestamp and an ALL-CAPS parenthetical ((CHEERING), (APPLAUSE)) sit between the
# sentence end and the tag on the CNN legs, so the run has to be able to cross them.
_ALLCAPS_TOKEN = re.compile(r"^[A-Z0-9(\[][A-Z0-9.," + _CMB + _AP + r"()\[\]:\-/&“”\"]*$")

# GAP (b): AN ABBREVIATION PERIOD MUST NOT SATISFY THE SENTENCE ANCHOR.  'DONALD TRUMP (R),
# FORMER U.S. PRESIDENT AND ...' let the '.' of 'U.S.' open a false sentence, so the tag became
# the fragment 'PRESIDENT AND' and 'DONALD TRUMP (R), FORMER U.S.' stayed with the member
# (rows 82055, 71147, 70812, 205497; 514 INTERVIEW rows carry a dotted abbreviation in front of
# an ALL-CAPS run).  Two mechanisms, and they are each other's safety net:
#   * the ALL-CAPS run may CROSS a token that carries an INTERNAL period (U.S., F.B.I., D.C.),
#     which walks the start back to the head of the chyron;
#   * the anchor is then re-tested, and an anchor whose last token is an abbreviation is
#     REFUSED.  So crossing only ever succeeds when it lands on a real sentence end: "...raised
#     it with the F.B.I. SEN. X" crosses F.B.I., finds the lower-case 'the', stops, fails the
#     anchor, and the hit is dropped rather than mis-cut.
# 'FBI.' has no internal period, so it is a sentence end, not an abbreviation, and the graded
# row that must keep it with the member still does (test: ACRO_END).
# A TITLE abbreviation is also crossable, because a chyron head like 'MR. WILLIAM CAMPBELL,
# FORMER PRESIDENT AND CHIEF' otherwise leaves 'MR.' with the member (row 274133).
_TITLE_ABBREV = frozenset('''MR. MRS. MS. DR. ST. JR. SR. PROF. REV. GEN. GOV. SEN. REP. DEL.
LT. COL. ADM. CAPT. AMB. PRES. FMR. HON.'''.split())
# 'NO.' is deliberately ABSENT: "No." is a complete answer in an interview, not an abbreviation,
# and treating it as one silently un-flagged six real MS NOW bleeds (rows 260912, 263507, 264343,
# 268284, 274221, 275455, each an O`DONNELL turn that follows the member's "No.").
_DOTTED_ABBREV = _TITLE_ABBREV | frozenset('''INC. CO. CORP. LTD. VS. ETC. FIG. DEPT. EST.
APPROX. JAN. FEB. MAR. APR. JUN. JUL. AUG. SEP. SEPT. OCT. NOV. DEC.'''.split())


def _bare(token):
    return (token or '').strip('"“”’\'()[],;:')


def _is_ellipsis(t):
    """'Mamdani...' is a trailing-off, not an abbreviation: its 'internal' period is the ellipsis
    (row 174172, where reading it as one un-flagged a real mixed-case bleed)."""
    return t.endswith('..')


def is_abbreviation(token):
    """True for a token whose final '.' is an abbreviation point, not a sentence end."""
    t = _bare(token)
    if not t.endswith('.') or _is_ellipsis(t):
        return False
    return t.upper() in _DOTTED_ABBREV or '.' in t[:-1]


def _crosses_run(token):
    """An INTERNAL-period abbreviation (U.S., F.B.I.) or a TITLE abbreviation (MR., SEN.) may be
    crossed by the ALL-CAPS run.  Nothing else: 'FBI.' and 'No.' are sentence ends."""
    t = _bare(token)
    if not t.endswith('.') or _is_ellipsis(t):
        return False
    return '.' in t[:-1] or t.upper() in _TITLE_ABBREV


def anchor_ok(text, start):
    """Is `start` at a real sentence boundary?  `_ANCHOR_AT_END`, minus the abbreviation trap."""
    head = (text or '')[:start]
    if not _ANCHOR_AT_END.search(head):
        return False
    toks = head.rstrip().split()
    return not (toks and is_abbreviation(toks[-1]))

# U2_row_census.py:95-104, verbatim.  An acronym, an agency, a network, a heading word: none of
# them is a speaker.  This is the guard that keeps ALL-CAPS document headings from splitting --
# 'DEPARTMENT OF DEFENSE MILITARY RETIREMENT FUND:' is every-token-STOP and never fires.
STOP = frozenset('''USA GAO DOI EPA FBI CIA NSA DOJ DOD HHS IRS FEMA SNAP CNN FOX MSNBC NPR PBS ABC CBS NBC BBC AP
NATO UN EU WHO CDC NIH FDA SEC FCC FTC HUD VA DHS TSA ICE CBP ATF DEA NASA NOAA USDA DOT FAA GDP CEO ACA
WCNSF KUCB URI NPS DACA GAC SAFE RELIEF ACT BANKING COMMITTEE SENATE HOUSE CONGRESS CAUCUS SUBCOMMITTEE
CHAIR RANKING DEMOCRAT REPUBLICAN MAJORITY MINORITY LEADER WHIP HEALTH UNITS NEXT STEPS TITLE SECTION
GOP COVID US DC AI PS NOTE QUESTION ANSWER BACKGROUND SUMMARY CONCLUSION FUND DEPARTMENT DEFENSE MILITARY
RETIREMENT ADMINISTRATION BUREAU OFFICE AGENCY SERVICE SERVICES PROGRAM PROGRAMS ACTION REPORT REPORTS
UPDATE BREAKING LIVE VIDEO AUDIO TAPE CLIP TRANSCRIPT WATCH LISTEN READ MORE ALSO EDITOR NOTE
STATE STATES UNITED AMERICA AMERICAN FEDERAL NATIONAL INTERNATIONAL GLOBAL WORLD NEWS TODAY TONIGHT
MONDAY TUESDAY WEDNESDAY THURSDAY FRIDAY SATURDAY SUNDAY JANUARY FEBRUARY MARCH APRIL MAY JUNE JULY
AUGUST SEPTEMBER OCTOBER NOVEMBER DECEMBER'''.split())
HONRE = re.compile(r'^(?:' + HON + r')$')
# Mixed-case headings that must never become a class-D boundary.
MIXED_STOP = frozenset(('Editor', 'Editors', 'Note', 'Notes', 'Transcript', 'Update', 'Correction',
                        'Related', 'Watch', 'Read', 'Listen', 'Advertisement', 'Subscribe'))

# A speaker name is never a filler word, a connective or a bare question marker.  Without this
# set 'OK. Q' reads as a two-token name (rows 278454, 278528: "OK. Q: <moderator question>", and
# 110 corpus rows carry a bare "Q: " turn), and 'PRESIDENT AND' -- the fragment the abbreviation
# trap used to produce -- reads as a speaker.  A bare 'Q:' stays UNREPAIRED by design: one token
# that can never satisfy qualifies().
FILLER = frozenset('''OK OKAY YES YEAH YEP NO NOPE WELL SO AND BUT OR RIGHT SURE THANK THANKS
HI HELLO BYE UH UM AH OH Q A AN THE OF FOR TO ON IN AT WITH FROM BY AS IS ARE WAS WERE
I ME MY WE OUR YOU YOUR HE SHE IT THEY'''.split())
# A broadcast role label names a person who is definitionally NOT the row's member.  ALL-CAPS
# only, so it cannot fire inside ordinary prose.  75 corpus rows carry an UNIDENTIFIED/UNKNOWN
# label (fixN12v2 probe census); 'ANNOUNCER' is the one single-token role the no-space census saw.
BROADCAST_ROLE = re.compile(
    r'\b(?:(?:UNIDENTIFIED|UNKNOWN)\s+(?:MALE|FEMALE|MAN|WOMAN|MEN|WOMEN|SPEAKER|SPEAKERS|'
    r'REPORTER|REPORTERS|VOICE|VOICES|PERSON|PERSONS|PEOPLE|PROTESTER|PROTESTORS|PROTESTER|'
    r'PROTESTORS|CROWD|AUDIENCE|CHILD|STUDENT|TRANSLATOR)'
    r'|ANNOUNCER|NARRATOR|MODERATOR|INTERPRETER|AUTOMATED VOICE)\b')

ALLCAPS_SHARE = 0.85          # U2_row_census.py:108-110
MIN_TURN_WORDS = 8            # fetch_interviews.py:230, :433 -- micro-interjections drop

# The migration's three-way verdict on WHOSE tag it is.  FAIL SAFE: OWN is lenient (a surname
# match anywhere in the tag is enough, so a name-form quirk can never cut the member's own
# words), FOREIGN is strict (the tag must clearly name someone else or be a broadcast role), and
# anything in between is AMBIGUOUS -- the row is left exactly as it is and listed as residue.
OWN = 'own'
FOREIGN = 'foreign'
AMBIGUOUS = 'ambiguous'
OWNERSHIPS = (OWN, FOREIGN, AMBIGUOUS)

CLASS_A = 'A_anchored_caps'
CLASS_B_STOP = 'B_stop_name_paren'
CLASS_B_LEX = 'B_unknown_surname_paren'
CLASS_C = 'C_unanchored_paren'
CLASS_D = 'D_mixed_case_label'
CLASS_E = 'E_colonless_honorific'
CLASSES = (CLASS_A, CLASS_B_STOP, CLASS_B_LEX, CLASS_C, CLASS_D, CLASS_E)


def normalize(s):
    """fetch_interviews.py:120-123 (norm) with every apostrophe form folded to the straight one
    first, so O`DONNELL, O’DONNELL and O'Donnell normalise alike (U2_row_census.py:46-50).
    NFD + combining-mark removal is what makes Barrón-López fold onto BARRON-LOPEZ."""
    s = unicodedata.normalize('NFD', s or '')
    s = ''.join(c for c in s if not unicodedata.combining(c))
    for ch in _AP:
        s = s.replace(ch, "'")
    return re.sub(r"[^a-z ]", '', s.lower()).strip()


NICK_SETS = (
    {'bernard', 'bernie'}, {'charles', 'chuck', 'charlie'}, {'christopher', 'chris'},
    {'richard', 'dick', 'rich', 'rick'}, {'michael', 'mike'}, {'james', 'jim', 'jimmy'},
    {'robert', 'bob', 'rob', 'bobby'}, {'thomas', 'tom', 'tommy'}, {'daniel', 'dan', 'danny'},
    {'joseph', 'joe', 'joey'}, {'timothy', 'tim'}, {'patrick', 'pat'},
    {'william', 'bill', 'will', 'billy'}, {'edward', 'ed', 'ted', 'eddie'}, {'theodore', 'ted'},
    {'elizabeth', 'liz', 'beth', 'betty'}, {'katherine', 'kathryn', 'katie', 'kate', 'kathy'},
    {'margaret', 'maggie', 'meg'}, {'benjamin', 'ben'}, {'alexander', 'alex'}, {'anthony', 'tony'},
    {'andrew', 'andy', 'drew'}, {'matthew', 'matt'}, {'jonathan', 'jon'}, {'nicholas', 'nick'},
    {'gregory', 'greg'}, {'lawrence', 'larry'}, {'gerald', 'jerry'}, {'ronald', 'ron', 'ronny'},
    {'donald', 'don'}, {'kenneth', 'ken'}, {'stephen', 'steven', 'steve'}, {'david', 'dave'},
    {'samuel', 'sam'}, {'frederick', 'fred'}, {'susan', 'sue', 'suzy'},
    {'deborah', 'debbie', 'deb'}, {'cynthia', 'cindy'}, {'jacquelyn', 'jacqueline', 'jackie'},
    {'raphael', 'rafael', 'raffi'}, {'joshua', 'josh'}, {'zachary', 'zach', 'zack'},
    {'abigail', 'abby'}, {'rebecca', 'becca', 'becky'}, {'jefferson', 'jeff'}, {'jeffrey', 'jeff'},
    {'mitchell', 'mitch'}, {'randall', 'randy'}, {'terrence', 'terri', 'terry'},
    {'vincent', 'vince'},
)
NICK = {}
for _s in NICK_SETS:
    for _a in _s:
        NICK.setdefault(_a, set()).update(_s)


def variants(first):
    """fetch_interviews.py:148-149 (_variants)."""
    return NICK.get(first, set()) | {first}


def firstname_compatible(a, b):
    """fetch_interviews.py:171-175 (_firstname_compatible): 'tom'~'thomas' yes, nickname classes
    yes, 'raul'~'adelita' NO (the succession-seat misattribution guard)."""
    if not a or not b:
        return False
    a, b = a.lower(), b.lower()
    return a == b or a.startswith(b) or b.startswith(a) or bool(variants(a) & variants(b))


def roster_surnames(members):
    """The last normalised token of every roster name."""
    out = set()
    for m in members or ():
        p = normalize(m).split()
        if p:
            out.add(p[-1])
    return frozenset(out)


_TOKSPLIT = re.compile(r"[^A-Za-zÀ-ɏ." + _CMB + _AP + r"\-]+")
_TOKSTRIP = ".'`’‘ʼ-"


def tag_parts(tag):
    """-> (has_honorific, [UPPER name tokens]) with the parenthetical and comma tail removed.
    U2_row_census.py:113-123."""
    core = re.sub(r'\([^)]*\)', ' ', tag or '')
    core = core.split(',')[0]
    toks = [t for t in _TOKSPLIT.split(core) if t.strip(_TOKSTRIP)]
    if not toks:
        return False, []
    has_hon = bool(HONRE.match(toks[0].upper()))
    words = [t for t in toks if not HONRE.match(t.upper())]
    up = [w.strip(_TOKSTRIP).upper() for w in words if w.strip(_TOKSTRIP)]
    return has_hon, up


def name_tokens(tag):
    """Every normalised name-ish token anywhere in the tag -- the comma tail and the parenthetical
    included -- plus the hyphen/particle COMPONENTS of each.  This is deliberately over-inclusive:
    it is only ever used to decide that a tag MIGHT be the row's own member, never that it is
    somebody else."""
    out = []
    for w in _TOKSPLIT.split(tag or ''):
        n = normalize(w)
        if n:
            out.append(n)
        for part in re.split(r'[-\s]+', w):
            p = normalize(part)
            if p and p != n:
                out.append(p)
    return out


def own_lenient(tag, member):
    """FAIL-SAFE ownership: does the member's surname appear ANYWHERE in this tag?

    Deliberately weaker than names_member() (which the COLLECTOR uses and which keeps its
    succession-seat first-name guard).  The migration cuts text, so the expensive error is
    cutting the member's own words because a name form did not match: 'JOHNSON (voice-over)' on
    a Mike Johnson row reads as OWN here, which is why row 245558 -- five segments the grade
    proved belong to a Summit Metro Parks officer, not to the Representative -- is now left
    alone instead of being re-attributed."""
    mp = normalize(member).split()
    if not mp:
        return False
    return mp[-1] in set(name_tokens(tag))


def _near_member(tag, member):
    """A name form that is CLOSE to the member's surname but not equal to it: never cut on it."""
    mp = normalize(member).split()
    if not mp or len(mp[-1]) < 4:
        return False
    msur = mp[-1]
    for t in set(name_tokens(tag)):
        if len(t) < 4 or t == msur:
            continue
        if t.startswith(msur) or msur.startswith(t):
            return True
    return False


def tag_ownership(tag, member):
    """-> OWN | FOREIGN | AMBIGUOUS.  See the OWN/FOREIGN/AMBIGUOUS note above."""
    if not tag:
        return AMBIGUOUS
    if own_lenient(tag, member):
        return OWN
    if _near_member(tag, member):
        return AMBIGUOUS
    if BROADCAST_ROLE.search(tag):
        return FOREIGN
    _hon, up = tag_parts(tag)
    named = [w for w in up if w not in STOP and w not in FILLER]
    if named:
        return FOREIGN
    if BROADCAST_PAREN.search(tag):
        # 'FOX (voice-over)' -- CNN's Lauren Fox, not the network: a broadcast parenthetical is
        # itself proof of a speaker tag (U2b_recall_probe.py:44-45), and it is not the member.
        return FOREIGN
    return AMBIGUOUS


def is_upper_tag(tag):
    """The NAME part must actually be ALL CAPS; a lower-case parenthetical is allowed.
    U2_row_census.py:126-130."""
    core = re.sub(r'\([^)]*\)', ' ', tag or '')
    letters = [c for c in core if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def is_allcaps_row(text):
    """An ALL-CAPS row is a heading block, not a turn sequence: it is skipped whole, which is
    what leaves the 7,117 all-caps Washington Journal rows untouched (U2_row_census.py:108-110,
    U5c_allcaps_cspan.out.txt)."""
    letters = [c for c in (text or '') if c.isalpha()]
    return bool(letters) and sum(1 for c in letters if c.isupper()) / len(letters) > ALLCAPS_SHARE


def qualifies(tag, lexicon=()):
    """U2_row_census.py:159-172.  The name must not be all STOP or FILLER tokens, and one of:
    an honorific is present / >=2 live name tokens / a broadcast role label / exactly one token
    that the corpus lexicon knows as a speaker surname.  The last clause admits bare 'WALLACE:'
    and 'O`DONNELL:' without admitting 'ICE:'; the FILLER clause is what keeps 'OK. Q' from
    reading as a two-token name (rows 278454 / 278528)."""
    hon, up = tag_parts(tag)
    live = [w for w in up if w not in STOP and w not in FILLER]
    if not up or not live:
        return bool(BROADCAST_ROLE.search(tag or ''))
    if hon:
        return True
    if len(live) >= 2:
        return True
    if BROADCAST_ROLE.search(tag or ''):
        return True
    return len(up) == 1 and normalize(up[0]) in (lexicon or ())


def names_member(tag, member):
    """Does this tag name the row's own attributed member?  U2_row_census.py:175-190: the
    surname must appear, and when the tag carries >=2 name tokens one of the others must be
    first-name compatible -- so 'SEN. RON JOHNSON' does not read as Mike Johnson's own tag."""
    hon, up = tag_parts(tag)
    if not up:
        return False
    mp = normalize(member).split()
    if not mp:
        return False
    msur, mfirst = mp[-1], mp[0]
    tn = [normalize(w) for w in up if normalize(w)]
    if msur not in tn:
        return False
    if len(tn) >= 2:
        cand = [t for t in tn if t != msur]
        return any(firstname_compatible(t, mfirst) for t in cand) or not cand
    return True


def mixed_names_member(label, member):
    """names_member for a mixed-case class-D label (U2b_recall_probe.py:144-149)."""
    parts = normalize(label).split()
    mp = normalize(member).split()
    return bool(mp) and mp[-1] in parts


def speaker_lexicon(texts):
    """SPEAKER_LEX pass 1, U2_row_census.py:141-154: every surname that appears as the last name
    token of a qualifying MULTI-token ALL-CAPS tag anywhere in the corpus.  Pure: the caller
    supplies the texts.  Union this with roster_surnames()."""
    out = set()
    for txt in texts:
        if not txt or is_allcaps_row(txt):
            continue
        for m in TAG.finditer(txt):
            tag = m.group(1)
            if not is_upper_tag(tag):
                continue
            hon, up = tag_parts(tag)
            if len(up) >= 2 and not all(w in STOP for w in up):
                last = normalize(up[-1])
                if len(last) >= 3 and up[-1] not in STOP:
                    out.add(last)
    return frozenset(out)


class Hit(object):
    """One turn tag found in stored text.  `start`/`end` bound the tag AND its separator, so
    text[end:] is the speaker's words and text[:start] is the previous speaker's.
    `ownership` is the three-way verdict; `own` is kept as its OWN shorthand."""

    __slots__ = ('start', 'end', 'tag', 'cls', 'ownership')

    def __init__(self, start, end, tag, cls, ownership):
        self.start, self.end, self.tag, self.cls = start, end, tag, cls
        self.ownership = ownership

    @property
    def own(self):
        return self.ownership == OWN

    @property
    def foreign(self):
        return self.ownership == FOREIGN

    def __repr__(self):
        return 'Hit(%d,%d,%r,%s,%s)' % (self.start, self.end, self.tag, self.cls, self.ownership)


def inside_parenthetical(text, start):
    """True when `start` sits inside an unclosed '(' -- a stage direction, not a turn."""
    opened = text.rfind('(', 0, start)
    return opened >= 0 and opened > text.rfind(')', 0, start)


def extend_over_allcaps_run(text, start):
    """Move `start` back over the contiguous ALL-CAPS tokens that belong to the same tag.

    A token ending in '.?!' is a sentence end and stops the run -- EXCEPT an internal-period
    abbreviation (U.S., F.B.I.), which is part of the chyron and is crossed.  Crossing is safe
    because the caller re-tests the sentence anchor afterwards with anchor_ok(), which refuses an
    anchor whose last token is an abbreviation: a crossing that does not land on a real sentence
    end simply drops the hit."""
    while True:
        head = text[:start].rstrip(' ')
        if len(head) == len(text[:start]):      # no space immediately before: nothing to join
            return start
        cut = head.rfind(' ') + 1
        token = head[cut:]
        if not token or not _ALLCAPS_TOKEN.match(token):
            return start
        if token[-1] in '.?!' and not _crosses_run(token):
            return start
        if any(c.islower() for c in token):
            return start
        start = cut


def strip_leading_filler(text, start, tag):
    """'OK. OK. WILD (voice-over)' is not a three-token name: it is two of the member's own words
    and then the tag (rows 69544 / 244993, where reading the filler as part of the tag emptied
    the kept text).  Filler is dropped only while the remaining start is still sentence-anchored.
    """
    while True:
        m = re.match(r"([A-Za-z]{1,6})[.,!?]?[ ]+", tag)
        if not m or m.group(1).upper() not in FILLER:
            return start, tag
        nxt = start + m.end()
        if not anchor_ok(text, nxt):
            return start, tag
        start, tag = nxt, tag[m.end():]


def _add(hits, taken, start, end, tag, cls, member):
    if any(s < end and start < e for s, e in taken):
        return False
    taken.append((start, end))
    hits.append(Hit(start, end, tag, cls, tag_ownership(tag, member)))
    return True


def _nospace_is_strong(text, tag, sep_end):
    """A colon with no space after it is a turn boundary only for a STRONG tag: an honorific, a
    broadcast parenthetical or role, or two live name tokens.  Measured: 33 INTERVIEW rows carry
    the shape and every tag it produces is a real speaker (fixN12v2 v2_01 census)."""
    if sep_end >= len(text) or text[sep_end].isspace():
        return True                                   # the ordinary spaced form
    hon, up = tag_parts(tag)
    live = [w for w in up if w not in STOP and w not in FILLER]
    return bool(hon or len(live) >= 2 or BROADCAST_PAREN.search(tag)
                or BROADCAST_ROLE.search(tag))


def find_turn_tags(text, member, lexicon=(), show=None, colonless=True):
    """Every turn tag in one stored row, ordered by position.

    The five classes are exactly the measured ones (measR1d U2 + U2b R1-R4 + U2c R5); each hit
    carries the class that found it so the migration's census is comparable with the receipts
    row for row.  `own` says whether the tag names the row's attributed member.
    """
    t = text or ''
    hits, taken = [], []
    if not t or is_allcaps_row(t):
        return []
    lex = lexicon or ()
    # A / B: anchored tag.  B relaxes only the STOP and lexicon tests, and only when a
    # BROADCAST parenthetical is present.
    for m in TAG.finditer(t):
        start, tag = strip_leading_filler(t, m.start(1), m.group(1))
        if not is_upper_tag(tag):
            continue
        if not _nospace_is_strong(t, tag, m.end(0)):
            continue
        if not anchor_ok(t, start):
            # the anchor sits on an abbreviation point: walk back over the chyron's ALL-CAPS run
            # and keep the hit only if THAT lands on a real sentence end (rows 82055 / 205497).
            ext = extend_over_allcaps_run(t, start)
            if ext == start or not anchor_ok(t, ext):
                continue
            tag, start = t[ext:m.end(1)], ext
        if qualifies(tag, lex):
            cls = CLASS_A
        elif BROADCAST_PAREN.search(tag):
            hon, up = tag_parts(tag)
            if not up:
                continue
            cls = CLASS_B_STOP if all(w in STOP for w in up) else (
                CLASS_B_LEX if len(up) == 1 else CLASS_A)
        else:
            continue
        _add(hits, taken, start, m.end(0), tag, cls, member)
    # C: unanchored, parenthetical mandatory, name not all STOP.
    for m in TAG_NOANCHOR.finditer(t):
        tag = m.group(1)
        if not is_upper_tag(tag):
            continue
        if not _nospace_is_strong(t, tag, m.end(0)):
            continue
        hon, up = tag_parts(tag)
        if not up or all(w in STOP or w in FILLER for w in up):
            continue
        _add(hits, taken, m.start(1), m.end(0), tag, CLASS_C, member)
    # D: mixed-case label, only in the legs whose collector writes mixed-case labels.
    if show in MIXED_CASE_SHOWS:
        for m in MIXED.finditer(t):
            lab = m.group(1)
            parts = [w for w in _TOKSPLIT.split(lab) if w]
            if len(parts) < 2:
                continue
            if any(w.strip(_TOKSTRIP) in MIXED_STOP for w in parts):
                continue
            if any(w.strip(_TOKSTRIP).upper() in STOP for w in parts):
                continue
            if not anchor_ok(t, m.start(1)):
                continue
            _add(hits, taken, m.start(1), m.end(0), lab, CLASS_D, member)
    # E: colon-less honorific form.  Ungraded in measR1d; N12 graded it and kept the sentence
    # anchor OFF to reproduce the published census -- the caller decides.
    if colonless:
        for m in NOCOLON.finditer(t):
            if inside_parenthetical(t, m.start()):
                continue
            start = extend_over_allcaps_run(t, m.start())
            if start and not anchor_ok(t, start):
                continue
            _add(hits, taken, start, m.end(), t[start:m.end()], CLASS_E, member)
    hits.sort(key=lambda h: h.start)
    return hits


def first_foreign_hit(text, member, lexicon=(), show=None, colonless=True):
    """The CUT POINT for the truncate-only migration: the first tag that clearly belongs to
    somebody else.  None when the row carries no foreign tag -- a self-tag-only row is not a
    bleed and an ambiguous-tag row is not one either, as far as this function is concerned (the
    migration inspects ambiguity separately and skips the row)."""
    for h in find_turn_tags(text, member, lexicon, show, colonless):
        if h.ownership == FOREIGN:
            return h
    return None


def split_turns(text, member, lexicon=(), show=None, colonless=True):
    """Split one stored row into ordered turns.

    -> [(speaker_tag_or_None, segment_text), ...] with the first element always the row's own
    attributed member (tag None).  Returns a single (None, text) element when the row carries
    no turn tag at all, so a caller can treat "nothing to do" and "one turn" identically.

    NOTHING IS LOST: use split_spans() when you need to prove it.  Every character of `text`
    belongs to exactly one span there, in order.
    """
    return [(tag, seg) for tag, seg, _s, _e, _c in split_spans(text, member, lexicon, show,
                                                              colonless)]


def split_spans(text, member, lexicon=(), show=None, colonless=True):
    """split_turns with the accounting.

    -> [(tag_or_None, segment_text, span_start, span_end, cls_or_None), ...] where the spans
    tile the whole string: span_start of the first element is 0, each span_end is the next
    span_start, and the last span_end is len(text).  ''.join(text[s:e]) == text, always.
    The segment text is the span with the tag and its separator removed and the ends stripped
    -- the same shape the collector stores (fetch_interviews.py:210-211).
    """
    t = text or ''
    hits = find_turn_tags(t, member, lexicon, show, colonless)
    out = []
    starts = [0] + [h.start for h in hits]
    ends = [h.start for h in hits] + [len(t)]
    out.append((None, t[0:ends[0]].strip(), 0, ends[0], None))
    for i, h in enumerate(hits):
        span_end = ends[i + 1]
        out.append((h.tag, t[h.end:span_end].strip(), starts[i + 1], span_end, h.cls))
    return out


def own_words(text, member, lexicon=(), show=None, colonless=True):
    """The kept text for a row: the attributed member's own words, up to the first tag."""
    return split_spans(text, member, lexicon, show, colonless)[0][1]


# ------------------------------------------------- roster resolution (drop, never guess)
def build_roster_index(members):
    """The collector's resolution tables, restated: FULL (fetch_interviews.py:150-154) and SUR
    (:155-157).  Pure -- the caller supplies the roster names."""
    full, sur = {}, {}
    for m in members or ():
        p = normalize(m).split()
        if len(p) >= 2:
            for v in variants(p[0]):
                full.setdefault((v, p[-1]), m)
        if p:
            sur.setdefault(p[-1], []).append(m)
    return {'full': full, 'sur': {k: tuple(v) for k, v in sur.items()}}


TITLE = re.compile(r'^(SEN\.|REP\.|SENATOR|REPRESENTATIVE|SPEAKER OF THE HOUSE|SPEAKER|'
                   r'HOUSE MINORITY LEADER|HOUSE MAJORITY LEADER|SENATE (MAJORITY|MINORITY) LEADER|'
                   r'DEL\.|CONGRESSMAN|CONGRESSWOMAN|DR\.)\s+', re.I)
_RANKED = re.compile(r'^(SEN|REP|SENATOR|REPRESENTATIVE|CONGRESS)', re.I)


def resolve_label(label, index):
    """'SEN. ELIZABETH WARREN' / 'THOM TILLIS' -> roster member or None, NEVER a guess.

    fetch_interviews.py:177-200 (resolve_full) restated.  The unique-surname fallback fires only
    with an explicit honorific, and only when the label's first name is nickname/prefix
    compatible with the roster member's -- a mismatched first name means a different person
    (predecessor, relative) and must never bind.  A bare surname with no honorific never binds
    here at all: in the collector that case is answered by the PAGE's own intro alias map, which
    a stored row does not carry, so for stored text it stays unresolved by design.
    """
    if not label:
        return None
    s = TITLE.sub('', label.strip())
    s = re.sub(r'\(.*?\)', '', s)
    s = re.sub(r',\s*[DRI]\.?\s*-\s*[A-Za-z][A-Za-z.\- ]{0,20}\s*$', '', s)
    p = normalize(s).split()
    if len(p) >= 2:
        m = index['full'].get((p[0], p[-1]))
        if m:
            return m
        cands = index['sur'].get(p[-1], ())
        if (len(cands) == 1 and _RANKED.match(label.strip())
                and firstname_compatible(p[0], normalize(cands[0]).split()[0])):
            return cands[0]
    elif len(p) == 1:
        cands = index['sur'].get(p[0], ())
        if len(cands) == 1 and _RANKED.match(label.strip()):
            return cands[0]
    return None


def resolve_tag(tag, index):
    """resolve_label on a turn tag: the full-name part is everything before the comma tail
    (fetch_interviews.py:214, :223)."""
    base = (tag or '').split(',')[0].strip()
    return resolve_label(base, index)
