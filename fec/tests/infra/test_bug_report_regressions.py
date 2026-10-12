# -*- coding: utf-8 -*-
"""Regression tests for the independent 2026-07-14 bug report.

These tests intentionally exercise contracts that the historical kernel/full-audit suites did
not cover.  They are kept dependency-light so rebuildability can be checked with a clean Python
runtime as well as the workstation environment.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


FEC = Path(__file__).resolve().parents[2]
ROOT = FEC.parent
DATA_ROOT = Path(os.environ.get("MONEYBALL_ROOT", ROOT))
sys.path.insert(0, str(FEC))


def source_path(name: str) -> Path:
    matches = [
        path for path in FEC.rglob(name)
        if not any(
            part in {"tests", "attic", "__pycache__"} or part.startswith("_quarantine")
            for part in path.relative_to(FEC).parts[:-1]
        )
    ]
    if len(matches) != 1:
        raise RuntimeError("expected one source for %s, found %s" % (name, matches))
    return matches[0]


def source(name: str) -> str:
    return source_path(name).read_text(encoding="utf-8-sig")


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, source_path(filename))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_function(filename: str, function_name: str, namespace=None):
    tree = ast.parse(source(filename), filename=filename)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function_name)
    ns = dict(namespace or {})
    exec(compile(ast.Module(body=[fn], type_ignores=[]), filename, "exec"), ns)
    return ns[function_name]


class PipelineContracts(unittest.TestCase):
    def test_unknown_resume_target_is_a_hard_failure(self):
        run = subprocess.run(
            [sys.executable, str(FEC / "run_pipeline.py"), "--from", "definitely_not_a_step"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertNotEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertIn("unknown --from target", (run.stdout + run.stderr).lower())

    def test_pipeline_has_an_exclusive_run_lock(self):
        text = source("run_pipeline.py")
        self.assertIn("with pipeline_run_lock():", text)
        lock = source("pipeline_lock.py")   # the lock moved to fec/pipeline_lock.py on 2026-08-29 (0bf7de7e0)
        self.assertIn("os.O_EXCL", lock)
        self.assertIn("finally", lock)

    def test_weekly_refresh_never_publishes_from_a_skipped_rank_rebuild(self):
        text = source("weekly_refresh.py")
        self.assertNotIn("DASHBOARD FALLBACK", text)
        self.assertNotIn("--from','per_topic_flip", text)
        self.assertIn("hard=True", text)

    def test_weekly_timeout_terminates_the_whole_process_tree(self):
        # 2026-09-19: the tree kill moved out of weekly_refresh.py into the shared bounded
        # launcher, so "taskkill" in that file's text would now be satisfied by a comment alone.
        # Pin the CALLS instead: the step goes through run_bounded, the orphan guard through
        # kill_tree, and kill_tree is where taskkill /T lives.
        text = source("weekly_refresh.py")
        self.assertIn("bounded_subprocess.run_bounded(", text)
        self.assertIn("bounded_subprocess.kill_tree(", text)
        self.assertIn("CREATE_NEW_PROCESS_GROUP", text)
        launcher = source("bounded_subprocess.py")
        self.assertIn("'taskkill', '/F', '/T', '/PID'", launcher)

    def test_canonical_runtime_dependencies_are_declared_and_preflighted(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        for package in ("networkx", "numpy", "openpyxl", "pandas", "scikit-learn", "scipy"):
            self.assertIn(package, requirements)
        runner = source("run_pipeline.py")
        self.assertIn("preflight_dependencies", runner)
        self.assertIn("pip install -r requirements.txt", runner)


class MathAndSelectionContracts(unittest.TestCase):
    def test_unratified_formal_office_context_cannot_move_rank_authority(self):
        producer = source("compute_pillars.py")
        self.assertIn("E=dict(Ebase)", producer.replace(" ", ""))
        self.assertNotIn("Ebase[m])+ROLE_BONUS[m]", producer.replace(" ", ""))
        self.assertIn("formal_role_display_weight", producer)
        self.assertIn("formal_role_influence_bonus", producer)
        self.assertIn("display-only", producer.lower())

    def test_media_domain_normalization_preserves_signed_relative_magnitude(self):
        import stance.media_rules as media_rules

        normalized = media_rules.normalize_signed_extrema(
            {"high_pro": 8.0, "low_pro": 2.0, "high_anti": -10.0, "low_anti": -2.5, "zero": 0.0}
        )
        self.assertEqual(
            {"high_pro": 1.0, "low_pro": 0.25, "high_anti": -1.0, "low_anti": -0.25, "zero": 0.0},
            normalized,
        )

    def test_general_media_is_audited_without_inventing_policy_specificity(self):
        import stance.media_rules as media_rules

        ledger = media_rules.general_exclusion_ledger({"A": 0.8, "B": 0.0, "C": -0.4})
        self.assertEqual({"A", "C"}, set(ledger))
        self.assertTrue(all(row["applied_to_policy_domains"] is False for row in ledger.values()))
        core = source("moneyball_core.py")
        self.assertIn("normalize_signed_extrema", core)
        self.assertIn("general_exclusion_ledger", core)
        self.assertNotIn("disperse_general", core)

    def test_priming_charges_recruit_rate_times_recruited_influence(self):
        tree = ast.parse(source("lt_priming.py"), filename="lt_priming.py")
        prime = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "prime")
        body = ast.unparse(prime).replace(" ", "")
        self.assertIn("cost+=c*add", body)
        self.assertNotIn("cost+=c;", body)

    def test_network_selector_prefers_augmented_network_over_newer_base(self):
        import moneyball_core as core

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "congress_cosponsor_network_119_20260714.xlsx"
            aug = Path(tmp) / "congress_cosponsor_network_AUG_20260713.xlsx"
            base.write_bytes(b"base")
            aug.write_bytes(b"aug")
            os.utime(base, (200, 200))
            os.utime(aug, (100, 100))
            self.assertEqual(aug, Path(core.select_network_file([str(base), str(aug)])))


class ValidationContracts(unittest.TestCase):
    def test_audio_duration_floor_matches_two_minimum_clips(self):
        import audio_rules

        self.assertEqual(30.0, audio_rules.MIN_CLIP_TARGET_SEC)
        self.assertEqual(2, audio_rules.MIN_INDEPENDENT_PROGRAMS)
        self.assertEqual(
            audio_rules.MIN_CLIP_TARGET_SEC * audio_rules.MIN_INDEPENDENT_PROGRAMS,
            audio_rules.MIN_MEMBER_TARGET_SEC,
        )
        self.assertEqual(60.0, audio_rules.MIN_MEMBER_TARGET_SEC)
        self.assertEqual(5, audio_rules.MIN_MEMBER_SPEECH_UNITS)
        self.assertEqual(2, audio_rules.MIN_MEMBER_DISTINCT_TOPICS)
        for filename in (
            "b5_audio_descriptors_candidate.py",
            "b5_audio_build.py",
            "b5_audio_fetch.py",
            "b5_audio_verify.py",
        ):
            self.assertIn("audio_rules", source(filename), filename)

    def test_active_audio_contract_uses_the_same_duration_floor(self):
        import audio_rules

        artifact = json.loads((DATA_ROOT / "data" / "members" / "b5_audio_descriptors_v2.json").read_text(encoding="utf-8"))
        reviews = json.loads((DATA_ROOT / "data" / "members" / "b5_audio_reviews_v2.json").read_text(encoding="utf-8"))
        self.assertEqual(
            audio_rules.MIN_MEMBER_TARGET_SEC,
            artifact["measurement_contract"]["minimum_target_seconds_per_member"],
        )
        self.assertEqual(
            audio_rules.MIN_MEMBER_TARGET_SEC,
            reviews["contract"]["minimum_confirmed_target_seconds_per_member"],
        )
        self.assertEqual(
            audio_rules.MIN_MEMBER_SPEECH_UNITS,
            artifact["measurement_contract"]["minimum_speech_units_per_member"],
        )
        self.assertEqual(
            audio_rules.MIN_MEMBER_DISTINCT_TOPICS,
            reviews["contract"]["minimum_distinct_topics_per_member"],
        )
        self.assertIn("pitch_deviation_from_speaker_baseline_z", artifact["measurement_contract"])
        self.assertEqual(
            hashlib.sha256((DATA_ROOT / "data" / "members" / "b5_audio_reviews_v2.json").read_bytes()).hexdigest(),
            artifact["review_sha256"],
        )
        self.assertIn("--active", source("b5_audio_descriptors_candidate.py"))

    def test_external_gold_e_is_equal_weight_and_discriminant(self):
        artifact = json.loads((DATA_ROOT / "data" / "stance" / "big5_e_gold_anchored.json").read_text(encoding="utf-8"))
        validation = json.loads((DATA_ROOT / "data" / "stance" / "big5_external_gold_validation.json").read_text(encoding="utf-8"))
        self.assertEqual("ACTIVE_EXTERNAL_GOLD_ANCHORED", artifact["status"])
        self.assertGreaterEqual(len(artifact["members"]), 450)
        for row in validation["panel_results"].values():
            self.assertGreaterEqual(row["e_r"], .30)
            self.assertTrue(row["diagonal_dominant"])
            self.assertLess(row["e_permutation_p_one_sided"], .05)
        sample = next(iter(artifact["members"].values()))
        self.assertEqual({"blue_visibility": .5, "pink_observer": .5}, sample["channel_weights"])

    def test_staff_directory_rejects_missing_titles(self):
        validate_staff_rows = load_function("build_staff_directory.py", "validate_staff_rows")
        with self.assertRaisesRegex(ValueError, "no staff rows"):
            validate_staff_rows({})
        with self.assertRaisesRegex(ValueError, "missing title"):
            validate_staff_rows({"Q-1": {"n": "A Staffer", "t": "", "m": "Example Member"}})
        validate_staff_rows({"Q-1": {"n": "A Staffer", "t": "Legislative Director", "m": "Example Member"}})
        directory = json.loads((DATA_ROOT / "data/members/staff_directory.json").read_text(encoding="utf-8"))
        self.assertFalse(
            [(member, row.get("n")) for member, rows in directory.items()
             for row in rows if not str(row.get("t") or "").strip()]
        )

    def test_communication_style_requires_every_facet(self):
        module = load_module("validate_communication_style_test", "validate_communication_style.py")
        artifact = {
            "schema": "communication-style-v1",
            "status": "ACTIVE_DISPLAY_ONLY",
            "members": {
                "Example Member": {
                    "scope": {}, "raw_text": {}, "facets": {"warmth": {"percentile": 0.5}},
                    "public_exposure": None, "extraversion_status": "unknown",
                }
            },
        }
        errors = module.validate(artifact, ["Example Member"], {"members": {}}, verify_hashes=False)
        self.assertTrue(any("missing required facets" in error for error in errors), errors)

    def test_recorded_input_hashes_are_enforced(self):
        module = load_module("validate_communication_style_hash_test", "validate_communication_style.py")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "input.json"
            p.write_text("current", encoding="utf-8")
            artifact = {"inputs_sha256": {"input.json": hashlib.sha256(b"stale").hexdigest()}}
            errors = module.validate_input_hashes(artifact, Path(tmp))
            self.assertTrue(any("input hash mismatch" in error for error in errors), errors)

    def test_guarded_profile_validator_enforces_recorded_hashes(self):
        text = source("validate_guarded_profile_artifacts.py")
        self.assertIn("validate_input_hashes", text)
        self.assertIn("review_sha256", text)

    def test_ratified_receptivity_slots_do_not_silently_revert(self):
        text = source("receptivity_layer.py")
        self.assertNotIn("reverting RECEPT_N", text)
        self.assertNotIn("reverting to RECEPT_COOP", text)
        self.assertIn("required ratified", text)

    def test_affective_n_builder_has_rebuildable_input_path_and_atomic_output(self):
        text = source("emit_affective_n.py")
        self.assertIn("AFFECTIVE_N_INPUT", text)
        self.assertNotIn("claude-worktrees", text)
        self.assertIn("dump_atomic", text)

    def test_malformed_vote_evidence_cannot_be_treated_as_no_evidence(self):
        import moneyball_core as core

        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "vote_category_lean.json"
            bad.write_text('{"name_of": {}}', encoding="utf-8")
            core._VOTECORR = None
            with mock.patch.object(core, "p", return_value=str(bad)):
                with self.assertRaises(RuntimeError):
                    core.vote_corroboration()

    def test_malformed_delivery_evidence_cannot_be_treated_as_neutral(self):
        import moneyball_core as core

        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "say_do_gap.json"
            bad.write_text('{"wrong_key": {}}', encoding="utf-8")
            core._DELIV = None
            with mock.patch.object(core, "p", return_value=str(bad)):
                with self.assertRaises(RuntimeError):
                    core.delivery_mult("Example Member")


class IngestionContracts(unittest.TestCase):
    def test_float_coerced_manual_directions_remain_valid(self):
        import math
        pd=types.SimpleNamespace(isna=lambda value:isinstance(value,float) and math.isnan(value))
        parse_direction = load_function("build_archive_legislative.py", "parse_direction", {"pd":pd})
        self.assertEqual(1, parse_direction(1.0))
        self.assertEqual(-1, parse_direction(-1.0))
        self.assertIsNone(parse_direction(float("nan")))

    def test_congress_changes_on_january_third_not_january_first(self):
        text = source("collect/fetch_congressional_record.py")
        tree = ast.parse(text, filename="fetch_congressional_record.py")
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "congress_for_date")
        import datetime
        ns = {"datetime":datetime}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "fetch_congressional_record.py", "exec"), ns)
        self.assertEqual(116, ns["congress_for_date"]("2021-01-01"))
        self.assertEqual(116, ns["congress_for_date"]("2021-01-02"))
        self.assertEqual(117, ns["congress_for_date"]("2021-01-03"))

    def test_collector_never_decodes_scoring_text_with_replacement(self):
        text = source("collect/fetch_congressional_record.py")
        self.assertNotIn("decode('utf-8','replace')", text.replace('"', "'"))

    def test_govinfo_key_is_sent_in_a_header_not_the_url(self):
        # EVERY govinfo collector must send the key in the X-Api-Key HEADER, never the URL query string
        # (secret-hygiene: URL query leaks into logs/proxies). 2026-07-15: extended to the hearings collectors.
        for fn in ("collect/fetch_congressional_record.py", "collect/fetch_hearings_antag.py", "collect/fetch_hearings_seq.py"):
            text = source(fn)
            self.assertIn("X-Api-Key", text, fn)
            self.assertNotIn("api_key=%s", text, fn)
            self.assertNotIn("&api_key=", text, fn)

    def test_empty_social_cells_never_become_literal_nan_tokens(self):
        import math
        clean_social_text = load_function("big5_traits.py", "clean_social_text", {"math":math})
        self.assertIsNone(clean_social_text(float("nan")))
        self.assertIsNone(clean_social_text("nan"))
        self.assertEqual("real text", clean_social_text(" real text "))

    def test_electoral_history_uses_atomic_json_write(self):
        text = source("build_electoral_history.py")
        self.assertIn("dump_atomic", text)
        self.assertNotIn("json.dump(out", text)


class DisplayContracts(unittest.TestCase):
    def test_embedded_json_cannot_close_its_script_element(self):
        import moneyball_core as core

        payload = core.json_for_script({"title": "x</script><script>alert(1)</script>"})
        self.assertNotIn("</script", payload.lower())
        self.assertEqual(
            {"title": "x</script><script>alert(1)</script>"},
            json.loads(payload),
        )

    def test_dashboard_uses_ratified_monte_carlo_and_cascade_cap(self):
        bundle = source("build_dashboard_bundle.py")
        template = source("surfaces/templates/make_dashboard_html_00.tpl")
        self.assertIn("'MC':20", bundle.replace(" ", ""))
        self.assertNotIn("Math.min(0.95", template)
        self.assertIn("Math.min(0.9", template)

    def test_cfpb_display_has_one_layer_without_double_weighting_the_aggregate(self):
        contract = load_function(
            "build_dashboard_bundle.py",
            "dashboard_issue_contract",
            {
                "LBLCAT": {"CFPB Restrain": "CFPB", "CFPB Reach (-)": "CFPB"},
                "DISPLAY_LABEL": {
                    "CFPB Restrain": "CFPB Restrain (pro-CU; limits agency reach)"
                },
                "DROPPED_DISPLAY_CLONES": {"CFPB Reach (-)"},
            },
        )
        source_issues, labels, categories = contract(
            ["Interchange", "CFPB Restrain", "CFPB Reach (-)"]
        )
        self.assertEqual(["Interchange", "CFPB Restrain"], source_issues)
        self.assertEqual(1, sum("CFPB" in label for label in labels))
        self.assertEqual(["Interchange", "CFPB"], categories)
        producer = source("build_dashboard_bundle.py")
        self.assertIn("for lb in source_issues", producer)

    def test_big_five_missing_axes_are_not_drawn_as_median_measurements(self):
        template = source("surfaces/templates/make_member_desk_00.tpl")
        self.assertNotIn("P.big5_pct.map(_ns)", template)
        self.assertIn("Axes with no displayable source", template)  # 2026-07-17: estimate-layer commit @2133640 renamed the empty-state; assertion updated

    def test_dashboard_semantic_hash_ignores_only_volatile_freshness(self):
        producer = source("audit/dashboard_integrity.py")
        self.assertIn("k not in ('integrity','freshness')", producer)
        self.assertIn("data/stance/big5_gold_anchored.json", producer)

    def test_biography_links_protect_opener(self):
        template = source("surfaces/templates/make_member_desk_00.tpl")
        for line in template.splitlines():
            if "target=_blank" in line:
                self.assertIn("rel=noopener", line, line)

    def test_inputs_have_accessible_names(self):
        for template in (source("surfaces/templates/make_member_desk_00.tpl"), source("surfaces/templates/make_dashboard_html_00.tpl")):
            for line in template.splitlines():
                if "<input" in line and "type=hidden" not in line and " hidden" not in line:
                    self.assertTrue("aria-label=" in line or "aria-labelledby=" in line, line)

    def test_media_headlines_are_bounded_in_the_ui(self):
        template = source("surfaces/templates/make_member_desk_00.tpl")
        self.assertIn("clipTitle(", template)

    def test_product_surfaces_exclusion_accounting_and_freshness(self):
        template = source("surfaces/templates/make_member_desk_00.tpl")
        self.assertIn("Exclusion accounting", template)
        self.assertIn("Data freshness", template)

    def test_cu_frame_is_explicit_where_stances_are_shown(self):
        template = source("surfaces/templates/make_member_desk_00.tpl")
        self.assertIn("CU advocacy frame", template)
        self.assertIn("restrain CFPB reach", template)

    def test_model_dials_and_display_only_tiers_are_labeled_at_the_result(self):
        dashboard = source("surfaces/templates/make_dashboard_html_00.tpl")
        desk = source("surfaces/templates/make_member_desk_00.tpl")
        self.assertIn("Modeled chain holds", dashboard)
        self.assertIn("not an empirical probability", dashboard)
        self.assertIn("overall rank unchanged", desk)

    def test_rank_audit_compares_the_shipped_adjusted_authority(self):
        text = source("compute_pillars.py")
        self.assertIn("SHIPPED AUTHORITY PARITY", text)
        self.assertIn("effective_weight_diagnostics", text)

    def test_offtopic_exclusions_have_per_member_counts(self):
        producer = source("make_member_desk.py")
        template = source("surfaces/templates/make_member_desk_00.tpl")
        self.assertIn("social_exclusions_by_member", producer)
        self.assertIn("filtered social items", template)

    def test_big_five_axis_uses_canonical_extraversion_spelling_everywhere(self):
        self.assertNotIn("Extroversion", source("surfaces/templates/make_member_desk_00.tpl"))
        self.assertNotIn("Extroversion", source("audit/audit_full.py"))


class PersonalTextureContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module("personal_texture_contract_test", "personal_texture_layer.py")

    def test_attributed_third_party_first_person_quote_is_rejected(self):
        row = {
            "member": "Kevin Kiley",
            "evidence": 'Constituent Jodie told the committee, "We traveled to 10 countries and love visiting Paris."',
            "hints": ["places_travel"],
        }
        result = self.module.local_explicit(row)
        self.assertFalse(result["keep"], result)

    def test_memorial_companion_story_is_not_member_biography(self):
        row = {
            "member": "Michael Baumgartner",
            "evidence": ("Mike and I spoke often. Together, we traveled the world, visiting over 10 countries. "
                         "Unfortunately, he was taken from us too soon when he passed."),
            "hints": ["places_travel"],
        }
        self.assertFalse(self.module.local_explicit(row)["keep"])

    def test_somebody_elses_favorite_quote_is_not_member_biography(self):
        row = {
            "member": "Mark DeSaulnier",
            "evidence": "Our Speaker has a favorite quote where she says public opinion means everything.",
            "hints": ["favorite", "quotes_values"],
        }
        self.assertFalse(self.module.local_explicit(row)["keep"])

    def test_preceding_hypothetical_attribution_is_not_member_biography(self):
        row = {
            "member": "Steve Scalise",
            "evidence": (
                "A waiter is going to go: I can save that money and, for the first time, "
                "start a college fund for my kid."
            ),
            "hints": ["family"],
        }
        self.assertFalse(self.module.local_explicit(row)["keep"])

    def test_local_interview_detail_is_displayable_but_never_narrative_eligible(self):
        row = {
            "member": "Steve Scalise", "date": "2018-11-15",
            "source": "INTERVIEW", "locator": "https://example.test/interview",
            "url": "https://example.test/interview", "evidence": "I love hunting and fishing.",
            "hints": ["hobbies_sports"], "key": "fixture-key",
        }
        result = self.module.local_explicit(row)
        self.assertTrue(result["keep"], result)
        old_out = self.module.OUT
        try:
            with tempfile.TemporaryDirectory() as directory:
                self.module.OUT = os.path.join(directory, "personal.json")
                self.module.write_artifact([row], {"fixture-key": result}, 1)
                artifact = json.loads(Path(self.module.OUT).read_text(encoding="utf-8"))
        finally:
            self.module.OUT = old_out
        item = artifact["members"]["Steve Scalise"]["items"][0]
        self.assertTrue(item["display_eligible"])
        self.assertFalse(item["narrative_eligible"])

    def test_near_duplicate_claims_share_a_fingerprint(self):
        a = "My family and I traveled to ten countries during those years."
        b = "During those years, my family and I travelled to 10 countries!"
        self.assertEqual(self.module.claim_fingerprint(a), self.module.claim_fingerprint(b))

    def test_local_fallback_details_cannot_enter_narrative_prompts(self):
        text = source("b5_narratives.py")
        self.assertIn("narrative_eligible", text)


class DocumentationContracts(unittest.TestCase):
    def test_current_runbook_names_the_real_harnesses_and_runtime(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8-sig")
        # README dates its counts ("N ... as of YYYY-MM-DD; count only grows"); hold the original floors.
        kernel = re.search(r"(\d+) kernel checks as of \d{4}-\d{2}-\d{2}", readme)
        audit = re.search(r"(\d+) full-audit checks as of \d{4}-\d{2}-\d{2}", readme)
        self.assertTrue(kernel and int(kernel.group(1)) >= 64, "README must date a kernel-check count >= 64")
        self.assertTrue(audit and int(audit.group(1)) >= 97, "README must date a full-audit count >= 97")
        self.assertIn("pip install -r requirements.txt", readme)
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8-sig")
        self.assertIn("!requirements*.txt", gitignore)

    def test_paid_narrative_header_names_the_configured_primary_model(self):
        text = source("b5_narratives.py")
        self.assertNotIn("FABLE write", text)
        self.assertIn("claude-sonnet-5", text)

    def test_historical_architecture_review_is_not_presented_as_current_state(self):
        review = (ROOT / "ARCHITECTURE_REVIEW_2026-07-02.md").read_text(encoding="utf-8-sig")
        self.assertIn("Historical snapshot", review[:500])

    def test_audio_runbook_distinguishes_legacy_corpus_from_strict_gate(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8-sig")
        for fact in ("2,192", "1,761", "435", "strict profile gate accepts 0"):
            self.assertIn(fact, readme)


# ======================================================================================
# BUG (product owner, 2026-08-02): "I cant get back to the main dashboard or access it
# from the member desk, and all members should be linked to their profile."
#
# (A) Navigation was one-way in practice. The desk's only route back was a grey pill
#     labelled "Network view", visually identical to the Export/Import ghost buttons
#     beside it; lobby-network.html had no route back at all.
# (B) Member names were dead text almost everywhere. The addressing mechanism already
#     existed -- the desk selects a member from ?m=<display name> -- but only the single
#     floating "Member details" chip on the network canvas ever minted that address.
#
# Where behaviour is what matters these tests execute the SHIPPED JavaScript (extracted
# from the templates) under node; they are skipped, not failed, when node is unavailable.
# ======================================================================================

TEMPLATES = FEC / "surfaces" / "templates"
DESK_TPL = TEMPLATES / "make_member_desk_00.tpl"
DASH_TPL = TEMPLATES / "make_dashboard_html_00.tpl"
NODE = shutil.which("node")


def _extract_js_function(text: str, name: str) -> str:
    """Slice `function <name>(...){ ... }` out of a template by brace matching."""
    start = text.index("function %s(" % name)
    depth = 0
    i = text.index("{", start)
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[start:j + 1]
    raise AssertionError("unbalanced braces while extracting %s()" % name)


def _run_node(script: str, payload: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "harness.js")
        Path(path).write_text(script, encoding="utf-8")
        proc = subprocess.run([NODE, path, payload], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _dashboard_artifact_name() -> str:
    """The filename make_dashboard_html.py ACTUALLY writes.

    'Moneyball.html' (the combined shell) and 'Moneyball Dashboard.html' (the network
    dashboard) are different artifacts, so linking to the wrong one strands the user
    again.  The expected name is read out of the builder rather than typed here.
    """
    # the builder may guard the path with an env override
    # (out = os.environ.get('DASHBOARD_HTML_OUT') or MB_ROOT + r'\...'), so that prefix is
    # optional here; the filename itself is still whatever the builder literal says.
    match = re.search(r"^\s*out\s*=\s*(?:os\.environ\.get\(\s*'[^']+'\s*\)\s*or\s*)?"
                      r"MB_ROOT\s*\+\s*r?'\\([^']+)'",
                      source("make_dashboard_html.py"), re.M)
    assert match, "could not read the dashboard output filename from make_dashboard_html.py"
    return match.group(1)


class MemberDeskNavigation(unittest.TestCase):
    """(A) Every surface reachable from the dashboard has a route back to it."""

    def test_the_dashboard_artifact_filename_is_read_from_its_builder(self):
        self.assertEqual("Moneyball Dashboard.html", _dashboard_artifact_name())
        # the combined shell is a DIFFERENT artifact; nothing may link back to it by accident
        self.assertIn("'Moneyball.html'", source("make_combined.py"))

    def test_desk_header_links_back_to_the_dashboard_the_builder_writes(self):
        desk = DESK_TPL.read_text(encoding="utf-8-sig")
        header = desk.split("<header>", 1)[1].split("</header>", 1)[0]
        self.assertIn('href="%s"' % _dashboard_artifact_name(), header,
                      "the member desk header has no route back to the dashboard")
        self.assertIn("deskhome", header)

    def test_the_back_link_gets_the_desklink_treatment_not_a_ghost_button(self):
        dash_rule = DASH_TPL.read_text(encoding="utf-8-sig").split(".desklink{", 1)[1].split("}", 1)[0]
        desk_rule = DESK_TPL.read_text(encoding="utf-8-sig").split(".deskhome{", 1)[1].split("}", 1)[0]
        for prop in ("border-radius:6px", "font-weight:600", "text-decoration:none"):
            self.assertIn(prop, dash_rule)
            self.assertIn(prop, desk_rule)

    def test_the_member_profile_view_also_offers_the_way_back(self):
        desk = DESK_TPL.read_text(encoding="utf-8-sig")
        self.assertGreaterEqual(desk.count('href="%s"' % _dashboard_artifact_name()), 2,
                                "a member profile must not be a dead end either")

    def test_back_links_are_real_anchors_with_a_visible_focus_ring(self):
        desk = DESK_TPL.read_text(encoding="utf-8-sig")
        self.assertIn('<a class="netlink deskhome"', desk)
        self.assertIn("a:focus-visible", desk)
        self.assertIn(".mlink:focus-visible", desk)

    def test_the_industry_page_is_not_a_one_way_trap(self):
        builder = source("make_lobby_network_html.py")
        header = builder.split("<header class=product-header>", 1)[1].split("</header>", 1)[0]
        self.assertIn('href="%s"' % _dashboard_artifact_name(), header)
        self.assertIn('href="cu-member-desk.html"', header)


class MemberProfileLinks(unittest.TestCase):
    """(B) A member's name is an address, everywhere it appears."""

    def test_the_desk_addresses_a_member_by_name_through_the_m_parameter(self):
        desk = DESK_TPL.read_text(encoding="utf-8-sig")
        self.assertIn("function memberHref(name){return '?m='+encodeURIComponent", desk)
        dash = DASH_TPL.read_text(encoding="utf-8-sig")
        self.assertIn("const DESK_FILE='cu-member-desk.html';", dash)
        self.assertIn("DESK_FILE+'?m='+encodeURIComponent(DATA.name[i])", dash)

    def test_every_clickable_member_list_on_the_dashboard_carries_the_address(self):
        """Each list that renders a member name must emit a profile address alongside it."""
        dash = DASH_TPL.read_text(encoding="utf-8-sig")
        emitting = [line for line in dash.splitlines()
                    if re.search(r"class=(lnk|tgt|oa|recruit)(?: tabindex=0)? data-i=", line)]
        self.assertGreaterEqual(len(emitting), 7, "member list renderers disappeared")
        for line in emitting:
            self.assertIn("mprof(", line,
                          "a member list renders a name with no profile address: %s" % line[:120])

    def test_the_dead_text_member_names_are_gone(self):
        dash = DASH_TPL.read_text(encoding="utf-8-sig").replace(" ", "")
        for dead in ("${k+1}.</span>${DATA.name[v]}", "map(p=>`${DATA.name[p.t]}"):
            self.assertNotIn(dead, dash, "a member name is dead text again: %s" % dead)
        self.assertGreater(dash.count("mlink("), 3)
        desk = DESK_TPL.read_text(encoding="utf-8-sig")
        self.assertGreaterEqual(desk.count("mlink("), 8,
                                "member names on the desk stopped being links")

    def test_a_row_click_never_swallows_the_profile_anchor(self):
        """Rows stay clickable for convenience, but the anchor inside owns its own click and
        a click made while selecting text is not hijacked."""
        for tpl, guard in ((DASH_TPL, "rowSelect"), (DESK_TPL, "rowOpen")):
            body = _extract_js_function(tpl.read_text(encoding="utf-8-sig"), guard)
            self.assertIn("closest('a')", body)
            self.assertIn("getSelection", body)
        dash = DASH_TPL.read_text(encoding="utf-8-sig")
        for selector in ("'.tgt'", "'.oa'"):
            self.assertNotIn(
                "querySelectorAll(%s).forEach(el=>el.onclick=()=>selectMember" % selector, dash)
        desk = DESK_TPL.read_text(encoding="utf-8-sig")
        for selector in ("'#rows tr'", "'#effrows tr'"):
            self.assertNotIn(
                "querySelectorAll(%s).forEach(tr=>tr.onclick=()=>openMember" % selector, desk)

    def test_the_combined_dashboard_keeps_member_links_in_page(self):
        """Moneyball.html embeds BOTH apps, so a member link there must switch views rather
        than re-fetch a 30MB document."""
        combined = source("make_combined.py")
        # 2026-08-04 (15560ea82): the #mdlink/a.mlink/a.mprof selector became an href router.
        self.assertIn("e.target.closest('a[href]')", combined)
        self.assertIn("h.indexOf('cu-member-desk.html')===0", combined)
        self.assertIn("window.gotoMember(m)", combined)
        self.assertIn("e.metaKey||e.ctrlKey", combined,
                      "ctrl/cmd-click must still be able to open a real tab")

    @unittest.skipIf(NODE is None, "node is not available on PATH")
    def test_dashboard_renders_a_member_name_as_an_anchor_carrying_its_address(self):
        dash = DASH_TPL.read_text(encoding="utf-8-sig")
        shipped = "\n".join(_extract_js_function(dash, name)
                            for name in ("mesc", "deskHref", "mlink", "mprof"))
        harness = HARNESS_MEMBER_LINK.replace("__SHIPPED__", shipped)
        out = json.loads(_run_node(harness, json.dumps(
            {"name": ["Jane Doe", "Rep. O&Co", "<script>x</script>"]})))
        self.assertIn('<a class=mlink href="cu-member-desk.html?m=Jane%20Doe"', out["named"])
        self.assertTrue(out["named"].rstrip().endswith("Jane Doe</a>"))
        self.assertIn("?m=Rep.%20O%26Co", out["spaced"])
        self.assertIn(">Doe</a>", out["lastname"])
        self.assertIn("class=mprof", out["badge"])
        self.assertIn('aria-label="Open Jane Doe in the member desk"', out["badge"])
        self.assertEqual("", out["missing"], "an unknown member must not become a dead link")
        self.assertEqual("", out["missingBadge"])
        self.assertNotIn("<script>", out["escaped"], "member names must be escaped")

    @unittest.skipIf(NODE is None, "node is not available on PATH")
    def test_the_desk_handles_an_inbound_deep_link(self):
        """A link that lands on the top of a 30MB page is not a fix: arriving with ?m= must
        resolve to the member."""
        desk = DESK_TPL.read_text(encoding="utf-8-sig")
        shipped = "\n".join(_extract_js_function(desk, name)
                            for name in ("resolveMemberAddress", "inboundMemberAddress"))
        harness = HARNESS_DEEP_LINK.replace("__SHIPPED__", shipped)
        cases = [
            {"search": "?m=Jane%20Doe"},          # the address the dashboard mints
            {"search": "?m=Jane%20Doe&x=1"},      # extra parameters must not confuse it
            {"search": "?m=%20jane%20doe%20"},    # hand-typed: case and padding
            {"hash": "#m=John%20Roe"},            # fragment form
            {"search": "?m=Nobody%20Here"},       # unknown member
            {"search": ""},                       # no address at all
        ]
        got = json.loads(_run_node(harness, json.dumps(cases)))
        self.assertEqual(4, got[0]["index"])
        self.assertEqual(4, got[1]["index"])
        self.assertEqual(4, got[2]["index"], "a case/whitespace variant must still land")
        self.assertEqual(9, got[3]["index"])
        self.assertIsNone(got[4]["index"], "an unknown member must not silently open someone else")
        self.assertIsNone(got[5]["address"])

    def test_arriving_with_an_address_opens_the_member_instead_of_the_ranked_list(self):
        init = DESK_TPL.read_text(encoding="utf-8-sig").split("---------- init ----------", 1)[1]
        self.assertIn("inboundMemberAddress()", init)
        self.assertIn("openMember(mi)", init)
        self.assertIn("popstate", init, "browser Back must still work from a member profile")


HARNESS_MEMBER_LINK = """
'use strict';
const DATA = JSON.parse(process.argv[2]);
const DESK_FILE = 'cu-member-desk.html';
__SHIPPED__
process.stdout.write(JSON.stringify({
  named: mlink(0), badge: mprof(0), spaced: mlink(1), lastname: mlink(0, 'Doe'),
  missing: mlink(-1), missingBadge: mprof(-1), escaped: mlink(2)
}));
"""

HARNESS_DEEP_LINK = """
'use strict';
const CASES = JSON.parse(process.argv[2]);
const byName = {'Jane Doe': 4, 'John Roe': 9};
let location = {search: '', hash: ''};
__SHIPPED__
const out = CASES.map(c => {
  location = {search: c.search || '', hash: c.hash || ''};
  const address = inboundMemberAddress();
  return {address: address, index: resolveMemberAddress(address)};
});
process.stdout.write(JSON.stringify(out));
"""


if __name__ == "__main__":
    unittest.main(verbosity=2)
