# -*- coding: utf-8 -*-
"""INTERVIEW TURN TAGS + the turn-bleed migration, V2 TRUNCATE-ONLY (Paul: "GO on the
truncate-only v2", 2026-09-19).

WHAT IS UNDER TEST.
  fec/members/turn_tags.py                     the pure turn-boundary grammar, both dial positions
  fec/members/fetch_interviews.py              the collector's wiring of that grammar (read as TEXT)
  fec/members/migrate_interview_turn_bleed.py  the one-shot repair of the stored corpus

WHY. The collector's turn regex (fetch_interviews.py:202) accepts only an ALL-CAPS tag anchored at
line start, so a backtick (O`DONNELL:) and any lower-case parenthetical (WALLACE (voice-over):) are
not boundaries and the next speaker's words stay inside the previous member's row. Measured in
audits/laundry_list_closeout_20260918/measR1d: 5,835 of 153,576 stored INTERVIEW rows carry another
speaker's words; graded 40/40 (flagged) and 72/72 (residual classes R1-R4).

WHAT CHANGED FROM V1, AND WHY.  An independent adversarial grade
(audits/laundry_list_closeout_20260918/gradeN12/receipts/FINDINGS.txt) rejected v1: of the 64 rows
v1 would have INSERTED, 18 were not the speaker it named (3 pure chyron, 5 a different person, 10
carrying another speaker's words), and v1's grammar left four measured gaps.  Every assertion below
that pinned v1's INSERT behaviour is REPLACED by the v2 rule and is marked [V2 REPLACES V1-INSERT];
nothing else was weakened.  The v2 rules:
  1. TRUNCATE ONLY.  A flagged row keeps the member's text BEFORE the first FOREIGN tag; the rest
     goes to the ledger.  The tool contains no INSERT INTO speeches and no DELETE FROM speeches.
  2. FAIL SAFE ON WHOSE TAG IT IS.  Surname match (leniently normalised) => the row's OWN member,
     never cut.  FOREIGN only when the tag clearly names someone else or is a broadcast role tag.
     Anything else is AMBIGUOUS and the row is left alone.
  3. The four grammar gaps the grade measured are closed, each with its counter-example below.
  4. Five skip rules: short kept text, label-bound rows, CU-extract rows, all-caps / non-INTERVIEW
     rows, ambiguous rows.

EVERY SHAPE BELOW IS A MEASURED ONE. The splitting fixtures are the graded tags out of
measR1d/receipts and the gradeN12 counter-examples, re-read from the LIVE bytes in
audits/laundry_list_closeout_20260918/fixN12v2/v2_01_probe_grade_rows.out.txt; the false markers are
the ones the measurement proved must NOT split.

Synthetic fixtures only: a synthetic roster, an in-memory / temp-file sqlite database, no corpus,
no artifact, no MONEYBALL_ROOT, no network. The migration module is imported, which is safe by
construction -- it does its work under `if __name__ == '__main__'` and nothing at import time.
"""
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')   # run_tests.py decodes this pipe as utf-8

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
FEC = os.path.join(ROOT, 'fec')
sys.path.insert(0, FEC)
from members import turn_tags as TT                    # noqa: E402
from members import migrate_interview_turn_bleed as MIG  # noqa: E402

P = F = 0


def ok(name, cond, detail=''):
    global P, F
    if cond:
        P += 1
        print('  OK   ' + name)
    else:
        F += 1
        print('  FAIL ' + name + ('  :: ' + str(detail) if detail else ''))


COLLECTOR = os.path.join(FEC, 'members', 'fetch_interviews.py')
MIGRATOR = os.path.join(FEC, 'members', 'migrate_interview_turn_bleed.py')
GRAMMAR = os.path.join(FEC, 'members', 'turn_tags.py')
COLLECTOR_SRC = io.open(COLLECTOR, encoding='utf-8').read()
MIGRATOR_SRC = io.open(MIGRATOR, encoding='utf-8').read()
GRAMMAR_SRC = io.open(GRAMMAR, encoding='utf-8').read()

# A synthetic roster: 10 members, 10 distinct surnames, none of them a real member.
ROSTER = ['Ada Alpher', 'Ben Bravo', 'Cy Charlie', 'Dee Delta', 'Eli Echo',
          'Fay Foxtrot', 'Gus Golf', 'Hal Hotel', 'Ida India', 'Jay Juliet']
INDEX = TT.build_roster_index(ROSTER)
SUR = TT.roster_surnames(ROSTER)
# The corpus-derived speaker lexicon, stated here instead of derived from the live corpus.
LEX = frozenset(SUR | {'odonnell', 'wallace', 'raju', 'lemon', 'blitzer', 'mckend', 'fox',
                       'obrien', 'wild', 'todd', 'nelson', 'prokupecz'})

print('[the dial: two positions, nothing else]')
ok('default is the repaired rule', TT.DEFAULT == TT.REPAIRED == '2' and TT.LEGACY == '1')
ok('the dial name is IV_TURN_TAGS', TT.DIAL == 'IV_TURN_TAGS')
ok('only "1" selects legacy; a typo falls back to the default, never to a third behaviour',
   TT.dial_position('1') == '1' and TT.dial_position('2') == '2'
   and TT.dial_position('') == '2' and TT.dial_position(None) == '2'
   and TT.dial_position('legacy') == '2' and TT.dial_position(' 1 ') == '1')

print('[legacy collector patterns are byte-identical to fetch_interviews.py at 35521f9aa]')
# Pinned literals: fetch_interviews.py:202 (LABEL), :97 (INLINE_LABEL), :421 (MIXED_LABEL).
LEGACY_LABEL_PIN = r'^\s*([A-Z][A-Z .,\'()\-]{1,70}?):\s'
LEGACY_INLINE_PIN = r'\s([A-Z][A-Z.\'\-]+(?: [A-Z.,\'()\-]+[A-Z.\'\-]*){0,4}):\s'
LEGACY_MIXED_PIN = (r'^\s*((?:Sen\.|Rep\.|Del\.|Gov\.|Speaker|Leader|Dr\.)?\s*[A-Z][A-Za-z.\'\-]+'
                    r'(?: [A-Z][A-Za-z.\'\-]+){0,3}'
                    r'(?:\s*\([A-Za-z.\- ]+\)|,\s*[DRI]\.?\s*-\s*[A-Za-z][A-Za-z.\- ]{0,20})?):\s')
ok('LABEL legacy pattern is byte-identical', TT.label_pattern('1') == LEGACY_LABEL_PIN,
   TT.label_pattern('1'))
ok('INLINE_LABEL legacy pattern is byte-identical', TT.inline_label_pattern('1') == LEGACY_INLINE_PIN,
   TT.inline_label_pattern('1'))
ok('MIXED_LABEL legacy pattern is byte-identical', TT.mixed_label_pattern('1') == LEGACY_MIXED_PIN,
   TT.mixed_label_pattern('1'))
ok('the repaired patterns are not the legacy ones',
   TT.label_pattern('2') != LEGACY_LABEL_PIN
   and TT.inline_label_pattern('2') != LEGACY_INLINE_PIN
   and TT.mixed_label_pattern('2') != LEGACY_MIXED_PIN)

print('[the collector compiles the grammar from the module, and reads the dial once]')
ok('fetch_interviews.py imports the grammar module',
   'from members import turn_tags' in COLLECTOR_SRC)
ok('the dial is read once, at top level, with the repaired default',
   "IV_TURN_TAGS = os.environ.get('IV_TURN_TAGS', turn_tags.DEFAULT)" in COLLECTOR_SRC
   and COLLECTOR_SRC.count("os.environ.get('IV_TURN_TAGS'") == 1)
ok('LABEL, INLINE_LABEL and MIXED_LABEL all come from the module',
   'LABEL = re.compile(turn_tags.label_pattern(IV_TURN_TAGS), re.M)' in COLLECTOR_SRC
   and 'INLINE_LABEL = re.compile(turn_tags.inline_label_pattern(IV_TURN_TAGS))' in COLLECTOR_SRC
   and 'MIXED_LABEL = re.compile(turn_tags.mixed_label_pattern(IV_TURN_TAGS), re.M)' in COLLECTOR_SRC)
ok('no raw ALL-CAPS label literal is left behind in the collector',
   LEGACY_LABEL_PIN not in COLLECTOR_SRC and LEGACY_INLINE_PIN not in COLLECTOR_SRC)
ok('the collector still drops micro-interjections at the same floor, at all four sites',
   COLLECTOR_SRC.count('if len(sp.split()) >= 8:') == 4 and TT.MIN_TURN_WORDS == 8)
ok('scrub() keeps its acronym guard', "if words - _ACRO:" in COLLECTOR_SRC)
ok('the two other collectors are untouched by this change',
   'ingest_wj' not in GRAMMAR_SRC and 'load_cspan' not in GRAMMAR_SRC)

print('[collector: a page that carries only legacy shapes parses identically under both dials]')
PAGE = ('SEN. ADA ALPHER, A SENATOR: The first member turn is deliberately long enough to clear '
        'the eight-word floor.\n'
        'ANCHOR BLITZER: The host turn is also deliberately long enough to clear the floor.\n'
        'ALPHER: A surname continuation turn that is long enough to clear the eight-word floor.\n'
        'The CIA: their view is not a speaker label at all in running prose.\n')


def turn_starts(pattern, text, flags=re.M):
    return [(m.start(), m.group(1)) for m in re.compile(pattern, flags).finditer(text)]


ok('LABEL: identical boundaries on a legacy-only page',
   turn_starts(TT.label_pattern('1'), PAGE) == turn_starts(TT.label_pattern('2'), PAGE),
   (turn_starts(TT.label_pattern('1'), PAGE), turn_starts(TT.label_pattern('2'), PAGE)))
ok('INLINE_LABEL: identical truncation markers on a legacy-only page',
   turn_starts(TT.inline_label_pattern('1'), PAGE, 0)
   == turn_starts(TT.inline_label_pattern('2'), PAGE, 0))

print('[collector: the repaired patterns see the three shapes the legacy ones cannot]')
BLEED_PAGE = ('SEN. ADA ALPHER: My own words are deliberately long enough to clear the floor here.\n'
              'O`DONNELL: The backtick host tag MS NOW writes, long enough to clear the floor.\n'
              'WALLACE (voice over): A lower-case parenthetical tag, long enough to clear it too.\n'
              '[08:05:03] RAJU (on camera): A timestamped tag, also long enough to clear it.\n')
for shape, needle in (('backtick', 'O`DONNELL'), ('lower-case parenthetical', 'WALLACE (voice over)'),
                      ('bracketed timestamp', 'RAJU (on camera)')):
    legacy_labels = [g for _s, g in turn_starts(TT.label_pattern('1'), BLEED_PAGE)]
    rep_labels = [g for _s, g in turn_starts(TT.label_pattern('2'), BLEED_PAGE)]
    ok('LABEL repaired sees the %s form, legacy does not' % shape,
       any(needle in g for g in rep_labels) and not any(needle in g for g in legacy_labels),
       (legacy_labels, rep_labels))
ok('INLINE_LABEL repaired truncates at the backtick tag, legacy does not',
   re.compile(TT.inline_label_pattern('2')).search(' O`DONNELL: and then more words follow here')
   and not re.compile(TT.inline_label_pattern('1')).search(
       ' O`DONNELL: and then more words follow here'))
ok('INLINE_LABEL repaired truncates at a parenthetical tag, legacy does not',
   re.compile(TT.inline_label_pattern('2')).search(' FOX (voice-over): and then more words follow')
   and not re.compile(TT.inline_label_pattern('1')).search(
       ' FOX (voice-over): and then more words follow'))
ok('MIXED_LABEL repaired sees the PBS title tail, legacy does not',
   re.compile(TT.mixed_label_pattern('2'), re.M).match('Scott Bessent, U.S. Treasury Secretary: No, sir.')
   and not re.compile(TT.mixed_label_pattern('1'), re.M).match(
       'Scott Bessent, U.S. Treasury Secretary: No, sir.'))
ok('MIXED_LABEL repaired still matches the party tail it always matched',
   re.compile(TT.mixed_label_pattern('2'), re.M).match('Sen. Mark Warner, D-Va.: I do.')
   and re.compile(TT.mixed_label_pattern('1'), re.M).match('Sen. Mark Warner, D-Va.: I do.'))
ok('a label parenthetical can never hide a colon (parse_turns cuts on sp.index(":"))',
   not re.compile(TT.label_pattern('2'), re.M).match('WALLACE (via phone: London): words here'))
_TS_PAGE = '[08:05:03] SEN. ADA ALPHER: Words after a timestamped label.\nBEN BRAVO: Next turn.'
_m = list(re.compile(TT.label_pattern('2'), re.M).finditer(_TS_PAGE))
ok('a [hh:mm:ss] turn is sliced from the label end, not the stamp colon (speech text)',
   _m and _TS_PAGE[_m[0].end():_m[1].start()].strip() == 'Words after a timestamped label.'
   and "sp.index(':')" not in COLLECTOR_SRC and 'text[me:end]' in COLLECTOR_SRC, _m)

print('[stored text: every graded shape splits]')
GRADED = [
    # (name, member, show, text, expected foreign tag, expected kept text)
    ('backtick host tag (U2 tagfreq #1, 1,434 occurrences)', 'Ben Bravo', 'msnow',
     'This is the moment where we restored faith in our government. O`DONNELL: I find it easy to '
     'describe the process that we are seeing here tonight.',
     'O`DONNELL', 'This is the moment where we restored faith in our government.'),
    ('lower-case parenthetical (U2 tagfreq #2)', 'Cy Charlie', 'cnr',
     'Inaction is unacceptable. RAJU (voice-over): Two months ago, the Senate passed a $95 billion '
     'foreign aid package.',
     'RAJU (voice-over)', 'Inaction is unacceptable.'),
    ('on-camera parenthetical (U2 tagfreq #3)', 'Dee Delta', 'acd',
     'They want to be in the minority. LEMON (on camera): Now, Democrats are not united on the idea.',
     'LEMON (on camera)', 'They want to be in the minority.'),
    ('R1 STOP-name tag with a broadcast parenthetical', 'Eli Echo', 'ctw',
     'It is quite sad. FOX (voice-over): Twenty holdouts rally their support for the speaker.',
     'FOX (voice-over)', 'It is quite sad.'),
    ('R2 correspondent surname the lexicon never saw', 'Fay Foxtrot', 'cnr',
     'We need to act now. TAUSCHE (voice-over): The White House has been quiet about the deal.',
     'TAUSCHE (voice-over)', 'We need to act now.'),
    ('R3 tag after a bracketed timestamp (anchor fails)', 'Gus Golf', 'cnr',
     'I yield back my time. [08:05:03] RAJU (voice-over): The speaker has clashed with defense hawks.',
     'RAJU (voice-over)', 'I yield back my time. [08:05:03]'),
    ('R3 tag after a comma (anchor fails)', 'Hal Hotel', 'cnr',
     'That is what he said, LEMON (on camera): and now the panel weighs in on the whole affair.',
     'LEMON (on camera)', 'That is what he said,'),
    ('full correspondent tag with a role tail', 'Ida India', 'cnr',
     'He announced it today. LAUREN FOX, CNN CONGRESSIONAL CORRESPONDENT (voice-over): Republican '
     'members are weighing the motion.',
     'LAUREN FOX, CNN CONGRESSIONAL CORRESPONDENT (voice-over)', 'He announced it today.'),
    ('the contaminated in-use gold row, verbatim (measR1d U3U4 #15, speeches row 245771)',
     'Jay Juliet', 'tom-steyer-on-performance-in-nevada-expectations-for-south-carolina',
     'Oh, he does, clearly. We all take orders from Bertie. WALLACE (voice over): Committee members '
     'recently celebrated Bowman after the Senate Federal Credit Union announced it will name its '
     'new building after him.',
     'WALLACE (voice over)', 'Oh, he does, clearly. We all take orders from Bertie.'),
    ('R4 PBS "<Name>, U.S. <Title>:" label', 'Ada Alpher', 'pbs-newshour',
     'Did you actually tell him you were going to punch him in the face? Scott Bessent, U.S. '
     'Treasury Secretary: No, sir. I actually said something else entirely.',
     'Scott Bessent, U.S. Treasury Secretary',
     'Did you actually tell him you were going to punch him in the face?'),
    ('R4 mixed-case honorific prefix', 'Ben Bravo', 'pbs-newshour',
     'Well, do you believe basically all Americans are racist? Fmr. Attorney General Vanita Gupta: '
     'I think we all have implicit bias and racial bias, yes, I do.',
     'Fmr. Attorney General Vanita Gupta',
     'Well, do you believe basically all Americans are racist?'),
    ('R5 colon-less honorific tag', 'Cy Charlie', 'cnc',
     'So help you, God. SEN. PATTY MURRAY (D-WA) I do. Yes. At this time, I will administer the '
     'oath to all Senators in the chamber.',
     'SEN. PATTY MURRAY (D-WA)', 'So help you, God.'),
]
for name, member, show, text, want_tag, want_kept in GRADED:
    spans = TT.split_spans(text, member, LEX, show)
    got_tags = [s[0] for s in spans[1:]]
    ok('splits: ' + name,
       len(spans) >= 2 and want_tag in got_tags and spans[0][1] == want_kept,
       (spans[0][1], got_tags))

print('[stored text: every false marker the measurement proved must NOT split]')
FALSE = [
    ('an acronym-colon construction in real speech', 'Ada Alpher', None,
     'I asked the CIA: their view is that the threat is real and growing every single year.'),
    ('an ALL-CAPS document heading', 'Ada Alpher', None,
     'The table lists them. DEPARTMENT OF DEFENSE MILITARY RETIREMENT FUND: 1,234 and then more.'),
    ('a bare agency tag with no parenthetical', 'Ada Alpher', None,
     'We put the question to ICE: the answer came back three weeks later than promised.'),
    ('a mid-sentence attribution verb', 'Ada Alpher', None,
     'He told CNN: the deal is dead, and that is the end of the matter for this year.'),
    ('a network name with no parenthetical', 'Ada Alpher', None,
     'You can see it on FOX: the whole segment runs about eleven minutes end to end.'),
    ('a truncated honorific residue with no name', 'Ada Alpher', None,
     'That is what my conscience demands, and I hope my colleagues will do the same. SEN.'),
    ('an all-caps Washington Journal row (unmeasured, must be left alone)', 'Ada Alpher',
     'washington-journal',
     'WE WILL GET THE INTELLIGENCE BRIEFING TODAY AT 1:00. I HAVE STUDIED THE GENERAL FOR A DECADE '
     'AND A HALF. LESTER HOLT, ANCHOR, NBC NIGHTLY NEWS: I HAVE ALWAYS KNOWN HE WAS A TOP THREAT.'),
    ('a mixed-case sentence start in a NON-mixed-case leg', 'Ada Alpher', 'cnr',
     'He was clear about it. Scott Bessent, U.S. Treasury Secretary: is how the chyron read.'),
    ('a mixed-case editorial heading in a mixed-case leg', 'Ada Alpher', 'pbs-newshour',
     'That is the whole story of the week. Editor Note: the transcript continues below the fold.'),
]
for name, member, show, text in FALSE:
    spans = TT.split_spans(text, member, LEX, show)
    ok('does not split: ' + name, len(spans) == 1, [s[0] for s in spans[1:]])
ok('FMR. HUD on its own is not a speaker (no name follows the honorific)',
   not TT.qualifies('FMR. HUD', LEX))

print('[stored text: the guards N12 added to the colon-less class, each measured]')
POSS = ('And now, for some introductory remarks. (DEEPFAKE RECORDING OF SEN. RICHARD '
        "BLUMENTHAL'S VOICE PLAYS)")
ok('a deepfake stage direction is not a turn, possessive and all (rows 225771 / 242500)',
   len(TT.split_spans(POSS, 'Richard Blumenthal', LEX, 'se')) == 1,
   [s[0] for s in TT.split_spans(POSS, 'Richard Blumenthal', LEX, 'se')[1:]])
CUELLAR = ("And Chair of the Appropriations Committee, Nita Lowey. HENRY ROBERTO CUELLAR, (D) "
           "U.S REPRESENTATIVE FOR TEXAS'S 28TH DISTRICT I want to thank the chair for her work "
           "on this bill.")
sp = TT.split_spans(CUELLAR, 'Nancy Pelosi', LEX, 'CPT')
ok('a possessive INSIDE a real colon-less tag still splits (row 191195 is a real bleed)',
   len(sp) == 2 and sp[1][0].startswith('HENRY ROBERTO CUELLAR')
   and sp[0][1].endswith('Nita Lowey.'), (sp[0][1][-30:], [s[0] for s in sp[1:]]))
TS = ('On to victory in November. [21:25:00] (CHEERING) KAMALA HARRIS (D), VICE PRESIDENT OF THE '
      'UNITED STATES I am grateful to every one of you for being here tonight.')
sp = TT.split_spans(TS, 'Charles Schumer', LEX, 'se')
ok('the run crosses a bracketed timestamp and an ALL-CAPS parenthetical (row 70117)',
   len(sp) == 2 and 'KAMALA HARRIS' in sp[1][0] and sp[0][1].endswith('in November.'),
   (sp[0][1][-30:], [s[0] for s in sp[1:]]))
STAGE_DIR = ('Let me finish answering the question that was put to me. (SEN. DICK DURBIN BANGS '
             'THE GAVEL) And I will go on from there.')
ok('a colon-less tag inside an unclosed parenthesis is a stage direction (row 225128)',
   len(TT.split_spans(STAGE_DIR, 'Ted Cruz', LEX, 'se')) == 1,
   [s[0] for s in TT.split_spans(STAGE_DIR, 'Ted Cruz', LEX, 'se')[1:]])
MIDTAG = ('I would be very cautious about opening that door to those who served honorably. '
          'KAMALA HARRIS, VICE PRESIDENT OF THE UNITED STATES I am glad to be here today and '
          'this answer is long enough to clear the floor.')
sp = TT.split_spans(MIDTAG, 'Jay Juliet', LEX, 'cnap')
ok('the colon-less start is extended back over the whole ALL-CAPS run (row 69954 shape)',
   len(sp) == 2 and sp[1][0] == 'KAMALA HARRIS, VICE PRESIDENT OF THE UNITED STATES'
   and sp[0][1].endswith('served honorably.'), (sp[0][1][-40:], [s[0] for s in sp[1:]]))
ACRO_END = ('I raised exactly that question with the FBI. SEN. PATTY MURRAY (D-WA) I do agree '
            'with my colleague on this point.')
sp = TT.split_spans(ACRO_END, 'Jay Juliet', LEX, 'cnc')
ok('the extension never crosses a sentence terminator, so a closing acronym stays with the member',
   len(sp) == 2 and sp[0][1].endswith('with the FBI.')
   and sp[1][0] == 'SEN. PATTY MURRAY (D-WA)', (sp[0][1][-30:], [s[0] for s in sp[1:]]))
MENTION = 'I spoke with SEN. PATTY MURRAY (D-WA) about the bill and she agreed with me on it.'
ok('a mid-sentence colon-less mention is not a turn boundary',
   len(TT.split_spans(MENTION, 'Jay Juliet', LEX, 'cnc')) == 1,
   [s[0] for s in TT.split_spans(MENTION, 'Jay Juliet', LEX, 'cnc')[1:]])
ABBR = ('Elections have consequences, Mr. President. Rep. Nancy Pelosi, De-Calif.: Let me just '
        'say one thing about that here.')
sp = TT.split_spans(ABBR, 'Cy Charlie', LEX, 'pbs-newshour')
ok('an abbreviation does not open a false sentence for the mixed-case rule (row 260197)',
   len(sp) == 2 and sp[1][0] == 'Rep. Nancy Pelosi, De-Calif.'
   and sp[0][1].endswith('Mr. President.'), (sp[0][1][-30:], [s[0] for s in sp[1:]]))
ok('a heading whose every token is a STOP word never qualifies',
   not TT.qualifies('DEFENSE MILITARY RETIREMENT FUND', LEX)
   and not TT.qualifies('DEPARTMENT OF DEFENSE', LEX))

# ======================================================================================
# V2: every counter-example the independent grade (gradeN12/receipts/FINDINGS.txt) named.
# The text of each fixture is the LIVE row's bytes, re-read read-only in
# audits/laundry_list_closeout_20260918/fixN12v2/v2_01_probe_grade_rows.out.txt.
# ======================================================================================
print('[V2 grade counter-example: the no-space colon MTP writes (G1 MECHANISM 1, 38 corpus rows)]')
NOSPACE = ('Doing this without consulting Congress without a vote, I think, is a clear violation '
           'of law. CHUCK TODD:It’s fair to say that no one has been more forceful in '
           'responding to Syria’s chemical weapons than you have been.')
sp = TT.split_spans(NOSPACE, 'Jay Juliet', LEX, 'meet-the-press')
ok('CHUCK TODD:It  -- a colon with no space after it is still a turn boundary (row 104285)',
   len(sp) == 2 and sp[1][0] == 'CHUCK TODD' and sp[0][1].endswith('violation of law.'),
   (sp[0][1][-40:], [s[0] for s in sp[1:]]))
ok('the no-space form still needs a qualifying name: a time of day is not a tag',
   len(TT.split_spans('We start at 1:00. The briefing runs long every single time we do it.',
                      'Jay Juliet', LEX, 'meet-the-press')) == 1)
ok('the no-space form does not fire on an acronym or a heading word',
   len(TT.split_spans('I asked the CIA:their view is that the threat is real and growing.',
                      'Jay Juliet', LEX, 'meet-the-press')) == 1
   and len(TT.split_spans('That is the story. NOTE:The transcript continues below the fold here.',
                          'Jay Juliet', LEX, 'meet-the-press')) == 1)

print('[V2 grade counter-example: UNIDENTIFIED / quoted / dated role tails (G1 MECHANISM 2)]')
UNID = ('He is an independent person, somebody who has got lots of integrity. UNIDENTIFIED MALE, '
        'MSNBC, AUG. 3, 2017: He enjoys very broad support in the Senate both among Republicans.')
sp = TT.split_spans(UNID, 'Jay Juliet', LEX, 'ben-shapiro')
ok('UNIDENTIFIED MALE, MSNBC, AUG. 3, 2017:  splits (row 102837, 75 corpus rows)',
   len(sp) == 2 and sp[1][0].startswith('UNIDENTIFIED MALE')
   and sp[0][1].endswith('lots of integrity.'), (sp[0][1][-30:], [s[0] for s in sp[1:]]))
ok('UNIDENTIFIED MALE #1: splits too (the hash-number form, rows 70228 / 70229)',
   [s[0] for s in TT.split_spans(
       'Jordan was so much fun as a child. UNIDENTIFIED MALE #1: When all of a sudden the '
       'shooting started and everybody ran out of the parking lot.',
       'Jay Juliet', LEX, 'se')[1:]] == ['UNIDENTIFIED MALE #1'])
BAIER = ('I think that is exactly the wrong way to go about this whole thing. BRET BAIER, HOST OF '
         '"SPECIAL REPORT": The omnibus spending bill is going to come to the floor this week.')
sp = TT.split_spans(BAIER, 'Jay Juliet', LEX, 'sr')
ok('BRET BAIER, HOST OF "SPECIAL REPORT":  splits (row 242833, 215 of 262 words were his)',
   len(sp) == 2 and sp[1][0].startswith('BRET BAIER') and sp[0][1].endswith('whole thing.'),
   (sp[0][1][-30:], [s[0] for s in sp[1:]]))
MANCHIN = ('I have not seen any evidence to suggest that. SEN. JOE MANCHIN, D-W.VA, "MEET THE '
           'PRESS"/NBC, DEC. 17, 2017: I do not at all believe that Mr. Mueller has been '
           'compromised for his investigation.')
sp = TT.split_spans(MANCHIN, 'Jay Juliet', LEX, 'ben-shapiro')
ok('a quoted, slashed and dated role tail splits (row 102837 head)',
   len(sp) == 2 and sp[1][0].startswith('SEN. JOE MANCHIN')
   and sp[0][1].endswith('to suggest that.'), (sp[0][1][-30:], [s[0] for s in sp[1:]]))

print('[V2 grade counter-example: non-ASCII and curly-apostrophe names (G2a / G2e)]')
BARRON = ('We can see something move forward when we see the ratification. Laura Barrón-López: '
          'All the while, the war in Ukraine goes on. Overnight, Russian forces attacked Kyiv.')
sp = TT.split_spans(BARRON, 'Jay Juliet', LEX, 'pbs-newshour')
ok('Laura Barrón-López: splits (row 258713; MIXED name tokens were ASCII-only)',
   len(sp) == 2 and sp[1][0] == 'Laura Barrón-López'
   and sp[0][1].endswith('see the ratification.'), (sp[0][1][-30:], [s[0] for s in sp[1:]]))
NFD = BARRON.replace('ó', 'ó')     # the way rows 258713 and 7 others actually store it
sp = TT.split_spans(NFD, 'Jay Juliet', LEX, 'pbs-newshour')
ok('the DECOMPOSED spelling Barrón-López: splits too (row 258713, 8 corpus rows are NFD)',
   len(sp) == 2 and sp[1][0].startswith('Laura Barro')
   and sp[0][1].endswith('see the ratification.'), (sp[0][1][-30:], [s[0] for s in sp[1:]]))
ok('a decomposed and a precomposed name normalise to the same member',
   TT.normalize('BARRÓN-LÓPEZ') == TT.normalize('BARRÓN-LÓPEZ') == 'barronlopez')
LAZARO = ('We are voting yes because there is a vision we can collectively create as a city. '
          'Fred de Sam Lazaro: The plan would amend the Minneapolis charter, removing the police '
          'department and the requirement to fund a minimum number of officers.')
sp = TT.split_spans(LAZARO, 'Jay Juliet', LEX, 'pbs-newshour')
ok('Fred de Sam Lazaro: splits -- a lower-case name particle (row 257343)',
   len(sp) == 2 and sp[1][0] == 'Fred de Sam Lazaro'
   and sp[0][1].endswith('as a city.'), (sp[0][1][-30:], [s[0] for s in sp[1:]]))
CURLY = ('Donald Trump and Republicans on Capitol Hill are nowhere to be found. '
         'O’BRIEN (voice over): The pain of the gridlock keeps growing every single day.')
sp = TT.split_spans(CURLY, 'Jay Juliet', LEX, 'this-week')
ok('O’BRIEN (voice over): splits -- U+2019 joins the name class (row 80974, 183 corpus rows)',
   len(sp) == 2 and sp[1][0] == 'O’BRIEN (voice over)'
   and sp[0][1].endswith('nowhere to be found.'), (sp[0][1][-34:], [s[0] for s in sp[1:]]))
ok('the curly apostrophe folds to the straight one when matching a member',
   TT.normalize('O’ALPHER') == TT.normalize("O'ALPHER") == TT.normalize('O`ALPHER')
   and TT.own_lenient('O’ALPHER', "Ada O'Alpher"))

print('[V2 grade counter-example: an abbreviation period must not satisfy the sentence anchor (G2b)]')
TRUMPCHY = ('It is to push Donald Trump a little bit closer to the White House. And that is a '
            'dangerous proposition. DONALD TRUMP (R), FORMER U.S. PRESIDENT AND 2024')
sp = TT.split_spans(TRUMPCHY, 'Jay Juliet', LEX, 'this-week')
ok('the tag is the whole chyron, not "PRESIDENT AND" (rows 82055 / 71147 / 70812)',
   len(sp) == 2 and sp[1][0].startswith('DONALD TRUMP')
   and sp[0][1].endswith('dangerous proposition.'), (sp[0][1][-34:], [s[0] for s in sp[1:]]))
MORRISON = ('What did Ambassador Sondland say to tell you that he told Mr. Yermak? TIM MORRISON, '
            'FORMER U.S. ADVISER TO PRESIDENT TRUMP ON RUSSIA AND EUROPE ON THE')
sp = TT.split_spans(MORRISON, 'Jay Juliet', LEX, 'acd')
ok('the run crosses U.S. so the member keeps his whole question (row 205497)',
   len(sp) == 2 and sp[1][0].startswith('TIM MORRISON')
   and sp[0][1].endswith('he told Mr. Yermak?'), (sp[0][1][-34:], [s[0] for s in sp[1:]]))
ok('[V2] "No." is an ANSWER, not an abbreviation: the tag after it still splits '
   '(rows 260912 / 263507 / 264343 / 268284 / 274221 / 275455)',
   [s[0] for s in TT.split_spans(
       'No. O`DONNELL: What the -- I just want to go to the voting deadlines in Colorado.',
       'Jay Juliet', LEX, 'msnow')[1:]] == ['O`DONNELL'],
   TT.split_spans('No. O`DONNELL: What the -- I just want to go to the voting deadlines.',
                  'Jay Juliet', LEX, 'msnow'))
CAMPBELL = ('Do you believe nicotine is not addictive? MR. WILLIAM CAMPBELL, FORMER PRESIDENT '
            'AND CHIEF EXECUTIVE OF PHILIP MORRIS I believe nicotine is not addictive, yes.')
sp = TT.split_spans(CAMPBELL, 'Jay Juliet', LEX, 'msnow')
ok('[V2] the run crosses a TITLE abbreviation, so "MR." goes with the tag (row 274133)',
   len(sp) == 2 and sp[1][0].startswith('MR. WILLIAM CAMPBELL')
   and sp[0][1].endswith('not addictive?'), (sp[0][1][-30:], [s[0] for s in sp[1:]]))
JAFFER = ('That is absolutely not what the Constitution says, and I strongly disagree with him. '
          'Jamil N. Jaffer, George Mason University: The president has broad authority here.')
sp = TT.split_spans(JAFFER, 'Jay Juliet', LEX, 'pbs-newshour')
ok('[V2] a mixed-case middle initial is still part of the name (row 175681)',
   len(sp) == 2 and sp[1][0].startswith('Jamil N. Jaffer')
   and sp[0][1].endswith('disagree with him.'), (sp[0][1][-30:], [s[0] for s in sp[1:]]))
ok('[V2] a same-surname mixed tag is the member own, even for a relative (row 274661)',
   TT.tag_ownership('JENNIFER GOSAR, REP. PAUL GOSAR', 'Paul Gosar') == TT.OWN)
ok('[V2] an ellipsis is a trailing-off, not an abbreviation (row 174172)',
   not TT.is_abbreviation('Mamdani...') and not TT.is_abbreviation('so...')
   and TT.is_abbreviation('U.S.') and TT.is_abbreviation('Mr.')
   and not TT.is_abbreviation('FBI.') and not TT.is_abbreviation('No.'))
ELL = ('I do solemnly swear that I will faithfully discharge the duties of the office, so help '
       'me God... Zohran Mamdani, New York City Mayor: And that I take this obligation freely.')
sp = TT.split_spans(ELL, 'Jay Juliet', LEX, 'pbs-newshour')
ok('[V2] a mixed-case tag after an ellipsis still splits (row 174172)',
   len(sp) == 2 and sp[1][0].startswith('Zohran Mamdani'), [s[0] for s in sp[1:]])
ok('a dotted abbreviation on its own never opens a turn',
   len(TT.split_spans('He now works for the U.S. PRESIDENT AND the team he built over there.',
                      'Jay Juliet', LEX, 'acd')) == 1,
   [s[0] for s in TT.split_spans(
       'He now works for the U.S. PRESIDENT AND the team he built over there.',
       'Jay Juliet', LEX, 'acd')[1:]])

print('[V2 grade counter-example: class C must not leave the next speaker NAME behind (G2d)]')
NELSON = ('Kamala had been a hard and tough prosecutor in Alameda County, prosecuting men who were '
          'pimping young girls and rapists. BRIAN NELSON, GENERAL COUNSEL, CALIFORNIA DEPARTMENT '
          'OF JUSTICE (2011- 2015): Kamala wanted to make sure that murderers face accountability.')
sp = TT.split_spans(NELSON, 'Jay Juliet', LEX, 'se')
ok('BRIAN NELSON, GENERAL COUNSEL, ... (2011- 2015):  the NAME goes with the tag (row 70149)',
   len(sp) == 2 and sp[1][0].startswith('BRIAN NELSON')
   and sp[0][1].endswith('girls and rapists.'), (sp[0][1][-34:], [s[0] for s in sp[1:]]))
MALVEAUX = ('I am proof that as Americans, we can embrace our differences. SUZANNE MALVEAUX, '
            'NATIONAL CORRESPONDENT, CNN(off-camera): Freshman Congresswoman Ilhan Omar was sworn '
            'into office just two months ago.')
sp = TT.split_spans(MALVEAUX, 'Jay Juliet', LEX, 'ebo')
ok('SUZANNE MALVEAUX, NATIONAL CORRESPONDENT, CNN(off-camera): takes its name too (row 192133)',
   len(sp) == 2 and sp[1][0].startswith('SUZANNE MALVEAUX')
   and sp[0][1].endswith('embrace our differences.'), (sp[0][1][-34:], [s[0] for s in sp[1:]]))

print('[V2 rule 2: whose tag is it -- lenient for the member, strict for everyone else]')
ok('a self tag by surname is own', TT.names_member('ALPHER', 'Ada Alpher'))
ok('a self tag with the full name is own', TT.names_member('SEN. ADA ALPHER', 'Ada Alpher'))
ok('a same-surname different-first-name tag is NOT own for the COLLECTOR rule '
   '(succession-seat guard, names_member is unchanged)',
   not TT.names_member('SEN. ZOE ALPHER', 'Ada Alpher'))
ok('a different surname is not own', not TT.names_member('O`DONNELL', 'Ada Alpher'))
ok('the backtick folds to an apostrophe when matching a member', TT.names_member("O`ALPHER", "Ada O'Alpher"))
ok('[V2] the MIGRATION rule is LENIENT: a same-surname tag is the row own member and is NEVER cut',
   TT.tag_ownership('JOHNSON (voice-over)', 'Mike Johnson') == TT.OWN
   and TT.tag_ownership('SEN. RON JOHNSON', 'Mike Johnson') == TT.OWN)
ok('[V2] a hyphenated or particled surname component still reads as own',
   TT.tag_ownership('BARRÓN-LÓPEZ', 'Laura Lopez') == TT.OWN
   and TT.tag_ownership('Fred de Sam Lazaro', 'Fred Lazaro') == TT.OWN)
ok('[V2] a tag that clearly names someone else is FOREIGN',
   TT.tag_ownership('O`DONNELL', 'Ada Alpher') == TT.FOREIGN
   and TT.tag_ownership('SEN. PATTY MURRAY (D-WA)', 'Chuck Grassley') == TT.FOREIGN)
ok('[V2] a broadcast role tag is FOREIGN even though it names nobody',
   TT.tag_ownership('UNIDENTIFIED MALE', 'Ada Alpher') == TT.FOREIGN
   and TT.tag_ownership('FOX (voice-over)', 'Ada Alpher') == TT.FOREIGN)
ok('[V2] a tag with no name left after the role and heading words is AMBIGUOUS, never cut',
   TT.tag_ownership('PRESIDENT AND', 'Ada Alpher') == TT.AMBIGUOUS)
ok('[V2] a near-miss on the member surname is AMBIGUOUS, never cut',
   TT.tag_ownership('ALPHERS', 'Ada Alpher') == TT.AMBIGUOUS
   and TT.tag_ownership('ALPH', 'Ada Alpher') == TT.AMBIGUOUS)
ok('[V2] filler words are never a speaker name: a bare Q: can never be a tag',
   not TT.qualifies('OK. Q', LEX) and not TT.qualifies('Q', LEX)
   and len(TT.split_spans('OK. Q: Do you think that it is possible to make progress on your plan?',
                          'Bill Cassidy', LEX, 'cfr')) == 1)
OKOK = ('OK. OK. WILD (voice-over): Members of the Committee pressed Cheatle for answers about why '
        'the former president was allowed to take the stage that evening.')
sp = TT.split_spans(OKOK, 'James Comer', LEX, 'cnr')
ok('[V2] leading filler is not part of the tag, so the member keeps "OK. OK." (rows 69544/244993)',
   len(sp) == 2 and sp[1][0] == 'WILD (voice-over)' and sp[0][1] == 'OK. OK.',
   (sp[0][1], [s[0] for s in sp[1:]]))

SELF = ('My first answer is deliberately long enough to clear the eight-word floor here. '
        'O`DONNELL: The host asks a question that is also long enough to clear the floor. '
        'ALPHER: My second answer is long enough to clear the eight-word floor as well.')
sp = TT.split_spans(SELF, 'Ada Alpher', LEX, 'msnow')
ok('a row whose member speaks again splits into three turns, the third marked own',
   len(sp) == 3 and sp[1][0] == 'O`DONNELL' and sp[2][0] == 'ALPHER'
   and [h.own for h in TT.find_turn_tags(SELF, 'Ada Alpher', LEX, 'msnow')] == [False, True],
   [s[0] for s in sp])
ok('[V2] first_foreign_hit finds the CUT POINT and ignores the member own later turn',
   TT.first_foreign_hit(SELF, 'Ada Alpher', LEX, 'msnow').tag == 'O`DONNELL')
ok('[V2] a row whose only tags are the member own name has no cut point',
   TT.first_foreign_hit('My answer is long enough to clear the floor here. ALPHER: And my second '
                        'answer is also long enough to clear the eight-word floor.',
                        'Ada Alpher', LEX, 'msnow') is None)

print('[stored text: nothing is lost -- the spans tile the whole string]')
for name, member, show, text, _t, _k in GRADED:
    spans = TT.split_spans(text, member, LEX, show)
    rebuilt = ''.join(text[s:e] for _tag, _seg, s, e, _c in spans)
    contiguous = all(spans[i][3] == spans[i + 1][2] for i in range(len(spans) - 1))
    ok('spans tile the string: ' + name[:46],
       rebuilt == text and spans[0][2] == 0 and spans[-1][3] == len(text) and contiguous)
ok('every span is either the kept text or carries a class',
   all(s[4] in TT.CLASSES for s in TT.split_spans(GRADED[0][3], GRADED[0][1], LEX, GRADED[0][2])[1:]))

print('[stored text: resolution is fail-closed -- drop, never guess]')
ok('a full roster name resolves', TT.resolve_tag('SEN. ADA ALPHER', INDEX) == 'Ada Alpher')
ok('a nickname resolves', TT.resolve_label('BEN BRAVO', INDEX) == 'Ben Bravo')
ok('a role tail is ignored', TT.resolve_tag('ADA ALPHER, U.S. SENATOR FROM SOMEWHERE', INDEX)
   == 'Ada Alpher')
ok('a party suffix is stripped', TT.resolve_label('SEN. ADA ALPHER, D-XX', INDEX) == 'Ada Alpher')
ok('a bare surname with NO honorific never binds (the page alias map does not exist here)',
   TT.resolve_label('ALPHER', INDEX) is None and TT.resolve_label('O`DONNELL', INDEX) is None)
ok('a bare surname WITH an honorific binds only when the roster surname is unique',
   TT.resolve_label('SEN. ALPHER', INDEX) == 'Ada Alpher')
ok('a mismatched first name never binds (predecessor / relative guard)',
   TT.resolve_label('SEN. ZOE ALPHER', INDEX) is None)
ok('an off-roster host never binds', TT.resolve_tag('WALLACE (voice over)', INDEX) is None
   and TT.resolve_tag('LAUREN FOX, CNN CONGRESSIONAL CORRESPONDENT', INDEX) is None)
AMBIG = TT.build_roster_index(['Mike Johnson', 'Ron Johnson'])
ok('an ambiguous surname is never guessed', TT.resolve_label('SEN. JOHNSON', AMBIG) is None)

# --------------------------------------------------------------------------- the migration
print('[migration: importing the tool does nothing]')
ok('the tool is guarded by a main block', "if __name__ == '__main__':" in MIGRATOR_SRC)
ok('the tool opens the database read-only for a dry run',
   "mode=ro" in MIGRATOR_SRC and 'PRAGMA query_only' in MIGRATOR_SRC)
ok('[V2 REPLACES V1-INSERT] the tool can neither insert nor delete a speeches row, at all',
   'INSERT INTO speeches' not in MIGRATOR_SRC and 'DELETE FROM speeches' not in MIGRATOR_SRC
   and 'DROP TABLE' not in MIGRATOR_SRC)
ok('[V2] the only statement it runs against speeches is the truncating UPDATE',
   MIGRATOR_SRC.count('UPDATE speeches') == 2         # the truncate and the restore
   and 'UPDATE speeches SET text=?, nwords=? WHERE rowid=?' in MIGRATOR_SRC)
ok('the ledger table name is the agreed one', MIG.LEDGER == 'speeches_turn_split_ledger')
ok('[V2] the tool version says truncate-only', 'truncate' in MIG.TOOL_VERSION)

SCHEMA = ('CREATE TABLE speeches(member TEXT,date TEXT,source TEXT,chamber TEXT,'
          'package TEXT,nwords INTEGER,text TEXT)')
# (member, date, source, chamber, package, text) -- nwords is derived, as the live corpus has it
FIXTURE = [
    # 0 a bled row: host turn (full-name form, which is what teaches the lexicon) then the member
    ('Ada Alpher', '2021-07-14', 'INTERVIEW', 'msnow', 'https://ms.now/t/1',
     'My own words are deliberately long enough to clear the eight-word floor here. '
     'LAWRENCE O`DONNELL, MS NOW HOST: The host speaks next and this sentence is long enough '
     'to clear the floor. '
     'ALPHER: My second answer is also long enough to clear the eight-word floor here.'),
    # 1 a bled row whose later speaker IS another roster member -> STILL ONLY TRUNCATED in v2
    ('Ben Bravo', '2022-05-24', 'INTERVIEW', 'cnr', 'https://transcripts.cnn.com/t/2',
     'I will not support that motion and this sentence is long enough to clear the floor. '
     'SEN. CY CHARLIE: I disagree with my colleague and this sentence clears the floor too.'),
    # 2 a bled row whose later speaker is a host -> truncated
    ('Cy Charlie', '2024-05-01', 'INTERVIEW', 'cnr', 'https://transcripts.cnn.com/t/3',
     'Inaction is unacceptable and this opening sentence is long enough to clear the floor. '
     'RAJU (voice-over): Two months ago the Senate passed a foreign aid package worth billions.'),
    # 3 a clean row: no foreign tag at all -> untouched
    ('Dee Delta', '2025-01-02', 'INTERVIEW', 'cnr', 'https://transcripts.cnn.com/t/4',
     'Nothing in this row is a speaker tag, and I asked the CIA: their view is well known.'),
    # 4 an ALL-CAPS Washington Journal row -> untouched (rule d; unmeasured, out of bounds)
    ('Eli Echo', '2020-01-06', 'INTERVIEW', 'washington-journal', 'https://c-span.org/t/5',
     'WE WILL GET THE INTELLIGENCE BRIEFING TODAY AT 1:00. LESTER HOLT, ANCHOR, NBC: I HAVE '
     'ALWAYS KNOWN HE WAS A TOP THREAT TO AMERICA AND I SAID SO AT THE TIME.'),
    # 5 a CREC row that carries the same shape -> untouched (rule d; INTERVIEW only)
    ('Fay Foxtrot', '2021-03-03', 'CREC', 'house', 'CREC-2021-03-03',
     'My floor remarks are long enough to clear the floor. O`DONNELL: and this text must never '
     'be touched because this row is not an interview row at all.'),
    # 6 a short later member turn -> truncated; the short turn is ledgered, never re-inserted
    ('Gus Golf', '2023-02-02', 'INTERVIEW', 'cnr', 'https://transcripts.cnn.com/t/7',
     'This is my own answer and it is long enough to clear the eight-word floor. '
     'SEN. HAL HOTEL: I agree entirely.'),
    # 7 the contaminated gold row, verbatim -- SKIPPED in v2 by rule (c): it is a CU row
    ('Jay Juliet', '2020-02-23', 'INTERVIEW', 'fox-news-sunday', 'https://www.foxnews.com/t/8',
     'Oh, he does, clearly. We all take orders from Bertie. WALLACE (voice over): Committee '
     'members recently celebrated Bowman after the Senate Federal Credit Union announced it '
     'will name its new building after him.'),
    # 8 a BARE backtick surname tag: only the corpus-derived lexicon can admit this one
    ('Ida India', '2019-07-02', 'INTERVIEW', 'msnow', 'https://ms.now/t/9',
     'God help us all if this is what Americans do, and I will keep saying so. '
     'O`DONNELL: Congresswoman Frederica Wilson of Florida said this in her own statement.'),
    # 9 the row opens with a foreign tag, so it holds NO member words before it -> rule (a)/skip
    ('Hal Hotel', '2024-07-23', 'INTERVIEW', 'cnr', 'https://transcripts.cnn.com/t/10',
     'OK. OK. WILD (voice-over): Members of the Committee pressed her for answers about why the '
     'former president was allowed to take the stage that evening.'),
    # 10 [V2 rule a] the kept text is under the eight-word floor -> skipped, never emptied
    ('Ada Alpher', '2024-03-03', 'INTERVIEW', 'cnr', 'https://transcripts.cnn.com/t/11',
     'Yes. RAJU (voice-over): The speaker has clashed with defense hawks over the aid package '
     'and the whip count is nowhere near where the leadership needs it to be tonight.'),
    # 11 [V2 rule b] a label-bound row -> skipped even though it is a clean, long bleed
    ('Ben Bravo', '2024-04-04', 'INTERVIEW', 'cnr', 'https://transcripts.cnn.com/t/12',
     'A reviewed excerpt is pinned into this row and it is long enough to clear the floor. '
     'RAJU (voice-over): The speaker has clashed with defense hawks over the aid package.'),
    # 12 [V2 rule e] an ambiguous tag -> the whole row is left alone
    ('Cy Charlie', '2024-06-06', 'INTERVIEW', 'cnr', 'https://transcripts.cnn.com/t/13',
     'My own words here are deliberately long enough to clear the eight-word floor. '
     'CHARLIES (voice-over): a near miss on the member surname must never cut the row.'),
    # 13 [V2] a same-surname tag is the row own member: nothing foreign, nothing to do (245558)
    ('Ida India', '2023-08-17', 'INTERVIEW', 'qmb', 'https://transcripts.cnn.com/t/14',
     'You see there are these large flowers down there, some white ones and some pink ones. '
     'INDIA (voice-over): My name is Ida India. I am the chief of conservation for the parks '
     'and this sentence is long enough to clear the eight-word floor.'),
]
CU_TEXT_MARK = 'Credit Union'          # rule (c): the live gate is a keyword gate over the text


def build_db(path):
    con = sqlite3.connect(path)
    con.execute(SCHEMA)
    con.execute('CREATE INDEX ix_sp_m ON speeches(member)')
    for member, date, source, chamber, package, text in FIXTURE:
        con.execute('INSERT INTO speeches VALUES (?,?,?,?,?,?,?)',
                    (member, date, source, chamber, package, len(text.split()), text))
    con.commit()
    con.close()


def snapshot(path):
    con = sqlite3.connect('file:%s?mode=ro' % path.replace('\\', '/'), uri=True)
    rows = con.execute('SELECT rowid,member,date,source,chamber,package,nwords,text '
                       'FROM speeches ORDER BY rowid').fetchall()
    con.close()
    return rows


def file_sha256(path):
    h = hashlib.sha256()
    with io.open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


TMP = tempfile.mkdtemp(prefix='n12v2_')
try:
    DB = os.path.join(TMP, 'social_media.db')
    build_db(DB)
    BEFORE = snapshot(DB)
    BEFORE_SHA = hashlib.sha256(json.dumps(BEFORE, ensure_ascii=False,
                                           default=str).encode('utf-8')).hexdigest()
    # rule (b) and rule (c) sets, exactly as the live run supplies them
    LABEL_BOUND = frozenset({12})                                    # fixture 11 -> rowid 12
    CU_ROWS = frozenset(r[0] for r in BEFORE if CU_TEXT_MARK.lower() in (r[7] or '').lower())
    PROT = MIG.Protection(label_bound=LABEL_BOUND, cu_extract=CU_ROWS)
    ok('[V2 rule c] the CU set really does pin the gold row', CU_ROWS == frozenset({8}), CU_ROWS)

    print('[migration: the dry run changes nothing]')
    con = MIG.open_ro(DB)
    lexicon = MIG.build_lexicon(con, ROSTER)
    plans = MIG.plan_all(con, lexicon, PROT)
    con.close()
    ok('the dry run leaves every byte in place', snapshot(DB) == BEFORE)
    flagged = {p['rowid'] for p in plans}
    by_rowid = {p['rowid']: p for p in plans}
    ok('[V2 REPLACES V1-INSERT] the bled INTERVIEW rows are flagged, skips included',
       flagged == {1, 2, 3, 7, 8, 9, 10, 11, 12, 13}, sorted(flagged))
    ok('the clean row is not flagged', 4 not in flagged)
    ok('the ALL-CAPS Washington Journal row is not flagged (rule d)', 5 not in flagged)
    ok('the CREC row is not flagged (rule d)', 6 not in flagged)
    ok('[V2] the same-surname row carries no foreign tag at all, so it is not flagged (rule 2)',
       14 not in flagged)
    ok('the lexicon is derived from the corpus AND the roster',
       'odonnell' in lexicon and 'alpher' in lexicon)
    ok('a bare surname tag is admitted only because the corpus taught the lexicon',
       9 in flagged and 'odonnell' not in TT.roster_surnames(ROSTER)
       and not TT.qualifies('O`DONNELL', TT.roster_surnames(ROSTER))
       and TT.qualifies('O`DONNELL', lexicon))

    print('[migration: the five skip rules, each with its own reason]')
    ok('[V2 rule a] a row whose kept text is under eight words is SKIPPED, not emptied',
       by_rowid[11]['skip'] == MIG.SKIP_SHORT_KEPT and by_rowid[11]['kept_nwords'] < TT.MIN_TURN_WORDS,
       (by_rowid[11]['skip'], by_rowid[11]['kept_nwords']))
    ok('[V2 rule a] a row that opens with a foreign tag keeps no member words and is SKIPPED',
       by_rowid[10]['skip'] in (MIG.SKIP_NO_MEMBER_WORDS, MIG.SKIP_SHORT_KEPT),
       by_rowid[10]['skip'])
    ok('[V2 rule b] a label-bound row is SKIPPED with the label reason',
       by_rowid[12]['skip'] == MIG.SKIP_LABEL_BOUND, by_rowid[12]['skip'])
    ok('[V2 rule c] a CU-extract row is SKIPPED with the CU reason (the gold row)',
       by_rowid[8]['skip'] == MIG.SKIP_CU_EXTRACT, by_rowid[8]['skip'])
    ok('[V2 rule e] an ambiguous tag SKIPS the whole row',
       by_rowid[13]['skip'] == MIG.SKIP_AMBIGUOUS_TAG, by_rowid[13]['skip'])
    ok('[V2] every skipped row names its reason and every unskipped row has a cut point',
       all(p['skip'] in MIG.SKIP_REASONS for p in plans if p['skip'])
       and all(p['cut_at'] is not None and p['kept_nwords'] >= TT.MIN_TURN_WORDS
               for p in plans if not p['skip']))
    ok('[V2] the residue list is exactly the skipped rows',
       sorted(r['rowid'] for r in MIG.residue(plans)) == sorted(p['rowid'] for p in plans
                                                               if p['skip']))

    print('[migration: --apply refuses without a verified backup]')
    missing = os.path.join(TMP, 'nope.db')
    try:
        MIG.check_apply_preconditions(DB, missing, os.path.getsize(DB))
        ok('a missing backup is refused', False, 'no refusal')
    except MIG.Refused as e:
        ok('a missing backup is refused', 'backup' in str(e).lower(), str(e))
    short = os.path.join(TMP, 'short.db')
    io.open(short, 'wb').write(b'x')
    try:
        MIG.check_apply_preconditions(DB, short, os.path.getsize(DB))
        ok('a wrong-size backup is refused', False, 'no refusal')
    except MIG.Refused as e:
        ok('a wrong-size backup is refused', 'size' in str(e).lower(), str(e))
    BACKUP = os.path.join(TMP, 'social_media.backup.db')
    shutil.copy2(DB, BACKUP)
    io.open(os.path.join(TMP, '.pipeline_run.lock'), 'w').write('1')
    try:
        MIG.check_apply_preconditions(DB, BACKUP, os.path.getsize(DB))
        ok('a pipeline lock beside the database is refused', False, 'no refusal')
    except MIG.Refused as e:
        ok('a pipeline lock beside the database is refused', 'lock' in str(e).lower(), str(e))
    os.remove(os.path.join(TMP, '.pipeline_run.lock'))
    wal = DB + '-wal'
    io.open(wal, 'wb').write(b'\x00' * 64)
    try:
        MIG.check_apply_preconditions(DB, BACKUP, os.path.getsize(DB))
        ok('a non-empty -wal is refused', False, 'no refusal')
    except MIG.Refused as e:
        ok('a non-empty -wal is refused', 'wal' in str(e).lower(), str(e))
    os.remove(wal)
    ok('with a byte-sized backup and no lock the preconditions pass',
       MIG.check_apply_preconditions(DB, BACKUP, os.path.getsize(DB)) is True)
    try:
        MIG.run_apply(DB, BACKUP, protection=None, roster=ROSTER)
        ok('[V2] --apply without the label-safety inventory is refused', False, 'no refusal')
    except MIG.Refused as e:
        ok('[V2] --apply without the label-safety inventory is refused',
           'protect' in str(e).lower() or 'inventory' in str(e).lower(), str(e))

    print('[migration: --apply]')
    shutil.copy2(DB, BACKUP)          # a fresh backup, exactly as the operator would take one
    result = MIG.run_apply(DB, BACKUP, protection=PROT, roster=ROSTER)
    AFTER = snapshot(DB)
    ok('[V2 REPLACES V1-INSERT] the transaction truncated and inserted nothing',
       result['rows_truncated'] == 5 and result['rows_inserted'] == 0
       and result['rows_skipped'] == 5, result)
    ok('[V2 REPLACES V1-INSERT] the row count is unchanged: nothing was inserted or deleted',
       len(AFTER) == len(BEFORE), (len(BEFORE), len(AFTER)))
    by_id = {r[0]: r for r in AFTER}
    ok('the bled row keeps only the member own words',
       by_id[1][7] == 'My own words are deliberately long enough to clear the eight-word floor here.',
       by_id[1][7])
    ok('nwords is recomputed with the text',
       all(r[6] == len((r[7] or '').split()) for r in AFTER))
    ok('[V2] the member own later turn is NOT re-created as a row anywhere',
       not any('My second answer' in (r[7] or '') for r in AFTER))
    ok('[V2 REPLACES V1-INSERT] another roster member later turn is NOT re-attributed to them',
       not any('I disagree with my colleague' in (r[7] or '') for r in AFTER))
    ok('a host turn is never inserted',
       not any('Two months ago' in (r[7] or '') for r in AFTER))
    ok('a short later member turn is never inserted',
       not any('I agree entirely' in (r[7] or '') for r in AFTER))
    for rid, why in ((4, 'clean'), (5, 'all-caps Washington Journal'), (6, 'CREC'),
                     (8, 'CU-extract'), (10, 'no member words'), (11, 'short kept text'),
                     (12, 'label-bound'), (13, 'ambiguous tag'), (14, 'same-surname own tag')):
        ok('the %s row is byte-identical' % why, by_id[rid] == BEFORE[rid - 1],
           (by_id[rid], BEFORE[rid - 1]))
    ok('every truncated row is a strict prefix of what it held before',
       all((BEFORE[r[0] - 1][7] or '').startswith(r[7])
           for r in AFTER if r[7] != BEFORE[r[0] - 1][7]))

    print('[migration: the ledger holds every removed character]')
    con = sqlite3.connect(DB)
    cols = [c[1] for c in con.execute('PRAGMA table_info(%s)' % MIG.LEDGER)]
    led = con.execute('SELECT speech_rowid, original_row_json, kept_raw, removed_raw, '
                      'removed_json, detector_classes FROM %s ORDER BY speech_rowid'
                      % MIG.LEDGER).fetchall()
    con.close()
    ok('[V2 REPLACES V1-INSERT] the ledger carries no inserted-rowid column any more',
       'inserted_rowids' not in cols and 'removed_raw' in cols, cols)
    ok('one ledger row per truncated speeches row', len(led) == 5, len(led))
    total_recon = 0
    for rowid, orig_json, kept_raw, removed_raw, removed_json, classes in led:
        orig = json.loads(orig_json)
        removed = json.loads(removed_json)
        ok('row %d: kept + removed reproduces the original text byte for byte' % rowid,
           MIG.reconstruct(kept_raw, removed_raw) == orig['text'],
           (MIG.reconstruct(kept_raw, removed_raw)[:70], orig['text'][:70]))
        ok('row %d: the removed segments tile the removed text exactly' % rowid,
           ''.join(s['raw'] for s in removed) == removed_raw,
           (len(''.join(s['raw'] for s in removed)), len(removed_raw)))
        ok('row %d: the original row carries all seven columns' % rowid,
           set(orig) >= {'rowid', 'member', 'date', 'source', 'chamber', 'package', 'nwords', 'text'},
           sorted(orig))
        ok('row %d: every removed segment names its tag, class and ownership' % rowid,
           all(s.get('tag') and s.get('class') in TT.CLASSES
               and s.get('ownership') in (TT.OWN, TT.FOREIGN, TT.AMBIGUOUS) for s in removed),
           [(s.get('tag'), s.get('class'), s.get('ownership')) for s in removed])
        ok('row %d: the first removed segment is the FOREIGN one the cut was made at' % rowid,
           removed[0]['ownership'] == TT.FOREIGN, removed[0])
        ok('row %d: the detector classes are recorded on the ledger row' % rowid,
           bool(classes) and all(c in TT.CLASSES for c in classes.split(',')), classes)
        total_recon += 1
    ok('every ledgered row was reconstructed', total_recon == 5)
    ok('[V2] no skipped row was ledgered',
       {r[0] for r in led}.isdisjoint({4, 5, 6, 8, 10, 11, 12, 13, 14}), {r[0] for r in led})

    print('[migration: idempotency]')
    shutil.copy2(DB, BACKUP)
    again = MIG.run_apply(DB, BACKUP, protection=PROT, roster=ROSTER)
    ok('a second apply finds nothing to truncate',
       again['rows_truncated'] == 0 and again['rows_inserted'] == 0, again)
    ok('a second apply leaves every byte in place', snapshot(DB) == AFTER)
    con = MIG.open_ro(DB)
    lex2 = MIG.build_lexicon(con, ROSTER)
    left = [p['rowid'] for p in MIG.plan_all(con, lex2, PROT) if not p['skip']]
    con.close()
    ok('the only rows still carrying a foreign tag are the ones the migration deliberately skipped',
       left == [], left)

    print('[migration: --restore is byte-exact]')
    back = MIG.run_restore(DB)
    RESTORED = snapshot(DB)
    ok('restore reported what it did', back['rows_restored'] == 5, back)
    ok('the database is byte-identical to its pre-migration state', RESTORED == BEFORE,
       [(a, b) for a, b in zip(RESTORED, BEFORE) if a != b][:2])
    ok('the pre-migration digest is reproduced',
       hashlib.sha256(json.dumps(RESTORED, ensure_ascii=False,
                                 default=str).encode('utf-8')).hexdigest() == BEFORE_SHA)
    ok('restore twice is a no-op', MIG.run_restore(DB)['rows_restored'] == 0
       and snapshot(DB) == BEFORE)
    shutil.copy2(DB, BACKUP)
    ok('apply after restore is possible again (the ledger is marked, not deleted)',
       MIG.run_apply(DB, BACKUP, protection=PROT, roster=ROSTER)['rows_truncated'] == 5)
    MIG.run_restore(DB)
    ok('and restores byte-exact a second time', snapshot(DB) == BEFORE)

    print('[migration: apply -> restore returns the FILE, not just the rows]')
    # A raw file hash CANNOT come back equal and no honest test may claim it does: SQLite stamps
    # its own counters into the 100-byte header on every write (file change counter at offset
    # 24, schema cookie at 40, version-valid-for at 92).  The provable statement -- measured byte
    # by byte in audits/laundry_list_closeout_20260918/fixN12v2/v2_02_header_delta.out.txt -- is
    # that after apply, restore, dropping the ledger and VACUUM, the ONLY bytes that differ are
    # those three header counters and NOTHING at or past byte 100 differs at all.
    FRESH = os.path.join(TMP, 'fresh.db')
    build_db(FRESH)
    con = sqlite3.connect(FRESH)
    con.execute('VACUUM')
    con.close()
    FRESH_SHA = file_sha256(FRESH)
    ORIG_BYTES = io.open(FRESH, 'rb').read()
    FRESH_BACKUP = os.path.join(TMP, 'fresh.backup.db')
    shutil.copy2(FRESH, FRESH_BACKUP)
    MIG.run_apply(FRESH, FRESH_BACKUP, protection=PROT, roster=ROSTER)
    ok('apply really changed the file', file_sha256(FRESH) != FRESH_SHA)
    MIG.run_restore(FRESH)
    con = sqlite3.connect(FRESH)
    con.execute('DROP TABLE %s' % MIG.LEDGER)
    con.execute('VACUUM')
    con.close()
    NOW_BYTES = io.open(FRESH, 'rb').read()
    HEADER_COUNTERS = set(range(24, 28)) | set(range(40, 44)) | set(range(92, 96))
    diffs = [i for i in range(min(len(ORIG_BYTES), len(NOW_BYTES)))
             if ORIG_BYTES[i] != NOW_BYTES[i]]
    ok('[V2] after restore, every byte outside SQLite own header counters is identical',
       len(ORIG_BYTES) == len(NOW_BYTES) and set(diffs) <= HEADER_COUNTERS, diffs)
    ok('[V2] not one byte at or past the end of the file header differs',
       [i for i in diffs if i >= 100] == [], [i for i in diffs if i >= 100])

    print('[migration: labels, constants and out-of-bounds sources are never touched]')
    ok('no stance, category or gold label word appears in the migrator',
       not any(w in MIGRATOR_SRC for w in ('Champion', 'Ally', 'Skeptic', 'gold_stance',
                                           'support', 'oppose', 'neutral')))
    ok('the migrator touches one source only',
       MIGRATOR_SRC.count("source='INTERVIEW'") + MIGRATOR_SRC.count('"INTERVIEW"')
       + MIGRATOR_SRC.count("'INTERVIEW'") >= 1
       and 'CREC' not in MIGRATOR_SRC and 'CHRG' not in MIGRATOR_SRC)
    ok('the migrator never writes iv_fetch_log or any other table',
       'iv_fetch_log' not in MIGRATOR_SRC)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print('INTERVIEW TURN TAG TESTS — %d passed, %d FAILED' % (P, F))
sys.exit(1 if F else 0)
