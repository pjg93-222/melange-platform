# -*- coding: utf-8 -*-
"""The NAME STANDARD contract: staffer_network.name_standard.

Every rule in the module docstring gets an assertion here, plus the four
properties Paul's ruling depends on:

  * DETERMINISM      -- parsing the same string twice gives the same parts;
  * IDEMPOTENCE      -- parse(display_name(parse(x))) keys identically to
                        parse(x) and matches part-for-part once case is folded
                        (nickname_raw excluded: display_name does not render it);
  * SUFFIX ISOLATION -- a suffix never leaks into first or last;
  * NICKNAME ISOLATION -- a quoted/parenthesised nickname never reaches first.

and the equivalence that makes the re-key delta attributable: on 5,000 REAL raw
House SOD CSV strings the house_csv rule reproduces
``build_hill_personnel_spine.payroll_payee_key`` exactly, except where the raw
carries a hyphen-glued suffix -- which is the one intended change, and is
asserted to be the ONLY difference.

The 5,000 strings are real, not reconstructed: they were drawn (seed 20260906)
from house_personnel_observations.ndjson.gz rows with source_form ==
'detail_csv', field payee_raw, and each row carries the builder's own key
computed at draw time.  When the audits/ tree is present the suite ALSO
re-computes those keys against the live builder, so the pin cannot drift.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import unicodedata
import unittest

FEC = Path(__file__).resolve().parents[2]
LIVE = FEC.parent
sys.path.insert(0, str(FEC))

from staffer_network.name_standard import (  # noqa: E402
    HOUSE_CSV_PARTICLES, HOUSE_CSV_PARTICLE_SEQUENCES, NAME_STANDARD_VERSION,
    NameParts, PARTICLES, POST_NOMINALS, SOURCES, SUFFIXES, canonical_first,
    class_table_path, display_name, fold_key, load_class_table, name_key,
    parse_name, parts_from_payee_key, reset_class_table_cache,
)

FIXTURE = FEC / "tests" / "fixtures" / "staffer_name_standard" / \
    "house_csv_raw_sample_20260906.ndjson"
GOLD = LIVE / "data" / "staffer_network" / "reference" / \
    "name_standard_gold_sample_20260906.ndjson"
#: v1.1.1 (2026-09-07).  The 2026-09-06 and _v1_1 files stay on disk as the
#: v1.0 and v1.1 records; the REGRESSION pin follows the SHIPPED parser, which
#: is now v1.1.1.
GOLD_PARSER = LIVE / "data" / "staffer_network" / "reference" / \
    "name_standard_gold_parser_20260907_v1_1_1.ndjson"
V1_TABLE = LIVE / "data" / "staffer_network" / "reference" / \
    "first_name_nicknames_v1.json"
SPINE = LIVE / "audits" / "staffer_network" / \
    "hill_personnel_spine_20260907_v7_primary" / "hill_person_spine.ndjson.gz"
BUILDER = LIVE / "audits" / "staffer_network" / "build_hill_personnel_spine.py"


def _load_fixture() -> list[dict]:
    if not FIXTURE.is_file():
        return []
    rows = []
    with FIXTURE.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            if "_header" in row:
                continue
            rows.append(row)
    return rows


def _load_gold() -> list[dict]:
    if not GOLD.is_file():
        return []
    with GOLD.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _load_gold_parser() -> list[dict]:
    if not GOLD_PARSER.is_file():
        return []
    with GOLD_PARSER.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


FIXTURE_ROWS = _load_fixture()
GOLD_ROWS = _load_gold()
GOLD_PARSER_ROWS = _load_gold_parser()


def _builder_key_folded(triple) -> str:
    last, first, middle = triple
    return "%s|%s|%s" % (fold_key(last), fold_key(first),
                         fold_key(middle or "")[:1])


# ==========================================================================
# RULE 1-3: the four source formats
# ==========================================================================
class HouseCsvRuleTests(unittest.TestCase):
    def test_positional_assignment_matches_the_builder_shape(self):
        parts = parse_name("SMITH JOHN A", "house_csv")
        self.assertEqual((parts.last, parts.first, parts.middle), ("SMITH", "JOHN", "A"))
        self.assertEqual(parts.format_detected, "house_csv")

    def test_tokens_past_index_two_are_retained_in_middle_key_neutrally(self):
        parts = parse_name("VANDERBERG JOHN Q PUBLIC", "house_csv")
        self.assertEqual(parts.middle, "Q PUBLIC")
        # The builder discards "PUBLIC"; the key uses only the first letter, so
        # retaining it cannot move a key.
        self.assertEqual(name_key(parts, nicknames=False), "vanderberg|john|q")

    def test_single_token_becomes_last_and_is_flagged(self):
        parts = parse_name("CHER", "house_csv")
        self.assertEqual(parts.last, "CHER")
        self.assertEqual(parts.first, "")
        self.assertEqual(parts.format_detected, "house_csv_single_token")

    def test_empty_input_is_not_an_error(self):
        for raw in ("", "   ", "///"):
            parts = parse_name(raw, "house_csv")
            self.assertEqual(parts.format_detected, "house_csv_empty")
            self.assertEqual(name_key(parts), "||")

    def test_date_hint_is_removed_before_tokenising(self):
        parts = parse_name("SMITH JOHN M 01/05/2019", "house_csv")
        self.assertEqual((parts.last, parts.first, parts.middle), ("SMITH", "JOHN", "M"))


class PayrollCommaRuleTests(unittest.TestCase):
    def test_every_left_token_joins_into_the_surname(self):
        parts = parse_name("VAN DER BERG, JOHN", "payroll_comma")
        self.assertEqual(parts.last, "VAN DER BERG")
        self.assertEqual(parts.first, "JOHN")

    def test_no_space_after_comma(self):
        parts = parse_name("ONEK,JOSEPH", "payroll_comma")
        self.assertEqual((parts.last, parts.first), ("ONEK", "JOSEPH"))

    def test_trailing_period_on_the_middle_initial(self):
        parts = parse_name("LEVISON, LARA K.", "payroll_comma")
        self.assertEqual((parts.last, parts.first, parts.middle), ("LEVISON", "LARA", "K"))

    def test_date_hint_stripped_first(self):
        parts = parse_name("DALY, BRENDAN 07/01/09", "payroll_comma")
        self.assertEqual((parts.last, parts.first), ("DALY", "BRENDAN"))
        self.assertEqual(parts.middle, "")

    def test_second_comma_carries_only_a_suffix(self):
        parts = parse_name("SMITH, JOHN, JR", "payroll_comma")
        self.assertEqual((parts.last, parts.first, parts.suffix), ("SMITH", "JOHN", "JR"))

    def test_no_comma_falls_back_to_the_csv_rule_and_says_so(self):
        parts = parse_name("SMITH JOHN A", "payroll_comma")
        self.assertEqual((parts.last, parts.first, parts.middle), ("SMITH", "JOHN", "A"))
        self.assertIn("no_comma", parts.format_detected)

    def test_missing_given_name_is_flagged_not_guessed(self):
        parts = parse_name("SMITH,", "payroll_comma")
        self.assertEqual(parts.last, "SMITH")
        self.assertEqual(parts.first, "")
        self.assertEqual(parts.format_detected, "payroll_comma_missing_given")


class SenateRuleTests(unittest.TestCase):
    def test_senator_title_is_dropped_and_the_name_kept(self):
        parts = parse_name("SENATOR CHARLES E SCHUMER", "senate")
        self.assertEqual((parts.first, parts.middle, parts.last),
                         ("CHARLES", "E", "SCHUMER"))
        self.assertTrue(parts.format_detected.startswith("senate_senator"))

    def test_senator_suffix_is_kept_as_a_part(self):
        parts = parse_name("SENATOR ROBERT P CASEY JR", "senate")
        self.assertEqual(parts.suffix, "JR")
        self.assertEqual(parts.last, "CASEY")

    def test_intern_compensation_yields_a_surname_only(self):
        parts = parse_name("INTERN COMPENSATION - SCHUMER", "senate")
        self.assertEqual(parts.last, "SCHUMER")
        self.assertEqual(parts.first, "")
        self.assertEqual(parts.format_detected, "senate_intern")

    def test_a_comma_routes_to_the_payroll_comma_rule(self):
        parts = parse_name("SMITH, JOHN A", "senate")
        self.assertEqual((parts.last, parts.first), ("SMITH", "JOHN"))
        self.assertEqual(parts.format_detected, "senate_comma")

    def test_no_prefix_and_no_comma_routes_to_display_order(self):
        parts = parse_name("JOHN A SMITH", "senate")
        self.assertEqual((parts.first, parts.last), ("JOHN", "SMITH"))
        self.assertTrue(parts.format_detected.startswith("senate_display"))


class DisplayRuleTests(unittest.TestCase):
    def test_western_order(self):
        parts = parse_name("Robert P. Casey Jr.", "display")
        self.assertEqual((parts.first, parts.middle, parts.last, parts.suffix),
                         ("Robert", "P", "Casey", "JR"))

    def test_comma_inversion(self):
        parts = parse_name("Smith, John A", "display")
        self.assertEqual((parts.last, parts.first, parts.middle), ("Smith", "John", "A"))
        self.assertEqual(parts.format_detected, "display_comma_inverted")

    def test_a_comma_before_a_suffix_is_not_an_inversion(self):
        parts = parse_name("John Smith, Jr.", "display")
        self.assertEqual((parts.first, parts.last, parts.suffix), ("John", "Smith", "JR"))
        self.assertNotEqual(parts.format_detected, "display_comma_inverted")

    def test_nickname_delimiters(self):
        for raw in ('John "Jack" Smith', "John (Jack) Smith",
                    "John ‘Jack’ Smith", "John [Jack] Smith",
                    "John “Jack” Smith"):
            with self.subTest(raw=raw):
                parts = parse_name(raw, "display")
                self.assertEqual(parts.nickname_raw, "Jack")
                self.assertEqual(parts.first, "John")
                self.assertEqual(parts.last, "Smith")

    def test_a_straight_apostrophe_is_never_a_nickname_delimiter(self):
        parts = parse_name("Mary O'Brien", "display")
        self.assertEqual(parts.nickname_raw, "")
        self.assertEqual(parts.last, "O'Brien")

    def test_two_nicknames_join(self):
        parts = parse_name('John "Jack" (JJ) Smith', "display")
        self.assertEqual(parts.nickname_raw, "Jack; JJ")

    def test_leading_honorific_is_dropped_while_two_tokens_remain(self):
        parts = parse_name("Mr Allen Irving Questrom", "display")
        self.assertEqual(parts.first, "Allen")
        self.assertIn("titled", parts.format_detected)
        # The guard: an honorific that IS the whole name survives.
        kept = parse_name("Dr Who", "display")
        self.assertEqual((kept.first, kept.last), ("Dr", "Who"))

    def test_single_token_goes_to_last_not_first(self):
        parts = parse_name("Cher", "display")
        self.assertEqual(parts.last, "Cher")
        self.assertEqual(parts.first, "")
        self.assertEqual(parts.format_detected, "display_single_token")

    def test_surname_particles_extend_the_surname_leftwards(self):
        parts = parse_name("Juan de la Cruz", "display")
        self.assertEqual(parts.last, "de la Cruz")
        self.assertEqual(parts.first, "Juan")
        self.assertEqual(name_key(parts, nicknames=False), "delacruz|juan|")

    def test_a_particle_at_position_zero_is_a_given_name(self):
        for raw, first, last in (("Van Jones", "Van", "Jones"),
                                 ("Al Green", "Al", "Green"),
                                 ("De Vries Smith", "De", "Smith")):
            with self.subTest(raw=raw):
                parts = parse_name(raw, "display")
                self.assertEqual(parts.first, first)
                self.assertEqual(parts.last, last)

    def test_a_trailing_particle_does_not_start_a_surname(self):
        parts = parse_name("Mary Ann Le", "display")
        self.assertEqual(parts.last, "Le")

    def test_date_hints_are_not_stripped_from_display_names(self):
        parts = parse_name("John 1/2/03 Smith", "display")
        self.assertIn("1", parts.middle)


# ==========================================================================
# RULE 2: the suffix separator law
# ==========================================================================
class SuffixRuleTests(unittest.TestCase):
    def test_whole_token_suffixes_in_every_spelling(self):
        # R1: "V" is NOT in this list any more -- see
        # test_bare_v_is_a_middle_initial_not_a_suffix.
        for token in ("JR", "Jr", "jr", "Jr.", "JR.", "SR", "Sr.", "II", "III",
                      "IV", "iii."):
            with self.subTest(token=token):
                parts = parse_name("SMITH JOHN %s" % token, "house_csv")
                self.assertEqual(parts.suffix, token.strip(".").upper())
                self.assertEqual(parts.last, "SMITH")

    def test_hyphen_glued_suffix_is_split_off(self):
        parts = parse_name("DALY-JR PATRICK", "house_csv")
        self.assertEqual(parts.last, "DALY")
        self.assertEqual(parts.suffix, "JR")

    def test_run_on_suffix_is_never_split(self):
        parts = parse_name("SMITHJR JOHN", "house_csv")
        self.assertEqual(parts.last, "SMITHJR")
        self.assertEqual(parts.suffix, "")

    def test_apostrophe_is_not_a_suffix_separator(self):
        parts = parse_name("O'V MARY", "house_csv")
        self.assertEqual(parts.last, "O'V")
        self.assertEqual(parts.suffix, "")

    def test_a_suffix_only_string_is_not_emptied(self):
        parts = parse_name("JR", "house_csv")
        self.assertEqual(parts.last, "JR")
        self.assertEqual(parts.suffix, "")

    def test_bare_v_is_a_middle_initial_not_a_suffix(self):
        """R1 (Paul, 2026-09-07).  v1.0 read this as a suffix and dropped it.

        The change is DELIBERATE and moves payroll keys; the spine id map
        absorbs them.  ``V`` is now an initial in every position and every
        source, and it reaches the key.
        """

        for raw, source in (("SMITH JOHN V", "house_csv"),
                            ("SMITH, JOHN V", "payroll_comma"),
                            ("SMITH,JOHN V.", "payroll_comma"),
                            ("John V. Smith", "display")):
            with self.subTest(raw=raw, source=source):
                parts = parse_name(raw, source)
                self.assertEqual(fold_key(parts.middle), "v")
                self.assertEqual(parts.suffix, "")
                self.assertEqual(name_key(parts, nicknames=False),
                                 "smith|john|v")
        self.assertNotIn("V", SUFFIXES)
        # THE CARVE-OUT: "V" is never a GIVEN NAME.  These twelve real strings
        # would otherwise read first="V" or last="QUEALLY V".
        for raw, source, expected in (
                ("HENNESSY V MICHAEL F", "house_csv",
                 ("MICHAEL", "F", "HENNESSY", "V")),
                ("SMITH V ROBERT B", "house_csv",
                 ("ROBERT", "B", "SMITH", "V")),
                ("WICKES V WILLIAM", "house_csv",
                 ("WILLIAM", "", "WICKES", "V")),
                ("LOVE V CHARLES M.", "house_csv",
                 ("CHARLES", "M", "LOVE", "V")),
                ("QUEALLY V,THOMAS F", "payroll_comma",
                 ("THOMAS", "F", "QUEALLY", "V"))):
            with self.subTest(raw=raw):
                parts = parse_name(raw, source)
                self.assertEqual((parts.first, parts.middle, parts.last,
                                  parts.suffix), expected)
                self.assertNotEqual(fold_key(parts.first), "v")
        # and the carve-out must NOT reach the trailing case R1 is aimed at
        self.assertEqual(fold_key(parse_name("SMITH V", "house_csv").first), "")
        # A regnal ordinal that is NOT V still lifts, so the set really did
        # shrink by exactly one member.
        self.assertEqual(parse_name("SMITH JOHN IV", "house_csv").suffix, "IV")
        self.assertEqual(display_name(parse_name("WALL KELSEY V", "house_csv")),
                         "Kelsey V. Wall")

    def test_hyphen_prefix_suffix_is_not_split(self):
        # "-JR" has no head before the hyphen, so nothing is lifted.
        parts = parse_name("JR-SMITH JOHN", "house_csv")
        self.assertEqual(parts.last, "JR-SMITH")
        self.assertEqual(parts.suffix, "")

    def test_suffix_is_excluded_from_the_key(self):
        with_suffix = parse_name("Robert Casey Jr.", "display")
        without = parse_name("Robert Casey", "display")
        self.assertEqual(name_key(with_suffix), name_key(without))

    def test_suffixes_from_both_sides_of_a_comma_are_collected(self):
        parts = parse_name("DE LA CRUZ JR, MARIA T", "payroll_comma")
        self.assertEqual(parts.suffix, "JR")
        self.assertEqual(parts.last, "DE LA CRUZ")


# ==========================================================================
# v1.1 RULINGS -- R2 house_csv surname particles
# ==========================================================================
class HouseCsvParticleTests(unittest.TestCase):
    """R2 (Paul, 2026-09-07).  Ruled particles join the surname, GUARDED.

    The guard is not decoration.  The corpus carries 257 observation rows
    whose surname IS the particle ("LE CATHERINE L.", "VAN BRITTANY M",
    "SAINT RICHARD C", "LA ALISA M", "DU MICHELLE C."), and a blind join
    destroys every one of them.
    """

    def test_a_single_particle_joins_the_surname(self):
        parts = parse_name("DEL BECCARO CHRISTOPHER E", "house_csv")
        self.assertEqual((parts.last, parts.first, parts.middle),
                         ("DEL BECCARO", "CHRISTOPHER", "E"))
        self.assertEqual(parts.format_detected, "house_csv_particle_surname")
        self.assertEqual(name_key(parts, nicknames=False),
                         "delbeccaro|christopher|e")

    def test_a_particle_run_joins_the_surname(self):
        parts = parse_name("DE LA ROSA ADRIAN D", "house_csv")
        self.assertEqual((parts.last, parts.first, parts.middle),
                         ("DE LA ROSA", "ADRIAN", "D"))

    def test_the_ruled_van_der_sequence(self):
        # "DER" is not a particle on its own, so only the ruled PAIR reads
        # this correctly.
        parts = parse_name("VAN DER LUGT ROELOF A.", "house_csv")
        self.assertEqual((parts.last, parts.first), ("VAN DER LUGT", "ROELOF"))
        self.assertIn(("van", "der"), HOUSE_CSV_PARTICLE_SEQUENCES)
        self.assertNotIn("der", HOUSE_CSV_PARTICLES)

    def test_the_guard_saves_a_surname_that_IS_the_particle(self):
        for raw, last, first, middle in (
                ("LE CATHERINE L.", "LE", "CATHERINE", "L"),
                ("VAN BRITTANY M", "VAN", "BRITTANY", "M"),
                ("SAINT RICHARD C", "SAINT", "RICHARD", "C"),
                ("LA ALISA M", "LA", "ALISA", "M"),
                ("DU MICHELLE C.", "DU", "MICHELLE", "C")):
            with self.subTest(raw=raw):
                parts = parse_name(raw, "house_csv")
                self.assertEqual((parts.last, parts.first, parts.middle),
                                 (last, first, middle))
                self.assertEqual(parts.format_detected, "house_csv")

    def test_the_guard_backs_off_one_particle_at_a_time(self):
        # Greedy consumption takes DE, LA and O and then finds only the
        # initial "F" left; backing off one token reads the real name.
        parts = parse_name("DE LA O IRVIN F", "house_csv")
        self.assertEqual((parts.last, parts.first, parts.middle),
                         ("DE LA O", "IRVIN", "F"))

    def test_two_tokens_never_join(self):
        parts = parse_name("LE LINCOLN", "house_csv")
        self.assertEqual((parts.last, parts.first), ("LE", "LINCOLN"))
        self.assertEqual(parts.format_detected, "house_csv")

    def test_mc_is_deliberately_not_a_particle(self):
        self.assertNotIn("mc", HOUSE_CSV_PARTICLES)
        parts = parse_name("MC DONALD RONALD J", "house_csv")
        self.assertEqual((parts.last, parts.first), ("MC", "DONALD"))

    def test_an_unmarked_double_surname_stays_positional(self):
        # The DOCUMENTED limit: nothing in the string marks the boundary.
        parts = parse_name("GRECO JOHNSON KRISTINE M", "house_csv")
        self.assertEqual((parts.last, parts.first), ("GRECO", "JOHNSON"))

    def test_a_suffix_is_lifted_before_the_particle_scan(self):
        parts = parse_name("ST. HILAIRE II EUGENE R", "house_csv")
        self.assertEqual((parts.last, parts.first, parts.middle, parts.suffix),
                         ("ST HILAIRE", "EUGENE", "R", "II"))

    def test_the_rule_reaches_the_payroll_comma_fallback(self):
        parts = parse_name("VAN DUSEN EMILY J", "payroll_comma")
        self.assertEqual(parts.last, "VAN DUSEN")
        self.assertIn("no_comma", parts.format_detected)

    def test_the_comma_form_is_untouched_by_R2(self):
        # Left of a comma is already the whole surname; R2 must not fire.
        parts = parse_name("VAN DUSEN, EMILY J", "payroll_comma")
        self.assertEqual(parts.last, "VAN DUSEN")
        self.assertEqual(parts.format_detected, "payroll_comma")


# ==========================================================================
# v1.1 RULINGS -- R3 glued job-title and party fragments
# ==========================================================================
class PayrollAnnotationTests(unittest.TestCase):
    """R3 (Paul, 2026-09-07).  Built from a census, not from guesses.

    28 Senate rows carry a ``)`` before the first comma; 0 House rows carry a
    parenthetical at all.  Both facts are what keep the rule narrow.
    """

    def test_a_glued_leading_job_title_is_removed(self):
        for raw, last, first, middle in (
                ("ASSISTANT(REPUBLICAN)SOMMER, MATTHEW",
                 "SOMMER", "MATTHEW", ""),
                ("DIRECTOR (FORT WORTH)NICHOLSON, EMILY J",
                 "NICHOLSON", "EMILY", "J"),
                ("ERSHIP LIAISON(MINORITY)HAWKINS, THOMAS P",
                 "HAWKINS", "THOMAS", "P"),
                ("PAYABLE TECHNICIAN (CL)LEVESKI, KIMBERLY B",
                 "LEVESKI", "KIMBERLY", "B"),
                ("SECRETARY(WASHINGTON D.C.)NOVAK, MILES T",
                 "NOVAK", "MILES", "T")):
            with self.subTest(raw=raw):
                parts = parse_name(raw, "payroll_comma")
                self.assertEqual((parts.last, parts.first, parts.middle),
                                 (last, first, middle))
                self.assertIn("glued_annotation", parts.format_detected)

    def test_an_unmatched_close_paren_prefix_is_removed_too(self):
        parts = parse_name("AND APPROPRIATIONS)HERRLE, CYNTHIA A",
                           "payroll_comma")
        self.assertEqual((parts.last, parts.first, parts.middle),
                         ("HERRLE", "CYNTHIA", "A"))

    def test_a_trailing_party_or_role_word_is_removed(self):
        parts = parse_name("CLESI, CHRISTIAN D FELLOW (DEMOCRATIC",
                           "payroll_comma")
        self.assertEqual((parts.last, parts.first, parts.middle),
                         ("CLESI", "CHRISTIAN", "D"))
        self.assertEqual(display_name(parts), "Christian D. Clesi")

    def test_the_trailing_strip_never_eats_the_given_name(self):
        # A person really can be called Fellow.  Token [0] is untouchable.
        parts = parse_name("SMITH, FELLOW", "payroll_comma")
        self.assertEqual((parts.last, parts.first), ("SMITH", "FELLOW"))

    def test_a_real_middle_name_is_not_a_job_title(self):
        # The census top-10 of "first middle token is a word" is ANN, MARIE,
        # ELIZABETH, ANNE, LYNN, JAMES, LEE -- all real.  None may be lost.
        for raw, middle in (("KITCHEN, DAWN MARIE", "MARIE"),
                            ("DUDLEY, DRENAN ELIZABETH", "ELIZABETH"),
                            ("HUNTER, JERRY LEE", "LEE"),
                            ("DOVE, CARRIE LYNN", "LYNN")):
            with self.subTest(raw=raw):
                self.assertEqual(parse_name(raw, "payroll_comma").middle,
                                 middle)

    def test_a_parenthetical_after_the_comma_is_not_a_leading_glue(self):
        parts = parse_name("SMITH, JOHN (JACK)", "payroll_comma")
        self.assertEqual(parts.last, "SMITH")
        self.assertNotIn("glued_annotation", parts.format_detected)

    def test_a_senator_state_code_is_dropped_not_read_as_a_nickname(self):
        parts = parse_name("SENATOR TOM UDALL (NM)", "senate")
        self.assertEqual((parts.first, parts.last), ("TOM", "UDALL"))
        self.assertEqual(parts.nickname_raw, "")

    def test_only_a_real_postal_code_is_dropped(self):
        # "(ZZ)" is not a state, so it is judged as a group like any other --
        # and being a capitalised single word, it is a nickname.
        parts = parse_name("SENATOR TOM UDALL (ZZ)", "senate")
        self.assertEqual(parts.nickname_raw, "ZZ")


# ==========================================================================
# v1.1 RULINGS -- R4 nickname vs annotation
# ==========================================================================
class NicknameVersusAnnotationTests(unittest.TestCase):
    """R4 (Paul, 2026-09-07).  A group is a nickname only if it looks like one.

    Evidence: in the LDA corpus -- the only display corpus carrying groups at
    all, 74 of 25,340 rows -- EVERY multi-word group is an annotation and the
    single-word ones are overwhelmingly real nicknames.
    """

    def test_a_single_capitalised_word_is_still_a_nickname(self):
        for raw in ('John "Jack" Smith', "John (Jack) Smith",
                    "John ‘Jack’ Smith", "John [Jack] Smith",
                    "John “Jack” Smith"):
            with self.subTest(raw=raw):
                parts = parse_name(raw, "display")
                self.assertEqual(parts.nickname_raw, "Jack")

    def test_a_multi_word_group_is_an_annotation_and_is_dropped(self):
        for raw in ("DAVID LESSTRANG (CONTRACT LOBBYIST)",
                    "JOHN ANDERSON (NAME ERROR/NOT A REAL PERSON)",
                    "JANE DOE (REGISTERED IN ERROR)",
                    "MARY ROE (FORMERLY BONICELLI)"):
            with self.subTest(raw=raw):
                parts = parse_name(raw, "display")
                self.assertEqual(parts.nickname_raw, "")
                self.assertTrue(parts.last)

    def test_a_role_or_status_word_is_an_annotation(self):
        for raw, expected_last in (("Keith Uncapher (Deceased)", "Uncapher"),
                                   ("Ann Lee (Retired)", "Lee"),
                                   ("Ann Lee (Contractor)", "Lee")):
            with self.subTest(raw=raw):
                parts = parse_name(raw, "display")
                self.assertEqual(parts.nickname_raw, "")
                self.assertEqual(parts.last, expected_last)

    def test_the_groups_own_quotes_are_stripped(self):
        parts = parse_name('PATRICIA ("TRISH") REILLY', "display")
        self.assertEqual(parts.nickname_raw, "TRISH")
        self.assertEqual((parts.first, parts.last), ("PATRICIA", "REILLY"))

    def test_a_trailing_effective_annotation_is_stripped(self):
        parts = parse_name("JILL DOWELL - EFFECTIVE 2/9/21", "display")
        self.assertEqual((parts.first, parts.middle, parts.last),
                         ("JILL", "", "DOWELL"))

    def test_an_unbalanced_parenthesis_truncates_rather_than_inventing_a_name(self):
        parts = parse_name("MATTHEW SMITH (TIKTOK VALESKO", "display")
        self.assertEqual((parts.first, parts.last), ("MATTHEW", "SMITH"))

    def test_a_trailing_scrape_artifact_is_dropped(self):
        parts = parse_name("Timothy D Hendrix Search", "display")
        self.assertEqual((parts.first, parts.middle, parts.last),
                         ("Timothy", "D", "Hendrix"))
        # The guard: two tokens must survive, so a two-token name is safe.
        self.assertEqual(parse_name("Amy Search", "display").last, "Search")

    def test_an_annotation_never_becomes_a_name_part(self):
        parts = parse_name("Keith Uncapher (Deceased)", "display")
        for field in (parts.first, parts.middle, parts.last):
            self.assertNotIn("deceased", fold_key(field))


# ==========================================================================
# v1.1 -- the version constant, and R5
# ==========================================================================
class VersionAndDisplayRulingTests(unittest.TestCase):
    def test_the_version_constant_is_bumped(self):
        self.assertEqual(NAME_STANDARD_VERSION, "1.2")

    def test_R5_display_name_never_renders_a_nickname(self):
        """Paul's minimal-duplication law, re-affirmed 2026-09-07.

        The gold was relabelled to this, not the renderer changed to the gold.
        """

        for raw in ('John "Jack" Smith', 'PATRICIA ("TRISH") REILLY',
                    "Isaac \"Jack\" Azran", "William “Fritz” Reber"):
            with self.subTest(raw=raw):
                parts = parse_name(raw, "display")
                self.assertTrue(parts.nickname_raw)
                rendered = display_name(parts)
                for token in parts.nickname_raw.split("; "):
                    self.assertNotIn(fold_key(token),
                                     [fold_key(piece)
                                      for piece in rendered.split()])


class InvisibleCharacterTests(unittest.TestCase):
    """RULE 1a -- glyphless characters are deleted in place, never split on.

    Caught on the real gold sample: LittleSis carries "Mario Th\\x82riault",
    CP850 mojibake for Thériault.  Splitting on the C1 control invented the
    middle name "Th" and the surname "riault"; the spine builder's ascii fold
    drops it in place and reads "Thriault", so splitting would have put the two
    sides of every join on different keys.
    """

    def test_c1_mojibake_does_not_split_a_surname(self):
        parts = parse_name("Mario Th\x82riault", "display")
        self.assertEqual(parts.first, "Mario")
        self.assertEqual(parts.last, "Thriault")
        self.assertEqual(parts.middle, "")
        self.assertEqual(name_key(parts, nicknames=False), "thriault|mario|")

    def test_zero_width_and_soft_hyphen_and_bom_do_not_split(self):
        for invisible in ("​", "­", "﻿", "‎", "�"):
            with self.subTest(codepoint=hex(ord(invisible))):
                parts = parse_name("Ana%sMaria Silva" % invisible, "display")
                self.assertEqual(parts.first, "AnaMaria")
                self.assertEqual(parts.last, "Silva")

    def test_real_whitespace_still_separates(self):
        for space in ("\t", "\n", " ", " ", "\v"):
            with self.subTest(codepoint=hex(ord(space))):
                parts = parse_name("SMITH%sJOHN" % space, "house_csv")
                self.assertEqual((parts.last, parts.first), ("SMITH", "JOHN"))

    def test_a_visible_separator_still_separates(self):
        # U+00B7 carries a glyph, so it is a separator and stays one -- this is
        # a DELIBERATE divergence from the builder's ascii fold, which deletes
        # it and welds the tokens together.
        parts = parse_name("Abdullah·Mehmet", "display")
        self.assertEqual((parts.first, parts.last), ("Abdullah", "Mehmet"))


class PunctuationPreservationTests(unittest.TestCase):
    def test_apostrophes_and_hyphens_survive_into_the_parts(self):
        for raw, expected in (("O'BRIEN MARY", "O'BRIEN"),
                              ("SMITH-JONES ANNE", "SMITH-JONES"),
                              ("D'ANGELO PAUL", "D'ANGELO")):
            with self.subTest(raw=raw):
                self.assertEqual(parse_name(raw, "house_csv").last, expected)

    def test_they_are_removed_only_at_key_time(self):
        self.assertEqual(name_key(parse_name("O'BRIEN MARY", "house_csv"),
                                  nicknames=False), "obrien|mary|")


class PayeeKeyLiftTests(unittest.TestCase):
    """``parts_from_payee_key`` -- the spine side of every join.

    The regression this guards is real and was caught in the 2026-09-06 delta
    run: reconstructing "LAST, FIRST M" from a stored payee_key and re-parsing
    it read a middle initial of "v" (VICTOR, VINCENT) as a Roman-numeral suffix
    and deleted it, inventing 183 phantom "suffix" rows.
    """

    def test_a_v_middle_initial_survives(self):
        parts = parts_from_payee_key("smith", "john", "v")
        self.assertEqual(parts.middle, "v")
        self.assertEqual(parts.suffix, "")
        self.assertEqual(name_key(parts, nicknames=False), "smith|john|v")

    def test_the_v_round_trip_hazard_is_gone_but_the_jr_one_is_not(self):
        """R1 retired the "V" hazard; the reason for the lift is now ``first``.

        Under v1.0 re-serialising a stored triple and re-parsing it read a
        middle initial "v" as a Roman numeral and deleted it.  R1 removed V
        from the suffix set, so that specific round trip is now SAFE -- this
        asserts it, because a stale comment is worse than no comment.  What is
        still not safe is a GIVEN NAME shaped like a suffix, which is why
        ``parts_from_payee_key`` stays.
        """

        naive = parse_name("SMITH, JOHN V", "payroll_comma")
        self.assertEqual(naive.suffix, "")
        self.assertEqual(fold_key(naive.middle), "v")
        self.assertEqual(name_key(naive, nicknames=False),
                         name_key(parts_from_payee_key("smith", "john", "v"),
                                  nicknames=False))
        # Still unsafe, and still the reason the lift exists.  A GIVEN NAME
        # shaped like a suffix survives only while it is the last token
        # standing (rule 2's degenerate guard); add a middle name behind it
        # and the round trip lifts it and loses the given name.
        self.assertEqual(parse_name("SMITH, JR", "payroll_comma").first, "JR")
        round_tripped = parse_name("SMITH, JR ANN", "payroll_comma")
        self.assertEqual(round_tripped.first, "ANN")
        self.assertEqual(round_tripped.suffix, "JR")
        lifted = parts_from_payee_key("smith", "jr", "a")
        self.assertEqual(lifted.first, "jr")
        self.assertEqual(lifted.suffix, "")
        self.assertNotEqual(name_key(round_tripped, nicknames=False),
                            name_key(lifted, nicknames=False))

    def test_a_hyphen_glued_suffix_in_last_is_still_lifted(self):
        parts = parts_from_payee_key("daly-jr", "patrick", None)
        self.assertEqual((parts.last, parts.suffix), ("daly", "JR"))

    def test_a_first_name_that_looks_like_a_suffix_is_left_alone(self):
        parts = parts_from_payee_key("smith", "jr", None)
        self.assertEqual(parts.first, "jr")
        self.assertEqual(parts.suffix, "")

    def test_multi_token_surnames_survive(self):
        parts = parts_from_payee_key("van der berg", "john", "q")
        self.assertEqual(parts.last, "van der berg")
        self.assertEqual(name_key(parts, nicknames=False), "vanderberg|john|q")

    def test_empty_fields_are_tolerated(self):
        self.assertEqual(name_key(parts_from_payee_key(None, None, None)), "||")


# ==========================================================================
# RULE 6: the key + the nickname class table
# ==========================================================================
class KeyAndClassTableTests(unittest.TestCase):
    def setUp(self):
        self.table, self.loaded = load_class_table()

    def test_the_committed_table_loads(self):
        self.assertTrue(self.loaded, "v2 class table missing at %s" % class_table_path())
        self.assertGreater(len(self.table), 300)

    def test_every_stem_is_a_fixed_point_and_chains_are_one_hop(self):
        for variant, stem in self.table.items():
            with self.subTest(variant=variant):
                self.assertEqual(self.table.get(stem), stem)

    def test_canonical_first_is_idempotent(self):
        for variant in list(self.table)[:200]:
            once = canonical_first(variant)
            self.assertEqual(canonical_first(once), once)

    def test_paul_named_ambiguous_stems_are_present_and_ambiguous(self):
        for stem, members in (("alex", ("alexander", "alexandra")),
                              ("sam", ("samuel", "samantha")),
                              ("pat", ("patrick", "patricia")),
                              ("chris", ("christopher", "christina")),
                              ("jo", ("joan", "josephine")),
                              ("terry", ("terence", "theresa")),
                              ("lee", ("leigh",))):
            with self.subTest(stem=stem):
                for member in members:
                    self.assertEqual(canonical_first(member), stem)
                self.assertEqual(canonical_first(stem), stem)

    def test_v1_multi_formal_ambiguity_paul_did_not_name_stays_unmerged(self):
        # "al" covers alan/albert/alfred/allen in v1; merging them would fuse
        # four different people.  It must remain its own class.
        self.assertEqual(canonical_first("al"), "al")
        for name in ("alan", "albert", "alfred", "allen"):
            self.assertEqual(canonical_first(name), name)

    def test_unknown_names_fold_to_themselves(self):
        self.assertEqual(canonical_first("Zebediah"), "zebediah")
        self.assertEqual(canonical_first("Élodie"), "elodie")

    def test_key_shape(self):
        parts = parse_name("Bill Smith", "display")
        self.assertEqual(name_key(parts), "smith|william|")
        self.assertEqual(name_key(parts, nicknames=False), "smith|bill|")

    def test_key_uses_only_the_middles_first_letter(self):
        self.assertEqual(name_key(parse_name("John Quincy Smith", "display")),
                         name_key(parse_name("John Q Smith", "display")))

    def test_key_folds_unicode(self):
        self.assertEqual(name_key(parse_name("José Martínez", "display")),
                         "martinez|jose|")

    def test_nicknames_flag_disables_folding(self):
        parts = parse_name("Bill Smith", "display")
        self.assertNotEqual(name_key(parts, nicknames=True),
                            name_key(parts, nicknames=False))


# ==========================================================================
# RULE 8: display rendering
# ==========================================================================
class DisplayNameTests(unittest.TestCase):
    def test_the_worked_example(self):
        parts = parse_name("CASEY, ROBERT P", "payroll_comma")
        self.assertEqual(display_name(parts), "Robert P. Casey")
        parts = parse_name("CASEY JR, ROBERT P", "payroll_comma")
        self.assertEqual(display_name(parts), "Robert P. Casey Jr.")

    def test_gaelic_and_apostrophe_prefixes(self):
        cases = {
            "MCDONALD RONALD": "Ronald McDonald",
            "MACARTHUR KATHLEEN": "Kathleen MacArthur",
            "O'BRIEN MARY": "Mary O'Brien",
            "D'ANGELO PAUL": "Paul D'Angelo",
            "SMITH-JONES ANNE": "Anne Smith-Jones",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(display_name(parse_name(raw, "house_csv")), expected)

    def test_mac_run_ons_are_not_split(self):
        self.assertEqual(display_name(parse_name("MACK CONNIE", "house_csv")),
                         "Connie Mack")
        self.assertEqual(display_name(parse_name("MACHADO LUIS", "house_csv")),
                         "Luis Machado")

    def test_mixed_case_input_is_left_alone(self):
        parts = parse_name("Kathleen MacKenzie", "display")
        self.assertEqual(display_name(parts), "Kathleen MacKenzie")

    def test_particles_lowercase_inside_a_surname(self):
        self.assertEqual(display_name(parse_name("DE LA CRUZ, MARIA", "payroll_comma")),
                         "Maria de la Cruz")

    def test_roman_numerals_take_no_period(self):
        # R1: "V" is no longer a suffix, so it is not in this table.
        for suffix, rendered in (("III", "III"), ("IV", "IV"),
                                 ("JR", "Jr."), ("SR", "Sr.")):
            with self.subTest(suffix=suffix):
                parts = parse_name("SMITH JOHN %s" % suffix, "house_csv")
                self.assertTrue(display_name(parts).endswith(rendered))

    def test_a_single_letter_middle_gains_a_period(self):
        self.assertEqual(display_name(parse_name("SMITH JOHN A", "house_csv")),
                         "John A. Smith")

    def test_the_nickname_is_not_rendered(self):
        parts = parse_name('John "Jack" Smith', "display")
        self.assertEqual(display_name(parts), "John Smith")

    def test_van_and_von_particles_lowercase_like_de_la(self):
        cases = {
            "VAN BUREN, MARTIN": "Martin van Buren",
            "VON BRAUN, WERNHER": "Wernher von Braun",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(
                    display_name(parse_name(raw, "payroll_comma")), expected)


# ==========================================================================
# Properties
# ==========================================================================
def _property_corpus() -> list[tuple[str, str]]:
    corpus = [
        ("house_csv", "SMITH JOHN A"), ("house_csv", "DALY-JR PATRICK"),
        ("house_csv", "O'BRIEN MARY"), ("house_csv", "CHER"),
        ("payroll_comma", "VAN DER BERG, JOHN"),
        ("payroll_comma", "DALY, BRENDAN 07/01/09"),
        ("payroll_comma", "DE LA CRUZ JR, MARIA T"),
        ("senate", "SENATOR ROBERT P CASEY JR"),
        ("senate", "INTERN COMPENSATION - SCHUMER"),
        ("display", 'John "Jack" Smith'), ("display", "Smith, John"),
        ("display", "Juan de la Cruz"), ("display", "José Martínez"),
        ("display", "KENT R HANCE"), ("display", "Mr Allen Irving Questrom"),
        ("display", "Cher"), ("display", "Ronald McDonald"),
    ]
    for row in GOLD_ROWS:
        corpus.append((row["parser_source"], row["raw"]))
    for row in FIXTURE_ROWS[:1500]:
        corpus.append(("house_csv", row["raw"]))
    return corpus


class PropertyTests(unittest.TestCase):
    def test_parsing_is_deterministic(self):
        for source, raw in _property_corpus():
            first = parse_name(raw, source)
            second = parse_name(raw, source)
            self.assertEqual(first, second)
            self.assertEqual(name_key(first), name_key(second))

    def test_no_input_raises(self):
        weird = ["", " ", ",", ",,,", "()", '""', "-", "'", "’", "123",
                 "中文名字", "\U0001f600", "JR", "  ,  ",
                 "a" * 500, "SMITH,", ",JOHN", "(())", "[", "\\", "%s"]
        for source in SOURCES:
            for raw in weird:
                with self.subTest(source=source, raw=raw):
                    parse_name(raw, source)
        for source, raw in _property_corpus():
            parse_name(raw, source)

    def test_unknown_source_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            parse_name("John Smith", "linkedin")

    def test_display_round_trip_is_idempotent(self):
        for source, raw in _property_corpus():
            if source != "display":
                continue
            once = parse_name(raw, "display")
            twice = parse_name(display_name(once), "display")
            with self.subTest(raw=raw):
                self.assertEqual(name_key(once), name_key(twice))
                self.assertEqual(
                    [fold_key(once.first), fold_key(once.middle),
                     fold_key(once.last), once.suffix],
                    [fold_key(twice.first), fold_key(twice.middle),
                     fold_key(twice.last), twice.suffix])

    def test_a_suffix_never_leaks_into_first_or_last(self):
        for source, raw in _property_corpus():
            parts = parse_name(raw, source)
            if not parts.suffix:
                continue
            with self.subTest(raw=raw):
                for field in (parts.first, parts.last, parts.middle):
                    for token in field.split():
                        self.assertNotIn(token.strip(".").upper(), SUFFIXES)

    def test_a_nickname_never_reaches_first_middle_or_last(self):
        """No nickname TOKEN survives into a name part.

        Token-wise, not substring-wise: the gold sample contains
        'Soohyung "Soo" Kim', where the nickname "Soo" is a substring of the
        given name "Soohyung" and must stay there.  The escape hatch is a raw
        string that genuinely repeats the token (a nickname equal to the given
        name), which is not a leak.
        """

        for source, raw in _property_corpus():
            parts = parse_name(raw, source)
            if not parts.nickname_raw:
                continue
            part_tokens = [fold_key(token)
                           for field in (parts.first, parts.middle, parts.last)
                           for token in field.split()]
            raw_tokens = [fold_key(token) for token in
                          raw.replace('"', " ").replace("(", " ")
                             .replace(")", " ").replace("[", " ")
                             .replace("]", " ").replace("“", " ")
                             .replace("”", " ").replace("‘", " ")
                             .replace("’", " ").split()]
            with self.subTest(raw=raw):
                for nickname in parts.nickname_raw.split("; "):
                    for token in nickname.split():
                        folded = fold_key(token)
                        if folded and folded in part_tokens:
                            self.assertGreaterEqual(
                                raw_tokens.count(folded), 2,
                                "nickname token %r leaked into the parts" % token)

    def test_the_key_never_carries_punctuation_or_case(self):
        for source, raw in _property_corpus():
            key = name_key(parse_name(raw, source))
            body = key.replace("|", "")
            with self.subTest(raw=raw):
                self.assertTrue(all(ch.isalnum() and not ch.isupper()
                                    for ch in body), key)


# ==========================================================================
# Builder equivalence on 5,000 REAL raw House SOD CSV strings
# ==========================================================================
def _builder_expected_from_parts(parts) -> str | None:
    """Re-derive ``payroll_payee_key``'s key from OUR v1.1 parts.

    The builder takes tokens[0], tokens[1], tokens[2] positionally from the
    suffix-stripped token list.  v1.1 differs from it in exactly two ways that
    are visible in the parts: it KEEPS a bare "V" that the builder deletes as a
    Roman numeral (R1), and it JOINS a ruled particle surname the builder
    splits (R2).  Undoing both -- delete every "v" token, then read the list
    positionally -- must reproduce the builder's key exactly.  This is a
    reconstruction, not a pattern match, so it cannot be fixture-lucky.

    Returns ``None`` when fewer than two tokens survive, which the builder
    itself refuses.
    """

    tokens = parts.last.split()
    if parts.first:
        tokens.append(parts.first)
    tokens.extend(parts.middle.split())
    tokens = [token for token in tokens if fold_key(token) != "v"]
    if len(tokens) < 2:
        return None
    middle_initial = fold_key(tokens[2])[:1] if len(tokens) > 2 else ""
    return "%s|%s|%s" % (fold_key(tokens[0]), fold_key(tokens[1]),
                         middle_initial)


class BuilderEquivalenceTests(unittest.TestCase):
    @unittest.skipUnless(FIXTURE_ROWS, "house_csv raw fixture not present")
    def test_house_csv_rule_equals_payroll_payee_key_except_for_three_ruled_fixes(self):
        """Every divergence is one of exactly three ruled changes.

        v1.0 had one (the hyphen-glued suffix).  v1.1 adds R1's bare-"V"
        middle initial and R2's particle surname.  Nothing else may differ:
        ``other`` is asserted empty, and the class counts are pinned so a
        silent change of population is caught too.
        """

        agree = suffix_fix = builder_none = 0
        v_initial = particle = both = 0
        other = []
        for row in FIXTURE_ROWS:
            raw, triple = row["raw"], row["builder_payee_key"]
            parts = parse_name(raw, "house_csv")
            ours = name_key(parts, nicknames=False)
            if triple is None:
                builder_none += 1
                self.assertNotIn(parts.format_detected,
                                 ("house_csv", "house_csv_particle_surname"),
                                 "builder refused %r but we read it as a "
                                 "complete name" % raw)
                continue
            theirs = _builder_key_folded(triple)
            if ours == theirs:
                agree += 1
                continue
            ours_last, ours_first, ours_mi = ours.split("|")
            their_last, their_first, their_mi = theirs.split("|")
            if (parts.suffix
                    and their_last == ours_last + fold_key(parts.suffix)
                    and ours_first == their_first and ours_mi == their_mi):
                suffix_fix += 1
                continue
            if _builder_expected_from_parts(parts) == theirs:
                carries_v = any(
                    fold_key(token) == "v"
                    for token in (parts.last.split() + [parts.first]
                                  + parts.middle.split()))
                joined = parts.format_detected.endswith("_particle_surname")
                if carries_v and joined:
                    both += 1
                elif carries_v:
                    v_initial += 1
                elif joined:
                    particle += 1
                else:
                    other.append((raw, ours, theirs, parts.format_detected))
                continue
            other.append((raw, ours, theirs, parts.format_detected))
        self.assertEqual(other, [], "unexplained divergences from the builder")
        self.assertGreater(agree, 0)
        # Pinned on the 5,248-row fixture, 2026-09-07.
        self.assertEqual(
            (agree, suffix_fix, v_initial, particle, both, builder_none),
            (5190, 0, 38, 20, 0, 0))
        # Negative control: the comparison is not vacuous.
        self.assertNotEqual(
            name_key(parse_name("SMITH JOHN A", "house_csv"), nicknames=False),
            _builder_key_folded(("smith", "jane", "a")))

    @unittest.skipUnless(FIXTURE_ROWS, "house_csv raw fixture not present")
    def test_the_fixture_is_the_size_and_shape_it_claims(self):
        self.assertGreaterEqual(len(FIXTURE_ROWS), 4000)
        for row in FIXTURE_ROWS[:50]:
            self.assertIn("raw", row)
            self.assertIn("builder_payee_key", row)

    @unittest.skipUnless(FIXTURE_ROWS and BUILDER.is_file(),
                         "live spine builder not present in this tree")
    def test_the_pinned_builder_keys_have_not_drifted(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("spine_builder_pin", BUILDER)
        module = importlib.util.module_from_spec(spec)
        sys.modules["spine_builder_pin"] = module
        spec.loader.exec_module(module)
        for row in FIXTURE_ROWS:
            live = module.payroll_payee_key(row["raw"])
            pinned = row["builder_payee_key"]
            with self.subTest(raw=row["raw"]):
                self.assertEqual(None if live is None else list(live), pinned)


# ==========================================================================
# The blind gold sample stays blind
# ==========================================================================
class GoldSampleTests(unittest.TestCase):
    @unittest.skipUnless(GOLD_ROWS, "gold sample not present")
    def test_the_labeler_file_carries_no_parser_output(self):
        allowed = {"sample_id", "source", "parser_source", "raw"}
        for row in GOLD_ROWS:
            self.assertEqual(set(row), allowed, row.get("sample_id"))

    @unittest.skipUnless(GOLD_ROWS, "gold sample not present")
    def test_the_sample_is_three_hundred_rows_with_unique_ids(self):
        self.assertEqual(len(GOLD_ROWS), 300)
        self.assertEqual(len({row["sample_id"] for row in GOLD_ROWS}), 300)

    @unittest.skipUnless(GOLD_ROWS, "gold sample not present")
    def test_every_family_is_represented(self):
        families = {row["source"] for row in GOLD_ROWS}
        self.assertEqual(families, {"house_csv", "payroll_comma", "lda",
                                    "quorum", "politico", "littlesis"})

    @unittest.skipUnless(GOLD_ROWS, "gold sample not present")
    def test_every_parser_source_is_a_declared_source(self):
        for row in GOLD_ROWS:
            self.assertIn(row["parser_source"], SOURCES)


# ==========================================================================
# v1.1.1 -- lane-owner ruling D: the "do" particle and the post-nominals
# ==========================================================================
class RulingDParticleTests(unittest.TestCase):
    """D1: "do" is a rule-7 particle, as "da", "das", "dos" and "du" already were."""

    def test_do_is_in_the_display_particle_set(self):
        self.assertIn("do", PARTICLES)
        for sibling in ("da", "das", "dos", "du"):
            self.assertIn(sibling, PARTICLES, "the sibling forms were already ruled")

    def test_do_extends_the_surname_leftwards(self):
        parts = parse_name("Antonia Germano Do Nascimento", "display")
        self.assertEqual(parts.first, "Antonia")
        self.assertEqual(parts.middle, "Germano")
        self.assertEqual(parts.last, "Do Nascimento")
        self.assertEqual(name_key(parts), "donascimento|antonia|g")

    def test_do_is_not_a_particle_in_the_house_csv_set(self):
        """R2's set is Paul's and narrower; ruling D did not touch it."""

        self.assertNotIn("do", HOUSE_CSV_PARTICLES)
        parts = parse_name("DO NASCIMENTO ANTONIA", "house_csv")
        self.assertEqual(parts.last, "DO")

    def test_a_surname_that_is_just_do_is_left_alone(self):
        parts = parse_name("Todd Do", "display")
        self.assertEqual(parts.first, "Todd")
        self.assertEqual(parts.last, "Do")


class RulingDPostNominalTests(unittest.TestCase):
    """D2: a credential is never a name part, never a key, never rendered."""

    def test_the_ruled_set_is_exactly_the_six(self):
        self.assertEqual(POST_NOMINALS,
                         frozenset({"esq", "jd", "phd", "mba", "cpa", "md"}))

    def test_a_trailing_credential_leaves_the_name(self):
        for raw, expected in (
            ("Jane Doe, Esq.", "ESQ"),
            ("Maria Lopez Ph.D.", "PHD"),
            ("Doe, Jane, PhD", "PHD"),
            ("Robert Smith M.D.", "MD"),
            ("Alice Ng CPA", "CPA"),
            ("Ravi Patel M.B.A.", "MBA"),
        ):
            with self.subTest(raw=raw):
                parts = parse_name(raw, "display")
                self.assertEqual(parts.post_nominals, expected)
                self.assertNotIn(fold_key(expected), fold_key(parts.last))
                self.assertNotIn(fold_key(expected), fold_key(parts.first))
                self.assertNotIn(fold_key(expected), fold_key(parts.middle))

    def test_a_run_of_credentials_is_lifted_in_source_order(self):
        parts = parse_name("Dana Lee J.D., M.B.A.", "display")
        self.assertEqual(parts.post_nominals, "JD MBA")
        self.assertEqual(parts.first, "Dana")
        self.assertEqual(parts.last, "Lee")

    def test_a_credential_never_reaches_the_key_or_the_display_name(self):
        plain = parse_name("Jane Doe", "display")
        credentialed = parse_name("Jane Doe, Esq.", "display")
        self.assertEqual(name_key(credentialed), name_key(plain))
        self.assertEqual(display_name(credentialed), display_name(plain))
        self.assertNotIn("esq", display_name(credentialed).casefold())

    def test_space_separated_initials_are_not_a_credential(self):
        """The whole reason the lift works on TEXT and not on tokens.

        ``_tokens`` throws the periods away, so at token level "Robert M. D."
        and "Robert M.D." are the same two tokens.  The rule allows a period
        between a credential's letters but never a space.
        """

        for raw in ("Robert M D", "Robert M. D."):
            with self.subTest(raw=raw):
                parts = parse_name(raw, "display")
                self.assertEqual(parts.post_nominals, "")
                self.assertEqual(parts.middle, "M")
        glued = parse_name("Robert M.D.", "display")
        self.assertEqual(glued.post_nominals, "MD")
        self.assertEqual(glued.last, "Robert")

    def test_a_credential_in_the_middle_of_a_name_is_left_alone(self):
        parts = parse_name("Robert Md Jones", "display")
        self.assertEqual(parts.post_nominals, "")
        self.assertEqual(parts.middle, "Md")
        self.assertEqual(parts.last, "Jones")

    def test_nothing_is_lifted_when_it_would_empty_the_name(self):
        for raw in ("MD", "Esq", "Cpa"):
            with self.subTest(raw=raw):
                parts = parse_name(raw, "display")
                self.assertEqual(parts.post_nominals, "")
                self.assertEqual(parts.last, raw)

    def test_the_payroll_readers_are_not_touched(self):
        """A payroll key must not move because a surname folds to MD."""

        comma = parse_name("SMITH, JANE MD", "payroll_comma")
        self.assertEqual(comma.post_nominals, "")
        self.assertEqual(comma.middle, "MD")
        csv = parse_name("MD JANE A", "house_csv")
        self.assertEqual(csv.post_nominals, "")
        self.assertEqual(csv.last, "MD")
        senate = parse_name("SENATOR JANE MD", "senate")
        self.assertEqual(senate.post_nominals, "")

    def test_a_suffix_still_survives_beside_a_credential(self):
        parts = parse_name("John Smith Jr., Esq.", "display")
        self.assertEqual(parts.suffix, "JR")
        self.assertEqual(parts.post_nominals, "ESQ")
        self.assertEqual(display_name(parts), "John Smith Jr.")

    def test_the_field_defaults_to_empty_everywhere_else(self):
        self.assertEqual(NameParts().post_nominals, "")
        self.assertEqual(parts_from_payee_key("SMITH", "JANE", "A").post_nominals, "")

    def test_the_field_is_last_so_positional_construction_is_unchanged(self):
        positional = NameParts("JANE", "A", "SMITH", "JR", "", "house_csv", "raw")
        self.assertEqual(positional.first, "JANE")
        self.assertEqual(positional.raw, "raw")
        self.assertEqual(positional.post_nominals, "")


# ==========================================================================
# Parser-output regression: the 2026-09-06 gold parser file
# ==========================================================================
class GoldParserRegressionTests(unittest.TestCase):
    """Re-parsing the 300 gold-sample raws reproduces the pinned parser output.

    ``name_standard_gold_parser_20260906.ndjson`` is the PARSER's own recorded
    output on the 300 raws of ``name_standard_gold_sample_20260906.ndjson`` --
    not a human label (the labeler file stays blind; see ``GoldSampleTests``).
    Every field the parser produced (parts, display_name, both name_key
    flavors) is pinned here, so a silent behaviour change on any of the 300
    rows is caught, and re-parsing all 300 raws can never raise -- the "both
    files" half of the no-exception property that ``PropertyTests`` covers
    for the labeler file.
    """

    @unittest.skipUnless(GOLD_PARSER_ROWS, "gold parser file not present")
    def test_the_file_is_three_hundred_rows_matching_the_gold_sample(self):
        self.assertEqual(len(GOLD_PARSER_ROWS), 300)
        self.assertEqual(len({row["sample_id"] for row in GOLD_PARSER_ROWS}), 300)
        sample_ids = {row["sample_id"] for row in GOLD_ROWS}
        parser_ids = {row["sample_id"] for row in GOLD_PARSER_ROWS}
        self.assertEqual(sample_ids, parser_ids)

    @unittest.skipUnless(GOLD_PARSER_ROWS, "gold parser file not present")
    def test_reparsing_reproduces_the_pinned_output_exactly(self):
        for row in GOLD_PARSER_ROWS:
            expected = row["parts"]
            with self.subTest(sample_id=row["sample_id"]):
                parts = parse_name(expected["raw"], row["parser_source"])
                self.assertEqual(parts.first, expected["first"])
                self.assertEqual(parts.middle, expected["middle"])
                self.assertEqual(parts.last, expected["last"])
                self.assertEqual(parts.suffix, expected["suffix"])
                self.assertEqual(parts.nickname_raw, expected["nickname_raw"])
                self.assertEqual(parts.format_detected, expected["format_detected"])
                self.assertEqual(parts.raw, expected["raw"])
                # v1.1.1: the field is pinned too, so a credential that starts
                # or stops being lifted on any of the 300 is caught here.
                self.assertEqual(parts.post_nominals,
                                 expected.get("post_nominals", ""))
                self.assertEqual(display_name(parts), row["display_name"])
                self.assertEqual(name_key(parts, nicknames=False),
                                  row["name_key_no_nicknames"])
                self.assertEqual(name_key(parts, nicknames=True),
                                  row["name_key_v2"])


# ==========================================================================
# v1 table integrity -- 2026-09-06 built v2; nothing authorises touching v1
# ==========================================================================
class V1TableIntegrityTests(unittest.TestCase):
    """``first_name_nicknames_v1.json`` is superseded by v2, never edited.

    ``rekey_delta_report.json`` pins the MODULE and the v2 TABLE sha256 (both
    verified equal to the live files -- see ``test_the_committed_table_loads``
    and this lane's own receipt) but records no v1 pin at all: v1 was out of
    scope for that run.  This suite records the baseline here, since nothing
    else does; ``receipt.json`` carries the same value.
    """

    #: Recorded 2026-09-07 by this suite from the live file; there was no
    #: earlier pin in rekey_delta_report.json or any receipt to read instead.
    V1_SHA256 = "39632ef4cfe7234ca19a1c1a122353d076593b8815e311258c44d226004aa62e"

    @unittest.skipUnless(V1_TABLE.is_file(), "v1 table not present")
    def test_v1_table_matches_the_recorded_baseline(self):
        digest = hashlib.sha256(V1_TABLE.read_bytes()).hexdigest()
        self.assertEqual(
            digest, self.V1_SHA256,
            "first_name_nicknames_v1.json changed -- v1 is superseded by v2 "
            "and is not authorised to change; re-derive and update the "
            "pinned baseline (here and in receipt.json) only on Paul's say.")

    def test_the_default_class_table_path_resolves_to_v2_not_v1(self):
        saved = os.environ.pop("STAFFER_NAME_CLASS_TABLE", None)
        try:
            self.assertEqual(class_table_path().name, "first_name_nicknames_v2.json")
            self.assertNotEqual(class_table_path().name, V1_TABLE.name)
        finally:
            if saved is not None:
                os.environ["STAFFER_NAME_CLASS_TABLE"] = saved


# ==========================================================================
# reset_class_table_cache -- the documented escape hatch for a swapped table
# ==========================================================================
class ClassTableCacheTests(unittest.TestCase):
    """``reset_class_table_cache`` invalidates ``load_class_table``'s cache.

    Its own docstring says "tests that swap the table file use this" -- this
    is that test: it proves the cache really is stale until reset, and that
    resetting really does pick up a changed ``STAFFER_NAME_CLASS_TABLE``.
    """

    def setUp(self):
        reset_class_table_cache()
        self._saved_env = os.environ.get("STAFFER_NAME_CLASS_TABLE")

    def tearDown(self):
        if self._saved_env is None:
            os.environ.pop("STAFFER_NAME_CLASS_TABLE", None)
        else:
            os.environ["STAFFER_NAME_CLASS_TABLE"] = self._saved_env
        reset_class_table_cache()

    def test_reset_forces_a_reload_after_the_env_override_changes(self):
        real_table, real_loaded = load_class_table()
        self.assertTrue(real_loaded)
        self.assertEqual(real_table.get("chris"), "chris")

        with tempfile.TemporaryDirectory() as tmp:
            swapped = Path(tmp) / "swapped.json"
            swapped.write_text(json.dumps({
                "schema_id": "test-swap",
                "entries": {"chris": "kristoff", "kristoff": "kristoff"},
            }), encoding="utf-8")
            os.environ["STAFFER_NAME_CLASS_TABLE"] = str(swapped)

            # Without a reset the module-level cache still serves the v2 table.
            still_cached, _ = load_class_table()
            self.assertEqual(still_cached.get("chris"), "chris")

            reset_class_table_cache()
            swapped_table, swapped_loaded = load_class_table()
            self.assertTrue(swapped_loaded)
            self.assertEqual(swapped_table.get("chris"), "kristoff")

        # The temp dir (and swapped.json with it) is gone now: restore the
        # override too, before resetting, or the next load degrades to the
        # documented "missing table" identity mapping instead of the real one.
        os.environ.pop("STAFFER_NAME_CLASS_TABLE", None)
        reset_class_table_cache()
        restored_table, _ = load_class_table()
        self.assertEqual(restored_table.get("chris"), "chris")


# ==========================================================================
# Properties on 20,000 real spine rows, streamed
# ==========================================================================
def _reconstruct_from_payee_key(payee_key: dict, comma: bool) -> str:
    """Rebuild a raw string from a stored ``payee_key`` triple.

    Mirrors the two shapes rule 3 documents: "LAST FIRST M" (house_csv) and
    "LAST, FIRST M" (payroll_comma).  A missing piece is simply omitted, never
    turned into stray punctuation or doubled whitespace.
    """

    last = (payee_key.get("last") or "").upper()
    first = (payee_key.get("first") or "").upper()
    middle = (payee_key.get("middle_initial") or "").upper()
    if comma:
        tail = " ".join(token for token in (first, middle) if token)
        if not last:
            return tail
        return ("%s, %s" % (last, tail)) if tail else "%s," % last
    return " ".join(token for token in (last, first, middle) if token)


def _reservoir_sample_lines(path: Path, sample_size: int, seed: int) -> list[str]:
    """Single-pass reservoir sample (Algorithm R) of non-blank lines.

    Every line gets an equal chance of landing in the sample and the file is
    never materialised in full -- only ``sample_size`` lines are ever held at
    once, which is the point of calling this on a 144,690-row spine file.
    """

    rng = random.Random(seed)
    reservoir: list[str] = []
    seen = 0
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            if seen < sample_size:
                reservoir.append(line)
            else:
                slot = rng.randint(0, seen)
                if slot < sample_size:
                    reservoir[slot] = line
            seen += 1
    return reservoir


SPINE_SAMPLE_SIZE = 20000
#: The lane's own re-key seed (rekey_delta.py, this test file's own docstring).
SPINE_SAMPLE_SEED = 20260906


@unittest.skipUnless(SPINE.is_file(), "hill_person_spine fixture not present")
class SpineStreamPropertyTests(unittest.TestCase):
    """No exception on 20,000 real ``payee_key`` triples, streamed from disk.

    The corpora above are synthetic and gold-sample-sized (300 rows); this is
    the module's actual production input at scale -- the Hill personnel spine
    itself, 144,690 rows as of 2026-09-07 -- reconstructed back into the two
    raw shapes ``parts_from_payee_key`` exists to avoid re-parsing naively
    (see ``PayeeKeyLiftTests``), then run through the ordinary string parser
    to prove IT never raises on real production keys either.
    """

    @classmethod
    def setUpClass(cls):
        lines = _reservoir_sample_lines(SPINE, SPINE_SAMPLE_SIZE, SPINE_SAMPLE_SEED)
        cls.payee_keys = [json.loads(line)["payee_key"] for line in lines]

    def test_the_sample_is_the_requested_size(self):
        self.assertEqual(len(self.payee_keys), SPINE_SAMPLE_SIZE)

    def test_no_exception_reconstructing_and_reparsing_house_csv_form(self):
        for payee_key in self.payee_keys:
            raw = _reconstruct_from_payee_key(payee_key, comma=False)
            with self.subTest(payee_key=payee_key):
                parts = parse_name(raw, "house_csv")
                for token in parts.suffix.split():
                    self.assertIn(token, SUFFIXES)

    def test_no_exception_reconstructing_and_reparsing_payroll_comma_form(self):
        for payee_key in self.payee_keys:
            raw = _reconstruct_from_payee_key(payee_key, comma=True)
            with self.subTest(payee_key=payee_key):
                parts = parse_name(raw, "payroll_comma")
                for token in parts.suffix.split():
                    self.assertIn(token, SUFFIXES)


if __name__ == "__main__":
    unittest.main()
